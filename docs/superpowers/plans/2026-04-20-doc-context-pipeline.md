# Doc Context Pipeline + Namespaces

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Give Kibitzer the ability to retrieve and refine documentation excerpts relevant to tool failures, using pluckit for mechanical retrieval and consumer-provided callbacks for judgment. Add namespace support to partition docs, tools, and failure patterns by domain.

**Architecture:** Three-layer pipeline owned by Kibitzer:

```
register_docs(doc_refs, docs_root, namespace=)
    |
get_doc_context(query, tool=, failure_mode=, namespace=, refinement=)
    |
+------------------------------------------+
|  1. RETRIEVE  (kibitzer, pluckit)        |  Plucker(docs=root).docs().filter(...)
|       -> candidate sections              |
|                                          |
|  2. SELECT    (callback, optional)       |  "which of these matter?"
|       -> filtered candidates             |
|                                          |
|  3. PRESENT   (callback, optional)       |  "how should these read?"
|       -> final excerpts                  |
+------------------------------------------+
```

Kibitzer provides sensible defaults (BM25/ILIKE ranking for retrieve, top-N for select, raw content for present). Consumers override select/present with domain heuristics or LLM-backed implementations.

**Key principles:**
- Kibitzer owns signal and retrieval; consumers own judgment and prompt text
- Namespaces partition everything (docs, tools, failure patterns) by domain — not "interpreter" (a lackpy concept)
- Pluckit is a soft dependency (graceful degradation to empty results)
- Callbacks are sync; consumers manage async externally if needed
- Callback errors are always swallowed — must not break the pipeline

**Tech Stack:** Python 3.10+, dataclasses, typing.Protocol, pluckit (optional), pytest

---

## File Structure

### Kibitzer side

| File | Responsibility |
|------|----------------|
| `src/kibitzer/docs.py` (create) | `DocRefinement` protocol, `DocSection` dataclass, `DocResult` result |
| `src/kibitzer/session.py` (modify) | `namespace` support, `register_docs()`, `get_doc_context()` pipeline |
| `tests/test_docs.py` (create) | Pipeline tests: retrieval, callbacks, namespace scoping |
| `tests/test_namespace.py` (create) | Namespace threading through existing APIs |
| `pyproject.toml` (modify) | Add `pluckit` optional dependency |

### Lackpy side (future — not implemented here)

| File | Responsibility |
|------|----------------|
| `src/lackpy/infer/distill.py` (create) | `select` and `present` callback implementations |
| `src/lackpy/service.py` (modify) | Wire `register_docs()` + callbacks at init |
| `src/lackpy/infer/correction.py` (modify) | Use doc context in correction chain |

---

## Task 1: Namespace support on KibitzerSession

**Files:**
- Modify: `src/kibitzer/session.py`
- Create: `tests/test_namespace.py`

Thread an optional `namespace` parameter through the session. All existing APIs gain it as an optional kwarg that defaults to the session-level namespace.

- [ ] **Step 1: Write tests for namespace basics**

