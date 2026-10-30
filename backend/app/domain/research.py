"""Domain data contracts for research planning, queries, sources, evidence, and run execution."""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]+$")

SUPPORTED_SCHEMA_VERSIONS = {"1.0.0"}

ResearchRunStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
QueryStatus = Literal["planned", "running", "succeeded", "failed", "skipped"]
SourceType = Literal["web_page", "scholarly", "encyclopedic", "primary", "news", "other"]
RetrievalMethod = Literal["search_snippet", "full_extraction", "manual"]
RetrievalStatus = Literal["discovered", "extracted", "failed", "unavailable"]
EvidenceStatus = Literal["retrieved", "candidate", "discarded"]


# ---------------------------------------------------------------------------
# Budget Constraints Contract
# ---------------------------------------------------------------------------

class ResearchBudgetConstraints(BaseModel):
    """Budget limits and timeout ceilings for a research run."""
    model_config = ConfigDict(extra="forbid")

    max_search_calls: int = Field(default=3, ge=1, le=20, description="Max outbound search API calls.")
    max_extract_calls: int = Field(default=3, ge=0, le=20, description="Max outbound extract API calls.")
    call_timeout_seconds: float = Field(default=15.0, ge=1.0, le=60.0, description="Timeout per HTTP call.")
    deadline_seconds: float = Field(default=60.0, ge=5.0, le=300.0, description="Total stage deadline.")
    max_page_chars: int = Field(default=15000, ge=500, le=100000, description="Max chars per extracted page.")
    max_total_chars: int = Field(default=45000, ge=1000, le=300000, description="Max total extracted chars.")


# ---------------------------------------------------------------------------
# Research Plan Contract
# ---------------------------------------------------------------------------

class PlannedQuery(BaseModel):
    """A search query planned as part of a research plan."""
    model_config = ConfigDict(extra="forbid")

    query_id: str = Field(..., description="Stable query identifier (e.g., qry_<id>).")
    query_text: str = Field(..., min_length=1, max_length=500, description="Exact search query string.")
    purpose: str = Field(..., min_length=1, max_length=500, description="Objective of this search query.")
    target_question: Optional[str] = Field(default=None, description="The research question this query addresses.")
    tool_category: str = Field(default="web_search", description="Approved tool category (e.g., web_search).")

    @field_validator("query_id")
    @classmethod
    def validate_query_id(cls, v: str) -> str:
        if not v or not ID_REGEX.match(v):
            raise ValueError(f"Invalid query_id '{v}'. Must be alphanumeric with underscores or dashes.")
        return v


class ResearchPlan(BaseModel):
    """The intended research work specification before execution."""
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(..., description="Stable research plan identifier (e.g., rplan_<id>).")
    project_id: str = Field(..., description="Associated project identifier.")
    topic: str = Field(..., min_length=1, max_length=500, description="The subject being researched.")
    research_objective: str = Field(..., min_length=1, max_length=1000, description="Overall goal of the research.")
    research_questions: List[str] = Field(..., min_length=1, description="Core questions the research must answer.")
    planned_queries: List[PlannedQuery] = Field(default_factory=list, description="Planned queries to execute.")
    approved_tool_categories: List[str] = Field(
        default_factory=lambda: ["web_search", "web_extract"],
        description="Tool categories permitted for this plan.",
    )
    budget_limits: ResearchBudgetConstraints = Field(
        default_factory=ResearchBudgetConstraints,
        description="Configured invocation and content limits.",
    )
    created_at: datetime = Field(default_factory=utc_now)
    schema_version: str = "1.0.0"

    @field_validator("plan_id", "project_id")
    @classmethod
    def validate_ids(cls, v: str) -> str:
        if not v or not ID_REGEX.match(v):
            raise ValueError(f"Invalid identifier '{v}'. Must be alphanumeric with underscores or dashes.")
        return v

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, v: str) -> str:
        if v not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"Unsupported schema_version '{v}'. Supported versions: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
            )
        return v


    @field_validator("research_questions")
    @classmethod
    def validate_questions(cls, v: List[str]) -> List[str]:
        cleaned = [q.strip() for q in v if q and q.strip()]
        if not cleaned:
            raise ValueError("Research plan must include at least one non-empty research question.")
        return cleaned


