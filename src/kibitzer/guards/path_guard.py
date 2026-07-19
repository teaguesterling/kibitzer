"""Path protection for write tools and Bash write vectors.

Two layers:

1. ``check_floor`` — a mode-INDEPENDENT deny-list (the "safety floor"),
   checked before any mode's writable set. Even the ``free`` mode blocks
   genuinely dangerous writes (secret dirs, system paths, raw ``.git``
   internals, writes outside the repo). Configurable via ``[floor]`` in
   config.toml; fails open when repo detection fails or the floor itself
   errors — a broken floor must never block normal work.

2. ``check_path`` / ``check_bash_writes`` — the per-mode writable-set
   check. Writable entries may be repo-relative prefixes ("src/"), exact
   files ("README.md"), or absolute / ``~``-prefixed glob patterns
   expanded at check time ("~/.claude/projects/*/memory/").

Mode-check decision rules:

- Every path is **canonicalized before it is compared**: ``~`` is expanded,
  relative paths are anchored at the project dir, symlinks are followed,
  and ``.``/``..`` are collapsed (``os.path.realpath``), so the guard
  decides on the path the OS would actually write — not on the literal
  string the agent supplied.
- Containment is checked per path segment (``os.path.commonpath``), never
  by string prefix, so ``src`` cannot match ``src_secret/`` and
  ``README.md`` cannot match ``README.md.evil``. Glob entries are matched
  with ``fnmatch`` against the canonical target.
- The mode check **fails closed**: a malformed policy, an empty target, an
  unparseable Bash command in a restricted mode, or any internal error
  denies the write instead of allowing it. (The floor, by contrast, fails
  OPEN on its own internal errors — fail-closed is about mode CONFIG
  validity, fail-open is about floor implementation errors.)

Bash coverage is a static analysis of the command string (redirections and
a table of common write commands). It catches the common write vectors but
it is not a shell interpreter; see the README's "Scope and limits" section
for what it cannot see. Modes with ``writable = ["*"]`` skip the Bash mode
analysis entirely — the floor still applies to extracted targets.
"""

from __future__ import annotations

import os
import re
import shlex
import tempfile
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

_MODE_HINT = "Use the ChangeToolMode tool to switch modes."

# Literal redirect targets that are never file writes worth guarding.
_DEVICE_SINKS = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty", "-"}
_DEV_FD_RE = re.compile(r"^/dev/fd/\d+$")

# Shell wrapper commands: skip them to find the real command.
_WRAPPERS = {"sudo", "command", "nohup", "nice", "time", "builtin", "env"}

# VAR=value environment assignment preceding a command.
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Shells whose `-c` argument is itself a command string we can re-scan.
_SHELLS = {"sh", "bash", "dash", "zsh", "ksh"}

# Commands whose file arguments are written, created, moved, or removed.
# Values name the extraction strategy used in _command_targets().
_WRITE_COMMANDS = {
    "tee": "all",
    "touch": "all",
    "mkdir": "all",
    "truncate": "all",
    "shred": "all",
    "rm": "all",
    "rmdir": "all",
    "unlink": "all",
    "mv": "all",          # mutates the source (removal) and the dest
    "cp": "dest",
    "install": "dest",
    "rsync": "dest",
    "ln": "link",
    "sed": "sed",
    "dd": "dd",
}

_PUNCT_CHARS = set("();<>|&")

_GLOB_CHARS = ("*", "?", "[")


@dataclass
class PathGuardResult:
    allowed: bool
    reason: str = ""


@dataclass
class FloorResult:
    """Result of the mode-independent safety-floor check."""

    allowed: bool
    rule: str = ""
    reason: str = ""


# --- Floor rule tables (see check_floor) ---

_SECRET_DIRS = ("~/.ssh", "~/.aws", "~/.gnupg", "~/.config/gh")

_CREDENTIAL_FILE_PATTERNS = (
    "*credentials*",
    "*.pem",
    "id_rsa*",
    "id_ed25519*",
    "id_ecdsa*",
    "id_dsa*",
)

_SYSTEM_PATHS = ("/etc", "/usr", "/boot", "/bin", "/sbin", "/lib", "/lib64")

# Raw writes into these .git subpaths corrupt repository state. Normal git
# commands don't go through Write/Edit, so this only catches raw file writes.
_GIT_INTERNALS = {"objects", "refs", "HEAD", "packed-refs"}