```python
# tests/test_namespace.py
from kibitzer.session import KibitzerSession
from kibitzer.state import fresh_state, save_state


def _project(tmp_path):
    state_dir = tmp_path / ".kibitzer"
    state_dir.mkdir()
    save_state(fresh_state(), state_dir)
    return tmp_path


class TestNamespaceInit:
    def test_default_namespace_is_none(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            assert session.namespace is None

    def test_namespace_set_at_init(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj, namespace="python") as session:
            assert session.namespace == "python"

    def test_namespace_context_manager(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj, namespace="python") as session:
            assert session.namespace == "python"
            with session.ns("sql"):
                assert session.namespace == "sql"
            assert session.namespace == "python"


class TestNamespaceInReportGeneration:
    def test_namespace_stored_in_generation_event(self, tmp_path):
        import json
        from kibitzer.store import KibitzerStore

        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj, namespace="python") as session:
            session.report_generation({
                "intent": "read file",
                "success": False,
                "failure_mode": "stdlib_leak",
                "model": "qwen:3b",
            })

        store = KibitzerStore(tmp_path / ".kibitzer" / "store.sqlite")
        events = store.query_events(event_type="generation")
        data = json.loads(events[0]["data"])
        assert data.get("namespace") == "python"

    def test_explicit_namespace_overrides_session(self, tmp_path):
        import json
        from kibitzer.store import KibitzerStore

        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj, namespace="python") as session:
            session.report_generation({
                "intent": "query table",
                "success": False,
                "failure_mode": "syntax_artifact",
                "model": "qwen:3b",
            }, namespace="sql")

        store = KibitzerStore(tmp_path / ".kibitzer" / "store.sqlite")
        events = store.query_events(event_type="generation")
        data = json.loads(events[0]["data"])
        assert data.get("namespace") == "sql"


class TestNamespaceInFailurePatterns:
    def test_patterns_scoped_to_namespace(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.report_generation({
                "failure_mode": "stdlib_leak", "model": "qwen:3b",
                "intent": "a", "success": False, "namespace": "python",
            })
            session.report_generation({
                "failure_mode": "syntax_artifact", "model": "qwen:3b",
                "intent": "b", "success": False, "namespace": "sql",
            })
            # Unscoped: sees both
            all_patterns = session.get_failure_patterns()
            assert len(all_patterns) == 2

            # Scoped: sees only python
            py_patterns = session.get_failure_patterns(namespace="python")
            assert len(py_patterns) == 1
            assert py_patterns[0]["pattern"] == "stdlib_leak"
```

- [ ] **Step 2: Run tests, confirm they fail**

```bash
cd /mnt/aux-data/teague/Projects/kibitzer
/home/teague/.local/share/venv/bin/python -m pytest tests/test_namespace.py -v
```

- [ ] **Step 3: Implement namespace support**

In `src/kibitzer/session.py`:

1. Add `namespace` parameter to `__init__`:
   ```python
   def __init__(self, project_dir=None, safe_mode=False, namespace=None):
       ...
       self._namespace: str | None = namespace
   ```

2. Add `namespace` property and `ns()` context manager:
   ```python
   @property
   def namespace(self) -> str | None:
       return self._namespace

   @contextmanager
   def ns(self, namespace: str):
       previous = self._namespace
       self._namespace = namespace
       try:
           yield self
       finally:
           self._namespace = previous
   ```

3. Add `_resolve_namespace()` helper:
   ```python
   def _resolve_namespace(self, explicit: str | None = ...) -> str | None:
       """Return explicit namespace if given, else session default."""
       if explicit is not ...:
           return explicit
       return self._namespace
   ```

4. Update `report_generation()` to accept and store namespace:
   ```python
   def report_generation(self, report: dict[str, Any], namespace: str | None = ...) -> None:
       ns = self._resolve_namespace(namespace)
       if ns is not None:
           report = {**report, "namespace": ns}
       ...
   ```

5. Update `get_failure_patterns()` to filter by namespace:
   ```python
   def get_failure_patterns(self, model=None, window=50, namespace=None):
       ...
       # Inside the event loop, after parsing data:
       if namespace is not None:
           event_ns = data.get("namespace")
           if event_ns != namespace:
               continue
       ...
   ```

- [ ] **Step 4: Run tests, confirm they pass**
- [ ] **Step 5: Run full suite to check for regressions**

```bash
/home/teague/.local/share/venv/bin/python -m pytest tests/ -q
```

- [ ] **Step 6: Commit**

```bash
git add src/kibitzer/session.py tests/test_namespace.py
git commit -m "feat: namespace support on KibitzerSession"
```

---

## Task 2: Doc types and refinement protocol

**Files:**
- Create: `src/kibitzer/docs.py`
- Create: `tests/test_docs.py`

Define the data types for the doc context pipeline. No pluckit dependency yet — just the protocol and types.

