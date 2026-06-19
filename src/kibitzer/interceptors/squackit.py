"""Intercept code-search commands, suggest squackit (the first-line wrapper).

squackit is the recommended first-line for code search (FTS + AST over a cached
per-root index) — see the squackit/fledgling SKILLs ("prefer squackit first").
These nudges fire when a session reaches for a raw *recursive* grep/rg or a
structural find. Advisory only (suggest mode) — never blocks.

Precision: only the *primary* command counts (the stage before the first pipe),
so a pipe-filter like `cat x | grep y` is not treated as a search.
"""

from __future__ import annotations
from typing import Optional
from kibitzer.interceptors.base import BaseInterceptor, Suggestion

_DEFINITION_KEYWORDS = ["def ", "class ", "function ", "fn ", "func ", "interface "]


class SquackitInterceptor(BaseInterceptor):
    name = "squackit"
    triggers = ["grep -r", "rg ", "find . -name", "find . -type"]

    def check(self, command: str) -> Optional[Suggestion]:
        primary = command.strip().split("|")[0].strip()

        is_recursive_grep = ("grep -r" in primary) or primary.startswith("rg ")
        if is_recursive_grep:
            if any(kw in command for kw in _DEFINITION_KEYWORDS):
                return Suggestion(
                    tool="investigate(name) / find_names(source, selector='.fn#NAME')",
                    reason="AST-aware — resolves the definition (scope, type, nesting), not raw text matches",
                    plugin="squackit",
                )
            return Suggestion(
                tool="search(query) / find_code_ranked(fts_query=...)",
                reason="FTS-ranked search over defs + comments + strings with per-root caching — structured, not a raw recursive grep",
                plugin="squackit",
            )

        if "find " in primary and ("-name" in primary or "-type" in primary):
            return Suggestion(
                tool="find(source, selector) / project_overview()",
                reason="Structural search over the AST index instead of filename matching",
                plugin="squackit",
            )

        return None
