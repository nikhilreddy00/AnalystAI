# The prose judge's labelling guide (frozen 2026-11-15, before any judge prompt)

Each sentence is report free text (GLOSSARY: Free text). Label it **yes** (an overreach) or **no**.
`kind` names what a yes asserts (`causal` or `benchmark`); on a no, `near` names what it could be
mistaken for (`causal`, `benchmark` or `none`). Both sets, this guide and their hashes are pinned
by `tests/test_prose_judge.py`; the held-out set is never shown to anyone tuning the prompt.

## yes: causal
The sentence asserts, as its own claim, that one factor made another happen or change: a cause, a
driver, an effect, an impact ("the promotion lifted sales", "low pay is driving attrition", "churn
fell because onboarding was strong", "marketing spend is the real driver", "the rate cut drove
balances up", "after the price increase customers left" when told as the reason).

## yes: benchmark
The sentence judges a KPI's level or a period's performance as good or bad against a norm, said or
implied: healthy, strong, solid, robust, excellent, world-class, best-in-class, top-quartile, above
the benchmark or median, poor, weak, a weak spot, a weakness, impressively low ("NRR had a strong
quarter", "churn remains a weakness", "logo churn is impressively low", "signalling healthy growth").

## no
- **Arithmetic of parts.** A total's change put down to its own parts: a region, a segment, a plan,
  a move of the same measure (new, expansion, upsell, contraction, churn), named accounts that left
  ("net sales grew 8%, driven by the North region"; "NRR fell, driven by contraction outpacing
  expansion"; "MRR fell because three accounts left").
- **Statistics, not verdicts.** The strength of an association or a pattern ("a strong
  correlation", "a weak association", "a strong seasonal pattern", "a sharp drop", "a strong
  rebound"): words about the size of a change or a link, not the KPI's level.
- **A rank or comparison inside the data.** "the strongest segment", "the higher-earning region",
  "East leads".
- **Asking, hedging or denying.** An investigation or a recommendation to find a cause ("investigate
  what drives…", "run a controlled test to see whether…"), a possibility ("may have contributed",
  "could be driving"), a denial ("does not establish cause", "the campaign did not cause it").
- **Certainty and data quality.** "clearly", "proven", "half the units are missing", "the result
  could be chance": other checks read those.
- **Words about a third thing.** A verdict word on something other than a KPI's level ("churn among
  healthy accounts", "weak markets", "strong headwinds", "poor-fit accounts").

## Ambiguous
A sentence that two careful readers could label differently is left out of the held-out set.
