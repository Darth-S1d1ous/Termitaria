"""Search provider adapters: no network calls."""
import asyncio
import json

import pytest

from services.worker.search import (
    BraveSearch,
    SearchError,
    SearchHit,
    TavilySearch,
    build_search,
)


def test_tavily_maps_results():
    seen = {}

    def transport(method, url, headers, body):
        seen.update(method=method, url=url, headers=headers, body=json.loads(body))
        return 200, json.dumps(
            {"results": [{"title": "T", "url": "https://e.com", "content": "S"}]}
        ).encode()

    hits = TavilySearch("tavily-key", transport=transport).search("copper", limit=3)
    assert hits == [SearchHit("T", "https://e.com", "S")]
    assert seen["method"] == "POST"
    assert seen["url"] == "https://api.tavily.com/search"
    assert seen["headers"]["Authorization"] == "Bearer tavily-key"
    assert seen["body"]["query"] == "copper"
    assert seen["body"]["max_results"] == 3


def test_brave_maps_results():
    seen = {}

    def transport(method, url, headers, body):
        seen.update(method=method, url=url, headers=headers)
        return 200, json.dumps(
            {"web": {"results": [{"title": "T", "url": "https://e.com", "description": "S"}]}}
        ).encode()

    hits = BraveSearch("brave-key", transport=transport).search("copper", limit=3)
    assert hits == [SearchHit("T", "https://e.com", "S")]
    assert seen["method"] == "GET"
    assert "q=copper" in seen["url"]
    assert "count=3" in seen["url"]
    assert seen["headers"]["X-Subscription-Token"] == "brave-key"


def test_search_errors_are_search_errors():
    with pytest.raises(SearchError):
        TavilySearch("key", transport=lambda *args: (500, b"boom")).search("copper")


def _isolate(monkeypatch, *, provider=None, tavily="", brave=""):
    if provider is None:
        monkeypatch.delenv("SEARCH_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("SEARCH_PROVIDER", provider)
    for key, value in (("TAVILY_API_KEY", tavily), ("BRAVE_API_KEY", brave)):
        if value:
            monkeypatch.setenv(key, value)
        else:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("SEARCH_API_KEY", raising=False)
    monkeypatch.setattr("services.worker.search._secret", lambda *args: "")


def test_default_is_tavily(monkeypatch):
    _isolate(monkeypatch, tavily="t", brave="b")
    assert isinstance(build_search(), TavilySearch)


def test_explicit_brave_wins(monkeypatch):
    _isolate(monkeypatch, provider="brave", tavily="t", brave="b")
    assert isinstance(build_search(), BraveSearch)


def test_brave_is_used_when_tavily_key_is_absent(monkeypatch):
    _isolate(monkeypatch, brave="b")
    assert isinstance(build_search(), BraveSearch)


def test_explicit_tavily_without_key_does_not_fall_back(monkeypatch):
    _isolate(monkeypatch, provider="tavily", brave="b")
    with pytest.raises(SearchError):
        build_search()


def test_brave_without_key_raises(monkeypatch):
    _isolate(monkeypatch, provider="brave")
    with pytest.raises(SearchError):
        build_search()


def test_missing_keys_raise(monkeypatch):
    _isolate(monkeypatch)
    with pytest.raises(SearchError):
        build_search()


def test_tavily_sends_freshness_and_topic():
    seen = {}

    def transport(method, url, headers, body):
        seen["body"] = json.loads(body)
        return 200, b'{"results": []}'

    TavilySearch("key", transport=transport).search(
        "copper", freshness="week", topic="news"
    )
    assert seen["body"]["time_range"] == "week"
    assert seen["body"]["topic"] == "news"


def test_brave_sends_freshness_and_ignores_topic():
    seen = {}

    def transport(method, url, headers, body):
        seen["url"] = url
        return 200, b'{"web": {"results": []}}'

    BraveSearch("key", transport=transport).search(
        "copper", freshness="week", topic="news"
    )
    assert "freshness=pw" in seen["url"]
    assert "topic=" not in seen["url"]


def test_web_search_schema_has_optional_filters():
    from services.worker.tools import make_toolbox

    box = make_toolbox(
        agent={"id": "searcher", "kind": "WORKER", "tools": ["web_search"]},
        project_id="p",
        steps=[],
        skills=[],
        mcp_tools=[],
        gateway="http://127.0.0.1:9",
    )
    tool = box.tools["web_search"]
    assert tool.parameters["freshness"]["enum"] == ["day", "week", "month"]
    assert tool.parameters["topic"]["enum"] == ["general", "news"]
    assert tool.required == ["query"]


def test_web_search_rejects_unknown_freshness():
    from services.worker.tools import _web_search

    out = asyncio.run(_web_search({"query": "copper", "freshness": "year"}))
    assert "day, week, or month" in out


def test_unknown_provider_is_rejected(monkeypatch):
    _isolate(monkeypatch, provider="duckduckgo", tavily="t")
    with pytest.raises(SearchError):
        build_search()


def test_web_search_tool_formats_adapter_results(monkeypatch):
    class FakeAdapter:
        name = "Tavily"

        def search(self, query, limit=5, *, freshness="", topic=""):
            return [SearchHit("Title", "https://e.com", "Snippet")]

    monkeypatch.setattr("services.worker.tools.build_search", lambda: FakeAdapter())
    from services.worker.tools import _search_blocking

    out = _search_blocking("copper")
    assert out.startswith("Source Tavily, 1 hits.")
    assert "https://e.com" in out
    assert "Snippet" in out
