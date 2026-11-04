# Insurance domain research: grain, KPI definitions, traps, personal data (2026-11-03)

Purpose: exact, citable rules for the insurance domain pack (Phase 5.7 Domain 4).
Method: primary sources first: CAS study notes (Werner & Modlin "Basic Ratemaking" 5th ed. 2016;
Friedland "Estimating Unpaid Claims Using Basic Techniques" v3 2010), NAIC model laws and the
American Academy of Actuaries' 2026 comment letter quoting the NAIC Schedule P instructions,
SOA/LIMRA persistency study, Colorado statute, IRMI, vendor docs. Every claim carries its URL.
**unverified** = no primary source could be read; treat as analyst convention, not rule.

Short source keys (cited as [WM p.N], [FR ch.N]):
- WM = https://www.casact.org/library/studynotes/Werner_Modlin_Ratemaking.pdf
- FR = https://www.casact.org/library/studynotes/Friedland_estimating.pdf
- AAA = https://actuary.org/wp-content/uploads/2026/04/casualty-comments-castf-schedule-P-Phase-2.pdf
  (Academy letter to NAIC CASTF, 2026-03-26, quoting current Schedule P Part 1 instructions)

Fetch notes: both CAS PDFs were read in full (pdftotext). The NAIC Annual Statement Instructions
are licensed/paywalled; Schedule P wording comes verbatim from the AAA letter. iii.org's glossary
404'd; Guidewire docs render client-side (quoted from search snippets of docs.guidewire.com);
Lloyd's Coverholder Reporting Standards field list not read.

---

## 1. Grain of typical extracts

### 1.1 Policy table
- Exposure = "the basic unit of risk that underlies the insurance premium"; one house-year for
  homeowners, payroll/$100 for US workers comp. [WM p.2]
- Four measures each for exposure and premium: **written, earned, unearned, in-force** ("in-force
  premium is the full-term premium for policies that are in effect at a given point in time"). [WM p.2]
- Typical exposure bases (Table 4.1): personal auto = **earned car year**; homeowners = earned house
  year; WC = payroll; CGL = sales/payroll/sq ft/units; commercial property = amount of insurance;
  physicians = physician-years. [WM ch.4]
- Policy database is **transactional**: a mid-term cancellation adds a second record with negative
  exposure and negative written premium (Policy B: +1.00/$600, then −0.25/−$150 → net 0.75/$450);
  an endorsement adds a negating record and a new record. Fields: policy, original effective date,
  original termination date, transaction effective date, rating variables, written exposure,
  written premium. [WM pp.37–39, Table 3.1]
  → Confirmed: grain may be policy-term, policy-transaction, or (for GLMs) aggregated to
  "net" rows per rating-variable period. A row is not necessarily a policy.
- Store driver **date of birth**, not age (age changes between terms). [WM p.37]
- Public example (Kaggle/CASdatasets freMTPL2freq): one row per policy with `IDpol, ClaimNb,
  Exposure` ("length of the policy's exposure period, in years"), `VehPower, VehAge, DrivAge,
  BonusMalus, VehBrand, VehGas, Area, Density, Region`; freMTPL2sev has `IDpol, ClaimAmount`
  (one row per claim). https://dutangc.github.io/CASdatasets/reference/freMTPL.html
  **unverified**: commonly-cited cleaning (exposure capped at 1 year, ClaimNb > 4 set to 4) — only
  a course deck (Scribd) says so.

### 1.2 Claims table
- "Each record generally represents a **transaction** tied to a specific claim (e.g., a payment or
  a change in reserve)". [WM p.39]
