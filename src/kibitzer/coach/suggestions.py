"""Generate and dedup coach suggestions."""

from __future__ import annotations

from typing import Any

from kibitzer.coach.observer import detect_patterns


def should_fire(state: dict[str, Any], config: dict) -> bool:
    """Check if the coach should fire on this call."""
    if not config.get("coach", {}).get("enabled", True):
        return False
    frequency = config.get("coach", {}).get("frequency", 5)
    model = state.get("model")
    if model:
        overrides = config.get("coach", {}).get("model_overrides", {})
        if model in overrides:
            frequency = overrides[model].get("frequency", frequency)
    return state.get("total_calls", 0) > 0 and state["total_calls"] % frequency == 0


def generate_suggestions(state: dict[str, Any]) -> list[str]:
    """Generate new suggestions, filtering out already-given ones.

    Mutates state["suggestions_given"] to track what's been suggested.
    """
    already_given = set(state.get("suggestions_given", []))
    patterns = detect_patterns(state)

    new_suggestions = []
    for pattern_id, message in patterns:
        if pattern_id not in already_given:
            new_suggestions.append(message)
            state.setdefault("suggestions_given", []).append(pattern_id)

    return new_suggestions
