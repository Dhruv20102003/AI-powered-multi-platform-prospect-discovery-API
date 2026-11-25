"""Mocked tests for search engine providers: Serper.dev, SerpAPI, Google CSE, and DuckDuckGo."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.config import Settings
from app.platforms.search_engine import (
    SearchProvidersUnavailable,
    SearchResultItem,
    _configured_providers,
    _resolve_provider_order,
    _search_serper,
    active_search_providers,
    execute_web_search,
    log_configured_providers,
)


@pytest.fixture
def mock_client() -> httpx.AsyncClient:
    return AsyncMock(spec=httpx.AsyncClient)


def _http_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://example.com")
    return httpx.HTTPStatusError(
        "error", request=req, response=httpx.Response(status, request=req)
    )


# -- Serper parsing ------------------------------------------------------------


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

    test_settings = Settings(_env_file=None, SERPER_API_KEY="test_serper_key")

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
    """A Serper response with no 'organic' key returns an empty list gracefully."""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "searchParameters": {"q": "obscure query", "num": 10},
        "knowledgeGraph": {"title": "Sample Knowledge Graph"},
    }
    mock_resp.raise_for_status.return_value = None
    mock_client.post.return_value = mock_resp

    test_settings = Settings(_env_file=None, SERPER_API_KEY="test_serper_key")

    with patch("app.platforms.search_engine.get_settings", return_value=test_settings):
        results = await _search_serper("obscure query", mock_client, 10)

    assert results == []


# -- Fallback behavior ---------------------------------------------------------


@pytest.mark.asyncio
async def test_serper_429_fallback(mock_client: AsyncMock) -> None:
    """When Serper returns HTTP 429, the next provider is tried."""
    serpapi_item = SearchResultItem("SerpAPI Result", "https://linkedin.com/in/user", "Bio")
    test_settings = Settings(
        _env_file=None,
        APP_ENV="development",
        SERPER_API_KEY="test_serper_key",
        SERPAPI_API_KEY="test_serpapi_key",
    )

    with (
        patch("app.platforms.search_engine.get_settings", return_value=test_settings),
        patch("app.platforms.search_engine._search_serper", side_effect=_http_error(429)) as m_serper,
        patch("app.platforms.search_engine._search_serpapi", return_value=[serpapi_item]) as m_serpapi,
    ):
        results = await execute_web_search("test query", mock_client, 10)

    assert m_serper.called
    assert m_serpapi.called
    assert len(results) == 1
    assert results[0].title == "SerpAPI Result"


@pytest.mark.asyncio
async def test_provider_priority_serper_called_first(mock_client: AsyncMock) -> None:
    """When all providers are configured, Serper is tried first and others are skipped."""
    serper_item = SearchResultItem("Serper Winner", "https://linkedin.com/in/winner", "Bio")
    test_settings = Settings(
        _env_file=None,
        APP_ENV="development",
        SERPER_API_KEY="test_serper_key",
        SERPAPI_API_KEY="test_serpapi_key",
        GOOGLE_CSE_API_KEY="test_cse_key",
        GOOGLE_CSE_CX="test_cx",
    )

    with (
        patch("app.platforms.search_engine.get_settings", return_value=test_settings),
        patch("app.platforms.search_engine._search_serper", return_value=[serper_item]) as m_serper,
        patch("app.platforms.search_engine._search_serpapi") as m_serpapi,
        patch("app.platforms.search_engine._search_google_cse") as m_cse,
        patch("app.platforms.search_engine._search_duckduckgo") as m_ddg,
    ):
        results = await execute_web_search("test query", mock_client, 10)

    assert m_serper.called
    assert not m_serpapi.called
    assert not m_cse.called
    assert not m_ddg.called
    assert results[0].title == "Serper Winner"


@pytest.mark.asyncio
async def test_fallback_chain_reaches_ddg_in_development(mock_client: AsyncMock) -> None:
    """In development: Serper (401) -> SerpAPI (429) -> Google CSE (403) -> DuckDuckGo."""
    ddg_item = SearchResultItem("DDG Final Fallback", "https://linkedin.com/in/ddg", "DDG Bio")
    test_settings = Settings(
        _env_file=None,
        APP_ENV="development",
        SERPER_API_KEY="serper_key",
        SERPAPI_API_KEY="serpapi_key",
        GOOGLE_CSE_API_KEY="cse_key",
        GOOGLE_CSE_CX="cx",
    )

    with (
        patch("app.platforms.search_engine.get_settings", return_value=test_settings),
        patch("app.platforms.search_engine._search_serper", side_effect=_http_error(401)) as m_serper,
        patch("app.platforms.search_engine._search_serpapi", side_effect=_http_error(429)) as m_serpapi,
        patch("app.platforms.search_engine._search_google_cse", side_effect=_http_error(403)) as m_cse,
        patch("app.platforms.search_engine._search_duckduckgo", return_value=[ddg_item]) as m_ddg,
    ):
        results = await execute_web_search("test query", mock_client, 10)

    assert m_serper.called
    assert m_serpapi.called
    assert m_cse.called
    assert m_ddg.called
    assert results[0].title == "DDG Final Fallback"


@pytest.mark.asyncio
async def test_all_providers_failing_raises_in_production(mock_client: AsyncMock) -> None:
    """In production, when the only configured provider fails, an outage error is raised."""
    test_settings = Settings(_env_file=None, APP_ENV="production", SERPER_API_KEY="serper_key")

    with (
        patch("app.platforms.search_engine.get_settings", return_value=test_settings),
        patch("app.platforms.search_engine._search_serper", side_effect=_http_error(401)),
        patch("app.platforms.search_engine._search_duckduckgo") as m_ddg,
        pytest.raises(SearchProvidersUnavailable),
    ):
        await execute_web_search("test query", mock_client, 10)

    assert not m_ddg.called, "DuckDuckGo must never be used in production"


@pytest.mark.asyncio
async def test_no_provider_configured_raises_in_production(mock_client: AsyncMock) -> None:
    """With no keys in production, search raises instead of returning an empty list."""
    test_settings = Settings(_env_file=None, APP_ENV="production")

    with (
        patch("app.platforms.search_engine.get_settings", return_value=test_settings),
        pytest.raises(SearchProvidersUnavailable),
    ):
        await execute_web_search("test query", mock_client, 10)


@pytest.mark.asyncio
async def test_empty_results_are_not_an_outage(mock_client: AsyncMock) -> None:
    """A provider that answers successfully with no results returns [] (no exception)."""
    test_settings = Settings(_env_file=None, APP_ENV="production", SERPER_API_KEY="serper_key")

    with (
        patch("app.platforms.search_engine.get_settings", return_value=test_settings),
        patch("app.platforms.search_engine._search_serper", return_value=[]),
    ):
        results = await execute_web_search("test query", mock_client, 10)

    assert results == []


# -- Provider selection --------------------------------------------------------


def test_configured_providers_auto_detection_order() -> None:
    """_configured_providers returns keyed providers in Serper -> SerpAPI -> CSE order."""
    settings = Settings(
        _env_file=None,
        SERPER_API_KEY="sk",
        SERPAPI_API_KEY="sak",
        GOOGLE_CSE_API_KEY="cse",
        GOOGLE_CSE_CX="cx",
    )
    assert _configured_providers(settings) == ["serper", "serpapi", "google_cse"]


def test_configured_providers_only_serpapi() -> None:
    """When only the SerpAPI key is set, only serpapi is configured."""
    settings = Settings(_env_file=None, SERPAPI_API_KEY="sak")
    assert _configured_providers(settings) == ["serpapi"]


def test_configured_providers_no_keys() -> None:
    """When no API keys are set, nothing is configured."""
    settings = Settings(_env_file=None)
    assert _configured_providers(settings) == []


def test_resolve_provider_order_no_override_development() -> None:
    """Without an override in development, keyed providers come first, then DuckDuckGo."""
    settings = Settings(
        _env_file=None, APP_ENV="development", SERPER_API_KEY="sk", SERPAPI_API_KEY="sak"
    )
    assert settings.SEARCH_ENGINE_PROVIDER is None
    assert _resolve_provider_order(settings) == ["serper", "serpapi", "ddg"]


def test_resolve_provider_order_override_serpapi() -> None:
    """SEARCH_ENGINE_PROVIDER=serpapi moves serpapi first; other keyed providers follow."""
    settings = Settings(
        _env_file=None,
        APP_ENV="development",
        SERPER_API_KEY="sk",
        SERPAPI_API_KEY="sak",
        SEARCH_ENGINE_PROVIDER="serpapi",
    )
    assert _resolve_provider_order(settings) == ["serpapi", "serper", "ddg"]


def test_resolve_provider_order_override_ddg_development() -> None:
    """SEARCH_ENGINE_PROVIDER=ddg yields only ddg in development."""
    settings = Settings(
        _env_file=None,
        APP_ENV="development",
        SERPER_API_KEY="sk",
        SEARCH_ENGINE_PROVIDER="ddg",
    )
    assert _resolve_provider_order(settings) == ["ddg"]


def test_production_never_uses_duckduckgo() -> None:
    """In production, DuckDuckGo is excluded even without any API keys."""
    settings = Settings(_env_file=None, APP_ENV="production")
    assert active_search_providers(settings) == []


def test_production_override_ddg_is_ignored() -> None:
    """SEARCH_ENGINE_PROVIDER=ddg yields no providers in production."""
    settings = Settings(_env_file=None, APP_ENV="production", SEARCH_ENGINE_PROVIDER="ddg")
    assert active_search_providers(settings) == []


def test_override_without_credentials_falls_back_to_auto() -> None:
    """An override pointing at a provider without credentials is ignored."""
    settings = Settings(
        _env_file=None,
        APP_ENV="production",
        SERPER_API_KEY="sk",
        SEARCH_ENGINE_PROVIDER="serpapi",
    )
    assert active_search_providers(settings) == ["serper"]


@pytest.mark.asyncio
async def test_override_forces_serpapi_first(mock_client: AsyncMock) -> None:
    """With SEARCH_ENGINE_PROVIDER=serpapi, Serper is not called when SerpAPI succeeds."""
    serpapi_item = SearchResultItem("SerpAPI Forced", "https://x.com/user", "Bio")
    test_settings = Settings(
        _env_file=None,
        APP_ENV="development",
        SERPER_API_KEY="serper_key",
        SERPAPI_API_KEY="serpapi_key",
        SEARCH_ENGINE_PROVIDER="serpapi",
    )

    with (
        patch("app.platforms.search_engine.get_settings", return_value=test_settings),
        patch("app.platforms.search_engine._search_serper") as m_serper,
        patch("app.platforms.search_engine._search_serpapi", return_value=[serpapi_item]) as m_serpapi,
    ):
        results = await execute_web_search("test query", mock_client, 10)

    assert not m_serper.called, "Serper must NOT be called when override=serpapi succeeds"
    assert m_serpapi.called
    assert results[0].title == "SerpAPI Forced"


# -- Startup logging -----------------------------------------------------------


def test_log_configured_providers_lists_names_in_order(caplog: pytest.LogCaptureFixture) -> None:
    """The startup log lists the providers in use and never prints key values."""
    settings = Settings(
        _env_file=None, APP_ENV="development", SERPER_API_KEY="sk", SERPAPI_API_KEY="sak"
    )
    with caplog.at_level(logging.INFO, logger="prospect.search_engine"):
        log_configured_providers(settings)

    messages = " ".join(caplog.messages).lower()
    assert messages.index("serper") < messages.index("serpapi")
    assert "sk" not in messages
    assert "sak" not in messages


def test_log_configured_providers_with_override(caplog: pytest.LogCaptureFixture) -> None:
    """With an override, the overridden provider comes first in the logged order."""
    settings = Settings(
        _env_file=None,
        APP_ENV="development",
        SERPER_API_KEY="sk",
        SERPAPI_API_KEY="sak",
        SEARCH_ENGINE_PROVIDER="serpapi",
    )
    with caplog.at_level(logging.INFO, logger="prospect.search_engine"):
        log_configured_providers(settings)

    messages = " ".join(caplog.messages).lower()
    assert messages.index("serpapi") < messages.index("serper")
    assert "sak" not in messages


def test_log_warns_when_no_provider_available(caplog: pytest.LogCaptureFixture) -> None:
    """A production start with no keys logs a clear warning (no secrets involved)."""
    settings = Settings(_env_file=None, APP_ENV="production")
    with caplog.at_level(logging.WARNING, logger="prospect.search_engine"):
        log_configured_providers(settings)

    assert any("no search provider" in m.lower() for m in caplog.messages)
