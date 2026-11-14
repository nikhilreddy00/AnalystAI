# Phase 5.7b-2 — Structural fixes, session 2: the recurring classes ($0)

Plan: `~/.claude/plans/pasted-content-id-5014-start-phase-giggly-wand.md`. It checks off every
plan-mode step by name:
- brainstorming, with the scope fixed by the ROADMAP;
- the `domain-modeling` term ledger;
- `codebase-design`;
- research (not needed: the tool notes cover it);
- grilling, answered as a senior engineer;
- one red-team: 1 blocker, 9 majors and 6 minors, all folded in.

The 9 ROADMAP steps didn't fit one session, so the user approved three:
- **2a** (this section): the `Window` and `KPIValue` types and the snapshot cadence;
- **2b**: the ledger (C3 transfers, C1 `term_rows`, the scorer reading the reference, C4);
- **2c**: words and scale (C6, C7 scale, C9, the pre-push gate). "Phase 5.7b-2 done" comes there.

Spend: $0. There is no model call anywhere in the session.

## 5.7b-2a: Window, KPIValue, snapshot cadence (2026-11-10)

### The user's decisions (asked in plan mode)
1. **An asked period moves the bridge, not the cohort rates.**
   - Opening MRR on its own opens where the period's moves start (`Window.moves_from`).
   - NRR, GRR and logo churn keep the 12-month cohort ending at the period's end
     (`Window.cohort_start`, expert M3).
   - I first recommended the opposite. The red-team found M3's pinned test, and found that a
     dashboard date filter would have turned NRR into a one-week cohort, so I asked again.
2. **Paused and unpaid subscriptions stay out of every month-end, and it is said.** A
   subscription list holds its status for today only, with no date it began. The story and every
   spell answer now say so (`Spell.today_only`). No number changed. This deviates from ROADMAP
   step 4's "churn at their date", because there is no date.

### Measured first (main `07d63ab`)
| What | Before | After |
|---|---|---|
| xfail-strict known failures | 12 (C2 ×3, C3 ×1, C5 ×5, C7 ×3) | 3: C3 ×1 (2b); PR7-01, a new C1 found here (2b); PR7-03, `{window}` in pack prose (2c) |
| golden, `domain_diff.py main`, `snapshot text main` | 53 passed, 28 identical, 0 changes | CHANGES below |
| Majors per class (ledger) | C2 29 rows, 7 unenforced; C5 12, 4 | C2 30, 6; C5 14, 4 (`docs/retro-ledger.md`) |

### What was built
- **C5, `KPIValue`** (`domain_pack/pack.py`, `kpi.py`):
  - One `measure()` returns `Measured`, `MeasuredBy` (per group or period, with its hidden small
    cells) or `Unavailable(reason, roles)`.
  - Each reason is decided where the value is computed, and `unavailable_words` says it.
  - Product code never calls `compute_kpi`, which is now the tests' float-or-None view. An AST
    guard enforces that.
  - Rules:
    - a blank column reads as a missing one: the pack's formula without it, else unavailable;
    - a spell list without its end date can't count ends, churn, reactivation or retention;
    - nobody in force is a measured 0;
    - a part with none of a column's values while the data has them is a measured 0.
  - The type review was adopted: the scalar and grouped types split, invariants checked in
    `__post_init__`, and roles held as a tuple.
- **C2, `Window`** (`domain_pack/spell.py`):
  - One constructor, `window()`, through `_cut`. Its fields are `lo`, `as_of`, `month_ends`,
    `first`, `known` and `asked`.
  - Properties: `first_day`, `moves_from`, `cohort_start`, `full_cohort`, `months`,
    `move_months`. Methods: `year_earlier()` and `period()`.
  - The month-end rule: a span of exactly N months has N month-ends (the reference's 12 on a
    mid-month as-of); any other span has one per month it touches (D5: since 15 Mar keeps
    31 Mar).
  - Every reader now names Window fields: the ledger, kpi, rows, story, targeted and
    window_words. `SpellWindow`, `spell_window` and `cohort_start` are deleted.
  - The type review was adopted: the inputs live on the Window, and year-earlier and per-period
    windows are methods.
- **C7, declared cadence:**
  - `Spell.snapshot_every: month` in the SaaS and HR packs, so `snapshot_rows` no longer guesses
    from an entity's own gaps.
  - Banking and insurance declare none, so they keep the guess.
  - The differential test D now runs on MRR exports with broken runs.
- **Gate tooling:**
  - `scripts/mutation.py --lines REF` runs only the mutants on lines changed since REF. A
    mutant's line is read from mutmut's generated file, so there is no run on unchanged lines.
  - `source_paths` now also covers the three changed agent files.

### Flipped (the xfail marker removed in the fix's commit)
| Class | Test | Commit |
|---|---|---|
| C5 | P4 blank column ×3; P4 `canceled_at` with no end date; D nobody paying | `6b303a6` |
| C2 | P1 asked window; P5 mid-month as-of; the rebuild guard (widened to `evals/`) | `af75e7f` |
| C7 | D customer absent from the newest month ×2; D person absent from the newest snapshot | `e044c0d` |

