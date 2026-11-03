# Phase 5.7 — Domain 3: Healthcare (2026-11-03)

Plan: `~/.claude/plans/pasted-content-id-b153-start-phase-magical-hollerith.md` (expert brief v2 after
one red-team: 2 blockers, 6 majors folded in; open decisions D1–D5 settled by a senior-analyst
judgment the user delegated). Cited facts: `docs/research/2026-11-03-healthcare.md`. ADR-0008
amended (exact dates = a limited data set). Builder terms: `GLOSSARY.md`.

## Result

- **Domain pack** `domain_packs/healthcare.yaml`: hospital stays (discharges, ALOS by discharge,
  crude 30-day readmission with the CMS index exclusions and censoring, in-hospital mortality,
  patients) and payer claim lines (each line's last version, paid / allowed / billed, the denial
  rate on claims over adjudicated claims, members, run-out). `phi: true` — any healthcare match
  turns PHI mode on.
- **Paid gates (all at their bars after diagnosed fixes):**
  - analysis: 8 healthcare cases at k=3, every run passes (health-8 after prompt v9);
  - intake: 14 cases, intent 100%, no needless ask, 0 invented columns; health-10/12 3/3 after
    the glossary fix;
  - insight 2/2, quality 2/2 (critic on: defect recall 100%, false alarms 0);
  - E2E: 9/9 at k=1 after $0 rescores and diagnosed re-gates, incl. a multi-turn case and two
    non-CSV sources (SQLite table, JSON upload);
  - hard-rule violations: 0 on the final runs.
- **Cross-domain re-measure** (shared changes): retail/banking analysis 4/4 on prompt v9, E2E
  e2e-35 (rescored) and e2e-46 pass.
- **Live API + worker** (asyncpg, `APP_ENV=production`): found 3 bugs (below), all fixed.
- **Browser check, 1440 px, live API + worker:** stays via CSV and via a **Postgres table**
  (SQL tab), claims via **Excel**, one retail and one banking upload; every tile equals the
  generator truth (stays: 3,455 discharges, ALOS 3.72 days, readmission 9.0%, mortality 2.4%,
  1,707 patients; claims: paid $257,150.19, allowed $293,787.43, 2,668 claims, 403 members,
  denial 9.5%; retail $310,635.46 / 2,272 orders; banking $47,521,711.85 / 1,094 accounts).
  REST API and Google Sheets: proven with mocks only (no accounts).
- **Data sources (user request):** silent truncation disclosed, SQL table load read-only and
  timed, SQLite refused in production, Sheets crash fixed, API wrapper shapes + key header,
  `.jsonl`, honest `.xls`/MySQL, PHI guard on SQL/API sessions; a source-invariance oracle (same
  table via CSV/xlsx/JSON/parquet/SQLite → same domain, roles, PHI mode, tiles) for healthcare,
  banking and retail. New connectors: Phase 5.8 (ROADMAP).

## Spend: $4.36 of the $4.70 stop

| run | report | result | cost |
|---|---|---|---|
| smoke (health-4/6/7, e2e-55) | `analysis-…174405Z`, `e2e-…174937Z` | 2/3 + 1 crash (fixed), e2e rescored 1/1 | $0.20 |
| health-4 after fix | `analysis-…174902Z` | 1/1 | $0.02 |
| analysis 8 cases k=3 | `analysis-…175226Z` | 7/8 (health-8 diagnosed) | $0.48 |
| intake 14 cases k=1 | `intake-…175225Z` | 12/14 (glossary verb, fixed) | $0.14 (**45% over**: stopped, user said continue) |
| health-8 re-runs (v8 fix, v9) | `analysis-…180310Z`, `…180918Z` | 2/3, 3/3 | $0.15 |
| insight (4 runs as the harness was fixed) | `insight-…180311Z` … `…191036Z` | 2/2 each | $0.19 |
| quality (critic) | `quality-…182408Z` | 2/2 | $0.02 |
| live API + worker | live `run_events` | 3 bugs found | $0.45 |
| retail/banking re-measure | `analysis-…184608Z`, `e2e-…184604Z` | 4/4, 2/2 (rescored) | $0.42 |
| healthcare E2E k=1 | `e2e-…184600Z` | 7/9 after case-scale rescore | $0.79 (**49% over**: stopped, user said continue) |
| E2E re-gates | `e2e-…191033Z`, `…192140Z`, `…192807Z` | 3/4, 0/1, 1/1 | $0.70 |
| intake-health-10/12 k=3 | `intake-…194212Z` | 6/6 | $0.09 |
| browser check (5 overviews) | live `run_events` | all tiles = truth | $0.74 |

Estimator: the smoke run calibrated ×1.25; intake (×1.45) and E2E overviews (×1.49) still ran
over — healthcare contexts are larger than banking's. Recalibrate from the smoke run per suite.

## Bugs found and fixed (root fixes; each has a test that fails without it)

- **Before any paid run ($0):** claims file skipped PHI mode (B1); claim versions (B2); codes read
  as metrics; KPI filter values pooled ("Expired"); pattern codes withheld; DRG `313.0`; `Line
  No` pseudonymised (claims chains collapsed); run-out drop reported as a −64.8% change; ALOS
  without its unit; pooling a float code column broke parquet (oracle adapter now takes the PHI
  copy, as production).
