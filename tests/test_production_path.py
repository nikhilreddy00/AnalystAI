"""CODING_STANDARDS rule 1 (an adapter takes the production path), as checks that own the class
instead of one test per harness (D3-10: a $0 harness off the production path, 6 recurrences, each
closed by one more per-harness test; the next new caller started off the path again)."""
import json

import pytest

from tests.test_dashboard_filters import PACK_DATASETS, _dashboard


@pytest.mark.parametrize("name", PACK_DATASETS)
def test_the_built_ins_give_one_answer_on_the_plain_and_the_models_frame(name):
    """A run hands standard_stats the model's frame (row kind, derived spans and spell events
    added, run_analysis_agent); the golden, the eval harness and most tests hand it the plain
    frame. Whatever a caller passes, the answer is one (D7-19 logi-4 paid 0/3: measure() found no
    derived roles on the model's frame)."""
    import domain_pack
    from agents.analysis_agent import builtin_result, model_frame
    from orchestrator.state import AnalysisTask
    clean, contract, *_ = _dashboard(name)
    group = next((c.name for c in contract.columns if not (c.is_metric or c.is_date or c.is_id) and not c.pii),
                 None)
    task = AnalysisTask(raw_query="overview", analysis_types=["eda", "trend", "segmentation"],
                        target_metrics=contract.metric_columns[:3], segments=[group] if group else [])
    pack = domain_pack.pack_for(contract.domain)

    model = model_frame(clean, contract)[0]
    # a number only the model's frame has (a derived span: repair hours, days late)
    spans = [c for c in model.columns if c not in clean.columns and str(model[c].dtype).startswith(("float", "int"))]

    def raw() -> dict | None:   # the model's shapes D7-19 corrects: a plain count, a KPI by name,
        if not group:           # and a plain total of a derived span (repair_breakdowns' input)
            return None
        first = clean.columns[0]
        every = clean.groupby(group)[first].count()
        sums = model.groupby(group)[spans[0]].sum() if spans else None
        return {"segments": [{"segment_column": group, "metric_column": first, "aggregation": "count",
                              "results": [{"name": str(k), "value": float(v)} for k, v in every.items()]}]
                + [{"segment_column": group, "metric_column": first, "aggregation": "ratio", "label": k,
                    "results": [{"name": str(n), "value": 0.0} for n in every.index]}
                   for k in pack.overview_kpis]
                + ([{"segment_column": group, "metric_column": spans[0], "aggregation": "sum",
                     "results": [{"name": str(k), "value": float(v)} for k, v in sums.items()]}]
                   if spans else [])}
    plain = builtin_result(clean, contract, task, raw())
    framed = builtin_result(model, contract, task, raw())
    assert json.dumps(framed, sort_keys=True, default=str) == json.dumps(plain, sort_keys=True, default=str)
    # the next step that takes a frame, repair_breakdowns, on its run's findings: one answer too
    from agents.analysis_agent import _parse_findings, repair_breakdowns
    repaired = []
    for frame, result in ((clean, plain), (model, framed)):
        f = _parse_findings(json.loads(json.dumps(result, default=str)), "", [])
        notes = repair_breakdowns(f, frame, contract, task, set(result.get("builtin") or []))
        repaired.append(json.dumps([[s.model_dump(mode="json") for s in f.segments], notes], default=str))
    assert repaired[0] == repaired[1]
