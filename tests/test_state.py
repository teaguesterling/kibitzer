import json
import pytest
from kibitzer.state import load_state, save_state, fresh_state


def test_fresh_state_has_defaults():
    state = fresh_state()
    assert state["mode"] == "implement"
    assert state["failure_count"] == 0
    assert state["success_count"] == 0
    assert state["consecutive_failures"] == 0
    assert state["turns_in_mode"] == 0
    assert state["turns_in_previous_mode"] == 0
    assert state["total_calls"] == 0
    assert state["mode_switches"] == 0
    assert state["tools_used_in_mode"] == {}
    assert state["suggestions_given"] == []
    assert state["previous_mode"] is None
    assert state["model"] is None
    assert state["session_id"] is None


def test_save_and_load_roundtrip(state_dir):
    state = fresh_state()
    state["mode"] = "debug"
    state["failure_count"] = 3
    save_state(state, state_dir)

    loaded = load_state(state_dir)
    assert loaded["mode"] == "debug"
    assert loaded["failure_count"] == 3


def test_load_nonexistent_returns_fresh(tmp_path):
    state = load_state(tmp_path / "nonexistent")
    assert state["mode"] == "implement"
    assert state["total_calls"] == 0


def test_save_creates_directory(tmp_path):
    d = tmp_path / ".kibitzer"
    assert not d.exists()
    state = fresh_state()
    save_state(state, d)
    assert d.exists()
    assert (d / "state.json").exists()


def test_state_preserves_extra_fields(state_dir):
    """Forward-compatibility: unknown fields survive roundtrip."""
    state = fresh_state()
    state["custom_field"] = "hello"
    save_state(state, state_dir)
    loaded = load_state(state_dir)
    assert loaded["custom_field"] == "hello"
