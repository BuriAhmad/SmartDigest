"""Rate limiting configuration using slowapi.

Applies per-key rate limits on the trigger endpoint.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request


def _get_key_identifier(request: Request) -> str:
    """Rate limit by API key prefix (falls back to IP if no key)."""
    key_prefix = getattr(request.state, "key_prefix", None)
    if key_prefix:
        return f"key:{key_prefix}"
    return get_remote_address(request)


# Shared limiter instance — imported in main.py and subscription routes
limiter = Limiter(key_func=_get_key_identifier)
