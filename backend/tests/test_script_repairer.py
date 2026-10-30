"""Tests for Phase 5 Checkpoint 3: ScriptRepairer & Surgical Claim Repair."""

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
)
from backend.app.domain.research import EvidenceRecord, SourceRecord
from backend.app.domain.script import Script, ScriptClaim, ScriptSection
from backend.app.llm.base import LLMResponseMalformedError, LLMUnavailableError
from backend.app.llm.mock import MockLLMProvider
from backend.app.services.scriptwriter.fact_checker import (
    ScriptFactChecker,
    ScriptFactCheckError,
)
from backend.app.services.scriptwriter.outliner import ValidatedEvidenceCatalog
from backend.app.services.scriptwriter.repairer import (
    ClaimRepairActionRecord,
    ClaimRepairPayload,
    ScriptRepairError,
    ScriptRepairMalformedError,
    ScriptRepairResult,
    ScriptRepairSecurityError,
    ScriptRepairUnavailableError,
    ScriptRepairer,
    SectionRepairPayload,
    _is_same_factual_proposition,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_catalog():
    """Create a ValidatedEvidenceCatalog with 4 evidence records across 2 sources."""
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
            excerpt="Water freezes into crystalline ice at exactly 0 degrees Celsius under standard atmospheric pressure (1 atm).",
        ),
        "evi_02": EvidenceRecord(
            evidence_id="evi_02",
            project_id="proj_alpha",
            run_id="run_test_01",
            source_id="src_02",
            excerpt="Mars atmospheric pressure is less than 1% of Earth's (averaging 610 Pascals), causing liquid water to vaporize rapidly.",
        ),
        "evi_03": EvidenceRecord(
            evidence_id="evi_03",
            project_id="proj_alpha",
            run_id="run_test_01",
            source_id="src_01",
            excerpt="Supercooled water can remain liquid down to -40 degrees Celsius in the complete absence of nucleation seeds.",
        ),
        "evi_alt_04": EvidenceRecord(
            evidence_id="evi_alt_04",
            project_id="proj_alpha",
            run_id="run_test_01",
            source_id="src_01",
            excerpt="Titan has liquid methane and ethane lakes covering hundreds of square kilometers.",
        ),
    }
    return ValidatedEvidenceCatalog(
        run_id="run_test_01",
        evidence_by_id=evidence,
        sources_by_id=sources,
    )


@pytest.fixture
def sample_script():
    """Create a 3-section Script with 4 claims."""
    sections = [
        ScriptSection(
            id="sec_01",
            type="hook",
            heading="Introduction",
            narration="Did you know water freezes at 0 degrees Celsius under standard atmospheric pressure?",
            claim_ids=["clm_01"],
        ),
        ScriptSection(
            id="sec_02",
            type="middle",
            heading="Atmospheric Extremes",
            narration="On Mars, liquid water lakes exist everywhere across the surface. Meanwhile, Martian atmospheric pressure is under 1% of Earth's.",
            claim_ids=["clm_02", "clm_03"],
        ),
        ScriptSection(
            id="sec_03",
            type="takeaway",
            heading="Extreme Cold",
            narration="Without nucleation seeds, supercooled water stays liquid to minus 40 degrees Celsius.",
            claim_ids=["clm_04"],
        ),
    ]
    claims = [
        ScriptClaim(
            claim_id="clm_01",
            section_id="sec_01",
            claim_text="Water freezes at 0 degrees Celsius under standard atmospheric pressure.",
            evidence_ids=["evi_01"],
            risk_level="normal",
        ),
        ScriptClaim(
            claim_id="clm_02",
            section_id="sec_02",
            claim_text="On Mars, liquid water lakes exist everywhere across the surface.",
            evidence_ids=[],
            risk_level="high",
        ),
        ScriptClaim(
            claim_id="clm_03",
            section_id="sec_02",
            claim_text="Mars atmospheric pressure is under 1% of Earth's.",
            evidence_ids=["evi_02"],
            risk_level="normal",
        ),
        ScriptClaim(
            claim_id="clm_04",
            section_id="sec_03",
            claim_text="Without nucleation seeds, supercooled water stays liquid to -40 Celsius.",
            evidence_ids=["evi_03"],
            risk_level="normal",
        ),
    ]
    return Script(
        script_id="scr_01",
        project_id="proj_alpha",
        version=1,
        title="Physics of Cold",
        sections=sections,
        claims=claims,
        research_run_id="run_test_01",
    )


@pytest.fixture
def sample_report():
    """Create a fact check report with clm_01 supported, clm_02 contradicted, clm_03 partially_supported, clm_04 supported."""
    assessments = [
        ClaimAssessment(
            claim_id="clm_01",
            section_id="sec_01",
            proposition="Water freezes at 0 degrees Celsius under standard atmospheric pressure.",
            status="supported",
            risk_level="normal",
            explanation="Substantiated by evi_01.",
            considered_evidence_ids=["evi_01"],
            supporting_evidence_ids=["evi_01"],
        ),
        ClaimAssessment(
            claim_id="clm_02",
            section_id="sec_02",
            proposition="On Mars, liquid water lakes exist everywhere across the surface.",
            status="contradicted",
            risk_level="high",
            explanation="Directly refuted by Mars atmospheric pressure data (evi_02: water vaporizes rapidly).",
            considered_evidence_ids=["evi_02"],
            contradicting_evidence_ids=["evi_02"],
            recommended_action="remove",
        ),
        ClaimAssessment(
            claim_id="clm_03",
            section_id="sec_02",
            proposition="Mars atmospheric pressure is under 1% of Earth's.",
            status="partially_supported",
            risk_level="normal",
            explanation="Evidence supports under 1% (averaging 610 Pascals), but needs pressure qualifier.",
            considered_evidence_ids=["evi_02"],
            contextual_evidence_ids=["evi_02"],
            recommended_action="qualify",
        ),
        ClaimAssessment(
            claim_id="clm_04",
            section_id="sec_03",
            proposition="Without nucleation seeds, supercooled water stays liquid to -40 Celsius.",
            status="supported",
            risk_level="normal",
            explanation="Substantiated by evi_03.",
            considered_evidence_ids=["evi_03"],
            supporting_evidence_ids=["evi_03"],
        ),
    ]
    report = ScriptFactCheckReport(
        project_id="proj_alpha",
        script_version=1,
        research_run_id="run_test_01",
        assessments=assessments,
    )
    report.recompute_summary()
    return report


