"""Pipeline orchestrator managing stage validation, prerequisites, and execution."""

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import uuid

from backend.app.config import Settings, get_settings
from backend.app.domain.contracts import ScenePlan, Script
from backend.app.domain.fact_check import ScriptFactCheckReport
from backend.app.domain.research import (
    ChallengeClaimResponse,
    QueryOutcomeView,
    ResearchBriefResponse,
    ResearchOverviewResponse,
    SafeEvidenceView,
    SafeSourceView,
)
from backend.app.domain.project import (
    ProjectMetadata,
    RunManifest,
    StageMetadata,
    STAGE_NAMES,
    utc_now,
)
from backend.app.domain.script import ConversationEvent, ScriptOutline
from backend.app.services.project.service import (
    Project,
    ProjectNotFoundError,
    ProjectService,
    ProjectValidationError,
)
from backend.app.services.research.orchestrator import ResearchOrchestrator
from backend.app.services.research.storage import (
    ResearchEvidenceNotFoundError,
    ResearchPlanNotFoundError,
    ResearchRunNotFoundError,
    ResearchSecurityError,
    ResearchSourceNotFoundError,
    ResearchStorageError,
    ResearchStorageService,
    ResearchValidationError,
)
from backend.app.services.scriptwriter.service import (
    ScriptError,
    ScriptFinalizationError,
    ScriptNotFoundError,
    ScriptService,
    ScriptStorageError,
    ScriptValidationError,
)


class PipelineError(Exception):
    """Base exception for pipeline execution errors."""


class PrerequisiteError(PipelineError):
    """Raised when a stage's prerequisites are not satisfied."""


class StageNotImplementedError(PipelineError):
    """Raised when an invoked stage has no concrete implementation registered."""


@dataclass
class StageResult:
    """Outcome of an individual stage handler execution."""

    success: bool
    artifacts: List[str] = field(default_factory=list)
    error: Optional[str] = None


StageHandler = Callable[[Project], StageResult]


def default_unimplemented_handler(stage_name: str) -> StageHandler:
    """Default handler that accurately reports unimplemented stages per Section 4."""

    def _handler(project: Project) -> StageResult:
        raise StageNotImplementedError(
            f"Stage '{stage_name}' is not yet implemented for project '{project.metadata.project_id}'."
        )

    return _handler


@dataclass(frozen=True)
class PipelineResult:
    """Summary of a single-stage or full-pipeline execution."""

    run_id: str
    target_stage: str
    success: bool
    executed_stages: List[str]
    errors: List[str]
    run_manifest_path: Path


