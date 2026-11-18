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
TRANSFERS = ("transfer_in", "transfer_out")   # a move between parts: nothing in the whole
MOVE_NAMES = {"new": ("new_mrr", "new_customers"), "expansion": ("expansion_mrr",   # (money, count)
              "expansion_customers"), "contraction": ("contraction_mrr", "contraction_customers"),
              "churn": ("churned_mrr", "churned_customers"),
              "reactivation": ("reactivation_mrr", "reactivated_customers")}


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
    # columns that are a level at a date (MRR, seats, salary): read on the rows in force, never
    # summed over dates; the first is the entity's value (MRR, a column the list may not have)
    levels: tuple = ()


HR_ROSTER = Spec(entity="Employee_ID", money=False, value=lambda r: 1.0, start="Hire_Date",
                 end="Termination_Date", status="Employment_Status", ended=HR_ENDED,
                 segment="Department", worker_type="Worker_Type",
                 category="Termination_Category", fte="FTE", levels=("FTE", "Annual_Salary"))
SPECS = {
    "saas_subscriptions": Spec(
        entity="customer_id", money=True, value=subscription_mrr, start="created",
        end="ended_at", paid_from="trial_end", status="status", ended=SAAS_ENDED,
        out_statuses=SAAS_OUT, concurrent=True, segment="segment", groups=("segment", "plan"),
        currency=("currency", "USD"), test_cols=("customer_id", "contact_email"), levels=("mrr",)),
    "saas_mrr_snapshots": Spec(
        entity="customer_id", money=True, value=lambda r: float(r["mrr"]), snapshot="month",
        month_labels=True, segment="segment", groups=("segment",), test_cols=("customer_id",),
        levels=("mrr",)),
    # account-month billing: MRR and seats as levels (the insight cases' book)
    "subscriptions": Spec(
        entity="account_id", money=True, value=lambda r: float(r["mrr"]), snapshot="billing_date",
        month_labels=True, segment="plan", groups=("plan",), levels=("mrr", "seats")),
    # an account-month export: every account each month, MRR, seats and users as levels
    "saas_accounts": Spec(
        entity="account_id", money=True, value=lambda r: float(r["mrr"]), snapshot="month",
        month_labels=True, segment="plan", groups=("plan",), levels=("mrr", "seats", "active_users")),
    "hr_roster": HR_ROSTER,
    "hr_roster_onerow": HR_ROSTER,   # Hire_Date is the latest hire; Original_Hire_Date unread
    "hr_snapshots": Spec(
        entity="Emp_ID", money=False, value=lambda r: 1.0, snapshot="Snapshot_Date",
        end="Termination_Date", status="Status", ended=HR_ENDED, segment="Department",
        worker_type="Worker_Type", fte="FTE", levels=("FTE", "Base_Salary")),
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


def _lines(rs: list[dict], part) -> dict:
    """An entity's ledger lines at a date: part -> its rows' values summed (GLOSSARY, Ledger)."""
    out: dict = {}
    for r in rs:
        out[part(r)] = out.get(part(r), 0.0) + r["value"]
    return out


def moves(ledger: dict, points: list, lo, part=lambda r: str(r["segment"])) -> dict | None:
    """Each entity's change between consecutive points whose later point is after lo (GLOSSARY,
    Move). Per entity, its lines' rises and falls across parts are matched first: the matched
    amount is a transfer (out of the parts that fell, into the parts that rose, each in
    proportion to its own rise or fall). The rest is classed on the entity's whole value: new
    (from 0, never in force before), reactivation (from 0, in force at an earlier point),
    expansion, contraction, churn (to 0), booked in the parts that moved, in proportion. `count`
    counts entities per kind (a transfer is no entity's move in the whole). `by_month` keys a
    move by its later point's month. None when no point is after lo (one month of data: GLOSSARY
    Unavailable, 'no month of moves'; never $0)."""
    if not any(b > lo for b in points):
        return None
    kinds = (*MOVES, *TRANSFERS)
    amount = {k: 0.0 for k in kinds}
    count = {k: 0 for k in MOVES}
    by_seg: dict = {k: {} for k in kinds}
    by_month: dict = {k: {} for k in MOVES}
    n_month: dict = {k: {} for k in MOVES}
    seen: set = set()
    for a, b in zip(points, points[1:]):
        va, vb = ledger[a], ledger[b]
        seen |= set(va)
        if b <= lo:
            continue
        month = f"{b:%Y-%m}"
        for name in MOVES:   # every month of moves, a month with none at 0
            by_month[name].setdefault(month, 0.0)
            n_month[name].setdefault(month, 0)
        for c in set(va) | set(vb):
            xa, yb = _lines(va.get(c, []), part), _lines(vb.get(c, []), part)
            x, y = sum(xa.values()), sum(yb.values())
            d = {k: yb.get(k, 0.0) - xa.get(k, 0.0) for k in set(xa) | set(yb)}
            up = sum(v for v in d.values() if v > 0)
            down = sum(-v for v in d.values() if v < 0)
            matched = min(up, down)
            rise = ("reactivation" if c in seen else "new") if x == 0 else "expansion"
            fall = "churn" if y == 0 else "contraction"
            booked: dict = {}
            for k, v in d.items():
                if v == 0:
                    continue
                side, kind, size, total = (("transfer_in", rise, v, up) if v > 0
                                           else ("transfer_out", fall, -v, down))
                moved = size * matched / total
                for name, amt in ((side, moved), (kind, size - moved)):
                    if amt:
                        amount[name] += amt
                        by_seg[name][k] = by_seg[name].get(k, 0.0) + amt
                        booked[name] = booked.get(name, 0.0) + amt
            for name in MOVES:
                if booked.get(name):
                    count[name] += 1
                    by_month[name][month] = by_month[name].get(month, 0.0) + booked[name]
                    n_month[name][month] = n_month[name].get(month, 0) + 1
    return {"amount": amount, "count": count, "by_segment": by_seg, "by_month": by_month,
            "count_by_month": n_month}


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


def ledger_at(rows: list[dict], spec: Spec, lo, month_ends) -> tuple:
    """(lo, points, ledger): the in-force map at every ledger point. A move or a cohort needs a
    known state: on snapshots, from the first snapshot on."""
    if spec.snapshot:
        lo = max(lo, min(r["snap"] for r in rows if r["snap"] is not None))
    points = history(rows, spec, lo, month_ends)
    return lo, points, {t: in_force(rows, spec, t) for t in points}


def ledger_kpis(rows: list[dict], spec: Spec, lo, at, month_ends) -> dict:
    lo, points, ledger = ledger_at(rows, spec, lo, month_ends)
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
    for kind, (money, n) in MOVE_NAMES.items():
        out[money] = mv["amount"][kind] if mv else None
        out[n] = mv["count"][kind] if mv else None
        out[f"{money}_by_segment"] = mv["by_segment"][kind] if mv else {}
        out[f"{money}_by_month"] = mv["by_month"][kind] if mv else {}
        out[f"{n}_by_month"] = mv["count_by_month"][kind] if mv else {}
        out[f"{money}_mean_by_month"] = {m: v / mv["count_by_month"][kind][m]
                                         for m, v in mv["by_month"][kind].items()
                                         if mv["count_by_month"][kind][m]} if mv else {}
    for kind in TRANSFERS:   # the whole book is one part: nothing moves; each segment's own
        out[f"{kind}_mrr"] = 0.0 if mv else None
        out[f"{kind}_mrr_by_segment"] = mv["by_segment"][kind] if mv else {}
    for col in spec.groups:
        out[f"mrr_by_{col}"] = by_column(now, col)
    if spec.segment:   # a segment bridge opens on its lines (a cohort places by the opening row)
        out["opening_mrr_by_segment"] = by_column(ledger[lo], spec.segment) if lo < at else {}
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


AGGS = {   # naive, as pandas defines them (std: the sample's, n − 1)
    "sum": sum, "count": len, "min": min, "max": max,
    "mean": lambda v: sum(v) / len(v),
    "median": lambda v: (sorted(v)[(len(v) - 1) // 2] + sorted(v)[len(v) // 2]) / 2,
    "std": lambda v: (sum((x - sum(v) / len(v)) ** 2 for x in v) / (len(v) - 1)) ** 0.5
    if len(v) > 1 else float("nan"),
}


def reference_by(name: str, df: pd.DataFrame, cols: list[str], metric: str, agg: str,
                 months: set | frozenset = frozenset(),
                 today: pd.Timestamp | None = None) -> tuple[list[dict], list[dict]]:
    """(levels, moves): the readings of a level (MRR, seats, salary: the spec's `levels`) by any
    row columns, as the definitions give them. Levels: the rows in force at the as-of (the
    population's), grouped by `cols` ('a|b' keys, a date column in `months` by its month
    'YYYY-MM', an 'Overall' total), `agg` over those rows; per entity (a count of entities, a
    sum ÷ entities: MRR per customer). Moves of the entity's value: by one column, each move by
    month (an amount, or its mean per entity); summed by groups, each move by them, transfers
    included. ([], []) for any other column."""
    spec = SPECS[name]
    if metric not in spec.levels or agg not in AGGS:
        return [], []
    today = today or pd.Timestamp(os.environ.get("ANALYSTAI_TODAY") or pd.Timestamp.today())
    rows = prepare(df, spec)
    at = as_of(rows, spec, today.normalize())
    now = in_force(rows, spec, at)

    def value(r) -> float | None:
        if metric == spec.levels[0] and spec.money:
            return r["value"]   # the entity's own value (a list's MRR from its price)
        return None if _blank(r["row"].get(metric)) else float(r["row"][metric])

    def key(r) -> str:
        return "|".join(f"{_date(r['row'][k]):%Y-%m}" if k in months and _date(r["row"][k])
                        else str(r["row"][k]) for k in cols)

    vals: dict = {}
    ents: dict = {}
    for c, rs in now.items():
        for r in rs:
            for k in (key(r), "Overall"):
                if (v := value(r)) is not None:
                    vals.setdefault(k, []).append(v)
                ents.setdefault(k, set()).add(c)
    levels = [{k: AGGS[agg](v) for k, v in vals.items()}]
    if agg in ("count", "mean"):   # per entity: customers, MRR per customer
        levels.append({k: (len(ents[k]) if agg == "count" else sum(vals.get(k, [])) / len(ents[k]))
                       for k in ents})
    moved: list[dict] = []
    if agg in ("sum", "mean") and metric == spec.levels[0] and spec.money:   # the value's moves
        if len(cols) == 1:   # by month ('YYYY-MM': a month column's groups)
            got = reference(name, df, today)
            moved += [got.get(f"{MOVE_NAMES[k][0]}_{'mean_' if agg == 'mean' else ''}by_month") or {}
                      for k in MOVES]
        if agg == "sum" and not set(cols) & set(months):   # by the groups, each line its group
            # (GLOSSARY Move, transfers too)
            lo, _, month_ends = window(rows, spec, at)
            lo, points, ledger = ledger_at(rows, spec, lo, month_ends)
            mv = moves(ledger, points, lo, part=key)
            moved += [mv["by_segment"][k] for k in (*MOVES, *TRANSFERS)] if mv else []
    return [o for o in levels if o], [o for o in moved if o]


# ── IoT / manufacturing (Domain 7; docs/research/2026-11-16-iot.md §2) ─────────────────────────
# iot.yaml's words, copied (test_the_references_word_lists_are_the_packs checks them)
WO_CORRECTIVE = {"cm", "em", "corrective", "corrective maintenance", "emergency",
                 "emergency maintenance", "emergency repair", "breakdown", "breakdown maintenance",
                 "breakdown repair", "reactive", "reactive maintenance", "unplanned",
                 "unplanned maintenance", "unplanned breakdown", "repair", "fault", "failure"}
WO_PREVENTIVE = {"pm", "preventive", "preventive maintenance", "planned", "planned maintenance",
                 "pdm", "predictive", "predictive maintenance", "condition based", "cbm",
                 "inspection", "routine", "calibration"}
WO_OPEN = {"inprg", "in progress", "wappr", "waiting on approval", "appr", "approved", "wmatl",
           "waiting on material", "wsched", "waiting to be scheduled", "open", "hold"}
WO_CANCELLED = {"can", "cancel", "cancelled", "canceled", "void", "voided", "rejected"}


def _w(v) -> str:
    return "" if v is None or (isinstance(v, float) and v != v) else \
        re.sub(r"[\s_-]+", " ", str(v)).strip().lower()


def _n(v) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if x != x else x


def shift_kpis(df: pd.DataFrame, c: dict) -> dict:
    """A shift report's KPIs, summed over every row before any division (ISO 22400 / oee.com):
    run = planned − unplanned downtime − changeover; OEE = Σ(ideal cycle s × good) / 60 ÷ Σ
    planned minutes; MTBF = Σ run hours ÷ Σ failures."""
    planned = run = ideal_total = ideal_good = total = good = scrap = fails = down = 0.0
    for r in df.to_dict("records"):
        p, u, ch = _n(r[c["planned"]]), _n(r[c["unplanned"]]), _n(r.get(c.get("changeover")))
        planned += p
        run += p - u - ch
        down += u
        total += _n(r[c["total"]])
        good += _n(r[c["good"]])
        scrap += _n(r[c["scrap"]])
        ideal_total += _n(r[c["ideal"]]) * _n(r[c["total"]]) / 60
        ideal_good += _n(r[c["ideal"]]) * _n(r[c["good"]]) / 60
        fails += _n(r[c["failures"]])
    ratio = lambda a, b: a / b if b else None   # noqa: E731
    return {"oee": ratio(ideal_good, planned), "availability": ratio(run, planned),
            "performance": ratio(ideal_total, run), "quality_rate": ratio(good, total),
            "scrap_rate": ratio(scrap, total), "good_count": good, "failures": fails,
            "unplanned_downtime": down, "run_minutes": run, "planned_time": planned,
            "mtbf": ratio(run / 60, fails)}


def wo_kpis(df: pd.DataFrame, c: dict) -> dict:
    """A work-order log's KPIs: a cancelled order is no work; MTTR = actual finish − actual start
    in hours over finished corrective orders whose span is known and not negative. A count of a
    kind no row of the file has (no open order at all: an export of closed ones?) is unknown."""
    seen, corrective, opened, preventive, assets = set(), set(), set(), set(), set()
    hours, repaired = 0.0, set()
    records = df.to_dict("records")
    live = any(_w(r[c["status"]]) not in WO_CANCELLED for r in records)   # no counted row: unknown
    has = {k: live and any(_w(r[c[col]]) in words for r in records)
           for k, col, words in (("corrective", "type", WO_CORRECTIVE), ("open", "status", WO_OPEN),
                                 ("preventive", "type", WO_PREVENTIVE))}
    for r in records:
        if _w(r[c["status"]]) in WO_CANCELLED:
            continue
        wo, kind = r[c["wo"]], _w(r[c["type"]])
        seen.add(wo)
        assets.add(r[c["asset"]])
        if kind in WO_PREVENTIVE:
            preventive.add(wo)
        if _w(r[c["status"]]) in WO_OPEN:
            opened.add(wo)
        if kind not in WO_CORRECTIVE:
            continue
        corrective.add(wo)
        a, b = r.get(c["start"]), r.get(c["finish"])
        if a is None or b is None or pd.isna(a) or pd.isna(b):
            continue
        h = (pd.Timestamp(b) - pd.Timestamp(a)).total_seconds() / 3600
        if h >= 0:
            hours += h
            repaired.add(wo)
    return {"corrective_wos": float(len(corrective)) if has["corrective"] else None,
            "open_wos": float(len(opened)) if has["open"] else None,
            "pm_share": len(preventive) / len(seen) if seen and has["preventive"] else None,
            "mttr": hours / len(repaired) if repaired else None,
            "assets": float(len(assets)) if live else None}


IOT = {   # dataset or book → (KPI function, its columns)
    "shift_production": (shift_kpis, {"planned": "Planned Minutes", "unplanned": "Unplanned Downtime (min)",
                                      "changeover": "Changeover (min)", "total": "Total Count",
                                      "good": "Good Count", "scrap": "Scrap Count",
                                      "ideal": "Ideal Cycle Time (s)", "failures": "Failures"}),
    "book_iot_shifts": (shift_kpis, {"planned": "planned_minutes", "unplanned": "unplanned_downtime",
                                     "changeover": "changeover", "total": "total_count",
                                     "good": "good_count", "scrap": "scrap_count",
                                     "ideal": "ideal_cycle_time", "failures": "failures"}),
    "work_orders": (wo_kpis, {"wo": "WONUM", "type": "WORKTYPE", "status": "STATUS",
                              "start": "ACTSTART", "finish": "ACTFINISH", "asset": "ASSETNUM"}),
    "book_iot_work_orders": (wo_kpis, {"wo": "wonum", "type": "worktype", "status": "status",
                                       "start": "actstart", "finish": "actfinish",
                                       "asset": "assetnum"}),
}


# ── Logistics (Domain 8; docs/research/2026-11-18-logistics.md §2) ────────────────────────────
# logistics.yaml's words, copied (test_the_references_word_lists_are_the_packs checks them)
LOG_CANCELLED = {"cancelled", "canceled", "cancel", "void", "voided"}
LOG_RETURNED = {"returned to sender", "return to sender", "rts", "returned", "refused"}
LOG_OPEN = {"in transit", "intransit", "out for delivery", "picked up", "exception",
            "delivery exception", "delayed", "tendered", "booked", "dispatched", "pending"}
LOG_UNRELEASED = {"open", "pending", "allocated", "released", "picked", "packed"}
LOG_UNDELIVERED = {"in transit", "intransit", "out for delivery", "picked up", "exception",
                   "delivery exception", "delayed", "tendered", "booked", "dispatched", "pending",
                   "shipped", "partially shipped", "backordered", "back ordered", "short shipped",
                   "open", "allocated", "released", "picked", "packed"}


def _as_of(df: pd.DataFrame, col: str):
    """The data's last day on its time axis, not after today (U1: what 'past due' is judged at)."""
    today = pd.Timestamp(os.environ.get("ANALYSTAI_TODAY") or pd.Timestamp.today()).normalize()
    days = pd.to_datetime(df[col], errors="coerce").dt.normalize()
    days = days[days <= today]
    return days.max() if days.notna().any() else None
LOG_NO_SHOW = {"no show", "no-show", "noshow", "missed"}
_LB = {"lb": 1.0, "lbs": 1.0, "l": 1.0, "pound": 1.0, "pounds": 1.0, "kg": 2.20462, "kgs": 2.20462,
       "k": 2.20462, "kilogram": 2.20462, "kilograms": 2.20462, "oz": 0.0625, "ounce": 0.0625,
       "ounces": 0.0625}


def _day(v):
    return None if v is None or pd.isna(v) else pd.Timestamp(v).normalize()


def _ratio(num, den):
    return num / den if den else None


def shipment_kpis(df: pd.DataFrame, c: dict) -> dict:
    """On time = delivered on or before the promise (whole days, early is on time) ÷ delivered +
    returned with a promise; transit = delivered − ship date in whole days, ≥ 0; cost per lb =
    Σ cost ÷ Σ lb over rows with a weight in a known unit. Cancelled rows count nowhere."""
    on, base, transit, ids, open_, ret = set(), set(), {}, set(), set(), set()
    late, cost, wcost, lb = {}, 0.0, 0.0, 0.0
    as_of = _as_of(df, c["ship"])
    for _, r in df.iterrows():
        st = _w(r[c["status"]])
        if st in LOG_CANCELLED:
            continue
        sid = r[c["id"]]
        ids.add(sid)
        open_ |= {sid} if st in LOG_OPEN else set()
        ret |= {sid} if st in LOG_RETURNED else set()
        ship, deliv, prom = _day(r[c["ship"]]), _day(r[c["delivered"]]), _day(r[c["promised"]])
        span = (deliv - ship).days if deliv is not None and ship is not None else None
        if span is not None and span >= 0:
            transit[sid] = transit.get(sid, 0) + span
            if prom is not None:
                base.add(sid)
                if deliv <= prom:
                    on.add(sid)
                else:
                    late[sid] = late.get(sid, 0) + (deliv - prom).days
        if prom is not None and st in LOG_RETURNED:
            base.add(sid)
        if prom is not None and as_of is not None and st in LOG_UNDELIVERED and prom < as_of:
            base.add(sid)   # U1: still open after its promise: late
        cost += _n(r[c["cost"]])
        w, unit = _n(r[c["weight"]]), _LB.get(_w(r[c["uom"]]))
        if w > 0 and unit:
            wcost += _n(r[c["cost"]])
            lb += w * unit
    # no delivery date on any row: the export has none, so on time is unknown (never 0%)
    dated = df[c["delivered"]].notna().any()
    return {"on_time_delivery": _ratio(len(on), len(base)) if dated else None,
            "avg_transit_days": _ratio(sum(transit.values()), len(transit)),
            "cost_per_shipment": _ratio(cost, len(ids)), "cost_per_lb": _ratio(wcost, lb),
            "shipments": float(len(ids)) if ids else None,
            "open_shipments": float(len(open_)) if open_ else None,
            "returned_shipments": float(len(ret)) if ret else None,
            "avg_days_late": _ratio(sum(late.values()), len(late))}


def order_line_kpis(df: pd.DataFrame, c: dict) -> dict:
    """OTIF (line) = delivered lines on time (by the requested date) and in full ÷ delivered
    lines; OTIF (order) = orders whose every delivered line is OTIF ÷ orders with a delivered
    line; fills over lines past release: Σ shipped ÷ Σ ordered, full lines ÷ lines."""
    ok_lines, deliv_lines, orders, order_ok = set(), set(), set(), {}
    shipped = ordered = 0.0
    released, full = set(), set()
    as_of = _as_of(df, c["date"])
    for _, r in df.iterrows():
        st = _w(r[c["status"]])
        if st in LOG_CANCELLED:
            continue
        line, order = r[c["line"]], r[c["order"]]
        orders.add(order)
        deliv, req = _day(r[c["delivered"]]), _day(r[c["promised"]])
        is_full = _n(r[c["shipped"]]) - _n(r[c["ordered"]]) >= 0
        if deliv is not None and req is not None:
            deliv_lines.add(line)
            ok = deliv <= req and is_full
            ok_lines |= {line} if ok else set()
            order_ok[order] = order_ok.get(order, True) and ok
        elif req is not None and as_of is not None and st in LOG_UNDELIVERED and req < as_of:
            deliv_lines.add(line)   # U1: still open after its requested date: it failed
            order_ok[order] = False
        if st not in LOG_UNRELEASED:
            released.add(line)
            full |= {line} if is_full else set()
            shipped += _n(r[c["shipped"]])
            ordered += _n(r[c["ordered"]])
    return {"otif_line": _ratio(len(ok_lines), len(deliv_lines)),
            "otif_order": _ratio(sum(order_ok.values()), len(order_ok)),
            "unit_fill": _ratio(shipped, ordered), "line_fill": _ratio(len(full), len(released)),
            "orders": float(len(orders)) if orders else None}


def dock_kpis(df: pd.DataFrame, c: dict) -> dict:
    """Dwell = departed − arrived in exact hours, ≥ 0, averaged per visit; on-time arrival =
    arrived at or before the appointment ÷ visits that arrived; no-shows apart."""
    dwell, on, arrived, visits, no_show = {}, set(), set(), set(), set()
    for _, r in df.iterrows():
        st = _w(r[c["status"]])
        if st in LOG_CANCELLED:
            continue
        v = r[c["id"]]
        visits.add(v)
        no_show |= {v} if st in LOG_NO_SHOW else set()
        appt, arr, dep = (None if pd.isna(r[c[k]]) else pd.Timestamp(r[c[k]])
                          for k in ("appointment", "arrived", "departed"))
        if arr is not None and appt is not None:
            arrived.add(v)
            on |= {v} if arr <= appt else set()
        if arr is not None and dep is not None and dep >= arr:
            dwell[v] = (dep - arr).total_seconds() / 3600
    return {"avg_dwell": _ratio(sum(dwell.values()), len(dwell)),
            "on_time_arrival": _ratio(len(on), len(arrived)),
            "visits_over_2h": _ratio(sum(h > 2 for h in dwell.values()), len(dwell)),
            "no_shows": float(len(no_show)) if no_show else None,
            "visits": float(len(visits)) if visits else None}


LOGISTICS = {
    "book_shipments": (shipment_kpis, {"id": "shipment_id", "status": "status", "ship": "ship_date",
                                       "delivered": "delivered_at", "promised": "promised_date",
                                       "cost": "total_charge", "weight": "weight",
                                       "uom": "weight_uom"}),
    "book_order_lines": (order_line_kpis, {"line": "order_line_id", "order": "order_id",
                                           "date": "order_date",
                                           "status": "status", "delivered": "delivered_date",
                                           "promised": "requested_date", "ordered": "qty_ordered",
                                           "shipped": "qty_shipped"}),
    "book_dock_visits": (dock_kpis, {"id": "appointment_id", "status": "status",
                                     "appointment": "appointment_time", "arrived": "arrived_at",
                                     "departed": "departed_at"}),
}


def reference(name: str, df: pd.DataFrame, today: pd.Timestamp | None = None) -> dict:
    """Every spell or ledger KPI of a dataset, from the definitions alone."""
    if name in IOT or name in LOGISTICS:
        f, cols = {**IOT, **LOGISTICS}[name]
        return f(df, cols)
    spec = SPECS[name]
    today = today or pd.Timestamp(os.environ.get("ANALYSTAI_TODAY") or pd.Timestamp.today())
    rows = prepare(df, spec)
    lo, at, month_ends = window(rows, spec, as_of(rows, spec, today.normalize()))
    out = {"as_of": f"{at:%Y-%m-%d}", "window_start": f"{lo:%Y-%m-%d}"}
    kpis = ledger_kpis if spec.money else headcount_kpis
    return {**out, **kpis(rows, spec, lo, at, month_ends)}
