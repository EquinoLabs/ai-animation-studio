"""Phase 5 Checkpoint 4 Lifecycle Integration Tests.

Verifies end-to-end integration of ScriptFactChecker and ScriptRepairer into ScriptService,
including:
- Mandatory Requirement A: Independent evidence retrieval and contradiction discovery
- Mandatory Requirement B: Application-controlled deterministic risk classification
- Mandatory Requirement C: Programmatic sentence-level replacement and verbatim preservation
- Service lifecycle: start_script, revise_script, automated fact-checking, surgical repair,
  readiness gating on finalize_script, and immutable version progression.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch
import pytest

import backend.app.services.scriptwriter.service as script_service_module
from backend.app.domain.fact_check import (
    BatchFactCheckResponsePayload,
    ClaimAssessment,
    ClaimAssessmentPayload,
    ClaimRiskLevel,
    ScriptFactCheckReport,
)
from backend.app.domain.project import utc_now
from backend.app.domain.research import (
    EvidenceRecord,
    PlannedQuery,
    ResearchPlan,
    ResearchRun,
    SourceRecord,
)
from backend.app.domain.script import (
    GeneratedScriptPayload,
    Script,
    ScriptClaim,
    ScriptClaimPayload,
    ScriptOutline,
    ScriptSection,
    ScriptSectionPayload,
)
from backend.app.llm.base import LLMProvider
from backend.app.llm.mock import MockLLMProvider
from backend.app.services.project.service import Project, ProjectService
from backend.app.services.research.storage import ResearchStorageService
from backend.app.services.scriptwriter.classifier import classify_claim_risk
from backend.app.services.scriptwriter.fact_checker import ScriptFactChecker
from backend.app.services.scriptwriter.outliner import ValidatedEvidenceCatalog
from backend.app.services.scriptwriter.repairer import (
    ClaimRepairActionRecord,
    ClaimRepairPayload,
    ScriptRepairer,
    SectionRepairPayload,
)
from backend.app.services.scriptwriter.service import (
    ScriptFinalizationError,
    ScriptService,
    ScriptStorageError,
    ScriptValidationError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_catalog() -> ValidatedEvidenceCatalog:
    """Evidence catalog with supporting, alternative, and contradictory evidence."""
    sources = {
        "src_01": SourceRecord(
            source_id="src_01",
            project_id="proj_lifecycle",
            title="Thermodynamics Journal",
            url="https://example.com/thermo",
        ),
        "src_02": SourceRecord(
            source_id="src_02",
            project_id="proj_lifecycle",
            title="Planetary Science Review",
            url="https://example.com/mars",
        ),
    }
    evidence = {
        "evi_01": EvidenceRecord(
            evidence_id="evi_01",
            project_id="proj_lifecycle",
            run_id="run_life_01",
            source_id="src_01",
            excerpt="Water freezes into solid crystalline ice at exactly 0 degrees Celsius under standard atmospheric pressure.",
            target_claim="Water freezing threshold",
        ),
        "evi_02": EvidenceRecord(
            evidence_id="evi_02",
            project_id="proj_lifecycle",
            run_id="run_life_01",
            source_id="src_02",
            excerpt="Mars atmospheric pressure is strictly 610 Pascals, which is less than 1% of Earth's atmosphere.",
            target_claim="Mars atmospheric pressure",
        ),
        "evi_contradiction": EvidenceRecord(
            evidence_id="evi_contradiction",
            project_id="proj_lifecycle",
            run_id="run_life_01",
            source_id="src_01",
            excerpt="Contrary to common misconceptions, boiling water at 100 degrees Celsius is standard; water never freezes at 50 degrees Celsius.",
            target_claim="Freezing temperatures",
        ),
        "evi_antonym": EvidenceRecord(
            evidence_id="evi_antonym",
            project_id="proj_lifecycle",
            run_id="run_life_01",
            source_id="src_02",
            excerpt="Recent measurements showed global temperature decreased by 20% in the outer region.",
            target_claim="Temperature trends",
        ),
    }
    return ValidatedEvidenceCatalog(
        run_id="run_life_01",
        evidence_by_id=evidence,
        sources_by_id=sources,
    )


@pytest.fixture
def lifecycle_env(tmp_path: Path):
    """Setup project, research run, and storage environment."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    project_service = ProjectService(projects_root=projects_dir)
    project = project_service.create_project(title="Lifecycle Project")
    pid = project.metadata.project_id

    research_storage = ResearchStorageService(projects_root=projects_dir)
    plan = ResearchPlan(
        plan_id="plan_01",
        project_id=pid,
        topic="Physics and Planetary Science",
        research_objective="Investigate thermodynamics and planetary physics.",
        research_questions=["What is the freezing point?"],
        planned_queries=[
            PlannedQuery(
                query_id="qry_01",
                query_text="water freezing point",
                purpose="Investigate freezing point",
            )
        ],
    )
    research_storage.save_plan(plan)

    run = ResearchRun(
        run_id="run_life_01",
        plan_id="plan_01",
        project_id=pid,
        status="completed",
        started_at=utc_now(),
        completed_at=utc_now(),
    )
    research_storage.save_run(run)

    # Save evidence and sources to disk
    sources = [
        SourceRecord(source_id="src_01", project_id=pid, title="Thermodynamics Journal", url="https://example.com/thermo"),
        SourceRecord(source_id="src_02", project_id=pid, title="Planetary Science Review", url="https://example.com/mars"),
    ]
    for s in sources:
        research_storage.save_source(pid, s)

    evidence = [
        EvidenceRecord(
            evidence_id="evi_01",
            project_id=pid,
            run_id="run_life_01",
            source_id="src_01",
            excerpt="Water freezes into solid crystalline ice at exactly 0 degrees Celsius under standard atmospheric pressure.",
            target_claim="Water freezing threshold",
        ),
        EvidenceRecord(
            evidence_id="evi_02",
            project_id=pid,
            run_id="run_life_01",
            source_id="src_02",
            excerpt="Mars atmospheric pressure is strictly 610 Pascals, which is less than 1% of Earth's atmosphere.",
            target_claim="Mars atmospheric pressure",
        ),
        EvidenceRecord(
            evidence_id="evi_contradiction",
            project_id=pid,
            run_id="run_life_01",
            source_id="src_01",
            excerpt="Contrary to common misconceptions, boiling water at 100 degrees Celsius is standard; water never freezes at 50 degrees Celsius.",
            target_claim="Freezing temperatures",
        ),
        EvidenceRecord(
            evidence_id="evi_antonym",
            project_id=pid,
            run_id="run_life_01",
            source_id="src_02",
            excerpt="Recent measurements showed global temperature decreased by 20% in the outer region.",
            target_claim="Temperature trends",
        ),
    ]
    for ev in evidence:
        research_storage.save_evidence(pid, ev)

    llm = MockLLMProvider()
    service = ScriptService(
        project_service=project_service,
        llm_provider=llm,
        research_storage=research_storage,
    )

    return {
        "pid": pid,
        "service": service,
        "llm": llm,
        "project_service": project_service,
        "research_storage": research_storage,
    }


