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

    def test_stores_model_and_success(self, tmp_path):
        """Extended report fields are stored for failure pattern aggregation."""
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.report_generation({
                "intent": "find types",
                "model": "qwen2.5-coder:3b",
                "interpreter": "python",
                "prompt_variant": "specialized",
                "failure_mode": "stdlib_leak",
                "success": False,
            })

        from kibitzer.store import KibitzerStore
        store = KibitzerStore(tmp_path / ".kibitzer" / "store.sqlite")
        events = store.query_events(event_type="generation")
        assert len(events) == 1
        assert events[0]["tool_name"] == "qwen2.5-coder:3b"
        assert events[0]["success"] == 0
        data = json.loads(events[0]["data"])
        assert data["failure_mode"] == "stdlib_leak"


class TestGetFailurePatterns:
    def _seed_generations(self, session, reports):
        """Seed the store with a list of generation reports."""
        for report in reports:
            session.report_generation(report)

    def test_empty_store(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            patterns = session.get_failure_patterns()
            assert patterns == []

    def test_aggregates_by_mode_and_model(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            self._seed_generations(session, [
                {"failure_mode": "stdlib_leak", "model": "qwen:3b",
                 "intent": "read files", "success": False},
                {"failure_mode": "stdlib_leak", "model": "qwen:3b",
                 "intent": "find defs", "success": False},
                {"failure_mode": "path_prefix", "model": "qwen:3b",
                 "intent": "list files", "success": False},
                {"failure_mode": "stdlib_leak", "model": "smollm2",
                 "intent": "search", "success": False},
                # Success — no failure_mode
                {"model": "qwen:3b", "intent": "count lines", "success": True},
            ])
            patterns = session.get_failure_patterns()
            # Should be sorted by count descending
            assert len(patterns) == 3
            assert patterns[0]["pattern"] == "stdlib_leak"
            assert patterns[0]["model"] == "qwen:3b"
            assert patterns[0]["count"] == 2

    def test_filter_by_model(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            self._seed_generations(session, [
                {"failure_mode": "stdlib_leak", "model": "qwen:3b",
                 "intent": "a", "success": False},
                {"failure_mode": "stdlib_leak", "model": "smollm2",
                 "intent": "b", "success": False},
            ])
            patterns = session.get_failure_patterns(model="smollm2")
            assert len(patterns) == 1
            assert patterns[0]["model"] == "smollm2"

    def test_ignores_successes(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            self._seed_generations(session, [
                {"model": "qwen:3b", "intent": "a", "success": True},
                {"model": "qwen:3b", "intent": "b", "success": True},
            ])
            patterns = session.get_failure_patterns()
            assert patterns == []


class TestGetPromptHints:
    def _seed_generations(self, session, reports):
        for report in reports:
            session.report_generation(report)

    def test_empty_store_returns_empty(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            hints = session.get_prompt_hints()
            assert hints == []

    def test_known_failure_mode_produces_hint(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            # 3 failures out of 4 generations → 75% confidence
            self._seed_generations(session, [
                {"failure_mode": "stdlib_leak", "model": "qwen:3b",
                 "success": False, "intent": "a"},
                {"failure_mode": "stdlib_leak", "model": "qwen:3b",
                 "success": False, "intent": "b"},
                {"failure_mode": "stdlib_leak", "model": "qwen:3b",
                 "success": False, "intent": "c"},
                {"model": "qwen:3b", "success": True, "intent": "d"},
            ])
            hints = session.get_prompt_hints(model="qwen:3b")
            assert len(hints) == 1
            assert hints[0]["type"] == "negative_constraint"
            assert "open()" in hints[0]["content"]
            assert hints[0]["confidence"] == 0.75
            assert hints[0]["source"] == "failure_pattern:stdlib_leak"

    def test_unknown_failure_mode_gets_generic_hint(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            self._seed_generations(session, [
                {"failure_mode": "wrong_tool_args", "model": "qwen:3b",
                 "success": False, "intent": "a"},
                {"failure_mode": "wrong_tool_args", "model": "qwen:3b",
                 "success": False, "intent": "b"},
            ])
            hints = session.get_prompt_hints(model="qwen:3b")
            assert len(hints) == 1
            assert "wrong tool args" in hints[0]["content"]

    def test_low_confidence_filtered_out(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            # 1 failure out of 10 → 10% confidence, below default 30% threshold
            self._seed_generations(session, [
                {"failure_mode": "stdlib_leak", "model": "qwen:3b",
                 "success": False, "intent": "a"},
            ] + [
                {"model": "qwen:3b", "success": True, "intent": f"ok{i}"}
                for i in range(9)
            ])
            hints = session.get_prompt_hints(model="qwen:3b")
            assert hints == []

    def test_implement_not_orchestrate_hint(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            self._seed_generations(session, [
                {"failure_mode": "implement_not_orchestrate", "model": "smollm2",
                 "success": False, "intent": "find defs"},
                {"failure_mode": "implement_not_orchestrate", "model": "smollm2",
                 "success": False, "intent": "search"},
            ])
            hints = session.get_prompt_hints(model="smollm2")
            assert len(hints) == 1
            assert "call the pre-loaded tools" in hints[0]["content"]


class TestGetModePolicy:
    def test_returns_current_mode_info(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            policy = session.get_mode_policy()
            assert policy["mode"] == "implement"
            assert "src/" in policy["writable"]

    def test_reflects_mode_change(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.change_mode("review")
            policy = session.get_mode_policy()
            assert policy["mode"] == "review"
            assert policy["writable"] == []
