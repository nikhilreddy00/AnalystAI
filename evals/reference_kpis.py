"""The independent oracle for spell and ledger KPIs (ROADMAP Phase 5.7b-1, step 2).

Naive, loop-based code a reviewer can check line by line against the definitions in
GLOSSARY.md (Spell, In force at t, As-of date, Ledger, Move, Opening cohort, Month label) and the
SaaS and HR packs' words (domain_packs/saas.yaml, domain_packs/hr.yaml). It is the differential
oracle for the product's KPI engine, so it keeps two independence rules:

1. it never imports the product (`domain_pack`, `agents`);
2. it never imports the data generator (`evals/datagen.py`): its rules come from the
   definitions, not from the generator's truth code.

Frames are read once with `df.to_dict("records")`; everything after that is plain loops and
dicts (no groupby, merge or pivot). `reference(name, df)` returns a dict of named KPIs.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

# HR pack words (hr.yaml): contingent workers are out of headcount and every rate
CONTINGENT = {"contractor", "contingent", "agency", "agency worker", "consultant", "freelancer",
              "vendor", "contract worker", "temp agency"}
VOLUNTARY = {"voluntary", "resignation", "resigned", "quit", "voluntary resignation"}
INVOLUNTARY = {"involuntary", "layoff", "laid off", "dismissal", "dismissed", "discharge",
               "discharged", "redundancy", "redundant", "end of contract",
               "termination for cause", "fired"}
RETIREMENT = {"retirement", "retired", "retire"}
HR_ENDED = ("terminated", "inactive", "separated", "withdrawn", "retired", "deceased")
# SaaS pack words (saas.yaml): the spell's ended statuses, the &paying anchor's excluded
# statuses, the test-account marker and the billing interval in months
SAAS_ENDED = ("canceled", "cancelled", "incomplete_expired", "ended")
SAAS_OUT = ("trialing", "paused", "incomplete", "incomplete_expired", "unpaid")
TEST_MARKER = re.compile(r"(?i)^cus_test|@example\.(?:com|org|net)$|^test(?:@|\+|$|[-_ ]?\d)"
                         r"|@test\.")
INTERVAL_MONTHS = {"month": 1, "year": 12}
MOVES = ("new", "expansion", "contraction", "churn", "reactivation")


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "") or (
        not isinstance(v, str) and pd.isna(v))


def _date(v) -> pd.Timestamp | None:
    return None if _blank(v) else pd.Timestamp(v).normalize()


def _num(v, default: float) -> float:
    return default if _blank(v) else float(v)


def _word(v) -> str:
    return "" if _blank(v) else str(v).strip().lower()


def subscription_mrr(r: dict) -> float:
    """The pack column: price × seats × (1 − discount) ÷ the billing interval in months."""
    months = INTERVAL_MONTHS[_word(r["interval"])] * _num(r.get("interval_count"), 1)
    return (float(r["unit_amount"]) * _num(r.get("quantity"), 1)
            * (1 - _num(r.get("discount_pct"), 0)) / months)


@dataclass(frozen=True)
class Spec:
    entity: str
    money: bool                          # MRR ledger KPIs, else headcount KPIs
    value: Callable[[dict], float]       # an entity row's value (MRR, or 1 for being there)
    start: str | None = None             # spell rows: start / end; snapshot rows: snapshot
    end: str | None = None
    paid_from: str | None = None         # a trial's end: the spell pays from the later date
    snapshot: str | None = None
    month_labels: bool = False           # a snapshot on the 1st names its whole month
    status: str | None = None
    ended: tuple = ()                    # statuses that end a spell (blank end: never in force)
    out_statuses: tuple = ()             # statuses out of the population
    concurrent: bool = False             # every spell in force counts (summed), not the latest
    segment: str | None = None           # moves and rates split by it (department, segment)
    groups: tuple = ()                   # levels split by these row columns
    worker_type: str | None = None       # contingent worker types are out
    category: str | None = None          # a separation's kind (voluntary, retirement ...)
    fte: str | None = None
    currency: tuple | None = None        # (column, the currency kept)
    test_cols: tuple = field(default=())  # columns the test-account marker reads


HR_ROSTER = Spec(entity="Employee_ID", money=False, value=lambda r: 1.0, start="Hire_Date",
                 end="Termination_Date", status="Employment_Status", ended=HR_ENDED,
                 segment="Department", worker_type="Worker_Type",
                 category="Termination_Category", fte="FTE")
SPECS = {
    "saas_subscriptions": Spec(
        entity="customer_id", money=True, value=subscription_mrr, start="created",
        end="ended_at", paid_from="trial_end", status="status", ended=SAAS_ENDED,
        out_statuses=SAAS_OUT, concurrent=True, segment="segment", groups=("segment", "plan"),
        currency=("currency", "USD"), test_cols=("customer_id", "contact_email")),
    "saas_mrr_snapshots": Spec(
        entity="customer_id", money=True, value=lambda r: float(r["mrr"]), snapshot="month",
        month_labels=True, segment="segment", groups=("segment",), test_cols=("customer_id",)),
    "hr_roster": HR_ROSTER,
    "hr_roster_onerow": HR_ROSTER,   # Hire_Date is the latest hire; Original_Hire_Date unread
    "hr_snapshots": Spec(
        entity="Emp_ID", money=False, value=lambda r: 1.0, snapshot="Snapshot_Date",
        end="Termination_Date", status="Status", ended=HR_ENDED, segment="Department",
        worker_type="Worker_Type", fte="FTE"),
    "employees": Spec(entity="employee_id", money=False, value=lambda r: 1.0,
                      start="hire_date", end="termination_date", segment="department"),
    # the property tests' small books (tests/test_properties.py)
    "book_mrr_export": Spec(entity="customer_id", money=True, value=lambda r: float(r["mrr"]),
                            snapshot="month", month_labels=True, segment="segment",
                            groups=("segment",)),
    "book_subscription_list": Spec(
        entity="customer_id", money=True, value=lambda r: float(r["mrr"]), start="created",
        end="ended_at", status="status", ended=SAAS_ENDED, out_statuses=SAAS_OUT,
        concurrent=True, segment="segment", groups=("segment",)),
    "book_hr_snapshots": Spec(entity="employee_id", money=False, value=lambda r: 1.0,
                              snapshot="snapshot_date", segment="department"),
    "book_roster": Spec(entity="employee_id", money=False, value=lambda r: 1.0,
                        start="hire_date", end="termination_date", status="status",
                        ended=HR_ENDED, segment="department"),
}


def prepare(df: pd.DataFrame, spec: Spec) -> list[dict]:
    """One small dict per row: dates parsed, the population decided, the value computed."""
    rows = []
    for r in df.to_dict("records"):
        start = _date(r[spec.start]) if spec.start else None
        if start is not None and spec.paid_from and _date(r[spec.paid_from]) is not None:
            start = max(start, _date(r[spec.paid_from]))
        snap = _date(r[spec.snapshot]) if spec.snapshot else None
        if snap is not None and spec.month_labels:
            snap = snap + pd.offsets.MonthEnd(0)
        end = _date(r[spec.end]) if spec.end else None
        status = _word(r[spec.status]) if spec.status else ""
        value = spec.value(r)
        contingent = spec.worker_type is not None and _word(r[spec.worker_type]) in CONTINGENT
        pop = (status not in spec.out_statuses and not contingent
               and not any(TEST_MARKER.search(str(r[c])) for c in spec.test_cols
                           if not _blank(r[c]))
               and (spec.currency is None or r[spec.currency[0]] == spec.currency[1])
               and (not spec.money or value > 0))
        rows.append({
            "id": r[spec.entity], "start": start, "end": end, "snap": snap, "value": value,
            "undated_end": status in spec.ended and end is None,
            # the start column, the (paid) start and the end: each a start or end date
            "dates": [d for d in (_date(r[spec.start]) if spec.start else None, start, end) if d],
            "pop": pop, "contingent": contingent, "row": r,
            "segment": r[spec.segment] if spec.segment else None,
            "category": _word(r[spec.category]) if spec.category else ""})
    return rows


def as_of(rows: list[dict], spec: Spec, today: pd.Timestamp) -> pd.Timestamp:
    """The latest snapshot, else the latest start or end date not after today."""
    if spec.snapshot:
        return max(r["snap"] for r in rows if r["snap"] is not None)
    return max(d for r in rows for d in r["dates"] if d <= today)


def window(rows: list[dict], spec: Spec, at: pd.Timestamp):
    """(lo, as-of, month_ends): the 12 months to the as-of, or the shorter span since the first
    date the data knows a state (GLOSSARY, Window): the day before the first snapshot (its own
    month-end is a complete stock); spells, the day before the earliest end (a leavers-only list
    misses who left before it), or before the earliest start when nobody ended. month_ends are
    each month's end (the as-of itself for its own month), after lo; flows count in (lo, as-of].
    The ledger's moves and cohort start no earlier than the first snapshot (ledger_kpis)."""
    if spec.snapshot:
        first = min(r["snap"] for r in rows if r["snap"] is not None) - pd.Timedelta(days=1)
    else:
        ends = [_date(r["row"][spec.end]) for r in rows if spec.end and _date(r["row"][spec.end])]
        starts = [_date(r["row"][spec.start]) for r in rows if _date(r["row"][spec.start])]
        first = min(ends or starts) - pd.Timedelta(days=1)
    lo = max(at - pd.DateOffset(months=12), first)
    month_ends = []
    for k in range(11, -1, -1):
        m = min(at - pd.DateOffset(months=k) + pd.offsets.MonthEnd(0), at)
        if m > lo:
            month_ends.append(m)
    return lo, at, month_ends


