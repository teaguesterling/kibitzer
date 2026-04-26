"""Tests for the Context7 provider."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from kibitzer.context7 import (
    _get_json,
    fetch_docs,
    query_docs,
    search_library,
)


class TestSearchLibrary:
    def test_returns_first_match_id(self):
        mock_response = [
            {"id": "/upstash/redis-py", "name": "redis-py"},
            {"id": "/other/lib", "name": "other"},
        ]
        with patch("kibitzer.context7._get_json", return_value=mock_response):
            result = search_library("redis-py")
        assert result == "/upstash/redis-py"

    def test_returns_none_on_empty(self):
        with patch("kibitzer.context7._get_json", return_value=[]):
            assert search_library("nonexistent-lib-xyz") is None

    def test_returns_none_on_network_error(self):
        with patch("kibitzer.context7._get_json", return_value=None):
            assert search_library("anything") is None

    def test_handles_dict_response_with_results_key(self):
        mock_response = {"results": [{"id": "/lib/foo"}]}
        with patch("kibitzer.context7._get_json", return_value=mock_response):
            assert search_library("foo") == "/lib/foo"

    def test_handles_libraryId_key(self):
        mock_response = [{"libraryId": "/alt/key"}]
        with patch("kibitzer.context7._get_json", return_value=mock_response):
            assert search_library("alt") == "/alt/key"


class TestFetchDocs:
    def test_parses_info_snippets(self):
        mock_response = {
            "infoSnippets": [
                {
                    "breadcrumb": "Getting Started",
                    "content": "Install with pip install redis",
                    "pageId": "https://docs.example.com/start",
                },
                {
                    "breadcrumb": "Connection",
                    "content": "Use Redis() to connect",
                    "pageId": "https://docs.example.com/connect",
                },
            ],
            "codeSnippets": [],
        }
        with patch("kibitzer.context7._get_json", return_value=mock_response):
            sections = fetch_docs("/lib/redis", "connection")
        assert len(sections) == 2
        assert sections[0]["title"] == "Getting Started"
        assert "pip install" in sections[0]["content"]
        assert sections[0]["source"] == "https://docs.example.com/start"

    def test_parses_code_snippets(self):
        mock_response = {
            "infoSnippets": [],
            "codeSnippets": [
                {
                    "codeTitle": "Config Example",
                    "codeDescription": "Set the URL",
                    "codeId": "config.md",
                    "codeList": [{"language": "python", "code": "url = 'redis://localhost'"}],
                }
            ],
        }
        with patch("kibitzer.context7._get_json", return_value=mock_response):
            sections = fetch_docs("/lib/x", "config")
        assert sections[0]["title"] == "Config Example"
        assert "Set the URL" in sections[0]["content"]
        assert "redis://localhost" in sections[0]["content"]

    def test_merges_info_and_code_snippets(self):
        mock_response = {
            "infoSnippets": [
                {"breadcrumb": "Auth", "content": "Use API key", "pageId": ""},
            ],
            "codeSnippets": [
                {
                    "codeTitle": "Auth Code",
                    "codeDescription": "Example",
                    "codeId": "",
                    "codeList": [{"code": "key = 'abc'"}],
                },
            ],
        }
        with patch("kibitzer.context7._get_json", return_value=mock_response):
            sections = fetch_docs("/lib/x", "auth")
        assert len(sections) == 2

    def test_skips_empty_content(self):
        mock_response = {
            "infoSnippets": [
                {"breadcrumb": "Empty", "content": "", "pageId": ""},
                {"breadcrumb": "Real", "content": "has content", "pageId": ""},
            ],
            "codeSnippets": [
                {"codeTitle": "Empty Code", "codeDescription": "", "codeId": "", "codeList": []},
            ],
        }
        with patch("kibitzer.context7._get_json", return_value=mock_response):
            sections = fetch_docs("/lib/x", "query")
        assert len(sections) == 1
        assert sections[0]["title"] == "Real"

    def test_returns_empty_on_failure(self):
        with patch("kibitzer.context7._get_json", return_value=None):
            assert fetch_docs("/lib/x", "query") == []

    def test_returns_empty_on_non_dict(self):
        with patch("kibitzer.context7._get_json", return_value="not a dict"):
            assert fetch_docs("/lib/x", "query") == []


class TestQueryDocs:
    def test_combines_search_and_fetch(self):
        with patch("kibitzer.context7.search_library", return_value="/lib/redis"):
            with patch("kibitzer.context7.fetch_docs", return_value=[{"title": "T", "content": "C"}]):
                result = query_docs("redis", "connection")
        assert len(result) == 1

    def test_returns_empty_when_library_not_found(self):
        with patch("kibitzer.context7.search_library", return_value=None):
            assert query_docs("nonexistent", "anything") == []


class TestGetJson:
    def test_returns_none_on_timeout(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError):
            assert _get_json("https://example.com") is None

    def test_returns_none_on_invalid_json(self):
        from unittest.mock import MagicMock
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = lambda s, *a: None
        with patch("urllib.request.urlopen", return_value=mock_resp):
            assert _get_json("https://example.com") is None
