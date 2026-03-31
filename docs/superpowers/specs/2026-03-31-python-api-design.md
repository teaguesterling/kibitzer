# Kibitzer Python API Design

## Goal

Extract kibitzer's core logic from hook entry points into a reusable `KibitzerSession` class. Hooks, MCP server, and external tools (lackpy, Riggs) all consume the same API. Add a SQLite event log for cross-session queryability.

## Architecture

### Before (v0.2.1)

```
hooks/pre_tool_use.py  → reads config, loads state, runs path guard + interceptors, writes state
hooks/post_tool_use.py → reads config, loads state, runs counters + controller + coach, writes state
mcp/server.py          → reads config, loads state, runs change_mode/get_feedback, writes state
```

Three separate entry points, each duplicating the load/process/save lifecycle. External tools can't reuse the logic without importing hook internals.

### After

```
session.py             → KibitzerSession: load, before_call, after_call, validate_calls, save
store.py               → SQLite event log: append, query
hooks/pre_tool_use.py  → thin wrapper: create session, call before_call, print result
hooks/post_tool_use.py → thin wrapper: create session, call after_call, print result
mcp/server.py          → creates session at start, delegates to session methods
```

One class owns the lifecycle. All entry points are thin wrappers.

## KibitzerSession

### Lifecycle

```python
# Context manager (recommended)
with KibitzerSession(project_dir=".") as session:
    result = session.before_call("Edit", {"file_path": "src/foo.py"})
    # state loaded on __enter__, saved on __exit__

# Manual lifecycle (for long-lived processes)
session = KibitzerSession(project_dir=".")
session.load()
# ... use ...
session.save()
```

`__enter__` calls `load()`. `__exit__` calls `save()`, handling errors:
- If the body raised: record the error in state, try to save, let the original exception propagate
- If save fails during an exception: log save failure, don't mask the original
- If save fails without an exception: raise the save error

### safe_mode

```python
with KibitzerSession(safe_mode=True) as session:
    result = session.before_call(...)  # never raises
```

When `safe_mode=True`:
- `before_call` returns None on any internal error (allow the tool call)
- `after_call` returns None on any internal error (no feedback)
- `save()` errors are swallowed
- Designed for hooks where a crash would block the agent

### Methods

#### before_call(tool_name, tool_input) → CallResult | None

Runs the pre-execution chain:
1. Path guard: check file_path against mode's writable prefixes (for Edit/Write/NotebookEdit)
2. Interceptors: check Bash command against plugin patterns

Returns None (allow silently), or a CallResult with denial or suggestion.

Does NOT update counters or state — this is a pre-check only.

#### after_call(tool_name, tool_input, success, tool_result) → CallResult | None

Runs the post-execution chain:
1. Update counters (total_calls, tools_used, failures, coach counters)
2. Check mode transitions (consecutive failures → explore)
3. Run coach if frequency fires (detect patterns, generate suggestions)
4. Append event to SQLite store

Returns None (nothing to report), or a CallResult with mode transition or coaching context.

If `tool_result` is provided and `success` is not explicitly set, uses built-in heuristics (Bash exit code, Edit error field).

#### validate_calls(calls) → list[CallResult]

Batch pre-validation. Checks each call against the path guard without updating state. Returns only violations (denied calls). Empty list = all allowed.

Read-only — does not modify counters, state, or event log.

Designed for lackpy: validate a generated program's planned tool calls before execution.

#### change_mode(mode, reason) → dict

Switch mode. Updates state, resets mode-level counters. Returns new mode constraints.

#### get_suggestions(mark_given=True) → list[str]

Run coach pattern detection and return new suggestions. Pass `mark_given=False` to query without consuming dedup budget (for dashboards/status).

#### get_feedback(status, suggestions, intercepts) → dict

Combined feedback — same as MCP GetFeedback tool.

### Properties

```python
session.mode              # str: current mode name
session.state             # dict: raw state (read-only view)
session.config            # dict: loaded config
session.writable          # list[str]: current mode's writable paths
session.path_guard        # PathGuard instance
session.coach             # access to observer + suggestions modules
session.controller        # ModeController instance
session.interceptors      # list[BaseInterceptor]: active plugins
session.available_tools   # dict: discovered tools from .mcp.json
```

### CallResult

