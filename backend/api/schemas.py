"""
schemas.py — Pydantic response models for the API layer.

All data flowing out of the API is typed and validated here.
The API layer never returns raw dict objects.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# /api/agent/init
# ─────────────────────────────────────────────────────────────────────────────


class InitResponse(BaseModel):
    status: str = Field(..., examples=["initialized"])
    agent: str = Field(..., examples=["ARIA"])
    message: str = Field(..., examples=["Autonomous pipeline started."])


# ─────────────────────────────────────────────────────────────────────────────
# /api/agent/status
# ─────────────────────────────────────────────────────────────────────────────


class StatusResponse(BaseModel):
    is_running: bool
    agent: str
    cycles_completed: int
    cycles_failed: int
    next_cycle_in_seconds: float
    last_cycle_at: str | None
    last_cycle_topic: str | None
    started_at: str | None
    total_posts_published: int
    total_topics_rejected: int
    cycle_interval_seconds: int


# ─────────────────────────────────────────────────────────────────────────────
# /api/agent/feed
# ─────────────────────────────────────────────────────────────────────────────


class ScoreDetail(BaseModel):
    relevance: float
    novelty: float
    depth: float
    credibility: float
    audience_value: float
    composite: float


class PostItem(BaseModel):
    id: int
    cycle_id: str
    topic: str
    post_text: str
    rationale: str
    relevance_score: float
    novelty_score: float
    depth_score: float
    composite_score: float
    source_url: str
    timestamp: str  # ISO-8601 UTC


class FeedResponse(BaseModel):
    posts: list[PostItem]
    total: int


# ─────────────────────────────────────────────────────────────────────────────
# /api/agent/rejected
# ─────────────────────────────────────────────────────────────────────────────


class RejectedTopicItem(BaseModel):
    id: int
    cycle_id: str
    topic: str
    rejection_reason: str
    scores: dict[str, Any]
    source_url: str | None
    rejected_at: str


class RejectedFeedResponse(BaseModel):
    rejected_topics: list[RejectedTopicItem]
    total: int
