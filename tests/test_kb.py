"""Knowledge base: FakeKB retrieval, the relevance threshold and RagProjectKB's contract."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, ClassVar

import pytest

from citebook.kb import (
    MAX_PASSAGES,
    FakeKB,
    KBUnavailable,
    Passage,
    RagProjectKB,
    retrieve_policy,
)


@pytest.fixture(scope="module")
def kb() -> FakeKB:
    return FakeKB.from_docs()


def test_fake_kb_reads_every_section_of_the_kb_folder(kb: FakeKB) -> None:
    sources = {p.source for p in kb.passages}
    assert {"policies.md", "online-booking.md", "hours.md", "pricing.md"} <= sources
    assert all(p.text and p.section for p in kb.passages)


@pytest.mark.parametrize(
    ("question", "source"),
    [
        ("How much notice do I need to cancel a booking?", "online-booking.md"),
        ("What is the late cancellation fee?", "policies.md"),
        ("What are your office hours on Saturday?", "hours.md"),
        ("Is there a travel fee for Saltmarsh?", "online-booking.md"),
    ],
)
def test_policy_questions_find_their_document(kb: FakeKB, question: str, source: str) -> None:
    result = retrieve_policy(kb, question)
    assert result.found
    assert source in {p.source for p in result.passages}
    assert len(result.passages) <= MAX_PASSAGES
    assert [p.distance for p in result.passages] == sorted(p.distance for p in result.passages)


@pytest.mark.parametrize(
    "question", ["Do you repair cars?", "What is the weather like?", "", "the and of"]
)
def test_unrelated_questions_find_nothing(kb: FakeKB, question: str) -> None:
    result = retrieve_policy(kb, question)
    assert not result.found and result.passages == []


def test_the_threshold_drops_distant_passages() -> None:
    near = Passage("a.md", "Fees", "The late fee is $45.", 0.0)
    far = Passage("b.md", "Pets", "Keep pets away.", 1.0)
    kb = FakeKB([near, far], max_distance=0.4)
    result = retrieve_policy(kb, "late fee")
    assert [p.source for p in result.passages] == ["a.md"]


def test_an_outage_is_not_found_and_says_so(kb: FakeKB) -> None:
    broken = FakeKB(kb.passages)
    broken.fail = True
    result = retrieve_policy(broken, "office hours")
    assert result.unavailable and not result.found


# --- RagProjectKB against a local stand-in server ---------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    reply: ClassVar[Any] = None
    seen: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        _Handler.seen.append(json.loads(self.rfile.read(length)))
        if self.path != "/api/search":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(_Handler.reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def stand_in() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_rag_project_kb_posts_the_question_and_reads_passages(stand_in: str) -> None:
    _Handler.seen = []
    _Handler.reply = {
        "passages": [
            {"source": "policies.md", "section": "Fees", "text": "Late fee $45.", "distance": 0.2},
            {"source": "faq.md", "section": None, "text": "Other.", "distance": 0.9},
        ]
    }
    kb = RagProjectKB(stand_in)
    result = retrieve_policy(kb, "late fee?")
    assert _Handler.seen == [{"question": "late fee?", "k": MAX_PASSAGES}]
    assert [p.source_id for p in result.passages] == ["policies.md#Fees"]


@pytest.mark.parametrize("reply", [{"answer": "no passages key"}, {"passages": [{"x": 1}]}])
def test_rag_project_kb_refuses_an_unusable_reply(stand_in: str, reply: Any) -> None:
    _Handler.reply = reply
    with pytest.raises(KBUnavailable):
        RagProjectKB(stand_in).search("q", 4)


def test_rag_project_kb_reports_a_missing_endpoint_as_unavailable() -> None:
    kb = RagProjectKB("http://127.0.0.1:9", timeout=0.5)  # nothing listens on port 9
    assert retrieve_policy(kb, "office hours").unavailable


@pytest.mark.skipif(
    os.environ.get("CITEBOOK_KB_INTEGRATION") != "true",
    reason="needs rag-project's POST /api/search on CITEBOOK_KB_URL (D-055)",
)
def test_rag_project_integration() -> None:
    from citebook.config import Settings

    result = retrieve_policy(RagProjectKB(Settings().kb_url), "How much is the call-out fee?")
    assert result.found
    assert any("$89" in p.text for p in result.passages)
