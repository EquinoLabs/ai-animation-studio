"""Firecrawl extraction tool implementation with SSRF validation, bounded streaming, and secret safety safeguards."""

import json
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit
import httpx
from pydantic import BaseModel, Field

from backend.app.config import Settings, get_settings
from backend.app.services.research.tools.base import (
    ExtractToolResult,
    MissingCredentialsError,
    ResearchExecutionSession,
    ResearchProviderError,
    ResearchTimeoutError,
    ResearchTool,
    ResearchToolError,
    ToolResult,
    UnsafeURLError,
    sanitize_secret,
    validate_url_safety,
)


class FirecrawlExtractArgs(BaseModel):
    """Input parameters for Firecrawl page extraction."""
    url: str = Field(..., min_length=1, description="The HTTP or HTTPS URL to extract content from.")


class FirecrawlExtractTool(ResearchTool):
    """Tool that extracts clean markdown content from web pages using Firecrawl."""

    name = "firecrawl_extract"
    description = (
        "Extract clean, readable markdown content from a specific web page URL. "
        "Performs SSRF validation and enforces content size limits."
    )
    parameters_schema = FirecrawlExtractArgs

    DEFAULT_API_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"
    API_ENDPOINT = DEFAULT_API_ENDPOINT

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
        settings: Optional[Settings] = None,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._api_key = api_key or self._settings.FIRECRAWL_API_KEY
        self._client = client
        if api_url is not None:
            configured_url = api_url
        else:
            configured_url = getattr(self._settings, "FIRECRAWL_API_URL", None) or self.DEFAULT_API_ENDPOINT
        self._api_url = self._validate_api_url(configured_url)

    @property
    def api_url(self) -> str:
        """Return the validated Firecrawl API endpoint URL."""
        return self._api_url

    @staticmethod
    def _validate_api_url(url: str) -> str:
        if not url or not isinstance(url, str):
            raise UnsafeURLError("Firecrawl API URL must be a non-empty string.")
        stripped = url.strip()
        parsed = urlsplit(stripped)
        scheme = (parsed.scheme or "").lower()
        if scheme not in ("http", "https"):
            raise UnsafeURLError(f"Unsupported Firecrawl API URL scheme '{scheme}'. Only HTTP and HTTPS are permitted.")
        if parsed.username or parsed.password:
            raise UnsafeURLError("Firecrawl API URL must not contain embedded user credentials.")
        if not (parsed.hostname or "").strip():
            raise UnsafeURLError("Firecrawl API URL must contain a valid hostname.")
        return stripped

    def _get_api_key(self) -> str:
        key = self._api_key or self._settings.FIRECRAWL_API_KEY
        if not key or not key.strip():
            raise MissingCredentialsError(
                "Firecrawl API key is not configured. Set ANIM_FIRECRAWL_API_KEY or configure Settings."
            )
        return key.strip()

    def _run(self, args: BaseModel, session: ResearchExecutionSession) -> ToolResult:
        if not isinstance(args, FirecrawlExtractArgs):
            raise TypeError(f"Expected FirecrawlExtractArgs, got {type(args)}")

        # 1. SSRF Safety Check on target URL (validation failure before dispatch does not consume budget)
        validate_url_safety(args.url)

        # 2. Consume extract invocation budget before outbound dispatch
        session.consume_extract_budget()

        # 3. Retrieve credentials and calculate effective timeout
        api_key = self._get_api_key()
        effective_timeout = session.get_effective_timeout()

        payload = {
            "url": args.url,
            "formats": ["markdown"],
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # Bounded buffer: allow maximum page chars * 4 bytes + 32KB JSON envelope
        max_bytes = (session.max_page_chars * 4) + (32 * 1024)

        def _execute_http(client: httpx.Client) -> tuple[int, bytes]:
            call_start = session._clock()
            try:
                with client.stream(
                    "POST",
                    self._api_url,
                    json=payload,
                    headers=headers,
                    timeout=effective_timeout,
                ) as response:
                    accumulated = bytearray()
                    for chunk in response.iter_bytes(chunk_size=8192):
                        # Enforce total session deadline and per-call timeout during streaming
                        if session.remaining_seconds() <= 0:
                            raise ResearchTimeoutError(
                                f"Firecrawl extraction timed out: overall deadline of {session.deadline_seconds:.1f}s expired during response streaming."
                            ) from None
                        if (session._clock() - call_start) >= effective_timeout:
                            raise ResearchTimeoutError(
                                f"Firecrawl extraction timed out after {effective_timeout:.1f}s during response streaming."
                            ) from None

                        accumulated.extend(chunk)
                        if len(accumulated) > max_bytes:
                            break
                    return response.status_code, bytes(accumulated)
            except httpx.TimeoutException as exc:
                sanitized_msg = sanitize_secret(str(exc), [api_key])
                raise ResearchTimeoutError(
                    f"Firecrawl extraction timed out after {effective_timeout:.1f}s: {sanitized_msg}"
                ) from None
            except httpx.HTTPError as exc:
                sanitized_msg = sanitize_secret(str(exc), [api_key])
                raise ResearchProviderError(f"Firecrawl request failed: {sanitized_msg}") from None

        try:
            if self._client:
                status_code, body = _execute_http(self._client)
            else:
                with httpx.Client() as client:
                    status_code, body = _execute_http(client)

            if status_code != 200:
                raw_text = sanitize_secret(body.decode("utf-8", errors="replace"), [api_key])
                raise ResearchProviderError(
                    f"Firecrawl API returned HTTP status {status_code}: {raw_text[:200]}"
                )

            data = json.loads(body.decode("utf-8", errors="replace"))
            inner_data = data.get("data") or {}
            raw_content = inner_data.get("markdown") or inner_data.get("content") or ""
            metadata = inner_data.get("metadata") or {}
            title = metadata.get("title")

            # Bounded content consumption
            bounded_text, truncated = session.consume_content_budget(raw_content)

            extract_result = ExtractToolResult(
                url=args.url,
                title=title,
                content=bounded_text,
                char_count=len(bounded_text),
                truncated=truncated,
            )

            session.record_extract(
                url=args.url,
                char_count=len(bounded_text),
                truncated=truncated,
            )

            return ToolResult(
                tool_name=self.name,
                success=True,
                data=extract_result.model_dump(),
                untrusted_text=True,
            )

        except Exception as e:
            sanitized_err = sanitize_secret(str(e), [api_key])
            if isinstance(
                e,
                (
                    MissingCredentialsError,
                    UnsafeURLError,
                    ResearchTimeoutError,
                    ResearchProviderError,
                    ResearchToolError,
                ),
            ):
                raise
            raise ResearchProviderError(f"Firecrawl extraction failed: {sanitized_err}") from None