# ---------------------------------------------------------------------------
# Research Planner Input and Output Payloads
# ---------------------------------------------------------------------------

class ResearchPlannerInput(BaseModel):
    """Input contract for requesting a structured research plan."""
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., description="Target project identifier.")
    topic: str = Field(..., min_length=1, max_length=500, description="Core topic to research.")
    target_duration_minutes: Optional[float] = Field(
        default=None, ge=0.5, le=60.0, description="Intended video duration in minutes if known."
    )
    existing_script_summary: Optional[str] = Field(
        default=None, max_length=2000, description="Optional summary of current draft if revising."
    )
    user_guidance: Optional[str] = Field(
        default=None, max_length=1000, description="Optional user directives or focus points."
    )
    budget_limits: ResearchBudgetConstraints = Field(
        default_factory=ResearchBudgetConstraints,
        description="Configured invocation and content limits for the research run.",
    )

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, v: str) -> str:
        if not v or not ID_REGEX.match(v):
            raise ValueError(f"Invalid project_id '{v}'. Must be alphanumeric with underscores or dashes.")
        return v

    @field_validator("topic")
    @classmethod
    def validate_topic(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Topic must not be empty or blank.")
        return cleaned


class PlannedQueryPayload(BaseModel):
    """Raw planned query parsed from model output."""
    model_config = ConfigDict(extra="ignore")

    query_id: Optional[str] = None
    query_text: str = Field(..., min_length=1, max_length=500, description="Search query string.")
    purpose: str = Field(..., min_length=1, max_length=500, description="Objective of this search query.")
    target_question: Optional[str] = Field(default=None, description="The research question this query addresses.")
    tool_category: str = Field(default="web_search", description="Tool category (e.g., web_search).")

    @field_validator("query_text", "purpose")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Field must not be empty or blank.")
        return cleaned


class GeneratedPlanPayload(BaseModel):
    """Structured research plan payload returned by the LLM."""
    model_config = ConfigDict(extra="ignore")

    research_objective: str = Field(
        ..., min_length=1, max_length=1000, description="Primary research objective."
    )
    research_questions: List[str] = Field(..., min_length=1, description="Core research questions.")
    planned_queries: List[PlannedQueryPayload] = Field(
        default_factory=list, description="Planned queries to execute."
    )
    approved_tool_categories: List[str] = Field(
        default_factory=lambda: ["web_search", "web_extract"],
        description="Approved tool categories.",
    )

    @field_validator("research_questions")
    @classmethod
    def validate_questions(cls, v: List[str]) -> List[str]:
        cleaned = [q.strip() for q in v if q and q.strip()]
        if not cleaned:
            raise ValueError("Research plan must include at least one non-empty research question.")
        return cleaned

    @field_validator("research_objective")
    @classmethod
    def validate_objective(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Research objective must not be empty or blank.")
        return cleaned



# ---------------------------------------------------------------------------
# Query Record Contract
# ---------------------------------------------------------------------------

class QueryRecord(BaseModel):
    """Execution record for an individual research query."""
    model_config = ConfigDict(extra="forbid")

    query_id: str
    plan_id: str
    run_id: str
    query_text: str = Field(..., min_length=1)
    intended_question: Optional[str] = None
    tool_name: str = Field(..., min_length=1)
    status: QueryStatus = "planned"
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    returned_source_ids: List[str] = Field(default_factory=list)
    result_count: int = 0

    @field_validator("query_id", "plan_id", "run_id")
    @classmethod
    def validate_ids(cls, v: str) -> str:
        if not v or not ID_REGEX.match(v):
            raise ValueError(f"Invalid identifier '{v}'. Must be alphanumeric with underscores or dashes.")
        return v


# ---------------------------------------------------------------------------
# Source Record Contract
# ---------------------------------------------------------------------------

class SourceRecord(BaseModel):
    """Retrieved source metadata and provenance tracking."""
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(..., description="Stable source identifier (e.g., src_<id>).")
    project_id: str
    url: str = Field(..., description="Canonical or retrieved HTTP/HTTPS URL.")
    canonical_url: Optional[str] = None
    title: Optional[str] = None
    publisher: Optional[str] = None
    source_type: SourceType = "web_page"
    retrieval_method: RetrievalMethod = "search_snippet"
    retrieval_status: RetrievalStatus = "discovered"
    retrieved_at: datetime = Field(default_factory=utc_now)
    publication_date: Optional[str] = None
    discovery_snippet: Optional[str] = Field(
        default=None,
        description="Search snippet captured at discovery time. Retained as metadata only; never promoted to an EvidenceRecord.",
    )
    content_artifact_ref: Optional[str] = None
    content_hash: Optional[str] = None
    char_count: int = 0

    @field_validator("source_id", "project_id")
    @classmethod
    def validate_ids(cls, v: str) -> str:
        if not v or not ID_REGEX.match(v):
            raise ValueError(f"Invalid identifier '{v}'. Must be alphanumeric with underscores or dashes.")
        return v

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v or not isinstance(v, str):
            raise ValueError("URL must be a non-empty string.")
        parsed = urlsplit(v.strip())
        scheme = (parsed.scheme or "").lower()
        if scheme not in ("http", "https"):
            raise ValueError(f"Invalid URL scheme '{scheme}'. Only HTTP and HTTPS are permitted.")
        if parsed.username or parsed.password:
            raise ValueError("URL must not contain embedded username or password credentials.")
        if not parsed.hostname:
            raise ValueError("URL must include a valid hostname.")
        return v.strip()


# ---------------------------------------------------------------------------
# Evidence Record Contract
# ---------------------------------------------------------------------------

class EvidenceRecord(BaseModel):
    """An excerpt of evidence retrieved from a source."""
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(..., description="Stable evidence identifier (e.g., evi_<id>).")
    project_id: str
    source_id: str
    query_id: Optional[str] = None
    run_id: Optional[str] = None
    excerpt: str = Field(..., min_length=1, description="The verbatim excerpt or factual note.")
    locator: Optional[str] = Field(default=None, description="Section, heading, or paragraph locator.")
    target_claim: Optional[str] = Field(default=None, description="Research question or claim addressed.")
    status: EvidenceStatus = "retrieved"
    selection_rationale: Optional[str] = Field(
        default=None,
        description="Optional rationale explaining why this evidence excerpt was selected as candidate."
    )
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("evidence_id", "project_id", "source_id")
    @classmethod
    def validate_ids(cls, v: str) -> str:
        if not v or not ID_REGEX.match(v):
            raise ValueError(f"Invalid identifier '{v}'. Must be alphanumeric with underscores or dashes.")
        return v


# ---------------------------------------------------------------------------
# Research Run State Contract
# ---------------------------------------------------------------------------

class ResearchRun(BaseModel):
    """Execution state and lifecycle record for a research run."""
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(..., description="Stable run identifier (e.g., rrun_<id>).")
    plan_id: str = Field(..., description="Associated research plan identifier.")
    project_id: str = Field(..., description="Associated project identifier.")
    status: ResearchRunStatus = "pending"
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: Optional[datetime] = None
    search_calls_used: int = Field(default=0, ge=0)
    extract_calls_used: int = Field(default=0, ge=0)
    total_chars_extracted: int = Field(default=0, ge=0)
    budget_limits: ResearchBudgetConstraints = Field(default_factory=ResearchBudgetConstraints)
    termination_reason: Optional[str] = None
    artifacts: Dict[str, str] = Field(default_factory=dict)
    query_records: List[QueryRecord] = Field(default_factory=list)
    schema_version: str = "1.0.0"

    @field_validator("run_id", "plan_id", "project_id")
    @classmethod
    def validate_ids(cls, v: str) -> str:
        if not v or not ID_REGEX.match(v):
            raise ValueError(f"Invalid identifier '{v}'. Must be alphanumeric with underscores or dashes.")
        return v

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, v: str) -> str:
        if v not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"Unsupported schema_version '{v}'. Supported versions: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
            )
        return v


