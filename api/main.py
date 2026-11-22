import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.config import settings
from connectors.base import _SCOPE as _credential_scope, credential_scope
from api.logging_scrub import install as _scrub_logs
from api.rate_limit import limiter

# Surface application INFO logs (intent classification, status changes, etc.).
# Without this the root logger defaults to WARNING and our logger.info() calls
# in the orchestrator are silently dropped.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
_scrub_logs()   # every log line is scrubbed of PII before it is written


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast: with these unset every token is rejected / secrets can't be
    # stored safely, and the failure would otherwise only show up as 401s.
    missing = [n for n in ("supabase_url", "encryption_key") if not getattr(settings, n)]
    if missing:
        raise RuntimeError(f"Required settings not set: {', '.join(n.upper() for n in missing)}")

    # ADR-0003: an invalid semantic layer stops the boot instead of failing every SQL question.
    from semantic_layer import configured_layer, file_layers
    configured_layer()
    file_layers()   # Phase 5.5: the flat-file layers too (semantic_layers/*.yaml)
    from domain_pack import load_packs
    load_packs()    # Phase 5.7: the domain packs (domain_packs/*.yaml), same rule

    # Bootstrap on-disk artifact dir for per-session parquet cache.
    from pathlib import Path
    Path(settings.session_data_dir).mkdir(parents=True, exist_ok=True)

    # Sessions, runs and checkpoints: Postgres, or memory in development (audit B4: production
    # refuses to start without it). The vector store degrades gracefully.
    from orchestrator.runner import open_deps, work
    deps = await open_deps(settings.app_env, settings.database_url)
    app.state.deps = deps
    app.state.session_store = deps.session_store
    app.state.vector_store = deps.vector_store
    app.state.run_store = deps.run_store

    # Development runs the worker in-process (required in memory mode); production runs
    # `python -m worker` as its own service. Two workers on one Postgres queue are safe.
    stop = asyncio.Event()
    embedded = None
    if settings.app_env != "production":
        embedded = asyncio.create_task(work(deps, stop, concurrency=settings.worker_concurrency))

    yield

    stop.set()
    if embedded:
        await embedded
    for close in deps.closers:
        await close()


app = FastAPI(
    title="AnalystAI",
    description="Multi-agent AI system that replaces a data analyst",
    version="0.1.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_UNEXPECTED = {"detail": "An unexpected error occurred. Please try again."}


class _CredentialScope:
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
            logging.getLogger(__name__).error("unhandled error: %s %s", scope.get("method"),
                                              scope.get("path"), exc_info=True)
            if not started:
                await JSONResponse(_UNEXPECTED, status_code=500)(scope, receive, send)
        finally:
            _credential_scope.reset(token)


from connectors.csv_connector import MAX_UPLOAD_MB  # noqa: E402

MAX_BODY = (MAX_UPLOAD_MB + 1) * 2**20   # the largest file plus its multipart framing
_TOO_LARGE = f"File too large (max {MAX_UPLOAD_MB} MB)"


class _BodyLimit:
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


app.add_middleware(_CredentialScope)
app.add_middleware(_BodyLimit)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ─────────────────────────────────────────────────────────────────────
from api.routes import chat, dashboard, connectors, datasets, imports, runs, sessions  # noqa: E402

app.include_router(chat.router, prefix="/chat", tags=["chat"])
app.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
app.include_router(connectors.router, prefix="/connectors", tags=["connectors"])
app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])
app.include_router(datasets.router, prefix="/api/datasets", tags=["datasets"])
app.include_router(runs.router, prefix="/runs", tags=["runs"])
app.include_router(imports.router, prefix="/api/imports", tags=["imports"])


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content=_UNEXPECTED)


@app.get("/health")
async def health():
    # persistent=False: development without Postgres; the UI shows a "not saved" banner.
    deps = getattr(app.state, "deps", None)
    return {"status": "ok", "env": settings.app_env, "persistent": bool(deps and deps.persistent)}
