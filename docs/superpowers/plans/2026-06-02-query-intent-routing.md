# Query Intent Routing — Implementation Plan (Spec 1 of Gap 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four new question intents (CHART_REQUEST, EXPORT_REQUEST, META_QUESTION, DRILL_DOWN ← DEEP_DIVE) so the chat pipeline routes to the minimal handler instead of always running the full 7-agent pipeline. Add a "rerun previous overview?" approval gate for OVERVIEW on already-completed sessions.

**Architecture:** Three sequential phases — backend classifier + the three deterministic intents → DRILL_DOWN node + rerun gate → frontend wiring. The intake agent remains the single classifier (no new pre-intake LLM call). The new lightweight intents handle inside `targeted_analysis_node` with no LLM calls; DRILL_DOWN gets a new node + new agent (one LLM call). Source spec at [docs/superpowers/specs/2026-06-02-query-intent-routing-design.md](../specs/2026-06-02-query-intent-routing-design.md).

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, LangGraph, Anthropic SDK, Next.js 16, React, pytest + pytest-asyncio.

**Testing convention.** Same convention as the Gap 6 plan: pytest for pure-Python pieces where it pays off (the model_validator coercion, the META handler, the dispatch logic), curl + log + UI smoke for graph nodes, FastAPI routes, and React components. The project already has `pytest>=8.2`, `pytest-asyncio>=0.23`, and `asyncio_mode = "auto"` configured.

**Phase verification gates.** Each phase is shippable on its own and verifies independently — do not start a later phase until the current phase's gate passes.

---

## Phase 1 — Classifier + the three deterministic intents

Invisible to end users (no frontend changes). After this phase, sending `"how many columns?"` / `"download as pptx"` / `"bar chart of X by Y"` via `/chat` returns a compact `TargetedResponse` without running the full pipeline.

### Task 1.1: Schema changes — enum + chart_type + deep_dive coercion

**Files:**
- Modify: `orchestrator/state.py`
- Create: `tests/test_state_intent_schema.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_state_intent_schema.py`:

```python
from __future__ import annotations

import pytest

from orchestrator.state import (
    AnalysisTask,
    AnalysisType,
    ChartType,
    QuestionIntent,
    TargetedParams,
)


def test_new_intents_present_on_enum():
    assert QuestionIntent.CHART_REQUEST.value == "chart_request"
    assert QuestionIntent.EXPORT_REQUEST.value == "export_request"
    assert QuestionIntent.META_QUESTION.value == "meta_question"
    assert QuestionIntent.DRILL_DOWN.value == "drill_down"


def test_deep_dive_value_no_longer_in_enum():
    # The literal string should be gone — only the coercion path preserves backward compat.
    with pytest.raises(ValueError):
        QuestionIntent("deep_dive")


def test_targeted_params_chart_type_optional():
    p = TargetedParams()
    assert p.chart_type is None
    p2 = TargetedParams(chart_type=ChartType.BAR)
    assert p2.chart_type == ChartType.BAR


def test_analysis_task_coerces_deep_dive_to_drill_down():
    data = {
        "raw_query": "tell me more about churn",
        "target_metrics": ["churn"],
        "analysis_types": [AnalysisType.STATISTICAL] if hasattr(AnalysisType, "STATISTICAL") else [],
        "question_intent": "deep_dive",
    }
    task = AnalysisTask.model_validate(data)
    assert task.question_intent == QuestionIntent.DRILL_DOWN


def test_analysis_task_accepts_drill_down_directly():
    data = {
        "raw_query": "why",
        "target_metrics": [],
        "analysis_types": [],
        "question_intent": "drill_down",
    }
    task = AnalysisTask.model_validate(data)
    assert task.question_intent == QuestionIntent.DRILL_DOWN


def test_analysis_task_accepts_new_intents():
    for raw in ("chart_request", "export_request", "meta_question"):
        data = {
            "raw_query": "x",
            "target_metrics": [],
            "analysis_types": [],
            "question_intent": raw,
        }
        task = AnalysisTask.model_validate(data)
        assert task.question_intent.value == raw
```

The `AnalysisType` reference is conditional because the existing enum may use different member names; the test reads whichever member exists or falls back to an empty list. The implementer should adjust the import once they read state.py.

- [ ] **Step 2: Confirm failure**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run pytest tests/test_state_intent_schema.py -v`
Expected: all 6 tests fail or error (enum lacks new values; no model_validator).

- [ ] **Step 3: Update `QuestionIntent` enum**

In `orchestrator/state.py`, replace the existing `QuestionIntent` class:

```python
class QuestionIntent(str, Enum):
    """Phase A: classifier output that drives pipeline routing.

    OVERVIEW runs the full 7-agent pipeline.
    DRILL_DOWN runs a compact follow-up via drill_down_node.
    All other intents route through targeted_analysis_node for a compact answer.
    """
    OVERVIEW = "overview"
    DRILL_DOWN = "drill_down"
    CORRELATION = "correlation"
    DISTRIBUTION = "distribution"
    TOP_N = "top_n"
    TIME_SERIES = "time_series"
    SEGMENT_COMPARE = "segment_compare"
    HYPOTHESIS_TEST = "hypothesis_test"
    AB_SIGNIFICANCE = "ab_significance"
    OUTLIER_SCAN = "outlier_scan"
    CHART_REQUEST = "chart_request"
    EXPORT_REQUEST = "export_request"
    META_QUESTION = "meta_question"
```

- [ ] **Step 4: Add `chart_type` to `TargetedParams`**

Inside `class TargetedParams(BaseModel)`, append one field at the end:

```python
    chart_type: ChartType | None = None     # CHART_REQUEST: bar/line/scatter/etc.
```

- [ ] **Step 5: Add the `deep_dive` coercion validator on `AnalysisTask`**

At the top of `state.py`, extend the pydantic import line to include `model_validator`. If the line currently reads:

```python
from pydantic import BaseModel, Field
```

change to:

```python
from pydantic import BaseModel, Field, model_validator
```

Then inside `class AnalysisTask(BaseModel)`, just below the `targeted_params` line, add:

```python
    @model_validator(mode="before")
    @classmethod
    def _coerce_deprecated_intents(cls, data):
        # Persisted sessions from before the DEEP_DIVE → DRILL_DOWN rename will
        # carry "deep_dive" in their JSON. Silently upgrade so model_validate_json
        # never blows up loading them.
        if isinstance(data, dict) and data.get("question_intent") == "deep_dive":
            data = {**data, "question_intent": "drill_down"}
        return data
