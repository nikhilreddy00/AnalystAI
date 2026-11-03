# Healthcare domain research: definitions, PHI rules, traps (2026-11-03)

Purpose: exact, citable rules for the healthcare domain pack (Phase 5.7 Domain 3).
Method: primary sources only (CFR text, HHS/OCR, CMS manuals/measure reports, AHRQ HCUP,
CDC NCHS, HFMA, HL7, ResDAC = CMS's data-assistance contractor). Every claim carries its URL.
**unverified** = no primary source could be fetched; treat as analyst convention, not rule.

Fetch notes: hhs.gov and ecfr.gov block automated fetches (403 / bot wall); the CFR text came
from govinfo.gov's official CFR XML (2023 edition) and the HHS guidance from the Internet
Archive copy of the hhs.gov page (2024 snapshot). NUBC's UB-04 manual is licensed; code lists
come from CMS's published variable dictionaries (Blue Button, ResDAC). The current QualityNet
readmission methodology pages render client-side and could not be read; the CMS HWR
methodology cited is the 2011 draft hosted on cms.gov.

---

## 1. HIPAA de-identification (45 CFR 164.514)

Sources: CFR text https://www.govinfo.gov/content/pkg/CFR-2023-title45-vol2/xml/CFR-2023-title45-vol2-sec164-514.xml
(canonical: https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-C/part-164/subpart-E/section-164.514);
OCR guidance https://www.hhs.gov/hipaa/for-professionals/special-topics/de-identification/index.html

### 1.1 Two methods (164.514(b))
- **Expert Determination (b)(1):** a person with appropriate statistical/scientific expertise
  "determines that the risk is very small that the information could be used, alone or in
  combination with other reasonably available information, by an anticipated recipient to
  identify an individual" and documents the methods and results.
- **Safe Harbor (b)(2):** remove the 18 identifiers below (of the individual *or of relatives,
  employers, or household members*) **and** (b)(2)(ii) "the covered entity does not have actual
  knowledge that the information could be used alone or in combination with other information
  to identify an individual."

### 1.2 The 18 Safe Harbor identifiers (164.514(b)(2)(i)(A)–(R), verbatim)
A. Names
B. All geographic subdivisions smaller than a State, including street address, city, county,
   precinct, zip code, and their equivalent geocodes, except for the initial three digits of a
   zip code if, according to the current publicly available data from the Bureau of the Census:
   (1) the geographic unit formed by combining all zip codes with the same three initial digits
   contains more than 20,000 people; and (2) the initial three digits of a zip code for all such
   geographic units containing 20,000 or fewer people is changed to 000.
C. All elements of dates (except year) for dates directly related to an individual, including
   birth date, admission date, discharge date, date of death; and all ages over 89 and all
   elements of dates (including year) indicative of such age, except that such ages and elements
   may be aggregated into a single category of age 90 or older
D. Telephone numbers
E. Fax numbers
F. Electronic mail addresses
G. Social security numbers
H. Medical record numbers
I. Health plan beneficiary numbers
J. Account numbers
K. Certificate/license numbers
L. Vehicle identifiers and serial numbers, including license plate numbers
M. Device identifiers and serial numbers
N. Web Universal Resource Locators (URLs)
O. Internet Protocol (IP) address numbers
P. Biometric identifiers, including finger and voice prints
Q. Full face photographic images and any comparable images
R. Any other unique identifying number, characteristic, or code, except as permitted by (c)

### 1.3 OCR guidance points that matter for a data tool (HHS URL above)
- **Dates:** "Elements of dates that are not permitted for disclosure include the day, month,
  and any other information that is more specific than the year of an event." "January 1, 2009"
  → "2009".
- **Lab/test dates are PHI:** "Dates associated with test measures, such as those derived from a
  laboratory report, are directly related to a specific individual ... Such dates are protected
  health information." So service dates, lab collection dates, claim service dates all count.
- **Ages 90+:** "Ages that are explicitly stated, or implied, as over 89 years old must be recoded
  as 90 or above." Implied age counts: birth year 1910 + service year 2010 → report birth year as
  "on or before 1920".
- **Partial identifiers are not allowed:** "a data set that contained patient initials, or the
  last four digits of a Social Security number, would not meet the requirement."
- **Hashes are identifiers:** "a code derived from a secure hash function without a secret key
  (e.g., 'salt') would be considered an identifying element." (Relevant to our pseudonyms: they
  must be keyed/random, not a plain hash of the MRN.)
