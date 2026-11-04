# Eval report: visualization (visualization-20261104T023620Z)

model `claude-sonnet-4-6` · k=3 · commit `1ae7ab1` · 20261104T023620Z

| metric | value |
|---|---|
| cases | 2 |
| runs | 6 |
| pass_rate | 0.5 |
| case_majority_pass_rate | 0.5 |
| flip_rate | 0.0 |
| hard_rule_violations | 0 |
| hard_rules_na | 0 |
| errors | 0 |
| runs_with_truncated_llm_output | 0 |
| runs_with_guardrail_retry | 0 |
| runs_with_prose_rewritten | 0 |
| runs_with_pii_guard_hit | 0 |
| cost_per_question_usd | 0 |
| latency_p50_s | 0.79 |
| latency_p95_s | 0.95 |
| right_chart | 0.5 |
| headline_coverage | None |
| lint_violations | 0 |
| charts_without_rationale | 0 |
| holdout_pass_rate | None |
| pass_rate_by_category | {'targeted': 0.5} |

## Cases

| case | passes | failing checks / hard rules / error |
|---|---|---|
| viz-t09-ins-loss-ratio | 3/3 | — |
| viz-t10-ins-incurred-by-ay | 0/3 | chart: want LineChart period→Incurred losses (none); got: x Loss Date, agg sum |
