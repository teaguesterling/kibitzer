> **Superseded.** This plan describes an earlier Fledgling-embedded design. The current implementation uses standalone hooks + MCP. See [architecture.md](../architecture.md) for the current design.

# Agent Kibitzer — Design Plan (historical)

## Origin

Extracted from fledgling P4-006 (session state). The session cache and access
log ship in fledgling-pro; the kibitzer builds on top of the access log and
deserves its own project.

## Concept

Observes an agent's MCP tool usage within a session and suggests better
approaches. Appends suggestions to tool output, filtered by a coaching level.

## Coaching Levels

| Level    | Inline behavior |
|----------|----------------|
| `off`    | Nothing inline. Suggestions still accumulate on the resource. |
| `low`    | Only cache hit notes |
| `medium` | Cache hits + high-confidence suggestions (repeated calls, large file without range) |
| `high`   | All suggestions including softer coaching ("try FindDefinitions instead") |
| `auto`   | Starts at `medium`, adjusts based on agent responsiveness |

Default: `auto`.

## Auto-Adjustment Logic

- Starts at `medium`
- If 3+ suggestions are acted on (agent changes behavior), stay at `medium`
- If 5 consecutive suggestions are ignored, drop to `low` with notice:
  `"Coaching reduced — use kibitzer__adjust(coaching='auto') to reset"`
- At `low`, if 5 more ignored, drop to `off` with same notice
- `kibitzer__adjust(coaching='auto')` resets streak counter and level to `medium`

### "Acted on" heuristic

After suggesting "try FindDefinitions," if the next 3 calls include
`find_definitions`, mark it as acted on. Simple, imperfect, good enough.

## Pattern Detectors

| Pattern | Suggestion | Min level |
|---------|-----------|-----------|
| Repeated identical call | "(cached)" note | `low` |
| 3+ `read_source` on same file, different `match` | "Try FindInAST for structural search" | `medium` |
| `read_source` without `lines` on file > 200 lines | "Use lines='N-M' to read a section" | `medium` |
| `find_definitions` returning 50+ results | "Use name_pattern to narrow" | `medium` |
| No code tools after initial explore | "Try CodeStructure or FindDefinitions" | `high` |

## Tool

```python
@mcp.tool()
async def kibitzer__adjust(coaching: str = 'auto') -> str:
    """Adjust coaching verbosity: 'auto', 'off', 'low', 'medium', 'high'"""
```

Returns current state summary: level, suggestions given, acted-on rate.

## Resource

`fledgling://session` includes a coaching section with all pending suggestions
regardless of the inline coaching level.

## Dependencies

- Fledgling-pro access log (session_access_log table from P4-006)
- Pattern detectors query the access log SQL table
- Kibitzer state is Python-side (coaching level, suggestion history, ignore streak)

## Integration Point

Hooks into `server.py`'s tool registration pipeline, after the access log
write. Runs pattern detectors, filters by coaching level, appends inline
suggestions to formatted output.
