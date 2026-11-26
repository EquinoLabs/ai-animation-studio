"""Comprehensive test suite for Phase 7 CP4C Remediation:
Claim Isolation, Evidence Relevance, Narrative Development, and Duration Compliance.

Tests:
1. Fact-checker claim isolation (1 claim per LLM call, prompt isolation, retry isolation, independent failure isolation).
2. Evidence extraction topic/query relevance, promotional/generic roadmap rejection, and verbatim preservation.
3. Outline narrative depth (4-5 beats for >150 words) and proportional underdevelopment expansion trigger.
4. Duration compliance evidence-quality correction (distinguishing substantive evidence from marketing/roadmaps).
"""

from pathlib import Path
import pytest
import tempfile
from typing import List, Optional

from backend.app.config import Settings
from backend.app.domain.fact_check import (
    BatchFactCheckResponsePayload,
    ClaimAssessment,
    ClaimAssessmentPayload,
    ScriptFactCheckReport,
)
from backend.app.domain.research import (
    EvidenceRecord,
    PlannedQuery,
    QueryRecord,
    ResearchPlan,
    ResearchRun,
    SourceRecord,
)
from backend.app.domain.script import (
    GeneratedScriptPayload,
    GeneratedSectionPayload,
    Script,
    ScriptClaim,
    ScriptClaimPayload,
    ScriptOutline,
    ScriptOutlineSection,
    ScriptSection,
    ScriptSectionPayload,
    _is_substantive_evidence_record,
    count_words,
    populate_script_duration_metrics,
)
from backend.app.llm.base import LLMResponseMalformedError
from backend.app.llm.mock import MockLLMProvider
from backend.app.services.research.orchestrator import (
    ResearchOrchestrator,
    extract_verbatim_evidence,
    score_candidate_paragraph,
)
from backend.app.services.scriptwriter.fact_checker import (
    ScriptFactChecker,
)
from backend.app.services.scriptwriter.generator import (
    DraftGeneratorInput,
    ScriptDraftGenerator,
)
from backend.app.services.scriptwriter.outliner import ValidatedEvidenceCatalog
from backend.app.services.scriptwriter.policy import (
    ScriptPromptBuilder,
    WritingPolicy,
)


from backend.app.services.scriptwriter.service import ScriptService


# ===========================================================================
# Workstream 1: Fact-Checker Claim-Level Isolation Tests
# ===========================================================================

def test_production_fact_checker_uses_single_claim_isolation():
    """Verify production ScriptService initializes ScriptFactChecker with max_batch_size = 1 for claim isolation."""
    mock_llm = MockLLMProvider()
    service = ScriptService(llm_provider=mock_llm)
    assert service.fact_checker.max_batch_size == 1


def test_fact_checker_evaluates_each_claim_in_isolated_request():
    """Verify that multi-claim scripts result in 1 isolated LLM request per claim."""
    prompts_received = []

    def tracking_generate_json(prompt: str, schema):
        prompts_received.append(prompt)
        # Parse claim ID from prompt
        if "clm_01" in prompt:
            return BatchFactCheckResponsePayload(
                assessments=[
                    ClaimAssessmentPayload(
                        claim_id="clm_01",
                        status="supported",
                        explanation="Evidence 1 supports claim 1.",
                        supporting_evidence_ids=["evi_01"],
                        contradicting_evidence_ids=[],
                        contextual_evidence_ids=[],
                        recommended_action="keep",
                    )
                ]
            )
        elif "clm_02" in prompt:
            return BatchFactCheckResponsePayload(
                assessments=[
                    ClaimAssessmentPayload(
                        claim_id="clm_02",
                        status="supported",
                        explanation="Evidence 2 supports claim 2.",
                        supporting_evidence_ids=["evi_02"],
                        contradicting_evidence_ids=[],
                        contextual_evidence_ids=[],
                        recommended_action="keep",
                    )
                ]
            )
        raise ValueError(f"Unexpected prompt: {prompt}")

    mock_llm = MockLLMProvider()
    mock_llm.generate_json = tracking_generate_json

    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)

    claims = [
        ScriptClaim(
            claim_id="clm_01",
            claim_text="Habits are driven by the basal ganglia.",
            section_id="sec_01",
            evidence_ids=["evi_01"],
        ),
        ScriptClaim(
            claim_id="clm_02",
            claim_text="Dopamine reinforces synaptic pathways.",
            section_id="sec_02",
            evidence_ids=["evi_02"],
        ),
    ]
    sections = [
        ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Habits are driven by the basal ganglia.", claim_ids=["clm_01"]),
        ScriptSection(id="sec_02", type="middle", heading="Middle", narration="Dopamine reinforces synaptic pathways.", claim_ids=["clm_02"]),
    ]
    script = Script(
        id="scr_01",
        project_id="proj_alpha",
        research_run_id="run_01",
        title="Habits",
        sections=sections,
        claims=claims,
    )

    evidence = {
        "evi_01": EvidenceRecord(
            evidence_id="evi_01",
            project_id="proj_alpha",
            source_id="s1",
            query_id="q1",
            excerpt="The basal ganglia is central to habit formation.",
        ),
        "evi_02": EvidenceRecord(
            evidence_id="evi_02",
            project_id="proj_alpha",
            source_id="s2",
            query_id="q2",
            excerpt="Dopamine reinforces synaptic pathways during reward anticipation.",
        ),
    }
    sources = {
        "s1": SourceRecord(source_id="s1", project_id="proj_alpha", url="https://example.com/s1"),
        "s2": SourceRecord(source_id="s2", project_id="proj_alpha", url="https://example.com/s2"),
    }
    catalog = ValidatedEvidenceCatalog(
        run_id="run_01",
        evidence_by_id=evidence,
        sources_by_id=sources,
    )

    report = checker.check_script(
        script=script,
        catalog=catalog,
        project_id="proj_alpha",
    )

    # Exactly 2 calls made
    assert len(prompts_received) == 2

    # Prompt 1 strictly isolates clm_01 and evi_01
    assert "=== CLAIM FACT-CHECK REQUEST ===" in prompts_received[0]
    assert "clm_01" in prompts_received[0]
    assert "evi_01" in prompts_received[0]
    assert "clm_02" not in prompts_received[0]
    assert "evi_02" not in prompts_received[0]

    # Prompt 2 strictly isolates clm_02 and evi_02
    assert "=== CLAIM FACT-CHECK REQUEST ===" in prompts_received[1]
    assert "clm_02" in prompts_received[1]
    assert "evi_02" in prompts_received[1]
    assert "clm_01" not in prompts_received[1]
    assert "evi_01" not in prompts_received[1]

    # Both assessments recorded successfully
    assert len(report.assessments) == 2
    assert report.assessments[0].claim_id == "clm_01"
    assert report.assessments[0].status == "supported"
    assert report.assessments[1].claim_id == "clm_02"
    assert report.assessments[1].status == "supported"


