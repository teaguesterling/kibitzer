# Python API

Use kibitzer's Python API to integrate tool-call observation and coaching into your own tools. This is the same logic that powers the Claude Code hooks and MCP server — without the stdin/stdout protocol.

## Install

```bash
pip install kibitzer
```

## Quick start

```python
from kibitzer import KibitzerSession

with KibitzerSession(project_dir=".") as session:
    # Check if a tool call is allowed
    result = session.before_call("Edit", {"file_path": "src/auth.py"})
    if result and result.denied:
        print(f"Blocked: {result.reason}")

    # Record a completed tool call
    session.after_call("Edit", {"file_path": "src/auth.py"}, success=True)

    # Get coaching suggestions
    for suggestion in session.get_suggestions():
        print(suggestion)
```

State is loaded on enter, saved on exit. If an exception occurs, state is still saved (counters preserved) and the exception propagates normally.

## Core API

### KibitzerSession

```python
KibitzerSession(
    project_dir: str | Path = ".",   # project root (where .kibitzer/ lives)
    safe_mode: bool = False,         # if True, swallow all internal errors
)
```

**`safe_mode`**: When True, any exception inside the session is caught and logged — the session never raises. Use this for hooks where a crash would block the agent. Don't use it for lackpy where you want errors to surface.

#### Context manager

```python
# Recommended: auto load + save
with KibitzerSession(project_dir=".") as session:
    ...  # state loaded, will save on exit

# Manual lifecycle (for long-lived processes like MCP servers)
session = KibitzerSession()
session.load()
# ... use session ...
session.save()
```

#### before_call

```python
result = session.before_call(
    tool_name: str,              # "Edit", "Write", "Bash", "Read", etc.
    tool_input: dict,            # {"file_path": "...", "command": "...", etc.}
) -> CallResult | None
```

Runs the pre-execution chain: path guard (for write tools) then interceptors (for Bash).

Returns `None` if the call is allowed with no comment. Returns a `CallResult` if the call is denied, has a suggestion, or should inject context.

```python
result = session.before_call("Edit", {"file_path": "tests/test_auth.py"})

if result is None:
    # Allowed, no comment
    pass
elif result.denied:
    # Blocked by path guard or redirect interceptor
    print(result.reason)
    # "Path 'tests/test_auth.py' is not writable in implement mode."
elif result.context:
    # Allowed, but with a suggestion
    print(result.context)
    # "[kibitzer] jetsam suggests: jetsam save '<description>'"
```

#### after_call

```python
result = session.after_call(
    tool_name: str,              # "Edit", "Bash", "Read", etc.
    tool_input: dict,            # same as before_call
    success: bool = True,        # did the tool call succeed?
    tool_result: Any = None,     # raw result (optional, for richer analysis)
) -> CallResult | None
```

Runs the post-execution chain: update counters, check mode transitions, run coach.

Returns `None` if nothing to report. Returns a `CallResult` if a mode transition happened or the coach has suggestions.

```python
result = session.after_call("Bash", {"command": "make test"}, success=False)

if result and result.context:
    print(result.context)
    # "[kibitzer] Mode switched to explore: Too many consecutive failures (3)"
```

**Success detection**: If you pass `tool_result`, kibitzer uses its built-in heuristics (Bash exit code, Edit error field). If you pass `success` explicitly, that takes precedence.

#### validate_calls

```python
violations = session.validate_calls(
    calls: list[dict],           # [{"tool": "Edit", "input": {...}}, ...]
) -> list[CallResult]
```

Batch validation — check multiple planned calls without executing or updating state. Returns only the violations (denied calls). Empty list means all calls are allowed.

This is designed for lackpy: validate a generated program's tool calls before execution.

```python
planned = [
    {"tool": "Read", "input": {"file_path": "src/auth.py"}},
    {"tool": "Edit", "input": {"file_path": "tests/test_auth.py"}},
    {"tool": "Edit", "input": {"file_path": "src/auth.py"}},
]

violations = session.validate_calls(planned)
for v in violations:
    print(f"{v.tool}: {v.reason}")
    # "Edit: Path 'tests/test_auth.py' is not writable in implement mode."
```

