"""
Insight Delivery Agent — synthesizes all upstream outputs into a stakeholder-ready InsightReport.

Claude Sonnet 4.6: narrative synthesis is its strongest capability.

PROMPT-3: the LLM emits two strictly-separated blocks — `executive_summary`
(plain English for stakeholders) and `analyst_detail` (statistical detail for
analysts). A deterministic sanitizer scrubs any statistical notation that leaks
into the executive block and swaps raw column names with their display names.
The frontend renders one block per tab.
"""
from __future__ import annotations

import json
import logging
import re

from agents.utils import strip_markdown_fences
from guardrails import UNTRUSTED_NOTE, delimit_untrusted, mask_pii, mask_row
from llm.client import get_client
from orchestrator.state import (
    ActionItem,
    AnalysisSession,
    AnalystDetail,
    AnomalyAlert,
    DataNarrative,
    ExecutiveSummary,
    InsightReport,
    RecommendedAction,
    Severity,
)

logger = logging.getLogger(__name__)


_SYSTEM = """\
You are a senior data analyst delivering findings to stakeholders. The user already
sees the briefing tables (head(5), schema with missing %, describe statistics) and
the chart panel — your job is to TIE IT ALL TOGETHER, not repeat the tables.

Return ONLY a JSON object with two strictly-separated blocks:
{
  "executive_summary": {
    "headline": "<1-line TL;DR a CEO would read at the top of an email>",
    "paragraphs": [
      "<sentence 1: what the data contains and its scope>",
      "<sentence 2: the most important finding>",
      "<sentence 3: what's concerning or needs attention>",
      "<sentence 4: recommended next step>"
    ],
    "findings": [
      "<plain-language bullet 1, no statistical notation>",
      "<plain-language bullet 2>"
    ],
    "action_items": [
      {"action": "<verb-led sentence specific enough to assign>", "expected_impact": "<one-line expected impact>"},
      ...
    ]
  },
  "analyst_detail": {
    "methodology": "<paragraph describing every method used: imputation strategies, outlier detection thresholds, statistical tests run, post-cleaning row/column counts>",
    "statistical_notes": [
      "<bullet with actual numbers: r=0.42, p<0.01, IQR×3.0, z>3.2 — exact thresholds>",
      "<another statistical bullet with raw column names as they appear in the DataFrame>"
    ],
    "caveats": [
      "<assumption or limitation 1>",
      "<assumption or limitation 2>"
    ],
    "code_references": [
      "<pseudo-code or short snippet showing how a key metric was derived>"
    ]
  },
  "anomaly_alerts": [
    {"title": "<MAX 8 words, pattern '[Column or metric]: [what is wrong]'>",
     "severity": "low|medium|high|critical",
     "description": "<MAX 2 sentences: [what we found] + [why it is a problem], plain English>",
     "affected_metric": "<raw column name>",
     "suggested_investigation": "<MAX 1 sentence, verb-led, specific enough to act on>"}
  ],
  "proactive_questions": [
    "<follow-up question 1>", "<follow-up question 2>", "<follow-up question 3>"
  ]
}

EXECUTIVE TAB content rules (executive_summary block AND anomaly_alerts):
- NO statistical notation anywhere. Forbidden: r=, p<, p=, p-value, std=, σ, μ,
  z-score, IQR, correlation coefficient, confidence interval, chi-square, t-test.
- NO raw column names. Convert snake_case/camelCase to natural language
  ("order_date" → "order date", "customerSegment" → "customer segment").
  If the prompt supplies a column_display_names map, USE IT — refer to columns
  by the human-readable label, never the raw name.
- NO algorithm or method names. Forbidden: "median imputation", "IQR method",
  "z-score", "linear regression", "k-means". Replace with plain phrases like
  "filled missing values using statistical estimates" or "flagged unusual data
  points".
- Numbers MUST be formatted for business readers:
  - Currency → "$1,234.56"
  - Percentages → "38.4%" (never "0.384")
  - Large numbers → "1.2M" or "45.3K" (not "1234567" or "45312")
  - Dates → "Jan 15, 2026" (not "2026-01-15")
- paragraphs: max 4 sentences COMBINED across the array. Structure as
  [scope] → [top finding] → [concern] → [next step].
- action_items: each entry starts with a verb, is specific enough to assign to a
  named person, and the expected_impact field explains the business outcome.
  GOOD: action="Review all transactions over $10,000 from March",
        expected_impact="12 flagged entries may indicate processing errors"
  BAD:  action="Investigate outliers in the amount column using IQR > 3x threshold"
- anomaly_alerts.description and suggested_investigation must each be max 2
  sentences and use plain English (no thresholds, no z-scores).

ANALYST TAB content rules (analyst_detail block ONLY):
- Full statistical detail is REQUIRED here: correlation coefficients, test
  statistics, p-values, confidence intervals.
- methodology MUST name the imputation method, outlier detection threshold, and
  every test that was run, plus the post-cleaning DataFrame shape (N rows, K
  features).
- statistical_notes: use raw column names exactly as they appear in the DataFrame.
- Include exact thresholds with numeric values. Example:
  "IQR × 3.0, flagged 14 rows in `transaction_amount` (z > 3.2, mean=…, std=…)".
- caveats: state every assumption and limitation explicitly.
- code_references: 1-3 pseudo-code lines showing how a key metric was derived,
  e.g. "fraud_rate = df['is_fraud'].mean()".

ANOMALY ALERT rules (PROMPT-4 — scan-in-5-seconds format):
- Emit AT MOST 5 alerts. If more than 5 issues exist, keep ONLY the 5 highest
  severity ones — the orchestrator appends a count note for the rest.
- Sort the array by severity, descending (critical → high → medium → low).
- title: MAX 8 words. Pattern is "[Column or metric]: [what is wrong]".
  GOOD: "Revenue: 34% weekly drop in Southeast"
        "Age: mixed case values detected"
        "Temperature: 12 extreme outliers"
        "Email: 8.3% missing values"
  BAD:  "There appears to be an issue with the Revenue column over time"
- description: MAX 2 sentences. First sentence states what was found; second
  states why it is a problem. NO statistical notation, NO method names.
- suggested_investigation: MAX 1 sentence. MUST start with a verb. Specific
  enough to assign to a named person.
  GOOD: "Standardize casing to uppercase before encoding."
  BAD:  "This issue should be investigated further to determine impact."
- severity assignment — apply these triggers strictly:
  - critical: dataset unusable without fixing. Examples: >50% missing in a
    needed column, wrong data types on >20% of columns, duplicate primary keys.
  - high: will cause incorrect results if not fixed. Examples: mixed casing in
    categoricals, >20% missing in important columns, imputation covering
    >30% of values in one column, data type mismatches.
  - medium: worth investigating but analysis can proceed. Examples: outliers
    under 5% of rows, moderate skew, 5-20% missing values, unexpected
    distributions.
  - low: informational only. Examples: confirmed expected patterns, minor
    formatting quirks, metadata notes.

OTHER rules:
- ALWAYS open the executive_summary headline by directly answering the user's
  exact question, in plain English.
- proactive_questions: 3 follow-up questions an analyst would naturally ask next.
- Return ONLY the JSON object — no markdown fences, no preamble.\
"""


