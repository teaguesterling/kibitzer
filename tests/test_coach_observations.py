"""Tests for experiment-driven coach patterns.

Based on ~/Projects/judgementalmonad.com/drafts/kibitzer-coaching-observations.md:
1. Repeated edit failures (whitespace mismatch)
2. Sequential file reads
3. Edit without test (improved)
4. Ignored semantic tools
7. Analysis loop (Opus pattern)
"""

from kibitzer.state import fresh_state
from kibitzer.controller.mode_controller import update_counters
from kibitzer.coach.observer import detect_patterns
from kibitzer.coach.suggestions import generate_suggestions


# ===========================================================================
# Counter tracking — update_counters needs to track new fields
# ===========================================================================

class TestEditFailureTracking:
    """Observation 1: consecutive edit failures on the same file."""

    def test_edit_failure_increments_counter(self):
        state = fresh_state()
        update_counters(state, tool_name="Edit", success=False,
                        tool_input={"file_path": "src/foo.py"})
        assert state["consecutive_edit_failures"] == 1
        assert state["last_failed_edit_file"] == "src/foo.py"

    def test_edit_success_resets_counter(self):
        state = fresh_state()
        state["consecutive_edit_failures"] = 3
        update_counters(state, tool_name="Edit", success=True,
                        tool_input={"file_path": "src/foo.py"})
        assert state["consecutive_edit_failures"] == 0

    def test_different_tool_doesnt_reset(self):
        """A Read call shouldn't reset the edit failure counter."""
        state = fresh_state()
        state["consecutive_edit_failures"] = 2
        state["last_failed_edit_file"] = "src/foo.py"
        update_counters(state, tool_name="Read", success=True)
        assert state["consecutive_edit_failures"] == 2

    def test_edit_failure_different_file_resets(self):
        """Failing on a different file resets the counter."""
        state = fresh_state()
        state["consecutive_edit_failures"] = 2
        state["last_failed_edit_file"] = "src/foo.py"
        update_counters(state, tool_name="Edit", success=False,
                        tool_input={"file_path": "src/bar.py"})
        assert state["consecutive_edit_failures"] == 1
        assert state["last_failed_edit_file"] == "src/bar.py"

    def test_edit_failure_same_file_accumulates(self):
        state = fresh_state()
        for _ in range(3):
            update_counters(state, tool_name="Edit", success=False,
                            tool_input={"file_path": "src/foo.py"})
        assert state["consecutive_edit_failures"] == 3
        assert state["last_failed_edit_file"] == "src/foo.py"


class TestConsecutiveReadTracking:
    """Observation 2: sequential file_read calls."""

    def test_read_increments_counter(self):
        state = fresh_state()
        update_counters(state, tool_name="Read", success=True)
        assert state["consecutive_reads"] == 1

    def test_non_read_resets_counter(self):
        state = fresh_state()
        state["consecutive_reads"] = 3
        update_counters(state, tool_name="Edit", success=True)
        assert state["consecutive_reads"] == 0

    def test_reads_accumulate(self):
        state = fresh_state()
        for _ in range(5):
            update_counters(state, tool_name="Read", success=True)
        assert state["consecutive_reads"] == 5


