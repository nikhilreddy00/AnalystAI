# Phase 5.7, Domain 2 review: banking / financial services

2026-11-02, branch `phase-5-7-banking` (from main 06343d7, main CI green). Plan v2 (red-team #1
folded in: 4 blockers, 6 majors, 6 minors), approved 2026-11-02. User choices during the run:
continue after the smoke run's estimator miss (cap $0.70); after the intake gate's miss, "full
plan, raise the stop to $5.00".

**Bottom line.** Banking has a domain pack (`domain_packs/banking.yaml`) and three datasets: a loan
tape, a transaction ledger and deposit snapshots. There are cases across every agent. The core
rule is that a balance is a level. It is added across accounts at one date and never across dates.
That rule now runs through one shared definition in every place a number is shown:
- tiles;
- KPI answers by group and by period;
- charts;
- the analysis sandbox's built-in code;
- correlations;
- drill-down charts.

Every paid gate passes on the final code:
- analysis: 7 banking cases at k=3, all 3/3 (4 of them after diagnosed fixes);
- intake: 20 cases at k=3, 60/60 runs after fixes;
- E2E: 10/10 banking cases and 4/4 changed retail cases at k=1;
- insight: 2/2;
- quality: 2/2, defect recall 100%, false alarms 0;
- hard-rule violations: 0.

The final audit pass met the bar (0 major, 2 minor, both fixed). The live API + worker run
(asyncpg, `APP_ENV=production`) and the desktop browser check (1440 px) each found real bugs, all
fixed. Spend: **$4.03 of the $5.00 stop**.

## Paid runs ($4.03)

| run | report | result | cost (est.) |
|---|---|---|---|
| smoke: bank-4 | `analysis-20261102T184300Z` | 1/1 | $0.031 ($0.01, **200% over**: stopped, user said continue) |
| smoke: e2e-43, e2e-49 (3 turns) | `e2e-20261102T184530Z` | 2/2; found correlations over every snapshot | $0.28 ($0.23) |
| **analysis, 7 banking cases, k=3** | `analysis-20261102T203011Z` | 10/21. Causes below | $0.55 ($0.52) |
| analysis re-run bank-4/5/6/7 k=3 | `…203339Z` | 9/12 (bank-5 0/3: a second cause) | $0.25 ($0.36) |
| analysis re-run bank-5 k=3 | `…204011Z` | **3/3** | $0.07 ($0.09) |
| analysis re-measure, 9 old cases (retail-1/2/5/6/7, saas-1..4), k=1 | `…204035Z` | 9/9 | $0.22 ($0.23) |
| intake, 20 cases, k=1 | `intake-20261102T204144Z` | 16/20 | $0.20 ($0.13, **52% over**: stopped, user raised the stop) |
| **intake, 20 cases, k=3** | `intake-20261102T205448Z` | 57/60, flip 0 | $0.47 ($0.60) |
| intake-bank-08 k=3 | `…205953Z` | **3/3** | $0.02 ($0.03) |
| insight ins-41/42 | `insight-20261102T210024Z` | 2/2 | $0.05 ($0.04) |
| quality q-41/42 | `quality-20261102T210206Z` | 2/2, recall 100%, false alarms 0 | $0.02 ($0.02) |
| live API + worker (8 runs) | traces, `run_events` | clean except one bug (below) | $0.30 ($0.25) |
| **E2E, 11 cases, k=1** | `e2e-20261102T212128Z` → `-rescored` | 9/11 → **10/10** (e2e-50 removed) | $0.63 ($0.91) |
| E2E retail re-measure (e2e-35/38/39/42), k=1 | `e2e-20261102T213308Z` | **4/4** | $0.33 ($0.45) |
| desktop browser check (5 overviews + follow-ups) | live `run_events` | 6 findings (below) | $0.62 ($0.56) |

Every failure was diagnosed. None passed "on retry":
- **Analysis bank-4/5/7: a regression of mine.** The built-in level breakdown (the pass-1 M5
  fix) replaced *every* model breakdown of the balance by the same group. That included the
  model's NPL ratio and its average balance filed under the balance column. It now replaces only
  totals (sum/last).
- **Analysis bank-5: a brief gap.** The model divided branch totals by every loan, including
  paid-off and charged-off ones (Harbor $45,368.89 vs $48,461.85). The brief now says the
  denominator is accounts on the books. A built-in mean of a level by segment replaces the model's
  mean.
