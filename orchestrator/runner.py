"""
Durable runs (Phase 5.6a): executes a stored run on the LangGraph graph, checkpointed after every
node (thread id = run id), and the worker loop that claims runs from the queue.

- A fresh run starts from the session in the session store and the run's input.
- A crashed run (a checkpoint with nodes still to go) resumes at the last finished node.
- A run waiting at a gate resumes with `Command(resume=answer)`.
- "Retry from where it failed" forks from the checkpoint before the node that failed.

The DataFrame is never checkpointed: on resume it is reloaded from `frame_ref` and checked
against the hash the run recorded, so a resumed run never silently analyses different rows.
Each (re)start seeds this run's PII guard, as api/routes/chat.py did when runs lived in the request.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import time
import warnings
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Command

from llm.client import get_client, pii_values, run_meter
from memory.run_store import (CANCELLED, HEARTBEAT_S, InMemoryRunStore, Run, RunStore,
                              cancel_events)
from memory.session_data import read_cleaned_parquet
from orchestrator.graph import RunEnv, build_graph, run_env
from orchestrator.state import AnalysisSession

logger = logging.getLogger(__name__)


class _Fernet:
    """Checkpoints hold the whole session (connector secrets included): encrypted at rest with
    the same ENCRYPTION_KEY as the session store."""

    def encrypt(self, plaintext: bytes) -> tuple[str, bytes]:
        from memory.session_store import _fernet
        return "fernet", _fernet().encrypt(plaintext)

    def decrypt(self, ciphername: str, ciphertext: bytes) -> bytes:
        from memory.session_store import _fernet
        return _fernet().decrypt(ciphertext)


class _StateSerde(JsonPlusSerializer):
    """The session is checkpointed as its own JSON, exactly as the session store saves it
    (LangGraph's generic pydantic path dumps in python mode, and enums or Timestamps inside it
    would come back altered). Everything else in the state is plain data: strict msgpack."""

    def __init__(self) -> None:
        super().__init__(allowed_msgpack_modules=None)

    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        if isinstance(obj, AnalysisSession):
            return "analysis_session", obj.model_dump_json().encode()
        return super().dumps_typed(obj)

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        if data[0] == "analysis_session":
            return AnalysisSession.model_validate_json(data[1])
        return super().loads_typed(data)


PLAIN = _StateSerde()
SERDE = EncryptedSerializer(_Fernet(), PLAIN)   # Postgres; in-memory checkpoints aren't at rest


class LeaseLost(Exception):
    """Another worker owns this run now (our lease expired): stop without touching it."""


class DataChanged(Exception):
    pass


_FINISHED = object()   # the graph already ran to its end: only the status is left to record


@dataclass
class Deps:
    graph: Any
    run_store: Any
    session_store: Any
    vector_store: Any = None
    persistent: bool = False
    closers: list = field(default_factory=list)


def in_memory_deps(**kw) -> Deps:
    from memory.session_store import InMemorySessionStore
    return Deps(graph=build_graph(InMemorySaver(serde=PLAIN)), run_store=InMemoryRunStore(),
                session_store=InMemorySessionStore(), **kw)


async def open_deps(app_env: str, database_url: str, *, vector: bool = True) -> Deps:
    """Postgres for sessions, runs and checkpoints. Audit B4: production refuses to start without
    it; development falls back to memory (the UI shows a "not saved" banner)."""
    try:
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        from memory.session_store import SessionStore
        sessions, runs = SessionStore(database_url), RunStore(database_url)
        await sessions.init()
        await runs.init()
        pool = AsyncConnectionPool(database_url.replace("+asyncpg", "").replace("+psycopg", ""),
                                   open=False, max_size=10,
                                   kwargs={"autocommit": True, "prepare_threshold": 0,
                                           "row_factory": dict_row})
        await pool.open(wait=True, timeout=10)
        saver = AsyncPostgresSaver(pool, serde=SERDE)
        await saver.setup()
        deps = Deps(graph=build_graph(saver), run_store=runs, session_store=sessions,
                    persistent=True, closers=[pool.close])
    except Exception as exc:
        if app_env == "production":
            raise RuntimeError(f"PostgreSQL unavailable in production: {exc}") from exc
        warnings.warn(f"PostgreSQL unavailable ({exc}). Sessions, runs and checkpoints are in "
                      "memory (development only: lost on restart).")
        deps = in_memory_deps()
    if vector:
        try:
            from api.config import settings
            from memory.vector_store import VectorStore
            vs = VectorStore(qdrant_host=settings.qdrant_host, qdrant_port=settings.qdrant_port,
                             ollama_host=settings.ollama_host)
            await vs.init()
            deps.vector_store = vs
        except Exception as exc:
            warnings.warn(f"Qdrant/Ollama unavailable ({exc}). Institutional memory disabled.")
    return deps


def provenance() -> dict:
    """What a run ran under, recorded when it starts (Phase 5.6b): the models and every agent's
    card version. The prompt each model call used is in that call's usage record."""
    from agents.analysis_agent import ANALYSIS_CARD
    from agents.cleaning_agent import CLEANING_CARD
    from agents.ingestion_agent import INGESTION_CARD
    from agents.insight_agent import INSIGHT_CARD
    from agents.intake_agent import INTAKE_CARD
    from agents.quality_agent import QUALITY_CARD
    from agents.sql_agent import SQL_CARD
    from agents.visualization_agent import VIZ_CARD
    from api.config import settings
    return {"type": "provenance", "model": settings.llm_model,
            "fast_model": settings.llm_fast_model,
            "cards": {c.name: c.version for c in (INGESTION_CARD, INTAKE_CARD, SQL_CARD,
                                                  CLEANING_CARD, ANALYSIS_CARD, VIZ_CARD,
                                                  INSIGHT_CARD, QUALITY_CARD)}}


