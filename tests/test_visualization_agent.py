"""Visualization agent (Phase 5): fault tests (contract step 5), determinism, CI arithmetic and a
seeded property test. Every chart that leaves the backend lints clean and carries a rationale."""
import io
import json

import numpy as np
import pandas as pd
import pytest

from agents import visualization_agent as viz
from evals import datagen
from evals.datagen import INJECTION
from evals.run import dataset, load_cases, viz_session
from guardrails import chart_lint
from orchestrator.state import (
    AnalysisSession,
    ChartConfig,
    ChartType,
    InsightReport,
    TargetedParams,
)


def _clean(charts, ctx):
    for c in charts:
        assert c.rationale is not None, c.title
        outcome = viz.pick_outcome([c.x_key, *c.y_keys], ctx)[0] \
            if c.chart_type == ChartType.SCATTER else None
        assert chart_lint(c.model_dump(mode="json"), ctx.measures, outcome) == [], c.title


def _case(cid):
    return next(c for c in load_cases("visualization", cid))


async def test_two_runs_are_identical_apart_from_ids():
    case = _case("viz-v14")
    df = dataset(case)[0]
    runs = []
    for _ in range(2):
        session, _ = viz_session(case, df)
        runs.append([{**c.model_dump(mode="json"), "id": None}
                     for c in await viz.run_visualization_agent(session)])
    assert runs[0] == runs[1] and runs[0]


async def test_no_frame_falls_back_to_a_labelled_contract_chart():
    case = _case("viz-v16")
    session, _ = viz_session(case, dataset(case)[0])
    session.__dict__.pop("_df_parquet")
    charts = await viz.run_visualization_agent(session)
    assert charts and all(c.rationale and c.placement == "data" for c in charts)


def test_all_null_metric_gives_no_chart_and_no_crash():
    df = pd.DataFrame({"segment": list("ABAB"), "revenue": [None] * 4})
    ctx = viz.build_context(df)
    assert viz.group_bars(df, "segment", "revenue", "mean", ctx) is None


def test_agent_pie_with_too_many_slices_is_repaired_to_bars(tmp_path):
    df = pd.DataFrame({"cat": [f"c{i}" for i in range(7)] * 3})
    ctx = viz.build_context(df)
    pie = ChartConfig(id="p", chart_type=ChartType.DONUT, title="t", x_key="name", y_keys=["value"],
                      data=[{"name": f"c{i}", "value": 3} for i in range(7)], source_finding="x")
    viz._why(pie, ctx, "profile:row_count", "r", "cat", "count", "count", 21)
    out = viz.finalize_charts([pie], ctx)
    assert out[0].chart_type == ChartType.HORIZONTAL_BAR and out[0].x_label and out[0].y_label
    _clean(out, ctx)


def test_user_pie_is_honoured_with_a_note():
    df, _ = datagen.make_sales(1)
    chart = viz.build_single_chart(df, TargetedParams(chart_type=ChartType.PIE, group_by="region",
                                                      metric_column="discount"))
    assert chart.chart_type == ChartType.PIE
    assert chart.rationale.aggregation == "mean"            # a rate is averaged, never summed
    assert any("parts of a whole" in w for w in chart.lint_warnings)


def test_fifteen_series_are_capped_at_eight():
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"d": pd.date_range("2025-01-01", periods=600, freq="D").astype(str),
                       "g": rng.choice([f"g{i}" for i in range(15)], 600),
                       "v": rng.normal(10, 1, 600)})
    ctx = viz.build_context(df)
    chart = viz.month_line(df, "d", "v", ctx, split="g")
    assert len(chart.y_keys) == 8
    chart.y_keys = [f"g{i}" for i in range(15)]                 # force it past the cap
    viz._why(chart, ctx, "trend→line", "r", "d", "v", "sum", 600)
    out = viz.finalize_charts([chart], ctx)
    assert len(out[0].y_keys) == 8


def test_outcome_first_correlation_still_puts_the_driver_on_x():
    case = _case("viz-v06")
    df = dataset(case)[0]
    session, _ = viz_session(case, df)
    ctx = viz.build_context(df, session.task_plan, session.analysis_findings)
    chart = viz.charts_for_findings(ctx)[0]
    assert (chart.x_key, chart.y_keys) == ("marketing_spend", ["revenue"])
    assert chart.rationale.evidence_ids == ["corr:revenue__marketing_spend"]


