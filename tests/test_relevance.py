"""Tests for relevance scoring and candidate filtering."""

from app.platforms.base import RawProfile
from app.schemas import LocationInfo, ParsedQueryIntent
from app.services.relevance_service import RelevanceService


def test_relevance_scoring_high_match() -> None:
    service = RelevanceService()
    intent = ParsedQueryIntent(
        interest="Football",
        audience="Football lovers",
        location=LocationInfo(city="Washington DC", country="USA"),
        target_type="individual",
    )

    high_match_profile = RawProfile(
        platform="LinkedIn",
        name="Alex Turner",
        profile_url="https://linkedin.com/in/alexturner-football",
        location="Washington DC, USA",
        bio="Lifelong football lover and community coach based in Washington DC.",
        evidence=["Washington DC", "Football lover"],
    )

    score = service.score_profile(high_match_profile, intent)
    assert score >= 0.70


def test_relevance_scoring_low_match_discarded() -> None:
    service = RelevanceService()
    intent = ParsedQueryIntent(
        interest="Football",
        audience="Football lovers",
        location=LocationInfo(city="Washington DC", country="USA"),
        target_type="individual",
    )

    irrelevant_profile = RawProfile(
        platform="YouTube",
        name="Garden Enthusiast",
        profile_url="https://youtube.com/@gardeningtokyo",
        location="Tokyo, Japan",
        bio="Bonsai trees and Japanese gardening techniques.",
        evidence=["Gardening", "Tokyo"],
    )

    score = service.score_profile(irrelevant_profile, intent)
    assert score < service.MIN_RELEVANCE_THRESHOLD

    filtered = service.filter_relevant_profiles([irrelevant_profile], intent)
    assert len(filtered) == 0
