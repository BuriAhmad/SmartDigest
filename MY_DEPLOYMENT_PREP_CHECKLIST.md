# My Deployment Prep Checklist

This is the outside-the-repo checklist to complete before asking Codex to help deploy SmartDigest to production on Google Cloud Run.

Do not paste secrets into public chats, screenshots, GitHub issues, or committed files. When Codex needs secret values later, provide them only in this local Codex thread, or put them directly into Google Secret Manager and tell Codex the secret names.

## 1. Google Cloud Account And Project

- Create or choose a Google Cloud account.
- Create a Google Cloud project for SmartDigest.
- Enable billing or free credits.
- Decide the project ID.
- Save it as:

```text
GCP_PROJECT_ID=
```

- Install and configure the `gcloud` CLI if needed:

```bash
gcloud auth login
gcloud config set project <your-project-id>
```

- Enable the required Google Cloud services:
  - Cloud Run
  - Artifact Registry
  - Cloud Build
  - Secret Manager
  - IAM
  - Cloud Logging
  - Cloud Monitoring

Values to provide back to Codex:

- Google Cloud project ID.
- Whether billing/free credits are active.
- Whether `gcloud` is installed and authenticated locally.

## 2. Cloud Run Region

Choose a Cloud Run region. Good first choices are:

- `us-central1`
- `us-east1`
- `europe-west1`

Pick a region close to you, your expected users, Neon, and Upstash if possible.

Save it as:

```text
CLOUD_RUN_REGION=
```

Value to provide back to Codex:

- Chosen Cloud Run region.
- Current likely choice: `us-central1`.

## 3. Neon Postgres

- Neon project/database created for SmartDigest.
- Provider: Neon Postgres.
- Neon region: AWS US East 2 / Ohio.
- Database name: `neondb`.
- Role: `neondb_owner`.
- Direct host: `ep-proud-poetry-ajc5pujb.c-3.us-east-2.aws.neon.tech`.
- Pooler host, not currently selected: `ep-proud-poetry-ajc5pujb-pooler.c-3.us-east-2.aws.neon.tech`.
- SSL is required.
- Direct connection is selected for first deployment validation. Do not switch to Neon pooling without a specific reason and approval.

Value to provide back to Codex:

```text
DATABASE_URL=
```

Important notes:

- This app uses SQLAlchemy asyncpg.
- The repo converts `postgres://` and `postgresql://` to `postgresql+asyncpg://`.
- The repo now translates Neon-style `sslmode=require` into asyncpg's native `ssl=True` connection argument.
- Local Neon validation ran successfully: DB smoke test, Alembic migration to head, source seeding, and table/source verification.
- No local database data needs to be migrated. Only schema setup is needed.

## 4. Upstash Redis

- Create an Upstash account.
- Create a Redis database for production. Upstash has been selected as the Redis provider for SmartDigest.
- Choose the same general region as Cloud Run if possible.
- Get the Redis protocol connection URL, not the REST endpoint.
- Prefer the TLS URL, usually beginning with `rediss://`.

Value to provide back to Codex:

```text
REDIS_URL=
```

Expected production shape:

```text
REDIS_URL=rediss://default:<password>@<host>:6379
```

Important notes:

- SmartDigest uses only `REDIS_URL` for Upstash Redis.
- `REDIS_URL` is used by ARQ for enqueue/dequeue and by the job-status endpoint for Redis queue checks.
- Do not provide only the Upstash REST URL.
- Do not provide only the Upstash REST token.
- Do not add `UPSTASH_REDIS_REST_URL` or `UPSTASH_REDIS_REST_TOKEN` unless a future feature intentionally uses the Upstash REST client.
- ARQ needs the Redis protocol URL; the REST URL/token are not needed for the current app.
- Keep the Upstash password/token private.
- Run the local Redis smoke test after setting `REDIS_URL`:

```bash
python scripts/smoke_redis.py
```

- If an Upstash password/token is pasted into chat, a screenshot, an issue, or any other exposed place, rotate/reset it before production and update Google Secret Manager/local `.env` with the fresh value.

