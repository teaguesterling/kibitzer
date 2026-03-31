# Kibitzer v0.2 Design: Adaptive Mode Transitions

## Problem

v0.1 kibitzer acts as a guard — it blocks writes in the wrong mode and requires the agent to explicitly call `ChangeToolMode`. This works but creates friction: the agent has to discover the MCP tool, call it, then retry. In live testing, agents figure it out, but the deny-retry cycle is ceremonial for obvious transitions.

The deeper problem: the guard treats all mode violations equally. An agent in explore mode trying to Edit src/main.py is obviously transitioning to implementation. An agent in explore mode running `sed -i` through Bash is suspicious. These should be handled differently.

## Design

### Modes (simplified)

```toml
[modes.free]
writable = ["*"]
strategy = ""

[modes.implement]
writable = ["src/", "lib/"]
strategy = ""

[modes.test]
writable = ["tests/", "test/", "spec/"]
strategy = "Write tests for expected behavior, not current behavior."

[modes.docs]
writable = ["docs/", "README.md", "CHANGELOG.md"]
strategy = "Explain the why, not the what."

[modes.explore]
writable = []
strategy = "Map the territory before making changes."
```

Five modes. Removed `create` (use `free`), `debug` and `review` (merged into `explore`), renamed `test_dev` → `test`, `document` → `docs`.

### Auto-transitions

When the path guard encounters a write to a path not allowed in the current mode, instead of immediately denying, it asks: **is this a clean transition?**

A clean transition:
1. The target path matches exactly one other mode's writable set
2. Auto-transitions are enabled (not in strict mode)
3. The config allows auto-transitions (`[controller] auto_transition = true`)

If clean: switch mode, allow the write, inject context via `additionalContext`.
If not clean: deny as before.

#### Auto-transition rules

| Current mode | Agent action | Transition | Why |
|---|---|---|---|
| explore | Edit src/foo.py | → implement | obvious intent to code |
| explore | Edit tests/foo.py | → test | obvious intent to test |
| explore | Edit docs/foo.md | → docs | obvious intent to document |
| implement | Edit tests/foo.py | → test | switching to test writing |
| test | Edit src/foo.py | → implement | switching to implementation |
| docs | Edit src/foo.py | → implement | done documenting |
| any | Bash with `pytest` | preemptive → test | test results coming, unlock tests |

#### Blocked (never auto-transition)

| Current mode | Agent action | Result |
|---|---|---|
| explore | Bash with file-modifying command | **block + strict** |
| any | Edit to path outside all modes | **block** (not in any writable set) |
| free | (anything) | always allowed |

### Trust level and strictness

State tracks two new fields:

```json
{
  "auto_transition_enabled": true,
  "trust_level": "normal"
}
```

**trust_level** values:
- `normal` — auto-transitions work, smooth flow
- `strict` — auto-transitions disabled, must call ChangeToolMode explicitly

**Transitions between trust levels:**
- normal → strict: agent attempts a bash write in a read-only mode, or rapid oscillation (3+ auto-transitions in 5 calls)
- strict → normal: agent behaves well for 10 calls (no denials, no suspicious bash)

**Config:**

```toml
[controller]
auto_transition = true           # master switch
strict_after_bash_write = true   # tighten on bash writes in read-only
strict_cooldown = 10             # calls before returning to normal
```

### Context injection on transitions

When a mode auto-transitions, the hook injects a summary as `additionalContext`:

```
[kibitzer] Switched to implement mode.
```

When jetsam or blq are available, enrich:

```
[kibitzer] Switched to test mode. Changes since last test: 3 files modified (from jetsam status).
```

```
[kibitzer] Switched to implement mode. Last test run: 2 failing, 13 passing (from blq errors).
```

Context injection only happens on transitions, not on every call. Keep it brief — one line.

### Test overfit detection

New coach pattern for the test workflow:

**`test_overfit`**: A test file has been edited 3+ times in the current session.

```
[kibitzer] tests/test_auth.py has been edited 4 times this session.
Consider stabilizing test expectations before adjusting further.
```

**`implement_before_test`**: In a session, src/ files were edited before any test/ files.

