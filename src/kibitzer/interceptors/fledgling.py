"""Intercept search/navigation commands, suggest fledgling semantic alternatives."""

from __future__ import annotations
from typing import Optional
from kibitzer.interceptors.base import BaseInterceptor, Suggestion

_DEFINITION_KEYWORDS = ["def ", "class ", "function ", "fn ", "func ", "interface "]


class FledglingInterceptor(BaseInterceptor):
    name = "fledgling"
    triggers = ["grep -r", "grep -rn", "find . -name", "find . -type f"]

    def check(self, command: str) -> Optional[Suggestion]:
        if "grep" in command and any(kw in command for kw in _DEFINITION_KEYWORDS):
            return Suggestion(
                tool="FindDefinitions(name_pattern='...')",
                reason="AST-aware search — finds the definition, not just text matches. Understands scope, type, and nesting",
                plugin=self.name,
            )
        if "find" in command and ("-name" in command or "-type" in command):
            return Suggestion(
                tool="CodeStructure(file_pattern='**/*.py')",
                reason="Shows classes, functions, and nesting — not just file names",
                plugin=self.name,
            )
        return None
