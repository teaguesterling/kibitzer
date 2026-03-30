"""PreToolUse hook entry point. Chains path guard + interceptors."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

from kibitzer.config import load_config, get_mode_policy
from kibitzer.guards.path_guard import check_path
from kibitzer.interceptors.base import InterceptMode
from kibitzer.interceptors.registry import build_registry
from kibitzer.state import load_state

_WRITE_TOOLS = {"Edit", "Write", "NotebookEdit"}
_INTERCEPT_TOOLS = {"Bash"}
_LOG_FILE = ".kibitzer/intercept.log"


def handle_pre_tool_use(
    hook_input: dict[str, Any],
    project_dir: Path | None = None,
    plugin_modes: dict[str, str] | None = None,
) -> Optional[dict]:
    """Process a PreToolUse hook call. Returns response dict or None (allow)."""
    if project_dir is None:
        project_dir = Path.cwd()

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})

    config = load_config(project_dir)
    state_dir = project_dir / ".kibitzer"
    state = load_state(state_dir)
    mode = state.get("mode", config["controller"].get("default_mode", "implement"))
    mode_policy = get_mode_policy(config, mode)

    # 1. Path guard for write tools
    if tool_name in _WRITE_TOOLS:
        file_path = tool_input.get("file_path", "")
        if file_path:
            result = check_path(file_path, mode_policy)
            if not result.allowed:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": result.reason,
                    }
                }

    # 2. Interceptors for Bash
    if tool_name in _INTERCEPT_TOOLS:
        command = tool_input.get("command", "")
        if command:
            if plugin_modes is None:
                plugin_modes = {}
                for name, pcfg in config.get("plugins", {}).items():
                    plugin_modes[name] = pcfg.get("mode", "observe")

            plugins = build_registry()
            for plugin in plugins:
                suggestion = plugin.check(command)
                if suggestion is None:
                    continue

                pmode = InterceptMode(plugin_modes.get(plugin.name, "observe"))

                if pmode == InterceptMode.OBSERVE:
                    _log_intercept(project_dir, command, suggestion)
                    return None

                if pmode == InterceptMode.SUGGEST:
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "additionalContext": (
                                f"[kibitzer] {suggestion.plugin} suggests: "
                                f"{suggestion.tool}\n"
                                f"Reason: {suggestion.reason}"
                            ),
                        }
                    }

                if pmode == InterceptMode.REDIRECT:
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": (
                                f"A structured alternative is available: "
                                f"{suggestion.tool}\n{suggestion.reason}"
                            ),
                        }
                    }

    return None


def _log_intercept(project_dir: Path, command: str, suggestion) -> None:
    log_path = project_dir / _LOG_FILE
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "bash_command": command[:200],
        "suggested_tool": suggestion.tool,
        "reason": suggestion.reason,
        "plugin": suggestion.plugin,
    }
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def main() -> None:
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError):
        return  # bad input — exit silently
    result = handle_pre_tool_use(hook_input)
    if result is not None:
        print(json.dumps(result))


if __name__ == "__main__":
    main()
