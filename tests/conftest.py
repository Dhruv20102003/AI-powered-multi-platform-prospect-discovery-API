"""Pytest fixtures, mocked clients, and test utilities."""

import os
from collections.abc import AsyncGenerator, Generator

import pytest
from fastapi.testclient import TestClient

# Ensure test environment settings
os.environ["APP_ENV"] = "testing"
os.environ["DEBUG"] = "true"
os.environ["RATE_LIMIT_PER_MINUTE"] = "1000"

from app.core.cache import cache
from app.main import app
from app.platforms.base import RawProfile
from app.schemas import LocationInfo, ParsedQueryIntent


@pytest.fixture(autouse=True)
async def clear_cache_before_each_test() -> AsyncGenerator[None, None]:
    """Clear in-memory cache between test runs to ensure isolation."""
    await cache.clear()
    yield
    await cache.clear()


@pytest.fixture
def test_client() -> Generator[TestClient, None, None]:
    """FastAPI test client fixture."""
    with TestClient(app) as client:
        yield client


@pytest.fixture
def sample_intent_football() -> ParsedQueryIntent:
    return ParsedQueryIntent(
        interest="Football",
        audience="Football lovers",
        location=LocationInfo(
            city="Washington DC",
            state="District of Columbia",
            country="USA",
        ),
        target_type="individual",
    )


@pytest.fixture
def sample_raw_profiles_same_person() -> list[RawProfile]:
    """Three profiles belonging to the same individual across LinkedIn, Instagram, and X."""
    return [
        RawProfile(
            platform="LinkedIn",
            name="John Smith",
            profile_url="https://linkedin.com/in/johnsmithdc",
            location="Washington DC, USA",
            bio="Passionate football fan based in Washington DC. Contact: john@smithdc.com",
            evidence=["John Smith", "Football lover", "Washington DC"],
        ),
        RawProfile(
            platform="Instagram",
            name="John Smith",
            profile_url="https://instagram.com/johnsmithdc",
            location="Washington DC, USA",
            bio="DC Football lover | https://linkedin.com/in/johnsmithdc",
            evidence=["DC Football", "Washington DC"],
        ),
        RawProfile(
            platform="X",
            name="John Smith",
            profile_url="https://x.com/johnsmithdc",
            location="Washington DC",
            bio="Washington Football supporter. Reach me at wa.me/12025550199",
            evidence=["Washington Football", "wa.me/12025550199"],
        ),
    ]


@pytest.fixture
def sample_raw_profiles_different_people() -> list[RawProfile]:
    """Two different people who merely share a common name."""
    return [
        RawProfile(
            platform="LinkedIn",
            name="Michael Johnson",
            profile_url="https://linkedin.com/in/michaeljohnson-dc-sports",
            location="Washington DC, USA",
            bio="Sports journalist and football lover in DC. Contact: mjohnson@dcsports.com",
            evidence=["Sports journalist", "football lover", "DC"],
        ),
        RawProfile(
            platform="YouTube",
            name="Michael Johnson",
            profile_url="https://youtube.com/@michaeljohnson-london-chef",
            location="London, UK",
            bio="Culinary arts and fine dining restaurant chef in London.",
            evidence=["Chef", "London restaurant"],
        ),
    ]
