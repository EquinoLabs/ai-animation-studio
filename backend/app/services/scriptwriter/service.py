"""Script Builder service managing iterative script drafting, revision, and finalization."""

import json
import logging
import os
from pathlib import Path
import re
import string
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

logger = logging.getLogger(__name__)

from backend.app.config import Settings, get_settings
from backend.app.domain.project import utc_now
from backend.app.domain.script import (
    ID_REGEX,
    ConversationEvent,
    ConversationEventType,
    Script,
    ScriptClaim,
    ScriptOutline,
    ScriptSection,
    populate_script_duration_metrics,
)
from backend.app.llm.base import (
    LLMProvider,
    LLMProviderError,
    LLMResponseMalformedError,
)
from backend.app.llm import get_llm_provider
from backend.app.services.project.service import (
    Project,
    ProjectNotFoundError,
    ProjectSecurityError,
    ProjectService,
)
from backend.app.services.research.storage import (
    ResearchRunNotFoundError,
    ResearchSecurityError,
    ResearchStorageError,
    ResearchStorageService,
    ResearchValidationError,
)
from backend.app.services.scriptwriter.generator import (
    DraftGeneratorInput,
    ScriptDraftEvidenceValidationError,
    ScriptDraftGenerator,
    ScriptDraftGeneratorError,
    ScriptDraftMalformedError,
    ScriptDraftRunError,
    ScriptDraftSecurityError,
    ScriptDraftUnavailableError,
)
from backend.app.services.scriptwriter.outliner import (
    OutlineEvidenceValidationError,
    OutlinerInput,
    ScriptOutliner,
    ScriptOutlinerError,
    ScriptOutlinerMalformedError,
    ScriptOutlinerRunError,
    ScriptOutlinerSecurityError,
    ScriptOutlinerUnavailableError,
    ValidatedEvidenceCatalog,
)


class ScriptError(Exception):
    """Base exception for script service errors."""


class ScriptNotFoundError(ScriptError):
    """Raised when a requested script draft or version does not exist."""


class ScriptValidationError(ScriptError):
    """Raised when script input or structure violates domain constraints."""


class ScriptRevisionEvidenceValidationError(ScriptValidationError):
    """Raised when revised script claims cite evidence records not in the run catalog."""


class ScriptFinalizationError(ScriptError):
    """Raised when an attempt to finalize a draft fails lifecycle prerequisites."""


class ScriptStorageError(ScriptError):
    """Raised when script data on disk cannot be read or is corrupted."""


