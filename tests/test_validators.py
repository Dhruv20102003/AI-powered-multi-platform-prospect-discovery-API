"""Unit tests for validators (strict profile URLs, non-prospect pages, country codes, SSRF)."""

import pytest

from app.core.exceptions import SSRFSecurityViolation
from app.core.security import validate_url_for_ssrf
from app.utils.validators import (
    derive_platform_from_url,
    is_non_prospect_page,
    validate_email_address,
    validate_phone_number,
    validate_platform_url,
    validate_whatsapp_link,
)


def test_email_validation() -> None:
    # Valid emails
    assert validate_email_address("john.smith@gmail.com") == "john.smith@gmail.com"
    assert validate_email_address("mailto:founder@startup.io") == "founder@startup.io"
    assert validate_email_address("contact@agency-ny.com") == "contact@agency-ny.com"

    # Invalid / synthetic / example emails
    assert validate_email_address("invalid-email") is None
    assert validate_email_address("@nodomain.com") is None
    assert validate_email_address("user@example.com") is None
    assert validate_email_address("test@test.com") is None
    assert validate_email_address("") is None


def test_phone_validation_with_country_sanity_check() -> None:
    # Valid US numbers in E.164
    assert validate_phone_number("+1 202-555-0143", expected_country="USA") == "+12025550143"
    assert validate_phone_number("202-555-0199", expected_country="USA") == "+12025550199"

    # Mismatched country (Pakistan +92 or Egypt +20 for USA query) -> REJECTED
    assert validate_phone_number("+92 319 4182036", expected_country="USA") is None
    assert validate_phone_number("+20 1515931120", expected_country="USA") is None

    # Valid UK number
    assert validate_phone_number("+44 20 7946 0991", expected_country="UK") == "+442079460991"

    # Valid India number
    assert validate_phone_number("+91 98765 43210", expected_country="India") == "+919876543210"

    # Invalid numbers
    assert validate_phone_number("12345") is None
    assert validate_phone_number("not-a-number") is None
    assert validate_phone_number("") is None


def test_whatsapp_link_validation_and_country_sanity() -> None:
    # Valid US format
    assert validate_whatsapp_link("https://wa.me/12025550143", expected_country="USA") == "https://wa.me/12025550143"
    assert validate_whatsapp_link("wa.me/12025550143", expected_country="USA") == "https://wa.me/12025550143"

    # Standalone wa.me/92... (Pakistan) for New York USA query -> REJECTED
    assert validate_whatsapp_link("https://wa.me/923194182036", expected_country="USA") is None
    assert validate_whatsapp_link("https://wa.me/201515931120", expected_country="USA") is None

    # Valid India link
    assert (
        validate_whatsapp_link("https://api.whatsapp.com/send?phone=919876543210", expected_country="India")
        == "https://wa.me/919876543210"
    )

    # Invalid links
    assert validate_whatsapp_link("https://wa.me/abc") is None
    assert validate_whatsapp_link("not a link") is None
    assert validate_whatsapp_link("") is None


def test_strict_profile_urls_accepted() -> None:
    """Real profile URLs must be accepted and canonicalized."""
    assert validate_platform_url("LinkedIn", "https://www.linkedin.com/in/johnsmith") == "https://www.linkedin.com/in/johnsmith"
    assert validate_platform_url("LinkedIn", "https://in.linkedin.com/in/kandukuri-dharma") == "https://in.linkedin.com/in/kandukuri-dharma"
    assert validate_platform_url("Instagram", "https://www.instagram.com/johnsmith") == "https://www.instagram.com/johnsmith"
    assert validate_platform_url("X", "https://x.com/johnsmith") == "https://x.com/johnsmith"
    assert validate_platform_url("X", "https://twitter.com/johnsmith") == "https://x.com/johnsmith"
    assert validate_platform_url("YouTube", "https://www.youtube.com/@johnsmith") == "https://www.youtube.com/@johnsmith"
    assert validate_platform_url("YouTube", "https://www.youtube.com/channel/UC123456789") == "https://www.youtube.com/channel/UC123456789"
    assert validate_platform_url("Threads", "https://www.threads.net/@johnsmith") == "https://www.threads.net/@johnsmith"


