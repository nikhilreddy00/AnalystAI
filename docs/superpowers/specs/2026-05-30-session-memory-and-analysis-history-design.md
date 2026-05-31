# Session Memory & Analysis History — Design

**Status:** Approved for plan-writing
**Date:** 2026-05-30
**Scope:** Gap 6 — Persist completed analyses so users can revisit them without re-running the pipeline, deduplicate re-uploads of the same dataset, and browse past sessions from the chat UI.

---

## 1. Goal & Non-Goals

### Goal
Give users a left-sidebar list of every analysis they have run. Clicking an entry instantly restores the full UI (insight report, charts, conversation) from cache — zero LLM calls. Re-uploading a file that has already been analyzed surfaces a "load previous or run fresh?" prompt. Follow-up questions on a past session reuse the cleaned DataFrame from disk instead of re-running ingestion + cleaning.

### Non-goals (explicitly deferred)
- Garbage collection of orphaned files in `uploads/` when their sessions are deleted.
- Pagination of the session list beyond `limit=50`.
- Validating cached parquet against an evolved `data_contract` on load.
- Alembic / formal migrations — `ALTER TABLE … ADD COLUMN IF NOT EXISTS` is sufficient at current scale.
- Sharing or copying sessions across users.
- Re-using `file_hash` for non-file connectors (SQL / Sheets / API) — they get sessions in the sidebar but no dedup prompt.

---

## 2. Architecture Overview

Storage is split: metadata + full `AnalysisSession` (including `insight_report`, `chart_configs`, `conversation_history`, `analysis_findings.column_roles_summary`) keeps living in the existing `SessionStore` (PG/in-memory) as JSON. Only the cleaned DataFrame goes to the filesystem — it is binary, can be large, and is a poor fit for JSONB.

Filesystem path: `<SESSION_DATA_DIR>/{session_id}/cleaned_data.parquet`, where `SESSION_DATA_DIR` defaults to `./session_data/` (gitignored), overridable via env. This mirrors the existing `uploads/` directory pattern.

**What extends, what is new**

- *Extends:* `AnalysisSession` (4 new fields), `SessionStore` + `InMemorySessionStore` (3 new methods + `delete` widened), `cleaning_node` (writes parquet on success), `ingestion_node` (loads parquet on cached resume; sets status), `insight_node` + `error_node` (set status), `/connectors/upload` (returns `file_hash`, `dataset_name`, optional `existing_session`), `ChatRequest` (accepts `file_hash` + `dataset_name`).
- *New:* `api/routes/sessions.py` (6 endpoints), `memory/session_data.py` (parquet helper), `<SESSION_DATA_DIR>/{id}/` directory layout, frontend `SessionSidebar.tsx` + `useSessions` hook, dedup modal inside `ConnectorSetup`.
- *Unchanged:* graph topology, all agent code, SSE event protocol, dashboard pinning, auth, rate limiter.

**Dedup contract.** Only CSV/Excel uploads compute a hash. `/connectors/upload` returns `{file_hash, dataset_name}` alongside the existing config; if `find_by_hash` matches, the response also carries `existing_session`. The frontend asks the user *before* calling `/chat`. "Load previous" fetches `/api/sessions/{id}/results` and never opens an SSE stream. "Run fresh" creates a new session with `file_hash` + `dataset_name` populated. Multiple sessions may share a `file_hash`.

---

## 3. Schema Changes

### 3.1 `AnalysisSession` (orchestrator/state.py)

Add four fields, all optional/defaulted so existing persisted sessions deserialize cleanly:

```python
dataset_name: str | None = None       # filename stem, e.g. "titanic"
file_hash: str | None = None          # sha256 hex of uploaded bytes; None for SQL/Sheets/API
status: Literal["in_progress", "completed", "failed"] = "in_progress"
cleaned_data_path: str | None = None  # absolute path to cleaned_data.parquet; None until cleaning succeeds
```

Row/column counts and timestamps are not duplicated — they already exist on `data_contract.row_count`, `data_contract.columns`, and `session.created_at` / `updated_at`.

### 3.2 `SessionSummary` (new, orchestrator/state.py)

```python
class SessionSummary(BaseModel):
    session_id: str
    dataset_name: str | None
    original_query: str | None     # first user message from conversation_history
    row_count: int | None          # from data_contract if present
    column_count: int | None
    status: Literal["in_progress", "completed", "failed"]
    created_at: datetime
    updated_at: datetime
```

### 3.3 `SessionRecord` (memory/session_store.py)

Add three columns + index for hash dedup:

```python
dataset_name = Column(String, nullable=True)
file_hash    = Column(String, nullable=True, index=True)
status       = Column(String, nullable=False, default="in_progress")
```

