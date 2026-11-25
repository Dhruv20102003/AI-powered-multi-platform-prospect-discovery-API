"""Link-liveness verification service using SSRF-safe HTTP client, concurrency limit, and TTL cache."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Literal

import httpx

from app.core.cache import InMemoryTTLCache
from app.core.cache import cache as default_cache
from app.schemas import ProspectRecord, SocialLink

logger = logging.getLogger("prospect.services.liveness")

LivenessState = Literal["alive", "unverified", "dead"]

_BOT_OR_AUTH_STATUSES = frozenset({401, 403, 429, 999})
_DEAD_STATUSES = frozenset({404, 410})
_AUTH_REDIRECT_MARKERS = ("/authwall", "/login", "/checkpoint", "/accounts/login")


@dataclass(frozen=True)
class LinkLivenessResult:
    """Outcome of a link-liveness check for a single URL."""

    url: str
    state: LivenessState
    http_status: int | None = None
    reason: str = ""

    @property
    def keep(self) -> bool:
        """Return True unless the URL is confirmed dead (404/410)."""
        return self.state != "dead"


class LinkLivenessChecker:
    """Verifies final candidate URLs concurrently with short timeouts and TTL caching."""

    def __init__(
        self,
        ttl_cache: InMemoryTTLCache | None = None,
        timeout_seconds: float = 3.0,
        max_concurrency: int = 6,
        cache_ttl_seconds: int = 3600,
    ) -> None:
        self.cache = ttl_cache or default_cache
        self.timeout_seconds = timeout_seconds
        self.max_concurrency = max_concurrency
        self.cache_ttl_seconds = cache_ttl_seconds

    async def check_url(
        self,
        url: str,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore | None = None,
    ) -> LinkLivenessResult:
        """Check liveness of a single URL, consulting TTL cache first."""
        if not url.startswith(("http://", "https://")):
            return LinkLivenessResult(
                url=url,
                state="alive",
                http_status=None,
                reason="Non-HTTP contact link (kept without probe)",
            )

        cache_key = f"liveness:{url}"
        cached: LinkLivenessResult | None = await self.cache.get(cache_key)
        if cached is not None:
            return cached

        sem = semaphore or asyncio.Semaphore(self.max_concurrency)
        async with sem:
            result = await self._probe_url(url, client)

        await self.cache.set(cache_key, result, ttl_seconds=self.cache_ttl_seconds)
        return result

    async def _probe_url(self, url: str, client: httpx.AsyncClient) -> LinkLivenessResult:
        """Perform a single SSRF-safe GET probe without bypassing bot/auth walls."""
        try:
            resp = await client.get(url, timeout=self.timeout_seconds)
            code = resp.status_code
            final_url = str(getattr(resp, "url", url)).lower()

            if code in _DEAD_STATUSES:
                logger.info("Dropping dead link %s (HTTP %d)", url, code)
                return LinkLivenessResult(
                    url=url,
                    state="dead",
                    http_status=code,
                    reason=f"HTTP {code} (not found/gone)",
                )

            if code in _BOT_OR_AUTH_STATUSES:
                logger.info(
                    "Keeping link %s as unverified (HTTP %d bot-block/login-wall)", url, code
                )
                return LinkLivenessResult(
                    url=url,
                    state="unverified",
                    http_status=code,
                    reason=f"HTTP {code} (bot-block/login-wall)",
                )

            if code == 200:
                if any(marker in final_url for marker in _AUTH_REDIRECT_MARKERS):
                    logger.info(
                        "Keeping link %s as unverified (redirected to login wall)", url
                    )
                    return LinkLivenessResult(
                        url=url,
                        state="unverified",
                        http_status=200,
                        reason="Redirected to login/auth wall",
                    )
                logger.info("Verified live link %s (HTTP 200)", url)
                return LinkLivenessResult(
                    url=url,
                    state="alive",
                    http_status=200,
                    reason="HTTP 200 OK",
                )

            logger.info("Keeping link %s as unverified (HTTP %d)", url, code)
            return LinkLivenessResult(
                url=url,
                state="unverified",
                http_status=code,
                reason=f"HTTP {code}",
            )

        except (httpx.TimeoutException, TimeoutError) as e:
            logger.info("Keeping link %s as unverified (timeout: %s)", url, type(e).__name__)
            return LinkLivenessResult(
                url=url,
                state="unverified",
                http_status=None,
                reason=f"Timeout ({type(e).__name__})",
            )
        except Exception as e:
            logger.info(
                "Keeping link %s as unverified (network error: %s)", url, type(e).__name__
            )
            return LinkLivenessResult(
                url=url,
                state="unverified",
                http_status=None,
                reason=f"Network error ({type(e).__name__})",
            )

    async def filter_prospects(
        self,
        prospects: list[ProspectRecord],
        client: httpx.AsyncClient,
        deadline_seconds: float | None = None,
    ) -> tuple[list[ProspectRecord], dict[str, LinkLivenessResult]]:
        """Verify final candidate links concurrently, dropping dead links and empty prospects."""
        if not prospects:
            return [], {}

        unique_urls: list[str] = []
        seen: set[str] = set()
        for p in prospects:
            for s in p.social_link:
                if s.link not in seen:
                    seen.add(s.link)
                    unique_urls.append(s.link)

        semaphore = asyncio.Semaphore(self.max_concurrency)
        results_by_url: dict[str, LinkLivenessResult] = {}

        async def _run_one(u: str) -> None:
            results_by_url[u] = await self.check_url(u, client, semaphore=semaphore)

        tasks = [asyncio.create_task(_run_one(u)) for u in unique_urls]
        try:
            if deadline_seconds is not None and deadline_seconds > 0:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=deadline_seconds,
                )
            else:
                await asyncio.gather(*tasks, return_exceptions=True)
        except TimeoutError:
            logger.warning(
                "Link-liveness check hit deadline (%.1fs); keeping remaining links as unverified.",
                deadline_seconds or 0.0,
            )
            for t in tasks:
                if not t.done():
                    t.cancel()

        filtered: list[ProspectRecord] = []
        for prospect in prospects:
            kept_links: list[SocialLink] = []
            for s in prospect.social_link:
                res = results_by_url.get(s.link)
                if res is None:
                    res = LinkLivenessResult(
                        url=s.link,
                        state="unverified",
                        http_status=None,
                        reason="Deadline reached before check",
                    )
                    results_by_url[s.link] = res

                if res.keep:
                    kept_links.append(s)

            if not kept_links:
                logger.info(
                    "Dropping prospect '%s': all links failed liveness check",
                    prospect.name,
                )
                continue

            filtered.append(
                ProspectRecord(
                    name=prospect.name,
                    location=prospect.location,
                    social_link=kept_links,
                )
            )

        return filtered, results_by_url
