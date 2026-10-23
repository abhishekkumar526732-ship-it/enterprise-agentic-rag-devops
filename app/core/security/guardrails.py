import re
import logging
from typing import Optional, Tuple
from pydantic import BaseModel, Field
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class GuardrailResult(BaseModel):
    passed: bool = Field(description="Whether the text passed the safety guardrails")
    flagged: bool = Field(description="True if an unsafe pattern was detected")
    reason: Optional[str] = Field(default=None, description="Reason if blocked")
    sanitized_text: str = Field(description="Sanitized text safe for execution")


class SafetyGuardrails:
    """Enterprise AI Guardrails protecting against prompt injection, jailbreaks, and out-of-scope attacks."""

    # Prompt injection and jailbreak signatures
    INJECTION_PATTERNS = [
        re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts|rules)", re.IGNORECASE),
        re.compile(r"(system\s+prompt|system\s+instructions|hidden\s+prompt)\s+(leak|reveal|print|show)", re.IGNORECASE),
        re.compile(r"\b(dan\s+mode|jailbreak|bypass\s+filters|developer\s+mode)\b", re.IGNORECASE),
        re.compile(r"you\s+are\s+now\s+an\s+unrestricted\s+ai", re.IGNORECASE),
        re.compile(r"disregard\s+all\s+(safety|ethical)\s+guidelines", re.IGNORECASE),
        re.compile(r"base64\s+decode.*exec", re.IGNORECASE),
    ]

    # Prohibited non-enterprise malicious topics
    HARMFUL_PATTERNS = [
        re.compile(r"\b(create|write|generate)\s+(a\s+)?(virus|malware|keylogger|trojan|ransomware|exploit)\b", re.IGNORECASE),
        re.compile(r"\b(how\s+to\s+hack|ddos\s+attack|sql\s+injection\s+payload)\b", re.IGNORECASE),
    ]

    def validate_input(self, question: str) -> GuardrailResult:
        """Evaluates input query against prompt injection and toxic/harmful patterns."""
        if not settings.guardrails_enabled or not question:
            return GuardrailResult(passed=True, flagged=False, sanitized_text=question)

        text = question.strip()

        # 1. Check prompt injection
        for pattern in self.INJECTION_PATTERNS:
            if pattern.search(text):
                logger.warning(f"Guardrail tripped: Prompt Injection detected in query '{text[:60]}...'")
                return GuardrailResult(
                    passed=False,
                    flagged=True,
                    reason="Security Violation: Prompt injection or system instruction manipulation detected.",
                    sanitized_text="",
                )

        # 2. Check harmful content
        for pattern in self.HARMFUL_PATTERNS:
            if pattern.search(text):
                logger.warning(f"Guardrail tripped: Prohibited content in query '{text[:60]}...'")
                return GuardrailResult(
                    passed=False,
                    flagged=True,
                    reason="Policy Violation: This assistant only supports legitimate enterprise HR policy and employee support inquiries.",
                    sanitized_text="",
                )

        return GuardrailResult(passed=True, flagged=False, sanitized_text=text)

    def validate_output(self, answer: str) -> GuardrailResult:
        """Evaluates output to ensure internal system prompt or secrets are not leaked."""
        if not settings.guardrails_enabled or not answer:
            return GuardrailResult(passed=True, flagged=False, sanitized_text=answer)

        # Check if the output mistakenly leaks API keys or system tokens
        if "sk-" in answer or "API_KEY" in answer:
            logger.error("Guardrail tripped: Output attempted to expose API credentials!")
            return GuardrailResult(
                passed=False,
                flagged=True,
                reason="Security Violation: Blocked attempted credential leak.",
                sanitized_text="The response was blocked because it contained sensitive system credentials.",
            )

        return GuardrailResult(passed=True, flagged=False, sanitized_text=answer)


_guardrails = None

def get_guardrails() -> SafetyGuardrails:
    global _guardrails
    if _guardrails is None:
        _guardrails = SafetyGuardrails()
    return _guardrails
