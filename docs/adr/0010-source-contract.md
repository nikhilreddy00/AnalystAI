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
  the app's own store's address, and the driver connects to the address that was checked (DNS
  rebinding). A LAN address is allowed: a self-hosted company's database sits on its own network
  (the plan said "public only"; that would refuse the main self-hosted case). The check knows the
  store's address, not every route to it: the deployment must not publish the store's port
  (`docker-compose.yml` binds it to loopback, a test keeps it so). A MySQL string must name its
  database. No message the user reads holds the password (`without_secrets`, applied where a
  connection test answers).
- **What may be read is the connection's table list** (`_tables`: the default schema's and other user
  schemas' tables; MySQL, the URL's own database only). A named-table load and a question are both held
  to it (audit pass 2: the parse guard's system-schema list caught `pg_catalog.pg_shadow` but not
  `pg_shadow`, and a MySQL question could read another database on the server).
- **Read-only is four layers** — the parse guard, a read-only transaction, a statement timeout with a row
  cap, and the user's read-only database role (the only real boundary; the rest is defence in depth).
  The parse guard refuses MySQL executable comments and optimizer hints (`/*!`, `/*M!`, `/*+`) in the raw
  text, because sqlglot reads them as comments while MySQL runs them; the MySQL session clears
  `ANSI_QUOTES` and `NO_BACKSLASH_ESCAPES` so the server reads quotes as the guard does.
  5.8b hardening: the server runs the guard's token-level canonical text, never the raw one; a
  question streams to its row cap (a server-side cursor on Postgres, unbuffered on MySQL); and a
  Postgres session runs in UTC (`SET LOCAL TIME ZONE 'UTC'`), so `now()`, `date_trunc` and a
  `timestamptz` read the same day whatever the database's default zone, as on the other engines.
- **Live vs import** (user, 2026-11-19): databases are queried live (a question → read-only SQL; a named
  table → a snapshot load, capped and disclosed). SaaS sources (5.8c) are imported into a local copy
  refreshed on demand: no API rate limits per question, and the same numbers every run (5.8c amendment:
  own fetchers into dataset versions, not dlt into DuckDB).
- **Values are normalised once**, where a database result becomes a frame (`QueryResult.frame`): Decimal →
  float, `TINYINT(1)` → bool, a date column's zero dates → missing (by the cursor's type, not the
  value). Columns by position (a join returns two `id` columns). No per-source frame code anywhere else.
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

## Amendment, 5.8b (2026-11-20)
Research: `docs/research/2026-11-20-5.8b-sources.md`. Decided with the user: the SQL Server driver, its
read-only login, links as imports.
- **MariaDB and SQL Server are rows.** MariaDB is MySQL's row with its own timeout (`max_statement_time`, seconds),
  picked by the server (SQLAlchemy's `is_mariadb`), not the URL's word. SQL Server (pyodbc + ODBC Driver 18, the driver
  set by code; only `encrypt` and `trustservercertificate` from the user) has **no read-only session, so its read-only
  layer is the login, required**: each session first refuses a login that can change data, and the query then runs in a
  transaction that is always rolled back; the driver gives the statement timeout. Layer 4 becomes layer 2 there.
- **The parse guard's functions are an allow list** (typed sqlglot classes, including the nodes sqlglot builds from
  syntax and other dialects' spellings, plus per-dialect names it doesn't know); a deny list never ends. A refused
  function gets the SQL agent's one guided retry; a write, a catalog or a lock never does.
- **What may be read stays one rule** (`_tables`), now walking T-SQL's APPLY over functions, `#temp`/`##temp`, and
  T-SQL's always-recursive CTEs.
- **Every host the server fetches for a user goes through one guarded client** (`connectors.base.public_client`): each
  connection (a retry, a redirect hop) resolves once, refuses unless every address is public after unwrapping
  IPv4-in-IPv6 forms (NAT64, 6to4, Teredo, mapped, translated, compatible), connects to the checked address, keeps TLS
  on the name, ignores proxy settings and asks for no compression. Database hosts keep their rule (a LAN host is fine)
  over the same `resolved()`.
- **A share link is an import**, not a live source: downloaded once (https on every hop, ≤ 5 redirects, the upload
  cap counted while streaming, only a workbook or text accepted) and filed as an upload of the same bytes. The link is a
  credential: never stored, logged or echoed (messages name the host only), and eval reports mask credentials.
- **MySQL/MariaDB TLS** is `ssl_mode` in the client's own words; the CAs are certifi's and the operator's bundle (the
  image ships AWS RDS's), never a path from the user; the pinned socket keeps the name for verification.
- **Every file read is bounded before it is parsed**: a workbook's declared unpacked size and parts, a CSV header's width.

## Amendment, 5.8c (2026-11-22)
Research: `docs/research/2026-11-22-5.8c-sources.md`. Decided by a design debate and accepted by the user.
- **SaaS imports use our own fetchers, not dlt.** dlt's verified sources reach the network through their own
  clients (stripe-python, `requests`), outside `public_client`; telemetry is opt-out; a floating `sqlglot` would move
  under our pinned guard. Each vendor is a few cursor paginators through `public_client`, with a fixed host list
  refused before connecting, typed by a declared per-vendor catalog through `QueryResult.frame`.
- **No DuckDB.** An import is filed as parquet dataset versions (one dataset per table), so nothing reads a DuckDB
  copy, and questions on an import run on pandas only: no new SQL path, no new guard. Files stay on today's one reader
  (a second sniffer would break the one load rule); the upload cap rises to what a measured `peak_mb()` holds.
- **Vendors: Stripe, Shopify, HubSpot.** Shopify through a merchant-owned Dev Dashboard app (client credentials) and
  Bulk Operations; HubSpot through Service Keys, a public beta (2026-02). **GA4 is deferred**: Google Cloud orgs created
  since 2024-05-03 block service-account keys by default, and OAuth needs a consent screen per install.
- **A refresh files each table on its own.** A table that fails, times out or is interrupted keeps its last good
  version and is said stale; the row cap is the only cut, and it is said with its first date.

## Consequences
- MariaDB (its own timeout variable) and SQL Server (no read-only transaction; CI-only database) come in
  5.8b as rows of the same table. Snowflake and BigQuery (5.8d) likewise.
- Typed host/port form fields, SSH tunnels and DB-specific semantic layers are left out until asked for.
