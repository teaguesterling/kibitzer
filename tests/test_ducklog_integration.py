"""Test that kibitzer reads config from a ducklog policy database.

When .kibitzer/policy.duckdb exists, its mode definitions merge on top
of the TOML config. This validates the umwelt → ducklog → kibitzer pipeline.
"""
from __future__ import annotations

from pathlib import Path

import pytest

try:
    import duckdb
    from ducklog.consumers.kibitzer import load_config_from_duckdb
    HAS_DUCKLOG = True
except ImportError:
    HAS_DUCKLOG = False

pytestmark = pytest.mark.skipif(not HAS_DUCKLOG, reason="ducklog not installed")


@pytest.fixture
def project_with_duckdb(tmp_path):
    """Create a project dir with .kibitzer/policy.duckdb containing mode config."""
    kibitzer_dir = tmp_path / ".kibitzer"
    kibitzer_dir.mkdir()

    db_path = kibitzer_dir / "policy.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("""
        CREATE TABLE entities (
            id INTEGER PRIMARY KEY, taxon VARCHAR, type_name VARCHAR,
            entity_id VARCHAR, classes VARCHAR[], attributes MAP(VARCHAR, VARCHAR),
            parent_id INTEGER
        );
        INSERT INTO entities VALUES
            (1, 'state', 'mode', NULL, ['implement'], MAP{'writable': 'src/', 'strategy': ''}, NULL),
            (2, 'state', 'mode', NULL, ['deploy'], MAP{'writable': 'infra/, deploy/', 'strategy': 'Verify before applying.'}, NULL);

        CREATE VIEW kibitzer_modes AS
            SELECT classes[1] AS mode_name, attributes['writable'] AS writable,
                   attributes['strategy'] AS strategy
            FROM entities WHERE type_name = 'mode';

        CREATE VIEW kibitzer_tool_surface AS
            SELECT NULL AS selector_text, NULL AS tool_name, NULL AS allowed, NULL AS mode_name
            WHERE FALSE;
    """)
    con.close()
    return tmp_path


class TestDucklogIntegration:
    def test_ducklog_modes_merged_into_config(self, project_with_duckdb):
        from kibitzer.config import load_config
        config = load_config(project_dir=project_with_duckdb)

        # Default modes from TOML should still be present
        assert "free" in config["modes"]
        assert "explore" in config["modes"]

        # ducklog-defined modes should be merged in
        assert "deploy" in config["modes"]
        assert "infra/" in config["modes"]["deploy"]["writable"]
        assert config["modes"]["deploy"]["strategy"] == "Verify before applying."

    def test_ducklog_overrides_toml_mode(self, project_with_duckdb):
        from kibitzer.config import load_config
        config = load_config(project_dir=project_with_duckdb)

        # implement mode from ducklog overrides the TOML default
        impl = config["modes"]["implement"]
        assert impl["writable"] == ["src/"]  # ducklog says src/ only, not src/ + lib/

    def test_no_duckdb_falls_back_to_toml(self, tmp_path):
        """Without a policy.duckdb, config loads normally from TOML."""
        from kibitzer.config import load_config
        config = load_config(project_dir=tmp_path)
        assert "implement" in config["modes"]
        assert config["modes"]["implement"]["writable"] == ["src/", "lib/"]

    def test_path_guard_works_with_merged_config(self, project_with_duckdb):
        from kibitzer.config import load_config, get_mode_policy
        from kibitzer.guards.path_guard import check_path

        config = load_config(project_dir=project_with_duckdb)

        # deploy mode from ducklog
        deploy_policy = get_mode_policy(config, "deploy")
        assert check_path("infra/main.tf", deploy_policy).allowed
        assert not check_path("src/app.py", deploy_policy).allowed

        # explore mode from TOML (not overridden)
        explore_policy = get_mode_policy(config, "explore")
        assert not check_path("anything.py", explore_policy).allowed