# Tokens that must never appear in executive content. Substring match.
_FORBIDDEN_EXECUTIVE_TOKENS = (
    "p-value", "p<", "p =", "p=", "r=", "r =",
    "std=", "std =", "z-score", "z score", "z>", "z >", "z<", "z <",
    "iqr", "σ", "μ", "correlation coefficient", "confidence interval",
    "chi-square", "chi squared", "t-test", "anova", "regression",
    "median imputation", "mean imputation", "mode imputation",
    "z-score threshold", "outlier threshold",
)

# Method-phrase replacements — short alternatives that read naturally in context
# (e.g. "we filled values using statistical estimates"). Longer phrases must
# come first so 'median imputation' wins before 'imputation' alone matches.
_EXECUTIVE_REPLACEMENTS: dict[str, str] = {
    "median imputation": "statistical estimates",
    "mean imputation":   "statistical estimates",
    "mode imputation":   "the most common value",
    "iqr method":        "the standard outlier check",
    "z-score method":    "the standard outlier check",
    "correlation coefficient": "relationship strength",
    "confidence interval":     "uncertainty range",
}

# Collapse common duplicate-word artifacts left by phrase substitution.
_DUPLICATE_WORD_RE = re.compile(r"\b(using|with|by|the|of|for|a|an)\s+\1\b", re.IGNORECASE)

