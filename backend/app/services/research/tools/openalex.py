"""OpenAlex scholarly research tools with inverted-index abstract reconstruction, SSRF, and budget controls."""

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

DEFAULT_OPENALEX_USER_AGENT = "ai-animation-studio/1.0 (educational research; mailto:info@example.com)"


def reconstruct_abstract(inverted_index: Optional[Dict[str, List[int]]]) -> str:
    """Reconstruct plain text abstract from OpenAlex's abstract_inverted_index dictionary.

    OpenAlex represents abstracts as a map of {word: [position_indices]}.
    This reconstructs the continuous text in correct token order.
    Returns empty string if inverted_index is missing or empty.
    """
    if not inverted_index or not isinstance(inverted_index, dict):
        return ""

    pos_word: List[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        if not isinstance(positions, (list, tuple)):
            continue
        for pos in positions:
            if isinstance(pos, int):
                pos_word.append((pos, str(word)))

    if not pos_word:
        return ""

    pos_word.sort(key=lambda x: x[0])
    return " ".join(word for _, word in pos_word)


class OpenAlexSearchArgs(BaseModel):
    """Input parameters for OpenAlex scholarly works search."""
    query: str = Field(..., min_length=1, max_length=500, description="The research or scholarly search query.")
    per_page: int = Field(default=5, ge=1, le=25, description="Max scholarly works to return.")


class OpenAlexSearchTool(ResearchTool):
    """Tool that searches OpenAlex for academic papers, journal articles, and peer-reviewed works."""

    name = "openalex_search"
    description = (
        "Search OpenAlex for peer-reviewed research papers, journal articles, and scholarly abstracts. "
        "Reconstructs abstracts from inverted indices and returns paper titles, DOIs, authors, and summaries."
    )
    parameters_schema = OpenAlexSearchArgs

    API_ENDPOINT = "https://api.openalex.org/works"
    MAX_RESPONSE_BYTES = 1024 * 1024  # 1 MB

    def __init__(
        self,
        settings: Optional[Settings] = None,
        client: Optional[httpx.Client] = None,
        user_agent: Optional[str] = None,
        email: Optional[str] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._user_agent = user_agent or getattr(self._settings, "OPENALEX_USER_AGENT", DEFAULT_OPENALEX_USER_AGENT)
        self._email = email or getattr(self._settings, "OPENALEX_EMAIL", None)

    def _run(self, args: BaseModel, session: ResearchExecutionSession) -> ToolResult:
        if not isinstance(args, OpenAlexSearchArgs):
            raise TypeError(f"Expected OpenAlexSearchArgs, got {type(args)}")

        # 1. Consume search budget before dispatch
        session.consume_search_budget()
        effective_timeout = session.get_effective_timeout()

        headers = {
            "User-Agent": self._user_agent,
            "Accept": "application/json",
        }
        params: Dict[str, Any] = {
            "search": args.query,
            "per-page": args.per_page,
        }
        if self._email and self._email.strip():
            params["mailto"] = self._email.strip()

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
                                f"OpenAlex search timed out: session deadline of {session.deadline_seconds:.1f}s expired."
                            ) from None
                        if (session._clock() - call_start) >= effective_timeout:
                            raise ResearchTimeoutError(
                                f"OpenAlex search timed out after {effective_timeout:.1f}s during streaming."
                            ) from None
                        accumulated.extend(chunk)
                        if len(accumulated) > self.MAX_RESPONSE_BYTES:
                            break
                    return response.status_code, bytes(accumulated)
            except httpx.TimeoutException as exc:
                raise ResearchTimeoutError(
                    f"OpenAlex search timed out after {effective_timeout:.1f}s: {exc}"
                ) from None
            except httpx.HTTPError as exc:
                raise ResearchProviderError(f"OpenAlex search request failed: {exc}") from None

        try:
            if self._client:
                status_code, body = _execute_http(self._client)
            else:
                with httpx.Client() as client:
                    status_code, body = _execute_http(client)
        except (ResearchTimeoutError, ResearchProviderError):
            raise
        except Exception as exc:
            raise ResearchProviderError(f"Unexpected error during OpenAlex search: {exc}") from exc

        if status_code == 429:
            raise ResearchProviderError("OpenAlex rate limit exceeded: HTTP 429 Too Many Requests.")
        if status_code != 200:
            raise ResearchProviderError(f"OpenAlex search returned HTTP {status_code}: {body.decode('utf-8', errors='replace')[:200]}")

        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise ResearchProviderError(f"Failed to parse OpenAlex search response as JSON: {exc}") from exc

        works = data.get("results", [])
        results: List[SearchResultItem] = []

        for work in works:
            title = work.get("title") or "Scholarly Article"
            doi = work.get("doi")
            openalex_id = work.get("id") or ""
            primary_loc = work.get("primary_location") or {}
            landing_url = primary_loc.get("landing_page_url")

            item_url = doi or landing_url or openalex_id
            if not item_url or not item_url.startswith(("http://", "https://")):
                item_url = f"https://openalex.org/{openalex_id.split('/')[-1] if openalex_id else 'works'}"

            # Reconstruct abstract or compile rich scholarly metadata
            reconstructed = reconstruct_abstract(work.get("abstract_inverted_index"))

            authorships = work.get("authorships") or []
            authors = [
                a.get("author", {}).get("display_name")
                for a in authorships
                if a.get("author", {}).get("display_name")
            ]
            year = work.get("publication_year")
            venue = primary_loc.get("source", {}).get("display_name") if primary_loc.get("source") else None

            meta_parts = []
            if authors:
                meta_parts.append(f"Authors: {', '.join(authors[:3])}{' et al.' if len(authors) > 3 else ''}")
            if year:
                meta_parts.append(f"Year: {year}")
            if venue:
                meta_parts.append(f"Venue: {venue}")
            meta_str = " | ".join(meta_parts)

            if reconstructed:
                snippet = f"{meta_str}. Abstract: {reconstructed}" if meta_str else reconstructed
            else:
                snippet = f"{meta_str}. (Abstract not indexed in OpenAlex record)." if meta_str else title

            results.append(SearchResultItem(title=title, url=item_url, snippet=snippet))

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


class OpenAlexExtractArgs(BaseModel):
    """Input parameters for retrieving scholarly work details from OpenAlex."""
    url: str = Field(..., min_length=1, description="OpenAlex work URL, work ID (W...), or DOI to retrieve.")


class OpenAlexExtractTool(ResearchTool):
    """Tool that retrieves structured paper metadata, citations, and reconstructed abstract from OpenAlex."""

    name = "openalex_extract"
    description = (
        "Retrieve peer-reviewed paper details, full reconstructed abstract, and author metadata from OpenAlex. "
        "Enforces SSRF validation and graceful handling of missing abstracts."
    )
    parameters_schema = OpenAlexExtractArgs

    WORKS_API_BASE = "https://api.openalex.org/works"
    MAX_RESPONSE_BYTES = 1024 * 1024

    def __init__(
        self,
        settings: Optional[Settings] = None,
        client: Optional[httpx.Client] = None,
        user_agent: Optional[str] = None,
        email: Optional[str] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._user_agent = user_agent or getattr(self._settings, "OPENALEX_USER_AGENT", DEFAULT_OPENALEX_USER_AGENT)
        self._email = email or getattr(self._settings, "OPENALEX_EMAIL", None)

    @staticmethod
    def _resolve_work_identifier_or_url(target: str) -> tuple[str, bool]:
        """Determine endpoint URL for OpenAlex work lookup.

        Returns (lookup_url, is_doi_lookup).
        """
        if not target or not isinstance(target, str):
            raise UnsafeURLError("Target identifier or URL must be non-empty.")
        cleaned = target.strip()

        # If it's a DOI (e.g. https://doi.org/10.1016/... or 10.1016/...)
        doi_match = re.search(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", cleaned)
        if doi_match:
            doi = doi_match.group(0)
            return f"https://api.openalex.org/works/https://doi.org/{doi}", True

        # If it's an OpenAlex ID or URL (e.g. W2741809807 or https://openalex.org/W2741809807)
        w_match = re.search(r"\bW\d{6,12}\b", cleaned, re.IGNORECASE)
        if w_match:
            work_id = w_match.group(0).upper()
            return f"https://api.openalex.org/works/{work_id}", False

        # Fallback to direct URL or search by URL
        return f"https://api.openalex.org/works/{quote(cleaned, safe='')}", False

    def _run(self, args: BaseModel, session: ResearchExecutionSession) -> ToolResult:
        if not isinstance(args, OpenAlexExtractArgs):
            raise TypeError(f"Expected OpenAlexExtractArgs, got {type(args)}")

        # 1. SSRF Safety Check if URL provided
        if args.url.startswith(("http://", "https://")):
            validate_url_safety(args.url)

        # 2. Consume extract budget before dispatch
        session.consume_extract_budget()
        effective_timeout = session.get_effective_timeout()

        endpoint, is_doi = self._resolve_work_identifier_or_url(args.url)

        headers = {
            "User-Agent": self._user_agent,
            "Accept": "application/json",
        }
        params: Dict[str, Any] = {}
        if self._email and self._email.strip():
            params["mailto"] = self._email.strip()

        def _execute_http(client: httpx.Client) -> tuple[int, bytes]:
            call_start = session._clock()
            try:
                with client.stream(
                    "GET",
                    endpoint,
                    params=params,
                    headers=headers,
                    timeout=effective_timeout,
                ) as response:
                    accumulated = bytearray()
                    for chunk in response.iter_bytes(chunk_size=8192):
                        if session.remaining_seconds() <= 0:
                            raise ResearchTimeoutError(
                                f"OpenAlex extract timed out: session deadline of {session.deadline_seconds:.1f}s expired."
                            ) from None
                        if (session._clock() - call_start) >= effective_timeout:
                            raise ResearchTimeoutError(
                                f"OpenAlex extract timed out after {effective_timeout:.1f}s during streaming."
                            ) from None
                        accumulated.extend(chunk)
                        if len(accumulated) > self.MAX_RESPONSE_BYTES:
                            break
                    return response.status_code, bytes(accumulated)
            except httpx.TimeoutException as exc:
                raise ResearchTimeoutError(
                    f"OpenAlex extract timed out after {effective_timeout:.1f}s: {exc}"
                ) from None
            except httpx.HTTPError as exc:
                raise ResearchProviderError(f"OpenAlex extract request failed: {exc}") from None

        try:
            if self._client:
                status_code, body = _execute_http(self._client)
            else:
                with httpx.Client() as client:
                    status_code, body = _execute_http(client)
        except (ResearchTimeoutError, ResearchProviderError):
            raise
        except Exception as exc:
            raise ResearchProviderError(f"Unexpected error during OpenAlex extract: {exc}") from exc

        if status_code == 429:
            raise ResearchProviderError("OpenAlex rate limit exceeded: HTTP 429 Too Many Requests.")
        if status_code == 404:
            raise ResearchProviderError(f"OpenAlex work '{args.url}' not found (HTTP 404).")
        if status_code != 200:
            raise ResearchProviderError(f"OpenAlex extract returned HTTP {status_code}: {body.decode('utf-8', errors='replace')[:200]}")

        try:
            work = json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise ResearchProviderError(f"Failed to parse OpenAlex work as JSON: {exc}") from exc

        title = work.get("title") or "Scholarly Paper"
        doi = work.get("doi")
        year = work.get("publication_year")
        citation_count = work.get("cited_by_count", 0)

        authorships = work.get("authorships") or []
        authors = [
            a.get("author", {}).get("display_name")
            for a in authorships
            if a.get("author", {}).get("display_name")
        ]

        primary_loc = work.get("primary_location") or {}
        venue = primary_loc.get("source", {}).get("display_name") if primary_loc.get("source") else None

        reconstructed_abstract = reconstruct_abstract(work.get("abstract_inverted_index"))

        concepts = [
            c.get("display_name") for c in (work.get("concepts") or [])
            if c.get("display_name") and c.get("score", 0) >= 0.4
        ]

        meta_lines = []
        if authors:
            meta_lines.append(f"**Authors**: {', '.join(authors[:5])}{' et al.' if len(authors) > 5 else ''}")
        if year:
            meta_lines.append(f"**Year**: {year}")
        if venue:
            meta_lines.append(f"**Journal/Venue**: {venue}")
        if doi:
            meta_lines.append(f"**DOI**: {doi}")
        if citation_count:
            meta_lines.append(f"**Citations**: {citation_count}")
        if concepts:
            meta_lines.append(f"**Key Concepts**: {', '.join(concepts[:5])}")

        meta_block = "\n".join(meta_lines)
        parts = [f"# {title}"]
        if meta_block:
            parts.append(meta_block)
        if reconstructed_abstract:
            parts.append(f"## Abstract\n{reconstructed_abstract}")
        else:
            parts.append("## Abstract\nAbstract text is not available or restricted for this scholarly work.")

        markdown_content = "\n\n".join(parts)

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
