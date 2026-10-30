"""Domain models and data contracts for Phase 5 claim-level fact-checking and assessments."""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Literal, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]+$")

SUPPORTED_SCHEMA_VERSIONS = {"1.0.0"}

# Exactly the six authoritative statuses approved in roadmap & Project Lead decisions
ClaimAssessmentStatus = Literal[
    "supported",            # Evidence directly entails the factual assertion
    "partially_supported",  # Evidence supports part; needs qualification or narrower scope
    "conflicting",          # Gathered evidence records disagree with each other
    "unsupported",          # Gathered evidence does not contain facts addressing the claim
    "not_checked",          # Unresearched script, provider timeout, or unassessed claim
    "contradicted",         # Reliable gathered evidence directly refutes the assertion
]

CLAIM_ASSESSMENT_STATUSES: List[str] = [
    "supported",
    "partially_supported",
    "conflicting",
    "unsupported",
    "not_checked",
    "contradicted",
]

# Risk-based scrutiny levels per Section 2.2 of approved Phase 5 design
ClaimRiskLevel = Literal["high", "normal", "creative_flexibility"]

CLAIM_RISK_LEVELS: List[str] = ["high", "normal", "creative_flexibility"]

# Recommended repair action
RepairAction = Literal["keep", "qualify", "remove", "rephrase"]


# ---------------------------------------------------------------------------
# LLM Response Payload Contracts (Structured Output Boundary)
# ---------------------------------------------------------------------------

class ClaimAssessmentPayload(BaseModel):
    """Structured assessment of a single claim emitted by the LLM."""
    model_config = ConfigDict(extra="ignore")

    claim_id: str = Field(..., description="Target claim identifier (e.g., clm_01)")
    status: ClaimAssessmentStatus = Field(..., description="One of the six approved assessment statuses")
    explanation: str = Field(..., min_length=1, description="Clear, plain-language explanation of assessment")
    supporting_evidence_ids: List[str] = Field(
        default_factory=list, description="IDs of evidence directly confirming proposition"
    )
    contradicting_evidence_ids: List[str] = Field(
        default_factory=list, description="IDs of evidence refuting proposition"
    )
    contextual_evidence_ids: List[str] = Field(
        default_factory=list, description="IDs of evidence providing background"
    )
    recommended_action: RepairAction = Field(
        default="keep", description="Recommended action: keep, qualify, remove, rephrase"
    )

    @field_validator("claim_id")
    @classmethod
    def validate_claim_id(cls, v: str) -> str:
        clean = (v or "").strip()
        if not clean:
            raise ValueError("claim_id cannot be blank.")
        return clean

    @field_validator("explanation")
    @classmethod
    def validate_explanation(cls, v: str) -> str:
        clean = (v or "").strip()
        if not clean:
            raise ValueError("explanation cannot be blank.")
        return clean


class BatchFactCheckResponsePayload(BaseModel):
    """Structured batch response payload containing assessments for multiple claims."""
    model_config = ConfigDict(extra="ignore")

    assessments: List[ClaimAssessmentPayload] = Field(
        default_factory=list, description="List of claim assessments"
    )


# ---------------------------------------------------------------------------
# Claim Assessment Contract
# ---------------------------------------------------------------------------