# Regex to detect raw snake_case / camelCase tokens that look like column names.
_RAW_COL_PATTERN = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b|\b[a-z]+(?:[A-Z][a-z0-9]+)+\b")


async def run_insight_agent(
    session: AnalysisSession,
    similar_past_insights: list[str] | None = None,
) -> InsightReport:
    """
    Generate the final InsightReport from all upstream agent outputs.
    similar_past_insights is injected by the orchestrator from Qdrant memory retrieval.
    """
    findings = session.analysis_findings
    contract = session.data_contract
    audit = session.cleaning_audit
    task = session.task_plan
    charts = session.chart_configs
    narrative = session.narrative

    if findings is None:
        raise ValueError("AnalysisFindings must be present before insight generation.")

    context_parts: list[str] = []

    if task:
        context_parts.append(f"User's question: {task.raw_query}")

    if contract:
        col_names = [c.name for c in contract.columns]
        context_parts.append(
            f"Dataset: {contract.row_count:,} rows, {contract.column_count} columns.\n"
            f"ALL column names: {', '.join(col_names)}\n"
            f"Metric/numeric columns: {contract.metric_columns}\n"
            f"Date columns: {contract.date_columns}\n"
            f"Schema: {contract.schema_summary}"
        )

    # PROMPT-3: hand the LLM the human-readable display names + entity noun so
    # the executive block can refer to columns naturally without raw casing.
    if narrative and narrative.column_display_names:
        display_block = "\n".join(
            f"  {raw} → {display}"
            for raw, display in narrative.column_display_names.items()
        )
        context_parts.append(
            "Column display names (use these in the executive block; raw names in analyst block):\n"
            f"{display_block}\n"
            f"Entity: one row = {narrative.entity_singular} (plural: {narrative.entity_plural})"
        )

    if audit:
        context_parts.append(f"Data quality: {audit.summary}")
        # PROMPT-5: feed the structured anomaly report into the prompt so the
        # LLM builds alerts off boundary values + 3·IQR detection, not its own
        # noisy IQR pass on raw data.
        report = audit.anomaly_report
        if report and (report.flags or report.methodology_note):
            anomaly_lines: list[str] = []
            for f in report.flags:
                if f.heavy_tailed:
                    anomaly_lines.append(
                        f"  {f.column}: HEAVY-TAILED, {f.extreme_pct:.1f}% past threshold — informational note only, NOT a high-severity alert"
                    )
                else:
                    anomaly_lines.append(
                        f"  {f.column}: {f.extreme_count} extreme outliers "
                        f"({f.extreme_pct:.1f}% of data, bounds [{f.lower_bound:.2f}, {f.upper_bound:.2f}], "
                        f"log_transform={f.used_log_transform})"
                    )
            if report.methodology_note:
                anomaly_lines.append(f"  methodology: {report.methodology_note}")
            if anomaly_lines:
                context_parts.append("Anomaly detector (use these for alerts):\n" + "\n".join(anomaly_lines))

    # Phase 2: feed the briefing payload into the prompt so the LLM can refer
    # to specific rows / dtypes / missing columns in its narrative.
    if findings.head_preview:
        context_parts.append(
            "Sample rows (head 5):\n"
            + delimit_untrusted("\n".join(str(mask_row(r)) for r in findings.head_preview))
        )
    if findings.dtypes_summary:
        context_parts.append(
            "Column types: "
            + ", ".join(f"{k}({v})" for k, v in list(findings.dtypes_summary.items())[:20])
        )
    if findings.missing_pct:
        # Only call out columns with non-trivial missingness
        notable_missing = sorted(
            ((k, v) for k, v in findings.missing_pct.items() if v > 1.0),
            key=lambda kv: kv[1], reverse=True,
        )[:5]
        if notable_missing:
            context_parts.append(
                "Top missing columns: "
                + ", ".join(f"{k}={v:.1f}%" for k, v in notable_missing)
            )

    if findings.eda_summary:
        eda_lines = []
        for col, stats in findings.eda_summary.items():
            if isinstance(stats, dict) and "mean" in stats:
                eda_lines.append(
                    f"  {col}: mean={stats.get('mean')}, std={stats.get('std')}, "
                    f"min={stats.get('min')}, max={stats.get('max')}"
                )
        if eda_lines:
            context_parts.append("EDA results:\n" + "\n".join(eda_lines))

    if findings.trends:
        trend_lines = []
        for t in findings.trends:
            seg = f" [{t.segment}]" if t.segment else ""
            trend_lines.append(f"  {t.column}{seg}: {t.direction} {t.pct_change:.1f}% ({t.period})")
        context_parts.append("Trends:\n" + delimit_untrusted("\n".join(trend_lines)))

    # Group names, trend segments, anomaly values and the analysis code's prose all carry cell
    # values (a cleaned frame can be just the injected row), so these sections are delimited.
    if findings.segments:
        seg_lines = []
        for seg in findings.segments[:5]:
            top_results = sorted(seg.results, key=lambda r: r.get("value", 0), reverse=True)[:3]
            seg_lines.append(
                f"  {seg.metric_column} by {seg.segment_column}: "
                + ", ".join(f"{r.get('name')}={r.get('value')}" for r in top_results)
            )
        context_parts.append("Segmentation:\n" + delimit_untrusted("\n".join(seg_lines)))

    if findings.anomalies:
        anomaly_lines = [
            f"  {a.column}: value={a.value}, severity={a.severity.value}, {a.explanation}"
            for a in findings.anomalies[:5]
        ]
        context_parts.append("Anomalies detected:\n" + delimit_untrusted("\n".join(anomaly_lines)))

    if findings.statistical_tests:
        test_lines = [
            f"  {t.test_name}: statistic={t.statistic:.4f}, p={t.p_value}, {t.interpretation}"
            for t in findings.statistical_tests
        ]
        context_parts.append("Statistical tests:\n" + delimit_untrusted("\n".join(test_lines)))

    if findings.correlations:
        strong = [(k, v) for k, v in findings.correlations.items() if abs(v) >= 0.5]
        if strong:
            context_parts.append(
                "Strong correlations: " + ", ".join(f"{k}={v:.2f}" for k, v in strong[:5])
            )

    if charts:
        # Titles carry data values (segment names, "A highest"), so they are data: masked, delimited.
        context_parts.append("Visualizations generated:\n" + delimit_untrusted(
            "\n".join(str(mask_pii(c.title)) for c in charts)))

    if similar_past_insights:
        context_parts.append(
            "Similar past analyses (institutional memory):\n"
            + "\n".join(f"  - {s}" for s in similar_past_insights)
        )

    prompt = "\n\n".join(context_parts)

    try:
        data = await _call_and_parse(prompt)
    except (json.JSONDecodeError, ValueError, IndexError):
        # One retry with an explicit "raw JSON only" reminder. Covers the common
        # failure mode where the LLM prefaces with prose or produces empty output.
        retry_prompt = (
            prompt
            + "\n\nIMPORTANT: Your previous response could not be parsed as JSON. "
            "Return ONLY a valid JSON object matching the schema. "
            "No markdown fences, no preamble, no trailing commentary."
        )
        try:
            data = await _call_and_parse(retry_prompt)
        except (json.JSONDecodeError, ValueError, IndexError) as exc:
            # Last resort: ship a structured report built from the findings we already have.
            # Typed LLM errors (refusal, truncation, outage) still propagate.
            logger.warning("insight: unparseable twice, using fallback report: %s", exc)
            return _fallback_report(session, contract, findings)

    # PROMPT-3: parse the two structured blocks, sanitize the executive block,
    # and derive the legacy flat fields for chat history / embeddings / PPTX.
    executive_view = _parse_executive(data.get("executive_summary"), narrative)
    analyst_view = _parse_analyst(data.get("analyst_detail"))

    raw_alerts = [
        AnomalyAlert(
            title=_clean_for_executive(a.get("title", "Anomaly detected"), narrative),
            severity=_map_severity(a.get("severity", "low")),
            description=_clean_for_executive(a.get("description", ""), narrative),
            affected_metric=a.get("affected_metric", ""),  # raw name kept for analyst tab
            suggested_investigation=_clean_for_executive(a.get("suggested_investigation", ""), narrative),
        )
        for a in data.get("anomaly_alerts", [])
    ]
    # PROMPT-4: scannable format + max 5, sorted by severity, with overflow note.
    anomaly_alerts, additional_issues_note = _compress_alerts(
        [_normalize_alert(a, findings.missing_pct if findings else {}) for a in raw_alerts],
    )

    # Legacy flat fields — derived so chat history, embeddings, and PPTX export
    # keep working without code changes on those consumers.
    legacy_summary = _flatten_executive_to_text(executive_view)
    legacy_findings = list(analyst_view.statistical_notes)
    legacy_methodology = analyst_view.methodology
    legacy_actions = [
        RecommendedAction(
            priority=i + 1,
            action=item.action,
            reasoning=item.expected_impact,    # closest legacy field
            metric_impact=item.expected_impact or None,
        )
        for i, item in enumerate(executive_view.action_items)
    ]

    return InsightReport(
        executive_summary=legacy_summary,
        technical_findings=legacy_findings,
        recommended_actions=legacy_actions,
        anomaly_alerts=anomaly_alerts,
        proactive_questions=data.get("proactive_questions", []),
        methodology_notes=legacy_methodology,
        executive_view=executive_view,
        analyst_view=analyst_view,
        additional_issues_note=additional_issues_note,
    )


