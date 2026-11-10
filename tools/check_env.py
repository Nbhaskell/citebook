"""Report whether this machine is ready to run Citebook.

Run from the repository root:  python tools/check_env.py   (add --strict to exit 1 on problems)

Uses only the standard library so it works before the virtual environment exists. It reports
problems and prints Windows install hints; it never installs anything and exits 0 unless
--strict is given, so it can never fail the test suite.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:8b"
DEFAULT_KB_URL = "http://127.0.0.1:8000"
REQUIRED_PYTHON = (3, 13)

OLLAMA_INSTALL_HINT = (
    "winget install --id Ollama.Ollama  (then start Ollama and run: ollama pull qwen3:8b)"
)
OLLAMA_START_HINT = (
    "Ollama is installed but not answering. Start the Ollama app, or run: ollama serve"
)
PYTHON_HINT = "winget install --id Python.Python.3.13  (the project needs Python 3.13)"
NODE_HINT = "winget install --id OpenJS.NodeJS.LTS  (the web app needs Node.js 20.19 or newer)"
KB_HINT = (
    "Start rag-project on port 8000 with RAG_DOCS_DIR set to Citebook's knowledge-base folder "
    "(docs/TRD.md section 6.1). Not needed until Phase 3; tests use FakeKB."
)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    hint: str = ""


@dataclass(frozen=True)
class Probes:
    """Everything that touches the machine, so tests can replace it."""

    which: Callable[[str], str | None]
    run: Callable[[list[str]], str]
    fetch_json: Callable[[str], Mapping[str, object]]
    post_json: Callable[[str, Mapping[str, object]], Mapping[str, object]]
    python_version: tuple[int, int, int]


def _http_only(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        raise ValueError("service URLs must start with http:// or https://")
    return url


def _read_json(request: urllib.request.Request | str, timeout: float) -> dict[str, object]:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.load(response)
    if not isinstance(data, dict):
        raise ValueError("unexpected reply")
    return data


def _run(args: list[str]) -> str:
    done = subprocess.run(args, capture_output=True, text=True, timeout=10, check=True)
    return (done.stdout + done.stderr).strip()


def _fetch_json(url: str) -> dict[str, object]:
    return _read_json(_http_only(url), timeout=3)


def _post_json(url: str, body: Mapping[str, object]) -> dict[str, object]:
    request = urllib.request.Request(
        _http_only(url),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return _read_json(request, timeout=10)


def default_probes() -> Probes:
    info = sys.version_info
    return Probes(
        which=shutil.which,
        run=_run,
        fetch_json=_fetch_json,
        post_json=_post_json,
        python_version=(info.major, info.minor, info.micro),
    )


def load_dotenv(path: Path) -> dict[str, str]:
    """Read KEY=VALUE lines. Blank values are skipped, like the app's settings do."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip("\"'").strip()
        if key.strip() and value:
            values[key.strip()] = value
    return values


def _check_python(probes: Probes) -> Check:
    major, minor, micro = probes.python_version
    found = f"{major}.{minor}.{micro}"
    if (major, minor) == REQUIRED_PYTHON:
        return Check("Python", True, found)
    return Check("Python", False, f"{found} (need 3.13)", PYTHON_HINT)


def _check_node(probes: Probes) -> Check:
    path = probes.which("node")
    if not path:
        return Check("Node.js", False, "not found", NODE_HINT)
    try:
        return Check("Node.js", True, probes.run([path, "--version"]).splitlines()[0])
    except Exception as error:
        return Check("Node.js", False, f"found but would not run ({type(error).__name__})")


def _pulled_models(reply: Mapping[str, object]) -> list[str]:
    models = reply.get("models")
    if not isinstance(models, list):
        raise ValueError("unexpected reply")
    names: list[str] = []
    for item in models:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError("unexpected reply")
        names.append(item["name"])
    return names


def _check_tool_calling(url: str, model: str, probes: Probes) -> Check:
    name = f"Ollama model {model}"
    try:
        capabilities = probes.post_json(f"{url}/api/show", {"model": model}).get("capabilities")
    except Exception as error:
        return Check(name, False, f"pulled; tool calling not confirmed ({type(error).__name__})")
    if isinstance(capabilities, list) and "tools" in capabilities:
        return Check(name, True, "pulled; supports tool calling")
    hint = "Citebook needs a model with tool calling (docs/TRD.md section 2)."
    return Check(name, False, "pulled, but Ollama does not list tool calling for it", hint)


def _check_ollama(env: Mapping[str, str], probes: Probes) -> list[Check]:
    url = env.get("OLLAMA_URL", DEFAULT_OLLAMA_URL).rstrip("/")
    try:
        names = _pulled_models(probes.fetch_json(f"{_http_only(url)}/api/tags"))
    except Exception as error:
        hint = OLLAMA_START_HINT if probes.which("ollama") else OLLAMA_INSTALL_HINT
        return [Check("Ollama", False, f"not reachable at {url} ({type(error).__name__})", hint)]

    checks = [Check("Ollama", True, f"reachable at {url}; {len(names)} models pulled")]
    model = env.get("CITEBOOK_MODEL", DEFAULT_MODEL)
    present = model in names or (":" not in model and f"{model}:latest" in names)
    if not present:
        checks.append(Check(f"Ollama model {model}", False, "not pulled", f"ollama pull {model}"))
    else:
        checks.append(_check_tool_calling(url, model, probes))
    return checks


def _check_knowledge_base(env: Mapping[str, str], probes: Probes) -> Check:
    url = env.get("CITEBOOK_KB_URL", DEFAULT_KB_URL).rstrip("/")
    try:
        reply = probes.fetch_json(f"{_http_only(url)}/api/status")
    except Exception as error:
        return Check(
            "Knowledge base", False, f"not reachable at {url} ({type(error).__name__})", KB_HINT
        )
    files = reply.get("files")
    return Check("Knowledge base", True, f"rag-project reachable at {url}; files indexed: {files}")


def run_checks(env: Mapping[str, str], probes: Probes) -> list[Check]:
    return [
        _check_python(probes),
        _check_node(probes),
        *_check_ollama(env, probes),
        _check_knowledge_base(env, probes),
    ]


def render_report(checks: Sequence[Check]) -> str:
    title = "Citebook environment check"
    lines = [title, "=" * len(title), ""]
    for check in checks:
        status = "[ok]" if check.ok else "[missing]"
        lines.append(f"{status:<10}{check.name:<28}{check.detail}")
    problems = [c for c in checks if not c.ok]
    if problems:
        lines += ["", "How to fix", "----------"]
        lines += [f"{c.name}: {c.hint}" for c in problems if c.hint]
    else:
        lines += ["", "Everything needed for the current phase is in place."]
    return "\n".join(lines)


def main(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    probes: Probes | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="Check the Citebook development environment.")
    parser.add_argument("--strict", action="store_true", help="exit 1 if anything is missing")
    args = parser.parse_args(argv)

    if env is None:
        repo_root = Path(__file__).resolve().parent.parent
        env = {**load_dotenv(repo_root / ".env"), **os.environ}
    checks = run_checks(env, probes or default_probes())
    print(render_report(checks))
    return 1 if args.strict and any(not c.ok for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
