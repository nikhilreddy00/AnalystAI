# Phase 5, PR 5 review: Quality check on the agent contract

Card `quality v3` (critic) + `verify_v2`, Haiku 4.5, 2026-10-29, branch `phase-5-quality`.
- **Final quality report: `evals/reports/quality-20261029T224558Z.*`**: 40 golden reports (20
  seeded, 102 seeded-defect instances over k=3; 8 holdout first seen in the final), k=3, 120 runs.
  Paired against the old critic `quality-20261029T183812Z` (20 stratified reports, k=1) and its hand
  score `quality-20261029T183812Z-handscore.yaml`.
- **E2E gates:** `e2e-20261029T224155Z` (all 20, k=1) and `e2e-20261029T224840Z` (the 5 overviews,
  k=1, after the truncation fix). The checks were changed once more after that, see Deviations.
- **Spend: $4.05** (under the $5 cap). One run broke the >30% rule: the overview re-gate cost $0.58
  against a $0.25 estimate. Paid runs stopped there.

## Numbers

| metric (ROADMAP Quality row) | target | old quality check | **final (k=3, 120 runs)** |
|---|---|---|---|
| **defect recall** (a flag lands on the seeded claim) | ≥ 90% | 26.5% (code half, all 40) · 31.6% (old critic, hand-scored 6/19) | **97.1%** (99/102) |
| **false alarms** (supported claims flagged) | ≤ 10% | 12.2% (code half; years read as numbers) | **0.67%** (6/900) |
| clean reports with any false alarm | | 35% | **5%** |
| hard: review numbers/dates grounded | 100% | 15% of old reviews | **100%** |
| hard: flags point at real claims and ids | 100% | — | **100%** |
| hard: no injection echo · bundle reproduced | 100% | 95% · 100% | **100% · 100%** |
| natural defect recall (ins-02, hand-labelled) | | 0% | **100%** |
| recall by kind | | | 100% on all 8 rule kinds; semantic 88.9% |
| recall by source | | | checks 82.4%, critic 14.7% |
| label agreement on caught defects | | | 97.0% |
| holdout pass rate | | | 87.5% (q-19, below) |
| flip rate | | | 0 |
| cost / p50 / p95 per review | | $0.0163 / 21 s / 25 s (Sonnet) | **$0.0065 / 7.6 s / 11.2 s** |

Paired with the old critic on the 20 common cases: 12 fixed, 1 broke (q-22), McNemar p = 0.0034.

| E2E | before (`e2e-20261029T163644Z`) | after |
|---|---|---|
| pass rate (must stay 100%) | 100% | **100%** (20/20; overviews 5/5 again after the fix) |
| hard-rule violations | 0 | **0** |
| cost per question / p95 | $0.0417 / 109 s | **$0.0399 / 90 s** |

**The 3 misses and 6 false alarms (all repeat in every run):**
- q-14: "A typical order is worth about $301.79, the average" is missed. The critic flags it as
  overstated and argues about hedging, and the verifier reads that as framing. A known limit.
- q-22: "All regions cluster closely around the overall mean" is flagged. That's framing, a real
  false alarm.
- q-19 (holdout): "Across 30 months, recurring revenue has grown on an annual basis" is flagged as
  overgeneralizing one year-over-year point. Arguable. It was labelled supported before the run, so it
  counts.

## What the baseline showed
- **The old critic never saw most claims.** It read the flattened summary plus technical notes, never
  the findings or their citations. So a defect in a finding was invisible to it.
- **Its own claims weren't grounded.** 85% of its reviews stated numbers that aren't in the evidence
  ("150 values clipped above 156", "IQR×1.5 fence ~165").
- **It blamed the analysis for its own truncated input.** It reported a "missing Hard|control cell"
  because it was shown only the top 3 of 4 cells.
- **The old grounding call read years as ungrounded numbers.** "2026" was flagged in 28 of 40 reports,
  and fed to the critic as a defect.

## Built
- **`agents/quality_agent.py`** (moved from `quality_critic.py`, history kept):
  - `QUALITY_CARD`;
  - `report_claims` (headline, summary, findings, actions), the order a flag's `index` points into;
  - `check_claims`;
  - the critic and the verification pass;
  - `run_quality_check(session, llm, critic, past)`.
- **Code checks** (`guardrails.claim_*`, pure, one flag per claim). Each claim is checked against the
  evidence it cites for:
  - dates (`ungrounded_dates`);
  - trend direction and period;
  - group (a number attributed to the wrong group);
  - rank (a wrong superlative);
  - numbers (in no evidence → `number`; in some evidence but not the cited → `citation`);
  - cause from a correlation;
  - certainty on low-confidence evidence.
