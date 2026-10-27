# Phase 2 review: Reliability foundations

Model `claude-sonnet-4-6` (ingestion: `claude-haiku-4-5`), temperature 0, k=3, 2026-10-27.
Final reports: `evals/reports/analysis-20261027T194402Z.*` and `evals/reports/e2e-20261027T194402Z.*`,
each paired against the Phase 1 baseline (`analysis-20261027T183233Z`, `e2e-20261027T180936Z`).

## Numbers (final run, analysis prompt v2)

| suite | Phase 1 | Phase 2 | paired diff |
|---|---|---|---|
| analysis pass rate | 40.0% | **87.5%** | +47.5 pts; 20 fixed, 0 broke, McNemar p ≈ 0 |
| analysis flip rate | 7.5% | 0% | |
| analysis runs with truncated output | 87 / 120 | **0 / 120** | |
| analysis hard-rule violations | 87 | **0** | |
| analysis cost / question · p95 | $0.044 · 37.0s | **$0.027 · 33.0s** | within the Phase 3 budget (≤ $0.053, ≤ 45s) |
| **e2e pass rate (gate ≥ 45.0%)** | 45.0% | **45.0%** | 0 fixed, 0 broke |
| e2e truncated runs · hard-rule violations | 15 / 60 · 0 | 0 / 60 · 0 | |
| e2e cost / question · p95 | $0.051 · 132.5s | $0.049 · 132.1s | |

The 20 fixed analysis cases are aggregation, correlation, robustness and segment, all 5 of each.
They failed at baseline only because truncated code produced empty findings.

**Why E2E didn't move:** all 11 failing E2E cases are the two known baseline findings, and
neither is in the analysis agent.
- 5 overview cases (e2e-01…05) fail because **cleaning collapses the data to 1 row** (finding
  #4). An all-but-empty `notes` column gets `drop_rows`, so the mean is computed on one row.
  This is Phase 5 (Cleaning).
- 6 targeted cases (e2e-06…08, 12…14) fail because **targeted answers name the top/bottom
  segment, not the one asked about** (finding #3). This is Phase 5 (Intake → targeted params).

## Phase 2 "done when"
- **Fault injection:** done. `tests/test_fault_injection.py` covers malformed JSON, an invented
  column, refusal, `max_tokens`, `DELETE`, `import os`, an injected cell with PII, sandbox
  timeout and API timeout, plus graph → SSE `error`. Each yields its typed error or block, never
  empty output.
- **Zero bare `except ...: pass` in the orchestrator:** done. An AST test enforces it in CI.
- **E2E not below baseline:** 45.0% = 45.0%.
- 97 offline tests pass; ruff and tsc are clean.

## What was built
- **Typed errors** (`agents/errors.py`): `AgentError` → `LLMError` (`LLMRefusal`, `LLMTruncated`,
  `LLMUnavailable`, `LLMOutputError`), `GuardrailViolation`, `SandboxError`.
  - The client raises on refusal and on `max_tokens`, and maps timeouts, connection errors, 429
    and 5xx to `LLMUnavailable`.
  - The graph's `_fail()` puts `user_message` on the SSE `error` event.
  - Targeted and drill-down failures now reach `error_node`; they used to end silently with no
    error event.
- **`max_tokens`:** the analysis cap went from 3000 to 8000, and the prompt asks for compact
  code. Typed truncation is enforced for every agent. The Phase 1 traces showed only analysis
  ever truncated: 269/363 calls, while every other agent stayed under 61% of its cap.
- **Guardrails** (`guardrails.py`, pure functions):
  - an AST import allowlist, plus banned reflection and I/O names, including
    `from pandas import read_csv`;
  - a check that `df['…']` column references exist;
  - a read-only SQL check parsed with sqlglot;
  - numeric grounding;
  - PII detection and masking;
  - `<untrusted_data>` delimiting.

  How they're wired in:
  - PII is masked once in `build_data_contract`, the source of every prompt's samples, and in
    insight's head rows.
  - Analysis gets one guided retry after a guardrail rejection, then raises.
  - Intake drops invented columns.
  - E2B now runs **without internet access**.
- **senior_expert folded into the quality check.** `quality_check_node` runs the critic
  (`agents/quality_critic.py`) on first runs, fed by the deterministic grounding check. Grounding
  is soft for now: it informs the critic and doesn't trigger retries. One node and one routing
  branch fewer; the `expert_review` event is unchanged.
- **Drill-down finished:** chart data is computed from the df (it was `data=[]`).
- **CrewAI, Groq and Vanna removed** (the lock file lost about 2,300 lines). NL→SQL now runs on
  the shared client (it is still dead code until Phase 4).

## Deviations and things to know
- **Three eval rounds, not one.** Earlier reports are committed for transparency:
  - Round 1 (`*-192207Z`): analysis 85.8%, E2E 45.0%. It found two issues:
    - E2E: the insight agent flagged the injected cell but **quoted it verbatim**, giving 4
      `no_injection_echo` violations. Fix: the untrusted-data instruction now says to name
      embedded instructions, never quote them.
    - Analysis: 14 `no_injection_echo` hits, all in `distributions`/`head_preview`. Those fields
      copy raw cells for the charts and no model reads them. Baseline never reached them because
      truncation emptied every run. **Scorer change:** the analysis-suite echo scan now excludes
      raw-data fields (`evals/scoring.py`). Pass rate is unaffected, since hard rules don't feed
      it.
  - Round 2 (`*-193448Z`): analysis 85.0%; `simpson-4` broke because 2/3 runs used `getattr`, and
    the guided retry repeated it. Fix: allow literal `getattr`, and **state the sandbox rules in
    the prompt (v2)** rather than teaching them only by rejection.
  - Round 3 (final, above): 0 broke, 0 errors.
- Grounding is soft (critic input only). It becomes a hard rule for Insight in Phase 5.
- `ADR-0005`'s "client as a parameter" is still `get_client()`; the deadline is Phase 3.
- The CI eval job hasn't been exercised with secrets. Evals were run locally.
