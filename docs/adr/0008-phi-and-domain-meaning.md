# ADR-0008: Patient data as a de-identified working copy; domain meaning in YAML for flat files

Status: accepted (Phase 5.5, 2026-11-01)

## Context
Phase 5.5 adds seven domains to the evals (banking, healthcare, insurance, HR, SaaS, IoT,
logistics). Two gaps were in the way: E2B (a third party) received every column, personal data
included; and every numeric column was summed by month, so balances, MRR and sensor readings came
out wrong.

## Decision
1. **E2B never receives a detected personal value.** Every value of a column detected as personal is replaced by a
   one-way pseudonym, `ID-` + 64 bits of HMAC-SHA256 keyed from `ENCRYPTION_KEY`, the user and the
   column (`guardrails.pseudonym`). Columns and headers stay, so counts and group-bys still work.
   E2B's results get the names back on our side only.
2. **Patient data is a de-identified working copy** (`agents/ingestion_agent.prepare_frame`,
   decided by one function, `phi_plan`). It turns on when a column is an MRN or patient id, a
   diagnosis-code column holds ICD-10 codes, or a health header sits next to personal data. It is
   an allowlist, so it fails closed:
   - Kept: numbers, dates that aren't personal, short categories, and ICD codes (generalised to
     their 3-character category past 50 codes).
   - Pseudonymised: every identifier.
   - Generalised: ZIP becomes ZIP3 (000 for restricted areas), and ages over 90 become 90.
   - Pooled: groups under 11 rows.
   - Withheld: personal columns, free text, names under any header, and anything that follows the
     patient one-to-one.

   Each step is an audited decision at the head of the cleaning audit. The guard (the client's
   tokenizer) holds every withheld value, so a name the user types never reaches a model.
3. **Domain meaning lives in YAML** (`semantic_layers/`, plus `SEMANTIC_LAYER_PATH`). A layer
   table whose columns are all in an upload gives that upload its aggregations:
   - `SUM` means a sum, `AVG` means a mean.
   - `non_additive_dimension` means a level: each entity's latest snapshot, then summed.
   - It also gives units.

   The analysis built-in trend, charts, targeted answers and KPIs share one rule for each
   (`agents/utils`: `effective_agg`, `aggregate`, `latest_rows`, `time_axis`). Layers are validated
   when the API and the worker boot.

## Limits (stated, not hidden)
- **Not HIPAA de-identification.**
  - Exact admission and discharge dates are kept.
  - Two-way and month × group cells can still fall under 11.
  - Headers in other languages (`paciente`, `fecha_nacimiento`) aren't recognised.
  - A name inside free text under a kept category column (a label that repeats) isn't found.
  - Identifiers written as column headers (a pivot) aren't masked.
- **Pseudonyms change when `ENCRYPTION_KEY` rotates**, and in-flight runs then fail closed.
  `01001` and `1001` are different values. There is no reverse map outside a run.
- **Detection outside patient data** is by name and by value: a name under an unrecognised header
  (`Resident`) or a phone stored as a bare integer isn't detected there, so it reaches E2B and the
  model as written (patient data withholds such columns).
- **Record numbers under 6 digits** aren't put in the guard (they would tokenise every year and
  count in a prompt): a short MRN the user types reaches the model as written.
- **Dates of birth outside patient data** reach E2B as birth years (ages computable, the day not);
  on patient data they are withheld. Yes/no outcome columns are never pooled (pooling the rare
  side would erase it), so a rare outcome can be a one-way cell under 11.
- **One-way small cells**: ZIP3 groups and a cleaning "Unknown" group created after pooling can be
  under 11.
- **The literal rewrite before E2B** maps whole literals compared against a personal column only.
  A partial match (`str.contains('Smi')`) reaches E2B as written.
- **Vector memory (Qdrant)** stores the question and the summary as text, unencrypted. On patient
  data these hold no record values, but the user's own words are stored.
- **Logs** are scrubbed of the run's guard values and PII patterns, at about 5 ms per line with a
  200k-value guard. In the API process outside a run, only patterns are scrubbed.
- **A level** needs its entity and time columns. If cleaning drops one, its total is withheld,
  never summed.
- **Domain support is measured with the sample layers.** A file with other column names gets no
  hints, so levels are then summed as before.
- **Upgrading** changes the fingerprint of some uploads (leading zeros now kept, PHI mode on).
  A run checkpointed before the upgrade fails closed on resume (`DataChanged`); ask it again.
- **`SEMANTIC_LAYER_PATH`** must be mounted into both containers; an unreadable path stops boot.
  A layer with a `non_additive_dimension` metric whose primary key has no entity column besides
  the time column (valid before 5.5) now fails validation: add the entity to the key.
- **Trends run over the data's first time axis** unless a layer names a level's own time: HR data
  with `hire_date` first trends over hires. A `window: first` level is the first snapshot per
  period over time, and each entity's first ever across groups.
- **On patient data, exact duplicates are judged on the raw rows** before cleaning normalises
  whitespace and case, so near-duplicates differing only in those survive there.
- **KPI entity names** fall back to a keyword in the headers ("users" from `active_users` on
  account-month rows) when no single row-unique id names what a row is.

## Consequences
- The 46 retail analysis contexts are byte-identical (`tests/test_suite_inputs_unchanged.py`).
  E2B receives pseudonymised customer emails and names, and every scored field is unchanged
  (`test_old_cases_unaffected_by_pseudonyms`).
- Domain claims (README) are per domain and backed by `by_domain` in the committed reports.
