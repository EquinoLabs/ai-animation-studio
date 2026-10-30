"""Unit and integration tests for ScriptOutliner and evidence-aware outline pipeline."""

from pathlib import Path
import pytest
import tempfile
from typing import List

from backend.app.config import Settings
from backend.app.domain.research import (
    EvidenceRecord,
    PlannedQuery,
    QueryRecord,
    ResearchPlan,
    ResearchRun,
    SourceRecord,
)
from backend.app.domain.script import (
    GeneratedOutlinePayload,
    OutlineSectionPayload,
    ScriptOutline,
)
from backend.app.llm.base import LLMResponseMalformedError, LLMUnavailableError
from backend.app.llm.mock import MockLLMProvider
from backend.app.services.project.service import ProjectService
from backend.app.services.research.evidence_brief import EvidenceBriefBuilder
from backend.app.services.research.storage import ResearchStorageService
from backend.app.services.scriptwriter.outliner import (
    OutlineEvidenceValidationError,
    OutlinerInput,
    ScriptOutliner,
    ScriptOutlinerMalformedError,
    ScriptOutlinerRunError,
    ScriptOutlinerSecurityError,
    ScriptOutlinerUnavailableError,
)
from backend.app.services.scriptwriter.policy import WritingPolicy


@pytest.fixture
def temp_workspace():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def test_settings(temp_workspace: Path):
    projects_dir = temp_workspace / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        PROJECTS_DIR=str(projects_dir),
    )


@pytest.fixture
def project_service(test_settings: Settings):
    return ProjectService(projects_root=Path(test_settings.PROJECTS_DIR))


@pytest.fixture
def storage_service(test_settings: Settings):
    return ResearchStorageService(projects_root=Path(test_settings.PROJECTS_DIR), settings=test_settings)


@pytest.fixture
def brief_builder(storage_service: ResearchStorageService, test_settings: Settings):
    return EvidenceBriefBuilder(storage_service=storage_service, settings=test_settings)


