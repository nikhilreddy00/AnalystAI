import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.config import settings
from api.rate_limit import limiter
from memory.session_store import SessionStore
from memory.vector_store import VectorStore

# Surface application INFO logs (intent classification, status changes, etc.).
# Without this the root logger defaults to WARNING and our logger.info() calls
# in the orchestrator are silently dropped.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ── Shared singletons (attached to app.state) ──────────────────────────────────
session_store: SessionStore | None = None
vector_store: VectorStore | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global session_store, vector_store

    # Fail fast: with these unset every token is rejected / secrets can't be
    # stored safely, and the failure would otherwise only show up as 401s.
    missing = [n for n in ("supabase_url", "encryption_key") if not getattr(settings, n)]
    if missing:
        raise RuntimeError(f"Required settings not set: {', '.join(n.upper() for n in missing)}")

    # Bootstrap on-disk artifact dir for per-session parquet cache.
    from pathlib import Path
    Path(settings.session_data_dir).mkdir(parents=True, exist_ok=True)

    # Session store — fall back to in-memory if PostgreSQL is unavailable
    try:
        session_store = SessionStore(settings.database_url)
        await session_store.init()
        app.state.session_store = session_store
    except Exception as exc:
        # Audit B4: in production an in-memory store loses every session on restart, silently.
        if settings.app_env == "production":
            raise RuntimeError(f"PostgreSQL unavailable in production: {exc}") from exc
        import warnings
        from memory.session_store import InMemorySessionStore
        warnings.warn(f"PostgreSQL unavailable ({exc}). Using in-memory session store "
                      "(development only — sessions are lost on restart).")
        app.state.session_store = InMemorySessionStore()

    # Vector store — gracefully degrade if Qdrant/Ollama is unavailable
    try:
        vector_store = VectorStore(
            qdrant_host=settings.qdrant_host,
            qdrant_port=settings.qdrant_port,
            ollama_host=settings.ollama_host,
        )
        await vector_store.init()
        app.state.vector_store = vector_store
    except Exception as exc:
        import warnings
        warnings.warn(f"Qdrant/Ollama unavailable ({exc}). Institutional memory disabled.")
        app.state.vector_store = None

    yield

    # Cleanup (nothing needed — connections close automatically)


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
from api.routes import chat, dashboard, connectors, sessions  # noqa: E402

app.include_router(chat.router, prefix="/chat", tags=["chat"])
app.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
app.include_router(connectors.router, prefix="/connectors", tags=["connectors"])
app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred. Please try again."},
    )


@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.app_env}
