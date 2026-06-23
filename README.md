<div align="center">

# 🧠 SmartDigest

### *AI-Powered Content Briefings — Built for Depth, Not Just Speed*

[![Live Demo](https://img.shields.io/badge/Live%20Demo-Railway-blueviolet?style=for-the-badge&logo=railway)](https://smartdigest-production.up.railway.app)
[![Python](https://img.shields.io/badge/Python-3.11+-blue?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)

> Stop drowning in tabs. SmartDigest fetches content from across the web, filters out the noise with a two-stage AI pipeline, and delivers a **personally relevant** briefing to your inbox — one that explains *why* each story matters to **you**.

**[🚀 Try the Live Demo](https://smartdigest-production.up.railway.app)**  ·  **[📖 API Docs](https://smartdigest-production.up.railway.app/docs)**

</div>

---

## ✨ Why SmartDigest is Different

Most newsletter tools give you a summary of what an article *says*.  
**SmartDigest tells you what it *means for you*.**

When you create a briefing, you don't just pick a topic — you describe your **intent**: what you're trying to learn, which keywords matter, examples of articles you find valuable, and what to ignore. The AI pipeline uses that intent at every stage:

| Stage | What happens |
|---|---|
| 🔍 **Fetch** | Concurrent async scraping across RSS feeds + a purpose-built Hacker News scraper that fetches full article text (not just headlines) |
| 🧹 **BM25 Lexical Filter** | Keyword-aware ranking drops clearly irrelevant articles before spending any AI quota |
| 🤖 **LLM Relevance Filter** | Gemini scores each remaining article 1–10 against your stated intent. Articles below the threshold are excluded. |
| ✍️ **Intent-Aware Summary** | The surviving articles are summarised in a single batched Gemini call — but not generically. The prompt instructs the model to act as *your knowledgeable advisor*, extracting what matters and explaining why. |
| 📧 **Delivery** | A clean HTML digest lands in your inbox via Resend |
| 📊 **Observability** | Every stage transition is written to `pipeline_events` — you can inspect durations, failure reasons, and throughput from the live dashboard |

---

## 🏗️ Architecture

```
Browser / API Client
        │
        ▼
┌─────────────────────────────┐      enqueue job       ┌───────────────────┐
│  FastAPI  (web service)     │ ─────────────────────► │    Redis Queue    │
│  Uvicorn · Jinja2 · HTMX   │                         └─────────┬─────────┘
│  SessionAuth · Rate Limit   │                                   │ dequeue
└────────────┬────────────────┘                                   ▼
             │ async read/write                ┌─────────────────────────────┐
             ▼                                 │      ARQ Worker             │
┌─────────────────────┐                        │  ┌─────────────────────┐   │
│    PostgreSQL 16     │ ◄──────────────────── │  │  1. Fetch (async)   │   │
│  SQLAlchemy 2 async  │                        │  │  2. BM25 Filter     │   │
└─────────────────────┘                        │  │  3. LLM Filter      │   │
                                               │  │  4. AI Summarise    │   │
                                               │  │  5. Email Deliver   │   │
                                               │  └─────────────────────┘   │
                                               └─────────────────────────────┘
```

The web server and background worker are **fully decoupled** — they share only the Redis queue and PostgreSQL. Every trigger returns `202 Accepted` immediately. The worker handles all long-running work.

---

## 🔥 Features

### 🎯 Intent-Driven Briefings
- Describe **what you want to learn**, not just a topic keyword
- Provide example articles that represent valuable content
- Set explicit **exclusion keywords** to suppress noise topics
- The AI pipeline passes your intent context through every stage

### ⚡ Production-Grade Pipeline
- Concurrent feed fetching with per-source scraper dispatch
- Fetches source articles newer than the last delivered digest before ranking
- **Purpose-built Hacker News scraper** — fetches full linked article text via `trafilatura`, not just RSS headlines
- Two-stage filtering: fast BM25 lexical pre-filter → LLM relevance scoring
- Single-batch Gemini summarisation (one API call per digest regardless of article count)
- **Model fallback chain**: `gemini-2.0-flash` → `gemini-2.5-flash` → lite variants — maximises uptime on free-tier quotas

### 🔐 Secure Authentication
- Email + password accounts with `bcrypt` password hashing
- JWT sessions in `httpOnly` cookies (`SameSite=Lax`)
- **Three-layer bfcache defence** — authenticated pages are never stored in browser history
- 3-day rolling sessions; `Cache-Control: no-store` on all protected HTML

### 📊 Full Observability
- Every pipeline stage (`fetch` / `filter` / `summarise` / `deliver`) logged to `pipeline_events`
- Live metrics panel on dashboard: 24h job counts, per-stage latency, last error — HTMX-polled every 5s
- `/api/v1/metrics/pipeline` endpoint backed by real SQL aggregates
- Structured JSON logging via `structlog` in production, pretty console in development

### 📱 Responsive Dashboard
- HTMX-powered UI — no JavaScript framework, no build step
- **Mobile bottom navigation bar** with safe-area insets for notched iPhones
- Schedule times shown in the **user's local timezone** (auto-detected via `Intl.DateTimeFormat`)
- Skeleton loading states, toast notifications, inline validation

### 🗓️ Scheduled Delivery
- ARQ cron job at 06:00 UTC enqueues all active briefings automatically
- Flexible schedule options: daily at 6 AM, 7 AM, 8 AM, noon, or 6 PM UTC
- Manual trigger via dashboard ("Run Now") — rate-limited to 3 runs/hour

### 🛠️ Developer-Friendly
- Dev mode: if `RESEND_API_KEY` is unset, emails are printed to console
- Full OpenAPI docs at `/docs` and `/redoc`
- CLI commands for seeding sources and creating users
- Alembic migrations with async support

---

## 🧰 Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Web framework | **FastAPI 0.115** | Async-native, auto-generated OpenAPI, excellent DX |
| ASGI server | **Uvicorn** | Production-ready, matches Railway's Procfile model |
| Database | **PostgreSQL 16** | JSONB for sources/keywords, partial indexes on active rows |
| ORM | **SQLAlchemy 2.0 (async)** | Fully async, typed `Mapped[]` columns |
| Migrations | **Alembic** | Async-configured, runs on every deploy |
| Task queue | **ARQ** | Async Redis queue — simpler than Celery, first-class async |
| Cache / broker | **Redis 7** | Backs both ARQ queue and `slowapi` rate limits |
| AI summarisation | **Google Gemini 2.0 Flash** | Direct REST via `httpx` — no SDK lock-in, stays in the async event loop |
| Email delivery | **Resend API** | 3,000 free emails/month, no credit card |
| HTTP client | **httpx (async)** | Async, connection pooling, timeout handling |
| RSS parsing | **feedparser** | Battle-tested, handles malformed feeds |
| Full-text extraction | **trafilatura** | Used by the HN scraper to pull article body from linked URLs |
| Frontend | **Jinja2 + HTMX + Tailwind CDN** | Server-rendered, zero build step, HTMX for partial updates |
| Rate limiting | **slowapi** | Redis-backed per-user limits |
| Structured logging | **structlog** | JSON in prod, pretty console in dev |
| Configuration | **Pydantic Settings** | Env-file + OS env, validated at startup |
| Auth | **PyJWT + bcrypt** | Signed httpOnly cookie sessions |
| Deployment | **Railway** | Two-service split: web + worker |

---

## 📐 Database Schema

```
users             — email, bcrypt hash, name, plan (free/pro), last_login_at
curated_sources   — pre-approved RSS feeds (seeded via CLI); name, rss_url, active
briefings         — user's configured feed: topic, intent_description, keywords[],
                    example_articles[], exclusion_keywords[], sources[], schedule, email
digests           — one record per pipeline run: briefing_id, status, delivered_at
digest_items      — one row per article: title, summary, source_url, item_url, fetch_duration_ms
pipeline_events   — observability log: stage × status × duration_ms × error_msg × item_count
```

Partial index on `briefings (user_id) WHERE active = true` — keeps scheduler query fast at scale.

---

## 🚀 Getting Started (Local)

### Prerequisites

- Python 3.11+
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

Create a `.env` file in the project root:

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/smartdigest
REDIS_URL=redis://localhost:6379
GEMINI_API_KEY=your_key_here
RESEND_API_KEY=your_key_here          # Omit entirely for dev mode (emails logged to console)
RESEND_FROM_EMAIL=onboarding@resend.dev
JWT_SECRET=generate-a-random-secret-here
ENV=development
```

### 4. Migrate and seed

```bash
alembic upgrade head
python -m app.cli seed_sources
```

### 5. Create your account

```bash
python -m app.cli create_user you@example.com yourpassword "Your Name"
```

### 6. Run both services

```bash
# Terminal 1 — Web server
uvicorn app.main:app --reload --port 8000

# Terminal 2 — Background worker
python worker.py
```

Open **http://localhost:8000** and log in.

---

## ☁️ Deploying on Railway

SmartDigest splits into two Railway services — each with its own `railway.toml`.

### Step 1 — Set up the project

1. Create a new Railway project
2. Add the **PostgreSQL** plugin → `DATABASE_URL` auto-injected
3. Add the **Redis** plugin → `REDIS_URL` auto-injected
4. Add a **GitHub service** (your repo) → name it **web**
5. Add a second **GitHub service** (same repo) → name it **worker**

### Step 2 — Point each service to its config file

| Service | Config File Path in Railway Settings |
|---|---|
| web | `services/web/railway.toml` |
| worker | `services/worker/railway.toml` |

### Step 3 — Environment variables (both services)

| Variable | Value |
|---|---|
| `DATABASE_URL` | Auto-injected by Railway Postgres |
| `REDIS_URL` | Auto-injected by Railway Redis |
| `GEMINI_API_KEY` | Your Google AI Studio key |
| `RESEND_API_KEY` | Your Resend key |
| `RESEND_FROM_EMAIL` | Your verified sender email |
| `JWT_SECRET` | Random secret — `openssl rand -hex 32` |
| `ENV` | `production` |

### Step 4 — Deploy

Push to GitHub. Railway runs `alembic upgrade head && python -m app.cli seed_sources` automatically before starting — the database is always up to date on every deploy.

---

## 📡 API Reference

Authentication uses httpOnly JWT session cookies set at login. All API endpoints under `/api/v1/` require an active session.

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/auth/register` | ✗ | Create a new account |
| `POST` | `/auth/login` | ✗ | Log in — sets `sd_session` cookie |
| `POST` | `/auth/logout` | ✓ | Log out — clears cookie |
| `GET` | `/api/v1/sources` | ✗ | List all curated RSS sources |
| `POST` | `/api/v1/briefings` | ✓ | Create a briefing |
| `GET` | `/api/v1/briefings` | ✓ | List your briefings |
| `GET` | `/api/v1/briefings/{id}` | ✓ | Get a single briefing |
| `PATCH` | `/api/v1/briefings/{id}` | ✓ | Update a briefing |
| `DELETE` | `/api/v1/briefings/{id}` | ✓ | Soft-delete a briefing |
| `POST` | `/api/v1/briefings/{id}/trigger` | ✓ | Manually trigger pipeline (3/hour) |
| `GET` | `/api/v1/digests` | ✓ | List digests |
| `GET` | `/api/v1/digests/{id}` | ✓ | Digest detail with articles |
| `GET` | `/api/v1/metrics/pipeline` | ✓ | 24h pipeline stats |
| `GET` | `/jobs/{job_id}` | ✓ | Poll background job status |

Interactive API explorer: **[/docs](https://smartdigest-production.up.railway.app/docs)**

---

## 📁 Project Structure

```
SmartDigest/
├── app/
│   ├── api/
│   │   ├── auth.py          # Register, login, logout
│   │   ├── briefings.py     # Full CRUD + trigger endpoint
│   │   ├── digests.py       # Digest list + detail
│   │   ├── jobs.py          # ARQ job status polling
│   │   ├── metrics.py       # Pipeline health aggregates
│   │   └── sources.py       # Curated source listing
│   │
│   ├── middleware/
│   │   ├── auth.py          # JWT cookie middleware + bfcache headers
│   │   └── rate_limit.py    # slowapi limiter setup
│   │
│   ├── models/              # SQLAlchemy 2.0 ORM models
│   │   ├── user.py
│   │   ├── briefing.py      # Core entity — intent + sources + schedule
│   │   ├── digest.py
│   │   ├── digest_item.py
│   │   ├── pipeline_event.py
│   │   └── curated_source.py
│   │
│   ├── schemas/             # Pydantic request/response models
│   │
│   ├── services/
│   │   ├── auth.py          # bcrypt hashing + JWT creation/verification
│   │   ├── fetcher.py       # Concurrent multi-source article fetching
│   │   ├── intent.py        # Intent context builder
│   │   ├── mailer.py        # Resend email delivery
│   │   ├── metrics.py       # SQL aggregates from pipeline_events
│   │   ├── scheduler.py     # ARQ job: full pipeline (fetch→filter→summarise→deliver)
│   │   ├── summariser.py    # Gemini batch summarisation + model fallback
│   │   │
│   │   ├── filters/
│   │   │   ├── bm25.py          # BM25 lexical pre-filter
│   │   │   ├── heuristic.py     # Legacy keyword-weighted pre-filter
│   │   │   └── llm_relevance.py # Gemini 1–10 relevance scoring
│   │   │
│   │   └── scrapers/
│   │       ├── base.py          # BaseScraper interface + RawArticle dataclass
│   │       ├── hackernews.py    # Full-text HN scraper (trafilatura)
│   │       └── rss_generic.py  # Generic feedparser-based scraper
│   │
│   ├── config.py            # Pydantic Settings (all env vars)
│   ├── database.py          # Async engine + session factory
│   ├── main.py              # FastAPI app factory + HTML routes
│   └── cli.py               # Management commands
│
├── templates/               # Jinja2 + HTMX templates
│   ├── base.html            # Layout: sidebar, mobile nav, bfcache defence
│   ├── dashboard.html       # Briefing cards + recent digests + create drawer
│   ├── digest_detail.html   # Per-digest article view
│   ├── digests.html         # Full digest history
│   ├── metrics.html         # Pipeline observability page
│   ├── login.html
│   └── partials/
│       ├── briefing_card.html    # HTMX-swappable card
│       ├── digest_row.html
│       ├── metrics_panel.html    # Polled every 5s
│       └── metrics_full.html
│
├── alembic/                 # Database migrations (async-configured)
│   └── versions/
│
├── services/
│   ├── web/railway.toml     # Railway web service config
│   └── worker/railway.toml  # Railway worker service config
│
├── worker.py                # ARQ worker entrypoint + cron registration
├── requirements.txt
└── Procfile                 # Fallback for single-service deployments
```

---

## 🧠 Design Decisions

**Intent context propagates through the entire pipeline**  
Most digest tools summarise articles generically. SmartDigest passes the user's `intent_description`, `keywords`, and `example_articles` to both the LLM filter and the summariser. The model is prompted to act as a *knowledgeable advisor*, not a compression algorithm.

**Two-stage filtering minimises cost without sacrificing quality**  
The BM25 lexical filter runs first for free, with hard exclusion keywords applied before ranking. Only the strongest lexical candidates are scored by the LLM. This keeps Gemini API usage proportional to likely relevance, not feed volume.

**Coverage-first retrieval**
Fetch uses the last delivered digest timestamp as its lower bound and avoids global pre-ranking caps. BM25 then ranks the full time-window candidate set generously, so the LLM is reserved for final intent-aware precision instead of compensating for missed fetch coverage.

**Direct REST over SDK (Gemini)**  
Calling the Gemini REST API directly via `httpx` removes SDK release-cycle dependency, and `httpx.AsyncClient` integrates naturally into FastAPI's async event loop without thread pool overhead.

**Single-batch summarisation**  
All articles for a digest are summarised in one Gemini call. This uses the large context window efficiently and costs exactly one API call per digest run — critical for staying within free-tier RPM limits.

**Model fallback chain**  
Free-tier Gemini quotas are per-model, not shared. The summariser tries `gemini-2.0-flash` → `gemini-2.5-flash` → lite variants in sequence. A quota exhaustion on one model doesn't fail the digest — it silently upgrades to the next available model.

**Decoupled worker**  
The web server never waits on pipeline work. A trigger enqueues a job to Redis and returns `202 Accepted` in milliseconds. The worker picks it up asynchronously. Both services share only the queue and the database — they can be scaled, restarted, or redeployed independently.

**Cookie-based JWT auth**  
SmartDigest is browser-first (Jinja2 + HTMX), so `httpOnly` cookies are the natural auth mechanism. They're sent automatically on every request without any JavaScript, and `SameSite=Lax` protects against CSRF on navigations.

**Purpose-built HN scraper**  
Hacker News RSS feeds contain only titles and links. The dedicated `HackerNewsScraper` fetches each linked article and extracts full body text using `trafilatura`, dramatically improving summarisation quality for HN sources.

**Plan column for future monetisation**  
A `plan` field (`free`/`pro`) lives on `users` from day one. It's not enforced yet but avoids a schema migration when gating features like higher trigger limits or more sources per briefing.

---

## 🗺️ Roadmap

- [ ] Webhook delivery on digest completion (HMAC-SHA256 signed)
- [ ] Per-briefing custom LLM prompt overrides
- [ ] Slack / Discord delivery channel
- [ ] CSV export of digest history
- [ ] Per-user rate-limit dashboard
- [ ] Custom RSS source addition (beyond curated list)
- [ ] Unit + integration test suite (pytest-asyncio)

---

## 📄 License

MIT — see [LICENSE](LICENSE)

---

<div align="center">

Built with ☕ by [Burhan Ahmad Khan](https://github.com/BuriAhmad)

⭐ Star this repo if you found it useful
