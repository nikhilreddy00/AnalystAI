# The agent contract

How every AnalystAI agent is built, with the analysis agent (`agents/analysis_agent.py`) as the worked example. ADR-0001 records the decision; this guide records what it takes in practice.

The contract is a convention, not a framework. An agent is still one `async def run_<agent>(session, llm) -> <Output>`. There are no base classes and no registry. A shared helper exists only once a second agent needs it.

## The six parts

### 1. Agent card
A Pydantic `AgentCard` constant in the agent module: name, version, scope, inputs, outputs, known limits and refusal rules (`orchestrator/state.py`).
- It is rendered into the system prompt, so the model reads the same limits the UI will show.
- `version` is the prompt version, so traces and eval reports name the card they ran under.
- **Write limits you have measured, not ones you hope for.** The analysis card says "Pearson only", "MoM compares calendar-month totals (means for rates)" and "< 30 rows → low confidence", and each of these is enforced in code.

### 2. Prompt file
- The prompt lives at `prompts/<agent>/v<N>.md`.
- The agent loads it once with `Path.read_text()`. `PROMPT_VERSION` picks the file and is passed to `llm.complete(prompt_version=...)`.
- **A change is a new file, never an edit in place.** Then `git diff v6.md v7.md` *is* the change log, and every eval report and trace says which version produced it.
- The system prompt is `card.render() + prompt file + UNTRUSTED_NOTE`.

### 3. Context builder
- `build_context(session) -> <Agent>Context` returns a typed bundle, and `render()` turns it into the user message. The analysis context holds:
  - the task (metrics, segments, types, timeframe);
  - the schema;
  - 3 PII-masked sample values per column;
  - the cleaning decisions.
- Everything that came from the user's file goes through `delimit_untrusted()`, including column names, because a column can be named like an instruction.
- Trim to a budget. Today that's a cap of 60 columns and 20 notes (`ponytail:` naive, to be replaced by a token-based trim once evals include wide tables).

### 4. Tools
- Real tool calling goes through `llm.complete(tools=..., tool_choice=...)`, which returns `LLMResult.tool_calls` and the raw `content` blocks to echo back.
- Tools are read-only and run locally:
  - output from user data is masked (`mask_row`, `mask_pii`) and delimited;
  - an unknown argument returns `is_error`, it never raises.
- The loop is bounded: at most 3 tool rounds, then `tool_choice={"type": "none"}`. Tools stay defined in that last call because the history contains tool blocks.
- Traces record tool *names* only, never inputs.
- **Measure before you offer.** Analysis offers tools only on the guided retry. In smoke runs the model called `get_schema`/`sample_rows` on every first attempt, every lookup repeated what the context already held, and each cost +5s and +$0.02. After a rejection, looking at the data does help.

### 5. Output schema and evidence
- The output is a Pydantic model, and every finding gets an `Evidence` item with:
  - the claim;
  - the code that produced it;
  - a result excerpt;
  - the rows behind it;
  - a confidence level and its reason;
  - assumptions.
- **Build evidence deterministically from what ran; never ask the model to write it:**
  - confidence is a pure function (`confidence(n, p, r, imputed_pct)`);
  - row counts come from the executed code (`non_null`, per-trend `n`);
  - assumptions come from the cleaning audit, plus the model's notes filtered to the finding's own columns.
- **Empty output is impossible by construction:** if every finding section is empty, the agent retries once, then raises.

### 6. Guardrails
- Pure checks live in `guardrails.py`. Each returns `None` or a reason.
- The agent decides whether a reason is hard or soft, and how a hard rule is enforced. Analysis has four hard rules:
  1. the code AST allowlist, plus the column references in the code;
  2. findings are non-empty (EDA counts only for an EDA question);
  3. finding columns exist;
  4. every number in the prose is grounded in a computed value (`ungrounded_numbers`).
- Rules 1–3 get one guided retry, then raise a typed `AgentError`.
- Rule 4 is enforced by **rewriting** the offending sentence from the stored values (`ground_prose`), and the evidence records that it was rewritten. A retry cost ~25s per run and set the p95. A deterministic repair holds the rule by construction for free.
- Prefer a repair whenever the rule has a deterministic fix.
- Every rejection writes a trace line holding only the rule label (`trace_verdict`), since reasons quote data. Eval reports count `runs_with_guardrail_retry`.

