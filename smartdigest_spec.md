# SmartDigest — Software Specification Document
*Async Content Pipeline with Delivery Tracking*

**Version:** 1.0 — MVP | **Status:** Implementation Ready
**Stack:** FastAPI · PostgreSQL · ARQ · Redis
**Frontend:** HTMX · Jinja2 · Tailwind CDN
**Deployment:** Railway (free tier)

---

## A · Product Definition

### A1 · Product Summary

SmartDigest is a backend-first web application that lets users subscribe to topics (e.g., "AI research", "startup funding", "Python ecosystem"), configure content sources (curated RSS feeds), and receive a daily email digest in which each item has been summarised by an LLM. The system runs an asynchronous multi-stage pipeline — fetch → summarise → deliver — and exposes a lightweight dashboard where users can see pipeline health, delivery history, and per-subscription analytics.

The project is purpose-built as a portfolio demonstration. Every technical decision is made to maximise visible backend depth: the pipeline architecture, the observability data model, the rate-limiting middleware, and the structured logging are all first-class concerns rather than afterthoughts.

### A2 · Target Users

| User Type | Description |
|---|---|
| Primary: Developer/Student | A single user who owns the deployment, creates API keys for themselves, configures subscriptions, and monitors the dashboard. For MVP this is the sole persona. |
| Recruiter/Reviewer | A technical recruiter or senior engineer who visits the live URL to evaluate system design. They read the dashboard, not the codebase. |

### A3 · Problems Solved

- Manual content curation is time-consuming — users check 5–10 sources daily.
- Raw RSS content is verbose and difficult to scan quickly.
- Existing digest tools (e.g., Mailbrew, Feedly) are closed ecosystems with no backend observability.
- This project lets a developer demonstrate a real pipeline system they built and operated.

### A4 · Value Proposition

> A working async pipeline with a visible observability layer — not a CRUD app. Every architectural decision has a demo-ready rationale.

---

## B · Scope Definition

### B1 · MVP Scope

- API key issuance and validation middleware
- Subscription management (create / list / delete)
- Three-stage async pipeline via ARQ: fetch → summarise → deliver
- `pipeline_events` table populated at every stage transition
- Manual pipeline trigger endpoint (rate-limited 3 req/hour per key)
- Daily scheduled pipeline run for all active subscriptions
- `/metrics/pipeline` and `/metrics/usage` endpoints backed by DB aggregates
- HTMX dashboard: subscriptions, digest history, pipeline health panel
- Structured JSON logging via structlog throughout
- Single Dockerfile, deployed on Railway with Postgres + Redis add-ons

### B2 · Out of Scope (MVP)

- OAuth / social login
- Multi-user tenancy with isolated data
- Webhook delivery (reserved as fast-follow)
- CSV export
- Custom LLM prompt templates per subscription
- Mobile-responsive polish (functional responsiveness only)
- Unit / integration test suite (interview talking point, not shipped code)

### B3 · Future Enhancements (V2)

- Webhook on digest completion with HMAC-SHA256 signing
- Per-subscription custom prompts
- CSV export of digest history
- Per-user rate limit dashboard
- Slack / Discord delivery channels in addition to email

---

## C · User Roles

For MVP there is a single role: API Key Owner. All authenticated endpoints require a valid API key in the Authorization header. There is no admin role distinct from the owner.

| Role | Auth Method | Permissions |
|---|---|---|
| API Key Owner | Bearer token (raw key) | Full CRUD on own subscriptions; trigger pipeline; view digests; view metrics |
| Unauthenticated | None | GET / (dashboard HTML only). All API endpoints return 401. |

> **Assumption:** API key issuance is handled via a bootstrapped `POST /keys` endpoint. For demo purposes the initial key is created via a one-time CLI command (`python -m app.cli create_key`) and stored in an env var for the dashboard.

---

## D · Detailed Features

### D1 · API Key Management

- User can issue an API key via `POST /keys`.
- The system generates a random 32-byte hex token, stores a SHA-256 hash in the database, and returns the plaintext key once — never again.
- A key has a 4-character visible prefix (stored plaintext) used for display in the dashboard (e.g., "a3f2…").
- A key records `last_used_at`, updated on every authenticated request.
- A key can be revoked via `DELETE /keys/{prefix}`, which sets `revoked_at`.

### D2 · Subscription Management

- A subscription belongs to one API key owner.
- Fields: topic label (free text), list of selected sources from curated list (JSONB array of RSS URLs), email delivery address, cron schedule string (default: `"0 7 * * *"` — 7 AM daily), active flag.
- CRUD: create, list, get, update, soft-delete (sets `active=false`).
- Validation: at least one source required; email must be valid format; cron string validated against a known pattern set.
- Sources are selected from the `curated_sources` table — users pick checkboxes, not raw URLs.

