"""
============================================================
CMI — Citizen Mobility Intelligence
Application Entry Point

Bootstraps the FastAPI application:
  1. Verifies the workspace directory structure.
  2. Loads and validates environment configuration.
  3. Instantiates the engine layer (TextIsolation, LLMRouter,
     RouteHarvester).
  4. Mounts the API router and static file serving.
  5. Starts the Uvicorn ASGI server.

Run directly:
    python main.py

Or via Uvicorn:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
============================================================
"""

from __future__ import annotations

import sys
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, Response

# ── Configure logging before any engine imports ────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("cmi.main")

# ── Local imports ──────────────────────────────────────────────
from config import get_settings, verify_workspace, DIRS
from engines.text_isolation import TextIsolationEngine
from engines.llm_router import LLMRouter
from engines.route_harvester import RouteHarvester
from engines.automation_scheduler import AutomationScheduler
from api.routes import create_router


def create_app() -> FastAPI:
    """
    Application factory — creates and configures the FastAPI instance.
    
    This pattern allows the app to be imported by Uvicorn, test
    harnesses, and serverless adapters without side effects.
    """

    # ── Step 1: Verify workspace ───────────────────────────────
    logger.info("=" * 60)
    logger.info("CMI — Citizen Mobility Intelligence")
    logger.info("Verifying workspace at: %s", DIRS["root"])
    workspace_report = verify_workspace()
    for name, existed in workspace_report.items():
        status = "✓ exists" if existed else "✚ created"
        logger.info("  %-12s %s", f"[{name}]", status)

    # ── Step 2: Load configuration ─────────────────────────────
    settings = get_settings()
    logger.info("Configuration loaded (env: %s)", settings.app_env)
    logger.info("LLM timeout: %.1fs | max retries: %d",
                settings.llm_timeout_seconds, settings.llm_max_retries)

    # ── Step 3: Instantiate engines ────────────────────────────
    text_engine = TextIsolationEngine()
    logger.info("TextIsolationEngine initialized")

    llm_router = LLMRouter.from_settings(settings)
    logger.info("LLMRouter initialized with %d providers",
                len(llm_router.list_providers()))

    route_harvester = RouteHarvester(
        llm_router=llm_router,
        text_engine=text_engine,
    )
    logger.info("RouteHarvester initialized")

    # Background automated LLM scheduler (autonomous corridor refresh)
    automation_scheduler = AutomationScheduler(
        route_harvester=route_harvester,
        llm_router=llm_router,
        refresh_interval_seconds=300,  # 5 min periodic intervals
    )
    logger.info("AutomationScheduler initialized")

    # ── Step 4: Create FastAPI app ─────────────────────────────
    app = FastAPI(
        title="CMI — Citizen Mobility Intelligence",
        description=(
            "AI-powered route corridor analysis for Chennai. "
            "Overlays traffic, construction, and hazard intelligence "
            "on an interactive map."
        ),
        version="0.2.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Mount API routes
    api_router = create_router(
        route_harvester=route_harvester,
        llm_router=llm_router,
        automation_scheduler=automation_scheduler,
    )
    app.include_router(api_router)

    # Mount static files (CSS, JS, images)
    static_dir = DIRS["static"]
    if static_dir.exists():
        app.mount(
            "/static",
            StaticFiles(directory=str(static_dir)),
            name="static",
        )
        logger.info("Static files mounted from: %s", static_dir)

    # Mount assets (logos, background art)
    assets_dir = static_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")
        logger.info("Assets mounted from: %s", assets_dir)

    # Mount frontend files (CityLens portal scripts, assets)
    frontend_dir = DIRS["root"] / "frontend"
    if frontend_dir.exists():
        app.mount(
            "/frontend",
            StaticFiles(directory=str(frontend_dir)),
            name="frontend",
        )
        logger.info("Frontend files mounted from: %s", frontend_dir)

    # ── Serve Static Assets explicitly ─────────────────────────
    @app.get("/assets/{filename}", include_in_schema=False)
    async def serve_asset_file(filename: str):
        for candidate_dir in [static_dir / "assets", DIRS["root"] / "public" / "assets"]:
            candidate = candidate_dir / filename
            if candidate.exists() and candidate.is_file():
                media_type = "image/png" if filename.endswith(".png") else "application/octet-stream"
                return FileResponse(str(candidate), media_type=media_type)
        return Response(status_code=404)

    # ── Serve CMI AI Corridor Map at root and /cmi ────────────
    @app.get("/", include_in_schema=False)
    @app.get("/cmi", include_in_schema=False)
    async def serve_cmi_map():
        """Serves the CMI AI Corridor Map interface."""
        index_path = static_dir / "index.html"
        if index_path.exists():
            return HTMLResponse(content=index_path.read_text(encoding="utf-8"))
        return {"message": "CMI API is running. Visit /docs for API documentation."}

    # ── Serve CityLens Citizen Intelligence & Reports Portal ──
    @app.get("/portal", include_in_schema=False)
    @app.get("/citylens", include_in_schema=False)
    async def serve_citylens_portal():
        """Serves the CityLens Citizen Portal connected with Supabase."""
        portal_path = frontend_dir / "citylens.html"
        if portal_path.exists():
            return HTMLResponse(content=portal_path.read_text(encoding="utf-8"))
        return {"message": "CityLens portal file not found."}

    # ── Startup event ──────────────────────────────────────────
    @app.on_event("startup")
    async def on_startup():
        logger.info("=" * 60)
        logger.info("CMI server started on http://%s:%d",
                     settings.app_host, settings.app_port)
        logger.info("API docs at http://%s:%d/docs",
                     settings.app_host, settings.app_port)
        logger.info("=" * 60)
        # Start autonomous LLM intelligence background scheduler
        automation_scheduler.start()

    # ── Shutdown event ─────────────────────────────────────────
    @app.on_event("shutdown")
    async def on_shutdown():
        logger.info("Stopping AutomationScheduler...")
        automation_scheduler.stop()

    return app


# ── Module-level app instance (for `uvicorn main:app`) ─────────
app = create_app()


# ── Direct execution ───────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=(settings.app_env == "development"),
        log_level=settings.log_level.lower(),
    )
