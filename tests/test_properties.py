"""Properties P1–P5 (Phase 5.7b): invariants every pack shape keeps, over generated data, so a fix
is checked on the variants nobody thought of (an audit's examples are only the ones someone
did). Two tiers: (a) small books Hypothesis builds and shrinks (an MRR export, a subscription
list, an HR roster and its snapshots) on the real packs; (b) every PACK_DATASETS dataset through
the production reader, with drawn transforms.

A known failure of a recurring class (docs/retro-ledger.md, C1–C9) is its own xfail-strict test
on its minimal case; the generative tests leave that case out, so a new failure is never hidden
behind an expected one, and fixing the class flips its xfail (XPASS fails the suite)."""
import io

import pandas as pd
import pytest
from hypothesis import assume, given, settings, strategies as st

import domain_pack as dp
from connectors.csv_connector import _read_bytes

SAAS, HR = dp.pack_for("saas"), dp.pack_for("hr")
LABELS = pd.date_range("2025-01-01", "2026-10-01", freq="MS")   # month labels, on the 1st
SEGMENTS = ["SMB", "Mid", "Ent"]
DEPTS = ["Ops", "Sales", "Eng"]
FLOWS = {"new_mrr": 1, "expansion_mrr": 1, "reactivation_mrr": 1, "contraction_mrr": -1,
         "churned_mrr": -1, "transfer_in_mrr": 1, "transfer_out_mrr": -1}
MRR_ROLES = {"month": "month", "customer": "customer_id", "mrr": "mrr", "segment": "segment"}
LIST_ROLES = {"customer": "customer_id", "created": "created", "ended_at": "ended_at",
              "status": "status", "mrr": "mrr", "segment": "segment"}
HR_ROLES = {"employee": "employee_id", "hire_date": "hire_date",
            "termination_date": "termination_date", "department": "department",
            "status": "status"}
SNAP_ROLES = {"employee": "employee_id", "snapshot_date": "snapshot_date",
              "department": "department"}


# ── Tier (a): small books ─────────────────────────────────────────────────────────────────────

@st.composite
def mrr_books(draw, moves_segment: bool = True, one_run: bool = False) -> pd.DataFrame:
    """An MRR export: one row per paying customer per month label; absent = not paying.
    `one_run`: each customer pays over one unbroken run of months, two months at least. With
    `moves_segment`, a customer may change segment and may hold a second row in a month (an
    add-on in another segment: the pack's concurrent spells)."""
    lo = draw(st.integers(0, len(LABELS) - 2))
    hi = draw(st.integers(lo + 1, len(LABELS) - 1))
    rows = []
    for c in range(draw(st.integers(1, 6))):
        seg = draw(st.sampled_from(SEGMENTS))
        a = draw(st.integers(lo, hi - 1)) if one_run else lo
        b = draw(st.integers(a + 1, hi)) if one_run else hi
        for m in LABELS[a:b + 1]:
            if moves_segment and draw(st.integers(0, 9)) == 0:
                seg = draw(st.sampled_from(SEGMENTS))
            if v := draw(st.sampled_from([50, 100, 150] if one_run else [0, 0, 50, 100, 150])):
                rows.append((m, f"c{c}", float(v), seg))
                if moves_segment and not one_run and draw(st.integers(0, 4)) == 0:
                    rows.append((m, f"c{c}", float(draw(st.sampled_from([20, 50]))),
                                 draw(st.sampled_from(SEGMENTS))))
    assume(rows)
    return pd.DataFrame(rows, columns=["month", "customer_id", "mrr", "segment"])


def _date(draw, lo="2023-06-01", hi="2026-10-31"):
    days = (pd.Timestamp(hi) - pd.Timestamp(lo)).days
    return pd.Timestamp(lo) + pd.Timedelta(days=draw(st.integers(0, days)))


@st.composite
def subscription_lists(draw) -> pd.DataFrame:
    """A subscription list: one row per subscription, today's MRR, an end date or none."""
    rows = []
    for c in range(draw(st.integers(1, 6))):
        start = _date(draw)
        for _ in range(draw(st.integers(1, 2))):   # a returning customer: a second subscription
            end = _date(draw, start.strftime("%Y-%m-%d")) if draw(st.booleans()) else pd.NaT
            status = "canceled" if pd.notna(end) else draw(st.sampled_from(["active", "past_due",
                                                                            "trialing"]))
            rows.append((f"c{c}", start, end, status, float(draw(st.sampled_from([50, 100]))),
                         draw(st.sampled_from(SEGMENTS))))
            if pd.isna(end):
                break
            start = end + pd.Timedelta(days=draw(st.integers(1, 200)))
            assume(start <= pd.Timestamp("2026-10-31"))
        if draw(st.integers(0, 3)) == 0:   # a concurrent subscription, maybe in another segment
            s2 = _date(draw)
            e2 = _date(draw, s2.strftime("%Y-%m-%d")) if draw(st.booleans()) else pd.NaT
            rows.append((f"c{c}", s2, e2, "canceled" if pd.notna(e2) else "active",
                         float(draw(st.sampled_from([20, 50]))), draw(st.sampled_from(SEGMENTS))))
    return pd.DataFrame(rows, columns=["customer_id", "created", "ended_at", "status", "mrr",
                                       "segment"])