### D3 · Pipeline — Fetch Stage

- For each source URL in the subscription, the fetcher makes an HTTP GET request with a 10-second timeout.
- RSS feeds are parsed; up to 5 most recent items are extracted per source.
- Each item yields: title, url, published_at, raw_content (first 1000 chars of description/content).
- A `pipeline_events` row is written for `stage=fetch` with status, duration_ms, and item_count.
- On failure: the event row records `error_msg`, `status=failed`. The pipeline does not proceed to summarise for that source; other sources continue.

### D4 · Pipeline — Summarise Stage

- For each fetched item, the summariser calls the Anthropic API (`claude-haiku-4-5`) with a fixed system prompt.
- **System prompt:** `"You are a concise newsletter editor. Summarise the following article in 2–3 sentences. Be direct. No filler phrases."`
- Concurrency: `asyncio.gather` with a semaphore limiting to 3 simultaneous calls.
- LLM call timeout: 30 seconds via `asyncio.wait_for`. On timeout or API error: `summary = "[Summary unavailable]"`, error logged.
- A `pipeline_events` row is written for `stage=summarise`.

### D5 · Pipeline — Deliver Stage

- The mailer composes an HTML email from a Jinja2 template, grouping digest items by source.
- Email is sent via Resend API (free tier: 3,000 emails/month, no credit card). Fallback: log email content to stdout if `RESEND_API_KEY` is not set (development mode).
- On successful delivery: `digest.status = delivered`, `digest.delivered_at = now()`.
- On failure: `digest.status = failed`, `pipeline_events` records the error. No automatic retry in MVP.
- A `pipeline_events` row is written for `stage=deliver`.

### D6 · Manual Trigger

- `POST /subscriptions/{id}/trigger` runs the full pipeline immediately for that subscription as an ARQ background job.
- Rate limited: 3 requests per hour per API key using a Redis token bucket via slowapi.
- Returns `202 Accepted` with a `job_id`. The client can poll `GET /jobs/{job_id}` for status.

### D7 · Scheduler

- ARQ includes a cron task that runs daily at 6 AM UTC.
- The task queries all active subscriptions and enqueues one pipeline job per subscription.
- For MVP, only "daily at 7 AM" is supported. Schedule field is stored for V2 expansion.

### D8 · Observability — Pipeline Metrics

- `GET /metrics/pipeline` returns aggregate statistics derived entirely from the `pipeline_events` table.
- Data returned: total jobs (24h), jobs by status (queued/processing/done/failed), average duration per stage, error rate per stage, last 10 failed events with error_msg.

### D9 · Observability — Usage Metrics

- `GET /metrics/usage` returns per-key statistics: total API calls (all time and 24h), `last_used_at`, manual trigger count, subscription count.
- API call count is incremented in auth middleware on every successful authenticated request.

### D10 · Curated Sources

- A `curated_sources` table is seeded at startup with 10–15 RSS feeds.
- The create-subscription form displays these as labelled checkboxes — not a URL text input.
- Selected sources are stored as their RSS URL strings in `subscriptions.sources JSONB`.

**Seed data:**

| Name | RSS URL |
|---|---|
| Hacker News | https://news.ycombinator.com/rss |
| TechCrunch | https://techcrunch.com/feed/ |
| MIT Tech Review | https://www.technologyreview.com/feed/ |
| The Verge | https://www.theverge.com/rss/index.xml |
| Wired | https://www.wired.com/feed/rss |
| Ars Technica | https://feeds.arstechnica.com/arstechnica/index |
| VentureBeat | https://venturebeat.com/feed/ |
| InfoQ | https://www.infoq.com/feed/ |
| Dev.to | https://dev.to/feed |
| Simon Willison | https://simonwillison.net/atom/everything/ |

---

## E · Frontend Specification

> **Tech:** Jinja2 templates served by FastAPI. HTMX for dynamic interactions. Tailwind CSS via CDN (no build step). Minimal vanilla JS only where HTMX is insufficient.

### E1 · Screen Inventory

| Screen | Route | Purpose |
|---|---|---|
| Dashboard | `GET /` | Primary view: subscription list, recent digests, pipeline health |
| Digest Detail | `GET /digests/{id}` | Shows all digest items with summaries for one digest run |
| Metrics Panel | `GET /dashboard/metrics` | Pipeline health fragment, HTMX-polled every 5s |
| API Key Setup | `GET /setup` | One-time setup: create API key, copy plaintext, link to dashboard |

### E2 · Dashboard (/)

**Layout:**
- Topbar: app name "SmartDigest", key prefix display (e.g. "Key: a3f2…"), "Docs" link, "+ New subscription" button.
- Two-column layout: left 60% (subscriptions + digests), right 40% (pipeline health).