# ── PROMPT-4: alert conciseness + severity reclassification ──────────────────

_SEVERITY_RANK: dict[Severity, int] = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
}

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _trim_words(s: str, max_words: int) -> str:
    """Cut a string at the Nth word, preserving trailing punctuation when it
    falls on the boundary. No ellipsis — alerts are scanned, not read."""
    if not s:
        return ""
    words = s.strip().split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]).rstrip(",;:")


def _trim_sentences(s: str, max_sentences: int) -> str:
    """Keep at most N sentences from the input. Splits on .!? boundaries."""
    if not s:
        return ""
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(s.strip()) if p.strip()]
    return " ".join(parts[:max_sentences]).strip()


def _enforce_title_pattern(title: str, affected_metric: str) -> str:
    """Title MUST be '[Column or metric]: [what is wrong]'. If the LLM produced
    a bare title with no colon, prepend the affected_metric so the pattern is
    consistent across datasets. Total length capped at 8 words."""
    title = (title or "").strip().rstrip(":")
    if not title:
        return _trim_words(affected_metric or "Issue detected", 8)
    if ":" not in title and affected_metric:
        title = f"{affected_metric}: {title}"
    return _trim_words(title, 8)


def _normalize_alert(alert: AnomalyAlert, missing_pct: dict[str, float]) -> AnomalyAlert:
    """Apply the PROMPT-4 alert shape: title ≤ 8 words in '[col]: [issue]' form,
    description ≤ 2 sentences, suggested_investigation ≤ 1 verb-led sentence,
    severity reclassified via objective triggers."""
    title = _enforce_title_pattern(alert.title, alert.affected_metric)
    description = _trim_sentences(alert.description, 2)
    investigation = _trim_sentences(alert.suggested_investigation, 1)
    severity = _classify_severity(alert, missing_pct)
    return AnomalyAlert(
        title=title,
        severity=severity,
        description=description,
        affected_metric=alert.affected_metric,
        suggested_investigation=investigation,
    )


