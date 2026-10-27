"""Shared rate limiter singleton — imported by both api.main and route modules
to avoid circular imports while still pointing at the same Limiter instance."""
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request


def _user_or_ip(request: Request) -> str:
    """Rate-limit per authenticated user, falling back to IP.

    `get_current_user` stashes the verified uid on request.state; because
    FastAPI resolves dependencies before the endpoint (which slowapi wraps),
    it is already set by the time this runs on authenticated routes. Anything
    unauthenticated — /health, a rejected token — still gets IP limiting.
    """
    uid = getattr(request.state, "user_id", None)
    return f"user:{uid}" if uid else get_remote_address(request)


limiter = Limiter(key_func=_user_or_ip)

# Per-IP ceiling stacked on top of per-user limits for expensive routes, so one
# client rotating many sign-up accounts can't multiply its quota.
ip_limit = limiter.limit("60/minute", key_func=get_remote_address)