```python
@dataclass
class CallResult:
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

## Lackpy integration APIs

Four additional methods on KibitzerSession for tool-composition engines like lackpy. These go beyond the core before_call/after_call pattern to support program-level validation and grade-aware enforcement.

### register_tools(tools)

**Priority: HIGH.** Tell kibitzer what tools exist and their grades.

```python
session.register_tools([
    {"name": "Read", "grade": Grade(w=0, d=0)},
    {"name": "Edit", "grade": Grade(w=2, d=1)},
    {"name": "Bash", "grade": Grade(w=4, d=4)},
    {"name": "FindDefinitions", "grade": Grade(w=0, d=0)},
])
```

Without this, kibitzer can only check tool names against hardcoded lists. With it, the path guard and coach become grade-aware:

- Mode policies can specify a grade ceiling: `max_grade_w = 2` means Edit is fine, Bash is blocked
- The coach can suggest lower-grade alternatives: "Bash(grep) is grade 4 — FindDefinitions is grade 0"
- The interceptor ratchet has a formal basis: observe tools above the grade ceiling, suggest tools below it

**Grade model:** Kibitzer understands grades natively as `(w, d)` tuples — write grade and dependency grade. This is more general than path prefixes. A mode has both: writable paths AND a grade ceiling. Both must pass for a call to be allowed.

```toml
[modes.implement]
writable = ["src/", "lib/"]
max_grade_w = 3          # structured writes only, no arbitrary bash
max_grade_d = 2          # no network, no external deps
```

Registered tools are stored in session memory (not persisted — they're declared per-session by the caller).

### validate_program(program_info)

**Priority: MEDIUM.** Program-level validation beyond per-call checks.

```python
result = session.validate_program({
    "calls": [...],                    # planned tool calls
    "grade_ceiling": Grade(w=2, d=1),  # max allowed grade
    "call_budget": 20,                 # max total calls
    "intent": "find and fix all type errors",
})
```

Returns program-level violations:
- Grade ceiling exceeded by any call
- Call budget exceeded
- Resource patterns ("15 read calls in a loop — consider batching")
- Path violations across all calls

This wraps `validate_calls` with additional program-level checks. Lackpy calls this before execution; `validate_calls` stays for simpler per-call checking.

### register_context(context)

**Priority: MEDIUM.** Give the coach task context so suggestions are relevant.

```python
session.register_context({
    "task_type": "lackpy_delegation",  # or "manual", "retry", "review"
    "intent": "find all functions matching handle_*",
    "attempt": 2,                       # retry number
    "parent_session": "abc123",         # if delegated from another session
})
```

Without context, the coach sees individual tool calls with no narrative. With it:
- In a lackpy delegation, suppress "you've made 5 edits without testing" (lackpy manages its own test cycle)
- On a retry, the coach knows not to repeat suggestions from the previous attempt
- From a parent session, the coach inherits relevant state

Context is stored in session memory and included in SQLite events for Riggs analysis.

### report_generation(report)

**Priority: LOW.** Feed outcomes into the event log for cross-session analysis.

```python
session.report_generation({
    "intent": "find all type errors",
    "program_hash": "abc123",
    "calls_planned": 8,
    "calls_executed": 8,
    "success": True,
    "calls_replaced": 5,          # manual calls this replaced
    "template_used": "search_and_fix",
})
```

Appended to the SQLite event log as `event_type="generation"`. Riggs reads these for:
- Trust scoring (successful delegations improve trust)
- Template promotion (which generated programs succeed reliably)
- Ratchet velocity (how many manual calls are being replaced by delegations)

Can wait until Riggs integration is built.

## SQLite event log

### Why

`state.json` is the hot path — fast counters read/written every hook call. But it can't answer "what happened 3 sessions ago" or "how many times has this agent hit the path guard across all sessions." The SQLite store provides queryable history alongside the fast JSON state.

### Schema

```sql
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
```

Event types: `tool_call`, `mode_switch`, `suggestion_fired`, `denial`, `interception`, `error`.

### store.py

```python
class KibitzerStore:
    def __init__(self, store_path: Path):
        self.path = store_path

    def append_event(self, event_type: str, **kwargs):
        """Open connection, insert one row, close."""
        ...

    def query_events(self, event_type: str = None, session_id: str = None, limit: int = 100) -> list[dict]:
        """Open connection, query, close, return dicts."""
        ...
