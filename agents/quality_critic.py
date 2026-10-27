"""
Quality critic — the LLM half of the quality check (formerly the senior_expert node; folded
into quality_check_node in Phase 2). The deterministic half (numeric grounding) runs first and
its verdicts are handed to the critic.

Acts as a principal data analyst reviewing a junior analyst's work. Takes the
full session context (briefing, findings, charts, insight report) and produces
a structured critique with:
  - overall assessment
  - what the analysis did well
  - what's missing or could go deeper
  - specific next investigations to run
  - methodology concerns / caveats

Only runs on the first-run auto-overview (Phase 3 gates this) so follow-up
Q&A stays fast.

Resilience pattern matches insight_agent: defensive JSON parse + one retry on
empty/malformed + minimal fallback report so the user always sees something.
"""
from __future__ import annotations

import json
import logging
import re

from agents.utils import strip_markdown_fences
from llm.client import get_client
from orchestrator.state import AnalysisSession, ExpertReview

logger = logging.getLogger(__name__)


# PROMPT-9: shared sentence splitter used by the deterministic 2-sentence cap
# applied to every bullet after the LLM responds. Belt-and-suspenders for the
# explicit prompt rule.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _cap_sentences(text: str, max_n: int = 2) -> str:
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split((text or "").strip()) if p.strip()]
    return " ".join(parts[:max_n]).strip()


_SYSTEM = """\
You are a Principal Data Analyst at a mature data team reviewing the output of
a junior analyst's first-pass analysis. You are constructive but rigorous —
your goal is to push the analysis to the next level of depth and to surface
investigations the junior missed.

Return ONLY a JSON object with these exact fields:
{
  "review_summary": "<EXACTLY 3 sentences, following this pattern verbatim — fill the bracketed values from the actual session: 'The analysis correctly addressed [N of M] requested tasks and identified [key finding in plain English]. However, [N] methodology gaps and [N] areas warrant further investigation before stakeholder delivery.'>",
  "strengths": ["<bullet>", "<bullet>", ...],
  "gaps": ["<bullet>", ...],
  "next_investigations": ["<bullet>", ...],
  "methodology_concerns": ["<bullet>", ...]
}

PROMPT-9 RULES — apply to every bullet:
- Each bullet is MAX 2 sentences.
  Sentence 1: what you observed.
  Sentence 2: why it matters or what to do.
- Each bullet MUST reference specific columns, values, percentages, or methods
  from THIS dataset. Forbidden generic phrases: "consider more analysis",
  "could be improved", "explore further", "additional context would help".
- methodology_concerns bullets MUST cite which method was used AND name the
  alternative. Example pattern: "IQR×1.5 was used on Fare which is log-normal;
  switch to log-IQR or a Mahalanobis distance to avoid flagging the natural
  right tail." If no real concern exists, return an empty list.
- 0-5 items per list. Empty lists are FINE when the situation truly warrants —
  the absence of methodology concerns is itself useful information.
- next_investigations must be concretely runnable, naming the column(s) and
  method (e.g. "Run a chi-square test on Pclass × Survived...").
- Don't repeat what the insight report already says.
- Return ONLY the JSON — no markdown fences, no preamble.\
"""