def test_agent_chart_without_rationale_is_dropped_and_traced(tmp_path):
    df = pd.DataFrame({"a": [1.0, 2.0, 3.0]})
    ctx = viz.build_context(df)
    bare = ChartConfig(id="b", chart_type=ChartType.BAR, title="t", x_key="a", y_keys=["a"],
                       data=[{"a": 1.0}], source_finding="x")
    assert viz.finalize_charts([bare], ctx) == []
    lines = [json.loads(x) for f in (tmp_path / "traces").glob("*.jsonl") for x in f.read_text().splitlines()]
    assert any(r.get("guardrail") == "no_rationale" for r in lines)


def test_injection_in_a_category_is_plotted_as_data_only():
    df = pd.DataFrame({"segment": ["A", INJECTION] * 20, "revenue": np.arange(40.0)})
    ctx = viz.build_context(df)
    chart = viz.group_bars(df, "segment", "revenue", "mean", ctx)
    assert {r["segment"] for r in chart.data} == {"A", INJECTION}
    assert INJECTION not in chart.title and INJECTION not in (chart.rationale.code or "")


async def test_drill_down_chart_on_a_fake_column_is_skipped_and_a_measure_bar_is_repaired(recorded_llm):
    df, _ = datagen.make_sales(1)
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    session = viz_session(_case("viz-v16"), df)[0]
    session.insight_report = InsightReport(executive_summary="s", technical_findings=[],
                                           recommended_actions=[], anomaly_alerts=[],
                                           proactive_questions=[], methodology_notes="")
    from agents.drill_down_agent import run_drill_down_agent

    def reply(x, y):
        return json.dumps({"answer_text": "a", "chart_spec": {"chart_type": "BarChart",
                                                              "x_column": x, "y_column": y}})
    recorded_llm.responses += [reply("nope", "revenue"), reply("units", "revenue")]
    assert (await run_drill_down_agent(session, buf.getvalue())).chart_configs == []
    charts = (await run_drill_down_agent(session, buf.getvalue())).chart_configs
    assert charts and charts[0].chart_type == ChartType.HISTOGRAM   # units is a measure
    assert charts[0].rationale and charts[0].rationale.rule == "distribution→histogram"


def test_confidence_intervals():
    flags = pd.Series([1] * 50 + [0] * 50)
    minus, plus = viz._ci(flags, "mean")                   # Wilson, in % points
    assert minus == pytest.approx(9.65, abs=0.05) and plus == pytest.approx(9.65, abs=0.05)
    assert viz._ci(flags, "sum") is None                    # a total has no sampling error
    m, p = viz._ci(pd.Series([1.0, 2.0, 3.0, 4.0]), "mean")
    assert m == p == pytest.approx(2.054, abs=0.001)        # t(0.975, 3) · s / √n


