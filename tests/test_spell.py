"""Spells (Domain 5, S1): a stock at a date and the flows in and out of it, from start and end
dates (a roster) or from snapshots. The toy pack is a subscription book (Domain 6's shape): the
second reader of `spell`, so nothing here is HR-only."""
import pandas as pd
import pytest
import yaml

from domain_pack import (Pack, Unavailable, Window, compute_kpi, load_packs, measure, spell_as_of,
                         spell_story, spell_view, today_only, window)

SUBS = """
domain: toysubs
strong:
  - {sub: [subscriber_id], start: [start_date], end: [cancel_date]}
signals:
  - {sub: [subscriber_id], start: [start_date]}
roles:
  snapshot_date: {kind: date, headers: [snapshot_date]}
  start_date:    {kind: date, headers: [start_date]}
  cancel_date:   {kind: date, headers: [cancel_date]}
  subscriber_id: {kind: id, headers: [subscriber_id]}
  plan:          {kind: category, headers: [plan]}
  kind:          {kind: category, headers: [kind]}
  seats:         {kind: number, headers: [seats]}
  status:        {kind: status, headers: [status]}
spell: {entity: subscriber_id, start: start_date, end: cancel_date, ended: [cancelled]}
kpis:
  - {name: active, label: Active subscribers, unit: count, tile: true,
     num: [[{spell: active, where: [[{role: kind, not_in: [trial]}]]}], [{spell: active}]]}
  - {name: seats, label: Seats, num: [[{spell: active, sum: seats}]]}
  - {name: avg_active, label: Average active subscribers,
     num: [[{spell: average, where: [[{role: kind, not_in: [trial]}]]}], [{spell: average}]]}
  - {name: cancels, label: Cancellations, unit: count,
     num: [[{spell: ends, where: [[{role: kind, not_in: [trial]}]]}], [{spell: ends}]]}
  - {name: starts, label: New subscribers, unit: count,
     num: [[{spell: starts, where: [[{role: kind, not_in: [trial]}]]}], [{spell: starts}]]}
  - {name: churn, label: Churn rate, unit: percent, num: [[{kpi: cancels}]], den: [[{kpi: avg_active}]]}
caveats:
  read_as_domain_weak: "Read as subscription data: {roles}."
"""


@pytest.fixture
def pack(monkeypatch):
    monkeypatch.setenv("ANALYSTAI_TODAY", "2026-11-01")
    return Pack.model_validate(yaml.safe_load(SUBS))


ROLES = {"subscriber_id": "subscriber_id", "start_date": "start_date", "cancel_date": "cancel_date",
         "plan": "plan", "kind": "kind", "seats": "seats", "status": "status"}


def _roster():
    """One row per subscription spell. S1 left and came back (two spells); S7 is a trial."""
    rows = [  # id, start, cancel, plan, kind, seats, status
        ("S1", "2024-01-10", "2025-12-15", "pro", "paid", 5, "cancelled"),
        ("S1", "2026-03-01", "", "pro", "paid", 6, "active"),
        ("S2", "2023-05-01", "", "basic", "paid", 1, "active"),
        ("S3", "2023-05-01", "2026-06-30", "basic", "paid", 2, "cancelled"),
        ("S4", "2025-12-20", "2026-11-20", "pro", "paid", 3, "active"),   # cancels after today
        ("S5", "2026-02-01", "", "pro", "paid", 4, "cancelled"),          # cancelled, date unknown
        ("S6", "2026-11-10", "", "pro", "paid", 9, "active"),             # starts after today
        ("S7", "2026-01-05", "2026-02-05", "pro", "trial", 1, "cancelled"),
        ("S8", "2022-01-01", "2026-10-31", "basic", "paid", 1, "cancelled"),
        ("S9", "2021-01-01", "2023-03-01", "basic", "paid", 1, "cancelled"),   # the earliest exit
    ]
    return pd.DataFrame(rows, columns=["subscriber_id", "start_date", "cancel_date", "plan",
                                       "kind", "seats", "status"])


def _month_ends(as_of):
    return [(as_of.to_period("M") - k).end_time.normalize() for k in range(11, -1, -1)]