@st.composite
def hr_rosters(draw, rehires: bool = True) -> pd.DataFrame:
    """An HR roster: one row per employment spell (a rehire is a second row of the same id)."""
    rows = []
    for e in range(draw(st.integers(1, 8))):
        hire, dept = _date(draw, "2022-01-01"), draw(st.sampled_from(DEPTS))
        for _ in range(draw(st.integers(1, 2 if rehires else 1))):
            end = _date(draw, hire.strftime("%Y-%m-%d")) if draw(st.booleans()) else pd.NaT
            rows.append((f"e{e}", hire, end, dept, "Terminated" if pd.notna(end) else "Active"))
            if pd.isna(end):
                break
            hire = end + pd.Timedelta(days=draw(st.integers(1, 300)))
            assume(hire <= pd.Timestamp("2026-10-31"))
    return pd.DataFrame(rows, columns=["employee_id", "hire_date", "termination_date",
                                       "department", "status"])


def _snapshots(roster: pd.DataFrame) -> pd.DataFrame:
    """The roster's month-end snapshots: each person in force at each month-end, from the first
    hire's month to Oct 2026."""
    ends = pd.date_range(roster["hire_date"].min(), "2026-10-31", freq="ME")
    rows = [(t, r.employee_id, r.department) for t in ends for r in roster.itertuples()
            if r.hire_date <= t and (pd.isna(r.termination_date) or r.termination_date > t)]
    return pd.DataFrame(rows, columns=["snapshot_date", "employee_id", "department"])


def hr_snapshot_books(rehires: bool = True):
    """An HR snapshot file: the people in force at each month-end of a generated roster."""
    return hr_rosters(rehires).map(_snapshots).filter(lambda df: len(df) > 0)


def _kpis(pack, roles, df) -> dict:
    return {k.name: dp.compute_kpi(df, roles, pack, k.name) for k in pack.kpis
            if dp.computable(pack, k.name, roles)}


def _same(a: dict, b: dict, scale: dict | None = None):
    assert a.keys() == b.keys()
    for k in a:
        want = None if a[k] is None else a[k] * (scale or {}).get(k, 1)
        assert (b[k] is None) == (want is None), k
        if want is not None:
            assert b[k] == pytest.approx(want, rel=1e-9, abs=1e-9), k


# ── P1: the bridge identity ───────────────────────────────────────────────────────────────────

def _bridge(df: pd.DataFrame, part: pd.DataFrame | None = None,
            roles: dict = MRR_ROLES) -> tuple[float, float] | None:
    """(MRR now, opening + Σ moves) on the whole book or a part of it; None when a term is
    unavailable (one month of data has no moves: the bridge can't be built, it isn't 0)."""
    k = {n: dp.compute_kpi(df if part is None else part, roles, SAAS, n,
                           full=None if part is None else df)
         for n in ("mrr", "opening_mrr", *FLOWS)}
    if any(v is None for v in k.values()):
        return None
    return k["mrr"], k["opening_mrr"] + sum(s * k[n] for n, s in FLOWS.items())


def _by(df: pd.DataFrame, roles: dict, by: str) -> dict | None:
    """Each bridge KPI by `by` (a group with none: 0); None when a term is unavailable."""
    k = {n: dp.compute_kpi(df, roles, SAAS, n, by=by) for n in ("mrr", "opening_mrr", *FLOWS)}
    if any(v is None for v in k.values()):
        return None
    groups = set().union(*(v.index for v in k.values()))
    return {n: v.reindex(sorted(groups, key=str), fill_value=0.0) for n, v in k.items()}

SPLIT_BOOKS = [(mrr_books(), MRR_ROLES), (subscription_lists(), LIST_ROLES)]


@given(mrr_books())
def test_p1_the_bridge_adds_up_in_total(df):
    if b := _bridge(df):
        assert b[0] == pytest.approx(b[1])


@given(mrr_books(moves_segment=False))
def test_p1_the_bridge_adds_up_per_segment_when_no_customer_changes_segment(df):
    for s in df["segment"].unique():
        if b := _bridge(df, df[df["segment"] == s]):
            assert b[0] == pytest.approx(b[1]), s


@pytest.mark.parametrize("book,roles", SPLIT_BOOKS, ids=["mrr_export", "subscription_list"])
@given(data=st.data())
def test_p1_the_bridge_adds_up_per_segment_for_a_customer_who_changes_segment(book, roles, data):
    """5.7b-2b (C3, PR1-03): a customer who changes segment, or holds subscriptions in two at
    once, moves between the segments' bridges (GLOSSARY Move: a transfer in and out). Each
    segment's opening plus its moves is its MRR now, as a filter's part and as a group."""
    df = data.draw(book)
    for s in df["segment"].unique():
        if b := _bridge(df, df[df["segment"] == s], roles):
            assert b[0] == pytest.approx(b[1]), s
    if k := _by(df, roles, "segment"):
        built = k["opening_mrr"] + sum(sg * k[n] for n, sg in FLOWS.items())
        assert k["mrr"].to_numpy() == pytest.approx(built.to_numpy())
        # a move between segments is nothing in the whole
        assert k["transfer_in_mrr"].sum() == pytest.approx(k["transfer_out_mrr"].sum())


