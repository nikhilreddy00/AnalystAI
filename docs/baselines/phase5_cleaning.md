# Phase 5, PR 3 review: Cleaning agent on the agent contract

Deterministic (no LLM), card `cleaning rules-v1`, 2026-10-28, branch `phase-5-cleaning`.
- **Final cleaning report: `evals/reports/cleaning-20261028T225227Z.*`**: 25 cases (5 holdout),
  k=3, $0. Paired against the baseline `cleaning-20261028T223727Z-rescored` (the old LLM agent, k=1,
  $0.07; rescored once for a detail-text fix, verdicts unchanged).
- **E2E "before":** `e2e-20261028T172325Z`, the last Visualization gate.
- **E2E gate:** **`e2e-20261028T224738Z`** (k=1). The commits after it change only audit reason
  text (shown in the Evidence drawer, never sent to a prompt), the gate-answer parser (the E2E answer
  still continues) and placeholder tokens that `read_csv` already turns into NaN. So there was no
  re-gate (see Deviations).

## Numbers

| metric (ROADMAP Cleaning row) | target | baseline (old LLM agent) | **final (k=3, 75 runs)** |
|---|---|---|---|
| planted-issue recall | ≥ 95% | 56.5% | **100%** (17 issue kinds, each 100%) |
| over-cleaning (traps changed) | ≤ 5% | 32.5% | **0%** |
| **hard:** row-count changes logged | 100% | 10 violations | **0 violations** |
| **hard:** every changed column has a decision | 100% | 21 violations | **0** |
| **hard:** ID / date / target never filled | 100% | 4 violations | **0** |
| holdout (5 multi-issue cases) | | 0% | **100%** |
| pass rate, all cases | | 0% | **100%** (25 fixed, 0 broke, McNemar p < 0.001) |
| **E2E** pass rate (must stay 100%) | 100% | 100% | **100%** (20/20, 0 broke, 0 hard) |
| E2E cost per question / p95 | | $0.0462 / 147s | $0.0455 / 145s |
| cleaning cost / latency per table | | $0.0029 / p95 6.4s | **$0 / p95 0.2s** |

Baseline failures, by cause:
- every low-cardinality label was lowercased (`North` → `north`, which then showed in charts);
- leading-zero store and account codes became numbers (`0681` → `681.0`);
- the `S`-prefixed codes became NaN, and then the model chose `drop_rows` for them, deleting clean rows;
- the question's metric was mean-imputed;
- `12.5%` was read as 12.5;
- placeholders (`-`, `?`) were left as labels;
- duplicates and dropped rows were a count, never a decision.

In the real E2E data, the old path mode-filled `notes`. Its only non-empty value is the planted
injection text, so every row got it.

## Built
- **`CLEANING_CARD`** with measured limits.
  - The agent is deterministic: no prompt file, no `llm` parameter, a $0 eval.
  - The old model call only repeated a fixed decision table, and changes to the user's data have to
    be reproducible.
