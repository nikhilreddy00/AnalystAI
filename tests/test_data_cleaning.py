"""Unit tests for the deterministic data-cleaning helpers."""
from __future__ import annotations

import pandas as pd

from agents.data_cleaning import (
    detect_dates_aggressive,
    triage_columns,
    try_numeric_conversion,
)


# ── triage_columns ────────────────────────────────────────────────────────────

def test_triage_flags_url_column():
    df = pd.DataFrame({
        "img_link": [f"https://example.com/img{i}.jpg" for i in range(20)],
        "name": [f"Product {i}" for i in range(20)],
    })
    res = triage_columns(df)
    assert "img_link" in res["url_cols"]
    assert "name" not in res["url_cols"]


def test_triage_does_not_flag_partial_url_column():
    vals = ["https://a.com"] * 3 + ["plain text"] * 7
    df = pd.DataFrame({"mixed": vals})
    res = triage_columns(df)
    assert "mixed" not in res["url_cols"]


def test_triage_flags_long_text_column():
    df = pd.DataFrame({
        "review": [f"This is a long-form review #{i}, " + "lorem ipsum dolor sit amet " * 8 for i in range(20)],
        "category": ["A", "B"] * 10,
    })
    res = triage_columns(df)
    assert "review" in res["long_text_cols"]
    assert "category" not in res["long_text_cols"]


def test_triage_flags_image_path_column():
    df = pd.DataFrame({
        "thumb": [f"/uploads/img_{i}.png" for i in range(15)],
        "label": [f"item{i}" for i in range(15)],
    })
    res = triage_columns(df)
    assert "thumb" in res["image_path_cols"]


def test_triage_keeps_first_id_skips_subsequent():
    df = pd.DataFrame({
        "product_id": [f"P{i:04d}" for i in range(20)],
        "review_id":  [f"R{i:04d}" for i in range(20)],
        "user_id":    [f"U{i:04d}" for i in range(20)],
        "name":       [f"item-{i}" for i in range(20)],
    })
    res = triage_columns(df)
    assert "product_id" not in res["secondary_id_cols"]
    assert "review_id" in res["secondary_id_cols"]
    assert "user_id" in res["secondary_id_cols"]


def test_triage_does_not_skip_id_named_when_low_uniqueness():
    df = pd.DataFrame({"category_code": ["A", "B", "C"] * 10})
    res = triage_columns(df)
    assert "category_code" not in res["secondary_id_cols"]


# ── try_numeric_conversion ────────────────────────────────────────────────────

def test_numeric_strips_currency():
    s = pd.Series(["₹399", "₹1,499", "₹250"])
    res = try_numeric_conversion(s)
    assert res is not None
    out, transformations = res
    assert list(out) == [399.0, 1499.0, 250.0]
    assert "currency symbol" in transformations
    assert "comma formatting" in transformations


def test_numeric_strips_percent_suffix():
    s = pd.Series(["48%", "12%", "100%"])
    res = try_numeric_conversion(s)
    assert res is not None
    out, transformations = res
    assert list(out) == [48.0, 12.0, 100.0]
    assert "% suffix" in transformations


def test_numeric_strips_rating_scale():
    s = pd.Series(["4.2 out of 5", "3.8 out of 5", "4.5/5"])
    res = try_numeric_conversion(s)
    assert res is not None
    out, _ = res
    assert list(out) == [4.2, 3.8, 4.5]


def test_numeric_strips_western_comma():
    s = pd.Series(["1,234", "12,345", "1,234,567"])
    res = try_numeric_conversion(s)
    assert res is not None
    out, transformations = res
    assert list(out) == [1234.0, 12345.0, 1234567.0]
    assert "comma formatting" in transformations


def test_numeric_strips_indian_lakh_comma():
    s = pd.Series(["1,23,456", "12,345", "1,39,900"])
    res = try_numeric_conversion(s)
    assert res is not None
    out, _ = res
    assert list(out) == [123456.0, 12345.0, 139900.0]


def test_numeric_strips_unit_suffix():
    s = pd.Series(["2.5 kg", "1.2 kg", "3 kg"])
    res = try_numeric_conversion(s)
    assert res is not None
    out, transformations = res
    assert list(out) == [2.5, 1.2, 3.0]
    assert "unit suffix" in transformations


def test_numeric_rejects_low_success_rate():
    s = pd.Series(["42", "alpha", "beta", "gamma"])
    res = try_numeric_conversion(s)
    assert res is None


def test_numeric_rejects_all_null():
    s = pd.Series([None, None, None], dtype=object)
    assert try_numeric_conversion(s) is None


def test_numeric_plain_strings():
    s = pd.Series(["42", "3.14", "100"])
    res = try_numeric_conversion(s)
    assert res is not None
    out, transformations = res
    assert list(out) == [42.0, 3.14, 100.0]
    assert transformations == ["parsed as numeric"]


# ── detect_dates_aggressive ───────────────────────────────────────────────────

def test_dates_iso():
    s = pd.Series(["2026-06-21", "2026-06-22", "2026-06-23"])
    out = detect_dates_aggressive(s)
    assert out is not None
    assert pd.api.types.is_datetime64_any_dtype(out)


def test_dates_dayfirst_european():
    s = pd.Series(["21/06/2026", "22/06/2026", "23/06/2026"])
    out = detect_dates_aggressive(s)
    assert out is not None
    assert out.iloc[0].day == 21
    assert out.iloc[0].month == 6


def test_dates_dayfirst_us():
    s = pd.Series(["06/21/2026", "06/22/2026", "06/23/2026"])
    out = detect_dates_aggressive(s)
    assert out is not None
    assert out.iloc[0].month == 6


def test_dates_named_month():
    s = pd.Series(["21-Jun-2026", "22-Jun-2026", "23-Jun-2026"])
    out = detect_dates_aggressive(s)
    assert out is not None
    assert pd.api.types.is_datetime64_any_dtype(out)


def test_dates_epoch_seconds():
    s = pd.Series([1718928000, 1718928060, 1718928120])
    out = detect_dates_aggressive(s)
    assert out is not None
    assert out.iloc[0].year == 2024


def test_dates_epoch_millis_not_parsed():
    s = pd.Series([1718928000000, 1718928060000, 1718928120000])
    out = detect_dates_aggressive(s)
    assert out is None


def test_dates_rejects_non_dates():
    s = pd.Series(["foo", "bar", "baz", "qux", "alpha"])
    assert detect_dates_aggressive(s) is None


def test_dates_rejects_mostly_garbage():
    s = pd.Series(["2026-06-21", "foo", "bar", "baz", "qux"])
    assert detect_dates_aggressive(s) is None


def test_dates_all_null():
    s = pd.Series([None, None, None], dtype=object)
    assert detect_dates_aggressive(s) is None
