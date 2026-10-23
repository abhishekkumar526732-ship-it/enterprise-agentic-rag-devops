import re
import logging
from typing import Dict, List, Tuple
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

try:
    from presidio_analyzer import AnalyzerEngine
    from presidio_anonymizer import AnonymizerEngine
    from presidio_anonymizer.entities import OperatorConfig
    PRESIDIO_AVAILABLE = True
except ImportError:
    PRESIDIO_AVAILABLE = False
    logger.info("Presidio not installed; using optimized regex fallback for PII detection.")


class PresidioPIIEngine:
    """Enterprise PII Detection and Anonymization using Microsoft Presidio with regex fallback."""

    # Common HR-related PII fallback patterns
    EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
    PHONE_REGEX = re.compile(r"(\+?\d{1,3}[-.\s]?)?(\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}")
    SSN_REGEX = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
    CREDIT_CARD_REGEX = re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b")
    SALARY_REGEX = re.compile(r"(\$|₹|€|£|\bUSD|\bINR)\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?(?:\s?(?:k|lac|lakh|per annum|p\.a\.|monthly))?", re.IGNORECASE)

    def __init__(self):
        self.analyzer = None
        self.anonymizer = None
        if PRESIDIO_AVAILABLE:
            try:
                self.analyzer = AnalyzerEngine()
                self.anonymizer = AnonymizerEngine()
            except Exception as e:
                logger.warning(f"Could not initialize Presidio NLP engine ({e}). Using regex fallback.")

    def anonymize(self, text: str) -> Tuple[str, bool, Dict[str, str]]:
        """
        Anonymizes PII in text.
        Returns: (anonymized_text, pii_detected, pii_map)
        """
        if not settings.pii_masking_enabled or not text:
            return text, False, {}

        pii_map: Dict[str, str] = {}
        anonymized_text = text
        pii_found = False

        if self.analyzer and self.anonymizer:
            try:
                results = self.analyzer.analyze(
                    text=text,
                    entities=["PHONE_NUMBER", "EMAIL_ADDRESS", "PERSON", "US_SSN", "CREDIT_CARD", "IBAN_CODE"],
                    language="en",
                )
                if results:
                    pii_found = True
                    anonymized_result = self.anonymizer.anonymize(
                        text=text,
                        analyzer_results=results,
                    )
                    anonymized_text = anonymized_result.text
            except Exception as e:
                logger.warning(f"Presidio analysis encountered an error: {e}. Falling back to regex.")

        # Apply regex fallback if presidio didn't run or to catch salary / edge cases
        if not pii_found or settings.confidential_salary_masking:
            # Mask emails
            for match in self.EMAIL_REGEX.finditer(anonymized_text):
                pii_found = True
                placeholder = "<EMAIL_REDACTED>"
                pii_map[placeholder] = match.group()
                anonymized_text = anonymized_text.replace(match.group(), placeholder)

            # Mask phones
            for match in self.PHONE_REGEX.finditer(anonymized_text):
                val = match.group().strip()
                if len(re.sub(r"\D", "", val)) >= 10:
                    pii_found = True
                    placeholder = "<PHONE_REDACTED>"
                    pii_map[placeholder] = val
                    anonymized_text = anonymized_text.replace(val, placeholder)

            # Mask SSN
            for match in self.SSN_REGEX.finditer(anonymized_text):
                pii_found = True
                placeholder = "<GOVT_ID_REDACTED>"
                pii_map[placeholder] = match.group()
                anonymized_text = anonymized_text.replace(match.group(), placeholder)

            # Mask specific individual salary inquiries if enabled
            if settings.confidential_salary_masking:
                for match in self.SALARY_REGEX.finditer(anonymized_text):
                    val = match.group().strip()
                    if any(char.isdigit() for char in val):
                        pii_found = True
                        placeholder = "<CONFIDENTIAL_COMPENSATION>"
                        pii_map[placeholder] = val
                        anonymized_text = anonymized_text.replace(val, placeholder)

        return anonymized_text, pii_found, pii_map

    def restore(self, text: str, pii_map: Dict[str, str]) -> str:
        """Restores original PII placeholders if required."""
        restored = text
        for placeholder, original in pii_map.items():
            restored = restored.replace(placeholder, original)
        return restored


_pii_engine = None

def get_pii_engine() -> PresidioPIIEngine:
    global _pii_engine
    if _pii_engine is None:
        _pii_engine = PresidioPIIEngine()
    return _pii_engine
