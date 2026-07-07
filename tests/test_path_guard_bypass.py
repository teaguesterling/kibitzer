"""Regression tests for path-guard canonicalization and fail-closed behavior.

These tests encode the fix for a set of path-protection weaknesses (tracked
in the project's private security advisory and issue #5):

- paths were compared with a raw ``startswith`` on unnormalized strings, so
  ``..`` traversal, absolute paths, symlinks, and sibling-prefix names
  (``src_secret/`` vs ``src/``) slipped past the guard;
- an unknown/unrecognized mode fell back to ``writable = ["*"]`` (fail open);
- an empty ``file_path`` skipped the guard entirely;
- Bash write vectors (redirections, ``tee``, ``cp``, ``mv``, ``rm``,
  ``sed -i``, ``ln``, ...) were never path-checked;
- an exception inside the guard was swallowed by safe mode (fail open).

Every test in the Blocked* classes FAILED on the pre-fix code. The Allowed*
classes pin down that the fail-closed guard does not over-block legitimate
in-scope writes.

Symlink/``..`` fixtures are built inside pytest tmp_path -- no real
protected path is ever touched.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from kibitzer.config import get_mode_policy
from kibitzer.session import KibitzerSession
from kibitzer.state import fresh_state, save_state


def _project(tmp_path, mode="implement"):
    """A throwaway project dir with kibitzer state in the given mode."""
    proj = tmp_path / "proj"
    (proj / ".kibitzer").mkdir(parents=True)
    (proj / "src").mkdir()
    (proj / "tests").mkdir()
    state = fresh_state()
    state["mode"] = mode
    save_state(state, proj / ".kibitzer")
    return proj


def _before_call(proj, tool, tool_input):
    with KibitzerSession(project_dir=proj) as session:
        return session.before_call(tool, tool_input)


def _denied(result):
    return result is not None and result.denied


class TestBlockedTraversal:
    """`..` and absolute-path forms that resolve outside writable dirs."""

    def test_dotdot_out_of_writable_blocked(self, tmp_path):
        # Literal path starts with 'src/' but resolves into tests/.
        proj = _project(tmp_path, mode="implement")
        result = _before_call(
            proj, "Write", {"file_path": "src/../tests/evil.py"},
        )
        assert _denied(result)

    def test_dotdot_escaping_project_blocked(self, tmp_path):
        proj = _project(tmp_path, mode="implement")
        result = _before_call(
            proj, "Write", {"file_path": "src/../../outside.py"},
        )
        assert _denied(result)

    def test_absolute_path_with_dotdot_blocked(self, tmp_path):
        proj = _project(tmp_path, mode="implement")
        target = str(proj / "src" / ".." / "tests" / "evil.py")
        result = _before_call(proj, "Write", {"file_path": target})
        assert _denied(result)

    def test_redundant_separators_normalized(self, tmp_path):
        proj = _project(tmp_path, mode="implement")
        result = _before_call(
            proj, "Write", {"file_path": "src/./..//tests/evil.py"},
        )
        assert _denied(result)


class TestBlockedSymlink:
    """A literal path inside a writable dir whose canonical target escapes."""

    def test_symlink_escape_blocked(self, tmp_path):
        proj = _project(tmp_path, mode="implement")
        outside = tmp_path / "outside"
        outside.mkdir()
        (proj / "src" / "link").symlink_to(outside)

        result = _before_call(
            proj, "Write", {"file_path": "src/link/evil.py"},
        )
        assert _denied(result)

    def test_symlink_into_protected_sibling_blocked(self, tmp_path):
        # src/link -> tests/: literally under src/, canonically in tests/.
        proj = _project(tmp_path, mode="implement")
        (proj / "src" / "link").symlink_to(proj / "tests")

        result = _before_call(
            proj, "Write", {"file_path": "src/link/evil.py"},
        )
        assert _denied(result)


class TestBlockedSiblingPrefix:
    """String-prefix matching over-matched sibling names (issue #5)."""

    def test_writable_file_prefix_does_not_match_extension(self, tmp_path):
        # docs mode allows README.md; README.md.evil must not ride along.
        proj = _project(tmp_path, mode="docs")
        result = _before_call(
            proj, "Write", {"file_path": "README.md.evil"},
        )
        assert _denied(result)

    def test_writable_dir_prefix_does_not_match_sibling(self, tmp_path):
        # Project override: writable 'src' (no slash) must not match src_secret/.
        proj = _project(tmp_path, mode="implement")
        (proj / ".kibitzer" / "config.toml").write_text(
            '[modes.implement]\nwritable = ["src"]\nstrategy = ""\n'
        )
        result = _before_call(
            proj, "Write", {"file_path": "src_secret/evil.py"},
        )
        assert _denied(result)


class TestFailClosed:
    """Unknown modes, missing paths, and guard errors must deny."""

    def test_unknown_mode_denies_writes(self, tmp_path):
        proj = _project(tmp_path, mode="no-such-mode")
        result = _before_call(
            proj, "Write", {"file_path": "anything.py"},
        )
        assert _denied(result)

    def test_unknown_mode_policy_is_empty(self):
        policy = get_mode_policy({}, "no-such-mode")
        assert policy.get("writable") == []

    def test_umwelt_missing_writable_fails_closed(self):
        # A umwelt-resolved mode with no writable property is read-only.
        from kibitzer.umwelt.consumer import ModePolicy, _parse_writable

        assert _parse_writable(None) == []
        assert ModePolicy(name="x").writable == []

    def test_empty_file_path_denied_in_restricted_mode(self, tmp_path):
        proj = _project(tmp_path, mode="implement")
        result = _before_call(proj, "Write", {})
        assert _denied(result)

    def test_guard_exception_denies_in_safe_mode(self, tmp_path):
        proj = _project(tmp_path, mode="implement")
        with patch(
            "kibitzer.session.check_path", side_effect=RuntimeError("boom"),
        ):
            with KibitzerSession(project_dir=proj, safe_mode=True) as session:
                result = session.before_call(
                    "Edit", {"file_path": "src/foo.py"},
                )
        assert _denied(result)


class TestBlockedBashWrites:
    """Bash write vectors go through the same canonicalized guard."""

    def test_redirect_into_protected_blocked(self, tmp_path):
        proj = _project(tmp_path, mode="implement")
        result = _before_call(
            proj, "Bash", {"command": "echo evil > tests/x.py"},
        )
        assert _denied(result)

    def test_append_redirect_blocked(self, tmp_path):
        proj = _project(tmp_path, mode="implement")
        result = _before_call(
            proj, "Bash", {"command": "echo evil >> tests/x.py"},
        )
        assert _denied(result)

    def test_tee_into_protected_blocked(self, tmp_path):
        proj = _project(tmp_path, mode="implement")
        result = _before_call(
            proj, "Bash", {"command": "echo evil | tee tests/x.py"},
        )
        assert _denied(result)

    def test_symlink_creation_into_protected_blocked(self, tmp_path):
        # The advisory's static symlink hole: create the link via Bash,
        # then write through it. Creating the link must itself be denied.
        proj = _project(tmp_path, mode="implement")
        result = _before_call(
            proj, "Bash", {"command": "ln -s /etc tests/link"},
        )
        assert _denied(result)

    def test_cp_into_protected_blocked(self, tmp_path):
        proj = _project(tmp_path, mode="implement")
        result = _before_call(
            proj, "Bash", {"command": "cp src/a.py tests/a.py"},
        )
        assert _denied(result)

    def test_sed_inplace_on_protected_blocked(self, tmp_path):
        proj = _project(tmp_path, mode="implement")
        result = _before_call(
            proj, "Bash", {"command": "sed -i s/a/b/ tests/x.py"},
        )
        assert _denied(result)

    def test_rm_of_protected_blocked(self, tmp_path):
        proj = _project(tmp_path, mode="implement")
        result = _before_call(
            proj, "Bash", {"command": "rm tests/x.py"},
        )
        assert _denied(result)

    def test_redirect_after_chain_blocked(self, tmp_path):
        proj = _project(tmp_path, mode="implement")
        result = _before_call(
            proj,
            "Bash",
            {"command": "true && echo evil > tests/x.py"},
        )
        assert _denied(result)

    def test_redirect_with_traversal_blocked(self, tmp_path):
        proj = _project(tmp_path, mode="implement")
        result = _before_call(
            proj, "Bash", {"command": "echo evil > src/../tests/x.py"},
        )
        assert _denied(result)


class TestAllowedStillAllowed:
    """The fail-closed guard must not over-block in-scope work."""

    def test_plain_write_in_writable_allowed(self, tmp_path):
        proj = _project(tmp_path, mode="implement")
        result = _before_call(proj, "Write", {"file_path": "src/foo.py"})
        assert not _denied(result)

    def test_absolute_write_in_writable_allowed(self, tmp_path):
        proj = _project(tmp_path, mode="implement")
        result = _before_call(
            proj, "Write", {"file_path": str(proj / "src" / "foo.py")},
        )
        assert not _denied(result)

    def test_symlinked_writable_dir_allowed(self, tmp_path):
        # src itself is a symlink; canonical prefix and target agree.
        proj = _project(tmp_path, mode="implement")
        real = tmp_path / "real_src"
        real.mkdir()
        (proj / "src").rmdir()
        (proj / "src").symlink_to(real)

        result = _before_call(proj, "Write", {"file_path": "src/foo.py"})
        assert not _denied(result)

    def test_exact_writable_file_allowed(self, tmp_path):
        proj = _project(tmp_path, mode="docs")
        result = _before_call(proj, "Write", {"file_path": "README.md"})
        assert not _denied(result)

    def test_free_mode_allows_everything(self, tmp_path):
        proj = _project(tmp_path, mode="free")
        result = _before_call(
            proj, "Write", {"file_path": "anywhere/at/all.py"},
        )
        assert not _denied(result)

    def test_readonly_mode_still_allows_readonly_bash(self, tmp_path):
        proj = _project(tmp_path, mode="explore")
        for command in ("git status", "ls -la src/", "cat src/foo.py"):
            result = _before_call(proj, "Bash", {"command": command})
            assert not _denied(result), command

    def test_bash_write_in_writable_allowed(self, tmp_path):
        proj = _project(tmp_path, mode="implement")
        result = _before_call(
            proj, "Bash", {"command": "echo hi > src/gen.txt"},
        )
        assert not _denied(result)

    def test_devnull_redirect_allowed(self, tmp_path):
        proj = _project(tmp_path, mode="implement")
        result = _before_call(
            proj, "Bash", {"command": "true 2>/dev/null"},
        )
        assert not _denied(result)

    def test_fd_dup_allowed(self, tmp_path):
        proj = _project(tmp_path, mode="implement")
        result = _before_call(
            proj, "Bash", {"command": "true 2>&1"},
        )
        assert not _denied(result)

    def test_denial_reason_still_actionable(self, tmp_path):
        proj = _project(tmp_path, mode="implement")
        result = _before_call(
            proj, "Write", {"file_path": "src/../tests/evil.py"},
        )
        assert _denied(result)
        assert "ChangeToolMode" in result.reason


class TestGuardUnits:
    """Unit-level checks of the canonicalize+decide functions."""

    def test_check_path_canonicalizes_against_project_dir(self, tmp_path):
        from kibitzer.guards.path_guard import check_path

        proj = _project(tmp_path)
        policy = {"writable": ["src/"], "strategy": ""}
        assert check_path("src/ok.py", policy, project_dir=proj).allowed
        assert not check_path(
            "src/../tests/evil.py", policy, project_dir=proj,
        ).allowed

    def test_check_path_never_raises(self, tmp_path):
        from kibitzer.guards import path_guard

        proj = _project(tmp_path)
        policy = {"writable": ["src/"], "strategy": ""}
        with patch.object(
            path_guard, "canonicalize", side_effect=OSError("boom"),
        ):
            result = path_guard.check_path(
                "src/ok.py", policy, project_dir=proj,
            )
        assert not result.allowed

    def test_check_path_malformed_policy_denies(self, tmp_path):
        from kibitzer.guards.path_guard import check_path

        proj = _project(tmp_path)
        result = check_path(
            "src/ok.py", {"writable": "src/"}, project_dir=proj,
        )
        assert not result.allowed

    def test_check_bash_unparseable_command_denies_when_restricted(
        self, tmp_path,
    ):
        from kibitzer.guards.path_guard import check_bash_writes

        proj = _project(tmp_path)
        policy = {"writable": ["src/"], "strategy": ""}
        result = check_bash_writes(
            'echo "unterminated > tests/x.py', policy, project_dir=proj,
        )
        assert not result.allowed

    def test_check_bash_unrestricted_skips_parse(self, tmp_path):
        from kibitzer.guards.path_guard import check_bash_writes

        proj = _project(tmp_path)
        policy = {"writable": ["*"], "strategy": ""}
        result = check_bash_writes(
            'echo "unterminated > anywhere', policy, project_dir=proj,
        )
        assert result.allowed

    def test_check_bash_mv_mutates_source_and_dest(self, tmp_path):
        from kibitzer.guards.path_guard import check_bash_writes

        proj = _project(tmp_path)
        policy = {"writable": ["src/"], "strategy": ""}
        # mv out of a protected dir mutates (deletes) the protected source.
        result = check_bash_writes(
            "mv tests/x.py src/x.py", policy, project_dir=proj,
        )
        assert not result.allowed
