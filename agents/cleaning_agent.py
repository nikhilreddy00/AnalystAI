"""
Data Cleaning Agent — cleans the DataFrame and produces a CleaningAudit.

Cleaning is done in Python for reliability; Claude Sonnet reasons about the
imputation strategy per column based on distribution shape and domain context.

Operations (in order):
1. Triage — flag URL/long-text/image-path/secondary-ID columns into a skip_set
2. Type coercion — strip currency/%/units/commas then try numeric; fall back to
   aggressive date detection (dayfirst both ways + epoch seconds)
3. Whitespace normalization — strip + lowercase low-cardinality object cols
   (skipped columns excluded)
4. Duplicate removal
5. Missing value imputation — strategy decided by Claude per column (skip_set excluded)
6. Outlier detection — IQR flagging on metric columns (flag only, not remove)

LangGraph checks CleaningAudit.high_null_columns to route to approval gate (>40% null).
"""
from __future__ import annotations

import io
import json

import pandas as pd
from anthropic import AsyncAnthropic

from agents.anomaly_detector import detect_anomalies
from agents.data_cleaning import (
    detect_dates_aggressive,
    triage_columns,
    try_numeric_conversion,
)
from agents.utils import strip_markdown_fences
from agents.visualization_agent import classify_columns
from api.config import settings
from orchestrator.state import (
    AnalysisSession,
    CleaningAudit,
    CleaningDecision,
    DataContract,
)

_client = AsyncAnthropic(api_key=settings.anthropic_api_key)

_SKIP_REASON = {
    "url": "URL column — not analytically useful",
    "long_text": "Long free-text column — not categorical",
    "image_path": "Image/file path column — not analytically useful",
    "secondary_id": "Secondary identifier — first ID kept; this one skipped",
}


async def run_cleaning_agent(session: AnalysisSession) -> tuple[pd.DataFrame, CleaningAudit]:
    """
    Clean the DataFrame stored in session and return (cleaned_df, CleaningAudit).
    Also updates session._df_parquet with the cleaned version.
    """
    df_parquet = session.__dict__.get("_df_parquet")
    if df_parquet is None:
        raise ValueError("No DataFrame in session. Run ingestion first.")

    df = pd.read_parquet(io.BytesIO(df_parquet))
    contract = session.data_contract
    if contract is None:
        raise ValueError("No DataContract in session.")

    decisions: list[CleaningDecision] = []
    rows_before = len(df)
    columns_coerced: list[str] = []
    outliers_flagged: list[str] = []

    # === Layer 1 (triage): identify obviously-noise columns BEFORE any type
    #     coercion. Skipped columns stay in df (so the schema table sees them)
    #     but are excluded from whitespace normalization, LLM-driven imputation,
    #     and downstream chart/KPI generation. ===
    triage = triage_columns(df)
    skip_set: set[str] = set()
    for kind, cols in triage.items():
        strategy = kind.removesuffix("_cols")
        for col in cols:
            skip_set.add(col)
            rows_affected = int(df[col].notna().sum())
            decisions.append(CleaningDecision(
                column=col,
                operation="skip",
                strategy=strategy,
                reason=_SKIP_REASON[strategy],
                rows_affected=rows_affected,
                before_null_pct=0.0,
                after_null_pct=0.0,
            ))

    # === Layer 2 (coercion): for every non-skipped column, try numeric coercion
    #     first (strips currency/%, units, comma formatting), then fall back to
    #     aggressive date detection. Subsumes the old explicit date-coercion
    #     loop that used contract.date_columns. ===
    for col in list(df.columns):
        if col in skip_set:
            continue

        # pandas 2.x+ may report string columns as StringDtype rather than
        # 'object', so use is_string_dtype which covers both.
        if pd.api.types.is_string_dtype(df[col]):
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
                continue

        dt = detect_dates_aggressive(df[col])
        if dt is not None:
            df[col] = dt
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

    # 2. Whitespace normalization on string columns (object or StringDtype)
    string_cols = [c for c in df.columns if pd.api.types.is_string_dtype(df[c])]
    for col in string_cols:
        if col in skip_set:
            continue
        # BUG-12: guard against mixed-type object columns (e.g., strings + ints + None)
        try:
            non_null = df[col].dropna()
            if len(non_null) > 0 and non_null.apply(type).eq(str).mean() > 0.5:
                df[col] = df[col].astype(str).str.strip()
                df.loc[df[col] == 'nan', col] = pd.NA  # restore NaN from astype(str)
                col_info = next((c for c in contract.columns if c.name == col), None)
                if col_info and not col_info.is_id and df[col].nunique() < 50:
                    df[col] = df[col].str.lower()
        except Exception:
            pass

    # 3. Duplicate removal
    dupes_before = len(df)
    df = df.drop_duplicates()
    duplicates_removed = dupes_before - len(df)

    # 4. Missing value imputation
    high_null_columns: list[str] = []
    null_counts = df.isnull().sum()
    cols_with_nulls = [col for col in df.columns if null_counts[col] > 0 and col not in skip_set]

    if cols_with_nulls:
        strategies = await _get_imputation_strategies(df, cols_with_nulls, contract)

        for col, strategy_info in strategies.items():
            if col not in df.columns:
                continue

            before_null_pct = null_counts[col] / len(df) * 100
            if before_null_pct > 40:
                high_null_columns.append(col)

            strategy = strategy_info.get("strategy", "mode")
            reason = strategy_info.get("reason", "Default strategy applied.")
            rows_affected = int(null_counts[col])

            if strategy == "mean" and pd.api.types.is_numeric_dtype(df[col]):
                df[col] = df[col].fillna(df[col].mean())
            elif strategy == "median" and pd.api.types.is_numeric_dtype(df[col]):
                df[col] = df[col].fillna(df[col].median())
            elif strategy == "mode":
                mode_vals = df[col].mode()
                if len(mode_vals) > 0:
                    df[col] = df[col].fillna(mode_vals[0])
            elif strategy == "forward_fill":
                # BUG-21: sort by date column before ffill for temporal continuity
                if contract.date_columns:
                    date_col = contract.date_columns[0]
                    if date_col in df.columns:
                        df = df.sort_values(date_col, na_position='last')
                df[col] = df[col].ffill()
            elif strategy == "drop_rows":
                # BUG-20: use current row count, not original rows_before
                current_rows = len(df)
                df = df.dropna(subset=[col])
                rows_affected = current_rows - len(df)

            after_null_pct = df[col].isnull().sum() / max(len(df), 1) * 100

            decisions.append(CleaningDecision(
                column=col,
                operation="impute",
                strategy=strategy,
                reason=reason,
                rows_affected=rows_affected,
                before_null_pct=round(before_null_pct, 1),
                after_null_pct=round(after_null_pct, 1),
            ))

    # 5. PROMPT-5: robust outlier detection. Replaces the old raw-IQR loop that
    # flagged 40-50% of rows on right-skewed columns. The detector filters out
    # IDs / binaries / low-card ordinals, applies log1p to highly skewed columns
    # before IQR, splits into mild / extreme tiers, and caps the total at 10%
    # of the dataset (switching to extreme-only when the cap is hit).
    column_roles = classify_columns(df)
    df, anomaly_report = detect_anomalies(df, column_roles)
    outliers_flagged = [f.message for f in anomaly_report.flags]

    rows_after = len(df)

    summary = _build_summary(
        decisions, duplicates_removed, outliers_flagged,
        high_null_columns, rows_before, rows_after,
        anomaly_methodology=anomaly_report.methodology_note,
    )

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

    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    session.__dict__["_df_parquet"] = buf.getvalue()

    return df, audit


