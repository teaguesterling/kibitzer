"""Tests for KibitzerSession safe_mode — errors swallowed, never crashes."""

from unittest.mock import patch
from kibitzer.session import KibitzerSession
from kibitzer.state import fresh_state, save_state, load_state
import pytest


def _project(tmp_path):
    state_dir = tmp_path / ".kibitzer"
    state_dir.mkdir()
    save_state(fresh_state(), state_dir)
    return tmp_path


class TestSafeMode:
    def test_before_call_returns_none_on_error(self, tmp_path):
        proj = _project(tmp_path)
        with patch("kibitzer.session.check_path", side_effect=RuntimeError("boom")):
            with KibitzerSession(project_dir=proj, safe_mode=True) as session:
                result = session.before_call("Edit", {"file_path": "src/foo.py"})
                assert result is None

    def test_after_call_returns_none_on_error(self, tmp_path):
        proj = _project(tmp_path)
        with patch("kibitzer.session.update_counters", side_effect=RuntimeError("boom")):
            with KibitzerSession(project_dir=proj, safe_mode=True) as session:
                result = session.after_call("Edit", {}, success=True)
                assert result is None

    def test_safe_mode_still_saves(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj, safe_mode=True) as session:
            session.after_call("Read", {}, success=True)

        state = load_state(proj / ".kibitzer")
        assert state["total_calls"] == 1

    def test_normal_mode_raises(self, tmp_path):
        proj = _project(tmp_path)
        with patch("kibitzer.session.check_path", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                with KibitzerSession(project_dir=proj) as session:
                    session.before_call("Edit", {"file_path": "src/foo.py"})

    def test_context_manager_saves_on_exception(self, tmp_path):
        proj = _project(tmp_path)
        try:
            with KibitzerSession(project_dir=proj) as session:
                session.after_call("Edit", {}, success=True)
                raise ValueError("user error")
        except ValueError:
            pass

        state = load_state(proj / ".kibitzer")
        assert state["total_calls"] == 1