def test_rejected_non_profile_urls_from_saved_results() -> None:
    """Assert real non-profile URLs from saved results are strictly REJECTED."""
    # Instagram reel
    assert derive_platform_from_url("https://www.instagram.com/reel/DctWEsXI6pD/") is None
    assert derive_platform_from_url("https://www.instagram.com/p/Dd8HaOfmg00/") is None

    # Facebook group post / video / share
    assert derive_platform_from_url("https://www.facebook.com/groups/restaurantownersindubaiuae/posts/4326746650976759/") is None
    assert derive_platform_from_url("https://www.facebook.com/RallyHouse/videos/we-had-a-great-time-spending-the-day/1057861627056765/") is None
    assert derive_platform_from_url("https://www.facebook.com/valiantcricketteam/posts/valiant-cricket/3272926669433000/") is None

    # YouTube watch / shorts / playlist
    assert derive_platform_from_url("https://www.youtube.com/watch?v=CyRAWMpPgSY") is None
    assert derive_platform_from_url("https://www.youtube.com/shorts/12345") is None

    # Reddit thread
    assert derive_platform_from_url("https://www.reddit.com/r/ukstartups/comments/1q0phdr/whats_the_best_place/") is None

    # Salesforce article / Stripe careers / Nesta PDF
    assert derive_platform_from_url("https://www.salesforce.com/blog/what-is-digital-marketing/") is None
    assert derive_platform_from_url("https://stripe.com/jobs/careers/engineering") is None
    assert derive_platform_from_url("https://www.nesta.org.uk/report/startup-founders.pdf") is None

    # News topic page
    assert is_non_prospect_page("https://www.theguardian.com/football/news") is True

    # Pro Football Hall of Fame site for Washington DC query
    assert is_non_prospect_page("https://www.profootballhof.com/") is True


def test_platform_label_derived_from_domain_only() -> None:
    """Assert platform label is derived purely from the URL's domain, never labeled Google."""
    # Instagram URL is labeled Instagram
    res_ig = derive_platform_from_url("https://www.instagram.com/dcunited")
    assert res_ig is not None
    assert res_ig[0] == "Instagram"
    assert res_ig[0] != "Google"

    # LinkedIn URL is labeled LinkedIn
    res_li = derive_platform_from_url("https://in.linkedin.com/in/kandukuri-dharma-rakshith-54088a1a7")
    assert res_li is not None
    assert res_li[0] == "LinkedIn"
    assert res_li[0] != "Google"

    # X / Twitter URL is labeled X
    res_x = derive_platform_from_url("https://twitter.com/dcfootballfans")
    assert res_x is not None
    assert res_x[0] == "X"
    assert res_x[0] != "Google"

    # Real personal/business website is labeled Google
    res_web = derive_platform_from_url("https://expertsinhospitality.me/maurizio-ferraiuolo")
    assert res_web is not None
    assert res_web[0] == "Google"


def test_ssrf_protection_blocks_internal_and_loopback_ips() -> None:
    # Loopback targets
    with pytest.raises(SSRFSecurityViolation):
        validate_url_for_ssrf("http://localhost:8000/secret")

    with pytest.raises(SSRFSecurityViolation):
        validate_url_for_ssrf("http://127.0.0.1/admin")

    # Cloud metadata endpoint
    with pytest.raises(SSRFSecurityViolation):
        validate_url_for_ssrf("http://169.254.169.254/latest/meta-data")

    # Invalid schemes
    with pytest.raises(SSRFSecurityViolation):
        validate_url_for_ssrf("file:///etc/passwd")

    with pytest.raises(SSRFSecurityViolation):
        validate_url_for_ssrf("ftp://example.com/file")

    # Credentials in URL
    with pytest.raises(SSRFSecurityViolation):
        validate_url_for_ssrf("http://user:pass@example.com/")

    # IPv4-mapped IPv6 addresses
    with pytest.raises(SSRFSecurityViolation):
        validate_url_for_ssrf("http://[::ffff:127.0.0.1]/status")

    with pytest.raises(SSRFSecurityViolation):
        validate_url_for_ssrf("http://[::ffff:169.254.169.254]/latest/meta-data")

    # Alternate IP representations (decimal, hex, octal)
    with pytest.raises(SSRFSecurityViolation):
        validate_url_for_ssrf("http://2130706433/secret")

    with pytest.raises(SSRFSecurityViolation):
        validate_url_for_ssrf("http://0x7f000001/admin")

    with pytest.raises(SSRFSecurityViolation):
        validate_url_for_ssrf("http://0x7f.0.0.1/admin")

    with pytest.raises(SSRFSecurityViolation):
        validate_url_for_ssrf("http://0177.0.0.1/admin")
