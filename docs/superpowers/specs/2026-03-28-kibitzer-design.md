# Kibitzer Design Spec

*A Claude Code extension that watches agent tool calls and suggests structured alternatives.*

A kibitzer is the person watching your chess game who can't help offering opinions. This extension watches an agent's tool calls and offers structured alternatives — "you just ran `git add && git commit` through bash; `jetsam save` does that with plan tracking and confirmation."

Kibitzer coordinates three existing tools:
- **Fledgling** (code intelligence, level 0 — read-only DuckDB queries over ASTs and conversation logs)
- **blq** (build/test capture, level 1-2 — structured event capture with optional sandbox enforcement)
- **jetsam** (git workflow, level 1-2 — atomic saves, syncs, plans, mode state)

Kibitzer itself is level 1 — specified rules over structured data. No LLM in the decision loop. Every decision traces to config.toml.

---

## Architecture

Kibitzer has two interfaces:

1. **Passive (hooks)** — PreToolUse and PostToolUse hooks that fire automatically on every tool call. The agent doesn't invoke these; they observe and intercept.
2. **Active (MCP server)** — Two MCP tools the agent can call explicitly: `ChangeToolMode` and `GetFeedback`.

Both interfaces share state through `.kibitzer/state.json` — a plain JSON file. No IPC, no sockets, no shared memory.

### File Structure

```
kibitzer/
├── __init__.py
├── cli.py                     # `kibitzer init` / `kibitzer serve`
├── config.py                  # Load config.toml, merge defaults with project-local
├── state.py                   # Read/write .kibitzer/state.json
├── mcp/
│   └── server.py              # FastMCP: ChangeToolMode, GetFeedback
├── guards/
│   └── path_guard.py          # Mode-based path protection for Edit/Write/NotebookEdit
├── interceptors/
│   ├── base.py                # BaseInterceptor, InterceptMode enum, Suggestion dataclass
│   ├── blq.py                 # Build/test commands -> blq suggestions
│   ├── jetsam.py              # Git commands -> jetsam suggestions
│   └── fledgling.py           # Search/nav commands -> fledgling suggestions
├── coach/
│   ├── observer.py            # Pattern detection from state
│   └── suggestions.py         # Suggestion generation + dedup
├── controller/
│   └── mode_controller.py     # Failure counters, transition rules, oscillation guard
├── hooks/
│   ├── pre_tool_use.py        # PreToolUse entry point (path guard + interceptors)
│   ├── post_tool_use.py       # PostToolUse entry point (controller + coach)
│   └── templates.py           # Generate bash hook scripts for .claude/hooks/
└── config.toml                # Default configuration
```

---

## CLI & Lifecycle

**Entry point:** `python -m kibitzer` or `kibitzer` (if pip-installed)

### `kibitzer init [--hooks] [--mcp] [--no-mcp]`

Creates project-local configuration and registers hooks:

```
.kibitzer/
├── config.toml          # Project-local overrides (copy of defaults to edit)
└── state.json           # Runtime state (mode, counters)

.claude/hooks/
├── kibitzer-pre.sh      # PreToolUse hook
└── kibitzer-post.sh     # PostToolUse hook

.claude/settings.json    # Hooks registered (merged, not overwritten)
.mcp.json                # MCP server entry (if --mcp)
```

Hook scripts are bash, following blq/jetsam's pattern. They read stdin, pipe it to the Python module, and forward the output.

Init detects existing hooks from blq/jetsam/superpowers and merges without clobbering.

### `kibitzer serve [--transport stdio|sse]`

Runs the FastMCP server. Transport defaults to `stdio` for Claude Code integration.

---

## Configuration

### `config.toml`

```toml
[modes.free]
writable = ["*"]
strategy = ""

[modes.create]
writable = ["*"]
strategy = "Scaffold structure before filling in details."

[modes.implement]
writable = ["src/", "lib/"]
strategy = ""

[modes.test_dev]
writable = ["tests/", "test/", "spec/"]
strategy = "Write tests for expected behavior, not current behavior."

[modes.document]
writable = ["docs/", "README.md", "CHANGELOG.md"]
strategy = "Explain the why, not the what."

[modes.debug]
writable = []
strategy = "Identify all failures before proposing fixes."

[modes.review]
writable = []
strategy = "Read everything before forming an opinion."

[controller]
default_mode = "implement"
max_consecutive_failures = 3
max_turns_in_debug = 20
auto_review_on_tests_passing = true

[coach]
frequency = 5
enabled = true

[coach.model_overrides]
haiku = { frequency = 3 }
sonnet = { frequency = 5 }
opus = { frequency = 10 }

[plugins.blq]
mode = "observe"
enabled = true

[plugins.jetsam]
mode = "observe"
enabled = true

[plugins.fledgling]
mode = "observe"
enabled = true
```

