# Modes

Kibitzer restricts what the agent can write based on the current mode. The path guard checks every `Edit`, `Write`, and `NotebookEdit` call against the mode's `writable` list, and statically scans `Bash` commands for common write vectors (redirections, `tee`, `cp`, `mv`, `rm`, `sed -i`, `ln`, ...). If the target doesn't resolve into a writable path, the call is denied with a reason that tells the agent how to switch.

Before comparing, the guard **canonicalizes** the target against the project directory — symlinks are followed, `.`/`..` are collapsed, and absolute and relative spellings of the same location compare equal. The guard **fails closed**: an unknown mode, a missing target path, an unparseable Bash command in a restricted mode, or an internal guard error denies the write.

Bash coverage is a static analysis of the command string, not a shell interpreter — see [Scope and limits](#scope-and-limits) below for what it cannot see.

## The 6 modes

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
- **When to use:** Diagnosing a problem before changing it. All edits are blocked — the agent can only read, search, and run commands. Forces investigation before action. The mode controller auto-switches here after 3+ consecutive failures.

### `review`
- **Writable:** nothing (read-only)
- **Strategy:** "Read everything, then verify with tests."
- **When to use:** Reviewing existing code (a PR, a change, a suspect module). Like `explore` it blocks all edits, but the strategy points at *judging* code rather than *diagnosing a problem* — read broadly, then confirm with the test suite. Not part of the auto-transition graph; switch into it deliberately via `ChangeToolMode`.

## Path matching

Writable entries are matched by **canonical path containment**, not string prefix. `"src/"` matches `src/foo/bar.py` (and any path — absolute, relative, `..`-laden, or symlinked — that resolves inside `src/`), but not `src_secret/`. `"README.md"` matches `README.md` exactly, not `README.md.bak`. `["*"]` means everything is writable. `[]` means nothing is writable (read-only). Unknown mode names resolve to `[]`.

## Scope and limits

Path protection is one layer, not a sandbox:

- **Bash coverage is static.** The guard analyzes the command string for redirections and a table of common write commands. Writes hidden behind interpreter one-liners (`python -c ...`), scripts fed on stdin, `make` targets, or git commands that mutate the tree are not visible to it. Opaque write constructs it *can* spot — `xargs rm`, process substitution targets, unparseable quoting — are denied in restricted modes rather than waved through.
- **Check-then-act window.** Symlinks are resolved at decision time; a race that swaps a path component between the check and the actual write is not preventable at this layer.
- **`free` mode is unguarded by design** (`["*"]`) and is the default. Set a restricted `default_mode` in `.kibitzer/config.toml` if you want the guard active from the first call.

For a hard boundary, compose kibitzer with the Claude Code permission system and an OS-level sandbox (e.g. an umwelt-compiled policy under nsjail). Kibitzer keeps a cooperating-but-fallible agent on task; the sandbox bounds what a misbehaving process can do.

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

You can also define entirely new modes (beyond the six built in):

```toml
[modes.deploy]
writable = ["infra/", "deploy/", "k8s/"]
strategy = "Verify before applying."

[modes.migration]
writable = ["migrations/", "alembic/"]
strategy = "One reversible step at a time."
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
