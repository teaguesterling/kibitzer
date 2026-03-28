import json
import pytest
from unittest.mock import patch
from kibitzer.hooks.pre_tool_use import handle_pre_tool_use


@pytest.fixture
def implement_state(state_dir):
    from kibitzer.state import fresh_state, save_state
    state = fresh_state()
    state["mode"] = "implement"
    save_state(state, state_dir)
    return state_dir


@pytest.fixture
def free_state(state_dir):
    from kibitzer.state import fresh_state, save_state
    state = fresh_state()
    state["mode"] = "free"
    save_state(state, state_dir)
    return state_dir


class TestPreToolUsePathGuard:
    def test_allow_edit_in_writable_path(self, implement_state):
        hook_input = {"tool_name": "Edit", "tool_input": {"file_path": "src/foo.py"}}
        result = handle_pre_tool_use(hook_input, project_dir=implement_state.parent)
        assert result is None

    def test_deny_edit_in_protected_path(self, implement_state):
        hook_input = {"tool_name": "Edit", "tool_input": {"file_path": "tests/test_foo.py"}}
        result = handle_pre_tool_use(hook_input, project_dir=implement_state.parent)
        assert result is not None
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_allow_read_tool(self, implement_state):
        hook_input = {"tool_name": "Read", "tool_input": {"file_path": "tests/test_foo.py"}}
        result = handle_pre_tool_use(hook_input, project_dir=implement_state.parent)
        assert result is None

    def test_allow_everything_in_free_mode(self, free_state):
        hook_input = {"tool_name": "Edit", "tool_input": {"file_path": "tests/test_foo.py"}}
        result = handle_pre_tool_use(hook_input, project_dir=free_state.parent)
        assert result is None

    def test_deny_write_tool(self, implement_state):
        hook_input = {"tool_name": "Write", "tool_input": {"file_path": "tests/new.py"}}
        result = handle_pre_tool_use(hook_input, project_dir=implement_state.parent)
        assert result is not None
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


class TestPreToolUseInterceptor:
    @patch("kibitzer.hooks.pre_tool_use.build_registry")
    def test_suggest_mode_injects_context(self, mock_registry, implement_state):
        from kibitzer.interceptors.jetsam import JetsamInterceptor
        mock_registry.return_value = [JetsamInterceptor()]
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "git add -A && git commit -m 'fix'"},
        }
        result = handle_pre_tool_use(
            hook_input, project_dir=implement_state.parent,
            plugin_modes={"jetsam": "suggest"},
        )
        assert result is not None
        assert "additionalContext" in result["hookSpecificOutput"]
        assert "jetsam" in result["hookSpecificOutput"]["additionalContext"]

    @patch("kibitzer.hooks.pre_tool_use.build_registry")
    def test_observe_mode_returns_none(self, mock_registry, implement_state):
        from kibitzer.interceptors.jetsam import JetsamInterceptor
        mock_registry.return_value = [JetsamInterceptor()]
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "git add -A && git commit -m 'fix'"},
        }
        result = handle_pre_tool_use(
            hook_input, project_dir=implement_state.parent,
            plugin_modes={"jetsam": "observe"},
        )
        assert result is None

    @patch("kibitzer.hooks.pre_tool_use.build_registry")
    def test_no_match_allows(self, mock_registry, implement_state):
        from kibitzer.interceptors.jetsam import JetsamInterceptor
        mock_registry.return_value = [JetsamInterceptor()]
        hook_input = {"tool_name": "Bash", "tool_input": {"command": "ls -la"}}
        result = handle_pre_tool_use(
            hook_input, project_dir=implement_state.parent,
            plugin_modes={"jetsam": "suggest"},
        )
        assert result is None
