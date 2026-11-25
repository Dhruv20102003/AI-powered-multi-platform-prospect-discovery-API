"""Instagram discovery provider for publicly indexed user profiles."""

import logging
from typing import Any

import httpx

from app.platforms.base import PlatformProvider, RawProfile
from app.platforms.search_engine import execute_web_search
from app.schemas import ParsedQueryIntent, PlatformName
from app.utils.normalizers import is_valid_name, normalize_name
from app.utils.validators import derive_platform_from_url

logger = logging.getLogger("prospect.platforms.instagram")


class InstagramPlatformProvider(PlatformProvider):
    """Instagram public profile discovery provider."""

    @property
    def platform_name(self) -> PlatformName:
        return "Instagram"

    @property
    def is_search_source_only(self) -> bool:
        return False

    def build_search_query(self, parsed_intent: ParsedQueryIntent) -> str:
        loc_str = parsed_intent.location.city or parsed_intent.location.country or ""
        parts = [
            "site:instagram.com",
            f'"{parsed_intent.interest}"',
            loc_str,
        ]
        return " ".join(p for p in parts if p).strip()

    async def search(
        self,
        parsed_intent: ParsedQueryIntent,
        client: httpx.AsyncClient,
        tracker: Any | None = None,
    ) -> list[RawProfile]:
        query_str = self.build_search_query(parsed_intent)
        items = await execute_web_search(query_str, client, num_results=10)
        profiles: list[RawProfile] = []

        for item in items:
            if tracker:
                tracker.record_raw_candidate("Instagram")

            derived = derive_platform_from_url(
                item.link,
                target_type=parsed_intent.target_type,
                title=item.title,
                snippet=item.snippet,
            )
            if not derived:
                if tracker:
                    tracker.record_url_drop(item.link, "Instagram", "Invalid or rejected Instagram profile URL")
                continue

            derived_platform, clean_url = derived
            if tracker:
                tracker.record_url_pass()

            name = normalize_name(item.title)
            if not name or len(name) < 2 or not is_valid_name(name):
                if tracker:
                    tracker.record_name_drop(item.title, "Instagram", f"Invalid extracted name: '{name}'")
                continue

            if tracker:
                tracker.record_name_pass()

            profiles.append(
                RawProfile(
                    platform=derived_platform,
                    name=name,
                    profile_url=clean_url,
                    location=None,
                    bio=item.snippet,
                    evidence=[item.title, item.snippet],
                    raw_metadata=item.raw,
                )
            )

        return profiles
