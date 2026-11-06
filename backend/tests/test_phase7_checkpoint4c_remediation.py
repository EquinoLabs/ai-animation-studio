"""Comprehensive regression test suite for Phase 7 Checkpoint 4C Remediation (Fact-Checker Reliability & Script Generation).

Tests Workstream A (Fact-checker prompt schema reliability, 9 tests) and
Workstream B (Script generator narrative architecture & section-by-section generation, 10 tests).
"""

import logging
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
    DEFAULT_WORDS_PER_MINUTE,
    GeneratedScriptPayload,
    GeneratedSectionPayload,
    Script,
    ScriptClaim,
    ScriptClaimPayload,
    ScriptOutline,
    ScriptOutlineSection,
    ScriptSection,
    ScriptSectionPayload,
    count_words,
)
from backend.app.llm.base import LLMResponseMalformedError, LLMUnavailableError
from backend.app.llm.mock import MockLLMProvider
from backend.app.services.project.service import ProjectService
from backend.app.services.research.evidence_brief import EvidenceBriefBuilder
from backend.app.services.research.storage import ResearchStorageService
from backend.app.services.scriptwriter.fact_checker import (
    ScriptFactCheckError,
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
    get_multi_beat_guidance,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_workspace():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def test_settings(temp_workspace: Path):
    projects_dir = temp_workspace / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    return Settings(PROJECTS_DIR=str(projects_dir))


@pytest.fixture
def project_service(test_settings: Settings):
    return ProjectService(projects_root=Path(test_settings.PROJECTS_DIR))


@pytest.fixture
def storage_service(test_settings: Settings):
    return ResearchStorageService(projects_root=Path(test_settings.PROJECTS_DIR), settings=test_settings)


@pytest.fixture
def brief_builder(storage_service: ResearchStorageService, test_settings: Settings):
    return EvidenceBriefBuilder(storage_service=storage_service, settings=test_settings)


def setup_test_project_and_research(
    project_service: ProjectService,
    storage_service: ResearchStorageService,
    project_id: str = "proj_test_cp4c",
    run_id: str = "rrun_01",
    plan_id: str = "rplan_01",
):
    """Helper to set up project, research run, source, and evidence."""
    proj = project_service.create_project(title="Test CP4C Project", project_id=project_id)

    plan = ResearchPlan(
        plan_id=plan_id,
        project_id=project_id,
        topic="Aristotle Theory of Spontaneous Generation",
        research_objective="Investigate Aristotle's biological theories.",
        research_questions=["What did Aristotle propose about spontaneous generation?"],
        planned_queries=[
            PlannedQuery(
                query_id="qry_01",
                query_text="Aristotle spontaneous generation biology",
                purpose="Historical overview of Aristotle's theory",
            )
        ],
    )
    storage_service.save_plan(plan)

    run = ResearchRun(
        run_id=run_id,
        plan_id=plan_id,
        project_id=project_id,
        status="completed",
        query_records=[
            QueryRecord(
                query_id="qry_01",
                plan_id=plan_id,
                run_id=run_id,
                query_text="Aristotle spontaneous generation biology",
                tool_name="web_search",
                status="succeeded",
            )
        ],
    )
    storage_service.save_run(run)

    source = SourceRecord(
        source_id="src_01",
        project_id=project_id,
        url="https://plato.stanford.edu/entries/aristotle-biology/",
        title="Aristotle's Biology - Stanford Encyclopedia of Philosophy",
        source_type="scholarly",
    )
    storage_service.save_source(project_id, source)

    evidence = EvidenceRecord(
        evidence_id="evi_01",
        project_id=project_id,
        source_id="src_01",
        run_id=run_id,
        query_id="qry_01",
        excerpt="Aristotle proposed that some living organisms could arise spontaneously from non-living matter like mud and decaying organic material.",
    )
    storage_service.save_evidence(project_id, evidence)

    return proj, plan, run


def create_test_catalog(
    project_id: str = "proj_test_cp4c",
    run_id: str = "rrun_01",
) -> ValidatedEvidenceCatalog:
    """Helper to create a validated evidence catalog."""
    cat = ValidatedEvidenceCatalog(run_id=run_id)
    source = SourceRecord(
        source_id="src_01",
        project_id=project_id,
        url="https://plato.stanford.edu/entries/aristotle-biology/",
        title="Aristotle's Biology - Stanford Encyclopedia of Philosophy",
        source_type="scholarly",
    )
    evidence = EvidenceRecord(
        evidence_id="evi_01",
        project_id=project_id,
        source_id="src_01",
        run_id=run_id,
        query_id="qry_01",
        excerpt="Aristotle proposed that some living organisms could arise spontaneously from non-living matter like mud and decaying organic material.",
    )
    cat.sources_by_id["src_01"] = source
    cat.evidence_by_id["evi_01"] = evidence
    return cat


def create_test_script(
    project_id: str = "proj_test_cp4c",
    run_id: str = "rrun_01",
) -> Script:
    """Helper to construct a valid in-memory script with claims."""
    sections = [
        ScriptSection(
            id="sec_01",
            type="hook",
            heading="The Spontaneous Life Puzzle",
            narration="For centuries, people believed mud could turn into living frogs.",
            outline_section_index=0,
            claim_ids=["clm_01"],
        ),
        ScriptSection(
            id="sec_02",
            type="setup",
            heading="Aristotle's Ancient World",
            narration="Aristotle formalized spontaneous generation in his biological writings.",
            outline_section_index=1,
            claim_ids=["clm_02"],
        ),
    ]
    claims = [
        ScriptClaim(
            claim_id="clm_01",
            claim_text="For centuries people believed mud could turn into living frogs.",
            section_id="sec_01",
            evidence_ids=["evi_01"],
            source_ids=["src_01"],
            risk_level="creative_flexibility",
            link_status="linked",
        ),
        ScriptClaim(
            claim_id="clm_02",
            claim_text="Aristotle formalised the concept that living organisms arise from non-living matter.",
            section_id="sec_02",
            evidence_ids=["evi_01"],
            source_ids=["src_01"],
            risk_level="normal",
            link_status="linked",
        ),
    ]
    return Script(
        script_id="scr_test_cp4c",
        project_id=project_id,
        research_run_id=run_id,
        title="Spontaneous Generation Explained",
        target_duration_minutes=3.0,
        sections=sections,
        claims=claims,
    )


def create_test_outline(
    project_id: str = "proj_test_cp4c",
    run_id: str = "rrun_01",
    target_words_per_section: int = 140,
) -> ScriptOutline:
    """Helper to create a 5-section outline."""
    sec_types = ["hook", "setup", "middle", "climax", "takeaway"]
    sections = [
        ScriptOutlineSection(
            type=st,
            heading=f"Heading for {st.capitalize()}",
            target_words=target_words_per_section,
            factual_milestones=[f"Milestone {i+1} for {st}"],
            evidence_ids=["evi_01"] if st in ("setup", "middle") else [],
        )
        for i, st in enumerate(sec_types)
    ]
    return ScriptOutline(
        outline_id="out_test_cp4c",
        project_id=project_id,
        topic="Aristotle Theory of Spontaneous Generation",
        target_duration_minutes=5.0,
        narrative_structure="chronological",
        sections=sections,
        research_run_id=run_id,
    )


# ===========================================================================
# WORKSTREAM A: Fact-Checker Prompt and Schema Reliability (Tests 1-9)
# ===========================================================================

def test_fact_checker_schema_in_original_prompt(test_settings):
    """Test 1: Complete JSON schema is present in original batch prompt."""
    llm = MockLLMProvider()
    checker = ScriptFactChecker(llm_provider=llm, settings=test_settings)
    catalog = create_test_catalog()
    script = create_test_script()

    sections_by_id = {s.id: s for s in script.sections}
    claim_supplied_eids = {c.claim_id: set(c.evidence_ids) for c in script.claims}
    prompt = checker._build_batch_prompt(script.claims, sections_by_id, catalog, claim_supplied_eids)

    assert '"assessments": [' in prompt
    assert '"claim_id":' in prompt
    assert '"status":' in prompt
    assert '"explanation":' in prompt
    assert '"supporting_evidence_ids":' in prompt
    assert '"contradicting_evidence_ids":' in prompt
    assert '"contextual_evidence_ids":' in prompt
    assert '"recommended_action":' in prompt


def test_fact_checker_schema_in_retry_prompt(test_settings):
    """Test 2: Complete JSON schema is present in corrective retry prompt."""
    llm = MockLLMProvider()
    checker = ScriptFactChecker(llm_provider=llm, settings=test_settings)
    catalog = create_test_catalog()
    script = create_test_script()

    # Queue a malformed response then check prompt sent in retry
    raw_bad_json = '{"assessments": [{"claim_id": "clm_01", "recommended_action": "keep"}]}'
    llm.queue_response(raw_bad_json)
    # Queue a valid response for retry
    valid_resp = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                status="supported",
                explanation="Valid explanation.",
                supporting_evidence_ids=["evi_01"],
                recommended_action="keep",
            ),
            ClaimAssessmentPayload(
                claim_id="clm_02",
                status="supported",
                explanation="Valid explanation 2.",
                supporting_evidence_ids=["evi_01"],
                recommended_action="keep",
            ),
        ]
    )
    llm.queue_response(valid_resp)

    checker.check_script(script=script, catalog=catalog)

    assert len(llm.calls) >= 2
    retry_call = llm.calls[1]
    retry_prompt = retry_call["prompt"]

    assert "CORRECTION REQUIRED" in retry_prompt
    assert '"assessments": [' in retry_prompt
    assert '"claim_id":' in retry_prompt
    assert '"status":' in retry_prompt
    assert '"explanation":' in retry_prompt
    assert '"supporting_evidence_ids":' in retry_prompt


