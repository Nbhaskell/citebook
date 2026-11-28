"""HostedProvider: a Claude model through the Anthropic API (D-099). Off by default (rule 11).

The turn loop keeps its messages in Ollama's shape; this module converts them to the Messages
API shape and back, so tool calls work the same way with either provider:
- leading system messages become the top-level `system`; later ones become user text marked
  "[System message]", because the API accepts a mid-conversation system message only right
  after a user message, and the loop also adds them after an assistant draft;
- an assistant tool call becomes a `tool_use` block with a generated id, and the tool message
  after it becomes the matching `tool_result` block.

Sampling settings are not sent (the model rejects non-default values), so runs are not seeded.
The API key is never logged, recorded or included in an error.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

import anthropic

from citebook.providers.base import Message, ModelTurn, ModelUnavailable, ToolCall, strip_thinking
from citebook.tools.registry import REGISTRY

MAX_TOKENS = 4096
SYSTEM_MARK = "[System message] "


def tool_spec(schema: dict[str, Any]) -> dict[str, Any]:
    function = schema["function"]
    return {
        "name": function["name"],
        "description": function["description"],
        "input_schema": function["parameters"],
    }


def convert(messages: list[Message]) -> tuple[str, list[dict[str, Any]], bool]:
    """(system text, API messages, whether the history holds tool blocks)."""
    system: list[str] = []
    out: list[dict[str, Any]] = []
    pending: list[str] = []  # tool_use ids still waiting for their result
    count = 0
    has_tools = False

    def add(role: str, block: dict[str, Any]) -> None:
        if out and out[-1]["role"] == role:
            out[-1]["content"].append(block)
        else:
            out.append({"role": role, "content": [block]})

    for message in messages:
        role, content = message["role"], str(message.get("content") or "")
        if role == "system" and not out:
            system.append(content)
        elif role == "system":
            add("user", {"type": "text", "text": SYSTEM_MARK + content})
        elif role == "user":
            add("user", {"type": "text", "text": content or "(empty message)"})
        elif role == "assistant":
            if content.strip():
                add("assistant", {"type": "text", "text": content})
            for call in message.get("tool_calls") or []:
                count += 1
                pending.append(f"call_{count}")
                arguments = call["function"].get("arguments")
                add(
                    "assistant",
                    {
                        "type": "tool_use",
                        "id": pending[-1],
                        "name": call["function"]["name"],
                        "input": arguments if isinstance(arguments, dict) else {"value": arguments},
                    },
                )
                has_tools = True
        elif role == "tool" and pending:
            add("user", {"type": "tool_result", "tool_use_id": pending.pop(0), "content": content})
    if out and out[0]["role"] == "assistant":
        out.insert(
            0, {"role": "user", "content": [{"type": "text", "text": "(conversation continues)"}]}
        )
    return "\n\n".join(system), out, has_tools


def parse(response: Any, duration_ms: int) -> ModelTurn:
    usage = response.usage
    prompt = (
        usage.input_tokens
        + (usage.cache_read_input_tokens or 0)
        + (usage.cache_creation_input_tokens or 0)
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        # No usable reply; the loop treats an empty turn as the model being unavailable.
        return ModelTurn(prompt_tokens=prompt, completion_tokens=usage.output_tokens)
    text = "".join(b.text for b in response.content if b.type == "text")
    calls = [ToolCall(b.name, b.input) for b in response.content if b.type == "tool_use"]
    return ModelTurn(
        text=strip_thinking(text),
        tool_calls=calls,
        prompt_tokens=prompt,
        completion_tokens=usage.output_tokens,
        duration_ms=duration_ms,
    )


class HostedProvider:
    name = "hosted"

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        think: bool = False,
        timeout: float = 60.0,
        client: Any = None,  # an `anthropic.Anthropic`; tests pass a stand-in
    ) -> None:
        self.model = model
        self.think = think
        self.timeout = timeout
        self.client = client if client is not None else anthropic.Anthropic(api_key=api_key)
        self.usage: Counter[str] = Counter()  # summed API usage fields, for the cost record

    def reachable(self) -> bool:
        return True  # no network call for the status page; a failed call raises ModelUnavailable

    def request_body(self, messages: list[Message], tools: list[dict[str, Any]]) -> dict[str, Any]:
        system, converted, has_tools = convert(messages)
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": converted,
            "thinking": {"type": "adaptive"} if self.think else {"type": "disabled"},
            "cache_control": {"type": "ephemeral"},
        }
        if tools:
            body["tools"] = [tool_spec(t) for t in tools]
        elif has_tools:
            # The API needs tool definitions when the history holds tool blocks; none may be called.
            body["tools"] = [tool_spec(spec.schema()) for spec in REGISTRY.values()]
            body["tool_choice"] = {"type": "none"}
        return body

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelTurn:
        body = self.request_body(messages, tools)
        started = time.perf_counter()
        try:
            response = self.client.with_options(timeout=self.timeout).messages.create(**body)
        except anthropic.APIError as error:
            self.usage["errors"] += 1
            raise ModelUnavailable(type(error).__name__) from None
        for field in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            self.usage[field] += getattr(response.usage, field, None) or 0
        self.usage["calls"] += 1
        self.usage["no_tool_calls"] += body.get("tool_choice") == {"type": "none"}
        return parse(response, int((time.perf_counter() - started) * 1000))