- **Analysis bank-6: a case defect.** "Card spend" on a ledger with a Channel column reads as
  Channel = Card (the model's figure was 0.615 of truth; Card is 60% of rows). The question now
  says "spending" (also in e2e-48).
- **Analysis bank-7, one run: an E2B `WriteError`** (the sandbox), not the model.
- **Intake bank-01, bank-09, bank-11: three post-model causes.**
  - an overview intent that names a KPI after the overview → that KPI's time series;
  - a default prediction → an unsupported concept, so intake asks;
  - "average account balance" → the avg_balance glossary.

  Each was replayed at $0 before the paid re-run.
- **Intake bank-08: a product limit.** The case asked for two KPIs in one answer ("money in and
  money out"). It is now reworded to one KPI, "money coming in" was added to the glossary, and
  the limit is stated below.
- **E2E e2e-43: a scorer collision.** "Days Past Due MoM −2.0%" sat within 3% of the naive 2.01%
  NPL-by-count figure. The tolerance is now 0.5%, rescored at $0.
- **E2E e2e-50: a case expectation.** Intake rightly asks about NIM, and the E2E harness counts
  any clarification as a failure. The case was removed. intake-bank-05 measures that ask (3/3).
  It also exposed a repeated-bug class: intake wrote "Nim needs…". There is now one
  `Pack.name_of`.

## "Done when" (ROADMAP 5.7, per domain)

| criterion | evidence | result |
|---|---|---|
| analysis cases pass at k=3 on every run | `analysis-…203011Z` + the two re-runs | **met** (after diagnosed fixes) |
| E2E at k=1 (k=3 on failures), hard rules 100% | `e2e-…212128Z-rescored`, `…213308Z` | **met**: 10/10 + 4/4. No k=3, because both failures were a scorer collision and a case expectation, not model variance |
| each agent's expert-rubric checks pass | the cases below | **met** for the cases written |
| final audit pass: 0 major, ≤ 2 minor | pass 4 | **met** (0 major, 2 minor, both fixed) |
| README domain list regenerated from the reports | `tests/test_readme_domains.py` | **met** |

## Built
- **Schema** (`domain_pack.py`, only where banking needed it):
  - `Role.over_time` ("last": a level) and role kind `number`;
  - `Pack.snapshot_key`, `Pack.conditional_fields`;
  - `KPI.at` ("latest": each key's latest row of any kind, then the filters);
  - `Term.where` (OR of AND condition groups);
  - condition `level_as_of`;
  - text `non_product_rule` and `money_rows_note`;
  - `KPI.phrase`, `Pack.name_of`.
- **Banking pack:**
  - strong shapes: a loan tape (account + balance + DPD or bucket); a ledger with a running
    balance and flows; a ledger with banking transaction types;
  - KPIs: total balance, open accounts, average balance, NPL (90+ DPD or non-accrual, by
    balance), 30+ delinquency, net flow, inflows, outflows, fee income, interest charged, spend,
    transactions, charge-offs;
  - glossary, definitions (roll rate, ADB, originations, utilisation), unsupported (NIM, default
    prediction), caveats, causal triggers, first questions.
- **One definition per rule, each with a guard or a test:**
  - `utils.snapshot_rows`, the core of `latest_rows` and of `KPI.at`;
  - `utils.level_rows`, a level's statistic by group (answer = chart);
  - `domain_pack.entity_rows`, one row per entity for correlations (the targeted answer, the
    heatmap, scatters, dashboard pairs, target correlations, with the sandbox `_cdf` mirroring it;
    guard per function);
  - `domain_pack.layout_columns`, blanks that are layout (first-line-only, conditional fields)
    for the ingestion summary, cleaning and evidence confidence (guard: `first_line_only(` only in
    `domain_pack`);
  - `utils.with_display_names`, one rename for the insight and the expert review (guard);
  - `KPI.phrase` (guard: no `.label.lower()`).
- **Leftovers from Domain 1, closed:**
  - the intake schema summary says layout blanks, re-measured on the 7 retail_lines intake cases;
  - drill-down checks run on weak matches;
  - E2E hard rules apply to retail too (the last three E2E reports rescored: no run changed);
  - a balance is never summed on a mean question.
- **Other shared fixes found in this domain:**
  - IBAN detection (registry lengths, mod-97, every start; masking fails closed; the column
    classifier needs a whole token);
  - a pack's `additive: false` role is averaged everywhere, including retail's unit price;
  - segment and top-N answers use the one money filter (`money_rows`);
  - a level's snapshot cadence is in the contract and the brief;
  - the snapshot grain is stated as a fact ("one Loan ID at one As Of Date");
  - a KPI by period keeps $0 periods; a state is "as of" a date;
  - percent KPI charts are in percent;
  - an event date's period note reads coverage from the time axis;
  - KPI tiles' context wraps.
- **Harness:**
  - replay `CHANGED` (an intended post-model change must now differ *and* pass);
  - the golden compares pinned stages only, plus new probes (a mean by group, a pack money column
    by group);
  - rescore drops runs of removed cases;
  - the replay ignores `generated_at`.

## Bug ledger by pass

| pass | blocker | major | minor | note |
|---|---|---|---|---|
| plan red-team #1 | 4 | 6 | 6 | folded into plan v2 |
| backend review | 0 | 2 | 5 | 6 fixed, 1 stated (later fixed in pass 1) |
| security reviews (3) | — | 4 findings | | IBAN over/under-matching; all fixed |
| smoke run | 1 finding | | | correlations over every snapshot |
| **audit pass 1** (expert + builder) | 3 (+1 same) | 9 | ~14 | |
| diff review of pass-1 fixes | 0 | 2 near | 4 | |
| **audit pass 2** | 0 | 3 | 5 | |
| **audit pass 3** (+ diff) | 0 | 2 | 6 | |
| **audit pass 4** (final) | 0 | **0** | 2 | bar met; both fixed |
| paid gates | 9 causes | | | 1 regression of mine, 4 post-model/brief gaps, 2 case defects, 1 scorer, 1 sandbox |
| live run | 1 | | | event-date coverage note |
| desktop browser check | 6 findings | | | NPL chart missing, tile context cut, method verbosity, as-of in a line title, percent axis, (ledger dashboard row limit: stated) |

Majors per audit pass went 9 → 3 → 2 → 0. Bugs came from four areas:
- **Rows for a statistic.** Which rows a number reads (latest-first, on-book, per entity) was
  defined in several places until `snapshot_rows` / `level_rows` / `entity_rows` / `money_rows`
  and their guards.
- **Built-ins replacing model output.** A replacement must replace only the same meaning (the
  bank-4 regression).
- **Pack-level wording leaking retail words.** Fixed with text keys and labels.
- **Post-model deterministic steps.** Intake fill-in and naming. The $0 replay caught every
  intended change, and nothing unintended.

## Golden re-pin (`evals.snapshot pin`, 2026-11-02)

New cases: analysis +4, intake +12, insight +2, E2E +8, deterministic +2 datasets.

Changed against the Domain 1 pin, each listed in `CHANGES` while building:
- **Banking.** bank_balances-1/2/3 (now strong banking: KPIs, level hints, caveats); bank-1/2/3
  contexts; intake-clear-sql-03 (a weak banking match).
- **Retail.** retail_lines-1 (contract: layout blanks; audit: outlier names in full; built-in
  code: unit price averaged); store_weeks-1 (audit: outlier names); b2b_invoices-1 (unit price
  averaged); retail-1/2/5/6/7 contexts (mean hint); intake-retail-01/02/03/05/06/07/08 (layout
  blanks). All re-measured: 9/9 analysis, 21/21 intake runs, 4/4 E2E.
- **SaaS.** saas_accounts-1/2/3 (cadence; MRR by plan built in from latest snapshots; target
  correlations per account); saas-1..4 contexts. Re-measured 4/4.
- **Every dataset.** The new `answers_by_group` probe was pinned for the first time. No other
  stage changed for an unlisted dataset (checked stage by stage).

## Deviations from the plan
- **R2 needed no code.** A comparison's "sum" on a balance already becomes each account's latest
  balance (`effective_agg` → `last`). A test pins it.
- **R3 (mean of a level by group) was kept, not cut.** The golden showed no other domain's
  output changed. It became `utils.level_rows`, shared by answers and charts.
- **Cut:**
  - `kind_names` (labels and text keys carry the words);
  - e2e-52, a drill-down on a weak match (its $0 test covers R5);
  - bank-8, a total from a running balance (a ledger's latest row is often a reversal);
  - the utilisation KPI (no card-limit dataset; kept as a definition);
  - e2e-50 (see above).
- **Ordering and budget.**
  - The live run ran before the E2E gate (Domain 1 lesson 2).
  - Budget: two estimator misses. The smoke run was 200% over and intake k=1 was 52% over:
    banking contexts are larger than the averages the estimator uses. The user raised the stop to
    $5.00. Later estimates were recalibrated from banking's measured per-case costs, and each
    landed within 30%.
- **Retail behaviour changed on purpose** (all listed and re-measured):
  - first-line blanks are said as layout in the schema summary;
  - outlier names are given in full;
  - unit price is averaged in trends;
  - segment and top-N answers count the tiles' rows (discount by country leaves out cancelled
    and test orders).

## Known limits (stated, not fixed)
- **Snapshots.**
  - Row profiles (describe, outliers) of a level read every snapshot row. The insight can quote
    the all-snapshot mean ($42,673.17) beside the tile's per-account $43,438.49.
  - Outlier charts on a tape flag mortgages against a pooled mix.
  - Rate and DPD means by group read every snapshot (the means are right, n is ~14×).
  - An account whose currency changes between snapshots: the tile filters currency before taking
    the latest row.
- **Model emphasis.** An overview can headline "average days past due rose 161% YoY" (an average
  DPD trend) beside the NPL/30+ figures. The tiles and the NPL-by-product chart carry the right
  measures.
- **Ledgers.**
  - A reversal entry whose original is not marked reversed leaves the original counted (no pair
    link; card_ledger seed 2 plants it).
  - Two KPIs in one answer ("money in and out") are not offered.
  - Exports over 25,000 rows get no interactive dashboard: a 422 from the existing row limit; the
    ledger is 36k rows.
- **Charge-offs.** A tape that repeats a charged-off loan every month would count it each month
  (dedupe by the first charged-off row when such a tape appears). Products with no charge-offs
  are no group.
- **Wording.**
  - "leads" for a risk metric;
  - raw `mean(col)` labels and money axes not formatted as currency (both pre-existing);
  - the dashboard shows DPD as category chips.
- **Gates.** E2E, insight and quality ran at k=1.

## CI before merge (2026-11-02)

The branch's final CI run failed: `card_ledger-1`'s contract and frame, and the ins-42 replay,
differed on Linux. A temporary diagnostic printed per-column hashes on both machines. Only
`Amount` differed. The generator's salaries were `float(rng.lognormal(...))`, never rounded to
cents, and `exp()` differs in its last digits between macOS and Linux. They are now rounded, like
every other amount. `round` draws nothing from the random stream, so every fee, spend, reversal
and transaction truth is unchanged; inflow and balance totals move by cents. A test pins cents for
every ledger amount. The golden was re-pinned for `card_ledger-1`, and ins-42 was listed in the
replay's `CHANGED`. No paid case depends on the moved values.

## What to carry into the next domain
- **Recalibrate the estimator from the smoke run** (actual ÷ estimate per suite) and apply it to
  every later estimate. A new domain's contexts are not the old average.
- **Probe the deterministic answers at $0 before any paid run.** The rehearsal script (targeted
  handlers with the params intake should produce, against the truth) found three bugs before the
  smoke run and one the auditors missed ("average week" on a state).
- **A built-in that replaces model output replaces only the same meaning.** Test it with the
  model's other findings on the same column present.
- **Every number a user sees reads the one row set** (`money_rows`, `level_rows`, `entity_rows`,
  `KPI.at`). Add a guard per function when a new caller appears.
- **Case wording must not name a column's value by accident** ("card spend" vs Channel = Card).
  E2E cannot expect a clarification: test asks in intake.
- **The browser check finds what no eval reads:** a truncated tile context, a fraction axis on a
  percent chart, a missing domain chart.
- **Intended post-model changes go in the replay test's `CHANGED`,** each required to pass.
  Unintended ones fail it.
- **A generator's values must not depend on the machine:** round every float that comes from
  `exp`/`log` before it is stored. CI on Linux is the check; push before the final pin.
