# ADR-0006: Durable runs (Phase 5.6a)

Status: accepted, 2026-10-30.

## Context
Audit B3: the whole LangGraph run executed inside the `POST /chat` request, so a refresh, a proxy
timeout or a redeploy lost the run. Audit B4: without Postgres the app kept running in memory.

## Decision
- **A run is a row.** `runs` (status `queued → running → waiting_approval → completed | failed |
  cancelled`) is also the queue: `UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED LIMIT 1)`.
  No Redis or Celery: a self-hosted install keeps one database (the procrastinate / Oban pattern).
  `(user_id, client_request_id)` is unique, so a double submit makes one run.
- **Workers.** `python -m worker` (its own Compose service); development also runs one in the API
  process. Lease 120 s, heartbeat 5 s, both fenced by `attempt`. A worker whose lease expired
  writes nothing more, and cancels itself at its next heartbeat. The reaper re-queues an expired
  run up to 3 times, then fails it with a reason. SIGTERM hands runs back without using an attempt.
- **Checkpoints.** LangGraph `AsyncPostgresSaver`, written with `durability="sync"`: each
  checkpoint is saved before the next node starts. Nothing finished re-runs, and no model call
  repeats. Checkpoints are Fernet-encrypted (`ENCRYPTION_KEY`), because they hold the whole
  session, connector secrets included. The session is serialized with its own `model_dump_json`:
  LangGraph's generic pydantic path drops enums and Timestamps.
- **Thread id = run id**, not session id (the roadmap text said session). A session holds many
  runs. If its thread were shared, a new question would overwrite an older run's pending gate or
  its failure point, and "retry that run" would be ambiguous. Deleting a thread (5.6b) deletes
  the checkpoints of its runs.
- **No DataFrame in state.** The frame lives in a per-run `RunEnv` (a ContextVar, like
  `pii_values`). The state names it by `frame_ref` (`raw:<fingerprint>` or `cleaned:<sha256>`).
  A resume reloads it and checks the hash, and if the data changed the run fails loudly. Every
  (re)start seeds the run's PII guard before any model call.
- **Gates** (clarification, high-null) are `interrupt()`s. The answer stays on the run row until
  the run ends, so a crash before LangGraph saves it can't lose it. `rerun_overview` stays a UI
  choice.
- **Events.** Each node's events reach `run_events (run_id, seq)` before its checkpoint (the
  `_durable` wrapper flushes them). `GET /runs/{id}/events` replays after `Last-Event-ID`. A run
  killed *inside* a node re-runs that node, which re-emits its progress events once.
- **Fail loud (B4).** The production API and the worker (in any environment) refuse to start
  without Postgres. Development falls back to memory, and the header says "Not saved".

## Consequences
- The streaming endpoint polls `run_events` every 0.5 s. LISTEN/NOTIFY is the upgrade if idle
  streams ever matter.
- A kill is simulated in-process: a `BaseException` right after a checkpoint is written, then a
  new graph, pool and run env. A real subprocess SIGKILL test is not written.
