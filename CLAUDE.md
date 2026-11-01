# AnalystAI — CLAUDE.md

> **Purpose**: Canonical project context for AI coding assistants. Read this FIRST to avoid re-reading every file on every session. Reduces token burn by ~80%.

---

## Roadmap & Current Phase (READ THIS SECOND)

The project is executing **`docs/ROADMAP.md`** — the "trustworthy agents" plan (approved 2026-10-27):
every agent gets guardrails, memory context, an agent card (self-knowledge), retrieval tools,
evals/tests, and evidence-backed justification; then an open-source v0.1 release.

**Working rules**
- **One phase per session.** Finish the phase, verify its "Done when" criteria, post a short review
  note (built / metrics / deviations), update the status table below, then tell the user
  "Phase N done" — they `/clear` and start the next phase in a fresh session.
- Never start the next phase without the user's explicit sign-off.
- "Done" requires a committed test run or eval report showing the number — no metric, not done.
- Measure before changing: every phase must not regress the Phase 1 baseline.

| Phase | Scope | Status |
|---|---|---|
| 0 | Research + gap map + ADRs (`docs/research/`, `docs/adr/`) | ✅ done (2026-10-27) |
| 1 | Shared LLM client, traces, synthetic data generator, eval harness, CI, **baseline** | ✅ done (2026-10-27) — `docs/baselines/phase1.md` |
| 2 | Typed errors, guardrails module, PII masking, cut CrewAI / fold senior_expert / resolve drill-down | ✅ done (2026-10-27) — `docs/baselines/phase2.md` (analysis 87.5%, e2e 45.0%) |
| 3 | Analysis agent as the agent-contract template | ✅ done (2026-10-27) — `docs/baselines/phase3.md` (numeric 97.6%, recall 100%, false findings 0.41%, p95 27s, e2e 45.0%); guide `docs/agent-contract.md` |
| 3.5 | UI & layout redesign (added 2026-10-28; no API spend) | ✅ done (2026-10-28) — `docs/baselines/phase3_5.md` (signed off; phone width, light/dark, a11y basics, lint 0) |
| 4 | SQL path + YAML semantic layer | ✅ done (2026-10-27) — `docs/baselines/phase4.md` (execution 100%, metric use 100%, attacks blocked 100%, fan-out 8/8; k=3, 50 cases retail + banking; e2e 100%) |
| 5 | Roll the contract out: Intake → Visualization → Cleaning → Insight → Quality check → Ingestion | ✅ done (2026-10-30) — **Intake ✅ (2026-10-28)** `docs/baselines/phase5_intake.md` (intent 97.4%, columns 98.2%, asks 100%, no-ask 100%, invented 0; SQL follow-ups 100%; e2e 100%; $4.39); **Visualization ✅ (2026-10-28)** `docs/baselines/phase5_visualization.md` (right chart 100%, headline coverage 100%, lint 0, no-rationale 0; 27 cases, $0; e2e 100%; $1.87); **Cleaning ✅ (2026-10-28)** `docs/baselines/phase5_cleaning.md` (recall 100%, over-cleaning 0%, hard 0; 25 cases k=3, $0; e2e 100%; $0.98); **Insight ✅ (2026-10-29)** `docs/baselines/phase5_insight.md` (grounding 100%, dates 100%, citations 100%, jargon 0, judge faithfulness 96.3%; 40 bundles k=3; e2e 100%, 0 hard; $4.35); **Quality check ✅ (2026-10-29)** `docs/baselines/phase5_quality.md` (defect recall 97.1%, false alarms 0.67%, hard 0; 40 reports k=3, 102 seeded defects; e2e 100%; $4.05); **Ingestion ✅ (2026-10-30)** `docs/baselines/phase5_ingestion.md` (type inference 100%, PII recall 100%, load fidelity 100%, PII canary 0, no LLM; 31 cases k=3 at $0; intake 100%, e2e 100%; $2.09) |
| 5.6 | Durable runs (run records, Postgres queue + worker, LangGraph checkpoints, replayable events; fixes audit B3/B4) and the analysis workspace (left sidebar, datasets, immutable report snapshots) — runs after Phase 5 Ingestion, before 5.5 (added 2026-10-29) | **5.6a durable runs ✅ (2026-10-30)** `docs/baselines/phase5_6a.md` (kill at all 11 checkpoints → identical report, memory + Postgres; post-phase audit fixed 9 bugs; reconnect replay exact; approval after restart; prod refuses without PG; e2e 100%; $0.90; ADR-0006); **5.6b workspace ✅ (2026-10-31)** `docs/baselines/phase5_6b.md` (reopened report = delivered (sha256 + DOM); delete leaves 0 in every store, memory + Postgres; sidebar list p95 5.4 ms at 200 threads, 390 px; plan red-team + 2 independent reviews + confirmation before done; e2e 100%; $0.82; ADR-0007) |
| 5.5 | Domain coverage: ~8 domain datasets in analysis + E2E evals, PHI masking, generic labels (added 2026-10-27) | pending |
| 6 | E2E hardening + open-source v0.1 release | pending |

