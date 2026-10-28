# Eval report: visualization (visualization-20261028T171534Z)

model `claude-sonnet-4-6` · k=3 · commit `838d378` · 20261028T171534Z

| metric | value |
|---|---|
| cases | 26 |
| runs | 78 |
| pass_rate | 1.0 |
| case_majority_pass_rate | 1.0 |
| flip_rate | 0.0 |
| hard_rule_violations | 0 |
| errors | 0 |
| runs_with_truncated_llm_output | 0 |
| runs_with_guardrail_retry | 0 |
| runs_with_prose_rewritten | 0 |
| cost_per_question_usd | 0 |
| latency_p50_s | 0.09 |
| latency_p95_s | 0.11 |
| right_chart | 1.0 |
| headline_coverage | 1.0 |
| lint_violations | 0 |
| charts_without_rationale | 0 |
| holdout_pass_rate | 1.0 |
| pass_rate_by_category | {'finding': 1.0, 'profile': 1.0, 'targeted': 1.0} |

## Diff vs `visualization-20261028T164654Z-rescored`

- pass rate Δ on 26 common cases: +84.62 pts · McNemar p = 0.0
- broke: none
- fixed: viz-t01, viz-t03, viz-t06, viz-t07, viz-t08-holdout, viz-v01, viz-v02, viz-v03, viz-v04, viz-v05, viz-v06, viz-v07-holdout, viz-v08, viz-v09, viz-v10, viz-v11, viz-v12, viz-v13-holdout, viz-v14, viz-v15, viz-v16, viz-v17
- new (no prior): none

## Cases

| case | passes | failing checks / hard rules / error |
|---|---|---|
| viz-t01 | 3/3 | — |
| viz-t02 | 3/3 | — |
| viz-t03 | 3/3 | — |
| viz-t04-holdout | 3/3 | — |
| viz-t05 | 3/3 | — |
| viz-t06 | 3/3 | — |
| viz-t07 | 3/3 | — |
| viz-t08-holdout | 3/3 | — |
| viz-v01 | 3/3 | — |
| viz-v02 | 3/3 | — |
| viz-v03 | 3/3 | — |
| viz-v04 | 3/3 | — |
| viz-v05 | 3/3 | — |
| viz-v06 | 3/3 | — |
| viz-v07-holdout | 3/3 | — |
| viz-v08 | 3/3 | — |
| viz-v09 | 3/3 | — |
| viz-v10 | 3/3 | — |
| viz-v11 | 3/3 | — |
| viz-v12 | 3/3 | — |
| viz-v13-holdout | 3/3 | — |
| viz-v14 | 3/3 | — |
| viz-v15 | 3/3 | — |
| viz-v16 | 3/3 | — |
| viz-v17 | 3/3 | — |
| viz-v18-holdout | 3/3 | — |
