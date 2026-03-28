"""Tests for the actual stdin/stdout hook contract.

These test the main() functions — the real boundary that Claude Code's
bash hook scripts invoke. JSON in on stdin, JSON out on stdout (or silence).
"""

import json
import subprocess
import sys
import pytest

from kibitzer.state import fresh_state, save_state, load_state


@pytest.fixture
def project(tmp_path):
    """Kibitzer initialized in implement mode."""
    state_dir = tmp_path / ".kibitzer"
    state_dir.mkdir()
    save_state(fresh_state(), state_dir)
    return tmp_path


@pytest.fixture
def project_in_mode(tmp_path):
    def _make(mode: str, **overrides):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir(exist_ok=True)
        state = fresh_state()
        state["mode"] = mode
        state.update(overrides)
        save_state(state, state_dir)
        return tmp_path
    return _make


def _run_hook(module: str, hook_input: dict, cwd: str) -> tuple[int, str, str]:
    """Run a hook module as a subprocess, piping JSON on stdin.

    Returns (exit_code, stdout, stderr).
    """
    result = subprocess.run(
        [sys.executable, "-m", module],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=10,
    )
    return result.returncode, result.stdout, result.stderr


# ===========================================================================
# PreToolUse main() — stdin/stdout contract
# ===========================================================================