- **Paid gates:** HCPCS codes wiped by cleaning (344 values); "top procedures by paid" ranked by
  line count (prompt v9); trend months guessed ("Aug→Sep" for Jul→Jun: the evidence now names
  them); scorer and harness gaps (counted-rows population, `/` in a column, removal notices,
  case scales, the insight harness skipped PHI mode and the run-out, used Paid Date as its axis).
- **Live run:** a date column's breakdown crashed `repair_breakdowns`; trend months lost in the
  parser; the pooled bucket ranked "lowest (0.00)" and a ratio shown as 0.14.
- **Security reviews:** a column vote let free text ride in a code column; a whole-column pooling
  exemption; a loose pattern let 1:1 word tokens skip withholding.

## Audits (converging)

| pass | reviewer | major | minor |
|---|---|---|---|
| 1 | code review (Standards + Spec) + healthcare expert | 14 | 21 |
| 2 | fix-diff only | 3 | 7 |
| 3 | fix-diff only | **0** | 7 → 2 after fixes |

Open minors (2): `drop_unknown_findings` repeats `_finding_columns` (one helper later); the
brief's derived-column line doesn't say ALOS counts inpatient rows only. Known limits, stated:
a single small group pooled alone still shows its own rate (ADR-0008; highest/lowest never rank
it, counts of 1–10 are flagged); run-out is anchored to the last service month, not the extract
date; coded columns with > 50 values aren't pooled; labs (censored `<5`, mixed units) are Phase 6.

## Golden re-pin (named diff)

Changed (all listed in `CHANGES` during the domain, re-measured where a paid context moved):
`health-1` (pack brief); `encounters-1/2/3`, `e2e-23/24` (the pack strong-matches make_encounters);
`employees-1/2/3`, `retail_lines-1`, `card_ledger-1`, `ins-42` (a trend that skips a partial month
names its months); `bank_balances-1/2/3`, `loan_tape-1` (a distinct count's How says its filter);
`b2b_invoices-1` (summary stats over counted rows). Added: 5 analysis, 9 E2E, 14 intake, 2 insight
cases, 2 datasets.

## Time (wall clock, one session; Domain 2 took 12.0 h)

| stage | time |
|---|---|
| plan: research + code map + brief + red-team + grill + sources research | 1 h 04 m |
| build: 10 shared fixes, pack, data (subagent), data-source fixes (subagent), cases, $0 gates | 0 h 50 m |
| paid gates + diagnosis (smoke → analysis/intake → insight/quality → live → E2E) | 1 h 07 m |
| audits (3 passes, fixes) + re-gates | 0 h 40 m |
| browser check (1440 px, live) | 0 h 25 m |
| finish (pin, docs, CI) | ~0 h 20 m |
| **total** | **≈ 4 h 30 m** |

What made it faster: research, data generation and the data-source fixes ran in parallel
subagents; full suite at `-n 8` in the background; CI never waited on in the foreground; fresh
reviewers per pass on the fix diff only; the $0 replay/rescore settled most paid failures.

## Deviations

- Labs deferred to Phase 6 (D1); the member-months (enrollment) file is not detected as a domain
  (a member id + month + plan reads like a gym file too); PMPM / per-1,000 are "unsupported"
  answers, not KPIs.
- The plan's paid E2E via Postgres ran as SQLite in the harness (the harness has no Postgres
  source); Postgres was proven live in the browser check.
- Truncation is an insight alert, not a pack CONDITION (works for every domain; low value to
  move).

## Post-domain (2026-11-03, user report): dashboard filters, final review, recurring classes

- **Dashboard filters changed nothing (every domain).** Diagnosed with a red repro, then live at
  1440 px. Root causes: (1) `ChartRenderer` kept `useState(original)`, a copy of its whole chart,
  so no new data ever reached a chart; (2) only row-count charts and plain tiles were recomputed
  in the browser, while every sum, KPI chart and domain-pack tile needs rules only the server has.
  Fix: the renderer keeps only the user's chart-type switch; `POST /dashboard/latest/filtered`
  rebuilds tiles and deterministic charts on the filtered rows with the functions that built
  them, judging row kinds on the whole data (a filter never revives a replaced claim version);
  this session's pins only; patient data filtered below 11 records shows no numbers; the row cap,
  a 30 s timeout and a 500-value cap. Charts from the model's findings keep an "unfiltered" pill.
  Live: Product = Mortgage → total balance $40,584,162.85 (truth), 216 accounts, one bar.
  Guards: a frozen-prop test, an e2e spec at 1440 px, server tests on stays, claims, retail and
  banking.
- **Final review (fresh, on all of Domain 3):** 3 majors (version revival under filters, other
  sessions' pins, small cells in filtered tiles) and 11 minors; all majors and 7 minors fixed.
- **Recurring classes (plan v2) in Domain 3:** class 1 (which rows) recurred in the summary stats,
  the outlier histogram and the distribution/outlier answers (voided claims' −$442), class 2 (a
  second copy of a rule) in the eval harness and the browser's KPI code, class 3 (exact
  vocabulary) in "we pay", 07 vs 7, 313.0 and `Line No`; class 4 only as traces; class 5 none.
  The rows_for guard missed class 1 because it only looked for `aggregate(`; it now flags any
  numeric reduction of the frame without a row rule, with an explicit, reasoned list of
  whole-table profile functions. **Open:** the dashboard's profile histogram of a measure still
  reads every row (moving profile charts onto counted rows changes retail/banking overviews, a
  paid re-measure): planned with Phase 5.8.
