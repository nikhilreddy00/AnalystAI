# ADR-0007: The analysis workspace (Phase 5.6b)

Status: accepted, 2026-10-31.

## Context
5.6a made runs durable records (ADR-0006). The roadmap's part B asks for a ChatGPT-style
workspace: every thread listed and reopenable, datasets as first-class versioned objects, reports
that never change once delivered, memory that follows the dataset, complete deletion (GDPR Art. 17)
with retention, and a Markdown export.

## Decisions
- **One dataset per thread.** "Change" starts a new thread; "Re-run on current data" moves a thread
  to its own dataset's newest version. A thread spanning datasets would make staleness, memory
  scope and the deletion cascade per-turn bookkeeping, and a miss would orphan personal data.
  Hex, Mode and Deepnote bind an analysis to its data the same way. Multi-dataset threads can come
  later as a deliberate feature; mixed threads could never be split after the fact.
- **Datasets and versions** (`datasets`, `dataset_versions`, in the session store's database).
  An upload is a version, identified by the sha256 of its bytes:
  - same bytes → the existing version, no new file (dedup by content, as in Git);
  - same name **and** same columns → a new version of that dataset (the schema check Tableau and
    Power BI make before swapping a source); otherwise a new dataset. A wrong merge (two unrelated
    `data.csv`) would mix memory across datasets and put a false "data changed" banner on a board
    report; a wrong split costs a click, and "Keep as a separate dataset" undoes an automatic
    version while it has no analyses;
  - "Upload new version" on the dataset page always makes a version (the explicit act, as in Git
    and DVC);
  - live sources (SQL, Sheets, API) are one dataset per connection (an HMAC of the connection,
    config encrypted), with **no versions**: a live database changes all the time and the SQL
    path's fingerprint depends on the question. Their reports record the fingerprint instead.
  - The client names data by `dataset_version_id` / `dataset_id`; it never sends or receives a
    server file path.
- **A report is its run's own report events** (`briefing, evidence, charts, insight,
  expert_review, targeted_response` in `run_events`), never the session, which later turns
  overwrite. There is no snapshot table: `run_events` is already append-only and fenced.
  - The report is segmented exactly as the UI builds it (`run_store.report_events`): a gate's
    answer or a retry starts a new answer, a crash resume doesn't, and the newest answer with a
    report is the report.
  - Every terminal transition (completed, failed, cancelled, reaped) seals `report_sha256` over it
    in the same transaction; a resume clears it. Reopening returns the events byte-identical to
    the stream, and the UI replays them through the live stream's own reducer.
  - **Provenance**: a `provenance` event at a fresh start (models, every agent card version) and a
    `usage` event per node (agent, prompt version, model, tokens, cost) from a per-run meter (its
    own ContextVar, so the eval harness's meter and `--max-usd` are untouched). The run's input
    records its dataset version.
  - A newer version of the dataset marks a report stale; a re-run is a new run beside it.
- **Memory** is keyed by `dataset_id` (it follows re-uploads and versions) and excludes the thread
  itself; the report lists the earlier analyses it consulted (`InsightReport.memory_used`, set by
  code, never by the model).
- **Deletion is a tombstone, then a purge** (`memory/retention.py`).
  - DELETE hides the thread at once, cancels its run and drops its memory point.
  - Only `POST /chat` creates a thread (an unknown id is a 404); a worker's save is a no-op on a
    deleted thread. A thread is created with share locks on its dataset and version, so a
    dataset delete or a version split either sees it or makes it fail.
  - The worker's sweep purges a tombstoned thread once its runs have stopped and a lease has
    passed: memory points, checkpoints (thread id = run id), run events and runs, pinned charts,
    cached files and a pre-5.6b thread's own upload (if no other thread uses it), the row last.
    The row is the tombstone, so a crash or a store that is down at any step is finished by the
    next sweep. A dataset goes once its threads are gone: memory, version files, rows.
  - An orphan sweep (persistent stores only) removes what a race left behind: runs, checkpoints,
    cache folders, memory points without a thread, upload files nothing points at (after an hour).
  - `PURGE_TABLES` lists every table with user data; a test fails on any Postgres table with an id
    column outside it.
  - `RETENTION_DAYS` (0 = forever) tombstones idle threads and unused datasets.
- **Sidebar list** reads columns only (`title`, `questions`, `pinned`, `archived`, `deleted_at`,
  `dataset_id` on `analysis_sessions`), never a session's JSON, plus each thread's latest run
  status: p95 5 ms for 200 threads on Postgres.

## Consequences
- Deletion completes where a worker runs: within a sweep (30 s) after the run stops, plus a lease
  (120 s) as the margin for a stalled worker.
- A retry continues the same run, so a failed run's report can change when it is retried; any new
  question is a new run.
- Pre-5.6b threads (no runs) reopen through the old `/results` path and have no dataset.
- `analysis_sessions` keeps its zone-less timestamp columns, written as UTC.
