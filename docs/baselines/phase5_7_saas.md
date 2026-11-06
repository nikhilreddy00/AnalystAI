# Phase 5.7 — Domain 6: SaaS / subscription analytics (2026-11-05/06)

Plan: `~/.claude/plans/pasted-content-id-8a1d-start-phase-rustling-candle.md`
- expert brief v2, after one red-team: 6 blockers, 10 majors and 5 minors folded in;
- decisions C0 1–11 settled as a senior SaaS finance / RevOps lead, as the user delegated.

Cited facts: `docs/research/2026-11-05-saas.md`
- sources: ChartMogul, Stripe Billing, Baremetrics, ProfitWell/Paddle, SaaS Capital 2025, KeyBanc
  press releases, a16z, Scale VP;
- ASC 606 comes from Big-4 snippets only and is never quoted;
- where sources disagree, the defaults are chosen in the file.

Builder terms (`GLOSSARY.md`): Ledger, Move, Opening cohort, Month label, Pack column.

## Result

### Shared mechanisms

No role name appears in shared code; the pack YAML holds the SaaS knowledge. The second readers are
toy packs in `tests/test_ledger.py`, `test_spell.py` and `test_pack_columns.py`.

- **S0 `Spell.words`.** The spell's frame columns and sentences use the pack's own words:
  - HR: hire / exit / people;
  - SaaS: start / cancellation / customers.

  HR is byte-identical, and a guard bans HR and SaaS words in shared code.
- **S1 `Pack.columns` (Formula).** A column the product adds at ingestion (`prepare_frame` /
  `add_pack_columns`): a subscription list's MRR = price × seats × (1 − discount) ÷ (interval
  months × interval count).
  - `Role.numbers` gives an interval its months.
  - A Stripe `percent_off` is read as 0–100.
  - An unknown interval, or an odd discount scale, leaves the row blank and is named in the
    source notes.

  `Term.times` gives ARR = 12 × MRR, a state.
- **S2 the ledger.** One entity × month-end value matrix, behind three new term values:
  - `opening` / `retained` (+`capped`): the 12-month cohort ending at the period's end, NRR, GRR
    and logo churn;
  - `moves` (+`move`): new / expansion / contraction / churn / reactivation.

  Rules:
  - A move counts where its later row is (a churn: its earlier row).
  - The bridge identity holds by construction.
  - Expansion and retention values need dated snapshots: they are refused on a subscription
    list, never a confident $0.
- **S3 `Spell.concurrent`.** A base plan and an add-on both count.
- **S3b `Spell.paid_from`.** A converted trial counts from its trial end.
- **S5 `Spell.month_labels`.** An MRR export's `2026-10-01` means October.
- **S4 SQL `schema.table`.** Proven on Postgres in E2E and live: `saas_live.mrr`.
- **S9 the ledger story.** The bridge, the churned count beside the cohort rate, the largest month
  of churned MRR with its single largest customer, the ledger's numbers by month and by group.
  It is citeable evidence (`clean:story`), for every spell pack.

### Pack `domain_packs/saas.yaml`

- **KPIs:** MRR, ARR, paying customers, ARPA, the bridge (5 moves), logo churn, NRR, GRR, quick
  ratio, billings, recurring share.
- **One paying population** (a YAML anchor) for every term.
- **Unsupported:** LTV, CAC, payback, magic number, Rule of 40, recognised revenue, bookings, usage
  MRR, cohort curves, FX, pause history.
- **never_say:** revenue for MRR, × 12 churn, LTV/CAC as computed, benchmark verdicts (a
  structural rule tested on 60+ sentences), bookings.
- **Also:** plain-word role caveats, causal triggers.

### Datasets (`evals/datagen.py`)

Each trap is asserted to move its answer by more than its tolerance.

- `saas_subscriptions` (Stripe-like): 15 traps.
- `saas_mrr_snapshots` (ChartMogul-like): 9.
- `saas_invoices` (Stripe invoice lines): 6.

