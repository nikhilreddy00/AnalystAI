"""Every answer path on every domain dataset, at $0 (retro 2026-11-04). After every gate passed,
a one-off audit of ~900 answers found 6 majors on paths no test walked: developing periods
compared as trends, the youngest year cut, a partial period headlined, averages per row on claim
data, rows called entities. This walks the same paths for every pack KPI the tiles show, for
every dataset in tests/test_dashboard_filters.PACK_DATASETS (which a test keeps complete), so a
new domain is audited on every push. Relations need no truth table:
- the overall answer states the tile's number;
- a named value's answer = that value's bar in the by-group answer;
- an additive KPI's groups add up to the overall (a group column with no blanks);
- a measure that still develops is never compared across periods, and keeps its youngest period;
- no answer calls the file's row count a count of a tile's entities.
Truths for specific traps stay in tests/test_oracle.py."""
import re

import pytest

import domain_pack
from guardrails import claim_rows_as_entities
from tests.test_dashboard_filters import PACK_DATASETS
from tests.test_oracle import _built, answer


def _number(text: str) -> float:
    return float(re.sub(r"[^0-9.\-]", "", text))


def _first_money_or_number(text: str) -> float | None:
    m = re.search(r":\s*(-?[$£€]?-?[\d,]+(?:\.\d+)?)", text)
    return _number(m.group(1)) if m else None


@pytest.mark.parametrize("name", PACK_DATASETS)
def test_every_kpi_answer_path_agrees_with_the_tile_and_the_groups(name):
    from agents.visualization_agent import pack_kpis
    _, clean, contract, _ = _built(name)
    pack = domain_pack.pack_for(contract.domain)
    roles = {r: c for r, c in contract.roles.items() if c in clean.columns}
    tiles = {k.recipe.kpi: k for k in pack_kpis(clean, contract)}
    groups = [roles[g] for g in pack.overview_groups if g in roles][:2]
    rows = len(clean[~domain_pack.superseded(clean, contract)])
    counts = {k.label: k.value for n, k in tiles.items() if pack.kpi(n).unit == "count"}
    for kpi_name, tile in tiles.items():
        kpi = pack.kpi(kpi_name)
        overall, _ = answer(name, "segment_compare", f"what is our {kpi.phrase}?", kpi=kpi_name)
        texts = [overall]
        if not domain_pack.mixes(clean, roles, kpi):   # a per-line KPI has no one overall figure
            assert tile.value.split(" ")[0] in overall, (name, kpi_name, tile.value, overall)
        for col in groups:
            text, by = answer(name, "top_n", f"{kpi.phrase} by {col}", kpi=kpi_name, group_by=col)
            texts.append(text)
            if not by:
                continue
            value = next(iter(by))   # a named value = its bar
            one, _ = answer(name, "segment_compare", f"{kpi.phrase} for {value}", kpi=kpi_name,
                            segment_column=col, segment_value=value)
            texts.append(one)
            got = _first_money_or_number(one.split(" How:")[0])
            scale = 100 if kpi.unit == "percent" else 1
            if got is not None:
                assert abs(got - by[value] * scale) <= 0.01 * max(1, abs(by[value] * scale)) + 0.05, \
                    (name, kpi_name, col, value, got, by[value])
            # every group shown (a by-group answer lists the top 20) and every row in one group
            full_groups = clean[col].notna().all() and len(by) < 20
            if domain_pack.additive(pack, kpi_name) and full_groups and not domain_pack.mixes(clean, roles, kpi):
                total = _number(tile.value.split(" ")[0])
                assert abs(sum(by.values()) - total) <= 0.01 * len(by) + 1e-6 * abs(total), \
                    (name, kpi_name, col, sum(by.values()), total)
        yearly, chart = answer(name, "time_series", f"{kpi.phrase} by year", kpi=kpi_name, frequency="Y")
        texts.append(yearly)
        develops = domain_pack.developing_note(contract, clean, domain_pack.kpi_measures(pack, kpi_name))
        if develops and chart:   # development is no trend; the youngest year is shown
            assert "year over year" not in yearly and "the period before (" not in yearly, (name, kpi_name, yearly)
            last = max(chart)
            assert last in yearly, (name, kpi_name, last, yearly)
        for t in texts:   # the file's rows are never a count of the things a tile counts
            assert not claim_rows_as_entities(t, rows, counts), (name, kpi_name, t[:200])


@pytest.mark.parametrize("name", PACK_DATASETS)
def test_a_stock_reconciles_with_its_flows_every_month(name):
    """Opening + starts − ends = closing, month by month (Domain 5 red-team M21): the standard HR
    data check, truth-free, so a second copy of the in-force rule that drifts from the events
    (dropouts, rehires, future dates) fails here. The only allowed gap: a spell over at an
    unknown date (status ended, no end date) starts but is never in force."""
    import pandas as pd
    _, clean, contract, _ = _built(name)
    pack = domain_pack.pack_for(contract.domain)
    if not pack.spell:
        return
    roles = {r: c for r, c in contract.roles.items() if c in clean.columns}
    stock = next((k.name for k in pack.kpis if [t.spell for alt in k.num[-1:] for t in alt] == ["active"]
                  and k.num[-1][0].sum is None), None)
    flows = [next(k.name for k in pack.kpis if k.num[-1][0].spell == s and not k.num[-1][0].where)
             for s in ("starts", "ends")]
    frame = clean.assign(_period=pd.Series(pd.NaT, index=clean.index).astype("period[M]"))
    act, st, en = (domain_pack.compute_kpi(frame, roles, pack, k, by="_period", full=clean)
                   for k in (stock, *flows))
    st, en = st.reindex(act.index, fill_value=0), en.reindex(act.index, fill_value=0)
    gap = (act - act.shift(1) - st + en).iloc[1:]
    ended = 0
    if pack.spell.ended and "status" in roles and pack.spell.end in roles:
        sts = domain_pack.words(clean[roles["status"]])
        ended = int((sts.isin(set(domain_pack.words(pd.Series(pack.spell.ended))))
                     & clean[roles[pack.spell.end]].isna()).sum())
    assert (gap <= 0).all() and -gap.sum() <= ended, (name, gap[gap != 0].to_dict(), ended)