- [ ] **Step 1: Write tests for types**

```python
# tests/test_docs.py
from kibitzer.docs import DocRefinement, DocSection, DocResult


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
        assert result.sections[0].title == "read_file"

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
            DocSection(title="read_file", content="...",
                       file_path="a.md"),
            DocSection(title="edit_file", content="...",
                       file_path="b.md"),
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
```

- [ ] **Step 2: Run tests, confirm they fail**

- [ ] **Step 3: Implement types**

```python
# src/kibitzer/docs.py
"""Doc context pipeline types.

Kibitzer retrieves documentation sections via pluckit, then optionally
refines them through consumer-provided callbacks. The pipeline:
    retrieve (pluckit) -> select (callback) -> present (callback)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class DocSection:
    """A single documentation section retrieved from pluckit."""
    title: str
    content: str
    file_path: str
    level: int = 1
    tool: str | None = None


@dataclass
class DocResult:
    """Result of the doc context pipeline."""
    sections: list[DocSection] = field(default_factory=list)


SelectCallback = Callable[[list[DocSection], dict[str, Any]], list[DocSection]]
PresentCallback = Callable[[list[DocSection], dict[str, Any]], list[DocSection]]


@dataclass
class DocRefinement:
    """Consumer-provided callbacks for the select and present steps.

    Both are optional. When omitted, kibitzer uses defaults:
    - select: top-N by retrieval ranking
    - present: raw section content
    """
    select: SelectCallback | None = None
    present: PresentCallback | None = None
```

- [ ] **Step 4: Run tests, confirm they pass**
- [ ] **Step 5: Commit**

```bash
git add src/kibitzer/docs.py tests/test_docs.py
git commit -m "feat: doc pipeline types — DocSection, DocResult, DocRefinement"
```

---

## Task 3: register_docs() and get_doc_context() on KibitzerSession

**Files:**
- Modify: `src/kibitzer/session.py`
- Modify: `tests/test_docs.py` (extend)
- Modify: `pyproject.toml` (add pluckit optional dep)

The core pipeline. `register_docs()` stores doc references per namespace. `get_doc_context()` retrieves via pluckit and runs the refinement pipeline.

- [ ] **Step 1: Add pluckit optional dependency**

In `pyproject.toml`, add:
```toml
[project.optional-dependencies]
# ...existing...
pluckit = [
    "pluckit>=0.3.0",
]
```

- [ ] **Step 2: Write tests for register_docs and get_doc_context**

```python
# tests/test_docs.py (extend)
import os
from kibitzer.session import KibitzerSession
from kibitzer.state import fresh_state, save_state
from kibitzer.docs import DocRefinement, DocSection


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


class TestGetDocContext:
    def test_retrieves_relevant_sections(self, tmp_path):
        pytest = __import__("pytest")
        try:
            import pluckit
        except ImportError:
            pytest.skip("pluckit not installed")

        proj = _project(tmp_path)
        doc_refs = _write_tool_docs(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.register_docs(doc_refs, docs_root=str(tmp_path))
            result = session.get_doc_context("read file contents")
            assert len(result.sections) > 0
            files = {s.file_path for s in result.sections}
            assert any("read_file" in f for f in files)

    def test_tool_filter_narrows_results(self, tmp_path):
        pytest = __import__("pytest")
        try:
            import pluckit
        except ImportError:
            pytest.skip("pluckit not installed")

        proj = _project(tmp_path)
        doc_refs = _write_tool_docs(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.register_docs(doc_refs, docs_root=str(tmp_path))
            result = session.get_doc_context("signature", tool="edit_file")
            for s in result.sections:
                assert "edit_file" in s.file_path

    def test_returns_empty_without_pluckit(self, tmp_path, monkeypatch):
        proj = _project(tmp_path)
        doc_refs = _write_tool_docs(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.register_docs(doc_refs, docs_root=str(tmp_path))
            import builtins
            real_import = builtins.__import__
            def mock_import(name, *args, **kwargs):
                if name == "pluckit":
                    raise ImportError("no pluckit")
                return real_import(name, *args, **kwargs)
            monkeypatch.setattr(builtins, "__import__", mock_import)
            result = session.get_doc_context("anything")
            assert result.sections == []

    def test_select_callback_filters(self, tmp_path):
        pytest = __import__("pytest")
        try:
            import pluckit
        except ImportError:
            pytest.skip("pluckit not installed")

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
        pytest = __import__("pytest")
        try:
            import pluckit
        except ImportError:
            pytest.skip("pluckit not installed")

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

    def test_namespace_scopes_retrieval(self, tmp_path):
        pytest = __import__("pytest")
        try:
            import pluckit
        except ImportError:
            pytest.skip("pluckit not installed")

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
```

