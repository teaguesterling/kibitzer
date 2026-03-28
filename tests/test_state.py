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


# --- Corruption resilience ---

def test_load_empty_file_returns_fresh(state_dir):
    (state_dir / "state.json").write_text("")
    state = load_state(state_dir)
    assert state["mode"] == "implement"
    assert state["total_calls"] == 0


def test_load_invalid_json_returns_fresh(state_dir):
    (state_dir / "state.json").write_text("{not valid json!!")
    state = load_state(state_dir)
    assert state["mode"] == "implement"


def test_load_json_array_returns_fresh(state_dir):
    (state_dir / "state.json").write_text("[1, 2, 3]")
    state = load_state(state_dir)
    assert state["mode"] == "implement"


def test_load_json_string_returns_fresh(state_dir):
    (state_dir / "state.json").write_text('"just a string"')
    state = load_state(state_dir)
    assert state["mode"] == "implement"


def test_load_json_null_returns_fresh(state_dir):
    (state_dir / "state.json").write_text("null")
    state = load_state(state_dir)
    assert state["mode"] == "implement"


def test_load_partial_state_fills_defaults(state_dir):
    """State with only some fields should get defaults for the rest."""
    (state_dir / "state.json").write_text('{"mode": "debug", "total_calls": 5}')
    state = load_state(state_dir)
    assert state["mode"] == "debug"
    assert state["total_calls"] == 5
    assert state["failure_count"] == 0  # filled from fresh_state


def test_save_atomic_no_partial_writes(state_dir):
    """save_state uses tmp+rename so a crash mid-write won't corrupt."""
    state = fresh_state()
    state["mode"] = "debug"
    save_state(state, state_dir)

    # Verify no .tmp file left behind
    assert not (state_dir / "state.json.tmp").exists()
    # Verify the file is valid
    loaded = load_state(state_dir)
    assert loaded["mode"] == "debug"
