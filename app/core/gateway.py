import logging
from typing import Any, Dict, List, Optional
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

try:
    import litellm
    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False
    logger.warning("litellm is not installed; falling back to direct provider completion.")

# Detailed multi-provider pricing estimates (per 1M tokens)
PRICING_ESTIMATES = {
    # Google Gemini Models
    "gemini/gemini-2.5-flash": {"prompt": 0.075 / 1_000_000, "completion": 0.30 / 1_000_000},
    "gemini/gemini-3.8-flash": {"prompt": 0.075 / 1_000_000, "completion": 0.30 / 1_000_000},
    "gemini/gemini-1.5-flash": {"prompt": 0.075 / 1_000_000, "completion": 0.30 / 1_000_000},
    # Groq Models (Ultra-low latency LPU)
    "groq/llama-3.3-70b-versatile": {"prompt": 0.59 / 1_000_000, "completion": 0.79 / 1_000_000},
    "groq/llama-3.1-70b-versatile": {"prompt": 0.59 / 1_000_000, "completion": 0.79 / 1_000_000},
    "groq/gpt-oss-120b": {"prompt": 0.80 / 1_000_000, "completion": 1.20 / 1_000_000},
    # OpenAI Models
    "gpt-4o-mini": {"prompt": 0.15 / 1_000_000, "completion": 0.60 / 1_000_000},
    "gpt-3.5-turbo": {"prompt": 0.50 / 1_000_000, "completion": 1.50 / 1_000_000},
}


class ModelGateway:
    """Enterprise LiteLLM Gateway routing requests across Gemini Flash, Groq, and OpenAI with cost tracking."""

    def __init__(self):
        self.primary_model = settings.litellm_model or "gemini/gemini-2.5-flash"
        self.fallback_models = settings.litellm_fallback_models or ["groq/llama-3.3-70b-versatile", "gpt-4o-mini"]

    def _get_api_key_for_model(self, model_name: str) -> str:
        """Dynamically routes the correct API key depending on the model provider."""
        if model_name.startswith("gemini"):
            return settings.gemini_api_key or settings.openai_api_key
        elif model_name.startswith("groq"):
            return settings.groq_api_key or settings.openai_api_key
        return settings.openai_api_key

    def calculate_cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        if LITELLM_AVAILABLE:
            try:
                cost = litellm.completion_cost(
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens
                )
                if cost is not None:
                    return float(cost)
            except Exception:
                pass

        # Fallback table pricing
        pricing = PRICING_ESTIMATES.get(model, PRICING_ESTIMATES["gpt-4o-mini"])
        cost = (prompt_tokens * pricing["prompt"]) + (completion_tokens * pricing["completion"])
        return round(cost, 6)

    def complete(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
        response_format: Optional[Dict[str, Any]] = None,
        max_tokens: Optional[int] = None,
        model_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute completion across Gemini Flash, Groq, or OpenAI with automatic fallback and token accounting.
        """
        primary = model_override or self.primary_model
        models_to_try = [primary] + [m for m in self.fallback_models if m != primary]
        last_error = None

        for model_name in models_to_try:
            api_key = self._get_api_key_for_model(model_name)
            try:
                if LITELLM_AVAILABLE:
                    kwargs: Dict[str, Any] = {
                        "model": model_name,
                        "messages": messages,
                        "temperature": temperature,
                        "api_key": api_key,
                    }
                    if response_format:
                        kwargs["response_format"] = response_format
                    if max_tokens:
                        kwargs["max_tokens"] = max_tokens

                    response = litellm.completion(**kwargs)
                    content = response.choices[0].message.content or ""
                    usage = getattr(response, "usage", None)
                    p_tok = usage.prompt_tokens if usage else 0
                    c_tok = usage.completion_tokens if usage else 0
                    t_tok = usage.total_tokens if usage else (p_tok + c_tok)

                    cost = self.calculate_cost(model_name, p_tok, c_tok)
                    logger.info(f"Gateway routed to {model_name} | Tokens: {t_tok} | Cost: ${cost:.6f}")

                    return {
                        "content": content,
                        "model_used": model_name,
                        "cost_usd": cost,
                        "prompt_tokens": p_tok,
                        "completion_tokens": c_tok,
                        "total_tokens": t_tok,
                    }
                else:
                    # Fallback direct OpenAI client if litellm not installed
                    from openai import OpenAI
                    client = OpenAI(api_key=settings.openai_api_key)
                    resp = client.chat.completions.create(
                        model=settings.openai_model,
                        messages=messages,
                        temperature=temperature,
                        response_format=response_format,
                    )
                    content = resp.choices[0].message.content or ""
                    p_tok = resp.usage.prompt_tokens if resp.usage else 0
                    c_tok = resp.usage.completion_tokens if resp.usage else 0
                    cost = self.calculate_cost(settings.openai_model, p_tok, c_tok)

                    return {
                        "content": content,
                        "model_used": settings.openai_model,
                        "cost_usd": cost,
                        "prompt_tokens": p_tok,
                        "completion_tokens": c_tok,
                        "total_tokens": p_tok + c_tok,
                    }

            except Exception as e:
                logger.warning(f"Gateway model '{model_name}' failed: {e}. Attempting fallback...")
                last_error = e

        raise RuntimeError(f"All Model Gateway candidates failed. Last error: {last_error}")


_gateway: Optional[ModelGateway] = None

def get_gateway() -> ModelGateway:
    global _gateway
    if _gateway is None:
        _gateway = ModelGateway()
    return _gateway
