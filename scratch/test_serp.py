import asyncio

import httpx

from app.config import get_settings


async def test():
    settings = get_settings()
    url = "https://serpapi.com/search.json"
    q = 'site:linkedin.com/in/ "Restaurant" owner Dubai'
    async with httpx.AsyncClient(timeout=20.0) as client:
        params = {
            "q": q,
            "api_key": settings.SERPAPI_API_KEY,
            "num": 10,
            "engine": "google",
        }
        resp = await client.get(url, params=params)
        data = resp.json()
        organic = data.get("organic_results", [])
        print(f"\nQUERY: {q} -> count: {len(organic)}")
        for idx, o in enumerate(organic):
            print(f"[{idx}] Title: {o.get('title')}")
            print(f"     Link: {o.get('link')}")
            print(f"     Snippet: {o.get('snippet')}")

asyncio.run(test())
