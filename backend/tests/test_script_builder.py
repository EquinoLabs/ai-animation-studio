"""Automated tests for Script Builder backend: drafting, revision, acceptance, finalization, persistence, and CLI."""

import json
from pathlib import Path
import pytest

from backend.app.cli.main import run_cli
from backend.app.config import Settings
from backend.app.domain.contracts import Script, ScriptSection
from backend.app.domain.research import (
    EvidenceRecord,
    PlannedQuery,
    QueryRecord,
    ResearchPlan,
    ResearchRun,
    SourceRecord,
)
from backend.app.domain.script import ConversationEvent
from backend.app.services.research.storage import ResearchStorageService
from backend.app.llm.base import (
    LLMProviderError,
    LLMResponseMalformedError,
    LLMUnavailableError,
)
from backend.app.llm.mock import MockLLMProvider
from backend.app.llm.ollama import OllamaLLMProvider
from backend.app.services.pipeline.orchestrator import (
    PipelineOrchestrator,
    PrerequisiteError,
)
from backend.app.services.project.service import (
    ProjectError,
    ProjectNotFoundError,
    ProjectSecurityError,
    ProjectService,
)
from backend.app.services.scriptwriter.service import (
    ScriptError,
    ScriptFinalizationError,
    ScriptNotFoundError,
    ScriptService,
    ScriptStorageError,
    ScriptValidationError,
)