Per-agent metrics and targets: see the table at the end of `docs/ROADMAP.md`.

---

## What This Is

AnalystAI is a **multi-agent AI data analyst** — six specialized agents form a pipeline that replaces a human analyst: intake → ingest → clean → analyze → visualize → deliver insights. It's self-hosted via Docker Compose, open source, and designed for small companies.

---

## Tech Stack (Frozen Decisions)

| Layer              | Tech                                              |
| ------------------ | ------------------------------------------------- |
| Orchestration      | **LangGraph** (stateful routing); agents are plain async functions on `llm/client.py` |
| Primary LLM        | **Claude Sonnet 4.6** (via `anthropic` SDK)     |
| Speed-tier LLM     | **Claude Haiku 4.5** (`settings.llm_fast_model`; Groq retired in Phase 1) |
| Embeddings         | **nomic-embed-text** via Ollama (self-hosted)      |
| Backend            | **FastAPI** (Python 3.11+, `uv` managed)           |
| Code Sandbox       | **E2B** (isolates LLM-generated Python from main process) |
| Frontend           | **Next.js 16** + Tailwind v4 + shadcn/ui v4 + Recharts v3 |
| Session Store      | **PostgreSQL** (JSONB via SQLAlchemy async)         |
| Vector Store       | **Qdrant** (institutional memory)                   |
| Package Manager    | Python: `uv` · JS: `npm`                           |

---

## Agent Pipeline

```
User query
  │
  ▼
[1. Intake Agent]      Claude  — NL → AnalysisTask (structured plan)
  │
  ▼
[2. Ingestion Agent]           — deterministic (no LLM): load → roles, PII, masked samples, source evidence → DataContract
  │
  ▼
[3. Cleaning Agent]            — deterministic (no LLM): parse/merge/dedup, nothing imputed → CleaningAudit
  │
  ▼
[4. Analysis Agent]    Claude  — generates Python → E2B sandbox → AnalysisFindings
  │
  ▼
[5. Visualization Agent]       — deterministic (no LLM): chart per finding + rationale, linted → list[ChartConfig]
  │
  ▼
[6. Insight Agent]     Claude  — synthesizes all → InsightReport
  │
  ▼
[Quality Check Node]  checks + critic — FEAT-02 retry once; every full run: claim checks; first run: + Haiku critic + verify (expert_review) → END
```

### Graph Entry Point (IMPORTANT)
The LangGraph entry point is `ingestion`, NOT `intake`. The graph runs:
`ingestion → intake → cleaning → analysis → visualization → insight → quality_check → END`

This is intentional: ingestion loads data first so the intake agent has schema context (column names, row count) when parsing the user's question.

### Quality Check Retry Loop (FEAT-02)
After `insight_node`, the graph routes to either `error_node` (if `session.error_message` is set) or `quality_check_node`. The quality check inspects the report for missing/short summary, missing technical findings, or missing charts. If issues are found AND `session.quality_check_attempts < 2`, it clears the analysis state and routes back to `analysis` for one retry; the retry budget is reset to 0 at the start of every graph run inside `ingestion_node`. The node is the single source of truth for the retry decision — `route_after_quality_check` only translates "did the node clear `insight_report`?" into `analysis` vs `end`. On the commit path the node runs the review (`agents/quality_agent.py`): code checks every claim against the evidence it cites on every full run; on the first run a Haiku critic (+ a verification pass) labels what the checks can't; the report itself is never changed (flags are shown next to their claims); then it flips `overview_done`.

