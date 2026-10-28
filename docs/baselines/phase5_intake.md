# Phase 5, PR 1 review: Intake agent on the agent contract

Model `claude-sonnet-4-6`, temperature 0, prompt `intake/v2`, 2026-10-28, branch `phase-5-intake`.
- **Final Intake report: `evals/reports/intake-20261028T144728Z.*`** (k=3, 60 cases, commit `467bb7c`).
  It is paired against the k=1 baseline `intake-20261028T143517Z` (the old agent, `v0`).
- **Post-audit confirmation on the final code:** `intake-20261028T145555Z` (k=1, commit `60440b7`).
- **SQL follow-ups (the Phase 4 open item):** `sql-20261028T145103Z` (k=3), against the baseline
  `sql-20261028T143637Z`.
- **E2E gate:** `e2e-20261028T145138Z` and, on the final code, `e2e-20261028T145647Z`. Both are k=1,
  paired with the last gate `e2e-20261028T024530Z`. The method was agreed with the user: k=1 first,
  then k=3 only on failing cases. No case failed, so nothing was re-run.
- **Rejected option kept as evidence:** `intake-20261028T144620Z` (Haiku 4.5, 48 non-holdout cases).

## Numbers

| metric (ROADMAP Intake row) | target | baseline v0 (k=1) | **final v2 (k=3, 180 runs)** | final code (k=1) |
|---|---|---|---|---|
| intent accuracy | ≥ 90% | 100% | **97.4%** | 97.4% |
| column mapping | ≥ 95% | 100% | **98.2%** | 100% |
| asks when ambiguous | ≥ 85% | 61.1% | **100%** | 100% |
| doesn't ask when clear | ≥ 90% | 100% | **100%** | 100% |
| **hard:** invented columns shipped | 0 | 0 | **0** (the model invented 0) | 0 |
| hard: injections followed | 0 | 0 | **0** | 0 |
| parameter accuracy (n, order, frequency, chart type) | | 94.7% | 96.5% | 100% |
| holdout (12 cases never used while iterating) | | 91.7% | **100%** | 100% |
| pass rate, all 60 | | 86.7% | **97.2%** (flip 1.7%; 7 fixed, 1 broke, McNemar p 0.07) | 98.3% |
| runs with a guided retry | | — | 1 / 180 (format) | |
| cost per question | | $0.0059 | **$0.0053** | $0.0065 |
| p50 / p95 latency | | 3.8s / 6.0s | **4.0s / 6.6s** | |
| **SQL follow-ups**: execution accuracy (6 cases) | | 33% (2/6) | **100%** (18/18 runs) | |
| **E2E** pass rate (must stay ≥ 100%) | 100% | | **100%** (20/20, 0 broke) | **100%** (20/20) |
| E2E cost per question / p95 | | $0.0554 / 155s (last gate) | $0.0353 / 116s | $0.0349 / 112s |

**The one regression is `followup-sql-02`** ("how did those change month to month?" over a result
with a `month` column). `connectors/base.py` detects dates by column name only, so `month` is marked
categorical. Intake is then told "Date columns: []" and plans a by-month comparison instead of a time
series (3 of 3 runs). The baseline had guessed a time series even though nothing in its input marked a
date. The cause is ingestion's type inference: a SQL `DATE` column arrives as an object column and hits
the same gap. It is carried to the Ingestion PR, whose 20-table type-inference eval can check a fix
safely. The label was not changed.

**Read the 100% asking rate honestly.** Three ambiguous cases mirror examples that were already in
the original prompt ("how are we doing?", "check the data", "any insights?"); the cases were written
from the old prompt's own policy. Prompt v2's new examples come from no case. The holdout ambiguous
cases (`Is this good?`, the `price_usd`/`price_eur` split) pass 3/3, but 60 synthetic English
questions are a small set: a 95% CI on 18 asking cases at 100% is roughly 81–100%.

## Built
- **`INTAKE_CARD`.** Its refusal rules are also the eval's labelling policy: ask only on zero
  signal or when a word matches 2+ columns, say so when the data is missing, never substitute a column,
  never ask on a first run.
- **Prompts:** `prompts/intake/v1.md` (the old inline prompt, verbatim), then `v2.md`. v2 removes a
  contradiction: v1 listed "how are we doing?" as vague *and* said to default to overview when
  ambiguous, and the model followed the second. v2 also adds the column-ambiguity rule.
  `overview_v<N>.md` holds the first-run override.
- **`build_context` → `IntakeContext`:** the schema (delimited, capped at 60 columns), the
  conversation memory, and the previous plan (delimited since the audit).
- **Shared memory, `agents.utils.conversation_context`:** the last 6 turns (assistant turns cut to
  400 chars) plus the last query run, delimited.
  - Intake uses it.
  - **Ingestion passes it to the SQL agent.** That closes the Phase 4 open item: ingestion runs
    before intake, so the SQL agent needed the memory itself.
  - The block is omitted when empty, so standalone SQL questions see the byte-identical Phase 4
    prompt (a test proves it), and the 50 SQL cases did not need a re-run.