def _classify_severity(alert: AnomalyAlert, missing_pct: dict[str, float]) -> Severity:
    """Map an alert to a severity tier using dataset-agnostic objective rules:
      critical  — column >50% missing, duplicate primary keys, dataset unusable
      high      — column >20% missing, mixed casing, >30% imputation, type mismatch
      medium    — column 5-20% missing, outliers <5%, skew, unexpected distribution
      low       — informational, formatting quirks, expected patterns
    The LLM's initial severity is the floor — we only escalate, never demote, so
    legitimately critical issues the heuristics miss still stand."""
    text = f"{alert.title} {alert.description} {alert.suggested_investigation}".lower()
    col = alert.affected_metric
    pct = float(missing_pct.get(col, 0.0)) if col else 0.0

    candidate = alert.severity

    # Critical triggers — strictly per the spec (numeric / structural)
    if pct > 50.0:
        candidate = max(candidate, Severity.CRITICAL, key=_SEVERITY_RANK.get)
    if "duplicate" in text and any(k in text for k in ("primary key", "primary-key", "row id", "unique id", "duplicate id")):
        candidate = max(candidate, Severity.CRITICAL, key=_SEVERITY_RANK.get)

    # High triggers
    if 20.0 < pct <= 50.0:
        candidate = max(candidate, Severity.HIGH, key=_SEVERITY_RANK.get)
    if any(p in text for p in ("mixed case", "mixed casing", "inconsistent case", "data type mismatch", "wrong data type", "wrong dtype")):
        candidate = max(candidate, Severity.HIGH, key=_SEVERITY_RANK.get)
    if "imputation" in text or "imputed" in text:
        # Imputation covering >30% of values is high-severity per the spec.
        if pct > 30.0:
            candidate = max(candidate, Severity.HIGH, key=_SEVERITY_RANK.get)

    # Medium triggers
    if 5.0 <= pct <= 20.0:
        candidate = max(candidate, Severity.MEDIUM, key=_SEVERITY_RANK.get)
    if any(k in text for k in ("outlier", "skew", "unexpected distribution", "extreme value")):
        candidate = max(candidate, Severity.MEDIUM, key=_SEVERITY_RANK.get)

    return candidate


