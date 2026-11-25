"""Entity resolution and profile deduplication service."""

import logging
from typing import Any

from app.platforms.base import RawProfile
from app.schemas import PlatformName, ProspectRecord, SocialLink
from app.services.profile_extractor import ProfileExtractor
from app.utils.normalizers import (
    extract_username_from_url,
    handles_share_fragment,
    has_mojibake,
    is_valid_name,
    names_match,
    normalize_name,
)

logger = logging.getLogger("prospect.services.deduplication")


class DeduplicationService:
    """Consolidates multiple profiles belonging to the same individual without false merges."""

    def __init__(self, extractor: ProfileExtractor | None = None) -> None:
        self.extractor = extractor or ProfileExtractor()

    def consolidate_profiles(
        self,
        raw_profiles: list[RawProfile],
        default_location: str | None = None,
        expected_country: str | None = None,
        tracker: Any | None = None,
    ) -> list[ProspectRecord]:
        """Group matching raw profiles and produce consolidated ProspectRecord instances."""
        if not raw_profiles:
            return []

        # Ensure all profiles have been enriched with emails and contacts from bio
        enriched_profiles = [
            self.extractor.extract_and_enrich_profile(p, expected_country=expected_country)
            if (not p.email or not p.whatsapp_link)
            else p
            for p in raw_profiles
        ]

        clusters: list[list[RawProfile]] = []

        for candidate in enriched_profiles:
            matched_cluster: list[RawProfile] | None = None

            for cluster in clusters:
                if self._is_same_person(candidate, cluster):
                    matched_cluster = cluster
                    break

            if matched_cluster is not None:
                matched_cluster.append(candidate)
            else:
                clusters.append([candidate])

        if tracker:
            tracker.dedup_clusters = len(clusters)
            tracker.dedup_merged = sum(len(c) - 1 for c in clusters if len(c) > 1)

        # Build final consolidated ProspectRecords
        consolidated: list[ProspectRecord] = []
        for cluster in clusters:
            record = self._build_prospect_record(cluster, default_location)
            if record:
                consolidated.append(record)

        if tracker:
            tracker.final_returned = len(consolidated)

        return consolidated

    def _is_same_person(self, candidate: RawProfile, cluster: list[RawProfile]) -> bool:
        """Conservative matching logic requiring >= 2 agreeing signals. Never merges on name alone."""
        cand_name = normalize_name(candidate.name) or candidate.name
        cand_handle = extract_username_from_url(candidate.profile_url)
        cand_email = candidate.email.lower() if candidate.email else None
        cand_phone = candidate.phone
        cand_text = " ".join([candidate.bio or ""] + candidate.evidence)

        for existing in cluster:
            # Exact same URL is always the same account
            if candidate.profile_url.rstrip("/").lower() == existing.profile_url.rstrip("/").lower():
                return True

            exist_name = normalize_name(existing.name) or existing.name
            exist_handle = extract_username_from_url(existing.profile_url)
            exist_email = existing.email.lower() if existing.email else None
            exist_phone = existing.phone
            exist_text = " ".join([existing.bio or ""] + existing.evidence)

            # Two distinct handles on the same platform are distinct accounts unless email/phone matches
            if (
                candidate.platform == existing.platform
                and cand_handle
                and exist_handle
                and cand_handle != exist_handle
            ):
                if not ((cand_email and exist_email and cand_email == exist_email) or (cand_phone and exist_phone and cand_phone == exist_phone)):
                    continue

            signals: list[str] = []

            name_matched = names_match(cand_name, exist_name)
            if name_matched:
                signals.append("name")

            contact_matched = bool(
                (cand_email and exist_email and cand_email == exist_email)
                or (cand_phone and exist_phone and cand_phone == exist_phone)
            )
            if contact_matched:
                signals.append("contact")

            if handles_share_fragment(cand_handle, exist_handle):
                signals.append("handle")

            if candidate.location and existing.location:
                c_loc = candidate.location.lower()
                e_loc = existing.location.lower()
                if c_loc == e_loc or c_loc in e_loc or e_loc in c_loc:
                    signals.append("location")

            if (
                (candidate.profile_url and candidate.profile_url in exist_text)
                or (existing.profile_url and existing.profile_url in cand_text)
            ):
                signals.append("cross_link")

            # Require >= 2 agreeing signals AND either a name match or verified contact match
            if len(signals) >= 2 and (name_matched or contact_matched):
                logger.info(
                    "Merged '%s' (%s) with '%s' (%s) — agreeing signals: %s",
                    candidate.name,
                    candidate.profile_url,
                    existing.name,
                    existing.profile_url,
                    ", ".join(signals),
                )
                return True

            if name_matched:
                logger.info(
                    "Kept '%s' (%s) separate from '%s' (%s) — name match alone insufficient (signals: %s)",
                    candidate.name,
                    candidate.profile_url,
                    existing.name,
                    existing.profile_url,
                    ", ".join(signals),
                )

        return False

    def _build_prospect_record(
        self, cluster: list[RawProfile], default_location: str | None
    ) -> ProspectRecord | None:
        """Merge a cluster of verified profiles into a single ProspectRecord with at most 1 link per platform."""
        if not cluster:
            return None

        # Pick best representative name (most complete / capitalized)
        valid_names = [
            normalize_name(p.name)
            for p in cluster
            if normalize_name(p.name)
            and is_valid_name(normalize_name(p.name))
            and not has_mojibake(normalize_name(p.name))
        ]
        if not valid_names:
            return None

        best_name = max(valid_names, key=len)
        if not is_valid_name(best_name) or has_mojibake(best_name):
            return None

        # Location preference: specific discovered location from evidence only
        locations = [p.location for p in cluster if p.location]
        best_location = locations[0] if locations else default_location

        # Deduplicate and consolidate social links: ONE LINK PER PLATFORM (Fix 9)
        social_links: list[SocialLink] = []
        seen_platforms: set[PlatformName] = set()

        for p in cluster:
            # 1. Primary profile URL
            if p.profile_url and p.platform not in seen_platforms:
                social_links.append(SocialLink(platform=p.platform, link=p.profile_url))
                seen_platforms.add(p.platform)

            # 2. Public WhatsApp link
            if p.whatsapp_link and "WhatsApp" not in seen_platforms:
                social_links.append(SocialLink(platform="WhatsApp", link=p.whatsapp_link))
                seen_platforms.add("WhatsApp")

            # 3. Public Email address (formatted as mailto:<email>)
            if p.email and "Email" not in seen_platforms:
                email_link = f"mailto:{p.email}"
                social_links.append(SocialLink(platform="Email", link=email_link))
                seen_platforms.add("Email")

            # 4. Embedded social links found in bio
            bio_text = " ".join([p.bio or ""] + p.evidence)
            embedded = self.extractor.extract_embedded_social_links(bio_text)
            for plat, link in embedded:
                if plat not in seen_platforms:
                    social_links.append(SocialLink(platform=plat, link=link))
                    seen_platforms.add(plat)

        return ProspectRecord(
            name=best_name,
            location=best_location,
            social_link=social_links,
        )