# ===========================================================================
# 1. Mandatory Requirement A: Independent Evidence Coverage
# ===========================================================================

def test_independent_evidence_retrieval_finds_unlinked_supporting_evidence(mock_catalog):
    """Verify that when a claim has no cited evidence IDs, candidate retrieval finds matching catalog evidence."""
    checker = ScriptFactChecker(discover_candidates=True)

    unlinked_claim = ScriptClaim(
        claim_id="clm_test",
        section_id="sec_01",
        claim_text="Under normal atmospheric pressure, water freezes at 0 degrees Celsius.",
        evidence_ids=[],  # Completely unlinked!
        risk_level="high",
    )

    candidates = checker.retrieve_candidate_evidence_for_claim(unlinked_claim, mock_catalog, max_candidates=5)
    assert "evi_01" in candidates
    assert len(candidates) <= 5


def test_independent_evidence_retrieval_discovers_contradictory_evidence(mock_catalog):
    """Verify that candidate retrieval discovers contradictory evidence with conflicting numbers or antonyms."""
    checker = ScriptFactChecker(discover_candidates=True)

    # Claim asserts false figure: water freezes at 50 degrees Celsius
    false_number_claim = ScriptClaim(
        claim_id="clm_bad_num",
        section_id="sec_01",
        claim_text="Liquid water freezes at 50 degrees Celsius.",
        evidence_ids=[],
        risk_level="high",
    )
    candidates_num = checker.retrieve_candidate_evidence_for_claim(false_number_claim, mock_catalog, max_candidates=5)
    # evi_contradiction specifically addresses water freezing and mentions 50 degrees Celsius refutation
    assert "evi_contradiction" in candidates_num or "evi_01" in candidates_num

    # Claim asserts antonym: global temperature increased by 20%
    antonym_claim = ScriptClaim(
        claim_id="clm_antonym",
        section_id="sec_01",
        claim_text="Global temperature increased by 20% in the region.",
        evidence_ids=[],
        risk_level="high",
    )
    candidates_antonym = checker.retrieve_candidate_evidence_for_claim(antonym_claim, mock_catalog, max_candidates=5)
    # evi_antonym has "temperature decreased by 20%"
    assert "evi_antonym" in candidates_antonym


def test_independent_evidence_retrieval_is_bounded_and_deterministic(mock_catalog):
    """Verify that candidate retrieval is strictly bounded and yields deterministic order."""
    checker = ScriptFactChecker(discover_candidates=True)

    claim = ScriptClaim(
        claim_id="clm_bound",
        section_id="sec_01",
        claim_text="Mars atmospheric pressure is under 1% of Earth.",
        evidence_ids=[],
        risk_level="high",
    )

    res1 = checker.retrieve_candidate_evidence_for_claim(claim, mock_catalog, max_candidates=2)
    res2 = checker.retrieve_candidate_evidence_for_claim(claim, mock_catalog, max_candidates=2)

    assert len(res1) <= 2
    assert res1 == res2
    assert "evi_02" in res1


# ===========================================================================
# 2. Mandatory Requirement B: Application-Controlled Risk Classification
# ===========================================================================

def test_risk_classification_rules():
    """Verify deterministic risk classification rules across all categories."""
    # High risk: statistics, percentages, numbers
    assert classify_claim_risk("The treatment reduced risk by 20%.") == "high"
    assert classify_claim_risk("There are 500 Pascals of atmospheric pressure.") == "high"
    assert classify_claim_risk("The value is 3.1415.") == "high"

    # High risk: dates and years
    assert classify_claim_risk("The mission launched in 1969.") == "high"
    assert classify_claim_risk("The conference took place in October.") == "high"

    # High risk: scientific mechanisms and causal terms
    assert classify_claim_risk("Increased greenhouse gas concentration causes global warming.") == "high"
    assert classify_claim_risk("High pressure results in rapid crystallization.") == "high"
    assert classify_claim_risk("Water temperature drops below the freezing threshold.") == "high"
    assert classify_claim_risk("The cellular mitochondria generate chemical energy.") == "high"

    # High risk: named entities
    assert classify_claim_risk("Albert Einstein published four groundbreaking papers.") == "high"
    assert classify_claim_risk("Mount Everest is the highest peak above sea level.") == "high"

    # Creative flexibility: pure metaphors without factual claims
    assert classify_claim_risk("Imagine the universe as a grand cosmic symphony.") == "creative_flexibility"
    assert classify_claim_risk("Picture childhood like an endless summer afternoon.") == "creative_flexibility"
    assert classify_claim_risk("Have you ever wondered what lies beyond?") == "creative_flexibility"

    # Normal risk: standard narrative statements without specific high-risk triggers
    assert classify_claim_risk("The forest provides habitat for diverse wildlife.") == "normal"
    assert classify_claim_risk("The ancient library holds thousands of historical manuscripts.") == "normal"


def test_mixed_factual_and_metaphor_sentences_never_escape_scrutiny():
    """Verify that sentences with both stylistic framing and factual propositions are classified as high risk."""
    mixed_sentence = "Imagine a cosmic dance where the core temperature reaches 15 million Celsius."
    # Contains metaphor ('Imagine', 'cosmic dance') BUT ALSO concrete numbers ('15 million') and scientific terms ('temperature', 'Celsius')
    assert classify_claim_risk(mixed_sentence) == "high"


# ===========================================================================
# 3. Mandatory Requirement C: Surgical Sentence Preservation
# ===========================================================================

