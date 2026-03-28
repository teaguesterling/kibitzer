"""Detect tool usage patterns from state."""

from __future__ import annotations

from typing import Any

# Thresholds
_EDITS_SINCE_TEST_THRESHOLD = 4  # fire after 5th edit without test (> 4)
_FAILURE_RATIO_THRESHOLD = 0.5
_MIN_CALLS_FOR_RATIO = 5
_OSCILLATION_THRESHOLD = 4
_CONSECUTIVE_EDIT_FAILURES_THRESHOLD = 2
_CONSECUTIVE_READS_THRESHOLD = 3
_SEMANTIC_MIN_CALLS = 10
_SEMANTIC_MIN_SEARCHES = 5
_ANALYSIS_LOOP_THRESHOLD = 15


def detect_patterns(state: dict[str, Any]) -> list[tuple[str, str]]:
    """Detect patterns in state. Returns list of (pattern_id, message) tuples."""
    patterns = []
    tools = state.get("tools_used_in_mode", {})

    # Obs 1: Repeated edit failures on the same file
    if state.get("consecutive_edit_failures", 0) >= _CONSECUTIVE_EDIT_FAILURES_THRESHOLD:
        failed_file = state.get("last_failed_edit_file", "unknown")
        patterns.append((
            "repeated_edit_failure",
            f"Edit failed {state['consecutive_edit_failures']} times on {failed_file}. "
            f"The old_string may have wrong indentation. "
            f"Try Read({failed_file}) first to see the exact current content.",
        ))

    # Obs 2: Sequential file reads
    if state.get("consecutive_reads", 0) >= _CONSECUTIVE_READS_THRESHOLD:
        n = state["consecutive_reads"]
        patterns.append((
            "sequential_reads",
            f"You've read {n} files one at a time. "
            "Consider using FindDefinitions or CodeStructure to get an overview in one call.",
        ))

    # Obs 3: Edit streak without tests (improved — uses edits_since_test counter)
    edits_since_test = state.get("edits_since_test", 0)
    if edits_since_test > _EDITS_SINCE_TEST_THRESHOLD:
        patterns.append((
            "edit_without_test",
            f"You've made {edits_since_test} edits without running tests. "
            "Consider running tests to verify your changes.",
        ))

    # Obs 4: Ignoring available semantic tools
    search_count = (tools.get("Read", 0) + tools.get("Grep", 0) +
                    tools.get("Glob", 0) + tools.get("file_search", 0))
    if (state.get("total_calls", 0) > _SEMANTIC_MIN_CALLS
            and search_count >= _SEMANTIC_MIN_SEARCHES
            and not state.get("semantic_tools_used", False)):
        patterns.append((
            "semantic_underuse",
            "You've been searching through files manually. "
            "FindDefinitions shows all functions and classes across the codebase "
            "with their types and locations — one call instead of searching file by file.",
        ))

    # Obs 7: Analysis loop (Opus pattern) — reading without editing
    turns_since_edit = state.get("total_calls", 0) - state.get("last_edit_turn", 0)
    if (turns_since_edit > _ANALYSIS_LOOP_THRESHOLD
            and state.get("total_calls", 0) > _ANALYSIS_LOOP_THRESHOLD):
        patterns.append((
            "analysis_loop",
            f"You've spent {turns_since_edit} turns reading without making changes. "
            "Consider starting with the most confident fix — you can verify with tests and adjust.",
        ))

    # High failure ratio
    total = state.get("failure_count", 0) + state.get("success_count", 0)
    if total >= _MIN_CALLS_FOR_RATIO:
        ratio = state.get("failure_count", 0) / total
        if ratio > _FAILURE_RATIO_THRESHOLD:
            pct = int(ratio * 100)
            patterns.append((
                "high_failure_ratio",
                f"High failure rate ({pct}%). "
                "Consider stepping back to read before editing.",
            ))

    # Oscillation
    if state.get("mode_switches", 0) > _OSCILLATION_THRESHOLD:
        patterns.append((
            "oscillation",
            "Frequent mode switches. Consider using free mode for this task.",
        ))

    # Mode mismatch: editing in debug mode
    if state.get("mode") == "debug" and tools.get("Edit", 0) > 0:
        patterns.append((
            "debug_mode_edits",
            "You're editing files in debug mode. "
            "Use ChangeToolMode to switch to implement mode first.",
        ))

    return patterns