@pytest.fixture
def script_env(tmp_path):
    """Set up an isolated project service, mock LLM provider, and script service."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        PROJECTS_DIR=projects_dir,
        LLM_SCRIPT_PROVIDER="mock",
    )
    project_service = ProjectService(projects_root=projects_dir)
    mock_llm = MockLLMProvider()
    script_service = ScriptService(
        project_service=project_service,
        llm_provider=mock_llm,
        settings=settings,
    )
    orchestrator = PipelineOrchestrator(
        project_service=project_service,
        script_service=script_service,
        settings=settings,
    )
    project = project_service.create_project(title="Quantum Computing Explainer")
    return {
        "projects_dir": projects_dir,
        "settings": settings,
        "project_service": project_service,
        "mock_llm": mock_llm,
        "script_service": script_service,
        "orchestrator": orchestrator,
        "project": project,
        "project_id": project.metadata.project_id,
    }


# ==============================================================================
# 1. Generation & Validation Tests
# ==============================================================================

def test_start_script_generates_and_persists_valid_draft(script_env):
    """Verify that a valid topic creates a structured v1 draft with conversation events."""
    service = script_env["script_service"]
    pid = script_env["project_id"]

    script = service.start_script(
        project_id=pid,
        topic="Superposition in Quantum Computing",
        target_duration_minutes=6.0,
        allow_unresearched=True,
    )

    assert script.version == 1
    assert script.status == "draft"
    assert script.target_duration_minutes == 6.0
    assert script.topic == "Superposition in Quantum Computing"
    assert len(script.sections) == 5
    assert [s.type for s in script.sections] == ["hook", "setup", "middle", "climax", "takeaway"]

    # Verify filesystem persistence
    dirs = service._get_project_dirs(pid)
    assert dirs["current_file"].is_file()
    assert (dirs["versions_dir"] / "v001.json").is_file()

    # Verify conversation events
    events = service.get_conversation_history(pid)
    assert len(events) == 3
    assert events[0].event_type == "topic_submission"
    assert events[0].content == "Superposition in Quantum Computing"
    assert events[1].event_type == "outline_generated"
    assert events[2].event_type == "draft_generated"
    assert events[2].script_version == 1

    # Verify project stage is in_progress
    project = script_env["project_service"].get_project(pid)
    assert project.metadata.stages["script"].status == "in_progress"


def test_start_script_rejects_invalid_inputs(script_env):
    """Verify topic and duration boundary validations."""
    service = script_env["script_service"]
    pid = script_env["project_id"]

    # Empty topic
    with pytest.raises(ScriptValidationError, match="Topic cannot be empty"):
        service.start_script(pid, topic="   ")

    # Excessive topic length
    with pytest.raises(ScriptValidationError, match="Topic must not exceed 1000 characters"):
        service.start_script(pid, topic="a" * 1001)

    # Invalid duration
    with pytest.raises(ScriptValidationError, match="Target duration must be between"):
        service.start_script(pid, topic="Valid topic", target_duration_minutes=0.0)

    with pytest.raises(ScriptValidationError, match="Target duration must be between"):
        service.start_script(pid, topic="Valid topic", target_duration_minutes=75.0)


def test_validation_rejects_empty_narration_and_duplicate_sections(script_env):
    """Verify that empty narration or section alignment failure triggers validation errors."""
    mock_llm = script_env["mock_llm"]
    service = script_env["script_service"]
    pid = script_env["project_id"]

    outline = service.create_outline(pid, topic="Test Topic", allow_unresearched=True)

    # 1. LLM output with empty narration
    bad_script_data = {
        "title": "Bad Script",
        "topic": "Test Topic",
        "target_duration_minutes": 5.0,
        "sections": [
            {"id": "sec_1", "type": "hook", "heading": "Hook", "narration": "   "},
            {"id": "sec_2", "type": "setup", "heading": "Setup", "narration": "Narration 2"},
            {"id": "sec_3", "type": "middle", "heading": "Middle", "narration": "Narration 3"},
            {"id": "sec_4", "type": "climax", "heading": "Climax", "narration": "Narration 4"},
            {"id": "sec_5", "type": "takeaway", "heading": "Takeaway", "narration": "Narration 5"},
        ],
    }
    mock_llm.set_raw_response(bad_script_data)
    with pytest.raises(ScriptValidationError, match=r"empty.*(narration|blank)"):
        service.start_script(pid, topic="Test Topic", outline=outline, allow_unresearched=True)

    # 2. LLM output with mismatched section count
    mismatch_data = {
        "title": "Mismatch Sections Script",
        "topic": "Test Topic",
        "target_duration_minutes": 5.0,
        "sections": [
            {"id": "sec_1", "type": "hook", "heading": "H1", "narration": "Text 1"},
            {"id": "sec_2", "type": "middle", "heading": "H2", "narration": "Text 2"},
        ],
    }
    mock_llm.set_raw_response(mismatch_data)
    with pytest.raises(ScriptValidationError, match="does not match outline section count"):
        service.start_script(pid, topic="Test Topic", outline=outline, allow_unresearched=True)


def test_provider_failure_preserves_existing_valid_data(script_env):
    """Verify that an LLM provider crash leaves existing drafts uncorrupted."""
    service = script_env["script_service"]
    mock_llm = script_env["mock_llm"]
    pid = script_env["project_id"]

    # Generate initial valid draft
    v1 = service.start_script(pid, topic="Gravity Explainer", allow_unresearched=True)
    assert v1.version == 1

    # Simulate provider failure on revision
    mock_llm.set_error(LLMProviderError("Provider timeout / out of memory"))
    with pytest.raises(ScriptError, match="Failed to revise script via LLM provider"):
        service.revise_script(pid, instruction="Make the climax more dramatic")

    # Verify v1 is still intact and readable
    current = service.get_current_script(pid)
    assert current.version == 1
    assert current.title == v1.title


# ==============================================================================
# 2. Revision & Versioning Tests
# ==============================================================================

def test_revise_script_creates_new_version_and_records_history(script_env):
    """Verify that revising creates version N+1, supplies context, and records events."""
    service = script_env["script_service"]
    mock_llm = script_env["mock_llm"]
    pid = script_env["project_id"]

    # Initial draft
    service.start_script(pid, topic="Photosynthesis in plants", allow_unresearched=True)

    # First revision
    v2 = service.revise_script(pid, instruction="Make the hook more provocative")
    assert v2.version == 2
    assert v2.status == "draft"
    assert "Updated hook" in v2.sections[0].narration

    # Verify call context passed to LLM
    last_call = mock_llm.calls[-1]
    assert last_call["action"] == "revise_script"
    assert last_call["current_script_version"] == 1
    assert "Make the hook more provocative" in last_call["instruction"]

    # Second revision
    v3 = service.revise_script(pid, instruction="Update the title to be catchy")
    assert v3.version == 3
    assert "Revised" in v3.title

    # Verify all historical versions exist
    versions = service.list_script_versions(pid)
    assert len(versions) == 3
    assert [v.version for v in versions] == [1, 2, 3]

    # Verify conversation history
    events = service.get_conversation_history(pid)
    types = [e.event_type for e in events]
    assert types == [
        "topic_submission",
        "outline_generated",
        "draft_generated",
        "revision_instruction",
        "revision_result",
        "revision_instruction",
        "revision_result",
    ]


def test_invalid_revision_preserves_current_draft(script_env):
    """Verify that an empty revision instruction or bad LLM revision doesn't corrupt draft."""
    service = script_env["script_service"]
    mock_llm = script_env["mock_llm"]
    pid = script_env["project_id"]

    service.start_script(pid, topic="Black Holes", allow_unresearched=True)

    # Empty instruction
    with pytest.raises(ScriptValidationError, match="Revision instruction cannot be empty"):
        service.revise_script(pid, instruction="   ")

    # Malformed output from LLM during revision
    mock_llm.set_raw_response({"invalid_field": 123})
    with pytest.raises(ScriptValidationError):
        service.revise_script(pid, instruction="Shorten the middle")

    # Current remains v1
    current = service.get_current_script(pid)
    assert current.version == 1


