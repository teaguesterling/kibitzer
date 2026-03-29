"""Detect tool usage patterns from state, optionally enriched by fledgling queries."""

from __future__ import annotations

from pathlib import Path
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

# Mode sets for pattern applicability
_WRITABLE_MODES = {"implement", "test_dev", "create", "free"}
_READONLY_MODES = {"debug", "review"}


def detect_patterns(
    state: dict[str, Any],
    project_dir: Path | None = None,
) -> list[tuple[str, str]]:
    """Detect patterns in state. Returns list of (pattern_id, message) tuples.

    If project_dir is provided and fledgling is available, enriches detection
    with conversation analytics queries.
    """
    patterns = []
    tools = state.get("tools_used_in_mode", {})
    mode = state.get("mode", "implement")

    # Obs 1: Repeated edit failures on the same file
    # Only in writable modes — you shouldn't be editing in debug/review anyway
    if mode in _WRITABLE_MODES:
        if state.get("consecutive_edit_failures", 0) >= _CONSECUTIVE_EDIT_FAILURES_THRESHOLD:
            failed_file = state.get("last_failed_edit_file", "unknown")
            patterns.append((
                "repeated_edit_failure",
                f"Edit failed {state['consecutive_edit_failures']} times on {failed_file}. "
                f"The old_string may have wrong indentation. "
                f"Try Read({failed_file}) first to see the exact current content.",
            ))

    # Obs 2: Sequential file reads
    # Skip in debug/review — sequential reading IS the job there
    if mode not in _READONLY_MODES:
        if state.get("consecutive_reads", 0) >= _CONSECUTIVE_READS_THRESHOLD:
            n = state["consecutive_reads"]
            patterns.append((
                "sequential_reads",
                f"You've read {n} files one at a time. "
                "Consider using FindDefinitions or CodeStructure to get an overview in one call.",
            ))

    # Obs 3: Edit streak without tests
    # Only in code-editing modes — not debug/review (read-only) or document (no tests needed)
    if mode in _WRITABLE_MODES and mode != "document":
        edits_since_test = state.get("edits_since_test", 0)
        if edits_since_test > _EDITS_SINCE_TEST_THRESHOLD:
            patterns.append((
                "edit_without_test",
                f"You've made {edits_since_test} edits without running tests. "
                "Consider running tests to verify your changes.",
            ))

    # Obs 4: Ignoring available semantic tools
    # Relevant in any mode — searching is always valid
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

    # Obs 7: Analysis loop — reading without editing
    # Skip in debug/review — not editing is correct behavior there
    if mode not in _READONLY_MODES:
        turns_since_edit = state.get("total_calls", 0) - state.get("last_edit_turn", 0)
        if (turns_since_edit > _ANALYSIS_LOOP_THRESHOLD
                and state.get("total_calls", 0) > _ANALYSIS_LOOP_THRESHOLD):
            patterns.append((
                "analysis_loop",
                f"You've spent {turns_since_edit} turns reading without making changes. "
                "Consider starting with the most confident fix — you can verify with tests and adjust.",
            ))

    # High failure ratio — relevant in any mode
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

    # Oscillation — relevant in any mode
    if state.get("mode_switches", 0) > _OSCILLATION_THRESHOLD:
        patterns.append((
            "oscillation",
            "Frequent mode switches. Consider using free mode for this task.",
        ))

    # Mode mismatch: editing in debug mode — only in debug
    if mode == "debug" and tools.get("Edit", 0) > 0:
        patterns.append((
            "debug_mode_edits",
            "You're editing files in debug mode. "
            "Use ChangeToolMode to switch to implement mode first.",
        ))

    # --- Fledgling-enriched patterns ---
    # These only fire if fledgling is available. They query conversation
    # history for patterns that state.json alone can't detect.
    if project_dir is not None:
        patterns.extend(_detect_fledgling_patterns(state, project_dir))

    return patterns


def _detect_fledgling_patterns(
    state: dict[str, Any],
    project_dir: Path,
) -> list[tuple[str, str]]:
    """Detect patterns using fledgling conversation analytics."""
    from kibitzer.coach.fledgling import is_available, repeated_search_patterns, replaceable_bash_commands

    if not is_available(project_dir):
        return []

    patterns = []

    # Repeated search patterns — same grep/read pattern 3+ times
    repeated = repeated_search_patterns(project_dir)
    if repeated:
        top = repeated[0]
        pattern_str = top.get("pattern", "?")
        count = top.get("count", 3)
        tool = top.get("tool", "Grep")
        if len(pattern_str) > 60:
            pattern_str = pattern_str[:57] + "..."
        patterns.append((
            "fledgling_repeated_search",
            f"You've searched for '{pattern_str}' {count} times via {tool}. "
            "FindDefinitions or CodeStructure may find what you need in one call.",
        ))

    # Bash commands with structured replacements
    replaceable = replaceable_bash_commands(project_dir)
    if replaceable:
        top = replaceable[0]
        cmd = top.get("command", "?")
        alt = top.get("replaceable_by", "?")
        count = top.get("count", 1)
        if count >= 2:
            patterns.append((
                "fledgling_replaceable_bash",
                f"You've run '{cmd}' {count} times via Bash. "
                f"'{alt}' provides structured output for the same operation.",
            ))

    return patterns
