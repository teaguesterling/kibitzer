"""CLI entry point for kibitzer."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import click

from kibitzer.config import DEFAULT_CONFIG_PATH
from kibitzer.hooks.templates import write_hook_scripts
from kibitzer.state import fresh_state, load_state, save_state


@click.group()
def cli():
    """Kibitzer — watches agent tool calls and suggests structured alternatives."""
    pass


@cli.command()
@click.option("--hooks/--no-hooks", default=True, help="Install Claude Code hooks")
@click.option("--mcp/--no-mcp", default=False, help="Create .mcp.json for MCP server")
@click.option("--shell", is_flag=True, default=False,
              help="Print shell integration snippet (bash/zsh)")
@click.option("--git-hooks", is_flag=True, default=False,
              help="Install git pre-commit hook")
def init(hooks: bool, mcp: bool, shell: bool, git_hooks: bool):
    """Initialize kibitzer in the current project."""
    project_dir = Path.cwd()

    kibitzer_dir = project_dir / ".kibitzer"
    kibitzer_dir.mkdir(exist_ok=True)

    config_dest = kibitzer_dir / "config.toml"
    if not config_dest.exists():
        shutil.copy2(DEFAULT_CONFIG_PATH, config_dest)
        click.echo(f"Created {config_dest}")
    else:
        click.echo(f"Config already exists: {config_dest}")

    state_file = kibitzer_dir / "state.json"
    if not state_file.exists():
        save_state(fresh_state(), kibitzer_dir)
        click.echo(f"Created {state_file}")

    if hooks:
        hooks_dir = project_dir / ".claude" / "hooks"
        pre_path, post_path = write_hook_scripts(hooks_dir)
        click.echo(f"Created {pre_path}")
        click.echo(f"Created {post_path}")
        _merge_settings(project_dir, pre_path, post_path)

    if mcp:
        _write_mcp_json(project_dir)

    if git_hooks:
        from kibitzer.hooks.templates import write_git_pre_commit_hook
        hook_path = write_git_pre_commit_hook(project_dir)
        click.echo(f"Created {hook_path}")

    click.echo("Kibitzer initialized.")

    if shell:
        from kibitzer.hooks.templates import shell_integration_snippet
        click.echo(shell_integration_snippet())


def _merge_settings(project_dir: Path, pre_path: Path, post_path: Path) -> None:
    """Merge kibitzer hooks into .claude/settings.json using relative paths."""
    settings_path = project_dir / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    if settings_path.exists():
        settings = json.loads(settings_path.read_text())
    else:
        settings = {}

    # Use relative paths from project root for portability
    rel_pre = str(pre_path.relative_to(project_dir))
    rel_post = str(post_path.relative_to(project_dir))

    settings.setdefault("hooks", {})
    settings["hooks"].setdefault("PreToolUse", [])
    settings["hooks"].setdefault("PostToolUse", [])

    pre_entry = {
        "matcher": "",
        "hooks": [{"type": "command", "command": rel_pre}],
    }
    post_entry = {
        "matcher": "",
        "hooks": [{"type": "command", "command": rel_post}],
    }

    # Idempotent: remove existing kibitzer entries before adding
    settings["hooks"]["PreToolUse"] = [
        m for m in settings["hooks"]["PreToolUse"]
        if not any("kibitzer" in h.get("command", "") for h in m.get("hooks", []))
    ]
    settings["hooks"]["PreToolUse"].append(pre_entry)

    settings["hooks"]["PostToolUse"] = [
        m for m in settings["hooks"]["PostToolUse"]
        if not any("kibitzer" in h.get("command", "") for h in m.get("hooks", []))
    ]
    settings["hooks"]["PostToolUse"].append(post_entry)

    settings_path.write_text(json.dumps(settings, indent=2))
    click.echo(f"Updated {settings_path}")


def _write_mcp_json(project_dir: Path) -> None:
    """Write kibitzer MCP server entry, using the python that ran init."""
    import sys

    mcp_path = project_dir / ".mcp.json"

    if mcp_path.exists():
        try:
            data = json.loads(mcp_path.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
    else:
        data = {}

    data.setdefault("mcpServers", {})
    data["mcpServers"]["kibitzer"] = {
        "command": sys.executable,
        "args": ["-m", "kibitzer", "serve"],
    }

    mcp_path.write_text(json.dumps(data, indent=2))
    click.echo(f"Created {mcp_path}")


@cli.command()
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse"]),
    default="stdio",
    help="MCP transport type",
)
def serve(transport: str):
    """Run the kibitzer MCP server."""
    from kibitzer.mcp.server import create_mcp_server
    mcp = create_mcp_server()
    mcp.run(transport=transport)


# --- Human-facing CLI commands ---


def _open_session():
    """Create and load a KibitzerSession for CLI use."""
    from kibitzer.session import KibitzerSession
    session = KibitzerSession(project_dir=Path.cwd())
    session.load()
    return session


@cli.command()
@click.argument("new_mode", required=False)
def mode(new_mode: str | None):
    """Get or set the current kibitzer mode."""
    session = _open_session()
    if new_mode is None:
        policy = session._resolve_mode_policy(session.mode)
        click.echo(f"{session.mode}")
        writable = policy.get("writable", ["*"])
        if writable != ["*"]:
            click.echo(f"  writable: {', '.join(writable)}")
        strategy = policy.get("strategy", "")
        if strategy:
            click.echo(f"  strategy: {strategy}")
    else:
        result = session.change_mode(new_mode, reason="cli")
        session.save()
        if "error" in result:
            raise click.ClickException(result["error"])
        click.echo(f"{result['previous_mode']} → {result['new_mode']}")


@cli.command()
def status():
    """Show kibitzer session status."""
    project_dir = Path.cwd()
    state_dir = project_dir / ".kibitzer"
    state = load_state(state_dir)

    click.echo(f"mode:    {state.get('mode', 'implement')}")
    click.echo(f"turns:   {state.get('turns_in_mode', 0)}")
    click.echo(f"calls:   {state.get('total_calls', 0)}")
    click.echo(f"fails:   {state.get('consecutive_failures', 0)} consecutive")
    click.echo(f"edits:   {state.get('edits_since_test', 0)} since last test")
    switches = state.get("mode_switches", 0)
    prev = state.get("previous_mode")
    if prev:
        click.echo(f"prev:    {prev} ({switches} switches)")


@cli.command()
@click.option("--format", "fmt", type=click.Choice(["text", "json"]),
              default="text", help="Output format")
def prompt(fmt: str):
    """Print a short mode indicator for shell prompt integration."""
    project_dir = Path.cwd()
    state_dir = project_dir / ".kibitzer"
    state = load_state(state_dir)
    mode_name = state.get("mode", "implement")
    fails = state.get("consecutive_failures", 0)

    if fmt == "json":
        click.echo(json.dumps({"mode": mode_name, "consecutive_failures": fails}))
    else:
        indicator = mode_name
        if fails >= 3:
            indicator += "!"
        click.echo(indicator)


@cli.command(name="validate-staged")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]),
              default="text", help="Output format")
def validate_staged(fmt: str):
    """Check staged git files against mode writable paths. Exit 1 if violations."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            raise click.ClickException("git diff --cached failed")
        staged = [f.strip() for f in result.stdout.splitlines() if f.strip()]
    except FileNotFoundError:
        raise click.ClickException("git not found")
    except subprocess.TimeoutExpired:
        raise click.ClickException("git diff --cached timed out")

    if not staged:
        if fmt == "json":
            click.echo(json.dumps({"ok": True, "violations": []}))
        return

    session = _open_session()
    policy = session._resolve_mode_policy(session.mode)

    from kibitzer.guards.path_guard import check_path
    violations = []
    for path in staged:
        guard_result = check_path(path, policy)
        if not guard_result.allowed:
            violations.append({"path": path, "reason": guard_result.reason})

    if fmt == "json":
        click.echo(json.dumps({"ok": not violations, "violations": violations}))
    elif violations:
        click.echo(
            f"Mode '{session.mode}' does not allow writes to:",
            err=True,
        )
        for v in violations:
            click.echo(f"  {v['path']}", err=True)
        raise SystemExit(1)