# ==============================================================================
# 3. Acceptance & Finalization Lifecycle Tests
# ==============================================================================

def test_acceptance_workflow_and_finalization(script_env):
    """Verify that acceptance enables finalization, which writes immutable artifacts and mirrors script.json."""
    service = script_env["script_service"]
    pid = script_env["project_id"]

    service.start_script(pid, topic="The Fermi Paradox", allow_unresearched=True)

    # 1. Finalization fails without acceptance
    with pytest.raises(ScriptFinalizationError, match="must be explicitly accepted"):
        service.finalize_script(pid)

    # 2. Explicitly accept the draft
    accepted = service.accept_draft(pid)
    assert accepted.version == 1

    # Status is still draft (acceptance alone does NOT finalize!)
    current = service.get_current_script(pid)
    assert current.status == "draft"

    # 3. Explicit finalization succeeds
    finalized = service.finalize_script(pid)
    assert finalized.status == "final"
    assert finalized.version == 1

    # Verify immutable artifact exists in finalized/v001.json
    dirs = service._get_project_dirs(pid)
    finalized_file = dirs["finalized_dir"] / "v001.json"
    assert finalized_file.is_file()

    # Verify mirrored copy in script/script.json
    mirrored_file = dirs["script_file"]
    assert mirrored_file.is_file()
    mirrored_data = json.loads(mirrored_file.read_text(encoding="utf-8"))
    assert mirrored_data["status"] == "final"

    # Verify project stage marked completed
    project = script_env["project_service"].get_project(pid)
    assert project.metadata.stages["script"].status == "completed"
    assert project.metadata.stages["script"].updated_at is not None

    # Verify immutability: attempting to re-finalize v1 directly raises error
    with pytest.raises(ScriptFinalizationError, match="already exists and is immutable"):
        service.finalize_script(pid)


def test_rejected_draft_cannot_be_finalized(script_env):
    """Verify that rejecting a draft blocks finalization and requires revision."""
    service = script_env["script_service"]
    pid = script_env["project_id"]

    service.start_script(pid, topic="Tectonic Plates", allow_unresearched=True)
    service.accept_draft(pid)

    # Reject the draft
    service.reject_draft(pid, reason="Middle explanation is confusing")

    # Draft remains viewable
    current = service.get_current_script(pid)
    assert current.version == 1

    # Attempting to finalize raises rejection error
    with pytest.raises(ScriptFinalizationError, match="is rejected and cannot be finalized"):
        service.finalize_script(pid)

    # Revising clears rejection and creates v2
    v2 = service.revise_script(pid, instruction="Clarify tectonic plate boundaries in the middle")
    assert v2.version == 2

    # Still cannot finalize v2 without accepting v2
    with pytest.raises(ScriptFinalizationError, match="must be explicitly accepted"):
        service.finalize_script(pid)

    # Accept v2 and finalize
    service.accept_draft(pid)
    finalized_v2 = service.finalize_script(pid)
    assert finalized_v2.version == 2
    assert finalized_v2.status == "final"


def test_revising_finalized_script_preserves_immutable_finalized_artifact(script_env):
    """Verify that revising a finalized script creates a new draft without modifying the prior finalized artifact."""
    service = script_env["script_service"]
    pid = script_env["project_id"]

    service.start_script(pid, topic="How Stars are Born", allow_unresearched=True)
    service.accept_draft(pid)
    v1_final = service.finalize_script(pid)
    assert v1_final.version == 1

    dirs = service._get_project_dirs(pid)
    v1_final_path = dirs["finalized_dir"] / "v001.json"
    v1_final_content_before = v1_final_path.read_text(encoding="utf-8")

    # Revise the finalized script -> creates v2 draft
    v2_draft = service.revise_script(pid, instruction="Add more visual notes about nebulae")
    assert v2_draft.version == 2
    assert v2_draft.status == "draft"

    # Prior finalized artifact v001.json is 100% UNCHANGED
    assert v1_final_path.read_text(encoding="utf-8") == v1_final_content_before

    # script/script.json still reflects the finalized version v1 until v2 is accepted & finalized
    mirrored = json.loads(dirs["script_file"].read_text(encoding="utf-8"))
    assert mirrored["version"] == 1
    assert mirrored["status"] == "final"

    # Now accept and finalize v2
    service.accept_draft(pid)
    v2_final = service.finalize_script(pid)
    assert v2_final.version == 2

    # Both finalized artifacts exist independently
    assert (dirs["finalized_dir"] / "v001.json").is_file()
    assert (dirs["finalized_dir"] / "v002.json").is_file()
    # script.json now mirrors v2
    mirrored_v2 = json.loads(dirs["script_file"].read_text(encoding="utf-8"))
    assert mirrored_v2["version"] == 2


