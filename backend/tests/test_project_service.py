"""Tests for ProjectService filesystem management, validation, and safety."""

import json
from pathlib import Path
import pytest

from backend.app.domain.project import ProjectMetadata
from backend.app.services.project.service import (
    ProjectAlreadyExistsError,
    ProjectNotFoundError,
    ProjectSecurityError,
    ProjectService,
    ProjectValidationError,
    REQUIRED_STAGE_DIRS,
)


@pytest.fixture
def temp_project_service(tmp_path):
    projects_dir = tmp_path / "projects"
    return ProjectService(projects_root=projects_dir)


def test_create_project_directory_structure_and_metadata(temp_project_service):
    """Verify that creating a project creates metadata.json and all required stage directories."""
    project = temp_project_service.create_project(title="Why We Overthink")

    assert project.metadata.title == "Why We Overthink"
    assert project.metadata.project_id.startswith("proj_")
    assert project.root_dir.is_dir()

    # Check required directories
    for stage_dir in REQUIRED_STAGE_DIRS:
        stage_path = project.root_dir / stage_dir
        assert stage_path.is_dir(), f"Expected directory '{stage_dir}' was not created."

    # Check metadata.json exists and is valid
    metadata_file = project.root_dir / "metadata.json"
    assert metadata_file.is_file()
    loaded_meta = ProjectMetadata.model_validate_json(metadata_file.read_text(encoding="utf-8"))
    assert loaded_meta.project_id == project.metadata.project_id
    assert loaded_meta.title == "Why We Overthink"
    assert set(loaded_meta.stages.keys()) == {"script", "plan-scenes", "script-to-voice", "video"}
    for stage_meta in loaded_meta.stages.values():
        assert stage_meta.status == "not_started"


def test_project_ids_unique_and_stable(temp_project_service):
    """Verify that auto-generated project IDs are unique across creations."""
    p1 = temp_project_service.create_project(title="Project Alpha")
    p2 = temp_project_service.create_project(title="Project Beta")
    assert p1.metadata.project_id != p2.metadata.project_id

    # Re-reading p1 yields the same ID
    p1_loaded = temp_project_service.get_project(p1.metadata.project_id)
    assert p1_loaded.metadata.project_id == p1.metadata.project_id


def test_duplicate_titles_handled_correctly(temp_project_service):
    """Verify that multiple projects with the exact same title can coexist with unique IDs."""
    p1 = temp_project_service.create_project(title="Identical Title")
    p2 = temp_project_service.create_project(title="Identical Title")

    assert p1.metadata.project_id != p2.metadata.project_id
    assert p1.metadata.title == p2.metadata.title
    assert p1.root_dir != p2.root_dir

    all_projects = temp_project_service.list_projects()
    assert len(all_projects) == 2


def test_load_and_list_existing_projects(temp_project_service):
    """Verify loading a project by ID and listing all projects in root."""
    p1 = temp_project_service.create_project(title="First Project")
    p2 = temp_project_service.create_project(title="Second Project")

    # Load specific project
    loaded = temp_project_service.get_project(p1.metadata.project_id)
    assert loaded.metadata.title == "First Project"
    assert loaded.script_dir == p1.script_dir

    # List projects
    listing = temp_project_service.list_projects()
    assert len(listing) == 2
    listed_ids = {p.project_id for p in listing}
    assert p1.metadata.project_id in listed_ids
    assert p2.metadata.project_id in listed_ids


def test_nonexistent_project_raises_not_found(temp_project_service):
    """Verify that querying a nonexistent project raises ProjectNotFoundError."""
    with pytest.raises(ProjectNotFoundError, match="does not exist"):
        temp_project_service.get_project("proj_does_not_exist")


def test_invalid_missing_or_corrupt_metadata_handled(temp_project_service, tmp_path):
    """Verify that corrupted metadata or missing directories raise ProjectValidationError."""
    project = temp_project_service.create_project(title="Healthy Project")
    meta_path = project.root_dir / "metadata.json"

    # Corrupt metadata JSON
    meta_path.write_text("{ corrupt json ...", encoding="utf-8")
    with pytest.raises(ProjectValidationError, match="corrupted metadata.json"):
        temp_project_service.get_project(project.metadata.project_id)

    # Missing metadata file
    meta_path.unlink()
    with pytest.raises(ProjectValidationError, match="missing metadata.json"):
        temp_project_service.get_project(project.metadata.project_id)

    # Missing required stage directory
    temp_project_service.create_project(title="Dir Check", project_id="proj_dir_check")
    missing_dir = temp_project_service.projects_root / "proj_dir_check" / "video"
    missing_dir.rmdir()
    with pytest.raises(ProjectValidationError, match="corrupted directory layout"):
        temp_project_service.get_project("proj_dir_check")


def test_metadata_update_atomic_and_timestamps(temp_project_service):
    """Verify updating stage status atomically updates metadata and timestamps."""
    project = temp_project_service.create_project(title="Stage Update Test")
    t0 = project.metadata.updated_at

    updated_meta = temp_project_service.update_stage_status(
        project_id=project.metadata.project_id,
        stage_name="script",
        status="completed",
        artifacts=["script/script.json"],
    )

    assert updated_meta.stages["script"].status == "completed"
    assert updated_meta.stages["script"].artifacts == ["script/script.json"]
    assert updated_meta.updated_at >= t0

    # Verify persisted on disk
    reloaded = temp_project_service.get_project(project.metadata.project_id)
    assert reloaded.metadata.stages["script"].status == "completed"


def test_path_traversal_prevention(temp_project_service):
    """Verify that path traversal attempts in project IDs are blocked."""
    with pytest.raises(ProjectSecurityError, match="Invalid project ID|Path traversal"):
        temp_project_service.get_project("../escaped_project")

    with pytest.raises(ProjectSecurityError, match="Invalid project ID|Path traversal"):
        temp_project_service.create_project(title="Attack", project_id="../../hack")


def test_symlink_and_junction_traversal_blocked(temp_project_service, tmp_path):
    """Verify that a directory symlink or junction pointing outside project root is blocked."""
    import os
    outside_dir = tmp_path / "outside_target"
    outside_dir.mkdir(parents=True, exist_ok=True)
    junction_link = temp_project_service.projects_root / "proj_junction_link"

    created = False
    # Attempt junction on Windows or symlink on Unix/Developer mode
    try:
        import _winapi
        _winapi.CreateJunction(str(outside_dir), str(junction_link))
        created = True
    except (ImportError, OSError):
        try:
            os.symlink(outside_dir, junction_link, target_is_directory=True)
            created = True
        except OSError:
            pass

    if created:
        with pytest.raises(ProjectSecurityError, match="Path traversal detected"):
            temp_project_service.get_project("proj_junction_link")
