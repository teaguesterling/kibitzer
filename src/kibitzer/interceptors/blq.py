"""Intercept build/test commands, suggest blq structured capture."""

from __future__ import annotations
from typing import Optional
from kibitzer.interceptors.base import BaseInterceptor, Suggestion

_TEST_TRIGGERS = [
    "pytest", "python -m pytest", "npm test", "cargo test",
    "go test", "make test", "gradle test",
]


class BlqInterceptor(BaseInterceptor):
    name = "blq"
    triggers = _TEST_TRIGGERS

    def check(self, command: str) -> Optional[Suggestion]:
        for trigger in self.triggers:
            if trigger in command:
                return Suggestion(
                    tool="blq run test",
                    reason="Captures structured output — errors queryable via blq errors, results persisted across sessions",
                    plugin=self.name,
                )
        return None
