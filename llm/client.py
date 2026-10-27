"""
Shared LLM client (ADR-0005). Every agent calls Claude through `get_client().complete(...)`.

- ClaudeClient: production + evals. Pinned model from settings, temperature 0, SDK retries.
- RecordedClient: tests. Replays canned responses; never touches the network.
- Each call writes one metadata-only JSONL trace line (no prompt or data text → no PII on disk).
"""
from __future__ import annotations

import json
import time
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from api.config import settings

# USD per million tokens (input, output). Unknown models trace cost as 0.
_PRICES = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

# When set (eval runner), every trace record of the current task is appended here too.
trace_meter: ContextVar[list[dict] | None] = ContextVar("trace_meter", default=None)


class LLMError(Exception):
    """The model refused — there is no output to use."""


@dataclass
class LLMResult:
    text: str
    stop_reason: str = "end_turn"
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0


class LLMClient(Protocol):
    async def complete(
        self, *, messages: list[dict], max_tokens: int, agent: str, prompt_version: str,
        system: str | None = None,
    ) -> LLMResult: ...


def _check(result: LLMResult, agent: str) -> LLMResult:
    # ponytail: max_tokens is traced, not raised. The baseline showed the analysis agent truncates
    # in most runs; raising would change prod behavior in Phase 1. Typed handling lands in Phase 2.
    if result.stop_reason == "refusal":
        raise LLMError(f"{agent}: model stopped with 'refusal'")
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
        system: str | None = None,
    ) -> LLMResult:
        kwargs = {"system": system} if system else {}
        start = time.perf_counter()
        response = await self._client().messages.create(
            model=self.model, max_tokens=max_tokens, temperature=0,
            messages=messages, **kwargs,
        )
        in_price, out_price = _PRICES.get(self.model, (0.0, 0.0))
        usage = response.usage
        result = LLMResult(
            text="".join(b.text for b in response.content if getattr(b, "type", None) == "text"),
            stop_reason=response.stop_reason or "end_turn",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            latency_ms=int((time.perf_counter() - start) * 1000),
            cost_usd=(usage.input_tokens * in_price + usage.output_tokens * out_price) / 1e6,
        )
        record = {k: v for k, v in asdict(result).items() if k != "text"}
        _trace({
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent": agent, "prompt_version": prompt_version, "model": self.model, **record,
        })
        return _check(result, agent)


class RecordedClient:
    """Test double: replays `responses` in order and records every call in `.calls`."""

    def __init__(self, responses: list[str | LLMResult] | None = None):
        self.responses = list(responses or [])
        self.calls: list[dict] = []

    async def complete(
        self, *, messages: list[dict], max_tokens: int, agent: str, prompt_version: str,
        system: str | None = None,
    ) -> LLMResult:
        self.calls.append({"agent": agent, "system": system, "messages": messages})
        if not self.responses:
            raise LLMError(f"RecordedClient: no recorded response left for agent '{agent}'")
        r = self.responses.pop(0)
        return _check(LLMResult(text=r) if isinstance(r, str) else r, agent)


_client: LLMClient | None = None


def get_client() -> LLMClient:
    global _client
    if _client is None:
        _client = ClaudeClient()
    return _client


def set_client(client: LLMClient | None) -> None:
    global _client
    _client = client
