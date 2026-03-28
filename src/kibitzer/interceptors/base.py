"""Base classes for interceptor plugins."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class InterceptMode(Enum):
    OBSERVE = "observe"
    SUGGEST = "suggest"
    REDIRECT = "redirect"


@dataclass
class Suggestion:
    tool: str
    reason: str
    plugin: str


class BaseInterceptor:
    name: str = ""
    triggers: list[str] = []

    def check(self, command: str) -> Optional[Suggestion]:
        raise NotImplementedError
