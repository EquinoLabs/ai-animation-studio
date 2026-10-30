"""Tests for Phase 5 Checkpoint 1: Claim Fact-Checking Domain Contracts & Storage."""

import json
from pathlib import Path
import pytest
from pydantic import ValidationError

from backend.app.config import Settings
from backend.app.domain.fact_check import (
    ClaimAssessment,
    ClaimAssessmentStatus,
    ClaimRiskLevel,
    RepairAction,
    ScriptFactCheckReport,
    CLAIM_ASSESSMENT_STATUSES,
    CLAIM_RISK_LEVELS,
)
from backend.app.domain.script import (
    Script,
    ScriptClaim,
    ScriptSection,
)
from backend.app.services.project.service import ProjectNotFoundError, ProjectService
from backend.app.services.scriptwriter.service import (
    ScriptNotFoundError,
    ScriptService,
    ScriptStorageError,
    ScriptValidationError,
)


@pytest.fixture
def env(tmp_path):
    """Isolated environment for fact-check domain and storage testing."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(PROJECTS_DIR=projects_dir, LLM_SCRIPT_PROVIDER="mock")
    project_service = ProjectService(projects_root=projects_dir)
    script_service = ScriptService(project_service=project_service, settings=settings)
    project = project_service.create_project(title="Fact Check Storage Test")
    return {
        "projects_dir": projects_dir,
        "project_service": project_service,
        "script_service": script_service,
        "project": project,
        "project_id": project.metadata.project_id,
    }


# ==============================================================================
# 1. Domain Contracts: Statuses and Validation
# ==============================================================================

def test_all_six_assessment_statuses_valid():
    """Verify that all six approved statuses can be instantiated on ClaimAssessment."""
    expected_statuses = [
        "supported",
        "partially_supported",
        "conflicting",
        "unsupported",
        "not_checked",
        "contradicted",
    ]
    assert CLAIM_ASSESSMENT_STATUSES == expected_statuses

    for st in expected_statuses:
        assessment = ClaimAssessment(
            claim_id="clm_01",
            section_id="sec_01",
            proposition="Water freezes at 0 degrees Celsius.",
            status=st,
            explanation=f"Testing status: {st}",
        )
        assert assessment.status == st


def test_invalid_assessment_status_rejected():
    """Verify that binary true/false, verified, or invalid strings are strictly rejected."""
    invalid_statuses = ["true", "false", "verified", "plausible", "unknown", ""]
    for inv in invalid_statuses:
        with pytest.raises(ValidationError):
            ClaimAssessment(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="Some claim text.",
                status=inv,  # type: ignore
                explanation="Explanation text.",
            )


def test_claim_assessment_required_and_optional_fields():
    """Verify required vs optional fields and their defaults."""
    # Minimum required fields
    assessment = ClaimAssessment(
        claim_id="clm_01",
        section_id="sec_01",
        proposition="A statement of fact.",
        status="supported",
        explanation="Directly entailed by evidence.",
    )
    assert assessment.risk_level == "normal"
    assert assessment.considered_evidence_ids == []
    assert assessment.supporting_evidence_ids == []
    assert assessment.contradicting_evidence_ids == []
    assert assessment.contextual_evidence_ids == []
    assert assessment.unassessed_reason is None
    assert assessment.recommended_action == "keep"
    assert assessment.assessed_at is not None

    # Full fields
    full = ClaimAssessment(
        claim_id="clm_02",
        section_id="sec_02",
        proposition="A detailed mechanism assertion.",
        status="partially_supported",
        risk_level="high",
        explanation="Partially supported by trial results; dosage was lower.",
        considered_evidence_ids=["evi_01", "evi_02"],
        supporting_evidence_ids=["evi_01"],
        contradicting_evidence_ids=[],
        contextual_evidence_ids=["evi_02"],
        unassessed_reason=None,
        recommended_action="qualify",
    )
    assert full.risk_level == "high"
    assert full.recommended_action == "qualify"
    assert full.considered_evidence_ids == ["evi_01", "evi_02"]


def test_claim_assessment_invalid_fields_rejected():
    """Verify that empty proposition, empty explanation, or invalid IDs are rejected."""
    # Empty proposition
    with pytest.raises(ValidationError):
        ClaimAssessment(
            claim_id="clm_01",
            section_id="sec_01",
            proposition="   ",
            status="supported",
            explanation="Valid explanation.",
        )

    # Empty explanation
    with pytest.raises(ValidationError):
        ClaimAssessment(
            claim_id="clm_01",
            section_id="sec_01",
            proposition="Valid proposition.",
            status="supported",
            explanation="",
        )

    # Invalid ID format
    with pytest.raises(ValidationError):
        ClaimAssessment(
            claim_id="clm!invalid#",
            section_id="sec_01",
            proposition="Valid proposition.",
            status="supported",
            explanation="Valid explanation.",
        )

    # Invalid evidence ID format in list
    with pytest.raises(ValidationError):
        ClaimAssessment(
            claim_id="clm_01",
            section_id="sec_01",
            proposition="Valid proposition.",
            status="supported",
            explanation="Valid explanation.",
            considered_evidence_ids=["evi@invalid!"],
        )


# ==============================================================================
# 2. ScriptFactCheckReport: Readiness & Scrutiny Metrics
# ==============================================================================

def test_script_fact_check_report_summary_all_supported():
    """When all claims are supported, report computes is_ready=True."""
    report = ScriptFactCheckReport(
        project_id="proj_01",
        script_version=1,
        assessments=[
            ClaimAssessment(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="Assertion 1",
                status="supported",
                explanation="Entailed by evi_01",
            ),
            ClaimAssessment(
                claim_id="clm_02",
                section_id="sec_02",
                proposition="Assertion 2",
                status="supported",
                explanation="Entailed by evi_02",
            ),
        ],
    )
    report.recompute_summary()
    assert report.total_claims == 2
    assert report.supported_count == 2
    assert report.contradicted_count == 0
    assert report.is_ready is True
    assert report.has_unresolved_high_risk is False


def test_script_fact_check_report_high_risk_partially_supported_blocks_readiness():
    """High-risk claims that are partially_supported block ready status."""
    report = ScriptFactCheckReport(
        project_id="proj_01",
        script_version=1,
        assessments=[
            ClaimAssessment(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="High risk claim",
                status="partially_supported",
                risk_level="high",
                explanation="Only partially supported",
            )
        ],
    )
    report.recompute_summary()
    assert report.is_ready is False
    assert report.has_unresolved_high_risk is True
    assert report.requires_repair is True
    assert "partially supported" in (report.readiness_reason or "")


def test_script_fact_check_report_normal_risk_contradicted_remains_flagged():
    """Normal-risk contradicted claims trigger repair and remain flagged without silent discard."""
    report = ScriptFactCheckReport(
        project_id="proj_01",
        script_version=1,
        assessments=[
            ClaimAssessment(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="Minor anecdote",
                status="contradicted",
                risk_level="normal",
                explanation="Refuted by secondary source",
            ),
            ClaimAssessment(
                claim_id="clm_02",
                section_id="sec_02",
                proposition="Main thesis",
                status="supported",
                risk_level="high",
                explanation="Verified",
            ),
        ],
    )
    report.recompute_summary()
    assert report.requires_repair is True
    assert report.contradicted_count == 1
    assert "normal-risk/creative claim(s) contradicted" in (report.readiness_reason or "")
    assert report.has_unresolved_high_risk is False
    assert report.is_ready is True


def test_script_claim_strongly_typed_assessment_status():
    """Verify ScriptClaim.assessment_status is strongly typed to ClaimAssessmentStatus."""
    from backend.app.domain.script import ScriptClaim

    # Valid status
    c1 = ScriptClaim(
        claim_id="clm_01",
        section_id="sec_01",
        claim_text="Assertion",
        assessment_status="supported",
    )
    assert c1.assessment_status == "supported"

    # Default is None
    c2 = ScriptClaim(
        claim_id="clm_02",
        section_id="sec_01",
        claim_text="Assertion 2",
    )
    assert c2.assessment_status is None

    # Invalid status rejected
    with pytest.raises(ValidationError):
        ScriptClaim(
            claim_id="clm_03",
            section_id="sec_01",
            claim_text="Assertion 3",
            assessment_status="invalid_status",  # type: ignore
        )


def test_script_fact_check_report_high_risk_unsupported_blocks_readiness():
    """High-risk claims that are unsupported block ready status."""
    report = ScriptFactCheckReport(
        project_id="proj_01",
        script_version=1,
        assessments=[
            ClaimAssessment(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="Central scientific mechanism assertion",
                status="unsupported",
                risk_level="high",
                explanation="No evidence found for this core mechanism",
            ),
        ],
    )
    report.recompute_summary()
    assert report.unsupported_count == 1
    assert report.has_unresolved_high_risk is True
    assert report.is_ready is False
    assert "high-risk claim(s) unsupported" in (report.readiness_reason or "")


def test_script_fact_check_report_normal_risk_unsupported_does_not_block_readiness():
    """Normal-risk unsupported claims are flagged but do not automatically block readiness."""
    report = ScriptFactCheckReport(
        project_id="proj_01",
        script_version=1,
        assessments=[
            ClaimAssessment(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="The meeting occurred in a small cafe in Vienna.",
                status="unsupported",
                risk_level="normal",  # Background detail
                explanation="Background detail not mentioned in primary evidence.",
            ),
            ClaimAssessment(
                claim_id="clm_02",
                section_id="sec_02",
                proposition="The treaty was signed in 1919.",
                status="supported",
                risk_level="high",
                explanation="Verified in source documents.",
            ),
        ],
    )
    report.recompute_summary()
    assert report.unsupported_count == 1
    assert report.supported_count == 1
    assert report.has_unresolved_high_risk is False
    assert report.is_ready is True  # Allowed per Section 2.2 risk rules


def test_script_fact_check_report_round_trip_serialization():
    """Verify complete JSON serialization and deserialization."""
    report = ScriptFactCheckReport(
        project_id="proj_test_roundtrip",
        script_version=2,
        research_run_id="rrun_01",
        assessments=[
            ClaimAssessment(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="Claim one.",
                status="supported",
                explanation="Verified.",
                considered_evidence_ids=["evi_01"],
                supporting_evidence_ids=["evi_01"],
            )
        ],
    )
    report.recompute_summary()

    json_str = report.model_dump_json(indent=2)
    deserialized = ScriptFactCheckReport.model_validate_json(json_str)

    assert deserialized.report_id == report.report_id
    assert deserialized.project_id == "proj_test_roundtrip"
    assert deserialized.script_version == 2
    assert deserialized.total_claims == 1
    assert deserialized.supported_count == 1
    assert len(deserialized.assessments) == 1
    assert deserialized.assessments[0].claim_id == "clm_01"
    assert deserialized.assessments[0].supporting_evidence_ids == ["evi_01"]


# ==============================================================================
# 3. Report Persistence & Storage
# ==============================================================================

def test_save_and_get_fact_check_report(env):
    """Verify version-specific persistence and retrieval in script/fact_checks/vNNN.json."""
    service = env["script_service"]
    pid = env["project_id"]

    report = ScriptFactCheckReport(
        project_id=pid,
        script_version=1,
        research_run_id="rrun_01",
        assessments=[
            ClaimAssessment(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="Verified claim.",
                status="supported",
                explanation="Evidence matches.",
            )
        ],
    )
    report.recompute_summary()

    saved_path = service.save_fact_check_report(pid, report)
    assert saved_path.exists()
    assert saved_path.name == "v001.json"
    assert "fact_checks" in saved_path.parts

    # Retrieve and verify
    retrieved = service.get_fact_check_report(pid, version=1)
    assert retrieved.report_id == report.report_id
    assert retrieved.script_version == 1
    assert retrieved.total_claims == 1
    assert retrieved.supported_count == 1


def test_fact_check_report_overwrite_protection(env):
    """Saving a fact-check report for an existing version must raise ScriptStorageError."""
    service = env["script_service"]
    pid = env["project_id"]

    report = ScriptFactCheckReport(
        project_id=pid,
        script_version=1,
        assessments=[],
    )
    service.save_fact_check_report(pid, report)

    # Attempting to save again for version 1 must fail
    with pytest.raises(ScriptStorageError, match="already exists"):
        service.save_fact_check_report(pid, report)


def test_get_nonexistent_report_raises_script_not_found(env):
    """Requesting a non-existent version report raises ScriptNotFoundError."""
    service = env["script_service"]
    pid = env["project_id"]

    with pytest.raises(ScriptNotFoundError, match="Fact-check report for version 99 not found"):
        service.get_fact_check_report(pid, version=99)


def test_list_fact_check_reports_sorted(env):
    """list_fact_check_reports returns all reports sorted by version ascending."""
    service = env["script_service"]
    pid = env["project_id"]

    r1 = ScriptFactCheckReport(project_id=pid, script_version=1, assessments=[])
    r2 = ScriptFactCheckReport(project_id=pid, script_version=2, assessments=[])
    service.save_fact_check_report(pid, r1)
    service.save_fact_check_report(pid, r2)

    reports = service.list_fact_check_reports(pid)
    assert len(reports) == 2
    assert reports[0].script_version == 1
    assert reports[1].script_version == 2


def test_project_isolation_in_report_storage(env):
    """Mismatched project_id raises ScriptValidationError; non-existent project raises ProjectNotFoundError."""
    service = env["script_service"]
    pid = env["project_id"]

    mismatched = ScriptFactCheckReport(
        project_id="proj_foreign",
        script_version=1,
        assessments=[],
    )
    with pytest.raises(ScriptValidationError, match="does not match target project_id"):
        service.save_fact_check_report(pid, mismatched)

    with pytest.raises(ProjectNotFoundError):
        service.get_fact_check_report("proj_does_not_exist", version=1)


# ==============================================================================
# 4. Backward Compatibility with Serialized Scripts
# ==============================================================================

def test_script_claim_backward_compatibility():
    """Verify serialized legacy ScriptClaim payloads without assessment_status load cleanly."""
    legacy_claim_dict = {
        "claim_id": "clm_01",
        "claim_text": "An existing claim without assessment_status.",
        "section_id": "sec_01",
        "evidence_ids": ["evi_01"],
        "source_ids": ["src_01"],
        "link_status": "linked",
    }
    claim = ScriptClaim.model_validate(legacy_claim_dict)
    assert claim.claim_id == "clm_01"
    assert claim.link_status == "linked"
    assert claim.assessment_status is None

    # Now assign assessment_status
    claim.assessment_status = "supported"
    dumped = claim.model_dump(mode="json")
    assert dumped["assessment_status"] == "supported"
    assert dumped["link_status"] == "linked"


def test_full_script_with_legacy_and_new_claims():
    """Verify Script domain model continues to validate existing serialized script structures."""
    script_data = {
        "title": "Legacy Explainer",
        "topic": "The Solar System",
        "target_duration_minutes": 5.0,
        "sections": [
            {
                "id": "sec_01",
                "type": "hook",
                "heading": "Hook",
                "narration": "Have you ever wondered about Jupiter?",
                "visual_description": "Jupiter graphic.",
            }
        ],
        "claims": [
            {
                "claim_id": "clm_01",
                "claim_text": "Jupiter is the largest planet in our solar system.",
                "section_id": "sec_01",
                "evidence_ids": ["evi_01"],
                "source_ids": ["src_01"],
                "link_status": "linked",
                # assessment_status omitted intentionally
            }
        ],
        "version": 1,
        "status": "draft",
    }
    script = Script.model_validate(script_data)
    assert len(script.claims) == 1
    assert script.claims[0].assessment_status is None
    assert script.claims[0].link_status == "linked"