def test_fact_checker_single_claim_failure_does_not_affect_other_claims():
    """Verify that if one claim causes malformed output, only that claim becomes not_checked."""
    call_counts = {"clm_01": 0, "clm_02": 0}

    def failing_first_claim_llm(prompt: str, schema):
        if "clm_01" in prompt:
            call_counts["clm_01"] += 1
            # Always raise malformed error for clm_01 (initial + retry fail)
            raise LLMResponseMalformedError("Simulated LLM JSON malformation")
        elif "clm_02" in prompt:
            call_counts["clm_02"] += 1
            return BatchFactCheckResponsePayload(
                assessments=[
                    ClaimAssessmentPayload(
                        claim_id="clm_02",
                        status="supported",
                        explanation="Valid claim 2",
                        supporting_evidence_ids=["evi_02"],
                        contradicting_evidence_ids=[],
                        contextual_evidence_ids=[],
                        recommended_action="keep",
                    )
                ]
            )
        raise ValueError(f"Unknown prompt: {prompt}")

    mock_llm = MockLLMProvider()
    mock_llm.generate_json = failing_first_claim_llm

    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)

    claims = [
        ScriptClaim(claim_id="clm_01", claim_text="Claim 1", section_id="sec_01", evidence_ids=["evi_01"]),
        ScriptClaim(claim_id="clm_02", claim_text="Claim 2", section_id="sec_02", evidence_ids=["evi_02"]),
    ]
    sections = [
        ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Claim 1 narration", claim_ids=["clm_01"]),
        ScriptSection(id="sec_02", type="middle", heading="Middle", narration="Claim 2 narration", claim_ids=["clm_02"]),
    ]
    script = Script(id="scr_02", project_id="proj_alpha", research_run_id="run_02", title="Habits", sections=sections, claims=claims)

    evidence = {
        "evi_01": EvidenceRecord(evidence_id="evi_01", project_id="proj_alpha", source_id="s1", query_id="q1", excerpt="Text 1"),
        "evi_02": EvidenceRecord(evidence_id="evi_02", project_id="proj_alpha", source_id="s2", query_id="q2", excerpt="Text 2"),
    }
    sources = {
        "s1": SourceRecord(source_id="s1", project_id="proj_alpha", url="https://example.com/s1"),
        "s2": SourceRecord(source_id="s2", project_id="proj_alpha", url="https://example.com/s2"),
    }
    catalog = ValidatedEvidenceCatalog(run_id="run_02", evidence_by_id=evidence, sources_by_id=sources)

    report = checker.check_script(
        script=script,
        catalog=catalog,
        project_id="proj_alpha",
    )

    assert len(report.assessments) == 2
    # clm_01 failed and gracefully fell back to not_checked
    assert report.assessments[0].claim_id == "clm_01"
    assert report.assessments[0].status == "not_checked"
    assert "malformed response on retry" in report.assessments[0].explanation

    # clm_02 completely succeeded
    assert report.assessments[1].claim_id == "clm_02"
    assert report.assessments[1].status == "supported"


# ===========================================================================
# Workstream 2: Evidence Relevance & Anti-Promotional Filtering Tests
# ===========================================================================

def test_score_candidate_paragraph_penalizes_promotional_copy():
    """Verify that marketing/funding copy receives severe penalties (-150.0)."""
    promotional_text = (
        "We help startups raise capital and secure angel investors. "
        "Our team works with founders to review your pitch deck, plan your capital raise, "
        "and find the right funding roadmap for real estate and tech startups."
    )
    score_promo = score_candidate_paragraph(
        paragraph=promotional_text,
        target_question="How does habit formation work in the human brain?",
        query_text="neuroscience habit formation basal ganglia",
        topic="Habit Formation",
    )

    scientific_text = (
        "The basal ganglia plays a central role in habit formation by chunking behavioral sequences. "
        "When an action becomes automatic, the prefrontal cortex reduces activity while the striatum "
        "maintains procedural execution in response to environmental cues."
    )
    score_sci = score_candidate_paragraph(
        paragraph=scientific_text,
        target_question="How does habit formation work in the human brain?",
        query_text="neuroscience habit formation basal ganglia",
        topic="Habit Formation",
    )

    assert score_promo < 0.0
    assert score_sci > 50.0
    assert score_sci > score_promo + 100.0


def test_score_candidate_paragraph_penalizes_generic_roadmap():
    """Verify that generic roadmap copy is penalized."""
    roadmap_text = (
        "In this article we will explore the habit loop. "
        "Stay tuned to discover how the brain automates daily actions. "
        "We will also discuss effective strategies for changing habits."
    )
    score_roadmap = score_candidate_paragraph(
        paragraph=roadmap_text,
        target_question="Why do humans form habits?",
        query_text="psychology of habit loops",
        topic="Habit Formation",
    )
    assert score_roadmap < 20.0


def test_extract_verbatim_evidence_selects_relevant_paragraph_over_promotional():
    """Verify extract_verbatim_evidence selects substantive content over promotional header text."""
    markdown_content = (
        "# FasterCapital Startup Services\n\n"
        "We help startups raise capital, find angel investors, and review pitch decks. "
        "Contact our team to get funded and grow your early stage venture.\n\n"
        "## Neural Mechanisms of Habit Formation\n\n"
        "Research in cognitive neuroscience demonstrates that habit formation relies on a three-part loop: "
        "a sensory cue, a routine behavioral sequence, and a dopamine-mediated neurological reward. "
        "Over time, synaptic plasticity in the striatum automates this behavioral pathway."
    )

    excerpt, locator = extract_verbatim_evidence(
        content=markdown_content,
        max_chars=1000,
        query_text="habit loop sensory cue routine reward dopamine striatum",
        topic="Habit Formation",
        target_question="How does the habit loop work neurologically?",
    )

    assert excerpt is not None
    assert "striatum" in excerpt or "three-part loop" in excerpt
    assert "raise capital" not in excerpt
    assert "angel investors" not in excerpt
    assert locator is not None


# ===========================================================================
# Workstream 3: Outline Narrative Depth & Proportional Expansion Tests
# ===========================================================================

def test_build_outline_prompt_specifies_multi_beat_depth():
    """Verify outline prompt explicitly requires 4-5 sequential narrative beats for substantial sections."""
    policy = WritingPolicy()
    builder = ScriptPromptBuilder(policy=policy)
    prompt = builder.build_outline_prompt(
        topic="The Science of Sleep Cycles",
        target_duration_minutes=5.0,
        evidence_brief_text=None,
    )

    assert "4–5 distinct mechanical steps" in prompt
    assert "Core mechanical step 1: trigger or sensory cue" in prompt
    assert "Core mechanical step 4: reinforcing feedback or reward mechanism" in prompt


def test_proportional_expansion_condition_triggers_appropriately():
    """Verify proportional underdevelopment triggers expansion when appropriate."""
    out_sec_large = ScriptOutlineSection(
        type="middle",
        heading="Mechanics",
        target_words=250,
        factual_milestones=["M1", "M2", "M3", "M4"],
        visual_concept="V1",
    )

    # 134 words for target 250:
    # 134 < 0.65 * 250 (162.5), even though 134 >= 60 words
    wc_134 = 134
    should_expand_134 = (
        (out_sec_large.target_words >= 120 and wc_134 < (0.65 * out_sec_large.target_words))
        or (wc_134 < (0.5 * out_sec_large.target_words))
        or (out_sec_large.target_words >= 60 and wc_134 < 40)
    )
    assert should_expand_134 is True

    # 180 words for target 250:
    # 180 > 162.5 -> should not expand
    wc_180 = 180
    should_expand_180 = (
        (out_sec_large.target_words >= 120 and wc_180 < (0.65 * out_sec_large.target_words))
        or (wc_180 < (0.5 * out_sec_large.target_words))
        or (out_sec_large.target_words >= 60 and wc_180 < 40)
    )
    assert should_expand_180 is False

    # Short section: target 80, wc 35 (< 40 words)
    out_sec_short = ScriptOutlineSection(
        type="hook",
        heading="Hook",
        target_words=80,
        factual_milestones=["M1"],
        visual_concept="V1",
    )
    wc_35 = 35
    should_expand_35 = (
        (out_sec_short.target_words >= 120 and wc_35 < (0.65 * out_sec_short.target_words))
        or (wc_35 < (0.5 * out_sec_short.target_words))
        or (out_sec_short.target_words >= 60 and wc_35 < 40)
    )
    assert should_expand_35 is True


# ===========================================================================
# Workstream 4: Duration Compliance Evidence Substance Classification Tests
# ===========================================================================

