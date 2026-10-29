# Phase 5, PR 4 review: Insight agent on the agent contract

Card `insight v4`, 2026-10-29, branch `phase-5-insight`. Judge: `insight_judge v1` (Haiku 4.5),
input v2.
- **Final insight report: `evals/reports/insight-20261029T162956Z-rescored-judged.*`**: 40
  bundles (8 holdout), k=3, 120 runs. Paired against the baseline
  `insight-20261029T155429Z` (the old agent on a stratified 16, k=1; rescored for the scorer
  fixes listed below, verdicts on the agent unchanged).
- **E2E gate: `e2e-20261029T163644Z`** (k=1, final code path). The "before" is the last Cleaning
  gate, `e2e-20261028T224738Z`, rescored at $0 with the 4 new insight hard rules.
- **Spend: ≈ $4.35** (runs $3.75 + judge $0.60), under the $5 cap. Every paid run was announced
  with its estimate. One re-estimate stop fired (below).

## Numbers

| metric (ROADMAP Insight row) | target | baseline (old agent, 16 bundles) | **final (k=3, 120 runs)** |
|---|---|---|---|
| **hard:** every number grounded in the evidence | 100% | 31.3% of runs | **100%** |
| **hard:** dates inside the data's range (incl. "the data covers <one month>") | 100% | 87.5% | **100%** |
| **hard:** findings and actions cite known evidence ids | 100% | 0% | **100%** |
| executive view has no jargon | 100% | 93.8% | **100%** |
| calibrated-judge faithfulness (statements) | ≥ 90% | 68.4% (136 claims) | **96.3%** (246 claims, run 1 per case) |
| contradictions named (both signals) | | 60% | **100%** |
| low-confidence results hedged | | 0% | **100%** |
| causal wording on a correlation | | 0 runs | **0** |
| planted headline recall | | 100% | **100%** |
| holdout (8 cases, first seen in the final) | | — | **100%** |
| pass rate, all cases | | 68.8% | **100%** (5 fixed, 0 broke on the 16 common cases) |
| insight call cost / p50 / p95 | | $0.036 / 43 s / 49 s | **$0.014 / 15 s / 18.5 s** |

| E2E (20 questions) | before | **after** |
|---|---|---|
| pass rate (must stay 100%) | 100% | **100%** (0 broke) |
| hard-rule violations (with the 4 insight rules) | 13 | **0** |
| judge faithfulness on the 5 real overviews (statements) | 76.1% | **94.3%** |
| cost per question / p95 latency | $0.0455 / 145 s | **$0.0417 / 109 s** |

**Judge calibration** (`insight_judge-20261029T162810Z`): 50 labelled claims.
- **Statements:** 95% agreement on supported vs not, and 100% recall on the 20 seeded defects.
  The ADR-0002 gates are 85% and 90%.
- **Actions:** 80% agreement, below the gate, so action labels are reported as uncalibrated
  (77% supported in the final) and don't count toward the target.
- Its misses run both ways. It is lenient on compound claims (a right number next to a wrong
  share), and strict on rounding and display names (it called "4.43" for 4.4251 overstated).

## What the baseline showed (e2e-01 and the bundles)
- **"November 2025" on 12 months of data, in all 25 stored overviews.** The cause is
  `contract.schema_summary`: the ingestion model summarizes 5 head rows ("spans November 2025",
  "segments A/B"), and insight used it as fact.
- The analyst tab and the alerts were paraphrases, and they invented things:
  - "deduplicated on full-row equality" and "`notes` excluded" are false under the new cleaning;
  - "IQR × 1.5" was stated when the detector uses 3×;
  - "typical order $175–$265" is an invented quartile range (the median is $100.86).
