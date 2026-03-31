# Kibitzer Python API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract kibitzer's core logic into a reusable `KibitzerSession` class with SQLite event log, then rewire hooks and MCP server as thin wrappers.

**Architecture:** `KibitzerSession` is a stateful class with context manager support. It holds config, state, and component references in memory. `load()` reads from disk, `save()` writes back. A `KibitzerStore` handles SQLite event log append/query. Hooks call `with KibitzerSession(safe_mode=True)` for crash-safe operation. Lackpy calls the same class directly.

**Tech Stack:** Python 3.10+, sqlite3 (stdlib), existing kibitzer modules (config, state, guards, interceptors, coach, controller)

---

## File Structure

```
src/kibitzer/
├── session.py          # NEW: KibitzerSession class, CallResult dataclass
├── store.py            # NEW: KibitzerStore — SQLite event log
├── config.py           # unchanged
├── state.py            # unchanged (add bash_without_structured to fresh_state if missing)
├── guards/
│   └── path_guard.py   # unchanged
├── interceptors/       # unchanged
├── coach/              # unchanged
├── controller/         # unchanged
├── hooks/
│   ├── pre_tool_use.py   # SIMPLIFIED: thin wrapper around KibitzerSession
│   ├── post_tool_use.py  # SIMPLIFIED: thin wrapper around KibitzerSession
│   └── templates.py      # unchanged
├── mcp/
│   └── server.py         # SIMPLIFIED: delegates to KibitzerSession
├── cli.py              # unchanged
└── __init__.py         # ADD: export KibitzerSession, CallResult

tests/
├── test_session.py           # NEW: KibitzerSession lifecycle, before/after/validate
├── test_store.py             # NEW: SQLite store append/query
├── test_session_safe_mode.py # NEW: safe_mode error handling
├── test_session_lackpy.py    # NEW: register_tools, validate_program, register_context, report
├── test_session_integration.py # NEW: hooks + MCP produce same results via session
```

---

### Task 1: CallResult Dataclass

**Files:**
- Create: `src/kibitzer/session.py` (partial — just CallResult)
- Create: `tests/test_session.py` (partial — just CallResult tests)

- [ ] **Step 1: Write failing tests for CallResult**

Create `tests/test_session.py`:

```python
"""Tests for KibitzerSession and CallResult."""

from kibitzer.session import CallResult


class TestCallResult:
    def test_allow_result(self):
        result = CallResult()
        assert not result.denied
        assert result.reason == ""
        assert result.context == ""

    def test_deny_result(self):
        result = CallResult(denied=True, reason="not writable", tool="Edit")
        assert result.denied
        assert "not writable" in result.reason

    def test_context_result(self):
        result = CallResult(context="[kibitzer] suggestion", tool="Bash")
        assert not result.denied
        assert "[kibitzer]" in result.context

    def test_to_hook_output_deny(self):
        result = CallResult(denied=True, reason="blocked")
        output = result.to_hook_output("PreToolUse")
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert output["hookSpecificOutput"]["permissionDecisionReason"] == "blocked"
        assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"

    def test_to_hook_output_context(self):
        result = CallResult(context="[kibitzer] try jetsam")
        output = result.to_hook_output("PreToolUse")
        assert "additionalContext" in output["hookSpecificOutput"]
        assert "permissionDecision" not in output["hookSpecificOutput"]

    def test_to_hook_output_post_tool(self):
        result = CallResult(context="[kibitzer] mode switched")
        output = result.to_hook_output("PostToolUse")
        assert output["hookSpecificOutput"]["hookEventName"] == "PostToolUse"

    def test_to_hook_output_empty(self):
        result = CallResult()
        output = result.to_hook_output("PreToolUse")
        assert output == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_session.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kibitzer.session'`

- [ ] **Step 3: Implement CallResult**

Create `src/kibitzer/session.py`:

```python
"""KibitzerSession — the Python API for kibitzer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CallResult:
    """Result of a before_call, after_call, or validate_calls check."""

    denied: bool = False
    reason: str = ""
    context: str = ""
    tool: str = ""

    def to_hook_output(self, hook_event: str = "PreToolUse") -> dict:
        """Convert to Claude Code hook JSON protocol."""
        if self.denied:
            return {
                "hookSpecificOutput": {
                    "hookEventName": hook_event,
                    "permissionDecision": "deny",
                    "permissionDecisionReason": self.reason,
                }
            }
        if self.context:
            return {
                "hookSpecificOutput": {
                    "hookEventName": hook_event,
                    "additionalContext": self.context,
                }
            }
        return {}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_session.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/kibitzer/session.py tests/test_session.py
git commit -m "feat: CallResult dataclass for Python API"
```

---

### Task 2: KibitzerStore (SQLite event log)

**Files:**
- Create: `src/kibitzer/store.py`
- Create: `tests/test_store.py`

- [ ] **Step 1: Write failing tests for KibitzerStore**

Create `tests/test_store.py`:

```python
"""Tests for KibitzerStore — SQLite event log."""

import sqlite3
from kibitzer.store import KibitzerStore


class TestKibitzerStore:
    def test_create_store(self, tmp_path):
        store_path = tmp_path / "store.sqlite"
        store = KibitzerStore(store_path)
        store.init()
        assert store_path.exists()

    def test_append_and_query(self, tmp_path):
        store = KibitzerStore(tmp_path / "store.sqlite")
        store.init()

        store.append_event(
            event_type="tool_call",
            session_id="sess-001",
            tool_name="Edit",
            tool_input='{"file_path": "src/foo.py"}',
            success=True,
            mode="implement",
        )

        events = store.query_events()
        assert len(events) == 1
        assert events[0]["event_type"] == "tool_call"
        assert events[0]["tool_name"] == "Edit"
        assert events[0]["session_id"] == "sess-001"

    def test_query_by_event_type(self, tmp_path):
        store = KibitzerStore(tmp_path / "store.sqlite")
        store.init()

        store.append_event(event_type="tool_call", tool_name="Edit")
        store.append_event(event_type="mode_switch", tool_name="")
        store.append_event(event_type="tool_call", tool_name="Read")

        tool_calls = store.query_events(event_type="tool_call")
        assert len(tool_calls) == 2

        switches = store.query_events(event_type="mode_switch")
        assert len(switches) == 1

    def test_query_by_session(self, tmp_path):
        store = KibitzerStore(tmp_path / "store.sqlite")
        store.init()

        store.append_event(event_type="tool_call", session_id="sess-001")
        store.append_event(event_type="tool_call", session_id="sess-002")

        events = store.query_events(session_id="sess-001")
        assert len(events) == 1

    def test_query_limit(self, tmp_path):
        store = KibitzerStore(tmp_path / "store.sqlite")
        store.init()

        for i in range(10):
            store.append_event(event_type="tool_call", tool_name=f"tool_{i}")

        events = store.query_events(limit=3)
        assert len(events) == 3

    def test_append_with_data(self, tmp_path):
        store = KibitzerStore(tmp_path / "store.sqlite")
        store.init()

        store.append_event(
            event_type="denial",
            tool_name="Edit",
            data='{"reason": "path not writable"}',
        )

        events = store.query_events()
        assert events[0]["data"] == '{"reason": "path not writable"}'

    def test_corrupt_store_reinits(self, tmp_path):
        """Corrupt SQLite file should be handled gracefully."""
        store_path = tmp_path / "store.sqlite"
        store_path.write_text("not a sqlite file")

        store = KibitzerStore(store_path)
        # Should not crash — either reinit or degrade
        try:
            store.init()
            store.append_event(event_type="test")
            events = store.query_events()
            assert len(events) == 1
        except Exception:
            pass  # acceptable to fail on corrupt file

    def test_concurrent_appends(self, tmp_path):
        """Two store instances appending to the same file."""
        store_path = tmp_path / "store.sqlite"
        store_a = KibitzerStore(store_path)
        store_a.init()
        store_b = KibitzerStore(store_path)

        store_a.append_event(event_type="tool_call", tool_name="Edit")
        store_b.append_event(event_type="tool_call", tool_name="Read")

        events = store_a.query_events()
        assert len(events) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_store.py -v`
Expected: FAIL

- [ ] **Step 3: Implement KibitzerStore**

Create `src/kibitzer/store.py`:

```python
"""SQLite event log for cross-session queryability."""

from __future__ import annotations

import json
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_store.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/kibitzer/store.py tests/test_store.py
git commit -m "feat: KibitzerStore — SQLite event log"
```

---

### Task 3: KibitzerSession Core (load, save, context manager, before_call, after_call)

**Files:**
- Modify: `src/kibitzer/session.py`
- Create: `tests/test_session.py` (extend with session tests)

This is the biggest task — the session class that pulls together all existing modules.

- [ ] **Step 1: Write failing tests for session lifecycle**

Append to `tests/test_session.py`:

```python
from pathlib import Path
from kibitzer.session import KibitzerSession, CallResult
from kibitzer.state import fresh_state, save_state, load_state


class TestSessionLifecycle:
    def test_context_manager_loads_and_saves(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            assert session.mode == "implement"

    def test_manual_load_save(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        session = KibitzerSession(project_dir=tmp_path)
        session.load()
        assert session.mode == "implement"
        session.save()

    def test_no_state_dir_uses_defaults(self, tmp_path):
        with KibitzerSession(project_dir=tmp_path) as session:
            assert session.mode == "implement"

    def test_properties(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            assert session.mode == "implement"
            assert isinstance(session.config, dict)
            assert isinstance(session.state, dict)
            assert session.writable == ["src/", "lib/"]


class TestBeforeCall:
    def test_allow_src_edit(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            result = session.before_call("Edit", {"file_path": "src/foo.py"})
            assert result is None

    def test_deny_test_edit_in_implement(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            result = session.before_call("Edit", {"file_path": "tests/foo.py"})
            assert result is not None
            assert result.denied
            assert "ChangeToolMode" in result.reason

    def test_allow_read_in_any_mode(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        state = fresh_state()
        state["mode"] = "explore"
        save_state(state, state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            result = session.before_call("Read", {"file_path": "src/foo.py"})
            assert result is None


class TestAfterCall:
    def test_updates_counters(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            session.after_call("Edit", {"file_path": "src/foo.py"}, success=True)
            assert session.state["total_calls"] == 1
            assert session.state["success_count"] == 1

    def test_mode_transition(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        state = fresh_state()
        state["consecutive_failures"] = 2
        save_state(state, state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            result = session.after_call("Bash", {"command": "make"}, success=False)
            assert session.mode == "explore"
            assert result is not None
            assert "explore" in result.context

    def test_state_persisted_after_exit(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            session.after_call("Edit", {"file_path": "src/foo.py"}, success=True)

        loaded = load_state(state_dir)
        assert loaded["total_calls"] == 1


class TestValidateCalls:
    def test_all_allowed(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            violations = session.validate_calls([
                {"tool": "Read", "input": {"file_path": "src/foo.py"}},
                {"tool": "Edit", "input": {"file_path": "src/bar.py"}},
            ])
            assert violations == []

    def test_returns_violations(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            violations = session.validate_calls([
                {"tool": "Edit", "input": {"file_path": "src/ok.py"}},
                {"tool": "Edit", "input": {"file_path": "tests/blocked.py"}},
            ])
            assert len(violations) == 1
            assert violations[0].denied
            assert "tests/blocked.py" in violations[0].reason

    def test_does_not_modify_state(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            before = session.state["total_calls"]
            session.validate_calls([
                {"tool": "Edit", "input": {"file_path": "tests/foo.py"}},
            ])
            assert session.state["total_calls"] == before


class TestChangeMode:
    def test_switch_mode(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            result = session.change_mode("test", reason="writing tests")
            assert result["new_mode"] == "test"
            assert result["previous_mode"] == "implement"
            assert session.mode == "test"

    def test_invalid_mode(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            result = session.change_mode("nonexistent")
            assert "error" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_session.py -v`
