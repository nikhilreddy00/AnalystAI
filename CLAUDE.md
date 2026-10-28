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
| 4 | SQL path + YAML semantic layer | ⏭ next |
| 5 | Roll the contract out: Intake → Cleaning → Insight → Quality check → Ingestion → Visualization | pending |
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
[2. Ingestion Agent]   Haiku   — connector.load() → DataFrame + DataContract
  │
  ▼
[3. Cleaning Agent]    Claude  — impute/dedup/coerce → CleaningAudit
  │
  ▼
[4. Analysis Agent]    Claude  — generates Python → E2B sandbox → AnalysisFindings
  │
  ▼
[5. Visualization Agent]       — deterministic (no LLM) → list[ChartConfig]
  │
  ▼
[6. Insight Agent]     Claude  — synthesizes all → InsightReport
  │
  ▼
[Quality Check Node]  checks + critic — FEAT-02 retry once; first run: grounding check + LLM critic (expert_review) → END
```

### Graph Entry Point (IMPORTANT)
The LangGraph entry point is `ingestion`, NOT `intake`. The graph runs:
`ingestion → intake → cleaning → analysis → visualization → insight → quality_check → END`

This is intentional: ingestion loads data first so the intake agent has schema context (column names, row count) when parsing the user's question.

### Quality Check Retry Loop (FEAT-02)
After `insight_node`, the graph routes to either `error_node` (if `session.error_message` is set) or `quality_check_node`. The quality check inspects the report for missing/short summary, missing technical findings, or missing charts. If issues are found AND `session.quality_check_attempts < 2`, it clears the analysis state and routes back to `analysis` for one retry; the retry budget is reset to 0 at the start of every graph run inside `ingestion_node`. The node is the single source of truth for the retry decision — `route_after_quality_check` only translates "did the node clear `insight_report`?" into `analysis` vs `end`. On the first run's commit path the node also runs the critic (`agents/quality_critic.py`, formerly the `senior_expert` node), fed by the numeric-grounding check, then flips `overview_done`.

### Errors (Phase 2)
Agents raise typed `AgentError` subclasses (`agents/errors.py`); graph nodes route every failure through `_fail()`, which puts `exc.user_message` into `session.error_message` → `error_node` → SSE `error`. Never return empty output on failure, and never `except ...: pass` in `orchestrator/` (a test enforces it). `targeted_analysis` / `drill_down` failures also route to `error_node`.

### Multi-Turn Resume
When `task.resume_from == "analysis"`, routing skips intake + cleaning and jumps directly from ingestion to analysis. The DataFrame is re-loaded from the connector (not stored in DB).

### Human Approval Gates
- **Clarification**: intake detects ambiguous query → `human_approval` node → SSE `approval_required` event
- **High null**: cleaning finds >40% null columns → `human_approval` node → modal in frontend

---

## Key File Map

```
agents/
  intake_agent.py          — returns AnalysisTask (drops columns not in the DataContract)
  ingestion_agent.py       — shared LLM client (Haiku), returns DataContract
  cleaning_agent.py        — returns (DataFrame, CleaningAudit)
  analysis_agent.py        — the agent-contract reference (docs/agent-contract.md): ANALYSIS_CARD,
                             build_context, tools (retry only), code → guard → E2B → built-in
                             standard stats → hard rules (1 guided retry) → findings + Evidence
  visualization_agent.py   — PURE PYTHON (no LLM), returns list[ChartConfig]
  insight_agent.py         — returns InsightReport
  quality_critic.py        — LLM critic run by quality_check_node → ExpertReview
  drill_down_agent.py      — follow-up answer + chart computed from the df
  targeted_analysis_agent.py — deterministic answers for non-overview intents
  errors.py                — AgentError hierarchy (LLMRefusal/Truncated/Unavailable/OutputError, GuardrailViolation, SandboxError)

prompts/<agent>/v<N>.md    — versioned system prompts (analysis: v3 = old v2 verbatim … v7 current)

guardrails.py              — pure checks: code AST allowlist, column refs, read-only SQL (sqlglot),
                             numeric grounding, PII masking, <untrusted_data> delimiting

orchestrator/
  state.py                 — ALL Pydantic schemas (AnalysisSession is the master state)
  graph.py                 — LangGraph StateGraph: 11 nodes + conditional edges (incl. quality_check, targeted_analysis, drill_down, human_approval, error)
  routing.py               — one routing function per conditional edge

connectors/
  base.py                  — DataConnector ABC + get_connector() factory
  csv_connector.py         — CSV/Excel with encoding detection
  sql_connector.py         — SQLAlchemy + NL→SQL on the shared client, parsed read-only guard
  sheets_connector.py      — gspread + OAuth2
  api_connector.py         — httpx + pagination + auth

memory/
  session_store.py         — PostgreSQL + InMemorySessionStore fallback
  vector_store.py          — Qdrant + Ollama embedding

sandbox/
  executor.py              — E2B wrapper: parquet→CSV, run code, parse output

api/
  main.py                  — FastAPI app, lifespan, CORS, rate limiter
  config.py                — pydantic-settings (reads .env)
  routes/chat.py           — POST /chat → SSE stream (runs full LangGraph)
  routes/dashboard.py      — GET/POST/DELETE pinned charts
  routes/connectors.py     — POST /connectors/test, /connectors/upload

