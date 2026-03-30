"""Integration tests: full hook chains with realistic scenarios."""

import pytest
from kibitzer.hooks.pre_tool_use import handle_pre_tool_use
from kibitzer.hooks.post_tool_use import handle_post_tool_use
from kibitzer.state import fresh_state, save_state, load_state


@pytest.fixture
def project(tmp_path):
    """Set up a project with kibitzer initialized."""
    state_dir = tmp_path / ".kibitzer"
    state_dir.mkdir()
    state = fresh_state()
    state["mode"] = "implement"
    save_state(state, state_dir)
    return tmp_path


class TestPathGuardEndToEnd:
    def test_implement_blocks_test_edit(self, project):
        result = handle_pre_tool_use(
            {"tool_name": "Edit", "tool_input": {"file_path": "tests/test_foo.py"}},
            project_dir=project,
        )
        assert result is not None
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "ChangeToolMode" in result["hookSpecificOutput"]["permissionDecisionReason"]

    def test_implement_allows_src_edit(self, project):
        result = handle_pre_tool_use(
            {"tool_name": "Edit", "tool_input": {"file_path": "src/main.py"}},
            project_dir=project,
        )
        assert result is None

    def test_free_mode_allows_everything(self, project):
        state = load_state(project / ".kibitzer")
        state["mode"] = "free"
        save_state(state, project / ".kibitzer")

        result = handle_pre_tool_use(
            {"tool_name": "Edit", "tool_input": {"file_path": "tests/test_foo.py"}},
            project_dir=project,
        )
        assert result is None

    def test_document_mode_allows_docs(self, project):
        state = load_state(project / ".kibitzer")
        state["mode"] = "document"
        save_state(state, project / ".kibitzer")

        assert handle_pre_tool_use(
            {"tool_name": "Edit", "tool_input": {"file_path": "docs/guide.md"}},
            project_dir=project,
        ) is None

        result = handle_pre_tool_use(
            {"tool_name": "Edit", "tool_input": {"file_path": "src/main.py"}},
            project_dir=project,
        )
        assert result is not None
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


class TestFailureDrivenModeSwitch:
    def test_consecutive_failures_trigger_debug(self, project):
        """Simulate 4 consecutive bash failures -> auto-switch to debug."""
        for i in range(4):
            handle_post_tool_use(
                {
                    "tool_name": "Bash",
                    "tool_input": {"command": "make build"},
                    "tool_result": {"exitCode": 1, "stdout": "", "stderr": "error"},
                },
                project_dir=project,
            )

        state = load_state(project / ".kibitzer")
        assert state["mode"] == "debug"
        assert state["previous_mode"] == "implement"

    def test_success_resets_streak(self, project):
        """2 failures then a success should not trigger debug (threshold is 3)."""
        for i in range(2):
            handle_post_tool_use(
                {
                    "tool_name": "Bash",
                    "tool_input": {"command": "make"},
                    "tool_result": {"exitCode": 1, "stdout": "", "stderr": "err"},
                },
                project_dir=project,
            )
        # One success resets the streak
        handle_post_tool_use(
            {"tool_name": "Edit", "tool_input": {}, "tool_result": "ok"},
            project_dir=project,
        )
        # One more failure — streak is back to 1, not enough
        handle_post_tool_use(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "make"},
                "tool_result": {"exitCode": 1, "stdout": "", "stderr": "err"},
            },
            project_dir=project,
        )

        state = load_state(project / ".kibitzer")
        assert state["mode"] == "implement"  # should NOT have switched


class TestOscillationGuard:
    def test_stops_after_too_many_switches(self, project):
        """After 6+ mode switches, auto-transitions should stop."""
        state = load_state(project / ".kibitzer")
        state["mode_switches"] = 7
        state["consecutive_failures"] = 10
        save_state(state, project / ".kibitzer")

        handle_post_tool_use(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "make"},
                "tool_result": {"exitCode": 1, "stdout": "", "stderr": "err"},
            },
            project_dir=project,
        )
        state = load_state(project / ".kibitzer")
        assert state["mode"] == "implement"  # should NOT have switched


class TestMCPAndHooksShareState:
    def test_mcp_mode_change_affects_hooks(self, project):
        """ChangeToolMode via MCP should be respected by hooks."""
        from kibitzer.mcp.server import change_tool_mode

        change_tool_mode("test_dev", project_dir=project)

        # Now test edit should be allowed, src edit denied
        assert handle_pre_tool_use(
            {"tool_name": "Edit", "tool_input": {"file_path": "tests/test_foo.py"}},
            project_dir=project,
        ) is None

        result = handle_pre_tool_use(
            {"tool_name": "Edit", "tool_input": {"file_path": "src/main.py"}},
            project_dir=project,
        )
        assert result is not None
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_get_feedback_reflects_hook_activity(self, project):
        """GetFeedback should reflect state changes from hooks."""
        from kibitzer.mcp.server import get_feedback

        # Run some tool calls through hooks
        for _ in range(3):
            handle_post_tool_use(
                {"tool_name": "Edit", "tool_input": {}, "tool_result": "ok"},
                project_dir=project,
            )

        result = get_feedback(status=True, suggestions=False, intercepts=False, project_dir=project)
        assert result["status"]["total_calls"] == 3
        assert result["status"]["success_count"] == 3
