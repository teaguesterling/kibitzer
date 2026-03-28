"""Failure-driven mode transitions with oscillation guard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

_NO_AUTO_TRANSITION = {"free", "create"}
_MIN_TURNS_BEFORE_RETURN = 5
_MAX_MODE_SWITCHES = 6


@dataclass
class Transition:
    target: str
    reason: str


def update_counters(state: dict[str, Any], tool_name: str, success: bool) -> None:
    state["total_calls"] += 1
    state["turns_in_mode"] += 1
    tools = state["tools_used_in_mode"]
    tools[tool_name] = tools.get(tool_name, 0) + 1
    if success:
        state["success_count"] += 1
        state["consecutive_failures"] = 0
    else:
        state["failure_count"] += 1
        state["consecutive_failures"] += 1


def should_transition(state: dict[str, Any], target: str) -> bool:
    if state["mode_switches"] >= _MAX_MODE_SWITCHES:
        return False
    if (state.get("previous_mode") == target
            and state.get("turns_in_previous_mode", 0) < _MIN_TURNS_BEFORE_RETURN):
        return False
    return True


def check_transitions(state: dict[str, Any], config: dict) -> Optional[Transition]:
    mode = state["mode"]
    if mode in _NO_AUTO_TRANSITION:
        return None
    controller = config.get("controller", {})
    max_failures = controller.get("max_consecutive_failures", 3)
    max_debug_turns = controller.get("max_turns_in_debug", 20)

    if mode not in ("debug", "review") and state["consecutive_failures"] > max_failures:
        if should_transition(state, "debug"):
            return Transition(target="debug", reason=f"Too many consecutive failures ({state['consecutive_failures']})")

    if mode == "debug" and state["turns_in_mode"] > max_debug_turns:
        if should_transition(state, "implement"):
            return Transition(target="implement", reason=f"Extended diagnosis ({state['turns_in_mode']} turns) — time to try fixing")

    return None


def apply_transition(state: dict[str, Any], transition: Transition) -> None:
    state["previous_mode"] = state["mode"]
    state["turns_in_previous_mode"] = state["turns_in_mode"]
    state["mode"] = transition.target
    state["failure_count"] = 0
    state["success_count"] = 0
    state["consecutive_failures"] = 0
    state["turns_in_mode"] = 0
    state["mode_switches"] += 1
    state["tools_used_in_mode"] = {}
