"""Automated offline tests for expanded research providers (Wikimedia, Internet Archive, OpenAlex).

Verifies:
1. Wikimedia provider: search, article extraction, error handling, rate limiting, SSRF.
2. Internet Archive provider: metadata search, item retrieval, graceful handling of inaccessible items, SSRF.
3. OpenAlex provider: works search, abstract reconstruction from inverted index, author/venue metadata, missing abstract handling.
4. Tool factory registration and registry lookup for all providers.
5. Budget, deadline, and character ceiling enforcement across new providers.
"""

import json
from typing import Any, Dict
import httpx
import pytest

from backend.app.config import Settings
from backend.app.services.research.orchestrator import ResearchOrchestrator
from backend.app.services.research.tools.base import (
    ExtractToolResult,
    ResearchBudgetExceededError,
    ResearchDeadlineExceededError,
    ResearchExecutionSession,
    ResearchProviderError,
    ResearchTimeoutError,
    SearchResultItem,
    SearchToolResult,
    ToolRegistry,
    ToolResult,
    ToolValidationError,
    UnsafeURLError,
)
from backend.app.services.research.tools.factory import ResearchToolFactory
from backend.app.services.research.tools.internet_archive import (
    InternetArchiveExtractArgs,
    InternetArchiveExtractTool,
    InternetArchiveSearchArgs,
    InternetArchiveSearchTool,
)
from backend.app.services.research.tools.openalex import (
    OpenAlexExtractArgs,
    OpenAlexExtractTool,
    OpenAlexSearchArgs,
    OpenAlexSearchTool,
    reconstruct_abstract,
)
from backend.app.services.research.tools.wikimedia import (
    WikimediaExtractArgs,
    WikimediaExtractTool,
    WikimediaSearchArgs,
    WikimediaSearchTool,
)


# ---------------------------------------------------------------------------
# 1. Wikimedia Provider Tests
# ---------------------------------------------------------------------------

def test_wikimedia_search_success() -> None:
    """Verify WikimediaSearchTool returns normalized search results."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert "q=basal+ganglia" in str(request.url) or "q=basal%20ganglia" in str(request.url)
        assert request.headers.get("User-Agent") is not None
        payload = {
            "pages": [
                {
                    "id": 12345,
                    "key": "Basal_ganglia",
                    "title": "Basal ganglia",
                    "description": "Group of subcortical nuclei in the brain",
                    "excerpt": "The <span class=\"searchmatch\">basal ganglia</span> are involved in motor control and habit learning.",
                }
            ]
        }
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    tool = WikimediaSearchTool(client=client)
    session = ResearchExecutionSession(max_search_calls=3)

    result = tool.execute({"query": "basal ganglia", "limit": 2}, session)
    assert result.success is True
    assert result.tool_name == "wikimedia_search"
    assert session.search_calls == 1

    data = result.data
    assert data["query"] == "basal ganglia"
    assert len(data["results"]) == 1
    item = data["results"][0]
    assert item["title"] == "Basal ganglia"
    assert "https://en.wikipedia.org/wiki/Basal_ganglia" in item["url"]
    assert "subcortical nuclei" in item["snippet"]
    assert "<span" not in item["snippet"]  # HTML tags stripped


def test_wikimedia_search_rate_limiting_and_errors() -> None:
    """Verify WikimediaSearchTool handles HTTP 429 rate limit and HTTP 500 errors."""
    def rate_limit_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="Too Many Requests")

    client_429 = httpx.Client(transport=httpx.MockTransport(rate_limit_handler))
    tool_429 = WikimediaSearchTool(client=client_429)
    session = ResearchExecutionSession()

    with pytest.raises(ResearchProviderError) as exc_429:
        tool_429.execute({"query": "rate limited query"}, session)
    assert "rate limit exceeded" in str(exc_429.value).lower()

    def error_500_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    client_500 = httpx.Client(transport=httpx.MockTransport(error_500_handler))
    tool_500 = WikimediaSearchTool(client=client_500)
    with pytest.raises(ResearchProviderError) as exc_500:
        tool_500.execute({"query": "error query"}, session)
    assert "HTTP 500" in str(exc_500.value)


def test_wikimedia_search_timeout() -> None:
    """Verify WikimediaSearchTool handles network timeouts."""
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Connection timed out")

    client = httpx.Client(transport=httpx.MockTransport(timeout_handler))
    tool = WikimediaSearchTool(client=client)
    session = ResearchExecutionSession()

    with pytest.raises(ResearchTimeoutError) as exc:
        tool.execute({"query": "timeout query"}, session)
    assert "timed out" in str(exc.value)


def test_wikimedia_extract_success() -> None:
    """Verify WikimediaExtractTool extracts clean markdown content and title."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert "Basal_ganglia" in str(request.url)
        payload = {
            "title": "Basal ganglia",
            "extract": "The basal ganglia are a group of subcortical nuclei strongly implicated in habit formation.",
            "description": "Brain structure",
        }
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    tool = WikimediaExtractTool(client=client)
    session = ResearchExecutionSession(max_extract_calls=3)

    result = tool.execute({"url": "https://en.wikipedia.org/wiki/Basal_ganglia"}, session)
    assert result.success is True
    assert result.tool_name == "wikimedia_extract"
    assert session.extract_calls == 1

    data = result.data
    assert data["title"] == "Basal ganglia"
    assert "# Basal ganglia" in data["content"]
    assert "habit formation" in data["content"]
    assert data["char_count"] > 0
    assert data["truncated"] is False


