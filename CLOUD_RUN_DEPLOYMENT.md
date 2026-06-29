# SmartDigest Cloud Run Deployment

SmartDigest uses one container image in three separate Cloud Run resources:

- `smartdigest-web`: a public Cloud Run service running FastAPI.
- `smartdigest-worker`: a private Cloud Run worker pool running the continuous ARQ worker.
- `smartdigest-release`: an on-demand Cloud Run job that runs migrations and seeds sources before a release.

The web service and worker pool share Neon Postgres and Upstash Redis. They do not need to run in the same Cloud Run instance.

## Repository-provided deployment behavior

- `Dockerfile` builds the shared Python 3.11 image and defaults to the FastAPI command.
- `deploy/*.env.yaml` contains non-secret, role-specific production settings.
- `scripts/run_release_tasks.sh` runs Alembic and source seeding as a bounded release task.
- `scripts/deploy_gcloud.sh` creates least-privilege runtime service accounts, grants per-secret access, builds the image, runs the release job, deploys FastAPI, and deploys one ARQ worker-pool instance.
- Production configuration fails at startup when required role-specific secrets are absent or unsafe.
- The first worker deployment disables semantic retrieval and the local reranker. BM25 and LLM relevance remain active. Package the model weights into the image before enabling those stages in production.

## Run from the repository root

Choose a Cloud Run region that supports worker pools, then run:

```bash
bash scripts/deploy_gcloud.sh YOUR_PROJECT_ID YOUR_REGION
```

The script performs Google Cloud writes and can incur charges. It intentionally pins Secret Manager environment variables to version `1`. To deploy a later secret version:

```bash
SECRET_VERSION=2 bash scripts/deploy_gcloud.sh YOUR_PROJECT_ID YOUR_REGION
```

## External prerequisites

- Billing is enabled for the Google Cloud project.
- The deploying account can enable APIs, create Artifact Registry repositories, create service accounts, modify secret IAM policies, run Cloud Build, and deploy Cloud Run resources.
- Secret Manager contains `DATABASE_URL`, `REDIS_URL`, `LLM_API_KEY`, `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `JWT_SECRET`, and `FIREBASE_SERVICE_ACCOUNT_JSON`.
- The selected region supports Cloud Run worker pools.
- The Resend sender domain is verified.

## Post-deployment checks

1. Open the printed `/healthz` URL and confirm it returns `ok`.
2. Add the Cloud Run hostname to Firebase Authentication authorized domains.
3. Sign in and create a briefing.
4. Trigger a digest and confirm the web service enqueues it.
5. Confirm the worker pool consumes the job and the digest reaches a terminal status.
6. Confirm a real email arrives through Resend.
7. Review Cloud Run logs for secret, database, Redis, LLM, or model-loading errors.