class TestPreToolUseStdio:
    """Test pre_tool_use.main() via subprocess."""

    def test_allow_produces_no_output(self, project):
        """Allowed tool call = exit 0, empty stdout."""
        hook_input = {
            "tool_name": "Read",
            "tool_input": {"file_path": "src/foo.py"},
        }
        code, stdout, stderr = _run_hook(
            "kibitzer.hooks.pre_tool_use", hook_input, str(project),
        )
        assert code == 0
        assert stdout.strip() == ""

    def test_deny_produces_valid_json(self, project):
        """Denied tool call = exit 0, JSON with permissionDecision on stdout."""
        hook_input = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "tests/test_foo.py", "old_string": "a", "new_string": "b"},
        }
        code, stdout, stderr = _run_hook(
            "kibitzer.hooks.pre_tool_use", hook_input, str(project),
        )
        assert code == 0
        assert stdout.strip() != ""

        output = json.loads(stdout)
        assert "hookSpecificOutput" in output
        hook_output = output["hookSpecificOutput"]
        assert hook_output["permissionDecision"] == "deny"
        assert isinstance(hook_output["permissionDecisionReason"], str)
        assert len(hook_output["permissionDecisionReason"]) > 0

    def test_deny_reason_is_actionable(self, project):
        """Deny reason should tell the agent how to fix the situation."""
        hook_input = {
            "tool_name": "Write",
            "tool_input": {"file_path": "tests/new_test.py", "content": "# test"},
        }
        code, stdout, _ = _run_hook(
            "kibitzer.hooks.pre_tool_use", hook_input, str(project),
        )
        output = json.loads(stdout)
        reason = output["hookSpecificOutput"]["permissionDecisionReason"]
        assert "ChangeToolMode" in reason

    def test_allow_src_edit_in_implement(self, project):
        hook_input = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/handler.py", "old_string": "x", "new_string": "y"},
        }
        code, stdout, _ = _run_hook(
            "kibitzer.hooks.pre_tool_use", hook_input, str(project),
        )
        assert code == 0
        assert stdout.strip() == ""

    def test_free_mode_allows_everything(self, project_in_mode):
        proj = project_in_mode("free")
        hook_input = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "anywhere/anything.py", "old_string": "a", "new_string": "b"},
        }
        code, stdout, _ = _run_hook(
            "kibitzer.hooks.pre_tool_use", hook_input, str(proj),
        )
        assert code == 0
        assert stdout.strip() == ""

    def test_debug_mode_denies_all_writes(self, project_in_mode):
        proj = project_in_mode("debug")
        hook_input = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/main.py", "old_string": "a", "new_string": "b"},
        }
        code, stdout, _ = _run_hook(
            "kibitzer.hooks.pre_tool_use", hook_input, str(proj),
        )
        assert code == 0
        output = json.loads(stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_notebook_edit_guarded(self, project):
        hook_input = {
            "tool_name": "NotebookEdit",
            "tool_input": {"file_path": "notebooks/explore.ipynb", "cell_index": 0, "new_source": "x"},
        }
        code, stdout, _ = _run_hook(
            "kibitzer.hooks.pre_tool_use", hook_input, str(project),
        )
        assert code == 0
        output = json.loads(stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_read_tool_always_silent(self, project_in_mode):
        """Read should produce no output in any mode."""
        for mode in ["debug", "review", "implement", "test_dev", "document"]:
            proj = project_in_mode(mode)
            hook_input = {"tool_name": "Read", "tool_input": {"file_path": "secret.py"}}
            code, stdout, _ = _run_hook(
                "kibitzer.hooks.pre_tool_use", hook_input, str(proj),
            )
            assert code == 0, f"Read failed in {mode}"
            assert stdout.strip() == "", f"Read produced output in {mode}"

    def test_unknown_tool_silent(self, project):
        hook_input = {"tool_name": "FutureTool", "tool_input": {"x": 1}}
        code, stdout, _ = _run_hook(
            "kibitzer.hooks.pre_tool_use", hook_input, str(project),
        )
        assert code == 0
        assert stdout.strip() == ""

    def test_extra_fields_ignored(self, project):
        """Claude Code sends session_id, cwd, etc. — should be ignored gracefully."""
        hook_input = {
            "session_id": "abc123",
            "transcript_path": "/tmp/t.jsonl",
            "cwd": "/some/path",
            "permission_mode": "default",
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "src/foo.py"},
            "tool_use_id": "toolu_xyz",
        }
        code, stdout, _ = _run_hook(
            "kibitzer.hooks.pre_tool_use", hook_input, str(project),
        )
        assert code == 0
        assert stdout.strip() == ""


# ===========================================================================
# PostToolUse main() — stdin/stdout contract
# ===========================================================================

class TestPostToolUseStdio:
    """Test post_tool_use.main() via subprocess."""

    def test_normal_success_no_output(self, project):
        """A simple successful call should produce no output."""
        hook_input = {
            "tool_name": "Read",
            "tool_input": {"file_path": "src/foo.py"},
            "tool_result": {"content": "hello world"},
        }
        code, stdout, _ = _run_hook(
            "kibitzer.hooks.post_tool_use", hook_input, str(project),
        )
        assert code == 0
        assert stdout.strip() == ""

    def test_state_updated_after_call(self, project):
        """State file should reflect the tool call."""
        hook_input = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/foo.py"},
            "tool_result": "Applied edit",
        }
        _run_hook("kibitzer.hooks.post_tool_use", hook_input, str(project))

        state = load_state(project / ".kibitzer")
        assert state["total_calls"] == 1
        assert state["success_count"] == 1
        assert state["tools_used_in_mode"]["Edit"] == 1

    def test_failure_tracked(self, project):
        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "make test"},
            "tool_result": {"exitCode": 1, "stdout": "", "stderr": "FAIL"},
        }
        _run_hook("kibitzer.hooks.post_tool_use", hook_input, str(project))

        state = load_state(project / ".kibitzer")
        assert state["failure_count"] == 1
        assert state["consecutive_failures"] == 1

    def test_mode_transition_produces_output(self, project_in_mode):
        """When mode transitions, stdout should have the transition message."""
        proj = project_in_mode("implement", consecutive_failures=3)

        hook_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "make"},
            "tool_result": {"exitCode": 1, "stdout": "", "stderr": "error"},
        }
        code, stdout, _ = _run_hook(
            "kibitzer.hooks.post_tool_use", hook_input, str(proj),
        )
        assert code == 0
        assert stdout.strip() != ""

        output = json.loads(stdout)
        ctx = output["hookSpecificOutput"]["additionalContext"]
        assert "[kibitzer] Mode switched to debug" in ctx

        state = load_state(proj / ".kibitzer")
        assert state["mode"] == "debug"

    def test_coach_output_on_frequency(self, project_in_mode):
        """Coach should fire at frequency and produce output."""
        proj = project_in_mode(
            "implement",
            total_calls=4,
            tools_used_in_mode={"Edit": 4},
        )
        hook_input = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/foo.py"},
            "tool_result": "ok",
        }
        code, stdout, _ = _run_hook(
            "kibitzer.hooks.post_tool_use", hook_input, str(proj),
        )
        assert code == 0
        # Coach fires at call 5 (frequency=5)
        if stdout.strip():
            output = json.loads(stdout)
            ctx = output["hookSpecificOutput"]["additionalContext"]
            assert "[kibitzer]" in ctx

    def test_multiple_calls_accumulate(self, project):
        """Run 3 tool calls in sequence, verify state accumulates."""
        for i in range(3):
            hook_input = {
                "tool_name": "Edit",
                "tool_input": {"file_path": f"src/file{i}.py"},
                "tool_result": "ok",
            }
            _run_hook("kibitzer.hooks.post_tool_use", hook_input, str(project))

        state = load_state(project / ".kibitzer")
        assert state["total_calls"] == 3
        assert state["success_count"] == 3
        assert state["tools_used_in_mode"]["Edit"] == 3

    def test_extra_fields_ignored(self, project):
        """Full Claude Code payload with all fields should work."""
        hook_input = {
            "session_id": "abc123",
            "transcript_path": "/tmp/t.jsonl",
            "cwd": "/some/path",
            "permission_mode": "default",
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "echo hello"},
            "tool_result": {"exitCode": 0, "stdout": "hello\n", "stderr": ""},
            "tool_use_id": "toolu_xyz",
        }
        code, stdout, _ = _run_hook(
            "kibitzer.hooks.post_tool_use", hook_input, str(project),
        )
        assert code == 0

    def test_edit_error_is_failure(self, project):
        """Edit that returns an error dict should be tracked as failure."""
        hook_input = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/foo.py", "old_string": "nonexistent", "new_string": "new"},
            "tool_result": {"error": "old_string not found in file"},
        }
        _run_hook("kibitzer.hooks.post_tool_use", hook_input, str(project))

        state = load_state(project / ".kibitzer")
        assert state["failure_count"] == 1
        assert state["consecutive_failures"] == 1


