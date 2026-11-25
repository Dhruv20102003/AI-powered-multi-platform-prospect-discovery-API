"""Application settings loaded from environment variables using Pydantic Settings."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Production application settings with environment variable bindings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General API Configuration
    APP_NAME: str = "AI-Powered Multi-Platform Prospect Discovery API"
    APP_ENV: Literal["development", "production", "testing"] = "development"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 4000
    RATE_LIMIT_PER_MINUTE: int = 30
    API_KEY: str | None = None  # Optional API key for demo / public endpoint

    # LLM Settings
    LLM_PROVIDER: Literal["gemini", "openai", "anthropic"] = "gemini"
    LLM_MODEL: str = "gemini-2.0-flash"
    LLM_FALLBACK_MODEL: str = "gemini-1.5-flash-latest"
    GEMINI_API_KEY: str | None = None
    OPENAI_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None

    # Search & Discovery Settings
    # Optional override: forces a specific provider regardless of which keys are configured.
    # Leave unset (or blank) to auto-select: Serper → SerpAPI → Google CSE → DuckDuckGo.
    # Allowed values: serper, serpapi, google_cse, ddg
    SEARCH_ENGINE_PROVIDER: Literal["serper", "serpapi", "google_cse", "ddg"] | None = None
    SERPER_API_KEY: str | None = None
    GOOGLE_CSE_API_KEY: str | None = None
    GOOGLE_CSE_CX: str | None = None
    SERPAPI_API_KEY: str | None = None
    YOUTUBE_API_KEY: str | None = None
    TWITTER_BEARER_TOKEN: str | None = None

    # HTTP & Scraping Security Settings
    HTTP_TIMEOUT_SECONDS: float = Field(default=8.0, ge=1.0, le=30.0)
    MAX_CONCURRENT_PLATFORMS: int = Field(default=9, ge=1, le=20)
    CACHE_TTL_SECONDS: int = Field(default=3600, ge=60, le=86400)
    MAX_REQUEST_SIZE_BYTES: int = Field(default=65536, ge=1024, le=1048576)
    MAX_REDIRECTS: int = 3
    MAX_RESPONSE_BODY_BYTES: int = 1_048_576  # 1 MB max for fetched pages

    # Enrichment and Consolidation Budget Settings
    MAX_ENRICHMENT_PROSPECTS: int = Field(default=5, ge=1, le=10)
    MAX_SEARCH_CALLS_PER_PROSPECT: int = Field(default=3, ge=1, le=5)
    MAX_SEARCH_CALLS_PER_REQUEST: int = Field(default=10, ge=1, le=30)
    REQUEST_DEADLINE_SECONDS: float = Field(default=25.0, ge=5.0, le=60.0)
    MAX_PROSPECTS_RETURNED: int = Field(default=15, ge=1, le=100)

    @field_validator("SEARCH_ENGINE_PROVIDER", mode="before")
    @classmethod
    def _coerce_blank_provider(
        cls, v: object
    ) -> Literal["serper", "serpapi", "google_cse", "ddg"] | None:
        """Treat a blank / whitespace-only env var value as None (auto-detect mode)."""
        if isinstance(v, str) and not v.strip():
            return None
        return v  # type: ignore[return-value]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings instance."""
    return Settings()