# ==============================================================================
# 1. Surgical Repair Policy & Action Tests
# ==============================================================================

def test_partially_supported_claim_qualification(mock_catalog, sample_script):
    """Test qualification of a partially supported claim and targeted recheck."""
    # Script with single partially supported claim
    script = sample_script.model_copy(deep=True)
    script.sections = [sample_script.sections[1]]
    script.claims = [sample_script.claims[2]]  # clm_03
    script.sections[0].claim_ids = ["clm_03"]

    report = ScriptFactCheckReport(
        project_id="proj_alpha",
        script_version=1,
        research_run_id="run_test_01",
        assessments=[
            ClaimAssessment(
                claim_id="clm_03",
                section_id="sec_02",
                proposition="Mars atmospheric pressure is under 1% of Earth's.",
                status="partially_supported",
                risk_level="normal",
                explanation="Needs qualifier on average pressure.",
                considered_evidence_ids=["evi_02"],
                contextual_evidence_ids=["evi_02"],
                recommended_action="qualify",
            )
        ],
    )
    report.recompute_summary()

    llm = MockLLMProvider()
    # 1. Repair response: qualifies the claim
    repair_payload = SectionRepairPayload(
        section_id="sec_02",
        revised_narration="On Mars, average atmospheric pressure is under 1% of Earth's, averaging 610 Pascals.",
        repairs=[
            ClaimRepairPayload(
                claim_id="clm_03",
                action="qualify",
                target_span="Meanwhile, Martian atmospheric pressure is under 1% of Earth's.",
                replacement_span="On Mars, average atmospheric pressure is under 1% of Earth's, averaging 610 Pascals.",
                revised_claim_text="Mars average atmospheric pressure is under 1% of Earth's.",
                evidence_ids=["evi_02"],
                explanation="Added qualifier on average pressure matching evi_02.",
            )
        ],
    )
    # 2. Recheck response: confirms supported
    recheck_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_03",
                status="supported",
                explanation="Verified with qualifier.",
                supporting_evidence_ids=["evi_02"],
            )
        ]
    )
    llm.queue_responses([repair_payload, recheck_payload])

    repairer = ScriptRepairer(llm_provider=llm)
    known_ids = {"clm_01", "clm_02", "clm_03", "clm_04"}
    result = repairer.repair_script(script, report, mock_catalog, all_known_claim_ids=known_ids)

    assert result.attempted_claims_count == 1
    assert result.resolved_claims_count == 1
    assert result.is_fully_resolved is True
    assert result.fact_check_report.supported_count == 1
    assert "average atmospheric pressure" in result.repaired_script.sections[0].narration


def test_contradicted_claim_correction_using_permitted_evidence(mock_catalog, sample_script):
    """Test contradicted claim correction using alternative permitted evidence from catalog."""
    # Script with clm_02 (falsely claimed Mars has liquid lakes)
    script = sample_script.model_copy(deep=True)
    script.sections = [sample_script.sections[1]]
    script.claims = [sample_script.claims[1]]  # clm_02
    script.sections[0].claim_ids = ["clm_02"]

    report = ScriptFactCheckReport(
        project_id="proj_alpha",
        script_version=1,
        research_run_id="run_test_01",
        assessments=[
            ClaimAssessment(
                claim_id="clm_02",
                section_id="sec_02",
                proposition="On Mars, liquid water lakes exist everywhere across the surface.",
                status="contradicted",
                risk_level="high",
                explanation="Directly contradicted by Mars pressure.",
                considered_evidence_ids=["evi_02"],
                contradicting_evidence_ids=["evi_02"],
                recommended_action="rephrase",
            )
        ],
    )
    report.recompute_summary()

    llm = MockLLMProvider()
    # 1. Repair response: replaces Mars liquid lakes with Titan liquid methane lakes (evi_alt_04)
    repair_payload = SectionRepairPayload(
        section_id="sec_02",
        revised_narration="Unlike Mars where liquid water vaporizes, Saturn's moon Titan possesses vast liquid methane lakes.",
        repairs=[
            ClaimRepairPayload(
                claim_id="clm_02",
                action="rephrase",
                target_span="On Mars, liquid water lakes exist everywhere across the surface.",
                replacement_span="Unlike Mars where liquid water vaporizes, Saturn's moon Titan possesses vast liquid methane lakes.",
                revised_claim_text="Titan has vast liquid methane and ethane lakes.",
                evidence_ids=["evi_alt_04"],
                explanation="Corrected by citing verified extraterrestrial lakes on Titan from evi_alt_04.",
            )
        ],
    )
    # 2. Recheck response: confirms new claim (clm_05 or newly allocated) is supported
    recheck_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_05",
                status="supported",
                explanation="Verified against evi_alt_04.",
                supporting_evidence_ids=["evi_alt_04"],
            )
        ]
    )
    llm.queue_responses([repair_payload, recheck_payload])

    repairer = ScriptRepairer(llm_provider=llm)
    known_ids = {"clm_01", "clm_02", "clm_03", "clm_04"}
    result = repairer.repair_script(script, report, mock_catalog, all_known_claim_ids=known_ids)

    assert result.attempted_claims_count == 1
    assert result.resolved_claims_count == 1
    assert result.is_fully_resolved is True
    # The factual proposition changed, so a new claim ID was allocated!
    assert result.repair_actions[0].original_claim_id == "clm_02"
    assert result.repair_actions[0].repaired_claim_id == "clm_05"
    assert result.repair_actions[0].action == "rephrase"
    assert result.repaired_script.claims[0].claim_id == "clm_05"
    assert result.repaired_script.sections[0].claim_ids == ["clm_05"]


