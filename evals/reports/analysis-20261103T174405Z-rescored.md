# Eval report: analysis (analysis-20261103T174405Z-rescored)

model `claude-sonnet-4-6` · k=1 · commit `7737d5d` · 20261103T174405Z

Rescored with the current scorer from `analysis-20261103T174405Z` (no new model calls).

| metric | value |
|---|---|
| cases | 3 |
| runs | 3 |
| pass_rate | 0.6667 |
| case_majority_pass_rate | 0.6667 |
| flip_rate | 0.0 |
| hard_rule_violations | 0 |
| hard_rules_na | 0 |
| errors | 1 |
| runs_with_truncated_llm_output | 0 |
| runs_with_guardrail_retry | 0 |
| runs_with_prose_rewritten | 0 |
| runs_with_pii_guard_hit | 0 |
| cost_per_question_usd | 0.017 |
| latency_p50_s | 25.09 |
| latency_p95_s | 38.36 |
| numeric_accuracy | 0.6667 |
| insight_recall | 0.6667 |
| false_finding_rate | 0.0 |
| verifiable_claims | 65 |
| by_domain | {'healthcare': {'cases': 3, 'k_min': 1, 'k_max': 1, 'cases_passed_all_runs': 2, 'pass_rate': 0.6667, 'hard_rule_violations': 0, 'hard_rules_na': 0, 'builtin_only_cases': [], 'model_dependent_passed': 2, 'model_dependent_cases': 3, 'numeric_accuracy': 0.6667, 'insight_recall': 0.6667, 'false_finding_rate': 0.0, 'verifiable_claims': 65, 'model_dependent_quality': {'numeric_accuracy': 0.6667, 'insight_recall': 0.6667, 'false_finding_rate': 0.0, 'verifiable_claims': 65}}} |

## Cases

| case | passes | failing checks / hard rules / error |
|---|---|---|
| health-4 | 0/1 | error: harness: ArrowInvalid: ("Could not convert 'Other (fewer than 11 each)' with type str: tried to convert to double", 'Con<br>segment_value_truth: no findings |
| health-6 | 1/1 | — |
| health-7 | 1/1 | — |
