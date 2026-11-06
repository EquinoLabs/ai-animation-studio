"""Tests for Phase 7 Checkpoint 3: Research Pipeline Integration.

Verifies:
1. Clean-project research initiation via API without preseeded fixture JSON.
2. Research planning, tool execution, evidence extraction, and brief compilation.
3. Research-to-script handoff, run provenance, and evidence citing.
4. Claim-level fact-check integration against research evidence catalog.
5. Automated research triggering on clean start_script.
6. Partial and total failure behavior and status observability.
7. Production mock-fallback prohibition (missing credentials raise error).
8. Conversational investigate challenge regression.
"""

from pathlib import Path
from typing import Any, Dict, List
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.domain.research import (
    EvidenceRecord,
    PlannedQuery,
    ResearchBudgetConstraints,
    ResearchPlan,
    ResearchRun,
    SourceRecord,
)
from backend.app.domain.fact_check import ClaimAssessment, ScriptFactCheckReport
from backend.app.domain.contracts import Script, ScriptSection, ScriptClaim
from backend.app.llm.mock import MockLLMProvider
from backend.app.main import create_app
from backend.app.api.routes import get_orchestrator, get_project_service
from backend.app.services.pipeline.orchestrator import PipelineError, PipelineOrchestrator
from backend.app.services.project.service import ProjectService
from backend.app.services.scriptwriter.outliner import OutlinerInput, ScriptOutlinerRunError
from backend.app.services.research.orchestrator import ResearchOrchestrator
from backend.app.services.research.storage import ResearchStorageService
from backend.app.services.research.tools import (
    MissingCredentialsError,
    MockExtractTool,
    MockSearchTool,
    ResearchToolFactory,
    SearchResultItem,
    ToolRegistry,
)


