"""Tests for Phase 7 Checkpoint 2: Provider Factory and Production Runtime Wiring.

Verifies:
1. ResearchToolFactory creates real Tavily and Firecrawl adapters by default.
2. Missing credentials raise actionable MissingCredentialsError without falling back to mocks.
3. Configurable and validated Firecrawl API endpoint.
4. Production PipelineOrchestrator dependency wiring connects ResearchOrchestrator to ScriptService.
5. ResearchPlanner uses the configured LLM provider.
6. Explicit mock injection remains fully supported for testing.
"""

from pathlib import Path
import pytest
import httpx

from backend.app.api.routes import get_orchestrator, get_project_service
from backend.app.config import Settings
from backend.app.domain.contracts import Script, ScriptClaim, ScriptSection
from backend.app.domain.fact_check import ClaimAssessment, ScriptFactCheckReport
from backend.app.domain.research import (
    EvidenceRecord,
    PlannedQuery,
    ResearchPlan,
    ResearchRun,
    SourceRecord,
)
from backend.app.llm.mock import MockLLMProvider
from backend.app.llm.ollama import OllamaLLMProvider
from backend.app.services.pipeline.orchestrator import PipelineOrchestrator
from backend.app.services.project.service import ProjectService
from backend.app.services.research.orchestrator import ResearchOrchestrator
from backend.app.services.research.planner import ResearchPlanner
from backend.app.services.research.storage import ResearchStorageService
from backend.app.services.research.tools import (
    FirecrawlExtractTool,
    MissingCredentialsError,
    MockExtractTool,
    MockSearchTool,
    ResearchToolFactory,
    TavilySearchTool,
    ToolRegistry,
    UnsafeURLError,
)


# ---------------------------------------------------------------------------
# 1. Factory Construction & Default Tool Registration
# ---------------------------------------------------------------------------

def test_factory_creates_real_tools_by_default(tmp_path: Path):
    """By default (use_mocks=False), the factory registers TavilySearchTool and FirecrawlExtractTool."""
    settings = Settings(
        PROJECTS_DIR=tmp_path / "projects",
        TAVILY_API_KEY="test-tvly-key",
        FIRECRAWL_API_KEY="test-fc-key",
    )
    registry = ResearchToolFactory.create_tool_registry(settings=settings, use_mocks=False)

    assert isinstance(registry, ToolRegistry)
    tavily = registry.get("tavily_search")
    firecrawl = registry.get("firecrawl_extract")

    assert isinstance(tavily, TavilySearchTool)
    assert isinstance(firecrawl, FirecrawlExtractTool)
    assert not isinstance(tavily, MockSearchTool)
    assert not isinstance(firecrawl, MockExtractTool)


def test_factory_no_silent_mock_fallback_on_missing_credentials(tmp_path: Path):
    """When credentials are missing, factory must register real adapters that raise MissingCredentialsError.
    It must NEVER silently swap in MockSearchTool or MockExtractTool.
    """
    settings = Settings(
        PROJECTS_DIR=tmp_path / "projects",
        TAVILY_API_KEY=None,
        FIRECRAWL_API_KEY=None,
    )
    registry = ResearchToolFactory.create_tool_registry(settings=settings, use_mocks=False)

    tavily = registry.get("tavily_search")
    firecrawl = registry.get("firecrawl_extract")

    # Both must be real adapters, not mocks
    assert isinstance(tavily, TavilySearchTool)
    assert isinstance(firecrawl, FirecrawlExtractTool)
    assert not isinstance(tavily, MockSearchTool)
    assert not isinstance(firecrawl, MockExtractTool)

    # Calling validate_credentials or executing tool raises actionable MissingCredentialsError
    with pytest.raises(MissingCredentialsError) as exc_info:
        ResearchToolFactory.validate_credentials(settings)
    assert "ANIM_TAVILY_API_KEY" in str(exc_info.value)
    assert "ANIM_FIRECRAWL_API_KEY" in str(exc_info.value)

    # Inspecting tool's key retrieval raises actionable error
    with pytest.raises(MissingCredentialsError) as tavily_exc:
        tavily._get_api_key()
    assert "ANIM_TAVILY_API_KEY" in str(tavily_exc.value)

    with pytest.raises(MissingCredentialsError) as fc_exc:
        firecrawl._get_api_key()
    assert "ANIM_FIRECRAWL_API_KEY" in str(fc_exc.value)


