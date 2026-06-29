#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${1:-}"
REGION="${2:-}"
SECRET_VERSION="${SECRET_VERSION:-1}"
ARTIFACT_REPOSITORY="${ARTIFACT_REPOSITORY:-smartdigest}"
WEB_SERVICE="${WEB_SERVICE:-smartdigest-web}"
WORKER_POOL="${WORKER_POOL:-smartdigest-worker}"
RELEASE_JOB="${RELEASE_JOB:-smartdigest-release}"
IMAGE_TAG="${IMAGE_TAG:-$(date -u +%Y%m%d-%H%M%S)}"

if [ -z "$PROJECT_ID" ] || [ -z "$REGION" ]; then
  echo "Usage: $0 <gcp-project-id> <region>" >&2
  echo "Example: $0 smartdigest-500718 us-central1" >&2
  exit 1
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud is not installed" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

WEB_SA_NAME="smartdigest-web"
WORKER_SA_NAME="smartdigest-worker"
RELEASE_SA_NAME="smartdigest-release"
WEB_SA="${WEB_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
WORKER_SA="${WORKER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
RELEASE_SA="${RELEASE_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPOSITORY}/smartdigest:${IMAGE_TAG}"

ensure_service_account() {
  local name="$1"
  local email="$2"
  local display_name="$3"
  if ! gcloud iam service-accounts describe "$email" --project "$PROJECT_ID" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$name" \
      --project "$PROJECT_ID" \
      --display-name "$display_name"
  fi
}

grant_secret_access() {
  local secret="$1"
  local service_account="$2"
  gcloud secrets add-iam-policy-binding "$secret" \
    --project "$PROJECT_ID" \
    --member "serviceAccount:${service_account}" \
    --role roles/secretmanager.secretAccessor \
    --quiet >/dev/null
}

echo "Enabling required Google Cloud APIs..."
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  iam.googleapis.com \
  secretmanager.googleapis.com \
  --project "$PROJECT_ID"

if ! gcloud artifacts repositories describe "$ARTIFACT_REPOSITORY" \
  --project "$PROJECT_ID" \
  --location "$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$ARTIFACT_REPOSITORY" \
    --project "$PROJECT_ID" \
    --location "$REGION" \
    --repository-format docker \
    --description "SmartDigest container images"
fi

ensure_service_account "$WEB_SA_NAME" "$WEB_SA" "SmartDigest web runtime"
ensure_service_account "$WORKER_SA_NAME" "$WORKER_SA" "SmartDigest worker runtime"
ensure_service_account "$RELEASE_SA_NAME" "$RELEASE_SA" "SmartDigest release job"

for secret in DATABASE_URL REDIS_URL JWT_SECRET FIREBASE_SERVICE_ACCOUNT_JSON; do
  grant_secret_access "$secret" "$WEB_SA"
done

for secret in DATABASE_URL REDIS_URL LLM_API_KEY RESEND_API_KEY RESEND_FROM_EMAIL; do
  grant_secret_access "$secret" "$WORKER_SA"
done

grant_secret_access DATABASE_URL "$RELEASE_SA"

echo "Building ${IMAGE}..."
gcloud builds submit --project "$PROJECT_ID" --tag "$IMAGE" .

echo "Deploying and executing the database release job..."
gcloud run jobs deploy "$RELEASE_JOB" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --image "$IMAGE" \
  --service-account "$RELEASE_SA" \
  --command sh \
  --args scripts/run_release_tasks.sh \
  --env-vars-file deploy/release.env.yaml \
  --set-secrets "DATABASE_URL=DATABASE_URL:${SECRET_VERSION}" \
  --tasks 1 \
  --parallelism 1 \
  --max-retries 0 \
  --task-timeout 15m
gcloud run jobs execute "$RELEASE_JOB" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --wait

echo "Deploying the FastAPI web service..."
gcloud run deploy "$WEB_SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --image "$IMAGE" \
  --service-account "$WEB_SA" \
  --env-vars-file deploy/web.env.yaml \
  --set-secrets "DATABASE_URL=DATABASE_URL:${SECRET_VERSION},REDIS_URL=REDIS_URL:${SECRET_VERSION},JWT_SECRET=JWT_SECRET:${SECRET_VERSION},FIREBASE_SERVICE_ACCOUNT_JSON=FIREBASE_SERVICE_ACCOUNT_JSON:${SECRET_VERSION}" \
  --cpu 1 \
  --memory 1Gi \
  --concurrency 20 \
  --min-instances 0 \
  --max-instances 3 \
  --timeout 300 \
  --allow-unauthenticated

echo "Deploying the continuous ARQ worker pool..."
gcloud run worker-pools deploy "$WORKER_POOL" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --image "$IMAGE" \
  --service-account "$WORKER_SA" \
  --command python \
  --args worker.py \
  --env-vars-file deploy/worker.env.yaml \
  --set-secrets "DATABASE_URL=DATABASE_URL:${SECRET_VERSION},REDIS_URL=REDIS_URL:${SECRET_VERSION},LLM_API_KEY=LLM_API_KEY:${SECRET_VERSION},RESEND_API_KEY=RESEND_API_KEY:${SECRET_VERSION},RESEND_FROM_EMAIL=RESEND_FROM_EMAIL:${SECRET_VERSION}" \
  --instances 1 \
  --cpu 1 \
  --memory 2Gi

WEB_URL="$(gcloud run services describe "$WEB_SERVICE" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --format 'value(status.url)')"

echo
echo "Deployment complete."
echo "Web URL: ${WEB_URL}"
echo "Health check: ${WEB_URL}/healthz"
echo "Add the web hostname to Firebase Authentication authorized domains."
