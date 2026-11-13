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
    monkeypatch.setattr(domain_pack.pack, "load_packs", lambda: tuple(
        p if x.domain == domain else x for x in domain_pack.__dict__["_real_load"]()))
    assert domain_pack.pack_for(domain) is p   # the patch reaches the readers (else: no check)
    return p


domain_pack.__dict__.setdefault("_real_load", domain_pack.load_packs)


def _leaks(texts: list[str], lexicon: re.Pattern = LEXICON) -> list[str]:
    return sorted({m.group(0).lower() for t in texts for m in lexicon.finditer(t)})


def test_text_defaults_say_no_domain_word():
    """A pack that doesn't override a sentence gets the default: it must fit every domain."""
    assert _leaks(list(TEXT_DEFAULTS.values()), _lexicon("")) == []


_TOKEN = re.compile(r"[a-z][a-z]+")
# English's function and time words: never a domain's (the domain words are built from the packs;
# "left out", "months", "when it covers less" are the product's own English)
_STOP = set("the and an no not with from one of a to in on by is per at or as all each its new "
            "who are so only more first came months when left their span fewer".split())


def _only_its_words(domain: str) -> set[str]:
    """C9 (ROADMAP 5.7b-2 step 8): the words only this pack says, built from every pack (never a
    hand list): its KPI labels' and glossary phrases' words no other pack uses, and its spell's
    words no other spell's words use ("churn" is HR's glossary word too, never its spell's)."""
    def tokens(said) -> set[str]:
        return {t for w in said for t in _TOKEN.findall(str(w).lower())}

    def vocab(p) -> set[str]:
        return tokens([*(k.label for k in p.kpis), *(w for ws in p.glossary.values() for w in ws)])

    def spell(p) -> set[str]:   # its names (customers, Monthly churn), not its sentences' English
        w = p.spell.words.model_dump(exclude={"moves"}) if p.spell else {}
        return tokens(v for v in w.values() if len(v.split()) <= 3)
    packs = domain_pack.load_packs()
    others = [p for p in packs if p.domain != domain]
    its = pack_for(domain)
    generic = tokens(domain_pack.pack.SpellWords().model_dump(exclude={"moves"}).values())
    return ((vocab(its) - set().union(*map(vocab, others)))
            | (spell(its) - set().union(*map(spell, others)) - generic)) - _STOP


def _wordless(domain: str):
    """The pseudo pack with its spell's words replaced too: every sentence they are in."""
    p = _pseudo(domain)
    w = p.spell.words

    def token(name: str, text: str) -> str:   # its placeholders kept: what fills them is checked
        return " ".join([f"⟦{name}⟧", *re.findall(r"\{\w+\}", text)])
    words = w.model_copy(update={**{f: token(f"w_{f}", getattr(w, f)) for f in type(w).model_fields
                                    if isinstance(getattr(w, f), str) and getattr(w, f)},
                                 "moves": {m: token(f"move_{m}", t) for m, t in w.moves.items()}})
    return p.model_copy(update={"spell": p.spell.model_copy(update={"words": words})})


SPELL_DATASETS = [("saas", n) for n in ("saas_mrr_snapshots", "saas_subscriptions", "subscriptions",
                                        "saas_accounts")] + [
    ("hr", n) for n in ("hr_roster", "hr_snapshots", "employees")]


@pytest.mark.parametrize("domain, name", SPELL_DATASETS)
def test_the_spell_and_ledger_sentences_say_only_the_packs_words(domain, name, monkeypatch):
    """PR1-09 (C9): the ledger's sentences (the bridge, monthly churn, each move's How) were
    SaaS's words in shared Python. On a pack whose every word is a token, and data whose columns
    are neutral, the spell story and each KPI's How hold no word only that pack says."""
    from tests.test_oracle import _built
    own = _only_its_words(domain)   # from the real packs, before the patch
    assert {"churn", "customers"} <= _only_its_words("saas") and "people" in _only_its_words("hr")
    p = _wordless(domain)
    monkeypatch.setattr(domain_pack.pack, "load_packs", lambda: tuple(
        p if x.domain == domain else x for x in domain_pack.__dict__["_real_load"]()))
    _, clean, contract, _ = _built(name)
    neutral = {c: f"col{i}" for i, c in enumerate(clean.columns)}
    df = clean.rename(columns=neutral)
    roles = {r: neutral[c] for r, c in contract.roles.items() if c in clean.columns}
    texts = [*domain_pack.spell_story(p, df, roles), *domain_pack.spell_facts(p, df, roles),
             *(domain_pack.describe_kpi(p, k.name, roles, parts=True) for k in p.kpis
               if domain_pack.computable(p, k.name, roles)),
             *(domain_pack.window_words(p, df, roles, k.name) for k in p.kpis),
             *(domain_pack.unavailable_words(domain_pack.Unavailable(r, (p.spell.end,)), p)
               for r in __import__("typing").get_args(domain_pack.pack.Reason)
               if r != "small_cell")]   # 'fewer than 11 people' in every domain (GLOSSARY Small cell)
    # a token and the pack's own value lists (said through `where`) are its words
    texts = [re.sub(r"⟦[^⟧]*⟧|\[[^\]]*\]| where .*", " ", t) for t in texts]
    # the data's own values (a plan called Growth, a Legal department) are the user's words
    own -= {w for c in clean.select_dtypes(exclude="number") if clean[c].nunique() <= 50   # not ids
            for v in clean[c].dropna().unique() for w in _TOKEN.findall(str(v).lower())}
    leaks = sorted({w for t in texts for w in _TOKEN.findall(t.lower()) if w in own})
    assert texts and not leaks, [t for t in texts if set(_TOKEN.findall(t.lower())) & own][:3]


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
