# ADR-0001: The agent contract is a convention, not a framework

- Status: Proposed (Phase 0, 2026-10-27)
- Gaps addressed: G1, G2, G4, G6, G7 (see `docs/research/2026-10-27-data-agents.md`)

## Context
Every LLM agent is a single-shot "return JSON" call. Prompts are inline f-strings, LLM-named columns
are never checked, and each agent assembles its own ad-hoc context. Production data agents get their
gains from explicit context (Snowflake: +21 pts from the semantic model alone [P4]), from
validating names against the schema (LinkedIn: schema hallucination fell from 23% to 1% [P2]), and
from showing assumptions [P3].

## Decision
Each agent stays a plain `async def run_<agent>(session, llm) -> <Output>` and declares six things.
There are no base classes or registry.

1. **Agent card**: a Pydantic constant in the agent module with `name`, `version`, `scope`,
   `inputs`, `outputs`, `limits` and `refusal_rules`. It is injected into the system prompt and
   served to the UI.
2. **Prompt file**: `prompts/<agent>/v<N>.md`. A version bump is a new file, so diffs show prompt
   changes, and eval reports record the prompt version.
3. **Context builder**: `build_context(session) -> <Agent>Context`, a typed bundle (task, data
   contract, last N turns, semantic-layer entries, past insights) trimmed to a token budget.
4. **Tools**: functions exposed through real tool calling (`get_schema`, `get_metric_definition`,
   `sample_rows`, `search_past_insights`, `run_sql`).
5. **Output schema**: a Pydantic model enforced through structured output ([ADR-0005](0005-llm-interface.md)).
6. **Guardrails**: a list of pure checks from `guardrails/` that each return pass or a reason.
   A hard-rule failure raises an `AgentError` subclass. A soft failure lowers confidence.

Findings gain an `Evidence` object (code or SQL, result excerpt, row count, confidence with a reason,
and assumptions). Narrative claims reference evidence IDs.

## Consequences
- Rolling the contract out to the next agent is mechanical, which is the point of Phase 5.
- Shared helpers stay small. A helper is extracted only when a second agent needs it.
- Empty output without an error becomes impossible by construction, because the output schema
  requires at least one finding or a raised error.

---
Source keys ([P1], [S6], …) refer to the Sources list in `docs/research/2026-10-27-data-agents.md`.
