"""OllamaProvider: native tool calling through Ollama's `POST /api/chat` on this machine.

Temperature 0, a fixed seed, thinking off unless CITEBOOK_ENABLE_THINKING is true, no streaming.
Uses the standard library's urllib, so no HTTP client dependency is added.
"""

from __future__ import annotations

import contextlib
import json
import time
import urllib.error
import urllib.request
from typing import Any

from citebook.providers.base import Message, ModelTurn, ModelUnavailable, ToolCall, strip_thinking

SEED = 7


class OllamaProvider:
    name = "ollama"

    def __init__(
        self, base_url: str, model: str, *, think: bool = False, timeout: float = 60.0
    ) -> None:
        self.url = base_url.rstrip("/") + "/api/chat"
        self.model = model
        self.think = think
        self.timeout = timeout

    def request_body(self, messages: list[Message], tools: list[dict[str, Any]]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": self.think,
            "options": {"temperature": 0, "seed": SEED},
        }
        if tools:
            body["tools"] = tools
        return body

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelTurn:
        data = json.dumps(self.request_body(messages, tools)).encode("utf-8")
        request = urllib.request.Request(
            self.url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                reply = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, ValueError) as error:
            raise ModelUnavailable(type(error).__name__) from error
        return parse_reply(reply, int((time.perf_counter() - started) * 1000))


def parse_reply(reply: Any, duration_ms: int = 0) -> ModelTurn:
    """Read Ollama's chat reply. Tool arguments may come as an object or as a JSON string."""
    try:
        message = reply["message"]
        calls = []
        for call in message.get("tool_calls") or []:
            function = call["function"]
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                # Text that is not JSON stays text; the gate refuses it as INVALID_ARGUMENTS.
                with contextlib.suppress(ValueError):
                    arguments = json.loads(arguments)
            calls.append(ToolCall(name=str(function["name"]), arguments=arguments))
        return ModelTurn(
            text=strip_thinking(str(message.get("content") or "")),
            tool_calls=calls,
            prompt_tokens=int(reply.get("prompt_eval_count") or 0),
            completion_tokens=int(reply.get("eval_count") or 0),
            duration_ms=duration_ms,
        )
    except (KeyError, TypeError, AttributeError) as error:
        raise ModelUnavailable("unusable reply") from error