- Typical fields: policy id, risk id, **claim id** ("same identifier is used if the claim has
  multiple claim transaction records"), **claimant id** ("unique identifier for each specific
  claimant on a particular claim"), date of loss, report date, transaction date, claim status
  (open/closed, optionally re-opened/re-closed), claim count "by coverage", paid loss (by coverage/
  peril, cat flagged), event (catastrophe) id, **case reserve "or the change in the case reserve"**,
  paid ALAE (often no ALAE reserve), salvage/subrogation, claim characteristics. ULAE is **not** on
  the claim file ("cannot be assigned to a specific claim"). [WM pp.39–41]
- Example Table 3.2 columns: `Policy, Claim Number, Accident Date, Report Date, Transaction Date,
  Claim Status, Loss Payment, Case Reserve, Paid ALAE, Salvage/Subrogation`. [WM p.41]
- Feature/coverage level (Guidewire ClaimCenter calls it an **"exposure"**): "Every exposure is
  linked to one coverage (where the money is 'coming from') and one claimant (where the money is
  'going to')"; "When an exposure is created, ClaimCenter also creates a reserve line".
  https://docs.guidewire.com/cloud/cc/202402/cloudapibf/cloudAPI/topics/111-CCFNOL/05-exposures/c_overview-of-exposures-in-ClaimCenter.html
  (quoted via search snippet; page renders client-side)
  → Trap: "exposure" in a Guidewire extract = a claim feature, not earned car-years.
- Hierarchy (confirmed): claim (occurrence) → claimant → feature/coverage → transaction.
  Insurers differ on counting an auto accident with BI + PD, or several claimants, "as one claim
  or multiple claims". [FR ch.3]
- NAIC Schedule P: insurers must "Indicate in the Interrogatories whether claim count information
  is reported per claim or per claimant"; the Academy suggests per-feature could be allowed. [AAA §7]
- Claim snapshots: "reported claims at a point in time" = cumulative paid through date + case
  outstanding as of that date; over an interval = payments + change in case outstanding. [FR ch.2]
- Bordereaux (Lloyd's delegated authority): risk/premium and claims bordereaux governed by Lloyd's
  Coverholder Reporting Standards (v5.2), mandatory + conditional fields.
  https://www.lloyds.com/coverholderreportingstandards — field names **unverified** (not read).

### 1.3 Development triangles
- Rows = accident year ("each row consists of a fixed group of claims"), columns = age (12, 24,
  36… months), diagonals = valuation dates. [FR ch.5, Table 3]
- Paid triangles are **cumulative or incremental**: "it is important to clearly state whether the
  claims are cumulative or incremental." [FR ch.2]
- Rows can also be policy year or report year (claims-made). [FR ch.5; WM p.44]

### 1.4 Column names (hint check)
Confirmed by WM/FR: policy number, effective/termination date, written premium, written exposure,
accident/loss date, report date, transaction date, status, paid loss, case reserve (outstanding),
ALAE, salvage/subrogation, coverage, peril, cat/event id, state/territory. Earned premium and
earned exposure are usually **derived** (from dates) rather than stored per transaction [WM ch.4–5].
Guidewire/Duck Creek physical column names: **unverified** (vendor schemas not public).

---

## 2. KPI definitions (exact)

All from [WM Ch.1 pp.7–10] unless noted. WM repeats: "it is important to clearly document the
types of claims and exposures used" — every KPI has variants, so the report must name its inputs.

| KPI | Formula | Notes from source |
|---|---|---|
| Frequency | claims ÷ exposures | "Normally, the numerator is the number of reported claims and the denominator is the number of earned exposures." |
| Severity | losses ÷ claims | "paid severity is calculated using paid losses on closed claims divided by closed claims. Reported severity … reported losses and reported claims"; ALAE in or out |
| Pure premium (loss cost, burning cost) | losses ÷ exposures = frequency × severity | "Typically … reported losses (or ultimate losses) and earned exposures"; may include ALAE/ULAE |
| Average premium | premium ÷ exposures | premium and exposures "on the same basis (e.g., written, earned, or in-force)" |
| Loss ratio | losses ÷ premium = pure premium ÷ average premium | "Typically … total reported losses and total earned premium"; with LAE = "loss and LAE ratio" |
| LAE ratio | LAE ÷ **losses** (not premium) | loss & LAE ratio = LR × (1 + LAE ratio), not LR + LAE ratio |
| UW expense ratio | UW expenses ÷ premium | acquisition/commission/taxes ÷ **written**; general ÷ **earned** |
| Operating expense ratio | UW expense ratio + LAE ÷ earned premium | |
| Combined ratio | LR + LAE ÷ **earned** + UW expenses ÷ **written** ("historically") or LR + OER | "the loss ratio should not include LAE or it will be double counted" |
| Retention ratio | policies renewed ÷ potential renewal policies | "significant number of variations", e.g. excluding death/non-renewals |
| Close (hit) ratio | accepted quotes ÷ quotes | multiple quotes per prospect counted once or each |

- Combined ratio basis naming: IRMI: loss + LAE ÷ earned "(the calendar year loss ratio)" plus
  "all other expenses by either written or earned premiums (i.e., trade basis or statutory basis
  expense ratio)". https://www.irmi.com/term/insurance-definitions/combined-ratio
  Secondary sources **conflict** on which label goes with written vs earned; WM's
  "historical" formula (expenses ÷ written) is the citable one. Never compute without expenses.
- Earned premium/exposure: "assumes the policy is earned evenly throughout the policy period";
  not true for warranty or seasonal lines. [WM ch.4 p.56 fn] Individual policy: written = earned +
  unearned. Calendar year: CY unearned = CY written − CY earned + unearned at start. [WM p.56]
  Monthly "24ths" rule (policies assumed written mid-month) is an approximation. [WM ch.4]
  → Daily pro-rata: EP(period) = WP × days of term inside period ÷ days in term (same principle;
  day-count is the exact version of WM's even-earning assumption).
- Policy-year exposure is fully earned only 24 months after the PY starts (annual terms). [WM p.43]
- Incurred: "Reported Losses = Paid Losses + Case Reserve" (= case incurred). Ultimate = reported
  + IBNR + IBNER ("development on known claims"). [WM p.3]
- LAE = ALAE (claim-specific, e.g. outside defence counsel) + ULAE (claims staff salaries). [WM p.4]
- Aggregation [WM pp.42–44]:
  - **Calendar year**: all transactions in the year; CY reported loss = paid + change in case
    reserves; fixed at year end; mismatches premium and losses.
  - **Accident year**: losses for accidents in the year vs premium earned in the year; "not closed
    (fixed) at the end of the year" — future development must be estimated.
  - **Policy year** (underwriting year): "best match"; slowest to mature.
  - **Report year**: by report date; claims-made lines.
- Claim-count types used by actuaries: reported, **closed with payment, closed with no payment**,
  open, **reopened**. [FR ch.3]
- Schedule P: "The number of claims reported in each incurred year is equal to the number of open
  claims at the end of the current year plus cumulative claims closed with or without payment"
  (instruction #3, quoted). [AAA §5] → statutory reported counts **include** CWP/CNP.
- Life lapse (SOA/LIMRA): "Annualized Policy Lapse Rate = 100 × Number of Policies Lapsed During
  the Year ÷ Number of Policies Exposed to Lapse During the Year"; lapses contribute a full year of
  exposure; deaths/expiry/maturity/conversion are not lapses and contribute only the fraction in
  force. Lapse includes non-payment, insufficient cash value, full surrender, reduced paid-up/
  extended term, unknown. Reported on a **policy (count) basis and a face-amount basis** (2001–02:
  4.9% vs 7.8%). https://www.soa.org/globalassets/assets/Files/Research/Exp-Study/US-Indiv-Life-Persistency-Report-FINAL.pdf
  Older SOA work used three bases: number of policies, amount of insurance, annual premium.
  https://www.soa.org/4939a0/globalassets/assets/library/research/transactions-of-society-of-actuaries/1960/january/tsa60v12n3331.pdf
- Persistency = 1 − lapse rate over the period (**unverified** as a formal SOA definition; the
  SOA UL study measures "premium persistency" separately from lapse).
  https://soa.org/49fa9d/globalassets/assets/files/resources/experience-studies/2024/15-21-ulpp-ulls.pdf

---

## 3. Traps (hint check)

1. **Written ≠ earned** — confirmed: written books full term at issue; earned accrues with time
   [WM p.2]. Loss ratio uses earned. A file with only written premium can't give a loss ratio
   without deriving earned from dates.
2. **Reserves are a level at a valuation date** — confirmed: case reserve is "an estimate … to
   ultimately settle that claim … excludes any payments already made" and is revised over time
   [WM p.3]; reported claims "at a point in time" vs "over an interval" [FR ch.2]. Summing case
   reserves across snapshots multiplies them; a transaction table may hold the reserve **change**
   not the level [WM p.40] — the column's meaning must be established first.
3. **Incurred = paid + case (case incurred) ≠ ultimate** — confirmed [WM p.3].
4. **Latest AY immature** — confirmed: AY is "not closed" [WM p.43]; development factors for "the
   most recent accident years" are "highly leveraged" (cumulative paid factors > 10 not unusual for
   long-tail lines), so a single claim can give "unreasonable projections" [FR ch.7]. A senior
   analyst says: "AY 20XX is at N months; its loss ratio will rise as claims are reported and
   reserves develop; not comparable to mature years."
5. **IBNR needs history** — a triangle needs several diagonals (valuation dates) [FR ch.5]; one
   snapshot has one diagonal, so no development factors and no IBNR. Confirmed by construction.
6. **Reopened claims** — Schedule P's count rule "could imply that re-opened claims should be
   double-counted" (Academy) [AAA §5]; insurers differ on whether a reopen is a new claim [FR ch.3].
   A reopen shows as a payment with no prior case reserve [FR ch.2 Table 2].
7. **Salvage & subrogation** — Schedule P: "Loss payments are to be reported net of salvage and
   subrogation received" [AAA §8]; insurers record paid net or gross, or track recoveries separately
   [FR ch.3]. As a negative paid row it makes incremental paid negative.
8. **Claim vs claimant vs feature** — confirmed (§1.2). Counting rows of a feature table inflates
   frequency.
9. **Calendar vs accident vs policy year** — confirmed (§2). Never mix CY premium with AY losses
   without saying so.
10. **Exposure units differ by line** — car-years vs house-years vs payroll [WM Table 4.1]; never
   add exposures across lines.
11. **CWP/CNP in frequency** — WM "normally" uses reported counts [WM p.7]; Schedule P reported
   includes closed without payment [AAA §5]; FR tracks CWP and CNP separately [FR ch.3]. The pack
   must say which.
12. **Large-loss and cat** — shock losses (multi-claimant liability, high-value total loss) and
   catastrophes distort; ratemaking removes them or caps above a threshold and adds a long-run
   provision; PCS cat = ≥ $25M insured property loss [WM pp.97–98]. Large claim thresholds differ
   (claims dept $100k vs actuary $1M) [FR ch.3].
13. **Mixing lines** — loss ratios differ by line (WM's per-line exposure bases; tail length
   differs [FR ch.7]); a total loss ratio moves with mix. (Mix wording is analyst convention;
   WM says average premium changes "highlight changes in the mix of business" [WM p.8].)
14. **Claim counts on transaction tables** — claim id repeats on every transaction [WM p.39];
   count distinct claim ids (or the claim-count field), never rows.
15. **Gross vs net of reinsurance** — analyse gross and net separately [FR ch.3]; Schedule P
   reconciles reinsurance, discounting, S&S [AAA §9].
16. **Trend/inflation** — losses are trended to future cost levels; average premium moves with
   rate changes and mix [WM ch.5–6]. Severity across years is in nominal dollars unless trended.
17. **Cancellations are negative rows** — summing written premium is right (net), counting rows as
   policies is wrong [WM Table 3.1].
18. **Earning pattern** — even earning fails for warranty/seasonal lines [WM ch.4].

---

## 4. Must-say / must-never-say

Must say (each KPI): which losses (paid/case incurred; ALAE in/out; net/gross of S&S and
reinsurance), which premium (earned vs written), which claim counts (reported incl./excl. CNP;
claim vs claimant vs feature), which year basis (AY/CY/PY), the valuation date, and the maturity
of the latest year. WM: "it is important to clearly document the inputs chosen." [WM p.8]

Never:
- Call a loss ratio on an immature AY/PY "final" or "ultimate" [WM p.43; FR ch.7].
- Estimate IBNR/ultimate from one snapshot (§3.5).
- Report a combined ratio without expense data (it needs UW expenses + LAE [WM p.10]).
- Compare loss ratios across lines/segments without noting mix and maturity.
- Make causal claims about rating factors. Basis: WM — claim characteristics may be studied, but
  "only characteristics known … at the time of policy quotation are usable in the rating
  algorithm" [WM p.41]; causal wording itself is analyst principle (**unverified** as a rule).
- Recommend using protected attributes in rating/underwriting. Facts:
  - Most US states require rates "not excessive, not inadequate, and not unfairly
    discriminatory"; "some states have statutes prohibiting the use of gender in rating" while
    others permit it; credit score restricted in some states. [WM, rating-variable "Legal Criteria" section]
  - NAIC Unfair Trade Practices Act (Model 880) §4H(5): no refusing/limiting coverage "because of
    the sex, marital status, race, religion or national origin of the individual"; §4H(3) bars
    refusal "solely because of the geographic location" absent sound actuarial basis.
    https://content.naic.org/sites/default/files/model-law-880.pdf
  - Colorado SB21-169 (2021): insurers' external consumer data, algorithms and models must not
    unfairly discriminate on race, color, national or ethnic origin, religion, sex, sexual
    orientation, disability, gender identity or gender expression; testing framework required.
    https://leg.colorado.gov/bills/sb21-169 ; https://doi.colorado.gov/for-consumers/sb21-169-protecting-consumers-from-unfair-discrimination-in-insurance-practices
  → Pack: a gender/race/religion/national-origin column may be described, but any "rate by X"
  comparison carries a caution that use in rating is restricted by state law; never suggest it.

---

## 5. First questions a senior analyst asks

Policy table:
1. What is one row — a policy term, a transaction (endorsement/cancel), or a policy-month? [WM Table 3.1]
2. Is premium written, earned or in-force? If written, are effective/expiration dates present to
   earn it? [WM p.2]
3. What is the exposure unit, and is it written or earned? [WM Table 4.1]
4. Which lines/states/coverages are mixed in the file?
5. Term length (6 vs 12 months) and cancellations represented how? [WM ch.4]
6. As-of date of the extract.

Claims table:
1. What is one row — claim, claimant, feature/coverage, transaction, or snapshot? [WM p.39; FR ch.3]
2. Valuation date(s): one snapshot or many? Is the reserve column a level or a change? [WM p.40]
3. Is paid cumulative or incremental? [FR ch.2]
4. Does incurred include ALAE? Net or gross of S&S, of reinsurance, of deductibles? [FR ch.3]
5. How are reopens recorded — same claim id or new claim? [FR ch.3]
6. Are CWP/CNP claims included? [FR ch.3]
7. Cat/large-loss flags or event ids? [WM p.40]
8. Can claims be joined to policies (policy id) for premium/exposure? Otherwise no loss ratio or
   frequency.
FR also asks for reconciliation to financial statements and "large negative paid claims or
apparent inconsistencies" as data checks. [FR ch.3]

---

## 6. Life insurance (brief) and recommendation

- Lapse rate: count and face-amount bases, exposure-weighted, deaths/maturities excluded from the
  numerator (SOA/LIMRA, §2). Premium persistency is measured separately (SOA UL study, §2).
- Life KPIs that matter (mortality A/E, reserves, lapse by duration) need policy-duration
  exposure, issue age, face amount, and published tables — not one uploaded snapshot.
- **Recommendation: stop at P&C for the pack.** Reasons: (1) the KPIs, grain and traps above are
  P&C (earned premium, claims development); life shares almost none beyond "retention"; (2) a
  correct lapse rate needs exposure-to-lapse calculation by duration, which a single extract
  rarely supports; (3) life/disability files carry health data (Model 668 §3K(3), §7), raising
  the bar to the healthcare pack's PHI rules; (4) a per-domain eval is required for a support
  claim and none exists for life. Offer only a generic "retention / lapse = lapsed ÷ exposed
  (count basis)" with the basis stated; life-specific questions are unsupported.

---

## 7. Personal data in insurance extracts

- NAIC Insurance Data Security Model Law (#668) §3K "Nonpublic Information": information that can
  identify a consumer by "name, number, personal mark, or other identifier" **in combination with**
  (a) SSN, (b) **driver's license number** or non-driver ID, (c) account number, credit/debit card
  number, (d) security/access codes, (e) biometric records; and (3) **any health information**
  ("except age or gender") from a provider or consumer about health condition, care or payment.
  https://content.naic.org/sites/default/files/model-law-668.pdf
- Sensitive in P&C extracts (pack PII roles): policyholder/claimant/driver name, street address,
  phone, email, DOB (WM: stored per driver [WM p.37]), SSN, driver licence number, bank/card
  account, VIN and licence plate (vehicle identifiers; HIPAA lists them as identifiers in the
  healthcare research; **unverified** as an insurance-law category), claim narrative/notes
  (free text with injuries). Bodily-injury, WC and disability claims hold health data (§3K(3)).
- Quasi-identifiers: ZIP/territory + DOB + gender (see healthcare research §1.3 uniqueness note).
- Protected attributes (gender, marital status, race, religion, national origin) are
  regulatory-sensitive in use, not only privacy-sensitive (§4).

---

## 8. P&C claims vs health-payer claims

Shared: claim_id, paid amount, status, dates. Distinguishing P&C columns:
- **accident/loss date** + report date + accident year [WM pp.2–3]; **case reserve / outstanding**,
  incurred, IBNR [WM p.3]; **policy number/term** with effective/expiration [WM p.38]; coverage
  (BI, PD, collision, comprehensive), **peril/cause of loss**, cat/event id [WM p.40];
  **ALAE**, salvage/subrogation [WM pp.40–41]; claimant + feature [FR ch.3]; VIN/property address;
  exposure (car-years) on the policy side.
Distinguishing health-payer columns (from the healthcare research doc,
`docs/research/2026-11-03-healthcare.md`): member/subscriber id, service from/through dates,
CPT/HCPCS procedure codes, ICD-10 diagnosis, MS-DRG, NPI/provider, place of service, revenue
code, billed/allowed/paid, claim frequency code (original/replacement/void), member-months.
→ Detection rule: a reserve/outstanding column, loss/accident date, peril/coverage or exposure
(car/house-year) ⇒ P&C; CPT/ICD/DRG/NPI/member ⇒ healthcare. Workers comp and auto BI can carry
ICD codes **and** reserves: reserve + loss date wins (P&C), with health data flagged sensitive.

---

## Implications for the pack

**Roles** (declared in YAML): policy_id, claim_id, claimant_id, feature/coverage, line_of_business,
state/territory, effective_date, expiration_date, transaction_date, loss_date (accident),
report_date, close_date, valuation_date (snapshot_key), claim_status (reserved status role),
written_premium, earned_premium, exposure (with unit), paid_loss, case_reserve (a **level**:
`over_time: last` per claim at the latest valuation), incurred, alae, salvage_subrogation,
cat_flag/event_id, gross/net flag. PII: name, address, DOB, licence, VIN, plate, SSN, account.

**KPIs** (always state inputs):
- Earned premium = Σ WP × (days of term in period ÷ term days) (or stored EP).
- Loss ratio = (Σ paid + Σ latest case reserve) ÷ earned premium, by AY; label "case-incurred,
  ex-IBNR, as of <valuation date>".
- Frequency = distinct reported claims ÷ earned exposure (state CNP in/out; claim vs claimant).
- Severity = incurred ÷ reported claims; paid severity = paid on closed ÷ closed claims.
- Pure premium = incurred ÷ earned exposure; average premium = premium ÷ exposure (same basis).
- Closure rate = closed ÷ reported; CWP share = closed with no payment ÷ closed.
- Retention = renewed ÷ eligible for renewal (count basis stated).

**Traps to plant in synthetic data (and how to compute truth):**
1. Transaction-level claims (claim id repeats) → truth: count distinct claim ids.
2. Multiple valuation snapshots with case reserves → truth: reserve at latest valuation per claim;
   summing all snapshots must be wrong by a known multiple.
3. Cancellation rows with negative WP/exposure → truth: net sum; policy count = distinct ids.
4. Only written premium + dates → truth: daily pro-rata EP computed in the generator.
5. Latest AY with a few months of development → truth: lower LR; the report must flag immaturity.
6. Reopened claim with second close → truth: one claim, not two.
7. Salvage/subrogation as negative paid → truth: net paid; gross shown separately if asked.
8. Claimant/feature rows (2 claimants on one claim) → truth: claim count vs claimant count differ.
9. CNP claims (closed, paid = 0) → truth: frequency with and without CNP, both known.
10. Two lines (auto car-years + home house-years) → truth: per-line ratios; no summed exposure.
11. One cat event / one shock loss → truth: LR with and without the event id.
12. Gender column → truth: no rating recommendation; caution text present.

**Caveats** (pack_caveats): loss ratios are case-incurred ex-IBNR; latest year immature; basis
(AY/CY/PY) and valuation date stated; gross/net of reinsurance and S&S; claim counting unit;
mix across lines; nominal dollars, no trend applied.

**Unsupported (say so plainly):** IBNR, ultimate losses, development factors from one snapshot;
reserve adequacy; combined/expense/operating ratios without expense data; rate indications;
causal effect of rating factors; life mortality/A-E and reserves; reinsurance pricing.
