"""Pytest test session configuration and environment isolation."""

import os
import pytest


@pytest.fixture(autouse=True, scope="session")
def configure_test_environment():
    """Ensure test suite runs against deterministic mock provider without requiring live Ollama."""
    old_provider = os.environ.get("ANIM_LLM_SCRIPT_PROVIDER")
    os.environ["ANIM_LLM_SCRIPT_PROVIDER"] = "mock"
    yield
    if old_provider is None:
        os.environ.pop("ANIM_LLM_SCRIPT_PROVIDER", None)
    else:
        os.environ["ANIM_LLM_SCRIPT_PROVIDER"] = old_provider