### CHANGES (each a wrong output of a class, with its truth)
| Where | Change | Class | Truth |
|---|---|---|---|
| saas_subscriptions story, ins-49 context, every spell answer's How | "10 rows are paused or unpaid today: … out of every month-end's MRR, never churned at a guessed date." | C5 | user decision 2026-11-10; no number moved |
| ins-16 (a 20-row ticket list read as SaaS) | no longer names churned MRR and logo churn among its KPIs | C5 | it has no end-date column: unavailable, not 0 |
| bank_statement, filtered (ATM, direct debits) | the pack strip ($0.00 inflows, −$5,760.93 net flow) replaces the generic tiles | C5 | Credit is blank on debit rows, the Dr/Cr layout; the statement has credits |
| card_ledger, ledger_drcr, sales, store_weeks | an unanswerable KPI says why ("the data has no rows of the kind it counts") | C5 | measure's reason |
| hr_snapshots contingent workers by department | "there are no rows to count", not a guessed "every group has fewer than 11 people" | C5 | there are no contingent workers |
| employees, hr_snapshots, inpatient filters | a tile the part can't support shows "—" with its reason (its base is 0); FTE of Terminated is 0.00 | C5 | the strip keeps the whole data's tiles |
| saas_subscriptions filtered `interval_count=3` MRR line | months before any quarterly subscription read $0 | C5 | nobody in force is a measured 0 |
| reference oracle | one month of data: no moves, no cohort (it said $0 and 0%) | C5 | GLOSSARY Unavailable: "no month of moves" (ledger PR7-02) |

No window change moved a number: every default window is byte-identical.

### Found on the way (ledger)
- **PR7-01 (C1):** on HR snapshots, a return after a gap is no hire. Pinned for 2b.
- **PR7-02 (C5):** the one-month case. The mutation profile's draws found it.
- **PR7-03 (C2):** `{window}` in pack prose, and billings' period. Moved to 2c and pinned.
- **PR7-04 (B):** two filter tests passed vacuously on the generic tiles.
- **PR7-05 (C5):** a caller guessed a reason.
- **PR7-06 (G):** a baseline ran on a half-edited tree.

### Deviations from the plan
- **`{window}` in pack prose moved to 2c, which needs the user's sign-off.** The plan put it in
  B1. Filling it in caveats means carrying the window through the cleaning audit, a new stored
  field that changes every stored spell replay. It is pinned xfail-strict (PR7-03).
- **Decision 2 deviates from ROADMAP step 4,** as above. It is said in the story and in every
  spell KPI answer; dashboard tiles and the non-KPI handlers don't say it.
- **`KPIValue` has three variants, not two (`MeasuredBy`), and 11 reasons, not 7.** The type
  review split the scalar from the grouped value; each reason is a distinct None source in the
  code.
- **One rule not in the plan's decisions:** a part whose column is blank while the whole data
  has values is a measured 0. It is the cause of the bank_statement CHANGES entry.
- **Decision 1's "or a denominator"** is implemented as "the asked KPI has a retained term". No
  pack KPI has a cohort opening behind a denominator without a retained term.
- **P5 compares every Window field** (asked or not, and the year earlier). The readers in
  `handle_kpi` and the dashboard read only those fields, under the widened rebuild guard;
  there is no per-reader behavioural check.
- **The reference's one-month cohort** follows GLOSSARY's Opening cohort, sharpened this
  session ("a cohort needs a span"). The entry was silent on one month. This is a judgement call
  for the user to check.
- **`compute_kpi` is kept as the tests' view** (about 170 call sites), guarded.
- **The plan's `Asked` type was skipped.** `first_day` removes the exclusive/inclusive trap.

