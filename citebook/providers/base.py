"""The model provider interface (TRD section 10). Every model call goes through `ModelProvider`."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

Message = dict[str, Any]  # {"role", "content", and optionally "tool_calls" or "tool_name"}


class ModelUnavailable(Exception):
    """The model did not answer in time or answered with something unusable."""


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: Any  # as the model proposed it; the gate validates it


@dataclass(frozen=True)
class ModelTurn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: int = 0


class ModelProvider(Protocol):
    name: str
    model: str

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelTurn:
        """One model turn. Raises ModelUnavailable."""
        ...


_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_thinking(text: str) -> str:
    """Remove any reasoning block a model returns inside its text, even with thinking off."""
    return _THINK.sub("", text).strip()
