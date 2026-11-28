"""HostedProvider with a stand-in client: conversion, tool calls, usage, errors. No network."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from citebook.config import Settings
from citebook.providers import make_provider
from citebook.providers.base import ModelUnavailable
from citebook.providers.hosted import SYSTEM_MARK, HostedProvider, convert
from citebook.providers.ollama import OllamaProvider

TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "check_availability",
        "description": "Find open times.",
        "parameters": {"type": "object", "properties": {"zip": {"type": "string"}}},
    },
}


def usage(**extra: int) -> SimpleNamespace:
    fields = {"cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    return SimpleNamespace(input_tokens=100, output_tokens=20, **(fields | extra))


def reply(*blocks: Any, stop: str = "end_turn", **extra: int) -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks), stop_reason=stop, usage=usage(**extra))


def text(value: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=value)


def tool_use(name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id="toolu_1", name=name, input=arguments)


class FakeClient:
    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.bodies: list[dict[str, Any]] = []
        self.messages = self

    def with_options(self, **_: Any) -> FakeClient:
        return self

    def create(self, **body: Any) -> Any:
        self.bodies.append(body)
        step = self.replies.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def provider(*replies: Any, think: bool = False) -> tuple[HostedProvider, FakeClient]:
    client = FakeClient(*replies)
    return HostedProvider("claude-haiku-5-5", client=client, think=think), client


HISTORY: list[dict[str, Any]] = [
    {"role": "system", "content": "prompt"},
    {"role": "system", "content": "state"},
    {"role": "user", "content": "Can you come Tuesday?"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "check_availability", "arguments": {"zip": "1"}}}],
    },
    {"role": "tool", "tool_name": "check_availability", "content": "BEGIN DATA ..."},
    {"role": "assistant", "content": "draft"},
    {"role": "system", "content": "nudge"},
]


def test_conversion_keeps_tool_exchanges_paired() -> None:
    system, messages, has_tools = convert(HISTORY)
    assert system == "prompt\n\nstate"
    assert has_tools
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant", "user"]
    use = messages[1]["content"][0]
    assert use == {
        "type": "tool_use",
        "id": "call_1",
        "name": "check_availability",
        "input": {"zip": "1"},
    }
    result = messages[2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "call_1"
    assert messages[4]["content"] == [{"type": "text", "text": SYSTEM_MARK + "nudge"}]


def test_history_starting_with_assistant_gets_a_user_turn_first() -> None:
    _, messages, _ = convert([{"role": "assistant", "content": "Hello"}])
    assert messages[0]["role"] == "user"


def test_tool_call_reply_becomes_a_tool_call() -> None:
    hosted, client = provider(reply(tool_use("check_availability", {"zip": "2"}), stop="tool_use"))
    turn = hosted.chat(HISTORY[:3], [TOOL])
    assert [(c.name, c.arguments) for c in turn.tool_calls] == [
        ("check_availability", {"zip": "2"})
    ]
    body = client.bodies[0]
    assert body["tools"][0]["input_schema"] == TOOL["function"]["parameters"]
    assert body["thinking"] == {"type": "disabled"}
    assert "temperature" not in body and "tool_choice" not in body


def test_text_reply_and_usage_are_counted() -> None:
    hosted, _ = provider(reply(text("Tuesday at 9."), cache_read_input_tokens=50))
    turn = hosted.chat(HISTORY[:3], [TOOL])
    assert turn.text == "Tuesday at 9." and not turn.tool_calls
    assert turn.prompt_tokens == 150 and turn.completion_tokens == 20
    assert hosted.usage["input_tokens"] == 100
    assert hosted.usage["cache_read_input_tokens"] == 50
    assert hosted.usage["calls"] == 1


def test_no_tools_offered_but_tool_history_sends_definitions_that_cannot_be_called() -> None:
    hosted, client = provider(reply(text("ok")))
    hosted.chat(HISTORY, [])
    assert client.bodies[0]["tool_choice"] == {"type": "none"}
    assert client.bodies[0]["tools"]
    assert hosted.usage["no_tool_calls"] == 1


def test_thinking_setting_maps_to_adaptive() -> None:
    hosted, client = provider(reply(text("ok")), think=True)
    hosted.chat(HISTORY[:3], [TOOL])
    assert client.bodies[0]["thinking"] == {"type": "adaptive"}


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_refusal_or_cut_reply_is_an_empty_turn(stop: str) -> None:
    hosted, _ = provider(reply(tool_use("check_availability", {}), stop=stop))
    turn = hosted.chat(HISTORY[:3], [TOOL])
    assert turn.text == "" and turn.tool_calls == []


def test_api_errors_become_model_unavailable_without_details() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    hosted, _ = provider(anthropic.APIConnectionError(request=request))
    with pytest.raises(ModelUnavailable, match=r"^APIConnectionError$"):
        hosted.chat(HISTORY[:3], [TOOL])
    assert hosted.usage["errors"] == 1


def test_factory_builds_ollama_by_default_and_hosted_only_when_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert isinstance(make_provider(Settings(_env_file=None), False), OllamaProvider)
    settings = Settings(
        _env_file=None,
        CITEBOOK_MODEL_PROVIDER="hosted",
        CITEBOOK_ALLOW_HOSTED="true",
        CITEBOOK_MODEL="claude-haiku-5-5",
        ANTHROPIC_API_KEY="test-key-not-real",
    )
    made = make_provider(settings, False)
    assert isinstance(made, HostedProvider) and made.model == "claude-haiku-5-5"
    assert "test-key-not-real" not in repr(settings)
