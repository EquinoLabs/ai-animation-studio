"""Automated offline tests for research tools, registries, policies, and safeguards."""

import json
import socket
from typing import Any, Dict
import httpx
import pytest

from backend.app.config import Settings
from backend.app.services.research.tools.base import (
    ExtractToolResult,
    MissingCredentialsError,
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
    UnknownToolError,
    UnsafeURLError,
    sanitize_secret,
    validate_url_safety,
)
from backend.app.services.research.tools.firecrawl import (
    FirecrawlExtractArgs,
    FirecrawlExtractTool,
)
from backend.app.services.research.tools.mock import (
    MockExtractTool,
    MockSearchTool,
)
from backend.app.services.research.tools.tavily import (
    TavilySearchArgs,
    TavilySearchTool,
)


# ---------------------------------------------------------------------------
# 1. Tool Registry Tests
# ---------------------------------------------------------------------------

def test_registry_registration_and_lookup() -> None:
    registry = ToolRegistry()
    search_tool = MockSearchTool(name="tavily_search")
    extract_tool = MockExtractTool(name="firecrawl_extract")

    registry.register(search_tool)
    registry.register(extract_tool)

    assert registry.get("tavily_search") is search_tool
    assert registry.get("firecrawl_extract") is extract_tool

    tools_meta = registry.list_tools()
    assert len(tools_meta) == 2
    names = {t["name"] for t in tools_meta}
    assert names == {"tavily_search", "firecrawl_extract"}
    for meta in tools_meta:
        assert "parameters" in meta
        assert "properties" in meta["parameters"]


def test_registry_unknown_tool_raises() -> None:
    registry = ToolRegistry()
    with pytest.raises(UnknownToolError) as exc_info:
        registry.get("nonexistent_tool")
    assert "Unknown tool 'nonexistent_tool'" in str(exc_info.value)


def test_registry_execute() -> None:
    registry = ToolRegistry()
    registry.register(MockSearchTool(name="tavily_search"))
    session = ResearchExecutionSession()

    res = registry.execute(
        "tavily_search",
        {"query": "ancient currency", "max_results": 2},
        session,
    )
    assert isinstance(res, ToolResult)
    assert res.success is True
    assert res.tool_name == "tavily_search"
    assert res.untrusted_text is True
    assert "results" in res.data


# ---------------------------------------------------------------------------
# 2. Argument Validation Tests
# ---------------------------------------------------------------------------

def test_tool_argument_validation_failures() -> None:
    search_tool = MockSearchTool()
    session = ResearchExecutionSession()

    # Missing required query
    with pytest.raises(ToolValidationError):
        search_tool.execute({}, session)

    # Empty query min_length violation
    with pytest.raises(ToolValidationError):
        search_tool.execute({"query": ""}, session)

    # Out of range max_results (> 10)
    with pytest.raises(ToolValidationError):
        search_tool.execute({"query": "valid", "max_results": 99}, session)


def test_extract_tool_argument_validation() -> None:
    extract_tool = MockExtractTool()
    session = ResearchExecutionSession()

    # Missing url
    with pytest.raises(ToolValidationError):
        extract_tool.execute({}, session)

    # Empty url
    with pytest.raises(ToolValidationError):
        extract_tool.execute({"url": ""}, session)


# ---------------------------------------------------------------------------
# 3. Invocation Budget Enforcement Tests
# ---------------------------------------------------------------------------

def test_search_budget_enforcement() -> None:
    session = ResearchExecutionSession(max_search_calls=2)
    search_tool = MockSearchTool()

    search_tool.execute({"query": "q1"}, session)
    search_tool.execute({"query": "q2"}, session)
    assert session.search_calls == 2

    # 3rd call must exceed budget
    with pytest.raises(ResearchBudgetExceededError) as exc_info:
        search_tool.execute({"query": "q3"}, session)
    assert "Search budget exhausted: 2/2" in str(exc_info.value)


def test_extract_budget_enforcement() -> None:
    session = ResearchExecutionSession(max_extract_calls=2)
    extract_tool = MockExtractTool(check_ssrf=False)

    extract_tool.execute({"url": "https://example.org/1"}, session)
    extract_tool.execute({"url": "https://example.org/2"}, session)
    assert session.extract_calls == 2

    # 3rd call must exceed budget
    with pytest.raises(ResearchBudgetExceededError) as exc_info:
        extract_tool.execute({"url": "https://example.org/3"}, session)
    assert "Extract budget exhausted: 2/2" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 4. Timeout and Deadline Enforcement Tests
