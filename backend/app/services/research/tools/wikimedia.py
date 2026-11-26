"""Wikimedia search and extract research tools with rate limit, timeout, and SSRF safety controls."""

import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote, urlsplit
import httpx
from pydantic import BaseModel, Field

from backend.app.config import Settings, get_settings
from backend.app.services.research.tools.base import (
    ExtractToolResult,
    ResearchExecutionSession,
    ResearchProviderError,
    ResearchTimeoutError,
    ResearchTool,
    SearchResultItem,
    SearchToolResult,
    ToolResult,
    UnsafeURLError,
    sanitize_secret,
    validate_url_safety,
)

logger = logging.getLogger(__name__)

DEFAULT_WIKIMEDIA_USER_AGENT = "ai-animation-studio/1.0 (educational research; mailto:info@example.com)"


class WikimediaSearchArgs(BaseModel):
    """Input parameters for Wikimedia page search."""
    query: str = Field(..., min_length=1, max_length=500, description="The search query.")
    limit: int = Field(default=5, ge=1, le=20, description="Max search results to return.")


class WikimediaSearchTool(ResearchTool):
    """Tool that searches Wikipedia articles using the MediaWiki Core REST Search API."""

    name = "wikimedia_search"
    description = (
        "Search Wikipedia for encyclopedic overviews, verified scientific concepts, "
        "and historical records. Returns article titles, URLs, and descriptive snippets."
    )
    parameters_schema = WikimediaSearchArgs

    API_ENDPOINT = "https://en.wikipedia.org/w/rest.php/v1/search/page"
    MAX_RESPONSE_BYTES = 512 * 1024  # 512 KB

    def __init__(
        self,
        settings: Optional[Settings] = None,
        client: Optional[httpx.Client] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._user_agent = user_agent or getattr(self._settings, "WIKIMEDIA_USER_AGENT", DEFAULT_WIKIMEDIA_USER_AGENT)

    def _run(self, args: BaseModel, session: ResearchExecutionSession) -> ToolResult:
        if not isinstance(args, WikimediaSearchArgs):
            raise TypeError(f"Expected WikimediaSearchArgs, got {type(args)}")

        # 1. Consume search budget before outbound dispatch
        session.consume_search_budget()
        effective_timeout = session.get_effective_timeout()

        headers = {
            "User-Agent": self._user_agent,
            "Accept": "application/json",
        }
        params = {
            "q": args.query,
            "limit": args.limit,
        }

        def _execute_http(client: httpx.Client) -> tuple[int, bytes]:
            call_start = session._clock()
            try:
                with client.stream(
                    "GET",
                    self.API_ENDPOINT,
                    params=params,
                    headers=headers,
                    timeout=effective_timeout,
                ) as response:
                    accumulated = bytearray()
                    for chunk in response.iter_bytes(chunk_size=8192):
                        if session.remaining_seconds() <= 0:
                            raise ResearchTimeoutError(
                                f"Wikimedia search timed out: overall deadline of {session.deadline_seconds:.1f}s expired."
                            ) from None
                        if (session._clock() - call_start) >= effective_timeout:
                            raise ResearchTimeoutError(
                                f"Wikimedia search timed out after {effective_timeout:.1f}s during streaming."
                            ) from None
                        accumulated.extend(chunk)
                        if len(accumulated) > self.MAX_RESPONSE_BYTES:
                            break
                    return response.status_code, bytes(accumulated)
            except httpx.TimeoutException as exc:
                raise ResearchTimeoutError(
                    f"Wikimedia search timed out after {effective_timeout:.1f}s: {exc}"
                ) from None
            except httpx.HTTPError as exc:
                raise ResearchProviderError(f"Wikimedia search request failed: {exc}") from None

        try:
            if self._client:
                status_code, body = _execute_http(self._client)
            else:
                with httpx.Client() as client:
                    status_code, body = _execute_http(client)
        except (ResearchTimeoutError, ResearchProviderError):
            raise
        except Exception as exc:
            raise ResearchProviderError(f"Unexpected error during Wikimedia search: {exc}") from exc

        if status_code == 429:
            raise ResearchProviderError("Wikimedia rate limit exceeded: HTTP 429 Too Many Requests.")
        if status_code != 200:
            raise ResearchProviderError(f"Wikimedia search returned HTTP {status_code}: {body.decode('utf-8', errors='replace')[:200]}")

        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise ResearchProviderError(f"Failed to parse Wikimedia search response as JSON: {exc}") from exc

        pages = data.get("pages", [])
        results: List[SearchResultItem] = []
        for p in pages:
            title = p.get("title") or p.get("key") or "Wikipedia Article"
            key = p.get("key") or title.replace(" ", "_")
            article_url = f"https://en.wikipedia.org/wiki/{quote(key)}"
            
            # Prefer description or clean excerpt snippet
            desc = p.get("description") or ""
            raw_excerpt = p.get("excerpt") or ""
            clean_excerpt = re.sub(r"<[^>]+>", "", raw_excerpt).strip()
            
            if desc and clean_excerpt:
                snippet = f"{desc}. {clean_excerpt}"
            else:
                snippet = desc or clean_excerpt or title

            results.append(SearchResultItem(title=title, url=article_url, snippet=snippet))

        session.record_search(args.query, len(results))

        return ToolResult(
            tool_name=self.name,
            success=True,
            data=SearchToolResult(
                query=args.query,
                results=results,
                total_results=len(results),
            ).model_dump(),
            untrusted_text=True,
        )


class WikimediaExtractArgs(BaseModel):
    """Input parameters for extracting content from a Wikipedia article."""
    url: str = Field(..., min_length=1, description="The Wikipedia URL or article page key to extract.")


class WikimediaExtractTool(ResearchTool):
    """Tool that extracts structured summary and article content from Wikipedia."""

    name = "wikimedia_extract"
    description = (
        "Extract clean, authoritative summary and core explanatory content from a Wikipedia article URL. "
        "Enforces SSRF safety and bounded content limits."
    )
    parameters_schema = WikimediaExtractArgs

    SUMMARY_API_BASE = "https://en.wikipedia.org/api/rest_v1/page/summary"
    MAX_RESPONSE_BYTES = 512 * 1024

    def __init__(
        self,
        settings: Optional[Settings] = None,
        client: Optional[httpx.Client] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._user_agent = user_agent or getattr(self._settings, "WIKIMEDIA_USER_AGENT", DEFAULT_WIKIMEDIA_USER_AGENT)

    @staticmethod
    def _extract_title_from_url(url: str) -> str:
        """Extract and unquote page title from a Wikipedia URL or key string."""
        if not url or not isinstance(url, str):
            raise UnsafeURLError("Wikipedia URL must be non-empty.")
        cleaned = url.strip()
        parsed = urlsplit(cleaned)
        path = parsed.path
        if "/wiki/" in path:
            title_part = path.split("/wiki/", 1)[1]
        else:
            title_part = path.lstrip("/") or cleaned
        unquoted = unquote(title_part).strip()
        if not unquoted:
            raise UnsafeURLError("Could not extract valid article title from URL.")
        return unquoted

    def _run(self, args: BaseModel, session: ResearchExecutionSession) -> ToolResult:
        if not isinstance(args, WikimediaExtractArgs):
            raise TypeError(f"Expected WikimediaExtractArgs, got {type(args)}")

        # 1. SSRF Safety Check
        validate_url_safety(args.url)

        # 2. Consume extract budget before dispatch
        session.consume_extract_budget()
        effective_timeout = session.get_effective_timeout()

        page_title = self._extract_title_from_url(args.url)
        endpoint = f"{self.SUMMARY_API_BASE}/{quote(page_title)}"

        headers = {
            "User-Agent": self._user_agent,
            "Accept": "application/json",
        }

        def _execute_http(client: httpx.Client) -> tuple[int, bytes]:
            call_start = session._clock()
            try:
                with client.stream(
                    "GET",
                    endpoint,
                    headers=headers,
                    timeout=effective_timeout,
                ) as response:
                    accumulated = bytearray()
                    for chunk in response.iter_bytes(chunk_size=8192):
                        if session.remaining_seconds() <= 0:
                            raise ResearchTimeoutError(
                                f"Wikimedia extract timed out: session deadline of {session.deadline_seconds:.1f}s expired."
                            ) from None
                        if (session._clock() - call_start) >= effective_timeout:
                            raise ResearchTimeoutError(
                                f"Wikimedia extract timed out after {effective_timeout:.1f}s during streaming."
                            ) from None
                        accumulated.extend(chunk)
                        if len(accumulated) > self.MAX_RESPONSE_BYTES:
                            break
                    return response.status_code, bytes(accumulated)
            except httpx.TimeoutException as exc:
                raise ResearchTimeoutError(
                    f"Wikimedia extract timed out after {effective_timeout:.1f}s: {exc}"
                ) from None
            except httpx.HTTPError as exc:
                raise ResearchProviderError(f"Wikimedia extract request failed: {exc}") from None

        try:
            if self._client:
                status_code, body = _execute_http(self._client)
            else:
                with httpx.Client() as client:
                    status_code, body = _execute_http(client)
        except (ResearchTimeoutError, ResearchProviderError):
            raise
        except Exception as exc:
            raise ResearchProviderError(f"Unexpected error during Wikimedia extract: {exc}") from exc

        if status_code == 429:
            raise ResearchProviderError("Wikimedia rate limit exceeded: HTTP 429 Too Many Requests.")
        if status_code == 404:
            raise ResearchProviderError(f"Wikipedia page '{page_title}' not found (HTTP 404).")
        if status_code != 200:
            raise ResearchProviderError(f"Wikimedia extract returned HTTP {status_code}: {body.decode('utf-8', errors='replace')[:200]}")

        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise ResearchProviderError(f"Failed to parse Wikimedia extract response as JSON: {exc}") from exc

        title = data.get("title") or page_title
        extract_text = data.get("extract") or data.get("description") or ""

        # Format into clean readable markdown
        markdown_content = f"# {title}\n\n{extract_text.strip()}"

        bounded_content, is_truncated = session.consume_content_budget(markdown_content)
        session.record_extract(args.url, len(bounded_content), is_truncated)

        return ToolResult(
            tool_name=self.name,
            success=True,
            data=ExtractToolResult(
                url=args.url,
                title=title,
                content=bounded_content,
                char_count=len(bounded_content),
                truncated=is_truncated,
            ).model_dump(),
            untrusted_text=True,
        )
