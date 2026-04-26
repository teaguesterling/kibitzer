"""Tests for kibitzer's umwelt plugin — vocabulary and consumer."""

from __future__ import annotations

import pytest

try:
    from umwelt.policy import PolicyEngine
    from umwelt.registry.taxa import registry_scope

    HAS_UMWELT = True
except ImportError:
    HAS_UMWELT = False

pytestmark = pytest.mark.skipif(not HAS_UMWELT, reason="umwelt not installed")


@pytest.fixture
def _vocab():
    """Register sandbox + kibitzer vocabulary in an isolated scope."""
    with registry_scope():
        from umwelt.sandbox.vocabulary import register_sandbox_vocabulary

        from kibitzer.umwelt.vocabulary import register_kibitzer_vocabulary

        register_sandbox_vocabulary()
        register_kibitzer_vocabulary()
        yield


@pytest.fixture
def policy_engine(_vocab):
    """A PolicyEngine with modes and kibitzer vocabulary."""
    engine = PolicyEngine()
    engine.add_entities([
        {"type": "mode", "id": "implement", "classes": (), "attributes": {}},
        {"type": "mode", "id": "test", "classes": (), "attributes": {}},
        {"type": "mode", "id": "explore", "classes": (), "attributes": {}},
        {"type": "mode", "id": "free", "classes": (), "attributes": {}},
    ])
    engine.add_stylesheet("""
        mode { writable: *; strategy: ; coaching-frequency: 5; }
        mode#implement { writable: src/, lib/; }
        mode#test { writable: tests/, test/, spec/;
                    strategy: Write tests for expected behavior, not current behavior.; }
        mode#explore { writable: ; max-turns: 20;
                       strategy: Map the territory before making changes.; }
        mode#free { writable: *; }
    """)
    return engine


class TestVocabularyRegistration:
    def test_registers_writable_property(self, _vocab):
        from umwelt.registry.properties import get_property

        prop = get_property("state", "mode", "writable")
        assert prop.value_type is list
        assert prop.comparison == "pattern-in"
        assert prop.restrictive_direction == "subset"

    def test_registers_strategy_property(self, _vocab):
        from umwelt.registry.properties import get_property

        prop = get_property("state", "mode", "strategy")
        assert prop.value_type is str

    def test_registers_coaching_frequency(self, _vocab):
        from umwelt.registry.properties import get_property

        prop = get_property("state", "mode", "coaching-frequency")
        assert prop.value_type is int
        assert prop.comparison == "<="
        assert prop.restrictive_direction == "min"

    def test_registers_max_consecutive_failures(self, _vocab):
        from umwelt.registry.properties import get_property

        prop = get_property("state", "mode", "max-consecutive-failures")
        assert prop.value_type is int
        assert prop.comparison == "<="

    def test_registers_max_turns(self, _vocab):
        from umwelt.registry.properties import get_property

        prop = get_property("state", "mode", "max-turns")
        assert prop.value_type is int
        assert prop.comparison == "<="
        assert prop.value_range == (1, 200)

    def test_no_duplicate_registration(self, _vocab):
        from umwelt.errors import RegistryError

        from kibitzer.umwelt.vocabulary import register_kibitzer_vocabulary

        with pytest.raises(RegistryError, match="already registered"):
            register_kibitzer_vocabulary()


class TestPolicyEngineResolve:
    def test_resolve_implement_mode(self, policy_engine):
        props = policy_engine.resolve(type="mode", id="implement")
        assert props is not None
        assert isinstance(props, dict)
        assert "src/, lib/" in props.get("writable", "")

    def test_resolve_explore_mode(self, policy_engine):
        props = policy_engine.resolve(type="mode", id="explore")
        assert props is not None
        assert isinstance(props, dict)
        assert props.get("max-turns") == "20"

    def test_resolve_unknown_mode(self, policy_engine):
        props = policy_engine.resolve(type="mode", id="nonexistent")
        assert not props  # empty dict for nonexistent entity