# ---------------------------------------------------------------------------

def test_session_deadline_enforcement() -> None:
    fake_time = 100.0

    def mock_clock() -> float:
        return fake_time

    session = ResearchExecutionSession(deadline_seconds=30.0, clock=mock_clock)
    search_tool = MockSearchTool()

    # Time within deadline
    fake_time = 110.0
    res = search_tool.execute({"query": "test query"}, session)
    assert res.success is True

    # Advance clock past deadline
    fake_time = 131.0
    with pytest.raises(ResearchDeadlineExceededError) as exc_info:
        search_tool.execute({"query": "test query"}, session)
    assert "deadline of 30.0s exceeded" in str(exc_info.value)


def test_effective_timeout_calculation() -> None:
    fake_time = 1000.0

    def mock_clock() -> float:
        return fake_time

    session = ResearchExecutionSession(
        call_timeout_seconds=15.0,
        deadline_seconds=20.0,
        clock=mock_clock,
    )
    # Initially 20s remaining, call timeout 15s -> returns 15s
    assert session.get_effective_timeout() == 15.0

    # Advance 10s: 10s remaining, call timeout 15s -> returns 10s
    fake_time = 1010.0
    assert session.get_effective_timeout() == 10.0


# ---------------------------------------------------------------------------
# 5. Content Size Limits and Bounded Streaming Tests
# ---------------------------------------------------------------------------

def test_per_page_content_limit() -> None:
    session = ResearchExecutionSession(max_page_chars=100)
    long_content = "A" * 500
    extract_tool = MockExtractTool(canned_content=long_content, check_ssrf=False)

    res = extract_tool.execute({"url": "https://example.org/long"}, session)
    assert res.success is True
    data = res.data
    assert data["char_count"] == 100
    assert len(data["content"]) == 100
    assert data["truncated"] is True


def test_aggregate_content_budget_limit() -> None:
    session = ResearchExecutionSession(max_page_chars=500, max_total_chars=800)
    extract_tool = MockExtractTool(canned_content="B" * 500, check_ssrf=False)

    # Page 1: consumes 500 chars
    res1 = extract_tool.execute({"url": "https://example.org/p1"}, session)
    assert res1.data["char_count"] == 500
    assert res1.data["truncated"] is False
    assert session.total_chars_extracted == 500

    # Page 2: only 300 chars remaining in aggregate budget
    res2 = extract_tool.execute({"url": "https://example.org/p2"}, session)
    assert res2.data["char_count"] == 300
    assert res2.data["truncated"] is True
    assert session.total_chars_extracted == 800


# ---------------------------------------------------------------------------
# 6. SSRF and URL Safety Tests
# ---------------------------------------------------------------------------

def test_validate_url_safety_accepts_valid_public_urls() -> None:
    # Safe public domains on standard HTTP/HTTPS ports
    validate_url_safety("https://example.com/page")
    validate_url_safety("http://example.org:80/index.html")
    validate_url_safety("https://example.org:443/article")


def test_validate_url_safety_rejects_embedded_credentials() -> None:
    credential_urls = [
        "http://user:pass@example.com/",
        "https://admin:secret123@example.org/resource",
        "http://user@example.com/path",
    ]
    for url in credential_urls:
        with pytest.raises(UnsafeURLError) as exc_info:
            validate_url_safety(url)
        assert "embedded username or password" in str(exc_info.value)


def test_validate_url_safety_rejects_nonstandard_ports() -> None:
    nonstandard_urls = [
        "http://example.com:22/",
        "http://example.com:8080/api",
        "https://example.com:8443/data",
        "http://example.com:443/wrong-scheme-port",
        "https://example.com:80/wrong-scheme-port",
    ]
    for url in nonstandard_urls:
        with pytest.raises(UnsafeURLError) as exc_info:
            validate_url_safety(url)
        assert "Nonstandard port" in str(exc_info.value)


def test_validate_url_safety_rejects_disallowed_schemes() -> None:
    disallowed = [
        "ftp://example.com/file",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/plain;base64,SGVsbG8=",
        "gopher://example.com",
    ]
    for url in disallowed:
        with pytest.raises(UnsafeURLError) as exc_info:
            validate_url_safety(url)
        assert "Unsupported URL scheme" in str(exc_info.value) or "Malformed" in str(exc_info.value)


