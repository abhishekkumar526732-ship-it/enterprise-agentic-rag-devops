import logging
from pathlib import Path
from typing import Optional
from app.core.config import get_settings
from app.core.security.guardrails import get_guardrails, GuardrailResult

logger = logging.getLogger(__name__)
settings = get_settings()

GUARDRAILS_DIR = Path(__file__).parent


class NeMoGuardrailsService:
    """Enterprise NeMo Guardrails Service enforcing input and output safety rails."""

    def __init__(self):
        self.rails = None
        if settings.nemoguardrails_enabled:
            try:
                from nemoguardrails import RailsConfig, LLMRails
                config = RailsConfig.from_path(str(GUARDRAILS_DIR))
                self.rails = LLMRails(config)
                logger.info("NeMo Guardrails Colang engine loaded successfully.")
            except ImportError:
                logger.info("NeMo Guardrails library not installed; running optimized built-in Colang rail engine.")
            except Exception as e:
                logger.warning(f"Could not initialize NeMo Guardrails config: {e}. Using native rail runner.")

    def check_input(self, text: str) -> GuardrailResult:
        """Runs input guardrails for prompt injection and topic violations."""
        if not settings.guardrails_enabled:
            return GuardrailResult(passed=True, flagged=False, sanitized_text=text)

        # 1. NeMo Guardrails async/sync validation if active
        if self.rails:
            try:
                # Synchronous invocation check
                response = self.rails.generate(messages=[{"role": "user", "content": text}])
                content = response.get("content", "")
                if "Security Violation" in content or "Policy Violation" in content:
                    return GuardrailResult(
                        passed=False,
                        flagged=True,
                        reason=content,
                        sanitized_text="",
                    )
            except Exception as e:
                logger.warning(f"NeMo engine error: {e}. Falling back to native rail verification.")

        # 2. Native high-performance Colang rail validator
        return get_guardrails().validate_input(text)

    def check_output(self, text: str) -> GuardrailResult:
        """Runs output rails to prevent credential and confidentiality leakage."""
        if not settings.guardrails_enabled:
            return GuardrailResult(passed=True, flagged=False, sanitized_text=text)
        return get_guardrails().validate_output(text)


_nemo_service: Optional[NeMoGuardrailsService] = None

def get_nemo_service() -> NeMoGuardrailsService:
    global _nemo_service
    if _nemo_service is None:
        _nemo_service = NeMoGuardrailsService()
    return _nemo_service
