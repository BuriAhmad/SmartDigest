# SmartDigest

> **An async AI content pipeline** — subscribe to topics, fetch the latest articles from RSS feeds, summarise them with Google Gemini, and receive a clean email digest. Built with FastAPI, PostgreSQL, Redis, and ARQ.

---

## What it does

SmartDigest is a production-grade background processing system. You define a topic (e.g. "Machine Learning", "Software Jobs"), pick your RSS sources, and set a schedule. SmartDigest:

1. **Fetches** articles from your chosen RSS feeds concurrently
2. **Summarises** all articles in a single Gemini API call (batched to minimise free-tier usage)
3. **Delivers** a beautifully formatted HTML email digest to your inbox
4. **Tracks** every pipeline stage in the database for full observability

---

## Architecture

```
Browser / API Client
       │
       ▼
┌─────────────────┐      enqueue job      ┌──────────────────┐
│  FastAPI Server │ ────────────────────► │   Redis Queue    │
│  (Uvicorn)      │                       └────────┬─────────┘
└─────────────────┘                                │ dequeue
       │                                           ▼
       │ read/write                    ┌──────────────────────┐
       ▼                               │    ARQ Worker        │
┌─────────────────┐                   │  ┌────────────────┐   │
│   PostgreSQL    │ ◄─────────────────│  │ 1. Fetch RSS   │   │
│   Database      │                   │  │ 2. Gemini AI   │   │
└─────────────────┘                   │  │ 3. Send Email  │   │
                                      │  └────────────────┘   │
                                      └──────────────────────┘
```

The API server and background worker are **completely decoupled** — they only share the Redis queue and the PostgreSQL database. This means the API stays fast and responsive even while long-running pipeline jobs are executing.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI 0.115 |
| ASGI server | Uvicorn |
| Database | PostgreSQL 16 (Docker) |
| ORM | SQLAlchemy 2.0 (async) |
| Migrations | Alembic |
| Task queue | ARQ (async Redis queue) |
| Cache / broker | Redis 7 (Docker) |
| AI summarisation | Google Gemini 2.0 Flash (REST API) |
| Email delivery | Resend API |
| HTTP client | httpx (async) |
| RSS parsing | feedparser |
| Frontend | Jinja2 + HTMX + Tailwind CSS |
| Rate limiting | slowapi |
| Structured logging | structlog |
| Config management | Pydantic Settings |

---

## Features

- **API key authentication** — SHA-256 hashed keys, timing-safe comparison
- **Subscription CRUD** — create, update, delete subscriptions via REST or dashboard UI
- **One-click pipeline trigger** — manually trigger a digest from the dashboard
- **Scheduled daily cron** — ARQ cron job enqueues all active subscriptions at 06:00 UTC
- **Full observability** — every pipeline stage (fetch / summarise / deliver) is recorded in `pipeline_events` with duration, status, and error messages
- **Live metrics panel** — HTMX-polled dashboard panel showing 24h stats, per-stage latency, and last error
- **Rate limiting** — 3 trigger requests per hour per API key (slowapi)
- **Structured JSON logging** — structlog, JSON in production, pretty console in development
- **Dev mode email** — if `RESEND_API_KEY` is unset, emails are logged to console instead of sent

---

## Database Schema

```
api_keys          — hashed keys with usage counters
curated_sources   — pre-approved RSS feeds (seeded via CLI)
subscriptions     — user topic + sources + email + schedule
digests           — one record per pipeline run
digest_items      — one record per article, with title + summary + URL
pipeline_events   — observability log (stage, status, duration_ms, error_msg)
```

---

## Getting Started

### Prerequisites

- Python 3.9+
- Docker (for PostgreSQL + Redis)
- A [Resend](https://resend.com) account (free tier works)
- A [Google AI Studio](https://aistudio.google.com) API key (free tier works)

### 1. Clone and set up the environment

```bash
git clone https://github.com/BuriAhmad/SmartDigest.git
cd SmartDigest
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Start infrastructure

```bash
# PostgreSQL
docker run -d --name smartdigest-postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=smartdigest \
  -p 5432:5432 postgres:16

# Redis
docker run -d --name smartdigest-redis \
  -p 6379:6379 redis:7
```

### 3. Configure environment

Create a `.env` file:

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/smartdigest
REDIS_URL=redis://localhost:6379
GEMINI_API_KEY=your_google_ai_studio_key
RESEND_API_KEY=your_resend_api_key
RESEND_FROM_EMAIL=onboarding@resend.dev
ENV=development
```

### 4. Run migrations and seed sources

```bash
alembic upgrade head
python -m app seed-sources
```

### 5. Generate your API key

```bash
python -m app create-key
```

Copy the key shown — you'll need it for the dashboard.

### 6. Start the servers

```bash
# Terminal 1 — API server
python -m uvicorn app.main:app --reload --port 8000

# Terminal 2 — Background worker
python worker.py
```

Open **http://localhost:8000** in your browser.

---

## API Reference

All API endpoints (except key creation and sources listing) require:
```
Authorization: Bearer <your-api-key>
```

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/keys` | Create a new API key |
| `DELETE` | `/api/v1/keys/{prefix}` | Revoke an API key |
| `GET` | `/api/v1/sources` | List available RSS sources |
| `POST` | `/api/v1/subscriptions` | Create a subscription |
| `GET` | `/api/v1/subscriptions` | List your subscriptions |
| `PATCH` | `/api/v1/subscriptions/{id}` | Update a subscription |
| `DELETE` | `/api/v1/subscriptions/{id}` | Delete a subscription |
| `POST` | `/api/v1/subscriptions/{id}/trigger` | Trigger pipeline (rate limited: 3/hour) |
| `GET` | `/api/v1/digests` | List your digests |
| `GET` | `/api/v1/digests/{id}` | Get digest detail with items |
| `GET` | `/api/v1/metrics/pipeline` | 24h pipeline stats |
| `GET` | `/api/v1/metrics/usage` | Per-key usage stats |

Interactive API docs available at **http://localhost:8000/docs**.

---

## Project Structure

```
SmartDigest/
├── app/
│   ├── api/              # FastAPI routers (keys, sources, subscriptions, digests, metrics)
│   ├── middleware/        # Auth middleware + rate limiter
│   ├── models/           # SQLAlchemy ORM models
│   ├── schemas/          # Pydantic request/response schemas
│   ├── services/         # Business logic (fetcher, summariser, mailer, metrics, scheduler)
│   ├── config.py         # Pydantic Settings (env vars)
│   ├── database.py       # Async engine + session factory
│   ├── main.py           # App factory + HTML routes
│   └── cli.py            # Management CLI (create-key, seed-sources)
├── templates/            # Jinja2 HTML templates (HTMX-powered dashboard)
│   └── partials/         # HTMX partial templates
├── alembic/              # Database migrations
├── worker.py             # ARQ worker entrypoint
├── requirements.txt
└── README.md
```

---

## Design Decisions

**Why ARQ over Celery?** ARQ is async-native, lightweight, and uses Redis directly. No broker configuration headaches, and it pairs perfectly with async SQLAlchemy.

**Why batch Gemini calls?** The free tier has strict RPM (requests per minute) limits. Batching all articles into one prompt uses the large context window efficiently and costs one API call regardless of article count.

**Why httpx for Gemini?** Direct REST calls avoid SDK version fragility. The Gemini REST API is stable and well-documented — no dependency on Google's Python SDK release cycle.

**Why HTMX?** Zero-build frontend. The dashboard is fully interactive (live metrics, subscription management, pipeline triggers) without a JavaScript bundler, React, or build step.

---

## License

MIT
