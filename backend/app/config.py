"""Application configuration loaded from environment variables and .env file."""

from pathlib import Path
from typing import Literal, Optional
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT: Path = Path(__file__).resolve().parent.parent.parent
DOTENV_PATH: Path = REPO_ROOT / ".env"


class Settings(BaseSettings):
    """Configuration settings prefixed with ANIM_ per TechSpec Section 9."""

    model_config = SettingsConfigDict(
        env_prefix="ANIM_",
        env_file=(str(DOTENV_PATH), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATA_DIR: Path = Path("data")
    ASSETS_DIR: Path = Path("assets")
    PROJECTS_DIR: Path = Path("projects")
    FPS: int = 24
    WIDTH: int = 1080
    HEIGHT: int = 1920
    ENCODER: Literal["auto", "nvenc", "x264"] = "auto"
    WORDS_PER_MINUTE: int = 140
    LLM_SCRIPT_PROVIDER: str = "mock"
    LLM_PLAN_PROVIDER: str = "ollama"
    OLLAMA_MODEL: str = "qwen2.5:7b"
    OLLAMA_HOST: str = "http://localhost:11434"
    OLLAMA_TIMEOUT_SECONDS: float = 60.0
    TTS_ADAPTER: str = "fake"

    # Research tools configuration (Phase 1)
    TAVILY_API_KEY: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("ANIM_TAVILY_API_KEY", "TAVILY_API_KEY"),
    )
    FIRECRAWL_API_KEY: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("ANIM_FIRECRAWL_API_KEY", "FIRECRAWL_API_KEY"),
    )
    FIRECRAWL_API_URL: str = Field(
        default="https://api.firecrawl.dev/v1/scrape",
        validation_alias=AliasChoices("ANIM_FIRECRAWL_API_URL", "FIRECRAWL_API_URL"),
    )
    RESEARCH_MAX_SEARCH_CALLS: int = 3
    RESEARCH_MAX_EXTRACT_CALLS: int = 3
    RESEARCH_CALL_TIMEOUT_SECONDS: float = 15.0
    RESEARCH_DEADLINE_SECONDS: float = 60.0
    RESEARCH_MAX_PAGE_CHARS: int = 15000
    RESEARCH_MAX_TOTAL_CHARS: int = 45000


def get_settings() -> Settings:
    """Return a cached or newly instantiated Settings object."""
    return Settings()
