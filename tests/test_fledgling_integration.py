"""Tests for fledgling query integration in the coach.

Fledgling queries are mocked since the tool may not be installed.
Tests verify:
- Graceful degradation when fledgling is unavailable
- Pattern detection from fledgling query results
- Dedup of fledgling-sourced patterns
- Integration with the existing coach pipeline
"""

from pathlib import Path
from unittest.mock import patch, MagicMock


from kibitzer.coach import fledgling
from kibitzer.coach.observer import detect_patterns, _detect_fledgling_patterns
from kibitzer.coach.suggestions import generate_suggestions
from kibitzer.state import fresh_state


# ===========================================================================
# fledgling.py — availability and query execution
# ===========================================================================

class TestFledglingAvailability:
    @patch("kibitzer.coach.fledgling.shutil.which", return_value=None)
    def test_not_available_when_not_installed(self, mock_which):
        assert fledgling.is_available() is False

    @patch("kibitzer.coach.fledgling.shutil.which", return_value="/usr/bin/fledgling")
    @patch("kibitzer.coach.fledgling._find_init", return_value=None)
    def test_not_available_when_not_initialized(self, mock_init, mock_which):
        assert fledgling.is_available() is False

    @patch("kibitzer.coach.fledgling.shutil.which", return_value="/usr/bin/fledgling")
    @patch("kibitzer.coach.fledgling._find_init", return_value=Path("/tmp/init.sql"))
    def test_available_when_installed_and_initialized(self, mock_init, mock_which):
        assert fledgling.is_available() is True


class TestFledglingInitDetection:
    def test_finds_project_local_init(self, tmp_path):
        init_file = tmp_path / ".fledgling-init.sql"
        init_file.write_text("-- init")
        assert fledgling._find_init(tmp_path) == init_file

    def test_finds_global_init(self, tmp_path, monkeypatch):
        global_init = tmp_path / ".fledgling" / "init.sql"
        global_init.parent.mkdir()
        global_init.write_text("-- init")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # No project-local init
        assert fledgling._find_init(tmp_path / "some-project") is not None

    def test_env_var_overrides(self, tmp_path, monkeypatch):
        init_file = tmp_path / "custom-init.sql"
        init_file.write_text("-- init")
        monkeypatch.setenv("FLEDGLING_INIT", str(init_file))
        assert fledgling._find_init() == init_file

    def test_returns_none_when_nothing_found(self, tmp_path):
        assert fledgling._find_init(tmp_path) is None


class TestFledglingQuery:
    @patch("kibitzer.coach.fledgling.is_available", return_value=False)
    def test_returns_none_when_unavailable(self, mock_avail):
        result = fledgling.query("SELECT 1")
        assert result is None

    @patch("kibitzer.coach.fledgling.is_available", return_value=True)
    @patch("kibitzer.coach.fledgling.subprocess.run")
    def test_returns_parsed_json(self, mock_run, mock_avail):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='[{"tool_name": "Edit", "count": 5}]',
        )
        result = fledgling.query("SELECT tool_name, count FROM ...")
        assert result == [{"tool_name": "Edit", "count": 5}]

    @patch("kibitzer.coach.fledgling.is_available", return_value=True)
    @patch("kibitzer.coach.fledgling.subprocess.run")
    def test_returns_none_on_failure(self, mock_run, mock_avail):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        result = fledgling.query("SELECT bad")
        assert result is None

    @patch("kibitzer.coach.fledgling.is_available", return_value=True)
    @patch("kibitzer.coach.fledgling.subprocess.run")
    def test_returns_none_on_timeout(self, mock_run, mock_avail):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="fledgling", timeout=5)
        result = fledgling.query("SELECT slow")
        assert result is None

    @patch("kibitzer.coach.fledgling.is_available", return_value=True)
    @patch("kibitzer.coach.fledgling.subprocess.run")
    def test_returns_empty_list_on_empty_output(self, mock_run, mock_avail):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        result = fledgling.query("SELECT nothing")
        assert result == []

    @patch("kibitzer.coach.fledgling.is_available", return_value=True)
    @patch("kibitzer.coach.fledgling.subprocess.run")
    def test_returns_none_on_invalid_json(self, mock_run, mock_avail):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json")
        result = fledgling.query("SELECT bad_json")
        assert result is None

    @patch("kibitzer.coach.fledgling.is_available", return_value=True)
    @patch("kibitzer.coach.fledgling.subprocess.run")
    def test_single_dict_wrapped_in_list(self, mock_run, mock_avail):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"tool_name": "Edit", "count": 1}',
        )
        result = fledgling.query("SELECT one_row")
        assert result == [{"tool_name": "Edit", "count": 1}]


