"""
editorial_engine.py — Multi-dimensional topic scoring and editorial decision making.

This is the intelligence core of the pipeline. It behaves like a professional
technology editor, not a simple scoring function.

Every candidate topic is evaluated on five independent dimensions:
  1. Relevance       — alignment with the persona's defined topic scope
  2. Novelty         — freshness and surprisingness relative to prior posts
  3. Technical Depth — substance beyond surface-level announcement
  4. Source Credibility — quality and trustworthiness of the origin
  5. Audience Value  — practical usefulness to the persona's target readers

Each dimension is scored 0–10 by Claude with a reasoning sentence.
A weighted composite score is computed per the persona's editorial_standards.

Duplicate detection compares against the FULL historical memory — no truncation.
Similarity is measured by token-overlap (no external embedding service required),
fast enough for thousands of stored topics.

Every decision (accept OR reject) carries a structured, human-readable rationale
that is stored permanently in the database.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from groq import Groq

from backend.config import get_settings
from backend.core.persona_manager import Persona
from backend.core.topic_discovery import DiscoveredTopic

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data types
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ScoreBreakdown:
    relevance: float
    novelty: float
    depth: float
    credibility: float
    audience_value: float
    composite: float

    # Per-dimension reasoning sentences from the LLM
    relevance_reason: str = ""
    novelty_reason: str = ""
    depth_reason: str = ""
    credibility_reason: str = ""
    audience_value_reason: str = ""
    overall_rationale: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "relevance": self.relevance,
            "novelty": self.novelty,
            "depth": self.depth,
            "credibility": self.credibility,
            "audience_value": self.audience_value,
            "composite": self.composite,
            "reasons": {
                "relevance": self.relevance_reason,
                "novelty": self.novelty_reason,
                "depth": self.depth_reason,
                "credibility": self.credibility_reason,
                "audience_value": self.audience_value_reason,
            },
        }


@dataclass
class EditorialDecision:
    topic: DiscoveredTopic
    accepted: bool
    scores: ScoreBreakdown
    rationale: str          # Single human-readable paragraph explaining the decision
    is_duplicate: bool = False
    duplicate_of: str = ""  # Title of the existing post it duplicates


# ─────────────────────────────────────────────────────────────────────────────
# Scoring prompt
# ─────────────────────────────────────────────────────────────────────────────

_SCORING_SYSTEM = """You are the editorial decision engine for {persona_name}, a {persona_role}.

Your job is to evaluate whether a candidate AI/technology topic meets the publication bar.

{voice_block}

SCORING CRITERIA — score each dimension 0.0 to 10.0 (one decimal):

1. RELEVANCE (0–10): Does this topic squarely match the persona's stated topic scope?
   - 9–10: Perfect fit, core topic area
   - 7–8: Good fit, clearly relevant
   - 5–6: Marginal, loosely related
   - 0–4: Out of scope or explicitly excluded

2. NOVELTY (0–10): How new and surprising is this relative to existing knowledge?
   - 9–10: Genuinely new development, not yet widely covered
   - 7–8: Fresh enough, adds something new
   - 5–6: Known area, incremental update
   - 0–4: Old news or entirely expected

3. TECHNICAL DEPTH (0–10): Does the topic have real substance?
   - 9–10: Specific benchmarks, architectural details, or concrete findings
   - 7–8: Clear technical claim, some supporting evidence
   - 5–6: Some substance, mostly high-level
   - 0–4: Vague announcement, no technical specifics

4. SOURCE CREDIBILITY (0–10): How trustworthy is the source?
   - 9–10: Top-tier: arxiv, Nature, Anthropic, OpenAI, DeepMind, MIT TR
   - 7–8: Reputable tech media: Ars Technica, TechCrunch, VentureBeat
   - 5–6: General media or company blog
   - 0–4: Unknown source, social media, no byline

5. AUDIENCE VALUE (0–10): How useful is this to {audience}?
   - 9–10: Directly actionable or changes how practitioners should work
   - 7–8: Important to know, informs decisions
   - 5–6: Interesting but not immediately useful
   - 0–4: Tangential or not relevant to practitioners

OUTPUT — respond ONLY with this JSON object (no prose, no markdown fences):
{{
  "relevance": <float>,
  "relevance_reason": "<one sentence>",
  "novelty": <float>,
  "novelty_reason": "<one sentence>",
  "depth": <float>,
  "depth_reason": "<one sentence>",
  "credibility": <float>,
  "credibility_reason": "<one sentence>",
  "audience_value": <float>,
  "audience_value_reason": "<one sentence>",
  "overall_rationale": "<2–3 sentences summarising the editorial decision and why>"
}}
"""

_SCORING_USER = """Evaluate this candidate topic:

TITLE: {title}
SUMMARY: {summary}
SOURCE: {source_name} ({source_url})
RECENCY: {recency_hint}
SEARCH SNIPPET: {snippet}

Score each dimension and explain the overall editorial decision."""


# ─────────────────────────────────────────────────────────────────────────────
# Duplicate detection helpers
# ─────────────────────────────────────────────────────────────────────────────

# Minimum token overlap ratio to flag as duplicate
_DUPLICATE_THRESHOLD = 0.55


def _tokenise(text: str) -> set[str]:
    """Simple whitespace + punctuation tokeniser, lowercase."""
    return set(re.sub(r"[^\w\s]", " ", text.lower()).split())


def _overlap_ratio(a: str, b: str) -> float:
    """Jaccard similarity between token sets of two strings."""
    tokens_a = _tokenise(a)
    tokens_b = _tokenise(b)
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def _is_duplicate(
    candidate_title: str,
    candidate_summary: str,
    historical_topics: list[str],
) -> tuple[bool, str]:
    """
    Check whether a candidate topic duplicates any historical post.
    Returns (is_duplicate, matched_topic_title).
    Compares against the FULL history — no truncation.
    """
    candidate_text = f"{candidate_title} {candidate_summary}"
    for historical_title in historical_topics:
        ratio = _overlap_ratio(candidate_text, historical_title)
        if ratio >= _DUPLICATE_THRESHOLD:
            logger.debug(
                "Duplicate detected",
                extra={
                    "candidate": candidate_title,
                    "matched": historical_title,
                    "ratio": ratio,
                },
            )
            return True, historical_title
    return False, ""


# ─────────────────────────────────────────────────────────────────────────────
# Editorial Decision Engine
# ─────────────────────────────────────────────────────────────────────────────


class EditorialDecisionEngine:
    """
    Scores candidate topics and makes accept/reject decisions.

    Each decision:
    - Scores on 5 dimensions via Claude
    - Checks full-history deduplication via token overlap
    - Applies persona-configured score thresholds
    - Produces a human-readable rationale for every decision
    """

    def __init__(self, persona: Persona) -> None:
        self._persona = persona
        self._settings = get_settings()
        self._client = Groq(api_key=self._settings.groq_api_key)

    async def evaluate_all(
        self,
        candidates: list[DiscoveredTopic],
        historical_topics: list[str],
    ) -> list[EditorialDecision]:
        """
        Evaluate candidates sequentially with a small delay between each call.
        Sequential (not parallel) to respect Groq free-tier TPM rate limits.
        """
        if not candidates:
            return []

        results: list[EditorialDecision] = []
        for i, topic in enumerate(candidates):
            # Small delay between calls to stay within Groq's 12K TPM limit
            if i > 0:
                await asyncio.sleep(6)
            try:
                decision = await self._evaluate_one(topic, historical_topics)
                results.append(decision)
            except Exception as exc:
                logger.error(
                    "Scoring failed for topic",
                    extra={"topic": topic.title, "error": str(exc)},
                )
                results.append(
                    EditorialDecision(
                        topic=topic,
                        accepted=False,
                        scores=_zero_scores(),
                        rationale=f"Scoring failed due to an API error: {exc}",
                    )
                )

        return results

    async def _evaluate_one(
        self,
        topic: DiscoveredTopic,
        historical_topics: list[str],
    ) -> EditorialDecision:
        """Score one topic and return a decision with full rationale."""
        persona = self._persona

        # ── Step 1: Duplicate check (fast, no LLM needed) ─────────────────
        is_dup, dup_of = _is_duplicate(
            topic.title, topic.summary, historical_topics
        )
        if is_dup:
            rationale = (
                f"REJECTED — Duplicate: This topic substantially overlaps with a "
                f"previously published post titled '{dup_of}'. "
                f"Publishing duplicate content would undermine audience trust."
            )
            logger.info(
                "Topic rejected (duplicate)",
                extra={"topic": topic.title, "duplicate_of": dup_of},
            )
            return EditorialDecision(
                topic=topic,
                accepted=False,
                scores=_zero_scores(),
                rationale=rationale,
                is_duplicate=True,
                duplicate_of=dup_of,
            )

        # ── Step 2: LLM scoring ───────────────────────────────────────────
        scores = await self._score_with_llm(topic)

        # ── Step 3: Apply editorial bar ───────────────────────────────────
        min_score = self._settings.min_relevance_score
        below_bar = scores.composite < min_score
        below_novelty = scores.novelty < persona.min_novelty_score
        below_depth = scores.depth < persona.min_depth_score
        below_credibility = scores.credibility < persona.min_credibility_score

        accepted = not (below_bar or below_novelty or below_depth or below_credibility)

        if accepted:
            rationale = (
                f"ACCEPTED — Composite score {scores.composite:.1f}/10. "
                f"{scores.overall_rationale}"
            )
            logger.info(
                "Topic accepted",
                extra={
                    "topic": topic.title,
                    "composite": scores.composite,
                },
            )
        else:
            reasons = []
            if below_bar:
                reasons.append(
                    f"composite score {scores.composite:.1f} is below the bar of {min_score}"
                )
            if below_novelty:
                reasons.append(f"novelty score {scores.novelty:.1f} < minimum {persona.min_novelty_score}")
            if below_depth:
                reasons.append(f"depth score {scores.depth:.1f} < minimum {persona.min_depth_score}")
            if below_credibility:
                reasons.append(f"credibility score {scores.credibility:.1f} < minimum {persona.min_credibility_score}")

            rationale = (
                f"REJECTED — {'; '.join(reasons).capitalize()}. "
                f"{scores.overall_rationale}"
            )
            logger.info(
                "Topic rejected (below bar)",
                extra={
                    "topic": topic.title,
                    "composite": scores.composite,
                    "reasons": reasons,
                },
            )

        return EditorialDecision(
            topic=topic,
            accepted=accepted,
            scores=scores,
            rationale=rationale,
        )

    async def _score_with_llm(self, topic: DiscoveredTopic) -> "ScoreBreakdown":
        """Call Claude to score this topic on all five dimensions."""
        persona = self._persona
        system = _SCORING_SYSTEM.format(
            persona_name=persona.name,
            persona_role=persona.role,
            voice_block=persona.voice_block(),
            audience=persona.audience_primary,
        )
        user = _SCORING_USER.format(
            title=topic.title,
            summary=topic.summary,
            source_name=topic.source_name,
            source_url=topic.source_url,
            recency_hint=topic.recency_hint,
            snippet=topic.raw_search_snippet[:500],
        )

        def _sync_call() -> str:
            response = self._client.chat.completions.create(
                model=self._settings.groq_model,
                max_tokens=1024,
                temperature=0.1,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            return response.choices[0].message.content or ""

        raw = await asyncio.to_thread(_sync_call)
        return self._parse_scores(raw, topic.title)

    def _parse_scores(self, raw: str, topic_title: str) -> "ScoreBreakdown":
        """Parse the JSON scoring response from Claude."""
        raw = raw.strip()
        # Strip markdown fences
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
        if fenced:
            raw = fenced.group(1).strip()
        start = raw.find("{")
        if start != -1:
            raw = raw[start:]

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(
                "Could not parse scoring JSON; using fallback zeros",
                extra={"topic": topic_title},
            )
            return _zero_scores()

        def _clamp(v: Any, default: float = 5.0) -> float:
            try:
                return max(0.0, min(10.0, float(v)))
            except (TypeError, ValueError):
                return default

        weights = self._persona.editorial_scoring_weights
        relevance = _clamp(data.get("relevance"))
        novelty = _clamp(data.get("novelty"))
        depth = _clamp(data.get("depth"))
        credibility = _clamp(data.get("credibility"))
        audience_value = _clamp(data.get("audience_value"))

        composite = (
            relevance * weights.get("relevance", 0.25)
            + novelty * weights.get("novelty", 0.25)
            + depth * weights.get("depth", 0.20)
            + credibility * weights.get("credibility", 0.15)
            + audience_value * weights.get("audience_value", 0.15)
        )

        overall_rationale = str(data.get("overall_rationale", "")).strip()
        if not overall_rationale:
            overall_rationale = "No rationale provided."

        return ScoreBreakdown(
            relevance=relevance,
            novelty=novelty,
            depth=depth,
            credibility=credibility,
            audience_value=audience_value,
            composite=round(composite, 2),
            relevance_reason=str(data.get("relevance_reason", "")),
            novelty_reason=str(data.get("novelty_reason", "")),
            depth_reason=str(data.get("depth_reason", "")),
            credibility_reason=str(data.get("credibility_reason", "")),
            audience_value_reason=str(data.get("audience_value_reason", "")),
            overall_rationale=str(data.get("overall_rationale", "")).strip(),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _zero_scores() -> ScoreBreakdown:
    return ScoreBreakdown(
        relevance=0.0, novelty=0.0, depth=0.0,
        credibility=0.0, audience_value=0.0, composite=0.0,
        overall_rationale="",
    )
