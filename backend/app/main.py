"""FastAPI entry — mounts OTEL endpoints, panels API, SPA frontend."""
from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api.fixactions import router as fixactions_router
from .api.panels import router as panels_router
from .config import DEV_MODE, FRONTEND_DIST, HOST, PORT
from .db import init_db
from .ingest.otel import router as otel_router
from .ingest.watcher import run_watcher
from .ingest.system_metrics import run_system_metrics_collector
from .ingest.hook_monitor import run_hook_monitor
from .ingest.pricing import run_pricing_refresh
from .ingest.yield_analyzer import run_yield_analyzer
from .ingest.optimize import run_optimize_detector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("claude-dashboard")


def create_app() -> FastAPI:
    init_db()
    if DEV_MODE:
        from .ingest.fixtures import seed

        count = seed()
        log.info("DEV_MODE: seeded %d fixture tool_calls", count)

    app = FastAPI(
        title="Claude Code Dashboard",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
    )

    app.include_router(otel_router)
    app.include_router(panels_router)
    app.include_router(fixactions_router)

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "version": __version__}

    # Static SPA
    if FRONTEND_DIST.exists() and (FRONTEND_DIST / "index.html").exists():
        app.mount(
            "/assets",
            StaticFiles(directory=str(FRONTEND_DIST / "assets")),
            name="assets",
        )

        @app.api_route("/", methods=["GET", "HEAD"])
        def index():
            return FileResponse(FRONTEND_DIST / "index.html")

        @app.api_route("/{path:path}", methods=["GET", "HEAD"])
        def spa_catchall(path: str):
            # Anything that looks like an API/static request has been handled already.
            return FileResponse(FRONTEND_DIST / "index.html")
    else:

        @app.get("/")
        def index_fallback():
            return {
                "ok": True,
                "message": (
                    "Frontend not built yet. Run `cd frontend && npm install && npm run build`. "
                    "API docs at /api/docs."
                ),
                "api_docs": "/api/docs",
            }

    @app.on_event("startup")
    async def _start_watcher():
        # Hold a reference on app.state so the task isn't garbage-collected.
        app.state.watcher_task = asyncio.create_task(run_watcher())
        app.state.system_metrics_task = asyncio.create_task(run_system_metrics_collector())
        app.state.hook_monitor_task = asyncio.create_task(run_hook_monitor())
        app.state.pricing_task = asyncio.create_task(run_pricing_refresh())
        app.state.yield_task = asyncio.create_task(run_yield_analyzer())
        app.state.optimize_task = asyncio.create_task(run_optimize_detector())

    @app.on_event("shutdown")
    async def _stop_watcher():
        for task_name in ["watcher_task", "system_metrics_task", "hook_monitor_task", "pricing_task", "yield_task", "optimize_task"]:
            task = getattr(app.state, task_name, None)
            if task:
                task.cancel()

    return app


app = create_app()


def main():
    """Run with `python -m app.main`."""
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=PORT,
        reload=False,
        access_log=False,
    )


if __name__ == "__main__":
    main()
