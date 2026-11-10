# Phase 5.7b-1 — Structural fixes, session 1: foundations (2026-11-09, $0)

Plan: `~/.claude/plans/pasted-content-id-6ec8-start-phase-foamy-frost.md`. It checks off every
plan-mode step by name:
- brainstorming;
- the `domain-modeling` term ledger;
- `codebase-design`;
- tool research (`docs/research/2026-11-09-hypothesis-importlinter-mutmut.md`);
- grilling, answered as a senior engineer;
- one red-team: 4 blockers, 9 majors and 4 minors, all folded in.

Spend: $0. There is no model call anywhere in this session.

## Measured first (before any product change)

| What | Before (main `cc1af3a`) | After (this branch) |
|---|---|---|
| Suite, `pytest -n 8` | 2,841 passed, 128 skipped (4 min 37 s) | 2,974 passed, 128 skipped, 12 xfailed (10 min 13 s with the gates running at once) |
| Property failures | none run | 12 known failures, each pinned xfail-strict with its class (below) |
| Audit majors per class | none counted | `docs/retro-ledger.md` → "Majors per class" (234 rows) |
| Mutation score per spell / ledger / pack-column function | pilot only (`pack_columns` 71%) | table below, measured on the pre-split tree `ab4270a` |

## Step 1: retro ledger

`docs/retro-ledger.md` has one row per finding from D1–D6 and PRs #1–#3, plus this session's
PR5 rows (234 in all). Each row records:
- its class (C0–C9 for bugs, A–G for process);
- the fix;
- `enforced-by`, a named check or `unenforced`;
- recurrences.

`tests/test_retro_ledger.py` checks the ledger:
- every named check exists (a renamed guard fails it);
- the classes are valid;
- the summary equals the rows.

An "Open" fix means the row is pinned by an xfail-strict test, not fixed.

GLOSSARY:
- added **Window**, which names the first known date per shape;
- added **Unavailable**;
- sharpened **As-of** (a month label's month-end) and **Opening cohort** (clipped to the first
  known state; a signup cohort is a different thing).

## Step 2: the reference oracle

`evals/reference_kpis.py` is 400 lines of plain loops over records, written from GLOSSARY and the
pack words.
- An import contract forbids it from importing `domain_pack`, `agents` or `evals.datagen`.
- A test checks that its copied word lists equal the packs' lists.
- **It equals the generator's truth** on all 6 spell datasets (`saas_subscriptions`,
  `saas_mrr_snapshots`, `hr_roster`, `hr_roster_onerow`, `hr_snapshots`, `employees`). Each
  dataset has a required key list, and every excluded truth key carries a reason.
- **It equals the product** (`compute_kpi` through the production reader and cleaning) on
  62 KPI comparisons across the same 6 datasets.

## Step 3: properties (Hypothesis 6.170.15)

`tests/test_properties.py` runs in two tiers:
- **Tier (a), small books on the real packs.** Hypothesis builds and shrinks five shapes: an MRR
  export, a subscription list, an HR roster and its snapshots.
- **Tier (b), every `PACK_DATASETS` dataset** (25 of them) through the production reader, with
  drawn transforms.

Profiles (`tests/conftest.py`):

| Profile | Examples | Used by |
|---|---|---|
| `ci` | 25, derandomized | CI |
| `dev` | the same | local runs |
| `deep` | 500 | the nightly cron, tier (a) |
| `mutation` | 5 | each mutant |

| Property | What it checks | Green on | Pinned known failures (xfail-strict) |
|---|---|---|---|
| P1 bridge | opening + moves = level at the as-of | total; per segment when nobody moves segment | C3 (a customer moves segment); C2 (an asked window) |
| P2 partition | Σ groups = total; an unavailable total has no group numbers | MRR by segment, HR (roster and snapshots) by department, tier (b) over each pack's overview groups | none |
| P3 invariance | row order; CSV / JSON / xlsx; text dates; money ×k | every shape; tier (b) shuffle and scale | none |
| P4 never a confident 0 | a KPI missing a role it needs is unavailable; a blank column the KPI doesn't read changes nothing | every shape | C5: a blank column the KPI reads (mrr, status) gives 0; `canceled_at` with no `ended_at` gives 0% logo churn |
| P5 one window | the default window asked for explicitly gives the same number and words | MRR export, roster | C2: a mid-month as-of reads 12 vs 13 month-ends; the window rebuilt outside `spell.py` (guard) |
| D differential | product = reference, unavailable included | every shape (the MRR export in unbroken runs) | C5: nobody in force reads as unavailable, not 0; C7 ×3: a snapshot's cadence guessed from gaps (a customer, a person absent from the newest snapshot still counts) |

