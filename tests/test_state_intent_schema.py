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
        "analysis_types": [],
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
