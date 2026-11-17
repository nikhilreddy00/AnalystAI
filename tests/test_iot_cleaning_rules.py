"""Domain 7 cleaning rules, pinned by worked examples: a pack's invalid_when blanking, its units
conversion, the series double-write drop, and the per-series flags (clashes, clock-change folds,
silences, stuck runs). Every expected string and number is worked out by hand from the frame."""
import pandas as pd
import yaml

from agents.cleaning_agent import CleanContext, clean
from domain_pack import Pack
from tests.test_second_pack import BANK

F_TO_C = "[0.5555555555555556, -17.77777777777778]"
PACK = Pack.model_validate(yaml.safe_load(BANK.replace("  quantity:", """  pipe_temp: {kind: number, additive: false, headers: [pipe_temp]}
  temp_unit: {kind: category, headers: [temp_unit]}
  quality: {kind: category, headers: [quality]}
  quantity:""") + f"""invalid_when:
  status: {{values: [void], blanks: [amount]}}
  quality: {{values: [Bad, '0'], doubtful: [Uncertain, Questionable], blanks: [quantity, pipe_temp]}}
units:
  quantity: {{unit_role: temp_unit, to: "°C", convert: {{K: [1, -273.15]}}}}
  pipe_temp: {{unit_role: temp_unit, to: "°C", convert: {{"deg F": {F_TO_C}, K: [1, -273.15],
                                                        degF: {F_TO_C}, dC: [0.1, 0]}}}}
series_key: account_id
stuck_hours: {{quantity: 24, pipe_temp: 24}}
"""))
T0 = pd.Timestamp("2026-03-01 00:00")


def _clean(df: pd.DataFrame, dates=("txn_date",)):
    return clean(CleanContext(df=df, ids={"account_id"}, dates=set(dates) & set(df.columns),
                              targets=set(), drop_high_null=False, pack=PACK,
                              roles={c: c for c in df.columns}))


def _rows(key: str, hours, temps=None) -> list[dict]:
    temps = temps if temps is not None else [20.0 + i * 0.5 for i in range(len(hours))]
    return [{"account_id": key, "txn_date": T0 + pd.Timedelta(hours=h), "pipe_temp": v}
            for h, v in zip(hours, temps)]


def _flags(df: pd.DataFrame, **kw) -> dict:
    _, audit = _clean(df, **kw)
    return {d.strategy: d for d in audit.decisions if d.operation == "flag"}


def _said(d) -> tuple:
    return d.column, d.operation, d.strategy, d.reason, d.rows_affected, d.before_null_pct, d.after_null_pct


def test_a_value_its_flag_marks_invalid_is_blanked_once_per_measure_and_its_doubtful_rows_counted():
    df = pd.DataFrame({"account_id": list("ABCDEF"), "pipe_temp": [20.0, 900.0, None, 21.0, 22.0, 23.0],
                       "quality": ["Good", "Bad", "0", "Uncertain", "questionable ", "Good"]})
    out, audit = _clean(df)
    assert out["pipe_temp"].isna().tolist() == [False, True, True, False, False, False]
    said = [_said(d) for d in audit.decisions if d.column in ("pipe_temp", "quality")
            and d.strategy in ("invalid_flag", "doubtful_flag")]
    assert said == [
        ("pipe_temp", "sentinel_null", "invalid_flag", "1 pipe temp values on 2 rows its quality marks "
         "invalid (Bad, 0) read as missing; the rows are kept.", 1, 16.7, 33.3),
        ("quality", "flag", "doubtful_flag", "2 rows marked Uncertain, Questionable: kept and counted.",
         2, 0.0, 0.0)]
    _, audit = _clean(df.head(2))
    assert [d.reason for d in audit.decisions if d.strategy == "invalid_flag"] == [
        "1 pipe temp values on 1 row its quality marks invalid (Bad, 0) read as missing; the rows are kept."]