def _active_at(df, t):
    s, e = pd.to_datetime(df["start_date"]), pd.to_datetime(df["cancel_date"], errors="coerce")
    ok = (s <= t) & (e.isna() | (e > t)) & ~((df["status"] == "cancelled") & e.isna())
    return df.loc[ok & (df["kind"] != "trial"), "subscriber_id"].nunique()


def test_as_of_is_the_latest_date_not_after_today(pack):
    assert spell_as_of(pack, _roster(), ROLES) == pd.Timestamp("2026-10-31")


def test_active_counts_people_in_force_at_the_as_of(pack):
    df = _roster()
    # S1 (2nd spell), S2, S4 (cancels after today), not S3/S5/S6/S7/S8 → 3
    assert compute_kpi(df, ROLES, pack, "active") == 3 == _active_at(df, pd.Timestamp("2026-10-31"))
    assert compute_kpi(df, ROLES, pack, "seats") == 6 + 1 + 3


def test_flows_are_events_in_the_12_months_and_the_rate_divides_by_the_month_end_mean(pack):
    df = _roster()
    # cancels in (2025-10-31, 2026-10-31], trials out: S1 (15 Dec), S3, S8; S4 is after today
    assert compute_kpi(df, ROLES, pack, "cancels") == 3
    # starts: S1's 2nd spell, S4, S5 (its cancellation date is unknown: a start all the same)
    assert compute_kpi(df, ROLES, pack, "starts") == 3
    ends = _month_ends(pd.Timestamp("2026-10-31"))
    avg = sum(_active_at(df, t) for t in ends) / 12
    assert avg == pytest.approx(50 / 12)
    assert compute_kpi(df, ROLES, pack, "avg_active") == pytest.approx(avg)
    assert compute_kpi(df, ROLES, pack, "churn") == pytest.approx(3 / avg)


def test_a_rehire_is_one_person_and_two_events(pack):
    df = pd.concat([_roster(), pd.DataFrame([("S1", "2026-03-01", "2026-09-30", "pro", "paid", 1,
                                              "cancelled")], columns=_roster().columns)],
                   ignore_index=True)
    df = df[~((df["subscriber_id"] == "S1") & (df["cancel_date"] == ""))]
    assert compute_kpi(df, ROLES, pack, "cancels") == 4   # S1 left twice in the window


def test_by_group_uses_the_spells_own_row(pack):
    by = compute_kpi(_roster(), ROLES, pack, "cancels", by="plan")
    assert by.to_dict() == {"pro": 1, "basic": 2}


def test_by_period_has_every_month_and_reconciles(pack):
    df = _roster()
    df = df[df["subscriber_id"] != "S5"]   # cancelled, date unknown: a start that never joins
    df["_period"] = pd.to_datetime(df["start_date"]).dt.to_period("M")
    act = compute_kpi(df, ROLES, pack, "active", by="_period")
    # gap-free from the earliest exit (2023-03) to the as-of, incl. months no row is dated in
    assert act.index[0] == pd.Period("2023-03", "M") and act.index[-1] == pd.Period("2026-10", "M")
    assert len(act) == len(pd.period_range("2023-03", "2026-10", freq="M"))
    starts = compute_kpi(df, ROLES, pack, "starts", by="_period").reindex(act.index, fill_value=0)
    ends = compute_kpi(df, ROLES, pack, "cancels", by="_period").reindex(act.index, fill_value=0)
    for p in act.index[1:]:   # opening + in − out = closing, every month
        assert act[p] == act[p - 1] + starts[p] - ends[p], p


def test_a_filter_keeps_the_as_of_and_judges_the_state_on_all_rows(pack):
    df = _roster()
    pro = df[df["plan"] == "pro"]
    assert compute_kpi(pro, ROLES, pack, "active", full=df) == 2   # S1, S4
    # the window stays the data's: a filter to basic (latest basic date 2026-10-31 too) and to a
    # group whose last date is earlier both read the 12 months to 31 Oct 2026
    s3 = df[df["subscriber_id"] == "S3"]
    assert compute_kpi(s3, ROLES, pack, "active", full=df) is None or \
        compute_kpi(s3, ROLES, pack, "active", full=df) == 0


SNAP_ROLES = {**ROLES, "snapshot_date": "snapshot_date"}