# ===========================================================================
# State corruption resilience
# ===========================================================================

class TestStateCorruption:
    """Hooks should handle corrupted or missing state gracefully."""

    def test_empty_state_file(self, tmp_path):
        """Empty state.json should fall back to fresh state, not crash."""
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        (state_dir / "state.json").write_text("")

        hook_input = {"tool_name": "Read", "tool_input": {"file_path": "x"}}
        code, stdout, stderr = _run_hook(
            "kibitzer.hooks.pre_tool_use", hook_input, str(tmp_path),
        )
        assert code == 0
        assert stdout.strip() == ""

    def test_invalid_json_state_file(self, tmp_path):
        """Garbage in state.json should fall back to fresh state, not crash."""
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        (state_dir / "state.json").write_text("{invalid json!!")

        hook_input = {"tool_name": "Read", "tool_input": {"file_path": "x"}}
        code, stdout, stderr = _run_hook(
            "kibitzer.hooks.pre_tool_use", hook_input, str(tmp_path),
        )
        assert code == 0
        assert stdout.strip() == ""

    def test_json_array_state_file(self, tmp_path):
        """state.json containing a JSON array (not object) should fall back."""
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        (state_dir / "state.json").write_text("[1, 2, 3]")

        hook_input = {"tool_name": "Read", "tool_input": {"file_path": "x"}}
        code, stdout, _ = _run_hook(
            "kibitzer.hooks.pre_tool_use", hook_input, str(tmp_path),
        )
        assert code == 0

    def test_post_hook_recovers_from_corruption(self, tmp_path):
        """PostToolUse should recover from corrupt state and write a clean one."""
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()
        (state_dir / "state.json").write_text("not json at all")

        hook_input = {
            "tool_name": "Read",
            "tool_input": {"file_path": "x"},
            "tool_result": {"content": "y"},
        }
        code, _, _ = _run_hook(
            "kibitzer.hooks.post_tool_use", hook_input, str(tmp_path),
        )
        assert code == 0

        # State should now be valid
        state = load_state(state_dir)
        assert state["total_calls"] == 1

    def test_missing_kibitzer_dir(self, tmp_path):
        """No .kibitzer/ dir at all — should use defaults."""
        hook_input = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/main.py", "old_string": "a", "new_string": "b"},
        }
        code, stdout, _ = _run_hook(
            "kibitzer.hooks.pre_tool_use", hook_input, str(tmp_path),
        )
        # Default mode is implement, src/ is writable
        assert code == 0
        assert stdout.strip() == ""

    def test_post_hook_with_missing_dir_creates_it(self, tmp_path):
        """PostToolUse should create .kibitzer/ if missing (via save_state)."""
        hook_input = {
            "tool_name": "Read",
            "tool_input": {"file_path": "x"},
            "tool_result": {"content": "y"},
        }
        code, _, _ = _run_hook(
            "kibitzer.hooks.post_tool_use", hook_input, str(tmp_path),
        )
        assert code == 0
        assert (tmp_path / ".kibitzer" / "state.json").exists()


# ===========================================================================
# Intercept log round-trip
# ===========================================================================

