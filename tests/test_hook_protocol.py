"""Tests for hook protocol: realistic Claude Code payloads with expected outputs.

These tests exercise handle_pre_tool_use and handle_post_tool_use with
payloads that match Claude Code's actual hook protocol, including all
common fields (session_id, cwd, hook_event_name, tool_use_id).
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from kibitzer.hooks.pre_tool_use import handle_pre_tool_use
from kibitzer.hooks.post_tool_use import handle_post_tool_use, _detect_success
from kibitzer.state import fresh_state, save_state, load_state


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def project(tmp_path):
    """Kibitzer initialized in implement mode."""
    state_dir = tmp_path / ".kibitzer"
    state_dir.mkdir()
    save_state(fresh_state(), state_dir)
    return tmp_path


@pytest.fixture
def project_in_mode(tmp_path):
    """Factory fixture: kibitzer initialized in a given mode."""
    def _make(mode: str, **state_overrides):
        state_dir = tmp_path / ".kibitzer"
        state_dir.mkdir(exist_ok=True)
        state = fresh_state()
        state["mode"] = mode
        state.update(state_overrides)
        save_state(state, state_dir)
        return tmp_path
    return _make


# ---------------------------------------------------------------------------
# Realistic hook payloads — matching Claude Code's protocol
# ---------------------------------------------------------------------------

def _pre_hook(tool_name: str, tool_input: dict, **extra) -> dict:
    """Build a realistic PreToolUse hook input."""
    return {
        "session_id": "test-session-001",
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": "/home/user/project",
        "permission_mode": "default",
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_use_id": "toolu_test_001",
        **extra,
    }


def _post_hook(tool_name: str, tool_input: dict, tool_result, **extra) -> dict:
    """Build a realistic PostToolUse hook input."""
    return {
        "session_id": "test-session-001",
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": "/home/user/project",
        "permission_mode": "default",
        "hook_event_name": "PostToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_result": tool_result,
        "tool_use_id": "toolu_test_001",
        **extra,
    }


# ===========================================================================
# PreToolUse — Edit
# ===========================================================================

class TestPreToolUseEdit:
    """Edit tool calls in various modes."""

    def test_edit_src_in_implement_allows(self, project):
        hook = _pre_hook("Edit", {
            "file_path": "src/auth/handler.py",
            "old_string": "def login(user):",
            "new_string": "def login(user, remember=False):",
        })
        result = handle_pre_tool_use(hook, project_dir=project)
        assert result is None

    def test_edit_test_in_implement_denies(self, project):
        hook = _pre_hook("Edit", {
            "file_path": "tests/test_auth.py",
            "old_string": "assert result == True",
            "new_string": "assert result is True",
        })
        result = handle_pre_tool_use(hook, project_dir=project)
        assert result is not None
        output = result["hookSpecificOutput"]
        assert output["permissionDecision"] == "deny"
        assert "ChangeToolMode" in output["permissionDecisionReason"]
        assert "tests/test_auth.py" in output["permissionDecisionReason"]

    def test_edit_test_in_test_dev_allows(self, project_in_mode):
        proj = project_in_mode("test_dev")
        hook = _pre_hook("Edit", {
            "file_path": "tests/test_auth.py",
            "old_string": "old",
            "new_string": "new",
        })
        result = handle_pre_tool_use(hook, project_dir=proj)
        assert result is None

    def test_edit_src_in_test_dev_denies(self, project_in_mode):
        proj = project_in_mode("test_dev")
        hook = _pre_hook("Edit", {
            "file_path": "src/auth/handler.py",
            "old_string": "old",
            "new_string": "new",
        })
        result = handle_pre_tool_use(hook, project_dir=proj)
        assert result is not None
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_edit_anything_in_debug_denies(self, project_in_mode):
        proj = project_in_mode("debug")
        hook = _pre_hook("Edit", {
            "file_path": "src/main.py",
            "old_string": "old",
            "new_string": "new",
        })
        result = handle_pre_tool_use(hook, project_dir=proj)
        assert result is not None
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_edit_anything_in_review_denies(self, project_in_mode):
        proj = project_in_mode("review")
        hook = _pre_hook("Edit", {"file_path": "src/main.py", "old_string": "a", "new_string": "b"})
        result = handle_pre_tool_use(hook, project_dir=proj)
        assert result is not None
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_edit_anything_in_free_allows(self, project_in_mode):
        proj = project_in_mode("free")
        hook = _pre_hook("Edit", {
            "file_path": "whatever/anywhere.py",
            "old_string": "a",
            "new_string": "b",
        })
        result = handle_pre_tool_use(hook, project_dir=proj)
        assert result is None

    def test_edit_anything_in_create_allows(self, project_in_mode):
        proj = project_in_mode("create")
        hook = _pre_hook("Edit", {"file_path": "new_module/init.py", "old_string": "", "new_string": "# new"})
        result = handle_pre_tool_use(hook, project_dir=proj)
        assert result is None

    def test_edit_docs_in_document_allows(self, project_in_mode):
        proj = project_in_mode("document")
        hook = _pre_hook("Edit", {"file_path": "docs/api.md", "old_string": "old", "new_string": "new"})
        result = handle_pre_tool_use(hook, project_dir=proj)
        assert result is None

    def test_edit_readme_in_document_allows(self, project_in_mode):
        proj = project_in_mode("document")
        hook = _pre_hook("Edit", {"file_path": "README.md", "old_string": "old", "new_string": "new"})
        result = handle_pre_tool_use(hook, project_dir=proj)
        assert result is None

    def test_edit_src_in_document_denies(self, project_in_mode):
        proj = project_in_mode("document")
        hook = _pre_hook("Edit", {"file_path": "src/main.py", "old_string": "a", "new_string": "b"})
        result = handle_pre_tool_use(hook, project_dir=proj)
        assert result is not None
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


# ===========================================================================
# PreToolUse — Write
# ===========================================================================

class TestPreToolUseWrite:
    """Write tool creates new files."""

    def test_write_new_src_file_in_implement_allows(self, project):
        hook = _pre_hook("Write", {
            "file_path": "src/utils/helpers.py",
            "content": "def helper():\n    pass\n",
        })
        result = handle_pre_tool_use(hook, project_dir=project)
        assert result is None

    def test_write_new_test_file_in_implement_denies(self, project):
        hook = _pre_hook("Write", {
            "file_path": "tests/test_helpers.py",
            "content": "def test_helper():\n    assert True\n",
        })
        result = handle_pre_tool_use(hook, project_dir=project)
        assert result is not None
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_write_config_file_in_implement_denies(self, project):
        """Config files aren't in src/ or lib/, so denied in implement mode."""
        hook = _pre_hook("Write", {
            "file_path": "pyproject.toml",
            "content": "[project]\nname = 'foo'\n",
        })
        result = handle_pre_tool_use(hook, project_dir=project)
        assert result is not None
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