def _compress_alerts(
    alerts: list[AnomalyAlert],
) -> tuple[list[AnomalyAlert], str]:
    """Sort by severity descending, keep the top 5, return an overflow note for
    the rest. Dataset-agnostic — works for Titanic, sales, sensors, surveys."""
    if not alerts:
        return [], ""
    ranked = sorted(alerts, key=lambda a: _SEVERITY_RANK.get(a.severity, 0), reverse=True)
    kept = ranked[:5]
    extras = len(ranked) - len(kept)
    note = ""
    if extras > 0:
        plural = "s" if extras != 1 else ""
        note = f"{extras} additional minor issue{plural} detected — see analyst detail tab."
    return kept, note


# ── PROMPT-3: parsers + sanitizer ────────────────────────────────────────────

def _parse_executive(raw, narrative: DataNarrative | None) -> ExecutiveSummary:
    """Coerce the LLM's `executive_summary` block into ExecutiveSummary. Runs
    the sanitizer on every string field so any stats notation or raw column
    names that slipped through are scrubbed before reaching the UI."""
    if not isinstance(raw, dict):
        return ExecutiveSummary()

    headline = _clean_for_executive(str(raw.get("headline") or "").strip(), narrative)

    paragraphs_raw = raw.get("paragraphs") or []
    paragraphs = [
        _clean_for_executive(str(p).strip(), narrative)
        for p in paragraphs_raw if isinstance(p, str) and p.strip()
    ][:4]

    findings_raw = raw.get("findings") or []
    findings = [
        _clean_for_executive(str(f).strip(), narrative)
        for f in findings_raw if isinstance(f, str) and f.strip()
    ]

    items: list[ActionItem] = []
    for it in raw.get("action_items") or []:
        if isinstance(it, dict):
            action = _clean_for_executive(str(it.get("action") or "").strip(), narrative)
            impact = _clean_for_executive(str(it.get("expected_impact") or "").strip(), narrative)
            if action:
                items.append(ActionItem(action=action, expected_impact=impact))
        elif isinstance(it, str) and it.strip():
            items.append(ActionItem(action=_clean_for_executive(it.strip(), narrative)))

    return ExecutiveSummary(
        headline=headline,
        paragraphs=paragraphs,
        findings=findings,
        action_items=items,
    )


