> **Superseded.** This plan describes an earlier Fledgling-embedded design. The current implementation uses standalone hooks + MCP. See [architecture.md](../architecture.md) for the current design.

# User Kibitzer — Design Plan (historical)

## Origin

Extracted from fledgling P4-006 (session state). Deferred from the initial
implementation to a follow-up. Builds on the access log and conversation
history.

## Concept

Analyzes the human's workflow patterns across sessions and suggests
improvements. Runs as an MCP tool the agent can call, or as a resource.
Different audience from the agent kibitzer: this coaches the human, not the
agent.

## Depends On

- Fledgling-pro access log (P4-006)
- Fledgling conversation macros (`sessions()`, `tool_frequency()`, etc.)
- Agent kibitzer infrastructure (coaching levels, suggestion tracking)

## Suggested Patterns

| Pattern | Suggestion |
|---------|-----------|
| bash > 50% of operations | "Fledgling tools like FindDefinitions and ReadLines are more token-efficient" |
| Never uses CodeStructure | "Try CodeStructure before reading files" |
| CLAUDE.md missing fledgling guidance | "Adding 'use FindDefinitions instead of grep' helps the agent choose structured tools" |
| No blq setup | "Consider running init-dev to set up blq for build/test tracking" |
| No jetsam setup | "Consider running init-dev to set up jetsam for git workflow" |
| Always starts by reading README | "Consider adding it as a resource" |
| Frequently searches same patterns | "Consider a custom macro or alias" |

## Interface

```python
@mcp.tool()
async def suggest_improvements() -> str:
    """Analyze recent sessions and suggest workflow improvements."""

@mcp.resource("fledgling://suggestions")
async def suggestions_resource() -> str:
    """Workflow improvement suggestions based on recent usage."""
```

## Cross-Session Data

The user kibitzer needs to persist data across sessions. Options:
- Query conversation JSONL files via fledgling's chat macros (already exists)
- Aggregate access logs across sessions (requires persisting the access log
  to disk, or querying blq-style captured data)

## Notes from P4-006 Design

- The access log table structure was designed with user kibitzer in mind
- The coaching level system (auto-adjustment, acted-on heuristics) can be
  reused for user-level coaching
- Cross-session analysis is analogous to blq's cross-run analysis
