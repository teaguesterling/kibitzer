"""Detect tool usage patterns from state."""

from __future__ import annotations

from typing import Any

_EDIT_THRESHOLD = 3
_FAILURE_RATIO_THRESHOLD = 0.5
_MIN_CALLS_FOR_RATIO = 5
_OSCILLATION_THRESHOLD = 4


def detect_patterns(state: dict[str, Any]) -> list[tuple[str, str]]:
    """Detect patterns in state. Returns list of (pattern_id, message) tuples."""
    patterns = []
    tools = state.get("tools_used_in_mode", {})

    # Edit streak without tests
    edit_count = tools.get("Edit", 0) + tools.get("Write", 0)
    bash_count = tools.get("Bash", 0)
    if edit_count > _EDIT_THRESHOLD and bash_count == 0:
        patterns.append((
            "edit_without_test",
            f"You've edited {edit_count} files without running tests. "
            "Consider running tests to verify your changes.",
        ))

    # High failure ratio
    total = state.get("failure_count", 0) + state.get("success_count", 0)
    if total >= _MIN_CALLS_FOR_RATIO:
        ratio = state.get("failure_count", 0) / total
        if ratio > _FAILURE_RATIO_THRESHOLD:
            pct = int(ratio * 100)
            patterns.append((
                "high_failure_ratio",
                f"High failure rate ({pct}%). "
                "Consider stepping back to read before editing.",
            ))

    # Oscillation
    if state.get("mode_switches", 0) > _OSCILLATION_THRESHOLD:
        patterns.append((
            "oscillation",
            "Frequent mode switches. Consider using free mode for this task.",
        ))

    # Mode mismatch: editing in debug mode
    if state.get("mode") == "debug" and tools.get("Edit", 0) > 0:
        patterns.append((
            "debug_mode_edits",
            "You're editing files in debug mode. "
            "Use ChangeToolMode to switch to implement mode first.",
        ))

    return patterns
