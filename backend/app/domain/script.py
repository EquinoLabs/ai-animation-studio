"""Domain models and validation for Script Builder drafts and conversation history."""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Literal, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.domain.fact_check import ClaimAssessmentStatus, ClaimRiskLevel


def utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]+$")

SECTION_TYPES: List[str] = ["hook", "setup", "middle", "climax", "takeaway"]
ScriptStatus = Literal["draft", "final"]
ClaimLinkStatus = Literal["unlinked", "linked"]
DEFAULT_WORDS_PER_MINUTE = 140


def count_words(text: Optional[str]) -> int:
    """Return the number of spoken words in text."""
    if not text:
        return 0
    return len(text.split())


def estimate_duration_seconds(word_count: int, words_per_minute: int = DEFAULT_WORDS_PER_MINUTE) -> float:
    """Estimate spoken duration in seconds given word count and speaking rate (WPM)."""
    wpm = words_per_minute if words_per_minute > 0 else DEFAULT_WORDS_PER_MINUTE
    return round((word_count / wpm) * 60.0, 1)


def compute_duration_word_targets(
    target_duration_minutes: float, words_per_minute: int = DEFAULT_WORDS_PER_MINUTE
) -> Dict[str, Any]:
    """Compute soft word count targets and section allocations for a given target duration."""
    min_wpm = 120
    max_wpm = 150
    wpm = words_per_minute if words_per_minute > 0 else DEFAULT_WORDS_PER_MINUTE

    target_words = int(target_duration_minutes * wpm)
    min_words = int(target_duration_minutes * min_wpm)
    max_words = int(target_duration_minutes * max_wpm)

    # Section allocations: hook (12%), setup (20%), middle (36%), climax (20%), takeaway (12%)
    hook_words = max(35, int(target_words * 0.12))
    setup_words = max(50, int(target_words * 0.20))
    middle_words = max(80, int(target_words * 0.36))
    climax_words = max(50, int(target_words * 0.20))
    takeaway_words = max(35, int(target_words * 0.12))

    return {
        "target_words": target_words,
        "min_words": min_words,
        "max_words": max_words,
        "hook_words": hook_words,
        "setup_words": setup_words,
        "middle_words": middle_words,
        "climax_words": climax_words,
        "takeaway_words": takeaway_words,
    }


DurationComplianceStatus = Literal[
    "compliant",
    "under_target_evidence_deficit",
    "under_target_generator_shortfall",
    "under_target_undetermined",
]


class DurationComplianceReport(BaseModel):
    """Audited duration metrics, target comparison, and deficit reporting."""

    target_duration_minutes: float = Field(..., description="Target runtime in minutes")
    target_words: int = Field(..., description="Target word count based on baseline WPM")
    actual_words: int = Field(..., description="Actual spoken narration word count")
    min_target_words: int = Field(..., description="Minimum acceptable word count for target duration")
    max_target_words: int = Field(..., description="Maximum acceptable word count for target duration")
    deficit_words: int = Field(default=0, description="Word count deficit below minimum target (0 if compliant)")
    compliance_status: DurationComplianceStatus = Field(
        default="compliant", description="Audited duration compliance classification"
    )
    assessment_note: str = Field(
        default="", description="Explanatory assessment note on duration compliance and evidence constraints"
    )