def in_force(rows: list[dict], spec: Spec, t: pd.Timestamp, keep=lambda r: r["pop"]) -> dict:
    """entity -> its rows in force at t. A snapshot entity's row on the latest snapshot on or
    before t (absent from it = gone); a spell entity's latest spell started by t (every one when
    spells are concurrent); out when its end is on or before t or its status ended undated.
    The population filter (`keep`) applies to the chosen row."""
    if spec.snapshot:
        dates = [r["snap"] for r in rows if r["snap"] is not None and r["snap"] <= t]
        newest = max(dates) if dates else None
        chosen = [r for r in rows if newest is not None and r["snap"] == newest]
    else:
        chosen = [r for r in rows if r["start"] is not None and r["start"] <= t]
        if not spec.concurrent:
            latest = {}
            for r in chosen:   # ties on start: the row that ends later (blank = never)
                key = (r["start"], r["end"] or pd.Timestamp.max)
                old = latest.get(r["id"])
                if old is None or key > (old["start"], old["end"] or pd.Timestamp.max):
                    latest[r["id"]] = r
            chosen = list(latest.values())
    out: dict = {}
    for r in chosen:
        if r["undated_end"] or (r["end"] is not None and r["end"] <= t) or not keep(r):
            continue
        out.setdefault(r["id"], []).append(r)
    return out