### Mutation gate (changed lines)
`scripts/mutation.py --changed-since main --lines main`, run in a throwaway worktree:
- **First run (b1c1652):** 1,483 mutants on changed lines, **183 survivors**. Most were text
  (each reason's clause, 44), story's year-earlier facts, the per-period average and the
  `Window` invariants.
- **What closed them:**
  - Tests, each plant-proven (`--plant` printed "caught"): `tests/test_kpi_value.py` (a hand-written
    clause per reason); in `test_spell.py`, the Window invariants, an exact-N span from the 2nd,
    the reason per spell case, the monthly and quarterly averages, the year earlier (asked start,
    asked end only, a shorter prior), and today-only's singular; in `test_ledger.py`, the
    snapshot book's unknown first month and the by-month cohort rate. The story, also-named and
    spread facts are checked against `evals/reference_kpis.py` (`test_saas.py`,
    `test_hr_audit_guards.py`, `test_dashboard_filters.py`).
  - Dead code deleted, because a Window's month-ends always end at its as-of: the average's
    "no month-ends" guards, the ledger's NaN-cohort reason, a redundant `lo < as_of` clause,
    story's `full=df` next to `df`'s own frame, and `_amount`'s unused `pack`.
- **Re-runs at 11b4c5a and d087738:** 456 + 372 mutants; 18 + 22 survivors, every one then
  killed by a plant-proven test, deleted, or named below.
- **Final run at c3d5a69 (the functions edited after those):** 371 mutants, 6 survivors. All
  six were on the story's money-peak sentence and a by-month fact's full stop. They are now
  killed by `test_ledger.py::test_the_story_says_a_money_stocks_fall_from_its_peak_and_each_months_moves`,
  all six plant-proven. **0 unexplained survivors on changed lines.**
- **Equivalents (11, with reasons):**
  - `snapshot_rows` 30→31 days, and 86400→86401 seconds: a declared month is read only as "at
    least 27 days".
  - `_ledger_term`: `None` or `"NAN"` for `float("nan")` (×3); `.reindex(axis)` and
    `dtype=float` on a dict already keyed on the axis, with float values (×3); a month-end two
    months before the data instead of one (an all-zero column moves nothing).
  - `_also_named` `kind == 'currency' or True`: only the currency kind reads a unit.
  - `_spread` `group or True`: with no group, `measure` is a scalar, so the result is "".

### Suite and CI
- **Full suite at d087738 (worktree):** 3,018 passed, 128 skipped, 3 xfailed. The xfails are
  C3 per-segment and PR7-01 (2b) and PR7-03 (2c).
- **`domain_diff.py main`:** the same 9 datasets changed as at b1c1652, each a CHANGES entry;
  19 identical. **`snapshot text main`:** 3 changes, all the today-only sentence (CHANGES).
- **Deep Hypothesis profile (500 examples):** 54 passed, 2 xfailed. One deep failure earlier in
  the session did not reproduce, and its traceback was not kept.
- **CI:** see the PR.

## 5.7b-2b: the ledger (2026-11-12)

Plan: `~/.claude/plans/pasted-content-id-0e41-start-phase-structured-pizza.md`. It covers ROADMAP
steps 2, 3 and 7, and the 2a plan's decisions 7–9. Every plan-mode step is checked off by name.
The red-team found 3 blockers, 8 majors and 7 minors, all folded in. Spend: $0.

### Measured first (main `a89b3b0`, a throwaway worktree)
| What | Before |
|---|---|
| xfail-strict known failures | 3: C3 P1 per-segment, PR7-01 (C1), PR7-03 (C2, for 2c) |
| golden | 53 passed |
| `domain_diff.py main` | every dataset identical |
| `snapshot text main` | 0 distinct changes |
| Majors per class (ledger) | C1 30 rows, 9 unenforced; C3 1, 0; C4 2, 1 |

The worktree needed `uv sync --all-extras --all-groups`: a fresh worktree's venv has no
pytest-asyncio, and the golden fails 52 of 53 without it.

### GLOSSARY (B0)
- New: Part, Term rows, Transfer move (inside Move), Move breakdown (was "a part of the level":
  "part" now means a figure's rows).
- Sharpened:
  - Ledger is one line per (entity, part).
  - Move books its rest in the parts that moved, not "where its later row is" (the cause of
    PR1-03).
  - Opening cohort says a cohort places its entities by their opening row.
- `source_paths` gains `agents/analysis_agent.py` and `evals/scoring.py`. The reference oracle
  stays out: the generators check it, and mutating the truth against the product is circular.

### What was built
- **C1, one `term_rows`** (`domain_pack/spell.py`, beside `_in_force`).
  - It returns the rows a spell term reads: its row kinds, its condition, and with a date those
    in force then.
  - `_spell`, the ledger and `kpi.stock_rows` read it. `stock_rows` is the stock tile's rows,
    read by the model frame's `in_force_now` / event markers (`with_spell_events`), a level by
    group (`utils._in_force_rows`) and the story's churned entities, which read raw `_in_force`
    before. Their copies are deleted. The owner guard names `term_rows`.
  - A characterization test came first: on all 8 spell datasets, the marker's rows are the
    stock tiles' rows. It passed before and after.
  - `people_in` (the small-cell rule) is a different rule: people in force at any point of a
    window, with no row kinds. It is left alone.
- **PR7-01** (`_spell_events`). On snapshots with no start date and a declared monthly cadence,
  an entity's snapshots split into runs at a missing month. A return is a hire, a month with no
  file at all is no gap, and snapshots on each entity's own day are one month. The exit is the
  first month a run is missing from, so the staggered-day case no longer reads a false exit. D
  now runs the snapshot books with rehires.
- **C3, the entity × part ledger** (`domain_pack/ledger.py`).
  - `_ledger` keeps one line per (entity, part): in or out of a filter's part, and its `by`
    group.
  - `_moved` splits each step per entity. The matched fall and rise across its lines is a
    transfer, each line's share in proportion. The rest is classed on the entity's whole value
    and booked in the lines that moved. On a count, a part gained or lost while the entity stays
    is a one-sided transfer.
  - A bridge's opening reads lines. A cohort rate keeps the entity placement (decision 1, plan
    red-team 5).
  - SaaS gains `transfer_in_mrr` / `transfer_out_mrr` (non-tile, 0 on a whole book).
  - A move by group says "<group>: $X moved in from another segment and $Y moved out to another
    segment (customers who changed segment: never new or churned)". The words live in
    `Spell.words.moved` (saas.yaml), and a guard keeps "transfer" out of every glossary and those
    words.
  - The reference has the same rule, written from GLOSSARY. It was checked against the
    generator's churned MRR by month, the 8 scenarios and its own segment bridge on P1's books,
    before the product was compared to it.
- **C4** (`agents/analysis_agent.py`). `_moves_asked(task, contract)` replaces `_a_part_of`: a
  breakdown filed under a level's column is kept when the question names a move (the task's KPI,
  else `named_kpis`), and is otherwise the level, whatever its values. `repair_breakdowns` takes
  the task. The replay over 8 stored reports is unchanged.
