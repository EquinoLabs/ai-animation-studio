"""Regression tests for Phase 7 Checkpoint 4B audit remediation.

Covers:
1. Evidence extraction quality (cookie/bot gate rejection, navigation chrome rejection,
   byline filtering, valid prose acceptance, and no evidence records for rejected content).
2. Claim grounding integrity (cookie/chrome cannot support claims, title inference blocked,
   safe fallback for invalid citations, preserved repair limits, readiness protection).
3. Research coverage (deterministic round-robin extraction, deduplication, provenance, budget limits).
4. Duration compliance (compliant scripts, deficit flagging, deficit vs shortfall distinction,
   independence from factual readiness).
"""

from datetime import datetime, timezone
import pytest
from unittest.mock import MagicMock

from backend.app.domain.contracts import (
    DurationComplianceReport,
    Script,
    ScriptClaim,
    ScriptSection,
    populate_script_duration_metrics,
)
from backend.app.domain.fact_check import ClaimAssessment, ScriptFactCheckReport
from backend.app.domain.research import (
    EvidenceRecord,
    PlannedQuery,
    ResearchBudgetConstraints,
    ResearchPlan,
    ResearchRun,
    SourceRecord,
    utc_now,
)
from backend.app.services.project.service import ProjectService
from backend.app.services.research.orchestrator import (
    ResearchOrchestrator,
    extract_verbatim_evidence,
    is_challenge_or_gatekeeper,
    is_navigation_or_chrome,
    is_byline_or_metadata,
)
from backend.app.services.research.storage import ResearchStorageService
from backend.app.services.research.tools.base import (
    ResearchExecutionSession,
    ResearchTool,
    ToolRegistry,
    ToolResult,
)
from backend.app.services.scriptwriter.fact_checker import (
    BatchFactCheckResponsePayload,
    ClaimAssessmentPayload,
    ScriptFactCheckMalformedError,
    ScriptFactChecker,
)
from backend.app.services.scriptwriter.outliner import ValidatedEvidenceCatalog


# ==============================================================================
# 1. Evidence Quality Tests
# ==============================================================================

def test_extract_verbatim_evidence_rejects_cookie_gate() -> None:
    cookie_content = """# Cookies must be enabled

Enable cookies for pmc.ncbi.nlm.nih.gov and reload this page to continue.
"""
    excerpt, locator = extract_verbatim_evidence(cookie_content)
    assert excerpt is None
    assert locator is None
    assert is_challenge_or_gatekeeper(cookie_content) is True


def test_extract_verbatim_evidence_rejects_bot_challenge_and_access_denied() -> None:
    cloudflare_block = """# 403 Forbidden - Access Denied

Attention Required! | Cloudflare
Please verify you are a human before accessing this website.
Ray ID: 7a8b9c0d1e2f
"""
    excerpt, locator = extract_verbatim_evidence(cloudflare_block)
    assert excerpt is None
    assert locator is None
    assert is_challenge_or_gatekeeper(cloudflare_block) is True


def test_extract_verbatim_evidence_rejects_navigation_chrome() -> None:
    nav_content = """# Main Menu

[Ask the Chatbot](https://example.com/chatbot) [Games & Quizzes](https://example.com/quiz)
[History & Society](https://example.com/history) [Science & Tech](https://example.com/science)
[Biographies](https://example.com/bio) [Animals & Nature](https://example.com/nature)
[Geography & Travel](https://example.com/geo) [Arts & Culture](https://example.com/arts)

- [Home](https://example.com)
- [About Us](https://example.com/about)
- [Contact](https://example.com/contact)
- [Privacy Policy](https://example.com/privacy)
"""
    excerpt, locator = extract_verbatim_evidence(nav_content)
    assert excerpt is None
    assert locator is None


def test_extract_verbatim_evidence_rejects_byline_metadata() -> None:
    byline_content = """# The Evolution of Psychological Thought

Tracing the Historical Development of Theories in Psychology

![Author Photo](https://example.com/images/author.jpg)

Sarah Lee
AI generatedLlama-4-Maverick-17B-128E-Instruct-FP8 5 min read May 24, 2025

149 views
"""
    excerpt, locator = extract_verbatim_evidence(byline_content)
    assert excerpt is None
    assert locator is None