class TestEditsSinceTestTracking:
    """Observation 3: improved edit-without-test via edits_since_test counter."""

    def test_edit_increments_edits_since_test(self):
        state = fresh_state()
        update_counters(state, tool_name="Edit", success=True)
        assert state["edits_since_test"] == 1

    def test_write_increments_edits_since_test(self):
        state = fresh_state()
        update_counters(state, tool_name="Write", success=True)
        assert state["edits_since_test"] == 1

    def test_bash_with_test_resets_edits_since_test(self):
        """Running tests via bash should reset the counter."""
        state = fresh_state()
        state["edits_since_test"] = 5
        update_counters(state, tool_name="Bash", success=True,
                        tool_input={"command": "python -m pytest tests/ -v"})
        assert state["edits_since_test"] == 0

    def test_bash_without_test_doesnt_reset(self):
        state = fresh_state()
        state["edits_since_test"] = 5
        update_counters(state, tool_name="Bash", success=True,
                        tool_input={"command": "ls -la"})
        assert state["edits_since_test"] == 5

    def test_read_doesnt_affect_edits_since_test(self):
        state = fresh_state()
        state["edits_since_test"] = 3
        update_counters(state, tool_name="Read", success=True)
        assert state["edits_since_test"] == 3

    def test_edit_then_test_then_edit(self):
        state = fresh_state()
        update_counters(state, tool_name="Edit", success=True)
        update_counters(state, tool_name="Edit", success=True)
        assert state["edits_since_test"] == 2

        update_counters(state, tool_name="Bash", success=True,
                        tool_input={"command": "pytest"})
        assert state["edits_since_test"] == 0

        update_counters(state, tool_name="Edit", success=True)
        assert state["edits_since_test"] == 1


class TestLastEditTurnTracking:
    """Observation 7: track when the last edit happened for analysis loop detection."""

    def test_edit_updates_last_edit_turn(self):
        state = fresh_state()
        update_counters(state, tool_name="Edit", success=True)
        assert state["last_edit_turn"] == 1  # total_calls after increment

    def test_write_updates_last_edit_turn(self):
        state = fresh_state()
        update_counters(state, tool_name="Write", success=True)
        assert state["last_edit_turn"] == 1

    def test_read_doesnt_update_last_edit_turn(self):
        state = fresh_state()
        update_counters(state, tool_name="Read", success=True)
        assert state["last_edit_turn"] == 0

    def test_turns_since_edit_grows(self):
        state = fresh_state()
        update_counters(state, tool_name="Edit", success=True)  # turn 1
        for _ in range(5):
            update_counters(state, tool_name="Read", success=True)  # turns 2-6
        assert state["total_calls"] == 6
        assert state["last_edit_turn"] == 1
        # turns_since_edit = total_calls - last_edit_turn = 5


# ===========================================================================
# Pattern detection — observer.detect_patterns
# ===========================================================================

class TestEditFailurePattern:
    """Observation 1: suggest re-reading file after repeated edit failures."""

    def test_triggers_after_2_failures(self):
        state = fresh_state()
        state["consecutive_edit_failures"] = 2
        state["last_failed_edit_file"] = "src/handler.py"
        patterns = detect_patterns(state)
        matching = [(pid, msg) for pid, msg in patterns if pid == "repeated_edit_failure"]
        assert len(matching) == 1
        assert "src/handler.py" in matching[0][1]
        assert "Read" in matching[0][1] or "read" in matching[0][1].lower()

    def test_does_not_trigger_after_1_failure(self):
        state = fresh_state()
        state["consecutive_edit_failures"] = 1
        state["last_failed_edit_file"] = "src/handler.py"
        patterns = detect_patterns(state)
        matching = [pid for pid, _ in patterns if pid == "repeated_edit_failure"]
        assert len(matching) == 0


class TestSequentialReadsPattern:
    """Observation 2: suggest batch/semantic tools after 3+ sequential reads."""

    def test_triggers_after_3_reads(self):
        state = fresh_state()
        state["consecutive_reads"] = 3
        patterns = detect_patterns(state)
        matching = [(pid, msg) for pid, msg in patterns if pid == "sequential_reads"]
        assert len(matching) == 1

    def test_does_not_trigger_at_2_reads(self):
        state = fresh_state()
        state["consecutive_reads"] = 2
        patterns = detect_patterns(state)
        matching = [pid for pid, _ in patterns if pid == "sequential_reads"]
        assert len(matching) == 0


