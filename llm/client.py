"""
Shared LLM client (ADR-0005). Every agent calls Claude through `get_client().complete(...)`.

- ClaudeClient: production + evals. Pinned model from settings, temperature 0, SDK retries.
- RecordedClient: tests. Replays canned responses; never touches the network.
- Each call writes one metadata-only JSONL trace line (no prompt or data text → no PII on disk).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from agents.errors import LLMError, LLMRefusal, LLMTruncated, LLMUnavailable
from api.config import settings

__all__ = ["LLMError", "LLMRefusal", "LLMTruncated", "LLMUnavailable"]

# USD per million tokens (input, output). Unknown models trace cost as 0.
# Prompt cache: a 5-minute write costs 1.25x input, a read 0.1x.
_CACHE_WRITE, _CACHE_READ = 1.25, 0.1
_PRICES = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

# When set (eval runner), every trace record of the current task is appended here too.
trace_meter: ContextVar[list[dict] | None] = ContextVar("trace_meter", default=None)

# Hard rule (Phase 5 Ingestion): unmasked PII from the user's data never reaches an LLM. A run's
# entry point sets an empty set (api/routes/chat.py, the eval harness); ingestion fills it in place
# with the dataset's PII values, so every node's task sees the same set. Each request's messages are
# pseudonymized against it ("Jane Doe" → "[PII_1a2b3c4d]") and each reply restored, so a question
# naming a customer still works while the model never sees the name.
pii_values: ContextVar[set[str] | None] = ContextVar("pii_values", default=None)
_TOKEN_RE = re.compile(r"\[PII_[0-9a-f]{8}\]")
_patterns: dict[tuple[int, int], re.Pattern] = {}


def pii_token(value: str) -> str:
    return f"[PII_{hashlib.sha1(value.encode()).hexdigest()[:8]}]"


def _walk(obj, fn):
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [_walk(x, fn) for x in obj]
    if isinstance(obj, dict):
        return {k: _walk(v, fn) for k, v in obj.items()}
    return obj


def pseudonymize(obj, values: set[str] | None = None):
    """(obj with every known PII value replaced by its token, {token: value} for what was
    replaced). `values` defaults to the current run's set; no set → unchanged."""
    values = pii_values.get() if values is None else values
    if not values:
        return obj, {}
    key = (id(values), len(values))   # ponytail: one compiled pattern per set size; fine ≤ ~20k values
    if key not in _patterns:
        _patterns.clear()
        alts = "|".join(re.escape(v) for v in sorted(values, key=len, reverse=True))
        _patterns[key] = re.compile(rf"(?<![\w@.])(?:{alts})(?![\w@])")
    mapping: dict[str, str] = {}

    def sub(m: re.Match) -> str:
        token = pii_token(m.group())
        mapping[token] = m.group()
        return token
    return _walk(obj, lambda t: _patterns[key].sub(sub, t)), mapping


def restore(obj, mapping: dict[str, str]):
    if not mapping:
        return obj
    return _walk(obj, lambda t: _TOKEN_RE.sub(lambda m: mapping.get(m.group(), m.group()), t))


def _restore_result(result: "LLMResult", mapping: dict[str, str]) -> "LLMResult":
    if mapping:
        result.text = restore(result.text, mapping)
        result.tool_calls = restore(result.tool_calls, mapping)
        result.content = restore(result.content, mapping)
    return result


@dataclass
class LLMResult:
    text: str
    stop_reason: str = "end_turn"
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    tool_calls: list[dict] = field(default_factory=list)   # [{"id", "name", "input"}]
    content: list[dict] = field(default_factory=list)      # raw blocks, to echo the assistant turn


class LLMClient(Protocol):
    async def complete(
        self, *, messages: list[dict], max_tokens: int, agent: str, prompt_version: str,
        system: str | None = None, model: str | None = None,
        tools: list[dict] | None = None, tool_choice: dict | None = None,
    ) -> LLMResult: ...


def _check(result: LLMResult, agent: str) -> LLMResult:
    if result.stop_reason == "refusal":
        raise LLMRefusal(agent, "model refused", "The model declined to answer this request.")
    if result.stop_reason == "max_tokens":
        raise LLMTruncated(agent, "output hit max_tokens",
                           f"The {agent} step produced too much output and was cut off.")
    return result