**Runtime (local, 8 workers):**
- tier (a): 41 passed and 11 xfailed in 85 s;
- tier (b): 75 tests in about 40 s.

On CI the 4 shards split it. The figure is recorded from the PR run.

**Each property fails on its own class.** Mutants were planted in a throwaway worktree, one
per class:

| Property | Mutant | Result |
|---|---|---|
| P1 total | expansion counted as `y` | red |
| P1 per segment | the part filter ignored for churn; churn counted at the later row | red |
| P2 MRR | a group dropped; `.max()` for `.sum()` | red |
| P2 HR | a group dropped | red after the fix (it checked nothing before) |
| P3 shuffle | `_in_force_at` without its sort | red |
| P3 text dates | strict `_day` format | red after the fix (the reader had parsed every date) |
| P3 money scale | `mrr > 60` filter | red |
| P4 | a missing role returns 0.0; a KPI reads an unrelated role | red |
| P5 | an asked window keeps lo's month-end | red |
| D | reactivation folded into new | red (the subscription list, and both SaaS datasets in `test_reference.py`) |

Re-run a plant with `scripts/mutation.py --plant FILE OLD NEW --tests tests/test_properties.py
-k EXPR` (exit 0 = caught, 1 = missed, 2 = pytest error). The three kept from the session,
re-run on `1c57881` and all caught:

```text
--plant domain_pack/kpi.py    'return count(rows, rows[by]).reindex(groups, fill_value=0)' \
        'return count(rows, rows[by]).reindex(groups, fill_value=0).iloc[1:]'      -k p2_hr
--plant domain_pack/pack.py   'pd.to_datetime(s, format="mixed", errors="coerce")' \
        'pd.to_datetime(s, format="%Y-%m-%d", errors="coerce")'                    -k dates_written_as_text
--plant domain_pack/ledger.py 'out[b] = total(delta, a if kind == "churn" else b)' \
        'out[b] = total(delta, b)'                -k p1_the_bridge_adds_up_per_segment_when
```

The other rows were planted by a review agent; their exact edits were not kept.

Every pinned xfail fails on its own assertion (`raises=AssertionError`), so fixing its class
flips it.

## Step 4: mutation baseline (mutmut 3.8.1)

`scripts/mutation.py <functions> --tests … [-k …]` runs mutmut on HEAD in a throwaway worktree
and reports a score per function: killed ÷ (killed + survived + no test). A mutant that no test
reaches counts as not killed; timeouts are shown apart. Its grouping of mutmut's `.meta` files
is tested in `tests/test_mutation_script.py`.

Under mutmut, an autouse fixture clears every cache (`_MEMO`, `load_packs`, the built datasets).
The `pack_columns` pilot came out the same with the caches cleared (134 / 189), so for that
function the caches were not hiding mutants.

The run covers the 23 functions on the pre-split tree `ab4270a`:
- 1,635 mutants out of the file's 6,632;
- tests: test_saas, test_ledger, test_spell, test_reference, test_properties tier (a),
  test_pack_columns and test_hr_audit_guards;
- the per-function table, the wall time, and the post-split scores of the three functions
  SpellView changed are filled in below.

| Function (module after the split) | Mutants | Killed | Survived | Score |
|---|---|---|---|---|
| `spell_as_of` (spell) | 19 | 17 | 2 | 89.5% |
| `_spell_as_of` (spell) | 39 | 33 | 6 | 84.6% |
| `spell_from` (spell) | 26 | 22 | 4 | 84.6% |
| `spell_window` (spell) | 60 | 56 | 4 | 93.3% |
| `spell_covered` (spell) | 32 | 23 | 9 | 71.9% |
| `_month_ends` (spell) | 24 | 21 | 3 | 87.5% |
| `window_words` (spell) | 80 | 66 | 14 | 82.5% |
| `_month_labelled` (spell) | 11 | 11 | 0 | 100.0% |
| `first_known` (spell) | 26 | 22 | 4 | 84.6% |
| `cohort_start` (spell) | 6 | 6 | 0 | 100.0% |
| `_in_force_at` (spell) | 49 | 47 | 2 | 95.9% |
| `_ended` (spell) | 19 | 17 | 2 | 89.5% |
| `_ended_undated` (spell) | 28 | 20 | 8 | 71.4% |
| `_started` (spell) | 13 | 13 | 0 | 100.0% |
| `has_spell` (spell) | 13 | 12 | 1 | 92.3% |
| `_spell_events` (spell) | 132 | 90 | 42 | 68.2% |
| `_spell` (kpi) | 294 | 256 | 38 | 87.1% |
| `_ledger` (ledger) | 71 | 61 | 10 | 85.9% |
| `_ledger_term` (ledger) | 312 | 263 | 49 | 84.3% |
| `pack_columns` (columns) | 189 | 158 | 31 | 83.6% |
| `filled_statuses` (columns) | 17 | 6 | 11 | 35.3% |
| `layout_columns` (columns) | 75 | 26 | 49 | 34.7% |
| `with_spell_events` (rows) | 100 | 64 | 36 | 64.0% |
| **all 23** | **1635** | **1310** | **325** | **80.1%** |