# ===========================================================================
# Convenience query functions
# ===========================================================================

class TestRepeatedSearchPatterns:
    @patch("kibitzer.coach.fledgling.query")
    def test_returns_results(self, mock_query):
        mock_query.return_value = [
            {"pattern": "def handle_request", "tool": "Grep", "count": 4},
            {"pattern": "src/auth.py", "tool": "Read", "count": 3},
        ]
        result = fledgling.repeated_search_patterns()
        assert len(result) == 2
        assert result[0]["count"] == 4

    @patch("kibitzer.coach.fledgling.query")
    def test_returns_none_when_unavailable(self, mock_query):
        mock_query.return_value = None
        result = fledgling.repeated_search_patterns()
        assert result is None


class TestReplaceableBashCommands:
    @patch("kibitzer.coach.fledgling.query")
    def test_returns_results(self, mock_query):
        mock_query.return_value = [
            {"command": "grep", "replaceable_by": "FindDefinitions", "count": 3},
        ]
        result = fledgling.replaceable_bash_commands()
        assert len(result) == 1
        assert result[0]["replaceable_by"] == "FindDefinitions"


# ===========================================================================
# Pattern detection with fledgling data
# ===========================================================================

class TestFledglingPatterns:
    @patch("kibitzer.coach.fledgling.is_available", return_value=True)
    @patch("kibitzer.coach.fledgling.repeated_search_patterns")
    @patch("kibitzer.coach.fledgling.replaceable_bash_commands")
    def test_repeated_search_fires(self, mock_bash, mock_search, mock_avail, tmp_path):
        mock_search.return_value = [
            {"pattern": "def handle_request", "tool": "Grep", "count": 4},
        ]
        mock_bash.return_value = []

        state = fresh_state()
        patterns = _detect_fledgling_patterns(state, tmp_path)
        matching = [(pid, msg) for pid, msg in patterns if pid == "fledgling_repeated_search"]
        assert len(matching) == 1
        assert "def handle_request" in matching[0][1]
        assert "4 times" in matching[0][1]

    @patch("kibitzer.coach.fledgling.is_available", return_value=True)
    @patch("kibitzer.coach.fledgling.repeated_search_patterns")
    @patch("kibitzer.coach.fledgling.replaceable_bash_commands")
    def test_replaceable_bash_fires(self, mock_bash, mock_search, mock_avail, tmp_path):
        mock_search.return_value = []
        mock_bash.return_value = [
            {"command": "grep", "replaceable_by": "FindDefinitions", "count": 3},
        ]

        state = fresh_state()
        patterns = _detect_fledgling_patterns(state, tmp_path)
        matching = [(pid, msg) for pid, msg in patterns if pid == "fledgling_replaceable_bash"]
        assert len(matching) == 1
        assert "FindDefinitions" in matching[0][1]

    @patch("kibitzer.coach.fledgling.is_available", return_value=True)
    @patch("kibitzer.coach.fledgling.repeated_search_patterns")
    @patch("kibitzer.coach.fledgling.replaceable_bash_commands")
    def test_single_replaceable_bash_does_not_fire(self, mock_bash, mock_search, mock_avail, tmp_path):
        """Only fire when count >= 2."""
        mock_search.return_value = []
        mock_bash.return_value = [
            {"command": "grep", "replaceable_by": "FindDefinitions", "count": 1},
        ]

        state = fresh_state()
        patterns = _detect_fledgling_patterns(state, tmp_path)
        matching = [pid for pid, _ in patterns if pid == "fledgling_replaceable_bash"]
        assert len(matching) == 0

    @patch("kibitzer.coach.fledgling.is_available", return_value=False)
    def test_no_patterns_when_unavailable(self, mock_avail, tmp_path):
        state = fresh_state()
        patterns = _detect_fledgling_patterns(state, tmp_path)
        assert len(patterns) == 0

    @patch("kibitzer.coach.fledgling.is_available", return_value=True)
    @patch("kibitzer.coach.fledgling.repeated_search_patterns")
    @patch("kibitzer.coach.fledgling.replaceable_bash_commands")
    def test_none_results_handled(self, mock_bash, mock_search, mock_avail, tmp_path):
        """Query returning None (error) should not crash."""
        mock_search.return_value = None
        mock_bash.return_value = None

        state = fresh_state()
        patterns = _detect_fledgling_patterns(state, tmp_path)
        assert len(patterns) == 0

    @patch("kibitzer.coach.fledgling.is_available", return_value=True)
    @patch("kibitzer.coach.fledgling.repeated_search_patterns")
    @patch("kibitzer.coach.fledgling.replaceable_bash_commands")
    def test_long_pattern_truncated(self, mock_bash, mock_search, mock_avail, tmp_path):
        mock_search.return_value = [
            {"pattern": "a" * 100, "tool": "Grep", "count": 5},
        ]
        mock_bash.return_value = []

        state = fresh_state()
        patterns = _detect_fledgling_patterns(state, tmp_path)
        msg = patterns[0][1]
        # Pattern should be truncated
        assert "..." in msg
        assert len(msg) < 200


