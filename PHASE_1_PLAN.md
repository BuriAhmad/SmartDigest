# PHASE 1 PLAN — Project Skeleton + Database + API Key Auth

**Status:** 🟡 Awaiting Approval  
**Goal:** The FastAPI app runs. The database schema exists. You can issue an API key and authenticate with it.

---

## What We Are Building

1. **Full folder structure** — every directory and `__init__.py` from the spec's Section N
2. **Configuration** — `app/config.py` using Pydantic BaseSettings, loading `.env`
3. **Database layer** — `app/database.py` with async SQLAlchemy engine + session dependency
4. **All 6 SQLAlchemy models** — `api_keys`, `curated_sources`, `subscriptions`, `digests`, `digest_items`, `pipeline_events` (all tables, all columns, all indexes from Section I)
5. **Alembic setup** — `alembic init`, configured for async, initial migration generated + applied
6. **Auth middleware** — `app/middleware/auth.py`: SHA-256 key hashing, timing-safe comparison, `request.state.owner_key_id`, `last_used_at` + `api_call_count` updates, path exclusions
7. **API key endpoints** — `POST /api/v1/keys` (issue key) and `DELETE /api/v1/keys/{prefix}` (revoke key)
8. **CLI** — `app/cli.py` with `create_key` command (`python -m app.cli create_key`)
9. **App factory** — `app/main.py` with lifespan (DB pool + structlog init), router registration
10. **Root endpoint** — `GET /` returns plain text "SmartDigest is running"
11. **Stub endpoints** — subscriptions, digests, jobs, metrics all return realistic-looking fake data (not errors), so the app is complete enough to test auth against

---

## Files We Will Create

```
app/
├── __init__.py
├── main.py                  # FastAPI app factory + lifespan context manager
├── config.py                # Pydantic BaseSettings — all env vars
├── database.py              # Async engine, sessionmaker, get_db dependency
├── cli.py                   # python -m app.cli create_key
│
├── api/
│   ├── __init__.py
│   ├── keys.py              # POST /keys, DELETE /keys/{prefix}
│   ├── subscriptions.py     # STUB — returns fake data
│   ├── digests.py           # STUB — returns fake data
│   ├── jobs.py              # STUB — returns fake data
│   └── metrics.py           # STUB — returns fake data
│
├── models/
│   ├── __init__.py          # Re-exports all models (for Alembic)
│   ├── api_key.py
│   ├── curated_source.py
│   ├── subscription.py
│   ├── digest.py
│   ├── digest_item.py
│   └── pipeline_event.py
│
├── schemas/
│   ├── __init__.py
│   └── keys.py              # KeyCreate response schema
│
├── services/                # Empty — placeholder for later phases
│   └── __init__.py
│
├── middleware/
│   ├── __init__.py
│   └── auth.py              # API key auth middleware
│
templates/                   # Empty dir — placeholder for Phase 3
worker.py                    # Minimal placeholder (just imports, doesn't crash)

alembic/
├── alembic.ini
├── env.py                   # Configured for async SQLAlchemy
└── versions/                # Initial migration auto-generated here
```

---

## File-by-File Detail