# --- Canonicalization / containment (shared by mode checks) ---


def canonicalize(path: str, project_dir: str | os.PathLike | None = None) -> str:
    """Resolve *path* to a canonical absolute path.

    ``~`` is expanded (shell semantics — a Bash redirect target or writable
    entry spelled ``~/...`` means the home dir, never a literal ``~`` dir),
    relative paths are anchored at *project_dir* (default: cwd), symlinks in
    every existing component are followed, and ``.``/``..`` are collapsed,
    so two spellings of the same filesystem location compare equal.
    """
    base = os.fspath(project_dir) if project_dir is not None else os.getcwd()
    base = os.path.realpath(base)
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.join(base, path)
    return os.path.realpath(path)


def _contained(target: str, prefix: str) -> bool:
    """True if canonical *target* is *prefix* or lies under it (by segment)."""
    try:
        return os.path.commonpath([target, prefix]) == prefix
    except ValueError:
        # Different drives / mixed absolute-relative — cannot be contained.
        return False


def _validated_writable(mode_policy: dict) -> list | None:
    """The policy's writable list, or None if the policy is malformed."""
    if not isinstance(mode_policy, dict):
        return None
    writable = mode_policy.get("writable", [])
    if not isinstance(writable, (list, tuple)):
        return None
    if not all(isinstance(p, str) for p in writable):
        return None
    return list(writable)


def _deny(file_path: str, writable: list) -> PathGuardResult:
    if not writable:
        return PathGuardResult(
            allowed=False,
            reason=(
                f"Current mode is read-only (tried to write: {file_path}). "
                + _MODE_HINT
            ),
        )
    return PathGuardResult(
        allowed=False,
        reason=(
            f"Path '{file_path}' is not writable in the current mode "
            f"(writable: {writable}). " + _MODE_HINT
        ),
    )


# --- Floor path helpers ---


def _resolve(path: Path) -> Path:
    """Resolve symlinks / normalize; tolerate unresolvable paths."""
    try:
        return path.resolve()
    except OSError:
        return path


def _absolutize(file_path: str, base: Path) -> Path:
    """Expand ~, anchor relative paths at ``base``, resolve symlinks."""
    p = Path(os.path.expanduser(file_path))
    if not p.is_absolute():
        p = base / p
    return _resolve(p)


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _match_pattern(path: str, pattern: str) -> bool:
    """fnmatch with directory semantics: a pattern also matches everything
    beneath it (trailing '/' optional)."""
    pattern = pattern.rstrip("/")
    if not pattern:
        return False
    return fnmatch(path, pattern) or fnmatch(path, pattern + "/*")


def find_repo_root(start: Path) -> Path | None:
    """Nearest ancestor (inclusive) containing ``.git`` (dir or worktree
    file). None when not inside a repo — callers must fail OPEN."""
    start = _resolve(start)
    for candidate in (start, *start.parents):
        try:
            if (candidate / ".git").exists():
                return candidate
        except OSError:
            continue
    return None


def _default_tmp_dirs() -> list[Path]:
    """Temp locations always writable regardless of repo scope (includes the
    Claude Code scratchpad, which lives under the system temp dir)."""
    dirs = [Path(tempfile.gettempdir()), Path("/tmp"), Path("/var/tmp"), Path("/dev/shm")]
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir:
        dirs.append(Path(tmpdir))
    return [_resolve(d) for d in dirs]


# --- Safety floor ---


def _floor_block(rule: str, file_path: str, detail: str) -> FloorResult:
    return FloorResult(
        allowed=False,
        rule=rule,
        reason=(
            f"[kibitzer floor] Write to '{file_path}' blocked by safety-floor "
            f"rule '{rule}' ({detail}). The floor is mode-independent and "
            "guards against accidental writes to sensitive or out-of-project "
            "locations. To override: add a matching pattern to [floor] allow "
            "in .kibitzer/config.toml, or use ChangeToolMode to switch to a "
            "mode whose writable set explicitly lists this path."
        ),
    )


