import json
import sqlite3
from datetime import datetime, timezone
from app.core.config import get_settings


settings = get_settings()


def init_db() -> None:
    con = sqlite3.connect(settings.audit_db_path)
    con.execute(
        """CREATE TABLE IF NOT EXISTS query_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            question TEXT NOT NULL,
            source_used TEXT NOT NULL,
            trace_json TEXT NOT NULL,
            user_id TEXT DEFAULT 'anonymous',
            user_role TEXT DEFAULT 'employee',
            cost_usd REAL DEFAULT 0.0,
            tokens_used INTEGER DEFAULT 0,
            pii_detected INTEGER DEFAULT 0
        )"""
    )
    # Automatically add columns if migrating from an older DB
    cursor = con.cursor()
    cursor.execute("PRAGMA table_info(query_audit)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    for col, ctype in [
        ("user_id", "TEXT DEFAULT 'anonymous'"),
        ("user_role", "TEXT DEFAULT 'employee'"),
        ("cost_usd", "REAL DEFAULT 0.0"),
        ("tokens_used", "INTEGER DEFAULT 0"),
        ("pii_detected", "INTEGER DEFAULT 0"),
    ]:
        if col not in existing_cols:
            con.execute(f"ALTER TABLE query_audit ADD COLUMN {col} {ctype}")
    con.commit()
    con.close()


def write_audit(
    question: str,
    source_used: str,
    trace: list[str],
    user_id: str = "anonymous",
    user_role: str = "employee",
    cost_usd: float = 0.0,
    tokens_used: int = 0,
    pii_detected: bool = False,
) -> None:
    con = sqlite3.connect(settings.audit_db_path)
    con.execute(
        """INSERT INTO query_audit (
            created_at, question, source_used, trace_json,
            user_id, user_role, cost_usd, tokens_used, pii_detected
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.now(timezone.utc).isoformat(),
            question,
            source_used,
            json.dumps(trace),
            user_id,
            user_role,
            round(cost_usd, 6),
            tokens_used,
            1 if pii_detected else 0,
        ),
    )
    con.commit()
    con.close()


def get_audit_logs(limit: int = 50) -> list[dict]:
    con = sqlite3.connect(settings.audit_db_path)
    con.row_factory = sqlite3.Row
    cursor = con.cursor()
    cursor.execute(
        "SELECT id, created_at, question, source_used, user_id, user_role, cost_usd, tokens_used, pii_detected, trace_json "
        "FROM query_audit ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows = cursor.fetchall()
    results = []
    for r in rows:
        results.append({
            "id": r["id"],
            "created_at": r["created_at"],
            "question": r["question"],
            "source_used": r["source_used"],
            "user_id": r["user_id"],
            "user_role": r["user_role"],
            "cost_usd": r["cost_usd"],
            "tokens_used": r["tokens_used"],
            "pii_detected": bool(r["pii_detected"]),
            "trace": json.loads(r["trace_json"]) if r["trace_json"] else [],
        })
    con.close()
    return results