`validate_calls` is read-only — it doesn't modify state, counters, or the event log.

#### change_mode

```python
result = session.change_mode(
    mode: str,                   # "free", "implement", "test", "docs", "explore", "review"
    reason: str = "",            # optional reason for the switch
) -> dict
```

Switch modes. Returns the new mode's constraints.

```python
result = session.change_mode("test", reason="writing tests for auth module")
# {"previous_mode": "implement", "new_mode": "test",
#  "writable": ["tests/", "test/", "spec/"],
#  "strategy": "Write tests for expected behavior, not current behavior."}
```

#### get_suggestions

```python
suggestions = session.get_suggestions(
    mark_given: bool = True,     # if False, don't consume dedup budget
) -> list[str]
```

Get current coaching suggestions based on state. Each suggestion is a one-line string.

Pass `mark_given=False` when querying for display without suppressing future hook-based suggestions (e.g., for a dashboard or status check).

```python
suggestions = session.get_suggestions()
# ["You've made 7 edits without running tests.",
#  "test_auth.py has been edited 4 times. Stabilize expectations."]
```

#### get_feedback

```python
feedback = session.get_feedback(
    status: bool = True,
    suggestions: bool = True,
    intercepts: bool = True,
) -> dict
```

Combined feedback — same as the MCP `GetFeedback` tool. Returns a dict with optional sections.

#### Properties

```python
session.mode          # current mode name (str)
session.state         # raw state dict (read-only view)
session.config        # loaded config dict
session.writable      # current mode's writable paths (list[str])
```

### Tool registration and program validation (lackpy integration)

For tool-composition engines that need grade-aware enforcement and program-level validation.

#### register_tools

```python
session.register_tools([
    {"name": "Read", "grade": (0, 0)},
    {"name": "Edit", "grade": (2, 1)},
    {"name": "Bash", "grade": (4, 4)},
    {"name": "FindDefinitions", "grade": (0, 0)},
])
```

Tells kibitzer what tools exist and their grades `(w, d)` — write grade and dependency grade. Enables grade-aware enforcement: modes can specify `max_grade_w = 2` to block high-grade tools even on writable paths.

#### validate_program

```python
result = session.validate_program({
    "calls": planned_calls,
    "grade_ceiling": (2, 1),
    "call_budget": 20,
    "intent": "find and fix all type errors",
})
```

Program-level validation — checks grade ceiling, call budget, resource patterns ("15 reads in a loop — batch them"), and path violations across all calls. Wraps `validate_calls` with additional checks.

#### register_context

```python
session.register_context({
    "task_type": "lackpy_delegation",
    "intent": "find all functions matching handle_*",
    "attempt": 2,
})
```

Gives the coach task context. In a lackpy delegation, the coach knows to suppress "edit without test" (lackpy manages its own cycle). On a retry, it won't repeat suggestions from the previous attempt.

#### report_generation

```python
session.report_generation({
    "intent": "find all type errors",
    "calls_planned": 8,
    "calls_executed": 8,
    "success": True,
    "calls_replaced": 5,
})
```

Feeds delegation outcomes into the SQLite event log for Riggs trust scoring and template promotion.

### CallResult

Returned by `before_call`, `after_call`, and `validate_calls`.

```python
@dataclass
class CallResult:
    denied: bool = False         # was the call blocked?
    reason: str = ""             # why (for denials)
    context: str = ""            # additional context to inject
    tool: str = ""               # which tool this is about

    def to_hook_output(self) -> dict:
        """Convert to Claude Code hook JSON output."""
        ...
```

### Component access

For advanced use cases (Riggs, custom analysis):

```python
session.path_guard         # PathGuard — check_path(file_path, mode_policy)
session.coach              # Coach — detect_patterns(), generate_suggestions()
session.controller         # ModeController — update_counters(), check_transitions()
session.interceptors       # list[BaseInterceptor] — registered plugins
session.available_tools    # dict — discovered tools from .mcp.json
```

