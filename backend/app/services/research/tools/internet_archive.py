"""Internet Archive search and metadata extraction research tools with budget, SSRF, and graceful degradation."""

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
    validate_url_safety,
)

logger = logging.getLogger(__name__)

DEFAULT_ARCHIVE_USER_AGENT = "ai-animation-studio/1.0 (educational research; mailto:info@example.com)"


class InternetArchiveSearchArgs(BaseModel):
    """Input parameters for Internet Archive search."""
    query: str = Field(..., min_length=1, max_length=500, description="The search query.")
    rows: int = Field(default=5, ge=1, le=20, description="Max search results to return.")
    mediatype: Optional[str] = Field(default=None, description="Optional filter (e.g. 'texts', 'audio').")


class InternetArchiveSearchTool(ResearchTool):
    """Tool that searches the Internet Archive using the Advanced Search API."""

    name = "internet_archive_search"
    description = (
        "Search the Internet Archive for historical texts, public domain books, and archival records. "
        "Returns item titles, URLs, and descriptions."
    )
    parameters_schema = InternetArchiveSearchArgs

    API_ENDPOINT = "https://archive.org/advancedsearch.php"
    MAX_RESPONSE_BYTES = 512 * 1024  # 512 KB

    def __init__(
        self,
        settings: Optional[Settings] = None,
        client: Optional[httpx.Client] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._user_agent = user_agent or getattr(self._settings, "INTERNET_ARCHIVE_USER_AGENT", DEFAULT_ARCHIVE_USER_AGENT)

    def _run(self, args: BaseModel, session: ResearchExecutionSession) -> ToolResult:
        if not isinstance(args, InternetArchiveSearchArgs):
            raise TypeError(f"Expected InternetArchiveSearchArgs, got {type(args)}")

        # 1. Consume search budget before outbound dispatch
        session.consume_search_budget()
        effective_timeout = session.get_effective_timeout()

        query_str = args.query.strip()
        if args.mediatype:
            query_str = f"({query_str}) AND mediatype:({args.mediatype.strip()})"

        params = [
            ("q", query_str),
            ("fl[]", "identifier"),
            ("fl[]", "title"),
            ("fl[]", "description"),
            ("fl[]", "mediatype"),
            ("fl[]", "creator"),
            ("fl[]", "year"),
            ("rows", str(args.rows)),
            ("page", "1"),
            ("output", "json"),
        ]

        headers = {
            "User-Agent": self._user_agent,
            "Accept": "application/json",
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
                                f"Internet Archive search timed out: session deadline of {session.deadline_seconds:.1f}s expired."
                            ) from None
                        if (session._clock() - call_start) >= effective_timeout:
                            raise ResearchTimeoutError(
                                f"Internet Archive search timed out after {effective_timeout:.1f}s during streaming."
                            ) from None
                        accumulated.extend(chunk)
                        if len(accumulated) > self.MAX_RESPONSE_BYTES:
                            break
                    return response.status_code, bytes(accumulated)
            except httpx.TimeoutException as exc:
                raise ResearchTimeoutError(
                    f"Internet Archive search timed out after {effective_timeout:.1f}s: {exc}"
                ) from None
            except httpx.HTTPError as exc:
                raise ResearchProviderError(f"Internet Archive search request failed: {exc}") from None

        try:
            if self._client:
                status_code, body = _execute_http(self._client)
            else:
                with httpx.Client() as client:
                    status_code, body = _execute_http(client)
        except (ResearchTimeoutError, ResearchProviderError):
            raise
        except Exception as exc:
            raise ResearchProviderError(f"Unexpected error during Internet Archive search: {exc}") from exc

        if status_code == 429:
            raise ResearchProviderError("Internet Archive rate limit exceeded: HTTP 429 Too Many Requests.")
        if status_code != 200:
            raise ResearchProviderError(f"Internet Archive search returned HTTP {status_code}: {body.decode('utf-8', errors='replace')[:200]}")

        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise ResearchProviderError(f"Failed to parse Internet Archive search response as JSON: {exc}") from exc

        docs = data.get("response", {}).get("docs", [])
        results: List[SearchResultItem] = []

        for doc in docs:
            ident = doc.get("identifier")
            if not ident:
                continue
            title = doc.get("title") or ident
            url = f"https://archive.org/details/{ident}"
            
            raw_desc = doc.get("description")
            if isinstance(raw_desc, list):
                desc_text = " ".join(str(d) for d in raw_desc if d)
            elif isinstance(raw_desc, str):
                desc_text = raw_desc
            else:
                desc_text = ""

            creator = doc.get("creator")
            year = doc.get("year")
            meta_parts = []
            if creator:
                meta_parts.append(f"Creator: {creator if isinstance(creator, str) else ', '.join(creator)}")
            if year:
                meta_parts.append(f"Year: {year}")
            meta_str = " | ".join(meta_parts)

            clean_desc = re.sub(r"<[^>]+>", "", desc_text).strip()
            if clean_desc and meta_str:
                snippet = f"{meta_str}. {clean_desc}"
            else:
                snippet = clean_desc or meta_str or title

            results.append(SearchResultItem(title=title, url=url, snippet=snippet))

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


class InternetArchiveExtractArgs(BaseModel):
    """Input parameters for extracting metadata/text from an Internet Archive item."""
    url: str = Field(..., min_length=1, description="The Internet Archive URL or item identifier to extract.")


class InternetArchiveExtractTool(ResearchTool):
    """Tool that retrieves item metadata and available descriptive text from Internet Archive."""

    name = "internet_archive_extract"
    description = (
        "Extract metadata, descriptions, and available text for an Internet Archive item URL. "
        "Enforces SSRF safety, bounded character limits, and graceful handling of inaccessible full-texts."
    )
    parameters_schema = InternetArchiveExtractArgs

    METADATA_API_BASE = "https://archive.org/metadata"
    MAX_RESPONSE_BYTES = 512 * 1024

    def __init__(
        self,
        settings: Optional[Settings] = None,
        client: Optional[httpx.Client] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._user_agent = user_agent or getattr(self._settings, "INTERNET_ARCHIVE_USER_AGENT", DEFAULT_ARCHIVE_USER_AGENT)

    @staticmethod
    def _extract_identifier_from_url(url: str) -> str:
        """Extract item identifier from an archive.org URL or bare identifier string."""
        if not url or not isinstance(url, str):
            raise UnsafeURLError("Internet Archive URL must be non-empty.")
        cleaned = url.strip()
        parsed = urlsplit(cleaned)
        path = parsed.path
        if "/details/" in path:
            ident_part = path.split("/details/", 1)[1]
        else:
            ident_part = path.lstrip("/") or cleaned
        ident = unquote(ident_part).strip().split("/")[0]
        if not ident:
            raise UnsafeURLError("Could not extract valid item identifier from URL.")
        return ident

    def _run(self, args: BaseModel, session: ResearchExecutionSession) -> ToolResult:
        if not isinstance(args, InternetArchiveExtractArgs):
            raise TypeError(f"Expected InternetArchiveExtractArgs, got {type(args)}")

        # 1. SSRF Safety Check
        validate_url_safety(args.url)

        # 2. Consume extract budget before dispatch
        session.consume_extract_budget()
        effective_timeout = session.get_effective_timeout()

        identifier = self._extract_identifier_from_url(args.url)
        endpoint = f"{self.METADATA_API_BASE}/{quote(identifier)}"

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
                                f"Internet Archive extract timed out: session deadline of {session.deadline_seconds:.1f}s expired."
                            ) from None
                        if (session._clock() - call_start) >= effective_timeout:
                            raise ResearchTimeoutError(
                                f"Internet Archive extract timed out after {effective_timeout:.1f}s during streaming."
                            ) from None
                        accumulated.extend(chunk)
                        if len(accumulated) > self.MAX_RESPONSE_BYTES:
                            break
                    return response.status_code, bytes(accumulated)
            except httpx.TimeoutException as exc:
                raise ResearchTimeoutError(
                    f"Internet Archive extract timed out after {effective_timeout:.1f}s: {exc}"
                ) from None
            except httpx.HTTPError as exc:
                raise ResearchProviderError(f"Internet Archive extract request failed: {exc}") from None

        try:
            if self._client:
                status_code, body = _execute_http(self._client)
            else:
                with httpx.Client() as client:
                    status_code, body = _execute_http(client)
        except (ResearchTimeoutError, ResearchProviderError):
            raise
        except Exception as exc:
            raise ResearchProviderError(f"Unexpected error during Internet Archive extract: {exc}") from exc

        if status_code == 429:
            raise ResearchProviderError("Internet Archive rate limit exceeded: HTTP 429 Too Many Requests.")
        if status_code == 404:
            raise ResearchProviderError(f"Internet Archive item '{identifier}' not found (HTTP 404).")
        if status_code != 200:
            raise ResearchProviderError(f"Internet Archive extract returned HTTP {status_code}: {body.decode('utf-8', errors='replace')[:200]}")

        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise ResearchProviderError(f"Failed to parse Internet Archive metadata as JSON: {exc}") from exc

        # Handle empty item or missing metadata gracefully
        metadata = data.get("metadata", {})
        if not metadata:
            # Graceful degradation for inaccessible or empty item
            title = identifier
            markdown_content = f"# {title}\n\nItem record exists on Internet Archive, but detailed text metadata is unavailable or restricted."
        else:
            title = metadata.get("title") or identifier
            raw_desc = metadata.get("description", "")
            if isinstance(raw_desc, list):
                desc_text = "\n\n".join(str(d) for d in raw_desc if d)
            else:
                desc_text = str(raw_desc or "")
            clean_desc = re.sub(r"<[^>]+>", "", desc_text).strip()

            creator = metadata.get("creator")
            year = metadata.get("year") or metadata.get("date")
            mediatype = metadata.get("mediatype", "texts")
            subject = metadata.get("subject", "")
            if isinstance(subject, list):
                subject = ", ".join(str(s) for s in subject)

            meta_lines = []
            if creator:
                meta_lines.append(f"**Creator**: {creator if isinstance(creator, str) else ', '.join(creator)}")
            if year:
                meta_lines.append(f"**Date/Year**: {year}")
            if mediatype:
                meta_lines.append(f"**Media Type**: {mediatype}")
            if subject:
                meta_lines.append(f"**Subjects**: {subject}")

            meta_block = "\n".join(meta_lines)
            content_parts = [f"# {title}"]
            if meta_block:
                content_parts.append(meta_block)
            if clean_desc:
                content_parts.append(f"## Overview\n{clean_desc}")
            else:
                content_parts.append("## Overview\nFull text or description is restricted or unavailable for this archival item.")

            markdown_content = "\n\n".join(content_parts)

        bounded_content, is_truncated = session.consume_content_budget(markdown_content)
        session.record_extract(args.url, len(bounded_content), is_truncated)

        return ToolResult(
            tool_name=self.name,
            success=True,
            data=ExtractToolResult(
                url=args.url,
                title=title if isinstance(title, str) else identifier,
                content=bounded_content,
                char_count=len(bounded_content),
                truncated=is_truncated,
            ).model_dump(),
            untrusted_text=True,
        )
