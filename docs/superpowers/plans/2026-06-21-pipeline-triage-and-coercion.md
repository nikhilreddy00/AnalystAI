# Pipeline Triage + Robust Type Coercion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Layers 1+2 of the analyst-grade pipeline — column triage (URLs, long text, secondary IDs, image paths → skip) and robust type coercion (strip currency, %, units, comma formatting; aggressive date detection) — so real-world CSVs (Indian e-commerce ₹, Euro €, US $, Japanese ¥, percent + comma) yield correctly-typed dataframes that the rest of the pipeline can actually use.

**Architecture:** New module `agents/data_cleaning.py` with 3 pure helpers (`triage_columns`, `try_numeric_conversion`, `detect_dates_aggressive`). `cleaning_agent.py` wires them into the existing pipeline BEFORE whitespace/imputation/outlier stages. `CleaningAudit` gains a `skipped_columns` field. `analysis_agent.py` overlays `"skipped"` onto the column-role map for the schema-table badge.

**Tech Stack:** Python 3.11+, pandas, pytest. Frontend: Next.js 16, TypeScript (one single role-map entry).

**Spec:** [docs/superpowers/specs/2026-06-21-pipeline-triage-and-coercion-design.md](../specs/2026-06-21-pipeline-triage-and-coercion-design.md)

---

## Task 1: `agents/data_cleaning.py` + unit tests

The three helpers are pure functions with no LLM, no I/O, no dependencies on the session/contract — perfect for isolated unit testing. Land this first; the rest of the pipeline integration depends on these.

**Files:**
- Create: `agents/data_cleaning.py`
- Create: `tests/test_data_cleaning.py`

### Step 1: Create `agents/data_cleaning.py`

- [ ] **Write the file** with this content verbatim:

```python
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
    r"(?i)\.(jpe?g|png|gif|bmp|webp|svg|pdf|csv|xlsx?|docx?|mp[34]|mov)(?:[?#]|$)"
    r"|[\\/][^\\/]+\.\w{2,5}$"
)
_ID_NAME_RE = re.compile(r"(?i)(_id$|^id_|^id$|_key$|_hash$|_uuid$|_code$)")

# ── Layer 2 numeric patterns (order-dependent — comma stripping LAST) ─────────
_CURRENCY_RE = re.compile(r"[₹¥₩₽₫฿$€£¢]|R\$|kr\.?|zł|CHF|AED|SAR|Rs\.?|USD|EUR|GBP|INR|JPY|CNY")
_PCT_TAIL_RE = re.compile(r"\s*%\s*$")
_RATING_RE = re.compile(r"\s*(out\s+of|/)\s*[\d.]+\s*$")
_UNIT_RE = re.compile(
    r"(?i)\s*(kg|g|mg|ml|l|cm|mm|m|km|lb|oz|ft|in|sq\.?\s*ft|hrs?|mins?|secs?|pcs?|units?)\s*$"
)
_COMMA_NUM_RE = re.compile(r"\d,\d")


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

        # Non-object columns can still be ID-like by name.
        if s.dtype != object:
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
    if cleaned.dropna().str.contains(_PCT_TAIL_RE, regex=True).any():
        cleaned = cleaned.str.replace(_PCT_TAIL_RE, "", regex=True)
        transformations.append("% suffix")
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
    if int(numeric.notna().sum()) / original_non_null >= 0.8:
        return numeric, transformations or ["parsed as numeric"]
    return None


def detect_dates_aggressive(series: pd.Series) -> pd.Series | None:
    """Layer 2 date path. Returns a datetime Series if >=80% of non-null values
    parse as dates, else None.

    For numeric input: tries epoch seconds (values in [1e9, 1e13)).
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
        # Epoch seconds range: 2001-09-09 to 2286-11-20. >1e13 is likely millis;
        # we skip those in v1 to avoid wrong conversions.
        if (vals > 1e9).mean() > 0.8 and (vals < 1e13).mean() > 0.8:
            converted = pd.to_datetime(series, unit="s", errors="coerce")
            if int(converted.notna().sum()) / original_non_null > 0.8:
                return converted
        return None

    if s.dtype != object:
        return None

    sample = s.astype(str).str.strip()
    for dayfirst in (False, True):
        try:
            parsed_sample = pd.to_datetime(
                sample.head(200), format="mixed", dayfirst=dayfirst, errors="coerce"
            )
            if int(parsed_sample.notna().sum()) / max(len(parsed_sample), 1) > 0.8:
                full = pd.to_datetime(series, format="mixed", dayfirst=dayfirst, errors="coerce")
                if int(full.notna().sum()) / original_non_null > 0.8:
                    return full
        except Exception:
            continue
    return None
```

