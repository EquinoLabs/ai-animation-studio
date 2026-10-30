"""Pydantic data contracts between pipeline stages per TechSpec Section 4."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# 1. Script Contract
from backend.app.domain.script import (  # noqa: E402
    ClaimLinkStatus,
    Script,
    ScriptClaim,
    ScriptOutline,
    ScriptOutlineSection,
    ScriptSection,
    Section,
    count_words,
    estimate_duration_seconds,
    compute_duration_word_targets,
    populate_script_duration_metrics,
)


# 2. Asset Manifest Contract
AssetType = Literal["background", "prop", "character", "pose", "expression", "action"]


class Asset(BaseModel):
    id: str
    type: AssetType
    tags: List[str] = Field(default_factory=list)
    description: str = ""
    file_path: str


class AssetManifest(BaseModel):
    version: str = "1.0.0"
    assets: List[Asset] = Field(default_factory=list)


# 3. Scene Plan Contract
class CharacterRef(BaseModel):
    id: str
    pose: str
    expression: str


class SceneConfig(BaseModel):
    layout: str = "single"
    background: str
    characters: List[CharacterRef] = Field(default_factory=list)
    props: List[str] = Field(default_factory=list)
    action: str = "none"
    camera: str = "static"


class Beat(BaseModel):
    id: str
    narration: str
    scene: SceneConfig


class Scene(BaseModel):
    id: str
    beats: List[Beat] = Field(default_factory=list)


class ScenePlan(BaseModel):
    version: int = 1
    scenes: List[Scene] = Field(default_factory=list)


# 4. Coverage Report Contract
class MissingAssetSpec(BaseModel):
    id: str
    type: AssetType
    description: str
    needed_in_scenes: List[str] = Field(default_factory=list)


class SceneCoverage(BaseModel):
    scene_id: str
    status: Literal["reuse", "substitute", "missing"]
    reused_assets: List[str] = Field(default_factory=list)
    substitutions: Dict[str, str] = Field(default_factory=dict)
    missing_assets: List[str] = Field(default_factory=list)


class CoverageReport(BaseModel):
    timestamp: datetime = Field(default_factory=utc_now)
    scenes: List[SceneCoverage] = Field(default_factory=list)
    backlog: List[MissingAssetSpec] = Field(default_factory=list)


# 5. Run Manifest Contract
class StageStatus(BaseModel):
    stage_name: str
    status: Literal["pending", "running", "completed", "failed", "skipped"] = "pending"
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_s: Optional[float] = None
    artifacts: Dict[str, str] = Field(default_factory=dict)
    hash: Optional[str] = None
    error: Optional[str] = None


class AnimationRunManifest(BaseModel):
    run_id: str
    created_at: datetime = Field(default_factory=utc_now)
    script_version: int = 1
    stages: Dict[str, StageStatus] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# 6. Research Contracts (Phase 2)
from backend.app.domain.research import (  # noqa: E402
    EvidenceRecord,
    PlannedQuery,
    QueryRecord,
    ResearchBudgetConstraints,
    ResearchPlan,
    ResearchRun,
    SourceRecord,
    SUPPORTED_SCHEMA_VERSIONS,
)
