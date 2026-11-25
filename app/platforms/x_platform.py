"""X (Twitter) discovery provider using public web indexing and API v2 support."""

import logging
from typing import Any

import httpx

from app.config import get_settings
from app.platforms.base import PlatformProvider, RawProfile
from app.platforms.search_engine import execute_web_search
from app.schemas import ParsedQueryIntent, PlatformName
from app.utils.normalizers import is_valid_name, normalize_name
from app.utils.validators import derive_platform_from_url

logger = logging.getLogger("prospect.platforms.x")


class XPlatformProvider(PlatformProvider):
    """X (Twitter) platform provider."""

    @property
    def platform_name(self) -> PlatformName:
        return "X"

    @property
    def is_search_source_only(self) -> bool:
        return False

    def build_search_query(self, parsed_intent: ParsedQueryIntent) -> str:
        loc_str = parsed_intent.location.city or parsed_intent.location.country or ""
        parts = [
            "(site:x.com OR site:twitter.com)",
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
        settings = get_settings()

        # If Twitter Bearer Token is provided, try direct Twitter API v2
        if settings.TWITTER_BEARER_TOKEN:
            try:
                api_profiles = await self._search_twitter_api(parsed_intent, client)
                if api_profiles:
                    return api_profiles
            except Exception as e:
                logger.warning(f"Twitter API v2 search failed, falling back to web search: {e}")

        # Web indexing fallback
        query_str = self.build_search_query(parsed_intent)
        items = await execute_web_search(query_str, client, num_results=10)
        profiles: list[RawProfile] = []

        for item in items:
            if tracker:
                tracker.record_raw_candidate("X")

            derived = derive_platform_from_url(
                item.link,
                target_type=parsed_intent.target_type,
                title=item.title,
                snippet=item.snippet,
            )
            if not derived:
                if tracker:
                    tracker.record_url_drop(item.link, "X", "Invalid or rejected X profile URL")
                continue

            derived_platform, clean_url = derived
            if tracker:
                tracker.record_url_pass()

            name = normalize_name(item.title)
            if not name or len(name) < 2 or not is_valid_name(name):
                if tracker:
                    tracker.record_name_drop(item.title, "X", f"Invalid extracted name: '{name}'")
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

    async def _search_twitter_api(
        self, parsed_intent: ParsedQueryIntent, client: httpx.AsyncClient
    ) -> list[RawProfile]:
        settings = get_settings()
        headers = {"Authorization": f"Bearer {settings.TWITTER_BEARER_TOKEN}"}
        query = f"{parsed_intent.interest} {parsed_intent.location.to_location_string() or ''}".strip()
        resp = await client.get(
            "https://api.twitter.com/2/users/by",
            params={"usernames": query},
            headers=headers,
        )
        if resp.status_code == 200:
            data = resp.json()
            users = data.get("data", [])
            results: list[RawProfile] = []
            for u in users:
                username = u.get("username", "")
                name = u.get("name", "")
                norm_name = normalize_name(name)
                if username and norm_name:
                    results.append(
                        RawProfile(
                            platform="X",
                            name=norm_name,
                            profile_url=f"https://x.com/{username}",
                            location=None,
                            bio=u.get("description", ""),
                            evidence=[name, u.get("description", "")],
                        )
                    )
            return results
        return []
