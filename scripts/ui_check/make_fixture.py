"""Builds the UI-check fixtures in scripts/ui_check/fixtures. No LLM calls.

Charts, KPIs and the briefing are computed from evals datagen sales-1. Evidence is the real
output of one analysis-agent run, kept in the committed full.sse. The insight report and
review are hand-written from the numbers in a real e2e-01 answer, so the UI has every panel.

    uv run python scripts/ui_check/make_fixture.py scripts/ui_check/fixtures
"""

import json
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
from evals import datagen
from agents.visualization_agent import (
    classify_columns,
    generate_smart_kpis,
    compute_briefing_extras,
    run_visualization_agent,
)
from evals.run import load_cases, viz_session

OUT = sys.argv[1]
evidence = next(
    json.loads(line[6:])["data"]
    for line in open(f"{OUT}/full.sse")
    if line.startswith('data: {"type": "evidence"')
)

df, _ = datagen.make_sales(1)
roles = classify_columns(df)
q = "Give me an overview of this sales data"
# Phase 5: charts from the real agent on the viz-v14 fixture (trend, segments, correlation), so
# "why this chart" and its evidence links render; the fixture's evidence replaces the SSE's.
_case = next(c for c in load_cases("visualization", "viz-v14"))
_session, _ = viz_session(_case, df)
charts = [c.model_dump() for c in __import__("asyncio").run(run_visualization_agent(_session))]
evidence = [e.model_dump() for e in _session.analysis_findings.evidence]
kpis = [k.model_dump() for k in generate_smart_kpis(df, roles, q)]
extras = compute_briefing_extras(df, roles, None)
num = df.select_dtypes("number")
briefing = {
    "head_preview": json.loads(df.head(5).to_json(orient="records", date_format="iso")),
    "dtypes_summary": {c: str(t) for c, t in df.dtypes.items()},
    "missing_pct": {c: round(float(v) * 100, 2) for c, v in df.isna().mean().items()},
    "describe_stats": json.loads(num.describe().to_json()),
    "kpis": kpis,
    **extras,
}

insight = {
    "executive_summary": "Segment B's revenue is falling while its marketing spend climbs — the business is paying more to earn less there.",
    "technical_findings": [
        "revenue ~ marketing_spend: Pearson r = 0.57 (n = 3,000)",
        "Segment A mean revenue $121.29 vs B $98.06 vs C $78.88 (ANOVA p < 0.001)",
        "Segment B: revenue −12.6% MoM, marketing_spend +31.2%, discount +8.7%",
        "3 revenue outliers > $900 (IQR × 3 rule)",
    ],
    "recommended_actions": [
        {
            "priority": 1,
            "action": "Deep-dive Segment B order economics",
            "reasoning": "Spend up 31%, revenue down 13%",
            "metric_impact": "revenue",
        },
        {
            "priority": 2,
            "action": "Review the three orders above $900",
            "reasoning": "Extreme outliers can distort forecasts",
            "metric_impact": None,
        },
    ],
    "anomaly_alerts": [
        {
            "title": "Three extreme revenue spikes",
            "severity": "high",
            "description": "Three orders exceed $900, far above the typical $60–$140 range.",
            "affected_metric": "revenue",
            "suggested_investigation": "Check for data-entry or bulk-order errors.",
        }
    ],
    "proactive_questions": [
        "Why did Segment B's revenue drop last month?",
        "Which regions drive Segment A's lead?",
        "Is the discount increase hurting margin?",
    ],
    "methodology_notes": "3,025 rows → 25 exact duplicates dropped → 3,000. Median imputation grouped by segment and region for one column. Outliers by IQR × 3.",
    "executive_view": {
        "headline": "Segment B's revenue is falling while its marketing spend climbs.",
        "paragraphs": [
            "The dataset covers 3,000 sales orders from November 2025 across three customer segments and several regions. After removing 25 duplicates, the data is analysis-ready.",
            "Higher marketing spend is clearly associated with higher revenue overall, and Segment A earns the most per order at $121.29, against $98.06 for B and $78.88 for C.",
        ],
        "findings": [
            "Segment B revenue fell 12.6% month over month while its marketing spend rose 31.2%.",
            "Segment A earns 24% more per order than Segment B.",
            "Three orders above $900 look like outliers and need checking.",
        ],
        "action_items": [
            {
                "action": "Deep-dive Segment B's order economics",
                "expected_impact": "Stop paying more for less revenue",
            },
            {
                "action": "Review the three orders above $900",
                "expected_impact": "Cleaner forecasts and budgets",
            },
        ],
    },
    "analyst_view": {
        "methodology": "Pearson correlation on numeric pairs, one-way ANOVA across segment, month-over-month trend per segment, IQR × 3 outlier rule.",
        "statistical_notes": [
            "r(revenue, marketing_spend) = 0.570, n = 3,000",
            "ANOVA F on revenue by segment, p < 0.001",
        ],
        "caveats": [
            "One month of data limits trend conclusions.",
            "Correlation is not causation: spend may follow expected demand.",
        ],
        "code_references": ["df.groupby('segment')['revenue'].mean()"],
    },
    "additional_issues_note": "",
    "generated_at": "2026-10-27T12:00:00Z",
}

