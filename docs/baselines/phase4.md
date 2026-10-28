# Phase 4 review: SQL path and YAML semantic layer

Model `claude-sonnet-4-6`, temperature 0, prompt `sql/v1`, 2026-10-27, branch `phase-4-sql`.
- Final SQL report: `evals/reports/sql-20261028T025653Z.*` (k=3, commit `2b420bc`), paired against the
  k=1 baseline `sql-20261028T023858Z` (commit `537767a`).
- E2E gate: `evals/reports/e2e-20261028T024530Z.*` (k=3, commit `ace8c67`), paired against the Phase 3
  gate `e2e-20261027T220630Z`. Later commits touch only the SQL checks, which the CSV path never calls.
- Intermediate k=3 reports kept as evidence for the two checker fixes: `sql-20261028T024536Z`,
  `sql-20261028T025054Z`.

## Numbers

| metric | target | baseline (k=1) | **final (k=3, 150 runs)** |
|---|---|---|---|
| execution accuracy (result set = golden, 40 cases) | ≥ 85% | 90.0% | **100%** |
| metric definition used when one applies (10 cases) | ≥ 95% | 90.0% | **100%** |
| write/DDL attacks blocked (10 cases, DB fingerprint unchanged) | 100% | 100% | **100%** (27 refusals, 3 harmless reads) |
| fan-out / chasm cases passing a majority of runs | 8/8 | 8/8 | **8/8** |
| hard-rule violations | 0 | 0 | **0** |
| pass rate, all 50 | | 92.0% | **100%** (flip rate 0%, 0 broke) |
| runs with a guided retry | | 4 | **9 / 150** |
| cost / question | | $0.023 | **$0.023** |
| p50 / p95 latency | | 4.9s / 17.1s | **5.0s / 15.5s** |
| **E2E pass rate** | ≥ 45.0% | | **100%** (60/60, 0 broke) — see below |

Offline (pytest, no API): the 30-item attack corpus is rejected by the parse guard alone; with the guard
bypassed, the READ ONLY transaction blocks `DELETE`, CTE-wrapped `DELETE`, `nextval` and `SELECT INTO`,
and the database fingerprint is unchanged; every trap SQL returns a different answer than its golden;
no golden is flagged by the fan-out check; top-N cases are tie-free; golden SQL never appears in a layer
or prompt.

**Read the E2E number honestly.** The +55 points are not Phase 4's. The 11 cases that flipped (e2e-01..08,
12..14) were fixed by `86839e4` between Phase 3 and 3.5 (the cleaning prompt's "null% > 80 → drop_rows"
deleted the data; pandas-3 resample aliases broke targeted time series). Only a k=1 run (75%) had
measured E2E since; this is the first k=3. Phase 4 did not change the CSV path.

**Read the SQL 100% honestly.** It is 50 synthetic questions on two schemas I wrote, k=3. Each case's
trap is proven to change the answer, but the set is small (a 95% CI on 40 cases at 100% is ~91–100%),
and the questions were written to be unambiguous (see deviations). Real warehouses will be messier.

## Built
- `semantic_layer.py`: strict YAML loader (ADR-0003, now Accepted with amendments). Tables carry their
  grain, metrics their grain table + joins, relationships their cardinality, balance-like metrics a
  `non_additive_dimension`. An invalid file stops the boot; `check_against_db` warns on a stale layer at
  connection test. Two layers ship: `evals/sql/retail.yaml` (also the sample) and `evals/sql/banking.yaml`.
- `agents/sql_agent.py` on the agent contract: `SQL_CARD`, `prompts/sql/v1.md`, `build_context`,
  tools `get_metric_definition` + `run_sql`, `Evidence(kind="query")` with the SQL that ran.
- Four safety layers, each tested alone: parsed read-only guard (now also `SELECT INTO`, `FOR UPDATE`,
  side-effect/file/network functions), READ ONLY transaction always rolled back, statement timeout +
  `fetchmany` row cap, and the database role (operators should connect read-only).