## What the analysis agent taught us
- **Look for scorer and schema bugs before blaming the model.** `trends: list[TrendResult | dict]` coerced every dict into a model object, so the scorer's dict-only filter saw no trends. That made trend fail in every case in Phases 1 and 2, while the agent was reporting the right number.
- **Put one-right-answer statistics in deterministic code.** Asking the model for per-segment MoM and joint breakdowns doubled its output (3–4.6k tokens, 45–60s per call), and it still used a second MoM definition. `_build_standard_code` computes them for the requested types, and its MoM replaces the model's. The model keeps the judgment work: tests, anomalies, interpretation. Smoke cost fell from ~$0.08 to ~$0.03 per question.
- **A hard rule needs a precise check.** Grounding read `p=5.7e-40` as `5.7` and `40` and rejected correct prose. Before you make a check hard, run it over real output.
- **Delimit data wherever it surfaces, not just where it enters.** A cleaned frame can be just the injected row, and then a group name *is* the injection. The insight prompt delimited the head rows but not the segment names derived from data, and E2E caught the echo.
- **The rejection count is the latency budget.** p95 tracked the retry rate almost one for one. Prevent the causes in the prompt: the model wrote `department=Easy` as a column, and EDA-only answers counted as empty.
- **The evidence UI found bugs the eval couldn't.** One real run in the drawer showed `discount` (a rate) being summed for MoM and per-segment trends claiming all 3,025 rows. The evals don't score evidence, so read some by hand.
- **Security review on every commit pays off.** It caught four masking and delimiting gaps in shared guardrails: a fail-open PII name list, camelCase names, integer and float PII, and close-tag spellings. Fix those in the shared function, not at the caller.

## What the intake agent taught us (Phase 5, PR 1)
- **Read the old prompt for contradictions before tuning it.** v1 said "ask on zero signal" and
  "default to overview when ambiguous"; the model took the second every time. Removing the
  contradiction fixed 7 cases; no new instruction was needed.
- **Never copy eval cases into prompt examples, and hold some cases out.** A first draft of v2 used
  three phrasings from the eval; they were swapped for unseen ones. 12 holdout cases ran only in the
  final k=3.
- **A soft rule can still be enforced in code.** Invented columns get a guided retry, then the
  column is dropped and the user is asked. The hard rule holds by construction, and the user gets a
  question, not an error.
- **Shared memory belongs to one helper, not to each agent.** `conversation_context` feeds both
  Intake and the SQL agent. It is omitted when empty, so older prompts stay byte-identical and
  older suites need no re-run.
- **Delimit everything derived from data, including your own earlier outputs.** The audit found the
  previous plan (column names and the past question) sent undelimited.
- **Caching makes cheaper models cheaper only at the margin.** With the system prompt cached,
  Haiku saved 39% on an already small call, and it failed an injection case.

## What the visualization agent taught us (Phase 5, PR 2)
- **A deterministic agent still gets the whole contract, minus the prompt.** Card, context builder,
  rationale (its evidence), guardrails and a labelled eval all apply; only the prompt file and `llm`
  parameter drop out. Its eval costs $0, so it ran at k=3 for free.
- **Find every producer before you fix one.** Only the overview reached the visualization agent; five
  of six question types drew charts elsewhere (targeted handlers, chart requests, drill-down). One
  `finalize_charts` that every chart passes through is what makes "lint violations 0" true.
- **Keep the scorer independent of the agent.** The scorer has its own idea of dates and measures, so
  an agent bug can't be mirrored. A baseline-only inference gap (a donut keyed "name") was a scorer
  bug, fixed and re-scored for $0 before any agent change.
- **Use one definition across agents.** Trend charts reuse analysis's MoM rule (totals, means for
  0-1 values), so a chart's title states the same % as the finding; a test pins it.
- **Data-derived display text is data.** Titles now carry segment names; they reach the insight and
  critic prompts masked and delimited. The planted-injection test fails on the old code.
- **The user always wins, inside a fence.** The one-click switch only offers types that can't mislead
  (the server re-checks); a free-form request is honoured with a lint note. Overrides are logged by
  rule label, so the most-overridden rule becomes the next eval case.

