import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.config import settings
from api.rate_limit import limiter

# Surface application INFO logs (intent classification, status changes, etc.).
# Without this the root logger defaults to WARNING and our logger.info() calls
# in the orchestrator are silently dropped.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast: with these unset every token is rejected / secrets can't be
    # stored safely, and the failure would otherwise only show up as 401s.
    missing = [n for n in ("supabase_url", "encryption_key") if not getattr(settings, n)]
    if missing:
        raise RuntimeError(f"Required settings not set: {', '.join(n.upper() for n in missing)}")

    # ADR-0003: an invalid semantic layer stops the boot instead of failing every SQL question.
    from semantic_layer import configured_layer
    configured_layer()

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ─────────────────────────────────────────────────────────────────────
from api.routes import chat, dashboard, connectors, datasets, runs, sessions  # noqa: E402

app.include_router(chat.router, prefix="/chat", tags=["chat"])
app.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
app.include_router(connectors.router, prefix="/connectors", tags=["connectors"])
app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])
app.include_router(datasets.router, prefix="/api/datasets", tags=["datasets"])
app.include_router(runs.router, prefix="/runs", tags=["runs"])


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred. Please try again."},
    )


@app.get("/health")
async def health():
    # persistent=False: development without Postgres; the UI shows a "not saved" banner.
    deps = getattr(app.state, "deps", None)
    return {"status": "ok", "env": settings.app_env, "persistent": bool(deps and deps.persistent)}