frontend/src/
  app/page.tsx             — Landing page
  app/chat/page.tsx        — Chat view (wraps ChatInterface)
  app/dashboard/page.tsx   — Dashboard view (AppHeader + AutoDashboard)
  components/
    ChatInterface.tsx      — /chat "Workbench": chat column (left) drives ReportCanvas (right);
                             Chat/Report tabs below lg; history in a <dialog> drawer
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
  hooks/useAnalysis.ts     — SSE stream consumer, state management
  types/index.ts           — TypeScript mirrors of Python Pydantic models
  lib/api.ts               — fetch wrappers for dashboard endpoints
```

---

## Data Flow Through the System

1. **DataFrame is NEVER stored in the database.** It flows as parquet bytes through `session.__dict__["_df_parquet"]` (a transient attribute, not a Pydantic field).
2. In `graph.py`, each node pops `_df_parquet` from session and passes it via `AgentState["df_parquet"]`.
3. For E2B, parquet is converted to CSV locally (`_parquet_to_csv_bytes`) because E2B's Python env lacks pyarrow.
4. The final `analysis_node` discards `_df_parquet` — it's not needed after analysis.

---

## SSE Event Protocol

The `/chat` endpoint streams these event types in order:
```
session_id        → {type: "session_id", value: "<uuid>"}
agent_start       → {type: "agent_start", agent: "<name>", message: "<status>"}
agent_complete    → {type: "agent_complete", agent: "<name>", message: "<result>"}
approval_required → {type: "approval_required", context: "clarification"|"high_null", ...}
suggested_questions → {type: "suggested_questions", questions: string[]}  // FEAT-01
evidence          → {type: "evidence", data: Evidence[]}  // Phase 3: one per analysis finding
charts            → {type: "charts", data: ChartConfig[]}
export            → {type: "export", format: "pptx", url: "/dashboard/latest/report.pptx"}
insight           → {type: "insight", data: InsightReport}
quality_retry     → {type: "quality_retry", message: "<reason>; Re-running analysis..."}  // FEAT-02
expert_review     → {type: "expert_review", data: ExpertReview}  // quality critic, first run only
error             → {type: "error", message: "<user-readable>"}
done              → {type: "done"}  // always last
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
- **Untrusted data**: dataset text in a prompt goes through `delimit_untrusted()` with `UNTRUSTED_NOTE` in the system prompt; PII is masked once in `build_data_contract` (mask anything else you send, e.g. `mask_row`)
- **LLM calls**: through `llm/client.py` — never a new `AsyncAnthropic`. Contract agents (analysis so far) take `llm: LLMClient` as a parameter; the rest still call `get_client()` until Phase 5. Model is pinned in `settings.llm_model`; traces go to `traces/<date>.jsonl` (metadata, tool names and guardrail rule labels only)
- **Agent contract**: new/refactored agents follow `docs/agent-contract.md` (card, prompt file, context builder, tools, evidence, guardrails)
- **Tests**: `uv run pytest` — offline, no keys (an autouse `RecordedClient` makes real model calls impossible). Append to the `recorded_llm` fixture's `responses` to fake LLM output
- **Eval budget (real money, the user's prepaid credits)**: iterate with `--only <case> --k 1`; one full k=3
  run per suite per phase, at the end; E2E at k=1 except at the phase gate. `--max-usd` (default $2)
  refuses over-budget runs — raise it deliberately, say the estimate to the user first. Phases 0–3 cost
  ~$59 (2026-10-27), mostly repeated full k=3 rounds.
- **Evals**: `uv run python -m evals.run {analysis|e2e} --k 3 [--compare <prev.json>]` — real API spend; cases in `evals/cases/`, reports in `evals/reports/`, baseline in `docs/baselines/phase1.md`. CI (`.github/workflows/ci.yml`) runs ruff/pytest/tsc on push; evals only on manual dispatch

---

## Environment Variables

Required: `ANTHROPIC_API_KEY`, `E2B_API_KEY`, `SUPABASE_URL`, `ENCRYPTION_KEY` (the API refuses to start without the last two).
Frontend (`frontend/.env.local`): `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`
Optional: `DATABASE_URL`, `QDRANT_HOST`, `QDRANT_PORT`, `OLLAMA_HOST`, `FRONTEND_URL`, `APP_ENV`

---

## Common Pitfalls

1. **`_df_parquet` is NOT a Pydantic field** — it's stored via `session.__dict__["_df_parquet"]`. Don't try to serialize it.
2. **Visualization agent has NO LLM** — it's pure Python with deterministic chart selection rules.
3. **The graph entry point is `ingestion`**, not `intake`. This is by design for schema-aware intake.
4. **API keys are in `.env`** — NEVER commit real keys. The `.env` file should be gitignored.
5. **SQL connector blocking calls are wrapped in `asyncio.to_thread`** — every sync SQLAlchemy call in `sql_connector.py` goes through `await asyncio.to_thread(...)`. Don't add raw `engine.connect()` calls inside `async def` methods.
6. **`layout.tsx` is a server component** — `"use client"` lives in `providers.tsx`, not the root layout. Don't move it; metadata exports depend on the layout staying server-side.