def test_extract_verbatim_evidence_accepts_valid_long_form_prose() -> None:
    full_article = """# Human Behavior

[Ask the Chatbot](https://example.com/chat) [Games](https://example.com/games)

## Introduction

Disciplines such as psychology, sociology, and anthropology study human actions, often seeking to generalize about human behavior in society. Organizational culture, with its established assumptions, also exerts a strong influence on behavior, often surpassing the impact of individual preferences. Emotions, shaped by culture, thoughts, and experiences, significantly impact behavior, and can be both rational and irrational.

## Footer Links
- [Privacy](https://example.com/privacy)
"""
    excerpt, locator = extract_verbatim_evidence(full_article)
    assert excerpt is not None
    assert "Disciplines such as psychology" in excerpt
    assert "Emotions, shaped by culture" in excerpt
    assert locator == "heading: 'Introduction'"
    assert "Ask the Chatbot" not in excerpt


def test_extract_verbatim_evidence_accepts_concise_substantive_definitions() -> None:
    short_def = """# Definition

A habit is an acquired pattern of behavior that is repeated until it becomes automatic and involuntary.
"""
    excerpt, locator = extract_verbatim_evidence(short_def)
    assert excerpt is not None
    assert excerpt == "A habit is an acquired pattern of behavior that is repeated until it becomes automatic and involuntary."
    assert locator == "heading: 'Definition'"


# ==============================================================================
# 2. Claim Grounding Integrity Tests
# ==============================================================================

class MockLLMForRemediation:
    def __init__(self, response_payload: BatchFactCheckResponsePayload) -> None:
        self.response_payload = response_payload
        self.call_count = 0

    def generate_json(self, prompt: str, schema: type) -> BatchFactCheckResponsePayload:
        self.call_count += 1
        return self.response_payload


def _create_test_catalog(excerpt: str, title: str = "Test Source") -> ValidatedEvidenceCatalog:
    catalog = ValidatedEvidenceCatalog(run_id="run_test_01")
    src = SourceRecord(
        source_id="src_01",
        project_id="proj_01",
        url="https://example.com/article",
        title=title,
        retrieval_status="extracted",
    )
    ev = EvidenceRecord(
        evidence_id="evi_01",
        project_id="proj_01",
        source_id="src_01",
        run_id="run_test_01",
        excerpt=excerpt,
        locator="heading: 'Test'",
        status="retrieved",
    )
    catalog.sources_by_id["src_01"] = src
    catalog.evidence_by_id["evi_01"] = ev
    return catalog


def test_claim_citing_cookie_notice_cannot_be_supported() -> None:
    # Catalog contains cookie gate excerpt
    catalog = _create_test_catalog("Enable cookies for pmc.ncbi.nlm.nih.gov and reload this page to continue.")

    # Model attempts to return 'supported' citing the cookie excerpt
    mock_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="Aristotle believed virtues were formed through habit.",
                status="supported",
                risk_level="normal",
                explanation="The evidence supports that Aristotle formed virtues through habit.",
                supporting_evidence_ids=["evi_01"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )

    llm = MockLLMForRemediation(mock_payload)
    checker = ScriptFactChecker(llm_provider=llm)

    script = Script(
        script_id="scr_01",
        project_id="proj_01",
        version=1,
        title="Test Script",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Aristotle believed virtues were habits.", claim_ids=["clm_01"])],
        claims=[ScriptClaim(claim_id="clm_01", claim_text="Aristotle believed virtues were habits.", section_id="sec_01", evidence_ids=["evi_01"])],
        research_run_id="run_test_01",
    )

    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]

    # Must be safely downgraded from supported to unsupported
    assert assessment.status == "unsupported"
    assert assessment.supporting_evidence_ids == []
    assert "boilerplate/gatekeeper" in assessment.explanation