def test_fact_checker_all_seven_field_definitions_in_prompt(test_settings):
    """Test 3: Explicit field definitions are present in prompt (all 7 fields explained)."""
    llm = MockLLMProvider()
    checker = ScriptFactChecker(llm_provider=llm, settings=test_settings)
    catalog = create_test_catalog()
    script = create_test_script()

    sections_by_id = {s.id: s for s in script.sections}
    claim_supplied_eids = {c.claim_id: set(c.evidence_ids) for c in script.claims}
    prompt = checker._build_batch_prompt(script.claims, sections_by_id, catalog, claim_supplied_eids)

    assert "1. 'claim_id'" in prompt
    assert "2. 'status'" in prompt
    assert "3. 'explanation'" in prompt
    assert "4. 'supporting_evidence_ids'" in prompt
    assert "5. 'contradicting_evidence_ids'" in prompt
    assert "6. 'contextual_evidence_ids'" in prompt
    assert "7. 'recommended_action'" in prompt


def test_fact_checker_prompt_status_is_mandatory(test_settings):
    """Test 4: Prompt explicitly states that status is mandatory and cannot be omitted."""
    llm = MockLLMProvider()
    checker = ScriptFactChecker(llm_provider=llm, settings=test_settings)
    catalog = create_test_catalog()
    script = create_test_script()

    sections_by_id = {s.id: s for s in script.sections}
    claim_supplied_eids = {c.claim_id: set(c.evidence_ids) for c in script.claims}
    prompt = checker._build_batch_prompt(script.claims, sections_by_id, catalog, claim_supplied_eids)

    assert "'status' is mandatory on every entry. Never omit this field." in prompt
    assert "'status' (string, required): MUST be exactly one of:" in prompt


