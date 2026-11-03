# Glossary (builder terms)

Domain words (ALOS, readmission, NPL …) live in `domain_packs/<domain>.yaml`, not here.

- **Protected working copy** — the copy of a patient-data frame that agents and the sandbox see:
  personal columns withheld, ids pseudonymised, small groups of patient attributes pooled. It is
  not "de-identified": exact service dates stay (see ADR-0008, 2026-11-03).
- **Version chain** — the rows sharing one key (e.g. a claim and line), ordered by a version
  column; only the final version counts, and a chain whose final version is a void counts as
  cancelled.
- **Within term** — a KPI term counting the entities' rows that are followed by another row of the
  same entity within N days of their end; rows whose window runs past the data's last start are
  censored (left out of the count and the base).
- **Small cell** — a reported count of 1–10 patients or encounters.
