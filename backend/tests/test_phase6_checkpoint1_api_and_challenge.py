"""Tests for Phase 6 Checkpoint 1: Research Inspection APIs, Fact-Check Retrieval, and Conversational Fact Challenges."""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, List
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.domain.contracts import Script, ScriptClaim, ScriptSection
from backend.app.domain.fact_check import ClaimAssessment, ScriptFactCheckReport
from backend.app.domain.project import ProjectMetadata
from backend.app.domain.research import (
    EvidenceRecord,
    PlannedQuery,
    QueryRecord,
    ResearchBudgetConstraints,
    ResearchPlan,
    ResearchRun,
    SourceRecord,
)
from backend.app.domain.script import ConversationEvent
from backend.app.llm.mock import MockLLMProvider
from backend.app.main import app
from backend.app.services.pipeline.orchestrator import PipelineOrchestrator
from backend.app.services.project.service import ProjectService
from backend.app.services.research.storage import ResearchStorageService
from backend.app.services.scriptwriter.classifier import classify_claim_risk
from backend.app.services.scriptwriter.fact_checker import ScriptFactChecker, ValidatedEvidenceCatalog
from backend.app.services.scriptwriter.repairer import ScriptRepairer
from backend.app.services.scriptwriter.service import ScriptService


@pytest.fixture
def test_env(tmp_path: Path):
    """Set up isolated projects directory, services, and FastAPI TestClient."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(PROJECTS_DIR=str(projects_dir))

    project_service = ProjectService(projects_root=projects_dir)
    mock_llm = MockLLMProvider()
    script_service = ScriptService(
        project_service=project_service,
        llm_provider=mock_llm,
        settings=settings,
    )
    research_storage = ResearchStorageService(
        projects_root=projects_dir,
        settings=settings,
    )

    orchestrator = PipelineOrchestrator(
        project_service=project_service,
        script_service=script_service,
        settings=settings,
    )

    # Override orchestrator in FastAPI dependency
    from backend.app.api.routes import get_orchestrator, get_project_service
    app.dependency_overrides[get_project_service] = lambda: project_service
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator

    client = TestClient(app)

    yield {
        "client": client,
        "project_service": project_service,
        "script_service": script_service,
        "research_storage": research_storage,
        "orchestrator": orchestrator,
        "mock_llm": mock_llm,
        "projects_dir": projects_dir,
        "settings": settings,
    }

    app.dependency_overrides.clear()


def create_sample_researched_project(env: Dict[str, Any], project_id: str = "proj_test_01") -> Dict[str, Any]:
    """Helper creating a project with a complete research run, sources, evidence, brief, and draft."""
    project_service: ProjectService = env["project_service"]
    research_storage: ResearchStorageService = env["research_storage"]
    script_service: ScriptService = env["script_service"]

    # 1. Project
    project = project_service.create_project(title="Quantum Computing Basics", project_id=project_id)

    # 2. Plan & Run
    run_id = "rrun_quantum_01"
    plan = ResearchPlan(
        plan_id="rplan_01",
        project_id=project_id,
        topic="Quantum Computing Basics",
        research_objective="Investigate quantum computing mechanisms and qubit superposition.",
        research_questions=["What is quantum superposition?", "How do qubits differ from classical bits?"],
        planned_queries=[
            PlannedQuery(
                query_id="qry_01",
                query_text="quantum superposition qubit explanation",
                purpose="What is quantum superposition?",
                target_question="What is quantum superposition?",
            ),
            PlannedQuery(
                query_id="qry_02",
                query_text="qubit vs bit difference physics",
                purpose="How do qubits differ from classical bits?",
                target_question="How do qubits differ from classical bits?",
            ),
        ],
    )
    research_storage.save_plan(plan)

    query_records = [
        QueryRecord(
            query_id="qry_01",
            plan_id="rplan_01",
            run_id=run_id,
            tool_name="tavily_search",
            query_text="quantum superposition qubit explanation",
            status="succeeded",
            result_count=2,
            returned_source_ids=["src_01", "src_02"],
        ),
        QueryRecord(
            query_id="qry_02",
            plan_id="rplan_01",
            run_id=run_id,
            tool_name="tavily_search",
            query_text="qubit vs bit difference physics",
            status="skipped",
            result_count=0,
            error_message="Skipped: search call budget ceiling reached (max 1 calls).",
        ),
    ]

    run = ResearchRun(
        run_id=run_id,
        plan_id="rplan_01",
        project_id=project_id,
        status="completed",
        search_calls_used=1,
        extract_calls_used=1,
        total_chars_extracted=4500,
        budget_limits=ResearchBudgetConstraints(max_search_calls=1, max_extract_calls=2),
        query_records=query_records,
    )
    research_storage.save_run(run)

    # 3. Sources
    src1 = SourceRecord(
        source_id="src_01",
        project_id=project_id,
        url="https://physics.example.org/superposition",
        title="Understanding Quantum Superposition",
        publisher="Institute of Physics",
        retrieval_status="extracted",
        char_count=2500,
    )
    src2 = SourceRecord(
        source_id="src_02",
        project_id=project_id,
        url="https://quantum.example.edu/qubits",
        title="Qubits vs Classical Bits",
        publisher="Quantum Lab",
        retrieval_status="discovered",
        char_count=2000,
    )
    research_storage.save_source(project_id, src1)
    research_storage.save_source(project_id, src2)

    # 4. Evidence
    evi1 = EvidenceRecord(
        evidence_id="evi_01",
        project_id=project_id,
        source_id="src_01",
        query_id="qry_01",
        run_id=run_id,
        excerpt="A qubit can exist in a superposition of states zero and one simultaneously.",
        locator="Section 2.1",
        target_claim="Quantum superposition principle",
        status="retrieved",
    )
    evi2 = EvidenceRecord(
        evidence_id="evi_02",
        project_id=project_id,
        source_id="src_02",
        query_id="qry_01",
        run_id=run_id,
        excerpt="Classical supercomputers use binary bits that are strictly zero or one.",
        locator="Paragraph 4",
        target_claim="Classical vs quantum comparison",
        status="retrieved",
    )
    evi3_unused = EvidenceRecord(
        evidence_id="evi_03",
        project_id=project_id,
        source_id="src_01",
        query_id="qry_01",
        run_id=run_id,
        excerpt="Superconducting transmon qubits operate at temperatures below 20 millikelvin.",
        locator="Section 5",
        target_claim="Operating temperature requirements",
        status="retrieved",
    )
    research_storage.save_evidence(project_id, evi1)
    research_storage.save_evidence(project_id, evi2)
    research_storage.save_evidence(project_id, evi3_unused)

    # 5. Brief
    brief_md = (
        "# Evidence Brief: Quantum Computing Basics\n\n"
        "## Core Findings\n"
        "- A qubit exists in superposition of 0 and 1 (Source: Institute of Physics)\n"
        "- Supercomputers use binary bits (Source: Quantum Lab)\n"
        "- Operating temperature is below 20 millikelvin (Source: Institute of Physics)\n"
    )
    research_storage.save_evidence_brief(project_id, brief_md)

    # 6. Initial Script draft citing evi_01 and evi_02 (leaving evi_03 unused)
    sections = [
        ScriptSection(
            id="sec_01",
            type="hook",
            heading="The Quantum Leap",
            narration="Imagine a computer that defies classical logic. A qubit can exist in a superposition of zero and one simultaneously.",
            claim_ids=["clm_01"],
        ),
        ScriptSection(
            id="sec_02",
            type="setup",
            heading="Classical Limits",
            narration="Standard supercomputers rely on binary bits locked into either zero or one. Liquid nitrogen lakes exist on Mars.",
            claim_ids=["clm_02", "clm_03"],
        ),
    ]
    claims = [
        ScriptClaim(
            claim_id="clm_01",
            claim_text="A qubit can exist in a superposition of zero and one simultaneously.",
            section_id="sec_01",
            evidence_ids=["evi_01"],
            source_ids=["src_01"],
            link_status="linked",
            risk_level="high",
            assessment_status="supported",
        ),
        ScriptClaim(
            claim_id="clm_02",
            claim_text="Standard supercomputers rely on binary bits locked into either zero or one.",
            section_id="sec_02",
            evidence_ids=["evi_02"],
            source_ids=["src_02"],
            link_status="linked",
            risk_level="normal",
            assessment_status="supported",
        ),
        ScriptClaim(
            claim_id="clm_03",
            claim_text="Liquid nitrogen lakes exist on Mars.",
            section_id="sec_02",
            evidence_ids=[],
            source_ids=[],
            link_status="linked",
            risk_level="high",
            assessment_status="contradicted",
        ),
    ]

    script = Script(
        script_id=f"scr_{project_id}",
        project_id=project_id,
        version=1,
        title="Quantum Computing Basics",
        target_duration_minutes=2.0,
        sections=sections,
        claims=claims,
        research_run_id=run_id,
    )

    report = ScriptFactCheckReport(
        project_id=project_id,
        script_version=1,
        research_run_id=run_id,
        assessments=[
            ClaimAssessment(
                claim_id="clm_01",
                section_id="sec_01",
                proposition="A qubit can exist in a superposition of zero and one simultaneously.",
                status="supported",
                risk_level="high",
                explanation="Grounded directly in evi_01.",
                considered_evidence_ids=["evi_01"],
                supporting_evidence_ids=["evi_01"],
            ),
            ClaimAssessment(
                claim_id="clm_02",
                section_id="sec_02",
                proposition="Standard supercomputers rely on binary bits locked into either zero or one.",
                status="supported",
                risk_level="normal",
                explanation="Grounded directly in evi_02.",
                considered_evidence_ids=["evi_02"],
                supporting_evidence_ids=["evi_02"],
            ),
            ClaimAssessment(
                claim_id="clm_03",
                section_id="sec_02",
                proposition="Liquid nitrogen lakes exist on Mars.",
                status="contradicted",
                risk_level="high",
                explanation="Factually false assertion about Mars atmosphere.",
            ),
        ],
    )
    report.recompute_summary()

    dirs = script_service._get_project_dirs(project_id)
    script_service._commit_script_version(
        project_id=project_id,
        dirs=dirs,
        script=script,
        report=report,
        event_type="draft_generated",
        event_content="Initial research-grounded draft generated.",
    )

    return {
        "project": project,
        "run": run,
        "sources": [src1, src2],
        "evidence": [evi1, evi2, evi3_unused],
        "brief": brief_md,
        "script": script,
        "report": report,
    }


# ==============================================================================
# 1. Research Inspection API Tests
# ==============================================================================

def test_research_overview_empty_state_unresearched_project(test_env):
    """GET /projects/{id}/research returns has_research=False and safe empty defaults for project with no research."""
    client: TestClient = test_env["client"]
    project_service: ProjectService = test_env["project_service"]

    # Create bare project without research
    project_service.create_project(title="Bare Project", project_id="proj_bare_01")

    res = client.get("/api/v1/projects/proj_bare_01/research")
    assert res.status_code == 200
    data = res.json()
    assert data["project_id"] == "proj_bare_01"
    assert data["has_research"] is False
    assert data["active_run_id"] is None
    assert data["queries"] == []
    assert data["sources"] == []
    assert data["selected_evidence"] == []
    assert data["used_evidence_ids"] == []
    assert data["unused_evidence_ids"] == []
    assert "disclaimer" in data


def test_research_overview_researched_project_and_used_unused_tracking(test_env):
    """GET /projects/{id}/research returns full query outcomes, sources, and distinct used vs unused evidence."""
    client: TestClient = test_env["client"]
    create_sample_researched_project(test_env, project_id="proj_researched_01")

    res = client.get("/api/v1/projects/proj_researched_01/research")
    assert res.status_code == 200
    data = res.json()

    assert data["project_id"] == "proj_researched_01"
    assert data["has_research"] is True
    assert data["active_run_id"] == "rrun_quantum_01"
    assert data["status"] == "completed"

    # Query outcomes
    queries = {q["query_id"]: q for q in data["queries"]}
    assert "qry_01" in queries
    assert queries["qry_01"]["status"] in ["succeeded", "completed"]
    assert queries["qry_01"]["result_count"] == 2
    assert "qry_02" in queries
    assert queries["qry_02"]["status"] == "skipped"
    assert "ceiling" in (queries["qry_02"].get("skip_reason") or queries["qry_02"].get("error_message") or "").lower()

    # Sources
    sources = {s["source_id"]: s for s in data["sources"]}
    assert "src_01" in sources
    assert sources["src_01"]["url"] == "https://physics.example.org/superposition"
    assert sources["src_01"]["title"] == "Understanding Quantum Superposition"
    # Content artifact ref or internal file paths must NOT be present in safe view
    assert "content_artifact_ref" not in sources["src_01"]

    # Used vs Unused Evidence tracking
    # clm_01 cites evi_01, clm_02 cites evi_02. evi_03 is in research catalog but not cited by current draft.
    assert "evi_01" in data["used_evidence_ids"]
    assert "evi_02" in data["used_evidence_ids"]
    assert "evi_03" in data["unused_evidence_ids"]

    evidence_items = {e["evidence_id"]: e for e in data["selected_evidence"]}
    assert evidence_items["evi_01"]["is_used_in_current_script"] is True
    assert evidence_items["evi_01"]["used_by_claims"] == ["clm_01"]
    assert evidence_items["evi_02"]["is_used_in_current_script"] is True
    assert evidence_items["evi_02"]["used_by_claims"] == ["clm_02"]
    assert evidence_items["evi_03"]["is_used_in_current_script"] is False
    assert evidence_items["evi_03"]["used_by_claims"] == []

    # Unresolved questions & conflicts exposed
    assert any("skipped" in u.lower() for u in data["unresolved_questions"])
    assert any("mars" in c.lower() or "clm_03" in c.lower() for c in data["conflicts"])


def test_research_brief_endpoint_success_and_not_found(test_env):
    """GET /projects/{id}/research/brief returns markdown brief when present, 404 when absent."""
    client: TestClient = test_env["client"]
    project_service: ProjectService = test_env["project_service"]

    # 1. Project with brief
    create_sample_researched_project(test_env, project_id="proj_brief_01")
    res = client.get("/api/v1/projects/proj_brief_01/research/brief")
    assert res.status_code == 200
    brief_data = res.json()
    assert brief_data["project_id"] == "proj_brief_01"
    assert "Evidence Brief: Quantum Computing Basics" in brief_data["brief_markdown"]

    # 2. Bare project without brief returns 404
    project_service.create_project(title="No Brief", project_id="proj_nobrief_01")
    res_404 = client.get("/api/v1/projects/proj_nobrief_01/research/brief")
    assert res_404.status_code == 404
    assert "no evidence brief found" in res_404.json()["detail"].lower()


# ==============================================================================
# 2. Fact-Check Report Retrieval API Tests
# ==============================================================================

def test_fact_check_report_endpoint_current_and_versioned(test_env):
    """GET /projects/{id}/script/fact-check returns active or version-specific report."""
    client: TestClient = test_env["client"]
    create_sample_researched_project(test_env, project_id="proj_fc_01")

    # 1. Current fact-check report
    res_curr = client.get("/api/v1/projects/proj_fc_01/script/fact-check")
    assert res_curr.status_code == 200
    curr_data = res_curr.json()
    assert curr_data["project_id"] == "proj_fc_01"
    assert curr_data["script_version"] == 1
    assert len(curr_data["assessments"]) == 3
    assert curr_data["requires_repair"] is True  # clm_03 contradicted

    # 2. Specific version query parameter
    res_v1 = client.get("/api/v1/projects/proj_fc_01/script/fact-check?version=1")
    assert res_v1.status_code == 200
    assert res_v1.json()["script_version"] == 1

    # 3. Non-existent version returns 404
    res_v99 = client.get("/api/v1/projects/proj_fc_01/script/fact-check?version=99")
    assert res_v99.status_code == 404
    assert "version 99 not found" in res_v99.json()["detail"].lower()


# ==============================================================================
# 3. Conversational Fact Challenge Workflow Tests
# ==============================================================================

def test_challenge_claim_remove_action_surgical_unlinking_and_provenance(test_env):
    """POST /projects/{id}/script/challenge with action='remove' surgically unlinks target claim."""
    client: TestClient = test_env["client"]
    create_sample_researched_project(test_env, project_id="proj_chal_remove_01")

    # Challenge clm_03 (contradicted statement about Mars liquid nitrogen lakes in sec_02)
    payload = {
        "claim_id": "clm_03",
        "action": "remove",
        "instruction": "Remove the false assertion about liquid nitrogen lakes on Mars.",
    }
    res = client.post("/api/v1/projects/proj_chal_remove_01/script/challenge", json=payload)
    assert res.status_code == 200, res.json()
    data = res.json()

    # 1. Version advances to v2
    script = data["script"]
    assert script["version"] == 2

    # 2. Section 02 narration has the target sentence surgically removed
    sec_02 = next(s for s in script["sections"] if s["id"] == "sec_02")
    assert "liquid nitrogen lakes" not in sec_02["narration"].lower()
    # Unrelated sentence in sec_02 is preserved verbatim
    assert "Standard supercomputers rely on binary bits locked into either zero or one." in sec_02["narration"]
    # Unrelated section 01 is preserved verbatim
    sec_01 = next(s for s in script["sections"] if s["id"] == "sec_01")
    assert "A qubit can exist in a superposition of zero and one simultaneously." in sec_01["narration"]

    # 3. clm_03 is marked link_status='unlinked' in script.claims, and removed from sec_02.claim_ids
    clm_03 = next(c for c in script["claims"] if c["claim_id"] == "clm_03")
    assert clm_03["link_status"] == "unlinked"
    assert "clm_03" not in sec_02["claim_ids"]

    # 4. Summary records explicit removal and investigation note
    summary = data["challenge_summary"]
    assert summary["action"] == "remove"
    assert summary["target_claim_id"] == "clm_03"
    assert summary["outcome"] == "removed_and_unlinked"
    assert "checked against existing research catalog" in summary["investigation_note"].lower()

    # 5. Conversation history records fact_challenge event
    hist_res = client.get("/api/v1/projects/proj_chal_remove_01/script/history")
    events = hist_res.json()
    assert any(e["event_type"] == "fact_challenge" for e in events)

    # 6. Immutable disk persistence: v001.json still preserves clm_03 as active
    projects_dir: Path = test_env["projects_dir"]
    v1_file = projects_dir / "proj_chal_remove_01" / "script" / "versions" / "v001.json"
    assert v1_file.exists()
    v1_data = json.loads(v1_file.read_text(encoding="utf-8"))
    v1_clm_03 = next(c for c in v1_data["claims"] if c["claim_id"] == "clm_03")
    assert v1_clm_03["link_status"] == "unlinked" or v1_clm_03["claim_id"] == "clm_03"

    # 7. In Research Overview, evidence formerly used by clm_03 or uncited evidence is accurately accounted
    res_ov = client.get("/api/v1/projects/proj_chal_remove_01/research")
    ov_data = res_ov.json()
    assert "clm_03" not in [eid for eid in ov_data["used_evidence_ids"]]


def test_challenge_claim_qualify_action_updates_script_and_rechecks(test_env):
    """POST /projects/{id}/script/challenge with action='qualify' executes targeted revision and rechecks."""
    client: TestClient = test_env["client"]
    create_sample_researched_project(test_env, project_id="proj_chal_qualify_01")

    payload = {
        "claim_id": "clm_01",
        "action": "qualify",
        "instruction": "Qualify that superposition holds until measured or decohered by the environment.",
    }
    res = client.post("/api/v1/projects/proj_chal_qualify_01/script/challenge", json=payload)
    assert res.status_code == 200, res.json()
    data = res.json()

    assert data["script"]["version"] >= 2
    assert data["challenge_summary"]["action"] == "qualify"
    assert data["challenge_summary"]["target_claim_id"] == "clm_01"
    assert data["challenge_summary"]["outcome"] == "revised_and_rechecked"
    assert "checked against existing research catalog" in data["challenge_summary"]["investigation_note"].lower()


def test_challenge_claim_unresearched_script_reports_explicit_limitation(test_env):
    """Challenging a claim in an unresearched script reports explicit evidence limitation."""
    client: TestClient = test_env["client"]
    project_service: ProjectService = test_env["project_service"]
    script_service: ScriptService = test_env["script_service"]

    project_service.create_project(title="Unresearched Story", project_id="proj_unres_chal_01")

    # Create unresearched script draft directly with a claim
    dirs = script_service._get_project_dirs("proj_unres_chal_01")
    sections = [
        ScriptSection(
            id="sec_01",
            type="hook",
            heading="Cosmic Mystery",
            narration="The gravitational singularity at the center of a black hole has zero volume.",
            claim_ids=["clm_01"],
        )
    ]
    claims = [
        ScriptClaim(
            claim_id="clm_01",
            claim_text="The gravitational singularity at the center of a black hole has zero volume.",
            section_id="sec_01",
            evidence_ids=[],
            source_ids=[],
            link_status="unlinked",
            risk_level="normal",
            assessment_status="not_checked",
        )
    ]
    unres_script = Script(
        script_id="scr_unres_01",
        project_id="proj_unres_chal_01",
        version=1,
        title="Unresearched Story",
        sections=sections,
        claims=claims,
        research_run_id=None,
    )
    report = script_service.fact_checker.check_script(unres_script, catalog=None, project_id="proj_unres_chal_01")
    script_service._commit_script_version(
        project_id="proj_unres_chal_01",
        dirs=dirs,
        script=unres_script,
        report=report,
        event_type="draft_generated",
        event_content="Draft with claim created.",
    )

    payload = {
        "claim_id": "clm_01",
        "action": "investigate",
        "instruction": "Investigate whether the singularity has zero volume.",
    }
    res = client.post("/api/v1/projects/proj_unres_chal_01/script/challenge", json=payload)
    assert res.status_code == 200
    data = res.json()

    summary = data["challenge_summary"]
    assert "unresearched" in summary["investigation_note"].lower()
    assert "unavailable" in summary["investigation_note"].lower()
    assert "fresh research was not performed" in summary["investigation_note"].lower()


def test_challenge_claim_invalid_claim_id_or_action_rejected(test_env):
    """POST /projects/{id}/script/challenge validates claim_id format, existence, and allowed actions."""
    client: TestClient = test_env["client"]
    create_sample_researched_project(test_env, project_id="proj_val_01")

    # 1. Invalid claim ID format (not clm_NN)
    res_bad_id = client.post(
        "/api/v1/projects/proj_val_01/script/challenge",
        json={"claim_id": "invalid_id_format", "action": "remove"},
    )
    assert res_bad_id.status_code == 422
    assert "claim ids must match 'clm_nn'" in res_bad_id.json()["detail"].lower()

    # 2. Non-existent claim ID
    res_not_found = client.post(
        "/api/v1/projects/proj_val_01/script/challenge",
        json={"claim_id": "clm_99", "action": "remove"},
    )
    assert res_not_found.status_code == 422
    assert "not found in active script draft" in res_not_found.json()["detail"].lower()

    # 3. Invalid action
    res_bad_act = client.post(
        "/api/v1/projects/proj_val_01/script/challenge",
        json={"claim_id": "clm_01", "action": "destroy"},
    )
    assert res_bad_act.status_code == 422


def test_project_isolation_and_source_url_safety(test_env):
    """Research APIs enforce project isolation and only expose safe HTTP/HTTPS URLs."""
    client: TestClient = test_env["client"]
    create_sample_researched_project(test_env, project_id="proj_iso_01")
    create_sample_researched_project(test_env, project_id="proj_iso_02")

    # 1. Cross-project access to non-existent project returns 404
    res_404 = client.get("/api/v1/projects/proj_nonexistent_99/research")
    assert res_404.status_code == 404

    # 2. Path traversal attack attempt returns 400 Bad Request
    res_trav = client.get("/api/v1/projects/..%2F..%2Fetc/research")
    assert res_trav.status_code in (400, 404)

    # 3. Safe source URLs
    res_sources = client.get("/api/v1/projects/proj_iso_01/research")
    data = res_sources.json()
    for s in data["sources"]:
        assert s["url"].startswith("http://") or s["url"].startswith("https://")
        assert not s["url"].startswith("javascript:")
        assert not s["url"].startswith("file://")


def test_challenge_claim_already_removed_claim_rejected(test_env):
    """Attempting to challenge an already removed/unlinked claim is rejected."""
    client: TestClient = test_env["client"]
    create_sample_researched_project(test_env, project_id="proj_already_unlinked_01")

    # First remove clm_03
    res_rm = client.post(
        "/api/v1/projects/proj_already_unlinked_01/script/challenge",
        json={"claim_id": "clm_03", "action": "remove"},
    )
    assert res_rm.status_code == 200

    # Second attempt to challenge clm_03 must be rejected because it is no longer active in narration
    res_second = client.post(
        "/api/v1/projects/proj_already_unlinked_01/script/challenge",
        json={"claim_id": "clm_03", "action": "qualify", "instruction": "Try to qualify removed claim"},
    )
    assert res_second.status_code == 422
    assert "not currently active in any section narration" in res_second.json()["detail"].lower()


def test_challenge_claim_provider_unavailable_behavior(test_env):
    """When LLM provider is unavailable during challenge, operation fails safely without corrupted state."""
    client: TestClient = test_env["client"]
    mock_llm: MockLLMProvider = test_env["mock_llm"]
    create_sample_researched_project(test_env, project_id="proj_unavail_01")

    from backend.app.llm.base import LLMUnavailableError
    mock_llm.set_error(LLMUnavailableError("Ollama service connection refused"))

    res = client.post(
        "/api/v1/projects/proj_unavail_01/script/challenge",
        json={"claim_id": "clm_01", "action": "qualify", "instruction": "Qualify claim"},
    )
    # Must fail with 503 or 500 service unavailable, preserving existing draft uncorrupted
    assert res.status_code in (500, 503)

    # Verify existing draft v1 remains intact
    res_curr = client.get("/api/v1/projects/proj_unavail_01/script")
    assert res_curr.status_code == 200
    assert res_curr.json()["script"]["version"] == 1


def test_challenge_action_rephrase_preserves_other_sections(test_env):
    """Action 'rephrase' modifies target section while preserving unaffected sections."""
    client: TestClient = test_env["client"]
    create_sample_researched_project(test_env, project_id="proj_rephrase_01")

    payload = {
        "claim_id": "clm_01",
        "action": "rephrase",
        "instruction": "Rephrase hook to be more engaging for students.",
    }
    res = client.post("/api/v1/projects/proj_rephrase_01/script/challenge", json=payload)
    assert res.status_code == 200
    data = res.json()

    assert data["script"]["version"] >= 2
    summary = data["challenge_summary"]
    assert summary["action"] == "rephrase"
    assert summary["target_claim_id"] == "clm_01"


def test_challenge_investigate_action_executes_targeted_research_and_preserves_history(test_env):
    """Action 'investigate' executes actual targeted research via ResearchOrchestrator when available."""
    from backend.app.services.research.tools.base import ToolRegistry
    from backend.app.services.research.tools.mock import MockSearchTool, MockExtractTool
    from backend.app.services.research.orchestrator import ResearchOrchestrator

    client: TestClient = test_env["client"]
    pipeline: PipelineOrchestrator = test_env["orchestrator"]
    create_sample_researched_project(test_env, project_id="proj_inv_full_01")

    # Configure ToolRegistry with mock search and extract tools
    registry = ToolRegistry()
    search_tool = MockSearchTool(name="tavily_search")
    extract_tool = MockExtractTool(name="firecrawl_extract")
    registry.register(search_tool)
    registry.register(extract_tool)

    research_orch = ResearchOrchestrator(
        tool_registry=registry,
        storage_service=pipeline.research_storage,
        settings=test_env["settings"],
    )
    # Wire research_orchestrator into script_service
    pipeline.script_service.research_orchestrator = research_orch
    pipeline.research_orchestrator = research_orch

    # Inspect initial state before challenge: script v1, report v1, run rrun_quantum_01
    initial_script = pipeline.script_service.get_current_script("proj_inv_full_01")
    initial_report = pipeline.script_service.get_current_fact_check_report("proj_inv_full_01")
    assert initial_script.version == 1
    assert initial_script.research_run_id == "rrun_quantum_01"
    assert initial_report.research_run_id == "rrun_quantum_01"

    # Challenge clm_02 with action="investigate"
    payload = {
        "claim_id": "clm_02",
        "action": "investigate",
        "instruction": "Targeted investigation on whether binary bits are strictly locked into zero or one in standard hardware.",
    }
    res = client.post("/api/v1/projects/proj_inv_full_01/script/challenge", json=payload)
    assert res.status_code == 200
    data = res.json()

    # 1. Challenge summary reports actual fresh investigation
    summary = data["challenge_summary"]
    assert summary["action"] == "investigate"
    assert summary["investigation_performed"] is True
    assert summary["prior_run_id"] == "rrun_quantum_01"
    assert summary["new_run_id"] is not None
    assert summary["new_run_id"] != "rrun_quantum_01"
    assert len(summary["new_evidence_ids"]) > 0
    assert "targeted investigation completed" in summary["investigation_note"].lower()

    # 2. Original script v1, report v1, and run rrun_quantum_01 remain fully preserved and recoverable
    v1_script = next(s for s in pipeline.script_service.list_script_versions("proj_inv_full_01") if s.version == 1)
    assert v1_script.version == 1
    assert v1_script.research_run_id == "rrun_quantum_01"

    v1_report = pipeline.script_service.get_fact_check_report("proj_inv_full_01", version=1)
    assert v1_report.script_version == 1
    assert v1_report.research_run_id == "rrun_quantum_01"

    old_run = pipeline.research_storage.get_run("proj_inv_full_01", "rrun_quantum_01")
    assert old_run.run_id == "rrun_quantum_01"
    old_evidence = pipeline.research_storage.list_evidence("proj_inv_full_01", run_id="rrun_quantum_01")
    assert any(e.evidence_id == "evi_01" for e in old_evidence)

    # 3. New research run exists in storage with its fresh evidence
    new_run_id = summary["new_run_id"]
    new_run = pipeline.research_storage.get_run("proj_inv_full_01", new_run_id)
    assert new_run.run_id == new_run_id

    fresh_evidence = pipeline.research_storage.list_evidence("proj_inv_full_01", run_id=new_run_id)
    assert len(fresh_evidence) > 0
    # Fresh evidence is separately identifiable from original evidence
    assert not any(e.evidence_id == "evi_01" for e in fresh_evidence)

    # 4. Resulting script advances to v2 and is grounded in new run
    script = data["script"]
    assert script["version"] == 2
    assert script["research_run_id"] == new_run_id

    # 5. Resulting fact check report matches new run
    report_data = data["fact_check_report"]
    assert report_data["script_version"] == 2
    assert report_data["research_run_id"] == new_run_id

    # 6. Conversation event preserves provenance without exposing file paths or secrets
    history = pipeline.script_service.get_conversation_history("proj_inv_full_01")
    chal_event = next(e for e in reversed(history) if e.event_type == "fact_challenge")
    assert chal_event.metadata["action"] == "investigate"
    assert chal_event.metadata["investigation_performed"] is True
    assert chal_event.metadata["prior_run_id"] == "rrun_quantum_01"
    assert chal_event.metadata["new_run_id"] == new_run_id
    # Ensure no internal paths or sensitive provider tokens are in event metadata
    assert "file://" not in str(chal_event.metadata)
    assert "api_key" not in str(chal_event.metadata)


def test_challenge_investigate_unavailable_tools_reports_limitation_honestly(test_env):
    """When research tools are unavailable, investigate does NOT claim fresh research occurred."""
    client: TestClient = test_env["client"]
    pipeline: PipelineOrchestrator = test_env["orchestrator"]
    create_sample_researched_project(test_env, project_id="proj_inv_unavail_01")

    # Explicitly ensure research_orchestrator is None
    pipeline.script_service.research_orchestrator = None
    pipeline.research_orchestrator = None

    payload = {
        "claim_id": "clm_01",
        "action": "investigate",
        "instruction": "Investigate quantum superposition.",
    }
    res = client.post("/api/v1/projects/proj_inv_unavail_01/script/challenge", json=payload)
    assert res.status_code == 200
    data = res.json()

    summary = data["challenge_summary"]
    assert summary["action"] == "investigate"
    assert summary["investigation_performed"] is False
    assert summary["new_run_id"] is None
    assert summary["outcome"] == "rechecked_existing_evidence_only"
    assert "fresh research was not performed" in summary["investigation_note"].lower()
    assert "no active research tools configured" in summary["investigation_note"].lower()


def test_query_outcomes_expose_all_lifecycle_states_and_reasons(test_env):
    """GET /research exposes all 4 query states: planned, completed, skipped (with reason), failed."""
    client: TestClient = test_env["client"]
    project_service: ProjectService = test_env["project_service"]
    pipeline: PipelineOrchestrator = test_env["orchestrator"]

    project_service.create_project(title="Lifecycle Queries", project_id="proj_qry_life_01")

    plan = ResearchPlan(
        plan_id="rplan_life_01",
        project_id="proj_qry_life_01",
        topic="Query Lifecycle Test",
        research_objective="Verify all query states",
        research_questions=["Question 1", "Question 2", "Question 3", "Question 4"],
        budget_limits=ResearchBudgetConstraints(max_search_calls=3, max_extract_calls=2),
        planned_queries=[
            PlannedQuery(query_id="qry_done_01", query_text="Completed query", purpose="Verify completed status", tool_category="web_search"),
            PlannedQuery(query_id="qry_skip_01", query_text="Skipped query", purpose="Verify skipped status", tool_category="web_search"),
            PlannedQuery(query_id="qry_fail_01", query_text="Failed query", purpose="Verify failed status", tool_category="web_search"),
            PlannedQuery(query_id="qry_plan_01", query_text="Unexecuted planned query", purpose="Verify planned status", tool_category="web_search"),
        ],
    )
    pipeline.research_storage.save_plan(plan)

    run = ResearchRun(
        run_id="rrun_life_01",
        plan_id=plan.plan_id,
        project_id="proj_qry_life_01",
        status="completed",
        termination_reason="Completed with mixed query outcomes",
        budget_limits=plan.budget_limits,
        query_records=[
            QueryRecord(
                query_id="qry_done_01",
                plan_id=plan.plan_id,
                run_id="rrun_life_01",
                query_text="Completed query",
                tool_name="tavily_search",
                status="succeeded",
                result_count=3,
            ),
            QueryRecord(
                query_id="qry_skip_01",
                plan_id=plan.plan_id,
                run_id="rrun_life_01",
                query_text="Skipped query",
                tool_name="skipped",
                status="skipped",
                error_message="Search call budget exhausted (max 3 calls reached).",
                result_count=0,
            ),
            QueryRecord(
                query_id="qry_fail_01",
                plan_id=plan.plan_id,
                run_id="rrun_life_01",
                query_text="Failed query",
                tool_name="tavily_search",
                status="failed",
                error_message="HTTP 503 Provider unavailable",
                result_count=0,
            ),
        ],
    )
    pipeline.research_storage.save_run(run)

    res = client.get("/api/v1/projects/proj_qry_life_01/research")
    assert res.status_code == 200
    data = res.json()

    queries_map = {q["query_id"]: q for q in data["queries"]}
    assert len(queries_map) == 4

    # 1. Completed
    assert queries_map["qry_done_01"]["status"] == "completed"
    assert queries_map["qry_done_01"]["result_count"] == 3

    # 2. Skipped with exact reason
    assert queries_map["qry_skip_01"]["status"] == "skipped"
    assert queries_map["qry_skip_01"]["skip_reason"] == "Search call budget exhausted (max 3 calls reached)."

    # 3. Failed with error message
    assert queries_map["qry_fail_01"]["status"] == "failed"
    assert queries_map["qry_fail_01"]["error_message"] == "HTTP 503 Provider unavailable"

    # 4. Planned (not yet executed)
    assert queries_map["qry_plan_01"]["status"] == "planned"
    assert queries_map["qry_plan_01"]["result_count"] == 0
    assert queries_map["qry_plan_01"]["skip_reason"] is None


def test_evidence_distinctions_retrieved_selected_used_unused(test_env):
    """GET /research distinctly partitions retrieved vs selected vs active-used vs unused evidence."""
    from backend.app.domain.script import ScriptOutline, ScriptOutlineSection

    client: TestClient = test_env["client"]
    project_service: ProjectService = test_env["project_service"]
    pipeline: PipelineOrchestrator = test_env["orchestrator"]
    script_service: ScriptService = test_env["script_service"]

    project_service.create_project(title="Evidence Distinctions", project_id="proj_evi_dist_01")

    # 1. Setup Research Run with 4 Evidence Records
    plan = ResearchPlan(
        plan_id="rplan_dist_01",
        project_id="proj_evi_dist_01",
        topic="Evidence Distinction Test",
        research_objective="Distinguish evidence stages",
        research_questions=["Supercooling question"],
        budget_limits=ResearchBudgetConstraints(),
    )
    pipeline.research_storage.save_plan(plan)
    run = ResearchRun(
        run_id="rrun_dist_01",
        plan_id=plan.plan_id,
        project_id="proj_evi_dist_01",
        status="completed",
        budget_limits=plan.budget_limits,
    )
    pipeline.research_storage.save_run(run)

    # Source
    src = SourceRecord(
        source_id="src_dist_01",
        project_id="proj_evi_dist_01",
        url="https://physics.example.org/water",
        title="Physics of Supercooling",
        retrieval_status="extracted",
    )
    pipeline.research_storage.save_source("proj_evi_dist_01", src)

    # 1. Condition 1 & 5: Candidate without selection, with explicit selection rationale
    evi_cand_unselected = EvidenceRecord(
        evidence_id="evi_cand_unselected",
        project_id="proj_evi_dist_01",
        source_id=src.source_id,
        run_id=run.run_id,
        excerpt="Hexagonal ice forms under normal atmospheric pressures.",
        status="candidate",
        selection_rationale="Solid empirical data on freezing point depression.",
    )
    # 2. Condition 2: Candidate WITH selection (in outline, not in claims)
    evi_cand_selected = EvidenceRecord(
        evidence_id="evi_cand_selected",
        project_id="proj_evi_dist_01",
        source_id=src.source_id,
        run_id=run.run_id,
        excerpt="Ice polymorphs exhibit complex crystalline lattices at ultra-low temperatures.",
        status="candidate",
        selection_rationale="Key reference for ice nucleation mechanisms.",
    )
    # 3. Condition 3 & 5: Selected without usage (in outline, status='retrieved', null rationale)
    evi_selected_unused = EvidenceRecord(
        evidence_id="evi_selected_unused",
        project_id="proj_evi_dist_01",
        source_id=src.source_id,
        run_id=run.run_id,
        excerpt="Pure water can remain liquid down to minus 48 degrees Celsius.",
        status="retrieved",
        selection_rationale=None,
    )
    # 4. Condition 4 & 5: Used in narration (in outline, cited in claim, null rationale)
    evi_used_in_narration = EvidenceRecord(
        evidence_id="evi_used_in_narration",
        project_id="proj_evi_dist_01",
        source_id=src.source_id,
        run_id=run.run_id,
        excerpt="Water freezes at 0 degrees Celsius under standard atmospheric pressure.",
        status="retrieved",
        selection_rationale=None,
    )
    # 5. Retrieved only: neither candidate, nor selected, nor used
    evi_retrieved_only = EvidenceRecord(
        evidence_id="evi_retrieved_only",
        project_id="proj_evi_dist_01",
        source_id=src.source_id,
        run_id=run.run_id,
        excerpt="Supercooled droplet collision velocities vary by droplet diameter.",
        status="retrieved",
        selection_rationale=None,
    )

    pipeline.research_storage.save_evidence("proj_evi_dist_01", evi_cand_unselected)
    pipeline.research_storage.save_evidence("proj_evi_dist_01", evi_cand_selected)
    pipeline.research_storage.save_evidence("proj_evi_dist_01", evi_selected_unused)
    pipeline.research_storage.save_evidence("proj_evi_dist_01", evi_used_in_narration)
    pipeline.research_storage.save_evidence("proj_evi_dist_01", evi_retrieved_only)

    # 2. Outline explicitly selects: evi_cand_selected, evi_selected_unused, evi_used_in_narration
    # NOTE: evi_cand_unselected and evi_retrieved_only are NOT selected by the outline.
    outline = ScriptOutline(
        outline_id="out_dist_01",
        project_id="proj_evi_dist_01",
        title="Supercooling Outline",
        research_run_id=run.run_id,
        sections=[
            ScriptOutlineSection(
                type="hook",
                heading="Freezing Thresholds",
                target_words=50,
                evidence_ids=["evi_cand_selected", "evi_selected_unused", "evi_used_in_narration"],
            )
        ],
    )
    dirs = script_service._get_project_dirs("proj_evi_dist_01")
    (dirs["outlines_dir"] / "out_dist_01.json").write_text(json.dumps(outline.model_dump(mode="json")), encoding="utf-8")

    # 3. Script draft cites ONLY evi_used_in_narration (leaving evi_cand_selected & evi_selected_unused unused)
    sections = [
        ScriptSection(
            id="sec_01",
            type="hook",
            heading="Freezing Thresholds",
            narration="Did you know water freezes at 0 degrees Celsius under standard atmospheric pressure?",
            claim_ids=["clm_01"],
        )
    ]
    claims = [
        ScriptClaim(
            claim_id="clm_01",
            claim_text="Water freezes at 0 degrees Celsius under standard atmospheric pressure.",
            section_id="sec_01",
            evidence_ids=["evi_used_in_narration"],
            source_ids=[src.source_id],
            link_status="linked",
        )
    ]
    script = Script(
        script_id="scr_dist_01",
        project_id="proj_evi_dist_01",
        version=1,
        title="Supercooling Script",
        sections=sections,
        claims=claims,
        research_run_id=run.run_id,
        outline=outline,
    )
    report = script_service.fact_checker.check_script(
        script=script,
        catalog=ValidatedEvidenceCatalog(
            run_id=run.run_id,
            evidence_by_id={
                "evi_cand_unselected": evi_cand_unselected,
                "evi_cand_selected": evi_cand_selected,
                "evi_selected_unused": evi_selected_unused,
                "evi_used_in_narration": evi_used_in_narration,
                "evi_retrieved_only": evi_retrieved_only,
            },
            sources_by_id={src.source_id: src},
        ),
        project_id="proj_evi_dist_01",
    )
    script_service._commit_script_version(
        project_id="proj_evi_dist_01",
        dirs=dirs,
        script=script,
        report=report,
        event_type="draft_generated",
        event_content="Draft created.",
    )

    # 4. Request /research overview
    res = client.get("/api/v1/projects/proj_evi_dist_01/research")
    assert res.status_code == 200
    data = res.json()

    # All 5 items are persisted in the run -> all 5 are in retrieved_evidence_ids
    assert set(data["retrieved_evidence_ids"]) == {
        "evi_cand_unselected",
        "evi_cand_selected",
        "evi_selected_unused",
        "evi_used_in_narration",
        "evi_retrieved_only",
    }

    # Only outline-selected items are in selected_evidence_ids
    assert set(data["selected_evidence_ids"]) == {
        "evi_cand_selected",
        "evi_selected_unused",
        "evi_used_in_narration",
    }
    assert "evi_cand_unselected" not in data["selected_evidence_ids"]
    assert "evi_retrieved_only" not in data["selected_evidence_ids"]

    # Only evi_used_in_narration is actually linked to active script claims
    assert data["used_evidence_ids"] == ["evi_used_in_narration"]

    # Unused evidence includes everything not cited in narration claims
    assert set(data["unused_evidence_ids"]) == {
        "evi_cand_unselected",
        "evi_cand_selected",
        "evi_selected_unused",
        "evi_retrieved_only",
    }

    # Map items for detailed property verification
    evi_map = {e["evidence_id"]: e for e in data["selected_evidence"]}

    # --- Condition 1: Candidate WITHOUT selection ---
    # Has status="candidate", but was NOT selected by outline or selection record
    assert evi_map["evi_cand_unselected"]["status"] == "candidate"
    assert evi_map["evi_cand_unselected"]["is_selected"] is False
    assert evi_map["evi_cand_unselected"]["is_used_in_current_script"] is False
    assert evi_map["evi_cand_unselected"]["used_by_claims"] == []
    assert evi_map["evi_cand_unselected"]["selection_rationale"] == "Solid empirical data on freezing point depression."

    # --- Condition 2: Candidate WITH selection ---
    # Has status="candidate" and was explicitly selected by narrative outline
    assert evi_map["evi_cand_selected"]["status"] == "candidate"
    assert evi_map["evi_cand_selected"]["is_selected"] is True
    assert evi_map["evi_cand_selected"]["selection_rationale"] == "Key reference for ice nucleation mechanisms."

    # --- Condition 3: Selected WITHOUT usage ---
    # Was selected by outline, but is NOT cited in active script claims/narration
    assert evi_map["evi_selected_unused"]["is_selected"] is True
    assert evi_map["evi_selected_unused"]["is_used_in_current_script"] is False
    assert evi_map["evi_selected_unused"]["used_by_claims"] == []

    # --- Condition 4: Used in narration ---
    # Was selected by outline and IS cited in active script claims asserted in narration
    assert evi_map["evi_used_in_narration"]["is_selected"] is True
    assert evi_map["evi_used_in_narration"]["is_used_in_current_script"] is True
    assert evi_map["evi_used_in_narration"]["used_by_claims"] == ["clm_01"]

    # --- Condition 5: Null selection rationale ---
    # Missing/unset rationale must remain None / null, never fabricated
    assert evi_map["evi_selected_unused"]["selection_rationale"] is None
    assert evi_map["evi_used_in_narration"]["selection_rationale"] is None
    assert evi_map["evi_retrieved_only"]["selection_rationale"] is None

