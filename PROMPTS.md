# PROMPTS.md — AI Usage Log & Pair-Programming Journal

This project was built using **Google Antigravity (Gemini)** as an AI pair programming assistant.
The entire backend, frontend, responsive layout, and deployment configuration was vibe-coded and iteratively refined.

---

## 👥 Team & Contributors

- **Sudhanshu Kumar** ([@Sudhanshu7393](https://github.com/Sudhanshu7393)) — Lead Architect & Full-Stack Developer
- **Tanishq** ([@Tanishq-7777](https://github.com/Tanishq-7777)) — Contributor & AI Pipeline Developer
- **Suryansh Kumar** ([@SuryanshKumar001](https://github.com/SuryanshKumar001)) — Contributor & Infrastructure Engineer

---

## Key Prompts Used & Development Iterations

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

### 2. Provider Flexibility
```
anthropic ki jagh groq key use nahi kar sakte?
```
→ Switched LLM provider to Groq (`llama-3.3-70b-versatile` & `llama-3.1-8b-instant`) with automatic failover.

### 3. Search Provider Selection
```
agar isse best ho koi to btaao use karunga and jo bta rhe likhega wo kahi likh nahi rha
```
→ Recommended & implemented Tavily API, and later added a 3-tiered perpetual fallback system.

### 4. Rate-Limit & Token Quota Resolution
```
[screenshot showing 0 posts and Groq TPD rate limit error 429]
permanent solution btaao iska ya fir model change karna sahi hoga?
```
→ Fixed Groq rate limits by switching default model to `llama-3.1-8b-instant` (500K TPD) and adding self-healing fallback when 429 or 413 token errors occur.

### 5. Multi-Tiered Perpetual Search Fallback
```
Tavily ka limited token thha shayad khatam ho gya h koi alternate option ho jo hmesha chalta rahe?
```
→ Implemented 3-Tiered Perpetual Search Fallback (Tavily → DuckDuckGo News → Google News RSS). If Tavily quota expires, the agent automatically falls back to free, unlimited DuckDuckGo and Google News RSS without human intervention.

### 6. Responsive UI & Mobile Support
```
alag alag device like phone par, tablet par and all devices include karlo unpar unke accordingly aacha dikhe jo abhi dikh nahi raha
```
→ Redesigned `index.html` with Tailwind CSS responsive grid (`grid-cols-1 lg:grid-cols-[1fr_320px]`), touch-friendly buttons, mobile status pill, and text truncation wrappers for all devices.

---

## 🏛️ System Architecture & File Breakdown

| File | Description |
|------|-------------|
| `backend/core/topic_discovery.py` | 3-Tier Search (Tavily → DDG → Google RSS) + Groq structuring |
| `backend/core/editorial_engine.py` | 5-Dimension scoring + duplicate checking + model fallback |
| `backend/core/content_generator.py` | Persona post generation + length validation + model fallback |
| `backend/core/memory_manager.py` | Async SQLite database manager with WAL mode |
| `backend/core/pipeline.py` | Stateless pipeline orchestrator |
| `backend/core/scheduler.py` | Autonomous background asyncio loop with resilient timer state |
| `backend/api/routes.py` | FastAPI endpoints (`/init`, `/status`, `/feed`, `/rejected`, `/trigger`) |
| `frontend/index.html` | Ultra-responsive SPA — dark theme, live countdown ring, status panel |

---

## 🔒 Verification & Compliance

- **Read-Only `/feed`**: `GET /api/agent/feed` reads strictly from SQLite memory.
- **Fail-Safe Loop**: Each background cycle is wrapped in try/except blocks so a failed cycle never stops the loop.
- **Perpetual Free Search**: 3-tiered fallback ensures discovery never stops even if Tavily API quota expires.
- **Self-Healing LLM Calls**: Rate limit 429/413 errors automatically trigger instant fallback to `llama-3.1-8b-instant`.