- **The scorer reads the reference** (`evals/scoring.py`, `evals/run.py`).
  - On `SPECS` datasets a level by group is one of `reference_by`'s readings: the rows in force
    (any `agg`), per entity, and each move by month (amount or mean per entity). A group with
    nobody in force reads its every row (pass 8).
  - `_move_means`, `_per_entity` and `_populations`' `level_rows` branch are deleted.
  - A kept sum of a level is scored against the reference's moves by month. Off spell data it
    stays unverifiable, as before.
  - `saas_accounts` gets a Spec, checked against its truth and the product.
  - **Scorer change (recorded per the eval rules):** accepted readings for a level by group on
    spell data are now the reference's. Two kinds of claim become verifiable: a kept move
    breakdown, and every move kind's mean by month. Two test factors moved because ×1.07 and
    ×1.13 now land on another true number (Sep new MRR per customer; May new MRR).

### Flipped (the xfail marker removed in the fix's commit)
| Class | Test | Commit |
|---|---|---|
| C1 | `test_d_a_person_back_on_the_snapshots_after_a_gap_is_a_hire` (PR7-01) | `a27c933` |
| C3 | `test_p1_the_bridge_adds_up_per_segment_for_a_customer_who_changes_segment`, rewritten under its name: P1's books gain concurrent rows and movers; per segment, by group, Σ in = Σ out | `e0219b5` |

### Found on the way (ledger)
- **PR9-01 (C3):** D's new per-segment check found NRR / logo churn by segment following the
  file's row order when a customer pays in two segments at the cohort's opening (main had it
  too). The placement is now the first group in sort order (GLOSSARY Opening cohort).
- **domain_diff, on the branch:** a count's entity with lines in and out of a filter counted
  twice in its cohort (logo churn by segment on a filtered dashboard moved). Fixed before merge,
  so there is no ledger row: a count entity's value is the max of its lines, pinned by
  `test_a_customer_with_rows_in_and_out_of_a_filter_is_one_customer_in_its_cohort`.

