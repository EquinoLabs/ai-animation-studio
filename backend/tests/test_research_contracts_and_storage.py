"""Automated offline tests for research domain contracts, project artifact storage, and lifecycle state."""

from datetime import datetime, timezone
import json
from pathlib import Path
import pytest

from backend.app.domain.research import (
    EvidenceRecord,
    PlannedQuery,
    QueryRecord,
    ResearchBudgetConstraints,
    ResearchPlan,
    ResearchRun,
    SourceRecord,
)
from backend.app.services.project.service import (
    ProjectNotFoundError,
    ProjectService,
)
from backend.app.services.research.storage import (
    ResearchEvidenceNotFoundError,
    ResearchPlanNotFoundError,
    ResearchReferenceIntegrityError,
    ResearchRunNotFoundError,
    ResearchSecurityError,
    ResearchSourceNotFoundError,
    ResearchStateTransitionError,
    ResearchStorageService,
    ResearchValidationError,
)


@pytest.fixture
def project_environment(tmp_path: Path):
    """Fixture providing an initialized project in a temporary workspace."""
    projects_root = tmp_path / "projects"
    project_service = ProjectService(projects_root=projects_root)
    storage_service = ResearchStorageService(projects_root=projects_root)

    proj = project_service.create_project(title="History of Currency", project_id="proj_currency123")
    return {
        "projects_root": projects_root,
        "project_service": project_service,
        "storage": storage_service,
        "project": proj,
        "project_id": proj.metadata.project_id,
    }


# ---------------------------------------------------------------------------
# 1. Contract Validation and Inputs
# ---------------------------------------------------------------------------

def test_research_plan_valid_and_invalid_inputs() -> None:
    # Valid plan
    plan = ResearchPlan(
        plan_id="rplan_test1",
        project_id="proj_currency123",
        topic="How Did Humans Invent Currency?",
        research_objective="Trace barter frictions to commodity money and stamped coinage.",
        research_questions=["What was the double coincidence of wants?", "Where were Lydian coins minted?"],
        planned_queries=[
            PlannedQuery(
                query_id="qry_1",
                query_text="barter double coincidence of wants history",
                purpose="Understand economic frictions before currency",
            )
        ],
    )
    assert plan.plan_id == "rplan_test1"
    assert len(plan.planned_queries) == 1
    assert plan.schema_version == "1.0.0"

    # Invalid plan_id format
    with pytest.raises(ValueError, match="Invalid identifier"):
        ResearchPlan(
            plan_id="rplan/../../evil",
            project_id="proj_currency123",
            topic="Valid Topic",
            research_objective="Valid Objective",
            research_questions=["Question 1"],
        )

    # Empty research questions
    with pytest.raises(ValueError, match="at least one non-empty research question"):
        ResearchPlan(
            plan_id="rplan_valid",
            project_id="proj_currency123",
            topic="Valid Topic",
            research_objective="Valid Objective",
            research_questions=["   ", ""],
        )


def test_source_record_url_validation() -> None:
    # Valid source
    source = SourceRecord(
        source_id="src_1",
        project_id="proj_currency123",
        url="https://example.org/lydia-coinage",
        title="Lydian Electrum Coinage",
    )
    assert source.url == "https://example.org/lydia-coinage"
    assert source.retrieval_status == "discovered"

    # Invalid scheme (ftp)
    with pytest.raises(ValueError, match="Only HTTP and HTTPS"):
        SourceRecord(
            source_id="src_2",
            project_id="proj_currency123",
            url="ftp://example.org/file",
        )

    # Embedded credentials rejected
    with pytest.raises(ValueError, match="embedded username or password"):
        SourceRecord(
            source_id="src_3",
            project_id="proj_currency123",
            url="https://user:pass@example.org/resource",
        )