def test_source_title_alone_cannot_establish_support_with_zero_overlap() -> None:
    # Source title is relevant, but excerpt is unrelated
    catalog = _create_test_catalog(
        excerpt="The weather in July was exceptionally sunny across southern regions.",
        title="The Aristotelian Conception of Habit in Human Neuroscience",
    )

    # Model attempts to return supported citing title in explanation
    mock_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="Aristotle developed foundational theories of habit formation.",
                status="supported",
                risk_level="normal",
                explanation="The source 'The Aristotelian Conception of Habit in Human Neuroscience' supports Aristotle theories.",
                supporting_evidence_ids=["evi_01"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )

    llm = MockLLMForRemediation(mock_payload)
    checker = ScriptFactChecker(llm_provider=llm)

    # Validation should detect title inference with zero excerpt overlap and trigger corrective retry
    is_valid, err_msg = checker._validate_batch_response(
        payload=mock_payload,
        batch_claims=[ScriptClaim(claim_id="clm_01", claim_text="Aristotle developed foundational theories of habit formation.", section_id="sec_01", evidence_ids=["evi_01"])],
        catalog=catalog,
        claim_supplied_eids={"clm_01": {"evi_01"}},
    )
    assert is_valid is False
    assert "zero lexical overlap" in (err_msg or "")


def test_valid_paraphrased_support_is_not_rejected() -> None:
    # Excerpt contains real article text that conceptually substantiates the claim
    catalog = _create_test_catalog(
        excerpt="Ancient Greek philosophy laid the foundation for habit psychology through Aristotle's concept of repeated moral actions.",
        title="Evolution of Psychological Thought",
    )

    valid_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="Aristotle believed virtues were formed through repeated actions.",
                status="supported",
                risk_level="normal",
                explanation="Excerpt confirms Aristotle's concept of repeated moral actions in Greek philosophy.",
                supporting_evidence_ids=["evi_01"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )

    llm = MockLLMForRemediation(valid_payload)
    checker = ScriptFactChecker(llm_provider=llm)

    is_valid, err_msg = checker._validate_batch_response(
        payload=valid_payload,
        batch_claims=[ScriptClaim(claim_id="clm_01", claim_text="Aristotle believed virtues were formed through repeated actions.", section_id="sec_01", evidence_ids=["evi_01"])],
        catalog=catalog,
        claim_supplied_eids={"clm_01": {"evi_01"}},
    )
    assert is_valid is True
    assert err_msg is None


def test_high_risk_unresolved_claim_blocks_readiness() -> None:
    report = ScriptFactCheckReport(
        project_id="proj_01",
        script_version=1,
        research_run_id="run_01",
        assessments=[
            ClaimAssessment(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="Over 80% of human decisions are controlled by the basal ganglia.",
                status="unsupported",
                risk_level="high",
                explanation="No statistical evidence found.",
                supporting_evidence_ids=[],
                recommended_action="remove",
                assessed_at=utc_now(),
            )
        ],
    )
    report.recompute_summary()
    assert report.has_unresolved_high_risk is True
    assert report.is_ready is False
    assert "high-risk claim(s) unsupported" in report.readiness_reason


# ==============================================================================
# 3. Research Coverage & Round-Robin Scheduling Tests
# ==============================================================================

from backend.app.services.research.tools.mock import (
    MockExtractTool,
    MockSearchTool,
    SearchResultItem,
)


