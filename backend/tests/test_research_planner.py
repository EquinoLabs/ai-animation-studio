"""Deterministic offline tests for the Research Planner service (P2-S2)."""

import json
from pathlib import Path
import pytest

from backend.app.config import Settings
from backend.app.domain.research import (
    GeneratedPlanPayload,
    PlannedQuery,
    PlannedQueryPayload,
    ResearchBudgetConstraints,
    ResearchPlan,
    ResearchPlannerInput,
)
from backend.app.llm.base import LLMResponseMalformedError, LLMUnavailableError
from backend.app.llm.mock import MockLLMProvider
from backend.app.services.project.service import ProjectService
from backend.app.services.research.planner import (
    ResearchPlanner,
    ResearchPlannerError,
    ResearchPlannerMalformedError,
    ResearchPlannerSecurityError,
    ResearchPlannerUnavailableError,
)
from backend.app.services.research.storage import ResearchStorageService


@pytest.fixture
def temp_project(tmp_path: Path):
    """Fixture creating an isolated project environment with ResearchStorageService."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(PROJECTS_DIR=str(projects_dir))

    proj_service = ProjectService(projects_root=projects_dir)
    proj = proj_service.create_project(title="Research Test Project", project_id="proj_test_01")
    project_id = proj.metadata.project_id

    storage_service = ResearchStorageService(projects_root=projects_dir)
    return {
        "settings": settings,
        "project_service": proj_service,
        "storage_service": storage_service,
        "project_id": project_id,
        "projects_dir": projects_dir,
    }


def test_plan_research_success_and_persistence(temp_project):
    """Verify ResearchPlanner generates and atomically persists a schema-valid ResearchPlan."""
    mock_llm = MockLLMProvider()
    planner = ResearchPlanner(
        llm_provider=mock_llm,
        storage_service=temp_project["storage_service"],
        settings=temp_project["settings"],
    )

    planner_input = ResearchPlannerInput(
        project_id=temp_project["project_id"],
        topic="How Humans Invented Currency",
        target_duration_minutes=5.0,
    )

    plan = planner.plan_research(planner_input)

    # 1. Assert entity fields
    assert isinstance(plan, ResearchPlan)
    assert plan.plan_id.startswith("rplan_")
    assert plan.project_id == temp_project["project_id"]
    assert plan.topic == "How Humans Invented Currency"
    assert len(plan.research_questions) >= 1
    assert len(plan.planned_queries) >= 1
    assert plan.schema_version == "1.0.0"
    for q in plan.planned_queries:
        assert isinstance(q, PlannedQuery)
        assert q.query_id.startswith("qry_")
        assert len(q.query_text) > 0
        assert len(q.purpose) > 0
        assert q.tool_category in ["web_search", "web_extract"]

    # 2. Verify disk persistence
    plan_file = (
        temp_project["projects_dir"]
        / temp_project["project_id"]
        / "research"
        / "plans"
        / f"{plan.plan_id}.json"
    )
    assert plan_file.exists()
    assert plan_file.is_file()

    # 3. Verify retrieved plan matches
    retrieved = temp_project["storage_service"].get_plan(temp_project["project_id"], plan.plan_id)
    assert retrieved.plan_id == plan.plan_id
    assert retrieved.topic == plan.topic
    assert retrieved.research_questions == plan.research_questions
    assert len(retrieved.planned_queries) == len(plan.planned_queries)


def test_plan_research_retries_on_malformed_json_and_recovers(temp_project):
    """Verify bounded retry when LLM first returns malformed output, then succeeds."""
    mock_llm = MockLLMProvider()

    # Attempt 1 returns invalid payload (missing research_questions)
    malformed_response = {
        "research_objective": "Invalid draft missing required research questions.",
        "planned_queries": [],
    }

    # Attempt 2 returns valid payload
    valid_response = {
        "research_objective": "Investigate earliest commodity money and minting transitions in Lydia.",
        "research_questions": [
            "Why was barter inadequate for specialized urban trade?",
            "What role did electrum metallurgy play in the kingdom of Lydia?",
        ],
        "planned_queries": [
            {
                "query_id": "qry_01",
                "query_text": "Lydian electrum coinage invention 600 BC",
                "purpose": "Verify historical timeline of first standardized state-minted coins",
                "target_question": "What role did electrum metallurgy play in the kingdom of Lydia?",
                "tool_category": "web_search",
            }
        ],
        "approved_tool_categories": ["web_search", "web_extract"],
    }

    mock_llm.queue_responses([malformed_response, valid_response])

    planner = ResearchPlanner(
        llm_provider=mock_llm,
        storage_service=temp_project["storage_service"],
        settings=temp_project["settings"],
        max_retries=2,
    )

    planner_input = ResearchPlannerInput(
        project_id=temp_project["project_id"],
        topic="Invention of Coins in Ancient Lydia",
    )

    plan = planner.plan_research(planner_input)

    assert plan is not None
    assert len(mock_llm.calls) == 2
    # Verify second prompt included correction feedback
    second_prompt = mock_llm.calls[1]["prompt"]
    assert "CRITICAL FIX REQUIRED (Attempt 2/3)" in second_prompt
    assert plan.research_questions[0] == "Why was barter inadequate for specialized urban trade?"
    assert plan.planned_queries[0].query_text == "Lydian electrum coinage invention 600 BC"


def test_plan_research_exhausts_retries_and_raises_typed_error(temp_project):
    """Verify planner exhausts retries and raises ResearchPlannerMalformedError without saving plan."""
    mock_llm = MockLLMProvider()

    # 3 consecutive malformed payloads
    bad_responses = [
        {"broken": "no objective"},
        {"research_objective": ""},  # empty string rejected by validator
        {"research_objective": "Valid objective", "research_questions": []},  # empty list rejected
    ]
    mock_llm.queue_responses(bad_responses)

    planner = ResearchPlanner(
        llm_provider=mock_llm,
        storage_service=temp_project["storage_service"],
        settings=temp_project["settings"],
        max_retries=2,
    )

    planner_input = ResearchPlannerInput(
        project_id=temp_project["project_id"],
        topic="Defective Plan Generation Test",
    )

    with pytest.raises(ResearchPlannerMalformedError) as exc_info:
        planner.plan_research(planner_input)

    assert "exhausted all 3 attempts" in str(exc_info.value).lower() or "after 3 attempts" in str(exc_info.value).lower()
    assert len(mock_llm.calls) == 3

    # Ensure no plan file was written
    plans_dir = temp_project["projects_dir"] / temp_project["project_id"] / "research" / "plans"
    plan_files = list(plans_dir.glob("*.json"))
    assert len(plan_files) == 0


def test_plan_research_handles_llm_unavailable_error(temp_project):
    """Verify planner raises ResearchPlannerUnavailableError immediately when provider is unreachable."""
    mock_llm = MockLLMProvider()
    mock_llm.set_error(LLMUnavailableError("Connection refused to http://localhost:11434"))

    planner = ResearchPlanner(
        llm_provider=mock_llm,
        storage_service=temp_project["storage_service"],
        settings=temp_project["settings"],
        max_retries=2,
    )

    planner_input = ResearchPlannerInput(
        project_id=temp_project["project_id"],
        topic="Ancient Trade Routes",
    )

    with pytest.raises(ResearchPlannerUnavailableError) as exc_info:
        planner.plan_research(planner_input)

    assert "Connection refused" in str(exc_info.value)
    # Unavailable provider must NOT be retried multiple times
    assert len(mock_llm.calls) == 1

    # Ensure no plan file was written
    plans_dir = temp_project["projects_dir"] / temp_project["project_id"] / "research" / "plans"
    assert len(list(plans_dir.glob("*.json"))) == 0


def test_plan_research_enforces_project_isolation(temp_project):
    """Verify path traversal or malformed project IDs are rejected with security errors."""
    mock_llm = MockLLMProvider()
    planner = ResearchPlanner(
        llm_provider=mock_llm,
        storage_service=temp_project["storage_service"],
        settings=temp_project["settings"],
    )

    # 1. Pydantic validator on ResearchPlannerInput blocks malformed project_id
    with pytest.raises(ValueError):
        ResearchPlannerInput(
            project_id="../../etc/shadow",
            topic="Path Traversal Test",
        )

    # 2. Defense-in-depth: if an invalid model instance is constructed
    malicious_input = ResearchPlannerInput.model_construct(
        project_id="../traversal_proj",
        topic="Path Traversal Test",
        budget_limits=ResearchBudgetConstraints(),
    )

    with pytest.raises(ResearchPlannerSecurityError) as exc_info:
        planner.plan_research(malicious_input)

    assert "Project security error" in str(exc_info.value)
    assert len(mock_llm.calls) == 0


def test_plan_research_incorporates_context_and_respects_budget_capping(temp_project):
    """Verify prompt incorporates user directives, duration, script summary, and caps excess queries."""
    mock_llm = MockLLMProvider()

    # Preset payload returning 5 queries when budget only allows 2
    excess_queries_payload = {
        "research_objective": "Explore agricultural revolution origins.",
        "research_questions": [
            "What climatic shifts triggered the Natufian transition to sedentarism?",
        ],
        "planned_queries": [
            {
                "query_id": f"qry_{i}",
                "query_text": f"Natufian agriculture query {i}",
                "purpose": f"Purpose {i}",
                "target_question": "What climatic shifts triggered the Natufian transition to sedentarism?",
                "tool_category": "web_search",
            }
            for i in range(1, 6)
        ],
        "approved_tool_categories": ["web_search", "web_extract"],
    }
    mock_llm.set_response(excess_queries_payload)

    planner = ResearchPlanner(
        llm_provider=mock_llm,
        storage_service=temp_project["storage_service"],
        settings=temp_project["settings"],
    )

    planner_input = ResearchPlannerInput(
        project_id=temp_project["project_id"],
        topic="The Natufian Transition",
        target_duration_minutes=8.5,
        user_guidance="Emphasize archaeological evidence from the Fertile Crescent.",
        existing_script_summary="Draft v1 lacked Younger Dryas climate details.",
        budget_limits=ResearchBudgetConstraints(max_search_calls=2),
    )

    plan = planner.plan_research(planner_input)

    # Verify prompt contents
    prompt_sent = mock_llm.calls[0]["prompt"]
    assert "Target Video Duration: approximately 8.5 minutes" in prompt_sent
    assert "Emphasize archaeological evidence from the Fertile Crescent." in prompt_sent
    assert "Draft v1 lacked Younger Dryas climate details." in prompt_sent
    assert "Planned Queries (1 to 2 queries):" in prompt_sent

    # Verify query capping
    assert len(plan.planned_queries) == 2
    assert plan.planned_queries[0].query_text == "Natufian agriculture query 1"
    assert plan.planned_queries[1].query_text == "Natufian agriculture query 2"
    assert plan.budget_limits.max_search_calls == 2


def test_plan_research_minimum_budget_max_search_calls_one(temp_project):
    """Verify behavior when max_search_calls is at its minimum allowed value (1)."""
    mock_llm = MockLLMProvider()

    # Model proposes 3 queries
    payload = {
        "research_objective": "Determine single decisive historical moment.",
        "research_questions": ["What was the primary turning point?"],
        "planned_queries": [
            {
                "query_id": f"qry_{i}",
                "query_text": f"Search query {i}",
                "purpose": f"Purpose {i}",
                "target_question": "What was the primary turning point?",
                "tool_category": "web_search",
            }
            for i in range(1, 4)
        ],
        "approved_tool_categories": ["web_search"],
    }
    mock_llm.set_response(payload)

    planner = ResearchPlanner(
        llm_provider=mock_llm,
        storage_service=temp_project["storage_service"],
        settings=temp_project["settings"],
    )

    planner_input = ResearchPlannerInput(
        project_id=temp_project["project_id"],
        topic="Turning Points in Astronomy",
        budget_limits=ResearchBudgetConstraints(max_search_calls=1),
    )

    plan = planner.plan_research(planner_input)

    # Verify prompt instructs single query
    prompt_sent = mock_llm.calls[0]["prompt"]
    assert "Planned Queries (exactly 1 query):" in prompt_sent

    # Verify capped at exactly 1 query and contract is fully satisfied
    assert len(plan.planned_queries) == 1
    assert plan.planned_queries[0].query_text == "Search query 1"
    assert plan.budget_limits.max_search_calls == 1
    assert plan.schema_version == "1.0.0"

    # Verify disk persistence
    retrieved = temp_project["storage_service"].get_plan(temp_project["project_id"], plan.plan_id)
    assert len(retrieved.planned_queries) == 1


def test_plan_research_filters_unsupported_tool_categories(temp_project):
    """Verify unsupported tool categories from LLM output cannot survive into persisted plan."""
    mock_llm = MockLLMProvider()

    # Payload with forbidden/unsupported categories
    payload = {
        "research_objective": "Test tool category allowlist filtering.",
        "research_questions": ["Can arbitrary tools be injected?"],
        "planned_queries": [
            {
                "query_id": "qry_01",
                "query_text": "Safe search query",
                "purpose": "Verify safe search",
                "target_question": "Can arbitrary tools be injected?",
                "tool_category": "arbitrary_unauthorized_tool",
            }
        ],
        "approved_tool_categories": ["eval_exec", "bash_run", "web_search"],
    }
    mock_llm.set_response(payload)

    planner = ResearchPlanner(
        llm_provider=mock_llm,
        storage_service=temp_project["storage_service"],
        settings=temp_project["settings"],
    )

    planner_input = ResearchPlannerInput(
        project_id=temp_project["project_id"],
        topic="Security Filtering Test",
    )

    plan = planner.plan_research(planner_input)

    # Approved tool categories must only contain allowlisted items
    assert plan.approved_tool_categories == ["web_search"]
    # Query tool category must be sanitized to web_search
    assert plan.planned_queries[0].tool_category == "web_search"

    # Verify persisted artifact also has sanitized categories
    retrieved = temp_project["storage_service"].get_plan(temp_project["project_id"], plan.plan_id)
    assert retrieved.approved_tool_categories == ["web_search"]
    assert retrieved.planned_queries[0].tool_category == "web_search"


def test_plan_research_model_cannot_bypass_budget_constraints(temp_project):
    """Verify caller's budget constraints strictly govern the plan regardless of model output."""
    mock_llm = MockLLMProvider()

    custom_budget = ResearchBudgetConstraints(
        max_search_calls=2,
        max_extract_calls=1,
        call_timeout_seconds=8.0,
        deadline_seconds=25.0,
        max_page_chars=5000,
        max_total_chars=12000,
    )

    planner = ResearchPlanner(
        llm_provider=mock_llm,
        storage_service=temp_project["storage_service"],
        settings=temp_project["settings"],
    )

    planner_input = ResearchPlannerInput(
        project_id=temp_project["project_id"],
        topic="Budget Invariance Test",
        budget_limits=custom_budget,
    )

    plan = planner.plan_research(planner_input)

    # Plan's budget limits must match caller's constraints verbatim
    assert plan.budget_limits == custom_budget
    assert plan.budget_limits.max_search_calls == 2
    assert plan.budget_limits.deadline_seconds == 25.0


