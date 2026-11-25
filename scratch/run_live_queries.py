"""Run all 7 live queries against the app and save results with timing, stage info, and call metrics."""

import asyncio
import json
import logging
import os
import sys
import time

# Configure logging to show INFO level (stage tracker output)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)

# Add app to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

import app.platforms.search_engine as search_engine  # noqa: E402
import app.services.llm_service as llm_service  # noqa: E402
from app.services.prospect_service import ProspectDiscoveryService  # noqa: E402

os.makedirs("results", exist_ok=True)

QUERIES = [
    ("Football lovers in Washington DC in USA", "results/query1.json"),
    ("Digital marketing agency owners in New York USA", "results/query2.json"),
    ("Startup founders in London UK", "results/query3.json"),
    ("Cricket lovers in Ahmedabad India", "results/query4.json"),
    ("Restaurant owners in Dubai UAE", "results/query5.json"),
    ("Yoga teachers in Berlin Germany", "results/query6.json"),
    ("Dentists in Toronto Canada", "results/query7.json"),
]

# Instrumentation for Serper and LLM calls
serper_calls_per_query: list[int] = []
llm_calls_per_query: list[int] = []

current_serper_calls = 0
current_llm_calls = 0

orig_search_serper = search_engine._search_serper
orig_parse_intent = llm_service.LLMService.parse_query_intent
orig_batch_classify = llm_service.LLMService.batch_classify_candidates


async def instrumented_search_serper(*args, **kwargs):
    global current_serper_calls
    current_serper_calls += 1
    return await orig_search_serper(*args, **kwargs)


async def instrumented_parse_intent(self, *args, **kwargs):
    global current_llm_calls
    current_llm_calls += 1
    return await orig_parse_intent(self, *args, **kwargs)


async def instrumented_batch_classify(self, *args, **kwargs):
    global current_llm_calls
    current_llm_calls += 1
    return await orig_batch_classify(self, *args, **kwargs)


search_engine._search_serper = instrumented_search_serper
llm_service.LLMService.parse_query_intent = instrumented_parse_intent
llm_service.LLMService.batch_classify_candidates = instrumented_batch_classify


async def main() -> None:
    global current_serper_calls, current_llm_calls

    summary_records = []

    for idx, (query, outfile) in enumerate(QUERIES, 1):
        print(f"\n{'='*70}")
        print(f"QUERY {idx}/7: {query}")
        print(f"{'='*70}")

        current_serper_calls = 0
        current_llm_calls = 0

        t0 = time.perf_counter()
        try:
            svc = ProspectDiscoveryService()
            response = await svc.discover_prospects(query)
            elapsed = time.perf_counter() - t0
            result = response.model_dump()
        except Exception as e:
            elapsed = time.perf_counter() - t0
            result = {
                "success": False,
                "query": query,
                "error": str(e),
                "total_prospects": 0,
                "prospects": [],
            }

        serper_calls_per_query.append(current_serper_calls)
        llm_calls_per_query.append(current_llm_calls)

        print(f"\n--- METRICS FOR QUERY {idx} ---")
        print(f"Response time: {elapsed:.2f}s")
        print(f"Prospects returned: {result.get('total_prospects', 0)}")
        print(f"Serper API calls: {current_serper_calls}")
        print(f"LLM API calls: {current_llm_calls}")

        # Show returned prospects and their location evidence
        print("\nReturned Prospects:")
        prospects = result.get("prospects", [])
        if not prospects:
            print("  (None returned)")
        else:
            for p in prospects:
                platforms = [f"{link['platform']}: {link['link']}" for link in p.get("social_link", [])]
                print(f"  * Name: {p.get('name')}")
                print(f"    Location: {p.get('location')}")
                print(f"    Links: {', '.join(platforms)}")

        with open(outfile, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=4)
        print(f"\nSaved to {outfile}")

        summary_records.append({
            "query": query,
            "elapsed": elapsed,
            "prospects": len(prospects),
            "serper_calls": current_serper_calls,
            "llm_calls": current_llm_calls,
        })

    print(f"\n{'='*70}")
    print("ALL 7 QUERIES COMPLETED")
    print(f"{'='*70}")
    avg_serper = sum(serper_calls_per_query) / len(serper_calls_per_query) if serper_calls_per_query else 0
    avg_llm = sum(llm_calls_per_query) / len(llm_calls_per_query) if llm_calls_per_query else 0

    print(f"Average Serper API calls per request: {avg_serper:.1f}")
    print(f"Average LLM API calls per request: {avg_llm:.1f}")
    for idx, r in enumerate(summary_records, 1):
        print(f"Q{idx}: {r['query'][:40]:<40} | Time: {r['elapsed']:5.2f}s | Returned: {r['prospects']:2d} | Serper: {r['serper_calls']} | LLM: {r['llm_calls']}")


if __name__ == "__main__":
    asyncio.run(main())
