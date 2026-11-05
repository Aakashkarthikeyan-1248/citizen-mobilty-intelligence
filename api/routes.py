"""
============================================================
CMI — API Route Definitions
============================================================
"""

from __future__ import annotations

import logging
from typing import Optional, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("cmi.api.routes")


# ── Request / Response Schemas ─────────────────────────────────

class RouteAnalysisRequest(BaseModel):
    """Schema for the route analysis endpoint."""
    origin: str = Field(
        ...,
        min_length=2,
        max_length=200,
        description="Human-readable start location (e.g., 'Chennai Central')",
        examples=["Chennai Central"],
    )
    destination: str = Field(
        ...,
        min_length=2,
        max_length=200,
        description="Human-readable end location (e.g., 'Chennai Airport')",
        examples=["Chennai Airport"],
    )
    mode: str = Field(
        default="driving",
        description="Travel mode: driving | transit | walking",
    )


class AlertMarker(BaseModel):
    """A single geo-tagged alert for the frontend map."""
    headline: str
    description: str
    lat: float
    lng: float
    severity: str
    category: str
    source: str
    marker_color: str


class RouteAnalysisResponse(BaseModel):
    """Structured response from the route analysis pipeline."""
    summary: str = Field(description="AI-generated corridor summary text")
    provider: str = Field(description="Which LLM provider served the summary")
    bounding_box: dict = Field(description="Geographic bounding box of the corridor")
    alerts: list[AlertMarker] = Field(description="Geo-tagged alert markers")
    route_geometry: list[list[float]] = Field(
        default_factory=list,
        description="Array of [lat, lng] coordinates following real roads for Google Maps polylines",
    )
    distance_km: float = Field(default=0.0, description="Driving route distance in kilometers")
    duration_min: float = Field(default=0.0, description="Estimated transit duration in minutes")
    origin_coords: dict = Field(default_factory=dict, description="Resolved lat/lng for origin")
    destination_coords: dict = Field(default_factory=dict, description="Resolved lat/lng for destination")
    travel_mode: str = Field(default="driving", description="Selected travel mode")


class PlaceSuggestion(BaseModel):
    """Autocomplete location prediction."""
    name: str
    subtitle: str
    display_name: str
    lat: float
    lng: float


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "ok"
    service: str = "cmi"
    version: str = "0.2.0"


class ProviderInfo(BaseModel):
    """LLM provider status entry."""
    name: str
    model: str
    base_url: str
    configured: bool


# ── Router Factory ─────────────────────────────────────────────

def create_router(
    route_harvester,
    llm_router,
    automation_scheduler=None,
) -> APIRouter:
    """
    Factory function that creates the API router with injected
    engine dependencies.
    """
    router = APIRouter(prefix="/api", tags=["CMI Core"])

    # ── POST /api/route/analyze ────────────────────────────────
    @router.post(
        "/route/analyze",
        response_model=RouteAnalysisResponse,
        summary="Analyze a driving route corridor with road polylines",
    )
    async def analyze_route(req: RouteAnalysisRequest):
        """Main endpoint — triggers the full road corridor analysis pipeline."""
        logger.info("Route analysis requested: '%s' → '%s' [mode: %s]", req.origin, req.destination, req.mode)

        try:
            corridor_summary = await route_harvester.process(
                origin=req.origin,
                destination=req.destination,
                mode=req.mode,
            )
        except Exception as exc:
            logger.exception("Route analysis pipeline failed")
            raise HTTPException(
                status_code=500,
                detail=f"Analysis pipeline error: {str(exc)}",
            )

        return RouteAnalysisResponse(
            summary=corridor_summary.summary_text,
            provider=corridor_summary.provider_used,
            bounding_box=corridor_summary.bounding_box,
            distance_km=corridor_summary.distance_km,
            duration_min=corridor_summary.duration_min,
            origin_coords=corridor_summary.origin_coords,
            destination_coords=corridor_summary.destination_coords,
            route_geometry=corridor_summary.route_geometry,
            travel_mode=corridor_summary.travel_mode,
            alerts=[
                AlertMarker(
                    headline=a.headline,
                    description=a.description,
                    lat=a.lat,
                    lng=a.lng,
                    severity=a.severity,
                    category=a.category,
                    source=a.source,
                    marker_color=a.marker_color,
                )
                for a in corridor_summary.alerts
            ],
        )

    # ── GET /api/places/autocomplete ───────────────────────────
    @router.get(
        "/places/autocomplete",
        response_model=list[PlaceSuggestion],
        summary="Predictive place autocomplete search",
    )
    async def autocomplete_places(q: str = Query(..., min_length=1, description="Search query")):
        """Provides instant location predictions as the user types."""
        results = route_harvester.search_autocomplete(query=q, limit=5)
        return [PlaceSuggestion(**item) for item in results]

    # ── GET /api/corridor/latest ───────────────────────────────
    @router.get(
        "/corridor/latest",
        summary="Get latest automated corridor intelligence",
    )
    async def get_latest_intelligence():
        """Returns the latest autonomous background LLM intelligence without delay."""
        if automation_scheduler:
            return automation_scheduler.get_status()
        return {"status": "scheduler_not_initialized"}

    # ── POST /api/automation/trigger ───────────────────────────
    @router.post(
        "/automation/trigger",
        summary="Manually trigger an automated LLM harvest cycle",
    )
    async def trigger_automation():
        """Force triggers an automated LLM intelligence refresh cycle."""
        if automation_scheduler:
            result = await automation_scheduler.execute_automation_cycle()
            return {"status": "success", "result": result}
        raise HTTPException(status_code=503, detail="Automation scheduler unavailable")

    # ── GET /api/health ────────────────────────────────────────
    @router.get("/health", response_model=HealthResponse)
    async def health_check():
        return HealthResponse()

    # ── GET /api/providers ─────────────────────────────────────
    @router.get("/providers", response_model=list[ProviderInfo])
    async def list_providers():
        return [ProviderInfo(**p) for p in llm_router.list_providers()]

    return router
