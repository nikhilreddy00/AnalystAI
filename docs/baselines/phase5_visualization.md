# Phase 5, PR 2 review: Visualization agent on the agent contract

Deterministic (no LLM), card `visualization rules-v2`, 2026-10-28, branch `phase-5-visualization`.
- **Final visualization report: `evals/reports/visualization-20261028T172107Z.*`**: 27 cases, k=3
  (identical by construction; a test pins byte-identical reruns), $0. Paired against the baseline
  `visualization-20261028T164654Z-rescored`, the old agent at k=1, rescored after one scorer fix (see Deviations).
- **E2E "before":** `e2e-20261028T145647Z-rescored`, the last gate rescored for $0 with the two new
  chart hard rules.
- **E2E gates:** `e2e-20261028T171557Z` (k=1), then **`e2e-20261028T172325Z`** on the final commit (k=1).

## Numbers

| metric (ROADMAP Visualization row) | target | baseline (old agent) | **final (k=3, 81 runs)** |
|---|---|---|---|
| right chart (type + x + y + aggregation, plus split / CI / fences where labelled) | ≥ 90% | 14.3% | **100%** |
| headline findings with a chart that cites their evidence id | ≥ 95% | 0% | **100%** |
| **hard:** chart lint violations | 0 | 4 | **0** |
| **hard:** charts without a rationale | 0 | 94 | **0** |
| holdout (5 cases) | | 40% | **100%** |
| pass rate, all cases | | 15.4% | **100%** (22 fixed, 0 broke, McNemar p < 0.001) |
| **E2E** pass rate (must stay 100%) | 100% | 100% | **100%** (20/20, 0 broke, both gates) |
| E2E chart hard rules (lint clean, rationale) | 0 | 20 violations | **0** |
| E2E cost per question / p95 | | $0.0349 / 112s | $0.0462 / 147s (the overview now runs its full plan, see Cost) |

Cases: 18 finding/profile cases (sales, Simpson, mixed dates), 8 targeted-question cases, and `viz-v19`,
a regression case replaying the first real overview after the approval fix. Every case carries a
`label_note` giving the analyst's reason.

On the final gate, the real overview (`e2e-01`) shows, in order: revenue by region and segment,
revenue vs marketing spend (r = 0.57), 5 unusual revenue values above 260.65 (the 5 planted ones),
segment B's revenue down 12.6% month over month, average revenue by segment, and revenue vs units.

## Built
- **`VIZ_CARD`** with measured limits. The agent is deterministic, so there is no prompt file and no
  `llm` parameter, and its eval costs $0.
- **`build_context` → `VizContext`:** the frame, column roles, measures (`units` 1-8 counts as a
  measure, `Pclass` does not), date column, plan, findings and evidence.
- **`charts_for_findings`:** one chart per finding.
  - trend → monthly line, split by segment for per-segment trends;
  - comparison → bars of the metric with 95% CIs (t for means, Wilson for rates, none for totals);
  - joint segments → grouped bars within the stratum (Simpson);
  - correlation → scatter with the driver on x;
  - anomaly → full-range histogram with Tukey fences;
  - a test → its group means.
  - Kinds take turns in the plan's order, the plan's first metric first, with up to 6 charts in Summary.
  - Months use the analysis agent's MoM rule, so a title's % equals the finding's % (a test pins it).
  - Titles state the finding, built from the plotted numbers.
- **`ChartRationale` on every chart:** the rule, a plain reason, x / y / split, aggregation, n, the
  evidence ids, a pandas expression that recomputes it, and the finding's confidence (never the chart's own).
- **Profile charts** for overviews with few findings. Row-count charts get `placement="data"` (Data tab).
- **`guardrails.chart_lint` + `finalize_charts`:** every chart leaves through `finalize_charts`. That
  covers the viz agent, all targeted handlers, chart requests and drill-down's model-suggested chart.
  - The lint rules are: empty data, missing axis labels, a measure on a category axis, pie > 5 slices
    or non-additive, dual axes, > 8 series, outcome on x, a line over an unordered category.
  - Agent charts are repaired or dropped, with a trace line per drop. The user's own choices are drawn
    as asked, with a plain-English note.
  - Generic palette colours become `[]`.
- **Targeted answers on the same policy:**
  - correlation → scatter of the pair (it was a bar of r values);
  - segment and A/B comparisons → bars with CIs;
  - A/B is stratified when the plan names a segment;
  - outlier scan → the distribution with its fences (it was a bar of outlier counts);
  - a chart request averages rates instead of summing them.
- **UI (`ChartRenderer`, `ReportCanvas`, `EvidencePanel`, `KPICard`):**
  - rows and the finding's confidence under each title, CI whiskers, fence lines, and bars that always
    start at zero;
  - a "Why this chart" section, where "See evidence" opens the cited evidence item;
  - lint notes;
  - a type switch that offers only types that can't mislead, and "Change variables…" to prefill the
    chat box;
  - row-count charts in the Data tab;
  - KPI tiles that say how they were computed.
  - Checked with the recorded-SSE kit on desktop (dark) and phone (light), with no overflow.
