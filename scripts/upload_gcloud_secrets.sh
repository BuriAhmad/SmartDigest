#!/usr/bin/env bash
set -euo pipefail
umask 077

PROJECT_ID="${1:-}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
FIREBASE_JSON="${FIREBASE_JSON:-$HOME/.config/smartdigest/firebase/firebase-admin-service-account.json}"
JWT_SECRET_FILE="${JWT_SECRET_FILE:-$HOME/.config/smartdigest/jwt-secret.txt}"

if [ -z "$PROJECT_ID" ]; then
  echo "Usage: $0 <gcp-project-id>"
  exit 1
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud is not installed"
  exit 1
fi

if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl is not installed"
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing env file: $ENV_FILE"
  exit 1
fi

if [ ! -s "$FIREBASE_JSON" ]; then
  echo "Missing or empty Firebase JSON file: $FIREBASE_JSON"
  exit 1
fi

get_env_value() {
  local key="$1"
  local line
  line="$(grep -m1 "^${key}=" "$ENV_FILE" || true)"
  if [ -z "$line" ]; then
    return 1
  fi
  printf '%s' "${line#*=}"
}

require_env_value() {
  local key="$1"
  local value
  value="$(get_env_value "$key" || true)"
  if [ -z "$value" ]; then
    echo "Missing or empty $key in $ENV_FILE" >&2
    exit 1
  fi
  printf '%s' "$value"
}

ensure_secret_exists() {
  local name="$1"
  if ! gcloud secrets describe "$name" --project "$PROJECT_ID" >/dev/null 2>&1; then
    gcloud secrets create "$name" \
      --project "$PROJECT_ID" \
      --replication-policy=automatic >/dev/null
  fi
}

add_secret_version_from_value() {
  local name="$1"
  local value="$2"
  ensure_secret_exists "$name"
  printf '%s' "$value" | gcloud secrets versions add "$name" \
    --project "$PROJECT_ID" \
    --data-file=- >/dev/null
  echo "Uploaded $name"
}

add_secret_version_from_file() {
  local name="$1"
  local file_path="$2"
  ensure_secret_exists "$name"
  gcloud secrets versions add "$name" \
    --project "$PROJECT_ID" \
    --data-file="$file_path" >/dev/null
  echo "Uploaded $name"
}

DATABASE_URL_VALUE="$(require_env_value DATABASE_URL)"
REDIS_URL_VALUE="$(require_env_value REDIS_URL)"
RESEND_API_KEY_VALUE="$(require_env_value RESEND_API_KEY)"
RESEND_FROM_EMAIL_VALUE="$(get_env_value RESEND_FROM_EMAIL || true)"
LLM_API_KEY_VALUE="$(get_env_value LLM_API_KEY || true)"

if [ -z "${LLM_API_KEY_VALUE:-}" ]; then
  LLM_API_KEY_VALUE="$(require_env_value GEMINI_API_KEY)"
fi

if [ -z "${RESEND_FROM_EMAIL_VALUE:-}" ]; then
  RESEND_FROM_EMAIL_VALUE="SmartDigest <digest@smartdigest.app>"
fi

if [ ! -s "$JWT_SECRET_FILE" ]; then
  mkdir -p "$(dirname "$JWT_SECRET_FILE")"
  openssl rand -hex 32 > "$JWT_SECRET_FILE"
  echo "Generated JWT secret file at $JWT_SECRET_FILE"
else
  echo "Reusing existing JWT secret file at $JWT_SECRET_FILE"
fi
chmod 600 "$JWT_SECRET_FILE"

gcloud config set project "$PROJECT_ID" >/dev/null
gcloud services enable secretmanager.googleapis.com --project "$PROJECT_ID" >/dev/null

add_secret_version_from_value "DATABASE_URL" "$DATABASE_URL_VALUE"
add_secret_version_from_value "REDIS_URL" "$REDIS_URL_VALUE"
add_secret_version_from_value "LLM_API_KEY" "$LLM_API_KEY_VALUE"
add_secret_version_from_value "RESEND_API_KEY" "$RESEND_API_KEY_VALUE"
add_secret_version_from_value "RESEND_FROM_EMAIL" "$RESEND_FROM_EMAIL_VALUE"
add_secret_version_from_file "JWT_SECRET" "$JWT_SECRET_FILE"
add_secret_version_from_file "FIREBASE_SERVICE_ACCOUNT_JSON" "$FIREBASE_JSON"

cat <<EOF

Done. Secret Manager now has:
  DATABASE_URL
  REDIS_URL
  LLM_API_KEY
  RESEND_API_KEY
  RESEND_FROM_EMAIL
  JWT_SECRET
  FIREBASE_SERVICE_ACCOUNT_JSON

Keep using this same JWT secret for both web and worker deployments.
You still need to set ENV=production on Cloud Run as a normal environment variable.
EOF
