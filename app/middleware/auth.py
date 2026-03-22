"""Session authentication middleware.

Reads the sd_session httpOnly cookie, verifies the JWT, and sets
request.state.user_id and request.state.user_email on success.
Unauthenticated browser requests are redirected to /login.
Unauthenticated API requests receive a 401 JSON response.
"""

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from app.services.auth import verify_session_token

logger = structlog.get_logger()

COOKIE_NAME = "sd_session"

# Paths that never require authentication
EXCLUDED_PATHS = {
    ("GET", "/login"),
    ("POST", "/auth/login"),
    ("POST", "/auth/register"),
    ("POST", "/auth/logout"),
    ("GET", "/api/v1/sources"),
}

# Prefixes that are always excluded (static files, docs)
EXCLUDED_PREFIXES = (
    "/docs",
    "/openapi.json",
    "/redoc",
    "/static",
)


class SessionAuthMiddleware(BaseHTTPMiddleware):
    """Validates JWT session cookie on every request (except excluded paths)."""

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

        # Read session cookie
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return self._unauthenticated(request)

        # Verify JWT
        payload = verify_session_token(token)
        if payload is None:
            return self._unauthenticated(request)

        # Set state for downstream handlers
        request.state.user_id = payload["sub"]
        request.state.user_email = payload["email"]

        return await call_next(request)

    @staticmethod
    def _unauthenticated(request: Request) -> Response:
        """Return 401 JSON for API calls, redirect to /login for browser requests."""
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"},
            )
        return RedirectResponse(url="/login", status_code=303)