## Integration patterns

### lackpy — grade-aware validation and execution

```python
from kibitzer import KibitzerSession

with KibitzerSession(project_dir=workspace) as session:
    # 0. Register tools with grades and set context
    session.register_tools(tool_registry)
    session.register_context({
        "task_type": "lackpy_delegation",
        "intent": intent,
        "attempt": attempt_number,
    })

    # 1. Validate the whole program (grade ceiling, call budget, paths)
    result = session.validate_program({
        "calls": planned_calls,
        "grade_ceiling": (2, 1),
        "call_budget": 20,
        "intent": intent,
    })
    if result.denied:
        return {"error": result.reason}

    # 2. Execute each call, recording results
    for call in execute(program):
        session.after_call(
            tool_name=call.tool,
            tool_input=call.input,
            success=call.succeeded,
        )

    # 3. Report the delegation outcome
    session.report_generation({
        "intent": intent,
        "calls_planned": len(planned_calls),
        "calls_executed": len(executed),
        "success": all_succeeded,
        "calls_replaced": estimated_manual_calls,
    })

    # 4. Get coaching feedback for next generation
    suggestions = session.get_suggestions()
    if suggestions:
        feedback.extend(suggestions)
```

### Hooks — stateless per invocation

```python
from kibitzer import KibitzerSession

def main():
    hook_input = json.loads(sys.stdin.read())

    with KibitzerSession(safe_mode=True) as session:
        result = session.before_call(
            hook_input["tool_name"],
            hook_input.get("tool_input", {}),
        )

    if result is not None:
        print(json.dumps(result.to_hook_output()))
```

### MCP server — long-lived session

```python
from kibitzer import KibitzerSession

session = KibitzerSession()
session.load()

@mcp.tool()
def ChangeToolMode(mode: str, reason: str = ""):
    result = session.change_mode(mode, reason)
    session.save()
    return json.dumps(result)

@mcp.tool()
def GetFeedback(status=True, suggestions=True, intercepts=True):
    return json.dumps(session.get_feedback(status, suggestions, intercepts))
```

### Custom analysis — component access

```python
from kibitzer import KibitzerSession

with KibitzerSession(project_dir=".") as session:
    # Direct pattern detection
    patterns = session.coach.detect_patterns(session.state)

    # Check a specific path
    result = session.path_guard.check("src/main.py", session.config)

    # Read intercept log
    feedback = session.get_feedback(status=False, suggestions=False, intercepts=True)
    for entry in feedback["intercepts"]["recent"]:
        print(f"{entry['plugin']}: {entry['bash_command']} -> {entry['suggested_tool']}")
```

## Event log (SQLite)

`KibitzerSession` appends events to `.kibitzer/store.sqlite` on `after_call()` and `save()`. The schema is append-only:

```sql
CREATE TABLE events (
    id INTEGER PRIMARY KEY,
    timestamp TEXT DEFAULT (datetime('now')),
    session_id TEXT,
    event_type TEXT,        -- 'tool_call', 'mode_switch', 'suggestion', 'error'
    tool_name TEXT,
    tool_input TEXT,        -- JSON
    success INTEGER,
    mode TEXT,
    data TEXT               -- JSON, event-specific payload
);
```

Other tools (Riggs, fledgling) can read this via DuckDB:

```sql
ATTACH '.kibitzer/store.sqlite' AS kibitzer (TYPE sqlite);
SELECT * FROM kibitzer.events WHERE event_type = 'tool_call' ORDER BY timestamp DESC;
```

## Configuration

The Python API reads the same `.kibitzer/config.toml` as the hooks. See [Configuration](configuration.md) for the full reference.

## Error handling

- **`safe_mode=False` (default)**: Exceptions propagate normally. Use for lackpy and tools that want to handle errors.
- **`safe_mode=True`**: All internal errors are swallowed. `before_call` returns None (allow), `after_call` returns None (no feedback). Use for hooks where a crash would block the agent.

In both modes, the context manager tries to save state on exit. A save failure during an exception doesn't mask the original error.