Wall time 4 h 56 min (11:50–16:46), about 2 min per mutant on the central functions, which every
selected test reaches. No mutant was untested or timed out. The lowest scores, and so 5.7b-2's
first targets for its 100%-on-changed-lines gate: `layout_columns` and `filled_statuses` (about 35%), `with_spell_events` (64%), `_spell_events` (68%). `pack_columns` scores 83.6% here against the pilot's 71%, because the wider test selection kills more.

**After SpellView** (the new baseline for the three functions it changed; `ded3c70`, same
tests). Their mutants changed with the code, so these replace the pre-split rows for 5.7b-2:

| Function | Pre-split | Post-split | Mutants (killed / survived / timeout) |
|---|---|---|---|
| `_spell` (kpi) | 87.1% | 85.2% | 284 (242 / 42 / 0) |
| `_ledger` (ledger) | 85.9% | 86.1% | 75 (62 / 10 / 3) |
| `_ledger_term` (ledger) | 84.3% | 84.4% | 308 (255 / 47 / 6) |

## Step 5: the split

`domain_pack.py` (2,760 lines) became `domain_pack/`, 9 files and 3,041 lines in total:
- pack (41 names): the models, loading, the shared helpers;
- detect (11);
- columns (8);
- spell (25: the as-of, window, in force, the memo);
- ledger (3);
- kpi (28);
- rows (11);
- story (16).

Each top-level statement moved verbatim, placed by an AST scan, with 0 references pointing up a
layer. `__init__` re-exports every name.

**Byte-identical evidence**, at the move commit and again after SpellView and the review fixes:
- the golden suite: 90, then 97 passed;
- `scripts/domain_diff.py main`: 28 datasets identical;
- `python -m evals.snapshot text main`: 0 changes.

**Contracts** (`lint-imports`, in CI and the pre-push hook):
- the layers `story | rows > kpi > ledger > spell | columns > detect > pack`;
- the reference oracle never imports the product or the generator.

Each contract was shown to break on a planted import. A `def` guard keeps the window, in-force
and ledger definitions in their owner modules (red on a planted copy). An xfail-strict C2 guard
lists the window rebuilt outside `spell.py`.

**SpellView** cuts `_ledger_term` from 13 parameters to 7. A type review came first and its
changes were adopted:
- the window as one `SpellWindow` field, which 5.7b-2's `Window` replaces;
- the term's kinds travel beside the view, not in it;
- a typed error for a window its readers can't share.

A part relabelled away from the whole reads the values it read before the refactor (pinned).

## Deviations from the plan

- **Eight modules instead of the ROADMAP's five.** The cycles force `pack`, `rows` and `story` out
  of the five. Each module passes the deletion test: a 5.7b-2 fix lands in each.
- **The contracts landed with the split.** import-linter needs a package as its root.
- **The mutation baseline was measured during Step 5.** It ran on the pre-split commit
  `ab4270a`, in its own worktree, so it is still the measurement before the move.
- **P5 compares the window's public readers** (`compute_kpi`, `window_words`, default against
  asked). The internal rebuilds (`_ledger_term`, `spell_story`, the targeted year-before) are
  listed by the C2 guard instead, because they have no seam to compare through until 5.7b-2's
  `Window`.
- **D runs on the MRR export only in unbroken runs, until C7 is fixed.** So it can't yet see a
  reactivation error there. The subscription list and both SaaS datasets do catch it.
- **`test_oracle` is left out of the mutation selection** (time). Its spell checks are covered by
  test_saas and test_reference.

## Found and pinned (fixed in 5.7b-2)

All of these are in the ledger as PR5-01 … PR5-09:
- **C7:** the snapshot cadence is guessed from gaps (SaaS and HR).
- **C2:** a mid-month as-of reads 12 vs 13 month-ends; an asked window's bridge; the definition
  of the window's first date.
- **C5:** a blank column a KPI reads gives 0; nobody in force reads as unavailable, not 0.
- **C0:** `kpi_roles` under-reports a spell KPI's roles.
- **G:** CLAUDE.md's 100-character rule is unchecked (E501 is off).
- **Oracle drift:** the oracle's word lists can drift from the packs; now one test reads both.
