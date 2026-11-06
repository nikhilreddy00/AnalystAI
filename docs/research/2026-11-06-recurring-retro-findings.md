# Why retro findings and audit bugs recur (Domains 1–6), and what changes

Written 2026-11-06, after Domain 6 (SaaS) and its two review PRs.

**Sources:**
- the `/retro` reports of Domains 3, 4, 5 and 6 (session logs; Domains 1–2 had none, so their
  lessons in memory stand in);
- `docs/baselines/phase5_7_*.md`;
- the git history;
- a web research pass (citations inline);
- two $0 pilots run on this code today.

## 1. The headline

**Findings fixed in code stopped recurring. Findings fixed in prose came back.**

| Fixed as | Examples | Recurred? |
|---|---|---|
| Code (a test, a hook, a script) | the cost estimator and `spend.py`, the pre-push hook, the filter partition test, the `pkill` hook, `domain_diff.py` | No, for that class |
| Prose (a memory lesson, `CODING_STANDARDS.md`, a ROADMAP step) | "list a widened rule's readers" (D4), "audit before merge" (D2), the design skills in plan mode (D4/D5), "design period rules once" (D1) | Yes, every time |

Prose is read by the agent that is already under the most context pressure, at the moment it
is least likely to look. Code runs whether anyone remembers it or not.

## 2. Retro findings by theme and domain

| Theme | D1 | D2 | D3 | D4 | D5 | D6 | Fix applied | Held? |
|---|---|---|---|---|---|---|---|---|
| **A. A fix judged on its own example** (neighbouring variants break) | ✓ | | ✓ | ✓ | ✓ | ✓ | Lessons 3, 20, 22, 26, 31; a CODING_STANDARDS rule; a one-definition guard per class | **No: passes 3 → 5 → 9 → 13** |
| **B. A check fires late** (an audit or the browser finds what a test should) | ✓ | | ✓ | ✓ | ✓ | ✓ | A new test per class, after it bit | Per class yes; a new class escapes every domain |
| **C. A process step skipped or done out of order** | | ✓ | | | ✓ | ✓ | ROADMAP text and memory | **No**: D5 Spec review after the merge; D6 design skills skipped |
| **D. CI and push time** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | Hook (D3), zero-suites in the hook (D4), tree check (D5); D6: 4 shards, one run per change, 3-min hook | D6 reversed the trend (21 → 8 min CI, 8 → 3 min push) |
| **E. Steering files grow** | | | ✓ | ✓ | | ✓ | Moved content out twice | No cap, so grew back; **now capped by a test** |
| **F. Eval cost estimates** | ✓ | ✓ | ✓ | ✓ | ✓ | | Estimator code, `spend.py` | **Held in D6** (code) |
| **G. Tooling gaps** | | | ✓ | ✓ | ✓ | ✓ | A script each | Held (code) |

**Audit passes per domain (majors per pass):**

| Domain | Majors per pass | Passes |
|---|---|---|
| D1 | 28→5→6→2 (capped) | 4 |
| D2 | 9→3→2→0 | 4 |
| D3 | 14→3→0 | 3 |
| D4 | 11→3→3→1→0 | 5 |
| D5 | ~30→6→3→2→0→0, then 4→1→0 | 9 |
| D6 | 9→2→2→2→1→1→3→2→1→1→1→1→3, plus 2 post-merge reviews | 13 |

The audit tail is the whole time overrun in D5 (7.2 h vs 4.5 h) and D6 (6.8 h vs 4.5 h).

## 3. Why the audits keep finding bugs

1. **Every guard checks examples someone already thought of.** The golden, the replay, the
   per-dataset diffs and the oracle's datasets are all fixed examples. An LLM auditor draws new
   examples from the same imagination, so each pass finds a few more and never exhausts the
   space.
2. **A fix is tested on the example that exposed it.** In Domain 6:
   - the snapshot fix broke one-snapshot files;
   - the blank-input note missed optional inputs;
   - the regex fix hid claims next to the new words.