def test_surgical_sentence_preservation_replaces_only_target_span():
    """Verify that non-target sentences in a repaired section are character-for-character identical to the original."""
    sentence_1 = "Welcome to the extreme world of thermodynamics."
    sentence_2_bad = "Did you know that water freezes at 50 degrees Celsius under standard atmospheric pressure?"
    sentence_2_good = "Did you know that water freezes at 0 degrees Celsius under standard atmospheric pressure?"
    sentence_3 = "Let us explore why this fundamental transition shapes our climate."

    full_narration = f"{sentence_1} {sentence_2_bad} {sentence_3}"

    section = ScriptSection(
        id="sec_01",
        type="hook",
        heading="Introduction",
        narration=full_narration,
        claim_ids=["clm_01"],
    )

    claim = ScriptClaim(
        claim_id="clm_01",
        section_id="sec_01",
        claim_text="Water freezes at 50 degrees Celsius.",
        evidence_ids=["evi_01"],
        risk_level="high",
    )

    repairer = ScriptRepairer(llm_provider=MockLLMProvider())

    repair_payload = SectionRepairPayload(
        section_id="sec_01",
        repairs=[
            ClaimRepairPayload(
                claim_id="clm_01",
                action="rephrase",
                target_span=sentence_2_bad,
                replacement_span=sentence_2_good,
                revised_claim_text="Water freezes at 0 degrees Celsius under standard pressure.",
                evidence_ids=["evi_01"],
                explanation="Corrected false temperature 50C to 0C.",
            )
        ],
    )

    assessments = {
        "clm_01": ClaimAssessment(
            claim_id="clm_01",
            section_id="sec_01",
            proposition="Water freezes at 50 degrees Celsius.",
            status="contradicted",
            risk_level="high",
            explanation="False temperature.",
            recommended_action="rephrase",
        )
    }

    result = repairer._apply_section_repairs(
        payload=repair_payload,
        section=section,
        target_claims=[claim],
        target_assessments=assessments,
        catalog=None,
        known_ids={"clm_01"},
    )

    repaired_narration = result["revised_narration"]

    # Verifiable surgical preservation: non-target sentences must be exact
    assert sentence_1 in repaired_narration
    assert sentence_3 in repaired_narration
    assert sentence_2_bad not in repaired_narration
    assert sentence_2_good in repaired_narration
    assert repaired_narration == f"{sentence_1} {sentence_2_good} {sentence_3}"


def test_surgical_repair_rejects_ambiguous_or_unlocated_target_span():
    """Verify that if target_span is not found or ambiguous, repair fails and claim remains unresolved."""
    full_narration = "First sentence here. Second sentence here. Third sentence here."

    section = ScriptSection(
        id="sec_01",
        type="hook",
        narration=full_narration,
        claim_ids=["clm_01"],
    )
    claim = ScriptClaim(
        claim_id="clm_01",
        section_id="sec_01",
        claim_text="Non-existent claim.",
        evidence_ids=[],
        risk_level="high",
    )

    repairer = ScriptRepairer(llm_provider=MockLLMProvider())

    # Target span that does not exist in narration
    repair_payload = SectionRepairPayload(
        section_id="sec_01",
        repairs=[
            ClaimRepairPayload(
                claim_id="clm_01",
                action="rephrase",
                target_span="This text definitely does not exist in narration.",
                replacement_span="Something new.",
                revised_claim_text="Something new.",
                evidence_ids=[],
                explanation="Testing failed span match.",
            )
        ],
    )

    assessments = {
        "clm_01": ClaimAssessment(
            claim_id="clm_01",
            section_id="sec_01",
            proposition="Non-existent claim.",
            status="unsupported",
            risk_level="high",
            explanation="Needs repair.",
            recommended_action="rephrase",
        )
    }

    result = repairer._apply_section_repairs(
        payload=repair_payload,
        section=section,
        target_claims=[claim],
        target_assessments=assessments,
        catalog=None,
        known_ids={"clm_01"},
    )

    # Claim must remain unresolved and narration must be unchanged
    assert len(result["action_records"]) == 1
    assert result["action_records"][0].is_resolved is False
    assert "could not be located unambiguously" in result["action_records"][0].explanation
    assert result["revised_narration"] == full_narration


# ===========================================================================
# 4. Service Lifecycle Integration: start_script & revise_script
# ===========================================================================

def test_start_script_orchestrates_fact_checking_and_clean_draft(lifecycle_env):
    """Verify start_script automatically runs fact-checking and stores report v001."""
    pid = lifecycle_env["pid"]
    service: ScriptService = lifecycle_env["service"]

    # When all claims are verified / supported, draft is ready at v001
    script = service.start_script(
        project_id=pid,
        topic="Physics of Cold",
        research_run_id="run_life_01",
    )

    assert script.version == 1
    assert script.project_id == pid

    # Fact-check report v001 must exist on disk and be accessible
    report_v1 = service.get_fact_check_report(pid, version=1)
    assert report_v1.script_version == 1
    assert report_v1.project_id == pid
    assert len(report_v1.assessments) > 0

    # Also check helper
    current_report = service.get_current_fact_check_report(pid)
    assert current_report is not None
    assert current_report.script_version == 1