- [ ] **Step 3: Run tests, confirm they fail**

- [ ] **Step 4: Implement register_docs and get_doc_context**

In `src/kibitzer/session.py`:

1. Add to `__init__`:
   ```python
   self._doc_registry: dict[str | None, dict] = {}
   # keyed by namespace -> {"refs": {tool: path}, "root": str, "refinement": ...}
   ```

2. Add `register_docs()`:
   ```python
   def register_docs(
       self,
       doc_refs: dict[str, str | None],
       docs_root: str | None = None,
       namespace: str | None = ...,
       refinement: DocRefinement | None = None,
   ) -> None:
       ns = self._resolve_namespace(namespace)
       self._doc_registry[ns] = {
           "refs": {k: v for k, v in doc_refs.items() if v},
           "root": docs_root,
           "refinement": refinement,
       }
   ```

3. Add `doc_refs` / `doc_refs_for()` accessors:
   ```python
   @property
   def doc_refs(self) -> dict[str, str]:
       ns = self._resolve_namespace()
       entry = self._doc_registry.get(ns, {})
       return entry.get("refs", {})

   def doc_refs_for(self, namespace: str) -> dict[str, str]:
       entry = self._doc_registry.get(namespace, {})
       return entry.get("refs", {})
   ```

4. Add `get_doc_context()` — the pipeline:
   ```python
   def get_doc_context(
       self,
       query: str,
       tool: str | None = None,
       failure_mode: str | None = None,
       namespace: str | None = ...,
       refinement: DocRefinement | None = None,
       limit: int = 5,
   ) -> DocResult:
       from kibitzer.docs import DocResult

       ns = self._resolve_namespace(namespace)
       registry = self._doc_registry.get(ns)
       if not registry:
           return DocResult()

       # --- Step 1: RETRIEVE (mechanical, pluckit) ---
       candidates = self._retrieve_doc_sections(
           query, registry, tool=tool,
       )

       # Use registered refinement as default, allow override
       effective_refinement = refinement or registry.get("refinement")

       # --- Step 2: SELECT (callback or default top-N) ---
       context = {
           "query": query, "tool": tool,
           "failure_mode": failure_mode, "namespace": ns,
       }
       if effective_refinement and effective_refinement.select:
           try:
               candidates = effective_refinement.select(candidates, context)
           except Exception:
               pass

       candidates = candidates[:limit]

       # --- Step 3: PRESENT (callback or raw) ---
       if effective_refinement and effective_refinement.present:
           try:
               candidates = effective_refinement.present(candidates, context)
           except Exception:
               pass

       return DocResult(sections=candidates)
   ```

5. Add `_retrieve_doc_sections()` helper:
   ```python
   def _retrieve_doc_sections(self, query, registry, tool=None):
       from kibitzer.docs import DocSection
       try:
           from pluckit import Plucker
       except ImportError:
           return []

       docs_root = registry.get("root")
       if not docs_root:
           return []

       try:
           p = Plucker(docs=f"{docs_root}/**/*.md")
           docs = p.docs()

           if tool:
               doc_path = registry["refs"].get(tool)
               if doc_path:
                   docs = docs.filter(file_path=doc_path)

           if query:
               docs = docs.filter(search=query)

           raw_sections = docs.sections()
       except Exception:
           return []

       return [
           DocSection(
               title=s.get("title", ""),
               content=str(s.get("content", "")),
               file_path=s.get("file_path", ""),
               level=s.get("level", 1),
               tool=tool,
           )
           for s in raw_sections
       ]
   ```

