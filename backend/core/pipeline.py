"""
pipeline.py — Orchestrates one complete autonomous cycle.

A "cycle" is the full pipeline from topic discovery to post persistence.
The pipeline is stateless — it receives all dependencies as arguments
and has no awareness of the scheduler's interval or timing.

Each cycle:
  1. Opens a cycle record in the database (status=running)
  2. Discovers fresh topics via the TopicDiscoveryService
  3. Retrieves full historical topic list from MemoryManager
  4. Scores and filters all candidates via the EditorialDecisionEngine
  5. Persists ALL rejected topics with their rationales
  6. If no topic passes the bar, exits cleanly (logged as no-op cycle)
  7. Selects the single highest-scoring accepted topic
  8. Generates a post via ContentGenerator
  9. Persists the post to the database
  10. Marks the cycle as completed

The pipeline does NOT handle its own exceptions — the Scheduler wraps
each pipeline run in try/except so one failure never stops the loop.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from backend.core.content_generator import ContentGenerator
from backend.core.editorial_engine import EditorialDecision, EditorialDecisionEngine
from backend.core.memory_manager import MemoryManager
from backend.core.persona_manager import Persona, PersonaManager
from backend.core.topic_discovery import DiscoveredTopic, TopicDiscoveryService

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Result type
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class CycleResult:
    cycle_id: str
    topics_discovered: int
    topics_rejected: int
    post_published: bool
    published_topic: str | None = None
    published_post_id: int | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────────────────────


class AutonomousPipeline:
    """
    Orchestrates one full autonomous cycle.
    Instantiate once; call run_cycle() repeatedly.
    """

    def __init__(
        self,
        memory: MemoryManager,
        persona_manager: PersonaManager,
    ) -> None:
        self._memory = memory
        self._persona_manager = persona_manager

    async def run_cycle(self) -> CycleResult:
        """Execute one complete pipeline cycle. Raises on unrecoverable error."""
        cycle_id = str(uuid.uuid4())
        persona: Persona = self._persona_manager.persona

        logger.info("=" * 60)
        logger.info("Pipeline cycle starting", extra={"cycle_id": cycle_id})

        # ── Record cycle start ─────────────────────────────────────────────
        await self._memory.start_cycle(cycle_id)

        # ── Step 1: Topic Discovery ────────────────────────────────────────
        discovery_service = TopicDiscoveryService(persona)
        candidates: list[DiscoveredTopic] = await discovery_service.discover()

        logger.info(
            "Discovery complete",
            extra={"cycle_id": cycle_id, "candidates": len(candidates)},
        )

        if not candidates:
            logger.warning(
                "No topics discovered this cycle — skipping generation",
                extra={"cycle_id": cycle_id},
            )
            await self._memory.complete_cycle(
                cycle_id,
                topics_discovered=0,
                topics_rejected=0,
                post_published=0,
            )
            return CycleResult(
                cycle_id=cycle_id,
                topics_discovered=0,
                topics_rejected=0,
                post_published=False,
            )

        # ── Step 2: Retrieve full historical context ───────────────────────
        historical_topics: list[str] = await self._memory.get_all_published_topics()
        logger.info(
            "Historical context loaded",
            extra={"cycle_id": cycle_id, "history_size": len(historical_topics)},
        )

        # ── Step 3: Editorial scoring ──────────────────────────────────────
        engine = EditorialDecisionEngine(persona)
        decisions: list[EditorialDecision] = await engine.evaluate_all(
            candidates, historical_topics
        )

        accepted = [d for d in decisions if d.accepted]
        rejected = [d for d in decisions if not d.accepted]

        logger.info(
            "Editorial decisions made",
            extra={
                "cycle_id": cycle_id,
                "accepted": len(accepted),
                "rejected": len(rejected),
            },
        )

        # ── Step 4: Persist all rejected topics ───────────────────────────
        for decision in rejected:
            await self._memory.save_rejected_topic(
                cycle_id=cycle_id,
                topic=decision.topic.title,
                rejection_reason=decision.rationale,
                scores=decision.scores.as_dict(),
                source_url=decision.topic.source_url,
            )

        if not accepted:
            logger.warning(
                "No topics passed the editorial bar this cycle",
                extra={"cycle_id": cycle_id, "rejected": len(rejected)},
            )
            await self._memory.complete_cycle(
                cycle_id,
                topics_discovered=len(candidates),
                topics_rejected=len(rejected),
                post_published=0,
            )
            return CycleResult(
                cycle_id=cycle_id,
                topics_discovered=len(candidates),
                topics_rejected=len(rejected),
                post_published=False,
            )

        # ── Step 5: Select best topic ──────────────────────────────────────
        best: EditorialDecision = max(accepted, key=lambda d: d.scores.composite)
        logger.info(
            "Selected best topic",
            extra={
                "cycle_id": cycle_id,
                "topic": best.topic.title,
                "score": best.scores.composite,
            },
        )

        # ── Step 6: Generate post ──────────────────────────────────────────
        generator = ContentGenerator(persona)
        generated = await generator.generate(best.topic)

        # ── Step 7: Persist post ───────────────────────────────────────────
        post_id = await self._memory.save_post(
            cycle_id=cycle_id,
            topic=best.topic.title,
            post_text=generated.post_text,
            rationale=best.rationale,
            relevance_score=best.scores.relevance,
            novelty_score=best.scores.novelty,
            depth_score=best.scores.depth,
            composite_score=best.scores.composite,
            source_url=best.topic.source_url,
        )

        await self._memory.complete_cycle(
            cycle_id,
            topics_discovered=len(candidates),
            topics_rejected=len(rejected),
            post_published=1,
        )

        logger.info(
            "Cycle complete — post published",
            extra={
                "cycle_id": cycle_id,
                "post_id": post_id,
                "topic": best.topic.title,
                "char_count": generated.char_count,
            },
        )
        logger.info("=" * 60)

        return CycleResult(
            cycle_id=cycle_id,
            topics_discovered=len(candidates),
            topics_rejected=len(rejected),
            post_published=True,
            published_topic=best.topic.title,
            published_post_id=post_id,
        )
