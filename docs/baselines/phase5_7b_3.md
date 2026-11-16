# Phase 5.7b-3 — prose, charts and the audit itself (2026-11-15)

Plan: `~/.claude/plans/pasted-content-id-0a38-start-phase-expressive-barto.md`. It checks off
every plan-mode step of the ROADMAP 5.7 contract by name:
- brainstorming, with the scope fixed by ROADMAP 5.7b-3 steps 1–4;
- the `domain-modeling` term ledger (typed claim, causal claim, benchmark verdict, free text,
  bridge), GLOSSARY entries and ADR-0009;
- `codebase-design` (the deletion test dropped a proposed `overview_bridge` field: the bridge is
  derived from `ledger_kpi` / `stock_kpi`);
- research (not needed: in-repo precedent);
- grilling, answered as a senior engineer;
- one red-team subagent: 4 blockers, 18 majors and 8 minors, all folded in;
- sizing (one PR; the split point after step 2 was not needed).

The audit procedure this session wrote into the 5.7 contract ran on this branch; its stop rule
is properties, the mutation gate and the ledger checks green.

**User decisions:**
- the property hook is a frozen exemption with a release deadline (2026-11-15);
- spend may go past $4, up to $5 (2026-11-16);
- the judge stays on Sonnet (2026-11-16);
- the quality cases that fail on the critic or on judge false positives are recorded and shipped,
  with no more prompt tuning (2026-11-16).

**Spend: ≈ $3.85 of the $5 cap.**
- judge tuning and held-out runs, insight and quality k=1 and k=3, the replay runs and the
  smoke: $2.87;
- the e2e-01 fixture: a failed attempt $0.07 (no eval key; the guard is below) and the re-record
  $0.11;
- the E2E gate: $0.81.

## Measured first (B0: main `694cf25`, rescored, never rerun)
| What | B0 | After (this branch) |
|---|---|---|
| Insight pass rate | 1.0 (k=3, 40 cases, `insight-20261029T162956Z-rescored`) | k=1, 50 cases: 0.96 with judge v3 (`insight-20261116T042427Z`, a replay). The 2 failures, ins-13 and ins-49, pass at k=3 (2/3 each) |
| Insight hard rules | 1.0 | 1.0 (0 violations) |
| Quality `--checks-only` ($0, CI gate 0.85) | 0.86 | 0.86 (judge v3 replies replayed); natural recall 0.924 |
| Quality pass rate, live critic | 0.90 (k=3, `quality-20261029T224558Z-rescored`) | 0.82 at k=1 (`quality-20261116T041831Z-rescored`); see "Quality, case by case" |
| Quality hard rules | 1.0 | 1.0 after the recorded echo scorer change (0.98 before it, q-31) |
| Prose judge, held-out (100 sentences, frozen) | (regexes; never measured) | v3 on Sonnet 4.6: TPR 0.92 [0.81, 0.97], TNR 0.94 [0.84, 0.98]. Causal TPR 0.93, near-causal TNR 0.93; benchmark TPR 0.90, near-benchmark TNR 0.94 |
| E2E gate (k=1) | (5.7 domains: pass) | 6/6: e2e-15, 35, 53, 71, 78, 82 (e2e-82 rescored after the date fix below) |
| UNENFORCED (ledger ratchet) | 83 | 78 |
| Full suite | — | 3199 passed, 1 xfailed (`test_every_pack_has_a_generator`, xfail-strict, PR14-01) |

### Quality, case by case (k=3 on each failure)
- **q-09, q-15:** 3/3 pass.
- **q-07 (1/3), q-12 (0/3), q-38 (0/3):** these fail on main's code today too. The critic
  request is byte-identical, so the critic's behaviour has moved since 2026-11-04. Not this
  branch.
- **q-14 (0/3):** failed in every B0 run (a semantic miss by the critic). This is the baseline.
- **q-31 (0/3), q-48 (0/3):** judge false positives on sound sentences: "the strongest of the
  three segments" and "Attrition has improved materially". v3's own guide calls both neither, and
  they are inside the measured TNR of 0.94. Without the judge, q-48's causal defect is missed.
- **q-42 (1/3):** the critic. It passes 3/3 on main, so it's recorded under the user's decision.

## What was built
- **Step 1, typed claims (C8, ADR-0009):**
  - Insight prompt v5 gives causal, benchmark and comparison claims as data.
  - `agents/claims.py` checks each one against the evidence it cites and writes its sentence:
    - a cause is always an association, from a correlation, breakdown or test that names both
      sides;
    - a benchmark verdict is never said, only its measured metric;
    - a comparison's direction comes from the values, and its measure from the breakdown;
    - evidence short of high confidence is said ("may not hold").
  - Deleted: the causal regexes, every pack's `causal_triggers` and SaaS's benchmark `never_say`.
    A 7-phrase, domain-neutral `BENCHMARK_PHRASES` list stays as the $0 floor.
  - Free text goes to one binary judge (`agents/prose_judge.py`, prompt v3) in insight, the
    first quality run and drill-down (with or without a pack).
    - The judge was calibrated on two frozen tuning sets, then measured once on the held-out set.
    - The held-out log pins the prompt, set and model by sha.
    - A failed judge is said, never passed as checked.
