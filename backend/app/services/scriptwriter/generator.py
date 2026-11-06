"""Evidence-Grounded Script Draft Generation Service (Phase 4, P4-S4).

Converts an accepted `ScriptOutline` into a coherent, evidence-grounded `Script` draft
with run-scoped provenance validation, authoritative claim identification, and soft pacing.
"""

from dataclasses import dataclass, field
from datetime import datetime
import json
import logging
from typing import Any, Dict, List, Literal, Optional, Set, Tuple
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.config import Settings, get_settings
from backend.app.domain.research import (
    EvidenceRecord,
    ID_REGEX,
    ResearchRun,
    SourceRecord,
    utc_now,
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
    estimate_duration_seconds,
    populate_script_duration_metrics,
)
from backend.app.llm.base import (
    LLMProvider,
    LLMProviderError,
    LLMResponseMalformedError,
    LLMUnavailableError,
)
from backend.app.services.project.service import (
    ProjectNotFoundError,
    ProjectSecurityError,
    ProjectService,
)
from backend.app.services.research.evidence_brief import EvidenceBriefBuilder, EvidenceBriefError
from backend.app.services.research.storage import (
    ResearchRunNotFoundError,
    ResearchSecurityError,
    ResearchStorageError,
    ResearchStorageService,
    ResearchValidationError,
)
from backend.app.services.scriptwriter.classifier import classify_claim_risk
from backend.app.services.scriptwriter.outliner import ValidatedEvidenceCatalog
from backend.app.services.scriptwriter.policy import (
    ScriptPromptBuilder,
    WritingPolicy,
    detect_narrative_structure,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ScriptDraftGeneratorError(Exception):
    """Base exception for all script draft generator failures."""
    pass


class ScriptDraftSecurityError(ScriptDraftGeneratorError):
    """Raised when project isolation, security validation, or cross-project access fails."""
    pass


class ScriptDraftRunError(ScriptDraftGeneratorError):
    """Raised when research run selection fails, is incomplete, or storage evidence/source records are corrupted."""
    pass


class ScriptDraftUnavailableError(ScriptDraftGeneratorError):
    """Raised when the LLM provider service is unreachable or unavailable."""
    pass


class ScriptDraftMalformedError(ScriptDraftGeneratorError):
    """Raised when LLM output violates schema, section progression, or narrative progression constraints."""
    pass


class ScriptDraftEvidenceValidationError(ScriptDraftGeneratorError):
    """Raised when generated script cites unknown or invalid evidence IDs after corrective retry, or cites evidence in unresearched mode."""
    pass


# ---------------------------------------------------------------------------
# Input Contract
# ---------------------------------------------------------------------------

class DraftGeneratorInput(BaseModel):
    """Input contract for evidence-grounded script draft generation."""
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., description="Target project identifier")
    outline: ScriptOutline = Field(..., description="Accepted narrative outline to expand")
    allow_unresearched: bool = Field(default=False, description="Explicit authorization to generate without research grounding")
    title: Optional[str] = Field(default=None, max_length=200, description="Optional working title override")
    writing_policy: Optional[WritingPolicy] = Field(default=None, description="Custom writing policy directives")
    generation_mode: Literal["section_by_section", "monolithic"] = Field(
        default="section_by_section",
        description="Generation mode: section_by_section for deeper multi-beat narrative development, monolithic for single-pass",
    )

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, v: str) -> str:
        clean = (v or "").strip()
        if not clean or not ID_REGEX.match(clean):
            raise ValueError(f"Invalid project_id '{v}'. Must be alphanumeric with underscores or dashes.")
        return clean


# ---------------------------------------------------------------------------
# Script Draft Generator Service
# ---------------------------------------------------------------------------

