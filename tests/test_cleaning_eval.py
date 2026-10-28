"""The cleaning eval scores what it should: a perfect cleaner passes every check, doing nothing
fails every planted issue, and over-cleaning is caught (offline, no model)."""
import pandas as pd

from evals.run import cleaning_dataset, load_cases
from evals.scoring import clean_cell, score_cleaning

CASES = load_cases("cleaning", None)


def _frame(df: pd.DataFrame, audit: dict) -> dict:
    return {"index": [int(i) for i in df.index], "columns": list(df.columns),
            "rows": [[clean_cell(v) for v in r] for r in df.itertuples(index=False)], "audit": audit}


def _perfect(rec: pd.DataFrame, exp: pd.DataFrame) -> dict:
    return _frame(exp[list(rec.columns)], {
        "decisions": [{"column": c} for c in rec.columns]
        + [{"column": "*", "rows_removed": len(rec) - len(exp)}],
        "rows_before": len(rec), "rows_after": len(exp), "high_null_columns": ["promo", "memo"],
        "anomaly_report": {"flags": [{"column": "revenue"}, {"column": "amount"}]}})


def test_suite_shape():
    assert len(CASES) == 25
    assert len({c["id"] for c in CASES}) == 25
    assert sum(c["id"].endswith("holdout") for c in CASES) == 5
    assert all(c.get("label_note") for c in CASES)


def test_perfect_cleaner_passes_everything():
    for case in CASES:
        rec, exp, man, prot = cleaning_dataset(case)
        checks, hard = score_cleaning(_perfect(rec, exp), rec, exp, man, prot, None)
        assert all(x["ok"] for x in checks + hard), (case["id"], [x for x in checks + hard if not x["ok"]])


def test_doing_nothing_fails_every_planted_issue():
    for case in CASES:
        rec, exp, man, prot = cleaning_dataset(case)
        audit = {"decisions": [], "rows_before": len(rec), "rows_after": len(rec), "high_null_columns": []}
        checks, _ = score_cleaning(_frame(rec, audit), rec, exp, man, prot, None)
        assert not any(x["ok"] for x in checks if x["kind"] == "planted"), case["id"]


def test_over_cleaning_and_hard_rules_are_caught():
    case = next(c for c in CASES if c["id"] == "clean-20-clean-bank")
    rec, exp, man, prot = cleaning_dataset(case)
    out = exp[list(rec.columns)].copy()
    out["product"] = out["product"].str.replace("-", "")        # merges A-1 into A1
    out["account_code"] = pd.to_numeric(out["account_code"], errors="coerce")  # drops leading zeros
    out = out.drop(index=[0, 1])                                # unlogged removal
    audit = {"decisions": [{"column": "txn_date"}], "rows_before": len(rec),
             "rows_after": len(out), "high_null_columns": []}
    checks, hard = score_cleaning(_frame(out, audit), rec, exp, man, prot, None)
    failed = {x.get("issue") or x["rule"] for x in checks + hard if not x["ok"]}
    assert {"unchanged:product", "unchanged:account_code", "base_rows_kept",
            "rows_logged", "changes_logged"} <= failed


def test_protected_fill_is_a_hard_violation():
    case = next(c for c in CASES if c["id"] == "clean-12-target-nulls")
    rec, exp, man, prot = cleaning_dataset(case)
    out = exp[list(rec.columns)].copy()
    out["revenue"] = out["revenue"].fillna(out["revenue"].mean())
    audit = {"decisions": [{"column": c} for c in rec.columns], "rows_before": len(rec),
             "rows_after": len(out), "high_null_columns": []}
    _, hard = score_cleaning(_frame(out, audit), rec, exp, man, prot, None)
    assert [h["rule"] for h in hard if not h["ok"]] == ["protected_not_filled"]


def test_generator_is_deterministic():
    a, b = cleaning_dataset(CASES[-1]), cleaning_dataset(CASES[-1])
    pd.testing.assert_frame_equal(a[0], b[0])
    assert a[2] == b[2]
