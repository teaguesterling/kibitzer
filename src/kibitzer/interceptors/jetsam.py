"""Intercept git commands, suggest jetsam workflow operations."""

from __future__ import annotations
from typing import Optional
from kibitzer.interceptors.base import BaseInterceptor, Suggestion

_GIT_PATTERNS = [
    {"match": lambda cmd: "git add" in cmd and "git commit" in cmd,
     "tool": "jetsam save '<description>'",
     "reason": "Atomic save with plan tracking, confirmation step, and branch management"},
    {"match": lambda cmd: "git push" in cmd,
     "tool": "jetsam sync",
     "reason": "Syncs with remote, handles rebasing, requires confirmation"},
    {"match": lambda cmd: "git stash" in cmd,
     "tool": "jetsam save '<description>'",
     "reason": "jetsam save creates a checkpoint without losing work"},
    {"match": lambda cmd: "git diff" in cmd,
     "tool": "jetsam diff",
     "reason": "Structured diff with plan context"},
    {"match": lambda cmd: "git log" in cmd,
     "tool": "jetsam log",
     "reason": "Commit history with plan annotations"},
]


class JetsamInterceptor(BaseInterceptor):
    name = "jetsam"
    triggers = ["git add", "git commit", "git push", "git stash", "git diff", "git log"]

    def check(self, command: str) -> Optional[Suggestion]:
        for pattern in _GIT_PATTERNS:
            if pattern["match"](command):
                return Suggestion(tool=pattern["tool"], reason=pattern["reason"], plugin=self.name)
        return None
