# Research: data sources for AnalystAI (Phase 5.8)

Collected 2026-11-19 by four background research agents (usage, peers, connector workflow, tools),
merged here. Primary sources unless marked *community* or *secondary*. Labels: **DEVELOPERS** (a
developer survey), **COMPANIES** (vendor-disclosed customer counts), **INDEX** (popularity index or
web-crawl share), **DATA TEAMS** (a data-team survey). "Not found" means the primary source was read
and the number wasn't there; nothing is estimated.

Not reachable: reddit.com (blocks fetch and search; only mirrors, so Reddit evidence is weak),
julius.ai (403), X and Instagram (not searchable from here). The ROADMAP's source survey ("in the
Domain 3 review note") was never committed; this file rebuilds it from the primary sources.

## 1. Usage numbers

### Databases developers use: Stack Overflow Developer Survey (DEVELOPERS)
ROADMAP's "Postgres 56%, MySQL 41%, SQL Server 30%" is the **2025** survey, all respondents
(55.6 / 40.5 / 30.1), and was right for that year. The **2026** survey (database question n=12,061,
all respondents only, no professional split):
[survey.stackoverflow.co/2026](https://survey.stackoverflow.co/2026/technology/data/databases)

| DB | 2024 | 2025 | 2026 |
|---|---|---|---|
| PostgreSQL | 48.7 | 55.6 | **57.9** |
| SQLite | 33.1 | 37.5 | **42.1** |
| MySQL | 40.3 | 40.5 | **35.1** |
| MS SQL Server | 25.3 | 30.1 | **27.1** |
| MariaDB | 17.2 | 22.5 | 20.3 |
| MongoDB | 24.8 | 24.0 | 19.0 |
| Supabase | 4.0 | 6.0 | 7.7 |
| BigQuery | 4.8 | 6.5 | 6.7 |
| DuckDB | 1.4 | 3.3 | 5.7 |
| Snowflake | 2.6 | 4.1 | 4.5 |
| Databricks SQL | 1.9 | 3.4 | 4.2 |
| ClickHouse | 1.7 | 2.4 | 3.6 |
| Amazon Redshift | n/a | 2.3 | 2.2 |

Sources: [2025](https://survey.stackoverflow.co/2025/technology) (n=26,083; professionals 58.2 / 39.6 / 30.9
for PG / MySQL / MSSQL), [2024](https://survey.stackoverflow.co/2024/technology) (n=52,418). The 2026 sample
is under half of 2025's, so year-on-year moves are noisy.

### DB-Engines ranking, November 2026 (INDEX)
[db-engines.com/en/ranking](https://db-engines.com/en/ranking): Oracle 1096, MySQL 823, SQL Server 694,
PostgreSQL 690 (+38 on the year, the only top-5 riser), MongoDB 366, Snowflake 218 (+20), Databricks 177
(+45, the largest gain in the top 25), SQLite 95, MariaDB 75, BigQuery 48 (−14), ClickHouse 26.5 (#25,
+5.4), DuckDB 12.2 (#40, up from #43). A popularity index from mentions, jobs and search, not installs.

JetBrains State of Developer Ecosystem 2025 (24,534 developers): the page text says only "PostgreSQL
edges MySQL out"; the percentages are chart images. **Not verified.**
[devecosystem-2025.jetbrains.com](https://devecosystem-2025.jetbrains.com/tools-and-trends)

### Small companies' tools (COMPANIES / INDEX)
| Tool | Number | Who | Source |
|---|---|---|---|
| Intuit online (QuickBooks Online + other online products) | 8.9M paying customers | COMPANIES | [Q4 FY26 script](https://investors.intuit.com/_assets/_64f3415e6601d7a94d9a083d8f6622dc/intuit/db/946/10378/webcast_transcript/Q4+FY26+Earnings+Script.pdf), 2026-08-25 |
| Xero | 4.59M subscribers | COMPANIES | [ASX release](https://announcements.asx.com.au/asxpdf/20251113/pdf/06rw38qnphwcgl.pdf), 2025-09-30 |
| HubSpot | 311,704 customers | COMPANIES | [8-K](https://www.sec.gov/Archives/edgar/data/0001404655/000119312526447249/hubs-ex99_1.htm), 2026-09-30 |
| Stripe | >5M businesses | COMPANIES | [annual letter](https://stripe.com/newsroom/news/stripe-2025-update), 2026-02-24 |
| Microsoft 365 | >450M paid commercial seats, SMB-led growth | SEATS | [FY26 Q2](https://www.microsoft.com/en-us/investor/events/fy-2026/earnings-fy-2026-q2) |
| WooCommerce / Shopify / Wix / Squarespace | 7.6 / 5.4 / 4.2 / 2.4 % of all websites | INDEX | [W3Techs](https://w3techs.com/technologies/overview/content_management), 2026-11-19 |
| Google Analytics | 46.1% of all websites | INDEX | [W3Techs](https://w3techs.com/technologies/overview/traffic_analysis) |
| Excel / Google Sheets used regularly | 94% / 61% of 650+ professionals | PEOPLE (vendor-run) | [Smartsheet](https://www.smartsheet.com/content/hidden-cost-of-spreadsheets-report), Oct 2025 |

Not found or not verified: the share of small companies whose main analysis tool is a spreadsheet;
Sage, FreshBooks, Zoho Books, Salesforce, Square, Airtable and Notion counts; a OneDrive vs Google Drive
split; Google Workspace's "11M paying customers" (secondary press only); BuiltWith (blocks automated access).

### Data-team stacks (DATA TEAMS)
**Metabase Community Data Stack Survey 2025**, tallied from the raw file
([repo](https://github.com/metabase/data-stack-survey-2025), 339 rows): 63% of respondents work at
companies of ≤100 people; 22% have no data team. Biased: 67% of BI users use Metabase.
- Transactional DB: PostgreSQL 47.5%, MySQL 16.5%, SQL Server 14.2%, MongoDB 4.7%, MariaDB 3.2%.
- Analytics run on a general-purpose DB: **46.6%**; a warehouse: 36.1%; Google Sheets: 2.1%.
- Warehouse (n=119): Snowflake 37.8%, BigQuery 31.9%, Redshift 21.0%, ClickHouse 7.6%.
- Ingestion (n=222): in-house 27.9%, Airbyte 15.3%, Fivetran 9.9%, dlt 2.3%.

**Airbyte connector registry** ([oss_registry.json](https://connectors.airbyte.com/files/registries/v0/oss_registry.json)):
132 of 592 sources marked high usage, including QuickBooks, Xero, Stripe, Shopify, WooCommerce, HubSpot,
GA4, Google Sheets, Google Drive, OneDrive, SharePoint, Airtable and Notion. dbt's State of Analytics
Engineering 2026 gives no warehouse split; Fivetran publishes no popularity ranking.

## 2. Peers

### Health (GitHub API, 2026-11-19)
| Project | Stars | Last push | License |
|---|---|---|---|
| [Superset](https://github.com/apache/superset) | 75.6k | 2026-11-19 | Apache-2.0 |
| [Metabase](https://github.com/metabase/metabase) | 50.4k | 2026-11-19 | AGPL + commercial |
| [Redash](https://github.com/getredash/redash) | 28.9k | 2026-11-14 | BSD-2 |
| [Chat2DB](https://github.com/OtterMind/Chat2DB) | 28.4k | 2026-11-11 | custom |
| [PandasAI](https://github.com/sinaptik-ai/pandas-ai) | 24.0k | 2025-10-28 (stalled) | MIT + EE |
| [Vanna](https://github.com/vanna-ai/vanna) | 23.8k | **archived** 2026-02 | MIT |
| [DB-GPT](https://github.com/eosphoros-ai/DB-GPT) | 20.3k | 2026-11-19 | MIT |
| [WrenAI](https://github.com/Canner/WrenAI) | 18.3k | 2026-11-19 | AGPL/Apache |
| [SQLBot](https://github.com/dataease/SQLBot) | 7.2k | 2026-11-19 | unstated |
| [Evidence](https://github.com/evidence-dev/evidence) | 7.1k | 2026-11-19 | MIT |
| [Lightdash](https://github.com/lightdash/lightdash) | 6.2k | 2026-11-19 | MIT + EE |
| [WhoDB](https://github.com/clidey/whodb) | 5.1k | 2026-11-19 | Apache-2.0 |
| [Dataherald](https://github.com/Dataherald/dataherald) | 3.7k | 2024-07 (dormant) | Apache-2.0 |
| [nao](https://github.com/getnao/nao) | 2.0k | 2026-11-19 | custom |
| [DataLine](https://github.com/RamiAwar/dataline) | 1.6k | 2026-02 | GPL-3.0 |

### Source × peer
N native · D documented driver/plugin · P paid/cloud-only · − none.

| Source | Metabase | Superset | Redash | Evidence | Vanna | WrenAI | PandasAI | DB-GPT | Julius (closed) | **AnalystAI (today)** |
|---|---|---|---|---|---|---|---|---|---|---|
| Postgres | N | N | N | N | N | N | D | N | N | **N** |
| MySQL/MariaDB | N | N | N | N | N | N | D | N | N | − |
| SQL Server | N | N | N | N | N | N | D | − | N | − |
| Snowflake / BigQuery | N | N | N | N | N | N | P | − | N | − |
| ClickHouse | N | N | N | N | N | N | − | N | − | − |
| DuckDB/MotherDuck | community | D | N | N | N | N | − | N | − | − |
| CSV/Excel upload | N (CSV, 50 MB) | N | N | N | − | D | N | N | N | **N** (≤50 MB) |
| Google Sheets | P (Cloud only) | D (shillelagh) | N | ? | − | − | − | − | via Drive | **N** (service account) |
| OneDrive/SharePoint Excel | − | − | − | − | − | − | − | − | N | − |
| Google Drive files | P | − | − | − | − | − | − | − | N | − |
| S3/GCS Parquet | − | D | D | N | − | N | − | − | − | − |
| QuickBooks/Xero | − | − | − | P (Fivetran) | − | D (dlt skill) | − | − | − | − |
| Stripe/Shopify/HubSpot | − | − | − | P (Fivetran) | − | D (dlt skill) | − | − | − | − |
| GA4 | − | − | N | P | − | D (dlt skill) | − | − | − | − |
| Airtable/Notion | − (#4338) | − | − | P | − | D (dlt skill) | − | − | − | − |
| Generic REST API | − (#4831) | D | N | − | − | − | − | − | − | **N** |

Docs read: [Metabase](https://www.metabase.com/docs/latest/databases/connecting),
[Superset db_engine_specs](https://github.com/apache/superset/tree/master/superset/db_engine_specs),
[Redash](https://redash.io/help/data-sources/querying/supported-data-sources),
[Evidence](https://docs.evidence.dev/core-concepts/data-sources/), [Vanna](https://github.com/vanna-ai/vanna),
[WrenAI](https://docs.getwren.ai/oss/guide/connect/overview) and its
[dlt-connector skill](https://github.com/Canner/WrenAI/blob/main/core/wren/src/wren/skills_content/dlt-connector/SKILL.md),
[PandasAI](https://docs.pandas-ai.com/v3/semantic-layer/data-ingestion), [DB-GPT](https://github.com/eosphoros-ai/DB-GPT),
Julius *secondary* ([connectors](https://julius.ai/product/data-connectors)).

### Demand (GitHub 👍, 2026-11-19)
| 👍 | State | Request |
|---|---|---|
| 1219 | open | [Metabase: ElasticSearch](https://github.com/metabase/metabase/issues/1300) |
| **876** | open | [Metabase: REST API driver](https://github.com/metabase/metabase/issues/4831) (AnalystAI has one) |
| 423 | open | [Metabase: DynamoDB](https://github.com/metabase/metabase/issues/3368) |
| 283 | closed | [Metabase: CSV uploads](https://github.com/metabase/metabase/issues/6094) (shipped) |
| **205** | open | [Metabase: Google Sheets self-hosted](https://github.com/metabase/metabase/issues/7985) (AnalystAI has one) |
| 52 | open | [Metabase: Airtable](https://github.com/metabase/metabase/issues/4338) |
| 14 | open | [Metabase: Salesforce](https://github.com/metabase/metabase/issues/8366) |
| 12 | closed | [WrenAI: SQL Server](https://github.com/Canner/WrenAI/issues/173) |

AI-analyst repos get few connector votes: their users already own a database. Small-company demand
shows instead in forum threads about Excel in SharePoint folders (*community*:
[r/dataengineering mirror](https://redlib.hbubli.cc/r/dataengineering/comments/1j92x7u/best_automated_approach_for_pulling_sharepoint),
[Fabric community](https://community.fabric.microsoft.com/t5/Power-Query/Connect-PowerBI-to-Shared-Folder-for-Free/m-p/4142160),
[HN](https://news.ycombinator.com/item?id=37226524)) and in closed vendors selling "QuickBooks/Shopify + AI
without a warehouse" ([Metabase × QuickBooks via MCP](https://www.metabase.com/integrations/quickbooks),
[Definite](https://www.definite.app/dashboards/shopify-ecommerce)).

### Gaps: what no open-source peer does for small companies
- **Excel in OneDrive/SharePoint and a Google Drive folder:** none (Julius, closed, does).
- **QuickBooks/Xero, Stripe, Shopify, HubSpot, GA4, Airtable, Notion built into the product:** none;
  Evidence only through paid Fivetran, WrenAI only as a developer-facing dlt skill, Redash has GA4.
- **Google Sheets self-hosted:** only Redash and AnalystAI.
- Peer health: Vanna archived, Dataherald dormant, PandasAI stalled — room for an active project.

### Building blocks
| Block | Gives | License |
|---|---|---|
| [dlt](https://github.com/dlt-hub/dlt) + [verified sources](https://dlthub.com/docs/dlt-ecosystem/verified-sources/) | ~30 sources incl. Stripe, Shopify, HubSpot, GA4, Sheets, Salesforce, Airtable, Notion, Pipedrive, SharePoint; `rest_api`; DuckDB destination. **No QuickBooks, Xero, WooCommerce** | Apache-2.0 |
| [Airbyte](https://github.com/airbytehq/airbyte) / [PyAirbyte](https://github.com/airbytehq/PyAirbyte) | 616 sources | **ELv2** (forbids offering as a managed service) → never a dependency |
| [DuckDB core extensions](https://duckdb.org/docs/2.0/core_extensions/overview.html) | httpfs, excel, postgres, mysql, sqlite, iceberg, delta | MIT |
| DuckDB community `gdrive`/`sharepoint` | Drive/SharePoint files | MIT, 4–10 ★, 3–4 months old: too young |
| [SQLAlchemy dialects](https://docs.sqlalchemy.org/en/20/dialects/) | PG, MySQL/MariaDB, SQLite, Oracle, MSSQL built in; Snowflake, BigQuery, ClickHouse, Databricks, `duckdb_engine` external | permissive |
| [Ibis](https://ibis-project.org/backends/support/matrix), [ConnectorX](https://github.com/sfu-db/connector-x) | one API over ~22 backends; fast DB→Arrow | Apache-2.0 / MIT |

## 3. Security lessons from peers
- **The connection string is an attack surface.** Metabase CVE-2023-38646 (pre-auth RCE through an H2
  `INIT=` URL on the test-connection endpoint; [Assetnote](https://blog.assetnote.io/2023/07/22/pre-auth-rce-metabase/)).
  Superset CVE-2023-39265 (a `sqlite://` blocklist bypassed by `sqlite+pysqlite://`;
  [CIRCL](https://cve.circl.lu/vuln/cve-2023-39265)). Lesson: allowlist dialect **and driver**, allowlist URL
  query keys (PyMySQL takes `init_command`, `local_infile`, `read_default_file` from the URL).
- **SQL parsing can be bypassed.** Superset CVE-2026-23984 (DML on a read-only Postgres connection;
  [advisory](https://advisories.gitlab.com/pypi/apache-superset/CVE-2026-23984/)), CVE-2025-55674 (function
  deny-list bypass). Verified here with sqlglot 30.25.0: MySQL `/*! … */` executable comments and `/*+ … */`
  hints parse as plain comments, so `INTO OUTFILE`, `SLEEP` and `MAX_EXECUTION_TIME` hide inside them.
- **The DB role is the only real boundary**; read-only transactions, timeouts and the parse guard are
  defence in depth. SQL Server has no read-only transaction at all. Metabase
  [recommends a read-only user](https://www.metabase.com/docs/latest/databases/users-roles-privileges).
- **DuckDB httpfs bypasses Python SSRF checks** ([Securing DuckDB](https://duckdb.org/docs/stable/operations_manual/securing_duckdb/overview);
  CVE-2024-41672 read files despite `enable_external_access=false`). No model-written SQL reaches a DuckDB
  connection with external access.
- PyMySQL: pin ≥1.1.1 ([CVE-2024-36039](https://osv.dev/vulnerability/CVE-2024-36039)); keep `local_infile` off.
- Peers' guard rails: Superset `ROW_LIMIT=50000`, `SQLLAB_TIMEOUT=30s`, `PREVENT_UNSAFE_DB_CONNECTIONS`;
  WrenAI statement timeout 180 s; Metabase credentials encrypted at rest (AnalystAI: `ENCRYPTION_KEY`).

## 4. Type pitfalls a source must survive
| Pitfall | Source |
|---|---|
| DECIMAL arrives as `Decimal` (object dtype) | [pandas read_sql](https://pandas.pydata.org/docs/reference/api/pandas.read_sql.html) |
| MySQL zero dates `0000-00-00` (PyMySQL returns the string) | [MySQL sql_mode](https://dev.mysql.com/doc/refman/8.4/en/sql-mode.html) |
| `TINYINT(1)` = BOOL comes back as 0/1 ints | [MySQL numeric types](https://dev.mysql.com/doc/refman/8.4/en/numeric-type-syntax.html) |
| MySQL `TIMESTAMP` converts to the session time zone | [MySQL datetime](https://dev.mysql.com/doc/refman/8.4/en/datetime.html) |
| Case-insensitive default collations merge "Acme"/"ACME" in SQL `GROUP BY` | [MySQL charsets](https://dev.mysql.com/doc/refman/8.4/en/charset-unicode-sets.html) |
| SQL Server `DATETIMEOFFSET` unsupported by pymssql/pyodbc; `MONEY` as Decimal | [datetimeoffset](https://learn.microsoft.com/en-us/sql/t-sql/data-types/datetimeoffset-transact-sql) |
| Sheets CSV export gives displayed, locale-formatted values (`10,3`) | *community* [locale separators](https://bettersheets.co/explained/locale-separators) |
| Excel 1900 leap-year bug in date serials | [Microsoft KB](https://learn.microsoft.com/en-us/troubleshoot/microsoft-365-apps/excel/wrongly-assumes-1900-is-leap-year) |

## 5. How mature projects build and test connectors
- **One shared black-box suite, many thin adapters**: Metabase's driver tests ("a huge suite of tests
  that automatically run against all drivers"; [docs](https://www.metabase.com/docs/latest/developers-guide/drivers/driver-tests)),
  Airbyte's [Connector Acceptance Tests](https://docs.airbyte.com/platform/connector-development/testing-connectors/connector-acceptance-tests-reference),
  the [SQLAlchemy dialect suite](https://github.com/sqlalchemy/sqlalchemy/blob/main/README.dialects.rst), Ibis's
  backend suite with explicit `notimpl`/`notyet` marks. AnalystAI's source-invariance oracle is this pattern.
- **Real databases as pinned CI service containers** ([Metabase drivers.yml](https://github.com/metabase/metabase/blob/master/.github/workflows/drivers.yml):
  `mysql:8.4`, `mariadb`, `mssql/server:2022`; "`mysql:latest` now fails" → pin tags). testcontainers needs a
  Docker daemon this machine doesn't run; the SQL Server image is x86-only, so it is tested in CI only.
- **One source per PR**: Metabase MotherDuck driver +2963 lines / 43 files / 12 days; WrenAI StarRocks
  +429 / 24 / 7 days; Superset thin specs +25–600. A first-class source with tests, types, CI and UI is
  400–600 lines; five in one PR ≈ 3k lines, far past the 200–400 LOC a review catches defects in
  (*community summary of* [SmartBear](https://smartbear.com/learn/code-review/best-practices-for-peer-code-review/)).

## 6. Workflow references (AI-assisted, 2025–2026)
- Anthropic, [Claude Code best practices](https://code.claude.com/docs/en/best-practices): interview to a spec,
  build in a fresh session, a failing test first, review by a fresh context that "didn't write the code".
- Kent Beck, [Augmented coding](https://newsletter.kentbeck.com/p/augmented-coding-beyond-the-vibes): TDD; an agent
  disabling or deleting tests is cheating.
- Simon Willison, [Vibe engineering](https://simonwillison.net/2025/Oct/7/vibe-engineering/): the engineer owns
  specs, success criteria and the QA plan.
- Hamel Husain, [evals FAQ](https://hamel.dev/blog/posts/evals-faq/); Anthropic,
  [Demystifying evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents): error analysis
  before spend, binary criteria, pass@k vs pass^k. Connectors are deterministic, so their correctness is
  tests; the paid eval is one E2E case per source.
- Meta, [mutation-guided test generation (ACH)](https://arxiv.org/abs/2501.12862): mutants for one concern,
  keep the tests that kill them (this repo: `scripts/mutation.py --plant`).
- Skills: [obra/superpowers](https://github.com/obra/superpowers), [mattpocock/skills](https://github.com/mattpocock/skills),
  [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) (doubt-driven, constraint-driven,
  deprecation-and-migration installed here).
- Tools: [Hypothesis](https://hypothesis.readthedocs.io/) (property tests for the guard),
  [PayloadsAllTheThings](https://github.com/swisskyrepo/PayloadsAllTheThings) (MIT; SQL injection corpus; sqlmap is GPL
  and only a reference), [Playwright best practices](https://playwright.dev/docs/best-practices), OWASP
  [SQL injection](https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html) and
  [SSRF](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html) cheat sheets.

## 7. Tiered list (share of small companies reached ÷ build and test cost)
| Tier | Sources | Why |
|---|---|---|
| **Must (5.8a, 5.8b)** | MySQL; MariaDB; SQL Server; Supabase/Neon as named Postgres options | Table stakes: every peer has them; SQLAlchemy dialects, cheap; MySQL 35% / SQL Server 27% of developers, 16.5% / 14.2% of small teams' main DB |
| **Differentiators (5.8b, 5.8c)** | Share-link files (public Google Sheet, OneDrive/SharePoint/Drive links, https CSV/xlsx); Stripe, HubSpot, Shopify, GA4 through dlt into DuckDB; files up to ~250 MB through DuckDB | No open-source peer has them; Stripe >5M businesses, HubSpot 312k, Shopify 5.4% of sites, GA 46% of sites; dlt is Apache-2.0 |
| **Before v0.1 (5.8d)** | Snowflake, BigQuery | Every peer has them; 36% of small teams have a warehouse, Snowflake ≈ BigQuery |
| **After v0.1** | QuickBooks, Xero | Largest small-company reach (8.9M + 4.6M) but no dlt source: a registered OAuth app and Intuit's review |
| **Later** | ClickHouse, Airtable, Notion, S3/GCS, private Drive/OneDrive via OAuth | Smaller reach or young building blocks |
| **Never (for this audience)** | Airbyte as a dependency (ELv2); MongoDB, Oracle, Trino, Databricks before v1 | License, or data-team sources |

Decisions taken on this list (user, 2026-11-19): 5.8 splits into 5.8a (contract + MySQL), 5.8b (MariaDB, SQL
Server, share links), 5.8c (DuckDB files, dlt SaaS import), 5.8d (Snowflake, BigQuery). Databases are queried
live; SaaS is imported into DuckDB with a Refresh button; the upload cap rises to ~250 MB with analysis on pandas.
