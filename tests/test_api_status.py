from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from citebook import __main__ as entry
from citebook.api import create_app
from citebook.config import Settings
from citebook.kb import FakeKB
from citebook.providers.fake import FakeProvider
from tests.conftest import seeded


def client(db_path: Path | None = None, kb: FakeKB | None = None) -> TestClient:
    extra = {"CITEBOOK_DB_PATH": str(db_path)} if db_path else {}
    settings = Settings(_env_file=None, **extra)  # type: ignore[arg-type]
    return TestClient(create_app(settings, provider=FakeProvider(), kb=kb or FakeKB([])))


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [
        "CITEBOOK_MODEL",
        "CITEBOOK_MODEL_PROVIDER",
        "CITEBOOK_ABLATION",
        "CITEBOOK_EVAL",
        "CITEBOOK_KB",
        "CITEBOOK_DB_PATH",
    ]:
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
    assert body["kb"] == "fake"


def test_status_checks_the_model_kb_and_database(tmp_path: Path) -> None:
    path = tmp_path / "citebook.db"
    seeded(str(path)).close()
    body = client(path).get("/api/status").json()
    assert body["model_reachable"] is True
    assert body["kb_reachable"] is True
    assert body["database"].startswith("ok (migration 0")


def test_status_reports_problems(tmp_path: Path) -> None:
    down = FakeKB([])
    down.fail = True
    body = client(tmp_path / "missing.db", down).get("/api/status").json()
    assert body["kb_reachable"] is False
    assert body["database"].startswith("missing")


def test_status_names_an_ablation_in_force(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CITEBOOK_EVAL", "true")
    monkeypatch.setenv("CITEBOOK_ABLATION", "baseline")
    body = client().get("/api/status").json()
    assert body["ablation"] == "baseline"


def test_server_binds_to_loopback_only() -> None:
    assert entry.HOST == "127.0.0.1"
