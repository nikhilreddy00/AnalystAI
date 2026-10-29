"""
Deterministic data-cleaning helpers used by the cleaning agent.

Three pure functions — no LLM, no I/O. All rules are dataset-agnostic so
the same code handles Indian e-commerce (₹), Euro finance (€), US healthcare
($), Japanese retail (¥), survey percentages, IoT with units, etc.

Layer 1 — triage_columns: classify obviously-noise columns into a skip set
  before any type coercion runs. URLs, image paths, long free text,
  secondary IDs.

Layer 2 — try_numeric_conversion: strip currency symbols, percent signs,
  rating-scale suffixes, unit suffixes, and comma formatting before
  attempting numeric conversion. 80%-success gate.

Layer 2 — detect_dates_aggressive: try epoch-seconds, dayfirst=False,
  then dayfirst=True with a 200-row sample gate for performance.
"""
from __future__ import annotations

import re

import pandas as pd

# ── Layer 1 patterns ──────────────────────────────────────────────────────────
_URL_RE = re.compile(r"(?i)https?://|www\.")
_IMAGE_EXT_RE = re.compile(
    r"(?i)\.(?:jpe?g|png|gif|bmp|webp|svg|pdf|csv|xlsx?|docx?|mp[34]|mov)(?:[?#]|$)"
    r"|[\\/][^\\/]+\.\w{2,5}$"
)
_ID_NAME_RE = re.compile(r"(?i)(_id$|^id_|^id$|_key$|_hash$|_uuid$|_code$)")

# ── Layer 2 numeric patterns (order-dependent — comma stripping LAST) ─────────
_CURRENCY_RE = re.compile(r"[₹¥₩₽₫฿$€£¢]|R\$|kr\.?|zł|CHF|AED|SAR|Rs\.?|USD|EUR|GBP|INR|JPY|CNY")
_PCT_TAIL_RE = re.compile(r"\s*%\s*$")
_RATING_RE = re.compile(r"\s*(?:out\s+of|/)\s*[\d.]+\s*$")
_UNIT_RE = re.compile(
    r"(?i)\s*(?:kg|g|mg|ml|l|cm|mm|m|km|lb|oz|ft|in|sq\.?\s*ft|hrs?|mins?|secs?|pcs?|units?)\s*$"
)
_COMMA_NUM_RE = re.compile(r"\d,\d")
_HAS_YEAR_RE = re.compile(r"\d{4}|\b\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2}\b")


def triage_columns(df: pd.DataFrame) -> dict[str, list[str]]:
    """Layer 1. Bucket obviously-noise columns into kinds for the caller to
    aggregate into a skip set.

    Returns dict with keys:
      url_cols          — >50% values match https?:// or contain www.
      long_text_cols    — median str length > 100 AND unique ratio > 0.8
      image_path_cols   — >50% values match image/file extensions
      secondary_id_cols — ID-name pattern + uniqueness > 0.9, NOT the first such
    """
    url_cols: list[str] = []
    long_text_cols: list[str] = []
    image_path_cols: list[str] = []
    id_like_in_order: list[str] = []

    for col in df.columns:
        s = df[col].dropna()
        if s.empty:
            continue

        # Non-string columns can still be ID-like by name.
        # pandas 2.x+ may report string columns as StringDtype rather than
        # 'object', so use is_string_dtype which covers both.
        if not pd.api.types.is_string_dtype(s):
            if _ID_NAME_RE.search(col) and (s.nunique() / max(len(s), 1)) > 0.9:
                id_like_in_order.append(col)
            continue

        sample = s.astype(str)
        n = len(sample)
        url_hits = int(sample.str.contains(_URL_RE, regex=True).sum())
        if url_hits / n > 0.5:
            url_cols.append(col)
            continue

        image_hits = int(sample.str.contains(_IMAGE_EXT_RE, regex=True).sum())
        if image_hits / n > 0.5:
            image_path_cols.append(col)
            continue

        median_len = float(sample.str.len().median())
        unique_ratio = s.nunique() / n
        if median_len > 100 and unique_ratio > 0.8:
            long_text_cols.append(col)
            continue

        if _ID_NAME_RE.search(col) and unique_ratio > 0.9:
            id_like_in_order.append(col)

    secondary_id_cols = id_like_in_order[1:] if len(id_like_in_order) > 1 else []

    return {
        "url_cols": url_cols,
        "long_text_cols": long_text_cols,
        "image_path_cols": image_path_cols,
        "secondary_id_cols": secondary_id_cols,
    }