def _parse_analyst(raw) -> AnalystDetail:
    """Coerce the LLM's `analyst_detail` block. NO sanitizing — analyst content
    is expected to retain statistical notation and raw column names."""
    if not isinstance(raw, dict):
        return AnalystDetail()

    def _strs(key: str) -> list[str]:
        v = raw.get(key) or []
        return [str(x).strip() for x in v if isinstance(x, str) and x.strip()]

    return AnalystDetail(
        methodology=str(raw.get("methodology") or "").strip(),
        statistical_notes=_strs("statistical_notes"),
        caveats=_strs("caveats"),
        code_references=_strs("code_references"),
    )


def _clean_for_executive(text: str, narrative: DataNarrative | None) -> str:
    """Deterministic safety net for executive content. The LLM is instructed to
    avoid stats notation and raw column names, but slips happen — this pass:
      1. swaps every known raw column name with its narrative display name,
      2. replaces forbidden method phrases ('median imputation' → '…'),
      3. converts unfamiliar snake_case/camelCase tokens to natural words,
      4. removes any remaining forbidden token.
    """
    if not text:
        return ""
    out = text

    # 1. Display-name swap. Skip no-ops (same word, different case) so common
    #    English verbs like 'Survived' aren't repeatedly replaced with the
    #    column's noun form. Longest raw-name first so 'order_date' wins over
    #    'order'.
    if narrative and narrative.column_display_names:
        for raw_name, display in sorted(
            narrative.column_display_names.items(),
            key=lambda kv: len(kv[0]), reverse=True,
        ):
            if not raw_name or raw_name.lower() == display.lower():
                continue
            pattern = re.compile(rf"`?\b{re.escape(raw_name)}\b`?", re.IGNORECASE)
            out = pattern.sub(display, out)

    # 2. Replace specific method phrases (case-insensitive).
    for needle, replacement in _EXECUTIVE_REPLACEMENTS.items():
        out = re.sub(re.escape(needle), replacement, out, flags=re.IGNORECASE)

    # 3. Convert any remaining snake_case / camelCase tokens to natural words.
    def _humanize(match: re.Match[str]) -> str:
        token = match.group(0)
        s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", token)
        s = s.replace("_", " ").replace("-", " ")
        return re.sub(r"\s+", " ", s).strip().lower()
    out = _RAW_COL_PATTERN.sub(_humanize, out)

    # 4. Final guard — strip any remaining forbidden token. The surrounding
    #    sentence stays; only the notation is scrubbed.
    for tok in _FORBIDDEN_EXECUTIVE_TOKENS:
        out = re.sub(re.escape(tok), "", out, flags=re.IGNORECASE)

    # Tidy up after substitution: collapse doubled spaces, then doubled fillers
    # ('using using' → 'using') created when a replacement happens next to the
    # same connector word already in the surrounding sentence.
    out = re.sub(r"\s{2,}", " ", out)
    out = _DUPLICATE_WORD_RE.sub(r"\1", out)
    # Drop stranded punctuation left after token removal: "of -3.1" → "of -3.1"
    # is fine; ", , " → ", "; " ," → ","; " ." → "."
    out = re.sub(r"\s+([,.;:])", r"\1", out)
    out = re.sub(r"(?:,\s*){2,}", ", ", out)
    return out.strip()


