"""Research Planner service for transforming topics and project context into schema-valid research plans."""

import logging
from typing import List, Optional
import uuid

from backend.app.config import Settings, get_settings
from backend.app.domain.research import (
    GeneratedPlanPayload,
    ID_REGEX,
    PlannedQuery,
    PlannedQueryPayload,
    ResearchPlan,
    ResearchPlannerInput,
    utc_now,
)
from backend.app.llm.base import (
    LLMProvider,
    LLMProviderError,
    LLMResponseMalformedError,
    LLMUnavailableError,
)
from backend.app.services.project.service import ProjectNotFoundError, ProjectSecurityError
from backend.app.services.research.storage import (
    ResearchSecurityError,
    ResearchStorageError,
    ResearchStorageService,
    ResearchValidationError,
)

logger = logging.getLogger(__name__)


ALLOWED_TOOL_CATEGORIES = ("web_search", "web_extract")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ResearchPlannerError(Exception):
    """Base exception for all research planner failures."""
    pass


class ResearchPlannerUnavailableError(ResearchPlannerError):
    """Raised when the LLM provider service is unreachable or unavailable."""
    pass


class ResearchPlannerMalformedError(ResearchPlannerError):
    """Raised when the LLM output is malformed or fails schema validation after retries."""
    pass


class ResearchPlannerSecurityError(ResearchPlannerError):
    """Raised when project isolation or security validation fails."""
    pass


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

RESEARCH_PLAN_PROMPT_TEMPLATE = """You are a senior investigative researcher and science/history documentarian.
Your goal is to formulate a targeted, structured research plan to investigate the following topic for an educational explainer video.

Topic: {topic}
{optional_context}

Research Plan Requirements:
1. Research Objective:
   - Provide a clear, substantive objective explaining what historical, scientific, or mechanical principles must be uncovered.
2. Core Research Questions (2 to 5 questions):
   - Propose 2 to 5 targeted research questions that explore foundational baseline conditions, dilemmas/puzzles, causal mechanisms, and modern impact.
   - Do NOT ask superficial or trivial questions.
3. Planned Queries ({query_range_desc}):
   - Propose specific, high-signal web search queries directly tied to answering the research questions.
   - For each query, provide:
     * query_id: stable id (e.g., "qry_01", "qry_02")
     * query_text: concise, keyword-rich search terms (no conversational filler, no search syntax like AND/OR unless necessary)
     * purpose: specific factual insight sought
     * target_question: the exact research question this query addresses
     * tool_category: "web_search"
4. Tool Categories:
   - Set approved_tool_categories to ["web_search", "web_extract"].

Output strictly valid JSON matching this structure:
{{
  "research_objective": "Comprehensive summary of what must be researched...",
  "research_questions": [
    "Question 1...",
    "Question 2..."
  ],
  "planned_queries": [
    {{
      "query_id": "qry_01",
      "query_text": "concrete search keywords",
      "purpose": "specific factual insight sought",
      "target_question": "Question 1...",
      "tool_category": "web_search"
    }}
  ],
  "approved_tool_categories": [
    "web_search",
    "web_extract"
  ]
}}
"""


# ---------------------------------------------------------------------------
# Planner Service
# ---------------------------------------------------------------------------

