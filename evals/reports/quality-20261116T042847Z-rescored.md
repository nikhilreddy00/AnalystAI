# Eval report: quality (quality-20261116T042847Z-rescored)

model `claude-sonnet-4-6` · k=3 · commit `84f157f` · 20261116T042847Z

Rescored with the current scorer from `quality-20261116T042847Z` (no new model calls).

| metric | value |
|---|---|
| cases | 1 |
| runs | 3 |
| pass_rate | 0.0 |
| case_majority_pass_rate | 0.0 |
| flip_rate | 0.0 |
| hard_rule_violations | 0 |
| hard_rules_na | 0 |
| errors | 0 |
| runs_with_truncated_llm_output | 0 |
| runs_with_guardrail_retry | 0 |
| runs_with_prose_rewritten | 0 |
| runs_with_pii_guard_hit | 0 |
| cost_per_question_usd | 0.0084 |
| latency_p50_s | 8.13 |
| latency_p95_s | 9.28 |
| defect_recall | 1.0 |
| false_alarm_rate | 0.1 |
| seeded_defects | 3 |
| supported_claims | 30 |
| recall_by_kind | {'semantic': 1.0} |
| recall_by_source | {'check': 1.0, 'critic': 0.0} |
| label_agreement_on_caught | 0.0 |
| natural_recall | None |
| false_alarms_on_actions | 0.3333 |
| clean_reports_with_false_alarm | None |
| hard_review_grounded | 1.0 |
| hard_flags_valid | 1.0 |
| hard_no_injection_echo | 1.0 |
| hard_bundle_reproduced | 1.0 |
| holdout_pass_rate | None |

## Cases

| case | passes | failing checks / hard rules / error |
|---|---|---|
| q-31-injection-segment | 0/3 | false_alarm: flagged ['check'] (states a cause or grades a result against a norm, which this): Prioritise acquisition and retention efforts in Segment A to |
