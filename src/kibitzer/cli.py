"""CLI entry point: kibitzer init / kibitzer serve."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import click

from kibitzer.config import DEFAULT_CONFIG_PATH
from kibitzer.hooks.templates import write_hook_scripts
from kibitzer.state import fresh_state, save_state


@click.group()
def cli():
    """Kibitzer — watches agent tool calls and suggests structured alternatives."""
    pass


@cli.command()
@click.option("--hooks/--no-hooks", default=True, help="Install Claude Code hooks")
@click.option("--mcp/--no-mcp", default=False, help="Create .mcp.json for MCP server")
def init(hooks: bool, mcp: bool):
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

    click.echo("Kibitzer initialized.")


def _merge_settings(project_dir: Path, pre_path: Path, post_path: Path) -> None:
    settings_path = project_dir / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    if settings_path.exists():
        settings = json.loads(settings_path.read_text())
    else:
        settings = {}

    settings.setdefault("hooks", {})
    settings["hooks"].setdefault("PreToolUse", [])
    settings["hooks"].setdefault("PostToolUse", [])

    pre_entry = {
        "matcher": "",
        "hooks": [{"type": "command", "command": str(pre_path)}],
    }
    post_entry = {
        "matcher": "",
        "hooks": [{"type": "command", "command": str(post_path)}],
    }

    pre_commands = [
        h.get("hooks", [{}])[0].get("command", "")
        for h in settings["hooks"]["PreToolUse"]
    ]
    if str(pre_path) not in pre_commands:
        settings["hooks"]["PreToolUse"].append(pre_entry)

    post_commands = [
        h.get("hooks", [{}])[0].get("command", "")
        for h in settings["hooks"]["PostToolUse"]
    ]
    if str(post_path) not in post_commands:
        settings["hooks"]["PostToolUse"].append(post_entry)

    settings_path.write_text(json.dumps(settings, indent=2))
    click.echo(f"Updated {settings_path}")


def _write_mcp_json(project_dir: Path) -> None:
    mcp_path = project_dir / ".mcp.json"

    if mcp_path.exists():
        data = json.loads(mcp_path.read_text())
    else:
        data = {}

    data.setdefault("mcpServers", {})
    data["mcpServers"]["kibitzer"] = {
        "command": "python3",
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