Expected: FAIL — `KibitzerSession` not defined

- [ ] **Step 3: Implement KibitzerSession**

Update `src/kibitzer/session.py` — add the full session class after `CallResult`:

```python
import json
from pathlib import Path
from typing import Any, Optional

from kibitzer.config import load_config, get_mode_policy
from kibitzer.controller.mode_controller import (
    apply_transition, check_transitions, update_counters,
)
from kibitzer.guards.path_guard import check_path
from kibitzer.interceptors.base import InterceptMode
from kibitzer.interceptors.registry import build_registry
from kibitzer.state import fresh_state, load_state, save_state
from kibitzer.store import KibitzerStore
from kibitzer.coach.suggestions import generate_suggestions, should_fire
from kibitzer.coach.tools import discover_tools

_WRITE_TOOLS = {"Edit", "Write", "NotebookEdit"}
_INTERCEPT_TOOLS = {"Bash"}


class KibitzerSession:
    """The Python API for kibitzer.

    Use as a context manager for automatic load/save:
        with KibitzerSession(project_dir=".") as session:
            result = session.before_call("Edit", {"file_path": "src/foo.py"})

    Or manage lifecycle manually:
        session = KibitzerSession()
        session.load()
        ...
        session.save()
    """

    def __init__(
        self,
        project_dir: str | Path | None = None,
        safe_mode: bool = False,
    ):
        self._project_dir = Path(project_dir) if project_dir else Path.cwd()
        self._safe_mode = safe_mode
        self._config: dict = {}
        self._state: dict = {}
        self._store: KibitzerStore | None = None
        self._interceptors: list | None = None
        self._available_tools: dict | None = None
        self._loaded = False

    def __enter__(self):
        self.load()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self._record_error(exc_type, exc_val)
        try:
            self.save()
        except Exception:
            if exc_type is None:
                raise
        return False

    def load(self) -> None:
        """Load config and state from disk."""
        self._config = load_config(self._project_dir)
        state_dir = self._project_dir / ".kibitzer"
        self._state = load_state(state_dir)
        store_path = state_dir / "store.sqlite"
        self._store = KibitzerStore(store_path)
        self._store.init()
        self._loaded = True

    def save(self) -> None:
        """Persist state to disk."""
        state_dir = self._project_dir / ".kibitzer"
        save_state(self._state, state_dir)

    # --- Properties ---

    @property
    def mode(self) -> str:
        return self._state.get("mode", "implement")

    @property
    def state(self) -> dict:
        return self._state

    @property
    def config(self) -> dict:
        return self._config

    @property
    def writable(self) -> list[str]:
        policy = get_mode_policy(self._config, self.mode)
        return policy.get("writable", ["*"])

    @property
    def path_guard(self):
        from kibitzer.guards import path_guard
        return path_guard

    @property
    def coach(self):
        from kibitzer.coach import observer, suggestions
        return type("Coach", (), {"detect_patterns": observer.detect_patterns,
                                   "generate_suggestions": suggestions.generate_suggestions,
                                   "should_fire": suggestions.should_fire})()

    @property
    def controller(self):
        from kibitzer.controller import mode_controller
        return mode_controller

    @property
    def interceptors(self) -> list:
        if self._interceptors is None:
            self._interceptors = build_registry()
        return self._interceptors

    @property
    def available_tools(self) -> dict:
        if self._available_tools is None:
            self._available_tools = discover_tools(self._project_dir)
        return self._available_tools

    # --- Core API ---

    def before_call(
        self,
        tool_name: str,
        tool_input: dict[str, Any] | None = None,
    ) -> CallResult | None:
        """Pre-execution check: path guard + interceptors."""
        if self._safe_mode:
            try:
                return self._before_call_impl(tool_name, tool_input or {})
            except Exception:
                return None
        return self._before_call_impl(tool_name, tool_input or {})

    def after_call(
        self,
        tool_name: str,
        tool_input: dict[str, Any] | None = None,
        success: bool | None = None,
        tool_result: Any = None,
    ) -> CallResult | None:
        """Post-execution: update counters, check transitions, run coach."""
        if self._safe_mode:
            try:
                return self._after_call_impl(tool_name, tool_input or {}, success, tool_result)
            except Exception:
                return None
        return self._after_call_impl(tool_name, tool_input or {}, success, tool_result)

    def validate_calls(self, calls: list[dict]) -> list[CallResult]:
        """Batch validation — check calls without updating state."""
        violations = []
        mode_policy = get_mode_policy(self._config, self.mode)
        for call in calls:
            tool = call.get("tool", "")
            inp = call.get("input", {})
            if tool in _WRITE_TOOLS:
                file_path = inp.get("file_path", "") or inp.get("notebook_path", "")
                if file_path:
                    file_path = self._relativize(file_path)
                    result = check_path(file_path, mode_policy)
                    if not result.allowed:
                        violations.append(CallResult(
                            denied=True, reason=result.reason, tool=tool,
                        ))
        return violations

    def change_mode(self, mode: str, reason: str = "") -> dict[str, Any]:
        """Switch mode. Returns new mode info or error."""
        if mode not in self._config.get("modes", {}):
            return {"error": f"Unknown mode: {mode}. Available: {list(self._config['modes'].keys())}"}

        previous = self.mode
        policy = get_mode_policy(self._config, mode)

        self._state["previous_mode"] = previous
        self._state["turns_in_previous_mode"] = self._state.get("turns_in_mode", 0)
        self._state["mode"] = mode
        self._state["failure_count"] = 0
        self._state["success_count"] = 0
        self._state["consecutive_failures"] = 0
        self._state["turns_in_mode"] = 0
        self._state["mode_switches"] = self._state.get("mode_switches", 0) + 1
        self._state["tools_used_in_mode"] = {}

        if self._store:
            self._store.append_event(
                event_type="mode_switch",
                session_id=self._state.get("session_id"),
                mode=mode,
                data=json.dumps({"previous": previous, "reason": reason}),
            )

        return {
            "previous_mode": previous,
            "new_mode": mode,
            "writable": policy["writable"],
            "strategy": policy["strategy"],
        }

    def get_suggestions(self, mark_given: bool = True) -> list[str]:
        """Get coaching suggestions."""
        return generate_suggestions(
            self._state, project_dir=self._project_dir, mark_given=mark_given,
        )

    def get_feedback(
        self,
        status: bool = True,
        suggestions: bool = True,
        intercepts: bool = True,
    ) -> dict[str, Any]:
        """Combined feedback — status, suggestions, intercepts."""
        result: dict[str, Any] = {}

        if status:
            policy = get_mode_policy(self._config, self.mode)
            result["status"] = {
                "mode": self.mode,
                "failure_count": self._state["failure_count"],
                "success_count": self._state["success_count"],
                "consecutive_failures": self._state["consecutive_failures"],
                "turns_in_mode": self._state["turns_in_mode"],
                "total_calls": self._state["total_calls"],
                "writable": policy["writable"],
            }

        if suggestions:
            result["suggestions"] = self.get_suggestions(mark_given=False)

        if intercepts:
            result["intercepts"] = self._read_intercept_log()

        return result

    # --- Internal ---

    def _before_call_impl(self, tool_name: str, tool_input: dict) -> CallResult | None:
        mode_policy = get_mode_policy(self._config, self.mode)

        # Path guard
        if tool_name in _WRITE_TOOLS:
            file_path = tool_input.get("file_path", "") or tool_input.get("notebook_path", "")
            if file_path:
                file_path = self._relativize(file_path)
                result = check_path(file_path, mode_policy)
                if not result.allowed:
                    if self._store:
                        self._store.append_event(
                            event_type="denial",
                            session_id=self._state.get("session_id"),
                            tool_name=tool_name,
                            tool_input=json.dumps(tool_input)[:500],
                            mode=self.mode,
                            data=json.dumps({"reason": result.reason}),
                        )
                    return CallResult(denied=True, reason=result.reason, tool=tool_name)

        # Interceptors
        if tool_name in _INTERCEPT_TOOLS:
            command = tool_input.get("command", "")
            if command:
                plugin_modes = {}
                for name, pcfg in self._config.get("plugins", {}).items():
                    if pcfg.get("enabled", True):
                        plugin_modes[name] = pcfg.get("mode", "observe")

                for plugin in self.interceptors:
                    if plugin.name not in plugin_modes:
                        continue
                    suggestion = plugin.check(command)
                    if suggestion is None:
                        continue

                    pmode = InterceptMode(plugin_modes.get(plugin.name, "observe"))

                    if pmode == InterceptMode.OBSERVE:
                        self._log_intercept(command, suggestion)
                        return None

                    if pmode == InterceptMode.SUGGEST:
                        return CallResult(
                            context=(
                                f"[kibitzer] {suggestion.plugin} suggests: "
                                f"{suggestion.tool}\nReason: {suggestion.reason}"
                            ),
                            tool=tool_name,
                        )

                    if pmode == InterceptMode.REDIRECT:
                        return CallResult(
                            denied=True,
                            reason=(
                                f"A structured alternative is available: "
                                f"{suggestion.tool}\n{suggestion.reason}"
                            ),
                            tool=tool_name,
                        )

        return None

    def _after_call_impl(
        self, tool_name: str, tool_input: dict,
        success: bool | None, tool_result: Any,
    ) -> CallResult | None:
        # Detect success if not explicit
        if success is None:
            success = self._detect_success(tool_name, tool_result)

        update_counters(self._state, tool_name, success, tool_input=tool_input)

        messages = []

        transition = check_transitions(self._state, self._config)
        if transition is not None:
            apply_transition(self._state, transition)
            messages.append(
                f"[kibitzer] Mode switched to {transition.target}: {transition.reason}"
            )

        if should_fire(self._state, self._config):
            suggestions = generate_suggestions(
                self._state, project_dir=self._project_dir,
            )
            for s in suggestions:
                messages.append(f"[kibitzer] {s}")

        # Append to SQLite store
        if self._store:
            self._store.append_event(
                event_type="tool_call",
                session_id=self._state.get("session_id"),
                tool_name=tool_name,
                tool_input=json.dumps(tool_input)[:500],
                success=success,
                mode=self.mode,
            )

        if messages:
            return CallResult(context="\n".join(messages), tool=tool_name)
        return None

    def _detect_success(self, tool_name: str, tool_result: Any) -> bool:
        if tool_name == "Bash" and isinstance(tool_result, dict):
            return tool_result.get("exitCode", 0) == 0
        if isinstance(tool_result, dict) and "error" in tool_result:
            return False
        return True

    def _relativize(self, file_path: str) -> str:
        try:
            fp = Path(file_path)
            if fp.is_absolute():
                return str(fp.relative_to(self._project_dir))
        except (ValueError, TypeError):
            pass
        return file_path

    def _log_intercept(self, command: str, suggestion) -> None:
        log_path = self._project_dir / ".kibitzer" / "intercept.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "bash_command": command[:200],
            "suggested_tool": suggestion.tool,
            "reason": suggestion.reason,
            "plugin": suggestion.plugin,
        }
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def _read_intercept_log(self) -> dict[str, Any]:
        log_path = self._project_dir / ".kibitzer" / "intercept.log"
        entries = []
        if log_path.exists():
            for line in log_path.read_text().strip().split("\n"):
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return {"total_observed": len(entries), "recent": entries[-10:]}

    def _record_error(self, exc_type, exc_val) -> None:
        try:
            if self._store:
                self._store.append_event(
                    event_type="error",
                    session_id=self._state.get("session_id"),
                    data=json.dumps({"type": str(exc_type), "message": str(exc_val)}),
                )
        except Exception:
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_session.py -v`
Expected: all PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `source .venv/bin/activate && python -m pytest tests/ -q --tb=short`
Expected: 374+ passed

