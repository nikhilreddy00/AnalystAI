# Phase 5.7, Domain 1 review: sales / retail (the pilot)

2026-11-02, branch `phase-5-7-sales` (from main ab05639). Plan v2 (red-team #1 folded in), approved
2026-11-01. User choices: confirm when unsure (strong vs weak detection); paid gates as planned;
after audit pass 3, "one more round, then cap".

**Bottom line.** Retail now has a domain pack (`domain_packs/retail.yaml`, read by every agent
through `domain_pack.py`), three realistic exports with planted traps, and cases across every
agent. Every paid gate passes on the final code: analysis 21/21 runs (7 cases, k=3), E2E 25/25 at
k=1 (10 re-run after diagnosed fixes), intake 39/39 runs (13 cases, k=3), and insight 5/5. There
were 0 hard-rule violations. The live API + worker run on asyncpg with `APP_ENV=production` works
end to end.

**The audit bar was not met.** The plan's bar is a final audit pass with 0 major and ≤ 2 minor.
The user capped the loop after pass 3. Pass 4 found 2 majors and 5 minors, all fixed, each with a
failing-first test. The paid gates, the live run and the last diff review after them found more,
up to that review's 3 majors and 4 minors (see the ledger). That review's fixes were not reviewed
again. Partial periods and year-over-year logic produced majors in every pass until
the rule was replaced by one coverage rule in pass 3.

## Paid gates (spend $3.60 of the $5 cap; $4.26 with the post-domain browser check)

| run | report | result | cost (est.) |
|---|---|---|---|
| smoke: analysis retail-1/4/5, E2E e2e-38, k=1 | `analysis-20261101T192908Z`, `e2e-20261101T192933Z` | 2/3 + 1/1; found 3 gaps (analysis code ignored the row rules, B2B country per customer, retail-4 wording) | $0.105 ($0.04, **100% over**: per-case cost was underestimated; paid runs paused and the estimator fixed) |
| smoke re-run: e2e-39 (twice), retail-1/4/5, retail-1 (twice) | `e2e-20261102T001025Z`, `…001351Z`, `analysis-20261102T001416Z`, `…001919Z`, `…002103Z` | e2e-39 found a regression of mine (a daily KPI ask compared 31 Jan with 30 Jan); retail-1 found two brief gaps (the row-kind column was not in the schema; emails reach the sandbox as pseudonyms, so the model's own test-order regex missed). All fixed; last runs pass | $0.17 (one $0.026 run was not announced: its estimate equalled the cap, so the cap did not stop it) |
| analysis, retail-2/3/6/7, k=1 | `analysis-20261102T002142Z` | 4/4 | $0.06 ($0.06) |
| **analysis, 7 retail cases, k=3** | `analysis-20261102T002211Z` | **21/21**, flip 0, hard 0 | $0.50 ($0.41) |
| intake, 12 retail + intake-oos-csv-01, k=1 | `intake-20261102T002401Z`, `…002919Z` | 10/13, then 3/3 after fixes ("net sales" named by its own column was no KPI: the store answer said "No segment column specified"; the period note; "sends back" phrases) | $0.19 ($0.12) |
| **E2E, 25 cases, k=1** (15 old sales, 8 new retail, HR e2e-27/28) | `e2e-20261102T002956Z-rescored` | 15/25, hard 0. Four causes, below | $1.26 ($1.11) |
| E2E re-run of the 10 affected cases, k=1 | `e2e-20261102T004453Z` | **10/10**, hard 0, errors 0 | $0.71 ($0.65) |
| insight, the 5 overviews whose context changed, k=1 | `insight-20261102T004752Z` | 5/5, hard 0 | $0.08 ($0.08) |
| **intake final, 13 cases, k=3** | `intake-20261102T004821Z` | **39/39**, flip 0, hard 0 | $0.26 ($0.47) |
| intake-retail-07, k=3, after the last review's "sent back" change | `intake-20261102T011843Z`, `…011951Z` | 0/3 (the definition did not name the KPI key, so the model followed the "sales" note), then **3/3** | $0.07 ($0.02 each) |
| live: upload + overview + "How were net sales in January 2026?" + cancel, twice | traces 2026-11-02 | first run found 3 bugs (below); second run: overview with net sales $310,635.46, January +8.0% year over year, cancel honoured | $0.20 ($0.15–0.20) |

The four causes of the 10 E2E failures, each diagnosed (no case passed "on retry"):
- **e2e-06/07/08/12/13/14, a regression I introduced just before the gate.** After the intake fix,
  "revenue" in a file with a revenue column became the net-sales KPI, so "average revenue for
  segment B" came back as a sum. Now a column's own name is the KPI only when it is the KPI's own
  name. A new $0 test runs that fill-in step over every pre-5.7 sales question. The snapshot pins
  contexts, not that step, which is how this got past the $0 gates.
- **e2e-35/42, a harness crash.** The scorer read the raw export's mixed UTC offsets with
  `format="mixed"`. Fixed (`evals.scoring.any_dates`).
- **e2e-36, a scorer collision.** A "+34.0%" trend fell within the 0.5% tolerance of the
  mean-of-row-margins trap (34.16%). The tolerance is now 0.15%, and the stored run was rescored
  at $0.
- **e2e-37, a model reading.** The model profiled only sale rows. The brief now says column
  profiles describe every row.

The live run found three bugs; the evals' harness answers the approval gate, so no eval saw the
second:
- **Schema-init race.** The API and the worker starting together on a fresh database both ran
  `CREATE TABLE IF NOT EXISTS`, and the worker died. This was there before 5.7. Schema setup now
  holds a Postgres advisory lock, with a test on both drivers.
- **High-null gate.** An overview of the Shopify-style export stopped at the high-null modal for
  Discount Amount, Billing Name and Total. The export writes those on each order's first line
  only, so they are now kept as order-level fields.
- **Report alerts.** They said "Discount amount: 43.7% missing" and "Unit price: 0 extreme
  values". The second, a mild-only flag shown as an alert, was there before 5.7 in every domain.

The live run itself:
- **Reached:** HTTP upload, `/chat`, the Postgres queue, the separate worker, the checkpointed
  graph, SSE replay, cancel, a 409 on retrying a cancelled run, and one active run per session.
- **Not reached:** a retry of a failed run. The 5.6a offline tests cover it.

## "Done when" (ROADMAP 5.7, per domain)

| criterion | evidence | result |
|---|---|---|
| analysis cases pass at k=3 on every run | `analysis-20261102T002211Z` | **met**: 21/21 |
| E2E cases at k=1 (k=3 on failures), hard rules 100% | `e2e-20261102T002956Z-rescored` + `e2e-20261102T004453Z` | **met**: 25/25 after diagnosed fixes. No k=3, because every failure had a found and fixed cause, and the re-run is on the fixed code. Hard rules 100% |
| each agent's expert-rubric checks pass | the cases below (each LLM case has a model-dependent check) | **met** for the cases written; see "Known limits" for what they do not cover |
| final audit pass: 0 major, ≤ 2 minor | the ledger | **not met** (capped by the user after pass 3; pass 4: 2 majors and 5 minors, fixed) |
| README domain list regenerated from the reports | `tests/test_readme_domains.py` (new depth line: every run of the listed reports must pass) | **met** |

## Built
- **The domain pack.** A strict schema (`domain_pack.Pack`, unknown keys refused, cross-checked
  roles, KPIs, caveats, first questions) and `retail.yaml`. Its contents:
  - signals (strong and weak shapes), roles with header variants and value checks;
  - status values, credit prefix, non-product codes, test marker, sentinels, ranges;
  - order-level fields and the week end;
  - KPIs as structured terms (net sales, gross sales, orders, AOV, units, UPT, ASP, gross margin
    %, discount rate, return rate, customers);
  - glossary, consumed phrases, definitions, unsupported concepts (sell-through);
  - caveats, causal trigger words, first questions with needs.
- **The domain-neutral code.**
  - `detect` (strong/weak), `row_kinds` and `with_row_kind` (one row filter for every agent);
  - `compute_kpi` (0 where a kind is absent here but present in the data, otherwise unknown);
    `money_rows` (one money filter for charts and answers);
  - `pack_brief` and `pack_caveats` (conditions in code, words in YAML); `first_questions`;
  - `_cut_partial`, one coverage rule: daily data by its trading weekdays, weekly and monthly rows
    by the next expected date;
  - `year_earlier`/`current_window` (52 weeks on weekly data; whole months against whole months).
- **Every agent.**
  - **Ingestion:** detection, plus mixed-offset dates loaded as local time for a daylight-saving
    pair.
  - **Cleaning:** sentinels, whole-number ids, export-overlap dedup per order, order-level carry
    by layout, flags (return, refunded, cancel, test, non-product, other currency,
    out-of-range), grain, partial months, main currency, order-level blanks.
  - **Intake:** glossary notes, KPI and period parameters, the sell-through ask.
  - **Analysis:** the brief only when the question names a KPI or asks for an overview; the
    row-kind column in the sandbox.
  - **Targeted answers:** KPIs by group and over time, periods, year over year, the stores note,
    the consumer-name refusal.
  - **Visualization:** pack KPI tiles and money trends on the same rows.
  - **Insight:** KPI facts and must-say caveats.
  - **Quality:** promotion-cause and profit-without-cost rules.
  - **Drill-down:** checked answers.
- **Datasets** (`evals/datagen.py`):
  - `retail_lines`, a Shopify-style order-line export with mixed offsets, CAD rows, test orders,
    refunds, voids, export overlap, and fields on the first line only;
  - `store_weeks`, POS store × week × department, with new stores and a 9999 sentinel;
  - `b2b_invoices`, UCI-style, with credits, postage, blank customer ids and a £ price.
- **Cases:**
  - analysis: retail-1..7;
  - E2E: e2e-35..42 (e2e-42 is three turns; e2e-39 is the seasonal trap, January −30% month over
    month and +8% year over year);
  - intake: intake-retail-01..12;
  - cleaning: clean-retail-*, clean-neg-*;
  - ingestion: dom-*, neg-*, including 9 other-domain negatives.
- **Harness:**
  - multi-turn E2E through the chat route's own helper;
  - `--fail-under`, and a CI `zero-suites` job;
  - the snapshot pins the deterministic outputs per dataset (contract, audit, tiles, built-in code
    output, trend chart, targeted answers) against main;
  - `estimate_usd` from the newest full report, with conversation turns priced;
  - `lint_case` rejects unknown check kinds.
- **Frontend:** pack KPI tiles, with the context and the filter note; the dashboard recounts only
  row-count charts; one Playwright spec on recorded SSE.

## Bug ledger by pass

| pass | blocker | major | minor | note |
|---|---|---|---|---|
| plan red-team #1 | 7 | 10 | 5 | folded into plan v2 |
| backend review #1 | 3 | 17 | 12 | |
| diff round 1 | 3 | 9 | 2 | |
| diff round 2 | 1 | 9 | 3 | |
| diff rounds 3–4 | 0 | 7 | 3 | |
| smoke run #1 | 3 findings | | | before the audit loop |
| **audit pass 1** (4 reviewers) | 4 | 28 | ~20 | |
| diff review of pass-1 fixes | 0 | 6 | 7 | |
| **audit pass 2** | 1 | 5 | 7 | |
| diff review of pass-2 fixes | 0 | 7 | 3 | partial periods rewritten to one rule |
| **audit pass 3** | 0 | 6 | 8 | user: one more round, then cap |
| **audit pass 4** (final, capped) | 0 | 2 | 5 | all fixed |
| smoke re-runs | 3 findings | | | one regression of mine (daily KPI), two brief gaps |
| paid gates (intake, E2E) | 7 causes | | | one regression of mine (KPI fill), one harness, one scorer, model guidance ×4 |
| live run | 3 findings | | | two were there before 5.7 |
| diff review of the gate/live fixes | 0 | 3 | 4 | 3 majors and 3 minors fixed (one with a paid re-run of intake-retail-07); one minor kept as a stated deviation (order-level layout on weak matches) |

Bugs came from three areas:
- **Partial periods and year over year.** Every pass until pass 3 replaced the rule.
- **The causal and profit regexes.** Heuristic; tuned with negative cases every pass.
- **Deterministic post-model steps that no $0 gate covered** (intake fill-in, the approval gate).

## Deviations from the plan
- **Detection is strong or weak (user's choice), not one threshold.** A weak match changes
  nothing by default. It gets a "read as" hedge line, and a KPI the user names. The one exception
  is the order-field carry, and keeping order-level blanks out of the high-null gate, which run
  on the file's own layout evidence. A real Shopify export can detect as weak (audit pass 1, B2).
- **B7 (two signals).** Retail needs a retail-specific signal (an item or quantity) plus money;
  bank, HR, insurance, logistics and SaaS negatives stay unmatched.
- **Generic changes, all gated to a pack match except two fixes.** The schema-init lock (all
  processes) and "0 extreme values" no longer alerted (all domains). The snapshot shows every
  other domain's built-in code output, charts and answers are unchanged from main.
- **e2e-39 is the open question.** "How were net sales in January 2026?" is the trap: an unasked
  same-month-last-year comparison on strong retail data. The explicit wording is
  intake-retail-05.
- **Intake KPI fill-in is deterministic.** It is scored after the fill-in by design. Each intake
  retail case keeps a model-decided check (grouping column, period, compare, or a KPI the
  glossary does not name).

## Known limits (stated, not fixed)
- **Currency and calendar.**
  - Other currencies are excluded, not converted.
  - No 4-5-4 calendar or 53-week years.
- **Data a sales export doesn't carry.**
  - Sell-through and other inventory KPIs need stock data (intake asks).
  - Partial refunds without a refund-amount column count as full sales.
  - Recent return rates are understated (returns arrive late); the window is not adjusted.
  - A dated stock count reads as sales.
- **Formats not recognised.**
  - Square's alphanumeric transaction ids are not order ids.
  - Only English headers are recognised.
  - Amazon, NetSuite, WooCommerce and Walmart exports are not detected.
  - UCI non-product codes (M, S, D, B) could hit a size-coded SKU.
- **Dashboard.** Pack KPI tiles are not recomputed under dashboard filters (marked on the tile).
- **Weak matches.** Expense or fuel logs can read as weak retail; that is only hedged.
- **Drill-down and time zones.**
  - Drill-down charts average all rows.
  - A US chain across time zones is read in UTC.
- **Stores.** Store openings are noted on KPI answers, not on overview growth claims (no cleaning
  flag).
- **Scorer.** It compares instants per cell (aware) or local times (naive).
- **Model-dependent.**
  - Intake defaults a time series to monthly, so "How were net sales in Q4?" usually gets a
    monthly trend, with each month against a year earlier, rather than the Q4 total.
  - The narrative can label columns oddly: the live run's report called Billing Name "Billing
    order number".
- **E2E gate.** It ran at k=1. Ten cases were re-run after fixes; none were run at k=3. The last
  review's fixes (high-null on first lines, the checkpointer lock, "sent back") were checked
  offline and by the intake-retail-07 re-run, not by another E2E or live run.
- **Daily KPIs.** A KPI asked by day is shown by month, and a short window by its total.
  Daily flash reporting is not offered.
- **Harness.** A crash in the harness after the pipeline ran stores no output, so it cannot be
  rescored and needs a paid re-run (e2e-35/42).

## Post-domain audit and browser check (2026-11-02, after sign-off; $0.66)

Before banking the user asked for a senior audit (repeated bugs to their root cause; code reusable
by the next packs without changing retail) and a real browser run. One read-only audit reviewer
tried a toy second pack beside retail. Then I opened the app in Playwright at 390 px (real API +
separate worker, asyncpg, `APP_ENV=production`, frontend on a mocked Supabase session with a real
HS256 token), uploaded three files and read every number against the generator's truth.

| upload | checked | result |
|---|---|---|
| `retail_lines` overview (twice) | tiles vs truth: net sales $310,635.46, orders 2,272, AOV $136.72, units 6,548; partial Feb, refunds, voids, CAD, test orders said | all match. Run 1 led with "January −30% MoM" (the seasonal trap): no YoY was built. Fixed; run 2 says "up 8.0% YoY but down 30.0% MoM" |
| `b2b_invoices` overview + "average basket value by country?" | net sales £277,046.81, return rate 2.6%, 119 credits netted, 266 postage lines out; AOV by country recomputed from the raw file | all match (UK £293.92, Germany £125.44 to the penny) |
| `shipments` (logistics, no pack) overview | no retail wording, tiles or caveats; on-time 79.5% (truth 0.795), distance 851.24 km | no leakage |
| dashboard page | `/dashboard/latest/data` | 500 before the fix, 200 after |

**Bugs found and fixed** (each with a failing-first test; snapshot: every non-retail dataset
byte-identical):

| # | sev | where | bug | root-cause fix |
|---|---|---|---|---|
| 1 | major | `api/routes/dashboard.py` | NaN/inf floats made the dashboard data a 500 (every domain) | `json_rows`: object dtype, inf → null |
| 2 | major | `analysis_agent` built-in trend | MoM only: the overview led with a seasonal drop | under a pack, built-in YoY beside MoM (replaces the model's) |
| 3 | major | `analysis_agent.build_evidence` | an order-level field was "44% missing"; the report asked to fix the export | the order-level decision says "one value per order" |
| 4 | major | `domain_pack` + agents | shared code read retail role names (`quantity`, `order_id`, `"date"`, `cost`, `store` …) and retail words | pack fields: `negative_is_return`, `order_key`, `item_roles`, `line_weight`, `cost_roles`, `location_role`, `row_labels`, `row_flags`, `text`, a required date role (inert when absent) |
| 5 | major | `domain_pack.detect` | two matching packs = no pack, silently | strong beats weak; a tie is traced `domain_ambiguous` |
| 6 | major | `quality_agent` | margin talk flagged under any pack (banking's "net interest margin") | the profit rule needs the pack's `cost_roles` |
| 7 | major | guardrails / insight / drill-down | three sentence splitters, two hedge lists (repeated-bug class) | one `_SENTENCE_RE`, one not-asserted set; the profit rule no longer skips "before" |
| 8 | major | analysis + charts | "units sold rose 26.6%" included postage lines; the Units tile doesn't | additive count roles use the KPI rows (`domain_pack.counted`) |
| 9 | minor | `guardrails._label_hits` | "partially refunded" read as group "refunded" (false review flag) | `_`/`-`/space match; a label inside a longer one is not named |
| 10 | minor | `insight._clean_for_executive` | "Order Order Total"; "net sales total" → "Order Total" | one rename pass; plain-word names only as written |
| 11 | minor | `first_questions` | its own 13-month rule (repeated-bug class) | the shared `utils.cut_partial` |
| 12 | process | evals | post-model steps had no $0 replay (repeated-bug class) | every suite stores model replies; `tests/test_replay.py` replays them through `RecordedClient` |

**Repeated-bug classes, root causes now closed:** one coverage rule (`utils.cut_partial`) for
answers and first questions (the sandbox mirror stays pinned to `partial_last_month`;
`year_earlier` keeps its 7-day window slack, documented); one sentence rule and one not-asserted
set for every claim check; a $0 replay of stored replies (fills from the next paid intake or
insight run).

**Left for banking (schema, not retail):** a balance is semi-additive (`_asked_agg` sums any
money role on a "mean" question) — add a pack-level role flag first; drill-down grounding is still
gated to strong matches; `evals/run.py` exempts `domain: retail` E2E cases from hard rules
(predates 5.7). The audit bar for this addendum is the snapshot + full suite + the browser check,
not another audit pass.

## Desktop browser check and the domain regression gate (2026-11-02, $0.28; total $4.54)

The user asked for a desktop (not phone) check that a senior sales analyst would find useful,
and for a permanent guarantee that building one domain never breaks another.

**CI had been red since 2026-11-01 (19 commits, merged to main).** Only on Linux: healthcare's
segment answer ranked tied groups ('MRN-…' at 11.00) by numpy's unstable sort, which orders ties
differently on Linux and macOS. No one checked `gh run list`. Root fix: `utils.ranked` (by label,
then stable by value) for every ranking; the answer now says when groups tie and names the real
lowest group (it named the 20th shown). CI is green again.

**Desktop check (1440 px, live API + worker, asyncpg, production):**

| what | result |
|---|---|
| retail overview: tiles | net sales $310,635.46, 2,272 orders, AOV $136.72, units 6,548, return 3.9% — match truth; the net sales value was cut off at 5 tiles → fixed |
| retail overview: charts | had no net sales by month or by product (the first two charts a sales analyst reads) → added (`pack_overview_charts`, pack field `overview_groups`) |
| follow-up "January 2026 vs January 2025" | +8.0% YoY, $25,545.94 vs $23,653.70 — matches truth; the report panel was empty (no chart) → two-period bars, and any chartless answer shows its text |
| suggested questions | asked about "43.7% missing Financial Status" (first-line-only layout) → `domain_pack.layout_blanks`, the one rule cleaning uses too |
| Dashboard page | KPI strip, net sales charts, filters render; earlier the data endpoint 500ed (fixed) |

Left as minor: chart tooltips show unformatted money; every other bar label on long product
lists; the panel heading says "Segment comparison" for a period question (the model's intent);
the intake schema summary still states raw null shares (fixing it changes retail intake contexts:
needs a paid intake re-run, left for the banking session's budget).

**The gate (permanent, in ROADMAP 5.7 and CLAUDE.md):** the rolling golden
`evals/snapshots/golden.json` pins every dataset's no-LLM outputs and every paid case's context
for all domains (the old pre-5.5/pre-5.7 baselines missed every retail case); a mutation of one
retail label fails it naming exactly the retail cases. `tests/test_one_definition.py` guards the
rules that kept recurring (rankings, sentence splitting, role names in shared code).

## What to carry into the next domain
- Put every deterministic step that runs after the model under a $0 regression check over the
  old cases. The intake fill-in is the one the snapshot missed.
- Run the live API + worker before the paid E2E gate. The harness answers approval gates, so
  only a live run sees a gate that shouldn't fire.
- Write partial-period and comparison rules once, by coverage, before the first audit, not
  patch by patch.
- Set the estimate cap below any amount you don't intend to spend: an estimate equal to the cap
  runs.
- Open the real app in a browser at phone width and read the numbers against the generator's
  truth before calling a domain done: the gates passed, yet the browser found a seasonal headline,
  a false "missing" claim, a 500 and a label bug.
- Check CI before every merge. A red main hid a platform bug for 19 commits.
- The browser check is at desktop width and includes one upload of every finished domain.
- A new pack declares its layout (`order_key`, `negative_is_return`, `cost_roles` …) in YAML;
  shared code must never name a role. `tests/test_second_pack.py` is the guard.
