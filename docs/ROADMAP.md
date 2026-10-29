# Spec: Trustworthy agents — AnalystAI as a self-hosted analyst for startups

## Problem Statement

A startup that wants an analyst today either hires one or pastes data into Claude. AnalystAI is
meant to be the third option: an open-source tool a team installs once, points at its own data,
and trusts. Right now it can't earn that trust:

- An agent's answer can't be checked against the data. Findings carry no evidence, confidence, or
  link to the rows and query that produced them.
- Agents fail silently. When the Analysis agent's generated code fails, it returns empty findings
  instead of an error, and the orchestrator swallows exceptions in about nine places.
- Nothing measures quality. There are no evals, no golden sets, no CI, so "is it getting better?"
  has no answer, and a prompt change can quietly make things worse.
- Agents can name columns that don't exist, run unchecked generated code, and send raw sample
  values (possibly PII) to third-party LLMs.
- The data path doesn't match where startup data lives. The tool is built around CSV upload,
  while real data sits in Postgres/BigQuery/Snowflake. The NL→SQL path exists but is never called.
- Each agent sees a different, ad-hoc slice of context. Only Insight uses long-term memory, and
  no agent knows its own scope or limits.

## Solution

Make every agent follow one **agent contract** and prove it with evals, building it end to end on
the **Analysis agent** first and then rolling the pattern out to the others one at a time.

