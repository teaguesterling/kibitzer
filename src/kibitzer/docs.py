"""Doc context pipeline types.

Kibitzer retrieves documentation sections via pluckit, then optionally
refines them through consumer-provided callbacks. The pipeline:
    retrieve (pluckit) -> select (callback) -> present (callback)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class DocSection:
    """A single documentation section retrieved from pluckit."""

    title: str
    content: str
    file_path: str
    level: int = 1
    tool: str | None = None


@dataclass
class DocResult:
    """Result of the doc context pipeline."""

    sections: list[DocSection] = field(default_factory=list)


SelectCallback = Callable[[list[DocSection], dict[str, Any]], list[DocSection]]
PresentCallback = Callable[[list[DocSection], dict[str, Any]], list[DocSection]]


@dataclass
class DocRefinement:
    """Consumer-provided callbacks for the select and present steps.

    Both are optional. When omitted, kibitzer uses defaults:
    - select: top-N by retrieval ranking
    - present: raw section content
    """

    select: SelectCallback | None = None
    present: PresentCallback | None = None
