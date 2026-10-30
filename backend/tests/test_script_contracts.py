"""Focused unit tests for Phase 4 Script domain contracts, claim models, and backward compatibility."""

import json
import pytest
from pydantic import ValidationError

from backend.app.domain.script import (
    Script,
    ScriptClaim,
    ScriptOutline,
    ScriptOutlineSection,
    ScriptSection,
    Section,
)


def test_script_claim_valid_and_link_status_sync():
    """Verify that ScriptClaim validates correctly and synchronizes link_status."""
    # 1. Unlinked claim with no evidence
    claim_unlinked = ScriptClaim(
        claim_id="clm_01",
        claim_text="The first transatlantic telegraph cable operated in 1858.",
        section_id="sec_01",
    )
    assert claim_unlinked.claim_id == "clm_01"
    assert claim_unlinked.link_status == "unlinked"
    assert claim_unlinked.evidence_ids == []
    assert claim_unlinked.source_ids == []

    # 2. Claim with evidence_ids automatically syncs link_status to 'linked' if not specified
    claim_linked = ScriptClaim(
        claim_id="clm_02",
        claim_text="Signals deteriorated rapidly due to high-voltage insulation breakdown.",
        section_id="sec_02",
        evidence_ids=["evi_cable_01", "evi_cable_02"],
        source_ids=["src_history_01"],
    )
    assert claim_linked.link_status == "linked"
    assert len(claim_linked.evidence_ids) == 2

    # 3. Explicit link_status respected
    claim_explicit = ScriptClaim(
        claim_id="clm_03",
        claim_text="Some uncorroborated assertion.",
        section_id="sec_01",
        evidence_ids=["evi_candidate_01"],
        link_status="unlinked",
    )
    assert claim_explicit.link_status == "unlinked"


def test_script_claim_link_status_semantics_and_boundaries():
    """Verify that populating evidence_ids establishes declared references, not storage validation or verification."""
    # 1. Declaring evidence_ids establishes that references are declared (link_status = 'linked')
    unvalidated_evidence_id = "evi_arbitrary_declared_id_not_checked_in_storage"
    claim = ScriptClaim(
        claim_id="clm_99",
        claim_text="Unvalidated claim asserting historical date.",
        section_id="sec_01",
        evidence_ids=[unvalidated_evidence_id],
    )
    assert claim.link_status == "linked"
    assert claim.evidence_ids == [unvalidated_evidence_id]

    # 2. Domain model does NOT perform storage validation:
    # Any syntactically valid ID is accepted by the domain contract without checking disk or database.
    # Storage-level reference validation belongs strictly to the service layer.
    assert claim.evidence_ids[0] == unvalidated_evidence_id

    # 3. link_status does NOT assert substantive factual truth or verification:
    # Claim-level fact-checking and substantive support remain strictly Phase 5 boundaries.
    assert not hasattr(claim, "is_verified_by_research")
    assert not hasattr(claim, "is_fact_checked")
    assert not hasattr(claim, "verification_verdict")


def test_script_claim_malformed_inputs_raise():
    """Verify that malformed IDs, whitespace-only text, or invalid identifiers raise ValidationError."""
    # Empty claim_text
    with pytest.raises(ValidationError, match="claim_text cannot be empty"):
        ScriptClaim(claim_id="clm_01", claim_text="   ", section_id="sec_01")

    # Invalid claim_id (contains spaces/symbols)
    with pytest.raises(ValidationError, match="Invalid claim_id"):
        ScriptClaim(claim_id="bad claim id!", claim_text="Valid text", section_id="sec_01")

    # Invalid section_id
    with pytest.raises(ValidationError, match="Invalid section_id"):
        ScriptClaim(claim_id="clm_01", claim_text="Valid text", section_id="bad id!")

    # Invalid evidence_id in list
    with pytest.raises(ValidationError, match="Invalid identifier"):
        ScriptClaim(
            claim_id="clm_01",
            claim_text="Valid text",
            section_id="sec_01",
            evidence_ids=["valid_id", "bad id*with*symbols"],
        )