# ==============================================================================
# 4. Persistence, Recovery & Security Tests
# ==============================================================================

def test_script_state_and_history_survive_service_restart(script_env):
    """Verify that re-instantiating the service loads existing draft, versions, and events."""
    service = script_env["script_service"]
    pid = script_env["project_id"]

    service.start_script(pid, topic="Machine Learning Foundations", allow_unresearched=True)
    service.revise_script(pid, instruction="Add a section about backpropagation")

    # Create a fresh service instance referencing the same projects directory
    fresh_service = ScriptService(
        project_service=script_env["project_service"],
        llm_provider=MockLLMProvider(),
        settings=script_env["settings"],
    )

    loaded = fresh_service.get_current_script(pid)
    assert loaded.version == 2
    assert loaded.topic == "Machine Learning Foundations"

    versions = fresh_service.list_script_versions(pid)
    assert len(versions) == 2

    history = fresh_service.get_conversation_history(pid)
    assert len(history) == 5


def test_corrupt_files_raise_clear_storage_errors(script_env):
    """Verify that corrupt JSON files raise ScriptStorageError instead of raw crashes."""
    service = script_env["script_service"]
    pid = script_env["project_id"]

    dirs = service._get_project_dirs(pid)
    dirs["current_file"].write_text("{corrupt json", encoding="utf-8")

    with pytest.raises(ScriptStorageError, match="Failed to parse current script"):
        service.get_current_script(pid)


def test_path_traversal_attempts_in_script_service_are_blocked(script_env):
    """Verify that path traversal in project_id is blocked."""
    service = script_env["script_service"]

    with pytest.raises(ProjectSecurityError):
        service.get_current_script("../../etc/passwd")

    with pytest.raises(ProjectSecurityError):
        service.start_script("../escape", topic="Traverse")


# ==============================================================================
# 5. Pipeline Integration & Prerequisite Enforcement Tests
# ==============================================================================

def test_orchestrator_script_stage_execution(script_env):
    """Verify pipeline orchestrator handles the script stage appropriately."""
    orchestrator = script_env["orchestrator"]
    pid = script_env["project_id"]
    project = script_env["project"]

    # Populate a valid plan and completed research run so the script stage has research grounding
    storage_service = ResearchStorageService(projects_root=script_env["projects_dir"], settings=script_env["settings"])
    plan = ResearchPlan(
        plan_id="rplan_orch_01",
        project_id=pid,
        topic=project.metadata.title,
        research_objective="Investigate quantum principles",
        research_questions=["What is superposition?"],
    )
    storage_service.save_plan(plan)
    run = ResearchRun(
        run_id="rrun_orch_01",
        plan_id="rplan_orch_01",
        project_id=pid,
        status="completed",
    )
    storage_service.save_run(run)
    source = SourceRecord(
        source_id="src_01",
        project_id=pid,
        url="https://example.org/quantum",
        title="Quantum Physics Overview",
        source_type="scholarly",
    )
    storage_service.save_source(pid, source)
    evidence = EvidenceRecord(
        evidence_id="evi_01",
        run_id="rrun_orch_01",
        project_id=pid,
        source_id="src_01",
        excerpt="Quantum superposition is a fundamental principle.",
    )
    storage_service.save_evidence(pid, evidence)

    # Register the production script handler on orchestrator
    orchestrator.register_script_handler()

    # 1. Project has no script draft yet -> runs handler, generates draft from project title
    res = orchestrator.run_stage(pid, "script")
    assert not res.success
    assert "Initial draft v1 generated" in (res.errors[0] if res.errors else "")

    # 2. Accept and finalize the draft via orchestrator delegation methods
    orchestrator.accept_draft(pid)
    finalized = orchestrator.finalize_script(pid)
    assert finalized.status == "final"

    # 3. Running stage again succeeds because valid finalized script exists
    res_again = orchestrator.run_stage(pid, "script")
    assert res_again.success
    assert "script" in res_again.executed_stages