```

- [ ] **Step 6: Run tests to confirm they pass**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run pytest tests/test_state_intent_schema.py -v`
Expected: 6 passed. (If `AnalysisType.STATISTICAL` doesn't exist, the test will pass empty lists which is fine since `analysis_types: list[AnalysisType]` accepts an empty list per the existing field default behavior. If pydantic rejects an empty list, the implementer updates the test to use whatever values the enum actually has.)

- [ ] **Step 7: Smoke-check imports for downstream modules**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run python -c "from orchestrator.state import QuestionIntent, TargetedParams; from orchestrator.routing import _FULL_PIPELINE_INTENTS; print(QuestionIntent.DRILL_DOWN, len(QuestionIntent))"`
Expected: `QuestionIntent.DRILL_DOWN 13`. (Routing imports may fail because routing.py still references `DEEP_DIVE` — that's the next task. If so, fix Task 1.2 next; the import error is expected here.)

- [ ] **Step 8: Commit**

```bash
git add orchestrator/state.py tests/test_state_intent_schema.py
git commit -m "feat(state): add CHART/EXPORT/META intents + chart_type, coerce deep_dive→drill_down"
```

---

### Task 1.2: Update intake prompt + routing constants

**Files:**
- Modify: `agents/intake_agent.py`
- Modify: `orchestrator/routing.py`

- [ ] **Step 1: Update `_FULL_PIPELINE_INTENTS` in `orchestrator/routing.py`**

The current line near the top of `routing.py` reads:

```python
_FULL_PIPELINE_INTENTS = {QuestionIntent.OVERVIEW, QuestionIntent.DEEP_DIVE}
```

Change to:

```python
_FULL_PIPELINE_INTENTS = {QuestionIntent.OVERVIEW}
```

If any other line in `routing.py` references `QuestionIntent.DEEP_DIVE` (search with `grep -n DEEP_DIVE orchestrator/routing.py`), replace with `QuestionIntent.DRILL_DOWN`. The grep should return no other matches after this edit.

- [ ] **Step 2: Update the intake system prompt**

In `agents/intake_agent.py`, read the existing `_SYSTEM` constant (the string holding the system prompt). Find the section that lists intent examples. Locate the existing `DEEP_DIVE` example block — delete it.

Then add four new example blocks (the implementer chooses where in the list to place them — keep them adjacent to `OVERVIEW` so the LLM sees the full intent menu together):

```
- DRILL_DOWN: follow-up that builds on a prior finding. Requires that the user
  has already received an analysis in this session. Examples:
  "tell me more about why revenue dropped"
  "what's driving the spike in churn"
  "break down the anomaly you found in March"

- CHART_REQUEST: user wants a specific chart, not a full analysis. Examples:
  "show me a bar chart of revenue by region"
  "plot price vs square footage"
  "scatter plot of age and income"
  Populate TargetedParams.chart_type with one of: BarChart, LineChart,
  ScatterChart, PieChart, DonutChart, HistogramChart, HorizontalBarChart,
  HeatmapChart, ComposedChart. Also fill target_columns / metric_column /
  group_by from the column references in the user's request.

- EXPORT_REQUEST: user wants to download the analysis. Examples:
  "download as pptx", "export the report", "save this analysis",
  "give me a powerpoint", "send me the report"
  No additional parameter extraction needed.

- META_QUESTION: user asks ABOUT the data structure, not about its content.
  Examples:
  "how many columns", "what are the column names", "what's the schema",
  "what data types are these", "how many rows", "show me a preview"
  Leave TargetedParams empty unless the user names specific columns.
```

If the system prompt also has language that mentions `DEEP_DIVE` outside of the example block (e.g. a list of supported intent strings), search and replace `DEEP_DIVE` with `DRILL_DOWN` and `deep_dive` with `drill_down`.

- [ ] **Step 3: Verify imports parse**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run python -c "from orchestrator.routing import _FULL_PIPELINE_INTENTS, route_after_intake, route_after_ingestion; from agents.intake_agent import run_intake_agent; print(len(_FULL_PIPELINE_INTENTS))"`
Expected: `1`

- [ ] **Step 4: Run all tests**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run pytest tests/ -v`
Expected: all tests pass (the 6 new from Task 1.1 + the 11 from Gap 6 = 17 passed).

- [ ] **Step 5: Commit**

```bash
git add orchestrator/routing.py agents/intake_agent.py
git commit -m "feat(intake): add new intent prompt examples; shrink full-pipeline set to OVERVIEW"
```

---

### Task 1.3: `build_single_chart` helper in `visualization_agent`

**Files:**
- Modify: `agents/visualization_agent.py`

- [ ] **Step 1: Read the existing visualization agent**

Run: `grep -n "def \|classify_columns\|chart_configs\|ChartConfig" /Users/uvnikhil/Desktop/AnalystAI/agents/visualization_agent.py | head -30`

Note the existing helpers — `classify_columns`, `run_visualization_agent`, `generate_smart_kpis`, `compute_briefing_extras`. The new helper reuses primitives from this module so the chart looks consistent with the other charts the system produces.

- [ ] **Step 2: Add `build_single_chart` at the end of the file**

Add this function. The implementer adapts the column-selection logic to use whatever helpers already exist in `visualization_agent.py` — e.g. `classify_columns(df)` to get roles, and the existing chart-data assembly path:

```python
def build_single_chart(
    df: pd.DataFrame,
    params: "TargetedParams",
) -> "ChartConfig":
    """Build one ChartConfig from CHART_REQUEST params + DataFrame.

    Deterministic — no LLM call. Falls back to a reasonable default when
    columns the user named aren't present in the dataframe (returns a chart
    over whatever columns are valid).
    """
    from orchestrator.state import ChartConfig, ChartType
    import uuid

    chart_type = params.chart_type or ChartType.BAR

    # Resolve x and y from TargetedParams. Prefer explicit metric_column for y,
    # group_by for x. Fall back to the first column the user listed in
    # target_columns. Only keep columns that actually exist in df.
    available = list(df.columns)
    x_key = None
    y_keys: list[str] = []

    if params.group_by and params.group_by in available:
        x_key = params.group_by
    if params.metric_column and params.metric_column in available:
        y_keys = [params.metric_column]
    for col in params.target_columns:
        if col not in available:
            continue
        if x_key is None:
            x_key = col
        elif col not in y_keys:
            y_keys.append(col)

    if x_key is None or not y_keys:
        # Last-resort fallback: pick first non-id column for x, first numeric for y.
        roles = classify_columns(df)
        for col in df.columns:
            if col not in roles.get("id", []):
                x_key = x_key or col
                break
        for col in df.columns:
            if col in roles.get("continuous", []) or col in roles.get("metric", []):
                if col != x_key:
                    y_keys = [col]
                    break
        if x_key is None:
            x_key = df.columns[0]
        if not y_keys and len(df.columns) > 1:
            y_keys = [df.columns[1]]

    # Build chart data: small aggregation so chat-rendered charts stay light.
    # For bar / pie / horizontal bar: aggregate by x_key.
    grouped_types = {ChartType.BAR, ChartType.HORIZONTAL_BAR, ChartType.PIE, ChartType.DONUT}
    if chart_type in grouped_types:
        try:
            agg = df.groupby(x_key)[y_keys].sum().reset_index().head(20)
            data = agg.to_dict("records")
        except Exception:
            data = df[[x_key] + y_keys].head(20).to_dict("records")
    else:
        # Line / scatter / histogram: pass raw rows, truncated.
        data = df[[x_key] + y_keys].dropna().head(200).to_dict("records")

    return ChartConfig(
        id=str(uuid.uuid4()),
        chart_type=chart_type,
        title=f"{y_keys[0]} by {x_key}" if y_keys else f"{x_key} distribution",
        data=data,
        x_key=x_key,
        y_keys=y_keys,
        colors=["#6366f1"] * max(len(y_keys), 1),
        show_legend=True,
        show_grid=True,
        source_finding="user_chart_request",
    )
```

The function returns a `ChartConfig` — same dataclass that `run_visualization_agent` produces. Renderers in the frontend are agnostic to where the chart came from.

- [ ] **Step 3: Smoke-check the helper**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run python -c "
import pandas as pd
from agents.visualization_agent import build_single_chart
from orchestrator.state import TargetedParams, ChartType
df = pd.DataFrame({'region':['us','eu','asia']*4, 'revenue':[10,20,30,15,25,35,12,22,32,18,28,38]})
chart = build_single_chart(df, TargetedParams(chart_type=ChartType.BAR, group_by='region', metric_column='revenue'))
print(chart.chart_type, chart.x_key, chart.y_keys, len(chart.data))
"`
Expected: prints `BarChart region ['revenue'] 3` (or similar — confirms a valid ChartConfig with the aggregated 3 unique regions).

- [ ] **Step 4: Commit**

```bash
git add agents/visualization_agent.py
git commit -m "feat(visualization): build_single_chart helper for CHART_REQUEST intent"
```

---

### Task 1.4: Intent-handler module + META_QUESTION handler + tests

**Files:**
- Create: `agents/intent_handlers.py`
- Create: `tests/test_intent_handlers.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_intent_handlers.py`:

```python
from __future__ import annotations

import pandas as pd

from agents.intent_handlers import handle_meta_question, handle_export_request, handle_chart_request
from orchestrator.state import (
    AnalysisFindings,
    AnalysisSession,
    AnalysisTask,
    AnalysisType,
    ColumnInfo,
    ConnectorType,
    DataContract,
    InsightReport,
    QuestionIntent,
    TargetedParams,
    ChartType,
)


def _make_task(query: str, intent: QuestionIntent, params: TargetedParams | None = None) -> AnalysisTask:
    return AnalysisTask(
        raw_query=query,
        target_metrics=[],
        analysis_types=[],
        question_intent=intent,
        targeted_params=params or TargetedParams(),
    )


def _make_session_with_contract(rows: int = 100, cols: int = 5) -> AnalysisSession:
    s = AnalysisSession(session_id="s1")
    s.data_contract = DataContract(
        source_type=ConnectorType.CSV,
        row_count=rows,
        column_count=cols,
        columns=[
            ColumnInfo(name=f"c{i}", dtype="int64", nullable=False, sample_values=[1, 2, 3])
            for i in range(cols)
        ],
        sample_rows=[],
    )
    return s


def test_meta_question_shape_query():
    s = _make_session_with_contract(rows=100, cols=5)
    s.task_plan = _make_task("how many rows are there", QuestionIntent.META_QUESTION)
    resp = handle_meta_question(s)
    assert resp.intent == QuestionIntent.META_QUESTION
    assert "100" in resp.answer_text
    assert "5" in resp.answer_text


def test_meta_question_schema_query_returns_table():
    s = _make_session_with_contract(cols=3)
    s.task_plan = _make_task("what are the column names", QuestionIntent.META_QUESTION)
    resp = handle_meta_question(s)
    assert resp.table_columns == ["name", "dtype", "nullable"]
    assert len(resp.table_data) == 3
    assert resp.table_data[0]["name"] == "c0"


def test_meta_question_preview_without_findings():
    s = _make_session_with_contract()
    s.task_plan = _make_task("show me a preview of the data", QuestionIntent.META_QUESTION)
    resp = handle_meta_question(s)
    assert "Run an analysis first" in resp.answer_text


def test_export_request_without_insight_report():
    s = _make_session_with_contract()
    s.task_plan = _make_task("download as pptx", QuestionIntent.EXPORT_REQUEST)
    emitted: list[dict] = []
    resp = handle_export_request(s, emitted.append)
    assert resp.intent == QuestionIntent.EXPORT_REQUEST
    assert "run an analysis first" in resp.answer_text.lower()
    assert emitted == []


def test_export_request_unsupported_format():
    s = _make_session_with_contract()
    s.task_plan = _make_task("export this as a csv please", QuestionIntent.EXPORT_REQUEST)
    emitted: list[dict] = []
    resp = handle_export_request(s, emitted.append)
    assert "pptx" in resp.answer_text.lower()
    assert emitted == []


def test_export_request_pptx_emits_event():
    s = _make_session_with_contract()
    s.task_plan = _make_task("download the powerpoint", QuestionIntent.EXPORT_REQUEST)
    # Stub insight_report so the export gate passes.
    s.insight_report = InsightReport(
        executive_summary="ok",
        technical_findings=["ok"],
        recommended_actions=[],
        anomaly_alerts=[],
        proactive_questions=[],
        methodology_notes="ok",
    )
    emitted: list[dict] = []
    resp = handle_export_request(s, emitted.append)
    assert resp.answer_text.startswith("Generating")
    assert len(emitted) == 1
    assert emitted[0] == {
        "type": "export",
        "format": "pptx",
        "url": "/dashboard/latest/report.pptx",
    }


def test_chart_request_missing_params_returns_hint():
    s = _make_session_with_contract()
    s.task_plan = _make_task("chart", QuestionIntent.CHART_REQUEST, TargetedParams())
    resp = handle_chart_request(s, pd.DataFrame({"x": [1, 2], "y": [3, 4]}))
    assert "couldn't tell" in resp.answer_text.lower() or "couldn" in resp.answer_text.lower()
    assert resp.chart_configs == []


def test_chart_request_with_valid_params_returns_one_chart():
    df = pd.DataFrame({"region": ["us", "eu", "us"], "revenue": [10, 20, 15]})
    s = _make_session_with_contract()
    s.task_plan = _make_task(
        "bar chart of revenue by region",
        QuestionIntent.CHART_REQUEST,
        TargetedParams(chart_type=ChartType.BAR, group_by="region", metric_column="revenue"),
    )
    resp = handle_chart_request(s, df)
    assert len(resp.chart_configs) == 1
    assert resp.chart_configs[0].chart_type == ChartType.BAR
```

Some fields on `AnalysisFindings` and `InsightReport` may have additional required fields beyond what this test sets. If pydantic complains during Step 2 (the failure run), the test author should adjust to include the minimum required fields — these are deterministic schema issues, not behavior bugs.

- [ ] **Step 2: Confirm failure**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run pytest tests/test_intent_handlers.py -v`
Expected: `ImportError: cannot import name 'handle_meta_question' from 'agents.intent_handlers'` or equivalent — the module doesn't exist yet.

- [ ] **Step 3: Create the handler module**

Create `agents/intent_handlers.py`:

```python
"""Deterministic handlers for the three lightweight intents introduced in Gap 1
Spec 1: META_QUESTION, EXPORT_REQUEST, CHART_REQUEST. No LLM calls; all three
return a TargetedResponse synchronously.
"""
from __future__ import annotations

from typing import Callable

import pandas as pd

from orchestrator.state import (
    AnalysisSession,
    QuestionIntent,
    TargetedResponse,
)


# Emit signature mirrors graph._emit: the caller passes a one-arg callable that
# pushes onto the SSE event queue. Handlers that need to emit an extra event
# (only EXPORT_REQUEST today) take this callable as a parameter.
Emit = Callable[[dict], None]


def handle_meta_question(session: AnalysisSession) -> TargetedResponse:
    """Answer questions ABOUT the dataset (shape, schema, dtypes, preview)."""
    raw = (session.task_plan.raw_query if session.task_plan else "").lower()
    contract = session.data_contract

    if contract is None:
        return TargetedResponse(
            intent=QuestionIntent.META_QUESTION,
            answer_text="Connect a data source first — I don't know what data you're asking about.",
        )

    if any(kw in raw for kw in ("preview", "first rows", "first 5", "sample row", "head")):
        findings = session.analysis_findings
        if findings and findings.head_preview:
            return TargetedResponse(
                intent=QuestionIntent.META_QUESTION,
                answer_text=f"Here are the first {len(findings.head_preview)} rows.",
                table_data=findings.head_preview,
                table_columns=list(findings.head_preview[0].keys()) if findings.head_preview else [],
            )
        return TargetedResponse(
            intent=QuestionIntent.META_QUESTION,
            answer_text="Run an analysis first to see a preview of the rows.",
        )

    if any(kw in raw for kw in ("dtype", "data type", "data types")):
        rows = [
            {"name": c.name, "dtype": c.dtype, "nullable": c.nullable}
            for c in contract.columns
        ]
        return TargetedResponse(
            intent=QuestionIntent.META_QUESTION,
            answer_text=f"Column types for the {len(rows)} columns:",
            table_columns=["name", "dtype", "nullable"],
            table_data=rows,
        )

    if any(kw in raw for kw in ("column name", "what columns", "schema", "fields")):
        rows = [
            {"name": c.name, "dtype": c.dtype, "nullable": c.nullable}
            for c in contract.columns
        ]
        return TargetedResponse(
            intent=QuestionIntent.META_QUESTION,
            answer_text=f"The dataset has {len(rows)} columns.",
            table_columns=["name", "dtype", "nullable"],
            table_data=rows,
        )

    # Default / "how many rows" / "shape" / "size"
    return TargetedResponse(
        intent=QuestionIntent.META_QUESTION,
        answer_text=(
            f"This dataset has {contract.row_count:,} rows × "
            f"{contract.column_count} columns."
        ),
    )


def handle_export_request(session: AnalysisSession, emit: Emit) -> TargetedResponse:
    """Trigger PPTX export, or explain why we can't."""
    raw = (session.task_plan.raw_query if session.task_plan else "").lower()
    is_pptx = any(kw in raw for kw in ("pptx", "powerpoint", "ppt", "slide", "deck"))

    if not is_pptx:
        return TargetedResponse(
            intent=QuestionIntent.EXPORT_REQUEST,
            answer_text="Only PowerPoint (PPTX) export is supported right now.",
        )

    if not session.insight_report:
        return TargetedResponse(
            intent=QuestionIntent.EXPORT_REQUEST,
            answer_text="Nothing to export — run an analysis first.",
        )

    emit({
        "type": "export",
        "format": "pptx",
        "url": "/dashboard/latest/report.pptx",
    })
    return TargetedResponse(
        intent=QuestionIntent.EXPORT_REQUEST,
        answer_text="Generating the PPTX report. The download will start in a moment.",
    )


def handle_chart_request(session: AnalysisSession, df: pd.DataFrame) -> TargetedResponse:
    """Build a single chart from CHART_REQUEST params."""
    from agents.visualization_agent import build_single_chart

    if session.task_plan is None:
        return TargetedResponse(
            intent=QuestionIntent.CHART_REQUEST,
            answer_text="I couldn't tell which chart to make.",
        )

    p = session.task_plan.targeted_params
    if not p.chart_type or not (p.target_columns or p.metric_column):
        return TargetedResponse(
            intent=QuestionIntent.CHART_REQUEST,
            answer_text=(
                'I couldn\'t tell which columns to chart. '
                'Try: "bar chart of revenue by region".'
            ),
        )

    chart = build_single_chart(df, p)
    return TargetedResponse(
        intent=QuestionIntent.CHART_REQUEST,
        answer_text=f"Here's the {chart.chart_type} you asked for.",
        chart_configs=[chart],
    )
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run pytest tests/test_intent_handlers.py -v`
Expected: 8 passed. If any test fails because `AnalysisFindings` or `InsightReport` has required fields beyond what the test stubs, the implementer adjusts the test stubs (those are constructor concerns, not handler bugs).

- [ ] **Step 5: Run all tests**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run pytest tests/ -v`
Expected: All tests pass (Gap 6: 11 + Task 1.1: 6 + Task 1.4: 8 = 25).

- [ ] **Step 6: Commit**

```bash
git add agents/intent_handlers.py tests/test_intent_handlers.py
git commit -m "feat(intent_handlers): META/EXPORT/CHART deterministic handlers + tests"
```

---

### Task 1.5: Wire dispatch into `targeted_analysis_node`

**Files:**
- Modify: `orchestrator/graph.py`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Read the current `targeted_analysis_node`**

`targeted_analysis_node` is around line 436 in `orchestrator/graph.py`. The relevant block calls `run_targeted_analysis(session)` and emits `targeted_response`. Read the entire function (lines ~436–490) before editing so you see the SSE emit pattern, error handling, session_store.save, and `done` emit.

- [ ] **Step 2: Add import + dispatch**

At the top of `orchestrator/graph.py`, alongside the existing `from agents.targeted_analysis_agent import run_targeted_analysis`, add:

```python
from agents.intent_handlers import (
    handle_chart_request,
    handle_export_request,
    handle_meta_question,
)
```

Inside `targeted_analysis_node`, replace the single `response = run_targeted_analysis(session)` call with a dispatch. Find the line:

```python
        response = run_targeted_analysis(session)
```

Replace with:

```python
        intent = session.task_plan.question_intent if session.task_plan else None
        if intent == QuestionIntent.META_QUESTION:
            response = handle_meta_question(session)
        elif intent == QuestionIntent.EXPORT_REQUEST:
            response = handle_export_request(session, lambda ev: _emit(state, ev))
        elif intent == QuestionIntent.CHART_REQUEST:
            df_bytes = state.get("df_parquet")
            import io, pandas as pd
            df = pd.read_parquet(io.BytesIO(df_bytes)) if df_bytes else pd.DataFrame()
            response = handle_chart_request(session, df)
        else:
            response = run_targeted_analysis(session)
```

`QuestionIntent` needs to be importable at the top of `graph.py`. Check the existing imports — `MessageRole` is already imported from `orchestrator.state`. Add `QuestionIntent` to that same import line if not already present.

- [ ] **Step 3: Update CLAUDE.md SSE protocol list**

Open `CLAUDE.md`. Find the existing "SSE Event Protocol" section (around the line that starts "The `/chat` endpoint streams these event types in order"). Add one new line in the event list:

```
export            → {type: "export", format: "pptx", url: "/dashboard/latest/report.pptx"}
```

Place it between `charts` and `insight` for ordering consistency.

- [ ] **Step 4: Smoke-check the graph still compiles**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run python -c "from orchestrator.graph import build_graph; g = build_graph(); print('graph ok')"`
Expected: `graph ok`.

- [ ] **Step 5: Run all tests**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run pytest tests/ -v`
Expected: 25 passed (same as Task 1.4 end state — no new tests in this task).

- [ ] **Step 6: Commit**

```bash
git add orchestrator/graph.py CLAUDE.md
git commit -m "feat(graph): dispatch META/EXPORT/CHART in targeted_analysis_node + doc SSE event"
```

---

### Phase 1 verification gate

Before starting Phase 2, confirm:

- `uv run pytest tests/ -v` → 25 passed.
- Backend boots clean (`uv run uvicorn api.main:app --port 8765` — expect only the PG/Qdrant fallback warnings).
- Manual end-to-end on a session that has a populated `data_contract` (after at least one OVERVIEW run, or by patching one in via the in-memory store):
  - Hit `/chat` with `"how many rows do we have?"` → SSE stream contains a `targeted_response` event whose `answer_text` includes the row count. No `briefing`, no `charts`, no `insight` events.
  - Hit `/chat` with `"download as pptx"` after a completed analysis → SSE stream contains both an `export` event AND a `targeted_response` event.
  - Hit `/chat` with `"bar chart of <colA> by <colB>"` → `targeted_response.data.chart_configs` has exactly one chart with `chart_type == "BarChart"`.
- For a session WITHOUT a prior analysis: `"download as pptx"` returns the polite "Nothing to export — run an analysis first."

---

## Phase 2 — DRILL_DOWN node + OVERVIEW-on-completed gate

Backend-only changes. Compact follow-ups (`"why is X happening?"`) now route to a new `drill_down_node` with one Claude call, and re-asking for an `OVERVIEW` on a completed session triggers an approval modal.

### Task 2.1: `drill_down_agent.py`

**Files:**
- Create: `agents/drill_down_agent.py`

- [ ] **Step 1: Inspect the existing intake_agent for patterns**

Run: `grep -nE "AsyncAnthropic|messages.create|response\.|json\.loads|model=" /Users/uvnikhil/Desktop/AnalystAI/agents/intake_agent.py | head -15`

Note the Anthropic SDK call pattern, response-parsing convention (strip markdown fences, parse JSON, `model_validate_json`), and the system-prompt + user-prompt construction.

- [ ] **Step 2: Create `agents/drill_down_agent.py`**

```python
"""Drill-down agent — compact follow-up using prior analysis context.

Used by drill_down_node when QuestionIntent == DRILL_DOWN. Calls Claude once
with the prior insight_report + data summary + the new question, and parses a
small JSON response into a TargetedResponse.
"""
from __future__ import annotations

import json
import re
import uuid

from anthropic import AsyncAnthropic

from api.config import settings
from orchestrator.state import (
    AnalysisSession,
    ChartConfig,
    ChartType,
    QuestionIntent,
    TargetedResponse,
)


_SYSTEM = """\
You are a senior data analyst answering a follow-up question.

The user has already received an analysis with executive summary, technical
findings, and anomaly alerts (provided to you below). They are now asking a
specific drill-down question. Your job is to answer it directly in 2-3 sentences,
referencing the prior findings when relevant.

If a chart would help, suggest ONE chart spec. Otherwise, leave chart_spec null.

Respond with ONLY a JSON object (no markdown fences, no extra text):

{
  "answer_text": "<2-3 sentence answer>",
  "chart_spec": null OR {
    "chart_type": "BarChart" | "LineChart" | "ScatterChart" | "PieChart" | "HistogramChart",
    "x_column": "<column name>",
    "y_column": "<column name>"
  }
}
"""


def _strip_fences(text: str) -> str:
    """Remove ```json ... ``` wrappers if the model added them despite instructions."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    return text


async def run_drill_down_agent(
    session: AnalysisSession,
    df_parquet: bytes | None,
) -> TargetedResponse:
    """Single Claude call. Returns a fully-formed TargetedResponse."""
    if not session.insight_report:
        return TargetedResponse(
            intent=QuestionIntent.DRILL_DOWN,
            answer_text="Drill-down requires a prior analysis. Ask for an overview first.",
        )

    report = session.insight_report
    query = session.task_plan.raw_query if session.task_plan else ""

    # Compact data summary — keep tokens bounded.
    contract = session.data_contract
    column_names = [c.name for c in contract.columns] if contract else []
    column_dtypes = (
        ", ".join(f"{c.name}:{c.dtype}" for c in contract.columns[:30])
        if contract else "<no contract>"
    )
    findings = session.analysis_findings
    eda_summary = json.dumps(findings.eda_summary, default=str)[:1500] if findings else ""

    user_prompt = f"""\
Prior analysis:
- Executive summary: {report.executive_summary}
- Technical findings: {report.technical_findings[:5]}
- Anomaly alerts: {[a.title for a in report.anomaly_alerts][:5]}

Dataset:
- Rows: {contract.row_count if contract else "?"}
- Columns ({len(column_names)}): {column_dtypes}
- EDA stats (truncated): {eda_summary}

Follow-up question: {query}

Answer in JSON as specified.
"""

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    msg = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw_text = "".join(b.text for b in msg.content if hasattr(b, "text"))
    payload = json.loads(_strip_fences(raw_text))

    answer = payload.get("answer_text", "").strip()
    chart_configs: list[ChartConfig] = []
    spec = payload.get("chart_spec")
    if spec and isinstance(spec, dict):
        chart_type_str = spec.get("chart_type")
        x = spec.get("x_column")
        y = spec.get("y_column")
        # Only build the chart if both columns exist in the contract.
        if chart_type_str and x in column_names and y in column_names:
            try:
                chart_type = ChartType(chart_type_str)
            except ValueError:
                chart_type = ChartType.BAR
            # Lightweight chart from EDA — no pandas join here, we hand a stub
            # ChartConfig that the frontend renderer can display. If a richer
            # plot is needed, the user can switch to CHART_REQUEST.
            chart_configs.append(ChartConfig(
                id=str(uuid.uuid4()),
                chart_type=chart_type,
                title=f"{y} vs {x}",
                data=[],   # frontend renders an empty-state placeholder for now
                x_key=x,
                y_keys=[y],
                colors=["#6366f1"],
                show_legend=True,
                show_grid=True,
                source_finding="drill_down",
            ))

    return TargetedResponse(
        intent=QuestionIntent.DRILL_DOWN,
        answer_text=answer or "I couldn't formulate a direct answer to that.",
        chart_configs=chart_configs,
    )
```

The empty-data chart from drill-down is intentional and matches the spec's "optional chart spec" framing — we surface the LLM's chart suggestion without re-loading and aggregating the DataFrame at this layer. Users who want a richer chart fall through to CHART_REQUEST. (If you want to change this later — to load `df_parquet` and aggregate — that's a follow-up.)

- [ ] **Step 3: Smoke-check the import path**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run python -c "from agents.drill_down_agent import run_drill_down_agent; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add agents/drill_down_agent.py
git commit -m "feat(drill_down): new agent — LLM follow-up with prior insight context"
```

---

### Task 2.2: `drill_down_node` + routing wiring

**Files:**
- Modify: `orchestrator/graph.py`
- Modify: `orchestrator/routing.py`

- [ ] **Step 1: Add `drill_down_node` to `orchestrator/graph.py`**

At the top of `graph.py`, add to the existing agent imports:

```python
from agents.drill_down_agent import run_drill_down_agent
```

Then add a new node function next to `targeted_analysis_node` (e.g. just above or below it):

```python
async def drill_down_node(state: AgentState) -> dict:
    """DRILL_DOWN: compact follow-up that reuses prior insight context.
    Mirrors targeted_analysis_node's SSE + persistence pattern; no dashboard rebuild.
    """
    _emit(state, {"type": "agent_start", "agent": "drill_down", "message": "Investigating..."})
    session: AnalysisSession = state["session"]

    if not session.insight_report:
        session.error_message = "Drill-down requires a prior analysis. Ask for an overview first."
        return {"session": session}

    if state.get("df_parquet"):
        session.__dict__["_df_parquet"] = state["df_parquet"]

    try:
        response = await run_drill_down_agent(session, state.get("df_parquet"))
        session.targeted_response = response
        _emit(state, {"type": "targeted_response", "data": response.model_dump()})
        _emit(state, {"type": "agent_complete", "agent": "drill_down", "message": "Answer ready"})

        if response.answer_text:
            session.add_message(MessageRole.ASSISTANT, response.answer_text)
        session.iteration += 1
    except Exception as exc:
        session.error_message = f"Drill-down failed: {exc}"

    session.__dict__.pop("_df_parquet", None)

    ss = state.get("session_store")
    if ss:
        try:
            await ss.save(session)
        except Exception:
            pass

    _emit(state, {"type": "done"})
    return {"session": session}
```

- [ ] **Step 2: Register the node + edge in `build_graph()`**

In `build_graph()`, after the existing `graph.add_node("targeted_analysis", targeted_analysis_node)` line, add:

```python
    graph.add_node("drill_down", drill_down_node)
```

In the `route_after_ingestion` mapping dict (`graph.add_conditional_edges("ingestion", route_after_ingestion, { ... })`), add:

```python
        "drill_down": "drill_down",
```

In the `route_after_intake` mapping dict (`graph.add_conditional_edges("intake", route_after_intake, { ... })`), add:

```python
        "drill_down": "drill_down",
```

Finally, add the terminal edge:

```python
    graph.add_edge("drill_down", END)
```

- [ ] **Step 3: Update `route_after_intake` in `orchestrator/routing.py`**

The current `route_after_intake` returns `"cleaning"` for full-pipeline intents and `"targeted_analysis"` for everything else. Insert a DRILL_DOWN check ABOVE the `_is_targeted` branch:

```python
def route_after_intake(state) -> str:
    session = state["session"]
    task = session.task_plan
    if not task:
        return "error"
    if session.error_message:
        return "error"
    if task.clarification_needed:
        return "human_approval"
    if task.question_intent == QuestionIntent.DRILL_DOWN:
        return "drill_down"
    if _is_targeted(task):
        return "targeted_analysis"
    return "cleaning"
```

The exact shape may differ from what's in the file — preserve the existing structure (error checks, clarification check) and just insert the DRILL_DOWN line at the right position. Read the existing `route_after_intake` first to confirm.

- [ ] **Step 4: Update `route_after_ingestion` in `orchestrator/routing.py`**

The current `route_after_ingestion` has a branch for resume (`task.resume_from == "analysis"`) that returns `"targeted_analysis"` for any non-full-pipeline intent. Add the DRILL_DOWN case there too:

```python
def route_after_ingestion(state) -> str:
    session = state["session"]
    if session.error_message:
        return "error"
    task = session.task_plan
    if task and task.resume_from == "analysis":
        # Multi-turn resume: skip intake. Pick the right terminal node by intent.
        if task.question_intent == QuestionIntent.DRILL_DOWN:
            return "drill_down"
        if task.question_intent in _FULL_PIPELINE_INTENTS:
            return "analysis"
        return "targeted_analysis"
    return "intake"
```

Again, preserve the file's existing structure; only insert the DRILL_DOWN branch.

- [ ] **Step 5: Smoke-check graph compiles**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run python -c "from orchestrator.graph import build_graph; g = build_graph(); print('graph ok')"`
Expected: `graph ok`.

- [ ] **Step 6: Run all tests**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run pytest tests/ -v`
Expected: 25 passed (no new tests in this task — verification is via manual SSE smoke at the phase gate).

- [ ] **Step 7: Commit**

```bash
git add orchestrator/graph.py orchestrator/routing.py
git commit -m "feat(graph): drill_down_node + DRILL_DOWN routing branches"
```

---

### Task 2.3: `ChatRequest.approval_context` + chat-route reset

**Files:**
- Modify: `api/routes/chat.py`

- [ ] **Step 1: Extend `ChatRequest`**

In `api/routes/chat.py`, find the `class ChatRequest(BaseModel)` declaration. Add one new optional field:

```python
    approval_context: str | None = Field(default=None, max_length=64)
```

The existing fields (`message`, `session_id`, `user_id`, `is_approval`, `connector_config`, `file_hash`, `dataset_name`) stay unchanged.

- [ ] **Step 2: Reset session state on rerun_overview approval**

In the `chat()` handler body, find the existing block that loads or creates a session. After the session is loaded (existing session path) but BEFORE the graph state is built, insert:

```python
    if session is not None and body.is_approval and body.approval_context == "rerun_overview":
        # User chose "Run fresh" on the rerun-overview modal. Reset the
        # session so the graph runs a clean OVERVIEW from intake forward.
        session.overview_done = False
        session.status = "in_progress"
        session.task_plan = None
```

This block runs only when (a) we have an existing session loaded, (b) the request is flagged as an approval response, and (c) the context tag matches. For brand-new sessions or for other approval flows (clarification, high_null), nothing happens.

- [ ] **Step 3: Smoke-check imports**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run python -c "from api.routes.chat import ChatRequest; r = ChatRequest(message='hi', is_approval=True, approval_context='rerun_overview'); print(r.approval_context)"`
Expected: `rerun_overview`

- [ ] **Step 4: Run all tests**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run pytest tests/ -v`
Expected: 25 passed.

- [ ] **Step 5: Commit**

```bash
git add api/routes/chat.py
git commit -m "feat(chat): accept approval_context; reset session on rerun_overview"
```

---

### Task 2.4: `intake_node` rerun_overview detection

**Files:**
- Modify: `orchestrator/graph.py`

- [ ] **Step 1: Read the current `intake_node`**

`intake_node` is around line 76 of `orchestrator/graph.py`. The relevant block is the `if task.clarification_needed: _emit(...) else: _emit(agent_complete)` pair after `run_intake_agent` returns. Confirm this is the structure before editing.

- [ ] **Step 2: Restructure as `if / elif / else`**

Replace the existing two-branch block:

```python
        if task.clarification_needed:
            _emit(state, {
                "type": "approval_required",
                "context": "clarification",
                "question": task.clarification_question,
            })
        else:
            _emit(state, {"type": "agent_complete", "agent": "intake", "message": "Analysis plan ready"})
```

with the three-branch version:

```python
        if task.clarification_needed:
            _emit(state, {
                "type": "approval_required",
                "context": "clarification",
                "question": task.clarification_question,
            })
        elif (task.question_intent == QuestionIntent.OVERVIEW
              and session.overview_done
              and session.status == "completed"
              and not state.get("is_approval")):
            _emit(state, {
                "type": "approval_required",
                "context": "rerun_overview",
                "question": (
                    "You already have a complete analysis for this dataset. "
                    "Load the previous one, or run fresh?"
                ),
            })
            task.clarification_needed = True   # routes to human_approval via existing route_after_intake
        else:
            _emit(state, {"type": "agent_complete", "agent": "intake", "message": "Analysis plan ready"})
```

`QuestionIntent` must already be imported at the top of `graph.py` from Task 1.5 — verify with a quick grep:

Run: `grep -n "QuestionIntent" /Users/uvnikhil/Desktop/AnalystAI/orchestrator/graph.py | head -3`
Expected: at least one import line referencing `QuestionIntent`. If missing, add it to the existing `from orchestrator.state import …` line.

- [ ] **Step 3: Smoke-check the graph still compiles**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run python -c "from orchestrator.graph import build_graph; g = build_graph(); print('graph ok')"`
Expected: `graph ok`.

- [ ] **Step 4: Run all tests**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run pytest tests/ -v`
Expected: 25 passed.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/graph.py
git commit -m "feat(intake): detect OVERVIEW on completed session, emit rerun_overview gate"
```

---

### Phase 2 verification gate

Before starting Phase 3, confirm:

- `uv run pytest tests/ -v` → 25 passed.
- Backend boots clean.
- Manual smoke (requires real Anthropic key + a session with a completed analysis):
  - Ask `"why is X happening?"` on a completed session → SSE log shows `agent_start: drill_down`, `targeted_response`, `done` events. NO `briefing`, NO `charts`, NO `insight`, NO `expert_review`.
  - Ask DRILL_DOWN on a session with no `insight_report` → graceful error: "Drill-down requires a prior analysis."
  - Ask `"analyze this data"` on a completed session → SSE stream contains `approval_required` with `context: "rerun_overview"`, then `done`. No further pipeline runs.
  - Re-POST `/chat` with `is_approval=true, approval_context="rerun_overview"` and the same message → full OVERVIEW pipeline runs end to end and a fresh `insight_report` replaces the old one.

---

## Phase 3 — Frontend wiring

User-facing. Adds the export download handler, the `approvalContext` param on `sendMessage`, the rerun_overview branch in `ApprovalModal`, and the three callbacks in `ChatInterface`.

> **Frontend reminder.** Per [frontend/AGENTS.md](../../../frontend/AGENTS.md), this is not the Next.js you know — APIs may differ from training data. Stay within existing patterns: read each file end-to-end before editing.

### Task 3.1: Extend `AgentEvent` discriminated union

**Files:**
- Modify: `frontend/src/types/index.ts`

- [ ] **Step 1: Locate the existing `AgentEvent` definition**

Read `frontend/src/types/index.ts` and find the `export type AgentEvent` union (or `export interface AgentEvent` discriminated set). It currently lists variants like `session_id`, `agent_start`, `agent_complete`, `briefing`, `charts`, `insight`, `targeted_response`, `expert_review`, `approval_required`, `suggested_questions`, `quality_retry`, `error`, `done`.

- [ ] **Step 2: Add the `export` variant**

Append a new variant to the union (matching the existing convention — discriminated by `type`):

```ts
| { type: "export"; format: string; url: string }
```

Find the exact location and syntax by reading the file. Match the existing style (intersection-with-discriminator vs separate interfaces).

- [ ] **Step 3: Verify the frontend compiles**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/index.ts
git commit -m "feat(types): AgentEvent gains 'export' variant"
```

---

### Task 3.2: `useAnalysis` — `approvalContext` param + export handler

**Files:**
- Modify: `frontend/src/hooks/useAnalysis.ts`

- [ ] **Step 1: Read the current hook**

Read `frontend/src/hooks/useAnalysis.ts` end-to-end. Note:
- The `sendMessage` signature (currently `(content: string, isApproval = false)`).
- The `fetch(${API_URL}/chat, { body: JSON.stringify({...}) })` body shape (already has `message`, `session_id`, `is_approval`, `connector_config`, `file_hash`, `dataset_name` from Gap 6).
- The `for await (event of parseSSEStream(res))` switch — where new event types get added.

- [ ] **Step 2: Add the `approvalContext` parameter**

Find the existing `sendMessage = useCallback(async (content: string, isApproval = false) => { ... }` declaration. Widen the signature:

```ts
const sendMessage = useCallback(
  async (content: string, isApproval = false, approvalContext: string | null = null) => {
```

Inside `sendMessage`, in the `body: JSON.stringify({...})` block, add `approval_context: approvalContext` to the body:

```ts
body: JSON.stringify({
  message: content,
  session_id: sessionId,
  is_approval: isApproval,
  approval_context: approvalContext,
  connector_config: connectorConfig.current,
  file_hash: fileHashRef.current,
  dataset_name: datasetNameRef.current,
}),
```

The `useCallback` dependency array currently includes `[sessionId]`. Leave it as-is — `approvalContext` is a parameter, not state, so it doesn't need to be in the deps.

- [ ] **Step 3: Add the `export` event handler**

In the `switch (event.type)` block inside `parseSSEStream`, add a new case. Place it adjacent to the existing `case "charts"` for readability:

```ts
case "export":
  if (event.format === "pptx") {
    void downloadLatestReportPptx();
  }
  break;
```

`downloadLatestReportPptx` must be importable from `@/lib/api`. Check the existing top-of-file imports — if not already imported, add it to the existing `import { ..., API_URL } from "@/lib/api"` line.

- [ ] **Step 4: Verify the frontend compiles**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useAnalysis.ts
git commit -m "feat(useAnalysis): sendMessage approvalContext + export SSE handler"
```

---

### Task 3.3: `ApprovalModal` rerun_overview branch

**Files:**
- Modify: `frontend/src/components/ApprovalModal.tsx`

- [ ] **Step 1: Read the existing modal**

Read `frontend/src/components/ApprovalModal.tsx` end-to-end. Note:
- The component's props (likely `pendingApproval` + handler callbacks).
- The current rendering for `clarification` (free-text input) and `high_null` (response).
- Where the response is submitted back through `sendMessage`.

- [ ] **Step 2: Add the three new optional callback props**

Widen the props interface:

```ts
interface ApprovalModalProps {
  pendingApproval: ApprovalRequiredEvent;
  // ...existing required callbacks...
  onLoadPrevious?: () => void;
  onRunFresh?: () => void;
  onCancel?: () => void;
}
```

If the file uses a destructured Props type inline rather than a named interface, follow the existing convention.

- [ ] **Step 3: Add the rerun_overview branch in the JSX**

Inside the modal body, branch on `pendingApproval.context`. The existing clarification + high_null forms stay; add a new condition for `rerun_overview` that renders three buttons:

```tsx
{pendingApproval.context === "rerun_overview" ? (
  <div className="flex gap-2 justify-end">
    <button
      type="button"
      onClick={onCancel}
      className="px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded"
    >
      Cancel
    </button>
    <button
      type="button"
      onClick={onLoadPrevious}
      className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
    >
      Load previous
    </button>
    <button
      type="button"
      onClick={onRunFresh}
      className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700"
    >
      Run fresh
    </button>
  </div>
) : (
  /* existing clarification / high_null free-text response form stays here */
)}
```

Match the surrounding styling (background, padding) to whatever the existing modal uses. The three buttons are the entire body for this context; no text input.

- [ ] **Step 4: Verify the frontend compiles**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ApprovalModal.tsx
git commit -m "feat(approval-modal): rerun_overview context renders three buttons"
```

---

### Task 3.4: `ChatInterface` — wire the three callbacks

**Files:**
- Modify: `frontend/src/components/ChatInterface.tsx`

- [ ] **Step 1: Read the current `ChatInterface.tsx`**

Read the file end-to-end. Find:
- The `useAnalysis()` destructure (`sessionId`, `messages`, `hydrateFromSnapshot`, etc.).
- The `<ApprovalModal>` render and what props it currently receives.
- The `getSessionResults` import (added in Gap 6).

- [ ] **Step 2: Add the three handlers**

In the component body, alongside the existing handlers (`handleLoadSession`, `handleNewAnalysis`, `handleDeleteSession`):

```ts
const handleLoadPrevious = async () => {
  if (sessionId) {
    const snapshot = await getSessionResults(sessionId);
    if (snapshot) hydrateFromSnapshot(snapshot);
  }
  setPendingApproval(null);
};

const handleRunFresh = () => {
  const lastUserMessage =
    [...messages].reverse().find((m) => m.role === "user")?.content ?? "";
  setPendingApproval(null);
  void sendMessage(lastUserMessage, true, "rerun_overview");
};

const handleCancelApproval = () => {
  setPendingApproval(null);
};
```

These rely on `sendMessage` accepting the third positional argument (`approvalContext`) added in Task 3.2. Verify your edit there is in place by re-running `npm run build`.

The `sendMessage`, `setPendingApproval`, `hydrateFromSnapshot`, `getSessionResults` need to be in scope. The first three should already be destructured from `useAnalysis()` from prior phases; if any are missing, add them to the destructure. `getSessionResults` is an import from `@/lib/api` already added in Gap 6.

- [ ] **Step 3: Pass the new callbacks to `<ApprovalModal>`**

Find the `<ApprovalModal ... />` render. Add three new props (preserve any existing props passed to the modal):

```tsx
<ApprovalModal
  /* ...existing props... */
  onLoadPrevious={handleLoadPrevious}
  onRunFresh={handleRunFresh}
  onCancel={handleCancelApproval}
/>
```

- [ ] **Step 4: Verify the frontend compiles**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ChatInterface.tsx
git commit -m "feat(chat-interface): wire rerun_overview modal callbacks"
```

---

### Phase 3 verification gate

End-to-end smoke matrix to run before declaring the feature complete:

| Action | Expected |
|---|---|
| Connect a CSV, run an OVERVIEW analysis, then ask `"how many columns?"` | Text reply only, no dashboard rebuild |
| Same session, ask `"download as pptx"` | PPTX file downloads (browser save dialog) |
| Same session, ask `"show me a scatter of <colA> vs <colB>"` | One chart renders in chat — no full dashboard |
| Same session, ask `"why is <some finding> happening?"` | Compact answer + optional chart, no dashboard rebuild, log shows `agent_start: drill_down` |
| Same session, ask `"analyze this data"` (or `"give me an overview"`) | Approval modal appears with Cancel / Load previous / Run fresh buttons |
| Click Load previous | UI re-hydrates with existing analysis; no `/chat` request in Network tab |
| Click Run fresh | New SSE run; fresh `insight_report` replaces the prior one |
| Click Cancel | Modal closes; workspace unchanged |
| Brand-new session, ask `"download as pptx"` BEFORE running an analysis | Polite text: "Nothing to export — run an analysis first." |
| Brand-new session, ask `"why is X happening?"` BEFORE running an analysis | Polite error: "Drill-down requires a prior analysis." |
| Persisted session previously saved with `question_intent: "deep_dive"` (force one via REPL by setting `session.task_plan.question_intent = QuestionIntent("drill_down")`, save, reload) | Loads without error; intent is `DRILL_DOWN` |

---

## Spec coverage check

- Spec §3 (Schema Changes) → Task 1.1.
- Spec §4 (Intake Prompt + Routing) → Tasks 1.2, 2.2.
- Spec §5 (New Intent Handlers) → Tasks 1.3, 1.4, 1.5, 2.1, 2.2.
- Spec §6 (OVERVIEW-on-Completed Approval Gate) → Tasks 2.3, 2.4.
- Spec §7 (Frontend) → Tasks 3.1, 3.2, 3.3, 3.4.
- Spec §8 (Phasing & Verification) → Phase verification gates after Tasks 1.5, 2.4, 3.4.
- Spec §1 (Non-goals) — FILTER_REQUEST deferred, PDF/CSV export deferred, no pre-intake classifier — explicitly preserved as non-goals; no tasks created for them.