def test_is_substantive_evidence_record_detects_promotional_copy():
    """Verify helper identifies promotional or generic roadmap text."""
    promotional_rec = EvidenceRecord(
        evidence_id="evi_promo",
        project_id="prj_test",
        source_id="s1",
        query_id="q1",
        excerpt="We help founders raise capital and find angel investors with pitch deck consulting and seed venture financing.",
    )
    assert _is_substantive_evidence_record(promotional_rec) is False

    roadmap_rec = EvidenceRecord(
        evidence_id="evi_road",
        project_id="prj_test",
        source_id="s2",
        query_id="q2",
        excerpt="In this article we will explore the habit loop. In the following sections we will cover habit loops and related topics.",
    )
    assert _is_substantive_evidence_record(roadmap_rec) is False

    substantive_rec = EvidenceRecord(
        evidence_id="evi_sci",
        project_id="prj_test",
        source_id="s3",
        query_id="q3",
        excerpt="The basal ganglia consolidates neural patterns through repetition and synaptic reinforcement, allowing complex behavioral routines to execute automatically with minimal prefrontal cortex activation.",
    )
    assert _is_substantive_evidence_record(substantive_rec) is True


def test_duration_metrics_classifies_evidence_deficit_when_records_are_promotional():
    """Verify that a short script with 3 records (2 promotional, 1 substantive) is classified as evidence deficit."""
    script = Script(
        id="scr_test",
        project_id="prj_test",
        title="Test Script",
        target_duration_minutes=5.0,
        target_word_count=750,
        sections=[
            ScriptSection(id="s1", type="hook", heading="Hook", narration="A short hook intro with just a few basic introductory words.", word_count=10),
            ScriptSection(id="s2", type="middle", heading="Middle", narration="A brief middle explanation describing the general problem in superficial terms.", word_count=12),
        ],
        claims=[],
    )

    # 3 total records: 1 substantive with ~55 words (>=50 and <80 words), 2 promotional
    evidence_records = [
        EvidenceRecord(
            evidence_id="evi_01",
            project_id="prj_test",
            source_id="s1",
            query_id="q1",
            excerpt=(
                "Habit formation relies on synaptic connections within the basal ganglia and striatum to "
                "automate repetitive routines across time with minimal active prefrontal cortex control. "
                "Through prolonged behavioral practice, synaptic plasticity stabilizes neural pathways so that "
                "actions execute predictably in response to environmental cues and associated contextual triggers "
                "without requiring continuous conscious deliberation."
            ),
        ),
        EvidenceRecord(
            evidence_id="evi_02",
            project_id="prj_test",
            source_id="s2",
            query_id="q2",
            excerpt="We help startups raise capital and secure angel investors for real estate funding with customized pitch decks and business plans.",
        ),
        EvidenceRecord(
            evidence_id="evi_03",
            project_id="prj_test",
            source_id="s3",
            query_id="q3",
            excerpt="In this article we will explore how businesses grow. Stay tuned to discover our consulting methods and strategies in later sections.",
        ),
    ]

    updated_script = populate_script_duration_metrics(
        script=script,
        evidence_records=evidence_records,
    )

    assert updated_script.duration_compliance is not None
    assert updated_script.duration_compliance.compliance_status == "under_target_evidence_deficit"
    assert "1 substantive record" in updated_script.duration_compliance.assessment_note
    assert "2 non-substantive records excluded" in updated_script.duration_compliance.assessment_note


def test_duration_metrics_classifies_generator_shortfall_when_evidence_is_genuinely_substantive():
    """Verify that a short script with >= 3 genuinely substantive records is classified as generator shortfall."""
    script = Script(
        id="scr_test",
        project_id="prj_test",
        title="Test Script",
        target_duration_minutes=5.0,
        target_word_count=750,
        sections=[
            ScriptSection(id="s1", type="hook", heading="Hook", narration="A short hook intro discussing neural habits.", word_count=6),
            ScriptSection(id="s2", type="middle", heading="Middle", narration="A brief middle explanation of the striatum and synaptic connections.", word_count=10),
        ],
        claims=[],
    )

    # 3 distinct sources, 3 distinct queries, each substantive with ~45 words (total ~135 >= 100 words)
    evidence_records = [
        EvidenceRecord(
            evidence_id="evi_01",
            project_id="prj_test",
            source_id="s1",
            query_id="q1",
            excerpt=(
                "Habit formation relies heavily on synaptic connections within the basal ganglia and striatum to "
                "automate repetitive behavioral routines across extended time horizons without conscious effort from "
                "prefrontal executive systems in primate subjects. This architectural delegation frees conscious attention "
                "for novel sensory inputs and adaptive problem solving."
            ),
        ),
        EvidenceRecord(
            evidence_id="evi_02",
            project_id="prj_test",
            source_id="s2",
            query_id="q2",
            excerpt=(
                "Dopamine release actively anticipates biological rewards and powerfully reinforces the underlying behavioral "
                "routine upon encountering salient environmental cues through specialized neurological pathways in primates "
                "and human cognitive test subjects. Phasic dopamine surges signal reward prediction errors and calibrate "
                "habitual reinforcement circuits over repeated trial iterations."
            ),
        ),
        EvidenceRecord(
            evidence_id="evi_03",
            project_id="prj_test",
            source_id="s3",
            query_id="q3",
            excerpt=(
                "Prefrontal cognitive control decreases substantially as procedural automaticity increases during habitual "
                "behavioral execution in both animal models and healthy human subjects across empirical neuroscience studies "
                "and behavioral laboratories. Optogenetic inhibition demonstrates that deliberate goal-directed action can be "
                "rapidly restored when corticostriatal communications are modulated dynamically."
            ),
        ),
    ]

    updated_script = populate_script_duration_metrics(
        script=script,
        evidence_records=evidence_records,
    )

    assert updated_script.duration_compliance is not None
    assert updated_script.duration_compliance.compliance_status == "under_target_generator_shortfall"
    assert "Adequate substantive research evidence was available" in updated_script.duration_compliance.assessment_note


# ===========================================================================
# Workstream 5: Required Section 4 Regression Tests (10 Mandated Scenarios)
# ===========================================================================

def test_regression_01_unrelated_educational_article_cannot_produce_eligible_evidence():
    """Scenario 1: An entirely unrelated educational article (Victorian crop rotation)

    cannot produce eligible evidence for a habit formation topic.
    """
    victorian_article = (
        "# Agricultural Revolutions in Victorian England\n\n"
        "In nineteenth-century Norfolk, agricultural innovators popularized the four-field crop rotation system. "
        "By alternating wheat, turnips, barley, and clover across consecutive farming seasons, landowners successfully "
        "replenished soil nitrogen levels without requiring a fallow season. This dramatic increase in fodder crop "
        "production sustained livestock herds throughout winter months, fundamentally elevating British crop yields "
        "and transforming agrarian productivity across Western Europe."
    )
    topic = "Why do humans form habits, and how does habit formation work?"
    query = "neuroscience of habit formation cue routine reward"
    question = "How does the neurological habit loop operate in humans?"

    # Candidate scoring must be heavily penalized (< 0.0, -100.0 relevance gate)
    score = score_candidate_paragraph(
        paragraph=victorian_article,
        target_question=question,
        query_text=query,
        topic=topic,
    )
    assert score < 0.0

    # Extraction must return None, None
    excerpt, locator = extract_verbatim_evidence(
        content=victorian_article,
        max_chars=1000,
        query_text=query,
        topic=topic,
        target_question=question,
    )
    assert excerpt is None
    assert locator is None


def test_regression_02_relevant_passage_using_technical_or_synonymous_terminology_is_selected():
    """Scenario 2: A relevant passage using technical or synonymous terminology is selected and preserved."""
    technical_article = (
        "# Neurobiology of Action Selection\n\n"
        "Through persistent conditioning, synaptic plasticity within the striatum stabilizes procedural action sequences "
        "until environmental triggers elicit automatic behavioral responses with negligible supervisory oversight from the cortex."
    )
    topic = "Why do humans form habits, and how does habit formation work?"
    query = "synaptic plasticity procedural automaticity striatum conditioning"
    question = "What neurobiological mechanisms automate human routines?"

    score = score_candidate_paragraph(
        paragraph=technical_article,
        target_question=question,
        query_text=query,
        topic=topic,
    )
    assert score > 40.0

    excerpt, locator = extract_verbatim_evidence(
        content=technical_article,
        max_chars=1000,
        query_text=query,
        topic=topic,
        target_question=question,
    )
    assert excerpt is not None
    assert "synaptic plasticity within the striatum" in excerpt
    assert locator == "heading: 'Neurobiology of Action Selection'"


