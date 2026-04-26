"""KibitzerSession — the Python API for kibitzer."""

from __future__ import annotations

import json
from contextlib import contextmanager
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
_UNSET = object()  # sentinel for "use session default"


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
        namespace: str | None = None,
    ):
        self._project_dir = Path(project_dir) if project_dir else Path.cwd()
        self._safe_mode = safe_mode
        self._namespace: str | None = namespace
        self._config: dict = {}
        self._state: dict = {}
        self._store: KibitzerStore | None = None
        self._interceptors: list | None = None
        self._available_tools: dict | None = None
        self._registered_tools: dict[str, tuple[int, int]] = {}
        self._context: dict[str, Any] = {}
        self._doc_registry: dict[str | None, dict] = {}
        self._policy_consumer = None
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
        self._policy_consumer = self._load_policy_consumer()
        self._load_docs_from_config()
        self._loaded = True

    def _load_policy_consumer(self):
        """Try to load a PolicyConsumer from the compiled policy database."""
        policy_db = self._project_dir / ".kibitzer" / "policy.db"
        if not policy_db.exists():
            return None
        try:
            from kibitzer.umwelt.consumer import PolicyConsumer
            return PolicyConsumer.from_db(policy_db)
        except Exception:
            return None

    def _load_docs_from_config(self) -> None:
        """Auto-register docs from the [docs] config section."""
        docs_cfg = self._config.get("docs", {})
        refs = docs_cfg.get("refs")
        if not refs:
            return
        root = docs_cfg.get("root")
        if root:
            root = str(self._project_dir / root)
        self.register_docs(doc_refs=refs, docs_root=root)

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
        return self._resolve_mode_policy(self.mode).get("writable", ["*"])

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

    @property
    def policy_consumer(self):
        """PolicyConsumer when umwelt policy is loaded, else None."""
        return self._policy_consumer

    @property
    def namespace(self) -> str | None:
        return self._namespace

    @property
    def doc_refs(self) -> dict[str, str]:
        ns = self._resolve_namespace()
        entry = self._doc_registry.get(ns, {})
        return entry.get("refs", {})

    def doc_refs_for(self, namespace: str) -> dict[str, str]:
        entry = self._doc_registry.get(namespace, {})
        return entry.get("refs", {})

    @contextmanager
    def ns(self, namespace: str):
        """Temporarily switch the session namespace."""
        previous = self._namespace
        self._namespace = namespace
        try:
            yield self
        finally:
            self._namespace = previous

    def _resolve_namespace(self, explicit=_UNSET) -> str | None:
        if explicit is not _UNSET:
            return explicit
        return self._namespace

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
        mode_policy = self._resolve_mode_policy(self.mode)
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
        available = self._available_modes()
        if mode not in available:
            return {
                "error": (
                    f"Unknown mode: {mode}. Available: {list(available)}"
                )
            }

        previous = self.mode

        self._state["previous_mode"] = previous
        self._state["turns_in_previous_mode"] = self._state.get("turns_in_mode", 0)
        self._state["mode"] = mode
        self._state["failure_count"] = 0
        self._state["success_count"] = 0
        self._state["consecutive_failures"] = 0
        self._state["turns_in_mode"] = 0
        self._state["mode_switches"] = self._state.get("mode_switches", 0) + 1
        self._state["tools_used_in_mode"] = {}

        if self._policy_consumer is not None:
            self._policy_consumer.invalidate_cache()

        policy = self._resolve_mode_policy(mode)

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
            "writable": policy.get("writable", ["*"]),
            "strategy": policy.get("strategy", ""),
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
            policy = self._resolve_mode_policy(self.mode)
            result["status"] = {
                "mode": self.mode,
                "failure_count": self._state["failure_count"],
                "success_count": self._state["success_count"],
                "consecutive_failures": self._state["consecutive_failures"],
                "turns_in_mode": self._state["turns_in_mode"],
                "total_calls": self._state["total_calls"],
                "writable": policy.get("writable", ["*"]),
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

    def register_docs(
        self,
        doc_refs: dict[str, str | None],
        docs_root: str | None = None,
        namespace=_UNSET,
        refinement=None,
    ) -> None:
        """Register tool documentation references for contextual search.

        Args:
            doc_refs: tool name -> relative doc path (from lackpy docs_index).
            docs_root: Root directory to resolve relative paths.
            namespace: Namespace to register under (defaults to session namespace).
            refinement: Default DocRefinement for this namespace's docs.
        """
        ns = self._resolve_namespace(namespace)
        self._doc_registry[ns] = {
            "refs": {k: v for k, v in doc_refs.items() if v},
            "root": docs_root,
            "refinement": refinement,
        }

    def get_doc_context(
        self,
        query: str,
        tool: str | None = None,
        failure_mode: str | None = None,
        namespace=_UNSET,
        refinement=None,
        limit: int = 5,
    ):
        """Search tool docs for context relevant to a query.

        Pipeline: local docs (pluckit) → Context7 fallback → select → present.

        Returns DocResult with matching sections. Falls back to Context7
        for external library docs when local docs have no results.
        """
        from kibitzer.docs import DocResult

        ns = self._resolve_namespace(namespace)
        registry = self._doc_registry.get(ns)

        # Step 1: RETRIEVE — local docs first
        candidates = []
        if registry:
            candidates = self._retrieve_doc_sections(query, registry, tool=tool)

        # Step 1b: FALLBACK — Context7 for external library docs
        if not candidates and self._config.get("docs", {}).get("context7", True):
            candidates = self._retrieve_from_context7(query)

        if not candidates:
            return DocResult()

        # Use registered refinement as default, allow override
        effective_refinement = refinement or (
            registry.get("refinement") if registry else None
        )

        # Step 2: SELECT (callback or default top-N)
        context = {
            "query": query, "tool": tool,
            "failure_mode": failure_mode, "namespace": ns,
        }
        if effective_refinement and effective_refinement.select:
            try:
                candidates = effective_refinement.select(candidates, context)
            except Exception:
                pass

        candidates = candidates[:limit]

        # Step 3: PRESENT (callback or raw)
        if effective_refinement and effective_refinement.present:
            try:
                candidates = effective_refinement.present(candidates, context)
            except Exception:
                pass

        return DocResult(sections=candidates)

    def _retrieve_doc_sections(self, query, registry, tool=None):
        from kibitzer.docs import DocSection
        try:
            from pluckit import Plucker
        except ImportError:
            return []

        docs_root = registry.get("root")
        if not docs_root:
            return []

        try:
            p = Plucker(docs=f"{docs_root}/**/*.md")
            docs = p.docs()

            if tool:
                doc_path = registry["refs"].get(tool)
                if doc_path:
                    docs = docs.filter(file_path=doc_path)

            if query:
                # ILIKE needs exact substring — search each word separately
                # to handle multi-word queries like "read file"
                words = query.split()
                if len(words) == 1:
                    docs = docs.filter(search=words[0])
                else:
                    # Try full query first, fall back to longest word
                    full = docs.filter(search=query)
                    if full.sections():
                        docs = full
                    else:
                        longest = max(words, key=len)
                        docs = docs.filter(search=longest)

            raw_sections = docs.sections()
        except Exception:
            return []

        return [
            DocSection(
                title=s.get("title", ""),
                content=str(s.get("content", "")),
                file_path=s.get("file_path", ""),
                level=s.get("level", 1),
                tool=tool,
            )
            for s in raw_sections
        ]

    def _retrieve_from_context7(self, query: str) -> list:
        """Fallback: search Context7 for external library documentation."""
        from kibitzer.docs import DocSection
        try:
            from kibitzer.context7 import query_docs
        except ImportError:
            return []

        # Extract a library name from the query — use the first
        # recognizable token (heuristic: longest word that looks
        # like a package name)
        words = query.split()
        if not words:
            return []
        library_name = max(words, key=len)

        try:
            results = query_docs(library_name, query, max_tokens=1500)
        except Exception:
            return []

        return [
            DocSection(
                title=r.get("title", ""),
                content=r.get("content", ""),
                file_path=r.get("source", "context7"),
                level=1,
                tool=None,
            )
            for r in results
            if r.get("content")
        ]

    def report_generation(
        self, report: dict[str, Any], namespace=_UNSET,
    ) -> None:
        """Record a lackpy generation outcome in the event log.

        Expected fields (all optional, but richer reports enable better hints):
            intent, program, provider, correction_attempts, success,
            failure_mode, model, interpreter, prompt_variant
        """
        ns = self._resolve_namespace(namespace)
        if ns is not None and "namespace" not in report:
            report = {**report, "namespace": ns}
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
        namespace=_UNSET,
    ) -> list[dict[str, Any]]:
        """Aggregate failure modes from recent generation events.

        Returns list of dicts sorted by count descending:
            [{"pattern": str, "model": str, "count": int, "last_seen": str,
              "sample_intent": str}, ...]
        """
        if not self._store:
            return []

        ns = self._resolve_namespace(namespace)
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

            if ns is not None and data.get("namespace") != ns:
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
        namespace=_UNSET,
    ) -> list[dict[str, Any]]:
        """Return structured prompt hints derived from observed failure patterns.

        Returns list of hint dicts:
            [{"type": "negative_constraint"|"positive_example",
              "content": str,
              "confidence": float,   # fraction of recent generations with this failure
              "source": str}, ...]
        """
        ns = self._resolve_namespace(namespace)
        patterns = self.get_failure_patterns(
            model=model, window=window, namespace=ns,
        )
        if not patterns:
            return []

        # Count total generations in window for confidence calculation
        total_generations = 0
        if self._store:
            events = self._store.query_events(
                event_type="generation", limit=window,
            )
            for e in events:
                try:
                    d = json.loads(e.get("data", "{}"))
                    if ns is not None and d.get("namespace") != ns:
                        continue
                    if model and d.get("model") != model:
                        continue
                    total_generations += 1
                except (json.JSONDecodeError, TypeError):
                    continue

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
        tool: str | None = None,
        namespace=_UNSET,
    ) -> dict[str, Any]:
        """Return correction signal for a failed generation.

        Returns structured data — not prompt text. Lackpy's correction
        chain decides how to turn this into prompt language.

        Args:
            failure_mode: The classified failure (from failure_modes taxonomy).
            model: The model that failed (for historical pattern lookup).
            attempt: Which correction attempt this is (1 = first retry).
            tool: The tool that was misused (for doc context lookup).
            namespace: Namespace to scope pattern lookup and doc retrieval.

        Returns:
            Signal dict with failure_mode, known, attempt, escalation_level,
            history, and optionally doc_context.
        """
        from kibitzer.failure_modes import ALL_MODES

        ns = self._resolve_namespace(namespace)
        clamped = min(attempt, MAX_ESCALATION)

        result: dict[str, Any] = {
            "failure_mode": failure_mode,
            "known": failure_mode in ALL_MODES,
            "attempt": attempt,
            "escalation_level": clamped,
            "history": None,
        }

        if model:
            patterns = self.get_failure_patterns(
                model=model, window=20, namespace=ns,
            )
            for pattern in patterns:
                if pattern["pattern"] == failure_mode:
                    total = 0
                    if self._store:
                        events = self._store.query_events(
                            event_type="generation", limit=20,
                        )
                        for e in events:
                            try:
                                d = json.loads(e.get("data", "{}"))
                                if ns is not None and d.get("namespace") != ns:
                                    continue
                                if d.get("model") == model:
                                    total += 1
                            except (json.JSONDecodeError, TypeError):
                                continue
                    result["history"] = {
                        "count": pattern["count"],
                        "total": total,
                    }
                    break

        # Include doc context if docs are registered
        effective_ns = ns if ns in self._doc_registry else None
        if effective_ns in self._doc_registry:
            try:
                query = tool or failure_mode.replace("_", " ")
                doc_result = self.get_doc_context(
                    query=query,
                    tool=tool,
                    failure_mode=failure_mode,
                    namespace=ns,
                    limit=3,
                )
                if doc_result.sections:
                    result["doc_context"] = [
                        {"title": s.title, "content": s.content,
                         "file": s.file_path}
                        for s in doc_result.sections
                    ]
            except Exception:
                pass

        return result

    def get_mode_policy(self) -> dict[str, Any]:
        """Expose current mode constraints for grade-aware tool selection.

        When a PolicyConsumer is available, queries it for richer mode
        data (coaching frequency, transition thresholds). Falls back to
        the config dict.
        """
        resolved = self._resolve_mode_policy(self.mode)
        result: dict[str, Any] = {
            "mode": self.mode,
            "writable": resolved.get("writable", []),
            "strategy": resolved.get("strategy", ""),
        }
        for key in (
            "coaching_frequency", "max_consecutive_failures",
            "max_turns", "max_grade_w", "max_grade_d",
        ):
            if key in resolved:
                result[key] = resolved[key]
        return result

    # --- Internal ---

    def _available_modes(self) -> set[str]:
        """Modes known to config and/or PolicyConsumer."""
        modes = set(self._config.get("modes", {}).keys())
        if self._policy_consumer is not None:
            modes.update(self._policy_consumer.list_modes())
        return modes

    def _resolve_mode_policy(self, mode: str) -> dict[str, Any]:
        """Single source of truth for mode policy resolution.

        Prefers PolicyConsumer (umwelt) when available, falls back to
        config dict. All internal callers should use this instead of
        get_mode_policy(self._config, mode) directly.
        """
        if self._policy_consumer is not None:
            mp = self._policy_consumer.get_mode_policy(
                mode, active_mode=self.mode,
            )
            if mp is not None:
                result: dict[str, Any] = {
                    "writable": mp.writable,
                    "strategy": mp.strategy,
                }
                if mp.coaching_frequency is not None:
                    result["coaching_frequency"] = mp.coaching_frequency
                if mp.max_consecutive_failures is not None:
                    result["max_consecutive_failures"] = (
                        mp.max_consecutive_failures
                    )
                if mp.max_turns is not None:
                    result["max_turns"] = mp.max_turns
                return result
        return get_mode_policy(self._config, mode)

    def _before_call_impl(
        self, tool_name: str, tool_input: dict,
    ) -> CallResult | None:
        mode_policy = self._resolve_mode_policy(self.mode)

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

        resolved = self._resolve_mode_policy(self.mode)
        transition = check_transitions(
            self._state, self._config,
            max_consecutive_failures=resolved.get("max_consecutive_failures"),
            max_turns=resolved.get("max_turns"),
        )
        if transition is not None:
            if self._policy_consumer is not None:
                self._policy_consumer.invalidate_cache()
            apply_transition(self._state, transition)
            messages.append(
                f"[kibitzer] Mode switched to {transition.target}: "
                f"{transition.reason}"
            )

        if should_fire(
            self._state, self._config,
            coaching_frequency=resolved.get("coaching_frequency"),
        ):
            suggestions = generate_suggestions(
                self._state, project_dir=self._project_dir,
            )
            for s in suggestions:
                messages.append(f"[kibitzer] {s}")

        if not success and self._doc_registry:
            doc_hint = self._doc_hint_for_failure(tool_name, tool_result)
            if doc_hint:
                messages.append(doc_hint)

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

    def _doc_hint_for_failure(
        self, tool_name: str, tool_result: Any,
    ) -> str | None:
        """Query registered docs for context relevant to a tool failure.

        Returns a formatted hint string, or None if no docs match.
        """
        error_text = self._extract_error_text(tool_result)
        query = error_text or tool_name
        try:
            doc_result = self.get_doc_context(
                query=query, tool=tool_name, limit=2,
            )
        except Exception:
            return None
        if not doc_result.sections:
            return None
        parts = [f"[kibitzer] Relevant docs for {tool_name}:"]
        for s in doc_result.sections:
            title = s.title or s.file_path
            content = s.content
            if len(content) > 300:
                content = content[:297] + "..."
            parts.append(f"  {title}: {content}")
        return "\n".join(parts)

    @staticmethod
    def _extract_error_text(tool_result: Any) -> str:
        """Pull error text from a tool result for doc search."""
        if isinstance(tool_result, dict):
            if "error" in tool_result:
                return str(tool_result["error"])[:200]
            if "stderr" in tool_result:
                stderr = str(tool_result["stderr"])
                return stderr[-200:] if len(stderr) > 200 else stderr
        if isinstance(tool_result, str) and len(tool_result) < 500:
            return tool_result
        return ""

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
