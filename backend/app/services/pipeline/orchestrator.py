"""Pipeline orchestrator managing stage validation, prerequisites, and execution."""

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import uuid

from backend.app.config import Settings, get_settings
from backend.app.domain.contracts import ScenePlan, Script
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
        settings: Optional[Settings] = None,
        register_builtins: bool = False,
    ):
        self.settings = settings or get_settings()
        self.project_service = project_service or ProjectService(settings=self.settings)
        self.script_service = script_service or ScriptService(
            project_service=self.project_service,
            settings=self.settings,
        )
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
