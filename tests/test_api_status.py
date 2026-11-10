import pytest
from fastapi.testclient import TestClient

from citebook import __main__ as entry
from citebook.api import create_app
from citebook.config import Settings


def client() -> TestClient:
    return TestClient(create_app(Settings(_env_file=None)))


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ["CITEBOOK_MODEL", "CITEBOOK_MODEL_PROVIDER", "CITEBOOK_ABLATION", "CITEBOOK_EVAL"]:
        monkeypatch.delenv(name, raising=False)


def test_status_reports_configuration() -> None:
    response = client().get("/api/status")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "citebook"
    assert body["provider"] == "ollama"
    assert body["model"] == "qwen3:8b"
    assert body["synthetic_data"] is True
    assert body["ablation"] == "none"


def test_status_does_not_claim_checks_that_do_not_exist_yet() -> None:
    body = client().get("/api/status").json()
    assert body["model_reachable"] is None
    assert body["kb_reachable"] is None
    assert body["database"] is None


def test_status_names_an_ablation_in_force(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CITEBOOK_EVAL", "true")
    monkeypatch.setenv("CITEBOOK_ABLATION", "baseline")
    body = client().get("/api/status").json()
    assert body["ablation"] == "baseline"


def test_server_binds_to_loopback_only() -> None:
    assert entry.HOST == "127.0.0.1"