## What the cleaning agent taught us (Phase 5, PR 3)
- **Read the prompt before you keep the model.** The cleaning prompt was a decision table the
  model repeated, so the LLM added cost and variance but no judgment. Rules that change the user's data
  must be reproducible: deterministic code, a $0 eval, p95 6.4s → 0.2s.
- **"Clean" is a policy, and the analyst's default is "don't invent".** Imputation biased every
  descriptive number (a filled sum fabricates revenue), and a mode fill copied the planted injection
  into every row of `notes`. Missing numbers now stay missing with n reported; only labels get an
  explicit `Unknown`.
- **Score from the diff, not from the agent's accounting.** The scorer compares the output frame
  with the generator's expected frame. Hard rules like "every row change is logged" are checked
  against what actually changed, so an agent can't pass by under-reporting.
- **Every eval needs traps, not just planted issues.** A cleaner that fixes everything also
  over-cleans. Codes with leading zeros, records that differ only by ID, and `A-1` vs `A1` gave the
  old agent its 32.5% over-cleaning; recall alone would have hidden that.
- **Fault tests find policy gaps that the planted cases miss.** Three identical outlier rows were
  "deduplicated", which led to the rule: dedup only when an ID column defines a record. The audit found
  that "don't drop anything" dropped, and that `NA` (North America) was read as missing.
- **When the same author writes the generator and the agent, say so.** A first-run 100% proves
  less than it looks. Holdouts, random-combination property tests and unit traps the generator doesn't
  plant are the mitigation; the domain datasets in Phase 5.5 are the real test.

## What the insight agent taught us (Phase 5, PR 4)
- **Don't feed one model's prose to another as fact.** The "November 2025" in every overview was
  the ingestion model's summary of 5 rows, passed on by insight. Compute the facts a narrative
  needs (row counts, the date range, which months a MoM compares) and give the model those.
- **Let the model write only what needs judgment.** The analyst tab and the alerts were
  paraphrases of the evidence, and that is where the invented methods were. Built by code, they
  can't drift, and the call fell from 2.8k to ~0.9k output tokens (p95 49 s → 18 s).
- **"Grounded" means "in what the model was shown".** The repair pool is the rendered context,
  not the session. That made the rule exact: derived numbers ("54% higher") and invented
  thresholds ("above $900") fail, so the model learns to state both values.
- **A judge counts only where it was calibrated.** The calibration set held statements only, so
  its labels on actions were unmeasured. Adding 10 labelled actions showed 80% (< 85%), so actions
  are reported, not counted. Check the judge's input too: a 12k-character cut hid half of a real
  overview's evidence and cost 30 points of apparent faithfulness.
- **Read every failure before you change the agent.** Eight of the eleven fixes this PR were in
  the scorer or the case patterns, among them a decimal read as a year, a correct refutation read
  as an echo, and a correct "no month-over-month comparison exists" read as a claim. Each was
  rescored for $0.
- **Measure the carry-over instead of arguing it.** Replaying stored targeted plans on raw vs
  cleaned frames turned "targeted skips cleaning" into "10 of 15 answers change", at $0.

## What the quality agent taught us (Phase 5, PR 5)
- **A reviewer is only as good as its precision.** The first critic caught every seeded defect, but
  23% of its flags were right and half the clean reports got one. Fix precision before recall: an
  explicit not-a-defect list, a quote of what the evidence says, and a second pass that re-judges
  each flag alone (false alarms 8.1% → 0.4%, for +$0.0007 a report).
- **Layer by what has one right answer.** Code checks caught all 25 rule-shaped defects (numbers,
  dates, direction, period, group, rank, cause, certainty) at $0 with 0% false alarms. The LLM is
  needed only for the semantic rest (Simpson, "typical" = mean, data quality vs the audit).
- **Run a check over every stored output before it counts.** 140 real reports found 9 false-positive
  patterns the eval didn't plant (a pronoun, "respectively", "second-largest", a possessive), and one
  real insight defect the insight eval had passed in all 3 runs.
- **Truncation is a silent input bug.** Evidence excerpts are stored cut at 300 characters; the old
  critic, the new one and the insight agent each misread a 4-cell breakdown as missing a cell.
  Read structured results by id, never a display excerpt.
