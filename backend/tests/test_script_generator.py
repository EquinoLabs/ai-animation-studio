"""Unit and integration tests for ScriptDraftGenerator (Phase 4, P4-S4)."""

import json
from pathlib import Path
import pytest
import tempfile
from typing import List, Optional

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
    Script,
    ScriptOutline,
    ScriptOutlineSection,
)
from backend.app.llm.base import LLMResponseMalformedError, LLMUnavailableError
from backend.app.llm.mock import MockLLMProvider
from backend.app.services.project.service import ProjectService
from backend.app.services.research.evidence_brief import EvidenceBriefBuilder
from backend.app.services.research.storage import ResearchStorageService
from backend.app.services.scriptwriter.generator import (
    DraftGeneratorInput,
    ScriptDraftEvidenceValidationError,
    ScriptDraftGenerator,
    ScriptDraftMalformedError,
    ScriptDraftRunError,
    ScriptDraftSecurityError,
    ScriptDraftUnavailableError,
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


def create_test_outline(
    project_id: str = "proj_test01",
    run_id: Optional[str] = "rrun_01",
    sections_count: int = 5,
) -> ScriptOutline:
    """Helper to create a structured outline."""
    if sections_count == 5:
        types = ["hook", "setup", "middle", "climax", "takeaway"]
    elif sections_count == 6:
        types = ["hook", "setup", "middle", "middle", "climax", "takeaway"]
    else:
        types = ["hook", "setup"] + ["middle"] * (sections_count - 4) + ["climax", "takeaway"]

    sections = [
        ScriptOutlineSection(
            type=t,
            heading=f"Heading {i+1} for {t.capitalize()}",
            target_words=100,
            factual_milestones=[f"Milestone {i+1}"],
            evidence_ids=["evi_01"] if t == "middle" else [],
        )
        for i, t in enumerate(types)
    ]

    return ScriptOutline(
        outline_id="out_test01",
        project_id=project_id,
        topic="Nuclear Fusion Physics",
        target_duration_minutes=3.0,
        narrative_structure="mechanism_breakdown",
        sections=sections,
        research_run_id=run_id,
    )


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

def test_successful_researched_draft_generation(project_service, storage_service, test_settings):
    """Verify happy path script draft generation grounded in research outline and evidence."""
    project_id = "proj_draft_happy"
    run_id = "rrun_draft_happy"
    setup_test_project_and_research(
        project_service, storage_service, project_id=project_id, run_id=run_id
    )

    outline = create_test_outline(project_id=project_id, run_id=run_id, sections_count=5)

    llm = MockLLMProvider()
    generator = ScriptDraftGenerator(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    inp = DraftGeneratorInput(
        project_id=project_id,
        outline=outline,
    )

    script = generator.generate_draft(inp)

    assert isinstance(script, Script)
    assert script.project_id == project_id
    assert script.research_run_id == run_id
    assert script.status == "draft"
    assert script.outline == outline
    assert len(script.sections) == 5

    # Check outline_section_index traceability
    for idx, sec in enumerate(script.sections):
        assert sec.outline_section_index == idx
        assert sec.id == f"sec_{idx+1:02d}"

    # Verify claim bidirectional linkage and authoritative claim_id assignment
    assert len(script.claims) > 0
    for c in script.claims:
        assert c.claim_id.startswith("clm_")
        assert c.section_id in [s.id for s in script.sections]
        assert c.link_status == "linked"
        assert "src_01" in c.source_ids  # Authoritatively derived from evidence catalog

    # Check duration metrics
    assert script.total_word_count > 0
    assert script.estimated_duration_seconds > 0.0


def test_variable_outline_length_multi_middle_sections(project_service, storage_service, test_settings):
    """Verify that an outline with 6 sections (2 middle beats) produces a 6-section draft without forcing 5."""
    project_id = "proj_multi"
    run_id = "rrun_multi"
    setup_test_project_and_research(
        project_service, storage_service, project_id=project_id, run_id=run_id
    )

    outline = create_test_outline(project_id=project_id, run_id=run_id, sections_count=6)
    assert len(outline.sections) == 6

    llm = MockLLMProvider()
    generator = ScriptDraftGenerator(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    script = generator.generate_draft(DraftGeneratorInput(project_id=project_id, outline=outline))

    assert len(script.sections) == 6
    assert [s.type for s in script.sections] == ["hook", "setup", "middle", "middle", "climax", "takeaway"]
    for idx, s in enumerate(script.sections):
        assert s.outline_section_index == idx


def test_authoritative_claim_assignment_and_model_claim_id_override(project_service, storage_service, test_settings):
    """Verify that application authoritatively assigns canonical claim IDs and links section claim_ids."""
    project_id = "proj_claim_auth"
    run_id = "rrun_claim_auth"
    setup_test_project_and_research(
        project_service, storage_service, project_id=project_id, run_id=run_id
    )

    outline = create_test_outline(project_id=project_id, run_id=run_id, sections_count=5)

    # Model supplies bogus claim_id and tries to emit source_ids
    raw_payload = {
        "title": "Custom Documentary Title",
        "sections": [
            {"id": "sec_01", "type": "hook", "heading": "Hook", "narration": "Hook narration words words."},
            {"id": "sec_02", "type": "setup", "heading": "Setup", "narration": "Setup narration words words."},
            {"id": "sec_03", "type": "middle", "heading": "Middle", "narration": "Middle narration words words."},
            {"id": "sec_04", "type": "climax", "heading": "Climax", "narration": "Climax narration words words."},
            {"id": "sec_05", "type": "takeaway", "heading": "Takeaway", "narration": "Takeaway narration words words."},
        ],
        "claims": [
            {
                "claim_id": "clm_bogus999",
                "claim_text": "Plasma reaches critical density.",
                "section_id": "sec_03",
                "evidence_ids": ["evi_01"],
                "source_ids": ["src_bogus_attacker"],
            }
        ],
    }

    llm = MockLLMProvider()
    llm.set_response(raw_payload)

    generator = ScriptDraftGenerator(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    script = generator.generate_draft(DraftGeneratorInput(project_id=project_id, outline=outline))

    assert len(script.claims) == 1
    claim = script.claims[0]
    assert claim.claim_id == "clm_01"  # Canonical sequential ID assigned by application
    assert claim.claim_id != "clm_bogus999"
    assert claim.source_ids == ["src_01"]  # Authoritatively derived from catalog, bogus source discarded
    assert script.sections[2].claim_ids == ["clm_01"]  # Linked on target section


def test_source_integrity_failure_raises_run_error(project_service, storage_service, test_settings):
    """Verify that an evidence record referencing a missing source raises ScriptDraftRunError."""
    project_id = "proj_src_corrupt"
    run_id = "rrun_src_corrupt"
    setup_test_project_and_research(
        project_service, storage_service, project_id=project_id, run_id=run_id
    )

    # Corrupt evidence file directly to reference nonexistent source
    research_dir = storage_service._resolve_research_dir(project_id)
    evidence_file = research_dir / "evidence" / "evidence.json"
    evidence_map = json.loads(evidence_file.read_text(encoding="utf-8"))
    dangling_ev = EvidenceRecord(
        evidence_id="evi_dangling",
        project_id=project_id,
        source_id="src_missing_from_disk",
        run_id=run_id,
        query_id="qry_01",
        excerpt="Valid excerpt referencing missing source.",
    )
    evidence_map["evi_dangling"] = json.loads(dangling_ev.model_dump_json())
    storage_service._atomic_write_json(evidence_file, evidence_map)

    outline = create_test_outline(project_id=project_id, run_id=run_id, sections_count=5)

    llm = MockLLMProvider()
    generator = ScriptDraftGenerator(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    with pytest.raises(ScriptDraftRunError, match="references unknown source 'src_missing_from_disk'"):
        generator.generate_draft(DraftGeneratorInput(project_id=project_id, outline=outline))


def test_unresearched_mode_handling_and_evidence_rejection(project_service, storage_service, test_settings):
    """Verify unresearched mode authorization and rejection of model-emitted evidence IDs."""
    project_id = "proj_unres"
    project_service.create_project(title="Unresearched Project", project_id=project_id)

    # Outline with no research run
    unres_outline = create_test_outline(project_id=project_id, run_id=None, sections_count=5)

    llm = MockLLMProvider()
    generator = ScriptDraftGenerator(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    # 1. Rejected if allow_unresearched=False
    with pytest.raises(ScriptDraftRunError, match="requires a research run unless 'allow_unresearched=True'"):
        generator.generate_draft(DraftGeneratorInput(project_id=project_id, outline=unres_outline, allow_unresearched=False))

    # 2. Succeeds when allow_unresearched=True and model complies (no evidence IDs)
    script = generator.generate_draft(DraftGeneratorInput(project_id=project_id, outline=unres_outline, allow_unresearched=True))
    assert script.research_run_id is None
    assert script.evidence_brief_path is None
    for claim in script.claims:
        assert claim.evidence_ids == []
        assert claim.source_ids == []
        assert claim.link_status == "unlinked"

    # 3. Model attempts to emit evidence IDs in unresearched mode -> Rejected explicitly
    bad_unres_payload = {
        "title": "Unresearched Title",
        "sections": [
            {"id": "sec_01", "type": "hook", "heading": "Hook", "narration": "Narration text words words."},
            {"id": "sec_02", "type": "setup", "heading": "Setup", "narration": "Narration text words words."},
            {"id": "sec_03", "type": "middle", "heading": "Middle", "narration": "Narration text words words."},
            {"id": "sec_04", "type": "climax", "heading": "Climax", "narration": "Narration text words words."},
            {"id": "sec_05", "type": "takeaway", "heading": "Takeaway", "narration": "Narration text words words."},
        ],
        "claims": [
            {
                "claim_text": "Hallucinated claim with fake citation.",
                "section_id": "sec_03",
                "evidence_ids": ["evi_invented"],
            }
        ],
    }
    llm.set_response(bad_unres_payload)
    with pytest.raises(ScriptDraftEvidenceValidationError, match="emitted evidence IDs in unresearched mode"):
        generator.generate_draft(DraftGeneratorInput(project_id=project_id, outline=unres_outline, allow_unresearched=True))


def test_evidence_id_corrective_retry_and_exhaustion(project_service, storage_service, test_settings):
    """Verify bounded corrective retry on unknown evidence IDs and failure upon exhaustion."""
    project_id = "proj_retry_exhaust"
    run_id = "rrun_retry_exhaust"
    setup_test_project_and_research(
        project_service, storage_service, project_id=project_id, run_id=run_id
    )

    outline = create_test_outline(project_id=project_id, run_id=run_id, sections_count=5)

    invalid_claim_payload = {
        "title": "Title",
        "sections": [
            {"id": "sec_01", "type": "hook", "heading": "Hook", "narration": "Narration words words."},
            {"id": "sec_02", "type": "setup", "heading": "Setup", "narration": "Narration words words."},
            {"id": "sec_03", "type": "middle", "heading": "Middle", "narration": "Narration words words."},
            {"id": "sec_04", "type": "climax", "heading": "Climax", "narration": "Narration words words."},
            {"id": "sec_05", "type": "takeaway", "heading": "Takeaway", "narration": "Narration words words."},
        ],
        "claims": [
            {
                "claim_text": "Plasma achieves equilibrium.",
                "section_id": "sec_03",
                "evidence_ids": ["evi_unknown_hallucinated"],
            }
        ],
    }

    corrected_claim_payload = {
        "title": "Title",
        "sections": [
            {"id": "sec_01", "type": "hook", "heading": "Hook", "narration": "Narration words words."},
            {"id": "sec_02", "type": "setup", "heading": "Setup", "narration": "Narration words words."},
            {"id": "sec_03", "type": "middle", "heading": "Middle", "narration": "Narration words words."},
            {"id": "sec_04", "type": "climax", "heading": "Climax", "narration": "Narration words words."},
            {"id": "sec_05", "type": "takeaway", "heading": "Takeaway", "narration": "Narration words words."},
        ],
        "claims": [
            {
                "claim_text": "Plasma achieves equilibrium.",
                "section_id": "sec_03",
                "evidence_ids": ["evi_01"],
            }
        ],
    }

    # 1. Successful retry
    llm = MockLLMProvider()
    llm._response_queue = [invalid_claim_payload, corrected_claim_payload]

    generator = ScriptDraftGenerator(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    script = generator.generate_draft(DraftGeneratorInput(project_id=project_id, outline=outline))
    assert script.claims[0].evidence_ids == ["evi_01"]

    # 2. Retry exhaustion
    llm._response_queue = [invalid_claim_payload, invalid_claim_payload]
    with pytest.raises(ScriptDraftEvidenceValidationError, match="evi_unknown_hallucinated"):
        generator.generate_draft(DraftGeneratorInput(project_id=project_id, outline=outline))


def test_section_progression_mismatch_raises_malformed(project_service, storage_service, test_settings):
    """Verify that section type or count mismatch against outline raises ScriptDraftMalformedError."""
    project_id = "proj_mismatch"
    run_id = "rrun_mismatch"
    setup_test_project_and_research(
        project_service, storage_service, project_id=project_id, run_id=run_id
    )

    outline = create_test_outline(project_id=project_id, run_id=run_id, sections_count=5)

    # Generated only 4 sections
    short_payload = {
        "title": "Title",
        "sections": [
            {"id": "sec_01", "type": "hook", "heading": "Hook", "narration": "Narration words."},
            {"id": "sec_02", "type": "setup", "heading": "Setup", "narration": "Narration words."},
            {"id": "sec_03", "type": "middle", "heading": "Middle", "narration": "Narration words."},
            {"id": "sec_04", "type": "climax", "heading": "Climax", "narration": "Narration words."},
        ],
        "claims": [],
    }

    llm = MockLLMProvider()
    llm.set_response(short_payload)

    generator = ScriptDraftGenerator(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    with pytest.raises(ScriptDraftMalformedError, match="section count \\(4\\) does not match outline section count \\(5\\)"):
        generator.generate_draft(DraftGeneratorInput(project_id=project_id, outline=outline))


def test_provider_errors_wrapped_cleanly(project_service, storage_service, test_settings):
    """Verify LLMUnavailableError and LLMResponseMalformedError are wrapped with proper causes."""
    project_id = "proj_prov_err"
    run_id = "rrun_prov_err"
    setup_test_project_and_research(
        project_service, storage_service, project_id=project_id, run_id=run_id
    )

    outline = create_test_outline(project_id=project_id, run_id=run_id, sections_count=5)

    llm = MockLLMProvider()
    generator = ScriptDraftGenerator(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    # 1. Unavailable
    llm.set_error(LLMUnavailableError("Ollama endpoint offline"))
    with pytest.raises(ScriptDraftUnavailableError, match="LLM provider unavailable") as exc_info:
        generator.generate_draft(DraftGeneratorInput(project_id=project_id, outline=outline))
    assert isinstance(exc_info.value.__cause__, LLMUnavailableError)

    # 2. Malformed
    llm.set_error(LLMResponseMalformedError("Unparseable tokens"))
    with pytest.raises(ScriptDraftMalformedError, match="LLM generated malformed script payload") as exc_info2:
        generator.generate_draft(DraftGeneratorInput(project_id=project_id, outline=outline))
    assert isinstance(exc_info2.value.__cause__, LLMResponseMalformedError)
