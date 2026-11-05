"""
============================================================
CMI — API Route Definitions

FastAPI router that exposes the core CMI endpoints:

  POST /api/route/analyze
       Accepts origin + destination, runs the full corridor
       analysis pipeline, and returns a structured JSON response
       with an AI summary and geo-tagged alert markers.

  GET  /api/health
       Lightweight health check for monitoring and Vercel probes.

  GET  /api/providers
       Lists configured LLM providers and their status.

All heavy lifting is delegated to the engine layer.
The routes are thin HTTP adapters only.
============================================================
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
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


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "ok"
    service: str = "cmi"
    version: str = "0.1.0"


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
) -> APIRouter:
    """
    Factory function that creates the API router with injected
    engine dependencies. This pattern keeps the route definitions
    decoupled from the engine instantiation in main.py.

    Args:
        route_harvester: A RouteHarvester instance.
        llm_router:      An LLMRouter instance.

    Returns:
        A configured FastAPI APIRouter.
    """
    router = APIRouter(prefix="/api", tags=["CMI Core"])

    # ── POST /api/route/analyze ────────────────────────────────
    @router.post(
        "/route/analyze",
        response_model=RouteAnalysisResponse,
        summary="Analyze a driving route corridor",
        description=(
            "Geocodes origin and destination, calculates a driving route, "
            "harvests corridor alerts, and returns an AI-summarized analysis "
            "with geo-tagged markers for the frontend map."
        ),
    )
    async def analyze_route(req: RouteAnalysisRequest):
        """
        Main endpoint — triggers the full corridor analysis pipeline.
        """
        logger.info(
            "Route analysis requested: '%s' → '%s'",
            req.origin, req.destination,
        )

        try:
            corridor_summary = await route_harvester.process(
                origin=req.origin,
                destination=req.destination,
            )
        except Exception as exc:
            logger.exception("Route analysis pipeline failed")
            raise HTTPException(
                status_code=500,
                detail=f"Analysis pipeline error: {str(exc)}",
            )

        # Map dataclass output → Pydantic response model
        return RouteAnalysisResponse(
            summary=corridor_summary.summary_text,
            provider=corridor_summary.provider_used,
            bounding_box=corridor_summary.bounding_box,
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

    # ── GET /api/health ────────────────────────────────────────
    @router.get(
        "/health",
        response_model=HealthResponse,
        summary="Service health check",
    )
    async def health_check():
        """Returns service status for monitoring and deployment probes."""
        return HealthResponse()

    # ── GET /api/providers ─────────────────────────────────────
    @router.get(
        "/providers",
        response_model=list[ProviderInfo],
        summary="List configured LLM providers",
    )
    async def list_providers():
        """Returns the configured LLM providers and their status."""
        return [
            ProviderInfo(**p) for p in llm_router.list_providers()
        ]

    return router