class TestImprovedEditWithoutTestPattern:
    """Observation 3: improved detection using edits_since_test."""

    def test_triggers_after_5_edits_without_test(self):
        state = fresh_state()
        state["edits_since_test"] = 5
        patterns = detect_patterns(state)
        matching = [(pid, msg) for pid, msg in patterns if pid == "edit_without_test"]
        assert len(matching) == 1
        assert "5" in matching[0][1]
        assert "test" in matching[0][1].lower()

    def test_does_not_trigger_at_3_edits(self):
        state = fresh_state()
        state["edits_since_test"] = 3
        patterns = detect_patterns(state)
        matching = [pid for pid, _ in patterns if pid == "edit_without_test"]
        assert len(matching) == 0

    def test_old_heuristic_replaced(self):
        """The old Edit>3 && Bash==0 check should be replaced by edits_since_test."""
        state = fresh_state()
        state["tools_used_in_mode"] = {"Edit": 5, "Bash": 2}
        state["edits_since_test"] = 5  # still haven't tested despite Bash calls
        patterns = detect_patterns(state)
        matching = [pid for pid, _ in patterns if pid == "edit_without_test"]
        assert len(matching) == 1  # fires on edits_since_test, not old heuristic


class TestSemanticToolUnderusePattern:
    """Observation 4: agent ignores available semantic tools."""

    def test_triggers_when_searching_without_semantic(self):
        state = fresh_state()
        state["total_calls"] = 12
        state["tools_used_in_mode"] = {"Read": 6, "Grep": 2}
        state["semantic_tools_used"] = False
        patterns = detect_patterns(state)
        matching = [(pid, msg) for pid, msg in patterns if pid == "semantic_underuse"]
        assert len(matching) == 1
        assert "FindDefinitions" in matching[0][1] or "find_definitions" in matching[0][1].lower()

    def test_does_not_trigger_when_semantic_used(self):
        state = fresh_state()
        state["total_calls"] = 12
        state["tools_used_in_mode"] = {"Read": 6, "Grep": 2}
        state["semantic_tools_used"] = True
        patterns = detect_patterns(state)
        matching = [pid for pid, _ in patterns if pid == "semantic_underuse"]
        assert len(matching) == 0

    def test_does_not_trigger_with_few_calls(self):
        state = fresh_state()
        state["total_calls"] = 5
        state["tools_used_in_mode"] = {"Read": 3}
        state["semantic_tools_used"] = False
        patterns = detect_patterns(state)
        matching = [pid for pid, _ in patterns if pid == "semantic_underuse"]
        assert len(matching) == 0

    def test_does_not_trigger_without_search_activity(self):
        """If agent isn't searching, no need to suggest semantic tools."""
        state = fresh_state()
        state["total_calls"] = 15
        state["tools_used_in_mode"] = {"Edit": 10, "Bash": 5}
        state["semantic_tools_used"] = False
        patterns = detect_patterns(state)
        matching = [pid for pid, _ in patterns if pid == "semantic_underuse"]
        assert len(matching) == 0


class TestAnalysisLoopPattern:
    """Observation 7: agent stuck reading without editing (Opus pattern)."""

    def test_triggers_after_15_turns_without_edit(self):
        state = fresh_state()
        state["total_calls"] = 16
        state["last_edit_turn"] = 0
        patterns = detect_patterns(state)
        matching = [(pid, msg) for pid, msg in patterns if pid == "analysis_loop"]
        assert len(matching) == 1
        assert "16" in matching[0][1] or "reading" in matching[0][1].lower()

    def test_does_not_trigger_with_recent_edit(self):
        state = fresh_state()
        state["total_calls"] = 20
        state["last_edit_turn"] = 18
        patterns = detect_patterns(state)
        matching = [pid for pid, _ in patterns if pid == "analysis_loop"]
        assert len(matching) == 0

    def test_does_not_trigger_with_few_calls(self):
        state = fresh_state()
        state["total_calls"] = 5
        state["last_edit_turn"] = 0
        patterns = detect_patterns(state)
        matching = [pid for pid, _ in patterns if pid == "analysis_loop"]
        assert len(matching) == 0


# ===========================================================================
# Dedup — new patterns should dedup properly
# ===========================================================================

