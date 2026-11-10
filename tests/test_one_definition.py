"""Guards for rules that kept coming back in Phase 5.7 (each bug was a copy of a rule, or a rule
that depended on the machine). A failure here names the one shared definition to use instead."""
import re
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).parent.parent
PACKAGE = sorted((ROOT / "domain_pack").glob("*.py"))
SHARED = [*sorted((ROOT / "agents").glob("*.py")), ROOT / "guardrails.py", *PACKAGE]


def _name(p: Path) -> str:
    """agents/x.py → 'x.py'; domain_pack/x.py → 'domain_pack/x.py' (two __init__.py)."""
    return f"domain_pack/{p.name}" if p.parent.name == "domain_pack" else p.name


DOMAIN_PACK = {_name(p) for p in PACKAGE}   # the package's own files


def _hits(pattern: str, allowed: set[str] = frozenset()) -> list[str]:
    rx = re.compile(pattern)
    return [f"{_name(p)}:{i}" for p in SHARED if _name(p) not in allowed
            for i, line in enumerate(p.read_text().splitlines(), 1)
            if rx.search(line) and not line.lstrip().startswith("#")]


def test_every_shared_file_the_guards_read_exists():
    """A guard over a file that moved reads nothing and passes (the domain_pack split, 5.7b-1):
    every path is there, and the package is read whole."""
    assert all(p.is_file() for p in SHARED)
    assert {p.stem for p in PACKAGE} >= {"__init__", "pack", "detect", "columns", "spell", "ledger",
                                         "kpi", "rows", "story"}


def test_ties_rank_the_same_on_every_machine():
    """CI (Linux) and macOS ranked tied groups differently: an unstable sort. utils.ranked sorts
    by label, then stably by value."""
    from agents.utils import ranked
    s = pd.Series([11.0, 5.0, 11.0, 11.0], index=["c", "a", "b", "d"])
    assert list(ranked(s)) == [11.0, 11.0, 11.0, 5.0]
    assert list(ranked(s).index) == ["b", "c", "d", "a"]
    assert list(ranked(s, ascending=True).index) == ["a", "b", "c", "d"]


def test_rankings_go_through_ranked():
    assert _hits(r"\.sort_values\(ascending=(?![^)]*kind=)") == [], "use agents.utils.ranked(series, ascending)"


def test_one_sentence_splitter():
    assert _hits(r"\(\?<=\[\.!\?\]\)", {"guardrails.py"}) == [], "use guardrails._SENTENCE_RE"


def test_shared_code_never_names_a_pack_role():
    """A pack declares its layout in YAML (order_key, negative_is_return, cost_roles …): shared
    code reading roles["order_id"] gives every other domain retail's behaviour, or none. Only the
    schema's reserved names (domain_pack.RESERVED_ROLES: status, currency) may be named."""
    import domain_pack
    names = {r for p in domain_pack.load_packs() for r in p.roles} - set(domain_pack.RESERVED_ROLES)
    alt = "|".join(sorted(names))
    found = _hits(rf"""roles(?:\.get\(|\[)\s*["']({alt})["']|["']({alt})["']\s+in\s+(?:ctx\.)?roles\b""")
    assert found == [], f"read the pack's field, not a role name: {found}"


def test_a_comparison_says_ties_and_names_the_real_lowest_group():
    """CI found it: 'MRN-006520' leads at 11.00, 'MRN-004874' trails at 11.00 — a tie named as
    a lead, and 'trails' was the 20th group shown, not the lowest."""
    from agents.targeted_analysis_agent import handle_segment_compare
    from connectors.csv_connector import CSVConnector
    from orchestrator.state import ConnectorConfig, ConnectorType, TargetedParams
    df = pd.DataFrame({"site": [f"S{i:02d}" for i in range(25)],
                       "stay": [11.0] * 3 + [5.0] * 21 + [1.0]})
    c = CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df)
    r = handle_segment_compare(df, TargetedParams(metric_column="stay", segment_column="site",
                                                  aggregation="mean"), c)
    assert "'S00', 'S01' and 'S02' tie at 11.00" in r.answer_text
    assert "'S24' is lowest at 1.00" in r.answer_text
    flat = pd.DataFrame({"site": ["a", "b"], "stay": [3.0, 3.0]})
    r = handle_segment_compare(flat, TargetedParams(metric_column="stay", segment_column="site",
                                                    aggregation="mean"), c)
    assert "all 2 groups are at 3.00" in r.answer_text


