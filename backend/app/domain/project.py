"""Domain models for local project storage and pipeline run manifests."""

from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


STAGE_NAMES: List[str] = ["script", "plan-scenes", "script-to-voice", "video"]
StageStatus = Literal["not_started", "in_progress", "completed", "failed"]
RunStatus = Literal["pending", "running", "completed", "failed"]


class StageMetadata(BaseModel):
    """Metadata describing the state of a single pipeline stage."""

    status: StageStatus = "not_started"
    updated_at: Optional[datetime] = None
    error: Optional[str] = None
    artifacts: List[str] = Field(default_factory=list)


def default_stages_dict() -> Dict[str, StageMetadata]:
    """Create default initial stage tracking dictionary."""
    return {stage: StageMetadata(status="not_started") for stage in STAGE_NAMES}


class ProjectMetadata(BaseModel):
    """Metadata contract stored in projects/proj_<id>/metadata.json."""

    project_id: str
    title: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    schema_version: str = "1.0.0"
    stages: Dict[str, StageMetadata] = Field(default_factory=default_stages_dict)


class RunManifest(BaseModel):
    """Execution record stored in projects/proj_<id>/runs/<run_id>.json."""

    run_id: str
    project_id: str
    target_stage: str
    status: RunStatus = "pending"
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: Optional[datetime] = None
    executed_stages: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
