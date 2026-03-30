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

### Implementation order

1. Rename modes (breaking change, update all tests)
2. Add `test_overfit` and `implement_before_test` coach patterns
3. Add auto-transition logic to path guard
4. Add trust level / strictness ratchet
5. Add context injection on transitions (jetsam/blq summaries)
6. Update docs

### Open questions

- Should `ChangeToolMode` be renamed to just `mode` or `SetMode`?
- Should auto-transitions be logged to intercept.log for ratchet data?
- Should the default starting mode be `explore` instead of `implement`?
- Future: should kibitzer predict the next mode based on recent tool patterns? (the HMM idea — park for now)
