"""Comprehensive deterministic tests for ScriptService lifecycle integration (Phase 4, P4-S5 Checkpoint 1).

Covers:
1. Strict research-run selection policy (rejection on zero runs, selection of latest completed, rejection of incomplete/failed).
2. Explicit unresearched generation authorization and override.
3. Outline lifecycle (standalone creation in script/outlines/, retrieval, passing supplied outline, automatic generation).
4. Robust version allocation (_allocate_next_version scanning versions/ and finalized/).
5. Failure safety and immutability (pre-persistence failure preserves working draft; snapshots are never overwritten).
6. Backward compatibility with older saved-script JSON representations.
7. Researched draft claim/evidence provenance and bidirectional section linkage.
8. CLI command flags (--create-outline, --allow-unresearched, --research-run-id).
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from typing import Optional
import pytest

from backend.app.cli.main import run_cli
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
    GeneratedScriptPayload,
    OutlineSectionPayload,
    Script,
    ScriptClaimPayload,
    ScriptOutline,
    ScriptOutlineSection,
    ScriptSectionPayload,
)
from backend.app.llm.base import LLMProviderError
from backend.app.llm.mock import MockLLMProvider
from backend.app.services.pipeline.orchestrator import PipelineOrchestrator
from backend.app.services.project.service import ProjectService
from backend.app.services.research.storage import ResearchStorageService
from backend.app.services.scriptwriter.service import (
    ScriptError,
    ScriptNotFoundError,
    ScriptService,
    ScriptStorageError,
    ScriptValidationError,
)


@pytest.fixture
def lifecycle_env(tmp_path):
    """Isolated environment for ScriptService lifecycle tests."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        PROJECTS_DIR=projects_dir,
        LLM_SCRIPT_PROVIDER="mock",
    )
    project_service = ProjectService(projects_root=projects_dir)
    storage_service = ResearchStorageService(projects_root=projects_dir, settings=settings)
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
    project = project_service.create_project(title="Lifecycle Test Explainer")
    return {
        "projects_dir": projects_dir,
        "settings": settings,
        "project_service": project_service,
        "storage_service": storage_service,
        "mock_llm": mock_llm,
        "script_service": script_service,
        "orchestrator": orchestrator,
        "project": project,
        "project_id": project.metadata.project_id,
    }


