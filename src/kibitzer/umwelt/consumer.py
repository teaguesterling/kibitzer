"""Policy consumer — reads resolved umwelt policy for kibitzer enforcement.

Wraps PolicyEngine to provide kibitzer-specific queries: mode policy,
path access, tool restrictions. Falls back gracefully when umwelt is
not installed or no policy database exists.

Three integration levels:
    1. No umwelt → config.toml only (existing behavior)
    2. Compiled policy.db → PolicyConsumer.from_db() loads pre-resolved policy
    3. Live PolicyEngine → PolicyConsumer.from_engine() for in-process use
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ModePolicy:
    """Resolved policy for a single mode."""

    name: str
    writable: list[str] = field(default_factory=lambda: ["*"])
    strategy: str = ""
    coaching_frequency: int | None = None
    max_consecutive_failures: int | None = None
    max_turns: int | None = None


class PolicyConsumer:
    """Kibitzer's interface to the umwelt policy engine.

    Queries resolved policy for mode configuration, path access,
    and tool restrictions. Caches resolved mode policies for the
    lifetime of the consumer.
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self._mode_cache: dict[tuple[str, str | None], ModePolicy] = {}

    @classmethod
    def from_db(cls, path: str | Path) -> PolicyConsumer | None:
        """Load from a compiled policy database. Returns None if unavailable."""
        path = Path(path)
        if not path.exists():
            return None
        try:
            from umwelt.policy import PolicyEngine

            engine = PolicyEngine.from_db(path)
            return cls(engine)
        except ImportError:
            return None
        except Exception:
            return None

    @classmethod
    def from_engine(cls, engine: Any) -> PolicyConsumer:
        """Wrap an existing PolicyEngine instance."""
        return cls(engine)

    @property
    def engine(self) -> Any:
        return self._engine

    def get_mode_policy(
        self, mode: str, active_mode: str | None = None,
    ) -> ModePolicy | None:
        """Resolve a mode's full policy from the cascade.

        Args:
            mode: The mode entity to resolve properties for.
            active_mode: Current active mode for cross-axis filtering
                (v0.6). Rules scoped to a specific mode only apply
                when that mode is active. Pass None to skip filtering.

        Returns None if the mode doesn't exist in the policy.
        """
        cache_key = (mode, active_mode)
        if cache_key in self._mode_cache:
            return self._mode_cache[cache_key]

        props = self._engine.resolve(type="mode", id=mode, mode=active_mode)
        if not props or not isinstance(props, dict):
            return None

        writable = _parse_writable(props.get("writable"))

        policy = ModePolicy(
            name=mode,
            writable=writable,
            strategy=props.get("strategy", ""),
            coaching_frequency=_parse_int(props.get("coaching-frequency")),
            max_consecutive_failures=_parse_int(
                props.get("max-consecutive-failures"),
            ),
            max_turns=_parse_int(props.get("max-turns")),
        )
        self._mode_cache[cache_key] = policy
        return policy

    def list_modes(self, active_mode: str | None = None) -> list[str]:
        """Return all mode IDs defined in the policy."""
        try:
            all_modes = self._engine.resolve_all(
                type="mode", mode=active_mode,
            )
            return [
                m.get("entity_id", "")
                for m in all_modes
                if m.get("entity_id")
            ]
        except Exception:
            return []

    def get_tool_policy(
        self, tool_name: str, active_mode: str | None = None,
    ) -> dict[str, str]:
        """Resolve tool properties from the cascade.

        Args:
            tool_name: Tool entity to resolve.
            active_mode: Current mode for cross-axis filtering (v0.6).
                Mode-scoped tool rules (e.g. "allow Bash only in free
                mode") only apply when that mode is active.
        """
        props = self._engine.resolve(
            type="tool", id=tool_name, mode=active_mode,
        )
        if not props or not isinstance(props, dict):
            return {}
        return dict(props)

    def to_config(self, active_mode: str | None = None) -> dict[str, Any]:
        """Convert resolved policy to kibitzer config dict format.

        Bridge for backwards compatibility — existing code that reads
        config dicts works unchanged with policy-resolved data.

        Args:
            active_mode: Current mode for cross-axis filtering. When
                provided, mode-scoped rules only apply for the active mode.
        """
        modes: dict[str, dict[str, Any]] = {}
        for mode_id in self.list_modes(active_mode=active_mode):
            policy = self.get_mode_policy(mode_id, active_mode=active_mode)
            if policy is None:
                continue
            mode_dict: dict[str, Any] = {
                "writable": policy.writable,
                "strategy": policy.strategy,
            }
            if policy.coaching_frequency is not None:
                mode_dict["coaching_frequency"] = policy.coaching_frequency
            if policy.max_consecutive_failures is not None:
                mode_dict["max_consecutive_failures"] = (
                    policy.max_consecutive_failures
                )
            if policy.max_turns is not None:
                mode_dict["max_turns"] = policy.max_turns
            modes[mode_id] = mode_dict

        result: dict[str, Any] = {}
        if modes:
            result["modes"] = modes
        return result

    def invalidate_cache(self) -> None:
        """Clear cached mode policies (e.g. after a mode switch)."""
        self._mode_cache.clear()


def _parse_writable(value: str | None) -> list[str]:
    """Parse writable property from resolved string to path list."""
    if value is None:
        return ["*"]
    value = value.strip()
    if not value:
        return []
    if value == "*":
        return ["*"]
    return [p.strip() for p in value.split(",") if p.strip()]


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None
