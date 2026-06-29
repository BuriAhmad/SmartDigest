# SmartDigest Production Readiness Audit

Original audit date: 2026-06-26
Implementation update: 2026-06-29

## 2026-06-29 Implementation Update

This update supersedes the older "missing" findings below for container packaging, production validation, `/healthz`, explicit ARQ concurrency, release tasks, and Cloud Run deployment files. The older sections remain as historical audit context.

Implemented and locally verified:

- Added a Python 3.11 `Dockerfile` and `.dockerignore`; `docker build --check .` reports no warnings.
- Added role-specific fail-fast production validation with `APP_ROLE=web`, `worker`, or `release`.
- Added public `GET /healthz`, excluded it from session authentication, and tested the response.
- Added `ARQ_MAX_JOBS`, wired it to `WorkerSettings.max_jobs`, and set the first-deploy worker value to `2`.
- Added a bounded release script for Alembic migrations and source seeding.
- Added non-secret web, worker, and release environment files under `deploy/`.
- Added `scripts/deploy_gcloud.sh` to build one image and deploy a web service, release job, and continuous worker pool with separate runtime identities and per-secret IAM access.
- Disabled semantic retrieval and reranking only in the first-deploy worker environment; BM25 and LLM relevance remain active.
- Added `CLOUD_RUN_DEPLOYMENT.md` with the deployment and post-deployment runbook.
- Ran 6 focused production-readiness tests and the existing 49 retrieval/pipeline tests successfully.

Still external or operational:

- The deployment script has not been run against Google Cloud.
- The selected region must support Cloud Run worker pools.
- Resend sender-domain verification and Firebase authorized-domain configuration remain console/provider tasks.
- Post-deployment login, enqueue, worker, LLM, email, and logging smoke tests remain required.

## Original 2026-06-26 Audit

Scope: production readiness audit plus the Upstash Redis and Neon Postgres readiness updates that existed at that date.

Current verification performed:

- Inspected repo structure with `rg --files`, `find app`, and targeted reads of app, worker, migration, template, and deployment-related files.
- Confirmed local venv runtime with `env PYTHONPYCACHEPREFIX=/private/tmp/smartdigest-pycache .venv/bin/python --version`: Python 3.11.14.
- Introspected FastAPI routes from `app.main:app`; at that date no `/healthz` or `/readyz` route existed.
- Ran `env PYTHONPYCACHEPREFIX=/private/tmp/smartdigest-pycache .venv/bin/python -m unittest tests.test_retrieval_pipeline`: 49 tests passed.
- Ran `env PYTHONPYCACHEPREFIX=/private/tmp/smartdigest-pycache .venv/bin/python scripts/smoke_redis.py`: Upstash `rediss://` parsed through ARQ with SSL enabled, Redis `PING` succeeded, and short-lived `SET`/`GET` succeeded.
- Ran production Redis config validation sanity checks: local/default Redis and non-TLS production Redis are rejected unless the explicit override is set.
- Ran `env PYTHONPYCACHEPREFIX=/private/tmp/smartdigest-pycache .venv/bin/python scripts/smoke_db.py`: Neon direct host connected through SQLAlchemy asyncpg after translating `sslmode=require` to asyncpg `ssl=True`.
- Ran `env PYTHONPYCACHEPREFIX=/private/tmp/smartdigest-pycache .venv/bin/python -m alembic upgrade head` against Neon: migrations completed through `e5f6a7b8c9d0`.
- Ran `env PYTHONPYCACHEPREFIX=/private/tmp/smartdigest-pycache .venv/bin/python -m app.cli seed_sources` against Neon: seeded 10 curated sources.
- Ran `env PYTHONPYCACHEPREFIX=/private/tmp/smartdigest-pycache .venv/bin/python scripts/smoke_db.py --check-tables`: core tables exist and 10 curated sources are present.
- Checked `.env` tracking risk with `git ls-files .env --error-unmatch`: `.env` is not tracked. The local `.env` contains keys for `GEMINI_API_KEY`, `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `DATABASE_URL`, `REDIS_URL`, and `ENV`, but values are intentionally not repeated here.

## Current Repo Understanding

SmartDigest is a small FastAPI web app with server-rendered Jinja2/HTMX pages. There is no separate frontend build or frontend server in the current repository.

The app lets authenticated users create content briefings, select curated RSS-like sources, fetch articles, filter candidates with BM25 plus optional semantic retrieval and cross-encoder reranking, score relevance with Gemini through a shared LLM layer, summarise surviving articles, store digest state in PostgreSQL, and deliver emails through Resend.

Durable state lives in PostgreSQL. Redis is used as the ARQ queue transport. The web process enqueues digest jobs; the worker process performs the long-running fetch/filter/summarise/deliver pipeline and also runs ARQ cron jobs for scheduled digest enqueueing and queue recovery.

Important files and modules:

- `app/main.py`: FastAPI app factory, middleware, router registration, Jinja template setup, HTML routes, startup lifespan.
- `app/config.py`: Pydantic settings and environment variable defaults.
- `app/database.py`: SQLAlchemy async engine and session dependency.
- `worker.py`: ARQ worker entrypoint and cron registration.
- `app/services/scheduler.py`: full digest pipeline, scheduled enqueueing, queue recovery, advisory lock.
- `app/api/briefings.py`: briefing CRUD and manual ARQ enqueue endpoint.
- `app/api/jobs.py`: job-status endpoint using Postgres digest state plus ARQ Redis keys.
- `app/api/auth.py`, `app/services/auth.py`, `app/middleware/auth.py`: Firebase token exchange, app JWT session cookie, request authentication.
- `app/services/llm/google_genai.py`: Gemini provider adapter using `google-genai`.
- `app/services/filters/__init__.py`, `app/services/filters/semantic.py`, `app/services/filters/reranker.py`, `app/services/filters/llm_relevance.py`: retrieval, reranking, and LLM relevance pipeline.
- `app/services/summariser.py`: Gemini summarisation call site.
- `app/services/mailer.py`: Resend email delivery, with dev-mode logging fallback.
- `alembic/`: version-controlled database migrations.
- `templates/`: server-rendered UI templates.
- `Procfile`, `railway.toml`, `railway.worker.toml`, `services/web/railway.toml`, `services/worker/railway.toml`: existing Railway-oriented deployment references.
- `requirements.txt`: Python dependencies.

## Existing Architecture Summary

Runtime services:

- Web: FastAPI/Uvicorn using `app.main:app`.
- Worker: ARQ worker using `python worker.py`.
- Database: async SQLAlchemy using `postgresql+asyncpg`.
- Migrations: Alembic, async-configured, reading `DATABASE_URL`.
- Queue: ARQ over Redis using `RedisSettings.from_dsn(settings.REDIS_URL)`.
- Auth: Firebase browser SDK in templates exchanges Firebase ID tokens at `/auth/firebase/session`; backend verifies with Firebase Admin SDK and sets an httpOnly `sd_session` JWT cookie.
- LLM: shared Google GenAI adapter. New config names are `LLM_*`; legacy `GEMINI_*` names still exist.
- Email: Resend via `app/services/mailer.py`; if `RESEND_API_KEY` is absent, mailer logs and returns success as dev mode.
- UI: Jinja2 templates with inline CSS and CDN imports for Firebase JS SDK, HTMX, and Google Fonts.

Existing deployment hints:

- `Procfile`: `web: uvicorn app.main:app --host 0.0.0.0 --port $PORT` and `worker: python worker.py`.
- `services/web/railway.toml`: starts Uvicorn on `$PORT` and runs `alembic upgrade head && python -m app.cli seed_sources` as Railway pre-deploy.
- `services/worker/railway.toml`: starts `python worker.py`.
- No Dockerfile, `.dockerignore`, Cloud Run service config, or Cloud Build config exists in the current checkout.

## Target Production Architecture

Recommended first production architecture:

- Google Cloud Run web service running FastAPI/Uvicorn.
- Separate Cloud Run worker runtime for ARQ jobs.
- Neon Postgres for production database. Current Neon database: provider Neon, region AWS US East 2 / Ohio, database `neondb`, direct host `ep-proud-poetry-ajc5pujb.c-3.us-east-2.aws.neon.tech`, SSL required, direct connection selected for first deployment validation.
- Upstash Redis has been selected for ARQ queue transport.
- Firebase Auth for browser authentication.
- Gemini API through the existing shared LLM layer.
- Resend for digest email delivery, because the current repo uses Resend for mail.
- Google Secret Manager for secrets, injected as environment variables or secret-mounted files.
- Alembic for schema setup and future version-controlled schema changes.

Current best worker shape:

- The current `python worker.py` process is long-running and does not listen on `$PORT`.
- A plain Cloud Run service must listen on `$PORT`, so the worker is not directly Cloud Run service-ready yet.
- Best minimal future implementation: one worker container/service that starts a tiny HTTP health listener plus the ARQ worker, with min instances set to 1 and CPU always allocated.
- Alternative later: a Cloud Run Job only if a future command is added that processes due/queued work in a bounded way and exits cleanly. The current ARQ worker is not that shape.

## Findings By Area

### 1. Application Entrypoint And Runtime

What is already good:

- FastAPI app entrypoint is clear: `app.main:app`.
- `app/main.py` defines `create_app()` and `app = create_app()`.
- Existing web deployment references bind to Cloud Run's required network shape: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- Templates are loaded from the repository root `templates` directory using `Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))`.
- No separate frontend server or frontend build step was found.
- No persistent file upload/local storage feature was found.
- The app can run as a same-origin server-rendered web app, which fits one Cloud Run web service.

Gaps and risks:

- No dedicated `/healthz` endpoint exists.
- Existing command does not include proxy header flags. Cloud Run terminates HTTPS before the container, so the production command should include proxy handling, for example `uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'`.
- `SessionAuthMiddleware` excludes `/static`, but there is no `StaticFiles` mount. That is fine if there are no local static assets, but future static files will need an explicit mount.
- The UI depends on external CDNs at runtime: Firebase browser SDK, HTMX, and Google Fonts.
- Localhost defaults exist in `app/config.py` for Postgres, Redis, and Firebase service account path.
- `worker.py` does not bind to `$PORT`; it cannot be deployed as a standard Cloud Run service without a wrapper/listener.