def level(force: dict) -> float:
    """MRR (or headcount) of an in-force map: each entity's rows' values summed, then added."""
    total = 0.0
    for rs in force.values():
        for r in rs:
            total += r["value"]
    return total


def _value(rs: list[dict]) -> float:
    return sum(r["value"] for r in rs)


def _segment(rs: list[dict]):
    # one segment per entity at a date; concurrent rows that disagree: the first in sort order
    return sorted(str(r["segment"]) for r in rs)[0]


def by_column(force: dict, col: str) -> dict:
    out: dict = {}
    for rs in force.values():
        for r in rs:
            k = r["row"][col]
            out[k] = out.get(k, 0.0) + r["value"]
    return out


def history(rows: list[dict], spec: Spec, lo, month_ends) -> list[pd.Timestamp]:
    """Ledger points: every month-end from the first known date to lo, lo, the window's
    month-ends. A move's 'in force before' reads every earlier point."""
    first = min(r["snap"] if spec.snapshot else r["start"] for r in rows
                if (r["snap"] if spec.snapshot else r["start"]) is not None)
    points = {lo, *month_ends}
    m = first + pd.offsets.MonthEnd(0)
    while m < lo:
        points.add(m)
        m = m + pd.offsets.MonthEnd(1)
    return sorted(points)


