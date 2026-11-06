"""Surgical Claim Repair Engine (Phase 5 Checkpoint 3).

Applies targeted, evidence-grounded factual repairs to claims identified by
`ScriptFactChecker`, preserving unrelated sections, narrative voice, claim
identity where justified, and strict evidence provenance without recursive loops.
"""

from dataclasses import dataclass
from datetime import datetime
import json
import logging
import re
import string
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.config import Settings, get_settings
from backend.app.domain.fact_check import (
    ClaimAssessment,
    ClaimAssessmentStatus,
    ClaimRiskLevel,
    RepairAction,
    ScriptFactCheckReport,
    utc_now,
)
from backend.app.domain.script import (
    ID_REGEX,
    Script,
    ScriptClaim,
    ScriptSection,
    populate_script_duration_metrics,
)
from backend.app.llm.base import (
    LLMProvider,
    LLMProviderError,
    LLMResponseMalformedError,
    LLMUnavailableError,
)
from backend.app.llm.ollama import OllamaLLMProvider
from backend.app.services.scriptwriter.fact_checker import (
    ScriptFactChecker,
    ScriptFactCheckError,
    ScriptFactCheckUnavailableError,
)
from backend.app.services.scriptwriter.outliner import ValidatedEvidenceCatalog

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ScriptRepairError(Exception):
    """Base exception for all surgical script repair failures."""
    pass


class ScriptRepairSecurityError(ScriptRepairError):
    """Raised for project/run tampering or cross-project evidence citations."""
    pass


class ScriptRepairUnavailableError(ScriptRepairError):
    """Raised when LLM provider is unavailable during repair."""
    pass


class ScriptRepairMalformedError(ScriptRepairError):
    """Raised when LLM returns invalid JSON or schema violations."""
    pass


# ---------------------------------------------------------------------------
# Proposition Equivalence & Identity Helpers
# ---------------------------------------------------------------------------

_PROPOSITION_STOPWORDS = {
    "a", "an", "the", "in", "on", "at", "by", "for", "with", "about",
    "is", "are", "was", "were", "be", "been", "being", "that", "this",
    "to", "from", "of", "and", "or", "as", "it",
}


def _normalize_proposition(text: str) -> str:
    """Normalize proposition string by stripping punctuation and lowercasing."""
    if not text:
        return ""
    cleaned = text.lower()
    cleaned = cleaned.translate(str.maketrans(string.punctuation, " " * len(string.punctuation)))
    tokens = [t.strip() for t in cleaned.split() if t.strip()]
    return " ".join(tokens)


def _extract_content_tokens(text: str) -> List[str]:
    """Extract significant content tokens and numbers from proposition text."""
    norm = _normalize_proposition(text)
    return [t for t in norm.split() if t not in _PROPOSITION_STOPWORDS]


def _is_same_factual_proposition(text_a: str, text_b: str) -> bool:
    """Conservatively determine if two assertions express the exact same factual proposition."""
    norm_a = _normalize_proposition(text_a)
    norm_b = _normalize_proposition(text_b)
    if not norm_a or not norm_b:
        return False

    # 1. Exact normalized match
    if norm_a == norm_b:
        return True

    # 2. Compare numbers: any difference in numbers indicates a material change
    nums_a = re.findall(r"\b\d+(?:\.\d+)?\b", norm_a)
    nums_b = re.findall(r"\b\d+(?:\.\d+)?\b", norm_b)
    if nums_a != nums_b:
        return False

    # 3. Compare negations: any difference in negation words indicates contradiction
    negations = {"no", "not", "never", "neither", "nor", "none"}
    has_neg_a = any(n in norm_a.split() for n in negations)
    has_neg_b = any(n in norm_b.split() for n in negations)
    if has_neg_a != has_neg_b:
        return False

    # 4. Compare significant content tokens
    tokens_a = _extract_content_tokens(text_a)
    tokens_b = _extract_content_tokens(text_b)
    if tokens_a and tokens_a == tokens_b:
        return True

    return False


# ---------------------------------------------------------------------------
# Structured LLM Schemas for Surgical Section Repair
# ---------------------------------------------------------------------------