def setup_test_project_and_research(
    project_service: ProjectService,
    storage_service: ResearchStorageService,
    project_id: str = "proj_test01",
    run_status: str = "completed",
    run_id: str = "rrun_01",
    plan_id: str = "rplan_01",
    create_evidence: bool = True,
):
    """Helper to create project and populated research records."""
    proj = project_service.create_project(title="Test Project", project_id=project_id)

    plan = ResearchPlan(
        plan_id=plan_id,
        project_id=project_id,
        topic="How Does Nuclear Fusion Work",
        research_objective="Investigate nuclear fusion principles.",
        research_questions=["What is fusion?", "What are the temperature requirements?"],
        planned_queries=[
            PlannedQuery(
                query_id="qry_01",
                query_text="nuclear fusion mechanism",
                purpose="Investigate core physics",
            )
        ],
    )
    storage_service.save_plan(plan)

    run = ResearchRun(
        run_id=run_id,
        plan_id=plan_id,
        project_id=project_id,
        status=run_status,
        query_records=[
            QueryRecord(
                query_id="qry_01",
                plan_id=plan_id,
                run_id=run_id,
                query_text="nuclear fusion mechanism",
                tool_name="web_search",
                status="succeeded",
            )
        ],
    )
    storage_service.save_run(run)

    if create_evidence:
        source = SourceRecord(
            source_id="src_01",
            project_id=project_id,
            url="https://example.org/fusion",
            title="Fusion Energy Overview",
            source_type="scholarly",
        )
        storage_service.save_source(project_id, source)

        evidence = EvidenceRecord(
            evidence_id="evi_01",
            project_id=project_id,
            source_id="src_01",
            run_id=run_id,
            query_id="qry_01",
            excerpt="Nuclear fusion requires temperatures exceeding 100 million Kelvin to overcome Coulomb repulsion.",
        )
        storage_service.save_evidence(project_id, evidence)

    return proj, plan, run


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_successful_researched_outline_generation(project_service, storage_service, test_settings):
    """Verify happy path outline generation with verified evidence and brief recovery."""
    project_id = "proj_happy"
    run_id = "rrun_happy"
    setup_test_project_and_research(
        project_service, storage_service, project_id=project_id, run_id=run_id
    )

    llm = MockLLMProvider()
    outliner = ScriptOutliner(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    inp = OutlinerInput(
        project_id=project_id,
        topic="Nuclear Fusion Energy",
        target_duration_minutes=3.0,
    )

    outline = outliner.generate_outline(inp)

    assert isinstance(outline, ScriptOutline)
    assert outline.project_id == project_id
    assert outline.research_run_id == run_id
    assert outline.topic == "Nuclear Fusion Energy"
    assert outline.target_duration_minutes == 3.0
    assert len(outline.sections) == 5
    assert outline.sections[0].type == "hook"
    assert outline.sections[1].type == "setup"
    assert outline.sections[2].type == "middle"
    assert outline.sections[3].type == "climax"
    assert outline.sections[4].type == "takeaway"

    # Verify that run brief artifact was persisted
    updated_run = storage_service.get_run(project_id, run_id)
    assert "evidence_brief" in updated_run.artifacts
    assert storage_service.get_run_evidence_brief(project_id, run_id) is not None


def test_explicit_run_selection_and_cross_project_rejection(project_service, storage_service, test_settings):
    """Verify explicit run selection and rejection of foreign/cross-project runs."""
    setup_test_project_and_research(
        project_service, storage_service, project_id="proj_p1", run_id="rrun_p1"
    )
    setup_test_project_and_research(
        project_service, storage_service, project_id="proj_p2", run_id="rrun_p2"
    )

    llm = MockLLMProvider()
    outliner = ScriptOutliner(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    # Cross-project reference: proj_p1 trying to use rrun_p2
    with pytest.raises(ScriptOutlinerRunError, match="not found in project 'proj_p1'"):
        outliner.generate_outline(
            OutlinerInput(
                project_id="proj_p1",
                topic="Fusion",
                research_run_id="rrun_p2",
            )
        )

    # Valid explicit run
    outline = outliner.generate_outline(
        OutlinerInput(
            project_id="proj_p1",
            topic="Fusion",
            research_run_id="rrun_p1",
        )
    )
    assert outline.research_run_id == "rrun_p1"


def test_rejection_of_newer_incomplete_run_without_fallback(project_service, storage_service, test_settings):
    """Verify that a newer running/failed run halts the pipeline and does NOT fall back to an older completed run."""
    project_id = "proj_runs"
    # Older completed run
    _, _, old_run = setup_test_project_and_research(
        project_service, storage_service, project_id=project_id, run_id="rrun_01_old", run_status="completed"
    )

    # Newer failed run
    new_run = ResearchRun(
        run_id="rrun_02_new",
        plan_id="rplan_01",
        project_id=project_id,
        status="failed",
        termination_reason="Budget exhausted",
    )
    storage_service.save_run(new_run)

    llm = MockLLMProvider()
    outliner = ScriptOutliner(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    with pytest.raises(ScriptOutlinerRunError, match="has status 'failed'"):
        outliner.generate_outline(
            OutlinerInput(
                project_id=project_id,
                topic="Fusion",
            )
        )


def test_unresearched_mode_handling(project_service, storage_service, test_settings):
    """Verify rejection when no research exists unless allow_unresearched=True is specified."""
    project_id = "proj_no_research"
    project_service.create_project(title="No Research", project_id=project_id)

    llm = MockLLMProvider()
    outliner = ScriptOutliner(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    # Default rejects when research is absent
    with pytest.raises(ScriptOutlinerRunError, match="No research runs found"):
        outliner.generate_outline(
            OutlinerInput(
                project_id=project_id,
                topic="Creative Writing",
                allow_unresearched=False,
            )
        )

    # Allowed when explicit flag is True
    outline = outliner.generate_outline(
        OutlinerInput(
            project_id=project_id,
            topic="Creative Writing",
            allow_unresearched=True,
        )
    )
    assert outline.research_run_id is None
    assert outline.topic == "Creative Writing"
    assert len(outline.sections) == 5


def test_authoritative_metadata_assignment(project_service, storage_service, test_settings):
    """Verify that model-supplied metadata is dropped and application authoritatively assigns identifiers."""
    project_id = "proj_meta"
    setup_test_project_and_research(
        project_service, storage_service, project_id=project_id, run_id="rrun_meta"
    )

    # Model supplies bogus metadata
    bogus_payload = {
        "outline_id": "out_bogus999",
        "project_id": "proj_attacker",
        "created_at": "1999-01-01T00:00:00Z",
        "research_run_id": "rrun_fake",
        "narrative_structure": "mechanism_breakdown",
        "sections": [
            {"type": "hook", "heading": "Hook", "target_words": 50, "factual_milestones": [], "evidence_ids": []},
            {"type": "setup", "heading": "Setup", "target_words": 80, "factual_milestones": [], "evidence_ids": []},
            {"type": "middle", "heading": "Mid", "target_words": 150, "factual_milestones": [], "evidence_ids": []},
            {"type": "climax", "heading": "Climax", "target_words": 80, "factual_milestones": [], "evidence_ids": []},
            {"type": "takeaway", "heading": "Takeaway", "target_words": 50, "factual_milestones": [], "evidence_ids": []},
        ],
    }

    llm = MockLLMProvider()
    llm.set_response(bogus_payload)

    outliner = ScriptOutliner(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    outline = outliner.generate_outline(
        OutlinerInput(
            project_id=project_id,
            topic="Nuclear Physics",
        )
    )

    # Authoritative properties assigned by application
    assert outline.outline_id.startswith("out_")
    assert outline.outline_id != "out_bogus999"
    assert outline.project_id == project_id
    assert outline.research_run_id == "rrun_meta"
    assert outline.narrative_structure == "mechanism_breakdown"


def test_narrative_progression_validation_and_multi_middle_sections(project_service, storage_service, test_settings):
    """Verify multi-beat middle sections are permitted and invalid progressions are rejected."""
    project_id = "proj_progress"
    setup_test_project_and_research(
        project_service, storage_service, project_id=project_id, run_id="rrun_prog"
    )

    llm = MockLLMProvider()
    outliner = ScriptOutliner(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    # 1. Valid outline with 2 middle sections (6 sections total)
    multi_middle_payload = {
        "narrative_structure": "chronological",
        "sections": [
            {"type": "hook", "heading": "Hook", "target_words": 60, "factual_milestones": []},
            {"type": "setup", "heading": "Setup", "target_words": 80, "factual_milestones": []},
            {"type": "middle", "heading": "Middle Part 1", "target_words": 120, "factual_milestones": []},
            {"type": "middle", "heading": "Middle Part 2", "target_words": 120, "factual_milestones": []},
            {"type": "climax", "heading": "Climax", "target_words": 90, "factual_milestones": []},
            {"type": "takeaway", "heading": "Takeaway", "target_words": 60, "factual_milestones": []},
        ],
    }
    llm.set_response(multi_middle_payload)
    outline = outliner.generate_outline(OutlinerInput(project_id=project_id, topic="Multi beat test"))
    assert len(outline.sections) == 6
    assert [s.type for s in outline.sections] == ["hook", "setup", "middle", "middle", "climax", "takeaway"]

    # 2. Invalid progression: missing takeaway (ends with middle)
    bad_order_payload = {
        "narrative_structure": "chronological",
        "sections": [
            {"type": "hook", "heading": "Hook", "target_words": 60},
            {"type": "setup", "heading": "Setup", "target_words": 80},
            {"type": "middle", "heading": "Middle 1", "target_words": 120},
            {"type": "climax", "heading": "Climax", "target_words": 90},
            {"type": "middle", "heading": "Middle 2", "target_words": 60},
        ],
    }
    llm.set_response(bad_order_payload)
    with pytest.raises(ScriptOutlinerMalformedError, match="Final section must be 'takeaway'"):
        outliner.generate_outline(OutlinerInput(project_id=project_id, topic="Bad order"))

    # 3. Invalid progression: hook missing
    bad_hook_payload = {
        "narrative_structure": "chronological",
        "sections": [
            {"type": "setup", "heading": "Setup", "target_words": 60},
            {"type": "setup", "heading": "Setup 2", "target_words": 80},
            {"type": "middle", "heading": "Middle", "target_words": 120},
            {"type": "climax", "heading": "Climax", "target_words": 90},
            {"type": "takeaway", "heading": "Takeaway", "target_words": 60},
        ],
    }
    llm.set_response(bad_hook_payload)
    with pytest.raises(ScriptOutlinerMalformedError, match="First section must be 'hook'"):
        outliner.generate_outline(OutlinerInput(project_id=project_id, topic="Bad hook"))


def test_evidence_id_validation_and_successful_corrective_retry(project_service, storage_service, test_settings):
    """Verify that unknown evidence IDs trigger a bounded corrective retry and succeed when corrected."""
    project_id = "proj_retry"
    setup_test_project_and_research(
        project_service, storage_service, project_id=project_id, run_id="rrun_retry"
    )

    # Initial response cites invalid evidence ID 'evi_hallucinated'
    first_response = {
        "narrative_structure": "chronological",
        "sections": [
            {"type": "hook", "heading": "Hook", "target_words": 50, "evidence_ids": ["evi_hallucinated"]},
            {"type": "setup", "heading": "Setup", "target_words": 80, "evidence_ids": []},
            {"type": "middle", "heading": "Middle", "target_words": 120, "evidence_ids": ["evi_01"]},
            {"type": "climax", "heading": "Climax", "target_words": 80, "evidence_ids": []},
            {"type": "takeaway", "heading": "Takeaway", "target_words": 50, "evidence_ids": []},
        ],
    }
    # Corrected response on retry
    corrected_response = {
        "narrative_structure": "chronological",
        "sections": [
            {"type": "hook", "heading": "Hook", "target_words": 50, "evidence_ids": []},
            {"type": "setup", "heading": "Setup", "target_words": 80, "evidence_ids": []},
            {"type": "middle", "heading": "Middle", "target_words": 120, "evidence_ids": ["evi_01"]},
            {"type": "climax", "heading": "Climax", "target_words": 80, "evidence_ids": []},
            {"type": "takeaway", "heading": "Takeaway", "target_words": 50, "evidence_ids": []},
        ],
    }

    llm = MockLLMProvider()
    llm.set_response(first_response)
    # Configure next call queue for retry
    llm._response_queue = [first_response, corrected_response]

    outliner = ScriptOutliner(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    outline = outliner.generate_outline(OutlinerInput(project_id=project_id, topic="Retry test"))
    assert outline.sections[0].evidence_ids == []
    assert outline.sections[2].evidence_ids == ["evi_01"]


def test_evidence_id_validation_retry_exhaustion_raises(project_service, storage_service, test_settings):
    """Verify that persisting in citing unknown evidence IDs after retry raises OutlineEvidenceValidationError."""
    project_id = "proj_exhaust"
    setup_test_project_and_research(
        project_service, storage_service, project_id=project_id, run_id="rrun_exhaust"
    )

    stubborn_response = {
        "narrative_structure": "chronological",
        "sections": [
            {"type": "hook", "heading": "Hook", "target_words": 50, "evidence_ids": ["evi_hallucinated"]},
            {"type": "setup", "heading": "Setup", "target_words": 80, "evidence_ids": []},
            {"type": "middle", "heading": "Middle", "target_words": 120, "evidence_ids": []},
            {"type": "climax", "heading": "Climax", "target_words": 80, "evidence_ids": []},
            {"type": "takeaway", "heading": "Takeaway", "target_words": 50, "evidence_ids": []},
        ],
    }

    llm = MockLLMProvider()
    llm._response_queue = [stubborn_response, stubborn_response]

    outliner = ScriptOutliner(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    with pytest.raises(OutlineEvidenceValidationError, match="evi_hallucinated"):
        outliner.generate_outline(OutlinerInput(project_id=project_id, topic="Exhaust test"))


def test_provider_failure_and_malformed_output(project_service, storage_service, test_settings):
    """Verify typed error handling for provider failure and malformed JSON."""
    project_id = "proj_fail"
    setup_test_project_and_research(
        project_service, storage_service, project_id=project_id, run_id="rrun_fail"
    )

    llm = MockLLMProvider()
    outliner = ScriptOutliner(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    # Provider unavailable
    llm.set_error(LLMUnavailableError("API rate limit"))
    with pytest.raises(ScriptOutlinerUnavailableError, match="LLM provider unavailable"):
        outliner.generate_outline(OutlinerInput(project_id=project_id, topic="Unavailable"))

    # Malformed output
    llm.set_error(LLMResponseMalformedError("Unclosed JSON"))
    with pytest.raises(ScriptOutlinerMalformedError, match="LLM generated malformed outline"):
        outliner.generate_outline(OutlinerInput(project_id=project_id, topic="Malformed"))


def test_dangling_source_reference_raises_run_error(project_service, storage_service, test_settings):
    """Verify that an evidence record referencing a nonexistent source raises ScriptOutlinerRunError."""
    import json

    project_id = "proj_dangling"
    setup_test_project_and_research(
        project_service, storage_service, project_id=project_id, run_id="rrun_dangling"
    )

    # Directly inject an evidence record with a non-existent source_id into evidence.json
    # (bypassing save_evidence which validates source existence at write time)
    research_dir = storage_service._resolve_research_dir(project_id)
    evidence_file = research_dir / "evidence" / "evidence.json"
    evidence_map = json.loads(evidence_file.read_text(encoding="utf-8"))

    dangling_ev = EvidenceRecord(
        evidence_id="evi_dangling",
        project_id=project_id,
        source_id="src_nonexistent",
        run_id="rrun_dangling",
        query_id="qry_01",
        excerpt="Valid excerpt text but references nonexistent source.",
    )
    evidence_map["evi_dangling"] = json.loads(dangling_ev.model_dump_json())
    storage_service._atomic_write_json(evidence_file, evidence_map)

    llm = MockLLMProvider()
    outliner = ScriptOutliner(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    with pytest.raises(ScriptOutlinerRunError, match="references unknown source 'src_nonexistent'"):
        outliner.generate_outline(OutlinerInput(project_id=project_id, topic="Dangling source test"))


def test_deleted_run_scoped_brief_recovery(project_service, storage_service, test_settings, brief_builder):
    """Verify that if an existing run-scoped brief is deleted from disk, the outliner reconstructs it."""
    project_id = "proj_recover"
    run_id = "rrun_recover"
    setup_test_project_and_research(
        project_service, storage_service, project_id=project_id, run_id=run_id
    )

    # 1. Create and persist initial run-scoped brief
    brief_filename = f"runs/{run_id}/evidence_brief.md"
    brief_path, content = brief_builder.build_and_save_brief(
        project_id=project_id,
        run_id=run_id,
        filename=brief_filename,
    )
    assert brief_path.is_file()
    assert brief_path.read_text(encoding="utf-8").strip() != ""

    # Verify run.artifacts points to the brief
    run = storage_service.get_run(project_id, run_id)
    assert run.artifacts.get("evidence_brief") == brief_filename

    # 2. Delete the brief file from disk
    brief_path.unlink()
    assert not brief_path.exists()
    assert storage_service.get_run_evidence_brief(project_id, run_id) is None

    # 3. Trigger outliner - normal recovery path
    llm = MockLLMProvider()
    outliner = ScriptOutliner(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        brief_builder=brief_builder,
        settings=test_settings,
    )

    outline = outliner.generate_outline(
        OutlinerInput(
            project_id=project_id,
            topic="Nuclear Fusion Recovery",
            target_duration_minutes=3.0,
        )
    )

    # 4. Assert outline generation succeeded
    assert outline.research_run_id == run_id

    # 5. Reload the ResearchRun from disk and verify artifact association remains correct
    reloaded_run = storage_service.get_run(project_id, run_id)
    assert reloaded_run.artifacts.get("evidence_brief") == brief_filename

    # 6. Verify the reconstructed brief file exists and can be read with full content
    recovered_content = storage_service.get_run_evidence_brief(project_id, run_id)
    assert recovered_content is not None
    assert "Nuclear fusion requires temperatures exceeding 100 million Kelvin" in recovered_content
    assert brief_path.is_file()