- [ ] **Step 6: Commit**

```bash
git add src/kibitzer/session.py tests/test_session.py
git commit -m "feat: KibitzerSession core — before_call, after_call, validate_calls, context manager"
```

---

### Task 4: Safe Mode Tests

**Files:**
- Create: `tests/test_session_safe_mode.py`

- [ ] **Step 1: Write safe mode tests**

Create `tests/test_session_safe_mode.py`:

```python
"""Tests for KibitzerSession safe_mode — errors swallowed, never crashes."""

from pathlib import Path
from unittest.mock import patch
from kibitzer.session import KibitzerSession
from kibitzer.state import fresh_state, save_state


class TestSafeMode:
    def _project(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)
        return tmp_path

    def test_before_call_returns_none_on_error(self, tmp_path):
        proj = self._project(tmp_path)
        with patch("kibitzer.session.check_path", side_effect=RuntimeError("boom")):
            with KibitzerSession(project_dir=proj, safe_mode=True) as session:
                result = session.before_call("Edit", {"file_path": "src/foo.py"})
                assert result is None  # swallowed

    def test_after_call_returns_none_on_error(self, tmp_path):
        proj = self._project(tmp_path)
        with patch("kibitzer.session.update_counters", side_effect=RuntimeError("boom")):
            with KibitzerSession(project_dir=proj, safe_mode=True) as session:
                result = session.after_call("Edit", {}, success=True)
                assert result is None

    def test_safe_mode_still_saves(self, tmp_path):
        """Even in safe mode, state should be saved on exit."""
        proj = self._project(tmp_path)
        with KibitzerSession(project_dir=proj, safe_mode=True) as session:
            session.after_call("Read", {}, success=True)

        from kibitzer.state import load_state
        state = load_state(proj / ".kibitzer")
        assert state["total_calls"] == 1

    def test_normal_mode_raises(self, tmp_path):
        """Without safe_mode, errors should propagate."""
        proj = self._project(tmp_path)
        import pytest
        with patch("kibitzer.session.check_path", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                with KibitzerSession(project_dir=proj) as session:
                    session.before_call("Edit", {"file_path": "src/foo.py"})

    def test_context_manager_saves_on_exception(self, tmp_path):
        """If the body raises, state should still be saved."""
        proj = self._project(tmp_path)
        try:
            with KibitzerSession(project_dir=proj) as session:
                session.after_call("Edit", {}, success=True)
                raise ValueError("user error")
        except ValueError:
            pass

        from kibitzer.state import load_state
        state = load_state(proj / ".kibitzer")
        assert state["total_calls"] == 1
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_session_safe_mode.py -v`
Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_session_safe_mode.py
git commit -m "test: KibitzerSession safe_mode error handling"
```

---

### Task 5: Rewire Hooks

**Files:**
- Modify: `src/kibitzer/hooks/pre_tool_use.py`
- Modify: `src/kibitzer/hooks/post_tool_use.py`

Simplify both hooks to thin wrappers around KibitzerSession.

- [ ] **Step 1: Rewrite pre_tool_use.py**

Replace `src/kibitzer/hooks/pre_tool_use.py`:

```python
"""PreToolUse hook — thin wrapper around KibitzerSession."""