def _snapshots():
    """Month-end snapshots listing active subscribers. A vanishes after March (no date); B moves
    from basic to pro in April and cancels in July (a final row with the date); C stays; D's
    cancel date is after today."""
    rows = []
    for m in pd.period_range("2026-01", "2026-10", freq="M"):
        t = m.end_time.normalize().strftime("%Y-%m-%d")
        if m <= pd.Period("2026-03", "M"):
            rows.append((t, "A", "2025-06-01", "", "basic", "paid", 1, "active"))
        if m <= pd.Period("2026-07", "M"):
            final = m == pd.Period("2026-07", "M")
            rows.append((t, "B", "2025-01-01", "2026-07-12" if final else "",
                         "basic" if m < pd.Period("2026-04", "M") else "pro", "paid", 2,
                         "cancelled" if final else "active"))
        rows.append((t, "C", "2024-01-01", "", "pro", "paid", 3, "active"))
        rows.append((t, "D", "2024-01-01", "2026-11-20" if m == pd.Period("2026-10", "M") else "",
                     "basic", "paid", 4, "active"))
    return pd.DataFrame(rows, columns=["snapshot_date", "subscriber_id", "start_date",
                                       "cancel_date", "plan", "kind", "seats", "status"])


def test_snapshots_a_level_is_never_summed_and_leavers_without_a_date_are_out(pack):
    df = _snapshots()
    assert spell_as_of(pack, df, SNAP_ROLES) == pd.Timestamp("2026-10-31")
    assert compute_kpi(df, SNAP_ROLES, pack, "active") == 2          # C, D
    assert compute_kpi(df, SNAP_ROLES, pack, "seats") == 3 + 4       # never × 10 months
    # A vanished after March: out from April; B cancelled 12 Jul
    assert compute_kpi(df, SNAP_ROLES, pack, "cancels") == 2


def test_snapshots_an_exit_is_counted_where_it_left(pack):
    by = compute_kpi(_snapshots(), SNAP_ROLES, pack, "cancels", by="plan")
    assert by.to_dict() == {"basic": 1, "pro": 1}   # B left from pro (moved in April), A from basic


def test_snapshots_a_filter_counts_who_is_in_the_group_at_the_date(pack):
    df = _snapshots()
    df["_period"] = pd.to_datetime(df["snapshot_date"]).dt.to_period("M")
    basic = df[df["plan"] == "basic"]
    act = compute_kpi(basic, SNAP_ROLES, pack, "active", by="_period", full=df)
    assert act[pd.Period("2026-03", "M")] == 3   # A, B, D
    assert act[pd.Period("2026-05", "M")] == 1   # B moved to pro: only D


def test_a_typo_date_never_makes_an_unbounded_series(pack):
    df = _roster()
    df.loc[len(df)] = ("S0", "1900-01-01", "1901-01-01", "basic", "paid", 1, "cancelled")
    df["_period"] = pd.to_datetime(df["start_date"]).dt.to_period("W")
    act = compute_kpi(df, ROLES, pack, "active", by="_period")
    assert len(act) <= 520 and act.index[0].start_time >= pd.Timestamp("2016-10-01")


def test_an_asked_window_from_mid_month_keeps_that_months_end(pack):
    from domain_pack import window
    pts = window(pack, _roster(), ROLES, ("2026-03-15", "2026-06-30")).month_ends
    assert pts[0] == pd.Timestamp("2026-03-31") and pts[-1] == pd.Timestamp("2026-06-30") and len(pts) == 4
    assert window(pack, _roster(), ROLES).months == 12   # the default window keeps its 12


def test_concurrent_spells_of_one_entity_all_count_when_the_pack_says_so(pack):
    """Domain 6 S3 (red-team R1): a customer with a base plan and a newer add-on cancelled since
    was out of force (its latest spell had ended), losing the base plan's seats. With
    `concurrent: true` every spell in force counts (counts stay distinct entities); HR's rehires
    never overlap, so its rule (the latest spell) is unchanged."""
    raw = yaml.safe_load(SUBS)
    raw["spell"]["concurrent"] = True
    both = Pack.model_validate(raw)
    df = pd.DataFrame([("C1", "2025-01-01", "", "pro", "paid", 5, "active"),
                       ("C1", "2026-03-01", "2026-06-01", "pro", "paid", 2, "cancelled"),
                       ("C2", "2025-01-01", "", "pro", "paid", 1, "active"),
                       ("C2", "2025-06-01", "", "pro", "paid", 3, "active")],
                      columns=["subscriber_id", "start_date", "cancel_date", "plan", "kind",
                               "seats", "status"])
    assert compute_kpi(df, ROLES, both, "active") == 2 and compute_kpi(df, ROLES, both, "seats") == 9
    assert compute_kpi(df, ROLES, pack, "seats") == 3   # the latest spell only: C1 out, C2's add-on