The older `subscriptions` and `saas_accounts` now read as SaaS. The change is intended and listed.

### Paid gates (all on the audited code unless named)

- **Analysis:**
  - gate: 11 cases k=3, 8/11 every run;
  - 3 diagnosed:
    - the model re-derived the ledger → the ledger's facts;
    - a level repair replaced the model's correct churned MRR → lesson 13;
    - the model summed every row → `in_force_now` is the tile's rows;
  - final re-measure saas-5..11 k=3: **21/21**, numeric accuracy 1.0, false-finding rate 0.0
    after scorer fixes (rescored at $0);
  - saas-1..4 are built-in only (3/3 at the gate, not re-run).
- **Intake:** 14 at k=1, 13/14; intake-saas-06 at k=3 after its glossary fix: 3/3.
- **Insight:** 14 at k=1 (the 2 SaaS cases plus every changed context: 7 older subscription cases,
  banking ins-41, insurance ins-45/46, HR ins-47/48). 13/14 at first; ins-49 (a hard-rule hit:
  column names in caveats) and ins-16 (a scorer gap) both 3/3 after their fixes.
- **Quality** with the critic: SaaS 2/2 and HR 2/2, defect recall 100%, false alarms 0, hard 0.
- **E2E k=1:**
  - gate: 8 cases; subscriptions ×2, HR and the multi-turn passed;
  - 4 SaaS answer-path gaps fixed (every KPI the question names; a total asked for; the cohort
    label);
  - re-measure e2e-78..81 (CSV, xlsx, JSON, Postgres in a non-default schema): **4/4**, hard 0.

### Browser, 1440 px

Live API + worker, production mode, asyncpg, `ANALYSTAI_TODAY` pinned. Every number equals the
generator's truth.

| source and dataset | before the filter | filter | after the filter |
|---|---|---|---|
| subscription list via **CSV** | MRR $869,234.95; ARR $10,430,819.34; 251 customers; new $276,080.10; churned $206,513.79; MRR by segment Ent $631,048.45 / SMB $125,189.33 / Mid $112,997.16; logo churn SMB 20.8% / Mid 14.9%; Feb 2026 churn, 45% one customer | segment = Enterprise | MRR $631,048.45; 22 customers; new $188,723.80; churned $142,222.00; the MRR-over-time line rescaled |
| MRR export via **Excel** | MRR $562,608.85; 539 customers; NRR 91.1%; logo churn 20.1%; the bridge; +36.2% YoY; Mar 2026 88% one customer | — | — |
| invoices via **JSON** | billings $5,931,219; recurring 97.3% | — | — |
| MRR export via **Postgres** (`saas_live.e2e_saas_mrr_snapshots_1`) | NRR Ent 93.0% / Mid 89.2% / SMB 85.4%; MRR; 539 customers | — | — |

- The JSON run found the partial-first-month YoY (+109% against 5 days of September), now fixed.
- **Finished domains** at $0 (`scripts/ui_check`, `make_fixture.py --dataset`, the real
  `filtered_dashboard`). A filter on each changed the over-time line and the tiles; every value
  equals truth:

  | dataset | filter | values |
  |---|---|---|
  | retail_lines | Canada | AOV $118.02, UPT 2.83 |
  | loan_tape | Credit Card | balance $856,530.14, NPL 3.0%, 30+ 10.4% |
  | inpatient | Cardiology | ALOS 4.72, readmission 14.3% |
  | policies | Home | loss ratio 70.1% |
  | hr_roster | Support | attrition 54.6%, headcount 143 |

## Spend: $4.10 of $5 (stop $4.70)

- Evals: $3.198 in total:
  - smoke $0.098;
  - analysis gate $0.871;
  - saas-10/11 re-check $0.067;
  - intake $0.165;
  - insight $0.436;
  - quality $0.052;
  - E2E $0.789;
  - final re-measure $0.72.