def moves(ledger: dict, points: list, lo) -> dict | None:
    """Each entity's change between consecutive points whose later point is after lo: new (from
    0, never in force before), reactivation (from 0, in force at an earlier point), expansion,
    contraction, churn (to 0). Counted in the segment of the later row (a churn: the earlier).
    None when no point is after lo (one month of data: GLOSSARY Unavailable, 'no month of
    moves'; never $0)."""
    if not any(b > lo for b in points):
        return None
    amount = {k: 0.0 for k in MOVES}
    count = {k: 0 for k in MOVES}
    by_seg: dict = {k: {} for k in MOVES}
    seen: set = set()
    for a, b in zip(points, points[1:]):
        va, vb = ledger[a], ledger[b]
        seen |= set(va)
        if b <= lo:
            continue
        for c in set(va) | set(vb):
            x = _value(va[c]) if c in va else 0.0
            y = _value(vb[c]) if c in vb else 0.0
            if x == 0 and y > 0:
                kind, amt = ("reactivation" if c in seen else "new"), y
            elif x > 0 and y == 0:
                kind, amt = "churn", x
            elif y > x:
                kind, amt = "expansion", y - x
            elif y < x:
                kind, amt = "contraction", x - y
            else:
                continue
            seg = _segment(va[c] if kind == "churn" else vb[c])
            amount[kind] += amt
            count[kind] += 1
            by_seg[kind][seg] = by_seg[kind].get(seg, 0.0) + amt
    return {"amount": amount, "count": count, "by_segment": by_seg}


def cohort(opening: dict, closing: dict) -> dict:
    """The entities in force at lo, followed to the as-of: opening, retained, NRR, GRR (each
    entity capped at its opening value), logo churn; also split by the opening row's segment."""
    def one(ids):
        open_v = sum(_value(opening[c]) for c in ids)
        now = sum(_value(closing[c]) for c in ids if c in closing)
        capped = sum(min(_value(closing[c]), _value(opening[c])) for c in ids if c in closing)
        kept = sum(1 for c in ids if c in closing)
        return {"opening_customers": len(ids), "opening_mrr": open_v, "retained_customers": kept,
                "retained_mrr": now, "nrr": now / open_v if open_v else None,
                "grr": capped / open_v if open_v else None,
                "logo_churn_rate": 1 - kept / len(ids) if ids else None}
    segs: dict = {}
    for c, rs in opening.items():
        segs.setdefault(_segment(rs), []).append(c)
    return {**one(list(opening)), "by_segment": {s: one(ids) for s, ids in segs.items()}}


def ledger_kpis(rows: list[dict], spec: Spec, lo, at, month_ends) -> dict:
    if spec.snapshot:   # a move or a cohort needs a known state: from the first snapshot on
        lo = max(lo, min(r["snap"] for r in rows if r["snap"] is not None))
    points = history(rows, spec, lo, month_ends)
    ledger = {t: in_force(rows, spec, t) for t in points}
    mv = moves(ledger, points, lo)
    # a cohort needs a span: one month of data opens and ends on the same date (no rate there:
    # GLOSSARY Unavailable, never a 0% churn)
    co = cohort(ledger[lo], ledger[at]) if lo < at else None
    now = ledger[at]
    mrr = level(now)
    out = {"mrr": mrr, "arr": 12 * mrr, "paying_customers": len(now),
           "arpa": mrr / len(now) if now else None,
           **{k: co[k] if co else None for k in ("opening_customers", "opening_mrr",
                                                 "retained_customers", "nrr", "grr",
                                                 "logo_churn_rate")},
           "nrr_by_segment": {s: v["nrr"] for s, v in co["by_segment"].items()} if co else {},
           "logo_churn_rate_by_segment": {s: v["logo_churn_rate"]
                                          for s, v in co["by_segment"].items()} if co else {}}
    names = {"new": ("new_mrr", "new_customers"), "expansion": ("expansion_mrr",
             "expansion_customers"), "contraction": ("contraction_mrr", "contraction_customers"),
             "churn": ("churned_mrr", "churned_customers"),
             "reactivation": ("reactivation_mrr", "reactivated_customers")}
    for kind, (money, n) in names.items():
        out[money] = mv["amount"][kind] if mv else None
        out[n] = mv["count"][kind] if mv else None
        out[f"{money}_by_segment"] = mv["by_segment"][kind] if mv else {}
    for col in spec.groups:
        out[f"mrr_by_{col}"] = by_column(now, col)
    return out