- **Step 2, ledger charts:**
  - The SaaS overview draws the MRR bridge (moves-only bars that add up to the change, a fixed
    step set, recomputed under filters).
  - It draws NRR at month-ends through `handle_kpi`.
  - Both go through `finalize_charts` with a `ChartRationale`.
  - `frontend/e2e/ledger_charts.spec.ts` at 1440 px, plus the keyless UI check (screenshots in
    the PR).
- **Step 3, the audit procedure:**
  - Written into ROADMAP 5.7.
  - `tests/test_plan_steps.py` checks every session note from 2026-11-15 on names each plan step
    and the stop rule.
- **Step 4, the property hook:**
  - `tests/test_properties.py::test_every_pack_registers_its_shapes_or_is_a_known_gap`, with
    plants both ways.
  - `NO_GENERATOR_YET` holds 4 packs, and its count ratchet only goes down.
  - It must be empty before v0.1 (ROADMAP Phase 6).
- **Tooling:**
  - `evals.run --replay` re-measures a judge prompt on stored insight and quality runs, paying
    for the judge alone.
  - The $0 quality gate replays the paid run's judge replies; a case with none fails loudly.
  - `scripts/record_run_fixture.py` sets the eval key and never writes a failed run.

## Deviations
- **The judge runs on Sonnet 4.6, not Haiku** (ADR-0009 amended).
  - v2 on Haiku scored held-out TPR 0.98 / TNR 0.90 on curated sentences.
  - On real reports v2 flagged 16% of sentences, about 13% of them correctly, and quality fell
    to 0.36.
  - A second frozen tuning set of real report sentences went in before any v3 run.
  - v3 on Haiku stayed at TNR ~0.85.
  - Cost: about $0.01 a call instead of $0.002.
- **Recorded scorer changes:**
  - q-41 and q-50 gained `natural` labels, because benchmark verdicts became a defect class after
    those cases were labelled.
  - The quality echo rule exempts a group named like an instruction, as it already did a column
    (PR14-02). The quality reports were rescored.
- **k levels:** k=1 on all 50, then k=3 on the failing cases, not k=3 on every touched case. The
  insight faithfulness judge was not rerun.
- **Drill-down now judges pack-less data too** (audit pass 1).
- **The bar-click guard was reverted, because it couldn't be proven.** A chart click sets no
  filter at all (Recharts 3 gives `onClick` no `activeLabel`), and main has the same bug.
  - It is recorded as D3-33, recurrence 1.
  - An expected-failure spec pins it.

## Findings and their checks (each plant-proven with `scripts/mutation.py --plant`)
| Finding | Ledger | Check |
|---|---|---|
| Audit pass 1: a typed comparison showed one metric's values under another's label; a cause passed on a one-letter substring or a trend; the phrase floor dropped when the judge failed; drill-down on pack-less data judged nothing; an unknown judge label passed | D1-09 (recurrence 3) | `tests/test_typed_claims.py::test_a_metric_is_one_the_analysis_measured_and_a_cause_is_two_names_of_an_association` and the D1-09 enforced-by list |
| The $0 replay could pass a case unjudged | PR7-04 (3) | `tests/test_quality_eval.py::test_the_judge_replay_fails_loudly_never_passes_unjudged` |
| Judge v2 over-flagged real reports; a typed sentence dropped its evidence's low confidence (ins-15, ins-17) | D1-09 | `tests/test_prose_judge.py::test_the_held_out_run_matches_the_current_prompt_set_and_model`; `tests/test_typed_claims.py::test_a_comparison_on_evidence_short_of_high_confidence_says_the_gap_may_not_hold` |
| The fixture recorder crashed without an eval key and overwrote the fixture | D3-10 (4) | `tests/test_record_run_fixture.py::test_the_recorder_sets_the_eval_key_and_never_writes_a_failed_run` |
| q-31: the critic quoting a contaminated group name failed the echo rule | PR14-02 | `tests/test_quality_eval.py::test_quoting_a_value_named_like_an_instruction_is_no_echo_but_following_it_is` |
| e2e-82: "31 Oct 2026 … in the data" was read as a one-month scope claim. Replaying the stored runs, 5 HR and SaaS overview runs now keep a sound "covers … at 31 Oct 2026" sentence the guard had removed (`tests/test_replay.py` CHANGED); none fails a check it passed | PR14-03 | `tests/test_guardrails.py::test_dates_outside_the_range_and_one_month_scope` |

Known limits, still prose (D1-38, D1-39): the audit's judgement of "a fix judged on its own example"
and "a check fires late" decides nothing in code.

## Mutation gate (`--lines main`: 0 unexplained survivors on changed lines)
- Run 1 had 215 survivors. They were closed by tests from 4 worktree agents (claims and judge,
  quality, drill-down and story, insight), by deleting a redundant guard, and by named
  equivalents.
- Final runs: every changed function scores 0.95–1.0.
- The named equivalents (`docs/mutation/gate.json`) are a joiner, `>=` vs `>` on non-zero signs,
  the `frozenset()` vs `None` default, and similar.
- `_named`'s last survivor (the claim–id joiner) was killed by a test.
- `guardrails.py` joined `source_paths`, as plan item G9 intended; it had been copied only. The
  date guard `ungrounded_dates` scores 1.0.
- The echo scorer `score_quality` scored 0.69 at first. Its longest-name-first order and the
  column exemption are now pinned, and the dead defaults were dropped, so it scores 1.0.
- `--record`, then `--check main` passes.
