"""HTTP API. Phase 0 has only the status route (docs/TRD.md section 11)."""

from fastapi import FastAPI
from pydantic import BaseModel

from citebook import __version__
from citebook.config import Ablation, Settings


class Status(BaseModel):
    """What the service can say about itself. `None` means "not checked yet": the parts that
    would be checked (model, knowledge base, database) are built in later phases."""

    service: str
    version: str
    provider: str
    model: str
    synthetic_data: bool
    ablation: Ablation
    model_reachable: bool | None
    kb_reachable: bool | None
    database: str | None


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="Citebook API", version=__version__)

    @app.get("/api/status")
    def status() -> Status:
        return Status(
            service="citebook",
            version=__version__,
            provider=settings.model_provider,
            model=settings.model,
            synthetic_data=settings.synthetic_data,
            ablation=settings.ablation,
            model_reachable=None,
            kb_reachable=None,
            database=None,
        )

    return app