def try_numeric_conversion(series: pd.Series) -> tuple[pd.Series, list[str]] | None:
    """Layer 2. Strip common formatting noise from a string series and try
    numeric conversion. Returns (converted_series, transformations_applied)
    if >=80% of non-null values convert, else None.

    Transformations are human-readable labels used to build the cleaning log
    entry (e.g. ['currency symbol', 'comma formatting'])."""
    original_non_null = int(series.notna().sum())
    if original_non_null == 0:
        return None

    cleaned = series.astype(str).str.strip()
    cleaned = cleaned.replace({"nan": pd.NA, "None": pd.NA, "": pd.NA})

    transformations: list[str] = []
    if cleaned.dropna().str.contains(_CURRENCY_RE, regex=True).any():
        cleaned = cleaned.str.replace(_CURRENCY_RE, "", regex=True)
        transformations.append("currency symbol")
    pct = cleaned.str.contains(_PCT_TAIL_RE, regex=True).fillna(False).astype(bool)
    if pct.any():
        cleaned = cleaned.str.replace(_PCT_TAIL_RE, "", regex=True)
        transformations.append("% suffix (12.5% → 0.125)")
    if cleaned.dropna().str.contains(_RATING_RE, regex=True).any():
        cleaned = cleaned.str.replace(_RATING_RE, "", regex=True)
        transformations.append("rating scale suffix")
    if cleaned.dropna().str.contains(_UNIT_RE, regex=True).any():
        cleaned = cleaned.str.replace(_UNIT_RE, "", regex=True)
        transformations.append("unit suffix")
    if cleaned.dropna().str.contains(_COMMA_NUM_RE, regex=True).any():
        cleaned = cleaned.str.replace(",", "", regex=False)
        transformations.append("comma formatting")

    cleaned = cleaned.str.strip()
    numeric = pd.to_numeric(cleaned, errors="coerce")
    # "12.5%" is the rate 0.125: kept as 12.5 it reads as an amount and gets summed.
    numeric = numeric.where(~pct, numeric / 100)
    if int(numeric.notna().sum()) / original_non_null >= 0.8:
        return numeric, transformations or ["parsed as numeric"]
    return None


def detect_dates_aggressive(series: pd.Series) -> pd.Series | None:
    """Layer 2 date path. Returns a datetime Series if >=80% of non-null values
    parse as dates, else None.

    For numeric input: tries epoch seconds (values in [1e9, 1e10)).
    For object input: tries dayfirst=False then dayfirst=True with a 200-row
    sample gate to avoid expensive full-column parse on non-date columns."""
    original_non_null = int(series.notna().sum())
    if original_non_null == 0:
        return None

    s = series.dropna()

    if pd.api.types.is_numeric_dtype(s):
        vals = pd.to_numeric(s, errors="coerce").dropna()
        if len(vals) == 0:
            return None
        # Epoch seconds range: 2001-09-09 to 2286-11-20. >=1e10 is likely millis;
        # we skip those in v1 to avoid wrong conversions.
        if (vals > 1e9).mean() >= 0.8 and (vals < 1e10).mean() >= 0.8:
            converted = pd.to_datetime(series, unit="s", errors="coerce")
            if int(converted.notna().sum()) / original_non_null >= 0.8:
                return converted
        return None

    if not pd.api.types.is_string_dtype(s):
        return None

    sample = s.astype(str).str.strip()
    # A date needs a year: "Jan", "Monday" or "12:30" would parse with an invented year or today.
    if sample.head(200).str.contains(_HAS_YEAR_RE, regex=True).mean() < 0.8:
        return None
    for dayfirst in (False, True):
        try:
            parsed_sample = pd.to_datetime(
                sample.head(200), format="mixed", dayfirst=dayfirst, errors="coerce"
            )
            if int(parsed_sample.notna().sum()) / max(len(parsed_sample), 1) >= 0.8:
                full = pd.to_datetime(series, format="mixed", dayfirst=dayfirst, errors="coerce")
                if int(full.notna().sum()) / original_non_null >= 0.8:
                    return full
        except Exception:
            continue
    return None
