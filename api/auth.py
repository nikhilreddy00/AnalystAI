"""Supabase JWT authentication.

Every request is verified locally against the project's signing key — no
network round-trip per request, no trusted-by-default path. There is
deliberately NO development bypass: a missing or invalid token is a 401 in
every environment. Tests sign their own HS256 tokens with the configured
secret rather than skipping verification.
"""

import asyncio
import logging
import threading
import time
from functools import lru_cache
from typing import Any, Dict

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.config import settings

logger = logging.getLogger(__name__)

# Reusable security scheme
security_scheme = HTTPBearer(auto_error=False)

_ASYMMETRIC_ALGS = ["ES256", "RS256"]


class UserProfile:
    """Represents the authenticated user profile."""
    def __init__(self, uid: str, email: str | None = None, display_name: str | None = None):
        self.uid = uid
        self.email = email
        self.display_name = display_name

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uid": self.uid,
            "email": self.email,
            "display_name": self.display_name,
        }


@lru_cache(maxsize=1)
def _jwk_client() -> jwt.PyJWKClient:
    """One client per process. The JWK set is cached for 5 minutes (PyJWT's
    default lifespan), so a key revoked in Supabase stops verifying within
    that window. No `cache_keys` — that would pin a resolved kid forever."""
    return jwt.PyJWKClient(f"{settings.supabase_url}/auth/v1/.well-known/jwks.json")


# An unknown kid makes PyJWKClient refetch the JWKS. Tokens are attacker
# input, so cap forced refetches at one per interval across the process.
_FORCED_REFRESH_INTERVAL = 60.0
_last_forced_refresh = 0.0
_refresh_lock = threading.Lock()


def _signing_key(token: str) -> Any:
    kid = jwt.get_unverified_header(token).get("kid")
    client = _jwk_client()
    for k in client.get_signing_keys():  # served from the 5-minute cache
        if k.key_id == kid:
            return k.key

    global _last_forced_refresh
    with _refresh_lock:
        now = time.monotonic()
        if now - _last_forced_refresh < _FORCED_REFRESH_INTERVAL:
            raise jwt.InvalidTokenError(f"Unknown signing key id: {kid!r}")
        _last_forced_refresh = now
    return client.get_signing_key(kid).key


def _decode_token(token: str) -> Dict[str, Any]:
    """Verify and decode a Supabase access token. Blocking — call via to_thread.

    Routes on the token's own `alg` header instead of try/except-ing between
    algorithms, so an expired asymmetric token reports "expired" rather than
    falling through to a confusing HS256 signature error.
    """
    issuer = f"{settings.supabase_url}/auth/v1"
    alg = jwt.get_unverified_header(token).get("alg")

    # Reject anything outside the allowlist before touching a key — in
    # particular `none`, which must never reach the verification path.
    if alg not in _ASYMMETRIC_ALGS and alg != "HS256":
        raise jwt.InvalidAlgorithmError(f"Unsupported token algorithm: {alg!r}")

    if alg == "HS256":
        # Legacy Supabase projects (and the test suite) sign symmetrically.
        if not settings.supabase_jwt_secret:
            raise jwt.InvalidTokenError("HS256 token received but SUPABASE_JWT_SECRET is not set")
        key: Any = settings.supabase_jwt_secret
        algorithms = ["HS256"]
    else:
        key = _signing_key(token)
        algorithms = _ASYMMETRIC_ALGS

    return jwt.decode(
        token,
        key,
        algorithms=algorithms,
        audience="authenticated",
        issuer=issuer,
    )


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
) -> UserProfile:
    """FastAPI dependency: resolve the caller from a verified Supabase JWT."""
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authentication credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = await asyncio.to_thread(_decode_token, credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as exc:
        logger.warning("Token verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    uid = claims.get("sub")
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject (sub) claim.",
        )

    metadata = claims.get("user_metadata") or {}
    # Stashed for the rate limiter's key function (see api/rate_limit.py), which
    # only receives the Request.
    request.state.user_id = uid

    return UserProfile(
        uid=uid,
        email=claims.get("email"),
        display_name=metadata.get("full_name") or metadata.get("name"),
    )