def _flatten_executive_to_text(ev: ExecutiveSummary) -> str:
    """Build the legacy `executive_summary` string from the structured block —
    used by chat history, embedding storage, PPTX export."""
    parts: list[str] = []
    if ev.headline:
        parts.append(ev.headline)
    if ev.paragraphs:
        parts.append(" ".join(ev.paragraphs))
    return " ".join(p for p in parts if p).strip()


async def _call_and_parse(prompt: str) -> dict:
    """Single Claude call + JSON parse. Raises on empty/invalid output."""
    response = await get_client().complete(
        agent="insight", prompt_version="v0",
        max_tokens=4000,        # bumped from 2000: full schema for wide datasets needs headroom
        system=_SYSTEM + "\n\n" + UNTRUSTED_NOTE,
        messages=[{"role": "user", "content": prompt}],
    )
    if not response.text:
        raise ValueError("LLM returned no text blocks")
    raw = strip_markdown_fences(response.text.strip())
    if not raw:
        raise ValueError("LLM returned empty text after stripping")
    return json.loads(raw)


def _fallback_report(session, contract, findings) -> InsightReport:
    """
    Built from data we already have — no LLM. Used when Claude fails twice
    so the user gets a structured response instead of an error screen.
    """
    parts: list[str] = []
    if contract:
        parts.append(
            f"Dataset has {contract.row_count:,} rows and {contract.column_count} columns "
            f"({', '.join(c.name for c in contract.columns[:8])}"
            f"{', ...' if len(contract.columns) > 8 else ''})."
        )
    if session.task_plan and session.task_plan.raw_query:
        parts.append(f"You asked: \"{session.task_plan.raw_query}\".")
    parts.append(
        "Insight generation hit a parsing error after two attempts. "
        "The charts below were generated from the analysis successfully — "
        "please re-ask your question or try a more specific query."
    )

    tech: list[str] = []
    if findings and findings.eda_summary:
        for col, stats in list(findings.eda_summary.items())[:5]:
            if isinstance(stats, dict) and "mean" in stats:
                tech.append(
                    f"{col}: mean={stats.get('mean')}, min={stats.get('min')}, max={stats.get('max')}"
                )
    if findings and findings.correlations:
        strong = sorted(findings.correlations.items(), key=lambda kv: abs(kv[1]), reverse=True)[:3]
        for pair, r in strong:
            tech.append(f"Correlation {pair}: {r:.2f}")

    legacy_summary = " ".join(parts)
    legacy_methodology = "Fallback report — LLM synthesis failed; this was assembled from the raw findings."
    legacy_findings = tech or ["No statistical results available."]
    return InsightReport(
        executive_summary=legacy_summary,
        technical_findings=legacy_findings,
        recommended_actions=[],
        anomaly_alerts=[],
        proactive_questions=[
            "Can you rephrase the question more specifically?",
            "Which columns are most important for your decision?",
            "Would you like a breakdown by a particular segment?",
        ],
        methodology_notes=legacy_methodology,
        executive_view=ExecutiveSummary(
            headline="Analysis ready — narrative generation unavailable",
            paragraphs=parts,
            findings=[],
            action_items=[],
        ),
        analyst_view=AnalystDetail(
            methodology=legacy_methodology,
            statistical_notes=legacy_findings,
            caveats=["Narrative synthesis failed — raw findings only."],
            code_references=[],
        ),
        additional_issues_note="",
    )


def _map_severity(s: str) -> Severity:
    return {
        "critical": Severity.CRITICAL,
        "high": Severity.HIGH,
        "medium": Severity.MEDIUM,
    }.get(s, Severity.LOW)