**Subscription Cards (left panel):**
- One card per active subscription.
- Shows: topic label, source chips (coloured pill badges for each selected source), last delivered timestamp (or "Never"), status badge (active/paused).
- Actions: "Trigger Now" button (POST via HTMX), "Edit", "Delete".
- Empty state: "No subscriptions yet. Add your first topic."

**Recent Digests Table (left panel, below cards):**
- Columns: Topic, Created At, Status, Items, Action.
- Status badges: queued (gray), processing (yellow), delivered (green), failed (red).
- "View" link opens Digest Detail page.
- Shows last 10 digests across all subscriptions.
- Empty state: "No digests yet. Trigger a pipeline run to get started."

**Pipeline Health Panel (right panel):**
- HTMX polling: `hx-get="/dashboard/metrics" hx-trigger="every 5s" hx-swap="innerHTML"`.
- Shows: jobs in last 24h (total / failed / success), avg latency per stage as bar chart, last error with timestamp.
- Loading state: skeleton bars. Error state: "Metrics unavailable" with retry link.

**Create Subscription Modal:**
- Triggered by "+ New subscription" button.
- Fields: Topic Label (text, required), Sources (checkbox list from `curated_sources`, min 1 required), Email (email input, required), Schedule (select: "Daily 7AM" only for MVP).
- Submit: `POST /subscriptions` via HTMX.
- Validation errors displayed inline under each field.
- On success: modal closes, new subscription card appended to list.

**Trigger Confirmation:**
- Clicking "Trigger Now" shows inline spinner on button.
- On 202: toast — "Pipeline queued successfully."
- On 429: toast — "Rate limit reached. Try again in X minutes."

### E3 · Digest Detail (/digests/{id})

- Header: subscription topic, digest `created_at`, status badge, delivery timestamp.
- Item list: title (linked to source URL), source name, summary text, fetch latency.
- Back link to Dashboard.
- Empty state: "No items in this digest."

### E4 · API Key Setup (/setup)

- Shown on first visit (no key exists).
- "Generate API Key" button → `POST /keys`.
- After generation: displays plaintext key in monospace box with "Copy" button.
- Warning: "This key will not be shown again. Copy it now."
- "Continue to Dashboard" button.

### E5 · Global States

| State | Handling |
|---|---|
| Loading | HTMX hx-indicator spinner. Buttons disabled during in-flight requests. |
| Empty | Each list/table has a dedicated empty state with a call to action. |
| Error (API) | HTMX response with HTTP 4xx/5xx swaps in an error fragment. |
| 401 Unauth | Server returns HX-Redirect header pointing to /setup. |

---

## F · User Flows

### F1 · First-Time Setup

1. User visits live URL.
2. Server checks for API key cookie → not found → renders `/setup`.
3. User clicks "Generate API Key" → `POST /keys` → receives plaintext key.
4. User copies key, clicks "Continue to Dashboard".
5. Dashboard renders with empty subscription list.

### F2 · Create Subscription + Trigger

1. User clicks "+ New Subscription".
2. Fills form: topic, source checkboxes, email, schedule.
3. Submits → `POST /subscriptions` → card appears in list.
4. Clicks "Trigger Now" on new card.
5. `POST /subscriptions/{id}/trigger` → 202, job_id returned.
6. Pipeline runs in background:
   - Fetch stage → digest_items rows created, pipeline_events written.
   - Summarise stage → summaries populated, pipeline_events written.
   - Deliver stage → email sent, digest.status=delivered, pipeline_events written.
7. Pipeline health panel auto-refreshes → shows completed job.
8. Recent digests table shows new row with "delivered" badge.

### F3 · Digest Review

1. User sees "delivered" digest in Recent Digests table.
2. Clicks "View" → navigates to `/digests/{id}`.
3. Reads summaries, clicks source links to read originals.
4. Clicks "Back to Dashboard".

### F4 · Edge — Trigger Rate Limit

1. User clicks "Trigger Now" more than 3 times in one hour.
2. `POST /subscriptions/{id}/trigger` returns 429 with `retry-after` seconds.
3. Toast: "Rate limit reached. Try again in X minutes."

### F5 · Edge — Pipeline Failure

1. Fetch stage fails for one source (timeout, invalid RSS).
2. `pipeline_events` row written: stage=fetch, status=failed, error_msg recorded.
3. Other sources continue processing.
4. If ALL sources fail: `digest.status=failed`, no email sent.
5. Dashboard pipeline panel shows failure; last error visible in metrics.

---

## G · Backend Specification

### G1 · Application Factory

- FastAPI app created in `app/main.py` with lifespan context manager.
- Lifespan: creates DB connection pool, initialises ARQ connection, configures structlog.
- All routers registered in `main.py` with `/api/v1` prefix except HTML routes.