Production command recommendation:

```bash
uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'
```

### 2. Environment Variables And Secrets

Config source: `app/config.py`.

Required production environment variables:

- `ENV=production`
- `DATABASE_URL`: Neon Postgres connection string.
- `REDIS_URL`: Upstash Redis protocol URL, preferably TLS with `rediss://`. For this repo, this is the only Upstash Redis credential the app needs.
- `LLM_API_KEY`: Gemini API key. `GEMINI_API_KEY` still works as a legacy fallback, but new production config should use `LLM_API_KEY`.
- `JWT_SECRET`: high-entropy session signing secret. Do not use the default in `app/config.py`.
- `FIREBASE_SERVICE_ACCOUNT_JSON` or `FIREBASE_SERVICE_ACCOUNT_PATH`: Firebase Admin credential for backend token verification.
- `FIREBASE_WEB_API_KEY`
- `FIREBASE_WEB_AUTH_DOMAIN`
- `FIREBASE_WEB_PROJECT_ID`
- `FIREBASE_WEB_STORAGE_BUCKET`
- `FIREBASE_WEB_MESSAGING_SENDER_ID`
- `FIREBASE_WEB_APP_ID`
- `FIREBASE_WEB_MEASUREMENT_ID` if enabled.
- `RESEND_API_KEY`
- `RESEND_FROM_EMAIL`

Production tuning variables already supported:

- `LLM_RELEVANCE_MODELS`
- `LLM_SUMMARY_MODELS`
- `LLM_REQUEST_TIMEOUT_SECONDS`
- `LLM_RETRY_ATTEMPTS`
- `LLM_RETRY_BACKOFF_SECONDS`
- `LLM_SUMMARY_BATCH_SIZE`
- `LLM_SUMMARY_ARTICLE_MAX_CHARS`
- `GEMINI_RELEVANCE_MODELS`
- `GEMINI_SUMMARY_MODELS`
- `GEMINI_REQUEST_TIMEOUT_SECONDS`
- `GEMINI_RETRY_ATTEMPTS`
- `GEMINI_RETRY_BACKOFF_SECONDS`
- `GEMINI_SUMMARY_BATCH_SIZE`
- `GEMINI_SUMMARY_ARTICLE_MAX_CHARS`
- `SEMANTIC_RETRIEVAL_ENABLED`
- `SEMANTIC_WARMUP_ENABLED`
- `SEMANTIC_MODEL_LOCAL_FILES_ONLY`
- `SEMANTIC_MODEL_LOAD_TIMEOUT_SECONDS`
- `SEMANTIC_MODEL_NAME`
- `SEMANTIC_TOP_K`
- `SEMANTIC_MIN_SCORE`
- `SEMANTIC_QUERY_MAX_CHARS`
- `SEMANTIC_ARTICLE_MAX_CHARS`
- `RETRIEVAL_UNION_MAX_K`
- `RERANKER_ENABLED`
- `RERANKER_REQUIRED`
- `RERANKER_WARMUP_ENABLED`
- `RERANKER_MODEL_LOCAL_FILES_ONLY`
- `RERANKER_MODEL_LOAD_TIMEOUT_SECONDS`
- `RERANKER_MODEL_NAME`
- `RERANKER_TOP_K`
- `RERANKER_MIN_KEEP`
- `RERANKER_MIN_SCORE`
- `RERANKER_MAX_SCORE_DROP`
- `RERANKER_ARTICLE_MAX_CHARS`
- `RERANKER_BATCH_SIZE`
- `ARQ_JOB_EXPIRES_SECONDS`
- `QUEUED_DIGEST_RECOVERY_AFTER_MINUTES`
- `PROCESSING_DIGEST_RECOVERY_AFTER_MINUTES`
- `ARQ_JOB_TIMEOUT_SECONDS`
- `ARQ_MAX_TRIES`
- `PIPELINE_RETRY_DEFER_SECONDS`

Recommended future `.env.example` shape:

```env
ENV=development
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/smartdigest
REDIS_URL=redis://localhost:6379
# Production Upstash should use the Redis protocol TLS URL, not REST credentials:
# REDIS_URL=rediss://default:<password>@<host>:6379

LLM_API_KEY=
LLM_RELEVANCE_MODELS=gemini-2.5-flash-lite,gemini-2.5-flash
LLM_SUMMARY_MODELS=gemini-2.5-flash-lite,gemini-2.5-flash
LLM_REQUEST_TIMEOUT_SECONDS=45
LLM_RETRY_ATTEMPTS=2
LLM_RETRY_BACKOFF_SECONDS=1
LLM_SUMMARY_BATCH_SIZE=8
LLM_SUMMARY_ARTICLE_MAX_CHARS=1800

JWT_SECRET=change-me

FIREBASE_SERVICE_ACCOUNT_JSON=
FIREBASE_SERVICE_ACCOUNT_PATH=
FIREBASE_WEB_API_KEY=
FIREBASE_WEB_AUTH_DOMAIN=
FIREBASE_WEB_PROJECT_ID=
FIREBASE_WEB_STORAGE_BUCKET=
FIREBASE_WEB_MESSAGING_SENDER_ID=
FIREBASE_WEB_APP_ID=
FIREBASE_WEB_MEASUREMENT_ID=

RESEND_API_KEY=
RESEND_FROM_EMAIL=SmartDigest <digest@smartdigest.app>

SEMANTIC_RETRIEVAL_ENABLED=true
SEMANTIC_WARMUP_ENABLED=false
SEMANTIC_MODEL_LOCAL_FILES_ONLY=true
SEMANTIC_MODEL_LOAD_TIMEOUT_SECONDS=5

RERANKER_ENABLED=true
RERANKER_REQUIRED=true
RERANKER_WARMUP_ENABLED=false
RERANKER_MODEL_LOCAL_FILES_ONLY=false
RERANKER_MODEL_LOAD_TIMEOUT_SECONDS=45

ARQ_JOB_TIMEOUT_SECONDS=900
ARQ_MAX_TRIES=4
ARQ_JOB_EXPIRES_SECONDS=604800
QUEUED_DIGEST_RECOVERY_AFTER_MINUTES=5
PROCESSING_DIGEST_RECOVERY_AFTER_MINUTES=30
PIPELINE_RETRY_DEFER_SECONDS=300
ALLOW_INSECURE_PRODUCTION_REDIS=false
```

Secrets risk:

- `.env` exists locally but is ignored by `.gitignore` and is not tracked.
- No tracked Firebase service-account JSON, private key, or `.env` file was found in the scan.
- `app/config.py` includes a committed dev JWT default: `dev-secret-change-in-production-abc123`. Production should fail startup if this value is still active.
- Firebase web config values are committed in `app/config.py`. These are public client config values, not a Firebase Admin private key, but production should intentionally set the exact Firebase project values through environment variables.
- `RESEND_API_KEY`, `LLM_API_KEY`, `GEMINI_API_KEY`, `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET`, and Firebase Admin credentials should be stored in Secret Manager, not committed files.
- Upstash REST URL/token values are not part of the current app configuration. Do not introduce `UPSTASH_REDIS_REST_URL` or `UPSTASH_REDIS_REST_TOKEN` unless a future feature intentionally uses a REST Redis client.
- The Redis credential currently belongs only in local `.env` for testing or in Secret Manager for production. Real Redis passwords/tokens must not be repeated in docs.

### 3. Database Readiness

What is already good:

- ORM/database library: SQLAlchemy 2 async with `asyncpg`.
- Migrations exist under `alembic/versions`.
- `app/config.py` converts `postgres://` or `postgresql://` to `postgresql+asyncpg://`.
- `app/config.py`, `app/database.py`, and `alembic/env.py` now share asyncpg URL preparation that strips `sslmode=require` from the URL and passes asyncpg `ssl=True`.
- `alembic/env.py` loads local `.env` for local migration validation while still allowing deployment environments to inject `DATABASE_URL`.
- No `Base.metadata.create_all()` or auto-create-table-on-startup path was found.
- Main durable tables are `users`, `curated_sources`, `briefings`, `digests`, `digest_items`, and `pipeline_events`.
- Current tests around retrieval/pipeline pass.
- Neon direct-host validation has completed: DB smoke test passed, Alembic head is `e5f6a7b8c9d0`, and 10 curated sources are seeded.

Schema setup requirements:

- For the currently configured Neon database, `alembic upgrade head` has already been run successfully.
- For the currently configured Neon database, `python -m app.cli seed_sources` has already inserted 10 curated RSS sources. This command is idempotent by URL.
- No local data migration is needed.

Gaps and risks:

- No Cloud Run-specific migration/release step exists yet.
- Migrations and source seeding should not run inside every web instance startup command.
- Neon direct connection is validated. The pooler host has not been selected or tested; switching to it should be an explicit decision because pooling changes connection behavior.
- The smoke script confirmed asyncpg receives `ssl=True`; `pg_stat_ssl` reported `False` for the backend session, likely because Neon terminates TLS at a managed endpoint/proxy before the Postgres backend. Treat this as a managed-provider observability nuance, not evidence that the app URL parser is still passing `sslmode` incorrectly.
- `app/api/keys.py`, `app/schemas/keys.py`, and `app/models/api_key.py` remain as removed/stale surfaces. They are not routed in `app/main.py` and `app/models/__init__.py` no longer exports `ApiKey`, but they can confuse future maintenance.