### `app/config.py`
- Pydantic `BaseSettings` with `model_config` pointing to `.env`
- Fields: `DATABASE_URL`, `REDIS_URL`, `GEMINI_API_KEY`, `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `ENV` (default "development")
- Singleton `get_settings()` using `lru_cache`

### `app/database.py`
- `create_async_engine` with the `DATABASE_URL` from settings
- `async_sessionmaker` bound to the engine
- `async def get_db()` — FastAPI dependency yielding an `AsyncSession`

### `app/models/*`
All 6 tables exactly as specified in Section I:
- **api_keys**: id, prefix, key_hash (unique, indexed), api_call_count, last_used_at, created_at, revoked_at
- **curated_sources**: id, name, rss_url (unique), active, created_at
- **subscriptions**: id, api_key_id (FK), topic, sources (JSONB), email, schedule, active, created_at — with partial index on `(api_key_id) WHERE active = true`
- **digests**: id, subscription_id (FK), status, created_at, delivered_at — with index on `(subscription_id, created_at DESC)`
- **digest_items**: id, digest_id (FK), source_url, title, item_url, raw_content, summary, fetch_duration_ms, published_at
- **pipeline_events**: id, digest_id (FK, nullable), stage, status, duration_ms, error_msg, item_count, created_at — with indexes on `digest_id` and `created_at DESC`

### `app/middleware/auth.py`
- Starlette `BaseHTTPMiddleware`
- Excluded paths: `POST /api/v1/keys`, `GET /`, `GET /setup`, `GET /dashboard/metrics`, static assets
- Extracts `Bearer <token>` from `Authorization` header
- SHA-256 hashes the token, queries `api_keys` where `key_hash` matches and `revoked_at IS NULL`
- Uses `hmac.compare_digest` for timing-safe comparison
- On match: sets `request.state.owner_key_id` and `request.state.key_prefix`, updates `last_used_at` and increments `api_call_count`
- On miss: returns 401 JSON `{ "detail": "Invalid or revoked API key" }`
- Missing header: returns 401 JSON `{ "detail": "Authorization header required" }`

### `app/api/keys.py`
- `POST /api/v1/keys`: generates 32-byte random hex token, computes SHA-256 hash, stores hash + 4-char prefix, returns plaintext key once with 201
- `DELETE /api/v1/keys/{prefix}`: sets `revoked_at = now()`, returns 204. Requires auth.

### `app/api/subscriptions.py` (STUB)
- `GET /api/v1/subscriptions`: returns `[]` (empty list) — proves auth works
- All other subscription endpoints: return realistic stub responses

### `app/api/digests.py` (STUB)
- Returns empty list / stub data

### `app/api/jobs.py` (STUB)
- Returns stub job status

### `app/api/metrics.py` (STUB)
- Returns zero-value metrics structs

### `app/cli.py`
- Uses `asyncio.run()` to create a DB session
- Generates a key the same way `POST /keys` does
- Prints: `API Key created! Key: <plaintext> (prefix: <prefix>)`
- Prints: `⚠ Save this key — it will not be shown again.`

### `app/main.py`
- `lifespan` async context manager: logs startup/shutdown via structlog
- Creates FastAPI app with title "SmartDigest"
- Adds auth middleware
- Registers all routers (`keys`, `subscriptions`, `digests`, `jobs`, `metrics`) under `/api/v1`
- `GET /` returns `PlainTextResponse("SmartDigest is running")`

### `worker.py`
- Minimal placeholder that defines `WorkerSettings` with Redis settings
- Has an empty functions list — no real jobs yet
- File exists so the project structure is complete

### Alembic
- `alembic init -t async alembic` (async template)
- `alembic/env.py` configured to import all models and use async engine
- `alembic.ini` pointed at `DATABASE_URL`
- Initial migration generated with `alembic revision --autogenerate -m "initial schema"`
- Applied with `alembic upgrade head`

---

## What We Are NOT Building Yet

- ❌ Subscription CRUD logic (Phase 2)
- ❌ Curated sources seeder (Phase 2)
- ❌ HTMX templates / dashboard UI (Phase 3)
- ❌ Email delivery / MailerService (Phase 3)
- ❌ FetcherService / RSS fetching (Phase 4)
- ❌ SummariserService / Gemini calls (Phase 5)
- ❌ Real metrics aggregation (Phase 6)
- ❌ Rate limiting (Phase 6)
- ❌ ARQ cron scheduler (Phase 6)
- ❌ Dockerfile (Phase 6)
- ❌ Railway deployment (Phase 7)

---

## Acceptance Criteria (How You Will Manually Test)

After Phase 1 is complete, run these commands:

### 1. Server starts cleanly
```bash
uvicorn app.main:app --reload
# Expected: starts on port 8000 with no errors
```

### 2. Root endpoint works
```bash
curl http://localhost:8000/
# Expected: "SmartDigest is running"
```

### 3. Create API key via CLI
```bash
python -m app.cli create_key
# Expected: prints a 64-char hex key and 4-char prefix
```

### 4. Create API key via API
```bash
curl -s -X POST http://localhost:8000/api/v1/keys | python -m json.tool
# Expected: 201 with { "key": "...", "prefix": "...", "created_at": "..." }
```

### 5. Auth works — valid key
```bash
curl -s -H "Authorization: Bearer <key-from-step-4>" \
  http://localhost:8000/api/v1/subscriptions
# Expected: 200 with [] (empty list)
```

### 6. Auth works — invalid key rejected
```bash
curl -s -H "Authorization: Bearer wrongkey" \
  http://localhost:8000/api/v1/subscriptions
# Expected: 401 with { "detail": "Invalid or revoked API key" }
```

### 7. Auth works — missing header rejected
```bash
curl -s http://localhost:8000/api/v1/subscriptions
# Expected: 401 with { "detail": "Authorization header required" }
```

### 8. Revoke key works
```bash
curl -s -X DELETE -H "Authorization: Bearer <key>" \
  http://localhost:8000/api/v1/keys/<prefix>
# Expected: 204
# Then retry step 5 with the same key → 401
```

### 9. Database tables exist
```bash
# Connect to Postgres and verify all 6 tables:
docker exec -it smartdigest-postgres psql -U postgres -d smartdigest -c "\dt"
# Expected: api_keys, curated_sources, subscriptions, digests, digest_items, pipeline_events
```

---

## Dependencies & Assumptions

- Postgres is running on `localhost:5432` (Docker container `smartdigest-postgres`)
- Redis is running on `localhost:6379` (Docker container `smartdigest-redis`)
- `.env` file exists with valid `DATABASE_URL` and `REDIS_URL`
- Virtual environment is activated with all packages from `requirements.txt` installed