def test_round_robin_extraction_allocates_across_queries(tmp_path) -> None:
    proj_service = ProjectService(projects_root=tmp_path)
    proj_service.create_project("Habit Formation", project_id="p_cov_01")
    storage = ResearchStorageService(projects_root=tmp_path)

    # Plan with 3 queries
    plan = ResearchPlan(
        plan_id="plan_01",
        project_id="p_cov_01",
        topic="Habit Formation",
        research_objective="Investigate habit formation",
        research_questions=["Historical roots", "Neurological basis", "Psychological loops"],
        planned_queries=[
            PlannedQuery(query_id="q_01", query_text="query 1 historical", purpose="Historical roots", target_question="Historical roots", tool_category="web_search"),
            PlannedQuery(query_id="q_02", query_text="query 2 neurological", purpose="Neurological basis", target_question="Neurological basis", tool_category="web_search"),
            PlannedQuery(query_id="q_03", query_text="query 3 psychological", purpose="Psychological loops", target_question="Psychological loops", tool_category="web_search"),
        ],
        budget_limits=ResearchBudgetConstraints(
            max_search_calls=3,
            max_extract_calls=3,
        ),
    )
    storage.save_plan(plan)

    # Query 1 returns S1_1, S1_2
    # Query 2 returns S2_1, S2_2
    # Query 3 returns S3_1, S3_2
    search_data = {
        "query 1 historical": [{"url": "https://example.com/q1_s1"}, {"url": "https://example.com/q1_s2"}],
        "query 2 neurological": [{"url": "https://example.com/q2_s1"}, {"url": "https://example.com/q2_s2"}],
        "query 3 psychological": [{"url": "https://example.com/q3_s1"}, {"url": "https://example.com/q3_s2"}],
    }
    extract_data = {
        "https://example.com/q1_s1": "# Q1 Content\n\nSubstantive historical analysis of habit development in ancient Greece showed virtues were formed by repetition.",
        "https://example.com/q2_s1": "# Q2 Content\n\nNeurological pathways in the basal ganglia regulate automatic motor behaviors through repeated synaptic strengthening.",
        "https://example.com/q3_s1": "# Q3 Content\n\nPsychological cue-routine-reward loops govern daily behavioral formation and automatic response patterns.",
    }

    extracted_urls: list[str] = []

    def search_handler(args, session):
        items = search_data.get(args.query, [])
        return [SearchResultItem(url=it["url"], title="Title", snippet="Snippet") for it in items]

    def extract_handler(args, session):
        extracted_urls.append(args.url)
        return extract_data.get(args.url, "# Default\n\nSubstantive content with complete sentences.")

    registry = ToolRegistry()
    search_tool = MockSearchTool(name="tavily_search", custom_handler=search_handler)
    extract_tool = MockExtractTool(name="firecrawl_extract", custom_handler=extract_handler, check_ssrf=False)
    registry.register(search_tool)
    registry.register(extract_tool)

    orchestrator = ResearchOrchestrator(storage_service=storage, tool_registry=registry)
    run = orchestrator.execute_plan(project_id="p_cov_01", plan_id="plan_01")

    assert run.status == "completed"
    assert len(search_tool.call_history) == 3
    assert len(extracted_urls) == 3

    # Must be round-robin: 1 from Q1, 1 from Q2, 1 from Q3
    assert extracted_urls == [
        "https://example.com/q1_s1",
        "https://example.com/q2_s1",
        "https://example.com/q3_s1",
    ]

    # Verify evidence records exist for all 3 inquiries
    ev_list = storage.list_evidence("p_cov_01", run_id=run.run_id)
    assert len(ev_list) == 3
    inquiry_targets = {e.target_claim for e in ev_list}
    assert inquiry_targets == {"Historical roots", "Neurological basis", "Psychological loops"}


def test_round_robin_respects_deduplicated_sources(tmp_path) -> None:
    proj_service = ProjectService(projects_root=tmp_path)
    proj_service.create_project("Habit Formation", project_id="p_cov_02")
    storage = ResearchStorageService(projects_root=tmp_path)

    # Q1 and Q2 return the same URL
    plan = ResearchPlan(
        plan_id="plan_02",
        project_id="p_cov_02",
        topic="Habit Formation",
        research_objective="Investigate habit formation",
        research_questions=["Question 1", "Question 2"],
        planned_queries=[
            PlannedQuery(query_id="q_01", query_text="query 1", purpose="Question 1", target_question="Question 1", tool_category="web_search"),
            PlannedQuery(query_id="q_02", query_text="query 2", purpose="Question 2", target_question="Question 2", tool_category="web_search"),
        ],
        budget_limits=ResearchBudgetConstraints(
            max_search_calls=2,
            max_extract_calls=2,
        ),
    )
    storage.save_plan(plan)

    search_data = {
        "query 1": [{"url": "https://example.com/shared_article"}],
        "query 2": [{"url": "https://example.com/shared_article"}, {"url": "https://example.com/unique_q2"}],
    }
    extract_data = {
        "https://example.com/shared_article": "# Shared\n\nSubstantive article discussing both historical and modern habit theories across multiple developmental eras.",
        "https://example.com/unique_q2": "# Unique Q2\n\nDetailed breakdown of dopamine spikes during reward anticipation and associative learning loops.",
    }

    extracted_urls: list[str] = []

    def search_handler(args, session):
        items = search_data.get(args.query, [])
        return [SearchResultItem(url=it["url"], title="Title", snippet="Snippet") for it in items]

    def extract_handler(args, session):
        extracted_urls.append(args.url)
        return extract_data.get(args.url, "# Default\n\nSubstantive content with complete sentences.")

    registry = ToolRegistry()
    search_tool = MockSearchTool(name="tavily_search", custom_handler=search_handler)
    extract_tool = MockExtractTool(name="firecrawl_extract", custom_handler=extract_handler, check_ssrf=False)
    registry.register(search_tool)
    registry.register(extract_tool)

    orchestrator = ResearchOrchestrator(storage_service=storage, tool_registry=registry)
    run = orchestrator.execute_plan(project_id="p_cov_02", plan_id="plan_02")

    assert run.status == "completed"
    # Shared article extracted only once, then unique_q2 extracted
    assert extracted_urls == [
        "https://example.com/shared_article",
        "https://example.com/unique_q2",
    ]