def test_regression_03_unrelated_passage_not_selected_merely_because_no_better_paragraph_exists():
    """Scenario 3: An unrelated passage is not selected merely because no better paragraph exists."""
    unrelated_doc = (
        "# Principles of Quantum Thermodynamics\n\n"
        "Macroscopic thermodynamic equilibrium emerges from underlying statistical mechanics governed by microscopic quantum states. "
        "Isolated thermodynamic systems maximize entropy as available energy dissipates across microscopic vibrational modes.\n\n"
        "In non-equilibrium steady states, energy gradients drive irreversible entropy production through persistent heat dissipation. "
        "Fluctuation theorems rigorously quantify the probabilistic symmetry of microscopic entropy production trajectories."
    )
    topic = "Why do humans form habits, and how does habit formation work?"

    excerpt, locator = extract_verbatim_evidence(
        content=unrelated_doc,
        max_chars=1000,
        query_text="how habits form",
        topic=topic,
        target_question="How does habit formation work?",
    )
    # Even though both paragraphs have high prose quality, neither is selected
    assert excerpt is None
    assert locator is None


def test_regression_04_relevant_passage_appearing_late_selected_over_early_promotional_header():
    """Scenario 4: A relevant passage appearing late in a document is selected over early promotional or roadmap headers."""
    mixed_doc = (
        "# Startup Accelerator & Capital Services\n\n"
        "We help innovative founders raise capital, prepare compelling pitch decks, and connect with angel investors.\n\n"
        "## Overview of Behavioral Guides\n\n"
        "In this article we will explore human habit formation and discuss effective behavioral change strategies.\n\n"
        "## Corticostriatal Pathways in Habitual Control\n\n"
        "Empirical neuroscience demonstrates that habit formation relies on a neurological loop comprising a sensory cue, "
        "an automated behavioral routine, and a dopamine-mediated reward signal. As repetitions accumulate, synaptic connections "
        "in the basal ganglia strengthen, delegating procedural control away from conscious executive regions."
    )
    topic = "Why do humans form habits, and how does habit formation work?"
    query = "cue routine reward habit loop basal ganglia"

    excerpt, locator = extract_verbatim_evidence(
        content=mixed_doc,
        max_chars=1000,
        query_text=query,
        topic=topic,
        target_question="How does the neurological habit loop operate?",
    )
    assert excerpt is not None
    assert "basal ganglia strengthen" in excerpt
    assert "raise capital" not in excerpt
    assert "angel investors" not in excerpt
    assert locator == "heading: 'Corticostriatal Pathways in Habitual Control'"


def test_regression_05_promotional_and_generic_roadmap_content_remain_excluded():
    """Scenario 5: Promotional and generic roadmap content remain strictly excluded."""
    promo_text = "We help tech startups raise capital, connect with angel investors, and refine pitch decks for venture funding."
    roadmap_text = "In this article we will explore habit formation. Stay tuned to discover how the brain automates daily actions."

    score_promo = score_candidate_paragraph(
        paragraph=promo_text,
        target_question="How do habits work?",
        query_text="habit formation",
        topic="Habit Formation",
    )
    assert score_promo < 0.0

    score_roadmap = score_candidate_paragraph(
        paragraph=roadmap_text,
        target_question="How do habits work?",
        query_text="habit formation",
        topic="Habit Formation",
    )
    # Roadmap has a -40 penalty, remaining well below minimum selection threshold
    assert score_roadmap < 20.0

    rec_promo = EvidenceRecord(evidence_id="ev_p", project_id="p1", source_id="s1", query_id="q1", excerpt=promo_text)
    rec_roadmap = EvidenceRecord(evidence_id="ev_r", project_id="p1", source_id="s1", query_id="q1", excerpt=roadmap_text)
    assert _is_substantive_evidence_record(rec_promo, topic="Habit Formation") is False
    assert _is_substantive_evidence_record(rec_roadmap, topic="Habit Formation") is False


def test_regression_06_verbatim_excerpt_and_locator_integrity_remain_intact():
    """Scenario 6: Verbatim excerpt and locator integrity remain intact."""
    markdown_doc = (
        "# Behavioral Neuroscience Review\n\n"
        "### Striatal Dopamine and Cue-Triggered Routines\n\n"
        "Phasic dopamine bursts in the nucleus accumbens and dorsolateral striatum encode reward prediction errors during habit acquisition. "
        "These neurochemical bursts consolidate synaptic plasticity so that environmental cues trigger procedural routines automatically."
    )
    topic = "Habit Formation"
    query = "striatal dopamine cue routine reward habit acquisition"

    excerpt, locator = extract_verbatim_evidence(
        content=markdown_doc,
        max_chars=1000,
        query_text=query,
        topic=topic,
        target_question="How does dopamine reinforce habit acquisition?",
    )
    assert excerpt is not None
    assert locator == "heading: 'Striatal Dopamine and Cue-Triggered Routines'"
    # Strict verbatim integrity: excerpt must exist in source document unchanged
    assert excerpt in markdown_doc


def test_regression_07_substantive_off_topic_evidence_does_not_count_toward_duration_compliance():
    """Scenario 7: Substantive but off-topic evidence does not count toward duration compliance."""
    script = Script(
        script_id="scr_off_topic",
        project_id="proj_habits",
        title="Habit Formation",
        topic="Why do humans form habits, and how does habit formation work?",
        target_duration_minutes=5.0,
        sections=[
            ScriptSection(id="sec_01", type="middle", heading="Body", narration="A brief summary of habits that is far under the target word count.", target_duration_seconds=10),
        ],
    )
    # 3 records with substantive educational prose, but entirely off-topic
    records = [
        EvidenceRecord(
            evidence_id="evi_01",
            project_id="proj_habits",
            source_id="src_01",
            query_id="q_01",
            excerpt=(
                "In nineteenth-century Norfolk, agricultural innovators popularized the four-field crop rotation system. "
                "By alternating wheat, turnips, barley, and clover across consecutive farming seasons, landowners successfully "
                "replenished soil nitrogen levels without requiring a fallow season across British estates."
            ),
        ),
        EvidenceRecord(
            evidence_id="evi_02",
            project_id="proj_habits",
            source_id="src_02",
            query_id="q_02",
            excerpt=(
                "Medieval blacksmithing techniques relied heavily on charcoal hearths to heat wrought iron bloom to forging temperatures. "
                "Master metalworkers carefully manipulated anvil striking angles and quench rates to temper edge tools and armor components."
            ),
        ),
        EvidenceRecord(
            evidence_id="evi_03",
            project_id="proj_habits",
            source_id="src_03",
            query_id="q_03",
            excerpt=(
                "Deep-sea hydrothermal vents support chemotrophic ecosystems independent of sunlight. Specialized tubeworms harbor "
                "symbiotic sulfur-oxidizing bacteria within their trophosome tissues to synthesize organic molecules in abyssal depths."
            ),
        ),
    ]

    updated = populate_script_duration_metrics(script=script, evidence_records=records)
    assert updated.duration_compliance is not None
    assert updated.duration_compliance.compliance_status == "under_target_evidence_deficit"
    assert "none contained substantive topic-relevant evidence" in updated.duration_compliance.assessment_note
    assert "3 substantive but off-topic record(s) excluded" in updated.duration_compliance.assessment_note


