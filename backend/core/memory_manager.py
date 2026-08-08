"""
memory_manager.py — Persistent SQLite layer.

Responsibilities:
  - Database initialisation (schema creation, migrations)
  - Writing published posts
  - Writing rejected topic records
  - Writing cycle execution records
  - Serving duplicate-check queries to the Editorial Engine
  - Serving the read-only feed API

Uses aiosqlite for non-blocking async I/O so database operations
never block the FastAPI event loop.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from backend.config import get_settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Dataclasses (lightweight, no ORM overhead)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class PostRecord:
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
    published_at: str


@dataclass
class RejectedTopicRecord:
    id: int
    cycle_id: str
    topic: str
    rejection_reason: str
    scores: dict[str, Any]
    source_url: str | None
    rejected_at: str


@dataclass
class CycleRecord:
    id: int
    cycle_id: str
    started_at: str
    completed_at: str | None
    status: str
    topics_discovered: int
    topics_rejected: int
    post_published: int
    error_message: str | None


# ─────────────────────────────────────────────────────────────────────────────
# DDL
# ─────────────────────────────────────────────────────────────────────────────

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS posts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id        TEXT    NOT NULL,
    topic           TEXT    NOT NULL,
    post_text       TEXT    NOT NULL,
    rationale       TEXT    NOT NULL,
    relevance_score REAL    NOT NULL,
    novelty_score   REAL    NOT NULL,
    depth_score     REAL    NOT NULL,
    composite_score REAL    NOT NULL,
    source_url      TEXT    NOT NULL,
    published_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS rejected_topics (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id         TEXT    NOT NULL,
    topic            TEXT    NOT NULL,
    rejection_reason TEXT    NOT NULL,
    scores           TEXT    NOT NULL,
    source_url       TEXT,
    rejected_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS cycles (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id          TEXT    NOT NULL UNIQUE,
    started_at        TEXT    NOT NULL,
    completed_at      TEXT,
    status            TEXT    NOT NULL DEFAULT 'running',
    topics_discovered INTEGER NOT NULL DEFAULT 0,
    topics_rejected   INTEGER NOT NULL DEFAULT 0,
    post_published    INTEGER NOT NULL DEFAULT 0,
    error_message     TEXT
);

CREATE INDEX IF NOT EXISTS idx_posts_published_at    ON posts (published_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_composite_score ON posts (composite_score DESC);
CREATE INDEX IF NOT EXISTS idx_rejected_cycle        ON rejected_topics (cycle_id);
CREATE INDEX IF NOT EXISTS idx_cycles_started        ON cycles (started_at DESC);
"""


# ─────────────────────────────────────────────────────────────────────────────
# Manager
# ─────────────────────────────────────────────────────────────────────────────


