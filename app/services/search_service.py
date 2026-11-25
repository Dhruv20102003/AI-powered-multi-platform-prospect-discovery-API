"""Concurrent search service executing discovery across all registered platforms."""

import asyncio
import logging
from typing import Any

import httpx

from app.platforms.base import PlatformProvider, RawProfile
from app.platforms.registry import get_default_platform_providers
from app.platforms.search_engine import SearchProvidersUnavailable
from app.schemas import ParsedQueryIntent

logger = logging.getLogger("prospect.services.search")


class SearchService:
    """Orchestrates concurrent multi-platform prospect discovery with failure isolation."""

    def __init__(self, providers: list[PlatformProvider] | None = None) -> None:
        self.providers = providers or get_default_platform_providers()

    async def discover_across_platforms(
        self,
        parsed_intent: ParsedQueryIntent,
        client: httpx.AsyncClient,
        tracker: Any | None = None,
    ) -> tuple[list[RawProfile], list[str]]:
        """Run search across all platforms concurrently using asyncio.gather.

        Returns:
            Tuple of (all_discovered_profiles, list_of_failed_or_skipped_platforms)

        Raises:
            SearchProvidersUnavailable: when nothing was found and the search
                providers were unavailable (an outage, not an empty result).
        """
        tasks = [
            self._safe_platform_search(provider, parsed_intent, client, tracker)
            for provider in self.providers
        ]

        # Concurrently execute all platform searches with return_exceptions=True
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_profiles: list[RawProfile] = []
        failed_or_skipped: list[str] = []
        search_unavailable = 0

        for provider, res in zip(self.providers, results, strict=False):
            if isinstance(res, SearchProvidersUnavailable):
                search_unavailable += 1
                failed_or_skipped.append(f"{provider.platform_name}: search unavailable")
            elif isinstance(res, Exception):
                logger.error(
                    f"Platform {provider.platform_name} discovery raised unexpected error: {res}",
                    exc_info=True,
                )
                failed_or_skipped.append(f"{provider.platform_name}: {str(res)}")
            elif isinstance(res, list):
                all_profiles.extend(res)
            else:
                logger.warning(f"Platform {provider.platform_name} returned non-list result: {res}")
                failed_or_skipped.append(f"{provider.platform_name}: Invalid result type")

        logger.info(
            f"Multi-platform discovery completed: {len(all_profiles)} raw profiles found. "
            f"{len(failed_or_skipped)} platforms reported issues."
        )
        # Nothing found AND search was broken: this is an outage, not an empty result.
        if not all_profiles and search_unavailable:
            raise SearchProvidersUnavailable("search providers are unavailable")
        return all_profiles, failed_or_skipped

    async def _safe_platform_search(
        self,
        provider: PlatformProvider,
        parsed_intent: ParsedQueryIntent,
        client: httpx.AsyncClient,
        tracker: Any | None = None,
    ) -> list[RawProfile]:
        """Wrap individual platform search in try-except block to ensure failure isolation."""
        try:
            logger.debug(f"Starting discovery for platform: {provider.platform_name}")
            return await provider.search(parsed_intent, client, tracker=tracker)  # type: ignore[call-arg]
        except SearchProvidersUnavailable:
            # Let the outage propagate so the caller can tell it apart from "no results".
            raise
        except TypeError:
            return await provider.search(parsed_intent, client)
        except Exception as e:
            # Log only the error type: some HTTP errors embed URLs that can contain API keys.
            logger.warning(
                f"Discovery error on {provider.platform_name}: {type(e).__name__}"
            )
            return []