def test_regression_08_substantive_topic_relevant_evidence_allows_generator_shortfall_when_adequate():
    """Scenario 8: Substantive topic-relevant evidence allows under_target_generator_shortfall when adequate."""
    script = Script(
        script_id="scr_shortfall",
        project_id="proj_habits",
        title="Habit Formation",
        topic="Why do humans form habits, and how does habit formation work?",
        target_duration_minutes=5.0,
        sections=[
            ScriptSection(id="sec_01", type="middle", heading="Body", narration="A brief summary of habits that is far under the target word count.", target_duration_seconds=10),
        ],
    )
    # 3 records with substantive, topic-relevant evidence across 3 distinct sources and queries
    records = [
        EvidenceRecord(
            evidence_id="evi_01",
            project_id="proj_habits",
            source_id="src_01",
            query_id="q_01",
            excerpt=(
                "Cognitive neuroscience identifies the basal ganglia as a primary subcortical structure responsible for human habit automation. "
                "Through repetitive daily practice, discrete behavioral action sequences are chunked into unified motor programs that "
                "execute smoothly and automatically with minimal conscious executive attention from human prefrontal cortical areas."
            ),
        ),
        EvidenceRecord(
            evidence_id="evi_02",
            project_id="proj_habits",
            source_id="src_02",
            query_id="q_02",
            excerpt=(
                "Phasic dopamine release reinforces human habit loop formation by signaling reward prediction errors in response to environmental cues. "
                "This neurochemical reinforcement stabilizes synaptic connections across corticostriatal circuits over multiple behavioral iterations, "
                "strengthening associative automaticity between sensory cues and subsequent motor responses."
            ),
        ),
        EvidenceRecord(
            evidence_id="evi_03",
            project_id="proj_habits",
            source_id="src_03",
            query_id="q_03",
            excerpt=(
                "Behavioral psychology establishes that human habits operate through contextual cues, structured routines, and physiological rewards. "
                "Once habitual automaticity is achieved through conditioning, cues trigger the behavioral routine reliably even when conscious "
                "intentional goal valuation fluctuates or cognitive distraction occurs."
            ),
        ),
    ]

    updated = populate_script_duration_metrics(script=script, evidence_records=records)
    assert updated.duration_compliance is not None
    assert updated.duration_compliance.compliance_status == "under_target_generator_shortfall"
    assert "Adequate substantive research evidence was available" in updated.duration_compliance.assessment_note
    assert "3 substantive records across 3 sources" in updated.duration_compliance.assessment_note


def test_regression_09_source_and_query_diversity_correctly_excludes_disqualified_records():
    """Scenario 9: Source and query diversity metrics correctly exclude disqualified records."""
    script = Script(
        script_id="scr_diversity",
        project_id="proj_habits",
        title="Habit Formation",
        topic="Why do humans form habits, and how does habit formation work?",
        target_duration_minutes=5.0,
        sections=[
            ScriptSection(id="sec_01", type="middle", heading="Body", narration="Short narration text.", target_duration_seconds=10),
        ],
    )
    # 5 records from 5 distinct sources and queries:
    # 2 qualifying, 1 promotional, 1 off-topic, 1 short snippet
    records = [
        EvidenceRecord(
            evidence_id="evi_01",
            project_id="proj_habits",
            source_id="src_01",
            query_id="q_01",
            excerpt=(
                "Cognitive neuroscience identifies the basal ganglia as a primary subcortical structure responsible for human habit automation. "
                "Through repetitive practice, behavioral action sequences are chunked into unified motor programs."
            ),
        ),
        EvidenceRecord(
            evidence_id="evi_02",
            project_id="proj_habits",
            source_id="src_02",
            query_id="q_02",
            excerpt=(
                "Phasic dopamine release reinforces human habit loop formation by signaling reward prediction errors in response to environmental cues. "
                "This neurochemical reinforcement stabilizes synaptic connections across corticostriatal circuits."
            ),
        ),
        EvidenceRecord(
            evidence_id="evi_03",
            project_id="proj_habits",
            source_id="src_03",
            query_id="q_03",
            excerpt="We help early-stage ventures raise capital and secure angel investors for real estate funding with customized pitch decks.",
        ),
        EvidenceRecord(
            evidence_id="evi_04",
            project_id="proj_habits",
            source_id="src_04",
            query_id="q_04",
            excerpt=(
                "In nineteenth-century Norfolk, agricultural innovators popularized the four-field crop rotation system. "
                "By alternating wheat, turnips, barley, and clover across consecutive farming seasons, landowners successfully replenished soil nitrogen."
            ),
        ),
        EvidenceRecord(
            evidence_id="evi_05",
            project_id="proj_habits",
            source_id="src_05",
            query_id="q_05",
            excerpt="Habit loops repeat automatically.",  # < 20 words
        ),
    ]

    updated = populate_script_duration_metrics(script=script, evidence_records=records)
    assert updated.duration_compliance is not None
    # 2 qualifying records across 2 sources and 2 queries, totaling ~60 words (< 100 words) -> evidence deficit (limited coverage)
    assert updated.duration_compliance.compliance_status == "under_target_evidence_deficit"
    # Verification of exclusions documented in note
    note = updated.duration_compliance.assessment_note
    assert "2 substantive record(s) across 2 source(s) and 2 query coverage" in note
    assert "3 non-substantive records excluded" in note
    assert "1 promotional or generic-roadmap record(s) excluded" in note
    assert "1 substantive but off-topic record(s) excluded" in note
    assert "1 insufficient length or empty record(s) excluded" in note


def test_regression_10_existing_duration_statuses_and_factual_readiness_behavior_unchanged_p7_pattern():
    """Scenario 10: Existing duration statuses and factual-readiness behavior remain unchanged (including p7_live_validation_003 pattern)."""
    # Pattern observed in p7_live_validation_003:
    # 210 narration words (< 600 min words), but 2 verified factual claims successfully checked
    narration_210_words = " ".join(["habit"] * 210)
    script = Script(
        script_id="scr_p7_pattern",
        project_id="proj_p7_003",
        title="Habit Formation",
        topic="Why do humans form habits, and how does habit formation work?",
        target_duration_minutes=5.0,
        sections=[
            ScriptSection(id="sec_01", type="hook", heading="Hook", narration=narration_210_words, claim_ids=["clm_01", "clm_02"]),
        ],
        claims=[
            ScriptClaim(claim_id="clm_01", claim_text="The basal ganglia chunks habits into automatic routines.", section_id="sec_01", evidence_ids=["evi_01"]),
            ScriptClaim(claim_id="clm_02", claim_text="Dopamine reinforces habitual cue-routine-reward associations.", section_id="sec_01", evidence_ids=["evi_02"]),
        ],
    )

    records = [
        EvidenceRecord(
            evidence_id="evi_01",
            project_id="proj_p7_003",
            source_id="src_01",
            query_id="q_01",
            excerpt="The basal ganglia chunk behavioral routines into automatic neural action patterns over repeated daily practice.",
        ),
        EvidenceRecord(
            evidence_id="evi_02",
            project_id="proj_p7_003",
            source_id="src_02",
            query_id="q_02",
            excerpt="Dopamine signaling reinforces associative connections between environmental cues, behavioral routines, and expected rewards.",
        ),
    ]

    # Populate duration metrics
    populate_script_duration_metrics(script=script, evidence_records=records)
    assert script.duration_compliance is not None
    # Flagged with deficit below 600 words
    assert script.duration_compliance.deficit_words == 600 - 210

    # Fact check report verifies all claims
    fact_report = ScriptFactCheckReport(
        project_id="proj_p7_003",
        script_version=1,
        assessments=[
            ClaimAssessment(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="The basal ganglia chunks habits into automatic routines.",
                status="supported",
                risk_level="normal",
                explanation="Basal ganglia chunking is supported by evidence excerpt.",
                supporting_evidence_ids=["evi_01"],
                recommended_action="keep",
            ),
            ClaimAssessment(
                claim_id="clm_02",
                section_id="sec_01",
                proposition="Dopamine reinforces habitual cue-routine-reward associations.",
                status="supported",
                risk_level="normal",
                explanation="Dopamine reinforcement is supported by evidence excerpt.",
                supporting_evidence_ids=["evi_02"],
                recommended_action="keep",
            ),
        ],
    )
    fact_report.recompute_summary()

    # Crucial architectural contract: factual readiness succeeds despite duration deficit!
    assert fact_report.is_ready is True
    assert fact_report.requires_repair is False
    assert script.duration_compliance.compliance_status in (
        "under_target_evidence_deficit",
        "under_target_generator_shortfall",
    )


