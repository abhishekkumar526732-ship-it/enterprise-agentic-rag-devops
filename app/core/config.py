from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "Enterprise HR Policy Agentic RAG Copilot"
    app_env: str = "development"
    # Multi-Provider API Keys
    openai_api_key: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""
    tavily_api_key: str = ""
    pinecone_api_key: str = ""
    pinecone_index_name: str = "fde-hr-policy-rag"
    pinecone_namespace: str = "company-hr-kb"
    embedding_model: str = "text-embedding-3-small"
    openai_model: str = "gpt-4o-mini"
    top_k: int = 4
    max_retries: int = 1
    admin_api_key: str = "change-me-in-production"
    audit_db_path: str = str(BASE_DIR / "data" / "audit.db")
    upload_dir: str = str(BASE_DIR / "uploads")
    sample_kb_dir: str = str(BASE_DIR / "data" / "sample_kb")

    # Enterprise Pillar 1: Model Gateway (LiteLLM with Gemini Flash & Groq)
    litellm_model: str = "gemini/gemini-2.5-flash"
    litellm_fallback_models: list[str] = ["groq/llama-3.3-70b-versatile", "gpt-4o-mini"]
    litellm_max_budget_usd: float = 50.0
    litellm_cache_enabled: bool = True

    # Enterprise Pillar 2: AI Security & Guardrails (NeMo Guardrails & Presidio PII)
    pii_masking_enabled: bool = True
    guardrails_enabled: bool = True
    nemoguardrails_enabled: bool = True
    confidential_salary_masking: bool = True

    # Enterprise Pillar 3: AI Observability & Tracing (LangSmith)
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "enterprise-hr-policy-copilot"

    # Enterprise Pillar 4: Enterprise Identity & Access Control (Keycloak)
    keycloak_enabled: bool = False
    keycloak_server_url: str = "http://localhost:8080"
    keycloak_realm: str = "enterprise-hr"
    keycloak_client_id: str = "hr-copilot-client"
    keycloak_client_secret: str = ""

    # Enterprise Redis (Response Cache, Session Memory & Rate Limiting)
    redis_enabled: bool = True
    redis_url: str = "redis://localhost:6379/0"
    redis_cache_ttl_seconds: int = 3600
    redis_rate_limit_per_minute: int = 30

    model_config = SettingsConfigDict(env_file=str(BASE_DIR / ".env"), extra="ignore")




@lru_cache
def get_settings() -> Settings:
    return Settings()