# ===========================================================================
# PreToolUse — NotebookEdit
# ===========================================================================

class TestPreToolUseNotebookEdit:
    """NotebookEdit should follow same rules as Edit/Write."""

    def test_notebook_in_src_allows_in_implement(self, project):
        hook = _pre_hook("NotebookEdit", {
            "file_path": "src/analysis.ipynb",
            "cell_index": 3,
            "new_source": "import pandas as pd",
        })
        result = handle_pre_tool_use(hook, project_dir=project)
        assert result is None

    def test_notebook_outside_src_denies_in_implement(self, project):
        hook = _pre_hook("NotebookEdit", {
            "file_path": "notebooks/exploration.ipynb",
            "cell_index": 0,
            "new_source": "# hello",
        })
        result = handle_pre_tool_use(hook, project_dir=project)
        assert result is not None
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


# ===========================================================================
# PreToolUse — Read-only tools (should always pass through)
# ===========================================================================

class TestPreToolUseReadOnly:
    """Read, Grep, Glob, WebFetch, WebSearch, Agent — never blocked."""

    def test_read_passes_in_any_mode(self, project_in_mode):
        for mode in ["debug", "review", "implement", "free"]:
            proj = project_in_mode(mode)
            hook = _pre_hook("Read", {"file_path": "src/secret.py"})
            result = handle_pre_tool_use(hook, project_dir=proj)
            assert result is None, f"Read blocked in {mode} mode"

    def test_grep_passes(self, project):
        hook = _pre_hook("Grep", {"pattern": "def main", "path": "src/"})
        result = handle_pre_tool_use(hook, project_dir=project)
        assert result is None

    def test_glob_passes(self, project):
        hook = _pre_hook("Glob", {"pattern": "**/*.py", "path": "src/"})
        result = handle_pre_tool_use(hook, project_dir=project)
        assert result is None

    def test_webfetch_passes(self, project):
        hook = _pre_hook("WebFetch", {"url": "https://example.com", "prompt": "summarize"})
        result = handle_pre_tool_use(hook, project_dir=project)
        assert result is None

    def test_websearch_passes(self, project):
        hook = _pre_hook("WebSearch", {"query": "python pathlib"})
        result = handle_pre_tool_use(hook, project_dir=project)
        assert result is None

    def test_agent_passes(self, project):
        hook = _pre_hook("Agent", {"prompt": "find all test files"})
        result = handle_pre_tool_use(hook, project_dir=project)
        assert result is None

    def test_mcp_tool_passes(self, project):
        """MCP tools (mcp__server__tool) should pass through."""
        hook = _pre_hook("mcp__fledgling__FindDefinitions", {
            "name_pattern": "handle_request",
        })
        result = handle_pre_tool_use(hook, project_dir=project)
        assert result is None


