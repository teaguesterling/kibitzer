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
    namespace: str | None = None,    # scope docs, tools, and patterns by domain
)
```

**`safe_mode`**: When True, any exception inside the session is caught and logged — the session never raises. Use this for hooks where a crash would block the agent. Don't use it for lackpy where you want errors to surface.

**`namespace`**: Scope docs, tools, and failure patterns by domain. For example, lackpy's Python interpreter uses `namespace="python"` to keep its doc refs and failure history separate from other interpreters. See [Namespaces](#namespaces) below.

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
session.namespace     # current namespace (str | None)
session.doc_refs      # registered doc refs for current namespace (dict[str, str])
```

### Namespaces

Namespaces scope docs, failure patterns, and generation history by domain. Useful when multiple interpreters or tool domains share a session.

```python
# Set at session level
with KibitzerSession(namespace="python") as session:
    session.register_docs(python_doc_refs, docs_root="docs/")
    # All API calls default to namespace="python"

# Temporary switch with context manager
with session.ns("shell") as s:
    s.register_docs(shell_doc_refs, docs_root="docs/shell/")
    result = s.get_doc_context("pipe error", tool="Bash")
    # Back to "python" namespace after the block

# Explicit override on any call
hints = session.get_prompt_hints(namespace="shell")

# Cross-namespace doc lookup
refs = session.doc_refs_for("shell")
```

The `ns()` context manager restores the previous namespace even if an exception occurs. The explicit `namespace=` parameter on individual methods always takes precedence over the session default.

### Doc context pipeline

Retrieve documentation excerpts relevant to tool failures, errors, and failure modes. Uses pluckit for retrieval, with optional consumer-provided callbacks for selection and presentation.

Install with the optional dependency:

```bash
pip install kibitzer[pluckit]
```

#### register_docs

```python
session.register_docs(
    doc_refs: dict[str, str | None],  # tool name -> relative doc path
    docs_root: str | None = None,     # root dir for resolving paths
    namespace=<session default>,      # namespace to register under
    refinement: DocRefinement | None = None,  # default callbacks
)
```

Register tool documentation references. Typically called once during initialization with the tool catalog's `docs_index`.

```python
session.register_docs(
    doc_refs={"Read": "tools/read.md", "Edit": "tools/edit.md", "Bash": None},
    docs_root="/path/to/project/docs",
)
```

#### get_doc_context

```python
result = session.get_doc_context(
    query: str,                       # search query
    tool: str | None = None,          # filter to a specific tool's docs
    failure_mode: str | None = None,  # passed to callbacks as context
    namespace=<session default>,      # namespace to search
    refinement: DocRefinement | None = None,  # override callbacks
    limit: int = 5,                   # max sections to return
) -> DocResult
```

Three-step pipeline:

1. **Retrieve** — pluckit searches registered docs. If a `tool` is specified and has a doc path, searches within that file. Multi-word queries try the full phrase first, then fall back to the longest word.
2. **Select** — if a `select` callback is provided (via `refinement` or `register_docs`), it filters and reorders the candidates. Otherwise, top-N by retrieval ranking.
3. **Present** — if a `present` callback is provided, it transforms sections (e.g., summarize, reformat). Otherwise, raw content.

Returns `DocResult(sections=[])` if pluckit is not installed or no docs are registered.

```python
result = session.get_doc_context("permission denied", tool="Edit")
for section in result.sections:
    print(f"[{section.file_path}] {section.title}")
    print(section.content[:200])
```

### Types

#### DocSection

```python
@dataclass
class DocSection:
    title: str               # section heading
    content: str             # section body
    file_path: str           # source file
    level: int = 1           # heading level
    tool: str | None = None  # which tool this relates to
```

#### DocResult

```python
@dataclass
class DocResult:
    sections: list[DocSection] = field(default_factory=list)
```

#### DocRefinement

```python
@dataclass
class DocRefinement:
    select: SelectCallback | None = None   # filter/reorder candidates
    present: PresentCallback | None = None # transform for display
```

Both callbacks receive `(sections: list[DocSection], context: dict)` and return `list[DocSection]`. The context dict contains `query`, `tool`, `failure_mode`, and `namespace`. Exceptions in callbacks are swallowed — the pipeline continues with unmodified sections.

```python
from kibitzer import DocRefinement, DocSection

def select_by_relevance(sections, context):
    """Keep only sections mentioning the tool name."""
    tool = context.get("tool", "")
    return [s for s in sections if tool.lower() in s.content.lower()]

def present_as_hints(sections, context):
    """Trim sections to first paragraph."""
    for s in sections:
        s.content = s.content.split("\n\n")[0]
    return sections

refinement = DocRefinement(select=select_by_relevance, present=present_as_hints)

# Register as default for a namespace
session.register_docs(doc_refs, docs_root="docs/", refinement=refinement)

# Or pass per-query
result = session.get_doc_context("error", refinement=refinement)
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
session.report_generation(
    report: dict,                    # generation outcome
    namespace=<session default>,     # namespace to tag the event with
)
```

