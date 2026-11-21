# Phase 5.8b — MariaDB, SQL Server, share links (+ the 5.8a carry-overs) (2026-11-20)

Wall clock: 2.8 h (first commit 2026-11-20 16:42 → merge pending; 2.8 h to the audit's last fix at 19:31)
Plan mode before it: ~1.4 h (a background research agent, a code-map agent, a red-team, the user's five decisions in
two rounds). Sized at ~12.5 h (the red-team's 12–15 h); the breaker (4.0 h before the paid step) never came near:
the paid E2E ran at 1.4 h.

Plan: `~/.claude/plans/pasted-content-id-2968-phase-merry-mochi.md`. It checks off every plan-mode step by name:
- **brainstorming:** the user's brief settled intent; five decisions asked in two rounds (below);
- **research:** one background agent on primary sources → `docs/research/2026-11-20-5.8b-sources.md` (with the
  build's corrections as an addendum);
- **domain-modeling:** the term table (share link, import, read-only login, checked address, allowed function) →
  GLOSSARY and the ADR-0010 amendment;
- **codebase-design:** the deletion test: an engine is a `DIALECTS` row, never a connector; one guarded HTTP client
  for every user URL; one `_register` path for an upload's and a link's bytes; the table load written by the
  engine's compiler;
- **red-team:** one fresh Plan subagent, claims checked in code: 5 blockers, 9 majors, 6 minors, all folded in
  (B1, B3, B4 reproduced before folding);
- **grilling:** the two question rounds and the research corrections (pyodbc's converter, NAT64);
- **sizing:** ~12.5 h and ≈ $0.70 planned; actual below;
- **stop rule:** at most 2 audit passes, stop at a pass with 0 majors and the properties, mutation gate and ledger
  checks green (see Audit).

## Decisions (user, 2026-11-20)
- SQL Server driver: **pyodbc + msodbcsql18** (research recommended python-tds; pyodbc kept TLS name verification
  under the pin and needs no converter: SQLAlchemy registers DATETIMEOFFSET's).
- SQL Server **requires a read-only login** (it has no read-only session).
- Share links are **imported as a dataset version**, not a live source.
- The live link: a public Google Sheet made from an eval dataset in the user's Drive, trashed after the check.
- colima installed: MariaDB 11.4 natively, SQL Server 2022 CU28 under Rosetta, for the tests, the paid runs and the
  browser check.

## Result
- **MariaDB and SQL Server are `DIALECTS` rows**, no new connector. MariaDB is MySQL's row with
  `max_statement_time`, and the row follows the server (`is_mariadb`), so a `mysql://` string pointed at
  MariaDB works. SQL Server (pyodbc + msodbcsql18) has no read-only session, so each session first
  refuses a login that can change data: an **allow list** of the login's effective database and server
  permissions, every grant to it or its roles, and ownership. On the real servers (colima locally, service
  containers in CI) writes fail for the read-only reason, a slow query stops at the timeout, the plants
  are permanent tests, and a db_owner, db_datawriter, db_securityadmin, db_ddladmin, EXECUTE, IMPERSONATE,
  schema-owner or object-owner login is refused.
- **Share links** (Google Sheets/Drive, OneDrive/SharePoint, Dropbox, any https CSV/xlsx) are downloaded once by
  the server and filed as an upload (`_register`), through **one guarded HTTP client** (`public_client`):
  each connection resolves once, requires a public address (every embedded IPv4 unwrapped), and connects
  to the checked IP; ≤ 5 https hops; raw bytes capped at 50 MB with no Content-Encoding; one 120 s deadline;
  kind by the bytes; no token in any message, log, trace or report. The REST API source moved onto the
  same client (the key stays on its origin; one byte budget per load).
- **The parse guard's functions are an allow list** (typed sqlglot classes + per-dialect names, built from
  what a corpus parses to). All 58 SQL eval cases and the 138 stored model SQLs pass unchanged; an analyst
  function off the list gets one retry; a lock, admin, session or file function is final. T-SQL batch
  words and invisible characters outside strings are refused (both proven on SQL Server in the audit).
- **MySQL/MariaDB TLS:** `ssl_mode=REQUIRED|VERIFY_CA|VERIFY_IDENTITY`, certifi + the operator's
  `EXTRA_CA_BUNDLE` (the image ships AWS RDS's), the name kept for TLS under the production pin.
- **The connect form** has a password field (type=password, URL-encoded into the string, never shown).
- **The oracle** passes for mariadb, mssql and link over every dataset; the own-types test for MySQL,
  MariaDB and SQL Server. Golden: 64/64 unchanged; `domain_diff.py main`: every dataset identical.
- **Paid E2E** e2e-100..104 at k=1: 5/5 ($0.121). Live 1440 px check: MariaDB and SQL Server tiles equal
  the CSV path's, the `sa` login refused in the form, the Sheet link answered 60 %/40 % ($0.348).

## Gates
- live browser at 1440 px: done (production mode; MariaDB and SQL Server over the colima VM's address, the Sheet link
  pasted in the form; tiles equal the CSV path's, a writer SQL Server login refused in the form, one Dashboard filter
  changed a tile and the charts to the filtered truth)
- finished domains' filter reads: done (the source oracle compares every dataset's cleaned rows and, per pack, the
  whole dashboard and built-in answers from MariaDB, SQL Server and the link)
- all-domain answer probe: done (built-in answers byte-equal per pack from every new source; golden on all 8 domains
  unchanged)
- playwright specs: done (`mysql_connect.spec.ts` password field, `share_link.spec.ts`)
- full suite (`scripts/suite.py`): 4561 passed, 1 failed (the note's commit named "retro" with the ledger rows one
  commit before: paired in `UNLEDGERED`, green after), 5 xfailed (pinned open bugs)
- mutation gate: every changed gated function recorded clean; 51 survivors closed (29 killed by a planted-and-caught
  test, 21 named equivalents, 1 unread message removed); `--check main` passes
- golden 64/64 unchanged; `domain_diff.py main` identical on every dataset

## Spend: $0.469 of $2.50 (ask $2.20, stop $2.35)
E2E e2e-100..104 $0.121; live check $0.348. No audit fix needed a paid rerun: none touched a prompt or
what reaches the model (the replay and golden show the same contexts).

## Audit
Two passes, four fresh reviewers in parallel each time: **code-review** (high),
**silent-failure-hunter**, **pr-test-analyzer**, **security-review**. Every finding: a red test (shown
failing on the code before its fix), the fix at its cause, a ledger row.
- **Pass 1** (about 25 findings, all fixed):
  - security-review: T-SQL batch injection (`FROM t x EXEC(…)` read as an alias); the login check missing
    ownership, IMPERSONATE and EXECUTE; Postgres's string mode; the xlsx ratio.
  - silent-failure-hunter: a byte cap counted after decompression; JSON/Parquet links read as CSV; a
    redirect tested "reachable"; corrupt workbooks and malformed links as 500s; session errors costing paid
    retries; a per-read timeout.
  - code-review: a `LATERAL generate_series` regression; `SSL_MODE`'s case; the password form; the
    unpacked total; the filename kept a path.
  - pr-test-analyzer: each login-check branch, the pin per scheme, the oracle's reorder (database sources
    only), two refused functions ending a question, report masking of an escaped quote.
- **Pass 2: majors remained, so the stop rule's cap (2 passes) ended the audit, not a clean pass.** All
  majors and all but three minors fixed:
  - security-review: a zero-width space split a statement on SQL Server (high, proven); a db_securityadmin
    login passed the check and wrote (proven) → the login check became an allow list; ODBC's DSN and
    Windows sign-in fallbacks.
  - code-review: lock/admin functions became retryable and `p.close` failed a run → soft/hard decided in
    the guard by meaning; no rate limit on `/connectors/link`; minors (HTML case, repeated option,
    off-by-one, TLS context, Key File Map).
  - silent-failure-hunter: a pinned MySQL socket's raw OSError and the API's untyped errors took the wrong
    path; the API key sent to another origin; one cap per page; the .md reports unmasked.
  - pr-test-analyzer: CI's SQL Server login race (reproduced: 4 of 5 rounds failed); no test of the API
    deadline or the exact header read; the pinned SQL Server session; `importLink`'s unused `dataset_id`
    (deleted).
  - Not fixed, said: the xlsx per-part ratio stays 200 (not probed; repetitive real sheets pack far past
    XML's usual 5-10x); a secret inside a JSON string inside a report isn't masked (no report stores
    one today; the committed-report grep still guards URL passwords); server-level IMPERSONATE has no
    test of its own (a server-wide grant races the xdist workers; the allow list covers it).

## Repeat risks
- P58-01: every new source ends in `normalize_frame`: the oracle over mariadb, mssql, link — held
- P58-02: exact values (SQL Server DECIMAL/MONEY via `coerce_float`, the link's bytes) — held
- P58-03: SQL Server returns keyless rows in no order: MAXDOP 1 on the load, the oracle reorders database sources only — held
- P58-04: SQL Server's read-only is the login check, its timeout the driver's (`LOCK_TIMEOUT` raced it and was deleted) — held
- P58-05: the guard alone refuses T-SQL attacks — recurred in the audit (batch alias, U+200B), fixed at the tokenizer
- P58-06: no password or token in any sink — recurred (report writers, query keys, the API's next page)
- P58-07: every hop checked at connect, embedded IPv4 unwrapped — held
- P58-08: types per engine (BIT, DATETIMEOFFSET, TINYINT(1)) — held
- P58-09: duplicate names by position on mariadb/mssql — held
- P58-10: `_unlisted_tables` the one rule (APPLY, `#temp`, schema-qualified functions) — held after the plan's fix
- P58-11: plants are permanent tests on mariadb/mssql; PT011/B017 on — held
- P58-12: CI refuses to start without the new URLs — held; P58-31 (the login race) found by the audit
- P58-13: the downloader refuses loopback and the app's store — held
- P58-14: session errors never a paid retry — recurred (table listing, tool call, pinned socket, API errors)
- P58-15: zero dates by the cursor's type on MariaDB — held
- P58-16: session string modes read as the guard does — recurred (Postgres)
- P58-17: SQL Server schemas listed by name — held
- P58-19: four fresh reviewers — done, two passes
- P58-20: Repeat risks and Retro matched to the ledger — this note
- P58-21: nightly read at Step 0 (two pinned bugs, out of scope); `test_dbs.sh` starts both new engines — held
- D4-03: the oracle covers the new sources — held
- D3-10: tests take the production path (the route, `get_connector`, the SQL agent) — held
- D1-38: a widened rule lists its readers — recurred (APPLY broke LATERAL; the batch-word scan broke `close`)
- D1-59: the wall clock is in this note — held
- D3-34: no one-off scripts (`test_dbs.sh` extended) — held

## Retro
Each item, matched to the ledger (ROADMAP 5.8 "Lessons out").
- SQL Server's login check was a deny list: each audit found more ways to write; an allow list ends it → P58-23 (new)
- A tokenizer and its server disagreed twice (a statement word as an alias, U+200B as a space) → P58-05 (recurrence: 5.8a fixed one disagreement, MySQL's comments; the class is any character or word the two read differently)
- Session errors took the paid path from three callers 5.8a's fix didn't reach → P58-14 (recurrence: the fix sat in `run_readonly`, not at every session the connector opens)
- Postgres's string mode was never pinned → P58-16 (recurrence: the 5.8a fix was MySQL's only)
- A T-SQL rule broke a Postgres read, a keyword scan broke a column name → D1-38 (recurrence: each judged on its attack, not on the reads beside it)
- Caps and deadlines sat at a layer an attacker steps past → P58-27 (new)
- The source boundary said the wrong cause or crashed on odd input → P58-28 (new)
- One report writer of three masked, the .md never → P58-06 (recurrence: the 5.8b fix masked where it was written, not every writer)
- The connect form dropped or kept a password silently → P58-29 (new)
- The link route had no rate limit → P58-30 (new)
- CI's login race only shows on a fresh server → P58-31 (new)
- Two commits went in red through `| tail -1 &&` → P58-32 (new)
- A Google Sheet with no tab exported 400 for `gid=0`; only the live check saw it → P58-25 (new)
- `mysql://` at a MariaDB server failed every session → P58-22 (new)
- Uploads had no unpacked-size bound → P58-24 (new)
- macOS drops `DYLD_*` for system binaries → P58-26 (new)
- Two checks only CI runs fired after the push (gitleaks on CI's SQL Server password, the status-row cap) → P58-33 (new)
- CLAUDE.md's 5.8 status row grew past its cap → PR3-05 (recurrence: a doc edit runs no test before the push)
