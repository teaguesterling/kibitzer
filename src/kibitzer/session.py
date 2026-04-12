"""KibitzerSession — the Python API for kibitzer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kibitzer.coach.suggestions import generate_suggestions, should_fire
from kibitzer.coach.tools import discover_tools
from kibitzer.config import get_mode_policy, load_config
from kibitzer.failure_modes import HINT_MAP as _FAILURE_HINT_MAP, MAX_ESCALATION
from kibitzer.controller.mode_controller import (
    apply_transition,
    check_transitions,
    update_counters,
)
from kibitzer.guards.path_guard import check_path
from kibitzer.interceptors.base import InterceptMode
from kibitzer.interceptors.registry import build_registry
from kibitzer.state import load_state, save_state
from kibitzer.store import KibitzerStore

_WRITE_TOOLS = {"Edit", "Write", "NotebookEdit"}
_INTERCEPT_TOOLS = {"Bash"}
_LOG_FILE = ".kibitzer/intercept.log"


@dataclass
class CallResult:
    """Result of a before_call, after_call, or validate_calls check."""
    denied: bool = False
    reason: str = ""
    context: str = ""
    tool: str = ""

    def to_hook_output(self, hook_event: str = "PreToolUse") -> dict:
        """Convert to Claude Code hook JSON protocol."""
        if self.denied:
            return {
                "hookSpecificOutput": {
                    "hookEventName": hook_event,
                    "permissionDecision": "deny",
                    "permissionDecisionReason": self.reason,
                }
            }
        if self.context:
            return {
                "hookSpecificOutput": {
                    "hookEventName": hook_event,
                    "additionalContext": self.context,
                }
            }
        return {}


class KibitzerSession:
    """The Python API for kibitzer.

    Use as a context manager for automatic load/save:
        with KibitzerSession(project_dir=".") as session:
            result = session.before_call("Edit", {"file_path": "src/foo.py"})

    Or manage lifecycle manually:
        session = KibitzerSession()
        session.load()
        ...
        session.save()
    """

    def __init__(
        self,
        project_dir: str | Path | None = None,
        safe_mode: bool = False,
    ):
        self._project_dir = Path(project_dir) if project_dir else Path.cwd()
        self._safe_mode = safe_mode
        self._config: dict = {}
        self._state: dict = {}
        self._store: KibitzerStore | None = None
        self._interceptors: list | None = None
        self._available_tools: dict | None = None
        self._registered_tools: dict[str, tuple[int, int]] = {}
        self._context: dict[str, Any] = {}
        self._loaded = False

    def __enter__(self):
        self.load()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self._record_error(exc_type, exc_val)
        try:
            self.save()
        except Exception:
            if exc_type is None:
                raise
        return False

    def load(self) -> None:
        """Load config and state from disk. Initialize SQLite store."""
        self._config = load_config(self._project_dir)
        state_dir = self._project_dir / ".kibitzer"
        self._state = load_state(state_dir)
        store_path = state_dir / "store.sqlite"
        self._store = KibitzerStore(store_path)
        try:
            self._store.init()
        except Exception:
            self._store = None  # degrade gracefully
        self._loaded = True

    def save(self) -> None:
        """Persist state to disk."""
        state_dir = self._project_dir / ".kibitzer"
        save_state(self._state, state_dir)

    # --- Properties ---

    @property
    def mode(self) -> str:
        return self._state.get("mode", "implement")

    @property
    def state(self) -> dict:
        return self._state

    @property
    def config(self) -> dict:
        return self._config

    @property
    def writable(self) -> list[str]:
        policy = get_mode_policy(self._config, self.mode)
        return policy.get("writable", ["*"])

    @property
    def path_guard(self):
        from kibitzer.guards import path_guard as _pg
        return _pg

    @property
    def coach(self):
        from kibitzer.coach import observer as _obs
        from kibitzer.coach import suggestions as _sug
        return type("Coach", (), {
            "detect_patterns": staticmethod(_obs.detect_patterns),
            "generate_suggestions": staticmethod(_sug.generate_suggestions),
            "should_fire": staticmethod(_sug.should_fire),
        })()

    @property
    def controller(self):
        from kibitzer.controller import mode_controller as _mc
        return _mc

    @property
    def interceptors(self) -> list:
        if self._interceptors is None:
            self._interceptors = build_registry()
        return self._interceptors

    @property
    def available_tools(self) -> dict:
        if self._available_tools is None:
            self._available_tools = discover_tools(self._project_dir)
        return self._available_tools

    @property
    def registered_tools(self) -> dict[str, tuple[int, int]]:
        return self._registered_tools

    @property
    def context(self) -> dict[str, Any]:
        return self._context

    # --- Core API ---

    def before_call(
        self,
        tool_name: str,
        tool_input: dict[str, Any] | None = None,
    ) -> CallResult | None:
        """Pre-execution check: path guard + interceptors."""
        if self._safe_mode:
            try:
                return self._before_call_impl(tool_name, tool_input or {})
            except Exception:
                return None
        return self._before_call_impl(tool_name, tool_input or {})

    def after_call(
        self,
        tool_name: str,
        tool_input: dict[str, Any] | None = None,
        success: bool | None = None,
        tool_result: Any = None,
    ) -> CallResult | None:
        """Post-execution: update counters, check transitions, run coach."""
        if self._safe_mode:
            try:
                return self._after_call_impl(
                    tool_name, tool_input or {}, success, tool_result,
                )
            except Exception:
                return None
        return self._after_call_impl(
            tool_name, tool_input or {}, success, tool_result,
        )

    def validate_calls(self, calls: list[dict]) -> list[CallResult]:
        """Batch validation — check calls without updating state."""
        violations = []
        mode_policy = get_mode_policy(self._config, self.mode)
        for call in calls:
            tool = call.get("tool", "")
            inp = call.get("input", {})
            if tool in _WRITE_TOOLS:
                file_path = (
                    inp.get("file_path", "") or inp.get("notebook_path", "")
                )
                if file_path:
                    file_path = self._relativize(file_path)
                    result = check_path(file_path, mode_policy)
                    if not result.allowed:
                        violations.append(
                            CallResult(denied=True, reason=result.reason, tool=tool)
                        )
        return violations

    def change_mode(self, mode: str, reason: str = "") -> dict[str, Any]:
        """Switch mode. Returns new mode info or error."""
        if mode not in self._config.get("modes", {}):
            return {
                "error": (
                    f"Unknown mode: {mode}. "
                    f"Available: {list(self._config['modes'].keys())}"
                )
            }

        previous = self.mode
        policy = get_mode_policy(self._config, mode)

        self._state["previous_mode"] = previous
        self._state["turns_in_previous_mode"] = self._state.get("turns_in_mode", 0)
        self._state["mode"] = mode
        self._state["failure_count"] = 0
        self._state["success_count"] = 0
        self._state["consecutive_failures"] = 0
        self._state["turns_in_mode"] = 0
        self._state["mode_switches"] = self._state.get("mode_switches", 0) + 1
        self._state["tools_used_in_mode"] = {}

        if self._store:
            self._store.append_event(
                event_type="mode_switch",
                session_id=self._state.get("session_id"),
                mode=mode,
                data=json.dumps({"previous": previous, "reason": reason}),
            )

        return {
            "previous_mode": previous,
            "new_mode": mode,
            "writable": policy["writable"],
            "strategy": policy["strategy"],
        }

    def get_suggestions(self, mark_given: bool = True) -> list[str]:
        """Get coaching suggestions."""
        return generate_suggestions(
            self._state, project_dir=self._project_dir, mark_given=mark_given,
        )

    def get_feedback(
        self,
        status: bool = True,
        suggestions: bool = True,
        intercepts: bool = True,
    ) -> dict[str, Any]:
        """Combined feedback — status, suggestions, intercepts."""
        result: dict[str, Any] = {}

        if status:
            policy = get_mode_policy(self._config, self.mode)
            result["status"] = {
                "mode": self.mode,
                "failure_count": self._state["failure_count"],
                "success_count": self._state["success_count"],
                "consecutive_failures": self._state["consecutive_failures"],
                "turns_in_mode": self._state["turns_in_mode"],
                "total_calls": self._state["total_calls"],
                "writable": policy["writable"],
            }

        if suggestions:
            result["suggestions"] = self.get_suggestions(mark_given=False)

        if intercepts:
            result["intercepts"] = self._read_intercept_log()

        return result

    # --- Lackpy integration ---

    def register_tools(self, tools: list[dict[str, Any]]) -> None:
        """Register tools with their grades. Session-memory only."""
        for tool in tools:
            name = tool["name"]
            grade = tool.get("grade", (0, 0))
            if isinstance(grade, (list, tuple)):
                grade = tuple(grade)
            self._registered_tools[name] = grade

    def validate_program(self, program_info: dict[str, Any]) -> CallResult:
        """Program-level validation: grade ceiling, call budget, path violations."""
        calls = program_info.get("calls", [])
        grade_ceiling = program_info.get("grade_ceiling")
        call_budget = program_info.get("call_budget")

        if call_budget is not None and len(calls) > call_budget:
            return CallResult(
                denied=True,
                reason=f"Call budget exceeded: {len(calls)} calls > budget of {call_budget}",
            )

        if grade_ceiling is not None and self._registered_tools:
            ceiling_w, ceiling_d = grade_ceiling
            for call in calls:
                tool_name = call.get("tool", "")
                grade = self._registered_tools.get(tool_name)
                if grade and (grade[0] > ceiling_w or grade[1] > ceiling_d):
                    return CallResult(
                        denied=True,
                        reason=(
                            f"Tool '{tool_name}' grade {grade} exceeds "
                            f"ceiling {grade_ceiling}"
                        ),
                    )

        violations = self.validate_calls(calls)
        if violations:
            return violations[0]

        return CallResult(denied=False)

    def register_context(self, context: dict[str, Any]) -> None:
        """Set task context for coach-aware suggestions."""
        self._context = context

    def report_generation(self, report: dict[str, Any]) -> None:
        """Record a lackpy generation outcome in the event log.

        Expected fields (all optional, but richer reports enable better hints):
            intent, program, provider, correction_attempts, success,
            failure_mode, model, interpreter, prompt_variant
        """
        if self._store:
            self._store.append_event(
                event_type="generation",
                session_id=self._state.get("session_id"),
                tool_name=report.get("model"),
                success=report.get("success"),
                data=json.dumps(report),
            )

    def get_failure_patterns(
        self,
        model: str | None = None,
        window: int = 50,
    ) -> list[dict[str, Any]]:
        """Aggregate failure modes from recent generation events.

        Returns list of dicts sorted by count descending:
            [{"pattern": str, "model": str, "count": int, "last_seen": str,
              "sample_intent": str}, ...]
        """
        if not self._store:
            return []

        events = self._store.query_events(event_type="generation", limit=window)
        # Aggregate failure_mode from the data JSON
        pattern_counts: dict[tuple[str, str], dict[str, Any]] = {}
        for event in events:
            data_str = event.get("data", "")
            if not data_str:
                continue
            try:
                data = json.loads(data_str)
            except (json.JSONDecodeError, TypeError):
                continue

            failure_mode = data.get("failure_mode")
            if not failure_mode:
                continue

            event_model = data.get("model", "unknown")
            if model is not None and event_model != model:
                continue

            key = (failure_mode, event_model)
            if key not in pattern_counts:
                pattern_counts[key] = {
                    "pattern": failure_mode,
                    "model": event_model,
                    "count": 0,
                    "last_seen": event.get("timestamp", ""),
                    "sample_intent": data.get("intent", ""),
                }
            pattern_counts[key]["count"] += 1
            # Keep the most recent timestamp
            ts = event.get("timestamp", "")
            if ts > pattern_counts[key]["last_seen"]:
                pattern_counts[key]["last_seen"] = ts

        result = sorted(
            pattern_counts.values(), key=lambda x: x["count"], reverse=True,
        )
        return result

    def get_prompt_hints(
        self,
        model: str | None = None,
        window: int = 50,
        min_confidence: float = 0.3,
    ) -> list[dict[str, Any]]:
        """Return structured prompt hints derived from observed failure patterns.

        Returns list of hint dicts:
            [{"type": "negative_constraint"|"positive_example",
              "content": str,
              "confidence": float,   # fraction of recent generations with this failure
              "source": str}, ...]
        """
        patterns = self.get_failure_patterns(model=model, window=window)
        if not patterns:
            return []

        # Count total generations in window for confidence calculation
        total_generations = 0
        if self._store:
            events = self._store.query_events(
                event_type="generation", limit=window,
            )
            if model:
                for e in events:
                    try:
                        d = json.loads(e.get("data", "{}"))
                        if d.get("model") == model:
                            total_generations += 1
                    except (json.JSONDecodeError, TypeError):
                        continue
            else:
                total_generations = len(events)

        if total_generations == 0:
            return []

        hints = []
        for pattern in patterns:
            confidence = pattern["count"] / total_generations
            if confidence < min_confidence:
                continue

            # Use known hint mapping if available, else generate generic hint
            known = _FAILURE_HINT_MAP.get(pattern["pattern"])
            if known:
                hints.append({
                    "type": known["type"],
                    "content": known["content"],
                    "confidence": round(confidence, 2),
                    "source": f"failure_pattern:{pattern['pattern']}",
                })
            else:
                # Generic hint for unknown failure modes
                hints.append({
                    "type": "negative_constraint",
                    "content": f"Avoid: {pattern['pattern'].replace('_', ' ')}",
                    "confidence": round(confidence, 2),
                    "source": f"failure_pattern:{pattern['pattern']}",
                })

        return hints

    def get_correction_hints(
        self,
        failure_mode: str,
        model: str | None = None,
        attempt: int = 1,
    ) -> dict[str, Any]:
        """Return correction signal for a failed generation.

        Returns structured data — not prompt text. Lackpy's correction
        chain decides how to turn this into prompt language.

        Args:
            failure_mode: The classified failure (from failure_modes taxonomy).
            model: The model that failed (for historical pattern lookup).
            attempt: Which correction attempt this is (1 = first retry).

        Returns:
            Signal dict:
                {
                    "failure_mode": str,
                    "known": bool,           # recognized in taxonomy
                    "attempt": int,
                    "escalation_level": int,  # 1=normal, 2=simplify, 3=minimal
                    "history": {              # omitted if no model or no history
                        "count": int,         # times this model hit this mode
                        "total": int,         # total generations in window
                    } | None,
                }
        """
        from kibitzer.failure_modes import ALL_MODES

        clamped = min(attempt, MAX_ESCALATION)

        result: dict[str, Any] = {
            "failure_mode": failure_mode,
            "known": failure_mode in ALL_MODES,
            "attempt": attempt,
            "escalation_level": clamped,
            "history": None,
        }

        if model:
            patterns = self.get_failure_patterns(model=model, window=20)
            for pattern in patterns:
                if pattern["pattern"] == failure_mode:
                    # Count total generations for this model in the window
                    total = 0
                    if self._store:
                        events = self._store.query_events(
                            event_type="generation", limit=20,
                        )
                        for e in events:
                            try:
                                d = json.loads(e.get("data", "{}"))
                                if d.get("model") == model:
                                    total += 1
                            except (json.JSONDecodeError, TypeError):
                                continue
                    result["history"] = {
                        "count": pattern["count"],
                        "total": total,
                    }
                    break

        return result

    def get_mode_policy(self) -> dict[str, Any]:
        """Expose current mode constraints for grade-aware tool selection."""
        policy = get_mode_policy(self._config, self.mode)
        result: dict[str, Any] = {
            "mode": self.mode,
            "writable": policy.get("writable", []),
            "strategy": policy.get("strategy", ""),
        }
        # Include grade ceiling if configured
        if "max_grade_w" in policy:
            result["max_grade_w"] = policy["max_grade_w"]
        if "max_grade_d" in policy:
            result["max_grade_d"] = policy["max_grade_d"]
        return result

    # --- Internal ---

    def _before_call_impl(
        self, tool_name: str, tool_input: dict,
    ) -> CallResult | None:
        mode_policy = get_mode_policy(self._config, self.mode)

        # Path guard
        if tool_name in _WRITE_TOOLS:
            file_path = (
                tool_input.get("file_path", "")
                or tool_input.get("notebook_path", "")
            )
            if file_path:
                file_path = self._relativize(file_path)
                result = check_path(file_path, mode_policy)
                if not result.allowed:
                    if self._store:
                        self._store.append_event(
                            event_type="denial",
                            session_id=self._state.get("session_id"),
                            tool_name=tool_name,
                            tool_input=json.dumps(tool_input)[:500],
                            mode=self.mode,
                            data=json.dumps({"reason": result.reason}),
                        )
                    return CallResult(
                        denied=True, reason=result.reason, tool=tool_name,
                    )

        # Interceptors
        if tool_name in _INTERCEPT_TOOLS:
            command = tool_input.get("command", "")
            if command:
                plugin_modes = {}
                for name, pcfg in self._config.get("plugins", {}).items():
                    if pcfg.get("enabled", True):
                        plugin_modes[name] = pcfg.get("mode", "observe")

                for plugin in self.interceptors:
                    if plugin.name not in plugin_modes:
                        continue
                    suggestion = plugin.check(command)
                    if suggestion is None:
                        continue

                    pmode = InterceptMode(
                        plugin_modes.get(plugin.name, "observe")
                    )

                    if pmode == InterceptMode.OBSERVE:
                        self._log_intercept(command, suggestion)
                        return None

                    if pmode == InterceptMode.SUGGEST:
                        return CallResult(
                            context=(
                                f"[kibitzer] {suggestion.plugin} suggests: "
                                f"{suggestion.tool}\n"
                                f"Reason: {suggestion.reason}"
                            ),
                            tool=tool_name,
                        )

                    if pmode == InterceptMode.REDIRECT:
                        return CallResult(
                            denied=True,
                            reason=(
                                f"A structured alternative is available: "
                                f"{suggestion.tool}\n{suggestion.reason}"
                            ),
                            tool=tool_name,
                        )

        return None

    def _after_call_impl(
        self,
        tool_name: str,
        tool_input: dict,
        success: bool | None,
        tool_result: Any,
    ) -> CallResult | None:
        if success is None:
            success = self._detect_success(tool_name, tool_result)

        update_counters(self._state, tool_name, success, tool_input=tool_input)

        messages = []

        transition = check_transitions(self._state, self._config)
        if transition is not None:
            apply_transition(self._state, transition)
            messages.append(
                f"[kibitzer] Mode switched to {transition.target}: "
                f"{transition.reason}"
            )

        if should_fire(self._state, self._config):
            suggestions = generate_suggestions(
                self._state, project_dir=self._project_dir,
            )
            for s in suggestions:
                messages.append(f"[kibitzer] {s}")

        # Append to SQLite store
        if self._store:
            event_data = None
            if self._context:
                event_data = json.dumps({"context": self._context})
            self._store.append_event(
                event_type="tool_call",
                session_id=self._state.get("session_id"),
                tool_name=tool_name,
                tool_input=json.dumps(tool_input)[:500],
                success=success,
                mode=self.mode,
                data=event_data,
            )

        if messages:
            return CallResult(context="\n".join(messages), tool=tool_name)
        return None

    def _detect_success(self, tool_name: str, tool_result: Any) -> bool:
        if tool_name == "Bash" and isinstance(tool_result, dict):
            return tool_result.get("exitCode", 0) == 0
        if isinstance(tool_result, dict) and "error" in tool_result:
            return False
        return True

    def _relativize(self, file_path: str) -> str:
        try:
            fp = Path(file_path)
            if fp.is_absolute():
                return str(fp.relative_to(self._project_dir))
        except (ValueError, TypeError):
            pass
        return file_path

    def _log_intercept(self, command: str, suggestion: Any) -> None:
        log_path = self._project_dir / _LOG_FILE
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "bash_command": command[:200],
            "suggested_tool": suggestion.tool,
            "reason": suggestion.reason,
            "plugin": suggestion.plugin,
        }
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def _read_intercept_log(self) -> dict[str, Any]:
        log_path = self._project_dir / _LOG_FILE
        entries: list[dict] = []
        if log_path.exists():
            for line in log_path.read_text().strip().split("\n"):
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return {"total_observed": len(entries), "recent": entries[-10:]}

    def _record_error(self, exc_type: type, exc_val: BaseException) -> None:
        try:
            if self._store:
                self._store.append_event(
                    event_type="error",
                    session_id=self._state.get("session_id"),
                    data=json.dumps({
                        "type": str(exc_type.__name__),
                        "message": str(exc_val),
                    }),
                )
        except Exception:
            pass