def test_p1_a_customer_who_moves_segment_moves_its_mrr_between_the_bridges():
    """The C3 case 5.7b-1 pinned: c0 moves SMB → Ent. SMB opened at 100 with no move out and
    Ent ended at 100 with no move in; now 100 moves out of SMB and into Ent, and c0 is neither
    a churned nor a new customer."""
    df = pd.DataFrame({"month": pd.to_datetime(["2026-09-01", "2026-10-01"]),
                       "customer_id": ["c0", "c0"], "mrr": [100.0, 100.0],
                       "segment": ["SMB", "Ent"]})
    k = _by(df, MRR_ROLES, "segment")
    assert (k["transfer_out_mrr"]["SMB"], k["transfer_in_mrr"]["Ent"]) == (100, 100)
    assert k["churned_mrr"].sum() == k["new_mrr"].sum() == 0
    assert dp.compute_kpi(df, MRR_ROLES, SAAS, "churned_customers", by="segment").sum() == 0


def test_a_cohort_by_segment_places_a_customer_in_two_segments_the_same_way_in_any_row_order():
    """5.7b-2b (PR9-01, found by D): c0 pays in Mid and in Ent at the cohort's opening. A cohort
    places an entity in one group (GLOSSARY Opening cohort): the first in sort order, Ent, never
    the file's first row, so NRR by segment never depends on the row order."""
    months = pd.date_range("2025-10-01", "2026-10-01", freq="MS")
    df = pd.DataFrame([(m, c, v, s) for m in months for c, v, s in
                       (("c0", 150.0, "Mid"), ("c0", 50.0, "Ent"), ("c1", 100.0, "SMB"))],
                      columns=["month", "customer_id", "mrr", "segment"])
    for book in (df, df.iloc[::-1].reset_index(drop=True)):
        assert dp.compute_kpi(book, MRR_ROLES, SAAS, "nrr", by="segment").to_dict() == {"Ent": 1.0, "SMB": 1.0}


def test_a_customer_new_in_two_segments_at_once_is_new_in_each_and_once_in_the_whole():
    """GLOSSARY Move on counts: c0 starts paying in SMB and Ent in October. Each segment gained
    a new customer (a distinct count by group, never a partition: P2); the whole has one."""
    df = pd.DataFrame({"month": pd.to_datetime(["2026-09-01", "2026-10-01", "2026-10-01", "2026-10-01"]),
                       "customer_id": ["c1", "c1", "c0", "c0"], "mrr": [50.0, 50.0, 100.0, 20.0],
                       "segment": ["Mid", "Mid", "SMB", "Ent"]})
    assert dp.compute_kpi(df, MRR_ROLES, SAAS, "new_customers") == 1
    by = dp.compute_kpi(df, MRR_ROLES, SAAS, "new_customers", by="segment")
    assert {s: v for s, v in by.items() if v} == {"SMB": 1, "Ent": 1}


def test_nobody_paying_at_any_month_end_moves_nothing():
    """An empty ledger (every row's MRR is 0: the paying condition keeps none): every move and
    transfer is a measured 0, never an error; by segment there is no paying group to show."""
    df = pd.DataFrame({"month": pd.to_datetime(["2026-09-01", "2026-10-01"]), "customer_id": ["c0", "c0"],
                       "mrr": [0.0, 0.0], "segment": ["SMB", "SMB"]})
    for k in FLOWS:
        assert dp.compute_kpi(df, MRR_ROLES, SAAS, k) == 0, k
        assert dp.measure(df, MRR_ROLES, SAAS, k, by="segment") == dp.Unavailable("no_rows"), k


def test_a_customer_with_rows_in_and_out_of_a_filter_is_one_customer_in_its_cohort():
    """5.7b-2b (domain_diff, found on the branch): c0 holds a monthly and a yearly subscription.
    Filtered to the monthly ones, its two ledger lines (in and out of the part) are one customer,
    never two: c0 stays, c1 leaves, so logo churn is 50%."""
    df = pd.DataFrame({"customer_id": ["c0", "c0", "c1"],
                       "created": pd.to_datetime(["2024-01-01"] * 3),
                       "ended_at": pd.to_datetime([None, None, "2026-06-01"]),
                       "status": ["active", "active", "canceled"], "mrr": [50.0, 100.0, 50.0],
                       "segment": ["SMB"] * 3, "interval": ["month", "year", "month"]})
    part = df[df["interval"] == "month"]
    assert dp.compute_kpi(part, LIST_ROLES, SAAS, "logo_churn_rate", full=df) == pytest.approx(0.5)