# ===========================================================================
# Workstream 6: Strict Meaningful Relevance Verification (Section 3 Requirements)
# ===========================================================================

def test_relevance_rejects_long_unrelated_paragraph_with_single_incidental_keyword():
    """Case 1: A long unrelated paragraph containing the main topic keyword exactly once

    must be rejected by the relevance gate.
    """
    agile_sprint_text = (
        "Effective office sprint managers encourage regular communication between engineering and marketing teams. "
        "In our quarterly planning review, team leads emphasized that continuous integration pipelines reduce software "
        "deployment friction across agile sprints. Furthermore, cross-functional squads should coordinate backlog grooming "
        "sessions every Tuesday to resolve dependency blockers. Staff should make it a habit to log pull request tickets "
        "in Jira before end of day. Executive leadership remains focused on customer retention metrics and annual revenue targets."
    )
    topic = "Why do humans form habits, and how does habit formation work?"
    query = "how habits form in humans"
    question = "How does habit formation work?"

    score = score_candidate_paragraph(
        paragraph=agile_sprint_text,
        current_heading="Agile Sprint Retrospectives",
        target_question=question,
        query_text=query,
        topic=topic,
    )
    assert score < 0.0

    excerpt, locator = extract_verbatim_evidence(
        content=f"## Agile Sprint Retrospectives\n\n{agile_sprint_text}",
        max_chars=1000,
        query_text=query,
        topic=topic,
        target_question=question,
    )
    assert excerpt is None
    assert locator is None


def test_relevance_rejects_unrelated_body_despite_topic_keyword_in_heading():
    """Case 2: A paragraph whose heading contains a topic keyword but whose body is unrelated

    must not be qualified by the heading alone.
    """
    ergonomic_body = (
        "Ergonomic office furniture significantly mitigates occupational musculoskeletal strain among desk workers. "
        "Adjustable height standing workstations promote active posture shifts during prolonged typing tasks. "
        "Lumbar support cushions maintain spinal alignment, reducing lower back tension over eight-hour shifts. "
        "Proper monitor elevation at eye level prevents cervical neck fatigue and visual discomfort."
    )
    topic = "Why do humans form habits, and how does habit formation work?"
    query = "psychological habit loops and conditioning"
    question = "How do habits form in humans?"

    score = score_candidate_paragraph(
        paragraph=ergonomic_body,
        current_heading="The Science of Habit Formation",
        target_question=question,
        query_text=query,
        topic=topic,
    )
    assert score < 0.0

    excerpt, locator = extract_verbatim_evidence(
        content=f"# The Science of Habit Formation\n\n{ergonomic_body}",
        max_chars=1000,
        query_text=query,
        topic=topic,
        target_question=question,
    )
    assert excerpt is None
    assert locator is None


def test_relevance_rejects_unrelated_paragraph_with_short_prefix_collision():
    """Case 3: An unrelated paragraph containing short prefix collisions (e.g. 'form', 'work')

    from generic context words must be rejected.
    """
    concrete_engineering_text = (
        "The formidable structural formula for reinforced concrete requires precise water-to-cement ratios. "
        "Civil engineers calculate shear load tolerances across expansive bridge spans using finite element modeling. "
        "Modern construction standards mandate continuous compression testing of cured cylindrical core samples "
        "before vertical masonry assemblies proceed."
    )
    topic = "Why do humans form habits, and how does habit formation work?"
    query = "how habits form and how habit loops work"
    question = "How does habit formation work?"

    score = score_candidate_paragraph(
        paragraph=concrete_engineering_text,
        current_heading="Structural Engineering Standards",
        target_question=question,
        query_text=query,
        topic=topic,
    )
    assert score < 0.0

    excerpt, locator = extract_verbatim_evidence(
        content=f"## Structural Engineering Standards\n\n{concrete_engineering_text}",
        max_chars=1000,
        query_text=query,
        topic=topic,
        target_question=question,
    )
    assert excerpt is None
    assert locator is None


def test_relevance_accepts_genuinely_relevant_paragraph_with_multiple_contextual_matches():
    """Case 4: A genuinely relevant paragraph with multiple meaningful contextual matches

    must be accepted with a strong positive score.
    """
    habit_loop_text = (
        "In cognitive psychology, habit formation occurs when behavioral responses become automated through repeated cue exposure. "
        "When an individual encounters a consistent contextual cue, the associated procedural routine executes with minimal deliberative intervention. "
        "Neurological reward signals then reinforce the cognitive association, strengthening the automated behavioral loop over time."
    )
    topic = "Why do humans form habits, and how does habit formation work?"
    query = "psychological cue routine reward loop in habit formation"
    question = "How does the habit loop operate?"

    score = score_candidate_paragraph(
        paragraph=habit_loop_text,
        current_heading="Mechanisms of Habit Formation",
        target_question=question,
        query_text=query,
        topic=topic,
    )
    assert score > 50.0

    excerpt, locator = extract_verbatim_evidence(
        content=f"## Mechanisms of Habit Formation\n\n{habit_loop_text}",
        max_chars=1000,
        query_text=query,
        topic=topic,
        target_question=question,
    )
    assert excerpt is not None
    assert "habit formation occurs" in excerpt
    assert locator == "heading: 'Mechanisms of Habit Formation'"


def test_relevance_accepts_technically_relevant_paragraph_using_query_terminology():
    """Case 5: A technically relevant paragraph using terminology present in planned research queries

    must be preserved and selected.
    """
    technical_neuro_text = (
        "Through persistent conditioning, synaptic plasticity within the striatum stabilizes procedural action sequences "
        "until environmental triggers elicit automatic behavioral responses with negligible supervisory oversight from the cortex."
    )
    topic = "Why do humans form habits, and how does habit formation work?"
    query = "synaptic plasticity procedural automaticity striatum conditioning"
    question = "What neurobiological mechanisms automate human routines?"

    score = score_candidate_paragraph(
        paragraph=technical_neuro_text,
        current_heading="Neurobiology of Action Selection",
        target_question=question,
        query_text=query,
        topic=topic,
    )
    assert score > 50.0

    excerpt, locator = extract_verbatim_evidence(
        content=f"# Neurobiology of Action Selection\n\n{technical_neuro_text}",
        max_chars=1000,
        query_text=query,
        topic=topic,
        target_question=question,
    )
    assert excerpt is not None
    assert "synaptic plasticity within the striatum" in excerpt
    assert locator == "heading: 'Neurobiology of Action Selection'"


def test_duration_metrics_excludes_substantive_weakly_relevant_record():
    """Case 6: A substantive but weakly relevant record (single incidental keyword in unrelated domain)

    must not count toward duration metrics.
    """
    script = Script(
        script_id="scr_weak_rel",
        project_id="proj_habits",
        title="Habit Formation",
        topic="Why do humans form habits, and how does habit formation work?",
        target_duration_minutes=5.0,
        sections=[
            ScriptSection(id="sec_01", type="middle", heading="Body", narration="A brief script far under the target word count.", target_duration_seconds=10),
        ],
    )
    weakly_relevant_record = EvidenceRecord(
        evidence_id="evi_weak_01",
        project_id="proj_habits",
        source_id="src_weak_01",
        query_id="q_weak_01",
        excerpt=(
            "Modern corporations encourage productive operational habits among distributed workforce teams. "
            "However, enterprise productivity depends primarily on clear organizational hierarchy, transparent OKR goal tracking, "
            "and asynchronous communication workflows across distributed remote offices."
        ),
    )

    updated = populate_script_duration_metrics(script=script, evidence_records=[weakly_relevant_record])
    assert updated.duration_compliance is not None
    assert updated.duration_compliance.compliance_status == "under_target_evidence_deficit"
    assert "none contained substantive topic-relevant evidence" in updated.duration_compliance.assessment_note
    assert "1 substantive but off-topic record(s) excluded" in updated.duration_compliance.assessment_note


# ===========================================================================
# Workstream 7: Factual Grounding, Direct Support vs. Conceptual Consistency
# ===========================================================================