def test_contradicted_claim_removal_when_no_supported_correction(mock_catalog, sample_script):
    """Test contradicted claim removal when model determines no supported correction exists."""
    script = sample_script.model_copy(deep=True)
    script.sections = [sample_script.sections[1]]
    script.claims = [sample_script.claims[1]]  # clm_02
    script.sections[0].claim_ids = ["clm_02"]

    report = ScriptFactCheckReport(
        project_id="proj_alpha",
        script_version=1,
        research_run_id="run_test_01",
        assessments=[
            ClaimAssessment(
                claim_id="clm_02",
                section_id="sec_02",
                proposition="On Mars, liquid water lakes exist everywhere across the surface.",
                status="contradicted",
                risk_level="high",
                explanation="Directly contradicted by evi_02.",
                considered_evidence_ids=["evi_02"],
                contradicting_evidence_ids=["evi_02"],
                recommended_action="remove",
            )
        ],
    )
    report.recompute_summary()

    llm = MockLLMProvider()
    repair_payload = SectionRepairPayload(
        section_id="sec_02",
        revised_narration="On Mars, extreme atmospheric conditions cause liquid water to vaporize rapidly.",
        repairs=[
            ClaimRepairPayload(
                claim_id="clm_02",
                action="remove",
                target_span="On Mars, liquid water lakes exist everywhere across the surface.",
                replacement_span="",
                revised_claim_text=None,
                evidence_ids=[],
                explanation="Removed false statement about surface liquid water lakes.",
            )
        ],
    )
    llm.set_response(repair_payload)

    repairer = ScriptRepairer(llm_provider=llm)
    known_ids = {"clm_01", "clm_02", "clm_03", "clm_04"}
    result = repairer.repair_script(script, report, mock_catalog, all_known_claim_ids=known_ids)

    assert result.attempted_claims_count == 1
    assert result.resolved_claims_count == 1
    assert result.repair_actions[0].action == "remove"
    assert result.repair_actions[0].repaired_claim_id is None
    # Claim was removed from script claims list and section claim_ids!
    assert len(result.repaired_script.claims) == 0
    assert result.repaired_script.sections[0].claim_ids == []
    # No recheck was needed because no claims remain to recheck
    assert result.fact_check_report.total_claims == 0


def test_unsupported_claim_correction_or_removal(mock_catalog, sample_script):
    """Test unsupported claim correction to supported evidence."""
    unsupported_claim = ScriptClaim(
        claim_id="clm_unsupported",
        section_id="sec_01",
        claim_text="Water freezes at 5 degrees Celsius in high humidity.",
        evidence_ids=[],
        risk_level="normal",
    )
    script = sample_script.model_copy(deep=True)
    script.sections = [sample_script.sections[0].model_copy(deep=True)]
    script.sections[0].narration = "Water freezes at 5 degrees Celsius in high humidity."
    script.claims = [unsupported_claim]
    script.sections[0].claim_ids = ["clm_unsupported"]

    report = ScriptFactCheckReport(
        project_id="proj_alpha",
        script_version=1,
        research_run_id="run_test_01",
        assessments=[
            ClaimAssessment(
                claim_id="clm_unsupported",
                section_id="sec_01",
                proposition="Water freezes at 5 degrees Celsius in high humidity.",
                status="unsupported",
                risk_level="normal",
                explanation="No evidence supports 5 degrees Celsius freezing.",
                recommended_action="remove",
            )
        ],
    )
    report.recompute_summary()

    llm = MockLLMProvider()
    repair_payload = SectionRepairPayload(
        section_id="sec_01",
        revised_narration="Under standard atmospheric pressure, water freezes at 0 degrees Celsius.",
        repairs=[
            ClaimRepairPayload(
                claim_id="clm_unsupported",
                action="rephrase",
                target_span="Water freezes at 5 degrees Celsius in high humidity.",
                replacement_span="Under standard atmospheric pressure, water freezes at 0 degrees Celsius.",
                revised_claim_text="Water freezes at 0 degrees Celsius under standard pressure.",
                evidence_ids=["evi_01"],
                explanation="Corrected to verified 0 degrees Celsius freezing point from evi_01.",
            )
        ],
    )
    recheck_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_05",
                status="supported",
                explanation="Substantiated by evi_01.",
                supporting_evidence_ids=["evi_01"],
            )
        ]
    )
    llm.queue_responses([repair_payload, recheck_payload])

    repairer = ScriptRepairer(llm_provider=llm)
    known_ids = {"clm_01", "clm_02", "clm_03", "clm_04"}
    result = repairer.repair_script(script, report, mock_catalog, all_known_claim_ids=known_ids)

    assert result.attempted_claims_count == 1
    assert result.resolved_claims_count == 1
    assert result.is_fully_resolved is True