```

Open-write-close pattern. No persistent connection. SQLite handles the file locking.

### Cross-tool access

Riggs and fledgling read the store via DuckDB ATTACH:

```sql
ATTACH '.kibitzer/store.sqlite' AS kibitzer (TYPE sqlite);
SELECT event_type, count(*) FROM kibitzer.events GROUP BY event_type;
```

Kibitzer never needs DuckDB as a dependency. The SQLite store is readable by any tool.

## File changes

### New files

| File | Purpose |
|---|---|
| `src/kibitzer/session.py` | KibitzerSession class, CallResult dataclass |
| `src/kibitzer/store.py` | KibitzerStore — SQLite event log |

### Simplified files

| File | Change |
|---|---|
| `hooks/pre_tool_use.py` | Thin wrapper: `with KibitzerSession(safe_mode=True)` → `before_call` → print |
| `hooks/post_tool_use.py` | Thin wrapper: `with KibitzerSession(safe_mode=True)` → `after_call` → print |
| `mcp/server.py` | Creates session at start, delegates to session methods |

### Unchanged files

Config, state, path_guard, interceptors, coach, controller, tools, fledgling, templates, cli — all unchanged. `KibitzerSession` imports and calls them.

## State management

Two stores, different purposes:

| Store | Format | Access pattern | Contents |
|---|---|---|---|
| `.kibitzer/state.json` | JSON | Read-modify-write every hook call | Mode, counters, suggestions_given, coach observation counters |
| `.kibitzer/store.sqlite` | SQLite | Append on after_call, query on get_feedback | Event log, tool call history, denials, mode switches |

`state.json` is the hot path. `store.sqlite` is the analytical store. They don't duplicate data — state.json has current counters, SQLite has the full history.

## Persistence

| Caller | state.json | store.sqlite |
|---|---|---|
| Hook (pre) | Read only | Not touched |
| Hook (post) | Read + write | Append event |
| MCP GetFeedback | Read only | Read only |
| MCP ChangeToolMode | Read + write | Append event |
| lackpy validate_calls | Read only | Not touched |
| lackpy after_call | Read + write | Append event |
| Riggs | Not touched | Read via DuckDB ATTACH |

## Error handling

### safe_mode=False (default)

For lackpy and tools that want errors to surface:

```python
with KibitzerSession() as session:
    result = session.before_call(...)  # may raise on config/state issues
```

### safe_mode=True

For hooks where a crash blocks the agent:

```python
with KibitzerSession(safe_mode=True) as session:
    result = session.before_call(...)  # returns None on any internal error
```

Implementation: wraps each public method in try/except. Logs errors to store.sqlite if possible, swallows them otherwise.

### Context manager error handling

```python
def __exit__(self, exc_type, exc_val, exc_tb):
    if exc_type is not None:
        self._record_error(exc_type, exc_val)
    try:
        self.save()
    except Exception:
        if exc_type is None:
            raise  # save failure is the only error
        pass  # don't mask original exception
    return False  # never suppress exceptions
```

## Testing strategy

### Unit tests for KibitzerSession

- `test_session_lifecycle` — load/save/context manager
- `test_before_call_*` — all path guard and interceptor scenarios (reuse existing test cases)
- `test_after_call_*` — all counter, transition, and coach scenarios
- `test_validate_calls` — batch validation, read-only guarantee
- `test_safe_mode` — errors swallowed, returns None
- `test_context_manager_error_handling` — save on exception, don't mask

### Unit tests for KibitzerStore

- `test_append_and_query` — round-trip
- `test_concurrent_appends` — two stores appending to same file
- `test_corrupt_store` — graceful degradation on bad SQLite file

### Integration tests

- `test_hook_uses_session` — verify hooks produce same output via session as before
- `test_lackpy_pattern` — validate_calls → execute → after_call → get_suggestions
- `test_store_readable_from_duckdb` — write events, ATTACH from DuckDB, query

### Migration

Existing tests for hooks, MCP, coach, etc. should pass with minimal changes — the logic doesn't change, just the entry point. Hook tests that call `handle_pre_tool_use()` directly can continue to work (the function delegates to session internally).

## What doesn't change

- Config format and loading
- State schema and counters
- Path guard logic
- Interceptor plugin system
- Coach pattern detection
- Mode controller transitions
- Fledgling integration
- Tool discovery
- CLI (init, serve)
- Hook bash script templates
