# Shell Hooks

Kibitzer extends beyond Claude Code agent hooks to your terminal. Four layers give you visibility and guardrails from the shell prompt, without slowing down your workflow.

## Quick setup

```bash
# Install everything
kibitzer init --shell --git-hooks

# Or pick what you want
kibitzer init --shell       # print shell integration snippet
kibitzer init --git-hooks   # install git pre-commit hook
```

## CLI commands

These commands work standalone — no agent session required.

### `kibitzer mode [MODE]`

Get or set the current mode.

```bash
$ kibitzer mode
implement
  writable: src/, lib/, tests/, pyproject.toml

$ kibitzer mode test
implement → test
```

Switching mode from the shell is useful when you want to constrain the agent before starting a task, or when you're working alongside the agent and want to coordinate.

### `kibitzer status`

Dashboard showing session state.

```bash
$ kibitzer status
mode:    implement
turns:   61
calls:   436
fails:   0 consecutive
edits:   0 since last test
prev:    free (19 switches)
```

### `kibitzer prompt`

Short mode indicator for shell prompt integration. Adds `!` when 3+ consecutive failures have occurred.

```bash
$ kibitzer prompt
implement

$ kibitzer prompt --format json
{"mode": "implement", "consecutive_failures": 0}
```

### `kibitzer validate-staged`

Check staged git files against the current mode's writable paths. Exits 1 if any file violates the constraint.

```bash
$ kibitzer mode test
$ git add src/main.py
$ kibitzer validate-staged
Mode 'test' does not allow writes to:
  src/main.py

$ kibitzer validate-staged --format json
{"ok": false, "violations": [{"path": "src/main.py", "reason": "..."}]}
```

### `kibitzer shell-post <command>`

Coaching feedback for shell commands. Suggests structured alternatives when tools like jetsam or blq are available.

```bash
$ kibitzer shell-post "git commit -m fix"
kibitzer: consider: jetsam save

$ kibitzer shell-post "pytest tests/"
kibitzer: consider: blq run test
```

## Shell prompt integration

Add the kibitzer mode indicator to your shell prompt. The snippet reads `.kibitzer/state.json` directly — no Python invocation on every prompt render.

### Setup

Run `kibitzer init --shell` to print the integration snippet, then add it to your shell config:

```bash
# Add to .bashrc or .zshrc
eval "$(kibitzer init --shell 2>/dev/null)"

# Then use in your prompt:
# bash:
PS1='$(_kibitzer_prompt) \$ '
# zsh:
PROMPT='$(_kibitzer_prompt) %# '
```

### What it shows

```
[kib:implement] $          # normal
[kib:test] $               # test mode
[kib:implement!] $         # 3+ consecutive failures
```

The `!` indicator appears when the agent (or you) has hit 3+ consecutive failures — a signal to check what's going wrong.

## Shell command coaching

The `_kibitzer_preexec` function fires before interesting commands (git, pytest) and calls `kibitzer shell-post` for suggestions. Only triggers on commands that have known structured alternatives.

### Matched commands

| Shell command | Suggested alternative | Requires |
|---------------|----------------------|----------|
| `git add`, `git commit` | `jetsam save` | jetsam on PATH |
| `git push` | `jetsam sync` | jetsam on PATH |
| `git checkout` | `jetsam switch` | jetsam on PATH |
| `pytest` | `blq run test` | blq on PATH |

The coaching is advisory — it never blocks or modifies your command.

### Zsh setup

```zsh
# In .zshrc — works natively
preexec_functions+=(_kibitzer_preexec)
```

### Bash setup

```bash
# In .bashrc — requires bash-preexec (https://github.com/rcaloras/bash-preexec)
preexec_functions+=(_kibitzer_preexec)
```

## Git pre-commit guard

The pre-commit hook validates staged files against the current mode's writable paths. If you're in `test` mode and try to commit a file in `src/`, the commit is blocked.

### Setup

```bash
kibitzer init --git-hooks
```

This writes a pre-commit hook to `.git/hooks/pre-commit`. If a pre-commit hook already exists, kibitzer appends to it.

### Behavior

```bash
$ kibitzer mode test
$ git add src/main.py
$ git commit -m "oops"
Mode 'test' does not allow writes to:
  src/main.py
# commit blocked (exit 1)
```

To bypass when needed: `git commit --no-verify`.

### Coexistence

The git hook calls `kibitzer validate-staged`, which reads mode policy the same way the agent's path guard does. If you have umwelt policies, they're respected here too.

## Event source tagging

Shell-originated events are tagged with `source: "shell"` in the SQLite event log (`.kibitzer/store.sqlite`). This lets you distinguish human shell activity from agent tool calls:

```sql
SELECT * FROM events WHERE source = 'shell';
SELECT * FROM events WHERE source = 'agent';
```

## Architecture

```
Shell prompt ──── _kibitzer_prompt()  ──── reads state.json (no Python)
                                            ~2ms per prompt

Shell command ─── _kibitzer_preexec() ──── pattern match in shell
                        │                   selective Python invocation
                        └── kibitzer shell-post ── coach + log event

Git commit ────── pre-commit hook ──────── kibitzer validate-staged
                                            check staged files vs mode

CLI ───────────── kibitzer mode/status ─── direct state read/write
```

The shell prompt layer is pure shell — it reads `state.json` with a single `python3 -c` invocation. The coaching and validation layers invoke Python only when needed. This keeps the fast path fast.
