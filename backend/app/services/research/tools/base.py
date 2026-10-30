"""Base interfaces, registries, execution sessions, and security controls for research tools.

Enforces:
1. Strict parameter validation via Pydantic schemas.
2. Invocation budgets (search & extract ceilings).
3. Per-call timeouts and shared session deadlines.
4. Per-page and aggregate content limits with bounded streaming.
5. SSRF validation on target extraction URLs.
6. Untrusted text marking and secret redaction.
"""

from abc import ABC, abstractmethod
import ipaddress
import re
import socket
import time
from typing import Any, Callable, Dict, List, Optional, Type
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError


# ---------------------------------------------------------------------------
# Exceptions Hierarchy
# ---------------------------------------------------------------------------

class ResearchToolError(Exception):
    """Base exception for research tool operations."""
    pass


class UnknownToolError(ResearchToolError):
    """Raised when an unregistered tool is requested."""
    pass


class ToolValidationError(ResearchToolError):
    """Raised when tool arguments fail Pydantic schema validation."""
    pass


class MissingCredentialsError(ResearchToolError):
    """Raised when a required API key or credential is missing."""
    pass


class ResearchProviderError(ResearchToolError):
    """Raised when an external research provider returns an error."""
    pass


class ResearchTimeoutError(ResearchToolError):
    """Raised when a tool call exceeds its per-call timeout."""
    pass


class ResearchDeadlineExceededError(ResearchToolError):
    """Raised when the session-wide research deadline has been exceeded."""
    pass


class ResearchBudgetExceededError(ResearchToolError):
    """Raised when a tool invocation budget (search or extract) is exhausted."""
    pass


class UnsafeURLError(ResearchToolError):
    """Raised when an extraction URL fails SSRF safety validation."""
    pass


# ---------------------------------------------------------------------------
# Security: URL Safety / SSRF Validation
# ---------------------------------------------------------------------------

BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata.google.internal",
    "metadata.azure.internal",
    "169.254.169.254",
    "instance-data",
}

BLOCKED_DOMAIN_SUFFIXES = (
    ".local",
    ".internal",
    ".localhost",
    ".lan",
    ".corp",
)


def validate_url_safety(url: str) -> None:
    """Validate that a URL is safe to fetch and not attempting SSRF or local network access.

    Security checks enforced:
    - Scheme must be strictly 'http' or 'https'.
    - Rejects embedded credentials (username/password in URL).
    - Port policy: Restricts strictly to standard HTTP/HTTPS defaults (80 for http, 443 for https).
    - Hostname must be present and non-empty.
    - Blocks localhost, loopbacks, cloud metadata services, and internal domain suffixes.
    - Resolves DNS hostname and checks all resolved IP addresses:
      Rejects private RFC 1918, loopback, link-local RFC 3927, multicast, reserved, and unspecified addresses.

    Limitations:
    - Remote Scraper & Redirect Limitations: When URLs are submitted to remote crawlers
      such as Firecrawl, the crawl and any subsequent HTTP 3xx redirects occur on Firecrawl's
      remote infrastructure. Local DNS and IP validation acts as an outbound pre-filter for the
      local application, but cannot inspect or constrain remote redirects or crawler-side egress.
    - DNS rebinding: A malicious domain could return a public IP at validation time
      and a private IP at connection time. In production environments with untrusted user
      input, this application-level check should be paired with egress firewall rules
      or a hardened egress proxy.
    """
    if not url or not isinstance(url, str):
        raise UnsafeURLError("URL must be a non-empty string.")

    try:
        parsed = urlsplit(url.strip())
    except Exception as e:
        raise UnsafeURLError(f"Malformed URL: {e}") from e

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise UnsafeURLError(f"Unsupported URL scheme '{scheme}'. Only HTTP and HTTPS are permitted.")

    # Reject embedded username / password credentials
    if parsed.username or parsed.password:
        raise UnsafeURLError("URL must not contain embedded username or password credentials.")

    # Restrict to standard HTTP/HTTPS ports (80 for http, 443 for https)
    port = parsed.port
    if scheme == "http" and port not in (None, 80):
        raise UnsafeURLError(f"Nonstandard port '{port}' is prohibited for HTTP URLs (permitted: 80).")
    if scheme == "https" and port not in (None, 443):
        raise UnsafeURLError(f"Nonstandard port '{port}' is prohibited for HTTPS URLs (permitted: 443).")

    hostname = (parsed.hostname or "").lower().strip()
    if not hostname:
        raise UnsafeURLError("URL must include a valid hostname.")

    if hostname in BLOCKED_HOSTNAMES or any(hostname.endswith(sfx) for sfx in BLOCKED_DOMAIN_SUFFIXES):
        raise UnsafeURLError(f"Access to blocked host '{hostname}' is prohibited.")

    # Check if hostname is already a direct IP address literal
    try:
        ip = ipaddress.ip_address(hostname)
        _verify_ip_safety(ip)
        return
    except ValueError:
        # Not an IP literal, proceed to DNS resolution check
        pass

    # Resolve hostname via DNS and verify all returned addresses
    try:
        addr_info = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise UnsafeURLError(f"Failed to resolve hostname '{hostname}': {e}") from e
    except Exception as e:
        raise UnsafeURLError(f"DNS lookup error for '{hostname}': {e}") from e

    if not addr_info:
        raise UnsafeURLError(f"Hostname '{hostname}' did not resolve to any address.")

    resolved_ips: List[str] = []
    for item in addr_info:
        sockaddr = item[4]
        ip_str = sockaddr[0]
        if ip_str not in resolved_ips:
            resolved_ips.append(ip_str)

    for ip_str in resolved_ips:
        try:
            ip = ipaddress.ip_address(ip_str)
            _verify_ip_safety(ip)
        except ValueError as e:
            raise UnsafeURLError(f"Invalid resolved IP '{ip_str}': {e}") from e


