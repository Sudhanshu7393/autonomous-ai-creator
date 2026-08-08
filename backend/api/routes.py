"""
routes.py — FastAPI route handlers.

This module is intentionally thin. Every endpoint either:
  a) Reads from the MemoryManager (pure read — no side effects), or
  b) Starts the Scheduler (non-blocking — returns immediately).

No business logic lives here. The API layer is a transparent window
into the pipeline's persistent state.

KEY CONSTRAINTS (enforced by design, not just convention):
  - GET /feed: reads from DB, NEVER triggers generation.
  - POST /init: starts scheduler and returns immediately (non-blocking).
  - GET /status: reads in-memory scheduler state, no DB query on critical path.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from backend.api.schemas import (
    FeedResponse,
    InitResponse,
    PostItem,
    RejectedFeedResponse,
    RejectedTopicItem,
    StatusResponse,
)
from backend.config import get_settings
from backend.core.memory_manager import MemoryManager
from backend.core.scheduler import get_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent"])


# ─────────────────────────────────────────────────────────────────────────────
# Dependency: Memory Manager from app state
# ─────────────────────────────────────────────────────────────────────────────


def get_memory(request: Request) -> MemoryManager:
    return request.app.state.memory


def get_scheduler(request: Request):
    return request.app.state.scheduler


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/agent/init
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/init",
    response_model=InitResponse,
    status_code=status.HTTP_200_OK,
    summary="Start the autonomous agent",
    description=(
        "Starts the background autonomous pipeline loop if not already running. "
        "Returns immediately — does NOT wait for any cycle to complete."
    ),
)
async def init_agent(
    scheduler=Depends(get_scheduler),
) -> InitResponse:
    """
    Non-blocking init. The scheduler runs in an asyncio background task,
    completely decoupled from this request lifecycle.
    """
    state = get_state()
    persona_name = scheduler._persona_manager.persona.name

    if state.is_running:
        logger.info("Init called but agent already running")
        return InitResponse(
            status="already_running",
            agent=persona_name,
            message=f"Agent {persona_name} is already running autonomously.",
        )

    started = scheduler.start()
    logger.info("Agent initialised via API", extra={"agent": persona_name})

    return InitResponse(
        status="initialized",
        agent=persona_name,
        message=f"Agent {persona_name} started. First cycle running now.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/agent/feed
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/feed",
    response_model=FeedResponse,
    summary="Get all published posts",
    description=(
        "Returns all generated posts, newest first. "
        "Read-only — calling this endpoint NEVER triggers generation."
    ),
)
async def get_feed(
    memory: MemoryManager = Depends(get_memory),
    limit: int = 100,
) -> FeedResponse:
    """
    Pure read. Queries the database and returns posts.
    Zero side effects. No generation, no state mutation.
    """
    posts = await memory.get_feed(limit=limit)
    items = [
        PostItem(
            id=p.id,
            cycle_id=p.cycle_id,
            topic=p.topic,
            post_text=p.post_text,
            rationale=p.rationale,
            relevance_score=p.relevance_score,
            novelty_score=p.novelty_score,
            depth_score=p.depth_score,
            composite_score=p.composite_score,
            source_url=p.source_url,
            timestamp=p.published_at,
        )
        for p in posts
    ]
    return FeedResponse(posts=items, total=len(items))


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/agent/status
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/status",
    response_model=StatusResponse,
    summary="Get agent scheduler status",
    description="Returns live scheduler state including countdown to next cycle.",
)
async def get_status(
    memory: MemoryManager = Depends(get_memory),
) -> StatusResponse:
    """Returns combined live scheduler state + aggregate DB stats."""
    state = get_state()
    db_stats = await memory.get_cycle_stats()
    settings = get_settings()

    return StatusResponse(
        is_running=state.is_running,
        agent=state.agent_name,
        cycles_completed=state.cycles_completed,
        cycles_failed=state.cycles_failed,
        next_cycle_in_seconds=state.seconds_until_next(),
        last_cycle_at=state.last_cycle_at.isoformat() if state.last_cycle_at else None,
        last_cycle_topic=state.last_cycle_topic,
        started_at=state.started_at.isoformat() if state.started_at else None,
        total_posts_published=db_stats["total_posts_published"],
        total_topics_rejected=db_stats["total_topics_rejected"],
        cycle_interval_seconds=settings.cycle_interval_seconds,
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/agent/rejected  (bonus — high demo value)
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/rejected",
    response_model=RejectedFeedResponse,
    summary="Get all rejected topics",
    description=(
        "Returns all topics that were evaluated and rejected, newest first. "
        "Demonstrates the editorial judgment engine's real-time operation."
    ),
)
async def get_rejected(
    memory: MemoryManager = Depends(get_memory),
    limit: int = 200,
) -> RejectedFeedResponse:
    """Read-only: returns the rejection log from persistent storage."""
    rejected = await memory.get_rejected_feed(limit=limit)
    items = [
        RejectedTopicItem(
            id=r.id,
            cycle_id=r.cycle_id,
            topic=r.topic,
            rejection_reason=r.rejection_reason,
            scores=r.scores,
            source_url=r.source_url,
            rejected_at=r.rejected_at,
        )
        for r in rejected
    ]
    return RejectedFeedResponse(rejected_topics=items, total=len(items))
