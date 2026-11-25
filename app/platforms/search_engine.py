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


class SearchProvidersUnavailable(RuntimeError):
    """Raised when no search provider is configured or every provider failed."""


def _configured_providers(settings: Any) -> list[str]:
    """Return providers that have credentials present, in priority order (no DDG)."""
    available: list[str] = []
    if settings.SERPER_API_KEY:
        available.append("serper")
    if settings.SERPAPI_API_KEY:
        available.append("serpapi")
    if settings.GOOGLE_CSE_API_KEY and settings.GOOGLE_CSE_CX:
        available.append("google_cse")
    return available


def _resolve_provider_order(settings: Any) -> list[str]:
    """Return the ordered list of providers to try for this request.

    Configured API providers come first (an override moves one to the front).
    DuckDuckGo is a development-only fallback: it is blocked from cloud servers and
    is not an authorized API, so it is never used when APP_ENV=production.
    """
    override = settings.SEARCH_ENGINE_PROVIDER
    is_production = settings.APP_ENV == "production"

    if override == "ddg":
        return [] if is_production else ["ddg"]

    order = _configured_providers(settings)
    if override:
        if override in order:
            order = [override] + [p for p in order if p != override]
        else:
            logger.warning(
                "SEARCH_ENGINE_PROVIDER='%s' is set but its credentials are missing; "
                "using auto-detection.",
                override,
            )
    if not is_production:
        order.append("ddg")
    return order


def active_search_providers(settings: Any) -> list[str]:
    """Names of the search providers that would be tried, in order (no secrets)."""
    return _resolve_provider_order(settings)


def log_configured_providers(settings: Any) -> None:
    """Log which search providers are usable at startup (names only, no key values)."""
    order = _resolve_provider_order(settings)
    if order:
        logger.info("Search providers in use (in order): %s", ", ".join(order))
    else:
        logger.warning(
            "No search provider is available (APP_ENV=%s). Discovery requests will fail "
            "with 503 until SERPER_API_KEY (or another provider key) is configured.",
            settings.APP_ENV,
        )


async def execute_web_search(
    query: str,
    client: httpx.AsyncClient,
    num_results: int = 10,
) -> list["SearchResultItem"]:
    """Run a web search through the first working provider.

    Returns an empty list only when a provider answered successfully with no results.
    Raises SearchProvidersUnavailable when no provider is configured or all failed,
    so callers can tell "nothing found" apart from "search is broken".
    """
    settings = get_settings()
    provider_order = _resolve_provider_order(settings)
    if not provider_order:
        raise SearchProvidersUnavailable("no search provider is configured")

    for provider in provider_order:
        try:
            if provider == "serper":
                return await _search_serper(query, client, num_results)
            if provider == "serpapi":
                return await _search_serpapi(query, client, num_results)
            if provider == "google_cse":
                return await _search_google_cse(query, client, num_results)
            if provider == "ddg":
                return await _search_duckduckgo(query, client, num_results)
        except httpx.HTTPStatusError as e:
            logger.warning(
                "Search provider '%s' returned HTTP %d. Falling through...",
                provider,
                e.response.status_code,
            )
        except Exception as e:
            # Log only the error type: some HTTP errors embed the request URL,
            # which can contain an API key.
            logger.warning(
                "Search provider '%s' failed (%s). Falling through...",
                provider,
                type(e).__name__,
            )

    logger.warning("All search providers failed for one query.")
    raise SearchProvidersUnavailable("all search providers failed")


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
    """DuckDuckGo HTML lite search endpoint (development fallback only)."""
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
