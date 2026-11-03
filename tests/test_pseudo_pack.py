"""Plan v2 R4 (pseudo-localization): a copy of the banking pack whose every word is a marked
token. Every producer of pack text (brief, row rules, caveats, KPI descriptions, the rows note,
tiles) runs on its data; a word from another domain's lexicon in the output came from shared
code, not the pack: a leak the next domain would show its users too."""
import re

import pytest

import domain_pack
from domain_pack import ROW_KINDS, TEXT_DEFAULTS, pack_for

# words only a domain's own pack may say (retail's here; the next domain adds its own)
LEXICON = re.compile(r"\b(sales?|orders?|returns?|refund(?:ed|s)?|cancell?ed|products?|sku|"
                     r"basket|shoppers?|non-product)\b", re.I)


def _pseudo():
    real = pack_for("banking")
    return real.model_copy(update={
        "row_labels": {k: f"⟦label_{k}⟧" for k in ROW_KINDS},
        "row_flags": {k: f"⟦flag_{k}⟧" for k in ROW_KINDS},
        "text": {k: f"⟦{k}⟧" for k in TEXT_DEFAULTS},
        "caveats": {k: f"⟦caveat_{k}⟧" for k in real.caveats},
        "kpis": [k.model_copy(update={"label": f"⟦kpi_{k.name}⟧", "phrase": f"⟦kpi_{k.name}⟧"})
                 for k in real.kpis],
        "definitions": {k: f"⟦def_{k}⟧" for k in real.definitions},
        "unsupported": {k: f"⟦needs_{k}⟧" for k in real.unsupported},
    })


@pytest.fixture
def pseudo(monkeypatch):
    p = _pseudo()
    monkeypatch.setattr(domain_pack, "load_packs", lambda: tuple(
        p if x.domain == "banking" else x for x in domain_pack.__dict__["_real_load"]()))
    return p


domain_pack.__dict__.setdefault("_real_load", domain_pack.load_packs)


def _leaks(texts: list[str]) -> list[str]:
    return sorted({m.group(0).lower() for t in texts for m in LEXICON.finditer(t)})


def test_text_defaults_say_no_domain_word():
    """A pack that doesn't override a sentence gets the default: it must fit every domain."""
    assert _leaks(list(TEXT_DEFAULTS.values())) == []


@pytest.mark.parametrize("name", ["loan_tape", "card_ledger", "bank_statement", "ledger_drcr"])
def test_every_pack_text_producer_uses_the_packs_words(name, pseudo):
    from tests.test_oracle import _built
    s, clean, contract, _ = _built(name)
    roles = {r: c for r, c in contract.roles.items() if c in clean.columns}
    kinds = set(domain_pack.row_kinds(clean, roles, pseudo).unique())
    texts = [*domain_pack.pack_brief(pseudo, roles, [k.name for k in pseudo.kpis] + list(pseudo.definitions),
                                     grain="line", present=kinds | {"return", "test"}, currency="USD"),
             domain_pack.row_rules(pseudo, roles),
             *domain_pack.pack_caveats(pseudo, s.cleaning_audit, roles, "strong"),
             *(domain_pack.describe_kpi(pseudo, k.name, {r: r for r in roles}, kinds) for k in pseudo.kpis
               if domain_pack.computable(pseudo, k.name, roles)),
             *(domain_pack.rows_for(clean, contract, c)[1] for c in clean.columns),
             *(d.reason for d in s.cleaning_audit.decisions)]
    assert _leaks(texts) == [], [t for t in texts if LEXICON.search(t)][:5]
