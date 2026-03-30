# Modes

Kibitzer enforces what the agent can write based on the current mode. The path guard checks every `Edit`, `Write`, and `NotebookEdit` call against the mode's `writable` list. If the path doesn't match a writable prefix, the call is denied with a reason that tells the agent how to switch.

Bash writes are not guarded — that's [blq's sandbox enforcement](integration.md#blq) domain.

## The 5 modes

### `free`
- **Writable:** everything (`["*"]`)
- **Strategy:** none
- **When to use:** Prototyping, exploration, or when the guardrails are getting in the way. The coach will suggest switching if you've been in free mode for a while with a lot of failures.

### `implement`
- **Writable:** `src/`, `lib/`
- **Strategy:** none
- **When to use:** Normal development. Tests are protected (can't accidentally modify them to pass). Config files are protected. This is the default mode.

### `test`
- **Writable:** `tests/`, `test/`, `spec/`
- **Strategy:** "Write tests for expected behavior, not current behavior."
- **When to use:** Writing or updating tests. Source code is protected — you can't change implementation to match broken tests.

### `docs`
- **Writable:** `docs/`, `README.md`, `CHANGELOG.md`
- **Strategy:** "Explain the why, not the what."
- **When to use:** Writing documentation. Source and tests are protected. The coach suppresses edit-without-test suggestions in this mode (docs don't need test runs).

### `explore`
- **Writable:** nothing (read-only)
- **Strategy:** "Map the territory before making changes."
- **When to use:** Diagnosing a problem or reviewing code. All edits are blocked — the agent can only read, search, and run commands. Forces investigation before action. The mode controller auto-switches here after 3+ consecutive failures.

## Path matching

Writable paths are prefix-matched. `"src/"` matches `src/foo/bar.py`. `"README.md"` matches `README.md` exactly. `["*"]` means everything is writable. `[]` means nothing is writable (read-only).

## Switching modes

The agent switches modes by calling the `ChangeToolMode` MCP tool:

```
ChangeToolMode(mode="test", reason="writing tests for the new feature")
```

The response tells the agent what's now writable:

```json
{
  "previous_mode": "implement",
  "new_mode": "test",
  "writable": ["tests/", "test/", "spec/"],
  "strategy": "Write tests for expected behavior, not current behavior."
}
```

Mode switches reset counters (failure count, success count, turns in mode, tools used) so the coach evaluates behavior fresh in the new mode.

## Auto-transitions

The mode controller can switch modes automatically based on failure patterns:

| Trigger | Transition | Condition |
|---------|-----------|-----------|
| 3+ consecutive failures | → `explore` | Current mode is writable (not explore) |
| 20+ turns in explore | → `implement` | Extended diagnosis, time to act |

Auto-transitions have an **oscillation guard**: if the agent has spent fewer than 5 turns in the current mode, it won't auto-switch. After 6+ total mode switches, auto-transitions stop entirely — the coach suggests using `free` mode instead.

`free` mode never auto-transitions. If you chose that mode, kibitzer respects that.

## Customizing modes

Override writable paths per-project in `.kibitzer/config.toml`:

```toml
# Rust project: source is in src/, tests are inline
[modes.implement]
writable = ["src/", "Cargo.toml", "build.rs"]

[modes.test]
writable = ["src/", "tests/"]
# src/ writable because Rust tests live in source files
```

You can also define entirely new modes:

```toml
[modes.deploy]
writable = ["infra/", "deploy/", "k8s/"]
strategy = "Verify before applying."

[modes.review]
writable = []
strategy = "Read everything before forming an opinion."
```

## Coach behavior per mode

Not all coach patterns fire in all modes. Patterns that would be noise in the current mode are suppressed:

| Pattern | Active in | Suppressed in |
|---------|-----------|---------------|
| Repeated edit failure | writable modes | explore |
| Sequential reads | writable modes | explore (reading is the job) |
| Edit without test | implement, test, free | explore, docs |
| Semantic tool underuse | all modes | — |
| Analysis loop | writable modes | explore (not editing is correct) |
| High failure ratio | all modes | — |
| Mode oscillation | all modes | — |
| Explore mode edits | explore only | — |
