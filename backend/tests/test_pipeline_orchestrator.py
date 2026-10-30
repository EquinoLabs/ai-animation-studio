"""Tests for PipelineOrchestrator stage dispatch, prerequisite checks, and failure handling."""

import json
from pathlib import Path
import pytest

from backend.app.domain.contracts import CharacterRef, Scene, SceneConfig, ScenePlan, Script, Section
from backend.app.domain.project import RunManifest
from backend.app.services.pipeline.orchestrator import (
    PipelineOrchestrator,
    PrerequisiteError,
    StageNotImplementedError,
    StageResult,
)
from backend.app.services.project.service import Project, ProjectService


@pytest.fixture
def orchestrator_setup(tmp_path):
    projects_dir = tmp_path / "projects"
    project_service = ProjectService(projects_root=projects_dir)
    orchestrator = PipelineOrchestrator(project_service=project_service)
    project = project_service.create_project(title="Test Pipeline Project")
    return project_service, orchestrator, project


def test_unimplemented_stage_reports_error_not_false_success(orchestrator_setup):
    """Verify that placeholder/unimplemented stages accurately report failure instead of false success."""
    _, orchestrator, project = orchestrator_setup

    res = orchestrator.run_stage(project.metadata.project_id, "script")

    assert not res.success
    assert res.target_stage == "script"
    assert len(res.errors) == 1
    assert "not yet implemented" in res.errors[0]

    # Verify run manifest was written and marks failure
    manifest_data = json.loads(res.run_manifest_path.read_text(encoding="utf-8"))
    manifest = RunManifest.model_validate(manifest_data)
    assert manifest.status == "failed"
    assert "not yet implemented" in manifest.errors[0]


def test_prerequisite_script_stage_has_no_prerequisites(orchestrator_setup):
    """Verify that 'script' stage can start on an empty project without preceding artifacts."""
    _, orchestrator, project = orchestrator_setup
    # Should not raise PrerequisiteError
    orchestrator.check_prerequisites(project, "script")


def test_prerequisites_enforced_plan_scenes(orchestrator_setup):
    """Verify 'plan-scenes' requires script/script.json with status 'final'."""
    project_service, orchestrator, project = orchestrator_setup

    # 1. Missing script.json
    with pytest.raises(PrerequisiteError, match="missing 'script/script.json'"):
        orchestrator.check_prerequisites(project, "plan-scenes")

    # 2. Draft script.json
    script_file = project.script_dir / "script.json"
    draft_script = Script(title="Draft", status="draft", version=1)
    script_file.write_text(draft_script.model_dump_json(), encoding="utf-8")

    with pytest.raises(PrerequisiteError, match="requires status 'final'"):
        orchestrator.check_prerequisites(project, "plan-scenes")

    # 3. Final script.json -> Passes!
    final_script = Script(
        title="Final",
        status="final",
        version=1,
        sections=[Section(name="hook", narration="Narration text")],
    )
    script_file.write_text(final_script.model_dump_json(), encoding="utf-8")
    orchestrator.check_prerequisites(project, "plan-scenes")


def test_prerequisites_enforced_script_to_voice(orchestrator_setup):
    """Verify 'script-to-voice' requires script/script.json with status 'final'."""
    _, orchestrator, project = orchestrator_setup
    with pytest.raises(PrerequisiteError, match="missing 'script/script.json'"):
        orchestrator.check_prerequisites(project, "script-to-voice")


def test_prerequisites_enforced_video(orchestrator_setup):
    """Verify 'video' requires both scene_planning/scene_plan.json and audio in script_to_audio/."""
    _, orchestrator, project = orchestrator_setup

    # 1. Missing scene plan
    with pytest.raises(PrerequisiteError, match="missing 'scene_planning/scene_plan.json'"):
        orchestrator.check_prerequisites(project, "video")

    # Write valid scene plan
    plan_file = project.scene_planning_dir / "scene_plan.json"
    plan = ScenePlan(
        version=1,
        scenes=[
            Scene(
                id="s1",
                beats=[],
            )
        ],
    )
    plan_file.write_text(plan.model_dump_json(), encoding="utf-8")

    # 2. Missing audio artifacts
    with pytest.raises(PrerequisiteError, match="no audio artifacts in 'script_to_audio/'"):
        orchestrator.check_prerequisites(project, "video")

    # Create dummy audio artifact
    dummy_wav = project.script_to_audio_dir / "narration.wav"
    dummy_wav.write_bytes(b"dummy wav data")

    # Now video prerequisites pass
    orchestrator.check_prerequisites(project, "video")


