# Phase 5.5 review: domain coverage

2026-11-01, branch `phase-5-5-domains` (from main 5eca99a). Plan v4 (3 red-teams), design `docs/adr/0008-phi-and-domain-meaning.md`.

**Bottom line.** All eight domains pass their analysis and E2E suites, with 0 hard-rule violations and PHI recall 100%. The final audit bar (a pass with 0 major and ≤ 2 minor findings) was **not met** in the 3 allowed passes. After that, two narrow regression reviews and the paid smoke run each still found majors. All findings are fixed, each with a test that fails without its fix. The last fixes (7464d9d) were not reviewed independently.

## Paid gates (spend $4.37 of the $5 cap; no run over its estimate)

| run | report | result | cost (est.) |
|---|---|---|---|
| live HTTP ask, WP4 (healthcare + banking) | traces | worked end to end on API + worker, asyncpg, `APP_ENV=production` | $0.70 |
| analysis smoke: health-1, ins-1, saas-4, k=1 | `analysis-20261101T150025Z` | 0/3; found the two product gaps below | $0.08 ($0.10) |
| smoke re-runs after the fixes | `analysis-20261101T151310Z` and next | health-1 1/1, ins-1 1/1 (saas-4 1/1 on rescore) | $0.07 |
| analysis, all 68 cases, k=1 (prompt v8 + question in context: the 46 old cases re-measured) | `analysis-20261101T152434Z-rescored` | **68/68**; numeric 100%, recall 100%, false findings 0%, hard 0 | $1.01 ($1.65) |
| E2E, all 34 cases, k=1 | `e2e-20261101T152850Z-rescored` | **34/34**; hard 0, errors 0, PII guard hits 0 | $1.51 ($1.78) |
| analysis, 22 domain cases, k=3 | `analysis-20261101T153341Z-rescored` | 22/22 by majority, 20/22 on all 3 runs; hard 0 | $1.01 ($0.98) |

- No E2E case failed, so there was no k=3 E2E re-run.
- The two cases short of 3/3 are iot-2 and saas-4, at 2/3 each. In both, the model reported a std or a median under `aggregation: "mean"`. The parser now takes the statistic from the label. The 6 stored runs, re-parsed through the new parser, all pass with 0 false findings. This was checked on the stored outputs; there was no new paid run.
- E2B sandbox time is billed separately and is not in the $5.

## "Done when" (ROADMAP Phase 5.5)

| criterion | evidence | result |
|---|---|---|
| each listed domain passes its suite at the analysis/E2E targets | the three reports above | **retail, banking, healthcare, insurance, HR, logistics**: every run. **IoT, SaaS**: by majority (one case each at 2/3, cause fixed and re-parsed) |
| PII/PHI recall ≥ 95% on the healthcare set | `evals/reports/ingestion-*` ($0, deterministic) | **PHI recall 100%** (32 planted columns), over-masking 0, flag accuracy 1.0 |
| README lists exactly the domains that passed | `tests/test_readme_domains.py` | the README's two lists are derived from the committed reports; the test fails on a wrong list |

**Per domain** (analysis k=3; E2E k=1). "Model-dependent" counts cases the built-in code alone does not pass. Most domain cases pass on built-in code, so the evidence about the model is thin: 1–2 cases per domain.

| domain | analysis cases | all runs | model-dependent | E2E | hard |
|---|---|---|---|---|---|
| retail (46 old cases, k=1) | 46 | 46 | 20 | 20/20 | 0 |
| banking | 3 | 3 | 1 | 2/2 | 0 |
| healthcare | 3 | 3 | 2 | 2/2 | 0 |
| insurance | 3 | 3 | 2 | 2/2 | 0 |
| HR | 3 | 3 | 1 | 2/2 | 0 |
| logistics | 3 | 3 | 1 | 2/2 | 0 |
| IoT | 3 | 2 (iot-2 2/3) | 1 | 2/2 | 0 |
| SaaS | 4 | 3 (saas-4 2/3) | 1 | 2/2 | 0 |