class TestPolicyConsumer:
    def test_from_engine(self, policy_engine):
        from kibitzer.umwelt.consumer import PolicyConsumer

        consumer = PolicyConsumer.from_engine(policy_engine)
        assert consumer is not None
        assert consumer.engine is policy_engine

    def test_get_mode_policy_implement(self, policy_engine):
        from kibitzer.umwelt.consumer import PolicyConsumer

        consumer = PolicyConsumer.from_engine(policy_engine)
        policy = consumer.get_mode_policy("implement")
        assert policy is not None
        assert policy.name == "implement"
        assert "src/" in policy.writable
        assert "lib/" in policy.writable

    def test_get_mode_policy_explore(self, policy_engine):
        from kibitzer.umwelt.consumer import PolicyConsumer

        consumer = PolicyConsumer.from_engine(policy_engine)
        policy = consumer.get_mode_policy("explore")
        assert policy is not None
        assert policy.writable == []
        assert policy.max_turns == 20
        assert "territory" in policy.strategy

    def test_get_mode_policy_free(self, policy_engine):
        from kibitzer.umwelt.consumer import PolicyConsumer

        consumer = PolicyConsumer.from_engine(policy_engine)
        policy = consumer.get_mode_policy("free")
        assert policy is not None
        assert policy.writable == ["*"]

    def test_get_mode_policy_unknown(self, policy_engine):
        from kibitzer.umwelt.consumer import PolicyConsumer

        consumer = PolicyConsumer.from_engine(policy_engine)
        policy = consumer.get_mode_policy("nonexistent")
        assert policy is None

    def test_list_modes(self, policy_engine):
        from kibitzer.umwelt.consumer import PolicyConsumer

        consumer = PolicyConsumer.from_engine(policy_engine)
        modes = consumer.list_modes()
        assert set(modes) >= {"implement", "test", "explore", "free"}

    def test_to_config_bridge(self, policy_engine):
        from kibitzer.umwelt.consumer import PolicyConsumer

        consumer = PolicyConsumer.from_engine(policy_engine)
        config = consumer.to_config()
        assert "modes" in config
        assert "implement" in config["modes"]
        assert "src/" in config["modes"]["implement"]["writable"]

    def test_caching(self, policy_engine):
        from kibitzer.umwelt.consumer import PolicyConsumer

        consumer = PolicyConsumer.from_engine(policy_engine)
        p1 = consumer.get_mode_policy("implement")
        p2 = consumer.get_mode_policy("implement")
        assert p1 is p2

    def test_invalidate_cache(self, policy_engine):
        from kibitzer.umwelt.consumer import PolicyConsumer

        consumer = PolicyConsumer.from_engine(policy_engine)
        p1 = consumer.get_mode_policy("implement")
        consumer.invalidate_cache()
        p2 = consumer.get_mode_policy("implement")
        assert p1 is not p2
        assert p1.writable == p2.writable


class TestPolicyConsumerFromDb:
    def test_from_db_with_compiled_policy(self, tmp_path, policy_engine):
        db_path = tmp_path / "policy.db"
        policy_engine.save(str(db_path))

        from kibitzer.umwelt.consumer import PolicyConsumer

        consumer = PolicyConsumer.from_db(db_path)
        assert consumer is not None
        policy = consumer.get_mode_policy("implement")
        assert policy is not None
        assert "src/" in policy.writable

    def test_from_db_missing_file(self, tmp_path):
        from kibitzer.umwelt.consumer import PolicyConsumer

        consumer = PolicyConsumer.from_db(tmp_path / "nonexistent.db")
        assert consumer is None


class TestConfigIntegration:
    def test_umwelt_policy_merged_into_config(self, tmp_path, policy_engine):
        kibitzer_dir = tmp_path / ".kibitzer"
        kibitzer_dir.mkdir()
        policy_engine.save(str(kibitzer_dir / "policy.db"))

        from kibitzer.config import load_config

        config = load_config(project_dir=tmp_path)
        assert "implement" in config["modes"]
        assert "src/" in config["modes"]["implement"]["writable"]

    def test_umwelt_supersedes_ducklog(self, tmp_path, policy_engine):
        """When both policy.db and policy.duckdb exist, umwelt wins."""
        kibitzer_dir = tmp_path / ".kibitzer"
        kibitzer_dir.mkdir()
        policy_engine.save(str(kibitzer_dir / "policy.db"))
        # Create a dummy duckdb file (won't be read)
        (kibitzer_dir / "policy.duckdb").write_bytes(b"dummy")

        from kibitzer.config import load_config

        config = load_config(project_dir=tmp_path)
        # Should have umwelt-resolved modes, not duckdb error
        assert "implement" in config["modes"]

    def test_toml_defaults_without_umwelt(self, tmp_path):
        from kibitzer.config import load_config

        config = load_config(project_dir=tmp_path)
        assert "implement" in config["modes"]
        assert config["modes"]["implement"]["writable"] == ["src/", "lib/"]


class TestSessionPolicyConsumer:
    def test_session_loads_policy_consumer(self, tmp_path, policy_engine):
        kibitzer_dir = tmp_path / ".kibitzer"
        kibitzer_dir.mkdir()
        policy_engine.save(str(kibitzer_dir / "policy.db"))

        from kibitzer.session import KibitzerSession

        with KibitzerSession(project_dir=tmp_path) as session:
            assert session.policy_consumer is not None

    def test_session_no_policy_consumer_without_db(self, tmp_path):
        kibitzer_dir = tmp_path / ".kibitzer"
        kibitzer_dir.mkdir()

        from kibitzer.session import KibitzerSession

        with KibitzerSession(project_dir=tmp_path) as session:
            assert session.policy_consumer is None

    def test_get_mode_policy_uses_consumer(self, tmp_path, policy_engine):
        kibitzer_dir = tmp_path / ".kibitzer"
        kibitzer_dir.mkdir()
        policy_engine.save(str(kibitzer_dir / "policy.db"))

        from kibitzer.session import KibitzerSession

        with KibitzerSession(project_dir=tmp_path) as session:
            mp = session.get_mode_policy()
            assert mp["mode"] == "implement"
            assert "src/" in mp["writable"]