def test_one_rule_for_blanks_that_are_the_layout():
    """Domain 1 left the schema summary saying "Discount Amount 44% missing" on a Shopify export
    while cleaning knew better: every place that reports blanks reads domain_pack.layout_columns."""
    assert _hits(r"first_line_only\(", DOMAIN_PACK) == [], "use domain_pack.layout_columns"


def test_a_kpi_label_in_a_sentence_is_kpi_phrase():
    """Banking's caveat said "npl ratio (90+ dpd)": labels were lower-cased at each site."""
    import domain_pack
    assert _hits(r"\.label\.lower\(\)") == [], "use KPI.phrase"
    bank, retail = domain_pack.pack_for("banking"), domain_pack.pack_for("retail")
    assert (bank.kpi("npl_ratio").phrase, retail.kpi("net_sales").phrase) == (
        "NPL ratio", "net sales")


def test_one_rename_to_display_names():
    """Domain 1 fixed 'Order Order Total' in the insight; the expert review kept its own rename
    and wrote 'Loan Loan Product' (Domain 2 smoke run)."""
    from agents.utils import with_display_names
    names = {"Product": "Loan Product", "order_date": "Order Date", "Total": "Order Total"}
    assert with_display_names("Days Past Due by Loan Product and Product", names) == \
        "Days Past Due by Loan Product and Loan Product"
    assert with_display_names("order_date and order date; the total Total", names) == \
        "Order Date and order date; the total Order Total"   # a display name as written stays
    # chart titles (visualization_agent) are built from raw names, never model prose, and also fix
    # casing ('revenue' → 'Revenue'): their own rule
    assert _hits(r"column_display_names\.items\(\)|names_re", {"visualization_agent.py"}) == [], \
        "use utils.with_display_names"


def test_a_correlation_on_snapshot_data_counts_each_entity_once():
    """Passes 1–3 each found correlations over every snapshot (each loan 14 times): every r a
    user sees reads domain_pack.entity_rows (the sandbox mirrors it as _cdf). Per function: one
    that computes r takes entity_rows, is handed its rows by one that does, or is a column-role
    heuristic (which outcome column; no r is shown)."""
    import inspect

    from agents import targeted_analysis_agent as t
    from agents import visualization_agent as v
    handed = {"_best_correlated_pair", "_build_correlation_heatmap", "_build_scatter"}
    roles_only = {"classify_columns", "_rank_continuous_by_target_assoc"}
    for mod in (t, v):
        for name, fn in inspect.getmembers(mod, inspect.isfunction):
            if fn.__module__ != mod.__name__ or name in handed | roles_only:
                continue
            src = inspect.getsource(fn)
            assert ".corr(" not in src or "entity_rows" in src, f"{name}: r on domain_pack.entity_rows"
    from agents import analysis_agent   # the built-ins compute r in-process, on entity_rows
    assert "entity_rows(" in inspect.getsource(analysis_agent.standard_stats)


def test_one_percent_change_rule_in_answers():
    """Four hand-written % changes in the targeted answers, one of them off a negative base: every
    stated % change is utils.pct_change (None off a zero or negative base)."""
    import re
    src = (ROOT / "agents" / "targeted_analysis_agent.py").read_text()
    hand = [ln for ln in src.splitlines()
            if re.search(r"/\s*\w+(\.iloc\[[-\d]+\])?\s*-\s*1\)\s*\*\s*100", ln)]
    assert hand == [], f"use agents.utils.pct_change: {hand}"


_REDUCES = re.compile(r"\.(?:quantile|mean|median|sum|corr|std|nunique)\(|value_counts\(|aggregate\(|np\.histogram\(")
_ROW_RULES = ("rows_for(", "entity_rows(", "compute_kpi(", "handle_kpi(", "pack_kpis(",
              "superseded(")   # a version chain's earlier rows (domain_pack): counted rows only
