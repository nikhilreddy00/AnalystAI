# Eval report: visualization (visualization-20261030T183125Z)

model `claude-sonnet-4-6` · k=1 · commit `dbf4489` · 20261030T183125Z

| metric | value |
|---|---|
| cases | 27 |
| runs | 27 |
| pass_rate | 1.0 |
| case_majority_pass_rate | 1.0 |
| flip_rate | 0.0 |
| hard_rule_violations | 0 |
| errors | 0 |
| runs_with_truncated_llm_output | 0 |
| runs_with_guardrail_retry | 0 |
| runs_with_prose_rewritten | 0 |
| runs_with_pii_guard_hit | 0 |
| cost_per_question_usd | 0 |
| latency_p50_s | 0.11 |
| latency_p95_s | 0.17 |
| right_chart | 1.0 |
| headline_coverage | 1.0 |
| lint_violations | 0 |
| charts_without_rationale | 0 |
| holdout_pass_rate | 1.0 |
| pass_rate_by_category | {'finding': 1.0, 'profile': 1.0, 'targeted': 1.0} |

## Diff vs `visualization-20261028T172107Z`

- pass rate Δ on 27 common cases: +0.0 pts · McNemar p = 1.0
- broke: none
- fixed: none
- new (no prior): none

## Cases

| case | passes | failing checks / hard rules / error |
|---|---|---|
| viz-t01 | 1/1 | — |
| viz-t02 | 1/1 | — |
| viz-t03 | 1/1 | — |
| viz-t04-holdout | 1/1 | — |
| viz-t05 | 1/1 | — |
| viz-t06 | 1/1 | — |
| viz-t07 | 1/1 | — |
| viz-t08-holdout | 1/1 | — |
| viz-v01 | 1/1 | — |
| viz-v02 | 1/1 | — |
| viz-v03 | 1/1 | — |
| viz-v04 | 1/1 | — |
| viz-v05 | 1/1 | — |
| viz-v06 | 1/1 | — |
| viz-v07-holdout | 1/1 | — |
| viz-v08 | 1/1 | — |
| viz-v09 | 1/1 | — |
| viz-v10 | 1/1 | — |
| viz-v11 | 1/1 | — |
| viz-v12 | 1/1 | — |
| viz-v13-holdout | 1/1 | — |
| viz-v14 | 1/1 | — |
| viz-v15 | 1/1 | — |
| viz-v16 | 1/1 | — |
| viz-v17 | 1/1 | — |
| viz-v18-holdout | 1/1 | — |
| viz-v19 | 1/1 | — |