def test_plan_research_failed_write_raises_and_preserves_existing_plans(temp_project, monkeypatch):
    """Verify write failure raises ResearchPlannerError and does not corrupt existing valid plans."""
    mock_llm = MockLLMProvider()
    planner = ResearchPlanner(
        llm_provider=mock_llm,
        storage_service=temp_project["storage_service"],
        settings=temp_project["settings"],
    )

    # 1. Successfully create an initial plan
    input_1 = ResearchPlannerInput(
        project_id=temp_project["project_id"],
        topic="Initial Valid Plan",
    )
    plan_1 = planner.plan_research(input_1)
    assert temp_project["storage_service"].get_plan(temp_project["project_id"], plan_1.plan_id) is not None

    # 2. Simulate storage failure on second plan save
    def failing_save_plan(plan):
        raise OSError("Disk write error: simulated out of space")

    monkeypatch.setattr(temp_project["storage_service"], "save_plan", failing_save_plan)

    input_2 = ResearchPlannerInput(
        project_id=temp_project["project_id"],
        topic="Second Failing Plan",
    )

    with pytest.raises(ResearchPlannerError) as exc_info:
        planner.plan_research(input_2)

    assert "Failed to persist research plan" in str(exc_info.value)

    # 3. Verify initial plan remains intact and uncorrupted
    retrieved_1 = temp_project["storage_service"].get_plan(temp_project["project_id"], plan_1.plan_id)
    assert retrieved_1.plan_id == plan_1.plan_id
    assert retrieved_1.topic == "Initial Valid Plan"


