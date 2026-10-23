import os
import uuid
import logging
from pathlib import Path
from typing import Literal, Optional
from langchain_openai import ChatOpenAI
from langchain_tavily import TavilySearch
from langgraph.graph import StateGraph, START, END

from app.core.config import get_settings
from app.core.gateway import get_gateway
from app.core.security import get_pii_engine
from app.core.guardrails import get_nemo_service
from app.services.memory import get_langmem_service
from app.rag.state import AgentState, RouteDecision, EvidenceGrade
from app.rag.vectorstore import get_retriever

logger = logging.getLogger(__name__)
settings = get_settings()

# Initialize LangSmith environment variables if configured
if settings.langchain_tracing_v2 or settings.langchain_api_key:
    os.environ["LANGCHAIN_TRACING_V2"] = "true" if settings.langchain_tracing_v2 else "false"
    if settings.langchain_api_key:
        os.environ["LANGCHAIN_API_KEY"] = settings.langchain_api_key
    os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project
    logger.info(f"LangSmith Tracing configured for project: {settings.langchain_project}")

_llm = None
_web_search = None


def llm():
    global _llm
    if _llm is None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is missing")
        _llm = ChatOpenAI(
            model=settings.openai_model,
            temperature=0,
            api_key=settings.openai_api_key,
        )
    return _llm


def web_search_tool():
    global _web_search
    if _web_search is None:
        if not settings.tavily_api_key:
            raise RuntimeError("TAVILY_API_KEY is missing")
        _web_search = TavilySearch(
            tavily_api_key=settings.tavily_api_key,
            max_results=5,
            topic="general",
            include_answer=True,
            include_raw_content=False,
        )
    return _web_search


def add_trace(state: AgentState, message: str):
    return [*state.get("trace", []), message]


# --- Enterprise Pillar 1 & 2: Security, Guardrails & PII Check Node ---

def guardrails_and_pii_check(state: AgentState):
    """Inspects query for prompt injection attacks and masks sensitive personal data."""
    trace = list(state.get("trace", []))
    q = state["question"]

    # 1. Run NeMo Guardrails Check
    guard = get_nemo_service().check_input(q)
    if not guard.passed:
        trace.append(f"NeMo Guardrails BLOCKED: {guard.reason}")
        return {
            "guardrail_status": "blocked",
            "answer": guard.reason or "This request violates enterprise safety policies.",
            "source_used": "security_guardrails",
            "trace": trace,
        }

    trace.append("NeMo Guardrails -> PASSED input safety inspection")

    # 2. Run Presidio PII Masking
    anonymized_q, pii_detected, pii_map = get_pii_engine().anonymize(q)
    if pii_detected:
        trace.append("Presidio PII -> Sensitive personal data masked for privacy")
        logger.info(f"PII masked for user '{state.get('user_id')}': {list(pii_map.keys())}")
    else:
        trace.append("Presidio PII -> No sensitive PII detected in query")

    # 3. Retrieve LangMem Long-Term Employee Memories
    user_id = state.get("user_id", "anonymous")
    memories = get_langmem_service().get_user_memories(user_id)
    if memories:
        trace.append(f"LangMem -> Loaded {len(memories)} personalized long-term employee facts")
    else:
        trace.append("LangMem -> No previous long-term profile facts for this employee")

    return {
        "guardrail_status": "passed",
        "current_query": anonymized_q,
        "pii_detected": pii_detected,
        "pii_map": pii_map,
        "user_memories": memories,
        "trace": trace,
    }


def route_after_guardrails(state: AgentState) -> Literal["route_question", "direct_exit"]:
    if state.get("guardrail_status") == "blocked":
        return "direct_exit"
    return "route_question"


# --- Standard RAG Graph Nodes with Gateway Cost & Observability ---

