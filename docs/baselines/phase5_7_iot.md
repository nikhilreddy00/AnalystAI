# Phase 5.7 — Domain 7: IoT / manufacturing (2026-11-16/17)

Plan: `~/.claude/plans/pasted-content-id-d1f9-start-phase-tender-raccoon.md` (v2). It checks off
every plan-mode step of the ROADMAP 5.7 contract by name:
- **brainstorming:** one `iot` pack, three grains (historian readings, MES shift report, CMMS
  work orders), decided as a senior engineer; what is not built is listed below;
- **research:** a background agent on primary sources (NIST/SEMATECH, OPC UA and the ISO 22400-2
  annex, SiteWise, Ignition, Maximo, PEP 495), `docs/research/2026-11-16-iot.md`; paywalled
  standards (ISO 22400-2, SEMI E10, IEC 60050-192, SMRP) are marked snippet / unverified;
- **expert brief:** the expert view (a reliability analyst: KPIs exactly, 21 planted traps T1–T21,
  must say / must never say) and each agent's builder view;
- **domain-modeling:** the term ledger (reading, run time, availability, performance, quality,
  OEE, MTBF, MTTR, failure, corrective / open WO, PM share, downtime, line, shift, cycle) with its
  clashes against finished packs; GLOSSARY: series key, invalid reading, zero-event group;
- **codebase-design:** the shared-fix table F1–F11, each with a second reader and the deletion
  test; rejected: a mean term, an hourly bucket, a per-pack benchmark list, a new caveat
  condition, widening `rows_for`;
- **Step 0, ledger:** 12 rows this domain touches, each with its check;
- **red-team:** one fresh subagent: 4 blockers, 19 majors, 6 minors, all folded in (RT-n);
- **grilling:** G1–G6 answered as a senior engineer; the user's calls U1 (°F rows converted to
  °C), U2 (control chart in a targeted answer only), U3 (ask at $4.40);
- **sizing + cost estimate:** one PR, ≈ $4.0 estimated; split point after the $0 work (not needed).

**Stop rule (the audit procedure, first run):** stop when the properties, the mutation gate and
the ledger checks (`tests/test_retro_ledger.py`) are green after a pass. Met after pass 2.

## Result

### Shared fixes (no IoT word in shared code; second readers are toy packs)

- **F1 (K1)** `guardrails.without_dates` strips clock times (with a date, seconds, a zone, am/pm
  or an hour ≥ 13); a bare "2:30" stays a claim. The scorer's four date regexes are deleted.
- **F2** one time rule (`connectors/base.py` `as_times`) for the load, cleaning's fallback, pack
  durations, detection and every targeted answer; epoch seconds read as dates (D7-08).
- **F3** exact hours as a term operator beside whole days; a span ending before its start is blank,
  but signed for the range check (D7-09).
