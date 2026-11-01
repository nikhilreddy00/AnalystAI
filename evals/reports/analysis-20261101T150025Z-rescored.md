# Eval report: analysis (analysis-20261101T150025Z-rescored)

model `claude-sonnet-4-6` · k=1 · commit `0ba706b` · 20261101T150025Z

Rescored with the current scorer from `analysis-20261101T150025Z` (no new model calls).

| metric | value |
|---|---|
| cases | 3 |
| runs | 3 |
| pass_rate | 0.3333 |
| case_majority_pass_rate | 0.3333 |
| flip_rate | 0.0 |
| hard_rule_violations | 0 |
| hard_rules_na | 4 |
| errors | 0 |
| runs_with_truncated_llm_output | 0 |
| runs_with_guardrail_retry | 0 |
| runs_with_prose_rewritten | 0 |
| runs_with_pii_guard_hit | 0 |
| cost_per_question_usd | 0.0258 |
| latency_p50_s | 20.98 |
| latency_p95_s | 21.17 |
| numeric_accuracy | 0.3333 |
| insight_recall | 0.3333 |
| false_finding_rate | 0.0328 |
| verifiable_claims | 183 |
| by_domain | {'healthcare': {'cases': 1, 'k_min': 1, 'k_max': 1, 'cases_passed_all_runs': 0, 'pass_rate': 0.0, 'hard_rule_violations': 0, 'hard_rules_na': 0, 'builtin_only_cases': [], 'model_dependent_passed': 0, 'model_dependent_cases': 1, 'numeric_accuracy': 0.0, 'insight_recall': 0.0, 'false_finding_rate': 0.0, 'verifiable_claims': 76, 'model_dependent_quality': {'numeric_accuracy': 0.0, 'insight_recall': 0.0, 'false_finding_rate': 0.0, 'verifiable_claims': 76}}, 'insurance': {'cases': 1, 'k_min': 1, 'k_max': 1, 'cases_passed_all_runs': 0, 'pass_rate': 0.0, 'hard_rule_violations': 0, 'hard_rules_na': 2, 'builtin_only_cases': [], 'model_dependent_passed': 0, 'model_dependent_cases': 1, 'numeric_accuracy': 0.0, 'insight_recall': 0.0, 'false_finding_rate': 0.0441, 'verifiable_claims': 68, 'model_dependent_quality': {'numeric_accuracy': 0.0, 'insight_recall': 0.0, 'false_finding_rate': 0.0441, 'verifiable_claims': 68}}, 'saas': {'cases': 1, 'k_min': 1, 'k_max': 1, 'cases_passed_all_runs': 1, 'pass_rate': 1.0, 'hard_rule_violations': 0, 'hard_rules_na': 2, 'builtin_only_cases': [], 'model_dependent_passed': 1, 'model_dependent_cases': 1, 'numeric_accuracy': 1.0, 'insight_recall': 1.0, 'false_finding_rate': 0.0769, 'verifiable_claims': 39, 'model_dependent_quality': {'numeric_accuracy': 1.0, 'insight_recall': 1.0, 'false_finding_rate': 0.0769, 'verifiable_claims': 39}}} |

## Cases

| case | passes | failing checks / hard rules / error |
|---|---|---|
| health-1 | 0/1 | segment_value_truth: no department breakdown matches readmit_rate_by_dept (tried ['length_of_stay', 'cost', 'age', 'department']) |
| ins-1 | 0/1 | false: mean claim_amount[auto (loss_ratio)]<br>false: mean claim_amount[home (loss_ratio)]<br>false: mean claim_amount[life (loss_ratio)]<br>segment_value_truth: no line breakdown matches loss_ratio_by_line (tried ['claim_amount', 'earned_premium', 'claim_count', 'claim_amount', 'earned_premium', 'claim_count', 'policy_id', 'claim_amount']) |
| saas-4 | 1/1 | false: mean churned[Pro]<br>false: mean churned[Scale]<br>false: mean churned[Starter]<br>retry: level_recomputed |
