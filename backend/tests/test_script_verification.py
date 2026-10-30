"""Architectural verification pass tests for Script Builder backend.

Covers:
- Verification A: Finalization fault injection & recovery across all multi-file interruption points
- Verification B: Version identity, immutability, and downstream resolution
- Verification C: Exact lifecycle state transition sequences
- Verification D: Provider & contract validation (roles, bounds, status spoofing)
- Verification E: Persistence recovery and path traversal security
"""

import json
from pathlib import Path
import pytest

from backend.app.config import Settings
from backend.app.domain.contracts import Script, ScriptSection
from backend.app.domain.script import SECTION_TYPES
from backend.app.llm.base import (
    LLMProviderError,
    LLMResponseMalformedError,
    LLMUnavailableError,
)
from backend.app.llm.mock import MockLLMProvider
from backend.app.services.pipeline.orchestrator import PipelineOrchestrator
from backend.app.services.project.service import (
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
def v_env(tmp_path):
    """Isolated environment for verification tests."""
    projects_dir = tmp_path / "v_projects"
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
    orig_start = script_service.start_script
    def patched_start(project_id, topic, **kwargs):
        if "allow_unresearched" not in kwargs and "research_run_id" not in kwargs:
            kwargs["allow_unresearched"] = True
        return orig_start(project_id, topic, **kwargs)
    script_service.start_script = patched_start

    project = project_service.create_project(title="Cosmic Verification")
    return {
        "projects_dir": projects_dir,
        "settings": settings,
        "project_service": project_service,
        "mock_llm": mock_llm,
        "script_service": script_service,
        "orchestrator": orchestrator,
        "project": project,
        "pid": project.metadata.project_id,
    }


# ==============================================================================
# Verification A — Finalization Interruption & Fault Injection Tests
# ==============================================================================

def test_interruption_at_commit_marker_before_artifacts_written(v_env):
    """Fault injection 0: Crash right after writing .finalizing.json before writing finalized/v001.json."""
    service = v_env["script_service"]
    pid = v_env["pid"]

    service.start_script(pid, topic="Neutron Stars")
    service.accept_draft(pid)
    current = service.get_current_script(pid)

    dirs = service._get_project_dirs(pid)
    # Simulate writing the commit marker and then crashing
    finalized = current.model_copy(update={"status": "final"})
    commit_payload = {
        "version": finalized.version,
        "finalized_script": finalized.model_dump(mode="json"),
        "timestamp": "2026-10-29T00:00:00Z",
    }
    dirs["commit_marker"].write_text(json.dumps(commit_payload), encoding="utf-8")

    # On restart, reading the script triggers reconciliation
    fresh_service = ScriptService(
        project_service=v_env["project_service"],
        llm_provider=v_env["mock_llm"],
        settings=v_env["settings"],
    )
    recovered = fresh_service.get_current_script(pid)
    assert recovered.status == "final"
    assert recovered.version == 1

    # Verify all artifacts were reconciled to consistent state
    assert (dirs["finalized_dir"] / "v001.json").is_file()
    assert dirs["script_file"].is_file()
    assert not dirs["commit_marker"].exists()

    # Verify metadata marked completed
    project = v_env["project_service"].get_project(pid)
    assert project.metadata.stages["script"].status == "completed"


def test_interruption_after_finalized_artifact_before_downstream_mirror(v_env):
    """Fault injection 1: Crash after finalized/v001.json is written, before script/script.json."""
    service = v_env["script_service"]
    pid = v_env["pid"]

    service.start_script(pid, topic="Neutron Stars")
    service.accept_draft(pid)
    current = service.get_current_script(pid)

    dirs = service._get_project_dirs(pid)
    finalized = current.model_copy(update={"status": "final"})
    commit_payload = {
        "version": finalized.version,
        "finalized_script": finalized.model_dump(mode="json"),
        "timestamp": "2026-10-29T00:00:00Z",
    }
    dirs["commit_marker"].write_text(json.dumps(commit_payload), encoding="utf-8")
    (dirs["finalized_dir"] / "v001.json").write_text(finalized.model_dump_json(), encoding="utf-8")
    # script.json is intentionally NOT written yet

    # Service restarts
    fresh_service = ScriptService(
        project_service=v_env["project_service"],
        llm_provider=v_env["mock_llm"],
        settings=v_env["settings"],
    )
    reconciled = fresh_service.get_finalized_script(pid)
    assert reconciled.version == 1
    assert reconciled.status == "final"

    # script/script.json and current.json have been safely generated
    assert dirs["script_file"].is_file()
    assert dirs["current_file"].is_file()
    assert not dirs["commit_marker"].exists()


def test_interruption_after_script_json_before_current_json(v_env):
    """Fault injection 2: Crash after script/script.json written, before current.json updated."""
    service = v_env["script_service"]
    pid = v_env["pid"]

    service.start_script(pid, topic="Neutron Stars")
    service.accept_draft(pid)
    current = service.get_current_script(pid)

    dirs = service._get_project_dirs(pid)
    finalized = current.model_copy(update={"status": "final"})
    commit_payload = {
        "version": finalized.version,
        "finalized_script": finalized.model_dump(mode="json"),
        "timestamp": "2026-10-29T00:00:00Z",
    }
    dirs["commit_marker"].write_text(json.dumps(commit_payload), encoding="utf-8")
    (dirs["finalized_dir"] / "v001.json").write_text(finalized.model_dump_json(), encoding="utf-8")
    dirs["script_file"].write_text(finalized.model_dump_json(), encoding="utf-8")
    # current.json still has status="draft"

    fresh_service = ScriptService(
        project_service=v_env["project_service"],
        llm_provider=v_env["mock_llm"],
        settings=v_env["settings"],
    )
    loaded = fresh_service.get_current_script(pid)
    assert loaded.status == "final"
    assert not dirs["commit_marker"].exists()


def test_interruption_after_current_json_before_conversation_event(v_env):
    """Fault injection 3: Crash after current.json written, before conversation event appended."""
    service = v_env["script_service"]
    pid = v_env["pid"]

    service.start_script(pid, topic="Neutron Stars")
    service.accept_draft(pid)
    current = service.get_current_script(pid)

    dirs = service._get_project_dirs(pid)
    finalized = current.model_copy(update={"status": "final"})
    commit_payload = {
        "version": finalized.version,
        "finalized_script": finalized.model_dump(mode="json"),
        "timestamp": "2026-10-29T00:00:00Z",
    }
    dirs["commit_marker"].write_text(json.dumps(commit_payload), encoding="utf-8")
    (dirs["finalized_dir"] / "v001.json").write_text(finalized.model_dump_json(), encoding="utf-8")
    dirs["script_file"].write_text(finalized.model_dump_json(), encoding="utf-8")
    dirs["current_file"].write_text(finalized.model_dump_json(), encoding="utf-8")
    # conversation.json does not yet contain script_finalized event
    # metadata is still in_progress

    fresh_service = ScriptService(
        project_service=v_env["project_service"],
        llm_provider=v_env["mock_llm"],
        settings=v_env["settings"],
    )
    loaded = fresh_service.get_current_script(pid)
    assert loaded.status == "final"

    # Verify conversation history now has script_finalized exactly once
    history = fresh_service.get_conversation_history(pid)
    fin_events = [e for e in history if e.event_type == "script_finalized"]
    assert len(fin_events) == 1
    assert fin_events[0].script_version == 1

    # Verify metadata is completed
    reloaded_project = v_env["project_service"].get_project(pid)
    assert reloaded_project.metadata.stages["script"].status == "completed"
    assert not dirs["commit_marker"].exists()


def test_interruption_after_conversation_event_before_metadata(v_env):
    """Fault injection 4: Crash after conversation event appended, before metadata updated."""
    service = v_env["script_service"]
    pid = v_env["pid"]

    service.start_script(pid, topic="Neutron Stars")
    service.accept_draft(pid)
    current = service.get_current_script(pid)

    dirs = service._get_project_dirs(pid)
    finalized = current.model_copy(update={"status": "final"})
    commit_payload = {
        "version": finalized.version,
        "finalized_script": finalized.model_dump(mode="json"),
        "timestamp": "2026-10-29T00:00:00Z",
    }
    dirs["commit_marker"].write_text(json.dumps(commit_payload), encoding="utf-8")
    (dirs["finalized_dir"] / "v001.json").write_text(finalized.model_dump_json(), encoding="utf-8")
    dirs["script_file"].write_text(finalized.model_dump_json(), encoding="utf-8")
    dirs["current_file"].write_text(finalized.model_dump_json(), encoding="utf-8")

    # Manually append script_finalized event prior to crash
    service._append_conversation_event(
        dirs["conversation_file"],
        event_type="script_finalized",
        content=f"Finalized version {finalized.version}: '{finalized.title}'",
        script_version=finalized.version,
    )
    history_len_before = len(service.get_conversation_history(pid))
    # Project metadata is still in_progress

    fresh_service = ScriptService(
        project_service=v_env["project_service"],
        llm_provider=v_env["mock_llm"],
        settings=v_env["settings"],
    )
    fresh_service.get_current_script(pid)

    # Reconciled metadata
    reloaded_project = v_env["project_service"].get_project(pid)
    assert reloaded_project.metadata.stages["script"].status == "completed"
    assert not dirs["commit_marker"].exists()

    # Verify conversation event was NOT duplicated during recovery
    history_len_after = len(fresh_service.get_conversation_history(pid))
    assert history_len_after == history_len_before


def test_interruption_after_metadata_before_marker_removal(v_env):
    """Fault injection 5: Crash after metadata updated, before commit marker unlinked."""
    service = v_env["script_service"]
    pid = v_env["pid"]

    service.start_script(pid, topic="Neutron Stars")
    service.accept_draft(pid)
    current = service.get_current_script(pid)

    dirs = service._get_project_dirs(pid)
    finalized = current.model_copy(update={"status": "final"})
    commit_payload = {
        "version": finalized.version,
        "finalized_script": finalized.model_dump(mode="json"),
        "timestamp": "2026-10-29T00:00:00Z",
    }
    dirs["commit_marker"].write_text(json.dumps(commit_payload), encoding="utf-8")
    (dirs["finalized_dir"] / "v001.json").write_text(finalized.model_dump_json(), encoding="utf-8")
    dirs["script_file"].write_text(finalized.model_dump_json(), encoding="utf-8")
    dirs["current_file"].write_text(finalized.model_dump_json(), encoding="utf-8")
    service._append_conversation_event(
        dirs["conversation_file"],
        event_type="script_finalized",
        content=f"Finalized version {finalized.version}: '{finalized.title}'",
        script_version=finalized.version,
    )
    v_env["project_service"].update_stage_status(pid, "script", "completed")
    # Commit marker still sits on disk

    fresh_service = ScriptService(
        project_service=v_env["project_service"],
        llm_provider=v_env["mock_llm"],
        settings=v_env["settings"],
    )
    # Any read operation triggers reconciliation
    loaded = fresh_service.get_current_script(pid)
    assert loaded.status == "final"
    # Commit marker was safely unlinked
    assert not dirs["commit_marker"].exists()


def test_reconciliation_is_idempotent_and_does_not_mutate_or_duplicate(v_env):
    """Verify repeated reconciliation calls do not duplicate events, increment versions, or alter state."""
    service = v_env["script_service"]
    pid = v_env["pid"]

    service.start_script(pid, topic="Neutron Stars")
    service.accept_draft(pid)
    finalized = service.finalize_script(pid)
    assert finalized.version == 1

    dirs = service._get_project_dirs(pid)
    events_before = len(service.get_conversation_history(pid))
    finalized_content_before = (dirs["finalized_dir"] / "v001.json").read_text(encoding="utf-8")

    # Manually re-place a commit marker to simulate repeated recovery invocations
    commit_payload = {
        "version": finalized.version,
        "finalized_script": finalized.model_dump(mode="json"),
        "timestamp": "2026-10-29T00:00:00Z",
    }
    for _ in range(5):
        dirs["commit_marker"].write_text(json.dumps(commit_payload), encoding="utf-8")
        service._reconcile_finalization(pid, dirs)

    # State must be identical: no duplicate events, no modified artifacts, no version increments
    events_after = len(service.get_conversation_history(pid))
    assert events_after == events_before

    finalized_content_after = (dirs["finalized_dir"] / "v001.json").read_text(encoding="utf-8")
    assert finalized_content_after == finalized_content_before

    current = service.get_current_script(pid)
    assert current.version == 1
    assert current.status == "final"


def test_recovery_never_overwrites_existing_finalized_artifact_with_divergent_payload(v_env):
    """Verify recovery treats on-disk finalized artifacts as immutable ground truth."""
    service = v_env["script_service"]
    pid = v_env["pid"]

    service.start_script(pid, topic="Neutron Stars")
    service.accept_draft(pid)
    finalized = service.finalize_script(pid)

    dirs = service._get_project_dirs(pid)
    original_final_content = (dirs["finalized_dir"] / "v001.json").read_text(encoding="utf-8")

    # Inject a rogue commit marker with different, tampered narration
    tampered_script = finalized.model_copy(deep=True)
    tampered_script.sections[0].narration = "TAMPERED NARATION THAT MUST NOT OVERWRITE"
    tampered_payload = {
        "version": 1,
        "finalized_script": tampered_script.model_dump(mode="json"),
        "timestamp": "2026-10-29T00:00:00Z",
    }
    dirs["commit_marker"].write_text(json.dumps(tampered_payload), encoding="utf-8")

    # Reconcile
    service._reconcile_finalization(pid, dirs)

    # The existing finalized artifact v001.json MUST NOT be overwritten
    actual_final_content = (dirs["finalized_dir"] / "v001.json").read_text(encoding="utf-8")
    assert actual_final_content == original_final_content
    assert "TAMPERED" not in actual_final_content

    # Downstream script.json also matches the immutable artifact, not the tampered marker
    downstream_data = json.loads(dirs["script_file"].read_text(encoding="utf-8"))
    assert downstream_data["sections"][0]["narration"] == finalized.sections[0].narration


# ==============================================================================
# Verification B — Version Identity, Immutability & Downstream Resolution
# ==============================================================================

def test_versioning_policy_and_downstream_resolution(v_env):
    """Verify draft vs finalized versioning, immutability, and downstream resolution."""
    service = v_env["script_service"]
    pid = v_env["pid"]

    # Draft 1
    d1 = service.start_script(pid, topic="Gravitational Waves")
    assert d1.version == 1
    assert d1.status == "draft"

    # Draft 2
    d2 = service.revise_script(pid, instruction="Clarify laser interferometry")
    assert d2.version == 2
    assert d2.status == "draft"

    # Accept & Finalize Version 2
    service.accept_draft(pid)
    f2 = service.finalize_script(pid)
    assert f2.version == 2
    assert f2.status == "final"

    # Downstream resolution: resolves v2
    assert service.get_finalized_script(pid).version == 2

    # Draft 3 (revision of finalized script)
    d3 = service.revise_script(pid, instruction="Add visual notes on black hole merger")
    assert d3.version == 3
    assert d3.status == "draft"

    # Downstream consumers STILL resolve v2 while v3 is an active draft!
    assert service.get_finalized_script(pid).version == 2

    # Accept & Finalize Version 3
    service.accept_draft(pid)
    f3 = service.finalize_script(pid)
    assert f3.version == 3

    # Now downstream resolves v3, but v2 remains discoverable and intact
    assert service.get_finalized_script(pid).version == 3
    assert service.get_finalized_script(pid, version=2).version == 2

    # List all finalized versions
    all_final = service.list_finalized_versions(pid)
    assert len(all_final) == 2
    assert [f.version for f in all_final] == [2, 3]


# ==============================================================================
# Verification C — Lifecycle State Transitions (6 Exact Sequences)
# ==============================================================================

def test_sequence_1_accept_revise_attempt_finalization_fails(v_env):
    """Sequence 1: Start -> accept -> revise -> attempt finalization without reaccepting."""
    service = v_env["script_service"]
    pid = v_env["pid"]

    service.start_script(pid, topic="Quantum Entanglement")
    service.accept_draft(pid)
    service.revise_script(pid, instruction="Simplify Bell test")

    # Attempt finalization without re-accepting v2 must fail
    with pytest.raises(ScriptFinalizationError, match="must be explicitly accepted"):
        service.finalize_script(pid)


def test_sequence_2_reject_attempt_finalization_fails(v_env):
    """Sequence 2: Start -> reject -> attempt finalization."""
    service = v_env["script_service"]
    pid = v_env["pid"]

    service.start_script(pid, topic="Quantum Entanglement")
    service.reject_draft(pid, reason="Hook is inaccurate")

    with pytest.raises(ScriptFinalizationError, match="is rejected and cannot be finalized"):
        service.finalize_script(pid)


def test_sequence_3_reject_revise_accept_finalize_succeeds(v_env):
    """Sequence 3: Start -> reject -> revise -> accept -> finalize."""
    service = v_env["script_service"]
    pid = v_env["pid"]

    service.start_script(pid, topic="Quantum Entanglement")
    service.reject_draft(pid, reason="Too confusing")
    service.revise_script(pid, instruction="Make it simpler")
    service.accept_draft(pid)
    finalized = service.finalize_script(pid)

    assert finalized.version == 2
    assert finalized.status == "final"


def test_sequence_4_finalize_revise_reject_preserves_finalized(v_env):
    """Sequence 4: Start -> accept -> finalize -> revise -> reject."""
    service = v_env["script_service"]
    pid = v_env["pid"]

    service.start_script(pid, topic="Dark Matter")
    service.accept_draft(pid)
    f1 = service.finalize_script(pid)
    assert f1.version == 1

    # Revise into v2 and reject v2
    service.revise_script(pid, instruction="Include WIMPs")
    rejected_v2 = service.reject_draft(pid, reason="WIMPs are outdated hypothesis")
    assert rejected_v2.version == 2

    # Finalized v1 remains valid and unchanged
    v1_artifact = service.get_finalized_script(pid, version=1)
    assert v1_artifact.version == 1
    assert v1_artifact.status == "final"
    # Downstream still sees v1
    assert service.get_finalized_script(pid).version == 1


def test_sequence_5_finalize_a_finalize_b_both_discoverable(v_env):
    """Sequence 5: Finalize version A -> create and accept version B -> finalize B."""
    service = v_env["script_service"]
    pid = v_env["pid"]

    service.start_script(pid, topic="Plate Tectonics")
    service.accept_draft(pid)
    fa = service.finalize_script(pid)
    assert fa.version == 1

    service.revise_script(pid, instruction="Discuss Pangea")
    service.accept_draft(pid)
    fb = service.finalize_script(pid)
    assert fb.version == 2

    assert service.get_finalized_script(pid, version=1).title == fa.title
    assert service.get_finalized_script(pid, version=2).title == fb.title
    assert service.get_finalized_script(pid).version == 2


def test_sequence_6_missing_corrupt_or_foreign_project_script(v_env):
    """Sequence 6: Attempt acceptance or finalization when script is missing, corrupt, or foreign."""
    service = v_env["script_service"]
    pid = v_env["pid"]
    dirs = service._get_project_dirs(pid)

    # 1. Missing
    with pytest.raises(ScriptNotFoundError):
        service.accept_draft(pid)
    with pytest.raises(ScriptNotFoundError):
        service.finalize_script(pid)

    # 2. Corrupt
    dirs["current_file"].write_text("{corrupt json", encoding="utf-8")
    with pytest.raises(ScriptStorageError):
        service.accept_draft(pid)
    with pytest.raises(ScriptStorageError):
        service.finalize_script(pid)

    # 3. Foreign project identity
    foreign_script = Script(
        script_id="scr_foreign",
        project_id="proj_SOME_OTHER_PROJECT",
        title="Alien Script",
        version=1,
        status="draft",
        sections=[ScriptSection(id="s1", type="hook", heading="H", narration="Text")],
    )
    dirs["current_file"].write_text(foreign_script.model_dump_json(), encoding="utf-8")
    dirs["state_file"].write_text(json.dumps({"accepted_version": 1, "rejected_version": None}), encoding="utf-8")

    with pytest.raises(ScriptValidationError, match="Script identity mismatch"):
        service.accept_draft(pid)
    with pytest.raises(ScriptValidationError, match="Script identity mismatch"):
        service.finalize_script(pid)


# ==============================================================================
# Verification D — Provider & Contract Validation
# ==============================================================================

def test_invalid_section_roles_rejected(v_env):
    """Verify that section type outside allowed roles (hook, setup, middle, climax, takeaway) is rejected."""
    mock_llm = v_env["mock_llm"]
    service = v_env["script_service"]
    pid = v_env["pid"]

    outline = service.create_outline(pid, topic="Test Topic", allow_unresearched=True)
    bad_role_data = {
        "title": "Bad Role",
        "topic": "Test Topic",
        "target_duration_minutes": 5.0,
        "sections": [
            {"id": "sec_1", "type": "dance_break", "heading": "Dance", "narration": "Narration text 1"},
            {"id": "sec_2", "type": "setup", "heading": "Setup", "narration": "Narration text 2"},
            {"id": "sec_3", "type": "middle", "heading": "Middle", "narration": "Narration text 3"},
            {"id": "sec_4", "type": "climax", "heading": "Climax", "narration": "Narration text 4"},
            {"id": "sec_5", "type": "takeaway", "heading": "Takeaway", "narration": "Narration text 5"},
        ],
    }
    mock_llm.set_raw_response(bad_role_data)
    with pytest.raises(ScriptValidationError, match="Invalid section type|validation"):
        service.start_script(pid, topic="Test Topic", outline=outline)


def test_section_role_case_insensitivity_and_canonical_normalization(v_env):
    """Condition B: Prove case-insensitive valid roles are accepted and stored as canonical lowercase."""
    # 1. Direct unit test of Pydantic model boundary
    s1 = ScriptSection(id="s1", type="HOOK", heading="Hook", narration="Text")
    assert s1.type == "hook"

    s2 = ScriptSection(id="s2", type="TakeAway", heading="Takeaway", narration="Text")
    assert s2.type == "takeaway"

    s3 = ScriptSection(id="s3", type="SETUP", heading="Setup", narration="Text")
    assert s3.type == "setup"

    s4 = ScriptSection(id="s4", type="MiDdle", heading="Middle", narration="Text")
    assert s4.type == "middle"

    s5 = ScriptSection(id="s5", type="CLIMAX", heading="Climax", narration="Text")
    assert s5.type == "climax"

    # Serialization preserves canonical lowercase
    assert s1.model_dump()["type"] == "hook"
    assert json.loads(s1.model_dump_json())["type"] == "hook"
    assert s2.model_dump()["type"] == "takeaway"
    assert json.loads(s2.model_dump_json())["type"] == "takeaway"

    # 2. Rejection of invalid roles
    for bad_role in ["dance_break", "intro", "outro", "random", ""]:
        with pytest.raises(ValueError, match="Invalid section role"):
            ScriptSection(id="sb", type=bad_role, heading="Bad", narration="Text")

    # 3. End-to-end via service generation with mixed-case and uppercase roles
    mock_llm = v_env["mock_llm"]
    service = v_env["script_service"]
    pid = v_env["pid"]

    outline = service.create_outline(pid, topic="Normalization Test", allow_unresearched=True)
    mixed_roles_data = {
        "title": "Normalized Script",
        "topic": "Normalization Test",
        "target_duration_minutes": 5.0,
        "sections": [
            {"id": "sec_1", "type": "HOOK", "heading": "Hook", "narration": "Hook narration"},
            {"id": "sec_2", "type": "SetUp", "heading": "Setup", "narration": "Setup narration"},
            {"id": "sec_3", "type": "MIDDLE", "heading": "Middle", "narration": "Middle narration"},
            {"id": "sec_4", "type": "CliMax", "heading": "Climax", "narration": "Climax narration"},
            {"id": "sec_5", "type": "TakeAway", "heading": "Takeaway", "narration": "Takeaway narration"},
        ],
    }
    mock_llm.set_raw_response(mixed_roles_data)
    script = service.start_script(pid, topic="Normalization Test", outline=outline)

    # All section roles must be canonically lowercase in memory
    assert [s.type for s in script.sections] == ["hook", "setup", "middle", "climax", "takeaway"]

    # All section roles must be canonically lowercase on disk in current.json
    dirs = service._get_project_dirs(pid)
    saved_json = json.loads(dirs["current_file"].read_text(encoding="utf-8"))
    assert [s["type"] for s in saved_json["sections"]] == ["hook", "setup", "middle", "climax", "takeaway"]


def test_status_spoofing_by_llm_is_overridden_to_draft(v_env):
    """Verify that model output attempting to return status='final' is strictly overridden to 'draft'."""
    mock_llm = v_env["mock_llm"]
    service = v_env["script_service"]
    pid = v_env["pid"]

    outline = service.create_outline(pid, topic="Test Topic", allow_unresearched=True)
    spoofed_data = {
        "title": "Spoofed Final",
        "topic": "Test Topic",
        "status": "final",  # Attempting to bypass draft status
        "target_duration_minutes": 5.0,
        "sections": [
            {"id": "sec_1", "type": "hook", "heading": "Hook", "narration": "Narration text 1"},
            {"id": "sec_2", "type": "setup", "heading": "Setup", "narration": "Narration text 2"},
            {"id": "sec_3", "type": "middle", "heading": "Middle", "narration": "Narration text 3"},
            {"id": "sec_4", "type": "climax", "heading": "Climax", "narration": "Narration text 4"},
            {"id": "sec_5", "type": "takeaway", "heading": "Takeaway", "narration": "Narration text 5"},
        ],
    }
    mock_llm.set_raw_response(spoofed_data)
    script = service.start_script(pid, topic="Test Topic", outline=outline)

    # Service MUST enforce draft status
    assert script.status == "draft"


# ==============================================================================
# Verification E — Persistence & Path Traversal Security
# ==============================================================================

def test_path_traversal_blocked_across_all_script_operations(v_env):
    """Verify that path traversal in project_id is blocked across every public service method."""
    service = v_env["script_service"]
    bad_id = "../../../secret"

    with pytest.raises(ProjectSecurityError):
        service.start_script(bad_id, topic="Test")

    with pytest.raises(ProjectSecurityError):
        service.revise_script(bad_id, instruction="Test")

    with pytest.raises(ProjectSecurityError):
        service.accept_draft(bad_id)

    with pytest.raises(ProjectSecurityError):
        service.reject_draft(bad_id)

    with pytest.raises(ProjectSecurityError):
        service.finalize_script(bad_id)

    with pytest.raises(ProjectSecurityError):
        service.get_current_script(bad_id)

    with pytest.raises(ProjectSecurityError):
        service.get_finalized_script(bad_id)

    with pytest.raises(ProjectSecurityError):
        service.list_script_versions(bad_id)

    with pytest.raises(ProjectSecurityError):
        service.list_finalized_versions(bad_id)

    with pytest.raises(ProjectSecurityError):
        service.get_conversation_history(bad_id)
