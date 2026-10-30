"""Comprehensive offline unit and integration tests for Research Orchestrator (P2-S3)."""

from datetime import datetime
import json
from pathlib import Path
import pytest

from backend.app.config import Settings
from backend.app.domain.research import (
    EvidenceRecord,
    PlannedQuery,
    QueryRecord,
    ResearchBudgetConstraints,
    ResearchPlan,
    ResearchRun,
    SourceRecord,
)
from backend.app.services.project.service import ProjectService
from backend.app.services.research.orchestrator import (
    ResearchOrchestrator,
    ResearchOrchestratorError,
    normalize_url_for_dedupe,
)
from backend.app.services.research.storage import ResearchStorageService
from backend.app.services.research.tools.base import (
    ExtractToolResult,
    ResearchExecutionSession,
    ResearchProviderError,
    ResearchTimeoutError,
    SearchResultItem,
    SearchToolResult,
    ToolRegistry,
    ToolResult,
)
from backend.app.services.research.tools.mock import MockExtractTool, MockSearchTool


@pytest.fixture
def project_env(tmp_path: Path):
    """Fixture providing an isolated project workspace with storage and tool registry."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(PROJECTS_DIR=str(projects_dir))

    proj_service = ProjectService(projects_root=projects_dir)
    proj = proj_service.create_project(title="Orchestration Test Project", project_id="proj_orch_01")
    project_id = proj.metadata.project_id

    storage_service = ResearchStorageService(projects_root=projects_dir, settings=settings)

    registry = ToolRegistry()
    search_tool = MockSearchTool(name="tavily_search")
    extract_tool = MockExtractTool(name="firecrawl_extract")
    registry.register(search_tool)
    registry.register(extract_tool)

    return {
        "projects_dir": projects_dir,
        "project_id": project_id,
        "storage": storage_service,
        "registry": registry,
        "search_tool": search_tool,
        "extract_tool": extract_tool,
        "settings": settings,
    }


def create_sample_plan(
    storage: ResearchStorageService,
    project_id: str,
    plan_id: str = "rplan_01",
    queries: int = 2,
    max_search_calls: int = 3,
    max_extract_calls: int = 3,
    deadline_seconds: float = 60.0,
) -> ResearchPlan:
    """Helper to persist a test plan with configurable queries and budget limits."""
    planned_queries = [
        PlannedQuery(
            query_id=f"qry_{i:02d}",
            query_text=f"Historical evidence query {i}",
            purpose=f"Investigate core mechanism {i}",
            target_question=f"What occurred during phase {i} of currency evolution?",
            tool_category="web_search",
        )
        for i in range(1, queries + 1)
    ]

    plan = ResearchPlan(
        plan_id=plan_id,
        project_id=project_id,
        topic="History of Currency",
        research_objective="Investigate origins and evolution of standardized coinage",
        research_questions=[q.target_question for q in planned_queries],
        planned_queries=planned_queries,
        approved_tool_categories=["web_search", "web_extract"],
        budget_limits=ResearchBudgetConstraints(
            max_search_calls=max_search_calls,
            max_extract_calls=max_extract_calls,
            deadline_seconds=deadline_seconds,
        ),
        schema_version="1.0.0",
    )
    storage.save_plan(plan)
    return plan


# ---------------------------------------------------------------------------
# Test 1: Full Research Success
# ---------------------------------------------------------------------------

def test_orchestrator_full_successful_run(project_env):
    """Verify full successful research run creates queries, sources, content, evidence, and completed_full_coverage status."""
    plan = create_sample_plan(project_env["storage"], project_env["project_id"], queries=2)

    orchestrator = ResearchOrchestrator(
        tool_registry=project_env["registry"],
        storage_service=project_env["storage"],
        settings=project_env["settings"],
    )

    run = orchestrator.execute_plan(project_env["project_id"], plan.plan_id)

    assert isinstance(run, ResearchRun)
    assert run.status == "completed"
    assert run.termination_reason == "completed_full_coverage"
    assert run.search_calls_used == 2
    assert run.extract_calls_used >= 1
    assert run.total_chars_extracted > 0
    assert run.completed_at is not None

    # Verify query records
    assert len(run.query_records) == 2
    for q in run.query_records:
        assert q.status == "succeeded"
        assert len(q.returned_source_ids) > 0
        assert q.result_count > 0

    # Verify sources persisted in sources.json
    sources = project_env["storage"].list_sources(project_env["project_id"])
    assert len(sources) >= 1
    for s in sources:
        assert s.retrieval_status == "extracted"
        assert s.char_count > 0
        assert s.content_artifact_ref is not None
        assert s.content_hash is not None
        # Verify content file exists
        content = project_env["storage"].get_source_content(project_env["project_id"], s.source_id)
        assert content is not None
        assert len(content) > 0

    # Verify evidence records persisted in evidence.json
    evidence = project_env["storage"].list_evidence(project_env["project_id"])
    assert len(evidence) >= 1
    for e in evidence:
        assert isinstance(e, EvidenceRecord)
        assert e.run_id == run.run_id
        assert len(e.excerpt) > 0
        assert e.status == "retrieved"
        assert "heading" in e.locator or "section" in e.locator
        assert e.target_claim is not None


# ---------------------------------------------------------------------------
# Test 2: Search Snippets Never Become Evidence on Extract Failure
# ---------------------------------------------------------------------------

def test_snippets_never_promoted_to_evidence_on_extract_failure(project_env):
    """Verify that when extraction fails, discovery_snippet is preserved as metadata but zero EvidenceRecords are generated."""
    plan = create_sample_plan(project_env["storage"], project_env["project_id"], queries=1)

    # Configure extraction tool to simulate failure (e.g. 500 error from remote scraper)
    def failing_extract(args, session):
        raise ResearchProviderError("Scraping service encountered HTTP 500 error.")

    failing_extract_tool = MockExtractTool(name="firecrawl_extract", custom_handler=failing_extract)
    registry = ToolRegistry()
    registry.register(project_env["search_tool"])
    registry.register(failing_extract_tool)

    orchestrator = ResearchOrchestrator(
        tool_registry=registry,
        storage_service=project_env["storage"],
        settings=project_env["settings"],
    )

    run = orchestrator.execute_plan(project_env["project_id"], plan.plan_id)

    # With zero evidence extracted, run must fail honestly
    assert run.status == "failed"
    assert "failed_no_evidence_extracted" in (run.termination_reason or "")

    # Source retains discovery snippet
    sources = project_env["storage"].list_sources(project_env["project_id"])
    assert len(sources) >= 1
    for s in sources:
        assert s.retrieval_status == "failed"
        assert s.discovery_snippet is not None
        assert len(s.discovery_snippet) > 0

    # ZERO evidence records exist in storage
    evidence = project_env["storage"].list_evidence(project_env["project_id"])
    assert len(evidence) == 0


# ---------------------------------------------------------------------------
# Test 3: Conservative URL Deduplication
# ---------------------------------------------------------------------------

def test_conservative_url_deduplication(project_env):
    """Verify that URLs differing only by tracking parameters are deduplicated without stripping content parameters."""
    # Test deduplication normalizer unit logic
    u1 = "https://example.com/article?id=99&utm_source=twitter&utm_medium=social"
    u2 = "HTTPS://example.com/article?utm_campaign=spring&id=99"
    u3 = "https://example.com/article?id=100"

    norm1 = normalize_url_for_dedupe(u1)
    norm2 = normalize_url_for_dedupe(u2)
    norm3 = normalize_url_for_dedupe(u3)

    assert norm1 == "https://example.com/article?id=99"
    assert norm1 == norm2  # Tracking stripped, content param id=99 preserved
    assert norm1 != norm3  # Different id preserved

    # Integration test: Two queries return the same underlying article
    canned = [
        SearchResultItem(
            title="Ancient Coins Overview",
            url="https://example.org/coins?article=42&utm_source=search",
            snippet="Overview of coinage invention in Lydia.",
        )
    ]
    search_tool = MockSearchTool(name="tavily_search", canned_results=canned)
    registry = ToolRegistry()
    registry.register(search_tool)
    registry.register(project_env["extract_tool"])

    plan = create_sample_plan(project_env["storage"], project_env["project_id"], queries=2)
    orchestrator = ResearchOrchestrator(
        tool_registry=registry,
        storage_service=project_env["storage"],
        settings=project_env["settings"],
    )

    run = orchestrator.execute_plan(project_env["project_id"], plan.plan_id)
    assert run.status == "completed"

    # Only ONE SourceRecord created despite being returned by both queries
    sources = project_env["storage"].list_sources(project_env["project_id"])
    assert len(sources) == 1
    assert sources[0].canonical_url == "https://example.org/coins?article=42"
    # Extracted only once
    assert run.extract_calls_used == 1


# ---------------------------------------------------------------------------
# Test 4: Partial Run Budget Exhaustion
# ---------------------------------------------------------------------------

def test_partial_run_budget_exhaustion(project_env):
    """Verify that when max_search_calls ceiling is hit, remaining queries are skipped and partial coverage is reported."""
    # 3 planned queries, but max_search_calls is 2
    plan = create_sample_plan(
        project_env["storage"],
        project_env["project_id"],
        queries=3,
        max_search_calls=2,
    )

    orchestrator = ResearchOrchestrator(
        tool_registry=project_env["registry"],
        storage_service=project_env["storage"],
        settings=project_env["settings"],
    )

    run = orchestrator.execute_plan(project_env["project_id"], plan.plan_id)

    assert run.status == "completed"
    assert "partial_coverage_budget_exhausted" in (run.termination_reason or "")
    assert run.search_calls_used == 2

    # Query 1 and 2 succeeded; Query 3 skipped
    q_map = {q.query_id: q for q in run.query_records}
    assert q_map["qry_01"].status == "succeeded"
    assert q_map["qry_02"].status == "succeeded"
    assert q_map["qry_03"].status == "skipped"
    assert "budget limit reached" in (q_map["qry_03"].error_message or "")


# ---------------------------------------------------------------------------
# Test 5: Partial Run Deadline Expiration
# ---------------------------------------------------------------------------

def test_partial_run_deadline_expiration_with_extracted_evidence(project_env):
    """Verify deadline expiration after evidence is gathered results in partial_coverage_deadline_exceeded."""
    simulated_time = 1000.0

    def mock_clock():
        return simulated_time

    plan = create_sample_plan(
        project_env["storage"],
        project_env["project_id"],
        queries=2,
        deadline_seconds=10.0,
    )

    # Wrap extract tool so after 1 extraction, the clock advances past deadline
    original_extract = project_env["extract_tool"]._run
    extract_count = 0

    def time_advancing_extract(args, session):
        nonlocal simulated_time, extract_count
        extract_count += 1
        res = original_extract(args, session)
        simulated_time += 15.0  # Exceeds 10.0s deadline during extraction stage
        return res

    mock_extract = MockExtractTool(name="firecrawl_extract")
    mock_extract._run = time_advancing_extract

    registry = ToolRegistry()
    registry.register(project_env["search_tool"])
    registry.register(mock_extract)

    orchestrator = ResearchOrchestrator(
        tool_registry=registry,
        storage_service=project_env["storage"],
        settings=project_env["settings"],
    )

    run = orchestrator.execute_plan(project_env["project_id"], plan.plan_id, clock=mock_clock)

    assert run.status == "completed"
    assert "partial_coverage_deadline_exceeded" in (run.termination_reason or "")
    assert run.extract_calls_used >= 1
    # Evidence was extracted for at least one source
    evidence = project_env["storage"].list_evidence(project_env["project_id"])
    assert len(evidence) >= 1


def test_deadline_expiration_before_evidence_marks_run_failed(project_env):
    """Verify deadline expiration before any evidence can be extracted honestly marks the run failed."""
    simulated_time = 1000.0

    def mock_clock():
        return simulated_time

    plan = create_sample_plan(
        project_env["storage"],
        project_env["project_id"],
        queries=2,
        deadline_seconds=10.0,
    )

    # Wrap search tool to advance clock past deadline immediately on query 1
    original_search = project_env["search_tool"]._run

    def time_advancing_search(args, session):
        nonlocal simulated_time
        res = original_search(args, session)
        simulated_time += 15.0  # Exceeds 10.0s deadline before extraction can start
        return res

    mock_search = MockSearchTool(name="tavily_search")
    mock_search._run = time_advancing_search

    registry = ToolRegistry()
    registry.register(mock_search)
    registry.register(project_env["extract_tool"])

    orchestrator = ResearchOrchestrator(
        tool_registry=registry,
        storage_service=project_env["storage"],
        settings=project_env["settings"],
    )

    run = orchestrator.execute_plan(project_env["project_id"], plan.plan_id, clock=mock_clock)

    assert run.status == "failed"
    assert "failed_deadline_exceeded_before_evidence" in (run.termination_reason or "")
    q_map = {q.query_id: q for q in run.query_records}
    assert q_map["qry_01"].status == "succeeded"
    assert q_map["qry_02"].status == "skipped"
    assert "deadline exceeded" in (q_map["qry_02"].error_message or "").lower()


# ---------------------------------------------------------------------------
# Test 6: Partial Run Query Errors & Failed Calls Consume Budget
# ---------------------------------------------------------------------------

def test_partial_run_query_errors_and_budget_consumption(project_env):
    """Verify failed search calls consume budget and result in partial_coverage_query_errors status."""
    call_count = 0

    def sometimes_failing_search(args, session):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            session.consume_search_budget()
            raise ResearchTimeoutError("Tavily gateway timeout after 15.0s")
        return project_env["search_tool"]._run(args, session)

    failing_search_tool = MockSearchTool(name="tavily_search")
    failing_search_tool._run = sometimes_failing_search

    registry = ToolRegistry()
    registry.register(failing_search_tool)
    registry.register(project_env["extract_tool"])

    plan = create_sample_plan(project_env["storage"], project_env["project_id"], queries=2)
    orchestrator = ResearchOrchestrator(
        tool_registry=registry,
        storage_service=project_env["storage"],
        settings=project_env["settings"],
    )

    run = orchestrator.execute_plan(project_env["project_id"], plan.plan_id)

    assert run.status == "completed"
    assert "partial_coverage_query_errors" in (run.termination_reason or "")
    assert run.search_calls_used == 2

    q_map = {q.query_id: q for q in run.query_records}
    assert q_map["qry_01"].status == "failed"
    assert "timeout" in (q_map["qry_01"].error_message or "").lower()
    assert q_map["qry_02"].status == "succeeded"


# ---------------------------------------------------------------------------
# Test 7: All Queries Failing Results in Failed Run
# ---------------------------------------------------------------------------

def test_all_queries_failing_marks_run_failed(project_env):
    """Verify that when all search queries fail, the run status transitions to 'failed' honestly."""
    def always_failing_search(args, session):
        session.consume_search_budget()
        raise ResearchProviderError("External search provider 500 Outage.")

    failing_search_tool = MockSearchTool(name="tavily_search")
    failing_search_tool._run = always_failing_search

    registry = ToolRegistry()
    registry.register(failing_search_tool)
    registry.register(project_env["extract_tool"])

    plan = create_sample_plan(project_env["storage"], project_env["project_id"], queries=2)
    orchestrator = ResearchOrchestrator(
        tool_registry=registry,
        storage_service=project_env["storage"],
        settings=project_env["settings"],
    )

    run = orchestrator.execute_plan(project_env["project_id"], plan.plan_id)

    assert run.status == "failed"
    assert "failed_all_queries_errored: 0 of 2 queries succeeded" in (run.termination_reason or "")
    for q in run.query_records:
        assert q.status == "failed"


# ---------------------------------------------------------------------------
# Test 8: Zero Search Results Marks Run Failed
# ---------------------------------------------------------------------------

def test_zero_search_results_marks_run_failed(project_env):
    """Verify that when queries succeed but return zero results, the run is marked failed."""
    empty_search_tool = MockSearchTool(name="tavily_search", canned_results=[])
    registry = ToolRegistry()
    registry.register(empty_search_tool)
    registry.register(project_env["extract_tool"])

    plan = create_sample_plan(project_env["storage"], project_env["project_id"], queries=1)
    orchestrator = ResearchOrchestrator(
        tool_registry=registry,
        storage_service=project_env["storage"],
        settings=project_env["settings"],
    )

    run = orchestrator.execute_plan(project_env["project_id"], plan.plan_id)

    assert run.status == "failed"
    assert "failed_no_sources_found" in (run.termination_reason or "")


# ---------------------------------------------------------------------------
# Test 9: Unsafe URLs Handled Safely Without Consuming Extract Budget
# ---------------------------------------------------------------------------

def test_unsafe_url_handled_safely_without_budget_consumption(project_env):
    """Verify SSRF pre-flight validation marks unsafe sources unavailable and consumes 0 extract calls."""
    unsafe_items = [
        SearchResultItem(
            title="Internal Metadata Service",
            url="http://169.254.169.254/latest/meta-data/",
            snippet="AWS cloud metadata credentials leak.",
        ),
        SearchResultItem(
            title="Local Router Admin",
            url="http://192.168.1.1/admin",
            snippet="Router settings page.",
        ),
    ]
    search_tool = MockSearchTool(name="tavily_search", canned_results=unsafe_items)
    registry = ToolRegistry()
    registry.register(search_tool)
    registry.register(project_env["extract_tool"])

    plan = create_sample_plan(project_env["storage"], project_env["project_id"], queries=1)
    orchestrator = ResearchOrchestrator(
        tool_registry=registry,
        storage_service=project_env["storage"],
        settings=project_env["settings"],
    )

    run = orchestrator.execute_plan(project_env["project_id"], plan.plan_id)

    # All discovered sources were unsafe, so 0 extractions were executed
    assert run.status == "failed"
    assert "failed_no_evidence_extracted" in (run.termination_reason or "")
    assert run.extract_calls_used == 0

    sources = project_env["storage"].list_sources(project_env["project_id"])
    assert len(sources) == 2
    for s in sources:
        assert s.retrieval_status == "unavailable"


# ---------------------------------------------------------------------------
# Test 10: Persistence Failure Aborts Run and Preserves Prior Artifacts
# ---------------------------------------------------------------------------

def test_persistence_failure_aborts_run_without_false_success(project_env, monkeypatch):
    """Verify persistence errors raise ResearchOrchestratorError and do not produce a success result."""
    plan = create_sample_plan(project_env["storage"], project_env["project_id"], queries=1)

    orchestrator = ResearchOrchestrator(
        tool_registry=project_env["registry"],
        storage_service=project_env["storage"],
        settings=project_env["settings"],
    )

    # Monkeypatch save_evidence to simulate a disk failure
    def failing_save_evidence(project_id, evidence):
        raise OSError("Disk full: simulated ENOSPC")

    monkeypatch.setattr(project_env["storage"], "save_evidence", failing_save_evidence)

    with pytest.raises(ResearchOrchestratorError) as exc_info:
        orchestrator.execute_plan(project_env["project_id"], plan.plan_id)

    assert "persistence failure" in str(exc_info.value).lower()

    # Verify run is marked failed in storage (or reconciled to failed)
    runs = project_env["storage"].list_runs(project_env["project_id"])
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert "persistence_failure" in (runs[0].termination_reason or "")


# ---------------------------------------------------------------------------
# Test 11: Interrupted Run Recovery
# ---------------------------------------------------------------------------

def test_interrupted_run_recovery(project_env):
    """Verify that an abandoned 'running' state on disk reconciles to 'failed' on subsequent access."""
    plan = create_sample_plan(project_env["storage"], project_env["project_id"], queries=1)

    # Create run and transition to running
    run = project_env["storage"].create_run(project_env["project_id"], plan.plan_id)
    project_env["storage"].transition_run_status(project_env["project_id"], run.run_id, "running")

    # Simulate process crash / restart by initializing a fresh storage service instance
    fresh_storage = ResearchStorageService(
        projects_root=project_env["projects_dir"],
        settings=project_env["settings"],
    )

    # Retrieve run from fresh service
    reconciled_run = fresh_storage.get_run(project_env["project_id"], run.run_id)
    assert reconciled_run.status == "failed"
    assert "Process interrupted" in (reconciled_run.termination_reason or "")


# ---------------------------------------------------------------------------
# Test 12: Exact Fetch URL Validated and Passed to Extractor
# ---------------------------------------------------------------------------

def test_exact_fetch_url_validated_and_passed_to_extractor(project_env, monkeypatch):
    """Verify validate_url_safety() and extractor receive the exact fetch URL, with normalization only for deduplication."""
    validated_urls: list[str] = []
    from backend.app.services.research import orchestrator as orch_module

    original_validate = orch_module.validate_url_safety

    def spy_validate(url: str):
        validated_urls.append(url)
        return original_validate(url)

    monkeypatch.setattr(orch_module, "validate_url_safety", spy_validate)

    raw_fetch_url = "https://example.org/article?id=99&utm_source=newsletter"
    canned = [
        SearchResultItem(
            title="Tracking URL Test",
            url=raw_fetch_url,
            snippet="Testing URL fidelity.",
        )
    ]

    extracted_urls: list[str] = []

    def spy_extract_handler(args, session):
        extracted_urls.append(args.url)
        return "# Article 99\n\nContent for article 99."

    spy_search = MockSearchTool(name="tavily_search", canned_results=canned)
    spy_extract = MockExtractTool(name="firecrawl_extract", custom_handler=spy_extract_handler)

    registry = ToolRegistry()
    registry.register(spy_search)
    registry.register(spy_extract)

    plan = create_sample_plan(project_env["storage"], project_env["project_id"], queries=1)
    orchestrator = ResearchOrchestrator(
        tool_registry=registry,
        storage_service=project_env["storage"],
        settings=project_env["settings"],
    )

    run = orchestrator.execute_plan(project_env["project_id"], plan.plan_id)
    assert run.status == "completed"

    # 1. validate_url_safety was called on the EXACT raw fetch URL
    assert raw_fetch_url in validated_urls

    # 2. extractor tool received the EXACT raw fetch URL
    assert len(extracted_urls) == 1
    assert extracted_urls[0] == raw_fetch_url

    # 3. canonical_url was normalized purely for deduplication
    sources = project_env["storage"].list_sources(project_env["project_id"])
    assert len(sources) == 1
    assert sources[0].url == raw_fetch_url
    assert sources[0].canonical_url == "https://example.org/article?id=99"


# ---------------------------------------------------------------------------
# Test 13: Content Persisted Strictly Before EvidenceRecord
# ---------------------------------------------------------------------------

def test_content_persisted_strictly_before_evidence_record(project_env, monkeypatch):
    """Verify content markdown is persisted on disk BEFORE save_evidence, and failure to persist content aborts evidence creation."""
    plan = create_sample_plan(project_env["storage"], project_env["project_id"], queries=1)

    events_order: list[str] = []
    original_save_source = project_env["storage"].save_source
    original_save_evidence = project_env["storage"].save_evidence

    def spy_save_source(project_id, source, content=None):
        if content is not None:
            events_order.append("save_source_content")
        return original_save_source(project_id, source, content)

    def spy_save_evidence(project_id, evidence):
        events_order.append("save_evidence")
        # Assert content file already exists on disk when save_evidence is called
        content_path = (
            project_env["projects_dir"]
            / project_id
            / "research"
            / "content"
            / f"{evidence.source_id}.md"
        )
        assert content_path.exists(), "Content artifact must exist on disk before save_evidence"
        return original_save_evidence(project_id, evidence)

    monkeypatch.setattr(project_env["storage"], "save_source", spy_save_source)
    monkeypatch.setattr(project_env["storage"], "save_evidence", spy_save_evidence)

    orchestrator = ResearchOrchestrator(
        tool_registry=project_env["registry"],
        storage_service=project_env["storage"],
        settings=project_env["settings"],
    )

    run = orchestrator.execute_plan(project_env["project_id"], plan.plan_id)
    assert run.status == "completed"

    # Confirmed order: each source's content is persisted strictly before its evidence
    assert len(events_order) >= 2
    for i in range(1, len(events_order), 2):
        assert events_order[i - 1] == "save_source_content"
        assert events_order[i] == "save_evidence"

    # Part B: verify failure during content saving halts execution and prevents evidence creation
    def failing_save_source(project_id, source, content=None):
        if content is not None:
            raise OSError("Disk write failed: simulated failure while writing content markdown")
        return original_save_source(project_id, source, content)

    monkeypatch.setattr(project_env["storage"], "save_source", failing_save_source)

    evidence_call_count = 0

    def fail_if_save_evidence_called(project_id, evidence):
        nonlocal evidence_call_count
        evidence_call_count += 1
        return original_save_evidence(project_id, evidence)

    monkeypatch.setattr(project_env["storage"], "save_evidence", fail_if_save_evidence_called)

    plan_2 = create_sample_plan(project_env["storage"], project_env["project_id"], plan_id="rplan_02", queries=1)
    with pytest.raises(ResearchOrchestratorError) as exc_info:
        orchestrator.execute_plan(project_env["project_id"], plan_2.plan_id)

    assert "persistence failure" in str(exc_info.value).lower()
    # Confirmed: save_evidence was NEVER called when content saving failed
    assert evidence_call_count == 0


# ---------------------------------------------------------------------------
# Test 14: Terminal State Persistence Failure Propagates Error & Reconciles
# ---------------------------------------------------------------------------

def test_terminal_state_persistence_failure_propagates_error_and_reconciles(project_env, monkeypatch):
    """Verify that failure to persist the terminal run state propagates ResearchOrchestratorError and reconciles to failed."""
    plan = create_sample_plan(project_env["storage"], project_env["project_id"], queries=1)

    orchestrator = ResearchOrchestrator(
        tool_registry=project_env["registry"],
        storage_service=project_env["storage"],
        settings=project_env["settings"],
    )

    original_transition = project_env["storage"].transition_run_status
    terminal_transition_attempted = False

    def failing_terminal_transition(project_id, run_id, to_status, termination_reason=None):
        nonlocal terminal_transition_attempted
        if to_status in ("completed", "failed") and "running" not in to_status:
            terminal_transition_attempted = True
            raise OSError("Disk write failed: simulated ENOSPC on terminal state transition")
        return original_transition(project_id, run_id, to_status, termination_reason)

    monkeypatch.setattr(project_env["storage"], "transition_run_status", failing_terminal_transition)

    with pytest.raises(ResearchOrchestratorError) as exc_info:
        orchestrator.execute_plan(project_env["project_id"], plan.plan_id)

    assert terminal_transition_attempted is True
    assert "persistence failure" in str(exc_info.value).lower()

    # Verify run was NOT reported as successful, and fresh storage reconciles the unfinalized in-flight run to failed
    fresh_storage = ResearchStorageService(
        projects_root=project_env["projects_dir"],
        settings=project_env["settings"],
    )
    runs = fresh_storage.list_runs(project_env["project_id"])
    assert len(runs) == 1
    # Run was interrupted because terminal state could not be written to disk
    assert runs[0].status == "failed"
    assert "Process interrupted" in (runs[0].termination_reason or "")

