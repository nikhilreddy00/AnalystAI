"""A local session for the live stack (scripts/live_check/env.sh's secret), valid 24 h.

    uv run python scripts/live_check/session_token.py            # prints the localStorage JS to paste
    uv run python scripts/live_check/session_token.py --bearer   # prints only the access token
In Playwright: run the printed line in the page (browser_evaluate), then open /chat."""
import json
import sys
import time

import jwt

SECRET = "live-check-secret-not-a-real-one-0123456789"
ISS = "http://127.0.0.1:54321/auth/v1"


def token(hours: int = 24) -> str:
    return jwt.encode({"sub": "live-check-user", "aud": "authenticated", "role": "authenticated",
                       "email": "live@example.com", "iss": ISS,
                       "exp": int(time.time()) + hours * 3600}, SECRET, algorithm="HS256")


if __name__ == "__main__":
    tok = token()
    if "--bearer" in sys.argv:
        print(tok)
    else:
        session = {"access_token": tok, "refresh_token": "r", "expires_at": int(time.time()) + 86400,
                   "expires_in": 86400, "token_type": "bearer",
                   "user": {"id": "live-check-user", "email": "live@example.com",
                            "aud": "authenticated", "role": "authenticated"}}
        print(f"() => localStorage.setItem('sb-127-auth-token', JSON.stringify({json.dumps(session)}))")
