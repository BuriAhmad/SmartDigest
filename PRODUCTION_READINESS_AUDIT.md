# SmartDigest Production Readiness Audit

Audit date: 2026-06-25

Scope: planning and audit only. No application code, schema, migration, config, Docker, or existing documentation changes were made as part of this audit. The repository already had uncommitted code changes before this audit; this document describes the current working tree as inspected.

## Current repo understanding

SmartDigest is a small FastAPI web app with server-rendered Jinja2/HTMX pages. There is no separate frontend build or frontend server in the current repository.

The app lets authenticated users create briefings, fetches articles from curated RSS-like sources, filters candidates with BM25 plus optional semantic retrieval, scores them with Gemini through a shared LLM layer, summarises surviving articles, stores digest state in PostgreSQL, and delivers email through Resend.

The durable state is PostgreSQL. Redis is used as the ARQ queue transport. The web process enqueues digest jobs; the worker process performs the long-running fetch/filter/summarise/deliver pipeline and also runs ARQ cron jobs for scheduled digest enqueueing and queue recovery.

Important files:

- `app/main.py`: FastAPI app factory, middleware, router registration, HTML routes, Jinja template setup, startup lifespan.
- `app/config.py`: Pydantic settings and environment variable defaults.
- `app/database.py`: SQLAlchemy async engine and session dependency.
- `worker.py`: ARQ worker entrypoint and cron registration.
- `app/services/scheduler.py`: full digest pipeline, scheduled enqueueing, recovery, advisory lock.
- `app/api/briefings.py`: briefing CRUD and manual ARQ enqueue endpoint.
- `app/api/jobs.py`: job-status endpoint using Postgres digest state plus ARQ Redis keys.
- `app/api/auth.py`, `app/services/auth.py`, `app/middleware/auth.py`: Firebase token exchange, app JWT session cookie, request authentication.
- `app/services/llm/google_genai.py`: Gemini provider adapter using `google-genai`.
- `app/services/filters/llm_relevance.py`, `app/services/summariser.py`: Gemini call sites.
- `alembic/`: version-controlled database migrations.
- `templates/`: server-rendered UI templates.
- `Procfile`, `railway.toml`, `railway.worker.toml`, `services/web/railway.toml`, `services/worker/railway.toml`: existing Railway-oriented deployment references.
- `requirements.txt`: Python dependencies.

## Existing architecture summary

Runtime services:

- Web: `uvicorn app.main:app --host 0.0.0.0 --port $PORT` in `Procfile` and Railway config.
- Worker: `python worker.py`.
- Database: async SQLAlchemy using `postgresql+asyncpg`.
- Migrations: Alembic, async-configured, reading `DATABASE_URL`.
- Queue: ARQ over Redis using `RedisSettings.from_dsn(settings.REDIS_URL)`.
- Auth: Firebase client SDK in templates exchanges Firebase ID token at `/auth/firebase/session`; backend verifies with Firebase Admin SDK and sets an httpOnly `sd_session` JWT cookie.
- LLM: shared Google GenAI adapter. New config names are `LLM_*`; legacy `GEMINI_*` names still exist.
- Email: Resend via `app/services/mailer.py`; if `RESEND_API_KEY` is absent, the mailer logs and returns success as dev mode.
- UI: server-rendered templates, external CDN imports for Firebase JS SDK, HTMX, and Google Fonts.

## Target production architecture

Recommended target for first Cloud Run deployment:

- Cloud Run web service: FastAPI/Uvicorn serving HTML and API routes.
- Separate worker runtime for ARQ jobs:
  - Preferred with current code shape: a long-running Cloud Run worker service, but only after adding a small HTTP health listener/wrapper because Cloud Run services must listen on `$PORT`.
  - Alternative: a finite Cloud Run Job only if a later code change adds a bounded "drain queued jobs and exit" worker command. The current `python worker.py` is long-running and is not a natural finite job.
- Neon Postgres: schema created by Alembic only; no local data migration needed.
- Upstash Redis: Redis protocol URL, preferably `rediss://...`, used by ARQ and job-status checks.
- Firebase Auth: production project or confirmed existing project, authorized Cloud Run/custom domains, backend service account configured through Secret Manager.
- Gemini API: API key in Secret Manager, injected as `LLM_API_KEY`.
- Resend: required for actual digest email delivery, even though it was not listed in the target architecture prompt.

