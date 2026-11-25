"""Pydantic schemas for request, response, query parsing, and prospects."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Exact platform names as defined in the README specification
PlatformName = Literal[
    "Google",
    "LinkedIn",
    "X",
    "YouTube",
    "Instagram",
    "WhatsApp",
    "Facebook",
    "Email",
    "Threads",
]


class QueryRequest(BaseModel):
    """Incoming prospect discovery request schema."""

    query: str = Field(
        ...,
        min_length=2,
        max_length=500,
        description="Natural language search query for prospect discovery",
        examples=["Football lovers in Washington DC in USA"],
    )

    @field_validator("query")
    @classmethod
    def validate_query_text(cls, v: str) -> str:
        """Validate query text is non-empty, stripped, and not pure punctuation or gibberish."""
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Query string cannot be empty or purely whitespace.")
        # Reject control characters
        if any(ord(c) < 32 and c not in ("\n", "\t", "\r") for c in cleaned):
            raise ValueError("Query contains invalid control characters.")
        return cleaned


class LocationInfo(BaseModel):
    """Geographical location parameters extracted from the user query."""

    city: str | None = Field(default=None, description="Target city if specified")
    state: str | None = Field(default=None, description="Target state or region if specified")
    country: str | None = Field(default=None, description="Target country if specified")

    model_config = ConfigDict(extra="ignore")

    def to_location_string(self) -> str | None:
        """Format location as a clean comma-separated string."""
        parts = [p for p in (self.city, self.state, self.country) if p and p.strip()]
        return ", ".join(parts) if parts else None


class ParsedQueryIntent(BaseModel):
    """Structured understanding of search intent extracted via LLM."""

    interest: str = Field(..., description="Primary interest, profession, or industry")
    audience: str = Field(..., description="Target audience group or title")
    location: LocationInfo = Field(
        default_factory=LocationInfo,
        description="Structured geographical parameters",
    )
    target_type: Literal["individual", "business", "community", "any"] = Field(
        default="individual",
        description="Entity type to discover",
    )

    model_config = ConfigDict(extra="ignore")


class SocialLink(BaseModel):
    """Social profile or contact link associated with a prospect."""

    platform: PlatformName = Field(
        ...,
        description="Platform name matching the specification",
    )
    link: str = Field(
        ...,
        description="Verified public profile URL or formatted contact link",
    )

    model_config = ConfigDict(extra="forbid")


class ProspectRecord(BaseModel):
    """Discovered, validated, and consolidated prospect record."""

    name: str = Field(..., min_length=1, description="Prospect's full or business name")
    location: str | None = Field(
        default=None,
        description="Geographical location formatted as 'City, State, Country' when available",
    )
    social_link: list[SocialLink] = Field(
        default_factory=list,
        description="Array of verified public social and contact links",
    )

    model_config = ConfigDict(extra="forbid")


class ProspectResponse(BaseModel):
    """Exact API response schema specified in the README."""

    success: bool = Field(True, description="Indicates whether the request succeeded")
    query: str = Field(..., description="The original natural language query")
    total_prospects: int = Field(..., ge=0, description="Count of discovered matching prospects")
    prospects: list[ProspectRecord] = Field(
        default_factory=list,
        description="List of consolidated matching prospects",
    )

    model_config = ConfigDict(extra="forbid")


class CandidateClassification(BaseModel):
    """Classification result for a single prospect candidate from LLM or deterministic engine."""

    id: str = Field(..., description="Unique identifier matching input candidate id")
    entity_type: str = Field(
        ...,
        description="person | business | organization | article | directory | association | union | insurer | other",
    )
    name: str = Field(..., description="Cleaned entity or person name")
    location_text: str | None = Field(
        default=None,
        description="Location string found in candidate text, or None",
    )
    location_matches_query: bool = Field(
        ...,
        description="True ONLY if text explicitly matches target query location",
    )
    topic_matches: bool = Field(
        ...,
        description="True ONLY if text matches query interest/profession",
    )
    role_evidence: str | None = Field(
        default=None,
        description="Exact role keyword found in text or None",
    )
    keep: bool = Field(
        ...,
        description="True if candidate passes all criteria, false otherwise",
    )
    reason: str = Field(..., description="Justification for keeping or dropping")

    model_config = ConfigDict(extra="ignore")


class BatchClassificationResponse(BaseModel):
    """Batched candidate classification output schema."""

    classifications: list[CandidateClassification] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")
