import json
import pytest
from pathlib import Path
from click.testing import CliRunner
from kibitzer.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


class TestInit:
    def test_init_creates_config(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            result = runner.invoke(cli, ["init"], catch_exceptions=False)
            assert result.exit_code == 0
            assert (Path(td) / ".kibitzer" / "config.toml").exists()
            assert (Path(td) / ".kibitzer" / "state.json").exists()

    def test_init_creates_hooks(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            result = runner.invoke(cli, ["init", "--hooks"], catch_exceptions=False)
            assert result.exit_code == 0
            assert (Path(td) / ".claude" / "hooks" / "kibitzer-pre.sh").exists()
            assert (Path(td) / ".claude" / "hooks" / "kibitzer-post.sh").exists()

    def test_init_creates_mcp_json(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            result = runner.invoke(cli, ["init", "--mcp"], catch_exceptions=False)
            assert result.exit_code == 0
            mcp_json = Path(td) / ".mcp.json"
            assert mcp_json.exists()
            data = json.loads(mcp_json.read_text())
            assert "mcpServers" in data
            assert "kibitzer" in data["mcpServers"]

    def test_init_merges_settings(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            claude_dir = Path(td) / ".claude"
            claude_dir.mkdir()
            existing = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "other-tool.sh"}]}]}}
            (claude_dir / "settings.json").write_text(json.dumps(existing))

            result = runner.invoke(cli, ["init", "--hooks"], catch_exceptions=False)
            assert result.exit_code == 0

            settings = json.loads((claude_dir / "settings.json").read_text())
            pre_hooks = settings["hooks"]["PreToolUse"]
            assert len(pre_hooks) >= 2

    def test_init_idempotent(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init", "--hooks"], catch_exceptions=False)
            result = runner.invoke(cli, ["init", "--hooks"], catch_exceptions=False)
            assert result.exit_code == 0