def test_a_measure_is_converted_once_by_its_unit_code_and_a_row_without_a_unit_is_blanked():
    """Audit pass 2: a row without a unit, in a column holding °F and K, is no °C reading."""
    df = pd.DataFrame({"account_id": list("ABCDEFG"),
                       "pipe_temp": [212.0, 50.0, 300.0, 205.0, 21.0, 22.0, None],
                       "temp_unit": ["deg F", "DegF", "K", "dC", "°C", None, "K"]})
    out, audit = _clean(df)
    assert out["pipe_temp"].round(6).tolist()[:5] == [100.0, 10.0, 26.85, 20.5, 21.0]
    assert out["pipe_temp"].iloc[5:].isna().all()
    assert out["temp_unit"].tolist() == ["°C"] * 5 + ["Unknown", "°C"]   # a blank label: Unknown
    said = [_said(d) for d in audit.decisions if d.strategy == "unit_converted"]
    assert said == [
        ("pipe_temp", "type_coerce", "unit_converted",
         "2 pipe temp values in deg F (their temp_unit) converted to °C: × 0.555556 − 17.7778.", 2, 14.3, 14.3),
        ("temp_unit", "type_coerce", "unit_converted",
         "2 rows' unit changed from deg F to °C, with their values.", 2, 14.3, 14.3),
        ("pipe_temp", "type_coerce", "unit_converted",
         "1 pipe temp values in K (their temp_unit) converted to °C: × 1 − 273.15.", 1, 14.3, 14.3),
        ("temp_unit", "type_coerce", "unit_converted",
         "2 rows' unit changed from K to °C, with their values.", 2, 14.3, 14.3),
        ("pipe_temp", "type_coerce", "unit_converted",
         "1 pipe temp values in dC (their temp_unit) converted to °C: × 0.1 + 0.", 1, 14.3, 14.3),
        ("temp_unit", "type_coerce", "unit_converted",
         "1 rows' unit changed from dC to °C, with their values.", 1, 14.3, 14.3)]


def _units(units, pack=PACK) -> tuple[list, list]:
    df = pd.DataFrame({"account_id": [chr(65 + i) for i in range(len(units))],
                       "pipe_temp": [20.0 + i for i in range(len(units))], "temp_unit": units})
    out, audit = clean(CleanContext(df=df, ids={"account_id"}, dates=set(), targets=set(),
                                    drop_high_null=False, pack=pack, roles={c: c for c in df.columns}))
    return out["pipe_temp"].tolist(), [_said(d) for d in audit.decisions
                                       if d.strategy in ("unit_converted", "unknown_unit")]


def test_a_unit_no_conversion_knows_is_blanked_its_three_commonest_named():
    temps, said = _units(["°C", "°R", "°R", "furlong", "X", "X", "X", "°C"])
    assert str(temps) == "[20.0, nan, nan, nan, nan, nan, nan, 27.0]"
    assert said == [("pipe_temp", "sentinel_null", "unknown_unit",
                     "6 pipe temp values in a unit with no conversion to °C (X (3), °R (2), furlong (1)) "
                     "read as missing, never averaged with the rest; the rows are kept.", 6, 0.0, 75.0)]
    temps, said = _units(["°C", "°R", "°R", None])   # a blank unit beside °R: which is it?
    assert str(temps) == "[20.0, nan, nan, nan]" and [s[3] for s in said] == [
        "3 pipe temp values in a unit with no conversion to °C (°R (2), (blank) (1)) read as missing, "
        "never averaged with the rest; the rows are kept."]


def test_a_blank_unit_beside_the_target_unit_or_its_other_name_is_that_unit():
    """No unit but `to` (or a 1 × + 0 name of it) in the column: a blank unit is no doubt."""
    assert _units(["°C", None, "°C"]) == ([20.0, 21.0, 22.0], [])
    rule = PACK.units["pipe_temp"]
    alias = PACK.model_copy(update={"units": {**PACK.units, "pipe_temp": rule.model_copy(
        update={"convert": {**rule.convert, "Celsius": (1.0, 0.0)}})}})
    assert _units(["Celsius", None, "°C"], alias) == ([20.0, 21.0, 22.0], [   # renamed, never converted
        ("temp_unit", "type_coerce", "unit_converted",
         "1 rows' unit changed from Celsius to °C, with their values.", 1, 33.3, 33.3)])