def test_script_section_claim_ids_default_and_validation():
    """Verify ScriptSection supports claim_ids with backward-compatible default."""
    # Default is empty list
    sec = ScriptSection(type="hook", heading="Hook", narration="Some hook text.")
    assert sec.claim_ids == []

    # Valid claim_ids
    sec2 = ScriptSection(
        type="setup",
        heading="Setup",
        narration="Some setup text.",
        claim_ids=["clm_01", "clm_02"],
    )
    assert sec2.claim_ids == ["clm_01", "clm_02"]

    # Invalid claim_id raises
    with pytest.raises(ValidationError, match="Invalid claim_id"):
        ScriptSection(
            type="middle",
            heading="Middle",
            narration="Narration text.",
            claim_ids=["invalid claim!?"],
        )


def test_script_outline_and_section_validation():
    """Verify ScriptOutlineSection and ScriptOutline validation rules."""
    # Valid outline section
    sec_out = ScriptOutlineSection(
        type="hook",
        heading="The Cable Dilemma",
        target_words=60,
        factual_milestones=["1858 cable completion", "First message sent"],
        visual_concept="Split-screen of London and New York with steamships",
        evidence_ids=["evi_01", "evi_02"],
    )
    assert sec_out.type == "hook"
    assert sec_out.target_words == 60
    assert len(sec_out.factual_milestones) == 2

    # Invalid section role raises
    with pytest.raises(ValidationError, match="Invalid section role 'invalid_role'"):
        ScriptOutlineSection(type="invalid_role", target_words=50)

    # Non-positive target_words raises
    with pytest.raises(ValidationError, match="greater than 0"):
        ScriptOutlineSection(type="setup", target_words=0)

    with pytest.raises(ValidationError, match="greater than 0"):
        ScriptOutlineSection(type="setup", target_words=-15)

    # Valid outline
    outline = ScriptOutline(
        outline_id="out_telegraph_01",
        project_id="proj_telegraph",
        topic="Transatlantic Cable",
        target_duration_minutes=5.0,
        narrative_structure="chronological",
        sections=[sec_out],
    )
    assert outline.outline_id == "out_telegraph_01"
    assert outline.target_duration_minutes == 5.0

    # Invalid target_duration_minutes raises
    with pytest.raises(ValidationError, match="greater than 0"):
        ScriptOutline(target_duration_minutes=0.0)

    with pytest.raises(ValidationError, match="less than or equal to 60"):
        ScriptOutline(target_duration_minutes=65.0)

    # Malformed outline_id raises
    with pytest.raises(ValidationError, match="Invalid identifier"):
        ScriptOutline(outline_id="invalid/outline/id")


def test_script_claim_section_relationship_validation():
    """Verify clean domain boundary relationship validation between claims and sections."""
    sec1 = ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Hook narration.")
    sec2 = ScriptSection(id="sec_02", type="setup", heading="Setup", narration="Setup narration.")

    claim1 = ScriptClaim(claim_id="clm_01", claim_text="Claim 1", section_id="sec_01")
    claim2 = ScriptClaim(claim_id="clm_02", claim_text="Claim 2", section_id="sec_02")

    # Valid script with valid claims referencing existing sections
    script = Script(
        title="Valid Script",
        topic="Transatlantic Cable",
        sections=[sec1, sec2],
        claims=[claim1, claim2],
    )
    assert len(script.claims) == 2

    # Duplicate claim_id raises ValidationError
    claim_dup = ScriptClaim(claim_id="clm_01", claim_text="Duplicate claim ID", section_id="sec_02")
    with pytest.raises(ValidationError, match="Duplicate claim ID 'clm_01' found"):
        Script(
            title="Duplicate Claim Script",
            topic="Test",
            sections=[sec1, sec2],
            claims=[claim1, claim_dup],
        )

    # Claim referencing non-existent section_id raises ValidationError
    claim_ghost = ScriptClaim(
        claim_id="clm_ghost", claim_text="Orphan claim", section_id="sec_non_existent"
    )
    with pytest.raises(ValidationError, match="references unknown section_id 'sec_non_existent'"):
        Script(
            title="Orphan Claim Script",
            topic="Test",
            sections=[sec1, sec2],
            claims=[claim1, claim_ghost],
        )

    # Section claim_ids referencing an unknown claim_id raises ValidationError
    sec_bad_claim = ScriptSection(
        id="sec_01",
        type="hook",
        heading="Hook",
        narration="Hook narration.",
        claim_ids=["clm_unknown"],
    )
    with pytest.raises(ValidationError, match="references unknown claim_id 'clm_unknown'"):
        Script(
            title="Bad Claim Ref Script",
            topic="Test",
            sections=[sec_bad_claim],
            claims=[claim1],
        )


