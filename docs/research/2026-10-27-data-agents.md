# How companies built internal data agents and what AnalystAI takes from them

Phase 0 of `docs/ROADMAP.md`. Date: 2026-10-27.
Each claim cites a source. **[P]** marks a primary source (the company's own post, paper or docs).
**[S]** marks secondary coverage, used only where no primary source could be retrieved.

## 1. Systems studied

| System | Architecture | Context retrieval | Semantic layer | Eval method | Reported accuracy / impact | Failure modes reported |
|---|---|---|---|---|---|---|
| **Uber QueryGPT** [P1] | Intent agent → workspace → table agent (user confirms) → column-prune agent → SQL plus explanation | Vector kNN over SQL samples and schemas. v1 took 3 tables and 7 samples. Intent and workspace scoping narrowed the search. | None. Curated "workspaces" of tables and SQL samples instead. | Golden set of real questions with labeled intent, tables and SQL. Two flows: "vanilla" (end to end) and "decoupled" (true intent/tables given). Metrics: intent accuracy, table overlap, runs, returns rows, LLM-scored similarity. | No overall accuracy published. 300 DAU; 78% say it saves time; ~10 min → ~3 min per query. Run-to-run variation of ~5% is treated as noise. | Invented tables and columns; wrong filter literals ("Finished" vs "Completed"); missing partition filters; 40–60K-token schemas; vague prompts |
| **LinkedIn SQL Bot** [P2] | Intent router → Query Writer agent (retrieve K=20 → LLM rank K=7 → write → fix ≤2×) plus Data Finder, Fixer and QA agents (LangGraph) | Knowledge graph built from DataHub metadata, query logs, example queries, wiki jargon, and table certification/deprecation | No formal layer. Closest: column type (metric/dimension/attribute), certification, jargon map. | 133 expert questions (60% have >1 valid answer). Table/column recall, compilation, hallucination rate, 1–5 score from humans and an LLM judge. | Expert review: **53%** correct or close. Score 4+: 48%. Compiles 96%. Schema hallucination **23% → 1%** after validation. | **Wrong filters are the #1 error (24%).** Semantics, not modeling, is the bottleneck. Self-consistency and a query planner did not help. |
| **Pinterest (Querybook)** [S1, S2] | v1: user picks tables → LLM. v2: RAG table selection → LLM picks top-K → user confirms → SQL. | Vector index of table summaries plus query summaries; table docs weighted higher | None stated | Spider (comparable to literature); offline check of table-retrieval hit rate | First-shot acceptance **20% → 40%+**. 35% faster task completion (task mix not controlled). Table hit rate 40% without docs, up to ~90% with docs weighted. | Table search is the hard part; benchmarks understate real difficulty; metadata quality decides retrieval |
| **Airbnb Minerva** [S3, S4] | A metrics platform, not an agent. Metrics are defined once in a reviewed repo and consumed everywhere. | n/a | **Yes.** It is the reference case for "define a metric once": 12,000+ metrics and 4,000+ dimensions. | n/a | n/a | It exists because analysts derived the same metric from different tables and got different numbers. No public write-up of an Airbnb LLM data agent was found. |
| **Snowflake Cortex Analyst** [P3, P4, P5] | Agentic system plus a YAML semantic model/view. Restates the question with its assumptions. | Semantic model; Cortex Search for high-cardinality literal values | **Yes.** YAML: logical tables, dimensions, time dimensions, facts, metrics, filters, synonyms, relationships. | 150 internal BI questions (filter, aggregate, trend). Human-picked sets of acceptable gold SQL with column precision/recall, because exact match fails when several SQL queries are valid. Product evals score result-set equality against verified queries. | **90%+** internal vs 51% for single-prompt GPT-4o. On BIRD, same LLM (Claude 3.5 Sonnet): **57% → 78%** from the semantic model alone. BIRD human accuracy is ~93%. | Implicit date assumptions; LAG over non-consecutive days; filter-before-window; using the model's metric definition instead of the user's; answers without the date/row that makes them checkable |
| **Databricks Genie** [P6, P7] | Space = tables plus instructions plus example SQL plus metric views. "Trusted" label when a parameterized example query or function is reused. | UC metadata, PK/FK, value dictionaries, commented example SQL, text instructions | **Metric views** | 10–20 SME benchmark questions with reference SQL, written *before* building. Result compared to the reference result; questions without one go to manual review. Benchmarks are never used as context. | Case study: **0% → 54% → 77% → 100%** over 6 iterations on 13 questions. Guidance: ≥80% before user acceptance testing. | Cryptic names; misleading column names; failed joins; filter values that don't match user wording; non-standard metrics; undefined terms |
| **Hex Magic** [P8, S5] | Assistant inside notebooks: generates, edits and debugs SQL/Python | Schema plus project code. **Row data is not sent to the model.** | Later: "semantic authoring" of governed models | 2023 beta: none described. Later: an Evals tool using an LLM judge over conversations [S5]. | "Much lower" error rates after moving to GPT-4 (no number) | Says it "will make significant errors"; users must review the code |
| **Julius** [S6, S7] | Writes Python/R → runs it immediately → fixes errors → answers | Uploaded files | None | None public | None independent | Founder: grounding comes from making the model **output code that is executed**, not prose. Reviews: misreads ambiguous queries; quality tracks data cleanliness. |

## 2. Cross-cutting findings

**F1. Context beats model choice.**
- Snowflake measured +21 points from the semantic model alone, with the same LLM [P4].
- LinkedIn found that knowledge-graph quality mattered more than agent modeling [P2].
- Pinterest's table hit rate moved from 40% to ~90% on documentation weight alone [S1].

**F2. Wrong filters and literals are the most common error.**
- LinkedIn: 24%, the #1 category [P2].
- Uber: "Finished" vs "Completed" [P1].
- Genie: value dictionaries [P6].
- Cortex: search for literal values [P3].
- Fix: ground literals with real sample values.

**F3. Validate names against the schema; don't trust the model.**
- LinkedIn's validator cut schema hallucination from 23% to 1% [P2].
- Uber lists invented tables and columns as unsolved [P1].

**F4. Every serious team scores against a golden set. Exact-match SQL is the wrong metric.**
- Snowflake and LinkedIn both note that many SQL queries can be valid [P2, P3].
- Genie and Cortex compare **result sets** [P5, P6].
- Uber separates end-to-end from decoupled component evals [P1].
- Genie writes the benchmark *before* building and never feeds it to the agent [P6].

**F5. Run-to-run noise is real.** Uber treats ~5% variation as noise [P1]. This supports repeated runs (k) and paired comparison.

**F6. Show the work.**
- Cortex restates the question with its assumptions and exposes intermediate values so users can check results [P3].
- Genie labels "Trusted" answers [P7].
- Julius makes the model emit executable code [S6].

**F7. Keep row data away from the model where you can.** Hex sends schema, not rows [P8]. When samples are needed, mask them.

**F8. Accuracy in the wild is modest.**
- LinkedIn: 53% expert-judged [P2].
- Cortex: 90%+ only with a curated semantic model [P3].
- Genie: needed 5 iterations of context work [P6].

## 3. Gap map: every audit gap → finding → decision

The audit gaps come from `docs/ROADMAP.md` "Audit facts" and the 2026-08-23 audit (B1–B4). They were re-verified in code today.

| # | Gap (verified 2026-10-27) | Informed by | Decision | Phase |
|---|---|---|---|---|
| G1 | Single-shot JSON calls, no tool use, inline f-string prompts | F1, F6 | Shared LLM client with tools and structured output; versioned prompt files ([ADR-0001](../adr/0001-agent-contract.md), [ADR-0005](../adr/0005-llm-interface.md)) | 1, 3 |
| G2 | LLM-named columns are never checked; no schema validation | F3 | Column-existence guardrail; strict output schemas ([ADR-0001](../adr/0001-agent-contract.md)) | 2 |
| G3 | Analysis has no AST/import guard before E2B | F6 | AST plus import allowlist before the sandbox ([ADR-0004](../adr/0004-sandbox.md)) | 2 |
| G4 | `_parse_findings` returns empty findings on failure (`agents/analysis_agent.py:262`) | F6 | Typed error. Empty output without an error is a hard-rule failure. | 2 |
| G5 | 11 `except …: pass` blocks in `orchestrator/graph.py` | — | Typed agent errors mapped to the SSE `error` event; grep gate in CI | 2 |
| G6 | Only Insight uses Qdrant; only Intake reads history | F1 | Per-agent context builder with a token budget ([ADR-0001](../adr/0001-agent-contract.md)) | 3, 5 |
| G7 | No evidence, confidence or assumptions on findings | F6 | `Evidence` object on every finding; claims link to evidence IDs | 3 |
| G8 | Quality check only tests lengths (`graph.py:413-423`) | F4, F6 | Evidence checks (grounded numbers, no orphan claims) plus the folded `senior_expert` critic | 2, 5 |
| G9 | No evals, no CI; 55 deterministic tests | F4, F5 | Seeded synthetic golden sets, k=3, paired diff, result-set scoring ([ADR-0002](../adr/0002-eval-method.md)) | 1 |
| G10 | `query_nl` is dead; "semantic layer" is DDL-only; Vanna is given the Groq key (`sql_connector.py:53`) | F1, F2 | YAML semantic layer modeled on Cortex/Genie; revive NL→SQL on the shared client; drop Vanna ([ADR-0003](../adr/0003-semantic-layer.md)) | 4 |
| G11 | Read-only guard checks only the first word (`sql_connector.py:34`) | F3 | Parse with `sqlglot`: exactly one statement, a SELECT/WITH tree with no DML/DDL nodes, plus LIMIT and timeout | 2, 4 |
| G12 | No PII redaction of samples sent to the LLM | F7 | PII detection plus masking; schema-first context, masked samples only | 2 |
| G13 | CrewAI used for one Groq call; `senior_expert` overlaps the quality check; drill-down chart is a stub (`drill_down_agent.py:171`) | — | Remove CrewAI; fold `senior_expert` into the quality check; **finish drill-down or delete it in Phase 2** (decide there with eval data) | 2 |
| G14 | Model ID hardcoded in 7 files; no retries, timeouts, token/cost accounting or traces | F5 | LLM client owns model config, retries and JSONL traces ([ADR-0005](../adr/0005-llm-interface.md)) | 1 |
| G15 | Cell values reach prompts undelimited (prompt-injection surface) | F7 | Data is wrapped and labeled untrusted; injected-cell eval cases | 2 |
| G16 | E2B is the only sandbox; self-hosters may not want it | — | Keep E2B for v0.1 behind a narrow interface; **won't do** a local Docker sandbox before v0.1 ([ADR-0004](../adr/0004-sandbox.md)) | — |
| B1 | Fake `mock_` auth | — | **Closed** by commit `11c4355` (Supabase JWT) | done |
| B2 | Upload IDOR: `csv_connector.py` only checks that the path is under a flat `uploads/` | F7 | Namespace uploads per user and check the owner. Added to Phase 2 (trust-boundary fix, small diff). | 2 |
| B3 | Pipeline runs inside the HTTP request (`chat.py:122`) | — | **Won't do before v0.1.** Acceptable for one team per install. Revisit if E2E p95 latency exceeds the proxy timeout. | — |
| B4 | Silent fallback to `InMemorySessionStore` (`api/main.py:50`) | — | Fail at boot when `APP_ENV=production` and Postgres is unreachable (user story 4). Keep the fallback in dev. | 2 |

## 4. What we deliberately don't copy

- **Multi-agent table pruning** (Uber, LinkedIn) exists for warehouses with 100k+ tables. A startup install has tens of tables, so the semantic layer plus a `get_schema` tool is enough. Revisit if an install exceeds ~200 tables.
- **Knowledge graphs built from query logs** (LinkedIn): startups have no query-log history. The YAML semantic layer is the curated substitute.
- **LLM-judged SQL similarity** (Uber): result-set equality on planted-truth data is cheaper and exact.

## Sources

- [P1] Uber, "QueryGPT – Natural Language to SQL using Generative AI", 2024-09-19. https://www.uber.com/blog/query-gpt/
- [P2] LinkedIn, "Text-to-SQL for Enterprise Data Analytics", arXiv:2507.14372, 2025-07. https://arxiv.org/abs/2507.14372
- [P3] Snowflake, "Cortex Analyst: Evaluating Text-to-SQL Accuracy for Real-World BI Scenarios", 2024-08. https://snowflake.com/en/engineering-blog/cortex-analyst-text-to-sql-accuracy-bi
- [P4] Snowflake, "Agentic Semantic Model Improvement: Elevating Text-to-SQL Performance", 2025-03-31. https://www.snowflake.com/en/blog/engineering/agentic-semantic-model-text-to-sql/
- [P5] Snowflake docs, "Cortex Analyst evaluations". https://docs.snowflake.com/user-guide/snowflake-cortex/cortex-analyst-evaluations ; semantic view YAML spec: https://docs.snowflake.com/en/user-guide/views-semantic/semantic-view-yaml-spec
- [P6] Databricks, "How to build production-ready Genie spaces and build trust along the way", 2026-02. https://www.databricks.com/blog/how-build-production-ready-genie-spaces-and-build-trust-along-way
- [P7] Databricks docs, "Genie" (trusted assets, benchmarks). https://docs.databricks.com/gcp/en/genie/
- [P8] Hex, "Magic public beta", 2023-05-04. https://hex.tech/blog/magic-public-beta/
- [P9] Anthropic, "Structured outputs" and "Define tools". https://platform.claude.com/docs/en/build-with-claude/structured-outputs , https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools
- [S1] ZenML LLMOps DB summary of Pinterest Engineering, "How we built Text-to-SQL at Pinterest" (2024; original Medium post returned 403). https://www.zenml.io/llmops-database/text-to-sql-system-with-rag-enhanced-table-selection
- [S2] Wren AI, "Deep dive into how Pinterest built its text-to-SQL solution". https://getwren.ai/post/deep-dive-into-how-pinterest-built-its-text-to-sql-solution
- [S3] dbt Roundup, "Robert Chang on scaling metrics @ Airbnb". https://roundup.getdbt.com/p/ep-1-robert-chang-on-scaling-metrics
- [S4] Analytics India Magazine, "How did Airbnb achieve metric consistency at scale". https://analyticsindiamag.com/how-did-airbnb-achieve-model-metric-consistency-at-scale/
- [S5] Plushcap summary of Hex blog posts, 2026-08 (Hex Evals). https://www.plushcap.com/companies/hex/blog/summaries/2026/08
- [S6] Cerebral Valley interview with Julius founder. https://cerebralvalley.beehiiv.com/p/julius-transforming-computation-ai
- [S7] Dupple, "Julius AI" review. https://dupple.com/tools/julius-ai