## 5. Firebase Auth

- Create or choose a Firebase project.
- Enable Firebase Authentication.
- Enable Email/Password sign-in if you want password accounts.
- Enable Google sign-in if you want "Continue with Google".
- Configure password reset/action email settings if password login will be used.
- Add authorized domains:
  - `localhost` for local development if needed;
  - the Cloud Run service domain after the first deploy gives you one;
  - any custom domain later.
- Create a Firebase Admin SDK service account key.
- Store the service account JSON securely. In production it should go into Google Secret Manager, not the repo.

Values to provide back to Codex:

```text
FIREBASE_WEB_API_KEY=
FIREBASE_WEB_AUTH_DOMAIN=
FIREBASE_WEB_PROJECT_ID=
FIREBASE_WEB_STORAGE_BUCKET=
FIREBASE_WEB_MESSAGING_SENDER_ID=
FIREBASE_WEB_APP_ID=
FIREBASE_WEB_MEASUREMENT_ID=
FIREBASE_SERVICE_ACCOUNT_JSON secret name or value=
```

Do not share publicly:

- Firebase Admin service account JSON.
- Firebase private key.
- Firebase service account private key block.

Note:

- Firebase web config is public client config, but you should still provide the exact production values so the app uses the intended Firebase project.

## 6. Gemini API

- Create or choose a Google AI Studio / Gemini API project.
- Create a Gemini API key.
- Decide initial model fallback values.

Recommended initial values:

```text
LLM_RELEVANCE_MODELS=gemini-2.5-flash-lite,gemini-2.5-flash
LLM_SUMMARY_MODELS=gemini-2.5-flash-lite,gemini-2.5-flash
```

Values to provide back to Codex:

```text
LLM_API_KEY secret name or value=
LLM_RELEVANCE_MODELS=
LLM_SUMMARY_MODELS=
```

Do not share publicly:

- Gemini API key.

## 7. Email Delivery With Resend

The current repo sends digest emails with Resend.

- Create or choose a Resend account.
- Verify a sending domain or sender email.
- Create a Resend API key.
- Decide the production sender address.

Values to provide back to Codex:

```text
RESEND_API_KEY secret name or value=
RESEND_FROM_EMAIL=
```

Important:

- If `RESEND_API_KEY` is missing, the current app logs email content and returns success in dev mode.
- For production, use a real key so digests actually send.

## 8. App Secrets And Production Environment Values

Create or decide these required values:

```text
ENV=production
JWT_SECRET=
DATABASE_URL=
REDIS_URL=
LLM_API_KEY=
RESEND_API_KEY=
RESEND_FROM_EMAIL=
FIREBASE_SERVICE_ACCOUNT_JSON=
FIREBASE_WEB_API_KEY=
FIREBASE_WEB_AUTH_DOMAIN=
FIREBASE_WEB_PROJECT_ID=
FIREBASE_WEB_STORAGE_BUCKET=
FIREBASE_WEB_MESSAGING_SENDER_ID=
FIREBASE_WEB_APP_ID=
FIREBASE_WEB_MEASUREMENT_ID=
```

Generate `JWT_SECRET` as a long random value, for example:

```bash
openssl rand -hex 32
```

Optional tuning values to decide later:

```text
LLM_REQUEST_TIMEOUT_SECONDS=
LLM_RETRY_ATTEMPTS=
LLM_RETRY_BACKOFF_SECONDS=
LLM_SUMMARY_BATCH_SIZE=
LLM_SUMMARY_ARTICLE_MAX_CHARS=
SEMANTIC_RETRIEVAL_ENABLED=
SEMANTIC_WARMUP_ENABLED=
RERANKER_ENABLED=
RERANKER_REQUIRED=
ARQ_JOB_TIMEOUT_SECONDS=
ARQ_MAX_TRIES=
ARQ_JOB_EXPIRES_SECONDS=
QUEUED_DIGEST_RECOVERY_AFTER_MINUTES=
PROCESSING_DIGEST_RECOVERY_AFTER_MINUTES=
PIPELINE_RETRY_DEFER_SECONDS=
```

