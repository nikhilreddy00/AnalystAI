# Phase 1 baseline

Model `claude-sonnet-4-6` (ingestion: `claude-haiku-4-5`), temperature 0, k=3, 2026-10-27.
Reports: `evals/reports/analysis-20261027T180935Z.*` (run 1), `evals/reports/analysis-20261027T183233Z.*`
(run 2, paired against run 1), `evals/reports/e2e-20261027T180936Z.*`.

**Run-to-run agreement gate: met.** Analysis run 2 vs run 1: +0.83 pts (39.2% → 40.0%);
1 case flipped (`segment-4`), McNemar p = 1.0. An earlier second run (18:21Z) was discarded:
the Anthropic credit balance ran out mid-run (34/120 runs got HTTP 400 before reaching the model).

## Numbers

| suite | pass rate | flip rate | runs w/ truncated LLM output | hard-rule violations | cost / question | p50 / p95 latency |
|---|---|---|---|---|---|---|
| analysis run 1 (40 × 3) | **39.2%** (26.7% non-vacuous¹) | 2.5% | 88 / 120 | 88 (all `non_empty_or_error`) | $0.044 | 35.2s / 37.1s |
| analysis run 2 (40 × 3) | **40.0%** (27.5% non-vacuous¹) | 7.5% | 87 / 120 | 87 (all `non_empty_or_error`) | $0.044 | 35.3s / 37.0s |
| e2e (20 × 3) | **45.0%** | 0% | 15 / 60 | 0 | $0.051 | 13.7s / 132.5s |

¹ The 15 `null_effect` passes are vacuous: findings were empty, so "did not claim a discount
correlation" is trivially true. Phase 3 should require non-empty findings for that check.

## Budgets (Phase 3 gates, ≤ 1.2× baseline)
- Analysis: ≤ $0.053 per question, p95 ≤ 45s.
- E2E: ≤ $0.061 per question, p95 ≤ 159s.

## What the baseline found
1. **Ingestion was broken in production.** Groq no longer serves `llama-3.3-70b-versatile`
   (`model_not_found`), so as-found E2E was 0%. Fixed in Phase 1 by moving the call onto the
   shared client per ADR-0005.
2. **Analysis truncates in ~73% of runs.** The generated code hits `max_tokens=3000`, fails to
   parse in E2B, and comes back as silent empty findings. This is the dominant failure; only
   anomaly and Simpson cases (short code) pass reliably. Every overview E2E case fails for
   this reason.
3. **Targeted segment answers skip the asked-about segment.** "Average revenue for segment B?"
   and "segment B change last month?" answer with the highest/lowest segment and never name B.
4. **Cleaning can collapse the data** (one smoke run: 3,025 → 1 rows after a near-empty
   `notes` column triggered row drops). Not separately scored yet; Phase 5 (Cleaning).

## Deviations from the plan / ADRs
- **Temperature 0** for all agents (ADR-0002 "where supported"); set before the baseline.
- **Client via `get_client()`**, not a parameter (ADR-0005). Deadline: Phase 3.
- **`max_tokens` is traced, not raised** (ADR-0005 says raise). Raising would have turned
  today's silent empty report into an error screen in 73% of analyses, a prod behavior change.
  Fix in Phase 2/3 against this baseline. **Resolved in Phase 2** (`docs/baselines/phase2.md`).
- **Ingestion moved to Claude Haiku in Phase 1** (planned for Phase 2) because Groq broke it.
  CrewAI/Groq packages are still in `pyproject.toml`; removal stays in Phase 2. **Removed in Phase 2.**
- **`tools=` / structured output** not built yet; first caller is Phase 3.
- **Fan-out join** dataset deferred to Phase 4 (SQL).
- `sql_connector.query_nl` still uses its own sync client (dead code; Phase 4 rewrites it).