def _create_grounding_catalog(evidence_dict):
    sources = {
        f"src_{eid}": SourceRecord(
            source_id=f"src_{eid}",
            project_id="proj_grounding",
            title=f"Source for {eid}",
            url=f"https://example.com/{eid}",
        )
        for eid in evidence_dict
    }
    evidence = {
        eid: EvidenceRecord(
            evidence_id=eid,
            project_id="proj_grounding",
            run_id="run_grounding",
            source_id=f"src_{eid}",
            excerpt=excerpt,
        )
        for eid, excerpt in evidence_dict.items()
    }
    return ValidatedEvidenceCatalog(
        run_id="run_grounding",
        evidence_by_id=evidence,
        sources_by_id=sources,
    )


def test_grounding_rejects_dopamine_claim_from_reward_only_evidence():
    """Test 1: Reward evidence does not establish dopamine. Model assigning supported is rejected."""
    catalog = _create_grounding_catalog({
        "evi_01": "Habit loops are reinforced by positive reward feedback, making the routine repeat over time."
    })
    claim = ScriptClaim(
        claim_id="clm_01",
        claim_text="Dopamine reinforces habitual routines through positive feedback.",
        section_id="sec_01",
        risk_level="normal",
        evidence_ids=["evi_01"],
    )
    # LLM attempts to assign supported relying on background knowledge
    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                status="supported",
                explanation="Reward feedback aligns with dopamine reinforcement.",
                supporting_evidence_ids=["evi_01"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_01",
        project_id="proj_grounding",
        version=1,
        title="Habit Test",
        sections=[ScriptSection(id="sec_01", type="middle", heading="Middle", narration="Narration text.", claim_ids=["clm_01"])],
        claims=[claim],
        research_run_id="run_grounding",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "unsupported"
    assert assessment.supporting_evidence_ids == []
    assert "Factual grounding check failed" in assessment.explanation


def test_grounding_rejects_cognitive_energy_claim_from_habit_loop_evidence():
    """Test 2: Habit-loop evidence does not establish cognitive-energy savings."""
    catalog = _create_grounding_catalog({
        "evi_01": "At the core of habit formation is the habit loop, a three-step process that consists of a cue, a routine, and a reward."
    })
    claim = ScriptClaim(
        claim_id="clm_01",
        claim_text="Habits save cognitive energy by automating daily routines.",
        section_id="sec_01",
        risk_level="high",
        evidence_ids=["evi_01"],
    )
    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                status="supported",
                explanation="The habit loop routine can be understood as automating tasks to save cognitive energy.",
                supporting_evidence_ids=["evi_01"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_01",
        project_id="proj_grounding",
        version=1,
        title="Habit Test",
        sections=[ScriptSection(id="sec_01", type="middle", heading="Middle", narration="Narration text.", claim_ids=["clm_01"])],
        claims=[claim],
        research_run_id="run_grounding",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "unsupported"
    assert assessment.supporting_evidence_ids == []
    assert report.is_ready is False


def test_grounding_rejects_basal_ganglia_claim_from_habit_loop_evidence():
    """Test 3: Habit-loop evidence does not establish basal ganglia involvement."""
    catalog = _create_grounding_catalog({
        "evi_01": "Habits are automatic behaviors formed through repeated practice and reinforced by patterns of reward."
    })
    claim = ScriptClaim(
        claim_id="clm_01",
        claim_text="The basal ganglia coordinate and automate routine actions in the brain.",
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
                explanation="The excerpt describes automatic behaviors which involve the basal ganglia.",
                supporting_evidence_ids=["evi_01"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_01",
        project_id="proj_grounding",
        version=1,
        title="Habit Test",
        sections=[ScriptSection(id="sec_01", type="middle", heading="Middle", narration="Narration text.", claim_ids=["clm_01"])],
        claims=[claim],
        research_run_id="run_grounding",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "unsupported"
    assert assessment.supporting_evidence_ids == []


def test_grounding_accepts_genuine_paraphrase_of_directly_supported_evidence():
    """Test 4: A genuine paraphrase of directly supported evidence remains supported."""
    catalog = _create_grounding_catalog({
        "evi_01": "At the core of habit formation is the habit loop, a three-step process that consists of a cue, a routine, and a reward."
    })
    claim = ScriptClaim(
        claim_id="clm_01",
        claim_text="Habitual behaviors are initiated by cues, executed via regular routines, and reinforced by rewards.",
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
                explanation="The excerpt directly establishes that habit formation consists of cues, routines, and rewards.",
                supporting_evidence_ids=["evi_01"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_01",
        project_id="proj_grounding",
        version=1,
        title="Habit Test",
        sections=[ScriptSection(id="sec_01", type="middle", heading="Middle", narration="Narration text.", claim_ids=["clm_01"])],
        claims=[claim],
        research_run_id="run_grounding",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "supported"
    assert assessment.supporting_evidence_ids == ["evi_01"]


def test_grounding_rejects_supported_for_multipart_claim_with_only_one_part_supported():
    """Test 5: Evidence supporting only one part of a multi-part claim does not produce supported."""
    catalog = _create_grounding_catalog({
        "evi_01": "The habit loop consists of a cue, a routine, and a reward."
    })
    # Multi-part: loop components (supported) + dopamine release (unsupported)
    claim = ScriptClaim(
        claim_id="clm_01",
        claim_text="The habit loop consists of cues and routines, and releases dopamine to reinforce the behavior.",
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
                explanation="The excerpt verifies the cue and routine, and dopamine provides the reward.",
                supporting_evidence_ids=["evi_01"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_01",
        project_id="proj_grounding",
        version=1,
        title="Habit Test",
        sections=[ScriptSection(id="sec_01", type="middle", heading="Middle", narration="Narration text.", claim_ids=["clm_01"])],
        claims=[claim],
        research_run_id="run_grounding",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status in ("unsupported", "partially_supported")
    assert assessment.status != "supported"


def test_grounding_rejects_conceptual_consistency_without_direct_support():
    """Test 6: Conceptual consistency without direct support is rejected when explanation admits non-mention."""
    catalog = _create_grounding_catalog({
        "evi_01": "Rewards provide positive reinforcement that cements the behavioral loop."
    })
    claim = ScriptClaim(
        claim_id="clm_01",
        claim_text="Dopamine release drives the reinforcement of habit loops.",
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
                explanation="While it does not explicitly mention dopamine, the concept of rewarding behaviors being reinforced is consistent with the role of dopamine.",
                supporting_evidence_ids=["evi_01"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_01",
        project_id="proj_grounding",
        version=1,
        title="Habit Test",
        sections=[ScriptSection(id="sec_01", type="middle", heading="Middle", narration="Narration text.", claim_ids=["clm_01"])],
        claims=[claim],
        research_run_id="run_grounding",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "unsupported"
    assert assessment.supporting_evidence_ids == []


def test_grounding_high_risk_unsupported_claim_blocks_readiness():
    """Test 7: A high-risk unsupported claim prevents readiness."""
    catalog = _create_grounding_catalog({
        "evi_01": "The habit loop consists of a cue, a routine, and a reward."
    })
    claim = ScriptClaim(
        claim_id="clm_01",
        claim_text="Habits save cognitive energy by automating routine tasks, allowing us to focus on more complex activities.",
        section_id="sec_01",
        risk_level="high",
        evidence_ids=["evi_01"],
    )
    mock_llm = MockLLMProvider()
    mock_llm.generate_json = lambda prompt, schema: BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                status="supported",
                explanation="The excerpt describes routine automation which relates to cognitive energy.",
                supporting_evidence_ids=["evi_01"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_01",
        project_id="proj_grounding",
        version=1,
        title="Habit Test",
        sections=[ScriptSection(id="sec_01", type="middle", heading="Middle", narration="Narration text.", claim_ids=["clm_01"])],
        claims=[claim],
        research_run_id="run_grounding",
    )
    report = checker.check_script(script, catalog=catalog)
    assert report.is_ready is False
    assert report.has_unresolved_high_risk is True
    assert "1 high-risk claim(s) unsupported" in report.readiness_reason


def test_grounding_corrective_retry_enforces_grounding_rules():
    """Test 8: The corrective retry follows exactly the same grounding rules and recovers when LLM adjusts."""
    catalog = _create_grounding_catalog({
        "evi_01": "Rewards provide positive reinforcement that cements the behavioral loop."
    })
    claim = ScriptClaim(
        claim_id="clm_01",
        claim_text="Dopamine reinforces habit loops.",
        section_id="sec_01",
        risk_level="normal",
        evidence_ids=["evi_01"],
    )
    prompts_seen = []

    def tracking_generate_json(prompt: str, schema):
        prompts_seen.append(prompt)
        if len(prompts_seen) == 1:
            # First attempt: invalid supported status based on background knowledge
            return BatchFactCheckResponsePayload(
                assessments=[
                    ClaimAssessmentPayload(
                        claim_id="clm_01",
                        status="supported",
                        explanation="While not mentioned, dopamine is consistent with rewards.",
                        supporting_evidence_ids=["evi_01"],
                        contradicting_evidence_ids=[],
                        contextual_evidence_ids=[],
                        recommended_action="keep",
                    )
                ]
            )
        else:
            # Retry attempt: LLM corrects its assessment to unsupported based on feedback
            assert "FACTUAL GROUNDING RULES" in prompt
            return BatchFactCheckResponsePayload(
                assessments=[
                    ClaimAssessmentPayload(
                        claim_id="clm_01",
                        status="unsupported",
                        explanation="The excerpt discusses reward reinforcement but makes no mention of dopamine.",
                        supporting_evidence_ids=[],
                        contradicting_evidence_ids=[],
                        contextual_evidence_ids=[],
                        recommended_action="qualify",
                    )
                ]
            )

    mock_llm = MockLLMProvider()
    mock_llm.generate_json = tracking_generate_json
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_01",
        project_id="proj_grounding",
        version=1,
        title="Habit Test",
        sections=[ScriptSection(id="sec_01", type="middle", heading="Middle", narration="Narration text.", claim_ids=["clm_01"])],
        claims=[claim],
        research_run_id="run_grounding",
    )
    report = checker.check_script(script, catalog=catalog)
    assert len(prompts_seen) == 2
    assessment = report.assessments[0]
    assert assessment.status == "unsupported"
    assert assessment.supporting_evidence_ids == []
    assert assessment.recommended_action == "qualify"


def test_grounding_claim_isolation_and_permitted_ids_intact():
    """Test 9: Claim isolation and permitted evidence IDs remain intact."""
    catalog = _create_grounding_catalog({
        "evi_01": "Habit loops are reinforced by rewards.",
        "evi_02": "The habit loop consists of a cue, routine, and reward.",
    })
    claim_1 = ScriptClaim(
        claim_id="clm_01",
        claim_text="Dopamine reinforces habits.",
        section_id="sec_01",
        evidence_ids=["evi_01"],
    )
    claim_2 = ScriptClaim(
        claim_id="clm_02",
        claim_text="Habits consist of cue, routine, and reward.",
        section_id="sec_02",
        evidence_ids=["evi_02"],
    )
    prompts_seen = []

    def tracking_generate_json(prompt: str, schema):
        prompts_seen.append(prompt)
        if "clm_01" in prompt:
            # Claim 1 should see only evi_01
            assert "evi_01" in prompt
            assert "evi_02" not in prompt
            return BatchFactCheckResponsePayload(
                assessments=[
                    ClaimAssessmentPayload(
                        claim_id="clm_01",
                        status="unsupported",
                        explanation="Dopamine is not mentioned in evidence.",
                        supporting_evidence_ids=[],
                        contradicting_evidence_ids=[],
                        contextual_evidence_ids=[],
                        recommended_action="qualify",
                    )
                ]
            )
        elif "clm_02" in prompt:
            # Claim 2 should see only evi_02
            assert "evi_02" in prompt
            assert "evi_01" not in prompt
            return BatchFactCheckResponsePayload(
                assessments=[
                    ClaimAssessmentPayload(
                        claim_id="clm_02",
                        status="supported",
                        explanation="Excerpt directly states habit loop consists of cue, routine, reward.",
                        supporting_evidence_ids=["evi_02"],
                        contradicting_evidence_ids=[],
                        contextual_evidence_ids=[],
                        recommended_action="keep",
                    )
                ]
            )
        raise ValueError(f"Unexpected prompt: {prompt}")

    mock_llm = MockLLMProvider()
    mock_llm.generate_json = tracking_generate_json
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_01",
        project_id="proj_grounding",
        version=1,
        title="Habit Test",
        sections=[
            ScriptSection(id="sec_01", type="middle", heading="Middle", narration="Narration 1.", claim_ids=["clm_01"]),
            ScriptSection(id="sec_02", type="takeaway", heading="Takeaway", narration="Narration 2.", claim_ids=["clm_02"]),
        ],
        claims=[claim_1, claim_2],
        research_run_id="run_grounding",
    )
    report = checker.check_script(script, catalog=catalog)
    assert len(prompts_seen) == 2
    assert report.assessments[0].status == "unsupported"
    assert report.assessments[1].status == "supported"


def test_grounding_preserves_supported_status_without_simplistic_keyword_matching():
    """Test 10: Existing supported claims are not incorrectly rejected due to simplistic keyword matching."""
    catalog = _create_grounding_catalog({
        "evi_01": "Water freezes into crystalline ice at exactly 0 degrees Celsius under standard atmospheric pressure.",
        "evi_02": "Repeated execution of behavioral sequences strengthens synaptic connections, leading to automaticity.",
    })
    claim_a = ScriptClaim(
        claim_id="clm_a",
        claim_text="Water freezes into solid ice at zero degrees Celsius under atmospheric pressure.",
        section_id="sec_01",
        evidence_ids=["evi_01"],
    )
    claim_b = ScriptClaim(
        claim_id="clm_b",
        claim_text="Repetition strengthens neural pathways over time.",
        section_id="sec_02",
        evidence_ids=["evi_02"],
    )

    def generate_json(prompt: str, schema):
        if "clm_a" in prompt:
            return BatchFactCheckResponsePayload(
                assessments=[
                    ClaimAssessmentPayload(
                        claim_id="clm_a",
                        status="supported",
                        explanation="Excerpt explicitly verifies the freezing threshold of water at 0 degrees Celsius.",
                        supporting_evidence_ids=["evi_01"],
                        contradicting_evidence_ids=[],
                        contextual_evidence_ids=[],
                        recommended_action="keep",
                    )
                ]
            )
        elif "clm_b" in prompt:
            return BatchFactCheckResponsePayload(
                assessments=[
                    ClaimAssessmentPayload(
                        claim_id="clm_b",
                        status="supported",
                        explanation="Excerpt confirms that repeated execution strengthens synaptic connections.",
                        supporting_evidence_ids=["evi_02"],
                        contradicting_evidence_ids=[],
                        contextual_evidence_ids=[],
                        recommended_action="keep",
                    )
                ]
            )
        raise ValueError(f"Unexpected prompt: {prompt}")

    mock_llm = MockLLMProvider()
    mock_llm.generate_json = generate_json
    checker = ScriptFactChecker(llm_provider=mock_llm, max_batch_size=1)
    script = Script(
        script_id="scr_01",
        project_id="proj_grounding",
        version=1,
        title="Physical Test",
        sections=[
            ScriptSection(id="sec_01", type="middle", heading="Middle", narration="Narration A.", claim_ids=["clm_a"]),
            ScriptSection(id="sec_02", type="takeaway", heading="Takeaway", narration="Narration B.", claim_ids=["clm_b"]),
        ],
        claims=[claim_a, claim_b],
        research_run_id="run_grounding",
    )
    report = checker.check_script(script, catalog=catalog)
    assert report.assessments[0].status == "supported"
    assert report.assessments[0].supporting_evidence_ids == ["evi_01"]
    assert report.assessments[1].status == "supported"
    assert report.assessments[1].supporting_evidence_ids == ["evi_02"]

