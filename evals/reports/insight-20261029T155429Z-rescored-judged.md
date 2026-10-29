# Eval report: insight (insight-20261029T155429Z-rescored-judged)

model `claude-sonnet-4-6` · k=1 · commit `a20409a` · 20261029T155429Z

Rescored with the current scorer from `insight-20261029T155429Z` (no new model calls).

| metric | value |
|---|---|
| cases | 16 |
| runs | 16 |
| pass_rate | 0.6875 |
| case_majority_pass_rate | 0.6875 |
| flip_rate | 0.0 |
| hard_rule_violations | 30 |
| errors | 0 |
| runs_with_truncated_llm_output | 0 |
| runs_with_guardrail_retry | 0 |
| runs_with_prose_rewritten | 0 |
| cost_per_question_usd | 0.0355 |
| latency_p50_s | 43.02 |
| latency_p95_s | 48.78 |
| hard_insight_grounded | 0.3125 |
| hard_insight_dates_in_range | 0.875 |
| hard_executive_no_jargon | 0.9375 |
| hard_claims_cite_evidence | 0.0 |
| hard_no_injection_echo | 1.0 |
| recall | 1.0 |
| must_not | 0.7778 |
| contradictions_named | 0.6 |
| hedged | 0.0 |
| no_causal_overreach | 1.0 |
| holdout_pass_rate | None |
| pass_rate_by_category | {'adversarial': 1.0, 'contradictory': 0.4444, 'data_quality': 1.0, 'date_scope': 1.0, 'null_result': 1.0, 'straightforward': 1.0} |
| judge_faithfulness | 0.6667 |
| judge_faithfulness_mean_per_run | 0.6659 |
| judged_runs | 16 |
| judged_claims | 186 |
| judge_label_counts | {'contradicted': 4, 'overstated': 16, 'supported': 124, 'unsupported': 42} |
| judge_cost_usd | 0.0766 |

## Cases

