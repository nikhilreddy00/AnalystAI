# Glossary (builder terms)

Domain words (ALOS, readmission, NPL …) live in `domain_packs/<domain>.yaml`, not here.

- **Protected working copy** — the copy of a patient-data frame that agents and the sandbox see:
  personal columns withheld, ids pseudonymised, small groups of patient attributes pooled. It is
  not "de-identified": exact service dates stay (see ADR-0008, 2026-11-03).
- **Version chain** — the rows sharing one key (e.g. a claim and line), ordered by a version
  column (a number, or a date: a loss run's valuation date); only the final version counts, and a
  chain whose final version is a void counts as cancelled.
- **Within term** — a KPI term counting the entities' rows that are followed by another row of the
  same entity within N days of their end; rows whose window runs past the data's last start are
  censored (left out of the count and the base).
- **Small cell** — a figure about 1–10 people: a count of patients or encounters on patient data;
  elsewhere any number on a group by a protected role, or of a protected measure (pay), with
  fewer than 11 people. Never shown; with one pay group hidden, the next smallest is hidden too.
- **Protected role** — a special-category attribute (gender, ethnicity, disability, religion,
  union membership …) or pay: described only, never a recommendation, never a small cell.
- **Spell** — one entity's stay between two dates (an employee from hire to termination, a
  subscription from start to end); a returning entity has a spell per stay. Rows are
  spells, or dated snapshots of who is in force.
- **In force at t** — an entity's latest row on or before t (a snapshot, or its latest spell
  started by t; a pack with concurrent spells keeps every one in force, a role summed over them:
  a customer's base plan and add-on) whose end is blank or after t, and whose status doesn't say
  it ended at an unknown date. Headcount is the people in force at a date; never a sum of rows or months.
- **As-of date** — the date spell figures are measured at: a report date in the data, else the
  latest snapshot, else the latest start or end date not after today. Dates after it are not
  yet events. Flows and rates read the 12 months to it unless a period is asked. A month label's
  as-of is that month's end, never after today. Data with no spells: the latest day of its time
  axis (the last ship or order date), not after today. Always judged on the whole data.
- **Past due** — still open after its promised date, as of the as-of date: late, never left out
  of an on-time rate (a shipment in transit two days after its promise has already missed it).
- **Window** — the span a flow or rate reads: (its start, its end]; the 12 months to the as-of,
  or an asked period, cut to the shorter span since the first date the data knows a state
  (snapshots: from the first snapshot, its month-end included; spells: the day before the first
  end, since a list of recent leavers misses who left before it, or before the first start when
  nobody ended). Its month-ends: a span of exactly N months (the 12 months to 24 Oct) has N, the
  end's own month ending at the end; any other span has one per month it touches (since 15 Mar
  keeps 31 Mar). Moves start at the first known state. Prose that names it says this data's
  span through `{window}` (flows), `{moves}` or `{cohort}` ("the 12 months", "the 7 months" on
  short data, "the months" when no window is known yet), never a written 12. A rule's own span
  (YoY's same window a year earlier, an annual invoice) is no window.
- **Dated by** — the date role a KPI's rows are dated by when it is not the pack's time axis
  (billings by invoice date on a file of MRR months): its answer names that role's span.
- **Opening cohort** — the entities in force 12 months before a window's end (or at the first
  date the data knows a state when it covers less); retention, NRR and logo churn follow them to
  the end, whatever period is asked; by group, each entity sits in the group of its row at the
  opening, the first in sort order when it has two (a cohort follows its entities, never its
  parts). An asked period's bridge opens at the
  period's own start.
  A cohort needs a span: when the data knows a state only at the window's end (one month of
  data), its opening and rates are unavailable, never a 0% churn over no time. A signup cohort
  (entities grouped by start month) is a different thing.
- **Measured** — a figure the data supports, with its evidence; 0 is a measurement (nobody in
  force at the as-of is 0 MRR, never unavailable).
- **Unavailable** — a figure the data can't support, with its reason: a role it lacks, a column
  it reads left blank, no end date for a churn or exit, a 0 denominator, no month of moves, a
  group under the small-cell limit. Said as unavailable, never shown as 0.
- **Snapshot cadence** — how often a pack's snapshots are taken (monthly), declared by the pack:
  an entity absent from the newest snapshot of that cadence is gone. Guessed from the dates only
  for a pack that declares none.