def route_question(state: AgentState):
    router = llm().with_structured_output(RouteDecision, method="json_mode")
    decision = router.invoke(f"""
You route messages for an enterprise HR policy and employee support assistant.
Use kb for questions about company HR policies, leave, holidays, benefits, payroll,
remote work, attendance, onboarding, performance, expenses, travel, conduct, or employee support.
Use direct only for greetings, thanks, or casual chat that needs no company knowledge.
Question: {state['current_query']}
Return valid JSON like {{"route":"kb"}}.
""")
    # Estimate router cost via Gateway
    cost = get_gateway().calculate_cost(settings.openai_model, prompt_tokens=150, completion_tokens=15)
    return {
        "source_used": decision.route,
        "cost_usd": state.get("cost_usd", 0.0) + cost,
        "tokens_used": state.get("tokens_used", 0) + 165,
        "trace": add_trace(state, f"Router → {decision.route.upper()}"),
    }


def route_after_router(state: AgentState) -> Literal["retrieve_kb", "direct_answer"]:
    return "retrieve_kb" if state["source_used"] == "kb" else "direct_answer"


def retrieve_kb(state: AgentState):
    docs = get_retriever().invoke(state["current_query"])
    return {"kb_docs": docs, "trace": add_trace(state, f"Private KB retrieval → {len(docs)} chunks")}


def grade_kb(state: AgentState):
    grader = llm().with_structured_output(EvidenceGrade, method="json_mode")
    context = "\n\n".join(f"Source: {d.metadata.get('source','unknown')}\n{d.page_content}" for d in state["kb_docs"])
    grade = grader.invoke(f"""
You grade evidence for an enterprise HR policy and employee support assistant.
Question: {state['current_query']}
Private company HR KB evidence:\n{context}
Return good only if the evidence is sufficient to answer confidently and specifically.
Otherwise return weak. JSON: {{"grade":"good"}} or {{"grade":"weak"}}.
""")
    cost = get_gateway().calculate_cost(settings.openai_model, prompt_tokens=600, completion_tokens=10)
    return {
        "kb_grade": grade.grade,
        "cost_usd": state.get("cost_usd", 0.0) + cost,
        "tokens_used": state.get("tokens_used", 0) + 610,
        "trace": add_trace(state, f"KB evidence grade → {grade.grade.upper()}"),
    }


def after_kb(state: AgentState) -> Literal["generate_from_kb", "search_web"]:
    return "generate_from_kb" if state["kb_grade"] == "good" else "search_web"


def search_web(state: AgentState):
    result = web_search_tool().invoke({"query": state["current_query"]})
    lines, citations = [], []
    if isinstance(result, dict):
        if result.get("answer"):
            lines.append("Search answer: " + result["answer"])
        for item in result.get("results", []):
            title, url, content = item.get("title", ""), item.get("url", ""), item.get("content", "")
            lines.append(f"Title: {title}\nURL: {url}\nContent: {content}")
            citations.append({"title": title or url, "url": url, "type": "web"})
    else:
        lines.append(str(result))
    return {
        "web_results": "\n\n".join(lines),
        "citations": citations,
        "source_used": "web",
        "trace": add_trace(state, "Web fallback → Tavily search"),
    }


def grade_web(state: AgentState):
    grader = llm().with_structured_output(EvidenceGrade, method="json_mode")
    grade = grader.invoke(f"""
Question: {state['current_query']}
Web evidence:\n{state['web_results']}
Return good if the evidence is sufficient and directly relevant; otherwise weak.
Return valid JSON like {{"grade":"good"}}.
""")
    cost = get_gateway().calculate_cost(settings.openai_model, prompt_tokens=700, completion_tokens=10)
    return {
        "web_grade": grade.grade,
        "cost_usd": state.get("cost_usd", 0.0) + cost,
        "tokens_used": state.get("tokens_used", 0) + 710,
        "trace": add_trace(state, f"Web evidence grade → {grade.grade.upper()}"),
    }


