"""Coordinator service orchestrating the multi-platform prospect discovery lifecycle."""

import asyncio
import logging
import re
import time

import httpx
from bs4 import BeautifulSoup

from app.config import get_settings
from app.core.security import create_safe_async_client
from app.platforms.base import RawProfile
from app.platforms.search_engine import execute_web_search
from app.schemas import ParsedQueryIntent, PlatformName, ProspectRecord, ProspectResponse
from app.services.deduplication_service import DeduplicationService
from app.services.link_liveness import LinkLivenessChecker
from app.services.profile_extractor import ProfileExtractor
from app.services.query_service import QueryService
from app.services.relevance_service import ROLE_KEYWORDS, RelevanceService
from app.services.search_service import SearchService
from app.services.stage_tracker import StageTracker
from app.utils.normalizers import (
    clean_unicode,
    extract_username_from_url,
    fold_accents,
    handles_share_fragment,
    names_match,
    normalize_name,
)
from app.utils.validators import (
    derive_platform_from_url,
    is_non_prospect_page,
    validate_email_address,
    validate_whatsapp_link,
)

logger = logging.getLogger("prospect.services.coordinator")


class ProspectDiscoveryService:
    """End-to-end coordinator implementing the entire prospect discovery pipeline."""

    def __init__(
        self,
        query_service: QueryService | None = None,
        search_service: SearchService | None = None,
        extractor: ProfileExtractor | None = None,
        relevance_service: RelevanceService | None = None,
        deduplication_service: DeduplicationService | None = None,
        liveness_checker: LinkLivenessChecker | None = None,
    ) -> None:
        self.query_service = query_service or QueryService()
        self.search_service = search_service or SearchService()
        self.extractor = extractor or ProfileExtractor()
        self.relevance_service = relevance_service or RelevanceService()
        self.deduplication_service = deduplication_service or DeduplicationService(self.extractor)
        self.settings = get_settings()
        self.liveness_checker = liveness_checker or LinkLivenessChecker(
            cache_ttl_seconds=self.settings.CACHE_TTL_SECONDS
        )

    async def discover_prospects(self, query: str) -> ProspectResponse:
        """Execute the end-to-end 9-step discovery pipeline with deadline and stage logging."""
        logger.info(f"Initiating prospect discovery pipeline for query: '{query}'")
        tracker = StageTracker(query)
        deadline = self.settings.REQUEST_DEADLINE_SECONDS
        t0 = time.monotonic()

        async with create_safe_async_client() as client:
            try:
                async with asyncio.timeout(deadline):
                    # Step 1 & 2: Natural Language Query Understanding
                    intent = await self.query_service.parse_and_understand_query(query, client)
                    logger.info(
                        f"Parsed intent: interest='{intent.interest}', audience='{intent.audience}', "
                        f"location={intent.location.to_location_string()}, target_type={intent.target_type}"
                    )

                    # Step 3 & 4: Multi-Platform Concurrent Search
                    raw_profiles, _failed_platforms = await self.search_service.discover_across_platforms(
                        intent, client, tracker=tracker
                    )

                    # Step 5: Contact & Profile Extraction & Validation
                    enriched_profiles = [
                        self.extractor.extract_and_enrich_profile(
                            p, expected_country=intent.location.country
                        )
                        for p in raw_profiles
                    ]

                    # Step 6 & 7: Relevance Gate (Strict Interest + Location Evidence required, batched LLM classification)
                    relevant_profiles = await self.relevance_service.filter_relevant_profiles_batched(
                        enriched_profiles, intent, client, tracker=tracker
                    )

                    # Step 8: Outbound Website Link Extraction & Targeted Search Enrichment
                    if relevant_profiles:
                        outbound_matches = await self._enrich_prospects_from_websites(
                            relevant_profiles, client, intent, tracker=tracker
                        )
                        relevant_profiles.extend(outbound_matches)

                        additional_matches = await self._enrich_top_prospects_targeted_search(
                            relevant_profiles, intent, client, tracker=tracker
                        )
                        relevant_profiles.extend(additional_matches)

            except TimeoutError:
                logger.warning(
                    "Request deadline of %0.1fs reached for query '%s'. Returning best partial results.",
                    deadline,
                    query,
                )
                tracker.stages_cut.append("Enrichment (deadline hit)")

            logger.info(
                "Enrichment lookups: %d executed, %d accepted",
                tracker.enrichment_lookups,
                tracker.enrichment_accepted,
            )

            # Step 9: Deduplication and Social Profile Consolidation
            consolidated_prospects = self.deduplication_service.consolidate_profiles(
                relevant_profiles,
                default_location=None,
                expected_country=intent.location.country,
                tracker=tracker,
            )

            # Rank by role/person priority (for owner/founder queries) and evidence strength
            consolidated_prospects = self._rank_prospects(
                consolidated_prospects, relevant_profiles, intent
            )

            # Step 9b: Link-liveness check on final candidate links (respecting request deadline)
            remaining = deadline - (time.monotonic() - t0)
            liveness_budget = min(4.0, max(1.0, remaining))
            consolidated_prospects, _ = await self.liveness_checker.filter_prospects(
                consolidated_prospects,
                client,
                deadline_seconds=liveness_budget,
            )

            max_return = self.settings.MAX_PROSPECTS_RETURNED
            if len(consolidated_prospects) > max_return:
                consolidated_prospects = consolidated_prospects[:max_return]
            tracker.final_returned = len(consolidated_prospects)

            # Step 10: Build and Validate Response Schema
            response = ProspectResponse(
                success=True,
                query=query,
                total_prospects=len(consolidated_prospects),
                prospects=consolidated_prospects,
            )

            tracker.log_summary()
            return response

    @staticmethod
    def _rank_prospects(
        prospects: list[ProspectRecord],
        raw_profiles: list[RawProfile],
        intent: ParsedQueryIntent,
    ) -> list[ProspectRecord]:
        """Rank prospects by evidence strength, prioritizing person profiles with role keywords for owner/founder queries."""
        audience_lower = intent.audience.lower()
        is_owner_founder_query = any(
            r in audience_lower
            for r in ("owner", "founder", "ceo", "director", "partner", "proprietor")
        )

        if not is_owner_founder_query:
            return sorted(
                prospects,
                key=lambda p: (len(p.social_link), 1 if p.location else 0),
                reverse=True,
            )

        # Map profile URLs back to their RawProfile evidence for role & entity inspection
        url_to_raw: dict[str, RawProfile] = {
            p.profile_url: p for p in raw_profiles if p.profile_url
        }

        business_indicators = {
            "restaurant", "cafe", "bistro", "lounge", "kitchen", "bakery", "bar",
            "grill", "gastro", "theatre", "theater", "cuisine", "catering",
            "hospitality", "group", "agency", "studio", "clinic", "company",
            "inc", "llc", "ltd", "corp",
        }
        loc_tokens = set(
            re.findall(r"\w+", (intent.location.to_location_string() or "").lower())
        )
        interest_tokens = set(re.findall(r"\w+", intent.interest.lower()))

        def _score(record: ProspectRecord) -> tuple[int, int, int]:
            matched_raws = [
                url_to_raw[s.link]
                for s in record.social_link
                if s.link in url_to_raw
            ]
            combined_text = " ".join(
                [record.name]
                + [
                    " ".join([r.name, r.bio or ""] + r.evidence)
                    for r in matched_raws
                ]
            ).lower()

            has_role = any(rk in combined_text for rk in ROLE_KEYWORDS)
            has_personal_linkedin = any(
                "linkedin.com/in/" in s.link.lower() for s in record.social_link
            )
            name_tokens = set(re.findall(r"\w+", record.name.lower()))
            looks_like_business_name = bool(
                name_tokens & (business_indicators | loc_tokens | interest_tokens)
            )
            is_person_name = has_personal_linkedin and not looks_like_business_name

            if is_person_name and has_role:
                tier = 3
            elif has_personal_linkedin and has_role:
                tier = 2
            elif has_role and not looks_like_business_name:
                tier = 1
            else:
                tier = 0

            return (tier, len(record.social_link), 1 if record.location else 0)

        return sorted(prospects, key=_score, reverse=True)

    async def _enrich_prospects_from_websites(
        self,
        profiles: list[RawProfile],
        client: httpx.AsyncClient,
        intent: ParsedQueryIntent,
        tracker: StageTracker | None = None,
    ) -> list[RawProfile]:
        """Fetch personal or business websites to extract outbound social links and contact info (Fix 5a)."""
        new_profiles: list[RawProfile] = []
        for p in profiles:
            if p.platform == "Google" and p.profile_url:
                if tracker:
                    tracker.enrichment_lookups += 1
                try:
                    resp = await client.get(p.profile_url, timeout=5.0)
                    if resp.status_code == 200:
                        html_text = clean_unicode(resp.text)
                        soup = BeautifulSoup(html_text, "html.parser")

                        for a_tag in soup.find_all("a", href=True):
                            href = a_tag.get("href")
                            if not isinstance(href, str):
                                continue

                            # 1. Outbound social platforms
                            if href.startswith(("http://", "https://")):
                                derived = derive_platform_from_url(
                                    href, target_type=intent.target_type
                                )
                                if derived and derived[0] != "Google":
                                    plat, valid_url = derived
                                    if plat == "WhatsApp":
                                        wa = validate_whatsapp_link(
                                            valid_url, expected_country=intent.location.country
                                        )
                                        if wa and not p.whatsapp_link:
                                            p.whatsapp_link = wa
                                            if tracker:
                                                tracker.enrichment_accepted += 1
                                    else:
                                        # Verify outbound social link belongs to this prospect (name/handle token check)
                                        anchor_text = a_tag.get_text(strip=True) or ""
                                        out_handle = extract_username_from_url(valid_url) or ""
                                        name_tokens = {
                                            t
                                            for t in re.findall(r"[^\W_]+", fold_accents(p.name))
                                            if len(t) >= 4
                                        }
                                        handle_flat = re.sub(r"[^a-z0-9]", "", fold_accents(out_handle))
                                        anchor_matches = names_match(p.name, anchor_text)
                                        handle_matches_name = any(t in handle_flat for t in name_tokens)

                                        if not anchor_matches and not handle_matches_name:
                                            logger.info(
                                                "Dropping outbound website link %s (%s) for '%s': "
                                                "handle/anchor does not match prospect name",
                                                valid_url,
                                                plat,
                                                p.name,
                                            )
                                            continue

                                        new_p = RawProfile(
                                            platform=plat,
                                            name=p.name,
                                            profile_url=valid_url,
                                            location=p.location,
                                            bio=p.bio,
                                            evidence=[f"Website outbound link from {p.profile_url}"],
                                        )
                                        new_profiles.append(new_p)
                                        p.evidence.append(f"{plat}: {valid_url}")
                                        if tracker:
                                            tracker.enrichment_accepted += 1

                            # 2. Email
                            elif href.startswith("mailto:"):
                                email = validate_email_address(href)
                                if email and not p.email:
                                    p.email = email
                                    if tracker:
                                        tracker.enrichment_accepted += 1

                            # 3. WhatsApp link
                            elif "wa.me" in href:
                                wa = validate_whatsapp_link(
                                    href, expected_country=intent.location.country
                                )
                                if wa and not p.whatsapp_link:
                                    p.whatsapp_link = wa
                                    if tracker:
                                        tracker.enrichment_accepted += 1

                except Exception as e:
                    logger.debug(f"Website outbound extraction skipped for {p.profile_url}: {e}")

        return new_profiles

    async def _enrich_top_prospects_targeted_search(
        self,
        profiles: list[RawProfile],
        intent: ParsedQueryIntent,
        client: httpx.AsyncClient,
        tracker: StageTracker | None = None,
    ) -> list[RawProfile]:
        """Perform targeted cross-platform searches for top N prospects within budget (Fix 5b)."""
        additional: list[RawProfile] = []
        top_prospects = profiles[: self.settings.MAX_ENRICHMENT_PROSPECTS]
        budget_remaining = self.settings.MAX_SEARCH_CALLS_PER_REQUEST
        city = intent.location.city or intent.location.country or ""

        target_platforms: list[tuple[str, PlatformName]] = [
            ("site:instagram.com", "Instagram"),
            ("site:twitter.com", "X"),
            ("site:linkedin.com/in/", "LinkedIn"),
        ]

        for prospect in top_prospects:
            if budget_remaining <= 0:
                break

            calls_for_prospect = 0
            for site_dork, plat_name in target_platforms:
                if (
                    budget_remaining <= 0
                    or calls_for_prospect >= self.settings.MAX_SEARCH_CALLS_PER_PROSPECT
                ):
                    break

                if prospect.platform == plat_name:
                    continue

                search_query = f'{site_dork} "{prospect.name}" {city}'.strip()
                if tracker:
                    tracker.enrichment_lookups += 1

                try:
                    items = await execute_web_search(search_query, client, num_results=3)
                    budget_remaining -= 1
                    calls_for_prospect += 1

                    for item in items:
                        derived = derive_platform_from_url(
                            item.link,
                            target_type=intent.target_type,
                            title=item.title,
                            snippet=item.snippet,
                        )
                        if not derived or derived[0] != plat_name:
                            continue

                        _, valid_url = derived
                        item_name = normalize_name(item.title)

                        # Check (a): normalized, accent-insensitive, token-based name match
                        if not item_name or not names_match(item_name, prospect.name):
                            logger.info(
                                "Dropping targeted enrichment link %s (%s) for '%s': "
                                "result title name '%s' does not match prospect name",
                                valid_url,
                                plat_name,
                                prospect.name,
                                item_name or item.title,
                            )
                            continue

                        # Reject non-prospect pages (articles, directories, etc.)
                        if is_non_prospect_page(valid_url, item_name, item.snippet):
                            logger.info(
                                "Dropping targeted enrichment link %s (%s) for '%s': "
                                "non-prospect page",
                                valid_url,
                                plat_name,
                                prospect.name,
                            )
                            continue

                        evidence_str = f"{item.title} {item.snippet}"

                        # Reject conflicting location in result text
                        conflict = self.relevance_service.check_conflicting_location(
                            evidence_str, intent.location
                        )
                        if conflict:
                            logger.info(
                                "Dropping targeted enrichment link %s (%s) for '%s': "
                                "conflicting location '%s'",
                                valid_url,
                                plat_name,
                                prospect.name,
                                conflict,
                            )
                            continue

                        # Check (b): at least one additional agreeing signal
                        agreeing_signals: list[str] = []
                        loc_match = self.relevance_service.extract_location_evidence(
                            evidence_str, intent.location
                        )
                        if loc_match:
                            agreeing_signals.append(f"location='{loc_match}'")

                        cand_handle = extract_username_from_url(valid_url)
                        pros_handle = extract_username_from_url(prospect.profile_url)
                        if handles_share_fragment(cand_handle, pros_handle):
                            agreeing_signals.append("shared_handle")

                        pros_text = " ".join([prospect.bio or ""] + prospect.evidence)
                        if (
                            valid_url in pros_text
                            or (prospect.profile_url and prospect.profile_url in evidence_str)
                        ):
                            agreeing_signals.append("cross_link")

                        if not agreeing_signals:
                            logger.info(
                                "Dropping targeted enrichment link %s (%s) for '%s': "
                                "name matched but no second agreeing signal (location/handle/cross-link)",
                                valid_url,
                                plat_name,
                                prospect.name,
                            )
                            continue

                        logger.info(
                            "Accepted targeted enrichment link %s (%s) for '%s' — "
                            "signals: name + %s",
                            valid_url,
                            plat_name,
                            prospect.name,
                            ", ".join(agreeing_signals),
                        )
                        additional.append(
                            RawProfile(
                                platform=plat_name,
                                name=item_name,
                                profile_url=valid_url,
                                location=loc_match or prospect.location,
                                bio=item.snippet,
                                evidence=[
                                    f"Targeted search match ({', '.join(agreeing_signals)}) for {prospect.name}: {item.title}"
                                ],
                            )
                        )
                        if tracker:
                            tracker.enrichment_accepted += 1
                        break  # Found best hit for this platform, move to next
                except Exception as e:
                    logger.debug(f"Targeted search failed for '{search_query}': {e}")

        return additional
