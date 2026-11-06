# Eval report: analysis (analysis-20261106T011019Z-rescored)

model `claude-sonnet-4-6` · k=3 · commit `a44f0b2` · 20261106T011019Z

Rescored with the current scorer from `analysis-20261106T011019Z` (no new model calls).

| metric | value |
|---|---|
| cases | 11 |
| runs | 33 |
| pass_rate | 0.8485 |
| case_majority_pass_rate | 0.9091 |
| flip_rate | 0.1818 |
| hard_rule_violations | 0 |
| hard_rules_na | 48 |
| errors | 1 |
| runs_with_truncated_llm_output | 0 |
| runs_with_guardrail_retry | 1 |
| runs_with_prose_rewritten | 2 |
| runs_with_pii_guard_hit | 0 |
| cost_per_question_usd | 0.0264 |
| latency_p50_s | 30.43 |
| latency_p95_s | 43.48 |
| numeric_accuracy | 0.8485 |
| insight_recall | 0.8485 |
| false_finding_rate | 0.0 |
| verifiable_claims | 923 |
| by_domain | {'saas': {'cases': 11, 'k_min': 3, 'k_max': 3, 'cases_passed_all_runs': 8, 'pass_rate': 0.8485, 'hard_rule_violations': 0, 'hard_rules_na': 48, 'builtin_only_cases': ['saas-1', 'saas-2', 'saas-3', 'saas-4'], 'model_dependent_passed': 4, 'model_dependent_cases': 7, 'numeric_accuracy': 0.8485, 'insight_recall': 0.8485, 'false_finding_rate': 0.0, 'verifiable_claims': 923, 'model_dependent_quality': {'numeric_accuracy': 0.7619, 'insight_recall': 0.7619, 'false_finding_rate': 0.0, 'verifiable_claims': 587}}} |

## Cases

| case | passes | failing checks / hard rules / error |
|---|---|---|
| saas-1 | 3/3 | — |
| saas-10 | 0/3 | segment_value_truth: no segment breakdown matches logo_churn_by_segment (tried ['cancellation_in_window', 'customer_id', 'customer_id', 'mrr'])<br>segment_value_truth: no segment breakdown matches logo_churn_by_segment (tried ['customer_id', 'customer_id', 'customer_id', 'mrr'])<br>segment_value_truth: no segment breakdown matches logo_churn_by_segment (tried ['customer_id', 'mrr', 'customer_id']) |
| saas-11 | 2/3 | error: GuardrailViolation: analysis: code does not parse: unterminated string literal (detected at line 1) (line 1)<br>retry: code_guard<br>retry: empty<br>segment_value_truth: no findings |
| saas-2 | 3/3 | — |
| saas-3 | 3/3 | retry: grounding_rewritten |
| saas-4 | 3/3 | — |
| saas-5 | 3/3 | — |
| saas-6 | 3/3 | — |
| saas-7 | 3/3 | — |
| saas-8 | 2/3 | retry: level_recomputed<br>segment_value_truth: no ['month', 'Month', 'churn_month', 'period'] breakdown matches churned_mrr_by_month_window (tried ['mrr']) |
| saas-9 | 3/3 | retry: breakdown_recomputed<br>retry: model_trend_labelled |