def test_one_clash_and_a_double_write_of_one_series():
    """C1 wrote 02:00 twice with another value (a clash, both kept); C2's 03:00 row is an exact
    copy (dropped). One series at a time has no clock change. A blank copy is no second value
    (audit pass 1): see test_iot.py's blanked-reading test."""
    rows = _rows("C1", range(6)) + _rows("C1", [2], [99.0]) + _rows("C2", range(6))
    df = pd.DataFrame(rows + [rows[-3]])
    out, audit = _clean(df)
    assert len(out) == len(df) - 1
    assert [_said(d) + (d.rows_removed,) for d in audit.decisions if d.operation == "drop_duplicates"] == [
        ("*", "drop_duplicates", "exact", "Removed 1 exact double writes (the same account_id, txn_date "
         "and every value).", 1, 0.0, 0.0, 1)]
    flags = {d.strategy: d for d in audit.decisions if d.operation == "flag"}
    assert set(flags) == {"series_clash"}
    assert _said(flags["series_clash"])[:5] == (
        "account_id", "flag", "series_clash", "1 time of one account_id with two different rows "
        "(C1 at 2026-03-01 02:00): both kept, check them at the source.", 1)


def test_a_clash_row_blank_in_another_column_is_still_a_clash():
    df = pd.DataFrame(_rows("C1", range(6)) + _rows("C1", [2], [99.0])).assign(amount=[1.0] * 6 + [None])
    assert _flags(df)["series_clash"].rows_affected == 1


def test_clashes_shared_by_fewer_than_half_the_series_are_clashes_three_named():
    rows = [r for k in ("G1", "G2", "G3", "G4", "G5") for r in _rows(k, range(6))]
    rows += _rows("G1", [1, 3], [9.0, 9.0]) + _rows("G2", [1, 4], [9.0, 9.0])
    flags = _flags(pd.DataFrame(rows))
    assert set(flags) == {"series_clash"}
    assert (flags["series_clash"].reason, flags["series_clash"].rows_affected) == (
        "4 times of one account_id with two different rows (G1 at 2026-03-01 01:00, G1 at "
        "2026-03-01 03:00, G2 at 2026-03-01 01:00): both kept, check them at the source.", 4)


def test_a_time_two_of_four_series_repeat_is_one_clock_change():
    rows = [r for k in ("F1", "F2", "F3", "F4") for r in _rows(k, range(6))]
    rows += _rows("F1", [2], [9.0]) + _rows("F2", [2], [9.0])
    flags = _flags(pd.DataFrame(rows))
    assert set(flags) == {"series_clock_change"}
    assert _said(flags["series_clock_change"])[:5] == (
        "account_id", "flag", "series_clock_change", "1 local time (2026-03-01 02:00) repeat for every "
        "account_id: the hour a clock change repeats, its offset dropped. Both readings are kept; "
        "durations across it can be an hour off.", 2)


def test_four_repeated_local_times_three_named():
    rows = [r for k in ("F1", "F2", "F3", "F4") for r in _rows(k, range(6))]
    rows += _rows("F1", [1, 2, 3, 4], [9.0] * 4) + _rows("F2", [1, 2, 3, 4], [9.0] * 4)
    flags = _flags(pd.DataFrame(rows))
    assert set(flags) == {"series_clock_change"}
    assert (flags["series_clock_change"].reason, flags["series_clock_change"].rows_affected) == (
        "4 local times (2026-03-01 01:00, 2026-03-01 02:00, 2026-03-01 03:00) repeat for every "
        "account_id: the hour a clock change repeats, its offset dropped. Both readings are kept; "
        "durations across it can be an hour off.", 8)


