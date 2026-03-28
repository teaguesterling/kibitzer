"""Read and write .kibitzer/state.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

STATE_FILENAME = "state.json"


def fresh_state(default_mode: str = "implement") -> dict[str, Any]:
    """Return a blank state dict with all expected fields."""
    return {
        "mode": default_mode,
        "previous_mode": None,
        "failure_count": 0,
        "success_count": 0,
        "consecutive_failures": 0,
        "turns_in_mode": 0,
        "turns_in_previous_mode": 0,
        "total_calls": 0,
        "mode_switches": 0,
        "tools_used_in_mode": {},
        "suggestions_given": [],
        "model": None,
        "session_id": None,
        # Coach observation counters
        "consecutive_edit_failures": 0,
        "last_failed_edit_file": "",
        "consecutive_reads": 0,
        "edits_since_test": 0,
        "last_edit_turn": 0,
        "semantic_tools_used": False,
    }


def load_state(state_dir: Path) -> dict[str, Any]:
    """Load state from state_dir/state.json. Returns fresh state if missing."""
    state_file = state_dir / STATE_FILENAME
    if not state_file.exists():
        return fresh_state()
    with open(state_file) as f:
        saved = json.load(f)
    # Merge with fresh state to fill any missing fields
    state = fresh_state()
    state.update(saved)
    return state


def save_state(state: dict[str, Any], state_dir: Path) -> None:
    """Write state to state_dir/state.json. Creates directory if needed."""
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / STATE_FILENAME
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)
