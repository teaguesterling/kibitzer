"""Context7 provider — external library documentation via context7.com REST API.

Two-step lookup:
    1. Search for a library ID by name
    2. Fetch documentation sections for that library + query

Falls back silently when the network is unavailable or the API
returns no results. All calls have a 5-second timeout.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_BASE_URL = "https://context7.com/api/v2"
_TIMEOUT = 5


def search_library(name: str) -> str | None:
    """Resolve a library name to a Context7 library ID.

    Returns the best-match library ID, or None if not found.
    """
    params = urllib.parse.urlencode({"query": name})
    url = f"{_BASE_URL}/libs/search?{params}"
    data = _get_json(url)
    if not data:
        return None
    results = data if isinstance(data, list) else data.get("results", [])
    if not results:
        return None
    best = results[0]
    return best.get("id") or best.get("libraryId")


def fetch_docs(
    library_id: str,
    query: str,
    max_tokens: int = 2000,
) -> list[dict[str, Any]]:
    """Fetch documentation sections from Context7 for a library + query.

    Returns a list of dicts with title, content, and source fields.
    The API returns two snippet types that we merge:
        - infoSnippets: prose documentation (breadcrumb, content, pageId)
        - codeSnippets: code examples (codeTitle, codeDescription, codeList)
    """
    params = urllib.parse.urlencode({
        "libraryId": library_id,
        "query": query,
        "type": "json",
        "tokens": str(max_tokens),
    })
    url = f"{_BASE_URL}/context?{params}"
    data = _get_json(url)
    if not data or not isinstance(data, dict):
        return []

    results: list[dict[str, Any]] = []

    for info in data.get("infoSnippets", []):
        content = info.get("content", "")
        if content:
            results.append({
                "title": info.get("breadcrumb", ""),
                "content": content,
                "source": info.get("pageId", ""),
            })

    for code in data.get("codeSnippets", []):
        parts = []
        desc = code.get("codeDescription", "")
        if desc:
            parts.append(desc)
        for item in code.get("codeList", []):
            c = item.get("code", "")
            if c:
                parts.append(c)
        content = "\n\n".join(parts)
        if content:
            results.append({
                "title": code.get("codeTitle", code.get("pageTitle", "")),
                "content": content,
                "source": code.get("codeId", ""),
            })

    return results


def query_docs(
    library_name: str,
    query: str,
    max_tokens: int = 2000,
) -> list[dict[str, Any]]:
    """Search + fetch in one call. Returns [] on any failure."""
    library_id = search_library(library_name)
    if not library_id:
        return []
    return fetch_docs(library_id, query, max_tokens=max_tokens)


def _get_json(url: str) -> Any:
    """GET a URL, parse JSON response. Returns None on any failure."""
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "kibitzer"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, json.JSONDecodeError, OSError, TimeoutError):
        return None