def test_evidence_and_query_record_validation() -> None:
    # Valid evidence record
    evidence = EvidenceRecord(
        evidence_id="evi_1",
        project_id="proj_currency123",
        source_id="src_1",
        excerpt="The earliest stamped coins were minted in Lydia around 600 BCE using electrum.",
        locator="Section 3, Paragraph 2",
        status="retrieved",
    )
    assert evidence.status == "retrieved"

    # Empty excerpt rejected
    with pytest.raises(ValueError):
        EvidenceRecord(
            evidence_id="evi_2",
            project_id="proj_currency123",
            source_id="src_1",
            excerpt="",
        )


# ---------------------------------------------------------------------------
# 2. Serialization and Deserialization Round-Trip
# ---------------------------------------------------------------------------

def test_contract_serialization_round_trip() -> None:
    plan = ResearchPlan(
        plan_id="rplan_roundtrip",
        project_id="proj_currency123",
        topic="Origins of Money",
        research_objective="Explain transition to coinage",
        research_questions=["Why metal?"],
        budget_limits=ResearchBudgetConstraints(max_search_calls=5, deadline_seconds=90.0),
    )
    serialized = plan.model_dump_json()
    loaded = ResearchPlan.model_validate_json(serialized)
    assert loaded == plan
    assert loaded.budget_limits.max_search_calls == 5
    assert loaded.budget_limits.deadline_seconds == 90.0


# ---------------------------------------------------------------------------
# 3. Plan Persistence & Project Isolation
# ---------------------------------------------------------------------------

def test_plan_persistence_and_isolation(project_environment) -> None:
    storage = project_environment["storage"]
    pid = project_environment["project_id"]

    plan = ResearchPlan(
        plan_id="rplan_persisted1",
        project_id=pid,
        topic="Mesopotamian Grain Currency",
        research_objective="Examine shekel grain accounting",
        research_questions=["How did barley function as money?"],
    )

    saved_path = storage.save_plan(plan)
    assert saved_path.exists()
    assert saved_path.name == "rplan_persisted1.json"

    loaded = storage.get_plan(pid, "rplan_persisted1")
    assert loaded.plan_id == plan.plan_id
    assert loaded.topic == plan.topic

    # Attempting to load non-existent plan raises ResearchPlanNotFoundError
    with pytest.raises(ResearchPlanNotFoundError):
        storage.get_plan(pid, "rplan_nonexistent")

    # Project isolation: reading with another project ID raises ResearchPlanNotFoundError or ResearchSecurityError
    proj2 = project_environment["project_service"].create_project("Project 2", "proj_other456")
    with pytest.raises(ResearchPlanNotFoundError):
        storage.get_plan(proj2.metadata.project_id, "rplan_persisted1")


# ---------------------------------------------------------------------------
# 4. Research Run State Transitions & Interruption Recovery
# ---------------------------------------------------------------------------

def test_research_run_lifecycle_and_transitions(project_environment) -> None:
    storage = project_environment["storage"]
    pid = project_environment["project_id"]

    plan = ResearchPlan(
        plan_id="rplan_lifecycle",
        project_id=pid,
        topic="Gold Standard",
        research_objective="Trace transition to fiat",
        research_questions=["What happened at Bretton Woods?"],
    )
    storage.save_plan(plan)

    # 1. Create run (starts in pending)
    run = storage.create_run(pid, plan.plan_id, run_id="rrun_testrun1")
    assert run.status == "pending"
    assert run.completed_at is None

    # 2. Transition pending -> running (valid)
    running_run = storage.transition_run_status(pid, run.run_id, "running")
    assert running_run.status == "running"

    # 3. Transition running -> completed (valid)
    completed_run = storage.transition_run_status(
        pid,
        run.run_id,
        "completed",
        termination_reason="Sufficient evidence gathered",
    )
    assert completed_run.status == "completed"
    assert completed_run.completed_at is not None
    assert completed_run.termination_reason == "Sufficient evidence gathered"

    # 4. Invalid transition from completed (terminal state)
    with pytest.raises(ResearchStateTransitionError):
        storage.transition_run_status(pid, run.run_id, "running")

    with pytest.raises(ResearchStateTransitionError):
        storage.transition_run_status(pid, run.run_id, "failed")


