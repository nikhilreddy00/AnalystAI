# ADR-0005: One thin async LLM client, used by every agent, that owns model config, retries and traces

- Status: Proposed (Phase 0, 2026-10-27)
- Gaps addressed: G1, G13 (CrewAI), G14

## Context
- Agents each create a module-level `AsyncAnthropic` client.
- `claude-sonnet-4-6` is hardcoded in 7 files.
- Ingestion makes one Groq call through CrewAI.
- Nothing records tokens, cost or latency, and tests cannot swap in a fake.
- The Claude API now offers structured outputs (`output_config.format` with a JSON schema) and
  strict tool use [P9].

## Decision
```python
class LLMClient(Protocol):
    async def complete(
        self, *, system: str, messages: list[dict],
        tools: list[Tool] | None = None,
        output_schema: type[BaseModel] | None = None,
        agent: str, prompt_version: str,
    ) -> LLMResult: ...   # .parsed, .tool_calls, .stop_reason, .usage, .latency_ms
```

- **Two implementations:**
  - `ClaudeClient` for production and evals.
  - `RecordedClient` for tests. It replays fixture responses, including bad ones (malformed JSON,
    invented column, refusal) for fault-injection tests.
- **Model ID**: comes from config (`settings.llm_model`) and is pinned. Phase 1 keeps the current
  model, so there is no behavior change. A model change requires a full eval run.
- **Retries**: use the SDK's built-in `max_retries` with backoff plus a request timeout. There is
  no custom retry loop. A `refusal` or `max_tokens` stop reason raises a typed error.
- **Traces**: one JSONL line per call in `traces/<date>.jsonl` with agent, prompt version, model,
  tokens, cost, latency, tool calls and guardrail verdicts. A hosted tracer such as Langfuse is
  optional later.
- **Groq/CrewAI**: the ingestion summary call moves onto `ClaudeClient` (Haiku-tier model through
  config). CrewAI and the Groq dependency are removed in Phase 2.
- **Second provider**: only the Protocol is in scope. An implementation is added only after evals
  exist to show what it costs in accuracy.

## Consequences
- Agents receive the client as a parameter, which is the second test seam in the roadmap.
- Removing Groq drops `GROQ_API_KEY` from the required environment, so there is one less key to
  set up.

---
Source keys ([P1], [S6], …) refer to the Sources list in `docs/research/2026-10-27-data-agents.md`.
