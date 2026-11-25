"""YouTube discovery provider for public creator channels and profiles."""

import logging

import httpx

from app.config import get_settings
from app.platforms.base import PlatformProvider, RawProfile
from app.platforms.search_engine import execute_web_search
from app.schemas import ParsedQueryIntent, PlatformName
from app.utils.normalizers import normalize_name
from app.utils.validators import derive_platform_from_url

logger = logging.getLogger("prospect.platforms.youtube")


class YouTubePlatformProvider(PlatformProvider):
    """YouTube creator and channel discovery provider."""

    @property
    def platform_name(self) -> PlatformName:
        return "YouTube"

    @property
    def is_search_source_only(self) -> bool:
        return False

    def build_search_query(self, parsed_intent: ParsedQueryIntent) -> str:
        loc_str = parsed_intent.location.to_location_string() or ""
        parts = [
            "site:youtube.com",
            "(inurl:@ OR inurl:channel OR inurl:c/)",
            "-inurl:watch -inurl:shorts -inurl:playlist",
            f'"{parsed_intent.interest}"',
            f'"{loc_str}"' if loc_str else "",
        ]
        return " ".join(p for p in parts if p).strip()

    async def search(
        self,
        parsed_intent: ParsedQueryIntent,
        client: httpx.AsyncClient,
    ) -> list[RawProfile]:
        settings = get_settings()
        api_key = settings.YOUTUBE_API_KEY or settings.GOOGLE_CSE_API_KEY

        # 1. Try YouTube Data API v3 if API key available
        if api_key:
            try:
                api_profiles = await self._search_youtube_api(parsed_intent, client, api_key)
                if api_profiles:
                    return api_profiles
            except Exception as e:
                logger.warning(f"YouTube Data API v3 call failed, falling back to web search: {e}")

        # 2. Web search fallback
        query_str = self.build_search_query(parsed_intent)
        items = await execute_web_search(query_str, client, num_results=10)
        profiles: list[RawProfile] = []

        for item in items:
            derived = derive_platform_from_url(
                item.link,
                target_type=parsed_intent.target_type,
                title=item.title,
                snippet=item.snippet,
            )
            if not derived:
                continue

            derived_platform, clean_url = derived
            name = normalize_name(item.title)
            if not name or len(name) < 2:
                continue

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

    async def _search_youtube_api(
        self,
        parsed_intent: ParsedQueryIntent,
        client: httpx.AsyncClient,
        api_key: str,
    ) -> list[RawProfile]:
        url = "https://www.googleapis.com/youtube/v3/search"
        q = f"{parsed_intent.interest} {parsed_intent.location.to_location_string() or ''}".strip()
        params: dict[str, str | int] = {
            "part": "snippet",
            "q": q,
            "type": "channel",
            "maxResults": 6,
            "key": api_key,
        }
        resp = await client.get(url, params=params)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("items", [])
            profiles: list[RawProfile] = []
            for item in items:
                snippet = item.get("snippet", {})
                channel_id = item.get("id", {}).get("channelId", "")
                title = snippet.get("title", "")
                description = snippet.get("description", "")
                norm_name = normalize_name(title)
                if channel_id and norm_name:
                    profiles.append(
                        RawProfile(
                            platform="YouTube",
                            name=norm_name,
                            profile_url=f"https://www.youtube.com/channel/{channel_id}",
                            location=None,
                            bio=description,
                            evidence=[title, description],
                            raw_metadata=item,
                        )
                    )
            return profiles
        return []