## Built
- **7 domain datasets** with planted truths and traps (`evals/datagen.py`):
  - bank balances (semi-additive);
  - encounters (readmission window, PHI);
  - claims (ratio of sums);
  - employees (censored tenure);
  - SaaS accounts (MRR level);
  - sensor readings (drift; mean, never sum);
  - shipments (Simpson's paradox).
- **Cases:** 22 analysis, 14 E2E and 38 ingestion PHI cases. `by_domain` reporting covers built-in-only versus model-dependent cases.
- **E2B gets one-way pseudonyms for every personal column.** This is the 5.6 carry-over.
- **PHI mode** works on a de-identified working copy:
  - withheld: names, contacts, DOB, notes and patient-level attributes;
  - pseudonymised: identifiers;
  - generalised: ZIP becomes ZIP3, ages are capped at 90, groups under 11 rows are pooled.
- **Hygiene.** Errors carry fixed messages, logs are scrubbed, and `raw_query` is no longer logged.
- **Domain meaning in YAML.** There are 7 sample semantic layers. Matched columns carry their aggregation (level, mean, sum) and unit into the analysis, charts, targeted answers and KPIs.
- **Generic labels.** Currency is an ISO code (from a layer or a `$` seen in the file); non-currency units are formatted as numbers with a suffix.
- **Analysis contract, prompt v8.** A derived rate (loss ratio, readmission, attrition) is a `ratio` segment with a validated snake_case label. The model now reads the user's question, delimited as untrusted.

## Bug ledger by stage

| stage | found | notes |
|---|---|---|
| plan red-teams v1 / v2 / v3 | 27 (5 blockers) / 24 (3) / 25 (4) | each round removed a whole mechanism |
| WP0–1 review | in 02315d2 | scorer fake-pass paths |
| WP2 review (4 reviewers) | 34 | PHI / E2B / failure paths / parity |
| WP3 review (3 reviewers) + automated security review | 23 + 2 | layers, units, SQL masking |
| WP4 live run (API + worker, asyncpg, production env) | 2 | dead asyncpg connection after a client drop (production driver only); summed levels |
| final audit pass 1 (5 auditors) | 1 blocker, 26 major, ~45 minor | |
| final audit pass 2 (5 auditors) | ~9 major + minors | mostly regressions of pass-1 fixes |
| final audit pass 3 (5 auditors) + security review | 13 major, ~28 minor + 4 | **bar not met after 3 passes** |
| regression review of the pass-3 diff (1 reviewer) | 6 major, 4 minor | widened health words; birth measures; id literals; place names |
| automated security review of that fix | 3 | Last-First default; bare `birth`; SQL weak words |
| paid smoke run | 2 product gaps, 1 scorer gap | no way to report a derived rate; the analysis model never saw the question; per-account snapshot means |
| review of that fix (1 reviewer) | 4 major, 4 minor | short / suffixed ids reached E2B unrewritten; `birth_month`; ratio id collision; scorer too lenient |
| analysis gates | 3 product, 4 scorer | labels contradicting the aggregation; `col=value` names; ratio / flag / nunique scoring |

The pattern behind the later majors, now classes 13–20 in the review checklist:
- A fix widens or narrows a rule (`birth\w*`, `hospital\w*`, the health words, the id-literal skip), and the rule then misfires on another domain's data.
- Data arrives in other conventions ("Last, First", lowercase, `PatNo`).
- A scorer does not know a population or aggregation that production emits.

## Deviations
- **The audit bar was missed**, and the user chose the path forward: a narrow review of the pass-3 diff, then the paid gates. Two more fix rounds followed that were not audit passes.
- **The analysis inputs changed on purpose:** prompt v8 and the question in the context. `test_suite_inputs_unchanged` now pins analysis on column roles only. All 46 old analysis cases were re-run at k=1 (100%), not at k=3. Intake and insight inputs are still byte-identical to the 5eca99a snapshot.
- **Scorer changes**, each documented in its commit and applied by rescoring at $0:
  - snapshot level columns may be scored on each entity's current snapshot;
  - a 0/1 flag's `ratio` is its mean;
  - an unpinned check ignores a derived share;
  - `count` of an id column may count entities;
  - `std` is scored;
  - `last` without a level hint is pandas' last.
- **Charts skip `ratio` segments.** A derived rate can't be re-aggregated from the frame. `ponytail:` chart its results when that's needed.
- **From the plan:**
  - diagnosis codes and admit/discharge dates are kept;
  - two-way and month × group cells can fall below 11;
  - **no HIPAA claim**.

## Known limits (not fixed)
- In patient mode, "Delayed, Weather"-style two-word categories and a "City, Georgia" are withheld as possible names. This errs toward hiding data.
- A patient column that is entirely blank counts as "each value follows one patient", so lowercase categories are withheld (fail closed).
- European dot-only thousands ("12.000") are read 1000× too small. This was already the case on main.
- A filtered dashboard chart keeps its original y-axis label over row counts.
- Old sealed reports render long KPI values in a smaller font.
- The healthcare prompt injection can't be measured: patient mode withholds the notes column it is planted in.
- Rescoring recomputes `builtin_only` with today's code.
- The legacy raw cache of a pre-5.5 patient thread stays on disk, owner-only. A resume from it fails closed.

## Verification
- **Offline at 7464d9d:**
  - pytest: 1259 passed, 115 skipped (Postgres tests skip without `TEST_PG_URL`);
  - ruff clean;
  - ingestion, cleaning and visualization suites 100% (PHI recall 1.0, over-masking 0).
- **Postgres:** at 8b6ca9e, 1318 tests passed on psycopg and the store tests passed on asyncpg. Later commits don't touch the stores; CI runs both drivers on every push.
- **Frontend at 8b6ca9e:** tsc and eslint clean, node 6/6, Playwright 34/34. No frontend change since.
- **Live (WP4, before the final audit; fixes in 974a92e):**
  - **Setup:** the API and a separate worker on scratch Postgres with asyncpg, `APP_ENV=production`. Healthcare and banking were asked over HTTP.
  - **Injected failures:** a cancel mid-run, an E2B failure (surfaced as a retryable error) and a Postgres outage (errors surfaced, and the worker survived and retried).
  - **Delete:** it left 0 rows and 0 files.
  - **Found:** a dead asyncpg connection after a client drop (fixed). The worker-SIGKILL and full leak-scan results for this run are not in the record; the PHI-exit tests (`tests/test_phi_*`) cover the exits offline.
  - **After the audit passes:** the code changed later and the live run was not repeated. The E2E gate runs `orchestrator.runner.execute`, the worker's own path.
