# Eval report: analysis (analysis-20261101T153341Z-rescored)

model `claude-sonnet-4-6` · k=3 · commit `52ee105` · 20261101T153341Z

Rescored with the current scorer from `analysis-20261101T153341Z` (no new model calls).

| metric | value |
|---|---|
| cases | 22 |
| runs | 66 |
| pass_rate | 0.9697 |
| case_majority_pass_rate | 1.0 |
| flip_rate | 0.0909 |
| hard_rule_violations | 0 |
| hard_rules_na | 96 |
| errors | 0 |
| runs_with_truncated_llm_output | 0 |
| runs_with_guardrail_retry | 0 |
| runs_with_prose_rewritten | 5 |
| runs_with_pii_guard_hit | 0 |
| cost_per_question_usd | 0.0153 |
| latency_p50_s | 17.02 |
| latency_p95_s | 22.72 |
| numeric_accuracy | 0.9667 |
| insight_recall | 0.9608 |
| false_finding_rate | 0.0046 |
| verifiable_claims | 1723 |
| by_domain | {'banking': {'cases': 3, 'k_min': 3, 'k_max': 3, 'cases_passed_all_runs': 3, 'pass_rate': 1.0, 'hard_rule_violations': 0, 'hard_rules_na': 18, 'builtin_only_cases': ['bank-1', 'bank-2'], 'model_dependent_passed': 1, 'model_dependent_cases': 1, 'numeric_accuracy': 1.0, 'insight_recall': 1.0, 'false_finding_rate': 0.0, 'verifiable_claims': 122, 'model_dependent_quality': {'insight_recall': 1.0, 'false_finding_rate': 0.0, 'verifiable_claims': 36}}, 'healthcare': {'cases': 3, 'k_min': 3, 'k_max': 3, 'cases_passed_all_runs': 3, 'pass_rate': 1.0, 'hard_rule_violations': 0, 'hard_rules_na': 0, 'builtin_only_cases': ['health-2'], 'model_dependent_passed': 2, 'model_dependent_cases': 2, 'numeric_accuracy': 1.0, 'insight_recall': 1.0, 'false_finding_rate': 0.0, 'verifiable_claims': 216, 'model_dependent_quality': {'numeric_accuracy': 1.0, 'insight_recall': 1.0, 'false_finding_rate': 0.0, 'verifiable_claims': 162}}, 'hr': {'cases': 3, 'k_min': 3, 'k_max': 3, 'cases_passed_all_runs': 3, 'pass_rate': 1.0, 'hard_rule_violations': 0, 'hard_rules_na': 0, 'builtin_only_cases': ['hr-2', 'hr-3'], 'model_dependent_passed': 1, 'model_dependent_cases': 1, 'numeric_accuracy': 1.0, 'insight_recall': 1.0, 'false_finding_rate': 0.0, 'verifiable_claims': 291, 'model_dependent_quality': {'numeric_accuracy': 1.0, 'insight_recall': 1.0, 'false_finding_rate': 0.0, 'verifiable_claims': 132}}, 'insurance': {'cases': 3, 'k_min': 3, 'k_max': 3, 'cases_passed_all_runs': 3, 'pass_rate': 1.0, 'hard_rule_violations': 0, 'hard_rules_na': 18, 'builtin_only_cases': ['ins-3'], 'model_dependent_passed': 2, 'model_dependent_cases': 2, 'numeric_accuracy': 1.0, 'insight_recall': 1.0, 'false_finding_rate': 0.0, 'verifiable_claims': 260, 'model_dependent_quality': {'numeric_accuracy': 1.0, 'insight_recall': 1.0, 'false_finding_rate': 0.0, 'verifiable_claims': 203}}, 'iot': {'cases': 3, 'k_min': 3, 'k_max': 3, 'cases_passed_all_runs': 2, 'pass_rate': 0.8889, 'hard_rule_violations': 0, 'hard_rules_na': 18, 'builtin_only_cases': ['iot-1', 'iot-3'], 'model_dependent_passed': 0, 'model_dependent_cases': 1, 'numeric_accuracy': 0.8889, 'insight_recall': 0.8889, 'false_finding_rate': 0.0266, 'verifiable_claims': 188, 'model_dependent_quality': {'numeric_accuracy': 0.6667, 'insight_recall': 0.6667, 'false_finding_rate': 0.0417, 'verifiable_claims': 72}}, 'logistics': {'cases': 3, 'k_min': 3, 'k_max': 3, 'cases_passed_all_runs': 3, 'pass_rate': 1.0, 'hard_rule_violations': 0, 'hard_rules_na': 18, 'builtin_only_cases': ['logi-1', 'logi-3'], 'model_dependent_passed': 1, 'model_dependent_cases': 1, 'numeric_accuracy': 1.0, 'insight_recall': 1.0, 'false_finding_rate': 0.0, 'verifiable_claims': 316, 'model_dependent_quality': {'numeric_accuracy': 1.0, 'false_finding_rate': 0.0, 'verifiable_claims': 118}}, 'saas': {'cases': 4, 'k_min': 3, 'k_max': 3, 'cases_passed_all_runs': 3, 'pass_rate': 0.9167, 'hard_rule_violations': 0, 'hard_rules_na': 24, 'builtin_only_cases': ['saas-1', 'saas-2', 'saas-3'], 'model_dependent_passed': 0, 'model_dependent_cases': 1, 'numeric_accuracy': 0.9167, 'insight_recall': 0.9167, 'false_finding_rate': 0.0091, 'verifiable_claims': 330, 'model_dependent_quality': {'numeric_accuracy': 0.6667, 'insight_recall': 0.6667, 'false_finding_rate': 0.0323, 'verifiable_claims': 93}}} |

## Cases

| case | passes | failing checks / hard rules / error |
|---|---|---|
| bank-1 | 3/3 | — |
| bank-2 | 3/3 | retry: level_recomputed |
| bank-3 | 3/3 | — |
| health-1 | 3/3 | — |
| health-2 | 3/3 | — |
| health-3 | 3/3 | — |
| hr-1 | 3/3 | — |
| hr-2 | 3/3 | retry: grounding_rewritten |
| hr-3 | 3/3 | — |
| ins-1 | 3/3 | — |
| ins-2 | 3/3 | — |
| ins-3 | 3/3 | — |
| iot-1 | 3/3 | — |
| iot-2 | 2/3 | false: mean vibration[A]<br>false: mean vibration[B]<br>false: mean vibration[C]<br>segment_value: mean vibration[A]: got 1.0052249817101333, want 2.0168 |
| iot-3 | 3/3 | false: mean vibration[failure=0]<br>false: mean vibration[failure=1]<br>retry: grounding_rewritten |
| logi-1 | 3/3 | — |
| logi-2 | 3/3 | — |
| logi-3 | 3/3 | — |
| saas-1 | 3/3 | — |
| saas-2 | 3/3 | retry: level_recomputed |
| saas-3 | 3/3 | — |
| saas-4 | 2/3 | false: mean seats[Pro]<br>false: mean seats[Scale]<br>false: mean seats[Starter]<br>segment_value: mean seats[Scale]: got 44.5, want 38.5185 |
