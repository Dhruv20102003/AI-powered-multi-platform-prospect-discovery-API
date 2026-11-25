"""Mocked unit tests for LinkLivenessChecker (no real network calls)."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.cache import InMemoryTTLCache
from app.schemas import ProspectRecord, SocialLink
from app.services.link_liveness import LinkLivenessChecker


def _make_response(status_code: int, url: str = "https://example.com") -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.url = url
    return resp


@pytest.mark.asyncio
async def test_liveness_404_dropped() -> None:
    """HTTP 404 (and 410) must be marked dead and dropped."""
    cache = InMemoryTTLCache(default_ttl_seconds=60)
    checker = LinkLivenessChecker(ttl_cache=cache)
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _make_response(
        404, "https://www.linkedin.com/in/vincent-sit-dds-98ba93146"
    )

    res = await checker.check_url(
        "https://www.linkedin.com/in/vincent-sit-dds-98ba93146", client
    )
    assert res.state == "dead"
    assert res.keep is False
    assert res.http_status == 404


@pytest.mark.asyncio
async def test_liveness_200_kept() -> None:
    """HTTP 200 OK must be marked alive and kept."""
    cache = InMemoryTTLCache(default_ttl_seconds=60)
    checker = LinkLivenessChecker(ttl_cache=cache)
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _make_response(200, "https://ca.linkedin.com/in/sharon-walden-a1796122")

    res = await checker.check_url(
        "https://ca.linkedin.com/in/sharon-walden-a1796122", client
    )
    assert res.state == "alive"
    assert res.keep is True
    assert res.http_status == 200


@pytest.mark.asyncio
async def test_liveness_999_kept_as_unverified() -> None:
    """HTTP 999 (LinkedIn bot-block / login-wall) must be kept and marked unverified."""
    cache = InMemoryTTLCache(default_ttl_seconds=60)
    checker = LinkLivenessChecker(ttl_cache=cache)
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _make_response(999, "https://ca.linkedin.com/in/kyletodt")

    res = await checker.check_url("https://ca.linkedin.com/in/kyletodt", client)
    assert res.state == "unverified"
    assert res.keep is True
    assert res.http_status == 999


@pytest.mark.asyncio
async def test_liveness_timeout_kept_as_unverified() -> None:
    """Timeouts must be kept as unverified rather than dropping valid links."""
    cache = InMemoryTTLCache(default_ttl_seconds=60)
    checker = LinkLivenessChecker(ttl_cache=cache)
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.side_effect = httpx.ReadTimeout("Timed out")

    res = await checker.check_url("https://www.instagram.com/torontobeachesdentists", client)
    assert res.state == "unverified"
    assert res.keep is True
    assert res.http_status is None


@pytest.mark.asyncio
async def test_liveness_drops_prospect_with_no_remaining_links() -> None:
    """If all of a prospect's links return 404/410, the prospect itself is dropped."""
    cache = InMemoryTTLCache(default_ttl_seconds=60)
    checker = LinkLivenessChecker(ttl_cache=cache)
    client = AsyncMock(spec=httpx.AsyncClient)

    async def fake_get(url: str, timeout: float = 3.0) -> MagicMock:
        if "vincent-sit" in url:
            return _make_response(404, url)
        if "kyletodt" in url:
            return _make_response(999, url)
        return _make_response(200, url)

    client.get.side_effect = fake_get

    p_dead = ProspectRecord(
        name="Vincent Sit DDS",
        location="Toronto",
        social_link=[
            SocialLink(
                platform="LinkedIn",
                link="https://www.linkedin.com/in/vincent-sit-dds-98ba93146",
            )
        ],
    )
    p_alive = ProspectRecord(
        name="Kyle Todt",
        location="Toronto, Canada",
        social_link=[
            SocialLink(platform="LinkedIn", link="https://ca.linkedin.com/in/kyletodt"),
            SocialLink(platform="Google", link="https://example-dental.ca"),
        ],
    )

    kept, results = await checker.filter_prospects([p_dead, p_alive], client)
    assert len(kept) == 1
    assert kept[0].name == "Kyle Todt"
    assert len(kept[0].social_link) == 2
    assert results["https://www.linkedin.com/in/vincent-sit-dds-98ba93146"].state == "dead"
    assert results["https://ca.linkedin.com/in/kyletodt"].state == "unverified"
    assert results["https://example-dental.ca"].state == "alive"
