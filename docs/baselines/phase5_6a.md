# Phase 5.6a review: durable runs

2026-10-30, branch `phase-5-6a-durable-runs` (from main after the Phase 5 Ingestion merge).
Design: `docs/adr/0006-durable-runs.md`.

- **E2E gate: `evals/reports/e2e-20261030T203729Z.*`**: 20 cases, k=1, through the new run path
  (`evals/run.py` → `orchestrator.runner.execute`, the worker's code). 17 cases stopped at the
  high-null gate as a persisted interrupt and resumed with the answer. Compared with
  `e2e-20261030T184649Z`: 0 broke, 0 fixed. No case failed, so no k=3 re-run.
- **Spend: ≈ $0.90** of the $3 cap: the fixture recording $0.13 (one $0.024 attempt failed at the
  first checkpoint without a local `ENCRYPTION_KEY`; that bug is fixed, see below) and the E2E gate
  $0.77 (estimate $0.80).

## "Done when" (ROADMAP Phase 5.6, part A)

| criterion | evidence | result |
|---|---|---|
| **Kill test** at every node boundary of the overview graph, $0 | `tests/test_durable_runs.py`: a real e2e-01 overview (recorded: 7 model replies, 1 sandbox run). The run is killed right after each of its 10 checkpoints (input, ingestion, intake, cleaning, the gate's interrupt, the resumed cleaning, analysis, visualization, insight). A new worker (new graph, run env and PII guard) reaps the run and resumes it | **10/10 resumed; report, findings, charts, review, audit, contract and plan identical to the uninterrupted run; same event list; no model call repeated or skipped; 0 PII values in a post-resume request; 0 runs lost** |
| … on the real stack | the same loop on Postgres (`AsyncPostgresSaver`, encrypted checkpoints, a new pool after each kill); runs in CI (`TEST_PG_URL`) | **10/10** |
| **Reconnect test** (Playwright, mocked backend) | `frontend/e2e/reconnect.spec.ts` (CI job): a stream cut after 8 events, and a tab closed mid-run then reopened | **each seq served once, in order; one question, one report in the DOM.** Mutation: without `Last-Event-ID`, the test fails (8 events sent twice) |
| An approval answered after a worker restart continues | `test_an_approval_answered_after_a_restart_continues`: everything in-process is dropped at the gate, then the answer is given | **completed, high-null answer applied** (plus the API test: answer via `POST /chat`, same run id, stream continues) |
| Production without Postgres refuses to boot | `tests/test_boot.py` | **API (production) and worker (any env) refuse; dev falls back with a warning; `/health` says `persistent: false`** |
| E2E stays 100% after one paid gate | `e2e-20261030T203729Z` | **100% (20/20), 0 hard violations, 0 errors, $0.0386/question, p95 85 s** |

Also: queue contract on both stores (`tests/test_run_store.py`, 7 tests × memory + Postgres):
double submit → one run, 5 concurrent claimers never share a run, a stale worker is fenced,
the reaper fails a run that keeps dying with a reason, events replay after any seq, cancel /
resume / retry, shutdown requeue. API tests (`tests/test_runs_api.py`): replay from any event,
409s, privacy (404 and 403 for another user). Full suite: 894 passed with Postgres.

## Built
- `memory/run_store.py` (`runs`, `run_events`; Postgres + in-memory), `orchestrator/runner.py`
  (execute / resume / retry, frame reload, PII seeding, worker loop, encrypted serde),
  `worker/__main__.py`, `api/routes/runs.py` (status, SSE replay, cancel, retry). `POST /chat`
  now stores a run and returns `{run_id, session_id, status}`.
- `orchestrator/graph.py`: the state holds no queue, stores or DataFrame (`RunEnv`, `frame_ref`).
  Gates are `interrupt()`s; `_durable` flushes events before each checkpoint. Intake's high-null
  shortcut is deleted (the resume goes straight to cleaning).
- Frontend: `useAnalysis` attaches to `/runs/{id}/events`, reconnects with `Last-Event-ID`, skips
  seen seqs, and reattaches after a reload (localStorage). It adds Stop, "Retry from where it
  stopped", and a "Not saved" header chip.
- Compose `worker` service; the `session_data` volume is shared, because a resumed run reloads
  the cleaned frame from it.

## Bugs found on the way (each now has a test)
1. **`greenlet` was not installed**, so SQLAlchemy's async engine could never connect. The Postgres
   session store has silently fallen back to memory in this environment all along: B4 in practice.
   Now an explicit dependency, and the worker's boot refusal shows the real reason.
2. LangGraph's default `durability="async"` starts the next node before the checkpoint is saved;
   a crash then re-runs it and repeats its model calls (the kill test fails at kill 6). Now `"sync"`.
3. LangGraph's pydantic serializer dumps in python mode: enums and Timestamps came back blocked
   or altered. The session is now checkpointed as its own JSON.
4. Clearing the gate answer at claim time would lose it to a crash before LangGraph saved it. The
   answer now stays on the run until the run ends, and is applied only while the gate waits.
5. The high-null gate left the *cleaned* frame in place, so the answer would have re-cleaned
   cleaned data. The gate now keeps the raw frame.
6. Security review: a failed session load fell through to a new session, and run lookups weren't
   scoped to the user. Now a load failure is a 503, lookups are filtered by user, and a test covers it.

## Deviations from the plan
- **Thread id = run id**, not session id (ADR-0006: one pending gate or failure point per run).
- Heartbeat 5 s (plan: 15 s) so Stop takes effect within seconds; lease stays 120 s.
- In-memory checkpoints are not encrypted (they are not at rest). Postgres ones are.
- The kill is simulated in-process (a `BaseException` right after a checkpoint, then a new graph,
  pool and env). No subprocess SIGKILL test.
- The mock-backed UI kit (`scripts/ui_check`) now speaks the run API. The old `slow`, `approval`
  and `error` scenarios still play, but the mock has no `approval_answered` (the real flow is
  covered by `tests/test_runs_api.py`).

## Carry-overs
- **E2B receives full data, PII columns included** (unchanged here; analysis inputs are not changed
  silently). Plan: in Phase 5.5's PHI-masking work, upload only the plan's columns plus non-PII
  columns. That changes analysis inputs, so it needs an analysis eval re-run and has its own budget.
- Drill-down still reads uncleaned data and is not on the agent contract.
- A clarification answer skips the high-null gate (`route_after_cleaning` reads `is_approval`, set
  for any gate). This is the old behaviour, kept so E2E didn't move. It should key on the gate.
- A kill *inside* a node re-runs that node, which re-emits its progress events once (the UI handles a
  repeated `agent_start`/`insight`).
- `/runs/{id}/events` polls every 0.5 s; LISTEN/NOTIFY if idle streams ever matter.
- 5.6b: the sidebar shows run status; deleting a thread must delete its runs, events and
  checkpoints (by run id).