# ---------------------------------------------------------------------------
# Phase 6: Research Studio Inspection & Conversational Challenge Contracts
# ---------------------------------------------------------------------------

class SafeSourceView(BaseModel):
    """Sanitized, safe view of a source record for frontend inspection."""
    model_config = ConfigDict(extra="forbid")

    source_id: str
    url: str
    title: Optional[str] = None
    publisher: Optional[str] = None
    retrieval_status: str
    retrieved_at: Optional[datetime] = None
    publication_date: Optional[str] = None
    char_count: int = 0


class SafeEvidenceView(BaseModel):
    """Sanitized view of an evidence record with active script usage tracking."""
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    source_id: str
    query_id: Optional[str] = None
    run_id: Optional[str] = None
    excerpt: str
    locator: Optional[str] = None
    target_claim: Optional[str] = None
    status: str
    is_selected: bool = False
    selection_rationale: Optional[str] = None
    is_used_in_current_script: bool = False
    used_by_claims: List[str] = Field(default_factory=list)


class QueryOutcomeView(BaseModel):
    """Sanitized view of a research query execution outcome."""
    model_config = ConfigDict(extra="forbid")

    query_id: str
    query_text: str
    intended_question: Optional[str] = None
    status: str
    result_count: int = 0
    error_message: Optional[str] = None
    skip_reason: Optional[str] = None
    returned_source_ids: List[str] = Field(default_factory=list)