### CHANGES (each a wrong output of a class, with its truth)
| Where | Change | Class | Truth |
|---|---|---|---|
| saas_mrr_snapshots, contraction MRR by segment | SMB $4,572.22 → $3,198.54; Mid $12,850.32 → $14,224.00 | C3 | `reference(...)["contraction_mrr_by_segment"]` (`test_reference.py`, per segment) |
| saas_mrr_snapshots, quick ratio by segment | Mid 2.53 → 2.44; SMB 1.54 → 1.63 | C3 | its contraction term (above) |
| saas_mrr_snapshots, churned / contraction / reactivation MRR by segment | adds the lead's moved-in / moved-out sentence (Enterprise: $0.00 in, $4,191.86 out: PR1-03's "Ent −$4,192") | C3 | `reference(...)["transfer_*_mrr_by_segment"]` |
| saas datasets (5) | two new KPIs answer: MRR moved in / out ($0.00 on a whole book; by group, each group's own) | C3 | the reference (0 in the whole) |

No total moved: every whole-book number, the golden and snapshot text are unchanged.

### The readers of each changed rule (CODING_STANDARDS: a widened rule lists its readers)
- **`_spell_events` (runs by month).** Readers: the hires and separations tiles, rates and
  by-period series (`kpi._spell`), and the model frame's start and end event markers
  (`with_spell_events`). Probes: D (snapshot books with rehires, all packs' shapes) and the
  golden and domain_diff (unchanged on every HR dataset).
- **Cohort placement (first group in sort order).** Readers: NRR, GRR and logo churn by group
  (tiles, answers, the story's facts by group). Probes: D per segment (PR9-01), and domain_diff,
  which shows no cohort change on any dataset.
- **`stock_rows` reads the term's own `rows` (`term.rows or stock.rows`, was `stock.rows`).** No
  pack's stock term sets `rows`, so the rows are the same. Probe: the characterization test on
  all 8 spell datasets.

### Deviations from the plan
- **Words:** one `Spell.words.moved` template, not `moved_in` / `moved_out`. One sentence with
  both amounts reads better than two half-sentences.
- **`_moves_asked(task, contract)`**, not `(task, pack, contract)`: the pack comes from the
  contract.
- **No bridge is said on a part.** The plan named "story's bridge on a part", but `spell_story`
  only ever reads the whole file (cleaning, the targeted story lines), where transfers are 0.
  The by-group answer (`handle_kpi`) is where a part's moves are said.
- **A move named keeps every summed-level breakdown in the findings, not only the move's one.**
  With "churned MRR by month" asked, an MRR-summed-by-segment breakdown is no longer replaced by
  the level. This is a known limit: the scorer marks it false against the reference (spec
  review c2). Narrowing it needs the breakdown's own meaning, which the model doesn't file.
- **`reference_by` offers each move by any group, not only by month** (ec33cae). The rescore
  found true numbers marked false without it. The scorer is more lenient: a sum matches if it
  equals any one of about 14 readings. Each reading is a true number.
- **`subscriptions` gets a Spec too** (test review: spell data outside `SPECS` would score a
  level by every row). It is checked against the product on every KPI both compute.
- **The e2e suite is not rescored:** its checks call neither `false_findings` nor
  `check_segment_value`.
- **The type-design-analyzer step did not run:** no new type was added. The ledger's lines are
  the (entity, part) index of a frame.
- **Rejected review notes:**
  - `_moves_asked` reaching into the pack (Feature Envy): it is one function at its only
    caller.
  - A NaN group in the ledger: it is dropped from groups, as before; no generator writes one.
  - Mean by month double-counting an entity: the ledger points give one step per month, so it
    can't happen.

### $0 rescore (rescore, never rerun)
The latest analysis report of every spell case (saas-1..11, hr-1..10), rescored with the new
scorer:

| Report | Runs | Pass rate | Runs whose score moved |
|---|---|---|---|
| `analysis-20261105T180055Z` (hr-1..10) | 30 | 1.0 → 1.0 | 0 |
| `analysis-20261106T033628Z` (saas-5..9) | 21 | 1.0 → 1.0 | 0 |
| `analysis-20261106T011019Z` (saas-2..4, saas-8) | 33 | 0.8485 → 0.8485 | 2 (below) |
| `analysis-20261106T181002Z` (saas-1, 10, 11) | 3 | 1.0 → 1.0 | 0 |

- **saas-8 run 1:** false findings 0/6 → 0/16. Its churned MRR by segment and plan were
  unverifiable before (a kept sum of a level). They are the reference's numbers, so all 10 are
  now verified true.
- **saas-8 run 0:** false findings 0/78 → 2/81. Its SMB and Mid churned MRR by segment ($22,514.47,
  $18,217.61) match no reading; the truth is $19,937.12 and $22,148.05. The model counted
  customers who changed segment by its own rule, the C3 class.
- **The rescore found a gap in the scorer change.** The first rescore marked run 1's true
  numbers false, because `reference_by` offered moves by month only. It now offers each move by
  any group, through the same line rule
  (`tests/test_reference.py::test_each_move_by_a_group_is_a_reading`).

### Mutation gate (changed lines)
`scripts/mutation.py --changed-since main --lines main`, each run in its own worktree:
- **First run (c3a7872):** 21 functions, **229 survivors** on changed lines:
  - domain_pack: 123 (`describe_kpi`'s How text 41, `_spell_events` 25, `_ledger` 19,
    `_moved` 19, `stock_rows` 12);
  - scorer: 76 (`check_segment_value` 35 with no test, `false_findings` 37,
    `_reference_wants` 4);
  - agents: 30 (`handle_kpi` 13, `repair_breakdowns` 10, `run_analysis_agent` 4 with no test,
    `_moves_asked` 3).
  The test list left out `check_segment_value`'s and `run_analysis_agent`'s callers; tests now
  call both directly.
- **What closed them** (three parallel agents, one module each, each test killing its mutants
  in a gate run or a `--plant`):
  - **Tests:**
    - `test_ledger.py`: a mover across parts by hand (money and count transfers, under $1, to a
      blank group, a negative line); the How text of all 7 moves.
    - `test_spell.py`: the in-force marker with and without a stock tile; snapshots with no
      start date and no declared cadence; a start date splitting spells; a blank start date
      that is missing.
    - `test_eval_scorer.py`: `check_segment_value` off and on spell data and over a pack's
      counted rows; total words; a group named "Overall"; a claim on no rows; the distinct
      count; mixed date formats by month; a skipped kept level that doesn't skip the next.
    - `test_saas.py`: the level repair reading the named KPI, with no task, past an untouched
      breakdown, at a first value, with no account column, for a stated period; the moved
      sentence for an asked period and part.
    - `test_analysis_agent_seam.py`: `run_analysis_agent` passes the session's question to
      the repair.
  - **Dead code deleted, no behaviour change:**
    - `term_rows`' reindex fill;
    - `_spell_events`' groupby `sort` arguments, `.fillna(2)` and the helper-column drop;
    - `_ledger`'s `rename`, sort kind and returned frames' `reindex` / `columns`;
    - `_moved`'s `.where`, which became `* c`, and the `fillna` / `dtype` in `before`;
    - `total`'s `dtype=bool`.
- **Found by the gate (C3 words):** on a named part, the moved sentence said "another segment",
  but a Starter customer who moved to Pro inside SMB is a transfer out of the part. It now says
  "another plan or segment" (`test_what_moved_between_segments_is_said_for_the_period_and_part_asked`,
  plant-proven). The numbers were already the reference's.
- **Final runs:**
  - domain_pack at 8245f8b: 486 mutants, 13 survivors. The `_spell_events` `dropna` mutant was
    then killed (plant-proven); the rest are named below.
  - scorer: 175 mutants, 11 survivors, all named.
  - `_spell_events` and `handle_kpi` at 741430a, after their last change: 136 mutants, 11
    survivors. `cut = None` on a named part is caught by `tests/test_protected_cells.py`
    (plant-proven); the rest are named. **0 unexplained survivors on changed lines.**
- **Equivalents (31, with reasons):**
  - **`_spell` / `stock_rows` `pack` → `None` (2):** with no `t`, `term_rows` never reads
    `pack`, and `used_terms` never reads its `pack`.
  - **`_ledger` group filler `""` → `None` / NaN / `"XXXX"` (3):** with no `by`, nothing groups by
    it.
  - **`dropna=None` (`_ledger`, `_spell_events`) (2):** pandas reads it as `False`.
  - **`_ledger_term` `float("NAN")` (1):** the same NaN (as in 2a).
  - **`_moved` `fillna(None)` (2):** it touches only lines where the rise (or fall) is 0. There
    every kind is NaN, and `total()` skips NaN as it skips 0.
  - **`describe_kpi` `.get` default (1):** read only for a moves term, whose `move` the pack
    validator makes a key.
  - **Scorer `dtype=float` dropped or `None` (9):** every value is read through `float()`, or the
    Series is only checked for membership.
  - **`false_findings` `monthly or True` (1):** with no monthly column, `assign(**{})` changes
    nothing.
  - **`false_findings` distinct count for every aggregation (1):** it is read only when
    `agg == "count"`.
  - **`handle_kpi` `spelled or True` and `else "XXXX"` (2):** unspelled, `spell_terms` is
    empty, so the move check is False either way.
  - **`handle_kpi` `cut` → `None` or omitted in the transfer `measure` (2):** `cut` feeds only
    `protected_cut`, and SaaS, the one pack with moved words, has no protected roles.
  - **`handle_kpi` `len(moved) == 2 or …` (1):** SaaS declares both transfer KPIs, which
    measure where the answer's own KPI already measured.
  - **`handle_kpi` `.get(lead, …)` default (3):** every move of a ledger has its lines'
    groups, so the lead is always there.
  - **`handle_kpi` `part = ""` (1):** falsy, like `None`.

### Suite and CI
- **Full suite at 741430a:** 3,067 passed, 128 skipped, 1 xfailed (PR7-03).
- **Golden:** unchanged.
- **`snapshot text main`:** 0 changes.
- **`domain_diff.py main`:** CHANGES only, the list above, plus the two new transfer KPIs on 5
  SaaS datasets.
- **CI:** see PR #9.

## 5.7b-2c: words, scale and the pre-push gate (2026-11-13)

Plan: `~/.claude/plans/pasted-content-id-105b-start-phase-precious-perlis.md`. It covers ROADMAP
steps 5, 6, 8 and 9, plus `{window}` in pack prose (PR7-03). Every plan-mode step is checked
off by name, and Step 0's list of 126 ledger rows is in the plan. The red-team found 5
blockers, 9 majors and 9 minors, all folded in. Spend: $0.

### The user's decisions (2026-11-13)
1. **A glossary phrase's unit is its KPI's own `Kpi.unit`.** No second copy is declared in
   YAML. This deviates from ROADMAP step 5's wording ("each entry declares"), because a
   declared copy would be the second definition the project forbids.
2. PR #11 (the ledger ratchet) was merged first.

### Measured first (main `1d04153`, a throwaway worktree)
| What | Before |
|---|---|
| xfail-strict known failures | 1: PR7-03 |
| golden | 53 passed |
| retro ledger | 5 passed, UNENFORCED 88 |

### What was built
- **The pre-push gate** (`scripts/mutation.py`, `docs/mutation/gate.json`).
  - `--check REF` runs in under a second, with no mutmut. It refuses when:
    - a changed spell / ledger / pack-column / scorer function (`spell.py`, `ledger.py`,
      `columns.py`, `kpi.py`, `rows.py`, `evals/scoring.py`) has no hash recorded in
      `gate.json`; "changed" means its AST, with the docstring out, differs from the
      merge-base's;
    - a key names a function that no longer exists.
  - `--record` writes a function's hash only after a clean gate. A clean gate means every mutant
    left on its changed lines is a named equivalent (old line, new line, count, reason); a
    no-tests or timeout mutant is never one.
  - The hook and CI's zero-suites job run `--check` (CI checks out with `fetch-depth: 0`).
  - Known limits, written in the script:
    - a module constant or a pack's YAML changes behaviour without changing a hash;
    - mutmut mutates no decorated function (a `@property`, a pydantic validator), so the run
      and the gate leave those out and list them. Their tests and plants are their check.
- **C6, units** (`domain_pack/story.py`, `agents/intake_agent.py`).
  - One unit-word rule, inside `glossary_hits`, so every caller gets it:
    - a percent word inside or right after the words ("revenue churn **rate**");
    - a count word right before ("**how many** returning customers");
    - "run rate" is money.
  - A KPI named in a clashing unit is dropped, and so is the model's pick of it. Intake then
    asks which KPI was meant, listing the KPIs of the asked unit that share a word, each with
    its unit.
  - `returning customers` moves to `reactivated_customers`. Every phrase in every pack is
    checked against its KPI's unit.
- **C7, scale** (`domain_pack/columns.py`):
  - `Role.scales` declares a header's scale. Only Stripe's `percent_off` and
    `coupon_percent_off` are declared percent, because `pct → percent` would rescale the
    finished SaaS dataset's 0.1.
  - One reader, `rate_values`, serves the pack column's net_of and a KPI term's net_of. The
    term's copy, which assumed 0–1, is deleted, and so is retail's `discount_rate ≤ 1` range.
  - A `kind: rate` role is read as fractions. Only a `kind: number` role's undeclared header
    is guessed, and then only when unambiguous: decimals over 1 (12.50: a percent or an
    amount?) and a column of only 1s are left blank and said.
- **C9, words** (`domain_pack/pack.py`, `story.py`):
  - `SpellWords` holds the ledger's sentences: the bridge, monthly churn, the unsplit rest and
    why, the count beside the cohort rate, and each move's How. It also gains `singular` and
    `ends`. A pack with moves must give them all. `plural[:-1]` and `end + "s"` are gone.
  - The guard is an output test. A pack whose every word is a token runs on data with neutral
    column names through the spell story, the facts, each KPI's How, its window words and
    `unavailable_words`. The output must hold no word from a list built from every pack:
    - the words of its labels and glossary that no other pack uses;
    - the short words of its spell that no other spell uses.
- **C2, `{window}`** (`spell.window_span`, `Pack.say` / `Pack.define`, `CleaningAudit.window_span`).
  - Pack prose that names this data's span says `the {window}` (flows), `the {moves}`, `paying
    {cohort} earlier` or `the{window_adj} window`. These read "12 months" on a full year and
    "6 months" on short data. With no window known they give no number ("the months", "a
    year").
  - Where the span comes from:
    - the cleaning audit stores it;
    - intake and analysis read it from the frame when there is no audit;
    - targeted reads its own frame.
  - A test refuses any written 12-month span in every pack's caveats, texts, definitions,
    alternatives and role caveats, except a rule's own span. The allowlist (8 entries, each with
    its reason) covers YoY, the definition of annual churn, `months_lt_13` and an annual invoice.
  - `Window.cohort_months` is the one cohort span (`window_words` computed it inline).
  - `KPI.dated_by`: billings and recurring share name their counted rows' invoice dates.

### Flipped (the xfail marker removed in the fix's commit)
| Class | Test | Commit |
|---|---|---|
| C2 | `test_pack_prose_names_the_window_through_its_placeholder` (PR7-03), widened to every written 12-month span | `33d57e6`, `c3a7926` |

No xfail is left.

### Found on the way (ledger: recurrences raise existing rows)
- **D3-10 (B), a harness off the production path, recurrence 3:**
  - the intake harness's session lacked the frame production's intake has (replay caught it);
  - the golden rendered every analysis context with no frame and no audit, a context no paid
    run sends (the full suite caught it).
  - One `evals.run.analysis_session` now serves the paid run and the golden.
- **PR7-04 (B), a vacuous pass, recurrence 1:** the C9 guard built its word list after
  patching the pack, so every plant passed. The plants found it.
- **Review, a real regression caught before merge:** a retail `discount_rate` of only 1s
  (100% off) was blanked by the "only 1s" guess and read as no discount. A rate role is now
  fractions (pr-test-analyzer).

### CHANGES (the golden re-pinned at 5.7b-2's end; CHANGES emptied)
| Where | Change | Class | Truth |
|---|---|---|---|
| saas_invoices, the billings and recurring-share tiles (11 dashboard paths) | name their invoice dates ("26 Sep 2025 to 30 Sep 2026") | C2 (PR1-02) | the counted rows' invoice dates; no number moved |
| 11 spell datasets, the cleaning audit | stores the span (`window_span`) | C2 | "12 months" on these full years; no text moved |
| 43 analysis contexts (golden) | the golden renders the paid run's session (frame, audit) | D3-10 | the paid inputs equal main's on 103 of 104 cases |
| saas-9 analysis context | NRR's definition says "paying a year earlier", was "12 months earlier" | C2 | data with no spell window: no window to name |
| ins-49 context, saas_subscriptions audit | 5.7b-2a's today-only sentence, now pinned | C5 | 2a's entry |

### The readers of each changed rule (CODING_STANDARDS: a widened rule lists its readers)
- **The unit-word rule (`glossary_hits`):** read by `domain_notes`, the unsupported check and
  `_pack_params` (intake), insight's named concepts, targeted `_also_named`, and analysis'
  `named_kpis`. Probes: `test_answer_paths`, the replay of stored intake reports (e2e-81,
  intake-hr-05, intake-saas-09: unchanged), the golden and the oracle.
- **`rate_values` / `scale_of`:** read by SaaS MRR (the pack column), retail net sales (a term)
  and detection's `_fits`. Probes: D, the reference, and the golden (no number moved).
- **The span placeholders:** read by `pack_caveats` (the insight report, the alert, the
  scorer), `say` (intake and targeted alternatives), `define` (domain notes, the analysis
  brief, targeted) and `window_words` (every tile and answer; the `dated_by` branch). Probes:
  the short-data tests through intake (before and after cleaning), targeted and the brief; and
  the full-data text, identical on every paid input but saas-9.

### Deviations from the plan
- **Four placeholders, not two.** `{moves}` and `{window_adj}` were added because one sentence
  can name moves and flows, and HR writes "the 12-month window". The SaaS alternatives name
  movements and retention with one `{moves}`: on the default window both start at the same
  date (tested equal on short data).
- **`_fits` accepts any number under a declared header.** A bound there would unbind the role
  silently. `rate_values` says the rows off the scale instead.
- **The undeclared guess keeps its majority rule** ("most values over 1 is percent").
  `test_one_odd_discount_never_rescales_the_column` pins it. Only ambiguous decimals over 1 and
  a column of only 1s are refused. Audit pass 4's fractional 12.5% now needs a declared header,
  which Stripe's have.
- **`rate_values(v, scale, col)` plus `scale_of`**, not `(df, roles, pack, role)`: the reader
  takes the values the caller already parsed.
- **Intake's span on a first question comes from the frame ingestion loaded,** as the plan
  allowed. The eval harness was off that path (D3-10).
- **A KPI term's rate notes are dropped** (`ponytail:` in `kpi._term`): a term's role is a rate
  role within 0–1, so nothing is left blank today.
- **D4-15 stays unenforced** (red-team 19: a check means filling `unit_word` on 16 count KPIs
  across 6 domains).
- **A KPI term's unreadable rate is no discount, unsaid** (found closing kpi mutants; now pinned
  by `test_a_discount_rate_reduces_net_sales_and_asp_uses_net_units`). It differs from
  `pack_columns`, which says it. It is unreachable in the pipeline, where cleaning has already
  coerced the column.

### The mutation gate on changed lines
| Run | Mutants on changed lines | Survivors |
|---|---|---|
| 1 (`01761c3`) | 643 | 103 (story 34; columns / kpi / detect 30; spell and the agents 39) |
| 2 (`f589b43`, after closing) | 630 | 7, each a named equivalent |

- The 103 were closed in parallel, one worktree agent per module group, and cherry-picked:
  - 89 by a test, each plant-proven with `scripts/mutation.py --plant` ("caught");
  - 7 by deleting dead code:
    - `_fits`' unused `header=""` default;
    - `pack_columns`' fallbacks for a net_of role that isn't bound;
    - `_spans`' `if contract`;
    - targeted's `chart_configs=[]` and `table_data=[]` (already the defaults);
  - 7 as named equivalents (below).
- Two closers had to move: a test that called `asyncio.run` inside an async test passed only
  on a warm cache, and the gate leaves `test_one_definition.py` out. The `pack_brief` check
  moved to `test_saas.py` and was plant-proven again.
- **Recorded** in `docs/mutation/gate.json`: `window_words`, `window_span`, `scale_of`,
  `rate_values`, `pack_columns` and `_term`. `--check` passes on HEAD and refuses the commit
  before the record (3 functions named, plus the command to run).
- **Named equivalents:**

| Where | Mutant | Why no output can change |
|---|---|---|
| `columns.rate_values` (gate.json) | `given > 1` → `>= 1` | a 1 changes neither the decimals check nor the percent test; a column of only 1s returned earlier |
| `columns.scale_of` ×2 (gate.json) | `"fraction"` → `"XXfractionXX"` / `"FRACTION"` | its only reader treats every scale but "percent" as a fraction |
| `kpi._term` (gate.json) | the column name → `None` | it reaches only the notes, which `_term` drops |
| `story._ledger_story` | `rest > 0` → `>= 0` | the line runs only when `abs(rest) >= 0.005` |
| `story.unit_said` | "run rate" → `"XXXX"` (not `""`) | `\b` anchors keep any neighbouring unit word's boundary the same |
| `targeted._also_named` | currency passed for every kind | `_format_value` ignores currency for percent and count; no additive KPI is a `number` |

### End checks (HEAD)
- Full suite (`scripts/suite.py`, `f120338`): 3121 passed, 128 skipped, 0 failed. The later
  commits change only tests (each file was rerun on its own) and `gate.json`.
- `domain_diff.py main --show`: only `saas_invoices` changed (11 paths, billings' invoice
  dates). `snapshot text main`: 49 changes, the analysis contexts the golden now renders with a
  paid run's frame and audit (D3-10). Each is in the CHANGES table above.
- Retro ledger: UNENFORCED 88 → 83. C1–C7 and C9 are each closed in its Classes table.
