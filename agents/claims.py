"""Typed claims (5.7b-3, ADR-0009, GLOSSARY Typed claim): a cause, a benchmark verdict or a
comparison the insight model gives as data. Code checks each against the evidence it cites and
writes its sentence, so the model never words one: a cause is said as an association (the data
never shows how its groups were assigned), a benchmark verdict is never said (the data holds no
outside norm), and a comparison's direction is read from its two values."""
from __future__ import annotations

import re
from typing import Callable

from pydantic import ValidationError

from agents.analysis_agent import findings_by_id
from guardrails import rankable
from orchestrator.state import AnalysisFindings, Claim, SegmentResult, TypedClaim

_NAME = 60   # a cause, an effect, a metric or a group is a name, never a sentence


def _norm(s) -> str:
    return re.sub(r"[\s_]+", " ", str(s).lower()).strip()


def typed(raw) -> TypedClaim | None:
    """The model's claim as a TypedClaim, or None when it isn't one: an unknown kind, or a name
    that is long or holds a sentence (a field can carry an instruction or a second claim)."""
    try:
        t = TypedClaim.model_validate(raw)
    except ValidationError:
        return None
    names = (t.cause, t.effect, t.metric, t.a, t.b)
    return None if any(len(n) > _NAME or re.search(r"[.!?\n]", n) for n in names) else t


def _pair(t: TypedClaim, findings: AnalysisFindings) -> tuple[float, float, bool] | None:
    """(a's value, b's value, a 0-1 ratio) from the cited breakdown, or None when either group
    isn't one of its rankable groups."""
    s = findings_by_id(findings).get(t.evidence_id)
    if not isinstance(s, SegmentResult) or _norm(t.a) == _norm(t.b):
        return None
    by = {_norm(r.get("name")): r["value"] for r in s.results
          if isinstance(r.get("value"), (int, float)) and rankable(r.get("name"))}
    a, b = by.get(_norm(t.a)), by.get(_norm(t.b))
    if a is None or b is None:
        return None
    return a, b, s.aggregation == "ratio" and all(0 <= v <= 1 for v in by.values())


def _named(name: str, ev) -> bool:
    """`name` is whole words of the evidence's claim or id (a metric under another's label, or a
    one-letter name inside a word, is not)."""
    n = _norm(name)
    said = _norm(ev.claim) + " " + _norm(ev.id)
    return bool(n) and re.search(rf"(?<![^\W_]){re.escape(n)}(?![^\W_])", said) is not None


# a breakdown's aggregation, said (SegmentResult: sum | mean | count | pct | ratio)
_AGG = {"mean": "average", "sum": "total", "count": "count of", "pct": "percent of"}

# what an association can cite: a computed co-movement or group difference (never a trend line,
# an outlier or a profile)
_ASSOCIATED = ("correlation", "segment", "test")


def check(t: TypedClaim, findings: AnalysisFindings) -> str | None:
    """None when the claim holds against its evidence, else why it is refused (a rule label)."""
    if t.kind == "benchmark":   # its metric is one the analysis measured
        if not t.metric:
            return "no_metric"
        return None if any(_named(t.metric, e) for e in findings.evidence) else "metric_not_in_evidence"
    ev = next((e for e in findings.evidence if e.id == t.evidence_id), None)
    if ev is None:
        return "unknown_evidence"
    if t.kind == "causal":   # two different names, both the cited association's own
        if ev.kind not in _ASSOCIATED:
            return "not_an_association"
        return None if _named(t.cause, ev) and _named(t.effect, ev) \
            and _norm(t.cause) != _norm(t.effect) else "names_not_in_evidence"
    return None if _pair(t, findings) else "groups_not_in_evidence"


def render(t: TypedClaim, findings: AnalysisFindings, name: Callable[[str], str] = str) -> str:
    """The claim's sentence, written by code (`name`: the report's display names). Call only on
    a claim `check` passed."""
    def up(s: str) -> str:
        s = name(s)
        return s[:1].upper() + s[1:]
    if t.kind == "causal":
        return (f"{up(t.cause)} and {name(t.effect)} move together in the data: an association, "
                "not proof that one causes the other.")
    if t.kind == "benchmark":
        return (f"{up(t.metric)} is reported without a verdict: this data holds no outside "
                "benchmark to judge it against.")
    a, b, rate = _pair(t, findings)
    s = findings_by_id(findings)[t.evidence_id]   # the breakdown's own measure, never the model's label
    what = (name(s.label) if s.label else
            f"{_AGG.get(s.aggregation, s.aggregation)} {name(s.metric_column)}").replace("_", " ")
    show = (lambda v: f"{v * 100:.1f}%") if rate else (lambda v: f"{v:.1f}%") \
        if s.aggregation == "pct" else (lambda v: f"{v:,.2f}")
    way = "higher than" if a > b else "lower than" if a < b else "the same as"
    return f"{what[:1].upper() + what[1:]}: {t.a} ({show(a)}) is {way} {t.b} ({show(b)})."


def settle(raw: list, findings: AnalysisFindings,
           name: Callable[[str], str] = str) -> tuple[list[Claim], list[str], int]:
    """The model's typed claims: (claims for the findings, a cause or a comparison; caveat lines,
    a benchmark verdict left unsaid; how many were refused by `check`)."""
    claims, caveats, refused = [], [], 0
    for item in raw if isinstance(raw, list) else []:
        t = typed(item)
        if t is None or check(t, findings) is not None:
            refused += 1
            continue
        if t.kind == "benchmark":
            caveats.append(render(t, findings, name))
        else:
            claims.append(Claim(text=render(t, findings, name), evidence_ids=[t.evidence_id], typed=t))
    return claims, caveats, refused


def recheck(claim: Claim, findings: AnalysisFindings, name: Callable[[str], str] = str) -> str | None:
    """A typed claim in a stored report: None when it still holds and its sentence is the one
    code writes for it, else why not (an edited sentence is no longer the claim's)."""
    t = claim.typed
    if t is None:
        return None
    why = check(t, findings)
    return why or (None if claim.text == render(t, findings, name) else "sentence_not_its_claim")