def test_fact_checker_prompt_status_example_is_illustrative(test_settings):
    """Test 5: Prompt explicitly states that status example is illustrative, not prescriptive."""
    llm = MockLLMProvider()
    checker = ScriptFactChecker(llm_provider=llm, settings=test_settings)
    catalog = create_test_catalog()
    script = create_test_script()

    sections_by_id = {s.id: s for s in script.sections}
    claim_supplied_eids = {c.claim_id: set(c.evidence_ids) for c in script.claims}
    prompt = checker._build_batch_prompt(script.claims, sections_by_id, catalog, claim_supplied_eids)

    assert "The illustrative schema example above shows 'supported'" in prompt


def test_fact_checker_retry_prompt_includes_error_context_and_missing_fields(test_settings):
    """Test 6: Corrective retry prompt includes previous error context and missing field names."""
    llm = MockLLMProvider()
    checker = ScriptFactChecker(llm_provider=llm, settings=test_settings)
    catalog = create_test_catalog()
    script = create_test_script()

    # Queue missing status
    bad_output = '{"assessments": [{"claim_id": "clm_01", "recommended_action": "keep", "explanation": "test"}]}'
    llm.queue_response(bad_output)
    # Queue valid fallback
    valid_resp = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                status="supported",
                explanation="Valid explanation.",
                supporting_evidence_ids=["evi_01"],
                recommended_action="keep",
            ),
            ClaimAssessmentPayload(
                claim_id="clm_02",
                status="unsupported",
                explanation="Valid explanation.",
                recommended_action="qualify",
            ),
        ]
    )
    llm.queue_response(valid_resp)

    checker.check_script(script=script, catalog=catalog)

    assert len(llm.calls) == 2
    retry_prompt = llm.calls[1]["prompt"]
    assert "ERROR:" in retry_prompt
    assert "status" in retry_prompt


