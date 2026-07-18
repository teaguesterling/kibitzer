"""Path protection for Edit/Write/NotebookEdit.

Two layers:

1. ``check_floor`` — a mode-INDEPENDENT deny-list (the "safety floor"),
   checked before any mode's writable set. Even the ``free`` mode blocks
   genuinely dangerous writes (secret dirs, system paths, raw ``.git``
   internals, writes outside the repo). Configurable via ``[floor]`` in
   config.toml; fails open when repo detection fails.

2. ``check_path`` — the per-mode writable-set check. Writable entries may
   be repo-relative prefixes ("src/"), exact files ("README.md"), or
   absolute / ``~``-prefixed glob patterns expanded at check time
   ("~/.claude/projects/*/memory/").
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path


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


# --- Shared path helpers ---


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
    #    (fail open); temp dirs and the scratchpad stay writable.
    if repo_root is not None and not in_repo and not _is_under(resolved, base):
        tmps = (
            _default_tmp_dirs() if tmp_dirs is None
            else [_resolve(Path(t)) for t in tmp_dirs]
        )
        if not any(_is_under(resolved, t) for t in tmps):
            return _floor_block(
                "outside-repo", file_path,
                f"outside repo root {repo_root}",
            )

    return FloorResult(allowed=True)


# --- Mode writable-set check ---


def check_path(
    file_path: str,
    mode_policy: dict,
    project_dir: str | Path | None = None,
) -> PathGuardResult:
    """Check if file_path is writable under the given mode policy.

    Writable entries may be:
      - repo-relative prefixes or files ("src/", "README.md") — matched
        against the path relative to ``project_dir``
      - absolute or ``~``-prefixed glob patterns ("/opt/scratch/",
        "~/.claude/projects/*/memory/") — expanded at check time and
        matched against the resolved absolute path
      - "*" — everything
    """
    writable = mode_policy.get("writable", [])

    if "*" in writable:
        return PathGuardResult(allowed=True)

    if not writable:
        return PathGuardResult(
            allowed=False,
            reason=(
                f"Current mode is read-only (tried to write: {file_path}). "
                "Use the ChangeToolMode tool to switch to a writable mode."
            ),
        )

    base = _resolve(Path(project_dir) if project_dir is not None else Path.cwd())
    abs_path = _absolutize(file_path, base)
    rel_path = file_path
    if Path(file_path).is_absolute() and _is_under(abs_path, base):
        rel_path = str(abs_path.relative_to(base))

    for entry in writable:
        if entry.startswith(("~", "/")):
            if _match_pattern(str(abs_path), os.path.expanduser(entry)):
                return PathGuardResult(allowed=True)
        elif rel_path.startswith(entry) or rel_path == entry:
            return PathGuardResult(allowed=True)

    return PathGuardResult(
        allowed=False,
        reason=(
            f"Path '{file_path}' is not writable in the current mode "
            f"(writable: {writable}). "
            "Use the ChangeToolMode tool to switch modes."
        ),
    )