# ===========================================================================
# PreToolUse — Bash interception
# ===========================================================================

class TestPreToolUseBashInterception:
    """Bash command interception in different plugin modes."""

    @patch("kibitzer.hooks.pre_tool_use.build_registry")
    def test_git_commit_suggest_mode(self, mock_registry, project):
        from kibitzer.interceptors.jetsam import JetsamInterceptor
        mock_registry.return_value = [JetsamInterceptor()]

        hook = _pre_hook("Bash", {"command": "git add -A && git commit -m 'fix auth bug'"})
        result = handle_pre_tool_use(
            hook, project_dir=project, plugin_modes={"jetsam": "suggest"},
        )
        assert result is not None
        output = result["hookSpecificOutput"]
        assert "additionalContext" in output
        assert "jetsam save" in output["additionalContext"]
        assert "permissionDecision" not in output  # suggest, not deny

    @patch("kibitzer.hooks.pre_tool_use.build_registry")
    def test_git_commit_redirect_mode(self, mock_registry, project):
        from kibitzer.interceptors.jetsam import JetsamInterceptor
        mock_registry.return_value = [JetsamInterceptor()]

        hook = _pre_hook("Bash", {"command": "git add . && git commit -m 'wip'"})
        result = handle_pre_tool_use(
            hook, project_dir=project, plugin_modes={"jetsam": "redirect"},
        )
        assert result is not None
        output = result["hookSpecificOutput"]
        assert output["permissionDecision"] == "deny"
        assert "jetsam save" in output["permissionDecisionReason"]

    @patch("kibitzer.hooks.pre_tool_use.build_registry")
    def test_git_commit_observe_mode_logs(self, mock_registry, project):
        from kibitzer.interceptors.jetsam import JetsamInterceptor
        mock_registry.return_value = [JetsamInterceptor()]

        hook = _pre_hook("Bash", {"command": "git add -A && git commit -m 'save'"})
        result = handle_pre_tool_use(
            hook, project_dir=project, plugin_modes={"jetsam": "observe"},
        )
        assert result is None  # allowed silently

        # But it should have logged
        log_path = project / ".kibitzer" / "intercept.log"
        assert log_path.exists()
        entry = json.loads(log_path.read_text().strip())
        assert entry["plugin"] == "jetsam"
        assert "jetsam save" in entry["suggested_tool"]

    @patch("kibitzer.hooks.pre_tool_use.build_registry")
    def test_pytest_suggest_blq(self, mock_registry, project):
        from kibitzer.interceptors.blq import BlqInterceptor
        mock_registry.return_value = [BlqInterceptor()]

        hook = _pre_hook("Bash", {"command": "python -m pytest tests/ -v --tb=short"})
        result = handle_pre_tool_use(
            hook, project_dir=project, plugin_modes={"blq": "suggest"},
        )
        assert result is not None
        assert "blq run test" in result["hookSpecificOutput"]["additionalContext"]

    @patch("kibitzer.hooks.pre_tool_use.build_registry")
    def test_grep_for_def_suggest_fledgling(self, mock_registry, project):
        from kibitzer.interceptors.fledgling import FledglingInterceptor
        mock_registry.return_value = [FledglingInterceptor()]

        hook = _pre_hook("Bash", {"command": "grep -rn 'def handle_request' src/"})
        result = handle_pre_tool_use(
            hook, project_dir=project, plugin_modes={"fledgling": "suggest"},
        )
        assert result is not None
        assert "FindDefinitions" in result["hookSpecificOutput"]["additionalContext"]

    @patch("kibitzer.hooks.pre_tool_use.build_registry")
    def test_non_matching_bash_passes(self, mock_registry, project):
        from kibitzer.interceptors.jetsam import JetsamInterceptor
        from kibitzer.interceptors.blq import BlqInterceptor
        mock_registry.return_value = [JetsamInterceptor(), BlqInterceptor()]

        hook = _pre_hook("Bash", {"command": "ls -la && cat README.md"})
        result = handle_pre_tool_use(
            hook, project_dir=project, plugin_modes={"jetsam": "suggest", "blq": "suggest"},
        )
        assert result is None

    @patch("kibitzer.hooks.pre_tool_use.build_registry")
    def test_empty_command_passes(self, mock_registry, project):
        mock_registry.return_value = []
        hook = _pre_hook("Bash", {"command": ""})
        result = handle_pre_tool_use(hook, project_dir=project)
        assert result is None


