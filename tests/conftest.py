"""Shared test fixtures for kibitzer."""

import pytest


@pytest.fixture
def state_dir(tmp_path):
    """Create a temporary .kibitzer directory for state tests."""
    d = tmp_path / ".kibitzer"
    d.mkdir()
    return d


@pytest.fixture
def project_dir(tmp_path):
    """A temporary project directory."""
    return tmp_path


@pytest.fixture(autouse=True)
def _no_live_context7(monkeypatch):
    """Never hit the live context7.com API during tests — it makes doc-context tests
    flaky and network-dependent (they assert on whatever the service happens to return).
    Default it to a no-op; tests that exercise context7 patch _retrieve_from_context7
    themselves and override this within their own scope."""
    monkeypatch.setattr(
        "kibitzer.session.KibitzerSession._retrieve_from_context7",
        lambda self, query: [],
        raising=False,
    )


@pytest.fixture(autouse=True)
def _deterministic_nudge(monkeypatch, tmp_path):
    """Make the nudge A/B deterministic in tests: always take the NUDGE arm
    (random -> 0.0, below any probability) and never write to the real
    ~/.kibitzer/nudge_trials.jsonl. Tests of the CONTROL arm re-patch random
    within their own scope."""
    monkeypatch.setattr("kibitzer.session.random.random", lambda: 0.0, raising=False)
    monkeypatch.setattr("kibitzer.session._TRIAL_LOG",
                        tmp_path / "nudge_trials.jsonl", raising=False)
