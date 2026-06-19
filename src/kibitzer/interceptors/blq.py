"""Intercept build/test commands, suggest blq structured capture."""

from __future__ import annotations
from typing import Optional
from kibitzer.interceptors.base import BaseInterceptor, Suggestion

_TRIGGERS = [
    # test runners
    "pytest", "python -m pytest", "npm test", "cargo test", "go test",
    "make test", "gradle test", "tox",
    # build runners — the dominant un-captured bypass (e.g. `cargo build | grep`)
    "cargo build", "go build", "npm run build", "make build", "gradle build",
]


class BlqInterceptor(BaseInterceptor):
    name = "blq"
    triggers = _TRIGGERS

    def check(self, command: str) -> Optional[Suggestion]:
        # Match the primary command (before the first pipe), so a build whose
        # output is piped to grep still counts but an incidental mention doesn't.
        primary = command.strip().split("|")[0]
        for trigger in self.triggers:
            if trigger in primary:
                return Suggestion(
                    tool="blq run <cmd> / blq exec '<cmd>'",
                    reason="Captures + indexes the output — errors stay queryable (blq events) and persist across sessions; no re-running to see what failed",
                    plugin=self.name,
                )
        return None