class ClaimRepairPayload(BaseModel):
    """Targeted repair for a single claim within a section."""
    model_config = ConfigDict(extra="ignore")

    claim_id: str = Field(..., description="Target claim ID being repaired (e.g. clm_01)")
    action: RepairAction = Field(..., description="Action taken: qualify, rephrase, remove, or keep")
    target_span: Optional[str] = Field(
        default=None, description="The exact verbatim sentence or text span in the original section narration"
    )
    replacement_span: Optional[str] = Field(
        default=None, description="The replacement sentence or text span to substitute (or empty/None if removing)"
    )
    revised_claim_text: Optional[str] = Field(
        default=None, description="The revised factual proposition if qualified/rephrased, or None if removed"
    )
    evidence_ids: List[str] = Field(
        default_factory=list, description="Permitted evidence IDs supporting the repaired proposition"
    )
    explanation: str = Field(
        ..., min_length=1, description="Explanation of how the repair addresses the assessment"
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


class SectionRepairPayload(BaseModel):
    """Structured surgical repair response for a single section."""
    model_config = ConfigDict(extra="ignore")

    section_id: str = Field(..., description="ID of the section being repaired")
    revised_narration: Optional[str] = Field(
        default=None, description="The complete revised narration for this section (optional if target_span used)"
    )
    repairs: List[ClaimRepairPayload] = Field(
        default_factory=list, description="List of repairs applied to target claims in this section"
    )

    @field_validator("section_id")
    @classmethod
    def validate_section_id(cls, v: str) -> str:
        clean = (v or "").strip()
        if not clean:
            raise ValueError("section_id cannot be blank.")
        return clean


# ---------------------------------------------------------------------------
# Repair Result Domain Objects
# ---------------------------------------------------------------------------

class ClaimRepairActionRecord(BaseModel):
    """Record of a surgical repair action taken on an individual claim."""
    model_config = ConfigDict(extra="forbid")

    original_claim_id: str
    repaired_claim_id: Optional[str] = None
    section_id: str
    action: RepairAction
    original_proposition: str
    repaired_proposition: Optional[str] = None
    original_status: ClaimAssessmentStatus
    new_status: Optional[ClaimAssessmentStatus] = None
    explanation: str
    is_resolved: bool = False


class ScriptRepairResult(BaseModel):
    """Intermediate typed result of a surgical repair and recheck pass."""
    model_config = ConfigDict(extra="forbid")

    repaired_script: Script
    fact_check_report: ScriptFactCheckReport
    repair_actions: List[ClaimRepairActionRecord] = Field(default_factory=list)
    attempted_claims_count: int = Field(default=0, ge=0)
    resolved_claims_count: int = Field(default=0, ge=0)
    unresolved_claims_count: int = Field(default=0, ge=0)
    is_fully_resolved: bool = False


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SECTION_REPAIR_SYSTEM_PROMPT = """You are an expert surgical script repair assistant for educational video scripts.
Your task is to repair ONLY the specified problematic factual claims within a single section of a script, while strictly preserving all other sentences, narrative flow, voice, and pacing.

SURGICAL REPAIR RULES:
1. MINIMAL EDITING: Modify ONLY the sentences containing the target claims. Do NOT rewrite, polish, or rephrase unrelated sentences in the section.
2. PRESERVE UNRELATED CONTENT: Keep all surrounding text, hooks, transitions, and questions intact.
3. REPAIR ACTIONS:
   - "qualify": If the claim is partially supported, narrow or qualify the assertion so that it is strictly substantiated by the permitted evidence.
   - "rephrase": If the claim is contradicted or unsupported, replace the false or ungrounded assertion with a verified factual statement drawn from the permitted evidence.
   - "remove": If no supported alternative exists in the permitted evidence, smoothly delete the false or unsupported factual assertion from the narration, ensuring surrounding text remains grammatically correct and coherent. (Set revised_claim_text=None and evidence_ids=[]).
   - "keep": If a creative expression requires no factual change, keep it.
4. STRICT EVIDENCE PROVENANCE:
   - You may ONLY cite evidence IDs that are listed under that target claim's 'PERMITTED EVIDENCE FOR REPAIR'.
   - Do NOT cite evidence IDs that belong to other claims or are not listed.
   - Do NOT fabricate or invent evidence IDs.
   - If a claim is removed, leave evidence_ids empty [].
5. OUTPUT SCHEMA:
   - Return valid JSON matching the SectionRepairPayload schema.
   - revised_narration must contain the complete, cohesive spoken narration for the section.
   - repairs must contain exactly one repair item for EVERY requested target claim ID in this section."""


# ---------------------------------------------------------------------------
# ScriptRepairer Service
# ---------------------------------------------------------------------------

class ScriptRepairer:
    """Executes single-pass surgical claim repairs and rechecks without recursive loops."""

    def __init__(
        self,
        llm_provider: Optional[LLMProvider] = None,
        fact_checker: Optional[ScriptFactChecker] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.llm_provider = llm_provider or OllamaLLMProvider(self.settings)
        self.fact_checker = fact_checker or ScriptFactChecker(
            llm_provider=self.llm_provider, settings=self.settings
        )

    def repair_script(
        self,
        script: Script,
        report: ScriptFactCheckReport,
        catalog: Optional[ValidatedEvidenceCatalog] = None,
        all_known_claim_ids: Optional[Set[str]] = None,
        fail_fast: bool = False,
    ) -> ScriptRepairResult:
        """Execute a single-pass surgical repair on claims requiring repair, followed by a targeted recheck.

        Args:
            script: The original Script draft to repair.
            report: The ScriptFactCheckReport evaluating the draft.
            catalog: The run's ValidatedEvidenceCatalog.
            all_known_claim_ids: Optional set of all known claim IDs across project history.
            fail_fast: If True, re-raises provider or validation errors immediately.
        """
        # 1. Security and provenance validation
        if report.project_id != script.project_id:
            raise ScriptRepairSecurityError(
                f"Report project_id '{report.project_id}' does not match script project_id '{script.project_id}'."
            )
        if report.script_version != script.version:
            raise ScriptRepairSecurityError(
                f"Report script_version {report.script_version} does not match script version {script.version}."
            )
        if script.research_run_id and catalog:
            if catalog.run_id != script.research_run_id:
                raise ScriptRepairSecurityError(
                    f"Catalog run_id '{catalog.run_id}' does not match script research_run_id '{script.research_run_id}'."
                )

        # 2. Identify claims requiring repair under approved policy
        target_assessments: Dict[str, ClaimAssessment] = {}
        for a in report.assessments:
            if a.status in ("contradicted", "partially_supported", "unsupported"):
                target_assessments[a.claim_id] = a

        if not target_assessments:
            logger.info("No claims in script require repair under approved policy.")
            return ScriptRepairResult(
                repaired_script=script.model_copy(deep=True),
                fact_check_report=report,
                repair_actions=[],
                attempted_claims_count=0,
                resolved_claims_count=0,
                unresolved_claims_count=0,
                is_fully_resolved=report.is_ready,
            )

        # Group target claims by section
        claims_by_id = {c.claim_id: c for c in script.claims}
        target_claims_by_sec: Dict[str, List[ScriptClaim]] = {}
        for cid in target_assessments:
            claim = claims_by_id.get(cid)
            if not claim:
                continue
            target_claims_by_sec.setdefault(claim.section_id, []).append(claim)

        # Initialize tracking for ID allocation
        known_ids: Set[str] = set(all_known_claim_ids or [c.claim_id for c in script.claims])

        # 3. Process each affected section surgically (single pass, no recursion)
        sections_by_id = {s.id: s.model_copy(deep=True) for s in script.sections}
        repaired_claims_by_sec: Dict[str, List[ScriptClaim]] = {}
        repair_action_records: List[ClaimRepairActionRecord] = []
        changed_claim_ids: Set[str] = set()

        for sec_id, sec_target_claims in target_claims_by_sec.items():
            section = sections_by_id.get(sec_id)
            if not section:
                continue

            sec_result = self._repair_section(
                section=section,
                target_claims=sec_target_claims,
                target_assessments=target_assessments,
                catalog=catalog,
                known_ids=known_ids,
                fail_fast=fail_fast,
            )

            # Update section narration
            section.narration = sec_result["revised_narration"]

            # Collect repaired claims and action records
            repaired_claims_by_sec[sec_id] = sec_result["repaired_claims"]
            repair_action_records.extend(sec_result["action_records"])
            changed_claim_ids.update(sec_result["changed_claim_ids"])

        # 4. Assemble the complete repaired Script draft
        repaired_sections: List[ScriptSection] = []
        repaired_claims: List[ScriptClaim] = []

        for s in script.sections:
            sec_copy = sections_by_id[s.id]
            repaired_sections.append(sec_copy)

        for c in script.claims:
            if c.claim_id not in target_assessments:
                # Unrelated, untouched claim
                repaired_claims.append(c.model_copy(deep=True))
            else:
                # Claim was targeted for repair; its replacement or omission was handled in section repair
                pass

        # Append all claims produced by section repairs
        for sec_id, claims_list in repaired_claims_by_sec.items():
            repaired_claims.extend(claims_list)

        # Rebuild bidirectional section-to-claim mappings
        for sec in repaired_sections:
            sec.claim_ids = [c.claim_id for c in repaired_claims if c.section_id == sec.id]

        repaired_script = script.model_copy(deep=True)
        repaired_script.sections = repaired_sections
        repaired_script.claims = repaired_claims
        repaired_script = populate_script_duration_metrics(
            repaired_script,
            words_per_minute=getattr(self.llm_provider, "words_per_minute", 140),
            evidence_catalog=catalog,
        )

        # 5. Targeted recheck of changed or replaced claims
        final_report = self._recheck_repaired_claims(
            original_script=script,
            repaired_script=repaired_script,
            original_report=report,
            changed_claim_ids=changed_claim_ids,
            repair_action_records=repair_action_records,
            catalog=catalog,
        )

        # Compute resolution metrics
        resolved_count = sum(1 for r in repair_action_records if r.is_resolved)
        unresolved_count = len(repair_action_records) - resolved_count
        is_fully_resolved = final_report.is_ready and (unresolved_count == 0)

        return ScriptRepairResult(
            repaired_script=repaired_script,
            fact_check_report=final_report,
            repair_actions=repair_action_records,
            attempted_claims_count=len(repair_action_records),
            resolved_claims_count=resolved_count,
            unresolved_claims_count=unresolved_count,
            is_fully_resolved=is_fully_resolved,
        )

    def _repair_section(
        self,
        section: ScriptSection,
        target_claims: List[ScriptClaim],
        target_assessments: Dict[str, ClaimAssessment],
        catalog: Optional[ValidatedEvidenceCatalog],
        known_ids: Set[str],
        fail_fast: bool,
    ) -> Dict[str, Any]:
        """Surgically repair target claims within a single section with application validation."""
        # Determine permitted evidence supplied for each target claim
        supplied_eids_by_claim: Dict[str, Set[str]] = {}
        valid_cat_eids = catalog.valid_evidence_ids if catalog else set()

        for c in target_claims:
            assessment = target_assessments[c.claim_id]
            # For partially supported: claim's existing considered evidence
            # For contradicted/unsupported: candidate alternative evidence from run catalog
            if assessment.status == "partially_supported":
                eids = set(c.evidence_ids) | set(assessment.considered_evidence_ids)
                supplied = eids & valid_cat_eids
            else:
                # Contradicted or unsupported: supply all catalog evidence as alternatives
                supplied = set(valid_cat_eids)
            supplied_eids_by_claim[c.claim_id] = supplied

        # Build prompt
        prompt = self._build_section_repair_prompt(
            section=section,
            target_claims=target_claims,
            target_assessments=target_assessments,
            catalog=catalog,
            supplied_eids_by_claim=supplied_eids_by_claim,
        )

        try:
            payload = self._invoke_section_repair_with_retry(
                prompt=prompt,
                section=section,
                target_claims=target_claims,
                catalog=catalog,
                supplied_eids_by_claim=supplied_eids_by_claim,
            )
            return self._apply_section_repairs(
                payload=payload,
                section=section,
                target_claims=target_claims,
                target_assessments=target_assessments,
                catalog=catalog,
                known_ids=known_ids,
            )
        except Exception as exc:
            if fail_fast:
                raise
            logger.warning(
                "Section '%s' repair failed (%s: %s). Preserving original narration and claims as unresolved.",
                section.id,
                type(exc).__name__,
                exc,
            )
            # Fallback: retain original section narration and original claims
            action_records: List[ClaimRepairActionRecord] = []
            for c in target_claims:
                orig_a = target_assessments[c.claim_id]
                action_records.append(
                    ClaimRepairActionRecord(
                        original_claim_id=c.claim_id,
                        repaired_claim_id=c.claim_id,
                        section_id=section.id,
                        action="keep",
                        original_proposition=c.claim_text,
                        repaired_proposition=c.claim_text,
                        original_status=orig_a.status,
                        new_status=orig_a.status,
                        explanation=f"Repair failed: {exc}",
                        is_resolved=False,
                    )
                )
            return {
                "revised_narration": section.narration,
                "repaired_claims": [c.model_copy(deep=True) for c in target_claims],
                "action_records": action_records,
                "changed_claim_ids": set(),
            }

    def _build_section_repair_prompt(
        self,
        section: ScriptSection,
        target_claims: List[ScriptClaim],
        target_assessments: Dict[str, ClaimAssessment],
        catalog: Optional[ValidatedEvidenceCatalog],
        supplied_eids_by_claim: Dict[str, Set[str]],
    ) -> str:
        """Construct surgical prompt for a single section and its target claims."""
        lines = [
            SECTION_REPAIR_SYSTEM_PROMPT,
            "",
            "=== SECTION SURGICAL REPAIR REQUEST ===",
            f"SECTION ID: {section.id}",
            f"SECTION HEADING: {section.heading or section.type}",
            "CURRENT SECTION NARRATION:",
            f'"{section.narration}"',
            "",
            f"TARGET CLAIMS TO REPAIR IN THIS SECTION ({len(target_claims)}):",
        ]

        for idx, c in enumerate(target_claims, 1):
            assessment = target_assessments[c.claim_id]
            supplied_ids = supplied_eids_by_claim.get(c.claim_id, set())

            lines.append(f"--- CLAIM {idx} of {len(target_claims)} ---")
            lines.append(f"CLAIM ID: {c.claim_id}")
            lines.append(f'CURRENT PROPOSITION: "{c.claim_text}"')
            lines.append(f"RISK LEVEL: {c.risk_level}")
            lines.append(f"FACT-CHECK STATUS: {assessment.status}")
            lines.append(f'ASSESSMENT EXPLANATION: "{assessment.explanation}"')
            lines.append("PERMITTED EVIDENCE FOR REPAIR:")

            if supplied_ids and catalog:
                for eid in sorted(list(supplied_ids)):
                    ev = catalog.evidence_by_id.get(eid)
                    if not ev:
                        continue
                    source = catalog.sources_by_id.get(ev.source_id)
                    source_title = source.title if source else "Source"
                    excerpt = (ev.excerpt or "").strip().replace("\n", " ")
                    lines.append(f"- ID: {eid} | Source: {source_title} | Excerpt: \"{excerpt}\"")
            else:
                lines.append("- (No verified research evidence available for this claim. Choose action='remove').")
            lines.append("")

        lines.append("=== END REQUEST ===")
        lines.append("Return valid JSON matching SectionRepairPayload.")
        return "\n".join(lines)

    def _invoke_section_repair_with_retry(
        self,
        prompt: str,
        section: ScriptSection,
        target_claims: List[ScriptClaim],
        catalog: Optional[ValidatedEvidenceCatalog],
        supplied_eids_by_claim: Dict[str, Set[str]],
    ) -> SectionRepairPayload:
        """Call LLM provider and execute at most one bounded corrective retry if validation fails."""
        try:
            payload: SectionRepairPayload = self.llm_provider.generate_json(
                prompt=prompt,
                schema=SectionRepairPayload,
            )
        except (LLMUnavailableError, TimeoutError, OSError) as conn_err:
            raise ScriptRepairUnavailableError(f"LLM provider error: {conn_err}") from conn_err
        except (LLMResponseMalformedError, json.JSONDecodeError, Exception) as malformed:
            logger.warning("LLM emitted malformed section repair JSON. Executing corrective retry.")
            return self._execute_section_corrective_retry(
                prompt=prompt,
                validation_error=f"Malformed JSON response: {malformed}",
                section=section,
                target_claims=target_claims,
                catalog=catalog,
                supplied_eids_by_claim=supplied_eids_by_claim,
            )

        is_valid, error_msg = self._validate_section_repair_response(
            payload=payload,
            section=section,
            target_claims=target_claims,
            catalog=catalog,
            supplied_eids_by_claim=supplied_eids_by_claim,
        )

        if is_valid:
            return payload

        logger.warning("Section repair validation failed: %s. Executing corrective retry.", error_msg)
        return self._execute_section_corrective_retry(
            prompt=prompt,
            validation_error=error_msg or "Validation failed",
            section=section,
            target_claims=target_claims,
            catalog=catalog,
            supplied_eids_by_claim=supplied_eids_by_claim,
        )

    def _execute_section_corrective_retry(
        self,
        prompt: str,
        validation_error: str,
        section: ScriptSection,
        target_claims: List[ScriptClaim],
        catalog: Optional[ValidatedEvidenceCatalog],
        supplied_eids_by_claim: Dict[str, Set[str]],
    ) -> SectionRepairPayload:
        """Perform exactly one bounded corrective retry with explicit error feedback."""
        expected_ids = sorted([c.claim_id for c in target_claims])
        allowed_ids_summary = {
            cid: sorted(list(eids)) for cid, eids in supplied_eids_by_claim.items()
        }

        retry_prompt = (
            f"{prompt}\n\n"
            f"--- CORRECTION REQUIRED ---\n"
            f"Your previous repair response was rejected due to errors:\n"
            f"ERROR: {validation_error}\n\n"
            f"STRICT CORRECTIVE REQUIREMENTS:\n"
            f"1. section_id must be exactly '{section.id}'.\n"
            f"2. repairs must contain exactly one repair item for each of these claim IDs: {expected_ids}.\n"
            f"3. For each claim, you may ONLY cite permitted evidence IDs:\n"
            f"   Permitted evidence mapping: {allowed_ids_summary}\n"
            f"4. If removing a claim, set revised_claim_text=null and evidence_ids=[].\n"
            f"5. Output valid JSON adhering to SectionRepairPayload.\n"
            f"--- END CORRECTION REQUIRED ---"
        )

        try:
            retry_payload: SectionRepairPayload = self.llm_provider.generate_json(
                prompt=retry_prompt,
                schema=SectionRepairPayload,
            )
        except (LLMUnavailableError, TimeoutError, OSError) as conn_err:
            raise ScriptRepairUnavailableError(f"LLM provider error on retry: {conn_err}") from conn_err
        except (LLMResponseMalformedError, json.JSONDecodeError, Exception) as malformed:
            raise ScriptRepairMalformedError(f"LLM generated malformed response on retry: {malformed}") from malformed

        is_valid, error_msg = self._validate_section_repair_response(
            payload=retry_payload,
            section=section,
            target_claims=target_claims,
            catalog=catalog,
            supplied_eids_by_claim=supplied_eids_by_claim,
        )

        if not is_valid:
            raise ScriptRepairMalformedError(f"Corrective retry failed validation: {error_msg}")

        return retry_payload

    def _validate_section_repair_response(
        self,
        payload: SectionRepairPayload,
        section: ScriptSection,
        target_claims: List[ScriptClaim],
        catalog: Optional[ValidatedEvidenceCatalog],
        supplied_eids_by_claim: Dict[str, Set[str]],
    ) -> Tuple[bool, Optional[str]]:
        """Validate surgical repair payload against strict application constraints."""
        if not payload:
            return False, "Payload is empty."

        # 1. Section ID match
        if payload.section_id != section.id:
            return False, f"section_id '{payload.section_id}' does not match target section '{section.id}'."

        # 2. Exactly 1-to-1 claim repair matching
        expected_ids = {c.claim_id for c in target_claims}
        returned_ids = [r.claim_id for r in payload.repairs]

        if len(returned_ids) != len(set(returned_ids)):
            dupes = [cid for cid in set(returned_ids) if returned_ids.count(cid) > 1]
            return False, f"Duplicate repair items returned for claim IDs: {sorted(dupes)}"

        returned_set = set(returned_ids)
        missing = expected_ids - returned_set
        extra = returned_set - expected_ids

        id_errors = []
        if missing:
            id_errors.append(f"Missing repair items for claim IDs: {sorted(list(missing))}")
        if extra:
            id_errors.append(f"Unknown or fabricated claim IDs in repair items: {sorted(list(extra))}")
        if id_errors:
            return False, "; ".join(id_errors)

        # 3. Validate repair items and evidence provenance
        valid_cat_eids = catalog.valid_evidence_ids if catalog else set()

        for r in payload.repairs:
            if r.action not in ("qualify", "rephrase", "remove", "keep"):
                return False, f"Invalid repair action '{r.action}' on claim '{r.claim_id}'."

            # Exact-span requirements:
            # Reject repairs where target_span is empty or missing
            target = r.target_span or ""
            if not target.strip():
                return False, f"Claim '{r.claim_id}' is missing mandatory target_span."

            if target not in section.narration and target.strip() in section.narration:
                target = target.strip()

            count = section.narration.count(target)
            if count == 0:
                return False, f"Claim '{r.claim_id}' target_span '{target}' does not occur in section narration."
            if count > 1:
                return False, f"Claim '{r.claim_id}' target_span '{target}' occurs {count} times in section narration (ambiguous span)."

            if r.action in ("qualify", "rephrase"):
                if not r.replacement_span or not r.replacement_span.strip():
                    return False, f"Claim '{r.claim_id}' has action '{r.action}' but replacement_span is empty."
                if not r.revised_claim_text or not r.revised_claim_text.strip():
                    return False, f"Claim '{r.claim_id}' has action '{r.action}' but revised_claim_text is empty."

            # Check evidence citations
            supplied_for_claim = supplied_eids_by_claim.get(r.claim_id, set())
            cited_eids = set(r.evidence_ids)

            foreign_or_fabricated = cited_eids - valid_cat_eids
            if foreign_or_fabricated:
                return (
                    False,
                    f"Claim '{r.claim_id}' cited unknown or fabricated evidence IDs: {sorted(list(foreign_or_fabricated))}",
                )

            unsupplied = cited_eids - supplied_for_claim
            if unsupplied:
                return (
                    False,
                    f"Claim '{r.claim_id}' cited evidence IDs not supplied for repair: {sorted(list(unsupplied))}",
                )

        return True, None

    def _apply_section_repairs(
        self,
        payload: SectionRepairPayload,
        section: ScriptSection,
        target_claims: List[ScriptClaim],
        target_assessments: Dict[str, ClaimAssessment],
        catalog: Optional[ValidatedEvidenceCatalog],
        known_ids: Set[str],
    ) -> Dict[str, Any]:
        """Apply validated repair payload to create new ScriptClaim objects and action records."""
        repaired_claims: List[ScriptClaim] = []
        action_records: List[ClaimRepairActionRecord] = []
        changed_claim_ids: Set[str] = set()

        claims_by_id = {c.claim_id: c for c in target_claims}

        # 1. Collect candidate spans for modify/remove actions
        span_candidates = []
        for r in payload.repairs:
            target = r.target_span or ""
            if target not in section.narration and target.strip() in section.narration:
                target = target.strip()

            count = section.narration.count(target)
            if count == 1 and r.action in ("qualify", "rephrase", "remove"):
                start = section.narration.find(target)
                end = start + len(target)
                span_candidates.append({
                    "repair": r,
                    "target": target,
                    "start": start,
                    "end": end,
                })

        # 2. Identify overlapping spans
        overlapping_claim_ids: Set[str] = set()
        for i in range(len(span_candidates)):
            for j in range(i + 1, len(span_candidates)):
                cand_i = span_candidates[i]
                cand_j = span_candidates[j]
                if max(cand_i["start"], cand_j["start"]) < min(cand_i["end"], cand_j["end"]):
                    overlapping_claim_ids.add(cand_i["repair"].claim_id)
                    overlapping_claim_ids.add(cand_j["repair"].claim_id)

        # 3. Filter non-overlapping repairs and sort descending by start offset (right-to-left)
        valid_spans = [
            c for c in span_candidates if c["repair"].claim_id not in overlapping_claim_ids
        ]
        valid_spans.sort(key=lambda x: x["start"], reverse=True)

        # 4. Apply non-overlapping substitutions right-to-left
        working_narration = section.narration
        for item in valid_spans:
            r = item["repair"]
            s = item["start"]
            e = item["end"]
            replacement = (r.replacement_span or "").strip()
            working_narration = working_narration[:s] + replacement + working_narration[e:]

        # Clean up spacing artifacts (e.g. duplicate spaces, orphan punctuation)
        working_narration = re.sub(r" +", " ", working_narration)
        working_narration = re.sub(r" ([.,!?;:])", r"\1", working_narration)
        working_narration = re.sub(r"\.{2,}", ".", working_narration).strip()

        # 5. Process claims and action records
        for r in payload.repairs:
            orig_claim = claims_by_id[r.claim_id]
            orig_assessment = target_assessments[r.claim_id]

            target = r.target_span or ""
            if target not in section.narration and target.strip() in section.narration:
                target = target.strip()
            count = section.narration.count(target)

            if count != 1:
                # Unambiguous target_span could not be located in narration
                action_records.append(
                    ClaimRepairActionRecord(
                        original_claim_id=orig_claim.claim_id,
                        repaired_claim_id=orig_claim.claim_id,
                        section_id=section.id,
                        action="keep",
                        original_proposition=orig_claim.claim_text,
                        repaired_proposition=orig_claim.claim_text,
                        original_status=orig_assessment.status,
                        new_status=orig_assessment.status,
                        explanation=f"Repair failed: target_span '{target}' could not be located unambiguously in section narration (count={count}).",
                        is_resolved=False,
                    )
                )
                repaired_claims.append(orig_claim.model_copy(deep=True))
                continue

            if r.claim_id in overlapping_claim_ids:
                # Overlapping span: rejected
                action_records.append(
                    ClaimRepairActionRecord(
                        original_claim_id=orig_claim.claim_id,
                        repaired_claim_id=orig_claim.claim_id,
                        section_id=section.id,
                        action="keep",
                        original_proposition=orig_claim.claim_text,
                        repaired_proposition=orig_claim.claim_text,
                        original_status=orig_assessment.status,
                        new_status=orig_assessment.status,
                        explanation="Repair rejected: target_span overlaps with another claim repair in the section.",
                        is_resolved=False,
                    )
                )
                repaired_claims.append(orig_claim.model_copy(deep=True))
                continue

            if r.action == "keep":
                action_records.append(
                    ClaimRepairActionRecord(
                        original_claim_id=orig_claim.claim_id,
                        repaired_claim_id=orig_claim.claim_id,
                        section_id=section.id,
                        action="keep",
                        original_proposition=orig_claim.claim_text,
                        repaired_proposition=orig_claim.claim_text,
                        original_status=orig_assessment.status,
                        new_status=orig_assessment.status,
                        explanation=r.explanation.strip(),
                        is_resolved=True,
                    )
                )
                repaired_claims.append(orig_claim.model_copy(deep=True))
                continue

            if r.action == "remove":
                action_records.append(
                    ClaimRepairActionRecord(
                        original_claim_id=orig_claim.claim_id,
                        repaired_claim_id=None,
                        section_id=section.id,
                        action="remove",
                        original_proposition=orig_claim.claim_text,
                        repaired_proposition=None,
                        original_status=orig_assessment.status,
                        new_status=None,
                        explanation=r.explanation.strip(),
                        is_resolved=True,
                    )
                )
                continue

            # qualify or rephrase
            revised_text = (r.revised_claim_text or "").strip()
            same_proposition = _is_same_factual_proposition(revised_text, orig_claim.claim_text)
            if same_proposition:
                assigned_id = orig_claim.claim_id
            else:
                assigned_id = self._allocate_claim_id(known_ids)

            changed_claim_ids.add(assigned_id)

            derived_sources: List[str] = []
            if catalog:
                derived_sources = list(
                    dict.fromkeys(
                        catalog.evidence_by_id[eid].source_id
                        for eid in r.evidence_ids
                        if eid in catalog.evidence_by_id
                    )
                )

            new_claim = ScriptClaim(
                claim_id=assigned_id,
                claim_text=revised_text,
                section_id=section.id,
                evidence_ids=sorted(list(set(r.evidence_ids))),
                source_ids=derived_sources,
                link_status="linked" if r.evidence_ids else "unlinked",
                risk_level=orig_claim.risk_level,
            )
            repaired_claims.append(new_claim)

            action_records.append(
                ClaimRepairActionRecord(
                    original_claim_id=orig_claim.claim_id,
                    repaired_claim_id=assigned_id,
                    section_id=section.id,
                    action=r.action,
                    original_proposition=orig_claim.claim_text,
                    repaired_proposition=revised_text,
                    original_status=orig_assessment.status,
                    new_status=None,
                    explanation=r.explanation.strip(),
                    is_resolved=False,
                )
            )

        # Exact span substitution: working_narration is final; NO fallback to payload.revised_narration
        return {
            "revised_narration": working_narration,
            "repaired_claims": repaired_claims,
            "action_records": action_records,
            "changed_claim_ids": changed_claim_ids,
        }

    def _allocate_claim_id(self, all_known_ids: Set[str]) -> str:
        """Deterministically allocate a new unique claim ID without collisions."""
        max_num = 0
        for cid in all_known_ids:
            match = re.match(r"^clm_(\d+)$", cid)
            if match:
                try:
                    num = int(match.group(1))
                    if num > max_num:
                        max_num = num
                except ValueError:
                    pass
        next_num = max_num + 1
        new_id = f"clm_{next_num:02d}"
        while new_id in all_known_ids:
            next_num += 1
            new_id = f"clm_{next_num:02d}"
        all_known_ids.add(new_id)
        return new_id

    def _recheck_repaired_claims(
        self,
        original_script: Script,
        repaired_script: Script,
        original_report: ScriptFactCheckReport,
        changed_claim_ids: Set[str],
        repair_action_records: List[ClaimRepairActionRecord],
        catalog: Optional[ValidatedEvidenceCatalog],
    ) -> ScriptFactCheckReport:
        """Execute a targeted recheck on changed/replaced claims and assemble the updated report."""
        final_assessments: List[ClaimAssessment] = []
        original_assessments_by_id = {a.claim_id: a for a in original_report.assessments}

        # 1. Preserve assessments for unchanged claims
        for claim in repaired_script.claims:
            if claim.claim_id not in changed_claim_ids:
                orig_a = original_assessments_by_id.get(claim.claim_id)
                if orig_a:
                    final_assessments.append(orig_a.model_copy(deep=True))

        # 2. Targeted recheck of changed claims
        changed_claims = [c for c in repaired_script.claims if c.claim_id in changed_claim_ids]
        rechecked_assessments_by_id: Dict[str, ClaimAssessment] = {}

        if changed_claims:
            changed_claim_id_set = {c.claim_id for c in changed_claims}
            mini_sections = []
            for s in repaired_script.sections:
                sec_copy = s.model_copy(deep=True)
                sec_copy.claim_ids = [cid for cid in sec_copy.claim_ids if cid in changed_claim_id_set]
                mini_sections.append(sec_copy)

            mini_script = Script(
                script_id=f"recheck_{repaired_script.script_id}",
                project_id=repaired_script.project_id,
                version=repaired_script.version,
                title=repaired_script.title,
                sections=mini_sections,
                claims=changed_claims,
                research_run_id=repaired_script.research_run_id,
            )

            try:
                recheck_sub_report = self.fact_checker.check_script(
                    script=mini_script,
                    catalog=catalog,
                    project_id=repaired_script.project_id,
                )
                for a in recheck_sub_report.assessments:
                    rechecked_assessments_by_id[a.claim_id] = a
            except Exception as exc:
                logger.warning("Recheck of changed claims failed (%s: %s). Marking claims 'not_checked'.", type(exc).__name__, exc)
                # Failed recheck must result in not_checked, not an invented success
                for c in changed_claims:
                    fallback_a = ClaimAssessment(
                        claim_id=c.claim_id,
                        section_id=c.section_id,
                        proposition=c.claim_text,
                        status="not_checked",
                        risk_level=c.risk_level or "normal",
                        explanation=f"Recheck verification failed: {exc}",
                        unassessed_reason="recheck_failed",
                        recommended_action="qualify",
                        assessed_at=utc_now(),
                    )
                    rechecked_assessments_by_id[c.claim_id] = fallback_a

        # Add rechecked assessments to final list
        for claim in changed_claims:
            new_a = rechecked_assessments_by_id.get(claim.claim_id)
            if new_a:
                final_assessments.append(new_a)

        # 3. Update action records with recheck status and resolution state
        for record in repair_action_records:
            if record.action == "remove":
                record.is_resolved = True
            elif record.repaired_claim_id in rechecked_assessments_by_id:
                new_a = rechecked_assessments_by_id[record.repaired_claim_id]
                record.new_status = new_a.status
                record.is_resolved = (new_a.status == "supported")

        # 4. Construct updated report and recompute summary
        updated_report = ScriptFactCheckReport(
            project_id=repaired_script.project_id,
            script_version=repaired_script.version,
            research_run_id=repaired_script.research_run_id,
            assessments=final_assessments,
        )
        updated_report.recompute_summary()
        return updated_report
