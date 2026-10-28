# Eval report: visualization (visualization-20261028T164654Z-rescored)

model `claude-sonnet-4-6` · k=1 · commit `d86537b` · 20261028T164654Z

Rescored with the current scorer from `visualization-20261028T164654Z` (no new model calls).

| metric | value |
|---|---|
| cases | 26 |
| runs | 26 |
| pass_rate | 0.1538 |
| case_majority_pass_rate | 0.1538 |
| flip_rate | 0.0 |
| hard_rule_violations | 30 |
| errors | 0 |
| runs_with_truncated_llm_output | 0 |
| runs_with_guardrail_retry | 0 |
| runs_with_prose_rewritten | 0 |
| cost_per_question_usd | 0 |
| latency_p50_s | 0.08 |
| latency_p95_s | 0.17 |
| right_chart | 0.1429 |
| headline_coverage | 0.0 |
| lint_violations | 4 |
| charts_without_rationale | 94 |
| holdout_pass_rate | 0.4 |
| pass_rate_by_category | {'finding': 0.0, 'profile': 0.25, 'targeted': 0.375} |

## Cases

| case | passes | failing checks / hard rules / error |
|---|---|---|
| viz-t01 | 0/1 | HARD chart_has_rationale: 1 chart(s) without a rationale<br>chart: want ScatterChart marketing_spend→revenue (none); got: type HorizontalBarChart, x pair, y correlation, agg ? |
| viz-t02 | 1/1 | HARD chart_has_rationale: 1 chart(s) without a rationale |
| viz-t03 | 0/1 | HARD chart_has_rationale: 1 chart(s) without a rationale<br>chart: want BarChart segment→revenue (mean); got: no CI |
| viz-t04-holdout | 1/1 | HARD chart_has_rationale: 1 chart(s) without a rationale |
| viz-t05 | 1/1 | HARD chart_has_rationale: 1 chart(s) without a rationale |
| viz-t06 | 0/1 | HARD chart_has_rationale: 1 chart(s) without a rationale<br>chart: want PieChart region→discount (mean); got: agg ?<br>user_choice_warned: the user's chart carries no lint note |
| viz-t07 | 0/1 | HARD chart_has_rationale: 1 chart(s) without a rationale<br>chart: want BarChart department→converted (mean); got: x group, split None, no CI |
| viz-t08-holdout | 0/1 | HARD chart_has_rationale: 1 chart(s) without a rationale<br>chart: want HistogramChart revenue→count (count); got: type HorizontalBarChart, x column, no thresholds |
| viz-v01 | 0/1 | HARD chart_has_rationale: 5 chart(s) without a rationale<br>chart: want LineChart order_date→revenue (sum); got: agg ?; type DonutChart, x segment, y count, agg count; type ScatterChart, x revenue, y marketing_spend, agg none; type HistogramChart, x discount, y count, agg count; type BarChart, x segment, y count, agg count<br>headline: no chart cites trend:revenue:MoM |
| viz-v02 | 0/1 | HARD chart_has_rationale: 5 chart(s) without a rationale<br>chart: want LineChart order_date→revenue (sum); got: agg ?, split None; type DonutChart, x segment, y count, agg count, split None; type ScatterChart, x revenue, y marketing_spend, agg none, split None; type HistogramChart, x discount, y count, agg count, split None; type BarChart, x segment, y count, agg count, split None<br>headline: no chart cites trend:revenue (B):MoM |
| viz-v03 | 0/1 | HARD chart_has_rationale: 5 chart(s) without a rationale<br>chart: want BarChart segment→revenue (mean); got: type LineChart, x order_date, agg ?, no CI; type DonutChart, y count, agg count, no CI; type ScatterChart, x revenue, y marketing_spend, agg none, no CI; type HistogramChart, x discount, y count, agg count, no CI; y count, agg count, no CI<br>headline: no chart cites seg:segment/revenue |
| viz-v04 | 0/1 | HARD chart_has_rationale: 5 chart(s) without a rationale<br>chart: want BarChart region→revenue (sum); got: type LineChart, x order_date, agg ?; type DonutChart, x segment, y count, agg count; type ScatterChart, x revenue, y marketing_spend, agg none; type HistogramChart, x discount, y count, agg count; x segment, y count, agg count<br>headline: no chart cites seg:region/revenue |
| viz-v05 | 0/1 | HARD chart_has_rationale: 5 chart(s) without a rationale<br>HARD chart_lint_clean: marketing_spend vs revenue: outcome_on_x<br>chart: want ScatterChart marketing_spend→revenue (none); got: type LineChart, x order_date, agg ?; type DonutChart, x segment, y count, agg count; x revenue, y marketing_spend; type HistogramChart, x discount, y count, agg count; type BarChart, x segment, y count, agg count<br>headline: no chart cites corr:marketing_spend__revenue |
| viz-v06 | 0/1 | HARD chart_has_rationale: 5 chart(s) without a rationale<br>HARD chart_lint_clean: marketing_spend vs revenue: outcome_on_x<br>chart: want ScatterChart marketing_spend→revenue (none); got: type LineChart, x order_date, agg ?; type DonutChart, x segment, y count, agg count; x revenue, y marketing_spend; type HistogramChart, x discount, y count, agg count; type BarChart, x segment, y count, agg count<br>headline: no chart cites corr:revenue__marketing_spend |
| viz-v07-holdout | 0/1 | HARD chart_has_rationale: 5 chart(s) without a rationale<br>HARD chart_lint_clean: marketing_spend vs revenue: outcome_on_x<br>chart: want ScatterChart discount→revenue (none); got: type LineChart, x order_date, agg ?; type DonutChart, x segment, y count, agg count; x revenue, y marketing_spend; type HistogramChart, y count, agg count; type BarChart, x segment, y count, agg count<br>headline: no chart cites corr:discount__revenue |
| viz-v08 | 0/1 | HARD chart_has_rationale: 5 chart(s) without a rationale<br>chart: want HistogramChart revenue→count (count); got: type LineChart, x order_date, y revenue, agg ?, no thresholds; type DonutChart, x segment, no thresholds; type ScatterChart, y marketing_spend, agg none, no thresholds; x discount, no thresholds; type BarChart, x segment, no thresholds<br>headline: no chart cites anom:revenue |
| viz-v09 | 0/1 | HARD chart_has_rationale: 5 chart(s) without a rationale<br>chart: want BarChart region→discount (mean); got: type LineChart, x order_date, y revenue, agg ?, no CI; type DonutChart, x segment, y count, agg count, no CI; type ScatterChart, x revenue, y marketing_spend, agg none, no CI; type HistogramChart, x discount, y count, agg count, no CI; x segment, y count, agg count, no CI<br>headline: no chart cites seg:region/discount |
| viz-v10 | 0/1 | HARD chart_has_rationale: 5 chart(s) without a rationale<br>chart: want LineChart order_date→discount (mean); got: y revenue, agg ?; type DonutChart, x segment, y count, agg count; type ScatterChart, x revenue, y marketing_spend, agg none; type HistogramChart, x discount, y count, agg count; type BarChart, x segment, y count, agg count<br>headline: no chart cites trend:discount:MoM |
| viz-v11 | 0/1 | HARD chart_has_rationale: 4 chart(s) without a rationale<br>chart: want BarChart department→converted (mean); got: split None, no CI; x group, split None, no CI; x session_minutes, agg count, split None, no CI; type HistogramChart, x session_minutes, y count, agg count, split None, no CI<br>headline: no chart cites seg:group\|department/converted |
| viz-v12 | 0/1 | HARD chart_has_rationale: 4 chart(s) without a rationale<br>chart: want BarChart department→converted (mean); got: no CI; x group, no CI; x session_minutes, agg count, no CI; type HistogramChart, x session_minutes, y count, agg count, no CI<br>headline: no chart cites seg:department/converted |
| viz-v13-holdout | 0/1 | HARD chart_has_rationale: 4 chart(s) without a rationale<br>chart: want BarChart group→session_minutes (mean); got: x department, y converted, no CI; y converted, no CI; x session_minutes, y converted, agg count, no CI; type HistogramChart, x session_minutes, y count, agg count, no CI<br>headline: no chart cites test:0 |
| viz-v14 | 0/1 | HARD chart_has_rationale: 5 chart(s) without a rationale<br>HARD chart_lint_clean: marketing_spend vs revenue: outcome_on_x<br>chart: want BarChart segment→revenue (mean); got: type LineChart, x order_date, agg ?, no CI; type DonutChart, y count, agg count, no CI; type ScatterChart, x revenue, y marketing_spend, agg none, no CI; type HistogramChart, x discount, y count, agg count, no CI; y count, agg count, no CI<br>chart: want LineChart order_date→revenue (sum); got: agg ?; type DonutChart, x segment, y count, agg count; type ScatterChart, x revenue, y marketing_spend, agg none; type HistogramChart, x discount, y count, agg count; type BarChart, x segment, y count, agg count<br>chart: want ScatterChart marketing_spend→revenue (none); got: type LineChart, x order_date, agg ?; type DonutChart, x segment, y count, agg count; x revenue, y marketing_spend; type HistogramChart, x discount, y count, agg count; type BarChart, x segment, y count, agg count<br>headline: no chart cites corr:marketing_spend__revenue<br>headline: no chart cites seg:segment/revenue<br>headline: no chart cites trend:revenue:MoM |
| viz-v15 | 0/1 | HARD chart_has_rationale: 5 chart(s) without a rationale<br>chart: want HistogramChart units→count (count); got: type LineChart, x order_date, y revenue, agg ?; type DonutChart, x segment; type ScatterChart, x revenue, y marketing_spend, agg none; x discount; type BarChart, x segment<br>no_row_count_in_summary: row-count charts in Summary: ['Segment Breakdown — A Leads at 41%', 'segment breakdown'] |
| viz-v16 | 0/1 | HARD chart_has_rationale: 5 chart(s) without a rationale<br>chart: want LineChart order_date→revenue (sum); got: agg ?; type DonutChart, x segment, y count, agg count; type ScatterChart, x revenue, y marketing_spend, agg none; type HistogramChart, x discount, y count, agg count; type BarChart, x segment, y count, agg count<br>no_row_count_in_summary: row-count charts in Summary: ['Segment Breakdown — A Leads at 41%', 'segment breakdown'] |
| viz-v17 | 0/1 | HARD chart_has_rationale: 5 chart(s) without a rationale<br>chart: want LineChart order_date→revenue (sum); got: agg ?; type DonutChart, x segment, y count, agg count; type ScatterChart, x revenue, y marketing_spend, agg none; type HistogramChart, x discount, y count, agg count; type BarChart, x segment, y count, agg count<br>no_row_count_in_summary: row-count charts in Summary: ['Segment Breakdown — A Leads at 41%', 'segment breakdown'] |
| viz-v18-holdout | 1/1 | HARD chart_has_rationale: 4 chart(s) without a rationale |