```
[kibitzer] You edited source before writing tests. Consider starting
with a failing test next time.
```

These are coach suggestions (additionalContext), not enforcement. They fire once per session (deduped).

### State changes

New fields in state.json:

```json
{
  "auto_transition_enabled": true,
  "trust_level": "normal",
  "strict_since_call": null,
  "test_file_edits": {},
  "first_edit_was_test": null,
  "auto_transitions_recent": []
}
```

- `trust_level`: "normal" or "strict"
- `strict_since_call`: total_calls when strict was triggered (for cooldown)
- `test_file_edits`: `{filepath: count}` for test files
- `first_edit_was_test`: `true/false/null` — was the first write to a test file?
- `auto_transitions_recent`: list of recent auto-transition call numbers (for oscillation detection)

### Migration from v0.1

Breaking changes:
- `test_dev` mode renamed to `test`
- `document` mode renamed to `docs`
- `create` mode removed (use `free` or configure `implement` wider)
- `debug` and `review` modes removed (use `explore`)
- `default_mode` changes from `implement` to `explore`

The path guard behavior changes fundamentally — from deny-only to auto-transition-with-deny-fallback. Existing configs that reference removed modes will fall through to the unknown-mode handler (unrestricted, as before).

### What doesn't change

- MCP tools: `ChangeToolMode` and `GetFeedback` stay the same
- Interceptors: observe/suggest/redirect ratchet unchanged
- Coach: existing patterns unchanged, new patterns added
- Fledgling integration: unchanged
- Hook protocol: unchanged (still PreToolUse/PostToolUse)

### v0.1 bugs to fix (from code review)

These should be fixed before or alongside the v0.2 feature work.

**Critical:**

1. **NotebookEdit path guard bypass.** The guard reads `tool_input.get("file_path")` but Claude Code sends `notebook_path` for NotebookEdit. All notebook writes are silently allowed regardless of mode. Fix: check both `file_path` and `notebook_path` fields.

2. **Plugin `enabled` flag is dead code.** Config supports `enabled = false` per plugin, but `pre_tool_use.py` only reads `mode`, never checks `enabled`. Disabling a plugin in config has no effect. Fix: check `enabled` before running interceptor.

**Important:**

3. **Consecutive failures off-by-one.** `mode_controller.py` uses `> max_failures` instead of `>= max_failures`. With default of 3, auto-debug triggers at 4 failures, not 3 as documented. Fix: change to `>=` and update tests.

4. **Oscillation guard checks wrong variable.** Checks `turns_in_previous_mode` (how long agent was in old mode before leaving) instead of checking whether the agent has been in the *current* mode long enough before switching back. Can fail to prevent rapid oscillation. Fix: rethink the oscillation guard logic — track when we entered the current mode and how long we've been here.

5. **GetFeedback consumes suggestion dedup budget.** Calling `GetFeedback(suggestions=true)` marks suggestions as "given" in state, preventing the hook-based coach from ever firing those same suggestions. An early GetFeedback call silently suppresses future coaching. Fix: separate "shown via MCP" from "shown via hook" tracking, or don't mark as given when called via MCP.

6. **`auto_review_on_tests_passing` not implemented.** Config key exists, spec describes it, no code implements it. Fix: either implement or remove from config/docs.

**Docs:**

7. **Intercept log field names inconsistent with spec examples.** Code uses `bash_command`/`suggested_tool`, some spec references use `bash`/`alternative`. Fix: align docs to match actual output.

8. **Stale plan files.** `docs/plans/agent-kibitzer.md` and `docs/plans/user-kibitzer.md` describe a superseded Fledgling-embedded design. Fix: archive or add "superseded" note.

### Implementation order

1. Fix v0.1 bugs (items 1-6 above)
2. Rename modes (breaking change, update all tests)
3. Add `test_overfit` and `implement_before_test` coach patterns
4. Add auto-transition logic to path guard
5. Add trust level / strictness ratchet
6. Add context injection on transitions (jetsam/blq summaries)
7. Update docs (fix items 7-8, update for v0.2 features)