def test_fact_checker_retry_succeeds_on_second_attempt(test_settings):
    """Test 7: Corrective retry succeeds when model produces valid JSON on second attempt."""
    llm = MockLLMProvider()
    checker = ScriptFactChecker(llm_provider=llm, settings=test_settings)
    catalog = create_test_catalog()
    script = create_test_script()

    # Attempt 1: malformed/missing status
    llm.queue_response('{"assessments": [{"claim_id": "clm_01", "explanation": "test"}]}')
    # Attempt 2: valid payload
    llm.queue_response(
        BatchFactCheckResponsePayload(
            assessments=[
                ClaimAssessmentPayload(
                    claim_id="clm_01",
                    status="supported",
                    explanation="Supported by research evidence.",
                    supporting_evidence_ids=["evi_01"],
                    recommended_action="keep",
                ),
                ClaimAssessmentPayload(
                    claim_id="clm_02",
                    status="supported",
                    explanation="Supported by historical writings.",
                    supporting_evidence_ids=["evi_01"],
                    recommended_action="keep",
                ),
            ]
        )
    )

    report = checker.check_script(script=script, catalog=catalog)

    assert report.total_claims == 2
    assert report.supported_count == 2


def test_fact_checker_retry_fails_gracefully_to_not_checked(test_settings):
    """Test 8: Corrective retry fails gracefully to not_checked when second attempt also fails."""
    llm = MockLLMProvider()
    checker = ScriptFactChecker(llm_provider=llm, settings=test_settings)
    catalog = create_test_catalog()
    script = create_test_script()

    # Attempt 1: missing status
    llm.queue_response('{"assessments": [{"claim_id": "clm_01", "explanation": "test"}]}')
    # Attempt 2: still missing status
    llm.queue_response('{"assessments": [{"claim_id": "clm_01", "explanation": "retry still broken"}]}')

    report = checker.check_script(script=script, catalog=catalog)

    assert report.total_claims == 2
    assert report.not_checked_count == 2
    for a in report.assessments:
        assert a.status == "not_checked"
        assert "failed" in a.explanation.lower()