### Step 2: Create `tests/test_data_cleaning.py`

- [ ] **Write the file** with this content verbatim:

```python
"""Unit tests for the deterministic data-cleaning helpers."""
from __future__ import annotations

import pandas as pd
import pytest

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
    # Only 30% URLs — under the 50% threshold
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
    # product_id is first, kept (not in secondary_id_cols).
    # review_id + user_id are subsequent ID-like cols → skipped.
    assert "product_id" not in res["secondary_id_cols"]
    assert "review_id" in res["secondary_id_cols"]
    assert "user_id" in res["secondary_id_cols"]


def test_triage_does_not_skip_id_named_when_low_uniqueness():
    # An ID-named column with low uniqueness (e.g. a category code) shouldn't
    # be treated as an identifier.
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
    # 1/4 = 25% conversion — under the 80% threshold.
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
    # Day 21 is unambiguous (not a valid month) — verify we got June 21, not June (with day 21 in some other slot)
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
    s = pd.Series([1718928000, 1718928060, 1718928120])  # June 2024-ish
    out = detect_dates_aggressive(s)
    assert out is not None
    assert out.iloc[0].year == 2024


def test_dates_epoch_millis_not_parsed():
    # >1e13 → likely milliseconds; we skip these in v1 to avoid wrong unit.
    s = pd.Series([1718928000000, 1718928060000, 1718928120000])
    out = detect_dates_aggressive(s)
    assert out is None


def test_dates_rejects_non_dates():
    s = pd.Series(["foo", "bar", "baz", "qux", "alpha"])
    assert detect_dates_aggressive(s) is None


def test_dates_rejects_mostly_garbage():
    # 1 valid date in 5 — under the 80% threshold.
    s = pd.Series(["2026-06-21", "foo", "bar", "baz", "qux"])
    assert detect_dates_aggressive(s) is None


def test_dates_all_null():
    s = pd.Series([None, None, None], dtype=object)
    assert detect_dates_aggressive(s) is None
```

### Step 3: Run the unit tests

- [ ] **Run:** `uv run pytest tests/test_data_cleaning.py -v`
- [ ] **Expected:** all tests pass (~20 cases). If anything fails on real pandas behavior (e.g. `format="mixed"` parsing differs), STOP and report the failure with the exact assertion + actual value.

### Step 4: Ruff check

- [ ] **Run:** `uv run ruff check agents/data_cleaning.py tests/test_data_cleaning.py`
- [ ] **Expected:** `All checks passed!`

### Step 5: Verify no regressions in the prior suite

- [ ] **Run:** `uv run pytest -v 2>&1 | tail -5`
- [ ] **Expected:** `31 prior + N new = passed`.

### Step 6: Commit

```bash
git add agents/data_cleaning.py tests/test_data_cleaning.py
git commit -m "$(cat <<'EOF'
feat(data-cleaning): triage + robust type coercion helpers

Three pure-Python helpers in agents/data_cleaning.py for the cleaning
pipeline rebuild:
- triage_columns: flag URLs, long free text, image paths, and secondary
  IDs (keeping the first ID-named column, skipping subsequent ones).
- try_numeric_conversion: strip currency symbols, percent suffix, rating
  scales, unit suffixes, and comma formatting (Western + Indian lakh)
  before pd.to_numeric. 80% success gate. Returns transformations list
  for the cleaning log.
- detect_dates_aggressive: try epoch seconds (1e9..1e13 range), then
  dayfirst=False, then dayfirst=True with a 200-row sample gate.

Unit tests cover 20 cases across the three helpers. No pipeline
integration in this commit — that's Task 2.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Self-review

After committing:
1. `git diff HEAD~1 HEAD --stat` — should show 2 files: `agents/data_cleaning.py` (new), `tests/test_data_cleaning.py` (new).
2. `grep -c "^def test_" tests/test_data_cleaning.py` — should print >= 19.
3. `uv run ruff check agents/data_cleaning.py tests/test_data_cleaning.py` — clean.

---

## Task 2: Wire Layers 1+2 into `cleaning_agent.py`

This task is the integration step. It inserts triage + coercion into `run_cleaning_agent`, deletes the explicit date-coercion loop (superseded), gates the whitespace + LLM-imputation stages on the skip set, and surfaces the skip set on the `CleaningAudit`.

**Files:**
- Modify: `agents/cleaning_agent.py`
- Modify: `orchestrator/state.py`

### Step 1: Extend `CleaningAudit` with `skipped_columns`

- [ ] **Read** `orchestrator/state.py` around the `CleaningAudit` class to locate it. Add one field at the end of the class body (preserve indentation):

```python
    skipped_columns: list[str] = Field(default_factory=list)
