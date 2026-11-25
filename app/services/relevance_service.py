"""Relevance scoring engine: positive evidence, location/entity verification, and batched classification."""

import logging
import re
from typing import Any

import httpx

from app.platforms.base import RawProfile
from app.schemas import LocationInfo, ParsedQueryIntent
from app.services.llm_service import LLMService
from app.utils.normalizers import is_valid_name
from app.utils.validators import is_malformed_or_truncated_url, is_non_prospect_page

logger = logging.getLogger("prospect.services.relevance")

# Canonical aliases for cities (STRICTLY city aliases, never general country aliases)
CITY_ALIASES: dict[str, set[str]] = {
    "washington dc": {
        "washington dc",
        "washington d.c.",
        "washington, dc",
        "washington, d.c.",
        "district of columbia",
        "d.c.",
    },
    "new york": {
        "new york",
        "new york city",
        "nyc",
        "manhattan",
        "brooklyn",
        "queens",
        "ny",
    },
    "london": {
        "london",
        "greater london",
    },
    "ahmedabad": {
        "ahmedabad",
        "amdavad",
    },
    "dubai": {
        "dubai",
    },
    "berlin": {
        "berlin",
    },
    "toronto": {
        "toronto",
        "greater toronto",
        "gta",
    },
    "paris": {
        "paris",
    },
}

COUNTRY_ALIASES: dict[str, set[str]] = {
    "usa": {"usa", "united states", "u.s.", "u.s.a.", "america"},
    "uk": {"uk", "united kingdom", "britain", "england", "gb", "great britain"},
    "canada": {"canada"},
    "germany": {"germany", "deutschland"},
    "uae": {"uae", "united arab emirates", "emirates"},
    "india": {"india", "bharat"},
    "france": {"france"},
}

# Major cities/states used for detecting location conflicts across queries
KNOWN_WORLD_CITIES = {
    # US cities and regions
    "las vegas", "florida", "philadelphia", "leesburg", "newtown", "saratoga springs",
    "chicago", "los angeles", "houston", "phoenix", "san diego", "dallas", "austin",
    "san francisco", "seattle", "denver", "boston", "atlanta", "miami", "nashville",
    "pennsylvania", "california", "texas", "ohio", "michigan", "virginia",
    # Canadian cities and provinces
    "toronto", "montreal", "vancouver", "calgary", "ottawa", "ontario", "quebec", "alberta",
    # UK cities
    "london", "manchester", "birmingham", "edinburgh", "glasgow", "leeds", "bristol",
    # German cities
    "berlin", "munich", "hamburg", "frankfurt", "cologne", "stuttgart", "dusseldorf",
    # UAE cities
    "dubai", "abu dhabi", "sharjah",
    # Indian cities
    "ahmedabad", "mumbai", "delhi", "bangalore", "chennai", "kolkata", "pune", "gujarat",
    # European cities
    "paris", "madrid", "barcelona", "rome", "milan", "amsterdam", "vienna", "zurich",
}

# Institutional, organizational, article, or non-prospect entity words
DISALLOWED_ENTITY_TERMS = {
    "union",
    "association",
    "foundation",
    "insurance",
    "insurer",
    "academy",
    "society",
    "federation",
    "council",
    "institute",
    "manual",
    "guide",
    "directory",
    "archives",
    "archive",
    "conference",
    "summit",
    "festival",
    "week",
    "team",
    "league",
    "club",
    "university",
    "college",
    "school",
    "news",
    "times",
    "magazine",
    "channel",
    "network",
    "community",
    "edic",
    "tdic",
}

# Role keywords required for queries targeting owners, founders, CEOs, or directors
ROLE_KEYWORDS = {
    "owner",
    "founder",
    "co-founder",
    "cofounder",
    "ceo",
    "director",
    "partner",
    "principal",
    "proprietor",
    "chef",
    "president",
    "managing director",
    "head of",
    "entrepreneur",
}