# Whole-table profile functions: they describe the table's shape (which column is a category,
# how many rows per value), never a measure's value a tile also states. A new entry needs a
# reason here; anything that states a measure's number takes rows_for (Domain 3: the outlier
# histogram summed voided claims because the guard only looked for `aggregate(`).
PROFILE = {"classify_columns", "_build_color_map", "_pick_primary_metric", "_pick_top_category",
           "_rank_continuous_by_target_assoc", "_ranked_categoricals_by_target_separation",
           "_highest_cv_kpi", "_comparison_kpi_chi2", "_best_correlated_pair",
           "_build_correlation_heatmap", "_build_value_counts", "_build_donut",
           "_build_horizontal_bar_top_n", "_build_stacked_bar_normalized", "_build_target_rate_by_cat",
           "_build_continuous_split_by_target", "_build_time_series", "compute_briefing_extras",
           "generate_dashboard_charts", "build_single_chart", "handle_ab_significance",
           # OPEN (2026-11-03): the profile histogram of a measure still reads every row; moving
           # the profile charts onto counted rows changes retail/banking overviews (a paid
           # re-measure) — planned with Phase 5.8, never extended silently
           "_build_histogram"}


def _forgets_rows_for(name: str, src: str) -> bool:
    """A function that reduces the uploaded frame without taking its rows from a row rule."""
    first = src.lstrip().splitlines()[0] if src.strip() else ""
    takes_df = re.search(r"def \w+\(\s*df\b", first) is not None
    return (takes_df and _REDUCES.search(src) is not None and not any(r in src for r in _ROW_RULES)
            and name not in PROFILE and not first.startswith("def aggregate"))


def test_every_number_a_user_reads_takes_its_rows_from_rows_for():
    """Plan v2 R1: 'which rows does this number read' recurred ~22 times in new call sites. A
    function that aggregates the uploaded frame for an answer, a chart or a drill-down reads its
    rows through domain_pack.rows_for (kinds, currency, a level's latest row first)."""
    import inspect

    from agents import drill_down_agent, targeted_analysis_agent, visualization_agent
    bad = [f"{m.__name__}.{n}" for m in (targeted_analysis_agent, visualization_agent, drill_down_agent)
           for n, fn in inspect.getmembers(m, inspect.isfunction)
           if fn.__module__ == m.__name__ and _forgets_rows_for(n, inspect.getsource(fn))]
    assert bad == [], f"take the rows from domain_pack.rows_for: {bad}"
    # the guard itself catches a new site that forgets the rule (a planted violation)
    assert _forgets_rows_for("by_branch", "def by_branch(df, c):\n    return aggregate(df, ['b'], 'x', 'mean', c)\n")
    assert _forgets_rows_for("fences", "def fences(df, col):\n    return df[col].quantile(0.25)\n")
    assert not _forgets_rows_for("ok", "def ok(df, c):\n    df, _ = rows_for(df, c, 'x')\n    return aggregate(df)\n")


def test_a_question_without_its_number_asks_never_guesses_the_first_column():
    """Plan v2 R3: 'the balance' on a loan tape read Original Amount (metric_columns[0]). One
    resolver (targeted_analysis_agent._metric); the guess appears nowhere else."""
    import inspect
    import re
    from pathlib import Path

    import pandas as pd

    from agents import targeted_analysis_agent as t
    from orchestrator.state import TargetedParams
    for f in Path("agents").glob("*.py"):
        src = f.read_text()
        if f.name == "targeted_analysis_agent.py":
            src = src.replace(inspect.getsource(t._metric), "")
        assert not re.search(r"metric_columns\[0\]", src), f.name
    from connectors.csv_connector import CSVConnector
    from orchestrator.state import ConnectorConfig, ConnectorType
    df = pd.DataFrame({"d": pd.to_datetime(["2026-01-31", "2026-02-28", "2026-03-31"] * 4),
                       "a": [1.5, 2.25, 3.75, 4.5] * 3, "b": [3.5, 4.25, 5.75, 6.5] * 3})
    build = CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract
    two = build(df)
    assert set(two.metric_columns) == {"a", "b"}
    r = t.handle_time_series(df, TargetedParams(frequency="M"), two)
    assert r.unanswerable_reason and "a or b" in r.unanswerable_reason
    one = build(df.drop(columns="b"))
    assert not t.handle_time_series(df, TargetedParams(frequency="M"), one).unanswerable_reason


