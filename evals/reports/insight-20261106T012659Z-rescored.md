# Eval report: insight (insight-20261106T012659Z-rescored)

model `claude-sonnet-4-6` · k=3 · commit `9355a3b` · 20261106T012659Z

Rescored with the current scorer from `insight-20261106T012659Z` (no new model calls).

| metric | value |
|---|---|
| cases | 2 |
| runs | 6 |
| pass_rate | 1.0 |
| case_majority_pass_rate | 1.0 |
| flip_rate | 0.0 |
| hard_rule_violations | 0 |
| hard_rules_na | 0 |
| errors | 0 |
| runs_with_truncated_llm_output | 0 |
| runs_with_guardrail_retry | 1 |
| runs_with_prose_rewritten | 2 |
| runs_with_pii_guard_hit | 0 |
| cost_per_question_usd | 0.019 |
| latency_p50_s | 23.68 |
| latency_p95_s | 25.07 |
| hard_insight_grounded | 1.0 |
| hard_insight_dates_in_range | 1.0 |
| hard_executive_no_jargon | 1.0 |
| hard_claims_cite_evidence | 1.0 |
| hard_no_injection_echo | 1.0 |
| recall | 1.0 |
| must_not | 1.0 |
| contradictions_named | None |
| hedged | 1.0 |
| no_causal_overreach | 1.0 |
| holdout_pass_rate | 1.0 |
| pass_rate_by_category | {'contradictory': 1.0, 'straightforward': 1.0} |
| by_domain | {'saas': {'cases': 1, 'k_min': 3, 'k_max': 3, 'cases_passed_all_runs': 1, 'pass_rate': 1.0, 'hard_rule_violations': 0, 'hard_rules_na': 0, 'builtin_only_cases': [], 'model_dependent_passed': 0, 'model_dependent_cases': 0}} |

## Cases

| case | passes | failing checks / hard rules / error |
|---|---|---|
| ins-16-tickets-not-significant-holdout | 3/3 | retry: grounding_rewritten |
| ins-49-saas-subscriptions-overview | 3/3 | retry: causal_removed |
