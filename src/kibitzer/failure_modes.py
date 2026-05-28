"""Failure-mode taxonomy for generation outcomes.

The taxonomy (mode identifiers + ALL_MODES) is lackpy's canonical, SemVer-stable
vocabulary — imported here from ``lackpy.lang.failure_modes`` (the dependency-free language
leaf) rather than mirrored, so the two sides cannot drift. Lackpy classifies the failure
mode after validation/execution; kibitzer accumulates them in the event log and maps each
to a prompt intervention (HINT_MAP, below) returned via get_prompt_hints().

The taxonomy is deliberately small. Each category maps to a specific prompt intervention —
if two failure modes need the same fix, they should be the same category.
"""

from __future__ import annotations

from lackpy.lang.failure_modes import (
    ALL_MODES,
    IMPLEMENT_NOT_ORCHESTRATE,
    JUPYTER_CONFUSION,
    KEY_HALLUCINATION,
    PATH_PREFIX,
    STDLIB_LEAK,
    SYNTAX_ARTIFACT,
    WRONG_OUTPUT,
)

# Re-export the taxonomy so existing `from kibitzer.failure_modes import ALL_MODES, ...`
# callers (kibitzer/__init__.py, session.py, tests) are unchanged.
__all__ = [
    "ALL_MODES",
    "IMPLEMENT_NOT_ORCHESTRATE",
    "STDLIB_LEAK",
    "PATH_PREFIX",
    "JUPYTER_CONFUSION",
    "SYNTAX_ARTIFACT",
    "KEY_HALLUCINATION",
    "WRONG_OUTPUT",
    "MAX_ESCALATION",
    "HINT_MAP",
]

# Maximum escalation level. Correction attempts beyond this clamp to MAX.
# Lackpy decides what each level means in prompt terms.
MAX_ESCALATION = 3

# Mapping from failure mode to structured prompt hint.
# Each entry produces a hint dict returned by get_prompt_hints().
# Maximum escalation level. Correction attempts beyond this clamp to MAX.
# Lackpy decides what each level means in prompt terms.
MAX_ESCALATION = 3


# Mapping from failure mode to structured prompt hint.
# Each entry produces a hint dict returned by get_prompt_hints().
HINT_MAP: dict[str, dict[str, str]] = {
    IMPLEMENT_NOT_ORCHESTRATE: {
        "type": "negative_constraint",
        "content": (
            "Do NOT define functions or implement logic — "
            "call the pre-loaded tools directly"
        ),
    },
    STDLIB_LEAK: {
        "type": "negative_constraint",
        "content": "Do NOT use open() — call read_file() instead",
    },
    PATH_PREFIX: {
        "type": "negative_constraint",
        "content": (
            "All paths are relative to the workspace root. "
            "Use bare filenames (e.g. 'app.py'), not prefixed paths"
        ),
    },
    JUPYTER_CONFUSION: {
        "type": "negative_constraint",
        "content": (
            "Output a complete Python program, not a language identifier. "
            "Do NOT output bare tokens like 'python' or 'ipynb'"
        ),
    },
    SYNTAX_ARTIFACT: {
        "type": "negative_constraint",
        "content": (
            "Output ONLY valid Python — no type annotations with ->, "
            "no prose, no arrow operators"
        ),
    },
    KEY_HALLUCINATION: {
        "type": "negative_constraint",
        "content": (
            "Check the actual return schema of each tool before "
            "accessing dictionary keys"
        ),
    },
    WRONG_OUTPUT: {
        "type": "instruction",
        "content": (
            "Previous attempt produced wrong output. "
            "Re-read the intent carefully and verify the result"
        ),
    },
}
