# My Deployment Prep Checklist

This is the outside-the-repo checklist to complete before asking Codex to help deploy SmartDigest to production on Google Cloud Run.

Do not paste secrets into public chats, screenshots, GitHub issues, or committed files. When Codex needs secret values later, provide them only in the local Codex thread or put them directly into Google Secret Manager and tell Codex the secret names.

## 1. Google Cloud setup

- Create or choose a Google Cloud account.
- Create a Google Cloud project for SmartDigest.
- Enable billing or free credits.
- Decide the project ID. Save it as:
  - `GCP_PROJECT_ID=...`
- Install and configure the `gcloud` CLI if it is not already installed:
  - `gcloud auth login`
  - `gcloud config set project <your-project-id>`
- Enable required services:
  - Cloud Run
  - Artifact Registry
  - Cloud Build
  - Secret Manager
  - IAM
  - Cloud Logging and Cloud Monitoring
- Choose a Cloud Run region. Good first choices are regions close to you or your users, for example:
  - `us-central1`
  - `us-east1`
  - `europe-west1`
- Save the chosen region as:
  - `CLOUD_RUN_REGION=...`

Values to provide back to Codex:

- Google Cloud project ID
- Cloud Run region
- Whether `gcloud` is installed and authenticated locally

## 2. Neon Postgres setup

- Create a Neon account.
- Create a Neon project for SmartDigest.
- Create a production database.
- Save the database name.
- Get the connection string.
- Prefer a direct connection string for the first small deployment unless you intentionally want Neon pooling.
- Confirm the connection requires SSL.

Value to provide back to Codex:

- `DATABASE_URL`

Important:

- This app uses SQLAlchemy asyncpg. The repo converts `postgres://` and `postgresql://` to `postgresql+asyncpg://`, but the exact Neon SSL query parameters still need a smoke test.
- If the Neon URL includes `sslmode=require`, keep it available, but expect Codex to test whether it works with the current asyncpg setup.
- No local database data needs to be migrated. Only schema setup is needed.

## 3. Upstash Redis setup

- Create an Upstash account.
- Create a Redis database for production.
- Choose the same general region as Cloud Run if possible.
- Get the Redis protocol connection URL.
- Prefer the TLS URL, usually beginning with `rediss://`.

Value to provide back to Codex:

- `REDIS_URL`

Important:

- Do not provide only the Upstash REST URL or REST token for this app.
- ARQ needs the Redis protocol URL.
- Keep the Upstash password/token private.

## 4. Firebase Auth setup

- Create or choose a Firebase project.
- Enable Firebase Authentication.
- Enable Email/Password sign-in if you want password accounts.
- Enable Google sign-in if you want "Continue with Google".
- Add authorized domains:
  - the Cloud Run service domain after it exists,
  - any custom domain later,
  - `localhost` for local development if needed.
- Configure password reset/action email settings if you will use password reset.
- Create a Firebase Admin SDK service account key.
- Store the service account JSON securely. In production it should go into Google Secret Manager, not the repo.

Values to provide back to Codex:

- `FIREBASE_WEB_API_KEY`
- `FIREBASE_WEB_AUTH_DOMAIN`
- `FIREBASE_WEB_PROJECT_ID`
- `FIREBASE_WEB_STORAGE_BUCKET`
- `FIREBASE_WEB_MESSAGING_SENDER_ID`
- `FIREBASE_WEB_APP_ID`
- `FIREBASE_WEB_MEASUREMENT_ID` if Firebase gives one
- Either the Secret Manager secret name for the service account JSON, or the service account JSON value if you are adding it locally to Secret Manager with Codex

Do not share publicly:

- Firebase Admin service account JSON
- private key
- client email private key block

Note:

- Firebase web config is public client config, but still provide the exact production values so the app uses the intended Firebase project.

## 5. Gemini API setup

- Create or choose a Google AI Studio / Gemini API project.
- Create a Gemini API key.
- Decide initial model fallback values. The repo already supports:
  - `gemini-2.5-flash-lite`
  - `gemini-2.5-flash`

Values to provide back to Codex:

- `LLM_API_KEY`
- `LLM_RELEVANCE_MODELS`, recommended initial value: `gemini-2.5-flash-lite,gemini-2.5-flash`
- `LLM_SUMMARY_MODELS`, recommended initial value: `gemini-2.5-flash-lite,gemini-2.5-flash`

Do not share publicly:

- Gemini API key

## 6. Email delivery setup

The repo currently sends digest emails with Resend.

- Create or choose a Resend account.
- Verify a sending domain or sender email.
- Create a Resend API key.
- Decide the production sender address.

Values to provide back to Codex:

- `RESEND_API_KEY`
- `RESEND_FROM_EMAIL`

Important:

- If `RESEND_API_KEY` is missing, the current app logs email content and returns success in dev mode. For production, use a real key so digests actually send.

## 7. App secrets and production env values

Create or decide these values:

- `ENV=production`
- `JWT_SECRET`
  - Generate a long random value, for example with `openssl rand -hex 32`.
- `DATABASE_URL`
- `REDIS_URL`
- `LLM_API_KEY`
- `RESEND_API_KEY`
- `RESEND_FROM_EMAIL`
- Firebase web config values
- Firebase Admin service account secret

Optional tuning values to decide later:

- `LLM_REQUEST_TIMEOUT_SECONDS`
- `LLM_RETRY_ATTEMPTS`
- `LLM_RETRY_BACKOFF_SECONDS`
- `LLM_SUMMARY_BATCH_SIZE`
- `LLM_SUMMARY_ARTICLE_MAX_CHARS`
- `SEMANTIC_RETRIEVAL_ENABLED`
- `SEMANTIC_WARMUP_ENABLED`
- `ARQ_JOB_TIMEOUT_SECONDS`
- `ARQ_MAX_TRIES`
- `ARQ_JOB_EXPIRES_SECONDS`

Recommended first deployment choices:

- `SEMANTIC_RETRIEVAL_ENABLED=false` unless you want to package the sentence-transformers model into the Cloud Run image.
- Keep Gemini batch sizes conservative.
- Keep worker concurrency low.

## 8. Domain decision

For first deployment:

- Use the default Cloud Run URL.

Later:

- Decide a custom domain.
- Map the domain in Cloud Run.
- Add the custom domain to Firebase authorized domains.
- Update any email templates or app links if needed.

Value to provide back to Codex later:

- Custom domain, if you want one now

## 9. What Codex needs from me before deploy work starts

Provide these exact values or tell Codex where they are stored in Secret Manager:

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

- whether you want Python 3.11 or to match the current local Python 3.9.6 runtime,
- whether semantic retrieval should be disabled for the first deploy,
- whether you want public FastAPI docs disabled in production,
- whether metrics should be admin-only before launch.

## 10. What not to share publicly

Never share these publicly:

- `DATABASE_URL`
- `REDIS_URL`
- `LLM_API_KEY`
- `RESEND_API_KEY`
- `JWT_SECRET`
- Firebase Admin service account JSON
- private keys
- Upstash tokens/passwords
- Neon passwords

Okay to be visible in frontend code, but still keep exact production values intentional:

- Firebase web API key
- Firebase auth domain
- Firebase project ID
- Firebase app ID

## 11. Ready to deploy checklist

Before telling Codex "deploy it", confirm:

- Google Cloud project exists.
- Billing/free credits are active.
- `gcloud` is installed and authenticated, or you want Codex to help install/configure it.
- Cloud Run region is chosen.
- Neon database exists.
- Neon `DATABASE_URL` is available.
- Upstash Redis database exists.
- Upstash Redis protocol `REDIS_URL` is available.
- Firebase Auth project is configured.
- Firebase Email/Password and/or Google provider are enabled.
- Firebase Admin service account JSON is available privately.
- Gemini API key exists.
- Resend API key and sender address exist.
- JWT secret is generated.
- You have decided whether to use the Cloud Run default URL first or a custom domain.
- You understand that local database data will not be migrated.
- You are ready for Codex to add deployment files and production hardening code in a later implementation phase.
