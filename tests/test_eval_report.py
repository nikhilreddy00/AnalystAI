"""evals/report.py reads a stored report: a case's runs with what failed, and summed spend."""
from evals.report import case_lines, cost

REPORT = {"runs": {
    "q-01-a": [{"passed": True, "cost_usd": 0.01, "checks": [{"kind": "defect", "ok": True,
                                                              "detail": "ok"}], "hard": []}],
    "q-02-b": [{"passed": False, "cost_usd": 0.02, "error": None, "replies": [{"text": "x"}],
                "checks": [{"kind": "false_alarm", "ok": False, "detail": "flagged"}],
                "hard": [{"rule": "no_injection_echo", "ok": False, "detail": "echoed"},
                         {"rule": "flags_valid", "ok": True, "detail": "ok"}]}] * 2}}


def test_a_case_shows_each_run_and_only_what_failed():
    got = case_lines(REPORT, "q-02", ["replies"])
    assert got[:3] == ["q-02-b run 0: fail $0.0200", "  - false_alarm: flagged",
                       "  - no_injection_echo: echoed"]
    assert got[3].startswith("  replies: [") and got[4].startswith("q-02-b run 1")
    assert case_lines(REPORT, "q-01", []) == ["q-01-a run 0: PASS $0.0100"]
    assert "cases: q-01-a q-02-b" in case_lines(REPORT, "zz", [])[0]


def test_cost_sums_every_run_of_every_report():
    assert cost([REPORT, REPORT]) == 0.1