### Errors (Phase 2)
Agents raise typed `AgentError` subclasses (`agents/errors.py`); graph nodes route every failure through `_fail()`, which puts `exc.user_message` into `session.error_message` → `error_node` → SSE `error`. Never return empty output on failure, and never `except ...: pass` in `orchestrator/` (a test enforces it). `targeted_analysis` / `drill_down` failures also route to `error_node`.

### Multi-Turn Resume
When `task.resume_from == "analysis"`, routing skips intake + cleaning and jumps directly from ingestion to analysis. The DataFrame is re-loaded from the connector (not stored in DB).

### Durable Runs (Phase 5.6a, ADR-0006)
`POST /chat` stores a run (`memory/run_store.py`: `runs` is also the queue, `FOR UPDATE SKIP
LOCKED`) and returns `{run_id, session_id, status}`. A worker (`python -m worker`; development
also runs one inside the API) executes it with `orchestrator/runner.py`. The graph is
checkpointed after every node (`durability="sync"`, thread id = **run id**, encrypted on
Postgres), so a crash resumes at the last finished node. Lease/heartbeat are fenced by `attempt`
(only goes up); `crashes` is the reaper's budget (3). One active run per session (the store
enforces it); only the latest run of a session can be retried. Cancel and "retry from
where it failed" are `POST /runs/{id}/cancel|retry`.
- **State holds no queue, stores or DataFrame**: those live in `graph.RunEnv` (a ContextVar; tests
  get one from the `run_env` fixture). The state names the frame by `frame_ref`; a resume reloads
  it and checks the hash (a change fails the run). The runner seeds `pii_values` on every (re)start.
- Every SSE event goes to `run_events` before its node's checkpoint; `GET /runs/{id}/events`
  replays after `Last-Event-ID`. Audit B4: production API and the worker refuse to start without
  Postgres; dev falls back to memory and `/health` says `persistent: false` (UI: "Not saved").

### Workspace (Phase 5.6b, ADR-0007)
- **One dataset per thread.** Uploads are dataset versions (`datasets`, `dataset_versions`): same
  bytes → that version, same name + columns → new version, else a new dataset; live sources are
  one dataset per connection, no versions. `POST /chat` names data by `dataset_version_id` /
  `dataset_id` (never a file path); other data on an existing thread is a 409.
- **A report is its run's own report events** (`run_store.report_events`, segmented as the UI
  does), sealed in `runs.report_sha256` at every terminal transition; never rebuilt from the
  session. Provenance = the run's `provenance` + `usage` events (`llm.client.run_meter`).
  `/api/sessions/{id}/thread` returns them; the UI replays them through `handle()`.
- **Only `POST /chat` creates a thread** (`session_store.create`); `save` is update-only and a
  no-op on a deleted thread. Seed tests with `create`.
- **Deletion = tombstone + purge** (`memory/retention.py`, in the worker loop): every table with
  user data is in `PURGE_TABLES` (a test checks); `RETENTION_DAYS` (0 = forever).

### Human Approval Gates
- **Clarification**: intake detects ambiguous query → `human_approval` node → SSE `approval_required` event
- **High null**: cleaning finds >40% null columns → `human_approval` node → modal in frontend;
  the answer reaches `cleaning_node` (Continue = keep as missing, Drop = `drop_column`, Cancel = stop)
- Both are persisted `interrupt()`s: the run waits (`waiting_approval`) across restarts; the
  answer (`POST /chat` with `is_approval`) resumes the same run → intake or cleaning
  (`route_after_approval`). `rerun_overview` is a UI choice: the run ends, the UI asks again.

---

## Key File Map