def test_validate_url_safety_rejects_localhost_and_private_ips() -> None:
    blocked_urls = [
        "http://localhost:8000/api",
        "http://127.0.0.1/admin",
        "http://127.0.0.1:8080",
        "http://192.168.1.1/router",
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://test.localhost/",
        "http://internal-service.local/",
        "http://[::1]/status",
        "http://[::ffff:127.0.0.1]/",
    ]
    for url in blocked_urls:
        with pytest.raises(UnsafeURLError):
            validate_url_safety(url)


# ---------------------------------------------------------------------------
# 7. Secret Protection and Leakage Tests
# ---------------------------------------------------------------------------

def test_sanitize_secret() -> None:
    secret = "tvly-secretkey12345"
    text = f"Connection failed to https://api.tavily.com?key={secret} with error"
    sanitized = sanitize_secret(text, [secret])
    assert secret not in sanitized
    assert "[REDACTED_API_KEY]" in sanitized


def test_live_adapters_do_not_leak_secrets_in_errors() -> None:
    import traceback

    secret_key = "tvly-super-secret-key-999"

    # Mock an upstream transport that responds with 500 and echoes headers/body
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=f"Internal Error with key {secret_key}")

    transport = httpx.MockTransport(mock_handler)
    client = httpx.Client(transport=transport)

    tool = TavilySearchTool(api_key=secret_key, client=client)
    session = ResearchExecutionSession()

    with pytest.raises(ResearchProviderError) as exc_info:
        tool.execute({"query": "currency history"}, session)

    msg = str(exc_info.value)
    assert secret_key not in msg
    assert "[REDACTED_API_KEY]" in msg

    formatted_tb = "".join(traceback.format_exception(exc_info.value))
    assert secret_key not in formatted_tb


def test_underlying_httpx_exception_does_not_leak_secret_in_traceback() -> None:
    import traceback

    secret_key = "tvly-secret-network-key-456"

    def mock_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"TLS failure sending key {secret_key}")

    transport = httpx.MockTransport(mock_handler)
    client = httpx.Client(transport=transport)

    tool = TavilySearchTool(api_key=secret_key, client=client)
    session = ResearchExecutionSession()

    with pytest.raises(ResearchProviderError) as exc_info:
        tool.execute({"query": "currency history"}, session)

    msg = str(exc_info.value)
    assert secret_key not in msg
    assert "[REDACTED_API_KEY]" in msg

    # Verify cause is suppressed and formatted traceback does not expose key
    assert exc_info.value.__cause__ is None
    formatted_tb = "".join(traceback.format_exception(exc_info.value))
    assert secret_key not in formatted_tb


def test_firecrawl_does_not_leak_secrets_in_errors() -> None:
    import traceback

    secret_key = "fc-private-key-777"

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=f"Firecrawl failed for {secret_key}")

    transport = httpx.MockTransport(mock_handler)
    client = httpx.Client(transport=transport)

    tool = FirecrawlExtractTool(api_key=secret_key, client=client)
    session = ResearchExecutionSession()

    with pytest.raises(ResearchProviderError) as exc_info:
        tool.execute({"url": "https://example.org/article"}, session)

    msg = str(exc_info.value)
    assert secret_key not in msg
    assert "[REDACTED_API_KEY]" in msg

    assert exc_info.value.__cause__ is None
    formatted_tb = "".join(traceback.format_exception(exc_info.value))
    assert secret_key not in formatted_tb


def test_firecrawl_underlying_exception_does_not_leak_secret_in_traceback() -> None:
    import traceback

    secret_key = "fc-secret-network-key-888"

    def mock_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"Scrape connection failed with {secret_key}")

    transport = httpx.MockTransport(mock_handler)
    client = httpx.Client(transport=transport)

    tool = FirecrawlExtractTool(api_key=secret_key, client=client)
    session = ResearchExecutionSession()

    with pytest.raises(ResearchProviderError) as exc_info:
        tool.execute({"url": "https://example.org/article"}, session)

    msg = str(exc_info.value)
    assert secret_key not in msg
    assert "[REDACTED_API_KEY]" in msg

    assert exc_info.value.__cause__ is None
    formatted_tb = "".join(traceback.format_exception(exc_info.value))
    assert secret_key not in formatted_tb


# ---------------------------------------------------------------------------
# 8. Missing Credentials Verification
# ---------------------------------------------------------------------------

