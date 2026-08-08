# PROMPTS.md — AI Usage Log

This project was built using **Google Antigravity (Gemini)** as an AI coding assistant.
The entire backend, frontend, and deployment configuration was vibe-coded in a single session.

---

## Build Session Summary

**Project:** Autonomous AI Creator  
**AI Tool:** Google Antigravity (Gemini 2.5 Pro)  
**Build Time:** ~1 day (hackathon sprint)  
**Approach:** Fully agentic pair programming — the AI wrote all code, fixed bugs, and deployed

---

## Key Prompts Used

### 1. Initial Project Brief
```
You are building "Autonomous AI Creator" — an autonomous backend AI agent for a hackathon,
to be completed in a single day. Build production-quality but appropriately scoped code.

A backend service where, after ONE initialization call, an AI agent runs on its own for an
extended period (simulate 48 hours via a configurable, demo-friendly cycle interval) and
repeatedly does the following without any further human input:

1. DISCOVER fresh AI/Technology topics from the last 24-48 hours using real web search
2. ANALYZE each discovered topic: score it for relevance, novelty, and substance
3. REJECT topics that are duplicates or score below a defined bar
4. SELECT the single highest-scoring valid topic
5. GENERATE a social-media-style post in a fixed, consistent persona voice
6. PUBLISH the post to a persistent feed (read-only endpoint — never generates on GET)
7. LOG all decisions (accepted + rejected) with full rationale to persistent storage
8. REPEAT autonomously every N seconds/minutes without any human input
```

### 2. Tech Stack Switch
```
anthropic ki jagh groq key use nahi kar sakte?
```
→ AI switched entire backend from Anthropic Claude to Groq (llama-3.3-70b-versatile) + DuckDuckGo search

### 3. Better Search API
```
agar isse best ho koi to btaao use karunga and jo bta rhe likhega wo kahi likh nahi rha
```
→ AI recommended and implemented Tavily Search API (purpose-built for AI agents, free tier)

### 4. Debugging Rate Limits
```
[screenshot of live frontend showing 0 posts]
```
→ AI diagnosed Groq 429 rate limit errors, fixed by switching from parallel to sequential scoring with 6s delays

### 5. Understanding the System
```
pehle ye project vistaar me mujhe hi samjhaao
```
→ AI explained entire architecture in Hindi — pipeline, modules, data flow, API design

---

## What the AI Built

| File | Lines | Description |
|------|-------|-------------|
| `backend/core/topic_discovery.py` | ~250 | Tavily search + Groq structuring |
| `backend/core/editorial_engine.py` | ~460 | 5-dimension scoring + duplicate detection |
| `backend/core/content_generator.py` | ~200 | Persona-grounded post generation |
| `backend/core/memory_manager.py` | ~180 | Async SQLite persistence |
| `backend/core/pipeline.py` | ~220 | Pipeline orchestrator |
| `backend/core/scheduler.py` | ~200 | Autonomous asyncio loop |
| `backend/core/persona_manager.py` | ~80 | ARIA persona loader |
| `backend/api/routes.py` | ~120 | FastAPI endpoints |
| `backend/api/schemas.py` | ~60 | Pydantic response models |
| `backend/main.py` | ~110 | App factory + lifespan |
| `backend/config.py` | ~90 | Pydantic-settings config |
| `frontend/index.html` | ~600 | Dark SPA — live feed + countdown |
| `backend/persona.json` | ~60 | ARIA persona definition |

**Total: ~2,600+ lines of production-quality code, zero boilerplate copied from templates.**

---

## Architecture Decisions Made by AI

1. **SQLite over PostgreSQL** — "WAL mode gives safe concurrent reads without ORM overhead for a hackathon scope"
2. **asyncio.to_thread for sync SDK calls** — "Keeps FastAPI event loop responsive while Groq SDK (sync) runs in executor"
3. **Sequential scoring with delays** — "Groq free tier = 12K TPM; parallel calls hit limit instantly; 6s gaps solve it"
4. **Persona injected verbatim each call** — "LLM has no memory between calls; inject full voice block every time to prevent drift"
5. **/feed is read-only by design** — "GET endpoints never generate; only scheduler creates posts; proven by architecture"
6. **Tavily over DuckDuckGo** — "DuckDuckGo library is unofficial and rate-limited; Tavily has official API with 48h date filter"

---

## Live Demo Evidence

- Agent runs autonomously every 90 seconds
- Real posts generated from real Tavily news search
- 26+ topics rejected with full editorial rationale logged
- Published posts include source URLs from real publications (Bloomberg, SiliconAngle, etc.)
- `/feed` endpoint verified read-only (multiple GET calls produce no new posts)