```

### Step 2: Insert Layer 1 + Layer 2 into `run_cleaning_agent`

This is the surgical change. The current shape of `run_cleaning_agent` (lines 39-172):

```
1. df, contract, decisions, rows_before, columns_coerced, outliers_flagged initialized
2. Date coerce loop using contract.date_columns  ← DELETE this
3. Whitespace loop                                ← GATE on skip_set
4. Duplicate removal
5. Imputation (LLM-driven)                        ← GATE cols_with_nulls on skip_set
6. Outlier detection
7. Build audit + persist
```

The new shape inserts Layers 1+2 between step 1 and the (deleted) step 2.

- [ ] **Edit `agents/cleaning_agent.py`** — replace the block at lines 53-65 (`decisions` init through end of the date-coerce loop) with the version below. Note: line numbers may have drifted; identify the block by finding the `decisions: list[CleaningDecision] = []` initialization and the immediately-following date-coercion loop that ends just before the comment `# 2. Whitespace normalization on object columns`.

```python
    decisions: list[CleaningDecision] = []
    rows_before = len(df)
    columns_coerced: list[str] = []
    outliers_flagged: list[str] = []

    # === Layer 1 (Gap pipeline triage): identify obviously-noise columns BEFORE
    #     any type coercion. Skipped columns stay in df (so the schema table sees
    #     them) but are excluded from whitespace normalization, LLM-driven
    #     imputation, and downstream chart/KPI generation. ===
    from agents.data_cleaning import (
        detect_dates_aggressive,
        triage_columns,
        try_numeric_conversion,
    )

    triage = triage_columns(df)
    skip_set: set[str] = set()
    _skip_reason = {
        "url": "URL column — not analytically useful",
        "long_text": "Long free-text column — not categorical",
        "image_path": "Image/file path column — not analytically useful",
        "secondary_id": "Secondary identifier — first ID kept; this one skipped",
    }
    for kind, cols in triage.items():
        strategy = kind.removesuffix("_cols")  # url / long_text / image_path / secondary_id
        for col in cols:
            skip_set.add(col)
            rows_affected = int(df[col].notna().sum())
            decisions.append(CleaningDecision(
                column=col,
                operation="skip",
                strategy=strategy,
                reason=_skip_reason[strategy],
                rows_affected=rows_affected,
                before_null_pct=0.0,
                after_null_pct=0.0,
            ))

    # === Layer 2 (Gap pipeline coercion): for every non-skipped column, try
    #     numeric coercion first (strips currency/%, units, comma formatting),
    #     then fall back to aggressive date detection. Subsumes the old explicit
    #     date-coercion loop that used contract.date_columns. ===
    for col in list(df.columns):
        if col in skip_set:
            continue

        if df[col].dtype == object:
            num_result = try_numeric_conversion(df[col])
            if num_result is not None:
                new_series, transformations = num_result
                df[col] = new_series
                columns_coerced.append(col)
                reason = (
                    f"Stripped {', '.join(transformations)}"
                    if transformations and transformations != ["parsed as numeric"]
                    else "Parsed as numeric"
                )
                decisions.append(CleaningDecision(
                    column=col,
                    operation="type_coerce",
                    strategy="to_numeric",
                    reason=reason,
                    rows_affected=int(new_series.notna().sum()),
                    before_null_pct=0.0,
                    after_null_pct=0.0,
                ))
                continue  # numeric won; skip date attempt

        dt = detect_dates_aggressive(df[col])
        if dt is not None:
            df[col] = dt
            if col not in columns_coerced:
                columns_coerced.append(col)
            decisions.append(CleaningDecision(
                column=col,
                operation="type_coerce",
                strategy="to_datetime",
                reason="Parsed as datetime",
                rows_affected=int(dt.notna().sum()),
                before_null_pct=0.0,
                after_null_pct=0.0,
            ))
```