### G2 · Auth Middleware

- `app/middleware/auth.py`: Starlette `BaseHTTPMiddleware` subclass.
- Extracts Bearer token from Authorization header.
- Computes SHA-256 of token, queries `api_keys` where `key_hash = ? AND revoked_at IS NULL`.
- On match: sets `request.state.owner_key_id`, updates `last_used_at`, increments `api_call_count`.
- On miss: returns 401 JSON response.
- Excluded paths: `POST /keys`, `GET /`, `GET /setup`, `GET /dashboard/metrics`.

### G3 · Rate Limit Middleware

- Uses slowapi (FastAPI-compatible, Redis-backed).
- Applied only to `POST /subscriptions/{id}/trigger`.
- Limit: `"3/hour"` keyed by `api_key_id` from `request.state`.
- On breach: 429 response with `Retry-After` header.

### G4 · Services

**FetcherService (`app/services/fetcher.py`)**
- `fetch_sources(subscription_id, sources: list[str]) → list[FetchedItem]`
- Uses `httpx.AsyncClient` with 10s connect + 10s read timeouts.
- RSS parsing via feedparser. Up to 5 items per source.
- Writes `pipeline_events` rows. Returns items list even on partial failure.

**SummariserService (`app/services/summariser.py`)**
- `summarise_items(digest_id, items: list[FetchedItem]) → list[SummarisedItem]`
- Calls Anthropic API (`claude-haiku-4-5`) per item.
- `asyncio.gather` with `asyncio.Semaphore(3)` for concurrency control.
- Hard timeout 30s per call via `asyncio.wait_for`.
- On failure: `summary = "[Summary unavailable]"`. Pipeline continues.
- Writes `pipeline_events` rows.

**MailerService (`app/services/mailer.py`)**
- `deliver_digest(digest_id, email: str, items: list[SummarisedItem]) → bool`
- Renders Jinja2 email template. Calls Resend API via httpx.
- If `RESEND_API_KEY` not set: logs rendered HTML to structlog at INFO (dev mode).
- Writes `pipeline_events` row.

**MetricsService (`app/services/metrics.py`)**
- `get_pipeline_summary(db) → PipelineMetrics`
- `get_usage_summary(db, key_id) → UsageMetrics`
- All logic is SQLAlchemy aggregate queries on `pipeline_events` and `api_keys`.
- No external metrics infrastructure.

**SchedulerService (`app/services/scheduler.py`)**
- ARQ cron task: `enqueue_scheduled_digests()` runs daily at 06:00 UTC.
- Queries all `active=true` subscriptions, enqueues one ARQ job per subscription.

### G5 · ARQ Worker (worker.py)

- Worker entrypoint exposes `WorkerSettings` with `redis_settings`, `functions` list, `cron_jobs`.
- Job function: `run_pipeline(ctx, subscription_id: int)`.
- Job creates a digest row (`status=queued`), calls fetch → summarise → deliver in sequence, updates digest status throughout.
- Any unhandled exception sets `digest.status=failed` and writes a `pipeline_events` error row.

### G6 · Structured Logging

- structlog configured at app startup with JSON renderer for production, ConsoleRenderer locally (detected via `ENV` variable).
- Every service function binds context (`subscription_id`, `digest_id`, `stage`) to the logger.
- No bare `print()` calls anywhere in the codebase.

---

## H · API Specification

> All API endpoints are prefixed `/api/v1`. All requests and responses use `application/json`. Auth required unless noted. Errors follow `{ "detail": "message" }` format.

### H1 · Keys

**`POST /api/v1/keys`** — Issue API Key (no auth required)
- Request: none
- Response 201: `{ "key": "<plaintext>", "prefix": "a3f2", "created_at": "..." }`
- Error 409: key already exists

**`DELETE /api/v1/keys/{prefix}`** — Revoke API Key (auth required)
- Response 204: no content
- Error 404: key not found

### H2 · Subscriptions

**`POST /api/v1/subscriptions`**
- Request: `{ "topic": str, "sources": [str], "email": str, "schedule": "0 7 * * *" }`
- Response 201: full subscription object
- Error 422: validation failure

**`GET /api/v1/subscriptions`**
- Response 200: array of subscription objects for authenticated key owner

**`GET /api/v1/subscriptions/{id}`**
- Response 200: subscription object with last 5 digests included
- Error 404: not found or not owned by caller

**`PATCH /api/v1/subscriptions/{id}`**
- Request: partial subscription fields
- Response 200: updated subscription object
- Error 404: not owned

**`DELETE /api/v1/subscriptions/{id}`**
- Soft delete (sets `active=false`)
- Response 204