def test_old_script_json_backward_compatibility():
    """Verify that serialized script JSON from before Phase 4 deserializes cleanly without errors."""
    old_script_json = """{
      "script_id": "scr_old_12345678",
      "project_id": "proj_legacy",
      "version": 1,
      "status": "draft",
      "title": "Legacy Video",
      "topic": "Old Topic",
      "target_duration_minutes": 5.0,
      "sections": [
        {
          "id": "sec_old_01",
          "type": "hook",
          "heading": "Opening",
          "narration": "Hello world and welcome to the explanation.",
          "notes": "Intro card",
          "target_duration_seconds": 15.0
        },
        {
          "id": "sec_old_02",
          "type": "middle",
          "heading": "The Core",
          "narration": "Here is the substantive part of the explanation.",
          "notes": "Diagram animation",
          "target_duration_seconds": 30.0
        }
      ],
      "total_word_count": 14,
      "estimated_duration_seconds": 45.0,
      "created_at": "2026-10-25T10:00:00Z",
      "updated_at": "2026-10-25T10:00:00Z"
    }"""

    # Deserializes without error
    script = Script.model_validate_json(old_script_json)
    assert script.script_id == "scr_old_12345678"
    assert script.research_run_id is None
    assert script.evidence_brief_path is None
    assert script.claims == []
    assert script.outline is None
    assert script.sections[0].claim_ids == []

    # Round trip preserves all fields
    dumped = script.model_dump(mode="json")
    script2 = Script.model_validate(dumped)
    assert script2.script_id == script.script_id
    assert script2.title == script.title
    assert len(script2.sections) == 2


def test_old_finalized_script_json_compatibility():
    """Verify that finalized script JSON without Phase 4 fields deserializes and preserves values."""
    finalized_json = """{
      "script_id": "scr_finalized_99",
      "project_id": "proj_finalized",
      "version": 3,
      "status": "final",
      "title": "Finalized Explainer",
      "topic": "Final Topic",
      "target_duration_minutes": 3.0,
      "sections": [
        {
          "id": "sec_fin_01",
          "type": "hook",
          "heading": "Hook Headline",
          "narration": "Why did this breakthrough matter?",
          "notes": "Action pose",
          "target_duration_seconds": 10.0
        }
      ],
      "total_word_count": 5,
      "estimated_duration_seconds": 10.0,
      "created_at": "2026-10-26T12:00:00Z",
      "updated_at": "2026-10-26T14:00:00Z"
    }"""

    script = Script.model_validate_json(finalized_json)
    assert script.status == "final"
    assert script.version == 3
    assert script.research_run_id is None
    assert script.claims == []
    assert script.sections[0].claim_ids == []


def test_script_with_phase4_fields_round_trip():
    """Verify that a Script with research_run_id, evidence_brief_path, outline, and claims round-trips cleanly."""
    outline_sec = ScriptOutlineSection(
        type="hook",
        heading="Hook Heading",
        target_words=50,
        factual_milestones=["Milestone A"],
        evidence_ids=["evi_01"],
    )
    outline = ScriptOutline(
        outline_id="out_01",
        project_id="proj_01",
        topic="Round Trip Topic",
        sections=[outline_sec],
    )

    sec = ScriptSection(
        id="sec_01",
        type="hook",
        heading="Hook Heading",
        narration="This is the spoken narration.",
        claim_ids=["clm_01"],
    )
    claim = ScriptClaim(
        claim_id="clm_01",
        claim_text="This is a verified claim from source.",
        section_id="sec_01",
        evidence_ids=["evi_01"],
        source_ids=["src_01"],
        link_status="linked",
    )

    script = Script(
        script_id="scr_p4_01",
        project_id="proj_01",
        version=1,
        title="Evidence-Grounded Explainer",
        topic="Round Trip Topic",
        research_run_id="rrun_01j7telegraph",
        evidence_brief_path="evidence_brief.md",
        outline=outline,
        sections=[sec],
        claims=[claim],
    )

    data = script.model_dump(mode="json")
    restored = Script.model_validate(data)

    assert restored.research_run_id == "rrun_01j7telegraph"
    assert restored.evidence_brief_path == "evidence_brief.md"
    assert restored.outline is not None
    assert restored.outline.outline_id == "out_01"
    assert len(restored.claims) == 1
    assert restored.claims[0].claim_id == "clm_01"
    assert restored.claims[0].link_status == "linked"
    assert restored.sections[0].claim_ids == ["clm_01"]
