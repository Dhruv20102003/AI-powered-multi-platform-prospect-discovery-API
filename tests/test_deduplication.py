"""Tests for entity resolution and deduplication."""

from app.platforms.base import RawProfile
from app.schemas import PlatformName
from app.services.deduplication_service import DeduplicationService


def test_duplicate_consolidation_same_person(
    sample_raw_profiles_same_person: list[RawProfile],
) -> None:
    """Verifies that 3 profiles belonging to the same individual are consolidated into 1 record."""
    service = DeduplicationService()
    consolidated = service.consolidate_profiles(sample_raw_profiles_same_person)

    assert len(consolidated) == 1
    record = consolidated[0]
    assert record.name == "John Smith"
    assert "Washington DC" in (record.location or "")

    # Check social links
    platforms: set[PlatformName] = {link.platform for link in record.social_link}
    assert "LinkedIn" in platforms
    assert "Instagram" in platforms
    assert "X" in platforms
    assert "Email" in platforms
    assert "WhatsApp" in platforms

    # Verify Email format is mailto:
    email_link = next(l for l in record.social_link if l.platform == "Email")
    assert email_link.link == "mailto:john@smithdc.com"

    # Verify WhatsApp link format
    wa_link = next(l for l in record.social_link if l.platform == "WhatsApp")
    assert wa_link.link.startswith("https://wa.me/")


def test_two_different_people_with_same_name_not_merged(
    sample_raw_profiles_different_people: list[RawProfile],
) -> None:
    """Verifies conservative deduplication: two different individuals with the same name are NOT merged."""
    service = DeduplicationService()
    consolidated = service.consolidate_profiles(sample_raw_profiles_different_people)

    # Must remain 2 distinct records
    assert len(consolidated) == 2
    names = [r.name for r in consolidated]
    assert names.count("Michael Johnson") == 2

    # Check that locations differ
    locations = [r.location for r in consolidated]
    assert any("Washington DC" in str(loc) for loc in locations)
    assert any("London" in str(loc) for loc in locations)


def test_wrong_person_link_not_merged_lena_jungmann_happylenayoga() -> None:
    """Wrong-person link from Q6 (Lena Jungmann vs Happy Lena Yoga / happylenayoga) must not merge."""
    service = DeduplicationService()
    yt_profile = RawProfile(
        platform="YouTube",
        name="Lena Jungmann",
        profile_url="https://www.youtube.com/channel/UC2t-L2ZZKpTl3mAO9Yq6HRg",
        location="Berlin, Germany",
        bio="Yoga teacher in Berlin, Germany.",
        evidence=["Yoga", "Berlin", "Germany"],
    )
    ig_profile = RawProfile(
        platform="Instagram",
        name="Happy Lena Yoga",
        profile_url="https://www.instagram.com/happylenayoga",
        location="Berlin",
        bio="Happy Lena Yoga in Berlin.",
        evidence=["Yoga", "Berlin"],
    )

    consolidated = service.consolidate_profiles([yt_profile, ig_profile])
    assert len(consolidated) == 2
    by_name = {r.name: r for r in consolidated}
    assert "Lena Jungmann" in by_name
    assert [s.link for s in by_name["Lena Jungmann"].social_link] == [
        "https://www.youtube.com/channel/UC2t-L2ZZKpTl3mAO9Yq6HRg"
    ]


def test_two_different_people_same_first_name_and_city_not_merged() -> None:
    """Two different people sharing a first name and city must remain separate."""
    service = DeduplicationService()
    p1 = RawProfile(
        platform="LinkedIn",
        name="Lena Jungmann",
        profile_url="https://de.linkedin.com/in/lena-jungmann-123",
        location="Berlin",
        bio="Yoga teacher in Berlin.",
        evidence=["Yoga", "Berlin"],
    )
    p2 = RawProfile(
        platform="Instagram",
        name="Lena Meyer",
        profile_url="https://www.instagram.com/lenameyer.yoga",
        location="Berlin",
        bio="Yoga instructor in Berlin.",
        evidence=["Yoga", "Berlin"],
    )

    consolidated = service.consolidate_profiles([p1, p2])
    assert len(consolidated) == 2