- Live: $0.904 (7 runs). Two runs were more than the plan: a misfired browser upload ran an
  overview in an old thread.
- No run went more than 30% over its estimate.

## Shared bugs found and fixed at the root

Every one has a test; HR stayed byte-identical except where listed in CHANGES.

- **Pre-existing on main:**
  - the whole-data kinds were not masked for other currencies (`compute_kpi(full=)`);
  - a level repair replaced a part of a level (`_a_part_of`, lesson 13);
  - a level's column path ignored the stock tile's population (`in_force_counted`). This also
    fixed HR's FTE-by-department column answer to leave contractors out, as its tile does (in
    CHANGES);
  - spell story facts were not citeable;
  - marker blanks were called missing in insight (the logged HR minor is now fixed);
  - the built-in YoY used a partial first month.
- **New with SaaS:**
  - the ledger edges: count presence, snapshot concurrency, the first month, an empty window,
    short data, long windows, leap February;
  - the formula correlation (seats vs MRR);
  - every KPI a question names now answered;
  - a group emptied by the currency cut no longer shown as $0;
  - a spell flow's partial last month.
- **Eval harness** (lesson 17, rescored at $0 each time, fail-first tests):
  - month groups; dates with colons;
  - a level's parts; per-customer count and mean; per-group in-force population;
  - hedges;
  - the insight / viz / intake harnesses adding the pack column, as production does.

## Audit ledger (converging audits, ROADMAP 5.7)

