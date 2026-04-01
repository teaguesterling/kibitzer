"""PostToolUse hook — thin wrapper around KibitzerSession."""

from __future__ import annotations

import json
import sys

from kibitzer.session import KibitzerSession


def _detect_success(hook_input):
    """Compatibility wrapper for existing tests."""
    tool_name = hook_input.get("tool_name", "")
    tool_result = hook_input.get("tool_result", "")
    if tool_name == "Bash" and isinstance(tool_result, dict):
        return tool_result.get("exitCode", 0) == 0
    if isinstance(tool_result, dict) and "error" in tool_result:
        return False
    return True


def main() -> None:
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError):
        return

    with KibitzerSession(safe_mode=True) as session:
        result = session.after_call(
            tool_name=hook_input.get("tool_name", ""),
            tool_input=hook_input.get("tool_input", {}),
            tool_result=hook_input.get("tool_result"),
        )

    if result is not None:
        output = result.to_hook_output("PostToolUse")
        if output:
            print(json.dumps(output))


# Backwards-compatible wrapper for existing tests
def handle_post_tool_use(hook_input, project_dir=None):
    """Compatibility wrapper — delegates to KibitzerSession."""
    session = KibitzerSession(project_dir=project_dir)
    session.load()
    result = session.after_call(
        hook_input.get("tool_name", ""),
        hook_input.get("tool_input", {}),
        tool_result=hook_input.get("tool_result"),
    )
    session.save()
    if result is None:
        return None
    return result.to_hook_output("PostToolUse") or None


if __name__ == "__main__":
    main()
