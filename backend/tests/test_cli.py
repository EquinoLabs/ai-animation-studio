"""Tests for the AI Animation Studio CLI interface."""

import json
from pathlib import Path
import pytest

from backend.app.cli.main import run_cli
from backend.app.domain.contracts import ScenePlan, Script
from backend.app.services.pipeline.orchestrator import PipelineOrchestrator, StageResult
from backend.app.services.project.service import ProjectService


@pytest.fixture
def cli_projects_dir(tmp_path):
    projects_dir = tmp_path / "cli_projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    return projects_dir


def test_cli_create_project_and_listing(cli_projects_dir, capsys):
    """Verify creating a project and listing projects via CLI."""
    # 1. Create project
    code = run_cli([
        "--new-project",
        "--title",
        "Why We Overthink",
        "--projects-dir",
        str(cli_projects_dir),
    ])
    assert code == 0
    out, err = capsys.readouterr()
    assert "Project created successfully" in out
    assert "Why We Overthink" in out

    # 2. List projects
    code_list = run_cli([
        "--list-projects",
        "--projects-dir",
        str(cli_projects_dir),
    ])
    assert code_list == 0
    out_list, _ = capsys.readouterr()
    assert "Why We Overthink" in out_list


def test_cli_missing_required_args_fails(cli_projects_dir, capsys):
    """Verify missing required arguments return exit code 2."""
    # --new-project without --title
    code1 = run_cli(["--new-project", "--projects-dir", str(cli_projects_dir)])
    assert code1 == 2
    _, err1 = capsys.readouterr()
    assert "requires a non-empty --title" in err1

    # --script without --project
    code2 = run_cli(["--script", "--projects-dir", str(cli_projects_dir)])
    assert code2 == 2
    _, err2 = capsys.readouterr()
    assert "requires --project" in err2


def test_cli_invalid_arg_combinations_fail(cli_projects_dir, capsys):
    """Verify invalid argument combinations return nonzero exit codes."""
    # --title without --new-project
    code = run_cli(["--list-projects", "--title", "Orphan Title", "--projects-dir", str(cli_projects_dir)])
    assert code == 2
    _, err = capsys.readouterr()
    assert "--title can only be used with --new-project" in err


def test_cli_unknown_project_fails(cli_projects_dir, capsys):
    """Verify executing a stage against a nonexistent project returns exit code 1."""
    code = run_cli([
        "--script",
        "--project",
        "proj_nonexistent_123",
        "--projects-dir",
        str(cli_projects_dir),
    ])
    assert code == 1
    _, err = capsys.readouterr()
    assert "does not exist" in err


def test_cli_unimplemented_stage_fails(cli_projects_dir, capsys):
    """Verify invoking an unimplemented stage fails cleanly and reports unready state."""
    # Create project first
    run_cli(["--new-project", "--title", "Stub Project", "--project", "proj_stub", "--projects-dir", str(cli_projects_dir)])
    capsys.readouterr()

    # Run script stage (currently unimplemented)
    code = run_cli([
        "--script",
        "--project",
        "proj_stub",
        "--projects-dir",
        str(cli_projects_dir),
    ])
    assert code == 1
    _, err = capsys.readouterr()
    assert "not yet implemented" in err


def test_cli_json_output_mode(cli_projects_dir, capsys):
    """Verify --json produces valid parseable JSON."""
    code = run_cli([
        "--new-project",
        "--title",
        "JSON Project",
        "--project",
        "proj_json_01",
        "--projects-dir",
        str(cli_projects_dir),
        "--json",
    ])
    assert code == 0
    out, _ = capsys.readouterr()
    parsed = json.loads(out)
    assert parsed["project_id"] == "proj_json_01"
    assert parsed["title"] == "JSON Project"


def test_cli_aliases(cli_projects_dir, capsys):
    """Verify --script-writing and --scene-builder aliases behave identically to primary flags."""
    # 1. Create project
    run_cli(["--new-project", "--title", "Alias Project", "--project", "proj_alias", "--projects-dir", str(cli_projects_dir)])
    capsys.readouterr()

    # 2. --script-writing requires --project
    code_no_proj = run_cli(["--script-writing", "--projects-dir", str(cli_projects_dir)])
    assert code_no_proj == 2
    _, err_no_proj = capsys.readouterr()
    assert "requires --project" in err_no_proj

    # 3. --scene-builder checks prerequisites
    code_scene = run_cli(["--scene-builder", "--project", "proj_alias", "--projects-dir", str(cli_projects_dir)])
    assert code_scene == 1
    _, err_scene = capsys.readouterr()
    assert "Prerequisite not met for 'plan-scenes'" in err_scene


def test_root_main_delegates_to_cli(cli_projects_dir):
    """Verify root main.py correctly executes and forwards CLI arguments."""
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parent.parent.parent
    main_py = repo_root / "main.py"
    assert main_py.exists(), "Root main.py must exist"

    # Run list-projects through root main.py
    proc = subprocess.run(
        [sys.executable, str(main_py), "--list-projects", "--projects-dir", str(cli_projects_dir)],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
    )
    assert proc.returncode == 0
    assert "Found" in proc.stdout or "No projects found" in proc.stdout