**`POST /api/v1/subscriptions/{id}/trigger`**
- Rate limit: 3/hour per API key
- Response 202: `{ "job_id": "arq:job:<uuid>", "status": "queued" }`
- Error 429: `{ "detail": "Rate limit exceeded", "retry_after": 1842 }`
- Error 404: subscription not found or inactive

### H3 · Digests

**`GET /api/v1/digests`**
- Query params: `subscription_id` (filter), `status` (filter), `limit` (default 20), `offset`
- Response 200: paginated list with total count

**`GET /api/v1/digests/{id}`**
- Response 200: digest with all `digest_items`
- Error 404: not owned by caller

### H4 · Jobs

**`GET /api/v1/jobs/{job_id}`**
- Response 200: `{ "job_id": "...", "status": "queued|in_progress|complete|failed", "result": {...} }`
- Queries ARQ Redis keys directly

### H5 · Metrics

**`GET /api/v1/metrics/pipeline`** (auth required)
```json
{
  "period_hours": 24,
  "total_jobs": 12,
  "by_status": { "done": 10, "failed": 2 },
  "stage_avg_ms": { "fetch": 820, "summarise": 4200, "deliver": 310 },
  "last_error": { "stage": "fetch", "msg": "...", "at": "..." }
}
```

**`GET /api/v1/metrics/usage`** (auth required)
```json
{
  "key_prefix": "a3f2",
  "total_api_calls": 142,
  "calls_24h": 18,
  "last_used_at": "...",
  "trigger_count": 5,
  "subscription_count": 3
}
```

---

## I · Data Model / Schema

### Entity Overview

| Table | Purpose |
|---|---|
| `api_keys` | Authentication credentials and usage counters |
| `curated_sources` | App-managed list of RSS feeds users can select from |
| `subscriptions` | User-defined topic pipelines with selected sources and schedule |
| `digests` | One record per pipeline run, tracking overall status |
| `digest_items` | One row per fetched+summarised content item within a digest |
| `pipeline_events` | Immutable audit log of every stage execution — the observability backbone |

### api_keys

| Column | Type | Notes |
|---|---|---|
| id | SERIAL PK | |
| prefix | VARCHAR(4) | First 4 chars of plaintext key. Stored plaintext. |
| key_hash | VARCHAR(64) | SHA-256 hex. UNIQUE NOT NULL. Indexed. |
| api_call_count | INTEGER | Incremented by auth middleware. Default 0. |
| last_used_at | TIMESTAMPTZ | Updated by auth middleware on each request. |
| created_at | TIMESTAMPTZ | DEFAULT now() |
| revoked_at | TIMESTAMPTZ | NULL = active. Set on revocation. |

### curated_sources

| Column | Type | Notes |
|---|---|---|
| id | SERIAL PK | |
| name | VARCHAR(100) | Display name (e.g. "Hacker News") |
| rss_url | TEXT | The actual feed URL. UNIQUE NOT NULL. |
| active | BOOLEAN | Default TRUE. FALSE = hidden from selection. |
| created_at | TIMESTAMPTZ | DEFAULT now() |

### subscriptions

| Column | Type | Notes |
|---|---|---|
| id | SERIAL PK | |
| api_key_id | INT FK | → api_keys.id. NOT NULL. |
| topic | VARCHAR(200) | Free text label. |
| sources | JSONB | Array of RSS URL strings. Min 1 enforced at app layer. |
| email | VARCHAR(255) | Delivery address. NOT NULL. |
| schedule | VARCHAR(50) | Cron string. Default "0 7 * * *". |
| active | BOOLEAN | Default TRUE. FALSE = soft-deleted. |
| created_at | TIMESTAMPTZ | DEFAULT now() |

> **Index:** `CREATE INDEX idx_subscriptions_api_key ON subscriptions(api_key_id) WHERE active = true;`

### digests

| Column | Type | Notes |
|---|---|---|
| id | SERIAL PK | |
| subscription_id | INT FK | → subscriptions.id. NOT NULL. |
| status | VARCHAR(20) | queued \| processing \| delivered \| failed. Default queued. |
| created_at | TIMESTAMPTZ | When the pipeline job was enqueued. |
| delivered_at | TIMESTAMPTZ | NULL until email confirmed sent. |

> **Index:** `CREATE INDEX idx_digests_subscription ON digests(subscription_id, created_at DESC);`

### digest_items

| Column | Type | Notes |
|---|---|---|
| id | SERIAL PK | |
| digest_id | INT FK | → digests.id. NOT NULL. |
| source_url | TEXT | The source URL this item came from. |
| title | TEXT | |
| item_url | TEXT | Link to original article. |
| raw_content | TEXT | First 1000 chars. Truncated at app layer. |
| summary | TEXT | LLM-generated. NULL until summarise stage completes. |
| fetch_duration_ms | INTEGER | |
| published_at | TIMESTAMPTZ | From RSS feed. Nullable. |

