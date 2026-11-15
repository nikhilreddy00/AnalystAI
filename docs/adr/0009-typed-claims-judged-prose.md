# ADR-0009: Claims are typed and checked by code; leftover prose goes to a calibrated judge

Status: accepted (Phase 5.7b-3, 2026-11-15)

## Context
Causal and benchmark claims in the insight report were judged by regexes: `claim_causal_overreach`
(a causal verb list plus each pack's `causal_triggers`) and SaaS's benchmark-verdict `never_say`
pattern. Each audit found a sentence the regex missed or over-matched, so the regex was retuned:
causal and profit regexes every Domain 1 pass, the benchmark pattern more than 10 times over 13
Domain 6 passes (retro ledger D1-09, PR1-08). "NRR had a strong quarter" and "churn remains a
weakness" still passed (D6-12). A regex over English grammar has no fixed point.

## Decision
1. **Typed claims.** The insight model gives each causal, benchmark and comparison claim as data
   (`{kind: causal, cause, effect, evidence_id}`, `{kind: benchmark, metric, verdict, source}`,
   `{kind: comparison, metric, a, b, evidence_id}`). Code checks each one against the evidence and
   writes its sentence (`agents/claims.py`):
   - a cause is said as a cause only on a controlled test's evidence, otherwise as an association;
   - a benchmark verdict is never said, because the data holds no norm; its value is;
   - a comparison's direction is read from the two values, never from the model.
2. **A binary judge for free text.** What the model writes outside a typed claim goes to one
   batched Haiku call (`agents/prose_judge.py`) asking one question: does a sentence assert a
   cause or a benchmark verdict? A flagged sentence is removed and the report says so. The judge is
   calibrated on a frozen labelled tuning set and reported on a held-out set (TPR and TNR ≥ 0.9),
   with the prompt, set and model pinned by hash.
3. **Deleted:** the causal regexes, every pack's `causal_triggers` and the benchmark `never_say`
   rule. A short, domain-neutral benchmark-phrase list stays as a $0 pre-check.

## Alternatives considered
- **Keep tuning the regexes with bigger labelled sets.** This is what Domains 1–6 did; the class
  recurred in three domains.
- **Let the quality critic catch it.** The critic runs on the first run only and is tuned for
  precision on its own defects; a hard rule needs a calibrated, measured check.
- **Only typed claims, no judge.** The model can still write a cause in free text; something must
  read it.

## Consequences
- One Haiku call (~$0.002, ~1–2 s) per insight report, per drill-down answer and per first quality
  review. A judge failure is said ("free text unchecked"), never passed as checked.
- The $0 quality suite replays the judge's recorded replies through `RecordedClient`.
- A judge prompt change needs a new held-out run; the test refuses a stale one.
- The regexes for certainty, profit and the other pack `never_say` rules stay; they are keyword
  lists, not grammar, and are out of this ADR's scope.
