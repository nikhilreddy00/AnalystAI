"""The prose judge (5.7b-3, ADR-0009): which sentences of a report's free text state a cause or a
benchmark verdict (GLOSSARY Free text). One batched call on the fast model, calibrated on a
frozen labelled set and measured on a held-out one (evals/cases/prose_judge; `python -m
evals.judge prose`). A few benchmark phrases are flagged without a call. Callers are async and
pass the result into their sync checks; a failed judge returns None, which the caller says."""
from __future__ import annotations

import json
import re
from pathlib import Path

from agents.errors import AgentError
from api.config import settings
from guardrails import UNTRUSTED_NOTE, delimit_untrusted, mask_pii
from llm.client import LLMClient, trace_verdict

PROMPT_VERSION = "v3"   # v1 asked for the flagged numbers only: Haiku skimmed (tuning TPR 0.52);
# v2 flagged 16% of real report sentences, ~13% of them overreach (parts, ranks, action aims).
# v3 runs on the pinned primary model: on Haiku its tuning TNR stayed ~0.85 (one false flag in
# every report of ~12 sentences); on Sonnet 0.97 at ~$0.01 a call.
_SYSTEM = (Path(__file__).parent.parent / "prompts" / "prose_judge" / f"{PROMPT_VERSION}.md"
           ).read_text() + "\n" + UNTRUSTED_NOTE
# Phrases that only ever grade against an outside norm, in any domain: flagged at $0 (no call).
BENCHMARK_PHRASES = ("best in class", "world class", "top quartile", "bottom quartile",
                     "industry average", "industry benchmark", "industry standard")


def phrase_hit(sentence: str) -> bool:
    s = re.sub(r"[-‐‑–—]", " ", sentence.lower())
    return any(p in s for p in BENCHMARK_PHRASES)


async def flagged(sentences: list[str], llm: LLMClient) -> set[int] | None:
    """The indices of `sentences` that state a cause or a benchmark verdict; None when the judge
    failed (no reply, or one that isn't the JSON asked for)."""
    hits = {i for i, s in enumerate(sentences) if phrase_hit(s)}
    ask = [i for i in range(len(sentences)) if i not in hits]
    if not ask:
        return hits
    body = "\n".join(f"{n}. {mask_pii(sentences[i])}" for n, i in enumerate(ask))
    try:
        result = await llm.complete(agent="prose_judge", prompt_version=PROMPT_VERSION,
                                    model=settings.llm_model, max_tokens=1000, system=_SYSTEM,
                                    messages=[{"role": "user", "content": delimit_untrusted(body)}])
        # the first JSON object (the model may explain after it): a verdict per sentence, by number
        says = json.loads(re.search(r"\{[^{}]*\}", result.text).group())
        says = {k: v.split(":")[0].strip() for k, v in says.items()}   # "cause: <factor> -> <measure>"
        if set(says) != {str(n) for n in range(len(ask))} \
                or not set(says.values()) <= {"cause", "verdict", "neither"}:   # unjudged, or an unknown label
            raise ValueError
        return hits | {i for n, i in enumerate(ask) if says[str(n)] in ("cause", "verdict")}
    except (AgentError, AttributeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        trace_verdict("prose_judge", PROMPT_VERSION, "judge_failed")
        return None