### pipeline_events ← observability backbone

Every pipeline stage writes an immutable row here. The metrics endpoint queries this table directly. No external metrics tooling required.

| Column | Type | Notes |
|---|---|---|
| id | SERIAL PK | |
| digest_id | INT FK | → digests.id. Nullable (for scheduler-level events). |
| stage | VARCHAR(20) | fetch \| summarise \| deliver \| scheduler |
| status | VARCHAR(20) | started \| success \| failed |
| duration_ms | INTEGER | Wall time for this stage. NULL for started rows. |
| error_msg | TEXT | NULL on success. Exception message on failure. |
| item_count | INTEGER | Items processed in this stage. |
| created_at | TIMESTAMPTZ | DEFAULT now(). Never updated. |

> **Indexes:**
> ```sql
> CREATE INDEX idx_pipeline_events_digest ON pipeline_events(digest_id);
> CREATE INDEX idx_pipeline_events_created ON pipeline_events(created_at DESC);
> ```

---

## J · Auth + Security

### Authentication Approach

- API key auth. User receives plaintext key once (`POST /keys`). Key is hashed with SHA-256 and stored.
- Every API request sends: `Authorization: Bearer <plaintext_key>`.
- Middleware computes SHA-256 of incoming token and compares to stored hash using `hmac.compare_digest` (timing-safe).

### Key Storage

- Plaintext key: never stored, never logged, returned once at creation.
- `key_hash`: indexed SHA-256 hex string in database.
- `prefix`: 4-character display string, not secret.

### Security Protections

| Protection | Implementation |
|---|---|
| Timing-safe key compare | `hmac.compare_digest()` in auth middleware |
| Rate limiting | slowapi, Redis-backed, per-key, on trigger endpoint |
| HTTPS | Enforced by Railway (TLS termination at proxy) |
| No secret in logs | structlog context never includes key or key_hash |
| Input validation | Pydantic schemas on all request bodies |
| SQL injection | SQLAlchemy ORM with parameterised queries throughout |

---

## K · Non-Functional Requirements

| Concern | Target / Approach |
|---|---|
| API Response Time | p95 < 200ms for all synchronous endpoints |
| Pipeline Throughput | Single ARQ worker; sufficient for demo workload |
| Observability | Every pipeline stage writes pipeline_events. Structured JSON logs to stdout. |
| Reliability | Fetch failures isolated per source. Single source failure doesn't kill the digest. |
| Maintainability | Services never import from each other. One-way graph: routes → services → models. |
| Scalability | Not a concern for MVP. Design doesn't preclude horizontal scaling. |
| Environment Parity | Dockerfile ensures local dev and production use identical runtime. |

---

## L · Edge Cases + Failure Handling

| Scenario | Handling |
|---|---|
| Invalid RSS URL | feedparser returns empty. pipeline_events: status=failed, error_msg="No items parsed". Other sources continue. |
| Fetch timeout (10s) | httpx raises ReadTimeout. Caught, pipeline_events written, source skipped. |
| LLM API timeout (30s) | asyncio.wait_for raises TimeoutError. summary = "[Summary unavailable]". Pipeline continues. |
| LLM API rate limit | Anthropic returns 429. Caught as APIStatusError. summary = "[Summary unavailable]". Logged. |
| Email delivery failure | Resend API error: digest.status=failed. pipeline_events error written. No retry in MVP. |
| All sources fail (fetch) | No digest_items created. Summarise/deliver skipped. digest.status=failed. |
| Duplicate trigger | Rate limiter prevents >3/hour. No further deduplication in MVP. |
| Invalid API key | Middleware returns 401 `{ "detail": "Invalid or revoked API key" }`. |
| Missing Authorization header | Middleware returns 401 `{ "detail": "Authorization header required" }`. |
| Subscription not owned by key | Returns 404 (not 403) to avoid enumeration. |
| Empty metrics (no jobs run) | /metrics/pipeline returns zero-value struct, not 404. Dashboard shows "No data yet." |
| Redis unavailable | Rate limiter fails open (allows request), logs warning. ARQ enqueue fails; returns 503. |

---

## M · Recommended Stack

