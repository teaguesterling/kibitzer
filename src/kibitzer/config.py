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
    """Load config: defaults merged with project-local .kibitzer/config.toml."""
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

    return config


def get_mode_policy(config: dict, mode: str) -> dict:
    """Get the writable/strategy policy for a mode. Unknown modes are unrestricted."""
    return config.get("modes", {}).get(mode, _FALLBACK_MODE)