- **Don't relabel after you see the result.** One holdout flag is arguable; it was labelled before the
  run and is counted. Label fixes found during iteration (q-32) are listed and rescored.
- **Estimate from the runs you're about to repeat.** An E2E re-gate on 5 overviews cost 2.8× the
  suite's per-question average; the estimate used the average and ran 132% over.

## What the ingestion agent taught us (Phase 5, PR 6)
- **Ask what a model call uniquely contributes before you contract it.** The summary call fed
  one agent, that agent's eval never saw it, and its one measured effect was invented dates.
  Removing it raised Intake to 100% and made ingestion reproducible.
- **A "never" rule needs one choke point.** The audit found 5 of 6 prompt builders leaking names
  that column names don't announce. Masking at each source fixes today's builders; tokenizing at
  the client every call goes through holds the rule for tomorrow's. Reversible tokens keep the
  user's own questions working. A guard hit is then a bug report, not the plan.
- **Key a security cache on content, never on identity.** The guard's pattern was first cached by
  `id(set)`; a dead run's id could serve a new run a stale pattern. The commit review caught it.
- **Loading is part of correctness.** The old loader silently turned `lifetime_value` into 1970
  timestamps, read European CSVs as one column and counted a TOTAL row as a sale. Load lossless,
  clean with an audit; a file that isn't a table gets a plain message.
- **Prove a suite unaffected by its inputs.** A snapshot of what each paid agent receives showed
  0/46 analysis and 0/40 insight contexts changed, so neither was re-run. A test pins it.
- **When a fix changes the right answer, the truth must follow.** E2E targeted truth was computed
  on a table with planted duplicate orders. The agent's new numbers matched the deduplicated
  values exactly. Diagnose before re-running, then relabel and say so.
- **Keep holdouts out of iteration.** Running them once while iterating spent one of them; a fresh
  holdout, labelled before its first run, restored the unseen check.

## Rolling it out to the next agent (Phase 5)
Each agent gets one PR, in this order:
1. **Agent card**, with honest, measured limits.
2. **Eval cases first**, with planted truths from `evals/datagen.py`. Add a scorer check for each new behavior and a hard rule for each guardrail.
3. **Baseline** the current agent on the new cases (k=1 across the whole suite is enough for a "before") and commit the report.
4. **Refactor onto the contract.** Move the prompt verbatim into `v<N>.md`, then add `build_context`, the tools, the output schema with evidence, and `llm` as a parameter. Smoke one case to confirm behavior didn't change.
5. **Guardrails and fault tests.** Use a `RecordedClient` response per bad output and assert the typed error or the retry. Never assert prompt wording.
6. **Iterate to target**:
   - change one thing per prompt version;
   - run `--only <case> --k 1` on the failing category;
   - read the failure reason before you change anything.
7. **Run the full suite** (k=3, `--compare` against the previous report) and E2E, then open the PR with the eval diff.

**Eval cost playbook (every PR; Phase 5 Intake ran on it for $4.39, Phase 4 cost $15.44 without it):**
- Announce the $ estimate before every paid run. If a run costs >30% over its estimate, stop and say so.
- Spend in this order: offline pytest ($0) → `--only <case> --k 1` → category k=1 (`--exclude holdout`) →
  **one** full k=3 per suite per PR, at the end.
- A scorer or checker bug never buys a re-run: fix it, then `python -m evals.run rescore <report.json>` ($0).
- Prove a suite is unaffected instead of re-running it, then pin the proof in a test. Phase 5 used a
  byte-identical prompt for the 50 SQL cases and identical column roles for the E2E datasets.
- Measure a risk before fixing it. The topic-switch cases passed 6/6 on the existing code, so they
  stay as a guard and nothing was changed.
- **E2E gate:** k=1, then k=3 only on a failing case. A failure needs a diagnosis from its stored
  output; a pass on retry is not one. Re-run the gate on the final commit only if the PR changed
  the CSV/E2E path after the last gate.
- A cheaper model ships only if it matches every target on the same cases. Prompt caching is on by
  default: keep system prompts constant.

**Checklist before the PR:**
- `grep -rn "get_client()"` in the agent finds nothing;
- every new field is in `frontend/src/types/index.ts`;
- the SSE protocol in CLAUDE.md is up to date;
- traces contain no prompt or data text;
- any scorer change is listed in the review note.