def test_correct_merge_with_two_agreeing_signals_and_accent_folding() -> None:
    """Accent-folded name match + location + handle fragment merges cleanly into 1 prospect."""
    service = DeduplicationService()
    p1 = RawProfile(
        platform="LinkedIn",
        name="Bárbara Conde",
        profile_url="https://de.linkedin.com/in/barbara-conde-danceonthemat",
        location="Berlin",
        bio="Yoga teacher in Berlin.",
        evidence=["Yoga", "Berlin"],
    )
    p2 = RawProfile(
        platform="Instagram",
        name="Barbara Conde",
        profile_url="https://www.instagram.com/barbara_conde",
        location="Berlin",
        bio="Yoga teacher in Berlin.",
        evidence=["Yoga", "Berlin"],
    )

    consolidated = service.consolidate_profiles([p1, p2])
    assert len(consolidated) == 1
    platforms = {s.platform for s in consolidated[0].social_link}
    assert platforms == {"LinkedIn", "Instagram"}


def test_name_match_alone_without_second_signal_never_merges() -> None:
    """A name match alone with no location, handle, contact, or cross-link must never merge."""
    service = DeduplicationService()
    p1 = RawProfile(
        platform="LinkedIn",
        name="Sarah Miller",
        profile_url="https://de.linkedin.com/in/sarah-miller-111",
        location=None,
        bio="Yoga teacher.",
        evidence=["Yoga"],
    )
    p2 = RawProfile(
        platform="Instagram",
        name="Sarah Miller",
        profile_url="https://www.instagram.com/yogawithsm",
        location=None,
        bio="Yoga instructor.",
        evidence=["Yoga"],
    )

    consolidated = service.consolidate_profiles([p1, p2])
    assert len(consolidated) == 2


async def test_targeted_enrichment_rejects_wrong_person_and_accepts_verified_match() -> None:
    """Targeted search must reject non-matching names (happylenayoga) and accept verified matches."""
    from unittest.mock import AsyncMock, patch

    import httpx

    from app.platforms.search_engine import SearchResultItem
    from app.schemas import LocationInfo, ParsedQueryIntent
    from app.services.prospect_service import ProspectDiscoveryService

    svc = ProspectDiscoveryService()
    mock_client = AsyncMock(spec=httpx.AsyncClient)

    prospect = RawProfile(
        platform="YouTube",
        name="Lena Jungmann",
        profile_url="https://www.youtube.com/channel/UC2t-L2ZZKpTl3mAO9Yq6HRg",
        location="Berlin, Germany",
        bio="Yoga teacher in Berlin.",
        evidence=["Yoga", "Berlin"],
    )
    intent = ParsedQueryIntent(
        interest="Yoga",
        audience="Yoga teachers",
        location=LocationInfo(city="Berlin", country="Germany"),
        target_type="individual",
    )

    fake_results = [
        SearchResultItem(
            title="Happy Lena Yoga (@happylenayoga) • Instagram photos and videos",
            link="https://www.instagram.com/happylenayoga",
            snippet="Yoga classes in Berlin Germany.",
        ),
        SearchResultItem(
            title="Lena Jungmann (@lena.jungmann) • Instagram photos and videos",
            link="https://www.instagram.com/lena.jungmann",
            snippet="Yoga teacher based in Berlin, Germany.",
        ),
    ]

    with patch(
        "app.services.prospect_service.execute_web_search",
        new=AsyncMock(side_effect=[fake_results, [], []]),
    ):
        added = await svc._enrich_top_prospects_targeted_search([prospect], intent, mock_client)

    assert len(added) == 1
    assert added[0].profile_url == "https://www.instagram.com/lena.jungmann"
    assert added[0].name == "Lena Jungmann"

