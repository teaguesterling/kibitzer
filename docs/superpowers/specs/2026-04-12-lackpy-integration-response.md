# Kibitzer ↔ Lackpy Integration — Response from the Kibitzer Side

*Response to `docs/superpowers/specs/2026-04-12-kibitzer-integration-prompt.md` in the lackpy repo.*

## Starting position

Kibitzer v0.2.1 has the four lackpy APIs implemented and tested: `register_tools`, `validate_program`, `register_context`, `report_generation`. The hooks pipeline (before_call → counters → patterns → suggestions) is stable. The event log (`store.sqlite`) is append-only and queryable. The coach currently has 15 pattern detectors, all state-counter-driven.

The discussion below is informed by a design conversation about **evolving kibitzer's mode system from explicit transitions to a side-effect-driven workflow engine**. That framing matters for several of the integration questions.

---

## Responses to the five questions

### 1. Is the before_call / after_call API stable?

**Yes, with a caveat.** The call-level API (`before_call`, `after_call`) is stable and won't change signatures. Lackpy can depend on it.

The caveat: the *return type* of `before_call` may grow. Currently it returns `CallResult(denied, reason)`. We're considering adding an `adjusted_input` field for cases where kibitzer wants to transparently fix a call rather than reject it (e.g., stripping a path prefix). This would be additive — existing callers that ignore the new field would still work.

**Recommendation:** Lackpy should already be checking `result.denied` before executing. If we add `result.adjusted_input`, lackpy should use it as the canonical input when present. This is the cleanest way to handle the interceptor patterns described in your question 2.

### 2. Failure pattern tracker across sessions

**Not yet, but the data is there.** The event log stores every generation report with intent, provider, success, correction metadata. What's missing is a query layer that aggregates failure modes per model.

This is the right shape for it:

```python
# Proposed: session.get_failure_patterns(model=None, window=50)
# Returns recent failure modes, optionally filtered by model
[
    {
        "pattern": "implement_not_orchestrate",
        "model": "qwen2.5-coder:3b",
        "count": 3,
        "last_seen": "2026-04-12T14:30:00",
        "sample_intent": "find all functions matching handle_*",
    },
    {
        "pattern": "stdlib_leak",
        "model": "smollm2:1.7b",
        "count": 5,
        "last_seen": "2026-04-12T14:25:00",
    },
]
```

**But** — kibitzer needs lackpy to classify the failure mode in `report_generation()`. Kibitzer sees tool call traces, not generated source code. It can't tell the difference between "model defined a function instead of calling a tool" and "model called the wrong tool." Lackpy's validator is the right place to classify the failure, then report it.

**Proposed contract:** Add a `failure_mode` field to `report_generation()`:

```python
session.report_generation({
    # ... existing fields ...
    "failure_mode": "implement_not_orchestrate",  # or "path_prefix", "stdlib_leak", None
    "model": "qwen2.5-coder:3b",
    "interpreter": "python",
    "prompt_variant": "specialized",
})
```

Kibitzer accumulates these. A new `get_failure_patterns()` method returns the aggregated view.

### 3. Structured prompt hints (not just string suggestions)

`get_suggestions()` returning strings is fine for the human-facing coach. For lackpy's prompt builder, we need something structured. Two options:

**Option A: Separate method**

```python
hints = session.get_prompt_hints(model="qwen2.5-coder:3b")
# Returns:
[
    {
        "type": "negative_constraint",
        "content": "Do NOT use open() — call read_file() instead",
        "confidence": 0.9,  # 5 of last 5 generations used open()
        "source": "failure_pattern:stdlib_leak",
    },
    {
        "type": "positive_example",
        "content": "result = read_file('app.py')",
        "confidence": 0.7,
        "source": "failure_pattern:implement_not_orchestrate",
    },
]
```

**Option B: Extend report_generation to accept and return hints**

The problem with a separate method is timing — lackpy needs hints *before* generation, but failure patterns are accumulated *after* generation. A separate method called before `generate()` is cleaner than trying to fold it into the post-generation report.

**Recommendation: Option A.** `get_prompt_hints(model=)` as a new method, called before generation. It reads the accumulated failure patterns and returns structured hints. Lackpy decides how to incorporate them into the prompt.

### 4. Who owns the model→prompt mapping?

**Lackpy should own the mapping. Kibitzer should own the signal.**

