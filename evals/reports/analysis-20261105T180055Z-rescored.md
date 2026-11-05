# Eval report: analysis (analysis-20261105T180055Z-rescored)

model `claude-sonnet-4-6` · k=3 · commit `7b513d4` · 20261105T180055Z

Rescored with the current scorer from `analysis-20261105T180055Z` (no new model calls).

| metric | value |
|---|---|
| cases | 10 |
| runs | 30 |
| pass_rate | 1.0 |
| case_majority_pass_rate | 1.0 |
| flip_rate | 0.0 |
| hard_rule_violations | 0 |
| hard_rules_na | 12 |
| errors | 0 |
| runs_with_truncated_llm_output | 0 |
| runs_with_guardrail_retry | 0 |
| runs_with_prose_rewritten | 4 |
| runs_with_pii_guard_hit | 0 |
| cost_per_question_usd | 0.0232 |
| latency_p50_s | 76.86 |
| latency_p95_s | 115.93 |
| numeric_accuracy | 1.0 |
| insight_recall | 1.0 |
| false_finding_rate | 0.0217 |
| verifiable_claims | 598 |
| by_domain | {'hr': {'cases': 10, 'k_min': 3, 'k_max': 3, 'cases_passed_all_runs': 10, 'pass_rate': 1.0, 'hard_rule_violations': 0, 'hard_rules_na': 12, 'builtin_only_cases': ['hr-2', 'hr-3', 'hr-8'], 'model_dependent_passed': 7, 'model_dependent_cases': 7, 'numeric_accuracy': 1.0, 'insight_recall': 1.0, 'false_finding_rate': 0.0217, 'verifiable_claims': 598, 'model_dependent_quality': {'numeric_accuracy': 1.0, 'insight_recall': 1.0, 'false_finding_rate': 0.0181, 'verifiable_claims': 386}}} |

## Cases

| case | passes | failing checks / hard rules / error |
|---|---|---|
| hr-1 | 3/3 | — |
| hr-10 | 3/3 | — |
| hr-2 | 3/3 | retry: grounding_rewritten |
| hr-3 | 3/3 | — |
| hr-4 | 3/3 | — |
| hr-5 | 3/3 | retry: grounding_rewritten |
| hr-6 | 3/3 | false: count Employee_ID[Engineering]<br>false: count Employee_ID[Finance]<br>false: count Employee_ID[Marketing]<br>false: count Employee_ID[Operations]<br>false: count Employee_ID[Sales]<br>false: count Employee_ID[Support]<br>false: count Employee_ID[Total] |
| hr-7 | 3/3 | — |
| hr-8 | 3/3 | false: last Base_Salary[Sales]<br>false: last Base_Salary[Support] |
| hr-9 | 3/3 | — |
