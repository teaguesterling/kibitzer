from unittest.mock import patch
from kibitzer.interceptors.blq import BlqInterceptor
from kibitzer.interceptors.jetsam import JetsamInterceptor
from kibitzer.interceptors.fledgling import FledglingInterceptor
from kibitzer.interceptors.registry import build_registry


class TestBlqInterceptor:
    def setup_method(self):
        self.plugin = BlqInterceptor()

    def test_matches_pytest(self):
        result = self.plugin.check("pytest tests/ -v")
        assert result is not None
        assert "blq" in result.tool

    def test_matches_npm_test(self):
        result = self.plugin.check("npm test")
        assert result is not None

    def test_matches_cargo_test(self):
        result = self.plugin.check("cargo test")
        assert result is not None

    def test_no_match_on_random_command(self):
        result = self.plugin.check("ls -la")
        assert result is None

    def test_no_match_on_non_test_python(self):
        result = self.plugin.check("python setup.py install")
        assert result is None


class TestJetsamInterceptor:
    def setup_method(self):
        self.plugin = JetsamInterceptor()

    def test_matches_git_add_commit(self):
        result = self.plugin.check("git add -A && git commit -m 'fix'")
        assert result is not None
        assert "jetsam save" in result.tool

    def test_matches_git_push(self):
        result = self.plugin.check("git push origin main")
        assert result is not None
        assert "jetsam sync" in result.tool

    def test_matches_git_diff(self):
        result = self.plugin.check("git diff HEAD~1")
        assert result is not None

    def test_matches_git_log(self):
        result = self.plugin.check("git log --oneline -10")
        assert result is not None

    def test_no_match_on_non_git(self):
        result = self.plugin.check("echo hello")
        assert result is None

    def test_git_status_not_intercepted(self):
        result = self.plugin.check("git status")
        assert result is None


class TestFledglingInterceptor:
    def setup_method(self):
        self.plugin = FledglingInterceptor()

    def test_matches_grep_for_def(self):
        result = self.plugin.check("grep -rn 'def handle_request' src/")
        assert result is not None
        assert "FindDefinitions" in result.tool

    def test_matches_grep_for_class(self):
        result = self.plugin.check("grep -r 'class MyService' .")
        assert result is not None

    def test_matches_find_name(self):
        result = self.plugin.check("find . -name '*.py' -type f")
        assert result is not None
        assert "CodeStructure" in result.tool

    def test_no_match_on_content_grep(self):
        result = self.plugin.check("grep -r 'error_message' src/")
        assert result is None

    def test_no_match_on_random_command(self):
        result = self.plugin.check("ls -la")
        assert result is None


class TestRegistry:
    @patch("kibitzer.interceptors.registry.shutil.which")
    def test_all_tools_available(self, mock_which):
        mock_which.return_value = "/usr/bin/tool"
        plugins = build_registry()
        names = [p.name for p in plugins]
        assert "blq" in names
        assert "jetsam" in names
        assert "fledgling" in names

    @patch("kibitzer.interceptors.registry.shutil.which")
    def test_no_tools_available(self, mock_which):
        mock_which.return_value = None
        plugins = build_registry()
        assert len(plugins) == 0

    @patch("kibitzer.interceptors.registry.shutil.which")
    def test_partial_availability(self, mock_which):
        def side_effect(name):
            return "/usr/bin/jetsam" if name == "jetsam" else None
        mock_which.side_effect = side_effect
        plugins = build_registry()
        assert len(plugins) == 1
        assert plugins[0].name == "jetsam"
