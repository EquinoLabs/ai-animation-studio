"""Automated tests for FastAPI REST API endpoints.

Covers:
- Health check
- Project creation, retrieval, and listing
- Script starting, drafting, revision, acceptance, rejection, and finalization
- History and version retrieval
- Error responses (400, 404, 422)
"""

from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.app.api.routes import get_orchestrator, get_project_service
from backend.app.config import Settings
from backend.app.llm.mock import MockLLMProvider
from backend.app.main import create_app
from backend.app.services.pipeline.orchestrator import PipelineOrchestrator
from backend.app.services.project.service import ProjectService
from backend.app.services.scriptwriter.service import ScriptService


@pytest.fixture
def api_client(tmp_path: Path):
    """Create a test client with an isolated ProjectService and PipelineOrchestrator."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        PROJECTS_DIR=projects_dir,
        LLM_SCRIPT_PROVIDER="mock",
    )
    project_service = ProjectService(projects_root=projects_dir)
    mock_llm = MockLLMProvider()
    script_service = ScriptService(
        project_service=project_service,
        llm_provider=mock_llm,
        settings=settings,
    )
    orchestrator = PipelineOrchestrator(
        project_service=project_service,
        script_service=script_service,
        settings=settings,
    )

    app = create_app()
    app.dependency_overrides[get_project_service] = lambda: project_service
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator

    with TestClient(app) as client:
        yield client, project_service, orchestrator


def test_health_check(api_client):
    client, _, _ = api_client
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "ai-animation-studio"}


def test_project_crud_flow(api_client):
    client, _, _ = api_client

    # 1. Initially empty
    res = client.get("/api/v1/projects")
    assert res.status_code == 200
    assert res.json() == []

    # 2. Create project
    create_res = client.post(
        "/api/v1/projects",
        json={"title": "Quantum Physics Explainer"},
    )
    assert create_res.status_code == 201
    proj_data = create_res.json()
    assert proj_data["title"] == "Quantum Physics Explainer"
    project_id = proj_data["project_id"]
    assert project_id is not None

    # 3. Retrieve project
    get_res = client.get(f"/api/v1/projects/{project_id}")
    assert get_res.status_code == 200
    assert get_res.json()["project_id"] == project_id
    assert get_res.json()["title"] == "Quantum Physics Explainer"

    # 4. List projects
    list_res = client.get("/api/v1/projects")
    assert list_res.status_code == 200
    assert len(list_res.json()) == 1

    # 5. Nonexistent project 404
    missing_res = client.get("/api/v1/projects/nonexistent_id")
    assert missing_res.status_code == 404


def test_script_initial_state_empty(api_client):
    client, _, _ = api_client
    proj = client.post("/api/v1/projects", json={"title": "Empty Script Test"}).json()
    project_id = proj["project_id"]

    res = client.get(f"/api/v1/projects/{project_id}/script")
    assert res.status_code == 200
    data = res.json()
    assert data["script"] is None
    assert data["workflow_state"]["accepted_version"] is None
    assert data["workflow_state"]["rejected_version"] is None


def test_script_start_revise_accept_finalize_lifecycle(api_client):
    client, _, _ = api_client
    proj = client.post("/api/v1/projects", json={"title": "Black Holes Video"}).json()
    project_id = proj["project_id"]

    # 1. Start script
    start_res = client.post(
        f"/api/v1/projects/{project_id}/script/start",
        json={"topic": "How Black Holes Work", "target_duration_minutes": 5.0, "allow_unresearched": True},
    )
    assert start_res.status_code == 201
    start_data = start_res.json()
    assert start_data["script"] is not None
    assert start_data["script"]["version"] == 1
    assert start_data["script"]["status"] == "draft"
    assert len(start_data["script"]["sections"]) >= 3

    # 2. Cannot finalize unaccepted draft
    finalize_res = client.post(f"/api/v1/projects/{project_id}/script/finalize")
    assert finalize_res.status_code == 400
    assert "accepted before finalization" in finalize_res.json()["detail"].lower()

    # 3. Revise script
    revise_res = client.post(
        f"/api/v1/projects/{project_id}/script/revise",
        json={"instruction": "Add more details about the event horizon."},
    )
    assert revise_res.status_code == 200
    revise_data = revise_res.json()
    assert revise_data["script"]["version"] == 2
    assert revise_data["script"]["status"] == "draft"

    # 4. Reject draft
    reject_res = client.post(
        f"/api/v1/projects/{project_id}/script/reject",
        json={"reason": "Pacing is too fast."},
    )
    assert reject_res.status_code == 200
    reject_data = reject_res.json()
    assert reject_data["workflow_state"]["rejected_version"] == 2
    assert reject_data["workflow_state"]["rejection_reason"] == "Pacing is too fast."

    # Cannot finalize rejected draft
    finalize_rejected_res = client.post(f"/api/v1/projects/{project_id}/script/finalize")
    assert finalize_rejected_res.status_code == 400

    # 5. Accept draft
    accept_res = client.post(f"/api/v1/projects/{project_id}/script/accept")
    assert accept_res.status_code == 200
    accept_data = accept_res.json()
    assert accept_data["workflow_state"]["accepted_version"] == 2
    assert accept_data["workflow_state"]["rejected_version"] is None

    # 6. Finalize accepted draft
    fin_res = client.post(f"/api/v1/projects/{project_id}/script/finalize")
    assert fin_res.status_code == 200
    fin_data = fin_res.json()
    assert fin_data["script"]["status"] == "final"

    # 7. Check history
    hist_res = client.get(f"/api/v1/projects/{project_id}/script/history")
    assert hist_res.status_code == 200
    history = hist_res.json()
    assert len(history) >= 4  # topic_provided, draft_generated, revision_requested, accepted, finalized

    # 8. Check draft versions
    versions_res = client.get(f"/api/v1/projects/{project_id}/script/versions")
    assert versions_res.status_code == 200
    versions = versions_res.json()
    assert len(versions) == 2

    # 9. Check finalized versions
    finalized_res = client.get(f"/api/v1/projects/{project_id}/script/finalized")
    assert finalized_res.status_code == 200
    finalized_versions = finalized_res.json()
    assert len(finalized_versions) == 1
    assert finalized_versions[0]["version"] == 2


def test_api_validation_errors(api_client):
    client, _, _ = api_client
    proj = client.post("/api/v1/projects", json={"title": "Validation Project"}).json()
    project_id = proj["project_id"]

    # Empty topic should return 422
    bad_start = client.post(
        f"/api/v1/projects/{project_id}/script/start",
        json={"topic": ""},
    )
    assert bad_start.status_code == 422

    # Empty instruction should return 422
    bad_revise = client.post(
        f"/api/v1/projects/{project_id}/script/revise",
        json={"instruction": ""},
    )
    assert bad_revise.status_code == 422

    # Nonexistent project operations should return 404
    missing_start = client.post(
        "/api/v1/projects/not-found/script/start",
        json={"topic": "Quantum Computing"},
    )
    assert missing_start.status_code == 404
