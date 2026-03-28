"""PostToolUse hook entry point. Chains counter update + mode controller + coach."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

from kibitzer.coach.suggestions import generate_suggestions, should_fire
from kibitzer.config import load_config
from kibitzer.controller.mode_controller import (
    apply_transition,
    check_transitions,
    update_counters,
)
from kibitzer.state import load_state, save_state


def _detect_success(hook_input: dict[str, Any]) -> bool:
    tool_name = hook_input.get("tool_name", "")
    tool_result = hook_input.get("tool_result", "")

    if tool_name == "Bash" and isinstance(tool_result, dict):
        return tool_result.get("exitCode", 0) == 0

    if isinstance(tool_result, dict) and "error" in tool_result:
        return False

    return True


def handle_post_tool_use(
    hook_input: dict[str, Any],
    project_dir: Path | None = None,
) -> Optional[dict]:
    if project_dir is None:
        project_dir = Path.cwd()

    config = load_config(project_dir)
    state_dir = project_dir / ".kibitzer"
    state = load_state(state_dir)

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})
    success = _detect_success(hook_input)

    update_counters(state, tool_name, success, tool_input=tool_input)

    messages = []

    transition = check_transitions(state, config)
    if transition is not None:
        apply_transition(state, transition)
        messages.append(
            f"[kibitzer] Mode switched to {transition.target}: {transition.reason}"
        )

    if should_fire(state, config):
        suggestions = generate_suggestions(state)
        for s in suggestions:
            messages.append(f"[kibitzer] {s}")

    save_state(state, state_dir)

    if messages:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": "\n".join(messages),
            }
        }
    return None


def main() -> None:
    hook_input = json.loads(sys.stdin.read())
    result = handle_post_tool_use(hook_input)
    if result is not None:
        print(json.dumps(result))


if __name__ == "__main__":
    main()
