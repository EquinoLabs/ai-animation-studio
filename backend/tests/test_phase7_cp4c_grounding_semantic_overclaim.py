"""Phase 7 CP4C Grounding Remediation: Semantic Overclaim & Compound Claim Audit Tests.

Adversarial offline regression tests verifying:
1. Habits become automatic, but evidence does not establish energy savings.
2. Evidence establishes energy savings, but not improved decision-making.
3. Evidence establishes neural activity changes, but not the specific mechanism asserted.
4. A researcher's experiment is cited, but the claim attributes a broader conclusion not established by that experiment.
5. A compound claim with one supported and one unsupported component.
6. A high-risk compound claim with an unsupported component blocks readiness.
7. A normal-risk claim follows existing readiness policy.
8. A faithful paraphrase remains supported.
9. A conceptual-consistency explanation cannot produce supported without evidence.
10. Corrective retry cannot bypass the deterministic grounding audit.
11. Claim isolation and permitted evidence IDs remain intact.
12. Existing Phase 5 fact-checking and repair behavior remains compatible.
Includes actual failure fixtures from p7_live_validation_006 (clm_11 and clm_12).
"""

from typing import Dict, List, Optional
import pytest

from backend.app.domain.fact_check import (
    BatchFactCheckResponsePayload,
    ClaimAssessmentPayload,
    ScriptFactCheckReport,
)
from backend.app.domain.research import EvidenceRecord, SourceRecord
from backend.app.domain.script import Script, ScriptClaim, ScriptSection
from backend.app.llm.base import LLMResponseMalformedError
from backend.app.llm.mock import MockLLMProvider
from backend.app.services.scriptwriter.fact_checker import (
    ScriptFactChecker,
    _audit_supported_grounding,
    split_proposition_clauses,
)
from backend.app.services.scriptwriter.outliner import ValidatedEvidenceCatalog
from backend.app.services.scriptwriter.repairer import ScriptRepairer


# ---------------------------------------------------------------------------
# Test Fixtures & Catalogs
# ---------------------------------------------------------------------------

def _create_catalog(evidence_dict: Dict[str, str], run_id: str = "run_test") -> ValidatedEvidenceCatalog:
    sources = {
        f"src_{eid}": SourceRecord(
            source_id=f"src_{eid}",
            project_id="proj_test",
            title=f"Source for {eid}",
            url=f"https://example.com/{eid}",
        )
        for eid in evidence_dict
    }
    evidence = {
        eid: EvidenceRecord(
            evidence_id=eid,
            project_id="proj_test",
            run_id=run_id,
            source_id=f"src_{eid}",
            excerpt=excerpt,
        )
        for eid, excerpt in evidence_dict.items()
    }
    return ValidatedEvidenceCatalog(
        run_id=run_id,
        evidence_by_id=evidence,
        sources_by_id=sources,
    )


EVI_HABIT_LOOP = (
    "At the foundation of every habit is a neurological pattern called the habit loop, which consists of three key components: "
    "the cue, the routine, and the reward. The cue is the trigger that signals the brain to initiate a behavior. Cues can be "
    "either external, like the time of day, or internal, such as emotions like boredom or stress. Once the cue is identified, "
    "the behavior is carried out in response. This routine can be physical, like grabbing a snack, or mental, like procrastination. "
    "Finally, the habit loop is completed by the reward, which provides a sense of satisfaction or relief that reinforces the behavior. "
    "Over time, as the brain associates the cue with the routine and reward, this loop becomes ingrained in our neural pathways, "
    "creating a habit that occurs subconsciously."
)

EVI_ENERGY_SAVINGS = (
    "Automatic habits conserve valuable mental energy and cognitive resources during daily routines, allowing individuals to navigate familiar tasks efficiently."
)

EVI_GRAYBIEL_MAZE = (
    "Ann Graybiel's lab at MIT demonstrated this beautifully in a series of landmark studies. They implanted electrodes in rats' basal ganglia "
    "and watched what happened as the animals learned to navigate a T-shaped maze. At first, neural activity in the basal ganglia was high "
    "throughout the maze run. But as the rats learned the route, the firing pattern changed dramatically. Neural activity spiked at the beginning "
    "of the maze, went quiet during the run, and spiked again at the end."
)


# ---------------------------------------------------------------------------
# Scenario 1: Habits become automatic, but evidence does not establish energy savings
# ---------------------------------------------------------------------------

