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
- The agent decides whether a reason is hard (retry, then raise a typed `AgentError`) or soft. Analysis has four hard rules:
  1. the code AST allowlist, plus the column references in the code;
  2. findings are non-empty (EDA counts only for an EDA question);
  3. finding columns exist;
  4. every number in the prose is grounded in a computed value (`ungrounded_numbers`).
- One guided retry covers all four. The rejection message quotes the offending sentence, because "numbers [...] are ungrounded" alone made the model repeat the mistake.
- Every rejection writes a trace line holding only the rule label (`trace_verdict`), since reasons quote data. Eval reports count `runs_with_guardrail_retry`.

## What the analysis agent taught us
- **Look for scorer and schema bugs before blaming the model.** `trends: list[TrendResult | dict]` coerced every dict into a model object, so the scorer's dict-only filter saw no trends. That made trend fail in every case in Phases 1 and 2, while the agent was reporting the right number.
- **Put one-right-answer statistics in deterministic code.** Asking the model for per-segment MoM and joint breakdowns doubled its output (3–4.6k tokens, 45–60s per call), and it still used a second MoM definition. `_build_standard_code` computes them for the requested types, and its MoM replaces the model's. The model keeps the judgment work: tests, anomalies, interpretation. Smoke cost fell from ~$0.08 to ~$0.03 per question.
- **A hard rule needs a precise check.** Grounding read `p=5.7e-40` as `5.7` and `40` and rejected correct prose. Before you make a check hard, run it over real output.
- **The rejection count is the latency budget.** p95 tracked the retry rate almost one for one. Prevent the causes in the prompt: the model wrote `department=Easy` as a column, and EDA-only answers counted as empty.
- **The evidence UI found bugs the eval couldn't.** One real run in the drawer showed `discount` (a rate) being summed for MoM and per-segment trends claiming all 3,025 rows. The evals don't score evidence, so read some by hand.
- **Security review on every commit pays off.** It caught four masking and delimiting gaps in shared guardrails: a fail-open PII name list, camelCase names, integer and float PII, and close-tag spellings. Fix those in the shared function, not at the caller.

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

**Checklist before the PR:**
- `grep -rn "get_client()"` in the agent finds nothing;
- every new field is in `frontend/src/types/index.ts`;
- the SSE protocol in CLAUDE.md is up to date;
- traces contain no prompt or data text;
- any scorer change is listed in the review note.