def populate_script_duration_metrics(
    script: "Script",
    words_per_minute: int = DEFAULT_WORDS_PER_MINUTE,
    evidence_count: Optional[int] = None,
    evidence_records: Optional[List[Any]] = None,
    evidence_catalog: Optional[Any] = None,
) -> "Script":
    """Calculate and set target_duration_seconds on sections, estimated duration, and duration compliance audit."""
    total_words = 0
    for section in script.sections:
        words = count_words(section.narration)
        total_words += words
        section.target_duration_seconds = round(estimate_duration_seconds(words, words_per_minute))
    script.total_word_count = total_words
    script.estimated_duration_seconds = estimate_duration_seconds(total_words, words_per_minute)

    # Duration compliance audit
    wpm = words_per_minute if words_per_minute > 0 else DEFAULT_WORDS_PER_MINUTE
    target_words = int(script.target_duration_minutes * wpm)
    min_words = int(script.target_duration_minutes * 120)
    max_words = int(script.target_duration_minutes * 150)

    if total_words >= min_words:
        status: DurationComplianceStatus = "compliant"
        deficit = 0
        note = f"Script narration word count ({total_words} words) meets target duration range ({min_words}-{max_words} words)."
    else:
        deficit = min_words - total_words

        # Resolve available evidence records from catalog or explicit list
        records: Optional[List[Any]] = None
        if evidence_catalog is not None and hasattr(evidence_catalog, "evidence_by_id"):
            records = list(evidence_catalog.evidence_by_id.values())
        elif evidence_records is not None:
            records = list(evidence_records)

        if records is not None:
            total_records = len(records)
            distinct_sources = len({getattr(r, "source_id", None) for r in records if getattr(r, "source_id", None)})
            distinct_queries = len({getattr(r, "query_id", None) for r in records if getattr(r, "query_id", None)})
            total_excerpt_words = sum(len((getattr(r, "excerpt", "") or "").split()) for r in records)
            tokens = [w.lower() for r in records for w in re.findall(r"\b[a-zA-Z]{3,}\b", getattr(r, "excerpt", "") or "")]
            vocab_ratio = (len(set(tokens)) / max(1, len(tokens)))

            if total_records == 0 or total_excerpt_words == 0:
                status = "under_target_evidence_deficit"
                note = (
                    f"Script duration ({total_words} words) is below target {script.target_duration_minutes:.1f} minutes "
                    f"({min_words}-{max_words} words, deficit: {deficit} words). No usable verified research "
                    f"evidence was available to sustain the requested duration without introducing ungrounded filler."
                )
            elif distinct_sources <= 1 or distinct_queries <= 1 or total_excerpt_words < 60 or vocab_ratio < 0.35:
                status = "under_target_evidence_deficit"
                note = (
                    f"Script duration ({total_words} words) is below target {script.target_duration_minutes:.1f} minutes "
                    f"({min_words}-{max_words} words, deficit: {deficit} words). Available evidence has limited topic "
                    f"coverage or repetitive content ({total_records} record(s) across {distinct_sources} source(s) and "
                    f"{distinct_queries} query coverage, totaling {total_excerpt_words} excerpt words), providing insufficient "
                    f"diverse material to sustain target duration without ungrounded filler."
                )
            elif total_records >= 2 and distinct_sources >= 2 and distinct_queries >= 2 and total_excerpt_words >= 100:
                status = "under_target_generator_shortfall"
                note = (
                    f"Script duration ({total_words} words) is below target {script.target_duration_minutes:.1f} minutes "
                    f"({min_words}-{max_words} words, deficit: {deficit} words). Adequate substantive research evidence was "
                    f"available ({total_records} records across {distinct_sources} sources and {distinct_queries} queries, "
                    f"{total_excerpt_words} excerpt words), but generator produced a concise script without expanding into full target duration."
                )
            else:
                # Borderline evidence (e.g. 2 records from 2 sources but low word count or marginal coverage)
                status = "under_target_undetermined"
                note = (
                    f"Script duration ({total_words} words) is below target {script.target_duration_minutes:.1f} minutes "
                    f"({min_words}-{max_words} words, deficit: {deficit} words). Evidence coverage ({total_records} records, "
                    f"{distinct_sources} sources, {total_excerpt_words} words) is borderline, preventing confident attribution "
                    f"between research evidence constraints and generator brevity."
                )
        elif evidence_count is not None:
            # Fallback when only count is passed without record details
            if evidence_count <= 1:
                status = "under_target_evidence_deficit"
                note = (
                    f"Script duration ({total_words} words) is below target {script.target_duration_minutes:.1f} minutes "
                    f"({min_words}-{max_words} words, deficit: {deficit} words). Verified research evidence count "
                    f"({evidence_count}) is insufficient to sustain the requested duration without introducing ungrounded filler."
                )
            elif evidence_count >= 5 and total_words < (0.6 * min_words):
                status = "under_target_generator_shortfall"
                note = (
                    f"Script duration ({total_words} words) is below target {script.target_duration_minutes:.1f} minutes "
                    f"({min_words}-{max_words} words, deficit: {deficit} words). Model generated concise narrative "
                    f"despite {evidence_count} catalog evidence items."
                )
            else:
                status = "under_target_undetermined"
                note = (
                    f"Script duration ({total_words} words) is below target {script.target_duration_minutes:.1f} minutes "
                    f"({min_words}-{max_words} words, deficit: {deficit} words). Available evidence count ({evidence_count}) "
                    f"without content/coverage inspection is insufficient to definitively distinguish evidence deficit from generator shortfall."
                )
        else:
            # Neither records nor count provided: honest uncertainty
            status = "under_target_undetermined"
            note = (
                f"Script duration ({total_words} words) is below target {script.target_duration_minutes:.1f} minutes "
                f"({min_words}-{max_words} words, deficit: {deficit} words). Insufficient research metadata available "
                f"to determine whether deficit stems from research constraints or generator brevity."
            )

    script.duration_compliance = DurationComplianceReport(
        target_duration_minutes=script.target_duration_minutes,
        target_words=target_words,
        actual_words=total_words,
        min_target_words=min_words,
        max_target_words=max_words,
        deficit_words=deficit,
        compliance_status=status,
        assessment_note=note,
    )
    return script