def _trace(record: dict) -> None:
    meter = trace_meter.get()
    if meter is not None:
        meter.append(record)
    try:
        trace_dir = Path(settings.trace_dir)
        trace_dir.mkdir(parents=True, exist_ok=True)
        with open(trace_dir / f"{record['ts'][:10]}.jsonl", "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass  # tracing must never break an analysis


def trace_verdict(agent: str, prompt_version: str, rule: str) -> None:
    """Record a guardrail rejection (ADR-0005). The rule label only: reasons quote data."""
    _trace({"ts": datetime.now(timezone.utc).isoformat(), "agent": agent,
            "prompt_version": prompt_version, "guardrail": rule})


def trace_feedback(agent: str, event: str, rule: str, before: str, after: str) -> None:
    """Record user feedback on an agent output (labels only, never data): e.g. a chart override."""
    _trace({"ts": datetime.now(timezone.utc).isoformat(), "agent": agent, "feedback": event,
            "rule": rule, "before": before, "after": after})


class ClaudeClient:
    def __init__(self, model: str | None = None):
        self.model = model or settings.llm_model
        self._sdk = None

    def _client(self):
        if self._sdk is None:
            from anthropic import AsyncAnthropic
            self._sdk = AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=180.0)
        return self._sdk

    async def complete(
        self, *, messages: list[dict], max_tokens: int, agent: str, prompt_version: str,
        system: str | None = None, model: str | None = None,
        tools: list[dict] | None = None, tool_choice: dict | None = None,
    ) -> LLMResult:
        model = model or self.model
        messages, mapping = pseudonymize(messages)
        if mapping:   # a builder let PII through: the guard caught it (label only, never values)
            trace_verdict(agent, prompt_version, "pii_tokenized")
        # Every agent's system prompt is a module constant, so it is cached (with the tools, which
        # render before it). Prompts under the model's minimum (1024 tokens on Sonnet 4.6, 4096
        # on Haiku 4.5) are silently not cached and cost nothing extra.
        kwargs = {"system": [{"type": "text", "text": system,
                              "cache_control": {"type": "ephemeral"}}]} if system else {}
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        start = time.perf_counter()
        # APIConnectionError also covers APITimeoutError; 429/5xx arrive after the SDK's retries.
        from anthropic import APIConnectionError, InternalServerError, RateLimitError
        try:
            response = await self._client().messages.create(
                model=model, max_tokens=max_tokens, temperature=0,
                messages=messages, **kwargs,
            )
        except (APIConnectionError, InternalServerError, RateLimitError) as exc:
            raise LLMUnavailable(agent, f"{type(exc).__name__}: {exc}",
                                 "The language model is unreachable right now. Try again.") from exc
        in_price, out_price = _PRICES.get(model, (0.0, 0.0))
        usage = response.usage
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        result = LLMResult(
            text="".join(b.text for b in response.content if getattr(b, "type", None) == "text"),
            stop_reason=response.stop_reason or "end_turn",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_write_tokens=cache_write,
            cache_read_tokens=cache_read,
            latency_ms=int((time.perf_counter() - start) * 1000),
            cost_usd=(usage.input_tokens * in_price + usage.output_tokens * out_price
                      + (cache_write * _CACHE_WRITE + cache_read * _CACHE_READ) * in_price) / 1e6,
            tool_calls=[{"id": b.id, "name": b.name, "input": b.input}
                        for b in response.content if getattr(b, "type", None) == "tool_use"],
            content=[b.model_dump(exclude_none=True) for b in response.content],
        )
        # Metadata only: tool names, never their inputs or the text (no data/PII on disk).
        record = {k: v for k, v in asdict(result).items() if k not in ("text", "content")}
        record["tool_calls"] = [c["name"] for c in result.tool_calls]
        _trace({
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent": agent, "prompt_version": prompt_version, "model": model, **record,
        })
        return _check(_restore_result(result, mapping), agent)


class RecordedClient:
    """Test double: replays `responses` in order and records every call in `.calls`."""

    def __init__(self, responses: list[str | LLMResult] | None = None):
        self.responses = list(responses or [])
        self.calls: list[dict] = []

    async def complete(
        self, *, messages: list[dict], max_tokens: int, agent: str, prompt_version: str,
        system: str | None = None, model: str | None = None,
        tools: list[dict] | None = None, tool_choice: dict | None = None,
    ) -> LLMResult:
        messages, mapping = pseudonymize(messages)   # tests see what would be sent
        self.calls.append({"agent": agent, "system": system, "messages": messages, "model": model,
                           "tools": tools, "tool_choice": tool_choice})
        if not self.responses:
            raise LLMError(agent, "RecordedClient has no recorded response left")
        r = self.responses.pop(0)
        return _check(_restore_result(LLMResult(text=r) if isinstance(r, str) else r, mapping), agent)


_client: LLMClient | None = None


def get_client() -> LLMClient:
    global _client
    if _client is None:
        _client = ClaudeClient()
    return _client


def set_client(client: LLMClient | None) -> None:
    global _client
    _client = client
