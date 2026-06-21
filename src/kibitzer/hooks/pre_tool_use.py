"""PreToolUse hook — thin wrapper around KibitzerSession."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from kibitzer.session import KibitzerSession


def _session_id(hook_input: dict) -> str | None:
    """Claude Code passes session_id on stdin; fall back to the transcript
    filename stem (the session UUID) if a payload ever omits it."""
    sid = hook_input.get("session_id")
    if sid:
        return sid
    tp = hook_input.get("transcript_path")
    return Path(tp).stem if tp else None


def main() -> None:
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError):
        return

    with KibitzerSession(safe_mode=True, session_id=_session_id(hook_input)) as session:
        result = session.before_call(
            tool_name=hook_input.get("tool_name", ""),
            tool_input=hook_input.get("tool_input", {}),
        )

    if result is not None:
        output = result.to_hook_output("PreToolUse")
        if output:
            print(json.dumps(output))


# Backwards-compatible wrapper for existing tests
def handle_pre_tool_use(hook_input, project_dir=None, plugin_modes=None):
    """Compatibility wrapper — delegates to KibitzerSession."""
    session = KibitzerSession(project_dir=project_dir)
    session.load()
    if plugin_modes is not None:
        for name, mode in plugin_modes.items():
            session._config.setdefault("plugins", {}).setdefault(name, {})["mode"] = mode
    result = session.before_call(
        hook_input.get("tool_name", ""),
        hook_input.get("tool_input", {}),
    )
    session.save()
    if result is None:
        return None
    return result.to_hook_output("PreToolUse") or None


if __name__ == "__main__":
    main()