def after_web(state: AgentState) -> Literal["generate_from_web", "rewrite_query", "insufficient"]:
    if state["web_grade"] == "good":
        return "generate_from_web"
    if state["retry_count"] < settings.max_retries:
        return "rewrite_query"
    return "insufficient"


def rewrite_query(state: AgentState):
    rewritten = llm().invoke(f"""
Rewrite this HR/employee-support question for better private knowledge retrieval and public web search.
Preserve intent, add useful HR/policy keywords, do not answer, return only the query.
Question: {state['current_query']}
""").content.strip()
    cost = get_gateway().calculate_cost(settings.openai_model, prompt_tokens=120, completion_tokens=25)
    return {
        "current_query": rewritten,
        "retry_count": state["retry_count"] + 1,
        "cost_usd": state.get("cost_usd", 0.0) + cost,
        "tokens_used": state.get("tokens_used", 0) + 145,
        "trace": add_trace(state, f"Query rewrite → {rewritten}"),
    }


def generate_from_kb(state: AgentState):
    context = "\n\n".join(f"[Source: {d.metadata.get('source','unknown')}]\n{d.page_content}" for d in state["kb_docs"])
    mem_context = ""
    if state.get("user_memories"):
        mem_context = "\n\nPersonal Employee Context (LangMem):\n" + "\n".join(f"- {m}" for m in state["user_memories"])

    raw_answer = llm().invoke(f"""
You are an enterprise HR policy and employee support copilot. Answer ONLY from the private company HR KB below.
Be concise, practical, respectful, and policy-grounded. If steps are present, present them clearly.
If employee personal context is available, tailor the answer respectfully to their profile/location.
Do not invent policy details. Mention that the answer is based on the company's private knowledge base.
Question: {state['current_query']}\n\nPrivate KB:\n{context}{mem_context}
""").content

    # Output Guardrail validation via NeMo Guardrails
    guard = get_nemo_service().check_output(raw_answer)
    final_answer = guard.sanitized_text

    citations = []
    seen = set()
    for d in state["kb_docs"]:
        src = d.metadata.get("source", "Private KB")
        if src not in seen:
            seen.add(src)
            # Use Path(src).name to fix Windows path backslash issues
            filename = Path(src).name
            citations.append({"title": filename, "url": "", "type": "private_kb"})

    cost = get_gateway().calculate_cost(settings.litellm_model, prompt_tokens=850, completion_tokens=200)
    return {
        "answer": final_answer,
        "source_used": "private_kb",
        "citations": citations,
        "cost_usd": state.get("cost_usd", 0.0) + cost,
        "tokens_used": state.get("tokens_used", 0) + 1050,
        "trace": add_trace(state, f"Answer generation → PRIVATE KB via {settings.litellm_model}"),
    }


def generate_from_web(state: AgentState):
    raw_answer = llm().invoke(f"""
You are an enterprise HR policy and employee support copilot. The private company HR KB was insufficient.
Answer ONLY from the web evidence below. Clearly say this is external public information and may require HR validation before being treated as company policy or employment guidance.
Question: {state['current_query']}\n\nWeb evidence:\n{state['web_results']}
""").content

    guard = get_nemo_service().check_output(raw_answer)
    final_answer = guard.sanitized_text

    cost = get_gateway().calculate_cost(settings.litellm_model, prompt_tokens=900, completion_tokens=250)
    return {
        "answer": final_answer,
        "source_used": "web_search",
        "cost_usd": state.get("cost_usd", 0.0) + cost,
        "tokens_used": state.get("tokens_used", 0) + 1150,
        "trace": add_trace(state, f"Answer generation → WEB SEARCH via {settings.litellm_model}"),
    }


def direct_answer(state: AgentState):
    raw_answer = llm().invoke(f"Respond briefly and naturally to: {state['current_query']}").content
    guard = get_nemo_service().check_output(raw_answer)
    final_answer = guard.sanitized_text

    cost = get_gateway().calculate_cost(settings.openai_model, prompt_tokens=50, completion_tokens=40)
    return {
        "answer": final_answer,
        "source_used": "direct",
        "cost_usd": state.get("cost_usd", 0.0) + cost,
        "tokens_used": state.get("tokens_used", 0) + 90,
        "trace": add_trace(state, "Direct response → no retrieval"),
    }


