# Coding standards (read by the reviewer, not at implementation)

Judgement calls a check can't make. Mechanical rules live in tests and linters
(`tests/test_one_definition.py`, `frontend/eslint.config.mjs`, `tests/test_frontend_tokens.py`):
never restate them here.

## Test and eval adapters take the production path

An eval harness, an oracle adapter or a test fixture builds its inputs through the production
functions: `prepare_frame` (PHI mode), `time_axis`, `runout_months`, `rows_for`, `row_kinds`,
`with_derived`. A hand-written stand-in for a production step names that step in a comment and
gets a test that compares the two.

**Why:** Domain 3's oracle skipped PHI mode and the insight harness skipped PHI mode, the run-out
and the pack's time axis; each hid a real bug until a paid run.

## Guards match the behaviour, not one spelling

A guard against a recurring bug class checks the behaviour (any numeric reduction without a row
rule; every function that writes a ranking sentence) with a reasoned allowlist, and proves itself
on a planted violation.

**Why:** the rows_for guard matched only `aggregate(` and the highest/lowest guard only the text
`} highest (`; the class came back through `.quantile()`, `np.histogram()` and two unguarded
sentence builders.

## A user interaction is tested by doing it

A feature a user drives (a filter, a chart-type switch, a follow-up) gets an e2e step that does
the interaction and asserts the visible result changed. A first render proves nothing about it.

**Why:** dashboard filters changed nothing for three domains; every check looked at first renders.
