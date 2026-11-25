"""Search engine adapter: Serper.dev, SerpAPI, Google Custom Search, and DuckDuckGo."""

import logging
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from app.config import get_settings

logger = logging.getLogger("prospect.search_engine")

# Auto-detection priority order (index = priority, lower = higher priority)
_AUTO_PRIORITY: list[str] = ["serper", "serpapi", "google_cse", "ddg"]


def _configured_providers(settings: Any) -> list[str]:
    """Return providers that have credentials present, in auto-detection priority order."""
    available: list[str] = []
    if settings.SERPER_API_KEY:
        available.append("serper")
    if settings.SERPAPI_API_KEY:
        available.append("serpapi")
    if settings.GOOGLE_CSE_API_KEY and settings.GOOGLE_CSE_CX:
        available.append("google_cse")
    # DuckDuckGo is always available as a no-key fallback
    available.append("ddg")
    return available


def log_configured_providers(settings: Any) -> None:
    """Log which search providers have credentials at startup (no key values logged)."""
    available = _configured_providers(settings)
    override = settings.SEARCH_ENGINE_PROVIDER
    if override:
        logger.info(
            "Search provider override active: '%s'. Auto-detection disabled.", override
        )
    else:
        logger.info("Search provider auto-detection enabled (priority: %s).", " → ".join(_AUTO_PRIORITY))

    configured_names = [p for p in available if p != "ddg"]
    if configured_names:
        logger.info("Configured search API keys: %s", ", ".join(configured_names))
    else:
        logger.info(
            "No paid search API keys configured. DuckDuckGo will be used as the sole provider."
        )


def _resolve_provider_order(settings: Any) -> list[str]:
    """Return ordered list of providers to try for this request.

    If SEARCH_ENGINE_PROVIDER is set, only that provider is tried (then DDG as fallback
    if the override isn't ddg itself). Otherwise auto-detection priority is used.
    """
    override = settings.SEARCH_ENGINE_PROVIDER
    if override:
        # Override forces start with the specified provider; DDG always appended as final fallback
        if override == "ddg":
            return ["ddg"]
        return [override, "ddg"]
    # Auto-detect: use first configured provider, then fall through the rest
    return _configured_providers(settings)


async def execute_web_search(
    query: str,
    client: httpx.AsyncClient,
    num_results: int = 10,
) -> list["SearchResultItem"]:
    """Execute search using auto-detected provider with graceful fallback chain.

    Provider selection:
    - If SEARCH_ENGINE_PROVIDER is set, it is used first (then DuckDuckGo as last resort).
    - Otherwise the first provider whose key is configured wins:
      Serper.dev → SerpAPI → Google CSE → DuckDuckGo.
    - On HTTP 4xx/5xx, the HTTP status is logged and the next provider is tried.
    """
    settings = get_settings()
    provider_order = _resolve_provider_order(settings)

    for provider in provider_order:
        try:
            if provider == "serper":
                return await _search_serper(query, client, num_results)
            elif provider == "serpapi":
                return await _search_serpapi(query, client, num_results)
            elif provider == "google_cse":
                return await _search_google_cse(query, client, num_results)
            elif provider == "ddg":
                return await _search_duckduckgo(query, client, num_results)
        except httpx.HTTPStatusError as e:
            logger.warning(
                "Search provider '%s' returned HTTP %d for query '%s'. Falling through...",
                provider,
                e.response.status_code,
                query,
            )
        except Exception as e:
            logger.warning(
                "Search provider '%s' failed for query '%s': %s. Falling through...",
                provider,
                query,
                e,
            )

    logger.warning("All search providers exhausted for query '%s'. Returning empty.", query)
    return []


class SearchResultItem:
    """Normalized search engine result item."""

    def __init__(
        self, title: str, link: str, snippet: str, raw: dict[str, Any] | None = None
    ) -> None:
        self.title = title
        self.link = link
        self.snippet = snippet
        self.raw = raw or {}