def test_month_labels_on_the_first_mean_the_month(pack, monkeypatch):
    """Domain 6 S5 (red-team R17): an MRR export labels October '2026-10-01'; read as a date,
    every figure was 'as of 1 Oct' and the window a month short. With month_labels the as-of
    is the labelled month's end (clipped to today); unset, the dates stay as written."""
    monkeypatch.setenv("ANALYSTAI_TODAY", "2026-11-05")
    raw = yaml.safe_load(SUBS)
    snaps = pd.DataFrame({"snapshot_date": ["2026-09-01", "2026-10-01", "2026-11-01"],
                          "subscriber_id": ["S1"] * 3, "start_date": ["2025-01-01"] * 3,
                          "cancel_date": [""] * 3, "kind": ["paid"] * 3, "status": ["active"] * 3})
    roles = {**{k: v for k, v in ROLES.items() if k in snaps}, "snapshot_date": "snapshot_date"}
    assert spell_as_of(Pack.model_validate(raw), snaps, roles) == pd.Timestamp("2026-11-01")
    raw["spell"]["month_labels"] = True
    labelled = Pack.model_validate(raw)
    assert spell_as_of(labelled, snaps, roles) == pd.Timestamp("2026-11-05")   # Nov, to today
    assert spell_as_of(labelled, snaps.iloc[:2], roles) == pd.Timestamp("2026-10-31")
    mid = snaps.assign(snapshot_date=["2026-09-15", "2026-10-01", "2026-10-31"])
    assert spell_as_of(labelled, mid, roles) == pd.Timestamp("2026-10-31")   # not labels: as written


def test_a_converted_trial_counts_from_its_trial_end(pack):
    """Red-team R3: a subscription converted from a trial pays from the trial's end; counted
    from its start, a December trial was a paying subscriber at 31 Dec."""
    raw = yaml.safe_load(SUBS)
    raw["roles"]["trial_end"] = {"kind": "date", "headers": ["trial_end"]}
    raw["spell"]["paid_from"] = "trial_end"
    paid = Pack.model_validate(raw)
    df = pd.DataFrame([("T1", "2025-12-20", "", "2026-01-03", "paid", 1, "active"),
                       ("T2", "2025-12-01", "", "", "paid", 1, "active")],
                      columns=["subscriber_id", "start_date", "cancel_date", "trial_end", "kind",
                               "seats", "status"])
    roles = {**{k: v for k, v in ROLES.items() if k in df}, "trial_end": "trial_end"}
    frame = df.assign(_p=pd.Series(pd.NaT, index=df.index).astype("period[M]"))
    by = compute_kpi(frame, roles, paid, "active", by="_p", full=df)
    assert by[pd.Period("2025-12", "M")] == 1 and by[pd.Period("2026-01", "M")] == 2
    assert compute_kpi(frame, roles, pack, "active", by="_p", full=df)[pd.Period("2025-12", "M")] == 2


def _ts(*days):
    return tuple(pd.Timestamp(d) for d in days)


def test_a_window_its_readers_can_not_share_is_refused():
    """Window's invariants (mutation gate 5.7b-2a): a span, strictly rising month-ends inside
    (lo, as_of], none before the first date, the last one the as-of."""
    ok = dict(lo=pd.Timestamp("2026-01-31"), as_of=pd.Timestamp("2026-03-31"),
              month_ends=_ts("2026-02-28", "2026-03-31"), first=pd.Timestamp("2025-01-01"),
              known=pd.Timestamp("2025-01-01"))
    Window(**ok)
    for bad in ({"lo": pd.Timestamp("2026-03-31"), "month_ends": _ts("2026-03-31")},   # no span
                {"month_ends": ()},                                                  # no month-end
                {"month_ends": _ts("2026-02-28")},                                   # as-of's missing
                {"month_ends": _ts("2026-03-31", "2026-03-31")},                     # repeated
                {"month_ends": _ts("2026-02-28", "2026-02-28", "2026-03-31")},       # repeated, 3
                {"month_ends": _ts("2026-01-31", "2026-03-31")},                     # at lo
                {"month_ends": _ts("2026-02-28", "2026-03-31"),
                 "first": pd.Timestamp("2026-03-01")}):                              # before first
        with pytest.raises(ValueError, match="a window its readers can't share"):
            Window(**{**ok, **bad})


