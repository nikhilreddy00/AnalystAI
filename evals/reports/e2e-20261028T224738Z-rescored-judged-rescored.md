# Eval report: e2e (e2e-20261028T224738Z-rescored-judged-rescored)

model `claude-sonnet-4-6` · k=1 · commit `5a3f2b0` · 20261028T224738Z

Rescored with the current scorer from `e2e-20261028T224738Z-rescored-judged` (no new model calls).

| metric | value |
|---|---|
| cases | 20 |
| runs | 20 |
| pass_rate | 1.0 |
| case_majority_pass_rate | 1.0 |
| flip_rate | 0.0 |
| hard_rule_violations | 13 |
| errors | 0 |
| runs_with_truncated_llm_output | 0 |
| runs_with_guardrail_retry | 0 |
| runs_with_prose_rewritten | 1 |
| cost_per_question_usd | 0.0455 |
| latency_p50_s | 12.96 |
| latency_p95_s | 144.96 |
| judge_faithfulness | 0.5652 |
| judge_faithfulness_mean_per_run | 0.574 |
| judge_actions_supported_uncalibrated | 0.2778 |
| judged_runs | 5 |
| judged_statements | 46 |
| judged_actions | 18 |
| judge_label_counts | {'contradicted': 6, 'overstated': 4, 'supported': 26, 'unsupported': 10} |
| judge_cost_usd | 0.0437 |

## Diff vs `e2e-20261028T172325Z`

- pass rate Δ on 20 common cases: +0.0 pts · McNemar p = 1.0
- broke: none
- fixed: none
- new (no prior): none

## Cases

| case | passes | failing checks / hard rules / error |
|---|---|---|
| e2e-01 | 1/1 | HARD claims_cite_evidence: uncited ['Segment A leads in average revenue per order ($121.29), foll', 'Marketing spend and revenue move together — orders with high']; unknown ids []<br>HARD insight_dates_in_range: dates ['scope: November 2025', 'scope: November 2025']<br>HARD insight_grounded: ungrounded ['900', '900'] |
| e2e-02 | 1/1 | HARD claims_cite_evidence: uncited ['Segment A generates the highest average revenue per order at', 'Revenue has dipped about 6.8% over the most recent seven-day']; unknown ids []<br>HARD insight_dates_in_range: dates ['scope: November 2025', 'scope: November 2025', 'scope: November 2025']<br>HARD insight_grounded: ungrounded ['900', '0.83%', '0.83%'] |
| e2e-03 | 1/1 | HARD claims_cite_evidence: uncited ['Segment A leads in average revenue per order ($122.00), near', 'Higher marketing spend is associated with higher revenue acr']; unknown ids []<br>HARD insight_grounded: ungrounded ['52%', '700', '0.83%', '0.83%']<br>retry: grounding_rewritten |
| e2e-04 | 1/1 | HARD claims_cite_evidence: uncited ['Segment A is the strongest performer with the highest averag', 'About 1 in 10 orders is missing a units-sold value, and the ']; unknown ids []<br>HARD insight_grounded: ungrounded ['54%', '900', '65', '130'] |
| e2e-05 | 1/1 | HARD claims_cite_evidence: uncited ['Segment A generates the highest revenue per order ($123.37 o', 'Marketing spend and revenue move together meaningfully, mean']; unknown ids []<br>HARD insight_dates_in_range: dates ['Oct 2025', 'scope: November 2025', 'scope: November 2025']<br>HARD insight_grounded: ungrounded ['900,', '900', '900,', '900', '0.56%', '0.56%'] |
| e2e-06 | 1/1 | — |
| e2e-07 | 1/1 | — |
| e2e-08 | 1/1 | — |
| e2e-09 | 1/1 | — |
| e2e-10 | 1/1 | — |
| e2e-11 | 1/1 | — |
| e2e-12 | 1/1 | — |
| e2e-13 | 1/1 | — |
| e2e-14 | 1/1 | — |
| e2e-15 | 1/1 | — |
| e2e-16 | 1/1 | — |
| e2e-17 | 1/1 | — |
| e2e-18 | 1/1 | — |
| e2e-19 | 1/1 | — |
| e2e-20 | 1/1 | — |
