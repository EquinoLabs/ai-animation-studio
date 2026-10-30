"""Evidence-Aware Outline Pipeline for Script Builder (Phase 4, P4-S3).

Transforms project research and creative directives into structured, evidence-grounded
narrative outlines (`ScriptOutline`) before full narration generation.
"""

from dataclasses import dataclass, field
from datetime import datetime
import logging
from typing import Any, Dict, List, Optional, Set, Tuple
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
    GeneratedOutlinePayload,
    OutlineSectionPayload,
    ScriptOutline,
    ScriptOutlineSection,
    SECTION_TYPES,
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
from backend.app.services.scriptwriter.policy import (
    ScriptPromptBuilder,
    WritingPolicy,
    detect_narrative_structure,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ScriptOutlinerError(Exception):
    """Base exception for all script outliner failures."""
    pass


class ScriptOutlinerSecurityError(ScriptOutlinerError):
    """Raised when project isolation, security validation, or cross-project access fails."""
    pass


class ScriptOutlinerRunError(ScriptOutlinerError):
    """Raised when research run selection fails or the selected run is invalid/incomplete."""
    pass


class ScriptOutlinerUnavailableError(ScriptOutlinerError):
    """Raised when the LLM provider service is unreachable or unavailable."""
    pass


class ScriptOutlinerMalformedError(ScriptOutlinerError):
    """Raised when LLM output is structurally malformed or violates domain constraints."""
    pass


class OutlineEvidenceValidationError(ScriptOutlinerError):
    """Raised when generated outline cites unknown or invalid evidence IDs after corrective retry."""
    pass


# ---------------------------------------------------------------------------
# Input Contract
# ---------------------------------------------------------------------------

class OutlinerInput(BaseModel):
    """Input parameters for evidence-aware outline generation."""
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., description="Target project identifier")
    topic: str = Field(..., min_length=1, max_length=500, description="Core topic for the script")
    target_duration_minutes: float = Field(default=5.0, gt=0.0, le=60.0, description="Target duration in minutes")
    research_run_id: Optional[str] = Field(default=None, description="Explicit research run ID to ground outline")
    allow_unresearched: bool = Field(default=False, description="Allow generating outline without research run")
    title: Optional[str] = Field(default=None, max_length=200, description="Optional working title")
    writing_policy: Optional[WritingPolicy] = Field(default=None, description="Custom writing policy directives")

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, v: str) -> str:
        clean = (v or "").strip()
        if not clean or not ID_REGEX.match(clean):
            raise ValueError(f"Invalid project_id '{v}'. Must be alphanumeric with underscores or dashes.")
        return clean

    @field_validator("topic")
    @classmethod
    def validate_topic(cls, v: str) -> str:
        clean = (v or "").strip()
        if not clean:
            raise ValueError("Topic must not be empty or blank.")
        return clean

    @field_validator("research_run_id")
    @classmethod
    def validate_run_id(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            clean = v.strip()
            if not clean or not ID_REGEX.match(clean):
                raise ValueError(f"Invalid research_run_id '{v}'. Must be alphanumeric with underscores or dashes.")
            return clean
        return None


# ---------------------------------------------------------------------------
# Evidence Catalog Container
# ---------------------------------------------------------------------------

@dataclass
class ValidatedEvidenceCatalog:
    """Catalog of run-scoped evidence and associated source metadata."""
    run_id: str
    evidence_by_id: Dict[str, EvidenceRecord] = field(default_factory=dict)
    sources_by_id: Dict[str, SourceRecord] = field(default_factory=dict)

    @property
    def valid_evidence_ids(self) -> Set[str]:
        return set(self.evidence_by_id.keys())

    def format_catalog_for_prompt(self) -> str:
        """Format allowed evidence IDs and their excerpts for LLM prompting."""
        if not self.evidence_by_id:
            return "No verified research evidence items available in this run."

        lines = ["ALLOWED EVIDENCE ITEMS:"]
        for e_id, ev in sorted(self.evidence_by_id.items()):
            source = self.sources_by_id.get(ev.source_id)
            source_title = source.title if source else "Unknown Source"
            source_url = source.url if source else ""
            excerpt = (ev.excerpt or "").strip().replace("\n", " ")
            if len(excerpt) > 160:
                excerpt = excerpt[:157] + "..."
            lines.append(f"- ID: {e_id} | Source: {source_title} ({source_url}) | Excerpt: \"{excerpt}\"")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Script Outliner Service
# ---------------------------------------------------------------------------

class ScriptOutliner:
    """Orchestrates evidence-aware outline generation with provenance validation."""

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
        self.project_service = project_service or ProjectService(projects_root=Path(self.settings.PROJECTS_DIR))
        self.brief_builder = brief_builder or EvidenceBriefBuilder(
            storage_service=self.research_storage, settings=self.settings
        )

    def generate_outline(self, input_data: OutlinerInput) -> ScriptOutline:
        """Generate a validated, evidence-aware ScriptOutline from input directives.

        Args:
            input_data: OutlinerInput contract.

        Returns:
            Authoritatively assembled and validated ScriptOutline.

        Raises:
            ScriptOutlinerSecurityError: On project isolation or security violations.
            ScriptOutlinerRunError: On run selection or run status failures.
            ScriptOutlinerUnavailableError: If LLM is unreachable.
            ScriptOutlinerMalformedError: If LLM output violates schema or narrative progression.
            OutlineEvidenceValidationError: If model outputs unknown evidence IDs after corrective retry.
        """
        # 1. Project boundary check
        self._verify_project(input_data.project_id)

        # 2. Research run selection & validation
        selected_run = self._select_research_run(
            project_id=input_data.project_id,
            explicit_run_id=input_data.research_run_id,
            allow_unresearched=input_data.allow_unresearched,
        )

        evidence_brief_text: Optional[str] = None
        evidence_catalog = ValidatedEvidenceCatalog(run_id=selected_run.run_id if selected_run else "")

        if selected_run:
            # 3. Retrieve or recover run-scoped evidence brief
            evidence_brief_text = self._get_or_recover_evidence_brief(
                project_id=input_data.project_id,
                run=selected_run,
            )

            # 4. Construct validated run-scoped evidence catalog
            evidence_catalog = self._build_evidence_catalog(
                project_id=input_data.project_id,
                run=selected_run,
            )

        # 5. Writing policy setup
        policy = input_data.writing_policy or WritingPolicy(
            narrative_structure=detect_narrative_structure(input_data.topic),
            words_per_minute=self.llm_provider.words_per_minute or DEFAULT_WORDS_PER_MINUTE,
        )
        prompt_builder = ScriptPromptBuilder(policy=policy)

        # 6. Build prompt
        prompt = self._build_outline_prompt(
            prompt_builder=prompt_builder,
            input_data=input_data,
            evidence_brief_text=evidence_brief_text,
            evidence_catalog=evidence_catalog,
        )

        # 7. LLM invocation with corrective retry for unknown evidence IDs
        payload = self._invoke_llm_with_validation(
            prompt=prompt,
            prompt_builder=prompt_builder,
            input_data=input_data,
            evidence_catalog=evidence_catalog,
        )

        # 8. Narrative progression and section constraints validation
        self._validate_narrative_progression(payload.sections)

        # 9. Authoritative assembly of ScriptOutline
        return self._assemble_authoritative_outline(
            input_data=input_data,
            payload=payload,
            selected_run=selected_run,
        )

    # -----------------------------------------------------------------------
    # Helper Methods
    # -----------------------------------------------------------------------

    def _verify_project(self, project_id: str) -> None:
        """Verify that the project exists and does not violate security constraints."""
        try:
            self.project_service.get_project(project_id)
        except (ProjectSecurityError, ValueError) as sec_err:
            raise ScriptOutlinerSecurityError(f"Security error for project '{project_id}': {sec_err}") from sec_err
        except ProjectNotFoundError as not_found:
            raise ScriptOutlinerSecurityError(f"Project '{project_id}' not found: {not_found}") from not_found

    def _select_research_run(
        self,
        project_id: str,
        explicit_run_id: Optional[str],
        allow_unresearched: bool,
    ) -> Optional[ResearchRun]:
        """Select and validate the research run for outline generation."""
        if explicit_run_id:
            try:
                run = self.research_storage.get_run(project_id, explicit_run_id)
            except (ResearchSecurityError, ProjectSecurityError, ValueError) as sec_err:
                raise ScriptOutlinerSecurityError(f"Security error accessing run '{explicit_run_id}': {sec_err}") from sec_err
            except ResearchRunNotFoundError as not_found:
                raise ScriptOutlinerRunError(f"Explicit research run '{explicit_run_id}' not found in project '{project_id}'.") from not_found
            except ResearchValidationError as val_err:
                raise ScriptOutlinerRunError(f"Corrupted research run '{explicit_run_id}': {val_err}") from val_err

            if run.status != "completed":
                raise ScriptOutlinerRunError(
                    f"Explicit research run '{explicit_run_id}' has status '{run.status}'; only 'completed' runs can ground outlines."
                )
            return run

        if not explicit_run_id and allow_unresearched:
            return None

        # No explicit run_id provided: inspect all runs in project
        runs = self.research_storage.list_runs(project_id)
        if not runs:
            raise ScriptOutlinerRunError(
                f"No research runs found in project '{project_id}'. Research must be conducted or 'allow_unresearched=True' must be specified."
            )

        # Sort runs by started_at descending (newest first)
        sorted_runs = sorted(runs, key=lambda r: r.started_at, reverse=True)
        newest_run = sorted_runs[0]

        if newest_run.status != "completed":
            raise ScriptOutlinerRunError(
                f"Newest research run '{newest_run.run_id}' has status '{newest_run.status}' (started: {newest_run.started_at.isoformat()}). "
                f"Cannot ground outline: incomplete or failed runs are rejected and cannot fall back to older runs."
            )

        return newest_run

    def _get_or_recover_evidence_brief(
        self,
        project_id: str,
        run: ResearchRun,
    ) -> str:
        """Retrieve the persisted run-scoped evidence brief, or recover it deterministically."""
        # 1. Try reading existing run brief
        content = self.research_storage.get_run_evidence_brief(project_id, run.run_id)
        if content and content.strip():
            return content

        # 2. If brief is missing or corrupted, rebuild and persist under run-scoped path
        run_scoped_path = f"runs/{run.run_id}/evidence_brief.md"
        try:
            _, rebuilt_content = self.brief_builder.build_and_save_brief(
                project_id=project_id,
                run_id=run.run_id,
                filename=run_scoped_path,
            )
            return rebuilt_content
        except EvidenceBriefError as eb_err:
            raise ScriptOutlinerRunError(
                f"Failed to recover evidence brief for run '{run.run_id}': {eb_err}"
            ) from eb_err

    def _build_evidence_catalog(
        self,
        project_id: str,
        run: ResearchRun,
    ) -> ValidatedEvidenceCatalog:
        """Construct catalog of evidence records and sources strictly belonging to this run."""
        catalog = ValidatedEvidenceCatalog(run_id=run.run_id)

        all_sources = {s.source_id: s for s in self.research_storage.list_sources(project_id)}
        evidence_items = self.research_storage.list_evidence(project_id, run_id=run.run_id)

        for ev in evidence_items:
            # Integrity check: evidence must belong to this run
            if ev.run_id != run.run_id:
                continue

            # Integrity check: evidence must have usable excerpt
            if not (ev.excerpt or "").strip():
                continue

            # Integrity check: evidence must resolve to a valid source record
            source = all_sources.get(ev.source_id)
            if not source:
                raise ScriptOutlinerRunError(
                    f"Evidence '{ev.evidence_id}' references unknown source '{ev.source_id}'."
                )

            catalog.evidence_by_id[ev.evidence_id] = ev
            catalog.sources_by_id[ev.source_id] = source

        return catalog

    def _build_outline_prompt(
        self,
        prompt_builder: ScriptPromptBuilder,
        input_data: OutlinerInput,
        evidence_brief_text: Optional[str],
        evidence_catalog: ValidatedEvidenceCatalog,
    ) -> str:
        """Build the outline prompt including evidence catalog constraints."""
        catalog_section = ""
        if evidence_catalog.valid_evidence_ids:
            catalog_section = (
                f"\n--- RUN EVIDENCE CATALOG ---\n"
                f"{evidence_catalog.format_catalog_for_prompt()}\n"
                f"--- END RUN EVIDENCE CATALOG ---\n\n"
                f"STRICT EVIDENCE CONSTRAINTS:\n"
                f"- You may ONLY cite evidence IDs from the catalog above.\n"
                f"- Permitted evidence IDs: {sorted(list(evidence_catalog.valid_evidence_ids))}\n"
                f"- Do NOT hallucinate or cite any evidence ID not explicitly listed in the catalog.\n"
                f"- If a section does not rely on specific retrieved evidence, leave 'evidence_ids' as an empty list.\n"
            )

        base_prompt = prompt_builder.build_outline_prompt(
            topic=input_data.topic,
            target_duration_minutes=input_data.target_duration_minutes,
            evidence_brief_text=evidence_brief_text,
            title=input_data.title,
        )

        return f"{base_prompt}\n{catalog_section}"

    def _invoke_llm_with_validation(
        self,
        prompt: str,
        prompt_builder: ScriptPromptBuilder,
        input_data: OutlinerInput,
        evidence_catalog: ValidatedEvidenceCatalog,
    ) -> GeneratedOutlinePayload:
        """Invoke LLM and perform one bounded corrective retry if invalid evidence IDs are cited."""
        try:
            payload: GeneratedOutlinePayload = self.llm_provider.generate_json(
                prompt=prompt,
                schema=GeneratedOutlinePayload,
            )
        except LLMUnavailableError as unavail:
            raise ScriptOutlinerUnavailableError(f"LLM provider unavailable: {unavail}") from unavail
        except (LLMResponseMalformedError, Exception) as malformed:
            raise ScriptOutlinerMalformedError(f"LLM generated malformed outline payload: {malformed}") from malformed

        # Check evidence citations
        invalid_ids = self._find_invalid_evidence_ids(payload, evidence_catalog)
        if not invalid_ids:
            return payload

        # Bounded corrective retry (one-shot)
        logger.warning(
            "Generated outline cited unknown evidence IDs: %s. Executing bounded corrective retry.",
            invalid_ids,
        )
        retry_prompt = (
            f"{prompt}\n\n"
            f"CORRECTION REQUIRED:\n"
            f"Your previous attempt cited the following unknown or disallowed evidence IDs: {sorted(list(invalid_ids))}.\n"
            f"Permitted evidence IDs are strictly: {sorted(list(evidence_catalog.valid_evidence_ids))}.\n"
            f"Please regenerate the outline JSON, using ONLY the permitted evidence IDs or an empty list []."
        )

        try:
            retry_payload: GeneratedOutlinePayload = self.llm_provider.generate_json(
                prompt=retry_prompt,
                schema=GeneratedOutlinePayload,
            )
        except LLMUnavailableError as unavail:
            raise ScriptOutlinerUnavailableError(f"LLM provider unavailable on retry: {unavail}") from unavail
        except (LLMResponseMalformedError, Exception) as malformed:
            raise ScriptOutlinerMalformedError(f"LLM generated malformed outline on retry: {malformed}") from malformed

        remaining_invalid = self._find_invalid_evidence_ids(retry_payload, evidence_catalog)
        if remaining_invalid:
            raise OutlineEvidenceValidationError(
                f"Generated outline cited invalid evidence IDs after corrective retry: {sorted(list(remaining_invalid))}. "
                f"Allowed IDs: {sorted(list(evidence_catalog.valid_evidence_ids))}."
            )

        return retry_payload

    def _find_invalid_evidence_ids(
        self,
        payload: GeneratedOutlinePayload,
        catalog: ValidatedEvidenceCatalog,
    ) -> Set[str]:
        """Collect all evidence IDs in payload that are not in the validated catalog."""
        invalid_ids: Set[str] = set()
        for sec in payload.sections:
            for eid in sec.evidence_ids:
                if eid not in catalog.valid_evidence_ids:
                    invalid_ids.add(eid)
        return invalid_ids

    def _validate_narrative_progression(self, sections: List[OutlineSectionPayload]) -> None:
        """Validate coherent narrative progression and section structural constraints.

        Progression requirements:
        1. Minimum 5 sections.
        2. First section must be 'hook'.
        3. Second section must be 'setup'.
        4. One or more 'middle' sections.
        5. Next section must be 'climax'.
        6. Final section must be 'takeaway'.
        7. Section headings must not be empty.
        8. target_words must be > 0.
        9. Total outline target words must be > 50.
        """
        if len(sections) < 5:
            raise ScriptOutlinerMalformedError(
                f"Outline must have at least 5 sections for full narrative progression; received {len(sections)}."
            )

        types = [s.type for s in sections]

        # 1. First section must be hook
        if types[0] != "hook":
            raise ScriptOutlinerMalformedError(f"First section must be 'hook', got '{types[0]}'.")

        # 2. Second section must be setup
        if types[1] != "setup":
            raise ScriptOutlinerMalformedError(f"Second section must be 'setup', got '{types[1]}'.")

        # 3. Final section must be takeaway
        if types[-1] != "takeaway":
            raise ScriptOutlinerMalformedError(f"Final section must be 'takeaway', got '{types[-1]}'.")

        # 4. Section before takeaway must be climax
        if types[-2] != "climax":
            raise ScriptOutlinerMalformedError(f"Section preceding 'takeaway' must be 'climax', got '{types[-2]}'.")

        # 5. Middle sections
        middle_types = types[2:-2]
        if not middle_types or not all(t == "middle" for t in middle_types):
            raise ScriptOutlinerMalformedError(
                f"Sections between 'setup' and 'climax' must all be of type 'middle'; got {middle_types}."
            )

        # 6. Detailed checks on each section
        total_words = 0
        for idx, sec in enumerate(sections):
            if not sec.heading or not sec.heading.strip():
                raise ScriptOutlinerMalformedError(f"Section {idx} ('{sec.type}') has empty heading.")
            if sec.target_words <= 0:
                raise ScriptOutlinerMalformedError(f"Section {idx} target_words must be > 0.")
            total_words += sec.target_words

        if total_words < 50:
            raise ScriptOutlinerMalformedError(
                f"Total outline word target ({total_words}) is too low (minimum 50 words required)."
            )

    def _assemble_authoritative_outline(
        self,
        input_data: OutlinerInput,
        payload: GeneratedOutlinePayload,
        selected_run: Optional[ResearchRun],
    ) -> ScriptOutline:
        """Construct authoritative ScriptOutline domain model, overriding model metadata."""
        domain_sections: List[ScriptOutlineSection] = []
        for idx, sec_payload in enumerate(payload.sections):
            domain_sections.append(
                ScriptOutlineSection(
                    type=sec_payload.type,
                    heading=sec_payload.heading.strip(),
                    target_words=sec_payload.target_words,
                    factual_milestones=[m.strip() for m in sec_payload.factual_milestones if m and m.strip()],
                    visual_concept=sec_payload.visual_concept.strip() if sec_payload.visual_concept else None,
                    evidence_ids=list(dict.fromkeys(sec_payload.evidence_ids)),  # Deduplicated order-preserved
                )
            )

        return ScriptOutline(
            outline_id=f"out_{uuid.uuid4().hex[:8]}",
            project_id=input_data.project_id,
            topic=input_data.topic,
            target_duration_minutes=input_data.target_duration_minutes,
            narrative_structure=payload.narrative_structure.strip(),
            sections=domain_sections,
            research_run_id=selected_run.run_id if selected_run else None,
            created_at=utc_now(),
        )