@given(mrr_books())
def test_p1_a_parts_bridge_adds_up_month_by_month(df):
    """By period on a part: each month's MRR is the month before plus that month's moves,
    transfers included (a month the snapshots can't tell is unknown, never a gap)."""
    df = df.assign(_period=pd.Series(pd.NaT, index=df.index).astype("period[M]"))
    for s in df["segment"].unique():
        part = df[df["segment"] == s]
        k = {n: dp.compute_kpi(part, MRR_ROLES, SAAS, n, by="_period", full=df) for n in ("mrr", *FLOWS)}
        if any(v is None for v in k.values()):
            continue
        moves = sum(sg * k[n] for n, sg in FLOWS.items())
        for p in k["mrr"].index[1:]:
            if pd.notna(moves.get(p)) and pd.notna(k["mrr"].get(p - 1)):
                assert k["mrr"][p] == pytest.approx(k["mrr"][p - 1] + moves[p]), (s, p)


def test_p1_the_bridge_adds_up_on_an_asked_window():
    """a pays 100 until May 2026; b pays 50, then 60 from Nov 2025. Asked: Apr–Oct 2026. The
    moves read those 7 months (a's churn); the opening read 31 Oct 2025 (a 12-month cohort): a
    bridge opens where its moves start, 31 Mar 2026 (C2; NRR keeps its 12-month cohort)."""
    months = pd.date_range("2024-10-01", "2026-10-01", freq="MS")
    rows = [(m, "a", 100.0, "SMB") for m in months[:20]] + \
        [(m, "b", 50.0 + 10 * (i > 12), "SMB") for i, m in enumerate(months)]
    df = pd.DataFrame(rows, columns=["month", "customer_id", "mrr", "segment"])
    asked = (pd.Timestamp("2026-04-01"), pd.Timestamp("2026-10-31"))
    k = {n: dp.compute_kpi(df, MRR_ROLES, SAAS, n, window=asked)
         for n in ("mrr", "opening_mrr", *FLOWS)}
    assert k["mrr"] == pytest.approx(k["opening_mrr"] + sum(sg * k[n] for n, sg in FLOWS.items()))


# ── P2: partition ─────────────────────────────────────────────────────────────────────────────

def _partitioned(pack, name: str) -> bool:
    """A KPI whose groups add up to its total: no denominator, every term a sum of rows or of
    each entity's own value, a count of events (hires, exits), or of entities in force at one
    date when each holds one spell at a time (a distinct count over groups double-counts a
    mover, and a customer's concurrent subscriptions in two segments)."""
    kpi = pack.kpi(name)
    one_at_a_time = pack.spell is not None and not pack.spell.concurrent

    def adds(t) -> bool:   # a transfer is a move between groups: 0 in the whole
        return not t.kpi and t.move not in dp.TRANSFERS and (t.sum is not None or t.spell in ("starts", "ends")
                              or (t.spell == "active" and one_at_a_time))
    return kpi.den is None and all(adds(t) for alt in kpi.num for t in alt)


def _check_partition(pack, roles, df, by):
    for k in (k.name for k in pack.kpis if _partitioned(pack, k.name)
              and dp.computable(pack, k.name, roles)):
        total, parts = dp.compute_kpi(df, roles, pack, k), dp.compute_kpi(df, roles, pack, k, by=by)
        if total is None:   # unavailable in total: no group has a number either
            assert parts is None or parts.isna().all(), (k, by)
            continue
        if parts is None or parts.attrs.get("hidden"):   # small cells: not shown, not added
            continue
        assert parts.sum() == pytest.approx(total, abs=1e-6), (k, by)


@given(mrr_books())
def test_p2_mrr_by_segment_adds_up_to_the_total(df):
    _check_partition(SAAS, MRR_ROLES, df, "segment")


@given(hr_rosters())
def test_p2_hr_by_department_adds_up_to_the_total(df):
    _check_partition(HR, HR_ROLES, df, "department")


@given(hr_snapshot_books())
def test_p2_hr_snapshots_by_department_add_up_to_the_total(df):
    _check_partition(HR, SNAP_ROLES, df, "department")


# ── P3: shuffle, source and scale invariance ──────────────────────────────────────────────────

BOOKS = [(mrr_books(), SAAS, MRR_ROLES), (subscription_lists(), SAAS, LIST_ROLES),
         (hr_rosters(), HR, HR_ROLES), (hr_snapshot_books(), HR, SNAP_ROLES)]
SHAPES = ["mrr_export", "subscription_list", "roster", "snapshots"]


@pytest.mark.parametrize("book,pack,roles", BOOKS, ids=SHAPES)
@given(data=st.data())
def test_p3_row_order_never_changes_a_kpi(book, pack, roles, data):
    df = data.draw(book)
    seed = data.draw(st.integers(0, 2**16))
    _same(_kpis(pack, roles, df), _kpis(pack, roles, df.sample(frac=1, random_state=seed)
                                        .reset_index(drop=True)))


