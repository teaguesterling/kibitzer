"""Tests for KibitzerStore — SQLite event log."""

from kibitzer.store import KibitzerStore


class TestKibitzerStore:
    def test_create_store(self, tmp_path):
        store_path = tmp_path / "store.sqlite"
        store = KibitzerStore(store_path)
        store.init()
        assert store_path.exists()

    def test_append_and_query(self, tmp_path):
        store = KibitzerStore(tmp_path / "store.sqlite")
        store.init()
        store.append_event(
            event_type="tool_call",
            session_id="sess-001",
            tool_name="Edit",
            tool_input='{"file_path": "src/foo.py"}',
            success=True,
            mode="implement",
        )
        events = store.query_events()
        assert len(events) == 1
        assert events[0]["event_type"] == "tool_call"
        assert events[0]["tool_name"] == "Edit"
        assert events[0]["session_id"] == "sess-001"

    def test_query_by_event_type(self, tmp_path):
        store = KibitzerStore(tmp_path / "store.sqlite")
        store.init()
        store.append_event(event_type="tool_call", tool_name="Edit")
        store.append_event(event_type="mode_switch", tool_name="")
        store.append_event(event_type="tool_call", tool_name="Read")
        tool_calls = store.query_events(event_type="tool_call")
        assert len(tool_calls) == 2
        switches = store.query_events(event_type="mode_switch")
        assert len(switches) == 1

    def test_query_by_session(self, tmp_path):
        store = KibitzerStore(tmp_path / "store.sqlite")
        store.init()
        store.append_event(event_type="tool_call", session_id="sess-001")
        store.append_event(event_type="tool_call", session_id="sess-002")
        events = store.query_events(session_id="sess-001")
        assert len(events) == 1

    def test_query_limit(self, tmp_path):
        store = KibitzerStore(tmp_path / "store.sqlite")
        store.init()
        for i in range(10):
            store.append_event(event_type="tool_call", tool_name=f"tool_{i}")
        events = store.query_events(limit=3)
        assert len(events) == 3

    def test_append_with_data(self, tmp_path):
        store = KibitzerStore(tmp_path / "store.sqlite")
        store.init()
        store.append_event(
            event_type="denial",
            tool_name="Edit",
            data='{"reason": "path not writable"}',
        )
        events = store.query_events()
        assert events[0]["data"] == '{"reason": "path not writable"}'

    def test_concurrent_appends(self, tmp_path):
        store_path = tmp_path / "store.sqlite"
        store_a = KibitzerStore(store_path)
        store_a.init()
        store_b = KibitzerStore(store_path)
        store_a.append_event(event_type="tool_call", tool_name="Edit")
        store_b.append_event(event_type="tool_call", tool_name="Read")
        events = store_a.query_events()
        assert len(events) == 2