## Findings by area

### 1. Application entrypoint and runtime

What is good:

- FastAPI entrypoint is clear: `app.main:app`.
- App factory is `create_app()` in `app/main.py`, with `app = create_app()` at module bottom.
- Existing web command binds to `0.0.0.0` and `$PORT` in `Procfile`, `railway.toml`, and `services/web/railway.toml`.
- Templates are loaded from the repo root `templates` directory via `Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))`.
- No separate frontend server was found.
- No app file-upload/local-storage feature was found.

Gaps and risks:

- No Dockerfile exists.
- The production web command should add proxy handling for Cloud Run, for example: `uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'`.
- No lightweight health endpoint exists. `/` is public, but it renders a template and may redirect authenticated users. A simple unauthenticated `/healthz` endpoint would be better for Cloud Run checks and monitoring.
- `worker.py` does not bind to `$PORT`. A plain `python worker.py` Cloud Run service will not satisfy Cloud Run's service contract unless wrapped with an HTTP listener.
- Runtime Python is inconsistent: the local virtualenv is Python 3.9.6, while `README.md` says Python 3.11+. Pick and test one production Python version before building.
- Localhost-only defaults exist in `app/config.py`: local Postgres, local Redis, Firebase service account path under `~/.config/...`.

### 2. Environment variables and secrets

Config source: `app/config.py`.

Production-required variables:

- `ENV=production`
- `DATABASE_URL`: Neon Postgres URL, adapted for SQLAlchemy asyncpg.
- `REDIS_URL`: Upstash Redis protocol URL, preferably `rediss://default:<password>@<host>:<port>`.
- `LLM_API_KEY`: Gemini API key. Legacy `GEMINI_API_KEY` still works, but new production config should use `LLM_API_KEY`.
- `JWT_SECRET`: high-entropy app session signing secret. Do not use the default `dev-secret-change-in-production-abc123`.
- `FIREBASE_SERVICE_ACCOUNT_JSON`: Firebase Admin service account JSON as a Secret Manager secret, or a secret-mounted file plus `FIREBASE_SERVICE_ACCOUNT_PATH`.
- `FIREBASE_WEB_API_KEY`
- `FIREBASE_WEB_AUTH_DOMAIN`
- `FIREBASE_WEB_PROJECT_ID`
- `FIREBASE_WEB_STORAGE_BUCKET`
- `FIREBASE_WEB_MESSAGING_SENDER_ID`
- `FIREBASE_WEB_APP_ID`
- `FIREBASE_WEB_MEASUREMENT_ID` if used.
- `RESEND_API_KEY`: needed for real email delivery.
- `RESEND_FROM_EMAIL`: verified sender address/domain.

Production tuning variables already supported:

- `LLM_RELEVANCE_MODELS`
- `LLM_SUMMARY_MODELS`
- `LLM_REQUEST_TIMEOUT_SECONDS`
- `LLM_RETRY_ATTEMPTS`
- `LLM_RETRY_BACKOFF_SECONDS`
- `LLM_SUMMARY_BATCH_SIZE`
- `LLM_SUMMARY_ARTICLE_MAX_CHARS`
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
- `ARQ_JOB_EXPIRES_SECONDS`
- `ARQ_JOB_TIMEOUT_SECONDS`
- `ARQ_MAX_TRIES`
- `QUEUED_DIGEST_RECOVERY_AFTER_MINUTES`
- `PROCESSING_DIGEST_RECOVERY_AFTER_MINUTES`
- `PIPELINE_RETRY_DEFER_SECONDS`

Recommended `.env.example` shape for a future change:

```env
ENV=development
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/smartdigest
REDIS_URL=redis://localhost:6379

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
RESEND_FROM_EMAIL=

SEMANTIC_RETRIEVAL_ENABLED=true
SEMANTIC_WARMUP_ENABLED=false
SEMANTIC_MODEL_LOCAL_FILES_ONLY=true

ARQ_JOB_TIMEOUT_SECONDS=900
ARQ_MAX_TRIES=4
ARQ_JOB_EXPIRES_SECONDS=604800
QUEUED_DIGEST_RECOVERY_AFTER_MINUTES=5
PROCESSING_DIGEST_RECOVERY_AFTER_MINUTES=30
PIPELINE_RETRY_DEFER_SECONDS=300
```