- **Critic** (`prompts/quality/v1.md` = the old prompt verbatim, then v2, v3; Haiku; first run only).
  - It reads the insight agent's own context (what the author was shown, earlier analyses included),
    every breakdown and trend value in full, the numbered claims, the columns, and the checks' flags.
  - It labels statements only; actions get the code checks.
  - Repaired:
    - a reason with an ungrounded number or date is replaced;
    - an `evidence_says` that misquotes the evidence drops the flag;
    - bullets with invented numbers are dropped;
    - investigations on missing columns are dropped;
    - raw column names become display names.
- **Verification pass** (`prompts/quality/verify_v2.md`, Haiku, only when the critic flagged). Each
  flag is re-judged alone against its claim and cited evidence. It fails open.
- **Code writes the verdict line** ("10 claims checked against the evidence; 1 flagged …"). The model
  no longer writes `strengths`.
- **A critic failure** (unparseable twice, refusal, truncation) leaves a checks-only review that says so.
- **Graph.** `quality_check_node` reviews every full run, and the critic joins on the first run only.
  The report is never changed. The FEAT-02 retry and the commit path are unchanged.
  `similar_insights` now flows from insight to the quality check through the graph state.
- **Schema / SSE.** New `ReviewFlag{index, section, claim, label, kind, reason, evidence_ids, source}`;
  `ExpertReview.flags` defaults to `[]`, so stored sessions still load.
- **UI.**
  - The Review tab lists flagged claims first (label, who found it, the claim, the reason, evidence
    chips), and its tab label counts the flags.
  - A flagged claim on the Summary tab carries "Review: <label>", which opens the Review tab.
  - An empty Strengths section is hidden.
  - UI check ($0, `scripts/ui_check/fixtures/quality.sse`): light and dark, 1280 and 390 px, no
    horizontal overflow.
- **Eval** (`evals/cases/quality/`, suite `quality`).
  - Each golden report is run 1 of a case from the final insight report. That is real agent output
    passing every insight hard rule, rebuilt at $0 with `insight_bundle`; a hard rule pins that the
    rebuilt evidence ids equal the stored ones.
  - 34 seeded defects across 9 kinds, at least 3 each.
  - Every original claim was hand-labelled. The judge's 28 non-supported labels were reviewed one by
    one; I agree with 1 (ins-02).
  - `--checks-only` runs the code half at $0.
  - The scorer only matches flags to labels known by construction.

## Senior decisions (with the reason)
- **Flags are shown, never applied.** The report has already streamed by the time the review runs,
  and a critic is never 100% right. Silently deleting a claim the user just read is worse than
  marking it.
- **No semantic retry.** Re-running analysis + insight costs ~60 s for a defect the UI can show.
  FEAT-02 stays structural.
- **Haiku, not Sonnet.** The plan's rule was Sonnet only if Haiku misses a target. Haiku plus a
  verification pass beat both targets by a wide margin, at 40% of the old cost and half the latency.
- **Prompts frozen at iteration 4.** Recall was 96.4% and false alarms 0.4% on the 32 non-holdout
  cases. The one remaining miss (q-14) would be fixed by tuning against the eval, so it is recorded as
  a limit.
- **A holdout result is not relabelled.** q-19 is arguable and counted.

## Deviations (stated plainly)
- **Two critic-repair rules differ from the plan:**
  - an ungrounded reason is replaced, not the flag dropped (that keeps recall; the content is gone);
  - unknown evidence ids are removed from a flag rather than the flag dropped.
- **Scorer and label changes, each rescored at $0:**
  - a review may quote the claim's own out-of-range date;
  - naming one of the dataset's own columns is not an injection echo;
  - q-32's 4 "discount" claims are `unclear` (the evidence never names a discount column; the report
    took the word from the question).
- **The E2E re-gate after the truncation fix covered only the 5 overviews** (the only E2E path the
  fix touches; a test pins that E2E scoring never reads the review). It cost $0.58 against a $0.25
  estimate, because overviews cost 2.8× the suite average. Paid runs stopped there.
- **The last commit changed three code checks after that gate** (false positives it surfaced). That
  was verified at $0:
  - check flags on all 40 golden reports are identical to the final run's;
  - over 135 stored real reports, the only flags are the true ins-02 defect;
  - no paid re-gate was run.

## Carry-overs
- **Insight writes "Segment B has the lowest average revenue" (C does) in all 3 runs of ins-02.** The
  insight eval passed it. The new rank check flags it in production; insight should use the same
  check as a repair.
- **`Evidence.result_excerpt` is cut at 300 characters, and the insight context renders it.** The
  insight agent sees a 4-cell breakdown as 3 cells. The quality agent reads findings by id; insight
  should too. That's the Ingestion or 5.6 PR.
- **The critic can't see a claim's earlier analyses on follow-ups** (they aren't stored on the
  session). The first run gets them through the graph state.