def _verify_ip_safety(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    """Ensure an IP address is strictly a globally routable public address."""
    # Check for IPv4-mapped IPv6 addresses (e.g. ::ffff:127.0.0.1)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped

    if ip.is_loopback:
        raise UnsafeURLError(f"Target IP '{ip}' is a loopback address.")
    if ip.is_private:
        raise UnsafeURLError(f"Target IP '{ip}' is a private network address.")
    if ip.is_link_local:
        raise UnsafeURLError(f"Target IP '{ip}' is a link-local address.")
    if ip.is_multicast:
        raise UnsafeURLError(f"Target IP '{ip}' is a multicast address.")
    if ip.is_reserved:
        raise UnsafeURLError(f"Target IP '{ip}' is a reserved address.")
    if ip.is_unspecified:
        raise UnsafeURLError(f"Target IP '{ip}' is an unspecified address.")


PROVIDER_KEY_PATTERN = re.compile(r"\b(?:tvly|fc)-[a-zA-Z0-9_\-]{4,}\b")


def sanitize_secret(text: str, secrets: List[Optional[str]]) -> str:
    """Redact configured secret keys or provider key patterns from error messages, exceptions, or logs."""
    sanitized = text
    for secret in secrets:
        if secret and len(secret) >= 4 and secret in sanitized:
            sanitized = sanitized.replace(secret, "[REDACTED_API_KEY]")
    # Also redact provider-style API key patterns (e.g. tvly-*, fc-*)
    sanitized = PROVIDER_KEY_PATTERN.sub("[REDACTED_API_KEY]", sanitized)
    return sanitized


# ---------------------------------------------------------------------------
# Execution Session & Budget Tracking
# ---------------------------------------------------------------------------

class ResearchExecutionSession:
    """Tracks research tool invocations, deadline timeouts, and character ceilings."""

    def __init__(
        self,
        max_search_calls: int = 3,
        max_extract_calls: int = 3,
        call_timeout_seconds: float = 15.0,
        deadline_seconds: float = 60.0,
        max_page_chars: int = 15000,
        max_total_chars: int = 45000,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.max_search_calls = max_search_calls
        self.max_extract_calls = max_extract_calls
        self.call_timeout_seconds = call_timeout_seconds
        self.deadline_seconds = deadline_seconds
        self.max_page_chars = max_page_chars
        self.max_total_chars = max_total_chars
        self._clock = clock or time.monotonic
        self.start_time = self._clock()

        self.search_calls = 0
        self.extract_calls = 0
        self.total_chars_extracted = 0
        self.history: List[Dict[str, Any]] = []

    def elapsed_seconds(self) -> float:
        """Seconds elapsed since the session started."""
        return max(0.0, self._clock() - self.start_time)

    def remaining_seconds(self) -> float:
        """Seconds remaining before the session deadline expires."""
        return max(0.0, self.deadline_seconds - self.elapsed_seconds())

    def check_deadline(self) -> None:
        """Raise ResearchDeadlineExceededError if the session deadline has elapsed."""
        if self.elapsed_seconds() >= self.deadline_seconds:
            raise ResearchDeadlineExceededError(
                f"Research deadline of {self.deadline_seconds:.1f}s exceeded "
                f"({self.elapsed_seconds():.1f}s elapsed)."
            )

    def check_search_budget(self) -> None:
        """Check if search invocation budget is available."""
        self.check_deadline()
        if self.search_calls >= self.max_search_calls:
            raise ResearchBudgetExceededError(
                f"Search budget exhausted: {self.search_calls}/{self.max_search_calls} calls used."
            )

    def consume_search_budget(self) -> None:
        """Verify budget and consume one search invocation before outbound dispatch."""
        self.check_search_budget()
        self.search_calls += 1

    def check_extract_budget(self) -> None:
        """Check if extract invocation budget is available."""
        self.check_deadline()
        if self.extract_calls >= self.max_extract_calls:
            raise ResearchBudgetExceededError(
                f"Extract budget exhausted: {self.extract_calls}/{self.max_extract_calls} calls used."
            )

    def consume_extract_budget(self) -> None:
        """Verify budget and consume one extract invocation before outbound dispatch."""
        self.check_extract_budget()
        self.extract_calls += 1

    def get_effective_timeout(self) -> float:
        """Return the per-call timeout bounded by remaining session time."""
        remaining = self.remaining_seconds()
        if remaining <= 0:
            raise ResearchDeadlineExceededError("No time remaining in research session.")
        return min(self.call_timeout_seconds, remaining)

    def record_search(self, query: str, result_count: int) -> None:
        """Record outcome of an executed search call in session history."""
        self.history.append({
            "type": "search",
            "query": query,
            "result_count": result_count,
            "elapsed": round(self.elapsed_seconds(), 2),
        })

    def record_extract(self, url: str, char_count: int, truncated: bool) -> None:
        """Record outcome of an executed extract call in session history."""
        self.history.append({
            "type": "extract",
            "url": url,
            "char_count": char_count,
            "truncated": truncated,
            "elapsed": round(self.elapsed_seconds(), 2),
        })

    def consume_content_budget(self, text: str) -> tuple[str, bool]:
        """Bound text to both max_page_chars and remaining session max_total_chars.

        Returns (bounded_text, is_truncated).
        """
        truncated = False
        # Bound against per-page limit
        if len(text) > self.max_page_chars:
            text = text[:self.max_page_chars]
            truncated = True

        # Bound against aggregate session budget
        remaining_budget = max(0, self.max_total_chars - self.total_chars_extracted)
        if len(text) > remaining_budget:
            text = text[:remaining_budget]
            truncated = True

        self.total_chars_extracted += len(text)
        return text, truncated


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

class SearchResultItem(BaseModel):
    """Individual item returned by a search tool."""
    model_config = ConfigDict(frozen=True)

    title: str = Field(..., description="Title of the search result.")
    url: str = Field(..., description="URL of the source page.")
    snippet: str = Field(..., description="Short summary snippet or excerpt.")


class SearchToolResult(BaseModel):
    """Payload returned by search tools."""
    query: str
    results: List[SearchResultItem]
    total_results: int


class ExtractToolResult(BaseModel):
    """Payload returned by extraction tools."""
    url: str
    title: Optional[str] = None
    content: str
    char_count: int
    truncated: bool = False


class ToolResult(BaseModel):
    """Universal return envelope for research tool execution."""
    tool_name: str
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    untrusted_text: bool = True


# ---------------------------------------------------------------------------
# Abstract Tool & Registry
# ---------------------------------------------------------------------------

class ResearchTool(ABC):
    """Abstract base class for research tools."""

    name: str
    description: str
    parameters_schema: Type[BaseModel]

    def execute(self, raw_args: Dict[str, Any], session: ResearchExecutionSession) -> ToolResult:
        """Validate arguments against schema, verify session constraints, and execute."""
        session.check_deadline()

        try:
            validated_args = self.parameters_schema.model_validate(raw_args)
        except ValidationError as e:
            raise ToolValidationError(f"Invalid arguments for {self.name}: {e}") from e

        return self._run(validated_args, session)

    @abstractmethod
    def _run(self, args: BaseModel, session: ResearchExecutionSession) -> ToolResult:
        """Subclass implementation of tool logic."""
        pass


class ToolRegistry:
    """Registry managing available research tools."""

    def __init__(self) -> None:
        self._tools: Dict[str, ResearchTool] = {}

    def register(self, tool: ResearchTool) -> None:
        """Register a research tool instance."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> ResearchTool:
        """Retrieve a tool by name or raise UnknownToolError."""
        if name not in self._tools:
            raise UnknownToolError(
                f"Unknown tool '{name}'. Available tools: {list(self._tools.keys())}"
            )
        return self._tools[name]

    def list_tools(self) -> List[Dict[str, Any]]:
        """Return metadata definitions and JSON schemas for all registered tools."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters_schema.model_json_schema(),
            }
            for tool in self._tools.values()
        ]

    def execute(
        self,
        name: str,
        raw_args: Dict[str, Any],
        session: ResearchExecutionSession,
    ) -> ToolResult:
        """Look up tool by name and execute with session controls."""
        tool = self.get(name)
        return tool.execute(raw_args, session)
