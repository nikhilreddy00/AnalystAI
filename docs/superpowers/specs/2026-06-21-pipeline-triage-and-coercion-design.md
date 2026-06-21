# Pipeline Triage + Robust Type Coercion — Design

**Date:** 2026-06-21
**Gap:** Data pipeline rebuild, Spec 1 of 2 (Layers 1+2 of the user's "Make the Pipeline Think Like a Data Analyst" spec)
**Status:** Spec approved

---

## Problem

The current cleaning pipeline assumes data arrives in clean, typed formats:
- `agents/cleaning_agent.py` only coerces `contract.date_columns` to datetime. Object columns stay as strings.
- `classify_columns` (in `agents/visualization_agent.py`) trusts pandas dtype, so any column containing currency symbols (`₹399`), percentage signs (`64%`), comma-formatted numbers (`1,234,567`), or unit suffixes (`2.5 kg`) is misbucketed as a high-cardinality categorical or as `skip_cols`.
- There is no column triage. URLs, image links, long free text, and secondary IDs all flow through cleaning, classification, and chart generation. The visualization agent tries to make bar charts of `img_link`. KPIs default to row-count because no numeric columns are detected.

Real-world datasets (Amazon products with `₹`, European finance with `€`, US healthcare with `$`, Japanese retail with `¥`, survey data with `%`, IoT data with units) break the pipeline downstream — the dashboard ends up empty.

## Goals

- Two new deterministic, pure-Python layers run BEFORE the LLM-driven imputation:
  - **Layer 1 (Triage):** classify obviously-noise columns (URLs, long text, secondary IDs, image paths) into a skip set.
  - **Layer 2 (Coercion):** for every non-skipped object column, aggressively strip common formatting noise and try numeric conversion; if that fails, try date conversion.
- All rules are dataset-agnostic — no Amazon-specific or Euro-specific code paths.
- The LLM imputation step still runs, but on a dataframe with correct dtypes and with skipped columns excluded from its prompt.
- Skipped columns surface to the user via the schema-table `skipped` badge so the user knows what was excluded and why.
- Each triage skip and each coercion produces a `CleaningDecision` so the cleaning audit reads: "Skipped 'img_link' (URL column)", "Converted 'discounted_price' from string to numeric (stripped currency symbol, comma formatting)".

## Non-goals

- Layer 3 (smart classification + importance ranking + no-target chart fallbacks) — deferred to Spec 2.
- Modifying `DataContract` (set during ingestion). Downstream agents already re-run `classify_columns` on the cleaned DF.
- Adding new Pydantic models. We reuse `CleaningDecision` with new `operation` values.
- Boolean-like string detection ("Yes"/"No" → 0/1). Per scope decision, these stay as categorical; binary/target detection is Layer 3.
- Connector-side changes. This is purely a cleaning-stage concern.
- Frontend changes beyond a single role-badge entry.

## Design

### 1. New pipeline order in `cleaning_agent.py:run_cleaning_agent`

```
1. Triage              ← NEW Layer 1
2. Type coerce (num)   ← NEW Layer 2, per object column
3. Date coerce         ← NEW Layer 2, per remaining object column
4. Whitespace normalization (existing) — skip_set guard
5. Duplicate removal (existing)
6. LLM imputation (existing) — skip_set excluded from prompt
7. Outlier detection (existing) — no change
```

The existing date-coercion loop at lines 58-65 (using `contract.date_columns`) is **deleted**; `detect_dates_aggressive` runs over object columns universally and subsumes it.

### 2. New module: `agents/data_cleaning.py`

Three pure functions, ~180 LoC total. No LLM. No I/O. Easy to unit-test.

#### `triage_columns(df) -> dict[str, list[str]]`

Returns:
```python
{
  "url_cols":         [...],  # >50% of values match https?:// or contain www.
  "long_text_cols":   [...],  # median str length > 100 AND unique ratio > 0.8
  "image_path_cols":  [...],  # >50% match common image/file extensions
  "secondary_id_cols":[...],  # ID-name + uniqueness > 0.9, NOT the first such column
}
```

Regexes:
```python
_URL_RE       = re.compile(r"(?i)https?://|www\.")
_IMAGE_EXT_RE = re.compile(r"(?i)\.(jpe?g|png|gif|bmp|webp|svg|pdf|csv|xlsx?|docx?|mp[34]|mov)(?:[?#]|$)|[\\/][^\\/]+\.\w{2,5}$")
_ID_NAME_RE   = re.compile(r"(?i)(_id$|^id_|^id$|_key$|_hash$|_uuid$|_code$)")
```

Pseudocode:
```python
def triage_columns(df):
    url_cols, long_text_cols, image_path_cols = [], [], []
    id_like_in_order = []

    for col in df.columns:
        s = df[col].dropna()
        if s.empty: continue

        if s.dtype != object:
            # numeric/datetime can still be ID-like by name
            if _ID_NAME_RE.search(col):
                id_like_in_order.append(col)
            continue

        sample = s.astype(str)
        n = len(sample)
        if sample.str.contains(_URL_RE, regex=True).sum() / n > 0.5:
            url_cols.append(col); continue
        if sample.str.contains(_IMAGE_EXT_RE, regex=True).sum() / n > 0.5:
            image_path_cols.append(col); continue

        median_len = sample.str.len().median()
        unique_ratio = s.nunique() / n
        if median_len > 100 and unique_ratio > 0.8:
            long_text_cols.append(col); continue

        if _ID_NAME_RE.search(col) and unique_ratio > 0.9:
            id_like_in_order.append(col)

    secondary_id_cols = id_like_in_order[1:] if len(id_like_in_order) > 1 else []

    return {
        "url_cols": url_cols,
        "long_text_cols": long_text_cols,
        "image_path_cols": image_path_cols,
        "secondary_id_cols": secondary_id_cols,
    }
```

#### `try_numeric_conversion(series) -> tuple[pd.Series, list[str]] | None`

6-stage strip pipeline + 80% success gate. Returns the converted numeric series + a list of transformation labels for the cleaning log.

Regexes (order-dependent — comma stripping LAST because units/symbols sometimes precede commas):
```python
_CURRENCY_RE  = re.compile(r"[₹¥₩₽₫฿$€£¢]|R\$|kr\.?|zł|CHF|AED|SAR|Rs\.?|USD|EUR|GBP|INR|JPY|CNY")
_PCT_TAIL_RE  = re.compile(r"\s*%\s*$")
_RATING_RE    = re.compile(r"\s*(out\s+of|/)\s*[\d.]+\s*$")
_UNIT_RE      = re.compile(r"(?i)\s*(kg|g|mg|ml|l|cm|mm|m|km|lb|oz|ft|in|sq\.?\s*ft|hrs?|mins?|secs?|pcs?|units?)\s*$")
_COMMA_NUM_RE = re.compile(r"\d,\d")
```

Pseudocode:
```python
def try_numeric_conversion(series):
    original_non_null = series.notna().sum()
    if original_non_null == 0:
        return None

    cleaned = series.astype(str).str.strip()
    cleaned = cleaned.replace({"nan": pd.NA, "None": pd.NA, "": pd.NA})

    transformations = []
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
    if numeric.notna().sum() / original_non_null >= 0.8:
        return numeric, transformations or ["parsed as numeric"]
    return None
```

Why `.dropna()` before each `.str.contains` — `series.astype(str)` turns NaN into the string `"nan"` which we map back to `pd.NA`; dropping NA before contains avoids both stale `"nan"` matches and `NaN`-bearing boolean ambiguity.

#### `detect_dates_aggressive(series) -> pd.Series | None`

Tries epoch seconds for numeric series; tries `dayfirst=False` then `dayfirst=True` for object series, sample-gated for speed.

```python
def detect_dates_aggressive(series):
    original_non_null = series.notna().sum()
    if original_non_null == 0: return None

    s = series.dropna()

    if pd.api.types.is_numeric_dtype(s):
        vals = pd.to_numeric(s, errors="coerce").dropna()
        if len(vals) > 0 and (vals > 1e9).mean() > 0.8 and (vals < 1e13).mean() > 0.8:
            converted = pd.to_datetime(series, unit="s", errors="coerce")
            if converted.notna().sum() / original_non_null > 0.8:
                return converted
        return None

    if s.dtype != object: return None

    sample = s.astype(str).str.strip()
    for dayfirst in (False, True):
        try:
            parsed = pd.to_datetime(sample.head(200), format="mixed", dayfirst=dayfirst, errors="coerce")
            if parsed.notna().sum() / max(len(parsed), 1) > 0.8:
                full = pd.to_datetime(series, format="mixed", dayfirst=dayfirst, errors="coerce")
                if full.notna().sum() / original_non_null > 0.8:
                    return full
        except Exception:
            continue
    return None
```

Why sample-gate the full parse — `pd.to_datetime(format="mixed")` is slow on long columns; sampling 200 rows quickly disqualifies non-date columns.

Why `dayfirst=False` first — US ISO/ISO-ish formats (`2026-06-21`, `06/21/2026`) are more common in tech datasets.

Why epoch range `[1e9, 1e13)` — `> 1e13` is likely milliseconds (would need `unit="ms"`); we skip those in v1 to avoid wrong conversions.

### 3. `cleaning_agent.py` integration

After the existing local-vars block (line 56), insert:
1. Layer 1 call → populate `skip_set`, append `CleaningDecision(operation="skip", ...)` per skipped column with one of strategies `url`/`long_text`/`image_path`/`secondary_id`.
2. Layer 2 numeric pass: for every non-skipped object column, call `try_numeric_conversion`. On success, replace `df[col]`, append `CleaningDecision(operation="type_coerce", strategy="to_numeric", reason="Stripped {transformations}" or "Parsed as numeric")`, add to `columns_coerced`.
3. Layer 2 date pass: for every remaining non-skipped object column (and for `contract.date_columns` to backfill the deleted explicit loop), call `detect_dates_aggressive`. On success, replace `df[col]`, append `CleaningDecision(operation="type_coerce", strategy="to_datetime", reason="Parsed as datetime")`, add to `columns_coerced`.

Existing date-coercion loop at lines 58-65 is **deleted** — subsumed by step 3 above.

Existing whitespace-normalization loop at lines 67-79 — add `if col in skip_set: continue` at top of loop body.

Existing imputation block at lines 86-137 — change `cols_with_nulls = [col for col in df.columns if null_counts[col] > 0]` to `cols_with_nulls = [col for col in df.columns if null_counts[col] > 0 and col not in skip_set]`. Skipped columns remain in `df` (so the schema table sees them) but get no LLM prompt + no imputation.

`classify_columns(df)` call at line 144 — already runs on cleaned `df`. Add an overlay pass to mark skipped columns:
```python
for col in skip_set:
    column_roles.setdefault("skip_cols", []).append(col)
```

### 4. Schema additions

**`orchestrator/state.py:CleaningAudit`** — add one field:
```python
skipped_columns: list[str] = Field(default_factory=list)
```

Populated at the end of `run_cleaning_agent`: `audit = CleaningAudit(..., skipped_columns=list(skip_set))`.

### 5. Analysis agent overlay

`agents/analysis_agent.py` builds `column_roles_summary: dict[str, str]` (lives on `AnalysisFindings`). After existing population:
```python
for col in session.cleaning_audit.skipped_columns:
    if col in column_roles_summary:
        column_roles_summary[col] = "skipped"
```

This is what the frontend schema-table reads to render the badge.

### 6. Frontend badge

`frontend/src/components/briefing/SchemaQualityTable.tsx` — single role-map entry:
```tsx
const ROLE_BADGE: Record<string, { label: string; cls: string }> = {
  // existing entries: id, binary, continuous, ordinal, low_card_cat, high_card_cat, datetime, ...
  skipped: { label: "skipped", cls: "bg-zinc-100 text-zinc-500 border-zinc-200" },
};
```

Schema table still shows the row (column name + dtype + missing %) so the user knows the column exists. The grayed-out badge communicates intentional exclusion.

## Files touched

| File | Change | LoC |
|---|---|---|
| `agents/data_cleaning.py` | NEW — 3 pure helpers | ~180 |
| `agents/cleaning_agent.py` | Insert Layer 1+2; gate whitespace + imputation on skip_set; populate audit.skipped_columns; delete the explicit date loop | +60 / −10 |
| `orchestrator/state.py` | `CleaningAudit.skipped_columns` | +1 |
| `agents/analysis_agent.py` | Overlay `"skipped"` role onto column_roles_summary | +3 |
| `frontend/src/components/briefing/SchemaQualityTable.tsx` | One role-map entry | +1-2 |
| `tests/test_data_cleaning.py` | NEW — unit tests | ~150 |

No connector, graph, routing, SSE, or PPTX changes.

## Verification

**Automated:**
1. `uv run pytest tests/test_data_cleaning.py -v` — new unit tests pass (~12-15 cases across the 3 helpers).
2. `uv run pytest -v` — 31 prior + N new = no regressions.
3. `uv run ruff check agents/data_cleaning.py agents/cleaning_agent.py orchestrator/state.py agents/analysis_agent.py` — clean.
4. `cd frontend && npm run build` — clean.

**Unit-test matrix (`tests/test_data_cleaning.py`):**

| Function | Cases |
|---|---|
| `triage_columns` | URL >50% flagged; URL <50% kept; long text (median>100, unique>0.8) flagged; short categorical kept; image-extension column flagged; first ID-named kept, subsequent ID-named skipped; ID-named numeric NOT flagged when uniqueness <0.9 |
| `try_numeric_conversion` | `₹399` → 399.0; `48%` → 48.0; `4.2 out of 5` → 4.2; Western comma `1,234,567` → 1234567; Indian lakh `1,23,456` → 123456; unit suffix `2.5 kg` → 2.5; mixed <80% parse → None; all-null → None; plain numeric strings `"42"` → 42 with transformations=`["parsed as numeric"]` |
| `detect_dates_aggressive` | `21-Jun-2026` parsed; `21/06/2026` parsed via dayfirst=True; `06/21/2026` parsed via dayfirst=False; ISO `2026-06-21` parsed; epoch seconds (`1718928000`) parsed; epoch millis (>1e13) NOT parsed; non-date strings → None; <80% parse → None |

**Manual smoke (user-side, after ship):**
- Amazon products CSV (₹, %, comma counts) → all 5 numeric columns coerced; 6 noise columns skipped with badges.
- European sales CSV (€, dd/mm/yyyy) → `price` coerced; `order_date` datetime via dayfirst=True.
- Japanese retail CSV (¥, comma numbers) → `price` coerced.

After Spec 1 ships, charts/KPIs on these datasets will improve substantially (since `classify_columns` now sees correct dtypes), but full chart/KPI polish — importance ranking, no-target fallbacks, hierarchical category extraction — is Spec 2.

## Risks / open questions

- **Multiple ID-named columns:** first kept, rest skipped. If a dataset has both `product_id` (primary) and `sku_code` (also keep), the order-based rule will skip `sku_code`. Documented limitation; users can manually re-include via cleaning audit if needed (out of scope for Spec 1).
- **`pd.to_datetime(format="mixed")` perf:** mitigated via 200-row sample gate.
- **`.str.contains` on NaN-bearing series:** mitigated via explicit `.dropna()` before contains and remapping `"nan"`/`"None"`/`""` to `pd.NA` early.
- **`DataContract.metric_columns` vs cleaned dtypes:** no conflict; downstream reads post-cleaning roles from `classify_columns(df)`, not the contract.
- **80% success threshold for numeric coercion:** chosen as a conservative cutoff. A column with 70% currency-stripped numeric and 30% genuine garbage stays as object (not converted). Documented; can be tuned later.
- **No automated end-to-end test of the cleaning pipeline.** Unit tests cover the helpers in isolation. Pipeline-level verification is via manual smoke per the user's instruction ("re-test on at least 3 datasets from different domains").

## Out of scope (Spec 2)

- Importance ranking on classified columns.
- Hierarchical category extraction ("Computers&Accessories|Accessories|Cables" → top-level).
- No-target chart/KPI fallbacks (distributions of top numeric columns, dominant category, spread metrics).
- Boolean-like string detection (Yes/No, Active/Inactive).
- High-cardinality categorical column handling for charts.