**Mode writable paths** are prefix-matched. `"src/"` matches `src/foo/bar.py`. `["*"]` means unrestricted. `[]` means read-only.

**All plugins start in `observe`** — log alternatives but don't interrupt. Users graduate to `suggest` then `redirect` by editing one line.

**Coach frequency is per-model** — Haiku gets more coaching (needs it), Opus gets less (over-analyzes).

### `state.json` (runtime, not user-edited)

```json
{
  "mode": "implement",
  "previous_mode": "debug",
  "failure_count": 2,
  "success_count": 11,
  "consecutive_failures": 0,
  "turns_in_mode": 14,
  "turns_in_previous_mode": 3,
  "total_calls": 47,
  "mode_switches": 4,
  "tools_used_in_mode": {
    "Edit": 6,
    "Read": 3,
    "Bash": 2
  },
  "suggestions_given": ["edit_without_test", "high_failure_ratio"],
  "model": null,
  "session_id": null
}
```

| Field | Purpose |
|-------|---------|
| `failure_count` / `success_count` | Failure ratio — 2/13 is fine, 2/3 is not |
| `consecutive_failures` | Streak trigger — 3 in a row -> debug mode |
| `previous_mode` / `turns_in_previous_mode` | Oscillation detection — don't switch back to a mode we just left |
| `mode_switches` | Global oscillation — 6+ switches suggests thresholds are wrong |
| `tools_used_in_mode` | Coach context — "6 edits, 0 test runs" |
| `suggestions_given` | Dedup — don't repeat the same suggestion in a session |

---

## MCP Server — Two Tools

### `ChangeToolMode(mode, reason?)`

```
Input:  {"mode": "debug", "reason": "3 consecutive test failures"}
Output: {"previous_mode": "implement", "new_mode": "debug",
         "writable": [], "strategy": "Identify all failures before proposing fixes."}
```

- Validates mode exists in config
- Updates `state.json` (resets counters, records previous mode)
- Returns the new mode's constraints immediately

### `GetFeedback(status?, suggestions?, intercepts?)`

All params default true. Returns a combined response shaped by the flags:

```
Input:  {"suggestions": true, "intercepts": true, "status": true}
Output: {
  "status": {
    "mode": "implement",
    "failure_count": 2,
    "success_count": 11,
    "consecutive_failures": 0,
    "turns_in_mode": 14,
    "total_calls": 47,
    "writable": ["src/", "lib/"]
  },
  "suggestions": [
    "You've edited 5 files without running tests.",
    "FindDefinitions is available - you've grepped for 'def handle_request' 3 times."
  ],
  "intercepts": {
    "total_observed": 12,
    "recent": [
      {"bash": "git add -A && git commit ...", "alternative": "jetsam save", "plugin": "jetsam"}
    ]
  }
}
```

The MCP server reads `state.json` and `.kibitzer/intercept.log` — same files the hooks write.

---

## Hook Logic

### PreToolUse Chain

1. **Path guard** — fires on `Edit`, `Write`, `NotebookEdit`. Reads mode from `state.json`, checks file path against writable prefixes. If denied: returns `permissionDecision: "deny"` with reason including how to switch modes via the `ChangeToolMode` MCP tool. Does NOT attempt to parse Bash commands for write paths — that's blq's sandbox enforcement domain.

2. **Interceptors** — fires on `Bash` only. Runs each enabled plugin's pattern match against the command string. Based on plugin mode:
   - `observe`: log to `.kibitzer/intercept.log`, allow silently
   - `suggest`: allow, inject `additionalContext` with the alternative
   - `redirect`: deny with the alternative as the reason

First matching plugin wins. No match = exit 0 (allow).

### PostToolUse Chain

1. **Counter update** — increment `total_calls`, `turns_in_mode`, `tools_used_in_mode[tool_name]`. Track success/failure: for Bash, check exit code; for Edit/Write, check error field. Update `consecutive_failures` (reset on success).

2. **Mode controller** — check transition rules with oscillation guard:
   ```
   if consecutive_failures > max_consecutive_failures
     AND should_transition(state, "debug"):
       -> debug

   if turns_in_mode > max_turns_in_debug AND mode == "debug"
     AND should_transition(state, "implement"):
       -> implement

   if test_passed AND mode == "implement" AND auto_review_on_tests_passing
     AND should_transition(state, "review"):
       -> review
   ```

   **Oscillation guard:** Don't auto-switch to a mode if `previous_mode == target` and `turns_in_previous_mode < 5`. Stop all auto-transitions if `mode_switches > 6`.

3. **Coach** — fires every `frequency` calls. One-line suggestions, never repeated in a session.

---

## Interceptor Plugins

Each plugin declares trigger patterns and returns a `Suggestion` or `None`.

### Plugin Registry