### Ecosystem integrations

#### Agent Riggs — cross-session intelligence (System 3*)

Riggs is kibitzer's cross-session memory. It ingests events from kibitzer's state and intercept logs, computes trust scores (t1/t5/t15 EWMA windows), and writes recommendations back to `.kibitzer/state.json`.

**Integration points:**
- Riggs reads: `.kibitzer/state.json`, `.kibitzer/intercept.log`
- Riggs writes: trust level and transition recommendations to `.kibitzer/state.json`
- Kibitzer reads: Riggs' trust recommendations to inform auto-transition decisions
- The `trust_level` field in state.json is the handoff point — Riggs sets it, kibitzer respects it

**Ratchet promotions:** Riggs identifies bash patterns that should be promoted (frequency ≥ 5, sessions ≥ 3, success rate ≥ 0.8) and recommends graduating interceptors from observe → suggest → redirect. Human reviews before applying.

**Session briefings:** Riggs composes briefings from trust + ratchet data. Could inject via a SessionStart hook or MCP resource (`riggs://briefing`).

#### lackpy — tool composition delegation

lackpy takes an intent, generates a restricted Python program against an AST whitelist, and executes with traced tool calls. One MCP call replaces N tool round-trips.

This is the *solution* to several patterns the coach detects:

| Coach detects | lackpy could do |
|---|---|
| 3+ sequential reads | `lackpy delegate "read and summarize these files" --kit read` |
| Repeated grep for same pattern | `lackpy delegate "find all definitions of X" --kit read,glob` |
| Multiple edits to apply same change | `lackpy delegate "apply this pattern to all matching files" --kit read,glob` |

**New interceptor type: sequence interceptor.** Current interceptors match single Bash commands. A LackpyInterceptor would match *sequences* of tool calls — detecting when multiple calls could be composed into a single delegation.

**Implementation approach:**
- Track recent tool call sequences in state (last N calls with their inputs)
- LackpyInterceptor checks the sequence for composable patterns
- In suggest mode: "These 5 reads could be one lackpy delegation"
- In redirect mode: automatically compose and delegate (future, needs more trust)
- lackpy availability checked via `.mcp.json` or `shutil.which("lackpy")`

**Key constraint:** lackpy generates and executes code, so this is a higher-trust suggestion than "use jetsam save." The interceptor should probably stay in observe/suggest mode longer before graduating to redirect.

#### sitting_duck / astquery — structural code intelligence

With sitting_duck's `ast_select` (CSS selectors for AST querying), the coach could move from file-level observations to structural observations:

| Current coach (file-level) | With ast_select (structural) |
|---|---|
| "test_auth.py edited 4 times" | "3 assertions in test_auth rewritten — overfitting?" |
| "5 edits without tests" | "modified handle_request return type — 3 callers may need updating" |
| "editing in explore mode" | "modified a function with @deprecated decorator" |

**Where this lives:** Fledgling, not kibitzer. Fledgling exposes higher-level queries (`change_impact()`, `assertion_churn()`), kibitzer's coach consumes them. Kibitzer shouldn't know AST internals.

**Dependency:** Waiting on the astquery/fledgling-edit layer that provides a builder API over sitting_duck queries + fledgling-edit changesets.

#### nsjail-python — physical enforcement

When kibitzer's path guard blocks a Bash write in a read-only mode, it's a soft deny (the agent sees a message). nsjail-python via blq provides hard enforcement — the command physically cannot write to protected paths.

**Integration:** kibitzer's mode → blq's sandbox spec → nsjail config. When mode is `explore`, blq could enforce `readonly_root()` via nsjail. This is blq's domain, not kibitzer's — kibitzer just communicates the mode.

### Open questions

- Should `ChangeToolMode` be renamed to just `mode` or `SetMode`?
- Should auto-transitions be logged to intercept.log for ratchet data?
- Should the default starting mode be `explore` instead of `implement`?
- Should sequence interceptors (for lackpy) live in the interceptor plugin system or be a separate coach pattern?
- Future: should kibitzer predict the next mode based on recent tool patterns? (the HMM idea — park for now)
