"""Tests for tool discovery and tool-aware coach suggestions."""

import json
from unittest.mock import patch

from kibitzer.coach.tools import discover_tools, suggest_search_tool, suggest_test_tool, suggest_save_tool
from kibitzer.coach.observer import detect_patterns, _search_tool_hint, _test_tool_hint
from kibitzer.state import fresh_state


# ===========================================================================
# Tool discovery from .mcp.json
# ===========================================================================

class TestDiscoverTools:
    def test_reads_mcp_json(self, tmp_path):
        mcp = {"mcpServers": {
            "blq": {"command": "blq", "args": ["mcp", "serve"]},
            "jetsam": {"command": "jetsam", "args": ["serve"]},
            "fledgling": {"command": "duckdb", "args": ["-init", ".fledgling-init.sql"]},
        }}
        (tmp_path / ".mcp.json").write_text(json.dumps(mcp))

        result = discover_tools(tmp_path)
        assert result["has_blq"]
        assert result["has_jetsam"]
        assert result["has_fledgling"]
        assert "FindDefinitions" in result["tools"]
        assert "blq run test" in result["tools"]
        assert "jetsam save" in result["tools"]

    @patch("kibitzer.coach.tools.shutil.which", return_value=None)
    def test_partial_mcp_json(self, mock_which, tmp_path):
        """Only blq registered — others should be absent (CLI fallback also disabled)."""
        mcp = {"mcpServers": {"blq_mcp": {"command": "blq", "args": ["mcp", "serve"]}}}
        (tmp_path / ".mcp.json").write_text(json.dumps(mcp))

        result = discover_tools(tmp_path)
        assert result["has_blq"]
        assert not result["has_jetsam"]
        assert not result["has_fledgling"]
        assert "blq run test" in result["tools"]
        assert "FindDefinitions" not in result["tools"]

    def test_no_mcp_json(self, tmp_path):
        """No .mcp.json — fall back to CLI availability."""
        with patch("kibitzer.coach.tools.shutil.which", return_value=None):
            result = discover_tools(tmp_path)
        assert not result["has_blq"]
        assert not result["has_jetsam"]
        assert not result["has_fledgling"]
        assert result["tools"] == []

    @patch("kibitzer.coach.tools.shutil.which")
    def test_cli_fallback(self, mock_which, tmp_path):
        """No .mcp.json but CLI available — discover via which."""
        def which_side_effect(name):
            return f"/usr/bin/{name}" if name == "jetsam" else None
        mock_which.side_effect = which_side_effect

        result = discover_tools(tmp_path)
        assert result["has_jetsam"]
        assert not result["has_blq"]
        assert "jetsam save" in result["tools"]

    def test_mcp_json_takes_priority_over_cli(self, tmp_path):
        """If .mcp.json lists a server, don't also check CLI for it."""
        mcp = {"mcpServers": {"fledgling": {"command": "duckdb"}}}
        (tmp_path / ".mcp.json").write_text(json.dumps(mcp))

        result = discover_tools(tmp_path)
        assert result["has_fledgling"]
        # FindDefinitions should appear once, not twice
        assert result["tools"].count("FindDefinitions") == 1

    def test_corrupt_mcp_json(self, tmp_path):
        """Corrupt .mcp.json should not crash."""
        (tmp_path / ".mcp.json").write_text("not json!!")
        with patch("kibitzer.coach.tools.shutil.which", return_value=None):
            result = discover_tools(tmp_path)
        assert result["tools"] == []

    def test_server_name_substring_matching(self, tmp_path):
        """Server names like 'blq_mcp' or 'venv_blq' should match 'blq'."""
        mcp = {"mcpServers": {
            "venv_blq": {"command": ".venv/bin/blq"},
            "blq_mcp": {"command": "blq"},
        }}
        (tmp_path / ".mcp.json").write_text(json.dumps(mcp))

        result = discover_tools(tmp_path)
        assert result["has_blq"]


# ===========================================================================
# Suggestion helpers
# ===========================================================================

class TestSuggestHelpers:
    def test_search_tool_with_fledgling(self):
        available = {"has_fledgling": True, "has_blq": False, "has_jetsam": False}
        hint = suggest_search_tool(available)
        assert hint is not None
        assert "FindDefinitions" in hint

    def test_search_tool_without_fledgling(self):
        available = {"has_fledgling": False, "has_blq": False, "has_jetsam": False}
        hint = suggest_search_tool(available)
        assert hint is None

    def test_test_tool_with_blq(self):
        available = {"has_blq": True, "has_fledgling": False, "has_jetsam": False}
        hint = suggest_test_tool(available)
        assert hint is not None
        assert "blq" in hint

    def test_test_tool_without_blq(self):
        available = {"has_blq": False}
        assert suggest_test_tool(available) is None

    def test_save_tool_with_jetsam(self):
        available = {"has_jetsam": True}
        hint = suggest_save_tool(available)
        assert "jetsam" in hint

    def test_save_tool_without_jetsam(self):
        available = {"has_jetsam": False}
        assert suggest_save_tool(available) is None