class MemoryManager:
    """
    Async SQLite persistence layer.

    Call `await MemoryManager.create()` to get an initialised instance.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @classmethod
    async def create(cls) -> "MemoryManager":
        settings = get_settings()
        db_path = settings.db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        instance = cls(db_path)
        await instance._initialise_schema()
        logger.info("MemoryManager initialised", extra={"db_path": db_path})
        return instance

    async def _initialise_schema(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_DDL)
            await db.commit()

    # ── Cycle records ─────────────────────────────────────────────────────────

    async def start_cycle(self, cycle_id: str) -> None:
        now = _utcnow()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO cycles (cycle_id, started_at, status) VALUES (?, ?, 'running')",
                (cycle_id, now),
            )
            await db.commit()
        logger.debug("Cycle started", extra={"cycle_id": cycle_id})

    async def complete_cycle(
        self,
        cycle_id: str,
        *,
        topics_discovered: int,
        topics_rejected: int,
        post_published: int,
    ) -> None:
        now = _utcnow()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """UPDATE cycles
                   SET completed_at=?, status='completed',
                       topics_discovered=?, topics_rejected=?, post_published=?
                   WHERE cycle_id=?""",
                (now, topics_discovered, topics_rejected, post_published, cycle_id),
            )
            await db.commit()
        logger.debug("Cycle completed", extra={"cycle_id": cycle_id})

    async def fail_cycle(self, cycle_id: str, error: str) -> None:
        now = _utcnow()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """UPDATE cycles
                   SET completed_at=?, status='failed', error_message=?
                   WHERE cycle_id=?""",
                (now, error[:2000], cycle_id),
            )
            await db.commit()
        logger.warning("Cycle failed", extra={"cycle_id": cycle_id, "error": error})

    # ── Writing posts ─────────────────────────────────────────────────────────

    async def save_post(
        self,
        *,
        cycle_id: str,
        topic: str,
        post_text: str,
        rationale: str,
        relevance_score: float,
        novelty_score: float,
        depth_score: float,
        composite_score: float,
        source_url: str,
    ) -> int:
        now = _utcnow()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                """INSERT INTO posts
                   (cycle_id, topic, post_text, rationale,
                    relevance_score, novelty_score, depth_score,
                    composite_score, source_url, published_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    cycle_id, topic, post_text, rationale,
                    relevance_score, novelty_score, depth_score,
                    composite_score, source_url, now,
                ),
            )
            await db.commit()
            post_id = cursor.lastrowid
        logger.info(
            "Post saved",
            extra={"post_id": post_id, "topic": topic, "score": composite_score},
        )
        return post_id

    # ── Writing rejected topics ───────────────────────────────────────────────

    async def save_rejected_topic(
        self,
        *,
        cycle_id: str,
        topic: str,
        rejection_reason: str,
        scores: dict[str, Any],
        source_url: str | None = None,
    ) -> None:
        now = _utcnow()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT INTO rejected_topics
                   (cycle_id, topic, rejection_reason, scores, source_url, rejected_at)
                   VALUES (?,?,?,?,?,?)""",
                (cycle_id, topic, rejection_reason, json.dumps(scores), source_url, now),
            )
            await db.commit()
        logger.info(
            "Rejected topic logged",
            extra={"topic": topic, "reason": rejection_reason},
        )

    # ── Duplicate detection ───────────────────────────────────────────────────

    async def get_all_published_topics(self) -> list[str]:
        """
        Return every topic string ever published.
        Used by the Editorial Engine for full-history duplicate detection.
        NEVER truncated — always returns the complete set.
        """
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute("SELECT topic FROM posts ORDER BY published_at DESC")
            rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def get_all_rejected_topics(self) -> list[str]:
        """Return every previously rejected topic string."""
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute("SELECT topic FROM rejected_topics")
            rows = await cursor.fetchall()
        return [row[0] for row in rows]

    # ── Feed (read-only) ──────────────────────────────────────────────────────

    async def get_feed(self, limit: int = 100) -> list[PostRecord]:
        """Return published posts, newest first. NEVER triggers generation."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM posts ORDER BY published_at DESC LIMIT ?""",
                (limit,),
            )
            rows = await cursor.fetchall()
        return [_row_to_post(r) for r in rows]

    async def get_rejected_feed(self, limit: int = 200) -> list[RejectedTopicRecord]:
        """Return rejected topics, newest first."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM rejected_topics ORDER BY rejected_at DESC LIMIT ?""",
                (limit,),
            )
            rows = await cursor.fetchall()
        return [_row_to_rejected(r) for r in rows]

    async def get_cycle_stats(self) -> dict[str, Any]:
        """Aggregated stats for /status endpoint."""
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM cycles WHERE status='completed'"
            )
            completed = (await cur.fetchone())[0]

            cur = await db.execute("SELECT COUNT(*) FROM posts")
            total_posts = (await cur.fetchone())[0]

            cur = await db.execute("SELECT COUNT(*) FROM rejected_topics")
            total_rejected = (await cur.fetchone())[0]

            cur = await db.execute(
                "SELECT started_at FROM cycles ORDER BY started_at DESC LIMIT 1"
            )
            row = await cur.fetchone()
            last_cycle_at = row[0] if row else None

        return {
            "cycles_completed": completed,
            "total_posts_published": total_posts,
            "total_topics_rejected": total_rejected,
            "last_cycle_at": last_cycle_at,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_post(row: aiosqlite.Row) -> PostRecord:
    return PostRecord(
        id=row["id"],
        cycle_id=row["cycle_id"],
        topic=row["topic"],
        post_text=row["post_text"],
        rationale=row["rationale"],
        relevance_score=row["relevance_score"],
        novelty_score=row["novelty_score"],
        depth_score=row["depth_score"],
        composite_score=row["composite_score"],
        source_url=row["source_url"],
        published_at=row["published_at"],
    )


def _row_to_rejected(row: aiosqlite.Row) -> RejectedTopicRecord:
    return RejectedTopicRecord(
        id=row["id"],
        cycle_id=row["cycle_id"],
        topic=row["topic"],
        rejection_reason=row["rejection_reason"],
        scores=json.loads(row["scores"]),
        source_url=row["source_url"],
        rejected_at=row["rejected_at"],
    )
