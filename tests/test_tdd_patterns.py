"""Tests for TDD-related coach patterns: test overfit and implement-before-test."""

from kibitzer.state import fresh_state
from kibitzer.controller.mode_controller import update_counters
from kibitzer.coach.observer import detect_patterns
from kibitzer.coach.suggestions import generate_suggestions


# ===========================================================================
# Counter tracking
# ===========================================================================

class TestTestFileEditTracking:
    def test_edit_test_file_tracked(self):
        state = fresh_state()
        update_counters(state, "Edit", True, tool_input={"file_path": "tests/test_auth.py"})
        assert state["test_file_edits"] == {"tests/test_auth.py": 1}

    def test_edit_test_file_accumulates(self):
        state = fresh_state()
        for _ in range(3):
            update_counters(state, "Edit", True, tool_input={"file_path": "tests/test_auth.py"})
        assert state["test_file_edits"]["tests/test_auth.py"] == 3

    def test_edit_src_file_not_tracked(self):
        state = fresh_state()
        update_counters(state, "Edit", True, tool_input={"file_path": "src/auth.py"})
        assert state["test_file_edits"] == {}

    def test_multiple_test_files_tracked(self):
        state = fresh_state()
        update_counters(state, "Edit", True, tool_input={"file_path": "tests/test_a.py"})
        update_counters(state, "Edit", True, tool_input={"file_path": "tests/test_b.py"})
        update_counters(state, "Edit", True, tool_input={"file_path": "tests/test_a.py"})
        assert state["test_file_edits"]["tests/test_a.py"] == 2
        assert state["test_file_edits"]["tests/test_b.py"] == 1

    def test_spec_dir_tracked(self):
        state = fresh_state()
        update_counters(state, "Edit", True, tool_input={"file_path": "spec/auth_spec.rb"})
        assert state["test_file_edits"]["spec/auth_spec.rb"] == 1

    def test_test_dir_tracked(self):
        state = fresh_state()
        update_counters(state, "Edit", True, tool_input={"file_path": "test/test_auth.js"})
        assert state["test_file_edits"]["test/test_auth.js"] == 1


class TestFirstEditTypeTracking:
    def test_first_edit_source(self):
        state = fresh_state()
        update_counters(state, "Edit", True, tool_input={"file_path": "src/auth.py"})
        assert state["first_edit_type"] == "source"

    def test_first_edit_test(self):
        state = fresh_state()
        update_counters(state, "Edit", True, tool_input={"file_path": "tests/test_auth.py"})
        assert state["first_edit_type"] == "test"

    def test_first_edit_preserved(self):
        """Once set, first_edit_type doesn't change."""
        state = fresh_state()
        update_counters(state, "Edit", True, tool_input={"file_path": "src/auth.py"})
        update_counters(state, "Edit", True, tool_input={"file_path": "tests/test_auth.py"})
        assert state["first_edit_type"] == "source"

    def test_read_doesnt_set_first_edit(self):
        state = fresh_state()
        update_counters(state, "Read", True)
        assert state["first_edit_type"] is None


# ===========================================================================
# Pattern detection
# ===========================================================================

class TestTestOverfitPattern:
    def test_fires_at_threshold(self):
        state = fresh_state()
        state["test_file_edits"] = {"tests/test_auth.py": 3}
        patterns = detect_patterns(state)
        matching = [(pid, msg) for pid, msg in patterns if pid == "test_overfit"]
        assert len(matching) == 1
        assert "test_auth.py" in matching[0][1]
        assert "3 times" in matching[0][1]

    def test_does_not_fire_below_threshold(self):
        state = fresh_state()
        state["test_file_edits"] = {"tests/test_auth.py": 2}
        patterns = detect_patterns(state)
        matching = [pid for pid, _ in patterns if pid == "test_overfit"]
        assert len(matching) == 0

    def test_fires_for_worst_offender(self):
        """Multiple test files: fires for the one over threshold."""
        state = fresh_state()
        state["test_file_edits"] = {
            "tests/test_a.py": 1,
            "tests/test_b.py": 4,
        }
        patterns = detect_patterns(state)
        matching = [(pid, msg) for pid, msg in patterns if pid == "test_overfit"]
        assert len(matching) == 1
        assert "test_b.py" in matching[0][1]

    def test_only_one_suggestion_even_with_multiple(self):
        """Even if multiple test files are over threshold, only one suggestion."""
        state = fresh_state()
        state["test_file_edits"] = {
            "tests/test_a.py": 5,
            "tests/test_b.py": 4,
        }
        patterns = detect_patterns(state)
        matching = [pid for pid, _ in patterns if pid == "test_overfit"]
        assert len(matching) == 1

    def test_deduped(self):
        state = fresh_state()
        state["test_file_edits"] = {"tests/test_auth.py": 5}
        state["suggestions_given"] = ["test_overfit"]
        suggestions = generate_suggestions(state)
        test_suggestions = [s for s in suggestions if "test_auth" in s]
        assert len(test_suggestions) == 0


