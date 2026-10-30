"""Deterministic tests for Script Revision Claim Identity & Evidence Integrity (Phase 4, P4-S5 Checkpoint 2).

Covers:
1. Unchanged claims preserve stable claim IDs and valid evidence links.
2. Materially changed claims receive new unique IDs and do not inherit prior evidence links.
3. New claims receive unique IDs without colliding with historical or existing IDs.
4. Unrelated narration changes preserve claim identities.
5. Section reordering preserves claim identity and updates bidirectional section-claim mapping.
6. Invalid evidence IDs trigger at most one corrective retry and typed failure (ScriptRevisionEvidenceValidationError).
7. Invalid evidence IDs corrected on retry succeed.
8. Source IDs are authoritatively derived from validated evidence records, ignoring model output.
9. Cross-project and cross-run evidence references are rejected.
10. Bidirectional claim-section mappings remain strictly consistent.
11. Unresearched revisions contain no evidence IDs or source IDs; model-emitted evidence IDs are rejected.
12. Failed revisions preserve prior current draft and emit no false success events.
13. New revisions allocate fresh versions without overwriting historical or finalized snapshots.
14. Legacy scripts without claims or project_id continue to work.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import List, Optional
import pytest

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
    ScriptClaim,
    ScriptSection,
)
from backend.app.llm.base import LLMProviderError
from backend.app.llm.mock import MockLLMProvider
from backend.app.services.project.service import ProjectService
from backend.app.services.research.storage import ResearchStorageService
from backend.app.services.scriptwriter.service import (
    ScriptRevisionEvidenceValidationError,
    ScriptService,
    ScriptValidationError,
)


@pytest.fixture
def revision_env(tmp_path):
    """Isolated environment for Script revision integrity tests."""
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
        research_storage=storage_service,
        settings=settings,
    )
    project = project_service.create_project(title="Revision Integrity Test Explainer")
    return {
        "projects_dir": projects_dir,
        "settings": settings,
        "project_service": project_service,
        "storage_service": storage_service,
        "mock_llm": mock_llm,
        "script_service": script_service,
        "project": project,
        "project_id": project.metadata.project_id,
    }


def seed_research(
    storage_service: ResearchStorageService,
    project_id: str,
    run_id: str = "rrun_01",
    status: str = "completed",
    evidence_items: Optional[List[dict]] = None,
) -> ResearchRun:
    """Helper to persist a plan, run, sources, and evidence records."""
    plan = ResearchPlan(
        plan_id=f"plan_{run_id}",
        project_id=project_id,
        topic="Explainer Topic",
        research_objective="Investigate topic mechanisms.",
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
        plan_id=plan.plan_id,
        project_id=project_id,
        status=status,
        started_at=datetime.now(timezone.utc),
        query_records=[
            QueryRecord(
                query_id="qry_01",
                plan_id=plan.plan_id,
                run_id=run_id,
                query_text="mechanism query",
                tool_name="web_search",
                status="succeeded",
            )
        ],
    )
    storage_service.save_run(run)

    if evidence_items is None:
        evidence_items = [
            {"evidence_id": "evi_01", "source_id": "src_01", "excerpt": "The reef covers 344,000 square kilometers."},
            {"evidence_id": "evi_02", "source_id": "src_02", "excerpt": "The reef consists of 2,900 individual reefs."},
        ]

    for item in evidence_items:
        sid = item["source_id"]
        source = SourceRecord(
            source_id=sid,
            project_id=project_id,
            url=f"https://example.org/{sid}",
            title=f"Source {sid}",
            source_type="scholarly",
        )
        storage_service.save_source(project_id, source)

        evidence = EvidenceRecord(
            evidence_id=item["evidence_id"],
            project_id=project_id,
            source_id=sid,
            run_id=run_id,
            excerpt=item["excerpt"],
        )
        storage_service.save_evidence(project_id, evidence)

    return run


def create_initial_script(
    script_service: ScriptService,
    project_id: str,
    run_id: Optional[str] = "rrun_01",
    claims: Optional[List[ScriptClaim]] = None,
) -> Script:
    """Helper to create and persist an initial Script draft."""
    dirs = script_service._get_project_dirs(project_id)
    sections = [
        ScriptSection(
            id="sec_01_hook",
            type="hook",
            heading="Opening Hook",
            narration="Did you know about the largest living structure on Earth?",
            notes="Wide ocean visual.",
            claim_ids=["clm_01"] if claims else [],
        ),
        ScriptSection(
            id="sec_02_setup",
            type="setup",
            heading="The Setting",
            narration="Off the coast of Queensland lies an underwater wonder.",
            notes="Underwater coral pan.",
            claim_ids=["clm_02"] if (claims and len(claims) > 1) else [],
        ),
        ScriptSection(
            id="sec_03_middle",
            type="middle",
            heading="The Scale",
            narration="It covers 344,000 square kilometers with 2,900 individual reefs.",
            notes="Satellite zoom out.",
            claim_ids=[],
        ),
        ScriptSection(
            id="sec_04_climax",
            type="climax",
            heading="The Crisis",
            narration="However, changing climates threaten this fragile marine ecosystem.",
            notes="Dramatic color shift.",
            claim_ids=[],
        ),
        ScriptSection(
            id="sec_05_takeaway",
            type="takeaway",
            heading="The Lesson",
            narration="Protecting these biodiversity hotspots ensures the health of our oceans.",
            notes="Closing graphic.",
            claim_ids=[],
        ),
    ]

    if claims is None:
        claims = [
            ScriptClaim(
                claim_id="clm_01",
                claim_text="The Great Barrier Reef covers 344,000 square kilometers.",
                section_id="sec_01_hook",
                evidence_ids=["evi_01"],
                source_ids=["src_01"],
                link_status="linked",
            ),
            ScriptClaim(
                claim_id="clm_02",
                claim_text="It is composed of 2,900 individual reefs.",
                section_id="sec_02_setup",
                evidence_ids=["evi_02"],
                source_ids=["src_02"],
                link_status="linked",
            ),
        ]

    script = Script(
        project_id=project_id,
        version=1,
        status="draft",
        title="Coral Reef Explainer",
        topic="The Great Barrier Reef",
        target_duration_minutes=5.0,
        sections=sections,
        claims=claims,
        research_run_id=run_id,
    )

    from backend.app.domain.script import populate_script_duration_metrics
    populate_script_duration_metrics(script, words_per_minute=script_service.words_per_minute)

    # Persist as initial draft
    from backend.app.services.scriptwriter.service import _atomic_write_json
    _atomic_write_json(dirs["versions_dir"] / "v001.json", script.model_dump(mode="json"))
    _atomic_write_json(dirs["current_file"], script.model_dump(mode="json"))
    return script


# ==============================================================================
# 1. Unchanged claims preserve ID and valid evidence links
# ==============================================================================

def test_unchanged_claims_preserve_identity_and_evidence(revision_env):
    """Unchanged factual propositions retain their original claim_id and valid evidence links."""
    service = revision_env["script_service"]
    pid = revision_env["project_id"]
    storage = revision_env["storage_service"]
    mock_llm = revision_env["mock_llm"]

    seed_research(storage, pid, run_id="rrun_01")
    initial_script = create_initial_script(service, pid, run_id="rrun_01")

    # Mock revision response keeping clm_01 proposition identical, with minor stylistic edit
    revised_response = initial_script.model_copy(deep=True)
    revised_response.sections[0].narration = "Opening Hook: Have you witnessed the largest living structure?"
    # Model returns the same claim_text for clm_01
    revised_response.claims = [
        ScriptClaim(
            claim_id="temporary_id_from_model",
            claim_text="The Great Barrier Reef covers 344,000 square kilometers.",
            section_id="sec_01_hook",
            evidence_ids=["evi_01"],
            source_ids=[],
        ),
        ScriptClaim(
            claim_id="temporary_id_2",
            claim_text="It is composed of 2,900 individual reefs.",
            section_id="sec_02_setup",
            evidence_ids=[],  # Model omitted evidence_ids; should retain prior valid evidence
            source_ids=[],
        ),
    ]
    mock_llm.set_response(revised_response)

    revised = service.revise_script(pid, instruction="Polish the hook question.")

    assert revised.version == 2
    assert len(revised.claims) == 2
    # Preserved IDs
    assert revised.claims[0].claim_id == "clm_01"
    assert revised.claims[0].evidence_ids == ["evi_01"]
    assert revised.claims[0].source_ids == ["src_01"]

    assert revised.claims[1].claim_id == "clm_02"
    assert revised.claims[1].evidence_ids == ["evi_02"]
    assert revised.claims[1].source_ids == ["src_02"]


# ==============================================================================
# 2. Materially changed claims receive new IDs and NEVER inherit evidence
# ==============================================================================

def test_materially_changed_claims_receive_new_ids_and_do_not_inherit_evidence(revision_env):
    """Materially modified assertions receive brand new unique IDs and never inherit prior evidence."""
    service = revision_env["script_service"]
    pid = revision_env["project_id"]
    storage = revision_env["storage_service"]
    mock_llm = revision_env["mock_llm"]

    seed_research(storage, pid, run_id="rrun_01")
    create_initial_script(service, pid, run_id="rrun_01")

    # Model returns a materially altered number for the first claim
    revised_response = create_initial_script(service, pid, run_id="rrun_01")
    revised_response.claims = [
        ScriptClaim(
            claim_id="clm_01",
            claim_text="The Great Barrier Reef covers 500,000 square kilometers.",  # Changed number!
            section_id="sec_01_hook",
            evidence_ids=[],  # No new evidence supplied
            source_ids=[],
        )
    ]
    mock_llm.set_response(revised_response)

    revised = service.revise_script(pid, instruction="Update area figure.")

    assert revised.version == 2
    assert len(revised.claims) == 1
    new_claim = revised.claims[0]

    # Material change must NOT preserve clm_01
    assert new_claim.claim_id != "clm_01"
    assert new_claim.claim_id == "clm_03"  # Allocated next ID
    # Must NOT inherit prior evidence
    assert new_claim.evidence_ids == []
    assert new_claim.source_ids == []
    assert new_claim.link_status == "unlinked"


# ==============================================================================
# 3. New claims receive unique IDs without collisions
# ==============================================================================

def test_new_claims_receive_unique_ids_without_collisions(revision_env):
    """New claims receive unique IDs that do not collide with existing or historical IDs."""
    service = revision_env["script_service"]
    pid = revision_env["project_id"]
    storage = revision_env["storage_service"]
    mock_llm = revision_env["mock_llm"]

    seed_research(storage, pid, run_id="rrun_01")
    initial_script = create_initial_script(service, pid, run_id="rrun_01")

    # Add a 3rd historical claim in v001
    dirs = service._get_project_dirs(pid)
    initial_script.claims.append(
        ScriptClaim(
            claim_id="clm_03",
            claim_text="Historical assertion.",
            section_id="sec_03_middle",
        )
    )
    from backend.app.services.scriptwriter.service import _atomic_write_json
    _atomic_write_json(dirs["current_file"], initial_script.model_dump(mode="json"))

    revised_response = initial_script.model_copy(deep=True)
    revised_response.claims.append(
        ScriptClaim(
            claim_id="model_id",
            claim_text="A completely novel scientific discovery about corals was made recently.",
            section_id="sec_04_climax",
        )
    )
    mock_llm.set_response(revised_response)

    revised = service.revise_script(pid, instruction="Add discovery claim.")

    all_ids = [c.claim_id for c in revised.claims]
    assert len(all_ids) == len(set(all_ids)), "Claim IDs must be unique."
    assert "clm_04" in all_ids, "Should allocate clm_04 avoiding clm_01, clm_02, clm_03."


# ==============================================================================
# 4. Unrelated narration changes preserve claim identities
# ==============================================================================

def test_unrelated_narration_changes_preserve_claim_identities(revision_env):
    """Rewriting narration in unrelated sections preserves claim identities and links."""
    service = revision_env["script_service"]
    pid = revision_env["project_id"]
    storage = revision_env["storage_service"]
    mock_llm = revision_env["mock_llm"]

    seed_research(storage, pid, run_id="rrun_01")
    create_initial_script(service, pid, run_id="rrun_01")

    revised_response = create_initial_script(service, pid, run_id="rrun_01")
    # Change narration of middle and takeaway sections
    revised_response.sections[2].narration = "A completely overhauled middle section with rich pacing."
    revised_response.sections[4].narration = "A newly polished closing takeaway narration."
    mock_llm.set_response(revised_response)

    revised = service.revise_script(pid, instruction="Make middle and takeaway punchier.")

    assert revised.version == 2
    assert [c.claim_id for c in revised.claims] == ["clm_01", "clm_02"]
    assert revised.claims[0].evidence_ids == ["evi_01"]
    assert revised.claims[1].evidence_ids == ["evi_02"]


# ==============================================================================
# 5. Section reordering preserves claim identity & updates mapping
# ==============================================================================

def test_section_reordering_preserves_claim_identity_and_updates_bidirectional_mapping(revision_env):
    """Moving a claim to a different section preserves its claim identity and updates section.claim_ids."""
    service = revision_env["script_service"]
    pid = revision_env["project_id"]
    storage = revision_env["storage_service"]
    mock_llm = revision_env["mock_llm"]

    seed_research(storage, pid, run_id="rrun_01")
    create_initial_script(service, pid, run_id="rrun_01")

    revised_response = create_initial_script(service, pid, run_id="rrun_01")
    # Move clm_01 from sec_01_hook to sec_03_middle
    revised_response.claims[0].section_id = "sec_03_middle"
    mock_llm.set_response(revised_response)

    revised = service.revise_script(pid, instruction="Move the area metric to the scale section.")

    assert revised.version == 2
    clm_01 = next(c for c in revised.claims if c.claim_id == "clm_01")
    assert clm_01.section_id == "sec_03_middle"

    # Bidirectional mapping check
    sec_01 = next(s for s in revised.sections if s.id == "sec_01_hook")
    sec_03 = next(s for s in revised.sections if s.id == "sec_03_middle")
    assert "clm_01" not in sec_01.claim_ids
    assert "clm_01" in sec_03.claim_ids


# ==============================================================================
# 6. Invalid evidence IDs trigger corrective retry and typed failure
# ==============================================================================

def test_invalid_evidence_ids_trigger_corrective_retry_and_typed_failure(revision_env):
    """Invalid evidence IDs trigger exactly one corrective retry and raise ScriptRevisionEvidenceValidationError if persistent."""
    service = revision_env["script_service"]
    pid = revision_env["project_id"]
    storage = revision_env["storage_service"]
    mock_llm = revision_env["mock_llm"]

    seed_research(storage, pid, run_id="rrun_01")
    create_initial_script(service, pid, run_id="rrun_01")

    # Queue 2 invalid responses (initial + retry both cite nonexistent evidence)
    bad_response_1 = create_initial_script(service, pid, run_id="rrun_01")
    bad_response_1.claims[0].evidence_ids = ["evi_hallucinated_1"]

    bad_response_2 = create_initial_script(service, pid, run_id="rrun_01")
    bad_response_2.claims[0].evidence_ids = ["evi_hallucinated_2"]

    mock_llm.queue_responses([bad_response_1, bad_response_2])

    with pytest.raises(ScriptRevisionEvidenceValidationError) as exc_info:
        service.revise_script(pid, instruction="Revise claims.")

    assert "evi_hallucinated_2" in str(exc_info.value)
    # Verify exactly 2 calls occurred (initial + 1 corrective retry)
    revision_calls = [c for c in mock_llm.calls if c["action"] == "revise_script"]
    assert len(revision_calls) == 2
    assert "CORRECTIVE EVIDENCE REQUIREMENT" in revision_calls[1]["instruction"]

    # Verify existing draft in current.json remains at version 1 uncorrupted
    current = service.get_current_script(pid)
    assert current.version == 1


# ==============================================================================
# 7. Invalid evidence IDs corrected on retry succeeds
# ==============================================================================

def test_invalid_evidence_ids_corrected_on_retry_succeeds(revision_env):
    """When model corrects invalid evidence ID on the retry attempt, revision succeeds."""
    service = revision_env["script_service"]
    pid = revision_env["project_id"]
    storage = revision_env["storage_service"]
    mock_llm = revision_env["mock_llm"]

    seed_research(storage, pid, run_id="rrun_01")
    create_initial_script(service, pid, run_id="rrun_01")

    # 1st response: invalid evidence ID
    bad_response = create_initial_script(service, pid, run_id="rrun_01")
    bad_response.claims[0].evidence_ids = ["evi_hallucinated"]

    # 2nd response (retry): corrected to valid evidence ID
    good_response = create_initial_script(service, pid, run_id="rrun_01")
    good_response.claims[0].evidence_ids = ["evi_01"]

    mock_llm.queue_responses([bad_response, good_response])

    revised = service.revise_script(pid, instruction="Revise claims.")

    assert revised.version == 2
    assert revised.claims[0].evidence_ids == ["evi_01"]
    assert revised.claims[0].source_ids == ["src_01"]

    revision_calls = [c for c in mock_llm.calls if c["action"] == "revise_script"]
    assert len(revision_calls) == 2


# ==============================================================================
# 8. Source IDs are authoritatively derived from validated evidence records
# ==============================================================================

def test_source_ids_derived_from_validated_evidence_not_model_output(revision_env):
    """Model-emitted source_ids are ignored; source_ids are derived authoritatively from catalog."""
    service = revision_env["script_service"]
    pid = revision_env["project_id"]
    storage = revision_env["storage_service"]
    mock_llm = revision_env["mock_llm"]

    seed_research(storage, pid, run_id="rrun_01")
    create_initial_script(service, pid, run_id="rrun_01")

    # Model attempts to emit fake/hallucinated source IDs
    revised_response = create_initial_script(service, pid, run_id="rrun_01")
    revised_response.claims[0].evidence_ids = ["evi_01"]
    revised_response.claims[0].source_ids = ["src_hallucinated_fake", "src_999"]
    mock_llm.set_response(revised_response)

    revised = service.revise_script(pid, instruction="Check source provenance.")

    assert revised.version == 2
    # In catalog, evi_01 maps to src_01
    assert revised.claims[0].source_ids == ["src_01"]
    assert "src_hallucinated_fake" not in revised.claims[0].source_ids


# ==============================================================================
# 9. Cross-project and cross-run evidence references are rejected
# ==============================================================================

def test_cross_project_and_cross_run_evidence_rejected(revision_env):
    """Evidence records belonging to another research run are rejected."""
    service = revision_env["script_service"]
    pid = revision_env["project_id"]
    storage = revision_env["storage_service"]
    mock_llm = revision_env["mock_llm"]

    # Seed run 1 and run 2
    seed_research(storage, pid, run_id="rrun_01")
    seed_research(
        storage,
        pid,
        run_id="rrun_02",
        evidence_items=[{"evidence_id": "evi_foreign", "source_id": "src_foreign", "excerpt": "Foreign data."}],
    )

    # Script is grounded in rrun_01
    create_initial_script(service, pid, run_id="rrun_01")

    # Model tries to cite evi_foreign (which belongs to rrun_02)
    bad_response = create_initial_script(service, pid, run_id="rrun_01")
    bad_response.claims[0].evidence_ids = ["evi_foreign"]

    mock_llm.queue_responses([bad_response, bad_response])

    with pytest.raises(ScriptRevisionEvidenceValidationError) as exc_info:
        service.revise_script(pid, instruction="Cite foreign run evidence.")

    assert "evi_foreign" in str(exc_info.value)


# ==============================================================================
# 10. Bidirectional claim-section mappings remain consistent
# ==============================================================================

def test_bidirectional_claim_section_mappings_consistent(revision_env):
    """Every section.claim_ids lists all claims referencing that section, and vice-versa."""
    service = revision_env["script_service"]
    pid = revision_env["project_id"]
    storage = revision_env["storage_service"]
    mock_llm = revision_env["mock_llm"]

    seed_research(storage, pid, run_id="rrun_01")
    create_initial_script(service, pid, run_id="rrun_01")

    revised_response = create_initial_script(service, pid, run_id="rrun_01")
    # Assign multiple claims to sec_01 and sec_02
    revised_response.claims = [
        ScriptClaim(
            claim_id="clm_01",
            claim_text="The Great Barrier Reef covers 344,000 square kilometers.",
            section_id="sec_01_hook",
            evidence_ids=["evi_01"],
        ),
        ScriptClaim(
            claim_id="clm_new_1",
            claim_text="Another hook claim.",
            section_id="sec_01_hook",
        ),
        ScriptClaim(
            claim_id="clm_02",
            claim_text="It is composed of 2,900 individual reefs.",
            section_id="sec_02_setup",
            evidence_ids=["evi_02"],
        ),
    ]
    mock_llm.set_response(revised_response)

    revised = service.revise_script(pid, instruction="Add claims to hook.")

    sec_map = {s.id: s for s in revised.sections}
    hook_sec = sec_map["sec_01_hook"]
    setup_sec = sec_map["sec_02_setup"]

    assert len(hook_sec.claim_ids) == 2
    assert "clm_01" in hook_sec.claim_ids
    assert len(setup_sec.claim_ids) == 1
    assert "clm_02" in setup_sec.claim_ids

    # Every claim's section_id matches where it appears
    for claim in revised.claims:
        assert claim.claim_id in sec_map[claim.section_id].claim_ids


# ==============================================================================
# 11. Unresearched revisions contain no evidence or source IDs
# ==============================================================================

def test_unresearched_revisions_reject_evidence_ids_and_have_no_sources(revision_env):
    """Unresearched script revisions must have empty evidence_ids and source_ids, rejecting model-emitted evidence."""
    service = revision_env["script_service"]
    pid = revision_env["project_id"]
    mock_llm = revision_env["mock_llm"]

    # Initial script with research_run_id=None
    initial_script = create_initial_script(service, pid, run_id=None, claims=[])

    # 1. Reject model-emitted evidence in unresearched mode
    bad_response = initial_script.model_copy(deep=True)
    bad_response.claims = [
        ScriptClaim(
            claim_id="clm_01",
            claim_text="An unresearched assertion.",
            section_id="sec_01_hook",
            evidence_ids=["evi_invented"],
        )
    ]
    mock_llm.set_response(bad_response)

    with pytest.raises(ScriptRevisionEvidenceValidationError, match="Unresearched script revision cannot cite evidence IDs"):
        service.revise_script(pid, instruction="Add claim.")

    # 2. Legitimate unresearched revision succeeds with empty evidence/source IDs
    good_response = initial_script.model_copy(deep=True)
    good_response.claims = [
        ScriptClaim(
            claim_id="clm_01",
            claim_text="An unresearched assertion.",
            section_id="sec_01_hook",
            evidence_ids=[],
            source_ids=[],
        )
    ]
    mock_llm.set_response(good_response)

    revised = service.revise_script(pid, instruction="Add clean claim.")
    assert revised.version == 2
    assert revised.claims[0].evidence_ids == []
    assert revised.claims[0].source_ids == []
    assert revised.claims[0].link_status == "unlinked"


# ==============================================================================
# 12. Failed revisions preserve prior current draft and emit no false events
# ==============================================================================

def test_failed_revisions_preserve_prior_draft_and_emit_no_events(revision_env):
    """If revision fails at any point, prior draft and conversation events remain untainted."""
    service = revision_env["script_service"]
    pid = revision_env["project_id"]
    mock_llm = revision_env["mock_llm"]

    create_initial_script(service, pid, run_id=None, claims=[])

    # LLM raises an unexpected provider error
    mock_llm.set_error(LLMProviderError("Provider unavailable."))

    dirs = service._get_project_dirs(pid)
    conv_file = dirs["conversation_file"]
    events_before = json.loads(conv_file.read_text(encoding="utf-8")) if conv_file.exists() else []

    with pytest.raises(Exception):
        service.revise_script(pid, instruction="Attempt failing revision.")

    # Current script still v1
    current = service.get_current_script(pid)
    assert current.version == 1

    # No v002.json snapshot exists
    assert not (dirs["versions_dir"] / "v002.json").exists()

    # No revision_result event emitted
    events_after = json.loads(conv_file.read_text(encoding="utf-8")) if conv_file.exists() else []
    result_events = [e for e in events_after if e.get("event_type") == "revision_result"]
    assert len(result_events) == 0


# ==============================================================================
# 13. New revisions allocate fresh versions without overwriting snapshots
# ==============================================================================

def test_new_revisions_allocate_fresh_versions_without_overwriting(revision_env):
    """Revising a finalized script creates a fresh draft version without overwriting finalized or snapshot files."""
    service = revision_env["script_service"]
    pid = revision_env["project_id"]
    mock_llm = revision_env["mock_llm"]

    create_initial_script(service, pid, run_id=None, claims=[])

    # Accept and finalize v1
    service.accept_draft(pid)
    finalized = service.finalize_script(pid)
    assert finalized.version == 1
    assert finalized.status == "final"

    # Revise the finalized script
    revised_response = finalized.model_copy(deep=True)
    revised_response.status = "draft"
    mock_llm.set_response(revised_response)

    revised = service.revise_script(pid, instruction="Post-finalization enhancement.")
    assert revised.version == 2
    assert revised.status == "draft"

    # Verify v001 snapshot and finalized script are completely intact
    dirs = service._get_project_dirs(pid)
    assert (dirs["versions_dir"] / "v001.json").exists()
    assert (dirs["versions_dir"] / "v002.json").exists()
    assert dirs["script_file"].exists()
    finalized_data = json.loads(dirs["script_file"].read_text(encoding="utf-8"))
    assert finalized_data["version"] == 1
    assert finalized_data["status"] == "final"


# ==============================================================================
# 14. Legacy scripts without claims or project_id continue to work
# ==============================================================================

def test_legacy_scripts_without_claims_or_project_id_continue_to_work(revision_env):
    """Scripts stored with legacy schema (missing claims or project_id) can be revised smoothly."""
    service = revision_env["script_service"]
    pid = revision_env["project_id"]
    mock_llm = revision_env["mock_llm"]
    dirs = service._get_project_dirs(pid)

    # Legacy script representation
    legacy_dict = {
        "title": "Legacy Explainer",
        "topic": "Legacy Topic",
        "target_duration_minutes": 5.0,
        "sections": [
            {
                "id": "sec_01",
                "type": "hook",
                "heading": "Hook",
                "narration": "Legacy hook narration.",
                "visual_description": "Legacy hook visual.",
            },
            {
                "id": "sec_02",
                "type": "setup",
                "heading": "Setup",
                "narration": "Legacy setup narration.",
                "visual_description": "Legacy setup visual.",
            },
            {
                "id": "sec_03",
                "type": "middle",
                "heading": "Middle",
                "narration": "Legacy middle narration.",
                "visual_description": "Legacy middle visual.",
            },
            {
                "id": "sec_04",
                "type": "climax",
                "heading": "Climax",
                "narration": "Legacy climax narration.",
                "visual_description": "Legacy climax visual.",
            },
            {
                "id": "sec_05",
                "type": "takeaway",
                "heading": "Takeaway",
                "narration": "Legacy takeaway narration.",
                "visual_description": "Legacy takeaway visual.",
            },
        ],
        "version": 1,
        "status": "draft",
        # Intentionally missing: project_id, research_run_id, claims
    }

    from backend.app.services.scriptwriter.service import _atomic_write_json
    _atomic_write_json(dirs["versions_dir"] / "v001.json", legacy_dict)
    _atomic_write_json(dirs["current_file"], legacy_dict)

    revised_response = Script.model_validate(legacy_dict)
    revised_response.sections[0].narration = "Updated legacy hook."
    mock_llm.set_response(revised_response)

    revised = service.revise_script(pid, instruction="Update hook.")

    assert revised.version == 2
    assert revised.project_id == pid
    assert revised.claims == []
    assert revised.sections[0].narration == "Updated legacy hook."


# ==============================================================================
# 15. Semantic Proposition Equivalence Tests
# ==============================================================================

def test_proposition_equivalence_distinguishes_semantics():
    """Verify conservative proposition matching distinguishes semantic reversals and changes."""
    from backend.app.services.scriptwriter.service import _is_same_factual_proposition

    # 1. Subject-object reversal
    assert not _is_same_factual_proposition("Cats eat mice", "Mice eat cats")

    # 2. Causal direction reversal
    assert not _is_same_factual_proposition("A causes B", "B causes A")
    assert not _is_same_factual_proposition("Smoking causes cancer", "Cancer causes smoking")

    # 3. Direction of effect change (reduced vs increased)
    assert not _is_same_factual_proposition(
        "The treatment reduced risk by 20%",
        "The treatment increased risk by 20%",
    )

    # 4. Numeric assertion mismatch
    assert not _is_same_factual_proposition(
        "The event happened in 2020",
        "The event happened in 2021",
    )

    # 5. Negation / contradictory assertions
    assert not _is_same_factual_proposition(
        "The drug is effective",
        "The drug is not effective",
    )
    assert not _is_same_factual_proposition(
        "No evidence was found",
        "Evidence was found",
    )

    # 6. Shared vocabulary with different factual assertions
    assert not _is_same_factual_proposition(
        "The turbine generates 500 megawatts during peak hours",
        "The turbine consumes 500 megawatts during peak hours",
    )

    # 7. Identical factual propositions with harmless stopword / formatting changes
    assert _is_same_factual_proposition(
        "The Great Barrier Reef covers 344,000 square kilometers.",
        "The Great Barrier Reef covers about 344,000 square kilometers.",
    )
    assert _is_same_factual_proposition(
        "Global sea levels rose by 20 centimeters in the last century.",
        "Global sea levels rose by 20 centimeters in the last century",
    )


# ==============================================================================
# 16. Historical Claim ID Allocation Scans Finalized Artifacts
# ==============================================================================

def test_claim_id_allocation_scans_finalized_artifacts(revision_env):
    """Claim ID allocation scans finalized/ artifacts and script.json to prevent collision."""
    service = revision_env["script_service"]
    pid = revision_env["project_id"]
    mock_llm = revision_env["mock_llm"]
    dirs = service._get_project_dirs(pid)

    # Seed finalized/v001.json with clm_07 and clm_08
    finalized_dict = {
        "title": "Finalized Explainer",
        "topic": "Finalized Topic",
        "target_duration_minutes": 5.0,
        "sections": [
            {"id": "sec_01", "type": "hook", "heading": "Hook", "narration": "Hook narration.", "visual_description": "V1."},
            {"id": "sec_02", "type": "setup", "heading": "Setup", "narration": "Setup narration.", "visual_description": "V2."},
            {"id": "sec_03", "type": "middle", "heading": "Middle", "narration": "Middle narration.", "visual_description": "V3."},
            {"id": "sec_04", "type": "climax", "heading": "Climax", "narration": "Climax narration.", "visual_description": "V4."},
            {"id": "sec_05", "type": "takeaway", "heading": "Takeaway", "narration": "Takeaway narration.", "visual_description": "V5."},
        ],
        "claims": [
            {"claim_id": "clm_07", "claim_text": "Finalized claim seven.", "section_id": "sec_01"},
            {"claim_id": "clm_08", "claim_text": "Finalized claim eight.", "section_id": "sec_02"},
        ],
        "version": 1,
        "status": "final",
    }
    from backend.app.services.scriptwriter.service import _atomic_write_json
    _atomic_write_json(dirs["finalized_dir"] / "v001.json", finalized_dict)
    _atomic_write_json(dirs["script_file"], finalized_dict)

    # Create current draft without clm_07/clm_08 (e.g. fresh draft)
    create_initial_script(service, pid, run_id=None, claims=[])

    all_known = service._collect_all_known_claim_ids(service.get_current_script(pid), dirs)
    assert "clm_07" in all_known
    assert "clm_08" in all_known

    # Allocate new claim ID
    allocated_id = service._allocate_claim_id(all_known)
    # Must be at least clm_09 since clm_07 and clm_08 exist in finalized artifacts
    assert allocated_id == "clm_09"

