from kibitzer.guards.path_guard import check_path


def _mode_config(writable):
    return {"writable": writable, "strategy": ""}


def test_allow_writable_prefix():
    result = check_path("src/foo/bar.py", _mode_config(["src/"]))
    assert result.allowed


def test_deny_non_writable():
    result = check_path("tests/test_foo.py", _mode_config(["src/"]))
    assert not result.allowed
    assert "tests/test_foo.py" in result.reason


def test_wildcard_allows_everything():
    result = check_path("anything/at/all.py", _mode_config(["*"]))
    assert result.allowed


def test_empty_writable_denies_everything():
    result = check_path("src/foo.py", _mode_config([]))
    assert not result.allowed


def test_multiple_prefixes():
    policy = _mode_config(["src/", "lib/"])
    assert check_path("src/foo.py", policy).allowed
    assert check_path("lib/bar.py", policy).allowed
    assert not check_path("tests/baz.py", policy).allowed


def test_exact_filename_match():
    policy = _mode_config(["docs/", "README.md"])
    assert check_path("README.md", policy).allowed
    assert check_path("docs/guide.md", policy).allowed
    assert not check_path("src/foo.py", policy).allowed


def test_reason_includes_mode_switch_hint():
    result = check_path("tests/test_foo.py", _mode_config(["src/"]))
    assert not result.allowed
    assert "ChangeToolMode" in result.reason


def test_free_mode_allows_all():
    result = check_path("anywhere/anything.py", _mode_config(["*"]))
    assert result.allowed


# --- Absolute / ~-prefixed writable entries (expanded at check time) ---


def test_absolute_entry_matches_subtree(tmp_path):
    policy = _mode_config([str(tmp_path / "data") + "/"])
    assert check_path(str(tmp_path / "data" / "f.csv"), policy).allowed
    assert not check_path(str(tmp_path / "other" / "f.csv"), policy).allowed


def test_tilde_entry_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    policy = _mode_config(["~/notes/"])
    assert check_path(str(tmp_path / "notes" / "todo.md"), policy).allowed
    assert not check_path(str(tmp_path / "elsewhere" / "todo.md"), policy).allowed


def test_memory_dir_glob_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    policy = _mode_config(["src/", "~/.claude/projects/*/memory/"])
    mem = tmp_path / ".claude" / "projects" / "-srv-x-y" / "memory" / "MEMORY.md"
    assert check_path(str(mem), policy).allowed
    other = tmp_path / ".claude" / "projects" / "-srv-x-y" / "notes.md"
    assert not check_path(str(other), policy).allowed


def test_absolute_file_path_matches_relative_entry(tmp_path):
    policy = _mode_config(["src/"])
    assert check_path(
        str(tmp_path / "src" / "foo.py"), policy, project_dir=tmp_path,
    ).allowed
    assert not check_path(
        str(tmp_path / "docs" / "foo.md"), policy, project_dir=tmp_path,
    ).allowed
