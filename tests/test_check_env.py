"""check_env must report problems, never raise them, and never touch the network in tests."""

from collections.abc import Mapping
from pathlib import Path

import pytest

from tools.check_env import (
    Check,
    Probes,
    load_dotenv,
    main,
    render_report,
    run_checks,
)

GOOD_TAGS = {"models": [{"name": "qwen3:8b"}, {"name": "embeddinggemma:latest"}]}
TOOLS_SHOW = {"capabilities": ["completion", "tools", "thinking"]}
KB_STATUS = {"files": 8, "chunks": 83}

Reply = Mapping[str, object] | Exception


def make_probes(
    *,
    on_path: dict[str, str] | None = None,
    commands: dict[str, str] | None = None,
    tags: Reply | None = None,
    show: Reply | None = None,
    kb: Reply | None = None,
    python: tuple[int, int, int] = (3, 13, 1),
) -> Probes:
    on_path = on_path or {}
    commands = commands or {}

    def run(args: list[str]) -> str:
        key = " ".join(args)
        if key not in commands:
            raise FileNotFoundError(args[0])
        return commands[key]

    def answer(reply: Reply | None) -> Mapping[str, object]:
        if reply is None or isinstance(reply, Exception):
            raise reply or ConnectionRefusedError("refused")
        return reply

    def fetch_json(url: str) -> Mapping[str, object]:
        if url.endswith("/api/tags"):
            return answer(tags)
        assert url.endswith("/api/status"), url
        return answer(kb)

    def post_json(url: str, body: Mapping[str, object]) -> Mapping[str, object]:
        assert url.endswith("/api/show"), url
        assert "model" in body
        return answer(show)

    return Probes(
        which=lambda name: on_path.get(name),
        run=run,
        fetch_json=fetch_json,
        post_json=post_json,
        python_version=python,
    )


def by_name(checks: list[Check]) -> dict[str, Check]:
    return {c.name: c for c in checks}


def healthy_probes() -> Probes:
    return make_probes(
        on_path={"ollama": "C:/ollama.exe", "node": "node"},
        commands={"node --version": "v24.20.0"},
        tags=GOOD_TAGS,
        show=TOOLS_SHOW,
        kb=KB_STATUS,
    )


def test_everything_present_reports_ok() -> None:
    checks = by_name(run_checks({}, healthy_probes()))
    assert all(c.ok for c in checks.values()), checks
    assert "tool calling" in checks["Ollama model qwen3:8b"].detail


def test_ollama_unreachable_when_not_installed() -> None:
    check = by_name(run_checks({}, make_probes()))["Ollama"]
    assert not check.ok
    assert "winget install" in check.hint
    assert "ollama pull qwen3:8b" in check.hint


def test_ollama_installed_but_not_running_says_to_start_it() -> None:
    check = by_name(run_checks({}, make_probes(on_path={"ollama": "C:/ollama.exe"})))["Ollama"]
    assert not check.ok
    assert "start" in check.hint.lower()
    assert "winget install" not in check.hint


def test_missing_model_gives_a_pull_command() -> None:
    probes = make_probes(tags={"models": [{"name": "llava:7b"}]})
    checks = by_name(run_checks({}, probes))
    assert checks["Ollama"].ok
    model = checks["Ollama model qwen3:8b"]
    assert not model.ok
    assert model.hint == "ollama pull qwen3:8b"


def test_model_without_tool_calling_is_flagged() -> None:
    probes = make_probes(tags=GOOD_TAGS, show={"capabilities": ["completion"]})
    model = by_name(run_checks({}, probes))["Ollama model qwen3:8b"]
    assert not model.ok
    assert "tool calling" in model.detail


def test_tool_support_unknown_is_flagged_not_raised() -> None:
    probes = make_probes(tags=GOOD_TAGS, show=TimeoutError("slow"))
    model = by_name(run_checks({}, probes))["Ollama model qwen3:8b"]
    assert not model.ok


def test_configured_model_name_is_respected() -> None:
    probes = make_probes(tags={"models": [{"name": "other:1b"}]}, show=TOOLS_SHOW)
    checks = by_name(run_checks({"CITEBOOK_MODEL": "other:1b"}, probes))
    assert checks["Ollama model other:1b"].ok
    assert "Ollama model qwen3:8b" not in checks


def test_malformed_ollama_reply_is_reported_not_raised() -> None:
    check = by_name(run_checks({}, make_probes(tags={"models": "nope"})))["Ollama"]
    assert not check.ok


def test_knowledge_base_unreachable_gives_a_hint() -> None:
    check = by_name(run_checks({}, make_probes(tags=GOOD_TAGS, show=TOOLS_SHOW)))["Knowledge base"]
    assert not check.ok
    assert "rag-project" in check.hint
    assert "FakeKB" in check.hint


def test_knowledge_base_url_setting_is_used() -> None:
    seen: list[str] = []
    base = healthy_probes()

    def fetch_json(url: str) -> Mapping[str, object]:
        seen.append(url)
        return base.fetch_json(url)

    probes = Probes(base.which, base.run, fetch_json, base.post_json, base.python_version)
    run_checks({"CITEBOOK_KB_URL": "http://127.0.0.1:8009/"}, probes)
    assert "http://127.0.0.1:8009/api/status" in seen


def test_non_http_urls_are_refused() -> None:
    checks = by_name(run_checks({"OLLAMA_URL": "file:///x"}, healthy_probes()))
    assert not checks["Ollama"].ok


def test_wrong_python_version_is_flagged() -> None:
    check = by_name(run_checks({}, make_probes(python=(3, 11, 9))))["Python"]
    assert not check.ok
    assert "3.13" in check.hint


def test_missing_node_is_flagged() -> None:
    check = by_name(run_checks({}, make_probes()))["Node.js"]
    assert not check.ok
    assert "winget install" in check.hint


def test_report_is_readable_and_lists_hints_for_failures() -> None:
    report = render_report(run_checks({}, make_probes()))
    assert "[ok]" in report
    assert "[missing]" in report
    assert "How to fix" in report


def test_report_has_no_fix_section_when_everything_is_ok() -> None:
    assert "How to fix" not in render_report(run_checks({}, healthy_probes()))


def test_main_exits_zero_even_when_everything_is_missing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([], env={}, probes=make_probes()) == 0
    assert "Citebook environment check" in capsys.readouterr().out


def test_main_strict_exits_one_when_something_is_missing() -> None:
    assert main(["--strict"], env={}, probes=make_probes()) == 1
    assert main(["--strict"], env={}, probes=healthy_probes()) == 0


def test_load_dotenv_reads_simple_lines(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        '# comment\nOLLAMA_URL=http://127.0.0.1:1234\nCITEBOOK_MODEL="m:1"\n'
        "EMPTY=\nCITEBOOK_KB_URL='http://127.0.0.1:8001'  \nnot a pair\n",
        encoding="utf-8",
    )
    assert load_dotenv(path) == {
        "OLLAMA_URL": "http://127.0.0.1:1234",
        "CITEBOOK_MODEL": "m:1",
        "CITEBOOK_KB_URL": "http://127.0.0.1:8001",
    }


def test_load_dotenv_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_dotenv(tmp_path / "nope.env") == {}