def check_floor(
    file_path: str,
    floor_config: dict,
    project_dir: str | Path,
    home: str | Path | None = None,
    tmp_dirs: list[str | Path] | None = None,
) -> FloorResult:
    """Mode-independent deny-list, checked BEFORE the mode's writable set.

    Rules (in order): configured allow overrides win; then configured deny
    patterns, raw .git internals, secret dirs, system paths, credential-like
    filenames under $HOME (outside the repo), and writes outside the repo
    root (with temp/scratchpad allowances).

    Fail-open by design: when ``project_dir`` is not inside a git repo, the
    outside-repo rule is skipped — only the secrets/system/git rules apply.

    ``home`` / ``tmp_dirs`` are injectable for tests.
    """
    home_p = _resolve(Path(home) if home is not None else Path.home())
    base = _resolve(Path(project_dir))
    resolved = _absolutize(file_path, base)
    spath = str(resolved)

    def expand(pat: str) -> str:
        if pat == "~" or pat.startswith("~/"):
            return str(home_p) + pat[1:]
        return pat

    # 1. Allow overrides win over everything.
    for pat in floor_config.get("allow", []):
        if _match_pattern(spath, expand(pat)):
            return FloorResult(allowed=True)

    # 2. Configured deny patterns.
    for pat in floor_config.get("deny", []):
        if _match_pattern(spath, expand(pat)):
            return _floor_block(
                "config-deny", file_path, f"matches deny pattern '{pat}'"
            )

    repo_root = find_repo_root(base)

    # 3. Raw writes into .git internals.
    parts = resolved.parts
    for i, part in enumerate(parts[:-1]):
        if part == ".git" and parts[i + 1] in _GIT_INTERNALS:
            return _floor_block(
                "git-internals", file_path,
                "raw write into .git objects/refs/HEAD",
            )

    # 4. Secret directories.
    for d in _SECRET_DIRS:
        if _is_under(resolved, Path(expand(d))):
            return _floor_block("secret-dir", file_path, f"inside {d}")

    # 5. System paths (unless the repo itself lives under one).
    for sp in _SYSTEM_PATHS:
        sp_p = _resolve(Path(sp))
        if _is_under(resolved, sp_p):
            if repo_root is not None and _is_under(repo_root, sp_p):
                continue
            return _floor_block("system-path", file_path, f"system path {sp}")

    in_repo = repo_root is not None and _is_under(resolved, repo_root)

    # 6. Credential-like filenames under $HOME (repo files are exempt).
    if not in_repo and _is_under(resolved, home_p):
        for pat in _CREDENTIAL_FILE_PATTERNS:
            if fnmatch(resolved.name, pat):
                return _floor_block(
                    "credential-file", file_path,
                    f"credential-like filename ('{pat}') under home",
                )

    # 7. Outside the repo root. Skipped entirely when no repo was found
    #    (fail open); temp dirs and the scratchpad stay writable. A target
    #    inside ANY git working tree is deliberate dev work (multi-repo
    #    sessions write to sibling repos constantly), not a stray write —
    #    the secrets/system/credential rules above already ran on it.
    if repo_root is not None and not in_repo and not _is_under(resolved, base):
        tmps = (
            _default_tmp_dirs() if tmp_dirs is None
            else [_resolve(Path(t)) for t in tmp_dirs]
        )
        if (
            not any(_is_under(resolved, t) for t in tmps)
            and find_repo_root(resolved.parent) is None
        ):
            return _floor_block(
                "outside-repo", file_path,
                f"outside repo root {repo_root}",
            )

    return FloorResult(allowed=True)


# --- Mode writable-set check ---


def _entry_allows(
    entry: str, target: str, project_dir: str | os.PathLike | None,
) -> bool:
    """True if one writable entry grants the canonical *target*.

    Entries containing glob characters are ``~``-expanded and fnmatch'd
    against the canonical target (a pattern also matches its subtree;
    relative patterns are anchored at the project dir). All other entries —
    repo-relative prefixes, exact files, absolute or ``~`` paths — are
    canonicalized and checked by per-segment containment.
    """
    expanded = os.path.expanduser(entry)
    if any(c in expanded for c in _GLOB_CHARS):
        if not os.path.isabs(expanded):
            base = (
                os.fspath(project_dir) if project_dir is not None
                else os.getcwd()
            )
            expanded = os.path.join(os.path.realpath(base), expanded)
        return _match_pattern(target, expanded)
    return _contained(target, canonicalize(expanded, project_dir))


