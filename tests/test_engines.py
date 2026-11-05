"""
============================================================
CMI — Automated Test Suite: Engines & Core Architecture
============================================================
"""

from __future__ import annotations

import asyncio
import pytest
from shapely.geometry import Point, Polygon

from engines.text_isolation import TextIsolationEngine, MediaType, IsolationResult
from engines.llm_router import (
    LLMRouter,
    LLMProviderConfig,
    LLMResponse,
    LLMProviderError,
    LLMAllProvidersExhausted,
)
from engines.route_harvester import (
    RouteHarvester,
    GeoPoint,
    RouteSegment,
    CorridorAlert,
    CorridorSummary,
)


# ═════════════════════════════════════════════════════════════════
# 1. TEXT ISOLATION ENGINE TESTS
# ═════════════════════════════════════════════════════════════════

class TestTextIsolationEngine:
    """Validates that incoming HTML/Media is cleanly isolated into words only."""

    @pytest.fixture
    def engine(self):
        return TextIsolationEngine()

    def test_strip_html_basic(self, engine):
        html_input = "<p>Traffic jam on <b>Mount Road</b> near LIC.</p>"
        result = engine.isolate(html_input, MediaType.HTML)
        assert isinstance(result, IsolationResult)
        assert result.text == "Traffic jam on Mount Road near LIC."
        assert "<" not in result.text
        assert ">" not in result.text

    def test_strip_html_removes_scripts_and_styles(self, engine):
        html_input = """
        <html>
            <head>
                <style>body { color: red; }</style>
                <script>alert('xss');</script>
            </head>
            <body>
                <h1>Road Advisory</h1>
                <p>Bridge closed for maintenance.</p>
                <noscript>JavaScript required</noscript>
            </body>
        </html>
        """
        result = engine.isolate(html_input, MediaType.HTML)
        assert "Road Advisory" in result.text
        assert "Bridge closed for maintenance." in result.text
        assert "color: red" not in result.text
        assert "alert('xss')" not in result.text
        assert "JavaScript required" not in result.text

    def test_strip_html_handles_bytes(self, engine):
        raw_bytes = b"<div>Emergency vehicle on <b>ECR</b>.</div>"
        result = engine.isolate(raw_bytes, MediaType.HTML)
        assert "Emergency vehicle on ECR." in result.text
        assert any("bytes" in w for w in result.warnings)

    def test_control_character_sanitization(self, engine):
        dirty_text = "Speed\x00limit\x07enforced\x1b[31mon highway."
        result = engine.isolate(dirty_text, MediaType.PLAINTEXT)
        assert "\x00" not in result.text
        assert "\x07" not in result.text
        assert "Speedlimit" in result.text or "Speed" in result.text

    def test_media_stubs_return_plaintext(self, engine):
        # Image OCR Stub
        ocr_result = engine.isolate("speed_camera_snap.png", MediaType.IMAGE)
        assert isinstance(ocr_result.text, str)
        assert len(ocr_result.text) > 0
        assert "speed_camera_snap.png" in ocr_result.text

        # Audio STT Stub
        stt_result = engine.isolate("traffic_radio.mp3", MediaType.AUDIO)
        assert isinstance(stt_result.text, str)
        assert "Velachery" in stt_result.text

        # Video Transcription Stub
        video_result = engine.isolate("corridor_drone_feed.mp4", MediaType.VIDEO)
        assert isinstance(video_result.text, str)
        assert "00:00" in video_result.text
        assert "Metro construction" in video_result.text


# ═════════════════════════════════════════════════════════════════
# 2. MULTI-API LLM ROUTER TESTS
# ═════════════════════════════════════════════════════════════════

