# Architecture reference

Where code lives and what the run stream carries. Moved out of CLAUDE.md (retro 2026-11-04) so
it loads only when needed; keep it current in the same commit as the change it describes.

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

domain_pack.py             — Phase 5.7 domain packs (domain_packs/<domain>.yaml, strict schema): detect →
                             strong/weak (strong = defaults apply; weak = hedge + a KPI the user names),
                             row_kinds/with_row_kind (one row filter), compute_kpi, money_rows, pack_brief,
                             pack_caveats, first_questions; adding a domain = a YAML file. Shared code
                             never names a role: a pack declares its layout (order_key,
                             negative_is_return, item_roles, line_weight, cost_roles, location_role,
                             row_labels/row_flags/text, overview_groups); status/currency are reserved
                             role names; tests/test_second_pack.py + test_one_definition.py guard it.
                             Domain 2: a level (`over_time: last`, `snapshot_key`) sets the same
                             ColumnInfo hint as the semantic layer; `KPI.at: latest` = latest row
                             of any kind, then filters; `Term.where`; `layout_columns` (one blanks
                             rule); `entity_rows` (one row per entity for every r shown)
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

scripts/
  domain_diff.py           — a branch vs a base ref: every finished domain's user-facing outputs
                             (tiles, charts, filters, filtered dashboards, answers), $0
  ui_check/                — the real frontend on a mock backend ($0); make_fixture.py --dataset
                             <name> serves any dataset's dashboard through the real filtered_dashboard
  live_check/              — the live stack (production mode, asyncpg, real worker): up.sh, ask.py,
                             spend.py (what live runs cost), down.sh
```

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
