"""Plan v2 R4 (pseudo-localization): a copy of the banking pack whose every word is a marked
token. Every producer of pack text (brief, row rules, caveats, KPI descriptions, the rows note,
tiles) runs on its data; a word from another domain's lexicon in the output came from shared
code, not the pack: a leak the next domain would show its users too."""
import re

import pytest

import domain_pack
from domain_pack import ROW_KINDS, TEXT_DEFAULTS, pack_for

# words only a domain's own pack may say: a run on one domain's data must say none of the
# OTHER domains' words (each new domain adds its own)
WORDS = {"retail": r"sales?|orders?|returns?|refund(?:ed|s)?|cancell?ed|products?|sku|basket|"
                   r"shoppers?|non-product",
         "banking": r"loans?|deposits?|npl|delinquen\w*|overdrafts?|on-book|charge-?offs?",
         "healthcare": r"patients?|readmi\w*|discharges?|claims?|payers?|denial\w*|alos|"
                       r"encounters?|members?",
         "insurance": r"premiums?|polic(?:y|ies)|reserves?|incurred|claims?|claimants?|"
                      r"valuations?|underwrit\w*|exposures?|loss ratio|accident years?",
         "hr": r"employees?|headcount|attrition|turnover|separations?|hires?|leavers?|"
               r"salar(?:y|ies)|tenure|fte|contractors?|discharges?"}
LEXICON = re.compile(rf"\b({WORDS['retail']})\b", re.I)   # TEXT_DEFAULTS: fit every domain


def _lexicon(domain: str) -> re.Pattern:
    """The other domains' words, less the ones this domain shares (both say 'claims')."""
    def alts(ws: str) -> list[str]:   # top-level alternatives: refund(?:ed|s)? stays whole
        return re.split(r"\|(?![^(]*\))", ws)
    own = set(alts(WORDS.get(domain, "")))
    words = [w for d, ws in WORDS.items() if d != domain for w in alts(ws) if w not in own]
    return re.compile(rf"\b({'|'.join(words)})\b", re.I)


DATASETS = [("banking", n) for n in ("loan_tape", "card_ledger", "bank_statement", "ledger_drcr")] + [
    ("healthcare", n) for n in ("inpatient", "health_claims")] + [
    ("insurance", n) for n in ("policies", "loss_run", "claims")] + [
    ("hr", n) for n in ("hr_roster", "hr_snapshots", "employees")]


def _pseudo(domain: str = "banking"):
    real = pack_for(domain)
    return real.model_copy(update={
        "row_labels": {k: f"⟦label_{k}⟧" for k in ROW_KINDS},
        "row_flags": {k: f"⟦flag_{k}⟧" for k in ROW_KINDS},
        "text": {k: f"⟦{k}⟧" for k in TEXT_DEFAULTS},
        "caveats": {k: f"⟦caveat_{k}⟧" for k in real.caveats},
        "kpis": [k.model_copy(update={"label": f"⟦kpi_{k.name}⟧", "phrase": f"⟦kpi_{k.name}⟧"})
                 for k in real.kpis],
        "definitions": {k: f"⟦def_{k}⟧" for k in real.definitions},
        "unsupported": {k: f"⟦needs_{k}⟧" for k in real.unsupported},
        "role_caveats": {k: f"⟦role_{k}⟧" for k in real.role_caveats},
    })


@pytest.fixture
def pseudo(monkeypatch, request):
    domain = getattr(request, "param", "banking")
    p = _pseudo(domain)
    monkeypatch.setattr(domain_pack, "load_packs", lambda: tuple(
        p if x.domain == domain else x for x in domain_pack.__dict__["_real_load"]()))
    return p


domain_pack.__dict__.setdefault("_real_load", domain_pack.load_packs)


def _leaks(texts: list[str], lexicon: re.Pattern = LEXICON) -> list[str]:
    return sorted({m.group(0).lower() for t in texts for m in lexicon.finditer(t)})


def test_text_defaults_say_no_domain_word():
    """A pack that doesn't override a sentence gets the default: it must fit every domain."""
    assert _leaks(list(TEXT_DEFAULTS.values()), _lexicon("")) == []


@pytest.mark.parametrize("pseudo, name", DATASETS, indirect=["pseudo"])
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
    lexicon = _lexicon(pseudo.domain)
    texts = [re.sub(r"\[[^\]]*\]", "[…]", t) for t in texts]   # the pack's own value lists
    assert _leaks(texts, lexicon) == [], [t for t in texts if lexicon.search(t)][:5]
    if pseudo.version_key and set(pseudo.version_key) <= set(roles):   # the rule is said
        assert any("later" in t and pseudo.version_order in t for t in texts)
