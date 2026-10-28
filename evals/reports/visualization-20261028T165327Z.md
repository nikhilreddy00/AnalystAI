# Eval report: visualization (visualization-20261028T165327Z)

model `claude-sonnet-4-6` · k=1 · commit `64b6783` · 20261028T165327Z

| metric | value |
|---|---|
| cases | 26 |
| runs | 26 |
| pass_rate | 0.8077 |
| case_majority_pass_rate | 0.8077 |
| flip_rate | 0.0 |
| hard_rule_violations | 7 |
| errors | 0 |
| runs_with_truncated_llm_output | 0 |
| runs_with_guardrail_retry | 0 |
| runs_with_prose_rewritten | 0 |
| cost_per_question_usd | 0 |
| latency_p50_s | 0.09 |
| latency_p95_s | 0.2 |
| right_chart | 0.8214 |
| headline_coverage | 1.0 |
| lint_violations | 0 |
| charts_without_rationale | 7 |
| holdout_pass_rate | 0.8 |
| pass_rate_by_category | {'finding': 1.0, 'profile': 0.75, 'targeted': 0.5} |

## Cases

| case | passes | failing checks / hard rules / error |
|---|---|---|
| viz-t01 | 0/1 | HARD chart_has_rationale: 1 chart(s) without a rationale<br>chart: want ScatterChart marketing_spend→revenue (none); got: type HorizontalBarChart, x pair, y correlation, agg ? |
| viz-t02 | 1/1 | HARD chart_has_rationale: 1 chart(s) without a rationale |
| viz-t03 | 0/1 | HARD chart_has_rationale: 1 chart(s) without a rationale<br>chart: want BarChart segment→revenue (mean); got: no CI |
| viz-t04-holdout | 1/1 | HARD chart_has_rationale: 1 chart(s) without a rationale |
| viz-t05 | 1/1 | HARD chart_has_rationale: 1 chart(s) without a rationale |
| viz-t06 | 1/1 | — |
| viz-t07 | 0/1 | HARD chart_has_rationale: 1 chart(s) without a rationale<br>chart: want BarChart department→converted (mean); got: x group, split None, no CI |
| viz-t08-holdout | 0/1 | HARD chart_has_rationale: 1 chart(s) without a rationale<br>chart: want HistogramChart revenue→count (count); got: type HorizontalBarChart, x column, no thresholds |
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
| viz-v15 | 0/1 | chart: want HistogramChart units→count (count); got: type LineChart, x order_date, y revenue, agg sum; type ScatterChart, x marketing_spend, y revenue, agg none; x revenue |
| viz-v16 | 1/1 | — |
| viz-v17 | 1/1 | — |
| viz-v18-holdout | 1/1 | — |
