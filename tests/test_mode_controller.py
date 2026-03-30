import pytest
from kibitzer.state import fresh_state
from kibitzer.config import load_config
from kibitzer.controller.mode_controller import (
    update_counters,
    check_transitions,
    should_transition,
)
from pathlib import Path


@pytest.fixture
def config():
    return load_config(project_dir=Path("/nonexistent"))


class TestUpdateCounters:
    def test_increments_total_calls(self):
        state = fresh_state()
        update_counters(state, tool_name="Edit", success=True)
        assert state["total_calls"] == 1

    def test_increments_turns_in_mode(self):
        state = fresh_state()
        update_counters(state, tool_name="Edit", success=True)
        assert state["turns_in_mode"] == 1

    def test_tracks_tool_usage(self):
        state = fresh_state()
        update_counters(state, tool_name="Edit", success=True)
        update_counters(state, tool_name="Edit", success=True)
        update_counters(state, tool_name="Read", success=True)
        assert state["tools_used_in_mode"]["Edit"] == 2
        assert state["tools_used_in_mode"]["Read"] == 1

    def test_success_resets_consecutive_failures(self):
        state = fresh_state()
        state["consecutive_failures"] = 3
        update_counters(state, tool_name="Edit", success=True)
        assert state["consecutive_failures"] == 0
        assert state["success_count"] == 1

    def test_failure_increments_consecutive(self):
        state = fresh_state()
        update_counters(state, tool_name="Bash", success=False)
        assert state["consecutive_failures"] == 1
        assert state["failure_count"] == 1

    def test_failure_streak(self):
        state = fresh_state()
        for _ in range(4):
            update_counters(state, tool_name="Bash", success=False)
        assert state["consecutive_failures"] == 4
        assert state["failure_count"] == 4


class TestShouldTransition:
    def test_allows_normal_transition(self):
        state = fresh_state()
        assert should_transition(state, "explore")

    def test_blocks_oscillation_to_previous_mode(self):
        state = fresh_state()
        state["previous_mode"] = "explore"
        state["turns_in_previous_mode"] = 2
        assert not should_transition(state, "explore")

    def test_allows_transition_after_enough_turns(self):
        state = fresh_state()
        state["previous_mode"] = "explore"
        state["turns_in_previous_mode"] = 10
        assert should_transition(state, "explore")

    def test_blocks_after_too_many_switches(self):
        state = fresh_state()
        state["mode_switches"] = 7
        assert not should_transition(state, "explore")


class TestCheckTransitions:
    def test_switch_to_debug_on_consecutive_failures(self, config):
        state = fresh_state()
        state["mode"] = "implement"
        state["consecutive_failures"] = 4
        transition = check_transitions(state, config)
        assert transition is not None
        assert transition.target == "explore"

    def test_no_switch_below_threshold(self, config):
        state = fresh_state()
        state["mode"] = "implement"
        state["consecutive_failures"] = 2
        transition = check_transitions(state, config)
        assert transition is None

    def test_switch_out_of_debug_after_max_turns(self, config):
        state = fresh_state()
        state["mode"] = "explore"
        state["turns_in_mode"] = 25
        transition = check_transitions(state, config)
        assert transition is not None
        assert transition.target == "implement"

    def test_no_transition_in_free_mode(self, config):
        state = fresh_state()
        state["mode"] = "free"
        state["consecutive_failures"] = 10
        transition = check_transitions(state, config)
        assert transition is None

    def test_oscillation_guard_prevents_transition(self, config):
        state = fresh_state()
        state["mode"] = "implement"
        state["consecutive_failures"] = 4
        state["previous_mode"] = "explore"
        state["turns_in_previous_mode"] = 2
        transition = check_transitions(state, config)
        assert transition is None