class ResearchOverviewResponse(BaseModel):
    """Comprehensive research overview for the Research & Facts Studio panel."""
    model_config = ConfigDict(extra="forbid")

    project_id: str
    has_research: bool = False
    active_run_id: Optional[str] = None
    status: Optional[str] = None
    termination_reason: Optional[str] = None
    budget_summary: Dict[str, Any] = Field(default_factory=dict)
    queries: List[QueryOutcomeView] = Field(default_factory=list)
    sources: List[SafeSourceView] = Field(default_factory=list)
    selected_evidence: List[SafeEvidenceView] = Field(default_factory=list)
    retrieved_evidence_ids: List[str] = Field(default_factory=list)
    selected_evidence_ids: List[str] = Field(default_factory=list)
    used_evidence_ids: List[str] = Field(default_factory=list)
    unused_evidence_ids: List[str] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)
    unresolved_questions: List[str] = Field(default_factory=list)
    disclaimer: str = (
        "Research and evidence records are provided for creator inspection and verification. "
        "AI-assisted retrieval improves reliability but does not guarantee absolute factual truth."
    )


class ResearchBriefResponse(BaseModel):
    """Evidence brief response for markdown display."""
    model_config = ConfigDict(extra="forbid")

    project_id: str
    run_id: Optional[str] = None
    brief_markdown: str


class ChallengeClaimRequest(BaseModel):
    """Request payload for conversational claim challenge."""
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(..., min_length=1, max_length=50, description="Target claim ID to challenge (e.g. clm_01)")
    instruction: Optional[str] = Field(default=None, max_length=2000, description="Conversational challenge instruction or objection")
    action: Optional[Literal["investigate", "remove", "qualify", "rephrase"]] = Field(
        default="investigate",
        description="Target challenge action: investigate, remove, qualify, or rephrase"
    )


class ChallengeClaimResponse(BaseModel):
    """Response payload returned upon executing a claim challenge."""
    model_config = ConfigDict(extra="forbid")

    script: Dict[str, Any] = Field(description="Updated script dictionary or model")
    workflow_state: Dict[str, Any] = Field(default_factory=dict)
    fact_check_report: Optional[Dict[str, Any]] = None
    challenge_summary: Dict[str, Any] = Field(default_factory=dict)