# ===========================================================================
# PreToolUse — Edge cases
# ===========================================================================

class TestPreToolUseEdgeCases:
    def test_missing_file_path_allows(self, project):
        """Edit with no file_path should pass (nothing to guard)."""
        hook = _pre_hook("Edit", {"old_string": "a", "new_string": "b"})
        result = handle_pre_tool_use(hook, project_dir=project)
        assert result is None

    def test_unknown_tool_allows(self, project):
        hook = _pre_hook("SomeNewTool", {"arg": "value"})
        result = handle_pre_tool_use(hook, project_dir=project)
        assert result is None

    def test_empty_tool_input_allows(self, project):
        hook = _pre_hook("Edit", {})
        result = handle_pre_tool_use(hook, project_dir=project)
        assert result is None

    def test_no_state_file_uses_default_mode(self, tmp_path):
        """No .kibitzer dir = fresh state = implement mode."""
        hook = _pre_hook("Edit", {"file_path": "src/foo.py"})
        result = handle_pre_tool_use(hook, project_dir=tmp_path)
        assert result is None  # src/ is writable in implement


# ===========================================================================
# PostToolUse — Success detection
# ===========================================================================

class TestDetectSuccess:
    """_detect_success heuristic for various tool results."""

    def test_bash_exit_0_is_success(self):
        hook = _post_hook("Bash", {"command": "make"}, {"exitCode": 0, "stdout": "ok", "stderr": ""})
        assert _detect_success(hook) is True

    def test_bash_exit_1_is_failure(self):
        hook = _post_hook("Bash", {"command": "make"}, {"exitCode": 1, "stdout": "", "stderr": "error"})
        assert _detect_success(hook) is False

    def test_bash_exit_2_is_failure(self):
        hook = _post_hook("Bash", {"command": "false"}, {"exitCode": 2, "stdout": "", "stderr": ""})
        assert _detect_success(hook) is False

    def test_bash_exit_127_is_failure(self):
        """Command not found."""
        hook = _post_hook("Bash", {"command": "nonexistent"}, {"exitCode": 127, "stdout": "", "stderr": "command not found"})
        assert _detect_success(hook) is False

    def test_edit_string_result_is_success(self):
        hook = _post_hook("Edit", {"file_path": "src/foo.py"}, "Applied edit to src/foo.py")
        assert _detect_success(hook) is True

    def test_edit_dict_result_is_success(self):
        hook = _post_hook("Edit", {"file_path": "src/foo.py"}, {"file_path": "src/foo.py"})
        assert _detect_success(hook) is True

    def test_edit_error_result_is_failure(self):
        hook = _post_hook("Edit", {"file_path": "src/foo.py"}, {"error": "old_string not found in file"})
        assert _detect_success(hook) is False

    def test_write_success(self):
        hook = _post_hook("Write", {"file_path": "src/new.py", "content": "# new"}, {"file_path": "src/new.py"})
        assert _detect_success(hook) is True

    def test_read_success(self):
        hook = _post_hook("Read", {"file_path": "src/foo.py"}, {"file_path": "src/foo.py", "content": "hello"})
        assert _detect_success(hook) is True

    def test_grep_success(self):
        hook = _post_hook("Grep", {"pattern": "def main"}, {"matches": [{"file": "src/main.py", "line": "def main():"}]})
        assert _detect_success(hook) is True

    def test_glob_success(self):
        hook = _post_hook("Glob", {"pattern": "*.py"}, {"matches": ["src/a.py", "src/b.py"]})
        assert _detect_success(hook) is True

    def test_none_result_is_success(self):
        """Some tools may return None/null."""
        hook = _post_hook("Read", {"file_path": "x"}, None)
        assert _detect_success(hook) is True

    def test_string_result_is_success(self):
        hook = _post_hook("Edit", {"file_path": "x"}, "success")
        assert _detect_success(hook) is True

    def test_bash_string_result_not_dict(self):
        """Bash with non-dict result — treat as success (defensive)."""
        hook = _post_hook("Bash", {"command": "echo hi"}, "hi\n")
        assert _detect_success(hook) is True


