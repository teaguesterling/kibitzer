"""Tests for the mode-independent safety floor (guards/path_guard.check_floor).

The floor is a deny-list checked BEFORE any mode's writable set — even
`free` blocks secret dirs, system paths, raw .git internals, and writes
outside the repo. It must fail OPEN (missing repo detection, internal
exceptions) and never misfire on normal work (repo files, /tmp, the
scratchpad, .kibitzer/, memory dirs).
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from kibitzer.config import load_config
from kibitzer.guards.path_guard import check_floor, find_repo_root
from kibitzer.hooks.pre_tool_use import handle_pre_tool_use
from kibitzer.state import fresh_state, save_state
from kibitzer.store import KibitzerStore

DEFAULT_FLOOR = load_config()["floor"]


@pytest.fixture
def repo(tmp_path):
    """A fake git repo (a .git dir is all find_repo_root needs)."""
    r = tmp_path / "repo"
    (r / ".git").mkdir(parents=True)
    return r


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


def _floor(path, project, home, cfg=None, tmp_dirs=()):
    return check_floor(
        str(path), cfg or DEFAULT_FLOOR, project,
        home=home, tmp_dirs=list(tmp_dirs),
    )


class TestFindRepoRoot:
    def test_finds_git_dir(self, repo):
        assert find_repo_root(repo / "src" / "pkg") == repo

    def test_finds_git_worktree_file(self, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /elsewhere\n")
        assert find_repo_root(wt) == wt

    def test_none_outside_repo(self, tmp_path):
        d = tmp_path / "plain"
        d.mkdir()
        # tmp_path itself has no .git anywhere up to / (barring a rogue
        # .git in a parent of the pytest tmp dir)
        assert find_repo_root(d) is None


class TestFloorBlocks:
    def test_blocks_secret_dirs(self, repo, home):
        for d in (".ssh", ".aws", ".gnupg", ".config/gh"):
            r = _floor(home / d / "somefile", repo, home)
            assert not r.allowed, d
            assert r.rule == "secret-dir"

    def test_blocks_outside_repo(self, repo, home, tmp_path):
        r = _floor(tmp_path / "elsewhere" / "f.py", repo, home)
        assert not r.allowed
        assert r.rule == "outside-repo"

    def test_allows_sibling_repo(self, repo, home, tmp_path):
        # Multi-repo sessions write to sibling working trees constantly —
        # a target inside ANY git repo is deliberate dev work, not a stray.
        sibling = tmp_path / "sibling"
        (sibling / ".git").mkdir(parents=True)
        (sibling / "src").mkdir()
        r = _floor(sibling / "src" / "f.py", repo, home)
        assert r.allowed

    def test_blocks_system_paths(self, repo, home):
        for p in ("/etc/passwd", "/usr/local/bin/x", "/boot/grub/grub.cfg"):
            r = _floor(p, repo, home)
            assert not r.allowed, p
            assert r.rule == "system-path"

    def test_blocks_git_internals(self, repo, home):
        for rel in (".git/HEAD", ".git/refs/heads/main",
                    ".git/objects/ab/cdef01", ".git/packed-refs"):
            r = _floor(repo / rel, repo, home)
            assert not r.allowed, rel
            assert r.rule == "git-internals"

    def test_blocks_credential_files_under_home(self, repo, home):
        for name in ("credentials.json", "aws_credentials", "server.pem",
                     "id_rsa", "id_ed25519.pub"):
            r = _floor(home / "stuff" / name, repo, home)
            assert not r.allowed, name
            assert r.rule == "credential-file"

    def test_config_deny_pattern(self, repo, home):
        cfg = dict(DEFAULT_FLOOR, deny=["*/denyzone/*"])
        r = _floor(repo / "denyzone" / "x.py", repo, home, cfg=cfg)
        assert not r.allowed
        assert r.rule == "config-deny"

    def test_reason_names_rule_and_overrides(self, repo, home):
        r = _floor(home / ".ssh" / "key", repo, home)
        assert "secret-dir" in r.reason
        assert "[floor] allow" in r.reason
        assert "ChangeToolMode" in r.reason

    def test_symlink_into_secret_dir_resolved(self, repo, home):
        (home / ".ssh").mkdir()
        link = repo / "innocent"
        link.symlink_to(home / ".ssh")
        r = _floor(repo / "innocent" / "key", repo, home)
        assert not r.allowed
        assert r.rule == "secret-dir"


class TestFloorNonBlocks:
    def test_repo_file(self, repo, home):
        assert _floor(repo / "src" / "foo.py", repo, home).allowed

    def test_repo_relative_path(self, repo, home):
        assert _floor("src/foo.py", repo, home).allowed

    def test_kibitzer_dir(self, repo, home):
        assert _floor(repo / ".kibitzer" / "config.toml", repo, home).allowed

    def test_credentialish_name_inside_repo_allowed(self, repo, home):
        # repo files are exempt from the credential-filename rule
        assert _floor(repo / "src" / "credentials.py", repo, home).allowed

    def test_github_dir_is_not_git_internals(self, repo, home):
        assert _floor(repo / ".github" / "workflows" / "ci.yml", repo, home).allowed

    def test_tmp_allowed_by_default(self, repo, home):
        r = check_floor("/tmp/scratch/x.py", DEFAULT_FLOOR, repo, home=home)
        assert r.allowed

    def test_scratchpad_allowed(self, repo, home):
        r = check_floor(
            "/tmp/claude-1000/proj/session/scratchpad/notes.md",
            DEFAULT_FLOOR, repo, home=home,
        )
        assert r.allowed

    def test_memory_dir_allowed(self, repo, home):
        # default [floor] allow covers the Claude memory dir (~ expanded)
        r = _floor(
            home / ".claude" / "projects" / "-x-y" / "memory" / "MEMORY.md",
            repo, home,
        )
        assert r.allowed

    def test_inbox_allowed(self, repo, home):
        assert _floor(home / ".claude" / "inbox" / "note.md", repo, home).allowed

    def test_no_repo_fails_open_for_outside_rule(self, tmp_path, home):
        norepo = tmp_path / "norepo"
        norepo.mkdir()
        r = _floor(tmp_path / "elsewhere" / "f.py", norepo, home)
        assert r.allowed  # no repo root → outside-repo rule skipped

    def test_no_repo_still_blocks_secrets_and_system(self, tmp_path, home):
        norepo = tmp_path / "norepo"
        norepo.mkdir()
        assert _floor(home / ".ssh" / "key", norepo, home).rule == "secret-dir"
        assert _floor("/etc/hosts", norepo, home).rule == "system-path"

    def test_allow_pattern_overrides_rules(self, repo, home, tmp_path):
        cfg = dict(DEFAULT_FLOOR, allow=[str(tmp_path / "shared") + "/"])
        r = _floor(tmp_path / "shared" / "f.py", repo, home, cfg=cfg)
        assert r.allowed


def _project(tmp_path, mode="free", config_text=None, git=True):
    d = tmp_path / ".kibitzer"
    d.mkdir(exist_ok=True)
    if git:
        (tmp_path / ".git").mkdir(exist_ok=True)
    if config_text:
        (d / "config.toml").write_text(config_text)
    s = fresh_state()
    s["mode"] = mode
    save_state(s, d)
    return tmp_path


class TestFloorSessionIntegration:
    """The floor wired through KibitzerSession / the PreToolUse hook."""

    def test_free_mode_blocks_ssh_write(self, tmp_path):
        proj = _project(tmp_path)
        target = str(Path.home() / ".ssh" / "kibitzer_floor_probe")
        result = handle_pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": target, "content": "x"}},
            project_dir=proj,
        )
        assert result is not None
        out = result["hookSpecificOutput"]
        assert out["permissionDecision"] == "deny"
        assert "secret-dir" in out["permissionDecisionReason"]

    def test_free_mode_allows_repo_write(self, tmp_path):
        proj = _project(tmp_path)
        result = handle_pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "src/new.py", "content": "x"}},
            project_dir=proj,
        )
        assert result is None

    def test_free_mode_allows_tmp_write(self, tmp_path):
        proj = _project(tmp_path)
        result = handle_pre_tool_use(
            {"tool_name": "Write",
             "tool_input": {"file_path": "/tmp/kibitzer-scratch/x.txt", "content": "x"}},
            project_dir=proj,
        )
        assert result is None

    def test_implement_mode_allows_memory_write(self, tmp_path):
        proj = _project(tmp_path, mode="implement")
        target = str(
            Path.home() / ".claude" / "projects" / "-x" / "memory" / "MEMORY.md"
        )
        result = handle_pre_tool_use(
            {"tool_name": "Edit",
             "tool_input": {"file_path": target, "old_string": "a", "new_string": "b"}},
            project_dir=proj,
        )
        assert result is None

    def test_floor_block_logged_to_store(self, tmp_path):
        proj = _project(tmp_path)
        target = str(Path.home() / ".ssh" / "kibitzer_floor_probe")
        handle_pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": target, "content": "x"}},
            project_dir=proj,
        )
        store = KibitzerStore(proj / ".kibitzer" / "store.sqlite")
        events = store.query_events(event_type="floor_block")
        assert len(events) == 1
        data = json.loads(events[0]["data"])
        assert data["rule"] == "secret-dir"

    def test_floor_disabled_via_config(self, tmp_path):
        proj = _project(tmp_path, config_text="[floor]\nenabled = false\n")
        target = str(Path.home() / ".ssh" / "kibitzer_floor_probe")
        result = handle_pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": target, "content": "x"}},
            project_dir=proj,
        )
        assert result is None

    def test_explicit_mode_writable_overrides_floor(self, tmp_path):
        cfg = (
            "[floor]\n"
            'deny = ["*/denyzone/*"]\n'
            "\n"
            "[modes.deployer]\n"
            'writable = ["denyzone/"]\n'
            'strategy = ""\n'
        )
        proj = _project(tmp_path, mode="deployer", config_text=cfg)
        result = handle_pre_tool_use(
            {"tool_name": "Write",
             "tool_input": {"file_path": "denyzone/x.py", "content": "x"}},
            project_dir=proj,
        )
        assert result is None  # explicit writable entry outranks the floor

    def test_wildcard_writable_does_not_override_floor(self, tmp_path):
        cfg = '[floor]\ndeny = ["*/denyzone/*"]\n'
        proj = _project(tmp_path, mode="free", config_text=cfg)
        result = handle_pre_tool_use(
            {"tool_name": "Write",
             "tool_input": {"file_path": "denyzone/x.py", "content": "x"}},
            project_dir=proj,
        )
        assert result is not None
        out = result["hookSpecificOutput"]
        assert out["permissionDecision"] == "deny"
        assert "config-deny" in out["permissionDecisionReason"]

    def test_floor_exception_fails_open_and_logs(self, tmp_path):
        proj = _project(tmp_path)
        target = str(Path.home() / ".ssh" / "kibitzer_floor_probe")
        with patch(
            "kibitzer.session.check_floor", side_effect=RuntimeError("boom"),
        ):
            result = handle_pre_tool_use(
                {"tool_name": "Write",
                 "tool_input": {"file_path": target, "content": "x"}},
                project_dir=proj,
            )
        assert result is None  # fail OPEN — a broken floor must never block
        store = KibitzerStore(proj / ".kibitzer" / "store.sqlite")
        events = store.query_events(event_type="floor_error")
        assert len(events) == 1

    def test_non_write_tools_skip_floor(self, tmp_path):
        proj = _project(tmp_path)
        target = str(Path.home() / ".ssh" / "id_rsa")
        result = handle_pre_tool_use(
            {"tool_name": "Read", "tool_input": {"file_path": target}},
            project_dir=proj,
        )
        assert result is None


class TestFloorOnBashTargets:
    """Synthesis of the floor with Bash write-vector extraction: statically
    extracted Bash write targets are floor-checked (mode-independent, even
    in free mode) BEFORE the mode-level check_bash_writes runs."""

    def test_bash_redirect_to_ssh_floor_blocked(self, tmp_path):
        # `> ~/.ssh/...` floor-blocks just like a Write would, in free mode.
        proj = _project(tmp_path)
        result = handle_pre_tool_use(
            {"tool_name": "Bash",
             "tool_input": {"command": "echo x > ~/.ssh/kibitzer_floor_probe"}},
            project_dir=proj,
        )
        assert result is not None
        out = result["hookSpecificOutput"]
        assert out["permissionDecision"] == "deny"
        assert "secret-dir" in out["permissionDecisionReason"]
        store = KibitzerStore(proj / ".kibitzer" / "store.sqlite")
        events = store.query_events(event_type="floor_block")
        assert len(events) == 1
        assert events[0]["tool_name"] == "Bash"

    def test_bash_target_in_sibling_repo_allowed(self, tmp_path):
        # A bash write into ANY git working tree is deliberate multi-repo
        # work, not a stray write (tmp allowance disabled so the sibling-repo
        # rule itself is what allows it).
        (tmp_path / "proj").mkdir()
        proj = _project(tmp_path / "proj")
        sibling = tmp_path / "sibling"
        (sibling / ".git").mkdir(parents=True)
        with patch(
            "kibitzer.guards.path_guard._default_tmp_dirs", return_value=[],
        ):
            result = handle_pre_tool_use(
                {"tool_name": "Bash",
                 "tool_input": {"command": f"echo x > {sibling}/notes.txt"}},
                project_dir=proj,
            )
        # Interceptors may still attach an advisory nudge; the point is
        # that nothing DENIES the sibling-repo write.
        if result is not None:
            out = result["hookSpecificOutput"]
            assert out.get("permissionDecision") != "deny"

    def test_unknown_mode_fail_closed_while_floor_fails_open(self, tmp_path):
        # The two error philosophies coexist: a broken floor fails OPEN
        # (never blocks), while an unknown mode still fails CLOSED (denies)
        # at the mode-level check.
        proj = _project(tmp_path, mode="no-such-mode")
        with patch(
            "kibitzer.session.check_floor", side_effect=RuntimeError("boom"),
        ):
            result = handle_pre_tool_use(
                {"tool_name": "Write",
                 "tool_input": {"file_path": "src/x.py", "content": "x"}},
                project_dir=proj,
            )
        assert result is not None
        out = result["hookSpecificOutput"]
        assert out["permissionDecision"] == "deny"
        assert "read-only" in out["permissionDecisionReason"]
        store = KibitzerStore(proj / ".kibitzer" / "store.sqlite")
        assert len(store.query_events(event_type="floor_error")) == 1