def test_plan_research_custom_max_retries_honored(temp_project):
    """Verify planner honors custom max_retries configuration (e.g., max_retries=1 => 2 attempts)."""
    mock_llm = MockLLMProvider()

    bad_responses = [
        {"broken": "attempt 1"},
        {"broken": "attempt 2"},
        {"broken": "attempt 3"},
    ]
    mock_llm.queue_responses(bad_responses)

    planner = ResearchPlanner(
        llm_provider=mock_llm,
        storage_service=temp_project["storage_service"],
        settings=temp_project["settings"],
        max_retries=1,  # Exactly 1 retry => 2 attempts total
    )

    planner_input = ResearchPlannerInput(
        project_id=temp_project["project_id"],
        topic="Bounded Retries Test",
    )

    with pytest.raises(ResearchPlannerMalformedError) as exc_info:
        planner.plan_research(planner_input)

    assert "after 2 attempts" in str(exc_info.value).lower()
    assert len(mock_llm.calls) == 2


def test_deterministic_mock_provider_default_behavior(temp_project):
    """Verify MockLLMProvider generates a valid research plan payload out-of-the-box."""
    mock_llm = MockLLMProvider()
    payload = mock_llm.generate_json("Investigate black holes", GeneratedPlanPayload)

    assert isinstance(payload, GeneratedPlanPayload)
    assert len(payload.research_objective) > 0
    assert len(payload.research_questions) >= 2
    assert len(payload.planned_queries) >= 2
    for q in payload.planned_queries:
        assert isinstance(q, PlannedQueryPayload)
        assert q.query_id.startswith("qry_")
        assert len(q.query_text) > 0
