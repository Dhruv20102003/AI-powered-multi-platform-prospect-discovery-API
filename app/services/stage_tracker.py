"""Stage tracker for comprehensive per-request stage pipeline logging."""

import logging
from collections import defaultdict

logger = logging.getLogger("prospect.pipeline.stages")


class StageTracker:
    """Tracks candidates through each pipeline stage and logs drop reasons."""

    def __init__(self, query: str) -> None:
        self.query = query
        self.candidates_per_platform: dict[str, int] = defaultdict(int)
        self.url_gate_passed: int = 0
        self.url_gate_dropped: list[dict[str, str]] = []
        self.name_gate_passed: int = 0
        self.name_gate_dropped: list[dict[str, str]] = []
        self.relevance_gate_passed: int = 0
        self.relevance_gate_dropped: list[dict[str, str]] = []
        self.enrichment_lookups: int = 0
        self.enrichment_accepted: int = 0
        self.dedup_clusters: int = 0
        self.dedup_merged: int = 0
        self.final_returned: int = 0
        self.stages_cut: list[str] = []

    def record_raw_candidate(self, platform: str) -> None:
        self.candidates_per_platform[platform] += 1

    def record_url_pass(self) -> None:
        self.url_gate_passed += 1

    def record_url_drop(self, url: str, platform: str, reason: str) -> None:
        self.url_gate_dropped.append({"url": url, "platform": platform, "reason": reason})

    def record_name_pass(self) -> None:
        self.name_gate_passed += 1

    def record_name_drop(self, title: str, platform: str, reason: str) -> None:
        self.name_gate_dropped.append({"title": title, "platform": platform, "reason": reason})

    def record_relevance_pass(self) -> None:
        self.relevance_gate_passed += 1

    def record_relevance_drop(self, name: str, platform: str, reason: str) -> None:
        self.relevance_gate_dropped.append({"name": name, "platform": platform, "reason": reason})

    def log_summary(self) -> None:
        """Log complete stage pipeline report at INFO level without sensitive keys."""
        total_raw = sum(self.candidates_per_platform.values())
        logger.info("================ STAGE PIPELINE REPORT ================")
        logger.info("Query: '%s'", self.query)
        logger.info(
            "Stage 1 [Raw Candidates]: Total %d across platforms: %s",
            total_raw,
            dict(self.candidates_per_platform),
        )
        logger.info(
            "Stage 2 [URL Gate]: %d passed, %d dropped",
            self.url_gate_passed,
            len(self.url_gate_dropped),
        )
        for d in self.url_gate_dropped:
            logger.info("  - [URL Drop] %s (%s): %s", d["url"], d["platform"], d["reason"])

        logger.info(
            "Stage 3 [Name Extraction]: %d passed, %d dropped",
            self.name_gate_passed,
            len(self.name_gate_dropped),
        )
        for d in self.name_gate_dropped:
            logger.info("  - [Name Drop] '%s' (%s): %s", d["title"], d["platform"], d["reason"])

        logger.info(
            "Stage 4 [Relevance & Entity Gate]: %d passed, %d dropped",
            self.relevance_gate_passed,
            len(self.relevance_gate_dropped),
        )
        for d in self.relevance_gate_dropped:
            logger.info("  - [Relevance Drop] '%s' (%s): %s", d["name"], d["platform"], d["reason"])

        logger.info(
            "Stage 5 [Enrichment]: %d lookups executed, %d cross-links accepted",
            self.enrichment_lookups,
            self.enrichment_accepted,
        )
        logger.info(
            "Stage 6 [Deduplication & Consolidation]: %d clusters formed, %d profiles merged",
            self.dedup_clusters,
            self.dedup_merged,
        )
        if self.stages_cut:
            logger.warning("Deadline Exceeded: Cut stages: %s", ", ".join(self.stages_cut))
        logger.info("Stage 7 [Final Returned]: %d high-quality prospects", self.final_returned)
        logger.info("=======================================================")
