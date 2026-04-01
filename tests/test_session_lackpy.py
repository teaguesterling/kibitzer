"""Tests for lackpy integration APIs on KibitzerSession."""

import json
from kibitzer.session import KibitzerSession
from kibitzer.state import fresh_state, save_state


def _project(tmp_path):
    state_dir = tmp_path / ".kibitzer"
    state_dir.mkdir()
    save_state(fresh_state(), state_dir)
    return tmp_path


class TestRegisterTools:
    def test_register_and_query(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.register_tools([
                {"name": "Read", "grade": (0, 0)},
                {"name": "Edit", "grade": (2, 1)},
                {"name": "Bash", "grade": (4, 4)},
            ])
            tools = session.registered_tools
            assert tools["Read"] == (0, 0)
            assert tools["Bash"] == (4, 4)

    def test_not_persisted(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.register_tools([{"name": "Read", "grade": (0, 0)}])

        with KibitzerSession(project_dir=proj) as session:
            assert session.registered_tools == {}


class TestValidateProgram:
    def test_grade_ceiling_violation(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.register_tools([
                {"name": "Edit", "grade": (2, 1)},
                {"name": "Bash", "grade": (4, 4)},
            ])
            result = session.validate_program({
                "calls": [
                    {"tool": "Edit", "input": {"file_path": "src/foo.py"}},
                    {"tool": "Bash", "input": {"command": "rm -rf /"}},
                ],
                "grade_ceiling": (2, 2),
            })
            assert result.denied
            assert "Bash" in result.reason

    def test_call_budget_exceeded(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            result = session.validate_program({
                "calls": [{"tool": "Read", "input": {}}] * 10,
                "call_budget": 5,
            })
            assert result.denied
            assert "budget" in result.reason.lower()

    def test_path_violations_included(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            result = session.validate_program({
                "calls": [
                    {"tool": "Edit", "input": {"file_path": "tests/foo.py"}},
                ],
            })
            assert result.denied

    def test_all_valid(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            result = session.validate_program({
                "calls": [
                    {"tool": "Read", "input": {"file_path": "src/foo.py"}},
                    {"tool": "Edit", "input": {"file_path": "src/bar.py"}},
                ],
            })
            assert not result.denied

    def test_does_not_modify_state(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            before = session.state["total_calls"]
            session.validate_program({"calls": [{"tool": "Read", "input": {}}]})
            assert session.state["total_calls"] == before


class TestRegisterContext:
    def test_context_stored(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.register_context({
                "task_type": "lackpy_delegation",
                "intent": "find bugs",
            })
            assert session.context["task_type"] == "lackpy_delegation"

    def test_context_in_events(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.register_context({"task_type": "lackpy_delegation"})
            session.after_call("Read", {}, success=True)

        from kibitzer.store import KibitzerStore
        store = KibitzerStore(tmp_path / ".kibitzer" / "store.sqlite")
        events = store.query_events(event_type="tool_call")
        assert len(events) >= 1
        data = json.loads(events[0]["data"]) if events[0]["data"] else {}
        assert data.get("context", {}).get("task_type") == "lackpy_delegation"


class TestReportGeneration:
    def test_appends_to_store(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.report_generation({
                "intent": "find bugs",
                "calls_planned": 5,
                "calls_executed": 5,
                "success": True,
                "calls_replaced": 3,
            })

        from kibitzer.store import KibitzerStore
        store = KibitzerStore(tmp_path / ".kibitzer" / "store.sqlite")
        events = store.query_events(event_type="generation")
        assert len(events) == 1
        data = json.loads(events[0]["data"])
        assert data["intent"] == "find bugs"
        assert data["calls_replaced"] == 3