| Layer | Choice | Justification |
|---|---|---|
| Backend | FastAPI + Python 3.12 | Fixed requirement. Async-native. |
| Database | PostgreSQL 16 | Fixed. JSONB for sources array, full SQL for aggregates. |
| ORM | SQLAlchemy 2.x (async) | Async session, type-safe, Alembic migrations included. |
| Task Queue | ARQ | Async-native, Redis-backed, far lighter than Celery. |
| Cache / Rate Limit | Redis 7 | ARQ broker + slowapi backend. Single shared instance. |
| Frontend | HTMX + Jinja2 + Tailwind CDN | No build step, served from FastAPI, minimal JS. |
| Email | Resend API | 3k free emails/month, no credit card, Python SDK. |
| LLM | Anthropic claude-haiku-4-5 | Cheapest Anthropic model. Fast. Sufficient for summaries. |
| HTTP Client | httpx (async) | Async, timeout control, used in fetcher and mailer. |
| RSS Parsing | feedparser | Battle-tested, handles malformed feeds gracefully. |
| Rate Limiting | slowapi | FastAPI-native wrapper over limits library. |
| Logging | structlog | JSON renderer in prod, ConsoleRenderer locally. |
| Migrations | Alembic | Standard with SQLAlchemy. |
| Deployment | Railway | No sleep on free tier ($5/mo credit). git push deploys. |
| Container | Docker (single Dockerfile) | Explicit runtime, resume signal, required for Railway. |

---

## N · Repository / Folder Structure

```
smartdigest/
├── app/
│   ├── main.py                  # FastAPI app factory + lifespan
│   ├── config.py                # Pydantic BaseSettings (env vars)
│   ├── database.py              # Async SQLAlchemy engine + session dep
│   │
│   ├── api/                     # Route handlers only — no business logic
│   │   ├── __init__.py
│   │   ├── keys.py
│   │   ├── subscriptions.py
│   │   ├── digests.py
│   │   ├── jobs.py
│   │   └── metrics.py
│   │
│   ├── services/                # Business logic — stateless, injectable
│   │   ├── fetcher.py
│   │   ├── summariser.py
│   │   ├── mailer.py
│   │   ├── metrics.py
│   │   └── scheduler.py         # ARQ job + cron definitions
│   │
│   ├── models/                  # SQLAlchemy ORM models
│   │   ├── api_key.py
│   │   ├── curated_source.py
│   │   ├── subscription.py
│   │   ├── digest.py
│   │   ├── digest_item.py
│   │   └── pipeline_event.py
│   │
│   ├── schemas/                 # Pydantic request/response schemas
│   │   ├── keys.py
│   │   ├── subscriptions.py
│   │   ├── digests.py
│   │   └── metrics.py
│   │
│   ├── middleware/
│   │   ├── auth.py
│   │   └── rate_limit.py
│   │
│   └── cli.py                   # python -m app.cli create_key
│
├── templates/
│   ├── base.html
│   ├── dashboard.html
│   ├── digest_detail.html
│   ├── setup.html
│   └── partials/
│       ├── subscription_card.html
│       ├── digest_row.html
│       ├── metrics_panel.html   # HTMX-polled fragment
│       └── toast.html
│
├── worker.py                    # ARQ WorkerSettings entrypoint
├── Dockerfile
├── requirements.txt
├── .env.example
└── alembic/
    ├── env.py
    └── versions/
```

### Key Conventions

- Routes import services. Services import models. No cross-service imports.
- All env vars accessed via `app/config.py` (Settings class), never `os.environ` directly.
- All DB sessions injected via `FastAPI Depends(get_db)`. No global session objects.
- ARQ job functions live in `app/services/scheduler.py`, not in `worker.py` (worker.py only imports them).

---

## O · Execution Plan

> **Constraint:** One week alongside coursework. ~3–4 focused hours/day. Each phase ends in a working, demonstrable state.

### Phase 1 — Foundation (Day 1)

1. Set up repo, virtual env, `requirements.txt`.
2. `app/config.py`, `app/database.py`, Alembic init.
3. All SQLAlchemy models. Run `alembic revision --autogenerate` + `alembic upgrade head`.
4. `app/middleware/auth.py` — key hash middleware.
5. `POST /keys` endpoint — generates key, hashes, stores, returns plaintext once.
6. Manual test: `curl POST /keys`, verify hash in DB.

**Exit criteria:** API key is issued and stored. Auth middleware blocks invalid tokens.

### Phase 2 — Subscription CRUD (Day 2)

1. Seed `curated_sources` table.
2. Pydantic schemas for subscriptions.
3. CRUD endpoints: POST, GET (list), GET (single), PATCH, DELETE.
4. Validation: sources list not empty, email format, cron string allowlist.
5. Manual test all endpoints with curl.

**Exit criteria:** Full subscription CRUD working. Data in Postgres.

### Phase 3 — Pipeline (Days 3–4) — MOST CRITICAL

