import asyncio

import httpx

from app.config import get_settings
from app.platforms.linkedin import LinkedInPlatformProvider
from app.platforms.search_engine import execute_web_search
from app.schemas import LocationInfo, ParsedQueryIntent
from app.utils.normalizers import normalize_name
from app.utils.validators import derive_platform_from_url


async def main():
    settings = get_settings()
    print("Serper key present:", bool(settings.SERPER_API_KEY))
    print("SerpAPI key present:", bool(settings.SERPAPI_API_KEY))

    intent = ParsedQueryIntent(
        interest="Restaurant",
        audience="Restaurant owners",
        location=LocationInfo(city="Dubai", country="UAE"),
        target_type="individual",
    )

    provider = LinkedInPlatformProvider()
    query1 = provider.build_search_query(intent)
    print("LinkedIn search query generated:", repr(query1))

    async with httpx.AsyncClient() as client:
        items1 = await execute_web_search(query1, client, num_results=10)
        print(f"Items returned for query1 ({len(items1)}):")
        for idx, item in enumerate(items1):
            print(f"[{idx}] Title: {item.title}")
            print(f"    Link: {item.link}")
            print(f"    Snippet: {item.snippet}")
            derived = derive_platform_from_url(item.link, target_type=intent.target_type, title=item.title, snippet=item.snippet)
            print(f"    Derived URL: {derived}")
            norm_name = normalize_name(item.title)
            print(f"    Norm name: {repr(norm_name)}")

asyncio.run(main())
