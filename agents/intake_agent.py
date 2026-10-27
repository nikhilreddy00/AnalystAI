"""
Stakeholder Intake Agent — translates natural language into a structured AnalysisTask.

Claude Sonnet 4.6 handles:
- Disambiguation of vague requests (asks ONE clarifying question)
- Multi-turn amendment (amends existing task, sets resume_from to skip re-ingestion)
- Compound request decomposition ("show revenue AND churn" → ordered analysis_types)
"""
from __future__ import annotations

import json
import logging

from agents.errors import LLMOutputError
from agents.utils import strip_markdown_fences
from guardrails import UNTRUSTED_NOTE, delimit_untrusted
from llm.client import get_client
from orchestrator.state import (
    AnalysisSession,
    AnalysisTask,
    AnalysisType,
    DataContract,
    DataNarrative,
    QuestionIntent,
    TargetedParams,
)

logger = logging.getLogger(__name__)


_SYSTEM = """\
You are a senior data analyst's intake specialist. Translate user requests into structured analysis plans.

Return ONLY a JSON object with these exact fields:
{
  "raw_query": "<user's exact request>",
  "target_metrics": ["<column_name>", ...],
  "timeframe": "<e.g. last 90 days>" or null,
  "segments": ["<column_name>", ...],
  "analysis_types": ["eda", "trend", "segmentation", "anomaly", "correlation", "hypothesis", "forecasting"],
  "output_format": "both",
  "resume_from": null,
  "clarification_needed": false,
  "clarification_question": null,
  "question_intent": "overview|drill_down|chart_request|export_request|meta_question|correlation|distribution|top_n|time_series|segment_compare|hypothesis_test|ab_significance|outlier_scan",
  "targeted_params": {
    "target_columns": [],
    "metric_column": null,
    "date_column": null,
    "segment_column": null,
    "group_by": null,
    "variant_column": null,
    "n": null,
    "order": null,
    "frequency": null,
    "test_type": null,
    "aggregation": null,
    "chart_type": null
  }
}

Selection rules for analysis_types (EDA is always included first):
- "compare", "vs", "by", "breakdown" → add segmentation
- "trend", "over time", "monthly", "weekly", "growth" → add trend
- "spike", "unusual", "anomaly", "outlier", "weird" → add anomaly
- "correlat", "relationship between", "affect" → add correlation
- "significant", "test", "hypothesis", "p-value" → add hypothesis
- "forecast", "predict", "next month", "future" → add forecasting

Question-intent classifier (pick exactly one, drives routing):
- overview         → broad first-pass survey: "give me an overview", "what's in this data", "explore", "summarize"
- drill_down       → any follow-up that builds on, re-explains, or reframes a
  prior analysis/finding. Requires that the user has already received an analysis
  in this session. This is the DEFAULT bucket for follow-up questions about the
  RESULTS (not the data structure). Examples:
  "tell me more about why revenue dropped"
  "what's driving the spike in churn"
  "break down the anomaly you found in March"
  "explain the findings"  /  "explain this to me"
  "summarize all the findings for non-technical stakeholders"
  "how do I explain this to a non-technical audience?"
  "what does this mean for the business?"
  "why is X happening"
  Use correlation when the user wants a NEW numeric correlation computed; use
  drill_down whenever the user is asking WHY a result happened OR to explain,
  summarize, simplify, or reframe an analysis they already received.
- chart_request    → user wants a specific chart, not a full analysis. Examples:
  "show me a bar chart of revenue by region"
  "plot price vs square footage"
  "scatter plot of age and income"
  Populate TargetedParams.chart_type with one of: BarChart, LineChart,
  ScatterChart, PieChart, DonutChart, HistogramChart, HorizontalBarChart,
  HeatmapChart, ComposedChart. Also fill target_columns / metric_column /
  group_by from the column references in the user's request.
- export_request   → user wants to download the analysis. Examples:
  "download as pptx", "export the report", "save this analysis",
  "give me a powerpoint", "send me the report"
  No additional parameter extraction needed.
- meta_question    → STRICTLY questions about the data's STRUCTURE — shape, schema,
  column names, data types, or a raw preview. NOTHING about the analysis results,
  meaning, or business implications. Examples (these are the ONLY kind that qualify):
  "how many columns", "how many rows", "what's the schema", "what columns exist",
  "what are the column names", "what data types are these", "show me a preview"
  Leave TargetedParams empty unless the user names specific columns.
  These are NOT meta_question (do NOT classify them here):
  - "explain the findings" / "explain this to me"        → drill_down
  - "summarize for stakeholders" / "summarize the results" → drill_down
  - "why is X happening" / "what's driving X"            → drill_down
  - any request about what the analysis MEANS or implies   → drill_down
  When uncertain whether a follow-up is about structure vs. results, prefer
  drill_down — only use meta_question when the user is unambiguously asking about
  the schema/shape of the table.
- correlation      → "correlat", "related to", "relationship between A and B", "what affects", "associated with"
- distribution     → "distribution of X", "histogram", "how is X spread", "range of X"
- top_n            → "top/bottom N", "best/worst", "highest/lowest", "rank", "leaderboard"
- time_series      → "over time", "trend of", "month/week/day/year over year", "growth in X"
- segment_compare  → "compare X by Y", "X vs Y", "X across regions/categories", "breakdown of X by Z"
- hypothesis_test  → "is X significantly different", "test if A > B", "p-value of"
- ab_significance  → "A/B test", "control vs treatment", "variant", "lift", "experiment"
- outlier_scan     → "outliers", "anomalies", "unusual values", "weird rows"

Greetings / chit-chat ("hi", "hello", "hey", "thanks", "ok", "what can you do?"):
classify as "overview". These are NOT meta_question — never answer a greeting with
schema facts. On an already-analyzed dataset the router will offer the user fresh
starting points rather than re-dumping the table shape.

Default to "overview" when ambiguous or genuinely broad. NEVER fall back to
meta_question when uncertain — meta_question is only for explicit structure/schema
questions.

targeted_params extraction (fill ONLY what the chosen intent needs, leave others as null/[]):
- correlation      → target_columns=[<2 or more numeric cols>] (empty = full numeric matrix)
- distribution     → target_columns=[<single col>]
- top_n            → metric_column, group_by, n (default 10 if unspecified), order ("desc" unless asked "bottom"), aggregation (default "sum")
- time_series      → metric_column, date_column, frequency ("D"|"W"|"M"|"Q"|"Y", default "M")
- segment_compare  → metric_column, segment_column, aggregation (default "mean")
- hypothesis_test  → target_columns=[<cols involved>], test_type ("ttest"|"chi2"|"anova")
- ab_significance  → variant_column, metric_column
- outlier_scan     → target_columns=[<cols to scan>] (empty = all numeric)
- chart_request    → chart_type, target_columns, metric_column, group_by
- overview/drill_down/export_request/meta_question → leave all targeted_params fields empty

Context awareness for follow-ups:
- If the user message is a short follow-up ("what about Q4?", "and by region?", "show me bottom 5 instead"),
  CARRY FORWARD metric_column/date_column/segment_column from the prior task_plan or recent assistant turn.
- Example: prior was "top 10 products by revenue", current is "what about bottom 5?" →
  intent=top_n, metric_column="revenue", group_by="product", n=5, order="asc".

Multi-turn rules:
- If data_contract is already present and user is drilling deeper → set resume_from="analysis"
- If user is only changing the segment/filter → set resume_from="analysis"
- Never set resume_from if user mentions a completely different dataset or metric

Clarification rules:
- Set clarification_needed=true ONLY if the query gives zero signal about what to measure
- One clarifying question max — make it multiple-choice if possible
- Examples of vague: "how are we doing?", "check the data", "any insights?"

Return ONLY the JSON — no explanation, no markdown fences.
""" + UNTRUSTED_NOTE