class TestNewPatternDedup:
    def test_repeated_edit_failure_deduped(self):
        state = fresh_state()
        state["consecutive_edit_failures"] = 3
        state["last_failed_edit_file"] = "src/foo.py"
        state["suggestions_given"] = ["repeated_edit_failure"]
        suggestions = generate_suggestions(state)
        assert len(suggestions) == 0

    def test_analysis_loop_deduped(self):
        state = fresh_state()
        state["total_calls"] = 20
        state["last_edit_turn"] = 0
        state["suggestions_given"] = ["analysis_loop"]
        suggestions = generate_suggestions(state)
        loop_suggestions = [s for s in suggestions if "reading" in s.lower()]
        assert len(loop_suggestions) == 0


# ===========================================================================
# Integration: update_counters + detect_patterns full cycle
# ===========================================================================

class TestCoachFullCycle:
    """Simulate realistic tool call sequences and verify coach fires correctly."""

    def test_edit_failure_spiral(self):
        """Haiku pattern: fail edit, re-read, fail edit, re-read..."""
        state = fresh_state()
        # Fail edit on foo.py
        update_counters(state, "Edit", False, tool_input={"file_path": "src/foo.py"})
        # Re-read the file
        update_counters(state, "Read", True)
        # Fail edit again on same file
        update_counters(state, "Edit", False, tool_input={"file_path": "src/foo.py"})

        patterns = detect_patterns(state)
        matching = [pid for pid, _ in patterns if pid == "repeated_edit_failure"]
        assert len(matching) == 1

    def test_sequential_read_exploration(self):
        """Agent reading files one by one to understand codebase."""
        state = fresh_state()
        for f in ["src/a.py", "src/b.py", "src/c.py"]:
            update_counters(state, "Read", True)

        patterns = detect_patterns(state)
        matching = [pid for pid, _ in patterns if pid == "sequential_reads"]
        assert len(matching) == 1

    def test_edit_streak_then_test_clears(self):
        """5 edits then a test run — pattern should fire on 5th edit, not after test."""
        state = fresh_state()
        for _ in range(5):
            update_counters(state, "Edit", True)

        patterns_before = detect_patterns(state)
        edit_patterns = [pid for pid, _ in patterns_before if pid == "edit_without_test"]
        assert len(edit_patterns) == 1

        # Run test
        update_counters(state, "Bash", True, tool_input={"command": "pytest"})
        patterns_after = detect_patterns(state)
        edit_patterns_after = [pid for pid, _ in patterns_after if pid == "edit_without_test"]
        assert len(edit_patterns_after) == 0

    def test_opus_analysis_loop(self):
        """Opus reads for 16 turns without editing."""
        state = fresh_state()
        for _ in range(16):
            update_counters(state, "Read", True)

        patterns = detect_patterns(state)
        matching = [pid for pid, _ in patterns if pid == "analysis_loop"]
        assert len(matching) == 1

    def test_no_analysis_loop_with_edits(self):
        """10 reads interleaved with edits — no analysis loop."""
        state = fresh_state()
        for i in range(20):
            if i % 4 == 0:
                update_counters(state, "Edit", True)
            else:
                update_counters(state, "Read", True)

        patterns = detect_patterns(state)
        matching = [pid for pid, _ in patterns if pid == "analysis_loop"]
        assert len(matching) == 0


# ===========================================================================
# Mode-awareness — patterns only fire in appropriate modes
# ===========================================================================

