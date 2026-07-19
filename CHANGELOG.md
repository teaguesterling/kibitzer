# Changelog

## v0.9.0

### Features
- **`intercept.log` entries are timestamped.** Each entry now carries an ISO `timestamp`, so downstream consumers (agent-riggs ingest) no longer have to stamp them at ingest time; old lines without the key still parse.
- **Mode-independent safety floor.** A deny-list checked BEFORE any mode's writable set — even the default `free` mode now blocks genuinely dangerous writes: secret dirs (`~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config/gh`), credential-like filenames under `$HOME` (outside the repo), system paths (`/etc`, `/usr`, `/boot`, `/bin`, `/sbin`, `/lib`), raw `.git` internals (objects/refs/HEAD/packed-refs), and writes outside the repo root. Temp dirs, the Claude scratchpad, memory dirs, and `~/.claude/inbox` stay writable. Configurable via `[floor]` (`enabled`/`deny`/`allow`); overridable per-path via `[floor] allow` or a mode whose writable set explicitly (non-`*`) grants the path. Fails OPEN: no repo detected → the outside-repo rule is skipped; any exception in floor checking allows + logs a `floor_error` event. Blocks are logged as `floor_block` store events so value-vs-friction is measurable across sessions.
- **Hardened path guard (PR #6).** Every write target and writable prefix is canonicalized before comparison (anchored at the project dir, symlinks followed, `.`/`..` collapsed) and matched by path segment, closing `..`/absolute/symlink traversal and the sibling-prefix over-match (`src` vs `src_secret/`). The mode check now **fails closed**: unknown modes resolve to `writable = []`, and a missing target, malformed policy, or internal guard error denies instead of allowing. **Bash write vectors** (redirections, `tee`, `cp`, `mv`, `rm`, `sed -i`, `ln`, `dd of=`, `find -delete`, `sh -c` payloads, ...) are statically extracted and checked with the same rules; statically opaque write constructs are denied in restricted modes, and `writable = ["*"]` modes skip the Bash mode check.
- **The safety floor covers Bash write targets too.** Statically extracted Bash write targets are floor-checked before the mode check, with the same semantics as `Write`/`Edit` targets — `echo x > ~/.ssh/authorized_keys` floor-blocks even in `free` mode, while a redirect into a sibling git repo stays allowed. The two error philosophies compose: mode-CONFIG problems (unknown mode, malformed policy) fail CLOSED; floor-internal errors fail OPEN (`floor_error` event, never blocks).
- **Adaptive nudge decay.** After N consecutive un-heeded NUDGE trials for a plugin (default `decay_threshold = 3`, streak persisted in state.json across sessions), that plugin's nudges are suppressed — intercepts are still logged, and each suppression is recorded with disposition `arm="suppressed"` in nudge_trials.jsonl plus a `nudge_suppressed` store event, keeping the A/B heed metrics clean. A heeded nudge resets the streak; un-heeded CONTROL trials (never shown) don't count.

### Config
- **`implement` mode now includes `tests/` and `test/`** so test-alongside-code TDD doesn't thrash between modes.
- **Writable sets accept absolute and `~`-prefixed glob entries**, expanded at check time (repo-relative entries behave as before). The Claude memory-dir pattern `~/.claude/projects/*/memory/` is now in the implement/test/docs writable defaults.

## v0.8.2

### Fixes
- **Self-ignore the `.kibitzer/` runtime dir.** `ensure_state_dir` now drops a `.kibitzer/.gitignore` (`*`) on creation, so the churning runtime artifacts (state.json, store.sqlite + WAL files, intercept.log) no longer show as dirty in the host repo or get swept into a commit. (Repos that already track `.kibitzer/` still need a one-time `git rm -r --cached .kibitzer/`.)

## v0.8.1

### Fixes
- **Attribute nudge A/B trials to their session.** The Pre/PostToolUse hooks now read `session_id` from the Claude Code hook payload (falling back to the transcript filename stem) and stamp it onto state, so each `nudge_trials.jsonl` record carries the session that opened it plus a timestamp — previously every record logged `session: null` and was unattributable. Heed is now credited only on a same-session match, so a concurrent session sharing the per-project state can't falsely mark heed.

### Performance
- **Stop fsync-per-tool-call in the observation store.** `KibitzerStore` connections now set `PRAGMA journal_mode=WAL` + `PRAGMA synchronous=OFF` (measured 4→0 fsync per appended event). The store is advisory, non-authoritative telemetry; worst case on power loss is losing the last few observed events. `save()` now dirty-checks state, skipping the redundant `state.json` rewrite when the pre hook didn't mutate anything.

### Config
- **jetsam interceptor moved to `suggest` mode** (was `observe`), so jetsam bypasses form measurable nudge trials like blq/squackit.