Recommended migration strategy:

- Use an empty Neon production database.
- Run Alembic once before the first deployment.
- Seed curated sources once after migrations.
- For future deploys, run migrations as a release step or Cloud Run Job before moving traffic to the new revision.
- Keep schema changes backward-compatible when possible.
- For destructive DB changes, use a two-deploy process: add/dual-write first, verify/backfill, then remove old schema later.

### 4. Redis And Job Queue Readiness

What is already good:

- Queue library: ARQ.
- Redis provider: Upstash Redis.
- Worker entrypoint: `python worker.py`.
- Manual trigger endpoint: `POST /api/v1/briefings/{briefing_id}/trigger` in `app/api/briefings.py`.
- Manual trigger creates `Digest(status="queued")`, enqueues `run_pipeline`, and returns `202`.
- Scheduled enqueue and recovery are ARQ cron jobs in `worker.py`:
  - `recover_queued_digests` every 5 minutes, with `run_at_startup=True`.
  - `enqueue_scheduled_digests` every 30 minutes, with `run_at_startup=True`.
- Duplicate pipeline protection uses Postgres advisory transaction locks in `app/services/scheduler.py`.
- ARQ job timeout, max tries, job expiry, and retry defer are configurable.
- `worker.py`, `app/api/briefings.py`, and `app/services/scheduler.py` use `RedisSettings.from_dsn(settings.REDIS_URL)`, which parses `rediss://` with SSL enabled.
- `app/api/jobs.py` uses `redis.from_url(settings.REDIS_URL)`, keeping the job-status Redis check on the same Redis protocol URL.
- No tight custom polling loop was found.

Gaps and risks:

- Current worker command is long-running and does not listen on `$PORT`.
- If deployed as Cloud Run service, the worker needs min instances 1 and CPU always allocated, or Redis jobs may sit unprocessed.
- `WorkerSettings` does not explicitly set worker concurrency/max jobs. ARQ defaults may be too high for a small app that performs external fetches and Gemini calls.
- `app/api/jobs.py` checks `zscore("arq:queue", job_id)` plus `exists("arq:job:{job_id}")`. These should work with Redis-protocol Upstash, but this endpoint should not be polled aggressively.
- Upstash REST URL/token is not suitable for ARQ and is not needed for the current app configuration. Use the Redis protocol URL in `REDIS_URL`.
- Prefer `rediss://` for TLS.
- Any Upstash password/token exposed outside private secret storage should be rotated before production and replaced in Secret Manager/local `.env`.

Recommended worker deployment shape:

- First production shape: separate Cloud Run worker service after adding a small HTTP health listener/wrapper around the ARQ worker.
- Configure worker service with min instances 1, CPU always allocated, low max instances, and explicit low job concurrency.
- Alternative later: Cloud Scheduler plus Cloud Run Jobs only if the app gets a bounded command that enqueues/processes due runs and exits.

### 5. Firebase Auth Readiness

What is already good:

- Firebase Auth is already integrated.
- `templates/login.html` uses Firebase JS SDK for email/password and Google sign-in.
- `/auth/firebase/session` in `app/api/auth.py` receives a Firebase ID token, verifies it through Firebase Admin, creates/updates a local `User`, and sets an `sd_session` cookie.
- `SessionAuthMiddleware` protects browser and API routes, except explicit public paths.
- In production, `app/api/auth.py` sets the cookie with `secure=True` when `ENV=production`.
- Backend session token verification is implemented in `app/services/auth.py`.

Production configuration needed:

- Use `FIREBASE_SERVICE_ACCOUNT_JSON` from Secret Manager or mount a secret file and set `FIREBASE_SERVICE_ACCOUNT_PATH`.
- Add the Cloud Run default domain to Firebase Auth authorized domains after deployment.
- Add any custom domain later before switching users to it.
- Enable Email/Password and Google providers in Firebase if both login methods are desired.
- Configure password reset/action email settings in Firebase.

Gaps and risks:

- Cloud Run cannot rely on the local default `~/.config/smartdigest/firebase/firebase-admin-service-account.json`.
- No `ALLOWED_ORIGINS` or CORS config exists. This is acceptable for the current same-origin server-rendered app, but a separate frontend would need explicit CORS/cookie planning.
- State-changing endpoints use cookie auth without CSRF tokens. `SameSite=Lax` helps, but CSRF protection should be added before broader public launch.
- FastAPI docs (`/docs`, `/redoc`, `/openapi.json`) are public by middleware exclusion.

### 6. Gemini API Readiness

What is already good:

- Gemini calls are centralized in `app/services/llm/google_genai.py`.
- API key lookup prefers `LLM_API_KEY` and accepts legacy `GEMINI_API_KEY`.
- Model fallback chain is configurable for relevance and summarisation.
- Timeouts, retry attempts, backoff, summary batch size, and article character caps are configurable.
- Provider errors log call name, model, attempt, status code, and shortened error text.
- Retryable LLM provider failures can raise `LLMRetryableError`; `app/services/scheduler.py` can requeue retryable filter/summarise failures.

Gaps and risks:

- If no LLM API key is configured, `LLMRelevanceFilter.score_and_filter()` marks articles with default score 6 and keeps them, while `summarise_articles()` writes placeholder summaries. In production, this should be a startup failure, not a silent fallback.
- `RESEND_API_KEY` has similar dev-mode behavior: absent key logs email content and returns success.
- Manual trigger is rate-limited to `3/hour`, but there is no broader Gemini budget/usage cap.
- Worker concurrency should be intentionally low to control Gemini spend.
- Provider usage/cost metadata is not captured if the SDK exposes it.

Local model deployment risk:

- `SEMANTIC_RETRIEVAL_ENABLED` defaults to true, `SEMANTIC_MODEL_LOCAL_FILES_ONLY` defaults to true, and `SEMANTIC_MODEL_NAME` defaults to `sentence-transformers/all-MiniLM-L6-v2`. If the model is not already cached inside the Cloud Run image, semantic retrieval will fail soft and BM25 will carry retrieval.
- `RERANKER_ENABLED` defaults to true, `RERANKER_REQUIRED` defaults to true, `RERANKER_MODEL_LOCAL_FILES_ONLY` defaults to false, and `RERANKER_MODEL_NAME` defaults to `cross-encoder/ettin-reranker-68m-v1`. That means the worker may try to download/load a Hugging Face model at runtime. If that fails or exceeds timeout, the pipeline can fail because reranker is required.
- For first deployment, either disable semantic/reranker features with env vars or deliberately package/cache models and allocate enough memory.

### 7. Cloud Run Deployment Readiness

What is already good:

- Existing Railway files identify the correct web and worker process commands.
- Web command already uses `$PORT`.
- App does not require persistent disk for current features.
- Templates are in the repo and should work if copied into the container image.

Gaps and risks:

- No Dockerfile exists.
- No `.dockerignore` exists.
- No Cloud Run service, job, or deploy script exists.
- No dedicated health endpoint exists.
- Worker needs Cloud Run-specific handling because it does not listen on `$PORT`.
- Startup tasks should not run on every web instance. Avoid putting `alembic upgrade head` or `python -m app.cli seed_sources` in the web service command.
- `sentence-transformers`, `transformers`, and `trafilatura` increase image size and memory needs.
- Cloud Run filesystem is ephemeral. Current app appears fine because it does not rely on persistent disk, but future uploads/cache files need external storage.

Conceptual Dockerfile contents for a later implementation:

- Use a Python 3.11 base image to match the local venv and README.
- Install system packages needed by Python dependencies, if required.
- Install `requirements.txt`.
- Copy `app/`, `alembic/`, `templates/`, `worker.py`, and `alembic.ini`.
- Run as a non-root user if practical.
- Let Cloud Run inject `PORT`.
- Use the web command listed above.
- Reuse the same image for worker with a separate command once worker health/listener shape is implemented.

Suggested initial Cloud Run sizing:

- Web service: 512 MiB to 1 GiB memory, 1 CPU, concurrency 20 to 40, low max instances during launch, min instances 0 unless warm starts matter.
- Worker service: 1 to 2 GiB memory if semantic/reranker models are enabled; otherwise 512 MiB to 1 GiB may be enough. Use min instances 1 and CPU always allocated.
- Keep worker max instances and job concurrency low at first to protect Neon, Upstash, Gemini, Resend, and source sites.

### 8. Production Safety And Maintainability

Already good:

- Structured logging through `structlog`, with JSON renderer in production.
- Pipeline events are stored in Postgres for fetch/filter/summarise/deliver observability.
- Manual trigger has a per-user/IP rate limit.
- Most SQL is SQLAlchemy expression API, not string-concatenated SQL.
- Briefing and digest detail endpoints enforce ownership.
- Secure cookie flag is tied to `ENV=production`.
- Current retrieval/pipeline tests pass.

Risks to fix before public production:

- `app/api/jobs.py` does not verify that the authenticated user owns the digest/job being inspected. A logged-in user who guesses a digest ID can see status and latest pipeline event details.
- Metrics pages and metrics APIs expose global pipeline health to any authenticated user.
- No explicit production config validation exists.
- Missing health endpoint.
- Worker is not Cloud Run service-ready.
- Missing LLM key and missing Resend key can lead to silent dev-mode behavior.
- Public `/docs`, `/redoc`, and `/openapi.json` may be acceptable for a private beta but should be an explicit decision.
- Cookie-auth state changes have no CSRF token.
- External scripts are loaded from CDNs without an explicit CSP.

Recommended smoke tests before deployment:

- Build image successfully.
- Run `alembic upgrade head` against Neon.
- Run `python -m app.cli seed_sources` against Neon.
- Start web container locally with production-like env.
- Confirm `/healthz` responds once added.
- Confirm Firebase login exchanges a token at `/auth/firebase/session`.
- Confirm `sd_session` cookie is `Secure`, `HttpOnly`, and `SameSite=Lax` in production.
- Create a briefing.
- Trigger a digest.
- Confirm web enqueues to Upstash.
- Confirm worker dequeues and processes a job.
- Run `python scripts/smoke_redis.py` with the intended `REDIS_URL` to verify Redis `PING`, short-lived `SET`/`GET`, and ARQ DSN parsing.
- Confirm Gemini calls succeed.
- Confirm Resend sends real email.
- Confirm metrics update.
- Confirm logout clears session.

Safe update and rollback strategy:

- Build each release as a new Cloud Run revision.
- Run migrations before routing traffic.
- Prefer backward-compatible migrations.
- Shift traffic gradually for non-trivial changes.
- Roll back by moving Cloud Run traffic to the previous revision.
- If a migration is destructive, rollback may need a paired database plan; do not assume app rollback alone is enough.

### 9. What Not To Overcomplicate

- Do not use Kubernetes.
- Do not switch to Cloud SQL unless Neon becomes a repo-specific blocker.
- Do not switch to Memorystore unless Upstash proves incompatible with ARQ or command volume.
- Do not split frontend hosting; the repo is server-rendered.
- Do not rewrite the queue system before first deployment.
- Do not introduce a separate SPA build pipeline.
- Do not run migrations from every web container startup.

## Risks And Blockers

Deployment blockers:

1. Worker is not Cloud Run service-ready because `python worker.py` does not listen on `$PORT`.
2. No production config validation; production could start with default/local DB or Redis URLs, default `JWT_SECRET`, missing Firebase Admin credentials, missing LLM key, or missing Resend key.
3. No dedicated `/healthz` endpoint.
4. `app/api/jobs.py` lacks digest ownership enforcement.
5. Metrics routes expose global operational data to any authenticated user.
6. First deployment must decide whether to disable or package semantic/reranker local models.

Operational risks:

1. Worker concurrency may be too high by default for Gemini/cost/source-site limits.
2. Upstash command and connection limits should be monitored.
3. External CDN dependencies can affect login/UI availability.
4. Existing README/Railway docs contain deployment/auth details that are not the Cloud Run plan.
5. No Cloud Run-specific runbook or deploy files exist yet.

## Recommended Changes By Priority

### Must Do Before Deployment

- Add production config validation:
  - fail if `ENV=production` and `JWT_SECRET` is default/short;
  - fail if `DATABASE_URL` or `REDIS_URL` is local/default;
  - require `rediss://` for production Redis unless `ALLOW_INSECURE_PRODUCTION_REDIS=true` is deliberately set;
  - fail if Firebase Admin credentials are missing;
  - fail if `LLM_API_KEY`/`GEMINI_API_KEY` is missing;
  - fail if `RESEND_API_KEY` is missing for production email delivery;
  - confirm Firebase web config is explicitly set.
- Add an unauthenticated lightweight `/healthz` endpoint and exclude it from auth middleware if needed.
- Decide and implement worker shape for Cloud Run:
  - long-running worker service with HTTP health listener, min instances 1, CPU always allocated; or
  - later bounded Cloud Run Job command if you choose to redesign the worker entrypoint.
- Protect `app/api/jobs.py` by verifying digest ownership through `briefings.user_id == request.state.user_id`.
- Restrict global metrics pages/API to an admin role or remove global error details from normal users.
- Decide first-deploy local model behavior:
  - simplest: set `SEMANTIC_RETRIEVAL_ENABLED=false` and `RERANKER_ENABLED=false`;
  - higher quality: package/cache the models and allocate enough memory.
- Add Cloud Run deployment assets in a later implementation phase:
  - Dockerfile;
  - `.dockerignore`;
  - web service command;
  - worker command/wrapper;
  - migration/seed release job or documented commands.
- Neon direct-host validation is complete locally. For deployment, repeat migration/seed as a release step only when targeting a fresh or changed production database.
- Validate Upstash Redis protocol TLS URL with `scripts/smoke_redis.py` and ARQ enqueue/dequeue.
- Configure Firebase authorized domains for Cloud Run and custom domain.

### Should Do Soon After Deployment

- Add CSRF protection for cookie-auth state-changing endpoints.
- Add an admin/user role model if metrics/admin pages should exist.
- Add security headers and CSP, especially if CDN scripts remain.
- Make ARQ worker concurrency explicit with a setting such as future `ARQ_MAX_JOBS`.
- Add owner-safe job history/progress details.
- Add deployment smoke tests for login, briefing creation, queue, worker, email, metrics, and logout.
- Add correlation/request IDs across web enqueue and worker pipeline logs.
- Add provider usage/cost logging for Gemini if available.
- Update README deployment/auth sections after Cloud Run implementation is complete.

