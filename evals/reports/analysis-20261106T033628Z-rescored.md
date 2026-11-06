# Eval report: analysis (analysis-20261106T033628Z-rescored)

model `claude-sonnet-4-6` · k=3 · commit `7e65197` · 20261106T033628Z

Rescored with the current scorer from `analysis-20261106T033628Z` (no new model calls).

| metric | value |
|---|---|
| cases | 7 |
| runs | 21 |
| pass_rate | 1.0 |
| case_majority_pass_rate | 1.0 |
| flip_rate | 0.0 |
| hard_rule_violations | 0 |
| hard_rules_na | 24 |
| errors | 0 |
| runs_with_truncated_llm_output | 0 |
| runs_with_guardrail_retry | 0 |
| runs_with_prose_rewritten | 0 |
| runs_with_pii_guard_hit | 0 |
| cost_per_question_usd | 0.0242 |
| latency_p50_s | 41.67 |
| latency_p95_s | 54.47 |
| numeric_accuracy | 1.0 |
| insight_recall | 1.0 |
| false_finding_rate | 0.0 |
| verifiable_claims | 469 |
| by_domain | {'saas': {'cases': 7, 'k_min': 3, 'k_max': 3, 'cases_passed_all_runs': 7, 'pass_rate': 1.0, 'hard_rule_violations': 0, 'hard_rules_na': 24, 'builtin_only_cases': [], 'model_dependent_passed': 7, 'model_dependent_cases': 7, 'numeric_accuracy': 1.0, 'insight_recall': 1.0, 'false_finding_rate': 0.0, 'verifiable_claims': 469, 'model_dependent_quality': {'numeric_accuracy': 1.0, 'insight_recall': 1.0, 'false_finding_rate': 0.0, 'verifiable_claims': 469}}} |

## Cases

| case | passes | failing checks / hard rules / error |
|---|---|---|
| saas-10 | 3/3 | — |
| saas-11 | 3/3 | — |
| saas-5 | 3/3 | — |
| saas-6 | 3/3 | — |
| saas-7 | 3/3 | — |
| saas-8 | 3/3 | — |
| saas-9 | 3/3 | — |
