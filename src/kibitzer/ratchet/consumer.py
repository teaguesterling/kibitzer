"""Ratchet consumer — reads agent-riggs' promoted ratchets for in-session coaching.

agent-riggs (the producer) observes failures, forms candidate ratchets, and *promotes*
the good ones (`agent-riggs ratchet promote KEY`). This reads the **promoted** rows from
the shared store (`.riggs/store.duckdb`, **read-only**) and matches them to the current
failure fingerprint, so kibitzer can surface the recorded pattern as coaching — closing
observe→learn→observe.

The store **schema is the contract**: we read the `ratchet_decisions` table via SQL and
never write. The match key mirrors agent-riggs' own derivation
(`find_constraint_candidates`): ``f"{category}-{tool or 'unknown'}-{mode or 'any'}"``.
A producer-driven conformance test pins that the two stay identical.

Graceful, like `PolicyConsumer`: a missing store (or no duckdb) yields ``None``; a store
that exists but has no promoted ratchets (incl. the table not created yet — the realistic
OFF state) yields an empty consumer whose ``suggest()`` is always ``None``.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PromotedRatchet:
    """One promoted ratchet from agent-riggs' store."""

    candidate_key: str
    candidate_type: str
    evidence: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None

    @property
    def trust(self) -> float:
        """Ordering score from the evidence payload: constraint ratchets carry
        ``avg_trust``, tool ratchets carry ``success_rate``; absent → 0.0."""
        ev = self.evidence or {}
        val = ev.get("avg_trust", ev.get("success_rate"))
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0

    @property
    def severity(self) -> str | None:
        return (self.evidence or {}).get("severity")

    def coaching_message(self) -> str:
        """A one-line coaching surface built from the persisted fields (the fix
        *recommendation* is recomputed producer-side and not stored, so we surface the
        pattern + evidence + promotion reason)."""
        ev = self.evidence or {}
        bits = [f"known repeat-failure pattern '{self.candidate_key}'"]
        if ev.get("severity"):
            bits.append(f"severity={ev['severity']}")
        if ev.get("occurrences") is not None:
            bits.append(f"{ev['occurrences']}× across {ev.get('sessions_affected', '?')} sessions")
        msg = "kibitzer: " + ", ".join(bits) + "."
        if self.reason:
            msg += f" Promoted: {self.reason}"
        return msg


class RatchetConsumer:
    """Kibitzer's read-only interface to agent-riggs' promoted ratchets."""

    def __init__(self, promoted: list[PromotedRatchet]) -> None:
        self._by_key: dict[str, list[PromotedRatchet]] = {}
        for r in promoted:
            self._by_key.setdefault(r.candidate_key, []).append(r)
        for matches in self._by_key.values():
            matches.sort(key=lambda r: r.trust, reverse=True)

    @classmethod
    def from_db(cls, path: str | Path) -> RatchetConsumer | None:
        """Open the riggs store read-only and load promoted ratchets.

        Returns ``None`` when there is no usable store (missing file, no duckdb,
        open error). Returns an **empty** consumer when the store opens but holds no
        promoted ratchets (including before the `ratchet_decisions` table exists)."""
        path = Path(path)
        if not path.exists():
            return None
        try:
            import duckdb
        except ImportError:
            return None
        try:
            con = duckdb.connect(str(path), read_only=True)
        except Exception:
            return None
        try:
            has_table = con.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'ratchet_decisions'"
            ).fetchone()
            if not has_table:
                return cls([])
            rows = con.execute(
                "SELECT candidate_key, candidate_type, evidence, reason "
                "FROM ratchet_decisions WHERE decision = 'promoted'"
            ).fetchall()
        except Exception:
            return cls([])
        finally:
            con.close()

        promoted: list[PromotedRatchet] = []
        for key, ctype, ev, reason in rows:
            try:
                evidence = json.loads(ev) if ev else {}
            except (TypeError, ValueError):
                evidence = {}
            promoted.append(
                PromotedRatchet(candidate_key=key, candidate_type=ctype,
                                evidence=evidence, reason=reason)
            )
        return cls(promoted)

    @classmethod
    def from_env(cls, var: str = "RIGGS_RATCHET_DB") -> RatchetConsumer | None:
        """Load from the store path named by env var `var` — the runtime/bench wiring
        point. Returns ``None`` when the var is unset or the store is unusable, so a
        kibitzer hook can call this unconditionally and stay inert outside a session
        that has explicitly pointed it at a (frozen) ratchet store."""
        path = os.environ.get(var)
        if not path:
            return None
        return cls.from_db(path)

    @staticmethod
    def constraint_key(category: str, tool: str | None = None, mode: str | None = None) -> str:
        """The constraint ratchet key, mirroring agent-riggs
        `find_constraint_candidates`. Pinned by the conformance test."""
        return f"{category}-{tool or 'unknown'}-{mode or 'any'}"

    def match(self, *, category: str, tool: str | None = None,
              mode: str | None = None) -> list[PromotedRatchet]:
        """Promoted ratchets for a failure fingerprint, best (highest trust) first."""
        return list(self._by_key.get(self.constraint_key(category, tool, mode), []))

    def match_key(self, candidate_key: str) -> list[PromotedRatchet]:
        """Promoted ratchets for an already-computed candidate_key (e.g. a tool ratchet
        key the caller derived), best first."""
        return list(self._by_key.get(candidate_key, []))

    def suggest(self, *, category: str, tool: str | None = None,
                mode: str | None = None) -> PromotedRatchet | None:
        """The single best promoted match for a failure fingerprint, or None."""
        matches = self.match(category=category, tool=tool, mode=mode)
        return matches[0] if matches else None

    def coaching_for_failure(self, *, tool: str | None, mode: str | None,
                             category: str = "failure") -> str | None:
        """The hook-facing call: given a just-observed failure's fingerprint
        (category/tool/mode), return the top promoted ratchet's coaching message, or
        ``None``. This is the surface a kibitzer PostToolUse hook invokes on a tool
        failure once a frozen ratchet store is present. Kept out of the live hook
        hot-path until the precision bar (false-promotion ≤10%) is validated — a wrong
        suggestion is worse than none."""
        suggestion = self.suggest(category=category, tool=tool, mode=mode)
        return suggestion.coaching_message() if suggestion else None

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_key.values())