def test_research_run_interruption_recovery(project_environment) -> None:
    """A run interrupted while 'running' must be reconciled to 'failed' rather than staying stuck or succeeding."""
    storage = project_environment["storage"]
    pid = project_environment["project_id"]

    plan = ResearchPlan(
        plan_id="rplan_interrupt",
        project_id=pid,
        topic="Ancient Banking",
        research_objective="Examine temple treasury lending",
        research_questions=["How did temples record loans?"],
    )
    storage.save_plan(plan)

    run = storage.create_run(pid, plan.plan_id, run_id="rrun_interrupted")
    storage.transition_run_status(pid, run.run_id, "running")

    # Simulate process crash: re-instantiate storage service
    new_storage = ResearchStorageService(projects_root=project_environment["projects_root"])
    reconciled = new_storage.get_run(pid, "rrun_interrupted")

    # Reconciled run must be marked failed
    assert reconciled.status == "failed"
    assert reconciled.completed_at is not None
    assert "interrupted" in (reconciled.termination_reason or "").lower()


# ---------------------------------------------------------------------------
# 5. Source Records, Content Artifacts & Deduplication
# ---------------------------------------------------------------------------

def test_source_persistence_and_content_artifact(project_environment) -> None:
    storage = project_environment["storage"]
    pid = project_environment["project_id"]

    source = SourceRecord(
        source_id="src_lydia_coins",
        project_id=pid,
        url="https://example.org/articles/lydia-coins",
        title="Electrum and the Kings of Lydia",
        publisher="Ancient History Encyclopedia",
        source_type="encyclopedic",
    )

    markdown_text = "# Electrum Coinage\n\nKing Alyattes of Lydia minted electrum coins stamped with a lion's head."
    saved_source = storage.save_source(pid, source, content=markdown_text)

    assert saved_source.content_artifact_ref == "content/src_lydia_coins.md"
    assert saved_source.content_hash is not None
    assert saved_source.char_count == len(markdown_text)
    assert saved_source.retrieval_status == "extracted"

    # Verify content retrieval
    retrieved_content = storage.get_source_content(pid, "src_lydia_coins")
    assert retrieved_content == markdown_text

    # Verify source listing
    sources_list = storage.list_sources(pid)
    assert len(sources_list) == 1
    assert sources_list[0].source_id == "src_lydia_coins"


# ---------------------------------------------------------------------------
# 6. Evidence Records and Reference Integrity
# ---------------------------------------------------------------------------

def test_evidence_reference_integrity_enforcement(project_environment) -> None:
    storage = project_environment["storage"]
    pid = project_environment["project_id"]

    # 1. Attempting to save evidence for a nonexistent source must fail
    orphan_evidence = EvidenceRecord(
        evidence_id="evi_orphan",
        project_id=pid,
        source_id="src_does_not_exist",
        excerpt="An unlinked claim.",
    )
    with pytest.raises(ResearchReferenceIntegrityError) as exc_info:
        storage.save_evidence(pid, orphan_evidence)
    assert "referenced source 'src_does_not_exist' does not exist" in str(exc_info.value)

    # 2. Create valid source, then evidence saves successfully
    source = SourceRecord(
        source_id="src_valid_ref",
        project_id=pid,
        url="https://example.org/valid",
        title="Valid Source",
    )
    storage.save_source(pid, source)

    valid_evidence = EvidenceRecord(
        evidence_id="evi_valid",
        project_id=pid,
        source_id="src_valid_ref",
        excerpt="Documented fact with integrity.",
    )
    saved_evidence = storage.save_evidence(pid, valid_evidence)
    assert saved_evidence.evidence_id == "evi_valid"

    # Retrieve and list evidence
    loaded_evidence = storage.get_evidence(pid, "evi_valid")
    assert loaded_evidence.excerpt == "Documented fact with integrity."

    evidence_list = storage.list_evidence(pid, source_id="src_valid_ref")
    assert len(evidence_list) == 1


# ---------------------------------------------------------------------------
# 7. Query Execution Logging and Secret Sanitization
# ---------------------------------------------------------------------------