def test_one_version_chain_rule():
    """Domain 3: a claim's replaced and voided versions are resolved once, in
    domain_pack._versions (inside row_kinds); every answer, tile and chart reads row kinds."""
    assert _hits(r"version_(?:key|order|void)", DOMAIN_PACK) == [], \
        "read domain_pack.row_kinds / rows_for; never re-derive version chains"


def test_every_highest_lowest_sentence_leaves_the_pooled_group_out():
    """Domain 3 live run: 'Other (fewer than 11 each) lowest (0.00)'. Per function (a file-level
    grep passed with one compliant site beside two that weren't, retro 2026-11-03): every function
    that writes a highest / lowest / leads sentence filters its groups through guardrails.rankable."""
    import inspect

    from agents import drill_down_agent, insight_agent, targeted_analysis_agent, visualization_agent
    sentence = re.compile(r"(?:highest|lowest|leads)(?: at)? \(?\{|\{[^}]*\} (?:highest|leads) |is lowest at")
    bad = [f"{m.__name__}.{n}" for m in (targeted_analysis_agent, visualization_agent, insight_agent,
                                         drill_down_agent)
           for n, fn in inspect.getmembers(m, inspect.isfunction)
           if fn.__module__ == m.__name__ and sentence.search(inspect.getsource(fn))
           and "rankable(" not in inspect.getsource(fn)
           and n not in {"_kpi_how"}]   # describes a method ("the highest and lowest <column>"), names no group
    assert bad == [], f"filter the groups through guardrails.rankable: {bad}"


def test_a_pooled_department_is_never_named_in_an_answer():
    """Behaviour, on the real stays data (PHI mode pools the 7-stay department)."""
    from tests.test_oracle import _built, answer
    from guardrails import POOLED_LABEL
    _built("inpatient")
    for kpi in ("readmission_rate", "mortality_rate", "alos"):
        for order in ("desc", "asc"):
            text, _ = answer("inpatient", "top_n", f"{kpi} by department", kpi=kpi, order=order,
                             segment_column="Department", group_by="Department")
            assert POOLED_LABEL not in text, (kpi, order, text)


def test_version_roles_are_named_once():
    """Pass 3: shared code asks domain_pack.version_roles, never re-lists a pack's version fields."""
    assert _hits(r"\(\*?pack\.version_key|pack\.version_order\)", DOMAIN_PACK) == []


@pytest.mark.parametrize("strategy", ["order_level", "paired", "conditional", "marker"])
def test_a_layout_blank_is_never_reported_missing(strategy):
    """Domain 4 live run: three copies of 'which blanks are the file's layout' (cleaning's list,
    analysis evidence notes, insight alerts) disagreed: a loss run's Case Reserve (blank on
    closed claims by design) was '71% missing' in evidence and a CRITICAL alert. Every reader
    of the audit takes cleaning_agent.LAYOUT_STRATEGIES; tested on the behaviour, per kind."""
    from agents.analysis_agent import build_evidence
    from agents.cleaning_agent import LAYOUT_STRATEGIES
    from agents.insight_agent import _alerts
    from orchestrator.state import (AnalysisFindings, AnalysisSession, CleaningAudit,
                                    CleaningDecision, SegmentResult)
    assert strategy in LAYOUT_STRATEGIES
    d = CleaningDecision(column="Col", operation="keep", strategy=strategy, reason="layout",
                         rows_affected=0, before_null_pct=90.0, after_null_pct=90.0)
    audit = CleaningAudit(decisions=[d], rows_before=10, rows_after=10, columns_coerced=[],
                          duplicates_removed=0, outliers_flagged=[], high_null_columns=[], summary="")
    f = AnalysisFindings(eda_summary={}, correlations={}, trends=[], anomalies=[], statistical_tests=[],
                         forecast_points=[], distributions={}, code_executed="", segments=[
        SegmentResult(segment_column="G", metric_column="Col", aggregation="sum",
                      results=[{"name": "a", "value": 1.0}])])
    notes = " ".join(a for e in build_evidence(f, {}, "", "", audit) for a in e.assumptions)
    alerts, _ = _alerts(AnalysisSession(session_id="t", cleaning_audit=audit, analysis_findings=f))
    assert "missing" not in notes and not [a for a in alerts if "missing" in a.title.lower()]