```
agents/
  intake_agent.py          — contract agent: INTAKE_CARD, prompts/intake/vN.md, build_context (schema +
                             conversation_context memory + previous plan, all delimited), hard rules
                             (plan_columns, intent, JSON: 1 guided retry, then drop + ask), returns AnalysisTask
  ingestion_agent.py       — contract agent, PURE PYTHON (no LLM): INGESTION_CARD, build_context,
                             infer_role (values first; names only break ties), build_contract (roles +
                             reasons, PII kind, masked samples, computed summary, fingerprint, Evidence
                             kind "source"), known_pii_values → llm.client.pii_values; IngestionError
  cleaning_agent.py        — contract agent, PURE PYTHON (no LLM): CLEANING_CARD, build_context →
                             CleanContext, clean() = 9 allowed operations, one CleaningDecision each
                             (nothing imputed: missing numbers/IDs/dates/targets stay missing, labels →
                             "Unknown"; key-collision label merge; dedup only with an ID column);
                             guardrails re-check rows/changes/protected; build_evidence → Evidence
                             kind "cleaning"; returns (DataFrame, CleaningAudit)
  analysis_agent.py        — the agent-contract reference (docs/agent-contract.md): ANALYSIS_CARD,
                             build_context, tools (retry only), code → guard → E2B → built-in
                             standard stats → hard rules (1 guided retry) → findings + Evidence
  visualization_agent.py   — contract agent, PURE PYTHON (no LLM): VIZ_CARD, build_context → VizContext,
                             charts_for_findings (trend→monthly line, comparison→bars + 95% CI,
                             correlation→scatter driver on x, anomaly→histogram + fences), profile
                             charts for overviews (row counts → Data tab), finalize_charts = lint
                             (guardrails.chart_lint) then repair/drop, or a note on the user's choice;
                             every chart has a ChartRationale; swap_options; KPI how + kpi_mismatches.
                             Targeted answers, chart requests and drill-down charts all go through it
  insight_agent.py         — contract agent: INSIGHT_CARD, prompts/insight/vN.md, build_context (computed facts +
                             date range from CleaningAudit.date_ranges, evidence lines, evidence charts, scoped
                             memory; no schema_summary / raw rows), model writes only claims citing Evidence ids;
                             repair: ungrounded number/date or jargon sentence removed, uncited claim removed;
                             analyst view + alerts built from Evidence and audit; returns InsightReport
  quality_agent.py         — contract agent: QUALITY_CARD, report_claims (headline, summary, findings,
                             actions), check_claims (guardrails.claim_*: dates, direction, period,
                             group, rank, numbers, citations, cause, certainty), critic (Haiku,
                             prompts/quality/vN.md, first run only) + verify pass; output repaired
                             (ungrounded reason replaced, bullets dropped); → ExpertReview + ReviewFlag[]
  sql_agent.py             — Phase 4: question → one read-only SQL query (SQL_CARD, prompts/sql/vN.md,
                             tools get_metric_definition/run_sql, fan-out + metric hard rules,
                             Evidence kind "query"); called by ingestion for a SQL source with no table
  drill_down_agent.py      — follow-up answer + chart computed from the df
  targeted_analysis_agent.py — deterministic answers for non-overview intents
  errors.py                — AgentError hierarchy (LLMRefusal/Truncated/Unavailable/OutputError, GuardrailViolation, SandboxError)

prompts/<agent>/v<N>.md    — versioned system prompts (analysis: v3 = old v2 verbatim … v7 current;
                             intake: v1 = old inline prompt verbatim, v2 current, overview_vN = first-run override;
                             insight: v1 = old prompt verbatim, v4 current; insight_judge: v1 = the eval's judge)

semantic_layer.py          — YAML semantic layer (ADR-0003): tables + grain, metrics, relationships;
                             strict loader, check_against_db; SEMANTIC_LAYER_PATH (invalid = no boot)

guardrails.py              — pure checks: code AST allowlist, column refs, read-only SQL (sqlglot),
                             SQL fan-out (grain/cardinality) and metric adherence,
                             numeric grounding, PII masking, <untrusted_data> delimiting

orchestrator/
  state.py                 — ALL Pydantic schemas (AnalysisSession is the master state)
  graph.py                 — LangGraph StateGraph: 11 nodes + conditional edges (incl. quality_check, targeted_analysis, drill_down, human_approval, error)
  routing.py               — one routing function per conditional edge
  runner.py                — durable runs: execute (fresh / crash resume / gate answer / retry),
                             frame reload + PII seeding, worker loop, encrypted checkpoint serde

worker/__main__.py         — `python -m worker`: claims runs from the Postgres queue

connectors/
  base.py                  — DataConnector ABC + get_connector() factory
  csv_connector.py         — one reader (_read_bytes): encoding, delimiter, title rows, decimal comma,
                             TOTAL footer, largest Excel sheet; lossless (dates parsed only if all parse)
  sql_connector.py         — SQLAlchemy (psycopg 3 for Postgres); query() = parse guard → READ ONLY
                             txn (always rolled back) → statement timeout → 10k-row cap
  sheets_connector.py      — gspread + OAuth2
  api_connector.py         — httpx + pagination + auth

memory/
  session_store.py         — threads (create/save fence, title, pin, archive, tombstone), pinned
                             charts, datasets + versions (upload rules, split, live sources);
                             Postgres + InMemorySessionStore twin
  vector_store.py          — Qdrant + Ollama embedding; memory_key = dataset id; delete by thread
  run_store.py             — runs + run_events (Postgres queue, InMemoryRunStore twin);
                             report_events / report_hash (the seal)
  retention.py             — purge sweep: tombstoned threads/datasets, orphans, retention

sandbox/
  executor.py              — E2B wrapper: parquet→CSV, run code, parse output

api/
  main.py                  — FastAPI app, lifespan, CORS, rate limiter
  config.py                — pydantic-settings (reads .env)
  routes/chat.py           — POST /chat → stores a run (or resumes the one waiting at a gate)
  routes/runs.py           — GET /runs/{id}, /runs/{id}/events (SSE, Last-Event-ID replay),
                             POST /runs/{id}/cancel, /runs/{id}/retry
  routes/dashboard.py      — GET/POST/DELETE pinned charts; POST /chart-feedback (user's chart-type
                             switch: owner only, swap_options re-checked, saved, label-only trace)
  routes/connectors.py     — POST /connectors/test, /connectors/upload (→ dataset version)
  routes/sessions.py       — threads: list (columns + run status), PATCH, DELETE (tombstone),
                             /thread (reports as delivered), /export.md
  routes/datasets.py       — datasets: list, page, rename, delete (cascade), version split
  lib/report.py            — a run's report: fold, provenance, Markdown, PPTX input

frontend/src/
  app/page.tsx             — Landing page
  app/chat/page.tsx        — Chat view (wraps ChatInterface; ?t=<thread>, ?dataset=<id>)
  app/datasets/page.tsx    — Datasets view (DatasetsView; ?d=<id>)
  app/dashboard/page.tsx   — Dashboard view (AppHeader + AutoDashboard)
  components/
    ChatInterface.tsx      — /chat "Workbench": sidebar (collapsible at lg, <dialog> drawer below),
                             chat column drives ReportCanvas; thread in the URL
    SessionSidebar.tsx     — threads by date, search, status dot, rename / pin / archive / delete
    DatasetsView.tsx       — datasets list + page (versions, analyses, pinned charts)
    ConfirmDialog.tsx      — native <dialog> confirm
    ReportCanvas.tsx       — one answer as a report: Summary / Evidence / Data / Review tabs
    AppHeader.tsx          — header shared by /chat and /dashboard (nav, theme toggle, sign out)
    ThemeToggle.tsx        — flips the `dark` class set pre-paint in layout.tsx (localStorage "theme")
    AgentProgressStream.tsx — Step-by-step agent progress indicator
    ChartRenderer.tsx      — Recharts chart-type router; colours from --chart-1..8 tokens
    InsightPanel.tsx       — Executive/Analyst toggle view
    ConnectorSetup.tsx     — Data source configuration panel
    ApprovalModal.tsx      — Human approval gate modal
    AutoDashboard.tsx      — dashboard: KPI strip, filters, cross-filtered chart grid, edit mode
    EvidencePanel.tsx      — Evidence list + <dialog> drawer (code, rows, confidence, assumptions)
  hooks/useAnalysis.ts     — SSE stream consumer, state management; hydrateThread (reopen)
  types/index.ts           — TypeScript mirrors of Python Pydantic models
  lib/api.ts               — fetch wrappers for dashboard endpoints
```