The reasoning:

- Lackpy knows what prompt variants exist, how interpreters work, and what the model's capabilities are. That's prompt authorship.
- Kibitzer knows what failed and how often. That's pattern observation.
- The eval harness produces static ground truth. That's calibration data.

The flow should be:

```
eval harness → baseline scores → lackpy's prompt selection defaults
kibitzer → live failure patterns → lackpy adjusts prompt at generation time
```

Kibitzer should NOT select prompts. It should report "this model keeps doing X" and let lackpy's prompt builder decide what to do about it. Kibitzer's `get_prompt_hints()` returns observations, not instructions.

This also preserves lackpy's ability to disagree with kibitzer. If kibitzer says "model X always uses open()" but lackpy knows the specialized prompt already handles that, lackpy can ignore the hint.

### 5. Integration testing

**Shared integration tests, living in lackpy's repo, with kibitzer as a test dependency.**

The dependency direction is lackpy → kibitzer, so the integration tests belong in lackpy. Kibitzer's own test suite has `test_session_lackpy.py` which tests the API contract from kibitzer's side using synthetic data. Lackpy's tests should exercise the real flow: generate → validate → execute → report → get_hints.

For CI, lackpy can pin a kibitzer version in its test dependencies. We should also have a cross-repo CI trigger — when kibitzer cuts a release, lackpy's integration tests run against it.

---

## Responses to the integration directions

### Direction 1: Kibitzer as prompt advisor — YES

This is the highest-value integration. The `get_prompt_hints()` method described above is the mechanism. The loop is:

1. Lackpy calls `get_prompt_hints(model=)` before generation
2. Lackpy incorporates hints into the prompt
3. Model generates
4. Lackpy calls `report_generation(failure_mode=, model=, ...)` 
5. Kibitzer accumulates, updates pattern counts
6. Next generation gets better hints

The eval data showing 3-5x improvement from the right prompt is the strongest argument for closing this loop dynamically.

**One constraint:** Kibitzer's hint confidence should reflect recency. A failure pattern from 50 generations ago is less relevant than one from the last 5. The aggregation should use a sliding window, not all-time counts.

### Direction 2: Interceptors for model mistakes — SPLIT

Three categories:

- **`open()` → `read_file()`**: This is a **kibitzer interceptor**. It's a tool-call-level redirection that applies regardless of what generated the call. Add it as a `before_call` adjustment (the `adjusted_input` field mentioned above). Kibitzer already has the interceptor plugin system for this.

- **Path prefix stripping (`toybox/app.py` → `app.py`)**: This is **lackpy's sanitization**. It requires knowing the project structure and the workspace root. Lackpy has this context; kibitzer doesn't. Lackpy should strip prefixes before the call reaches kibitzer.

- **FunctionDef detection**: This is **lackpy's validator**. Kibitzer never sees the AST — it sees tool calls. By the time kibitzer is involved, the program has already been validated. Lackpy's AST validator is the right boundary.

**Principle:** Kibitzer intercepts at the tool-call boundary. Anything that requires understanding the generated program's structure belongs in lackpy.

### Direction 3: Grade-aware interpreter selection — YES, via mode policy

Kibitzer already expresses mode constraints as writable paths. Extending this to grade ceilings is natural:

```toml
# kibitzer config
[modes.implement]
writable = ["src/", "lib/"]
max_grade_w = 3
max_grade_d = 3

[modes.review]
writable = []
max_grade_w = 1
max_grade_d = 0
```

Lackpy's interpreter selection can call `session.get_mode_policy()` (new method) and filter interpreters by grade compatibility. If the current mode caps `grade_w` at 1, lackpy knows to pick ast-select or plucker over the python interpreter.

This also feeds into the workflow engine direction: when kibitzer auto-transitions from review → implement, the grade ceiling rises, and lackpy's next delegation can use more powerful interpreters without being told explicitly.

### Direction 4: Generation outcome tracking — EXTEND report_generation

The additional fields (interpreter, prompt variant, specialized vs baseline) should go into `report_generation()`. No new API needed — just a richer report dict:

```python
session.report_generation({
    # Existing
    "intent": intent,
    "program": gen_result.program,
    "provider": gen_result.provider_name,
    "correction_attempts": attempts,
    "success": exec_result.success,
    # New
    "interpreter": "python",
    "prompt_variant": "specialized",
    "prompt_specialized": True,
    "model": "qwen2.5-coder:3b",
    "failure_mode": "stdlib_leak",  # or None on success
    "eval_score": 2,  # if eval harness involved, else omit
})
```

Kibitzer stores this in the event log. Agent-riggs or any analytics tool can query it via DuckDB.

### Direction 5: Coaching the correction chain — YES, via get_prompt_hints

This is actually the same mechanism as direction 1, called at a different point. When the correction chain retries:

```python
# In correction chain, after failure:
hints = session.get_prompt_hints(model=model_name)
# Feed hints into the fixer prompt
```

The hints are model-specific and recency-weighted, so the fixer gets "this model tends to use open()" rather than generic advice. This is more useful than the string suggestions from `get_suggestions()`.

---

## The workflow engine direction

Beyond these specific integration points, there's a broader architectural direction that affects how kibitzer and lackpy interact.

**Current state:** Kibitzer modes are explicit — the agent or user calls `ChangeToolMode`. Transitions are failure-driven (too many failures → debug mode) or manual.

**Proposed direction:** Kibitzer infers mode transitions from observed side effects. The agent (or lackpy) doesn't request a mode change — it tries to do something, and kibitzer either allows it (current mode permits it) or transitions to a mode that does.

For lackpy, this means:

1. **No mode management in lackpy's code.** Lackpy doesn't call `ChangeToolMode`. It calls `validate_program()` or `before_call()`, and kibitzer either allows the operation or transitions first.

2. **Declarable expected side effects.** Lackpy can declare what a delegation expects to do:
   ```python
   session.register_context({
       "source": "lackpy",
       "expected_effects": ["read"],  # or ["read", "write"]
       "intent": intent,
   })
   ```
   Kibitzer uses this to pre-validate that the current mode (or a valid transition target) supports those effects. If review mode doesn't allow writes and the delegation declares write effects, kibitzer can auto-transition to implement before execution begins — or reject if trust is low.

3. **Trust-gated autonomy.** Early in a session, kibitzer auto-transitions freely. As it observes problems (failed validations, corrections needed, test-editing-to-match-broken-impl patterns), it tightens — requiring explicit mode changes or refusing transitions. Lackpy doesn't need to know about the trust level; it just sees `validate_program()` start returning denials more often.

This is not a v0.3.0 feature — it's a direction. But the API additions proposed here (richer `register_context`, `get_prompt_hints`, extended `report_generation`) are all steps toward it. They give kibitzer the signal it needs to eventually make autonomous workflow decisions.

---

## Proposed API changes — summary

| Method | Status | Change |
|--------|--------|--------|
| `register_tools()` | Stable | No change |
| `validate_program()` | Stable | No change |
| `register_context()` | Stable | Add `expected_effects` field |
| `report_generation()` | Extend | Add `failure_mode`, `model`, `interpreter`, `prompt_variant` |
| `get_prompt_hints(model=)` | **New** | Structured hints from failure pattern aggregation |
| `get_failure_patterns(model=)` | **New** | Raw failure pattern data (lower level than hints) |
| `get_mode_policy()` | **New** | Expose current mode's constraints (writable, grade ceiling) |
| `before_call()` result | Extend | Add `adjusted_input` for transparent redirections |

**Priority order for implementation:**
1. `report_generation()` extensions — cheapest, enables everything else
2. `get_prompt_hints()` — highest value (closes the adaptive prompt loop)
3. `before_call()` adjusted_input — enables `open() → read_file()` interception
4. `get_mode_policy()` — enables grade-aware interpreter selection
5. `get_failure_patterns()` — raw data access for analytics

---

## Next steps

1. **Agree on `failure_mode` taxonomy.** Lackpy knows the failure modes from the eval (implement_not_orchestrate, path_prefix, stdlib_leak). We should formalize these as an enum or at least a documented set of strings that both projects recognize.

2. **Prototype `get_prompt_hints()`.** Kibitzer implements the aggregation; lackpy implements the prompt builder integration. Test with qwen2.5-coder:3b since it has the clearest failure patterns from the eval.

3. **Integration test in lackpy's repo.** Full cycle: register tools → get_prompt_hints → generate → validate → execute → report_generation → verify hints updated. This test runs against kibitzer as a real dependency, not mocked.
