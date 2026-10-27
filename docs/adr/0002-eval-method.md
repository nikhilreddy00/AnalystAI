# ADR-0002: Evals use seeded synthetic data with planted truths, scored by result, run k=3 and compared per case

- Status: Proposed (Phase 0, 2026-10-27)
- Gaps addressed: G9 (and the "Done when" criteria in every later phase)

## Context
There are no evals today. Industry practice:
- Every serious team scores against a golden set [P1, P2, P3, P6].
- Exact-match SQL is rejected because many queries can be valid. Cortex and Genie compare
  **result sets** [P3, P5, P6].
- Uber separates end-to-end and component ("decoupled") evals and treats ~5% run-to-run
  variation as noise [P1].
- Genie writes benchmarks before building and never feeds them to the agent [P6].

## Decision
1. **Data**: a seeded generator (`evals/datagen.py`) builds tables with planted truths: a 12% MoM
   drop in one segment, a confounded correlation, a null-effect column, duplicate rows, a fan-out
   join, mixed date formats, PII columns, and an injected "ignore previous instructions" cell.
   Ground truth is exact, so most cases need no judge.
2. **Case format**: one YAML file per case with `dataset seed`, `question`, `expected` (exact
   values with a relative tolerance, expected columns, must-not-claim list) and `hard_rules`.
   Golden cases are never used as prompt examples. Verified queries in the semantic layer are a
   separate set.
3. **Two levels**, as at Uber: per-agent sets (Analysis 40, growing to 100+) and an end-to-end set
   (20 questions, growing to 50).
4. **Scoring**: numeric values match within tolerance; SQL is scored by result-set equality;
   insights by planted-truth recall and false-finding rate. An LLM judge is used only for narrative
   quality, and only after it agrees with human labels on 30 cases at ≥85%.
5. **Statistics**:
   - Each case runs **k=3** times. Temperature 0 is used where the model supports it.
   - The report gives the mean pass rate and the flip rate.
   - Comparisons between runs are **paired per case**: list the cases that flipped and run
     McNemar's test.
   - Why: at n=40 and p=0.8, the 95% CI is about ±12 points, too wide for unpaired comparison.
6. **Report**: committed JSON plus Markdown with per-case pass/fail, hard-rule violations, cost per
   question, p50/p95 latency, and the diff against the previous run.
7. **Gates**:
   - Hard rules must hold 100%.
   - A target is set from the first baseline and never set below it.
   - CI runs deterministic tests on every push. Evals run on demand or nightly.

## Consequences
- The baseline must exist before any agent changes (Phase 1).
- Synthetic data can miss real-world mess. Mitigation: the user reviews every golden case, and
  later we add a small set of anonymized real tables.

---
Source keys ([P1], [S6], …) refer to the Sources list in `docs/research/2026-10-27-data-agents.md`.