- [ ] **Step 5: Run tests, confirm they pass**
- [ ] **Step 6: Run full suite**
- [ ] **Step 7: Commit**

```bash
git add src/kibitzer/docs.py src/kibitzer/session.py tests/test_docs.py pyproject.toml
git commit -m "feat: doc context pipeline with register_docs() and get_doc_context()"
```

---

## Task 4: Wire doc context into get_correction_hints

**Files:**
- Modify: `src/kibitzer/session.py`
- Modify: `tests/test_session_lackpy.py` (extend)

When `get_correction_hints()` is called and docs are registered, include relevant doc excerpts in the signal.

- [ ] **Step 1: Write tests**

```python
# tests/test_session_lackpy.py (extend)

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
        pytest = __import__("pytest")
        try:
            import pluckit
        except ImportError:
            pytest.skip("pluckit not installed")

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
        pytest = __import__("pytest")
        try:
            import pluckit
        except ImportError:
            pytest.skip("pluckit not installed")

        from kibitzer.docs import DocRefinement, DocSection

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
```

- [ ] **Step 2: Run tests, confirm they fail**

- [ ] **Step 3: Implement**

Extend `get_correction_hints()` signature with `tool` and `namespace` parameters. After building the result dict, call `get_doc_context()` when docs are registered:

```python
def get_correction_hints(
    self,
    failure_mode: str,
    model: str | None = None,
    attempt: int = 1,
    tool: str | None = None,
    namespace: str | None = ...,
) -> dict[str, Any]:
    ...
    # After building result dict, before return:
    ns = self._resolve_namespace(namespace)
    if ns in self._doc_registry or None in self._doc_registry:
        doc_result = self.get_doc_context(
            query=failure_mode.replace("_", " "),
            tool=tool,
            failure_mode=failure_mode,
            namespace=namespace,
            limit=3,
        )
        if doc_result.sections:
            result["doc_context"] = [
                {"title": s.title, "content": s.content, "file": s.file_path}
                for s in doc_result.sections
            ]

    return result
```

- [ ] **Step 4: Run tests, confirm they pass**
- [ ] **Step 5: Run full suite**
- [ ] **Step 6: Commit**

```bash
git add src/kibitzer/session.py tests/test_session_lackpy.py
git commit -m "feat: doc context in get_correction_hints signal"
```

---

## Task 5: Thread namespace through remaining APIs

**Files:**
- Modify: `src/kibitzer/session.py`
- Modify: `tests/test_namespace.py` (extend)

Add `namespace` parameter to `get_prompt_hints()`, `register_tools()`, and `register_context()`.

- [ ] **Step 1: Write tests for namespace in prompt hints and tools**

```python
# tests/test_namespace.py (extend)

class TestNamespaceInPromptHints:
    def test_prompt_hints_scoped_by_namespace(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            for _ in range(3):
                session.report_generation({
                    "failure_mode": "stdlib_leak", "model": "qwen:3b",
                    "intent": "read", "success": False,
                }, namespace="python")
            for _ in range(3):
                session.report_generation({
                    "failure_mode": "syntax_artifact", "model": "qwen:3b",
                    "intent": "query", "success": False,
                }, namespace="sql")

            py_hints = session.get_prompt_hints(
                model="qwen:3b", namespace="python", min_confidence=0.0,
            )
            sql_hints = session.get_prompt_hints(
                model="qwen:3b", namespace="sql", min_confidence=0.0,
            )

            py_sources = {h["source"] for h in py_hints}
            sql_sources = {h["source"] for h in sql_hints}

            assert "failure_pattern:stdlib_leak" in py_sources
            assert "failure_pattern:stdlib_leak" not in sql_sources
            assert "failure_pattern:syntax_artifact" in sql_sources


class TestNamespaceInTools:
    def test_register_tools_with_namespace(self, tmp_path):
        proj = _project(tmp_path)
        with KibitzerSession(project_dir=proj) as session:
            session.register_tools(
                [{"name": "read_file", "grade": (0, 0)}],
                namespace="python",
            )
            session.register_tools(
                [{"name": "query", "grade": (1, 0)}],
                namespace="sql",
            )
            session.register_tools(
                [{"name": "Bash", "grade": (4, 4)}],
            )
```

