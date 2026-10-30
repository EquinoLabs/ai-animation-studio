"""REST API endpoints for AI Animation Studio local project and script management."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.app.config import Settings, get_settings
from backend.app.domain.contracts import Script
from backend.app.domain.fact_check import ScriptFactCheckReport
from backend.app.domain.project import ProjectMetadata
from backend.app.domain.research import (
    ChallengeClaimRequest,
    ChallengeClaimResponse,
    ResearchBriefResponse,
    ResearchOverviewResponse,
)
from backend.app.domain.script import ConversationEvent
from backend.app.services.pipeline.orchestrator import (
    PipelineError,
    PipelineOrchestrator,
    ResearchEvidenceNotFoundError,
    ResearchPlanNotFoundError,
    ResearchRunNotFoundError,
    ResearchSecurityError,
    ResearchSourceNotFoundError,
    ResearchStorageError,
    ResearchValidationError,
    ScriptError,
    ScriptFinalizationError,
    ScriptNotFoundError,
    ScriptStorageError,
    ScriptValidationError,
)
from backend.app.services.project.service import (
    ProjectError,
    ProjectNotFoundError,
    ProjectSecurityError,
    ProjectService,
    ProjectValidationError,
)

router = APIRouter(prefix="/api/v1")


# Dependency injection helpers
def get_project_service() -> ProjectService:
    settings = get_settings()
    return ProjectService(projects_root=settings.PROJECTS_DIR)


def get_orchestrator(
    project_service: ProjectService = Depends(get_project_service),
) -> PipelineOrchestrator:
    settings = get_settings()
    return PipelineOrchestrator(
        project_service=project_service,
        settings=settings,
    )


# ------------------------------------------------------------------------------
# Request / Response Models
# ------------------------------------------------------------------------------

class CreateProjectRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200, description="Project title")
    project_id: Optional[str] = Field(default=None, description="Optional custom project ID")


class StartScriptRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=1000, description="Subject topic for script generation")
    target_duration_minutes: float = Field(default=5.0, gt=0.0, le=60.0, description="Target duration in minutes")
    title: Optional[str] = Field(default=None, description="Optional preferred script title")
    research_run_id: Optional[str] = Field(default=None, description="Explicit research run ID to ground script")
    allow_unresearched: bool = Field(default=False, description="Explicit authorization to generate without research")


class ReviseScriptRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000, description="Conversational revision instruction")


class RejectScriptRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=1000, description="Optional rejection reason")


class ScriptStateResponse(BaseModel):
    script: Optional[Script] = None
    workflow_state: Dict[str, Any] = Field(default_factory=dict)


def _handle_service_errors(exc: Exception) -> None:
    """Map domain exceptions to standard HTTP status codes."""
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, (ProjectNotFoundError, ScriptNotFoundError, ResearchRunNotFoundError, ResearchSourceNotFoundError, ResearchEvidenceNotFoundError)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, (ProjectSecurityError, ResearchSecurityError)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if isinstance(exc, (ScriptValidationError, ProjectValidationError, ResearchValidationError)):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    if isinstance(exc, ScriptFinalizationError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if isinstance(exc, (ScriptStorageError, ResearchStorageError)):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    if "unavailable" in str(exc).lower() or "connection" in str(exc).lower():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, (ScriptError, ProjectError, PipelineError)):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Unexpected error: {exc}")


# ------------------------------------------------------------------------------
# Health Check Endpoint
# ------------------------------------------------------------------------------

@router.get("/health")
def health_check() -> Dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "service": "ai-animation-studio"}


# ------------------------------------------------------------------------------
# Project Management Endpoints
# ------------------------------------------------------------------------------

@router.get("/projects", response_model=List[ProjectMetadata])
def list_projects(
    project_service: ProjectService = Depends(get_project_service),
) -> List[ProjectMetadata]:
    """List all existing local animation projects."""
    try:
        return project_service.list_projects()
    except Exception as exc:
        _handle_service_errors(exc)
        return []


@router.post("/projects", response_model=ProjectMetadata, status_code=status.HTTP_201_CREATED)
def create_project(
    req: CreateProjectRequest,
    project_service: ProjectService = Depends(get_project_service),
) -> ProjectMetadata:
    """Create a new local project with directory structure and metadata."""
    try:
        project = project_service.create_project(
            title=req.title,
            project_id=req.project_id,
        )
        return project.metadata
    except Exception as exc:
        _handle_service_errors(exc)
        raise


@router.get("/projects/{project_id}", response_model=ProjectMetadata)
def get_project(
    project_id: str,
    project_service: ProjectService = Depends(get_project_service),
) -> ProjectMetadata:
    """Get metadata for a specific project."""
    try:
        project = project_service.get_project(project_id)
        return project.metadata
    except Exception as exc:
        _handle_service_errors(exc)
        raise


# ------------------------------------------------------------------------------
# Script Builder Endpoints
# ------------------------------------------------------------------------------

@router.get("/projects/{project_id}/script", response_model=ScriptStateResponse)
def get_current_script(
    project_id: str,
    orchestrator: PipelineOrchestrator = Depends(get_orchestrator),
) -> ScriptStateResponse:
    """Get the active script draft and current workflow state for a project."""
    try:
        workflow_state = orchestrator.get_workflow_state(project_id)
        try:
            current = orchestrator.get_script(project_id)
            return ScriptStateResponse(script=current, workflow_state=workflow_state)
        except ScriptNotFoundError:
            return ScriptStateResponse(script=None, workflow_state=workflow_state)
    except Exception as exc:
        _handle_service_errors(exc)
        raise


@router.post("/projects/{project_id}/script/start", response_model=ScriptStateResponse, status_code=status.HTTP_201_CREATED)
def start_script(
    project_id: str,
    req: StartScriptRequest,
    orchestrator: PipelineOrchestrator = Depends(get_orchestrator),
) -> ScriptStateResponse:
    """Generate an initial structured script draft from a topic."""
    try:
        script = orchestrator.start_script(
            project_id=project_id,
            topic=req.topic,
            target_duration_minutes=req.target_duration_minutes,
            title=req.title,
            research_run_id=req.research_run_id,
            allow_unresearched=req.allow_unresearched,
        )
        workflow_state = orchestrator.get_workflow_state(project_id)
        return ScriptStateResponse(script=script, workflow_state=workflow_state)
    except Exception as exc:
        _handle_service_errors(exc)
        raise


@router.post("/projects/{project_id}/script/revise", response_model=ScriptStateResponse)
def revise_script(
    project_id: str,
    req: ReviseScriptRequest,
    orchestrator: PipelineOrchestrator = Depends(get_orchestrator),
) -> ScriptStateResponse:
    """Revise the active script draft based on conversational user instructions."""
    try:
        script = orchestrator.revise_script(
            project_id=project_id,
            instruction=req.instruction,
        )
        workflow_state = orchestrator.get_workflow_state(project_id)
        return ScriptStateResponse(script=script, workflow_state=workflow_state)
    except Exception as exc:
        _handle_service_errors(exc)
        raise


@router.post("/projects/{project_id}/script/accept", response_model=ScriptStateResponse)
def accept_draft(
    project_id: str,
    orchestrator: PipelineOrchestrator = Depends(get_orchestrator),
) -> ScriptStateResponse:
    """Accept the active script draft for finalization."""
    try:
        script = orchestrator.accept_draft(project_id)
        workflow_state = orchestrator.get_workflow_state(project_id)
        return ScriptStateResponse(script=script, workflow_state=workflow_state)
    except Exception as exc:
        _handle_service_errors(exc)
        raise


@router.post("/projects/{project_id}/script/reject", response_model=ScriptStateResponse)
def reject_draft(
    project_id: str,
    req: RejectScriptRequest,
    orchestrator: PipelineOrchestrator = Depends(get_orchestrator),
) -> ScriptStateResponse:
    """Reject the active script draft."""
    try:
        script = orchestrator.reject_draft(project_id, reason=req.reason)
        workflow_state = orchestrator.get_workflow_state(project_id)
        return ScriptStateResponse(script=script, workflow_state=workflow_state)
    except Exception as exc:
        _handle_service_errors(exc)
        raise


@router.post("/projects/{project_id}/script/finalize", response_model=ScriptStateResponse)
def finalize_script(
    project_id: str,
    orchestrator: PipelineOrchestrator = Depends(get_orchestrator),
) -> ScriptStateResponse:
    """Explicitly finalize the accepted draft into an immutable artifact."""
    try:
        script = orchestrator.finalize_script(project_id)
        workflow_state = orchestrator.get_workflow_state(project_id)
        return ScriptStateResponse(script=script, workflow_state=workflow_state)
    except Exception as exc:
        _handle_service_errors(exc)
        raise


@router.get("/projects/{project_id}/script/history", response_model=List[ConversationEvent])
def get_conversation_history(
    project_id: str,
    orchestrator: PipelineOrchestrator = Depends(get_orchestrator),
) -> List[ConversationEvent]:
    """Retrieve chronological conversation events for a project."""
    try:
        return orchestrator.get_conversation_history(project_id)
    except Exception as exc:
        _handle_service_errors(exc)
        raise


@router.get("/projects/{project_id}/script/versions", response_model=List[Script])
def list_script_versions(
    project_id: str,
    orchestrator: PipelineOrchestrator = Depends(get_orchestrator),
) -> List[Script]:
    """List all historical draft versions for a project."""
    try:
        return orchestrator.list_script_versions(project_id)
    except Exception as exc:
        _handle_service_errors(exc)
        raise


@router.get("/projects/{project_id}/script/finalized", response_model=List[Script])
def list_finalized_versions(
    project_id: str,
    orchestrator: PipelineOrchestrator = Depends(get_orchestrator),
) -> List[Script]:
    """List all immutable finalized script versions for a project."""
    try:
        return orchestrator.list_finalized_versions(project_id)
    except Exception as exc:
        _handle_service_errors(exc)
        raise


# ------------------------------------------------------------------------------
# Research Studio Endpoints (Phase 6)
# ------------------------------------------------------------------------------

@router.get("/projects/{project_id}/research", response_model=ResearchOverviewResponse)
def get_research_overview(
    project_id: str,
    orchestrator: PipelineOrchestrator = Depends(get_orchestrator),
) -> ResearchOverviewResponse:
    """Retrieve structured research overview including query outcomes, sources, and used/unused evidence."""
    try:
        return orchestrator.get_research_overview(project_id)
    except Exception as exc:
        _handle_service_errors(exc)
        raise


@router.get("/projects/{project_id}/research/brief", response_model=ResearchBriefResponse)
def get_evidence_brief(
    project_id: str,
    run_id: Optional[str] = None,
    orchestrator: PipelineOrchestrator = Depends(get_orchestrator),
) -> ResearchBriefResponse:
    """Retrieve persisted evidence brief markdown content for studio display."""
    try:
        brief_text = orchestrator.get_evidence_brief(project_id, run_id=run_id)
        if not brief_text:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No evidence brief found for project '{project_id}'.",
            )
        return ResearchBriefResponse(
            project_id=project_id,
            run_id=run_id,
            brief_markdown=brief_text,
        )
    except Exception as exc:
        _handle_service_errors(exc)
        raise


# ------------------------------------------------------------------------------
# Fact-Checking & Conversational Challenge Endpoints (Phase 6)
# ------------------------------------------------------------------------------

@router.get("/projects/{project_id}/script/fact-check", response_model=ScriptFactCheckReport)
def get_fact_check_report(
    project_id: str,
    version: Optional[int] = None,
    orchestrator: PipelineOrchestrator = Depends(get_orchestrator),
) -> ScriptFactCheckReport:
    """Retrieve the active or version-specific fact-check report."""
    try:
        report = orchestrator.get_fact_check_report(project_id, version=version)
        if report is None:
            raise ScriptNotFoundError(f"No fact-check report found for project '{project_id}'.")
        return report
    except Exception as exc:
        _handle_service_errors(exc)
        raise


@router.post("/projects/{project_id}/script/challenge", response_model=ChallengeClaimResponse)
def challenge_claim(
    project_id: str,
    req: ChallengeClaimRequest,
    orchestrator: PipelineOrchestrator = Depends(get_orchestrator),
) -> ChallengeClaimResponse:
    """Execute a conversational fact challenge against a specific factual claim."""
    try:
        return orchestrator.challenge_claim(
            project_id=project_id,
            claim_id=req.claim_id,
            instruction=req.instruction,
            action=req.action,
        )
    except Exception as exc:
        _handle_service_errors(exc)
        raise

