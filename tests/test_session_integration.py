"""Verify hooks and MCP produce same results through KibitzerSession."""

from kibitzer.session import KibitzerSession
from kibitzer.state import fresh_state, save_state, load_state
from kibitzer.mcp.server import change_tool_mode, get_feedback


def _project(tmp_path):
    state_dir = tmp_path / ".kibitzer"
    state_dir.mkdir()
    save_state(fresh_state(), state_dir)
    return tmp_path


class TestSessionMatchesHooks:
    def test_deny_matches(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            result = session.before_call("Edit", {"file_path": "tests/foo.py"})
        hook_output = result.to_hook_output("PreToolUse")
        assert hook_output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "ChangeToolMode" in hook_output["hookSpecificOutput"]["permissionDecisionReason"]

    def test_allow_produces_none(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            result = session.before_call("Read", {"file_path": "src/foo.py"})
        assert result is None

    def test_after_call_state_matches(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.after_call("Edit", {"file_path": "src/foo.py"}, success=True)

        state = load_state(proj / ".kibitzer")
        assert state["total_calls"] == 1
        assert state["tools_used_in_mode"]["Edit"] == 1


class TestMCPUsesSession:
    def test_change_mode(self, tmp_path):
        proj = _project(tmp_path)
        result = change_tool_mode("test", project_dir=proj)
        assert result["new_mode"] == "test"
        state = load_state(proj / ".kibitzer")
        assert state["mode"] == "test"

    def test_get_feedback(self, tmp_path):
        proj = _project(tmp_path)
        feedback = get_feedback(project_dir=proj)
        assert "status" in feedback
        assert feedback["status"]["mode"] == "implement"


class TestStoreEvents:
    def test_after_call_writes_event(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.after_call("Edit", {"file_path": "src/foo.py"}, success=True)

        from kibitzer.store import KibitzerStore
        store = KibitzerStore(proj / ".kibitzer" / "store.sqlite")
        events = store.query_events(event_type="tool_call")
        assert len(events) == 1
        assert events[0]["tool_name"] == "Edit"

    def test_denial_writes_event(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.before_call("Edit", {"file_path": "tests/foo.py"})

        from kibitzer.store import KibitzerStore
        store = KibitzerStore(proj / ".kibitzer" / "store.sqlite")
        events = store.query_events(event_type="denial")
        assert len(events) == 1

    def test_mode_switch_writes_event(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.change_mode("test")

        from kibitzer.store import KibitzerStore
        store = KibitzerStore(proj / ".kibitzer" / "store.sqlite")
        events = store.query_events(event_type="mode_switch")
        assert len(events) == 1