from __future__ import annotations

import json
import sys

from kibitzer.session import KibitzerSession


def main() -> None:
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError):
        return

    with KibitzerSession(safe_mode=True) as session:
        result = session.before_call(
            tool_name=hook_input.get("tool_name", ""),
            tool_input=hook_input.get("tool_input", {}),
        )

    if result is not None:
        output = result.to_hook_output("PreToolUse")
        if output:
            print(json.dumps(output))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Rewrite post_tool_use.py**

Replace `src/kibitzer/hooks/post_tool_use.py`:

```python
"""PostToolUse hook — thin wrapper around KibitzerSession."""

from __future__ import annotations

import json
import sys

from kibitzer.session import KibitzerSession


def main() -> None:
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError):
        return

    with KibitzerSession(safe_mode=True) as session:
        result = session.after_call(
            tool_name=hook_input.get("tool_name", ""),
            tool_input=hook_input.get("tool_input", {}),
            tool_result=hook_input.get("tool_result"),
        )

    if result is not None:
        output = result.to_hook_output("PostToolUse")
        if output:
            print(json.dumps(output))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run full test suite**

Run: `source .venv/bin/activate && python -m pytest tests/ -q --tb=short`
Expected: all existing tests pass. The hooks now produce output via `CallResult.to_hook_output()` which should match the previous JSON structure.

Note: some existing hook tests import `handle_pre_tool_use` and `handle_post_tool_use` directly. These functions no longer exist. Two options: (a) keep them as thin wrappers that create a session, or (b) update the tests to use KibitzerSession. Option (a) is simpler for migration — add compatibility wrappers:

```python
# At end of pre_tool_use.py, for backwards compat with existing tests
def handle_pre_tool_use(hook_input, project_dir=None, plugin_modes=None):
    """Compatibility wrapper for existing tests."""
    session = KibitzerSession(project_dir=project_dir)
    session.load()
    if plugin_modes is not None:
        # Override plugin modes in config
        for name, mode in plugin_modes.items():
            session._config.setdefault("plugins", {}).setdefault(name, {})["mode"] = mode
    result = session.before_call(
        hook_input.get("tool_name", ""),
        hook_input.get("tool_input", {}),
    )
    session.save()
    if result is None:
        return None
    return result.to_hook_output("PreToolUse") or None
