"""Tests for Phase 5 Checkpoint 2: Claim Verification Engine (ScriptFactChecker)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from backend.app.config import Settings
from backend.app.domain.fact_check import (
    BatchFactCheckResponsePayload,
    ClaimAssessment,
    ClaimAssessmentPayload,
    ClaimAssessmentStatus,
    ClaimRiskLevel,
    RepairAction,
    ScriptFactCheckReport,
    CLAIM_ASSESSMENT_STATUSES,
)
from backend.app.domain.research import EvidenceRecord, SourceRecord
from backend.app.domain.script import Script, ScriptClaim, ScriptSection
from backend.app.llm.base import LLMResponseMalformedError, LLMUnavailableError
from backend.app.llm.mock import MockLLMProvider
from backend.app.llm.ollama import OllamaLLMProvider
from backend.app.services.scriptwriter.fact_checker import (
    ScriptFactChecker,
    ScriptFactCheckError,
    ScriptFactCheckMalformedError,
    ScriptFactCheckSecurityError,
    ScriptFactCheckUnavailableError,
)
from backend.app.services.scriptwriter.outliner import ValidatedEvidenceCatalog


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_catalog():
    """Create a standard ValidatedEvidenceCatalog with 3 evidence items across 2 sources."""
    sources = {
        "src_01": SourceRecord(
            source_id="src_01",
            project_id="proj_alpha",
            title="Scientific American: Cold Physics",
            url="https://example.com/cold-physics",
        ),
        "src_02": SourceRecord(
            source_id="src_02",
            project_id="proj_alpha",
            title="NASA Atmosphere Studies",
            url="https://example.com/nasa-atmosphere",
        ),
    }
    evidence = {
        "evi_01": EvidenceRecord(
            evidence_id="evi_01",
            project_id="proj_alpha",
            run_id="run_test_01",
            source_id="src_01",
            excerpt="Water freezes into crystalline ice at exactly 0 degrees Celsius under standard atmospheric pressure.",
        ),
        "evi_02": EvidenceRecord(
            evidence_id="evi_02",
            project_id="proj_alpha",
            run_id="run_test_01",
            source_id="src_02",
            excerpt="Mars atmospheric pressure is less than 1% of Earth's, causing liquid water to vaporize rapidly.",
        ),
        "evi_03": EvidenceRecord(
            evidence_id="evi_03",
            project_id="proj_alpha",
            run_id="run_test_01",
            source_id="src_01",
            excerpt="Supercooled water can remain liquid down to -40 degrees Celsius in the complete absence of nucleation seeds.",
        ),
    }
    return ValidatedEvidenceCatalog(
        run_id="run_test_01",
        evidence_by_id=evidence,
        sources_by_id=sources,
    )


@pytest.fixture
def sample_script():
    """Create a sample 3-section Script with 3 claims."""
    sections = [
        ScriptSection(
            id="sec_01",
            type="hook",
            heading="The Freezing Threshold",
            narration="Did you know water freezes at 0 degrees Celsius under normal pressure?",
        ),
        ScriptSection(
            id="sec_02",
            type="middle",
            heading="Atmospheric Extremes",
            narration="On Mars, atmospheric pressure is under 1% of Earth's, making liquid water impossible.",
        ),
        ScriptSection(
            id="sec_03",
            type="takeaway",
            heading="Supercooling Magic",
            narration="Without nucleation seeds, supercooled water stays liquid to minus 40.",
        ),
    ]
    claims = [
        ScriptClaim(
            claim_id="clm_01",
            section_id="sec_01",
            claim_text="Water freezes at 0 degrees Celsius under standard pressure.",
            evidence_ids=["evi_01"],
            risk_level="normal",
        ),
        ScriptClaim(
            claim_id="clm_02",
            section_id="sec_02",
            claim_text="Mars atmospheric pressure is under 1% of Earth's.",
            evidence_ids=["evi_02"],
            risk_level="normal",
        ),
        ScriptClaim(
            claim_id="clm_03",
            section_id="sec_03",
            claim_text="Supercooled water stays liquid to -40 Celsius without seeds.",
            evidence_ids=["evi_03"],
            risk_level="high",
        ),
    ]
    return Script(
        script_id="scr_01",
        project_id="proj_alpha",
        version=1,
        title="Physics of Cold",
        topic="Freezing points and supercooling",
        sections=sections,
        claims=claims,
        research_run_id="run_test_01",
    )


# ==============================================================================
# 1. Assessment Statuses & Core Semantics Tests
# ==============================================================================

def test_all_six_assessment_statuses_supported(mock_catalog, sample_script):
    """Verify that all six approved statuses can be returned by the checker."""
    llm = MockLLMProvider()
    checker = ScriptFactChecker(llm_provider=llm)

    for status in CLAIM_ASSESSMENT_STATUSES:
        payload = BatchFactCheckResponsePayload(
            assessments=[
                ClaimAssessmentPayload(
                    claim_id="clm_01",
                    status=status,
                    explanation=f"Explicit evaluation resulting in {status}.",
                    supporting_evidence_ids=["evi_01"] if status == "supported" else [],
                    contradicting_evidence_ids=["evi_01"] if status == "contradicted" else [],
                    contextual_evidence_ids=["evi_01"] if status == "partially_supported" else [],
                    recommended_action="keep" if status == "supported" else "qualify",
                )
            ]
        )
        llm.set_response(payload)

        single_claim_script = sample_script.model_copy(deep=True)
        single_claim_script.claims = [sample_script.claims[0]]

        report = checker.check_script(single_claim_script, mock_catalog)
        assert len(report.assessments) == 1
        assert report.assessments[0].status == status
        assert report.assessments[0].explanation == f"Explicit evaluation resulting in {status}."


def test_supported_claim(mock_catalog, sample_script):
    """Test standard supported claim assessment with matching evidence citations."""
    llm = MockLLMProvider()
    llm.set_response(
        BatchFactCheckResponsePayload(
            assessments=[
                ClaimAssessmentPayload(
                    claim_id="clm_01",
                    status="supported",
                    explanation="Directly confirmed by Scientific American excerpt.",
                    supporting_evidence_ids=["evi_01"],
                    recommended_action="keep",
                )
            ]
        )
    )
    checker = ScriptFactChecker(llm_provider=llm)
    single_script = sample_script.model_copy(deep=True)
    single_script.claims = [sample_script.claims[0]]

    report = checker.check_script(single_script, mock_catalog)
    assert report.supported_count == 1
    assert report.is_ready is True
    assert report.assessments[0].supporting_evidence_ids == ["evi_01"]
    assert report.assessments[0].considered_evidence_ids == ["evi_01"]


def test_partially_supported_claim(mock_catalog, sample_script):
    """Test partially supported claim requiring qualification."""
    llm = MockLLMProvider()
    llm.set_response(
        BatchFactCheckResponsePayload(
            assessments=[
                ClaimAssessmentPayload(
                    claim_id="clm_01",
                    status="partially_supported",
                    explanation="Core statement supported, but standard pressure nuance should be specified.",
                    contextual_evidence_ids=["evi_01"],
                    recommended_action="qualify",
                )
            ]
        )
    )
    checker = ScriptFactChecker(llm_provider=llm)
    single_script = sample_script.model_copy(deep=True)
    single_script.claims = [sample_script.claims[0]]

    report = checker.check_script(single_script, mock_catalog)
    assert report.partially_supported_count == 1
    assert report.requires_repair is True
    assert report.assessments[0].recommended_action == "qualify"


def test_conflicting_evidence(mock_catalog, sample_script):
    """Test conflicting evidence assessment where sources disagree."""
    llm = MockLLMProvider()
    llm.set_response(
        BatchFactCheckResponsePayload(
            assessments=[
                ClaimAssessmentPayload(
                    claim_id="clm_01",
                    status="conflicting",
                    explanation="Source records provide conflicting pressure ranges.",
                    contextual_evidence_ids=["evi_01"],
                    recommended_action="qualify",
                )
            ]
        )
    )
    checker = ScriptFactChecker(llm_provider=llm)
    single_script = sample_script.model_copy(deep=True)
    single_script.claims = [sample_script.claims[0]]

    report = checker.check_script(single_script, mock_catalog)
    assert report.conflicting_count == 1
    assert report.assessments[0].status == "conflicting"


def test_unsupported_claim(mock_catalog, sample_script):
    """Test unsupported claim when supplied evidence does not substantiate assertion."""
    llm = MockLLMProvider()
    llm.set_response(
        BatchFactCheckResponsePayload(
            assessments=[
                ClaimAssessmentPayload(
                    claim_id="clm_01",
                    status="unsupported",
                    explanation="Supplied evidence does not address this specific assertion.",
                    supporting_evidence_ids=[],
                    recommended_action="qualify",
                )
            ]
        )
    )
    checker = ScriptFactChecker(llm_provider=llm)
    single_script = sample_script.model_copy(deep=True)
    single_script.claims = [sample_script.claims[0]]

    report = checker.check_script(single_script, mock_catalog)
    assert report.unsupported_count == 1
    assert report.assessments[0].status == "unsupported"


def test_contradicted_claim(mock_catalog, sample_script):
    """Test contradicted claim triggering repair."""
    llm = MockLLMProvider()
    llm.set_response(
        BatchFactCheckResponsePayload(
            assessments=[
                ClaimAssessmentPayload(
                    claim_id="clm_01",
                    status="contradicted",
                    explanation="Evidence proves water freezes at 0 C, not 10 C as claimed.",
                    contradicting_evidence_ids=["evi_01"],
                    recommended_action="remove",
                )
            ]
        )
    )
    checker = ScriptFactChecker(llm_provider=llm)
    single_script = sample_script.model_copy(deep=True)
    single_script.claims = [sample_script.claims[0]]

    report = checker.check_script(single_script, mock_catalog)
    assert report.contradicted_count == 1
    assert report.requires_repair is True
    assert report.assessments[0].recommended_action == "remove"


def test_creative_rhetorical_text(mock_catalog, sample_script):
    """Test rhetorical/creative claim handling with creative_flexibility risk level."""
    creative_claim = ScriptClaim(
        claim_id="clm_meta",
        section_id="sec_01",
        claim_text="Gravity is the invisible glue binding the cosmos together.",
        evidence_ids=[],
        risk_level="creative_flexibility",
    )
    sample_script.claims.append(creative_claim)

    llm = MockLLMProvider()
    # Mock provider auto-generates supported / keep for creative_flexibility
    checker = ScriptFactChecker(llm_provider=llm)
    report = checker.check_script(sample_script, mock_catalog)

    meta_assessment = next(a for a in report.assessments if a.claim_id == "clm_meta")
    assert meta_assessment.status in ["supported", "not_checked"]
    assert meta_assessment.recommended_action == "keep"
    assert meta_assessment.risk_level == "creative_flexibility"


def test_claims_with_no_evidence_links(mock_catalog, sample_script):
    """Test claim that declared no evidence links (link_status='unlinked')."""
    unlinked_claim = ScriptClaim(
        claim_id="clm_unlinked",
        section_id="sec_01",
        claim_text="Unlinked historical assertion.",
        evidence_ids=[],
        link_status="unlinked",
        risk_level="normal",
    )
    script = sample_script.model_copy(deep=True)
    script.claims = [unlinked_claim]

    llm = MockLLMProvider()
    checker = ScriptFactChecker(llm_provider=llm)
    report = checker.check_script(script, mock_catalog)

    assert len(report.assessments) == 1
    assessment = report.assessments[0]
    assert assessment.considered_evidence_ids == []
    assert assessment.supporting_evidence_ids == []


# ==============================================================================
# 2. Batching & Provenance Validation Tests
# ==============================================================================

def test_multiple_claims_in_one_batch(mock_catalog, sample_script):
    """Verify that multiple claims in a batch are accurately processed and mapped."""
    llm = MockLLMProvider()
    llm.set_response(
        BatchFactCheckResponsePayload(
            assessments=[
                ClaimAssessmentPayload(
                    claim_id="clm_01",
                    status="supported",
                    explanation="Verified freezing point.",
                    supporting_evidence_ids=["evi_01"],
                ),
                ClaimAssessmentPayload(
                    claim_id="clm_02",
                    status="supported",
                    explanation="Verified Martian atmospheric pressure.",
                    supporting_evidence_ids=["evi_02"],
                ),
                ClaimAssessmentPayload(
                    claim_id="clm_03",
                    status="supported",
                    explanation="Verified supercooling threshold.",
                    supporting_evidence_ids=["evi_03"],
                ),
            ]
        )
    )
    checker = ScriptFactChecker(llm_provider=llm, max_batch_size=5)
    report = checker.check_script(sample_script, mock_catalog)

    assert report.total_claims == 3
    assert report.supported_count == 3
    assert [a.claim_id for a in report.assessments] == ["clm_01", "clm_02", "clm_03"]


def test_missing_assessment_in_batch_triggers_retry_and_recovers(mock_catalog, sample_script):
    """Verify missing assessment in batch triggers corrective retry and recovers if retry succeeds."""
    llm = MockLLMProvider()
    # First response missing clm_03
    incomplete_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                status="supported",
                explanation="Verified clm_01.",
                supporting_evidence_ids=["evi_01"],
            ),
            ClaimAssessmentPayload(
                claim_id="clm_02",
                status="supported",
                explanation="Verified clm_02.",
                supporting_evidence_ids=["evi_02"],
            ),
        ]
    )
    # Second (retried) response provides all 3
    complete_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                status="supported",
                explanation="Verified clm_01.",
                supporting_evidence_ids=["evi_01"],
            ),
            ClaimAssessmentPayload(
                claim_id="clm_02",
                status="supported",
                explanation="Verified clm_02.",
                supporting_evidence_ids=["evi_02"],
            ),
            ClaimAssessmentPayload(
                claim_id="clm_03",
                status="supported",
                explanation="Verified clm_03 on retry.",
                supporting_evidence_ids=["evi_03"],
            ),
        ]
    )
    llm.queue_responses([incomplete_payload, complete_payload])

    checker = ScriptFactChecker(llm_provider=llm, max_batch_size=5)
    report = checker.check_script(sample_script, mock_catalog)

    assert len(llm.calls) == 2  # Proves corrective retry occurred
    assert report.total_claims == 3
    assert report.supported_count == 3
    assert "CORRECTION REQUIRED" in llm.calls[1]["prompt"]


def test_duplicate_assessment_triggers_retry(mock_catalog, sample_script):
    """Verify duplicate assessments for the same claim trigger corrective retry."""
    llm = MockLLMProvider()
    dup_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                status="supported",
                explanation="First copy.",
                supporting_evidence_ids=["evi_01"],
            ),
            ClaimAssessmentPayload(
                claim_id="clm_01",
                status="supported",
                explanation="Duplicate copy.",
                supporting_evidence_ids=["evi_01"],
            ),
            ClaimAssessmentPayload(
                claim_id="clm_02",
                status="supported",
                explanation="clm_02 copy.",
                supporting_evidence_ids=["evi_02"],
            ),
            ClaimAssessmentPayload(
                claim_id="clm_03",
                status="supported",
                explanation="clm_03 copy.",
                supporting_evidence_ids=["evi_03"],
            ),
        ]
    )
    valid_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                status="supported",
                explanation="Clean copy.",
                supporting_evidence_ids=["evi_01"],
            ),
            ClaimAssessmentPayload(
                claim_id="clm_02",
                status="supported",
                explanation="clm_02.",
                supporting_evidence_ids=["evi_02"],
            ),
            ClaimAssessmentPayload(
                claim_id="clm_03",
                status="supported",
                explanation="clm_03.",
                supporting_evidence_ids=["evi_03"],
            ),
        ]
    )
    llm.queue_responses([dup_payload, valid_payload])

    checker = ScriptFactChecker(llm_provider=llm, max_batch_size=5)
    report = checker.check_script(sample_script, mock_catalog)

    assert len(llm.calls) == 2
    assert "Duplicate assessments" in llm.calls[1]["prompt"]
    assert report.supported_count == 3


def test_fabricated_claim_id_rejected(mock_catalog, sample_script):
    """Verify fabricated claim ID (not requested) is rejected and retried."""
    llm = MockLLMProvider()
    fabricated_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                status="supported",
                explanation="clm_01.",
                supporting_evidence_ids=["evi_01"],
            ),
            ClaimAssessmentPayload(
                claim_id="clm_02",
                status="supported",
                explanation="clm_02.",
                supporting_evidence_ids=["evi_02"],
            ),
            ClaimAssessmentPayload(
                claim_id="clm_fabricated_999",
                status="supported",
                explanation="Fake claim.",
                supporting_evidence_ids=[],
            ),
        ]
    )
    valid_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                status="supported",
                explanation="clm_01.",
                supporting_evidence_ids=["evi_01"],
            ),
            ClaimAssessmentPayload(
                claim_id="clm_02",
                status="supported",
                explanation="clm_02.",
                supporting_evidence_ids=["evi_02"],
            ),
            ClaimAssessmentPayload(
                claim_id="clm_03",
                status="supported",
                explanation="clm_03 corrected.",
                supporting_evidence_ids=["evi_03"],
            ),
        ]
    )
    llm.queue_responses([fabricated_payload, valid_payload])

    checker = ScriptFactChecker(llm_provider=llm, max_batch_size=5)
    report = checker.check_script(sample_script, mock_catalog)

    assert len(llm.calls) == 2
    assert "clm_fabricated_999" in llm.calls[1]["prompt"]
    assert report.supported_count == 3


def test_fabricated_evidence_id_rejected(mock_catalog, sample_script):
    """Verify fabricated evidence ID (not in catalog) is rejected and retried."""
    llm = MockLLMProvider()
    fake_ev_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                status="supported",
                explanation="clm_01.",
                supporting_evidence_ids=["evi_fabricated_999"],
            ),
            ClaimAssessmentPayload(
                claim_id="clm_02",
                status="supported",
                explanation="clm_02.",
                supporting_evidence_ids=["evi_02"],
            ),
            ClaimAssessmentPayload(
                claim_id="clm_03",
                status="supported",
                explanation="clm_03.",
                supporting_evidence_ids=["evi_03"],
            ),
        ]
    )
    valid_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                status="supported",
                explanation="clm_01 with real evidence.",
                supporting_evidence_ids=["evi_01"],
            ),
            ClaimAssessmentPayload(
                claim_id="clm_02",
                status="supported",
                explanation="clm_02.",
                supporting_evidence_ids=["evi_02"],
            ),
            ClaimAssessmentPayload(
                claim_id="clm_03",
                status="supported",
                explanation="clm_03.",
                supporting_evidence_ids=["evi_03"],
            ),
        ]
    )
    llm.queue_responses([fake_ev_payload, valid_payload])

    checker = ScriptFactChecker(llm_provider=llm, max_batch_size=5)
    report = checker.check_script(sample_script, mock_catalog)

    assert len(llm.calls) == 2
    assert "evi_fabricated_999" in llm.calls[1]["prompt"]
    assert report.supported_count == 3


def test_evidence_from_another_run_or_project_rejected(mock_catalog, sample_script):
    """Verify evidence from another run (not in this catalog) is rejected."""
    # Attempt to cite foreign evidence ID 'evi_foreign_run'
    llm = MockLLMProvider()
    foreign_ev_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                status="supported",
                explanation="Citing foreign run evidence.",
                supporting_evidence_ids=["evi_foreign_run"],
            )
        ]
    )
    valid_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                status="supported",
                explanation="Citing valid run evidence.",
                supporting_evidence_ids=["evi_01"],
            )
        ]
    )
    llm.queue_responses([foreign_ev_payload, valid_payload])

    checker = ScriptFactChecker(llm_provider=llm)
    single_script = sample_script.model_copy(deep=True)
    single_script.claims = [sample_script.claims[0]]

    report = checker.check_script(single_script, mock_catalog)
    assert len(llm.calls) == 2
    assert "evi_foreign_run" in llm.calls[1]["prompt"]


def test_evidence_not_supplied_to_relevant_assessment_rejected(mock_catalog, sample_script):
    """Verify that citing an evidence ID from the catalog that was NOT supplied to this claim is rejected."""
    # clm_01 has supplied evidence: {'evi_01'}. 'evi_02' is in the catalog, but not supplied to clm_01.
    llm = MockLLMProvider()
    unsupplied_ev_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                status="supported",
                explanation="Using evi_02 for clm_01.",
                supporting_evidence_ids=["evi_02"],  # Invalid: not supplied for clm_01!
            )
        ]
    )
    valid_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                status="supported",
                explanation="Using correct evi_01.",
                supporting_evidence_ids=["evi_01"],
            )
        ]
    )
    llm.queue_responses([unsupplied_ev_payload, valid_payload])

    checker = ScriptFactChecker(llm_provider=llm)
    single_script = sample_script.model_copy(deep=True)
    single_script.claims = [sample_script.claims[0]]

    report = checker.check_script(single_script, mock_catalog)
    assert len(llm.calls) == 2
    assert "not supplied to it" in llm.calls[1]["prompt"]


# ==============================================================================
# 3. Failure, Retry & Graceful Degradation Tests
# ==============================================================================

def test_malformed_response_and_one_corrective_retry(mock_catalog, sample_script):
    """Test malformed LLM response triggers corrective retry and succeeds."""
    llm = MockLLMProvider()
    # First response raises LLMResponseMalformedError
    malformed_err = LLMResponseMalformedError("Invalid JSON syntax: expected closing quote.")
    valid_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                status="supported",
                explanation="Corrected payload.",
                supporting_evidence_ids=["evi_01"],
            ),
            ClaimAssessmentPayload(
                claim_id="clm_02",
                status="supported",
                explanation="Corrected payload.",
                supporting_evidence_ids=["evi_02"],
            ),
            ClaimAssessmentPayload(
                claim_id="clm_03",
                status="supported",
                explanation="Corrected payload.",
                supporting_evidence_ids=["evi_03"],
            ),
        ]
    )
    llm.queue_responses([malformed_err, valid_payload])

    checker = ScriptFactChecker(llm_provider=llm)
    report = checker.check_script(sample_script, mock_catalog)

    assert len(llm.calls) == 2
    assert report.supported_count == 3


def test_corrective_retry_failure_marks_batch_not_checked(mock_catalog, sample_script):
    """Test that when corrective retry also fails, claims in batch are marked not_checked with malformed_response."""
    llm = MockLLMProvider()
    malformed_err1 = LLMResponseMalformedError("Malformed JSON attempt 1.")
    malformed_err2 = LLMResponseMalformedError("Malformed JSON attempt 2.")
    llm.queue_responses([malformed_err1, malformed_err2])

    checker = ScriptFactChecker(llm_provider=llm)
    report = checker.check_script(sample_script, mock_catalog)

    assert len(llm.calls) == 2
    assert report.total_claims == 3
    assert report.not_checked_count == 3
    for a in report.assessments:
        assert a.status == "not_checked"
        assert a.unassessed_reason == "malformed_response"


def test_provider_unavailable_marks_not_checked(mock_catalog, sample_script):
    """Test LLM provider unavailable marks claims not_checked without false assessment."""
    llm = MockLLMProvider()
    llm.set_error(LLMUnavailableError("Ollama service down on port 11434."))

    checker = ScriptFactChecker(llm_provider=llm)
    report = checker.check_script(sample_script, mock_catalog)

    assert report.not_checked_count == 3
    assert report.supported_count == 0
    assert report.unsupported_count == 0
    assert report.contradicted_count == 0
    for a in report.assessments:
        assert a.status == "not_checked"
        assert a.unassessed_reason == "provider_unavailable"


def test_provider_timeout_marks_not_checked(mock_catalog, sample_script):
    """Test provider timeout marks claims not_checked with provider_timeout."""
    llm = MockLLMProvider()
    llm.set_error(TimeoutError("Request to LLM timed out after 60 seconds."))

    checker = ScriptFactChecker(llm_provider=llm)
    report = checker.check_script(sample_script, mock_catalog)

    assert report.not_checked_count == 3
    for a in report.assessments:
        assert a.status == "not_checked"
        assert a.unassessed_reason == "provider_timeout"


def test_no_false_assessment_on_provider_failure(mock_catalog, sample_script):
    """Strictly assert that provider failure is NEVER converted to unsupported or contradicted."""
    llm = MockLLMProvider()
    llm.set_error(LLMUnavailableError("Network unreachable"))

    checker = ScriptFactChecker(llm_provider=llm)
    report = checker.check_script(sample_script, mock_catalog)

    assert report.unsupported_count == 0
    assert report.contradicted_count == 0
    assert all(a.status == "not_checked" for a in report.assessments)


# ==============================================================================
# 4. Batch Splitting & Partial Failure Isolation Tests
# ==============================================================================

def test_deterministic_batch_splitting(mock_catalog):
    """Verify deterministic splitting into batches based on max_batch_size."""
    # Create 7 claims
    claims = [
        ScriptClaim(
            claim_id=f"clm_{i:02d}",
            section_id="sec_01",
            claim_text=f"Proposition number {i}.",
            evidence_ids=["evi_01"],
        )
        for i in range(1, 8)
    ]
    sections = [ScriptSection(id="sec_01", type="hook", narration="Narration text.")]
    script = Script(
        script_id="scr_split",
        project_id="proj_split",
        version=1,
        title="Split Test",
        sections=sections,
        claims=claims,
        research_run_id="run_test_01",
    )

    llm = MockLLMProvider()
    # Batch size 3 on 7 claims should yield 3 batches: [3, 3, 1]
    checker = ScriptFactChecker(llm_provider=llm, max_batch_size=3)
    report = checker.check_script(script, mock_catalog)

    assert len(llm.calls) == 3
    assert report.total_claims == 7
    assert report.supported_count == 7


def test_partial_batch_failure_retains_successful_results(mock_catalog):
    """Verify that if Batch 1 succeeds and Batch 2 fails, Batch 1 assessments are preserved."""
    claims = [
        ScriptClaim(claim_id="clm_01", section_id="sec_01", claim_text="Claim 1.", evidence_ids=["evi_01"]),
        ScriptClaim(claim_id="clm_02", section_id="sec_01", claim_text="Claim 2.", evidence_ids=["evi_01"]),
        ScriptClaim(claim_id="clm_03", section_id="sec_01", claim_text="Claim 3.", evidence_ids=["evi_01"]),
        ScriptClaim(claim_id="clm_04", section_id="sec_01", claim_text="Claim 4.", evidence_ids=["evi_01"]),
    ]
    script = Script(
        script_id="scr_partial",
        project_id="proj_partial",
        version=1,
        title="Partial Failure Test",
        sections=[ScriptSection(id="sec_01", type="hook", narration="Narration.")],
        claims=claims,
        research_run_id="run_test_01",
    )

    llm = MockLLMProvider()
    # Batch 1 (clm_01, clm_02) succeeds
    batch_1_resp = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(claim_id="clm_01", status="supported", explanation="Verified 1.", supporting_evidence_ids=["evi_01"]),
            ClaimAssessmentPayload(claim_id="clm_02", status="supported", explanation="Verified 2.", supporting_evidence_ids=["evi_01"]),
        ]
    )
    # Batch 2 fails on both primary and retry
    batch_2_err = LLMResponseMalformedError("Unrecoverable syntax error.")
    llm.queue_responses([batch_1_resp, batch_2_err, batch_2_err])

    checker = ScriptFactChecker(llm_provider=llm, max_batch_size=2)
    report = checker.check_script(script, mock_catalog)

    assert report.total_claims == 4
    assert report.supported_count == 2
    assert report.not_checked_count == 2

    c1 = next(a for a in report.assessments if a.claim_id == "clm_01")
    c2 = next(a for a in report.assessments if a.claim_id == "clm_02")
    c3 = next(a for a in report.assessments if a.claim_id == "clm_03")
    c4 = next(a for a in report.assessments if a.claim_id == "clm_04")

    assert c1.status == "supported"
    assert c2.status == "supported"
    assert c3.status == "not_checked"
    assert c3.unassessed_reason == "malformed_response"
    assert c4.status == "not_checked"
    assert c4.unassessed_reason == "malformed_response"


# ==============================================================================
# 5. Unresearched Mode & Security Constraints Tests
# ==============================================================================

def test_unresearched_mode_marks_all_claims_not_checked(sample_script):
    """Test unresearched script (research_run_id=None, catalog=None) marks all claims not_checked without LLM calls."""
    unresearched_script = sample_script.model_copy(deep=True)
    unresearched_script.research_run_id = None

    llm = MockLLMProvider()
    checker = ScriptFactChecker(llm_provider=llm)
    report = checker.check_script(unresearched_script, catalog=None)

    assert len(llm.calls) == 0  # Zero LLM invocations
    assert report.total_claims == 3
    assert report.not_checked_count == 3
    for a in report.assessments:
        assert a.status == "not_checked"
        assert a.unassessed_reason == "unresearched_script"


def test_cross_project_mismatch_raises_security_error(mock_catalog, sample_script):
    """Test project ID tampering raises ScriptFactCheckSecurityError."""
    llm = MockLLMProvider()
    checker = ScriptFactChecker(llm_provider=llm)

    with pytest.raises(ScriptFactCheckSecurityError, match="Project ID mismatch"):
        checker.check_script(sample_script, mock_catalog, project_id="proj_impostor")


def test_catalog_run_mismatch_raises_security_error(mock_catalog, sample_script):
    """Test mismatch between script.research_run_id and catalog.run_id raises ScriptFactCheckSecurityError."""
    tampered_catalog = ValidatedEvidenceCatalog(
        run_id="run_different_999",
        evidence_by_id=mock_catalog.evidence_by_id,
        sources_by_id=mock_catalog.sources_by_id,
    )
    llm = MockLLMProvider()
    checker = ScriptFactChecker(llm_provider=llm)

    with pytest.raises(ScriptFactCheckSecurityError, match="does not match script research_run_id"):
        checker.check_script(sample_script, tampered_catalog)


# ==============================================================================
# 6. Provider Compatibility Tests (Mock & Ollama)
# ==============================================================================

def test_compatibility_with_mock_llm_provider(mock_catalog, sample_script):
    """Verify end-to-end integration with default MockLLMProvider behavior."""
    llm = MockLLMProvider()
    checker = ScriptFactChecker(llm_provider=llm)
    report = checker.check_script(sample_script, mock_catalog)

    assert report.total_claims == 3
    assert report.supported_count == 3
    assert report.is_ready is True
    assert len(llm.calls) == 1


def test_compatibility_with_ollama_llm_provider_contracts():
    """Verify OllamaLLMProvider adheres to LLMProvider interface for fact-check schemas."""
    settings = Settings(
        OLLAMA_HOST="http://localhost:11434",
        OLLAMA_MODEL="qwen2.5:7b",
        OLLAMA_TIMEOUT_SECONDS=5.0,
    )
    ollama_provider = OllamaLLMProvider(settings=settings)

    mock_ollama_resp = {
        "response": json.dumps(
            {
                "assessments": [
                    {
                        "claim_id": "clm_01",
                        "status": "supported",
                        "explanation": "Verified via local Ollama inference.",
                        "supporting_evidence_ids": ["evi_01"],
                        "contradicting_evidence_ids": [],
                        "contextual_evidence_ids": [],
                        "recommended_action": "keep",
                    }
                ]
            }
        ),
        "done": True,
    }

    with patch.object(ollama_provider, "_post_generate", return_value=mock_ollama_resp):
        payload = ollama_provider.generate_json(
            prompt="Fact-check prompt",
            schema=BatchFactCheckResponsePayload,
        )
        assert isinstance(payload, BatchFactCheckResponsePayload)
        assert len(payload.assessments) == 1
        assert payload.assessments[0].claim_id == "clm_01"
        assert payload.assessments[0].status == "supported"
