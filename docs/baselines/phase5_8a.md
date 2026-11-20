# Phase 5.8a — the source contract + MySQL (2026-11-19)

Wall clock: 19.1 h (first commit 2026-11-19 19:22 → merge 2026-11-20 14:25, PR #27 `dae68bf`; 3.6 h to the first merge-ready, then the night, then audit pass 2 ~4.5 h)
Plan mode before it: ~1.5 h (four research agents, a red-team, the user's four decisions and a second
pass on the execution workflow). Sized at 10.3 h; the breaker (4 h before the query-path step) never
came near: that step was done at 0.8 h.

Plan: `~/.claude/plans/pasted-content-id-cf08-start-phase-greedy-marble.md`. It checks off every
plan-mode step by name:
- **brainstorming:** the user's intent (connect the sources small companies use, read-only, same
  numbers from every source, stars from what no open-source peer has) and the four decisions below;
- **research:** four background agents on primary sources (usage, peers, connector workflow, tools),
  merged into `docs/research/2026-11-19-data-sources.md` (Stack Overflow 2026, DB-Engines, Metabase's
  survey, vendor counts, the source × peer table, the tiered list, the CVE lessons);
- **domain-modeling:** the term table (source, dialect, connection, credential, read-only, live query,
  snapshot load, import, source invariance) → GLOSSARY and ADR-0010;
- **codebase-design:** the deletion test (a source is one `_read` and one row; a database engine is a
  `DIALECTS` row, never a connector) and every reader of the dialect named;
- **red-team:** one fresh Plan subagent, claims checked in code: 3 proven holes (executable comments,
  a password in a message, the oracle never seeing MySQL types), sizing ~2× low; all folded in;
- **grilling:** the user's four decisions (split 5.8 into a/b/c/d; databases live, SaaS imported; a
  ~250 MB DuckDB file cap later; share links, SaaS and Snowflake/BigQuery before v0.1), then the
  execution workflow table (skill, tool, proof, reference per step) the user asked for;
- **sizing:** 10.3 h and ≤ $0.60 planned; actual below;
- **stop rule:** at most 2 audit passes, stop at a pass with 0 majors (see Audit).

## Decisions (user, 2026-11-19)
- 5.8 is four sessions: **5.8a** contract + MySQL (this), **5.8b** MariaDB + SQL Server + share links,
  **5.8c** DuckDB files + Stripe/HubSpot/Shopify/GA4 via dlt, **5.8d** Snowflake + BigQuery.
- Databases queried live; SaaS imported into DuckDB with a Refresh button.
- The browser check binds the scratch MySQL to the LAN address so production mode runs unchanged
  (after the safety classifier refused it on my own initiative; the user allowed it).

## Result
- **MySQL connects read-only.** A `DIALECTS` row (PyMySQL ≥ 1.1.1, `charset` the only URL key,
  `SET SESSION TRANSACTION READ ONLY` + `max_execution_time` + `START TRANSACTION READ ONLY`,
  `local_infile` off, UTC, connect/read timeouts). On the real database INSERT, UPDATE, DELETE, DROP,
  TRUNCATE and CREATE fail through `run_readonly` alone **for the read-only reason** (audit pass 2:
  the first version of this test accepted any error and passed 5 of 6 writes with read-only off;
  now plant-proven: read-only statements removed → 17/17 red), and `SLEEP(20)` stops at the timeout. An engine
  or driver without a row is refused by every reader (`safe_url`, `run_readonly`, `sql_dialect`, the SQL
  agent, the semantic layer's check). Supabase and Neon strings are accepted as Postgres.
- **The source oracle owns source invariance**: all 33 domain datasets × xlsx, json, parquet, sqlite,
  postgres, mysql, api, sheets give the CSV's contract (but the fields naming the source) and the same
  cleaned rows, values exact; tiles, chart rows and built-in answers are byte-equal for one dataset per
  pack; a table written with MySQL's own types (TINYINT(1), DECIMAL, zero dates, DATETIME(6),
  TIMESTAMP, '' vs NULL, utf8mb4) reads as the CSV of the same table. 266 cases.
- **What the stronger bar found on the old sources** (P58-01..03, fixed at their cause, golden moved):
  SQL and API tables kept text dates; JSON floats 1 ulp off; -0.0 only in the CSV; Postgres returns a
  table's rows in another order, which the fingerprint (and a resumed run's "data changed" check) read
  as different data. One rule now: `DataConnector.load = normalize_frame(_read())`, an order-free
  fingerprint.
