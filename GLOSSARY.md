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
- **Small cell** — a reported count of 1–10 patients or encounters.
- **Developing period** — a period year younger than the pack's `maturity.months` at the latest
  valuation date (an accident year whose claims are still being reported and reserved): named with
  its age, never cut and never called final (Domain 4, 2026-11-03).
- **Role caveat** — a pack's caution said whenever the data has that role, on a strong or weak
  match (a protected attribute: described, never a rating recommendation).
- **Marker field** — a column filled only on the rows it applies to (a catastrophe event code):
  its blanks mean none, the file's layout, never missing values.