def test_an_asked_span_of_exactly_n_months_from_the_2nd_reads_n_month_ends(pack):
    """2 Feb–1 May is exactly 3 months: its first month is dropped (rule 3), as from the 15th."""
    w = window(pack, _roster(), ROLES, ("2026-02-02", "2026-05-01"))
    assert w.month_ends == _ts("2026-03-31", "2026-04-30", "2026-05-01")


def test_no_window_when_every_date_is_after_today(pack):
    """A first date but no as-of (all dated after today): no window, never a crash."""
    assert window(pack, _roster().assign(start_date="2027-01-05", cancel_date=""), ROLES) is None
    assert window(pack, _roster(), ROLES, ("2026-11-01", None)) is None   # the day after the as-of


def test_a_year_earlier_shifts_the_asked_span_and_needs_all_its_months(pack):
    df = _roster()   # as-of 31 Oct 2026, data from 2023
    assert window(pack, df, ROLES, ("2026-03-15", "2026-09-30")).year_earlier().asked \
        == _ts("2025-03-15", "2025-09-30")
    assert window(pack, df, ROLES, (None, "2026-09-30")).year_earlier().asked \
        == _ts("2024-10-01", "2025-09-30")   # no asked start: the 12 months to the asked end
    late = pd.DataFrame([("A", "2025-03-01", "", "pro", "paid", 1, "active"),
                         ("B", "2026-10-31", "", "pro", "paid", 1, "active")], columns=df.columns)
    assert window(pack, late, ROLES).year_earlier() is None   # data from Mar 2025: 8 months, none


def test_duplicate_row_labels_are_refused_by_name(pack):
    df = _roster()
    with pytest.raises(ValueError, match="^spell_view: the whole data needs unique row labels$"):
        spell_view(pack, df.set_axis([0] * len(df)), ROLES)


def test_each_spell_reason_is_its_own(pack):
    """measure's reasons on a spell KPI (C5): a missing spell role names the roles; an asked
    period wholly before the data is outside it; no dated row at all has no rows."""
    df = _roster()
    no_start = {k: v for k, v in ROLES.items() if k != "start_date"}
    assert measure(df, no_start, pack, "active") == Unavailable("role_missing",
                                                                ("subscriber_id", "start_date"))
    assert measure(df, ROLES, pack, "active", window=("2019-01-01", "2019-06-30")) \
        == Unavailable("outside_the_data")
    assert measure(df.assign(start_date="", cancel_date=""), ROLES, pack, "active") \
        == Unavailable("no_rows")


def test_by_month_an_average_is_that_months_end(pack):
    """A month's own window has one month-end, so its average is the count in force then."""
    df = _roster()
    df = df[df["subscriber_id"] != "S5"]
    df["_period"] = pd.to_datetime(df["start_date"]).dt.to_period("M")
    avg = compute_kpi(df, ROLES, pack, "avg_active", by="_period")
    assert avg.notna().all()
    for p in avg.index:
        assert avg[p] == _active_at(df, min(p.end_time.normalize(), pd.Timestamp("2026-10-31"))), p
    q = compute_kpi(df.assign(_period=df["_period"].dt.asfreq("Q")), ROLES, pack, "avg_active",
                    by="_period")   # a quarter averages its three month-ends
    ends = [pd.Timestamp(t) for t in ("2026-01-31", "2026-02-28", "2026-03-31")]
    assert q[pd.Period("2026Q1", "Q")] == pytest.approx(sum(_active_at(df, t) for t in ends) / 3)