---

## Data Flow Through the System

1. **DataFrame is NEVER stored in the database** (nor in a checkpoint: a test checks). Agents read it as parquet bytes from `session.__dict__["_df_parquet"]` (a transient attribute, not a Pydantic field).
2. In `graph.py`, nodes keep it in `run_env.frame` (in memory, per run) and put it on the session for the agent; the checkpointed state only names it (`frame_ref`).
3. For E2B, parquet is converted to CSV locally (`_parquet_to_csv_bytes`) because E2B's Python env lacks pyarrow.
4. The final `analysis_node` discards `_df_parquet` — it's not needed after analysis.

---

## SSE Event Protocol

`GET /runs/{id}/events` streams these event types (each with `id: <seq>`; replay after Last-Event-ID):
```
session_id        → {type: "session_id", value: "<uuid>", message, is_approval}  // seq 1, written by POST /chat
approval_answered → {type: "approval_answered", context, answer}  // a gate's answer resumed this run
run_resumed       → {type: "run_resumed", message}  // a worker restarted it, or a retry
agent_start       → {type: "agent_start", agent: "<name>", message: "<status>"}
agent_complete    → {type: "agent_complete", agent: "<name>", message: "<result>"}
approval_required → {type: "approval_required", context: "clarification"|"high_null", ...}
suggested_questions → {type: "suggested_questions", questions: string[]}  // FEAT-01
evidence          → {type: "evidence", data: Evidence[]}  // Phase 3: one per analysis finding (+ "source" first (Phase 5 Ingestion), + one "cleaning", + "query" for SQL, + "eda" per described column)
charts            → {type: "charts", data: ChartConfig[]}  // Phase 5: + rationale, placement, lint_warnings, error_key, split_key, reference_lines
export            → {type: "export", format: "pptx", url: "/dashboard/latest/report.pptx"}
insight           → {type: "insight", data: InsightReport}  // Phase 5: findings/paragraphs are Claim{text, evidence_ids}; actions carry evidence_ids
quality_retry     → {type: "quality_retry", message: "<reason>; Re-running analysis..."}  // FEAT-02
expert_review     → {type: "expert_review", data: ExpertReview}  // every full run; Phase 5: + flags: ReviewFlag[] {index, section, claim, label, kind, reason, evidence_ids, source}; critic on the first run only
error             → {type: "error", message: "<user-readable>", retryable?}
done              → {type: "done"}  // ends a run (a retry can add more after it); a gate waits without one
provenance        → {type: "provenance", model, fast_model, cards}  // Phase 5.6b: first, on a fresh start
usage             → {type: "usage", calls: [{agent, prompt_version, model, tokens…, cost_usd}]}  // per node; the UI ignores both
```