class ResearchPlanner:
    """Orchestrates structured research plan generation via LLM and persists to storage."""

    def __init__(
        self,
        llm_provider: LLMProvider,
        storage_service: Optional[ResearchStorageService] = None,
        settings: Optional[Settings] = None,
        max_retries: int = 2,
    ) -> None:
        self.llm_provider = llm_provider
        self.storage_service = storage_service or ResearchStorageService(settings=settings)
        self.settings = settings or get_settings()
        self.max_retries = max(0, max_retries)

    def _build_prompt(self, planner_input: ResearchPlannerInput) -> str:
        """Construct the planning prompt from user input and project context."""
        context_parts: List[str] = []
        if planner_input.target_duration_minutes is not None:
            context_parts.append(
                f"- Target Video Duration: approximately {planner_input.target_duration_minutes:.1f} minutes"
            )
        if planner_input.user_guidance:
            context_parts.append(f"- User Guidance: {planner_input.user_guidance.strip()}")
        if planner_input.existing_script_summary:
            context_parts.append(
                f"- Existing Script Context / Summary:\n{planner_input.existing_script_summary.strip()}"
            )

        optional_context = ""
        if context_parts:
            optional_context = "Context & Directives:\n" + "\n".join(context_parts) + "\n"

        max_queries = max(1, planner_input.budget_limits.max_search_calls)
        query_range_desc = "exactly 1 query" if max_queries == 1 else f"1 to {max_queries} queries"

        return RESEARCH_PLAN_PROMPT_TEMPLATE.format(
            topic=planner_input.topic,
            optional_context=optional_context,
            query_range_desc=query_range_desc,
        )

    def plan_research(self, planner_input: ResearchPlannerInput) -> ResearchPlan:
        """Generate a structured, schema-valid ResearchPlan and persist it to project storage.

        Args:
            planner_input: Validated input containing topic, project_id, context, and budget limits.

        Returns:
            The persisted ResearchPlan.

        Raises:
            ResearchPlannerSecurityError: If project_id is invalid or violates isolation.
            ResearchPlannerUnavailableError: If the LLM provider is unreachable.
            ResearchPlannerMalformedError: If LLM output fails schema validation after retries.
            ResearchPlannerError: If any other unrecoverable failure occurs.
        """
        # 1. Project isolation and validation
        try:
            self.storage_service._resolve_research_dir(planner_input.project_id)
        except (ResearchSecurityError, ProjectSecurityError, ValueError) as sec_err:
            raise ResearchPlannerSecurityError(
                f"Project security error for '{planner_input.project_id}': {sec_err}"
            ) from sec_err
        except Exception as st_err:
            raise ResearchPlannerError(
                f"Failed to access project storage for '{planner_input.project_id}': {st_err}"
            ) from st_err

        # 2. Build initial prompt
        base_prompt = self._build_prompt(planner_input)

        # 3. Execution loop with bounded retries
        payload: Optional[GeneratedPlanPayload] = None
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            if attempt == 0:
                current_prompt = base_prompt
            else:
                error_summary = str(last_error) if last_error else "Schema validation failed"
                current_prompt = (
                    base_prompt
                    + f"\n\nCRITICAL FIX REQUIRED (Attempt {attempt + 1}/{self.max_retries + 1}):\n"
                    + f"Your previous response failed schema validation with error: {error_summary}\n"
                    + "Please fix the output so it is strictly valid JSON conforming to the requested schema."
                )

            try:
                payload = self.llm_provider.generate_json(current_prompt, GeneratedPlanPayload)
                break
            except LLMUnavailableError as unavail_err:
                # LLM service unreachable: fail immediately rather than looping
                logger.error("LLM provider unavailable during research planning: %s", unavail_err)
                raise ResearchPlannerUnavailableError(
                    f"LLM provider is unavailable: {unavail_err}"
                ) from unavail_err
            except (LLMResponseMalformedError, ValueError, Exception) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    logger.warning(
                        "Research planner attempt %d/%d failed with error: %s. Retrying...",
                        attempt + 1,
                        self.max_retries + 1,
                        exc,
                    )
                    continue
                logger.error(
                    "Research planner exhausted all %d attempts. Last error: %s",
                    self.max_retries + 1,
                    exc,
                )
                raise ResearchPlannerMalformedError(
                    f"LLM failed to generate a schema-valid research plan after {self.max_retries + 1} attempts. "
                    f"Last error: {exc}"
                ) from exc

        if payload is None:
            raise ResearchPlannerMalformedError("LLM failed to produce a research plan payload.")

        # 4. Normalize and validate planned queries
        max_search_budget = planner_input.budget_limits.max_search_calls
        planned_queries: List[PlannedQuery] = []
        seen_query_ids: set[str] = set()

        for idx, raw_query in enumerate(payload.planned_queries):
            if len(planned_queries) >= max_search_budget:
                logger.warning(
                    "Plan proposed %d queries; capping at max_search_calls budget (%d).",
                    len(payload.planned_queries),
                    max_search_budget,
                )
                break

            # Assign or sanitize query ID
            qid = raw_query.query_id or f"qry_{idx + 1:02d}"
            if not ID_REGEX.match(qid) or qid in seen_query_ids:
                qid = f"qry_{idx + 1:02d}_{uuid.uuid4().hex[:4]}"
            seen_query_ids.add(qid)

            tool_cat = raw_query.tool_category if raw_query.tool_category in ALLOWED_TOOL_CATEGORIES else "web_search"

            planned_queries.append(
                PlannedQuery(
                    query_id=qid,
                    query_text=raw_query.query_text.strip(),
                    purpose=raw_query.purpose.strip(),
                    target_question=raw_query.target_question.strip() if raw_query.target_question else None,
                    tool_category=tool_cat,
                )
            )

        # 5. Assemble final ResearchPlan domain entity
        plan_id = f"rplan_{uuid.uuid4().hex[:12]}"
        raw_categories = payload.approved_tool_categories or ["web_search", "web_extract"]
        approved_categories = [cat for cat in raw_categories if cat in ALLOWED_TOOL_CATEGORIES]
        if not approved_categories:
            approved_categories = ["web_search", "web_extract"]

        try:
            plan = ResearchPlan(
                plan_id=plan_id,
                project_id=planner_input.project_id,
                topic=planner_input.topic,
                research_objective=payload.research_objective,
                research_questions=payload.research_questions,
                planned_queries=planned_queries,
                approved_tool_categories=approved_categories,
                budget_limits=planner_input.budget_limits,
                created_at=utc_now(),
                schema_version="1.0.0",
            )
        except Exception as val_err:
            raise ResearchPlannerMalformedError(
                f"Assembled research plan failed domain validation: {val_err}"
            ) from val_err

        # 6. Atomically persist plan to storage
        try:
            self.storage_service.save_plan(plan)
            logger.info("Successfully persisted research plan '%s' for project '%s'", plan_id, planner_input.project_id)
        except Exception as st_err:
            raise ResearchPlannerError(
                f"Failed to persist research plan '{plan_id}' in project '{planner_input.project_id}': {st_err}"
            ) from st_err

        return plan