class TestModeAwareness:
    """Patterns should respect the current mode."""

    def _state_with(self, mode, **overrides):
        state = fresh_state()
        state["mode"] = mode
        state.update(overrides)
        return state

    # --- Analysis loop ---

    def test_analysis_loop_suppressed_in_debug(self):
        """In debug mode, not editing is correct — no analysis loop warning."""
        state = self._state_with("debug", total_calls=20, last_edit_turn=0)
        patterns = detect_patterns(state)
        assert not any(pid == "analysis_loop" for pid, _ in patterns)

    def test_analysis_loop_suppressed_in_review(self):
        state = self._state_with("review", total_calls=20, last_edit_turn=0)
        patterns = detect_patterns(state)
        assert not any(pid == "analysis_loop" for pid, _ in patterns)

    def test_analysis_loop_fires_in_implement(self):
        state = self._state_with("implement", total_calls=20, last_edit_turn=0)
        patterns = detect_patterns(state)
        assert any(pid == "analysis_loop" for pid, _ in patterns)

    def test_analysis_loop_fires_in_free(self):
        state = self._state_with("free", total_calls=20, last_edit_turn=0)
        patterns = detect_patterns(state)
        assert any(pid == "analysis_loop" for pid, _ in patterns)

    # --- Sequential reads ---

    def test_sequential_reads_suppressed_in_debug(self):
        """In debug mode, reading sequentially is expected."""
        state = self._state_with("debug", consecutive_reads=5)
        patterns = detect_patterns(state)
        assert not any(pid == "sequential_reads" for pid, _ in patterns)

    def test_sequential_reads_suppressed_in_review(self):
        state = self._state_with("review", consecutive_reads=5)
        patterns = detect_patterns(state)
        assert not any(pid == "sequential_reads" for pid, _ in patterns)

    def test_sequential_reads_fires_in_implement(self):
        state = self._state_with("implement", consecutive_reads=5)
        patterns = detect_patterns(state)
        assert any(pid == "sequential_reads" for pid, _ in patterns)

    # --- Edit without test ---

    def test_edit_without_test_suppressed_in_debug(self):
        state = self._state_with("debug", edits_since_test=10)
        patterns = detect_patterns(state)
        assert not any(pid == "edit_without_test" for pid, _ in patterns)

    def test_edit_without_test_suppressed_in_review(self):
        state = self._state_with("review", edits_since_test=10)
        patterns = detect_patterns(state)
        assert not any(pid == "edit_without_test" for pid, _ in patterns)

    def test_edit_without_test_fires_in_test_dev(self):
        state = self._state_with("test_dev", edits_since_test=10)
        patterns = detect_patterns(state)
        assert any(pid == "edit_without_test" for pid, _ in patterns)

    # --- Repeated edit failure ---

    def test_edit_failure_suppressed_in_debug(self):
        state = self._state_with(
            "debug", consecutive_edit_failures=3, last_failed_edit_file="src/foo.py",
        )
        patterns = detect_patterns(state)
        assert not any(pid == "repeated_edit_failure" for pid, _ in patterns)

    def test_edit_failure_fires_in_create(self):
        state = self._state_with(
            "create", consecutive_edit_failures=3, last_failed_edit_file="src/foo.py",
        )
        patterns = detect_patterns(state)
        assert any(pid == "repeated_edit_failure" for pid, _ in patterns)

    # --- Semantic underuse fires in all modes ---

    def test_semantic_underuse_fires_in_debug(self):
        """Even in debug mode, suggesting semantic tools is helpful."""
        state = self._state_with(
            "debug",
            total_calls=15,
            tools_used_in_mode={"Read": 8, "Grep": 3},
            semantic_tools_used=False,
        )
        patterns = detect_patterns(state)
        assert any(pid == "semantic_underuse" for pid, _ in patterns)

    def test_semantic_underuse_fires_in_review(self):
        state = self._state_with(
            "review",
            total_calls=15,
            tools_used_in_mode={"Read": 8, "Grep": 3},
            semantic_tools_used=False,
        )
        patterns = detect_patterns(state)
        assert any(pid == "semantic_underuse" for pid, _ in patterns)

    # --- Document mode ---

    def test_edit_without_test_suppressed_in_document(self):
        """Editing docs doesn't need test runs."""
        state = self._state_with("document", edits_since_test=10)
        patterns = detect_patterns(state)
        assert not any(pid == "edit_without_test" for pid, _ in patterns)

    def test_analysis_loop_fires_in_document(self):
        """Document mode is writable — if you're not writing, something's off."""
        state = self._state_with("document", total_calls=20, last_edit_turn=0)
        patterns = detect_patterns(state)
        assert any(pid == "analysis_loop" for pid, _ in patterns)
