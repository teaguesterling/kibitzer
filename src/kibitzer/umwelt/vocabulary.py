"""Register kibitzer's vocabulary contributions to umwelt.

Kibitzer doesn't create its own taxon — it adds properties to existing
entities in the state taxon. These properties let policy authors configure
kibitzer's behavior per-mode through .umw stylesheets.

Roles:
    Restrictor — mode.writable controls path access (pattern-in, subset)
    Expander   — mode.strategy, mode.coaching-frequency guide coaching
    Interactor — mode.max-consecutive-failures, mode.max-turns drive transitions

Usage:
    from kibitzer.umwelt.vocabulary import register_kibitzer_vocabulary

    engine = PolicyEngine()
    engine.register_vocabulary(register_kibitzer_vocabulary)
"""

from __future__ import annotations


def register_kibitzer_vocabulary() -> None:
    """Register kibitzer-specific properties on existing umwelt entities.

    All properties live on state.mode — the regulation entity already
    registered by the sandbox vocabulary. Policy authors write:

        mode#implement { writable: src/, lib/; strategy: ""; }
        mode#explore   { writable: ; max-turns: 20; }
        mode#test      { writable: tests/, test/, spec/; }
    """
    from umwelt.registry import register_property

    # --- Restrictor: path access ---

    register_property(
        taxon="state",
        entity="mode",
        name="writable",
        value_type=list,
        comparison="pattern-in",
        restrictive_direction="subset",
        description=(
            "Path prefixes writable in this mode. "
            "['*'] = unrestricted, [] = read-only."
        ),
        category="access",
    )

    # --- Expander: coaching ---

    register_property(
        taxon="state",
        entity="mode",
        name="strategy",
        value_type=str,
        description="Coaching strategy text shown to the agent in this mode.",
        category="coaching",
    )

    register_property(
        taxon="state",
        entity="mode",
        name="coaching-frequency",
        value_type=int,
        comparison="<=",
        restrictive_direction="min",
        value_range=(1, 100),
        description="Coach fires every N tool calls in this mode.",
        category="coaching",
    )

    # --- Interactor: transitions ---

    register_property(
        taxon="state",
        entity="mode",
        name="max-consecutive-failures",
        value_type=int,
        comparison="<=",
        restrictive_direction="min",
        value_range=(1, 50),
        description=(
            "Auto-transition threshold: switch to explore "
            "after N consecutive failures."
        ),
        category="transitions",
    )

    register_property(
        taxon="state",
        entity="mode",
        name="max-turns",
        value_type=int,
        comparison="<=",
        restrictive_direction="min",
        value_range=(1, 200),
        description="Max turns in this mode before suggesting a switch.",
        category="transitions",
    )
