"""Mode-based path protection for Edit/Write/NotebookEdit."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PathGuardResult:
    allowed: bool
    reason: str = ""


def check_path(file_path: str, mode_policy: dict) -> PathGuardResult:
    """Check if file_path is writable under the given mode policy."""
    writable = mode_policy.get("writable", [])

    if "*" in writable:
        return PathGuardResult(allowed=True)

    if not writable:
        return PathGuardResult(
            allowed=False,
            reason=(
                f"Current mode is read-only (tried to write: {file_path}). "
                "Use the ChangeToolMode tool to switch to a writable mode."
            ),
        )

    for prefix in writable:
        if file_path.startswith(prefix) or file_path == prefix:
            return PathGuardResult(allowed=True)

    return PathGuardResult(
        allowed=False,
        reason=(
            f"Path '{file_path}' is not writable in the current mode "
            f"(writable: {writable}). "
            "Use the ChangeToolMode tool to switch modes."
        ),
    )