def test_start_script_triggers_repair_and_creates_v002_when_needed(lifecycle_env):
    """Verify that if initial draft requires repair, ScriptRepairer creates v002 and updates current."""
    pid = lifecycle_env["pid"]
    service: ScriptService = lifecycle_env["service"]
    llm: MockLLMProvider = lifecycle_env["llm"]

    # Configure mock responses:
    # 1. Fact-check on v001 reports contradicted claim on clm_01
    bad_fc_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                status="contradicted",
                explanation="Contradicted by research evidence.",
                contradicting_evidence_ids=["evi_01"],
                recommended_action="rephrase",
            )
        ]
    )

    # 2. Section repair payload for repairing clm_01
    repair_payload = SectionRepairPayload(
        section_id="sec_01",
        repairs=[
            ClaimRepairPayload(
                claim_id="clm_01",
                action="rephrase",
                target_span="Did you know water freezes at 50 degrees Celsius under normal pressure?",
                replacement_span="Corrected narration: water freezes at 0 degrees Celsius under standard pressure.",
                revised_claim_text="Water freezes at 0 degrees Celsius under standard pressure.",
                evidence_ids=["evi_01"],
                explanation="Corrected freezing point.",
            )
        ],
        revised_narration="Corrected narration: water freezes at 0 degrees Celsius under standard pressure.",
    )

    # 3. Targeted recheck payload reports supported
    good_fc_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                status="supported",
                explanation="Verified against evi_01.",
                supporting_evidence_ids=["evi_01"],
                recommended_action="keep",
            )
        ]
    )

    # 0. Pre-create outline so LLM call queue begins directly at draft, fact-check, and repair
    outline = service.create_outline(
        project_id=pid,
        topic="Freezing Transitions",
        research_run_id="run_life_01",
    )

    sections_payload = []
    for idx, s in enumerate(outline.sections, 1):
        sec_id = f"sec_{idx:02d}"
        sections_payload.append(
            ScriptSectionPayload(
                id=sec_id,
                type=s.type,
                heading=s.heading,
                narration=(
                    "Did you know water freezes at 50 degrees Celsius under normal pressure?"
                    if sec_id == "sec_01"
                    else "Detailed narration describing the phenomena."
                ),
                notes="Section visual notes.",
            )
        )

    draft_payload = GeneratedScriptPayload(
        title="Freezing Transitions",
        target_duration_minutes=5.0,
        sections=sections_payload,
        claims=[
            ScriptClaimPayload(
                claim_text="Water freezes at 50 degrees Celsius.",
                section_id="sec_01",
                evidence_ids=["evi_01"],
            )
        ],
    )

    llm.queue_responses([draft_payload, bad_fc_payload, repair_payload, good_fc_payload])

    script = service.start_script(
        project_id=pid,
        topic="Freezing Transitions",
        research_run_id="run_life_01",
        outline=outline,
    )

    # Active script must have progressed to version 2 due to surgical repair
    assert script.version == 2

    # Both v001 and v002 snapshots and reports must exist
    versions = service.list_script_versions(pid)
    version_nums = [v.version for v in versions]
    assert 1 in version_nums
    assert 2 in version_nums

    report_v1 = service.get_fact_check_report(pid, version=1)
    report_v2 = service.get_fact_check_report(pid, version=2)

    assert report_v1.script_version == 1
    assert report_v1.requires_repair is True
    assert report_v2.script_version == 2
    assert report_v2.is_ready is True


def test_revise_script_preserves_high_risk_and_orchestrates_fact_checking(lifecycle_env):
    """Verify revise_script performs claim reconciliation, preserves high risk, and runs fact checking."""
    pid = lifecycle_env["pid"]
    service: ScriptService = lifecycle_env["service"]

    v1 = service.start_script(
        project_id=pid,
        topic="Mars Atmosphere Exploration",
        research_run_id="run_life_01",
    )

    # Ensure v1 has a high risk claim
    assert any(c.risk_level == "high" for c in v1.claims)

    v2 = service.revise_script(
        project_id=pid,
        instruction="Make the tone slightly more conversational while keeping all numbers exact.",
    )

    assert v2.version == 2
    # Ensure high-risk claim identity/risk was preserved and not silently downgraded
    assert any(c.risk_level == "high" for c in v2.claims)

    # Fact check report for v2 must be persisted
    report_v2 = service.get_fact_check_report(pid, version=2)
    assert report_v2.script_version == 2


def test_finalize_script_enforces_fact_check_readiness_gating(lifecycle_env):
    """Verify finalize_script blocks finalization when fact-check report has is_ready == False."""
    pid = lifecycle_env["pid"]
    service: ScriptService = lifecycle_env["service"]
    llm: MockLLMProvider = lifecycle_env["llm"]

    # Start script with clean draft
    script = service.start_script(
        project_id=pid,
        topic="Thermodynamics Overview",
        research_run_id="run_life_01",
    )

    # Artificially set current report to is_ready = False (simulating unresolved contradicted claim)
    dirs = service._get_project_dirs(pid)
    rep_file = dirs["fact_checks_dir"] / f"v{script.version:03d}.json"

    bad_report = ScriptFactCheckReport(
        project_id=pid,
        script_version=script.version,
        research_run_id="run_life_01",
        assessments=[
            ClaimAssessment(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="Water freezes at 100 degrees.",
                status="contradicted",
                risk_level="high",
                explanation="Completely contradicted.",
                recommended_action="remove",
            )
        ],
    )
    bad_report.recompute_summary()
    assert bad_report.is_ready is False

    # Overwrite report file for test
    rep_file.unlink()
    service.save_fact_check_report(pid, bad_report)

    # Accept draft
    service.accept_draft(pid)

    # Finalize must be rejected due to fact-check readiness failure
    with pytest.raises(ScriptFinalizationError) as exc_info:
        service.finalize_script(pid)

    assert "cannot be finalized because it is not ready for review" in str(exc_info.value)
    assert "contradicted claim(s)" in str(exc_info.value)


def test_unresearched_script_lifecycle_and_finalization(lifecycle_env):
    """Verify unresearched scripts mark claims not_checked and can be accepted/finalized when authorized."""
    pid = lifecycle_env["pid"]
    service: ScriptService = lifecycle_env["service"]

    # Start script in authorized unresearched mode
    script = service.start_script(
        project_id=pid,
        topic="Creative Storytelling Journey",
        allow_unresearched=True,
    )

    report = service.get_fact_check_report(pid, version=script.version)
    assert report.script_version == script.version
    assert all(a.status == "not_checked" for a in report.assessments)
    assert all(a.unassessed_reason == "unresearched_script" for a in report.assessments)

    # In authorized unresearched mode, claims do not block readiness
    assert report.is_ready is True

    # Accept and finalize
    service.accept_draft(pid)
    finalized = service.finalize_script(pid)
    assert finalized.status == "final"
    assert finalized.version == script.version


# ===========================================================================
# 5. Targeted Corrective Work Regressions: Ordering, Fail-Closed & Regressions
# ===========================================================================

def test_persistence_ordering_report_persisted_before_current_update(lifecycle_env):
    """Verify that fact-check report is written to disk strictly BEFORE current.json is updated."""
    pid = lifecycle_env["pid"]
    service: ScriptService = lifecycle_env["service"]
    dirs = service._get_project_dirs(pid)

    observed_order = []
    original_atomic_write = script_service_module._atomic_write_json

    def tracking_atomic_write(path: Path, data: Any):
        if path == dirs["current_file"]:
            version = data.get("version", 1)
            report_file = dirs["fact_checks_dir"] / f"v{version:03d}.json"
            observed_order.append({
                "action": "update_current",
                "report_exists": report_file.exists(),
            })
        elif "fact_checks" in str(path):
            observed_order.append({"action": "write_report"})
        original_atomic_write(path, data)

    with patch.object(script_service_module, "_atomic_write_json", side_effect=tracking_atomic_write):
        service.start_script(
            project_id=pid,
            topic="Thermodynamics Persistence",
            research_run_id="run_life_01",
        )

    # Assert that write_report happened, and when current was updated, the report already existed on disk
    report_writes = [o for o in observed_order if o.get("action") == "write_report"]
    current_updates = [o for o in observed_order if o.get("action") == "update_current"]

    assert len(report_writes) >= 1
    assert len(current_updates) >= 1
    assert all(update["report_exists"] is True for update in current_updates)


