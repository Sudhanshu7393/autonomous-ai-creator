"""
scheduler.py — Autonomous asyncio background loop.

The Scheduler is the heart of the autonomous operation.
After a single /init call starts it, it runs indefinitely without
any further human interaction.

Key behaviours:
  - Runs as a genuine asyncio background task, completely decoupled from
    any API request lifecycle.
  - Configurable interval via CYCLE_INTERVAL_SECONDS (default 90 s for demos,
    7200–10800 s for production simulation of a 48-hour autonomous run).
  - Each cycle is wrapped in a try/except. Any exception — network failure,
    invalid AI response, database error — is logged and the loop continues.
    ONE failed cycle NEVER stops the scheduler.
  - Exposes a typed SchedulerState for the /status endpoint to read.
  - Idempotent start: calling start() when already running is a no-op.
  - Graceful shutdown: stop() signals the loop to exit after the current
    cycle completes.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from backend.config import get_settings
from backend.core.memory_manager import MemoryManager
from backend.core.persona_manager import PersonaManager
from backend.core.pipeline import AutonomousPipeline

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Shared state (read by /status endpoint)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SchedulerState:
    is_running: bool = False
    cycles_completed: int = 0
    cycles_failed: int = 0
    next_cycle_at: datetime | None = None
    last_cycle_at: datetime | None = None
    last_cycle_topic: str | None = None
    started_at: datetime | None = None
    agent_name: str = "ARIA"
    last_heartbeat_at: datetime | None = None

    def seconds_until_next(self) -> float:
        from backend.config import get_settings
        settings = get_settings()
        interval = float(settings.cycle_interval_seconds)

        if self.next_cycle_at is not None:
            delta = (self.next_cycle_at - datetime.now(timezone.utc)).total_seconds()
            if delta > 0:
                return delta

        if self.last_cycle_at is not None:
            delta = (self.last_cycle_at + timedelta(seconds=interval) - datetime.now(timezone.utc)).total_seconds()
            if delta > 0:
                return delta

        return interval


# Singleton state object shared with the API layer
_state = SchedulerState()


def get_state() -> SchedulerState:
    return _state


# ─────────────────────────────────────────────────────────────────────────────
# Scheduler
# ─────────────────────────────────────────────────────────────────────────────


class Scheduler:
    """
    Autonomous asyncio background loop.

    Usage:
        scheduler = Scheduler(memory, persona_manager)
        scheduler.start()   # non-blocking; call once from /init
        scheduler.stop()    # signals graceful shutdown
    """

    def __init__(
        self,
        memory: MemoryManager,
        persona_manager: PersonaManager,
    ) -> None:
        self._memory = memory
        self._persona_manager = persona_manager
        self._pipeline = AutonomousPipeline(memory, persona_manager)
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._settings = get_settings()

    def start(self) -> bool:
        """
        Start the autonomous loop as a background asyncio task.
        Returns True if started, False if already running (idempotent).
        """
        if _state.is_running:
            logger.info("Scheduler already running — ignoring duplicate start()")
            return False

        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._loop(), name="autonomous-pipeline-loop"
        )
        _state.is_running = True
        _state.started_at = datetime.now(timezone.utc)
        _state.last_heartbeat_at = datetime.now(timezone.utc)
        _state.agent_name = self._persona_manager.persona.name

        logger.info(
            "Scheduler started",
            extra={
                "agent": _state.agent_name,
                "interval_seconds": self._settings.cycle_interval_seconds,
            },
        )
        return True

    def stop(self) -> None:
        """Signal the background loop to stop gracefully."""
        self._stop_event.set()
        _state.is_running = False
        logger.info("Scheduler stop requested")

    async def trigger_immediate_cycle(self) -> None:
        """Trigger a single cycle execution immediately."""
        logger.info("Manual immediate cycle triggered")
        await self._run_one_cycle()
        _state.next_cycle_at = datetime.now(timezone.utc) + timedelta(seconds=self._settings.cycle_interval_seconds)

    async def _loop(self) -> None:
        """
        Main autonomous execution loop.
        Runs every `interval` seconds, stopping cleanly on `_stop_event`.
        Auto-pauses if no visitor heartbeat received for >45 seconds.
        """
        settings = get_settings()
        interval = float(settings.cycle_interval_seconds)
        logger.info(
            "Autonomous loop entering",
            extra={"interval": interval, "persona": _state.agent_name},
        )

        # Run the first cycle immediately without waiting
        await self._run_one_cycle()

        while not self._stop_event.is_set():
            # Schedule the next cycle
            next_at = datetime.now(timezone.utc).replace(microsecond=0)
            next_at = datetime.now(timezone.utc) + timedelta(seconds=interval)
            _state.next_cycle_at = next_at

            logger.info(
                "Waiting for next cycle",
                extra={
                    "next_in_seconds": interval,
                    "next_at": next_at.isoformat(),
                },
            )

            # Wait with short ticks so stop_event & heartbeats are checked frequently
            elapsed = 0.0
            tick = 5.0  # seconds per tick
            while elapsed < interval and not self._stop_event.is_set():
                await asyncio.sleep(min(tick, interval - elapsed))
                elapsed += tick

                # Check visitor heartbeat inactivity — auto-pause after 45s of no visitors
                if _state.last_heartbeat_at is not None:
                    idle = (datetime.now(timezone.utc) - _state.last_heartbeat_at).total_seconds()
                    if idle > 45.0:
                        logger.info("No active website visitors for >45s — auto-pausing background agent to save API limits")
                        self._stop_event.set()
                        break

            if self._stop_event.is_set():
                break

            await self._run_one_cycle()

        _state.is_running = False
        logger.info("Autonomous loop exited cleanly")

    async def _run_one_cycle(self) -> None:
        """
        Run one pipeline cycle, catching ALL exceptions.
        A single failed cycle NEVER stops the loop.
        """
        logger.info(
            "Cycle starting",
            extra={"cycle_number": _state.cycles_completed + 1},
        )
        try:
            result = await self._pipeline.run_cycle()
            _state.cycles_completed += 1
            _state.last_cycle_at = datetime.now(timezone.utc)
            if result.published_topic:
                _state.last_cycle_topic = result.published_topic
            logger.info(
                "Cycle succeeded",
                extra={
                    "cycle": _state.cycles_completed,
                    "published": result.post_published,
                    "topic": result.published_topic,
                },
            )
        except Exception as exc:  # pylint: disable=broad-except
            _state.cycles_failed += 1
            _state.last_cycle_at = datetime.now(timezone.utc)
            logger.error(
                "Cycle failed — loop continues",
                extra={
                    "error": str(exc),
                    "cycles_failed": _state.cycles_failed,
                },
                exc_info=True,
            )
