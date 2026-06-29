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
    data TEXT,
    source TEXT DEFAULT 'agent'
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
"""

_MIGRATIONS = [
    "ALTER TABLE events ADD COLUMN source TEXT DEFAULT 'agent'",
]


class KibitzerStore:
    """Append-only SQLite event log. Open-write-close per operation."""

    def __init__(self, store_path: Path):
        self.path = store_path

    def init(self) -> None:
        """Create the database and tables if they don't exist."""
        from kibitzer.state import ensure_state_dir
        ensure_state_dir(self.path.parent)
        with self._connect() as con:
            con.executescript(_SCHEMA)
            self._migrate(con)

    def _migrate(self, con: sqlite3.Connection) -> None:
        """Apply migrations for schema changes to existing databases."""
        for sql in _MIGRATIONS:
            try:
                con.execute(sql)
            except sqlite3.OperationalError as e:
                if "duplicate column" in str(e):
                    continue
                raise

    def append_event(
        self,
        event_type: str,
        session_id: str | None = None,
        tool_name: str | None = None,
        tool_input: str | None = None,
        success: bool | None = None,
        mode: str | None = None,
        data: str | None = None,
        source: str | None = None,
    ) -> None:
        """Append one event. Opens connection, inserts, closes."""
        with self._connect() as con:
            con.execute(
                """INSERT INTO events (session_id, event_type, tool_name, tool_input, success, mode, data, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, event_type, tool_name, tool_input,
                 1 if success else (0 if success is not None else None),
                 mode, data, source or "agent"),
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
        con = sqlite3.connect(str(self.path), timeout=5)
        # SSD-friendliness: this store is advisory, non-authoritative telemetry
        # appended once per tool call from a subprocess-per-hook. A held-open
        # WAL+NORMAL connection isn't possible here (each hook is its own short
        # process), and close-checkpoint would re-fsync — so synchronous=OFF is
        # the only lever that zeroes the per-event fsync (measured 4->0). Worst
        # case on power loss is losing the last few observed events (no decision
        # depends on durability); WAL also avoids per-transaction journal churn.
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=OFF")
        return con
