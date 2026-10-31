"""Deletion and retention (Phase 5.6b, ADR-0007): the purge sweep behind every delete.

DELETE only tombstones (the thread or dataset is hidden at once, its run cancelled, its memory
point dropped). The worker runs `sweep` beside the reaper, and it removes everything else:

- a tombstoned thread, once all its runs have stopped and LEASE_S has passed since the last of
  them changed (a stalled worker can't write after the purge), in this order: memory points →
  checkpoints (thread id = run id) → run events and runs → pinned charts, cached files and the
  thread's own upload → the session row last. The row is the tombstone: a crash or a failing
  store (Qdrant down) at any step leaves it, and the next sweep starts over. Every step is
  idempotent;
- a tombstoned dataset, once none of its threads has a row: memory points, then its version files
  and rows;
- orphans: runs, checkpoints, cached files and memory points whose thread has no row (a write
  that raced a purge), and upload files no dataset version or thread points at (after an hour:
  an upload being placed has its file a moment before its row). Only with a persistent store:
  in memory every thread is gone after a restart while Qdrant and the files are not;
- with `retention_days` set, threads idle (and datasets unused) longer than that are tombstoned
  and go the same way.

PURGE_TABLES lists every table that holds per-thread or per-dataset data, and which step empties
it. tests/test_deletion.py checks that no table outside it has such a column.
"""
from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime, timedelta, timezone

from memory.run_store import ACTIVE, LEASE_S, cancel_events

logger = logging.getLogger(__name__)

SWEEP_S = 30
UPLOAD_GRACE_S = 3600   # an upload's file exists a moment before its version row
PURGE_TABLES = {
    "analysis_sessions": "session_store.purge_session (last: the tombstone)",
    "dashboard_charts": "session_store.purge_session",
    "datasets": "session_store.purge_dataset",
    "dataset_versions": "session_store.purge_dataset (+ its files)",
    "runs": "run_store.purge_runs",
    "run_events": "run_store.purge_runs",
    "checkpoints": "checkpointer.adelete_thread(run_id)",
    "checkpoint_blobs": "checkpointer.adelete_thread(run_id)",
    "checkpoint_writes": "checkpointer.adelete_thread(run_id)",
}
# Tables with an id-like column but no user data.
NOT_USER_DATA = {"checkpoint_migrations"}


