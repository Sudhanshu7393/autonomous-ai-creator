"""
topic_discovery.py — Real web search via Tavily + Groq for structuring.

Tavily is purpose-built for AI agents:
  - Returns clean, structured results (no scraping/parsing needed)
  - Accurate date filtering (last 24h / last week)
  - Includes full content snippets, not just titles
  - 1000 free searches/month

Flow:
  1. Tavily search for multiple AI/tech queries → clean structured results
  2. Groq filters, deduplicates, and structures into DiscoveredTopic objects
  3. Local deduplication by URL and title
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from groq import Groq
from tavily import TavilyClient

from backend.config import get_settings
from backend.core.persona_manager import Persona

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data types
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class DiscoveredTopic:
    """A single candidate topic produced by the discovery stage."""
    title: str
    summary: str
    source_url: str
    source_name: str
    recency_hint: str
    raw_search_snippet: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Search queries — multiple focused queries for better coverage
# ─────────────────────────────────────────────────────────────────────────────

_SEARCH_QUERIES = [
    "latest artificial intelligence AI news today 2026",
    "new LLM large language model release benchmark 2026",
    "AI agent autonomous system enterprise breakthrough 2026",
    "open source AI model release announced 2026",
    "AI infrastructure chip GPU tooling news 2026",
]

# ─────────────────────────────────────────────────────────────────────────────
# Groq prompt
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a research assistant for {persona_name} ({persona_role}).

Your task: From the web search results below, extract the most significant and recent AI/technology topics.

INCLUDE these topic categories:
{include_topics}

EXCLUDE these topic categories:
{exclude_topics}

RULES:
- Only include topics published in the last 48 hours
- Prefer topics with specific technical details (model names, benchmarks, numbers)
- Skip vague announcements, opinion pieces, crypto, or celebrity news
- Skip near-duplicate stories (same news from different sources — pick the best source)

OUTPUT FORMAT — respond ONLY with valid JSON, no markdown, no explanation:
{{
  "topics": [
    {{
      "title": "Concise descriptive title, max 15 words",
      "summary": "2-3 factual sentences with specific technical details",
      "source_url": "exact URL from search results — do not invent URLs",
      "source_name": "publication name",
      "recency_hint": "e.g. '2 hours ago', 'today', 'August 7 2025'",
      "search_snippet": "verbatim excerpt proving this is real and recent"
    }}
  ]
}}

Return 4 to {max_topics} topics. Only use URLs that appear in the search results provided."""

_USER_PROMPT = """Here are the Tavily search results. Extract the best AI/tech topics:

{search_results}

Return JSON with up to {max_topics} high-quality topics matching the persona scope."""


# ─────────────────────────────────────────────────────────────────────────────
# Discovery Service
# ─────────────────────────────────────────────────────────────────────────────