def test_fact_checker_diagnostic_log_captures_snippet(test_settings, caplog):
    """Test 9: Diagnostic log captures sanitized response snippet on schema failure."""
    llm = MockLLMProvider()
    checker = ScriptFactChecker(llm_provider=llm, settings=test_settings)
    catalog = create_test_catalog()
    script = create_test_script()

    # Missing status field so Pydantic fails validation inside generate_json
    malformed_json_snippet = '{"assessments": [{"claim_id": "clm_01", "explanation": "test"}]}'
    llm.queue_response(malformed_json_snippet)
    # Queue valid retry so it finishes
    llm.queue_response(
        BatchFactCheckResponsePayload(
            assessments=[
                ClaimAssessmentPayload(
                    claim_id="clm_01",
                    status="supported",
                    explanation="Ok",
                    supporting_evidence_ids=["evi_01"],
                    recommended_action="keep",
                ),
                ClaimAssessmentPayload(
                    claim_id="clm_02",
                    status="supported",
                    explanation="Ok",
                    supporting_evidence_ids=["evi_01"],
                    recommended_action="keep",
                ),
            ]
        )
    )

    with caplog.at_level(logging.WARNING):
        checker.check_script(script=script, catalog=catalog)

    assert any("Diagnostic snippet:" in record.message for record in caplog.records)


# ===========================================================================
# WORKSTREAM B: Script Generator Narrative Architecture & Section-by-Section (Tests 10-19)
# ===========================================================================

def test_multi_beat_guidance_produces_two_to_four_milestones():
    """Test 10: Multi-beat guidance produces 2-4 milestones per section role and target length."""
    # Hook
    hook_beats = get_multi_beat_guidance("hook", 85, "chronological")
    assert 2 <= len(hook_beats) <= 4

    # Setup
    setup_beats = get_multi_beat_guidance("setup", 140, "mechanism_breakdown")
    assert 2 <= len(setup_beats) <= 4

    # Middle (>=200 words)
    middle_long_beats = get_multi_beat_guidance("middle", 250, "chronological")
    assert len(middle_long_beats) >= 3

    # Climax
    climax_beats = get_multi_beat_guidance("climax", 140, "problem_solution")
    assert 2 <= len(climax_beats) <= 4

    # Takeaway
    takeaway_beats = get_multi_beat_guidance("takeaway", 85, "chronological")
    assert 2 <= len(takeaway_beats) <= 4


def test_outline_prompt_includes_multi_beat_requirements():
    """Test 11: Outline prompt includes multi-beat requirements."""
    builder = ScriptPromptBuilder(policy=WritingPolicy())
    prompt = builder.build_outline_prompt(
        topic="Aristotle Spontaneous Generation",
        target_duration_minutes=5.0,
    )

    assert "Multi-Beat Narrative Outline Requirements:" in prompt
    assert "2–4 concrete factual milestones / narrative beats" in prompt
    assert "at least 3 distinct mechanical steps, causal transitions, or sequential developments" in prompt


