"""
Idempotency store.

Tracks which ticket IDs have already been triaged for a client (one SQLite
file per client, clients/<name>/state.db) so that re-running the bot,
recovering from a crash mid-run, or an overlapping scheduled run never
double-classifies or double-alerts on the same message.
"""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_tickets (
    ticket_id    TEXT PRIMARY KEY,
    processed_at TEXT NOT NULL,
    category     TEXT,
    urgency      TEXT,
    queue        TEXT
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def is_processed(conn: sqlite3.Connection, ticket_id) -> bool:
    row = conn.execute(
        "SELECT 1 FROM processed_tickets WHERE ticket_id = ?", (str(ticket_id),)
    ).fetchone()
    return row is not None


def mark_processed(conn: sqlite3.Connection, ticket_id, timestamp: str,
                    category: str, urgency: str, queue: str):
    conn.execute(
        "INSERT OR REPLACE INTO processed_tickets "
        "(ticket_id, processed_at, category, urgency, queue) VALUES (?, ?, ?, ?, ?)",
        (str(ticket_id), timestamp, category, urgency, queue),
    )
    conn.commit()