review = {
    "review_summary": "Solid first pass: the headline is grounded in computed numbers and caveats are stated. It stops short of explaining Segment B's decline.",
    "strengths": [
        "Every headline number traces to evidence",
        "Outliers flagged before forecasting",
    ],
    "gaps": ["No breakdown of Segment B by region", "Discount effect on margin not tested"],
    "next_investigations": [
        "Segment B revenue by region and week",
        "Regress revenue on discount within segment",
    ],
    "methodology_concerns": ["Single month — MoM change may be noise"],
    "generated_at": "2026-10-27T12:00:00Z",
}

steps = [
    ("ingestion", "Loading data...", "Loaded 3,025 rows × 11 columns"),
    ("intake", "Understanding your question...", "Plan: overview across segment and region"),
    ("cleaning", "Cleaning data...", "Dropped 25 duplicates; imputed 1 column"),
    (
        "analysis",
        "Running statistical analysis...",
        "Analysis complete — 2 segment breakdown(s), 3 anomaly(ies) detected",
    ),
    ("visualization", "Building charts...", f"{len(charts)} charts"),
    ("insight", "Writing the report...", "Report ready"),
    ("quality_check", "Checking the report...", "Passed"),
]
ev = [{"type": "session_id", "value": "ui-check"}]
for a, s, c in steps:
    ev += [
        {"type": "agent_start", "agent": a, "message": s},
        {"type": "agent_complete", "agent": a, "message": c},
    ]
    if a == "analysis":
        ev += [{"type": "briefing", "data": briefing}, {"type": "evidence", "data": evidence}]
    if a == "visualization":
        ev.append({"type": "charts", "data": charts})
ev += [
    {"type": "insight", "data": insight},
    {"type": "expert_review", "data": review},
    {"type": "suggested_questions", "questions": insight["proactive_questions"]},
    {"type": "done"},
]


def w(path, evs):
    open(path, "w").write("".join(f"data: {json.dumps(e, default=str)}\n\n" for e in evs))


w(f"{OUT}/full.sse", ev)
w(
    f"{OUT}/error.sse",
    ev[:7]
    + [
        {
            "type": "error",
            "message": "The analysis sandbox timed out. Try again, or ask a narrower question.",
        },
        {"type": "done"},
    ],
)
w(
    f"{OUT}/approval.sse",
    ev[:5]
    + [
        {
            "type": "approval_required",
            "context": "high_null",
            "high_null_columns": ["discount"],
            "message": "discount is 46% empty. Impute, drop the column, or continue?",
        }
    ],
)
print(len(charts), "charts", len(kpis), "kpis", len(evidence), "evidence")
rows = json.loads(df.to_json(orient="records", date_format="iso"))
json.dump(
    {
        "session_id": "ui-check",
        "row_count": len(rows),
        "column_roles": json.loads(json.dumps(roles, default=list)),
        "rows": rows,
    },
    open(f"{OUT}/dash_data.json", "w"),
    default=str,
)
json.dump(
    {
        "latest": {
            "session_id": "ui-check",
            "updated_at": "2026-10-27T12:00:00Z",
            "raw_query": q,
            "charts": charts,
            "insight": insight,
            "briefing": briefing,
            "expert_review": review,
        }
    },
    open(f"{OUT}/dash_latest.json", "w"),
    default=str,
)
