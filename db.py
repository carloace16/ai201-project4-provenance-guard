import sqlite3
import json
from datetime import datetime, timezone
from config import DB_PATH


def init_db():
    """Create the audit table if it doesn't exist."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit (
            entry_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp    TEXT NOT NULL,
            content_id   TEXT NOT NULL,
            creator_id   TEXT,
            event        TEXT NOT NULL,
            text         TEXT,
            attribution  TEXT,
            confidence   REAL,
            llm_score    REAL,
            stylo_score  REAL,
            label        TEXT,
            status       TEXT,
            appeal_reasoning TEXT
        )
    """)
    conn.commit()
    conn.close()


def write_entry(entry: dict) -> int:
    """Insert an audit entry. Returns the new entry_id."""
    entry = {**entry, "timestamp": datetime.now(timezone.utc).isoformat()}
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("""
        INSERT INTO audit
        (timestamp, content_id, creator_id, event, text,
         attribution, confidence, llm_score, stylo_score,
         label, status, appeal_reasoning)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        entry["timestamp"],
        entry.get("content_id"),
        entry.get("creator_id"),
        entry.get("event"),
        entry.get("text"),
        entry.get("attribution"),
        entry.get("confidence"),
        entry.get("llm_score"),
        entry.get("stylo_score"),
        entry.get("label"),
        entry.get("status"),
        entry.get("appeal_reasoning"),
    ))
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def update_status(content_id: str, new_status: str) -> bool:
    """Update the status of the most recent 'submission' entry for a content_id."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("""
        UPDATE audit
        SET status = ?
        WHERE content_id = ? AND event = 'submission'
    """, (new_status, content_id))
    conn.commit()
    rows = cur.rowcount
    conn.close()
    return rows > 0


def get_entry_by_content_id(content_id: str) -> dict | None:
    """Return the original submission entry for a content_id, or None."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("""
        SELECT * FROM audit
        WHERE content_id = ? AND event = 'submission'
        LIMIT 1
    """, (content_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_entries(limit: int = 100) -> list[dict]:
    """Return recent audit entries, newest first."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM audit
        ORDER BY entry_id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]