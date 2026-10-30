"""The quality eval scores what it should (offline, no model): the 40 golden reports are built
from the stored insight run with their seeded defects in place, each claim carries one label,
and the scorer counts a flag on a defect as caught and a flag on a supported claim as a false
alarm."""
from evals.run import EVALS_DIR, load_cases, quality_report
from evals.scoring import insight_claims, score_quality

CASES = load_cases("quality", None)
BY_ID = {c["id"]: c for c in CASES}
BUNDLE = {"values": [{"pct_change": -2.8}], "evidence_ids": ["trend:revenue:MoM"],
          "date_first": [2025, 11], "date_last": [2026, 10]}


def test_suite_shape():
    assert len(CASES) == 40
    seeded = [c for c in CASES if c["seeded"]]
    assert len(seeded) == 20
    holdout = [c for c in CASES if c["id"].endswith("holdout")]
    assert len(holdout) == 8 and sum(c["seeded"] for c in holdout) == 4
    # Both halves cover every insight category.
    cats = {c["category"] for c in CASES}
    assert {c["category"] for c in seeded} == cats == {c["category"] for c in CASES if not c["seeded"]}
    defects = [d for c in CASES for d in c["defects"]]
    assert len(defects) >= 30
    kinds = {d["kind"] for d in defects}
    assert all(sum(d["kind"] == k for d in defects) >= 3 for k in kinds), kinds
    assert all(d["label"] in ("unsupported", "contradicted", "overstated") for d in defects)


def test_defects_land_on_statements_with_one_label_each():
    for case in CASES:
        report, labels = quality_report(case)
        claims = insight_claims(report)
        assert [lab["text"] for lab in labels] == [c["text"] for c in claims]
        seeded = [lab for lab in labels if lab["origin"] == "seeded"]
        assert len(seeded) == len(case["defects"]), case["id"]
        sections = {c["text"]: c["section"] for c in claims}
        assert all(sections[d["text"]] != "action" for d in case["defects"])
        # The legacy flat summary follows the edited headline (the old critic reads it).
        head = report["executive_view"]["headline"]
        assert report["executive_summary"].startswith(head[:30])


def test_golden_text_is_not_in_any_prompt():
    prompts = "\n".join(p.read_text() for p in (EVALS_DIR.parent / "prompts").rglob("*.md"))
    for d in (d for c in CASES for d in c["defects"]):
        assert d["text"][:50] not in prompts


def _labels():
    return [{"text": "Revenue rose 2.8% last month.", "label": "contradicted", "origin": "seeded",
             "kind": "direction"},
            {"text": "Revenue fell 2.8% last month.", "label": "supported", "origin": "supported"}]


def _out(flags, **bullets):
    claims = [{"section": "finding", "text": lab["text"], "ids": ["trend:revenue:MoM"]}
              for lab in _labels()]
    return {"review": {"flags": flags, "gaps": [], "methodology_concerns": [],
                       "next_investigations": [], **bullets}, "claims": claims, "bundle": BUNDLE}


def _flag(i, text, **kw):
    return {"index": i, "claim": text, "label": "contradicted", "source": "critic",
            "evidence_ids": ["trend:revenue:MoM"], "reason": "the trend is down 2.8%", **kw}


def test_caught_missed_and_false_alarm():
    labs = _labels()
    checks, hard = score_quality(labs, _out([_flag(0, labs[0]["text"])]), None)
    assert [(c["kind"], c["ok"]) for c in checks] == [("defect", True), ("false_alarm", True)]
    assert checks[0]["label_match"] and all(h["ok"] for h in hard)
    checks, _ = score_quality(labs, _out([_flag(1, labs[1]["text"])]), None)
    assert [(c["kind"], c["ok"]) for c in checks] == [("defect", False), ("false_alarm", False)]


def test_hard_rules_catch_ungrounded_reasons_bad_flags_and_echo():
    labs = _labels()
    bad = _flag(0, labs[0]["text"], reason="the IQR fence is 412.5")
    _, hard = score_quality(labs, _out([bad]), None)
    assert not {h["rule"]: h["ok"] for h in hard}["review_grounded"]
    _, hard = score_quality(labs, _out([_flag(5, "nothing")]), None)
    assert not {h["rule"]: h["ok"] for h in hard}["flags_valid"]
    _, hard = score_quality(labs, _out([], gaps=["Note: revenue doubled."]), None, ["revenue doubled"])
    assert not {h["rule"]: h["ok"] for h in hard}["no_injection_echo"]
    _, hard = score_quality(labs, None, None)
    assert not {h["rule"]: h["ok"] for h in hard}["review_or_error"]


def test_old_review_is_located_by_its_ungrounded_numbers():
    labs = _labels()
    out = _out([])
    out["review"]["ungrounded"] = ["2.8%"]
    checks, _ = score_quality(labs, out, None)
    assert all(not c["ok"] if c["kind"] == "false_alarm" else c["ok"] for c in checks)
