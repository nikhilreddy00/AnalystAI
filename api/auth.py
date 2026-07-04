import base64
import json
import logging
from typing import Any, Dict

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.config import settings

logger = logging.getLogger(__name__)

# Reusable security scheme
security_scheme = HTTPBearer(auto_error=False)


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


def decode_jwt_payload_unverified(token: str) -> Dict[str, Any]:
    """Helper to decode JWT payload without verification (useful for mock/dev auth)."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload_b64 = parts[1]
        # Pad base64 string if necessary
        padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
        payload_bytes = base64.urlsafe_b64decode(padded)
        return json.loads(payload_bytes)
    except Exception as e:
        logger.warning(f"Failed to decode unverified JWT payload: {e}")
        return {}


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme)
) -> UserProfile:
    """
    FastAPI dependency to secure routes.
    Extracts Bearer token and validates via Firebase or local development mock.
    """
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authentication credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # 1. Firebase Auth Verification (uses Firebase Admin SDK — the correct API for ID tokens)
    if settings.firebase_project_id:
        try:
            import firebase_admin
            from firebase_admin import auth as firebase_auth, credentials

            if not firebase_admin._apps:
                # Initialize once per process. Uses GOOGLE_APPLICATION_CREDENTIALS
                # env var if present, otherwise falls back to default credentials.
                try:
                    firebase_admin.initialize_app(
                        options={"projectId": settings.firebase_project_id}
                    )
                except Exception:
                    firebase_admin.initialize_app(
                        credentials.ApplicationDefault(),
                        options={"projectId": settings.firebase_project_id},
                    )

            decoded = firebase_auth.verify_id_token(token)
            uid = decoded.get("uid") or decoded.get("sub") or decoded.get("user_id")
            if not uid:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token missing subject (UID) claim.",
                )

            return UserProfile(
                uid=uid,
                email=decoded.get("email"),
                display_name=decoded.get("name"),
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Firebase token verification failed: {exc}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Firebase authentication token.",
            )

    # 2. Local Mock / Developer Fallback Mode
    if settings.app_env == "development":
        # Supports simple strings like "mock_user_123" or standard unverified JWTs
        if token.startswith("mock_"):
            uid = token.replace("mock_", "")
            return UserProfile(uid=uid, email=f"{uid}@example.com", display_name=uid.capitalize())

        # If it looks like a JWT, let's decode and trust it in dev
        payload = decode_jwt_payload_unverified(token)
        if payload:
            uid = payload.get("sub") or payload.get("uid") or payload.get("user_id") or "default"
            email = payload.get("email")
            name = payload.get("name") or payload.get("displayName")
            return UserProfile(uid=uid, email=email, display_name=name)

        # Fallback to "default" if token is set but unrecognized in dev
        return UserProfile(uid="default", email="default@example.com", display_name="Default User")

    # In production, strict Firebase / OAuth verification is mandatory
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Production environment requires active Firebase Auth configuration.",
    )
