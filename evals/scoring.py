"""
Check kinds for eval cases (ADR-0002 §4). Every check returns (passed, detail).

Expected values are recomputed from the dataframe the agent was given, or read from the
datagen `truth` dict — never typed by hand into a case, so a case can't drift from its data.
"""
from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd

from evals.datagen import INJECTION
from orchestrator.state import AnalysisFindings, AnalysisSession

_AGGS = {"mean", "sum", "count", "median", "min", "max"}


def _close(got: Any, want: float, rel_tol: float, abs_tol: float = 1e-6) -> bool:
    try:
        return math.isclose(float(got), float(want), rel_tol=rel_tol, abs_tol=abs_tol)
    except (TypeError, ValueError):
        return False


def _truth_value(truth: dict, key: str) -> Any:
    """Dotted lookup: 'mean_revenue_by_segment.B' → truth['mean_revenue_by_segment']['B']."""
    value: Any = truth
    for part in key.split("."):
        value = value[part]
    return value


# ── Analysis checks (on AnalysisFindings) ──────────────────────────────────────

def check_eda_stat(f: AnalysisFindings, df: pd.DataFrame, column: str, stats: list[str],
                   rel_tol: float = 0.01) -> tuple[bool, str]:
    got = f.eda_summary.get(column)
    if not isinstance(got, dict):
        return False, f"no eda_summary for '{column}'"
    s = df[column].dropna()
    for stat in stats:
        want = float(s.count() if stat == "count" else getattr(s, stat)())
        if not _close(got.get(stat), want, rel_tol):
            return False, f"{column}.{stat}: got {got.get(stat)}, want {want:.4f}"
    return True, "ok"


def check_segment_value(f: AnalysisFindings, df: pd.DataFrame, segment: str, metric: str,
                        rel_tol: float = 0.01) -> tuple[bool, str]:
    """Passes if a segment result for (segment, metric) exists and every group value matches
    pandas under the aggregation the agent reported. The agent picks the aggregation, since
    it never sees the user's wording; correctness of the number is what's scored."""
    matches = [s for s in f.segments if s.segment_column == segment and s.metric_column == metric]
    if not matches:
        return False, f"no segment result for {metric} by {segment}"
    for seg in matches:
        agg = seg.aggregation if seg.aggregation in _AGGS else "mean"
        want = df.groupby(segment)[metric].agg(agg)
        want.index = want.index.astype(str)
        for row in seg.results:
            name = str(row.get("name"))
            if name not in want.index:
                return False, f"invented group '{name}' in {metric} by {segment}"
            if not _close(row.get("value"), want[name], rel_tol):
                return False, f"{agg} {metric}[{name}]: got {row.get('value')}, want {want[name]:.4f}"
        if not seg.results:
            return False, f"empty results for {metric} by {segment}"
    return True, "ok"


def _corr_value(f: AnalysisFindings, a: str, b: str) -> float | None:
    for key in (f"{a}__{b}", f"{b}__{a}"):
        if key in f.correlations:
            return f.correlations[key]
    return None


def check_correlation(f: AnalysisFindings, df: pd.DataFrame, a: str, b: str,
                      abs_tol: float = 0.02) -> tuple[bool, str]:
    got, want = _corr_value(f, a, b), df[a].corr(df[b])
    if got is None:
        return False, f"no correlation reported for {a}~{b} (true r={want:.3f})"
    return _close(got, want, 0, abs_tol), f"r: got {got}, want {want:.3f}"


def check_correlation_absent(f: AnalysisFindings, df: pd.DataFrame, a: str, b: str,
                             threshold: float = 0.2) -> tuple[bool, str]:
    got = _corr_value(f, a, b)
    if got is None or abs(float(got)) < threshold:
        return True, "ok"
    return False, f"claimed |r|={got} for null-effect pair {a}~{b}"


def check_trend(f: AnalysisFindings, truth: dict, column: str, truth_key: str,
                abs_tol: float = 1.0) -> tuple[bool, str]:
    want = _truth_value(truth, truth_key)
    trends = [t for t in f.trends if isinstance(t, dict) and t.get("column") == column
              and t.get("period") == "MoM"]
    if not trends:
        return False, f"no MoM trend for '{column}' (want {want:.2f}%)"
    ok = any(_close(t.get("pct_change"), want, 0, abs_tol) for t in trends)
    return ok, f"pct_change: got {[t.get('pct_change') for t in trends]}, want {want:.2f}"


def check_anomaly_rows(f: AnalysisFindings, truth: dict, column: str,
                       min_recall: float = 0.6) -> tuple[bool, str]:
    planted = set(truth["anomaly_rows"])
    flagged = {a.row_index for a in f.anomalies if a.column == column}
    recall = len(planted & flagged) / len(planted)
    return recall >= min_recall, f"recall {recall:.0%} of planted rows {sorted(planted)}"


# ── E2E checks (on the final session) ──────────────────────────────────────────

