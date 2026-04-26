"""Tests for the doc context pipeline."""

import pytest

from kibitzer.docs import DocRefinement, DocResult, DocSection
from kibitzer.session import KibitzerSession
from kibitzer.state import fresh_state, save_state


def _project(tmp_path):
    state_dir = tmp_path / ".kibitzer"
    state_dir.mkdir()
    save_state(fresh_state(), state_dir)
    return tmp_path


def _write_tool_docs(root):
    """Create sample tool doc files on disk."""
    docs_dir = root / "docs" / "tools"
    docs_dir.mkdir(parents=True)
    (docs_dir / "read_file.md").write_text(
        "# read_file\n\n"
        "Read a file from the workspace.\n\n"
        "## Signature\n\n"
        "```python\n"
        "read_file(path: str) -> str\n"
        "```\n\n"
        "## Parameters\n\n"
        "- **path**: Relative path to the file.\n\n"
        "## Notes\n\n"
        "- Raises FileNotFoundError if path does not exist.\n"
        "- All paths are relative to the workspace root.\n"
    )
    (docs_dir / "edit_file.md").write_text(
        "# edit_file\n\n"
        "Replace text in a file.\n\n"
        "## Signature\n\n"
        "```python\n"
        "edit_file(path: str, old_str: str, new_str: str) -> bool\n"
        "```\n\n"
        "## Parameters\n\n"
        "- **path**: File to edit.\n"
        "- **old_str**: Text to find.\n"
        "- **new_str**: Replacement text.\n"
    )
    return {
        "read_file": "docs/tools/read_file.md",
        "edit_file": "docs/tools/edit_file.md",
    }


# --- Type tests (no pluckit needed) ---

class TestDocSection:
    def test_required_fields(self):
        s = DocSection(
            title="read_file",
            content="Reads a file and returns contents.",
            file_path="docs/tools/read_file.md",
        )
        assert s.title == "read_file"
        assert s.content == "Reads a file and returns contents."

    def test_optional_fields(self):
        s = DocSection(
            title="Signature",
            content="read_file(path: str) -> str",
            file_path="docs/tools/read_file.md",
            level=2,
            tool="read_file",
        )
        assert s.level == 2
        assert s.tool == "read_file"


class TestDocResult:
    def test_from_sections(self):
        sections = [
            DocSection(title="read_file", content="Reads a file.",
                       file_path="docs/tools/read_file.md"),
        ]
        result = DocResult(sections=sections)
        assert len(result.sections) == 1

    def test_empty_result(self):
        result = DocResult(sections=[])
        assert result.sections == []


class TestDocRefinement:
    def test_defaults_to_none(self):
        r = DocRefinement()
        assert r.select is None
        assert r.present is None

    def test_select_callback(self):
        def my_select(candidates, context):
            return [c for c in candidates if "read" in c.title.lower()]

        r = DocRefinement(select=my_select)
        sections = [
            DocSection(title="read_file", content="...", file_path="a.md"),
            DocSection(title="edit_file", content="...", file_path="b.md"),
        ]
        result = r.select(sections, {})
        assert len(result) == 1

    def test_present_callback(self):
        def my_present(candidates, context):
            return [
                DocSection(
                    title=c.title, content=f"USE: {c.content}",
                    file_path=c.file_path,
                )
                for c in candidates
            ]

        r = DocRefinement(present=my_present)
        sections = [
            DocSection(title="read_file", content="read_file(path) -> str",
                       file_path="a.md"),
        ]
        result = r.present(sections, {})
        assert result[0].content.startswith("USE:")


# --- Registration tests ---

