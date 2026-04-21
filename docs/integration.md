# Integration

Kibitzer coordinates three existing tools and integrates with the superpowers plugin. None of these are required — kibitzer degrades gracefully.

## What works without anything installed

| Component | Requires |
|-----------|----------|
| Path guard | nothing — just config.toml and state.json |
| Mode controller | nothing |
| Coach (basic patterns) | nothing |
| Coach (tool-aware suggestions) | `.mcp.json` in project root |
| Coach (fledgling queries) | fledgling Python package or CLI |
| MCP tools | nothing |
| BlqInterceptor | blq on PATH |
| JetsamInterceptor | jetsam on PATH |
| FledglingInterceptor | fledgling on PATH |
| Coach (semantic underuse) | fledgling available to the agent |
| Doc context pipeline | pluckit Python package |

## blq

[blq](https://github.com/teague/lq) captures structured build/test output. Kibitzer's BlqInterceptor suggests `blq run test` when the agent runs pytest (or npm test, cargo test, etc.) through Bash.

**What kibitzer uses from blq:**
- `blq` binary on PATH (for interceptor availability check)

**What kibitzer does NOT do:**
- Does not call blq directly
- Does not enforce blq's sandbox specs (that's blq's domain)
- Does not parse blq's output

**Future:** The coach could query blq's event stream for richer pattern detection (e.g., "you've introduced 3 new test failures since your last edit").

## jetsam

[jetsam](https://github.com/teague/jetsam) manages git workflow — atomic saves, syncs, plans, mode state. Kibitzer's JetsamInterceptor suggests jetsam commands when the agent uses raw git through Bash.

**What kibitzer uses from jetsam:**
- `jetsam` binary on PATH (for interceptor availability check)

**What kibitzer does NOT do:**
- Does not read jetsam's state files (uses its own `.kibitzer/state.json`)
- Does not call `jetsam mode` — mode switching is via kibitzer's own `ChangeToolMode` MCP tool
- Does not duplicate jetsam's save/sync workflow

**Design note:** Kibitzer and jetsam both have a concept of "mode." They're independent — jetsam's mode controls its own workflow, kibitzer's mode controls path protection and coaching. An agent could use both. In a future version, the two could share mode state, but for now they're separate to avoid coupling.

## Fledgling

[Fledgling](https://github.com/teague/source-sextant) provides read-only code intelligence via DuckDB — AST queries, definition lookup, caller tracing, conversation analytics.

**What kibitzer uses from fledgling:**
- `fledgling` binary on PATH or Python package importable (for interceptor availability check)
- Semantic tool names in counter tracking (`FindDefinitions`, `CodeStructure`, etc.)
- **Conversation analytics queries** for richer coaching:
  - `tool_calls()` — detect repeated search patterns (same grep 3+ times)
  - `bash_commands()` — find bash commands with structured alternatives (`replaceable_by` field)

**How queries work:**

Kibitzer prefers fledgling's Python API when importable (`fledgling.connect()`), falling back to CLI subprocess calls (`fledgling -f json query "SQL"`). Install with `pip install kibitzer[fledgling]` for the Python API path.

```python
# Python API (preferred — in-process, fast)
import fledgling
con = fledgling.connect()
rows = con.sql("SELECT * FROM tool_calls() WHERE ...").df().to_dict(orient="records")

# CLI fallback (subprocess, slower but always works if CLI installed)
fledgling -f json query "SELECT * FROM tool_calls() WHERE ..."
```

All queries have a 5-second timeout. If fledgling is unavailable or a query fails, the coach falls back to state-only patterns — no degradation of existing behavior.

**What kibitzer does NOT do:**
- Does not manage fledgling kits (that's the quartermaster's future job)
- Does not write to fledgling's database (read-only, level 0)

**Future:** Kit effectiveness tracking (which tools were used vs. available in the current kit).

## Pluckit

[Pluckit](https://github.com/teague/pluckit) provides structured access to markdown documentation — glob-based doc discovery, ILIKE search, and section extraction.

**What kibitzer uses from pluckit:**
- `Plucker(docs=glob).docs()` — discover and load markdown files
- `.filter(search=term, file_path=path)` — ILIKE substring matching
- `.sections()` — extract structured sections (title, content, file_path, level)

**How it's used:**

Kibitzer's `get_doc_context()` pipeline uses pluckit for the mechanical retrieval step. Tool documentation paths are registered via `register_docs()` (typically from lackpy's tool catalog `docs_index`), and pluckit searches within those docs for sections relevant to the current failure or error.

```python
session.register_docs(
    doc_refs={"Read": "tools/read.md", "Edit": "tools/edit.md"},
    docs_root="/path/to/docs",
)
result = session.get_doc_context("permission denied", tool="Edit")
```

Install with `pip install kibitzer[pluckit]` for the doc context pipeline. Without pluckit, `get_doc_context()` returns an empty `DocResult` — no degradation of existing behavior.

**What kibitzer does NOT do:**
- Does not index docs at storage time (retrieval is on-demand)
- Does not provide FTS/semantic search (uses pluckit's ILIKE with multi-word fallback)
- Does not decide which sections matter — that's the consumer's `select` callback

## Superpowers

The [superpowers plugin](https://github.com/anthropics/claude-plugins-official/tree/main/superpowers) manages workflow phases (brainstorm → plan → implement → review) through skill invocations. Kibitzer manages tool constraints (what can be written where).

**These are complementary, not competing.**

| Concern | Superpowers owns | Kibitzer's role |
|---------|-----------------|----------------|
| Workflow phases | Skills define the progression | Observe active skill, suggest matching mode |
| Task tracking | TodoWrite / TaskCreate | Don't duplicate — read task state for coaching context |
| TDD discipline | test-driven-development skill | Path guard enforces mechanically (can't edit src/ in test_dev) |
| Code review | requesting-code-review skill | Coach can suggest invoking the skill |
| Git worktrees | using-git-worktrees skill | Detect worktree context, apply config per-worktree |
| Plan files | `docs/superpowers/plans/` | Read for coaching context ("you're on step 3 of 7") |
| Verification | verification-before-completion | Coach can remind after edit streaks |

**What kibitzer does NOT duplicate:**
- Superpowers' "design before code" gate
- Superpowers' "verify before claiming done" gate
- Superpowers' task/todo tracking
- Superpowers' subagent dispatch

**Hook coexistence:** Superpowers uses SessionStart hooks. Kibitzer uses PreToolUse and PostToolUse. No collision. `kibitzer init` detects existing hooks and merges without clobbering.

## Integration architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                       KIBITZER (level 1)                         │
│  Hooks: path guard, interceptors, mode controller, coach         │
│  MCP: ChangeToolMode, GetFeedback                                │
│  API: register_docs, get_doc_context, get_correction_hints, ...  │
└──────┬──────────────┬──────────────┬──────────────┬──────────────┘
       │              │              │              │
 ┌─────┴─────┐ ┌─────┴─────┐ ┌─────┴──────┐ ┌────┴──────┐
 │    blq    │ │  jetsam   │ │  fledgling  │ │  pluckit  │
 │ (level 1) │ │ (level 1) │ │  (level 0)  │ │ (level 0) │
 │           │ │           │ │             │ │           │
 │ test      │ │ git       │ │ code        │ │ doc       │
 │ capture   │ │ workflow  │ │ intelligence│ │ retrieval │
 └───────────┘ └───────────┘ └─────────────┘ └───────────┘
```

Each tool is independent. Kibitzer suggests alternatives but never wraps or calls them. The agent decides whether to use the suggestion. Pluckit is the one exception — kibitzer calls it directly for doc retrieval, but only when the consumer has registered docs.
