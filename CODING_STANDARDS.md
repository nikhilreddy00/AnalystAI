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

## A widened or narrowed rule lists its other readers

A commit that changes what a shared rule accepts (a regex, a role list, a row filter, a
threshold, a pack field's meaning) names in its message who else reads that rule (other domains,
other answer paths: tiles, by-group, time series, dashboard, quality checks) and probes them at $0
in the same commit (`tests/test_answer_paths.py`, the golden, the oracle).

**Why:** in Domain 4 most late majors came from fixes that widened a rule for insurance and
changed an answer path or domain no test was reading (a partial quarter headlined, a small cell's
count shown).

## Review scope includes the eval harness

`evals/` (the runner, scorers, the cost estimator, the generators) is product code for review: a
scorer that passes a wrong answer or an estimator that underprices a run costs real money.

**Why:** Domain 4's smoke run cost 4.5× its estimate because `estimate_usd` priced never-run
cases at the suite average; no review had read it.
