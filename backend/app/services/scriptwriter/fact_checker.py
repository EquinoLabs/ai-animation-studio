"""Claim Verification Engine (Phase 5 Checkpoint 2).

Performs batched structured fact-checking of discrete factual claims in a Script
against a ValidatedEvidenceCatalog, with application-authoritative provenance
enforcement, deterministic batching, bounded corrective retry, and risk-aligned
assessment semantics.
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
    BatchFactCheckResponsePayload,
    ClaimAssessment,
    ClaimAssessmentPayload,
    ClaimAssessmentStatus,
    ClaimRiskLevel,
    RepairAction,
    ScriptFactCheckReport,
    CLAIM_ASSESSMENT_STATUSES,
    CLAIM_RISK_LEVELS,
    utc_now,
)
from backend.app.domain.script import ID_REGEX, Script, ScriptClaim
from backend.app.llm.base import (
    LLMProvider,
    LLMProviderError,
    LLMResponseMalformedError,
    LLMUnavailableError,
)
from backend.app.llm.ollama import OllamaLLMProvider
from backend.app.services.research.orchestrator import (
    is_challenge_or_gatekeeper,
    is_navigation_or_chrome,
)
from backend.app.services.scriptwriter.outliner import ValidatedEvidenceCatalog

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ScriptFactCheckError(Exception):
    """Base exception for all claim verification engine errors."""
    pass


class ScriptFactCheckSecurityError(ScriptFactCheckError):
    """Raised when cross-project, cross-run, or evidence provenance violations occur."""
    pass


class ScriptFactCheckUnavailableError(ScriptFactCheckError):
    """Raised when LLM provider is unreachable or times out."""
    pass


class ScriptFactCheckMalformedError(ScriptFactCheckError):
    """Raised when LLM returns invalid JSON or schema violations."""
    pass


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

FACT_CHECK_SYSTEM_PROMPT = """You are an authoritative fact-checking verification assistant for video script drafts.
Your role is to rigorously evaluate whether discrete factual assertions made in a script are supported, partially supported, conflicting, unsupported, or contradicted by verified research evidence excerpts.

STATUS TAXONOMY (You must choose EXACTLY one of these six):
- "supported": The factual proposition is directly verified and substantiated by the supplied evidence.
- "partially_supported": The core proposition is partially verified, but figures, qualifiers, or specific details are not fully confirmed.
- "conflicting": The supplied evidence contains conflicting or contradictory accounts regarding the proposition.
- "unsupported": The proposition asserts factual information, but none of the supplied evidence addresses or confirms it.
- "not_checked": The proposition could not be evaluated (e.g., unresearched context, missing data).
- "contradicted": The supplied evidence directly refutes, disproves, or contradicts the factual proposition.

RISK LEVELS & RHETORICAL / CREATIVE EXPRESSIONS:
- "high": High-scrutiny claims (statistics, dates, causal scientific mechanisms, historical events, technical claims).
- "normal": Standard factual assertions.
- "creative_flexibility": Stylistic framing, metaphors, analogies, rhetorical questions, or idioms.
  * Do NOT evaluate metaphors, analogies, or figures of speech as "contradicted" unless they assert an explicitly false factual claim.
  * If the proposition is an acceptable metaphor or stylistic flourish, assign status "supported" or "not_checked" with recommended_action "keep".

STRICT EVIDENCE PROVENANCE & GROUNDING RULES:
1. EXCERPT TEXT IS THE SOLE FACTUAL BASIS:
   - You MUST evaluate whether the proposition is substantiated EXCLUSIVELY by the verbatim text inside 'Excerpt'.
   - Source titles, URLs, and publisher names provide external catalog context only; they do NOT establish proof or substantiation.
   - NEVER mark a claim "supported" based on a source title or headline if the excerpt text itself does not state the factual proposition.
2. NO PARAMETRIC HALLUCINATION:
   - Even if you know from your general knowledge that a proposition is historically, scientifically, or objectively true, if the provided 'Excerpt' does not explicitly state it, you MUST assign "unsupported" (or "partially_supported" if only partially confirmed).
   - NEVER mark a claim "supported" relying on outside knowledge when the excerpt is missing, silent, or unrelated.
3. BOILERPLATE, COOKIE, OR IRRELEVANT EXCERPTS:
   - If an excerpt contains cookie notices, access-denied text, navigation links, or completely unrelated statements, it CANNOT support the claim.
   - In such cases, you MUST assign status "unsupported" and leave supporting_evidence_ids as [].
4. PERMITTED CITATIONS:
   - You may ONLY cite evidence IDs that are explicitly listed under that specific claim's 'PERMITTED EVIDENCE' section.
   - You must NOT cite evidence IDs that were not supplied for that claim.
   - You must NOT cite evidence IDs that belong to other claims or other projects.
   - You must NOT fabricate or invent evidence IDs.
   - If there is no evidence or the evidence is insufficient, leave supporting_evidence_ids, contradicting_evidence_ids, and contextual_evidence_ids as empty lists [].

