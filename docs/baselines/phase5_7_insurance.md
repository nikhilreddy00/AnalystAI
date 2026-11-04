# Phase 5.7 — Domain 4: Insurance (2026-11-03)

Plan: `~/.claude/plans/pasted-content-id-1c19-start-phase-peaceful-peacock.md` (expert brief v2
after one red-team: 3 blockers, 7 majors, 6 minors folded in; decisions D1–D12 settled by a
senior-analyst judgment the user delegated). Cited facts: `docs/research/2026-11-03-insurance.md`
([WM] Werner & Modlin, [FR] Friedland, [AAA] Schedule P, NAIC 668/880). Builder terms: `GLOSSARY.md`.

## Result

- **Domain pack** `domain_packs/insurance.yaml` (P&C; life is unsupported):
  - **Policy tables** (terms, renewals, negative cancellation rows):
    - earned and written premium;
    - loss ratio = incurred ÷ earned (a ratio of sums);
    - frequency and pure premium per line only (`KPI.per`: car-years and house-years never add up);
    - severity per reported claim;
    - average premium per term;
    - retention and lapse over the terms up for renewal;
    - policies.
  - **Loss runs** (claim × claimant per valuation date):
    - each chain's latest valuation only (the version chain, ordered by valuation date);
    - incurred, paid, open reserves, net of recoveries;
    - accident years younger than 24 months named with their age (`Maturity`), never cut and
      never called final;
    - policy-year earned premium develops too (`Maturity.dated`).
  - **Unsupported** (each said plainly): IBNR/ultimate/triangles, the combined ratio without
    expenses, rate indications, life, and rating on protected attributes (gender, marital status,
    race, religion, national origin get a caution).
- **Paid gates (all at their bars after diagnosed fixes):**
  - analysis: ins-4..9 at k=3, every run passes (ins-6/9 after the brief fix), ins-1..3
    re-measured; all 9 again at k=1 after the audits;
  - intake: 12 cases, intent 100%, 0 invented columns; ins-09/11 3/3 after the glossary fix;
  - insight 2/2 (+ banking/healthcare re-measured), quality 2/2 (critic on: defect recall 100%,
    false alarms 0);
  - E2E: 9 insurance cases (incl. multi-turn, Postgres, JSON) + 3 cross-domain at k=1, 12/12 after
    the audits (e2e-46 rescored: scorer gap);
  - hard-rule violations: 0.
- **Live API + worker** (asyncpg, `APP_ENV=production`): a loss-run overview found 2 bugs (a layout
  blank reported "71% missing" in evidence and a CRITICAL alert; accident-month YoY headlined).
- **Browser check, 1440 px, live API + worker**, every number = generator truth; a filter changed
  a tile AND a chart on every dataset:

  | source and dataset | before the filter (truth) | filter | after the filter (truth) |
  |---|---|---|---|
  | loss run via **CSV** | incurred $33,674,954.88; paid $29,634,815.93; open reserves $4,040,138.95; 3,450 claims; severity $9,760.86 | Line of Business = Home | $17,769,798.56; 1,455 claims; "Showing 1,661 of 3,975 records" |
  | policy table via **Excel** | earned premium $4,023,883.78; loss ratio 60.5%; 1,300 policies | Line of Business = Auto | earned premium $2,617,354.01; loss ratio 55.3% |
  | loss run via **JSON** | 3,450 claims; $33,674,954.88 incurred | — | — |
  | policy table via **Postgres** (SQL tab) | loss ratio Home 70.1% / Auto 55.3% | — | — |
  | retail | $310,635.46; 2,272 orders | Shipping Country = Australia | $25,413.93; AOV $126.44 |
  | banking | $47,521,711.85; 1,094 accounts | Product = Mortgage | $40,584,162.85; 216 accounts |
  | healthcare | 3,455 discharges; ALOS 3.72; readmission 9.0%; mortality 2.4%; 1,707 patients | Department = Cardiology | readmission 14.3%; ALOS 4.72 |

  REST API and Google Sheets: proven with mocks in the source-invariance oracle (mocked API +
  Sheets + Postgres added this domain).

## Spend: $4.15 of the $4.70 stop

