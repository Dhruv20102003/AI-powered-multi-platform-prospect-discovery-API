"""Abstract base interface and data structures for all platform discovery providers."""

from abc import ABC, abstractmethod
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.schemas import ParsedQueryIntent, PlatformName


class RawProfile(BaseModel):
    """Raw profile record discovered from a platform or search source before consolidation."""

    platform: PlatformName = Field(
        ..., description="The platform where this profile was discovered"
    )
    name: str = Field(..., min_length=1, description="Raw or extracted name")
    profile_url: str = Field(..., description="Direct profile or portfolio URL")
    location: str | None = Field(default=None, description="Discovered location string")
    bio: str | None = Field(default=None, description="Bio, snippet, or headline text")
    email: str | None = Field(default=None, description="Discovered contact email")
    phone: str | None = Field(default=None, description="Discovered contact phone number")
    whatsapp_link: str | None = Field(default=None, description="Discovered wa.me link")
    evidence: list[str] = Field(
        default_factory=list,
        description="Text fragments proving relevance to interest and location",
    )
    raw_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Underlying raw response metadata for auditing",
    )

    model_config = ConfigDict(extra="ignore")


class PlatformProvider(ABC):
    """Abstract interface for all prospect discovery platform providers."""

    @property
    @abstractmethod
    def platform_name(self) -> PlatformName:
        """The formal platform name as specified in the README."""
        pass

    @property
    @abstractmethod
    def is_search_source_only(self) -> bool:
        """True if the platform is strictly a discovery engine rather than a final social identity."""
        pass

    @abstractmethod
    def build_search_query(self, parsed_intent: ParsedQueryIntent) -> str:
        """Build a platform-optimized search dork or query string."""
        pass

    @abstractmethod
    async def search(
        self,
        parsed_intent: ParsedQueryIntent,
        client: httpx.AsyncClient,
    ) -> list[RawProfile]:
        """Execute discovery across the platform and return raw profile candidates."""
        pass
