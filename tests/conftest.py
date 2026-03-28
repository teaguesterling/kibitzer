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
