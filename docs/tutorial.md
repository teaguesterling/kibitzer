# Tutorial: Set Up Kibitzer for Your Terminal

This walkthrough takes you from a fresh install to a working shell setup — prompt indicator, mode switching, pre-commit guard, and command coaching. About 5 minutes.

## Prerequisites

- Python 3.10+
- A project directory with a git repo
- bash or zsh

## 1. Install and initialize

```bash
pip install kibitzer
cd your-project/

# Set up everything: Claude Code hooks, shell integration, git guard
kibitzer init --hooks --mcp --shell --git-hooks
```

You'll see output like:

```
Created .kibitzer/config.toml
Created .kibitzer/state.json
Created .claude/hooks/kibitzer-pre.sh
Created .claude/hooks/kibitzer-post.sh
Updated .claude/settings.json
Created .mcp.json
Created .git/hooks/pre-commit
Kibitzer initialized.

# kibitzer shell integration — add to .bashrc or .zshrc
...
```

The shell integration snippet prints at the end. Copy it into your shell config (next step).

## 2. Add the prompt indicator

Add the snippet from `kibitzer init --shell` to your shell config:

```bash
# For zsh (~/.zshrc):
eval "$(kibitzer init --shell 2>/dev/null)"
PROMPT='$(_kibitzer_prompt) %# '

# For bash (~/.bashrc):
eval "$(kibitzer init --shell 2>/dev/null)"
PS1='$(_kibitzer_prompt) \$ '
```

Reload your shell:

```bash
source ~/.zshrc   # or ~/.bashrc
```

You should see the mode indicator in your prompt:

```
[kib:implement] $
```

## 3. Try switching modes

Kibitzer ships with 6 modes. Each constrains which paths are writable:

```bash
$ kibitzer mode
implement
  writable: src/, lib/, tests/, pyproject.toml

$ kibitzer mode test
implement → test

$ kibitzer mode
test
  writable: tests/, test/, spec/
```

Your prompt updates immediately:

```
[kib:test] $
```

Check the full status dashboard:

```bash
$ kibitzer status
mode:    test
turns:   0
calls:   0
fails:   0 consecutive
edits:   0 since last test
prev:    implement (1 switches)
```

## 4. See the pre-commit guard in action

While in `test` mode, try committing a source file:

```bash
$ kibitzer mode test
$ echo "# change" >> src/main.py
$ git add src/main.py
$ git commit -m "this should be blocked"
Mode 'test' does not allow writes to:
  src/main.py
```

The commit is blocked. The guard validates staged files against the current mode's writable paths — the same rules the agent's path guard uses.

Switch to a mode that allows it:

```bash
$ kibitzer mode implement
test → implement

$ git commit -m "now it works"
[main abc1234] now it works
```

To bypass the guard in a pinch: `git commit --no-verify`.

## 5. Get command coaching

If you have [jetsam](https://github.com/teague/jetsam) or [blq](https://github.com/teague/lq) installed, kibitzer suggests structured alternatives for common shell commands:

```bash
$ kibitzer shell-post git commit -m "quick fix"
kibitzer: consider: jetsam save

$ kibitzer shell-post pytest tests/
kibitzer: consider: blq run test
```

With the shell integration installed (step 2), this happens automatically via `preexec` — you don't need to call `shell-post` manually.

For zsh, add to `~/.zshrc`:

```zsh
preexec_functions+=(_kibitzer_preexec)
```

For bash, you'll need [bash-preexec](https://github.com/rcaloras/bash-preexec), then add to `~/.bashrc`:

```bash
preexec_functions+=(_kibitzer_preexec)
```

The coaching is advisory. It never blocks or modifies your command.

## 6. Watch the failure indicator

When the agent (or you) hits 3+ consecutive failures, the prompt adds a `!` warning:

```
[kib:implement!] $
```

This is a signal to check what's going wrong — run `kibitzer status` to see the details, or `kibitzer mode explore` to switch to read-only investigation.

## 7. Use alongside Claude Code

Kibitzer's shell hooks and agent hooks share the same state. When you switch modes from the terminal, the agent sees it immediately. When the agent switches modes via the `ChangeToolMode` MCP tool, your prompt updates on the next render.

A typical workflow:

1. `kibitzer mode test` — set the constraint before the agent starts
2. Ask the agent to write tests — it can only edit `tests/`
3. `kibitzer mode implement` — open up source files
4. Ask the agent to implement — it can edit `src/` and `tests/`
5. The pre-commit guard catches any files that shouldn't have changed

## What's in `.kibitzer/`

```
.kibitzer/
  config.toml    — project configuration (edit to customize modes)
  state.json     — runtime state (mode, counters — don't edit by hand)
  store.sqlite   — event log (queryable, append-only)
```

Events from shell commands are tagged with `source: "shell"` in the store, so you can distinguish them from agent activity:

```bash
sqlite3 .kibitzer/store.sqlite "SELECT event_type, source, data FROM events ORDER BY id DESC LIMIT 5"
```

## Next steps

- [Shell Hooks reference](shell-hooks.md) — full CLI reference and architecture details
- [Modes](modes.md) — the 6 built-in modes and how to customize them
- [Configuration](configuration.md) — full config.toml reference
- [Integration](integration.md) — how kibitzer works with blq, jetsam, fledgling, and umwelt