class RelevanceService:
    """Evaluates prospect relevance using strict evidence-backed gates and batched LLM classification."""

    MIN_RELEVANCE_THRESHOLD: float = 0.5

    def __init__(self, llm_service: LLMService | None = None) -> None:
        self.llm_service = llm_service or LLMService()

    def score_profile(self, profile: RawProfile, intent: ParsedQueryIntent) -> float:
        """Compatibility method for legacy scoring tests; returns 1.0 if passed, 0.0 if failed."""
        passed, _ = self.is_relevant(profile, intent)
        return 1.0 if passed else 0.0

    def extract_location_evidence(
        self, evidence_text: str, loc: LocationInfo
    ) -> str | None:
        """Check if candidate text contains verified positive evidence of query location."""
        if not evidence_text:
            return None

        text_lower = evidence_text.lower()
        matched_parts: list[str] = []

        # 1. City Check: If query specified city, city evidence is mandatory
        if loc.city:
            city_lower = loc.city.lower()
            aliases = CITY_ALIASES.get(city_lower, {city_lower})
            if any(re.search(r"\b" + re.escape(a) + r"\b", text_lower) for a in aliases):
                matched_parts.append(loc.city)
            else:
                # If city specified but not found in candidate text, location evidence is missing
                return None

        # 2. State Check
        if loc.state:
            state_lower = loc.state.lower()
            if re.search(r"\b" + re.escape(state_lower) + r"\b", text_lower):
                matched_parts.append(loc.state)

        # 3. Country Check
        if loc.country:
            country_lower = loc.country.lower()
            c_aliases = COUNTRY_ALIASES.get(country_lower, {country_lower})
            if any(re.search(r"\b" + re.escape(ca) + r"\b", text_lower) for ca in c_aliases):
                matched_parts.append(loc.country)
            elif not loc.city:
                # If only country was queried and not found, missing
                return None

        if matched_parts:
            # Preserve order, deduplicate
            seen: set[str] = set()
            clean: list[str] = []
            for p in matched_parts:
                if p not in seen:
                    seen.add(p)
                    clean.append(p)
            return ", ".join(clean)

        return None

    def check_conflicting_location(
        self, evidence_text: str, loc: LocationInfo
    ) -> str | None:
        """Detect if candidate text explicitly indicates a different city, region, or country."""
        text_lower = evidence_text.lower()

        # Check for conflicting country
        if loc.country:
            target_country = loc.country.lower()
            target_aliases = COUNTRY_ALIASES.get(target_country, {target_country})
            for c_name, aliases in COUNTRY_ALIASES.items():
                if c_name == target_country or c_name in target_aliases:
                    continue
                # If candidate explicitly states another country (e.g. "location: usa" or "based in india")
                if any(re.search(r"\b" + re.escape(a) + r"\b", text_lower) for a in aliases):
                    # Only conflict if target country is not also present
                    if not any(re.search(r"\b" + re.escape(ta) + r"\b", text_lower) for ta in target_aliases):
                        return c_name.upper()

        # Check for conflicting city/region
        if loc.city:
            target_city = loc.city.lower()
            target_city_aliases = CITY_ALIASES.get(target_city, {target_city})
            # Check for other prominent cities mentioned in the text
            for city in KNOWN_WORLD_CITIES:
                if city in target_city_aliases or city == target_city:
                    continue
                # If another known city appears as a location indicator without target city
                pattern = rf"\b{re.escape(city)}\b"
                if re.search(pattern, text_lower):
                    # Check if target city is present; if not, candidate belongs to another city
                    if not any(re.search(r"\b" + re.escape(a) + r"\b", text_lower) for a in target_city_aliases):
                        return city.title()

        return None

    def is_relevant(
        self, profile: RawProfile, intent: ParsedQueryIntent
    ) -> tuple[bool, str]:
        """Strict deterministic gate implementing the core principle: positive evidence or drop."""
        # 1. URL Gate: Malformed or truncated URL
        if is_malformed_or_truncated_url(profile.profile_url):
            return False, f"Malformed or truncated URL '{profile.profile_url}'"

        # 2. Non-prospect page & domain check
        if is_non_prospect_page(profile.profile_url, profile.name, " ".join(profile.evidence)):
            return False, f"Non-prospect page or domain '{profile.profile_url}'"

        # 3. Entity Name Validity Check
        if not is_valid_name(profile.name):
            return False, f"Invalid or non-prospect name '{profile.name}'"

        # Combine text for evaluation
        evidence_text = " ".join(
            [profile.name, profile.bio or "", profile.profile_url] + profile.evidence
        ).lower()

        # 4. Location Gate (Positive evidence mandatory; conflict means drop)
        loc_str = intent.location.to_location_string()
        if loc_str:
            extracted_loc = self.extract_location_evidence(evidence_text, intent.location)
            if not extracted_loc:
                profile.location = None
                return False, f"No location evidence matching '{loc_str}'"

            conflict = self.check_conflicting_location(evidence_text, intent.location)
            if conflict:
                return False, f"Candidate text indicates conflicting location '{conflict}' instead of target '{loc_str}'"

            profile.location = extracted_loc
        else:
            profile.location = None

        # 5. Topic Gate (Interest / profession must be present)
        interest_tokens = [t.lower() for t in re.findall(r"\w+", intent.interest) if len(t) > 2]
        expanded_tokens = set(interest_tokens)
        expanded_tokens.update(t.rstrip("s") for t in interest_tokens if len(t) > 3)
        expanded_tokens.update(t + "s" for t in interest_tokens)

        # Include specific known synonyms (e.g. dentist -> dental, yoga -> yogi)
        if "dentist" in intent.interest.lower() or "dental" in intent.interest.lower():
            expanded_tokens.update(["dentist", "dentists", "dental", "dentistry", "dds", "dmd"])
        elif "yoga" in intent.interest.lower():
            expanded_tokens.update(["yoga", "yogi", "asana", "vinyasa"])

        matched_interest = [t for t in expanded_tokens if t in evidence_text]
        if expanded_tokens and not matched_interest:
            return False, f"No topic evidence matching '{intent.interest}'"

        # 6. Entity Type Gate: Individual queries MUST NOT be organizations, teams, unions, insurers, etc.
        name_tokens = set(re.findall(r"\w+", profile.name.lower()))
        if intent.target_type == "individual":
            org_indicators = {
                "team", "league", "club", "commanders", "divas", "federation",
                "association", "university", "college", "school", "stadium"
            }
            org_matches = name_tokens & org_indicators
            if org_matches:
                return False, f"Organization indicator found in name: {', '.join(org_matches)}"

            disallowed_matches = name_tokens & DISALLOWED_ENTITY_TERMS
            if disallowed_matches:
                return False, f"Entity type '{', '.join(disallowed_matches)}' is not an individual prospect"

        # 7. Role Requirement Gate for owners/founders
        audience_lower = intent.audience.lower()
        is_role_query = any(
            r in audience_lower
            for r in ("owner", "founder", "ceo", "director", "partner", "proprietor")
        )
        if is_role_query:
            matched_role = next((r for r in ROLE_KEYWORDS if r in evidence_text), None)
            if not matched_role:
                return False, "Missing required executive/founder/owner role keyword in evidence"

            matched_ind = next((ind for ind in interest_tokens if ind in evidence_text), None)
            if not matched_ind:
                return False, f"Missing industry term in evidence matching '{intent.interest}'"

        return True, "Passed strict relevance gate"

    async def filter_relevant_profiles_batched(
        self,
        profiles: list[RawProfile],
        intent: ParsedQueryIntent,
        client: httpx.AsyncClient,
        tracker: Any | None = None,
    ) -> list[RawProfile]:
        """Classify candidates via batched LLM call, then re-verify deterministically."""
        if not profiles:
            return []

        # 1. Run Batched LLM Classification call
        candidates_input = [
            {
                "id": str(i),
                "platform": p.platform,
                "title": p.name,
                "snippet": p.bio or " ".join(p.evidence[:2]),
                "url": p.profile_url,
            }
            for i, p in enumerate(profiles)
        ]

        classifications = await self.llm_service.batch_classify_candidates(
            candidates_input, intent, client
        )
        class_map = {c.id: c for c in classifications}

        # 2. Re-verify deterministically
        relevant: list[RawProfile] = []
        for i, p in enumerate(profiles):
            # Check LLM decision first if available
            c = class_map.get(str(i))
            if c and not c.keep:
                logger.info(
                    "Dropping candidate '%s' (%s): LLM classified as %s (%s)",
                    p.name,
                    p.profile_url,
                    c.entity_type,
                    c.reason,
                )
                if tracker:
                    tracker.record_relevance_drop(
                        p.name, p.platform, f"LLM [{c.entity_type}]: {c.reason}"
                    )
                continue

            # Re-verify deterministically (mandatory for all candidates)
            passed, reason = self.is_relevant(p, intent)
            if passed:
                relevant.append(p)
                if tracker:
                    tracker.record_relevance_pass()
            else:
                logger.info("Dropping candidate '%s' (%s): %s", p.name, p.profile_url, reason)
                if tracker:
                    tracker.record_relevance_drop(p.name, p.platform, reason)

        return relevant

    def filter_relevant_profiles(
        self,
        profiles: list[RawProfile],
        intent: ParsedQueryIntent,
        tracker: Any | None = None,
    ) -> list[RawProfile]:
        """Synchronous deterministic filtering fallback."""
        relevant: list[RawProfile] = []
        for p in profiles:
            passed, reason = self.is_relevant(p, intent)
            if passed:
                relevant.append(p)
                if tracker:
                    tracker.record_relevance_pass()
            else:
                logger.info("Dropping candidate '%s' (%s): %s", p.name, p.profile_url, reason)
                if tracker:
                    tracker.record_relevance_drop(p.name, p.platform, reason)

        return relevant
