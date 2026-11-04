# Eval report: analysis (analysis-20261104T211227Z-rescored)

model `claude-sonnet-4-6` · k=1 · commit `1a651dc` · 20261104T211227Z

Rescored with the current scorer from `analysis-20261104T211227Z` (no new model calls).

| metric | value |
|---|---|
| cases | 2 |
| runs | 2 |
| pass_rate | 0.0 |
| case_majority_pass_rate | 0.0 |
| flip_rate | 0.0 |
| hard_rule_violations | 0 |
| hard_rules_na | 2 |
| errors | 0 |
| runs_with_truncated_llm_output | 0 |
| runs_with_guardrail_retry | 1 |
| runs_with_prose_rewritten | 0 |
| runs_with_pii_guard_hit | 0 |
| cost_per_question_usd | 0.0505 |
| latency_p50_s | 42.67 |
| latency_p95_s | 54.0 |
| numeric_accuracy | 0.0 |
| insight_recall | 0.0 |
| false_finding_rate | 0.0 |
| verifiable_claims | 30 |
| by_domain | {'hr': {'cases': 2, 'k_min': 1, 'k_max': 1, 'cases_passed_all_runs': 0, 'pass_rate': 0.0, 'hard_rule_violations': 0, 'hard_rules_na': 2, 'builtin_only_cases': [], 'model_dependent_passed': 0, 'model_dependent_cases': 2, 'numeric_accuracy': 0.0, 'insight_recall': 0.0, 'false_finding_rate': 0.0, 'verifiable_claims': 30, 'model_dependent_quality': {'numeric_accuracy': 0.0, 'insight_recall': 0.0, 'false_finding_rate': 0.0, 'verifiable_claims': 30}}} |

## Cases

| case | passes | failing checks / hard rules / error |
|---|---|---|
| hr-4 | 0/1 | retry: finding_columns<br>segment_value_truth: no Department breakdown matches attrition_12m_by_dept (tried ['exit_event_date', 'exit_event_date']) |
| hr-9 | 0/1 | segment_value_truth: no Department breakdown matches attrition_12m_by_dept_at_exit (tried ['exit_event_date', 'exit_event_date']) |