async def _get_imputation_strategies(
    df: pd.DataFrame,
    cols_with_nulls: list[str],
    contract: DataContract,
) -> dict[str, dict]:
    """Ask Claude to recommend an imputation strategy for each column with nulls."""
    profiles: list[str] = []
    for col in cols_with_nulls:
        null_pct = df[col].isnull().sum() / len(df) * 100
        dtype = str(df[col].dtype)
        skewness_str = ""
        if pd.api.types.is_numeric_dtype(df[col]):
            try:
                sk = float(df[col].skew())
                skewness_str = f", skewness={sk:.2f}"
            except Exception:
                pass
        col_info = next((c for c in contract.columns if c.name == col), None)
        is_date = col_info.is_date if col_info else False
        profiles.append(
            f"- {col}: dtype={dtype}, null%={null_pct:.1f}{skewness_str}, "
            f"is_time_series={is_date}, unique_values={df[col].nunique()}"
        )

    prompt = f"""\
For each column, choose the best imputation strategy.
Return a JSON object: {{"column_name": {{"strategy": "<strategy>", "reason": "<1 sentence>"}}, ...}}

Valid strategies: mean, median, mode, forward_fill, drop_rows

Rules:
- Numeric, |skewness| < 1 → mean
- Numeric, |skewness| >= 1 → median
- Object / categorical → mode
- Time-series ordered (is_time_series=true) → forward_fill
- null% > 80 → drop_rows

Columns:
{chr(10).join(profiles)}

Return ONLY the JSON object — no explanation.\
"""

    response = await _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    raw = strip_markdown_fences(raw)

    return json.loads(raw.strip())


def _build_summary(
    decisions: list[CleaningDecision],
    duplicates_removed: int,
    outliers_flagged: list[str],
    high_null_columns: list[str],
    rows_before: int,
    rows_after: int,
    anomaly_methodology: str = "",
) -> str:
    parts: list[str] = []
    if duplicates_removed:
        parts.append(f"Removed {duplicates_removed} duplicate rows.")
    if decisions:
        parts.append(f"Imputed missing values in {len(decisions)} column(s).")
    if outliers_flagged:
        # Each entry is either "N extreme outliers in COL ..." or
        # "COL has a heavy-tailed distribution ..." — keep just the column name.
        names: list[str] = []
        for s in outliers_flagged:
            tokens = s.split()
            if "in" in tokens:
                names.append(tokens[tokens.index("in") + 1])
            elif tokens:
                names.append(tokens[0])
        if names:
            parts.append(f"Outliers flagged in: {', '.join(names)}.")
    if anomaly_methodology:
        parts.append(anomaly_methodology)
    if high_null_columns:
        parts.append(
            f"High null rate (>40%) detected in: {', '.join(high_null_columns)} — review recommended."
        )
    parts.append(f"Final dataset: {rows_after} rows (started with {rows_before}).")
    return " ".join(parts)