class TestLLMRouter:
    """Validates multi-tier sequential fallback logic."""

    def test_empty_prompt_raises_value_error(self):
        router = LLMRouter([])
        with pytest.raises(ValueError):
            asyncio.run(router.generate(""))

    def test_sequential_fallback_from_model_a_to_b(self, monkeypatch):
        """Simulates Model A failing, verifying seamless cascade to Model B."""
        provider_a = LLMProviderConfig(
            name="openai",
            base_url="https://api.openai.com/v1",
            api_key="mock_key_a",
            model="gpt-4o-mini",
        )
        provider_b = LLMProviderConfig(
            name="nvidia",
            base_url="https://integrate.api.nvidia.com/v1",
            api_key="mock_key_b",
            model="meta/llama-3.1-70b-instruct",
        )

        router = LLMRouter([provider_a, provider_b])

        async def mock_call(provider, system, prompt, temperature, max_tokens):
            if provider.name == "openai":
                # Simulate 429 Quota Exceeded or Timeout
                raise LLMProviderError("openai", "HTTP 429 Rate Limit Exceeded")
            elif provider.name == "nvidia":
                return LLMResponse(
                    text="NVIDIA Model B analyzed traffic successfully.",
                    provider_used="nvidia",
                    model_used=provider.model,
                    latency_ms=120.0,
                )
            raise LLMProviderError(provider.name, "Unexpected provider")

        monkeypatch.setattr(router, "_call_openai_compat", mock_call)

        response = asyncio.run(router.generate("Summarize Chennai traffic conditions."))
        assert response.text == "NVIDIA Model B analyzed traffic successfully."
        assert response.provider_used == "nvidia"
        assert response.fallbacks == ["openai"]

    def test_all_providers_exhausted(self, monkeypatch):
        provider_a = LLMProviderConfig(
            name="nvidia",
            base_url="https://integrate.api.nvidia.com/v1",
            api_key="mock_key_a",
            model="meta/llama-3.1-70b-instruct",
        )

        router = LLMRouter([provider_a])

        async def mock_fail(*args, **kwargs):
            raise LLMProviderError("nvidia", "Service Unavailable 503")

        monkeypatch.setattr(router, "_call_openai_compat", mock_fail)

        with pytest.raises(LLMAllProvidersExhausted) as exc_info:
            asyncio.run(router.generate("Test query"))
        assert "nvidia" in exc_info.value.attempted


# ═════════════════════════════════════════════════════════════════
# 3. ROUTE HARVESTER & GEOGRAPHICAL ENGINE TESTS
# ═════════════════════════════════════════════════════════════════

class TestRouteHarvester:
    """Validates geospatial corridor expansion, alert filtering, and summary."""

    @pytest.fixture
    def harvester(self):
        text_engine = TextIsolationEngine()
        llm_router = LLMRouter([])
        return RouteHarvester(llm_router=llm_router, text_engine=text_engine)

    def test_haversine_distance_calculation(self, harvester):
        # Chennai Central (13.0827, 80.2707) to Chennai Airport (12.9815, 80.1637)
        p1 = GeoPoint(lat=13.0827, lng=80.2707)
        p2 = GeoPoint(lat=12.9815, lng=80.1637)
        dist = harvester._haversine(p1, p2)
        assert 14.0 < dist < 20.0  # Approx 16-18 km direct distance

    def test_corridor_polygon_and_bounding_box(self, harvester):
        origin = GeoPoint(lat=13.0827, lng=80.2707)
        dest = GeoPoint(lat=12.9815, lng=80.1637)
        route = harvester._fetch_route(origin, dest)

        corridor_poly, bbox = harvester._build_corridor(route)

        assert isinstance(corridor_poly, Polygon)
        assert corridor_poly.is_valid
        assert not corridor_poly.is_empty
        assert "north" in bbox and "south" in bbox and "east" in bbox and "west" in bbox
        assert bbox["north"] > bbox["south"]
        assert bbox["east"] > bbox["west"]

    def test_corridor_alert_filtering(self, harvester):
        origin = GeoPoint(lat=13.0827, lng=80.2707)
        dest = GeoPoint(lat=12.9815, lng=80.1637)
        route = harvester._fetch_route(origin, dest)
        corridor_poly, _ = harvester._build_corridor(route)

        alerts = harvester._harvest_corridor_alerts(corridor_poly, "Central", "Airport")
        assert len(alerts) > 0
        for alert in alerts:
            assert hasattr(alert, "lat")
            assert hasattr(alert, "lng")
            assert hasattr(alert, "severity")
            assert hasattr(alert, "marker_color")

    def test_corridor_summary_json_contract(self):
        alert = CorridorAlert(
            headline="Flooding on Anna Salai",
            description="Water level 1.5 ft",
            lat=13.0500,
            lng=80.2500,
            severity="critical",
            category="hazard",
            marker_color="darkred",
        )
        summary = CorridorSummary(
            summary_text="Severe waterlogging observed along Anna Salai.",
            alerts=[alert],
            bounding_box={"south": 12.9, "north": 13.1, "west": 80.1, "east": 80.3},
            provider_used="groq",
        )
        json_output = summary.to_json()
        assert "Severe waterlogging" in json_output
        assert "darkred" in json_output
        assert "Anna Salai" in json_output