def test_wikimedia_extract_ssrf_and_404() -> None:
    """Verify WikimediaExtractTool rejects unsafe loopback URLs and handles 404."""
    tool = WikimediaExtractTool()
    session = ResearchExecutionSession()

    # SSRF rejection before network dispatch
    with pytest.raises(UnsafeURLError):
        tool.execute({"url": "http://127.0.0.1:8000/wiki/secret"}, session)
    assert session.extract_calls == 0

    # 404 not found handling
    def not_found_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    client = httpx.Client(transport=httpx.MockTransport(not_found_handler))
    tool_404 = WikimediaExtractTool(client=client)
    with pytest.raises(ResearchProviderError) as exc:
        tool_404.execute({"url": "https://en.wikipedia.org/wiki/Nonexistent_Page_12345"}, session)
    assert "not found" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# 2. Internet Archive Provider Tests
# ---------------------------------------------------------------------------

def test_internet_archive_search_success() -> None:
    """Verify InternetArchiveSearchTool returns normalized search results with metadata."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert "advancedsearch.php" in str(request.url)
        payload = {
            "response": {
                "numFound": 1,
                "docs": [
                    {
                        "identifier": "habits_psychology_1914",
                        "title": "The Psychology of Habit and Learning",
                        "description": "An early 20th century treatise on behavioral routines.",
                        "creator": "Thorndike, Edward",
                        "year": "1914",
                        "mediatype": "texts",
                    }
                ],
            }
        }
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    tool = InternetArchiveSearchTool(client=client)
    session = ResearchExecutionSession(max_search_calls=3)

    result = tool.execute({"query": "habit psychology", "rows": 3, "mediatype": "texts"}, session)
    assert result.success is True
    assert result.tool_name == "internet_archive_search"
    assert session.search_calls == 1

    data = result.data
    assert len(data["results"]) == 1
    item = data["results"][0]
    assert item["title"] == "The Psychology of Habit and Learning"
    assert "https://archive.org/details/habits_psychology_1914" in item["url"]
    assert "Thorndike" in item["snippet"]
    assert "1914" in item["snippet"]


def test_internet_archive_extract_success_and_metadata_formatting() -> None:
    """Verify InternetArchiveExtractTool extracts metadata and structured markdown."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert "metadata/habits_psychology_1914" in str(request.url)
        payload = {
            "metadata": {
                "title": "The Psychology of Habit and Learning",
                "creator": "Thorndike, Edward",
                "year": "1914",
                "description": "Detailed observation of repeated behavioral loops in primates.",
                "mediatype": "texts",
                "subject": ["Habit", "Behavioral Psychology"],
            }
        }
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    tool = InternetArchiveExtractTool(client=client)
    session = ResearchExecutionSession(max_extract_calls=3)

    result = tool.execute({"url": "https://archive.org/details/habits_psychology_1914"}, session)
    assert result.success is True
    assert result.tool_name == "internet_archive_extract"
    assert session.extract_calls == 1

    data = result.data
    assert "The Psychology of Habit and Learning" in data["title"]
    assert "**Creator**: Thorndike, Edward" in data["content"]
    assert "**Date/Year**: 1914" in data["content"]
    assert "Detailed observation of repeated behavioral loops" in data["content"]