def test_persistence_failure_before_current_update_preserves_previous_current(lifecycle_env):
    """Verify that if report persistence fails, current.json is not updated and points to previous valid draft."""
    pid = lifecycle_env["pid"]
    service: ScriptService = lifecycle_env["service"]
    dirs = service._get_project_dirs(pid)

    # Establish initial v1
    v1_script = service.start_script(
        project_id=pid,
        topic="Initial Solid Draft",
        research_run_id="run_life_01",
    )
    assert v1_script.version == 1

    current_data_before = json.loads(dirs["current_file"].read_text(encoding="utf-8"))
    assert current_data_before["version"] == 1

    # Attempt revision, but inject disk failure during report persistence
    original_atomic_write = script_service_module._atomic_write_json

    def failing_atomic_write(path: Path, data: Any):
        if "fact_checks" in str(path) and "v002" in str(path):
            raise OSError("Simulated disk I/O failure during report snapshot persistence.")
        original_atomic_write(path, data)

    with patch.object(script_service_module, "_atomic_write_json", side_effect=failing_atomic_write):
        with pytest.raises(ScriptStorageError) as exc_info:
            service.revise_script(
                project_id=pid,
                instruction="Add details about atmospheric pressures.",
            )
        assert "Failed to persist fact-check report" in str(exc_info.value)

    # Current working copy must still safely point to v1!
    current_data_after = json.loads(dirs["current_file"].read_text(encoding="utf-8"))
    assert current_data_after["version"] == 1


def test_finalize_script_fails_closed_on_missing_report(lifecycle_env):
    """Verify finalize_script blocks finalization when mandatory fact-check report is missing."""
    pid = lifecycle_env["pid"]
    service: ScriptService = lifecycle_env["service"]
    dirs = service._get_project_dirs(pid)

    script = service.start_script(
        project_id=pid,
        topic="Missing Report Test",
        research_run_id="run_life_01",
    )
    service.accept_draft(pid)

    # Delete fact-check report
    report_file = dirs["fact_checks_dir"] / f"v{script.version:03d}.json"
    report_file.unlink()

    with pytest.raises(ScriptFinalizationError) as exc_info:
        service.finalize_script(pid)

    assert f"Draft v{script.version} cannot be finalized because its mandatory fact-check report is missing" in str(exc_info.value)


def test_finalize_script_fails_closed_on_mismatched_report(lifecycle_env):
    """Verify finalize_script blocks finalization when report metadata mismatches script version."""
    pid = lifecycle_env["pid"]
    service: ScriptService = lifecycle_env["service"]
    dirs = service._get_project_dirs(pid)

    script = service.start_script(
        project_id=pid,
        topic="Mismatched Report Test",
        research_run_id="run_life_01",
    )
    service.accept_draft(pid)

    report_file = dirs["fact_checks_dir"] / f"v{script.version:03d}.json"
    report = ScriptFactCheckReport.model_validate(json.loads(report_file.read_text(encoding="utf-8")))
    # Tamper with version
    tampered_data = report.model_dump(mode="json")
    tampered_data["script_version"] = 999
    report_file.write_text(json.dumps(tampered_data), encoding="utf-8")

    with pytest.raises(ScriptFinalizationError) as exc_info:
        service.finalize_script(pid)

    assert "mismatched metadata" in str(exc_info.value)


def test_finalize_script_fails_closed_on_malformed_report(lifecycle_env):
    """Verify finalize_script blocks finalization when report file is corrupt JSON."""
    pid = lifecycle_env["pid"]
    service: ScriptService = lifecycle_env["service"]
    dirs = service._get_project_dirs(pid)

    script = service.start_script(
        project_id=pid,
        topic="Malformed Report Test",
        research_run_id="run_life_01",
    )
    service.accept_draft(pid)

    report_file = dirs["fact_checks_dir"] / f"v{script.version:03d}.json"
    report_file.write_text("{corrupt: json syntax error", encoding="utf-8")

    with pytest.raises(ScriptFinalizationError) as exc_info:
        service.finalize_script(pid)

    assert "fact-check report is invalid or malformed" in str(exc_info.value)


def test_get_current_fact_check_report_fails_closed_on_corrupt_disk_file(lifecycle_env):
    """Verify get_current_fact_check_report raises ScriptStorageError on corrupt disk file instead of returning None."""
    pid = lifecycle_env["pid"]
    service: ScriptService = lifecycle_env["service"]
    dirs = service._get_project_dirs(pid)

    script = service.start_script(
        project_id=pid,
        topic="Corrupt File Retrieval",
        research_run_id="run_life_01",
    )

    report_file = dirs["fact_checks_dir"] / f"v{script.version:03d}.json"
    report_file.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(ScriptStorageError) as exc_info:
        service.get_current_fact_check_report(pid)

    assert "Corrupt or invalid fact-check report" in str(exc_info.value)


def test_unresearched_high_risk_claim_blocks_readiness_and_finalization(lifecycle_env):
    """Verify that an unresearched script with a high-risk claim blocks readiness and finalization."""
    pid = lifecycle_env["pid"]
    service: ScriptService = lifecycle_env["service"]

    # Generate draft in unresearched mode
    script = service.start_script(
        project_id=pid,
        topic="Story of Pressure",
        allow_unresearched=True,
    )

    # Artificially inject a high-risk claim into draft and report to test readiness policy
    dirs = service._get_project_dirs(pid)
    high_risk_assessment = ClaimAssessment(
        claim_id="clm_high_risk",
        section_id=script.sections[0].id,
        proposition="Over 95.8% of test subjects suffered critical failures.",
        status="not_checked",
        risk_level="high",
        unassessed_reason="unresearched_script",
        explanation="High-risk numerical proposition in unresearched script.",
        recommended_action="qualify",
    )

    report = service.get_fact_check_report(pid, script.version)
    report.assessments.append(high_risk_assessment)
    report.recompute_summary()

    # High-risk claim in unresearched mode MUST block readiness!
    assert report.is_ready is False
    assert "unresearched high-risk claim(s) require research grounding" in report.readiness_reason

    # Overwrite report on disk with this updated report
    dirs["fact_checks_dir"].joinpath(f"v{script.version:03d}.json").unlink()
    service.save_fact_check_report(pid, report)

    # Finalize must block
    service.accept_draft(pid)
    with pytest.raises(ScriptFinalizationError) as exc_info:
        service.finalize_script(pid)

    assert "cannot be finalized because it is not ready for review" in str(exc_info.value)
    assert "unresolved high-risk claim(s)" in str(exc_info.value)