- **F4/F5** per-series checks in cleaning (series key, stuck runs, gaps, exact and clashing
  duplicates, a clock change's repeated hour), all kept and said.
- **F6** `invalid_when`: a reading its own quality flag calls Bad is blanked, the row kept;
  Uncertain counted and said.
- **F7** `units`: °F → °C by the row's unit column (U1); an unknown or blank unit beside another
  unit is blanked and named.
- **F8 (U2)** one sensor's reading over time is a control chart: daily means, CL ± 3·MR̄/1.128 over
  30 calendar days, stuck and silent days named, MR̄ = 0 or < 20 points unavailable, said.
- **F9** a ratio's zero-base group is said ("no failure in N h", a lower bound), never dropped or
  ranked, and is a small cell like any other (D7-02).
- **F10** plant-floor things are not people; operators, technicians, logins and badges are.
- **F11** `num_den` for the ratio recompose property.
- **K2** `SHAPES_BY_PACK["iot"]` with two BOOKS generators; P2/P2r/P3/P4 + D on the IoT books.

### Pack and data

`domain_packs/iot.yaml`: OEE and its three factors (pooled, never a mean of shift OEEs), scrap
rate, good count, unplanned downtime, run time, MTBF (with n and "assumes a constant failure
rate"), MTTR (actual start → finish, open WOs left out and said), corrective / open WOs, PM share,
assets, alarms, failures. Unsupported (said): Cpk/Ppk, RUL, rate-unit ideal cycles. never_say:
benchmark verdicts ("world-class"), a sum of readings, "out of spec" from a chart signal.

Datasets (`evals/datagen.py`), each trap asserted to move its answer:
`machine_readings` (17,650 rows, 8 sensors), `shift_production` (2,630 shifts),
`work_orders` (640 WOs, UTC `Z`). `sensor_readings` now reads as `iot` (K4, listed in CHANGES).

### Paid gates

| gate | cases | result | report |
|---|---|---|---|
| smoke (k=1) | iot-4, e2e-82, e2e-83 | analysis 1/1; both E2E failed → 2 product fixes at $0 (89bb51d, 7f7f4d0) | analysis-20261116T213307Z, e2e-…213348Z / 213414Z |
| analysis (k=3) | iot-1..8 | **24/24**, numeric accuracy 1.0, hard 0 | analysis-20261116T214529Z(-rescored) |
| intake (k=1) | intake-iot-01..05 | 4/5; intake-iot-05 read "in control?" as an outlier scan → that intent gives the control chart too (b5a6752), the case accepts either: **5/5** rescored | intake-20261116T220121Z(-rescored) |
| insight (k=1) | ins-50..53 touched | 2/3; ins-53's must-not pattern caught a negated sentence → pattern fixed: **3/3** rescored | insight-20261116T220202Z(-rescored) |
| quality checks-only | 52 cases | **0.865** (K3 0.86) | quality-20261116T234132Z |
| quality live | 52 cases | **0.827** (K3 0.82), held-out 0.875 | quality-20261116T233837Z |
| judge (K5) | 14 IoT tuning sentences | 14/14 on v3; held-out frozen | prose_judge-tune-20261116T233453Z |
| E2E gate (k=1, a23d8bd) | e2e-31, 32, 82–90 | 8/11, hard 0; 3 diagnosed at $0 (below) | e2e-20261117T102606Z |
| E2E k=3 on failures (c3915d9) | e2e-83, e2e-86 | **6/6**, hard 0 | e2e-20261117T105133Z |
| E2E k=1 re-run (user's call: no k=3, ~$0.81 over the ask point) | e2e-89 | 0/1: **open, D7-19** | e2e-20261117T152306Z |
| intake re-measure for the v2 follow-up rule (k=1) | 19 follow-up / injected / out-of-scope + 5 IoT | **24/24** | intake-20261117T105108Z |
| final re-measure (a23d8bd) | iot-1..8 k=3; ins-50..53 k=1 | analysis **24/24**, numeric accuracy 1.0, false-finding 0.029, hard 0; insight **4/4**, hard 0 | analysis-20261117T102615Z, insight-20261117T102814Z |

K3 baselines (B0, main worktree): insight 0.96 (k=1), quality checks-only 0.86, live 0.82.

### Live stack, 1440 px

Production mode, asyncpg, real worker, `scripts/live_check`. Mocked, said: REST API and Sheets
sources only. Every value equals the generator's truth.

| source and dataset | read | truth |
|---|---|---|
| shift_production via **CSV** | OEE 70.8%, availability 84.1%, performance 86.6%, MTBF 42.29 h; OEE by Line "Not measurable, its base is 0: Line D" | 0.7079, 0.8415, 0.8663, 42.29 h |
| work_orders via **Excel** | MTTR 3.97 h, corrective WOs 311, open WOs 40 | 3.9729 h, 311, 40 |
| machine_readings via **JSON** | assets 8, alarms 264, 17,630 rows (20 exact duplicates dropped) | 8, 264, 17,650 − 20 |
| shift_production via **Postgres** | the same tiles as the CSV | — |
| Dashboard, filter shift = Night | OEE 70.7%, MTBF 40.05 h, Good count by Line rescaled, every footer 790 rows | 70.73%, 40.05 h, 790 |

Finished domains at $0 (mock stack, `make_fixture.py --dataset`, one dataset each of retail,
banking, healthcare, insurance, HR, SaaS): a filter changed a tile, an off-axis chart and the
footers on each. `npx playwright test`: 40/40, `iot_dashboard.spec.ts` included.

### The E2E gate's three failures (diagnosed from stored outputs at $0)

- **e2e-83** (multi-turn, "and on night shifts only?"): intake kept OEE by line but put the filter
  in fields the schema doesn't have (`filter_column` / `filter_value`, dropped), so turn 3 repeated
  turn 2. Fix: intake v2 says a narrowing follow-up is `segment_column` + `segment_value` beside
  `group_by` (the product already answered that shape). The turn check now asks the answer to name
  Night: night OEE (70.73%) sits within 0.3% of the whole (70.79%), so a number couldn't tell.
- **e2e-86** ("average temperature on each line?"): the column answer named the leader and the
  lowest of 3 lines, never line C (the °F-converted one). Fix: 5 groups or fewer are each named.
  This moved the finished domains' column group answers by those words only (`domain_diff --show`).
- **e2e-89** (work-order overview): the model's own code counted failure codes over cancelled
  orders (333; truth 311), 2 runs of 2. `standard_stats` replaces the model's level totals and
  means, never its counts. Open, pinned xfail-strict (D7-19); the user chose a k=1 re-run over k=3.
- **Harness:** the rescore of these runs passed e2e-83 on nothing: an E2E rescore re-scored only
  the first question (only the last turn's session is stored) and dropped the follow-ups' checks.
  A follow-up check now keeps its stored result (PR7-04 raised); the one earlier case it hid,
  healthcare e2e-58 turn 2, was read by hand (14.3% vs 14.25%: right).

## Spend: $4.03 of $5 (ask $4.40, stop $4.70)

- Before the E2E gate: $1.54 = eval reports $0.92 (smoke $0.20, analysis gate $0.46, intake
  $0.08, insight $0.10, quality $0.07) + judge $0.06 + live $0.57 (5 runs vs $0.96 estimated).
- The smoke's shift overview cost 2.6× its estimate (the 30% rule): asked, the user said continue;
  later estimates recalibrated from it.
- Final re-measure $0.56; E2E gate $0.91 (estimate $0.85); intake re-measure $0.19; e2e-83/86
  k=3 $0.53; e2e-89 re-run $0.31. e2e-89 at k=3 (~$0.81) would have crossed the ask point: asked.

## Audit (procedure from 5.7b-3, first run)

Brief: the invariants (P1–P5, D, hard rules, `test_one_definition`) and the class ledger, never
the diff. Each finding was closed by a check proven by planting the bug back (red before green).

| pass | model | majors | per class | minors |
|---|---|---|---|---|
| 1 | Sonnet | 7 | C6 3, C5 2, C0 1, C2 1 | 7 |
| 2 | Opus (the fix areas) | 4 | C0 2, C5 2 | 1 |

**Pass count: 2** (target ≤ 2, met). Pass 1's C6 majors recurred one class (exact vocabulary:
"Corrective Maintenance", "Cancelled - duplicate", "Bad - Sensor Failure"), so the fix was
structural, per the user's call: any status or closed-word value no pack list names is said in the
audit (`unlisted_values`, every pack), flags match by their first word, units by their letters.

After the audit:
- **code review** (`mattpocock-skills:code-review`, Standards + Spec vs the plan): S-b (one machine
  with no failure said "base is 0" → D7-14), T12 (performance > 100% planted but unsaid → D7-15),
  the control-chart baseline (D7-16), the plan's detection negatives;
- **the live check** found D7-18 (OEE by Line dropped Line D unsaid on the chart);
- **the mutation gate** found two real bugs: "OEE and MTBF for M08" answered the plant's MTBF as
  M08's (D2-09, count 5), and its first fix let a second KPI show a small protected group (5
  people) — found by the automated security review, fixed with `full=whole`; and a wording bug
  ("1 days repeats").

Mutation gate: 0 unexplained survivors on lines changed since main; equivalents named in
`docs/mutation/gate.json`; `--record`; `--check main` clean.

Ledger: D7-01..19, two Open and pinned xfail-strict: D7-13 (two fixed offsets an hour apart read
as one place across a clock change) and D7-19 (a model-written count by group reads every row).
UNENFORCED 77 (≤ 77).

Golden re-pinned at the end (`evals.snapshot pin`, CHANGES emptied). Its diff: `sensor_readings`
read as `iot` (K4: cases iot-1..3, e2e-31/32); "needs X and X" said once and "MRR" for "mrr" in
unavailable answers (claims, encounters, HR, inpatient, loss run, SaaS); a column's group answer
of 5 groups or fewer names each group (e2e-86). No number moved.

## Post-merge review (2026-11-17, `mattpocock-skills:code-review` on ec48735..main)

Two fresh reviewers (Standards, Spec) and a $0 browser read of all three IoT dashboards at
1440 px. **Recurrences of classes fixed before** (each row raised in the ledger):

| row (class) | came back as | fix |
|---|---|---|
| D7-06 (C7) | `as_times` read 'M01' beside dates as 1 Jan of year 1 | a value with no year is NaT (one `HAS_YEAR_RE`) |
| D7-08 (C2) | three epoch rules: ingestion typed ms as dates, cleaning / answers read seconds only; the per-series checks had their own `pd.to_datetime` | one `epoch_times` (s or ms) for all; guard over cleaning_agent |
| D2-10 (C0) | the login pattern read upper-case categories as people (Operator VERIZON, Lead WARM, Login SUCCESS) | logins only in system-user columns, 3+ distinct |
| D1-16 (C6) | "total temperature by line" summed a reading (Line B 468,696.77) | a number role is averaged whatever the words, said; T18's naive twin planted |
| D7-11 (C0) | a stuck run's days counted beyond the control limits (S03: 3 of 4) | a run's every day is named, never a point beyond |
| D1-03 (C0) | a work order's blank actual start / finish "left as missing" | `conditional_fields` by status; a finished order's blank finish still counted (3) |
| D7-14 (C5) | MTBF for a machine that never failed: "I couldn't answer this" | answered, "(a lower bound)"; `KPI.says` gives MTBF's failure count and assumption, MTTR's open orders |
| D7-19 (C1) | the generic breakdown charts count cancelled orders (CM + EM = 333) | **open**: a one-filter fix moved 10 finished datasets and would drop rows that still count; pinned xfail-strict |

New, fixed: the over-100% flag filed under the ideal cycle (was changeover, alphabetically);
preventive share is a work-order tile; MTTR's How says its corrective list once; a range
filter's bounds read 66.04, never 66.04444444444445.

Not fixed (judgement calls, follow-ups): "sensor" / "instrument" words in shared code (C9);
the named-value filter written twice (`_also_named`, `handle_kpi`); the series strategy
strings matched by string in insight.

**Browser read, all at $0 against generator truth:** shift report (OEE 70.8%, availability
84.1%, performance 86.6%, quality 97.1%, MTBF 42.29 h; Line B: 68.7%, 81.5%, 22.87 h, 789
rows); work orders (MTTR 3.97 h, 311 corrective, 40 open, PM share 48.3%; CM: 3.52 h, 255, 17);
readings (8 assets, 264 alarms; Line C: 4,418 rows, °C). Charts rescale and footers follow the
filter; the breakdowns a filter can't recompute say "SHOWS UNFILTERED DATA". Not checked at $0:
the chat report's narrative (needs the model; read live before the review, on 4 uploads).

Review checks: re-measure of the changed contexts iot-4/5/6/8 12/12 (k=3), intake-iot-03 1/1
($0.27; Domain 7 total $4.30 of $5); full suite green but one stored run's replay (named);
Playwright 40/40; mutation gate on `kpi_measures` and `describe_kpi` 0 survivors, recorded,
`--check main` clean. Below the gate (not gated, survivors left): `as_times` 0.68, `pii_kind`
0.80, `_says` 0.73.

## Deviations

- The analysis gate ran on 89bb51d, before the audit; the final re-measure on a23d8bd covers the
  touched cases. The E2E fixes after it (c3915d9) touch intake's prompt and the column group
  answer, both re-measured (intake 24/24, e2e-83/86 6/6).
- **E2E is not 100%:** e2e-89 failed 2 of 2 (D7-19, open, the user's call). The README lists IoT
  as not yet passing every run.
- The intake prompt change was re-measured on the follow-up, injected, out-of-scope and IoT cases
  (24), not the whole 141-case suite (cost).
- `machine_readings` has no KPI generator in BOOKS (only nunique / sum tiles).
- `spend.py --since` reads local time, not UTC (a follow-up).

## Not built (said)

Long / tag-format historians, counter-reset differencing, Maximo task / child WO rows, hourly or
per-shift chart buckets, Cpk/Ppk, run rules beyond 3σ, survival models, production hours joined
onto WOs, rate-unit ideal cycles, failure prediction / RUL, time-weighted reading means.

## Follow-ups

- D7-19: recompute a model-written count by group over the counted rows (as levels' totals and
  means are), then re-run e2e-89.
- D7-13: fixed offsets an hour apart (the load's DST pair rule since D1).
- PR14-01: four packs still lack property generators (due before v0.1).
- `spend.py --since` should take UTC.