def check_path(
    file_path: str,
    mode_policy: dict,
    project_dir: str | os.PathLike | None = None,
) -> PathGuardResult:
    """Check if *file_path* is writable under the given mode policy.

    Canonicalizes the target and each writable entry against *project_dir*
    before comparing (``~``-prefixed and glob entries are expanded at check
    time). Never raises: any internal error denies (fail closed).
    """
    try:
        writable = _validated_writable(mode_policy)
        if writable is None:
            return PathGuardResult(
                allowed=False,
                reason=(
                    "Malformed mode policy — denying write (fail closed). "
                    + _MODE_HINT
                ),
            )

        if "*" in writable:
            return PathGuardResult(allowed=True)

        if not isinstance(file_path, str) or not file_path.strip():
            return PathGuardResult(
                allowed=False,
                reason=(
                    "Write call without a target path is not allowed in a "
                    "restricted mode (fail closed). " + _MODE_HINT
                ),
            )

        if not writable:
            return _deny(file_path, writable)

        target = canonicalize(file_path, project_dir)
        for entry in writable:
            if _entry_allows(entry, target, project_dir):
                return PathGuardResult(allowed=True)

        return _deny(file_path, writable)
    except Exception as exc:  # noqa: BLE001 — guard must never fail open
        return PathGuardResult(
            allowed=False,
            reason=(
                f"Path guard error while checking '{file_path}': {exc!r} — "
                "denying (fail closed)."
            ),
        )


# --- Bash write-vector analysis -------------------------------------------


def _tokenize(command: str) -> list[str]:
    """Shell-like tokenization with operators split out.

    Raises ValueError on input shlex cannot parse (e.g. unbalanced quotes);
    the caller treats that as unanalyzable and denies in restricted modes.
    """
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    return list(lex)


def _is_punct(token: str) -> bool:
    return bool(token) and all(c in _PUNCT_CHARS for c in token)


def _nonflag(args: list[str]) -> list[str]:
    return [a for a in args if not a.startswith("-") or a == "-"]


def _command_targets(words: list[str], depth: int) -> tuple[list[str], str]:
    """Write targets of one simple command.

    Returns (targets, opaque_reason). A non-empty opaque_reason means the
    command performs writes whose targets cannot be determined statically —
    the caller denies in restricted modes.
    """
    i = 0
    while i < len(words) and (
        _ASSIGN_RE.match(words[i])
        or os.path.basename(words[i]) in _WRAPPERS
    ):
        i += 1
    if i >= len(words):
        return [], ""

    cmd = os.path.basename(words[i])
    args = [w for w in words[i + 1:] if w != "--"]

    if cmd in _SHELLS and "-c" in args:
        # Re-scan the -c payload one level down.
        idx = args.index("-c")
        if idx + 1 < len(args):
            if depth <= 0:
                return [], f"nested `{cmd} -c` too deep to analyze"
            return _extract_write_targets(args[idx + 1], depth - 1)
        return [], ""

    if cmd == "xargs":
        nested = _nonflag(args)
        if nested and os.path.basename(nested[0]) in _WRITE_COMMANDS:
            return [], (
                f"`xargs {nested[0]}` writes to paths supplied on stdin, "
                "which cannot be checked statically"
            )
        return [], ""

    if cmd == "find":
        mutating = {"-delete", "-exec", "-execdir", "-ok", "-okdir"}
        if any(a in mutating for a in args):
            roots = []
            for a in args:
                if a.startswith("-") or a in ("!", "(", ")"):
                    break
                roots.append(a)
            return roots or ["."], ""
        return [], ""

    strategy = _WRITE_COMMANDS.get(cmd)
    if strategy is None:
        return [], ""

    files = _nonflag(args)

    if strategy == "all":
        return files, ""

    if strategy == "dest":
        if "-t" in args:
            idx = args.index("-t")
            if idx + 1 < len(args):
                return [args[idx + 1]], ""
        for a in args:
            if a.startswith("--target-directory="):
                return [a.split("=", 1)[1]], ""
        return ([files[-1]] if len(files) >= 2 else []), ""

    if strategy == "link":
        if len(files) >= 2:
            return [files[-1]], ""
        if len(files) == 1:
            # `ln [-s] TARGET` creates basename(TARGET) in the cwd.
            return [os.path.basename(files[0])], ""
        return [], ""

    if strategy == "sed":
        if not any(
            a == "-i" or a.startswith("-i") or a == "--in-place"
            or a.startswith("--in-place=")
            for a in args
        ):
            return [], ""
        script_inline = any(
            a in ("-e", "-f", "--expression", "--file")
            or a.startswith(("--expression=", "--file="))
            for a in args
        )
        return (files if script_inline else files[1:]), ""

    if strategy == "dd":
        return [a[3:] for a in args if a.startswith("of=")], ""

    return [], ""