def test_finalized_script_prerequisite_for_downstream_stages(script_env):
    """Verify downstream stages (plan-scenes, script-to-voice) require finalized script."""
    orchestrator = script_env["orchestrator"]
    project = script_env["project"]
    pid = script_env["project_id"]

    # Before script finalization: plan-scenes fails prerequisite check
    with pytest.raises(PrerequisiteError, match="missing 'script/script.json'"):
        orchestrator.check_prerequisites(project, "plan-scenes")

    # Start and accept script
    orchestrator.start_script(pid, topic="Ocean Currents", allow_unresearched=True)
    orchestrator.accept_draft(pid)
    orchestrator.finalize_script(pid)

    # Now plan-scenes prerequisite check passes!
    orchestrator.check_prerequisites(project, "plan-scenes")
    orchestrator.check_prerequisites(project, "script-to-voice")


# ==============================================================================
# 6. CLI Integration Tests
# ==============================================================================

def test_cli_script_builder_workflow(script_env, capsys):
    """Verify the complete Script Builder conversational workflow using CLI commands."""
    p_dir = str(script_env["projects_dir"])
    pid = script_env["project_id"]

    # 1. Start script via CLI
    code1 = run_cli([
        "--script",
        "--project",
        pid,
        "--topic",
        "How Airplanes Fly",
        "--allow-unresearched",
        "--projects-dir",
        p_dir,
    ])
    assert code1 == 0
    out1, _ = capsys.readouterr()
    assert "Draft v1 created" in out1

    # 2. Revise script via CLI
    code2 = run_cli([
        "--script",
        "--project",
        pid,
        "--instruction",
        "Make the Bernoulli explanation simpler",
        "--projects-dir",
        p_dir,
    ])
    assert code2 == 0
    out2, _ = capsys.readouterr()
    assert "Draft v2 created" in out2

    # 3. Show script via CLI
    code3 = run_cli([
        "--script",
        "--project",
        pid,
        "--show-script",
        "--projects-dir",
        p_dir,
    ])
    assert code3 == 0
    out3, _ = capsys.readouterr()
    assert "v2, status: draft" in out3

    # 4. View history via CLI
    code4 = run_cli([
        "--script",
        "--project",
        pid,
        "--history",
        "--projects-dir",
        p_dir,
    ])
    assert code4 == 0
    out4, _ = capsys.readouterr()
    assert "topic_submission" in out4
    assert "revision_instruction" in out4

    # 5. Finalize without accept fails via CLI
    code5 = run_cli([
        "--script",
        "--project",
        pid,
        "--finalize",
        "--projects-dir",
        p_dir,
    ])
    assert code5 == 1
    _, err5 = capsys.readouterr()
    assert "must be explicitly accepted" in err5

    # 6. Reject draft via CLI
    code6 = run_cli([
        "--script",
        "--project",
        pid,
        "--reject",
        "--reason",
        "Too fast-paced",
        "--projects-dir",
        p_dir,
    ])
    assert code6 == 0
    out6, _ = capsys.readouterr()
    assert "Draft v2 rejected" in out6

    # 7. Accept draft via CLI
    code7 = run_cli([
        "--script",
        "--project",
        pid,
        "--accept",
        "--projects-dir",
        p_dir,
    ])
    assert code7 == 0
    out7, _ = capsys.readouterr()
    assert "Draft v2 accepted" in out7

    # 8. Finalize accepted draft via CLI
    code8 = run_cli([
        "--script",
        "--project",
        pid,
        "--finalize",
        "--projects-dir",
        p_dir,
    ])
    assert code8 == 0
    out8, _ = capsys.readouterr()
    assert "Script v2 finalized" in out8


# ==============================================================================
# 7. Real Provider Adapter (Ollama) Tests
# ==============================================================================

def test_ollama_provider_error_handling_when_unreachable():
    """Verify Ollama adapter reports LLMUnavailableError with helpful advice when offline."""
    settings = Settings(
        OLLAMA_HOST="http://127.0.0.1:54321",  # Non-existent port
        OLLAMA_MODEL="qwen2.5:7b",
        OLLAMA_TIMEOUT_SECONDS=0.5,
    )
    provider = OllamaLLMProvider(settings=settings)

    with pytest.raises(LLMUnavailableError, match="Unable to connect to Ollama service"):
        provider.generate_json("Prompt", Script)