async def run_quality_critic(
    session: AnalysisSession, ungrounded: list[str] | None = None,
) -> ExpertReview:
    """
    Critique the session's analysis. `ungrounded` are numbers in the report that the
    deterministic grounding check could not find in the findings.
    Returns a fallback ExpertReview if Claude's output is unparseable twice.
    """
    context_parts: list[str] = []

    if session.task_plan:
        context_parts.append(f"User's original question: {session.task_plan.raw_query}")

    if session.data_contract:
        dc = session.data_contract
        context_parts.append(
            f"Dataset: {dc.row_count:,} rows × {dc.column_count} columns.\n"
            f"All columns: {[c.name for c in dc.columns]}\n"
            f"Metric columns: {dc.metric_columns}\n"
            f"Date columns: {dc.date_columns}"
        )

    findings = session.analysis_findings
    if findings:
        if findings.eda_summary:
            eda_lines = []
            for col, stats in list(findings.eda_summary.items())[:8]:
                if isinstance(stats, dict) and "mean" in stats:
                    eda_lines.append(
                        f"  {col}: mean={stats.get('mean')}, std={stats.get('std')}, "
                        f"min={stats.get('min')}, max={stats.get('max')}"
                    )
            if eda_lines:
                context_parts.append("EDA results:\n" + "\n".join(eda_lines))

        if findings.correlations:
            strong = sorted(findings.correlations.items(), key=lambda kv: abs(kv[1]), reverse=True)[:5]
            context_parts.append(
                "Top correlations: " + ", ".join(f"{k}={v:.2f}" for k, v in strong)
            )

        if findings.segments:
            seg_lines = []
            for seg in findings.segments[:4]:
                top = sorted(seg.results, key=lambda r: r.get("value", 0), reverse=True)[:3]
                seg_lines.append(
                    f"  {seg.metric_column} by {seg.segment_column}: "
                    + ", ".join(f"{r.get('name')}={r.get('value')}" for r in top)
                )
            context_parts.append("Segmentations done:\n" + "\n".join(seg_lines))

        if findings.anomalies:
            context_parts.append(
                f"Anomalies detected: {len(findings.anomalies)} flagged "
                f"(top: {findings.anomalies[0].column}={findings.anomalies[0].value})"
            )

        if findings.statistical_tests:
            test_lines = [
                f"  {t.test_name}: stat={t.statistic:.3f}, p={t.p_value}, {t.interpretation}"
                for t in findings.statistical_tests
            ]
            context_parts.append("Statistical tests:\n" + "\n".join(test_lines))

    if session.chart_configs:
        context_parts.append(
            "Visualizations: " + ", ".join(c.title for c in session.chart_configs)
        )

    if session.insight_report:
        r = session.insight_report
        context_parts.append(
            f"Junior's executive summary: {r.executive_summary}\n"
            f"Junior's technical findings: {r.technical_findings}\n"
            f"Junior's recommended actions: "
            + "; ".join(f"({a.priority}) {a.action}" for a in r.recommended_actions[:4])
        )

    if ungrounded:
        context_parts.append(
            "Deterministic grounding check: these numbers in the junior's report do not appear "
            f"in the computed findings: {ungrounded}. If they are factual claims, raise them as "
            "methodology concerns (unsupported numbers)."
        )

    prompt = "\n\n".join(context_parts)

    try:
        data = await _call_and_parse(prompt)
    except (json.JSONDecodeError, ValueError, IndexError):
        retry_prompt = (
            prompt
            + "\n\nIMPORTANT: Your previous response could not be parsed as JSON. "
            "Return ONLY a valid JSON object matching the schema."
        )
        try:
            data = await _call_and_parse(retry_prompt)
        except (json.JSONDecodeError, ValueError, IndexError) as exc:
            logger.warning("quality critic: unparseable twice, using fallback review: %s", exc)
            return _fallback_review(session, ungrounded)

    # PROMPT-9: deterministic 2-sentence cap on every bullet so the panel stays
    # scannable even when the LLM ignores the prompt rule.
    def _bullets(key: str) -> list[str]:
        return [
            _cap_sentences(str(s), 2)
            for s in data.get(key, [])
            if s and str(s).strip()
        ][:5]

    return ExpertReview(
        review_summary=str(data.get("review_summary", "")).strip(),
        strengths=_bullets("strengths"),
        gaps=_bullets("gaps"),
        next_investigations=_bullets("next_investigations"),
        methodology_concerns=_bullets("methodology_concerns"),
    )


async def _call_and_parse(prompt: str) -> dict:
    response = await get_client().complete(
        agent="quality_critic", prompt_version="v0",
        max_tokens=2000,
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    if not response.text:
        raise ValueError("LLM returned no text blocks")
    raw = strip_markdown_fences(response.text.strip())
    if not raw:
        raise ValueError("LLM returned empty text after stripping")
    return json.loads(raw)


def _fallback_review(session: AnalysisSession, ungrounded: list[str] | None = None) -> ExpertReview:
    """Built from session data when the LLM fails — user gets something useful."""
    contract = session.data_contract
    findings = session.analysis_findings
    n_charts = len(session.chart_configs)

    strengths = []
    gaps = []
    next_inv = ["Re-run the review (the LLM call failed this time)."]
    concerns = ["The expert review could not be generated automatically; treat the insight report as preliminary."]
    if ungrounded:
        concerns.append(f"These numbers in the report are not in the computed findings: {ungrounded}.")

    if findings and findings.eda_summary:
        strengths.append(f"Computed EDA statistics for {len(findings.eda_summary)} column(s).")
    if findings and findings.correlations:
        strengths.append(f"Surfaced {len(findings.correlations)} correlations.")
    if n_charts:
        strengths.append(f"Generated {n_charts} visualization(s).")
    if contract and contract.date_columns:
        next_inv.append(f"Run a time-series trend analysis on {contract.date_columns[0]}.")
    if findings and not findings.statistical_tests:
        gaps.append("No statistical significance tests were performed.")

    return ExpertReview(
        review_summary=(
            "Expert review fallback — the LLM synthesis did not return parseable output. "
            "The findings below are extracted directly from session data."
        ),
        strengths=strengths or ["Initial analysis completed."],
        gaps=gaps or ["Could not assess gaps without LLM review."],
        next_investigations=next_inv,
        methodology_concerns=concerns,
    )