def test_scenario_1_automatic_habits_without_energy_savings():
    """Habits become automatic, but evidence does not establish energy savings. Weakest component forces partially_supported."""
    catalog = _create_catalog({"evi_01": EVI_HABIT_LOOP})
    claim = ScriptClaim(
        claim_id="clm_01",
        claim_text="Habits form automatic routines that run subconsciously, saving significant mental energy.",
        section_id="sec_01",
        risk_level="normal",
        evidence_ids=["evi_01"],
    )

    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                status="supported",
                explanation="The excerpt establishes habit loop automation.",
                supporting_evidence_ids=["evi_01"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )

    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_01", project_id="proj_test", version=1, title="Test",
        sections=[ScriptSection(id="sec_01", type="middle", heading="Middle", narration="Text.", claim_ids=["clm_01"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)

    assessment = report.assessments[0]
    assert assessment.status == "partially_supported"
    assert "unverified material component" in assessment.explanation
    assert assessment.recommended_action == "qualify"


# ---------------------------------------------------------------------------
# Scenario 2: Evidence establishes energy savings, but not improved decision-making
# ---------------------------------------------------------------------------

def test_scenario_2_energy_savings_without_decision_making():
    """Evidence establishes energy savings, but not improved decision-making. Weakest component forces partially_supported."""
    catalog = _create_catalog({"evi_02": EVI_ENERGY_SAVINGS})
    claim = ScriptClaim(
        claim_id="clm_02",
        claim_text="Habitual routines conserve mental energy and improve decision-making under stress.",
        section_id="sec_01",
        risk_level="normal",
        evidence_ids=["evi_02"],
    )

    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_02",
                status="supported",
                explanation="The excerpt confirms mental energy savings.",
                supporting_evidence_ids=["evi_02"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )

    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_01", project_id="proj_test", version=1, title="Test",
        sections=[ScriptSection(id="sec_01", type="middle", heading="Middle", narration="Text.", claim_ids=["clm_02"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)

    assessment = report.assessments[0]
    assert assessment.status == "partially_supported"
    assert "improve decision-making under stress" in assessment.explanation


# ---------------------------------------------------------------------------
# Scenario 3: Neural activity changes without asserted mechanism
# ---------------------------------------------------------------------------

def test_scenario_3_neural_activity_without_asserted_synaptic_pruning():
    """Evidence establishes neural activity changes, but not that synapses permanently prune dendritic spines."""
    catalog = _create_catalog({"evi_03": EVI_GRAYBIEL_MAZE})
    claim = ScriptClaim(
        claim_id="clm_03",
        claim_text="Neural activity changes in the basal ganglia occur because dendritic spines permanently prune synaptic connections.",
        section_id="sec_01",
        risk_level="high",
        evidence_ids=["evi_03"],
    )

    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_03",
                status="supported",
                explanation="The MIT maze study shows neural activity in the basal ganglia.",
                supporting_evidence_ids=["evi_03"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )

    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_01", project_id="proj_test", version=1, title="Test",
        sections=[ScriptSection(id="sec_01", type="middle", heading="Middle", narration="Text.", claim_ids=["clm_03"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)

    assessment = report.assessments[0]
    assert assessment.status in ("partially_supported", "unsupported")
    assert report.has_unresolved_high_risk is True
    assert report.is_ready is False


# ---------------------------------------------------------------------------
# Scenario 4: Researcher experiment cited with broader unevidenced conclusion
# ---------------------------------------------------------------------------

def test_scenario_4_researcher_experiment_with_broader_unsupported_conclusion():
    """Ann Graybiel's maze study cited, but claim attributes willpower depletion after twenty decisions."""
    catalog = _create_catalog({"evi_04": EVI_GRAYBIEL_MAZE})
    claim = ScriptClaim(
        claim_id="clm_04",
        claim_text="Ann Graybiel's MIT experiment proved that human willpower is biologically depleted after twenty decisions.",
        section_id="sec_01",
        risk_level="high",
        evidence_ids=["evi_04"],
    )

    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_04",
                status="supported",
                explanation="Graybiel's MIT study proves biological limits on willpower.",
                supporting_evidence_ids=["evi_04"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )

    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_01", project_id="proj_test", version=1, title="Test",
        sections=[ScriptSection(id="sec_01", type="middle", heading="Middle", narration="Text.", claim_ids=["clm_04"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)

    assessment = report.assessments[0]
    assert assessment.status in ("partially_supported", "unsupported")
    assert report.has_unresolved_high_risk is True
    assert report.is_ready is False


# ---------------------------------------------------------------------------
# Scenario 5: Compound claim with one supported and one unsupported component
# ---------------------------------------------------------------------------

def test_scenario_5_compound_claim_supported_and_unsupported_components():
    """Compound claim: cue-reward loop supported, weight loss guarantee unsupported -> partially_supported."""
    catalog = _create_catalog({"evi_05": EVI_HABIT_LOOP})
    claim = ScriptClaim(
        claim_id="clm_05",
        claim_text="The habit loop consists of cues and rewards, and guarantees lasting weight loss within two weeks.",
        section_id="sec_01",
        risk_level="normal",
        evidence_ids=["evi_05"],
    )

    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_05",
                status="supported",
                explanation="Habit loop consists of cue and reward.",
                supporting_evidence_ids=["evi_05"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )

    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_01", project_id="proj_test", version=1, title="Test",
        sections=[ScriptSection(id="sec_01", type="middle", heading="Middle", narration="Text.", claim_ids=["clm_05"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)

    assessment = report.assessments[0]
    assert assessment.status == "partially_supported"
    assert "guarantees lasting weight loss" in assessment.explanation


# ---------------------------------------------------------------------------
# Scenario 6: High-risk compound claim with unsupported component blocks readiness
# ---------------------------------------------------------------------------

def test_scenario_6_high_risk_compound_claim_blocks_readiness():
    """A high-risk claim downgraded to partially_supported must block readiness (is_ready=False)."""
    catalog = _create_catalog({"evi_06": EVI_HABIT_LOOP})
    claim = ScriptClaim(
        claim_id="clm_06",
        claim_text="Habits shape daily behavior by automating complex tasks, saving mental energy but limiting flexibility.",
        section_id="sec_01",
        risk_level="high",
        evidence_ids=["evi_06"],
    )

    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_06",
                status="supported",
                explanation="Directly supports by explaining habits involve an automatic routine.",
                supporting_evidence_ids=["evi_06"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )

    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_01", project_id="proj_test", version=1, title="Test",
        sections=[ScriptSection(id="sec_01", type="middle", heading="Middle", narration="Text.", claim_ids=["clm_06"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)

    assert report.has_unresolved_high_risk is True
    assert report.is_ready is False
    assert "high-risk claim(s) partially supported" in (report.readiness_reason or "")


# ---------------------------------------------------------------------------
# Scenario 7: Normal-risk claim follows existing readiness policy
# ---------------------------------------------------------------------------

def test_scenario_7_normal_risk_claim_follows_existing_readiness_policy():
    """A normal-risk claim downgraded to partially_supported triggers repair but does NOT block normal-risk readiness."""
    catalog = _create_catalog({"evi_07": EVI_HABIT_LOOP})
    claim = ScriptClaim(
        claim_id="clm_07",
        claim_text="Habits form automatic routines that run subconsciously, saving significant mental energy.",
        section_id="sec_01",
        risk_level="normal",
        evidence_ids=["evi_07"],
    )

    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_07",
                status="supported",
                explanation="Directly supported by habit loop excerpt.",
                supporting_evidence_ids=["evi_07"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )

    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_01", project_id="proj_test", version=1, title="Test",
        sections=[ScriptSection(id="sec_01", type="middle", heading="Middle", narration="Text.", claim_ids=["clm_07"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)

    assert report.assessments[0].status == "partially_supported"
    assert report.has_unresolved_high_risk is False
    assert report.requires_repair is True
    # In Phase 5 contract, normal-risk partially supported claims are ready with flagged warnings
    assert report.is_ready is True
    assert "Ready with flagged items" in (report.readiness_reason or "")


# ---------------------------------------------------------------------------
# Scenario 8: Faithful paraphrase remains supported
# ---------------------------------------------------------------------------

def test_scenario_8_faithful_paraphrase_remains_supported():
    """A faithful paraphrase using synonymous concepts remains supported."""
    catalog = _create_catalog({"evi_08": EVI_HABIT_LOOP})
    claim = ScriptClaim(
        claim_id="clm_08",
        claim_text="The routine is the action performed in response to the cue, and it leads to a reward.",
        section_id="sec_01",
        risk_level="normal",
        evidence_ids=["evi_08"],
    )

    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_08",
                status="supported",
                explanation="The excerpt explicitly defines routine as behavior in response to cue and completed by reward.",
                supporting_evidence_ids=["evi_08"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )

    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_01", project_id="proj_test", version=1, title="Test",
        sections=[ScriptSection(id="sec_01", type="middle", heading="Middle", narration="Text.", claim_ids=["clm_08"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)

    assert report.assessments[0].status == "supported"
    assert report.assessments[0].supporting_evidence_ids == ["evi_08"]
    assert report.is_ready is True


# ---------------------------------------------------------------------------
# Scenario 9: Conceptual-consistency explanation cannot produce supported
# ---------------------------------------------------------------------------

def test_scenario_9_conceptual_consistency_explanation_cannot_produce_supported():
    """An explanation admitting non-mention while asserting conceptual consistency is downgraded."""
    catalog = _create_catalog({"evi_09": EVI_HABIT_LOOP})
    claim = ScriptClaim(
        claim_id="clm_09",
        claim_text="Dopamine reinforces habit loops by creating pleasure associations with rewards.",
        section_id="sec_01",
        risk_level="normal",
        evidence_ids=["evi_09"],
    )

    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_09",
                status="supported",
                explanation="While the excerpt does not mention dopamine, rewards are consistent with dopamine signaling.",
                supporting_evidence_ids=["evi_09"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )

    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_01", project_id="proj_test", version=1, title="Test",
        sections=[ScriptSection(id="sec_01", type="middle", heading="Middle", narration="Text.", claim_ids=["clm_09"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)

    assert report.assessments[0].status == "unsupported"
    assert "does not state the proposition" in report.assessments[0].explanation


# ---------------------------------------------------------------------------
# Scenario 10: Corrective retry cannot bypass deterministic grounding audit
# ---------------------------------------------------------------------------

def test_scenario_10_corrective_retry_cannot_bypass_grounding_audit():
    """Even if an LLM returns supported on both initial invocation and corrective retry, the audit deterministically overrides it."""
    catalog = _create_catalog({"evi_10": EVI_HABIT_LOOP})
    claim = ScriptClaim(
        claim_id="clm_10",
        claim_text="Habits shape daily behavior and decision-making by automating complex tasks, saving mental energy but potentially limiting flexibility.",
        section_id="sec_01",
        risk_level="high",
        evidence_ids=["evi_10"],
    )

    calls = []
    def stubborn_llm(prompt: str, schema):
        calls.append(prompt)
        # LLM stubbornly insists claim is supported on both attempts
        return BatchFactCheckResponsePayload(
            assessments=[
                ClaimAssessmentPayload(
                    claim_id="clm_10",
                    status="supported",
                    explanation="Directly supports by explaining habits involve an automatic routine which saves mental energy.",
                    supporting_evidence_ids=["evi_10"],
                    contradicting_evidence_ids=[],
                    contextual_evidence_ids=[],
                    recommended_action="keep",
                )
            ]
        )

    mock_llm = MockLLMProvider()
    mock_llm.generate_json = stubborn_llm

    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_01", project_id="proj_test", version=1, title="Test",
        sections=[ScriptSection(id="sec_01", type="middle", heading="Middle", narration="Text.", claim_ids=["clm_10"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)

    # Initial call + 1 corrective retry occurred
    assert len(calls) == 2
    assert "CORRECTION REQUIRED" in calls[1]
    # Despite LLM returning supported on retry, status is deterministically adjusted to partially_supported
    assert report.assessments[0].status == "partially_supported"
    assert report.has_unresolved_high_risk is True
    assert report.is_ready is False


# ---------------------------------------------------------------------------
# Scenario 11: Claim isolation and permitted evidence IDs remain intact
# ---------------------------------------------------------------------------

def test_scenario_11_claim_isolation_and_permitted_evidence_intact():
    """Verify that multiple claims are checked with exactly 1 claim per request and permitted evidence isolated."""
    catalog = _create_catalog({
        "evi_a": EVI_HABIT_LOOP,
        "evi_b": EVI_GRAYBIEL_MAZE,
    })
    claim_1 = ScriptClaim(
        claim_id="clm_01",
        claim_text="Habits form through a neurological loop of cue, routine, and reward.",
        section_id="sec_01",
        evidence_ids=["evi_a"],
    )
    claim_2 = ScriptClaim(
        claim_id="clm_02",
        claim_text="Graybiel's rats demonstrated neural spikes at start and finish in a T-maze.",
        section_id="sec_02",
        evidence_ids=["evi_b"],
    )

    prompts_seen = []
    def isolated_llm(prompt: str, schema):
        prompts_seen.append(prompt)
        if "clm_01" in prompt:
            # evi_b must NOT appear in clm_01's prompt
            assert "evi_b" not in prompt
            assert "evi_a" in prompt
            return BatchFactCheckResponsePayload(
                assessments=[
                    ClaimAssessmentPayload(
                        claim_id="clm_01", status="supported", explanation="Verified 1.",
                        supporting_evidence_ids=["evi_a"], contradicting_evidence_ids=[],
                        contextual_evidence_ids=[], recommended_action="keep",
                    )
                ]
            )
        elif "clm_02" in prompt:
            # evi_a must NOT appear in clm_02's prompt
            assert "evi_a" not in prompt
            assert "evi_b" in prompt
            return BatchFactCheckResponsePayload(
                assessments=[
                    ClaimAssessmentPayload(
                        claim_id="clm_02", status="supported", explanation="Verified 2.",
                        supporting_evidence_ids=["evi_b"], contradicting_evidence_ids=[],
                        contextual_evidence_ids=[], recommended_action="keep",
                    )
                ]
            )
        raise ValueError("Unexpected prompt")

    mock_llm = MockLLMProvider()
    mock_llm.generate_json = isolated_llm

    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_01", project_id="proj_test", version=1, title="Test",
        sections=[
            ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Text 1.", claim_ids=["clm_01"]),
            ScriptSection(id="sec_02", type="middle", heading="Middle", narration="Text 2.", claim_ids=["clm_02"]),
        ],
        claims=[claim_1, claim_2], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)

    assert len(prompts_seen) == 2
    assert report.supported_count == 2
    assert report.is_ready is True


# ---------------------------------------------------------------------------
# Scenario 12: Existing Phase 5 fact-checking and repair behavior remains compatible
# ---------------------------------------------------------------------------

def test_scenario_12_phase5_repair_compatibility():
    """Verify that a partially supported claim triggers repair in ScriptRepairer and successfully repairs."""
    catalog = _create_catalog({"evi_01": EVI_HABIT_LOOP})
    claim = ScriptClaim(
        claim_id="clm_01",
        claim_text="Habits form automatic routines that run subconsciously, saving significant mental energy.",
        section_id="sec_01",
        risk_level="high",
        evidence_ids=["evi_01"],
    )

    def dynamic_llm(prompt: str, schema):
        if "clm_02" in prompt:
            cid = "clm_02"
            status = "supported"
            expl = "Directly established by habit loop excerpt."
        else:
            cid = "clm_01"
            status = "partially_supported"
            expl = "Habits form automatic routines, but mental energy is unverified."
        return BatchFactCheckResponsePayload(
            assessments=[
                ClaimAssessmentPayload(
                    claim_id=cid,
                    status=status,
                    explanation=expl,
                    supporting_evidence_ids=["evi_01"],
                    contradicting_evidence_ids=[],
                    contextual_evidence_ids=[],
                    recommended_action="keep" if status == "supported" else "qualify",
                )
            ]
        )

    mock_llm = MockLLMProvider()
    mock_llm.generate_json = dynamic_llm

    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_01", project_id="proj_test", version=1, title="Test",
        sections=[ScriptSection(id="sec_01", type="middle", heading="Middle", narration="Habits form automatic routines that run subconsciously, saving significant mental energy.", claim_ids=["clm_01"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)
    assert report.requires_repair is True
    assert report.has_unresolved_high_risk is True

    # Now verify repairer surgical repair
    repairer_llm = MockLLMProvider()
    from backend.app.services.scriptwriter.repairer import ClaimRepairPayload, SectionRepairPayload
    repairer_llm.queue_responses([
        # Repair response: qualifies the sentence to remove unevidenced energy savings
        SectionRepairPayload(
            section_id="sec_01",
            revised_narration="Habits form automatic routines that run subconsciously, through the habit loop.",
            repairs=[
                ClaimRepairPayload(
                    claim_id="clm_01",
                    action="qualify",
                    target_span="saving significant mental energy.",
                    replacement_span="through the habit loop.",
                    revised_claim_text="Habits form automatic routines that run subconsciously through the habit loop.",
                    evidence_ids=["evi_01"],
                    explanation="Removed unevidenced mental energy claim.",
                )
            ],
        ),
    ])

    repairer = ScriptRepairer(llm_provider=repairer_llm, fact_checker=checker)
    repair_result = repairer.repair_script(script, report, catalog=catalog)

    assert repair_result.is_fully_resolved is True
    assert repair_result.fact_check_report.is_ready is True
    assert repair_result.repaired_script.claims[0].claim_text == "Habits form automatic routines that run subconsciously through the habit loop."


# ---------------------------------------------------------------------------
# Actual Failure Fixture 1: Live Validation 006 clm_11
# ---------------------------------------------------------------------------

def test_actual_live_006_clm_11_fixture():
    """Audit the exact failure from p7_live_validation_006 clm_11 against Western University evidence.
    
    Proposition: 'Habits shape daily behavior and decision-making by automating complex tasks, saving mental energy but potentially limiting flexibility.'
    Status returned by model: 'supported'
    Must be detected as failing factual grounding and adjusted to partially_supported.
    Being high-risk, it must block readiness.
    """
    catalog = _create_catalog({"evi_260158c33510": EVI_HABIT_LOOP}, run_id="rrun_368e5f1dd468")
    claim = ScriptClaim(
        claim_id="clm_11",
        claim_text="Habits shape daily behavior and decision-making by automating complex tasks, saving mental energy but potentially limiting flexibility.",
        section_id="sec_05",
        risk_level="high",
        evidence_ids=["evi_260158c33510"],
    )

    # Actual explanation emitted by Qwen 2.5 7B in p7_live_validation_006
    actual_explanation = (
        "The excerpt from 'The Science Behind Habits: How the Brain Forms and Breaks Them - Thrive Online' "
        "directly supports the proposition by explaining that habits involve an automatic routine which saves mental energy. "
        "This aligns with the claim's assertion about automation saving cognitive resources."
    )

    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_11",
                status="supported",
                explanation=actual_explanation,
                supporting_evidence_ids=["evi_260158c33510"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )

    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_5d457f0fc4fa", project_id="p7_live_validation_006", version=1, title="Habits",
        sections=[ScriptSection(id="sec_05", type="takeaway", heading="Takeaway", narration="Narration.", claim_ids=["clm_11"])],
        claims=[claim], research_run_id="rrun_368e5f1dd468",
    )
    report = checker.check_script(script, catalog=catalog)

    assessment = report.assessments[0]
    assert assessment.status == "partially_supported"
    assert "unverified material component" in assessment.explanation
    assert assessment.recommended_action == "qualify"
    assert report.has_unresolved_high_risk is True
    assert report.is_ready is False


# ---------------------------------------------------------------------------
# Actual Failure Fixture 2: Live Validation 006 clm_12
# ---------------------------------------------------------------------------

def test_actual_live_006_clm_12_fixture():
    """Audit clm_12 attribution from p7_live_validation_006.
    
    Proposition: 'Ann Graybiel’s research demonstrated how cues trigger routines to deliver rewards, making these processes automatic over time.'
    Evidence cited: evi_260158c33510 (Thrive blog, zero mention of Ann Graybiel).
    Must fail grounding because attribution to Ann Graybiel is completely absent from cited evidence.
    """
    catalog = _create_catalog({"evi_260158c33510": EVI_HABIT_LOOP}, run_id="rrun_368e5f1dd468")
    claim = ScriptClaim(
        claim_id="clm_12",
        claim_text="Ann Graybiel’s research demonstrated how cues trigger routines to deliver rewards, making these processes automatic over time.",
        section_id="sec_05",
        risk_level="high",
        evidence_ids=["evi_260158c33510"],
    )

    actual_explanation = (
        "The excerpt from evidence ID evi_260158c33510 directly supports the claim by explaining that cues initiate routines "
        "to deliver rewards, making these processes automatic over time. This directly aligns with Ann Graybiel’s research."
    )

    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_12",
                status="supported",
                explanation=actual_explanation,
                supporting_evidence_ids=["evi_260158c33510"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )

    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_5d457f0fc4fa", project_id="p7_live_validation_006", version=1, title="Habits",
        sections=[ScriptSection(id="sec_05", type="takeaway", heading="Takeaway", narration="Narration.", claim_ids=["clm_12"])],
        claims=[claim], research_run_id="rrun_368e5f1dd468",
    )
    report = checker.check_script(script, catalog=catalog)

    assessment = report.assessments[0]
    assert assessment.status in ("unsupported", "partially_supported")
    assert "Ann Graybiel" in assessment.explanation
    assert report.has_unresolved_high_risk is True
    assert report.is_ready is False


# ===========================================================================
# Dedicated Adversarial Test Suite for All 6 Defect Categories
# ===========================================================================

def test_adversarial_prefix_collision_does_not_establish_support():
    """Prefix collision ('conse' in consequence vs conserve, 'habit' in habitat vs habit) must NOT establish support."""
    catalog = _create_catalog({"evi_env": "The consequences of destroying natural habitats are severe."})
    claim = ScriptClaim(
        claim_id="clm_prefix",
        claim_text="Habits conserve energy.",
        section_id="sec_01",
        risk_level="normal",
        evidence_ids=["evi_env"],
    )
    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_prefix",
                status="supported",
                explanation="Hallucinated match on consequences/habitats.",
                supporting_evidence_ids=["evi_env"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_adv", project_id="proj_adv", version=1, title="Adv",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Text.", claim_ids=["clm_prefix"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "unsupported"
    assert "not substantiated" in assessment.explanation


def test_adversarial_substring_false_positive_rejected():
    """Substring matching ('cue' in 'rescue', 'sav' in 'savage') must NOT establish support."""
    catalog = _create_catalog({"evi_rescue": "The rescue team arrived quickly at the scene."})
    claim = ScriptClaim(
        claim_id="clm_sub",
        claim_text="The animal responds to a cue.",
        section_id="sec_01",
        risk_level="normal",
        evidence_ids=["evi_rescue"],
    )
    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_sub",
                status="supported",
                explanation="Model claims supported.",
                supporting_evidence_ids=["evi_rescue"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_adv", project_id="proj_adv", version=1, title="Adv",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Text.", claim_ids=["clm_sub"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "unsupported"


def test_adversarial_antonym_inversion_detected():
    """Antonymic directionality mismatch ('reduces fatigue' vs 'increases fatigue') must be flagged as contradicted."""
    catalog = _create_catalog({"evi_med": "Clinical trials showed the supplement increases fatigue and mental exhaustion."})
    claim = ScriptClaim(
        claim_id="clm_antonym",
        claim_text="The supplement reduces fatigue.",
        section_id="sec_01",
        risk_level="high",
        evidence_ids=["evi_med"],
    )
    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_antonym",
                status="supported",
                explanation="Model claims supported based on shared words.",
                supporting_evidence_ids=["evi_med"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_adv", project_id="proj_adv", version=1, title="Adv",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Text.", claim_ids=["clm_antonym"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "contradicted"
    assert "Polarity contradiction" in assessment.explanation


def test_adversarial_negation_inversion_detected():
    """Claim asserting absence/negation ('without conscious deliberation') vs evidence asserting strict requirement."""
    catalog = _create_catalog({"evi_delib": "Habit execution always requires conscious deliberation throughout the routine."})
    claim = ScriptClaim(
        claim_id="clm_neg",
        claim_text="Habits form without conscious deliberation.",
        section_id="sec_01",
        risk_level="normal",
        evidence_ids=["evi_delib"],
    )
    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_neg",
                status="supported",
                explanation="Model falsely approves.",
                supporting_evidence_ids=["evi_delib"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_adv", project_id="proj_adv", version=1, title="Adv",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Text.", claim_ids=["clm_neg"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "contradicted"


def test_adversarial_causation_vs_correlation_downgrades():
    """Direct causation asserted when evidence only states correlation must downgrade to partially_supported."""
    catalog = _create_catalog({"evi_corr": "The mechanism is only correlated with the behavior, and no causal link has been demonstrated."})
    claim = ScriptClaim(
        claim_id="clm_causal",
        claim_text="The mechanism causes the behavior.",
        section_id="sec_01",
        risk_level="normal",
        evidence_ids=["evi_corr"],
    )
    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_causal",
                status="supported",
                explanation="Model claims supported.",
                supporting_evidence_ids=["evi_corr"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_adv", project_id="proj_adv", version=1, title="Adv",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Text.", claim_ids=["clm_causal"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "partially_supported"
    assert "correlation" in assessment.explanation


def test_adversarial_universal_claim_against_conditional_evidence():
    """Universal/unconditional assertion ('always in all cases') checked against conditional evidence downgrades."""
    catalog = _create_catalog({"evi_cond": "Only under restricted laboratory conditions in rodents was behavioral automation observed."})
    claim = ScriptClaim(
        claim_id="clm_univ",
        claim_text="Habits always automate behavior in all cases unconditionally.",
        section_id="sec_01",
        risk_level="normal",
        evidence_ids=["evi_cond"],
    )
    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_univ",
                status="supported",
                explanation="Model claims supported.",
                supporting_evidence_ids=["evi_cond"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_adv", project_id="proj_adv", version=1, title="Adv",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Text.", claim_ids=["clm_univ"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "partially_supported"
    assert "universal" in assessment.explanation or "scope" in assessment.explanation


def test_adversarial_unsupported_concessive_clause_downgrades():
    """Concessive clause ('Although habits conserve energy, they can limit flexibility') with energy-only evidence."""
    catalog = _create_catalog({"evi_energy_only": "Habits conserve mental energy by automating routine actions."})
    claim = ScriptClaim(
        claim_id="clm_concessive",
        claim_text="Although habits conserve energy, they can limit flexibility.",
        section_id="sec_01",
        risk_level="normal",
        evidence_ids=["evi_energy_only"],
    )
    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_concessive",
                status="supported",
                explanation="Model claims supported based on energy savings.",
                supporting_evidence_ids=["evi_energy_only"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_adv", project_id="proj_adv", version=1, title="Adv",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Text.", claim_ids=["clm_concessive"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "partially_supported"
    assert "limit flexibility" in assessment.explanation


def test_adversarial_example_list_false_negative_prevented():
    """Illustrative example lists ('such as brushing teeth, checking phones, and driving') must NOT trigger false negatives."""
    catalog = _create_catalog({"evi_daily": "Habits govern daily routine actions in human behavior."})
    claim = ScriptClaim(
        claim_id="clm_examples",
        claim_text="Habits govern routine actions, such as brushing teeth, checking phones, and driving.",
        section_id="sec_01",
        risk_level="normal",
        evidence_ids=["evi_daily"],
    )
    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_examples",
                status="supported",
                explanation="Core proposition is directly verified by daily routine actions.",
                supporting_evidence_ids=["evi_daily"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_adv", project_id="proj_adv", version=1, title="Adv",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Text.", claim_ids=["clm_examples"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "supported"


def test_adversarial_incidental_researcher_mention_rejected():
    """Researcher mentioned in an award context separately from an experiment does NOT substantiate attribution."""
    catalog = _create_catalog({
        "evi_sep": "Ann Graybiel received a major lifetime award in 2012. Separately, mice navigate mazes using the basal ganglia."
    })
    claim = ScriptClaim(
        claim_id="clm_graybiel_incidental",
        claim_text="According to Ann Graybiel, habits are deeply encoded in the basal ganglia.",
        section_id="sec_01",
        risk_level="high",
        evidence_ids=["evi_sep"],
    )
    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_graybiel_incidental",
                status="supported",
                explanation="Model falsely claims Graybiel is mentioned in evidence.",
                supporting_evidence_ids=["evi_sep"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_adv", project_id="proj_adv", version=1, title="Adv",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Text.", claim_ids=["clm_graybiel_incidental"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status in ("unsupported", "partially_supported")
    assert "Ann Graybiel" in assessment.explanation


def test_adversarial_correctly_attributed_finding_supported():
    """Researcher explicitly credited with landmark study in evidence must pass as supported."""
    catalog = _create_catalog({"evi_credited": EVI_GRAYBIEL_MAZE})
    claim = ScriptClaim(
        claim_id="clm_credited",
        claim_text="According to Ann Graybiel at MIT, habits are deeply encoded in the basal ganglia.",
        section_id="sec_01",
        risk_level="high",
        evidence_ids=["evi_credited"],
    )
    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_credited",
                status="supported",
                explanation="Ann Graybiel's lab at MIT is explicitly credited with recording basal ganglia activity during maze runs.",
                supporting_evidence_ids=["evi_credited"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_adv", project_id="proj_adv", version=1, title="Adv",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Text.", claim_ids=["clm_credited"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "supported"


def test_adversarial_incidental_institution_mention_rejected():
    """Institution 'MIT' must NOT match inside 'transmits' and 'permit' via substring."""
    catalog = _create_catalog({"evi_transmits": "The brain transmits neural signals that permit repetition."})
    claim = ScriptClaim(
        claim_id="clm_mit_sub",
        claim_text="According to researchers at MIT, habits form through repetition.",
        section_id="sec_01",
        risk_level="normal",
        evidence_ids=["evi_transmits"],
    )
    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_mit_sub",
                status="supported",
                explanation="Model claims supported.",
                supporting_evidence_ids=["evi_transmits"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_adv", project_id="proj_adv", version=1, title="Adv",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Text.", claim_ids=["clm_mit_sub"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status in ("unsupported", "partially_supported")
    assert "MIT" in assessment.explanation


def test_adversarial_genuine_synonyms_and_paraphrase_supported():
    """Genuine conceptual synonyms ('instantaneous' vs 'immediately without delay') must remain supported."""
    catalog = _create_catalog({"evi_instant": "The reaction occurs immediately without delay in response to the signal."})
    claim = ScriptClaim(
        claim_id="clm_instant",
        claim_text="The response is instantaneous.",
        section_id="sec_01",
        risk_level="normal",
        evidence_ids=["evi_instant"],
    )
    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_instant",
                status="supported",
                explanation="Directly substantiated: occurs immediately without delay.",
                supporting_evidence_ids=["evi_instant"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_adv", project_id="proj_adv", version=1, title="Adv",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Text.", claim_ids=["clm_instant"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "supported"


def test_adversarial_three_component_compound_claim():
    """Three-component claim (cue, routine, reward) succeeds when all present, downgrades when reward missing."""
    # Case A: All 3 present
    catalog_full = _create_catalog({"evi_full": "Habits are triggered by cues, executed through routines, and reinforced by rewards."})
    claim_full = ScriptClaim(
        claim_id="clm_full_triple",
        claim_text="Habits are triggered by cues, executed through routines, and reinforced by rewards.",
        section_id="sec_01",
        risk_level="normal",
        evidence_ids=["evi_full"],
    )
    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_full_triple",
                status="supported",
                explanation="All three components verified.",
                supporting_evidence_ids=["evi_full"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_adv", project_id="proj_adv", version=1, title="Adv",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Text.", claim_ids=["clm_full_triple"])],
        claims=[claim_full], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog_full)
    assert report.assessments[0].status == "supported"

    # Case B: Reward missing in evidence
    catalog_partial = _create_catalog({"evi_part": "Habits are triggered by cues and executed through routines."})
    claim_part = ScriptClaim(
        claim_id="clm_part_triple",
        claim_text="Habits are triggered by cues, executed through routines, and reinforced by rewards.",
        section_id="sec_01",
        risk_level="normal",
        evidence_ids=["evi_part"],
    )
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_part_triple",
                status="supported",
                explanation="Model claims supported.",
                supporting_evidence_ids=["evi_part"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    script_part = Script(
        script_id="scr_adv", project_id="proj_adv", version=1, title="Adv",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Text.", claim_ids=["clm_part_triple"])],
        claims=[claim_part], research_run_id="run_test",
    )
    report_part = checker.check_script(script_part, catalog=catalog_partial)
    assert report_part.assessments[0].status == "partially_supported"
    assert "reinforced by rewards" in report_part.assessments[0].explanation


def test_adversarial_corrective_retry_cannot_restore_unsupported_grounding():
    """When a model's corrective retry stubbornly insists on 'supported', deterministic layer overrides."""
    catalog = _create_catalog({"evi_habit": EVI_HABIT_LOOP})
    claim = ScriptClaim(
        claim_id="clm_stubborn",
        claim_text="Habits automate routines and conserve significant mental energy.",
        section_id="sec_01",
        risk_level="high",
        evidence_ids=["evi_habit"],
    )
    stubborn_response = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_stubborn",
                status="supported",
                explanation="Stubbornly insisting supported despite energy savings missing.",
                supporting_evidence_ids=["evi_habit"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    mock_llm = MockLLMProvider()
    # Emits stubborn response for primary AND retry
    mock_llm.generate_json = lambda prompt, schema: stubborn_response

    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_adv", project_id="proj_adv", version=1, title="Adv",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Text.", claim_ids=["clm_stubborn"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    # Overridden deterministically to partially_supported
    assert assessment.status == "partially_supported"
    assert "conserve significant mental energy" in assessment.explanation
    # High risk partially_supported blocks readiness
    assert report.has_unresolved_high_risk is True
    assert report.is_ready is False


# ---------------------------------------------------------------------------
# CP4C Final Grounding Remediation Adversarial Tests (6 Concerns + Counterexamples)
# ---------------------------------------------------------------------------

def test_adversarial_causation_vs_neutral_association_downgrades():
    """Claim asserts causation ('causes'), but evidence only establishes neutral association ('is associated with')."""
    catalog = _create_catalog({"evi_assoc": "Habit formation is associated with increased cognitive efficiency in workplace tasks."})
    claim = ScriptClaim(
        claim_id="clm_caus_assoc",
        claim_text="Habit formation causes increased cognitive efficiency.",
        section_id="sec_01",
        risk_level="high",
        evidence_ids=["evi_assoc"],
    )
    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_caus_assoc",
                status="supported",
                explanation="Model claims causation is supported by association study.",
                supporting_evidence_ids=["evi_assoc"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_adv", project_id="proj_adv", version=1, title="Adv",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Text.", claim_ids=["clm_caus_assoc"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "partially_supported"
    assert "neutral correlation or association" in assessment.explanation
    assert report.is_ready is False


def test_adversarial_causation_explicit_denial_contradicted():
    """Claim asserts causation ('causes'), but evidence explicitly denies it ('does not cause')."""
    catalog = _create_catalog({"evi_deny": "Habit formation does not cause increased cognitive efficiency."})
    claim = ScriptClaim(
        claim_id="clm_caus_deny",
        claim_text="Habit formation causes increased cognitive efficiency.",
        section_id="sec_01",
        risk_level="high",
        evidence_ids=["evi_deny"],
    )
    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_caus_deny",
                status="supported",
                explanation="Model erroneously approves despite explicit refutation.",
                supporting_evidence_ids=["evi_deny"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_adv", project_id="proj_adv", version=1, title="Adv",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Text.", claim_ids=["clm_caus_deny"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "contradicted"
    assert "refutes causation" in assessment.explanation
    assert report.is_ready is False


def test_adversarial_counterexample_association_claim_against_association_evidence():
    """Counterexample: Claim asserting association ('is associated with') remains supported by association evidence."""
    catalog = _create_catalog({"evi_assoc": "Habit formation is associated with increased cognitive efficiency in workplace tasks."})
    claim = ScriptClaim(
        claim_id="clm_assoc_ok",
        claim_text="Habit formation is associated with increased cognitive efficiency.",
        section_id="sec_01",
        risk_level="normal",
        evidence_ids=["evi_assoc"],
    )
    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_assoc_ok",
                status="supported",
                explanation="Direct association supported.",
                supporting_evidence_ids=["evi_assoc"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_adv", project_id="proj_adv", version=1, title="Adv",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Text.", claim_ids=["clm_assoc_ok"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "supported"


def test_adversarial_researcher_unrelated_study_rejected():
    """Researcher name appears in same sentence as 'study', but the study is on an unrelated topic."""
    catalog = _create_catalog({"evi_unrelated_study": "Ann Graybiel published a famous study on ocular dominance in 1985. Meanwhile, other researchers discovered habit chunking in rats."})
    claim = ScriptClaim(
        claim_id="clm_unrelated_attr",
        claim_text="Ann Graybiel discovered habit chunking in rats.",
        section_id="sec_01",
        risk_level="high",
        evidence_ids=["evi_unrelated_study"],
    )
    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_unrelated_attr",
                status="supported",
                explanation="Model claims Graybiel is supported because study and name appear.",
                supporting_evidence_ids=["evi_unrelated_study"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_adv", project_id="proj_adv", version=1, title="Adv",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Text.", claim_ids=["clm_unrelated_attr"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "unsupported"
    assert "attribution:ann graybiel" in assessment.explanation


def test_adversarial_substantive_claim_in_illustrative_phrase_audited():
    """Proposition following 'including' contains substantive claims ('improve memory') that lack evidence."""
    catalog = _create_catalog({"evi_routines": "Habits heavily shape daily human routines and everyday behaviors."})
    claim = ScriptClaim(
        claim_id="clm_sub_incl",
        claim_text="Habits influence daily routines, including routines that improve long-term memory.",
        section_id="sec_01",
        risk_level="high",
        evidence_ids=["evi_routines"],
    )
    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_sub_incl",
                status="supported",
                explanation="Model claims habits influence routines is supported.",
                supporting_evidence_ids=["evi_routines"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_adv", project_id="proj_adv", version=1, title="Adv",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Text.", claim_ids=["clm_sub_incl"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "partially_supported"
    assert "improve long-term memory" in assessment.explanation
    assert report.is_ready is False


def test_adversarial_non_restrictive_relative_clause_audited():
    """Non-restrictive relative clause (', which permanently eliminates cravings') is audited as a distinct component."""
    catalog = _create_catalog({"evi_circuit": "Habit formation reorganizes basal ganglia circuitry and behavioral sequences."})
    claim = ScriptClaim(
        claim_id="clm_relative",
        claim_text="Habit formation reorganizes basal ganglia circuitry, which permanently eliminates cravings.",
        section_id="sec_01",
        risk_level="high",
        evidence_ids=["evi_circuit"],
    )
    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_relative",
                status="supported",
                explanation="Model claims basal ganglia circuitry change is supported.",
                supporting_evidence_ids=["evi_circuit"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_adv", project_id="proj_adv", version=1, title="Adv",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Text.", claim_ids=["clm_relative"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "partially_supported"
    assert "permanently eliminates cravings" in assessment.explanation
    assert report.is_ready is False


def test_adversarial_creative_metaphor_with_embedded_facts_guarded():
    """Metaphorical frame ('Picture this... act like') containing embedded empirical assertions is audited strictly."""
    catalog = _create_catalog({"evi_general": "Habit loops create automatic routines in daily life."})
    # Claim with metaphor + empirical assertion (biological superconductor doubling brain speed)
    claim_text = "Picture this: habit loops act like a biological superconductor that doubles brain processing speed."
    from backend.app.services.scriptwriter.classifier import classify_claim_risk
    risk = classify_claim_risk(claim_text)
    assert risk == "high", "Metaphorical framing with embedded empirical claims must be classified as high risk!"

    claim = ScriptClaim(
        claim_id="clm_meta_fact",
        claim_text=claim_text,
        section_id="sec_01",
        risk_level=risk,
        evidence_ids=["evi_general"],
    )
    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_meta_fact",
                status="supported",
                explanation="Model claims metaphor is valid.",
                supporting_evidence_ids=["evi_general"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_adv", project_id="proj_adv", version=1, title="Adv",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Text.", claim_ids=["clm_meta_fact"])],
        claims=[claim], research_run_id="run_test",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status in ("unsupported", "partially_supported")
    assert assessment.status != "supported"
    assert report.is_ready is False