- [ ] **Step 2: Run tests, confirm they fail**
- [ ] **Step 3: Thread namespace through remaining APIs**
- [ ] **Step 4: Run tests, confirm they pass**
- [ ] **Step 5: Full suite**
- [ ] **Step 6: Commit**

```bash
git add src/kibitzer/session.py tests/test_namespace.py
git commit -m "feat: namespace threading through all lackpy integration APIs"
```

---

## Task 6: Export and version bump

**Files:**
- Modify: `src/kibitzer/__init__.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Update exports**

```python
# src/kibitzer/__init__.py
__version__ = "0.4.0"

from kibitzer.docs import DocRefinement, DocResult, DocSection
from kibitzer.failure_modes import ALL_MODES as FAILURE_MODES
from kibitzer.session import CallResult, KibitzerSession

__all__ = [
    "KibitzerSession", "CallResult", "FAILURE_MODES",
    "DocRefinement", "DocResult", "DocSection",
    "__version__",
]
```

- [ ] **Step 2: Bump pyproject.toml version to 0.4.0**
- [ ] **Step 3: Run full test suite**
- [ ] **Step 4: Commit and tag**

```bash
git add src/kibitzer/__init__.py pyproject.toml
git commit -m "release: v0.4.0 — doc context pipeline + namespaces"
git tag v0.4.0
```

---

## Comparison with prior plan (2026-04-20-distillation-callback.md in lackpy)

The earlier plan proposed a single `DistillCallback` protocol where lackpy owned both doc retrieval and hint construction. This plan supersedes it:

| Aspect | Prior plan | This plan |
|--------|-----------|-----------|
| Doc retrieval | Lackpy reads docs via `resolve_doc()` inside callback | Kibitzer retrieves via pluckit — mechanical, ranked |
| Pipeline shape | Single monolithic callback | Three steps: retrieve → select → present |
| Pluckit dependency | None (lackpy reads files directly) | Kibitzer (soft dep, graceful degradation) |
| Namespaces | Not addressed | First-class, threaded through all APIs |
| Interpreter awareness | Python-centric heuristics in `_pick_relevant_tool` | Domain-agnostic via namespaces |
| Callback granularity | One callable does everything | Two optional callbacks (select, present) |
| Kibitzer capability | Dumb pass-through | Gains doc search for coaching too |

The `DistillContext` and `DistillResult` shapes from the prior plan are subsumed by `DocSection`, `DocResult`, and the pipeline context dict. Lackpy's implementation (future work) provides `select`/`present` callbacks via `DocRefinement` instead of a monolithic `DistillCallback`.

---

## Future: Lackpy Integration (not in this plan)

These tasks live in the lackpy repo and depend on kibitzer v0.4.0:

1. **Wire `register_docs()`** in `_init_kibitzer()`: call `session.register_docs(self.docs_index()["tool_docs"], str(self._workspace), namespace=interpreter_name)`
2. **Provide select/present callbacks** via `DocRefinement` with lackpy's domain knowledge
3. **Use doc context in correction chain** — `get_correction_hints(tool=failed_tool)` now returns `doc_context`
4. **FTS upgrade** — when fledgling is available, use `DocSelection.search()` (BM25) instead of `filter(search=)` for better ranking
