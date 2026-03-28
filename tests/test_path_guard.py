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
