"""API key authentication middleware.

Extracts Bearer token, SHA-256 hashes it, and validates against the DB.
Sets request.state.owner_key_id and request.state.key_prefix on success.
"""

import hashlib
import hmac
from datetime import datetime, timezone

import structlog
from sqlalchemy import select, update
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.database import async_session
from app.models.api_key import ApiKey

logger = structlog.get_logger()

# Paths that do NOT require authentication
EXCLUDED_PATHS = {
    ("POST", "/api/v1/keys"),
    ("GET", "/"),
    ("GET", "/setup"),
    ("GET", "/dashboard/metrics"),
    ("GET", "/api/v1/sources"),
}

# Prefixes that are always excluded (static files, docs, HTML digest pages)
EXCLUDED_PREFIXES = (
    "/docs",
    "/openapi.json",
    "/redoc",
    "/static",
    "/digests/",
)


class ApiKeyAuthMiddleware(BaseHTTPMiddleware):
    """Validates Bearer token on every request (except excluded paths)."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        method = request.method
        path = request.url.path

        # Skip auth for excluded paths
        if (method, path) in EXCLUDED_PATHS:
            return await call_next(request)

        # Skip auth for excluded prefixes
        if path.startswith(EXCLUDED_PREFIXES):
            return await call_next(request)

        # Extract Bearer token
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return JSONResponse(
                status_code=401,
                content={"detail": "Authorization header required"},
            )

        parts = auth_header.split(" ", 1)
        if len(parts) != 2 or parts[0] != "Bearer":
            return JSONResponse(
                status_code=401,
                content={"detail": "Authorization header must be: Bearer <key>"},
            )

        raw_key = parts[1]
        key_prefix = raw_key[:4]

        # Hash the incoming key
        incoming_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        # Look up by prefix first (indexed), then compare full hash — avoids full table scan
        async with async_session() as session:
            result = await session.execute(
                select(ApiKey).where(
                    ApiKey.prefix == key_prefix,
                    ApiKey.revoked_at.is_(None),
                )
            )
            keys = result.scalars().all()

            matched_key = None
            for key in keys:
                if hmac.compare_digest(key.key_hash, incoming_hash):
                    matched_key = key
                    break

            if matched_key is None:
                logger.warning("auth.failed", path=path)
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or revoked API key"},
                )

            # Update last_used_at and api_call_count
            await session.execute(
                update(ApiKey)
                .where(ApiKey.id == matched_key.id)
                .values(
                    last_used_at=datetime.now(timezone.utc),
                    api_call_count=ApiKey.api_call_count + 1,
                )
            )
            await session.commit()

        # Set state for downstream handlers
        request.state.owner_key_id = matched_key.id
        request.state.key_prefix = matched_key.prefix

        return await call_next(request)