- **Hard rules:**
  - `guardrails.plan_columns` covers every column-bearing field, including the `targeted_params`
    columns, which were never checked before.
  - Invalid JSON, an unknown intent or an invented column gets one guided retry that lists the real
    columns.
  - Columns still invented after it are dropped, and the user is asked which column they meant
    (closest names via `difflib`). So no plan can ship an invented column.
  - An unknown intent twice raises `LLMOutputError`.
  - An invalid parameter value is dropped and traced.
  - A first run is forced to an overview with no question.
- **`llm` as a parameter:** for the agent, `generate_data_narrative` and `generate_proactive_questions`.
  The latter now delimits column names and the summary (it didn't before).
- **Eval:**
  - `evals.run intake` covers 60 cases: clear 20, ambiguous 10, out-of-scope 8, injected 8,
    follow-up 14. 18 of them run on database results, and 12 are held out.
  - The scorer is deterministic: intent, column mapping, parameters, asking and forbidden text.
    Hard rules are `no_invented_columns` and `no_injection_follow`.
  - 6 SQL follow-up cases. For each, re-running the previous query is the trap, and a Postgres test
    proves it gives a different answer.

## Cost controls (added at the user's request after ~$80 of eval spend in one day)
- **Prompt caching:** every agent's system prompt is a module constant and is now cached. Cache
  writes are priced at 1.25× input, reads at 0.1×. E2E cost per question fell from $0.0554 to
  $0.0353.
- **`python -m evals.run rescore <report>`:** runs store their raw output, so a scorer fix re-scores
  for $0 instead of re-running.
- **Run order:** offline tests → one case → category k=1 → one k=3 per suite. Estimates were announced
  before every run.
- **This PR's spend: ≈ $4.22** (estimate $5, hard stop $7).

| run | cost |
|---|---|
| caching check | $0.02 |
| baselines (intake $0.35, SQL follow-ups $0.22) | $0.57 |
| refactor + memory smokes | $0.10 |
| v2 smoke | $0.32 |
| Haiku candidate | $0.20 |
| intake k=3 | $0.95 |
| SQL follow-ups k=3 | $0.26 |
| E2E gate | $0.71 |
| post-audit confirmation (intake $0.39 + E2E $0.70) | $1.09 |

The SQL follow-up baseline ran 57% over its estimate ($0.22 vs $0.14): without the conversation, the
model probed the data with tools. This was flagged at the time.

## Deviations from the plan
- **Haiku 4.5 not shipped.** It was 39% cheaper per Intake call ($0.0041 vs $0.0067) and 2× faster,
  and it matched or beat Sonnet on intent, columns and parameters. But on `injected-csv-06` it acted
  on the injected `profit` column and asked about it (no-ask 97.1% < 100%). The agreed rule was that a
  cheaper model must match every target.
- **No `get_schema` tool.** The guided retry already lists the columns it would return. It is one
  round-trip and less code, and retries are rare (1 in 180 runs).
- **No semantic layer or `get_metric_definition` in Intake** (decided before the build): Intake maps to
  the query result's columns, after the SQL agent has applied the layer.
- **No Evidence item for Intake:** it produces a plan, not a finding.
- **E2E gate at k=1 + targeted k=3** (the user's choice, for cost) instead of k=3.
- **Scorer:** new, so there were no scorer changes. One count fix: `_sql_quality` is now keyed on
  `sql` runs, since intake runs also carry a `category`.

## Known limits (carried forward)
- **Date detection is name-only** (`followup-sql-02`). For the Ingestion PR.
- **The SQL agent now sees the conversation for every question.** A brand-new question after a
  filtered thread could inherit a stale filter. No eval case covers a topic switch yet.
- **`n` is not set to 1 for singular superlatives** ("which grew fastest?"). The ranked list still
  puts the answer first.
- **English only, ≤ 60 columns, the last 6 turns.** The datasets are the sales/experiment tables plus
  small database-result frames. Phase 5.5 covers other domains.

## Post-PR audit (2026-10-28)
- **Fixed, each with a regression test:**
  1. The previous plan was sent undelimited, though it quotes column names and the past question.
  2. A pydantic `ValidationError` reached users verbatim through `_fail`; it is now `LLMOutputError`.
  3. A list field given as a string was iterated by character.
  4. The card claimed sample values were an input.
- **Re-measured on the fixed code:** Intake k=1 and E2E k=1 (both above), with no change.
- **Checked, no change needed:**
  - injection paths: history, proactive questions, narrative, and the SQL conversation block, which
    is delimited, with the UNTRUSTED_NOTE in the SQL prompt;
  - no `except: pass`;
  - the clarification and `rerun_overview` routing is unchanged;
  - `AnalysisTask`, the SSE protocol and `frontend/src/types` are unchanged, so persisted sessions
    still load;
  - traces carry rule labels only (test).
