# AnalystAI — CLAUDE.md

> **Purpose**: Canonical project context for AI coding assistants. Read this FIRST to avoid re-reading every file on every session. Reduces token burn by ~80%.

---

## Roadmap & Current Phase (READ THIS SECOND)

The project is executing **`docs/ROADMAP.md`** — the "trustworthy agents" plan (approved 2026-10-27):
every agent gets guardrails, memory context, an agent card (self-knowledge), retrieval tools,
evals/tests, and evidence-backed justification; then an open-source v0.1 release.

**Working rules**
- **Phase 5.7: one domain per session**, < $5 eval spend. **Plan mode is done when the plan file
  checks off every plan-mode step of `docs/ROADMAP.md` Phase 5.7, by name**: expert brief,
  `domain-modeling` term ledger, `codebase-design` on the shared-fix table, red-team, grill. A
  domain prompt that omits a step does not drop it. Say "Domain N (<name>) done" at the end.
- **Domain regression contract (every domain, never skipped; ROADMAP 5.7).** Code as a 30-year
  senior engineer would: one shared definition per rule (fix the root in the shared function,
  then add a guard in `tests/test_one_definition.py` so it can't come back), domain knowledge in
  the pack YAML only, never a role name in shared code. Gates: the rolling golden
  (`tests/test_suite_inputs_unchanged.py`; re-pin only at a domain's end), `tests/test_second_pack.py`,
  `tests/test_replay.py`, every finished domain's $0 suites, **CI green (`gh run list`) before
  any merge**, and a desktop (1440 px) Playwright check with live uploads of this domain AND
  one dataset per finished domain before "Domain N done".
- **Repeating bugs (user, 2026-11-04):** fix the structural cause (delete the second copy of a
  rule), never per dataset. The browser never computes a number. Add the new domain's datasets to
  every cross-domain guard (`PACK_DATASETS` in `tests/test_dashboard_filters.py`, the oracle lists)
  in the pack's commit. Run a $0 senior audit over every domain's answer paths before "done"
  (ROADMAP 5.7 contract).
- **One phase per session.** Finish the phase, verify its "Done when" criteria, post a short review
  note (built / metrics / deviations), update the status table below, then tell the user
  "Phase N done" — they `/clear` and start the next phase in a fresh session.
- Never start the next phase without the user's explicit sign-off.
- "Done" requires a committed test run or eval report showing the number — no metric, not done.
- Measure before changing: every phase must not regress the Phase 1 baseline.

| Phase | Scope | Status |
|---|---|---|
| 0 | Research + gap map + ADRs | ✅ 2026-10-27 |
| 1 | Shared LLM client, traces, eval harness, CI, baseline | ✅ 2026-10-27 — `docs/baselines/phase1.md` |
| 2 | Typed errors, guardrails, PII masking | ✅ 2026-10-27 — `docs/baselines/phase2.md` |
| 3 | Analysis agent as the agent-contract template | ✅ 2026-10-27 — `docs/baselines/phase3.md`, `docs/agent-contract.md` |
| 3.5 | UI & layout redesign | ✅ 2026-10-28 — `docs/baselines/phase3_5.md` |
| 4 | SQL path + YAML semantic layer | ✅ 2026-10-27 — `docs/baselines/phase4.md` |
| 5 | The contract on every agent | ✅ 2026-10-30 — `docs/baselines/phase5_*.md` |
| 5.6 | Durable runs (a) + workspace (b) | ✅ 2026-10-31 — `docs/baselines/phase5_6a.md`, `phase5_6b.md` |
| 5.5 | Domain coverage (8 domains, PHI) | ✅ 2026-11-01 — `docs/baselines/phase5_5.md` |
| 5.7 | Domain depth, one domain per session | 1 retail ✅, 2 banking ✅, 3 healthcare ✅, 4 insurance ✅, 5 HR ✅, 6 SaaS ✅ (`docs/baselines/phase5_7_*.md`) — next **5.7b-1** (structural fixes, 3 sessions: `docs/ROADMAP.md` Phase 5.7b), then **7 IoT** |
| 5.8 | Data sources (MySQL, public Sheets, DuckDB, SQL Server, dlt) | pending |
| 6 | E2E hardening + open-source v0.1 release | pending |

Full per-phase metrics, costs and history: `docs/ROADMAP.md` → "Status log".

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

### Runtime internals
`docs/architecture.md` → "Runtime internals" (the quality-check retry loop, typed errors, multi-turn resume,
durable runs, workspace, approval gates, how the DataFrame flows): read it before touching
`orchestrator/`, a `memory/` store or a graph node.

---

## Key File Map

`docs/architecture.md` → "Key File Map": read it before searching for where an agent, route, store,
connector or UI component lives, and update it in the commit that adds or moves a module.

## SSE Event Protocol

`docs/architecture.md` → "SSE Event Protocol": read it before touching `run_events`, `/runs/{id}/events`,
`useAnalysis.ts` `handle()` or a new event type.

## Code Conventions

- **Python**: 3.11+, `ruff` linter, 100-char line length, `async/await` everywhere
- **Pydantic v2**: all schemas use `BaseModel`, `model_dump_json()`, `model_validate_json()`
- **LLM responses**: always strip markdown fences (```` ```json ... ``` ````) before JSON parsing
- **Error handling**: every agent wraps its core logic in try/except, sets `session.error_message`
- **Frontend**: TypeScript strict, Tailwind v4 with `@import "tailwindcss"`, `"use client"` on all interactive components
- **Frontend colours**: theme tokens only (`bg-card`, `text-muted-foreground`, `bg-primary`, `text-success`, `--chart-N` …,
  defined for light + dark in `globals.css`). Raw palette classes / hex fail `tests/test_frontend_tokens.py`.
  UI checks without keys: `scripts/ui_check/README.md`; the live stack for a domain's end check (production mode, asyncpg, real
  worker): `scripts/live_check/README.md`
- **Untrusted data**: dataset text in a prompt goes through `delimit_untrusted()` with `UNTRUSTED_NOTE` in the system prompt (`tests/test_untrusted_data.py` fails CI for any LLM-calling agent without both); PII is masked once in `build_data_contract` (mask anything else you send with `mask_row(row, pii_columns(contract))`). **PII hard rule**: each run sets `llm.client.pii_values` (chat route, eval harness), ingestion fills it, and `ClaudeClient` tokenizes those values in every request (`[PII_xxxxxxxx]`, restored in the reply). A `pii_tokenized` trace label means a builder leaked: fix the builder
- **LLM calls**: through `llm/client.py` — never a new `AsyncAnthropic`. Contract agents (all but drill-down) take `llm: LLMClient` as a parameter; the rest still call `get_client()` until their Phase 5 PR. Every system prompt is a module constant and is prompt-cached by `ClaudeClient` (keep it constant: per-call text belongs in messages). Model is pinned in `settings.llm_model`; traces go to `traces/<date>.jsonl` (metadata, tool names and guardrail rule labels only)
- **Agent contract**: new/refactored agents follow `docs/agent-contract.md` (card, prompt file, context builder, tools, evidence, guardrails)
- **Review**: reviewers apply `CODING_STANDARDS.md` (judgement calls; mechanical rules are tests/linters)
- **Before pushing**: `.githooks/pre-push` runs ruff, the branch's touched tests + the contract gates, the $0 suites and the frontend checks, ~2-3 min (turn on once: `git config core.hooksPath .githooks`; `PREPUSH_FULL=1` runs the whole suite, `PREPUSH_E2E=1` adds the Playwright e2e). CI (4 shards, ~8 min) is the merge gate
- **Tests, fast**: while iterating run only the touched test files; the golden per dataset (`pytest tests/test_suite_inputs_unchanged.py -k <dataset>`, ~3 s); the full suite once per batch, in parallel and in the background (`uv run pytest -q -n 8`, ~5 min). **Is a golden or domain_diff change only the text you meant?** `python -m evals.snapshot text <ref>` and `scripts/domain_diff.py main --show` print each change once
- **Tests**: `uv run pytest` — offline, no keys (an autouse `RecordedClient` makes real model calls impossible). Append to the `recorded_llm` fixture's `responses` to fake LLM output
- **Evals (real money, the user's prepaid credits)**: before any paid run read `docs/agent-contract.md` →
  "Running evals" (budget rules, `--max-usd`, k choices, SQL eval scratch DB, rescoring) and the
  "Eval cost playbook".

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
