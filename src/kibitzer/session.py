from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass
class CallResult:
    """Result of a before_call, after_call, or validate_calls check."""
    denied: bool = False
    reason: str = ""
    context: str = ""
    tool: str = ""

    def to_hook_output(self, hook_event: str = "PreToolUse") -> dict:
        """Convert to Claude Code hook JSON protocol."""
        if self.denied:
            return {
                "hookSpecificOutput": {
                    "hookEventName": hook_event,
                    "permissionDecision": "deny",
                    "permissionDecisionReason": self.reason,
                }
            }
        if self.context:
            return {
                "hookSpecificOutput": {
                    "hookEventName": hook_event,
                    "additionalContext": self.context,
                }
            }
        return {}
