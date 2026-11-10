"""FakeProvider: a scripted model for tests. It can propose any tool call, including malicious ones.

Each script step is a ModelTurn, a callable that builds one from the messages and tools it is
given, or a ModelUnavailable to raise. Every call is recorded so tests can see what the model saw.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from citebook.providers.base import Message, ModelTurn, ModelUnavailable, ToolCall

Step = ModelTurn | ModelUnavailable | Callable[[list[Message], list[dict[str, Any]]], ModelTurn]


@dataclass
class FakeProvider:
    script: list[Step] = field(default_factory=list)
    calls: list[tuple[list[Message], list[dict[str, Any]]]] = field(default_factory=list)
    name: str = "fake"
    model: str = "fake-model"

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelTurn:
        self.calls.append(([dict(m) for m in messages], list(tools)))
        if not self.script:
            raise AssertionError("FakeProvider script ran out")
        step = self.script.pop(0)
        if isinstance(step, ModelUnavailable):
            raise step
        if isinstance(step, ModelTurn):
            return step
        return step(messages, tools)

    def reachable(self) -> bool:
        return True

    def offered(self, index: int = -1) -> list[str]:
        """Names of the tools offered on one call."""
        return [t["function"]["name"] for t in self.calls[index][1]]


def say(text: str) -> ModelTurn:
    return ModelTurn(text=text)


def use(tool: str, /, **arguments: Any) -> ModelTurn:
    return ModelTurn(tool_calls=[ToolCall(tool, arguments)])