@pytest.mark.parametrize("suffix", [".csv", ".json", ".xlsx"])
@pytest.mark.parametrize("book,pack,roles", BOOKS, ids=SHAPES)
@given(data=st.data())
def test_p3_the_file_format_never_changes_a_kpi(book, pack, roles, suffix, data):
    df = data.draw(book)
    if suffix == ".xlsx":
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        raw = buf.getvalue()
    else:
        raw = (df.to_csv(index=False) if suffix == ".csv"
               else df.to_json(orient="records", date_format="iso")).encode()
    _same(_kpis(pack, roles, df), _kpis(pack, roles, _read_bytes(raw, suffix)[0]))


@pytest.mark.parametrize("book,pack,roles", BOOKS, ids=SHAPES)
@given(data=st.data())
def test_p3_dates_written_as_text_change_nothing(book, pack, roles, data):
    """A date column as text (an API's ISO strings, a sheet's cells) reads as the dates."""
    df = data.draw(book)
    text = df.assign(**{c: df[c].dt.strftime("%Y-%m-%dT%H:%M:%S")
                        for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])})
    _same(_kpis(pack, roles, df), _kpis(pack, roles, text))


@pytest.mark.parametrize("book,pack,roles", BOOKS[:2], ids=["mrr_export", "subscription_list"])
@given(data=st.data())
def test_p3_money_in_another_scale_scales_money_kpis_only(book, pack, roles, data):
    df = data.draw(book)
    k = data.draw(st.sampled_from([0.01, 3.0, 1000.0]))
    want = _kpis(pack, roles, df)
    got = _kpis(pack, roles, df.assign(mrr=df["mrr"] * k))
    _same(want, got, {n: k for n in want if pack.kpi(n).unit == "money"})


# ── P4: never a confident 0 from missing data ─────────────────────────────────────────────────

@pytest.mark.parametrize("book,pack,roles", BOOKS, ids=SHAPES)
@given(data=st.data())
def test_p4_a_kpi_without_a_role_it_needs_is_unavailable_never_0(book, pack, roles, data):
    df = data.draw(book)
    for role in roles:
        less = {r: c for r, c in roles.items() if r != role}
        for k in pack.kpis:
            if dp.computable(pack, k.name, roles) and not dp.computable(pack, k.name, less):
                got = dp.measure(df, less, pack, k.name)
                assert isinstance(got, dp.Unavailable) and got.reason in (
                    "role_missing", "end_date_missing"), (role, k.name, got)


@pytest.mark.parametrize("book,pack,roles", BOOKS, ids=SHAPES)
@given(data=st.data())
def test_p4_a_blank_column_a_kpi_doesnt_read_changes_nothing(book, pack, roles, data):
    df = data.draw(book)
    role = data.draw(st.sampled_from(sorted(r for r in roles if r not in ("customer", "employee"))))
    whole, blank = _kpis(pack, roles, df), _kpis(pack, roles, df.assign(**{roles[role]: None}))
    _same({k: v for k, v in whole.items() if role not in _reads(pack, k)},
          {k: v for k, v in blank.items() if role not in _reads(pack, k)})


def _reads(pack, name: str) -> set[str]:
    """The roles a KPI reads: kpi_roles, a spell term's whole spell included (PR5-04)."""
    return dp.kpi_roles(pack, name)


@pytest.mark.parametrize("role,name", [("mrr", "paying_customers"), ("mrr", "opening_mrr"),
                                       ("status", "paying_customers")])
def test_p4_a_blank_column_a_kpi_reads_never_makes_a_0(role, name):
    """Every value of a role's column blank reads as the column missing (the filter `mrr > 0` or
    `status not in …` kept nobody: 0): the pack's formula without it (paying customers by
    MRR > 0 when there is no status), else unavailable for that reason, never 0."""
    df = pd.DataFrame({"customer_id": ["c0", "c1"],
                       "created": pd.to_datetime(["2024-01-01", "2024-03-01"]),
                       "ended_at": pd.to_datetime([pd.NaT, pd.NaT]), "status": ["active"] * 2,
                       "mrr": [100.0, 50.0], "segment": ["SMB"] * 2})
    roles = LIST_ROLES if name == "paying_customers" else MRR_ROLES
    if roles is MRR_ROLES:
        df = pd.DataFrame({"month": pd.to_datetime(["2025-10-01", "2026-10-01"]),
                           "customer_id": ["c0", "c0"], "mrr": [100.0] * 2, "segment": ["SMB"] * 2})
    got = dp.measure(df.assign(**{role: None}), roles, SAAS, name)
    less = dp.measure(df, {r: c for r, c in roles.items() if r != role}, SAAS, name)
    assert got == (less if isinstance(less, dp.Measured) else dp.Unavailable("blank_column", (role,)))


