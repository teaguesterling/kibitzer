"""Discover available tools from .mcp.json and CLI availability.

Used by the coach to only suggest tools the agent actually has access to.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

# Map MCP server names to the tools they provide.
# Keys are substrings matched against .mcp.json server names.
_MCP_SERVER_TOOLS: dict[str, list[str]] = {
    "fledgling": ["FindDefinitions", "CodeStructure", "FindCallers", "ReadLines"],
    "blq": ["blq run test", "blq errors", "blq status"],
    "jetsam": ["jetsam save", "jetsam sync", "jetsam diff", "jetsam log"],
}

# Map CLI binaries to tools (fallback when no .mcp.json)
_CLI_TOOLS: dict[str, list[str]] = {
    "fledgling": ["FindDefinitions", "CodeStructure", "FindCallers"],
    "blq": ["blq run test", "blq errors"],
    "jetsam": ["jetsam save", "jetsam sync", "jetsam diff"],
}


def discover_tools(project_dir: Path | None = None) -> dict[str, Any]:
    """Discover available tools from .mcp.json and CLI.

    Returns:
        {
            "servers": ["blq", "jetsam", "fledgling"],  # registered MCP servers
            "tools": ["blq run test", "jetsam save", "FindDefinitions", ...],
            "has_fledgling": True,
            "has_blq": True,
            "has_jetsam": True,
        }
    """
    if project_dir is None:
        project_dir = Path.cwd()

    servers = _read_mcp_servers(project_dir)
    tools: list[str] = []
    has: dict[str, bool] = {"has_fledgling": False, "has_blq": False, "has_jetsam": False}

    # Check MCP servers first (authoritative — these are what the agent sees)
    for server_name in servers:
        for key, server_tools in _MCP_SERVER_TOOLS.items():
            if key in server_name.lower():
                tools.extend(server_tools)
                has[f"has_{key}"] = True

    # Fall back to CLI availability for tools not found via MCP
    for binary, cli_tools in _CLI_TOOLS.items():
        key = f"has_{binary}"
        if not has.get(key) and shutil.which(binary) is not None:
            tools.extend(cli_tools)
            has[key] = True

    return {
        "servers": servers,
        "tools": sorted(set(tools)),
        **has,
    }


def _read_mcp_servers(project_dir: Path) -> list[str]:
    """Read MCP server names from .mcp.json."""
    mcp_path = project_dir / ".mcp.json"
    if not mcp_path.exists():
        return []

    try:
        data = json.loads(mcp_path.read_text())
        return list(data.get("mcpServers", {}).keys())
    except (json.JSONDecodeError, OSError):
        return []


def suggest_search_tool(available: dict[str, Any]) -> str | None:
    """Return a suggestion for code search, or None if no semantic tools available."""
    if available.get("has_fledgling"):
        return "FindDefinitions shows all functions and classes across the codebase in one call."
    return None


def suggest_test_tool(available: dict[str, Any]) -> str | None:
    """Return a suggestion for running tests, or None if no structured test tool."""
    if available.get("has_blq"):
        return "blq run test captures structured output, queryable via blq errors."
    return None


def suggest_save_tool(available: dict[str, Any]) -> str | None:
    """Return a suggestion for saving work, or None if no workflow tool."""
    if available.get("has_jetsam"):
        return "jetsam save provides atomic saves with plan tracking."
    return None
