# Phase 5.8c — Stripe and Shopify imports, 100 MB uploads (2026-11-23)

Wall clock: about 30 h, three times the 10 h budget (first commit 2026-11-22 14:41; past 20 h in one session
by 2026-11-24 14:55, when the user split the rest into parts; Parts 1–3 to 23:55 that day, 9 h with a 1 h 53 min
gate record; Part 4 on 2026-11-25; the PR's number added at merge). The breaker (5 h before the paid step) was not
tripped: the paid E2E ran at about 5.9 h, after the user cut HubSpot for time. The overrun came after it: audit
pass 1 (six reviewers) and its fixes, the live check, then the gates (three failed mutation baselines, P58-56/57;
the survivors closed in Parts 1–2; CI's first findings in Part 3).

Plan: `~/.claude/plans/phase-5-8c-mighty-wreath.md`. It checks off every plan-mode step by name:
- **brainstorming:** the brief and an engineer ↔ challenger debate on Q1–Q14 (the user delegated them);
- **research:** one background agent on primary sources → `docs/research/2026-11-22-5.8c-sources.md`;
- **domain-modeling:** Import, Refresh, Last good copy, Stale, Cut, Date window, Minor units, Instant vs date →
  GLOSSARY; the ADR-0010 amendment;
- **codebase-design:** one deep module (`connectors/imports.py`: vendors as data, one host-locked client, one
  `refresh`); DESIGN-IT-TWICE on the import mechanism: dlt vs own fetchers;
- **red-team:** one fresh Plan subagent: 5 critical, 7 major, all folded in;
- **grilling:** `/grill-with-docs`, Q1–Q14 by the debate, three scope changes approved by the user;
- **sizing:** 10 h, ≈ $0.30 planned; actual below;
- **stop rule:** at most 2 audit passes; pass 1 done, pass 2 not needed (see Audit).

## Decisions (user)
- 2026-11-22: **no dlt** (its verified sources bypass the guarded client; telemetry; ~25 deps), **no DuckDB**
  (nothing would read it; a second sniffer breaks P58-01), **GA4 deferred** (service-account keys blocked by
  default on new Google Cloud orgs).
- 2026-11-22: the memory structural fix (a page header that disagrees with its footer) → Phase 6.
- 2026-11-23: test accounts Stripe + Shopify now; **HubSpot cut for time**; the SaaS pack fixed now (it didn't
  read Stripe's real exports).
- 2026-11-23: the paid runs' two product bugs (P58-46/47) → a follow-up fix PR before 5.8d, not this one.

## Built
- **Uploads to 100 MB and 25 million cells** (the cap set from measured memory, not the brief's 250 MB): each
  format bounded before it is decoded: text by lines × width, JSON an allow list of record shapes counted as
  flattened paths while read (fail-closed), parquet from its footer, dictionaries and uncompressed size; an
  oversize body refused before multipart by `BodyLimit`. One constant feeds the backend, the UI label and the
  README (`readable_file` is the one reader for uploads, links and imports).
- **Stripe and Shopify imports** (`connectors/imports.py`, `api/routes/imports.py`, the `imports` store on both
  stores): a read-only key tested at connect against each table's own first request; tables filed as parquet
  dataset versions laid out as the vendor's own export (the import oracle compares them to the real exports,
  recorded and scrubbed); one host-locked client over `public_client`; every failure a reason code, never the
  vendor's text; one refresh at a time per import under a row lock, 30 min after a good one, 3 tries an hour; a
  failed, timed-out or interrupted table keeps its last good copy and says why; keys Fernet-encrypted, masked in
  every sink. Stripe: restricted keys only, minor units by Stripe's currency list, one row per subscription item,
  Percent Off added. Shopify: a merchant's Dev Dashboard app (client credentials, a token minted per refresh),
  GraphQL cursor paging, the shop's own time zone, order fields on the first line only, a Test column.
- **The SaaS pack reads Stripe's real exports** (`Role.when`: a header that is a role only beside another).
- **The UI:** a Stripe / Shopify tab with numbered setup steps, the import card (Test data badge, per-table as-of,
  the failure in words, Get latest data, Replace key, Last 12 months only, Delete with or without its tables).

## Metrics
| check | result |
|---|---|
| a 100 MB long-narrow CSV (1.6 M rows), read as the process | 15.4 s, peak 1,249 MB (bounds 30 s, 1,500 MB) |
| 10 bomb shapes past the cell bound | refused in ≤ 1.9 s, peak ≤ 221 MB |
| import oracle (Stripe subscriptions + invoices, Shopify orders) | equal to the vendors' own exports |
| connection matrix per vendor through the route | ok, wrong key, permission, 429, 5xx mid-page, network, host, deadline, interrupted |
| paid E2E k=1 (e2e-105..108) | 2/4: e2e-106 (Shopify), e2e-108 (95 MB at the cap) pass; e2e-105, e2e-107 fail on P58-46/47 |
| live check, 1440 px, production mode | Stripe imported (2 tables, 10 rows each), a revoked key's refresh failed in words with the last good copy kept, a new key mended it, Get latest data: "no changes since the last copy"; the key in no log or trace |

## Gates
- live browser at 1440 px: done (production mode, asyncpg, real worker; headless agent-browser with a screenshot
  per step, `.live/5.8c-0*.png`: the user's Chrome couldn't reach the local app and they chose headless)
- finished domains' filter reads: done (the source oracle over every dataset unchanged; `domain_diff.py main`
  identical on all 35 datasets after the SaaS pack change; the imports' own oracle against the vendors' exports)
- all-domain answer probe: done (golden on all 8 domains unchanged, 143 passed after the import cases left the
  golden: a live vendor's table has no offline input; the imports' built-in answers: P58-46/47 pinned Open)
- playwright specs: not done (no import-tab spec yet; the follow-up PR adds it with P58-51's card states)
  The live check's screenshots stand in meanwhile.
- full suite (`scripts/suite.py`) at a13582e with every test database up: 5008 passed, 10 xfailed, 0 failed.
  The first suite and CI found five things, each fixed at its cause (Part 3 of `docs/handoff/phase5_8c.md`)
- mutation gate: recorded. The full `--record` at 716acd2 (1 h 53 min): 1586 mutants, 28 survivors, all 28
  named equivalents, 0 timeouts, 0 no_tests, 36 functions; `_readable_size` and `_read_parquet` re-recorded at
  e6313b8 (106 mutants, 1 survivor, named); `--check main` passes on this branch's head. Before it: three
  baseline runs failed on the matrix's stripe-ok (P58-56), a 2 h 17 min record stopped on a function mutmut
  doesn't mutate (P58-57); the narrow runs' 99 survivors → 58 killed by tests, 37 named, 4 gone with a dead
  codec line; 6 timeouts were 5 real infinite loops on a repeated or missing Shopify cursor
  (now refused as `schema`) and one throttle wait (0 in tests)
- store tests on psycopg and asyncpg: 26/26 each
- CI: green on a13582e (run 45547570417, dispatched); on the PR: see the PR

## Spend: $0.10 of $2.50 (ask $2.20, stop $2.35)
E2E k=1: e2e-105 $0.03 (a harness error) + $0.021, e2e-106 $0.021, e2e-107 $0.020, e2e-108 $0.011. The live
check made no model call ($0). No audit fix touched what reaches the model.

## Deviations
- Cap 100 MB, not ~250 MB: measured memory (the long-narrow file peaks at 1.25 GB at 100 MB).
- No dlt, no DuckDB, GA4 and HubSpot not built (decisions above).
- Shopify pages GraphQL, not Bulk Operations (bulk access lapses after 31 days unused, and the recording's token
  was refused it); a Dev Dashboard app can't hold `read_all_orders`, so 60 days of orders, said in the app and
  the README.
- Stripe: KWD not recorded (the account can't price in it; ISK and HUF instead); Percent Off added (the export
  names a coupon by id only).
- Shopify: Outstanding Balance dropped (export and API disagree); Source kept as text; the retail pack doesn't
  read the Test column yet.
- Open, pinned xfail-strict for the follow-up PR: P58-46 (subscription item rows counted as subscriptions),
  P58-47 (money summed across currencies), P58-51 (a table's stale / cut / empty state never reaches the
  analysis).
- Phase 6: the parquet page-header ceiling, the memory structural fix, a process-wide refresh queue (five imports
  per user meanwhile), imported copies plaintext on disk like uploads.

## Audit
Pass 1 (2026-11-23), on `main...HEAD`: a senior audit (normal inputs, real failure modes, a probe run on the
fixtures), then `code-review` (high), `silent-failure-hunter`, `pr-test-analyzer`, `security-review`,
`type-design-analyzer`, and a simplification pass. Findings: 2 majors from the senior audit (a deleted or swept
import table broke every refresh; an empty refresh left old rows as current), the refresh that kept running after
its import was deleted or re-keyed (four reviewers), the retention sweep and a split version on Postgres, seven
silent failures, the fixed-date window, the import count. Each got a red test, a fix at its cause and a ledger row
(P58-50, 52–55), except the empty / stale / cut state, pinned Open (P58-51). The automated commit review then
found the re-key rule could reset the hour's limit: fixed twice (a re-save, then a key swap), the second fix's
first draft committed red (P58-32). Not done: semgrep, zizmor, actionlint (not installed), gitleaks and pip-audit
(CI's security job runs both). Pass 2 not run: no major left open but the pinned ones.

## Repeat risks
- D1-38: must-accept corpora first: the upload corpus (5 formats) and the vendors' recorded real pages and exports
  before any refusal test — recurred (the SaaS pack had never read a real Stripe export), fixed with `Role.when`
- P58-01: imports go through `QueryResult.frame` → parquet → the one reader; the import oracle — held
- P58-02: exact values (minor units by Stripe's list, Shopify decimal strings) — held
- P58-03: order-free compare; Shopify same-second orders tie-break on id — held after the oracle found it (P58-43)
- P58-05: no new SQL path, no DuckDB — not engaged
- P58-06: keys registered at decrypt, the minted token at mint; canary sinks; the live logs — held
- P58-07: one host-locked client; the shop's address canonical before use — held
- P58-09: columns by position (Shopify's equal names) — held
- P58-13: the vendor's hosts only, resolved public — held
- P58-14: each failure its own words through the route (the matrix) — held; DNS said as the network (P58-41)
- P58-17: every import table listed by name — held
- P58-21: the nightly read by job at Step 0 — held
- P58-33: fixtures hold fake `rk_test_` keys; the reports checked for keys before commit — held
- P58-35: an interrupted refresh is derived from its deadline, never swept at startup — held
- P58-36: memory measured as the process in a child — held
- PR9-02: mutation `--record` without `--tests` — held
- D3-10: tests through the route, the store and the fake socket under `public_client` — held
- D1-22: the first paid case alone, then the rest — held ($0.10)
- D3-14, D7-19, D4-21, PR7-04: golden and domain_diff unchanged on all 35 datasets after the pack change — held
- P58-32: never commit after a piped test run — recurred three times, now a hook
- D1-59: the wall clock is in this note — held
- P58-20: Repeat risks and Retro matched to the ledger — this note

## Retro
- Seven security-review rounds on the raised cap each found a reader that measured after it decoded → P58-39 (new)
- The key test read one endpoint, so a key missing Customers passed it → P58-40 (new)
- A host that didn't resolve was said as another server → P58-41 (new)
- Importing `api.main` in tests installed the log scrubber on pytest's handler → P58-42 (new)
- It came back through `test_boot` in the gate's one-process run → P58-42 (recurrence: the first fix moved one importer, not the leak; a conftest fixture now removes it after every test)
- The import oracle found an empty column typed two ways and same-second orders in two orders → P58-43 (new)
- The row cap counted subscriptions and was bound at import time → P58-44 (new)
- The SaaS pack had never read a real Stripe export → D1-38 (recurrence: 5.8b gave must-pass corpora to the guards it touched, never to the domain packs)
- Commits went in red through a piped pytest, three times → P58-32 (recurrence: the 5.8b fix was a habit and a pre-push hook, which stops a push, never a commit)
- The built-in count read item rows as subscriptions → P58-46 (new)
- The built-in segment answer summed every currency → P58-47 (new)
- The recording aid's scrub wiped data, then let a city through → P58-48 (new)
- The eval harness read a case's truth five ways → P58-49 (recurrence: case_truth fixed three readers; the domain-cases test and the golden were two more)
- A refresh kept going after its import was deleted or re-keyed; a new key stayed locked out → P58-50 (new)
- A table's stale, cut or empty state never reaches the analysis → P58-51 (new)
- An import's tables were swept, deleted or split outside it → P58-52 (new)
- Silent failures at the refresh's edges → P58-53 (new)
- "The last 12 months" was a date stored once → P58-54 (new)
- No limit on imports per user → P58-55 (new)
- The gate's baseline failure was fixed twice by guess before it was reproduced in the gate's copy → P58-56 (new)
- The window's first day was the local day in the route and the UTC day in the refresh → P58-54 (recurrence: the m2 fix moved the refresh's rule and left the route's)
- The gate kept its own copy of which functions mutmut mutates and stopped a 2 h record on one it doesn't → P58-57 (new)
- Gate runs lost hours to idle sleep, to `caffeinate` as a wrapper (it strips `DYLD_LIBRARY_PATH`) and to the
  oracle in a narrow run that never finished → P58-58 (new: `live()` holds the Mac awake beside the run)
- The suite ran without the test databases, so two failures showed first in CI → P58-21 (recurrence:
  `test_dbs.sh` starts them only when asked; `suite.py` now names each unset URL)