Feeds delegation outcomes into the SQLite event log for Riggs trust scoring and template promotion. The namespace is stored in the event data for scoped pattern analysis.

```python
session.report_generation({
    "intent": "find all type errors",
    "calls_planned": 8,
    "calls_executed": 8,
    "success": True,
    "calls_replaced": 5,
    "failure_mode": "stdlib_leak",   # from failure_modes taxonomy
    "model": "claude-sonnet-4-20250514",
})
```

#### get_failure_patterns

```python
patterns = session.get_failure_patterns(
    model: str | None = None,        # filter to a specific model
    window: int = 50,                # look at last N generation events
    namespace=<session default>,     # scope to namespace
) -> list[dict]
```

Aggregate failure modes from recent generation events. Returns dicts sorted by count descending.

```python
patterns = session.get_failure_patterns(model="claude-sonnet-4-20250514")
# [{"pattern": "stdlib_leak", "model": "claude-sonnet-4-20250514",
#   "count": 3, "last_seen": "2026-04-20T...", "sample_intent": "..."}]
```

#### get_prompt_hints

```python
hints = session.get_prompt_hints(
    model: str | None = None,        # filter to a specific model
    window: int = 50,                # look at last N generation events
    min_confidence: float = 0.3,     # minimum failure frequency to include
    namespace=<session default>,     # scope to namespace
) -> list[dict]
```

Structured prompt hints derived from observed failure patterns. Uses the shared failure mode taxonomy (`HINT_MAP`) for known modes, generates generic constraints for unknown ones.

```python
hints = session.get_prompt_hints(model="claude-sonnet-4-20250514")
# [{"type": "negative_constraint",
#   "content": "Do not use stdlib modules unless explicitly imported...",
#   "confidence": 0.6,
#   "source": "failure_pattern:stdlib_leak"}]
```

#### get_correction_hints

```python
hints = session.get_correction_hints(
    failure_mode: str,               # classified failure from taxonomy
    model: str | None = None,        # model that failed
    attempt: int = 1,                # which retry (1 = first)
    tool: str | None = None,         # tool that was misused
    namespace=<session default>,     # scope pattern lookup and doc retrieval
) -> dict
```

Return correction signal for a failed generation. This is structured data — not prompt text. The consumer (lackpy) decides how to turn it into prompt language.

```python
hints = session.get_correction_hints(
    failure_mode="stdlib_leak",
    model="claude-sonnet-4-20250514",
    attempt=2,
    tool="Read",
)
# {"failure_mode": "stdlib_leak",
#  "known": True,
#  "attempt": 2,
#  "escalation_level": 2,
#  "history": {"count": 3, "total": 10},
#  "doc_context": [                    # present when docs are registered
#      {"title": "Read", "content": "...", "file": "tools/read.md"},
#  ]}
```

The `escalation_level` is clamped to `MAX_ESCALATION` (currently 3). When `doc_context` is included, it contains sections from `get_doc_context` relevant to the failed tool or failure mode.

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

### lackpy — grade-aware validation with docs and namespaces

```python
from kibitzer import KibitzerSession, DocRefinement

with KibitzerSession(project_dir=workspace, namespace="python") as session:
    # 0. Register tools, docs, and set context
    session.register_tools(tool_registry)
    session.register_docs(
        doc_refs=interpreter.docs_index,   # {"Read": "tools/read.md", ...}
        docs_root=interpreter.docs_root,
        refinement=DocRefinement(select=interpreter.select_docs),
    )
    session.register_context({
        "task_type": "lackpy_delegation",
        "intent": intent,
        "attempt": attempt_number,
    })

    # 1. Get prompt hints from failure history
    hints = session.get_prompt_hints(model=model)

    # 2. Validate the whole program
    result = session.validate_program({
        "calls": planned_calls,
        "grade_ceiling": (2, 1),
        "call_budget": 20,
        "intent": intent,
    })
    if result.denied:
        return {"error": result.reason}

    # 3. Execute each call, recording results
    for call in execute(program):
        session.after_call(
            tool_name=call.tool,
            tool_input=call.input,
            success=call.succeeded,
        )

    # 4. Report the delegation outcome
    session.report_generation({
        "intent": intent,
        "success": all_succeeded,
        "failure_mode": classified_failure,
        "model": model,
    })

    # 5. On failure: get correction hints (includes doc context)
    if not all_succeeded:
        correction = session.get_correction_hints(
            failure_mode=classified_failure,
            model=model,
            attempt=attempt_number,
            tool=failed_tool,
        )
        # correction["doc_context"] has relevant doc sections
        # correction["escalation_level"] guides retry strategy
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
