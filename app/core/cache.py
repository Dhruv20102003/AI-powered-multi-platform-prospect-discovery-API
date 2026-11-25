"""In-memory asynchronous TTL cache to avoid redundant LLM and search calls."""

import asyncio
import time
from typing import Any


class InMemoryTTLCache:
    """Thread-safe, asynchronous in-memory cache with Time-To-Live (TTL) expiration."""

    def __init__(self, default_ttl_seconds: int = 3600) -> None:
        self.default_ttl = default_ttl_seconds
        self._cache: dict[str, tuple[Any, float]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        """Retrieve a cached value if present and not expired."""
        async with self._lock:
            record = self._cache.get(key)
            if record is None:
                return None
            value, expires_at = record
            if time.time() > expires_at:
                del self._cache[key]
                return None
            return value

    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        """Store a value with an expiration timestamp."""
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
        expires_at = time.time() + ttl
        async with self._lock:
            self._cache[key] = (value, expires_at)

    async def clear(self) -> None:
        """Purge all entries from the cache."""
        async with self._lock:
            self._cache.clear()

    async def prune(self) -> None:
        """Remove expired entries."""
        now = time.time()
        async with self._lock:
            expired_keys = [k for k, (_, exp) in self._cache.items() if now > exp]
            for k in expired_keys:
                del self._cache[k]


# Global cache instance for query understanding & search results
cache = InMemoryTTLCache()
