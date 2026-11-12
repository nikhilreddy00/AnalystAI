# Eval report: analysis (analysis-20261106T181002Z-rescored)

model `claude-sonnet-4-6` · k=1 · commit `b4a8b54` · 20261106T181002Z

Rescored with the current scorer from `analysis-20261106T181002Z` (no new model calls).

| metric | value |
|---|---|
| cases | 3 |
| runs | 3 |
| pass_rate | 1.0 |
| case_majority_pass_rate | 1.0 |
| flip_rate | 0.0 |
| hard_rule_violations | 0 |
| hard_rules_na | 4 |
| errors | 0 |
| runs_with_truncated_llm_output | 0 |
| runs_with_guardrail_retry | 0 |
| runs_with_prose_rewritten | 0 |
| runs_with_pii_guard_hit | 0 |
| cost_per_question_usd | 0.0287 |
| latency_p50_s | 48.26 |
| latency_p95_s | 52.53 |
| numeric_accuracy | 1.0 |
| insight_recall | 1.0 |
| false_finding_rate | 0.0 |
| verifiable_claims | 60 |
| by_domain | {'saas': {'cases': 3, 'k_min': 1, 'k_max': 1, 'cases_passed_all_runs': 3, 'pass_rate': 1.0, 'hard_rule_violations': 0, 'hard_rules_na': 4, 'builtin_only_cases': ['saas-1'], 'model_dependent_passed': 2, 'model_dependent_cases': 2, 'numeric_accuracy': 1.0, 'insight_recall': 1.0, 'false_finding_rate': 0.0, 'verifiable_claims': 60, 'model_dependent_quality': {'numeric_accuracy': 1.0, 'insight_recall': 1.0, 'false_finding_rate': 0.0, 'verifiable_claims': 36}}} |

## Cases

| case | passes | failing checks / hard rules / error |
|---|---|---|
| saas-1 | 1/1 | — |
| saas-10 | 1/1 | — |
| saas-11 | 1/1 | — |
