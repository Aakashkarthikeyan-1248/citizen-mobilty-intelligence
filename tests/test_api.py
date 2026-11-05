"""
============================================================
CMI — Automated Test Suite: REST API & Endpoints
============================================================
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from config import get_settings
from engines.text_isolation import TextIsolationEngine
from engines.llm_router import LLMRouter, LLMProviderConfig, LLMResponse
from engines.route_harvester import RouteHarvester, GeoPoint
from api.routes import create_router


@pytest.fixture
def test_app(monkeypatch):
    """Creates a mock-backed FastAPI app instance for testing."""
    app = FastAPI(title="CMI-Test")
    text_engine = TextIsolationEngine()

    dummy_provider = LLMProviderConfig(
        name="mock-provider",
        base_url="https://mock.ai/v1",
        api_key="mock-key",
        model="mock-v1",
    )
    llm_router = LLMRouter([dummy_provider])

    # Mock the LLM generation call so no live network API is hit during unit tests
    async def mock_generate(*args, **kwargs):
        return LLMResponse(
            text="Mock AI corridor analysis: Normal transit conditions with moderate construction slowdowns.",
            provider_used="mock-provider",
            model_used="mock-v1",
            latency_ms=45.0,
        )

    monkeypatch.setattr(llm_router, "generate", mock_generate)

    route_harvester = RouteHarvester(
        llm_router=llm_router,
        text_engine=text_engine,
    )

    # Mock geocoder to avoid external network calls to OpenStreetMap/Nominatim during tests
    monkeypatch.setattr(
        route_harvester,
        "_geocode",
        lambda loc: GeoPoint(lat=13.0827, lng=80.2707) if "Central" in loc else GeoPoint(lat=12.9815, lng=80.1637),
    )

    # create_router already has prefix="/api"
    api_router = create_router(
        route_harvester=route_harvester,
        llm_router=llm_router,
    )
    app.include_router(api_router)
    return app


def test_health_check(test_app):
    client = TestClient(test_app)
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "cmi"


def test_providers_endpoint(test_app):
    client = TestClient(test_app)
    response = client.get("/api/providers")
    assert response.status_code == 200
    providers = response.json()
    assert isinstance(providers, list)
    assert len(providers) >= 1
    assert providers[0]["name"] == "mock-provider"


def test_route_analyze_endpoint(test_app):
    client = TestClient(test_app)
    payload = {
        "origin": "Chennai Central",
        "destination": "Chennai Airport",
    }
    response = client.post("/api/route/analyze", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert "summary" in data
    assert "Mock AI corridor analysis" in data["summary"]
    assert "alerts" in data
    assert isinstance(data["alerts"], list)
    assert len(data["alerts"]) > 0
    assert "bounding_box" in data
    assert "provider" in data