class PipelineOrchestrator:
    """Orchestrates pipeline stages, validates prerequisites, and manages run manifests."""

    FULL_PIPELINE_ORDER = ["script", "plan-scenes", "script-to-voice", "video"]

    def __init__(
        self,
        project_service: Optional[ProjectService] = None,
        script_service: Optional[ScriptService] = None,
        research_orchestrator: Optional[ResearchOrchestrator] = None,
        settings: Optional[Settings] = None,
        register_builtins: bool = False,
    ):
        self.settings = settings or get_settings()
        self.project_service = project_service or ProjectService(settings=self.settings)
        self.research_storage = ResearchStorageService(
            projects_root=self.project_service.projects_root,
            settings=self.settings,
        )
        self.research_orchestrator = research_orchestrator
        self.script_service = script_service or ScriptService(
            project_service=self.project_service,
            research_storage=self.research_storage,
            research_orchestrator=self.research_orchestrator,
            settings=self.settings,
        )
        if hasattr(self.script_service, "research_orchestrator") and self.script_service.research_orchestrator is None:
            self.script_service.research_orchestrator = self.research_orchestrator
        self._stage_handlers: Dict[str, StageHandler] = {
            stage: default_unimplemented_handler(stage) for stage in STAGE_NAMES
        }
        if register_builtins:
            self.register_script_handler()

    def register_stage_handler(self, stage_name: str, handler: StageHandler) -> None:
        """Register or override a stage handler (used by production modules or test doubles)."""
        if stage_name not in STAGE_NAMES:
            raise PipelineError(f"Cannot register unknown stage '{stage_name}'. Valid: {STAGE_NAMES}")
        self._stage_handlers[stage_name] = handler

    def check_prerequisites(self, project: Project, stage: str) -> None:
        """Verify that preceding stage outputs exist and meet validation criteria."""
        if stage not in STAGE_NAMES:
            raise PipelineError(f"Unknown stage '{stage}'. Valid stages are: {STAGE_NAMES}")

        if stage == "script":
            # Script stage creates or updates the script; no preceding requirements
            return

        if stage in ("plan-scenes", "script-to-voice"):
            script_path = project.script_dir / "script.json"
            if not script_path.exists():
                raise PrerequisiteError(
                    f"Prerequisite not met for '{stage}': Project '{project.metadata.project_id}' "
                    f"is missing 'script/script.json'. A finalized script is required."
                )
            try:
                script_data = json.loads(script_path.read_text(encoding="utf-8"))
                script = Script.model_validate(script_data)
            except Exception as exc:
                raise PrerequisiteError(
                    f"Prerequisite not met for '{stage}': 'script/script.json' is invalid: {exc}"
                ) from exc

            if script.status != "final":
                raise PrerequisiteError(
                    f"Prerequisite not met for '{stage}': 'script/script.json' has status "
                    f"'{script.status}'. Stage requires status 'final'."
                )
            if not script.sections or not any(s.narration.strip() for s in script.sections):
                raise PrerequisiteError(
                    f"Prerequisite not met for '{stage}': 'script/script.json' must contain at least "
                    "one section with narrative text."
                )
            return

        if stage == "video":
            # 1. Scene planning artifact
            plan_path = project.scene_planning_dir / "scene_plan.json"
            if not plan_path.exists():
                raise PrerequisiteError(
                    f"Prerequisite not met for 'video': Project '{project.metadata.project_id}' "
                    f"is missing 'scene_planning/scene_plan.json'."
                )
            try:
                plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
                ScenePlan.model_validate(plan_data)
            except Exception as exc:
                raise PrerequisiteError(
                    f"Prerequisite not met for 'video': 'scene_planning/scene_plan.json' is invalid: {exc}"
                ) from exc

            # 2. Audio artifact check
            audio_files = list(project.script_to_audio_dir.glob("*.wav")) + list(
                project.script_to_audio_dir.glob("*.mp3")
            )
            audio_manifest = project.script_to_audio_dir / "manifest.json"
            if not audio_files and not audio_manifest.exists():
                raise PrerequisiteError(
                    f"Prerequisite not met for 'video': Project '{project.metadata.project_id}' "
                    f"has no audio artifacts in 'script_to_audio/'."
                )
            return

    def run_stage(self, project_id: str, stage: str) -> PipelineResult:
        """Execute a single pipeline stage with prerequisite enforcement and manifest logging."""
        project = self.project_service.get_project(project_id)
        if stage not in STAGE_NAMES:
            raise PipelineError(f"Invalid stage '{stage}'. Valid stages are: {STAGE_NAMES}")

        run_id = f"run_{uuid.uuid4().hex[:12]}"
        manifest = RunManifest(
            run_id=run_id,
            project_id=project_id,
            target_stage=stage,
            status="running",
            started_at=utc_now(),
        )

        manifest_path = project.runs_dir / f"{run_id}.json"
        manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

        # 1. Check prerequisites
        try:
            self.check_prerequisites(project, stage)
        except PrerequisiteError as exc:
            err_msg = str(exc)
            manifest.status = "failed"
            manifest.completed_at = utc_now()
            manifest.errors.append(err_msg)
            manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
            self.project_service.update_stage_status(project_id, stage, "failed", error=err_msg)
            return PipelineResult(
                run_id=run_id,
                target_stage=stage,
                success=False,
                executed_stages=[],
                errors=[err_msg],
                run_manifest_path=manifest_path,
            )

        # 2. Execute stage handler
        self.project_service.update_stage_status(project_id, stage, "in_progress")
        handler = self._stage_handlers[stage]
        try:
            result = handler(project)
            if result.success:
                manifest.status = "completed"
                manifest.executed_stages.append(stage)
                manifest.completed_at = utc_now()
                self.project_service.update_stage_status(
                    project_id, stage, "completed", artifacts=result.artifacts
                )
                manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
                return PipelineResult(
                    run_id=run_id,
                    target_stage=stage,
                    success=True,
                    executed_stages=[stage],
                    errors=[],
                    run_manifest_path=manifest_path,
                )
            else:
                err_msg = result.error or f"Stage '{stage}' execution failed."
                manifest.status = "failed"
                manifest.errors.append(err_msg)
                manifest.completed_at = utc_now()
                self.project_service.update_stage_status(project_id, stage, "failed", error=err_msg)
                manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
                return PipelineResult(
                    run_id=run_id,
                    target_stage=stage,
                    success=False,
                    executed_stages=[],
                    errors=[err_msg],
                    run_manifest_path=manifest_path,
                )
        except BaseException as exc:
            err_msg = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            manifest.status = "failed"
            manifest.errors.append(err_msg)
            manifest.completed_at = utc_now()
            self.project_service.update_stage_status(project_id, stage, "failed", error=err_msg)
            manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return PipelineResult(
                run_id=run_id,
                target_stage=stage,
                success=False,
                executed_stages=[],
                errors=[err_msg],
                run_manifest_path=manifest_path,
            )

    def run_full(self, project_id: str) -> PipelineResult:
        """Execute all pipeline stages in dependency order, stopping safely upon any failure."""
        project = self.project_service.get_project(project_id)
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        manifest = RunManifest(
            run_id=run_id,
            project_id=project_id,
            target_stage="full",
            status="running",
            started_at=utc_now(),
        )

        manifest_path = project.runs_dir / f"{run_id}.json"
        manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

        executed: List[str] = []
        errors: List[str] = []

        for stage in self.FULL_PIPELINE_ORDER:
            # Refresh project state between stages
            current_project = self.project_service.get_project(project_id)

            # Check prerequisites
            try:
                self.check_prerequisites(current_project, stage)
            except PrerequisiteError as exc:
                err_msg = f"Full pipeline halted at '{stage}': {exc}"
                errors.append(err_msg)
                manifest.errors.append(err_msg)
                manifest.status = "failed"
                manifest.completed_at = utc_now()
                manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
                self.project_service.update_stage_status(project_id, stage, "failed", error=err_msg)
                return PipelineResult(
                    run_id=run_id,
                    target_stage="full",
                    success=False,
                    executed_stages=executed,
                    errors=errors,
                    run_manifest_path=manifest_path,
                )

            # Execute stage
            self.project_service.update_stage_status(project_id, stage, "in_progress")
            handler = self._stage_handlers[stage]
            try:
                res = handler(current_project)
                if not res.success:
                    err_msg = res.error or f"Stage '{stage}' failed during full pipeline execution."
                    errors.append(err_msg)
                    manifest.errors.append(err_msg)
                    manifest.status = "failed"
                    manifest.completed_at = utc_now()
                    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
                    self.project_service.update_stage_status(project_id, stage, "failed", error=err_msg)
                    return PipelineResult(
                        run_id=run_id,
                        target_stage="full",
                        success=False,
                        executed_stages=executed,
                        errors=errors,
                        run_manifest_path=manifest_path,
                    )

                executed.append(stage)
                manifest.executed_stages.append(stage)
                self.project_service.update_stage_status(
                    project_id, stage, "completed", artifacts=res.artifacts
                )
            except BaseException as exc:
                err_msg = f"Exception in stage '{stage}': {exc if str(exc) else type(exc).__name__}"
                errors.append(err_msg)
                manifest.errors.append(err_msg)
                manifest.status = "failed"
                manifest.completed_at = utc_now()
                manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
                self.project_service.update_stage_status(project_id, stage, "failed", error=err_msg)
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                return PipelineResult(
                    run_id=run_id,
                    target_stage="full",
                    success=False,
                    executed_stages=executed,
                    errors=errors,
                    run_manifest_path=manifest_path,
                )

        manifest.status = "completed"
        manifest.completed_at = utc_now()
        manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

        return PipelineResult(
            run_id=run_id,
            target_stage="full",
            success=True,
            executed_stages=executed,
            errors=[],
            run_manifest_path=manifest_path,
        )

    def register_script_handler(self) -> None:
        """Register the production ScriptService handler for the 'script' pipeline stage."""
        self.register_stage_handler("script", self._handle_script_stage)

    def _handle_script_stage(self, project: Project) -> StageResult:
        """Production handler executing the script pipeline stage."""
        script_file = project.script_dir / "script.json"

        # 1. If valid finalized script already exists, stage is complete
        if script_file.exists():
            try:
                data = json.loads(script_file.read_text(encoding="utf-8"))
                script = Script.model_validate(data)
                if script.status == "final" and script.sections and any(s.narration.strip() for s in script.sections):
                    return StageResult(success=True, artifacts=[str(script_file)])
            except Exception:
                pass

        # 2. If an accepted draft exists, finalize it
        try:
            finalized = self.script_service.finalize_script(project.metadata.project_id)
            return StageResult(success=True, artifacts=[str(script_file)])
        except ScriptFinalizationError as fin_err:
            return StageResult(
                success=False,
                error=f"Script stage cannot complete: {fin_err}",
            )
        except ScriptNotFoundError:
            # 3. If no draft exists, generate an initial draft from project metadata
            topic = getattr(project.metadata, "description", None) or project.metadata.title
            try:
                draft = self.script_service.start_script(
                    project.metadata.project_id,
                    topic=topic,
                    title=project.metadata.title,
                )
                return StageResult(
                    success=False,
                    artifacts=[str(project.script_dir / "current.json")],
                    error=f"Initial draft v{draft.version} generated from project topic. "
                          "Draft must be reviewed and accepted before finalization.",
                )
            except Exception as exc:
                return StageResult(success=False, error=f"Failed to generate initial script: {exc}")
        except Exception as exc:
            return StageResult(success=False, error=f"Script stage execution failed: {exc}")

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
        """Delegate script initiation to the script service."""
        return self.script_service.start_script(
            project_id=project_id,
            topic=topic,
            target_duration_minutes=target_duration_minutes,
            title=title,
            research_run_id=research_run_id,
            allow_unresearched=allow_unresearched,
            outline=outline,
        )

    def create_outline(
        self,
        project_id: str,
        topic: str,
        target_duration_minutes: float = 5.0,
        title: Optional[str] = None,
        research_run_id: Optional[str] = None,
        allow_unresearched: bool = False,
    ) -> ScriptOutline:
        """Delegate standalone outline creation to the script service."""
        return self.script_service.create_outline(
            project_id=project_id,
            topic=topic,
            target_duration_minutes=target_duration_minutes,
            title=title,
            research_run_id=research_run_id,
            allow_unresearched=allow_unresearched,
        )

    def get_outline(self, project_id: str, outline_id: str) -> ScriptOutline:
        """Delegate standalone outline retrieval to the script service."""
        return self.script_service.get_outline(project_id=project_id, outline_id=outline_id)

    def revise_script(self, project_id: str, instruction: str) -> Script:
        """Delegate script revision to the script service."""
        return self.script_service.revise_script(
            project_id=project_id,
            instruction=instruction,
        )

    def accept_draft(self, project_id: str) -> Script:
        """Delegate draft acceptance to the script service."""
        return self.script_service.accept_draft(project_id=project_id)

    def reject_draft(self, project_id: str, reason: Optional[str] = None) -> Script:
        """Delegate draft rejection to the script service."""
        return self.script_service.reject_draft(project_id=project_id, reason=reason)

    def finalize_script(self, project_id: str) -> Script:
        """Delegate draft finalization to the script service."""
        return self.script_service.finalize_script(project_id=project_id)

    def get_script(self, project_id: str) -> Script:
        """Delegate loading current script to the script service."""
        return self.script_service.get_current_script(project_id=project_id)

    def list_script_versions(self, project_id: str) -> List[Script]:
        """Delegate listing script versions to the script service."""
        return self.script_service.list_script_versions(project_id=project_id)

    def get_conversation_history(self, project_id: str) -> List[ConversationEvent]:
        """Delegate retrieving conversation history to the script service."""
        return self.script_service.get_conversation_history(project_id=project_id)

    def get_finalized_script(self, project_id: str, version: Optional[int] = None) -> Script:
        """Delegate retrieving latest or versioned finalized script to the script service."""
        return self.script_service.get_finalized_script(project_id=project_id, version=version)

    def list_finalized_versions(self, project_id: str) -> List[Script]:
        """Delegate listing finalized versions to the script service."""
        return self.script_service.list_finalized_versions(project_id=project_id)

    def get_workflow_state(self, project_id: str) -> Dict[str, Any]:
        """Delegate retrieving script workflow state to the script service."""
        dirs = self.script_service._get_project_dirs(project_id)
        return self.script_service._load_workflow_state(dirs["state_file"])

    def get_research_overview(self, project_id: str) -> ResearchOverviewResponse:
        """Assemble structured, sanitized research overview for studio inspection."""
        self.project_service.get_project(project_id)
        runs = self.research_storage.list_runs(project_id)

        if not runs:
            return ResearchOverviewResponse(
                project_id=project_id,
                has_research=False,
                active_run_id=None,
                status=None,
                termination_reason=None,
                budget_summary={},
                queries=[],
                sources=[],
                selected_evidence=[],
                used_evidence_ids=[],
                unused_evidence_ids=[],
                conflicts=[],
                unresolved_questions=[],
            )

        current_script: Optional[Script] = None
        try:
            current_script = self.get_script(project_id)
        except Exception:
            current_script = None

        if current_script and current_script.research_run_id:
            target_run_id = current_script.research_run_id
        else:
            target_run_id = runs[-1].run_id

        active_run = self.research_storage.get_run(project_id, target_run_id)

        # 1. Resolve research plan to discover unexecuted planned queries
        plan = None
        try:
            plan = self.research_storage.get_plan(project_id, active_run.plan_id)
        except Exception:
            plan = None

        queries: List[QueryOutcomeView] = []
        executed_qids: Set[str] = set()
        for q in active_run.query_records:
            executed_qids.add(q.query_id)
            status_clean = "completed" if q.status == "succeeded" else q.status
            skip_reason = q.error_message if q.status == "skipped" else None
            queries.append(
                QueryOutcomeView(
                    query_id=q.query_id,
                    query_text=q.query_text,
                    intended_question=q.intended_question,
                    status=status_clean,
                    result_count=q.result_count,
                    error_message=q.error_message if q.status != "skipped" else None,
                    skip_reason=skip_reason,
                    returned_source_ids=q.returned_source_ids,
                )
            )

        if plan:
            for pq in plan.planned_queries:
                if pq.query_id not in executed_qids:
                    queries.append(
                        QueryOutcomeView(
                            query_id=pq.query_id,
                            query_text=pq.query_text,
                            intended_question=pq.target_question or pq.purpose,
                            status="planned",
                            result_count=0,
                            error_message=None,
                            skip_reason=None,
                            returned_source_ids=[],
                        )
                    )

        # 2. Sanitized Sources View
        sources_raw = self.research_storage.list_sources(project_id)
        sources = [
            SafeSourceView(
                source_id=s.source_id,
                url=s.url,
                title=s.title,
                publisher=s.publisher,
                retrieval_status=s.retrieval_status,
                retrieved_at=s.retrieved_at,
                publication_date=s.publication_date,
                char_count=s.char_count,
            )
            for s in sources_raw
            if s.url.startswith("http://") or s.url.startswith("https://")
        ]

        # 3. Evidence Distinctions: Retrieved, Candidate, AI-Selected, Active Script Used vs Unused
        evidence_raw = self.research_storage.list_evidence(project_id, run_id=active_run.run_id)
        section_claim_ids = {cid for s in current_script.sections for cid in getattr(s, "claim_ids", [])} if current_script else set()
        active_claims = [
            c for c in (current_script.claims if current_script else [])
            if not section_claim_ids or c.claim_id in section_claim_ids
        ]
        used_eids = set(eid for c in active_claims for eid in c.evidence_ids)

        # AI-Selected evidence identification: from outline section citations or recorded selection operation
        # Note: Candidate status indicates availability for consideration, not automatic selection.
        outline = getattr(current_script, "outline", None) if current_script else None
        if not outline:
            try:
                dirs = self.script_service._get_project_dirs(project_id)
                outline_files = sorted(dirs["outlines_dir"].glob("*.json"))
                if outline_files:
                    outline_data = json.loads(outline_files[-1].read_text(encoding="utf-8"))
                    outline = ScriptOutline.model_validate(outline_data)
            except Exception:
                outline = None
        selected_by_outline_ids = set(eid for s in outline.sections for eid in s.evidence_ids) if outline else set()
        selected_ids_from_run = set()
        if isinstance(active_run.artifacts.get("selected_evidence_ids"), list):
            selected_ids_from_run = set(active_run.artifacts["selected_evidence_ids"])
        recorded_selected_ids = selected_by_outline_ids | selected_ids_from_run

        selected_evidence = []
        for e in evidence_raw:
            is_used = e.evidence_id in used_eids
            used_by = [c.claim_id for c in active_claims if e.evidence_id in c.evidence_ids]
            is_selected = e.evidence_id in recorded_selected_ids
            selection_rationale = getattr(e, "selection_rationale", None)
            selected_evidence.append(
                SafeEvidenceView(
                    evidence_id=e.evidence_id,
                    source_id=e.source_id,
                    query_id=e.query_id,
                    run_id=e.run_id,
                    excerpt=e.excerpt,
                    locator=e.locator,
                    target_claim=e.target_claim,
                    status=e.status,
                    is_selected=is_selected,
                    selection_rationale=selection_rationale,
                    is_used_in_current_script=is_used,
                    used_by_claims=used_by,
                )
            )

        retrieved_evidence_ids = sorted([e.evidence_id for e in evidence_raw])
        selected_evidence_ids = sorted([e.evidence_id for e in evidence_raw if e.evidence_id in recorded_selected_ids])
        used_evidence_ids = sorted(list(used_eids))
        unused_evidence_ids = sorted([e.evidence_id for e in evidence_raw if e.evidence_id not in used_eids])

        unresolved = []
        for q in active_run.query_records:
            if q.status == "skipped":
                unresolved.append(f"Skipped query: '{q.query_text}' ({q.error_message or 'Budget ceiling'})")
            elif q.status == "failed":
                unresolved.append(f"Failed query: '{q.query_text}' ({q.error_message or 'Provider error'})")

        conflicts = []
        try:
            report = self.script_service.get_current_fact_check_report(project_id)
            if report:
                for a in report.assessments:
                    if a.status == "conflicting":
                        conflicts.append(f"Claim '{a.claim_id}' has conflicting evidence: {a.explanation}")
                    elif a.status == "contradicted":
                        conflicts.append(f"Claim '{a.claim_id}' contradicted by evidence: {a.explanation}")
        except Exception:
            pass

        budget_summary = {
            "search_calls_used": active_run.search_calls_used,
            "extract_calls_used": active_run.extract_calls_used,
            "total_chars_extracted": active_run.total_chars_extracted,
            "max_search_calls": active_run.budget_limits.max_search_calls,
            "max_extract_calls": active_run.budget_limits.max_extract_calls,
        }

        return ResearchOverviewResponse(
            project_id=project_id,
            has_research=True,
            active_run_id=active_run.run_id,
            status=active_run.status,
            termination_reason=active_run.termination_reason,
            budget_summary=budget_summary,
            queries=queries,
            sources=sources,
            selected_evidence=selected_evidence,
            retrieved_evidence_ids=retrieved_evidence_ids,
            selected_evidence_ids=selected_evidence_ids,
            used_evidence_ids=used_evidence_ids,
            unused_evidence_ids=unused_evidence_ids,
            conflicts=conflicts,
            unresolved_questions=unresolved,
        )

    def get_evidence_brief(self, project_id: str, run_id: Optional[str] = None) -> Optional[str]:
        """Retrieve evidence brief markdown content for a project or specific run."""
        self.project_service.get_project(project_id)
        if run_id:
            return self.research_storage.get_run_evidence_brief(project_id, run_id)
        try:
            curr = self.get_script(project_id)
            if curr and curr.research_run_id:
                content = self.research_storage.get_run_evidence_brief(project_id, curr.research_run_id)
                if content:
                    return content
        except Exception:
            pass
        return self.research_storage.get_evidence_brief(project_id)

    def get_fact_check_report(self, project_id: str, version: Optional[int] = None) -> Optional[ScriptFactCheckReport]:
        """Retrieve active or versioned fact-check report."""
        if version is not None:
            return self.script_service.get_fact_check_report(project_id, version)
        return self.script_service.get_current_fact_check_report(project_id)

    def challenge_claim(
        self,
        project_id: str,
        claim_id: str,
        instruction: Optional[str] = None,
        action: Optional[str] = "investigate",
    ) -> ChallengeClaimResponse:
        """Execute a conversational fact challenge via ScriptService and return structured response."""
        raw_result = self.script_service.challenge_claim(
            project_id=project_id,
            claim_id=claim_id,
            instruction=instruction,
            action=action,
        )
        return ChallengeClaimResponse(
            script=raw_result["script"].model_dump() if hasattr(raw_result["script"], "model_dump") else raw_result["script"],
            workflow_state=raw_result["workflow_state"],
            fact_check_report=raw_result["fact_check_report"].model_dump() if hasattr(raw_result.get("fact_check_report"), "model_dump") else raw_result.get("fact_check_report"),
            challenge_summary=raw_result.get("challenge_summary", {}),
        )
