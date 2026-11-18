# Phase 5.7 — Domain 8: Logistics (2026-11-18)

Wall clock: 3.6 h (first commit 2026-11-18 10:07 → merge 2026-11-18 ~13:45; plan mode before,
~0.5 h). Domain 7: 20.4 h + a 4.7 h review; Domain 6: ~7.5 h. The breaker never fired (5 h before
the smoke and a 12 h projection were never approached: smoke at 0.9 h, projection ≤ 4 h).

Plan: `~/.claude/plans/pasted-content-id-0c60-start-phase-adaptive-metcalfe.md` (v2). It checks
off every plan-mode step of the ROADMAP 5.7 contract by name:
- **brainstorming:** one `logistics` pack, three grains (TMS shipments, WMS/OMS order lines, dock
  appointments) covering OTIF, on-time delivery, transit, cost per shipment, fill rate and dwell;
  what is not built is listed below;
- **research:** a background agent on primary sources (X12 214/210/945 guides from Best Buy,
  C.H. Robinson and FedEx; GS1 EPCIS 2.0; the DOT OIG 2018 detention report; 49 CFR 395.2; FedEx
  dim weight; SAP on-time and OTIF; EasyPost, Shippo; DPD's privacy notice),
  `docs/research/2026-11-18-logistics.md`; Walmart OTIF, SCOR wording, ASCM fill rate and the
  CSCMP/APQC glossaries marked unverified;
- **expert brief:** the expert view (KPIs exactly, traps T1–T21, must say / never say) and each
  agent's builder view;
- **domain-modeling:** the term ledger (shipment, on-time, days late, transit, cost per shipment /
  lb, fuel share, OTIF line and order, unit and line fill, dwell, visits over 2 h, on-time arrival,
  no-show, carrier, lane) with clashes against the 7 packs; GLOSSARY: as-of date with no spell,
  past due;
- **codebase-design:** the shared-fix table L1–L3 (deletion test, second readers);
- **Step 0, ledger:** 16 rows this domain touches, each with its check;
- **red-team:** one fresh subagent: 3 blockers, 7 majors, 4 minors, all folded in (RT-1..RT-14);
- **grilling:** G1–G8 answered as a senior engineer; the user's calls U1 (open past their promise
  are late), U2 (all three datasets), U3 (D7-19 in its own PR);
- **sizing:** cost and hours per build step, ≈ 11 h / ≈ $4.3 projected; actual 3.6 h / $4.45.

**Stop rule (the audit procedure):** stop when the properties, the mutation gate and the ledger
checks (`tests/test_retro_ledger.py`) are green after a pass. Met after pass 2.

## Result

### Shared fixes (no logistics word in shared code)

- **L2 (K1)** PII: tracking numbers and air waybills are masked identifiers; carrier, warehouse,
  facility, DC, hub, terminal, depot, dock and yard names are things; consignees and signers are
  people (`guardrails.py`). Insurance's `carrier_name` (an insurer) is no longer masked as a person.
- **L1 (U1)** `as_of` as a derived operand (`[as_of, days, promised_date]`), `operands()` the one
  list of roles an expression reads, `data_as_of` the time axis's latest day not after today,
  always on the whole frame; `latest_day` shared with the spell fallback. Second reader: a toy
  pack's overdue bills (`test_second_pack.py`).
- **C5 (D1-40)** a derived span blank everywhere because none qualified is judged by its inputs;
  a numeric condition no row meets is a measured 0 (unknown stays for a kind of row by its words).
- **C6 (RT-7, D1-16)** the same words in two concepts name the one the data can compute
  (on-time rate: delivery on shipments, arrival on a dock log).
- **C7 (D4-21)** an id is never a chart breakdown (`build_context`); a KPI grouped by the key it
  counts answers its overall value, said (`handle_kpi`).
- **C0 (D8-02)** one `wall_times`: a zoned date beside a naive one is read as written; a day span
  compares calendar days as written; the as-of and cleaning's spell as-of read zoned days.
