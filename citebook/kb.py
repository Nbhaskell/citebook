"""Knowledge base: the `KnowledgeBase` interface, `RagProjectKB` and `FakeKB` (TRD section 6.1).

`RagProjectKB` calls rag-project's retrieval-only `POST /api/search` (D-019, D-055). That endpoint
is a separate change in the rag-project repo; until it exists, `get_policy` runs on `FakeKB`.
Retrieved passages are business documents and are treated as untrusted data: the tool marks
them, and nothing in a passage can unlock a tool.

`retrieve_policy` applies the relevance threshold: a passage whose distance is above the knowledge
base's `max_distance` is dropped, and with none left the result is `found = False`, so the
assistant says it does not know instead of answering from model memory.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

MAX_PASSAGES = 4
KB_DOCS_DIR = Path(__file__).resolve().parent.parent / "kb" / "docs"


class KBUnavailable(Exception):
    """The knowledge base could not be reached or answered with something unusable."""


@dataclass(frozen=True)
class Passage:
    source: str
    section: str
    text: str
    distance: float  # smaller is closer

    @property
    def source_id(self) -> str:
        return f"{self.source}#{self.section}" if self.section else self.source


class KnowledgeBase(Protocol):
    max_distance: float

    def search(self, question: str, k: int) -> list[Passage]:
        """Up to `k` passages, closest first. Raises KBUnavailable."""
        ...


@dataclass(frozen=True)
class PolicyResult:
    found: bool
    passages: list[Passage]
    unavailable: bool = False


def retrieve_policy(kb: KnowledgeBase, question: str) -> PolicyResult:
    try:
        passages = kb.search(question, MAX_PASSAGES)
    except KBUnavailable:
        return PolicyResult(found=False, passages=[], unavailable=True)
    relevant = sorted(
        (p for p in passages if p.distance <= kb.max_distance), key=lambda p: p.distance
    )[:MAX_PASSAGES]
    return PolicyResult(found=bool(relevant), passages=relevant)


# --- rag-project ------------------------------------------------------------------------------


class RagProjectKB:
    """rag-project's `POST /api/search`: {"question", "k"} -> {"passages": [{source, section,
    text, distance}]}. No model call on that side."""

    def __init__(self, base_url: str, timeout: float = 5.0, max_distance: float = 0.6) -> None:
        self.url = base_url.rstrip("/") + "/api/search"
        self.timeout = timeout
        self.max_distance = max_distance

    def search(self, question: str, k: int) -> list[Passage]:
        body = json.dumps({"question": question, "k": k}).encode("utf-8")
        request = urllib.request.Request(
            self.url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            return [
                Passage(
                    source=str(p["source"]),
                    section=str(p.get("section") or ""),
                    text=str(p["text"]),
                    distance=float(p["distance"]),
                )
                for p in data["passages"]
            ][:k]
        except (OSError, urllib.error.URLError, ValueError, KeyError, TypeError) as error:
            raise KBUnavailable(type(error).__name__) from error


# --- fake for tests and until rag-project's endpoint exists ------------------------------------

_WORD = re.compile(r"[a-z0-9$]+(?:'[a-z]+)?")
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "am",
        "be",
        "can",
        "do",
        "does",
        "for",
        "from",
        "have",
        "how",
        "i",
        "if",
        "in",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "the",
        "there",
        "this",
        "to",
        "was",
        "we",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
    ]
)


def _terms(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


def _matches(term: str, words: set[str]) -> bool:
    if term in words:
        return True
    return len(term) >= 4 and any(
        len(w) >= 4 and (w.startswith(term) or term.startswith(w)) for w in words
    )


class FakeKB:
    """Word-overlap retrieval over markdown sections. Distance is the share of the question's
    meaningful words that the section lacks, so 0.0 is a full match."""

    def __init__(self, passages: list[Passage], max_distance: float = 0.4) -> None:
        self.passages = passages
        self.max_distance = max_distance
        self.fail = False  # tests set this to simulate an outage

    @classmethod
    def from_docs(cls, directory: Path = KB_DOCS_DIR, max_distance: float = 0.4) -> FakeKB:
        passages = []
        for path in sorted(directory.glob("*.md")):
            for heading, body in _sections(path.read_text(encoding="utf-8")):
                passages.append(Passage(path.name, heading, body, 1.0))
        return cls(passages, max_distance)

    def search(self, question: str, k: int) -> list[Passage]:
        if self.fail:
            raise KBUnavailable("simulated")
        terms = _terms(question)
        if not terms:
            return []
        scored = []
        for passage in self.passages:
            words = set(_terms(f"{passage.section} {passage.text}"))
            share = sum(_matches(t, words) for t in terms) / len(terms)
            distance = round(1 - share, 3)
            scored.append(Passage(passage.source, passage.section, passage.text, distance))
        scored.sort(key=lambda p: (p.distance, p.source, p.section))
        return scored[:k]


def _sections(markdown: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    heading, lines = "", list[str]()
    for line in markdown.splitlines():
        if line.startswith("## "):
            if heading and "".join(lines).strip():
                found.append((heading, "\n".join(lines).strip()))
            heading, lines = line[3:].strip(), []
        elif heading:
            lines.append(line)
    if heading and "".join(lines).strip():
        found.append((heading, "\n".join(lines).strip()))
    return found