@pytest.mark.parametrize("seed", range(200))
def test_property_every_chart_is_linted_and_explained(seed):
    """Random frames (dates, measures, small integer measures, categories, flags): whatever the
    profile path and a user's request produce leaves finalize clean and explained."""
    rng = np.random.default_rng(seed)
    n = int(rng.integers(5, 400))
    df = pd.DataFrame({
        "when": pd.date_range("2025-01-01", periods=n, freq=f"{int(rng.integers(1, 4))}D").astype(str),
        "amount": rng.lognormal(3, 1, n).round(2),
        "qty": rng.integers(1, int(rng.integers(3, 12)), n),
        "kind": rng.choice([f"k{i}" for i in range(int(rng.integers(2, 16)))], n),
        "flag": rng.integers(0, 2, n),
        "rate": rng.uniform(0, 1, n),
    })
    if rng.random() < 0.3:
        df.loc[rng.choice(n, n // 3), "amount"] = np.nan
    session = AnalysisSession(session_id="p")
    ctx = viz.build_context(df)
    charts = viz.finalize_charts(viz.generate_dashboard_charts(df, ctx.roles, ctx=ctx), ctx)
    _clean(charts, ctx)
    kind = [ChartType.BAR, ChartType.PIE, ChartType.LINE, ChartType.SCATTER][seed % 4]
    user = viz.build_single_chart(df, TargetedParams(chart_type=kind, group_by="kind",
                                                     metric_column="amount"))
    assert user.rationale is not None and session.session_id == "p"


@pytest.mark.parametrize("make", [datagen.make_sales, datagen.make_simpson])
def test_kpi_check_catches_a_wrong_tile_of_every_kind(make, tmp_path):
    df, _ = make(1)
    kpis = viz.generate_smart_kpis(df, viz.classify_columns(df))
    assert kpis and viz.kpi_mismatches(kpis, df) == [] and all(k.how for k in kpis)
    for i in range(len(kpis)):
        planted = [k.model_copy() for k in kpis]
        planted[i].value = "999"
        assert viz.kpi_mismatches(planted, df) == [planted[i].label]
    avg = next((k for k in kpis if k.recipe.kind == "metric_avg"), None)
    if avg:   # the report's own mean disagrees → the tile is wrong for this report
        assert viz.kpi_mismatches([avg], df, {avg.recipe.column: {"mean": 1e9}}) == [avg.label]


def test_swap_options_never_offer_a_misleading_type():
    df, _ = datagen.make_sales(1)
    ctx = viz.build_context(df)
    means = viz.group_bars(df, "region", "discount", "mean", ctx)
    totals = viz.group_bars(df, "segment", "revenue", "sum", ctx)
    totals.rationale.aggregation = "sum"
    assert ChartType.PIE not in viz.swap_options(means)        # an average is not a part of a whole
    assert ChartType.LINE not in viz.swap_options(means)       # regions have no order
    assert ChartType.DONUT in viz.swap_options(totals)
    assert viz.swap_options(viz.fence_histogram(df, "revenue", ctx)) == []


async def test_chart_feedback_persists_a_safe_switch_and_refuses_the_rest(tmp_path, monkeypatch):
    """POST /dashboard/chart-feedback: owner only, misleading types refused (422), the switch is
    saved on the stored chart and logged with labels only."""
    from types import SimpleNamespace

    from fastapi import HTTPException

    from api.auth import UserProfile
    from api.routes.dashboard import ChartFeedbackRequest, chart_feedback
    from memory.session_store import InMemorySessionStore

    df, _ = datagen.make_sales(1)
    ctx = viz.build_context(df)
    chart = viz.group_bars(df, "region", "discount", "mean", ctx)
    viz._why(chart, ctx, "segment→bars", "r", "region", "discount", "mean", len(df))
    store = InMemorySessionStore()
    session = AnalysisSession(session_id="s1", user_id="owner", chart_configs=[chart])
    await store.save(session)
    from starlette.requests import Request
    from api.rate_limit import limiter
    monkeypatch.setattr(limiter, "enabled", False)   # the limit is slowapi's job, not this test's
    app = SimpleNamespace(state=SimpleNamespace(session_store=store, limiter=limiter))
    req = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "app": app,
                   "client": ("127.0.0.1", 1)})

    def body(t):
        return ChartFeedbackRequest(session_id="s1", chart_id=chart.id, chart_type=t)

    with pytest.raises(HTTPException) as e:
        await chart_feedback(req, body(ChartType.HORIZONTAL_BAR), UserProfile("intruder"))
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        await chart_feedback(req, body(ChartType.PIE), UserProfile("owner"))   # pie of an average
    assert e.value.status_code == 422
    out = await chart_feedback(req, body(ChartType.HORIZONTAL_BAR), UserProfile("owner"))
    assert out["chart"]["chart_type"] == "HorizontalBarChart"
    assert (await store.load("s1")).chart_configs[0].chart_type == ChartType.HORIZONTAL_BAR
    lines = [json.loads(x) for f in (tmp_path / "traces").glob("*.jsonl") for x in f.read_text().splitlines()]
    fb = [r for r in lines if r.get("feedback") == "chart_override"]
    assert fb and fb[0]["before"] == "BarChart" and "discount" not in json.dumps(fb[0])


async def test_injection_in_a_chart_title_reaches_the_insight_prompt_delimited(monkeypatch):
    """Finding titles carry data values (a segment name); the insight prompt treats them as data."""
    import re

    from agents import insight_agent
    from orchestrator.state import AnalysisFindings

    seen = {}

    async def capture(ctx, llm):
        seen["prompt"] = ctx.render()
        raise RuntimeError("stop after the prompt")

    monkeypatch.setattr(insight_agent, "_ask", capture)
    df = pd.DataFrame({"segment": ["A", INJECTION] * 20, "revenue": np.arange(40.0)})
    chart = viz.group_bars(df, "segment", "revenue", "mean", viz.build_context(df))
    chart.title = f"Average revenue by segment: {INJECTION} highest"
    chart.rationale.evidence_ids = ["seg:segment/revenue"]   # only evidence charts reach insight
    session = AnalysisSession(session_id="i", chart_configs=[chart], analysis_findings=AnalysisFindings(
        eda_summary={}, correlations={}, trends=[], segments=[], anomalies=[], statistical_tests=[],
        code_executed=""))
    try:
        await insight_agent.run_insight_agent(session, None)
    except Exception:
        pass
    outside = re.sub(r"<untrusted_data>.*?</untrusted_data>", "", seen["prompt"], flags=re.S)
    assert INJECTION in seen["prompt"] and INJECTION not in outside


