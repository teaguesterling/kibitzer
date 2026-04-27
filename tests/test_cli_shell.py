"""Tests for human-facing CLI commands and shell hooks."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from kibitzer.cli import cli
from kibitzer.state import fresh_state, save_state


@pytest.fixture
def runner():
    return CliRunner()


def _init_project(td):
    """Set up a minimal kibitzer project in td."""
    kib_dir = Path(td) / ".kibitzer"
    kib_dir.mkdir(exist_ok=True)
    state = fresh_state()
    state["mode"] = "implement"
    save_state(state, kib_dir)
    return kib_dir


class TestMode:
    def test_show_current_mode(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            _init_project(td)
            result = runner.invoke(cli, ["mode"], catch_exceptions=False)
            assert result.exit_code == 0
            assert "implement" in result.output

    def test_switch_mode(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            _init_project(td)
            result = runner.invoke(cli, ["mode", "test"], catch_exceptions=False)
            assert result.exit_code == 0
            assert "implement" in result.output
            assert "test" in result.output

    def test_switch_to_unknown_mode(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            _init_project(td)
            result = runner.invoke(cli, ["mode", "nonexistent"])
            assert result.exit_code != 0

    def test_switch_persists(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            _init_project(td)
            runner.invoke(cli, ["mode", "test"], catch_exceptions=False)
            result = runner.invoke(cli, ["mode"], catch_exceptions=False)
            assert "test" in result.output


class TestStatus:
    def test_status_shows_mode(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            _init_project(td)
            result = runner.invoke(cli, ["status"], catch_exceptions=False)
            assert result.exit_code == 0
            assert "implement" in result.output
            assert "turns:" in result.output
            assert "calls:" in result.output

    def test_status_shows_previous_mode(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            kib_dir = _init_project(td)
            state = json.loads((kib_dir / "state.json").read_text())
            state["previous_mode"] = "explore"
            state["mode_switches"] = 2
            (kib_dir / "state.json").write_text(json.dumps(state))

            result = runner.invoke(cli, ["status"], catch_exceptions=False)
            assert "explore" in result.output
            assert "2 switches" in result.output


class TestPrompt:
    def test_text_output(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            _init_project(td)
            result = runner.invoke(cli, ["prompt"], catch_exceptions=False)
            assert result.exit_code == 0
            assert result.output.strip() == "implement"

    def test_text_with_failures(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            kib_dir = _init_project(td)
            state = json.loads((kib_dir / "state.json").read_text())
            state["consecutive_failures"] = 5
            (kib_dir / "state.json").write_text(json.dumps(state))

            result = runner.invoke(cli, ["prompt"], catch_exceptions=False)
            assert result.output.strip() == "implement!"

    def test_json_output(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            _init_project(td)
            result = runner.invoke(cli, ["prompt", "--format", "json"],
                                   catch_exceptions=False)
            data = json.loads(result.output)
            assert data["mode"] == "implement"
            assert data["consecutive_failures"] == 0

    def test_no_bang_under_threshold(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            kib_dir = _init_project(td)
            state = json.loads((kib_dir / "state.json").read_text())
            state["consecutive_failures"] = 2
            (kib_dir / "state.json").write_text(json.dumps(state))

            result = runner.invoke(cli, ["prompt"], catch_exceptions=False)
            assert "!" not in result.output


class TestValidateStaged:
    def test_no_staged_files(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            _init_project(td)
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = ""
                result = runner.invoke(cli, ["validate-staged"],
                                       catch_exceptions=False)
            assert result.exit_code == 0

    def test_allowed_files_pass(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            kib_dir = _init_project(td)
            state = json.loads((kib_dir / "state.json").read_text())
            state["mode"] = "free"
            (kib_dir / "state.json").write_text(json.dumps(state))

            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = "src/foo.py\ntests/bar.py\n"
                result = runner.invoke(cli, ["validate-staged"],
                                       catch_exceptions=False)
            assert result.exit_code == 0

    def test_violations_exit_1(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            kib_dir = _init_project(td)
            state = json.loads((kib_dir / "state.json").read_text())
            state["mode"] = "test"
            (kib_dir / "state.json").write_text(json.dumps(state))

            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = "src/main.py\n"
                result = runner.invoke(cli, ["validate-staged"])
            assert result.exit_code == 1

    def test_json_output_with_violations(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            kib_dir = _init_project(td)
            state = json.loads((kib_dir / "state.json").read_text())
            state["mode"] = "test"
            (kib_dir / "state.json").write_text(json.dumps(state))

            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = "src/main.py\n"
                result = runner.invoke(cli, ["validate-staged", "--format", "json"],
                                       catch_exceptions=False)
            data = json.loads(result.output)
            assert data["ok"] is False
            assert len(data["violations"]) == 1

    def test_git_not_found(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            _init_project(td)
            with patch("subprocess.run", side_effect=FileNotFoundError):
                result = runner.invoke(cli, ["validate-staged"])
            assert result.exit_code != 0

    def test_git_timeout(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            _init_project(td)
            with patch("subprocess.run",
                        side_effect=subprocess.TimeoutExpired("git", 10)):
                result = runner.invoke(cli, ["validate-staged"])
            assert result.exit_code != 0
            assert "timed out" in result.output


class TestShellPost:
    def test_no_output_for_uninteresting_command(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            _init_project(td)
            result = runner.invoke(cli, ["shell-post", "ls", "-la"],
                                   catch_exceptions=False)
            assert result.exit_code == 0
            assert result.output == ""

    def test_suggests_jetsam_for_git(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            _init_project(td)
            with patch("shutil.which", return_value="/usr/bin/jetsam"):
                result = runner.invoke(
                    cli, ["shell-post", "git", "commit", "-m", "fix"],
                    catch_exceptions=False,
                )
            assert result.exit_code == 0
            assert "jetsam save" in result.output

    def test_suggests_blq_for_pytest(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            _init_project(td)
            with patch("shutil.which", return_value="/usr/bin/blq"):
                result = runner.invoke(
                    cli, ["shell-post", "pytest", "tests/"],
                    catch_exceptions=False,
                )
            assert result.exit_code == 0
            assert "blq run test" in result.output

    def test_accepts_quoted_single_arg(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            _init_project(td)
            result = runner.invoke(cli, ["shell-post", "ls -la"],
                                   catch_exceptions=False)
            assert result.exit_code == 0

    def test_no_suggestion_without_tool(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            _init_project(td)
            with patch("shutil.which", return_value=None):
                result = runner.invoke(
                    cli, ["shell-post", "git", "commit", "-m", "fix"],
                    catch_exceptions=False,
                )
            assert result.exit_code == 0
            assert "consider:" not in result.output


class TestInitShell:
    def test_shell_flag_prints_snippet(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["init", "--shell"], catch_exceptions=False)
            assert result.exit_code == 0
            assert "_kibitzer_prompt" in result.output
            assert "_kibitzer_preexec" in result.output
            assert "Kibitzer initialized." in result.output

    def test_git_hooks_creates_pre_commit(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            (Path(td) / ".git" / "hooks").mkdir(parents=True)
            result = runner.invoke(cli, ["init", "--git-hooks"],
                                   catch_exceptions=False)
            assert result.exit_code == 0
            hook = Path(td) / ".git" / "hooks" / "pre-commit"
            assert hook.exists()
            assert "kibitzer" in hook.read_text()

    def test_combined_shell_and_git_hooks(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            (Path(td) / ".git" / "hooks").mkdir(parents=True)
            result = runner.invoke(
                cli, ["init", "--shell", "--git-hooks"],
                catch_exceptions=False,
            )
            assert result.exit_code == 0
            assert "_kibitzer_prompt" in result.output
            assert "Kibitzer initialized." in result.output
            hook = Path(td) / ".git" / "hooks" / "pre-commit"
            assert hook.exists()
            assert "kibitzer" in hook.read_text()


class TestStoreSource:
    def test_source_column_in_schema(self, tmp_path):
        from kibitzer.store import KibitzerStore
        store = KibitzerStore(tmp_path / "store.sqlite")
        store.init()
        store.append_event(event_type="test", source="shell")
        events = store.query_events(event_type="test")
        assert events[0]["source"] == "shell"

    def test_default_source_is_agent(self, tmp_path):
        from kibitzer.store import KibitzerStore
        store = KibitzerStore(tmp_path / "store.sqlite")
        store.init()
        store.append_event(event_type="test")
        events = store.query_events(event_type="test")
        assert events[0]["source"] == "agent"

    def test_migration_adds_source_column(self, tmp_path):
        import sqlite3
        db_path = tmp_path / "store.sqlite"
        con = sqlite3.connect(str(db_path))
        con.executescript("""
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT (datetime('now')),
                session_id TEXT,
                event_type TEXT NOT NULL,
                tool_name TEXT,
                tool_input TEXT,
                success INTEGER,
                mode TEXT,
                data TEXT
            );
        """)
        con.execute("INSERT INTO events (event_type) VALUES ('old_event')")
        con.commit()
        con.close()

        from kibitzer.store import KibitzerStore
        store = KibitzerStore(db_path)
        store.init()
        events = store.query_events(event_type="old_event")
        assert events[0]["source"] == "agent"


class TestSessionLogEvent:
    def test_log_event_public_api(self, tmp_path):
        from kibitzer.session import KibitzerSession
        with KibitzerSession(project_dir=tmp_path) as session:
            session.log_event("test_event", data='{"key": "val"}', source="shell")
        from kibitzer.store import KibitzerStore
        store = KibitzerStore(tmp_path / ".kibitzer" / "store.sqlite")
        store.init()
        events = store.query_events(event_type="test_event")
        assert len(events) == 1
        assert events[0]["source"] == "shell"