def test_the_today_only_sentence_counts_its_rows(pack):
    raw = yaml.safe_load(SUBS)
    raw["spell"]["today_only"] = ["paused"]
    held = Pack.model_validate(raw)
    df = _roster()
    one = df.assign(status=["paused"] + list(df["status"][1:]))
    assert today_only(held, one, ROLES) == f"1 row is {held.spell.words.today_only}"
    assert today_only(held, one.assign(status="paused"), ROLES).startswith("10 rows are ")
    assert today_only(held, df, ROLES) == ""                 # none held today
    assert today_only(pack, one, ROLES) == ""                # the pack holds no status for today
    retail = next(p for p in load_packs() if p.spell is None)
    assert today_only(retail, one, ROLES) == ""              # no spell at all


def test_a_pack_with_no_count_stock_says_rows_against_people_without_a_count(pack):
    """A user pack may count a stock only in money: the people line drops its 'in force' count
    (nothing to read it from) and never fails."""
    raw = yaml.safe_load(SUBS)
    raw["kpis"][0]["tile"] = False   # 'active' is the only count stock
    no_heads = Pack.model_validate(raw)
    line = next(s for s in spell_story(no_heads, _roster(), ROLES) if "more than one row" in s)
    assert line.endswith("(1 with more than one row).")


def test_the_in_force_marker_reads_the_stock_tiles_rows_or_every_row_without_one(pack):
    """By hand from _roster at 15 Jan 2026: S2, S3, S4, S8 and the trial S7 are in force (S1
    between its spells, S5 and S6 not started, S9 gone). The stock tile leaves the trial out; a
    pack with no stock tile reads every row in force."""
    from domain_pack import in_force_counted, stock_rows
    raw = yaml.safe_load(SUBS)
    raw["kpis"][0]["tile"] = False
    no_stock, df, t = Pack.model_validate(raw), _roster(), pd.Timestamp("2026-01-15")
    assert sorted(in_force_counted(df, ROLES, pack, t)) == [2, 3, 4, 8]
    assert sorted(in_force_counted(df, ROLES, no_stock, t)) == [2, 3, 4, 7, 8]
    assert stock_rows(df, ROLES, no_stock).index.equals(df.index)


def _weekly(present: dict[str, list[int]]) -> pd.DataFrame:
    weeks = pd.date_range("2026-10-04", periods=4, freq="7D")
    return pd.DataFrame([(weeks[i], s) for s, ws in present.items() for i in ws],
                        columns=["snapshot_date", "subscriber_id"])


def test_snapshots_with_no_start_date_read_each_snapshot_when_no_cadence_is_declared(pack):
    """Weekly snapshots (4, from 4 Oct 2026); the toy pack declares no cadence, so each snapshot
    is a step. A is on all; B misses the 2nd and is back on the 3rd (a new spell: an exit on the
    2nd, a start on the 3rd); C first shows on the 2nd. The first snapshot holds no starts."""
    df, roles = _weekly({"A": [0, 1, 2, 3], "B": [0, 2, 3], "C": [1, 2, 3]}), \
        {"subscriber_id": "subscriber_id", "snapshot_date": "snapshot_date"}
    assert compute_kpi(df, roles, pack, "starts") == 2     # C, B's return
    assert compute_kpi(df, roles, pack, "cancels") == 1    # B on the 2nd


def test_snapshots_with_a_start_date_split_spells_by_it(pack):
    """Month-end snapshots Jan to Oct 2026. C stays. E is on Jan to Mar (started 2025-01-01) and
    May to Oct (started 2026-05-01): two spells, so one start in the window and an exit on 30 Apr.
    F has no start date and is gone after June: its exit (31 Jul) still counts."""
    ends = pd.date_range("2026-01-31", "2026-10-31", freq="ME")
    rows = [(t, "C", "2024-01-01") for t in ends]
    rows += [(t, "E", "2025-01-01" if t.month <= 3 else "2026-05-01") for t in ends if t.month != 4]
    rows += [(t, "F", None) for t in ends if t.month <= 6]
    df = pd.DataFrame(rows, columns=["snapshot_date", "subscriber_id", "start_date"]).assign(
        cancel_date="", plan="pro", kind="paid", seats=1, status="active")
    assert compute_kpi(df, SNAP_ROLES, pack, "starts") == 1     # E's return
    assert compute_kpi(df, SNAP_ROLES, pack, "cancels") == 2    # E on 30 Apr, F on 31 Jul
