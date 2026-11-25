"""Diagnostic script to run 'Restaurant owners in Dubai UAE' and print full stage report & drop reasons."""

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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

import app.platforms.search_engine as search_engine  # noqa: E402
from app.services.prospect_service import ProspectDiscoveryService  # noqa: E402

serper_calls = 0
orig_serper = search_engine._search_serper


async def counting_serper(*args, **kwargs):
    global serper_calls
    serper_calls += 1
    return await orig_serper(*args, **kwargs)


search_engine._search_serper = counting_serper


async def main() -> None:
    svc = ProspectDiscoveryService()
    query = "Restaurant owners in Dubai UAE"
    response = await svc.discover_prospects(query)
    print("\n=== FINAL RESPONSE ===")
    print(json.dumps(response.model_dump(), ensure_ascii=False, indent=2))
    print(f"\nSerper API calls used: {serper_calls}")


if __name__ == "__main__":
    asyncio.run(main())
