"""Research tools package providing search and extraction adapters, security controls, and registries."""

from backend.app.services.research.tools.base import (
    ExtractToolResult,
    MissingCredentialsError,
    ResearchBudgetExceededError,
    ResearchDeadlineExceededError,
    ResearchExecutionSession,
    ResearchProviderError,
    ResearchTimeoutError,
    ResearchTool,
    ResearchToolError,
    SearchResultItem,
    SearchToolResult,
    ToolResult,
    ToolValidationError,
    ToolRegistry,
    UnknownToolError,
    UnsafeURLError,
    sanitize_secret,
    validate_url_safety,
)
from backend.app.services.research.tools.factory import ResearchToolFactory
from backend.app.services.research.tools.firecrawl import (
    FirecrawlExtractArgs,
    FirecrawlExtractTool,
)
from backend.app.services.research.tools.internet_archive import (
    InternetArchiveExtractArgs,
    InternetArchiveExtractTool,
    InternetArchiveSearchArgs,
    InternetArchiveSearchTool,
)
from backend.app.services.research.tools.mock import (
    MockExtractTool,
    MockSearchTool,
)
from backend.app.services.research.tools.openalex import (
    OpenAlexExtractArgs,
    OpenAlexExtractTool,
    OpenAlexSearchArgs,
    OpenAlexSearchTool,
    reconstruct_abstract,
)
from backend.app.services.research.tools.tavily import (
    TavilySearchArgs,
    TavilySearchTool,
)
from backend.app.services.research.tools.wikimedia import (
    WikimediaExtractArgs,
    WikimediaExtractTool,
    WikimediaSearchArgs,
    WikimediaSearchTool,
)

__all__ = [
    "ExtractToolResult",
    "FirecrawlExtractArgs",
    "FirecrawlExtractTool",
    "InternetArchiveExtractArgs",
    "InternetArchiveExtractTool",
    "InternetArchiveSearchArgs",
    "InternetArchiveSearchTool",
    "MissingCredentialsError",
    "MockExtractTool",
    "MockSearchTool",
    "OpenAlexExtractArgs",
    "OpenAlexExtractTool",
    "OpenAlexSearchArgs",
    "OpenAlexSearchTool",
    "ResearchBudgetExceededError",
    "ResearchDeadlineExceededError",
    "ResearchExecutionSession",
    "ResearchProviderError",
    "ResearchTimeoutError",
    "ResearchTool",
    "ResearchToolError",
    "ResearchToolFactory",
    "SearchResultItem",
    "SearchToolResult",
    "TavilySearchArgs",
    "TavilySearchTool",
    "ToolRegistry",
    "ToolResult",
    "ToolValidationError",
    "UnknownToolError",
    "UnsafeURLError",
    "WikimediaExtractArgs",
    "WikimediaExtractTool",
    "WikimediaSearchArgs",
    "WikimediaSearchTool",
    "reconstruct_abstract",
    "sanitize_secret",
    "validate_url_safety",
]