Secrets risk:

- `.env` exists locally and is ignored by `.gitignore`; it was not inspected.
- No tracked service account JSON or private key files were found in the quick scan.
- `app/config.py` contains a committed dev JWT secret default. This is acceptable only if production validation prevents it from being used.
- Firebase web config values are committed defaults. Firebase web config is public client config, not a service-account secret, but production should either confirm this is the intended Firebase project or override the values explicitly.

### 3. Database readiness

What is good:

- ORM is SQLAlchemy 2 async with `asyncpg`.
- Migrations exist under `alembic/versions`.
- `alembic/env.py` reads `DATABASE_URL` from the environment and converts `postgres://` or `postgresql://` to `postgresql+asyncpg://`.
- No `Base.metadata.create_all()` or auto-create-at-startup path was found.
- Durable state is in Postgres: `users`, `curated_sources`, `briefings`, `digests`, `digest_items`, `pipeline_events`.

Gaps and risks:

- Cloud Run deployment needs an explicit schema setup step: `alembic upgrade head`.
- Initial source data also needs `python -m app.cli seed_sources`. This is idempotent, but it should not run inside every web instance startup command.
- Neon connection strings often include SSL parameters. The current SQLAlchemy asyncpg path passes unknown query parameters through to `asyncpg`. If Neon supplies `sslmode=require`, smoke test it before deploy; it may need an asyncpg-compatible SSL form or a small future code/config adjustment.
- `app/models/api_key.py` and `app/schemas/keys.py` still exist as removed/stale surfaces, while `app/models/__init__.py` no longer exports `ApiKey`. This is not a launch blocker, but it can confuse future Alembic autogeneration and should be cleaned up later.
- Future schema changes must be Alembic revisions. Do not use runtime table creation.

Recommended migration strategy:

- Use Neon empty database for production. No local data migration is needed.
- Run `alembic upgrade head` once against the production database before shifting traffic.
- Run `python -m app.cli seed_sources` once after migrations.
- For future deploys, use a release step or Cloud Run Job to run migrations before traffic moves to the new revision.
- Keep migrations backward-compatible when possible so rollback to the previous Cloud Run revision remains safe.
- For destructive schema changes, use a two-step deploy: add new schema first, deploy code compatible with both, backfill/verify, then remove old schema later.

### 4. Redis/job queue readiness

What is good:

- Queue library is ARQ.
- Worker entrypoint is clear: `python worker.py`.
- Manual trigger path in `app/api/briefings.py` creates a `Digest(status="queued")`, enqueues `run_pipeline`, and returns `202`.
- Scheduled runs and recovery are in `worker.py` cron jobs:
  - `recover_queued_digests` every 5 minutes, with `run_at_startup=True`.
  - `enqueue_scheduled_digests` every 30 minutes, with `run_at_startup=True`.
- Duplicate pipeline protection uses Postgres advisory transaction locking in `app/services/scheduler.py`, not Redis.
- `ARQ_JOB_TIMEOUT_SECONDS`, `ARQ_MAX_TRIES`, and retry defer are configurable.

Gaps and risks:

- Current worker command is long-running and does not listen on `$PORT`, so it is not directly suitable as a Cloud Run service.
- If deployed as a Cloud Run service, the worker needs min instances set to 1 and CPU always allocated; otherwise ARQ jobs may sit in Redis with no worker polling them.
- `WorkerSettings` does not set `max_jobs`. ARQ defaults may allow more concurrent jobs than desired for a small app, especially because each job can fetch multiple pages and call Gemini.
- Upstash Redis charges/limits by commands and connections. ARQ polling plus job-status checks are probably fine at low volume, but should be monitored.
- `app/api/jobs.py` checks `zscore("arq:queue", job_id)` and `exists("arq:job:{job_id}")`. Those commands should be compatible with Redis protocol Upstash, but this endpoint should not be polled aggressively.
- Use the Upstash Redis protocol URL, not the Upstash REST URL or REST token.
- Prefer `rediss://` for TLS.

Recommended worker deployment shape:

- First production shape: separate Cloud Run worker service with:
  - a tiny HTTP health listener on `$PORT`,
  - ARQ worker running in the same container,
  - min instances 1,
  - CPU always allocated,
  - low concurrency/max jobs.
