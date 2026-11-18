# Logistics domain research: data shapes, metric definitions, traps, reporting rules (2026-11-18)

Purpose: exact, citable rules for the logistics domain pack (Phase 5.7 Domain 8).
Method: primary sources first. The X12 rules come from published trading-partner implementation guides:
Best Buy 214 v4030, C.H. Robinson dray 214, FedEx 210/820 v4060. The event model is GS1 EPCIS 2.0.
Carrier and aggregator tracking schemas come from the EasyPost and Shippo docs, and dimensional weight
from FedEx. Detention comes from the US DOT Office of Inspector General report and 49 CFR 395 (HOS).
On-time definitions come from SAP Help, and the SCOR level-1/level-2 metric names from a SCOR quick
reference card. Every claim carries a key.
**unverified** = no primary source could be read, so treat it as analyst convention, not a rule.
**snippet** = wording from a search result of the named page; the page itself was not read.

Short source keys:
- B214 = https://partners.bestbuy.com/documents/20126/104763/214+Transportation+Carrier+Shipment+Status+Message.pdf (Best Buy X12 214 v4030 guide, modified 5/3/2023; read in full via pdftotext)
- C214 = https://www.chrobinson.com/en-us/-/media/ChRobinson/documents/carrier-specs-docs/CHR%20Dray%20Carrier%20X12-214.pdf (C.H. Robinson dray/intermodal 214 spec, 5/30/2017; read)
- FX210 = https://www.fedex.com/content/dam/fedex/us-united-states/services/FedEx_210-820_X12-4060_EDI_Implementation_Guide-January2022.pdf (FedEx 210 invoice / 820 remittance guide; read)
- KR210 = https://edi.kroger.com/EDIPortal/documents/Maps/ht/HT_210_X12_4020_Guidelines.pdf (Harris Teeter 210; snippet: fuel as L1 code 405)
- S210 = Stedi-hosted 210 guides (stedi.com/app/guides/...; JS-rendered, snippet: "total invoicing cost in the L3 segment, accessorial charges per stop in the L1 segment")
- S214 = Stedi-hosted 214 guides (AAFES, Blue Diamond; JS-rendered, snippet)
- W945 = Infoplus / Cleverence 940/945 mapping docs (snippet: W12-02 qty ordered, W12-03 qty shipped, W12-04 difference, W12-01 CC/CP)
- EPC = https://ref.gs1.org/standards/epcis/ (GS1 EPCIS Standard 2.0, ratified Jun 2022; PDF read, §7.4.1, §7.4.1.1, §7.4.1.2)
- SSCC = https://www.gs1au.org/standards/id-keys/sscc and GS1 UK logistics-label guide (snippet; gs1.org page not read)
- EP = https://docs.easypost.com/docs/trackers (read)
- EPS = https://support.easypost.com/hc/en-us/articles/360044353091 (snippet: failed attempt stays in_transit; finalization after 3 months)
- SH = https://docs.goshippo.com/tracking/tracking.md (read)
- FXT = FedEx Track API / supply-chain tracking samples (snippet: PU picked up, OD on vehicle for delivery, DL delivered, DE delivery exception; developer.fedex.com is JS-rendered)
- FXD = https://fedex.com/en-ca/resources-tools/dimensional-weight.html (read)
- FXS = FedEx transit-time page / Service Guide (snippet: "In the event of a conflict between the FedEx Service Guide and the transit time on fedex.com … the FedEx Service Guide will prevail")
- UPSF = https://www.ups.com/us/en/support/shipping-support/shipping-costs-rates/fuel-surcharges (403; regional ups.com copies, snippet)
- OIG = https://www.oig.dot.gov/sites/default/files/FMCSA%20Driver%20Detention%20Final%20Report.pdf (DOT OIG ST2018019, 31 Jan 2018; read)
- HOS2 = 49 CFR 395.2 via https://www.law.cornell.edu/cfr/text/49/395.2 (ecfr.gov blocked fetch; read)
- HOS3 = 49 CFR 395.3 (snippet: 14 consecutive hours after coming on duty; 11 hours driving)
- SAPH = https://help.sap.com/saphelp_sbo901/helpdata/en/10/287015187c4053ad399c47fba74c23/content.htm (SAP Business One delivery performance; read)
- SAPB = SAP Community blog "Get more insights into delivery performance: … on-time in-full" (403; snippet)
- ARB = SAP Ariba support item 201567 (snippet: on time if receipt date ≤ requested; in full if received = requested)
- SCORQ = SCOR quick reference card (Supply Chain Council), third-party mirror chbstp.ir (read: metric names/codes only)
- SCMR = Supply Chain Management Review, "Secrets to successful order fulfillment" (snippet: SCC perfect order wording)
- WMT = Walmart OTIF program: SPS Commerce "Walmart OTIF Fundamentals", Supply Chain Dive 2017/2019 (secondary; Walmart's own Retail Link docs need login) = **unverified**
- NMFC = NMFTA freight classification via carrier/broker pages (secondary, snippet: 18 classes 50–500; density, stowability, handling, liability)
- DPD = https://dpd.co.uk/dpo-privacy-notice-1.jsp (read)
- GDPR = https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32016R0679 (Art. 4(1); same key as the IoT note)
- PEP = https://peps.python.org/pep-0495/ (DST fold/gap; same key as the IoT note)

Fetch notes: the ASC X12 standard itself (code lists for elements 1650/1651/623) is paywalled. Code
meanings come from trading-partner guides that print them (B214, C214), and the full 44 status codes /
86 reason codes / 51 time codes were not read [B214 "Total Codes: 44 / 86 / 51"]. The DoD 214 convention
(ustranscom.mil) and Kroger 214 PDFs did not download (an HTML block page and a timeout). Stedi guides are
JS-rendered and unreadable by fetch. ASCM SCOR-DS (scor.ascm.org), the ASCM Dictionary, the CSCMP glossary,
APQC Open Standards and WERC DC Measures are login- or paywalled: definitions of perfect order, fill rate
and dock-to-stock rest on snippets or convention, as marked. Walmart's OTIF policy lives in Retail
Link (login), and the public figures conflict by year (§3). FMCSA's detention page and the ecfr.gov site
returned 403/redirect. The DOT OIG report (which restates the 2014 FMCSA study) and a Cornell LII
copy of the CFR were read instead. developer.fedex.com, developer.ups.com and the UPS fuel page are
JS-rendered or 403. Census CFS / BTS methodology pages were not read in this pass.

---

## 1. Grain of typical extracts

- **Shipment-level TMS export** (one row = one shipment / load / BOL / PRO): ids `shipment_id`, `load_id`,
  `bol`/`bill_of_lading`, `pro_number`, `order_id`/`po_number` (one shipment may carry many orders, and one
  order may ship as several shipments); `carrier`, `scac`; `mode` (TL/FTL, LTL, parcel, intermodal, ocean, air);
  `origin_city/state/zip`, `dest_*`, `lane`; `ship_date`/`pickup_date`, `requested_delivery_date`,
  `promised_date`/`committed_date`/`must_arrive_by`, often as a window (`delivery_window_start/_end`),
  `actual_delivery_date`/`delivered_at`; `weight_lbs`/`weight_kg`/`billed_weight`, `pieces`/`pallets`/`cartons`,
  `freight_class`; `linehaul`, `fuel_surcharge`, `accessorials`, `total_cost`/`freight_cost`, `currency`; `status`
  (**unverified** as a canonical list: names vary by TMS). Bill-of-lading minimum contents: "names of the shipper
  and receiver, origin and destination points, number of packages, description of freight, and the weight,
  volume, or measurement of the cargo" [OIG fn10]. A multi-stop load reports status per stop: "three 214's
  would be required to report a status(s) for each stop of a 3-stop load" [C214].
- **Event / tracking feed** (one row = one scan or status event per shipment/package):
  - X12 214: `AT701` shipment status code, `AT702` reason code (send `NS` Normal Status / `NA`), `AT703`
    appointment status code, `AT705` date CCYYMMDD, `AT706` time HHMM[SS], `AT707` time code; "Only one of
    AT701 or AT703 may be present" [B214]. Location in `MS1` city/state/country/postal [B214]. Codes [C214]:
    X3 Arrived at Pick-up Location; AF Carrier Departed Pick-up Location with Shipment; X1 Arrived at
    Delivery Location; D1 Completed Unloading at Delivery Location; CD Carrier Departed Delivery Location;
    I1 In-Gate; OA Out-Gate. AT703 appointment codes: AA Pick-up Appointment Date and/or Time, AB Delivery
    Appointment Date and/or Time [C214]. AG as a *status* = estimated delivery (S214, snippet).
  - Aggregator APIs. EasyPost `status` ∈ unknown, pre_transit, in_transit, out_for_delivery, delivered,
    available_for_pickup, return_to_sender, failure, cancelled, error, plus 29 `status_detail` values
    (e.g. delayed, delivery_exception, lost, damaged, refused, weather_delay, missorted) [EP]. Shippo:
    PRE_TRANSIT, TRANSIT, DELIVERED, RETURNED, FAILURE, UNKNOWN with substatuses; FAILURE "does not indicate a
    technical, but a delivery issue"; UNKNOWN = "not been found via the carrier's tracking system" [SH].
    FedEx event types PU / OD / DL / DE [FXT, snippet].
  - GS1 EPCIS (serialized / pallet-level visibility): events with `eventTime`, `eventTimeZoneOffset`,
    `recordTime`, `eventID`, optional `errorDeclaration` [EPC]. Logistic units are identified by SSCC:
    18 digits = extension digit + GS1 company prefix + serial reference + check digit, carried in GS1-128 behind
    AI (00) [SSCC, snippet].
- **WMS order-line / fulfillment** (one row = one order line, or one line × shipment): `order_id`,
  `line_no`, `sku`, `qty_ordered`, `qty_shipped`, `qty_backordered`/`qty_short`, `uom`, `pick_ts`/`pack_ts`/
  `ship_ts`. X12 945 W12: W12-01 line status (CC complete / CP partial), W12-02 quantity ordered, W12-03
  quantity shipped, W12-04 difference, W12-05 unit of measure (EA) [W945, snippet]. One order line shipped
  in two cartons on two days appears twice: the grain is line × shipment.
- **Dock / yard appointments** (one row = one appointment / visit): `appointment_ts`, `checkin_ts`/`arrived_at`,
  `door`, `dock_in`, `dock_out`/`departed_at`, `carrier`, `trailer_no`, `load_type` (live / drop). Dwell =
  departed − arrived. Detention needs a free-time rule (§2.7). X12 214 carries appointments in AT703/AA/AB
  [C214], so arrival vs appointment can be computed from 214 too.
- **Freight invoice / audit** (one row = one invoice line, one shipment, or one charge): X12 210 total in
  `L3`, charges in `L1` lines with a special-charge code `L108` [S210, FX210]. FedEx: `L104` Charge
  Amount "will be negative in the case of discount"; "To obtain total tracking number charges, add all L104
  charges"; "Supplemental charges for Ground shipments may appear on a different invoice/file than the
  freight charges"; "Zero amount charges are possible in the event fees are waived" [FX210]. `L119` Percent
  = "Fuel Surcharge Factor"; `L120` Billed Currency Code; `CD301` 'B' Billed Weight; `L011` Weight Unit
  'L' Pounds / 'K' Kilograms (International) [FX210]. Amounts are type N2 (implied 2 decimals): the sample
  `L1****1660****080` is the freight charge, `L1****75****405****010*******11.5*USD` a fuel surcharge at
  factor 11.5, `RES` a residential surcharge, `DSC` a discount of −1010 [FX210]. KR210 sends fuel as code 405,
  while other guides use `FUE` (snippet) [KR210, S210]. Charge codes are partner vocabularies.

## 2. Definitions (exact)

### 2.1 On-time delivery (OTD)
- SAP: on time = "sales orders with actual delivery date before or equal to the scheduled delivery date";
  delayed = "actual delivery date after the scheduled delivery date"; delay days = "number of days passed
  from the delivery due date to the actual delivery date" [SAPH]. **Early counts as on time** in SAP's
  definition. SAP's average delay = "Sum of actual delay days / total number of delayed sales orders"
  (denominator = delayed orders only) [SAPH].
- SAP Ariba (inbound/procurement): on time if receipt date ≤ requested date [ARB, snippet].
- Retailer compliance programs (Walmart OTIF) count **early and late** as misses against a must-arrive-by-date
  (MABD) window [WMT, **unverified**].
- Reference date variants: requested (customer ask), promised/committed (seller confirm, SCOR "Customer
  Commit Date" [SCORQ]), carrier commitment / service standard (FedEx guarantee, governed by the Service
  Guide [FXS]), or appointment (AT703 AB [C214]). These give different numbers for the same shipments.
  State which date.
- Counting unit: shipment, order, order line or unit (pieces/cases). Denominator: the shipments with a
  due date in the period, or the shipments delivered in the period. The two disagree whenever shipments are
  still open (§4 trap 7) (**unverified**, convention).
- Undelivered shipments: pooled correctly, a shipment past its due date with no delivery event is already
  late. Excluding it ("delivered-only") inflates OTD (**unverified** as a sourced rule; follows from the SAP
  definition, since actual > scheduled once the due date passes). Shipments not yet due are excluded and
  should be counted and reported. Tracker states are not terminal: an EasyPost failed attempt with a retry
  "stays in_transit"; trackers with no updates are finalized only after 3 months [EPS, snippet].
- Granularity: date vs timestamp. "Late by one minute" exists only for timestamp windows (appointments,
  time-definite services). Day-level OTD compares dates in the consignee's local zone (**unverified**).

### 2.2 OTIF
- SAP (ByDesign) order-level OTIF: the order is compared against requested date and quantity; it appears
  only after ≥ 1 outbound delivery; if any item is late or partial the order is "not achieved"; item-level
  OTIF also exists (3-item order, 1 late item → 67% item-level, 0% order-level) [SAPB, snippet].
- In full = delivered qty ≥ ordered qty (Ariba: received = requested) [ARB, snippet]. Over-delivery
  handling is partner-specific (**unverified**).
- Walmart: OTIF measured per PO; on time against an MABD-based window, in full against cases ordered;
  prepaid vs collect differ (collect = ready for pickup on time); targets reported as 98% (2020), revised
  in 2024 to 90% on time / 95% in full per SPS Commerce; 3% COGS fine on non-compliant cases [WMT,
  **unverified**, sources conflict by year].
- Order-level OTIF ≤ line-level OTIF ≤ unit-level OTIF for the same data (an order fails if any line fails)
  (follows from the definitions [SAPB]). Name the level.

### 2.3 Perfect order (SCOR)
- SCOR level-1 Reliability metric RL.1.1 Perfect Order Fulfillment. Its level-2 components are RL.2.1 % of
  Orders Delivered In Full, RL.2.2 Delivery Performance to Customer Commit Date, RL.2.3 Documentation
  Accuracy and RL.2.4 Perfect Condition [SCORQ]. RS.1.1 Order Fulfillment Cycle Time sits under
  Responsiveness [SCORQ].
- An order is perfect only if it meets all components (delivered complete, on the commit date, with
  accurate documentation, undamaged) [SCMR, snippet]. Compute it per order. A product of the four
  component rates is an approximation that assumes independence (**unverified**, convention).

### 2.4 Fill rate (three different numbers)
- Unit fill = Σ units shipped ÷ Σ units ordered. Line fill = lines shipped complete ÷ lines ordered. Order
  fill = orders shipped complete ÷ orders. Some firms use case fill (**unverified**: the ASCM Dictionary was
  not read; secondary glossaries agree that these variants exist and differ).
- First-shipment fill (from stock at the first ship attempt) ≠ eventual fill after backorders ship
  (**unverified**). From a 945: unit fill = ΣW12-03 / ΣW12-02 [W945, snippet].
- Ordering: order fill ≤ line fill. Unit fill can be above or below line fill (**unverified**, follows from definitions).

### 2.5 Transit time and cycle times
- Transit time = delivery event − start event. The start can be the tender, the pickup scan (X3 arrival or
  AF departure [C214]; FedEx PU [FXT]) or the label creation (pre_transit [EP]). Name it, because
  "pre_transit" time is not carrier time.
- Calendar vs business days: carrier service standards are stated in business days (FedEx Ground "1–5
  business days" [FXS, snippet]). Business-day transit excludes weekends and carrier holidays, and needs
  a holiday calendar (**unverified** wording: Service Guide not read).
- Order cycle time (SCOR RS.1.1 Order Fulfillment Cycle Time [SCORQ]) = order receipt → customer receipt,
  covering pick/pack/ship plus transit. Do not confuse it with transit time.
- Dock-to-stock = receipt at dock → available in inventory (put away, system-available) (**unverified**:
  WERC not read).
- On-time pickup = actual pickup (X3/AF) vs scheduled pickup / AT703 AA appointment [C214]. Arrival vs
  departure at pickup gives different answers.

### 2.6 Cost metrics
- Cost per shipment = Σ freight cost ÷ shipments. Cost per lb = Σ cost ÷ Σ weight. Per cwt = Σ cost ÷
  (Σ weight / 100). Per mile = Σ cost ÷ Σ miles. Always pooled ratios, never means of per-shipment ratios
  (§4 trap 1).
- Weight basis: actual vs billed. Billed = greater of actual and dimensional; dim = L×W×H in inches ÷ 139
  (or cm ÷ 5,000 per kg), dimensions rounded up to the next whole inch, result rounded up to the next whole
  lb/kg [FXD]. FedEx folds "dimensional weight" corrections into the freight charge [FX210]. Contracted
  divisors can differ (secondary, snippet).
- LTL is rated per hundredweight by NMFC class: 18 classes, 50–500, set by density, stowability, handling
  and liability [NMFC, snippet]. A cost-per-lb comparison across classes or modes compares different
  products.
- Cost components: linehaul / base freight, fuel surcharge, accessorials (residential, liftgate,
  detention, hazmat…), discounts (negative) [FX210]. Fuel surcharge = index % × transportation charges,
  reset weekly (Mondays) from the EIA national on-highway diesel price two weeks prior (UPS ground) or Gulf
  Coast jet fuel (air) [UPSF, snippet]. A cost trend can be a diesel trend.
- Freight cost as % of sales = freight cost ÷ net sales for the same orders/period (**unverified**,
  convention). Mismatched periods (invoice date vs ship date) distort it.

### 2.7 Dwell, detention, free time
- Dwell = "the total amount of time spent at a shippers' or receivers' facility, including time spent
  loading and unloading". Detention = "any time spent waiting at a shipper or receiver's facility that is
  not spent performing these tasks" [OIG].
- Contractual free time is typically 2 h: "may entitle carriers to fees for any additional time spent at
  facilities, typically 2 hours"; and "an informal industry consensus that 2 hours is generally a
  reasonable estimate of time required for loading and unloading" (11 of 12 stakeholders) [OIG].
- FMCSA 2014 study: "defined detention time as all time spent at a stop in excess of 2 hours"; detention
  at "approximately 1 in 10 stops for an average duration of 1.4 hours"; medium carriers ~2× large; TL >
  LTL and private; reefer longer and more frequent [OIG]. The OIG called the data "likely unrepresentative".
- Billable detention = max(0, dwell − free time), usually clocked from appointment time or arrival,
  whichever is later, when the carrier is on time (**unverified**, tariff convention). Early arrivals start
  the clock at the appointment.
- Measurement bias: "carriers and drivers typically only track the amount of time spent at facilities that
  exceeds contractual thresholds" [OIG]. Accessorial-invoice detention data is censored below 2 h.
- HOS: on-duty time includes waiting "at a plant, terminal, facility, or other property of a motor carrier
  or shipper … waiting to be dispatched" and "attending a commercial motor vehicle being loaded or
  unloaded, remaining in readiness to operate" [HOS2]. Driving must stop 14 consecutive hours after coming
  on duty; 11 h driving max [HOS3, snippet]. Detention consumes the 14-h window.
- OIG model: a 15-minute increase in average dwell time → +6.2% expected crash rate; detention ≈ $1.1–1.3 B/yr
  lost earnings for for-hire drivers [OIG]. This is context only.

### 2.8 Rates on events, claims, tenders
- Exception rate = shipments with ≥ 1 exception event ÷ shipments (per shipment, not per event row).
  Exceptions live in reason codes (AT702, e.g. AF Accident, AG Consignee Related, D1 Carrier Dispatch Error,
  P1 Processing Delay, CA Customs [C214]) or in status_detail values (delivery_exception, weather_delay
  [EP]). Consignee-caused vs carrier-caused is a reason-code split (**unverified** as a standard taxonomy).
- Damage / claims rate = shipments (or units/value) with a damage claim ÷ shipments delivered (**unverified**:
  claim filing lags delivery by weeks, so recent periods look clean).
- Tender acceptance rate = loads accepted by the primary (routing-guide) carrier ÷ loads tendered to it
  (**unverified**: CSCMP glossary not read). Count tenders, not loads, if a load is re-tendered.
- Carrier scorecard: OTD, OTP, tender acceptance, invoice accuracy, claims, each pooled per carrier with n
  shown (**unverified**, convention).

## 3. Benchmarks and targets (context only, never a verdict)

- Detention free time 2 h is a contract convention [OIG], not a regulation. HOS sets no free time
  [HOS2].
- Walmart OTIF targets: 98% (2020 announcements), revised 2024 (90% on time prepaid / 95% in full per SPS
  Commerce), 3% COGS fine [WMT, **unverified**, sources conflict]. Never present as an industry benchmark.
- Carrier service standards (1–5 business days Ground) are carrier promises, governed by the Service Guide
  [FXS]. Some guarantees are suspended in peak (secondary, snippet).
- SCOR publishes benchmark data only to members (**unverified**). No public "world-class OTIF" figure was found
  in a primary source.

## 4. Traps (with the authority that warns)

1. **Mean of per-shipment / per-carrier percentages** instead of pooled numerators ÷ denominators. Cost
   per lb averaged per shipment over-weights light parcels. On-time % averaged per lane ignores lane
   volume (**unverified**, same rule as the IoT note §2.6).
2. **Event times in local time without a zone.** X12: "If AT707 is not present then AT706 represents local
   time of the status"; Best Buy requires LT at delivery and shipping locations [B214]; C.H. Robinson: "Date
   and Time are expressed as local time. DO NOT adjust for time zones" [C214]. A coast-to-coast shipment in
   local times can show delivery "before" pickup or a negative/short transit. EasyPost assigns "the
   appropriate local time zone" when location is known, "When location information is incomplete,
   timestamps default to UTC" [EP]: one feed mixes local and UTC. Shippo samples mix `Z` and naive
   timestamps [SH]. EPCIS `eventTime` is absolute, and `eventTimeZoneOffset` carries the local offset
   "to determine whether an event took place during business hours" [EPC]. Normalise to UTC with the
   event location's zone, keep the local time for business-hours logic, and watch DST folds [PEP].
3. **The same code means different things by element.** In C214, AF = Departed Pick-up (AT701) but Accident
   (AT702); D1 = Completed Unloading (AT701) but Carrier Dispatch Error (AT702); AG = estimated delivery
   (AT701, S214 snippet) but Consignee Related (AT702); AA = Pick-up Appointment (AT703) but Mis-sort (AT702)
   [C214]. Code columns must be interpreted with their element. Partners also send subsets and
   partner-specific sequences [S214].
4. **Duplicate and corrected events.** EPCIS corrections are new events: an ErrorDeclaration event is
   "otherwise identical to a prior event"; "The prior event is not modified in any way, and subsequent
   queries will return both" [EPC]. 214s are retransmitted (one per stop per status [C214]), so the same
   (shipment, code, time) can recur. Deduplicate on (shipment, status code, location, event time). Use the
   **first** pickup event and the **last** delivery event (**unverified**, convention).
5. **Multi-stop / multi-leg double counting.** Each stop has its own 214 [C214]. A load with 3 drops gives
   3 delivery events. Joining shipments to events, or to invoice lines (one L1 per charge [FX210]),
   multiplies weight and cost rows. Fix the grain before summing.
6. **Split shipments and partial deliveries vs OTIF.** One order → several shipments. Order-level OTIF needs
   the last delivery date and Σ shipped vs ordered. SAP lists an order only after ≥ 1 delivery [SAPB,
   snippet]: orders with zero deliveries are invisible there.
7. **In-transit / undelivered shipments in on-time %.** Delivered-only exports are survivorship-biased:
   past-due open shipments are the late ones (§2.1). Tracker finalization takes months [EPS]. Report
   open-past-due as late (or separately), and open-not-due as excluded with a count.
8. **Cancelled, returned, refused.** Status return_to_sender / RETURNED / refused [EP, SH] are not on-time
   deliveries. Cancelled before pickup leaves the denominator, and that should be stated (**unverified**,
   convention).
9. **Early deliveries.** SAP counts early as on time [SAPH]. Retail MABD windows count early as a miss [WMT,
   unverified]. Without a stated window the sign of "on time" is ambiguous.
10. **Promised windows vs points.** A promise may be a window (start/end) or a date. A timestamp after
    window_end on the same date is late at timestamp grain and on time at date grain (**unverified**).
11. **Business vs calendar days; weekends; holidays.** Service standards are business days [FXS]. A Friday
    ship with no weekend service adds 2 calendar days and 0 business days (snippet). Calendar-day transit
    comparisons across ship weekdays are biased.
12. **Weight units and weight basis.** lbs vs kg in one file (`L011` 'L'/'K' [FX210]); EasyPost tracker
    weight in **ounces** [EP]; actual vs billed (dim) weight [FXD, FX210]. Cost per lb on billed weight
    ≠ on actual.
13. **Implied decimals and signs on invoices.** X12 N2 amounts: `1660` = 16.60 [FX210]. Discounts are
    negative L104 lines [FX210]. Summing raw EDI amounts overstates cost ×100, and dropping negatives
    overstates net cost.
14. **Late-arriving charges.** Ground supplemental charges "may appear on a different invoice/file" [FX210].
    Recent periods understate cost until audit closes (**unverified** for the lag size).
15. **Fuel surcharge drift.** Index-based, weekly, 2-week lag [UPSF]. Cost changes may be diesel, not
    carrier pricing or performance.
16. **Currency mixes.** `L120` / `C301` billed currency per line [FX210]. Never sum across currencies without a
    dated FX rate.
17. **Accessorials and detention invoices censored at free time** [OIG]. Detention frequency from invoices
    undercounts real waiting.
18. **Exception counted per event row** instead of per shipment (§2.8). Multiple scans of one exception
    inflate the rate.
19. **Tracker statuses that are not outcomes.** unknown / pre_transit = no carrier data yet [EP, SH]. A
    label created but never shipped is not a late shipment.
20. **Causal or blame claims from a carrier correlation.** Lane, mode, region, season and shipper behaviour
    (late tenders, dock delays: AG Consignee Related [C214]) confound carrier comparisons. Small-n carrier
    rates are noisy (**unverified** as a sourced rule; product rule from other domains).
21. **Claims lag.** Damage claims are filed after delivery, so the latest weeks look damage-free
    (**unverified**).

## 5. PII in logistics data

- Personal data = "any information relating to an identified or identifiable natural person … by
  reference to an identifier such as a name, an identification number, location data, an online
  identifier" [GDPR Art. 4(1)].
- DPD (a carrier) lists as recipient personal data: "Full name", "Address (Including country)",
  "Telephone number", "Email address"; delivery history including "Parcel number", "name of sender and
  recipient", "date and time of delivery"; "Proof of delivery notes and or a photo of the delivery
  location"; "A map pinpoint of your address including What3Words location" [DPD]. A carrier therefore
  treats **parcel/tracking numbers as personal data when linked to a recipient**.
- EasyPost exposes `signed_by` = "The name of the person who signed for the package" [EP]. POD signer names
  and signature images are personal data. In the US, a signature is in the California Civil Code §1798.80(e)
  list (**unverified**: leginfo returned 403).
- Consignee names/addresses, phone, email: PII. For a business consignee, the company name is not personal
  data, but a contact name or residential address is (GDPR Art. 4(1)). Residential delivery is flagged
  by the `RES` charge code [FX210], which marks home addresses.
- Driver names, driver IDs, HOS/ELD logs, truck GPS traces: personal data of drivers (location data,
  identifier [GDPR]). Per-driver detention or speed comparisons are employee monitoring, so aggregate to
  carrier/lane.
- License plates / trailer numbers: a plate identifies a vehicle, and through registration a person
  (owner-operators) (**unverified** as a regulator ruling; follows from Art. 4(1)). Trailer and container
  numbers of fleet carriers are not personal data (**unverified**).
- Not personal: SCAC, carrier name, PRO/BOL numbers of B2B freight with no individual consignee, SSCCs,
  facility GLNs, zip3 / city-level geography (**unverified**: deidentification thresholds not read).
- Masking rule for the pack: mask consignee/recipient name, street address line, phone, email, signer,
  driver name/ID, plate; keep city/state/zip3/country, carrier, SCAC; tracking numbers → treat as an
  identifier (hash or mask) when the file also has recipient data [DPD, GDPR].

## 6. Must say / must never say (senior logistics analyst)

Must say:
- The on-time definition: reference date (requested / promised / carrier commit / appointment), window and
  tolerance (early counts? late by minutes or days?), grain (shipment / order / line / unit), and time zone
  basis [SAPH, SAPB, WMT, B214].
- How open shipments were treated: past-due-open counted late or reported separately, with counts;
  not-yet-due excluded with counts; cancelled/returned excluded with counts [EPS, SH].
- The OTIF level (order vs line vs unit) and in-full tolerance [SAPB, ARB].
- The transit start event and calendar vs business days (plus holiday calendar) [C214, FXS].
- Weight basis (actual vs billed/dim), unit conversions applied, and currency handling [FXD, FX210].
- Cost composition: linehaul vs fuel vs accessorials vs discounts, and invoice completeness (late
  supplemental charges) [FX210, UPSF].
- Free-time rule for detention (2 h if not stated, labelled as an assumption) and the clock start [OIG].
- n per carrier/lane next to every rate. Pooled ratios, not averages [§4 trap 1].
- Deduplication and time-normalisation rules applied to event data, with rows affected [EPC, B214].

Must never say:
- "Carrier X is bad/worse" or causes lateness, from observational data, small n, or without lane/mode/
  shipper-delay context; say "associated with" and name the confounders (AG Consignee Related etc.) [C214].
- An on-time % computed on delivered-only rows without saying so.
- A mean of per-shipment or per-lane percentages / cost-per-lb as the overall figure.
- A transit time or delivery-before-pickup from unnormalised local event times [B214, C214, EP].
- Detention frequency from accessorial invoices as total waiting time [OIG].
- Walmart / retailer OTIF targets as an industry benchmark [WMT].
- A cost trend as carrier price change without separating the fuel surcharge [UPSF].
- Any consignee, signer or driver name, address, phone or tracking number tied to a person [DPD, GDPR].

## 7. Column names in exports

| Concept | X12 214 / 210 / 945 | EasyPost [EP] | Shippo [SH] | EPCIS [EPC] | Generic TMS/WMS CSV (**unverified**) |
|---|---|---|---|---|---|
| shipment id | B10 (PRO / BOL ref), L11 refs; FedEx tracking no. on 210 [FX210] | `tracking_code`, `id` | `tracking_number` | SSCC in `epcList`/`parentID` | `shipment_id`, `load_id`, `pro_number`, `bol`, `tracking_number` |
| carrier | SCAC (ISA/GS sender) | `carrier` | `carrier` | `bizLocation`/source-dest | `carrier`, `scac`, `carrier_name` |
| status | AT701 + AT702 reason; AT703 + AT704 appt [B214] | `status`, `status_detail` | `tracking_status.status`, `substatus` | `bizStep`, `disposition` | `status`, `event_code`, `event_desc` |
| event time | AT705 date + AT706 time + AT707 code (LT if absent) [B214] | `tracking_details[].datetime` (local if known, else UTC) | `status_date` (Z or naive) | `eventTime` + `eventTimeZoneOffset`, `recordTime` | `event_ts`, `scan_time`, `event_local_time`, `event_utc` |
| event location | MS1 city/state/country/postal [B214] | `tracking_location` city/state/country/zip | `location` | `readPoint`, `bizLocation` | `city`, `state`, `facility` |
| ETA / promise | AG estimated delivery (status; snippet), AB delivery appointment [C214] | `est_delivery_date` | `eta`, `original_eta` (undefined in docs) | — | `promised_date`, `requested_date`, `mabd`, `window_start/end` |
| weight | L0 L004 weight, L005 qualifier, L011 L/K; CD3 'B' billed [FX210] | `weight` (oz) | — | quantity/UOM | `weight_lbs`, `weight_kg`, `billed_weight`, `dim_weight` |
| charges | L1 L104 amount (N2), L108 code (FUE/405, RES, DSC), L119 fuel %, L120 currency; L3 L305 total [FX210] | — | — | — | `linehaul`, `fuel_surcharge`, `accessorial_*`, `total_charge`, `currency` |
| signer / POD | — (POD on 210 per S210) | `signed_by` | — | — | `pod_name`, `signature`, `received_by` |
| qty ordered/shipped | 945 W12-02 / W12-03 / W12-04 diff; W12-01 CC/CP [W945] | — | — | `quantityList` | `qty_ordered`, `qty_shipped`, `qty_backordered` |

## 8. Word collisions with other domains

| Word | Logistics | Elsewhere in the product |
|---|---|---|
| shipment | one movement under one BOL/PRO/tracking no. | retail order fulfilment (one order); IoT "batch" |
| order | customer/sales order, PO; may split into shipments | trading order (banking); work order (IoT CMMS) |
| line | order line (SKU × qty) | production line (IoT); insurance line of business |
| load | truckload (one tender/trip, may be multi-stop) | ETL load; IoT machine load; electrical load |
| lane | origin–destination pair (zip3/city/region) | none, but "channel" in retail |
| carrier | transport provider (SCAC) | telecom carrier; insurance carrier (insurer) |
| claim | freight loss/damage claim | insurance claim (insurance domain); healthcare claim |
| exception | tracking exception event (reason code) | software exception; banking exception report |
| dwell | time at facility (dwell time) | web session dwell; IoT dwell in a state |
| detention | paid waiting beyond free time [OIG] | (healthcare/legal detention: unrelated) |
| fill rate | demand met from stock (unit/line/order) | HR requisition fill rate; trailer cube fill (logistics too!) |
| cycle time | order-to-delivery (SCOR RS.1.1) | IoT machine cycle time; SaaS billing cycle |
| cwt | hundredweight (100 lb, US) | (UK long cwt = 112 lb: also a trap) (**unverified**) |
| class | NMFC freight class 50–500 [NMFC] | ML class label; insurance class code |
| manifest | carrier manifest (shipments handed over) | software manifest |
| POD | proof of delivery | Kubernetes pod |
| accessorial | extra charge (liftgate, detention, residential) | — |
| on time | vs a stated promise (§2.1) | SaaS SLA uptime; HR attendance |

## 9. Defaults chosen for the pack (where sources disagree)

| Question | Sources | Default |
|---|---|---|
| On-time reference date | requested [ARB] vs scheduled/committed [SAPH, SCORQ] vs MABD window [WMT] | the dataset's promised/committed date if present, else requested; name it |
| Early deliveries | on time [SAPH] vs miss [WMT] | on time (SAP), and report the early count separately; MABD window only when given |
| Grain | order [SAPB] vs item [SAPB] vs unit | shipment-level OTD; OTIF at order level with line-level alongside |
| Open shipments | — | past-due-open = late (reported as a separate count); not-yet-due excluded with count |
| Time basis | local (AT707 LT/absent) [B214, C214] vs absolute (EPCIS eventTime) [EPC] | normalise to UTC using the event location's zone; date comparisons in consignee local date |
| Day grain vs timestamp | — | date grain unless the promise has a time / appointment |
| Transit days | calendar vs business [FXS] | calendar days, with business days alongside when a holiday calendar is known; start = first pickup event |
| Weight | actual vs billed [FXD, FX210] | billed weight for cost/lb when present, else actual; convert kg→lb (2.20462), oz→lb |
| Fill rate | unit / line / order (unverified) | unit fill, plus line fill; label both |
| Perfect order | per-order all-or-nothing [SCMR] vs product of components | per order, all components; never a product of rates |
| Detention free time | contract; typically 2 h [OIG] | 2 h from max(arrival, appointment), labelled an assumption |
| Exception rate | per event vs per shipment | per shipment (≥ 1 exception) |
| Duplicate events | EPCIS ErrorDeclaration [EPC]; 214 resends [C214] | dedupe on (shipment, code, location, time); first pickup, last delivery |
| Invoice amounts | N2 implied decimals [FX210] | detect implied decimals; keep negatives (discounts); sum by currency only |
| Roll-ups | pooled | Σ numerators / Σ denominators always |

## 10. Top 10 facts most likely to change a pack design decision

1. **X12 214 event times are local and carry no zone by default.** "If AT707 is not present then AT706
   represents local time of the status" [B214]; C.H. Robinson says "DO NOT adjust for time zones" [C214].
   EasyPost mixes local and UTC in one feed [EP]. The pack needs a time-normalisation cleaning rule and a
   "delivery before pickup" check in UTC.
2. **Status codes are positional.** AF/D1/AG/AA mean different things in AT701/AT703 vs AT702 [C214]. The
   pack's status vocabulary must key on (column, code), never on code alone.
3. **On-time has no single definition.** SAP counts early as on time [SAPH], while retailer MABD programs
   penalise early [WMT, unverified]. Reference dates differ (requested / committed / carrier / appointment).
   The pack must force a stated reference date and window (a must-say).
4. **OTIF order-level vs item-level differ hugely** (67% vs 0% on the same order [SAPB]). SAP shows an order
   only after its first delivery. Pack KPIs need an explicit level, and zero-delivery orders must be counted.
5. **Delivered-only exports bias OTD upward.** Trackers stay in_transit after failed attempts and finalize
   only after 3 months [EPS]. A KPI rule: past-due-open = late, with counts reported.
6. **Detention = dwell − 2 h free time is a contract convention**, and carriers record only time beyond
   it [OIG]. The 2 h is a labelled assumption. Invoice-derived detention is censored. HOS makes waiting
   on-duty time [HOS2].
7. **Billed weight ≠ actual weight** (dim 139 in³/lb or 5,000 cm³/kg, greater-of, round up [FXD]).
   EasyPost weight is in ounces [EP], and 210 weights are L/K-qualified [FX210]. Cost-per-lb needs a
   stated weight basis and unit-conversion sentinels.
8. **EDI invoice amounts use implied decimals and negative discounts**, and supplemental charges may arrive
   on a different invoice [FX210]. Cleaning must detect ×100 scale and keep negatives, and recent-period
   cost carries a completeness caveat.
9. **Event feeds contain corrections and retransmissions as new rows** (EPCIS ErrorDeclaration leaves the
   original untouched [EPC]; one 214 per stop per status [C214]). The pack needs dedupe keys and a
   first-pickup / last-delivery rule, and multi-stop grain handling.
10. **Tracking numbers, signer names and POD photos are personal data** when linked to a recipient (DPD lists
    "Parcel number" and POD photos [DPD]; EasyPost `signed_by` [EP]; GDPR Art. 4(1)). PII masking must
    cover tracking_number when recipient columns exist, plus signer, driver and plate.

Also near the cut: the fuel surcharge is index-based weekly with a 2-week lag [UPSF], so cost trends need a
fuel split. Perfect order (SCOR RL.1.1) is per-order all-or-nothing over RL.2.1–2.4 [SCORQ, SCMR].
