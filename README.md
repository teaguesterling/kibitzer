# Kibitzer

*The person watching your chess game who can't help offering opinions.*

Kibitzer is a [Claude Code](https://claude.ai/code) extension that watches how agents use tools and suggests better alternatives. It enforces path protection per mode, intercepts bash commands that have structured alternatives, and coaches agents toward more effective tool usage — all without an LLM in the decision loop.

[![PyPI](https://img.shields.io/pypi/v/kibitzer)](https://pypi.org/project/kibitzer/)
[![Python](https://img.shields.io/pypi/pyversions/kibitzer)](https://pypi.org/project/kibitzer/)
[![License](https://img.shields.io/github/license/teague/kibitzer)](LICENSE)
[![Docs](https://img.shields.io/readthedocs/kibitzer)](https://kibitzer.readthedocs.io)

## Install

```bash
pip install kibitzer
cd your-project/
kibitzer init --hooks --mcp
```

This registers PreToolUse/PostToolUse hooks in `.claude/settings.json` and optionally starts an MCP server with two tools: `ChangeToolMode` and `GetFeedback`.

## What it does

### Path protection

Each mode defines which paths the agent can write to. The path guard checks every `Edit`, `Write`, and `NotebookEdit` call.

```
Mode        Writable            Use case
─────────── ─────────────────── ───────────────────────────
free        everything          prototyping, no guardrails
create      everything          greenfield, scaffold-first
implement   src/, lib/          normal dev — tests protected
test_dev    tests/, test/       writing tests — source protected
document    docs/, README.md    documentation only
debug       nothing             read-only investigation
review      nothing             read-only code review
```

When a write is denied, the agent sees why and how to fix it:

```
Path 'tests/test_auth.py' is not writable in the current mode (writable: ['src/', 'lib/']).
Use the ChangeToolMode tool to switch modes.
```

### Interception

Interceptor plugins watch Bash calls for commands that have structured alternatives:

| Bash command | Suggested alternative | Plugin |
|---|---|---|
| `git add -A && git commit -m '...'` | `jetsam save` | jetsam |
| `pytest tests/` | `blq run test` | blq |
| `grep -rn 'def handler' src/` | `FindDefinitions(...)` | fledgling |

Three interception modes form a ratchet — start in `observe` (log silently), graduate to `suggest` (show alternative), then `redirect` (deny bash, require structured tool). Each graduation is a one-line config change.

### Coaching

The coach fires every N tool calls and detects patterns from ~250 experimental runs:

- **Repeated edit failures** — "Edit failed 3 times on src/handler.py. Try Read() first to see exact content."
- **Edit streak without tests** — "You've made 7 edits without running tests."
- **Semantic tool underuse** — "FindDefinitions shows all functions in one call instead of grepping file by file."
- **Analysis loop** — "You've spent 18 turns reading without changes. Start with the most confident fix."
- **Mode oscillation** — "Frequent mode switches. Consider using free mode."

Patterns are mode-aware: the analysis loop doesn't fire in debug mode (not editing is correct there), edit-without-test doesn't fire in document mode (docs don't need tests).

### Auto-transitions

The mode controller watches for failure patterns:
- 3+ consecutive failures → auto-switch to `debug`
- 20+ turns in debug → auto-switch back to `implement`

An oscillation guard prevents rapid switching: if the agent just left a mode (< 5 turns), it won't auto-switch back. After 6+ total switches, auto-transitions stop.

## MCP tools

The agent can call two tools explicitly:

**`ChangeToolMode(mode, reason?)`** — Switch modes. Returns the new mode's writable paths and strategy.

**`GetFeedback(status?, suggestions?, intercepts?)`** — Check current status, get coaching suggestions, and see which bash commands have been intercepted.

## Configuration

Override defaults in `.kibitzer/config.toml`:

```toml
# Monorepo: widen writable paths
[modes.implement]
writable = ["packages/core/src/", "packages/api/src/"]

# Graduate jetsam to suggest mode
[plugins.jetsam]
mode = "suggest"

# More aggressive coaching
[coach]
frequency = 3
```

## Coordinates with

Kibitzer suggests but never wraps these tools — each is independent:

- **[blq](https://github.com/teague/lq)** — structured build/test capture
- **[jetsam](https://github.com/teague/jetsam)** — git workflow acceleration
- **[Fledgling](https://github.com/teague/source-sextant)** — AST-aware code intelligence

None are required. Kibitzer degrades gracefully — path guard and coach work with nothing else installed.

## Documentation

Full docs at [kibitzer.readthedocs.io](https://kibitzer.readthedocs.io):

- [Modes](https://kibitzer.readthedocs.io/modes/) — path protection, switching, auto-transitions
- [Coach](https://kibitzer.readthedocs.io/coach/) — all patterns, experimental evidence, model dependency
- [Interceptors](https://kibitzer.readthedocs.io/interceptors/) — the observe/suggest/redirect ratchet
- [Configuration](https://kibitzer.readthedocs.io/configuration/) — full config.toml reference
- [Architecture](https://kibitzer.readthedocs.io/architecture/) — how the pieces fit together

## License

MIT
