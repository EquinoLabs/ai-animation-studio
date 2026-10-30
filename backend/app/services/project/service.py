"""Filesystem-based local project management service."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Dict, List, Optional
import uuid

from backend.app.config import get_settings
from backend.app.domain.project import (
    ProjectMetadata,
    StageMetadata,
    StageStatus,
    STAGE_NAMES,
    default_stages_dict,
    utc_now,
)


class ProjectError(Exception):
    """Base exception for project management operations."""


class ProjectNotFoundError(ProjectError):
    """Raised when a requested project does not exist."""


class ProjectAlreadyExistsError(ProjectError):
    """Raised when attempting to create a project with an existing ID."""


class ProjectValidationError(ProjectError):
    """Raised when project metadata or directory layout is invalid or corrupt."""


class ProjectSecurityError(ProjectError):
    """Raised when a project ID attempts path traversal outside the project root."""


REQUIRED_STAGE_DIRS = [
    "script",
    "scene_planning",
    "script_to_audio",
    "video",
    "runs",
]

PROJECT_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]+$")


@dataclass(frozen=True)
class Project:
    """Represents an active local project with directory bindings."""

    metadata: ProjectMetadata
    root_dir: Path
    script_dir: Path
    scene_planning_dir: Path
    script_to_audio_dir: Path
    video_dir: Path
    runs_dir: Path

    @property
    def research_dir(self) -> Path:
        """Directory for research plans, runs, sources, and evidence."""
        return self.root_dir / "research"


class ProjectService:
    """Manages project creation, retrieval, listing, and atomic metadata persistence."""

    def __init__(self, projects_root: Optional[Path] = None):
        settings = get_settings()
        self.projects_root = Path(projects_root or settings.PROJECTS_DIR).resolve()
        self.projects_root.mkdir(parents=True, exist_ok=True)

    def _resolve_project_dir(self, project_id: str) -> Path:
        """Resolve and validate that the project directory resides safely inside projects_root."""
        if not project_id or not PROJECT_ID_REGEX.match(project_id):
            raise ProjectSecurityError(
                f"Invalid project ID '{project_id}'. IDs must only contain alphanumeric characters, underscores, or dashes."
            )

        project_dir = (self.projects_root / project_id).resolve()
        try:
            is_relative = project_dir.is_relative_to(self.projects_root)
        except AttributeError:
            # Fallback for Python versions before 3.9 (safety check)
            is_relative = str(project_dir).startswith(str(self.projects_root))

        if not is_relative or project_dir.parent != self.projects_root:
            raise ProjectSecurityError(
                f"Path traversal detected: project ID '{project_id}' attempts to escape project root."
            )

        return project_dir

    def _write_metadata_atomic(self, project_dir: Path, metadata: ProjectMetadata) -> None:
        """Atomically persist metadata.json using a temporary file replacement."""
        metadata_path = project_dir / "metadata.json"
        tmp_fd, tmp_path_str = tempfile.mkstemp(
            prefix="metadata_",
            suffix=".tmp",
            dir=str(project_dir),
        )
        try:
            with open(tmp_fd, "w", encoding="utf-8") as f:
                f.write(metadata.model_dump_json(indent=2))
            # Atomic replacement
            os.replace(tmp_path_str, str(metadata_path))
        except Exception:
            if os.path.exists(tmp_path_str):
                os.remove(tmp_path_str)
            raise

    def create_project(self, title: str, project_id: Optional[str] = None) -> Project:
        """Create a new project directory structure and initialize metadata.json."""
        clean_title = (title or "").strip()
        if not clean_title:
            raise ProjectValidationError("Project title must be a non-empty string.")

        if project_id is not None:
            clean_id = project_id.strip()
        else:
            clean_id = f"proj_{uuid.uuid4().hex[:12]}"

        project_dir = self._resolve_project_dir(clean_id)
        if project_dir.exists():
            raise ProjectAlreadyExistsError(f"Project with ID '{clean_id}' already exists at {project_dir}")

        project_dir.mkdir(parents=True, exist_ok=False)

        # Create expected stage directories
        for stage_dir_name in REQUIRED_STAGE_DIRS:
            (project_dir / stage_dir_name).mkdir(parents=True, exist_ok=True)

        metadata = ProjectMetadata(
            project_id=clean_id,
            title=clean_title,
            created_at=utc_now(),
            updated_at=utc_now(),
            schema_version="1.0.0",
            stages=default_stages_dict(),
        )

        self._write_metadata_atomic(project_dir, metadata)

        return Project(
            metadata=metadata,
            root_dir=project_dir,
            script_dir=project_dir / "script",
            scene_planning_dir=project_dir / "scene_planning",
            script_to_audio_dir=project_dir / "script_to_audio",
            video_dir=project_dir / "video",
            runs_dir=project_dir / "runs",
        )

    def get_project(self, project_id: str) -> Project:
        """Load an existing project and validate metadata and directory structure."""
        project_dir = self._resolve_project_dir(project_id)
        if not project_dir.exists() or not project_dir.is_dir():
            raise ProjectNotFoundError(f"Project '{project_id}' does not exist.")

        metadata_path = project_dir / "metadata.json"
        if not metadata_path.exists() or not metadata_path.is_file():
            raise ProjectValidationError(f"Project '{project_id}' is corrupted: missing metadata.json.")

        try:
            content = metadata_path.read_text(encoding="utf-8")
            metadata = ProjectMetadata.model_validate_json(content)
        except Exception as exc:
            raise ProjectValidationError(f"Project '{project_id}' has corrupted metadata.json: {exc}") from exc

        # Verify directory structure
        missing_dirs = [d for d in REQUIRED_STAGE_DIRS if not (project_dir / d).is_dir()]
        if missing_dirs:
            raise ProjectValidationError(
                f"Project '{project_id}' has corrupted directory layout. Missing directories: {missing_dirs}"
            )

        return Project(
            metadata=metadata,
            root_dir=project_dir,
            script_dir=project_dir / "script",
            scene_planning_dir=project_dir / "scene_planning",
            script_to_audio_dir=project_dir / "script_to_audio",
            video_dir=project_dir / "video",
            runs_dir=project_dir / "runs",
        )

    def list_projects(self) -> List[ProjectMetadata]:
        """List all valid projects sorted by updated_at descending."""
        if not self.projects_root.exists():
            return []

        projects: List[ProjectMetadata] = []
        for entry in self.projects_root.iterdir():
            if entry.is_dir():
                metadata_file = entry / "metadata.json"
                if metadata_file.exists() and metadata_file.is_file():
                    try:
                        content = metadata_file.read_text(encoding="utf-8")
                        meta = ProjectMetadata.model_validate_json(content)
                        projects.append(meta)
                    except Exception:
                        # Skip or ignore malformed directories in list view
                        continue

        projects.sort(key=lambda p: p.updated_at, reverse=True)
        return projects

    def update_metadata(self, project_id: str, metadata: ProjectMetadata) -> ProjectMetadata:
        """Safely update and persist project metadata with updated timestamp."""
        project = self.get_project(project_id)
        updated = metadata.model_copy(update={"updated_at": utc_now()})
        self._write_metadata_atomic(project.root_dir, updated)
        return updated

    def update_stage_status(
        self,
        project_id: str,
        stage_name: str,
        status: StageStatus,
        error: Optional[str] = None,
        artifacts: Optional[List[str]] = None,
    ) -> ProjectMetadata:
        """Update a stage's execution status in metadata.json."""
        project = self.get_project(project_id)
        if stage_name not in STAGE_NAMES:
            raise ProjectValidationError(
                f"Unknown stage '{stage_name}'. Valid stages are: {STAGE_NAMES}"
            )

        updated_stages = dict(project.metadata.stages)
        prev_stage = updated_stages.get(stage_name) or StageMetadata()
        updated_stages[stage_name] = StageMetadata(
            status=status,
            updated_at=utc_now(),
            error=error,
            artifacts=artifacts if artifacts is not None else prev_stage.artifacts,
        )

        new_meta = project.metadata.model_copy(
            update={"stages": updated_stages, "updated_at": utc_now()}
        )
        self._write_metadata_atomic(project.root_dir, new_meta)
        return new_meta
