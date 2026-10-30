"""Tavily search tool implementation with timeout, budget, and secret safety safeguards."""

from typing import Any, Dict, List, Literal, Optional
import httpx
from pydantic import BaseModel, Field

from backend.app.config import Settings, get_settings
from backend.app.services.research.tools.base import (
    MissingCredentialsError,
    ResearchExecutionSession,
    ResearchProviderError,
    ResearchTimeoutError,
    ResearchTool,
    ResearchToolError,
    SearchResultItem,
    SearchToolResult,
    ToolResult,
    sanitize_secret,
)


class TavilySearchArgs(BaseModel):
    """Input parameters for Tavily search."""
    query: str = Field(..., min_length=1, max_length=500, description="The search query.")
    max_results: int = Field(default=5, ge=1, le=10, description="Max search results to return.")
    search_depth: Literal["basic", "advanced"] = Field(
        default="basic", description="Search depth: basic (fast) or advanced (thorough)."
    )
    include_answer: bool = Field(default=False, description="Whether to include a generated direct answer.")


class TavilySearchTool(ResearchTool):
    """Tool that executes web search using the Tavily Search API."""

    name = "tavily_search"
    description = (
        "Search the web for up-to-date facts, background context, and credible sources. "
        "Returns titles, URLs, and text snippets."
    )
    parameters_schema = TavilySearchArgs

    API_ENDPOINT = "https://api.tavily.com/search"
    MAX_RESPONSE_BYTES = 512 * 1024  # 512 KB limit on JSON payload

    def __init__(
        self,
        api_key: Optional[str] = None,
        settings: Optional[Settings] = None,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._api_key = api_key or self._settings.TAVILY_API_KEY
        self._client = client

    def _get_api_key(self) -> str:
        key = self._api_key or self._settings.TAVILY_API_KEY
        if not key or not key.strip():
            raise MissingCredentialsError(
                "Tavily API key is not configured. Set ANIM_TAVILY_API_KEY or configure Settings."
            )
        return key.strip()

    def _run(self, args: BaseModel, session: ResearchExecutionSession) -> ToolResult:
        if not isinstance(args, TavilySearchArgs):
            raise TypeError(f"Expected TavilySearchArgs, got {type(args)}")

        # Consume search budget before outbound dispatch
        session.consume_search_budget()
        api_key = self._get_api_key()
        effective_timeout = session.get_effective_timeout()

        payload = {
            "api_key": api_key,
            "query": args.query,
            "search_depth": args.search_depth,
            "max_results": args.max_results,
            "include_answer": args.include_answer,
        }

        # If a custom client was provided, use it; otherwise create a short-lived client
        def _execute_http(client: httpx.Client) -> tuple[int, bytes]:
            call_start = session._clock()
            try:
                with client.stream(
                    "POST",
                    self.API_ENDPOINT,
                    json=payload,
                    timeout=effective_timeout,
                    headers={"Content-Type": "application/json"},
                ) as response:
                    accumulated = bytearray()
                    for chunk in response.iter_bytes(chunk_size=8192):
                        # Enforce total session deadline and per-call timeout during streaming
                        if session.remaining_seconds() <= 0:
                            raise ResearchTimeoutError(
                                f"Tavily search timed out: overall deadline of {session.deadline_seconds:.1f}s expired during response streaming."
                            ) from None
                        if (session._clock() - call_start) >= effective_timeout:
                            raise ResearchTimeoutError(
                                f"Tavily search timed out after {effective_timeout:.1f}s during response streaming."
                            ) from None

                        accumulated.extend(chunk)
                        if len(accumulated) > self.MAX_RESPONSE_BYTES:
                            break
                    return response.status_code, bytes(accumulated)
            except httpx.TimeoutException as exc:
                sanitized_msg = sanitize_secret(str(exc), [api_key])
                raise ResearchTimeoutError(
                    f"Tavily search timed out after {effective_timeout:.1f}s: {sanitized_msg}"
                ) from None
            except httpx.HTTPError as exc:
                sanitized_msg = sanitize_secret(str(exc), [api_key])
                raise ResearchProviderError(f"Tavily request failed: {sanitized_msg}") from None

        try:
            if self._client:
                status_code, body = _execute_http(self._client)
            else:
                with httpx.Client() as client:
                    status_code, body = _execute_http(client)

            if status_code != 200:
                raw_text = sanitize_secret(body.decode("utf-8", errors="replace"), [api_key])
                raise ResearchProviderError(
                    f"Tavily API returned HTTP status {status_code}: {raw_text[:200]}"
                )

            import json
            data = json.loads(body.decode("utf-8", errors="replace"))
            results_raw = data.get("results", [])

            items: List[SearchResultItem] = []
            for r in results_raw:
                items.append(
                    SearchResultItem(
                        title=str(r.get("title", "")).strip(),
                        url=str(r.get("url", "")).strip(),
                        snippet=str(r.get("content", "")).strip(),
                    )
                )

            search_result = SearchToolResult(
                query=args.query,
                results=items,
                total_results=len(items),
            )

            session.record_search(query=args.query, result_count=len(items))

            return ToolResult(
                tool_name=self.name,
                success=True,
                data=search_result.model_dump(),
                untrusted_text=True,
            )

        except Exception as e:
            sanitized_err = sanitize_secret(str(e), [api_key])
            if isinstance(
                e,
                (
                    MissingCredentialsError,
                    ResearchTimeoutError,
                    ResearchProviderError,
                    ResearchToolError,
                ),
            ):
                raise
            raise ResearchProviderError(f"Tavily search execution failed: {sanitized_err}") from None
