"""Tests for KibitzerSession and CallResult."""

from kibitzer.session import CallResult


class TestCallResult:
    def test_allow_result(self):
        result = CallResult()
        assert not result.denied
        assert result.reason == ""
        assert result.context == ""

    def test_deny_result(self):
        result = CallResult(denied=True, reason="not writable", tool="Edit")
        assert result.denied
        assert "not writable" in result.reason

    def test_context_result(self):
        result = CallResult(context="[kibitzer] suggestion", tool="Bash")
        assert not result.denied
        assert "[kibitzer]" in result.context

    def test_to_hook_output_deny(self):
        result = CallResult(denied=True, reason="blocked")
        output = result.to_hook_output("PreToolUse")
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert output["hookSpecificOutput"]["permissionDecisionReason"] == "blocked"
        assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"

    def test_to_hook_output_context(self):
        result = CallResult(context="[kibitzer] try jetsam")
        output = result.to_hook_output("PreToolUse")
        assert "additionalContext" in output["hookSpecificOutput"]
        assert "permissionDecision" not in output["hookSpecificOutput"]

    def test_to_hook_output_post_tool(self):
        result = CallResult(context="[kibitzer] mode switched")
        output = result.to_hook_output("PostToolUse")
        assert output["hookSpecificOutput"]["hookEventName"] == "PostToolUse"

    def test_to_hook_output_empty(self):
        result = CallResult()
        output = result.to_hook_output("PreToolUse")
        assert output == {}