def _extract_write_targets(command: str, depth: int = 3) -> tuple[list[str], str]:
    """All statically visible write targets in a shell command string.

    Returns (targets, opaque_reason). Raises ValueError if the command
    cannot be tokenized.
    """
    tokens = _tokenize(command)
    targets: list[str] = []
    segment: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if _is_punct(tok):
            if ">" in tok and "<<" not in tok:
                if "(" in tok or ")" in tok:
                    return [], "process substitution cannot be checked statically"
                nxt = tokens[i + 1] if i + 1 < len(tokens) else None
                if nxt is None or _is_punct(nxt):
                    return [], f"redirection `{tok}` without a resolvable target"
                if "&" in tok and (nxt.isdigit() or nxt == "-"):
                    i += 2  # fd duplication (e.g. 2>&1), not a file write
                    continue
                targets.append(nxt)
                i += 2
                continue
            if tok in ("<<", "<<-"):
                i += 2  # heredoc delimiter, not a file
                continue
            if tok in ("<", "<<<"):
                i += 2  # input redirection source, not a write
                continue
            # Command separator: |, ||, &&, ;, &, (, ) ...
            seg_targets, opaque = _command_targets(segment, depth)
            if opaque:
                return [], opaque
            targets.extend(seg_targets)
            segment = []
            i += 1
            continue
        segment.append(tok)
        i += 1

    seg_targets, opaque = _command_targets(segment, depth)
    if opaque:
        return [], opaque
    targets.extend(seg_targets)
    return targets, ""


def extract_bash_write_targets(command: str) -> list[str]:
    """Statically visible write targets of a Bash command (best effort).

    Returns [] when the command cannot be parsed or its write targets are
    statically opaque — callers needing fail-closed semantics for those
    cases use :func:`check_bash_writes`; this helper exists for callers
    layering additional per-target checks (the safety floor) with
    fail-open semantics. Device sinks (``/dev/null`` etc.) are filtered.
    """
    try:
        targets, _opaque = _extract_write_targets(command)
    except ValueError:
        return []
    return [
        t for t in targets
        if t not in _DEVICE_SINKS and not _DEV_FD_RE.match(t)
    ]


def check_bash_writes(
    command: str,
    mode_policy: dict,
    project_dir: str | os.PathLike | None = None,
) -> PathGuardResult:
    """Check a Bash command's statically visible write targets.

    Applies the same canonicalized, fail-closed containment check as
    :func:`check_path` to every write vector the static analysis can see
    (redirections and common write commands). In restricted modes an
    unanalyzable command (unbalanced quotes, opaque write constructs)
    is denied. Never raises.
    """
    try:
        writable = _validated_writable(mode_policy)
        if writable is None:
            return PathGuardResult(
                allowed=False,
                reason=(
                    "Malformed mode policy — denying Bash command "
                    "(fail closed). " + _MODE_HINT
                ),
            )

        if "*" in writable:
            return PathGuardResult(allowed=True)

        if not isinstance(command, str) or not command.strip():
            return PathGuardResult(allowed=True)

        try:
            targets, opaque = _extract_write_targets(command)
        except ValueError as exc:
            return PathGuardResult(
                allowed=False,
                reason=(
                    f"Bash command could not be parsed for path protection "
                    f"({exc}) — denying in a restricted mode (fail closed). "
                    + _MODE_HINT
                ),
            )
        if opaque:
            return PathGuardResult(
                allowed=False,
                reason=(
                    f"Bash command denied in a restricted mode: {opaque} "
                    "(fail closed). " + _MODE_HINT
                ),
            )

        for raw in targets:
            if raw in _DEVICE_SINKS or _DEV_FD_RE.match(raw):
                continue
            target = canonicalize(raw, project_dir)
            if not any(
                _entry_allows(entry, target, project_dir)
                for entry in writable
            ):
                result = _deny(raw, writable)
                result.reason = (
                    f"Bash command writes to '{raw}', which is not writable "
                    f"in the current mode (writable: {writable}). "
                    + _MODE_HINT
                )
                return result
        return PathGuardResult(allowed=True)
    except Exception as exc:  # noqa: BLE001 — guard must never fail open
        return PathGuardResult(
            allowed=False,
            reason=(
                f"Path guard error while checking Bash command: {exc!r} — "
                "denying (fail closed)."
            ),
        )
