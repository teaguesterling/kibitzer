"""SQLite event log for cross-session queryability."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT DEFAULT (datetime('now')),
    session_id TEXT,
    event_type TEXT NOT NULL,
    tool_name TEXT,
    tool_input TEXT,
    success INTEGER,
    mode TEXT,
    data TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
"""


class KibitzerStore:
    """Append-only SQLite event log. Open-write-close per operation."""

    def __init__(self, store_path: Path):
        self.path = store_path

    def init(self) -> None:
        """Create the database and tables if they don't exist."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(_SCHEMA)

    def append_event(
        self,
        event_type: str,
        session_id: str | None = None,
        tool_name: str | None = None,
        tool_input: str | None = None,
        success: bool | None = None,
        mode: str | None = None,
        data: str | None = None,
    ) -> None:
        """Append one event. Opens connection, inserts, closes."""
        with self._connect() as con:
            con.execute(
                """INSERT INTO events (session_id, event_type, tool_name, tool_input, success, mode, data)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (session_id, event_type, tool_name, tool_input,
                 1 if success else (0 if success is not None else None),
                 mode, data),
            )

    def query_events(
        self,
        event_type: str | None = None,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query events. Returns list of dicts."""
        conditions = []
        params = []
        if event_type is not None:
            conditions.append("event_type = ?")
            params.append(event_type)
        if session_id is not None:
            conditions.append("session_id = ?")
            params.append(session_id)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        with self._connect() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                f"SELECT * FROM events {where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path), timeout=5)
