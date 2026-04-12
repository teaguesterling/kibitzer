"""Failure mode taxonomy for generation outcomes.

Mirrored from lackpy's canonical definition. Lackpy classifies the
failure mode after validation/execution; kibitzer accumulates them
in the event log and returns prompt hints via get_prompt_hints().

The taxonomy is deliberately small. Each category maps to a specific
prompt intervention — if two failure modes need the same fix, they
should be the same category.
"""

from __future__ import annotations

# Model defines functions/classes instead of calling pre-loaded tools.
# Fix: "ORCHESTRATE, DO NOT IMPLEMENT" framing.
IMPLEMENT_NOT_ORCHESTRATE = "implement_not_orchestrate"

# Model uses open(), import os, or other stdlib instead of kit tools.
# Fix: "Do NOT use open(). Use read_file() for ALL file reading."
STDLIB_LEAK = "stdlib_leak"

# Model prefixes paths with directory names (e.g. 'toybox/app.py').
# Fix: "All paths are relative to the workspace root."
PATH_PREFIX = "path_prefix"

# Model outputs bare tokens (ipynb, py, sql) from Jupyter framing.
# Fix: use interpreter-specialized prompt instead of Jupyter template.
JUPYTER_CONFUSION = "jupyter_confusion"

# Model emits non-Python syntax (-> annotations, prose, arrow operators).
# Fix: "Output ONLY the program — no annotations, no prose."
SYNTAX_ARTIFACT = "syntax_artifact"

# Model accesses wrong dict keys (e.g. 'path' instead of 'file').
# Fix: document return schema in namespace_desc.
KEY_HALLUCINATION = "key_hallucination"

# Model generates valid code that executes but produces wrong output.
# No single prompt fix — may need better examples or constraints.
WRONG_OUTPUT = "wrong_output"

# All recognized failure mode strings.
ALL_MODES = frozenset({
    IMPLEMENT_NOT_ORCHESTRATE,
    STDLIB_LEAK,
    PATH_PREFIX,
    JUPYTER_CONFUSION,
    SYNTAX_ARTIFACT,
    KEY_HALLUCINATION,
    WRONG_OUTPUT,
})

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
