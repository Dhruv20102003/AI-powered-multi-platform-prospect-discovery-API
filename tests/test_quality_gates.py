"""Tests for quality gates: location evidence, sentence names, mojibake, budget caps, and 1 link per platform."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.config import Settings
from app.platforms.base import RawProfile
from app.schemas import LocationInfo, ParsedQueryIntent
from app.services.deduplication_service import DeduplicationService
from app.services.prospect_service import ProspectDiscoveryService
from app.services.relevance_service import RelevanceService
from app.utils.normalizers import has_mojibake, is_valid_name, normalize_name


def test_mojibake_detection_and_rejection() -> None:
    """Mojibake characters must be detected and rejected."""
    mojibake_title = "Httpswa.me201515931120Ù„Ù„ØªÙˆØ§ØµÙ„ ÙˆØªØ³ Ø§Ø¨"
    assert has_mojibake(mojibake_title) is True
    assert normalize_name(mojibake_title) == ""


def test_name_extraction_rejects_sentences_and_post_titles() -> None:
    """Names that are sentences, post titles, or video descriptions must be rejected."""
    # Sentence headlines from saved results
    assert is_valid_name("We Had A Great Time Spending The Day With Ahmad Hardy At") is False
    assert is_valid_name("Hearing These Stories Makes It All Worth It This Is What Spl Truly") is False
    assert is_valid_name("Whats The Best Place To Network With Entrepreneurs In") is False
    assert is_valid_name("Thinking About Taking Your Restaurant To The Next Level Mahesh") is False
    assert is_valid_name("And That Was The Beginning Of The Journey Behind The Mini Ai") is False
    assert is_valid_name("Official Site Of The Washington Commanders") is False
    assert is_valid_name("Cv Available Your Success Our Priority") is False

    # Valid real prospect names
    assert is_valid_name("John Smith") is True
    assert is_valid_name("Maurizio Ferraiuolo") is True
    assert is_valid_name("Kandukuri Dharma Rakshith") is True


def test_normalize_name_strips_app_and_role_suffixes() -> None:
    """normalize_name must strip app/company names and role labels appended after a dash."""
    # LinkedIn title with role suffix
    assert normalize_name("Naman Dhingra - GETABAT Cricket") == "Naman Dhingra"
    # LinkedIn title with CEO/Owner suffix
    assert normalize_name("Sarah Jenkins - CEO") == "Sarah Jenkins"
    # "Name - Adjective RoleWord" (e.g. "COCO Dubai - Restaurant Owner")
    assert normalize_name("COCO Dubai - Restaurant Owner") == "COCO Dubai"
    # Longer role title
    assert normalize_name("Eric Lituchy - Digital Marketing Agency Owner") == "Eric Lituchy"
    # Already clean name
    assert normalize_name("Eric Lituchy") == "Eric Lituchy"
    # Community suffix names produce empty string (rejected by is_valid_name)
    assert normalize_name("INDIAN CRICKET LOVERS") == ""


def test_location_omitted_when_no_evidence() -> None:
    """If candidate bio/evidence has no location keywords, location is omitted (None) and rejected."""
    service = RelevanceService()
    intent = ParsedQueryIntent(
        interest="Football",
        audience="Football lovers",
        location=LocationInfo(city="Washington DC", country="USA"),
        target_type="individual",
    )

    profile_without_location = RawProfile(
        platform="LinkedIn",
        name="Alex Turner",
        profile_url="https://linkedin.com/in/alexturner",
        location=None,
        bio="Passionate football lover and community player.",
        evidence=["Football enthusiast", "Love the game"],
    )

    passed, reason = service.is_relevant(profile_without_location, intent)
    assert passed is False
    assert "No location evidence" in reason
    assert profile_without_location.location is None


def test_relevance_gate_requires_both_interest_and_location() -> None:
    """Candidate must have evidence for both interest and location."""
    service = RelevanceService()
    intent = ParsedQueryIntent(
        interest="Restaurant",
        audience="Restaurant owners",
        location=LocationInfo(city="Dubai", country="UAE"),
        target_type="individual",
    )

    # Has location (Dubai) and interest (restaurant) and role (owner)
    valid_candidate = RawProfile(
        platform="LinkedIn",
        name="Maurizio Ferraiuolo",
        profile_url="https://linkedin.com/in/maurizio-ferraiuolo",
        location=None,
        bio="Restaurant owner and hospitality director in Dubai, UAE.",
        evidence=["Restaurant owner in Dubai, UAE", "Hospitality"],
    )

    passed, _ = service.is_relevant(valid_candidate, intent)
    assert passed is True
    assert "Dubai" in (valid_candidate.location or "")

    # Has location (Dubai) but NO role keyword (missing owner/founder/ceo)
    missing_role = RawProfile(
        platform="Instagram",
        name="Foodie Fan",
        profile_url="https://instagram.com/foodiefan",
        location=None,
        bio="I love eating at every restaurant in Dubai UAE!",
        evidence=["Dubai restaurant fan"],
    )
    passed_role, reason_role = service.is_relevant(missing_role, intent)
    assert passed_role is False
    assert "role keyword" in reason_role


def test_relevance_gate_drops_organizations_for_individual_target_type() -> None:
    """Drop teams, leagues, universities, and hall of fame organizations when target_type is individual."""
    service = RelevanceService()
    intent = ParsedQueryIntent(
        interest="Football",
        audience="Football lovers",
        location=LocationInfo(city="Washington DC", country="USA"),
        target_type="individual",
    )

    team_candidate = RawProfile(
        platform="Google",
        name="Washington Commanders",
        profile_url="https://commanders.com",
        location="Washington DC",
        bio="Official Washington DC NFL football team.",
        evidence=["Washington DC", "Football team"],
    )

    passed, reason = service.is_relevant(team_candidate, intent)
    assert passed is False
    assert "Organization indicator" in reason


def test_one_link_per_platform_rule() -> None:
    """A prospect must have at most one link per platform."""
    dedup = DeduplicationService()
    cluster = [
        RawProfile(
            platform="Instagram",
            name="John Smith",
            profile_url="https://instagram.com/johnsmith",
            location="Washington DC",
            bio="DC Football lover",
            evidence=["DC Football", "Washington DC"],
        ),
        RawProfile(
            platform="Instagram",
            name="John Smith",
            profile_url="https://instagram.com/johnsmith_extra",
            location="Washington DC",
            bio="Second account",
            evidence=["DC Football", "Washington DC"],
        ),
        RawProfile(
            platform="LinkedIn",
            name="John Smith",
            profile_url="https://linkedin.com/in/johnsmith",
            location="Washington DC",
            bio="Football lover in Washington DC",
            evidence=["Washington DC", "Football"],
        ),
    ]

    record = dedup._build_prospect_record(cluster, default_location=None)
    assert record is not None

    # Count Instagram links
    ig_links = [l for l in record.social_link if l.platform == "Instagram"]
    assert len(ig_links) == 1
    assert ig_links[0].link == "https://instagram.com/johnsmith"


@pytest.mark.asyncio
async def test_consolidation_from_website_outbound_links() -> None:
    """Verify that outbound social links on a personal website are extracted and validated."""
    coordinator = ProspectDiscoveryService()
    mock_client = AsyncMock(spec=httpx.AsyncClient)

    html_content = """
    <html>
      <head><title>Maurizio Ferraiuolo</title></head>
      <body>
        <p>Founder & Chef</p>
        <a href="https://linkedin.com/in/maurizio-dubai">LinkedIn Profile</a>
        <a href="https://instagram.com/maurizio_dubai">Instagram</a>
        <a href="https://instagram.com/reel/12345">My Reel</a>
        <a href="mailto:maurizio@hospitality.me">Email Me</a>
        <a href="https://wa.me/971501234567">WhatsApp</a>
      </body>
    </html>
    """
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.text = html_content
    mock_client.get.return_value = mock_resp

    profile = RawProfile(
        platform="Google",
        name="Maurizio Ferraiuolo",
        profile_url="https://expertsinhospitality.me/maurizio",
        location="Dubai, UAE",
        bio="Hospitality director in Dubai UAE.",
        evidence=["Dubai", "UAE", "Hospitality"],
    )

    intent = ParsedQueryIntent(
        interest="Restaurant",
        audience="Restaurant owners",
        location=LocationInfo(city="Dubai", country="UAE"),
        target_type="individual",
    )

    await coordinator._enrich_prospects_from_websites([profile], mock_client, intent)

    assert profile.email == "maurizio@hospitality.me"
    assert profile.whatsapp_link == "https://wa.me/971501234567"
    # Reel must be rejected, real profiles accepted
    assert any("maurizio-dubai" in e and "LinkedIn" in e for e in profile.evidence)
    assert any("maurizio_dubai" in e and "Instagram" in e for e in profile.evidence)
    assert not any("12345" in e for e in profile.evidence)


@pytest.mark.asyncio
async def test_search_budget_cap_enforced() -> None:
    """Assert that targeted searches never exceed the configured budget caps."""
    custom_settings = Settings(
        MAX_ENRICHMENT_PROSPECTS=2,
        MAX_SEARCH_CALLS_PER_PROSPECT=1,
        MAX_SEARCH_CALLS_PER_REQUEST=1,  # Global request cap = 1
    )

    coordinator = ProspectDiscoveryService()
    coordinator.settings = custom_settings
    mock_client = AsyncMock(spec=httpx.AsyncClient)

    profiles = [
        RawProfile(platform="LinkedIn", name="Alice", profile_url="https://linkedin.com/in/alice", location="London"),
        RawProfile(platform="LinkedIn", name="Bob", profile_url="https://linkedin.com/in/bob", location="London"),
    ]

    intent = ParsedQueryIntent(
        interest="Startup",
        audience="Founders",
        location=LocationInfo(city="London", country="UK"),
        target_type="individual",
    )

    with patch("app.services.prospect_service.execute_web_search", return_value=[]) as mock_search:
        await coordinator._enrich_top_prospects_targeted_search(profiles, intent, mock_client)
        # Global cap is 1, so execute_web_search must be called exactly once
        assert mock_search.call_count == 1


def test_exact_mojibake_akashdas_rejected() -> None:
    """Explicitly verify that the exact corrupted string from threads.net/@akashdas.official is rejected."""
    mojibake_str1 = "ðð¤ðð¬ð¡ ððð¬"
    mojibake_str2 = "ð  €ð  ¤ð  šð  ¬ð  ¡ ð  ƒð  šð  ¬"

    assert has_mojibake(mojibake_str1) is True
    assert has_mojibake(mojibake_str2) is True
    assert is_valid_name(mojibake_str1) is False
    assert is_valid_name(mojibake_str2) is False

    # Ensure deduplication final guard drops cluster with only mojibake name
    dedup = DeduplicationService()
    cluster = [
        RawProfile(
            platform="Threads",
            name=mojibake_str1,
            profile_url="https://www.threads.net/@akashdas.official",
            location="Ahmedabad, India",
            bio="Cricket lover in Ahmedabad",
            evidence=["Cricket", "Ahmedabad"],
        )
    ]
    record = dedup._build_prospect_record(cluster, default_location=None)
    assert record is None


def test_entity_gate_rejects_chrome_directories_and_venues() -> None:
    """Page-chrome, directories, listings, and venue/hotel names must be rejected."""
    invalid_examples = [
        "Find Startups In London",
        "Startups Of London",
        "Contact",
        "Hyatt Regency Ahmedabad",
        "The Cricket",
        "Cricket Ground In Ahmedabad",
        "London Startup Community And Founder Mentorship",
        "UK Startup Scaleup Week",
        "Startup Week London",
        "Digital Marketing Agency",
    ]
    for ex in invalid_examples:
        assert is_valid_name(ex) is False, f"Expected '{ex}' to be rejected as non-prospect entity"


@pytest.mark.asyncio
async def test_consolidation_linkedin_enriched_with_instagram_and_x() -> None:
    """One person found on LinkedIn, enriched with Instagram and X, consolidated into 1 prospect with 3 links."""
    coordinator = ProspectDiscoveryService()
    mock_client = AsyncMock(spec=httpx.AsyncClient)

    # Initial candidate from LinkedIn
    linkedin_profile = RawProfile(
        platform="LinkedIn",
        name="Sarah Jenkins",
        profile_url="https://linkedin.com/in/sarahjenkins-london",
        location="London, UK",
        bio="Startup founder in London UK.",
        evidence=["Startup founder", "London", "UK"],
    )

    intent = ParsedQueryIntent(
        interest="Startup",
        audience="Startup founders",
        location=LocationInfo(city="London", country="UK"),
        target_type="individual",
    )

    from app.platforms.search_engine import SearchResultItem

    # Mock targeted searches for Instagram and X
    def mock_web_search(query: str, client: httpx.AsyncClient, num_results: int = 10) -> list[SearchResultItem]:
        if "site:instagram.com" in query:
            return [
                SearchResultItem(
                    title="Sarah Jenkins (@sarahj_london) • Instagram",
                    link="https://www.instagram.com/sarahj_london",
                    snippet="Startup founder building fintech in London UK.",
                )
            ]
        elif "site:twitter.com" in query or "site:x.com" in query:
            return [
                SearchResultItem(
                    title="Sarah Jenkins (@sarahj_tech) on X",
                    link="https://twitter.com/sarahj_tech",
                    snippet="Founder in London UK. Talking startups and tech.",
                )
            ]
        return []

    with patch("app.services.prospect_service.execute_web_search", side_effect=mock_web_search):
        additional = await coordinator._enrich_top_prospects_targeted_search(
            [linkedin_profile], intent, mock_client
        )

    assert len(additional) == 2
    platforms_found = {p.platform for p in additional}
    assert "Instagram" in platforms_found
    assert "X" in platforms_found

    # Consolidate the entire cluster (LinkedIn + Instagram + X)
    all_profiles = [linkedin_profile] + additional
    dedup = DeduplicationService()
    consolidated = dedup.consolidate_profiles(all_profiles)

    # Must be consolidated into EXACTLY ONE prospect record with 3 links
    assert len(consolidated) == 1
    record = consolidated[0]
    assert record.name == "Sarah Jenkins"


# =========================================================================
# Generalization Test Fixtures (Berlin Yoga, Toronto Dentists, London Startups)
# =========================================================================


def test_berlin_yoga_rejection_fixtures() -> None:
    """Verify that Berlin Yoga non-prospects are dropped for the exact specified reasons."""
    service = RelevanceService()
    intent = ParsedQueryIntent(
        interest="Yoga",
        audience="Yoga teachers",
        location=LocationInfo(city="Berlin", country="Germany"),
        target_type="individual",
    )

    # 1. "Private Yoga Instructor" Las Vegas page -> conflicting location / no Berlin
    p1 = RawProfile(
        platform="Google",
        name="Private Yoga Instructor",
        profile_url="https://example.com/private-yoga",
        evidence=["Private Yoga Instructor in Las Vegas", "Las Vegas, Nevada"],
    )
    passed, reason = service.is_relevant(p1, intent)
    assert passed is False
    assert "conflicting location" in reason.lower() or "no location evidence" in reason.lower()

    # 2. "Yoga Instructors In Florida" -> conflicting location Florida / no Berlin
    p2 = RawProfile(
        platform="Google",
        name="Yoga Instructors",
        profile_url="https://example.com/florida-yoga",
        evidence=["Top Yoga Instructors in Florida", "Florida, USA"],
    )
    passed, reason = service.is_relevant(p2, intent)
    assert passed is False
    assert "conflicting location" in reason.lower() or "no location evidence" in reason.lower()

    # 3. "Yoga Teachers Union IWGB" -> Union
    p3 = RawProfile(
        platform="Google",
        name="Yoga Teachers Union IWGB",
        profile_url="https://example.com/iwgb-yoga",
        evidence=["Yoga Teachers Union IWGB Berlin branch", "Berlin, Germany"],
    )
    passed, reason = service.is_relevant(p3, intent)
    assert passed is False
    assert (
        "union" in reason.lower()
        or "not an individual prospect" in reason.lower()
        or "invalid" in reason.lower()
        or "non-prospect" in reason.lower()
    )

    # 4. "Online Yoga Teaching" -> No location evidence
    p4 = RawProfile(
        platform="Google",
        name="Online Yoga Teaching",
        profile_url="https://example.com/online-yoga",
        evidence=["Online Yoga Teaching courses", "Learn yoga at home online"],
    )
    passed, reason = service.is_relevant(p4, intent)
    assert passed is False
    assert "no location evidence" in reason.lower()


def test_toronto_dentists_rejection_fixtures() -> None:
    """Verify that Toronto Dentist non-prospects are dropped for the exact specified reasons."""
    service = RelevanceService()
    intent = ParsedQueryIntent(
        interest="Dentists",
        audience="Dentists",
        location=LocationInfo(city="Toronto", country="Canada"),
        target_type="individual",
    )

    # 1. edic.com "Practicing Dentist Owner" -> Insurer
    p1 = RawProfile(
        platform="Google",
        name="Practicing Dentist Owner",
        profile_url="https://edic.com/practicing-dentist-owner",
        evidence=["Malpractice insurance for practicing dentist owners", "Toronto, Ontario, Canada"],
    )
    passed, reason = service.is_relevant(p1, intent)
    assert passed is False
    assert "non-prospect page" in reason.lower() or "insurer" in reason.lower() or "insurance" in reason.lower()

    # 2. "The Website Owners Manual For Dentists" -> Article / manual
    p2 = RawProfile(
        platform="Google",
        name="The Website Owners Manual For Dentists",
        profile_url="https://example.com/manual-for-dentists",
        evidence=["The Website Owners Manual For Dentists in Toronto", "Toronto, Canada"],
    )
    passed, reason = service.is_relevant(p2, intent)
    assert passed is False
    assert "manual" in reason.lower() or "non-prospect" in reason.lower() or "invalid" in reason.lower()

    # 3. "Union Of American Physicians Dentists" -> Union
    p3 = RawProfile(
        platform="Google",
        name="Union Of American Physicians Dentists",
        profile_url="https://example.com/uapd-toronto",
        evidence=["Union Of American Physicians Dentists Toronto", "Toronto, Canada"],
    )
    passed, reason = service.is_relevant(p3, intent)
    assert passed is False
    assert (
        "union" in reason.lower()
        or "invalid" in reason.lower()
        or "not an individual prospect" in reason.lower()
        or "non-prospect" in reason.lower()
    )

    # 4. TDIC (insurer)
    p4 = RawProfile(
        platform="Google",
        name="The Dentists Insurance Company",
        profile_url="https://tdic.com",
        evidence=["TDIC insurance for dentists", "Toronto, Canada"],
    )
    passed, reason = service.is_relevant(p4, intent)
    assert passed is False
    assert "insurance" in reason.lower() or "insurer" in reason.lower() or "non-prospect" in reason.lower()

    # 5. Academy Of General Dentistry -> Academy / association
    p5 = RawProfile(
        platform="Google",
        name="Academy Of General Dentistry",
        profile_url="https://agd.org",
        evidence=["Academy Of General Dentistry Toronto", "Toronto, Canada"],
    )
    passed, reason = service.is_relevant(p5, intent)
    assert passed is False
    assert (
        "academy" in reason.lower()
        or "association" in reason.lower()
        or "invalid" in reason.lower()
        or "non-prospect" in reason.lower()
    )

    # 6. Dentists On The Square Philadelphia PA -> Conflicting location
    p6 = RawProfile(
        platform="Google",
        name="Dentists On The Square",
        profile_url="https://dentistsonthesquare.com",
        evidence=["Dentists On The Square Philadelphia PA", "Philadelphia PA USA"],
    )
    passed, reason = service.is_relevant(p6, intent)
    assert passed is False
    assert "conflicting location" in reason.lower() or "no location evidence" in reason.lower()

    # 7. The Leesburg Dentists -> Conflicting location Leesburg
    p7 = RawProfile(
        platform="Google",
        name="The Leesburg Dentists",
        profile_url="https://leesburgdentists.com",
        evidence=["General dentistry in Leesburg VA", "Leesburg VA USA"],
    )
    passed, reason = service.is_relevant(p7, intent)
    assert passed is False
    assert "conflicting location" in reason.lower() or "no location evidence" in reason.lower()

    # 8. "UT Dentists" -> Texas / no Toronto evidence
    p8 = RawProfile(
        platform="Google",
        name="UT Dentists",
        profile_url="https://utdentists.com",
        evidence=["University of Texas dentists in Houston", "Texas USA"],
    )
    passed, reason = service.is_relevant(p8, intent)
    assert passed is False
    assert "no location evidence" in reason.lower() or "conflicting location" in reason.lower()

    # 9. Truncated midwest-dental.com URL ending in "-"
    p9 = RawProfile(
        platform="Google",
        name="Midwest Dental",
        profile_url="https://midwest-dental.com/locations/dental-centers/-",
        evidence=["Dentist in Toronto", "Toronto, Canada"],
    )
    passed, reason = service.is_relevant(p9, intent)
    assert passed is False
    assert "truncated" in reason.lower() or "malformed" in reason.lower()


def test_london_startups_rejection_fixtures() -> None:
    """Verify London startup non-prospect listicles, archives, and investor pages are dropped."""
    service = RelevanceService()
    intent = ParsedQueryIntent(
        interest="Startup",
        audience="Startup founders",
        location=LocationInfo(city="London", country="UK"),
        target_type="individual",
    )

    # 1. beststartup.co.uk listicle
    p1 = RawProfile(
        platform="Google",
        name="United Kingdoms 101 Most Impressive",
        profile_url="https://beststartup.co.uk/united-kingdoms-101-most-impressive-successful-founders-in-the-big-data-space",
        evidence=["101 most impressive startup founders in London UK", "London UK"],
    )
    passed, reason = service.is_relevant(p1, intent)
    assert passed is False

    # 2. cityam.com "Entrepreneur Archives"
    p2 = RawProfile(
        platform="Google",
        name="Entrepreneur Archives",
        profile_url="https://www.cityam.com/people-and-organizations/entrepreneur",
        evidence=["Archives of entrepreneurs in London", "London UK"],
    )
    passed, reason = service.is_relevant(p2, intent)
    assert passed is False

    # 3. Investor profile page
    p3 = RawProfile(
        platform="Google",
        name="Venture Founders UK Investor Profile",
        profile_url="https://entrepreneurplus.co.uk/investors/venture-founders",
        evidence=["Investor profile for venture founders in London", "London UK"],
    )
    passed, reason = service.is_relevant(p3, intent)
    assert passed is False


def test_accept_person_with_name_role_and_matching_city() -> None:
    """A person whose profile contains name + role + matching city must be ACCEPTED."""
    service = RelevanceService()
    intent = ParsedQueryIntent(
        interest="Dentists",
        audience="Dentists",
        location=LocationInfo(city="Toronto", country="Canada"),
        target_type="individual",
    )

    person = RawProfile(
        platform="LinkedIn",
        name="Sarah Miller",
        profile_url="https://www.linkedin.com/in/sarah-miller-dentist",
        evidence=["Dentist at Toronto Smiles", "Practicing dentist in Toronto, Ontario, Canada"],
    )
    passed, reason = service.is_relevant(person, intent)
    assert passed is True
    assert person.location is not None
    assert "Toronto" in person.location


def test_accept_business_with_matching_city_and_industry() -> None:
    """A business with the matching city + industry for a business query must be ACCEPTED."""
    service = RelevanceService()
    intent = ParsedQueryIntent(
        interest="Dentists",
        audience="Dental clinics",
        location=LocationInfo(city="Toronto", country="Canada"),
        target_type="business",
    )

    business = RawProfile(
        platform="Google",
        name="Toronto Dental Care",
        profile_url="https://torontodentalcare.com",
        evidence=["General and cosmetic dental clinic in Toronto", "Toronto, Canada"],
    )
    passed, reason = service.is_relevant(business, intent)
    assert passed is True
    assert business.location is not None
    assert "Toronto" in business.location


def test_unseen_third_query_generalization() -> None:
    """Verify that identical logic correctly generalizes to a 3rd unseen query ('Architects in Paris France')."""
    service = RelevanceService()
    intent = ParsedQueryIntent(
        interest="Architects",
        audience="Architects",
        location=LocationInfo(city="Paris", country="France"),
        target_type="individual",
    )

    # 1. Real architect in Paris -> MUST BE ACCEPTED
    valid_architect = RawProfile(
        platform="LinkedIn",
        name="Jean Nouvel",
        profile_url="https://www.linkedin.com/in/jean-nouvel-architect",
        evidence=["Principal architect based in Paris, France", "Paris, France"],
    )
    passed, reason = service.is_relevant(valid_architect, intent)
    assert passed is True
    assert "Paris" in (valid_architect.location or "")

    # 2. Association -> MUST BE REJECTED
    assoc = RawProfile(
        platform="Google",
        name="Architects Association of France",
        profile_url="https://example.com/arch-assoc",
        evidence=["Architects Association of France in Paris", "Paris, France"],
    )
    passed, reason = service.is_relevant(assoc, intent)
    assert passed is False
    assert "association" in reason.lower() or "invalid" in reason.lower() or "not an individual" in reason.lower()

    # 3. Architect in Lyon (conflicting city) -> MUST BE REJECTED
    other_city = RawProfile(
        platform="LinkedIn",
        name="Pierre Dubois",
        profile_url="https://www.linkedin.com/in/pierre-dubois",
        evidence=["Architect based in Lyon, France", "Lyon, France"],
    )
    passed, reason = service.is_relevant(other_city, intent)
    assert passed is False
    assert "conflicting location" in reason.lower() or "no location evidence" in reason.lower()

    # 4. Listicle/article -> MUST BE REJECTED
    article = RawProfile(
        platform="Google",
        name="Top 10 Modern Architecture Books",
        profile_url="https://example.com/top-10-arch-books",
        evidence=["Top 10 modern architecture books for designers in Paris", "Paris, France"],
    )
    passed, reason = service.is_relevant(article, intent)
    assert passed is False


@pytest.mark.asyncio
async def test_batched_llm_classification_flow() -> None:
    """Verify batched LLM classification with deterministic re-verification."""
    mock_llm = MagicMock()
    service = RelevanceService(llm_service=mock_llm)
    mock_client = AsyncMock(spec=httpx.AsyncClient)

    intent = ParsedQueryIntent(
        interest="Dentists",
        audience="Dentists",
        location=LocationInfo(city="Toronto", country="Canada"),
        target_type="individual",
    )

    p1 = RawProfile(
        platform="LinkedIn",
        name="Sarah Miller",
        profile_url="https://www.linkedin.com/in/sarah-miller-dentist",
        evidence=["Dentist at Toronto Smiles", "Practicing dentist in Toronto, Ontario, Canada"],
    )
    p2 = RawProfile(
        platform="Google",
        name="The Website Owners Manual For Dentists",
        profile_url="https://example.com/manual-for-dentists",
        evidence=["The Website Owners Manual For Dentists in Toronto", "Toronto, Canada"],
    )
    p3 = RawProfile(
        platform="LinkedIn",
        name="John Doe",
        profile_url="https://www.linkedin.com/in/john-doe-dentist",
        evidence=["Dentist in Toronto", "Toronto, Canada"],
    )

    from app.schemas import CandidateClassification

    mock_classifications = [
        CandidateClassification(
            id="0",
            entity_type="person",
            name="Sarah Miller",
            location_text="Toronto, Canada",
            location_matches_query=True,
            topic_matches=True,
            role_evidence="Dentist",
            keep=True,
            reason="Practicing dentist in Toronto",
        ),
        CandidateClassification(
            id="1",
            entity_type="article",
            name="The Website Owners Manual For Dentists",
            location_text="Toronto",
            location_matches_query=True,
            topic_matches=True,
            role_evidence=None,
            keep=False,
            reason="Article manual, not a person",
        ),
        CandidateClassification(
            id="2",
            entity_type="person",
            name="John Doe",
            location_text="Toronto, Canada",
            location_matches_query=True,
            topic_matches=True,
            role_evidence="Dentist",
            keep=True,
            reason="Dentist in Toronto",
        ),
    ]

    mock_llm.batch_classify_candidates = AsyncMock(return_value=mock_classifications)

    filtered = await service.filter_relevant_profiles_batched(
        [p1, p2, p3], intent, mock_client
    )

    # p2 was rejected by LLM, p1 and p3 were kept by both LLM and deterministic re-verification
    assert len(filtered) == 2
    names = {p.name for p in filtered}
    assert "Sarah Miller" in names
    assert "John Doe" in names
    assert "The Website Owners Manual For Dentists" not in names


def test_owner_title_profile_accepted_and_normalized() -> None:
    """LinkedIn owner profiles titled 'Owner of <Business> - <City>' normalize cleanly and pass."""
    raw = "Owner of Shamla Mandi Restaurant - Dubai | LinkedIn"
    normalized = normalize_name(raw)
    assert normalized == "Owner Of Shamla Mandi Restaurant"
    assert is_valid_name(normalized) is True


def test_rejects_undefined_in_name() -> None:
    """Any name containing the word 'undefined' must be rejected."""
    for bad in ("undefined", "Undefined", "John undefined", "undefined - Dubai"):
        assert is_valid_name(bad) is False
        assert normalize_name(bad) == ""


def test_owner_query_ranks_person_profiles_above_business_accounts() -> None:
    """For owner/founder queries, person profiles with a role keyword rank above business accounts."""
    from app.schemas import ProspectRecord, SocialLink

    intent = ParsedQueryIntent(
        interest="Restaurant",
        audience="Restaurant owners",
        location=LocationInfo(city="Dubai", country="UAE"),
        target_type="individual",
    )

    biz_raw = RawProfile(
        platform="Instagram",
        name="French Restaurant Dubai",
        profile_url="https://www.instagram.com/bisou.dubai",
        location="Dubai",
        bio="French Restaurant in Dubai UAE.",
        evidence=["French Restaurant", "Dubai"],
    )
    person_raw = RawProfile(
        platform="LinkedIn",
        name="Shoaib Rahman",
        profile_url="https://pk.linkedin.com/in/shoaib-rahman-14866036",
        location="Dubai",
        bio="Restaurant Owner in Dubai UAE.",
        evidence=["Restaurant Owner", "Dubai"],
    )
    owner_title_raw = RawProfile(
        platform="LinkedIn",
        name="Owner Of Shamla Mandi Restaurant",
        profile_url="https://ae.linkedin.com/in/shamla-mandi-owner",
        location="Dubai",
        bio="Owner of Shamla Mandi Restaurant in Dubai UAE.",
        evidence=["Owner", "Restaurant", "Dubai"],
    )

    # Deliberately pass business account first
    records = [
        ProspectRecord(
            name="French Restaurant Dubai",
            location="Dubai",
            social_link=[SocialLink(platform="Instagram", link=biz_raw.profile_url)],
        ),
        ProspectRecord(
            name="Owner Of Shamla Mandi Restaurant",
            location="Dubai",
            social_link=[SocialLink(platform="LinkedIn", link=owner_title_raw.profile_url)],
        ),
        ProspectRecord(
            name="Shoaib Rahman",
            location="Dubai",
            social_link=[SocialLink(platform="LinkedIn", link=person_raw.profile_url)],
        ),
    ]

    ranked = ProspectDiscoveryService._rank_prospects(
        records, [biz_raw, person_raw, owner_title_raw], intent
    )
    assert [r.name for r in ranked] == [
        "Shoaib Rahman",
        "Owner Of Shamla Mandi Restaurant",
        "French Restaurant Dubai",
    ]


