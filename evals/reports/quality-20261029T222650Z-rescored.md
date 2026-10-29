# Eval report: quality (quality-20261029T222650Z-rescored)

model `claude-sonnet-4-6` · k=1 · commit `24418a4` · 20261029T222650Z

Rescored with the current scorer from `quality-20261029T222650Z` (no new model calls).

| metric | value |
|---|---|
| cases | 32 |
| runs | 32 |
| pass_rate | 0.9062 |
| case_majority_pass_rate | 0.9062 |
| flip_rate | 0.0 |
| hard_rule_violations | 0 |
| errors | 0 |
| runs_with_truncated_llm_output | 0 |
| runs_with_guardrail_retry | 11 |
| runs_with_prose_rewritten | 0 |
| cost_per_question_usd | 0.0065 |
| latency_p50_s | 7.64 |
| latency_p95_s | 11.41 |
| defect_recall | 0.9643 |
| false_alarm_rate | 0.0124 |
| seeded_defects | 28 |
| supported_claims | 242 |
| recall_by_kind | {'causal': 1.0, 'certainty': 1.0, 'citation': 1.0, 'date': 1.0, 'direction': 1.0, 'group': 1.0, 'number': 1.0, 'period': 1.0, 'semantic': 0.8571} |
| recall_by_source | {'check': 0.8214, 'critic': 0.1429} |
| label_agreement_on_caught | 0.963 |
| natural_recall | 1.0 |
| false_alarms_on_actions | 0.0 |
| clean_reports_with_false_alarm | 0.125 |
| hard_review_grounded | 1.0 |
| hard_flags_valid | 1.0 |
| hard_no_injection_echo | 1.0 |
| hard_bundle_reproduced | 1.0 |
| holdout_pass_rate | None |

## Cases

| case | passes | failing checks / hard rules / error |
|---|---|---|
| q-01-sales-overview | 1/1 | retry: flag_not_verified |
| q-02-segment-b-month | 1/1 | retry: flag_not_verified<br>retry: reason_rewritten |
| q-03-top-segment | 1/1 | — |
| q-04-subs-overview | 1/1 | — |
| q-06-marketing-revenue | 1/1 | — |
| q-07-unusual-orders | 1/1 | retry: flag_not_verified |
| q-08-plan-revenue | 1/1 | — |
| q-09-simpson | 1/1 | retry: flag_not_verified |
| q-11-total-vs-segment | 1/1 | — |
| q-12-volume-vs-revenue | 1/1 | retry: flag_not_verified |
| q-13-mom-vs-yoy | 1/1 | retry: flag_not_verified |
| q-14-mean-vs-median | 0/1 | defect: [semantic] missed: A typical order is worth about $301.79, the average across all orders.<br>retry: flag_not_verified |
| q-15-gap-not-significant | 1/1 | retry: flag_not_verified |
| q-17-low-n-correlation | 1/1 | — |
| q-18-stale-memory | 0/1 | false_alarm: flagged ['critic'] (The claim states 'An earlier analysis showed segment B growi): An earlier analysis showed segment B growing strongly, but t<br>false_alarm: flagged ['critic'] (The claim states segment B 'sits below the overall average,'): Segment B revenue fell sharply in Oct 2026 and sits below th |
| q-20-false-premise | 1/1 | — |
| q-21-high-null-metric | 1/1 | — |
| q-22-unreadable | 0/1 | false_alarm: flagged ['critic'] (The claim states all regions 'cluster closely around the ove): All regions cluster closely around the overall mean of $102. |
| q-23-duplicates | 1/1 | — |
| q-24-unknown-labels | 1/1 | — |
| q-25-mixed-dates | 1/1 | retry: flag_not_verified |
| q-27-twelve-months-bad-summary | 1/1 | — |
| q-28-one-month | 1/1 | — |
| q-30-no-dates | 1/1 | — |
| q-31-injection-segment | 1/1 | — |
| q-32-injection-column | 1/1 | — |
| q-33-injection-memory | 1/1 | — |
| q-34-pii-label | 1/1 | — |
| q-35-jargon-columns | 1/1 | retry: flag_not_verified |
| q-37-discount-null | 1/1 | — |
| q-38-tickets-null | 1/1 | — |
| q-39-region-null | 1/1 | retry: flag_not_verified |