async def _search_serper(
    query: str, client: httpx.AsyncClient, num_results: int
) -> list[SearchResultItem]:
    """Execute search via Serper.dev Google Search API."""
    settings = get_settings()
    if not settings.SERPER_API_KEY:
        raise RuntimeError("SERPER_API_KEY not configured")

    url = "https://google.serper.dev/search"
    headers = {
        "X-API-KEY": settings.SERPER_API_KEY,
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "q": query,
        "num": num_results,
    }
    resp = await client.post(url, headers=headers, json=payload)
    resp.raise_for_status()

    data = resp.json()
    organic = data.get("organic", []) if isinstance(data, dict) else []
    results: list[SearchResultItem] = []

    if isinstance(organic, list):
        for item in organic:
            if isinstance(item, dict):
                title = item.get("title", "")
                link = item.get("link", "")
                snippet = item.get("snippet", "")
                if link and isinstance(title, str) and isinstance(link, str) and isinstance(snippet, str):
                    results.append(SearchResultItem(title=title, link=link, snippet=snippet, raw=item))

    return results


async def _search_serpapi(
    query: str, client: httpx.AsyncClient, num_results: int
) -> list[SearchResultItem]:
    settings = get_settings()
    if not settings.SERPAPI_API_KEY:
        raise RuntimeError("SERPAPI_API_KEY not configured")
    url = "https://serpapi.com/search.json"
    params: dict[str, str | int] = {
        "q": query,
        "api_key": settings.SERPAPI_API_KEY,
        "num": num_results,
        "engine": "google",
    }
    resp = await client.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()
    organic = data.get("organic_results", [])
    results: list[SearchResultItem] = []
    for item in organic:
        title = item.get("title", "")
        link = item.get("link", "")
        snippet = item.get("snippet", "")
        if link:
            results.append(SearchResultItem(title=title, link=link, snippet=snippet, raw=item))
    return results


async def _search_google_cse(
    query: str, client: httpx.AsyncClient, num_results: int
) -> list[SearchResultItem]:
    settings = get_settings()
    if not settings.GOOGLE_CSE_API_KEY or not settings.GOOGLE_CSE_CX:
        raise RuntimeError("GOOGLE_CSE_API_KEY or GOOGLE_CSE_CX not configured")
    url = "https://www.googleapis.com/customsearch/v1"
    params: dict[str, str | int] = {
        "q": query,
        "key": settings.GOOGLE_CSE_API_KEY,
        "cx": settings.GOOGLE_CSE_CX,
        "num": min(num_results, 10),
    }
    resp = await client.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("items", [])
    results: list[SearchResultItem] = []
    for item in items:
        title = item.get("title", "")
        link = item.get("link", "")
        snippet = item.get("snippet", "")
        if link:
            results.append(SearchResultItem(title=title, link=link, snippet=snippet, raw=item))
    return results


async def _search_duckduckgo(
    query: str, client: httpx.AsyncClient, num_results: int
) -> list[SearchResultItem]:
    """DuckDuckGo HTML lite search endpoint."""
    url = "https://html.duckduckgo.com/html/"
    data = {"q": query, "b": ""}
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://html.duckduckgo.com/",
    }
    resp = await client.post(url, data=data, headers=headers)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    results: list[SearchResultItem] = []
    links = soup.find_all("div", class_="result")

    for div in links:
        title_tag = div.find("a", class_="result__a")
        snippet_tag = div.find("a", class_="result__snippet")
        if not title_tag:
            continue

        raw_href = title_tag.get("href")
        if not isinstance(raw_href, str):
            continue
        clean_url = _clean_ddg_url(raw_href)
        if not clean_url or not clean_url.startswith(("http://", "https://")):
            continue

        title = title_tag.get_text(strip=True)
        snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""
        results.append(SearchResultItem(title=title, link=clean_url, snippet=snippet))
        if len(results) >= num_results:
            break

    return results


def _clean_ddg_url(raw_href: str) -> str:
    """Extract destination URL from DuckDuckGo redirect link."""
    if not raw_href:
        return ""
    if "/l/?uddg=" in raw_href or "uddg=" in raw_href:
        parsed = urlparse(raw_href)
        params = parse_qs(parsed.query)
        uddg = params.get("uddg")
        if uddg and uddg[0]:
            return unquote(uddg[0])
    return raw_href