def separations(rows: list[dict], spec: Spec, lo, at) -> tuple[list[dict], list[dict]]:
    """(exits, hires) dated in (lo, as-of], each with its row (a rehire's two exits are two).
    Spells: a row's own end and start. Snapshots: a row whose end date is on or before its
    snapshot is an exit record, dated by that end (once per person and date, even a stay never
    seen in force at a snapshot); else a person in force at one snapshot and absent from the
    next leaves (from the earlier row) at the next one. The first snapshot holds no hires."""
    if spec.snapshot is None:
        exits = [r for r in rows if r["pop"] and r["end"] is not None and lo < r["end"] <= at]
        hires = [r for r in rows if r["pop"] and r["start"] is not None and lo < r["start"] <= at]
        return exits, hires
    records: dict = {}   # (person, snapshot) -> the exit record written there
    for r in rows:
        if r["pop"] and r["end"] is not None and r["snap"] is not None and r["end"] <= r["snap"]:
            records[(r["id"], r["snap"])] = r
    exits, seen_exit = [], set()
    for r in records.values():
        if lo < r["end"] <= at and (r["id"], r["end"]) not in seen_exit:
            seen_exit.add((r["id"], r["end"]))
            exits.append(r)
    snaps = sorted({r["snap"] for r in rows if r["snap"] is not None})
    hires = []
    for a, b in zip(snaps, snaps[1:]):
        if not lo < b <= at:
            continue
        va, vb = in_force(rows, spec, a), in_force(rows, spec, b)
        exits += [va[c][0] for c in va if c not in vb and (c, b) not in records]
        hires += [vb[c][0] for c in vb if c not in va]
    return exits, hires


def headcount_kpis(rows: list[dict], spec: Spec, lo, at, month_ends) -> dict:
    by_end = {t: in_force(rows, spec, t) for t in month_ends}
    avg = sum(len(f) for f in by_end.values()) / len(month_ends) if month_ends else 0
    exits, hires = separations(rows, spec, lo, at)
    now = in_force(rows, spec, at)
    out = {"headcount": len(now), "avg_headcount": avg, "separations": len(exits),
           "hires": len(hires), "attrition_rate": len(exits) / avg if avg else None,
           "headcount_by_month_end": {f"{t:%Y-%m-%d}": len(f) for t, f in by_end.items()}}
    kinds = {"voluntary_attrition": VOLUNTARY, "involuntary_attrition": INVOLUNTARY,
             "retirement_rate": RETIREMENT}
    if spec.category:
        for name, words in kinds.items():
            out[name] = sum(1 for r in exits if r["category"] in words) / avg if avg else None
    if spec.worker_type:
        out["contingent_workers"] = len(in_force(rows, spec, at, keep=lambda r: r["contingent"]))
    if spec.fte:
        out["fte"] = sum(_num(r["row"][spec.fte], 0) for rs in now.values() for r in rs)
    if spec.segment:
        dept_avg: dict = {}
        for f in by_end.values():
            for rs in f.values():
                d = _segment(rs)
                dept_avg[d] = dept_avg.get(d, 0) + 1 / len(month_ends)
        heads: dict = {}
        for rs in now.values():
            heads[_segment(rs)] = heads.get(_segment(rs), 0) + 1
        out["headcount_by_department"] = heads
        out["avg_headcount_by_department"] = dept_avg
        rate_names = [("attrition_rate", None)] + (
            [("voluntary_attrition", VOLUNTARY)] if spec.category else [])
        for name, words in rate_names:
            n: dict = {}
            for r in exits:
                if words is None or r["category"] in words:
                    n[str(r["segment"])] = n.get(str(r["segment"]), 0) + 1
            out[f"{name}_by_department"] = {d: n.get(d, 0) / a for d, a in dept_avg.items()}
    return out


def reference(name: str, df: pd.DataFrame, today: pd.Timestamp | None = None) -> dict:
    """Every spell or ledger KPI of a dataset, from the definitions alone."""
    spec = SPECS[name]
    today = today or pd.Timestamp(os.environ.get("ANALYSTAI_TODAY") or pd.Timestamp.today())
    rows = prepare(df, spec)
    lo, at, month_ends = window(rows, spec, as_of(rows, spec, today.normalize()))
    out = {"as_of": f"{at:%Y-%m-%d}", "window_start": f"{lo:%Y-%m-%d}"}
    kpis = ledger_kpis if spec.money else headcount_kpis
    return {**out, **kpis(rows, spec, lo, at, month_ends)}