# ===========================================================================
# Observer hint functions
# ===========================================================================

class TestObserverHints:
    def test_search_hint_with_fledgling(self):
        available = {"has_fledgling": True}
        hint = _search_tool_hint(available)
        assert "FindDefinitions" in hint

    def test_search_hint_without_fledgling(self):
        available = {"has_fledgling": False}
        hint = _search_tool_hint(available)
        assert "FindDefinitions" not in hint
        assert "Grep" in hint  # generic fallback

    def test_test_hint_with_blq(self):
        available = {"has_blq": True}
        hint = _test_tool_hint(available)
        assert "blq" in hint

    def test_test_hint_without_blq(self):
        available = {"has_blq": False}
        hint = _test_tool_hint(available)
        assert "blq" not in hint
        assert "test" in hint.lower()


# ===========================================================================
# Observer patterns with tool awareness
# ===========================================================================

class TestToolAwarePatterns:
    def test_sequential_reads_mentions_fledgling_when_available(self, tmp_path):
        mcp = {"mcpServers": {"fledgling": {"command": "duckdb"}}}
        (tmp_path / ".mcp.json").write_text(json.dumps(mcp))

        state = fresh_state()
        state["consecutive_reads"] = 5
        patterns = detect_patterns(state, project_dir=tmp_path)
        matching = [(pid, msg) for pid, msg in patterns if pid == "sequential_reads"]
        assert len(matching) == 1
        assert "FindDefinitions" in matching[0][1]

    def test_sequential_reads_generic_without_fledgling(self, tmp_path):
        # No .mcp.json, no fledgling
        with patch("kibitzer.coach.tools.shutil.which", return_value=None):
            state = fresh_state()
            state["consecutive_reads"] = 5
            patterns = detect_patterns(state, project_dir=tmp_path)
            matching = [(pid, msg) for pid, msg in patterns if pid == "sequential_reads"]
            assert len(matching) == 1
            assert "FindDefinitions" not in matching[0][1]
            assert "Grep" in matching[0][1]

    def test_edit_without_test_mentions_blq_when_available(self, tmp_path):
        mcp = {"mcpServers": {"blq": {"command": "blq"}}}
        (tmp_path / ".mcp.json").write_text(json.dumps(mcp))

        state = fresh_state()
        state["edits_since_test"] = 6
        patterns = detect_patterns(state, project_dir=tmp_path)
        matching = [(pid, msg) for pid, msg in patterns if pid == "edit_without_test"]
        assert len(matching) == 1
        assert "blq" in matching[0][1]

    def test_edit_without_test_generic_without_blq(self, tmp_path):
        with patch("kibitzer.coach.tools.shutil.which", return_value=None):
            state = fresh_state()
            state["edits_since_test"] = 6
            patterns = detect_patterns(state, project_dir=tmp_path)
            matching = [(pid, msg) for pid, msg in patterns if pid == "edit_without_test"]
            assert len(matching) == 1
            assert "blq" not in matching[0][1]
            assert "test" in matching[0][1].lower()

    def test_semantic_underuse_only_with_fledgling(self, tmp_path):
        """semantic_underuse should NOT fire if fledgling is not available."""
        with patch("kibitzer.coach.tools.shutil.which", return_value=None):
            state = fresh_state()
            state["total_calls"] = 15
            state["tools_used_in_mode"] = {"Read": 8, "Grep": 3}
            state["semantic_tools_used"] = False
            patterns = detect_patterns(state, project_dir=tmp_path)
            matching = [pid for pid, _ in patterns if pid == "semantic_underuse"]
            assert len(matching) == 0

    def test_semantic_underuse_fires_with_fledgling(self, tmp_path):
        mcp = {"mcpServers": {"fledgling": {"command": "duckdb"}}}
        (tmp_path / ".mcp.json").write_text(json.dumps(mcp))

        state = fresh_state()
        state["total_calls"] = 15
        state["tools_used_in_mode"] = {"Read": 8, "Grep": 3}
        state["semantic_tools_used"] = False

        # Need to mock fledgling not being available for the fledgling_patterns check
        with patch("kibitzer.coach.fledgling.is_available", return_value=False):
            patterns = detect_patterns(state, project_dir=tmp_path)
        matching = [pid for pid, _ in patterns if pid == "semantic_underuse"]
        assert len(matching) == 1

    def test_no_project_dir_still_works(self):
        """Without project_dir, patterns still fire with generic hints."""
        state = fresh_state()
        state["consecutive_reads"] = 5
        state["edits_since_test"] = 6
        patterns = detect_patterns(state, project_dir=None)

        reads = [pid for pid, _ in patterns if pid == "sequential_reads"]
        edits = [pid for pid, _ in patterns if pid == "edit_without_test"]
        assert len(reads) == 1
        assert len(edits) == 1