def insufficient(state: AgentState):
    return {
        "answer": "I couldn't find enough reliable evidence in the company HR knowledge base or external search to answer confidently. Please contact the HR team or provide more details.",
        "source_used": "insufficient_evidence",
        "trace": add_trace(state, "Stopped → insufficient reliable evidence"),
    }


def build_graph():
    graph = StateGraph(AgentState)
    nodes = {
        "guardrails_and_pii_check": guardrails_and_pii_check,
        "route_question": route_question,
        "retrieve_kb": retrieve_kb,
        "grade_kb": grade_kb,
        "search_web": search_web,
        "grade_web": grade_web,
        "rewrite_query": rewrite_query,
        "generate_from_kb": generate_from_kb,
        "generate_from_web": generate_from_web,
        "direct_answer": direct_answer,
        "insufficient": insufficient,
    }
    for name, fn in nodes.items():
        graph.add_node(name, fn)

    # Graph edge routing with Guardrails
    graph.add_edge(START, "guardrails_and_pii_check")
    graph.add_conditional_edges("guardrails_and_pii_check", route_after_guardrails, {
        "direct_exit": END,
        "route_question": "route_question",
    })
    graph.add_conditional_edges("route_question", route_after_router, {
        "retrieve_kb": "retrieve_kb",
        "direct_answer": "direct_answer",
    })
    graph.add_edge("retrieve_kb", "grade_kb")
    graph.add_conditional_edges("grade_kb", after_kb, {
        "generate_from_kb": "generate_from_kb",
        "search_web": "search_web",
    })
    graph.add_edge("search_web", "grade_web")
    graph.add_conditional_edges("grade_web", after_web, {
        "generate_from_web": "generate_from_web",
        "rewrite_query": "rewrite_query",
        "insufficient": "insufficient",
    })
    graph.add_edge("rewrite_query", "retrieve_kb")
    graph.add_edge("generate_from_kb", END)
    graph.add_edge("generate_from_web", END)
    graph.add_edge("direct_answer", END)
    graph.add_edge("insufficient", END)
    return graph.compile()


agent_graph = build_graph()


def ask(question: str, user_id: str = "anonymous", user_role: str = "employee", model: Optional[str] = None):
    run_id = str(uuid.uuid4())
    active_model = model or settings.litellm_model
    initial: AgentState = {
        "question": question,
        "current_query": question,
        "kb_docs": [],
        "web_results": "",
        "kb_grade": "",
        "web_grade": "",
        "answer": "",
        "source_used": "",
        "retry_count": 0,
        "trace": [],
        "citations": [],
        "user_id": user_id,
        "user_role": user_role,
        "cost_usd": 0.0,
        "tokens_used": 0,
        "pii_detected": False,
        "pii_map": {},
        "guardrail_status": "pending",
        "langsmith_run_id": run_id,
        "model_used": active_model,
        "user_memories": [],
    }

    # Pass LangSmith tags and metadata
    call_config = {
        "run_name": "Enterprise_HR_Agentic_RAG",
        "metadata": {
            "user_id": user_id,
            "user_role": user_role,
            "project": settings.langchain_project,
            "run_id": run_id,
        },
        "tags": [user_role, settings.app_env],
    }

    result = agent_graph.invoke(initial, config=call_config)
    result["langsmith_run_id"] = run_id

    # Persist newly discovered employee facts into LangMem
    if user_id and user_id != "anonymous" and result.get("source_used") in {"private_kb", "web_search", "direct"}:
        try:
            get_langmem_service().extract_and_persist_facts(user_id, question, result.get("answer", ""))
        except Exception:
            pass

    return result
