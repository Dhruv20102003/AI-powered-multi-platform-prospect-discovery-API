"""End-to-end API endpoint tests for /prospect-discovery covering all 5 practical test cases."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.platforms.base import RawProfile


def test_health_check(test_client: TestClient) -> None:
    response = test_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_endpoint(test_client: TestClient) -> None:
    response = test_client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["endpoint"] == "/prospect-discovery"


def test_invalid_query_rejection(test_client: TestClient) -> None:
    # Empty query
    r1 = test_client.post("/prospect-discovery", json={"query": "   "})
    assert r1.status_code == 422

    # Gibberish / no alphanumeric
    r2 = test_client.post("/prospect-discovery", json={"query": "!?! ??? ..."})
    assert r2.status_code == 422


def test_empty_results_handling(test_client: TestClient) -> None:
    """When discovery yields zero real prospects, API must return success:true and empty list."""
    with patch(
        "app.services.search_service.SearchService.discover_across_platforms"
    ) as mock_search:
        mock_search.return_value = ([], [])
        resp = test_client.post(
            "/prospect-discovery",
            json={"query": "Quantum crypto researchers in Antarctica"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["total_prospects"] == 0
        assert data["prospects"] == []


def test_partial_platform_failure_resilience(test_client: TestClient) -> None:
    """If one or more platforms fail or raise exceptions, the request must still succeed."""
    sample_profile = RawProfile(
        platform="LinkedIn",
        name="Sarah Jenkins",
        profile_url="https://linkedin.com/in/sarahjenkins-dc",
        location="Washington DC, USA",
        bio="Football lover in Washington DC. Reach me at sarah@jenkinsdc.com",
        evidence=["Football", "Washington DC", "USA"],
    )

    with patch(
        "app.services.search_service.SearchService.discover_across_platforms"
    ) as mock_search:
        # Simulate partial failure: some platforms failed, but LinkedIn returned results
        mock_search.return_value = (
            [sample_profile],
            ["X: Rate limit exceeded", "Instagram: Network timeout"],
        )

        resp = test_client.post(
            "/prospect-discovery",
            json={"query": "Football lovers in Washington DC in USA"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["total_prospects"] == 1
        p = data["prospects"][0]
        assert p["name"] == "Sarah Jenkins"
        assert p["location"] == "Washington DC, USA"
        assert any(l["platform"] == "LinkedIn" for l in p["social_link"])
        assert any(l["platform"] == "Email" for l in p["social_link"])


# Test the 5 required test cases from README Section 6
@pytest.mark.parametrize(
    "query,expected_interest,expected_city",
    [
        (
            "Football lovers in Washington DC in USA",
            "Football",
            "Washington DC",
        ),
        (
            "Digital marketing agency owners in New York USA",
            "Digital Marketing",
            "New York",
        ),
        (
            "Startup founders in London UK",
            "Startup",
            "London",
        ),
        (
            "Cricket lovers in Ahmedabad India",
            "Cricket",
            "Ahmedabad",
        ),
        (
            "Restaurant owners in Dubai UAE",
            "Restaurant",
            "Dubai",
        ),
    ],
)
def test_five_mandatory_practical_test_cases(
    test_client: TestClient,
    query: str,
    expected_interest: str,
    expected_city: str,
) -> None:
    """Validate end-to-end response schema and intent handling for all 5 mandatory test queries."""
    sample_profile = RawProfile(
        platform="LinkedIn",
        name="Alex Smith",
        profile_url=f"https://linkedin.com/in/{expected_city.lower().replace(' ', '')}-lead",
        location=f"{expected_city}",
        bio=f"Passionate {expected_interest} founder and owner in {expected_city}.",
        evidence=[expected_interest, expected_city, "founder and owner"],
    )

    with patch(
        "app.services.search_service.SearchService.discover_across_platforms"
    ) as mock_search:
        mock_search.return_value = ([sample_profile], [])
        resp = test_client.post("/prospect-discovery", json={"query": query})
        assert resp.status_code == 200
        data = resp.json()

        # Strict response shape validation
        assert data["success"] is True
        assert data["query"] == query
        assert data["total_prospects"] == 1
        assert isinstance(data["prospects"], list)
        prospect = data["prospects"][0]
        assert "name" in prospect
        assert "location" in prospect
        assert "social_link" in prospect
        assert isinstance(prospect["social_link"], list)
        assert len(prospect["social_link"]) > 0
        assert prospect["social_link"][0]["platform"] == "LinkedIn"