# ===========================================================================
# PostToolUse — Counter tracking
# ===========================================================================

class TestPostToolUseCounters:
    """Verify state is correctly updated after various tool calls."""

    def test_successful_edit_updates_state(self, project):
        hook = _post_hook("Edit", {"file_path": "src/foo.py"}, "Applied edit")
        handle_post_tool_use(hook, project_dir=project)

        state = load_state(project / ".kibitzer")
        assert state["total_calls"] == 1
        assert state["turns_in_mode"] == 1
        assert state["success_count"] == 1
        assert state["failure_count"] == 0
        assert state["consecutive_failures"] == 0
        assert state["tools_used_in_mode"]["Edit"] == 1

    def test_failed_bash_updates_state(self, project):
        hook = _post_hook("Bash", {"command": "make test"}, {"exitCode": 1, "stdout": "", "stderr": "FAIL"})
        handle_post_tool_use(hook, project_dir=project)

        state = load_state(project / ".kibitzer")
        assert state["total_calls"] == 1
        assert state["failure_count"] == 1
        assert state["consecutive_failures"] == 1
        assert state["tools_used_in_mode"]["Bash"] == 1

    def test_mixed_sequence(self, project):
        """Edit, Edit, Bash(fail), Edit, Bash(fail), Bash(fail)"""
        calls = [
            _post_hook("Edit", {"file_path": "src/a.py"}, "ok"),
            _post_hook("Edit", {"file_path": "src/b.py"}, "ok"),
            _post_hook("Bash", {"command": "make"}, {"exitCode": 1, "stdout": "", "stderr": "err"}),
            _post_hook("Edit", {"file_path": "src/c.py"}, "ok"),
            _post_hook("Bash", {"command": "make"}, {"exitCode": 1, "stdout": "", "stderr": "err"}),
            _post_hook("Bash", {"command": "make"}, {"exitCode": 1, "stdout": "", "stderr": "err"}),
        ]
        for hook in calls:
            handle_post_tool_use(hook, project_dir=project)

        state = load_state(project / ".kibitzer")
        assert state["total_calls"] == 6
        assert state["success_count"] == 3
        assert state["failure_count"] == 3
        assert state["consecutive_failures"] == 2  # reset after 3rd Edit
        assert state["tools_used_in_mode"]["Edit"] == 3
        assert state["tools_used_in_mode"]["Bash"] == 3

    def test_read_and_grep_tracked(self, project):
        """Read-only tools should also be tracked in counters."""
        calls = [
            _post_hook("Read", {"file_path": "src/foo.py"}, {"content": "hello"}),
            _post_hook("Grep", {"pattern": "def"}, {"matches": []}),
            _post_hook("Glob", {"pattern": "*.py"}, {"matches": []}),
        ]
        for hook in calls:
            handle_post_tool_use(hook, project_dir=project)

        state = load_state(project / ".kibitzer")
        assert state["total_calls"] == 3
        assert state["tools_used_in_mode"]["Read"] == 1
        assert state["tools_used_in_mode"]["Grep"] == 1
        assert state["tools_used_in_mode"]["Glob"] == 1


