import logging
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Header
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.auth import User, get_current_user, require_role
from app.rag.workflow import ask
from app.rag.vectorstore import add_documents
from app.services.ingestion import load_file, chunk_documents, SUPPORTED
from app.services.audit import write_audit, get_audit_logs
from app.services.cache import get_cache_service
from app.services.memory import get_langmem_service

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api")


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=3000)
    session_id: Optional[str] = Field(default=None, description="Optional conversational session ID")
    model: Optional[str] = Field(default=None, description="Model selected from LiteLLM Gateway (Gemini Flash, Groq, or OpenAI)")


class FeedbackRequest(BaseModel):
    run_id: str
    score: int = Field(ge=0, le=1, description="1 for thumbs up, 0 for thumbs down")
    comment: Optional[str] = None


@router.get("/health")
def health():
    cache = get_cache_service()
    return {
        "status": "ok",
        "service": settings.app_name,
        "features": {
            "keycloak_enabled": settings.keycloak_enabled,
            "pii_masking": settings.pii_masking_enabled,
            "guardrails": settings.guardrails_enabled,
            "langsmith_tracing": settings.langchain_tracing_v2,
            "model_gateway": settings.litellm_model,
            "redis_connected": cache.client is not None,
            "redis_enabled": settings.redis_enabled,
        }
    }


@router.post("/chat")
def chat(payload: ChatRequest, current_user: User = Depends(get_current_user)):
    cache = get_cache_service()

    # 1. Distributed Rate Limiting via Redis
    if not cache.check_rate_limit(current_user.id):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Please wait a moment before sending another query."
        )

    user_role = "hr_admin" if current_user.has_role("hr_admin") else "employee"

    # 2. Redis Response Cache Lookup
    cached_data = cache.get_response(payload.question, user_role)
    if cached_data:
        cached_response = dict(cached_data)
        cached_response["cached"] = True
        cached_response["cost_usd"] = 0.0
        cached_response["trace"] = [
            "⚡ Redis Cache HIT → Instant response (<5ms) with zero LLM API cost"
        ] + cached_response.get("trace", [])
        return cached_response

    # 3. Agentic RAG Execution
    try:
        result = ask(
            question=payload.question,
            user_id=current_user.id,
            user_role=user_role,
            model=payload.model,
        )

        cost = result.get("cost_usd", 0.0)
        tokens = result.get("tokens_used", 0)
        pii = result.get("pii_detected", False)

        response_payload = {
            "answer": result["answer"],
            "source_used": result["source_used"],
            "model_used": result.get("model_used", payload.model or settings.litellm_model),
            "trace": result.get("trace", []),
            "citations": result.get("citations", []),
            "rewritten_query": result.get("current_query", payload.question),
            "cost_usd": cost,
            "tokens_used": tokens,
            "pii_detected": pii,
            "cached": False,
            "langsmith_run_id": result.get("langsmith_run_id", ""),
            "user_memories": result.get("user_memories", []),
            "user": {
                "id": current_user.id,
                "role": user_role,
            }
        }

        # 4. Cache response in Redis for future requests (if successful policy/web answer)
        if result["source_used"] in {"private_kb", "web_search", "direct"}:
            cache.set_response(payload.question, user_role, response_payload)

        # 5. Record Multi-turn Session Memory
        if payload.session_id:
            cache.add_session_message(payload.session_id, "user", payload.question)
            cache.add_session_message(payload.session_id, "assistant", result["answer"])

        # 6. Audit Trail Logging
        write_audit(
            question=payload.question,
            source_used=result["source_used"],
            trace=result.get("trace", []),
            user_id=current_user.id,
            user_role=user_role,
            cost_usd=cost,
            tokens_used=tokens,
            pii_detected=pii,
        )

        return response_payload

    except Exception as exc:
        logger.error(f"Error handling chat request: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/ingest")
async def ingest(
    file: UploadFile = File(...),
    current_user: User = Depends(require_role("hr_admin")),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED:
        raise HTTPException(status_code=400, detail=f"Supported file types: {', '.join(sorted(SUPPORTED))}")
    
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / Path(file.filename).name
    dest.write_bytes(await file.read())
    
    docs = load_file(dest)
    chunks = chunk_documents(docs)
    ids = add_documents(chunks)
    
    # Invalidate stale cache so new policy updates take effect immediately
    get_cache_service().clear()
    logger.info(f"Invalidated Redis cache after ingestion of {file.filename}")

    return {
        "message": "Document indexed successfully",
        "file": dest.name,
        "chunks": len(chunks),
        "ids_created": len(ids),
        "indexed_by": current_user.id,
    }


@router.get("/audit")
def audit_view(limit: int = 50, current_user: User = Depends(require_role("hr_admin"))):
    """Retrieve audit logs (restricted to HR Admins for governance)."""
    logs = get_audit_logs(limit=limit)
    return {"total": len(logs), "logs": logs}


@router.post("/feedback")
def submit_feedback(payload: FeedbackRequest, current_user: User = Depends(get_current_user)):
    """Logs user feedback (thumbs up/down) to LangSmith when configured."""
    if settings.langchain_api_key:
        try:
            from langsmith import Client
            client = Client(api_key=settings.langchain_api_key)
            client.create_feedback(
                run_id=payload.run_id,
                key="user_score",
                score=payload.score,
                comment=payload.comment or "",
            )
            return {"status": "success", "message": "Feedback submitted to LangSmith"}
        except Exception as e:
            logger.warning(f"Could not submit feedback to LangSmith: {e}")

    return {"status": "recorded", "message": "Feedback noted locally."}


@router.get("/memories")
def get_user_memories(current_user: User = Depends(get_current_user)):
    """Retrieves all long-term personal facts stored by LangMem for the authenticated employee."""
    memories = get_langmem_service().get_user_memories(current_user.id, limit=20)
    return {
        "user_id": current_user.id,
        "count": len(memories),
        "memories": memories,
    }


@router.delete("/memories")
def clear_user_memories(current_user: User = Depends(get_current_user)):
    """GDPR Right-to-be-Forgotten: Purges all persistent LangMem records for the user."""
    get_langmem_service().clear_user_memories(current_user.id)
    return {
        "status": "success",
        "message": f"All long-term profile memories for employee '{current_user.id}' have been purged.",
    }