def test_missing_labels_are_never_ranked_highest_or_lowest():
    """Post-Domain-4 audit: 'Unknown' (cleaning's label for blanks) was named the lowest DRG by
    billed charges. Like the pooled small groups, it is no group a ranking sentence names: one
    label, guardrails.UNKNOWN_LABEL, read by cleaning and by rankable."""
    from agents import cleaning_agent
    from guardrails import POOLED_LABEL, UNKNOWN_LABEL, rankable
    assert cleaning_agent.UNKNOWN is UNKNOWN_LABEL
    assert not rankable(UNKNOWN_LABEL) and not rankable(POOLED_LABEL) and rankable("Cardiology")


def test_a_spells_ended_statuses_are_read_once():
    """Post-Domain-5 audit: 'ended with no date' was written three ways (_ended, the frame's
    left_date_unknown marker, the file's story). One reader, domain_pack._ended_undated; its
    three users agree on the planted rows (5 Terminated, no date, on hr_roster)."""
    import domain_pack as dp
    from tests.test_oracle import _built
    src = (ROOT / "domain_pack" / "spell.py").read_text().splitlines()
    start = next(i for i, ln in enumerate(src, 1) if ln.startswith("def _ended_undated("))
    end = next(i for i, ln in enumerate(src[start:], start + 1) if ln.startswith("def "))
    readers = [h for h in _hits(r"\.ended\b") if not (h.startswith("domain_pack/spell.py:")
                                                       and start <= int(h.split(":")[1]) < end)]
    assert not readers, f"read the ended statuses through domain_pack._ended_undated: {readers}"
    _, df, contract, _ = _built("hr_roster")
    pack, roles = dp.pack_for("hr"), {r: c for r, c in contract.roles.items() if c in df.columns}
    marked = dp.with_spell_events(df, pack, roles)["left_date_unknown"].eq("yes")
    as_of = dp.spell_as_of(pack, df, roles)
    assert marked.sum() == 5 and not marked[dp._in_force(df, roles, pack, as_of).index].any()
    assert any(f"{int(marked.sum())} rows have an ended status" in s for s in dp.spell_story(pack, df, roles))


def test_shared_code_never_says_a_spells_hr_words():
    """Domain 6 (S0): the spell markers and sentences were HR's own words in shared code
    (hire_event_date, 'exit records', 'rows are N people'), so a subscription book read 'exits'.
    The pack names them (Spell.words); shared code reads spell_event_columns and the words."""
    hits = _hits(r"hire_event|exit_event|exit_in_window|left_date_unknown|exit records|"
                 r"rows are \{people:,\} people|\bexits in the period|"
                 r"stopped paying|not paying now|those paying|churns of customers")   # SaaS's (audit 1)
    assert not hits, f"say a spell's words through pack.spell.words: {hits}"
    import yaml

    import domain_pack as dp
    from tests.test_spell import SUBS, ROLES, _roster
    pack = dp.Pack.model_validate({**yaml.safe_load(SUBS), "spell": {
        **yaml.safe_load(SUBS)["spell"], "words": {"start": "start", "end": "cancellation",
                                                   "plural": "subscribers"}}})
    framed = dp.with_spell_events(_roster(), pack, ROLES)
    assert {"start_event_date", "cancellation_event_date", "cancellation_in_window"} <= set(framed)
    assert not any(w in c for c in framed.columns for w in ("hire", "exit"))
    assert any("subscribers" in s for s in dp.spell_story(pack, _roster(), ROLES))


