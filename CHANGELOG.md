# Changelog

## v0.8.1

### Fixes
- **Attribute nudge A/B trials to their session.** The Pre/PostToolUse hooks now read `session_id` from the Claude Code hook payload (falling back to the transcript filename stem) and stamp it onto state, so each `nudge_trials.jsonl` record carries the session that opened it plus a timestamp — previously every record logged `session: null` and was unattributable. Heed is now credited only on a same-session match, so a concurrent session sharing the per-project state can't falsely mark heed.

### Performance
- **Stop fsync-per-tool-call in the observation store.** `KibitzerStore` connections now set `PRAGMA journal_mode=WAL` + `PRAGMA synchronous=OFF` (measured 4→0 fsync per appended event). The store is advisory, non-authoritative telemetry; worst case on power loss is losing the last few observed events. `save()` now dirty-checks state, skipping the redundant `state.json` rewrite when the pre hook didn't mutate anything.

### Config
- **jetsam interceptor moved to `suggest` mode** (was `observe`), so jetsam bypasses form measurable nudge trials like blq/squackit.
