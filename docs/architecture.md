# Architecture

## KibitzerSession — the core

All of kibitzer's logic lives in `KibitzerSession`. Hooks, MCP server, and external tools are thin wrappers.

```
                        KibitzerSession
                    ┌───────────────────────┐
                    │  before_call()        │
                    │  after_call()         │
                    │  validate_calls()     │
                    │  change_mode()        │
                    │  get_suggestions()    │
                    │  get_feedback()       │
                    │  register_tools()     │
                    │  validate_program()   │
                    │  register_docs()      │
                    │  get_doc_context()    │
                    │  get_prompt_hints()   │
                    │  get_correction_hints()│
                    └───────────┬───────────┘
                                │
           ┌────────────────────┼────────────────────┐
           │                    │                     │
    Claude Code hooks      MCP server           Python API
    (thin wrappers)        (thin wrapper)       (direct import)
           │                    │                     │
    kibitzer-pre.sh        kibitzer serve      from kibitzer import
    kibitzer-post.sh       ChangeToolMode        KibitzerSession
                           GetFeedback
                                │
                    ┌───────────┴───────────┐
                    │                       │
              .kibitzer/              .kibitzer/
              state.json              store.sqlite
              (hot counters)          (event log)
```

Two persistence stores:

| Store | Format | Purpose |
|---|---|---|
| `state.json` | JSON | Hot counters — mode, failures, turns, suggestions given. Read/written every hook call. |
| `store.sqlite` | SQLite | Event log — tool calls, denials, mode switches, errors. Append-only, queryable by Riggs via DuckDB ATTACH. |

Hooks and MCP server share state through `KibitzerSession`. Each hook invocation creates a session (`with KibitzerSession(safe_mode=True)`), does its work, and saves on exit. The MCP server holds a longer-lived session. External tools like lackpy import `KibitzerSession` directly.

## Hook protocol

Hooks are bash scripts in `.claude/hooks/` that pipe stdin to Python. Claude Code sends JSON on stdin and reads JSON from stdout.

**PreToolUse** receives:
```json
{
  "session_id": "...",
  "tool_name": "Edit",
  "tool_input": {"file_path": "src/foo.py", "old_string": "...", "new_string": "..."},
  "tool_use_id": "..."
}
```

And outputs one of:
- **Nothing** (exit 0, empty stdout) — allow the tool call
- **Deny** — block the tool call, send reason to agent:
  ```json
  {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "..."}}
  ```
- **Suggest** — allow the tool call, inject context:
  ```json
  {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "[kibitzer] ..."}}
  ```

**PostToolUse** receives the same fields plus `tool_result` (Bash: `{exitCode, stdout, stderr}`, Edit: string or `{error: "..."}`, etc.). It outputs nothing (allow) or context:
```json
{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "[kibitzer] Mode switched to debug: ..."}}
```

## PreToolUse chain

```
stdin JSON
    │
    ├── tool_name in {Edit, Write, NotebookEdit}?
    │   └── Path guard: check file_path against mode's writable prefixes
    │       ├── allowed → continue
    │       └── denied → output deny JSON, exit
    │
    └── tool_name == Bash?
        └── Run each interceptor plugin against command string
            ├── no match → exit 0 (allow)
            └── match found → check plugin mode:
                ├── observe → log to intercept.log, exit 0
                ├── suggest → output additionalContext, exit 0
                └── redirect → output deny JSON, exit 0
```

## PostToolUse chain

```
stdin JSON
    │
    ├── 1. Update counters
    │   ├── total_calls, turns_in_mode, tools_used_in_mode
    │   ├── success_count or failure_count + consecutive_failures
    │   └── Coach counters: edit failures, reads, edits_since_test, last_edit_turn
    │
    ├── 2. Mode controller
    │   ├── Check transition rules (consecutive failures → debug, etc.)
    │   ├── Oscillation guard (don't switch back too quickly)
    │   └── Apply transition if triggered (reset mode-level counters)
    │
    ├── 3. Coach (every N calls)
    │   ├── Discover available tools from .mcp.json
    │   ├── Detect patterns from state (mode-aware)
    │   ├── If fledgling available: query conversation analytics for richer patterns
    │   ├── Filter out already-given suggestions (dedup)
    │   └── Output new suggestions as additionalContext (referencing only available tools)
    │
    └── Save state → .kibitzer/state.json
```

## MCP server

The MCP server runs as a persistent process (via `kibitzer serve`). It provides two tools:

**`ChangeToolMode(mode, reason?)`** — Validates mode exists, updates state, resets counters, returns new mode info. This is how the agent explicitly switches modes (vs. auto-transitions from the mode controller).

**`GetFeedback(status?, suggestions?, intercepts?)`** — Returns a combined response with current status, coaching suggestions, and/or the intercept log. All params default true. Reads state.json and intercept.log.

## File layout

```
src/kibitzer/
├── session.py             KibitzerSession + CallResult — the Python API
├── docs.py                DocSection, DocResult, DocRefinement — doc pipeline types
├── failure_modes.py       Shared failure mode taxonomy (7 modes + hint map)
├── store.py               KibitzerStore — SQLite event log (append, query)
├── config.py              Loads config.toml (defaults + project-local merge)
├── state.py               Reads/writes .kibitzer/state.json
├── guards/
│   └── path_guard.py      check_path(file_path, mode_policy) → PathGuardResult
├── interceptors/
│   ├── base.py            InterceptMode enum, Suggestion dataclass, BaseInterceptor
│   ├── registry.py        build_registry() — loads plugins for installed tools
│   ├── blq.py             Test/build command interception
│   ├── jetsam.py          Git command interception
│   └── fledgling.py       Search/navigation interception
├── controller/
│   └── mode_controller.py update_counters(), check_transitions(), apply_transition()
├── coach/
│   ├── observer.py        detect_patterns(state) — mode-aware pattern detection
│   ├── suggestions.py     should_fire(), generate_suggestions() — frequency + dedup
│   ├── fledgling.py       Query fledgling for conversation analytics (Python API + CLI fallback)
│   └── tools.py           Discover available tools from .mcp.json for tailored suggestions
├── hooks/
│   ├── pre_tool_use.py    Thin wrapper: KibitzerSession → before_call → hook output
│   ├── post_tool_use.py   Thin wrapper: KibitzerSession → after_call → hook output
│   └── templates.py       Generates bash hook scripts for .claude/hooks/
├── mcp/
│   └── server.py          Thin wrapper: delegates to KibitzerSession
├── cli.py                 Click CLI: init, serve
└── config.toml            Default configuration
```

## Grade

Kibitzer is level 1 — specified mutations over structured data:

- Path guard: reads mode (JSON), checks path against a prefix list, allows or blocks
- Mode controller: reads counters, compares to thresholds, updates mode
- Coach: reads counters, detects patterns, formats suggestion strings
- Interceptors: matches bash commands against string patterns, returns suggestions

No computation channels. No trained judgment. Every decision is traceable to a specified rule in config.toml.
