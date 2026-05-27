"""Tests for the ratchet consumer (workstream A).

T1: RatchetConsumer reads PROMOTED ratchets from a riggs-shaped store, graceful on
missing file/table, promoted-only, ordered by evidence trust.

Conformance (the contract tripwire): drives the *real* agent-riggs producer
(find_constraint_candidates → promote) and asserts the consumer's key derivation stays
identical — so a change to agent-riggs' candidate_key format turns this red.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import duckdb
import pytest

from kibitzer.ratchet import RatchetConsumer, PromotedRatchet

# agent-riggs' real schema (the read contract this consumer depends on).
_DDL = """
CREATE TABLE ratchet_decisions (
    decision_id BIGINT PRIMARY KEY, decided_at TIMESTAMP,
    candidate_type VARCHAR, candidate_key VARCHAR, decision VARCHAR,
    reason VARCHAR, evidence JSON, config_change JSON
)
"""


def _store(path, rows):
    """Create a riggs-shaped store at `path` with the given decision rows and close it
    (single-writer: readers attach only after the writer releases the file)."""
    con = duckdb.connect(str(path))
    con.execute(_DDL)
    for i, (ctype, key, decision, reason, evidence) in enumerate(rows):
        con.execute(
            "INSERT INTO ratchet_decisions VALUES (?,?,?,?,?,?,?,?)",
            [i, datetime.now(timezone.utc), ctype, key, decision, reason,
             json.dumps(evidence) if evidence is not None else None, None],
        )
    con.close()


# ── T1: graceful fallback ────────────────────────────────────────────────────
def test_missing_file_returns_none(tmp_path):
    assert RatchetConsumer.from_db(tmp_path / "nope.duckdb") is None


def test_store_without_table_is_empty_consumer(tmp_path):
    db = tmp_path / "store.duckdb"
    duckdb.connect(str(db)).close()  # exists but no ratchet_decisions (the OFF state)
    rc = RatchetConsumer.from_db(db)
    assert rc is not None and len(rc) == 0
    assert rc.suggest(category="failure", tool="Bash", mode="implement") is None


def test_empty_consumer_suggest_is_none():
    assert RatchetConsumer([]).suggest(category="failure") is None


# ── T1: promoted-only + matching ─────────────────────────────────────────────
def test_reads_only_promoted(tmp_path):
    db = tmp_path / "store.duckdb"
    _store(db, [
        ("constraint_promotion", "failure-Bash-implement", "promoted", "good",
         {"avg_trust": 0.4, "severity": "systemic", "occurrences": 6, "sessions_affected": 3}),
        ("constraint_promotion", "failure-Read-review", "rejected", "noisy", {"avg_trust": 0.9}),
        ("constraint_promotion", "timeout-Bash-implement", "candidate", None, {"avg_trust": 0.5}),
    ])
    rc = RatchetConsumer.from_db(db)
    assert len(rc) == 1  # only the promoted row
    assert rc.suggest(category="failure", tool="Bash", mode="implement") is not None
    assert rc.suggest(category="failure", tool="Read", mode="review") is None  # rejected
    assert rc.suggest(category="timeout", tool="Bash", mode="implement") is None  # candidate


def test_match_uses_constraint_key_derivation(tmp_path):
    db = tmp_path / "store.duckdb"
    _store(db, [("constraint_promotion", "path_denial-Edit-implement", "promoted", None,
                 {"avg_trust": 0.3})])
    rc = RatchetConsumer.from_db(db)
    assert [r.candidate_key for r in rc.match(category="path_denial", tool="Edit", mode="implement")] \
        == ["path_denial-Edit-implement"]
    # None tool/mode fall back to the producer's 'unknown'/'any' literals
    assert RatchetConsumer.constraint_key("failure") == "failure-unknown-any"


def test_orders_by_evidence_trust(tmp_path):
    db = tmp_path / "store.duckdb"
    _store(db, [
        ("constraint_promotion", "failure-Bash-implement", "promoted", "lo", {"avg_trust": 0.2}),
        ("constraint_promotion", "failure-Bash-implement", "promoted", "hi", {"avg_trust": 0.8}),
    ])
    rc = RatchetConsumer.from_db(db)
    best = rc.suggest(category="failure", tool="Bash", mode="implement")
    assert best.trust == 0.8 and best.reason == "hi"  # highest-trust first


def test_tool_ratchet_via_match_key_and_coaching(tmp_path):
    db = tmp_path / "store.duckdb"
    _store(db, [("tool_promotion", "bash-to-find-definitions", "promoted", None,
                 {"success_rate": 0.9, "frequency": 12, "sessions": 4})])
    rc = RatchetConsumer.from_db(db)
    matches = rc.match_key("bash-to-find-definitions")
    assert matches and matches[0].trust == 0.9
    assert "bash-to-find-definitions" in matches[0].coaching_message()


# ── Conformance: drive the REAL agent-riggs producer ─────────────────────────
agent_riggs = pytest.importorskip("agent_riggs", reason="agent-riggs not installed")


def test_candidate_key_matches_real_producer(tmp_path):
    """THE contract tripwire: the key agent-riggs generates for a failure must equal
    the key RatchetConsumer computes for the same fingerprint. Drives the real
    find_constraint_candidates + promote, then reads the frozen store."""
    from agent_riggs.assembly import assemble

    root = tmp_path / "proj"
    root.mkdir()
    svc = assemble(root)
    proj, now = root.name, datetime.now(timezone.utc)
    # seed >= min_frequency (5) failures for one fingerprint, across sessions
    for i in range(6):
        svc.store.execute(
            "INSERT INTO failure_stream (failure_id,turn_id,session_id,project,occurred_at,"
            "failure_category,tool_name,mode,trust_at_failure,detail) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [i, i, f"sess{i % 3}", proj, now, "failure", "Bash", "implement", 0.3, None],
        )
    cands = [c for c in svc.plugin("ratchet").candidates()
             if c.candidate_type == "constraint_promotion"]
    assert cands, "producer should emit a constraint candidate for the seeded failures"
    producer_key = cands[0].candidate_key
    svc.plugin("ratchet").promote(producer_key, reason="conformance")
    svc.store.conn.close()  # freeze before the reader attaches (single-writer)

    # the consumer computes the SAME key from the fingerprint, and finds the promotion
    assert RatchetConsumer.constraint_key("failure", "Bash", "implement") == producer_key
    rc = RatchetConsumer.from_db(root / ".riggs/store.duckdb")
    suggestion = rc.suggest(category="failure", tool="Bash", mode="implement")
    assert suggestion is not None and suggestion.candidate_key == producer_key
    assert suggestion.severity == "systemic"  # 3 sessions >= min_sessions


# ── A3: integration entry points (the hook-facing shape; guard) ──────────────
def test_from_env_unset_is_none(monkeypatch):
    monkeypatch.delenv("RIGGS_RATCHET_DB", raising=False)
    assert RatchetConsumer.from_env() is None


def test_from_env_loads_and_coaches(tmp_path, monkeypatch):
    db = tmp_path / "store.duckdb"
    _store(db, [("constraint_promotion", "failure-Bash-implement", "promoted", "seen often",
                 {"avg_trust": 0.4, "severity": "systemic", "occurrences": 6, "sessions_affected": 3})])
    monkeypatch.setenv("RIGGS_RATCHET_DB", str(db))
    rc = RatchetConsumer.from_env()
    assert rc is not None
    # the exact call a PostToolUse hook would make on a Bash failure in implement mode:
    msg = rc.coaching_for_failure(tool="Bash", mode="implement")
    assert msg and "failure-Bash-implement" in msg
    # a fingerprint with no promoted ratchet stays silent
    assert rc.coaching_for_failure(tool="Read", mode="review") is None
