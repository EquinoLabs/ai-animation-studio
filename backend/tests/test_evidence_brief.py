"""Deterministic offline tests for the Evidence Brief Builder (Phase 3)."""

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
from backend.app.services.research.evidence_brief import (
    EvidenceBriefBuilder,
    EvidenceBriefError,
)
from backend.app.services.research.orchestrator import ResearchOrchestrator
from backend.app.services.research.storage import ResearchStorageService
from backend.app.services.research.tools.base import (
    ResearchProviderError,
    SearchResultItem,
    ToolRegistry,
)
from backend.app.services.research.tools.mock import MockExtractTool, MockSearchTool


@pytest.fixture
def brief_env(tmp_path: Path):
    """Fixture providing an isolated project workspace for evidence brief tests."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(PROJECTS_DIR=str(projects_dir))

    proj_service = ProjectService(projects_root=projects_dir)
    proj = proj_service.create_project(title="Brief Test Project", project_id="proj_brief_01")
    project_id = proj.metadata.project_id

    storage = ResearchStorageService(projects_root=projects_dir, settings=settings)

    registry = ToolRegistry()
    search_tool = MockSearchTool(name="tavily_search")
    extract_tool = MockExtractTool(name="firecrawl_extract")
    registry.register(search_tool)
    registry.register(extract_tool)

    return {
        "projects_dir": projects_dir,
        "project_id": project_id,
        "storage": storage,
        "registry": registry,
        "search_tool": search_tool,
        "extract_tool": extract_tool,
        "settings": settings,
    }


def create_test_plan(
    storage: ResearchStorageService,
    project_id: str,
    plan_id: str = "rplan_brief",
    queries: int = 2,
    max_search_calls: int = 3,
    max_extract_calls: int = 3,
) -> ResearchPlan:
    """Helper to persist a structured test plan."""
    planned_queries = [
        PlannedQuery(
            query_id=f"qry_{i:02d}",
            query_text=f"Historical evidence query {i}",
            purpose=f"Investigate core mechanism {i}",
            target_question=f"What was mechanism {i} of early currency development?",
            tool_category="web_search",
        )
        for i in range(1, queries + 1)
    ]

    plan = ResearchPlan(
        plan_id=plan_id,
        project_id=project_id,
        topic="Evolution of Currency",
        research_objective="Investigate transitions from barter to electrum coinage",
        research_questions=[q.target_question for q in planned_queries],
        planned_queries=planned_queries,
        approved_tool_categories=["web_search", "web_extract"],
        budget_limits=ResearchBudgetConstraints(
            max_search_calls=max_search_calls,
            max_extract_calls=max_extract_calls,
        ),
        schema_version="1.0.0",
    )
    storage.save_plan(plan)
    return plan


# ---------------------------------------------------------------------------
# Test 1: Full Research Run Brief Generation & Artifact Reference
# ---------------------------------------------------------------------------

def test_evidence_brief_full_successful_run(brief_env):
    """Verify evidence_brief.md is automatically compiled and linked for a full successful run."""
    plan = create_test_plan(brief_env["storage"], brief_env["project_id"], queries=2)
    orchestrator = ResearchOrchestrator(
        tool_registry=brief_env["registry"],
        storage_service=brief_env["storage"],
        settings=brief_env["settings"],
    )

    run = orchestrator.execute_plan(brief_env["project_id"], plan.plan_id)
    assert run.status == "completed"

    # 1. Verify artifact reference in ResearchRun
    assert "evidence_brief" in run.artifacts
    assert run.artifacts["evidence_brief"] == "evidence_brief.md"

    # 2. Verify file exists on disk
    brief_text = brief_env["storage"].get_evidence_brief(brief_env["project_id"])
    assert brief_text is not None
    assert len(brief_text) > 200

    # 3. Verify sections and provenance
    assert "# Evidence Brief: Evolution of Currency" in brief_text
    assert f"**Project ID:** `{brief_env['project_id']}`" in brief_text
    assert f"**Run ID:** `{run.run_id}`" in brief_text
    assert "**Coverage Assessment:** Full coverage:" in brief_text
    assert "## 1. Research Objectives & Execution Overview" in brief_text
    assert "## 2. Research Questions & Retrieved Evidence" in brief_text
    assert "## 3. Evidence Consistency & Cross-Source Analysis" in brief_text
    assert "## 4. Unresolved Questions & Research Gaps" in brief_text
    assert "## 5. Methodological Limitations & Epistemic Boundaries" in brief_text
    assert "Retrieved Does Not Equal Verified" in brief_text

    # 4. Verify research questions and verbatim excerpts are present
    assert "What was mechanism 1 of early currency development?" in brief_text
    assert "What was mechanism 2 of early currency development?" in brief_text
    assert "— *Source:*" in brief_text
    assert "— *Locator:*" in brief_text
    assert "— *Inquiry Target:*" in brief_text


# ---------------------------------------------------------------------------
# Test 2: Partial Run Brief with Skipped and Failed Queries
# ---------------------------------------------------------------------------

def test_evidence_brief_partial_run_with_skipped_queries(brief_env):
    """Verify evidence brief honestly reports partial coverage, skipped queries, and resulting gaps."""
    # 3 planned queries, max_search_calls=2 => query 3 skipped
    plan = create_test_plan(
        brief_env["storage"],
        brief_env["project_id"],
        queries=3,
        max_search_calls=2,
    )

    orchestrator = ResearchOrchestrator(
        tool_registry=brief_env["registry"],
        storage_service=brief_env["storage"],
        settings=brief_env["settings"],
    )

    run = orchestrator.execute_plan(brief_env["project_id"], plan.plan_id)
    assert run.status == "completed"
    assert "partial_coverage" in (run.termination_reason or "")

    brief_text = brief_env["storage"].get_evidence_brief(brief_env["project_id"])
    assert brief_text is not None

    # Coverage assessment reflects partial execution
    assert "Partial coverage: 2 of 3 queries executed" in brief_text

    # Section 4 captures skipped queries and gaps
    assert "## 4. Unresolved Questions & Research Gaps" in brief_text
    assert "- **Skipped Queries (Budget / Deadline Ceilings):**" in brief_text
    assert "`qry_03`:" in brief_text
    assert "budget limit reached" in brief_text

    # Question 3 shows no verified evidence
    assert "### Question 3:" in brief_text
    assert "*No verified evidence was extracted for this question.*" in brief_text


# ---------------------------------------------------------------------------
# Test 3: Failed Run with Zero Evidence
# ---------------------------------------------------------------------------

def test_evidence_brief_failed_run_with_zero_evidence(brief_env):
    """Verify evidence brief compiles accurately for a failed run, documenting zero evidence and failures."""
    # Configure search tool to fail
    def failing_search(args, session):
        session.consume_search_budget()
        raise ResearchProviderError("External search gateway outage 503")

    mock_search = MockSearchTool(name="tavily_search")
    mock_search._run = failing_search

    registry = ToolRegistry()
    registry.register(mock_search)
    registry.register(brief_env["extract_tool"])

    plan = create_test_plan(brief_env["storage"], brief_env["project_id"], queries=2)
    orchestrator = ResearchOrchestrator(
        tool_registry=registry,
        storage_service=brief_env["storage"],
        settings=brief_env["settings"],
    )

    run = orchestrator.execute_plan(brief_env["project_id"], plan.plan_id)
    assert run.status == "failed"

    brief_text = brief_env["storage"].get_evidence_brief(brief_env["project_id"])
    assert brief_text is not None

    assert "**Execution Status:** `failed`" in brief_text
    assert "failed_all_queries_errored" in brief_text
    assert "- **Failed Queries (Provider / Network Errors):**" in brief_text
    assert "External search gateway outage 503" in brief_text
    assert "Zero evidence excerpts retrieved" in brief_text


# ---------------------------------------------------------------------------
# Test 4: Provenance and Verbatim Excerpt Integrity
# ---------------------------------------------------------------------------

def test_evidence_brief_preserves_provenance_without_fabrication(brief_env):
    """Verify all citations in the brief correspond to actual persisted SourceRecords and content files."""
    plan = create_test_plan(brief_env["storage"], brief_env["project_id"], queries=1)
    orchestrator = ResearchOrchestrator(
        tool_registry=brief_env["registry"],
        storage_service=brief_env["storage"],
        settings=brief_env["settings"],
    )

    run = orchestrator.execute_plan(brief_env["project_id"], plan.plan_id)
    assert run.status == "completed"

    brief_text = brief_env["storage"].get_evidence_brief(brief_env["project_id"])
    sources = brief_env["storage"].list_sources(brief_env["project_id"])
    evidence = brief_env["storage"].list_evidence(brief_env["project_id"])

    # Ensure every cited source ID exists
    for s in sources:
        assert f"`{s.source_id}`" in brief_text
        assert s.url in brief_text

    # Ensure every verbatim excerpt in evidence is present in brief
    for e in evidence:
        assert e.excerpt in brief_text
        assert e.locator in brief_text


# ---------------------------------------------------------------------------
# Test 5: Conflict and Consistency Analysis Handled Honestly
# ---------------------------------------------------------------------------

def test_evidence_brief_consistency_analysis_honesty(brief_env):
    """Verify Section 3 explicitly notes when available evidence is insufficient to assert consensus or conflict."""
    plan = create_test_plan(brief_env["storage"], brief_env["project_id"], queries=2)
    orchestrator = ResearchOrchestrator(
        tool_registry=brief_env["registry"],
        storage_service=brief_env["storage"],
        settings=brief_env["settings"],
    )

    run = orchestrator.execute_plan(brief_env["project_id"], plan.plan_id)
    assert run.status == "completed"

    brief_text = brief_env["storage"].get_evidence_brief(brief_env["project_id"])
    assert "## 3. Evidence Consistency & Cross-Source Analysis" in brief_text
    assert "insufficient to determine cross-source contradictions or historical consensus" in brief_text


# ---------------------------------------------------------------------------
# Test 6: Standalone Regeneration from Persisted Data
# ---------------------------------------------------------------------------

def test_evidence_brief_standalone_regeneration(brief_env):
    """Verify evidence_brief.md can be regenerated from persisted storage after file deletion."""
    plan = create_test_plan(brief_env["storage"], brief_env["project_id"], queries=1)
    orchestrator = ResearchOrchestrator(
        tool_registry=brief_env["registry"],
        storage_service=brief_env["storage"],
        settings=brief_env["settings"],
    )

    run = orchestrator.execute_plan(brief_env["project_id"], plan.plan_id)
    assert run.status == "completed"

    brief_file = (
        brief_env["projects_dir"]
        / brief_env["project_id"]
        / "research"
        / "evidence_brief.md"
    )
    assert brief_file.exists()
    original_text = brief_file.read_text(encoding="utf-8")

    # Delete the artifact
    brief_file.unlink()
    assert not brief_file.exists()

    # Re-instantiate a fresh builder with no in-memory state
    fresh_storage = ResearchStorageService(
        projects_root=brief_env["projects_dir"],
        settings=brief_env["settings"],
    )
    builder = EvidenceBriefBuilder(storage_service=fresh_storage, settings=brief_env["settings"])

    # Regenerate from disk
    new_path, regenerated_text = builder.build_and_save_brief(brief_env["project_id"], run.run_id)
    assert new_path.exists()
    assert new_path.name == "evidence_brief.md"

    # Content matches except for generation timestamp
    assert "# Evidence Brief: Evolution of Currency" in regenerated_text
    assert f"**Run ID:** `{run.run_id}`" in regenerated_text


# ---------------------------------------------------------------------------
# Test 7: Persistence Failure Handling
# ---------------------------------------------------------------------------

def test_evidence_brief_persistence_failure_raises(brief_env, monkeypatch):
    """Verify write failure during brief saving raises EvidenceBriefError without corrupting storage."""
    plan = create_test_plan(brief_env["storage"], brief_env["project_id"], queries=1)
    orchestrator = ResearchOrchestrator(
        tool_registry=brief_env["registry"],
        storage_service=brief_env["storage"],
        settings=brief_env["settings"],
    )

    # Monkeypatch save_evidence_brief to fail
    def failing_save_brief(project_id, markdown_content, filename="evidence_brief.md"):
        raise OSError("Disk out of space: simulated ENOSPC")

    monkeypatch.setattr(brief_env["storage"], "save_evidence_brief", failing_save_brief)

    builder = EvidenceBriefBuilder(storage_service=brief_env["storage"], settings=brief_env["settings"])

    with pytest.raises(EvidenceBriefError) as exc_info:
        # Create a pending run manually to test builder standalone
        run = brief_env["storage"].create_run(brief_env["project_id"], plan.plan_id)
        builder.build_and_save_brief(brief_env["project_id"], run.run_id)

    assert "Failed to persist evidence brief" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 8: Project Isolation and Path Safety
# ---------------------------------------------------------------------------

def test_evidence_brief_project_isolation(brief_env):
    """Verify attempting to build a brief with path traversal raises EvidenceBriefError."""
    builder = EvidenceBriefBuilder(storage_service=brief_env["storage"], settings=brief_env["settings"])

    with pytest.raises(EvidenceBriefError) as exc_info:
        builder.build_brief_markdown("../traversal_proj", "rrun_fake")

    assert "Security error" in str(exc_info.value)
