"""LLM integration interfaces, provider implementations, and factory."""

from typing import Optional

from backend.app.config import Settings, get_settings
from backend.app.llm.base import (
    LLMProvider,
    LLMProviderError,
    LLMResponseMalformedError,
    LLMUnavailableError,
)
from backend.app.llm.mock import MockLLMProvider
from backend.app.llm.ollama import OllamaLLMProvider


def get_llm_provider(
    name: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> LLMProvider:
    """Return the configured LLMProvider instance."""
    cfg = settings or get_settings()
    provider_name = (name or cfg.LLM_SCRIPT_PROVIDER or "mock").strip().lower()

    if provider_name == "mock":
        return MockLLMProvider()
    if provider_name == "ollama":
        return OllamaLLMProvider(settings=cfg)

    raise LLMProviderError(f"Unsupported or unconfigured LLM provider: '{provider_name}'.")


__all__ = [
    "LLMProvider",
    "LLMProviderError",
    "LLMResponseMalformedError",
    "LLMUnavailableError",
    "MockLLMProvider",
    "OllamaLLMProvider",
    "get_llm_provider",
]
