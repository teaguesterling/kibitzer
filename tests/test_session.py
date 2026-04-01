"""Tests for KibitzerSession and CallResult."""

from kibitzer.session import CallResult, KibitzerSession
from kibitzer.state import fresh_state, save_state, load_state


class TestCallResult:
    def test_allow_result(self):
        result = CallResult()
        assert not result.denied
        assert result.reason == ""
        assert result.context == ""

    def test_deny_result(self):
        result = CallResult(denied=True, reason="not writable", tool="Edit")
        assert result.denied
        assert "not writable" in result.reason

    def test_context_result(self):
        result = CallResult(context="[kibitzer] suggestion", tool="Bash")
        assert not result.denied
        assert "[kibitzer]" in result.context

    def test_to_hook_output_deny(self):
        result = CallResult(denied=True, reason="blocked")
        output = result.to_hook_output("PreToolUse")
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert output["hookSpecificOutput"]["permissionDecisionReason"] == "blocked"
        assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"

    def test_to_hook_output_context(self):
        result = CallResult(context="[kibitzer] try jetsam")
        output = result.to_hook_output("PreToolUse")
        assert "additionalContext" in output["hookSpecificOutput"]
        assert "permissionDecision" not in output["hookSpecificOutput"]

    def test_to_hook_output_post_tool(self):
        result = CallResult(context="[kibitzer] mode switched")
        output = result.to_hook_output("PostToolUse")
        assert output["hookSpecificOutput"]["hookEventName"] == "PostToolUse"

    def test_to_hook_output_empty(self):
        result = CallResult()
        output = result.to_hook_output("PreToolUse")
        assert output == {}


class TestSessionLifecycle:
    def test_context_manager_loads_and_saves(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            assert session.mode == "implement"

    def test_manual_load_save(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        session = KibitzerSession(project_dir=tmp_path)
        session.load()
        assert session.mode == "implement"
        session.save()

    def test_no_state_dir_uses_defaults(self, tmp_path):
        with KibitzerSession(project_dir=tmp_path) as session:
            assert session.mode == "implement"

    def test_properties(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            assert session.mode == "implement"
            assert isinstance(session.config, dict)
            assert isinstance(session.state, dict)
            assert session.writable == ["src/", "lib/"]


class TestBeforeCall:
    def test_allow_src_edit(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            result = session.before_call("Edit", {"file_path": "src/foo.py"})
            assert result is None

    def test_deny_test_edit_in_implement(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            result = session.before_call("Edit", {"file_path": "tests/foo.py"})
            assert result is not None
            assert result.denied
            assert "ChangeToolMode" in result.reason

    def test_allow_read_in_any_mode(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        state = fresh_state()
        state["mode"] = "explore"
        save_state(state, state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            result = session.before_call("Read", {"file_path": "src/foo.py"})
            assert result is None


class TestAfterCall:
    def test_updates_counters(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            session.after_call("Edit", {"file_path": "src/foo.py"}, success=True)
            assert session.state["total_calls"] == 1
            assert session.state["success_count"] == 1

    def test_mode_transition(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        state = fresh_state()
        state["consecutive_failures"] = 2
        save_state(state, state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            result = session.after_call("Bash", {"command": "make"}, success=False)
            assert session.mode == "explore"
            assert result is not None
            assert "explore" in result.context

    def test_state_persisted_after_exit(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            session.after_call("Edit", {"file_path": "src/foo.py"}, success=True)

        loaded = load_state(state_dir)
        assert loaded["total_calls"] == 1


class TestValidateCalls:
    def test_all_allowed(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            violations = session.validate_calls([
                {"tool": "Read", "input": {"file_path": "src/foo.py"}},
                {"tool": "Edit", "input": {"file_path": "src/bar.py"}},
            ])
            assert violations == []

    def test_returns_violations(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            violations = session.validate_calls([
                {"tool": "Edit", "input": {"file_path": "src/ok.py"}},
                {"tool": "Edit", "input": {"file_path": "tests/blocked.py"}},
            ])
            assert len(violations) == 1
            assert violations[0].denied
            assert "tests/blocked.py" in violations[0].reason

    def test_does_not_modify_state(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            before = session.state["total_calls"]
            session.validate_calls([
                {"tool": "Edit", "input": {"file_path": "tests/foo.py"}},
            ])
            assert session.state["total_calls"] == before


class TestChangeMode:
    def test_switch_mode(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            result = session.change_mode("test", reason="writing tests")
            assert result["new_mode"] == "test"
            assert result["previous_mode"] == "implement"
            assert session.mode == "test"

    def test_invalid_mode(self, tmp_path):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        save_state(fresh_state(), state_dir)

        with KibitzerSession(project_dir=tmp_path) as session:
            result = session.change_mode("nonexistent")
            assert "error" in result