- Alternative later: replace ARQ cron with Cloud Scheduler plus Cloud Run Jobs only if the repo gets a bounded worker command that can drain/process due work and exit.

### 5. Firebase Auth readiness

What is good:

- Firebase Auth is already integrated.
- `templates/login.html` uses Firebase client SDK for email/password and Google sign-in.
- `/auth/firebase/session` verifies Firebase ID tokens server-side with Firebase Admin SDK.
- The app sets an httpOnly session cookie through `app/api/auth.py`.
- Protected routes go through `SessionAuthMiddleware`.
- In production, the session cookie is marked `secure` when `ENV=production`.

Gaps and risks:

- Cloud Run cannot rely on the default local `FIREBASE_SERVICE_ACCOUNT_PATH`. Use `FIREBASE_SERVICE_ACCOUNT_JSON` from Secret Manager or mount the secret file and set `FIREBASE_SERVICE_ACCOUNT_PATH`.
- Firebase authorized domains must include the Cloud Run service domain and any future custom domain.
- If using Google sign-in, configure Google provider in Firebase Auth and confirm the OAuth consent screen/domain setup.
- Password reset email behavior depends on Firebase Auth email templates and authorized action domains.
- No `ALLOWED_ORIGINS` or CORS config exists. That is fine for the current server-rendered app, but do not split frontend hosting without adding explicit CORS and cookie-domain planning.
- There is no admin role check for admin-like metrics pages.

### 6. Gemini API readiness

What is good:

- Gemini calls are centralized through `app/services/llm/google_genai.py`.
- API keys are read from settings, preferring `LLM_API_KEY` and accepting legacy `GEMINI_API_KEY`.
- Model fallback, timeout, retry attempts, backoff, max output tokens, batch size, and article character caps are configurable.
- Transient provider failures can raise `LLMRetryableError`; scheduler can requeue retryable filter/summarise stages.
- Logs include model, call name, attempt, status code, and short error message.

Gaps and risks:

- If no LLM API key is configured, `LLMRelevanceFilter.score_and_filter()` marks candidates with default score 6 and `summarise_articles()` returns placeholder summaries. In production this should be a startup config failure, not a silent quality fallback.
- `RESEND_API_KEY` has similar dev-mode behavior: absent key logs email and returns success. Production should fail validation if real email delivery is expected.
- Need per-user or global cost controls beyond the current manual trigger rate limit. Existing manual trigger limit is `3/hour` in `app/api/briefings.py`.
- Cloud Run worker concurrency should be low to protect Gemini spend and external source fetch volume.
- Consider logging request sizes, selected model, article count, and final token/cost estimates if Gemini exposes usage metadata.

### 7. Cloud Run deployment readiness

What is good:

- Existing Railway files identify the correct web and worker process commands.
- `Procfile` already uses `$PORT` for the web process.
- App does not require persistent disk for current features.
- Templates are packaged in the repo and should work in a container if copied.

Gaps and risks:

- No Dockerfile exists.
- No Cloud Run service/job config exists.
- No health endpoint exists.
- Worker process needs Cloud Run-specific handling, as described above.
- Startup tasks should not run in every web instance. Do not put `alembic upgrade head` or `python -m app.cli seed_sources` in the web service command.
- The app imports `sentence-transformers` and `trafilatura`, which can increase image size and memory use. Test Cloud Run memory before assuming the smallest tier works.
- Semantic retrieval defaults to `SEMANTIC_MODEL_LOCAL_FILES_ONLY=true`. Unless the sentence-transformers model is baked into the image/cache, semantic retrieval will fail soft and BM25 will carry retrieval. For the first minimal deployment, either disable semantic retrieval or deliberately package the model and allocate enough memory.

Conceptual Dockerfile contents for a later implementation:

- Pin Python version, likely `python:3.11-slim` if you choose to match README, or use 3.9 if you choose to match the current local venv.
- Install system packages needed by Python dependencies if required.
- Install `requirements.txt`.
- Copy `app/`, `alembic/`, `templates/`, `worker.py`, `alembic.ini`.
- Set non-root user if practical.
- Web command should run Uvicorn on `${PORT}` with proxy headers.
- Worker image can reuse the same image but needs a different command.