def test_p4_a_list_with_a_cancellation_date_but_no_end_date_has_no_logo_churn():
    """c0 asked to cancel in Mar 2025, inside the 12 months to 30 Jun 2025; with no end date
    the churn is unknown, never 0% (c0 was never in force, so nobody churned). New customers
    need no end date: still measured."""
    df = pd.DataFrame({"customer_id": ["c0", "c1", "c2"],
                       "created": pd.to_datetime(["2024-01-01", "2024-01-01", "2025-06-30"]),
                       "canceled_at": pd.to_datetime(["2025-03-01", pd.NaT, pd.NaT]),
                       "status": ["canceled", "active", "active"], "mrr": [100.0] * 3})
    roles = {"customer": "customer_id", "created": "created", "canceled_at": "canceled_at",
             "status": "status", "mrr": "mrr"}
    for k in ("logo_churn_rate", "churned_customers"):
        assert dp.measure(df, roles, SAAS, k) == dp.Unavailable("end_date_missing", ("ended_at",)), k
    assert isinstance(dp.measure(df, roles, SAAS, "new_customers"), dp.Measured)


# ── P5: one window for every reader ───────────────────────────────────────────────────────────

SPELL_FLOWS = {"saas": ["new_mrr", "churned_mrr", "reactivation_mrr", "new_customers",
                      "churned_customers"],
               "hr": ["separations", "hires", "avg_headcount", "attrition_rate"]}


@pytest.mark.parametrize("book,pack,roles", [BOOKS[0], BOOKS[2]], ids=["mrr_export", "roster"])
@given(data=st.data())
def test_p5_asking_for_the_default_window_changes_nothing(book, pack, roles, data):
    """The window a KPI reads by default, asked for explicitly, gives the same number and the
    same words: every reader of the window agrees on it."""
    df = data.draw(book)
    if pack is HR:   # a report date on a month-end (a mid-month as-of is the known C2 below)
        end = data.draw(st.sampled_from(list(pd.date_range("2022-01-31", "2026-10-31", freq="ME"))))
        df, roles = df.assign(report_date=end), {**roles, "report_date": "report_date"}
    w = dp.window(pack, df, roles)
    assume(w is not None)
    # the default asked for: 'the 12 months to the as-of', never its data-clipped first day (a
    # roster starting 31 Jul asked from 31 Jul is exactly 9 months, a different window: deep)
    asked = (w.as_of - pd.DateOffset(months=dp.WINDOW_MONTHS) + pd.Timedelta(days=1), w.as_of)
    again = dp.window(pack, df, roles, asked)   # every field a reader names, the year-earlier too
    for f in ("lo", "as_of", "month_ends", "moves_from", "cohort_start", "full_cohort", "months",
              "move_months"):
        assert getattr(again, f) == getattr(w, f), f
        if w.year_earlier():
            assert getattr(again.year_earlier(), f) == getattr(w.year_earlier(), f), f
    for k in (k for k in SPELL_FLOWS[pack.domain] if dp.computable(pack, k, roles)):
        a, b = dp.compute_kpi(df, roles, pack, k), dp.compute_kpi(df, roles, pack, k, window=asked)
        assert (a is None) == (b is None) and (a is None or b == pytest.approx(a)), k
        assert dp.window_words(pack, df, roles, k) == dp.window_words(pack, df, roles, k, asked), k


# ── Tier (b): every pack dataset through the production reader ────────────────────────────────

def _pack_datasets():
    from tests.test_dashboard_filters import PACK_DATASETS
    return PACK_DATASETS


def _frame(name):
    from tests.test_oracle import _built
    _, clean, contract, _ = _built(name)
    pack = dp.pack_for(contract.domain)
    return clean, pack, {r: c for r, c in contract.roles.items() if c in clean.columns}


@pytest.mark.parametrize("name", _pack_datasets())
@settings(max_examples=3)
@given(seed=st.integers(0, 2**16))
def test_tier_b_row_order_never_changes_a_kpi(name, seed):
    df, pack, roles = _frame(name)
    _same(_kpis(pack, roles, df), _kpis(pack, roles, df.sample(frac=1, random_state=seed)
                                        .reset_index(drop=True)))


@pytest.mark.parametrize("name", _pack_datasets())
def test_tier_b_groups_add_up_to_the_total(name):
    df, pack, roles = _frame(name)
    for g in (roles[g] for g in pack.overview_groups if g in roles):
        _check_partition(pack, roles, df, g)


def test_p5_a_window_the_data_cuts_short_keeps_its_month_ends_asked_or_not():
    """Deep profile (5.7b-2a): a roster known from 31 Jul 2024 (its first exit), as of 30 Apr
    2025: the 12 months to it are cut to 31 Jul, with 10 month-ends from 31 Jul (the reference's
    rule). Asking 'the 12 months to 30 Apr 2025' gives the same window."""
    df = pd.DataFrame({"employee_id": ["e0", "e1"],
                       "hire_date": pd.to_datetime(["2022-01-01", "2022-01-01"]),
                       "termination_date": pd.to_datetime(["2024-07-31", pd.NaT]),
                       "department": ["Ops", "Ops"], "status": ["Terminated", "Active"],
                       "report_date": pd.to_datetime(["2025-04-30"] * 2)})
    roles = {**HR_ROLES, "report_date": "report_date"}
    w = dp.window(HR, df, roles)
    assert w.month_ends[0] == pd.Timestamp("2024-07-31") and w.months == 10
    again = dp.window(HR, df, roles, (pd.Timestamp("2024-05-01"), pd.Timestamp("2025-04-30")))
    assert (again.lo, again.month_ends) == (w.lo, w.month_ends)