def test_missing_credentials_raises_cleanly() -> None:
    settings = Settings(
        TAVILY_API_KEY=None,
        FIRECRAWL_API_KEY=None,
    )
    tavily = TavilySearchTool(api_key=None, settings=settings)
    firecrawl = FirecrawlExtractTool(api_key=None, settings=settings)
    session = ResearchExecutionSession()

    with pytest.raises(MissingCredentialsError) as exc1:
        tavily.execute({"query": "test"}, session)
    assert "Tavily API key is not configured" in str(exc1.value)

    with pytest.raises(MissingCredentialsError) as exc2:
        firecrawl.execute({"url": "https://example.org/article"}, session)
    assert "Firecrawl API key is not configured" in str(exc2.value)


# ---------------------------------------------------------------------------
# 9. Offline Adapter Flow with Mock Transport
# ---------------------------------------------------------------------------

def test_tavily_adapter_offline_mock_transport() -> None:
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        payload = json.loads(request.content)
        assert payload["query"] == "invention of paper money"
        return httpx.Response(
            200,
            json={
                "query": payload["query"],
                "results": [
                    {
                        "title": "Song Dynasty Jiaozi",
                        "url": "https://example.org/jiaozi",
                        "content": "Jiaozi was a form of banknote which appeared around the 10th century.",
                        "score": 0.98,
                    }
                ],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    tool = TavilySearchTool(api_key="tvly-mock-key", client=client)
    session = ResearchExecutionSession()

    result = tool.execute({"query": "invention of paper money"}, session)
    assert result.success is True
    assert result.untrusted_text is True
    data = result.data
    assert data["query"] == "invention of paper money"
    assert len(data["results"]) == 1
    assert data["results"][0]["title"] == "Song Dynasty Jiaozi"
    assert session.search_calls == 1


def test_firecrawl_adapter_offline_mock_transport() -> None:
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        payload = json.loads(request.content)
        assert payload["url"] == "https://example.org/money"
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "markdown": "# History of Coinage\nLydian lions were among the first stamped coins.",
                    "metadata": {"title": "Lydian Coinage"},
                },
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    tool = FirecrawlExtractTool(api_key="fc-mock-key", client=client)
    session = ResearchExecutionSession()

    result = tool.execute({"url": "https://example.org/money"}, session)
    assert result.success is True
    assert result.untrusted_text is True
    data = result.data
    assert data["title"] == "Lydian Coinage"
    assert "Lydian lions" in data["content"]
    assert session.extract_calls == 1


# ---------------------------------------------------------------------------
# 10. Budget Consumption and Slow Streaming Deadline Regression Tests
# ---------------------------------------------------------------------------

def test_outbound_failed_and_timed_out_attempts_consume_budget() -> None:
    """Outbound provider calls must consume budget before network dispatch, even on failure/timeout."""
    # Mock transport that fails with 500 error
    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal upstream error")

    client = httpx.Client(transport=httpx.MockTransport(failing_handler))
    tool = TavilySearchTool(api_key="tvly-mock-key", client=client)
    session = ResearchExecutionSession(max_search_calls=2)

    # Call 1 fails upstream, but MUST consume 1 search invocation
    with pytest.raises(ResearchProviderError):
        tool.execute({"query": "failed call 1"}, session)
    assert session.search_calls == 1

    # Call 2 also fails upstream, consuming 2nd invocation
    with pytest.raises(ResearchProviderError):
        tool.execute({"query": "failed call 2"}, session)
    assert session.search_calls == 2

    # Call 3 must be blocked immediately by budget exhaustion before dispatch
    with pytest.raises(ResearchBudgetExceededError) as exc_info:
        tool.execute({"query": "call 3 should exceed budget"}, session)
    assert "Search budget exhausted: 2/2" in str(exc_info.value)


def test_validation_failures_do_not_consume_budget() -> None:
    """Pre-dispatch validation rejections (schema or URL safety) must not consume budget."""
    session = ResearchExecutionSession(max_extract_calls=2)
    tool = FirecrawlExtractTool(api_key="fc-mock-key")

    # 1. Pydantic schema validation failure (empty URL)
    with pytest.raises(ToolValidationError):
        tool.execute({"url": ""}, session)
    assert session.extract_calls == 0

    # 2. URL safety failure (localhost)
    with pytest.raises(UnsafeURLError):
        tool.execute({"url": "http://localhost:8000/private"}, session)
    assert session.extract_calls == 0

    # 3. URL safety failure (embedded credentials)
    with pytest.raises(UnsafeURLError):
        tool.execute({"url": "http://user:pass@example.com/"}, session)
    assert session.extract_calls == 0


def test_slow_drip_streaming_deadline_enforcement_offline() -> None:
    """A slow-drip response where chunks arrive before per-read timeout must be terminated by wall-clock deadline."""
    fake_time = [100.0]

    def mock_clock() -> float:
        return fake_time[0]

    session = ResearchExecutionSession(
        call_timeout_seconds=15.0,
        deadline_seconds=20.0,
        clock=mock_clock,
    )

    # Generator simulating slow-drip chunks where time advances during streaming
    def slow_drip_generator():
        yield b'{"results": ['
        # Advance clock by 25s, which exceeds the 20s overall session deadline
        fake_time[0] += 25.0
        yield b'{"title": "late arrival"}'

    def slow_transport_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=slow_drip_generator())

    client = httpx.Client(transport=httpx.MockTransport(slow_transport_handler))
    tool = TavilySearchTool(api_key="tvly-mock-key", client=client)

    with pytest.raises(ResearchTimeoutError) as exc_info:
        tool.execute({"query": "slow streaming topic"}, session)

    msg = str(exc_info.value)
    assert "overall deadline of 20.0s expired during response streaming" in msg


