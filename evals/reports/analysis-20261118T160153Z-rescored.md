# Eval report: analysis (analysis-20261118T160153Z-rescored)

model `claude-sonnet-4-6` · k=1 · commit `5eca834` · 20261118T160153Z

Rescored with the current scorer from `analysis-20261118T160153Z` (no new model calls).

| metric | value |
|---|---|
| cases | 1 |
| runs | 1 |
| pass_rate | 0.0 |
| case_majority_pass_rate | 0.0 |
| flip_rate | 0.0 |
| hard_rule_violations | 0 |
| hard_rules_na | 0 |
| errors | 1 |
| runs_with_truncated_llm_output | 0 |
| runs_with_guardrail_retry | 0 |
| runs_with_prose_rewritten | 0 |
| runs_with_pii_guard_hit | 0 |
| cost_per_question_usd | 0.0259 |
| latency_p50_s | 23.28 |
| latency_p95_s | 23.28 |
| numeric_accuracy | 0.0 |
| insight_recall | 0.0 |
| by_domain | {'logistics': {'cases': 1, 'k_min': 1, 'k_max': 1, 'cases_passed_all_runs': 0, 'pass_rate': 0.0, 'hard_rule_violations': 0, 'hard_rules_na': 0, 'builtin_only_cases': [], 'model_dependent_passed': 0, 'model_dependent_cases': 1, 'numeric_accuracy': 0.0, 'insight_recall': 0.0, 'model_dependent_quality': {'numeric_accuracy': 0.0, 'insight_recall': 0.0}}} |

## Cases

| case | passes | failing checks / hard rules / error |
|---|---|---|
| logi-8 | 0/1 | error: harness: TypeError: check_segment_value_truth() got an unexpected keyword argument 'rel_tol'<br>segment_value_truth: no findings |
