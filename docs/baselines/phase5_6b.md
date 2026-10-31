# Phase 5.6b review: the analysis workspace

2026-10-31, branch `phase-5-6b-workspace` (from main after the 5.6a merge). Design: `docs/adr/0007-workspace.md`.

- **E2E gate: `evals/reports/e2e-20261031T044417Z.*`.** 20 cases, k=1, on the final run path:
  - seal, usage and provenance events;
  - `create()` instead of `save()`;
  - `memory_key`.
  - Compared with `e2e-20261030T203729Z`: 0 broke, 0 fixed, pass rate unchanged. No case failed, so there was no k=3 re-run.
- **Spend: ≈ $0.82** of the $2 cap (estimate $0.80). That is the E2E gate only. Every other check ran offline on the recorded e2e-01 run, at $0.
- **Other suites not re-run.** No prompt, agent logic or analysis input changed. The prompt-snapshot test passes, and `InsightReport.memory_used` defaults to `[]` (E2E has no vector store).

## "Done when" (ROADMAP Phase 5.6, part B)

| criterion | evidence | result |
|---|---|---|
| Reopening any thread renders the stored report byte-identical to the delivered one (hash check) | API: `tests/test_workspace_reports.py::test_reopening_returns_exactly_what_was_delivered`. UI: `frontend/e2e/workspace.spec.ts`, "a reopened thread renders the report exactly as it was delivered" | **API:** the thread's report is the canonical-JSON-identical list of report events the live SSE stream sent, and its sha256 equals the seal the run store wrote at the terminal transition. Mutation checked: no seal, or a usage event leaking into the report, fails it. **UI:** the reopened DOM equals the live DOM on all four tabs (React ids and chart geometry stripped), stable 5/5. Mutation checked: dropping one event type fails it. |
| Deleting a thread or dataset leaves 0 rows, files and vector points (a test enumerates every store) | `tests/test_deletion.py` (memory + Postgres) | A real delivered run fills every store first; each is asserted > 0 before deletion and **0 after**. The stores counted are `analysis_sessions`, `dashboard_charts`, `datasets`, `dataset_versions`, `runs`, `run_events`, checkpoints / blobs / writes, `session_data/<sid>`, upload files and memory points. Also tested: a crash after each of the 4 purge steps, memory store down, orphans of a race, retention, and purge waiting for a running run. A registry test fails on any Postgres table with an id column outside `PURGE_TABLES`. Mutation checked: each purge step, the active-run wait, the run grace, the registry and the chart delete. |
| The sidebar lists 200 threads with p95 < 300 ms and works at 390 px | `tests/test_workspace_perf.py` (Postgres); Playwright at 390 px | **API p95 5.4 ms** (200 threads with ~1 MB sessions, 50 calls; listing never reads a session's JSON). The 201-row drawer renders in about 150 ms in a dev build (asserted < 1 s). No sideways scroll at 390 px in the drawer or the Datasets view. |
| E2E stays at 100% after one paid gate | `e2e-20261031T044417Z` | **100% (20/20), 0 hard violations, 0 errors, $0.041 per question, p95 88 s** |

Every cell of the plan's state × event tables (§5: thread, dataset, snapshot, sidebar) has a named test, in:
- `tests/test_workspace_{store,api,reports,hardening,perf}.py`
- `tests/test_deletion.py`
- `tests/test_durable_runs.py` (the kill test now also asserts one seal and the same cost at all 11 checkpoints)
- `frontend/e2e/workspace.spec.ts` (22 Playwright tests, with `reconnect.spec.ts`)

Final counts: pytest **1001 passed** with Postgres, Playwright **22 passed**, ruff, tsc and eslint clean.

## Built
- **Datasets** (`datasets`, `dataset_versions`):
  - Uploads become versions. Same bytes → that version, with no new file. Same name and same columns → a new version. Otherwise a new dataset.
  - "Upload new version" and "Keep as a separate dataset" (undo) on the dataset page.
  - Live sources are one dataset per connection (HMAC key, encrypted config), with no versions.
  - `POST /chat` takes `dataset_version_id` / `dataset_id`. The client never sees a file path.
  - One dataset per thread.
- **Threads:**
  - Only `POST /chat` creates a thread. A save is update-only, so a deleted thread can never come back.
  - Titles are made by code (dataset name + first question), and questions are kept for search.
  - Rename, pin, archive.
  - Delete is a tombstone: the thread is hidden at once, its run is cancelled and its memory point dropped.
- **Reports never change:**
  - A report is its run's own report events (`run_store.report_events`), cut exactly as the UI's reducer cuts them.
  - The report is sealed (`runs.report_sha256`) at every terminal transition.
  - Provenance: a `provenance` event (models and card versions) and `usage` events (agent, prompt version, model, tokens, cost from a per-run meter).
  - `/api/sessions/{id}/thread`, per-report PPTX, thread Markdown export (escaped, with evidence).
  - A stale banner with "Re-run on current data".
- **Memory:** keyed by dataset id, excludes the thread itself, and the report says which earlier analyses it consulted.
- **Deletion and retention** (`memory/retention.py`, the worker's sweep):
  - purge in a safe order, with the session row last;
  - dataset cascade;
  - orphan sweep;
  - `RETENTION_DAYS` (0 = forever).
- **UI:**
  - Persistent collapsible sidebar on desktop, drawer on phones.
  - Groups, search, status dot, row menu (keyboard), confirm dialogs.
  - The thread lives in the URL; a reopened thread can be continued.
  - Datasets view.
- Pin requires owning the thread.

## Bug ledger: where each bug was found
The user asked for the count to fall at each stage and for nothing to be found after "done". Every product bug below now has a test that fails without its fix (mutation or stash checked).

| stage | found | what |
|---|---|---|
| Plan red-team (before any code) | **17 defects in plan v1** | The worst: a delete could be undone by a late write, the snapshot rule missed cancelled reports, and an export URL would 404. All were fixed in plan v2. |
| Build, before review (self, existing tests, mutation runs, automated security review) | **15** | Including a deleted dataset's file reachable through a raw path (security review); the E2E eval seeding with `save()`, which would have failed every paid case; usage written after `done`; provenance lost at kill 1; the orphan sweep reading parents before children; the header 1 px too wide at 390 px; and Enter in the rename input opening the thread. |
| Independent review #1 (backend, code only) | **14** (3 major) | Chat racing a dataset delete, a shared legacy file deleted, the sweep decrypting every checkpoint, non-UTC servers, dedup across datasets, retention clock, report segmentation. |
| Independent review #2 (whole diff; the first review of the frontend) | **17** (3 blockers, 4 major): 15 frontend, 2 backend | **Frontend:** stream / POST epochs, a stale data source on follow-ups, failed turns shown as "Done", the sidebar refresh, accessibility. **Backend:** report segmentation (retry / resume), the legacy-path check. |
| Confirmation review (fresh) | **5** (1 medium, 4 low): 2 of the 31 fixes incomplete, 3 new | B2 for a brand-new thread; R4's sibling route (`/dashboard/latest/report.pptx`); a sidebar blank after a failed patch; the upload sweep's cost; a stale recalled run. |

**Per area the count fell:**
- backend: 14 → 2 → 1;
- frontend: 15 (its first review) → 4.

Across stages it did not fall monotonically (14, then 17), because review #2 was the first look at the frontend. **Lesson for next time:** have the frontend reviewed when its work package ends (WP5), as the backend was after WP3, not only in the whole-diff review.

Two regressions came from fixes, and existing tests caught both before commit:
- the first R13 segmentation emptied the report on a kill after the last checkpoint;
- the m3 "back to /chat" fix ran on every render and cleared a new thread.

## Deviations from the plan
- **No `report_snapshots` table.** The red team showed that `run_events` already is the immutable, fenced record. A snapshot is the run's report events plus a seal on the run row: one less table to purge, and pre-5.6b runs are covered.
- **The `export` event and `RunEnv` are unchanged.** The export run has only a `targeted_response`. The per-report PPTX is a button.
- **No startup backfill of legacy sessions into datasets.** Postgres never held them in this environment (5.6a: `greenlet` was missing, so sessions fell back to memory). Legacy threads still list, reopen and delete; they have no dataset.
- **A 404, not a 202, for a second delete of the same thread.** The UI treats a 404 on delete as "gone".
- The UI timing for 200 threads is asserted at < 1 s in a dev build. The p95 < 300 ms criterion is met and asserted on the list API.
- The mock's `/thread` mirrors the server's gate rule rather than importing it (the mock runs on the system Python, without the app's dependencies). The rule itself is tested in Python.

## Carry-overs
- Still open from 5.6a:
  - E2B receives the PII columns (Phase 5.5 PHI work);
  - drill-down reads uncleaned data;
  - a clarification answer skips the high-null gate;
  - `/runs` events are polled (LISTEN/NOTIFY later).
- A retry continues the same run, so a failed run's report can change when it is retried. Every new question is a new run beside it.
- Deletion completes where a worker runs: one sweep (30 s) after the run stops, plus the lease (120 s).
- Phase 7 candidates (unchanged): team workspaces, share links, scheduled re-runs.
