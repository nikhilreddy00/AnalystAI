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
   - a cause is always said as an association (the data never shows how its groups were
     assigned), and only from a correlation, breakdown or test that names both sides;
   - a benchmark verdict is never said, because the data holds no norm; its measured metric is;
   - a comparison's direction is read from the two values and its measure from the breakdown,
     never from the model;
   - evidence short of high confidence is said in the sentence ("may not hold").
2. **A binary judge for free text.** What the model writes outside a typed claim goes to one
   batched call (`agents/prose_judge.py`) asking one question: does a sentence assert a cause or
   a benchmark verdict? A flagged sentence is removed and the report says so. The judge is
   calibrated on frozen labelled tuning sets and reported on a held-out set (TPR and TNR ≥ 0.9),
   with the prompt, set and model pinned by hash.
   - **Amended 2026-11-16 (5.7b-3 paid runs): the judge runs on the primary model, not Haiku.**
     v2 on Haiku scored held-out TPR 0.98 / TNR 0.90 on curated sentences, but on real reports it
     flagged 16% of sentences, ~13% of them correctly (arithmetic of parts, ranks, the aims of
     recommended actions), and quality fell from 0.90 to 0.36. v3 (a second frozen tuning set of
     real report sentences) on Haiku stayed at tuning TNR ~0.85; on Sonnet 4.6 it reached held-out
     TPR 0.92 [0.81, 0.97] and TNR 0.94 [0.84, 0.98].
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
- One judge call (~$0.01, ~10 s, Sonnet 4.6) per insight report, per drill-down answer and per
  first quality review: ~$0.01–0.03 per analysis. A judge failure is said ("free text
  unchecked"), never passed as checked; the benchmark phrases are removed even then.
- At TNR 0.94 a report of ~15 free sentences still loses a sound one now and then (q-31: "the
  strongest of the three segments"); the removal is said in a caveat.
- The $0 quality suite replays the judge's recorded replies (`QUALITY_JUDGE_REPORT`); a case
  without one fails. `evals.run --replay` re-measures a judge prompt on stored insight and quality
  runs, paying for the judge alone.
- A judge prompt change needs a new held-out run; the test refuses a stale one.
- The regexes for certainty, profit and the other pack `never_say` rules stay; they are keyword
  lists, not grammar, and are out of this ADR's scope.