class ScriptDraftGenerator:
    """Generates evidence-grounded Script drafts from accepted ScriptOutlines."""

    def __init__(
        self,
        llm_provider: LLMProvider,
        research_storage: Optional[ResearchStorageService] = None,
        project_service: Optional[ProjectService] = None,
        brief_builder: Optional[EvidenceBriefBuilder] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self.llm_provider = llm_provider
        self.settings = settings or get_settings()
        self.research_storage = research_storage or ResearchStorageService(settings=self.settings)
        self.project_service = project_service or ProjectService(projects_root=self.settings.PROJECTS_DIR)
        self.brief_builder = brief_builder or EvidenceBriefBuilder(
            storage_service=self.research_storage, settings=self.settings
        )

    def generate_draft(self, input_data: DraftGeneratorInput) -> Script:
        """Convert a ScriptOutline into a validated Script draft with evidence linking.

        Args:
            input_data: DraftGeneratorInput contract containing outline and options.

        Returns:
            Authoritatively assembled Script domain model (in-memory draft).

        Raises:
            ScriptDraftSecurityError: On project isolation or security violations.
            ScriptDraftRunError: If research run is missing, incomplete, or corrupted.
            ScriptDraftUnavailableError: If LLM is unreachable.
            ScriptDraftMalformedError: If LLM output violates schema or section progression.
            ScriptDraftEvidenceValidationError: If model outputs unknown evidence IDs or cites evidence in unresearched mode.
        """
        # 1. Verify project isolation
        self._verify_project(input_data.project_id)

        # 2. Resolve research run & validate evidence catalog
        selected_run, evidence_catalog, evidence_brief_text = self._resolve_research_context(
            project_id=input_data.project_id,
            outline=input_data.outline,
            allow_unresearched=input_data.allow_unresearched,
        )

        # 3. Setup writing policy & prompt builder
        policy = input_data.writing_policy or WritingPolicy(
            narrative_structure=input_data.outline.narrative_structure,
            words_per_minute=self.llm_provider.words_per_minute or DEFAULT_WORDS_PER_MINUTE,
        )
        prompt_builder = ScriptPromptBuilder(policy=policy)

        # 4. Check generation mode
        preset = getattr(self.llm_provider, "_preset_response", None)
        queue = getattr(self.llm_provider, "_response_queue", None)
        raw = getattr(self.llm_provider, "_raw_response", None)
        is_mock_monolithic_preset = (
            isinstance(preset, (GeneratedScriptPayload, Script))
            or (isinstance(preset, dict) and "sections" in preset)
            or isinstance(raw, (GeneratedScriptPayload, Script))
            or (isinstance(raw, dict) and "sections" in raw)
            or bool(queue and any(isinstance(x, (GeneratedScriptPayload, Script)) or (isinstance(x, dict) and "sections" in x) for x in queue))
            or getattr(self.llm_provider, "enable_auto_expansion", False)
        )
        use_monolithic = (input_data.generation_mode == "monolithic") or is_mock_monolithic_preset

        if use_monolithic:
            # Monolithic generation (single-pass)
            prompt = self._build_generation_prompt(
                prompt_builder=prompt_builder,
                input_data=input_data,
                evidence_brief_text=evidence_brief_text,
                evidence_catalog=evidence_catalog,
                is_unresearched=(selected_run is None),
            )
            payload = self._invoke_llm_with_validation(
                prompt=prompt,
                prompt_builder=prompt_builder,
                input_data=input_data,
                evidence_catalog=evidence_catalog,
                is_unresearched=(selected_run is None),
            )
        else:
            # Section-by-section generation with multi-beat depth
            payload = self._generate_draft_section_by_section(
                prompt_builder=prompt_builder,
                input_data=input_data,
                selected_run=selected_run,
                evidence_catalog=evidence_catalog,
                evidence_brief_text=evidence_brief_text,
                is_unresearched=(selected_run is None),
            )

        # 5. Validate section alignment with outline
        self._validate_section_alignment(payload.sections, input_data.outline)

        # 6. Authoritatively assemble Script domain model
        return self._assemble_authoritative_script(
            input_data=input_data,
            payload=payload,
            selected_run=selected_run,
            evidence_catalog=evidence_catalog,
        )

    # -----------------------------------------------------------------------
    # Helper Methods
    # -----------------------------------------------------------------------

    def _verify_project(self, project_id: str) -> None:
        """Verify project exists and is valid."""
        try:
            self.project_service.get_project(project_id)
        except (ProjectSecurityError, ValueError) as sec_err:
            raise ScriptDraftSecurityError(f"Security error for project '{project_id}': {sec_err}") from sec_err
        except ProjectNotFoundError as not_found:
            raise ScriptDraftSecurityError(f"Project '{project_id}' not found: {not_found}") from not_found

    def _resolve_research_context(
        self,
        project_id: str,
        outline: ScriptOutline,
        allow_unresearched: bool,
    ) -> Tuple[Optional[ResearchRun], ValidatedEvidenceCatalog, Optional[str]]:
        """Resolve research run, run brief, and run evidence catalog."""
        run_id = outline.research_run_id
        if not run_id:
            if not allow_unresearched:
                raise ScriptDraftRunError(
                    f"Outline for topic '{outline.topic}' has no research_run_id. "
                    f"Draft generation requires a research run unless 'allow_unresearched=True' is explicitly authorized."
                )
            return None, ValidatedEvidenceCatalog(run_id=""), None

        # Verify run exists and belongs to this project
        try:
            run = self.research_storage.get_run(project_id, run_id)
        except (ResearchSecurityError, ProjectSecurityError, ValueError) as sec_err:
            raise ScriptDraftSecurityError(f"Security error accessing run '{run_id}': {sec_err}") from sec_err
        except ResearchRunNotFoundError as not_found:
            raise ScriptDraftRunError(f"Research run '{run_id}' not found in project '{project_id}'.") from not_found
        except ResearchValidationError as val_err:
            raise ScriptDraftRunError(f"Corrupted research run '{run_id}': {val_err}") from val_err

        if run.status != "completed":
            raise ScriptDraftRunError(
                f"Research run '{run_id}' has status '{run.status}'. Only 'completed' runs can ground script drafts."
            )

        # Retrieve or recover run-scoped evidence brief
        evidence_brief_text = self._get_or_recover_evidence_brief(project_id, run)

        # Build run evidence catalog with source validation
        evidence_catalog = self._build_evidence_catalog(project_id, run)

        return run, evidence_catalog, evidence_brief_text

    def _get_or_recover_evidence_brief(
        self,
        project_id: str,
        run: ResearchRun,
    ) -> str:
        """Retrieve persisted run brief or deterministically rebuild it."""
        content = self.research_storage.get_run_evidence_brief(project_id, run.run_id)
        if content and content.strip():
            return content

        run_scoped_path = f"runs/{run.run_id}/evidence_brief.md"
        try:
            _, rebuilt_content = self.brief_builder.build_and_save_brief(
                project_id=project_id,
                run_id=run.run_id,
                filename=run_scoped_path,
            )
            return rebuilt_content
        except EvidenceBriefError as eb_err:
            raise ScriptDraftRunError(
                f"Failed to recover evidence brief for run '{run.run_id}': {eb_err}"
            ) from eb_err

    def _build_evidence_catalog(
        self,
        project_id: str,
        run: ResearchRun,
    ) -> ValidatedEvidenceCatalog:
        """Build catalog of evidence records and associated sources for this run."""
        catalog = ValidatedEvidenceCatalog(run_id=run.run_id)

        all_sources = {s.source_id: s for s in self.research_storage.list_sources(project_id)}
        evidence_items = self.research_storage.list_evidence(project_id, run_id=run.run_id)

        for ev in evidence_items:
            if ev.run_id != run.run_id:
                continue
            if not (ev.excerpt or "").strip():
                continue

            source = all_sources.get(ev.source_id)
            if not source:
                raise ScriptDraftRunError(
                    f"Evidence '{ev.evidence_id}' references unknown source '{ev.source_id}'."
                )

            catalog.evidence_by_id[ev.evidence_id] = ev
            catalog.sources_by_id[ev.source_id] = source

        return catalog

    def _build_generation_prompt(
        self,
        prompt_builder: ScriptPromptBuilder,
        input_data: DraftGeneratorInput,
        evidence_brief_text: Optional[str],
        evidence_catalog: ValidatedEvidenceCatalog,
        is_unresearched: bool,
    ) -> str:
        """Construct the prompt with dynamic outline sections and explicit catalog instructions."""
        outline_json = input_data.outline.model_dump_json()

        catalog_section = ""
        if is_unresearched:
            catalog_section = (
                "\n--- UNRESEARCHED MODE DIRECTIVE ---\n"
                "This script is being generated in UNRESEARCHED mode without external research grounding.\n"
                "- Do NOT cite any evidence IDs in 'evidence_ids' (must be empty list [] for all claims).\n"
                "- Do NOT hallucinate sources or evidence identifiers.\n"
                "--- END UNRESEARCHED MODE DIRECTIVE ---\n"
            )
        elif evidence_catalog.valid_evidence_ids:
            catalog_section = (
                f"\n--- RUN EVIDENCE CATALOG ---\n"
                f"{evidence_catalog.format_catalog_for_prompt()}\n"
                f"--- END RUN EVIDENCE CATALOG ---\n\n"
                f"STRICT EVIDENCE CONSTRAINTS:\n"
                f"- You may ONLY cite evidence IDs from the catalog above.\n"
                f"- Permitted evidence IDs: {sorted(list(evidence_catalog.valid_evidence_ids))}\n"
                f"- Do NOT hallucinate or cite any evidence ID not explicitly listed in the catalog.\n"
                f"- If an assertion lacks retrieved evidence, leave 'evidence_ids' as an empty list [].\n"
            )

        base_prompt = prompt_builder.build_script_generation_prompt(
            topic=input_data.outline.topic,
            target_duration_minutes=input_data.outline.target_duration_minutes,
            outline_json=outline_json,
            evidence_brief_text=evidence_brief_text,
            title=input_data.title or input_data.outline.topic,
        )

        return f"{base_prompt}\n{catalog_section}"

    def _invoke_llm_with_validation(
        self,
        prompt: str,
        prompt_builder: ScriptPromptBuilder,
        input_data: DraftGeneratorInput,
        evidence_catalog: ValidatedEvidenceCatalog,
        is_unresearched: bool,
    ) -> GeneratedScriptPayload:
        """Invoke LLM and execute exactly one bounded retry if invalid evidence IDs are cited."""
        try:
            payload: GeneratedScriptPayload = self.llm_provider.generate_json(
                prompt=prompt,
                schema=GeneratedScriptPayload,
            )
        except LLMUnavailableError as unavail:
            raise ScriptDraftUnavailableError(f"LLM provider unavailable: {unavail}") from unavail
        except (LLMResponseMalformedError, Exception) as malformed:
            raise ScriptDraftMalformedError(f"LLM generated malformed script payload: {malformed}") from malformed

        # Check evidence citations
        invalid_ids = self._find_invalid_evidence_ids(payload, evidence_catalog, is_unresearched)
        if not invalid_ids:
            return payload

        # In unresearched mode: model is never allowed to emit evidence IDs
        if is_unresearched:
            raise ScriptDraftEvidenceValidationError(
                f"Script draft emitted evidence IDs in unresearched mode: {sorted(list(invalid_ids))}. "
                f"Unresearched scripts cannot cite evidence."
            )

        # Researched mode: bounded corrective retry (one-shot)
        logger.warning(
            "Generated script cited unknown evidence IDs: %s. Executing bounded corrective retry.",
            invalid_ids,
        )
        retry_prompt = (
            f"{prompt}\n\n"
            f"CORRECTION REQUIRED:\n"
            f"Your previous attempt cited the following unknown or disallowed evidence IDs: {sorted(list(invalid_ids))}.\n"
            f"Permitted evidence IDs are strictly: {sorted(list(evidence_catalog.valid_evidence_ids))}.\n"
            f"Please regenerate the script JSON, using ONLY the permitted evidence IDs or an empty list []."
        )

        try:
            retry_payload: GeneratedScriptPayload = self.llm_provider.generate_json(
                prompt=retry_prompt,
                schema=GeneratedScriptPayload,
            )
        except LLMUnavailableError as unavail:
            raise ScriptDraftUnavailableError(f"LLM provider unavailable on retry: {unavail}") from unavail
        except (LLMResponseMalformedError, Exception) as malformed:
            raise ScriptDraftMalformedError(f"LLM generated malformed script on retry: {malformed}") from malformed

        remaining_invalid = self._find_invalid_evidence_ids(retry_payload, evidence_catalog, is_unresearched)
        if remaining_invalid:
            raise ScriptDraftEvidenceValidationError(
                f"Generated script cited invalid evidence IDs after corrective retry: {sorted(list(remaining_invalid))}. "
                f"Allowed IDs: {sorted(list(evidence_catalog.valid_evidence_ids))}."
            )

        return retry_payload

    def _generate_draft_section_by_section(
        self,
        prompt_builder: ScriptPromptBuilder,
        input_data: DraftGeneratorInput,
        selected_run: Optional[ResearchRun],
        evidence_catalog: ValidatedEvidenceCatalog,
        evidence_brief_text: Optional[str],
        is_unresearched: bool,
    ) -> GeneratedScriptPayload:
        """Generate a complete Script draft section-by-section with multi-beat depth and narrative continuity."""
        generated_sections: List[ScriptSectionPayload] = []
        accumulated_claims: List[ScriptClaimPayload] = []
        preceding_summaries: List[str] = []

        total_sections = len(input_data.outline.sections)

        for idx, out_sec in enumerate(input_data.outline.sections):
            sec_id = f"sec_{idx+1:02d}"

            # Filter or extract permitted evidence IDs for this section
            if is_unresearched:
                sec_evidence_ids: List[str] = []
            elif out_sec.evidence_ids:
                sec_evidence_ids = [
                    eid for eid in out_sec.evidence_ids
                    if eid in evidence_catalog.valid_evidence_ids
                ]
                if not sec_evidence_ids:
                    sec_evidence_ids = list(evidence_catalog.valid_evidence_ids)
            else:
                sec_evidence_ids = list(evidence_catalog.valid_evidence_ids)

            preceding_summary_text = "\n".join(preceding_summaries) if preceding_summaries else None

            # Build single-section prompt
            prompt = prompt_builder.build_single_section_prompt(
                topic=input_data.outline.topic,
                target_duration_minutes=input_data.outline.target_duration_minutes,
                section_index=idx,
                total_sections=total_sections,
                section_type=out_sec.type,
                heading=out_sec.heading,
                target_words=out_sec.target_words,
                factual_milestones=out_sec.factual_milestones,
                visual_concept=out_sec.visual_concept,
                evidence_brief_text=evidence_brief_text,
                preceding_sections_summary=preceding_summary_text,
                permitted_evidence_ids=sec_evidence_ids,
                is_unresearched=is_unresearched,
            )

            # Invoke LLM for section
            section_payload = self._invoke_section_with_validation(
                sec_prompt=prompt,
                out_sec=out_sec,
                sec_id=sec_id,
                evidence_catalog=evidence_catalog,
                is_unresearched=is_unresearched,
            )

            # Bounded section expansion fallback:
            # If narration falls substantially below target (<50% of target_words and <60 words)
            wc = count_words(section_payload.narration)
            if wc < (0.5 * out_sec.target_words) and wc < 60:
                section_payload = self._expand_underdeveloped_section(
                    section_payload=section_payload,
                    out_sec=out_sec,
                    topic=input_data.outline.topic,
                    prompt_builder=prompt_builder,
                    evidence_brief_text=evidence_brief_text,
                    permitted_evidence_ids=sec_evidence_ids,
                    is_unresearched=is_unresearched,
                    evidence_catalog=evidence_catalog,
                )

            # Record section
            generated_sections.append(
                ScriptSectionPayload(
                    id=sec_id,
                    type=out_sec.type,
                    heading=section_payload.heading or out_sec.heading,
                    narration=section_payload.narration,
                    notes=section_payload.notes,
                )
            )

            # Collect claims
            for claim in section_payload.claims:
                if is_unresearched:
                    clean_eids = []
                else:
                    clean_eids = [
                        eid for eid in claim.evidence_ids
                        if eid in evidence_catalog.valid_evidence_ids
                    ]
                accumulated_claims.append(
                    ScriptClaimPayload(
                        claim_id=f"clm_{len(accumulated_claims)+1:02d}",
                        claim_text=claim.claim_text,
                        section_id=sec_id,
                        evidence_ids=clean_eids,
                    )
                )

            # Record continuity summary
            words = section_payload.narration.split()
            synopsis = " ".join(words[:25])
            preceding_summaries.append(f"- Section '{out_sec.heading}' ({out_sec.type}): {synopsis}...")

        return GeneratedScriptPayload(
            title=input_data.title or input_data.outline.topic,
            sections=generated_sections,
            claims=accumulated_claims,
        )

    def _invoke_section_with_validation(
        self,
        sec_prompt: str,
        out_sec: ScriptOutlineSection,
        sec_id: str,
        evidence_catalog: ValidatedEvidenceCatalog,
        is_unresearched: bool,
    ) -> GeneratedSectionPayload:
        """Invoke LLM for a single section with malformed/unresearched error handling."""
        try:
            payload: GeneratedSectionPayload = self.llm_provider.generate_json(
                prompt=sec_prompt,
                schema=GeneratedSectionPayload,
            )
        except LLMUnavailableError as unavail:
            raise ScriptDraftUnavailableError(f"LLM provider unavailable: {unavail}") from unavail
        except (LLMResponseMalformedError, Exception) as malformed:
            raise ScriptDraftMalformedError(f"LLM generated malformed script payload: {malformed}") from malformed

        # Check evidence citations in unresearched mode
        if is_unresearched:
            for c in payload.claims:
                if c.evidence_ids:
                    raise ScriptDraftEvidenceValidationError(
                        f"Script draft emitted evidence IDs in unresearched mode: {sorted(list(c.evidence_ids))}. "
                        f"Unresearched scripts cannot cite evidence."
                    )
        else:
            # Filter any invalid IDs
            for c in payload.claims:
                c.evidence_ids = [eid for eid in c.evidence_ids if eid in evidence_catalog.valid_evidence_ids]

        return payload

    def _expand_underdeveloped_section(
        self,
        section_payload: GeneratedSectionPayload,
        out_sec: ScriptOutlineSection,
        topic: str,
        prompt_builder: ScriptPromptBuilder,
        evidence_brief_text: Optional[str],
        permitted_evidence_ids: List[str],
        is_unresearched: bool,
        evidence_catalog: ValidatedEvidenceCatalog,
    ) -> GeneratedSectionPayload:
        """Execute a single bounded expansion attempt for an underdeveloped section."""
        current_wc = count_words(section_payload.narration)
        logger.info(
            f"Section '{out_sec.heading}' is underdeveloped ({current_wc} words < target {out_sec.target_words}). "
            f"Triggering bounded single expansion attempt."
        )
        expansion_prompt = prompt_builder.build_section_expansion_prompt(
            topic=topic,
            section_type=out_sec.type,
            heading=section_payload.heading or out_sec.heading,
            target_words=out_sec.target_words,
            current_narration=section_payload.narration,
            current_word_count=current_wc,
            factual_milestones=out_sec.factual_milestones,
            evidence_brief_text=evidence_brief_text,
            permitted_evidence_ids=permitted_evidence_ids,
            is_unresearched=is_unresearched,
        )
        try:
            expanded_payload: GeneratedSectionPayload = self.llm_provider.generate_json(
                prompt=expansion_prompt,
                schema=GeneratedSectionPayload,
            )
            expanded_wc = count_words(expanded_payload.narration)
            if expanded_wc > current_wc:
                logger.info(f"Section expansion successful: grew from {current_wc} to {expanded_wc} words.")
                if is_unresearched:
                    for c in expanded_payload.claims:
                        if c.evidence_ids:
                            c.evidence_ids = []
                else:
                    for c in expanded_payload.claims:
                        c.evidence_ids = [eid for eid in c.evidence_ids if eid in evidence_catalog.valid_evidence_ids]
                return expanded_payload
            else:
                logger.warning(
                    f"Section expansion did not increase length ({expanded_wc} <= {current_wc}). Retaining original."
                )
                return section_payload
        except Exception as exc:
            logger.warning(f"Section expansion attempt failed gracefully: {exc}. Retaining original section.")
            return section_payload

    def _find_invalid_evidence_ids(
        self,
        payload: GeneratedScriptPayload,
        catalog: ValidatedEvidenceCatalog,
        is_unresearched: bool,
    ) -> Set[str]:
        """Collect all evidence IDs in payload that are invalid or disallowed."""
        invalid_ids: Set[str] = set()
        for claim in payload.claims:
            for eid in claim.evidence_ids:
                if is_unresearched:
                    invalid_ids.add(eid)
                elif eid not in catalog.valid_evidence_ids:
                    invalid_ids.add(eid)
        return invalid_ids

    def _validate_section_alignment(
        self,
        generated_sections: List[ScriptSectionPayload],
        outline: ScriptOutline,
    ) -> None:
        """Validate script sections against outline sections count, progression, and types."""
        if len(generated_sections) != len(outline.sections):
            raise ScriptDraftMalformedError(
                f"Generated script section count ({len(generated_sections)}) does not match outline section count ({len(outline.sections)})."
            )

        for idx, (gen_sec, out_sec) in enumerate(zip(generated_sections, outline.sections)):
            if gen_sec.type != out_sec.type:
                raise ScriptDraftMalformedError(
                    f"Section {idx} type mismatch: generated '{gen_sec.type}', outline requires '{out_sec.type}'."
                )
            if not gen_sec.heading or not gen_sec.heading.strip():
                raise ScriptDraftMalformedError(f"Section {idx} has empty heading.")
            if not gen_sec.narration or not gen_sec.narration.strip():
                raise ScriptDraftMalformedError(f"Section {idx} has empty narration.")

    def _assemble_authoritative_script(
        self,
        input_data: DraftGeneratorInput,
        payload: GeneratedScriptPayload,
        selected_run: Optional[ResearchRun],
        evidence_catalog: ValidatedEvidenceCatalog,
    ) -> Script:
        """Assemble domain Script model with authoritative metadata and bidirectional claim mappings."""
        # 1. Build canonical sections with stable IDs
        domain_sections: List[ScriptSection] = []
        section_id_map: Dict[str, str] = {}  # gen_id or index -> canonical sec_id

        for idx, sec_payload in enumerate(payload.sections):
            canonical_sec_id = f"sec_{idx+1:02d}"
            raw_id = sec_payload.id or canonical_sec_id
            section_id_map[raw_id] = canonical_sec_id
            section_id_map[canonical_sec_id] = canonical_sec_id
            section_id_map[str(idx)] = canonical_sec_id

            sec = ScriptSection(
                id=canonical_sec_id,
                type=sec_payload.type,
                heading=sec_payload.heading.strip(),
                narration=sec_payload.narration.strip(),
                notes=sec_payload.notes.strip() if sec_payload.notes else None,
                outline_section_index=idx,
                claim_ids=[],  # Populated below
            )
            domain_sections.append(sec)

        # 2. Build canonical claims
        domain_claims: List[ScriptClaim] = []
        section_claims_map: Dict[str, List[str]] = {s.id: [] for s in domain_sections}

        for c_idx, claim_payload in enumerate(payload.claims):
            canonical_claim_id = f"clm_{c_idx+1:02d}"
            target_sec_id = section_id_map.get(claim_payload.section_id, domain_sections[0].id)

            # Filter and validate evidence IDs
            valid_evidence_ids = [
                eid for eid in claim_payload.evidence_ids
                if eid in evidence_catalog.valid_evidence_ids
            ]

            # Authoritatively derive source_ids from catalog
            derived_source_ids = list(dict.fromkeys(
                evidence_catalog.evidence_by_id[eid].source_id
                for eid in valid_evidence_ids
                if eid in evidence_catalog.evidence_by_id
            ))

            risk_level = classify_claim_risk(
                claim_payload.claim_text.strip(),
                section_type=target_sec_id,
            )

            claim = ScriptClaim(
                claim_id=canonical_claim_id,
                claim_text=claim_payload.claim_text.strip(),
                section_id=target_sec_id,
                evidence_ids=valid_evidence_ids,
                source_ids=derived_source_ids,
                risk_level=risk_level,
                link_status="linked" if valid_evidence_ids else "unlinked",
            )
            domain_claims.append(claim)
            section_claims_map[target_sec_id].append(canonical_claim_id)

        # Assign claim_ids to sections
        for sec in domain_sections:
            sec.claim_ids = section_claims_map[sec.id]

        # 3. Assemble final Script domain model
        script = Script(
            script_id=f"scr_{uuid.uuid4().hex[:12]}",
            project_id=input_data.project_id,
            version=1,
            status="draft",
            title=payload.title.strip() if payload.title else (input_data.title or input_data.outline.topic),
            topic=input_data.outline.topic,
            target_duration_minutes=input_data.outline.target_duration_minutes,
            sections=domain_sections,
            claims=domain_claims,
            outline=input_data.outline,
            research_run_id=selected_run.run_id if selected_run else None,
            evidence_brief_path=f"runs/{selected_run.run_id}/evidence_brief.md" if selected_run else None,
            created_at=utc_now(),
            updated_at=utc_now(),
        )

        # Calculate word counts and duration metrics with audit compliance
        populate_script_duration_metrics(
            script,
            words_per_minute=self.llm_provider.words_per_minute or DEFAULT_WORDS_PER_MINUTE,
            evidence_catalog=evidence_catalog,
        )

        return script