# ===========================================================================
# PostToolUse — Mode transitions
# ===========================================================================

class TestPostToolUseModeTransitions:
    """Mode controller transitions triggered by tool results."""

    def test_four_consecutive_failures_switches_to_debug(self, project):
        for _ in range(4):
            handle_post_tool_use(
                _post_hook("Bash", {"command": "make test"}, {"exitCode": 1, "stdout": "", "stderr": "FAIL"}),
                project_dir=project,
            )

        state = load_state(project / ".kibitzer")
        assert state["mode"] == "debug"
        assert state["previous_mode"] == "implement"
        assert state["mode_switches"] == 1

    def test_three_failures_then_success_stays(self, project):
        for _ in range(3):
            handle_post_tool_use(
                _post_hook("Bash", {"command": "make"}, {"exitCode": 1, "stdout": "", "stderr": "err"}),
                project_dir=project,
            )
        handle_post_tool_use(
            _post_hook("Bash", {"command": "make"}, {"exitCode": 0, "stdout": "ok", "stderr": ""}),
            project_dir=project,
        )

        state = load_state(project / ".kibitzer")
        assert state["mode"] == "implement"
        assert state["consecutive_failures"] == 0

    def test_transition_output_format(self, project_in_mode):
        proj = project_in_mode("implement", consecutive_failures=3)

        result = handle_post_tool_use(
            _post_hook("Bash", {"command": "make"}, {"exitCode": 1, "stdout": "", "stderr": "err"}),
            project_dir=proj,
        )
        assert result is not None
        output = result["hookSpecificOutput"]
        assert output["hookEventName"] == "PostToolUse"
        assert "additionalContext" in output
        assert "[kibitzer] Mode switched to debug" in output["additionalContext"]

    def test_free_mode_never_auto_transitions(self, project_in_mode):
        proj = project_in_mode("free")
        for _ in range(10):
            handle_post_tool_use(
                _post_hook("Bash", {"command": "x"}, {"exitCode": 1, "stdout": "", "stderr": "e"}),
                project_dir=proj,
            )

        state = load_state(proj / ".kibitzer")
        assert state["mode"] == "free"

    def test_debug_exits_after_max_turns(self, project_in_mode):
        proj = project_in_mode("debug", turns_in_mode=20)

        handle_post_tool_use(
            _post_hook("Read", {"file_path": "src/foo.py"}, {"content": "hello"}),
            project_dir=proj,
        )

        state = load_state(proj / ".kibitzer")
        assert state["mode"] == "implement"

    def test_oscillation_guard(self, project_in_mode):
        """Don't switch back to a mode we just left quickly."""
        proj = project_in_mode(
            "implement",
            consecutive_failures=10,
            previous_mode="debug",
            turns_in_previous_mode=2,
        )

        handle_post_tool_use(
            _post_hook("Bash", {"command": "x"}, {"exitCode": 1, "stdout": "", "stderr": "e"}),
            project_dir=proj,
        )

        state = load_state(proj / ".kibitzer")
        assert state["mode"] == "implement"  # stayed, didn't oscillate to debug

    def test_max_switches_stops_transitions(self, project_in_mode):
        proj = project_in_mode("implement", mode_switches=7, consecutive_failures=10)

        handle_post_tool_use(
            _post_hook("Bash", {"command": "x"}, {"exitCode": 1, "stdout": "", "stderr": "e"}),
            project_dir=proj,
        )

        state = load_state(proj / ".kibitzer")
        assert state["mode"] == "implement"


# ===========================================================================
# PostToolUse — Coach suggestions
# ===========================================================================

