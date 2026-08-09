# Autonomous AI Creator

> An autonomous backend AI agent that independently discovers fresh AI/technology topics, applies multi-dimensional editorial judgment, and publishes persona-consistent posts — all without human input after a single initialization call.

---

## 👥 Team & Contributors

| Name / GitHub | Role | Profile Link |
|---------------|------|--------------|
| **Sudhanshu** | Lead Architect & Full-Stack Developer | [@Sudhanshu7393](https://github.com/Sudhanshu7393) |
| **Tanishq** | Contributor & AI Pipeline Developer | [@Tanishq-7777](https://github.com/Tanishq-7777) |
| **Suryansh Kumar** | Contributor & Infrastructure Engineer | [@SuryanshKumar001](https://github.com/SuryanshKumar001) |

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Autonomous Workflow](#autonomous-workflow)
3. [Quick Start](#quick-start)
4. [Environment Variables](#environment-variables)
5. [Demo Mode vs Production Mode](#demo-mode-vs-production-mode)
6. [API Documentation](#api-documentation)
7. [Database Schema](#database-schema)
8. [Persona System](#persona-system)
9. [Editorial Engine](#editorial-engine)
10. [Memory Design](#memory-design)
11. [Scheduler Lifecycle](#scheduler-lifecycle)
12. [Logging](#logging)
13. [Frontend](#frontend)
14. [Assumptions & Known Limitations](#assumptions--known-limitations)
15. [Future Improvements](#future-improvements)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                  FastAPI (API Layer)                        │
│   POST /init   GET /feed   GET /status   GET /rejected      │
│                                                             │
│   Thin layer — zero business logic.                        │
│   Reads from MemoryManager or starts the Scheduler.        │
└──────────────────┬──────────────────────────────────────────┘
                   │ starts once (non-blocking)
                   ▼
┌─────────────────────────────────────────────────────────────┐
│              Scheduler (asyncio background task)            │
│                                                             │
│  Runs forever after /init. Each cycle is isolated.         │
│  One failure NEVER stops the loop.                         │
│  Configurable via CYCLE_INTERVAL_SECONDS.                  │
└──────────────────┬──────────────────────────────────────────┘
                   │ ticks every N seconds
                   ▼
┌─────────────────────────────────────────────────────────────┐
│                  AutonomousPipeline (one cycle)             │
│                                                             │
│  1. TopicDiscoveryService                                   │
│     └─ Claude web_search tool → 5–8 fresh topics           │
│     └─ Normalise, deduplicate, validate URLs               │
│                                                             │
│  2. EditorialDecisionEngine                                 │
│     └─ Full-history duplicate check (Jaccard similarity)   │
│     └─ LLM scoring on 5 dimensions (relevance/novelty/     │
│         depth/credibility/audience_value)                  │
│     └─ Accept/reject with human-readable rationale         │
│                                                             │
│  3. ContentGenerator                                        │
│     └─ Full persona injected into every single call        │
│     └─ Validates length, structure, forbidden phrases      │
│                                                             │
│  4. MemoryManager                                           │
│     └─ Persist post + all rejections + cycle record        │
└─────────────────────────────────────────────────────────────┘
```

### Module Responsibilities

| Module | File | Responsibility |
|--------|------|---------------|
| PersonaManager | `backend/core/persona_manager.py` | Loads `persona.json`, provides immutable `Persona` dataclass with `voice_block()` helper |
| TopicDiscoveryService | `backend/core/topic_discovery.py` | Claude web_search → normalised candidate topics |
| EditorialDecisionEngine | `backend/core/editorial_engine.py` | 5-dimension scoring, dupe detection, accept/reject rationale |
| ContentGenerator | `backend/core/content_generator.py` | Persona-grounded post generation |
| MemoryManager | `backend/core/memory_manager.py` | Async SQLite: posts, rejections, cycles |
| AutonomousPipeline | `backend/core/pipeline.py` | Orchestrates one cycle end-to-end |
| Scheduler | `backend/core/scheduler.py` | Asyncio background loop, error isolation |
| API Routes | `backend/api/routes.py` | Thin FastAPI handlers |

---

## Autonomous Workflow

```
POST /init
    │
    └─ Scheduler.start()               ← returns immediately
         │
         ├─ [Cycle 1 runs immediately]
         │      │
         │      ├─ Discover topics      ← real web search (Claude)
         │      ├─ Score all candidates ← LLM + duplicate check
         │      ├─ Log all rejections   ← to SQLite
         │      ├─ Generate post        ← persona injected verbatim
         │      └─ Save to SQLite       ← post + editorial reasoning
         │
         └─ Wait CYCLE_INTERVAL_SECONDS
              │
              └─ [Cycle 2 runs] → ...→ [Cycle N runs]
                   (loop continues forever, fully autonomous)
```

**Key guarantee**: The scheduler loop is a genuine `asyncio.create_task` that is completely decoupled from the API request lifecycle. Calling `GET /feed` 1,000 times will never produce a new post. Only the scheduler's internal clock creates posts.

---

## Quick Start

### Prerequisites

- Python 3.11+
- An Anthropic API key with access to `claude-3-7-sonnet-20250219`

### Setup

```bash
# 1. Clone / enter the project directory
cd "Autonomous AI Creator"

# 2. Create virtual environment
python -m venv venv
.\venv\Scripts\activate      # Windows
# source venv/bin/activate   # macOS/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
copy .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=sk-ant-...

# 5. Start the server
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# 6. Start the agent (one call, then it runs forever)
curl -X POST http://localhost:8000/api/agent/init

# 7. Open the frontend
# Open frontend/index.html in a browser
# OR navigate to http://localhost:8000/ (served by FastAPI)
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | **required** | Your Anthropic API key |
| `CYCLE_INTERVAL_SECONDS` | `90` | Seconds between cycles. Set to 90 for demos, 7200+ for production |
| `MIN_RELEVANCE_SCORE` | `6.5` | Composite score threshold (0–10). Topics below this are rejected |
| `MAX_TOPICS_PER_CYCLE` | `8` | Max candidate topics fetched per cycle |
| `DB_PATH` | `./data/agent.db` | SQLite file path |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins |

---

## Demo Mode vs Production Mode

### Demo Mode (live presentations)
```bash
CYCLE_INTERVAL_SECONDS=90   # new post every 90 seconds
```
- Cycles fast enough to show judges real posts being generated in real time
- Useful for: hackathon demos, live walkthroughs, integration testing

### Production Mode (48-hour autonomous run simulation)
```bash
CYCLE_INTERVAL_SECONDS=7200   # new post every 2 hours = 24 posts over 48 hours
# OR
CYCLE_INTERVAL_SECONDS=10800  # every 3 hours = 16 posts over 48 hours
```
- Simulates a professional publishing cadence
- Avoids rate limits and keeps content genuinely fresh
- The scheduler, pipeline, and duplicate detection all work identically in both modes

The only difference between demo and production is the `CYCLE_INTERVAL_SECONDS` value. The code and architecture are identical.

---

## API Documentation

### `POST /api/agent/init`

Starts the background autonomous loop. **Returns immediately** — does not wait for any cycle to complete.

**Response:**
```json
{
  "status": "initialized",
  "agent": "ARIA",
  "message": "Agent ARIA started. First cycle running now."
}
```

**Idempotent**: Calling this while the agent is already running returns `status: "already_running"`.

---

### `GET /api/agent/feed`

Returns all published posts, newest first. **Strictly read-only.**

```json
{
  "posts": [
    {
      "id": 1,
      "cycle_id": "uuid",
      "topic": "OpenAI releases o3-mini with 40% lower cost",
      "post_text": "Worth watching: o3-mini cuts inference cost by 40%...",
      "rationale": "ACCEPTED — Composite score 8.2/10. Direct capability advance...",
      "relevance_score": 9.2,
      "novelty_score": 8.5,
      "depth_score": 7.8,
      "composite_score": 8.2,
      "source_url": "https://openai.com/...",
      "timestamp": "2025-08-07T19:32:11.420Z"
    }
  ],
  "total": 1
}
```

---

### `GET /api/agent/status`

```json
{
  "is_running": true,
  "agent": "ARIA",
  "cycles_completed": 3,
  "cycles_failed": 0,
  "next_cycle_in_seconds": 47.2,
  "last_cycle_at": "2025-08-07T19:32:11Z",
  "last_cycle_topic": "OpenAI releases o3-mini...",
  "started_at": "2025-08-07T19:00:00Z",
  "total_posts_published": 2,
  "total_topics_rejected": 14,
  "cycle_interval_seconds": 90
}
```

---

### `GET /api/agent/rejected`

Returns all topics that were evaluated and rejected, newest first. Demonstrates the editorial engine's real decision-making.

```json
{
  "rejected_topics": [
    {
      "id": 1,
      "cycle_id": "uuid",
      "topic": "Elon Musk tweets about Grok",
      "rejection_reason": "REJECTED — composite score 3.1 is below the bar of 6.5. This topic involves celebrity social media activity without technical substance...",
      "scores": {
        "relevance": 2.0,
        "novelty": 5.0,
        "depth": 1.5,
        "credibility": 4.0,
        "audience_value": 2.0,
        "composite": 3.1
      },
      "source_url": "https://twitter.com/...",
      "rejected_at": "2025-08-07T19:31:45Z"
    }
  ],
  "total": 14
}
```

---

## Database Schema

```sql
-- Published posts
CREATE TABLE posts (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  cycle_id        TEXT    NOT NULL,           -- Links to cycle
  topic           TEXT    NOT NULL,           -- Original topic title
  post_text       TEXT    NOT NULL,           -- Published post content
  rationale       TEXT    NOT NULL,           -- Editorial reasoning (NEVER discarded)
  relevance_score REAL    NOT NULL,           -- 0–10
  novelty_score   REAL    NOT NULL,           -- 0–10
  depth_score     REAL    NOT NULL,           -- 0–10
  composite_score REAL    NOT NULL,           -- Weighted composite
  source_url      TEXT    NOT NULL,           -- Real source URL from web search
  published_at    TEXT    NOT NULL            -- ISO-8601 UTC
);

-- All rejected topic evaluations
CREATE TABLE rejected_topics (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  cycle_id         TEXT    NOT NULL,
  topic            TEXT    NOT NULL,
  rejection_reason TEXT    NOT NULL,          -- Human-readable rationale
  scores           TEXT    NOT NULL,          -- JSON: all dimension scores
  source_url       TEXT,
  rejected_at      TEXT    NOT NULL
);

-- Cycle execution log
CREATE TABLE cycles (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  cycle_id          TEXT    NOT NULL UNIQUE,
  started_at        TEXT    NOT NULL,
  completed_at      TEXT,
  status            TEXT    NOT NULL,          -- running / completed / failed
  topics_discovered INTEGER NOT NULL DEFAULT 0,
  topics_rejected   INTEGER NOT NULL DEFAULT 0,
  post_published    INTEGER NOT NULL DEFAULT 0,
  error_message     TEXT
);
```

Database uses WAL mode for concurrent reads during writes (important since the API serves reads while the scheduler writes).

---

## Persona System

The persona is defined in `backend/persona.json` and loaded once at startup by `PersonaManager`. It is an **immutable dataclass** that gets passed explicitly into every pipeline stage.

The persona represents a real technology professional (ARIA) with:
- **Identity**: Name, role, tagline, target audience
- **Personality**: Tone, style, stance, forbidden phrases, preferred openers
- **Voice rules**: 7 explicit rules governing every post (e.g., "Lead with insight, not announcement")
- **Post format**: Max characters, structure, hashtag count, emoji policy
- **Topic scope**: Include/exclude lists with 10 included categories and 6 excluded
- **Editorial standards**: Per-dimension minimum scores and scoring weights

**Voice consistency**: The `persona.voice_block()` method returns a formatted multi-line block containing all voice rules, format constraints, forbidden phrases, and preferred openers. This block is injected verbatim into every ContentGenerator and EditorialDecisionEngine prompt. The AI never infers tone from conversational history — it reads the rules fresh every time.

---

## Editorial Engine

Every candidate topic is scored on five independent dimensions:

| Dimension | Weight | What it measures |
|-----------|--------|-----------------|
| Relevance | 25% | Alignment with persona's topic scope |
| Novelty | 25% | Freshness relative to prior posts |
| Technical Depth | 20% | Substance beyond surface-level announcements |
| Source Credibility | 15% | Trustworthiness of the origin publication |
| Audience Value | 15% | Practical usefulness to practitioners |

**Composite score** = weighted sum of all five. Topics must clear:
- Composite ≥ `MIN_RELEVANCE_SCORE` (default 6.5)
- Novelty ≥ 5.0
- Depth ≥ 5.0
- Credibility ≥ 5.0

**Duplicate detection** uses token-overlap (Jaccard similarity) to compare every candidate against the **full** published topic history. Threshold: 55% token overlap → duplicate. No LLM needed, no truncation of history.

**Every decision** (accept AND reject) gets a human-readable rationale stored permanently. Rejection reasons are first-class data, not debugging noise.

---

## Memory Design

`MemoryManager` uses `aiosqlite` for non-blocking async I/O. All writes go through parameterised queries (no SQL injection surface). WAL journal mode allows concurrent reads while writes happen.

**Full-history duplicate detection**: `get_all_published_topics()` always returns every published topic, never paginated. The editorial engine compares every incoming candidate against this complete set.

**Data permanence**: Nothing is ever deleted. Posts, rejections, and cycle records accumulate forever. This is intentional — the agent's entire editorial history is always queryable.

---

## Scheduler Lifecycle

```
Scheduler.start()
  └─ asyncio.create_task(_loop())    ← background task, non-blocking
       │
       ├─ _run_one_cycle()           ← runs immediately
       │     └─ pipeline.run_cycle()
       │
       └─ while not stop_event:
             wait CYCLE_INTERVAL_SECONDS (5s ticks for responsiveness)
             └─ _run_one_cycle()
                  └─ try:
                       pipeline.run_cycle()
                     except Exception:
                       log error
                       continue      ← loop NEVER stops on error
```

The 5-second tick granularity means stop signals (shutdown, KeyboardInterrupt) are honoured within 5 seconds rather than waiting up to the full interval.

---

## Logging

Structured log format: `timestamp | LEVEL | module | message`

Every pipeline stage emits logs with context extras:
```
2025-08-07T19:32:00Z | INFO     | backend.core.scheduler  | Cycle starting | cycle_number=1
2025-08-07T19:32:01Z | INFO     | backend.core.topic_disc | Discovery complete | raw=7 normalised=6
2025-08-07T19:32:15Z | INFO     | backend.core.editorial  | Topic accepted | topic="OpenAI o3-mini..." score=8.2
2025-08-07T19:32:15Z | INFO     | backend.core.editorial  | Rejected topic logged | topic="Elon tweets..." reason="REJECTED..."
2025-08-07T19:32:22Z | INFO     | backend.core.pipeline   | Post saved | post_id=1 topic="OpenAI o3-mini..."
```

Set `LOG_LEVEL=DEBUG` to see: JSON parsing details, duplicate detection ratios, character count validation.

---

## Frontend

`frontend/index.html` is a single self-contained file with no build step. It serves two purposes:

1. **Prove autonomy to judges**: The status sidebar shows live scheduler state, countdown ring, cycles completed, and total rejections — all updating automatically without page interaction.
2. **Display the feed**: Post cards show topic, post text, editorial scores (expandable), rationale, source link, and timestamp.

**Polling schedule**:
- `/api/agent/status` — every 5 seconds (keeps countdown accurate)
- `/api/agent/feed` — every 20 seconds (new posts are rare; polling more often is wasteful)
- `/api/agent/rejected` — on demand (when the rejection drawer is open)

**No generation on interaction**: Clicking any button or refreshing the page never creates a post. The "Start Agent" button calls `/api/agent/init` which only starts the scheduler — posts only appear when the scheduler's clock fires.

---

## Assumptions & Known Limitations

1. **Claude web_search tool**: Requires `claude-3-7-sonnet-20250219` or later with web_search enabled. If the model or tool changes, `topic_discovery.py` needs updating.

2. **Duplicate detection is keyword-based**: Jaccard token overlap is fast and works without embeddings, but may miss semantic duplicates with different wording. An embedding-based solution (e.g., using Claude embeddings API) would be more robust.

3. **Single-process**: The scheduler and API share a process. For production, consider running the scheduler as a separate worker process communicating via the database.

4. **No authentication**: The API has no auth. For production, add API key validation or OAuth.

5. **SQLite concurrency**: SQLite with WAL handles light concurrent load well. For high traffic, migrate to PostgreSQL.

6. **Rate limiting**: The Anthropic API has rate limits. At 90-second intervals, with ~10 API calls per cycle, this is well within typical limits. At shorter intervals, add retry logic.

7. **Web search accuracy**: Claude's web_search tool searches the real web, but result quality depends on Anthropic's search integration. Topics are real but may occasionally be slightly older than 48 hours.

---

## Future Improvements

- **Embedding-based duplicate detection**: Replace Jaccard with semantic similarity for better recall.
- **Multi-platform publishing**: Push posts to Twitter/X, LinkedIn, or Bluesky APIs.
- **Configurable personas**: Support multiple concurrent personas with different topic scopes.
- **A/B testing**: Score post variants and track engagement when published to real platforms.
- **Admin dashboard**: Full cycle history, editorial decision timeline, score trends.
- **Webhook notifications**: Push alerts when a post is published.
- **Retry with backoff**: Automatic retry for transient Anthropic API failures.
- **Separate worker process**: Decouple the scheduler from the API server for resilience.

---

## Engineering Decisions

**Why Claude's native web_search instead of a separate search API?**  
No additional API key or service dependency. The search results are grounded in what Claude actually found, not what a separate system returned.

**Why SQLite instead of PostgreSQL?**  
Zero infrastructure setup. For a hackathon and for the actual use case (one autonomous agent writing 8–24 posts/day), SQLite with WAL is entirely sufficient. The schema is designed to migrate to Postgres trivially.

**Why Jaccard similarity for duplicate detection instead of embeddings?**  
No external service dependency, fast enough for thousands of topics, and deterministic. The 55% threshold was chosen to catch obvious duplicates without false positives on topically adjacent content.

**Why asyncio.to_thread for Anthropic API calls?**  
The `anthropic` Python SDK is synchronous. Wrapping calls in `asyncio.to_thread` keeps the FastAPI event loop free during API waits, so `/feed` and `/status` remain responsive while the pipeline is running.

**Why not auto-start the scheduler on server startup?**  
The spec explicitly requires a single `/init` call to start the loop. This also makes deployment cleaner — you can deploy the server and verify it started before starting the agent.