def _config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}}


def _drain(q: asyncio.Queue) -> list[dict]:
    return [q.get_nowait() for _ in range(q.qsize())]


async def _restore_frame(env: RunEnv, session: AnalysisSession, ref: str | None) -> None:
    """Reload the frame a resumed run was using; this also refills the PII guard."""
    from agents.ingestion_agent import remember_pii, run_ingestion_agent
    if not ref or session.data_contract is None:
        return
    kind, _, want = ref.partition(":")
    if kind == "cleaned":
        frame = read_cleaned_parquet(session.session_id)
        if frame is None or hashlib.sha256(frame).hexdigest() != want:
            raise DataChanged("the cleaned data saved for this analysis is gone or was replaced")
        remember_pii(pd.read_parquet(io.BytesIO(frame)), session.data_contract)
    else:
        probe = session.model_copy(deep=True)
        question = session.task_plan.raw_query if session.task_plan else None
        contract = await run_ingestion_agent(probe, get_client(), question,
                                             session.data_contract.source_query)
        if contract.fingerprint != want:
            raise DataChanged("the data source returns different rows than when this run started")
        frame = probe.__dict__.pop("_df_parquet")
    env.frame = frame


async def _retry_point(graph, config: dict) -> dict:
    """The checkpoint just before the node that failed (its error not yet set)."""
    async for snap in graph.aget_state_history(config):
        s = snap.values.get("session")
        if snap.next and snap.next[0] != "error" and s is not None and not s.error_message:
            return snap.config
    return config


async def execute(run: Run, deps: Deps) -> str:
    """Take a claimed run to its next stop (done, failed, or waiting at a gate); returns the
    status it was left in. Events are fenced by the claim's attempt."""
    graph, store = deps.graph, deps.run_store
    config = _config(run.id)
    queue: asyncio.Queue = asyncio.Queue()

    meter: list[dict] = []

    async def flush() -> None:
        # Model usage goes out with the node's events, in the same fenced write: a crash after
        # the node's checkpoint can't lose its cost, and a node that re-runs is counted again.
        events = _drain(queue)
        if meter:   # first: the node's own events end with its `done`
            events.insert(0, {"type": "usage", "calls": meter[:]})
            meter.clear()
        if events and not await store.append(run.id, events, attempt=run.attempt):
            raise LeaseLost(run.id)

    env = RunEnv(events=queue, flush=flush, session_store=deps.session_store,
                 vector_store=deps.vector_store)
    token = run_env.set(env)
    meter_token = run_meter.set(meter)
    try:
        snap = await graph.aget_state(config)
        resume = run.resume or {}
        # The answer stays on the run until it finishes, so a crash before LangGraph has saved
        # it can't lose it; it is applied only while the gate is still waiting.
        # A checkpoint with nothing left to run is a finished graph (the worker died before
        # recording it): record it, never start over. Only a failed one forks for a retry.
        finished = bool(snap.values) and not snap.next
        if "answer" in resume and snap.interrupts:
            inp: Any = Command(resume=resume["answer"])
        elif snap.next:
            inp = None   # a crash: carry on from the last finished node
        elif finished and resume.get("retry") and snap.values["session"].status == "failed":
            config, inp = await _retry_point(graph, config), None
            snap = await graph.aget_state(config)
        elif finished:
            inp = _FINISHED
        else:
            session = await deps.session_store.load(run.session_id)
            if session is None:
                raise RuntimeError("the conversation for this run was not found")
            inp = {"session": session, "user_message": run.input["message"],
                   "is_approval": run.input.get("is_approval", False),
                   "approval_context": run.input.get("approval_context"),
                   "similar_insights": [], "frame_ref": None, "gate": None, "answered": None}
            queue.put_nowait(provenance())
            await flush()   # before the first checkpoint: a kill right after it can't lose this
        if inp is not _FINISHED:
            session = inp["session"] if isinstance(inp, dict) else snap.values["session"]
            # This run's PII guard: ingestion (or the reload below) fills it, and every LLM
            # request is pseudonymized against it. Set in this task: it belongs to this run only.
            pii_values.set(set(session.prior_pii))
            if not isinstance(inp, dict):
                await _restore_frame(env, session, snap.values.get("frame_ref"))
            # "sync": each checkpoint is written before the next node starts, so a crash never
            # re-runs a node that finished (or repeats its model calls).
            await graph.ainvoke(inp, config, durability="sync")
            await flush()
    except LeaseLost:
        logger.warning("run %s: lease lost, another worker has it", run.id)
        return "lost"
    except DataChanged as exc:
        return await _fail(store, run, f"This analysis can't continue: {exc}. Ask the question "
                                       "again to analyse the current data.", retryable=False)
    except Exception as exc:
        logger.error("run %s crashed", run.id, exc_info=exc)
        return await _fail(store, run, f"The analysis stopped unexpectedly: {exc}")
    finally:
        run_meter.reset(meter_token)
        run_env.reset(token)

    state = await graph.aget_state(_config(run.id))
    if state.next:   # stopped at an interrupt(): a gate is waiting for the user
        await store.finish(run.id, "waiting_approval", attempt=run.attempt)
        return "waiting_approval"
    session = state.values["session"]
    status = "failed" if session.status == "failed" else "completed"
    await store.finish(run.id, status, attempt=run.attempt,
                       error=session.error_message if status == "failed" else None)
    return status