def test_conflicting_claim_preservation_without_forced_resolution(mock_catalog, sample_script):
    """Test that conflicting claims are NOT targeted for repair and uncertainty is preserved."""
    script = sample_script.model_copy(deep=True)
    report = ScriptFactCheckReport(
        project_id="proj_alpha",
        script_version=1,
        research_run_id="run_test_01",
        assessments=[
            ClaimAssessment(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="Water freezes at 0 degrees Celsius.",
                status="conflicting",  # Conflicting evidence
                risk_level="normal",
                explanation="Sources disagree on exact crystallization temperature in microgravity.",
                considered_evidence_ids=["evi_01"],
            )
        ],
    )
    report.recompute_summary()

    llm = MockLLMProvider()
    repairer = ScriptRepairer(llm_provider=llm)
    known_ids = {"clm_01", "clm_02", "clm_03", "clm_04"}
    result = repairer.repair_script(script, report, mock_catalog, all_known_claim_ids=known_ids)

    # Conflicting claims do not trigger automated repair (preserves uncertainty)
    assert result.attempted_claims_count == 0
    assert len(llm.calls) == 0  # Zero repair calls made!
    assert result.repaired_script.claims[0].claim_text == script.claims[0].claim_text


def test_not_checked_claim_preservation(mock_catalog, sample_script):
    """Test that not_checked claims are NOT targeted for repair; preserved and flagged."""
    script = sample_script.model_copy(deep=True)
    report = ScriptFactCheckReport(
        project_id="proj_alpha",
        script_version=1,
        research_run_id="run_test_01",
        assessments=[
            ClaimAssessment(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="Water freezes at 0 degrees Celsius.",
                status="not_checked",
                risk_level="normal",
                explanation="Unassessed due to timeout.",
                unassessed_reason="provider_timeout",
            )
        ],
    )
    report.recompute_summary()

    llm = MockLLMProvider()
    repairer = ScriptRepairer(llm_provider=llm)
    known_ids = {"clm_01", "clm_02", "clm_03", "clm_04"}
    result = repairer.repair_script(script, report, mock_catalog, all_known_claim_ids=known_ids)

    assert result.attempted_claims_count == 0
    assert len(llm.calls) == 0


def test_supported_claim_left_untouched(mock_catalog, sample_script):
    """Test that scripts with all supported claims trigger zero repair calls."""
    report = ScriptFactCheckReport(
        project_id="proj_alpha",
        script_version=1,
        research_run_id="run_test_01",
        assessments=[
            ClaimAssessment(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="Proposition 1.",
                status="supported",
                explanation="Verified.",
                supporting_evidence_ids=["evi_01"],
            )
        ],
    )
    report.recompute_summary()

    llm = MockLLMProvider()
    repairer = ScriptRepairer(llm_provider=llm)
    result = repairer.repair_script(sample_script, report, mock_catalog)

    assert result.attempted_claims_count == 0
    assert result.is_fully_resolved is True
    assert len(llm.calls) == 0


def test_unrelated_sections_unchanged(mock_catalog, sample_script, sample_report):
    """Verify that sections with no target claims have their narration and properties preserved verbatim."""
    llm = MockLLMProvider()
    # Mock auto-generates deterministic repair for sec_02 (which contains clm_02 and clm_03)
    repairer = ScriptRepairer(llm_provider=llm)
    result = repairer.repair_script(sample_script, sample_report, mock_catalog)

    # sec_01 and sec_03 had no problematic claims: must be 100% identical!
    orig_sec_01 = sample_script.sections[0]
    orig_sec_03 = sample_script.sections[2]

    rep_sec_01 = result.repaired_script.sections[0]
    rep_sec_03 = result.repaired_script.sections[2]

    assert rep_sec_01.narration == orig_sec_01.narration
    assert rep_sec_01.heading == orig_sec_01.heading
    assert rep_sec_01.type == orig_sec_01.type

    assert rep_sec_03.narration == orig_sec_03.narration
    assert rep_sec_03.heading == orig_sec_03.heading
    assert rep_sec_03.type == orig_sec_03.type


def test_multiple_target_claims_in_one_script(mock_catalog, sample_script, sample_report):
    """Test repairing multiple target claims in a multi-section script."""
    llm = MockLLMProvider()
    # Mock fallback will qualify clm_03 and rephrase clm_02
    repairer = ScriptRepairer(llm_provider=llm)
    result = repairer.repair_script(sample_script, sample_report, mock_catalog)

    assert result.attempted_claims_count == 2
    assert result.resolved_claims_count == 2
    assert result.is_fully_resolved is True
    assert len(result.repair_actions) == 2


# ==============================================================================
# 2. Claim Identity, Evidence Validation & Provenance Tests
# ==============================================================================

def test_new_claim_ids_when_factual_meaning_changes(mock_catalog, sample_script):
    """Verify that when a repaired proposition changes meaning, a new claim ID is allocated."""
    script = sample_script.model_copy(deep=True)
    script.sections = [sample_script.sections[0]]
    script.claims = [sample_script.claims[0]]  # clm_01

    report = ScriptFactCheckReport(
        project_id="proj_alpha",
        script_version=1,
        research_run_id="run_test_01",
        assessments=[
            ClaimAssessment(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="Water freezes at 0 degrees Celsius under standard atmospheric pressure.",
                status="partially_supported",
                explanation="Needs revision.",
                considered_evidence_ids=["evi_01"],
            )
        ],
    )
    report.recompute_summary()

    llm = MockLLMProvider()
    # Proposition changed: "freezes at 0" -> "boils at 100" (factual meaning changed!)
    repair_payload = SectionRepairPayload(
        section_id="sec_01",
        revised_narration="Water boils at 100 degrees Celsius under standard atmospheric pressure.",
        repairs=[
            ClaimRepairPayload(
                claim_id="clm_01",
                action="rephrase",
                target_span="Did you know water freezes at 0 degrees Celsius under standard atmospheric pressure?",
                replacement_span="Water boils at 100 degrees Celsius under standard atmospheric pressure.",
                revised_claim_text="Water boils at 100 degrees Celsius under standard pressure.",
                evidence_ids=["evi_01"],
                explanation="Changed to boiling point.",
            )
        ],
    )
    recheck_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_05",
                status="supported",
                explanation="Substantiated.",
                supporting_evidence_ids=["evi_01"],
            )
        ]
    )
    llm.queue_responses([repair_payload, recheck_payload])

    repairer = ScriptRepairer(llm_provider=llm)
    # Pass known IDs containing clm_01, clm_02, clm_03, clm_04
    known_ids = {"clm_01", "clm_02", "clm_03", "clm_04"}
    result = repairer.repair_script(script, report, mock_catalog, all_known_claim_ids=known_ids)

    # Must allocate clm_05!
    action = result.repair_actions[0]
    assert action.original_claim_id == "clm_01"
    assert action.repaired_claim_id == "clm_05"
    assert result.repaired_script.claims[0].claim_id == "clm_05"