Indexed columns are persisted **alongside** the JSON blob (not extracted from it). Reason: `find_by_hash` must be an indexed lookup; `list_sessions` only needs metadata — both would be O(n) deserialization if we relied solely on the JSON column. `save()` writes both representations in one transaction.

### 3.4 Migration

`init()` keeps using `Base.metadata.create_all`. For dev (SQLite) the user can wipe the local DB. For Postgres, `init()` runs a one-shot idempotent block after `create_all`:

```sql
ALTER TABLE analysis_sessions ADD COLUMN IF NOT EXISTS dataset_name VARCHAR;
ALTER TABLE analysis_sessions ADD COLUMN IF NOT EXISTS file_hash    VARCHAR;
ALTER TABLE analysis_sessions ADD COLUMN IF NOT EXISTS status       VARCHAR NOT NULL DEFAULT 'in_progress';
CREATE INDEX IF NOT EXISTS ix_analysis_sessions_file_hash ON analysis_sessions(file_hash);
```

Dialect-detect: only run the ALTER block when the engine dialect is `postgresql`; SQLite picks up the columns at table-create time.

### 3.5 `/connectors/upload` response (api/routes/connectors.py)

Extend the existing return shape (no breaking change to existing fields):

```jsonc
{
  "status": "uploaded",
  "config": { /* ConnectorConfig */ },
  "file_hash": "abc123…",                 // NEW
  "dataset_name": "titanic",              // NEW
  "existing_session": {                   // NEW; present iff dedup hit
    "session_id": "…",
    "dataset_name": "titanic",
    "updated_at": "2026-05-20T…",
    "original_query": "…"
  }
}
```

Hash is computed streaming during the existing chunked file write — no extra read pass.

---

## 4. Persistence Layer

### 4.1 New methods on both store implementations

```python
async def list_sessions(user_id: str, limit: int = 50) -> list[SessionSummary]
async def find_by_hash(user_id: str, file_hash: str) -> SessionSummary | None
async def get_summary(session_id: str, user_id: str) -> SessionSummary | None
```

`SessionStore` (PG) queries the indexed columns directly and only deserializes the JSON blob to pull `original_query` / `row_count` per summary (single round trip). `InMemorySessionStore` walks its in-memory dict and produces the same shape — the chat path treats both implementations interchangeably.

### 4.2 Existing methods updated

- `save(session)` writes `session.dataset_name`, `session.file_hash`, `session.status` into the indexed columns on every upsert in addition to the JSON blob.
- `delete(session_id)` also removes `<SESSION_DATA_DIR>/{session_id}/` recursively (idempotent — missing directory is fine).

### 4.3 Parquet helper (new module `memory/session_data.py`)

```python
def session_dir(session_id: str) -> Path                  # ensures parent exists
def write_cleaned_parquet(session_id: str, df_parquet_bytes: bytes) -> str
def read_cleaned_parquet(session_id: str) -> bytes | None
def delete_session_files(session_id: str) -> None
```

`write_cleaned_parquet` writes to `cleaned_data.parquet.tmp` then `os.replace` → final name (atomic on POSIX). Returns the absolute path, which the caller stores in `session.cleaned_data_path`. Bytes-in / bytes-out — no DataFrame round-tripping at this layer, since the graph already carries data as parquet bytes.

### 4.4 Config

Add `SESSION_DATA_DIR: str = "./session_data"` to `api/config.py`. Directory is created on backend startup next to the existing `uploads/` bootstrap.

---

## 5. Graph & Chat-Route Wiring

### 5.1 Write path (cleaning_node)

At the end of `cleaning_node`, after `CleaningAudit` is built and the cleaned DataFrame is serialized to `df_parquet`:

```python
try:
    path = write_cleaned_parquet(session.session_id, df_parquet)
    session.cleaned_data_path = path
except Exception as exc:
    logger.warning("cached parquet write failed: %s", exc)   # non-fatal
```

The parquet replaces whatever was there from a prior run on the same session (e.g. a quality-check retry).

### 5.2 Status transitions

- `ingestion_node` first line: `session.status = "in_progress"` (covers both initial runs and resume/retry).
- `insight_node` on success: `session.status = "completed"`.
- `error_node`: `session.status = "failed"`.

The chat-route's `finally` block already calls `session_store.save()`; the indexed `status` column is updated automatically.

### 5.3 Read path on follow-up (`ingestion_node`)

Today, when `task_plan.resume_from == "analysis"` the graph skips intake + cleaning and `ingestion_node` re-loads from the connector. New behavior:

```python
if session.cleaned_data_path and Path(session.cleaned_data_path).exists():
    df_parquet = read_cleaned_parquet(session.session_id)
    logger.info("loaded cached parquet for session %s", session.session_id)
else:
    df_parquet = await connector.load_parquet(...)   # existing path
```

If the file is missing (server wiped, fresh session, or non-resume run), behavior is unchanged.

