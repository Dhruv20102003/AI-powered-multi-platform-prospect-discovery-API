"""Diagnostic script for Q7: 'Dentists in Toronto Canada'."""

import asyncio
import json
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)
logging.getLogger("httpx").setLevel(logging.WARNING)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from app.core.security import create_safe_async_client  # noqa: E402
from app.platforms.search_engine import execute_web_search  # noqa: E402
from app.services.prospect_service import ProspectDiscoveryService  # noqa: E402


async def main() -> None:
    async with create_safe_async_client() as client:
        raw_items = await execute_web_search('site:linkedin.com/in/ "Dentists" Toronto', client, 10)
        print("=== RAW SERPER LINKEDIN HITS ===")
        for it in raw_items:
            print(f"  title={it.title!r} | link={it.link!r}")

    svc = ProspectDiscoveryService()
    response = await svc.discover_prospects("Dentists in Toronto Canada")
    print("\n=== Q7 RESPONSE ===")
    print(json.dumps(response.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