def test_query_record_logging_and_secret_sanitization(project_environment) -> None:
    storage = project_environment["storage"]
    pid = project_environment["project_id"]

    plan = ResearchPlan(
        plan_id="rplan_queries",
        project_id=pid,
        topic="Paper Money",
        research_objective="Trace Song Dynasty Jiaozi",
        research_questions=["When did paper money start?"],
    )
    storage.save_plan(plan)
    run = storage.create_run(pid, plan.plan_id, run_id="rrun_querytest")

    secret_key = "tvly-super-secret-key-999"
    # Query with an error message that accidentally contains an API key
    query = QueryRecord(
        query_id="qry_leak_test",
        plan_id=plan.plan_id,
        run_id=run.run_id,
        query_text="song dynasty jiaozi banknotes",
        tool_name="tavily_search",
        status="failed",
        error_message=f"HTTP 500 error connecting to provider with key {secret_key}",
    )

    recorded = storage.record_query_execution(pid, run.run_id, query)
    assert secret_key not in (recorded.error_message or "")
    assert "[REDACTED_API_KEY]" in (recorded.error_message or "")

    # Verify persisted run contains sanitized query
    persisted_run = storage.get_run(pid, run.run_id)
    assert len(persisted_run.query_records) == 1
    assert secret_key not in (persisted_run.query_records[0].error_message or "")
    assert "[REDACTED_API_KEY]" in (persisted_run.query_records[0].error_message or "")


# ---------------------------------------------------------------------------
# 8. Path Traversal & Security Protections
# ---------------------------------------------------------------------------

def test_path_traversal_and_malicious_ids_blocked(project_environment) -> None:
    storage = project_environment["storage"]
    pid = project_environment["project_id"]

    malicious_ids = [
        "../../outside",
        "..\\..\\outside",
        "proj/escaped",
        "rplan_../../etc/passwd",
        "src_..\\traversal",
    ]

    for bad_id in malicious_ids:
        with pytest.raises((ResearchSecurityError, ProjectNotFoundError)):
            storage._resolve_project_dir(bad_id)

    # Malformed source ID in get_source
    with pytest.raises(ResearchSecurityError):
        storage.get_source(pid, "../../bad_id")

    # Malformed evidence ID in get_evidence
    with pytest.raises(ResearchSecurityError):
        storage.get_evidence(pid, "../evil_evidence")


# ---------------------------------------------------------------------------
# 9. Atomic Persistence and Corrupted Data Handling
# ---------------------------------------------------------------------------

def test_corrupted_json_handling(project_environment) -> None:
    storage = project_environment["storage"]
    pid = project_environment["project_id"]
    research_dir = storage._resolve_research_dir(pid)

    # Write corrupt JSON into plans directory
    corrupt_plan_file = research_dir / "plans" / "rplan_corrupt.json"
    corrupt_plan_file.write_text("{ incomplete json: ", encoding="utf-8")

    with pytest.raises(ResearchValidationError) as exc_info:
        storage.get_plan(pid, "rplan_corrupt")
    assert "Corrupted research plan 'rplan_corrupt'" in str(exc_info.value)


def test_corrupted_sources_index_raises_and_preserves_file(project_environment) -> None:
    storage = project_environment["storage"]
    pid = project_environment["project_id"]
    research_dir = storage._resolve_research_dir(pid)

    corrupt_payload = '{"incomplete_sources": [malformed json'
    sources_file = research_dir / "sources" / "sources.json"
    sources_file.write_text(corrupt_payload, encoding="utf-8")

    # 1. Loading corrupted sources map raises ResearchValidationError
    with pytest.raises(ResearchValidationError) as exc1:
        storage.list_sources(pid)
    assert "Corrupted sources.json" in str(exc1.value)

    with pytest.raises(ResearchValidationError) as exc2:
        storage.get_source(pid, "src_any")
    assert "Corrupted sources.json" in str(exc2.value)

    # 2. Attempting to save a new source must reject and NOT overwrite the corrupt file with an empty map
    new_source = SourceRecord(
        source_id="src_new",
        project_id=pid,
        url="https://example.org/new",
        title="New Source",
    )
    with pytest.raises(ResearchValidationError):
        storage.save_source(pid, new_source)

    # 3. Assert original corrupted file content remains unchanged
    assert sources_file.read_text(encoding="utf-8") == corrupt_payload