### Nice To Have Later

- Custom domain and polished Firebase action email templates.
- Cloud Monitoring dashboards and alerts for failed digests, queue backlog, worker restarts, and email failures.
- Error tracking service.
- More tests around auth, ownership, production config validation, and worker command shape.
- Clean up removed/stale API key files if confirmed unused.
- Consider replacing ARQ cron scheduling with Cloud Scheduler later only if it simplifies operations.

## Exact Files And Modules Involved

Entrypoint/runtime:

- `app/main.py`
- `Procfile`
- `railway.toml`
- `services/web/railway.toml`

Config/secrets:

- `app/config.py`
- `.gitignore`
- `.env` exists locally but is untracked

Database/migrations:

- `app/database.py`
- `app/models/*.py`
- `alembic/env.py`
- `alembic/versions/*.py`
- `app/cli.py`

Queue/worker:

- `worker.py`
- `app/services/scheduler.py`
- `app/api/briefings.py`
- `app/api/jobs.py`

Auth:

- `app/api/auth.py`
- `app/services/auth.py`
- `app/middleware/auth.py`
- `templates/login.html`
- `templates/account_security.html`
- `templates/public_home.html`

LLM/retrieval:

- `app/services/llm/google_genai.py`
- `app/services/filters/__init__.py`
- `app/services/filters/llm_relevance.py`
- `app/services/filters/semantic.py`
- `app/services/filters/reranker.py`
- `app/services/summariser.py`

Email:

- `app/services/mailer.py`

Metrics/security:

- `app/api/metrics.py`
- `app/services/metrics.py`
- `templates/metrics.html`
- `templates/partials/metrics_panel.html`
- `templates/partials/metrics_full.html`

Templates/static:

- `templates/base.html`
- `templates/*.html`

Tests:

- `tests/test_retrieval_pipeline.py`

## Suggested Deployment Shape

### Web Service

- Cloud Run service name: `smartdigest-web`.
- Command: `uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'`.
- Public traffic enabled.
- Runs no migrations or seed commands at startup.
- Uses `ENV=production`.
- Reads Neon, Upstash, Firebase, Gemini, Resend, and JWT secrets from env/Secret Manager.
- Set both `RESEND_API_KEY` and `RESEND_FROM_EMAIL=SmartDigest <digest@smartdigest.app>`.
- Suggested initial resources: 512 MiB to 1 GiB memory, 1 CPU, concurrency 20 to 40, conservative max instances.

### Worker Service Or Job

Current best fit: separate long-running Cloud Run worker service after a small future change adds an HTTP health listener.

- Cloud Run service name: `smartdigest-worker`.
- No public user traffic.
- Runs ARQ worker logic from `worker.py`.
- Must listen on `$PORT` for Cloud Run service health.
- Set min instances 1.
- Set CPU always allocated.
- Keep worker concurrency low.
- Set both `RESEND_API_KEY` and `RESEND_FROM_EMAIL=SmartDigest <digest@smartdigest.app>`; the worker is the process that sends digest emails.
- Use the same image as web if practical, but with a different command.

Do not deploy current `python worker.py` as a plain Cloud Run service without a port listener.

### Database

- Neon Postgres, empty production database.
- Current Neon direct-host database is at Alembic head `e5f6a7b8c9d0`.
- Current Neon direct-host database has 10 curated sources seeded.
- Future deploys should run `alembic upgrade head` and `python -m app.cli seed_sources` as explicit release-step commands, not web service startup commands.
- No local data migration required.

### Redis

- Upstash Redis using the Redis protocol URL in `REDIS_URL`.
- Prefer `rediss://...`; this is the correct credential shape for ARQ.
- Use the same `REDIS_URL` for web enqueue and worker dequeue.
- Do not use Upstash REST URL/token for ARQ or add REST env vars unless a future REST-client feature needs them.

### Auth

- Firebase Auth project with Email/Password enabled and Google provider enabled if desired.
- Backend verifies Firebase ID tokens through Firebase Admin SDK.
- App session cookie remains `sd_session`.
- Authorized domains must include Cloud Run URL and future custom domain.

### Gemini

- Store API key in Secret Manager and inject as `LLM_API_KEY`.
- Recommended initial model env:
  - `LLM_RELEVANCE_MODELS=gemini-2.5-flash-lite,gemini-2.5-flash`
  - `LLM_SUMMARY_MODELS=gemini-2.5-flash-lite,gemini-2.5-flash`
- Keep batching and article char caps conservative.
- Keep worker concurrency low to control spend.

### Email

- Store `RESEND_API_KEY` in Secret Manager.
- Set `RESEND_FROM_EMAIL` to `SmartDigest <digest@smartdigest.app>`.
- Inject both Resend env vars into both web and worker services, even though the worker performs digest delivery.
- Treat missing Resend key as a production deployment failure.
