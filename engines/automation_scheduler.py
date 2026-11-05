"""
============================================================
CMI — Automated Background LLM Intelligence Scheduler

Periodically and autonomously analyzes major transportation corridors
in Chennai without requiring human intervention:
  1. Cycles through high-priority arterial mobility corridors.
  2. Scrapes and harvests real-time municipal, hazard, and traffic reports.
  3. Sanitizes text through TextIsolationEngine down to strict plaintext.
  4. Runs the multi-provider LLM router (NVIDIA NIM -> Groq -> OpenAI).
  5. Caches updated intelligence into memory and local JSON storage.
  6. Broadcasts fresh traffic state to the frontend and API consumers.
============================================================
"""

from __future__ import annotations

import os
import json
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any

from config import PROJECT_ROOT, DIRS

logger = logging.getLogger("cmi.engines.automation_scheduler")


# ── Chennai Major Arterial Corridors ───────────────────────────
KEY_CHENNAI_CORRIDORS = [
    {
        "id": "anna_salai_gst",
        "name": "Anna Salai (Mount Road) ➔ GST Road Corridor",
        "origin": "Chennai Central",
        "destination": "Chennai Airport",
        "priority": 1,
    },
    {
        "id": "omr_it_corridor",
        "name": "Old Mahabalipuram Road (OMR) IT Expressway",
        "origin": "Madhya Kailash",
        "destination": "Siruseri IT Park",
        "priority": 2,
    },
    {
        "id": "inner_ring_road",
        "name": "100 Feet Inner Ring Road (Jawaharlal Nehru Road)",
        "origin": "Koyambedu CMBT",
        "destination": "Kathipara Junction",
        "priority": 3,
    },
    {
        "id": "ecr_coastal",
        "name": "East Coast Road (ECR) Coastal Corridor",
        "origin": "Thiruvanmiyur",
        "destination": "Kovalam",
        "priority": 4,
    },
]


class AutomationScheduler:
    """
    Autonomous background worker that periodically updates
    corridor intelligence without human prompting.
    """

    def __init__(
        self,
        route_harvester,
        llm_router,
        refresh_interval_seconds: int = 300,  # 5 minutes
    ):
        self.harvester = route_harvester
        self.llm = llm_router
        self.interval = refresh_interval_seconds
        self.is_running = False
        self._task: Optional[asyncio.Task] = None
        self.latest_intelligence: dict[str, Any] = {
            "last_updated": None,
            "next_scheduled_run": None,
            "provider_used": None,
            "corridors": {},
            "status": "idle",
        }
        self.cache_file = self._resolve_cache_path()

    def _resolve_cache_path(self) -> Path:
        """Resolves cache path safely across local disk and read-only cloud lambdas."""
        cache_dir = DIRS.get("data_cache", PROJECT_ROOT / "data" / "cache")
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            return cache_dir / "latest_intelligence.json"
        except OSError:
            tmp_cache = Path("/tmp/cmi/cache")
            tmp_cache.mkdir(parents=True, exist_ok=True)
            return tmp_cache / "latest_intelligence.json"

    def start(self) -> None:
        """Starts the autonomous background scheduling loop."""
        if self.is_running:
            logger.warning("AutomationScheduler is already running.")
            return

        self.is_running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "AutomationScheduler started! Refresh interval: %d seconds",
            self.interval,
        )

    def stop(self) -> None:
        """Stops the scheduler gracefully."""
        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("AutomationScheduler stopped.")

    async def _run_loop(self) -> None:
        """Main periodic loop."""
        # Initial analysis run shortly after boot (give app 5s to settle)
        await asyncio.sleep(5)

        while self.is_running:
            try:
                await self.execute_automation_cycle()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Error in automated LLM intelligence cycle: %s", exc)

            logger.info("AutomationScheduler sleeping for %d seconds...", self.interval)
            await asyncio.sleep(self.interval)

    async def execute_automation_cycle(self) -> dict[str, Any]:
        """
        Executes a single end-to-end harvest, plain-text isolation,
        and LLM inference cycle for the primary arterial corridors.
        """
        now = datetime.now(timezone.utc)
        logger.info("=== Starting Autonomous LLM Intelligence Harvest ===")
        self.latest_intelligence["status"] = "processing"

        corridor_results = {}
        last_provider = "unknown"

        for corridor in KEY_CHENNAI_CORRIDORS:
            c_id = corridor["id"]
            logger.info("Automating corridor analysis: %s (%s -> %s)",
                        corridor["name"], corridor["origin"], corridor["destination"])

            try:
                summary = await self.harvester.process(
                    origin=corridor["origin"],
                    destination=corridor["destination"],
                )
                last_provider = summary.provider_used
                corridor_results[c_id] = {
                    "id": c_id,
                    "name": corridor["name"],
                    "origin": corridor["origin"],
                    "destination": corridor["destination"],
                    "summary": summary.summary_text,
                    "provider": summary.provider_used,
                    "alerts_count": len(summary.alerts),
                    "alerts": [
                        {
                            "headline": a.headline,
                            "description": a.description,
                            "lat": a.lat,
                            "lng": a.lng,
                            "severity": a.severity,
                            "marker_color": a.marker_color,
                            "category": a.category,
                        }
                        for a in summary.alerts
                    ],
                    "bounding_box": summary.bounding_box,
                    "distance_km": summary.distance_km,
                    "duration_min": summary.duration_min,
                    "updated_at": now.isoformat(),
                }
            except Exception as exc:
                logger.error("Failed to analyze corridor %s: %s", c_id, exc)

        next_run = datetime.fromtimestamp(
            now.timestamp() + self.interval, timezone.utc
        )

        self.latest_intelligence = {
            "last_updated": now.isoformat(),
            "next_scheduled_run": next_run.isoformat(),
            "provider_used": last_provider,
            "corridors": corridor_results,
            "status": "ready",
            "interval_seconds": self.interval,
        }

        # Persist to cache file
        try:
            self.cache_file.write_text(
                json.dumps(self.latest_intelligence, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

        logger.info(
            "=== Automated LLM Cycle Finished (%d corridors updated via %s) ===",
            len(corridor_results),
            last_provider,
        )
        return self.latest_intelligence

    def get_status(self) -> dict[str, Any]:
        """Returns the current state and latest cached summaries."""
        return self.latest_intelligence