- **Re-identification codes (164.514(c))** are allowed only if "not derived from or related to
  information about the individual" and the mechanism is not disclosed.
- **Actual knowledge:** "clear and direct knowledge that the remaining information could be used,
  either alone or in combination with other information, to identify an individual." Example:
  rare, publicized clinical events. Knowing of re-identification research in general is *not*
  actual knowledge.
- **Restricted ZIP3 list (→ 000):** the guidance's list of 17, "based on the five-digit ZIP Code
  Tabulation Areas created by the Census Bureau for the 2000 Census":
  **036, 059, 063, 102, 203, 556, 692, 790, 821, 823, 830, 831, 878, 879, 884, 890, 893.**
  The guidance says it "will be updated when the Census makes new information available" and that
  entities must use "the most current publicly available Bureau of Census data". As of the
  archived page, OCR had **not** published a 2010/2020-census list. **unverified:** any
  2010/2020-based list; a tool should treat the 17 as the OCR-published minimum and say so.
- Uniqueness evidence (guidance): DOB + gender + 5-digit ZIP is unique for "over 50%" of US
  residents; year of birth + gender + 3-digit ZIP is much lower.

### 1.4 Limited Data Set (164.514(e)) — not de-identified
Removes 16 direct identifiers (names, street address except town/city/State/zip, phone, fax,
email, SSN, MRN, health plan beneficiary no., account no., certificate/license no., vehicle IDs,
device IDs, URLs, IPs, biometrics, full-face photos) but **may keep dates, town/city, State, ZIP
and ages**. It is still PHI, usable only "for the purposes of research, public health, or health
care operations" under a data use agreement. Source: CFR text above.
→ A file with real admission dates but no names is at best an LDS, **not** de-identified.

### 1.5 Date shifting
Safe Harbor permits year only; it does not describe date shifting. Shifted dates are an
Expert Determination technique (OCR guidance lists suppression, generalization, perturbation).
→ In a de-identified extract, intervals (LOS, days-to-readmit) may be preserved while calendar
dates, seasonality and day-of-week are not real. **unverified** as a general rule: depends on the
expert's method; always ask/caveat.