def test_evidence_ids_validated_against_supplied_repair_catalog(mock_catalog, sample_script):
    """Verify that cited evidence IDs must be in the supplied repair evidence."""
    script = sample_script.model_copy(deep=True)
    script.sections = [sample_script.sections[1]]
    script.claims = [sample_script.claims[2]]  # clm_03

    report = ScriptFactCheckReport(
        project_id="proj_alpha",
        script_version=1,
        research_run_id="run_test_01",
        assessments=[
            ClaimAssessment(
                claim_id="clm_03",
                section_id="sec_02",
                proposition="Mars atmospheric pressure is under 1% of Earth's.",
                status="partially_supported",
                explanation="Needs qualifier.",
                considered_evidence_ids=["evi_02"],
            )
        ],
    )
    report.recompute_summary()

    llm = MockLLMProvider()
    # 1st attempt cites fabricated evidence ID 'evi_fabricated_999'
    invalid_repair = SectionRepairPayload(
        section_id="sec_02",
        revised_narration="Revised narration.",
        repairs=[
            ClaimRepairPayload(
                claim_id="clm_03",
                action="qualify",
                target_span="Meanwhile, Martian atmospheric pressure is under 1% of Earth's.",
                replacement_span="Mars pressure is 610 Pascals.",
                revised_claim_text="Mars pressure is 610 Pascals.",
                evidence_ids=["evi_fabricated_999"],  # Invalid!
                explanation="Cited fabricated evidence.",
            )
        ],
    )
    # 2nd attempt corrects citation to 'evi_02'
    valid_repair = SectionRepairPayload(
        section_id="sec_02",
        revised_narration="Revised narration.",
        repairs=[
            ClaimRepairPayload(
                claim_id="clm_03",
                action="qualify",
                target_span="Meanwhile, Martian atmospheric pressure is under 1% of Earth's.",
                replacement_span="Mars pressure is 610 Pascals.",
                revised_claim_text="Mars pressure is 610 Pascals.",
                evidence_ids=["evi_02"],  # Valid!
                explanation="Cited valid evidence.",
            )
        ],
    )
    recheck_resp = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_05",
                status="supported",
                explanation="Verified.",
                supporting_evidence_ids=["evi_02"],
            )
        ]
    )
    llm.queue_responses([invalid_repair, valid_repair, recheck_resp])

    repairer = ScriptRepairer(llm_provider=llm)
    known_ids = {"clm_01", "clm_02", "clm_03", "clm_04"}
    result = repairer.repair_script(script, report, mock_catalog, all_known_claim_ids=known_ids)

    # Proves corrective retry occurred and succeeded!
    assert len(llm.calls) == 3
    assert "CORRECTION REQUIRED" in llm.calls[1]["prompt"]
    assert result.resolved_claims_count == 1


def test_invalid_target_claim_ids_rejected(mock_catalog, sample_script):
    """Verify that model returning invalid/extra claim IDs in repairs is rejected."""
    script = sample_script.model_copy(deep=True)
    script.sections = [sample_script.sections[1]]
    script.claims = [sample_script.claims[2]]  # clm_03

    report = ScriptFactCheckReport(
        project_id="proj_alpha",
        script_version=1,
        research_run_id="run_test_01",
        assessments=[
            ClaimAssessment(
                claim_id="clm_03",
                section_id="sec_02",
                proposition="Mars atmospheric pressure is under 1% of Earth's.",
                status="partially_supported",
                explanation="Needs qualifier.",
                considered_evidence_ids=["evi_02"],
            )
        ],
    )
    report.recompute_summary()

    llm = MockLLMProvider()
    # Emits an unknown claim ID 'clm_fake_999' instead of 'clm_03'
    bad_repair = SectionRepairPayload(
        section_id="sec_02",
        revised_narration="Revised.",
        repairs=[
            ClaimRepairPayload(
                claim_id="clm_fake_999",
                action="qualify",
                target_span="Meanwhile, Martian atmospheric pressure is under 1% of Earth's.",
                replacement_span="Fake.",
                revised_claim_text="Fake.",
                evidence_ids=["evi_02"],
                explanation="Unknown claim ID.",
            )
        ],
    )
    good_repair = SectionRepairPayload(
        section_id="sec_02",
        revised_narration="Revised.",
        repairs=[
            ClaimRepairPayload(
                claim_id="clm_03",
                action="qualify",
                target_span="Meanwhile, Martian atmospheric pressure is under 1% of Earth's.",
                replacement_span="Correct.",
                revised_claim_text="Correct.",
                evidence_ids=["evi_02"],
                explanation="Correct claim ID.",
            )
        ],
    )
    recheck_resp = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_05",
                status="supported",
                explanation="Verified.",
                supporting_evidence_ids=["evi_02"],
            )
        ]
    )
    llm.queue_responses([bad_repair, good_repair, recheck_resp])

    repairer = ScriptRepairer(llm_provider=llm)
    known_ids = {"clm_01", "clm_02", "clm_03", "clm_04"}
    result = repairer.repair_script(script, report, mock_catalog, all_known_claim_ids=known_ids)

    assert len(llm.calls) == 3
    assert "clm_fake_999" in llm.calls[1]["prompt"]