- **`build_context` → `CleanContext`** (frame, IDs, dates, the plan's targets, the gate answer), and
  **`clean(ctx)`** with nine allowed operations, in order. Each change is one `CleaningDecision`
  (new field `rows_removed`):
  1. `skip`: the existing triage;
  2. `sentinel_null`: only unambiguous placeholders (`-`, `?`, `N/A`, `null` …); `NA` and `None`
     are labels;
  3. `type_coerce`:
     - numbers and dates parse from text, and the values that don't parse are counted;
     - `12.5%` → 0.125;
     - code-like text with leading zeros stays text;
  4. `normalize`: label variants merge by key collision (trim, collapse spaces, casefold) to the most
     common spelling, which is OpenRefine's fingerprint method minus the punctuation and token steps,
     so `A-1` ≠ `A1`;
  5. `drop_duplicates`: only when an ID column defines a record. Without one, identical rows are kept
     and flagged, since survey answers and equal purchases repeat for real;
  6. `drop_rows`: only fully blank rows;
  7. `fill_unknown` / `keep`:
     - missing labels → `Unknown` (an existing `UNKNOWN` spelling is reused);
     - missing numbers, IDs, dates, targets and >40%-empty columns stay missing;
     - **nothing is imputed**;
  8. `drop_column`: only when the user chose it at the gate;
  9. `flag`: the existing anomaly detector; outliers are never removed.
- **Why no imputation (D2):** AnalystAI's outputs are descriptive.
  - Mean, median and mode fills bias them: they understate variance and weaken correlations (Little &
    Rubin; van Buuren). A filled sum fabricates revenue, and a mode fill inflates the largest segment.
  - Available-case analysis with the missingness reported is the standard. Analysis already reports
    the non-null n per finding.
- **Guardrails** (`guardrails.py`, pure): `unreconciled_rows`, `unlogged_changes` and
  `protected_filled` re-check the result against the input. A violation is a bug in the rules, so
  it raises `GuardrailViolation` with a label-only trace.
- **Evidence:** one `kind="cleaning"` item, built from the audit:
  - rows before → after, then each decision as an assumption;
  - confidence is medium when a column is >40% missing or had unreadable values;
  - counts and masked labels only.
  - It's inserted in `analysis_node` before the findings. The Evidence drawer labels it "Cleaning".
- **Analysis evidence** now says `units: 10% missing, left out of this result`, and labels grouped as
  `Unknown` lower confidence the way imputation did.
- **High-null gate, fixed:** "Drop those columns" and "Cancel" were ignored, and the re-run cleaned
  exactly as before.
  - Drop → `drop_column` decisions.
  - Cancel closes the modal without a request; a typed "cancel" / "C" stops with a message.
  - The answer must start with the option, so "don't drop anything" doesn't drop.
  - The recommended option now reads "Continue — missing values stay missing".
- **Security:** the insight prompt took `audit.summary` and the anomaly lines (which carry column
  names) undelimited; both are now delimited. Normalize reasons quote labels through `mask_pii`, and
  never for a PII-named column. Tests plant an injection-named column and email values.
- **Eval suite:** `evals/cleangen.py` generates two base schemas (orders, banking) with traps in
  every table:
  - leading-zero codes;
  - 5 records that differ only by ID;
  - the look-alike products `A-1`/`A1`.
  - On top of those, 17 injectable issue kinds.
  - The scorer (`score_cleaning`) is independent of the agent: it diffs the output frame against the
    generator's expected frame. The hard rules come from that diff, not from the agent's own
    accounting.

## Cost
- Cleaning baseline: **$0.07** (estimate $0.15). Every later cleaning run: $0.
- E2E gate: **$0.91** (estimate $0.92).
- **PR total: $0.98** (cap $5).

## Deviations from the plan
- **No E2E re-gate on the final commit.** After the gate there were three changes, none of which
  reaches a prompt or changes the E2E data:
  - reason wording (`>99.9%` instead of `100%`, "number" instead of "value");
  - the gate parser (E2E sends "Continue with imputation", which still continues; a test pins it);
  - fewer placeholder tokens (`read_csv` already maps `NA`/`None` to NaN, and the E2E audits had no
    `sentinel_null`).
- **Dedup needs a row key.** This was not in the plan; a fault test found it, when three identical
  outlier rows in a one-column frame were "deduplicated". The card states it.
- **Scorer change (baseline only):** the detail text said "got None" for a removed row. That was
  fixed and rescored for $0; no verdict changed.
- **Same-author risk:** the generator and the agent were written in the same PR. The dev cases passed
  on the first refactor run, which proves less than it looks. Mitigations:
  - the 5 holdouts ran only in the final k=3;
  - a 30-table random-combination property test;
  - unit traps the generator doesn't plant (`NA` as a label, rows that repeat without an ID, an
    existing `UNKNOWN` spelling).
  - Phase 5.5's domain datasets are the real test.

## Known limits (carried forward)
- **Targeted questions skip cleaning** (`routing.py`): they read the uploaded data, duplicates
  included. Cleaning is now free and fast (p95 0.2s), so it can run there, but that changes the
  targeted E2E path → Insight or Ingestion PR.
- **`read_csv` drops leading zeros on all-digit codes** and maps `NA`/`None` to NaN before cleaning
  sees them. That's the Ingestion PR.
- No fuzzy or near-duplicate matching; ambiguous dates are read month-first.
- **Carry-over to Insight:** the e2e-01 executive text says "November 2025" for a 12-month table, in
  this gate and in the previous one.

## Post-PR audit (2026-10-28)
- **Fixed, each with a test:**
  - a free-text gate answer containing "drop" dropped the columns;
  - `NA` / `None` / `n.a` were treated as placeholders;
  - the high-null reason said "100% missing" for a column with one value;
  - code-like detection ran only through `build_context` (it now runs inside `clean()` for every
    caller);
  - the generator overwrote planted nulls when a formatting issue followed.
- **Checked, no change needed:**
  - no `get_client()` in the cleaning agent;
  - the new Evidence kind is in `frontend/src/types/index.ts`, and `CleaningAudit` is not mirrored
    in TS;
  - traces are labels only;
  - the resume path reloads the cleaned cache, so the cleaning evidence matches the analysed frame;
  - the critic never reads evidence;
  - no other suite runs cleaning (pinned by `test_other_suites_never_run_cleaning`).
- **Still open:** none for Cleaning. The carry-overs are listed above.
