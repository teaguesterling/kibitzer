# Architecture

## Two interfaces, one state file

```
Claude Code
    │
    ├── PreToolUse hook ──→ kibitzer-pre.sh ──→ python -m kibitzer.hooks.pre_tool_use
    │   (before every tool call)                   │
    │                                              ├── Path guard (Edit/Write/NotebookEdit)
    │                                              └── Interceptors (Bash)
    │
    ├── PostToolUse hook ──→ kibitzer-post.sh ──→ python -m kibitzer.hooks.post_tool_use
    │   (after every tool call)                    │
    │                                              ├── Counter update
    │                                              ├── Mode controller
    │                                              └── Coach
    │
    └── MCP server ──→ python -m kibitzer serve
        (agent calls explicitly)
            ├── ChangeToolMode
            └── GetFeedback
                                    ▲
                                    │
                            .kibitzer/state.json
                            (shared by all three)
```

Hooks and MCP server share state through `.kibitzer/state.json`. No IPC, no sockets, no shared memory. The hooks are separate Python processes — each invocation reads state, does its work, and writes state back. The MCP server reads the same file.

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
    │   ├── Detect patterns from state
    │   ├── Filter out already-given suggestions
    │   └── Output new suggestions as additionalContext
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
│   └── suggestions.py     should_fire(), generate_suggestions() — frequency + dedup
├── hooks/
│   ├── pre_tool_use.py    PreToolUse entry: path guard → interceptors → output
│   ├── post_tool_use.py   PostToolUse entry: counters → controller → coach → output
│   └── templates.py       Generates bash hook scripts for .claude/hooks/
├── mcp/
│   └── server.py          FastMCP server with ChangeToolMode and GetFeedback
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
