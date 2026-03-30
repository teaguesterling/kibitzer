# Kibitzer

*The person watching your chess game who can't help offering opinions.*

Kibitzer is a standalone Claude Code extension that watches how agents use tools and suggests better alternatives. It optionally coordinates with [Fledgling](https://github.com/teague/source-sextant) (code intelligence), [blq](https://github.com/teague/lq) (build/test capture), and [jetsam](https://github.com/teague/jetsam) (git workflow) — but none are required.

Kibitzer is level 1: specified rules over structured data. No LLM in the decision loop. Every decision traces to `config.toml`.

## What it does

Kibitzer has two interfaces:

**Passive (hooks)** — PreToolUse and PostToolUse hooks fire automatically on every tool call. They guard paths, intercept bash commands, track counters, and suggest improvements. The agent doesn't invoke these.

**Active (MCP server)** — Two tools the agent can call explicitly: `ChangeToolMode` to switch modes and `GetFeedback` to check status, get coaching suggestions, and see intercepted patterns.

## Quick start

```bash
pip install kibitzer
cd your-project/
kibitzer init --hooks --mcp
```

For richer coaching with fledgling conversation analytics:

```bash
pip install kibitzer[fledgling]
```

This creates:
- `.kibitzer/config.toml` — project configuration (edit to customize)
- `.kibitzer/state.json` — runtime state (don't edit)
- `.claude/hooks/kibitzer-pre.sh` — PreToolUse hook
- `.claude/hooks/kibitzer-post.sh` — PostToolUse hook
- `.claude/settings.json` — hooks registered
- `.mcp.json` — MCP server entry

## Documentation

- [Modes](modes.md) — the 7 modes and how path protection works
- [Coach](coach.md) — what patterns the coach detects and when
- [Interceptors](interceptors.md) — bash command interception and the observe/suggest/redirect ratchet
- [Configuration](configuration.md) — full config.toml reference
- [Architecture](architecture.md) — how the pieces fit together
- [Integration](integration.md) — how kibitzer works with blq, jetsam, fledgling, and superpowers

## Design principles

1. **No LLM in the loop.** Every decision is a specified rule in config.toml. Counters, thresholds, pattern matches.

2. **Graceful degradation.** blq not installed? Skip BlqInterceptor. Jetsam not installed? Skip JetsamInterceptor. None installed? Path guard and coach still work.

3. **Observe before enforce.** All interceptor plugins start in `observe` mode. Graduate to `suggest` then `redirect` by editing one line in config.

4. **The agent sees the reason.** Every deny includes why and how to fix it. "Path 'tests/foo.py' is not writable in the current mode. Use the ChangeToolMode tool to switch modes."

5. **Hooks are fast.** Read a JSON file, check a condition, output JSON. Target: <100ms per invocation.