async def _fail(store, run: Run, message: str, retryable: bool = True) -> str:
    await store.finish(run.id, "failed", attempt=run.attempt, error=message,
                       events=[{"type": "error", "message": message, "retryable": retryable},
                               {"type": "done"}])
    return "failed"



async def supervise(run: Run, deps: Deps, heartbeat_s: float = HEARTBEAT_S) -> str:
    """Run one claimed run with a heartbeat that keeps its lease and notices a cancel."""
    job = asyncio.create_task(execute(run, deps))
    why = None
    try:
        while not job.done():
            await asyncio.wait({job}, timeout=heartbeat_s)
            if job.done():
                break
            try:
                alive, cancel = await deps.run_store.heartbeat(run.id, run.attempt)
            except Exception:
                # A database blip: keep running. If the lease does expire, fencing stops us.
                logger.warning("run %s: heartbeat failed", run.id, exc_info=True)
                continue
            if not alive or cancel:
                why = "cancel" if alive else "lost"
                job.cancel()
                break
        return await job
    except asyncio.CancelledError:
        if why == "cancel":
            await deps.run_store.finish(run.id, "cancelled", attempt=run.attempt,
                                        error=CANCELLED, events=cancel_events())
            return "cancelled"
        if why == "lost":
            return "lost"
        job.cancel()   # this worker is shutting down: hand the run back
        await deps.run_store.requeue(run.id, run.attempt)
        raise


async def work(deps: Deps, stop: asyncio.Event, *, concurrency: int = 4,
               poll_s: float = 0.5, heartbeat_s: float = HEARTBEAT_S) -> None:
    """The worker loop: reap expired leases, purge deleted threads and datasets (memory/retention.py),
    claim queued runs, run up to `concurrency` at once.
    ponytail: polls the queue every `poll_s`; LISTEN/NOTIFY if idle polling ever matters."""
    from memory.retention import SWEEP_S, sweep_logged
    tasks: set[asyncio.Task] = set()
    last_reap = last_sweep = 0.0
    sweeping: asyncio.Task | None = None
    try:
        while not stop.is_set():
            try:
                if time.monotonic() - last_reap > heartbeat_s:
                    for run_id, status in await deps.run_store.reap():
                        logger.warning("reaped run %s → %s", run_id, status)
                    last_reap = time.monotonic()
                # Deletes and retention, in their own task: a long sweep never holds up claiming
                # runs or their heartbeats.
                if time.monotonic() - last_sweep > SWEEP_S and not (sweeping and not sweeping.done()):
                    last_sweep = time.monotonic()
                    sweeping = asyncio.create_task(sweep_logged(deps))
                if len(tasks) < concurrency and (run := await deps.run_store.claim()):
                    task = asyncio.create_task(supervise(run, deps, heartbeat_s))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
                    continue
            except Exception:
                # A database blip must not end the worker (in the API it would die unseen).
                logger.error("worker: queue unavailable, retrying", exc_info=True)
            try:
                await asyncio.wait_for(stop.wait(), poll_s)
            except TimeoutError:
                continue   # the queue was empty: poll again
    finally:
        for t in [*tasks, *([sweeping] if sweeping else [])]:
            t.cancel()
        await asyncio.gather(*tasks, *([sweeping] if sweeping else []), return_exceptions=True)