| pass | scope | major | minor |
|---|---|---|---|
| 1 | code-review skill: Standards + Spec, and a senior SaaS expert $0 probe | 9 | ~20 |
| 2 | pass-1 fix diff | 2 | 3 |
| 3 | pass-2 fix diff | 2 | 3 |
| 4 | pass-3 fix diff | 2 | 4 |
| 5 | pass-4 fix diff | 1 | 4 |
| 6 | pass-5 fix diff | 1 | 4 |
| 7 | pass-6 fix diff (+ the final re-measure's scorer) | 3 | 1 |
| 8 | pass-7 fix diff | 2 | 2 |
| 9 | pass-8 fix diff | 1 | 3 |
| 10 | pass-9 fix diff | 1 | 1 |
| 11 | pass-10 fix diff | 1 | 0 |
| 12 | pass-11 fix diff (the verdict regex) | 1 | 0 |
| 13 | 40 fresh sentences on the verdict regex | 3 (folded in) | 2 |

- **Why it didn't converge faster:** from pass 3 on, every major was in two places:
  - the never_say benchmark-verdict regex;
  - the eval scorer's populations.

  Each was a narrowing of a minor finding that broke another sentence or reading (lesson 22, again).
- **Product numbers:** none of passes 3–9's majors changed a number a user sees. All SaaS answers
  equalled truth from pass 2 on.
- **Lesson:** a free-text classifier (a verdict regex) needs its labelled sentence set as the
  first commit, and the scorer's populations need right and wrong readings planted per change.
  Both are now tests (`test_saas.py`: 60+ sentences; `test_eval_scorer.py`: planted right ×1.0
  and wrong ×1.07 readings).

- **Where the loop stopped, and why:** the last three passes were all on the never_say
  benchmark-verdict regex, a free-text classifier.
  - It is now tested on 150+ labelled sentences, and every reviewer's set passes.
  - Pass 13's 40 fresh sentences: 37 right, then all 40 after its cases were folded in.
  - It is one signal beside the critic (quality gate: defect recall 100%), and it only flags a
    claim, never a number.
  - Remaining known misses ('NRR had a strong quarter', 'churn remains a weakness', 'impressively
    low') are minor and logged.

## Stated limits and follow-ups

- **minor:** a billing parameter (`interval_count`) is charted as a measure ("Average Interval
  Count by plan"). Fixing it means a role-kind change to the model's inputs: the next SaaS session.
- **minor:** the column path's count of a level on a list is subscriptions, not customers (the KPI
  path is right).
- **minor:** the add-on is listed as a plan in by-plan breakdowns.
- **minor:** the old `churned` flag in the older datasets isn't mentioned beside logo churn 0%.
- **minor:** a model-rounded ratio re-rounded in prose (85.5% vs the chart's 85.4%).
- **minor:** "NRR had a strong quarter" is not flagged (ambiguous by design).
- **Deviations from the plan's case list (the Spec review):**
  - analysis has 7 new cases (saas-5..11), not 10; the MRR bridge identity, ARPA per customer and
    reactivations are $0 tests (`test_saas.py`), not paid cases;
  - no retail cross-domain E2E (retail is byte-identical in `domain_diff`); HR e2e-71 was run;
  - S6 detection shapes differ slightly from the plan (keyed on price; no amount on invoices).
- **Unsupported, said:** LTV without margin, CAC, payback, Rule of 40, magic number, recognised
  revenue, bookings, usage MRR, cohort curves, FX, pause history; expansion and revenue retention
  on a subscription list.
- **Banking:** `avg_balance` is a ratio of states not marked a state (the cross-domain follow-up
  PR, unchanged here).

## Regression diff (`scripts/domain_diff.py main`)

- `hr_snapshots`: one sentence's grammar ("1 start or end date falls").
- `hr_roster` / `hr_roster_onerow`: FTE by department without contractors (= the FTE tile).
- `subscriptions` / `saas_accounts`: now read as SaaS.
- Every other dataset is byte-identical.

## Time: ~6.8 h vs ~4.5 h

| stage | time |
|---|---|
| context, research, brief, red-team, plan v2 | 0 h 50 |
| build S0–S5, S2 ledger, pack, generators, tests, $0 gates | 1 h 40 |
| paid gates and diagnoses | 1 h 10 |
| audits 1–13 | 2 h 30 |
| browser (live + finished domains) and docs | 0 h 40 |

The overrun is the audit tail (passes 3–10) on the regex and the scorer.

## After the merge: design reviews, review fixes and CI (PR #1, 2026-11-06)

**Skipped design steps.** Domain-modeling and codebase-design were skipped in plan mode. They
were run afterwards, with a senior code-review pass (Standards + Spec). The 13-pass audit tail
came from rules written more than once. Each pass fixed one copy, and the next pass found
another.

**Nine recurring bug classes were reported, not patched.** They wait for a structural plan; the
table is in PR #1's description.

**Seven one-off bugs were fixed at the root.** Each has a test that failed first.

| Bug | Root fix |
|---|---|
| A list's next `billing_date` read as snapshots: MRR $476k for $869k, logo churn 100% | `Role.weak_headers` (an ambiguous header needs repeating spells); one repeat test `_dates_each`, shared with `_panel` |
| `compute_kpi` crashed on a duplicate index | Unique row labels enforced at entry |
| A blank formula input made MRR blank silently | One note rule over every required input |
| "Logo churn (12 months)" next to "6-month cohort" | The period has one owner, `window_words`; guard over every pack |
| "Cancellation" meant both the end and the request | SaaS spell word `end` (`end_event_date`) |
| ARPU answered as ARPA silently | Label `ARPA per paying customer` |
| GLOSSARY drift | Spell, In force, Move |

**Found on the way:**
- My first `billing_date` fix dropped real one-snapshot roles; the review caught it and it was
  reworked.
- The causal regex flagged an investigation. This was a narrow fix; class #8 stays in the plan.
- An order-dependent purge-table test.

**Spend.** A k=1 smoke on `saas-1`, `-10` and `-11` cost $0.09 (accuracy 1.0, false findings 0),
plus `ins-16` re-run at k=1 for $0.02. Domain total: $4.21.

**CI.** It took ~21 min per run, ran twice per push (push + PR) and queued runs.
- The backend now runs in 4 shards at once, each worker with its own scratch database.
- Push triggers CI only on `main`, and a newer run cancels the older one.
- Result: green in 7 min 37 s (run 42272311467).