def test_failed_stage_stops_dependent_stages_in_full(orchestrator_setup):
    """Verify that when a stage fails during --full, execution halts immediately."""
    _, orchestrator, project = orchestrator_setup

    executed_stages = []

    def mock_script_success(proj: Project) -> StageResult:
        executed_stages.append("script")
        return StageResult(success=True, artifacts=["script/script.json"])

    def mock_plan_scenes_failure(proj: Project) -> StageResult:
        executed_stages.append("plan-scenes")
        return StageResult(success=False, error="Simulated planning failure")

    def mock_voice_unreached(proj: Project) -> StageResult:
        executed_stages.append("script-to-voice")
        return StageResult(success=True)

    orchestrator.register_stage_handler("script", mock_script_success)
    orchestrator.register_stage_handler("plan-scenes", mock_plan_scenes_failure)
    orchestrator.register_stage_handler("script-to-voice", mock_voice_unreached)

    # Note: we need to satisfy plan-scenes prerequisites for mock_plan_scenes to be called
    script_file = project.script_dir / "script.json"
    script_file.write_text(
        Script(
            title="Test",
            status="final",
            sections=[Section(name="hook", narration="Narration text")],
        ).model_dump_json(),
        encoding="utf-8",
    )

    result = orchestrator.run_full(project.metadata.project_id)

    assert not result.success
    assert executed_stages == ["script", "plan-scenes"]
    assert "script-to-voice" not in executed_stages
    assert "video" not in executed_stages
    assert "Simulated planning failure" in result.errors[0]


def test_full_pipeline_executes_dependency_order_with_mock_handlers(orchestrator_setup):
    """Verify that --full executes all 4 stages in exact dependency order when handlers succeed."""
    _, orchestrator, project = orchestrator_setup

    order_called = []

    def make_handler(name: str, artifact: str):
        def _h(proj: Project) -> StageResult:
            order_called.append(name)
            return StageResult(success=True, artifacts=[artifact])
        return _h

    # Pre-populate artifacts so prerequisites pass for the mocked pipeline
    (project.script_dir / "script.json").write_text(
        Script(
            title="Test",
            status="final",
            sections=[Section(name="hook", narration="Narration text")],
        ).model_dump_json(),
        encoding="utf-8",
    )
    (project.scene_planning_dir / "scene_plan.json").write_text(
        ScenePlan(version=1, scenes=[]).model_dump_json(), encoding="utf-8"
    )
    (project.script_to_audio_dir / "narration.wav").write_bytes(b"dummy audio")

    orchestrator.register_stage_handler("script", make_handler("script", "script/script.json"))
    orchestrator.register_stage_handler("plan-scenes", make_handler("plan-scenes", "scene_planning/scene_plan.json"))
    orchestrator.register_stage_handler("script-to-voice", make_handler("script-to-voice", "script_to_audio/narration.wav"))
    orchestrator.register_stage_handler("video", make_handler("video", "video/final.mp4"))

    result = orchestrator.run_full(project.metadata.project_id)

    assert result.success
    assert order_called == ["script", "plan-scenes", "script-to-voice", "video"]
    assert result.executed_stages == ["script", "plan-scenes", "script-to-voice", "video"]
    assert result.run_manifest_path.is_file()


def test_prerequisites_enforced_script_requires_sections_and_narration(orchestrator_setup):
    """Verify 'plan-scenes' rejects finalized script files with empty sections or blank narration."""
    _, orchestrator, project = orchestrator_setup
    script_file = project.script_dir / "script.json"

    # 1. Final status but 0 sections
    script_file.write_text(
        Script(title="Empty Sections", status="final", sections=[]).model_dump_json(),
        encoding="utf-8",
    )
    with pytest.raises(PrerequisiteError, match="must contain at least one section"):
        orchestrator.check_prerequisites(project, "plan-scenes")

    # 2. Final status with blank whitespace narration
    script_file.write_text(
        Script(
            title="Blank Narration",
            status="final",
            sections=[Section(name="hook", narration="   ")],
        ).model_dump_json(),
        encoding="utf-8",
    )
    with pytest.raises(PrerequisiteError, match="must contain at least one section"):
        orchestrator.check_prerequisites(project, "plan-scenes")


def test_run_interruption_marks_stage_and_manifest_failed(orchestrator_setup):
    """Verify that KeyboardInterrupt or exceptions record failure in metadata and run manifest."""
    project_service, orchestrator, project = orchestrator_setup

    def mock_interrupted_handler(proj: Project) -> StageResult:
        raise KeyboardInterrupt("Simulated user interrupt (Ctrl+C)")

    orchestrator.register_stage_handler("script", mock_interrupted_handler)

    with pytest.raises(KeyboardInterrupt):
        orchestrator.run_stage(project.metadata.project_id, "script")

    # Verify stage marked failed in project metadata
    reloaded_project = project_service.get_project(project.metadata.project_id)
    stage_meta = reloaded_project.metadata.stages["script"]
    assert stage_meta.status == "failed"
    assert "KeyboardInterrupt" in (stage_meta.error or "")

    # Verify run manifest marked failed
    run_files = list(project.runs_dir.glob("*.json"))
    assert len(run_files) == 1
    manifest = RunManifest.model_validate_json(run_files[0].read_text(encoding="utf-8"))
    assert manifest.status == "failed"
    assert manifest.completed_at is not None
    assert any("KeyboardInterrupt" in err for err in manifest.errors)

