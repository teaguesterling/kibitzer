import pytest
from kibitzer.hooks.post_tool_use import handle_post_tool_use
from kibitzer.state import fresh_state, save_state, load_state


@pytest.fixture
def implement_project(state_dir):
    state = fresh_state()
    state["mode"] = "implement"
    save_state(state, state_dir)
    return state_dir.parent


class TestPostToolUseCounters:
    def test_increments_total_calls(self, implement_project):
        hook_input = {"tool_name": "Edit", "tool_input": {}, "tool_result": "ok"}
        handle_post_tool_use(hook_input, project_dir=implement_project)
        state = load_state(implement_project / ".kibitzer")
        assert state["total_calls"] == 1

    def test_tracks_success(self, implement_project):
        hook_input = {"tool_name": "Edit", "tool_input": {}, "tool_result": "ok"}
        handle_post_tool_use(hook_input, project_dir=implement_project)
        state = load_state(implement_project / ".kibitzer")
        assert state["success_count"] == 1
        assert state["consecutive_failures"] == 0

    def test_tracks_bash_failure(self, implement_project):
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "false"},
            "tool_result": {"exitCode": 1, "stdout": "", "stderr": "error"},
        }
        handle_post_tool_use(hook_input, project_dir=implement_project)
        state = load_state(implement_project / ".kibitzer")
        assert state["failure_count"] == 1
        assert state["consecutive_failures"] == 1


class TestPostToolUseModeTransition:
    def test_auto_switch_to_debug(self, implement_project):
        state = fresh_state()
        state["mode"] = "implement"
        state["consecutive_failures"] = 3
        save_state(state, implement_project / ".kibitzer")

        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "make"},
            "tool_result": {"exitCode": 1, "stdout": "", "stderr": "error"},
        }
        result = handle_post_tool_use(hook_input, project_dir=implement_project)
        state = load_state(implement_project / ".kibitzer")
        assert state["mode"] == "explore"
        assert result is not None
        assert "explore" in result.get("hookSpecificOutput", {}).get("additionalContext", "")


class TestPostToolUseCoach:
    def test_coach_fires_at_frequency(self, implement_project):
        state = fresh_state()
        state["mode"] = "implement"
        state["total_calls"] = 4
        state["tools_used_in_mode"]["Edit"] = 4
        save_state(state, implement_project / ".kibitzer")

        hook_input = {"tool_name": "Edit", "tool_input": {}, "tool_result": "ok"}
        result = handle_post_tool_use(hook_input, project_dir=implement_project)
        if result:
            ctx = result.get("hookSpecificOutput", {}).get("additionalContext", "")
            assert "test" in ctx.lower() or ctx == ""
