"""The API's own ASGI layers, side-effect free to import: api.main installs them (and the process's
logging), and the route tests install the same ones without it (5.8c: importing api.main for its
layers attached the log scrubber to pytest's capture handler, which then dropped exc_info from
every later test's records)."""
from __future__ import annotations

import logging

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from connectors.base import _SCOPE as _credential_scope, credential_scope
from connectors.csv_connector import MAX_UPLOAD_MB

UNEXPECTED = {"detail": "An unexpected error occurred. Please try again."}
MAX_BODY = (MAX_UPLOAD_MB + 1) * 2**20   # the largest file plus its multipart framing
_TOO_LARGE = f"File too large (max {MAX_UPLOAD_MB} MB)"


class CredentialScope:
    """Each request its own credential registry (connectors.base): what one user's connector
    registers never masks another tenant's log lines. Pure ASGI: the route and every task or
    thread it starts run in this context. An error escaping the route is logged and answered
    here, inside the scope: Starlette's 500 handler and uvicorn's traceback run outside it, a
    credential the request registered in plain text (audit pass 2, proven)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        token, started = credential_scope(), False

        async def sending(message):
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)
        try:
            await self.app(scope, receive, sending)
        except Exception:
            logging.getLogger("api.main").error("unhandled error: %s %s", scope.get("method"),
                                                scope.get("path"), exc_info=True)
            if not started:
                await JSONResponse(UNEXPECTED, status_code=500)(scope, receive, send)
        finally:
            _credential_scope.reset(token)


class BodyLimit:
    """No request body past MAX_BODY reaches the app (5.8c red-team): Starlette spools a whole
    multipart body to disk before a route runs, so a route's own cap fired only after a 10 GB body
    was stored. Refused by its declared length unread, or counted as it streams (no length)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        declared = dict(scope["headers"]).get(b"content-length", b"0")
        if declared.isdigit() and int(declared) > MAX_BODY:
            return await JSONResponse({"detail": _TOO_LARGE}, status_code=413)(scope, receive, send)
        seen = 0

        async def counted():
            nonlocal seen
            message = await receive()
            seen += len(message.get("body", b""))
            if seen > MAX_BODY:   # FastAPI re-raises an HTTPException from its body parse
                raise HTTPException(status_code=413, detail=_TOO_LARGE)
            return message
        await self.app(scope, counted, send)
