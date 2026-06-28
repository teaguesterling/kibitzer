"""Tests for the nudge A/B experiment: arms, heed resolution, suppression."""

import json
from unittest.mock import patch

from kibitzer.session import KibitzerSession, _plugin_of_tool
from kibitzer.state import fresh_state, save_state, load_state
from kibitzer.interceptors.squackit import SquackitInterceptor


def _project(tmp_path, **state):
    d = tmp_path / ".kibitzer"
    d.mkdir(exist_ok=True)
    s = fresh_state()
    s["mode"] = "free"
    s["session_id"] = "exp-test"
    s.update(state)
    save_state(s, d)
    return tmp_path


def _cfg(session):
    session._config.setdefault("plugins", {})["squackit"] = {"mode": "suggest", "enabled": True}
    session._config["experiment"] = {"nudge_probability": 0.5, "heed_window": 4}


class TestPluginOfTool:
    def test_maps_mcp_tools(self):
        assert _plugin_of_tool("mcp__plugin_squackit_squackit__search") == "squackit"
        assert _plugin_of_tool("mcp__plugin_blq_blq_mcp__run") == "blq"
        assert _plugin_of_tool("Bash") is None
        assert _plugin_of_tool("mcp__claude_ai_Gmail__send") is None


@patch("kibitzer.session.build_registry", return_value=[SquackitInterceptor()])
class TestArms:
    def test_nudge_arm_emits_and_opens_trial(self, _reg, tmp_path):
        # autouse fixture forces the NUDGE arm
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as s:
            _cfg(s)
            result = s.before_call("Bash", {"command": "grep -rn foo src/"})
        assert result is not None
        st = load_state(proj / ".kibitzer")
        assert len(st["nudge_trials"]) == 1
        assert st["nudge_trials"][0]["arm"] == "nudge"

    def test_control_arm_is_silent_but_logs_trial(self, _reg, tmp_path, monkeypatch):
        monkeypatch.setattr("kibitzer.session.random.random", lambda: 0.99)  # CONTROL
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as s:
            _cfg(s)
            result = s.before_call("Bash", {"command": "grep -rn foo src/"})
        assert result is None  # silent
        st = load_state(proj / ".kibitzer")
        assert st["nudge_trials"][0]["arm"] == "control"

    def test_suppress_when_engaged(self, _reg, tmp_path):
        proj = _project(tmp_path, engaged_plugins=["squackit"])
        with KibitzerSession(project_dir=proj) as s:
            _cfg(s)
            result = s.before_call("Bash", {"command": "grep -rn foo src/"})
        assert result is None  # already uses squackit — no nudge
        st = load_state(proj / ".kibitzer")
        assert not st.get("nudge_trials")  # and no trial opened


@patch("kibitzer.session.build_registry", return_value=[SquackitInterceptor()])
class TestHeedResolution:
    def test_heed_true_when_tool_used_in_window(self, _reg, tmp_path):
        proj = _project(tmp_path)
        # open a trial
        with KibitzerSession(project_dir=proj) as s:
            _cfg(s)
            s.before_call("Bash", {"command": "grep -rn foo src/"})
        # then use a squackit tool within the window
        with KibitzerSession(project_dir=proj) as s:
            _cfg(s)
            s.after_call("mcp__plugin_squackit_squackit__search", {}, success=True)
        st = load_state(proj / ".kibitzer")
        assert not st.get("nudge_trials")  # resolved
        from kibitzer.session import _TRIAL_LOG
        records = [json.loads(x) for x in _TRIAL_LOG.read_text().splitlines() if x.strip()]
        assert records and records[-1]["heed"] is True

    def test_heed_false_at_deadline(self, _reg, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as s:
            _cfg(s)
            s.before_call("Bash", {"command": "grep -rn foo src/"})
        # run past the heed window (4) with non-matching tools
        for _ in range(6):
            with KibitzerSession(project_dir=proj) as s:
                _cfg(s)
                s.after_call("Read", {"file_path": "x"}, success=True)
        st = load_state(proj / ".kibitzer")
        assert not st.get("nudge_trials")
        from kibitzer.session import _TRIAL_LOG
        records = [json.loads(x) for x in _TRIAL_LOG.read_text().splitlines() if x.strip()]
        assert records and records[-1]["heed"] is False


@patch("kibitzer.session.build_registry", return_value=[SquackitInterceptor()])
class TestSessionAttribution:
    def test_record_carries_session_and_ts(self, _reg, tmp_path):
        # session_id flows from the constructor (the hook payload) into state,
        # gets stamped on the trial, and survives to the resolved record.
        proj = tmp_path
        d = proj / ".kibitzer"
        d.mkdir()
        s0 = fresh_state()
        s0["mode"] = "free"
        save_state(s0, d)
        with KibitzerSession(project_dir=proj, session_id="sess-A") as s:
            _cfg(s)
            s.before_call("Bash", {"command": "grep -rn foo src/"})
        with KibitzerSession(project_dir=proj, session_id="sess-A") as s:
            _cfg(s)
            s.after_call("mcp__plugin_squackit_squackit__search", {}, success=True)
        from kibitzer.session import _TRIAL_LOG
        rec = [json.loads(x) for x in _TRIAL_LOG.read_text().splitlines() if x.strip()][-1]
        assert rec["session"] == "sess-A"
        assert rec["heed"] is True
        assert isinstance(rec.get("ts"), (int, float))

    def test_other_session_does_not_credit_heed(self, _reg, tmp_path):
        # session A opens a trial; session B (same repo, shared state) uses the
        # tool. B must NOT be credited as heed for A's nudge.
        proj = tmp_path
        d = proj / ".kibitzer"
        d.mkdir()
        s0 = fresh_state()
        s0["mode"] = "free"
        save_state(s0, d)
        with KibitzerSession(project_dir=proj, session_id="sess-A") as s:
            _cfg(s)
            s.before_call("Bash", {"command": "grep -rn foo src/"})
        # session B uses squackit
        with KibitzerSession(project_dir=proj, session_id="sess-B") as s:
            _cfg(s)
            s.after_call("mcp__plugin_squackit_squackit__search", {}, success=True)
        # B's tool use must never produce a heed=True record for A's nudge,
        # whether A's trial is still open or later expires as heed=False.
        from kibitzer.session import _TRIAL_LOG
        recs = ([json.loads(x) for x in _TRIAL_LOG.read_text().splitlines() if x.strip()]
                if _TRIAL_LOG.exists() else [])
        assert not any(r["heed"] for r in recs)