- Hard rules: `guardrails.sql_fanout` (grain + cardinality, through same-grain subqueries/CTEs) and
  `guardrails.sql_uses_metric` (AST, alias- and qualification-insensitive) share one guided retry with
  database errors; write SQL or a refusal raises `GuardrailViolation` with no retry.
- Product wiring: a SQL connector with no table answers the question; the query is
  `DataContract.source_query` and the first evidence item; approval answers and follow-ups re-run the
  same query. The UI's table name is optional.
- Eval: `evals/sqlgen.py` (seeded `shop` star + `bank` schemas), 50 cases (36 retail / 14 banking:
  simple 10, join 10, fan-out 8, metric 10 + 2 must-not-apply, attack 10), `evals.run sql`, Postgres 16
  service in CI (`TEST_PG_URL`; PG tests can't be skipped in CI).
- Roadmap: Phase 5.5 "Domain coverage" added at the user's request (any-domain claims need evals).

## Deviations from the plan
- **Bugs found on the way:** the SQL connector had no sync Postgres driver (it could never connect to
  Postgres; `psycopg` added); `EXPLAIN` was already rejected (parses as a command); `returns →
  order_items` is one_to_one (the plan said many_to_one).
- **No separate unknown-column hard rule:** the database error already names the column, and it feeds
  the same retry. Less code, same effect (2 db-error retries in the baseline, both recovered).
- **Layer text is trusted in the prompt.** It is the operator's configuration (e.g. "never sum across
  line items" is guidance). Introspection, results and DB errors are delimited.
- **Case changes after the baseline** (ambiguity removed from the question, scorer unchanged):
  `retail-join-04` now says the no-region group keeps region NULL (the model labelled it 'Unknown');
  `retail-metric-05` says months are identified by their first day (it returned 1–12). The retail
  layer's `is_internal` now says plain counts include internal accounts (the model excluded them
  unasked in `retail-join-07`).
- **Checker changes (the agent's guard and the eval's adherence scorer are the same functions):**
  1. same-grain subqueries/CTEs resolve to their table — `SUM(o_fees.shipping_fee)` over filtered
     orders joined to lines slipped past the fan-out guard in 2/3 runs of `retail-fanout-06`;
  2. unqualified columns in a one-table scope resolve to that table;
  3. a metric's FILTER may AND extra conditions (a period) but never drop one — correct LEFT JOIN SQL
     with the period inside `fee_income`'s FILTER was rejected twice in `bank-fanout-01`.
  Each has a regression test built from the model's own SQL. A prompt v2 aimed at (3) was measured
  (no change on first attempts) and dropped, so the prompt is still v1.
- **Report fix:** refusals and read-only blocks no longer count as "guardrail retries".
- **Spend: $15.44** (plan: $13–14). Baseline $1.15; smokes and one repro $0.57; three full k=3 SQL
  runs $3.46 + $3.54 + $3.39 (each of the first two exposed a checker bug, so the gate had to be re-run
  on the fixed commit); E2E gate $3.32.

## Known limits (carried forward)
- PostgreSQL only is evaluated; MySQL/SQLite run untested beyond unit tests.
- The fan-out check assumes undeclared joins are safe, and maps one alias per statement.
- Tools are used in 123/150 runs; `get_metric_definition` often repeats a definition already in the
  context. Cheap at $0.023/question, so left; revisit if cost matters.
- A user-supplied `sqlite:///path` connection string can read any SQLite file on the server (true
  before Phase 4 for table loads) — for the Phase 6 security pass.
- Domains: retail and banking only. Phase 5.5 covers the rest.

## Post-phase audit (2026-10-27)
- **Fixed:** every `SQLConnector._engine()` call built a pooled engine, so each question left idle
  connections open on the user's database (measured 4 after 5 queries). Engines now use `NullPool`;
  a PG test counts `pg_stat_activity` and fails on the old code.
- **Open, for Phase 5 Intake:** ingestion runs before intake, so a SQL follow-up ("which of those grew
  fastest?") is queried from that sentence alone, without the conversation. Needs the intake/memory
  context work, not a patch here.
- **Open:** MySQL's read-only mode is untested (no driver installed); the card says not evaluated.
