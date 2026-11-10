"""Rule 4: no network client other than the model provider and the knowledge base.

Messages are never sent, so no module may import SMTP, SMS or general HTTP clients. Network
imports are allowed only in the knowledge-base module and the model providers.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "citebook"
NETWORK = {
    "smtplib",
    "poplib",
    "imaplib",
    "ftplib",
    "telnetlib",
    "socket",
    "ssl",
    "http.client",
    "urllib.request",
    "requests",
    "httpx",
    "aiohttp",
    "websockets",
    "twilio",
    "boto3",
}
ALLOWED = {"kb.py"}  # plus everything in citebook/providers/
NEVER = {"smtplib", "poplib", "imaplib", "twilio", "boto3"}


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _network(names: set[str]) -> set[str]:
    return {n for n in names if any(n == m or n.startswith(m + ".") for m in NETWORK)}


def test_only_the_kb_and_providers_import_network_clients() -> None:
    offenders = {}
    for path in PACKAGE.rglob("*.py"):
        relative = path.relative_to(PACKAGE)
        if relative.parts[0] == "providers" or str(relative) in ALLOWED:
            continue
        hits = _network(_imports(path))
        if hits:
            offenders[str(relative)] = sorted(hits)
    assert offenders == {}


def test_nothing_imports_a_messaging_client() -> None:
    for path in PACKAGE.rglob("*.py"):
        assert not (_network(_imports(path)) & NEVER), path


def test_the_kb_module_is_seen_as_a_network_client() -> None:
    assert _network(_imports(PACKAGE / "kb.py")) == {"urllib.request"}
