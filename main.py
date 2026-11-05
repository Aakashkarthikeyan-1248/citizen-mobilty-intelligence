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
from fastapi.responses import FileResponse, HTMLResponse

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

    # ── Serve landing page at root ─────────────────────────────
    @app.get("/", include_in_schema=False)
    async def serve_landing_page():
        """Serves the CMI landing page HTML shell."""
        index_path = static_dir / "index.html"
        if index_path.exists():
            return HTMLResponse(content=index_path.read_text(encoding="utf-8"))
        return {"message": "CMI API is running. Visit /docs for API documentation."}

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