def _utc(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt and dt.tzinfo is None else dt


async def purge_thread(deps, session_id: str, user_id: str, *, now: datetime,
                       grace_s: float = LEASE_S) -> bool:
    """Remove one tombstoned thread from every store. False: not yet (a run is still going, or
    stopped too recently); the next sweep tries again."""
    runs = await deps.run_store.for_session(session_id, user_id)
    if active := [r for r in runs if r.status in ACTIVE]:
        for r in active:   # DELETE asked already; a run that raced it is stopped here
            await deps.run_store.request_cancel(r.id, cancel_events())
        return False
    if any(r.updated_at and _utc(r.updated_at) > now - timedelta(seconds=grace_s) for r in runs):
        return False
    if deps.vector_store:
        await deps.vector_store.delete_session(session_id)
    for r in runs:
        await deps.graph.checkpointer.adelete_thread(r.id)
    await deps.run_store.purge_runs([r.id for r in runs])
    await deps.session_store.purge_session(session_id)
    return True


async def _orphans(deps) -> None:
    """Children are read before their parents: a thread is created before its runs, a run before
    its checkpoints, cached files and memory point. Anything created during the sweep is then
    missing from the child snapshot, never wrongly missing its parent. Each store is its own
    step: one that is down doesn't stop the others."""
    from connectors import csv_connector
    from memory import session_data
    threads = await _checkpoint_threads(deps)
    dirs = [d for d in session_data._DATA_DIR.iterdir() if d.is_dir()] \
        if session_data._DATA_DIR.is_dir() else []
    # uploads/<user>/<file> only: uploads/evals is the eval harness's own folder (in use while an
    # eval runs, referenced by nothing), and older flat files predate per-user folders.
    root = csv_connector._UPLOADS_DIR
    files = [f for d in (root.iterdir() if root.is_dir() else []) if d.is_dir()
             and d.name != "evals" for f in d.iterdir()
             if f.is_file() and time.time() - f.stat().st_mtime > UPLOAD_GRACE_S]
    points = None
    if deps.vector_store:
        try:
            points = await deps.vector_store.session_ids()
        except Exception:
            logger.error("orphan sweep: memory store unavailable", exc_info=True)
    by_session = await deps.run_store.runs_by_session()
    alive = await deps.session_store.session_ids()
    # Only when there is a file to judge: this reads every pre-5.6b thread's config.
    referenced = {str(p) for p in await deps.session_store.upload_paths()} if files else set()

    for sid, run_ids in by_session.items():
        if sid not in alive:
            for rid in run_ids:
                await deps.graph.checkpointer.adelete_thread(rid)
            await deps.run_store.purge_runs(run_ids)
            logger.warning("purged %d orphan run(s) of thread %s", len(run_ids), sid)
    known_runs = {rid for ids in by_session.values() for rid in ids}
    for thread in threads - known_runs:
        await deps.graph.checkpointer.adelete_thread(thread)
    for d in dirs:
        if d.name not in alive:
            shutil.rmtree(d, ignore_errors=True)
    for f in files:
        if str(f) not in referenced and str(f.resolve()) not in referenced:
            f.unlink(missing_ok=True)
            logger.warning("deleted an upload file nothing points at: %s", f.name)
    for sid in (points or set()) - alive:
        await deps.vector_store.delete_session(sid)


async def _checkpoint_threads(deps) -> set[str]:
    """Every checkpoint thread id (= run id), without loading a checkpoint: one DISTINCT query on
    Postgres (the checkpoints live in the run store's database), the dict's keys in memory."""
    if hasattr(deps.run_store, "_engine"):
        from sqlalchemy import text
        async with deps.run_store._engine.connect() as conn:
            return set((await conn.execute(text(
                "SELECT DISTINCT thread_id FROM checkpoints"))).scalars())
    return set(deps.graph.checkpointer.storage)


async def sweep(deps, *, now: datetime | None = None, grace_s: float = LEASE_S,
                retention_days: int | None = None) -> None:
    """One pass. Each part logs and carries on: one failing store never stops the others."""
    from api.config import settings
    now = now or datetime.now(timezone.utc)
    days = settings.retention_days if retention_days is None else retention_days
    store = deps.session_store
    if days > 0:
        before = now - timedelta(days=days)
        for sid, uid in await store.idle_threads(before):
            await store.tombstone(sid, uid)
        for did, uid in await store.idle_datasets(before):
            await store.tombstone_dataset(did, uid)
    for sid, uid, deleted_at in await store.tombstoned():
        if _utc(deleted_at) > now - timedelta(seconds=grace_s):
            continue   # a request that loaded it just before the delete may still be writing
        try:
            await purge_thread(deps, sid, uid, now=now, grace_s=grace_s)
        except Exception:
            logger.error("purge of thread %s failed; retrying next sweep", sid, exc_info=True)
    for did, _uid in await store.tombstoned_datasets():
        try:
            # A thread created while the dataset was being deleted: deleted with it.
            for sid in await store.tombstone_threads_of(did):
                logger.warning("thread %s raced its dataset's delete; tombstoned", sid)
            if deps.vector_store:
                await deps.vector_store.delete_dataset(did)
            await store.purge_dataset(did)
        except Exception:
            logger.error("purge of dataset %s failed; retrying next sweep", did, exc_info=True)
    if not deps.persistent:
        return   # in memory, "no thread" only means the process restarted
    try:
        await _orphans(deps)
    except Exception:
        logger.error("orphan sweep failed; retrying next sweep", exc_info=True)


async def sweep_logged(deps) -> None:
    """The worker's task: a sweep that fails (the database is down) is logged and tried again
    at the next interval."""
    try:
        await sweep(deps)
    except Exception:
        logger.error("purge sweep failed; retrying next interval", exc_info=True)
