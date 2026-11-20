# ADR-0010: The source contract (Phase 5.8)

Status: accepted, 2026-11-19. Research: `docs/research/2026-11-19-data-sources.md`.

## Context
Every domain was proven on file uploads. Small companies keep data in MySQL, SQL Server, spreadsheets
and SaaS tools; every open-source peer ships MySQL, SQL Server, Snowflake and BigQuery, and none ships
the small-company SaaS sources or cloud-drive spreadsheets. Phase 5.8 adds sources without opening a new
path through the agents and without a read-only hole. Before 5.8, only Postgres and SQLite were
actually read-only (`run_readonly` set nothing for any other engine) and any SQLAlchemy URL was accepted.

## Decisions
- **One contract.** A source is `DataConnector.load()` → a frame → `build_data_contract` (the ingestion
  agent's one rule) → `prepare_frame` → cleaning. A new source is a small adapter; the agents never learn
  where a table came from.
- **A database engine is a dialect, not a source.** One table in `connectors/sql_connector.py` names, per
  dialect, the accepted drivers and URL query keys, the read-only and timeout statements, the system
  schemas and the sqlglot dialect. An engine missing from it is refused everywhere (fail closed): the
  engine factory, the read-only runner, the parse guard, the SQL agent and the semantic layer.
- **The connection string is untrusted input** (Metabase CVE-2023-38646, Superset CVE-2023-39265). It is
  parsed with `make_url`; driver and query keys are allowlisted per dialect (`init_command`,
  `local_infile`, `client_flag`, `unix_socket`, `read_default_*`, file-path TLS keys and Postgres
  `host`/`passfile`/`service` are refused); timeouts, `local_infile=False` and TLS are set from code.
  In production the host is never this server (loopback), a link-local / cloud metadata address or
  the app's own store's host, and the driver connects to the address that was checked (DNS
  rebinding). A LAN address is allowed: a self-hosted company's database sits on its own network
  (the plan said "public only"; that would refuse the main self-hosted case). No message the user
  reads holds the password (`without_secrets`, applied where a connection test answers).
- **Read-only is four layers** — the parse guard, a read-only transaction, a statement timeout with a row
  cap, and the user's read-only database role (the only real boundary; the rest is defence in depth).
  The parse guard refuses MySQL executable comments and optimizer hints (`/*!`, `/*M!`, `/*+`) in the raw
  text, because sqlglot reads them as comments while MySQL runs them.
- **Live vs import** (user, 2026-11-19): databases are queried live (a question → read-only SQL; a named
  table → a snapshot load, capped and disclosed). SaaS sources (5.8c) are imported by dlt into a local
  DuckDB copy refreshed on demand: no API rate limits per question, and the same numbers every run.
- **Values are normalised once**, where a database result becomes a frame (`QueryResult.frame`): Decimal →
  float, `TINYINT(1)` → bool, zero dates → missing. No per-source frame code anywhere else.
- **Source invariance has one owner**: the oracle in `tests/test_oracle.py` loads every finished domain's
  datasets from every source and requires the same contract, cleaned frame, tiles and built-in answers.
  A new source joins that test; it never gets its own copy.
- **The source list is generated** from `connectors/base.py` `SOURCES`; the README section is a render
  of it, checked by a test.

## The bar (constraints no later change may lower)
- No dialect without read-only and timeout statements; no URL key outside the allowlist.
- No password in any message, trace or SSE event.
- The oracle's equality across sources; a gap is fixed at its cause or recorded in the ledger, never
  marked expected-to-fail silently.
- One E2E case per new source at k=1 and a browser check that connects it.

## Consequences
- MariaDB (its own timeout variable) and SQL Server (no read-only transaction; CI-only database) come in
  5.8b as rows of the same table. Snowflake and BigQuery (5.8d) likewise.
- Typed host/port form fields, SSH tunnels and DB-specific semantic layers are left out until asked for.
