import pytest
from kibitzer.state import fresh_state
from kibitzer.config import load_config
from kibitzer.coach.observer import detect_patterns
from kibitzer.coach.suggestions import generate_suggestions, should_fire
from pathlib import Path


@pytest.fixture
def config():
    return load_config(project_dir=Path("/nonexistent"))


class TestShouldFire:
    def test_fires_at_frequency(self, config):
        state = fresh_state()
        state["total_calls"] = 5
        assert should_fire(state, config)

    def test_does_not_fire_between(self, config):
        state = fresh_state()
        state["total_calls"] = 3
        assert not should_fire(state, config)

    def test_fires_at_multiples(self, config):
        state = fresh_state()
        state["total_calls"] = 10
        assert should_fire(state, config)

    def test_disabled_coach(self, config):
        config["coach"]["enabled"] = False
        state = fresh_state()
        state["total_calls"] = 5
        assert not should_fire(state, config)


class TestDetectPatterns:
    def test_edit_without_test(self):
        state = fresh_state()
        state["tools_used_in_mode"]["Edit"] = 4
        patterns = detect_patterns(state)
        # patterns is list of (pattern_id, message) tuples
        assert any("test" in msg.lower() for _, msg in patterns)

    def test_no_pattern_with_few_edits(self):
        state = fresh_state()
        state["tools_used_in_mode"]["Edit"] = 2
        patterns = detect_patterns(state)
        edit_patterns = [(pid, msg) for pid, msg in patterns if "test" in msg.lower()]
        assert len(edit_patterns) == 0

    def test_high_failure_ratio(self):
        state = fresh_state()
        state["failure_count"] = 4
        state["success_count"] = 3
        state["total_calls"] = 7
        patterns = detect_patterns(state)
        assert any("failure" in msg.lower() for _, msg in patterns)

    def test_no_high_failure_with_few_calls(self):
        state = fresh_state()
        state["failure_count"] = 2
        state["success_count"] = 1
        state["total_calls"] = 3
        patterns = detect_patterns(state)
        failure_patterns = [(pid, msg) for pid, msg in patterns if "failure" in msg.lower()]
        assert len(failure_patterns) == 0

    def test_oscillation_warning(self):
        state = fresh_state()
        state["mode_switches"] = 5
        patterns = detect_patterns(state)
        assert any("mode" in msg.lower() or "switch" in msg.lower() for _, msg in patterns)

    def test_mode_mismatch_debug_with_edits(self):
        state = fresh_state()
        state["mode"] = "debug"
        state["tools_used_in_mode"]["Edit"] = 2
        patterns = detect_patterns(state)
        assert any("debug" in msg.lower() for _, msg in patterns)


class TestGenerateSuggestions:
    def test_dedup_already_given(self):
        state = fresh_state()
        state["tools_used_in_mode"]["Edit"] = 5
        state["suggestions_given"] = ["edit_without_test"]
        suggestions = generate_suggestions(state)
        assert len(suggestions) == 0

    def test_returns_new_suggestions(self):
        state = fresh_state()
        state["tools_used_in_mode"]["Edit"] = 5
        state["suggestions_given"] = []
        suggestions = generate_suggestions(state)
        assert len(suggestions) > 0

    def test_marks_suggestions_as_given(self):
        state = fresh_state()
        state["tools_used_in_mode"]["Edit"] = 5
        state["suggestions_given"] = []
        generate_suggestions(state)
        assert len(state["suggestions_given"]) > 0