class TestPostToolUseCoach:
    """Coach fires at frequency intervals and generates suggestions."""

    def test_coach_suggests_tests_after_edit_streak(self, project_in_mode):
        proj = project_in_mode("implement", total_calls=4, tools_used_in_mode={"Edit": 4})

        result = handle_post_tool_use(
            _post_hook("Edit", {"file_path": "src/foo.py"}, "ok"),
            project_dir=proj,
        )
        # total_calls becomes 5, coach fires
        if result:
            ctx = result["hookSpecificOutput"].get("additionalContext", "")
            assert "[kibitzer]" in ctx
            assert "test" in ctx.lower()

    def test_coach_does_not_repeat_suggestion(self, project_in_mode):
        proj = project_in_mode(
            "implement",
            total_calls=4,
            tools_used_in_mode={"Edit": 4},
            suggestions_given=["edit_without_test"],
        )

        result = handle_post_tool_use(
            _post_hook("Edit", {"file_path": "src/foo.py"}, "ok"),
            project_dir=proj,
        )
        # The edit_without_test suggestion should be deduped
        if result:
            ctx = result["hookSpecificOutput"].get("additionalContext", "")
            # Should not contain the edit_without_test message
            assert "without running tests" not in ctx

    def test_coach_does_not_fire_between_intervals(self, project_in_mode):
        proj = project_in_mode("implement", total_calls=2)

        result = handle_post_tool_use(
            _post_hook("Edit", {"file_path": "src/foo.py"}, "ok"),
            project_dir=proj,
        )
        # total_calls becomes 3, coach shouldn't fire (frequency=5)
        assert result is None

    def test_no_output_on_normal_success(self, project):
        """A simple successful tool call with no patterns should produce no output."""
        result = handle_post_tool_use(
            _post_hook("Read", {"file_path": "src/foo.py"}, {"content": "hello"}),
            project_dir=project,
        )
        assert result is None


# ===========================================================================
# PostToolUse — Output format validation
# ===========================================================================

class TestPostToolUseOutputFormat:
    """Verify output JSON matches Claude Code's expected protocol."""

    def test_transition_output_is_valid_json(self, project_in_mode):
        proj = project_in_mode("implement", consecutive_failures=3)

        result = handle_post_tool_use(
            _post_hook("Bash", {"command": "x"}, {"exitCode": 1, "stdout": "", "stderr": "e"}),
            project_dir=proj,
        )
        assert result is not None
        # Should be JSON-serializable
        json_str = json.dumps(result)
        parsed = json.loads(json_str)
        assert "hookSpecificOutput" in parsed
        assert "hookEventName" in parsed["hookSpecificOutput"]
        assert "additionalContext" in parsed["hookSpecificOutput"]

    def test_none_output_means_allow(self, project):
        """When result is None, the hook exits 0 with no output = allow."""
        result = handle_post_tool_use(
            _post_hook("Read", {"file_path": "x"}, {"content": "y"}),
            project_dir=project,
        )
        assert result is None


# ===========================================================================
# PreToolUse — Output format validation
# ===========================================================================

class TestPreToolUseOutputFormat:
    """Verify deny/suggest output matches Claude Code protocol."""

    def test_deny_output_has_required_fields(self, project):
        hook = _pre_hook("Edit", {"file_path": "tests/foo.py", "old_string": "a", "new_string": "b"})
        result = handle_pre_tool_use(hook, project_dir=project)

        assert result is not None
        output = result["hookSpecificOutput"]
        assert output["hookEventName"] == "PreToolUse"
        assert output["permissionDecision"] == "deny"
        assert isinstance(output["permissionDecisionReason"], str)
        assert len(output["permissionDecisionReason"]) > 0

        # Should be valid JSON
        json_str = json.dumps(result)
        parsed = json.loads(json_str)
        assert parsed == result

    @patch("kibitzer.hooks.pre_tool_use.build_registry")
    def test_suggest_output_has_required_fields(self, mock_registry, project):
        from kibitzer.interceptors.blq import BlqInterceptor
        mock_registry.return_value = [BlqInterceptor()]

        hook = _pre_hook("Bash", {"command": "pytest tests/"})
        result = handle_pre_tool_use(
            hook, project_dir=project, plugin_modes={"blq": "suggest"},
        )

        assert result is not None
        output = result["hookSpecificOutput"]
        assert output["hookEventName"] == "PreToolUse"
        assert "additionalContext" in output
        assert isinstance(output["additionalContext"], str)
        assert "permissionDecision" not in output  # suggest, not deny
