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

This registers PreToolUse/PostToolUse hooks in `.claude/settings.json` and starts an MCP server with two tools the agent can call: `ChangeToolMode` and `GetFeedback`.

For richer coaching with [Fledgling](https://github.com/teague/source-sextant) conversation analytics:

```bash
pip install kibitzer[fledgling]
```

## What it does

### Path protection

Each mode defines which paths the agent can write to. The path guard checks every `Edit`, `Write`, and `NotebookEdit` call — including absolute paths from Claude Code.

```
Mode        Writable            Use case
─────────── ─────────────────── ───────────────────────────
free        everything          prototyping, no guardrails
implement   src/, lib/          normal dev — tests protected
test        tests/, test/       writing tests — source protected
docs        docs/, README.md    documentation only
explore     nothing             read-only investigation
```

When a write is denied, the agent sees why and how to fix it:

```
Path 'tests/test_auth.py' is not writable in the current mode (writable: ['src/', 'lib/']).
Use the ChangeToolMode tool to switch modes.
```

In testing, agents consistently read this message and call `ChangeToolMode` to switch — no documentation or pre-training needed.

### Interception

Interceptor plugins watch Bash calls for commands that have structured alternatives:

| Bash command | Suggested alternative | Plugin |
|---|---|---|
| `git add -A && git commit -m '...'` | `jetsam save` | jetsam |
| `pytest tests/` | `blq run test` | blq |
| `grep -rn 'def handler' src/` | `FindDefinitions(...)` | fledgling |

Three interception modes form a ratchet — start in `observe` (log silently), graduate to `suggest` (show alternative), then `redirect` (deny bash, require structured tool). Each graduation is a one-line config change.

### Coaching

The coach fires every N tool calls and detects patterns from ~250 experimental runs. Suggestions only reference tools the agent actually has — discovered from `.mcp.json` at runtime.

**State-based patterns (always available):**

- **Repeated edit failures** — "Edit failed 3 times on src/handler.py. Try Read() first to see exact content."
- **Edit streak without tests** — "You've made 7 edits without running tests." (mentions `blq run test` if blq is available)
- **Sequential file reads** — "You've read 5 files one at a time." (mentions `FindDefinitions` if fledgling is available)
- **Bash-heavy usage** — "You've run 6 bash commands without using structured tools."
- **Analysis loop** — "You've spent 18 turns reading without changes. Start with the most confident fix."
- **Semantic tool underuse** — "FindDefinitions shows all functions in one call." (only fires if fledgling is available)
- **Mode oscillation** — "Frequent mode switches. Consider using free mode."

**TDD patterns:**

- **Test overfit** — "test_auth.py has been edited 4 times. Stabilize test expectations before adjusting further."
- **Implement before test** — "You edited source before writing tests. Consider starting with a failing test."

**Fledgling-powered patterns (when fledgling is installed):**

- **Repeated search patterns** — "You've searched for 'def handle_request' 4 times via Grep."
- **Replaceable bash commands** — "You've run 'grep' 3 times. FindDefinitions provides structured output."

All patterns are mode-aware: the analysis loop doesn't fire in explore mode (not editing is correct there), edit-without-test doesn't fire in docs mode (docs don't need tests).

### Auto-transitions

The mode controller watches for failure patterns:
- 3+ consecutive failures → auto-switch to `explore`
- 20+ turns in explore → auto-switch back to `implement`

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

# Add custom modes
[modes.deploy]
writable = ["infra/", "deploy/"]
strategy = "Verify before applying."

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

None are required. Kibitzer degrades gracefully — path guard and coach work with nothing else installed. When tools are available, suggestions reference them specifically. When they're not, suggestions give generic advice.

## Documentation

Full docs at [kibitzer.readthedocs.io](https://kibitzer.readthedocs.io):

- [Modes](https://kibitzer.readthedocs.io/modes/) — path protection, switching, auto-transitions
- [Coach](https://kibitzer.readthedocs.io/coach/) — all patterns, experimental evidence, model dependency
- [Interceptors](https://kibitzer.readthedocs.io/interceptors/) — the observe/suggest/redirect ratchet
- [Configuration](https://kibitzer.readthedocs.io/configuration/) — full config.toml reference, resilience, optional deps
- [Architecture](https://kibitzer.readthedocs.io/architecture/) — how the pieces fit together
- [Integration](https://kibitzer.readthedocs.io/integration/) — blq, jetsam, fledgling, superpowers

## License

MIT