# Phase 3: appended to the system prompt when this is the FIRST run on a freshly
# connected dataset. Overrides the user's specific wording to guarantee a full
# end-to-end senior-analyst pass regardless of what they asked.
_OVERVIEW_OVERRIDE = """\

OVERRIDE — FIRST-RUN COMPREHENSIVE OVERVIEW MODE:
This is the user's first analysis on a freshly connected dataset. Regardless of
their specific wording, produce a COMPREHENSIVE overview plan:
- analysis_types MUST include at least: ["eda", "segmentation", "correlation", "anomaly"].
  Add "forecasting" only if date columns exist. Add "trend" if date columns exist.
- target_metrics: set to ALL metric/numeric columns from the schema (not just one).
- segments: set to the top 3 categorical columns from the schema.
- clarification_needed: MUST be false — do not gate the first run on clarification.
- resume_from: MUST be null — run the full pipeline.
- raw_query: prepend "[Dataset overview] " to the user's actual message.
- question_intent: MUST be "overview" (downstream router uses this to run the full pipeline).
- targeted_params: leave all fields null/[] — not used in overview mode.
If the user's wording suggests a specific focus (e.g. "show me churn"), you may
bias target_metrics toward those columns, but never DROP coverage below the
comprehensive baseline above.\
"""


async def run_intake_agent(
    session: AnalysisSession,
    user_message: str,
    is_overview_request: bool = False,
) -> AnalysisTask:
    """
    Parse natural language into a structured AnalysisTask.
    Handles both first-turn queries and multi-turn follow-ups.

    Phase 3: when `is_overview_request` is True, the agent is forced to plan a
    comprehensive end-to-end pass regardless of the user's specific wording.
    """
    history_text = ""
    if session.conversation_history:
        recent = session.conversation_history[-6:]
        history_text = "\n".join(f"{m.role.value.upper()}: {m.content}" for m in recent)

    schema_text = ""
    if session.data_contract:
        dc = session.data_contract
        categorical_cols = [
            c.name for c in dc.columns
            if not c.is_metric and not c.is_id and not c.is_date
        ]
        schema_text = (
            f"Dataset: {dc.row_count} rows, {dc.column_count} columns\n"
            f"Metric columns: {dc.metric_columns}\n"
            f"Date columns: {dc.date_columns}\n"
            f"Categorical columns: {categorical_cols}\n"
            f"Schema: {dc.schema_summary}"
        )

    existing_task = ""
    if session.task_plan:
        existing_task = f"Existing analysis task (amend if this is a follow-up):\n{session.task_plan.model_dump_json()}"

    user_content = "\n\n".join(filter(bool, [
        f"User message: {user_message}",
        f"Conversation history:\n{history_text}" if history_text else "",
        f"Dataset schema:\n{delimit_untrusted(schema_text)}" if schema_text else "",
        existing_task,
    ]))

    system_prompt = _SYSTEM + _OVERVIEW_OVERRIDE if is_overview_request else _SYSTEM

    response = await get_client().complete(
        agent="intake", prompt_version="v0",
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )

    raw = response.text.strip()
    raw = strip_markdown_fences(raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMOutputError("intake", f"malformed JSON: {exc}",
                             "I couldn't parse my own plan for that question. Try rephrasing.") from exc

    # Hard rule: the plan never names a column the dataset doesn't have.
    if session.data_contract:
        known = {c.name for c in session.data_contract.columns}
        for key in ("target_metrics", "segments"):
            cols = data.get(key) or []
            invented = [c for c in cols if c not in known]
            if invented:
                logger.warning("intake: dropped invented %s %s", key, invented)
                data[key] = [c for c in cols if c in known]

    valid_types = {t.value for t in AnalysisType}
    data["analysis_types"] = [t for t in data.get("analysis_types", ["eda"]) if t in valid_types]
    if not data["analysis_types"]:
        data["analysis_types"] = [AnalysisType.EDA.value]

    valid_intents = {i.value for i in QuestionIntent}
    intent_raw = data.get("question_intent")
    if is_overview_request or intent_raw not in valid_intents:
        data["question_intent"] = QuestionIntent.OVERVIEW.value

    try:
        data["targeted_params"] = TargetedParams(**(data.get("targeted_params") or {}))
    except Exception:
        data["targeted_params"] = TargetedParams()

    return AnalysisTask(**data)


async def generate_data_narrative(contract: DataContract) -> DataNarrative:
    """
    PROMPT-2 follow-up: build once-per-dataset rendering hints (column display
    names + entity noun). Downstream agents (KPI, chart titles, alerts) read
    these directly so no LLM call is needed at render time.

    The LLM sees every column with a short sample-value snippet so it can infer
    abbreviations like 'Pclass' → 'Passenger Class' or 'SibSp' → 'Siblings/Spouses'.
    """
    columns_block_lines: list[str] = []
    for c in contract.columns:
        sample = ", ".join(repr(v) for v in (c.sample_values or [])[:3])
        columns_block_lines.append(f"- {c.name} ({c.dtype}) — samples: [{sample}]")
    columns_block = "\n".join(columns_block_lines)

    prompt = f"""\
You are labeling a dataset for human-readable display.

{UNTRUSTED_NOTE}

Dataset columns ({contract.row_count:,} rows × {contract.column_count} columns):
{delimit_untrusted(columns_block)}

Return ONLY a JSON object with these exact keys:
{{
  "column_display_names": {{ "<raw_column_name>": "<Title Case Human Readable Label>", ... }},
  "entity_singular": "<noun describing one row, lowercased — e.g. 'passenger', 'order', 'patient', 'transaction'>",
  "entity_plural": "<plural form of entity_singular>"
}}

Rules:
- One entry in column_display_names for EVERY column above.
- Expand common abbreviations: "Pclass" → "Passenger Class", "SibSp" → "Siblings/Spouses",
  "Parch" → "Parents/Children", "qty" → "Quantity", "ts" → "Timestamp", "amt" → "Amount".
- snake_case and camelCase columns → Title Case (e.g. "unit_price" → "Unit Price").
- Keep names short — 1-4 words, no trailing punctuation, no descriptions.
- Pick entity_singular based on what each row actually represents. Look at the
  columns: name+age+sex → "person" (or more specific: "passenger" if there's a
  ticket/fare; "patient" if there's a diagnosis/treatment). order_id + amount +
  customer → "order". transaction_id + merchant → "transaction".
- entity_plural is just the normal English plural.
- Return ONLY the JSON object. No markdown fences, no explanation.\
"""

    try:
        response = await get_client().complete(
            agent="intake", prompt_version="v0",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = strip_markdown_fences(response.text.strip())
        data = json.loads(raw)
    except Exception as exc:
        logger.warning("intake: narrative fell back to defaults: %s", exc)
        # Fallback narrative — entity stays generic, display names empty so the
        # KPI helpers fall back to deterministic title-casing.
        return DataNarrative()

    names = data.get("column_display_names") or {}
    if not isinstance(names, dict):
        names = {}
    # Coerce to {str: str} and drop empty values
    safe_names: dict[str, str] = {}
    for k, v in names.items():
        if isinstance(k, str) and isinstance(v, str) and v.strip():
            safe_names[k] = v.strip()

    entity_singular = str(data.get("entity_singular") or "record").strip().lower() or "record"
    entity_plural = str(data.get("entity_plural") or "records").strip().lower() or "records"

    return DataNarrative(
        column_display_names=safe_names,
        entity_singular=entity_singular,
        entity_plural=entity_plural,
    )


async def generate_proactive_questions(contract: DataContract) -> list[str]:
    """
    FEAT-01: Generate contextual suggested questions based on the dataset schema.
    Called after data ingestion so the user sees relevant starting prompts.
    """
    col_names = [c.name for c in contract.columns]
    metric_cols = contract.metric_columns
    date_cols = contract.date_columns
    categorical_cols = [
        c.name for c in contract.columns
        if not c.is_metric and not c.is_id and not c.is_date
    ]

    prompt = f"""You are a senior data analyst who just received a new dataset.
Generate exactly 5 natural-language questions a business stakeholder would want answered.

Dataset: {contract.row_count:,} rows × {contract.column_count} columns
Metric columns: {metric_cols}
Date columns: {date_cols}
Categorical columns: {categorical_cols}
All columns: {col_names}
Schema: {contract.schema_summary}

Rules:
- Make questions specific to THIS dataset's columns (use actual column names)
- Mix question types: overview, trend, comparison, anomaly, correlation
- Questions should be immediately actionable (no setup needed)
- Return a JSON array of 5 strings — no explanation, no markdown fences."""

    try:
        response = await get_client().complete(
            agent="intake", prompt_version="v0",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = strip_markdown_fences(response.text.strip())
        questions = json.loads(raw)
        if isinstance(questions, list) and len(questions) > 0:
            return questions[:5]
    except Exception as exc:
        logger.warning("intake: suggested questions fell back to defaults: %s", exc)

    # Fallback: generate generic but dataset-aware questions
    fallback = []
    if metric_cols:
        fallback.append(f"Give me a complete overview of {metric_cols[0]}")
    if date_cols and metric_cols:
        fallback.append(f"Show me trends in {metric_cols[0]} over time")
    if categorical_cols and metric_cols:
        fallback.append(f"Compare {metric_cols[0]} across different {categorical_cols[0]} groups")
    fallback.append("Find any anomalies or outliers in the data")
    fallback.append("What are the key correlations in this dataset?")
    return fallback[:5]