def test_corrupted_evidence_index_raises_and_preserves_file(project_environment) -> None:
    storage = project_environment["storage"]
    pid = project_environment["project_id"]
    research_dir = storage._resolve_research_dir(pid)

    corrupt_payload = '{"evidence_map": {"truncated": '
    evidence_file = research_dir / "evidence" / "evidence.json"
    evidence_file.write_text(corrupt_payload, encoding="utf-8")

    # 1. Loading corrupted evidence map raises ResearchValidationError
    with pytest.raises(ResearchValidationError) as exc1:
        storage.list_evidence(pid)
    assert "Corrupted evidence.json" in str(exc1.value)

    with pytest.raises(ResearchValidationError) as exc2:
        storage.get_evidence(pid, "evi_any")
    assert "Corrupted evidence.json" in str(exc2.value)

    # Create a valid source record first so reference integrity passes
    # Note: to bypass the corrupted sources file for this test, save a dummy source after fixing sources
    valid_source = SourceRecord(
        source_id="src_dummy",
        project_id=pid,
        url="https://example.org/dummy",
        title="Dummy Source",
    )
    storage.save_source(pid, valid_source)

    # 2. Attempting to save new evidence must reject and NOT overwrite corrupt evidence.json
    new_evidence = EvidenceRecord(
        evidence_id="evi_new",
        project_id=pid,
        source_id="src_dummy",
        excerpt="Valid excerpt from dummy source.",
    )
    with pytest.raises(ResearchValidationError):
        storage.save_evidence(pid, new_evidence)

    # 3. Assert original corrupted file content remains unchanged
    assert evidence_file.read_text(encoding="utf-8") == corrupt_payload


def test_unsupported_schema_version_rejected_in_plan_and_run(project_environment) -> None:
    storage = project_environment["storage"]
    pid = project_environment["project_id"]
    research_dir = storage._resolve_research_dir(pid)

    # 1. In-memory validation rejects unsupported schema_version
    with pytest.raises(ValueError) as exc1:
        ResearchPlan(
            plan_id="rplan_future",
            project_id=pid,
            topic="Future Money",
            research_objective="Test future schema",
            research_questions=["Question?"],
            schema_version="2.0.0",
        )
    assert "Unsupported schema_version '2.0.0'" in str(exc1.value)

    with pytest.raises(ValueError) as exc2:
        ResearchRun(
            run_id="rrun_future",
            plan_id="rplan_dummy",
            project_id=pid,
            schema_version="99.0.0",
        )
    assert "Unsupported schema_version '99.0.0'" in str(exc2.value)

    # 2. Persisted artifact on disk with unsupported schema_version raises ResearchValidationError on read
    plan_file = research_dir / "plans" / "rplan_unsupported.json"
    plan_file.write_text(
        json.dumps({
            "plan_id": "rplan_unsupported",
            "project_id": pid,
            "topic": "Future Money",
            "research_objective": "Test future schema",
            "research_questions": ["Question?"],
            "planned_queries": [],
            "approved_tool_categories": ["web_search"],
            "budget_limits": {
                "max_search_calls": 3,
                "max_extract_calls": 3,
                "call_timeout_seconds": 15.0,
                "deadline_seconds": 60.0,
                "max_page_chars": 15000,
                "max_total_chars": 45000,
            },
            "created_at": "2026-10-29T10:00:00Z",
            "schema_version": "2.0.0",
        }),
        encoding="utf-8",
    )

    with pytest.raises(ResearchValidationError) as exc3:
        storage.get_plan(pid, "rplan_unsupported")
    assert "Unsupported schema_version '2.0.0'" in str(exc3.value)