| Plugin | Triggers | Suggests |
|--------|----------|----------|
| `BlqInterceptor` | `pytest`, `npm test`, `cargo test`, `go test`, `make test` | `blq run test` — structured capture, queryable errors |
| `JetsamInterceptor` | `git add && git commit`, `git push`, `git diff`, `git log`, `git stash` | `jetsam save`, `jetsam sync`, `jetsam diff`, etc. |
| `FledglingInterceptor` | `grep -r` + def/class/function keywords, `find . -name`, repeated `cat` | `FindDefinitions`, `CodeStructure`, `ReadLines` |

### Availability

Each plugin checks `shutil.which(tool_name)` at load time. Missing tool = plugin not registered. Kibitzer still works for path guarding, mode control, and coaching without any of the three tools.

### Interception Modes — The Ratchet

```
OBSERVE -> SUGGEST -> REDIRECT
```

Start in observe (gather data). Review logs. Graduate to suggest (agent sees alternatives). Confirm agent follows. Graduate to redirect (bash denied, must use structured tool). Each graduation is a config change, not a code change.

---

## Coach

Fires every N calls (model-dependent). Works from `state.json` alone; richer with fledgling.

### Pattern Detections (v1)

| Pattern | Detection | Suggestion |
|---------|-----------|------------|
| Edit streak without tests | `tools_used_in_mode["Edit"] > 3`, no test runs | "Consider running tests to verify your changes." |
| Mode mismatch | Mode is `debug` but Edit calls observed | "You're editing in debug mode. Switch to implement?" |
| High failure ratio | `failure / (failure + success) > 0.5`, `total > 5` | "High failure rate. Consider reading before editing." |
| Oscillation | `mode_switches > 4` | "Frequent mode switches. Consider free mode." |
| Superpowers hint | Plan file exists, mode doesn't match phase | "A plan exists — current step may inform mode." |

### With Fledgling

| Pattern | Query | Suggestion |
|---------|-------|------------|
| Repeated searches | `ChatToolUsage` grouped by pattern, count >= 3 | "Try FindDefinitions instead of repeated grep." |
| Tool underuse | Kit tools not called | "CodeStructure is available for navigation." |

### Coach Principles

- One line per suggestion
- Never repeated in a session (tracked in `suggestions_given`)
- No LLM calls
- No enforcement — `additionalContext` only
- No duplicating superpowers skill reminders

---

## Superpowers Integration

Superpowers manages **workflow phases** (brainstorm -> plan -> implement -> review). Kibitzer manages **tool constraints** (what can be written where, what bash has structured alternatives). Complementary, not competing.

### Integration Points

| Concern | Superpowers owns | Kibitzer's role |
|---------|-----------------|----------------|
| Workflow phases | Skills define progression | Observe active skill, suggest matching mode |
| Task tracking | TodoWrite / TaskCreate | Read task state for coaching context |
| TDD discipline | test-driven-development skill | Path guard enforces mechanically |
| Code review | requesting-code-review skill | Coach can suggest invoking the skill |
| Git worktrees | using-git-worktrees skill | Detect worktree context, apply config |
| Plan files | `docs/superpowers/plans/` | Read for coaching ("you're on step 3 of 7") |
| Verification | verification-before-completion | Coach reminds after edit streaks |

### What Kibitzer Does NOT Do

- Duplicate superpowers' "design before code" gate
- Duplicate superpowers' "verify before claiming done" gate
- Manage its own task/todo system
- Dispatch subagents (superpowers does this)

---

## Graceful Degradation

| Tool available | What works |
|----------------|-----------|
| None | Path guard, mode controller, basic coach (state-only patterns) |
| blq only | + BlqInterceptor suggestions |
| jetsam only | + JetsamInterceptor suggestions |
| fledgling only | + FledglingInterceptor suggestions, richer coaching queries |
| superpowers | + Skill-aware mode suggestions, plan-aware coaching |
| All | Full feature set |

---

## Testing

Test against synthetic scenarios:

1. **Path guard**: mode=implement, Edit tests/foo.py -> deny with mode switch instruction
2. **Path guard**: mode=free, Edit anything -> allow
3. **Mode switch**: 4 consecutive failures -> auto-switch to debug
4. **Oscillation**: rapid debug<->implement switching -> guard stops transitions
5. **Interceptor observe**: `git add && git commit` -> logged, not blocked
6. **Interceptor suggest**: same command -> allowed with jetsam suggestion in context
7. **Interceptor redirect**: same command -> denied, must use jetsam
8. **Coach**: 4 edits, 0 test runs -> suggestion fires
9. **Coach dedup**: same pattern again -> no second suggestion
10. **MCP ChangeToolMode**: switch to test_dev -> state updated, constraints returned
11. **MCP GetFeedback**: returns status + suggestions + intercepts
12. **Graceful degradation**: blq not installed -> BlqInterceptor not registered, everything else works
