# Eval report: analysis (analysis-20261104T211752Z-rescored)

model `claude-sonnet-4-6` · k=1 · commit `61bfd17` · 20261104T211752Z

Rescored with the current scorer from `analysis-20261104T211752Z` (no new model calls).

| metric | value |
|---|---|
| cases | 2 |
| runs | 2 |
| pass_rate | 1.0 |
| case_majority_pass_rate | 1.0 |
| flip_rate | 0.0 |
| hard_rule_violations | 0 |
| hard_rules_na | 2 |
| errors | 0 |
| runs_with_truncated_llm_output | 0 |
| runs_with_guardrail_retry | 0 |
| runs_with_prose_rewritten | 0 |
| runs_with_pii_guard_hit | 0 |
| cost_per_question_usd | 0.0278 |
| latency_p50_s | 28.25 |
| latency_p95_s | 30.76 |
| numeric_accuracy | 1.0 |
| insight_recall | 1.0 |
| false_finding_rate | 0.0 |
| verifiable_claims | 30 |
| by_domain | {'hr': {'cases': 2, 'k_min': 1, 'k_max': 1, 'cases_passed_all_runs': 2, 'pass_rate': 1.0, 'hard_rule_violations': 0, 'hard_rules_na': 2, 'builtin_only_cases': [], 'model_dependent_passed': 2, 'model_dependent_cases': 2, 'numeric_accuracy': 1.0, 'insight_recall': 1.0, 'false_finding_rate': 0.0, 'verifiable_claims': 30, 'model_dependent_quality': {'numeric_accuracy': 1.0, 'insight_recall': 1.0, 'false_finding_rate': 0.0, 'verifiable_claims': 30}}} |

## Cases

| case | passes | failing checks / hard rules / error |
|---|---|---|
| hr-4 | 1/1 | — |
| hr-9 | 1/1 | — |