def test_an_unreadable_time_is_left_out_of_the_series_checks():
    rows = [{"account_id": "T1", "txn_date": f"2026-03-01 0{h}:00", "pipe_temp": 20.0 + h} for h in range(7)]
    rows += [{"account_id": "T1", "txn_date": "2026-03-01 02:00", "pipe_temp": 9.0}]
    rows += [{"account_id": "T1", "txn_date": "sensor offline", "pipe_temp": 1.0 + i} for i in range(3)]
    flags = _flags(pd.DataFrame(rows), dates=())
    assert set(flags) == {"series_clash"} and "(T1 at 2026-03-01 02:00)" in flags["series_clash"].reason


def test_a_silence_over_3x_the_usual_interval_in_a_series_of_20_rows_or_more():
    """S1 (20 rows, hourly): a 3 h step is no silence, a 4 h one is. S2 has 19 rows: too few."""
    s1 = [0, 1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14, 18, 19, 20, 21, 22, 23, 24]
    s2 = [*range(10), *range(14, 23)]
    flags = _flags(pd.DataFrame(_rows("S1", s1) + _rows("S2", s2)))
    assert set(flags) == {"series_gap"}
    assert _said(flags["series_gap"])[:5] == (
        "account_id", "flag", "series_gap", "1 silence longer than 3× the usual interval (S1 2026-03-01 "
        "14:00 to 2026-03-01 18:00 (4 h)): a dropout, or a source that writes only on change. "
        "Nothing is filled in.", 1)


def test_four_silences_three_named():
    hours = [h for start in (0, 10, 20, 30, 40) for h in range(start, start + 6)]
    flags = _flags(pd.DataFrame(_rows("M1", hours)))
    assert (flags["series_gap"].reason, flags["series_gap"].rows_affected) == (
        "4 silences longer than 3× the usual interval (M1 2026-03-01 05:00 to 2026-03-01 10:00 (5 h); "
        "M1 2026-03-01 15:00 to 2026-03-01 20:00 (5 h); M1 2026-03-02 01:00 to 2026-03-02 06:00 (5 h)): "
        "a dropout, or a source that writes only on change. Nothing is filled in.", 4)


def test_a_series_written_twice_at_every_time_has_no_silence():
    """22 rows, two per hour: half the steps are 0, and the usual interval is still 1 h."""
    rows = _rows("Z1", range(11)) + _rows("Z1", range(11), [5.0 + i for i in range(11)])
    assert "series_gap" not in _flags(pd.DataFrame(rows))


def test_a_value_unchanged_for_exactly_the_packs_stuck_hours_is_said():
    temps = [10.0, 11.0, 12.0, 13.0, 14.0] + [7.5] * 25
    flags = _flags(pd.DataFrame(_rows("P1", range(30), temps)))
    assert set(flags) == {"series_stuck"}
    assert _said(flags["series_stuck"])[:5] == (
        "account_id", "flag", "series_stuck", "1 value unchanged for hours (P1 pipe temp at 7.5 for 24 h "
        "from 2026-03-01 05:00): a stuck sensor reads as a steady process. Kept.", 1)


def test_four_stuck_values_three_named():
    rows = [r for k in ("Q1", "Q2", "Q3", "Q4") for r in _rows(k, range(25), [5.0] * 25)]
    flags = _flags(pd.DataFrame(rows))
    assert (flags["series_stuck"].reason, flags["series_stuck"].rows_affected) == (
        "4 values unchanged for hours (Q1 pipe temp at 5 for 24 h from 2026-03-01 00:00; Q2 pipe temp at 5 "
        "for 24 h from 2026-03-01 00:00; Q3 pipe temp at 5 for 24 h from 2026-03-01 00:00): a stuck sensor "
        "reads as a steady process. Kept.", 4)
