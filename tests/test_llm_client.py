from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from anthropic.types import TextBlock, ToolUseBlock

from llm.client import (
    ClaudeClient, LLMError, LLMRefusal, LLMResult, LLMTruncated, RecordedClient, trace_meter,
)


async def test_recorded_client_replays_in_order_and_records_calls():
    client = RecordedClient(["first", LLMResult(text="second")])
    a = await client.complete(messages=[{"role": "user", "content": "q"}], max_tokens=10,
                              agent="t", prompt_version="v0")
    b = await client.complete(messages=[], max_tokens=10, agent="t", prompt_version="v0")
    assert (a.text, b.text) == ("first", "second")
    assert client.calls[0]["messages"][0]["content"] == "q"
    with pytest.raises(LLMError):
        await client.complete(messages=[], max_tokens=10, agent="t", prompt_version="v0")


async def test_refusal_and_truncation_raise_typed_errors():
    client = RecordedClient([LLMResult(text="", stop_reason="refusal"),
                             LLMResult(text="partial", stop_reason="max_tokens")])
    with pytest.raises(LLMRefusal):
        await client.complete(messages=[], max_tokens=10, agent="t", prompt_version="v0")
    with pytest.raises(LLMTruncated):
        await client.complete(messages=[], max_tokens=10, agent="t", prompt_version="v0")


def _fake_sdk(captured: dict, content: list | None = None, stop_reason: str = "end_turn"):
    async def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            content=content or [TextBlock(type="text", text='{"ok": 1}')],
            stop_reason=stop_reason,
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


async def test_tool_calls_are_parsed_and_traced_without_their_inputs(tmp_path):
    captured: dict = {}
    client = ClaudeClient(model="claude-sonnet-4-6")
    client._sdk = _fake_sdk(captured, stop_reason="tool_use", content=[
        TextBlock(type="text", text="Checking."),
        ToolUseBlock(type="tool_use", id="t1", name="sample_rows", input={"columns": ["secret"]}),
    ])
    tools = [{"name": "sample_rows", "input_schema": {"type": "object"}}]
    result = await client.complete(messages=[], max_tokens=50, agent="analysis",
                                   prompt_version="v3", tools=tools)

    assert captured["tools"] == tools
    assert result.tool_calls == [{"id": "t1", "name": "sample_rows", "input": {"columns": ["secret"]}}]
    assert [b["type"] for b in result.content] == ["text", "tool_use"]
    line = next((tmp_path / "traces").glob("*.jsonl")).read_text()
    assert '"tool_calls": ["sample_rows"]' in line and "secret" not in line