def test_surgical_repair_rejects_missing_target_span_and_whole_section_replacement():
    """Verify that SectionRepairPayload with empty/missing target_span is rejected without fallback."""
    full_narration = "First sentence of the hook. Second sentence stating a falsehood. Third sentence concluding."
    section = ScriptSection(
        id="sec_01",
        type="hook",
        narration=full_narration,
        claim_ids=["clm_01"],
    )
    claim = ScriptClaim(
        claim_id="clm_01",
        section_id="sec_01",
        claim_text="Second sentence stating a falsehood.",
        evidence_ids=[],
        risk_level="high",
    )
    assessments = {
        "clm_01": ClaimAssessment(
            claim_id="clm_01",
            section_id="sec_01",
            proposition="Second sentence stating a falsehood.",
            status="contradicted",
            risk_level="high",
            explanation="Falsehood.",
            recommended_action="rephrase",
        )
    }

    repairer = ScriptRepairer(llm_provider=MockLLMProvider())

    # Payload with revised_narration provided but target_span missing/empty
    payload = SectionRepairPayload(
        section_id="sec_01",
        revised_narration="Completely rewritten whole section by the model without spans.",
        repairs=[
            ClaimRepairPayload(
                claim_id="clm_01",
                action="rephrase",
                target_span=None,  # Missing target_span!
                replacement_span="Something new.",
                revised_claim_text="Something new.",
                evidence_ids=[],
                explanation="Model tried whole section rewriting.",
            )
        ],
    )

    # Validation must reject payload
    is_valid, err = repairer._validate_section_repair_response(
        payload=payload,
        section=section,
        target_claims=[claim],
        catalog=None,
        supplied_eids_by_claim={"clm_01": set()},
    )
    assert is_valid is False
    assert "missing mandatory target_span" in err


def test_recheck_repaired_claims_mixed_multi_claim_outcomes(mock_catalog):
    """Test A: Mixed multi-claim recheck outcome in ScriptRepairer._recheck_repaired_claims.

    Set up 3 repaired claims:
    - clm_01 rechecked -> supported
    - clm_02 rechecked -> partially_supported
    - clm_03 rechecked -> contradicted
    Verify resolution counters, readiness False, and individual action record mapping.
    """
    section = ScriptSection(
        id="sec_01",
        type="hook",
        narration="First proposition. Second proposition. Third proposition.",
        claim_ids=["clm_01", "clm_02", "clm_03"],
    )
    c1 = ScriptClaim(claim_id="clm_01", section_id="sec_01", claim_text="First proposition.", evidence_ids=["evi_01"], risk_level="normal")
    c2 = ScriptClaim(claim_id="clm_02", section_id="sec_01", claim_text="Second proposition.", evidence_ids=["evi_02"], risk_level="high")
    c3 = ScriptClaim(claim_id="clm_03", section_id="sec_01", claim_text="Third proposition.", evidence_ids=["evi_contradiction"], risk_level="high")

    orig_script = Script(
        script_id="scr_01",
        project_id="proj_lifecycle",
        research_run_id=mock_catalog.run_id,
        version=1,
        title="Physics",
        sections=[section],
        claims=[c1, c2, c3],
    )
    orig_report = ScriptFactCheckReport(
        project_id="proj_lifecycle",
        research_run_id=mock_catalog.run_id,
        script_version=1,
        assessments=[
            ClaimAssessment(claim_id="clm_01", section_id="sec_01", proposition="First proposition.", status="unsupported", risk_level="normal", explanation="Unsupported initially."),
            ClaimAssessment(claim_id="clm_02", section_id="sec_01", proposition="Second proposition.", status="partially_supported", risk_level="high", explanation="Partially supported initially."),
            ClaimAssessment(claim_id="clm_03", section_id="sec_01", proposition="Third proposition.", status="contradicted", risk_level="high", explanation="Contradicted initially."),
        ],
    )

    # Recheck batch response with 1 supported, 1 partially_supported, 1 contradicted
    recheck_response = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(claim_id="clm_01", status="supported", explanation="Supported by evidence.", supporting_evidence_ids=["evi_01"]),
            ClaimAssessmentPayload(claim_id="clm_02", status="partially_supported", explanation="Partially supported.", considered_evidence_ids=["evi_02"]),
            ClaimAssessmentPayload(claim_id="clm_03", status="contradicted", explanation="Contradicted.", contradicting_evidence_ids=["evi_contradiction"]),
        ]
    )

    llm = MockLLMProvider()
    llm.set_response(recheck_response)
    repairer = ScriptRepairer(llm_provider=llm)

    repair_actions = [
        ClaimRepairActionRecord(
            original_claim_id="clm_01",
            repaired_claim_id="clm_01",
            section_id="sec_01",
            action="rephrase",
            original_proposition=c1.claim_text,
            repaired_proposition=c1.claim_text,
            original_status="unsupported",
            explanation="Rephrased 1",
        ),
        ClaimRepairActionRecord(
            original_claim_id="clm_02",
            repaired_claim_id="clm_02",
            section_id="sec_01",
            action="qualify",
            original_proposition=c2.claim_text,
            repaired_proposition=c2.claim_text,
            original_status="partially_supported",
            explanation="Qualified 2",
        ),
        ClaimRepairActionRecord(
            original_claim_id="clm_03",
            repaired_claim_id="clm_03",
            section_id="sec_01",
            action="rephrase",
            original_proposition=c3.claim_text,
            repaired_proposition=c3.claim_text,
            original_status="contradicted",
            explanation="Rephrased 3",
        ),
    ]

    final_report = repairer._recheck_repaired_claims(
        original_script=orig_script,
        repaired_script=orig_script,
        original_report=orig_report,
        changed_claim_ids={"clm_01", "clm_02", "clm_03"},
        repair_action_records=repair_actions,
        catalog=mock_catalog,
    )

    # Verify action records reflect individual outcomes
    assert repair_actions[0].repaired_claim_id == "clm_01"
    assert repair_actions[0].new_status == "supported"
    assert repair_actions[0].is_resolved is True

    assert repair_actions[1].repaired_claim_id == "clm_02"
    assert repair_actions[1].new_status == "partially_supported"
    assert repair_actions[1].is_resolved is False

    assert repair_actions[2].repaired_claim_id == "clm_03"
    assert repair_actions[2].new_status == "contradicted"
    assert repair_actions[2].is_resolved is False

    # Resolution counters
    resolved_count = sum(1 for r in repair_actions if r.is_resolved)
    unresolved_count = len(repair_actions) - resolved_count
    assert resolved_count == 1
    assert unresolved_count == 2

    # Final report readiness must be False
    assert final_report.is_ready is False
    assert final_report.has_unresolved_high_risk is True
    assert final_report.contradicted_count == 1


