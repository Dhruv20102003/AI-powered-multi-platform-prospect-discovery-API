"""Diagnostic script for Q6: trace targeted enrichment and deduplication merges."""

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

import app.services.prospect_service as prospect_service_mod  # noqa: E402
from app.services.prospect_service import ProspectDiscoveryService  # noqa: E402

orig_exec = prospect_service_mod.execute_web_search


async def logging_exec(query: str, client, num_results: int = 10):
    items = await orig_exec(query, client, num_results)
    print(f"  [TARGETED SEARCH] query={query!r}")
    for it in items:
        print(f"    -> title={it.title!r} | link={it.link!r} | snippet={it.snippet[:120]!r}")
    return items


prospect_service_mod.execute_web_search = logging_exec


async def main() -> None:
    svc = ProspectDiscoveryService()
    response = await svc.discover_prospects("Yoga teachers in Berlin Germany")
    print("\n=== Q6 RESPONSE ===")
    print(json.dumps(response.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
