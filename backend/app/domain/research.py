"""Domain data contracts for research planning, queries, sources, evidence, and run execution."""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Literal, Optional, Set, Tuple
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


_TOPIC_STOPWORDS: Set[str] = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are",
    "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but",
    "by", "can", "could", "did", "do", "does", "doing", "down", "during", "each", "few", "for",
    "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers", "herself",
    "him", "himself", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself", "just",
    "me", "more", "most", "my", "myself", "no", "nor", "not", "now", "of", "off", "on", "once",
    "only", "or", "other", "our", "ours", "ourselves", "out", "over", "own", "same", "she",
    "should", "so", "some", "such", "than", "that", "the", "their", "theirs", "them", "themselves",
    "then", "there", "these", "they", "this", "those", "through", "to", "too", "under", "until",
    "up", "very", "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom",
    "why", "will", "with", "would", "you", "your", "yours", "yourself", "yourselves",
}

_GENERIC_CONTEXT_WORDS: Set[str] = {
    "work", "works", "worked", "working",
    "form", "forms", "formed", "forming", "formation",
    "use", "uses", "used", "using",
    "make", "makes", "made", "making",
    "way", "ways", "type", "types", "case", "cases",
    "take", "takes", "took", "taking",
    "help", "helps", "helped", "helping",
    "time", "times", "good", "better", "best", "bad",
    "start", "starts", "started", "starting",
    "need", "needs", "needed",
    "high", "higher", "highest", "low", "lower", "lowest",
    "look", "looks", "looked", "looking",
    "occur", "occurs", "occurred", "occurring",
    "show", "shows", "showed", "showing",
    "tell", "tells", "told", "step", "steps",
    "part", "parts", "point", "points", "thing", "things",
    # Broad/generic developmental and environmental terms that do not constitute habit mechanisms on their own:
    "factor", "factors", "development", "influencing", "influence", "influences", "influenced",
}