def test_p5_a_mid_month_as_of_reads_the_same_window_asked_or_not():
    """As of 1 Jan 2023, the 12 months to it read 12 month-ends; the same span asked as 'since
    2 Jan 2022' read 13 (31 Jan 2022 too): a span of exactly 12 months has 12 (C2)."""
    df = pd.DataFrame({"employee_id": ["e0", "e1"],
                       "hire_date": pd.to_datetime(["2022-01-01", "2023-01-01"]),
                       "termination_date": pd.to_datetime([pd.NaT, pd.NaT]),
                       "department": ["Ops", "Ops"], "status": ["Active", "Active"]})
    w = dp.window(HR, df, HR_ROLES)
    asked = (w.first_day, w.as_of)
    assert dp.window_words(HR, df, HR_ROLES, "avg_headcount") == \
        dp.window_words(HR, df, HR_ROLES, "avg_headcount", asked)


@pytest.mark.parametrize("name", _pack_datasets())
@settings(max_examples=2)
@given(k=st.sampled_from([0.01, 1000.0]))
def test_tier_b_money_in_another_scale_scales_money_kpis_only(name, k):
    df, pack, roles = _frame(name)
    money = dp.money_columns(pack, roles)
    scaled = df.assign(**{c: pd.to_numeric(df[c], errors="coerce") * k for c in money})
    want = _kpis(pack, roles, df)
    _same(want, _kpis(pack, roles, scaled), {n: k for n in want if pack.kpi(n).unit == "money"})


# ── D: the product against the independent reference (evals/reference_kpis.py) ──────────────

@pytest.mark.parametrize("book,pack,roles,spec", [   # broken runs too: the cadence is declared (C7)
    (*BOOKS[0], "book_mrr_export"), (*BOOKS[1], "book_subscription_list"),
    (*BOOKS[2], "book_roster"), (*BOOKS[3], "book_hr_snapshots")], ids=SHAPES)
@given(data=st.data())
def test_d_the_product_equals_the_reference(book, pack, roles, spec, data):
    from evals.reference_kpis import reference
    df = data.draw(book)
    ref = reference(spec, df)
    for k, v in _kpis(pack, roles, df).items():
        if k in ref:
            assert (v is None) == (ref[k] is None), (k, v, ref[k])
            if v is not None:
                assert v == pytest.approx(ref[k], rel=1e-9, abs=1e-9), k
    for k in (k for k in ("mrr", *FLOWS, "nrr") if f"{k}_by_segment" in ref and ref["new_mrr"] is not None
              and dp.computable(pack, k, roles)):
        got = dp.compute_kpi(df, roles, pack, k, by="segment")   # C3: each segment's own lines
        got = {} if got is None else {str(s): v for s, v in got.items() if v}
        assert got == pytest.approx({str(s): v for s, v in ref[f"{k}_by_segment"].items() if v}), k


def test_d_nobody_paying_at_the_as_of_is_0_mrr_not_unavailable():
    """c0 started and ended on 1 Jun 2023, the as-of: nobody pays then, a known $0."""
    df = pd.DataFrame({"customer_id": ["c0"], "created": pd.to_datetime(["2023-06-01"]),
                       "ended_at": pd.to_datetime(["2023-06-01"]), "status": ["canceled"],
                       "mrr": [50.0], "segment": ["SMB"]})
    assert dp.compute_kpi(df, LIST_ROLES, SAAS, "mrr") == 0


def test_d_a_person_absent_from_the_newest_snapshot_is_gone():
    """e1 is on the Jan, Jun and Jul 2022 snapshots only; e0 on every one to Oct 2026. The
    irregular gaps of e1 switched the guessed 'stopped' cut off, so e1 still counted in Oct
    2026; the pack declares monthly snapshots (C7)."""
    roster = pd.DataFrame({"employee_id": ["e0", "e1", "e1"],
                           "hire_date": pd.to_datetime(["2022-01-01", "2022-01-01", "2022-06-01"]),
                           "termination_date": pd.to_datetime([pd.NaT, "2022-02-01", "2022-08-02"]),
                           "department": ["Ops"] * 3, "status": ["Active", "Terminated", "Terminated"]})
    assert dp.compute_kpi(_snapshots(roster), SNAP_ROLES, HR, "headcount") == 1


@pytest.mark.parametrize("months,ids", [
    (["2025-09-01", "2025-08-01"], ["c0", "c1"]),                              # nobody repeats
    (["2025-09-01", "2025-05-01", "2025-07-01", "2025-08-01"], ["c0", "c1", "c1", "c1"])])  # gaps
