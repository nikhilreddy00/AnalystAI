# Phase 3 review: the analysis agent as the agent-contract template

Model `claude-sonnet-4-6`, temperature 0, k=3, 2026-10-27, commit `3903e93`.
- Final reports:
  - `evals/reports/analysis-20261027T215932Z.*`, paired against Phase 2 (`analysis-20261027T194402Z`);
  - `evals/reports/e2e-20261027T220630Z.*`, paired against `e2e-20261027T194402Z`.
- The before-picture for the new metrics comes from `analysis-20261027T204223Z`: the v2 agent scored with the new scorer, k=1, 46 cases.

## Numbers

| metric | target | v2 on new scorer (k=1) | **Phase 3 final (k=3)** |
|---|---|---|---|
| hard-rule violations (analysis / e2e) | 0 | 5 / — | **0 / 0** |
| numeric accuracy | ≥ 85%, ≥ baseline | 73.2% | **97.6%** |
| pass rate, 40 common cases | ≥ Phase 2 (87.5%) | — | **97.5%** (+10.0 pts, 0 broke) |
| pass rate, all 46 cases | | 76.1% | **97.8%** (case majority 100%) |
| planted-insight recall | ≥ 80% | 57.7% | **100%** |
| false-finding rate | ≤ 5% | 0.0% (1,395 claims) | **0.41%** (4,620 claims) |
| cost / question | ≤ $0.053 | $0.031 | **$0.023** |
| p95 latency | ≤ 45s | 33.2s | **27.1s** (p50 18.0s) |
| runs with a guided retry | | — | **0 / 138** (9 had prose rewritten) |
| **E2E pass rate** | ≥ 45.0% | | **45.0%** (0 fixed, 0 broke), p95 139s, unchanged (cleaning) |

**Read the +10 points honestly.** All of it is the five `trend-*` cases. They failed in Phases 1 and 2 because of a schema/scorer bug, not the agent:
- `trends: list[TrendResult | dict]` coerced every dict into a model object;
- the scorer only looked at dicts, so it never saw a trend.

The trend-1 dump shows the v2 agent was already reporting the correct −3.11%. The fix is in `546e7fe`.

The agent's own gains show in the new metrics:
- recall went from 57.7% to 100%;
- the stratified Simpson and per-segment trend insights are now found;
- null effects are reported;
- hard-rule hits went from 5 to 0.

## Done-when
- **Hard rules hold 100%:** 0 violations in both suites, over 198 runs.
- **Numeric accuracy ≥ 85% and above baseline:** 97.6%, with 0 broke against Phase 2.
- **Recall ≥ 80%:** 100%.
- **False findings ≤ 5%:** 0.41%.
- **Cost and p95 within budget:** $0.023 and 27.1s.
- **Manual UI checklist:** passed, with one limit (see the UI check section).
- **The agent-contract guide:** written, `docs/agent-contract.md`.
- **CI:** green on every pushed commit. Merge details are at the end of the session report.

## What was built
- **Carry-overs:**
  - PII name detection is fail-closed. Any `name` column is PII unless a thing-noun qualifies it; camelCase is split.
  - Column names are delimited in the analysis and intake prompts, and intake's main system prompt now carries `UNTRUSTED_NOTE`.
- **The contract in `agents/analysis_agent.py`:**
  - `ANALYSIS_CARD`;
  - `prompts/analysis/v3…v7.md`, where v3 is v2 verbatim;
  - `build_context` → `AnalysisContext`, with a delimited schema, masked samples and cleaning notes;
  - `run_analysis_agent(session, llm)`;
  - tools `get_schema` and `sample_rows` (masked, delimited, ≤ 3 rounds);
  - `Evidence` on every finding.