class ScriptClaim(BaseModel):
    """A discrete factual assertion in a script section and its declared evidence links.

    Note on verification and validation boundaries:
    1. References declared (`link_status == 'linked'`):
       The claim declares one or more `evidence_ids`. This is purely syntactic within the
       domain contract and indicates that references have been declared. It does NOT prove
       or establish that the referenced records exist in storage.
    2. References validated (Service/Storage layer):
       The application/service layer confirms that declared `evidence_ids` correspond to
       actual, persisted `EvidenceRecord`s in `projects/<project_id>/research/evidence.json`.
       The domain contract does not depend on storage and does not perform storage lookups.
    3. Claim verified (Phase 5):
       Substantive assessment of whether the cited evidence accurately and adequately supports
       the factual assertion. Factual truth evaluation and verification are strictly governed
       by Phase 5 fact-checking.
    """

    claim_id: str = Field(
        default_factory=lambda: f"clm_{uuid.uuid4().hex[:8]}",
        description="Stable claim identifier",
    )
    claim_text: str = Field(
        ..., min_length=1, max_length=2000, description="The factual assertion made in the narration"
    )
    section_id: str = Field(
        ..., min_length=1, description="ID of the script section where this claim appears"
    )
    evidence_ids: List[str] = Field(
        default_factory=list, description="IDs of referenced evidence records"
    )
    source_ids: List[str] = Field(
        default_factory=list, description="IDs of referenced source records"
    )
    link_status: ClaimLinkStatus = Field(
        default="unlinked",
        description="Reference declaration status ('linked' if evidence references are declared/supplied, 'unlinked' otherwise). Does NOT imply that records exist in storage or that the claim is verified.",
    )
    assessment_status: Optional[ClaimAssessmentStatus] = Field(
        default=None,
        description="Optional substantive Phase 5 assessment status ('supported', 'partially_supported', 'conflicting', 'unsupported', 'not_checked', 'contradicted'). Kept distinct from link_status.",
    )
    risk_level: Optional[ClaimRiskLevel] = Field(
        default="normal",
        description="Optional risk tier for fact-checking scrutiny ('high', 'normal', 'creative_flexibility').",
    )

    @field_validator("claim_id")
    @classmethod
    def validate_claim_id(cls, v: str) -> str:
        clean = (v or "").strip()
        if not clean or not ID_REGEX.match(clean):
            raise ValueError(f"Invalid claim_id '{v}'. Must be alphanumeric with underscores or dashes.")
        return clean

    @field_validator("section_id")
    @classmethod
    def validate_section_id(cls, v: str) -> str:
        clean = (v or "").strip()
        if not clean or not ID_REGEX.match(clean):
            raise ValueError(f"Invalid section_id '{v}'. Must be alphanumeric with underscores or dashes.")
        return clean

    @field_validator("claim_text")
    @classmethod
    def validate_claim_text(cls, v: str) -> str:
        clean = (v or "").strip()
        if not clean:
            raise ValueError("claim_text cannot be empty or whitespace-only.")
        return clean

    @field_validator("evidence_ids", "source_ids")
    @classmethod
    def validate_id_list(cls, v: List[str]) -> List[str]:
        cleaned: List[str] = []
        for item in v:
            clean_item = (item or "").strip()
            if not clean_item or not ID_REGEX.match(clean_item):
                raise ValueError(f"Invalid identifier '{item}'. Must be alphanumeric with underscores or dashes.")
            cleaned.append(clean_item)
        return cleaned

    @model_validator(mode="before")
    @classmethod
    def sync_link_status(cls, data: Any) -> Any:
        if isinstance(data, dict):
            evidence_ids = data.get("evidence_ids")
            if evidence_ids and "link_status" not in data:
                data["link_status"] = "linked"
        return data


