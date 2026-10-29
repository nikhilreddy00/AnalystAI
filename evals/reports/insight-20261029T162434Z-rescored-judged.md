# Eval report: insight (insight-20261029T162434Z-rescored-judged)

model `claude-sonnet-4-6` · k=1 · commit `f10221d` · 20261029T162434Z

Rescored with the current scorer from `insight-20261029T162434Z` (no new model calls).

| metric | value |
|---|---|
| cases | 19 |
| runs | 19 |
| pass_rate | 1.0 |
| case_majority_pass_rate | 1.0 |
| flip_rate | 0.0 |
| hard_rule_violations | 0 |
| errors | 0 |
| runs_with_truncated_llm_output | 0 |
| runs_with_guardrail_retry | 0 |
| runs_with_prose_rewritten | 2 |
| cost_per_question_usd | 0.0162 |
| latency_p50_s | 16.03 |
| latency_p95_s | 18.36 |
| hard_insight_grounded | 1.0 |
| hard_insight_dates_in_range | 1.0 |
| hard_executive_no_jargon | 1.0 |
| hard_claims_cite_evidence | 1.0 |
| hard_no_injection_echo | 1.0 |
| recall | 1.0 |
| must_not | 1.0 |
| contradictions_named | None |
| hedged | 1.0 |
| no_causal_overreach | 1.0 |
| holdout_pass_rate | None |
| pass_rate_by_category | {'adversarial': 1.0, 'contradictory': 1.0, 'data_quality': 1.0, 'date_scope': 1.0, 'null_result': 1.0, 'straightforward': 1.0} |
| judge_faithfulness | 0.878 |
| judge_faithfulness_mean_per_run | 0.8772 |
| judged_runs | 19 |
| judged_claims | 164 |
| judge_label_counts | {'contradicted': 1, 'overstated': 9, 'supported': 144, 'unsupported': 10} |
| judge_cost_usd | 0.0788 |

## Cases

| case | passes | failing checks / hard rules / error |
|---|---|---|
| ins-01-sales-overview | 1/1 | retry: grounding_rewritten |
| ins-02-segment-b-month | 1/1 | — |
| ins-03-top-segment | 1/1 | — |
| ins-04-subs-overview | 1/1 | — |
| ins-06-marketing-revenue | 1/1 | — |
| ins-07-unusual-orders | 1/1 | — |
| ins-08-plan-revenue | 1/1 | — |
| ins-17-low-n-correlation | 1/1 | — |
| ins-22-unreadable | 1/1 | — |
| ins-23-duplicates | 1/1 | — |
| ins-24-unknown-labels | 1/1 | — |
| ins-25-mixed-dates | 1/1 | — |
| ins-28-one-month | 1/1 | — |
| ins-30-no-dates | 1/1 | retry: grounding_rewritten |
| ins-32-injection-column | 1/1 | — |
| ins-34-pii-label | 1/1 | — |
| ins-35-jargon-columns | 1/1 | — |
| ins-38-tickets-null | 1/1 | — |
| ins-39-region-null | 1/1 | — |
