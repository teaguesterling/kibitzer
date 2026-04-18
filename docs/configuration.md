# Configuration

Kibitzer loads configuration from two sources, merged with project-local values overriding defaults:

1. **Package defaults** — ships with kibitzer at `src/kibitzer/config.toml`
2. **Project-local** — `.kibitzer/config.toml` in the project root (created by `kibitzer init`)

You only need to put overrides in the project-local file. Missing values fall back to defaults.

## Full default config

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

[controller]
default_mode = "implement"
max_consecutive_failures = 3
max_turns_in_explore = 20

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

## Section reference

### `[modes.<name>]`

Each mode defines what paths the agent can write to and an optional strategy instruction.

| Field | Type | Description |
|-------|------|-------------|
| `writable` | list of strings | Path prefixes the agent can write to. `["*"]` = unrestricted. `[]` = read-only. |
| `strategy` | string | Optional instruction injected when the mode is active. Empty string = none. |

**Writable paths** are prefix-matched: `"src/"` matches `src/foo/bar.py`. Exact filenames also work: `"README.md"` matches `README.md` but not `src/README.md`.

**Custom modes:** You can define new modes beyond the 5 defaults:

```toml
[modes.deploy]
writable = ["infra/", "deploy/"]
strategy = "Verify before applying."

[modes.review]
writable = []
strategy = "Read everything before forming an opinion."
```

### `[controller]`

Controls automatic mode transitions.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `default_mode` | string | `"implement"` | Mode on fresh state |
| `max_consecutive_failures` | integer | `3` | Consecutive failures before auto-switch to explore |
| `max_turns_in_explore` | integer | `20` | Turns in explore before auto-switch to implement |

### `[coach]`

Controls coaching behavior.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `frequency` | integer | `5` | Suggest every N tool calls |
| `enabled` | boolean | `true` | Master switch for the coach |

### `[coach.model_overrides]`

Override coach settings per model. Only `frequency` is currently supported.

```toml
[coach.model_overrides]
haiku = { frequency = 3 }    # more frequent for Haiku
sonnet = { frequency = 5 }
opus = { frequency = 10 }    # less frequent for Opus
```

### `[plugins.<name>]`

Configure interceptor plugins.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | string | `"observe"` | Interception mode: `"observe"`, `"suggest"`, or `"redirect"` |
| `enabled` | boolean | `true` | Whether the plugin is active (also requires the tool to be installed) |

## Common overrides

### Rust project (tests inline, no test/ dir)

```toml
[modes.implement]
writable = ["src/", "Cargo.toml", "build.rs"]

[modes.test]
writable = ["src/", "tests/"]
# src/ writable because Rust tests live in source files
```

### Monorepo

```toml
[modes.implement]
writable = ["packages/core/src/", "packages/api/src/", "packages/shared/src/"]

[modes.test]
writable = ["packages/core/tests/", "packages/api/tests/"]
```

### Aggressive coaching

```toml
[coach]
frequency = 3

[plugins.jetsam]
mode = "suggest"

[plugins.blq]
mode = "suggest"
```

### Disable coaching entirely

```toml
[coach]
enabled = false
```

## Optional dependencies

Kibitzer works standalone but benefits from optional integrations:

```bash
pip install kibitzer              # core only
pip install kibitzer[fledgling]   # + fledgling Python API for richer coaching
```

The coach discovers available tools by reading `.mcp.json` in the project root. If fledgling, blq, or jetsam are registered as MCP servers, the coach references their specific tools in suggestions. Without `.mcp.json`, it falls back to checking CLI availability via `which`.

## umwelt / ducklog integration (optional)

Kibitzer can read mode definitions and tool surfaces from an [umwelt](https://github.com/teaguesterling/umwelt) policy database compiled by [ducklog](https://github.com/teaguesterling/ducklog). This lets you author policy in umwelt's CSS-shaped `.umw` format and have kibitzer consume it automatically.

### Setup

```bash
pip install ducklog  # optional dependency

# Author a policy
cat > policy.umw << 'EOF'
mode.implement tool { allow: true; }
mode.explore tool { allow: false; }
mode.explore tool[name="Read"] { allow: true; }
mode.explore tool[name="Grep"] { allow: true; }
EOF

# Compile to a policy database
umwelt compile --target duckdb -o .kibitzer/policy.duckdb policy.umw
```

### How it works

When `.kibitzer/policy.duckdb` exists, `load_config()` merges its mode definitions on top of the TOML config:

1. Package defaults (`config.toml`) load first
2. Project-local `.kibitzer/config.toml` overrides (if present)
3. `.kibitzer/policy.duckdb` overrides (if present and ducklog is installed)

ducklog-defined modes override TOML modes of the same name. New modes (e.g., `deploy`) from the database are added. Modes only in TOML (not in the database) are preserved.

### What the database provides

- **Mode definitions** — writable paths and strategy text per mode
- **Tool surfaces** — per-mode allow/deny lists (which tools are available in which mode)

These appear in the config dict alongside TOML-defined values. `get_mode_policy()` and `check_path()` work unchanged.

### Without ducklog

If ducklog is not installed, the fallback is invisible — kibitzer loads from TOML only, no error, no warning. ducklog is a soft optional dependency.

## Resilience

Both `state.json` and project `config.toml` are loaded defensively:

- **Corrupt state.json** (empty, invalid JSON, non-dict) → falls back to fresh state
- **Corrupt config.toml** (invalid TOML) → falls back to package defaults
- **Missing `.kibitzer/` directory** → uses defaults, PostToolUse creates it on first call
- **Invalid hook input** (bad JSON on stdin) → hooks exit silently (exit 0, no output)
- **Fledgling query failure** (timeout, error) → coach uses state-only patterns

Writes to `state.json` are atomic (temp file + rename) so a crash mid-write won't corrupt state.

## State file

`.kibitzer/state.json` is runtime state — don't edit it manually. It tracks:

- Current mode and previous mode
- Failure/success counts and consecutive failure streak
- Turns in current mode
- Tools used in current mode
- Coach suggestions already given (for dedup)
- Coach observation counters (edit failures, reads, edits since test, etc.)

The state resets per-mode when you switch modes. Session-level fields (total calls, mode switches, suggestions given) persist across mode switches.