def test_section_by_section_produces_all_outline_sections(project_service, storage_service, test_settings):
    """Test 12: Section-by-section generation produces all outline sections."""
    project_id = "proj_sec_all"
    run_id = "rrun_sec_all"
    setup_test_project_and_research(project_service, storage_service, project_id=project_id, run_id=run_id)

    outline = create_test_outline(project_id=project_id, run_id=run_id)
    assert len(outline.sections) == 5

    llm = MockLLMProvider()
    generator = ScriptDraftGenerator(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    script = generator.generate_draft(
        DraftGeneratorInput(
            project_id=project_id,
            outline=outline,
            generation_mode="section_by_section",
        )
    )

    assert len(script.sections) == 5
    for idx, (sec, out_sec) in enumerate(zip(script.sections, outline.sections)):
        assert sec.type == out_sec.type
        assert sec.outline_section_index == idx
        assert sec.heading == out_sec.heading
        assert len(sec.narration.strip()) > 0


def test_section_by_section_preserves_sequential_claim_ids(project_service, storage_service, test_settings):
    """Test 13: Section-by-section preserves sequential claim IDs across sections."""
    project_id = "proj_claims_seq"
    run_id = "rrun_claims_seq"
    setup_test_project_and_research(project_service, storage_service, project_id=project_id, run_id=run_id)

    outline = create_test_outline(project_id=project_id, run_id=run_id)

    llm = MockLLMProvider()
    generator = ScriptDraftGenerator(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    script = generator.generate_draft(
        DraftGeneratorInput(
            project_id=project_id,
            outline=outline,
            generation_mode="section_by_section",
        )
    )

    assert len(script.claims) > 0
    expected_ids = [f"clm_{i+1:02d}" for i in range(len(script.claims))]
    actual_ids = [c.claim_id for c in script.claims]
    assert actual_ids == expected_ids


def test_section_by_section_includes_preceding_context_for_continuity(project_service, storage_service, test_settings):
    """Test 14: Section-by-section includes preceding section context for continuity."""
    project_id = "proj_context_cont"
    run_id = "rrun_context_cont"
    setup_test_project_and_research(project_service, storage_service, project_id=project_id, run_id=run_id)

    outline = create_test_outline(project_id=project_id, run_id=run_id)

    llm = MockLLMProvider()
    generator = ScriptDraftGenerator(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    generator.generate_draft(
        DraftGeneratorInput(
            project_id=project_id,
            outline=outline,
            generation_mode="section_by_section",
        )
    )

    section_calls = [c for c in llm.calls if c.get("schema") == "GeneratedSectionPayload"]
    assert len(section_calls) == 5

    # First call (section 1) has no preceding sections
    assert "--- NARRATIVE CONTINUITY & CONTEXT ---" not in section_calls[0]["prompt"]

    # Second call (section 2) must reference section 1
    assert "--- NARRATIVE CONTINUITY & CONTEXT ---" in section_calls[1]["prompt"]
    assert "Heading for Hook" in section_calls[1]["prompt"]


def test_section_prompt_includes_target_words_and_milestones():
    """Test 15: Section prompt includes target word count and multi-beat milestones."""
    builder = ScriptPromptBuilder(policy=WritingPolicy())
    prompt = builder.build_single_section_prompt(
        topic="Spontaneous Generation",
        target_duration_minutes=5.0,
        section_index=2,
        total_sections=5,
        section_type="middle",
        heading="The Mud Experiment",
        target_words=250,
        factual_milestones=["Milestone A: Observation of tadpoles", "Milestone B: Formation in mud"],
        visual_concept="Mud macro shot",
        permitted_evidence_ids=["evi_01"],
    )

    assert "Target Word Count: ~250 spoken words" in prompt
    assert "* Milestone A: Observation of tadpoles" in prompt
    assert "* Milestone B: Formation in mud" in prompt
    assert "Section Role: \"middle\"" in prompt
    assert "The Mud Experiment" in prompt


def test_bounded_section_expansion_triggers_when_under_threshold(project_service, storage_service, test_settings):
    """Test 16: Bounded section expansion triggers when narration is under 50% target and <60 words."""
    project_id = "proj_exp_trigger"
    run_id = "rrun_exp_trigger"
    setup_test_project_and_research(project_service, storage_service, project_id=project_id, run_id=run_id)

    outline = create_test_outline(project_id=project_id, run_id=run_id, target_words_per_section=150)

    llm = MockLLMProvider()

    # Queue an initial short section (13 words < 75 words and < 60 words) for Section 1
    short_section = GeneratedSectionPayload(
        heading="Short Hook",
        narration="This is an extremely short hook with only twelve words to trigger expansion now.",
        claims=[],
    )
    # Queue expanded section (35 words)
    expanded_section = GeneratedSectionPayload(
        heading="Expanded Hook",
        narration="This is an expanded hook that explains the phenomenon in rich detail with multiple sentences discussing the core dilemma and historical context thoroughly so that it exceeds the original length.",
        claims=[],
    )
    llm.queue_response(short_section)
    llm.queue_response(expanded_section)

    generator = ScriptDraftGenerator(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    script = generator.generate_draft(
        DraftGeneratorInput(
            project_id=project_id,
            outline=outline,
            generation_mode="section_by_section",
        )
    )

    assert "expanded hook" in script.sections[0].narration.lower()
    assert count_words(script.sections[0].narration) > 15


def test_bounded_section_expansion_does_not_trigger_when_meeting_threshold(project_service, storage_service, test_settings):
    """Test 17: Bounded section expansion does not trigger when narration meets threshold."""
    project_id = "proj_no_exp"
    run_id = "rrun_no_exp"
    setup_test_project_and_research(project_service, storage_service, project_id=project_id, run_id=run_id)

    outline = create_test_outline(project_id=project_id, run_id=run_id, target_words_per_section=100)

    llm = MockLLMProvider()

    generator = ScriptDraftGenerator(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    generator.generate_draft(
        DraftGeneratorInput(
            project_id=project_id,
            outline=outline,
            generation_mode="section_by_section",
        )
    )

    expansion_prompts = [c for c in llm.calls if "expanding an underdeveloped script section" in c.get("prompt", "").lower()]
    assert len(expansion_prompts) == 0


def test_bounded_section_expansion_limited_to_one_attempt(project_service, storage_service, test_settings):
    """Test 18: Bounded section expansion is limited to exactly one attempt per section even if still short."""
    project_id = "proj_exp_limit"
    run_id = "rrun_exp_limit"
    setup_test_project_and_research(project_service, storage_service, project_id=project_id, run_id=run_id)

    outline = create_test_outline(project_id=project_id, run_id=run_id, target_words_per_section=150)

    llm = MockLLMProvider()

    # Section 1 initial: short (10 words)
    llm.queue_response(
        GeneratedSectionPayload(
            heading="Very Short Hook",
            narration="One two three four five six seven eight nine ten.",
            claims=[],
        )
    )
    # Section 1 expansion attempt: still short (15 words > 10 words, so it replaces, but still < 60)
    llm.queue_response(
        GeneratedSectionPayload(
            heading="Still Short Hook",
            narration="One two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen.",
            claims=[],
        )
    )

    generator = ScriptDraftGenerator(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    script = generator.generate_draft(
        DraftGeneratorInput(
            project_id=project_id,
            outline=outline,
            generation_mode="section_by_section",
        )
    )

    sec1_expansion_calls = [
        c for c in llm.calls
        if "expanding an underdeveloped script section" in c.get("prompt", "").lower()
        and "Very Short Hook" in c.get("prompt", "")
    ]
    assert len(sec1_expansion_calls) == 1


def test_monolithic_generation_mode_remains_functional(project_service, storage_service, test_settings):
    """Test 19: Monolithic generation mode remains functional when explicitly selected."""
    project_id = "proj_mono"
    run_id = "rrun_mono"
    setup_test_project_and_research(project_service, storage_service, project_id=project_id, run_id=run_id)

    outline = create_test_outline(project_id=project_id, run_id=run_id)

    llm = MockLLMProvider()
    generator = ScriptDraftGenerator(
        llm_provider=llm,
        research_storage=storage_service,
        project_service=project_service,
        settings=test_settings,
    )

    script = generator.generate_draft(
        DraftGeneratorInput(
            project_id=project_id,
            outline=outline,
            generation_mode="monolithic",
        )
    )

    assert len(script.sections) == 5
    script_payload_calls = [c for c in llm.calls if c.get("schema") == "GeneratedScriptPayload"]
    assert len(script_payload_calls) >= 1