class ScriptSection(BaseModel):
    """A distinct structural narrative section suitable for later beat planning."""

    id: str = Field(default_factory=lambda: f"sec_{uuid.uuid4().hex[:8]}")
    type: str = Field(default="middle", description="hook, setup, middle, climax, or takeaway")
    heading: str = Field(default="", description="Section headline or topic anchor")
    narration: str = Field(description="Spoken narration text")
    notes: Optional[str] = Field(default=None, description="Optional scene planning suggestions")
    name: Optional[str] = Field(default=None, description="Backwards-compatible alias for type/heading")
    target_duration_seconds: Optional[float] = Field(
        default=None, description="Estimated section speaking runtime in seconds based on narration word count"
    )
    claim_ids: List[str] = Field(
        default_factory=list, description="List of claim IDs associated with this section"
    )
    outline_section_index: Optional[int] = Field(
        default=None, ge=0, description="0-based index of the corresponding outline section for provenance traceability"
    )

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        clean = (v or "").strip()
        if not clean:
            raise ValueError("Section id must be a non-empty string.")
        return clean

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        clean = (v or "").strip().lower()
        if clean not in SECTION_TYPES:
            raise ValueError(f"Invalid section role '{v}'. Allowed roles are: {SECTION_TYPES}")
        return clean

    @field_validator("claim_ids")
    @classmethod
    def validate_claim_ids(cls, v: List[str]) -> List[str]:
        cleaned: List[str] = []
        for item in v:
            clean_item = (item or "").strip()
            if not clean_item or not ID_REGEX.match(clean_item):
                raise ValueError(f"Invalid claim_id '{item}'. Must be alphanumeric with underscores or dashes.")
            cleaned.append(clean_item)
        return cleaned

    @model_validator(mode="before")
    @classmethod
    def populate_defaults_from_name(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Backwards compatibility: if 'name' was supplied without 'heading' or 'type'
            name_val = data.get("name")
            if name_val:
                if not data.get("heading"):
                    data["heading"] = str(name_val).capitalize()
                if not data.get("type"):
                    data["type"] = str(name_val).lower()
            if "visual_notes" in data and not data.get("notes"):
                data["notes"] = data["visual_notes"]
            elif "notes" in data and not data.get("visual_notes"):
                data["visual_notes"] = data["notes"]
        return data


class ScriptOutlineSection(BaseModel):
    """Outline specification for a single narrative section prior to full narration writing."""

    type: str = Field(default="middle", description="hook, setup, middle, climax, or takeaway")
    heading: str = Field(default="", description="Section headline or topic anchor")
    target_words: int = Field(..., gt=0, description="Positive soft target word count for this section")
    factual_milestones: List[str] = Field(
        default_factory=list, description="Core factual milestones or research points to cover"
    )
    visual_concept: Optional[str] = Field(
        default=None, description="2D visual and staging concept for this section"
    )
    evidence_ids: List[str] = Field(
        default_factory=list, description="Referenced evidence record IDs mapped to this section"
    )

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        clean = (v or "").strip().lower()
        if clean not in SECTION_TYPES:
            raise ValueError(f"Invalid section role '{v}'. Allowed roles are: {SECTION_TYPES}")
        return clean

    @field_validator("target_words")
    @classmethod
    def validate_target_words(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("target_words must be a positive integer greater than 0.")
        return v

    @field_validator("evidence_ids")
    @classmethod
    def validate_evidence_ids(cls, v: List[str]) -> List[str]:
        cleaned: List[str] = []
        for item in v:
            clean_item = (item or "").strip()
            if not clean_item or not ID_REGEX.match(clean_item):
                raise ValueError(f"Invalid evidence_id '{item}'. Must be alphanumeric with underscores or dashes.")
            cleaned.append(clean_item)
        return cleaned


# ---------------------------------------------------------------------------
# Generated Outline Payloads (Model Output Contracts)
# ---------------------------------------------------------------------------

class OutlineSectionPayload(BaseModel):
    """Raw outline section generated by the model before authoritative metadata assignment."""
    model_config = ConfigDict(extra="ignore")

    type: str = Field(..., description="Section type: hook, setup, middle, climax, or takeaway")
    heading: str = Field(..., min_length=1, description="Concise descriptive heading")
    target_words: int = Field(..., gt=0, description="Estimated target word count (> 0)")
    factual_milestones: List[str] = Field(default_factory=list, description="Key factual beats for this section")
    visual_concept: Optional[str] = Field(None, description="Visual staging / animation concept")
    evidence_ids: List[str] = Field(default_factory=list, description="Declared evidence references from catalog")

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        clean = (v or "").strip().lower()
        if clean not in SECTION_TYPES:
            raise ValueError(f"Invalid section type '{v}'. Allowed types are: {SECTION_TYPES}")
        return clean

    @field_validator("heading")
    @classmethod
    def validate_heading(cls, v: str) -> str:
        clean = (v or "").strip()
        if not clean:
            raise ValueError("Outline section heading cannot be empty or blank.")
        return clean

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def coerce_evidence_ids(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [str(item) for item in v if item is not None and str(item).strip()]
        return v


class GeneratedOutlinePayload(BaseModel):
    """Structured narrative outline payload returned by the LLM."""
    model_config = ConfigDict(extra="ignore")

    narrative_structure: str = Field(..., description="Narrative structure archetype")
    sections: List[OutlineSectionPayload] = Field(..., min_length=5, description="Planned outline sections (minimum 5)")

    @field_validator("narrative_structure")
    @classmethod
    def validate_structure(cls, v: str) -> str:
        clean = (v or "").strip()
        if not clean:
            raise ValueError("narrative_structure cannot be empty or blank.")
        return clean


# ---------------------------------------------------------------------------
# Generated Script Payloads (Model Output Contracts for Draft Generation)
# ---------------------------------------------------------------------------

class ScriptClaimPayload(BaseModel):
    """Raw claim emitted by the LLM before authoritative metadata assignment."""
    model_config = ConfigDict(extra="ignore")

    claim_text: str = Field(..., min_length=1, description="Factual assertion made in narration")
    section_id: Optional[str] = Field(default=None, description="Target section id (e.g., sec_01)")
    evidence_ids: List[str] = Field(default_factory=list, description="Referenced evidence record IDs from catalog")

    @field_validator("claim_text")
    @classmethod
    def validate_claim_text(cls, v: str) -> str:
        clean = (v or "").strip()
        if not clean:
            raise ValueError("claim_text cannot be empty or blank.")
        return clean

    @field_validator("section_id")
    @classmethod
    def validate_section_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        clean = (v or "").strip()
        return clean or None

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def coerce_evidence_ids(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [str(item) for item in v if item is not None and str(item).strip()]
        return v


class ScriptSectionPayload(BaseModel):
    """Raw script section emitted by the LLM before authoritative metadata assignment."""
    model_config = ConfigDict(extra="ignore")

    id: Optional[str] = None
    type: str = Field(..., description="Section role: hook, setup, middle, climax, or takeaway")
    heading: str = Field(..., min_length=1, description="Section heading")
    narration: str = Field(..., min_length=1, description="Spoken voiceover narration")
    notes: Optional[str] = Field(None, description="2D animation visual staging cues")

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        clean = (v or "").strip().lower()
        if clean not in SECTION_TYPES:
            raise ValueError(f"Invalid section type '{v}'. Allowed types are: {SECTION_TYPES}")
        return clean

    @field_validator("heading", "narration")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        clean = (v or "").strip()
        if not clean:
            raise ValueError("Field cannot be empty or blank.")
        return clean


class GeneratedScriptPayload(BaseModel):
    """Container payload emitted by model structured generation for a script draft."""
    model_config = ConfigDict(extra="ignore")

    title: str = Field(..., min_length=1, description="Working script title")
    sections: List[ScriptSectionPayload] = Field(..., min_length=1, description="Generated script sections")
    claims: List[ScriptClaimPayload] = Field(default_factory=list, description="Declared factual claims")


class GeneratedSectionPayload(BaseModel):
    """Container payload emitted when generating or expanding an individual script section."""
    model_config = ConfigDict(extra="ignore")

    heading: str = Field(..., min_length=1, description="Section heading")
    narration: str = Field(..., min_length=1, description="Spoken voiceover narration")
    notes: Optional[str] = Field(None, description="2D animation visual staging cues")
    claims: List[ScriptClaimPayload] = Field(default_factory=list, description="Factual claims declared in this section")

    @field_validator("heading", "narration")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        clean = (v or "").strip()
        if not clean:
            raise ValueError("Field cannot be empty or blank.")
        return clean


class ScriptOutline(BaseModel):
    """The structured narrative outline generated prior to full narration."""

    outline_id: str = Field(
        default_factory=lambda: f"out_{uuid.uuid4().hex[:8]}", description="Stable outline identifier"
    )
    project_id: str = Field(default="proj_default", description="Associated project identifier")
    topic: str = Field(default="", description="Source topic or user prompt")
    target_duration_minutes: float = Field(default=5.0, gt=0.0, le=60.0)
    narrative_structure: str = Field(
        default="chronological",
        description="Narrative approach (e.g. chronological, mechanism_breakdown, problem_solution)",
    )
    sections: List[ScriptOutlineSection] = Field(default_factory=list)
    research_run_id: Optional[str] = Field(
        default=None, description="Optional ID of the ResearchRun used for evidence-aware outline generation"
    )
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("outline_id", "project_id")
    @classmethod
    def validate_ids(cls, v: str) -> str:
        clean = (v or "").strip()
        if not clean or not ID_REGEX.match(clean):
            raise ValueError(f"Invalid identifier '{v}'. Must be alphanumeric with underscores or dashes.")
        return clean

    @field_validator("topic")
    @classmethod
    def validate_topic(cls, v: str) -> str:
        clean = (v or "").strip()
        if len(clean) > 1000:
            raise ValueError("Outline topic must not exceed 1000 characters.")
        return clean

    @field_validator("target_duration_minutes")
    @classmethod
    def validate_duration(cls, v: float) -> float:
        if v <= 0.0 or v > 60.0:
            raise ValueError("target_duration_minutes must be between 0.1 and 60.0 minutes.")
        return v

    @field_validator("research_run_id")
    @classmethod
    def validate_research_run_id(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            clean = v.strip()
            if not clean or not ID_REGEX.match(clean):
                raise ValueError(f"Invalid research_run_id '{v}'. Must be alphanumeric with underscores or dashes.")
            return clean
        return None


class Script(BaseModel):
    """Validated script draft or finalized artifact per TechSpec Section 4 & Task P1."""

    script_id: str = Field(default_factory=lambda: f"scr_{uuid.uuid4().hex[:12]}")
    project_id: str = Field(default="proj_default")
    version: int = Field(default=1, ge=1)
    status: ScriptStatus = Field(default="draft")
    title: str = Field(description="Short, human-readable script title")
    topic: str = Field(default="", description="Source topic or user prompt")
    target_duration_minutes: float = Field(default=5.0, gt=0.0, le=60.0)
    sections: List[ScriptSection] = Field(default_factory=list)
    total_word_count: Optional[int] = Field(
        default=None, description="Total spoken narration word count across all sections"
    )
    estimated_duration_seconds: Optional[float] = Field(
        default=None, description="Estimated total spoken runtime in seconds based on narration word count"
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    research_run_id: Optional[str] = Field(
        default=None, description="Optional ID of the ResearchRun used for evidence grounding"
    )
    evidence_brief_path: Optional[str] = Field(
        default=None, description="Optional project-relative path to evidence_brief.md"
    )
    claims: List[ScriptClaim] = Field(
        default_factory=list, description="Discrete factual assertions and their evidence links"
    )
    outline: Optional[ScriptOutline] = Field(
        default=None, description="Optional narrative outline used to structure this script"
    )
    duration_compliance: Optional[DurationComplianceReport] = Field(
        default=None, description="Optional audited duration metrics, target comparison, and deficit reporting"
    )

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        clean = (v or "").strip()
        if not clean:
            raise ValueError("Script title cannot be empty or whitespace-only.")
        if len(clean) > 200:
            raise ValueError("Script title must not exceed 200 characters.")
        return clean

    @field_validator("topic")
    @classmethod
    def validate_topic(cls, v: str) -> str:
        clean = (v or "").strip()
        if len(clean) > 1000:
            raise ValueError("Script topic must not exceed 1000 characters.")
        return clean

    @field_validator("target_duration_minutes")
    @classmethod
    def validate_duration(cls, v: float) -> float:
        if v <= 0.0 or v > 60.0:
            raise ValueError("target_duration_minutes must be between 0.1 and 60.0 minutes.")
        return v

    @field_validator("research_run_id")
    @classmethod
    def validate_research_run_id(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            clean = v.strip()
            if not clean or not ID_REGEX.match(clean):
                raise ValueError(f"Invalid research_run_id '{v}'. Must be alphanumeric with underscores or dashes.")
            return clean
        return None

    @model_validator(mode="after")
    def validate_script_structure(self) -> "Script":
        # Check unique section IDs
        seen_ids = set()
        for sec in self.sections:
            if sec.id in seen_ids:
                raise ValueError(f"Duplicate section ID '{sec.id}' found in script.")
            seen_ids.add(sec.id)

        # Validate claim to section references if claims exist
        if self.claims:
            seen_claim_ids = set()
            for claim in self.claims:
                if claim.claim_id in seen_claim_ids:
                    raise ValueError(f"Duplicate claim ID '{claim.claim_id}' found in script.")
                seen_claim_ids.add(claim.claim_id)

                if self.sections and claim.section_id not in seen_ids:
                    raise ValueError(
                        f"Claim '{claim.claim_id}' references unknown section_id '{claim.section_id}'."
                    )

            # Check that section claim_ids reference known claims
            for sec in self.sections:
                for cid in sec.claim_ids:
                    if cid not in seen_claim_ids:
                        raise ValueError(
                            f"Section '{sec.id}' references unknown claim_id '{cid}'."
                        )

        return self

    def validate_content(self) -> None:
        """Validate that script has non-empty sections and meaningful narrative text."""
        if not self.sections:
            raise ValueError("Script must contain at least one section.")
        for idx, sec in enumerate(self.sections):
            if not (sec.narration or "").strip():
                raise ValueError(f"Section {idx} ('{sec.heading or sec.type}') has empty or whitespace-only narration.")


# Backwards compatibility alias
Section = ScriptSection


ConversationEventType = Literal[
    "topic_submission",
    "outline_generated",
    "draft_generated",
    "revision_instruction",
    "revision_result",
    "draft_accepted",
    "draft_rejected",
    "script_finalized",
    "fact_challenge",
]


class ConversationEvent(BaseModel):
    """Structured conversation event recorded in project conversation history."""

    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    timestamp: datetime = Field(default_factory=utc_now)
    event_type: ConversationEventType
    content: Optional[str] = None
    script_version: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
