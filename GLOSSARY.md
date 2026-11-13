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
  as-of is that month's end, never after today.
- **Window** — the span a flow or rate reads: (its start, its end]; the 12 months to the as-of,
  or an asked period, cut to the shorter span since the first date the data knows a state
  (snapshots: from the first snapshot, its month-end included; spells: the day before the first
  end, since a list of recent leavers misses who left before it, or before the first start when
  nobody ended). Its month-ends: a span of exactly N months (the 12 months to 24 Oct) has N, the
  end's own month ending at the end; any other span has one per month it touches (since 15 Mar
  keeps 31 Mar). Moves start at the first known state.
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
- **Pack column** — a column the product adds to the data at ingestion from a pack formula (a
  subscription list's MRR from price, seats, discount and interval), read by every agent like
  an uploaded column, and named in the source notes.
