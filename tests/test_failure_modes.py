"""Tests for the shared failure mode taxonomy."""

from kibitzer.failure_modes import (
    ALL_MODES,
    HINT_MAP,
    IMPLEMENT_NOT_ORCHESTRATE,
    STDLIB_LEAK,
    PATH_PREFIX,
    JUPYTER_CONFUSION,
    SYNTAX_ARTIFACT,
    KEY_HALLUCINATION,
    WRONG_OUTPUT,
)


class TestTaxonomy:
    def test_all_modes_has_7_entries(self):
        assert len(ALL_MODES) == 7

    def test_constants_are_in_all_modes(self):
        expected = {
            IMPLEMENT_NOT_ORCHESTRATE,
            STDLIB_LEAK,
            PATH_PREFIX,
            JUPYTER_CONFUSION,
            SYNTAX_ARTIFACT,
            KEY_HALLUCINATION,
            WRONG_OUTPUT,
        }
        assert ALL_MODES == expected

    def test_constants_are_lowercase_snake_case(self):
        for mode in ALL_MODES:
            assert mode == mode.lower()
            assert " " not in mode
            assert mode.replace("_", "").isalpha()


class TestHintMap:
    def test_every_mode_has_a_hint(self):
        """Every failure mode must have a corresponding prompt hint."""
        for mode in ALL_MODES:
            assert mode in HINT_MAP, f"Missing hint for {mode}"

    def test_no_extra_hints(self):
        """Hint map should not contain entries for unknown modes."""
        for key in HINT_MAP:
            assert key in ALL_MODES, f"Unknown mode in HINT_MAP: {key}"

    def test_hints_have_required_fields(self):
        for mode, hint in HINT_MAP.items():
            assert "type" in hint, f"{mode} hint missing 'type'"
            assert "content" in hint, f"{mode} hint missing 'content'"
            assert hint["type"] in (
                "negative_constraint", "positive_example", "instruction",
            ), f"{mode} has unknown hint type: {hint['type']}"

    def test_hint_content_is_nonempty(self):
        for mode, hint in HINT_MAP.items():
            assert len(hint["content"]) > 10, f"{mode} hint content too short"


class TestLackpyParity:
    """Verify kibitzer's taxonomy matches lackpy's canonical definition."""

    def test_mode_strings_match_lackpy(self):
        """These exact strings are used by lackpy's classify_failure()."""
        expected_strings = {
            "implement_not_orchestrate",
            "stdlib_leak",
            "path_prefix",
            "jupyter_confusion",
            "syntax_artifact",
            "key_hallucination",
            "wrong_output",
        }
        assert ALL_MODES == expected_strings
