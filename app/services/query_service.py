"""Query service orchestrating query validation, caching, and intent extraction."""

import hashlib
import logging

import httpx

from app.core.cache import cache
from app.core.exceptions import QueryValidationError
from app.schemas import ParsedQueryIntent
from app.services.llm_service import LLMService

logger = logging.getLogger("prospect.services.query")


class QueryService:
    """Service responsible for query preprocessing, caching, and intent parsing."""

    def __init__(self, llm_service: LLMService | None = None) -> None:
        self.llm_service = llm_service or LLMService()

    async def parse_and_understand_query(
        self, query: str, client: httpx.AsyncClient
    ) -> ParsedQueryIntent:
        """Validate input query and extract structured intent with TTL caching."""
        self.validate_raw_query(query)

        # Cache key based on normalized query
        cache_key = (
            f"query_intent:{hashlib.sha256(query.strip().lower().encode('utf-8')).hexdigest()}"
        )
        cached_intent = await cache.get(cache_key)
        if cached_intent:
            logger.debug(f"Cache hit for query: '{query}'")
            return ParsedQueryIntent.model_validate(cached_intent)

        # Call LLM service
        parsed_intent = await self.llm_service.parse_query_intent(query, client)

        # Cache valid intent
        await cache.set(cache_key, parsed_intent.model_dump())
        return parsed_intent

    def validate_raw_query(self, query: str) -> None:
        """Enforce strict validation against malicious or invalid queries."""
        cleaned = query.strip()
        if not cleaned:
            raise QueryValidationError("Query cannot be empty or purely whitespace.")

        if len(cleaned) < 3:
            raise QueryValidationError(
                "Query is too short. Please provide a descriptive prospect search query."
            )

        if len(cleaned) > 500:
            raise QueryValidationError("Query exceeds maximum allowed length of 500 characters.")

        # Reject pure non-word characters / punctuation
        if not any(c.isalnum() for c in cleaned):
            raise QueryValidationError(
                "Query contains no recognizable words or alphanumeric characters."
            )
