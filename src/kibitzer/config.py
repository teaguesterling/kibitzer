"""Load and merge kibitzer configuration."""

from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.toml"

_FALLBACK_MODE = {"writable": ["*"], "strategy": ""}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Override wins for leaf values."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(project_dir: Path | None = None) -> dict:
    """Load config: defaults merged with project-local overrides.

    Config sources, checked in order (highest priority last):
      1. Package defaults (config.toml shipped with kibitzer)
      2. Project-local .kibitzer/config.toml (TOML overrides)
      3. Project-local .kibitzer/policy.duckdb (legacy ducklog database)
      4. Project-local .kibitzer/policy.db (umwelt compiled policy)

    Umwelt policy (tier 4) supersedes ducklog (tier 3). When both exist,
    only the umwelt policy is used. Either way, the result merges on top
    of the TOML config.
    """
    with open(DEFAULT_CONFIG_PATH, "rb") as f:
        config = tomllib.load(f)

    if project_dir is not None:
        project_config = project_dir / ".kibitzer" / "config.toml"
        if project_config.exists():
            try:
                with open(project_config, "rb") as f:
                    overrides = tomllib.load(f)
                config = _deep_merge(config, overrides)
            except (tomllib.TOMLDecodeError, OSError):
                pass  # corrupt project config — use defaults

        # Tier 4: umwelt compiled policy (preferred)
        policy_db = project_dir / ".kibitzer" / "policy.db"
        if policy_db.exists():
            umwelt_config = _load_from_umwelt(policy_db)
            if umwelt_config:
                config = _deep_merge(config, umwelt_config)
                return config

        # Tier 3: legacy ducklog policy database (fallback)
        ducklog_db = project_dir / ".kibitzer" / "policy.duckdb"
        if ducklog_db.exists():
            ducklog_config = _load_from_ducklog(ducklog_db)
            if ducklog_config:
                config = _deep_merge(config, ducklog_config)

    return config


def _load_from_umwelt(db_path: Path) -> dict | None:
    """Load mode config from a compiled umwelt policy database.

    Returns None if umwelt is not installed — this is an optional
    integration, not a hard dependency.
    """
    try:
        from kibitzer.umwelt.consumer import PolicyConsumer

        consumer = PolicyConsumer.from_db(db_path)
        if consumer is None:
            return None
        return consumer.to_config()
    except ImportError:
        return None
    except Exception:
        return None


def _load_from_ducklog(db_path: Path) -> dict | None:
    """Load mode and tool config from a ducklog policy database.

    Returns None if duckdb or ducklog is not installed — this is an
    optional integration, not a hard dependency.
    """
    try:
        from ducklog.consumers.kibitzer import load_config_from_duckdb
        return load_config_from_duckdb(str(db_path))
    except ImportError:
        return None
    except Exception:
        return None  # corrupt DB — fall through to TOML


def get_mode_policy(config: dict, mode: str) -> dict:
    """Get the writable/strategy policy for a mode. Unknown modes are unrestricted."""
    return config.get("modes", {}).get(mode, _FALLBACK_MODE)