def test_no_pack_caveat_names_a_column():
    """Domain 6 insight gate (ins-49, a hard-rule violation): a role caveat said 'canceled_at',
    the executive summary quoted it, and executive_no_jargon failed. Caveats reach the report:
    plain words only, every pack."""
    import domain_pack as dp
    bad = [(p.domain, k) for p in dp.load_packs() for k, t in [*p.role_caveats.items(), *p.caveats.items()]
           if re.search(r"(?<![{\w])[a-z]+_[a-z_]+\b(?![}])", t)]
    assert not bad, f"write caveats in plain words, never a column name: {bad}"


def test_no_spell_kpi_label_names_a_period():
    """Review: 'Logo churn (12 months)' sat beside its context '6-month cohort' on short data. A
    spell KPI's period has one owner, window_words (the tile context, the answer's brackets)."""
    import re

    from domain_pack import load_packs, spell_terms
    period = re.compile(r"\d+\s*(?:-\s*)?(?:month|year)s?|\bearlier\b|\bannual\b", re.I)
    bad = [(p.domain, k.label) for p in load_packs() if p.spell for k in p.kpis
           if spell_terms(p, k.name) and period.search(k.label)]
    assert not bad, bad


def test_one_repeat_test_for_dated_rows():
    """Review: _snapshot_dates copied _panel's 'dates per entity' median. domain_pack._dates_each
    is the one; a second `.size().median()` in shared code is a second definition."""
    assert len(_hits(r"\.size\(\)\.median\(\)")) == 1, _hits(r"\.size\(\)\.median\(\)")


def test_no_shared_sentence_writes_the_window_span():
    """Retro 2026-11-06: 'Logo churn (12 months)' was fixed in the labels, then the audit found
    '12-month cohort' in a targeted template and '(12 months to …)' in the ledger facts, wrong on
    shorter data. The span has one owner (domain_pack.WINDOW_MONTHS, window_words): no string a
    shared module writes names it. Comments and docstrings may."""
    import ast
    span = re.compile(r"\b(?:12|twelve)[- ]months?\b", re.I)
    bad = []
    for p in SHARED:
        tree = ast.parse(p.read_text())
        docs = {id(n.body[0].value) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and n.body and isinstance(n.body[0], ast.Expr)
                and isinstance(n.body[0].value, ast.Constant)}
        bad += [f"{p.name}:{n.lineno}" for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in docs and span.search(n.value)]
    assert not bad, bad


def test_the_window_in_force_and_moves_are_defined_once():
    """5.7b-1 (the domain_pack split): the window, the as-of and who is in force are defined in
    domain_pack/spell.py only; the ledger and its moves in domain_pack/ledger.py only. A second
    `def` of one of them anywhere in shared code is a second copy of the rule."""
    import ast
    owner = {**dict.fromkeys(["spell_window", "spell_as_of", "spell_from", "_in_force_at",
                              "_ended", "first_known", "cohort_start", "_month_ends"], "spell"),
             **dict.fromkeys(["_ledger", "_ledger_term"], "ledger")}
    owner["MOVES"] = "ledger"   # the move kinds: one tuple
    defs = [(n.name, _name(p)) for p in SHARED for n in ast.walk(ast.parse(p.read_text()))
            if isinstance(n, ast.FunctionDef) and n.name in owner]
    defs += [(t.id, _name(p)) for p in SHARED for n in ast.parse(p.read_text()).body
             if isinstance(n, ast.Assign) for t in n.targets
             if isinstance(t, ast.Name) and t.id in owner]
    assert sorted(defs) == sorted((f, f"domain_pack/{m}.py") for f, m in owner.items()), defs


@pytest.mark.xfail(strict=True, reason="C2: 5.7b-2's Window type removes these rebuilds")
def test_no_reader_rebuilds_the_window_from_its_span():
    """Today the window's start is rebuilt from its span outside spell.py: the ledger's cohort
    starts by period (ledger.py), the story's year-earlier window (story.py) and the targeted
    year-before (targeted_analysis_agent.py). Each is a copy of the rule (PR1-02)."""
    rebuilt = _hits(r"DateOffset\((?:months=WINDOW_MONTHS|years=1)",
                    {"domain_pack/spell.py", "utils.py"})   # utils: _year_back, a YoY offset
    assert rebuilt == [], rebuilt