| run | result | cost |
|---|---|---|
| smoke (ins-6/8, e2e-61) | 3/3 after 2 scorer fixes ($0 rescore) | $0.26 (**analysis 4.5× over: stopped, user said continue**) |
| intake 12 k=1 | 10/12 (glossary) | $0.14 |
| analysis ins-1..3 k=1, ins-4..9 k=3 | 3/3, 15/18 (one root cause) | $0.44 |
| ins-6/9 k=3, intake 09/11 k=3 | 6/6, 6/6 | $0.16 (**intake 39% over: stopped, user said continue**) |
| ins-4/5/7/8 k=3 on the final brief | 12/12 | $0.16 |
| insight (4 runs as the harness was fixed) | 2/2 each (+ ins-42/44 re-measure) | $0.21 |
| quality (critic) | 2/2 | $0.02 |
| live API + worker | 2 bugs | $0.13 |
| E2E 9 k=1, then e2e-64/65, e2e-65 k=3 | 6/9 → 9/9 | $0.76 |
| re-measure after the audits (analysis 9, insight 2, E2E 12) | 9/9, 2/2, 12/12 | $1.05 |
| browser check (7 runs) | all tiles = truth | $0.81 |

Estimator: the harness under-estimated insurance analysis by 4.5× at first (guided retries on the
loss run's large contexts), then ran under after the brief fix; recalibrate per suite.

## Bugs found and fixed (root fixes; each has a test that fails without it)

- **$0, before any paid run:**
  - a version chain couldn't order by a date (valuations from SQLite/API counted every row);
  - `runout` would have cut the accident years the report must name: `Maturity` instead;
  - VIN, driver licence and plate were not personal data (VIN by check digit, at column level only);
  - the oracle had no Postgres/API/Sheets relation;
  - a cat-event column would raise the high-null gate (`marker_fields`);
  - the paired layout was missing from the evidence's layout list.
- **Paid gates:**
  - 17/27 "false" stats and an "Overall" row (scorer read every row);
  - the pack caveat's numbers were ungrounded (scorer);
  - every analysis failure came from one cause: the model re-derived "latest valuation" with a
    sort that E2B's pandas 2 can't compare (local pandas 3 sorts stably); now the brief says
    counted rows are the latest;
  - "premium did we write" and "whole life" in the glossary;
  - insight: "38,255 claims" / "2,674 policies" (rows called entities) → the version grain and
    entity-count sentences; the insight harness summarised every valuation and compared a partial
    month (−70.4% vs +5.8%) → `counted_summary` / `counted_months`, one definition;
  - a top_n naming a KPI without a group ("How much have we incurred in total?");
  - "how many" read as a monthly series;
  - e2e-62's tolerance collided with an MoM (−51.3%).
- **Live run and browser:**
  - layout blanks reported "missing" (three copies of the layout rule → `LAYOUT_STRATEGIES`,
    guarded per kind);
  - accident-month YoY headlined → no built-in MoM/YoY of a developing measure;
  - a 38,255-row loss run refused by the dashboard (earlier valuations counted against the cap),
    then "Showing 15,780 of 3,975" → `domain_pack.superseded`; a raw-row hard cap (security review);
  - "A 2,674-policy book" → `claim_rows_as_entities` in the quality checks.

## Audits (converging)

| pass | reviewer | major | minor |
|---|---|---|---|
| 1 | Standards + Spec code review + insurance expert | 11 | ~20 |
| 2 | pass-1 fix diff | 3 | 4 |
| 3 | pass-2 fix diff | 3 | 3 |
| 4 | pass-3 fix diff | 1 | 2 |
| 5 | pass-4 fix diff | **0** | 3 → 2 (the one from this domain fixed) |

Pass 1's majors: frequency as one total across lines; lapse read as retention; "policies in force" all-time;
earned premium by effective month read as a drop; accident-month MoM in headlines; cat event never
mentioned; count units; renewal blanks "missing"; workers' comp vetoed; a string-compared entity
count; a version grain count with non-product rows. Passes 2–4 found holes the previous fixes
opened (a widened rule each time: the per-role override, the vetoes, header expansion, the
segment filter before kinds), the pattern of earlier domains.

**Open minors (2, pre-existing):**
- inpatient's last month shows a readmission rate on 4 eligible stays (an immature month);
- `at: latest` snapshots are taken after a named-value filter (no dataset hits it).

**Known limits, stated:**
- triangles and transaction-grain claims;
- claims joined to premiums across files;
- a loss run whose closed claims drop from later valuations is fine (the chain rule needs no
  re-listing);
- the 24-month maturity is the same for every line (the caveat says liability develops longer);
- dashboard profile scatters still read every row (logged for Phase 5.8).

## Recurring bug classes (plan v2), plainly

- **Class 1 (which rows): came back**, 4 times: the insight harness summaries, the dashboard cap
  and count, the segment filter before kinds, eda_stat. Each was fixed at the shared rule.
- **Class 2 (a second copy of a rule): came back**:
  - three copies of the layout list → one;
  - the harness's month and summary rules → `counted_months` / `counted_summary`;
  - one VIN check digit.
- **Class 3 (exact vocabulary): came back**: glossary phrases; a header compared without
  abbreviation expansion (`Maturity.dated`).
- **Class 4 (traces): none.**
- **Class 5 (harness not on the production path): came back** (the insight harness): CODING_STANDARDS
  rule 1 now has two more tests comparing stand-in and production.

## Golden re-pin (named diff)

Changed: `claims-1/2/3` and `ins-1..3` (make_claims reads as insurance; life → commercial);
`ins-42-ledger-overview` (the harness summarises the rows production counts);
`ins-44-claims-overview` (a version chain's grain sentence). Added: 6 analysis, 12 intake,
2 insight, 2 quality, 7 E2E, 3 ingestion, 2 cleaning, 2 visualization cases; 2 datasets.

## Time (wall clock, one session; Domain 3 took ≈ 4.5 h)

| stage | time |
|---|---|
| plan: research + code map + brief + red-team + self-grill | 0 h 37 m |
| build: 6 shared fixes, pack, data (subagent), cases, $0 gates | 0 h 40 m |
| paid gates + diagnosis (smoke → analysis/intake → insight/quality → live → E2E) | 1 h 00 m |
| audits (5 passes, fixes, CI zero-suites, re-measure) | 1 h 25 m |
| browser check (1440 px, live; 3 fixes) | 0 h 35 m |
| finish | ~0 h 15 m |
| **total** | **≈ 4 h 30 m** |

## Deviations

- The "live API + worker dry pass on recorded output" became the paid live run (production mode
  needs auth and a model to pass intake).
- `closure_rate` dropped (a `nunique` can't say "every claimant closed").
- A benefits-enrollment file (policy numbers + written premium) reads as insurance: it is premium
  data.
- CI's zero-suites job went red on two commits: the pre-push hook didn't run it; it does now.
- The grill was self-answered (delegated by the user), recorded in the plan's C0.

## Post-domain follow-up (2026-11-04, user request): dashboard filters and the row cap, permanently

**Why it kept coming back.**
- The 25,000-row "too large" 422 (Domains 3 and 4) existed only because `/dashboard/latest/data`
  shipped every row to the browser.
- The browser also recounted with its own copies of the filter and counting rules
  (`applyFilters`, `aggregateForChart`, `evaluateKPI`), which knew none of a domain's rules (row
  kinds, a claim's latest version, snapshots, currency). Filtered numbers were right when the
  server answered and wrong while it loaded, or for charts only the browser recounted. The CI
  mock had a third copy.

**Fix (one definition).**
- The browser never computes a number and never receives a row: it gets filter options from the
  counted rows (`visualization_agent.filter_options`) and the server's numbers
  (`filtered_dashboard`).
- The recount code is deleted, and the mock calls the real functions.
- The server guard is 1,000,000 rows, checked from parquet metadata; recomputations run on a
  2-worker pool.

**Permanent check.** `tests/test_dashboard_filters.py` runs every category filter and value, plus
`in`, `range` and `date_range` filters, on all 12 domain datasets, through the production path. Its
relations need no truth table, so new domains are covered automatically:
- counts add up across a column's values;
- a filtered tile = the KPI by that column (kinds judged on all rows);
- additive tiles partition the whole;
- a chart by the filtered column keeps that value's unfiltered number;
- a small cell shows no number, not even its count.

It was proven failing on the old rule (row kinds judged on the filtered rows).

**Live (1440 px, live API + worker):** loss run, Claim Status = Open → incurred $6,185,025.45,
566 claims (truth); + Home → open reserves $1,910,942.75 (truth); `/latest/data` carries no rows.

**Senior audit (13 datasets, ~900 answers; every tile and KPI value = independent truth), fixed at
the root:**
- developing measures are never compared across periods and keep their youngest period (with its
  coverage);
- windows that reach arriving months say so;
- follow-up rates leave out censored periods;
- ids are no chart measures or filters;
- `Pack.average_per`: an average of a claim pack's money is per claim (`utils.aggregate` =
  `group_bars`);
- `entity_rows` drops a chain's earlier versions;
- "Unknown" is never ranked.

**Fix-diff review:** 2 majors (a partial quarter headlined; a small cell's count shown), fixed.

**Open (cosmetic):** chart pills say the rows read (38,255) on chained data, not the 3,975
counted.
