import pytest
from kibitzer.state import fresh_state, save_state, load_state
from kibitzer.mcp.server import change_tool_mode, get_feedback


@pytest.fixture
def project_with_state(tmp_path):
    state_dir = tmp_path / ".kibitzer"
    state_dir.mkdir()
    state = fresh_state()
    state["mode"] = "implement"
    state["failure_count"] = 2
    state["success_count"] = 10
    state["total_calls"] = 12
    state["tools_used_in_mode"] = {"Edit": 5, "Read": 3}
    save_state(state, state_dir)
    return tmp_path


class TestChangeToolMode:
    def test_switch_mode(self, project_with_state):
        result = change_tool_mode("explore", project_dir=project_with_state)
        assert result["previous_mode"] == "implement"
        assert result["new_mode"] == "explore"
        assert result["writable"] == []
        state = load_state(project_with_state / ".kibitzer")
        assert state["mode"] == "explore"

    def test_switch_resets_counters(self, project_with_state):
        change_tool_mode("explore", project_dir=project_with_state)
        state = load_state(project_with_state / ".kibitzer")
        assert state["failure_count"] == 0
        assert state["success_count"] == 0
        assert state["turns_in_mode"] == 0

    def test_switch_with_reason(self, project_with_state):
        result = change_tool_mode("explore", reason="tests failing", project_dir=project_with_state)
        assert result["new_mode"] == "explore"

    def test_invalid_mode(self, project_with_state):
        result = change_tool_mode("nonexistent", project_dir=project_with_state)
        assert "error" in result


class TestGetFeedback:
    def test_status_only(self, project_with_state):
        result = get_feedback(
            status=True, suggestions=False, intercepts=False,
            project_dir=project_with_state,
        )
        assert "status" in result
        assert result["status"]["mode"] == "implement"
        assert result["status"]["total_calls"] == 12
        assert "suggestions" not in result
        assert "intercepts" not in result

    def test_all_sections(self, project_with_state):
        result = get_feedback(
            status=True, suggestions=True, intercepts=True,
            project_dir=project_with_state,
        )
        assert "status" in result
        assert "suggestions" in result
        assert "intercepts" in result

    def test_status_includes_writable(self, project_with_state):
        result = get_feedback(
            status=True, suggestions=False, intercepts=False,
            project_dir=project_with_state,
        )
        assert "writable" in result["status"]