- **The parse guard** refuses MySQL's executable comments and hints anywhere in the text, its
  side-effect functions and system databases (an attack corpus from PayloadsAllTheThings shapes, a
  Hypothesis property, sqlglot's must-allow reads).
- **No password in a message** (`without_secrets`, a fixed message for an unreadable string). In
  production a host is never loopback, link-local or the app's own store's address, and the driver
  connects to the checked address; a LAN host is allowed (self-hosted). The store's other routes are
  the deployment's: compose binds its port to loopback (audit pass 2, B4).
- **README's source list is generated** from the connectors (`sources_table`, a test keeps them equal).
- **E2E**: e2e-98 (e2e-64's incurred-total trap from a MySQL table) passes at k=1, $0.0263.
- **Live browser (1440 px, production mode, asyncpg, real worker)**: the connect form took
  `mysql://readonly:…@192.168.1.168:33069/acme` (a SELECT-only user: the database refused its DELETE),
  table `order_lines`; the overview's five tiles equal the CSV path's exactly (Net sales $310,635.46,
  Orders 2,272, Avg order value $136.72, Units sold 6,548, Return rate 3.9%); the run `completed`; the
  password appears nowhere in the page text; $0.149. Screenshot `.live/phase5_8a_mysql_report.png`.
- **Security in CI**: ruff `S` on the product, gitleaks (blocking; its first run flagged the Playwright
  spec's made-up password: one `.gitleaksignore` fingerprint), pip-audit (reporting); the security job
  reads only and pins its third-party actions to commits.
- **Full suite** on the final commit with Postgres and MySQL running locally: 4,052 passed, 2 skipped,
  3 xfailed (`scripts/suite.py`, 14.8 min). CI: 4 backend shards (with the `mysql:8.4` service),
  frontend, zero-suites (the mutation gate's `--check`) green.
- **Mutation gate**: 20 changed functions in `connectors/base.py`, `connectors/sql_connector.py` and
  `guardrails.py` (now GATED) recorded clean: the first run left ~185 survivors (unasserted refusal
  words, untested branches, dead code); after tests, deletions and 4 named equivalents (a numeric column
  never equals '', SQLite is the last dialect row, `None` vs `[]` read only by `if not`, a 15 ms longer
  timeout), 0 survivors on changed lines.

## Gates
- live browser at 1440 px: done (MySQL over the LAN address, production mode, the tiles read against the CSV path)
- finished domains' filter reads: done (the oracle compares every dataset's cleaned rows from every source exactly, and the whole dashboard per pack; `domain_diff.py main`: every dataset identical)
- all-domain answer probe: done (built-in answers byte-equal per pack from every source; golden on all 8 domains)
- playwright specs: done (`mysql_connect.spec.ts`, plant-proven; the full suite 44/44 before it)
- audit pass 2 (2026-11-20): golden 64/64 unchanged after the column-by-position change; full suite
  4,092 passed (3 skipped, 3 xfailed) with Postgres and MySQL; mutation gate `--check` clean on every
  changed function (survivors killed by tests, 5 equivalents named); e2e-99 1/1; no frontend change

## Spend: $0.30 of $2.50 (ask $2.20, stop $2.35)
E2E e2e-98 $0.0263; live stack $0.149 (one overview). Estimate was ≤ $0.60. Audit pass 2: e2e-99 (a
question answered by the SQL agent in MySQL, the path no paid run had covered) 0/1 $0.061 (the silent
20-table schema cap, B12), fixed, then 1/1 $0.059 (`evals/reports/e2e-20261120T155812Z.md`).

## Audit
| pass | who | majors | minors |
|---|---|---|---|
| 1 | `mattpocock-skills:code-review` Standards + Spec (fresh subagents) | 9 (two copies of a rule ×3, a defaulted dialect, a DNS lookup on the event loop; a question's rows skipped the load rule, TINYINT(1)=2 lost, ADR said "public only") | 8 |
| — | security-guidance reviews on each commit | 4 (IPv4-mapped bypass, DNS rebinding, a quote hiding an executable comment, an unresolvable store failing open) | 1 (the Anonymous-only deny check, covered by a test) |
| — | mutation gate | ~185 survivors → refusals' words pinned, dead branches deleted, true equivalents recorded | — |

| 2 | user-asked senior audit (2026-11-20): `code-review` (high), `pr-review-toolkit:silent-failure-hunter`, `pr-review-toolkit:pr-test-analyzer`, `security-review` (fresh subagents, parallel) | 7 | 4 |
| — | security-guidance on each audit-2 commit | 3 (a CTE shadowing a table, a function read as a table, a function given SQL as text) | — |
| — | the paid run on the question path (e2e-99) | 1 (a silent 20-table schema cap) | — |

**Pass count: 2** (the stop rule's last). Pass 1's fixes held; pass 2 found what pass 1's reviewers
never looked at: the question path's shapes and scope, and tests that could not fail.

### Audit pass 2 (2026-11-20): what was found, why it was missed, what fixed the class
Every finding below was reproduced, then a red test committed (25abf3b), then fixed at its cause.
| # | bug | root cause | fix |
|---|---|---|---|
| B1 | a join's two `id` columns crashed every dialect's question path (regression) | 5.8a's column steps ran by name before ingestion makes names unique | columns by position; a Hypothesis property over result shapes |
| B2 | a question read `pg_shadow`, MySQL's processlist, another database on the server | two copies of "what may be read" (the load's table list, the guard's qualified-schema list) | one rule: a question reads only `_tables`; CTEs by sqlglot scope; table functions and text-SQL functions refused |
| B3 | write tests passed with read-only off | any error counted; mutmut doesn't mutate data rows | the read-only reason required; hand plant 17/17 red; a test bans `raises(Exception)` there |
| B4 | the store reachable via the host's LAN address (compose published 5432, default credentials) | the IP check can't know every route; the deployment file was outside the threat model | compose binds loopback; a test |
| B5/B6 | a session failure blamed on the query (a paid retry); a skipped layer silent | new failure points not traced through callers' `except` | IngestionError for session setup; a traced, stated skip |
| B7 | a MySQL string without a database listed every database | the empty edge untested | refused |
| B8/B9 | a capped load read arbitrary rows; zero dates by value in any column, missed with a fraction | symptom fixed (order-free fingerprint), not cause; the docs' example, not the driver's output | key order; date columns by cursor type, any precision |
| B10 | MySQL tests could skip unseen in CI; the timeout test swallowed errors | env-gated tests without a CI rule | the backend job refuses to start without both URLs |
| B11 | sql_mode unpinned; two address normalisations; a test bug read as a connection problem | — | session clears ANSI_QUOTES / NO_BACKSLASH_ESCAPES; `resolved()` normalises; logged |
| B12 | (paid run) the SQL agent saw 20 tables per schema, silently | a Phase 4 cap; no question-path run on a many-table database | every table named, columns up to a budget |

Not fixed, with reasons: `normalize_frame` runs twice on CSV/SQL loads (idempotent; `_read_bytes` has
other callers); no password scrub on the run path (no driver message carries it; the route scrubs).
Known ceiling: the parse guard is a deny list (a SQL text built by concatenation isn't seen); the
database role is the boundary (ADR-0010 layer 4). Follow-up for 5.8b: an allow list of functions.

Workflow changes (ledger P58-09..17): list the invariants established after a boundary before adding
code at it; one rule by meaning (where else is this decided?); hand plants for safety data rows; a
refusal test names its reason; env-gated tests required in CI; trace callers' `except` for every new
failure point; the threat model reads the deployment files; test the driver's real output; a paid
run per path a change touches (the table path's E2E never saw the question path).

Ledger: P58-01..17, UNENFORCED 77 (≤ 77).

Golden re-pinned (`evals.snapshot pin`, CHANGES emptied). Its diff: every contract's fingerprint and its
16-character evidence excerpt (order-free fingerprint), and health_claims-1's -0.0 amount reading 0.0;
no tile, chart or model input moved (`domain_diff.py main`: all identical).

## Retro
Each item, matched to the ledger (ROADMAP 5.8 "Lessons out"); the next 5.8 plan starts from these
rows via `scripts/ledger.py --files` ("Lessons in").
- The audit step named no reviewers; four fresh ones found 12 bugs after Standards + Spec passed → P58-19 (new)
- No phase started from earlier phases' rows or matched its retro to them → P58-20 (new)
- A test expecting any error passed on the wrong one (ruff PT011/B017 now, every test) → P58-11 (new)
- Mutation testing can't mutate data rows: the read-only plant is a permanent test → P58-11 (new)
- The nightly was red two nights unseen; the test databases' start commands were lost in a reset → P58-21 (new)
- Two copies of "what may be read" (CODING_STANDARDS "One question, one rule") → P58-10 (new)
- A step at a boundary relied on a later invariant (CODING_STANDARDS "Code at a boundary …") → P58-09 (new)
- The guard's function deny list drew five security-review rounds: the allow list is 5.8b's scope → P58-10 (new)
- Nightly: every line open past its request, none delivered, read as unknown OTIF → D1-40 (recurrence: the D8 fix covered "no line on time", not "no line delivered"; property D found it, nobody read the nightly)
- Nightly: a one-day subscription then a return counts 3 churns and a reactivation, the reference 2 and none → P58-18 (new)

## Deviations
- **MariaDB moved to 5.8b** (red-team: its timeout variable differs; CI-only like SQL Server).
- **LAN hosts allowed in production** (the plan said public only: that refuses the main self-hosted case).
- **MySQL TLS not in 5.8a**: the host is pinned to the checked IP, so verified TLS needs the 5.8b work
  on hosted MySQL (PlanetScale, RDS).
- **pip-audit reports, doesn't block**: 14 locked packages already on main have advisories (pyjwt,
  cryptography, starlette, pillow …; pymysql clean). The upgrade is its own PR before v0.1.
- **Semantic layer metrics still parse as Postgres** (no MySQL layer exists; a `ponytail:` comment).
- **The connection-string field is plain text**, password visible while typed (peers use a password
  field): a UI follow-up.

## Follow-ups
- A dependency-upgrade PR (pip-audit's 14 packages) before v0.1, then pip-audit blocking.
- 5.8b: MariaDB, SQL Server, MySQL TLS, share links; a password-type field or typed host/port fields.