Suggested initial Cloud Run sizing:

- Web service: 512 MiB to 1 GiB memory, 1 CPU, concurrency 20-40, max instances low while testing, min instances 0 unless you need warm start.
- Worker service: 1-2 GiB memory if semantic model is enabled; otherwise 512 MiB to 1 GiB may be enough. Set min instances 1, CPU always allocated, concurrency effectively 1 at the app/worker level.
- Start with conservative max instances to protect Neon, Upstash, Gemini, and source sites.

### 8. Production safety and maintainability

Already good:

- Structured logging through `structlog`, JSON renderer in production.
- Pipeline events stored in Postgres for fetch/filter/summarise/deliver observability.
- Manual trigger is rate-limited.
- Most SQL is SQLAlchemy expression API, not string-concatenated SQL.
- Ownership checks exist for briefings and digest detail endpoints.
- Secure cookie flag is tied to `ENV=production`.

Risks to fix before public production:

- `app/api/jobs.py` does not verify that the authenticated user owns the digest/job being inspected. Any logged-in user who guesses a digest ID can see status and latest pipeline event details.
- Metrics pages and metrics APIs expose global pipeline health to any authenticated user. This may leak cross-user operational data and error messages.
- No explicit production config validation. The app can start with dev defaults or missing critical secrets.
- Missing health endpoint.
- Worker not Cloud Run service-ready.
- Real email delivery depends on `RESEND_API_KEY`; otherwise production can appear successful while no email is sent.
- LLM missing-key behavior can produce placeholder/low-quality digests instead of failing fast.

Security concerns to review:

- CSRF protection: state-changing browser routes use cookie auth and `fetch()`/forms without CSRF tokens. `SameSite=Lax` reduces risk, but add CSRF protection before broader public use.
- Admin routes: `/metrics` and `/app/admin/metrics` are authenticated but not admin-restricted.
- API docs: `/docs`, `/redoc`, and `/openapi.json` are public through middleware exclusions. Decide whether to disable/protect them in production.
- Cookies: production `secure=True` is good; keep `SameSite=Lax` unless a future cross-site frontend requires another policy.
- CORS: no CORS is configured, which is good for the current same-origin server-rendered app.
- Secrets: use Secret Manager, not committed files or local paths.
- Error messages: pipeline error messages may include provider/source details; avoid exposing them to non-admin users.
- External scripts: templates load Firebase SDK, HTMX, and fonts from CDNs. Add security headers/CSP later if you keep CDN usage.

## Risks and blockers

Deployment blockers:

1. Worker is not Cloud Run service-ready because `python worker.py` does not listen on `$PORT`.
2. No production config validation; production could run with missing `LLM_API_KEY`, missing `RESEND_API_KEY`, default `JWT_SECRET`, local DB/Redis defaults, or missing Firebase Admin credentials.
3. No explicit `/healthz` endpoint.
4. Job status endpoint lacks digest ownership enforcement.
5. Metrics pages/API expose global pipeline data to any authenticated user.
6. Need to validate Neon SSL connection-string compatibility with SQLAlchemy asyncpg.

Operational risks:

1. Semantic retrieval may silently disable itself on Cloud Run unless the model is packaged or the feature is disabled.
2. Worker concurrency and ARQ defaults may exceed small-app cost expectations.
3. Upstash command/connection limits need monitoring.
4. README and existing Railway docs contain stale references to old auth/API behavior and Railway deployment.
5. No Cloud Run-specific deploy/runbook files exist yet.

## Recommended changes by priority

### Must do before deployment

- Add production config validation in `app/config.py` or startup code:
  - fail if `ENV=production` and `JWT_SECRET` is default/short,
  - fail if `DATABASE_URL` or `REDIS_URL` is local/default,
  - fail if `LLM_API_KEY`/`GEMINI_API_KEY` is missing,
  - fail if `RESEND_API_KEY` is missing for real email delivery,
  - fail if Firebase Admin credentials are missing,
  - confirm Firebase web config is explicitly set.
- Add unauthenticated lightweight health endpoint, for example `/healthz`, and exclude it from auth middleware.
- Decide and implement Cloud Run worker shape:
  - long-running worker service with HTTP health listener, min instances 1, CPU always allocated, or
  - add a finite drain command if you want a Cloud Run Job instead.