### 1.6 What is NOT PHI / small cells
- De-identified data is not PHI (164.514(a): "is not individually identifiable health
  information").
- Aggregate statistics are not listed identifiers, but small cells can identify. **CMS cell size
  suppression policy** (ResDAC, https://resdac.org/articles/cms-cell-size-suppression-policy):
  "no cell (e.g. admissions, discharges, patients, services, etc.) containing a value of 1 to 10
  can be reported directly." Zero is allowed. Applies to excluded-case counts too. Complementary
  suppression required: "no cell can be reported that allows a value of 1 to 10 to be derived
  from other reported cells or information"; percentages that reveal such a cell are prohibited.
  → Pack rule: counts of 1–10 patients/encounters are shown as "<11"; a rate whose numerator or
  denominator is 1–10 is not shown or is flagged; check totals don't back out a suppressed cell.

---

## 2. KPIs: formulas, windows, exclusions

### 2.1 Length of stay (LOS) and ALOS
- **HCUP LOS:** "Length of stay (LOS) is calculated by subtracting the admission date (ADATE) from
  the discharge date (DDATE)." Same-day stays are coded **0**. "Leave days are not subtracted."
  Range 0–365 for inpatient data. https://hcup-us.ahrq.gov/db/vars/los/nisnote.jsp
- **Older NCHS convention (differs):** LOS excludes the discharge day, but a same-day stay is
  counted as **1** day of care when averaging (NHDS Series 13 reports, e.g.
  https://www.cdc.gov/nchs/data/series/sr_10/sr10_030.pdf — via search summary; **unverified**
  verbatim). → The tool must state which convention it used; default HCUP (same-day = 0).
- **ALOS:** NCHS Health, United States: "the average number of days a patient stays at a
  facility"; AHA computes it by "dividing the number of inpatient days by the number of
  admissions." https://www.cdc.gov/nchs/hus/sources-definitions/average-length-of-stay.htm
  Analyst default (**unverified** as a federal rule): ALOS = Σ LOS of discharges in period /
  number of discharges in period (discharge-based, so every stay is complete). State admissions-
  vs discharges-based in the caveat.
- **Observation is outpatient, not inpatient:** CMS Benefit Policy Manual ch.6 §20.6 —
  "Observation care is a well-defined set of specific, clinically appropriate services, which
  include ongoing short term treatment, assessment, and reassessment before a decision can be
  made regarding whether patients will require further treatment as hospital inpatients";
  decision usually "in less than 48 hours, usually in less than 24 hours".
  https://www.cms.gov/regulations-and-guidance/guidance/manuals/downloads/bp102c06.pdf
  → exclude observation stays from inpatient ALOS/admissions; report separately.
- **Two-midnight rule (Medicare Part A):** inpatient admission "is generally appropriate ... when
  the admitting physician expects the patient to require hospital care that crosses two
  midnights." 42 CFR 412.3(d)(1).
  https://www.govinfo.gov/content/pkg/CFR-2023-title42-vol2/xml/CFR-2023-title42-vol2-sec412-3.xml

### 2.2 30-day readmission rate
- **CMS HWR outcome:** "a subsequent inpatient admission to any acute care facility which occurs
  within 30 days of the discharge date of an eligible index admission. Any readmission is
  eligible to be counted as an outcome except those that are considered planned." "A
  readmission may in turn serve as an index admission." Planned readmissions are "not counted as
  outcomes". (CMS HWR draft methodology, 2011)
  https://cms.gov/Medicare/Quality-Initiatives-Patient-Assessment-Instruments/MMS/Downloads/MMSHospital-WideAll-ConditionReadmissionRate.pdf
- **Index eligibility (same report):** patient alive at discharge; **not transferred to another
  acute care hospital** ("responsibility for the readmission is assigned to the final discharging
  hospital"); age 65+; 30 days post-discharge FFS enrollment; not a PPS-exempt cancer hospital.
  **Exclusions:** <12 months prior enrollment; **discharged against medical advice (AMA)**;
  rehabilitation (CCS 254); high competing-mortality condition categories.
- **Risk-standardized rate (same report):** per-cohort predicted/expected ratios combined into
  Rj; "this ratio is then multiplied by the overall national readmission rate ... to produce the
  risk-standardized hospital-wide readmission rate." RSRR = (predicted / expected) × national
  observed rate. A crude rate from one file is **not** an RSRR.
- **HRRP** uses six condition/procedure measures (AMI, COPD, HF, pneumonia, CABG, elective
  THA/TKA), "30-day risk-standardized unplanned readmission measures"; penalty capped at 3%.
  https://www.cms.gov/medicare/payment/prospective-payment-systems/acute-inpatient-pps/hospital-readmissions-reduction-program-hrrp
- **HCUP crude all-cause method** (Statistical Brief #248,
  https://hcup-us.ahrq.gov/reports/statbriefs/sb248-Hospital-Readmissions-2010-2016.jsp):
  unit = index stay (a patient can contribute several); readmission to "the same or different
  hospital"; "No more than one readmission is counted within the 30-day period"; index stays
  excluded if "the patient died in the hospital", LOS missing, or "the patient was discharged in
  December" (cannot be followed 30 days); **transfers / same-day stays "were combined as a single
  stay and the second event was not counted as a readmission."** Rate = index stays with ≥1
  readmission / index stays (Jan–Nov).
- **CMS CCW (ResDAC):** readmitted = acute stay with discharge status not expired / not AMA
  starting within 30 days of a previous qualifying stay; index start must be in the file year,
  readmission may fall in January of the next year.
  https://resdac.org/cms-data/variables/acute-inpatient-hospital-readmissions
- **Pack formula (crude):** numerator = index discharges followed by ≥1 inpatient admission with
  0 < (readmit admission date − index discharge date) ≤ 30 days [**unverified**: the day-0 /
  day-30 boundary; HWR says "within 30 days of the discharge date"]; denominator = index
  discharges that are alive (status ≠ 20), not AMA (≠ 07), not transferred to acute care
  (≠ 02, 82 etc.), and whose discharge date ≤ data end − 30 days. Chain transfers into one
  stay first. Label the result "crude, all-cause, not risk-adjusted, planned readmissions not
  removed" unless a planned flag exists. Discharge statuses 81–95 = "with a planned acute care
  hospital inpatient readmission" (see §3.4).

### 2.3 Claim denial rate (HFMA)
- **MAP Key AR-5 Remittance Denial Rate** = Total Number of Claims Denied / Total Number of
  Claims Remitted. **CL-1 Clean Claim Rate** = Claims That Pass Edits Requiring No Manual
  Intervention / Claims Accepted Into Claims Processing Tool For Billing Prior To Submission.
  **AR-6** = Net Dollars Written Off As Denials / Average Monthly Net Patient Service Revenue.
  **FM-1 Net Days in A/R** = Net A/R / Average Daily Net Patient Service Revenue.
  https://www.hfma.org/data-and-insights/map-initiative/map-keys/
- **Initial denial rate (HFMA Claim Integrity Task Force):** by volume = initial denied claims /
  total claims submitted (denominator = average of the 3 months before the reporting month); by
  dollars = gross charges on initial denied claims / gross charges on all claims submitted;
  rebills (bill type xx7) excluded; only the **first** denial per claim counts. "A denied claim
  is one that is received and processed by the health insurance plan and a negative determination
  is made." Line-level variant uses line charges.
  https://www.hfma.org/guidance/standardizing-denial-metrics-revenue-cycle-benchmarking-process-improvement/
- → Always say which: count vs dollars, initial vs final (write-off), claim vs line, and the
  denominator (submitted vs remitted). Zero-paid ≠ denied (contractual/bundled zero pay):
  **unverified** — HFMA text does not address it explicitly.

### 2.4 PMPM and member months
- CMS Medicaid rate guide asks for "projected enrollment in member months by rate cell" and allows
  costs "as per member per month (PMPM)".
  https://www.medicaid.gov/medicaid/managed-care/downloads/2026-2027-medicaid-rate-guide-022026.pdf
  (via search summary). No primary text defining "member month" was found: **unverified** formal
  definition. Convention: 1 member month = 1 member enrolled for a month (from the **eligibility/
  enrollment** table, never from claims, since members with no claims still count).
  PMPM = total paid (or allowed) for the period / member months in the same period.
- **Dollar fields (ResDAC):** total charges = what the provider bills, set by the provider;
  allowed (covered) = amount Medicare recognizes = Medicare payment + other payers + beneficiary
  deductible/coinsurance; payment = Medicare's actual payment.
  https://resdac.org/sites/datadocumentation.resdac.org/files/Definitions%20of%20'Cost'%20in%20Medicare%20Utilization%20Files%20(Slides).pdf
  (via search summary); https://www.resdac.org/cms-data/variables/line-allowed-charge-amount
  ResDAC's teaching example: charges $23,000 vs payments ≈ $8,750.
  → **Billed charges are not cost and not revenue.** Default money KPI = paid (payer view) or
  allowed (total cost of care); never sum charges as "cost".
- Inpatient claim payment excludes per-diem pass-through amounts (ResDAC
  https://resdac.org/cms-data/variables/claim-medicare-payment-amount, via search summary).

### 2.5 Other KPIs (brief)
| KPI | Formula | Source / status |
|---|---|---|
| In-hospital mortality (crude) | discharges with status 20 (expired) / all discharges | status code: Blue Button (§3.4); formula **unverified** (AHRQ IQI are risk-adjusted) |
| ED visits per 1,000 | ED visits / population (or member-years) × 1,000 | **unverified** (convention) |
| Bed occupancy | patient (inpatient) days / (staffed beds × days in period) | old NCHS reports via search; **unverified** verbatim |
| No-show rate | no-show appts / scheduled appts (excl. provider-cancelled) | **unverified** — no federal definition found |
| Clean claim rate | HFMA CL-1 (§2.3) | HFMA |
| Days in A/R | HFMA FM-1 (§2.3) | HFMA |

---

## 3. Grain and codes

### 3.1 Claims
- Header vs line: one claim (header: bill type, statement dates, total charge, discharge status)
  has many lines (revenue code / HCPCS, line charge, line paid). Summing header totals after a
  header–line join multiplies money by the line count. UB-04 FL 47 total charges, FL 42 revenue
  codes (CMS Claims Processing Manual ch.25,
  https://www.cms.gov/regulations-and-guidance/guidance/manuals/downloads/clm104c25.pdf).
- One encounter/stay can have several claims (interim bills, late charges, replacements,
  professional + facility). CCW counts "Number of claims (final action) included in stay".
- **Type of bill frequency digit** (ResDAC CLM_FREQ_CD, "the sequence of a claim in the
  beneficiary's current episode of care"; https://resdac.org/cms-data/variables/claim-frequency-code-ffs):
  0 non-payment/zero; **1 admit thru discharge**; 2 interim–first; 3 interim–continuing;
  4 interim–last; 5 late charge(s) only; **7 replacement of prior claim; 8 void/cancel prior
  claim**; 9 final; G/H/I/J/K/M/P/Q/Y adjustment variants. In X12 837 this is CLM05-3
  (**unverified**: X12 spec is licensed).
  → Keep the latest replacement, drop voided originals; never count 7/8 as new claims.
- Payer adjustments often appear as reversal lines with negative amounts (**unverified** general
  claim; payer-specific). Negative paid = reversal, not a refund trend.
- Claims maturity / run-out: Medicare quarterly files ≈ "93% claims maturity" after ~3 months;
  annual files built "12 months after the close of the calendar year" are "99.9% complete".
  https://resdac.org/articles/rif-medicare-quarterly-data → recent months are incomplete
  (IBNR); flag the last 3 months of paid/claim counts by service date.

### 3.2 Codes
- **ICD-10-CM** "is a standardized system used to code diseases and medical conditions
  (morbidity) data" (CDC NCHS, https://www.cdc.gov/nchs/icd/icd-10-cm/index.html).
  ICD-10-PCS = "the procedure coding system developed by the Centers for Medicare and Medicaid
  Services" (same page); inpatient procedures (**unverified** on that page).
- **HCPCS Level I = CPT**: "a numeric coding system maintained by the American Medical
  Association", "5 numeric digits"; **Level II**: "used primarily to identify products,
  supplies, and services", "a single alphabetical letter followed by 4 numeric digits".
  https://www.cms.gov/medicare/coding-billing/healthcare-common-procedure-system
- **MS-DRG:** "cases are classified into Medicare Severity Diagnosis Related Groups (MS-DRGs) for
  payment under the IPPS" using "the principal diagnosis, up to 24 additional diagnoses, and up to
  25 procedures"; payment "on a rate per discharge basis". Versions change each Oct 1 (FY 2026 =
  v43). → one DRG per inpatient discharge; DRG mix across years needs the same grouper version.
  https://www.cms.gov/medicare/payment/prospective-payment-systems/acute-inpatient-pps/ms-drg-classifications-and-software
- **Revenue codes** (UB-04 FL 42) and **priority (type) of admission** (FL 14) code lists are
  NUBC-licensed; CMS ch.25 defers to NUBC. Commonly 1 emergency, 2 urgent, 3 elective,
  4 newborn, 5 trauma, 9 info not available: **unverified** (NUBC).

### 3.3 Lab results
- One row per result (order → many results), coded by **LOINC**: "a clinical terminology ...
  important for laboratory test orders and results, produced by the Regenstrief Institute"
  (NLM, https://www.nlm.nih.gov/research/umls/loinc_main.html).
- Same test can arrive in different units (e.g. mg/dL vs mmol/L); never average across units.
  Reference ranges are per lab/test/patient group (HL7 "as defined for the respective test
  procedure").
- **Interpretation flags** (HL7 v3 ObservationInterpretation,
  https://terminology.hl7.org/CodeSystem-v3-ObservationInterpretation.html):
  H High ("above the upper limit of the reference range"), L Low, HH Critical high, LL Critical
  low, A Abnormal (typically non-numeric), AA Critical abnormal, N Normal,
  **"<" Off scale low** ("below the minimum detection limit"), **">" Off scale high**.
  → "<5" is censored, not 5 and not missing; keep a censored flag, do not average it as a number.

### 3.4 Discharge status (UB-04 FL 17)
"the patient's discharge status as of the 'Through' date of the billing period" (CMS ch.25).
Codes (CMS Blue Button PTNT_DSCHRG_STUS_CD,
https://bluebutton.cms.gov/resources/variables/ptnt_dschrg_stus_cd/):
01 home/self-care; **02 transferred to other short term general hospital**; 03 SNF;
04 ICF; 05 another type of institution for inpatient care (including distinct parts);
06 home health; **07 left against medical advice or discontinued care**; 09 admitted as an
inpatient to this hospital; **20 expired**; 21 court/law enforcement; 30 still patient;
40/41/42 expired (hospice only); 43 federal hospital; 50/51 hospice home/facility; 61 swing bed;
62 IRF; 63 LTCH; 64 Medicaid-only NF; 65 psychiatric; 66 CAH; 69 disaster site; 70 other;
**81–95 = same destinations "with a planned acute care hospital inpatient readmission"**
(82 = short-term hospital with planned readmission).
→ Status 30 ("still patient") is not a discharge: exclude from LOS/ALOS denominators.

### 3.5 Eligibility / membership
One row per member per enrollment span (or per member-month). It is the denominator for PMPM and
per-1,000 rates. (**unverified**: no federal file-layout source fetched; CMS MBSF is per
beneficiary-year with monthly indicators.)

---

## 4. Traps a senior analyst checks
1. **Adjustments double count** — originals + replacements (freq 7) + voids (8) + negative
   reversal lines. Net to the final version per claim before counting or summing (§3.1).
2. **Header × line fan-out** — header totals repeated on every line (§3.1).
3. **Paid = 0 is a value**, not missing (denied, bundled, or capitated/encounter claims). Missing
   paid ≠ 0. (**unverified** as a federal statement; ResDAC freq code 0 "Non-payment/zero claims"
   shows zero-pay claims are a real class.)
4. **Charges ≠ cost ≠ payment** (§2.4). Never total billed charges as spend.
5. **Claims lag / IBNR** — recent service months incomplete (~93% at 3 months, 99.9% at 12)
   (§3.1). Don't report a "drop" in the latest months as real; use service date with run-out.
6. **Date basis** — service (from/through), admission, discharge, received and paid dates give
   different months. State one basis per KPI. **unverified** (convention).
7. **Readmission window crossing data end** — index discharges in the last 30 days can't be
   followed; exclude them (HCUP drops December).
8. **Transfers ≠ readmissions** — combine transfer chains into one stay (HCUP, CMS HWR §2.2).
9. **Deaths and AMA** excluded from readmission denominators (status 20, 07).
10. **Observation vs inpatient** — observation is outpatient; don't count as admissions/LOS (§2.1).
11. **LOS convention** — same-day = 0 (HCUP) vs 1 (old NCHS); long-stay outliers (HCUP caps 365)
    dominate means: report median alongside mean.
12. **"Still patient" (30)** — open stays have no LOS yet.
13. **Small cells** — 1–10 suppressed (CMS) incl. complementary cells (§1.6).
14. **Ages 90+ aggregated** — "90+" is a category, not 90; don't compute mean age with it as 90
    without saying so (§1.3).
15. **Shifted / year-only dates** in de-identified data — no seasonality or weekday claims (§1.5).
16. **Grouper versions** — MS-DRG changes each Oct 1; ICD-10 annual updates (§3.2).
17. **Case mix / Simpson's paradox** — crude rates across facilities or physicians differ by case
    mix; CMS itself only compares risk-standardized rates (§2.2). Stratify (e.g. by DRG/age)
    before comparing. **unverified** wording (statistical principle).
18. **Lab units and censoring** — mixed units, "<x"/">x" values, flags vs numbers (§3.3).
19. **Denial rate definition drift** — count vs dollar, initial vs final, submitted vs remitted
    (§2.3).
20. **Member months from claims** — wrong; use enrollment (§2.4).

## 5. Must-say caveats / forbidden claims
- Rates from an uploaded file are **crude, not risk-adjusted**; never compare to CMS published
  RSRRs or HRRP results as if equivalent (§2.2).
- **No clinical advice**: no diagnosis, treatment or dosing recommendations for patients.
  (Product rule; no regulatory citation — **unverified** as a legal requirement.)
- **No causal claims** about treatments, providers or programs from observational data
  ("associated with", not "caused/reduced"). (Analyst principle — **unverified**.)
- Say the LOS convention, the date basis, the readmission window and exclusions, the dollar field
  (paid/allowed/charges) and the denial-rate variant used.
- Flag incomplete recent months (run-out) and suppressed small cells.
- Never print identifiers (§1.2) or full dates of birth/service; ages >89 shown as 90+.
- A file with names removed but real dates/ZIPs is still PHI (LDS at best, §1.4).