def test_malformed_response_and_one_bounded_retry(mock_catalog, sample_script):
    """Test malformed LLM response triggers exactly one corrective retry and succeeds."""
    script = sample_script.model_copy(deep=True)
    script.sections = [sample_script.sections[1]]
    script.claims = [sample_script.claims[2]]

    report = ScriptFactCheckReport(
        project_id="proj_alpha",
        script_version=1,
        research_run_id="run_test_01",
        assessments=[
            ClaimAssessment(
                claim_id="clm_03",
                section_id="sec_02",
                proposition="Mars atmospheric pressure is under 1% of Earth's.",
                status="partially_supported",
                explanation="Needs qualifier.",
                considered_evidence_ids=["evi_02"],
            )
        ],
    )
    report.recompute_summary()

    llm = MockLLMProvider()
    malformed_err = LLMResponseMalformedError("Syntax error: missing brace.")
    valid_repair = SectionRepairPayload(
        section_id="sec_02",
        revised_narration="Valid narration.",
        repairs=[
            ClaimRepairPayload(
                claim_id="clm_03",
                action="qualify",
                target_span="Meanwhile, Martian atmospheric pressure is under 1% of Earth's.",
                replacement_span="Valid proposition.",
                revised_claim_text="Valid proposition.",
                evidence_ids=["evi_02"],
                explanation="Valid explanation.",
            )
        ],
    )
    recheck_resp = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_05",
                status="supported",
                explanation="Verified.",
                supporting_evidence_ids=["evi_02"],
            )
        ]
    )
    llm.queue_responses([malformed_err, valid_repair, recheck_resp])

    repairer = ScriptRepairer(llm_provider=llm)
    known_ids = {"clm_01", "clm_02", "clm_03", "clm_04"}
    result = repairer.repair_script(script, report, mock_catalog, all_known_claim_ids=known_ids)

    assert len(llm.calls) == 3
    assert result.resolved_claims_count == 1


# ==============================================================================
# 3. Recheck Failure, Provider Error & Safety Invariant Tests
# ==============================================================================

def test_provider_failure_without_false_resolution(mock_catalog, sample_script):
    """Test that provider failure on repair leaves claims unresolved without false success."""
    script = sample_script.model_copy(deep=True)
    script.sections = [sample_script.sections[1]]
    script.claims = [sample_script.claims[2]]

    report = ScriptFactCheckReport(
        project_id="proj_alpha",
        script_version=1,
        research_run_id="run_test_01",
        assessments=[
            ClaimAssessment(
                claim_id="clm_03",
                section_id="sec_02",
                proposition="Mars atmospheric pressure is under 1% of Earth's.",
                status="partially_supported",
                explanation="Needs qualifier.",
                considered_evidence_ids=["evi_02"],
            )
        ],
    )
    report.recompute_summary()

    llm = MockLLMProvider()
    llm.set_error(LLMUnavailableError("Ollama network connection refused."))

    repairer = ScriptRepairer(llm_provider=llm)
    known_ids = {"clm_01", "clm_02", "clm_03", "clm_04"}
    result = repairer.repair_script(script, report, mock_catalog, all_known_claim_ids=known_ids)

    assert result.attempted_claims_count == 1
    assert result.resolved_claims_count == 0
    assert result.unresolved_claims_count == 1
    assert result.is_fully_resolved is False
    assert result.repair_actions[0].is_resolved is False


def test_failed_recheck_remains_unresolved_not_checked(mock_catalog, sample_script):
    """Test that when recheck fails, the repaired claim is marked not_checked and remains unresolved."""
    script = sample_script.model_copy(deep=True)
    script.sections = [sample_script.sections[1]]
    script.claims = [sample_script.claims[2]]

    report = ScriptFactCheckReport(
        project_id="proj_alpha",
        script_version=1,
        research_run_id="run_test_01",
        assessments=[
            ClaimAssessment(
                claim_id="clm_03",
                section_id="sec_02",
                proposition="Mars atmospheric pressure is under 1% of Earth's.",
                status="partially_supported",
                explanation="Needs qualifier.",
                considered_evidence_ids=["evi_02"],
            )
        ],
    )
    report.recompute_summary()

    llm = MockLLMProvider()
    # 1. Repair succeeds
    repair_payload = SectionRepairPayload(
        section_id="sec_02",
        revised_narration="Valid narration.",
        repairs=[
            ClaimRepairPayload(
                claim_id="clm_03",
                action="qualify",
                target_span="Meanwhile, Martian atmospheric pressure is under 1% of Earth's.",
                replacement_span="Repaired text.",
                revised_claim_text="Repaired text.",
                evidence_ids=["evi_02"],
                explanation="Repaired.",
            )
        ],
    )
    # 2. Recheck provider fails
    recheck_err = LLMUnavailableError("Recheck provider timeout.")
    llm.queue_responses([repair_payload, recheck_err])

    repairer = ScriptRepairer(llm_provider=llm)
    known_ids = {"clm_01", "clm_02", "clm_03", "clm_04"}
    result = repairer.repair_script(script, report, mock_catalog, all_known_claim_ids=known_ids)

    # Claim must NOT be considered resolved!
    assert result.attempted_claims_count == 1
    assert result.resolved_claims_count == 0
    assert result.unresolved_claims_count == 1
    assert result.is_fully_resolved is False

    # The rechecked assessment must be not_checked!
    rep_claim = result.repaired_script.claims[0]
    assessment = next(a for a in result.fact_check_report.assessments if a.claim_id == rep_claim.claim_id)
    assert assessment.status == "not_checked"
    assert assessment.unassessed_reason in ("recheck_failed", "provider_timeout", "provider_unavailable")


