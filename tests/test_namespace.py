"""Tests for namespace support on KibitzerSession."""

import json

from kibitzer.session import KibitzerSession
from kibitzer.state import fresh_state, save_state
from kibitzer.store import KibitzerStore


def _project(tmp_path):
    state_dir = tmp_path / ".kibitzer"
    state_dir.mkdir()
    save_state(fresh_state(), state_dir)
    return tmp_path


class TestNamespaceInit:
    def test_default_namespace_is_none(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            assert session.namespace is None

    def test_namespace_set_at_init(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj, namespace="python") as session:
            assert session.namespace == "python"

    def test_namespace_context_manager(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj, namespace="python") as session:
            assert session.namespace == "python"
            with session.ns("sql"):
                assert session.namespace == "sql"
            assert session.namespace == "python"

    def test_ns_restores_on_exception(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj, namespace="python") as session:
            try:
                with session.ns("sql"):
                    raise ValueError("boom")
            except ValueError:
                pass
            assert session.namespace == "python"


class TestNamespaceInReportGeneration:
    def test_namespace_stored_in_generation_event(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj, namespace="python") as session:
            session.report_generation({
                "intent": "read file",
                "success": False,
                "failure_mode": "stdlib_leak",
                "model": "qwen:3b",
            })

        store = KibitzerStore(tmp_path / ".kibitzer" / "store.sqlite")
        events = store.query_events(event_type="generation")
        data = json.loads(events[0]["data"])
        assert data.get("namespace") == "python"

    def test_explicit_namespace_overrides_session(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj, namespace="python") as session:
            session.report_generation({
                "intent": "query table",
                "success": False,
                "failure_mode": "syntax_artifact",
                "model": "qwen:3b",
            }, namespace="sql")

        store = KibitzerStore(tmp_path / ".kibitzer" / "store.sqlite")
        events = store.query_events(event_type="generation")
        data = json.loads(events[0]["data"])
        assert data.get("namespace") == "sql"

    def test_no_namespace_when_none(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.report_generation({
                "intent": "read file",
                "success": True,
                "model": "qwen:3b",
            })

        store = KibitzerStore(tmp_path / ".kibitzer" / "store.sqlite")
        events = store.query_events(event_type="generation")
        data = json.loads(events[0]["data"])
        assert "namespace" not in data


class TestNamespaceInFailurePatterns:
    def test_patterns_scoped_to_namespace(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.report_generation({
                "failure_mode": "stdlib_leak", "model": "qwen:3b",
                "intent": "a", "success": False, "namespace": "python",
            })
            session.report_generation({
                "failure_mode": "syntax_artifact", "model": "qwen:3b",
                "intent": "b", "success": False, "namespace": "sql",
            })
            # Unscoped: sees both
            all_patterns = session.get_failure_patterns()
            assert len(all_patterns) == 2

            # Scoped: sees only python
            py_patterns = session.get_failure_patterns(namespace="python")
            assert len(py_patterns) == 1
            assert py_patterns[0]["pattern"] == "stdlib_leak"

    def test_unscoped_ignores_namespace_filter(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.report_generation({
                "failure_mode": "stdlib_leak", "model": "qwen:3b",
                "intent": "a", "success": False, "namespace": "python",
            })
            session.report_generation({
                "failure_mode": "stdlib_leak", "model": "qwen:3b",
                "intent": "b", "success": False,
            })
            # None namespace means "all"
            patterns = session.get_failure_patterns()
            assert len(patterns) == 1
            assert patterns[0]["count"] == 2


class TestNamespaceInPromptHints:
    def test_prompt_hints_scoped_by_namespace(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            for _ in range(3):
                session.report_generation({
                    "failure_mode": "stdlib_leak", "model": "qwen:3b",
                    "intent": "read", "success": False,
                }, namespace="python")
            for _ in range(3):
                session.report_generation({
                    "failure_mode": "syntax_artifact", "model": "qwen:3b",
                    "intent": "query", "success": False,
                }, namespace="sql")

            py_hints = session.get_prompt_hints(
                model="qwen:3b", namespace="python", min_confidence=0.0,
            )
            sql_hints = session.get_prompt_hints(
                model="qwen:3b", namespace="sql", min_confidence=0.0,
            )

            py_sources = {h["source"] for h in py_hints}
            sql_sources = {h["source"] for h in sql_hints}

            assert "failure_pattern:stdlib_leak" in py_sources
            assert "failure_pattern:stdlib_leak" not in sql_sources
            assert "failure_pattern:syntax_artifact" in sql_sources