# ==============================================================================
# 4. Duration Compliance Tests
# ==============================================================================

def test_script_meeting_target_duration_is_compliant() -> None:
    # 5 minutes at 140 WPM = target 700 words (min 600)
    narration_650_words = " ".join(["word"] * 650)
    script = Script(
        script_id="scr_01",
        project_id="p_dur_01",
        version=1,
        title="Full Script",
        target_duration_minutes=5.0,
        sections=[ScriptSection(id="sec_01", type="middle", heading="Body", narration=narration_650_words)],
    )

    populate_script_duration_metrics(script, words_per_minute=140)

    assert script.duration_compliance is not None
    assert script.duration_compliance.compliance_status == "compliant"
    assert script.duration_compliance.actual_words == 650
    assert script.duration_compliance.target_words == 700
    assert script.duration_compliance.deficit_words == 0


def test_script_with_severe_underproduction_is_flagged_with_deficit() -> None:
    # 5 minutes at 140 WPM target 700 (min 600), but only 186 words generated
    narration_186_words = " ".join(["habit"] * 186)
    script = Script(
        script_id="scr_02",
        project_id="p_dur_02",
        version=1,
        title="Short Script",
        target_duration_minutes=5.0,
        sections=[ScriptSection(id="sec_01", type="middle", heading="Body", narration=narration_186_words)],
    )

    populate_script_duration_metrics(script, words_per_minute=140, evidence_count=1)

    assert script.duration_compliance is not None
    assert script.duration_compliance.compliance_status == "under_target_evidence_deficit"
    assert script.duration_compliance.actual_words == 186
    assert script.duration_compliance.deficit_words == (600 - 186)
    assert "insufficient to sustain the requested duration" in script.duration_compliance.assessment_note


def test_duration_compliance_does_not_alter_factual_readiness() -> None:
    narration_186_words = " ".join(["habit"] * 186)
    script = Script(
        script_id="scr_03",
        project_id="p_dur_03",
        version=1,
        title="Short Script",
        target_duration_minutes=5.0,
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration=narration_186_words, claim_ids=["clm_01"])],
        claims=[ScriptClaim(claim_id="clm_01", claim_text="Habits are automatic.", section_id="sec_01", evidence_ids=["evi_01"])],
    )
    populate_script_duration_metrics(script, words_per_minute=140, evidence_count=1)

    # Script duration deficit is flagged
    assert script.duration_compliance.compliance_status == "under_target_evidence_deficit"

    # Fact-checking report remains strictly about factual claims
    report = ScriptFactCheckReport(
        project_id="p_dur_03",
        script_version=1,
        assessments=[
            ClaimAssessment(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="Habits are automatic.",
                status="supported",
                risk_level="normal",
                explanation="Verified by evidence.",
                supporting_evidence_ids=["evi_01"],
                recommended_action="keep",
                assessed_at=utc_now(),
            )
        ],
    )
    report.recompute_summary()

    # Duration deficit does not block factual readiness if factual claims are verified
    assert report.is_ready is True


