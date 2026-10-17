"""Merchant labels: what each merchant is, from you (source "user") or from the AI (source "ai").

Your labels always win. AI labels apply automatically once their confidence reaches AI_MIN; below that
they sit in the review queue until you accept or change them.
"""
import sqlite3
from datetime import datetime

AI_MIN = 0.75


def init(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS merchant_labels (
            key TEXT PRIMARY KEY, category TEXT NOT NULL, source TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 1,
            model TEXT NOT NULL DEFAULT '', example TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL)"""
    )


def set_label(conn, key: str, category: str, source: str, confidence: float = 1.0, model: str = "", example: str = "",
              overwrite_user: bool = False) -> bool:
    """Save a label. AI labels never overwrite one you made. Returns whether a row was written."""
    init(conn)
    if not key or not category:
        raise ValueError("key and category are required")
    if source == "ai" and not overwrite_user:
        row = conn.execute("SELECT source FROM merchant_labels WHERE key=?", (key,)).fetchone()
        if row and row[0] == "user":
            return False
    conn.execute(
        """INSERT INTO merchant_labels (key, category, source, confidence, model, example, updated) VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(key) DO UPDATE SET category=excluded.category, source=excluded.source,
             confidence=excluded.confidence, model=excluded.model, example=excluded.example, updated=excluded.updated""",
        (key, category, source, float(confidence), model, example, datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    return True


def delete_label(conn, key: str) -> None:
    init(conn)
    conn.execute("DELETE FROM merchant_labels WHERE key=?", (key,))
    conn.commit()


def load_all(conn) -> dict[str, dict]:
    init(conn)
    return {k: {"category": c, "source": s, "confidence": conf, "model": m, "example": ex, "updated": u}
            for k, c, s, conf, m, ex, u in conn.execute(
                "SELECT key, category, source, confidence, model, example, updated FROM merchant_labels")}


def load_default() -> dict[str, dict]:
    """Labels from the private data dir's ledger (used by load_rules)."""
    from fin_client import store
    conn = store.connect()
    try:
        return load_all(conn)
    finally:
        conn.close()


def user_examples(conn, limit: int = 25) -> list[dict]:
    """Your most recent labels, used as few-shot examples for the AI."""
    init(conn)
    return [{"merchant": ex or key, "category": cat} for key, cat, ex in conn.execute(
        "SELECT key, category, example FROM merchant_labels WHERE source='user' ORDER BY updated DESC LIMIT ?", (limit,))]


def usable(label: dict | None) -> bool:
    return bool(label) and (label["source"] == "user" or label["confidence"] >= AI_MIN)
