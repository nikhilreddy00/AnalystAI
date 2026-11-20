# Phase 5.8a — the source contract + MySQL (2026-11-19)

Wall clock: 3.6 h to merge-ready (first commit 2026-11-19 19:22 → merge pending the user's approval, PR #27)
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
  TRUNCATE and CREATE fail through `run_readonly` alone, and `SLEEP(20)` stops at the timeout. An engine
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
  production a host is never loopback, link-local or the app's own store, and the driver connects to
  the checked address; a LAN host is allowed (self-hosted).
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

## Spend: $0.18 of $2.50 (ask $2.20, stop $2.35)
E2E e2e-98 $0.0263; live stack $0.149 (one overview). Estimate was ≤ $0.60.

## Audit
| pass | who | majors | minors |
|---|---|---|---|
| 1 | `mattpocock-skills:code-review` Standards + Spec (fresh subagents) | 9 (two copies of a rule ×3, a defaulted dialect, a DNS lookup on the event loop; a question's rows skipped the load rule, TINYINT(1)=2 lost, ADR said "public only") | 8 |
| — | security-guidance reviews on each commit | 4 (IPv4-mapped bypass, DNS rebinding, a quote hiding an executable comment, an unresolvable store failing open) | 1 (the Anonymous-only deny check, covered by a test) |
| — | mutation gate | ~185 survivors → refusals' words pinned, dead branches deleted, true equivalents recorded | — |

**Pass count: 1** review pass with fixes; the stop rule's second pass is the mutation gate and CI on the
fixed code. Ledger: P58-01..08 (C0), UNENFORCED 77 (≤ 77).

Golden re-pinned (`evals.snapshot pin`, CHANGES emptied). Its diff: every contract's fingerprint and its
16-character evidence excerpt (order-free fingerprint), and health_claims-1's -0.0 amount reading 0.0;
no tile, chart or model input moved (`domain_diff.py main`: all identical).

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
