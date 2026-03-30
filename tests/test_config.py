from pathlib import Path
from kibitzer.config import load_config, get_mode_policy


def test_load_default_config():
    """Loading with no project config returns defaults."""
    config = load_config(project_dir=Path("/nonexistent"))
    assert "modes" in config
    assert "implement" in config["modes"]
    assert config["modes"]["implement"]["writable"] == ["src/", "lib/"]


def test_load_default_has_all_modes():
    config = load_config(project_dir=Path("/nonexistent"))
    expected_modes = {"free", "implement", "test", "docs", "explore"}
    assert set(config["modes"].keys()) == expected_modes


def test_project_config_overrides_defaults(tmp_path):
    """Project-local config.toml overrides specific values."""
    project_config = tmp_path / ".kibitzer" / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text("""
[modes.implement]
writable = ["src/", "lib/", "pkg/"]
strategy = "custom strategy"
""")
    config = load_config(project_dir=tmp_path)
    assert config["modes"]["implement"]["writable"] == ["src/", "lib/", "pkg/"]
    assert config["modes"]["implement"]["strategy"] == "custom strategy"
    # Other modes still have defaults
    assert config["modes"]["explore"]["writable"] == []


def test_get_mode_policy():
    config = load_config(project_dir=Path("/nonexistent"))
    policy = get_mode_policy(config, "implement")
    assert policy["writable"] == ["src/", "lib/"]
    assert policy["strategy"] == ""


def test_get_mode_policy_unknown_mode():
    config = load_config(project_dir=Path("/nonexistent"))
    policy = get_mode_policy(config, "nonexistent_mode")
    assert policy["writable"] == ["*"]


def test_controller_config():
    config = load_config(project_dir=Path("/nonexistent"))
    assert config["controller"]["max_consecutive_failures"] == 3
    assert config["controller"]["max_turns_in_explore"] == 20


def test_coach_config_with_model_overrides():
    config = load_config(project_dir=Path("/nonexistent"))
    assert config["coach"]["frequency"] == 5
    assert config["coach"]["model_overrides"]["haiku"]["frequency"] == 3


def test_plugin_config():
    config = load_config(project_dir=Path("/nonexistent"))
    assert config["plugins"]["blq"]["mode"] == "observe"
    assert config["plugins"]["blq"]["enabled"] is True


# --- Config corruption resilience ---

def test_corrupt_project_config_uses_defaults(tmp_path):
    """Invalid TOML in project config should fall back to defaults."""
    project_config = tmp_path / ".kibitzer" / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text("this is not valid toml {{{{")
    config = load_config(project_dir=tmp_path)
    # Should have all default modes
    assert "implement" in config["modes"]
    assert config["modes"]["implement"]["writable"] == ["src/", "lib/"]


def test_empty_project_config_uses_defaults(tmp_path):
    """Empty project config should use defaults (empty TOML is valid)."""
    project_config = tmp_path / ".kibitzer" / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text("")
    config = load_config(project_dir=tmp_path)
    assert "implement" in config["modes"]
