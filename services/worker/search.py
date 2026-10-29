"""Web search providers.

Tavily is the default. Brave remains available for existing setups.
build_search is the only wiring point; the web_search tool formats SearchHits.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import _secret

log = logging.getLogger(__name__)

Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str


class SearchError(RuntimeError):
    pass


def _http(method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=20) as res:
            return res.status, res.read(500_000)
    except HTTPError as exc:
        return exc.code, exc.read(300)
    except (URLError, TimeoutError, OSError) as exc:
        raise SearchError(str(exc)) from exc


def _json(raw: bytes, source: str) -> dict:
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise SearchError(f"{source} did not return JSON") from exc


class SearchAdapter:
    name = ""

    def search(
        self,
        query: str,
        limit: int = 5,
        *,
        freshness: str = "",
        topic: str = "",
    ) -> list[SearchHit]:
        raise NotImplementedError


class TavilySearch(SearchAdapter):
    name = "Tavily"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.tavily.com",
        transport: Transport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._transport = transport or _http

    def search(
        self,
        query: str,
        limit: int = 5,
        *,
        freshness: str = "",
        topic: str = "",
    ) -> list[SearchHit]:
        payload = {
            "query": query,
            "search_depth": "basic",
            "max_results": limit,
            "include_answer": False,
            "include_raw_content": False,
        }
        if freshness:
            payload["time_range"] = freshness
        if topic:
            payload["topic"] = topic
        body = json.dumps(payload).encode()
        status, raw = self._transport(
            "POST",
            f"{self._base_url}/search",
            {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Termitaria/0.1",
            },
            body,
        )
        if status != 200:
            detail = raw.decode("utf-8", "replace")[:300]
            raise SearchError(f"HTTP {status} {detail}".strip())
        data = _json(raw, "Tavily")
        hits = []
        for item in data.get("results") or []:
            title = str(item.get("title") or "").strip()
            link = str(item.get("url") or "").strip()
            snippet = str(item.get("content") or "").strip()
            if title and link:
                hits.append(SearchHit(title, link, snippet))
        return hits[:limit]


class BraveSearch(SearchAdapter):
    name = "Brave"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.search.brave.com/res/v1",
        transport: Transport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._transport = transport or _http

    def search(
        self,
        query: str,
        limit: int = 5,
        *,
        freshness: str = "",
        topic: str = "",
    ) -> list[SearchHit]:
        # Brave's web endpoint has no topic filter; topic=news is ignored here.
        _ = topic
        params = {"q": query, "count": str(limit)}
        freshness_code = {"day": "pd", "week": "pw", "month": "pm"}.get(freshness)
        if freshness_code:
            params["freshness"] = freshness_code
        url = f"{self._base_url}/web/search?" + urlencode(params)
        status, raw = self._transport(
            "GET",
            url,
            {
                "Accept": "application/json",
                "X-Subscription-Token": self._api_key,
                "User-Agent": "Termitaria/0.1",
            },
            None,
        )
        if status != 200:
            detail = raw.decode("utf-8", "replace")[:300]
            raise SearchError(f"HTTP {status} {detail}".strip())
        data = _json(raw, "Brave")
        hits = []
        for item in (data.get("web") or {}).get("results") or []:
            title = str(item.get("title") or "").strip()
            link = str(item.get("url") or "").strip()
            snippet = str(item.get("description") or "").strip()
            if title and link:
                hits.append(SearchHit(title, link, snippet))
        return hits[:limit]


def _tavily_key() -> str:
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if key:
        return key
    return (
        _secret("tavily-secrets", "TAVILY_API_KEY")
        or _secret("nebius-secrets", "TAVILY_API_KEY")
    )


def _brave_key() -> str:
    key = (
        os.environ.get("BRAVE_API_KEY", "").strip()
        or os.environ.get("SEARCH_API_KEY", "").strip()
    )
    if key:
        return key
    return _secret("brave-secrets", "SEARCH_API_KEY")


def build_search() -> SearchAdapter:
    """Pick the provider from SEARCH_PROVIDER. Unset means Tavily, then Brave.

    Always returns a configured adapter or raises SearchError. Callers get one
    failure shape for a missing key, a bad provider name, and a bad response.
    """
    raw = os.environ.get("SEARCH_PROVIDER", "").strip().lower()
    provider = raw or "tavily"
    if provider == "brave":
        key = _brave_key()
        if not key:
            raise SearchError(
                "SEARCH_PROVIDER=brave requires SEARCH_API_KEY or BRAVE_API_KEY"
            )
        return BraveSearch(key)
    if provider != "tavily":
        raise SearchError(f"unknown search provider {provider}")
    key = _tavily_key()
    if key:
        return TavilySearch(key)
    if not raw:
        key = _brave_key()
        if key:
            log.warning("TAVILY_API_KEY is not set; using Brave")
            return BraveSearch(key)
    if raw:
        raise SearchError("SEARCH_PROVIDER=tavily requires TAVILY_API_KEY")
    raise SearchError("set TAVILY_API_KEY or SEARCH_API_KEY")
