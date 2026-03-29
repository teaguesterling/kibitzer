"""Query fledgling for coaching data. Gracefully returns None if unavailable.

Prefers fledgling's Python API (fledgling.connect()) when importable.
Falls back to CLI subprocess calls if only the CLI is available.
Returns None on any failure — the coach works from state.json alone.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

# Cache the connection to avoid re-init on every query within a hook invocation
_connection = None
_connection_root: str | None = None


def _get_connection(project_dir: Path | None = None):
    """Get or create a fledgling Python API connection."""
    global _connection, _connection_root

    root = str(project_dir) if project_dir else str(Path.cwd())
    if _connection is not None and _connection_root == root:
        return _connection

    try:
        import fledgling
        _connection = fledgling.connect(root=root)
        _connection_root = root
        return _connection
    except Exception:
        return None


def _has_python_api() -> bool:
    """Check if the fledgling Python package is importable."""
    try:
        import fledgling  # noqa: F401
        return True
    except ImportError:
        return False


def is_available(project_dir: Path | None = None) -> bool:
    """Check if fledgling is available (Python API or CLI)."""
    if _has_python_api():
        try:
            con = _get_connection(project_dir)
            return con is not None
        except Exception:
            pass

    # Fall back to CLI check
    if shutil.which("fledgling") is None:
        return False
    return _find_init(project_dir) is not None


def _find_init(project_dir: Path | None = None) -> Path | None:
    """Find the fledgling init file (for CLI fallback)."""
    if env_init := os.getenv("FLEDGLING_INIT"):
        p = Path(env_init)
        return p if p.exists() else None

    if project_dir:
        local = project_dir / ".fledgling-init.sql"
        if local.exists():
            return local

    cwd_init = Path.cwd() / ".fledgling-init.sql"
    if cwd_init.exists():
        return cwd_init

    global_init = Path.home() / ".fledgling" / "init.sql"
    if global_init.exists():
        return global_init

    return None


def query(sql: str, project_dir: Path | None = None, timeout: float = 5.0) -> list[dict[str, Any]] | None:
    """Run a SQL query. Prefers Python API, falls back to CLI.

    Returns list of row dicts, or None on failure.
    """
    # Try Python API first
    if _has_python_api():
        result = _query_python(sql, project_dir)
        if result is not None:
            return result

    # Fall back to CLI
    return _query_cli(sql, project_dir, timeout)


def _query_python(sql: str, project_dir: Path | None = None) -> list[dict[str, Any]] | None:
    """Run a query via fledgling's Python API."""
    try:
        con = _get_connection(project_dir)
        if con is None:
            return None
        rel = con.sql(sql)
        df = rel.df()
        return df.to_dict(orient="records")
    except Exception:
        return None


def _query_cli(sql: str, project_dir: Path | None = None, timeout: float = 5.0) -> list[dict[str, Any]] | None:
    """Run a query via fledgling CLI subprocess."""
    if shutil.which("fledgling") is None:
        return None
    if _find_init(project_dir) is None:
        return None

    try:
        result = subprocess.run(
            ["fledgling", "-f", "json", "query", sql],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(project_dir) if project_dir else None,
        )
        if result.returncode != 0:
            return None

        output = result.stdout.strip()
        if not output:
            return []

        parsed = json.loads(output)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
        return None
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


def repeated_search_patterns(project_dir: Path | None = None) -> list[dict[str, Any]] | None:
    """Find search patterns used 3+ times in recent tool calls.

    Returns rows like: [{"pattern": "def handle", "count": 4, "tool": "Grep"}]
    """
    return query(
        """
        SELECT
            COALESCE(tc.grep_pattern, tc.file_path) AS pattern,
            tc.tool_name AS tool,
            count(*) AS count
        FROM tool_calls() tc
        WHERE tc.tool_name IN ('Grep', 'Read', 'Glob')
          AND tc.session_id = (SELECT session_id FROM sessions() ORDER BY started_at DESC LIMIT 1)
        GROUP BY pattern, tc.tool_name
        HAVING count(*) >= 3
        ORDER BY count DESC
        LIMIT 5
        """,
        project_dir=project_dir,
    )


def replaceable_bash_commands(project_dir: Path | None = None) -> list[dict[str, Any]] | None:
    """Find bash commands in the current session that have structured alternatives.

    Returns rows like: [{"command": "grep -rn 'def foo'", "replaceable_by": "FindDefinitions", "count": 2}]
    """
    return query(
        """
        SELECT
            leading_command AS command,
            replaceable_by,
            count(*) AS count
        FROM bash_commands()
        WHERE replaceable_by IS NOT NULL
          AND session_id = (SELECT session_id FROM sessions() ORDER BY started_at DESC LIMIT 1)
        GROUP BY leading_command, replaceable_by
        ORDER BY count DESC
        LIMIT 5
        """,
        project_dir=project_dir,
    )


def session_tool_summary(project_dir: Path | None = None) -> list[dict[str, Any]] | None:
    """Get tool usage summary for the current session.

    Returns rows like: [{"tool_name": "Edit", "total_calls": 12}]
    """
    return query(
        """
        SELECT tool_name, sum(call_count) AS total_calls
        FROM tool_frequency()
        WHERE session_id = (SELECT session_id FROM sessions() ORDER BY started_at DESC LIMIT 1)
        GROUP BY tool_name
        ORDER BY total_calls DESC
        """,
        project_dir=project_dir,
    )