def test_d_a_customer_absent_from_the_newest_month_label_is_gone(months, ids):
    """The pack says month labels (monthly); the cadence was learned from each customer's gaps
    instead, so with none (or irregular ones) c1, absent from September, still paid then."""
    df = pd.DataFrame({"month": pd.to_datetime(months), "customer_id": ids,
                       "mrr": [50.0] * len(ids), "segment": ["SMB"] * len(ids)})
    assert dp.compute_kpi(df, MRR_ROLES, SAAS, "mrr") == 50.0


def test_d_a_person_back_on_the_snapshots_after_a_gap_is_a_hire():
    """e3 leaves on 5 Jan 2025 and is back on 1 Nov 2025: on month-end snapshots, absent Jan to
    Oct 2025, present again from 30 Nov 2025. Hires in the 12 months to 31 Oct 2026 are 1 (the
    reference); the hires read each person's first snapshot only, so a return is never one."""
    roster = pd.DataFrame({"employee_id": ["e0", "e3", "e3"],
                           "hire_date": pd.to_datetime(["2022-01-01", "2022-01-01", "2025-11-01"]),
                           "termination_date": pd.to_datetime([pd.NaT, "2025-01-05", pd.NaT]),
                           "department": ["Ops"] * 3, "status": ["Active", "Terminated", "Active"]})
    assert dp.compute_kpi(_snapshots(roster), SNAP_ROLES, HR, "hires") == 1


def test_d_a_month_missing_from_the_snapshots_is_no_return():
    """PR7-01's rule, its edge (5.7b-2b red-team): no file at all for Mar 2026. e0 and e1 are on
    every other month-end: the April snapshot follows February's, so nobody comes back in April
    and nobody leaves in March (hires and exits 0 in the 12 months to 31 Oct 2026)."""
    ends = [t for t in pd.date_range("2025-01-31", "2026-10-31", freq="ME") if t.month != 3 or t.year != 2026]
    df = pd.DataFrame([(t, e, "Ops") for t in ends for e in ("e0", "e1")],
                      columns=["snapshot_date", "employee_id", "department"])
    assert (dp.compute_kpi(df, SNAP_ROLES, HR, "hires"), dp.compute_kpi(df, SNAP_ROLES, HR, "separations")) == (0, 0)


def test_d_snapshots_on_each_persons_own_day_of_the_month_are_one_run():
    """PR7-01's rule, its edge: e0 is on the 5th of each month, e1 on the 20th (monthly, the
    declared cadence). Neither ever leaves or comes back; e2 joins on 20 Jun 2026, a hire."""
    rows = [(pd.Timestamp(f"{y}-{m:02d}-{d:02d}"), e) for y, m in [(2025, m) for m in range(1, 13)]
            + [(2026, m) for m in range(1, 11)] for e, d in (("e0", 5), ("e1", 20))]
    rows += [(pd.Timestamp(f"2026-{m:02d}-20"), "e2") for m in range(6, 11)]
    df = pd.DataFrame(rows, columns=["snapshot_date", "employee_id"]).assign(department="Ops")
    assert (dp.compute_kpi(df, SNAP_ROLES, HR, "hires"), dp.compute_kpi(df, SNAP_ROLES, HR, "separations")) == (1, 0)


def test_d_one_month_of_data_has_no_moves_and_no_cohort():
    """5.7b-2a (found by the mutation profile's draws): one month label opens and ends its
    cohort on the same date, with no month of moves. The reference said $0 new MRR and 0% logo
    churn, the product unavailable with another reason: both now say too short (GLOSSARY
    Unavailable), never a 0."""
    from evals.reference_kpis import reference
    df = pd.DataFrame({"month": pd.to_datetime(["2026-10-01"]), "customer_id": ["c0"],
                       "mrr": [50.0], "segment": ["SMB"]})
    ref = reference("book_mrr_export", df)
    for k in ("new_mrr", "opening_customers", "logo_churn_rate", "nrr"):
        assert ref[k] is None and dp.measure(df, MRR_ROLES, SAAS, k) == dp.Unavailable("too_short"), k


def test_d_snapshots_taken_on_each_customers_own_billing_day_all_count():
    """Red-team (5.7b-2 plan): an export with each customer on its own billing day (c0 on the
    5th, c1 on the 20th) is still monthly: as of 20 Oct both pay, never only the customers
    billed on the newest day; c2, last billed in August, is gone."""
    rows = [(f"2026-{m:02d}-05", "c0") for m in range(6, 11)] + \
        [(f"2026-{m:02d}-20", "c1") for m in range(6, 11)] + [("2026-08-12", "c2")]
    df = pd.DataFrame(rows, columns=["month", "customer_id"]).assign(
        month=lambda d: pd.to_datetime(d["month"]), mrr=50.0, segment="SMB")
    assert dp.compute_kpi(df, MRR_ROLES, SAAS, "mrr") == 100.0
