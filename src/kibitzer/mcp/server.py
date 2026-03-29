"""FastMCP server exposing ChangeToolMode and GetFeedback tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kibitzer.coach.suggestions import generate_suggestions
from kibitzer.config import get_mode_policy, load_config
from kibitzer.state import load_state, save_state


def change_tool_mode(
    mode: str,
    reason: str | None = None,
    project_dir: Path | None = None,
) -> dict[str, Any]:
    if project_dir is None:
        project_dir = Path.cwd()

    config = load_config(project_dir)

    if mode not in config.get("modes", {}):
        return {"error": f"Unknown mode: {mode}. Available: {list(config['modes'].keys())}"}

    state_dir = project_dir / ".kibitzer"
    state = load_state(state_dir)

    previous_mode = state["mode"]
    policy = get_mode_policy(config, mode)

    state["previous_mode"] = previous_mode
    state["turns_in_previous_mode"] = state["turns_in_mode"]
    state["mode"] = mode
    state["failure_count"] = 0
    state["success_count"] = 0
    state["consecutive_failures"] = 0
    state["turns_in_mode"] = 0
    state["mode_switches"] += 1
    state["tools_used_in_mode"] = {}

    save_state(state, state_dir)

    return {
        "previous_mode": previous_mode,
        "new_mode": mode,
        "writable": policy["writable"],
        "strategy": policy["strategy"],
    }


def get_feedback(
    status: bool = True,
    suggestions: bool = True,
    intercepts: bool = True,
    project_dir: Path | None = None,
) -> dict[str, Any]:
    if project_dir is None:
        project_dir = Path.cwd()

    config = load_config(project_dir)
    state_dir = project_dir / ".kibitzer"
    state = load_state(state_dir)

    result: dict[str, Any] = {}

    if status:
        policy = get_mode_policy(config, state["mode"])
        result["status"] = {
            "mode": state["mode"],
            "failure_count": state["failure_count"],
            "success_count": state["success_count"],
            "consecutive_failures": state["consecutive_failures"],
            "turns_in_mode": state["turns_in_mode"],
            "total_calls": state["total_calls"],
            "writable": policy["writable"],
        }

    if suggestions:
        result["suggestions"] = generate_suggestions(state, project_dir=project_dir)
        save_state(state, state_dir)

    if intercepts:
        result["intercepts"] = _read_intercept_log(project_dir)

    return result


def _read_intercept_log(project_dir: Path) -> dict[str, Any]:
    log_path = project_dir / ".kibitzer" / "intercept.log"
    entries = []
    if log_path.exists():
        for line in log_path.read_text().strip().split("\n"):
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    return {
        "total_observed": len(entries),
        "recent": entries[-10:],
    }


def create_mcp_server():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "kibitzer",
        instructions=(
            "Kibitzer watches your tool calls and suggests structured alternatives. "
            "Use ChangeToolMode to switch between modes (free, create, implement, "
            "test_dev, document, debug, review). Use GetFeedback to check status, "
            "get coaching suggestions, and see intercepted patterns."
        ),
    )

    @mcp.tool()
    def ChangeToolMode(mode: str, reason: str = "") -> str:
        """Switch kibitzer mode to change which file paths are writable.

        Args:
            mode: Target mode (free, create, implement, test_dev, document, debug, review)
            reason: Optional reason for the switch
        """
        result = change_tool_mode(mode, reason=reason or None)
        return json.dumps(result, indent=2)

    @mcp.tool()
    def GetFeedback(
        status: bool = True,
        suggestions: bool = True,
        intercepts: bool = True,
    ) -> str:
        """Get kibitzer feedback: current status, coaching suggestions, and intercepted patterns."""
        result = get_feedback(status=status, suggestions=suggestions, intercepts=intercepts)
        return json.dumps(result, indent=2)

    return mcp