def seed_research_run(
    storage_service: ResearchStorageService,
    project_id: str,
    run_id: str = "rrun_01",
    status: str = "completed",
    plan_id: str = "rplan_01",
    create_evidence: bool = True,
    started_at: Optional[datetime] = None,
) -> ResearchRun:
    """Helper to persist a plan, run, source, and evidence records."""
    plan = ResearchPlan(
        plan_id=plan_id,
        project_id=project_id,
        topic="Lifecycle Research Topic",
        research_objective="Investigate lifecycle mechanisms.",
        research_questions=["What is the mechanism?"],
        planned_queries=[
            PlannedQuery(
                query_id="qry_01",
                query_text="mechanism query",
                purpose="Investigate core mechanism",
            )
        ],
    )
    storage_service.save_plan(plan)

    run = ResearchRun(
        run_id=run_id,
        plan_id=plan_id,
        project_id=project_id,
        status=status,
        started_at=started_at or datetime.now(timezone.utc),
        query_records=[
            QueryRecord(
                query_id="qry_01",
                plan_id=plan_id,
                run_id=run_id,
                query_text="mechanism query",
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
            url="https://example.org/mechanism",
            title="Mechanism Overview",
            source_type="scholarly",
        )
        storage_service.save_source(project_id, source)

        evidence = EvidenceRecord(
            evidence_id="evi_01",
            project_id=project_id,
            source_id="src_01",
            run_id=run_id,
            excerpt="Verified mechanism excerpt demonstrating proof.",
        )
        storage_service.save_evidence(project_id, evidence)

    return run


# ==============================================================================
# 1. Research Run Selection Policy
# ==============================================================================

def test_no_research_run_rejects_default_script_generation(lifecycle_env):
    """When project has 0 research runs, default start_script must raise ScriptValidationError."""
    service = lifecycle_env["script_service"]
    pid = lifecycle_env["project_id"]

    with pytest.raises(ScriptValidationError, match="No research runs found"):
        service.start_script(pid, topic="Unresearched Topic")


def test_explicit_unresearched_generation_succeeds(lifecycle_env):
    """When allow_unresearched=True, script generation succeeds without research runs."""
    service = lifecycle_env["script_service"]
    pid = lifecycle_env["project_id"]

    script = service.start_script(pid, topic="Unresearched Topic", allow_unresearched=True)
    assert script.version == 1
    assert script.research_run_id is None
    for claim in script.claims:
        assert claim.evidence_ids == []
        assert claim.source_ids == []


def test_completed_research_run_is_selected_when_run_id_omitted(lifecycle_env):
    """When completed research run exists, start_script selects it automatically."""
    service = lifecycle_env["script_service"]
    storage = lifecycle_env["storage_service"]
    pid = lifecycle_env["project_id"]

    run = seed_research_run(storage, pid, run_id="rrun_completed_01")
    script = service.start_script(pid, topic="Researched Topic")

    assert script.version == 1
    assert script.research_run_id == "rrun_completed_01"
    assert len(script.claims) > 0
    assert any("evi_01" in c.evidence_ids for c in script.claims)


def test_incomplete_or_failed_run_does_not_fall_back_silently(lifecycle_env):
    """When only incomplete or failed runs exist, start_script must reject with ScriptValidationError."""
    service = lifecycle_env["script_service"]
    storage = lifecycle_env["storage_service"]
    pid = lifecycle_env["project_id"]

    seed_research_run(storage, pid, run_id="rrun_failed_01", status="failed", create_evidence=False)

    with pytest.raises(ScriptValidationError, match="incomplete or failed runs are rejected"):
        service.start_script(pid, topic="Failed Research Topic")


def test_newer_incomplete_run_blocks_generation_without_falling_back_to_older_completed_run(lifecycle_env):
    """When an older run is completed but the newest overall run is failed/incomplete, reject explicitly."""
    service = lifecycle_env["script_service"]
    storage = lifecycle_env["storage_service"]
    pid = lifecycle_env["project_id"]

    # 1. Older completed run
    seed_research_run(
        storage,
        pid,
        run_id="rrun_01_old_completed",
        status="completed",
        plan_id="rplan_01",
        started_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
    )

    # 2. Newer failed run
    seed_research_run(
        storage,
        pid,
        run_id="rrun_02_new_failed",
        status="failed",
        plan_id="rplan_02",
        create_evidence=False,
        started_at=datetime(2026, 1, 2, 0, 0, 0, tzinfo=timezone.utc),
    )

    # 3. start_script must reject and not fall back to rrun_01_old_completed
    with pytest.raises(ScriptValidationError, match="incomplete or failed runs are rejected and cannot fall back"):
        service.start_script(pid, topic="Newest Run Failed Topic")


def test_explicit_unresearched_overrides_available_research(lifecycle_env):
    """When research run exists but allow_unresearched=True is passed, output is unresearched."""
    service = lifecycle_env["script_service"]
    storage = lifecycle_env["storage_service"]
    pid = lifecycle_env["project_id"]

    seed_research_run(storage, pid, run_id="rrun_existing_01")
    script = service.start_script(pid, topic="Override Topic", allow_unresearched=True)

    assert script.research_run_id is None
    for claim in script.claims:
        assert claim.evidence_ids == []


# ==============================================================================
# 2. Outline Lifecycle & Decoupled Execution
# ==============================================================================

def test_outline_only_creation_lifecycle(lifecycle_env):
    """create_outline creates script/outlines/<id>.json and logs event without creating drafts."""
    service = lifecycle_env["script_service"]
    pid = lifecycle_env["project_id"]

    outline = service.create_outline(pid, topic="Standalone Outline Topic", allow_unresearched=True)
    assert outline.outline_id.startswith("out_")
    assert len(outline.sections) >= 5

    # File exists in outlines/
    dirs = service._get_project_dirs(pid)
    outline_path = dirs["outlines_dir"] / f"{outline.outline_id}.json"
    assert outline_path.is_file()

    # Draft current.json and versions/ must NOT exist
    assert not dirs["current_file"].exists()
    assert len(list(dirs["versions_dir"].glob("*.json"))) == 0

    # Event logged is outline_generated, not draft_generated
    events = service.get_conversation_history(pid)
    assert len(events) == 1
    assert events[0].event_type == "outline_generated"

    # get_outline retrieves it cleanly
    retrieved = service.get_outline(pid, outline.outline_id)
    assert retrieved.outline_id == outline.outline_id
    assert len(retrieved.sections) == len(outline.sections)


def test_draft_generation_from_supplied_outline(lifecycle_env):
    """Passing pre-created outline to start_script skips re-outlining and grounds draft directly."""
    service = lifecycle_env["script_service"]
    mock_llm = lifecycle_env["mock_llm"]
    pid = lifecycle_env["project_id"]

    outline = service.create_outline(pid, topic="Pre-outlined Topic", allow_unresearched=True)
    mock_llm.clear()

    script = service.start_script(
        pid,
        topic="Pre-outlined Topic",
        outline=outline,
        allow_unresearched=True,
        generation_mode="monolithic",
    )
    assert script.version == 1
    # Only one LLM call occurred for script drafting, none for outline
    assert len(mock_llm.calls) == 1
    assert mock_llm.calls[0]["schema"] == "GeneratedScriptPayload"


def test_automatic_outline_creation_in_standard_flow(lifecycle_env):
    """Calling start_script without outline creates both an outline artifact and draft."""
    service = lifecycle_env["script_service"]
    pid = lifecycle_env["project_id"]

    script = service.start_script(pid, topic="Auto Outline Topic", allow_unresearched=True)
    assert script.version == 1

    dirs = service._get_project_dirs(pid)
    outlines = list(dirs["outlines_dir"].glob("*.json"))
    assert len(outlines) == 1

    events = service.get_conversation_history(pid)
    types = [e.event_type for e in events]
    assert types == ["topic_submission", "outline_generated", "draft_generated"]


# ==============================================================================
# 3. Robust Version Allocation & Overwrite Safety
# ==============================================================================

def test_version_allocation_scans_both_versions_and_finalized(lifecycle_env):
    """_allocate_next_version accounts for snapshots and finalized versions."""
    service = lifecycle_env["script_service"]
    pid = lifecycle_env["project_id"]
    dirs = service._get_project_dirs(pid)

    # 1. Fresh project -> version 1
    assert service._allocate_next_version(dirs) == 1

    # 2. Existing v1 in versions -> version 2
    (dirs["versions_dir"] / "v001.json").write_text("{}", encoding="utf-8")
    assert service._allocate_next_version(dirs) == 2

    # 3. Finalized v003 exists -> max(1, 3) + 1 = 4
    (dirs["finalized_dir"] / "v003.json").write_text("{}", encoding="utf-8")
    assert service._allocate_next_version(dirs) == 4


def test_existing_snapshot_is_never_overwritten(lifecycle_env):
    """If target snapshot file already exists, start_script raises ScriptStorageError."""
    service = lifecycle_env["script_service"]
    pid = lifecycle_env["project_id"]
    dirs = service._get_project_dirs(pid)

    # Manually pre-create v001.json snapshot
    (dirs["versions_dir"] / "v001.json").write_text('{"title": "Pre-existing"}', encoding="utf-8")

    # Force allocate to 1 by monkey-patching or passing state
    with pytest.raises(ScriptStorageError, match="already exists"):
        # We simulate conflict by allocating after helper returns 1
        original_alloc = service._allocate_next_version
        service._allocate_next_version = lambda *args, **kwargs: 1
        try:
            service.start_script(pid, topic="Collision Topic", allow_unresearched=True)
        finally:
            service._allocate_next_version = original_alloc


def test_generation_or_validation_failure_preserves_previous_current_draft(lifecycle_env):
    """If revision or generation fails, working draft is preserved without corruption."""
    service = lifecycle_env["script_service"]
    mock_llm = lifecycle_env["mock_llm"]
    pid = lifecycle_env["project_id"]

    v1 = service.start_script(pid, topic="Robustness Topic", allow_unresearched=True)
    assert v1.version == 1

    # Trigger failure on revision
    mock_llm.set_error(LLMProviderError("LLM out of memory"))
    with pytest.raises(ScriptError):
        service.revise_script(pid, instruction="Make it flashier")

    # v1 is completely intact
    current = service.get_current_script(pid)
    assert current.version == 1
    assert current.title == v1.title


def test_failed_draft_generation_does_not_emit_draft_generated_event(lifecycle_env):
    """When draft generation fails, conversation history must NOT contain draft_generated event."""
    service = lifecycle_env["script_service"]
    mock_llm = lifecycle_env["mock_llm"]
    pid = lifecycle_env["project_id"]

    # Pre-create a valid outline
    outline = service.create_outline(pid, topic="Failure Test Topic", allow_unresearched=True)

    # Queue an error for the generator's call
    mock_llm.set_error(LLMProviderError("LLM exploded during generation"))

    with pytest.raises(ScriptError):
        service.start_script(pid, topic="Failure Test Topic", outline=outline, allow_unresearched=True)

    # Verify conversation events
    events = service.get_conversation_history(pid)
    event_types = [e.event_type for e in events]
    assert "draft_generated" not in event_types


# ==============================================================================
# 4. Backward Compatibility with Older Saved Scripts
# ==============================================================================

def test_legacy_saved_script_remains_loadable(lifecycle_env):
    """Older script.json without research_run_id or claim_ids loads cleanly."""
    service = lifecycle_env["script_service"]
    pid = lifecycle_env["project_id"]
    dirs = service._get_project_dirs(pid)

    legacy_data = {
        "title": "Legacy Quantum Explainer",
        "topic": "Quantum Computing",
        "target_duration_minutes": 5.0,
        "version": 1,
        "status": "draft",
        "sections": [
            {
                "id": "sec_01",
                "type": "hook",
                "heading": "Hook",
                "narration": "What if a computer could explore every path at once?",
            },
            {
                "id": "sec_02",
                "type": "middle",
                "heading": "Middle",
                "narration": "Superposition makes this possible.",
            },
        ],
    }
    dirs["current_file"].write_text(json.dumps(legacy_data), encoding="utf-8")

    loaded = service.get_current_script(pid)
    assert loaded.title == "Legacy Quantum Explainer"
    assert loaded.research_run_id is None
    assert loaded.claims == []
    assert len(loaded.sections) == 2
    assert loaded.sections[0].claim_ids == []
    assert loaded.sections[0].outline_section_index is None


# ==============================================================================
# 5. Claim/Evidence Provenance and Bidirectional Traceability
# ==============================================================================

def test_researched_draft_claims_and_section_linkage(lifecycle_env):
    """Every claim in researched draft maps to valid section and derived sources."""
    service = lifecycle_env["script_service"]
    storage = lifecycle_env["storage_service"]
    pid = lifecycle_env["project_id"]

    seed_research_run(storage, pid, run_id="rrun_prov_01")
    script = service.start_script(pid, topic="Provenance Topic")

    assert script.research_run_id == "rrun_prov_01"
    assert len(script.claims) > 0

    section_ids = {s.id for s in script.sections}
    for claim in script.claims:
        assert claim.section_id in section_ids
        # Bidirectional check: section claim_ids must include this claim
        sec = next(s for s in script.sections if s.id == claim.section_id)
        assert claim.claim_id in sec.claim_ids
        # If evidence was cited, sources must be populated authoritatively
        if claim.evidence_ids:
            assert "evi_01" in claim.evidence_ids
            assert "src_01" in claim.source_ids


# ==============================================================================
# 6. CLI Integration for New Flags
# ==============================================================================

def test_cli_create_outline_only(lifecycle_env, capsys):
    """CLI --create-outline creates outline and exits cleanly."""
    p_dir = str(lifecycle_env["projects_dir"])
    pid = lifecycle_env["project_id"]

    code = run_cli([
        "--script",
        "--create-outline",
        "--project",
        pid,
        "--topic",
        "CLI Outline Topic",
        "--allow-unresearched",
        "--projects-dir",
        p_dir,
    ])
    assert code == 0
    out, _ = capsys.readouterr()
    assert "Outline created" in out

    # Verify no draft was created
    dirs = lifecycle_env["script_service"]._get_project_dirs(pid)
    assert not dirs["current_file"].exists()


def test_cli_researched_script_generation(lifecycle_env, capsys):
    """CLI --script with explicit --research-run-id succeeds."""
    p_dir = str(lifecycle_env["projects_dir"])
    pid = lifecycle_env["project_id"]
    storage = lifecycle_env["storage_service"]

    seed_research_run(storage, pid, run_id="rrun_cli_01")

    code = run_cli([
        "--script",
        "--project",
        pid,
        "--topic",
        "CLI Researched Topic",
        "--research-run-id",
        "rrun_cli_01",
        "--projects-dir",
        p_dir,
    ])
    assert code == 0
    out, _ = capsys.readouterr()
    assert "Draft v1 created" in out