def test_no_recursive_repair(mock_catalog, sample_script):
    """Assert strictly that repair is single-pass: exactly 1 repair pass, 1 recheck pass, NO loops."""
    script = sample_script.model_copy(deep=True)
    script.sections = [sample_script.sections[1]]
    script.claims = [sample_script.claims[2]]

    report = ScriptFactCheckReport(
        project_id="proj_alpha",
        script_version=1,
        research_run_id="run_test_01",
        assessments=[
            ClaimAssessment(
                claim_id="clm_03",
                section_id="sec_02",
                proposition="Mars atmospheric pressure is under 1% of Earth's.",
                status="partially_supported",
                explanation="Needs qualifier.",
                considered_evidence_ids=["evi_02"],
            )
        ],
    )
    report.recompute_summary()

    llm = MockLLMProvider()
    # 1. Repair succeeds
    repair_payload = SectionRepairPayload(
        section_id="sec_02",
        revised_narration="Valid narration.",
        repairs=[
            ClaimRepairPayload(
                claim_id="clm_03",
                action="qualify",
                target_span="Meanwhile, Martian atmospheric pressure is under 1% of Earth's.",
                replacement_span="Repaired text.",
                revised_claim_text="Repaired text.",
                evidence_ids=["evi_02"],
                explanation="Repaired.",
            )
        ],
    )
    # 2. Recheck reports contradicted (repair was insufficient)
    recheck_resp = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_05",
                status="contradicted",  # Recheck found contradicted!
                explanation="Still contradicted.",
                contradicting_evidence_ids=["evi_02"],
            )
        ]
    )
    llm.queue_responses([repair_payload, recheck_resp])

    repairer = ScriptRepairer(llm_provider=llm)
    known_ids = {"clm_01", "clm_02", "clm_03", "clm_04"}
    result = repairer.repair_script(script, report, mock_catalog, all_known_claim_ids=known_ids)

    # Must STOP after exactly 2 LLM calls (1 repair + 1 recheck). No secondary repair loop!
    assert len(llm.calls) == 2
    assert result.attempted_claims_count == 1
    assert result.resolved_claims_count == 0
    assert result.is_fully_resolved is False


def test_bidirectional_claim_mapping_consistency(mock_catalog, sample_script, sample_report):
    """Verify bidirectional claim-to-section and section-to-claim consistency after repair."""
    llm = MockLLMProvider()
    repairer = ScriptRepairer(llm_provider=llm)
    result = repairer.repair_script(sample_script, sample_report, mock_catalog)

    repaired_script = result.repaired_script

    # 1. Every claim's section_id points to an existing section
    sec_ids = {s.id for s in repaired_script.sections}
    for c in repaired_script.claims:
        assert c.section_id in sec_ids

    # 2. Every section's claim_ids contains exactly the claims belonging to it
    for s in repaired_script.sections:
        expected_claims_in_sec = [c.claim_id for c in repaired_script.claims if c.section_id == s.id]
        assert s.claim_ids == expected_claims_in_sec


def test_creative_flexibility_claims_do_not_bypass_factual_scrutiny(mock_catalog, sample_script):
    """Verify that a creative_flexibility claim that is contradicted triggers repair."""
    creative_claim = ScriptClaim(
        claim_id="clm_creative",
        section_id="sec_01",
        claim_text="Water freezes at 50 degrees Celsius due to cosmic energy.",
        evidence_ids=[],
        risk_level="creative_flexibility",
    )
    script = sample_script.model_copy(deep=True)
    script.sections = [sample_script.sections[0].model_copy(deep=True)]
    script.sections[0].narration = "Water freezes at 50 degrees Celsius due to cosmic energy."
    script.claims = [creative_claim]
    script.sections[0].claim_ids = ["clm_creative"]

    report = ScriptFactCheckReport(
        project_id="proj_alpha",
        script_version=1,
        research_run_id="run_test_01",
        assessments=[
            ClaimAssessment(
                claim_id="clm_creative",
                section_id="sec_01",
                proposition="Water freezes at 50 degrees Celsius due to cosmic energy.",
                status="contradicted",  # False factual assertion!
                risk_level="creative_flexibility",
                explanation="Contradicted by physics evidence (evi_01).",
                considered_evidence_ids=["evi_01"],
                contradicting_evidence_ids=["evi_01"],
            )
        ],
    )
    report.recompute_summary()

    llm = MockLLMProvider()
    # Repair removes the false claim
    repair_payload = SectionRepairPayload(
        section_id="sec_01",
        revised_narration="Water freezes at 0 degrees Celsius.",
        repairs=[
            ClaimRepairPayload(
                claim_id="clm_creative",
                action="remove",
                target_span="Water freezes at 50 degrees Celsius due to cosmic energy.",
                replacement_span="",
                revised_claim_text=None,
                evidence_ids=[],
                explanation="Removed false 50 degrees assertion.",
            )
        ],
    )
    llm.set_response(repair_payload)

    repairer = ScriptRepairer(llm_provider=llm)
    known_ids = {"clm_01", "clm_02", "clm_03", "clm_04"}
    result = repairer.repair_script(script, report, mock_catalog, all_known_claim_ids=known_ids)

    # Must NOT bypass repair!
    assert result.attempted_claims_count == 1
    assert result.resolved_claims_count == 1
    assert result.repair_actions[0].action == "remove"