# ==============================================================================
# 5. Adversarial Grounding & Multi-Factor Duration Verification
# ==============================================================================

def test_adversarial_legitimate_prose_does_not_support_unrelated_aristotle_claim() -> None:
    # Excerpt on modern organizational sociology
    catalog = _create_test_catalog(
        excerpt="Disciplines such as psychology, sociology, and anthropology study human actions, often seeking to generalize about human behavior in society. Organizational culture with established assumptions exerts a strong influence on behavior.",
        title="Perspectives on Human Action and Organizational Behavior",
    )
    claim = ScriptClaim(
        claim_id="clm_01",
        claim_text="Aristotle was the first philosopher to formulate a systematic theory of human habit formation in the Nicomachean Ethics.",
        section_id="sec_01",
        risk_level="high",
        evidence_ids=["evi_01"],
    )
    unsupported_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="Aristotle was the first philosopher to formulate a systematic theory of human habit formation in the Nicomachean Ethics.",
                status="unsupported",
                risk_level="high",
                explanation="Supplied evidence discusses modern sociology and organizational behavior, and makes no mention of Aristotle or the Nicomachean Ethics.",
                supporting_evidence_ids=[],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=["evi_01"],
                recommended_action="qualify",
            )
        ]
    )
    llm = MockLLMForRemediation(unsupported_payload)
    checker = ScriptFactChecker(llm_provider=llm)
    script = Script(
        script_id="scr_01",
        project_id="proj_01",
        version=1,
        title="Test Script",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Aristotle habit theory.", claim_ids=["clm_01"])],
        claims=[claim],
        research_run_id="run_test_01",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "unsupported"
    assert assessment.supporting_evidence_ids == []
    assert report.is_ready is False


def test_adversarial_topically_related_incidental_overlap_does_not_support_claim() -> None:
    # Excerpt mentions human brain and electrical impulses
    catalog = _create_test_catalog(
        excerpt="Neurons in the human brain fire electrical impulses across synapses to communicate sensory information throughout the central nervous system.",
        title="Fundamentals of Neurotransmission",
    )
    claim = ScriptClaim(
        claim_id="clm_01",
        claim_text="The basal ganglia in the human brain form habits through a three-step neurological loop of cue, routine, and reward.",
        section_id="sec_01",
        risk_level="high",
        evidence_ids=["evi_01"],
    )
    unsupported_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="The basal ganglia in the human brain form habits through a three-step neurological loop of cue, routine, and reward.",
                status="unsupported",
                risk_level="high",
                explanation="While both mention the human brain, the excerpt only discusses synaptic transmission and contains no information on basal ganglia or the cue-routine-reward loop.",
                supporting_evidence_ids=[],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=["evi_01"],
                recommended_action="qualify",
            )
        ]
    )
    llm = MockLLMForRemediation(unsupported_payload)
    checker = ScriptFactChecker(llm_provider=llm)
    script = Script(
        script_id="scr_01",
        project_id="proj_01",
        version=1,
        title="Test Script",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Basal ganglia habit loop.", claim_ids=["clm_01"])],
        claims=[claim],
        research_run_id="run_test_01",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "unsupported"
    assert assessment.supporting_evidence_ids == []
    assert report.is_ready is False


def test_adversarial_compound_claim_where_only_one_proposition_supported() -> None:
    catalog = _create_test_catalog(
        excerpt="Dopamine release in the striatum reinforces behavioral repetition when unexpected rewards occur during conditioning.",
        title="Neurobiology of Reward Learning",
    )
    claim = ScriptClaim(
        claim_id="clm_01",
        claim_text="Dopamine release reinforces habit repetition, and Aristotle discovered this neurochemical link in 350 BC.",
        section_id="sec_01",
        risk_level="high",
        evidence_ids=["evi_01"],
    )
    partial_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="Dopamine release reinforces habit repetition, and Aristotle discovered this neurochemical link in 350 BC.",
                status="partially_supported",
                risk_level="high",
                explanation="Dopamine reinforcement of repetition is supported, but the historical assertion that Aristotle discovered this neurochemical link in 350 BC is unsupported.",
                supporting_evidence_ids=["evi_01"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="qualify",
            )
        ]
    )
    llm = MockLLMForRemediation(partial_payload)
    checker = ScriptFactChecker(llm_provider=llm)
    script = Script(
        script_id="scr_01",
        project_id="proj_01",
        version=1,
        title="Test Script",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Dopamine habit loop and Aristotle.", claim_ids=["clm_01"])],
        claims=[claim],
        research_run_id="run_test_01",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "partially_supported"
    assert report.requires_repair is True
    assert report.is_ready is False


def test_adversarial_valid_paraphrase_genuinely_supports_claim() -> None:
    catalog = _create_test_catalog(
        excerpt="Repetitive routines become deeply automatic when sensory cues consistently trigger established neural pathways without conscious deliberation.",
        title="Mechanisms of Behavioral Automaticity",
    )
    claim = ScriptClaim(
        claim_id="clm_01",
        claim_text="Habits operate involuntarily without conscious intention once initiated by environmental triggers.",
        section_id="sec_01",
        evidence_ids=["evi_01"],
    )
    supported_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="Habits operate involuntarily without conscious intention once initiated by environmental triggers.",
                status="supported",
                risk_level="normal",
                explanation="The excerpt verifies that cues trigger neural pathways without conscious deliberation, directly entailing that habits operate involuntarily.",
                supporting_evidence_ids=["evi_01"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="keep",
            )
        ]
    )
    llm = MockLLMForRemediation(supported_payload)
    checker = ScriptFactChecker(llm_provider=llm)
    script = Script(
        script_id="scr_01",
        project_id="proj_01",
        version=1,
        title="Test Script",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Habits operate involuntarily.", claim_ids=["clm_01"])],
        claims=[claim],
        research_run_id="run_test_01",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "supported"
    assert assessment.supporting_evidence_ids == ["evi_01"]
    assert report.is_ready is True


def test_adversarial_excerpt_supports_narrower_statement_not_broader_universal_claim() -> None:
    catalog = _create_test_catalog(
        excerpt="In a 2009 study of 96 university students, simple daily health habits took an average of 66 days to reach plateau automaticity, with substantial individual variation.",
        title="European Journal of Social Psychology Study",
    )
    claim = ScriptClaim(
        claim_id="clm_01",
        claim_text="All human habits universally take exactly 66 days to permanently set in every individual.",
        section_id="sec_01",
        risk_level="high",
        evidence_ids=["evi_01"],
    )
    partial_payload = BatchFactCheckResponsePayload(
        assessments=[
            ClaimAssessmentPayload(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="All human habits universally take exactly 66 days to permanently set in every individual.",
                status="partially_supported",
                risk_level="high",
                explanation="The evidence found an average of 66 days with substantial individual variation among 96 students, which does not support the universal absolute assertion that all habits take exactly 66 days.",
                supporting_evidence_ids=["evi_01"],
                contradicting_evidence_ids=[],
                contextual_evidence_ids=[],
                recommended_action="qualify",
            )
        ]
    )
    llm = MockLLMForRemediation(partial_payload)
    checker = ScriptFactChecker(llm_provider=llm)
    script = Script(
        script_id="scr_01",
        project_id="proj_01",
        version=1,
        title="Test Script",
        sections=[ScriptSection(id="sec_01", type="hook", heading="Hook", narration="Habits take 66 days universally.", claim_ids=["clm_01"])],
        claims=[claim],
        research_run_id="run_test_01",
    )
    report = checker.check_script(script, catalog=catalog)
    assessment = report.assessments[0]
    assert assessment.status == "partially_supported"
    assert assessment.recommended_action == "qualify"
    assert report.requires_repair is True
    assert report.is_ready is False


def test_duration_classification_no_usable_evidence() -> None:
    narration_186 = " ".join(["word"] * 186)
    script = Script(
        script_id="scr_d1",
        project_id="proj_d1",
        version=1,
        title="Short Script",
        target_duration_minutes=5.0,
        sections=[ScriptSection(id="sec_01", type="middle", heading="Body", narration=narration_186)],
    )
    populate_script_duration_metrics(script, words_per_minute=140, evidence_records=[])
    assert script.duration_compliance is not None
    assert script.duration_compliance.compliance_status == "under_target_evidence_deficit"
    assert "No usable verified research evidence" in script.duration_compliance.assessment_note


def test_duration_classification_repetitive_or_narrow_evidence() -> None:
    narration_186 = " ".join(["word"] * 186)
    script = Script(
        script_id="scr_d2",
        project_id="proj_d2",
        version=1,
        title="Short Script",
        target_duration_minutes=5.0,
        sections=[ScriptSection(id="sec_01", type="middle", heading="Body", narration=narration_186)],
    )
    records = [
        EvidenceRecord(
            evidence_id=f"evi_{i}",
            project_id="proj_d2",
            source_id="src_01",
            query_id="q_01",
            run_id="r",
            excerpt="Repetitive habit loop routine.",
            status="retrieved",
        )
        for i in range(3)
    ]
    populate_script_duration_metrics(script, words_per_minute=140, evidence_records=records)
    assert script.duration_compliance is not None
    assert script.duration_compliance.compliance_status == "under_target_evidence_deficit"
    assert "limited topic coverage or repetitive content" in script.duration_compliance.assessment_note


def test_duration_classification_adequate_coverage_with_generator_shortfall() -> None:
    narration_186 = " ".join(["word"] * 186)
    script = Script(
        script_id="scr_d3",
        project_id="proj_d3",
        version=1,
        title="Short Script",
        target_duration_minutes=5.0,
        sections=[ScriptSection(id="sec_01", type="middle", heading="Body", narration=narration_186)],
    )
    records = [
        EvidenceRecord(
            evidence_id="evi_01",
            project_id="proj_d3",
            source_id="src_01",
            query_id="q_01",
            run_id="r",
            excerpt=(
                "Aristotle described moral virtues as habits formed through intentional practice and continuous repetition across a lifetime. "
                "In the Nicomachean Ethics, he argued that human character is not innate, but rather sculptured through the daily exercise of virtues, "
                "transforming conscious moral choices into effortless, deeply ingrained dispositions."
            ),
            status="retrieved",
        ),
        EvidenceRecord(
            evidence_id="evi_02",
            project_id="proj_d3",
            source_id="src_02",
            query_id="q_02",
            run_id="r",
            excerpt=(
                "Modern neuroscience identifies the striatum and basal ganglia as central neural hubs orchestrating sensory cues and automatic behavioral responses. "
                "Through neuroplastic adaptations and dopamine signaling, frequently repeated behaviors shift cognitive control away from the prefrontal cortex "
                "into automatic subcortical circuits that execute complex motor and decision routines."
            ),
            status="retrieved",
        ),
        EvidenceRecord(
            evidence_id="evi_03",
            project_id="proj_d3",
            source_id="src_03",
            query_id="q_03",
            run_id="r",
            excerpt=(
                "Psychological research demonstrates that cues, routines, and rewards constitute the fundamental behavioral habit loop. "
                "When environmental triggers occur, established cognitive routines execute automatically to satisfy underlying cravings for reward."
            ),
            status="retrieved",
        ),
    ]
    populate_script_duration_metrics(script, words_per_minute=140, evidence_records=records)
    assert script.duration_compliance is not None
    assert script.duration_compliance.compliance_status == "under_target_generator_shortfall"
    assert "generator produced a concise script" in script.duration_compliance.assessment_note


def test_duration_classification_insufficient_information_reports_undetermined() -> None:
    narration_186 = " ".join(["word"] * 186)
    script = Script(
        script_id="scr_d4",
        project_id="proj_d4",
        version=1,
        title="Short Script",
        target_duration_minutes=5.0,
        sections=[ScriptSection(id="sec_01", type="middle", heading="Body", narration=narration_186)],
    )
    populate_script_duration_metrics(script, words_per_minute=140)
    assert script.duration_compliance is not None
    assert script.duration_compliance.compliance_status == "under_target_undetermined"
    assert "Insufficient research metadata available" in script.duration_compliance.assessment_note
