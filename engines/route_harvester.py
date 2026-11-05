"""
============================================================
CMI — Route Bounding-Box News Harvester

End-to-end pipeline that:
  1. Geocodes origin/destination strings → lat/lng coordinates.
  2. Fetches a driving route polyline between them.
  3. Expands the polyline into a geographic bounding-box corridor.
  4. Harvests (mocks) recent transportation / hazard news within
     that corridor.
  5. Feeds the harvested text through the TextIsolationEngine
     and then the LLMRouter to produce a structured JSON summary
     with marker metadata for the frontend map.

This module is designed so that every external dependency
(geocoding, routing, news APIs) is behind a method that can
be swapped from mock → real without touching the pipeline logic.
============================================================
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Optional

from shapely.geometry import LineString, Polygon, box, mapping
from geopy.geocoders import Nominatim

logger = logging.getLogger("cmi.engines.route_harvester")


# ── Data contracts ─────────────────────────────────────────────

@dataclass
class GeoPoint:
    """A single geographic coordinate."""
    lat: float
    lng: float

    def as_tuple(self) -> tuple[float, float]:
        return (self.lat, self.lng)


@dataclass
class RouteSegment:
    """A decoded driving route between two points."""
    origin: GeoPoint
    destination: GeoPoint
    waypoints: list[GeoPoint] = field(default_factory=list)
    distance_km: float = 0.0
    duration_min: float = 0.0


@dataclass
class CorridorAlert:
    """A single transportation/hazard alert within the route corridor."""
    headline: str
    description: str
    lat: float
    lng: float
    severity: str = "medium"        # low | medium | high | critical
    category: str = "general"       # traffic | construction | hazard | municipal
    source: str = "mock"
    marker_color: str = "orange"    # Maps to frontend marker styling


@dataclass
class CorridorSummary:
    """
    Final structured output sent to the frontend.
    Contains a text summary and an array of geo-tagged alert markers.
    """
    summary_text: str
    alerts: list[CorridorAlert] = field(default_factory=list)
    bounding_box: dict = field(default_factory=dict)
    provider_used: str = ""

    def to_json(self) -> str:
        """Serialize to JSON for the API response."""
        return json.dumps(
            {
                "summary": self.summary_text,
                "provider": self.provider_used,
                "bounding_box": self.bounding_box,
                "alerts": [
                    {
                        "headline": a.headline,
                        "description": a.description,
                        "lat": a.lat,
                        "lng": a.lng,
                        "severity": a.severity,
                        "category": a.category,
                        "source": a.source,
                        "marker_color": a.marker_color,
                    }
                    for a in self.alerts
                ],
            },
            indent=2,
        )


# ── Harvester Engine ──────────────────────────────────────────

class RouteHarvester:
    """
    Orchestrates the full geocode → route → corridor → harvest → LLM pipeline.

    Usage:
        harvester = RouteHarvester(llm_router, text_engine)
        summary = await harvester.process(
            origin="Chennai Central",
            destination="Chennai Airport",
        )
        print(summary.to_json())
    """

    # Buffer distance in degrees (~1.1 km per 0.01°) to expand
    # the route polyline into a corridor polygon.
    CORRIDOR_BUFFER_DEG = 0.015   # ~1.65 km each side

    # Severity → marker color mapping for the frontend
    SEVERITY_COLORS = {
        "low":      "green",
        "medium":   "orange",
        "high":     "red",
        "critical": "darkred",
    }

    def __init__(self, llm_router, text_engine):
        """
        Args:
            llm_router:  An LLMRouter instance for AI summarization.
            text_engine: A TextIsolationEngine instance for text cleaning.
        """
        self._llm = llm_router
        self._text = text_engine
        self._geocoder = Nominatim(
            user_agent="cmi-citizen-mobility-intelligence/1.0",
            timeout=10,
        )

    # ── Public API ─────────────────────────────────────────────

    async def process(
        self,
        origin: str,
        destination: str,
    ) -> CorridorSummary:
        """
        Full pipeline: geocode → route → corridor → harvest → summarize.

        Args:
            origin:      Human-readable start location string.
            destination: Human-readable end location string.

        Returns:
            CorridorSummary with AI-generated text and alert markers.
        """
        logger.info("Processing route: '%s' → '%s'", origin, destination)

        # Step 1: Geocode both endpoints
        origin_pt = self._geocode(origin)
        dest_pt = self._geocode(destination)
        logger.info(
            "Geocoded: %s → (%.4f, %.4f) | %s → (%.4f, %.4f)",
            origin, origin_pt.lat, origin_pt.lng,
            destination, dest_pt.lat, dest_pt.lng,
        )

        # Step 2: Fetch the driving route (mock polyline)
        route = self._fetch_route(origin_pt, dest_pt)

        # Step 3: Build the corridor bounding polygon
        corridor_poly, bbox = self._build_corridor(route)
        logger.info("Corridor polygon built with %d vertices", len(corridor_poly.exterior.coords))

        # Step 4: Harvest alerts within the corridor
        raw_alerts = self._harvest_corridor_alerts(corridor_poly, origin, destination)
        logger.info("Harvested %d raw alerts", len(raw_alerts))

        # Step 5: Clean alert text through the isolation engine
        from .text_isolation import MediaType
        cleaned_alert_texts = []
        for alert in raw_alerts:
            result = self._text.isolate(
                alert.description,
                MediaType.PLAINTEXT,
                source_label=f"alert:{alert.headline}",
            )
            cleaned_alert_texts.append(
                f"[{alert.severity.upper()}] {alert.headline}: {result.text}"
            )

        # Step 6: Send combined text to LLM for structured summary
        combined_context = (
            f"Route: {origin} → {destination}\n"
            f"Distance: ~{route.distance_km:.1f} km\n"
            f"Duration: ~{route.duration_min:.0f} min\n\n"
            f"Corridor Alerts:\n"
            + "\n".join(cleaned_alert_texts)
        )

        try:
            llm_response = await self._llm.generate(
                prompt=combined_context,
                system=(
                    "You are a traffic intelligence analyst for Chennai, India. "
                    "Given the route and corridor alerts below, produce a concise "
                    "summary (3-5 sentences) of current conditions, hazards, and "
                    "recommended actions for the driver. Be specific about locations."
                ),
                temperature=0.2,
                max_tokens=512,
            )
            summary_text = llm_response.text
            provider_used = llm_response.provider_used
        except Exception as exc:
            logger.error("LLM summarization failed: %s", exc)
            summary_text = "AI summary unavailable. Raw alerts are displayed on the map."
            provider_used = "none (fallback)"

        return CorridorSummary(
            summary_text=summary_text,
            alerts=raw_alerts,
            bounding_box=bbox,
            provider_used=provider_used,
        )

    # ── Geocoding ──────────────────────────────────────────────

    def _geocode(self, location_str: str) -> GeoPoint:
        """
        Converts a human-readable location string into coordinates.
        Falls back to a Chennai-area default if geocoding fails.
        """
        try:
            result = self._geocoder.geocode(location_str)
            if result:
                return GeoPoint(lat=result.latitude, lng=result.longitude)
        except Exception as exc:
            logger.warning("Geocoding failed for '%s': %s", location_str, exc)

        # Fallback: approximate Chennai center
        logger.warning(
            "Using Chennai fallback coordinates for '%s'", location_str
        )
        return GeoPoint(lat=13.0827, lng=80.2707)

    # ── Route Fetching (Mock) ──────────────────────────────────

    def _fetch_route(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
    ) -> RouteSegment:
        """
        MOCK: Generates a straight-line route with intermediate
        waypoints between origin and destination.

        In production, replace this with a call to:
          - Google Maps Directions API
          - OSRM
          - Valhalla
          - Mapbox Directions
        and decode the returned polyline.
        """
        num_waypoints = 20
        waypoints = []

        for i in range(1, num_waypoints + 1):
            frac = i / (num_waypoints + 1)
            lat = origin.lat + frac * (destination.lat - origin.lat)
            lng = origin.lng + frac * (destination.lng - origin.lng)
            # Add slight sinusoidal offset to simulate road curvature
            offset = 0.005 * math.sin(frac * math.pi * 3)
            waypoints.append(GeoPoint(lat=lat + offset, lng=lng - offset))

        # Rough distance estimate (haversine)
        dist_km = self._haversine(origin, destination)
        duration_min = dist_km / 25.0 * 60  # Assume avg 25 km/h in Chennai

        return RouteSegment(
            origin=origin,
            destination=destination,
            waypoints=waypoints,
            distance_km=dist_km,
            duration_min=duration_min,
        )

    # ── Corridor Construction ──────────────────────────────────

    def _build_corridor(
        self,
        route: RouteSegment,
    ) -> tuple[Polygon, dict]:
        """
        Expands the route polyline into a buffered corridor polygon.

        Returns:
            (corridor_polygon, bounding_box_dict)
        """
        # Build the LineString from all route points
        coords = (
            [(route.origin.lng, route.origin.lat)]
            + [(wp.lng, wp.lat) for wp in route.waypoints]
            + [(route.destination.lng, route.destination.lat)]
        )
        line = LineString(coords)

        # Buffer the line to create a corridor polygon
        corridor = line.buffer(self.CORRIDOR_BUFFER_DEG)

        # Extract the bounding box
        minx, miny, maxx, maxy = corridor.bounds
        bbox = {
            "south": miny,
            "west": minx,
            "north": maxy,
            "east": maxx,
        }

        return corridor, bbox

    # ── News Harvesting (Mock) ─────────────────────────────────

    def _harvest_corridor_alerts(
        self,
        corridor: Polygon,
        origin_name: str,
        destination_name: str,
    ) -> list[CorridorAlert]:
        """
        MOCK: Returns simulated transportation alerts geolocated
        within the corridor polygon.

        In production, replace this with:
          - Google News API filtered by corridor bounding box
          - RSS feed parsers for Chennai traffic authority
          - Web scraping of municipal alert portals
          - Twitter/X API for real-time incident reports
        """
        # These mock alerts represent typical Chennai corridor issues
        mock_alerts = [
            CorridorAlert(
                headline="Metro Rail Construction — Anna Salai",
                description=(
                    "<p>Chennai Metro Phase 2 <b>tunnel boring</b> operations "
                    "continue on Anna Salai between Gemini Flyover and "
                    "Saidapet. Single-lane traffic enforced 10 PM – 5 AM.</p>"
                ),
                lat=13.0478,
                lng=80.2500,
                severity="high",
                category="construction",
                marker_color="red",
            ),
            CorridorAlert(
                headline="Waterlogging Alert — T. Nagar",
                description=(
                    "Moderate waterlogging reported near Panagal Park due to "
                    "overnight rainfall. Municipal pumps deployed. Vehicles "
                    "advised to use Kodambakkam bypass."
                ),
                lat=13.0418,
                lng=80.2341,
                severity="medium",
                category="hazard",
                marker_color="orange",
            ),
            CorridorAlert(
                headline="Signal Malfunction — Adyar Junction",
                description=(
                    "Traffic signal at Adyar Junction (LB Road intersection) "
                    "is non-functional since 3:00 PM. Traffic police deployed "
                    "for manual control. Expect 10-15 min delays."
                ),
                lat=13.0063,
                lng=80.2574,
                severity="medium",
                category="traffic",
                marker_color="orange",
            ),
            CorridorAlert(
                headline="Road Resurfacing — ECR",
                description=(
                    "National Highways Authority of India resurfacing East "
                    "Coast Road between Thiruvanmiyur and Sholinganallur. "
                    "Expect diversions via OMR (Old Mahabalipuram Road)."
                ),
                lat=12.9800,
                lng=80.2590,
                severity="low",
                category="construction",
                marker_color="green",
            ),
            CorridorAlert(
                headline="VIP Movement — Rajiv Gandhi Salai",
                description=(
                    "VIP convoy movement scheduled between 4:00 PM and "
                    "5:30 PM on Rajiv Gandhi Salai (OMR). Rolling road "
                    "blocks expected. Plan alternate routes."
                ),
                lat=12.9516,
                lng=80.2417,
                severity="critical",
                category="traffic",
                marker_color="darkred",
            ),
        ]

        # Filter: keep only alerts whose coordinates fall inside the corridor
        filtered = []
        for alert in mock_alerts:
            from shapely.geometry import Point
            pt = Point(alert.lng, alert.lat)
            if corridor.contains(pt) or corridor.distance(pt) < 0.02:
                filtered.append(alert)
                logger.debug(
                    "Alert '%s' is within corridor", alert.headline
                )
            else:
                logger.debug(
                    "Alert '%s' filtered out (outside corridor)", alert.headline
                )

        # If nothing matched, return all (demo mode)
        if not filtered:
            logger.warning(
                "No alerts matched corridor — returning all for demo."
            )
            return mock_alerts

        return filtered

    # ── Utility ────────────────────────────────────────────────

    @staticmethod
    def _haversine(p1: GeoPoint, p2: GeoPoint) -> float:
        """Haversine distance in kilometers between two GeoPoints."""
        R = 6371.0  # Earth radius in km
        lat1, lat2 = math.radians(p1.lat), math.radians(p2.lat)
        dlat = math.radians(p2.lat - p1.lat)
        dlng = math.radians(p2.lng - p1.lng)
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
        )
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