- One `_expr_words` for every derived expression (answers and the model's brief).
- Harness (D8-01): every case's check arguments bind to the scorer's signature at $0.

### Pack and data

`domain_packs/logistics.yaml`: on-time delivery (delivered by the promise ÷ delivered + returned
+ open past their promise; early is on time; no status column → the file's own flag or
unavailable), late shipments, average days late, transit (calendar days from the ship date), cost
per shipment and per lb (kg × 2.20462 by the row's unit; a blank unit is no weight), fuel share,
OTIF by order and by line, unit and line fill over lines that left the warehouse or were
backordered, dwell (exact hours), on-time arrival, late arrivals, visits over 2 h (the usual free
time, never billed detention), no-shows, dock appointments. Unsupported (said): business-day
transit, perfect order, tender acceptance, claims, ETA prediction, on-time pickup, order cycle
time.

Datasets (`evals/datagen.py`), each trap asserted against its naive twin: `tms_shipments`
(3,000), `order_lines` (6,025), `dock_appointments` (1,500). The old `shipments` reads as logistics
(K4): its 0/1 flag is the on-time alternative, averaged, never summed. K2: three Hypothesis books
with P2, P2r, P3 and D (product = a plain-loop reference).

### Paid gates

| gate | cases | result | report |
|---|---|---|---|
| smoke (k=1) | logi-7, logi-8, e2e-91 | first try died in the harness (a case argument, $0.049, D8-01); then **3/3**, hard 0 | analysis-…160443Z, …160510Z, e2e-…160534Z |
| analysis gate (k=3) | logi-1..9 | 22/27; logi-5 (stale brief) and logi-7 (cancelled kept by the model) fixed at $0 → **6/6** | analysis-20261118T161409Z, …162134Z, …162207Z |
| intake (k=1) | intake-logi-01..06 | **6/6** (and 6/6 after the word moves) | intake-…161640Z, …174128Z, …175433Z |
| insight (k=1) | ins-54, ins-55 | **2/2** (and 2/2 re-measured) | insight-…161651Z, …174137Z |
| E2E gate (k=1) | e2e-33, 34, 91–97 | 6/9, hard 0; e2e-34, 95, 97 diagnosed at $0 | e2e-20261118T173906Z |
| E2E k=3 on failures | e2e-34, 95, 97 | e2e-34 **3/3**; e2e-95 0/3 → fixed → **3/3**; e2e-97 0/3, then 0/1 (open) | e2e-…175039Z, …175105Z, …181719Z, …181930Z |
| final re-measure (k=3) | logi-1..9 | 25/27 (logi-4 1/3: the model's code errored, fixed); logi-1 3/3, logi-4 **0/3** (open) | analysis-…173905Z, …175343Z |
| judge (K5) | 12 logistics tuning sentences | 11/12 (missed a counterfactual causal) | prose_judge-tune-20261118T180531Z |

K3 baselines (B0, main): insight 0.96, quality checks-only 0.865, live 0.827 (rescored). No
quality case was added (said below); the quality suite reads the pack's never_say only.

### Live stack (production mode, asyncpg, real worker)

| source and dataset | read | truth |
|---|---|---|
| tms_shipments via **CSV** | on-time 88.9%, transit 3.05 days, cost per shipment $247.72 | 0.8891, 3.05, 247.72 |
| order_lines via **Excel** | OTIF by order 63.3%, unit fill 96.7% | 0.6328, 0.9668 |
| dock_appointments via **JSON** | on-time arrival 72.0%, dwell 2.32 h | 0.7204, 2.318 |
| tms_shipments via **Postgres** (`logi_live.tms_shipments`) | the same five tiles as the CSV | — |
| Dashboard, filter Carrier = UPS | 1,160 rows; on-time 93.7%, transit 2.45 days, 1,099 shipments; the over-time line rescaled | 0.9372 |
| follow-up "and only for LTL shipments?" | "On-time delivery by Carrier, LTL: Old Dominion 92.5%, FedEx Freight 88.9%" | 0.9247, 0.8891 |

The filter read used the API the page calls (`/dashboard/latest/filtered`), not a browser drive;
the page is covered by `frontend/e2e/logistics_dashboard.spec.ts` at 1440 px (41/41 Playwright).
REST API and Sheets sources: not run (as in Domain 7).

## Spend: $4.45 of $5 (ask $4.40, stop $4.70)

Eval reports $3.71 (smoke $0.26 incl. the lost $0.049, analysis gate $0.76, logi-5/7 $0.16, E2E
gate $0.66, final re-measure $0.70, re-runs $0.94, the user's fix round $0.24), judge $0.07, live
stack $0.67 (6 runs). The user authorised crossing the ask point for one fix round.

## Audit (procedure from 5.7b-3)

| pass | model | majors | per class | minors |
|---|---|---|---|---|
| 1 | Sonnet | 3 | C0 1 (zoned crash), C6 1 (count questions), C1 1 (weight sum: D7-19) | 7 |
| 2 | Opus, the fix areas | 3 | C6 1 (share of late → count), C0 2 (zone pair; HR zoned dates, pre-existing) | 6 |

**Pass count: 2.** A shared "percent of" unit rule moved three finished domains' contexts and was
backed out at once (the pack's phrases instead). Mutation gate: 17 changed gated functions, 0
survivors on changed lines (two named equivalents), `--check main` clean. Code review
(`mattpocock-skills:code-review`): Standards 2 hard (a second copy of the span words; the
reference dropped `today`), both fixed; Spec: the on-time no-status fallback and the fill wording
fixed, the rest below as deviations.

Ledger: D4-21, D1-40, D7-19, D1-20, D1-16 raised; new D8-01, D8-02, D8-03 (Open, pinned
xfail-strict). UNENFORCED 77 (≤ 77).

Golden re-pinned (`evals.snapshot pin`, CHANGES emptied). Its diff: `shipments` reads as
logistics (K4: logi-1, logi-3 contexts; its tiles and trend now a rate). `domain_diff.py main`:
`ledger_drcr` and `store_weeks` used to chart a histogram of an account or store number; they now
chart the amount and net sales, and store_weeks gets a department breakdown that follows the
region filter (the D4-21 rule; no model context moved, no number changed).

## Deviations

- **Not every run passes:** logi-4 0/3 at the final re-measure (the model's own on-time-by-carrier
  code drops open shipments past their promise; the live overview headline said UPS 96.2% for
  93.7%) and e2e-97 (the LTL follow-up after an overview isn't carried by intake). The product's
  tiles, answers and dashboard are right on both; the fix is D7-19's PR (the model reads the
  product's numbers). README lists logistics as not yet passing every run.
- Rows in another currency are left out of every figure, counts and on-time too (the existing
  one-population rule, said by the caveat); the banking follow-up "count vs money rows" stays.
- Not planted: T9 (no total-cost column: cost per lb needs a total), T21 (exact duplicates: kept
  and said by the generic rule), T12 confounding (each carrier has one mode), S-a, S-b, RT-13.
- The as-of is the time axis's last day (ship / order / appointment), not the latest event of any
  date role (a simplification of L1, in the GLOSSARY).
- The order-line time axis is the order date (G4 asked the requested date).
- RT-5 became a `line_number` id role; a line id that restarts per order is not checked.
- No visualization or quality case; billed weight, on-time pickup, extra never_say patterns not
  built (the judge covers causal and benchmark prose).
- The live filter read used the API, not a browser drive.
- PR14-01 for `machine_readings` not closed (it has no KPI to generate).

## Not built (said)

Tracking-event feeds (one row per scan), multi-leg / multi-stop loads, business-day transit, dim
or billed weight, EDI implied decimals, FX conversion, freight claims, tender acceptance, perfect
order beyond OTIF, billable detention, appointment grace minutes, ETA prediction.

## Follow-ups

- D7-19's PR: hand the analysis model the product's numbers by group (closes logi-4, the overview
  headline, the weight sum and the generic donuts reading cancelled and other-currency rows).
- D8-03: the spell code reads dates through `wall_times` (HR rosters with zoned dates).
- e2e-97: a narrowing follow-up after an overview (D1-16).
- The group answers' plain-words basis (`says` only on single values); "leads" on lower-is-better
  KPIs; '(lbs)' headers; lines per order.