### Step 3: Gate the whitespace-normalization loop on `skip_set`

- [ ] **Edit** the whitespace loop (currently at lines 68-79). Add `if col in skip_set: continue` as the first statement inside the loop body:

```python
    # 2. Whitespace normalization on object columns
    for col in df.select_dtypes(include="object").columns:
        if col in skip_set:
            continue
        # BUG-12: guard against mixed-type object columns (e.g., strings + ints + None)
        try:
            ...
```

### Step 4: Gate the LLM imputation prompt on `skip_set`

- [ ] **Edit** the imputation block (currently around line 89). Change:

```python
    cols_with_nulls = [col for col in df.columns if null_counts[col] > 0]
```

to:

```python
    cols_with_nulls = [col for col in df.columns if null_counts[col] > 0 and col not in skip_set]
```

### Step 5: Surface `skip_set` on the audit

- [ ] **Edit** the audit construction (currently lines 156-166). Add `skipped_columns=list(skip_set)` to the `CleaningAudit(...)` call:

```python
    audit = CleaningAudit(
        decisions=decisions,
        rows_before=rows_before,
        rows_after=rows_after,
        columns_coerced=columns_coerced,
        duplicates_removed=duplicates_removed,
        outliers_flagged=outliers_flagged,
        high_null_columns=high_null_columns,
        summary=summary,
        anomaly_report=anomaly_report,
        skipped_columns=list(skip_set),
    )
```

### Step 6: Smoke-import the modified module

- [ ] **Run:** `uv run python -c "from agents.cleaning_agent import run_cleaning_agent; print('import OK')"`
- [ ] **Expected:** prints `import OK` with no errors.

### Step 7: Verify no test regressions

