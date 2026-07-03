"""Mode-based path protection for write tools and Bash write vectors.

Decision rules:

- Every path is **canonicalized before it is compared**: relative paths are
  anchored at the project dir, symlinks are followed, and ``.``/``..`` are
  collapsed (``os.path.realpath``), so the guard decides on the path the OS
  would actually write — not on the literal string the agent supplied.
- Containment is checked per path segment (``os.path.commonpath``), never by
  string prefix, so ``src`` cannot match ``src_secret/`` and ``README.md``
  cannot match ``README.md.evil``.
- The guard **fails closed**: a malformed policy, an empty target, an
  unparseable Bash command in a restricted mode, or any internal error denies
  the write instead of allowing it.

Bash coverage is a static analysis of the command string (redirections and a
table of common write commands). It catches the common write vectors but it
is not a shell interpreter; see the README's "Scope and limits" section for
what it cannot see. Modes with ``writable = ["*"]`` skip Bash analysis
entirely.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass

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


@dataclass
class PathGuardResult:
    allowed: bool
    reason: str = ""


def canonicalize(path: str, project_dir: str | os.PathLike | None = None) -> str:
    """Resolve *path* to a canonical absolute path.

    Relative paths are anchored at *project_dir* (default: cwd). Symlinks in
    every existing component are followed and ``.``/``..`` are collapsed, so
    two spellings of the same filesystem location compare equal.
    """
    base = os.fspath(project_dir) if project_dir is not None else os.getcwd()
    base = os.path.realpath(base)
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


def check_path(
    file_path: str,
    mode_policy: dict,
    project_dir: str | os.PathLike | None = None,
) -> PathGuardResult:
    """Check if *file_path* is writable under the given mode policy.

    Canonicalizes the target and each writable prefix against *project_dir*
    before comparing. Never raises: any internal error denies (fail closed).
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
        for prefix in writable:
            if _contained(target, canonicalize(prefix, project_dir)):
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

        canon_prefixes = [canonicalize(p, project_dir) for p in writable]
        for raw in targets:
            if raw in _DEVICE_SINKS or _DEV_FD_RE.match(raw):
                continue
            target = canonicalize(raw, project_dir)
            if not any(_contained(target, p) for p in canon_prefixes):
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