Recommended first deployment choices:

- Set `SEMANTIC_RETRIEVAL_ENABLED=false` unless you want Codex to package the sentence-transformers model into the Cloud Run image.
- Set `RERANKER_ENABLED=false` for the simplest first deploy, or ask Codex to package/cache the reranker model and allocate more memory.
- Keep Gemini batch sizes conservative.
- Keep worker concurrency low.

## 9. Domain Decision

For first deployment:

- Use the default Cloud Run URL.

Later:

- Decide a custom domain.
- Map the custom domain in Cloud Run.
- Add the custom domain to Firebase authorized domains.
- Update Firebase action/email settings if needed.

Value to provide back to Codex if you want a custom domain now:

```text
CUSTOM_DOMAIN=
```

## 10. Exact Values Codex Needs Later

Provide these values or tell Codex the Secret Manager secret names:

```text
GCP_PROJECT_ID=
CLOUD_RUN_REGION=

DATABASE_URL=
REDIS_URL=

FIREBASE_WEB_API_KEY=
FIREBASE_WEB_AUTH_DOMAIN=
FIREBASE_WEB_PROJECT_ID=
FIREBASE_WEB_STORAGE_BUCKET=
FIREBASE_WEB_MESSAGING_SENDER_ID=
FIREBASE_WEB_APP_ID=
FIREBASE_WEB_MEASUREMENT_ID=
FIREBASE_SERVICE_ACCOUNT_JSON secret name or value=

LLM_API_KEY secret name or value=
LLM_RELEVANCE_MODELS=
LLM_SUMMARY_MODELS=

RESEND_API_KEY secret name or value=
RESEND_FROM_EMAIL=

JWT_SECRET secret name or value=

CUSTOM_DOMAIN=optional
```

Also tell Codex:

- whether you want to use Python 3.11 for Cloud Run;
- whether semantic retrieval should be disabled for the first deploy;
- whether reranking should be disabled for the first deploy;
- whether public FastAPI docs should be disabled in production;
- whether metrics should be admin-only before launch.

## 11. What Not To Share Publicly

Never share these publicly:

- `DATABASE_URL`
- `REDIS_URL`
- `LLM_API_KEY`
- `RESEND_API_KEY`
- `JWT_SECRET`
- Firebase Admin service account JSON
- Firebase private key
- Upstash tokens/passwords
- Neon passwords

Okay to be visible in frontend code, but keep exact production values intentional:

- Firebase web API key
- Firebase auth domain
- Firebase project ID
- Firebase app ID

## 12. Ready To Deploy Checklist

Before telling Codex "deploy it", confirm:

- Google Cloud project exists.
- Billing/free credits are active.
- `gcloud` is installed and authenticated, or you want Codex to help install/configure it.
- Cloud Run region is chosen.
- Neon database exists in AWS US East 2 / Ohio.
- Neon direct-host `DATABASE_URL` is available privately in local `.env`.
- Neon schema setup has been validated with `alembic upgrade head`.
- Neon curated source seeding has been validated with `python -m app.cli seed_sources`.
- Neon DB smoke/table check passes with asyncpg SSL translation.
- Upstash Redis database exists.
- Upstash Redis protocol TLS `REDIS_URL` is available.
- The local Redis smoke test passes against the intended `REDIS_URL`.
- Any Upstash password/token that was pasted or exposed has been rotated before production.
- Firebase Auth project is configured.
- Firebase Email/Password and/or Google provider are enabled.
- Firebase authorized domains can be updated after Cloud Run URL is known.
- Firebase Admin service account JSON is available privately.
- Gemini API key exists.
- Resend API key and sender address exist.
- JWT secret is generated.
- You have decided whether to use the Cloud Run default URL first or a custom domain.
- You have decided whether to disable semantic retrieval and reranking for the first deploy.
- You understand that local database data will not be migrated.
- You are ready for Codex to add deployment files and production hardening code in a later implementation phase.