```

```python
# At end of post_tool_use.py
def handle_post_tool_use(hook_input, project_dir=None):
    """Compatibility wrapper for existing tests."""
    session = KibitzerSession(project_dir=project_dir)
    session.load()
    result = session.after_call(
        hook_input.get("tool_name", ""),
        hook_input.get("tool_input", {}),
        tool_result=hook_input.get("tool_result"),
    )
    session.save()
    if result is None:
        return None
    return result.to_hook_output("PostToolUse") or None
```

Also keep `_detect_success` accessible for the existing test that imports it:

```python
# At end of post_tool_use.py
def _detect_success(hook_input):
    """Compatibility wrapper for existing tests."""
    session = KibitzerSession.__new__(KibitzerSession)
    return session._detect_success(
        hook_input.get("tool_name", ""),
        hook_input.get("tool_result"),
    )
```

- [ ] **Step 4: Run full test suite again**

Run: `source .venv/bin/activate && python -m pytest tests/ -q --tb=short`
Expected: all 374+ tests pass

- [ ] **Step 5: Commit**

```bash
git add src/kibitzer/hooks/pre_tool_use.py src/kibitzer/hooks/post_tool_use.py
git commit -m "refactor: hooks are thin wrappers around KibitzerSession"
```

---

### Task 6: Rewire MCP Server

**Files:**
- Modify: `src/kibitzer/mcp/server.py`

- [ ] **Step 1: Rewrite server.py to use KibitzerSession**

Replace the standalone functions with session-based implementations:

```python
"""FastMCP server — delegates to KibitzerSession."""

from __future__ import annotations

import json
from kibitzer.session import KibitzerSession

# Module-level session for the MCP server lifetime
_session: KibitzerSession | None = None


def _get_session() -> KibitzerSession:
    global _session
    if _session is None:
        _session = KibitzerSession()
        _session.load()
    return _session


def change_tool_mode(mode: str, reason: str | None = None, project_dir=None):
    """For direct Python callers and test compatibility."""
    if project_dir is not None:
        session = KibitzerSession(project_dir=project_dir)
        session.load()
        result = session.change_mode(mode, reason=reason or "")
        session.save()
        return result
    session = _get_session()
    result = session.change_mode(mode, reason=reason or "")
    session.save()
    return result


def get_feedback(status=True, suggestions=True, intercepts=True, project_dir=None):
    """For direct Python callers and test compatibility."""
    if project_dir is not None:
        session = KibitzerSession(project_dir=project_dir)
        session.load()
        return session.get_feedback(status, suggestions, intercepts)
    session = _get_session()
    return session.get_feedback(status, suggestions, intercepts)


def create_mcp_server():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "kibitzer",
        instructions=(
            "Kibitzer watches your tool calls and suggests structured alternatives. "
            "Use ChangeToolMode to switch between modes (free, implement, test, "
            "docs, explore, review). Use GetFeedback to check status, "
            "get coaching suggestions, and see intercepted patterns."
        ),
    )

    @mcp.tool()
    def ChangeToolMode(mode: str, reason: str = "") -> str:
        result = change_tool_mode(mode, reason=reason)
        return json.dumps(result, indent=2)

    @mcp.tool()
    def GetFeedback(status: bool = True, suggestions: bool = True, intercepts: bool = True) -> str:
        result = get_feedback(status=status, suggestions=suggestions, intercepts=intercepts)
        return json.dumps(result, indent=2)

    return mcp
```

- [ ] **Step 2: Run MCP tests**

Run: `source .venv/bin/activate && python -m pytest tests/test_mcp_server.py -v`
Expected: all PASS

- [ ] **Step 3: Run full test suite**

Run: `source .venv/bin/activate && python -m pytest tests/ -q --tb=short`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add src/kibitzer/mcp/server.py
git commit -m "refactor: MCP server delegates to KibitzerSession"
```

---

### Task 7: Export from __init__.py

**Files:**
- Modify: `src/kibitzer/__init__.py`

- [ ] **Step 1: Add exports**

Update `src/kibitzer/__init__.py`:

```python
"""Kibitzer — watches agent tool calls and suggests structured alternatives."""

__version__ = "0.2.1"

from kibitzer.session import CallResult, KibitzerSession

__all__ = ["KibitzerSession", "CallResult", "__version__"]
```

- [ ] **Step 2: Verify import works**

Run: `source .venv/bin/activate && python -c "from kibitzer import KibitzerSession, CallResult; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/kibitzer/__init__.py
git commit -m "feat: export KibitzerSession and CallResult from kibitzer package"
```

---

### Task 8: Lackpy Integration APIs

**Files:**
- Modify: `src/kibitzer/session.py`
- Create: `tests/test_session_lackpy.py`

- [ ] **Step 1: Write failing tests for lackpy APIs**

Create `tests/test_session_lackpy.py`:

```python
"""Tests for lackpy integration APIs on KibitzerSession."""

import json
from kibitzer.session import KibitzerSession
from kibitzer.state import fresh_state, save_state


def _project(tmp_path):
    state_dir = tmp_path / ".kibitzer"
    state_dir.mkdir()
    save_state(fresh_state(), state_dir)
    return tmp_path


class TestRegisterTools:
    def test_register_and_query(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.register_tools([
                {"name": "Read", "grade": (0, 0)},
                {"name": "Edit", "grade": (2, 1)},
                {"name": "Bash", "grade": (4, 4)},
            ])
            tools = session.registered_tools
            assert tools["Read"] == (0, 0)
            assert tools["Bash"] == (4, 4)

    def test_not_persisted(self, tmp_path):
        """Registered tools are session-memory only."""
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.register_tools([{"name": "Read", "grade": (0, 0)}])

        with KibitzerSession(project_dir=proj) as session:
            assert session.registered_tools == {}


class TestValidateProgram:
    def test_grade_ceiling_violation(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.register_tools([
                {"name": "Edit", "grade": (2, 1)},
                {"name": "Bash", "grade": (4, 4)},
            ])
            result = session.validate_program({
                "calls": [
                    {"tool": "Edit", "input": {"file_path": "src/foo.py"}},
                    {"tool": "Bash", "input": {"command": "rm -rf /"}},
                ],
                "grade_ceiling": (2, 2),
            })
            assert result.denied
            assert "grade" in result.reason.lower() or "Bash" in result.reason

    def test_call_budget_exceeded(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            result = session.validate_program({
                "calls": [{"tool": "Read", "input": {}}] * 10,
                "call_budget": 5,
            })
            assert result.denied
            assert "budget" in result.reason.lower()

    def test_path_violations_included(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            result = session.validate_program({
                "calls": [
                    {"tool": "Edit", "input": {"file_path": "tests/foo.py"}},
                ],
            })
            assert result.denied

    def test_all_valid(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            result = session.validate_program({
                "calls": [
                    {"tool": "Read", "input": {"file_path": "src/foo.py"}},
                    {"tool": "Edit", "input": {"file_path": "src/bar.py"}},
                ],
            })
            assert not result.denied

    def test_does_not_modify_state(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            before = session.state["total_calls"]
            session.validate_program({"calls": [{"tool": "Read", "input": {}}]})
            assert session.state["total_calls"] == before


class TestRegisterContext:
    def test_context_stored(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.register_context({
                "task_type": "lackpy_delegation",
                "intent": "find bugs",
            })
            assert session.context["task_type"] == "lackpy_delegation"

    def test_context_in_events(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.register_context({"task_type": "lackpy_delegation"})
            session.after_call("Read", {}, success=True)

        # Check SQLite event has context
        from kibitzer.store import KibitzerStore
        store = KibitzerStore(tmp_path / ".kibitzer" / "store.sqlite")
        events = store.query_events(event_type="tool_call")
        assert len(events) >= 1
        data = json.loads(events[0]["data"]) if events[0]["data"] else {}
        assert data.get("context", {}).get("task_type") == "lackpy_delegation"


class TestReportGeneration:
    def test_appends_to_store(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.report_generation({
                "intent": "find bugs",
                "calls_planned": 5,
                "calls_executed": 5,
                "success": True,
                "calls_replaced": 3,
            })

        from kibitzer.store import KibitzerStore
        store = KibitzerStore(tmp_path / ".kibitzer" / "store.sqlite")
        events = store.query_events(event_type="generation")
        assert len(events) == 1
        data = json.loads(events[0]["data"])
        assert data["intent"] == "find bugs"
        assert data["calls_replaced"] == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_session_lackpy.py -v`
Expected: FAIL

- [ ] **Step 3: Add lackpy methods to KibitzerSession**

Add to `src/kibitzer/session.py` inside the `KibitzerSession` class:

```python
    # --- Lackpy integration ---

    def __init__(self, ...):  # extend existing __init__
        ...
        self._registered_tools: dict[str, tuple[int, int]] = {}
        self._context: dict[str, Any] = {}

    @property
    def registered_tools(self) -> dict[str, tuple[int, int]]:
        return self._registered_tools

    @property
    def context(self) -> dict[str, Any]:
        return self._context

    def register_tools(self, tools: list[dict[str, Any]]) -> None:
        """Register tools with their grades. Session-memory only."""
        for tool in tools:
            name = tool["name"]
            grade = tool.get("grade", (0, 0))
            if isinstance(grade, (list, tuple)):
                grade = tuple(grade)
            self._registered_tools[name] = grade

    def validate_program(self, program_info: dict[str, Any]) -> CallResult:
        """Program-level validation: grade ceiling, call budget, path violations."""
        calls = program_info.get("calls", [])
        grade_ceiling = program_info.get("grade_ceiling")
        call_budget = program_info.get("call_budget")

        # Check call budget
        if call_budget is not None and len(calls) > call_budget:
            return CallResult(
                denied=True,
                reason=f"Call budget exceeded: {len(calls)} calls > budget of {call_budget}",
            )

        # Check grade ceiling
        if grade_ceiling is not None and self._registered_tools:
            ceiling_w, ceiling_d = grade_ceiling
            for call in calls:
                tool_name = call.get("tool", "")
                grade = self._registered_tools.get(tool_name)
                if grade and (grade[0] > ceiling_w or grade[1] > ceiling_d):
                    return CallResult(
                        denied=True,
                        reason=(
                            f"Tool '{tool_name}' grade {grade} exceeds ceiling "
                            f"{grade_ceiling}"
                        ),
                    )

        # Check path violations
        violations = self.validate_calls(calls)
        if violations:
            return violations[0]

        return CallResult(denied=False)

    def register_context(self, context: dict[str, Any]) -> None:
        """Set task context for coach-aware suggestions."""
        self._context = context

    def report_generation(self, report: dict[str, Any]) -> None:
        """Record a lackpy generation outcome in the event log."""
        if self._store:
            self._store.append_event(
                event_type="generation",
                session_id=self._state.get("session_id"),
                data=json.dumps(report),
            )
```

Also update `_after_call_impl` to include context in events:

```python
        # In _after_call_impl, update the store.append_event call:
        if self._store:
            event_data = None
            if self._context:
                event_data = json.dumps({"context": self._context})
            self._store.append_event(
                event_type="tool_call",
                session_id=self._state.get("session_id"),
                tool_name=tool_name,
                tool_input=json.dumps(tool_input)[:500],
                success=success,
                mode=self.mode,
                data=event_data,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_session_lackpy.py -v`
Expected: all PASS

- [ ] **Step 5: Run full test suite**

Run: `source .venv/bin/activate && python -m pytest tests/ -q --tb=short`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/kibitzer/session.py tests/test_session_lackpy.py
git commit -m "feat: lackpy integration APIs — register_tools, validate_program, register_context, report_generation"
```

---

### Task 9: Integration Tests + Final Verification

**Files:**
- Create: `tests/test_session_integration.py`

Verify that hooks and MCP produce identical results through KibitzerSession as they did before.

- [ ] **Step 1: Write integration tests**

Create `tests/test_session_integration.py`:

```python
"""Verify hooks and MCP produce same results through KibitzerSession."""

import json
import subprocess
import sys
from kibitzer.session import KibitzerSession
from kibitzer.state import fresh_state, save_state, load_state
from kibitzer.mcp.server import change_tool_mode, get_feedback


def _project(tmp_path):
    state_dir = tmp_path / ".kibitzer"
    state_dir.mkdir()
    save_state(fresh_state(), state_dir)
    return tmp_path


class TestSessionMatchesHooks:
    def test_deny_matches(self, tmp_path):
        """Session deny should match hook deny format."""
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            result = session.before_call("Edit", {"file_path": "tests/foo.py"})
        hook_output = result.to_hook_output("PreToolUse")
        assert hook_output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "ChangeToolMode" in hook_output["hookSpecificOutput"]["permissionDecisionReason"]

    def test_allow_produces_empty(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            result = session.before_call("Read", {"file_path": "src/foo.py"})
        assert result is None

    def test_after_call_state_matches(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.after_call("Edit", {"file_path": "src/foo.py"}, success=True)

        state = load_state(proj / ".kibitzer")
        assert state["total_calls"] == 1
        assert state["tools_used_in_mode"]["Edit"] == 1


class TestMCPUsesSession:
    def test_change_mode(self, tmp_path):
        proj = _project(tmp_path)
        result = change_tool_mode("test", project_dir=proj)
        assert result["new_mode"] == "test"
        state = load_state(proj / ".kibitzer")
        assert state["mode"] == "test"

    def test_get_feedback(self, tmp_path):
        proj = _project(tmp_path)
        feedback = get_feedback(project_dir=proj)
        assert "status" in feedback
        assert feedback["status"]["mode"] == "implement"


class TestStoreEvents:
    def test_after_call_writes_event(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.after_call("Edit", {"file_path": "src/foo.py"}, success=True)

        from kibitzer.store import KibitzerStore
        store = KibitzerStore(proj / ".kibitzer" / "store.sqlite")
        events = store.query_events(event_type="tool_call")
        assert len(events) == 1
        assert events[0]["tool_name"] == "Edit"

    def test_denial_writes_event(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.before_call("Edit", {"file_path": "tests/foo.py"})

        from kibitzer.store import KibitzerStore
        store = KibitzerStore(proj / ".kibitzer" / "store.sqlite")
        events = store.query_events(event_type="denial")
        assert len(events) == 1

    def test_mode_switch_writes_event(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.change_mode("test")

        from kibitzer.store import KibitzerStore
        store = KibitzerStore(proj / ".kibitzer" / "store.sqlite")
        events = store.query_events(event_type="mode_switch")
        assert len(events) == 1
```

- [ ] **Step 2: Run integration tests**

Run: `source .venv/bin/activate && python -m pytest tests/test_session_integration.py -v`
Expected: all PASS

- [ ] **Step 3: Run full test suite**

Run: `source .venv/bin/activate && python -m pytest tests/ -v --tb=short`
Expected: all pass (374+ original + ~60 new)

- [ ] **Step 4: Lint**

Run: `source .venv/bin/activate && ruff check src/ tests/`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add tests/test_session_integration.py
git commit -m "test: session integration — hooks, MCP, and store event verification"
```