def test_security_validations(mock_catalog, sample_script, sample_report):
    """Test project ID mismatch and catalog run mismatch raise ScriptRepairSecurityError."""
    llm = MockLLMProvider()
    repairer = ScriptRepairer(llm_provider=llm)

    # 1. Report project mismatch
    tampered_report = sample_report.model_copy(deep=True)
    tampered_report.project_id = "proj_impostor"
    with pytest.raises(ScriptRepairSecurityError, match="does not match script project_id"):
        repairer.repair_script(sample_script, tampered_report, mock_catalog)

    # 2. Catalog run mismatch
    tampered_catalog = ValidatedEvidenceCatalog(
        run_id="run_alien_999",
        evidence_by_id=mock_catalog.evidence_by_id,
        sources_by_id=mock_catalog.sources_by_id,
    )
    with pytest.raises(ScriptRepairSecurityError, match="does not match script research_run_id"):
        repairer.repair_script(sample_script, sample_report, tampered_catalog)


def test_two_stage_exact_span_failure_end_to_end_regression(mock_catalog, sample_script):
    """Verify that when both initial repair and corrective retry emit invalid target spans:
    - Exactly 2 repair attempts occur (initial + 1 corrective retry).
    - No additional retry occurs.
    - Original section narration remains character-for-character identical.
    - The affected claim remains active and unresolved.
    - Its previous fact-check assessment is retained.
    - No replacement narration is applied.
    - No whole-section fallback is invoked.
    - Resolution counters reflect the unresolved claim.
    - Readiness is not marked successful.
    """
    script = sample_script.model_copy(deep=True)
    script.sections = [sample_script.sections[1].model_copy(deep=True)]  # sec_02
    orig_narration = script.sections[0].narration
    target_claim = sample_script.claims[1].model_copy(deep=True)  # clm_02
    script.claims = [target_claim]
    script.sections[0].claim_ids = [target_claim.claim_id]

    report = ScriptFactCheckReport(
        project_id="proj_alpha",
        script_version=1,
        research_run_id="run_test_01",
        assessments=[
            ClaimAssessment(
                claim_id="clm_02",
                section_id="sec_02",
                proposition="On Mars, liquid water lakes exist everywhere across the surface.",
                status="contradicted",
                risk_level="high",
                explanation="Contradicted by physics and atmospheric evidence.",
                considered_evidence_ids=["evi_02"],
                contradicting_evidence_ids=["evi_02"],
                recommended_action="remove",
            )
        ],
    )
    report.recompute_summary()
    assert report.is_ready is False

    llm = MockLLMProvider()

    # 1. First repair response has nonexistent target_span
    bad_attempt_1 = SectionRepairPayload(
        section_id="sec_02",
        revised_narration="Whole section rewritten attempting to bypass exact-span enforcement.",
        repairs=[
            ClaimRepairPayload(
                claim_id="clm_02",
                action="rephrase",
                target_span="This exact sentence does not exist anywhere in section narration at all.",
                replacement_span="Attempted replacement text.",
                revised_claim_text="Mars has lakes.",
                evidence_ids=["evi_02"],
                explanation="First attempt with invalid span.",
            )
        ],
    )

    # 2. Corrective retry ALSO has invalid target_span (blank / whitespace-only span)
    bad_attempt_2 = SectionRepairPayload(
        section_id="sec_02",
        revised_narration="Another whole section rewrite attempting to bypass exact-span enforcement.",
        repairs=[
            ClaimRepairPayload(
                claim_id="clm_02",
                action="rephrase",
                target_span="   ",  # Blank / whitespace-only span!
                replacement_span="Attempted replacement text on retry.",
                revised_claim_text="Mars has lakes retry.",
                evidence_ids=["evi_02"],
                explanation="Second attempt with empty span.",
            )
        ],
    )

    llm.queue_responses([bad_attempt_1, bad_attempt_2])

    repairer = ScriptRepairer(llm_provider=llm)
    known_ids = {"clm_01", "clm_02", "clm_03", "clm_04"}
    result = repairer.repair_script(script, report, mock_catalog, all_known_claim_ids=known_ids)

    # 1. Exactly 2 repair attempts occurred (1 initial + 1 corrective retry); no loops
    assert len(llm.calls) == 2
    assert "CORRECTION REQUIRED" in llm.calls[1]["prompt"]

    # 2. Original section narration remains character-for-character identical
    repaired_sec = result.repaired_script.sections[0]
    assert repaired_sec.narration == orig_narration
    assert "Whole section rewritten" not in repaired_sec.narration
    assert "Attempted replacement text" not in repaired_sec.narration

    # 3. The affected claim remains active in the script and unresolved
    assert len(result.repaired_script.claims) == 1
    assert result.repaired_script.claims[0].claim_id == "clm_02"
    assert result.repaired_script.sections[0].claim_ids == ["clm_02"]

    # 4. Action record reflects failure and unresolved state
    assert len(result.repair_actions) == 1
    action = result.repair_actions[0]
    assert action.original_claim_id == "clm_02"
    assert action.repaired_claim_id == "clm_02"
    assert action.is_resolved is False
    assert action.action == "keep"
    assert "Repair failed" in action.explanation

    # 5. Its previous fact-check assessment is retained
    assert len(result.fact_check_report.assessments) == 1
    retained_a = result.fact_check_report.assessments[0]
    assert retained_a.claim_id == "clm_02"
    assert retained_a.status == "contradicted"
    assert retained_a.explanation == "Contradicted by physics and atmospheric evidence."

    # 6. Resolution counters reflect the unresolved claim
    assert result.attempted_claims_count == 1
    assert result.resolved_claims_count == 0
    assert result.unresolved_claims_count == 1
    assert result.is_fully_resolved is False

    # 7. Readiness is NOT marked successful
    assert result.fact_check_report.is_ready is False
    assert result.fact_check_report.contradicted_count == 1