1. `FetcherService`: httpx + feedparser. Write `pipeline_events` for fetch stage.
2. `SummariserService`: Anthropic SDK. Stub with fake summary first, replace with real API call after pipeline skeleton works.
3. `MailerService`: Resend API. Dev mode fallback (log to stdout).
4. ARQ worker: `worker.py`, `WorkerSettings`, `run_pipeline` job function.
5. `POST /subscriptions/{id}/trigger` returning 202 + job_id.
6. Test full pipeline end-to-end: trigger → check DB → check email (or stdout).

**Exit criteria:** Triggering a subscription runs all 3 stages. `pipeline_events` table has rows.

### Phase 4 — Observability + Rate Limiting (Days 4–5)

1. `MetricsService`: SQL aggregates on `pipeline_events`.
2. `GET /metrics/pipeline` and `GET /metrics/usage` endpoints.
3. slowapi rate limiting on trigger endpoint.
4. structlog replacing all `print()` calls.

**Exit criteria:** `/metrics/pipeline` returns real data. Rate limiter returns 429 after 3 triggers.

### Phase 5 — Frontend (Days 5–6)

1. `base.html` with Tailwind CDN, HTMX CDN.
2. `setup.html`: generate key page.
3. `dashboard.html`: subscription cards (with source chips), recent digests table.
4. `partials/metrics_panel.html`: HTMX-polled every 5s.
5. Create Subscription modal with checkbox source list.
6. `digest_detail.html`.

**Exit criteria:** Full browser-usable demo without curl.

### Phase 6 — Deployment (Day 7)

1. Write Dockerfile. Test locally.
2. Create Railway project. Add Postgres and Redis add-ons.
3. Set env vars: `ANTHROPIC_API_KEY`, `RESEND_API_KEY`, `DATABASE_URL`, `REDIS_URL`.
4. Connect GitHub repo. Push. Verify build.
5. Run `python -m app.cli create_key` on Railway shell.
6. Full end-to-end test on live URL.

**Exit criteria:** Live URL returns dashboard. Triggered pipeline runs on Railway. Metrics panel shows data.

### Fast Follow (Only If Ahead of Schedule)

- Webhook on digest completion with HMAC-SHA256 signing (3–4 hrs)
- Per-key rate limit display on dashboard (1 hr)
- Custom URL field below the checkbox list (1–2 hrs)

---

## P · Final Summary

### Feature Checklist

| Feature | MVP | V2 |
|---|---|---|
| API key issuance + revocation | ✓ | |
| API key auth middleware | ✓ | |
| Curated source list (checkbox UI) | ✓ | |
| Subscription CRUD | ✓ | |
| Async 3-stage pipeline (fetch/summarise/deliver) | ✓ | |
| `pipeline_events` observability table | ✓ | |
| Manual trigger with rate limiting | ✓ | |
| Daily scheduler (ARQ cron) | ✓ | |
| `/metrics/pipeline` endpoint | ✓ | |
| `/metrics/usage` endpoint | ✓ | |
| Structured JSON logging (structlog) | ✓ | |
| HTMX dashboard with auto-refresh metrics | ✓ | |
| Custom URL input alongside curated sources | | ✓ |
| Webhook on delivery with HMAC signing | | ✓ |
| CSV export of digest history | | ✓ |
| Per-subscription custom LLM prompts | | ✓ |

### Screen Inventory

- `/` — Dashboard
- `/digests/{id}` — Digest Detail
- `/setup` — API Key Setup
- `/dashboard/metrics` — HTMX-polled pipeline health fragment (partial, not a full page)

### Endpoint Inventory

- `POST /api/v1/keys`
- `DELETE /api/v1/keys/{prefix}`
- `POST /api/v1/subscriptions`
- `GET /api/v1/subscriptions`
- `GET /api/v1/subscriptions/{id}`
- `PATCH /api/v1/subscriptions/{id}`
- `DELETE /api/v1/subscriptions/{id}`
- `POST /api/v1/subscriptions/{id}/trigger`
- `GET /api/v1/digests`
- `GET /api/v1/digests/{id}`
- `GET /api/v1/jobs/{job_id}`
- `GET /api/v1/metrics/pipeline`
- `GET /api/v1/metrics/usage`
- `GET /` (HTML dashboard)
- `GET /setup` (HTML setup page)
- `GET /digests/{id}` (HTML detail page)
- `GET /dashboard/metrics` (HTML partial)

### Entity Inventory

- `api_keys` — auth credential + usage counter
- `curated_sources` — app-managed RSS feed list
- `subscriptions` — pipeline configuration
- `digests` — pipeline run records
- `digest_items` — per-item fetch + summary results
- `pipeline_events` — immutable stage audit log (observability backbone)

---

> **The one thing that matters most:** The `pipeline_events` table is the architectural decision that separates this from every bootcamp project. It makes observability a data modelling decision, not an afterthought. When a recruiter asks "how would you debug a failed pipeline run?" — the answer is a SQL query, not a grep on logs.
