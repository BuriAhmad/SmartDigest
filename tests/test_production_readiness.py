"""Focused checks for production configuration and process wiring."""

import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app
from worker import WorkerSettings, start_worker


DATABASE_URL = "postgresql://smartdigest:password@db.example.com/smartdigest"
REDIS_URL = "rediss://default:password@redis.example.com:6379"
FIREBASE_JSON = json.dumps({
    "project_id": "smartdigest-test",
    "private_key": "test-private-key",
    "client_email": "firebase@example.com",
})


class ProductionSettingsTests(unittest.TestCase):
    def test_web_role_accepts_only_web_runtime_secrets(self):
        settings = Settings(
            ENV="production",
            APP_ROLE="web",
            DATABASE_URL=DATABASE_URL,
            REDIS_URL=REDIS_URL,
            JWT_SECRET="a" * 64,
            FIREBASE_SERVICE_ACCOUNT_JSON=FIREBASE_JSON,
            _env_file=None,
        )

        self.assertEqual(settings.APP_ROLE, "web")

    def test_web_role_rejects_weak_jwt_secret(self):
        with self.assertRaisesRegex(ValidationError, "strong JWT_SECRET"):
            Settings(
                ENV="production",
                APP_ROLE="web",
                DATABASE_URL=DATABASE_URL,
                REDIS_URL=REDIS_URL,
                JWT_SECRET="short",
                FIREBASE_SERVICE_ACCOUNT_JSON=FIREBASE_JSON,
                _env_file=None,
            )

    def test_worker_role_accepts_only_worker_runtime_secrets(self):
        settings = Settings(
            ENV="production",
            APP_ROLE="worker",
            DATABASE_URL=DATABASE_URL,
            REDIS_URL=REDIS_URL,
            LLM_API_KEY="test-llm-key",
            RESEND_API_KEY="test-resend-key",
            RESEND_FROM_EMAIL="SmartDigest <digest@example.com>",
            ARQ_MAX_JOBS=2,
            _env_file=None,
        )

        self.assertEqual(settings.APP_ROLE, "worker")
        self.assertEqual(settings.ARQ_MAX_JOBS, 2)

    def test_arq_worker_uses_explicit_concurrency_limit(self):
        self.assertEqual(WorkerSettings.max_jobs, 2)

    def test_worker_installs_fresh_loop_after_model_preload(self):
        loop = object()
        with patch("worker._preload_enabled_models", new=lambda: "preload"), \
             patch("worker.asyncio.run") as run, \
             patch("worker.asyncio.new_event_loop", return_value=loop), \
             patch("worker.asyncio.set_event_loop") as set_event_loop, \
             patch("worker.run_worker") as run_worker:
            start_worker()

        run.assert_called_once_with("preload")
        set_event_loop.assert_called_once_with(loop)
        run_worker.assert_called_once_with(WorkerSettings)

    def test_release_role_only_requires_database(self):
        settings = Settings(
            ENV="production",
            APP_ROLE="release",
            DATABASE_URL=DATABASE_URL,
            _env_file=None,
        )

        self.assertEqual(settings.APP_ROLE, "release")


class HealthEndpointTests(unittest.TestCase):
    def test_healthz_is_public(self):
        with TestClient(create_app()) as client:
            response = client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "ok")


if __name__ == "__main__":
    unittest.main()