def test_transport_timeout_consumes_budget_and_blocks_subsequent() -> None:
    """A transport-level socket timeout must consume exactly one invocation and exhaust budget."""
    def timeout_transport_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Socket read timed out")

    client = httpx.Client(transport=httpx.MockTransport(timeout_transport_handler))
    tool = TavilySearchTool(api_key="tvly-mock-key", client=client)
    session = ResearchExecutionSession(max_search_calls=1)

    # Invocaton 1: transport timeout consumes the 1 allowable search call
    with pytest.raises(ResearchTimeoutError):
        tool.execute({"query": "timeout query"}, session)
    assert session.search_calls == 1

    # Invocaton 2: must be rejected with ResearchBudgetExceededError before dispatch
    with pytest.raises(ResearchBudgetExceededError) as exc_info:
        tool.execute({"query": "next query"}, session)
    assert "Search budget exhausted: 1/1" in str(exc_info.value)


def test_streaming_cleanup_on_deadline_timeout() -> None:
    """A timeout raised during response streaming must close the response and its underlying stream."""
    class TrackingByteStream(httpx.ByteStream):
        def __init__(self, fake_time_ref: list[float]) -> None:
            self.closed = False
            self._fake_time_ref = fake_time_ref

        def __iter__(self):
            yield b'{"results": ['
            # Advance fake clock beyond deadline
            self._fake_time_ref[0] += 30.0
            yield b'{"title": "never reached"}'

        def close(self) -> None:
            self.closed = True

    fake_time = [10.0]
    stream_instance = TrackingByteStream(fake_time)

    def mock_clock() -> float:
        return fake_time[0]

    session = ResearchExecutionSession(deadline_seconds=20.0, clock=mock_clock)

    def transport_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream_instance)

    client = httpx.Client(transport=httpx.MockTransport(transport_handler))
    tool = TavilySearchTool(api_key="tvly-mock-key", client=client)

    with pytest.raises(ResearchTimeoutError):
        tool.execute({"query": "streaming cleanup check"}, session)

    # Underlying stream must be cleanly closed by the context manager
    assert stream_instance.closed is True


def test_per_call_wall_clock_timeout_enforced_during_streaming() -> None:
    """During streaming, the per-call wall clock allowance must be enforced even if total deadline remains."""
    fake_time = [100.0]

    def mock_clock() -> float:
        return fake_time[0]

    # Session deadline is generous (60s), but per-call allowance is short (5s)
    session = ResearchExecutionSession(
        call_timeout_seconds=5.0,
        deadline_seconds=60.0,
        clock=mock_clock,
    )

    def slow_chunk_generator():
        yield b'{"results": ['
        # Advance clock by 6.0s (exceeding 5.0s call timeout, but well within 60s total deadline)
        fake_time[0] += 6.0
        yield b'{"title": "exceeded call allowance"}'

    def transport_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=slow_chunk_generator())

    client = httpx.Client(transport=httpx.MockTransport(transport_handler))
    tool = TavilySearchTool(api_key="tvly-mock-key", client=client)

    with pytest.raises(ResearchTimeoutError) as exc_info:
        tool.execute({"query": "per-call allowance test"}, session)

    msg = str(exc_info.value)
    assert "timed out after 5.0s during response streaming" in msg
