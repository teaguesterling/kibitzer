"""Generate and dedup coach suggestions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kibitzer.coach.observer import detect_patterns


def should_fire(
    state: dict[str, Any],
    config: dict,
    coaching_frequency: int | None = None,
) -> bool:
    """Check if the coach should fire on this call.

    Args:
        coaching_frequency: Override from PolicyConsumer. When provided,
            takes precedence over config["coach"]["frequency"].
    """
    if not config.get("coach", {}).get("enabled", True):
        return False
    frequency = coaching_frequency or config.get("coach", {}).get("frequency", 5)
    model = state.get("model")
    if model:
        overrides = config.get("coach", {}).get("model_overrides", {})
        if model in overrides:
            frequency = overrides[model].get("frequency", frequency)
    return state.get("total_calls", 0) > 0 and state["total_calls"] % frequency == 0


def generate_suggestions(
    state: dict[str, Any],
    project_dir: Path | None = None,
    mark_given: bool = True,
) -> list[str]:
    """Generate new suggestions, filtering out already-given ones.

    Args:
        state: Current state dict.
        project_dir: Project root for fledgling queries.
        mark_given: If True, marks suggestions as given in state (for dedup).
            Set False when called from MCP GetFeedback to avoid consuming
            the hook coach's dedup budget.
    """
    already_given = set(state.get("suggestions_given", []))
    patterns = detect_patterns(state, project_dir=project_dir)

    new_suggestions = []
    for pattern_id, message in patterns:
        if pattern_id not in already_given:
            new_suggestions.append(message)
            if mark_given:
                state.setdefault("suggestions_given", []).append(pattern_id)

    return new_suggestions