def test_factory_validate_credentials_flag(tmp_path: Path):
    """Setting validate_credentials=True raises immediately during registry creation if keys missing."""
    settings = Settings(
        PROJECTS_DIR=tmp_path / "projects",
        TAVILY_API_KEY=None,
        FIRECRAWL_API_KEY="valid-key",
    )
    with pytest.raises(MissingCredentialsError) as exc_info:
        ResearchToolFactory.create_tool_registry(
            settings=settings,
            use_mocks=False,
            validate_credentials=True,
        )
    assert "ANIM_TAVILY_API_KEY" in str(exc_info.value)
    assert "ANIM_FIRECRAWL_API_KEY" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# 2. Firecrawl Configurable & Validated Endpoint
# ---------------------------------------------------------------------------

def test_firecrawl_configurable_api_endpoint(tmp_path: Path):
    """FirecrawlExtractTool respects configured FIRECRAWL_API_URL."""
    custom_url = "https://custom-firecrawl.internal.net/v1/scrape"
    settings = Settings(
        PROJECTS_DIR=tmp_path / "projects",
        FIRECRAWL_API_KEY="test-fc-key",
        FIRECRAWL_API_URL=custom_url,
    )
    tool = FirecrawlExtractTool(settings=settings)
    assert tool.api_url == custom_url


def test_firecrawl_api_endpoint_constructor_override(tmp_path: Path):
    """Constructor api_url parameter overrides settings."""
    settings = Settings(
        PROJECTS_DIR=tmp_path / "projects",
        FIRECRAWL_API_KEY="test-fc-key",
        FIRECRAWL_API_URL="https://settings-url.com/v1/scrape",
    )
    override_url = "https://override-url.com/v1/scrape"
    tool = FirecrawlExtractTool(api_url=override_url, settings=settings)
    assert tool.api_url == override_url


def test_firecrawl_api_endpoint_validation_rejects_unsafe_urls():
    """FirecrawlExtractTool rejects invalid schemes, embedded credentials, or blank URLs."""
    with pytest.raises(UnsafeURLError):
        FirecrawlExtractTool(api_url="ftp://api.firecrawl.dev/v1/scrape")

    with pytest.raises(UnsafeURLError):
        FirecrawlExtractTool(api_url="https://user:password@api.firecrawl.dev/v1/scrape")

    with pytest.raises(UnsafeURLError):
        FirecrawlExtractTool(api_url="")


# ---------------------------------------------------------------------------
# 3. Production Runtime Wiring (routes.py and PipelineOrchestrator)
# ---------------------------------------------------------------------------

def test_pipeline_orchestrator_create_production_wires_research(tmp_path: Path):
    """PipelineOrchestrator.create_production wires a real ResearchOrchestrator into both pipeline and ScriptService."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        PROJECTS_DIR=projects_dir,
        TAVILY_API_KEY="test-tvly-key",
        FIRECRAWL_API_KEY="test-fc-key",
    )
    project_service = ProjectService(projects_root=projects_dir)
    orchestrator = PipelineOrchestrator.create_production(
        project_service=project_service,
        settings=settings,
        use_mocks=False,
    )

    # 1. PipelineOrchestrator has a configured ResearchOrchestrator
    assert orchestrator.research_orchestrator is not None
    assert isinstance(orchestrator.research_orchestrator, ResearchOrchestrator)

    # 2. ScriptService has the EXACT SAME ResearchOrchestrator instance
    assert orchestrator.script_service.research_orchestrator is orchestrator.research_orchestrator

    # 3. Registry contains real adapters
    registry = orchestrator.research_orchestrator.tool_registry
    assert isinstance(registry.get("tavily_search"), TavilySearchTool)
    assert isinstance(registry.get("firecrawl_extract"), FirecrawlExtractTool)


def test_routes_get_orchestrator_dependency_wires_production_runtime(tmp_path: Path):
    """get_orchestrator() FastAPI dependency returns PipelineOrchestrator with wired ResearchOrchestrator."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    project_service = ProjectService(projects_root=projects_dir)

    pipeline = get_orchestrator(project_service=project_service)

    assert pipeline.research_orchestrator is not None
    assert isinstance(pipeline.research_orchestrator, ResearchOrchestrator)
    assert pipeline.script_service.research_orchestrator is pipeline.research_orchestrator

    registry = pipeline.research_orchestrator.tool_registry
    assert isinstance(registry.get("tavily_search"), TavilySearchTool)
    assert isinstance(registry.get("firecrawl_extract"), FirecrawlExtractTool)