class TopicDiscoveryService:
    """
    Discovers fresh AI/tech topics using Tavily search + Groq structuring.
    Tavily is purpose-built for AI agents — clean results, accurate date filtering.
    """

    def __init__(self, persona: Persona) -> None:
        self._persona = persona
        self._settings = get_settings()
        self._tavily = TavilyClient(api_key=self._settings.tavily_api_key)
        self._groq = Groq(api_key=self._settings.groq_api_key)

    async def discover(self) -> list[DiscoveredTopic]:
        """Main entry point. 3-tier fallback ensures 100% perpetual operation."""
        logger.info("Starting topic discovery (Tavily → DuckDuckGo → Google News RSS)")
        try:
            raw_results = await self._search_tavily()
            if not raw_results:
                logger.info("Tavily returned no results or quota reached — falling back to DuckDuckGo News")
                raw_results = await self._search_duckduckgo()
            if not raw_results:
                logger.info("DuckDuckGo returned no results — falling back to Google News RSS")
                raw_results = await self._search_google_rss()

            if not raw_results:
                logger.warning("All discovery providers returned no results this cycle")
                return []

            structured = await self._structure_with_groq(raw_results)
            normalised = self._normalise(structured)
            logger.info(
                "Topic discovery complete",
                extra={"raw": len(raw_results), "structured": len(structured), "final": len(normalised)},
            )
            return normalised
        except Exception as exc:
            logger.error("Topic discovery failed", extra={"error": str(exc)}, exc_info=True)
            return []

    async def _search_tavily(self) -> list[dict[str, Any]]:
        """
        Run Tavily searches for all queries.
        Tavily returns clean, structured results with:
          - title, url, content (full snippet), score, published_date
        """
        def _sync_search() -> list[dict[str, Any]]:
            all_results: list[dict[str, Any]] = []
            seen_urls: set[str] = set()

            for query in _SEARCH_QUERIES:
                try:
                    response = self._tavily.search(
                        query=query,
                        search_depth="basic",     # "basic" = faster, "advanced" = deeper
                        topic="news",             # news mode — filters to recent articles
                        days=2,                   # last 48 hours only
                        max_results=5,
                        include_answer=False,
                        include_raw_content=False,
                    )
                    results = response.get("results", [])
                    for r in results:
                        url = r.get("url", "")
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            all_results.append({
                                "title":   r.get("title", ""),
                                "content": r.get("content", ""),
                                "url":     url,
                                "score":   r.get("score", 0),
                                "published_date": r.get("published_date", "recent"),
                            })
                    logger.debug(f"Tavily query done", extra={"query": query[:40], "results": len(results)})
                except Exception as e:
                    logger.warning(f"Tavily query failed: {query!r} — {e}")
                    continue

            # Sort by Tavily relevance score (higher = better)
            all_results.sort(key=lambda x: x.get("score", 0), reverse=True)
            return all_results

        results = await asyncio.to_thread(_sync_search)
        logger.info("Tavily search complete", extra={"total": len(results)})
        return results

    async def _search_duckduckgo(self) -> list[dict[str, Any]]:
        """100% Free & Unlimited DuckDuckGo News search fallback."""
        def _sync_ddg() -> list[dict[str, Any]]:
            all_results: list[dict[str, Any]] = []
            seen_urls: set[str] = set()
            try:
                try:
                    from duckduckgo_search import DDGS
                except ImportError:
                    from ddgs import DDGS
                ddgs = DDGS()
                for query in _SEARCH_QUERIES[:3]:
                    try:
                        results = list(ddgs.news(query, max_results=5))
                        for r in results:
                            url = r.get("url") or r.get("link") or ""
                            if url and url not in seen_urls:
                                seen_urls.add(url)
                                all_results.append({
                                    "title": r.get("title", ""),
                                    "content": r.get("body", "") or r.get("snippet", "") or r.get("title", ""),
                                    "url": url,
                                    "score": 1.0,
                                    "published_date": r.get("date", "recent"),
                                })
                    except Exception as q_err:
                        logger.warning(f"DDG query failed: {query} - {q_err}")
            except Exception as exc:
                logger.warning(f"DuckDuckGo search error: {exc}")
            return all_results

        results = await asyncio.to_thread(_sync_ddg)
        logger.info("DuckDuckGo News search complete", extra={"total": len(results)})
        return results

    async def _search_google_rss(self) -> list[dict[str, Any]]:
        """100% Free & Unlimited Google News RSS fallback (stdlib urllib + ET)."""
        def _sync_rss() -> list[dict[str, Any]]:
            import urllib.request
            import urllib.parse
            import xml.etree.ElementTree as ET
            all_results: list[dict[str, Any]] = []
            seen_urls: set[str] = set()

            queries = ["artificial intelligence news 2026", "agentic ai breakthrough 2026", "large language model release 2026", "open source AI 2026"]
            for q in queries:
                try:
                    encoded_q = urllib.parse.quote(q)
                    url = f"https://news.google.com/rss/search?q={encoded_q}&hl=en-US&gl=US&ceid=US:en"
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    xml_data = urllib.request.urlopen(req, timeout=10).read()
                    root = ET.fromstring(xml_data)
                    items = root.findall(".//item")
                    for item in items[:5]:
                        title = item.find("title").text if item.find("title") is not None else ""
                        link = item.find("link").text if item.find("link") is not None else ""
                        pub_date = item.find("pubDate").text if item.find("pubDate") is not None else "recent"
                        if link and link not in seen_urls:
                            seen_urls.add(link)
                            all_results.append({
                                "title": title,
                                "content": title,
                                "url": link,
                                "score": 1.0,
                                "published_date": pub_date,
                            })
                except Exception as r_err:
                    logger.warning(f"Google RSS query failed: {q} - {r_err}")
            return all_results

        results = await asyncio.to_thread(_sync_rss)
        logger.info("Google News RSS search complete", extra={"total": len(results)})
        return results

    async def _structure_with_groq(
        self, raw_results: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Use Groq to filter and structure Tavily results into typed topics."""
        persona = self._persona

        # Format results for the prompt (top 8 relevant search results)
        formatted = []
        for i, r in enumerate(raw_results[:8], 1):
            snippet = r.get("content", "")[:250].strip()
            formatted.append(
                f"[{i}]\n"
                f"TITLE: {r['title']}\n"
                f"URL: {r['url']}\n"
                f"DATE: {r.get('published_date', 'recent')}\n"
                f"CONTENT: {snippet}"
            )
        search_text = "\n\n---\n\n".join(formatted)

        system = _SYSTEM_PROMPT.format(
            persona_name=persona.name,
            persona_role=persona.role,
            include_topics="\n".join(f"  • {t}" for t in persona.include_topics),
            exclude_topics="\n".join(f"  • {t}" for t in persona.exclude_topics),
            max_topics=self._settings.max_topics_per_cycle,
        )
        user = _USER_PROMPT.format(
            search_results=search_text,
            max_topics=self._settings.max_topics_per_cycle,
        )

        def _sync_call() -> str:
            model_to_use = self._settings.groq_model
            try:
                response = self._groq.chat.completions.create(
                    model=model_to_use,
                    max_tokens=3000,
                    temperature=0.2,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user",   "content": user},
                    ],
                )
                return response.choices[0].message.content or ""
            except Exception as err:
                logger.warning(f"Groq call failed on {model_to_use}, falling back to llama-3.1-8b-instant: {err}")
                response = self._groq.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    max_tokens=3000,
                    temperature=0.2,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user",   "content": user},
                    ],
                )
                return response.choices[0].message.content or ""

        raw_text = await asyncio.to_thread(_sync_call)
        logger.debug("Groq structuring complete", extra={"length": len(raw_text)})
        return self._parse_json(raw_text)

    def _parse_json(self, text: str) -> list[dict[str, Any]]:
        """Extract topics JSON from Groq's response."""
        text = text.strip()
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if fenced:
            text = fenced.group(1).strip()
        start = text.find("{")
        if start == -1:
            logger.warning("No JSON found in Groq structuring response")
            return []
        text = text[start:]
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            for end in range(len(text), 0, -100):
                try:
                    data = json.loads(text[:end])
                    break
                except json.JSONDecodeError:
                    continue
            else:
                logger.error("Could not parse JSON from Groq structuring response")
                return []
        topics = data.get("topics", [])
        return topics if isinstance(topics, list) else []

    def _normalise(self, raw: list[dict[str, Any]]) -> list[DiscoveredTopic]:
        """Validate, normalise, and deduplicate topics."""
        seen_urls: set[str] = set()
        seen_titles: set[str] = set()
        results: list[DiscoveredTopic] = []

        for item in raw:
            if not isinstance(item, dict):
                continue

            title      = str(item.get("title", "")).strip()
            summary    = str(item.get("summary", "")).strip()
            source_url = str(item.get("source_url", "")).strip()
            source_name = str(item.get("source_name", "Unknown")).strip()
            recency    = str(item.get("recency_hint", "recent")).strip()
            snippet    = str(item.get("search_snippet", "")).strip()

            if not title or len(title) < 5:
                continue
            if not summary or len(summary) < 20:
                continue
            if not source_url or not source_url.startswith("http"):
                continue

            norm_url   = source_url.rstrip("/").lower()
            norm_title = re.sub(r"\W+", " ", title.lower()).strip()

            if norm_url in seen_urls or norm_title in seen_titles:
                continue

            seen_urls.add(norm_url)
            seen_titles.add(norm_title)

            results.append(DiscoveredTopic(
                title=title,
                summary=summary,
                source_url=source_url,
                source_name=source_name,
                recency_hint=recency,
                raw_search_snippet=snippet,
            ))

        return results[: self._settings.max_topics_per_cycle]