@pytest.mark.parametrize("cid", ["viz-v01", "viz-v02", "viz-v10"])
def test_trend_chart_title_states_the_same_change_as_the_finding(cid):
    """The chart and the analysis use one MoM definition, so the title's % is the finding's %."""
    import re
    case = _case(cid)
    df = dataset(case)[0]
    session, _ = viz_session(case, df)
    ctx = viz.build_context(df, session.task_plan, session.analysis_findings)
    chart = viz.charts_for_findings(ctx)[0]
    trends = session.analysis_findings.trends
    best = max(trends, key=lambda t: abs(t.pct_change))
    shown = float(re.search(r"(\d+\.\d)%", chart.title).group(1))
    assert shown == pytest.approx(abs(best.pct_change), abs=0.051), chart.title


async def test_dashboard_clears_the_old_generic_palette_on_pinned_charts():
    from types import SimpleNamespace

    from api.auth import UserProfile
    from api.routes.dashboard import get_dashboard
    from memory.session_store import InMemorySessionStore

    store = InMemorySessionStore()
    base = {"chart_type": "BarChart", "title": "t", "data": [{"a": "x", "b": 1}], "x_key": "a",
            "y_keys": ["b"], "source_finding": "x"}
    await store.pin_chart("u", "s", {**base, "id": "old", "colors": ["#4E79A7", "#F28E2B"]})
    await store.pin_chart("u", "s", {**base, "id": "meaning", "colors": ["#1D9E75", "#A32D2D"]})
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_store=store)))
    charts = {c["id"]: c for c in (await get_dashboard(req, UserProfile("u")))["charts"]}
    assert charts["old"]["colors"] == [] and charts["meaning"]["colors"] == ["#1D9E75", "#A32D2D"]



def test_e2e_answer_ignores_the_critic_so_critic_changes_need_no_regate():
    """Pins why the critic fix needed no paid E2E re-run: E2E scores answer_text, which never
    includes the expert review (and the critic runs after the report is final)."""
    from evals.scoring import answer_text
    from orchestrator.state import ExpertReview
    s = AnalysisSession(session_id="x", expert_review=ExpertReview(
        review_summary="CRITIC-ONLY", strengths=[], gaps=[], next_investigations=[],
        methodology_concerns=[]))
    assert "CRITIC-ONLY" not in answer_text(s)


def _mid_month_sales():
    """Daily orders Jan 1 – Jul 19: July is a partial month."""
    rng = np.random.default_rng(3)
    days = pd.date_range("2025-01-01", "2025-07-19", freq="D")
    return pd.DataFrame({"order_date": days.strftime("%Y-%m-%d"), "segment": rng.choice(list("AB"), len(days)),
                         "revenue": rng.normal(100, 5, len(days))})


def test_analysis_mom_skips_a_partial_last_month():
    """The sandbox's built-in MoM compares the last two complete months (Jun vs May), not a
    19-day July against a full June; on full months nothing changes."""
    from agents.analysis_agent import _build_standard_code
    from orchestrator.state import AnalysisTask

    df = _mid_month_sales()
    contract = viz_session(_case("viz-v16"), df)[0].data_contract
    task = AnalysisTask(raw_query="q", target_metrics=["revenue"], analysis_types=["trend"])
    code = _build_standard_code(contract, task)
    for frame, months in [(df, ("2025-06", "2025-05")),
                          (df[df.order_date <= "2025-06-30"], ("2025-06", "2025-05"))]:
        result = {"trends": [], "correlations": {}, "segments": []}
        exec(code, {"pd": pd, "np": np, "df": frame.copy(), "result": result})
        m = frame.assign(m=pd.to_datetime(frame.order_date).dt.to_period("M").astype(str)) \
            .groupby("m").revenue.sum()
        want = round(float((m[months[0]] / m[months[1]] - 1) * 100), 2)
        assert result["trends"][0]["pct_change"] == want


def test_trend_chart_and_targeted_series_leave_out_a_partial_month():
    df = _mid_month_sales()
    ctx = viz.build_context(df)
    chart = viz.month_line(df, "order_date", "revenue", ctx, monthly=True)
    assert chart.data[-1]["period"] == "2025-06" and "Jul 2025 is left out" in chart.rationale.reason
    from agents.targeted_analysis_agent import handle_time_series
    r = handle_time_series(df, TargetedParams(metric_column="revenue", date_column="order_date",
                                              frequency="M", aggregation="sum"), None, ctx=ctx)
    assert "Jul 2025 is left out" in r.answer_text and r.chart_configs[0].data[-1]["period"] == "2025-06-30"


@pytest.mark.parametrize("name", ["sales", "mixed_dates"])
def test_eval_datasets_end_on_a_month_end_so_the_partial_rule_moves_no_number(name):
    """Pins why the partial-month fix needed no paid re-run: every dated eval table is complete."""
    from agents.utils import partial_last_month
    assert not partial_last_month(datagen.make(name, 1)[0]["order_date"])