# ===========================================================================
# Integration: fledgling patterns flow through detect_patterns + generate_suggestions
# ===========================================================================

class TestFledglingIntegration:
    @patch("kibitzer.coach.fledgling.is_available", return_value=True)
    @patch("kibitzer.coach.fledgling.repeated_search_patterns")
    @patch("kibitzer.coach.fledgling.replaceable_bash_commands")
    def test_fledgling_patterns_in_detect_patterns(self, mock_bash, mock_search, mock_avail, tmp_path):
        mock_search.return_value = [
            {"pattern": "def foo", "tool": "Grep", "count": 3},
        ]
        mock_bash.return_value = []

        state = fresh_state()
        patterns = detect_patterns(state, project_dir=tmp_path)
        fledgling_patterns = [pid for pid, _ in patterns if pid.startswith("fledgling_")]
        assert len(fledgling_patterns) >= 1

    def test_no_fledgling_patterns_without_project_dir(self):
        """Without project_dir, fledgling patterns should not fire."""
        state = fresh_state()
        patterns = detect_patterns(state, project_dir=None)
        fledgling_patterns = [pid for pid, _ in patterns if pid.startswith("fledgling_")]
        assert len(fledgling_patterns) == 0

    @patch("kibitzer.coach.fledgling.is_available", return_value=True)
    @patch("kibitzer.coach.fledgling.repeated_search_patterns")
    @patch("kibitzer.coach.fledgling.replaceable_bash_commands")
    def test_fledgling_patterns_deduped(self, mock_bash, mock_search, mock_avail, tmp_path):
        mock_search.return_value = [
            {"pattern": "def foo", "tool": "Grep", "count": 3},
        ]
        mock_bash.return_value = []

        state = fresh_state()
        state["suggestions_given"] = ["fledgling_repeated_search"]
        suggestions = generate_suggestions(state, project_dir=tmp_path)
        search_suggestions = [s for s in suggestions if "def foo" in s]
        assert len(search_suggestions) == 0

    @patch("kibitzer.coach.fledgling.is_available", return_value=True)
    @patch("kibitzer.coach.fledgling.repeated_search_patterns")
    @patch("kibitzer.coach.fledgling.replaceable_bash_commands")
    def test_fledgling_and_state_patterns_combine(self, mock_bash, mock_search, mock_avail, tmp_path):
        """Both state-based and fledgling-based patterns should appear."""
        mock_search.return_value = [
            {"pattern": "def bar", "tool": "Grep", "count": 3},
        ]
        mock_bash.return_value = []

        state = fresh_state()
        state["edits_since_test"] = 10  # triggers edit_without_test
        state["suggestions_given"] = []

        suggestions = generate_suggestions(state, project_dir=tmp_path)
        assert len(suggestions) >= 2  # at least edit_without_test + fledgling_repeated_search
        has_test = any("test" in s.lower() for s in suggestions)
        has_fledgling = any("def bar" in s for s in suggestions)
        assert has_test
        assert has_fledgling
