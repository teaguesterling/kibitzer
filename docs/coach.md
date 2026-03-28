# Coach

The coach fires every N tool calls and checks for patterns that suggest the agent could work more effectively. It produces one-line suggestions injected as `additionalContext` in the PostToolUse hook response. The agent can read them and choose to act — or ignore them.

The coach never enforces. It never blocks. It just observes and comments.

## When it fires

The coach fires at a configurable frequency (default: every 5 calls). The frequency is model-dependent:

| Model | Frequency | Rationale |
|-------|-----------|-----------|
| Haiku | Every 3 calls | Needs more guidance — impulsive, benefits from frequent nudges |
| Sonnet | Every 5 calls | Balanced — enough context to form patterns, not too noisy |
| Opus | Every 10 calls | Less guidance — over-analyzes suggestions, action-oriented only |

Configure in `.kibitzer/config.toml`:

```toml
[coach]
frequency = 5
enabled = true

[coach.model_overrides]
haiku = { frequency = 3 }
sonnet = { frequency = 5 }
opus = { frequency = 10 }
```

## Patterns

Each pattern comes from experimental observations across ~250 runs. The priority order reflects impact:

### 1. Repeated edit failure (whitespace mismatch)

**What happens:** The agent calls Edit with an `old_string` that doesn't match the file content — usually correct logic but wrong indentation. It retries, fails, retries. Each cycle costs 2-3 turns.

**Detection:** 2+ consecutive Edit failures on the same file.

**Suggestion:**
```
[kibitzer] Edit failed 3 times on src/handler.py. The old_string may have
wrong indentation. Try Read(src/handler.py) first to see the exact current content.
```

**Why it matters:** In Haiku experiments, agents with 9+ failed edits exhausted their turn budget and failed the task. Agents with ≤3 failed edits recovered. Early intervention at the 2-failure mark prevents the spiral.

**Model dependency:** Critical for Haiku (generates bad old_strings frequently). Occasionally useful for Sonnet. Rare for Opus.

**Active in:** writable modes (implement, test_dev, create, free). Suppressed in debug/review where edits shouldn't happen.

---

### 2. Edit streak without testing

**What happens:** The agent makes multiple edits without running tests. It fixes what it thinks are all the bugs, then discovers some edits were wrong or introduced new failures.

**Detection:** 5+ edits (Edit or Write calls) since the last test run. A "test run" is any Bash call containing `pytest`, `npm test`, `cargo test`, `go test`, or `make test`.

**Suggestion:**
```
[kibitzer] You've made 7 edits without running tests. Consider running tests
to verify your changes.
```

**Why it matters:** Early testing catches cascading errors before they compound. The strategy instruction "understand before editing" addresses this partially, but some agents batch edits and only test at the end.

**Active in:** writable code modes (implement, test_dev, create, free). Suppressed in debug/review (can't edit) and document (docs don't need tests).

---

### 3. Ignoring semantic tools

**What happens:** The agent has FindDefinitions, CodeStructure, etc. available but never calls them. It uses Read and Grep to find code manually — slower and less precise.

**Detection:** 10+ total tool calls, 5+ search-like calls (Read, Grep, Glob), and zero semantic tool calls in the session.

**Suggestion:**
```
[kibitzer] You've been searching through files manually. FindDefinitions shows
all functions and classes across the codebase with their types and locations —
one call instead of searching file by file.
```

**Why it matters:** In Haiku experiments, the agent made ZERO semantic tool calls across all 5 runs when they were available. With the semantic-focused strategy instruction, it used FindDefinitions once per run and pass rate improved. The tool is invisible without either the strategy or the coach.

**Active in:** all modes. Searching is relevant everywhere.

---

### 4. Sequential file reads

**What happens:** The agent reads files one at a time — `Read("src/a.py")`, then `Read("src/b.py")`, then `Read("src/c.py")`. Each is a separate turn with full context re-send.

**Detection:** 3+ consecutive Read calls.

**Suggestion:**
```
[kibitzer] You've read 5 files one at a time. Consider using FindDefinitions
or CodeStructure to get an overview in one call.
```

**Why it matters:** In Sonnet experiments, sequential reads were a major cost driver — the round-trip overhead is why config A ($1.35) cost more than config E ($0.98). Batch reads or semantic tools eliminate the overhead.

**Active in:** writable modes. Suppressed in debug/review where sequential reading is expected behavior.

---

### 5. Analysis loop (Opus pattern)

**What happens:** The agent reads, searches, analyzes — but never edits. Turn count climbs. No Edit or Write calls after 15+ turns. The agent is understanding endlessly without acting.

**Detection:** 15+ turns since the last edit (or since session start if no edits yet).

**Suggestion:**
```
[kibitzer] You've spent 18 turns reading without making changes. Consider
starting with the most confident fix — you can verify with tests and adjust.
```

**Why it matters:** This is primarily an Opus pattern. The strategy instruction "understand before editing" can trigger it — Opus interprets "understand" as "analyze exhaustively." The coach compensates for a harmful instruction rather than removing the instruction (which helps Haiku and Sonnet).

**Active in:** writable modes (implement, test_dev, create, free, document). Suppressed in debug/review where not editing is the correct behavior.

---

### 6. High failure ratio

**Detection:** More than 50% of tool calls failing, after at least 5 total calls.

**Suggestion:**
```
[kibitzer] High failure rate (62%). Consider stepping back to read before editing.
```

**Active in:** all modes.

---

### 7. Mode oscillation

**Detection:** More than 4 mode switches in a session.

**Suggestion:**
```
[kibitzer] Frequent mode switches. Consider using free mode for this task.
```

**Active in:** all modes.

---

### 8. Editing in debug mode

**Detection:** Any Edit calls while in debug mode.

**Suggestion:**
```
[kibitzer] You're editing files in debug mode. Use ChangeToolMode to switch
to implement mode first.
```

**Active in:** debug mode only.

## Deduplication

Each pattern has a unique ID (`repeated_edit_failure`, `edit_without_test`, etc.). Once a suggestion fires, its ID is added to `state.json`'s `suggestions_given` list. The same pattern won't fire again in the same session.

This means the coach gives each suggestion exactly once. If the agent ignores it, the coach doesn't nag. If the agent follows it (e.g., runs tests after the edit-without-test suggestion), the pattern naturally resolves.

## How patterns connect to the ratchet

Each coach suggestion is a ratchet candidate. Over time:

- **High-frequency observations** → bake into the strategy instruction
- **Observations the agent follows** → the tool or instruction becomes default
- **Observations the agent ignores** → the suggestion may not be the right intervention

The coach's suggestion log (in `state.json`) is the ratchet's observation phase, running continuously. Use `GetFeedback(suggestions=true)` to inspect what the coach has suggested and whether patterns are repeating across sessions.
