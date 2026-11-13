"""Settings read from environment variables (and a local .env file). See docs/TRD.md section 12."""

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from citebook.models import Ablation


class Settings(BaseSettings):
    """The settings of TRD section 12. Business rules live in the database, not here."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    port: int = Field(default=8300, validation_alias="CITEBOOK_PORT")
    db_path: Path = Field(default=Path("data/citebook.db"), validation_alias="CITEBOOK_DB_PATH")

    model_provider: Literal["ollama", "hosted"] = Field(
        default="ollama", validation_alias="CITEBOOK_MODEL_PROVIDER"
    )
    ollama_url: str = Field(default="http://127.0.0.1:11434", validation_alias="OLLAMA_URL")
    model: str = Field(default="qwen3:8b", validation_alias="CITEBOOK_MODEL")
    kb_url: str = Field(default="http://127.0.0.1:8000", validation_alias="CITEBOOK_KB_URL")
    # rag-project's retrieval endpoint does not exist yet (D-055), so the fake is the default.
    kb: Literal["fake", "rag_project"] = Field(default="fake", validation_alias="CITEBOOK_KB")
    enable_thinking: bool = Field(default=False, validation_alias="CITEBOOK_ENABLE_THINKING")
    max_tool_calls_per_turn: int = Field(
        default=6, ge=1, validation_alias="CITEBOOK_MAX_TOOL_CALLS_PER_TURN"
    )
    max_consecutive_refusals: int = Field(
        default=3, ge=1, validation_alias="CITEBOOK_MAX_CONSECUTIVE_REFUSALS"
    )
    turn_timeout_s: float = Field(default=60.0, gt=0, validation_alias="CITEBOOK_TURN_TIMEOUT_S")

    allow_hosted: bool = Field(default=False, validation_alias="CITEBOOK_ALLOW_HOSTED")
    synthetic_data: bool = Field(default=True, validation_alias="CITEBOOK_SYNTHETIC_DATA")

    # A demo clock: when set, the app's time starts here and runs forward from the moment the
    # API starts, so the seeded calendar (from Monday 9 November 2026) always has open times.
    demo_start: datetime | None = Field(default=None, validation_alias="CITEBOOK_DEMO_START")

    ablation: Ablation = Field(default=Ablation.NONE, validation_alias="CITEBOOK_ABLATION")
    eval_mode: bool = Field(default=False, validation_alias="CITEBOOK_EVAL")

    @field_validator("ollama_url", "kb_url")
    @classmethod
    def http_only(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("service URLs must start with http:// or https://")
        return value.rstrip("/")

    @field_validator("demo_start")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError(
                "CITEBOOK_DEMO_START needs a UTC offset, e.g. 2026-11-09T07:00:00-08:00"
            )
        return value

    @model_validator(mode="after")
    def safety_switches(self) -> "Settings":
        if self.model_provider == "hosted" and not self.allow_hosted:
            raise ValueError(
                "CITEBOOK_MODEL_PROVIDER=hosted requires CITEBOOK_ALLOW_HOSTED=true. "
                "Hosted models send conversation text to a third party and are off by default."
            )
        if self.model_provider == "hosted":
            # The opt-in exists, the provider does not: refuse rather than run the local
            # model under a setting that says otherwise (D-093).
            raise ValueError(
                "CITEBOOK_MODEL_PROVIDER=hosted: this version has no hosted model provider. "
                "Use ollama (the default)."
            )
        if self.ablation != Ablation.NONE and not self.eval_mode:
            raise ValueError(
                "CITEBOOK_ABLATION switches off safety layers and is allowed only in the "
                "evaluation runner (CITEBOOK_EVAL=true)."
            )
        return self