@pytest.fixture
def clean_pipeline_env(tmp_path: Path):
    """Create a clean pipeline environment with isolated projects and mock tools."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        PROJECTS_DIR=projects_dir,
        LLM_SCRIPT_PROVIDER="mock",
        LLM_PLAN_PROVIDER="mock",
    )
    project_service = ProjectService(projects_root=projects_dir)
    storage_service = ResearchStorageService(projects_root=projects_dir, settings=settings)

    # Use explicit mock tools for controlled testing double
    canned_results = [
        SearchResultItem(
            title="Quantum Superposition Mechanics",
            url="https://example.com/superposition-mechanics",
            snippet="Quantum superposition allows qubits to simultaneously represent zero and one.",
        ),
        SearchResultItem(
            title="Classical Computing Comparison",
            url="https://example.org/binary-vs-qubit",
            snippet="Classical computers use binary transistors that are strictly 0 or 1.",
        ),
    ]
    mock_search = MockSearchTool(name="tavily_search", canned_results=canned_results)
    canned_content = (
        "# Quantum Superposition\n\n"
        "A qubit can exist in a linear superposition of state zero and state one simultaneously. "
        "In contrast, standard silicon microprocessors encode information strictly as binary bits."
    )
    mock_extract = MockExtractTool(name="firecrawl_extract", canned_content=canned_content, check_ssrf=False)

    registry = ToolRegistry()
    registry.register(mock_search)
    registry.register(mock_extract)

    research_orchestrator = ResearchOrchestrator(
        tool_registry=registry,
        storage_service=storage_service,
        settings=settings,
    )

    mock_llm = MockLLMProvider()
    pipeline = PipelineOrchestrator(
        project_service=project_service,
        research_orchestrator=research_orchestrator,
        settings=settings,
    )
    # Ensure script_service uses the mock LLM
    pipeline.script_service.llm_provider = mock_llm
    pipeline.script_service.outliner.llm_provider = mock_llm
    pipeline.script_service.generator.llm_provider = mock_llm
    pipeline.script_service.fact_checker.llm_provider = mock_llm
    pipeline.script_service.repairer.llm_provider = mock_llm

    app = create_app()
    app.dependency_overrides[get_project_service] = lambda: project_service
    app.dependency_overrides[get_orchestrator] = lambda: pipeline

    with TestClient(app) as client:
        yield {
            "client": client,
            "pipeline": pipeline,
            "project_service": project_service,
            "storage_service": storage_service,
            "settings": settings,
            "projects_dir": projects_dir,
            "mock_search": mock_search,
            "mock_extract": mock_extract,
        }


# ---------------------------------------------------------------------------
# 1. Clean Project Research Initiation via API
# ---------------------------------------------------------------------------

def test_clean_project_research_initiation_endpoint(clean_pipeline_env):
    """A clean project without preseeded JSON can initiate research via POST /research/start."""
    client: TestClient = clean_pipeline_env["client"]
    project_service: ProjectService = clean_pipeline_env["project_service"]

    # 1. Create a brand new clean project
    project = project_service.create_project(title="Superposition Explainer", project_id="proj_clean_init_01")

    # 2. Call research initiation API
    payload = {
        "topic": "Quantum Superposition and Qubits",
        "target_duration_minutes": 2.0,
        "user_guidance": "Focus on physical differences between qubits and classical bits.",
    }
    res = client.post(f"/api/v1/projects/{project.metadata.project_id}/research/start", json=payload)
    assert res.status_code == 201
    data = res.json()

    # 3. Verify response schema and status
    assert data["project_id"] == project.metadata.project_id
    assert data["run_id"].startswith("rrun_")
    assert data["status"] in ("completed", "pending", "running")
    assert data["evidence_count"] > 0
    assert data["sources_count"] > 0
    assert data["has_brief"] is True

    # 4. Verify overview payload is populated
    overview = data["overview"]
    assert overview["has_research"] is True
    assert overview["active_run_id"] == data["run_id"]
    assert len(overview["queries"]) > 0
    assert len(overview["sources"]) > 0
    assert len(overview["selected_evidence"]) > 0


# ---------------------------------------------------------------------------
# 2. Research Persistence & Artifact Verification
# ---------------------------------------------------------------------------

def test_research_persistence_and_evidence_brief(clean_pipeline_env):
    """Research run produces persisted plans, runs, queries, sources, evidence, and evidence_brief.md."""
    pipeline: PipelineOrchestrator = clean_pipeline_env["pipeline"]
    project_service: ProjectService = clean_pipeline_env["project_service"]
    projects_dir: Path = clean_pipeline_env["projects_dir"]

    project = project_service.create_project(title="Physics Lab", project_id="proj_persist_01")
    resp = pipeline.start_research(
        project_id=project.metadata.project_id,
        topic="Superconducting Qubits",
    )

    project_dir = projects_dir / project.metadata.project_id
    research_dir = project_dir / "research"

    # Verify directory structure and files on disk
    assert (research_dir / "plans" / f"{resp.plan_id}.json").exists()
    assert (research_dir / "runs" / f"{resp.run_id}.json").exists()
    assert (research_dir / "evidence_brief.md").exists()

    brief_text = (research_dir / "evidence_brief.md").read_text(encoding="utf-8")
    assert "# Evidence Brief" in brief_text
    assert "Methodological Limitations" in brief_text

    # Verify evidence persistence
    assert (research_dir / "evidence" / "evidence.json").exists()
    persisted_evidence = pipeline.research_storage.list_evidence(project.metadata.project_id)
    assert len(persisted_evidence) == resp.evidence_count
    assert resp.evidence_count > 0


# ---------------------------------------------------------------------------
# 3. Research-to-Script Handoff & Provenance
# ---------------------------------------------------------------------------

def test_research_to_script_handoff_and_run_provenance(clean_pipeline_env):
    """Script generated after research references the research run and its evidence."""
    pipeline: PipelineOrchestrator = clean_pipeline_env["pipeline"]
    project_service: ProjectService = clean_pipeline_env["project_service"]

    project = project_service.create_project(title="Quantum Intro", project_id="proj_handoff_01")

    # 1. Run research
    res_resp = pipeline.start_research(
        project_id=project.metadata.project_id,
        topic="Quantum Superposition",
    )
    assert res_resp.status == "completed"

    # 2. Start script without specifying run_id or allow_unresearched
    script = pipeline.start_script(
        project_id=project.metadata.project_id,
        topic="Quantum Superposition",
        target_duration_minutes=2.0,
        allow_unresearched=False,
    )

    # 3. Assert script references the research run
    assert script.research_run_id == res_resp.run_id
    assert script.version == 1

    # 4. Assert script claims cite valid evidence records from the run
    all_evidence = pipeline.research_storage.list_evidence(project.metadata.project_id, run_id=res_resp.run_id)
    valid_eids = {e.evidence_id for e in all_evidence}

    claimed_eids = [eid for c in script.claims for eid in c.evidence_ids]
    for eid in claimed_eids:
        assert eid in valid_eids


# ---------------------------------------------------------------------------
# 4. Clean Project Auto-Research on start_script
# ---------------------------------------------------------------------------

def test_clean_project_auto_research_on_start_script(clean_pipeline_env):
    """Calling start_script on a clean project automatically initiates research when allow_unresearched=False."""
    pipeline: PipelineOrchestrator = clean_pipeline_env["pipeline"]
    project_service: ProjectService = clean_pipeline_env["project_service"]

    project = project_service.create_project(title="Clean Auto Test", project_id="proj_auto_01")

    # Project starts completely empty
    existing_runs = pipeline.research_storage.list_runs(project.metadata.project_id)
    assert len(existing_runs) == 0

    # Start script with allow_unresearched=False (default auto_research=True)
    script = pipeline.start_script(
        project_id=project.metadata.project_id,
        topic="Quantum Superposition and Qubits",
        target_duration_minutes=1.5,
        allow_unresearched=False,
    )

    assert script.version == 1
    assert script.research_run_id is not None
    assert script.research_run_id.startswith("rrun_")

    # Verify research artifacts were created automatically
    runs_after = pipeline.research_storage.list_runs(project.metadata.project_id)
    assert len(runs_after) == 1
    assert runs_after[0].run_id == script.research_run_id

    # Verify fact-check report was automatically generated and references the run
    report = pipeline.get_fact_check_report(project.metadata.project_id)
    assert report is not None
    assert report.research_run_id == script.research_run_id
    assert report.script_version == 1


# ---------------------------------------------------------------------------
# 5. Claim-Level Fact Checking Provenance Integration
# ---------------------------------------------------------------------------

def test_claim_level_fact_check_integration(clean_pipeline_env):
    """Claim-level fact checking runs against the evidence catalog and adheres to Phase 5 rules."""
    pipeline: PipelineOrchestrator = clean_pipeline_env["pipeline"]
    project_service: ProjectService = clean_pipeline_env["project_service"]

    project = project_service.create_project(title="Fact Check Test", project_id="proj_fact_01")
    script = pipeline.start_script(
        project_id=project.metadata.project_id,
        topic="Quantum Superposition",
        allow_unresearched=False,
    )

    report = pipeline.get_fact_check_report(project.metadata.project_id)
    assert report is not None
    assert report.script_version == script.version
    assert len(report.assessments) > 0

    for assessment in report.assessments:
        assert assessment.status in ("supported", "partially_supported", "conflicting", "unsupported", "not_checked", "contradicted")
        assert assessment.risk_level in ("high", "normal", "creative_flexibility")
        assert len(assessment.explanation) > 0


# ---------------------------------------------------------------------------
# 6. Failure Handling (Total and Partial)
# ---------------------------------------------------------------------------

def test_total_failure_behavior(clean_pipeline_env):
    """When search returns zero results, research run fails and script cannot ground."""
    client: TestClient = clean_pipeline_env["client"]
    project_service: ProjectService = clean_pipeline_env["project_service"]
    mock_search: MockSearchTool = clean_pipeline_env["mock_search"]

    # Configure search tool to return 0 results
    mock_search._canned_results = []

    project = project_service.create_project(title="Empty Search", project_id="proj_fail_01")
    res = client.post(f"/api/v1/projects/{project.metadata.project_id}/research/start", json={"topic": "Obscure Topic"})
    assert res.status_code == 201
    data = res.json()

    assert data["status"] == "failed"
    assert "failed_no_sources_found" in (data["termination_reason"] or "")
    assert data["evidence_count"] == 0


def test_partial_coverage_behavior(clean_pipeline_env):
    """When queries skip due to budget ceilings, run completes with partial coverage reason."""
    pipeline: PipelineOrchestrator = clean_pipeline_env["pipeline"]
    project_service: ProjectService = clean_pipeline_env["project_service"]

    project = project_service.create_project(title="Budget Test", project_id="proj_partial_01")
    tight_budget = ResearchBudgetConstraints(
        max_search_calls=1,
        max_extract_calls=1,
    )
    resp = pipeline.start_research(
        project_id=project.metadata.project_id,
        topic="Quantum Computing",
        budget_limits=tight_budget,
    )

    assert resp.status == "completed"
    overview = resp.overview
    assert overview.has_research is True
    # Queries include at least one completed/succeeded and potentially skipped
    statuses = [q.status for q in overview.queries]
    assert "completed" in statuses or "succeeded" in statuses


# ---------------------------------------------------------------------------
# 7. Production Mock-Fallback Prohibition
# ---------------------------------------------------------------------------

def test_production_mode_missing_credentials_fails_without_fallback(tmp_path: Path):
    """In production runtime (use_mocks=False), starting research without API credentials
    raises MissingCredentialsError (HTTP 400) and NEVER silently falls back to mocks.
    """
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        PROJECTS_DIR=projects_dir,
        TAVILY_API_KEY=None,
        FIRECRAWL_API_KEY=None,
        LLM_PLAN_PROVIDER="mock",
    )
    project_service = ProjectService(projects_root=projects_dir)
    pipeline = PipelineOrchestrator.create_production(
        project_service=project_service,
        settings=settings,
        use_mocks=False,
    )

    app = create_app()
    app.dependency_overrides[get_project_service] = lambda: project_service
    app.dependency_overrides[get_orchestrator] = lambda: pipeline

    with TestClient(app) as client:
        project = project_service.create_project(title="No Credentials", project_id="proj_no_cred_01")
        res = client.post(f"/api/v1/projects/{project.metadata.project_id}/research/start", json={"topic": "Quantum"})

        # Must fail with HTTP 400 Bad Request due to MissingCredentialsError
        assert res.status_code == 400
        data = res.json()
        assert "ANIM_TAVILY_API_KEY" in data["detail"] or "Tavily" in data["detail"]


# ---------------------------------------------------------------------------
# 8. Conversational Investigate Challenge Regression
# ---------------------------------------------------------------------------

def test_investigate_challenge_regression(clean_pipeline_env):
    """Conversational fact challenge with action='investigate' executes fresh research and increments version."""
    client: TestClient = clean_pipeline_env["client"]
    pipeline: PipelineOrchestrator = clean_pipeline_env["pipeline"]
    project_service: ProjectService = clean_pipeline_env["project_service"]

    project = project_service.create_project(title="Challenge Reg", project_id="proj_chal_reg_01")
    script = pipeline.start_script(
        project_id=project.metadata.project_id,
        topic="Quantum Superposition and Qubits",
        allow_unresearched=False,
    )
    assert script.version == 1
    assert len(script.claims) > 0

    target_claim = script.claims[0]
    payload = {
        "claim_id": target_claim.claim_id,
        "action": "investigate",
        "instruction": "Verify laboratory experimental confirmation of superposition.",
    }
    res = client.post(f"/api/v1/projects/{project.metadata.project_id}/script/challenge", json=payload)
    assert res.status_code == 200
    data = res.json()

    assert data["script"]["version"] == 2
    assert data["challenge_summary"]["action"] == "investigate"
    assert data["challenge_summary"]["investigation_performed"] is True


# ---------------------------------------------------------------------------
# 9. Zero-Evidence Guard Tests
# ---------------------------------------------------------------------------

def test_completed_run_with_zero_evidence_not_usable_triggers_auto_research(clean_pipeline_env):
    """A completed run with zero evidence is not treated as usable research;
    start_script automatically triggers fresh research to ground the script.
    """
    pipeline: PipelineOrchestrator = clean_pipeline_env["pipeline"]
    project_service: ProjectService = clean_pipeline_env["project_service"]

    project = project_service.create_project(title="Zero Evidence Test", project_id="proj_zero_evi_01")
    pid = project.metadata.project_id

    # Manually create a plan and a completed run with 0 evidence
    plan = ResearchPlan(
        plan_id="rplan_test_01",
        project_id=pid,
        topic="Quantum Computing",
        research_objective="Understand superposition",
        research_questions=["What is superposition?"],
        approved_tool_categories=["web_search", "web_extract"],
        budget_limits=ResearchBudgetConstraints(),
    )
    pipeline.research_storage.save_plan(plan)
    empty_run = pipeline.research_storage.create_run(pid, plan.plan_id)
    pipeline.research_storage.transition_run_status(pid, empty_run.run_id, to_status="running")
    pipeline.research_storage.transition_run_status(
        pid, empty_run.run_id, to_status="completed", termination_reason="completed_manually_without_evidence"
    )

    # Verify no evidence exists for this empty run
    assert len(pipeline.research_storage.list_evidence(pid, run_id=empty_run.run_id)) == 0

    # Start script with auto_research=True. It must recognize empty_run is unusable and trigger fresh research
    script = pipeline.start_script(
        project_id=pid,
        topic="Quantum Computing",
        auto_research=True,
    )

    assert script.research_run_id is not None
    assert script.research_run_id != empty_run.run_id

    # The new run must contain valid evidence records
    new_evidence = pipeline.research_storage.list_evidence(pid, run_id=script.research_run_id)
    assert len(new_evidence) > 0


def test_auto_research_returning_zero_evidence_stops_with_pipeline_error(clean_pipeline_env):
    """If automated research produces zero evidence, script generation stops immediately
    with PipelineError and does not repeatedly retry. The empty run record is preserved on disk.
    """
    pipeline: PipelineOrchestrator = clean_pipeline_env["pipeline"]
    project_service: ProjectService = clean_pipeline_env["project_service"]

    project = project_service.create_project(title="Empty Auto Test", project_id="proj_empty_auto_01")
    pid = project.metadata.project_id

    # Configure search tool to return 0 results
    empty_search = MockSearchTool(canned_results={})
    registry = ToolRegistry()
    registry.register(empty_search)
    registry.register(clean_pipeline_env["mock_extract"])
    empty_orchestrator = ResearchOrchestrator(
        storage_service=pipeline.research_storage,
        tool_registry=registry,
        settings=pipeline.settings,
    )
    pipeline.research_orchestrator = empty_orchestrator

    with pytest.raises(PipelineError) as exc_info:
        pipeline.start_script(
            project_id=pid,
            topic="Obscure Topic Yielding Zero Results",
            auto_research=True,
        )

    err_msg = str(exc_info.value)
    assert "0 evidence records" in err_msg
    assert "Cannot ground script without retrieved evidence" in err_msg

    # Verify that the empty run is preserved on disk with metadata
    runs = pipeline.research_storage.list_runs(pid)
    assert len(runs) == 1
    failed_run = runs[0]
    assert failed_run.status == "failed"
    assert "no_sources" in failed_run.termination_reason or "no_evidence" in failed_run.termination_reason or "queries_errored" in failed_run.termination_reason


def test_completed_run_with_evidence_remains_eligible(clean_pipeline_env):
    """A completed run with evidence is directly eligible and reused by start_script without re-researching."""
    pipeline: PipelineOrchestrator = clean_pipeline_env["pipeline"]
    project_service: ProjectService = clean_pipeline_env["project_service"]

    project = project_service.create_project(title="Eligible Run Test", project_id="proj_eligible_01")
    pid = project.metadata.project_id

    # 1. Run research explicitly
    res_resp = pipeline.start_research(
        project_id=pid,
        topic="Quantum Computing",
    )
    assert res_resp.status == "completed"
    assert res_resp.evidence_count > 0

    # 2. Start script
    script = pipeline.start_script(
        project_id=pid,
        topic="Quantum Computing",
        auto_research=True,
    )

    # Assert script grounds on the existing completed run
    assert script.research_run_id == res_resp.run_id
    runs = pipeline.research_storage.list_runs(pid)
    assert len(runs) == 1


def test_outliner_rejects_completed_empty_run(clean_pipeline_env):
    """Outliner rejects a completed research run if it contains zero evidence records scoped to its run_id."""
    pipeline: PipelineOrchestrator = clean_pipeline_env["pipeline"]
    project_service: ProjectService = clean_pipeline_env["project_service"]
    outliner = pipeline.script_service.outliner

    project = project_service.create_project(title="Outliner Empty Test", project_id="proj_outliner_empty_01")
    pid = project.metadata.project_id

    plan = ResearchPlan(
        plan_id="rplan_test_outliner_01",
        project_id=pid,
        topic="Quantum Computing",
        research_objective="Understand superposition",
        research_questions=["What is superposition?"],
        approved_tool_categories=["web_search", "web_extract"],
        budget_limits=ResearchBudgetConstraints(),
    )
    pipeline.research_storage.save_plan(plan)
    empty_run = pipeline.research_storage.create_run(pid, plan.plan_id)
    pipeline.research_storage.transition_run_status(pid, empty_run.run_id, to_status="running")
    pipeline.research_storage.transition_run_status(
        pid, empty_run.run_id, to_status="completed", termination_reason="completed_manually_without_evidence"
    )

    # 1. Explicit run rejection
    with pytest.raises(ScriptOutlinerRunError) as exc_explicit:
        outliner.generate_outline(
            OutlinerInput(
                project_id=pid,
                topic="Quantum Computing",
                research_run_id=empty_run.run_id,
                allow_unresearched=False,
            )
        )
    assert "0 evidence records scoped to its run ID" in str(exc_explicit.value)

    # 2. Newest run rejection (when no explicit run_id provided)
    with pytest.raises(ScriptOutlinerRunError) as exc_newest:
        outliner.generate_outline(
            OutlinerInput(
                project_id=pid,
                topic="Quantum Computing",
                allow_unresearched=False,
            )
        )
    assert "0 evidence records scoped to its run ID" in str(exc_newest.value)


def test_earlier_run_evidence_cannot_make_empty_current_run_appear_usable(clean_pipeline_env):
    """Evidence from an earlier run cannot make an empty subsequent run appear usable;
    cross-run isolation strictly prevents leakage.
    """
    pipeline: PipelineOrchestrator = clean_pipeline_env["pipeline"]
    project_service: ProjectService = clean_pipeline_env["project_service"]
    outliner = pipeline.script_service.outliner

    project = project_service.create_project(title="Isolation Test", project_id="proj_iso_01")
    pid = project.metadata.project_id

    # Run 1: Completed with evidence
    res1 = pipeline.start_research(
        project_id=pid,
        topic="Quantum Computing",
    )
    assert res1.status == "completed"
    assert res1.evidence_count > 0

    # Run 2: Completed but with 0 evidence
    plan2 = ResearchPlan(
        plan_id="rplan_test_iso_02",
        project_id=pid,
        topic="Quantum Computing Part 2",
        research_objective="Understand superposition 2",
        research_questions=["What is superposition 2?"],
        approved_tool_categories=["web_search", "web_extract"],
        budget_limits=ResearchBudgetConstraints(),
    )
    pipeline.research_storage.save_plan(plan2)
    empty_run2 = pipeline.research_storage.create_run(pid, plan2.plan_id)
    pipeline.research_storage.transition_run_status(pid, empty_run2.run_id, to_status="running")
    pipeline.research_storage.transition_run_status(
        pid, empty_run2.run_id, to_status="completed", termination_reason="empty_run_2"
    )

    # Outliner targeting Run 2 must reject it even though Run 1's evidence is in the project's evidence store
    with pytest.raises(ScriptOutlinerRunError) as exc_info:
        outliner.generate_outline(
            OutlinerInput(
                project_id=pid,
                topic="Quantum Computing",
                research_run_id=empty_run2.run_id,
                allow_unresearched=False,
            )
        )
    assert "0 evidence records scoped to its run ID" in str(exc_info.value)

