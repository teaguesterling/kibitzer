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
    """
    params = urllib.parse.urlencode({
        "libraryId": library_id,
        "query": query,
        "type": "json",
        "tokens": str(max_tokens),
    })
    url = f"{_BASE_URL}/context?{params}"
    data = _get_json(url)
    if not data:
        return []
    sections = data if isinstance(data, list) else data.get("context", [])
    if not isinstance(sections, list):
        return []
    return [
        {
            "title": s.get("title", s.get("segment_title", "")),
            "content": s.get("content", s.get("segment_content", "")),
            "source": s.get("url", s.get("source", "")),
        }
        for s in sections
        if s.get("content") or s.get("segment_content")
    ]


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