---

## Code Conventions

- **Python**: 3.11+, `ruff` linter, 100-char line length, `async/await` everywhere
- **Pydantic v2**: all schemas use `BaseModel`, `model_dump_json()`, `model_validate_json()`
- **LLM responses**: always strip markdown fences (```` ```json ... ``` ````) before JSON parsing
- **Error handling**: every agent wraps its core logic in try/except, sets `session.error_message`
- **Frontend**: TypeScript strict, Tailwind v4 with `@import "tailwindcss"`, `"use client"` on all interactive components
- **Frontend colours**: theme tokens only (`bg-card`, `text-muted-foreground`, `bg-primary`, `text-success`, `--chart-N` …,
  defined for light + dark in `globals.css`). Raw palette classes / hex fail `tests/test_frontend_tokens.py`.
  UI checks without keys: `scripts/ui_check/README.md`
- **Untrusted data**: dataset text in a prompt goes through `delimit_untrusted()` with `UNTRUSTED_NOTE` in the system prompt (`tests/test_untrusted_data.py` fails CI for any LLM-calling agent without both); PII is masked once in `build_data_contract` (mask anything else you send with `mask_row(row, pii_columns(contract))`). **PII hard rule**: each run sets `llm.client.pii_values` (chat route, eval harness), ingestion fills it, and `ClaudeClient` tokenizes those values in every request (`[PII_xxxxxxxx]`, restored in the reply). A `pii_tokenized` trace label means a builder leaked: fix the builder
- **LLM calls**: through `llm/client.py` — never a new `AsyncAnthropic`. Contract agents (all but drill-down) take `llm: LLMClient` as a parameter; the rest still call `get_client()` until their Phase 5 PR. Every system prompt is a module constant and is prompt-cached by `ClaudeClient` (keep it constant: per-call text belongs in messages). Model is pinned in `settings.llm_model`; traces go to `traces/<date>.jsonl` (metadata, tool names and guardrail rule labels only)
- **Agent contract**: new/refactored agents follow `docs/agent-contract.md` (card, prompt file, context builder, tools, evidence, guardrails)
- **Tests**: `uv run pytest` — offline, no keys (an autouse `RecordedClient` makes real model calls impossible). Append to the `recorded_llm` fixture's `responses` to fake LLM output
- **Eval budget (real money, the user's prepaid credits)**: iterate with `--only <case> --k 1`; one full k=3
  run per suite per PR, at the end; **E2E gate = k=1, then k=3 only on failing cases** (user's choice,
  2026-10-28; a failure must be diagnosed, a pass on retry is not a diagnosis). `--max-usd` (default $2)
  refuses over-budget runs — raise it deliberately, say the estimate to the user first; stop and tell the
  user if a run costs >30% over its estimate. A cheaper model ships only if it matches every target.
  Phase 4 cost $15.44 (two checker bugs forced k=3 re-runs). Phases 0–3 cost ~$59 (2026-10-27), mostly
  repeated full k=3 rounds. Phase 5 Intake cost $4.39 (caching + rescore + this order); Phase 5 Visualization $1.87 (deterministic suite at $0; two E2E gates); Phase 5 Cleaning $0.98 (deterministic, one E2E gate); Phase 5 Insight $4.35 (stratified baseline, judge on run 1 per case); Phase 5 Quality $4.05 (checks iterated at $0; one E2E overview re-gate ran 132% over its estimate); Phase 5 Ingestion $2.09 (deterministic suite at $0; one E2E re-gate after review fixes; insight + analysis proven unaffected by snapshot, `evals/snapshot.py`); Phase 5.6a $0.90 (one recorded fixture run replays the kill tests at $0; one E2E gate); Phase 5.6b $0.82 (everything offline on the recording; one E2E gate on the final run path). **The playbook is in `docs/agent-contract.md` → "Eval cost playbook"; follow it every PR.**
- **SQL eval**: `TEST_PG_URL=postgresql+psycopg://… uv run python -m evals.run sql --k 1 --only <case>` — the
  URL must be a scratch database (the run drops and rebuilds schemas `shop`/`bank` from `evals/sqlgen.py`).
  PG tests skip without `TEST_PG_URL`; CI always sets it. Local scratch PG (no Docker): `initdb -D <dir> -U postgres -A trust`,
  `pg_ctl -D <dir> -o "-p 54329 -c unix_socket_directories='' -c listen_addresses=localhost" start`, `createdb -h localhost -p 54329 -U postgres analystai_eval`. Cases: `evals/cases/sql/` (golden SQL never goes
  in a layer's `verified_queries` or a prompt — a test checks)
- **Evals**: `uv run python -m evals.run {analysis|e2e|sql|intake|visualization|cleaning|insight|quality|ingestion} --k 3 [--compare <prev.json>] [--exclude holdout]`; a scorer fix is re-scored for $0 with `python -m evals.run rescore <report.json>` (never re-run for it) — real API spend; cases in `evals/cases/`, reports in `evals/reports/`, baseline in `docs/baselines/phase1.md`. CI (`.github/workflows/ci.yml`) runs ruff/pytest/tsc on push; evals only on manual dispatch

---

## Environment Variables

Required: `ANTHROPIC_API_KEY`, `E2B_API_KEY`, `SUPABASE_URL`, `ENCRYPTION_KEY` (the API refuses to start without the last two).
Frontend (`frontend/.env.local`): `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`
Optional: `SEMANTIC_LAYER_PATH` (SQL connector), `DATABASE_URL`, `QDRANT_HOST`, `QDRANT_PORT`, `OLLAMA_HOST`, `FRONTEND_URL`, `APP_ENV`

---

## Common Pitfalls

1. **`_df_parquet` is NOT a Pydantic field** — it's stored via `session.__dict__["_df_parquet"]`. Don't try to serialize it.
2. **Visualization agent has NO LLM** — deterministic rules keyed on the finding's kind. Every chart that
   leaves the backend must go through `finalize_charts` and carry a `ChartRationale`, or it is dropped.
3. **The graph entry point is `ingestion`**, not `intake`. This is by design for schema-aware intake.
4. **API keys are in `.env`** — NEVER commit real keys. The `.env` file should be gitignored.
5. **SQL connector blocking calls are wrapped in `asyncio.to_thread`** — every sync SQLAlchemy call in `sql_connector.py` goes through `await asyncio.to_thread(...)`. Don't add raw `engine.connect()` calls inside `async def` methods.
6. **The graph state is checkpointed** — never put a queue, store, DataFrame or other non-data
   object in `AgentState`; use `RunEnv`. A node must be safe to re-run from its start (a crash
   inside it re-runs it). E2E goes through `orchestrator.runner.execute`, as the worker does.
7. **`layout.tsx` is a server component** — `"use client"` lives in `providers.tsx`, not the root layout. Don't move it; metadata exports depend on the layout staying server-side.