def test_surgical_repair_removal_alongside_retention_in_one_section():
    """Test B: Removal alongside retention in one section.

    - A section contains two claims: one to qualify/rephrase, one to remove.
    - Verify that the removed claim's text is excised.
    - Verify that the retained claim's text is repaired.
    - Verify that unrelated section text is preserved verbatim.
    - Verify claim identity and lack of duplicate whitespace/punctuation.
    """
    sentence_pre = "In the deep vacuum of interstellar space, temperatures drop to near absolute zero."
    sentence_keep = "Supercooled liquid water can stay liquid down to minus 40 degrees Celsius under special conditions."
    sentence_remove = "Meanwhile, boiling liquid water lakes exist everywhere across the surface of Mars."
    sentence_post = "These extraordinary conditions test the absolute frontiers of modern thermodynamics."

    full_narration = f"{sentence_pre} {sentence_keep} {sentence_remove} {sentence_post}"

    section = ScriptSection(
        id="sec_01",
        type="middle",
        narration=full_narration,
        claim_ids=["clm_retain", "clm_remove"],
    )

    c_retain = ScriptClaim(
        claim_id="clm_retain",
        section_id="sec_01",
        claim_text="Supercooled water stays liquid to minus 40 degrees Celsius.",
        evidence_ids=["evi_03"],
        risk_level="normal",
    )
    c_remove = ScriptClaim(
        claim_id="clm_remove",
        section_id="sec_01",
        claim_text="Boiling liquid water lakes exist on Mars.",
        evidence_ids=[],
        risk_level="high",
    )

    repaired_keep_text = "Supercooled liquid water can stay liquid down to -40 degrees Celsius in the complete absence of nucleation seeds."

    payload = SectionRepairPayload(
        section_id="sec_01",
        repairs=[
            ClaimRepairPayload(
                claim_id="clm_retain",
                action="qualify",
                target_span=sentence_keep,
                replacement_span=repaired_keep_text,
                revised_claim_text="Supercooled water can remain liquid down to -40C without nucleation seeds.",
                evidence_ids=["evi_03"],
                explanation="Added nucleation seed qualifier from evi_03.",
            ),
            ClaimRepairPayload(
                claim_id="clm_remove",
                action="remove",
                target_span=sentence_remove,
                replacement_span="",
                revised_claim_text=None,
                evidence_ids=[],
                explanation="Removed false Martian boiling lakes assertion.",
            ),
        ],
    )

    assessments = {
        "clm_retain": ClaimAssessment(
            claim_id="clm_retain",
            section_id="sec_01",
            proposition=c_retain.claim_text,
            status="partially_supported",
            risk_level="normal",
            explanation="Needs nucleation qualifier.",
        ),
        "clm_remove": ClaimAssessment(
            claim_id="clm_remove",
            section_id="sec_01",
            proposition=c_remove.claim_text,
            status="contradicted",
            risk_level="high",
            explanation="Contradicted.",
        ),
    }

    repairer = ScriptRepairer(llm_provider=MockLLMProvider())
    result = repairer._apply_section_repairs(
        payload=payload,
        section=section,
        target_claims=[c_retain, c_remove],
        target_assessments=assessments,
        catalog=None,
        known_ids={"clm_retain", "clm_remove"},
    )

    repaired_narration = result["revised_narration"]

    # 1. Removed claim text is completely excised
    assert sentence_remove not in repaired_narration

    # 2. Retained claim text is substituted
    assert repaired_keep_text in repaired_narration
    assert sentence_keep not in repaired_narration

    # 3. Unrelated surrounding sentences are preserved verbatim
    assert sentence_pre in repaired_narration
    assert sentence_post in repaired_narration

    # 4. Spacing artifacts checked: no double spaces or orphan punctuation
    assert "  " not in repaired_narration
    assert ".." not in repaired_narration
    assert " ." not in repaired_narration

    # 5. Claim identities: retained claim kept, removed claim omitted from repaired_claims
    rep_claims = result["repaired_claims"]
    assert len(rep_claims) == 1
    assert rep_claims[0].claim_id in ("clm_retain", "clm_01")
    assert not any(c.claim_id == "clm_remove" for c in rep_claims)


# ===========================================================================
# 6. Research-Run Provenance Integrity Regressions
# ===========================================================================

def test_provenance_matching_run_ids_accepted_during_commit_and_finalization(lifecycle_env):
    """1. Matching run IDs are accepted during commit and finalization."""
    pid = lifecycle_env["pid"]
    service: ScriptService = lifecycle_env["service"]

    script = service.start_script(
        project_id=pid,
        topic="Matching Run Provenance",
        research_run_id="run_life_01",
    )
    assert script.research_run_id == "run_life_01"

    report = service.get_fact_check_report(pid, script.version)
    assert report.research_run_id == "run_life_01"

    service.accept_draft(pid)
    finalized = service.finalize_script(pid)
    assert finalized.version == script.version
    assert finalized.research_run_id == "run_life_01"


def test_provenance_foreign_report_run_id_rejected_during_commit(lifecycle_env):
    """2. Foreign report run ID is rejected during commit before current changes."""
    pid = lifecycle_env["pid"]
    service: ScriptService = lifecycle_env["service"]
    dirs = service._get_project_dirs(pid)

    # Establish initial v1
    v1 = service.start_script(
        project_id=pid,
        topic="Original Grounded Script",
        research_run_id="run_life_01",
    )
    assert v1.version == 1

    current_data_before = json.loads(dirs["current_file"].read_text(encoding="utf-8"))
    assert current_data_before["version"] == 1

    # Attempt to commit candidate v2 with a foreign research run ID report
    candidate_v2 = v1.model_copy(deep=True)
    candidate_v2.version = 2
    candidate_v2.research_run_id = "run_life_01"

    foreign_report = service.get_fact_check_report(pid, version=1).model_copy(deep=True)
    foreign_report.script_version = 2
    foreign_report.research_run_id = "run_foreign_999"

    with pytest.raises(ScriptValidationError) as exc_info:
        service._commit_script_version(
            project_id=pid,
            dirs=dirs,
            script=candidate_v2,
            report=foreign_report,
            event_type="draft_generated",
            event_content="Candidate v2",
        )

    assert "research provenance mismatch" in str(exc_info.value)
    assert "run_foreign_999" in str(exc_info.value)

    # Active current version must NOT have changed
    current_data_after = json.loads(dirs["current_file"].read_text(encoding="utf-8"))
    assert current_data_after["version"] == 1


