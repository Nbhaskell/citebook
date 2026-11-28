"""Model providers. `make_provider` builds the one the settings name (local Ollama by default)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from citebook.providers.base import ModelProvider

if TYPE_CHECKING:
    from citebook.config import Settings


def make_provider(settings: Settings, think: bool) -> ModelProvider:
    if settings.model_provider == "hosted":
        from citebook.providers.hosted import HostedProvider  # the SDK loads only when used

        key = settings.anthropic_api_key
        return HostedProvider(
            settings.model,
            api_key=key.get_secret_value() if key else None,
            think=think,
            timeout=settings.turn_timeout_s,
        )
    from citebook.providers.ollama import OllamaProvider

    return OllamaProvider(
        settings.ollama_url, settings.model, think=think, timeout=settings.turn_timeout_s
    )