def _atomic_write_json(path: Path, data: Any) -> None:
    """Atomically write JSON data to file using a temporary file and replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp_{uuid.uuid4().hex[:8]}")
    try:
        content = json.dumps(data, indent=2, default=str)
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, path)
    except Exception as exc:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise ScriptStorageError(f"Failed to write artifact at '{path}': {exc}") from exc


_PROPOSITION_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "in", "on", "at", "to", "for", "of", "and", "or", "that", "this", "it",
    "by", "with", "from", "as", "into", "about",
}


def _normalize_proposition(text: str) -> str:
    """Normalize proposition text for conservative claim identity comparison."""
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
    """Conservatively determine if two assertions express the exact same factual proposition.

    Preserves an existing claim ID only when the revised claim is demonstrably the same factual proposition.
    Materially changed claims, different numbers, different key terms, or negation return False.
    """
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


class ScriptService:
    """Manages the lifecycle of video scripts: creation, revision, acceptance, and finalization."""

    def __init__(
        self,
        project_service: Optional[ProjectService] = None,
        llm_provider: Optional[LLMProvider] = None,
        research_storage: Optional[ResearchStorageService] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.words_per_minute = getattr(self.settings, "WORDS_PER_MINUTE", 140)
        self.project_service = project_service or ProjectService(settings=self.settings)
        self.llm_provider = llm_provider or get_llm_provider(settings=self.settings)
        self.research_storage = research_storage or ResearchStorageService(settings=self.settings)
        self.outliner = ScriptOutliner(
            llm_provider=self.llm_provider,
            research_storage=self.research_storage,
            project_service=self.project_service,
            settings=self.settings,
        )
        self.generator = ScriptDraftGenerator(
            llm_provider=self.llm_provider,
            research_storage=self.research_storage,
            project_service=self.project_service,
            settings=self.settings,
        )

    def _get_project_dirs(self, project_id: str) -> Dict[str, Path]:
        """Validate project exists and resolve script directories."""
        project = self.project_service.get_project(project_id)
        script_dir = project.script_dir
        versions_dir = script_dir / "versions"
        finalized_dir = script_dir / "finalized"
        outlines_dir = script_dir / "outlines"
        versions_dir.mkdir(parents=True, exist_ok=True)
        finalized_dir.mkdir(parents=True, exist_ok=True)
        outlines_dir.mkdir(parents=True, exist_ok=True)

        return {
            "project_dir": project.root_dir,
            "script_dir": script_dir,
            "versions_dir": versions_dir,
            "finalized_dir": finalized_dir,
            "outlines_dir": outlines_dir,
            "current_file": script_dir / "current.json",
            "conversation_file": script_dir / "conversation.json",
            "state_file": script_dir / "workflow_state.json",
            "script_file": script_dir / "script.json",
            "commit_marker": script_dir / ".finalizing.json",
        }

    def _reconcile_finalization(self, project_id: str, dirs: Dict[str, Path]) -> None:
        """Reconcile in-flight or interrupted finalization transactions."""
        marker_file = dirs["commit_marker"]
        if marker_file.exists():
            try:
                commit_data = json.loads(marker_file.read_text(encoding="utf-8"))
                finalized_script = Script.model_validate(commit_data["finalized_script"])
                self._complete_finalization_transaction(project_id, dirs, finalized_script)
            except Exception:
                try:
                    marker_file.unlink()
                except OSError:
                    pass

    def _complete_finalization_transaction(
        self,
        project_id: str,
        dirs: Dict[str, Path],
        finalized: Script,
    ) -> None:
        """Idempotently execute all remaining steps of a finalization transaction."""
        finalized_path = dirs["finalized_dir"] / f"v{finalized.version:03d}.json"

        # 1. Finalized artifact (immutable ground truth)
        if not finalized_path.exists():
            _atomic_write_json(finalized_path, finalized.model_dump(mode="json"))
        else:
            # Ground truth is the existing immutable artifact; never overwrite it
            try:
                existing_data = json.loads(finalized_path.read_text(encoding="utf-8"))
                finalized = Script.model_validate(existing_data)
            except Exception:
                pass

        # 2. Downstream mirrored script.json
        _atomic_write_json(dirs["script_file"], finalized.model_dump(mode="json"))

        # 3. Current working copy
        _atomic_write_json(dirs["current_file"], finalized.model_dump(mode="json"))

        # 4. Conversation event (only append if not already logged for this version)
        history = self.get_conversation_history(project_id)
        already_logged = any(
            e.event_type == "script_finalized" and e.script_version == finalized.version
            for e in history
        )
        if not already_logged:
            self._append_conversation_event(
                dirs["conversation_file"],
                event_type="script_finalized",
                content=f"Finalized version {finalized.version}: '{finalized.title}'",
                script_version=finalized.version,
                metadata={"finalized_path": str(finalized_path)},
            )

        # 5. Project stage metadata
        self.project_service.update_stage_status(
            project_id=project_id,
            stage_name="script",
            status="completed",
            artifacts=[str(finalized_path), str(dirs["script_file"])],
        )

        # 6. Remove commit marker
        if dirs["commit_marker"].exists():
            try:
                dirs["commit_marker"].unlink()
            except OSError:
                pass

    def _load_workflow_state(self, state_file: Path) -> Dict[str, Any]:
        """Load persistent acceptance/rejection workflow state."""
        if not state_file.exists():
            return {
                "accepted_version": None,
                "rejected_version": None,
                "rejection_reason": None,
            }
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ScriptStorageError(f"Corrupt workflow state at '{state_file}': {exc}") from exc

    def _save_workflow_state(self, state_file: Path, state: Dict[str, Any]) -> None:
        """Persist workflow state atomically."""
        _atomic_write_json(state_file, state)

    def _append_conversation_event(
        self,
        conversation_file: Path,
        event_type: ConversationEventType,
        content: Optional[str] = None,
        script_version: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ConversationEvent:
        """Append an event to conversation.json."""
        events: List[Dict[str, Any]] = []
        if conversation_file.exists():
            try:
                events = json.loads(conversation_file.read_text(encoding="utf-8"))
                if not isinstance(events, list):
                    events = []
            except Exception as exc:
                raise ScriptStorageError(f"Corrupt conversation history at '{conversation_file}': {exc}") from exc

        event = ConversationEvent(
            event_type=event_type,
            content=content,
            script_version=script_version,
            metadata=metadata or {},
        )
        events.append(event.model_dump(mode="json"))
        _atomic_write_json(conversation_file, events)
        return event

    def _allocate_next_version(self, dirs: Dict[str, Path], current_version: Optional[int] = None) -> int:
        """Calculate the next strictly increasing, unique script version number.

        Inspects both `versions/` and `finalized/` directories to ensure that draft snapshots
        and finalized versions are accounted for and historical artifacts are never overwritten.
        """
        existing_nums = set()
        for search_dir in (dirs["versions_dir"], dirs["finalized_dir"]):
            if search_dir.exists():
                for f in search_dir.glob("v*.json"):
                    try:
                        existing_nums.add(int(f.stem.lstrip("v")))
                    except ValueError:
                        continue
        if current_version is not None:
            existing_nums.add(current_version)

        return max(existing_nums, default=0) + 1

    def create_outline(
        self,
        project_id: str,
        topic: str,
        target_duration_minutes: float = 5.0,
        title: Optional[str] = None,
        research_run_id: Optional[str] = None,
        allow_unresearched: bool = False,
    ) -> ScriptOutline:
        """Create and persist a standalone ScriptOutline without creating or modifying a draft.

        Args:
            project_id: Target project identifier.
            topic: Topic string.
            target_duration_minutes: Intended duration in minutes.
            title: Optional working title.
            research_run_id: Explicit research run ID to ground outline.
            allow_unresearched: Explicit authorization to generate without research.

        Returns:
            The generated and persisted ScriptOutline.

        Raises:
            ScriptValidationError: If input validation fails or research run policy is violated.
            ScriptStorageError: If persistence fails.
        """
        clean_topic = (topic or "").strip()
        if not clean_topic:
            raise ScriptValidationError("Topic cannot be empty or whitespace-only.")
        if len(clean_topic) > 1000:
            raise ScriptValidationError("Topic must not exceed 1000 characters.")
        if target_duration_minutes <= 0.0 or target_duration_minutes > 60.0:
            raise ScriptValidationError("Target duration must be between 0.1 and 60.0 minutes.")

        dirs = self._get_project_dirs(project_id)

        # Execute outline pipeline
        try:
            outline_input = OutlinerInput(
                project_id=project_id,
                topic=clean_topic,
                target_duration_minutes=target_duration_minutes,
                research_run_id=research_run_id,
                allow_unresearched=allow_unresearched,
                title=title,
            )
            outline = self.outliner.generate_outline(outline_input)
        except (ScriptOutlinerSecurityError, ScriptOutlinerRunError) as run_err:
            raise ScriptValidationError(f"Cannot generate outline: {run_err}") from run_err
        except ScriptOutlinerUnavailableError as unavail_err:
            raise ScriptError(f"LLM provider unavailable for outline generation: {unavail_err}") from unavail_err
        except (ScriptOutlinerMalformedError, OutlineEvidenceValidationError) as mal_err:
            raise ScriptValidationError(f"Outline generation failed validation: {mal_err}") from mal_err
        except Exception as exc:
            raise ScriptError(f"Unexpected outline generation failure: {exc}") from exc

        # Persist standalone outline artifact under script/outlines/<outline_id>.json
        outline_file = dirs["outlines_dir"] / f"{outline.outline_id}.json"
        _atomic_write_json(outline_file, outline.model_dump(mode="json"))

        # Log conversation event (outline_generated)
        self._append_conversation_event(
            dirs["conversation_file"],
            event_type="outline_generated",
            content=f"Generated outline: '{outline.topic}' ({len(outline.sections)} sections)",
            metadata={
                "outline_id": outline.outline_id,
                "research_run_id": outline.research_run_id,
                "sections_count": len(outline.sections),
            },
        )

        return outline

    def get_outline(self, project_id: str, outline_id: str) -> ScriptOutline:
        """Retrieve a persisted standalone outline artifact.

        Args:
            project_id: Project identifier.
            outline_id: Outline identifier.

        Returns:
            The loaded ScriptOutline.

        Raises:
            ScriptNotFoundError: If the outline artifact does not exist.
            ScriptStorageError: If reading or validation fails.
        """
        clean_oid = (outline_id or "").strip()
        if not clean_oid or not ID_REGEX.match(clean_oid):
            raise ScriptValidationError(f"Invalid outline_id '{outline_id}'.")

        dirs = self._get_project_dirs(project_id)
        outline_file = dirs["outlines_dir"] / f"{clean_oid}.json"
        if not outline_file.exists():
            raise ScriptNotFoundError(f"Outline '{clean_oid}' not found in project '{project_id}'.")

        try:
            data = json.loads(outline_file.read_text(encoding="utf-8"))
            return ScriptOutline.model_validate(data)
        except Exception as exc:
            raise ScriptStorageError(f"Failed to read outline artifact '{outline_file}': {exc}") from exc

    def start_script(
        self,
        project_id: str,
        topic: str,
        target_duration_minutes: float = 5.0,
        title: Optional[str] = None,
        research_run_id: Optional[str] = None,
        allow_unresearched: bool = False,
        outline: Optional[ScriptOutline] = None,
    ) -> Script:
        """Generate an initial structured Script draft from a topic using the approved roadmap pipeline.

        Args:
            project_id: Target project identifier.
            topic: Topic string.
            target_duration_minutes: Target runtime in minutes (soft guideline).
            title: Optional working title.
            research_run_id: Explicit research run ID to ground the script.
            allow_unresearched: Explicit authorization to generate without research grounding.
            outline: Optional pre-existing ScriptOutline to expand into narration.

        Returns:
            The created, validated, and persisted Script draft.

        Raises:
            ScriptValidationError: If input validation fails or research run policy is violated.
            ScriptError: If generation or persistence fails.
        """
        clean_topic = (topic or "").strip()
        if not clean_topic:
            raise ScriptValidationError("Topic cannot be empty or whitespace-only.")
        if len(clean_topic) > 1000:
            raise ScriptValidationError("Topic must not exceed 1000 characters.")
        if target_duration_minutes <= 0.0 or target_duration_minutes > 60.0:
            raise ScriptValidationError("Target duration must be between 0.1 and 60.0 minutes.")

        dirs = self._get_project_dirs(project_id)

        # 1. Log topic submission event
        self._append_conversation_event(
            dirs["conversation_file"],
            event_type="topic_submission",
            content=clean_topic,
            metadata={"target_duration_minutes": target_duration_minutes},
        )

        # 2. Resolve or create ScriptOutline
        active_outline: ScriptOutline
        if outline is not None:
            active_outline = outline
        else:
            # Automatic outline creation following research run selection policy
            active_outline = self.create_outline(
                project_id=project_id,
                topic=clean_topic,
                target_duration_minutes=target_duration_minutes,
                title=title,
                research_run_id=research_run_id,
                allow_unresearched=allow_unresearched,
            )

        # 3. Generate Script draft from outline
        try:
            draft_input = DraftGeneratorInput(
                project_id=project_id,
                outline=active_outline,
                allow_unresearched=allow_unresearched,
                title=title,
            )
            generated = self.generator.generate_draft(draft_input)
        except (ScriptDraftSecurityError, ScriptDraftRunError) as run_err:
            raise ScriptValidationError(f"Cannot generate draft: {run_err}") from run_err
        except ScriptDraftUnavailableError as unavail_err:
            raise ScriptError(f"LLM provider unavailable for draft generation: {unavail_err}") from unavail_err
        except (ScriptDraftMalformedError, ScriptDraftEvidenceValidationError) as mal_err:
            raise ScriptValidationError(f"Draft generation failed validation: {mal_err}") from mal_err
        except Exception as exc:
            raise ScriptError(f"Failed to generate draft: {exc}") from exc

        # 3. Determine version allocation
        version = self._allocate_next_version(dirs, current_version=None)
        generated.version = version
        generated.project_id = project_id
        generated.status = "draft"
        generated.updated_at = utc_now()
        populate_script_duration_metrics(generated, words_per_minute=self.words_per_minute)

        # 4. Strict validation before any disk replacement
        try:
            Script.model_validate(generated)
            generated.validate_content()
        except Exception as val_err:
            raise ScriptValidationError(f"Generated script is invalid: {val_err}") from val_err

        # 5. Atomic persistence: snapshot first, then current working copy
        version_file = dirs["versions_dir"] / f"v{version:03d}.json"
        if version_file.exists():
            raise ScriptStorageError(f"Snapshot version file '{version_file.name}' already exists.")

        _atomic_write_json(version_file, generated.model_dump(mode="json"))
        _atomic_write_json(dirs["current_file"], generated.model_dump(mode="json"))

        # 6. Reset workflow state
        self._save_workflow_state(
            dirs["state_file"],
            {
                "accepted_version": None,
                "rejected_version": None,
                "rejection_reason": None,
            },
        )

        # 7. Append conversation events
        self._append_conversation_event(
            dirs["conversation_file"],
            event_type="draft_generated",
            content=generated.title,
            script_version=version,
            metadata={
                "sections_count": len(generated.sections),
                "research_run_id": generated.research_run_id,
            },
        )

        # 8. Update project stage metadata
        self.project_service.update_stage_status(project_id, "script", "in_progress")

        return generated

    def _resolve_revision_catalog(
        self, project_id: str, script: Script
    ) -> Tuple[bool, ValidatedEvidenceCatalog]:
        """Resolve evidence catalog for revision based on the script's research run grounding."""
        run_id = script.research_run_id
        if not run_id:
            return False, ValidatedEvidenceCatalog(run_id="")

        try:
            run = self.research_storage.get_run(project_id, run_id)
        except (ResearchSecurityError, ProjectSecurityError, ValueError) as sec_err:
            raise ScriptRevisionEvidenceValidationError(
                f"Security violation accessing research run '{run_id}': {sec_err}"
            ) from sec_err
        except ResearchRunNotFoundError as not_found:
            raise ScriptRevisionEvidenceValidationError(
                f"Research run '{run_id}' not found in project '{project_id}'."
            ) from not_found
        except Exception as exc:
            raise ScriptRevisionEvidenceValidationError(
                f"Failed to resolve research run '{run_id}' for revision: {exc}"
            ) from exc

        if run.status != "completed":
            raise ScriptRevisionEvidenceValidationError(
                f"Research run '{run_id}' has status '{run.status}'. Only 'completed' runs can ground script revisions."
            )

        try:
            catalog = self.generator._build_evidence_catalog(project_id, run)
        except Exception as exc:
            raise ScriptRevisionEvidenceValidationError(
                f"Failed to build evidence catalog for run '{run_id}': {exc}"
            ) from exc

        return True, catalog

    def _find_invalid_evidence_ids(
        self, script: Script, catalog: ValidatedEvidenceCatalog
    ) -> Set[str]:
        """Find any evidence IDs cited in the script that do not exist in the run's catalog."""
        invalid_ids = set()
        for claim in script.claims:
            for eid in claim.evidence_ids:
                if eid not in catalog.evidence_by_id:
                    invalid_ids.add(eid)
        return invalid_ids

    def _collect_all_known_claim_ids(
        self, current_script: Script, dirs: Dict[str, Path]
    ) -> Set[str]:
        """Collect all claim IDs ever seen in the project across current, historical, and finalized versions."""
        known_ids = set()
        for c in current_script.claims:
            if c.claim_id:
                known_ids.add(c.claim_id)

        for search_dir in (dirs["versions_dir"], dirs["finalized_dir"]):
            if search_dir.exists():
                for v_file in search_dir.glob("v*.json"):
                    try:
                        v_data = json.loads(v_file.read_text(encoding="utf-8"))
                        for c in v_data.get("claims", []):
                            if isinstance(c, dict) and c.get("claim_id"):
                                known_ids.add(c["claim_id"])
                    except Exception:
                        pass

        if dirs["script_file"].exists():
            try:
                s_data = json.loads(dirs["script_file"].read_text(encoding="utf-8"))
                for c in s_data.get("claims", []):
                    if isinstance(c, dict) and c.get("claim_id"):
                        known_ids.add(c["claim_id"])
            except Exception:
                pass

        return known_ids

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

    def _reconcile_revision_claims(
        self,
        current_script: Script,
        raw_revised: Script,
        is_researched: bool,
        evidence_catalog: ValidatedEvidenceCatalog,
        all_known_claim_ids: Set[str],
    ) -> List[ScriptClaim]:
        """Reconcile claim identities conservatively and enforce authoritative source derivation."""
        # For unresearched scripts: reject any model-emitted evidence IDs
        if not is_researched:
            for c in raw_revised.claims:
                if c.evidence_ids:
                    raise ScriptRevisionEvidenceValidationError(
                        f"Unresearched script revision cannot cite evidence IDs: {c.evidence_ids}"
                    )

        used_prior_claim_ids: Set[str] = set()
        reconciled_claims: List[ScriptClaim] = []

        for rev_c in raw_revised.claims:
            matched_prior: Optional[ScriptClaim] = None

            # First pass: try matching same proposition AND same section_id
            for pc in current_script.claims:
                if pc.claim_id not in used_prior_claim_ids:
                    if pc.section_id == rev_c.section_id and _is_same_factual_proposition(rev_c.claim_text, pc.claim_text):
                        matched_prior = pc
                        break

            # Second pass: try matching same proposition across sections
            if matched_prior is None:
                for pc in current_script.claims:
                    if pc.claim_id not in used_prior_claim_ids:
                        if _is_same_factual_proposition(rev_c.claim_text, pc.claim_text):
                            matched_prior = pc
                            break

            if matched_prior is not None:
                # 1. Conservative match: same factual proposition -> preserve prior claim ID
                assigned_claim_id = matched_prior.claim_id
                used_prior_claim_ids.add(matched_prior.claim_id)

                if is_researched:
                    # If revised claim explicitly cites evidence, use valid ones
                    if rev_c.evidence_ids:
                        valid_eids = [
                            eid for eid in rev_c.evidence_ids
                            if eid in evidence_catalog.evidence_by_id
                        ]
                    else:
                        # Retain prior evidence links if still valid in catalog
                        valid_eids = [
                            eid for eid in matched_prior.evidence_ids
                            if eid in evidence_catalog.evidence_by_id
                        ]
                    # Derive source_ids strictly from validated evidence records
                    derived_sources = list(dict.fromkeys(
                        evidence_catalog.evidence_by_id[eid].source_id
                        for eid in valid_eids
                        if eid in evidence_catalog.evidence_by_id
                    ))
                else:
                    valid_eids = []
                    derived_sources = []
            else:
                # 2. New or materially changed claim -> allocate new unique ID; NEVER inherit prior evidence
                assigned_claim_id = self._allocate_claim_id(all_known_claim_ids)

                if is_researched:
                    valid_eids = [
                        eid for eid in rev_c.evidence_ids
                        if eid in evidence_catalog.evidence_by_id
                    ]
                    derived_sources = list(dict.fromkeys(
                        evidence_catalog.evidence_by_id[eid].source_id
                        for eid in valid_eids
                        if eid in evidence_catalog.evidence_by_id
                    ))
                else:
                    valid_eids = []
                    derived_sources = []

            link_status = "linked" if valid_eids else "unlinked"

            reconciled_claims.append(
                ScriptClaim(
                    claim_id=assigned_claim_id,
                    claim_text=rev_c.claim_text.strip(),
                    section_id=rev_c.section_id,
                    evidence_ids=valid_eids,
                    source_ids=derived_sources,
                    link_status=link_status,
                )
            )

        return reconciled_claims

    def _rebuild_bidirectional_mappings(
        self,
        sections: List[ScriptSection],
        claims: List[ScriptClaim],
    ) -> None:
        """Validate and rebuild bidirectional mapping between sections and claims."""
        section_ids = {sec.id for sec in sections}
        section_claims_map: Dict[str, List[str]] = {sec.id: [] for sec in sections}

        for claim in claims:
            target_sid = None
            if claim.section_id in section_ids:
                target_sid = claim.section_id
            else:
                # Substring/prefix fallback (e.g. sec_01 vs sec_01_hook)
                for sid in section_ids:
                    if claim.section_id in sid or sid in claim.section_id:
                        target_sid = sid
                        break

            if not target_sid:
                if sections:
                    target_sid = sections[0].id
                else:
                    raise ScriptValidationError(
                        f"Claim '{claim.claim_id}' references unknown section '{claim.section_id}'."
                    )

            claim.section_id = target_sid
            section_claims_map[target_sid].append(claim.claim_id)

        for sec in sections:
            sec.claim_ids = section_claims_map[sec.id]

    def revise_script(
        self,
        project_id: str,
        instruction: str,
    ) -> Script:
        """Revise the active script draft based on natural-language feedback with evidence and claim integrity."""
        clean_inst = (instruction or "").strip()
        if not clean_inst:
            raise ScriptValidationError("Revision instruction cannot be empty or whitespace-only.")

        dirs = self._get_project_dirs(project_id)
        if not dirs["current_file"].exists():
            raise ScriptNotFoundError(
                f"No active script draft found for project '{project_id}'. Call start_script first."
            )

        try:
            current_data = json.loads(dirs["current_file"].read_text(encoding="utf-8"))
            current_script = Script.model_validate(current_data)
        except Exception as exc:
            raise ScriptStorageError(f"Cannot read current draft for project '{project_id}': {exc}") from exc

        # 1. Resolve research catalog and run status
        is_researched, evidence_catalog = self._resolve_revision_catalog(project_id, current_script)

        # 2. Calculate next version
        next_version = self._allocate_next_version(dirs, current_version=current_script.version)

        # 3. Build context summary from recent conversation history
        conv_summary = ""
        if dirs["conversation_file"].exists():
            try:
                raw_events = json.loads(dirs["conversation_file"].read_text(encoding="utf-8"))
                recent = raw_events[-6:]
                lines = [f"- {e.get('event_type')}: {e.get('content')}" for e in recent if e.get("content")]
                conv_summary = "\n".join(lines)
            except Exception:
                pass

        # 4. Invoke LLM provider
        try:
            raw_revised = self.llm_provider.revise_script(
                current_script=current_script,
                instruction=clean_inst,
                conversation_context=conv_summary,
            )
        except (LLMResponseMalformedError, ScriptValidationError) as val_err:
            raise ScriptValidationError(f"Revised script is invalid: {val_err}") from val_err
        except Exception as exc:
            # Preserve existing draft uncorrupted
            raise ScriptError(f"Failed to revise script via LLM provider: {exc}") from exc

        # 5. Check invalid evidence IDs and perform at most one corrective retry if needed
        if is_researched:
            invalid_ids = self._find_invalid_evidence_ids(raw_revised, evidence_catalog)
            if invalid_ids:
                allowed_ids = sorted(list(evidence_catalog.evidence_by_id.keys()))
                allowed_str = ", ".join(allowed_ids) if allowed_ids else "None"
                corrective_inst = (
                    f"{clean_inst}\n\n"
                    f"[CORRECTIVE EVIDENCE REQUIREMENT]\n"
                    f"Your previous revision cited invalid evidence IDs: {sorted(list(invalid_ids))}.\n"
                    f"The ONLY valid evidence IDs for research run '{evidence_catalog.run_id}' are:\n"
                    f"[{allowed_str}]\n"
                    f"You MUST only cite evidence IDs from this allowed list, or leave evidence_ids empty. "
                    f"Do NOT invent any other evidence IDs."
                )
                try:
                    raw_revised = self.llm_provider.revise_script(
                        current_script=current_script,
                        instruction=corrective_inst,
                        conversation_context=conv_summary,
                    )
                except Exception as retry_exc:
                    raise ScriptRevisionEvidenceValidationError(
                        f"Corrective retry failed for revision: {retry_exc}"
                    ) from retry_exc

                still_invalid = self._find_invalid_evidence_ids(raw_revised, evidence_catalog)
                if still_invalid:
                    raise ScriptRevisionEvidenceValidationError(
                        f"Revised script cited invalid evidence IDs after corrective retry: {sorted(list(still_invalid))}. "
                        f"Allowed IDs: {allowed_ids}."
                    )

        # 6. Reconcile claims and preserve claim identity conservatively
        all_known_ids = self._collect_all_known_claim_ids(current_script, dirs)
        reconciled_claims = self._reconcile_revision_claims(
            current_script=current_script,
            raw_revised=raw_revised,
            is_researched=is_researched,
            evidence_catalog=evidence_catalog,
            all_known_claim_ids=all_known_ids,
        )

        # 7. Rebuild bidirectional section-claim mappings
        self._rebuild_bidirectional_mappings(raw_revised.sections, reconciled_claims)
        raw_revised.claims = reconciled_claims

        # 8. Reassemble metadata and metrics
        raw_revised.project_id = project_id
        raw_revised.version = next_version
        raw_revised.status = "draft"
        raw_revised.updated_at = utc_now()
        if current_script.research_run_id and not raw_revised.research_run_id:
            raw_revised.research_run_id = current_script.research_run_id
        if getattr(current_script, "outline", None) and not getattr(raw_revised, "outline", None):
            raw_revised.outline = current_script.outline
        if getattr(current_script, "evidence_brief_path", None) and not getattr(raw_revised, "evidence_brief_path", None):
            raw_revised.evidence_brief_path = current_script.evidence_brief_path

        populate_script_duration_metrics(raw_revised, words_per_minute=self.words_per_minute)

        # 9. Strict validation before disk persistence
        try:
            Script.model_validate(raw_revised)
            raw_revised.validate_content()
        except Exception as val_err:
            raise ScriptValidationError(f"Revised script is invalid: {val_err}") from val_err

        # 10. Atomic persistence: snapshot first, then current working copy
        version_file = dirs["versions_dir"] / f"v{next_version:03d}.json"
        if version_file.exists():
            raise ScriptStorageError(f"Snapshot version file '{version_file.name}' already exists.")

        _atomic_write_json(version_file, raw_revised.model_dump(mode="json"))
        _atomic_write_json(dirs["current_file"], raw_revised.model_dump(mode="json"))

        # 11. Reset workflow acceptance state for the new revision
        self._save_workflow_state(
            dirs["state_file"],
            {
                "accepted_version": None,
                "rejected_version": None,
                "rejection_reason": None,
            },
        )

        # 12. Record events
        self._append_conversation_event(
            dirs["conversation_file"],
            event_type="revision_instruction",
            content=clean_inst,
            script_version=current_script.version,
        )
        self._append_conversation_event(
            dirs["conversation_file"],
            event_type="revision_result",
            content=f"Created version {next_version}",
            script_version=next_version,
        )

        return raw_revised

    def accept_draft(self, project_id: str) -> Script:
        """Mark the active draft as accepted for finalization."""
        dirs = self._get_project_dirs(project_id)
        current = self.get_current_script(project_id)

        state = self._load_workflow_state(dirs["state_file"])
        state["accepted_version"] = current.version
        state["rejected_version"] = None
        state["rejection_reason"] = None
        self._save_workflow_state(dirs["state_file"], state)

        self._append_conversation_event(
            dirs["conversation_file"],
            event_type="draft_accepted",
            content=f"Draft version {current.version} accepted",
            script_version=current.version,
        )

        return current

    def reject_draft(self, project_id: str, reason: Optional[str] = None) -> Script:
        """Mark the active draft as rejected, preventing finalization until revised or re-accepted."""
        dirs = self._get_project_dirs(project_id)
        current = self.get_current_script(project_id)

        clean_reason = (reason or "").strip() or None
        state = self._load_workflow_state(dirs["state_file"])
        state["accepted_version"] = None
        state["rejected_version"] = current.version
        state["rejection_reason"] = clean_reason
        self._save_workflow_state(dirs["state_file"], state)

        self._append_conversation_event(
            dirs["conversation_file"],
            event_type="draft_rejected",
            content=clean_reason or "Draft rejected",
            script_version=current.version,
        )

        return current

    def finalize_script(self, project_id: str) -> Script:
        """Explicitly finalize the accepted draft into an immutable artifact."""
        dirs = self._get_project_dirs(project_id)
        self._reconcile_finalization(project_id, dirs)
        current = self.get_current_script(project_id)

        # 1. Check acceptance and rejection state
        state = self._load_workflow_state(dirs["state_file"])
        if state.get("rejected_version") == current.version:
            reason = state.get("rejection_reason")
            reason_msg = f" Reason: '{reason}'" if reason else ""
            raise ScriptFinalizationError(
                f"Draft v{current.version} is rejected and cannot be finalized.{reason_msg} "
                "Revise the script before finalization."
            )

        if state.get("accepted_version") != current.version:
            raise ScriptFinalizationError(
                f"Draft v{current.version} must be explicitly accepted before finalization. "
                "Call accept_draft first."
            )

        # 2. Strict validation as a finalized script
        try:
            current.validate_content()
        except Exception as val_err:
            raise ScriptValidationError(f"Cannot finalize script: {val_err}") from val_err

        # 3. Create immutable finalized copy
        finalized = current.model_copy(
            update={
                "status": "final",
                "updated_at": utc_now(),
            }
        )
        finalized_path = dirs["finalized_dir"] / f"v{finalized.version:03d}.json"

        # Check immutability: do not overwrite an existing finalized artifact
        if finalized_path.exists():
            raise ScriptFinalizationError(
                f"Finalized artifact '{finalized_path.name}' already exists and is immutable."
            )

        # 4. Commit Marker (Phase 1 Commit): Record finalization intent for crash recovery
        commit_payload = {
            "version": finalized.version,
            "finalized_script": finalized.model_dump(mode="json"),
            "timestamp": utc_now().isoformat(),
        }
        _atomic_write_json(dirs["commit_marker"], commit_payload)

        # 5. Complete Finalization Transaction (Phase 2 Commit)
        self._complete_finalization_transaction(project_id, dirs, finalized)

        return finalized

    def get_current_script(self, project_id: str) -> Script:
        """Load and validate the current working script draft."""
        dirs = self._get_project_dirs(project_id)
        self._reconcile_finalization(project_id, dirs)
        if not dirs["current_file"].exists():
            raise ScriptNotFoundError(f"No current script found for project '{project_id}'.")

        try:
            data = json.loads(dirs["current_file"].read_text(encoding="utf-8"))
            script = Script.model_validate(data)
            if script.project_id in (None, "", "proj_default"):
                script.project_id = project_id
            if script.project_id != project_id:
                raise ScriptValidationError(
                    f"Script identity mismatch: draft belongs to project '{script.project_id}', expected '{project_id}'."
                )
            if script.total_word_count is None or any(s.target_duration_seconds is None for s in script.sections):
                populate_script_duration_metrics(script, words_per_minute=self.words_per_minute)
            return script
        except ScriptValidationError:
            raise
        except json.JSONDecodeError as dec_err:
            raise ScriptStorageError(
                f"Failed to parse current script for project '{project_id}': {dec_err}"
            ) from dec_err
        except Exception as exc:
            raise ScriptStorageError(
                f"Current script for project '{project_id}' is malformed or invalid: {exc}"
            ) from exc

    def get_finalized_script(self, project_id: str, version: Optional[int] = None) -> Script:
        """Retrieve a specific finalized script version or the latest finalized script."""
        dirs = self._get_project_dirs(project_id)
        self._reconcile_finalization(project_id, dirs)

        if version is not None:
            target_path = dirs["finalized_dir"] / f"v{version:03d}.json"
            if not target_path.exists():
                raise ScriptNotFoundError(
                    f"Finalized script version {version} not found for project '{project_id}'."
                )
            try:
                data = json.loads(target_path.read_text(encoding="utf-8"))
                return Script.model_validate(data)
            except Exception as exc:
                raise ScriptStorageError(f"Corrupt finalized script artifact at '{target_path}': {exc}") from exc

        # If version not specified, resolve from script/script.json
        if dirs["script_file"].exists():
            try:
                data = json.loads(dirs["script_file"].read_text(encoding="utf-8"))
                return Script.model_validate(data)
            except Exception as exc:
                raise ScriptStorageError(f"Corrupt downstream script artifact at '{dirs['script_file']}': {exc}") from exc

        raise ScriptNotFoundError(f"No finalized script found for project '{project_id}'.")

    def list_finalized_versions(self, project_id: str) -> List[Script]:
        """Return all historical finalized script versions sorted by version number."""
        dirs = self._get_project_dirs(project_id)
        self._reconcile_finalization(project_id, dirs)
        files = sorted(dirs["finalized_dir"].glob("v*.json"))
        results: List[Script] = []
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                results.append(Script.model_validate(data))
            except Exception as exc:
                raise ScriptStorageError(f"Corrupt finalized version file '{f}': {exc}") from exc
        return results

    def list_script_versions(self, project_id: str) -> List[Script]:
        """Return all historical draft versions for the project sorted by version number."""
        dirs = self._get_project_dirs(project_id)
        version_files = sorted(dirs["versions_dir"].glob("v*.json"))
        results: List[Script] = []

        for vf in version_files:
            try:
                data = json.loads(vf.read_text(encoding="utf-8"))
                results.append(Script.model_validate(data))
            except Exception as exc:
                raise ScriptStorageError(f"Corrupt version file '{vf}': {exc}") from exc

        return results

    def get_conversation_history(self, project_id: str) -> List[ConversationEvent]:
        """Return the structured conversation event history for the project."""
        dirs = self._get_project_dirs(project_id)
        if not dirs["conversation_file"].exists():
            return []

        try:
            data = json.loads(dirs["conversation_file"].read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return []
            return [ConversationEvent.model_validate(item) for item in data]
        except Exception as exc:
            raise ScriptStorageError(
                f"Failed to read conversation history for project '{project_id}': {exc}"
            ) from exc
