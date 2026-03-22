"""Rate limiting configuration using slowapi.

Applies per-user rate limits on the trigger endpoint.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request


def _get_user_identifier(request: Request) -> str:
    """Rate limit by user ID (falls back to IP if no session)."""
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return f"user:{user_id}"
    return get_remote_address(request)


# Shared limiter instance — imported in main.py and subscription routes
limiter = Limiter(key_func=_get_user_identifier)