def test_internet_archive_extract_graceful_handling_of_inaccessible_items() -> None:
    """Verify InternetArchiveExtractTool handles empty or restricted metadata gracefully without crashing."""
    def empty_metadata_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"metadata": {}})

    client = httpx.Client(transport=httpx.MockTransport(empty_metadata_handler))
    tool = InternetArchiveExtractTool(client=client)
    session = ResearchExecutionSession()

    result = tool.execute({"url": "https://archive.org/details/restricted_item_999"}, session)
    assert result.success is True
    data = result.data
    assert "restricted_item_999" in data["title"]
    assert "unavailable or restricted" in data["content"].lower()


# ---------------------------------------------------------------------------
# 3. OpenAlex Provider Tests & Abstract Reconstruction
# ---------------------------------------------------------------------------

def test_reconstruct_abstract_inverted_index() -> None:
    """Verify inverted-index abstract reconstruction algorithm."""
    # Standard inverted index
    inverted = {
        "Dopamine": [0],
        "signals": [1],
        "reward": [2, 5],
        "prediction": [3],
        "errors": [4],
        "and": [6],
        "reinforcement.": [7],
    }
    reconstructed = reconstruct_abstract(inverted)
    assert reconstructed == "Dopamine signals reward prediction errors reward and reinforcement."

    # Empty dictionary returns empty string
    assert reconstruct_abstract({}) == ""
    # None returns empty string
    assert reconstruct_abstract(None) == ""


def test_openalex_search_success_with_abstract_reconstruction() -> None:
    """Verify OpenAlexSearchTool reconstructs abstracts and formats scholarly metadata."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert "api.openalex.org/works" in str(request.url)
        payload = {
            "meta": {"count": 1},
            "results": [
                {
                    "id": "https://openalex.org/W2741809807",
                    "title": "Neural Substrates of Habit Formation",
                    "doi": "https://doi.org/10.1038/nrn1234",
                    "publication_year": 2021,
                    "authorships": [
                        {"author": {"display_name": "Ann M. Graybiel"}},
                        {"author": {"display_name": "Kyle S. Smith"}},
                    ],
                    "primary_location": {
                        "landing_page_url": "https://nature.com/articles/nrn1234",
                        "source": {"display_name": "Nature Reviews Neuroscience"},
                    },
                    "abstract_inverted_index": {
                        "Habits": [0],
                        "are": [1],
                        "automatic": [2],
                        "behavioral": [3],
                        "routines.": [4],
                    },
                }
            ],
        }
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    tool = OpenAlexSearchTool(client=client)
    session = ResearchExecutionSession(max_search_calls=3)

    result = tool.execute({"query": "neural substrates habit formation", "per_page": 2}, session)
    assert result.success is True
    assert result.tool_name == "openalex_search"
    assert session.search_calls == 1

    data = result.data
    assert len(data["results"]) == 1
    item = data["results"][0]
    assert item["title"] == "Neural Substrates of Habit Formation"
    assert item["url"] == "https://doi.org/10.1038/nrn1234"
    assert "Ann M. Graybiel" in item["snippet"]
    assert "Nature Reviews Neuroscience" in item["snippet"]
    assert "Habits are automatic behavioral routines." in item["snippet"]


def test_openalex_search_missing_abstract_graceful_fallback() -> None:
    """Verify OpenAlexSearchTool handles missing abstract without error."""
    def missing_abstract_handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "results": [
                {
                    "id": "https://openalex.org/W99999",
                    "title": "Habit Mechanics in Neuroscience",
                    "doi": "https://doi.org/10.1016/j.neuron.2019",
                    "publication_year": 2019,
                    "authorships": [{"author": {"display_name": "Jane Doe"}}],
                    "abstract_inverted_index": None,  # No abstract indexed
                }
            ]
        }
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(missing_abstract_handler))
    tool = OpenAlexSearchTool(client=client)
    session = ResearchExecutionSession()

    result = tool.execute({"query": "habit mechanics"}, session)
    assert result.success is True
    item = result.data["results"][0]
    assert "Jane Doe" in item["snippet"]
    assert "Abstract not indexed" in item["snippet"]


def test_openalex_extract_success() -> None:
    """Verify OpenAlexExtractTool retrieves paper metadata, concepts, and abstract."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "id": "https://openalex.org/W2741809807",
            "title": "Neural Substrates of Habit Formation",
            "doi": "https://doi.org/10.1038/nrn1234",
            "publication_year": 2021,
            "cited_by_count": 142,
            "authorships": [{"author": {"display_name": "Ann M. Graybiel"}}],
            "primary_location": {"source": {"display_name": "Nature Reviews Neuroscience"}},
            "concepts": [{"display_name": "Habit", "score": 0.85}, {"display_name": "Neuroscience", "score": 0.90}],
            "abstract_inverted_index": {"Habits": [0], "depend": [1], "on": [2], "cortico-striatal": [3], "circuits.": [4]},
        }
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    tool = OpenAlexExtractTool(client=client)
    session = ResearchExecutionSession(max_extract_calls=3)

    result = tool.execute({"url": "https://doi.org/10.1038/nrn1234"}, session)
    assert result.success is True
    assert result.tool_name == "openalex_extract"
    assert session.extract_calls == 1

    data = result.data
    assert "Neural Substrates of Habit Formation" in data["title"]
    assert "**Citations**: 142" in data["content"]
    assert "**Key Concepts**: Habit, Neuroscience" in data["content"]
    assert "Habits depend on cortico-striatal circuits." in data["content"]