- **Today-only status** — a status a list holds for today only (a paused or unpaid subscription):
  it keeps the entity out of every month-end, since the date it changed is unknown, and the
  answer says so.
- **Developing period** — a period year younger than the pack's `maturity.months` at the latest
  valuation date (an accident year whose claims are still being reported and reserved): named with
  its age, never cut and never called final (Domain 4, 2026-11-03).
- **Role caveat** — a pack's caution said whenever the data has that role, on a strong or weak
  match (a protected attribute: described, never a rating recommendation).
- **Marker field** — a column filled only on the rows it applies to (a catastrophe event code):
  its blanks mean none, the file's layout, never missing values.
- **Part** — the rows a figure is cut to: a filter's rows, or one group of a by-column. The rows
  outside it are a part too.
- **Term rows** — the rows a term reads at a date: in force then, of the KPI's row kinds, meeting
  its condition (paying, not a contractor).
- **Counted record** — a row a count tile counts: of kind sale, in the main currency (`KPI.rows`'
  default). A count by group, from the model's code or a generic chart, reads these
  (`count_rows`); a cancelled, test or superseded row, a return line and another
  currency's row are not counted records. Rows ≠ records where a record has lines (a claim).
- **Kind-deciding column** — a column whose values name a row's kind: the status, the currency, a
  non-product code, a version's void code (`kind_labels`). A breakdown by one keeps the kinds it
  names (a Status breakdown shows its cancelled rows); an id, a test marker or a version order is
  not one (a breakdown by claim id still counts each claim's latest version only).
- **Ledger** — each entity's value in each part at each month-end: one **ledger line** per
  (entity, part), its term rows in that part summed (a role), or 1 for being there. An entity's
  value is the sum of its lines. Built on all rows. MRR movements and a bridge's opening are read
  from it, never from row sums (Domain 6, 2026-11-05).
- **Move** — one entity's change between two consecutive month-ends. First, its matched fall in
  some parts and rise in others is a **transfer move** (out of the parts that fell, into the parts
  that rose); it adds nothing to the whole. The rest is classed on the entity's whole value, of
  one kind: new (from 0, never in force before), reactivation (from 0, in force at an earlier
  month-end: a stay that began and ended inside one month is unseen, so its return is new),
  expansion, contraction, churn (to 0); it is booked in the parts that moved, in proportion. On
  counts, a part gained or lost while the entity stays is a transfer on its own. Answers say a
  transfer as "moved in from / out to another <group>" ("transfer" is a bank transaction and a
  hospital discharge).
- **Move breakdown** — a breakdown filed under a level's column (MRR by month) that answers a
  move the question names (churned MRR by month): decided by what the question names, a move or
  a level, never by the values. A level summed over dates with no move named is the level.
- **Month label** — a snapshot date written on the month's 1st that names the whole month (an
  MRR export's `2026-10-01` is October): the as-of is that month's end.
- **Unit** — the kind of number a KPI gives: money, percent, count or number, declared once on
  the KPI (`unit`). A glossary phrase is its KPI's in that unit; no second copy (5.7b-2c).
- **Unit words** — the words, the same in every domain, that name a unit beside a KPI's words:
  a percent word inside them or right after ("churn rate", "%"), a count word right before
  ("how many"); "run rate" is money. A KPI named in a clashing unit (money vs percent or count,
  count vs percent; a number clashes with none) is not answered: intake asks which was meant.
- **Scale** — how a rate role's numbers are written: percent (20 = 20%) or fraction (0.2),
  declared per header (`Role.scales`: Stripe's `percent_off`). An undeclared header is read
  from its values only when that is unambiguous (fractions 0–1, whole percents); decimals over
  1 (12.50: a percent or an amount?) and only 1s are unreadable, left blank and said.
- **Spell words** — the pack's words for a spell and its ledger's sentences (`Spell.words`:
  start, end, singular, plural, the bridge, monthly churn, each move's How). Shared code never
  says them; a pack with moves must give them.
- **Pack column** — a column the product adds to the data at ingestion from a pack formula (a
  subscription list's MRR from price, seats, discount and interval), read by every agent like
  an uploaded column, and named in the source notes.
- **Typed claim** — a report statement the model gives as data, never as prose (a report
  statement, not an insurance claim): a cause (a cause, an effect, its evidence), a benchmark
  verdict (a metric, a verdict, a source) or a comparison (a metric, two named values, its
  evidence). The product checks it against the evidence and writes its sentence.
- **Causal claim** — one thing made another happen. Said as a cause only on a controlled test's
  evidence; on any other evidence it is said as an association; with no evidence, not said.
- **Benchmark verdict** — a KPI's level judged against an outside norm (healthy, best-in-class,
  top quartile, a weakness). The data holds no norm, so the value is said without a verdict.
- **Free text** — report prose the model wrote outside a typed claim. It may state no cause and
  no benchmark verdict; a sentence that does (an overreach) is removed and the report says so.
  Arithmetic of parts ("churned MRR fell because 3 accounts left") and investigations ("to see
  what drives churn") are no overreach.
- **Bridge** — a level's opening, each move over the window (signed: falls negative) and the
  unsplit rest, adding up to its closing level.
- **Series key** — the role naming whose time series a row belongs to (a sensor, a machine): the
  per-series checks (a repeated timestamp, a gap, a value stuck for hours) read one series at a
  time, never across them.
- **Invalid reading** — a measured value its own quality flag marks as not a measurement (a
  historian's Bad): blanked like a sentinel, the row kept and counted in the log; never averaged.
- **Source** — where a table comes from: a file, a share link, a database, a Google Sheet, an API (later
  a SaaS import). A database engine is a **dialect** of the one SQL source, never a source of its own
  (ADR-0010).
- **Dialect** — a database engine the SQL source speaks (postgres, mysql, mariadb, mssql; sqlite in
  development). Its row in `DIALECTS` names its drivers, URL keys, read-only and timeout statements; an
  engine without a row is refused. MySQL vs MariaDB is decided by the server, not the URL.
- **Read-only login** — SQL Server's read-only layer: it has no read-only session, so a login that can
  change data is refused when the session opens. On Postgres and MySQL the read-only role is
  recommended (the transaction is read-only anyway); on SQL Server it is required.
- **Share link** — a public URL to one file or sheet (Google Sheets/Drive, OneDrive/SharePoint "anyone"
  links, Dropbox, any https CSV/xlsx). Its URL is a credential: never stored, logged or echoed.
- **Live query / snapshot load / import** — a question answered by read-only SQL in the user's database;
  a named table loaded whole into the pipeline (capped, the cap disclosed); a copy kept and refreshed on
  demand: a share link imported once as a dataset version (paste again for a newer copy, 5.8b), a SaaS
  import (5.8c).
- **Import** — a user's link to one vendor account (Stripe, Shopify, HubSpot): its key and its tables,
  each table a dataset of its own.
- **Refresh** ("Get latest data") — one fetch of every table of an import; each table that arrives whole
  becomes a new version of its dataset. One table per question: tables are never joined.
- **Last good copy** — a table's newest version from a whole fetch; questions read it.
- **Stale** — a table whose last refresh failed, timed out or was interrupted: shown with its last good
  time and the reason, never as fresh.
- **Cut** — a table capped at its row limit: the newest rows kept, the first date kept said, and an
  answer over earlier dates called incomplete. Never a fetch that ran out of time (that is stale).
- **Date window** — a span the user chose for an import to fetch ("the last 12 months"), said on its
  card. A subscription is in it when it was in force at some time inside it, whenever it started.
- **Minor units** — an amount in a currency's smallest unit (cents). Imports show the major unit, as the
  vendor's own export does, by the vendor's own currency list.
- **Instant vs date** — an instant is a moment (an order placed at 14:03 UTC); a date is a calendar day
  with no zone. An instant is shown in the zone the vendor's own export uses.
- **Checked address** — an address the server connects to only after `resolved()` has unwrapped it (an
  IPv4 address inside an IPv6 one is that IPv4 address) and its host rule passed: public internet for a
  URL fetch (`public_client`), not loopback/link-local/the app's store for a database. The connection goes
  to that address, never to the name again.
- **Allowed function** — a function the SQL parse guard lets a model query call: a typed sqlglot class
  or a per-dialect name on the allow list; anything else is refused by name.
- **Source invariance** — the same table from every source gives the same contract, cleaned frame,
  tiles and built-in answers; one oracle test owns it.
- **Zero-event group** — a group whose ratio has a denominator of 0 (a machine with run time and
  no failures): its numerator is said ("no failure in 1,240 h"), never a 0, an infinity, a rank or
  a silently missing bar.