def test_provenance_missing_report_run_id_rejected_during_commit_for_researched_script(lifecycle_env):
    """3. Missing report run ID is rejected for a researched script during commit."""
    pid = lifecycle_env["pid"]
    service: ScriptService = lifecycle_env["service"]
    dirs = service._get_project_dirs(pid)

    v1 = service.start_script(
        project_id=pid,
        topic="Grounded Script",
        research_run_id="run_life_01",
    )

    candidate_v2 = v1.model_copy(deep=True)
    candidate_v2.version = 2
    candidate_v2.research_run_id = "run_life_01"

    unlinked_report = service.get_fact_check_report(pid, version=1).model_copy(deep=True)
    unlinked_report.script_version = 2
    unlinked_report.research_run_id = None  # Missing run ID!

    with pytest.raises(ScriptValidationError) as exc_info:
        service._commit_script_version(
            project_id=pid,
            dirs=dirs,
            script=candidate_v2,
            report=unlinked_report,
            event_type="draft_generated",
            event_content="Candidate v2",
        )

    assert "research provenance mismatch" in str(exc_info.value)

    # Active current pointer unchanged
    current_data = json.loads(dirs["current_file"].read_text(encoding="utf-8"))
    assert current_data["version"] == 1


def test_provenance_foreign_report_run_id_rejected_during_finalization(lifecycle_env):
    """4. Foreign report run ID is rejected during finalization, including tampered report."""
    pid = lifecycle_env["pid"]
    service: ScriptService = lifecycle_env["service"]
    dirs = service._get_project_dirs(pid)

    script = service.start_script(
        project_id=pid,
        topic="Provenance Tampering Finalize",
        research_run_id="run_life_01",
    )
    service.accept_draft(pid)

    # Tamper with persisted report on disk
    report_file = dirs["fact_checks_dir"] / f"v{script.version:03d}.json"
    rep_data = json.loads(report_file.read_text(encoding="utf-8"))
    rep_data["research_run_id"] = "run_tampered_alien"
    report_file.write_text(json.dumps(rep_data), encoding="utf-8")

    with pytest.raises(ScriptFinalizationError) as exc_info:
        service.finalize_script(pid)

    assert "mismatched research provenance" in str(exc_info.value)
    assert "run_tampered_alien" in str(exc_info.value)


def test_provenance_missing_report_run_id_rejected_during_finalization_for_researched_script(lifecycle_env):
    """5. Missing report run ID is rejected during finalization for a researched script."""
    pid = lifecycle_env["pid"]
    service: ScriptService = lifecycle_env["service"]
    dirs = service._get_project_dirs(pid)

    script = service.start_script(
        project_id=pid,
        topic="Missing Run ID Finalize",
        research_run_id="run_life_01",
    )
    service.accept_draft(pid)

    # Tamper report to set research_run_id = None
    report_file = dirs["fact_checks_dir"] / f"v{script.version:03d}.json"
    rep_data = json.loads(report_file.read_text(encoding="utf-8"))
    rep_data["research_run_id"] = None
    report_file.write_text(json.dumps(rep_data), encoding="utf-8")

    with pytest.raises(ScriptFinalizationError) as exc_info:
        service.finalize_script(pid)

    assert "mismatched research provenance" in str(exc_info.value)


def test_provenance_none_run_ids_accepted_for_authorized_unresearched_scripts(lifecycle_env):
    """6. Both IDs being None works for authorized unresearched scripts."""
    pid = lifecycle_env["pid"]
    service: ScriptService = lifecycle_env["service"]

    script = service.start_script(
        project_id=pid,
        topic="Unresearched Creative Journey",
        allow_unresearched=True,
    )
    assert script.research_run_id is None

    report = service.get_fact_check_report(pid, script.version)
    assert report.research_run_id is None

    # Finalization must accept when both are None
    service.accept_draft(pid)
    finalized = service.finalize_script(pid)
    assert finalized.status == "final"
    assert finalized.version == script.version
    assert finalized.research_run_id is None


def test_provenance_legacy_script_compatibility_preserved(lifecycle_env):
    """7. Existing legacy compatibility remains intact for scripts without claims, reports, or run ID."""
    pid = lifecycle_env["pid"]
    service: ScriptService = lifecycle_env["service"]
    dirs = service._get_project_dirs(pid)

    # Manually seed a legacy pre-Phase 5 script (0 claims, no reports, research_run_id is None)
    legacy_script = Script(
        script_id="scr_legacy_01",
        project_id=pid,
        version=1,
        status="draft",
        title="Legacy Script Pre-Phase 5",
        topic="Pre-Phase 5 Legacy",
        target_duration_minutes=3.0,
        sections=[
            ScriptSection(
                id="sec_01",
                type="hook",
                heading="Legacy Hook",
                narration="Legacy narration without any claim annotations.",
                claim_ids=[],
            )
        ],
        claims=[],
        research_run_id=None,
    )
    script_service_module._atomic_write_json(dirs["current_file"], legacy_script.model_dump(mode="json"))
    script_service_module._atomic_write_json(dirs["versions_dir"] / "v001.json", legacy_script.model_dump(mode="json"))

    service.accept_draft(pid)
    finalized = service.finalize_script(pid)
    assert finalized.status == "final"
    assert finalized.version == 1
    assert len(finalized.claims) == 0
    assert finalized.research_run_id is None

    # But if a script has a research_run_id without a report, legacy exception must NOT apply
    researched_without_report = legacy_script.model_copy(deep=True)
    researched_without_report.research_run_id = "run_life_01"
    script_service_module._atomic_write_json(dirs["current_file"], researched_without_report.model_dump(mode="json"))
    with pytest.raises(ScriptFinalizationError) as exc_info:
        service.finalize_script(pid)
    assert "mandatory fact-check report is missing" in str(exc_info.value)