class ClaimAssessment(BaseModel):
    """Substantive factual evaluation of a discrete ScriptClaim against gathered evidence."""
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(..., description="Target claim identifier (e.g., clm_01)")
    section_id: str = Field(..., description="Section where the claim appears (e.g., sec_01)")
    proposition: str = Field(..., min_length=1, max_length=2000, description="Exact factual assertion evaluated")
    status: ClaimAssessmentStatus = Field(..., description="Fact-check assessment status")
    risk_level: ClaimRiskLevel = Field(default="normal", description="Scrutiny tier for readiness determination")
    explanation: str = Field(..., min_length=1, max_length=2000, description="Plain-language justification of assessment")
    considered_evidence_ids: List[str] = Field(default_factory=list, description="All evidence record IDs evaluated")
    supporting_evidence_ids: List[str] = Field(default_factory=list, description="Evidence record IDs supporting the assertion")
    contradicting_evidence_ids: List[str] = Field(default_factory=list, description="Evidence record IDs refuting the assertion")
    contextual_evidence_ids: List[str] = Field(default_factory=list, description="Evidence record IDs providing background without resolving")
    unassessed_reason: Optional[str] = Field(default=None, description="Explicit reason if status is 'not_checked'")
    recommended_action: RepairAction = Field(default="keep", description="Recommended repair action")
    assessed_at: datetime = Field(default_factory=utc_now)

    @field_validator("claim_id", "section_id")
    @classmethod
    def validate_ids(cls, v: str) -> str:
        clean = (v or "").strip()
        if not clean or not ID_REGEX.match(clean):
            raise ValueError(f"Invalid identifier '{v}'. Must be alphanumeric with underscores or dashes.")
        return clean

    @field_validator("proposition", "explanation")
    @classmethod
    def validate_non_empty_text(cls, v: str) -> str:
        clean = (v or "").strip()
        if not clean:
            raise ValueError("Field cannot be empty or whitespace-only.")
        return clean

    @field_validator(
        "considered_evidence_ids",
        "supporting_evidence_ids",
        "contradicting_evidence_ids",
        "contextual_evidence_ids",
    )
    @classmethod
    def validate_evidence_id_list(cls, v: List[str]) -> List[str]:
        cleaned = []
        for item in v:
            c = (item or "").strip()
            if not c or not ID_REGEX.match(c):
                raise ValueError(f"Invalid evidence identifier '{item}'.")
            cleaned.append(c)
        return cleaned


# ---------------------------------------------------------------------------
# Script Fact-Check Report Contract
# ---------------------------------------------------------------------------