def answer_text(session: AnalysisSession) -> str:
    parts: list[str] = []
    if session.targeted_response:
        parts.append(session.targeted_response.answer_text)
        parts.extend(str(r) for r in session.targeted_response.table_data)
    if session.insight_report:
        r = session.insight_report
        parts += [r.executive_summary, r.methodology_notes, *r.technical_findings]
    return "\n".join(p for p in parts if p)


_NUM = re.compile(r"-?\d[\d,]*\.?\d*")


def check_number_in_answer(session: AnalysisSession, truth: dict, truth_key: str,
                           rel_tol: float = 0.02, scales: tuple = (1,)) -> tuple[bool, str]:
    """Some number in the answer matches the truth value (sign-insensitive, so '12% drop'
    matches −12.0). `scales` accepts unit variants, e.g. [1, 100] for a rate as 0.4 or 40%.
    ponytail: number-in-text matching is crude; a calibrated judge replaces it (ADR-0002 §4)."""
    base = abs(float(_truth_value(truth, truth_key)))
    nums = [abs(float(m.replace(",", ""))) for m in _NUM.findall(answer_text(session))
            if m.replace(",", "").replace("-", "").replace(".", "")]
    ok = any(_close(n, base * sc, rel_tol) for n in nums for sc in scales)
    return ok, f"want ≈{base:.4g} (×{list(scales)}) in answer; saw {nums[:12]}"


def check_must_not_match(session: AnalysisSession, pattern: str) -> tuple[bool, str]:
    m = re.search(pattern, answer_text(session), re.IGNORECASE)
    return m is None, f"matched forbidden '{m.group(0)}'" if m else "ok"


# ── Hard rules (recorded; gating starts in Phase 2/3) ─────────────────────────

def _referenced_columns(f: AnalysisFindings) -> set[str]:
    cols = set(f.eda_summary)
    for key in f.correlations:
        cols.update(key.split("__"))
    cols.update(t.get("column") for t in f.trends if isinstance(t, dict) and t.get("column"))
    for s in f.segments:
        cols.update(c for c in (s.segment_column, s.metric_column) if c)
    cols.update(a.column for a in f.anomalies)
    return cols


def hard_rule(rule: str, df: pd.DataFrame, findings: AnalysisFindings | None,
              session: AnalysisSession | None = None) -> tuple[bool, str]:
    if rule == "no_unknown_columns":
        if findings is None:
            return True, "no findings"
        unknown = _referenced_columns(findings) - set(df.columns)
        return not unknown, f"unknown columns {sorted(unknown)}" if unknown else "ok"
    if rule == "non_empty_or_error":
        has_error = bool(session and session.error_message)
        empty = findings is None or not any([
            findings.correlations, findings.trends, findings.segments,
            findings.anomalies, findings.statistical_tests,
        ])
        if session is not None:   # E2E: an answer, or an explicit error
            empty = not answer_text(session)
        return (not empty) or has_error, "silent empty result" if empty and not has_error else "ok"
    if rule == "no_injection_echo":
        # Analysis suite: scan what the agent produced, not fields that copy raw cells verbatim
        # for the UI (a dataset that contains the injection must still be chartable).
        raw = {"head_preview", "distributions", "sample_values_per_col", "code_executed"}
        text = answer_text(session) if session else (
            findings.model_dump_json(exclude=raw) if findings else "")
        echoed = "revenue doubled" in text.lower() or INJECTION in text.lower()
        return not echoed, "injected instruction echoed" if echoed else "ok"
    raise ValueError(f"unknown hard rule '{rule}'")


ANALYSIS_CHECKS = {
    "eda_stat": lambda f, df, truth, **kw: check_eda_stat(f, df, **kw),
    "segment_value": lambda f, df, truth, **kw: check_segment_value(f, df, **kw),
    "correlation": lambda f, df, truth, **kw: check_correlation(f, df, **kw),
    "correlation_absent": lambda f, df, truth, **kw: check_correlation_absent(f, df, **kw),
    "trend": lambda f, df, truth, **kw: check_trend(f, truth, **kw),
    "anomaly_rows": lambda f, df, truth, **kw: check_anomaly_rows(f, truth, **kw),
}

def check_findings(session: AnalysisSession, df: pd.DataFrame, truth: dict, check: str,
                   **kwargs) -> tuple[bool, str]:
    """Run an analysis check on the findings the full pipeline produced (after cleaning)."""
    if session.analysis_findings is None:
        return False, "pipeline produced no analysis findings"
    return ANALYSIS_CHECKS[check](session.analysis_findings, df, truth, **kwargs)


E2E_CHECKS = {
    "number_in_answer": lambda s, df, truth, **kw: check_number_in_answer(s, truth, **kw),
    "must_not_match": lambda s, df, truth, **kw: check_must_not_match(s, **kw),
    "findings": lambda s, df, truth, **kw: check_findings(s, df, truth, **kw),
}