- **`llm/client.py`:** `tools`, `tool_choice`, `tool_calls` and `content`. Traces record tool names and guardrail rule labels only.
- **Built-in standard statistics** (`_build_standard_code`), for the requested types only:
  - target-pair r;
  - calendar-month MoM, overall and per segment value, which replaces the model's MoM (rates and flags use the month mean);
  - the joint two-column breakdown.
- **Hard rules after execution:**
  - non-empty and finding-columns-exist get one guided retry, then raise;
  - grounding is enforced by rewriting the prose from the stored values (`ground_prose`).
- **Evidence UI:**
  - an SSE `evidence` event, also in the session-results snapshot;
  - `EvidencePanel.tsx`, a list plus a native `<dialog>` drawer showing rows, confidence reason, assumptions, result and code.
- **Evals:**
  - checks `pair_reported`, `segment_trend` and `simpson_reversal`;
  - `planted` flags, `false_findings`, and the `numbers_grounded` hard rule;
  - 6 new cases;
  - `compare` diffs common cases and lists new ones;
  - the retry and rewrite counts.
- **Security fixes** found by commit review, all in the shared guardrails:
  - integer and float PII is now masked;
  - every spelling of the close tag is neutralized.

## Scorer changes (each also fixed in the product first where one applied)
1. **null_effect now requires `pair_reported`.** A null result must be stated.
2. **`trends` typed (`546e7fe`).** A product schema fix plus scorer reads; this is the +10 points above.
3. **`non_empty_or_error` counts `eda_summary` for EDA questions.** The agent got the same rule first.
4. **New metrics and cases.** These are additions; no existing check got easier.

## Deviations and things to know
- **Tools are offered only on the guided retry.** In smoke runs, first-attempt lookups were always redundant (+5s, +$0.02). With 0 retries in the final run, the tools never ran there; they're exercised by the offline tests. Revisit if wide or ambiguous tables arrive.
- **Tools deferred:**
  - `get_metric_definition` waits for the Phase 4 semantic layer.
  - `search_past_insights`: Qdrant scopes by `user_id`, not dataset, it isn't available on this machine, and no eval measures it. It moves to Phase 5 Insight.
- **Grounding is a rewrite, not a retry.** In 9 of 138 runs (mostly null_effect test interpretations), the model's sentence was replaced with `statistic=…, p=…: (not) significant at 0.05`. Plainer prose, same rule.
- **Remaining false findings (0.41%) come from 3 of 138 runs:**
  - 2 runs (simpson-2/3) labelled stratified groups under a single column, such as `control (dept=Easy)` under `group`. The prompt states the naming. The next step is a hard rule on group names.
  - 1 run (segment-4) reported a wrong mean for `units` by region (4.72 vs 4.54), most likely a filter the model applied. Evidence makes it visible, but nothing catches it yet.
- **The UI check used a real browser, the real app code and a real analysis run's SSE stream**, but with Supabase auth and `/chat` mocked:
  - `frontend/.env.local` has no Supabase keys;
  - the API has no dev bypass, by design.

  **Still to do by hand:** one logged-in run against the live backend.
- **E2E stayed at 45.0%, with the same 11 failures** (cleaning collapses e2e-01…05; targeted answers name the wrong segment). Both are Phase 5.
- **E2E round 1 had one `no_injection_echo` hit.**
  - Cause: the built-in breakdown turned the injected `notes` cell into a group name, and the insight prompt didn't delimit segment names.
  - Fixed in `3903e93`; round 2 has 0 hits.
- **Two final rounds.** Round 1 (`*-214125Z`, `*-214916Z`, committed) missed the p95 (52.4s) and had the echo. Both were fixed in the product and the full suites were rerun.
- **Spend:** $20.61 against a ~$12 estimate:
  - the extra final round;
  - a final run stopped once a pre-merge bug was found (`discount` MoM summed a rate; per-segment trend evidence claimed all rows);
  - debugging dumps.

  The k=1 `--only` smoke reports weren't committed. The k=1 full-suite smoke is (`analysis-20261027T212652Z`).
