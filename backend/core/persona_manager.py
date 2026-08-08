"""
persona_manager.py — Loads, validates, and supplies persona configuration.

The PersonaManager is instantiated once at startup and injected into both
the EditorialDecisionEngine and the ContentGenerator so that the persona
is never allowed to drift across pipeline cycles.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PERSONA_PATH = Path(__file__).parent.parent / "persona.json"


@dataclass(frozen=True)
class Persona:
    """
    Immutable snapshot of the loaded persona configuration.

    Passed verbatim into every ContentGenerator and EditorialEngine call
    so the AI never infers tone from conversational context.
    """

    name: str
    full_name: str
    role: str
    tagline: str
    audience_primary: str
    audience_secondary: str
    audience_assumption: str
    tone: str
    style: str
    stance: str
    forbidden_phrases: list[str]
    preferred_openers: list[str]
    voice_rules: list[str]
    post_max_characters: int
    post_structure: str
    post_hashtag_count: int
    post_emoji_policy: str
    include_topics: list[str]
    exclude_topics: list[str]
    editorial_scoring_weights: dict[str, float]
    min_novelty_score: float
    min_depth_score: float
    min_credibility_score: float

    # ── Serialisation helpers ─────────────────────────────────────────────

    def as_dict(self) -> dict[str, Any]:
        """Return the full persona as a plain dict (for JSON serialisation)."""
        import dataclasses
        return dataclasses.asdict(self)

    def voice_block(self) -> str:
        """
        Return a compact, human-readable block of all voice rules
        ready to be embedded into a prompt.
        """
        lines = [
            f"PERSONA: {self.full_name} ({self.name}) — {self.role}",
            f"TAGLINE: {self.tagline}",
            f"AUDIENCE: {self.audience_primary}",
            f"AUDIENCE ASSUMPTION: {self.audience_assumption}",
            "",
            f"TONE: {self.tone}",
            f"STYLE: {self.style}",
            f"STANCE: {self.stance}",
            "",
            "VOICE RULES (apply every single time):",
        ]
        for i, rule in enumerate(self.voice_rules, 1):
            lines.append(f"  {i}. {rule}")
        lines += [
            "",
            f"POST FORMAT: {self.post_structure}",
            f"MAX CHARACTERS: {self.post_max_characters}",
            f"HASHTAGS: exactly {self.post_hashtag_count} specific hashtags",
            f"EMOJI: {self.post_emoji_policy}",
            "",
            "FORBIDDEN PHRASES (never use):",
            "  " + ", ".join(f'"{p}"' for p in self.forbidden_phrases),
            "",
            "PREFERRED OPENERS (use one to start):",
            "  " + ", ".join(f'"{p}"' for p in self.preferred_openers),
        ]
        return "\n".join(lines)


class PersonaManager:
    """
    Loads and validates persona.json at startup.
    Provides a single Persona instance for the entire application lifetime.
    """

    def __init__(self, path: Path = _PERSONA_PATH) -> None:
        self._path = path
        self._persona: Persona | None = None

    def load(self) -> Persona:
        """Load and parse persona.json. Raises on any validation failure."""
        if self._persona is not None:
            return self._persona

        if not self._path.exists():
            raise FileNotFoundError(f"Persona file not found: {self._path}")

        with open(self._path, encoding="utf-8") as fh:
            raw: dict[str, Any] = json.load(fh)

        try:
            persona = Persona(
                name=raw["name"],
                full_name=raw["full_name"],
                role=raw["role"],
                tagline=raw["tagline"],
                audience_primary=raw["audience"]["primary"],
                audience_secondary=raw["audience"]["secondary"],
                audience_assumption=raw["audience"]["assumption"],
                tone=raw["personality"]["tone"],
                style=raw["personality"]["style"],
                stance=raw["personality"]["stance"],
                forbidden_phrases=raw["personality"]["forbidden_phrases"],
                preferred_openers=raw["personality"]["preferred_openers"],
                voice_rules=raw["voice_rules"],
                post_max_characters=raw["post_format"]["max_characters"],
                post_structure=raw["post_format"]["structure"],
                post_hashtag_count=raw["post_format"]["hashtag_count"],
                post_emoji_policy=raw["post_format"]["emoji_policy"],
                include_topics=raw["topic_scope"]["include"],
                exclude_topics=raw["topic_scope"]["exclude"],
                editorial_scoring_weights=raw["editorial_standards"]["scoring_weights"],
                min_novelty_score=raw["editorial_standards"]["min_novelty_score"],
                min_depth_score=raw["editorial_standards"]["min_depth_score"],
                min_credibility_score=raw["editorial_standards"]["min_credibility_score"],
            )
        except KeyError as exc:
            raise ValueError(f"Persona JSON missing required field: {exc}") from exc

        self._persona = persona
        logger.info(
            "Persona loaded",
            extra={"persona_name": persona.name, "role": persona.role},
        )
        return persona

    @property
    def persona(self) -> Persona:
        if self._persona is None:
            return self.load()
        return self._persona
