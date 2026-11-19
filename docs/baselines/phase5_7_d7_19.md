# Phase 5.7 follow-up — the D7-19 PR: one row rule for counts and rates by group (2026-11-19)

Wall clock: 5.1 h (first commit 2026-11-19 10:47 → merge 2026-11-19 15:50)
Plan mode before it: ~0.8 h. Budget 6 h; the breaker never fired (3.0 h was reached inside the paid
step, the projection stayed under 6 h). Time sinks: `evals.snapshot text` ran over an hour twice
(PR24-02, fixed); CI 15 min; the paid re-measure's two fix rounds.

Plan: `~/.claude/plans/pasted-content-id-2e32-start-the-buzzing-rabin.md`. It checks off every
plan-mode step of the ROADMAP 5.7 contract by name:
- **brainstorming:** the request played back against the code; the three measured failures
  (e2e-89 333/311, logi-4 UPS 96.2%/93.7%, logi-7 1.4027/1.4424) and the generic charts' 3,000 for
  2,840; four root causes, one fix each (R1 no rule for a count's rows, R2 `standard_stats` never
  asked `measure(by=)`, R3 the guard's `PROFILE` exemption was the second copy, R4 the ruled-out
  filter dropped the kinds a Status breakdown exists to show).
- **research:** two background agents. One on the three user decisions (SQLBI / DAX Patterns,
  dbt MetricFlow ratio metrics, Looker filters, IAS 21, Tableau's null indicator, ONS chart
  footnotes, CONSORT 13b, Google Small CLs, DORA), one on `addyosmani/agent-skills` (3 of 26
  installed: doubt-driven-development, deprecation-and-migration, constraint-driven-development).
- **domain-modeling:** a row kind × statistic term table; GLOSSARY: Counted record,
  Kind-deciding column.
