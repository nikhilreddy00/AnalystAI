# Ending the recurring bug classes (after Phase 5.7 Domains 1–2)

2026-11-02. Status: **proposal v2 (red-team folded in), awaiting approval.** No code changed yet beyond the one-off
`utils.pct_change` fix on branch `fix-final-banking-audit`.

## Why this document exists

Two domains (retail, banking) cost about $8.57 in eval spend and four to five audit passes each.
Most of that went on bugs of the **same five kinds**, found again in a new place every pass. The
final banking audit (2026-11-02, $0) found live instances of four of them still on main. Six
domains remain. If nothing changes, each domain will find the same classes again in new call
sites, at the same cost.

A senior engineer's first question about a bug that keeps coming back is not "where is this
one?" but **"why is it possible to write this bug at all?"** The aim of every fix below is to make
the class impossible (one place owns the rule), or caught at $0 before a paid run (an oracle that
runs every path against every dataset). It is not to find the next instance faster.

The principle under all five fixes:

- **Behavioural catches (reviews, audits, more regex guards) are linear in vigilance: you pay for
  them every domain, forever.** Structural fixes, which remove the condition that lets the bug be
  written, are paid once ([Lostsource](https://pete.lostsource.net/posts/2026-06-13-dont-catch-the-bug-remove-the-condition.html);
  [make illegal states unrepresentable](https://deviq.com/principles/make-illegal-states-unrepresentable/)).
- **Parse at the boundary, once.** Turn untrusted input (a CSV upload) into a typed object that
  carries its meaning, and let nothing downstream re-derive that meaning from raw data
  ([parse, don't validate](https://deviq.com/principles/parse-dont-validate/)).
- **Where two implementations must exist, make one the other's oracle.** This is differential
  testing: run both on generated inputs and fail on any disagreement
  ([differential testing](https://arxiv.org/pdf/2212.01748)).

## The five classes, with evidence

| # | class | occurrences (D1 + D2) | live on main now | current guard |
|---|---|---|---|---|
| 1 | Which rows a number reads | ~22 | drill-down chart averages paid-off $0 loans (Harbor $47,795 vs $48,462); built-in level-by-segment counts stopped accounts ($40.7M vs $30.8M); newest-first same-day ties; other-currency accounts dropped from a count (47 vs 120 open accounts); a monthly series counts closed accounts | regexes per seen instance |
| 2 | Sandbox code is a second copy of the Python rules | ~9 | built-in level breakdown has no stopped-account cut (documented as accepted); the golden runs it without the row-kind column, so those branches are never exercised | none |
| 3 | Exact-vocabulary detection, and a generic path that guesses | new in the final audit; status spellings recur in both domains | abbreviated loan-tape headers → no pack → 9 month-ends summed ($12.9M vs $1.32M), "NPL ratio" answered as "sum(Account)"; a bank statement's daily balances summed ($853,664 vs $34,839); Debit/Credit indicator ignored (net flow $1.17M vs $181k); false high-null prompt on a Debit/Credit statement | none |
| 4 | Retail as the base vocabulary | ~9 | banking intake prompt says "return rows…"; "stores opened" fires for any pack with a location role | role names only |
| 5 | Output depends on the machine | 3 | none known (each instance fixed); `value_counts().head(N)` ties not covered by the ranking guard | instance-only |

About 118 raw `groupby/sum/mean/median/corr/agg/value_counts/nunique` calls sit in the agents:
68 in visualization, 27 in analysis, 21 in targeted answers, 2 in drill-down. On top of that are
a 167-line sandbox code template and a TypeScript re-aggregation in the dashboard. Each one is a
place where class 1 can be written again.

---

## Root cause 1: "which rows does this number read" is decided by every caller

**Senior analysis.** The bug is never in one call site. A measure's meaning is decided where it
is *used*, not where it is *defined*. That meaning covers its grain, its row filter (kinds,
currency, on-book), its behaviour over time (flow vs level), and its statistic (total, mean per
entity, r per entity). The industry learned this the hard way in BI, and the answer is a
**semantic (metrics) layer**:
- define each measure once, with its aggregation type (additive, semi-additive with a window,
  non-additive) and filters;
- every consumer asks the layer and never touches raw rows
  ([dbt measures, `non_additive_dimension`](https://docs.getdbt.com/docs/build/measures);
  [AtScale semi-additive measures](https://documentation.atscale.com/container/creating-and-sharing-cubes/creating-cubes/modeling-cube-measures/types-of-cube-measures/semi-additive-measures);
  [single source of truth](https://www.getdbt.com/blog/semantic-layer-introduction)).

We already have half of one: pack KPIs, `ColumnInfo.aggregation` hints, and `semantic_layer.py`
for SQL. But consumers bypass it with raw pandas, so five selectors grew (`money_rows`,
`latest_rows`/`level_rows`, `snapshot_rows`, `entity_rows`, the `KPI.at` path), each switched on
under different conditions.

**Approaches.**

| | approach | prevents the next one? | cost | risk |
|---|---|---|---|---|
| A | **One `Measures` API** (`agents/measures.py`): `measure(frame, column_or_kpi, stat, by=None, period=None) → MeasureResult(values, rows_used, how)`. It composes the steps in one fixed order: time order → each entity's latest row (current, or per period) → row kinds and currency (blank a level, drop a flow) → statistic. Every handler, chart, drill-down, dashboard row feed and built-in calls it. A structural rule (below) bans raw aggregation on the uploaded frame outside `measures.py`. | **Yes, by construction**: a new call site cannot pick its own rows | 2–3 days (≈118 sites, mostly mechanical) | migration churn; the golden moves only where a site was wrong |
| B | Keep the five selectors and add a **differential oracle**: for every dataset × measure × group, tile = answer = chart = drill-down = built-in | No, but it **catches** every new disagreement at $0 | ½–1 day | none |
| C | Push all computation into DuckDB SQL generated from the semantic layer (as MetricFlow does) | Yes | 1–2 weeks | large; the sandbox and pandas paths stay for model code |

**Recommendation: A + B.** B is built first, against current code, so it shows today's live
failures in red. It becomes the acceptance test for A, and it stays forever as the oracle that
makes any new consumer prove it agrees. C is not justified at this size.

`MeasureFrame` is the "parse, don't validate" piece. At ingestion and cleaning, the upload is
turned *once* into a frame that carries:
- its row kinds and main-currency mask;
- its snapshot entity, time axis and cadence;
- its level and non-additive columns.

Consumers receive a `MeasureFrame`, not a raw `DataFrame`, so a rule can't be "forgotten": the
information comes with the data.

## Root cause 2: trusted statistics are re-implemented as text for the sandbox

**Senior analysis.** `_build_standard_code` contains no model code. It is our own deterministic
statistics (MoM, YoY, partial-month cut, correlations, level breakdowns), rewritten as a Python
*string* and executed in E2B. Two implementations of one rule will drift. Nine bugs did, and one
drift is documented as accepted. Sandboxing exists to isolate *untrusted* code
([ADR-0004](../adr/0004-sandbox.md)); our own code doesn't need it.

**Approaches.**

| | approach | prevents? | cost |
|---|---|---|---|
| A | **Compute the built-ins in-process with the `Measures` API** after E2B returns, then merge with the model's findings (replace only the same meaning, as today). E2B runs only model code. Deletes ~170 lines of string code. | **Yes**: one implementation | ~1 day |
| B | Ship the real module source into the sandbox (`inspect.getsource`) | Yes, but E2B lacks pyarrow and our deps | 1 day, fragile |
| C | Differential test sandbox vs Python | Catches only | ½ day |

**Recommendation: A.** The model still sees and cites the built-in results as evidence, exactly
as now. They are simply computed by the same function as the tiles and answers.

## Root cause 3: closed-world vocabulary, and a generic path that guesses

**Senior analysis.** Detection matches exact normalized headers against hand-written lists.
Real exports are an open world: "Cur Bal", "As Of", "Txn Amt", "Posting Dt", a "Dr/Cr" flag,
"Paid in Full". There are two failures, and the second is the dangerous one:
- a list miss drops the file to the generic path;
- the generic path **guesses**. It sums a balance across dates, and it answers "NPL ratio" with
  the first numeric column.

A senior engineer fixes both: widen recognition in layers, and **fail closed**. A named measure
the system can't compute is declined, or the user is asked; it is never substituted.

**Approaches** (the layered cascade the column-mapping tools use
([tabularmapper](https://pypi.org/project/tabularmapper/);
[heuristic vs LLM column recognition](https://codenote.net/en/posts/csv-column-auto-recognition-heuristic-vs-llm/);
[Dromo](https://dromo.io/blog/improving-data-import-accuracy-with-dromos-ai-powered-column-matching))):

1. **Normalize and tokenize headers** (NFKC, split camelCase, `#` → `no`, an abbreviation table:
   `bal`→balance, `amt`→amount, `txn`/`tran`→transaction, `dt`→date, `acct`→account,
   `cust`→customer, `prin`→principal). A pack lists **tokens and synonyms**, not whole headers.
2. **Fuzzy match** short headers with Jaro-Winkler ([rapidfuzz](https://pypi.org/project/rapidfuzz/),
   MIT, already the common choice), with a confidence score.
3. **Values decide** the role, as `_fits` already does for money, counts and rates. Add generic
   semantic checks, in the spirit of Sherlock/Sato, which type columns by their values rather
   than their headers ([Sherlock](https://arxiv.org/pdf/1905.10688.pdf),
   [Sato](https://github.com/VIDA-NYU/sato)):
   - a D/C indicator: two values, mostly `D`/`C`/`DR`/`CR`;
   - a running balance: per entity, `balance[t] − balance[t−1] = amount[t]`;
   - a snapshot grain: (entity, date) unique, entities repeating across dates.
4. **Optional LLM mapping** for whatever is still unmapped: one Haiku call with headers plus
   masked samples. It runs only on a weak or ambiguous match, its confidence is shown, and below
   the threshold it asks the user. This is how commercial importers handle the long tail.
5. **Fail closed, generically, with no pack needed.**
   - A snapshot grain (detected from the data) never sums a numeric across dates without saying
     so; levels get the latest-row rule even without a pack.
   - A question naming a concept that no column or KPI provides is declined, never answered with
     `metric_columns[0]`.
6. **Layout blanks by structure, not by list.** One rule: a column whose blanks are explained by
   another column (first line of an order, a status, the other side of a debit/credit pair) is
   layout, not missing. This replaces three special cases with one function.
7. **Status vocabulary by tokens plus data.** "paid in full" contains `paid`+`full` and
   "charged-off" contains `charge`+`off`. Treat a $0 balance with no later rows as closed.

**Recommendation: steps 1–3, 5–7 now** (deterministic, $0 to test). Step 4 behind a flag, after
the oracle shows how much the deterministic layers leave unmapped. The test is a **header-variant
corpus** per domain: realistic header spellings, including public exports such as bank-statement
CSVs and LendingClub-style tapes. A metamorphic rule applies: renaming headers to synonyms must
not change detection or any number.

## Root cause 4: retail is the base vocabulary

**Senior analysis.** This is an internationalisation problem in disguise. Row kinds are named
`sale/return/refunded`, and unfilled text falls back to retail sentences. So every pack that
doesn't override a sentence leaks retail words, and every leak is found by eye. The i18n world
solved this with **message catalogs** and **pseudo-localization**: render everything through a
catalog, then run a fake locale whose strings are visibly marked; anything that shows up unmarked
skipped the catalog ([pseudo-localization](https://generaltranslation.com/en-US/kb/pseudo-localization)).

**Approach.**
- **Neutral internal names:** `counted`, `netted`, `reversed`, `excluded`, `test`, `non_item`.
  Each pack labels them.
- **No domain words in shared code.** Every user-facing sentence is a catalog key. The pack
  validator requires every key a pack can trigger (computed from the pack's fields), so there is
  no silent default.
- **A pseudo-pack test ($0):** a synthetic pack whose every label is a token (`⟦KIND_COUNTED⟧`)
  is run through every producer (tiles, answers, caveats, briefs, alerts, intake notes) on every
  dataset. It fails on any word from a domain lexicon (retail, banking, healthcare…). This catches
  the next leak in any domain, including ones not built yet.

Cost ~1 day. The golden moves only where retail wording changes (expected: none, because retail's
pack supplies its own words).

## Root cause 5: output that depends on the machine

**Senior analysis.** All three instances came from the same policy gap: exact pins taken on
macOS and compared on Linux, with no rule for floats from `exp`/`log` or for tie order. The common
practice ([cross-platform golden tests](https://docs.rs/crate/kshana/latest/source/tests/cross_platform_golden.rs);
[snapshot tolerance](https://archive.linux.duke.edu/cran/web/packages/resultcheck/vignettes/snapshot-tolerance.Rmd))
is: pin exact values on one platform, and compare normalized or rounded values everywhere else.

**Approach.**
- **Pin on Linux:** the golden is verified (and re-pinned) by CI. Locally, the golden check
  rounds numeric leaves to 6 significant digits.
- **Metamorphic invariance tests** (property-based, with
  [Hypothesis](https://medium.com/clarityai-engineering/property-based-testing-a-practical-approach-in-python-with-hypothesis-and-pandas-6082d737c3ee)):
  shuffling row order or reversing tie order must leave every answer, chart and tile unchanged.
  This replaces the `sort_values` regex with a test of the property itself.
- **A datagen policy test:** every float a generator emits has at most 4 decimals.

Cost ~½ day.

## Cross-cutting: enforce structure, not vigilance

- **Fitness functions, not regexes.** Replace the per-instance regex guards with AST or
  [Semgrep](https://semgrep.dev/docs/writing-rules/rule-ideas) rules ("no DataFrame aggregation on
  the uploaded frame outside `agents/measures.py`"; "no string literal in shared code from a
  domain lexicon"), plus [import-linter](https://developertoolkit.ai/en/workflows/test/fitness-functions/)
  contracts ("agents may not import `pandas` aggregation helpers except through `measures`").
  They fail the build, which costs nothing per domain.
- **The oracle harness** (`tests/test_oracle.py`, $0, every commit). For every datagen dataset ×
  measure × group × period:
  - **Differential:** tile = targeted answer = chart data = drill-down chart = built-in = dashboard
    row feed.
  - **Metamorphic:** row shuffle → same; header synonyms → same; adding a closed account at $0 →
    open accounts −0, average unchanged; duplicating an export overlap → same after cleaning;
    relabelling a currency → that currency's rows only.
  - **Truth:** the generator's truth dict.

  Every future bug becomes a new relation in this file, not a new patch.
- **How AI-assisted teams keep this from decaying.** Prose rules in CLAUDE.md get skipped under
  pressure; executable rules don't.
  - Rules live as tests and lint.
  - A Claude Code **PostToolUse hook** runs the fast structural tests (`test_one_definition`,
    `test_oracle -k quick`) whenever `agents/` or `domain_pack.py` is edited, so a violation is
    seen in the same turn, not in an audit pass a day later.
  - Each domain session's plan starts by running the oracle on the new domain's datasets before
    any paid run. That's the $0 rehearsal from banking, made permanent.

## Red-team (fresh senior reviewer, against the code): 3 blockers, 6 majors, 7 minors

| id | finding | change to the plan |
|---|---|---|
| B1 | An oracle of *agreement* passes when every consumer is wrong the same way (exactly the state after one shared API) | **Truth first:** every relation asserts the generator's truth; agreement is second. Run it through the real pipeline (`with_row_kind`), which the golden never did for built-ins |
| B2 | The model's own sandbox code still aggregates raw rows; moving only the built-ins moves the bug into model code | **One post-sandbox step** (grown from `repair_levels`, which already does this for levels): every model trend/segment finding that `rows_for` can express is recomputed or rejected; anything else is labelled "model-computed" |
| B3 | The dashboard's TypeScript count mode counts every snapshot row (a loan ~14×) | The row feed carries a `_counted` flag computed by the Python rule; TS counts only those rows; one TS test on a snapshot fixture |
| M1 | 118 sites undercounted (ingestion, cleaning, domain_pack add ~87); the estimate is ~2× low | Re-estimated below; **migrate only the sites the oracle shows red**, not a sweep |
| M2 | "No aggregation outside measures" can't work: profiling `nunique`/null counts are legitimate | Scope the AST rule to modules that **produce user-facing numbers** (targeted, visualization, drill-down, built-ins, dashboard feed); give every guard a **planted-violation fixture** that must fail (cheap mutation testing) |
| M3 | `MeasureFrame` can't be "parsed once": drill-down and the dashboard run outside the graph, and checkpoints hold no DataFrames | A pure function `rows_for(frame, contract, …)`, memoised in `RunEnv` by frame hash; never in `AgentState`; the no-DataFrame-in-checkpoint test extended |
| M4 | In-process built-ins lose "the code is the evidence" (ADR-0004) | Evidence shows a readable spec / equivalent pandas; **ADR-0004 addendum** (trusted built-ins run in-process); evidence id strings unchanged so stored replies still cite valid ids. It also fixes a hidden bug: today built-ins read the frame *after* model code may have changed it |
| M5 | The SQL semantic layer is a second set of measure definitions | Differential relation SQL ↔ pandas for each KPI both define (sqlgen database); no third registry |
| M6 | Pinning the golden on Linux breaks the end-of-domain pin; the golden stores hashes | Keep pinning locally; **format statistics at display precision before rendering** so contexts don't carry raw float digits; delete `PLATFORM_FLOATS` |
| minor | Renaming row kinds is not worth it (19+ literal sites, stored code) | Map `row_kind` values to pack labels at the frame; pseudo-pack test + catalog keys only where it fails |
| minor | rapidfuzz over-matches ("date"/"rate" ≈ 0.83) and is a new dependency | Tokens + abbreviation table + value checks; fuzzy only if the corpus shows misses |
| minor | Running-balance detection has its own tie problem | Tolerance, a ≥ 90% match rate, stable order |
| minor | Fail-closed can't name "NPL" without a lexicon | General rule: **never fall back to `metric_columns[0]`; ask** |
| minor | Semgrep/import-linter add tooling | Keep AST checks in `test_one_definition` (the existing pattern); the PostToolUse hook stays |
| minor | Audit findings close with patches | **Bug → invariant:** a finding closes only with a new oracle relation or guard |
| minor | In-process built-ins compute on the raw frame | Keep the re-tokenising PII guard on in-process findings; cover the path in `test_pii_never_reaches_llm` |

## Plan v2, order and cost (≈ 6–7 days at $0, then one paid re-check)

| step | work | time | done when |
|---|---|---|---|
| R0 | **Oracle against truth**, through the real pipeline: truth + differential + metamorphic relations (row shuffle, header synonyms, a closed account at $0, an export overlap, a currency relabel), every datagen dataset; SQL ↔ pandas for shared KPIs | 2 days | each known live bug fails one relation (red) |
| R1 | **`rows_for` replaces the five selectors** (one function, fixed order: time order → entity latest, current or per period → kinds/currency → statistic); migrate only the oracle-red sites; dashboard `_counted` flag + TS test; AST rule scoped to number-producing modules, with planted-violation fixtures | 2 days | oracle green on those relations |
| R2 | **Built-ins in-process + one post-sandbox recompute** of model findings `rows_for` can express; ADR-0004 addendum; evidence ids unchanged; PII guard covered | 1½ days | the sandbox runs model code only; no model finding on a level/flow ships uncomputed |
| R3 | **Detection:** tokens + abbreviation table + value roles (D/C indicator, running balance with tolerance, snapshot grain); never fall back to `metric_columns[0]`; one structural layout-blanks rule (first line, status, debit/credit pair); status by tokens + $0-no-later-rows; header-variant corpus with other domains' negatives | 1½ days | corpus 100%, negatives 0 |
| R4 | **Pseudo-pack test** + catalog keys where it fails; `row_kind` values mapped to pack labels; display-precision formatting, `PLATFORM_FLOATS` deleted; datagen float policy; shuffle-invariance property test | ½–1 day | pseudo-pack and invariance green |
| R6 | Paid re-check: smoke + the cases whose outputs moved (analysis k=1, E2E subset), estimates recalibrated | ½ day, ~$1–2 | gates green |

Deferred until the oracle shows the point fixes keep recurring: the full `Measures` /
`MeasureFrame` API and a 118-site sweep, renaming row kinds, fuzzy matching, the LLM header
mapper, and moving computation to SQL.

**Workflow change for every remaining domain:** the domain session runs the oracle on its new
datasets *before* the smoke run, and an audit finding closes only with a new oracle relation or
guard (bug → invariant).

## What this deliberately does not do
- It does not move computation to SQL (approach 1C): not justified at this size.
- It does not make the LLM header mapper the default: deterministic layers first, measured by the
  corpus.
- It does not freeze model-dependent quality (headline emphasis, wording). Those stay behind paid
  gates, as now.
- It does not remove audits. They become shorter, because the oracle and the structural rules
  catch the five classes first.

## Self-review (risks)
- **R1 is the big one.** About 118 sites must be migrated without changing correct outputs. Run
  the oracle at every step, migrate one module per commit, and keep the golden diff restricted to
  sites the oracle shows were wrong.
- **R2 changes what the model sees as "built-in" evidence.** The ids and shapes must stay
  identical, so stored replies still replay (`test_replay`).
- **R3 can over-match** (a token cascade calling an AR-aging file "banking"). Keep `exclude`
  tokens, and the corpus must carry negatives from every other domain.
- **R4 renames row kinds.** That touches the analysis brief and the sandbox's `row_kind` column,
  so retail and banking contexts change and need one paid re-check (in R6).
