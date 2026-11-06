"""Research tool factory for constructing real and test research registries and orchestrators."""

from typing import TYPE_CHECKING, Any, Dict, List, Optional
import httpx

from backend.app.config import Settings, get_settings
from backend.app.services.research.tools.base import (
    MissingCredentialsError,
    ResearchTool,
    ToolRegistry,
)
from backend.app.services.research.tools.firecrawl import FirecrawlExtractTool
from backend.app.services.research.tools.mock import MockExtractTool, MockSearchTool
from backend.app.services.research.tools.tavily import TavilySearchTool

if TYPE_CHECKING:
    from backend.app.llm import LLMProvider
    from backend.app.services.research.orchestrator import ResearchOrchestrator
    from backend.app.services.research.planner import ResearchPlanner
    from backend.app.services.research.storage import ResearchStorageService


class ResearchToolFactory:
    """Constructs configured research tools, registries, orchestrators, and planners."""

    @classmethod
    def validate_credentials(cls, settings: Optional[Settings] = None) -> None:
        """Validate that all required external provider credentials are configured.

        Raises:
            MissingCredentialsError: If ANIM_TAVILY_API_KEY or ANIM_FIRECRAWL_API_KEY is not set.
        """
        cfg = settings or get_settings()
        missing: List[str] = []
        if not cfg.TAVILY_API_KEY or not cfg.TAVILY_API_KEY.strip():
            missing.append("ANIM_TAVILY_API_KEY")
        if not cfg.FIRECRAWL_API_KEY or not cfg.FIRECRAWL_API_KEY.strip():
            missing.append("ANIM_FIRECRAWL_API_KEY")
        if missing:
            raise MissingCredentialsError(
                f"Missing required research API credentials: {', '.join(missing)}. "
                f"Set these environment variables or configure Settings to enable live research."
            )

    @classmethod
    def create_tool_registry(
        cls,
        settings: Optional[Settings] = None,
        client: Optional[httpx.Client] = None,
        use_mocks: bool = False,
        custom_tools: Optional[List[ResearchTool]] = None,
        validate_credentials: bool = False,
    ) -> ToolRegistry:
        """Construct a ToolRegistry with real or mock tools.

        CRITICAL ARCHITECTURAL RULE:
        Production mode (`use_mocks=False`) MUST NEVER silently select MockSearchTool
        or MockExtractTool when credentials are missing. Instead, production adapters
        (TavilySearchTool, FirecrawlExtractTool) are registered, and missing credentials
        will produce explicit, actionable MissingCredentialsError exceptions upon execution
        (or immediately if validate_credentials=True).

        Mock tools are strictly for explicit test injection (`use_mocks=True`).
        """
        cfg = settings or get_settings()
        registry = ToolRegistry()

        if custom_tools:
            for tool in custom_tools:
                registry.register(tool)
            return registry

        if use_mocks:
            registry.register(MockSearchTool())
            registry.register(MockExtractTool())
            return registry

        # Production wiring: Always register real adapters
        if validate_credentials:
            cls.validate_credentials(cfg)

        tavily_tool = TavilySearchTool(settings=cfg, client=client)
        firecrawl_tool = FirecrawlExtractTool(settings=cfg, client=client)

        registry.register(tavily_tool)
        registry.register(firecrawl_tool)
        return registry

    @classmethod
    def create_orchestrator(
        cls,
        settings: Optional[Settings] = None,
        storage_service: Optional["ResearchStorageService"] = None,
        tool_registry: Optional[ToolRegistry] = None,
        client: Optional[httpx.Client] = None,
        use_mocks: bool = False,
        tool_map: Optional[Dict[str, str]] = None,
    ) -> "ResearchOrchestrator":
        """Construct a ResearchOrchestrator wired with the tool registry and storage."""
        from backend.app.services.research.orchestrator import ResearchOrchestrator
        from backend.app.services.research.storage import ResearchStorageService

        cfg = settings or get_settings()
        registry = tool_registry or cls.create_tool_registry(
            settings=cfg,
            client=client,
            use_mocks=use_mocks,
        )
        storage = storage_service or ResearchStorageService(
            projects_root=cfg.PROJECTS_DIR,
            settings=cfg,
        )
        return ResearchOrchestrator(
            tool_registry=registry,
            storage_service=storage,
            settings=cfg,
            tool_map=tool_map,
        )

    @classmethod
    def create_planner(
        cls,
        settings: Optional[Settings] = None,
        storage_service: Optional["ResearchStorageService"] = None,
        llm_provider: Optional["LLMProvider"] = None,
        max_retries: int = 2,
    ) -> "ResearchPlanner":
        """Construct a ResearchPlanner wired with the configured LLM provider and storage."""
        from backend.app.llm import get_llm_provider
        from backend.app.services.research.planner import ResearchPlanner
        from backend.app.services.research.storage import ResearchStorageService

        cfg = settings or get_settings()
        provider = llm_provider or get_llm_provider(
            name=cfg.LLM_PLAN_PROVIDER,
            settings=cfg,
        )
        storage = storage_service or ResearchStorageService(
            projects_root=cfg.PROJECTS_DIR,
            settings=cfg,
        )
        return ResearchPlanner(
            llm_provider=provider,
            storage_service=storage,
            settings=cfg,
            max_retries=max_retries,
        )
