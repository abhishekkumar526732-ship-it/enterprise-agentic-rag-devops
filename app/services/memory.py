import json
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.gateway import get_gateway

logger = logging.getLogger(__name__)
settings = get_settings()

try:
    import langmem
    LANGMEM_AVAILABLE = True
except ImportError:
    LANGMEM_AVAILABLE = False


class ExtractedMemory(BaseModel):
    facts: List[str] = Field(
        default_factory=list,
        description="Key factual attributes about the employee (e.g. branch office, role, joining date, previous leave status)",
    )


class LangMemService:
    """Enterprise Long-term Memory Service using LangMem and persistent SQLite/Redis storage."""

    def __init__(self):
        self.db_path = settings.audit_db_path
        self._init_memory_table()

    def _init_memory_table(self) -> None:
        try:
            con = sqlite3.connect(self.db_path)
            con.execute(
                """CREATE TABLE IF NOT EXISTS employee_longterm_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    memory_text TEXT NOT NULL,
                    category TEXT DEFAULT 'profile',
                    created_at TEXT NOT NULL
                )"""
            )
            con.commit()
            con.close()
        except Exception as e:
            logger.error(f"Failed to initialize memory table: {e}")

    def get_user_memories(self, user_id: str, limit: int = 5) -> List[str]:
        """Fetches persistent long-term memories and facts for a specific employee."""
        if not user_id or user_id == "anonymous":
            return []

        try:
            con = sqlite3.connect(self.db_path)
            cursor = con.cursor()
            cursor.execute(
                "SELECT memory_text FROM employee_longterm_memory WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            )
            rows = cursor.fetchall()
            con.close()
            return [r[0] for r in rows]
        except Exception as e:
            logger.warning(f"Could not load memories for user '{user_id}': {e}")
            return []

    def save_memory(self, user_id: str, memory_text: str, category: str = "profile") -> None:
        """Stores a new verified fact in long-term memory."""
        if not user_id or not memory_text or user_id == "anonymous":
            return

        text = memory_text.strip()
        try:
            con = sqlite3.connect(self.db_path)
            cursor = con.cursor()
            # Avoid duplicate memories
            cursor.execute(
                "SELECT id FROM employee_longterm_memory WHERE user_id = ? AND memory_text = ?",
                (user_id, text),
            )
            if not cursor.fetchone():
                con.execute(
                    "INSERT INTO employee_longterm_memory (user_id, memory_text, category, created_at) VALUES (?, ?, ?, ?)",
                    (user_id, text, category, datetime.now(timezone.utc).isoformat()),
                )
                con.commit()
                logger.info(f"LangMem stored new fact for '{user_id}': {text}")
            con.close()
        except Exception as e:
            logger.warning(f"Could not save memory for '{user_id}': {e}")

    def extract_and_persist_facts(self, user_id: str, question: str, answer: str) -> List[str]:
        """
        Uses LangMem/Gateway extractor to parse persistent facts about the employee
        from the interaction (e.g. location, employment tier, department, planned leaves).
        """
        if not user_id or user_id == "anonymous":
            return []

        prompt = f"""You are a LangMem enterprise long-term memory manager.
Extract any durable personal facts or attributes about the employee from this interaction.
Only extract clear personal facts (e.g., location, branch office, team, employment type, or past HR case context).
Do not extract general company policy facts.
If no personal facts are mentioned, return an empty list.

Employee Query: {question}
Assistant Answer: {answer}

Return valid JSON like:
{{"facts": ["Employee is based in London office", "Employee works on Engineering team"]}}
"""
        try:
            gateway = get_gateway()
            resp = gateway.complete(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.get("content", "{}"))
            facts = data.get("facts", [])
            for fact in facts:
                self.save_memory(user_id, fact, category="extracted_profile")
            return facts
        except Exception as e:
            logger.warning(f"LangMem fact extraction skipped: {e}")
            return []

    def clear_user_memories(self, user_id: str) -> None:
        """GDPR Right-to-be-forgotten compliance handler."""
        try:
            con = sqlite3.connect(self.db_path)
            con.execute("DELETE FROM employee_longterm_memory WHERE user_id = ?", (user_id,))
            con.commit()
            con.close()
            logger.info(f"Cleared all LangMem records for user: {user_id}")
        except Exception as e:
            logger.error(f"Error clearing memories: {e}")


_langmem_service: Optional[LangMemService] = None

def get_langmem_service() -> LangMemService:
    global _langmem_service
    if _langmem_service is None:
        _langmem_service = LangMemService()
    return _langmem_service
