"""
main.py — FastAPI application entrypoint.

Responsibilities:
  - Creates the FastAPI app with lifespan context manager
  - Initialises all shared dependencies at startup:
      * MemoryManager (SQLite)
      * PersonaManager (persona.json)
      * Scheduler (not yet started — waits for /init)
  - Mounts the API router
  - Configures structured JSON logging
  - Serves the frontend static file at /
  - Configures CORS

The app does NOT auto-start the scheduler on startup.
The scheduler starts only when POST /api/agent/init is called,
satisfying the requirement that the loop be started by the init endpoint.
"""
from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.api.routes import router
from backend.config import get_settings
from backend.core.memory_manager import MemoryManager
from backend.core.persona_manager import PersonaManager
from backend.core.scheduler import Scheduler

# ─────────────────────────────────────────────────────────────────────────────
# Structured logging configuration
# ─────────────────────────────────────────────────────────────────────────────


def configure_logging(level: str = "INFO") -> None:
    """
    Configure structured logging with explicit UTF-8 encoding.
    Uses programmatic setup (not dictConfig) so we can force UTF-8
    on the StreamHandler — required on Windows to handle Unicode chars.
    """
    import sys

    # UTF-8 stream handler
    handler = logging.StreamHandler(sys.stdout)
    try:
        if hasattr(handler.stream, "reconfigure"):
            handler.stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)-40s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    # Suppress noisy third-party loggers
    for noisy in ("httpx", "anthropic", "uvicorn.access", "primp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)



# ─────────────────────────────────────────────────────────────────────────────
# Lifespan (startup / shutdown)
# ─────────────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs startup logic before the app begins serving requests,
    and shutdown logic after the last request completes.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    logger = logging.getLogger(__name__)
    logger.info("=" * 70)
    logger.info("Autonomous AI Creator starting up")
    logger.info("=" * 70)

    # ── Memory Manager ─────────────────────────────────────────────────────
    memory = await MemoryManager.create()
    app.state.memory = memory

    # ── Persona Manager ────────────────────────────────────────────────────
    persona_manager = PersonaManager()
    persona_manager.load()
    app.state.persona_manager = persona_manager

    # ── Scheduler (auto-started on boot) ──────────────────────────────────
    scheduler = Scheduler(memory, persona_manager)
    scheduler.start()
    app.state.scheduler = scheduler

    persona = persona_manager.persona
    logger.info(
        "Application ready",
        extra={
            "agent": persona.name,
            "role": persona.role,
            "cycle_interval": settings.cycle_interval_seconds,
            "db_path": settings.db_path,
        },
    )
    logger.info("POST /api/agent/init to start the autonomous pipeline.")
    logger.info("=" * 70)

    yield  # ← Application serves requests here

    # ── Shutdown ───────────────────────────────────────────────────────────
    logger.info("Shutting down — signalling scheduler to stop")
    scheduler.stop()
    logger.info("Autonomous AI Creator shut down cleanly")


# ─────────────────────────────────────────────────────────────────────────────
# App factory
# ─────────────────────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Autonomous AI Creator",
        description=(
            "An autonomous backend AI agent that independently discovers fresh "
            "AI/technology topics, applies multi-dimensional editorial judgment, "
            "and publishes persona-consistent posts — all without human input."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # ── CORS ───────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── API routes ─────────────────────────────────────────────────────────
    app.include_router(router)

    # ── Frontend static file ───────────────────────────────────────────────
    frontend_dir = Path(__file__).parent.parent / "frontend"
    if frontend_dir.exists():
        @app.get("/", include_in_schema=False)
        async def serve_frontend():
            return FileResponse(frontend_dir / "index.html")

    # ── Health check ───────────────────────────────────────────────────────
    @app.get("/health", include_in_schema=False)
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