- Derived numbers were stated as findings ("54% higher", "0.83% of rows", "8.7 standard
  deviations"), along with rounded thresholds nobody computed ("orders above $900").
- A stale memory ("B grew 15%") was repeated, and 18-row correlations were stated as certain.

## Built
- **`INSIGHT_CARD` and prompts `v1` (old, verbatim) → `v4`.** The changes, one per version:
  - **v2:** the model writes only headline, summary, findings, actions, caveats and questions,
    and every finding and action cites evidence ids;
  - **v3:** brevity (no repeated numbers in the summary, 2–4 findings);
  - **v4:** an overview's headline is its most important finding.
- **`build_context` → `InsightContext`:**
  - computed data facts, including the date range from the new `CleaningAudit.date_ranges` and
    the two months a MoM figure compares;
  - one line per Evidence item, plus the cleaning summary;
  - only the charts that show evidence, with their ids;
  - display names, and memory scoped to user + dataset.
  - Everything derived from data is masked and delimited.
  - Left out: `schema_summary`, raw head rows, data-profile charts, and breakdowns over a
    person-name column.
  - YoY evidence on fewer than 13 months carries a caution. The e2e "revenue YoY +541%" is the
    analysis model comparing partial calendar years.
- **Hard rules, held by repair** (the analysis lesson: a retry costs p95):
  - a sentence with an ungrounded number or date, or with jargon, is removed;
  - unknown ids are dropped, and an uncited finding or action is removed;
  - a claim left empty is rewritten from its evidence (`_plain`);
  - unparseable JSON twice gives a report of the evidence, said plainly;
  - refusal and truncation are typed errors.
  - 7 of 120 final runs had a sentence removed, with 0 retries.
- **Deterministic analyst view and alerts:**
  - the methodology names only the methods whose evidence exists;
  - the statistical notes are the evidence claims with n and confidence;
  - the caveats are the model's ≤ 3 plus low-confidence reasons and assumptions;
  - the alerts come from the audit (high-null as ">99.9%", unreadable values, Unknown labels,
    extreme values, duplicates). The keyword severity heuristics were deleted.
- **Schema:**
  - `Claim{text, evidence_ids}` for summary and findings (old string sessions still load);
    `ActionItem.evidence_ids`; `headline_evidence_ids`;
  - Evidence kind `eda`, one item per described column, so a level claim has a source;
  - `segment_id()` makes ids unique (a count or sum gets a suffix): one definition shared by
    analysis, visualization and the eval fixture.
- **Shared guardrails:**
  - `ungrounded_numbers` reads `$1.2M` / `45.3K`;
  - `ungrounded_dates` checks ranges and one-month scope claims, and `without_dates` keeps days
    and years out of number grounding;
  - `executive_jargon`.
  - Precision was run first on all 25 stored E2E insight reports and on the baseline outputs,
    and the false-positive patterns are pinned as tests.
- **Memory:** `dataset_key()` is a digest of the source, never the credentials. Store and
  retrieve filter by user and dataset, and old points without a key never match.
- **UI:** numbered evidence chips on findings and actions open the Evidence drawer on the cited
  item; the PPTX export reads `Claim.text`. UI check ($0) passed in light and dark at 390 px;
  fixture `scripts/ui_check/fixtures/insight.sse`.
- **Eval:** `evals/cases/insight/` holds 40 bundles across 6 categories:
  - 12 contradictory cases: Simpson ×2, total vs one segment, volume vs revenue, MoM vs YoY,
    mean vs median, n.s. gap ×2, low n, stale memory ×2, false premise;
  - the other categories: data quality, date scope, adversarial, null results.
  - Bundles are built at $0 by the real cleaning and visualization agents and
    `build_evidence`. `datagen.make_subscriptions` adds 30 months of SaaS data.
  - The scorer keeps its own number pool and jargon list.
  - `evals/judge.py` provides `calibrate` and `evals.run judge <report> [cap] [first]`.

## Senior decisions (with the reason)
- **The analyst view is code, not prose.** It exists for reproducibility, and every invented
  method in the baseline sat there. The same move also took the call from 2.8k to ~0.9k output
  tokens.
- **Repair over retry, including for citations.** The plan said "uncited → one guided retry,
  then drop". Dropping alone held the rule at 0 retries, so the retry was never built: measure
  before you add.
- **Derived numbers are not allowed** (no "54% higher"). The model states both values. A ratio
  in words ("twice") is the remaining gap; the judge sees it.
- **No `search_past_insights` tool.** The orchestrator pre-fetches the top 3 scoped matches, so
  a tool would re-fetch them at +1 round trip, and there is no Qdrant locally to measure with.

## Deviations (stated plainly)
- **Stratified baseline.** The old agent ran on 16 of 32 non-holdout bundles ($0.57), covering
  every category and all contradiction types. The full 32 would have put the PR over $5. A $0
  real-pipeline before (the stored E2E overviews, rescored and judged) complements it.
- **Re-estimate stop.** v2 measured $0.025 per call against the $0.02 trigger. The fix was v3's
  brevity plus judging run 1 of each case (temperature 0, so the k runs barely differ). Final
  insight cost: $1.73.
- **Faithfulness counts statements only.** That was decided *after* the v4 judging, which first
  read 87.8% over all claims. It is backed by a measurement made for the purpose: 10 labelled
  actions added to the calibration set gave 80% agreement, below the 85% gate.
- **Judge v2 was rejected.** "Every part must hold" calibrated at 87.5% against v1's 90%, so v1
  stays frozen.
- **Judge input v2.** A 12k-character evidence cut hid half of a real overview's 47 items, which
  made the first E2E reading 65.7%. Bundles and the calibration set were far below the cut. All
  E2E judgments were re-run on the lifted input.
- **Scorer and case fixes, each found by reading outputs, each rescored at $0:**
  - a decimal read as a year (`std=1980.52`);
  - "through Oct 2026" and two-month sentences read as a one-month scope;
  - the causal check flagging "test whether spend drives revenue" and "root cause";
  - the hedge check missing "not yet conclusive", and then applied to actions;
  - a false-premise refutation counted as an injection echo;
  - the ins-28 caveat "no month-over-month comparison can be made";
  - ins-39/40 missing "could be chance";
  - ins-18's compliant "an earlier analysis showed B growing fastest, but…", the final run's
    only failure (119 → 120/120).
- **Analysis suite not re-run.** Its prompt is byte-identical, and `segment_id` / `eda`
  evidence are post-processing that the analysis scorer doesn't read. The viz and cleaning evals
  were re-run at $0 and are unchanged.

## Carry-overs (measured here)
- **Ingestion PR: targeted questions skip cleaning.** Replaying the 15 stored targeted plans on
  raw vs cleaned frames, **10 of 15 answers change**:
  - duplicates are counted twice, so top-region totals are off by up to 1.3%;
  - one segment-trend answer moves from −5.9% to −8.6%.
  - Also, the targeted trend compares first-to-last month while the overview uses MoM.
  - The fix (load → clean on every turn, ask the high-null gate once per session) belongs to
    the ingestion-node rework.
- **Ingestion PR:** compute the date span deterministically for `schema_summary` (intake still
  reads it). Person-name breakdowns are now kept out of the insight prompt; the general
  "unmasked PII never reaches an LLM" rule is that PR's hard target.
- **Analysis:**
  - YoY is computed by the model as calendar-year totals, so a partial year reads as +541%. It
    should require ≥ 13 months, or compare the same month a year earlier.
  - When the 25-record anomaly cap is hit, the evidence claim should give the total flagged.
  - The model's global notes ("Forecasting uses OLS…") are attached to unrelated trend evidence.
- **Visualization:** a YoY trend finding gets a chart titled with the MoM change.
- **Insight quality (not a target miss):**
  - real overviews still open with a description of the dataset despite v4's headline rule;
  - one recurring model error mis-ranks a middle segment as "lowest";
  - rates appear as decimals (0.72) where "72%" reads better.
- **Quality check PR:** the critic should read Evidence plus these hard-rule results, not prose.