- **`POST /dashboard/chart-feedback`:**
  - owner only;
  - re-checks `swap_options` (422 on a misleading type);
  - saves the switch on the stored chart, so Pin and the PPTX follow it;
  - logs a label-only feedback trace.
- **KPI check:** `kpi_mismatches` recomputes every tile from its recipe, and checks an average against
  the report's `eda_summary`. `analysis_node` drops a wrong tile. A test plants a wrong value in each
  tile kind.
- **PPTX:** Summary charts only, grouped bars with CI whiskers (the old renderer dropped every series
  after the first), and a series palette when `colors` is empty.
- **Root-cause fix (`raw_query`):** the high-null approval answer ("Continue with imputation") re-ran
  intake and replaced the user's question and plan.
  - The gate's context now travels with the answer (frontend → API → graph → E2E runner).
  - A high-null approval keeps the plan; a clarification answer is still classified (two tests).
- **Security:** chart titles now carry data values, so the insight and critic prompts mask and delimit
  them. A planted-injection test fails on the old code.

## Cost
- Visualization suite: $0 (deterministic) for every run, including k=3.
- E2E: the "before" was a rescore ($0). Gate 1 was **$0.95 against a $0.70 estimate (+36%)**, which
  crossed the 30% stop rule. I stopped, diagnosed it and reported it:
  - the approval fix made the overview run its full plan (39 findings, where every earlier gate had 0);
  - analysis output grew from ~270 to ~2,500 tokens per overview;
  - targeted cases were unchanged.
  - The user approved a re-gate on the final commit: $0.92 (estimate $0.95).
- **PR total: $1.87** (cap $5).

## Deviations from the plan
- **Holdouts ran once mid-iteration.** The first post-refactor run (`visualization-20261028T165327Z`)
  included the 5 holdout cases, which the playbook keeps for the final run. Nothing was tuned on them:
  4 of 5 already passed, and the failing one (t08) was in the targeted refactor already planned. Every
  later iteration ran with `--exclude holdout`.
- **Scorer change (baseline only):** the old donut is keyed `name`/`value`, so its column and row-count
  nature had to be inferred from the title. This was fixed and the baseline rescored for $0, before any
  agent change. New charts are scored from their rationale.
- **One case added after the plan:** `viz-v19`, from the E2E gate, because a real overview has far more
  findings than any planned case had.
- **Profile charts carry no evidence id** (there is no finding behind them). Their rationale has the
  rule, columns, n and pandas code instead. "Charts without a rationale = 0" holds for every chart.
- **No new Evidence kind for charts:** a chart cites the findings' evidence ids.

## Known limits (carried forward)
- Rule-based: only the finding kinds above get a dedicated chart. A statistical test is charted only
  as its group means, and only when the plan names a metric and a segment.
- Outcome detection is a name heuristic plus the plan, then the detected target. When neither column is
  marked, the rationale says so.
- At most 8 series and 15 categories (20 for a chart request); the rest are dropped and the reason says so.
- The eval covers sales-style, experiment and mixed-date tables only. Other domains wait for Phase 5.5.

## Post-PR audit (2026-10-28)
- **Fixed, each with a test:**
  - chart titles were undelimited in the insight and critic prompts;
  - trend charts could switch to days on short spans, while the finding is MoM;
  - count bars were labelled "Number of revenue";
  - Pin pinned the original chart, not the user's switch;
  - the top-category KPI check missed a wrong value;
  - the PPTX dropped grouped-bar series;
  - the overview's 6 Summary slots went all to one finding kind (v19);
  - charts pinned before this PR kept the old Tableau colours (now cleared when the dashboard loads).
- **Checked, no change needed:**
  - no `get_client()` in the visualization agent (it has no LLM);
  - every new field is in `frontend/src/types/index.ts`;
  - the SSE note in CLAUDE.md is updated;
  - traces hold labels only (`dropped:<rule>`, `no_rationale`, `kpi_mismatch`, and `chart_override`
    with before/after types).
- **Second audit (before merge), each with a test:**
  - one malformed finding (e.g. `amt__amt`) crashed the whole report; now it costs only its chart;
  - a mid-month export compared a partial last month with a full one. Analysis MoM, trend charts
    and targeted time series now compare complete months and say so. The eval tables end on month-ends,
    so no number moved (pinned by a test);
  - the quality critic now delimits and masks every data-derived section (closing the open item).
    E2E never scores the critic, so no paid re-run was needed (pinned by a test);
  - a duplicated reason on scatters, and a donut rationale naming its key ("name") instead of its column;
  - targeted-answer charts now persist type switches and support "Change variables…".
- **Still open:** none.
