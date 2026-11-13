from pathlib import Path

import pytest
from pydantic import ValidationError

from citebook.config import Settings


def make() -> Settings:
    """Build settings from the environment only, never from a local .env file."""
    return Settings(_env_file=None)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [
        "CITEBOOK_PORT",
        "CITEBOOK_DB_PATH",
        "CITEBOOK_MODEL_PROVIDER",
        "OLLAMA_URL",
        "CITEBOOK_MODEL",
        "CITEBOOK_KB_URL",
        "CITEBOOK_ALLOW_HOSTED",
        "CITEBOOK_SYNTHETIC_DATA",
        "CITEBOOK_ABLATION",
        "CITEBOOK_EVAL",
    ]:
        monkeypatch.delenv(name, raising=False)


def test_defaults_match_trd() -> None:
    s = make()
    assert s.port == 8300
    assert s.db_path == Path("data/citebook.db")
    assert s.model_provider == "ollama"
    assert s.ollama_url == "http://127.0.0.1:11434"
    assert s.model == "qwen3:8b"
    assert s.kb_url == "http://127.0.0.1:8000"
    assert s.allow_hosted is False
    assert s.synthetic_data is True
    assert s.ablation == "none"
    assert s.eval_mode is False


def test_documented_variable_names_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CITEBOOK_PORT", "8399")
    monkeypatch.setenv("CITEBOOK_MODEL", "qwen3:14b")
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11435")
    s = make()
    assert s.port == 8399
    assert s.model == "qwen3:14b"
    assert s.ollama_url == "http://localhost:11435"


def test_empty_values_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CITEBOOK_PORT", "")
    monkeypatch.setenv("CITEBOOK_MODEL", "")
    s = make()
    assert s.port == 8300
    assert s.model == "qwen3:8b"


def test_hosted_provider_needs_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CITEBOOK_MODEL_PROVIDER", "hosted")
    with pytest.raises(ValidationError, match="CITEBOOK_ALLOW_HOSTED"):
        make()
    # With the opt-in, the API still refuses: this version has no hosted provider (D-093).
    monkeypatch.setenv("CITEBOOK_ALLOW_HOSTED", "true")
    with pytest.raises(ValidationError, match="no hosted model provider"):
        make()


@pytest.mark.parametrize("value", ["baseline", "no_gate_policy", "no_reply_checks"])
def test_ablation_refused_outside_evaluation(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("CITEBOOK_ABLATION", value)
    with pytest.raises(ValidationError, match="CITEBOOK_EVAL"):
        make()
    monkeypatch.setenv("CITEBOOK_EVAL", "true")
    assert make().ablation == value


def test_unknown_ablation_value_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CITEBOOK_EVAL", "true")
    monkeypatch.setenv("CITEBOOK_ABLATION", "no_confirmation")
    with pytest.raises(ValidationError):
        make()


@pytest.mark.parametrize("name", ["OLLAMA_URL", "CITEBOOK_KB_URL"])
def test_service_urls_must_be_http(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, "file:///etc/passwd")
    with pytest.raises(ValidationError):
        make()
