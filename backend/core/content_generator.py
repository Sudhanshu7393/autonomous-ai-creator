"""
content_generator.py — Persona-grounded post generation.

Responsibilities:
  - Takes the selected topic + full persona config (every single call)
  - Generates a structured social-media post in ARIA's voice
  - Validates output structure and length before returning
  - Does NOT make editorial judgments — that is the Editorial Engine's job

Key design decisions:
  - Persona config is injected verbatim into every prompt — never relies on
    conversational memory or prior context for tone consistency.
  - Returns a structured GeneratedPost with post_text separated from metadata.
  - Validates that the post respects the persona's max_characters rule.
  - On generation failure, raises so the pipeline can log and skip the cycle
    rather than publishing malformed content.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass

from groq import Groq

from backend.config import get_settings
from backend.core.persona_manager import Persona
from backend.core.topic_discovery import DiscoveredTopic

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data types
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class GeneratedPost:
    post_text: str      # The final publishable post (within character limit)
    title: str          # Short editorial title for the feed UI
    hashtags: list[str] # Extracted hashtags
    char_count: int     # Actual character count of post_text


# ─────────────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
{voice_block}

---

YOUR TASK: Write one social-media post about the topic below, strictly following every rule above.

IMPORTANT CONSTRAINTS:
- The post_text field must be {max_chars} characters or fewer (count precisely).
- Include exactly {hashtag_count} hashtags at the end of the post_text.
- Never use any of these phrases: {forbidden}.
- Start with one of these openers (or a close variant): {openers}.
- Do NOT include the title in the post_text — keep it as a separate field.

OUTPUT FORMAT — respond ONLY with this JSON (no markdown fences, no extra text):
{{
  "title": "<short editorial title, 5–12 words>",
  "post_text": "<the complete post including hashtags, {max_chars} chars max>",
  "hashtags": ["#Tag1", "#Tag2", "#Tag3"]
}}
"""

_USER_PROMPT = """\
TOPIC: {title}
SUMMARY: {summary}
SOURCE: {source_name} — {source_url}
RECENCY: {recency_hint}

Write the post now. Remember: lead with insight, be specific, end with an implication or question.
"""


# ─────────────────────────────────────────────────────────────────────────────
# Generator
# ─────────────────────────────────────────────────────────────────────────────


class ContentGenerator:
    """
    Generates a persona-consistent social-media post for the selected topic.

    The full persona configuration is injected into every call so the AI
    never infers tone from context — personality cannot drift.
    """

    def __init__(self, persona: Persona) -> None:
        self._persona = persona
        self._settings = get_settings()
        self._client = Groq(api_key=self._settings.groq_api_key)

    async def generate(self, topic: DiscoveredTopic) -> GeneratedPost:
        """
        Generate and validate a post for `topic`.
        Raises RuntimeError if generation fails or output is malformed.
        """
        persona = self._persona
        logger.info("Generating post", extra={"topic": topic.title})

        system = _SYSTEM_PROMPT.format(
            voice_block=persona.voice_block(),
            max_chars=persona.post_max_characters,
            hashtag_count=persona.post_hashtag_count,
            forbidden=", ".join(f'"{p}"' for p in persona.forbidden_phrases),
            openers=", ".join(f'"{p}"' for p in persona.preferred_openers),
        )
        user = _USER_PROMPT.format(
            title=topic.title,
            summary=topic.summary,
            source_name=topic.source_name,
            source_url=topic.source_url,
            recency_hint=topic.recency_hint,
        )

        def _sync_call() -> str:
            model_to_use = self._settings.groq_model
            try:
                response = self._client.chat.completions.create(
                    model=model_to_use,
                    max_tokens=1024,
                    temperature=0.3,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user",   "content": user},
                    ],
                )
                return response.choices[0].message.content or ""
            except Exception as err:
                logger.warning(f"Content generation failed on {model_to_use}, falling back to llama-3.1-8b-instant: {err}")
                response = self._client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    max_tokens=1024,
                    temperature=0.3,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user",   "content": user},
                    ],
                )
                return response.choices[0].message.content or ""

        raw = await asyncio.to_thread(_sync_call)
        post = self._parse_and_validate(raw, topic)
        logger.info(
            "Post generated",
            extra={"topic": topic.title, "chars": post.char_count},
        )
        return post

    def _parse_and_validate(self, raw: str, topic: DiscoveredTopic) -> GeneratedPost:
        """Parse JSON response and validate the generated post."""
        raw = raw.strip()
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
        if fenced:
            raw = fenced.group(1).strip()
        start = raw.find("{")
        if start != -1:
            raw = raw[start:]

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Content generator returned invalid JSON: {exc}\nRaw: {raw[:300]}"
            ) from exc

        post_text = str(data.get("post_text", "")).strip()
        title = str(data.get("title", topic.title)).strip()
        hashtags = data.get("hashtags", [])
        if not isinstance(hashtags, list):
            hashtags = []
        hashtags = [str(h).strip() for h in hashtags if str(h).startswith("#")]

        if not post_text:
            raise RuntimeError("Content generator returned empty post_text")

        # Enforce character limit — truncate at last space before the limit
        max_chars = self._persona.post_max_characters
        if len(post_text) > max_chars:
            logger.warning(
                "Post exceeds character limit, truncating",
                extra={"original_len": len(post_text), "max": max_chars},
            )
            post_text = post_text[:max_chars].rsplit(" ", 1)[0] + "…"

        # Check for forbidden phrases
        lower = post_text.lower()
        for phrase in self._persona.forbidden_phrases:
            if phrase.lower() in lower:
                logger.warning(
                    "Forbidden phrase detected in generated post",
                    extra={"phrase": phrase, "topic": topic.title},
                )

        return GeneratedPost(
            post_text=post_text,
            title=title,
            hashtags=hashtags,
            char_count=len(post_text),
        )
