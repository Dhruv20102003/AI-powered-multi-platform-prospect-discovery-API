"""WhatsApp contact discovery provider extracting wa.me links."""

import logging

import httpx

from app.platforms.base import PlatformProvider, RawProfile
from app.schemas import ParsedQueryIntent, PlatformName

logger = logging.getLogger("prospect.platforms.whatsapp")


class WhatsAppPlatformProvider(PlatformProvider):
    """WhatsApp contact link discovery provider.

    Per Fix 7: Never create a prospect from a standalone wa.me link or classified ad.
    WhatsApp links are strictly extracted as contact links attached to already-kept prospects.
    """

    @property
    def platform_name(self) -> PlatformName:
        return "WhatsApp"

    @property
    def is_search_source_only(self) -> bool:
        return True

    def build_search_query(self, parsed_intent: ParsedQueryIntent) -> str:
        return ""

    async def search(
        self,
        parsed_intent: ParsedQueryIntent,
        client: httpx.AsyncClient,
    ) -> list[RawProfile]:
        # Do not create standalone prospects from wa.me links
        return []