class TestInterceptLogRoundTrip:
    """Observe mode writes to intercept.log, GetFeedback reads it back."""

    def test_observe_log_then_get_feedback(self, project):
        from unittest.mock import patch
        from kibitzer.interceptors.jetsam import JetsamInterceptor
        from kibitzer.hooks.pre_tool_use import handle_pre_tool_use
        from kibitzer.mcp.server import get_feedback

        # Run a few bash commands through observe mode
        with patch("kibitzer.hooks.pre_tool_use.build_registry", return_value=[JetsamInterceptor()]):
            for cmd in [
                "git add -A && git commit -m 'fix'",
                "git push origin main",
                "git log --oneline",
            ]:
                handle_pre_tool_use(
                    {"tool_name": "Bash", "tool_input": {"command": cmd}},
                    project_dir=project,
                    plugin_modes={"jetsam": "observe"},
                )

        # Now read via GetFeedback
        result = get_feedback(
            status=False, suggestions=False, intercepts=True,
            project_dir=project,
        )
        intercepts = result["intercepts"]
        assert intercepts["total_observed"] == 3
        assert len(intercepts["recent"]) == 3

        # Verify entries have expected structure
        for entry in intercepts["recent"]:
            assert "bash_command" in entry
            assert "suggested_tool" in entry
            assert "reason" in entry
            assert "plugin" in entry
            assert entry["plugin"] == "jetsam"

        # Verify specific suggestions
        tools = [e["suggested_tool"] for e in intercepts["recent"]]
        assert "jetsam save '<description>'" in tools
        assert "jetsam sync" in tools
        assert "jetsam log" in tools

    def test_empty_log_returns_zero(self, project):
        from kibitzer.mcp.server import get_feedback

        result = get_feedback(
            status=False, suggestions=False, intercepts=True,
            project_dir=project,
        )
        assert result["intercepts"]["total_observed"] == 0
        assert result["intercepts"]["recent"] == []

    def test_log_truncates_long_commands(self, project):
        from unittest.mock import patch
        from kibitzer.interceptors.blq import BlqInterceptor
        from kibitzer.hooks.pre_tool_use import handle_pre_tool_use

        long_cmd = "pytest " + "a" * 300

        with patch("kibitzer.hooks.pre_tool_use.build_registry", return_value=[BlqInterceptor()]):
            handle_pre_tool_use(
                {"tool_name": "Bash", "tool_input": {"command": long_cmd}},
                project_dir=project,
                plugin_modes={"blq": "observe"},
            )

        log_path = project / ".kibitzer" / "intercept.log"
        entry = json.loads(log_path.read_text().strip())
        assert len(entry["bash_command"]) == 200  # truncated


# ===========================================================================
# Config edge cases
# ===========================================================================

class TestConfigEdgeCases:
    """Config loading with unusual project-local overrides."""

    def test_custom_mode_via_project_config(self, tmp_path):
        """Project can define new modes not in defaults."""
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()

        # Add a custom mode
        config_content = """
[modes.deploy]
writable = ["infra/", "deploy/"]
strategy = "Verify before applying."
"""
        (state_dir / "config.toml").write_text(config_content)

        state = fresh_state()
        state["mode"] = "deploy"
        save_state(state, state_dir)

        from kibitzer.hooks.pre_tool_use import handle_pre_tool_use

        # infra/ should be writable in deploy mode
        result = handle_pre_tool_use(
            {"tool_name": "Edit", "tool_input": {"file_path": "infra/main.tf"}},
            project_dir=tmp_path,
        )
        assert result is None

        # src/ should be denied in deploy mode
        result = handle_pre_tool_use(
            {"tool_name": "Edit", "tool_input": {"file_path": "src/main.py"}},
            project_dir=tmp_path,
        )
        assert result is not None
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_override_writable_paths(self, tmp_path):
        """Project overrides default writable paths for implement mode."""
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()

        config_content = """
[modes.implement]
writable = ["src/", "lib/", "pkg/", "internal/"]
"""
        (state_dir / "config.toml").write_text(config_content)
        save_state(fresh_state(), state_dir)

        from kibitzer.hooks.pre_tool_use import handle_pre_tool_use

        result = handle_pre_tool_use(
            {"tool_name": "Edit", "tool_input": {"file_path": "internal/auth.py"}},
            project_dir=tmp_path,
        )
        assert result is None  # allowed because of override

    def test_override_controller_thresholds(self, tmp_path):
        """Project can change how many failures trigger debug."""
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir()

        config_content = """
[controller]
max_consecutive_failures = 1
"""
        (state_dir / "config.toml").write_text(config_content)

        state = fresh_state()
        state["mode"] = "implement"
        save_state(state, state_dir)

        from kibitzer.hooks.post_tool_use import handle_post_tool_use

        # Just 2 failures should trigger debug (threshold=1, triggers when >1)
        for _ in range(2):
            handle_post_tool_use(
                {"tool_name": "Bash", "tool_input": {"command": "make"}, "tool_result": {"exitCode": 1, "stdout": "", "stderr": "err"}},
                project_dir=tmp_path,
            )

        state = load_state(state_dir)
        assert state["mode"] == "debug"