class ScriptFactCheckReport(BaseModel):
    """Authoritative fact-check report for a specific Script draft version."""
    model_config = ConfigDict(extra="forbid")

    report_id: str = Field(
        default_factory=lambda: f"fcr_{uuid.uuid4().hex[:12]}",
        description="Stable report identifier",
    )
    project_id: str = Field(..., description="Associated project identifier")
    script_version: int = Field(..., ge=1, description="Script version evaluated")
    research_run_id: Optional[str] = Field(default=None, description="Research run grounding the check")
    total_claims: int = Field(default=0, ge=0)
    supported_count: int = Field(default=0, ge=0)
    partially_supported_count: int = Field(default=0, ge=0)
    conflicting_count: int = Field(default=0, ge=0)
    unsupported_count: int = Field(default=0, ge=0)
    not_checked_count: int = Field(default=0, ge=0)
    contradicted_count: int = Field(default=0, ge=0)
    has_unresolved_high_risk: bool = Field(
        default=False,
        description="True if any high-risk claim remains unsupported, contradicted, conflicting, partially supported, or unassessed",
    )
    requires_repair: bool = Field(
        default=False,
        description="True if any claim is contradicted or partially supported, triggering a repair attempt",
    )
    is_ready: bool = Field(
        default=False,
        description="Application-authoritative readiness determination based on approved risk policies",
    )
    readiness_reason: Optional[str] = Field(
        default=None,
        description="Explanation of readiness status or blocking reasons",
    )
    assessments: List[ClaimAssessment] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    schema_version: str = "1.0.0"

    @field_validator("report_id", "project_id")
    @classmethod
    def validate_ids(cls, v: str) -> str:
        clean = (v or "").strip()
        if not clean or not ID_REGEX.match(clean):
            raise ValueError(f"Invalid identifier '{v}'. Must be alphanumeric with underscores or dashes.")
        return clean

    @field_validator("research_run_id")
    @classmethod
    def validate_optional_run_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        clean = v.strip()
        if not clean:
            return None
        if not ID_REGEX.match(clean):
            raise ValueError(f"Invalid research_run_id '{v}'.")
        return clean

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, v: str) -> str:
        if v not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"Unsupported schema_version '{v}'. Supported versions: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
            )
        return v

    def recompute_summary(self) -> None:
        """Derive summary counts and application-authoritative readiness determination per approved risk policy."""
        self.total_claims = len(self.assessments)
        self.supported_count = sum(1 for a in self.assessments if a.status == "supported")
        self.partially_supported_count = sum(1 for a in self.assessments if a.status == "partially_supported")
        self.conflicting_count = sum(1 for a in self.assessments if a.status == "conflicting")
        self.unsupported_count = sum(1 for a in self.assessments if a.status == "unsupported")
        self.not_checked_count = sum(1 for a in self.assessments if a.status == "not_checked")
        self.contradicted_count = sum(1 for a in self.assessments if a.status == "contradicted")

        # Contradicted or partially_supported claims of any risk level trigger repair
        self.requires_repair = bool(self.contradicted_count > 0 or self.partially_supported_count > 0)

        blocking_reasons: List[str] = []

        # High-risk checks: contradicted, partially_supported, unsupported, conflicting, not_checked
        high_risk_contradicted = [
            a for a in self.assessments if a.risk_level == "high" and a.status == "contradicted"
        ]
        if high_risk_contradicted:
            blocking_reasons.append(f"{len(high_risk_contradicted)} high-risk claim(s) contradicted")

        high_risk_partially_supported = [
            a for a in self.assessments if a.risk_level == "high" and a.status == "partially_supported"
        ]
        if high_risk_partially_supported:
            blocking_reasons.append(f"{len(high_risk_partially_supported)} high-risk claim(s) partially supported")

        high_risk_unsupported = [
            a for a in self.assessments if a.risk_level == "high" and a.status == "unsupported"
        ]
        if high_risk_unsupported:
            blocking_reasons.append(f"{len(high_risk_unsupported)} high-risk claim(s) unsupported")

        high_risk_conflicting = [
            a for a in self.assessments if a.risk_level == "high" and a.status == "conflicting"
        ]
        if high_risk_conflicting:
            blocking_reasons.append(f"{len(high_risk_conflicting)} high-risk claim(s) with conflicting evidence")

        high_risk_not_checked = [
            a for a in self.assessments
            if a.risk_level == "high" and a.status == "not_checked"
        ]
        if high_risk_not_checked:
            unresearched_high = [a for a in high_risk_not_checked if a.unassessed_reason == "unresearched_script"]
            other_high = [a for a in high_risk_not_checked if a.unassessed_reason != "unresearched_script"]
            reasons = []
            if other_high:
                reasons.append(f"{len(other_high)} high-risk claim(s) not checked")
            if unresearched_high:
                reasons.append(f"{len(unresearched_high)} unresearched high-risk claim(s) require research grounding")
            blocking_reasons.append("; ".join(reasons))

        self.has_unresolved_high_risk = bool(
            high_risk_contradicted or high_risk_partially_supported or
            high_risk_unsupported or high_risk_conflicting or high_risk_not_checked
        )

        # Normal-risk contradiction and unsupported tracking (must not be silently discarded)
        normal_risk_contradicted = [
            a for a in self.assessments if a.risk_level != "high" and a.status == "contradicted"
        ]
        flagged_warnings: List[str] = []
        if normal_risk_contradicted:
            flagged_warnings.append(f"{len(normal_risk_contradicted)} normal-risk/creative claim(s) contradicted")

        normal_risk_unsupported = [
            a for a in self.assessments if a.risk_level != "high" and a.status == "unsupported"
        ]
        if normal_risk_unsupported:
            flagged_warnings.append(f"{len(normal_risk_unsupported)} normal-risk claim(s) unsupported")

        unresearched_non_high = [
            a for a in self.assessments
            if a.status == "not_checked" and a.unassessed_reason == "unresearched_script" and a.risk_level != "high"
        ]
        if unresearched_non_high:
            flagged_warnings.append(f"{len(unresearched_non_high)} unresearched claim(s) (authorized unresearched mode)")

        if blocking_reasons:
            self.is_ready = False
            self.readiness_reason = "; ".join(blocking_reasons)
            if flagged_warnings:
                self.readiness_reason += f" (Flagged: {'; '.join(flagged_warnings)})"
        else:
            self.is_ready = True
            if flagged_warnings:
                self.readiness_reason = f"Ready with flagged items: {'; '.join(flagged_warnings)}"
            else:
                self.readiness_reason = "All claims verified or acceptable within risk guidelines."
