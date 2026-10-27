"""
Typed agent errors (ADR-0001). The graph maps any AgentError to the SSE `error` event using
`user_message`; the full detail stays in logs. A failure is never an empty result.
"""
from __future__ import annotations


class AgentError(Exception):
    def __init__(self, agent: str, detail: str, user_message: str | None = None):
        super().__init__(f"{agent}: {detail}")
        self.agent = agent
        self.user_message = user_message or f"The {agent} step failed: {detail}"


class LLMError(AgentError):
    """The model gave no usable output."""


class LLMRefusal(LLMError):
    pass


class LLMTruncated(LLMError):
    """stop_reason == max_tokens — the output is cut off and must not be used."""


class LLMUnavailable(LLMError):
    """API timeout or connection failure after the SDK's own retries."""


class LLMOutputError(LLMError):
    """The model answered, but the output can't be parsed into the agent's schema."""


class GuardrailViolation(AgentError):
    """A hard guardrail rejected the output (unsafe code, invented column, write SQL)."""


class SandboxError(AgentError):
    """Generated code failed or timed out in the sandbox."""