class TestImplementBeforeTestPattern:
    def test_fires_when_source_first(self):
        state = fresh_state()
        state["first_edit_type"] = "source"
        state["total_calls"] = 15
        patterns = detect_patterns(state)
        matching = [(pid, msg) for pid, msg in patterns if pid == "implement_before_test"]
        assert len(matching) == 1
        assert "failing test" in matching[0][1]

    def test_does_not_fire_when_test_first(self):
        state = fresh_state()
        state["first_edit_type"] = "test"
        state["total_calls"] = 15
        patterns = detect_patterns(state)
        matching = [pid for pid, _ in patterns if pid == "implement_before_test"]
        assert len(matching) == 0

    def test_does_not_fire_early(self):
        """Don't nag about ordering until enough calls have happened."""
        state = fresh_state()
        state["first_edit_type"] = "source"
        state["total_calls"] = 3
        patterns = detect_patterns(state)
        matching = [pid for pid, _ in patterns if pid == "implement_before_test"]
        assert len(matching) == 0

    def test_does_not_fire_when_no_edits(self):
        state = fresh_state()
        state["total_calls"] = 15
        patterns = detect_patterns(state)
        matching = [pid for pid, _ in patterns if pid == "implement_before_test"]
        assert len(matching) == 0

    def test_deduped(self):
        state = fresh_state()
        state["first_edit_type"] = "source"
        state["total_calls"] = 15
        state["suggestions_given"] = ["implement_before_test"]
        suggestions = generate_suggestions(state)
        impl_suggestions = [s for s in suggestions if "failing test" in s]
        assert len(impl_suggestions) == 0


# ===========================================================================
# Full cycle
# ===========================================================================

class TestTDDFullCycle:
    def test_proper_tdd_no_warnings(self):
        """Write test first, then implement — no TDD warnings."""
        state = fresh_state()
        # Write test first
        update_counters(state, "Edit", True, tool_input={"file_path": "tests/test_auth.py"})
        # Run tests (they fail)
        update_counters(state, "Bash", False, tool_input={"command": "pytest tests/"})
        # Implement
        update_counters(state, "Edit", True, tool_input={"file_path": "src/auth.py"})
        update_counters(state, "Edit", True, tool_input={"file_path": "src/auth.py"})
        # Run tests (they pass)
        update_counters(state, "Bash", True, tool_input={"command": "pytest tests/"})

        patterns = detect_patterns(state)
        assert not any(pid == "implement_before_test" for pid, _ in patterns)
        assert not any(pid == "test_overfit" for pid, _ in patterns)

    def test_anti_tdd_triggers_warning(self):
        """Implement first, then adjust test 3 times — both warnings fire."""
        state = fresh_state()
        # Implement first (wrong order)
        update_counters(state, "Edit", True, tool_input={"file_path": "src/auth.py"})
        # Write test
        update_counters(state, "Edit", True, tool_input={"file_path": "tests/test_auth.py"})
        # Adjust test repeatedly (overfitting)
        update_counters(state, "Edit", True, tool_input={"file_path": "tests/test_auth.py"})
        update_counters(state, "Edit", True, tool_input={"file_path": "tests/test_auth.py"})

        # Need enough calls for implement_before_test
        for _ in range(8):
            update_counters(state, "Read", True)

        patterns = detect_patterns(state)
        pids = [pid for pid, _ in patterns]
        assert "test_overfit" in pids
        assert "implement_before_test" in pids