# ---------------------------------------------------------------------------
# 4. ResearchPlanner Creation & LLM Provider Configuration
# ---------------------------------------------------------------------------

def test_factory_create_planner_uses_configured_llm_provider(tmp_path: Path):
    """ResearchToolFactory.create_planner wires the configured LLM provider."""
    # When LLM_PLAN_PROVIDER is ollama
    settings_ollama = Settings(
        PROJECTS_DIR=tmp_path / "projects",
        LLM_PLAN_PROVIDER="ollama",
    )
    planner_ollama = ResearchToolFactory.create_planner(settings=settings_ollama)
    assert isinstance(planner_ollama, ResearchPlanner)
    assert isinstance(planner_ollama.llm_provider, OllamaLLMProvider)

    # When LLM_PLAN_PROVIDER is mock
    settings_mock = Settings(
        PROJECTS_DIR=tmp_path / "projects",
        LLM_PLAN_PROVIDER="mock",
    )
    planner_mock = ResearchToolFactory.create_planner(settings=settings_mock)
    assert isinstance(planner_mock.llm_provider, MockLLMProvider)

    # Explicit provider injection
    custom_mock = MockLLMProvider()
    planner_custom = ResearchToolFactory.create_planner(
        settings=settings_ollama,
        llm_provider=custom_mock,
    )
    assert planner_custom.llm_provider is custom_mock


# ---------------------------------------------------------------------------
# 5. Explicit Test Mock Injection Preserved
# ---------------------------------------------------------------------------

def test_explicit_mock_injection_preserved(tmp_path: Path):
    """When use_mocks=True is explicitly passed, MockSearchTool and MockExtractTool are registered."""
    settings = Settings(PROJECTS_DIR=tmp_path / "projects")
    registry = ResearchToolFactory.create_tool_registry(settings=settings, use_mocks=True)

    tavily = registry.get("tavily_search")
    firecrawl = registry.get("firecrawl_extract")

    assert isinstance(tavily, MockSearchTool)
    assert isinstance(firecrawl, MockExtractTool)

    orchestrator = ResearchToolFactory.create_orchestrator(settings=settings, use_mocks=True)
    assert isinstance(orchestrator, ResearchOrchestrator)
    assert isinstance(orchestrator.tool_registry.get("tavily_search"), MockSearchTool)


def test_custom_tools_injection_preserved(tmp_path: Path):
    """Custom tools passed to create_tool_registry are registered as requested."""
    settings = Settings(PROJECTS_DIR=tmp_path / "projects")
    custom_mock_search = MockSearchTool()
    custom_mock_extract = MockExtractTool()

    registry = ResearchToolFactory.create_tool_registry(
        settings=settings,
        custom_tools=[custom_mock_search, custom_mock_extract],
    )
    assert registry.get("tavily_search") is custom_mock_search
    assert registry.get("firecrawl_extract") is custom_mock_extract