RECOMMENDED ACTIONS:
- "keep": Claim is verified or acceptable creative expression.
- "qualify": Claim is partially supported or conflicting; needs qualification, hedging, or narrower phrasing.
- "remove": Claim is contradicted or high-risk unsupported; should be deleted.
- "rephrase": Claim wording needs minor correction or clarification.

You must respond with valid JSON adhering to the BatchFactCheckResponsePayload schema. Provide an assessment for EVERY requested claim ID."""


# ---------------------------------------------------------------------------
# Independent Evidence Retrieval & Token Extraction
# ---------------------------------------------------------------------------

_PROPOSITION_STOPWORDS = {
    "a", "an", "the", "in", "on", "at", "by", "for", "with", "about",
    "is", "are", "was", "were", "be", "been", "being", "that", "this",
    "to", "from", "of", "and", "or", "as", "it", "its", "into", "over",
}

_CONTRADICTION_ANTONYM_PAIRS = [
    ("increase", "decrease"), ("increased", "decreased"), ("increases", "decreases"), ("increasing", "decreasing"),
    ("raise", "lower"), ("raised", "lowered"), ("raises", "lowers"),
    ("reduced", "increased"), ("reduction", "increase"),
    ("above", "below"), ("over", "under"), ("more", "less"),
    ("higher", "lower"), ("highest", "lowest"),
    ("warmer", "colder"), ("hot", "cold"), ("hotter", "colder"),
    ("faster", "slower"),
    ("positive", "negative"),
    ("effective", "ineffective"), ("safe", "unsafe"), ("harmful", "beneficial"),
    ("freezes", "boils"), ("frozen", "liquid"),
    ("true", "false"),
]


def _extract_proposition_tokens(text: str) -> List[str]:
    """Extract significant content tokens from proposition text."""
    if not text:
        return []
    cleaned = text.lower()
    cleaned = cleaned.translate(str.maketrans(string.punctuation, " " * len(string.punctuation)))
    tokens = [t.strip() for t in cleaned.split() if t.strip()]
    return [t for t in tokens if t not in _PROPOSITION_STOPWORDS]


# ---------------------------------------------------------------------------
# ScriptFactChecker Service
# ---------------------------------------------------------------------------

class ScriptFactChecker:
    """Evaluates script claims against verified research evidence with batching and provenance validation."""

    def __init__(
        self,
        llm_provider: Optional[LLMProvider] = None,
        settings: Optional[Settings] = None,
        max_batch_size: int = 5,
        max_batch_chars: int = 12000,
        discover_candidates: bool = False,
    ) -> None:
        self.settings = settings or get_settings()
        self.llm_provider = llm_provider or OllamaLLMProvider(self.settings)
        self.max_batch_size = max(1, max_batch_size)
        self.max_batch_chars = max(1000, max_batch_chars)
        self.discover_candidates = discover_candidates

    def check_script(
        self,
        script: Script,
        catalog: Optional[ValidatedEvidenceCatalog] = None,
        project_id: Optional[str] = None,
        batch_size: Optional[int] = None,
        supply_all_catalog_evidence: bool = False,
        discover_candidates: Optional[bool] = None,
        fail_fast: bool = False,
    ) -> ScriptFactCheckReport:
        """Execute claim-level fact-checking across all claims in a script.

        Args:
            script: The Script draft to fact-check.
            catalog: ValidatedEvidenceCatalog containing run-scoped evidence records.
            project_id: Optional project_id override. Must match script.project_id if provided.
            batch_size: Optional batch size override (clamped to >= 1).
            supply_all_catalog_evidence: If True, claims with empty evidence_ids are supplied
                all catalog evidence records. If False (default), only declared evidence_ids
                are supplied.
            fail_fast: If True, re-raises provider or malformed errors immediately instead of
                marking the failed batch claims as 'not_checked'.
        """
        target_project_id = project_id or script.project_id
        if project_id and script.project_id and project_id != script.project_id:
            raise ScriptFactCheckSecurityError(
                f"Project ID mismatch: caller specified '{project_id}', but script belongs to '{script.project_id}'."
            )

        # Enforce catalog run-alignment if script is grounded in research
        if script.research_run_id and catalog:
            if catalog.run_id != script.research_run_id:
                raise ScriptFactCheckSecurityError(
                    f"Evidence catalog run_id '{catalog.run_id}' does not match script research_run_id '{script.research_run_id}'."
                )

        effective_batch_size = max(1, batch_size if batch_size is not None else self.max_batch_size)
        report = ScriptFactCheckReport(
            project_id=target_project_id,
            script_version=script.version,
            research_run_id=script.research_run_id,
        )

        if not script.claims:
            logger.info("Script has no claims to fact-check. Generating clean empty report.")
            report.recompute_summary()
            return report

        # Filter to active claims asserted in current script narration
        section_claim_ids = {cid for s in script.sections for cid in getattr(s, "claim_ids", [])}
        if section_claim_ids:
            active_claims = [c for c in script.claims if c.claim_id in section_claim_ids]
        else:
            active_claims = list(script.claims)

        # Unresearched mode check
        is_unresearched = (
            script.research_run_id is None
            or catalog is None
            or len(catalog.evidence_by_id) == 0
        )
        if is_unresearched:
            logger.info(
                "Script is in unresearched mode (research_run_id=%s, catalog=%s). Marking all claims 'not_checked'.",
                script.research_run_id,
                bool(catalog),
            )
            for claim in active_claims:
                risk = getattr(claim, "risk_level", None) or "normal"
                assessment = ClaimAssessment(
                    claim_id=claim.claim_id,
                    section_id=claim.section_id,
                    proposition=claim.claim_text,
                    status="not_checked",
                    risk_level=risk,
                    explanation="Script was generated in unresearched mode without external research grounding.",
                    unassessed_reason="unresearched_script",
                    recommended_action="keep" if risk == "creative_flexibility" else "qualify",
                    assessed_at=utc_now(),
                )
                report.assessments.append(assessment)
            report.recompute_summary()
            return report

        # Pre-index sections for narrative context
        sections_by_id = {s.id: s for s in script.sections}

        # Build batches from active claims only
        batches = self._split_into_batches(
            claims=active_claims,
            sections_by_id=sections_by_id,
            catalog=catalog,
            batch_size=effective_batch_size,
        )

        effective_discover = self.discover_candidates if discover_candidates is None else discover_candidates

        # Process each batch
        for batch_index, batch_claims in enumerate(batches):
            batch_assessments = self._process_batch(
                batch_index=batch_index,
                batch_claims=batch_claims,
                sections_by_id=sections_by_id,
                catalog=catalog,
                supply_all_catalog_evidence=supply_all_catalog_evidence,
                discover_candidates=effective_discover,
                fail_fast=fail_fast,
            )
            report.assessments.extend(batch_assessments)

        report.recompute_summary()
        return report

    def _split_into_batches(
        self,
        claims: List[ScriptClaim],
        sections_by_id: Dict[str, Any],
        catalog: Optional[ValidatedEvidenceCatalog],
        batch_size: int,
    ) -> List[List[ScriptClaim]]:
        """Split claims into deterministic batches respecting batch_size and character budgets."""
        initial_chunks: List[List[ScriptClaim]] = [
            claims[i : i + batch_size] for i in range(0, len(claims), batch_size)
        ]

        final_batches: List[List[ScriptClaim]] = []
        for chunk in initial_chunks:
            self._recursively_budget_chunk(chunk, final_batches)
        return final_batches

    def _recursively_budget_chunk(
        self,
        chunk: List[ScriptClaim],
        output_batches: List[List[ScriptClaim]],
    ) -> None:
        """Recursively split a chunk in half if it exceeds the max character budget and has > 1 item."""
        if len(chunk) <= 1:
            output_batches.append(chunk)
            return

        # Estimate chunk size
        approx_chars = sum(len(c.claim_text) + 200 for c in chunk)
        if approx_chars > self.max_batch_chars:
            mid = len(chunk) // 2
            self._recursively_budget_chunk(chunk[:mid], output_batches)
            self._recursively_budget_chunk(chunk[mid:], output_batches)
        else:
            output_batches.append(chunk)

    def retrieve_candidate_evidence_for_claim(
        self,
        claim: ScriptClaim,
        catalog: ValidatedEvidenceCatalog,
        max_candidates: int = 5,
    ) -> List[str]:
        """Deterministically retrieve relevant candidate evidence IDs from the catalog.

        Considers content tokens, numbers, dates, named entities, and contradiction
        indicators (differing figures, antonyms, negations) to discover supporting,
        contextual, and refuting evidence even if not explicitly linked to the claim.
        """
        if not catalog or not catalog.evidence_by_id:
            return []

        tokens = _extract_proposition_tokens(claim.claim_text)
        token_set = set(tokens)
        claim_lower = claim.claim_text.lower()
        claim_nums = set(re.findall(r"\b\d+(?:[.,]\d+)?%?\b", claim_lower))

        # Multi-word proper nouns / capitalized entity words
        claim_words = claim.claim_text.split()
        capitalized_entities = {
            re.sub(r"[^\w]", "", w) for w in claim_words
            if w and w[0].isupper() and len(w) > 2
        }

        has_negation = any(n in claim_lower.split() for n in ("no", "not", "never", "neither", "nor"))

        scored_candidates: List[Tuple[float, str]] = []

        for eid in sorted(catalog.valid_evidence_ids):
            ev = catalog.evidence_by_id.get(eid)
            if not ev:
                continue

            excerpt_lower = (ev.excerpt or "").lower()
            target_claim_lower = ((getattr(ev, "target_claim", None) or "")).lower()
            source = catalog.sources_by_id.get(ev.source_id)
            title_lower = (source.title.lower() if source and source.title else "")

            score = 0.0

            # 1. Content token matching
            matching_tokens = {t for t in token_set if t in excerpt_lower}
            score += len(matching_tokens) * 2.0

            # Target claim query matches
            claim_matches = {t for t in token_set if t in target_claim_lower}
            score += len(claim_matches) * 3.0

            # Title matches
            title_matches = {t for t in token_set if t in title_lower}
            score += len(title_matches) * 1.0

            # 2. Number matching & contradiction heuristics
            ev_nums = set(re.findall(r"\b\d+(?:[.,]\d+)?%?\b", excerpt_lower))
            if claim_nums and ev_nums:
                common_nums = claim_nums & ev_nums
                if common_nums:
                    score += len(common_nums) * 5.0
                elif len(matching_tokens) >= 1 or claim_matches:
                    # Differing numbers with matching topic/tokens -> high potential contradiction
                    score += 7.0

            # 3. Antonym / contradiction heuristics
            if matching_tokens or claim_matches:
                for w1, w2 in _CONTRADICTION_ANTONYM_PAIRS:
                    if (w1 in claim_lower and w2 in excerpt_lower) or (w2 in claim_lower and w1 in excerpt_lower):
                        score += 8.0

            # 4. Negation mismatch heuristic
            ev_has_neg = any(n in excerpt_lower.split() for n in ("no", "not", "never", "neither", "nor"))
            if (has_negation != ev_has_neg) and len(matching_tokens) >= 1:
                score += 6.0

            # 5. Entity matching
            matching_entities = {e for e in capitalized_entities if e.lower() in excerpt_lower or e.lower() in target_claim_lower}
            score += len(matching_entities) * 4.0

            if score > 0:
                scored_candidates.append((score, eid))

        # Deterministic sorting: score descending, then evidence_id ascending
        scored_candidates.sort(key=lambda item: (-item[0], item[1]))
        return [eid for _, eid in scored_candidates[:max_candidates]]

    def _process_batch(
        self,
        batch_index: int,
        batch_claims: List[ScriptClaim],
        sections_by_id: Dict[str, Any],
        catalog: Optional[ValidatedEvidenceCatalog],
        supply_all_catalog_evidence: bool,
        discover_candidates: bool,
        fail_fast: bool,
    ) -> List[ClaimAssessment]:
        """Process a single batch with prompt construction, invocation, validation, and bounded retry."""
        # 1. Determine permitted evidence IDs supplied for each claim
        claim_supplied_eids: Dict[str, Set[str]] = {}
        valid_cat_eids = catalog.valid_evidence_ids if catalog else set()

        for claim in batch_claims:
            if supply_all_catalog_evidence:
                supplied = set(valid_cat_eids)
            else:
                declared = (set(claim.evidence_ids) & valid_cat_eids) if claim.evidence_ids else set()
                discovered = (
                    set(self.retrieve_candidate_evidence_for_claim(claim, catalog, max_candidates=5))
                    if (catalog and discover_candidates)
                    else set()
                )
                supplied = declared | discovered
            claim_supplied_eids[claim.claim_id] = supplied

        # 2. Build batch prompt
        prompt = self._build_batch_prompt(
            batch_claims=batch_claims,
            sections_by_id=sections_by_id,
            catalog=catalog,
            claim_supplied_eids=claim_supplied_eids,
        )

        # 3. Invoke LLM with validation and bounded corrective retry
        try:
            assessments = self._invoke_batch_with_retry(
                prompt=prompt,
                batch_claims=batch_claims,
                catalog=catalog,
                claim_supplied_eids=claim_supplied_eids,
            )
            return assessments
        except Exception as exc:
            if fail_fast:
                raise

            # Graceful batch-level degradation: mark only this batch's claims as not_checked
            logger.warning(
                "Batch %d failed fact-check (%s: %s). Marking %d claims 'not_checked'.",
                batch_index,
                type(exc).__name__,
                exc,
                len(batch_claims),
            )
            unassessed_reason = "assessment_failed"
            err_str = str(exc).lower()
            if (
                isinstance(exc, TimeoutError)
                or isinstance(getattr(exc, "__cause__", None), TimeoutError)
                or "timeout" in err_str
                or "timed out" in err_str
            ):
                unassessed_reason = "provider_timeout"
            elif isinstance(exc, (LLMUnavailableError, ScriptFactCheckUnavailableError)):
                unassessed_reason = "provider_unavailable"
            elif isinstance(exc, (LLMResponseMalformedError, ScriptFactCheckMalformedError)):
                unassessed_reason = "malformed_response"

            fallback_assessments: List[ClaimAssessment] = []
            for claim in batch_claims:
                risk = getattr(claim, "risk_level", None) or "normal"
                fallback_assessments.append(
                    ClaimAssessment(
                        claim_id=claim.claim_id,
                        section_id=claim.section_id,
                        proposition=claim.claim_text,
                        status="not_checked",
                        risk_level=risk,
                        explanation=f"Batch verification failed: {exc}",
                        unassessed_reason=unassessed_reason,
                        recommended_action="keep" if risk == "creative_flexibility" else "qualify",
                        assessed_at=utc_now(),
                    )
                )
            return fallback_assessments

    def _build_batch_prompt(
        self,
        batch_claims: List[ScriptClaim],
        sections_by_id: Dict[str, Any],
        catalog: Optional[ValidatedEvidenceCatalog],
        claim_supplied_eids: Dict[str, Set[str]],
    ) -> str:
        """Construct the prompt formatted with exact claim details and permitted evidence excerpts."""
        lines = [
            FACT_CHECK_SYSTEM_PROMPT,
            "",
            "=== BATCH FACT-CHECK REQUEST ===",
            f"Claims in this batch: {len(batch_claims)}",
            "",
        ]

        for idx, claim in enumerate(batch_claims, 1):
            sec = sections_by_id.get(claim.section_id)
            sec_heading = sec.heading if sec else "Unknown Section"
            sec_narration = (sec.narration if sec else "")[:200]
            risk = getattr(claim, "risk_level", None) or "normal"
            supplied_ids = claim_supplied_eids.get(claim.claim_id, set())

            lines.append(f"--- CLAIM {idx} of {len(batch_claims)} ---")
            lines.append(f"CLAIM ID: {claim.claim_id}")
            lines.append(f"SECTION: {sec_heading} (ID: {claim.section_id})")
            if sec_narration:
                lines.append(f"SECTION CONTEXT: \"{sec_narration}\"")
            lines.append(f"PROPOSITION: \"{claim.claim_text}\"")
            lines.append(f"RISK LEVEL: {risk}")

            lines.append("PERMITTED EVIDENCE:")
            if supplied_ids and catalog:
                for eid in sorted(list(supplied_ids)):
                    ev = catalog.evidence_by_id.get(eid)
                    if not ev:
                        continue
                    source = catalog.sources_by_id.get(ev.source_id)
                    source_title = source.title if source else "Source"
                    excerpt = (ev.excerpt or "").strip().replace("\n", " ")
                    lines.append(f"- ID: {eid} | Excerpt: \"{excerpt}\" | (Context: Source title is '{source_title}')")
            else:
                lines.append("- (No verified evidence supplied for this claim)")
            lines.append("")

        lines.append("=== END BATCH REQUEST ===")
        lines.append("")
        lines.append("REQUIRED JSON OUTPUT SCHEMA:")
        lines.append("You MUST output strictly valid JSON matching this exact structure. Every assessment object MUST include all 7 fields:")
        lines.append("{")
        lines.append('  "assessments": [')
        lines.append("    {")
        lines.append('      "claim_id": "clm_01",')
        lines.append('      "status": "supported",')
        lines.append('      "explanation": "Explain precisely how the supplied excerpt supports the complete proposition.",')
        lines.append('      "supporting_evidence_ids": ["evi_01"],')
        lines.append('      "contradicting_evidence_ids": [],')
        lines.append('      "contextual_evidence_ids": [],')
        lines.append('      "recommended_action": "keep"')
        lines.append("    }")
        lines.append("  ]")
        lines.append("}")
        lines.append("")
        lines.append("MANDATORY FIELD DEFINITIONS & RULES:")
        lines.append("1. 'claim_id' (string, required): Exactly one assessment for every requested claim ID in this batch.")
        lines.append("2. 'status' (string, required): MUST be exactly one of: 'supported', 'partially_supported', 'conflicting', 'unsupported', 'not_checked', 'contradicted'.")
        lines.append("   - 'status' is mandatory on every entry. Never omit this field.")
        lines.append("   - The illustrative schema example above shows 'supported', but you MUST choose the status justified by the actual excerpt. If the excerpt does not substantiate the claim, you must choose 'unsupported'.")
        lines.append("   - Source titles, metadata, model knowledge, and search snippets cannot establish support.")
        lines.append("3. 'explanation' (string, required): Non-empty explanation detailing how the excerpt proves, refutes, or fails to address the claim.")
        lines.append("4. 'supporting_evidence_ids' (list of strings): IDs of evidence directly confirming the proposition. (Must come from PERMITTED EVIDENCE, else []).")
        lines.append("5. 'contradicting_evidence_ids' (list of strings): IDs of evidence directly refuting the proposition (else []).")
        lines.append("6. 'contextual_evidence_ids' (list of strings): IDs of evidence providing background without verifying (else []).")
        lines.append("   - Supporting, contradicting, and contextual evidence have distinct meanings. Do NOT use generic 'evidence_ids'.")
        lines.append("7. 'recommended_action' (string, required): Exactly one of: 'keep', 'qualify', 'remove', 'rephrase'.")
        return "\n".join(lines)

    def _invoke_batch_with_retry(
        self,
        prompt: str,
        batch_claims: List[ScriptClaim],
        catalog: Optional[ValidatedEvidenceCatalog],
        claim_supplied_eids: Dict[str, Set[str]],
    ) -> List[ClaimAssessment]:
        """Call LLM provider and execute at most one corrective retry if schema or validation fails."""
        # 1. Primary invocation
        try:
            payload: BatchFactCheckResponsePayload = self.llm_provider.generate_json(
                prompt=prompt,
                schema=BatchFactCheckResponsePayload,
            )
        except (LLMUnavailableError, TimeoutError, OSError) as conn_err:
            raise ScriptFactCheckUnavailableError(f"LLM provider error: {conn_err}") from conn_err
        except (LLMResponseMalformedError, json.JSONDecodeError, Exception) as malformed:
            raw_snippet = str(getattr(malformed, "raw_response", "")) or str(malformed)[:300]
            logger.warning(
                "LLM emitted malformed JSON for batch: %s | Diagnostic snippet: %s. Executing corrective retry.",
                malformed,
                raw_snippet,
            )
            return self._execute_corrective_retry(
                prompt=prompt,
                validation_error=f"Malformed JSON response: {malformed}",
                batch_claims=batch_claims,
                catalog=catalog,
                claim_supplied_eids=claim_supplied_eids,
            )

        # 2. Application validation
        is_valid, error_msg = self._validate_batch_response(
            payload=payload,
            batch_claims=batch_claims,
            catalog=catalog,
            claim_supplied_eids=claim_supplied_eids,
        )

        if is_valid:
            return self._build_claim_assessments(payload, batch_claims, claim_supplied_eids, catalog=catalog)

        # 3. Validation failed: execute one bounded corrective retry
        logger.warning(
            "Batch response failed application validation: %s. Executing corrective retry.",
            error_msg,
        )
        return self._execute_corrective_retry(
            prompt=prompt,
            validation_error=error_msg or "Validation failed",
            batch_claims=batch_claims,
            catalog=catalog,
            claim_supplied_eids=claim_supplied_eids,
        )

    def _execute_corrective_retry(
        self,
        prompt: str,
        validation_error: str,
        batch_claims: List[ScriptClaim],
        catalog: Optional[ValidatedEvidenceCatalog],
        claim_supplied_eids: Dict[str, Set[str]],
    ) -> List[ClaimAssessment]:
        """Perform exactly one bounded corrective retry with explicit error feedback and schema structure."""
        expected_ids = sorted([c.claim_id for c in batch_claims])
        allowed_ids_summary = {
            cid: sorted(list(eids)) for cid, eids in claim_supplied_eids.items()
        }
        first_id = expected_ids[0] if expected_ids else "clm_01"

        retry_prompt = (
            f"{prompt}\n\n"
            f"--- CORRECTION REQUIRED ---\n"
            f"Your previous response was rejected due to schema or validation errors:\n"
            f"ERROR: {validation_error}\n\n"
            f"STRICT CORRECTIVE REQUIREMENTS:\n"
            f"1. You must return an assessment for EXACTLY these claim IDs: {expected_ids}.\n"
            f"2. Do NOT omit any claim ID and do NOT return duplicate claim IDs.\n"
            f"3. Every assessment object in 'assessments' MUST include all 7 required fields:\n"
            f"   - 'claim_id': string matching the requested claim ID\n"
            f"   - 'status': mandatory string, one of ['supported', 'partially_supported', 'conflicting', 'unsupported', 'not_checked', 'contradicted']\n"
            f"   - 'explanation': mandatory non-empty explanation string\n"
            f"   - 'supporting_evidence_ids': list of evidence IDs from permitted evidence (never use generic 'evidence_ids')\n"
            f"   - 'contradicting_evidence_ids': list of evidence IDs refuting the proposition\n"
            f"   - 'contextual_evidence_ids': list of evidence IDs providing background\n"
            f"   - 'recommended_action': one of ['keep', 'qualify', 'remove', 'rephrase']\n"
            f"4. Permitted evidence mapping per claim: {allowed_ids_summary}\n"
            f"   If no evidence was supplied for a claim, all evidence ID lists must be empty [].\n"
            f"5. Output valid JSON matching this exact structure:\n"
            f"{{\n"
            f'  "assessments": [\n'
            f'    {{\n'
            f'      "claim_id": "{first_id}",\n'
            f'      "status": "unsupported",\n'
            f'      "explanation": "State clearly how the supplied excerpt supports or fails to support the proposition.",\n'
            f'      "supporting_evidence_ids": [],\n'
            f'      "contradicting_evidence_ids": [],\n'
            f'      "contextual_evidence_ids": [],\n'
            f'      "recommended_action": "qualify"\n'
            f'    }}\n'
            f'  ]\n'
            f"}}\n"
            f"--- END CORRECTION REQUIRED ---"
        )

        try:
            retry_payload: BatchFactCheckResponsePayload = self.llm_provider.generate_json(
                prompt=retry_prompt,
                schema=BatchFactCheckResponsePayload,
            )
        except (LLMUnavailableError, TimeoutError, OSError) as conn_err:
            raise ScriptFactCheckUnavailableError(
                f"LLM provider error on retry: {conn_err}"
            ) from conn_err
        except (LLMResponseMalformedError, json.JSONDecodeError, Exception) as malformed:
            raw_snippet = str(getattr(malformed, "raw_response", "")) or str(malformed)[:300]
            logger.warning(
                "LLM emitted malformed response on retry: %s | Diagnostic snippet: %s",
                malformed,
                raw_snippet,
            )
            raise ScriptFactCheckMalformedError(
                f"LLM generated malformed response on retry: {malformed}"
            ) from malformed

        is_valid, error_msg = self._validate_batch_response(
            payload=retry_payload,
            batch_claims=batch_claims,
            catalog=catalog,
            claim_supplied_eids=claim_supplied_eids,
        )

        if not is_valid:
            # If the failure is due to ungrounded evidence or boilerplate citations,
            # fail safely by building claim assessments where invalid citations are downgraded to unsupported
            err_lower = (error_msg or "").lower()
            if any(term in err_lower for term in ("boilerplate", "cookie", "challenge", "navigation", "zero lexical overlap", "zero token overlap")):
                logger.warning(
                    "Corrective retry cited invalid or ungrounded evidence (%s). Failing safely to unsupported.",
                    error_msg,
                )
                return self._build_claim_assessments(retry_payload, batch_claims, claim_supplied_eids, catalog=catalog)

            raise ScriptFactCheckMalformedError(
                f"Corrective retry failed validation: {error_msg}"
            )

        return self._build_claim_assessments(retry_payload, batch_claims, claim_supplied_eids, catalog=catalog)

    def _validate_batch_response(
        self,
        payload: BatchFactCheckResponsePayload,
        batch_claims: List[ScriptClaim],
        catalog: Optional[ValidatedEvidenceCatalog],
        claim_supplied_eids: Dict[str, Set[str]],
    ) -> Tuple[bool, Optional[str]]:
        """Validate LLM output against strict application-authoritative provenance constraints."""
        if not payload or not isinstance(payload.assessments, list):
            return False, "Payload does not contain an 'assessments' list."

        expected_ids = {c.claim_id for c in batch_claims}
        returned_ids = [a.claim_id for a in payload.assessments]

        # 1. Check for duplicates
        if len(returned_ids) != len(set(returned_ids)):
            dupes = [cid for cid in set(returned_ids) if returned_ids.count(cid) > 1]
            return False, f"Duplicate assessments returned for claim IDs: {sorted(dupes)}"

        # 2. Check 1-to-1 claim identity match
        returned_set = set(returned_ids)
        missing = expected_ids - returned_set
        extra = returned_set - expected_ids

        id_errors = []
        if missing:
            id_errors.append(f"Missing assessments for claim IDs: {sorted(list(missing))}")
        if extra:
            id_errors.append(f"Fabricated or unknown claim IDs returned: {sorted(list(extra))}")
        if id_errors:
            return False, "; ".join(id_errors)

        # 3. Check evidence citations and provenance
        claims_by_id = {c.claim_id: c for c in batch_claims}
        valid_cat_eids = catalog.valid_evidence_ids if catalog else set()
        for assessment in payload.assessments:
            # 3a. Status check
            if assessment.status not in CLAIM_ASSESSMENT_STATUSES:
                return (
                    False,
                    f"Claim '{assessment.claim_id}' returned invalid status '{assessment.status}'. "
                    f"Allowed: {CLAIM_ASSESSMENT_STATUSES}",
                )

            # 3b. Explanation check
            if not assessment.explanation or not assessment.explanation.strip():
                return False, f"Claim '{assessment.claim_id}' returned an empty explanation."

            # 3c. Evidence citation check
            supplied_for_claim = claim_supplied_eids.get(assessment.claim_id, set())
            cited_eids = (
                set(assessment.supporting_evidence_ids)
                | set(assessment.contradicting_evidence_ids)
                | set(assessment.contextual_evidence_ids)
            )

            # Check if any cited ID is unknown/fabricated in catalog
            foreign_or_fabricated = cited_eids - valid_cat_eids
            if foreign_or_fabricated:
                return (
                    False,
                    f"Claim '{assessment.claim_id}' cited unknown or fabricated evidence IDs: "
                    f"{sorted(list(foreign_or_fabricated))}",
                )

            # Check if any cited ID was not supplied to this specific claim
            unsupplied = cited_eids - supplied_for_claim
            if unsupplied:
                return (
                    False,
                    f"Claim '{assessment.claim_id}' cited evidence IDs not supplied to it: "
                    f"{sorted(list(unsupplied))}. Permitted for this claim: {sorted(list(supplied_for_claim))}",
                )

            # 3d. Substantive evidence validation for supported claims
            if assessment.status == "supported":
                if not assessment.supporting_evidence_ids:
                    return (
                        False,
                        f"Claim '{assessment.claim_id}' was assigned status 'supported' but provided no supporting_evidence_ids.",
                    )
                if catalog:
                    for eid in assessment.supporting_evidence_ids:
                        ev = catalog.evidence_by_id.get(eid)
                        if ev:
                            if is_challenge_or_gatekeeper(ev.excerpt):
                                return (
                                    False,
                                    f"Claim '{assessment.claim_id}' cited evidence '{eid}' as supporting, "
                                    f"but that excerpt contains an anti-bot/cookie challenge notice and cannot substantiate claims.",
                                )
                            if is_navigation_or_chrome(ev.excerpt):
                                return (
                                    False,
                                    f"Claim '{assessment.claim_id}' cited evidence '{eid}' as supporting, "
                                    f"but that excerpt contains navigation chrome and cannot substantiate claims.",
                                )
                            # Title inference warning when excerpt has zero content overlap
                            prop_text = claims_by_id[assessment.claim_id].claim_text if assessment.claim_id in claims_by_id else ""
                            prop_tokens = set(_extract_proposition_tokens(prop_text))
                            excerpt_tokens = set(_extract_proposition_tokens(ev.excerpt))
                            source = catalog.sources_by_id.get(ev.source_id)
                            title_tokens = set(_extract_proposition_tokens(source.title)) if (source and source.title) else set()
                            expl_lower = assessment.explanation.lower()
                            if len(prop_tokens & excerpt_tokens) == 0 and len(prop_tokens) >= 3:
                                if (
                                    (source and source.title and source.title.lower() in expl_lower)
                                    or "title" in expl_lower
                                    or len(title_tokens & prop_tokens) >= 2
                                ):
                                    return (
                                        False,
                                        f"Claim '{assessment.claim_id}' cited evidence '{eid}' as supporting, "
                                        f"but excerpt has zero lexical overlap with proposition and explanation relies on source title. "
                                        f"Support must be established strictly from excerpt text.",
                                    )

        return True, None

    def _build_claim_assessments(
        self,
        payload: BatchFactCheckResponsePayload,
        batch_claims: List[ScriptClaim],
        claim_supplied_eids: Dict[str, Set[str]],
        catalog: Optional[ValidatedEvidenceCatalog] = None,
    ) -> List[ClaimAssessment]:
        """Construct authoritative ClaimAssessment domain objects from validated payload."""
        claims_by_id = {c.claim_id: c for c in batch_claims}
        assessments: List[ClaimAssessment] = []

        for p_item in payload.assessments:
            claim = claims_by_id[p_item.claim_id]
            risk = getattr(claim, "risk_level", None) or "normal"
            supplied_ids = sorted(list(claim_supplied_eids.get(claim.claim_id, set())))

            status = p_item.status
            explanation = p_item.explanation.strip()
            supporting_ids = sorted(list(set(p_item.supporting_evidence_ids)))
            recommended_action = p_item.recommended_action

            # Defensive post-validation safeguard: ensure supported status cannot survive on invalid evidence
            if status == "supported":
                if not supporting_ids:
                    status = "unsupported"
                    explanation += " [Audited: Claim provided no supporting evidence records; downgraded to unsupported.]"
                    recommended_action = "qualify" if risk != "high" else "remove"
                elif catalog:
                    valid_supporting = []
                    for eid in supporting_ids:
                        ev = catalog.evidence_by_id.get(eid)
                        if ev and (is_challenge_or_gatekeeper(ev.excerpt) or is_navigation_or_chrome(ev.excerpt)):
                            logger.warning("Dropping invalid boilerplate evidence citation '%s' from claim '%s'.", eid, claim.claim_id)
                        else:
                            valid_supporting.append(eid)
                    supporting_ids = valid_supporting
                    if not valid_supporting:
                        status = "unsupported"
                        explanation += " [Audited: Cited evidence was invalid boilerplate/gatekeeper text; downgraded to unsupported.]"
                        recommended_action = "qualify" if risk != "high" else "remove"

            assessment = ClaimAssessment(
                claim_id=claim.claim_id,
                section_id=claim.section_id,
                proposition=claim.claim_text,
                status=status,
                risk_level=risk,
                explanation=explanation,
                considered_evidence_ids=supplied_ids,
                supporting_evidence_ids=supporting_ids,
                contradicting_evidence_ids=sorted(list(set(p_item.contradicting_evidence_ids))),
                contextual_evidence_ids=sorted(list(set(p_item.contextual_evidence_ids))),
                recommended_action=recommended_action,
                assessed_at=utc_now(),
            )
            assessments.append(assessment)

        return assessments
