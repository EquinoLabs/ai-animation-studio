"""Deterministic offline mock tools for unit tests and local development."""

from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel

from backend.app.services.research.tools.base import (
    ExtractToolResult,
    ResearchExecutionSession,
    ResearchTool,
    SearchResultItem,
    SearchToolResult,
    ToolResult,
    validate_url_safety,
)
from backend.app.services.research.tools.firecrawl import FirecrawlExtractArgs
from backend.app.services.research.tools.tavily import TavilySearchArgs


class MockSearchTool(ResearchTool):
    """Deterministic offline search tool double. Never performs external network requests."""

    parameters_schema = TavilySearchArgs

    def __init__(
        self,
        name: str = "tavily_search",
        canned_results: Optional[List[SearchResultItem]] = None,
        custom_handler: Optional[Callable[[TavilySearchArgs, ResearchExecutionSession], List[SearchResultItem]]] = None,
    ) -> None:
        self.name = name
        self.description = "Offline deterministic search tool double for testing."
        self._canned_results = canned_results
        self._custom_handler = custom_handler
        self.call_history: List[Dict[str, Any]] = []

    def _run(self, args: BaseModel, session: ResearchExecutionSession) -> ToolResult:
        if not isinstance(args, TavilySearchArgs):
            raise TypeError(f"Expected TavilySearchArgs, got {type(args)}")

        session.consume_search_budget()

        if self._custom_handler:
            items = self._custom_handler(args, session)
        elif self._canned_results is not None:
            items = self._canned_results[: args.max_results]
        else:
            # Deterministic default mock items based on query
            items = [
                SearchResultItem(
                    title=f"Historical Overview: {args.query.title()}",
                    url="https://example.org/history/overview",
                    snippet=f"Detailed factual analysis regarding {args.query}. Key events and mechanisms explained.",
                ),
                SearchResultItem(
                    title=f"Economic Origins and Principles: {args.query.title()}",
                    url="https://example.org/economics/principles",
                    snippet=f"Primary mechanisms, transition points, and historical necessity of {args.query}.",
                ),
            ][: args.max_results]

        session.record_search(query=args.query, result_count=len(items))
        self.call_history.append({"query": args.query, "count": len(items)})

        return ToolResult(
            tool_name=self.name,
            success=True,
            data=SearchToolResult(
                query=args.query,
                results=items,
                total_results=len(items),
            ).model_dump(),
            untrusted_text=True,
        )


class MockExtractTool(ResearchTool):
    """Deterministic offline extraction tool double. Enforces SSRF checks and content budgets."""

    parameters_schema = FirecrawlExtractArgs

    def __init__(
        self,
        name: str = "firecrawl_extract",
        canned_content: Optional[str] = None,
        check_ssrf: bool = True,
        custom_handler: Optional[Callable[[FirecrawlExtractArgs, ResearchExecutionSession], str]] = None,
    ) -> None:
        self.name = name
        self.description = "Offline deterministic extraction tool double for testing."
        self._canned_content = canned_content
        self._check_ssrf = check_ssrf
        self._custom_handler = custom_handler
        self.call_history: List[Dict[str, Any]] = []

    def _run(self, args: BaseModel, session: ResearchExecutionSession) -> ToolResult:
        if not isinstance(args, FirecrawlExtractArgs):
            raise TypeError(f"Expected FirecrawlExtractArgs, got {type(args)}")

        if self._check_ssrf:
            validate_url_safety(args.url)

        session.consume_extract_budget()

        if self._custom_handler:
            raw_content = self._custom_handler(args, session)
        elif self._canned_content is not None:
            raw_content = self._canned_content
        else:
            raw_content = (
                f"# Source Article from {args.url}\n\n"
                "## Historical Foundation\n"
                "Barter systems required a double coincidence of wants, which created high transaction frictions.\n"
                "Commodity monies like shells, barley, and cattle emerged as early universal mediums of exchange.\n\n"
                "## Standardization and Metal\n"
                "Electrum coins in Lydia standardized weight and purity, reducing the need for constant assaying.\n"
            )

        bounded_text, truncated = session.consume_content_budget(raw_content)

        session.record_extract(
            url=args.url,
            char_count=len(bounded_text),
            truncated=truncated,
        )
        self.call_history.append({"url": args.url, "char_count": len(bounded_text)})

        return ToolResult(
            tool_name=self.name,
            success=True,
            data=ExtractToolResult(
                url=args.url,
                title="Mock Source Page",
                content=bounded_text,
                char_count=len(bounded_text),
                truncated=truncated,
            ).model_dump(),
            untrusted_text=True,
        )