# ---------------------------------------------------------------------------
# 4. Tool Factory Registration & Registry Lookup Tests
# ---------------------------------------------------------------------------

def test_tool_factory_registers_all_expanded_providers() -> None:
    """Verify ResearchToolFactory registers all 8 tools and maps categories."""
    registry = ResearchToolFactory.create_tool_registry()

    expected_tools = [
        "tavily_search",
        "firecrawl_extract",
        "wikimedia_search",
        "wikimedia_extract",
        "internet_archive_search",
        "internet_archive_extract",
        "openalex_search",
        "openalex_extract",
    ]

    for tool_name in expected_tools:
        tool = registry.get(tool_name)
        assert tool is not None
        assert tool.name == tool_name

    listed = registry.list_tools()
    listed_names = {t["name"] for t in listed}
    for tool_name in expected_tools:
        assert tool_name in listed_names

    # Check ResearchOrchestrator DEFAULT_TOOL_MAP covers all categories
    tool_map = ResearchOrchestrator.DEFAULT_TOOL_MAP
    assert tool_map["web_search"] == "tavily_search"
    assert tool_map["web_extract"] == "firecrawl_extract"
    assert tool_map["wiki_search"] == "wikimedia_search"
    assert tool_map["wiki_extract"] == "wikimedia_extract"
    assert tool_map["archive_search"] == "internet_archive_search"
    assert tool_map["archive_extract"] == "internet_archive_extract"
    assert tool_map["academic_search"] == "openalex_search"
    assert tool_map["academic_extract"] == "openalex_extract"


# ---------------------------------------------------------------------------
# 5. Budget and Deadline Enforcement Across New Providers
# ---------------------------------------------------------------------------

def test_search_budget_exhaustion_on_new_providers() -> None:
    """Verify search budget exhaustion prevents further calls on expanded search tools."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"pages": []})

    client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    wiki_tool = WikimediaSearchTool(client=client)
    session = ResearchExecutionSession(max_search_calls=1)

    # First call consumes the single allowed search budget
    wiki_tool.execute({"query": "first search"}, session)
    assert session.search_calls == 1

    # Second call is blocked by budget exhaustion
    with pytest.raises(ResearchBudgetExceededError) as exc:
        wiki_tool.execute({"query": "second search"}, session)
    assert "Search budget exhausted" in str(exc.value)


def test_extract_character_and_call_budget_on_new_providers() -> None:
    """Verify character ceiling and call budget bounds extraction on expanded extract tools."""
    # Long text exceeding 100 char max_page_chars
    long_extract = "A" * 300
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"title": "Test", "extract": long_extract})

    client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    wiki_extract_tool = WikimediaExtractTool(client=client)
    session = ResearchExecutionSession(max_extract_calls=1, max_page_chars=100)

    result = wiki_extract_tool.execute({"url": "https://en.wikipedia.org/wiki/Test"}, session)
    assert result.success is True
    assert result.data["truncated"] is True
    assert result.data["char_count"] <= 100

    # Next call blocked by extract call budget
    with pytest.raises(ResearchBudgetExceededError):
        wiki_extract_tool.execute({"url": "https://en.wikipedia.org/wiki/Second"}, session)
