from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from llm.client import ClaudeClient, LLMError, LLMResult, RecordedClient, trace_meter


async def test_recorded_client_replays_in_order_and_records_calls():
    client = RecordedClient(["first", LLMResult(text="second")])
    a = await client.complete(messages=[{"role": "user", "content": "q"}], max_tokens=10,
                              agent="t", prompt_version="v0")
    b = await client.complete(messages=[], max_tokens=10, agent="t", prompt_version="v0")
    assert (a.text, b.text) == ("first", "second")
    assert client.calls[0]["messages"][0]["content"] == "q"
    with pytest.raises(LLMError):
        await client.complete(messages=[], max_tokens=10, agent="t", prompt_version="v0")


async def test_refusal_raises_but_truncation_is_returned():
    client = RecordedClient([LLMResult(text="", stop_reason="refusal"),
                             LLMResult(text="partial", stop_reason="max_tokens")])
    with pytest.raises(LLMError, match="refusal"):
        await client.complete(messages=[], max_tokens=10, agent="t", prompt_version="v0")
    truncated = await client.complete(messages=[], max_tokens=10, agent="t", prompt_version="v0")
    assert truncated.stop_reason == "max_tokens"   # Phase 1: traced, not raised


def _fake_sdk(captured: dict):
    async def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text='{"ok": 1}')],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        )
    return SimpleNamespace(messages=SimpleNamespace(create=create))


async def test_claude_client_pins_model_and_temperature_and_traces(tmp_path):
    captured: dict = {}
    client = ClaudeClient(model="claude-sonnet-4-6")
    client._sdk = _fake_sdk(captured)
    meter: list[dict] = []
    token = trace_meter.set(meter)
    try:
        result = await client.complete(messages=[{"role": "user", "content": "secret data"}],
                                       max_tokens=50, agent="analysis", prompt_version="v0")
    finally:
        trace_meter.reset(token)

    assert result.text == '{"ok": 1}'
    assert captured["model"] == "claude-sonnet-4-6" and captured["temperature"] == 0
    assert "system" not in captured
    assert result.cost_usd == pytest.approx((1000 * 3 + 200 * 15) / 1e6)

    from api.config import settings
    lines = [json.loads(line) for f in (tmp_path / "traces").glob("*.jsonl")
             for line in f.read_text().splitlines()]
    assert settings.trace_dir == str(tmp_path / "traces")
    assert len(lines) == 1 and lines[0]["agent"] == "analysis"
    assert "secret data" not in json.dumps(lines[0])   # metadata only, never prompt text
    assert meter == lines