def test_challenge_investigate_action_uses_wired_production_orchestrator(tmp_path: Path):
    """When PipelineOrchestrator is created via create_production(use_mocks=True),
    calling challenge_claim with action='investigate' executes research using the wired orchestrator.
    """
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(PROJECTS_DIR=projects_dir, LLM_SCRIPT_PROVIDER="mock")
    project_service = ProjectService(projects_root=projects_dir)
    pipeline = PipelineOrchestrator.create_production(
        project_service=project_service,
        settings=settings,
        use_mocks=True,
    )

    project_id = "proj_investigate_wired_01"
    project_service.create_project(title="Quantum Test", project_id=project_id)

    run_id = "rrun_quantum_init"
    plan = ResearchPlan(
        plan_id="rplan_init",
        project_id=project_id,
        topic="Quantum Computing Basics",
        research_objective="Investigate quantum computing mechanisms.",
        research_questions=["What is quantum superposition?"],
        planned_queries=[
            PlannedQuery(
                query_id="qry_01",
                query_text="quantum superposition qubit explanation",
                purpose="What is quantum superposition?",
                target_question="What is quantum superposition?",
            ),
        ],
    )
    pipeline.research_storage.save_plan(plan)
    run = ResearchRun(
        run_id=run_id,
        plan_id="rplan_init",
        project_id=project_id,
        status="completed",
        search_calls_used=1,
        extract_calls_used=0,
        total_chars_extracted=1000,
        query_records=[],
    )
    pipeline.research_storage.save_run(run)

    src = SourceRecord(
        source_id="src_01",
        project_id=project_id,
        url="https://physics.example.org/superposition",
        title="Understanding Quantum Superposition",
        publisher="Institute of Physics",
        retrieval_status="extracted",
        char_count=1000,
    )
    pipeline.research_storage.save_source(project_id, src)

    evi = EvidenceRecord(
        evidence_id="evi_01",
        project_id=project_id,
        source_id="src_01",
        query_id="qry_01",
        run_id=run_id,
        excerpt="A qubit can exist in a superposition of zero and one simultaneously.",
        locator="Section 1",
        target_claim="Quantum superposition principle",
        status="retrieved",
    )
    pipeline.research_storage.save_evidence(project_id, evi)

    script = Script(
        script_id=f"scr_{project_id}",
        project_id=project_id,
        version=1,
        title="Quantum Computing Basics",
        target_duration_minutes=1.0,
        sections=[
            ScriptSection(
                id="sec_01",
                type="hook",
                heading="The Quantum Leap",
                narration="A qubit can exist in a superposition of zero and one simultaneously.",
                claim_ids=["clm_01"],
            )
        ],
        claims=[
            ScriptClaim(
                claim_id="clm_01",
                claim_text="A qubit can exist in a superposition of zero and one simultaneously.",
                section_id="sec_01",
                evidence_ids=["evi_01"],
                source_ids=["src_01"],
                link_status="linked",
                risk_level="normal",
                assessment_status="supported",
            )
        ],
        research_run_id=run_id,
    )
    report = ScriptFactCheckReport(
        project_id=project_id,
        script_version=1,
        research_run_id=run_id,
        assessments=[
            ClaimAssessment(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="A qubit can exist in a superposition of zero and one simultaneously.",
                status="supported",
                risk_level="normal",
                explanation="Directly entailed by evidence.",
                supporting_evidence_ids=["evi_01"],
            )
        ],
    )
    dirs = pipeline.script_service._get_project_dirs(project_id)
    pipeline.script_service._commit_script_version(
        project_id=project_id,
        dirs=dirs,
        script=script,
        report=report,
        event_type="draft_generated",
        event_content="Initial draft created.",
    )

    response = pipeline.script_service.challenge_claim(
        project_id=project_id,
        claim_id="clm_01",
        action="investigate",
        instruction="Verify whether superposition is confirmed in laboratory experiments.",
    )

    assert response["script"].version == 2
    assert response["challenge_summary"]["action"] == "investigate"
    assert response["challenge_summary"]["target_claim_id"] == "clm_01"
    assert response["challenge_summary"]["investigation_performed"] is True
    assert response["fact_check_report"].script_version == 2

