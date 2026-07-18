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


@patch("kibitzer.session.build_registry", return_value=[SquackitInterceptor()])
class TestAdaptiveDecay:
    """After N consecutive un-heeded NUDGE trials, a plugin's nudges are
    suppressed (intercepts still logged; disposition recorded as
    arm="suppressed"). A heeded nudge resets the streak."""

    def _cfg(self, session, threshold=3):
        session._config.setdefault("plugins", {})["squackit"] = {
            "mode": "suggest", "enabled": True,
        }
        session._config["experiment"] = {
            "nudge_probability": 1.0,  # always NUDGE arm
            "heed_window": 2,
            "decay_threshold": threshold,
        }

    def _new_session_state(self, proj):
        """Simulate a fresh session: clear the per-session nudge dedup."""
        st = load_state(proj / ".kibitzer")
        st["suggestions_given"] = []
        st["engaged_plugins"] = []
        save_state(st, proj / ".kibitzer")

    def _nudge(self, proj, threshold=3):
        with KibitzerSession(project_dir=proj) as s:
            self._cfg(s, threshold)
            return s.before_call("Bash", {"command": "grep -rn foo src/"})

    def _expire_unheeded(self, proj, threshold=3):
        # run past the heed window (2) with non-matching tools
        for _ in range(4):
            with KibitzerSession(project_dir=proj) as s:
                self._cfg(s, threshold)
                s.after_call("Read", {"file_path": "x"}, success=True)

    def _heed(self, proj, threshold=3):
        with KibitzerSession(project_dir=proj) as s:
            self._cfg(s, threshold)
            s.after_call(
                "mcp__plugin_squackit_squackit__search", {}, success=True,
            )

    def _ignore_once(self, proj, threshold=3):
        assert self._nudge(proj, threshold) is not None
        self._expire_unheeded(proj, threshold)
        self._new_session_state(proj)

    def test_streak_increments_on_unheeded_nudge(self, _reg, tmp_path):
        proj = _project(tmp_path)
        self._ignore_once(proj)
        st = load_state(proj / ".kibitzer")
        assert st["nudge_ignore_streaks"]["squackit"] == 1

    def test_suppressed_after_threshold(self, _reg, tmp_path):
        proj = _project(tmp_path)
        for _ in range(3):
            self._ignore_once(proj)
        st = load_state(proj / ".kibitzer")
        assert st["nudge_ignore_streaks"]["squackit"] == 3
        # 4th eligible bypass: nudge is suppressed, no trial opened
        result = self._nudge(proj)
        assert result is None
        st = load_state(proj / ".kibitzer")
        assert not st.get("nudge_trials")

    def test_suppression_logs_disposition_and_intercept(self, _reg, tmp_path):
        proj = _project(tmp_path, nudge_ignore_streaks={"squackit": 3})
        result = self._nudge(proj)
        assert result is None
        # disposition recorded distinctly — never pollutes heed metrics
        from kibitzer.session import _TRIAL_LOG
        recs = [json.loads(x) for x in _TRIAL_LOG.read_text().splitlines() if x.strip()]
        assert recs[-1]["arm"] == "suppressed"
        assert recs[-1]["heed"] is None
        # intercept still logged for telemetry
        log = (proj / ".kibitzer" / "intercept.log").read_text()
        assert "squackit" in log
        # and a store event marks the suppression
        from kibitzer.store import KibitzerStore
        store = KibitzerStore(proj / ".kibitzer" / "store.sqlite")
        events = store.query_events(event_type="nudge_suppressed")
        assert len(events) == 1

    def test_heed_resets_streak(self, _reg, tmp_path):
        proj = _project(tmp_path)
        self._ignore_once(proj)
        self._ignore_once(proj)
        assert load_state(proj / ".kibitzer")["nudge_ignore_streaks"]["squackit"] == 2
        # a heeded nudge resets the counter
        assert self._nudge(proj) is not None
        self._heed(proj)
        assert load_state(proj / ".kibitzer")["nudge_ignore_streaks"]["squackit"] == 0
        # nudges keep flowing afterwards
        self._new_session_state(proj)
        assert self._nudge(proj) is not None

    def test_unheeded_control_does_not_count(self, _reg, tmp_path, monkeypatch):
        proj = _project(tmp_path)
        monkeypatch.setattr("kibitzer.session.random.random", lambda: 0.99)  # CONTROL
        with KibitzerSession(project_dir=proj) as s:
            self._cfg(s)
            s._config["experiment"]["nudge_probability"] = 0.5
            s.before_call("Bash", {"command": "grep -rn foo src/"})
        self._expire_unheeded(proj)
        st = load_state(proj / ".kibitzer")
        # control trials were never shown — an un-heeded control isn't "ignored"
        assert st["nudge_ignore_streaks"].get("squackit", 0) == 0

    def test_decay_disabled_with_zero_threshold(self, _reg, tmp_path):
        proj = _project(tmp_path, nudge_ignore_streaks={"squackit": 99})
        result = self._nudge(proj, threshold=0)
        assert result is not None  # still nudges

    def test_ab_bookkeeping_intact_below_threshold(self, _reg, tmp_path):
        proj = _project(tmp_path, nudge_ignore_streaks={"squackit": 2})
        assert self._nudge(proj) is not None  # threshold 3 not reached
        st = load_state(proj / ".kibitzer")
        assert st["nudge_trials"][0]["arm"] == "nudge"
