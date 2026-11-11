"""Model providers: the recorded Ollama contract, error handling and the scripted fake."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

from citebook.providers.base import ModelTurn, ModelUnavailable, ToolCall, strip_thinking
from citebook.providers.fake import FakeProvider, say, use
from citebook.providers.ollama import OllamaProvider, parse_reply

FIXTURES = Path(__file__).parent / "fixtures"


def recorded(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


class _Ollama(BaseHTTPRequestHandler):
    reply: ClassVar[Any] = None
    status: ClassVar[int] = 200
    seen: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        _Ollama.seen.append({"path": self.path, "body": json.loads(self.rfile.read(length))})
        body = json.dumps(_Ollama.reply).encode() if _Ollama.reply is not None else b"not json"
        self.send_response(_Ollama.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def ollama() -> Iterator[str]:
    _Ollama.seen, _Ollama.status = [], 200
    server = HTTPServer(("127.0.0.1", 0), _Ollama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_recorded_tool_call_is_read_as_a_tool_call(ollama: str) -> None:
    record = recorded("ollama_tool_call.json")
    _Ollama.reply = record["response"]
    provider = OllamaProvider(ollama, "qwen3:8b")
    turn = provider.chat(record["request"]["messages"], record["request"]["tools"])
    assert turn.tool_calls == [
        ToolCall("quote_price", {"service_code": "faucet replacement", "zip_code": "00012"})
    ]
    assert turn.text == ""
    assert (turn.prompt_tokens, turn.completion_tokens) == (176, 35)
    sent = _Ollama.seen[0]
    assert sent["path"] == "/api/chat"
    assert sent["body"]["stream"] is False and sent["body"]["think"] is False
    assert sent["body"]["options"]["temperature"] == 0
    assert sent["body"]["tools"] == record["request"]["tools"]


def test_recorded_text_reply_is_read_as_text(ollama: str) -> None:
    record = recorded("ollama_text.json")
    _Ollama.reply = record["response"]
    turn = OllamaProvider(ollama, "qwen3:8b").chat(record["request"]["messages"], [])
    assert turn.tool_calls == [] and turn.text == record["response"]["message"]["content"]
    assert "tools" not in _Ollama.seen[0]["body"]


def test_the_request_matches_the_recorded_request() -> None:
    record = recorded("ollama_tool_call.json")["request"]
    body = OllamaProvider("http://x", "qwen3:8b").request_body(record["messages"], record["tools"])
    assert body == record


def test_arguments_given_as_a_json_string_are_read() -> None:
    reply = {"message": {"tool_calls": [{"function": {"name": "x", "arguments": '{"a": 1}'}}]}}
    assert parse_reply(reply).tool_calls == [ToolCall("x", {"a": 1})]
    broken = {"message": {"tool_calls": [{"function": {"name": "x", "arguments": "{oops"}}]}}
    assert parse_reply(broken).tool_calls == [ToolCall("x", "{oops")]


def test_a_tool_call_written_as_text_is_read_and_never_shown() -> None:
    content = '<tool_call>\n{"name": "find_booking", "arguments": {}}\n</tool_call>'
    turn = parse_reply({"message": {"content": content}})
    assert turn.tool_calls == [ToolCall("find_booking", {})]
    assert turn.text == ""
    broken = parse_reply({"message": {"content": "<tool_call>{oops}</tool_call> Hi"}})
    assert broken.tool_calls == [] and broken.text == "Hi"


@pytest.mark.parametrize("reply", [{}, {"message": None}, {"message": {"tool_calls": [{}]}}, None])
def test_an_unusable_reply_is_model_unavailable(ollama: str, reply: Any) -> None:
    _Ollama.reply = reply
    with pytest.raises(ModelUnavailable):
        OllamaProvider(ollama, "qwen3:8b").chat([], [])


def test_a_server_error_or_no_server_is_model_unavailable(ollama: str) -> None:
    _Ollama.reply, _Ollama.status = {"error": "model not found"}, 404
    with pytest.raises(ModelUnavailable):
        OllamaProvider(ollama, "qwen3:8b").chat([], [])
    with pytest.raises(ModelUnavailable):
        OllamaProvider("http://127.0.0.1:9", "qwen3:8b", timeout=0.5).chat([], [])


def test_thinking_text_is_removed() -> None:
    assert strip_thinking("<think>\nhmm $5\n</think>\n\nHello") == "Hello"


def test_fake_provider_plays_its_script_and_records_calls() -> None:
    fake = FakeProvider(
        [
            use("approve_hold", action_id="a1"),
            ModelUnavailable("down"),
            say("Hi"),
            lambda m, t: say(str(len(m))),
        ]
    )
    tools = [{"type": "function", "function": {"name": "list_services"}}]
    assert fake.chat([{"role": "user", "content": "x"}], tools).tool_calls[0].name == "approve_hold"
    with pytest.raises(ModelUnavailable):
        fake.chat([], [])
    assert fake.chat([], []) == ModelTurn(text="Hi")
    assert fake.chat([{}, {}], []).text == "2"
    assert fake.offered(0) == ["list_services"]
    with pytest.raises(AssertionError):
        fake.chat([], [])
