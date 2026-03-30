"""Tests for review mode — read-only with verification encouragement."""

import json

from kibitzer.coach.observer import detect_patterns
from kibitzer.coach.suggestions import generate_suggestions
from kibitzer.hooks.pre_tool_use import handle_pre_tool_use
from kibitzer.hooks.post_tool_use import handle_post_tool_use
from kibitzer.state import fresh_state, save_state, load_state


def _project_in_review(tmp_path, **state_overrides):
    state_dir = tmp_path / ".kibitzer"
    state_dir.mkdir(exist_ok=True)
    state = fresh_state()
    state["mode"] = "review"
    state.update(state_overrides)
    save_state(state, state_dir)
    return tmp_path


# ===========================================================================
# Path guard — review is read-only
# ===========================================================================

class TestReviewPathGuard:
    def test_denies_edit_src(self, tmp_path):
        proj = _project_in_review(tmp_path)
        result = handle_pre_tool_use(
            {"tool_name": "Edit", "tool_input": {"file_path": "src/main.py"}},
            project_dir=proj,
        )
        assert result is not None
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_denies_edit_tests(self, tmp_path):
        proj = _project_in_review(tmp_path)
        result = handle_pre_tool_use(
            {"tool_name": "Edit", "tool_input": {"file_path": "tests/test_foo.py"}},
            project_dir=proj,
        )
        assert result is not None
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_allows_read(self, tmp_path):
        proj = _project_in_review(tmp_path)
        result = handle_pre_tool_use(
            {"tool_name": "Read", "tool_input": {"file_path": "src/main.py"}},
            project_dir=proj,
        )
        assert result is None

    def test_allows_bash(self, tmp_path):
        """Review mode should allow running commands (tests, checks)."""
        proj = _project_in_review(tmp_path)
        result = handle_pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "pytest tests/ -v"}},
            project_dir=proj,
        )
        assert result is None


# ===========================================================================
# Coach patterns in review mode
# ===========================================================================

class TestReviewCoachPatterns:
    def test_suggest_tests_after_reading(self):
        """After reading several files in review, suggest running tests."""
        state = fresh_state()
        state["mode"] = "review"
        state["tools_used_in_mode"] = {"Read": 5, "Grep": 2}
        patterns = detect_patterns(state)
        matching = [(pid, msg) for pid, msg in patterns if pid == "review_suggest_tests"]
        assert len(matching) == 1
        assert "test" in matching[0][1].lower()

    def test_no_suggest_if_already_ran_bash(self):
        """If agent already ran bash commands, don't suggest tests."""
        state = fresh_state()
        state["mode"] = "review"
        state["tools_used_in_mode"] = {"Read": 5, "Bash": 1}
        patterns = detect_patterns(state)
        matching = [pid for pid, _ in patterns if pid == "review_suggest_tests"]
        assert len(matching) == 0

    def test_no_suggest_with_few_reads(self):
        """Don't suggest tests if barely started reading."""
        state = fresh_state()
        state["mode"] = "review"
        state["tools_used_in_mode"] = {"Read": 2}
        patterns = detect_patterns(state)
        matching = [pid for pid, _ in patterns if pid == "review_suggest_tests"]
        assert len(matching) == 0

    def test_suggest_tests_not_in_explore(self):
        """Explore mode should NOT suggest running tests."""
        state = fresh_state()
        state["mode"] = "explore"
        state["tools_used_in_mode"] = {"Read": 8}
        patterns = detect_patterns(state)
        matching = [pid for pid, _ in patterns if pid == "review_suggest_tests"]
        assert len(matching) == 0

    def test_suggest_tests_mentions_blq_when_available(self, tmp_path):
        mcp = {"mcpServers": {"blq": {"command": "blq"}}}
        (tmp_path / ".mcp.json").write_text(json.dumps(mcp))

        state = fresh_state()
        state["mode"] = "review"
        state["tools_used_in_mode"] = {"Read": 6}
        patterns = detect_patterns(state, project_dir=tmp_path)
        matching = [(pid, msg) for pid, msg in patterns if pid == "review_suggest_tests"]
        assert len(matching) == 1
        assert "blq" in matching[0][1]

    def test_analysis_loop_suppressed_in_review(self):
        """Review mode: not editing is correct behavior."""
        state = fresh_state()
        state["mode"] = "review"
        state["total_calls"] = 20
        state["last_edit_turn"] = 0
        patterns = detect_patterns(state)
        assert not any(pid == "analysis_loop" for pid, _ in patterns)

    def test_sequential_reads_suppressed_in_review(self):
        state = fresh_state()
        state["mode"] = "review"
        state["consecutive_reads"] = 5
        patterns = detect_patterns(state)
        assert not any(pid == "sequential_reads" for pid, _ in patterns)

    def test_readonly_edits_fires_in_review(self):
        state = fresh_state()
        state["mode"] = "review"
        state["tools_used_in_mode"] = {"Edit": 1}
        patterns = detect_patterns(state)
        matching = [(pid, msg) for pid, msg in patterns if pid == "readonly_mode_edits"]
        assert len(matching) == 1
        assert "review" in matching[0][1]

    def test_deduped(self):
        state = fresh_state()
        state["mode"] = "review"
        state["tools_used_in_mode"] = {"Read": 6}
        state["suggestions_given"] = ["review_suggest_tests"]
        suggestions = generate_suggestions(state)
        test_suggestions = [s for s in suggestions if "test" in s.lower() and "verify" in s.lower()]
        assert len(test_suggestions) == 0


# ===========================================================================
# Mode controller — review doesn't auto-transition on failures
# ===========================================================================

class TestReviewModeController:
    def test_no_auto_transition_on_failures(self, tmp_path):
        """Failures in review mode shouldn't trigger explore — test failures are expected."""
        proj = _project_in_review(tmp_path)
        for _ in range(5):
            handle_post_tool_use(
                {"tool_name": "Bash", "tool_input": {"command": "pytest"},
                 "tool_result": {"exitCode": 1, "stdout": "", "stderr": "3 failed"}},
                project_dir=proj,
            )
        state = load_state(proj / ".kibitzer")
        assert state["mode"] == "review"  # should NOT have switched

    def test_counters_still_tracked(self, tmp_path):
        proj = _project_in_review(tmp_path)
        handle_post_tool_use(
            {"tool_name": "Read", "tool_input": {"file_path": "src/main.py"},
             "tool_result": {"content": "hello"}},
            project_dir=proj,
        )
        state = load_state(proj / ".kibitzer")
        assert state["total_calls"] == 1
        assert state["tools_used_in_mode"]["Read"] == 1


# ===========================================================================
# MCP — can switch to review mode
# ===========================================================================

class TestReviewMCP:
    def test_switch_to_review(self, tmp_path):
        from kibitzer.mcp.server import change_tool_mode

        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        result = change_tool_mode("review", project_dir=tmp_path)
        assert result["new_mode"] == "review"
        assert result["writable"] == []
        assert result["strategy"] == "Read everything, then verify with tests."