3. **Self-review doesn't converge.** LLMs don't reliably find their own reasoning errors without
   external feedback ([Huang et al. 2023](https://arxiv.org/abs/2310.01798)). LLM judges also
   favour their own outputs ([Panickssery et al. 2024](https://arxiv.org/abs/2404.13076)). And
   "a reviewer that's asked to find problems will always find *something*"
   ([Claude Code best practices](https://code.claude.com/docs/en/best-practices)). The stop rule
   "an auditor found nothing" has no fixed point.
4. **The same rule lives in several places, and a regex judges prose.** Both multiply the
   surface every fix must cover: classes 1–4 and 8 of PR #1.

## 4. Evidence from today's $0 pilots

| Pilot | Setup | What it found |
|---|---|---|
| **Property-based test** (Hypothesis): "the MRR bridge reconciles per segment" on generated subscription books | ~40 lines, 3.5 min | **Recurring class #3**, shrunk to a minimal case: one customer, 2 rows, SMB → Ent; SMB's bridge is off by $100. Several audit passes had to find this class by hand. |
| **Mutation testing** (mutmut 3.8) on `pack_columns` against its own tests | Config only, ~10 min | **134 of 189 mutants killed (71%), 55 survived.** For example, the "only 1s, scale unclear" branch can be deleted with no test failing. This is recurring class #7 (discount scale): its logic is barely pinned by tests, so each fix there could break a neighbour unseen. |
| **Metamorphic transforms** (`next_bill`, `one_snapshot`) plus a **shuffled test order** | ~40 lines | Two order-dependent leaks: a cached session shared across tests, and pseudonyms read as card numbers. The second was a real PII-guard bug, behind the "flaky" PHI test. |

## 5. What senior engineers use instead

Each method, with the bugs it catches mechanically:

1. **Property-based testing** ([Hypothesis](https://hypothesis.readthedocs.io/en/latest/stateful.html)).
   - State the invariant; the tool generates and shrinks the variants.
   - Invariants: the bridge identity (per segment and in total); partition (Σ over a filter's
     values = the total); source, shuffle and scale invariance; "a KPI is a number with evidence
     or explicitly unavailable, never a 0 from missing data".
   - Catches class A, and recurring classes 2, 3 and 5.
2. **Metamorphic testing** ([Hillel Wayne](https://www.hillelwayne.com/post/metamorphic-testing/)).
   - The oracle's transforms, generalised and run on every pack.
   - Real data has no truth, but its transformed copies must agree.
3. **Mutation testing as the "is this fix tested?" gate**
   ([Google, State of Mutation Testing](https://research.google/pubs/state-of-mutation-testing-at-google/);
   [mutmut](https://mutmut.readthedocs.io/en/latest/)).
   - Run only on the functions a change touched.
   - A surviving mutant on a changed line blocks "done".
   - Replaces a reviewer's judgement of "tested enough".
4. **Differential testing against one naive reference implementation**
   ([Csmith](https://users.cs.utah.edu/~regehr/papers/pldi11-preprint.pdf)).
   - A ~100-line loop-based `reference_kpis.py`.
   - The only independent oracle; the eval scorer's re-derivations are deleted (class 3/4).
5. **One definition by construction**
   ([parse, don't validate](https://lexi-lambda.github.io/blog/2019/11/05/parse-don-t-validate/);
   [import-linter](https://import-linter.readthedocs.io/en/stable/)).
   - A `Window` type built by one function.
   - `KPIValue = Measured | Unavailable`.
   - Replaces grep guards that only catch the spelling someone thought of (classes 1, 2, 5).
6. **Typed claims instead of regex judges of prose**
   ([Hamel Husain, LLM judges](https://hamel.dev/blog/posts/llm-judge/)).
   - The insight agent emits `{kind: causal | benchmark, …}`; code checks the claim.
   - Leftover prose goes to a calibrated binary judge (Haiku, a ~50-case labelled set).
   - Class 8.
7. **Close a finding with a check, not a sentence**
   ([Google SRE postmortems](https://sre.google/sre-book/postmortem-culture/)).
   - Every audit or retro finding gets a class label.
   - It is closed only by a test, hook, type or property; prose-only fixes are marked
     "unenforced".
   - Recurrence is counted per class per domain.

## 6. Proposed changes (for the 5.7b plan, to decide together)

| # | Change | Replaces | Setup |
|---|---|---|---|
| 1 | Hypothesis properties over every pack: bridge identity, partition, source and shuffle invariance, unavailable-never-0 | Hand-written variant tests; most of audit passes 3–13 | ~2 days, then ~1 h per domain |
| 2 | Mutation gate (mutmut) on the functions a domain changes; 0 surviving mutants on changed lines before "done" | The reviewer's "is it tested enough?" | ½ day |
| 3 | `Window` / `KPIValue` types + import-linter contracts | The grep guards; classes 1, 2, 5 | 2–3 days (with the `domain_pack.py` split) |
| 4 | Typed claims + a calibrated Haiku judge for leftover prose | The causal and benchmark regexes (class 8) | 2–3 days + ~$2 calibration |
| 5 | One naive reference implementation as the differential oracle | The scorer's re-derivations (classes 3, 4) | 1–2 days |
| 6 | **The audit changes role.** 1–2 invariant-focused reviewer passes, after 1–5 are green, each checking "is property P_i generated, and is every changed function's mutant score 100%?" Stop rule: properties and mutation gate green. | 5–13 diff-focused passes with no fixed point | Prompt change only |
| 7 | **Retro ledger.** A finding closes only with a check; a recurrence count per class | Prose lessons | ~1 h |

**Measure on Domain 7:** audit-found majors per class, and the pass count. The target is at most
2 passes. This is a judgement from the sources above, not yet a measured number.

## 7. Done today (retro of Domain 6, all as code)

| Retro item | Fix |
|---|---|
| 1 | CLAUDE.md: plan mode is done only when every ROADMAP 5.7 step is checked off by name |
| 2 | Metamorphic `next_bill` and `one_snapshot` on every pack |
| 3 | `WINDOW_MONTHS` + an AST guard over every shared string |
| 4 | `evals.snapshot text <ref>` and `domain_diff --show` |
| 5 | Nightly shuffled-order CI; found and fixed 2 leaks |
| 6 | CLAUDE.md 235 → 175 lines, capped by a test |
| 7 | Lessons pruned 33 → 23 (enforced ones removed); ROADMAP cell 4,768 → 763 chars |
