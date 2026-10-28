from __future__ import annotations

import pandas as pd

from agents.intent_handlers import handle_meta_question, handle_export_request, handle_chart_request
from orchestrator.state import (
    AnalysisSession,
    AnalysisTask,
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
        date_columns=[],
        id_columns=[],
        metric_columns=[],
        schema_summary="test dataset",
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
    assert "couldn" in resp.answer_text.lower()
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


def test_targeted_answers_name_the_group_the_user_asked_about():
    """E2E regression (e2e-06..08, 12..14): "segment B" was answered with the top and bottom
    segments, because TargetedParams had nowhere to put "B"."""
    from agents.targeted_analysis_agent import handle_segment_compare, handle_time_series
    from connectors.csv_connector import CSVConnector
    from evals import datagen
    from orchestrator.state import ConnectorConfig, ConnectorType
    df, truth = datagen.make_sales(1)
    contract = CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df)

    seg = handle_segment_compare(df, TargetedParams(metric_column="revenue", segment_column="segment",
                                                    segment_value="b"), contract)
    assert f"{truth['mean_revenue_by_segment']['B']:,.2f}" in seg.answer_text

    ts = handle_time_series(df, TargetedParams(metric_column="revenue", date_column="order_date",
                                               segment_column="segment", segment_value="B"), contract)
    assert f"{truth['segB_mom_pct']:+.1f}%" in ts.answer_text      # -12.0%


def test_long_time_series_chart_builds_on_pandas_3():
    """'Q'/'M' resample aliases raise on pandas 3; the 2-year+ chart used to vanish."""
    from agents.visualization_agent import _build_time_series
    df = pd.DataFrame({"d": pd.date_range("2022-01-01", periods=1000, freq="D"), "v": range(1000)})
    chart = _build_time_series(df, "d", "v")
    assert chart is not None and len(chart.data) >= 10