- **codebase-design:** deletion test (the count rule has 9 readers: `standard_stats`, 5 count
  builders, the user's chart, the distribution answer, the drill-down); no new pack field.
- **red-team:** one fresh subagent, claims checked in code: 5 majors (the golden pinned none of
  the moved outputs → probes first; the analysis suite is not in `test_replay` → a $0 replay
  script; the guard needs the rule in each builder's own source; the Status exception must keep
  currency out; rows ≠ records), all folded into the plan.
- **grilling:** G1–G5 answered as a senior engineer; the user's calls Q1–Q3 (below).
- **sizing:** 6.3 h / ≤ $1.45 projected; actual 5.1 h / $1.99 (the paid re-measure found two bugs).
- **stop rule:** properties, `scripts/mutation.py --check` and `tests/test_retro_ledger.py`
  green before the PR (the audit procedure's rule, not "an auditor found nothing").

The user's calls: **Q1** counts follow the tiles (other-currency rows out of both, said); the
research favours counts across currencies with money split, a later banking follow-up in the one
rule. **Q2** every count by group says what it left out, server-side, from the same kinds. **Q3**
e2e-97 stays out (its own PR).

## What changed

- `domain_pack.count_rows(df, contract, by, full)`: THE rows a count of rows by a column reads:
  the count tiles' rows (sale kind, main currency), except the kinds the column itself names
  (`kind_labels`: status, currency, a void or non-product code); kinds judged without that column
  and on the whole data under a filter. `counted()` also covers a number a KPI sums (Weight).
- `standard_stats` → `product_breakdowns`: a model segment labelled with a pack KPI's **exact**
  name (never a qualified label, never by a date, never by a column the model frame added) takes
  `measure(by=)` on the data's own columns; a plain every-row count takes the counted records;
  each segment's evidence n is the counted records; a category's built-in distribution is
  recounted. Each change is said in an assumption.
- The count builders (value counts, donut, top-N bar, stacked, target rate), the user's
  "rows by x" chart, `group_bars` counts, a category's distribution answer and the drill-down's
  rows per x read `count_rows`; the builders left `PROFILE` and take `ctx` as a required keyword.
- `scripts/replay_breakdowns.py` (new): a post-model step over every stored analysis output, on
  the production frame; `evals.snapshot text` reads lists item by item (it ran over an hour).

## Deviations from the plan

- G2: the label's words through `named_kpis` were dropped: the $0 replay showed 16 stored runs
  flipping to fail (a label's own window became the all-time KPI). Exact name only.
- The plan's `rows_for(count=True)` became its own function `count_rows` after the Standards
  review (CODING_STANDARDS: a parameter that changes a shared rule's answer is required).
- Added after the paid re-measure (not in the plan): measuring on the data's own columns, the
  segment n and the distribution recount (each found by a failing paid run, fixed at $0, re-run).
- The filtered dashboard's `rows` header counts the part's rows (a table count, earlier versions
  out), not counted records; the drill-down's rows per x carry no left-out note (no text surface).

## Measured

| Gate | Cases | Result | Report |
|---|---|---|---|
| $0 replay (155 stored runs) | every analysis case with segments | 0 flip to fail; logi-4 3/3 and one hr-7 run flip to pass | `scripts/replay_breakdowns.py` |
| analysis k=3 | logi-7 | **3/3** | analysis-20261119T181921Z |
| analysis k=3 | logi-4 | 0/3 (`measure` on the model frame: role_missing, segment emptied) → fixed → **3/3** | …181740Z, …184844Z |
| E2E k=3 | e2e-89 | 0/3 (evidence n and built-in value counts every row: '333 work orders with a failure code') → fixed → k=1 1/1 → **3/3** | e2e-…184927Z, …190055Z, …190331Z |

Golden: CHANGES-only on all 8 domains (43 datasets; each move with its truth: work_orders 39
cancelled out, CM + EM = 311; tms_shipments 2,840; health_claims line 1 = 2,668 claims; loss_run
3,975 claimant rows; dock 38, order_lines 110, saas_invoices 76, loan_tape 106, b2b 266 + 119,
retail 111 CAD, card_ledger 1,490 EUR). `domain_diff.py main --show` adds deposits_daily (the
Segment donut 1,468 → 888: 1,776 other-currency rows out, as the open-accounts tile; Q1) and
loan_tape_messy (106 out). Paid suites' contexts unchanged (the change is after the model).

Mutation: `rows_for`, `count_rows`, `kind_labels`, `counted`, `_summed`, `handle_distribution`
100% on their changed lines, recorded; `product_breakdowns` (not gated) 87%. Plants caught: a
donut counting every row, the distribution answer's count mode, the three left-out notes.

## Spend: $1.99 of $2.50 (ask $2.20, stop $2.35)

logi-4 k=3 $0.18 (2× its $0.09 estimate: stopped and asked; the user chose to continue) and
$0.09; logi-7 $0.06; e2e-89 k=3 $0.68, k=1 $0.23, k=3 $0.72.

## Reviews and audit

- Doubt-driven review of the rule's first draft (fresh subagent, artifact + contract only): 5
  actionable (an id column switched off its version chain; a KPI label on a Status breakdown;
  'Mode=LTL' names; malformed segments; a non-additive summed number), 1 trade-off (kinds judged
  by index label, as `measure`), nits. Cross-model (Gemini / Codex) offered: neither CLI installed.
- Code review, Standards axis: 1 hard (required parameter → `count_rows`, `ctx` required); smells
  noted (five near-identical builder calls, `segment_key` beside `segment_id`).
- Code review, Spec axis: Q2 notes missing on four paths (fixed), plan deviations (above).
- Audit pass on Sonnet (invariants + class ledger): 1 major, PR24-03 (a count by group counts
  rows, the count tile distinct records or entities in force: loss_run 1,098 claimant rows of
  Collision for 940 claims), Open and pinned xfail-strict; 1 minor fixed (an all-left-out group is
  0, never dropped); 1 judgement call (return lines out of counts, as the orders tile).

Ledger: D7-19 closed (its pinned tests plain + the cross-pack property); PR24-01 (C6, the label
glossary match), PR24-02 (G, snapshot text), PR24-03 (C1, Open); D3-10 recurs (the tests and the
$0 replay called `standard_stats` on the plain frame; a run hands it the model's frame).
UNENFORCED 77 (≤ 77).

## Gates

- live browser at 1440 px: not done (no new domain; the $0 mock-stack filter read and Playwright cover the moved charts; spend kept for the re-measure)
- finished domains' filter reads: done (one dataset per pack through the mock's `filtered_dashboard`: a filter changes tiles and charts on all 8, every count chart = the counted records of the part)
- all-domain answer probe: done (the count-answer and dashboard golden probes on every eval dataset, and the Sonnet audit's count-by-group probe over 18 datasets)
- playwright specs: done (44/44)

## Not done / open

- PR24-03: counting by record key (claims, shipments, headcount) needs a partition rule.
- e2e-97 (D1-16): its own PR (Q3).
- Counts across currencies (Q1's end state): the banking follow-up, one change in `count_rows`
  and `measure`'s currency line.
- The profile histogram of a measure still reads every row (5.8, `PROFILE` OPEN note).

## Post-merge quick audit (2026-11-19)

Report: UNENFORCED 77 (≤ 77); 27 rows with ≥ 2 recurrences (27 at step 0); recurrences raised by
this PR: D3-10 (5 → 6, a $0 harness off the production path). New rows: PR24-01 (C6, closed),
PR24-02 (G, closed), PR24-03 (C1, Open, pinned xfail-strict). Main CI after the merge: green.
Correction: D7-19's finding text no longer starts "Open:" (its fix closed it in this PR).
