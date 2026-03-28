# Interceptors

Interceptors watch Bash tool calls for commands that have structured alternatives. When the agent runs `git add -A && git commit -m 'fix'` through Bash, the jetsam interceptor notices and can observe, suggest, or redirect.

Interceptors only fire on Bash calls. Edit, Write, Read, and other structured tools are handled by the [path guard](modes.md) and don't need interception — they're already structured.

## The three plugins

### BlqInterceptor

Detects build/test commands and suggests [blq](https://github.com/teague/lq) structured capture.

| Trigger | Suggestion |
|---------|-----------|
| `pytest`, `python -m pytest` | `blq run test` |
| `npm test` | `blq run test` |
| `cargo test` | `blq run test` |
| `go test` | `blq run test` |
| `make test`, `gradle test` | `blq run test` |

**Why:** blq captures structured output — errors are queryable via `blq errors`, results persist across sessions, and output is diffable over time. Bash pytest works, but the output is ephemeral.

**Caveat from experiments:** In Sonnet/E, using bash for pytest was the most efficient approach ($0.98). The suggestion should be `observe` or `suggest` mode, not `redirect`. The agent may have good reasons for bash.

### JetsamInterceptor

Detects git workflow commands and suggests [jetsam](https://github.com/teague/jetsam) operations.

| Trigger | Suggestion |
|---------|-----------|
| `git add` + `git commit` (same command) | `jetsam save '<description>'` |
| `git push` | `jetsam sync` |
| `git stash` | `jetsam save '<description>'` |
| `git diff` | `jetsam diff` |
| `git log` | `jetsam log` |

**Not intercepted:** `git status` (read-only, no alternative needed), `git checkout`, `git branch` (too context-dependent).

**Why:** jetsam provides plan tracking, confirmation steps, branch management, and audit trails. `git add && git commit` works, but bypasses all of that.

### FledglingInterceptor

Detects code search/navigation and suggests [Fledgling](https://github.com/teague/source-sextant) semantic alternatives.

| Trigger | Suggestion |
|---------|-----------|
| `grep -r` / `grep -rn` + definition keyword (`def `, `class `, `function `, etc.) | `FindDefinitions(name_pattern='...')` |
| `find . -name` / `find . -type f` | `CodeStructure(file_pattern='**/*.py')` |

**Not intercepted:** `grep` for content strings (not definitions) — the agent may be searching for error messages, config values, etc. where grep is the right tool.

**Why:** FindDefinitions is AST-aware — it understands scope, type, and nesting. grep finds text matches, which includes comments, strings, and partial matches.

## Availability

Each plugin checks `shutil.which(tool_name)` at load time. If blq isn't installed, BlqInterceptor doesn't register. Kibitzer still works for everything else.

```
blq installed    → BlqInterceptor active
jetsam installed → JetsamInterceptor active
fledgling installed → FledglingInterceptor active
none installed   → interceptors disabled, path guard and coach still work
```

## Interception modes: the ratchet

Each plugin has an interception mode, configurable per-plugin in `.kibitzer/config.toml`:

```toml
[plugins.blq]
mode = "observe"    # or "suggest" or "redirect"

[plugins.jetsam]
mode = "observe"

[plugins.fledgling]
mode = "observe"
```

### `observe` (default)

The bash command is **allowed**. The interceptor logs the suggestion to `.kibitzer/intercept.log` but the agent never sees it. Use this to gather data on which bash patterns have structured alternatives.

The log is a JSONL file:
```json
{"bash_command": "git add -A && git commit -m 'fix'", "suggested_tool": "jetsam save '<description>'", "reason": "...", "plugin": "jetsam"}
```

Review the log with `GetFeedback(intercepts=true)` to see what's being intercepted. When you're confident the alternatives work, graduate to `suggest`.

### `suggest`

The bash command is **allowed**, but the suggestion is injected into the agent's context via `additionalContext`:

```
[kibitzer] jetsam suggests: jetsam save '<description>'
Reason: Atomic save with plan tracking, confirmation step, and branch management
```

The agent sees the suggestion alongside the bash call. It can choose either. This is the learning phase — the agent discovers the alternative and decides whether to adopt it.

### `redirect`

The bash command is **denied**. The agent must use the structured alternative:

```
A structured alternative is available: jetsam save '<description>'
Atomic save with plan tracking, confirmation step, and branch management
```

This is enforcement. Only use after confirming the agent follows suggestions reliably.

### The ratchet progression

```
observe → suggest → redirect
```

Each graduation is a one-line config change, not a code change. The progression is intentional:

1. **Observe** — gather data. Which bash patterns match? How often?
2. **Suggest** — inform. Does the agent adopt the alternative?
3. **Redirect** — enforce. The structured tool is now required.

Not every plugin needs to reach `redirect`. blq's test capture may stay at `suggest` forever — there are legitimate reasons to use bash pytest. But jetsam's git workflow might reach `redirect` once the agent consistently follows the suggestion.

## First match wins

If multiple plugins match the same bash command, only the first match fires. The check order is: blq, jetsam, fledgling. This rarely matters in practice since the trigger patterns don't overlap.