### 5.4 Read path on cached load (no chat call)

`GET /api/sessions/{id}/results` returns the full UI snapshot from `session_store.load()`. The graph is not invoked. The cleaned DataFrame is *not* in the response body — the frontend only needs `insight_report`, `chart_configs`, `targeted_response`, `expert_review`, `conversation_history`. If a future feature needs row-level data, `GET /api/sessions/{id}/data` streams the parquet.

### 5.5 Dedup wiring (upload time, pre-`/chat`)

Happens entirely in `/connectors/upload` and the frontend; the graph does not need to know.

1. Upload endpoint computes SHA256 streaming, calls `session_store.find_by_hash(user_id, hash)`.
2. If a hit is returned, the response includes `existing_session`. The frontend renders the prompt and either:
   - **Load previous** → calls `GET /api/sessions/{id}/results`, hydrates UI from the snapshot. No `/chat` call. Subsequent follow-ups call `/chat` with that `session_id` and follow the read path above.
   - **Run fresh** → calls `/chat` with no `session_id`, passing the new `connector_config` + `file_hash` + `dataset_name`. The chat route stamps these onto the new session.
3. If no hit, normal first-message flow with `file_hash` + `dataset_name` carried in.

### 5.6 Chat-route changes

`ChatRequest` gains optional `file_hash` and `dataset_name` strings. On new-session creation, they are written to the `AnalysisSession`. On resume, they are ignored (the loaded session already has them). That is the entire delta.

---

## 6. API Endpoints

All endpoints live in a new `api/routes/sessions.py`, mounted at `/api/sessions`. All require `get_current_user`; all enforce `session.user_id == current_user.uid` (same pattern as `/chat` and `/dashboard`): 404 on missing, 403 on cross-user access. All are rate-limited via the existing `limiter`.

| Method & Path                          | Returns                                                                                                                                                                                       | Notes                                                                                                                                |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `GET /api/sessions`                    | `list[SessionSummary]` sorted by `updated_at` desc, default `limit=50`                                                                                                                        | Lists the current user's sessions. Driven by indexed columns.                                                                        |
| `GET /api/sessions/{id}`               | `SessionSummary`                                                                                                                                                                              | Metadata only.                                                                                                                       |
| `GET /api/sessions/{id}/results`       | `{session_id, dataset_name, insight_report, targeted_response, chart_configs, expert_review, conversation_history, status, original_query, row_count, column_count}`                          | The "load cached" payload. No DataFrame.                                                                                             |
| `GET /api/sessions/{id}/conversation`  | `list[Message]` (from `conversation_history`)                                                                                                                                                 | Thin convenience endpoint.                                                                                                           |
| `GET /api/sessions/{id}/data`          | `application/octet-stream` parquet bytes, `Content-Disposition: attachment`                                                                                                                   | Streams `cleaned_data.parquet` via `FileResponse`. 404 if no `cleaned_data_path`. Not used by the sidebar load flow.                 |
| `DELETE /api/sessions/{id}`            | `{deleted: true}`                                                                                                                                                                             | Calls `session_store.delete(id)` which now also removes `<SESSION_DATA_DIR>/{id}/`. Idempotent.                                       |

Router mount: `app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])` in `api/main.py` next to the existing routers.

**Why no SSE on cached load.** The SSE protocol is for streaming live pipeline progress. Cached results are static; a single REST round-trip is faster and the frontend already has clean state machinery for ingesting a complete `InsightReport` snapshot.

---

## 7. Frontend

### 7.1 `SessionSidebar.tsx` (new)

Rendered in [ChatInterface.tsx](../../../frontend/src/components/ChatInterface.tsx) below the existing data-source buttons. Sized like the existing sidebar block; not a separate route.

Props:

```ts
{
  activeSessionId: string | null;
  onLoadSession: (id: string) => void;
  onNewAnalysis: () => void;
}
```

Each row renders: `dataset_name`, formatted `updated_at` ("2d ago"), `row_count`, a one-line `original_query` preview, a status pill (`in_progress` / `completed` / `failed`), and a trash icon. The active row is highlighted. The top of the panel has a "New analysis" button that calls `onNewAnalysis`.

### 7.2 `useSessions.ts` (new hook)

Wraps `GET /api/sessions`. Returns `{sessions, loading, refresh, deleteSession}`. Refreshes on:

- Mount.
- Every `done` SSE event from `useAnalysis` (list is stale after the pipeline finishes).
- `deleteSession` resolution.
- `onNewAnalysis` (a newly-created `in_progress` row should appear once the upload starts).

### 7.3 Load-cached flow (`onLoadSession`)

Calls `GET /api/sessions/{id}/results`. Hydrates the existing `useAnalysis` state via a new `hydrateFromSnapshot(data)` method so we never bypass its state machine:

- `insightReport` ← `data.insight_report`
- `charts` ← `data.chart_configs`
- `targetedResponse` ← `data.targeted_response`
- `expertReview` ← `data.expert_review`
- `messages` ← `data.conversation_history` (mapped to the existing `Message` shape)
- `sessionId` ← `data.session_id`

No SSE is opened. Subsequent follow-up questions call `/chat` with the loaded `session_id` — the backend resume + cached-parquet path (§5.3) handles them.

### 7.4 Dedup prompt (inside `ConnectorSetup.tsx`)

When `POST /connectors/upload` returns with `existing_session` populated, surface a small modal styled like `ApprovalModal.tsx`:

> **`titanic.csv`** was analyzed on May 20. Load previous analysis or run fresh?
> `[Load previous] [Run fresh] [Cancel]`

- **Load previous** → `onLoadSession(existing_session.session_id)`; the new upload's `connector_config` is discarded (file stays in `uploads/` — GC is out of scope).
- **Run fresh** → proceed exactly as today, but the next `/chat` call includes `file_hash` + `dataset_name` from the upload response.
- **Cancel** → close the modal and discard the in-memory upload result; the file written to `uploads/` is intentionally not deleted (orphan-file GC is out of scope, see §1).

### 7.5 "New analysis" button

Clears `useAnalysis` state, clears `connector_config` in the parent, scrolls to the connector picker. No API call.

### 7.6 Multi-tenant

Already covered. Every `/api/sessions/*` request goes through `get_current_user`, the store enforces `user_id` ownership, and the sidebar list is implicitly scoped to the logged-in user.

---

## 8. Phasing & Verification

Each phase is shippable on its own, does not regress prior behavior, and has explicit manual checks (no test suite exists yet — verification is via UI + curl + DB inspection).

### Phase 1 — Persistence groundwork (backend only, invisible to users)

Scope:

- Schema fields on `AnalysisSession`; `SessionSummary` model.
- `SessionRecord` columns + idempotent ALTER for Postgres.
- `SessionStore` + `InMemorySessionStore` gain `list_sessions`, `find_by_hash`, `get_summary`; `delete` removes session dir.
- `memory/session_data.py` parquet helper.
- `SESSION_DATA_DIR` config; directory bootstrapped on startup.
- `api/routes/sessions.py` with all six endpoints, mounted in `main.py`.

Verification gate:

- Backend boots clean against in-memory store and against an empty SQLite/Postgres DB.
- Existing `/chat` flow runs end-to-end unchanged; resulting session can be fetched via `GET /api/sessions/{id}` and `GET /api/sessions/{id}/results`.
- `GET /api/sessions` lists it; `DELETE /api/sessions/{id}` removes the DB row and (if any) the session dir.
- `curl` checks: 403 cross-user, 404 unknown-id.

### Phase 2 — Pipeline wiring (backend; user-observable only via repeat-question speed)

Scope:

- `cleaning_node` writes parquet on success.
- `ingestion_node` prefers cached parquet on resume; falls back to connector if absent.
- Status transitions in `ingestion_node` / `insight_node` / `error_node`.
- `/connectors/upload` returns `file_hash`, `dataset_name`, optional `existing_session`.
- `ChatRequest` accepts `file_hash` + `dataset_name`; chat route stamps them on new sessions.

Verification gate:

- Upload + analyze a CSV end-to-end. Inspect `<SESSION_DATA_DIR>/{id}/cleaned_data.parquet` exists.
- Re-upload the same file. `/connectors/upload` response contains `existing_session` referencing the prior id. (Frontend doesn't act on it yet — that's Phase 3.)
- Ask a follow-up on the prior session; log line `"loaded cached parquet …"` fires.
- `DELETE /api/sessions/{id}` removes the parquet from disk.

### Phase 3 — Frontend (user-facing)

Scope:

- `useSessions` hook + `SessionSidebar` component rendered in `ChatInterface`.
- `useAnalysis.hydrateFromSnapshot()` for cached loads.
- Dedup modal in `ConnectorSetup`.
- "New analysis" button.
- List refresh hooked to `done` SSE event and to delete / new-analysis actions.

Verification gate:

- Fresh user, fresh DB: sidebar empty.
- Upload + analyze: session appears in sidebar with status transitioning `in_progress` → `completed`.
- Click an existing session: UI hydrates from `/api/sessions/{id}/results` without any SSE; charts, KPIs, conversation render.
- Re-upload same file: dedup modal appears; "Load previous" hydrates; "Run fresh" creates a second session entry.
- Delete a session: row disappears; if it was active, workspace clears.
- Multi-tenant smoke: second user sees only their own sessions.

---

## 9. Open Questions

None — all design decisions ratified during brainstorming (storage approach, dedup scope, phasing). Any further questions are deferred to plan-writing.
