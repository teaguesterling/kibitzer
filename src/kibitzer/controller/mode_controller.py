"""Failure-driven mode transitions with oscillation guard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

_NO_AUTO_TRANSITION = {"free"}
_MIN_TURNS_BEFORE_RETURN = 5
_MAX_MODE_SWITCHES = 6


@dataclass
class Transition:
    target: str
    reason: str


_TEST_COMMANDS = {"pytest", "python -m pytest", "npm test", "cargo test", "go test", "make test"}
_EDIT_TOOLS = {"Edit", "Write", "NotebookEdit"}
_SEARCH_TOOLS = {"Read", "Grep", "Glob"}
_STRUCTURED_TOOLS = {"Edit", "Write", "Grep", "Read"}
_SEMANTIC_TOOLS = {"mcp__fledgling__FindDefinitions", "mcp__fledgling__FindCallers",
                   "mcp__fledgling__CodeStructure", "FindDefinitions", "FindCallers",
                   "CodeStructure"}


def _is_test_command(command: str) -> bool:
    """Check if a bash command looks like a test run."""
    return any(trigger in command for trigger in _TEST_COMMANDS)


def update_counters(
    state: dict[str, Any],
    tool_name: str,
    success: bool,
    tool_input: dict[str, Any] | None = None,
) -> None:
    """Update all counters in state after a tool call."""
    if tool_input is None:
        tool_input = {}

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

    # --- Coach observation counters ---

    # Obs 1: Consecutive edit failures on the same file
    if tool_name in _EDIT_TOOLS:
        if not success:
            failed_file = tool_input.get("file_path", "")
            if failed_file == state.get("last_failed_edit_file", ""):
                state["consecutive_edit_failures"] = state.get("consecutive_edit_failures", 0) + 1
            else:
                state["consecutive_edit_failures"] = 1
                state["last_failed_edit_file"] = failed_file
        else:
            state["consecutive_edit_failures"] = 0

    # Obs 2: Consecutive reads
    if tool_name == "Read":
        state["consecutive_reads"] = state.get("consecutive_reads", 0) + 1
    else:
        state["consecutive_reads"] = 0

    # Obs 3: Edits since test
    if tool_name in _EDIT_TOOLS:
        state["edits_since_test"] = state.get("edits_since_test", 0) + 1
    elif tool_name == "Bash" and _is_test_command(tool_input.get("command", "")):
        state["edits_since_test"] = 0

    # Obs 4: Semantic tool usage
    if tool_name in _SEMANTIC_TOOLS:
        state["semantic_tools_used"] = True

    # Obs 7: Last edit turn
    if tool_name in _EDIT_TOOLS:
        state["last_edit_turn"] = state["total_calls"]

    # Bash-heavy: track bash calls without structured tools
    if tool_name == "Bash":
        state["bash_without_structured"] = state.get("bash_without_structured", 0) + 1
    elif tool_name in _STRUCTURED_TOOLS:
        state["bash_without_structured"] = 0

    # Test overfit: track edits to test files
    _TEST_PREFIXES = ("tests/", "test/", "spec/")
    if tool_name in _EDIT_TOOLS:
        file_path = tool_input.get("file_path", "") or tool_input.get("notebook_path", "")
        if file_path and any(file_path.startswith(p) for p in _TEST_PREFIXES):
            edits = state.get("test_file_edits", {})
            edits[file_path] = edits.get(file_path, 0) + 1
            state["test_file_edits"] = edits
            if state.get("first_edit_type") is None:
                state["first_edit_type"] = "test"
        elif file_path:
            if state.get("first_edit_type") is None:
                state["first_edit_type"] = "source"


def should_transition(state: dict[str, Any], target: str) -> bool:
    """Check if an auto-transition is safe (no oscillation, not too many switches)."""
    if state["mode_switches"] >= _MAX_MODE_SWITCHES:
        return False
    # Don't switch back to a mode we just left if we barely spent time there
    if (state.get("previous_mode") == target
            and state.get("turns_in_previous_mode", 0) < _MIN_TURNS_BEFORE_RETURN):
        return False
    # After the first switch, don't switch out of current mode too quickly
    if (state.get("mode_switches", 0) > 0
            and state.get("turns_in_mode", 0) < _MIN_TURNS_BEFORE_RETURN):
        return False
    return True


def check_transitions(
    state: dict[str, Any],
    config: dict,
    max_consecutive_failures: int | None = None,
    max_turns: int | None = None,
) -> Optional[Transition]:
    """Check if an automatic mode transition should fire.

    Args:
        max_consecutive_failures: Override from PolicyConsumer. When
            provided, takes precedence over config["controller"].
        max_turns: Override for max turns in explore mode.
    """
    mode = state["mode"]
    if mode in _NO_AUTO_TRANSITION:
        return None
    controller = config.get("controller", {})
    eff_max_failures = (
        max_consecutive_failures
        or controller.get("max_consecutive_failures", 3)
    )
    eff_max_turns = max_turns or controller.get("max_turns_in_explore", 20)

    if mode not in ("explore", "review") and state["consecutive_failures"] >= eff_max_failures:
        if should_transition(state, "explore"):
            return Transition(target="explore", reason=f"Too many consecutive failures ({state['consecutive_failures']})")

    if mode == "explore" and state["turns_in_mode"] >= eff_max_turns:
        if should_transition(state, "implement"):
            return Transition(target="implement", reason=f"Extended exploration ({state['turns_in_mode']} turns) — time to try fixing")

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