@cli.command(name="shell-post", context_settings={"ignore_unknown_options": True})
@click.argument("command_parts", nargs=-1, type=click.UNPROCESSED, required=True)
def shell_post(command_parts: tuple[str, ...]):
    """Coaching feedback for a shell command. Called from preexec/precmd hooks."""
    command_line = " ".join(command_parts)
    session = _open_session()
    suggestions = _coach_shell_command(session, command_line)
    if suggestions:
        click.echo("kibitzer: " + suggestions[0], err=True)


def _coach_shell_command(session, command_line: str) -> list[str]:
    """Generate suggestions for a human shell command."""
    parts = command_line.strip().split()
    if not parts:
        return []
    cmd = parts[0]

    suggestions = []

    if cmd == "git" and len(parts) > 1:
        subcmd = parts[1]
        if subcmd in ("add", "commit", "push", "checkout", "reset"):
            try:
                import shutil as _shutil
                if _shutil.which("jetsam"):
                    jetsam_map = {
                        "add": "jetsam save",
                        "commit": "jetsam save",
                        "push": "jetsam sync",
                        "checkout": "jetsam switch",
                        "reset": "jetsam sync",
                    }
                    alt = jetsam_map.get(subcmd)
                    if alt:
                        suggestions.append(f"consider: {alt}")
            except Exception:
                pass

    if cmd == "pytest" or (cmd == "python" and "-m" in parts and "pytest" in parts):
        try:
            import shutil as _shutil
            if _shutil.which("blq"):
                suggestions.append("consider: blq run test")
        except Exception:
            pass

    session.log_event(
        event_type="shell_command",
        data=json.dumps({"command": command_line}),
        source="shell",
    )

    return suggestions
