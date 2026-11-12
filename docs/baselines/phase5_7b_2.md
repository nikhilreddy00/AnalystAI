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
| `snapshot text main` | see below |
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
