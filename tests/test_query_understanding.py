"""Tests for query understanding, Pydantic schemas, and injection defense."""

import pytest

from app.core.exceptions import QueryValidationError
from app.schemas import QueryRequest
from app.services.llm_service import LLMService
from app.services.query_service import QueryService


def test_query_request_validation() -> None:
    # Valid query
    req = QueryRequest(query="Football lovers in Washington DC in USA")
    assert req.query == "Football lovers in Washington DC in USA"

    # Empty query should fail
    with pytest.raises(ValueError):
        QueryRequest(query="   ")

    # Control characters should fail
    with pytest.raises(ValueError):
        QueryRequest(query="Malicious\x00Query")


def test_deterministic_intent_parser_cases() -> None:
    llm = LLMService()

    # Case 1: Football lovers in Washington DC in USA
    p1 = llm._deterministic_fallback_parser("Football lovers in Washington DC in USA")
    assert p1.interest == "Football"
    assert p1.location.city == "Washington DC"
    assert p1.location.country == "USA"

    # Case 2: Digital marketing agency owners in New York USA
    p2 = llm._deterministic_fallback_parser("Digital marketing agency owners in New York USA")
    assert p2.interest == "Digital Marketing"
    assert p2.location.city == "New York"
    assert p2.location.country == "USA"

    # Case 3: Startup founders in London UK
    p3 = llm._deterministic_fallback_parser("Startup founders in London UK")
    assert p3.interest == "Startup"
    assert p3.location.city == "London"
    assert p3.location.country == "UK"

    # Case 4: Cricket lovers in Ahmedabad India
    p4 = llm._deterministic_fallback_parser("Cricket lovers in Ahmedabad India")
    assert p4.interest == "Cricket"
    assert p4.location.city == "Ahmedabad"
    assert p4.location.country == "India"

    # Case 5: Restaurant owners in Dubai UAE
    p5 = llm._deterministic_fallback_parser("Restaurant owners in Dubai UAE")
    assert p5.interest == "Restaurant"
    assert p5.location.city == "Dubai"
    assert p5.location.country == "UAE"


def test_malformed_llm_output_parsing() -> None:
    llm = LLMService()

    # Markdown fenced json
    fenced = """```json
    {
      "interest": "Football",
      "audience": "Fans",
      "location": {"city": "Washington DC", "state": "DC", "country": "USA"},
      "target_type": "individual"
    }
    ```"""
    parsed = llm._validate_and_parse_json(fenced)
    assert parsed.interest == "Football"
    assert parsed.location.city == "Washington DC"


def test_query_service_validation_guards() -> None:
    qs = QueryService()

    # Too short
    with pytest.raises(QueryValidationError):
        qs.validate_raw_query("hi")

    # Gibberish / no alphanumeric
    with pytest.raises(QueryValidationError):
        qs.validate_raw_query("??? !!! ...")

    # Overly long
    with pytest.raises(QueryValidationError):
        qs.validate_raw_query("A" * 600)
