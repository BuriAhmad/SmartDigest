# SmartDigest

> A production-grade async content pipeline — subscribe to topics, fetch articles from RSS feeds concurrently, summarise them with Google Gemini in a single batched API call, and receive a clean HTML email digest. Built on FastAPI, PostgreSQL, Redis, and ARQ.

**Live demo:** [smartdigest-production.up.railway.app](https://smartdigest-production.up.railway.app)

---

## What it does

You define a topic (e.g. "AI Research", "Backend Engineering"), pick from a curated list of RSS sources, and set a delivery schedule. SmartDigest handles the rest:

1. **Fetches** articles from your chosen feeds concurrently via async HTTP
2. **Summarises** all articles in a single Gemini API batch call — maximising context window usage and minimising quota consumption
3. **Delivers** a formatted HTML digest to your inbox via Resend
4. **Tracks** every pipeline stage in PostgreSQL for full observability

---

## Architecture

```
Browser / API Client
        │
        ▼
┌──────────────────┐       enqueue job       ┌───────────────────┐
│  FastAPI (Web)   │ ──────────────────────► │    Redis Queue    │
│  Uvicorn         │                         └─────────┬─────────┘
└──────────────────┘                                   │ dequeue
        │                                              ▼
        │ async read/write               ┌─────────────────────────┐
        ▼                                │      ARQ Worker         │
┌──────────────────┐                     │  ┌───────────────────┐  │
│    PostgreSQL    │ ◄───────────────────│  │  1. Fetch RSS     │  │
│    (async ORM)   │                     │  │  2. Gemini AI     │  │
└──────────────────┘                     │  │  3. Send Email    │  │
                                         │  └───────────────────┘  │
                                         └─────────────────────────┘
```

The API server and background worker are **fully decoupled** — they communicate only through the Redis queue and the shared PostgreSQL database. The API returns `202 Accepted` immediately on trigger; the worker handles all long-running work asynchronously.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI 0.115 |
| ASGI server | Uvicorn |
| Database | PostgreSQL 16 |
| ORM | SQLAlchemy 2.0 (fully async) |
| Migrations | Alembic |
| Task queue | ARQ (async Redis queue) |
| Cache / broker | Redis 7 |
| AI summarisation | Google Gemini 2.0 Flash (direct REST, no SDK) |
| Email delivery | Resend API |
| HTTP client | httpx (async) |
| RSS parsing | feedparser |
| Frontend | Jinja2 + HTMX + Tailwind CSS |
| Rate limiting | slowapi |
| Structured logging | structlog (JSON in production) |
| Configuration | Pydantic Settings |
| Deployment | Railway (web + worker as separate services) |

---

## Features

- **User authentication** — email + password accounts with bcrypt hashing, JWT sessions via httpOnly cookies
- **Plan column** — `free`/`pro` on users table for future monetisation (not enforced yet)
- **Subscription management** — full CRUD via REST API and dashboard UI, scoped to authenticated user
- **Async pipeline** — concurrent feed fetching, single-batch AI summarisation, non-blocking email delivery
- **Model fallback** — if Gemini 2.0 Flash quota is exhausted, automatically falls back to 2.5 Flash, then lite models
- **Rate limiting** — 3 pipeline triggers per hour per key (slowapi)
- **Full observability** — every stage (fetch / summarise / deliver) written to `pipeline_events` with status, duration, and error details
- **Live metrics panel** — HTMX-polled dashboard showing 24h stats, per-stage average latency, and last error
- **Scheduled cron** — ARQ cron job enqueues all active subscriptions at 06:00 UTC daily
- **Dev mode** — if `RESEND_API_KEY` is unset, emails are printed to console; app runs fully without external accounts
- **Structured logging** — JSON in production, pretty console in development (structlog)

---

## Database Schema

```
users             — email + bcrypt password hash, name, plan (free/pro), login timestamps
curated_sources   — pre-approved RSS feed list (seeded via CLI)
subscriptions     — topic + sources + email + cron schedule, scoped to user_id
digests           — one record per pipeline run (status, delivered_at)
digest_items      — one record per article (title, summary, URL, source)
pipeline_events   — observability log: stage × status × duration_ms × error_msg
```

---

## Getting Started (Local)

### Prerequisites

- Python 3.9+
- Docker (for PostgreSQL and Redis)
- [Resend](https://resend.com) API key (free tier)
- [Google AI Studio](https://aistudio.google.com) API key (free tier)

### 1. Clone and install

```bash
git clone https://github.com/BuriAhmad/SmartDigest.git
cd SmartDigest
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Start PostgreSQL and Redis

```bash
docker run -d --name smartdigest-postgres \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=smartdigest \
  -p 5432:5432 postgres:16

docker run -d --name smartdigest-redis \
  -p 6379:6379 redis:7
```

### 3. Configure environment

```env
# .env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/smartdigest
REDIS_URL=redis://localhost:6379
GEMINI_API_KEY=your_key_here
RESEND_API_KEY=your_key_here
RESEND_FROM_EMAIL=onboarding@resend.dev
JWT_SECRET=generate-a-random-secret-here
ENV=development
```

### 4. Migrate and seed

```bash
alembic upgrade head
python -m app.cli seed_sources
```

### 5. Create a user account

```bash
python -m app.cli create_user you@example.com yourpassword "Your Name"
```

### 6. Run both services

```bash
# Terminal 1 — API
uvicorn app.main:app --reload --port 8000

# Terminal 2 — Worker
python worker.py
```

Open **http://localhost:8000** in your browser.

---

## Deploying on Railway

SmartDigest runs as two separate Railway services — web and worker — each with its own `railway.toml`.

### Step 1 — Add services in Railway dashboard

1. Create a new Railway project
2. Add **PostgreSQL** plugin → Railway auto-injects `DATABASE_URL`
3. Add **Redis** plugin → Railway auto-injects `REDIS_URL`
4. Add a **GitHub service** (your repo) → **web** service
5. Add a second **GitHub service** (same repo) → **worker** service

### Step 2 — Point each service to its config

| Service | Config File Path |
|---|---|
| Web | `services/web/railway.toml` |
| Worker | `services/worker/railway.toml` |

Set in: service → **Settings → Config File Path**

### Step 3 — Set environment variables (both services)

| Variable | Value |
|---|---|
| `DATABASE_URL` | Auto-injected by Railway Postgres |
| `REDIS_URL` | Auto-injected by Railway Redis |
| `GEMINI_API_KEY` | Your Google AI Studio key |
| `RESEND_API_KEY` | Your Resend key |
| `RESEND_FROM_EMAIL` | Your verified sender email |
| `JWT_SECRET` | Random secret for signing sessions |
| `ENV` | `production` |

### Step 4 — Deploy

Push to GitHub. Railway will run `alembic upgrade head && python -m app.cli seed_sources` before starting, ensuring the DB is always up to date.

---

## API Reference

Authentication is handled via httpOnly session cookies. Log in at `/login` to get a session.

Public endpoints (no auth): `GET /api/v1/sources`, `POST /auth/register`, `POST /auth/login`.

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/auth/register` | Create a new account |
| `POST` | `/auth/login` | Log in (sets session cookie) |
| `POST` | `/auth/logout` | Log out (clears cookie) |
| `GET` | `/api/v1/sources` | List available RSS sources |
| `POST` | `/api/v1/subscriptions` | Create a subscription |
| `GET` | `/api/v1/subscriptions` | List subscriptions |
| `PATCH` | `/api/v1/subscriptions/{id}` | Update a subscription |
| `DELETE` | `/api/v1/subscriptions/{id}` | Soft-delete a subscription |
| `POST` | `/api/v1/subscriptions/{id}/trigger` | Trigger pipeline (3/hour) |
| `GET` | `/api/v1/digests` | List digests |
| `GET` | `/api/v1/digests/{id}` | Digest detail with articles |
| `GET` | `/api/v1/metrics/pipeline` | 24h pipeline stats |

Interactive docs: **`/docs`**

---

## Project Structure

```
SmartDigest/
├── app/
│   ├── api/           # FastAPI routers (auth, sources, subscriptions, digests, metrics, jobs)
│   ├── middleware/    # JWT cookie auth + rate limiting
│   ├── models/        # SQLAlchemy ORM models
│   ├── schemas/       # Pydantic request/response schemas
│   ├── services/      # Business logic
│   │   ├── fetcher.py     # Concurrent async RSS fetching
│   │   ├── summariser.py  # Gemini batch summarisation with model fallback
│   │   ├── mailer.py      # Resend email delivery
│   │   ├── metrics.py     # SQL aggregates from pipeline_events
│   │   └── scheduler.py   # ARQ job functions + cron
│   ├── config.py      # Pydantic Settings — all env vars in one place
│   ├── database.py    # Async engine + session factory
│   ├── main.py        # App factory + Jinja2 HTML routes
│   └── cli.py         # Management commands (create_key, seed_sources)
├── templates/         # Jinja2 + HTMX dashboard
│   └── partials/      # Live-polled metric panels
├── alembic/           # Database migrations
├── services/
│   ├── web/railway.toml     # Railway web service config
│   └── worker/railway.toml  # Railway worker service config
├── worker.py          # ARQ worker entrypoint + cron registration
└── requirements.txt
```

---

## Design Notes

**Direct REST over SDK (Gemini)** — Calling the Gemini REST API directly via `httpx` removes the dependency on Google's Python SDK release cycle. The API surface used is stable, and the async HTTP client fits naturally into the event loop.

**Single-batch summarisation** — All articles for a digest are sent to Gemini in one prompt. This uses the large context window efficiently and costs one API call per digest regardless of article count — important for staying within free-tier RPM limits.

**Model fallback** — Gemini free-tier quotas are per-model, not shared. The summariser tries `gemini-2.0-flash` first, then falls back through `gemini-2.5-flash` and lite variants — maximising uptime without requiring a paid plan.

**Decoupled worker** — The web server never blocks on pipeline work. A trigger request enqueues a job to Redis and returns `202 Accepted` immediately. The worker picks it up asynchronously.

**Cookie-based JWT auth** — The app is browser-first (Jinja2 + HTMX), so httpOnly cookies are the natural auth mechanism. They’re sent automatically on every request, eliminating the need for JavaScript to inject Authorization headers. `SameSite=Lax` prevents CSRF on navigations.

**Plan column for future monetisation** — A `plan` field (`free`/`pro`) lives on the `users` table from day one. It’s not enforced yet, but it’s ready for gating features (e.g. more sources, higher trigger rate limits) without a schema migration.

---

## License

MIT