- Protect `app/api/jobs.py` by verifying the digest belongs to `request.state.user_id`.
- Restrict global metrics pages/API to an admin role or remove global details from normal users.
- Decide initial semantic retrieval behavior on Cloud Run:
  - disable with `SEMANTIC_RETRIEVAL_ENABLED=false`, or
  - package/warm the model and allocate enough memory.
- Create Cloud Run deployment files or scripts later:
  - Dockerfile or buildpack config,
  - web service command,
  - worker service/job command,
  - migration/seed release job.
- Validate Neon URL with `alembic upgrade head` and a simple app DB connection smoke test.
- Use Upstash Redis protocol URL with TLS and smoke test ARQ enqueue/dequeue.
- Configure Firebase authorized domains for Cloud Run/custom domain.

### Should do soon after deployment

- Add CSRF protection for cookie-auth state-changing endpoints.
- Add admin/user role model if metrics/admin pages should exist.
- Add security headers/CSP, especially because external scripts are loaded from CDNs.
- Make ARQ worker concurrency explicit and low, for example a future `ARQ_MAX_JOBS` setting.
- Add real queue visibility and owner-safe job history if users need progress details.
- Add deployment smoke tests:
  - login,
  - create briefing,
  - trigger digest,
  - worker processes job,
  - email is delivered,
  - metrics update,
  - logout.
- Add logging correlation IDs for digest/job IDs across web enqueue and worker pipeline.
- Add provider usage/cost logging for Gemini if SDK exposes usage metadata.
- Update stale README deployment/auth sections after Cloud Run plan is implemented.

### Nice to have later

- Custom domain and Firebase/Auth email template polish.
- Cloud Monitoring dashboards and alert policies for worker failures, failed digests, queue backlog, and email failures.
- Error tracking service.
- More complete test coverage around auth, ownership, and production config validation.
- Clean up removed API key model/schema files if confirmed unused.
- Consider moving scheduled enqueueing from ARQ cron to Cloud Scheduler later, but only if it simplifies operations.

## Suggested deployment shape

### Web service

- Cloud Run service name: `smartdigest-web`.
- Command: `uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'`.
- Receives public traffic.
- Runs no migrations or seeding at startup.
- Has `ENV=production` and all required secrets injected.
- Uses Neon and Upstash URLs.

### Worker service/job

Current best fit: separate long-running Cloud Run service after adding health listener support.

- Service name: `smartdigest-worker`.
- Internal/no public user traffic.
- Runs ARQ worker logic from `worker.py`.
- Needs to listen on `$PORT` for Cloud Run health.
- Set min instances 1.
- Set CPU always allocated.
- Keep concurrency/job count low.

Do not deploy current `python worker.py` as a plain Cloud Run service without a port listener.

### Database

- Neon Postgres, empty production database.
- Run schema migrations: `alembic upgrade head`.
- Seed curated sources: `python -m app.cli seed_sources`.
- No local data migration required.

### Redis

- Upstash Redis using Redis protocol URL.
- Prefer `rediss://...` TLS URL.
- Use same `REDIS_URL` for web enqueue and worker dequeue.
- Do not use Upstash REST token/URL for ARQ.

### Auth

- Firebase Auth project with Email/Password and Google provider enabled if desired.
- Backend verifies Firebase ID tokens using Admin SDK.
- App session cookie remains `sd_session`.
- Authorized domains must include Cloud Run URL and future custom domain.

### Gemini

- Store API key in Secret Manager and inject as `LLM_API_KEY`.
- Keep model fallback configurable:
  - `LLM_RELEVANCE_MODELS=gemini-2.5-flash-lite,gemini-2.5-flash`
  - `LLM_SUMMARY_MODELS=gemini-2.5-flash-lite,gemini-2.5-flash`
- Use conservative worker concurrency and current batching/char caps to control cost.

## What not to overcomplicate

- Do not introduce Kubernetes.
- Do not switch to Cloud SQL unless Neon becomes a repo-specific blocker.
- Do not switch to Memorystore unless Upstash proves incompatible with ARQ or command volume.
- Do not split frontend hosting; the repo is server-rendered.
- Do not rewrite the queue system before first deployment. Fix the Cloud Run worker shape and security/config blockers first.
- Do not run migrations from every web container startup.