def _canonical_root(word: str) -> str:
    """Return the base morphological root of a substantive word."""
    w = word.lower()
    for suffix in ("ing", "tions", "tion", "ities", "ity", "ally", "al", "ical", "ic", "ed", "es", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            w = w[:-len(suffix)]
            break
    if len(w) >= 5:
        return w[:5]
    return w


def extract_topic_tokens_and_stems(
    *sources: Optional[str],
) -> Tuple[Set[str], Set[str]]:
    """Extract substantive domain tokens and morphological prefix stems from context strings."""
    tokens: Set[str] = set()
    stems: Set[str] = set()
    for src in sources:
        if not src:
            continue
        cleaned = re.sub(r"[^\w\s]", " ", src.lower())
        for raw_w in cleaned.split():
            w = raw_w.strip()
            if len(w) >= 3 and w not in _TOPIC_STOPWORDS and not w.isdigit():
                tokens.add(w)
                if len(w) >= 5:
                    stems.add(w[:5])
                elif len(w) >= 4:
                    stems.add(w[:4])
                root = _canonical_root(w)
                if len(root) >= 4:
                    stems.add(root)
    return tokens, stems


def check_topical_relevance(
    text: str,
    heading: Optional[str],
    tokens: Set[str],
    stems: Set[str],
    topic: Optional[str] = None,
) -> Tuple[bool, Set[str], Set[str]]:
    """Determine whether text demonstrates meaningful topical relevance to tokens/stems.

    Enforces deterministic meaningful relevance:
    - Rejects heading-only matches where body content has zero specific anchor matches.
    - Rejects solitary incidental mentions (a single occurrence of 1 keyword with no other context).
    - Rejects short prefix collisions from generic terms (e.g. 'work', 'form').
    - Requires primary topic anchor (e.g. habit) when evaluating peripheral queries (e.g. environment, child development).
    - Accepts multi-term contextual matches, repeated concentration, or adjacent topic phrases.
    """
    if not tokens and not stems:
        return True, set(), set()

    text_words = re.findall(r"\b[a-zA-Z0-9_-]+\b", text.lower())
    text_words_set = set(text_words)
    heading_words = re.findall(r"\b[a-zA-Z0-9_-]+\b", (heading or "").lower())
    heading_words_set = set(heading_words)

    # 0. Primary Topic Anchor Gate:
    # If a broad overall research topic is provided (e.g. "Why do humans form habits..."),
    # peripheral sub-queries (e.g. "environmental factors") must contain at least one primary topic anchor root
    # in the text or heading to ensure generic content (e.g. child development, weather, economics) is not admitted.
    # Exception: If the passage exhibits deep technical query convergence (>= 3 distinct specific concept roots),
    # it is admitted as highly targeted scientific evidence.
    specific_tokens = {t for t in tokens if t not in _GENERIC_CONTEXT_WORDS}
    if not specific_tokens:
        specific_tokens = set(tokens)
    specific_roots = {_canonical_root(t) for t in specific_tokens}

    matched_specific_roots: Set[str] = set()
    body_specific_occurrences = 0
    for w in text_words:
        w_root = _canonical_root(w)
        for r in specific_roots:
            if w_root == r or (len(r) >= 4 and w.startswith(r)):
                matched_specific_roots.add(r)
                body_specific_occurrences += 1
                break

    if topic and len(matched_specific_roots) < 3:
        topic_cleaned = re.sub(r"[^\w\s]", " ", topic.lower())
        primary_topic_tokens = [
            w for w in topic_cleaned.split()
            if len(w) >= 4 and w not in _TOPIC_STOPWORDS and w not in _GENERIC_CONTEXT_WORDS
        ]
        primary_topic_roots = {_canonical_root(w) for w in primary_topic_tokens}
        if primary_topic_roots:
            has_topic_anchor = any(
                _canonical_root(w) in primary_topic_roots
                for w in (text_words + heading_words)
            )
            if not has_topic_anchor:
                return False, set(), set()

    # 3. All matches in content (tokens/stems) for backward compatibility and scoring
    matched_content = {t for t in tokens if t in text_words_set} | {
        s for s in stems
        if any(w == s or (len(s) >= 4 and w.startswith(s)) for w in text_words_set)
    }

    # 4. Heading matches
    matched_heading = {t for t in tokens if t in heading_words_set} | {
        s for s in stems
        if any(w == s or (len(s) >= 4 and w.startswith(s)) for w in heading_words_set)
    }
    matched_specific_heading_roots = {
        r for w in heading_words
        for r in specific_roots
        if _canonical_root(w) == r or (len(r) >= 4 and w.startswith(r))
    }

    # 5. Check for adjacent topic token bigram in body (e.g. "habit formation", "basal ganglia")
    has_adjacent_token_phrase = False
    for i in range(len(text_words) - 1):
        w1, w2 = text_words[i], text_words[i + 1]
        w1_match = w1 in tokens or any(len(s) >= 4 and (w1 == s or w1.startswith(s)) for s in stems)
        w2_match = w2 in tokens or any(len(s) >= 4 and (w2 == s or w2.startswith(s)) for s in stems)
        if w1_match and w2_match and (w1 in specific_tokens or w2 in specific_tokens):
            has_adjacent_token_phrase = True
            break

    heading_substantive_words = {
        w for w in heading_words
        if len(w) >= 4 and w not in _TOPIC_STOPWORDS and w not in _GENERIC_CONTEXT_WORDS
    }
    shared_heading_body_words = heading_substantive_words & text_words_set

    # 6. Meaningful topical relevance decision
    # - If heading has specific topic root AND body shares substantive concepts with that heading
    if len(matched_specific_heading_roots) >= 1 and len(shared_heading_body_words) >= 1:
        is_relevant = True
    # - Must contain at least one specific body match (heading alone without body overlap NEVER qualifies)
    elif len(matched_specific_roots) == 0:
        is_relevant = False
    # - Multi-term specific body matches (>= 2 distinct anchor concepts)
    elif len(matched_specific_roots) >= 2:
        is_relevant = True
    # - Single specific anchor term, but validated by adjacent phrase
    elif has_adjacent_token_phrase:
        is_relevant = True
    # - Single specific anchor term, with repeated concentration (>= 2 occurrences in body)
    elif body_specific_occurrences >= 2:
        is_relevant = True
    # - Single specific anchor term confirmed by specific heading match AND contextual content
    elif len(matched_specific_heading_roots) >= 1 and len(matched_content) >= 2:
        is_relevant = True
    # - Concise substantive definition (<= 25 words) with anchor keyword
    elif len(text_words) <= 25 and len(matched_specific_roots) >= 1:
        is_relevant = True
    else:
        # Solitary incidental mention without phrase, repetition, or heading confirmation
        is_relevant = False

    return is_relevant, matched_content, matched_heading


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


class StartResearchRequest(BaseModel):
    """Request payload for initiating research planning and execution."""
    model_config = ConfigDict(extra="forbid")

    topic: str = Field(..., min_length=1, max_length=1000, description="Core topic to research.")
    target_duration_minutes: Optional[float] = Field(
        default=None, ge=0.1, le=60.0, description="Intended video duration in minutes if known."
    )
    user_guidance: Optional[str] = Field(
        default=None, max_length=1000, description="Optional user directives or focus points."
    )
    budget_limits: Optional[ResearchBudgetConstraints] = Field(
        default=None, description="Optional custom invocation and content ceilings."
    )


class StartResearchResponse(BaseModel):
    """Response returned upon initiating or completing research."""
    model_config = ConfigDict(extra="forbid")

    project_id: str
    plan_id: str
    run_id: str
    status: str
    termination_reason: Optional[str] = None
    search_calls_used: int = 0
    extract_calls_used: int = 0
    total_chars_extracted: int = 0
    evidence_count: int = 0
    sources_count: int = 0
    has_brief: bool = False
    overview: ResearchOverviewResponse