class TestRegisterDocs:
    def test_register_and_query(self, tmp_path):
        proj = _project(tmp_path)
        doc_refs = _write_tool_docs(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.register_docs(doc_refs, docs_root=str(tmp_path))
            assert session.doc_refs == doc_refs

    def test_register_with_namespace(self, tmp_path):
        proj = _project(tmp_path)
        doc_refs = _write_tool_docs(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.register_docs(doc_refs, docs_root=str(tmp_path),
                                  namespace="python")
            session.register_docs({"query": "docs/sql/query.md"},
                                  docs_root=str(tmp_path),
                                  namespace="sql")
            assert session.doc_refs_for("python") == doc_refs
            assert "query" in session.doc_refs_for("sql")

    def test_not_persisted(self, tmp_path):
        proj = _project(tmp_path)
        doc_refs = _write_tool_docs(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.register_docs(doc_refs, docs_root=str(tmp_path))
        with KibitzerSession(project_dir=proj) as session:
            assert session.doc_refs == {}


# --- Pipeline tests (require pluckit) ---

def _has_pluckit():
    try:
        import pluckit
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _has_pluckit(), reason="pluckit not installed")
class TestGetDocContext:
    def test_retrieves_relevant_sections(self, tmp_path):
        proj = _project(tmp_path)
        doc_refs = _write_tool_docs(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.register_docs(doc_refs, docs_root=str(tmp_path))
            result = session.get_doc_context("read_file")
            assert len(result.sections) > 0
            files = {s.file_path for s in result.sections}
            assert any("read_file" in f for f in files)

    def test_tool_filter_narrows_results(self, tmp_path):
        proj = _project(tmp_path)
        doc_refs = _write_tool_docs(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.register_docs(doc_refs, docs_root=str(tmp_path))
            result = session.get_doc_context("signature", tool="edit_file")
            for s in result.sections:
                assert "edit_file" in s.file_path

    def test_select_callback_filters(self, tmp_path):
        proj = _project(tmp_path)
        doc_refs = _write_tool_docs(tmp_path)

        def only_signatures(candidates, context):
            return [c for c in candidates if "signature" in c.title.lower()]

        with KibitzerSession(project_dir=proj) as session:
            session.register_docs(doc_refs, docs_root=str(tmp_path))
            refinement = DocRefinement(select=only_signatures)
            result = session.get_doc_context(
                "read_file", refinement=refinement,
            )
            for s in result.sections:
                assert "signature" in s.title.lower()

    def test_present_callback_transforms(self, tmp_path):
        proj = _project(tmp_path)
        doc_refs = _write_tool_docs(tmp_path)

        def prefix_content(candidates, context):
            return [
                DocSection(
                    title=c.title,
                    content=f"[{context.get('failure_mode', '?')}] {c.content}",
                    file_path=c.file_path, level=c.level, tool=c.tool,
                )
                for c in candidates
            ]

        with KibitzerSession(project_dir=proj) as session:
            session.register_docs(doc_refs, docs_root=str(tmp_path))
            refinement = DocRefinement(present=prefix_content)
            result = session.get_doc_context(
                "read", failure_mode="stdlib_leak",
                refinement=refinement,
            )
            for s in result.sections:
                assert s.content.startswith("[stdlib_leak]")

    def test_empty_without_registration(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            result = session.get_doc_context("anything")
            assert result.sections == []

    def test_namespace_scopes_retrieval(self, tmp_path):
        proj = _project(tmp_path)
        doc_refs = _write_tool_docs(tmp_path)

        sql_root = tmp_path / "sql_docs"
        sql_docs_dir = sql_root / "docs"
        sql_docs_dir.mkdir(parents=True)
        (sql_docs_dir / "query.md").write_text(
            "# query\n\nExecute a SQL query.\n"
        )

        with KibitzerSession(project_dir=proj) as session:
            session.register_docs(doc_refs, docs_root=str(tmp_path),
                                  namespace="python")
            session.register_docs({"query": "docs/query.md"},
                                  docs_root=str(sql_root),
                                  namespace="sql")

            py_result = session.get_doc_context("read", namespace="python")
            sql_result = session.get_doc_context("query", namespace="sql")

            py_files = {s.file_path for s in py_result.sections}
            sql_files = {s.file_path for s in sql_result.sections}

            assert any("read_file" in f for f in py_files)
            assert not any("query" in f for f in py_files)

    def test_callback_error_swallowed(self, tmp_path):
        proj = _project(tmp_path)
        doc_refs = _write_tool_docs(tmp_path)

        def broken_select(candidates, context):
            raise RuntimeError("boom")

        with KibitzerSession(project_dir=proj) as session:
            session.register_docs(doc_refs, docs_root=str(tmp_path))
            refinement = DocRefinement(select=broken_select)
            result = session.get_doc_context(
                "read", refinement=refinement,
            )
            assert isinstance(result.sections, list)


# --- Correction hints with docs ---

@pytest.mark.skipif(not _has_pluckit(), reason="pluckit not installed")
class TestCorrectionHintsWithDocs:
    def _write_docs(self, root):
        docs_dir = root / "docs" / "tools"
        docs_dir.mkdir(parents=True)
        (docs_dir / "read_file.md").write_text(
            "# read_file\n\nRead file contents.\n\n"
            "## Signature\n\n```python\n"
            "read_file(path: str) -> str\n```\n"
        )
        return {"read_file": "docs/tools/read_file.md"}

    def test_correction_hints_include_doc_context(self, tmp_path):
        proj = _project(tmp_path)
        doc_refs = self._write_docs(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.register_docs(doc_refs, docs_root=str(tmp_path))
            signal = session.get_correction_hints(
                failure_mode="stdlib_leak",
                tool="read_file",
            )
            assert "doc_context" in signal
            assert len(signal["doc_context"]) > 0

    def test_correction_hints_without_docs_has_no_context(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            signal = session.get_correction_hints(
                failure_mode="stdlib_leak",
            )
            assert signal.get("doc_context") is None

    def test_refinement_applied_to_correction_docs(self, tmp_path):
        proj = _project(tmp_path)
        doc_refs = self._write_docs(tmp_path)

        def sig_only(candidates, ctx):
            return [c for c in candidates if "signature" in c.title.lower()]

        with KibitzerSession(project_dir=proj) as session:
            session.register_docs(
                doc_refs, docs_root=str(tmp_path),
                refinement=DocRefinement(select=sig_only),
            )
            signal = session.get_correction_hints(
                failure_mode="stdlib_leak",
                tool="read_file",
            )
            for section in (signal.get("doc_context") or []):
                assert "signature" in section["title"].lower()


# --- Config-based doc registration ---

class TestDocsFromConfig:
    def test_loads_docs_from_config(self, tmp_path):
        """Docs declared in [docs] config section are auto-registered."""
        proj = _project(tmp_path)
        doc_refs = _write_tool_docs(tmp_path)
        # Write a project config with [docs] section
        cfg_dir = tmp_path / ".kibitzer"
        cfg_dir.mkdir(exist_ok=True)
        import tomli_w  # noqa: F811 — only needed for writing TOML
        pytest.importorskip("tomli_w", reason="tomli_w not installed")
        with open(cfg_dir / "config.toml", "wb") as f:
            tomli_w.dump({
                "docs": {
                    "root": ".",
                    "refs": doc_refs,
                }
            }, f)
        with KibitzerSession(project_dir=proj) as session:
            assert session.doc_refs == doc_refs

    def test_no_docs_section_is_fine(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            assert session.doc_refs == {}


# --- PostToolUse doc injection on failure ---

@pytest.mark.skipif(not _has_pluckit(), reason="pluckit not installed")
class TestDocHintOnFailure:
    def test_failure_injects_doc_hint(self, tmp_path):
        proj = _project(tmp_path)
        doc_refs = _write_tool_docs(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.register_docs(doc_refs, docs_root=str(tmp_path))
            result = session.after_call(
                "edit_file",
                {"file_path": "src/foo.py", "old_str": "x", "new_str": "y"},
                success=False,
                tool_result={"error": "old_str not found in file"},
            )
            assert result is not None
            assert "[kibitzer] Relevant docs for edit_file" in result.context

    def test_success_no_doc_hint(self, tmp_path):
        proj = _project(tmp_path)
        doc_refs = _write_tool_docs(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.register_docs(doc_refs, docs_root=str(tmp_path))
            result = session.after_call(
                "edit_file",
                {"file_path": "src/foo.py"},
                success=True,
                tool_result="ok",
            )
            # On success, no doc hint (may be None or have coach messages only)
            if result is not None:
                assert "Relevant docs" not in result.context

    def test_no_docs_registered_no_hint(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            result = session.after_call(
                "edit_file",
                {"file_path": "src/foo.py"},
                success=False,
                tool_result={"error": "not found"},
            )
            if result is not None:
                assert "Relevant docs" not in result.context


# --- MCP GetDocContext tool ---

class TestContext7Fallback:
    def test_falls_back_to_context7_when_no_local_docs(self, tmp_path):
        """When no local docs match, Context7 is queried as fallback."""
        proj = _project(tmp_path)
        from unittest.mock import patch

        mock_sections = [
            {"title": "Redis Connection", "content": "Use Redis(url=...)", "source": "https://docs.example.com"},
        ]
        with KibitzerSession(project_dir=proj) as session:
            session.register_docs({"Read": "docs/read.md"}, docs_root=str(tmp_path))
            with patch("kibitzer.session.KibitzerSession._retrieve_from_context7") as mock_c7:
                from kibitzer.docs import DocSection
                mock_c7.return_value = [
                    DocSection(title="Redis Connection", content="Use Redis(url=...)",
                               file_path="context7", level=1),
                ]
                result = session.get_doc_context("redis connection error")
                mock_c7.assert_called_once()
                assert len(result.sections) > 0
                assert result.sections[0].title == "Redis Connection"

    def test_no_context7_when_local_docs_match(self, tmp_path):
        pytest.importorskip("pluckit", reason="pluckit not installed")
        proj = _project(tmp_path)
        doc_refs = _write_tool_docs(tmp_path)
        from unittest.mock import patch

        with KibitzerSession(project_dir=proj) as session:
            session.register_docs(doc_refs, docs_root=str(tmp_path))
            with patch("kibitzer.session.KibitzerSession._retrieve_from_context7") as mock_c7:
                result = session.get_doc_context("signature", tool="read_file")
                mock_c7.assert_not_called()
                assert len(result.sections) > 0

    def test_context7_disabled_in_config(self, tmp_path):
        proj = _project(tmp_path)
        from unittest.mock import patch

        with KibitzerSession(project_dir=proj) as session:
            session._config.setdefault("docs", {})["context7"] = False
            session.register_docs({"Read": "docs/read.md"}, docs_root=str(tmp_path))
            with patch("kibitzer.session.KibitzerSession._retrieve_from_context7") as mock_c7:
                session.get_doc_context("redis connection")
                mock_c7.assert_not_called()

    def test_context7_works_without_any_registration(self, tmp_path):
        """Context7 should work even when no local docs are registered."""
        proj = _project(tmp_path)
        from unittest.mock import patch
        from kibitzer.docs import DocSection

        with KibitzerSession(project_dir=proj) as session:
            with patch("kibitzer.session.KibitzerSession._retrieve_from_context7") as mock_c7:
                mock_c7.return_value = [
                    DocSection(title="Help", content="content",
                               file_path="context7", level=1),
                ]
                result = session.get_doc_context("fastapi middleware")
                assert len(result.sections) > 0


class TestMcpGetDocContext:
    def test_returns_sections(self, tmp_path):
        pytest.importorskip("pluckit", reason="pluckit not installed")
        proj = _project(tmp_path)
        doc_refs = _write_tool_docs(tmp_path)
        from kibitzer.mcp.server import get_doc_context
        # Direct Python caller path
        with KibitzerSession(project_dir=proj) as session:
            session.register_docs(doc_refs, docs_root=str(tmp_path))
            result = session.get_doc_context("signature", tool="read_file")
        assert len(result.sections) > 0

    def test_no_docs_returns_empty(self, tmp_path):
        from kibitzer.mcp.server import get_doc_context
        result = get_doc_context("anything", project_dir=tmp_path)
        assert result["sections"] == []