Each agent will have:
1. **Guardrails**: validated input, schema-validated output, hard invariants (no write SQL, no
   made-up columns, no numbers that aren't in the evidence), and a loud failure instead of a silent
   empty result.
2. **Memory context**: an explicit, typed context bundle (session state, recent conversation, and
   relevant long-term memories from earlier analyses of the same dataset).
3. **Self-knowledge**: a versioned agent card that states scope, inputs, outputs, known limits and
   refusal rules. It is injected into the prompt and shown in the UI.
4. **Retrieval tools**: real tool calling (schema lookup, semantic-layer metric lookup, sample
   rows, past insights) instead of one giant prompt.
5. **Evals and tests**: deterministic tests on recorded LLM responses, plus a golden eval set per
   agent with a pass-rate target and must-pass hard rules enforced in CI.
6. **Justification**: every finding carries its evidence (the code or SQL that ran, the result it
   came from, a confidence level, and the assumptions made), and the UI shows it.

Alongside this, SQL warehouses become the primary data path, backed by a small YAML semantic
layer. CSV stays as the quick path. The first deliverable is a published research write-up and
gap map that grounds these choices in how companies built their internal data agents.

## User Stories

**Startup operator (installs and runs it)**
1. As an operator, I want to install AnalystAI with my own API keys, so that my data never passes through a third party's hosted service.
2. As an operator, I want to connect my Postgres, BigQuery or Snowflake warehouse read-only, so that the analyst works on my real data, not exports.
3. As an operator, I want to describe my key metrics, tables and joins in one YAML file, so that the agents use my definitions of "revenue" or "active user" instead of guessing.
4. As an operator, I want the app to refuse to start when required config is missing, so that misconfiguration fails at boot, not as confusing runtime errors.
5. As an operator, I want any SQL the tool generates to be provably read-only, so that it can never modify or delete my data.
6. As an operator, I want PII columns detected and masked before sample values go to an LLM, so that I don't leak customer data to a model provider.
7. As an operator, I want to see each agent's eval scores for the version I'm running, so that I know how much to trust it before rolling it out to my team.
8. As an operator, I want to swap the LLM provider later (e.g. a local model) through one config, so that I'm not locked in, and the evals tell me what I lose.

**Business user (asks questions)**
9. As a business user, I want to ask a question in plain English and get an answer with a number and a one-line explanation, so that I don't need SQL.
10. As a business user, I want every number in an answer to show where it came from (query, rows, filter), so that I can trust it in a board meeting.
11. As a business user, I want a confidence level and the assumptions made on every finding, so that I know which conclusions are solid and which are tentative.
12. As a business user, I want the tool to tell me when it can't answer and why, so that I'm not handed a confident wrong answer.
13. As a business user, I want the tool to ask me a clarifying question when my question is ambiguous (e.g. "revenue: gross or net?"), so that it answers the question I meant.
14. As a business user, I want follow-up questions to remember what we were just discussing, so that I don't have to repeat context.
15. As a business user, I want the analyst to remember past findings about the same dataset, so that it can say "this is up from last month's analysis".
16. As a business user, I want to see each agent's role and limits in the UI, so that I understand what the tool can and can't do.
17. As a business user, I want a clear error message when something fails instead of an empty report, so that I know to retry or rephrase.

**Data-literate reviewer (checks the work)**
18. As a reviewer, I want to see the exact code or SQL that produced each finding, so that I can reproduce it.
19. As a reviewer, I want each statistical claim to include its test, sample size and p-value, so that I can judge its validity.
20. As a reviewer, I want the tool to flag when a finding is based on few rows or heavily imputed data, so that I can discount it.
21. As a reviewer, I want findings to be checked against the evidence before they reach me, so that the narrative never contradicts the numbers.

**Project developer (you)**
22. As the developer, I want one shared LLM client used by every agent, so that I can swap in recorded responses for tests and real Claude for evals.
23. As the developer, I want each agent's prompt stored as a versioned file, so that prompt changes are visible in diffs and tied to eval results.
24. As the developer, I want a golden eval set per agent with a pass-rate threshold, so that "good enough" is a number and not a feeling.
25. As the developer, I want hard rules (no write SQL, no made-up columns, no unsupported numbers) to count as must-pass checks, so that one violation fails the build.
26. As the developer, I want CI to run deterministic tests on every push and real-model evals on demand or nightly, so that regressions are caught without spending on every commit.
27. As the developer, I want an eval report showing per-case pass/fail and the diff from the last run, so that I can see exactly what a prompt change broke.
28. As the developer, I want agent failures to raise typed errors that the graph turns into user-facing messages, so that nothing is swallowed silently.
29. As the developer, I want each agent's context assembled in one place with a token budget, so that context is explicit and doesn't silently overflow.
30. As the developer, I want a written template ("the agent contract") proven on one agent, so that applying it to the other six is mechanical.
31. As the developer, I want a research write-up of how companies built internal data agents, so that my design choices rest on what has worked in practice.
32. As the developer, I want generated Python to pass a static check (AST, import allowlist) before it reaches the sandbox, so that obviously bad code is rejected cheaply and with a reason.
33. As the developer, I want retries with a cap and a reason logged, so that transient failures recover and permanent ones surface.

## Implementation Decisions

**Settled in grilling (2026-10-27)**
- Product shape: open-source, self-hosted, one team per install, users bring their own keys. Hosted multi-tenant is out of scope for now.
- "100% accurate" means per-agent golden eval sets with pass-rate targets, plus must-pass hard rules. CI fails on any regression below target.
- Stack stays as is: Python/FastAPI backend, Next.js frontend. No rewrite.
- Depth before breadth: make the existing agents trustworthy before adding new agents or integrations.
- Primary data path: SQL warehouses plus a YAML semantic layer. CSV remains the quick path.
- Claude by default, behind one thin LLM interface. A second provider only after evals exist.
- Order: research and gap map, then the Analysis agent end to end as the template, then the rest one at a time.

**Modules**
- **LLM client (new, shared):** one async interface (`complete` with optional tools and a structured-output schema). Implementations: Claude (production and evals) and a recorded/fake client (tests). It replaces the per-agent `AsyncAnthropic` instances. It owns retries with backoff, `stop_reason` and refusal handling, and token/latency logging.
- **Agent contract (new, small):** each agent declares an agent card (name, version, scope, inputs, outputs, limits, refusal rules), a versioned prompt file, a context builder, its tools, an output schema, and guardrail checks. Agents stay plain async functions; this is a convention plus a few helpers, not a framework.
- **Guardrails module (new):** reusable checks, each returning a pass or a reason. They cover:
  - columns referenced exist in the DataContract;
  - SQL is a single read-only statement, checked by parsing rather than by the first word;
  - generated Python passes an AST/import allowlist;
  - every number in narrative text appears in the evidence;
  - PII column detection and masking of samples.
  Hard-rule failures raise typed errors; soft ones lower confidence.
- **Context builder (per agent, shared helpers):** assembles a typed context bundle from session state, the last N turns, semantic-layer entries and long-term memories, within a token budget.
- **Retrieval tools:** exposed through real tool calling: `get_schema`, `get_metric_definition` (semantic layer), `sample_rows` (PII-masked), `search_past_insights` (Qdrant, scoped to user and dataset), `run_sql` (read-only, guarded) for the SQL path.
- **Semantic layer (new):** a YAML file loaded at startup and validated with Pydantic (metrics, tables, joins, synonyms). It is the retrieval source for metric definitions.
- **Justification schema change:** findings gain an `Evidence` object containing the code or SQL, a result excerpt, row count, confidence (`high` / `medium` / `low` with reason) and assumptions. Insight output links each claim to the evidence IDs it rests on.
- **Error handling:** the swallowing `except Exception: pass` blocks in the graph are replaced by typed agent errors mapped to the existing SSE `error` event. An empty result after a failure is no longer allowed.
- **SQL path:** the existing NL→SQL code is revived and wired into ingestion and intake for SQL connectors, grounded in the semantic layer, and run through the read-only guard. The Vanna dependency is dropped unless evals show it helps (it is currently wired to the wrong key).
- **Quality check:** upgraded from length checks to evidence checks (claims supported, no orphan numbers), reusing the guardrails module.
- **Frontend:** a finding card shows an evidence drawer (code/SQL, rows, confidence, assumptions); an agent-info panel renders the agent cards; an eval-scores page reads the latest eval report.

**Analysis agent (the template, built first)**
- Input: session (task, DataContract, cleaning audit). Output: `AnalysisFindings` with evidence on every finding.
- Flow: build context → model plans and calls tools (schema, metric definitions, samples) → generates code → AST guard → E2B → parse into the schema → evidence check → on failure, at most one guided retry with the error → otherwise raise a typed error.
- Hard rules (must pass every time):
  - never references a nonexistent column;
  - never reports a number not present in its own execution output;
  - never returns empty findings without an error;
  - code always passes the allowlist.

## Testing Decisions

- **Seams (confirmed with user):**
  1. Each agent's public entry function (e.g. `run_analysis_agent(session) -> AnalysisFindings`).
  2. The shared LLM client, which tests swap for recorded responses and evals run against real Claude.
  Graph-level end-to-end tests come later through the same two seams.
- **What makes a good test here:** it asserts external behavior at the seam (output schema, evidence present, hard rules held, typed error on bad input). It never asserts prompt wording or private helpers. Recorded LLM responses include bad outputs (malformed JSON, invented column, refusal) to prove the guardrails catch them.
- **Deterministic tests (every push, no API spend):** per agent, the entry function with a fake LLM and fixture sessions. Guardrails module tests (pure functions). Semantic-layer loader tests.
- **Evals (nightly or on demand, real model):** per agent, a golden set of cases. Each case is a fixture dataset plus a question plus expected outputs (exact values where possible, otherwise rubric checks). The first set is 40 cases for Analysis, built from the seeded synthetic generator (planted truths — see Senior review #2). The report records pass rate, hard-rule violations, cost and latency, and the diff against the previous run. Starting targets are set from the first baseline run, never below it; hard rules are 100%.
- **Prior art:** the existing pytest suite (`asyncio_mode = "auto"`), especially the async session-store and intent-handler tests, which already test pure behavior with fixtures.
- **CI:** a GitHub Actions workflow running ruff, pytest and the frontend type-check on push. The eval job runs manually or nightly with secrets.

## Out of Scope

- Hosted multi-tenant SaaS, billing, orgs/teams.
- Slack, Telegram, WhatsApp bots; scheduled or emailed reports (next phase, after evals are green).
- New agents beyond the existing set.
- Rewriting the backend in another language.
- Supporting a second LLM provider (only the interface is in scope).
- Fine-tuning models.

## Further Notes

**Issue tracker:** no tracker or triage labels are configured for this repo. Run `/setup-matt-pocock-skills` to configure one, or publish this spec as a GitHub issue labelled `ready-for-agent` on `nikhilreddy00/AnalystAI` (the GitHub MCP server is failing auth this session, so `gh` would be used).

---

# Senior review (25-yr engineer + analyst lens) — what changed and why

**Verdict on the first draft: ~70% confidence.** It was the right direction but had the wrong order
and soft exit criteria. Ten changes bring it to ~90%:

1. **Measure before you change anything.** The draft built evals inside the Analysis refactor, so
   there was no "before" number. The eval harness and a baseline of the *current* pipeline now come
   first (Phase 1). Every later phase must beat that baseline.
2. **Synthetic datasets with planted truths, not public datasets.** A seeded generator creates
   tables where the answers are known exactly: a planted 12% MoM drop in segment B, a correlation
   that is really a confounder, a null-effect column, duplicate rows, a join that fans out, mixed
   date formats, PII columns, and a cell containing "ignore previous instructions". The analyst's
   domain traps (Simpson's paradox, correlation vs causation, wrong date grain, survivorship bias,
   double counting) become test cases. Exact ground truth means no judge is needed for most cases.
3. **Statistics on the evals themselves.** 50 cases at an 80% pass rate gives a ±11% confidence
   interval, too wide to see a 5-point regression. So: compare runs per case (paired: which cases
   flipped), run each case k=3 at temperature 0 and report the mean and how often cases flip, and
   grow the Analysis set to 100+ cases before claiming accuracy.
4. **An end-to-end eval from day one** (20 questions). Per-agent scores can all go up while the
   pipeline gets worse, because errors compound between agents. This is the number users feel.
5. **LLM-as-judge only where exact checks are impossible** (narrative quality), and only after
   calibrating it: the judge must agree with your own labels on 30 cases ≥ 85% of the time.
6. **Treat data as hostile.** Cell values and column names can carry prompt injection. Data goes to
   the model delimited and labelled as untrusted, and eval cases include injected cells.
7. **Observability before tuning.** You can't fix what you can't see. Each run writes a JSONL trace
   per agent: prompt version, model, tokens, tool calls, latency, guardrail verdicts. That's
   enough; a hosted tracing service (Langfuse) is optional later.
8. **Cut before you add.**
   - Remove CrewAI (used only to make one Groq call in ingestion) by moving that call onto the
     shared client.
   - Fold `senior_expert` into the quality check as its critic.
   - Either finish drill-down or remove it; don't ship a stub with `data=[]`.
   Fewer agents, each one excellent.
9. **Budgets are metrics too.** Track cost per question and p95 latency. An "accurate" agent that
   costs $2 and 4 minutes per question fails for a startup.
10. **Open-source readiness is a phase, not an afterthought.** It covers quickstart, docs, the
    agent-contract guide, a public eval scoreboard, versioned releases, and a security policy.

**Remaining risk (the last ~10%):**
- Eval quality (bad cases give false confidence). Mitigation: you review every golden case.
- Self-hosters may not want E2B. Note it, and consider a local Docker sandbox after v0.1.
- Model drift. Mitigation: pin the model ID and re-run evals on any model change.
- Solo-developer bandwidth. Mitigation: phases are independently shippable.

---

# Phased execution plan with exit criteria

Every phase ends with: a short review note (what was built, the metrics, what changed vs. the
plan), a green CI run, and **your explicit sign-off** before the next phase starts.

### Phase 0: Research and decisions (time-boxed: 2 days)
- **Work:**
  - Study the internal data agents at Uber (QueryGPT), LinkedIn (SQL Bot), Pinterest
    (text-to-SQL), Airbnb, and Snowflake Cortex Analyst, plus Databricks Genie, Hex Magic and Julius.
  - For each, capture architecture, how context is retrieved, use of a semantic layer, the eval
    method, reported accuracy, and failure modes.
  - Map every finding to an audit gap.
  - Write 3–5 short ADRs (agent contract, eval method, semantic layer format, sandbox, LLM
    interface).
- **Output:** a published research page plus the ADRs in the repo.
- **Done when:** every audit gap has a decision or an explicit "won't do", every claim cites a
  source, and you've signed off.

### Phase 1: Measurement first
- **Work:**
  - Shared LLM client, with all agents moved onto it with no behavior change.
  - JSONL trace logging.
  - Seeded synthetic data generator (domain traps above).
  - Eval harness: per-case runner, k repeats, paired diff report, cost and latency.
  - Fake-LLM test harness.
  - CI on push (ruff, pytest, tsc). Evals run on demand.
  - **Baseline** of the current Analysis agent (40 cases) and E2E (20 questions).
- **Done when:**
  - pytest passes offline with no API keys;
  - two consecutive eval runs agree within ±3 points;
  - the baseline report is committed;
  - no agent behavior has changed (the existing 55 tests stay green).

### Phase 2: Reliability foundations
- **Work:**
  - Typed agent errors, and removing every swallowed exception in the graph.
  - Guardrails module: column existence, a parsed read-only SQL check, an AST/import allowlist,
    numeric grounding with a tolerance rule, PII detection and masking, and delimiting untrusted
    data.
  - Remove CrewAI, fold `senior_expert` into the quality check, and resolve drill-down.
- **Done when:**
  - a fault-injection suite feeds each bad response (malformed JSON, invented column, refusal,
    `DELETE`, `import os`, injected cell, timeout), and each one yields its typed error or block,
    never empty output;
  - a grep check finds zero bare `except ...: pass` in the orchestrator;
  - E2E is not below baseline.

### Phase 3: Analysis agent (the template)
- **Work, in this order:** agent card, then **write the eval cases first**, then refactor onto the
  contract (prompt file, context builder, tools, evidence schema), then guardrails, then iterate on
  the prompt against the eval, then the frontend evidence drawer.
- **Done when (measured over k=3 runs):**
  - hard rules hold 100%;
  - numeric accuracy is ≥ 85% and above baseline;
  - planted-insight recall is ≥ 80%;
  - false-finding rate is ≤ 5%;
  - cost and p95 latency are within budget (set from the baseline, e.g. ≤ 1.2× it);
  - the manual UI checklist passes;
  - "the agent contract" guide is written from this experience.

### Phase 3.5: UI and layout (added 2026-10-28 at the user's request)
- **Why:** the user finds the current UI and layout unappealing, and no other phase covers it.
  It is the first thing an open-source user sees (Phase 6 goal).
- **Work, in this order:**
  1. Audit every screen (landing, auth, chat, dashboard) with Playwright screenshots, using the
     mocked-auth method in memory.
  2. Agree on one design direction with the user before writing code.
  3. Rebuild layout and navigation, the chat flow, the insight/evidence panels and the charts.
     Use the installed frontend-design, shadcn and dataviz skills.
- **Done when:**
  - the user signs off on the screenshots;
  - light and dark themes both work;
  - every page works at phone width;
  - keyboard and screen-reader basics pass;
  - tsc and lint are clean, and CI is green.
- **Costs no API credits:** no evals change.

### Phase 4: SQL path and semantic layer
- **Work:**
  - YAML semantic layer and its loader.
  - Revive NL→SQL on the shared client and drop Vanna.
  - `get_metric_definition` and `run_sql` tools.
  - A Postgres service container in CI.
  - A SQL eval set of 50 questions on a seeded star schema.
- **Done when:**
  - execution accuracy (results match the expected result set) is ≥ 85%;
  - questions use the semantic-layer definition of a metric when one exists ≥ 95% of the time;
  - every write or DDL attempt in the attack suite is blocked (100%);
  - fan-out trap cases pass.

### Phase 5: Roll out the contract, one agent per PR
Order: Intake, then Visualization, then Cleaning, then Insight, then Quality check, then Ingestion.
(Visualization moved up on 2026-10-28: it only needs Phase 3's findings and evidence.)
Progress: **Intake ✅ 2026-10-28** (`docs/baselines/phase5_intake.md`; also closed the Phase 4
SQL follow-up item via shared conversation memory, and added value-based date detection);
**Visualization ✅ 2026-10-28** (`docs/baselines/phase5_visualization.md`; also fixed the high-null
approval overwriting the user's question, and put targeted, chart-request and drill-down charts under
the same policy);
**Cleaning ✅ 2026-10-28** (`docs/baselines/phase5_cleaning.md`; deterministic, nothing imputed; also
made the high-null gate honour Drop/Cancel and delimited the audit in the insight prompt);
**Insight ✅ 2026-10-29** (`docs/baselines/phase5_insight.md`; the model writes only claims that cite
evidence, code holds grounding, dates and jargon by repair and writes the analyst view and alerts; a
calibrated Haiku judge measures faithfulness; also scoped memory to user + dataset and measured the
targeted-path cleaning gap for the Ingestion PR).
Each PR runs the same 7 steps:
1. agent card;
2. eval cases first;
3. baseline;
4. refactor onto the contract;
5. guardrails and fault tests;
6. iterate to target;
7. PR with the eval diff report.

Each PR is done when its row in the table below is met and E2E is not below its previous value.

#### Visualization: chart trust (added 2026-10-28 at the user's request)
- **Why:** at the Phase 3.5 sign-off the user asked how an analyst can trust the charts. Today
  `auto_generate_charts` picks charts from column types only, never from the question or the
  findings. On the sales overview the headline ("Segment B revenue falls while spend rises") had
  no chart behind it, 3 of 6 charts were row counts, the scatter put the outcome (revenue) on x,
  and `units` was charted as a category.
- **Work:**
  1. **Chart per finding.** Each headline finding gets the chart that shows it, chosen from the
     finding's kind: trend → line split by the relevant segment; segment comparison → bars of the
     metric (not a row count) with uncertainty; correlation → scatter with the driver on x;
     anomaly → distribution with the flagged points marked. Row-count charts move to the Data tab.
  2. **"Why this chart" on every chart:** the rule that picked it, the columns, the aggregation
     (mean/sum/count), n rows, and a link to its evidence item (`Evidence.id`).
  3. **Chart lint** that blocks known anti-patterns: category axis for numeric data, pie > 5
     slices, dual y-axes, truncated bar baselines, > 8 series, outcome variable on x.
  4. **Analyst-labelled chart eval set:** ~20 dataset/question cases, each with the expected chart
     type, variables and aggregation. Deterministic, so no API spend.
  5. **User override:** swap chart type or variables in the UI; overrides are logged as feedback.
  6. **KPI tiles** get the same treatment: each says how it was computed, and a check confirms its
     numbers match the report's.
  7. The backend sends `colors: []` for generic charts (the frontend currently filters the Tableau
     cycle); keep the PPTX export working.

### Phase 5.5: Domain coverage (added 2026-10-27 at the user's request)
- **Why:** the goal is an analyst any company can use, in any domain. At Phase 4 the evals cover
  sales-style data only (53 of 66 analysis/E2E cases use one `sales` table; the SQL eval adds
  banking). Nothing tests healthcare, insurance, HR, SaaS, IoT or logistics, so no claim of
  "works for every domain" is backed by a number.
- **Work:**
  1. Synthetic datasets with planted truths for ~8 domains: retail, banking, healthcare,
     insurance, HR, SaaS/product analytics, manufacturing/IoT sensors, logistics. Each plants a
     domain trap (e.g. semi-additive balances, readmission windows, censored tenure, sensor drift).
  2. Run them through the analysis and E2E suites; a domain is "supported" only once it passes.
  3. PHI masking for healthcare (record numbers, diagnosis codes, dates of birth, small cells).
     Until then the project makes no HIPAA claim.
  4. Generic KPI and chart labels (today's heuristics match English, sales-style column names).
  5. A sample semantic layer per supported domain; domain meaning lives in YAML, not in code.
- **Done when:** each listed domain passes its suite at the analysis/E2E targets, PII/PHI recall
  ≥ 95% on the healthcare set, and the README lists exactly the domains that passed.

### Phase 6: End-to-end hardening and open-source release (v0.1)
- **Work:**
  - Raise the E2E eval to 50 questions.
  - Public eval scoreboard page in the app.
  - README quickstart, CONTRIBUTING (including how to add an agent and an eval case),
    SECURITY.md, CHANGELOG, tagged release, sample data and a sample semantic layer.
- **Done when:**
  - E2E answer correctness is ≥ 80% and hard rules hold 100%;
  - a fresh clone on a clean machine reaches a first answer in ≤ 15 minutes, following only the
    README;
  - a second person (or a clean VM run) confirms it.

---

# Per-agent breakdown (targets are provisional and get re-set from baselines, never below them)

| Agent | Guardrails | Memory/context | Tools | Eval set (planted truths) | Metrics → target |
|---|---|---|---|---|---|
| **Ingestion** | PII detection and masking; type inference is deterministic | Connector config and profile | none (deterministic, plus 1 LLM summary call) | 20 tables: mixed types, PII, encodings | type inference ≥ 98%; PII recall ≥ 95%; **hard:** unmasked PII never reaches an LLM |
| **Intake** | Columns must exist; intent enum; clarification policy | Last N turns, DataContract, semantic layer | `get_schema`, `get_metric_definition` | 60 questions: clear, ambiguous, out-of-scope, injected | intent accuracy ≥ 90%; column mapping ≥ 95%; asks when ambiguous ≥ 85%; doesn't ask when clear ≥ 90%; **hard:** 0 invented columns |
| **Cleaning** | Allowed strategies only; never impute ID or target columns; every row change audited | Profile and task | `sample_rows` (masked) | 25 dirty tables: dups, nulls, bad types, outliers | planted-issue recall ≥ 95%; over-cleaning ≤ 5%; **hard:** row-count changes 100% logged |
| **Analysis** | AST/import allowlist; columns exist; numeric grounding; no empty output without an error | Task, contract, audit, past insights | `get_schema`, `get_metric_definition`, `sample_rows`, `search_past_insights` | 100+ cases: aggregation, trend, segment, correlation, Simpson's paradox, null effect | numeric ≥ 85%; insight recall ≥ 80%; false findings ≤ 5%; **hard** rules 100% |
| **SQL (in Ingestion/Intake)** | Parsed read-only single statement; LIMIT; timeout | Semantic layer and schema | `run_sql`, `get_metric_definition` | 50 questions plus an attack suite | execution accuracy ≥ 85%; metric-definition use ≥ 95%; **hard:** 0 writes |
| **Visualization** | Deterministic; chart lint (no empty-data charts, labeled axes, no anti-patterns); every chart cites a finding/evidence ID | Findings, evidence, question | n/a | ~20 analyst-labelled chart cases plus property/snapshot tests | right chart (type + variables + aggregation) ≥ 90%; headline findings with a supporting chart ≥ 95%; **hard:** lint violations 0, charts without a rationale 0 |
| **Insight** | Every number grounded in the evidence; jargon sanitizer; every claim links to evidence IDs | Findings, audit, past insights | `search_past_insights` | 40 findings bundles, including contradictory ones | grounding 100% (**hard**); calibrated-judge faithfulness ≥ 90%; executive view has no jargon (100%) |
| **Quality check (+critic)** | Deterministic checks plus an LLM critic | Report and evidence | n/a | 40 reports, half with seeded defects | defect recall ≥ 90%; false alarms ≤ 10% |
| **End-to-end** | All of the above | — | — | 20 questions, growing to 50 | answer correctness ≥ 80%; cost and p95 latency within budget |

**Critical files** (current, may move): `agents/analysis_agent.py`, `agents/*_agent.py` (module-level `_client`), `orchestrator/state.py` (schemas: Evidence), `orchestrator/graph.py` (swallowed exceptions, quality check at ~L401), `connectors/sql_connector.py` (`query_nl`, `_assert_single_select`), `memory/vector_store.py`, `sandbox/executor.py`, `tests/`, `frontend/src/components/InsightPanel.tsx`.

**Reuse, don't rebuild:** `strip_markdown_fences`, E2B executor, `VectorStore.retrieve_similar/store_insight`, the existing human-approval gate (for clarification), the SSE `error` event, deterministic `agents/data_cleaning.py` / `anomaly_detector.py`.

**Verification:** see each phase's "Done when". The rule throughout is that nothing is "done"
without a committed eval report or test run that shows the number.

## Audit facts (2026-10-27, basis for the gaps above)
- All LLM agents make single-shot JSON calls via raw AsyncAnthropic. Ingestion uses Groq via CrewAI. There is no tool_use, and prompts are inline f-strings.
- No schema validation beyond enum coercion. LLM-named columns are never checked.
- Analysis: no AST/import guard; a parse failure gives silent empty findings.
- Qdrant is used by Insight only. Only Intake reads conversation history.
- Partial justification only; no confidence or evidence links. The quality check is length-only.
- 6 deterministic unit test files (~55 tests); no LLM evals, CI or frontend tests.
- `query_nl` is dead; the DDL-only "semantic layer"; Vanna is given the Groq key; the single-select guard checks the first word only.
- No PII redaction; ~9 swallowed exceptions in the graph; drill-down chart is a stub.