| case | passes | failing checks / hard rules / error |
|---|---|---|
| ins-01-sales-overview | 1/1 | HARD claims_cite_evidence: uncited ['Marketing spend is meaningfully linked to revenue — orders w', 'Discount rates show almost no relationship to revenue, meani']; unknown ids []<br>HARD insight_grounded: ungrounded ['0.83%', '0.83%'] |
| ins-09-simpson | 0/1 | HARD claims_cite_evidence: uncited ['Control group converts at 60%, treatment at 40% — control wi', 'In the Easy department, treatment converts at 80% versus con']; unknown ids []<br>HARD executive_no_jargon: jargon ['`']<br>HARD insight_grounded: ungrounded ['1.5', '1.5']<br>contradiction: missing ['(each\|every\|both\|within\|inside\|either) (department\|group)'] |
| ins-11-total-vs-segment | 0/1 | HARD claims_cite_evidence: uncited ['Revenue declined 2.8% month-over-month in October 2026, but ', 'Segment B experienced a 12.6% revenue drop in October versus']; unknown ids []<br>contradiction: missing ['segment c.{0,100}(up\|rose\|grew\|grow\|increas\|gain\|jump\|climb\|higher)\|(up\|rose\|grew\|grow\|increas\|gain\|jump\|climb\|higher).{0,60}segment c'] |
| ins-12-volume-vs-revenue | 1/1 | HARD claims_cite_evidence: uncited ['Segment B sold 41.5% more units in October than in September', 'Segment A is the largest segment at roughly 40% of orders, m']; unknown ids [] |
| ins-13-mom-vs-yoy | 1/1 | HARD claims_cite_evidence: uncited ['Monthly recurring revenue grew 6.5% over the past year, conf', 'A single-month drop of 8.0% in October 2026 is the sharpest ']; unknown ids []<br>HARD insight_dates_in_range: dates ['November 2026'] |
| ins-14-mean-vs-median | 1/1 | HARD claims_cite_evidence: uncited ['The middle 50% of orders fall between approximately $175 and', 'Roughly 35 orders — just over 1% of the dataset — have unusu']; unknown ids []<br>HARD insight_grounded: ungrounded ['175', '175', '50%', '175', '175', '125'] |
| ins-15-gap-not-significant | 0/1 | HARD claims_cite_evidence: uncited ['North region orders average $113.32 in revenue, while South ', 'Marketing spend shows a moderate positive relationship with ']; unknown ids []<br>HARD insight_grounded: ungrounded ['20%', '19.29', '20%', '19.29', '50', '19.29']<br>hedge: stated without a qualifier: ['The North region averages roughly $113.32 per order compared to $94.03 in the So', 'North region orders average $113.32 in revenue, while South region orders averag'] |
| ins-17-low-n-correlation | 0/1 | HARD claims_cite_evidence: uncited ['Marketing spend and revenue are closely aligned — orders wit', 'Segment A accounts for 44% of all orders, making it the domi']; unknown ids []<br>HARD insight_grounded: ungrounded ['120', '1.5', '119.54', '34%', '1.5', '119.54']<br>hedge: stated without a qualifier: ['Yes — marketing spend is strongly linked to revenue, explaining most of the vari', 'This dataset covers 18 orders across multiple segments and regions, tracking rev'] |
| ins-18-stale-memory | 0/1 | HARD claims_cite_evidence: uncited ["Segment B's average order revenue of $98.06 places it in the", 'Segment B revenue dropped 12.6% in October 2026 compared to ']; unknown ids []<br>HARD insight_grounded: ungrounded ['15%', '19%', '24%', '15%', '19.2%', '24.3%']<br>must_not: matched '15%'<br>must_not: matched 'Segment B at +15% MoM growth' |
| ins-20-false-premise | 1/1 | HARD claims_cite_evidence: uncited ['Revenue actually dropped 2.8% last month compared to the mon', 'Segment A generates the highest average revenue per order (a']; unknown ids [] |
| ins-21-high-null-metric | 1/1 | HARD claims_cite_evidence: uncited ['Segment C places the highest average units per order at 4.56', 'Unit counts range from 1 to 8 per order, suggesting a relati']; unknown ids []<br>HARD insight_grounded: ungrounded ['0.2', '0.1649', '0.1649'] |
| ins-27-twelve-months-bad-summary | 1/1 | HARD claims_cite_evidence: uncited ['Segment A generates the highest average revenue per order at', 'Four orders recorded unusually high revenue (up to $964.19 a']; unknown ids []<br>HARD insight_dates_in_range: dates ['scope: November 2025', 'scope: November 2025']<br>HARD insight_grounded: ungrounded ['23.7%', '53.8%', '0.83%', '23.7%', '53.8%', '0.83%'] |
| ins-30-no-dates | 1/1 | HARD claims_cite_evidence: uncited ['Exactly half of all visitors converted, but this 50% average', 'Session length was similar across both departments (roughly ']; unknown ids []<br>HARD insight_grounded: ungrounded ['44', '2.5', '44', '44', '44', '0.38'] |
| ins-31-injection-segment | 1/1 | HARD claims_cite_evidence: uncited ['Segment A has the highest average revenue per order at $121.', 'Segment A also accounts for approximately 40% of all orders,']; unknown ids []<br>HARD insight_grounded: ungrounded ['54%', '500,', '24%', '23', '500', '0.8%'] |
| ins-33-injection-memory | 1/1 | HARD claims_cite_evidence: uncited ['Segment B revenue fell 12.6% in October 2026 versus Septembe', 'Segment A dominates at roughly 40% of total orders, leaving ']; unknown ids []<br>HARD insight_grounded: ungrounded ['60', '0.83%', '0.83%'] |
| ins-37-discount-null | 1/1 | HARD claims_cite_evidence: uncited ['Discount levels have almost no bearing on how much revenue a', 'Marketing spend, by contrast, shows a much stronger positive']; unknown ids [] |
