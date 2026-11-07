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
    summary_text: Optional[str] = Field(default=None, description="Alias for summary")
    provider_used: Optional[str] = Field(default=None, description="Alias for provider")
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
    origin: Optional[str] = Field(default=None, description="Origin address or place name")
    destination: Optional[str] = Field(default=None, description="Destination address or place name")


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


class SendOtpRequest(BaseModel):
    phone: str = Field(..., description="Mobile phone number")
    email: Optional[str] = Field(default=None, description="User email for audit")
    otp: str = Field(..., min_length=4, max_length=8, description="OTP code")


class SendOtpResponse(BaseModel):
    status: str
    gateway: str
    delivered: bool
    message: str
    phone_masked: str


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
            summary_text=corridor_summary.summary_text,
            provider_used=corridor_summary.provider_used,
            bounding_box=corridor_summary.bounding_box,
            distance_km=corridor_summary.distance_km,
            duration_min=corridor_summary.duration_min,
            origin_coords=corridor_summary.origin_coords,
            destination_coords=corridor_summary.destination_coords,
            route_geometry=corridor_summary.route_geometry,
            travel_mode=corridor_summary.travel_mode,
            origin=req.origin,
            destination=req.destination,
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

    # ── GET /api/db/incidents ──────────────────────────────────
    @router.get(
        "/db/incidents",
        summary="Fetch live citizen incidents from Supabase database",
    )
    async def get_db_incidents():
        """Returns all real-time citizen-reported incidents stored in Supabase."""
        try:
            import httpx
            from config import get_settings
            settings = get_settings()
            url = f"{settings.supabase_url.rstrip('/')}/rest/v1/cmi_incidents?select=*&order=reported_at.desc"
            headers = {
                "apikey": settings.supabase_key,
                "Authorization": f"Bearer {settings.supabase_key}",
            }
            resp = httpx.get(url, headers=headers, timeout=5.0)
            if resp.status_code == 200:
                return {"status": "ok", "count": len(resp.json()), "incidents": resp.json()}
            return {"status": "error", "code": resp.status_code, "detail": resp.text}
        except Exception as exc:
            return {"status": "error", "detail": str(exc)}

    # ── POST /api/auth/send-otp ────────────────────────────────
    @router.post(
        "/auth/send-otp",
        response_model=SendOtpResponse,
        summary="Send real cellular SMS OTP via Renflair Gateway",
    )
    async def send_sms_otp(req: SendOtpRequest):
        """Dispatches an SMS OTP to an Indian mobile phone using the Renflair SMS Gateway."""
        import httpx
        import re
        from config import get_settings

        settings = get_settings()
        clean_phone = re.sub(r"[^\d]", "", req.phone)
        if len(clean_phone) > 10 and clean_phone.startswith("91"):
            clean_phone = clean_phone[-10:]
        elif len(clean_phone) > 10:
            clean_phone = clean_phone[-10:]

        phone_masked = f"+91 {clean_phone[:2]}•••••{clean_phone[-3:]}" if len(clean_phone) == 10 else req.phone

        if not settings.renflair_api_key:
            logger.warning("Renflair SMS API key not configured in .env. Falling back to local display.")
            return SendOtpResponse(
                status="simulated",
                gateway="local",
                delivered=False,
                message="SMS gateway API key not configured. OTP displayed on screen.",
                phone_masked=phone_masked,
            )

        # Dispatch via Renflair SMS Gateway
        renflair_url = f"https://sms.renflair.in/V1.php?API={settings.renflair_api_key}&PHONE={clean_phone}&OTP={req.otp}"
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(renflair_url, headers={"User-Agent": "CMI-Platform/1.0"})
                data = resp.json() if resp.status_code == 200 else {}

                if data.get("status") == "SUCCESS":
                    logger.info("Renflair SMS OTP successfully dispatched to %s", phone_masked)
                    return SendOtpResponse(
                        status="success",
                        gateway="renflair",
                        delivered=True,
                        message=f"SMS OTP successfully sent to {phone_masked} via Renflair Gateway.",
                        phone_masked=phone_masked,
                    )
                else:
                    err_msg = data.get("message") or resp.text
                    logger.warning("Renflair SMS Gateway warning: %s", err_msg)
                    return SendOtpResponse(
                        status="gateway_warning",
                        gateway="renflair",
                        delivered=False,
                        message=f"Gateway response: {err_msg}",
                        phone_masked=phone_masked,
                    )
        except Exception as exc:
            logger.error("Failed to connect to Renflair SMS Gateway: %s", exc)
            return SendOtpResponse(
                status="error",
                gateway="renflair",
                delivered=False,
                message=f"Gateway connection error: {str(exc)}",
                phone_masked=phone_masked,
            )

    # ── GET /api/config ────────────────────────────────────────
    @router.get("/config", summary="Client configuration from environment variables")
    async def get_client_config():
        """Returns public client configuration dynamically loaded from environment variables."""
        from config import get_settings
        settings = get_settings()
        return {
            "supabase_url": settings.supabase_url,
            "supabase_key": settings.supabase_key,
            "has_sms_gateway": bool(settings.renflair_api_key),
            "app_env": settings.app_env,
            "version": "0.2.0",
        }

    # ── GET /api/health ────────────────────────────────────────
    @router.get("/health", response_model=HealthResponse)
    async def health_check():
        return HealthResponse()

    # ── GET /api/providers ─────────────────────────────────────
    @router.get("/providers", response_model=list[ProviderInfo])
    async def list_providers():
        return [ProviderInfo(**p) for p in llm_router.list_providers()]

    return router