- [ ] **Run:** `uv run pytest -v 2>&1 | tail -5`
- [ ] **Expected:** all tests still pass. (No existing tests cover `run_cleaning_agent` end-to-end — it's async + LLM-dependent — so we're checking that the import-time changes don't break the rest.)

### Step 8: Ruff check

- [ ] **Run:** `uv run ruff check agents/cleaning_agent.py orchestrator/state.py`
- [ ] **Expected:** clean.

### Step 9: Commit

```bash
git add agents/cleaning_agent.py orchestrator/state.py
git commit -m "$(cat <<'EOF'
feat(cleaning): wire triage + robust type coercion into cleaning pipeline

Layer 1 (triage_columns) runs first: flags URL columns, long free text,
image paths, and secondary IDs into a skip_set. Each skip emits a
CleaningDecision (operation='skip').

Layer 2 (try_numeric_conversion → detect_dates_aggressive) runs per
non-skipped column: strips currency, %, units, commas; tries numeric
first then date. Each coercion emits a CleaningDecision
(operation='type_coerce').

The explicit contract.date_columns loop is deleted — detect_dates_aggressive
subsumes it and catches dates the ingestion agent missed.

Whitespace normalization and LLM-driven imputation now skip the skip_set.
The set is surfaced on CleaningAudit.skipped_columns so the schema table
can render a 'skipped' badge.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Self-review

1. `git diff HEAD~1 HEAD --stat` — 2 files: `agents/cleaning_agent.py`, `orchestrator/state.py`.
2. `grep -n "skip_set" agents/cleaning_agent.py` — should appear in the whitespace loop, the imputation block, and the audit construction (3+ places).
3. `grep "skipped_columns" orchestrator/state.py` — should show the new field on `CleaningAudit`.
4. `grep "date_columns" agents/cleaning_agent.py` — the only remaining reference should be inside the `forward_fill` strategy (line 116-118), NOT the deleted loop.

---

## Task 3: Analysis-agent role overlay + frontend badge

**Files:**
- Modify: `agents/analysis_agent.py`
- Modify: `frontend/src/components/briefing/SchemaQualityTable.tsx`

### Step 1: Locate the column_roles_summary build in `analysis_agent.py`

- [ ] **Run:** `grep -n "column_roles_summary" agents/analysis_agent.py`
- [ ] Identify the line(s) where `column_roles_summary` is populated on the `AnalysisFindings` instance. There should be a build step that derives the role string per column.

### Step 2: Overlay `"skipped"` after the existing population

- [ ] **Edit `agents/analysis_agent.py`** — after `column_roles_summary` is fully populated (and BEFORE the `AnalysisFindings(...)` constructor is called, or BEFORE it's assigned to the `findings.column_roles_summary` attribute), add:

```python
        # Overlay the cleaning-stage skip set: any column the triage layer
        # excluded gets a 'skipped' role so the schema table can render the
        # gray badge regardless of what classify_columns inferred.
        if session.cleaning_audit and session.cleaning_audit.skipped_columns:
            for col in session.cleaning_audit.skipped_columns:
                if col in column_roles_summary:
                    column_roles_summary[col] = "skipped"
```

The exact insertion point depends on the file's current shape — adapt the indentation to match the surrounding code. The block above assumes `column_roles_summary` is a local `dict[str, str]` being built and `session` is in scope. If the file uses a different variable name for the session (e.g., `state`), adapt accordingly.

### Step 3: Smoke-import

- [ ] **Run:** `uv run python -c "from agents.analysis_agent import run_analysis_agent; print('import OK')"` (or whatever the public entry function is named — `grep -n "^async def \|^def " agents/analysis_agent.py | head -3` to find it).
- [ ] **Expected:** prints `import OK`.

### Step 4: Frontend badge entry

- [ ] **Run:** `grep -n "ROLE_BADGE\|ROLE_LABEL\|role_badge\|role.*badge" frontend/src/components/briefing/SchemaQualityTable.tsx`
- [ ] Locate the role → badge mapping. It's likely a `Record<string, {label, cls}>` or similar at the top of the file.

- [ ] **Edit `frontend/src/components/briefing/SchemaQualityTable.tsx`** — add one entry to the role-badge map for `"skipped"`:

```tsx
skipped: { label: "skipped", cls: "bg-zinc-100 text-zinc-500 border-zinc-200" },
```

The exact property names depend on the map's existing shape. Match it. If the file uses inline rendering instead of a lookup map, add a single conditional render: when `role === "skipped"`, render `<span className="bg-zinc-100 text-zinc-500 border-zinc-200 ...">skipped</span>`.

### Step 5: Frontend build

- [ ] **Run:** `cd frontend && npm run build`
- [ ] **Expected:** build succeeds, no TypeScript errors.

### Step 6: Backend ruff

- [ ] **Run:** `uv run ruff check agents/analysis_agent.py`
- [ ] **Expected:** clean.

### Step 7: Backend test suite

- [ ] **Run:** `uv run pytest -v 2>&1 | tail -5`
- [ ] **Expected:** all tests pass.

### Step 8: Commit

```bash
git add agents/analysis_agent.py frontend/src/components/briefing/SchemaQualityTable.tsx
git commit -m "$(cat <<'EOF'
feat(briefing): surface skipped columns as 'skipped' badge in schema table

Analysis agent overlays "skipped" onto column_roles_summary for every
column in CleaningAudit.skipped_columns. SchemaQualityTable renders a
neutral gray badge for that role so users see exactly which columns
were excluded and why.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### Self-review

1. `git diff HEAD~1 HEAD --stat` — 2 files touched.
2. `grep "skipped_columns" agents/analysis_agent.py` — should show the new overlay.
3. `grep "skipped" frontend/src/components/briefing/SchemaQualityTable.tsx` — should show the new badge entry.

---

## Task 4: Cross-cutting verification + manual smoke checklist

This is a no-code verification task that runs after Tasks 1-3 are committed. It re-runs the full test suite + lint on all touched files, confirms the import graph isn't broken, and produces a manual-verification runbook for the user.

**Files:** none modified.

### Step 1: Full backend test suite

- [ ] **Run:** `uv run pytest -v 2>&1 | tail -10`
- [ ] **Expected:** `31 prior + N new = passed` with no failures or errors.

### Step 2: Backend ruff over all touched files

- [ ] **Run:** `uv run ruff check agents/data_cleaning.py agents/cleaning_agent.py orchestrator/state.py agents/analysis_agent.py tests/test_data_cleaning.py`
- [ ] **Expected:** `All checks passed!`

### Step 3: Frontend build

- [ ] **Run:** `cd frontend && npm run build`
- [ ] **Expected:** typecheck + build clean. (No frontend functional regression — only a single role-badge map entry was added.)

### Step 4: Verify the import graph is healthy

- [ ] **Run:** `uv run python -c "
from agents.cleaning_agent import run_cleaning_agent
from agents.data_cleaning import triage_columns, try_numeric_conversion, detect_dates_aggressive
from agents.analysis_agent import *  # noqa: F401, F403
from orchestrator.state import CleaningAudit, AnalysisFindings
print('import graph OK')
"`
- [ ] **Expected:** prints `import graph OK`.

### Step 5: Generate the manual-smoke runbook

Print a checklist for the user to walk through on real datasets after this spec ships. The user should run this matrix on at least 3 datasets from different domains (per the spec's "After this fix, re-test on at least 3 datasets" instruction).

The runbook (paste this into the final task report):

```
Manual smoke matrix (run after Spec 1 ships):

1. Indian e-commerce CSV (₹ prices, % discounts, comma-formatted counts):
   - Run an overview in /chat against the CSV.
   - Open /dashboard.
   - Verify in the schema table:
     * discounted_price, actual_price → numeric (₹ + commas stripped)
     * discount_percentage → numeric (% stripped)
     * rating → numeric (parsed directly)
     * rating_count → numeric (commas stripped)
     * img_link, product_link → "skipped" badge (URL columns)
     * about_product, review_content → "skipped" badge (long text)
     * review_id, user_id → "skipped" badge (secondary IDs)
   - Verify in the cleaning log:
     * "Stripped currency symbol, comma formatting" lines for prices
     * "Skipped 'img_link' (URL column — not analytically useful)" lines

2. European sales CSV (€ prices, dd/mm/yyyy dates):
   - Verify price → float64 (€ stripped); order_date → datetime via dayfirst.

3. Japanese retail CSV (¥, comma numbers):
   - Verify price → float64 (¥ + commas stripped).

If any dataset shows a column that SHOULD have been coerced but stayed as
object, check the cleaning log for a CleaningDecision indicating why
(80% gate, regex miss, etc.) and report back.
```

### Step 6: No commit

There are no code changes in this task. Report DONE with the verification output and the runbook.

---

## Self-Review

**Spec coverage:**
- ✅ Layer 1 triage with URL, long text, image path, secondary ID detection — Task 1
- ✅ Layer 2 numeric coercion with currency/%, rating/unit/comma stripping — Task 1
- ✅ Layer 2 aggressive date detection (epoch + dayfirst both ways) — Task 1
- ✅ Wired into cleaning pipeline BEFORE whitespace/imputation/outlier — Task 2
- ✅ CleaningDecision emitted for every skip + coerce — Task 2
- ✅ skip_set excluded from LLM imputation prompt — Task 2 step 4
- ✅ CleaningAudit.skipped_columns surfaces the set — Task 2 step 5
- ✅ Analysis agent overlays "skipped" onto column_roles_summary — Task 3 step 2
- ✅ SchemaQualityTable renders a neutral badge for skipped role — Task 3 step 4
- ✅ Unit tests cover ~20 cases — Task 1 step 2

**Placeholder scan:** No "TBD", "TODO", or vague steps. All code blocks complete and copy-pasteable.

**Type consistency:** `triage_columns -> dict[str, list[str]]`, `try_numeric_conversion -> tuple[pd.Series, list[str]] | None`, `detect_dates_aggressive -> pd.Series | None` — used consistently across module + caller + tests.

**Cross-task dependencies:** Task 2 depends on Task 1 (imports from agents.data_cleaning). Task 3 depends on Task 2 (consumes CleaningAudit.skipped_columns). Task 4 depends on all three (final integration check).
