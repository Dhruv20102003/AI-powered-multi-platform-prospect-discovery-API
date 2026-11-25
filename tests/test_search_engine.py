"""Mocked tests for search engine providers: Serper.dev, SerpAPI, Google CSE, and DuckDuckGo."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.config import Settings
from app.platforms.search_engine import (
    SearchResultItem,
    _configured_providers,
    _resolve_provider_order,
    _search_serper,
    execute_web_search,
    log_configured_providers,
)


@pytest.fixture
def mock_client() -> httpx.AsyncClient:
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.mark.asyncio
async def test_serper_successful_parse(mock_client: AsyncMock) -> None:
    """Validate that Serper.dev response is parsed into SearchResultItem instances."""
    sample_serper_response = {
        "searchParameters": {"q": "football washington dc", "num": 10},
        "organic": [
            {
                "title": "John Smith - Football Enthusiast",
                "link": "https://linkedin.com/in/johnsmith",
                "snippet": "Passionate football lover in Washington DC.",
                "position": 1,
            },
            {
                "title": "DC Football Fan Club",
                "link": "https://x.com/dcfootballfans",
                "snippet": "Official Washington DC football community.",
                "position": 2,
            },
        ],
    }

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = sample_serper_response
    mock_resp.raise_for_status.return_value = None
    mock_client.post.return_value = mock_resp

    test_settings = Settings(SERPER_API_KEY="test_serper_key")

    with patch("app.platforms.search_engine.get_settings", return_value=test_settings):
        results = await _search_serper("football washington dc", mock_client, 10)

    assert len(results) == 2
    assert results[0].title == "John Smith - Football Enthusiast"
    assert results[0].link == "https://linkedin.com/in/johnsmith"
    assert results[0].snippet == "Passionate football lover in Washington DC."
    assert results[0].raw["position"] == 1

    assert results[1].title == "DC Football Fan Club"
    assert results[1].link == "https://x.com/dcfootballfans"


@pytest.mark.asyncio
async def test_serper_missing_organic_key(mock_client: AsyncMock) -> None:
    """Validate that Serper response with no 'organic' key returns an empty list gracefully."""
    sample_response_no_organic = {
        "searchParameters": {"q": "obscure query", "num": 10},
        "knowledgeGraph": {"title": "Sample Knowledge Graph"},
    }

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = sample_response_no_organic
    mock_resp.raise_for_status.return_value = None
    mock_client.post.return_value = mock_resp

    test_settings = Settings(SERPER_API_KEY="test_serper_key")

    with patch("app.platforms.search_engine.get_settings", return_value=test_settings):
        results = await _search_serper("obscure query", mock_client, 10)

    assert results == []


@pytest.mark.asyncio
async def test_serper_429_fallback(mock_client: AsyncMock) -> None:
    """When Serper returns HTTP 429 Too Many Requests, fallback to next provider."""
    # Serper raises 429
    serper_req = httpx.Request("POST", "https://google.serper.dev/search")
    serper_resp = httpx.Response(429, request=serper_req, text="Rate limit exceeded")
    err_429 = httpx.HTTPStatusError("Rate limited", request=serper_req, response=serper_resp)

    # SerpAPI mock succeeds
    serpapi_item = SearchResultItem("SerpAPI Result", "https://linkedin.com/in/user", "Bio")

    test_settings = Settings(
        _env_file=None,
        SERPER_API_KEY="test_serper_key",
        SERPAPI_API_KEY="test_serpapi_key",
    )

    with (
        patch("app.platforms.search_engine.get_settings", return_value=test_settings),
        patch("app.platforms.search_engine._search_serper", side_effect=err_429) as mock_serper,
        patch("app.platforms.search_engine._search_serpapi", return_value=[serpapi_item]) as mock_serpapi,
    ):
        results = await execute_web_search("test query", mock_client, 10)

    assert mock_serper.called
    assert mock_serpapi.called
    assert len(results) == 1
    assert results[0].title == "SerpAPI Result"


@pytest.mark.asyncio
async def test_provider_priority_serper_called_first(mock_client: AsyncMock) -> None:
    """When all providers are configured, Serper is prioritized first."""
    serper_item = SearchResultItem("Serper Winner", "https://linkedin.com/in/winner", "Bio")

    test_settings = Settings(
        SERPER_API_KEY="test_serper_key",
        SERPAPI_API_KEY="test_serpapi_key",
        GOOGLE_CSE_API_KEY="test_cse_key",
        GOOGLE_CSE_CX="test_cx",
    )

    with (
        patch("app.platforms.search_engine.get_settings", return_value=test_settings),
        patch("app.platforms.search_engine._search_serper", return_value=[serper_item]) as mock_serper,
        patch("app.platforms.search_engine._search_serpapi") as mock_serpapi,
        patch("app.platforms.search_engine._search_google_cse") as mock_cse,
        patch("app.platforms.search_engine._search_duckduckgo") as mock_ddg,
    ):
        results = await execute_web_search("test query", mock_client, 10)

    assert mock_serper.called
    assert not mock_serpapi.called
    assert not mock_cse.called
    assert not mock_ddg.called
    assert len(results) == 1
    assert results[0].title == "Serper Winner"


@pytest.mark.asyncio
async def test_provider_priority_fallback_chain_to_ddg(mock_client: AsyncMock) -> None:
    """Validate full fallback chain Serper (401) -> SerpAPI (429) -> Google CSE (403) -> DDG."""
    req = httpx.Request("GET", "https://example.com")

    err_401 = httpx.HTTPStatusError("Unauthorized", request=req, response=httpx.Response(401, request=req))
    err_429 = httpx.HTTPStatusError("Rate limit", request=req, response=httpx.Response(429, request=req))
    err_403 = httpx.HTTPStatusError("Forbidden", request=req, response=httpx.Response(403, request=req))

    ddg_item = SearchResultItem("DDG Final Fallback", "https://linkedin.com/in/ddg", "DDG Bio")

    test_settings = Settings(
        _env_file=None,
        SERPER_API_KEY="serper_key",
        SERPAPI_API_KEY="serpapi_key",
        GOOGLE_CSE_API_KEY="cse_key",
        GOOGLE_CSE_CX="cx",
    )

    with (
        patch("app.platforms.search_engine.get_settings", return_value=test_settings),
        patch("app.platforms.search_engine._search_serper", side_effect=err_401) as mock_serper,
        patch("app.platforms.search_engine._search_serpapi", side_effect=err_429) as mock_serpapi,
        patch("app.platforms.search_engine._search_google_cse", side_effect=err_403) as mock_cse,
        patch("app.platforms.search_engine._search_duckduckgo", return_value=[ddg_item]) as mock_ddg,
    ):
        results = await execute_web_search("test query", mock_client, 10)

    assert mock_serper.called
    assert mock_serpapi.called
    assert mock_cse.called
    assert mock_ddg.called
    assert len(results) == 1
    assert results[0].title == "DDG Final Fallback"


# ── New tests for auto-detection and override ──────────────────────────────────


def test_configured_providers_auto_detection_order() -> None:
    """_configured_providers returns providers in Serper → SerpAPI → CSE → DDG order."""
    settings = Settings(
        SERPER_API_KEY="sk",
        SERPAPI_API_KEY="sak",
        GOOGLE_CSE_API_KEY="cse",
        GOOGLE_CSE_CX="cx",
    )
    providers = _configured_providers(settings)
    assert providers == ["serper", "serpapi", "google_cse", "ddg"]


def test_configured_providers_only_serpapi() -> None:
    """When only SerpAPI key is set, provider list is [serpapi, ddg]."""
    settings = Settings(_env_file=None, SERPAPI_API_KEY="sak")
    providers = _configured_providers(settings)
    assert providers == ["serpapi", "ddg"]


def test_configured_providers_no_keys() -> None:
    """When no API keys are set, only DDG is in the list."""
    settings = Settings(_env_file=None)
    providers = _configured_providers(settings)
    assert providers == ["ddg"]


def test_resolve_provider_order_no_override() -> None:
    """Without override, resolve order equals _configured_providers."""
    settings = Settings(_env_file=None, SERPER_API_KEY="sk", SERPAPI_API_KEY="sak")
    # SEARCH_ENGINE_PROVIDER is None → auto-detect
    assert settings.SEARCH_ENGINE_PROVIDER is None
    order = _resolve_provider_order(settings)
    assert order == ["serper", "serpapi", "ddg"]


def test_resolve_provider_order_override_serpapi() -> None:
    """SEARCH_ENGINE_PROVIDER=serpapi forces serpapi first, DDG remains as final fallback."""
    settings = Settings(
        SERPER_API_KEY="sk",
        SERPAPI_API_KEY="sak",
        SEARCH_ENGINE_PROVIDER="serpapi",
    )
    order = _resolve_provider_order(settings)
    assert order == ["serpapi", "ddg"]


def test_resolve_provider_order_override_ddg() -> None:
    """SEARCH_ENGINE_PROVIDER=ddg results in only ddg in the list."""
    settings = Settings(SERPER_API_KEY="sk", SEARCH_ENGINE_PROVIDER="ddg")
    order = _resolve_provider_order(settings)
    assert order == ["ddg"]


@pytest.mark.asyncio
async def test_override_forces_serpapi_skipping_serper(mock_client: AsyncMock) -> None:
    """When SEARCH_ENGINE_PROVIDER=serpapi, Serper is never called even if key is set."""
    serpapi_item = SearchResultItem("SerpAPI Forced", "https://x.com/user", "Bio")

    test_settings = Settings(
        SERPER_API_KEY="serper_key",
        SERPAPI_API_KEY="serpapi_key",
        SEARCH_ENGINE_PROVIDER="serpapi",
    )

    with (
        patch("app.platforms.search_engine.get_settings", return_value=test_settings),
        patch("app.platforms.search_engine._search_serper") as mock_serper,
        patch("app.platforms.search_engine._search_serpapi", return_value=[serpapi_item]) as mock_serpapi,
    ):
        results = await execute_web_search("test query", mock_client, 10)

    assert not mock_serper.called, "Serper must NOT be called when override=serpapi"
    assert mock_serpapi.called
    assert results[0].title == "SerpAPI Forced"


def test_log_configured_providers_no_override(caplog: pytest.LogCaptureFixture) -> None:
    """log_configured_providers logs auto-detection mode and configured key names."""
    import logging

    settings = Settings(_env_file=None, SERPER_API_KEY="sk", SERPAPI_API_KEY="sak")
    with caplog.at_level(logging.INFO, logger="prospect.search_engine"):
        log_configured_providers(settings)

    messages = " ".join(caplog.messages)
    assert "auto-detection" in messages.lower()
    assert "serper" in messages.lower()
    assert "serpapi" in messages.lower()
    # Key values must NOT appear
    assert "sk" not in messages
    assert "sak" not in messages


def test_log_configured_providers_with_override(caplog: pytest.LogCaptureFixture) -> None:
    """log_configured_providers logs the active override and suppresses auto-detection message."""
    import logging

    settings = Settings(
        SERPAPI_API_KEY="sak",
        SEARCH_ENGINE_PROVIDER="serpapi",
    )
    with caplog.at_level(logging.INFO, logger="prospect.search_engine"):
        log_configured_providers(settings)

    messages = " ".join(caplog.messages)
    assert "override" in messages.lower()
    assert "serpapi" in messages.lower()
    assert "sak" not in messages
