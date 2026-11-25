"""Public profile and contact extraction utility."""

import logging
import re

from app.platforms.base import RawProfile
from app.schemas import PlatformName
from app.utils.validators import (
    derive_platform_from_url,
    validate_email_address,
    validate_phone_number,
    validate_whatsapp_link,
)

logger = logging.getLogger("prospect.services.extractor")

# Regex to discover raw emails from public bio text
EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")

# Regex to discover phone numbers with country codes or standard local formatting
PHONE_REGEX = re.compile(r"(?:\+?\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}")

# Regex to discover social profile URLs inside bios or page text
SOCIAL_URL_REGEX = re.compile(
    r"https?://[^\s\"'<>]+"
)


class ProfileExtractor:
    """Extracts verified contact details and embedded social links from public profile text."""

    def extract_and_enrich_profile(
        self, profile: RawProfile, expected_country: str | None = None
    ) -> RawProfile:
        """Enrich a RawProfile by scanning its bio and evidence for emails, phones, and social links."""
        combined_text = " ".join([profile.bio or ""] + profile.evidence + [profile.profile_url])

        # 1. Discover email address
        if not profile.email:
            for match in EMAIL_REGEX.finditer(combined_text):
                valid_email = validate_email_address(match.group(0))
                if valid_email:
                    profile.email = valid_email
                    break

        # 2. Discover WhatsApp link (sanity checked against query country)
        if not profile.whatsapp_link:
            wa_link = validate_whatsapp_link(combined_text, expected_country=expected_country)
            if wa_link:
                profile.whatsapp_link = wa_link

        # 3. Discover phone number (sanity checked against query country)
        if not profile.phone:
            for match in PHONE_REGEX.finditer(combined_text):
                candidate = match.group(0)
                valid_phone = validate_phone_number(candidate, expected_country=expected_country)
                if valid_phone:
                    profile.phone = valid_phone
                    break

        return profile

    def extract_embedded_social_links(
        self, text: str, target_type: str = "individual"
    ) -> list[tuple[PlatformName, str]]:
        """Extract any third-party social profiles referenced in a bio or website text."""
        found: list[tuple[PlatformName, str]] = []
        if not text:
            return found

        for match in SOCIAL_URL_REGEX.finditer(text):
            url = match.group(0)
            derived = derive_platform_from_url(url, target_type=target_type)
            if derived and derived[0] != "Google":  # Only third-party social links
                found.append(derived)

        return found
