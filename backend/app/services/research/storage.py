"""Project-local artifact storage for research plans, runs, sources, and evidence."""

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Dict, List, Optional
import uuid

from backend.app.config import Settings, get_settings
from backend.app.domain.research import (
    EvidenceRecord,
    ID_REGEX,
    QueryRecord,
    ResearchBudgetConstraints,
    ResearchPlan,
    ResearchRun,
    ResearchRunStatus,
    SourceRecord,
    utc_now,
)
from backend.app.services.project.service import (
    PROJECT_ID_REGEX,
    ProjectNotFoundError,
    ProjectSecurityError,
    ProjectService,
)
from backend.app.services.research.tools.base import sanitize_secret


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ResearchStorageError(Exception):
    """Base exception for research storage and repository operations."""
    pass


class ResearchPlanNotFoundError(ResearchStorageError):
    """Raised when a requested research plan does not exist."""
    pass


class ResearchRunNotFoundError(ResearchStorageError):
    """Raised when a requested research run does not exist."""
    pass


class ResearchSourceNotFoundError(ResearchStorageError):
    """Raised when a requested source record does not exist."""
    pass


class ResearchEvidenceNotFoundError(ResearchStorageError):
    """Raised when a requested evidence record does not exist."""
    pass


class ResearchStateTransitionError(ResearchStorageError):
    """Raised when an invalid research run state transition is attempted."""
    pass


class ResearchValidationError(ResearchStorageError):
    """Raised when research data or schema validation fails."""
    pass


class ResearchSecurityError(ResearchStorageError):
    """Raised when a path traversal, symlink escape, or project isolation violation occurs."""
    pass


class ResearchReferenceIntegrityError(ResearchStorageError):
    """Raised when an entity references a nonexistent source, query, or plan."""
    pass


# ---------------------------------------------------------------------------
# State Machine Definitions
# ---------------------------------------------------------------------------

VALID_RUN_TRANSITIONS: Dict[ResearchRunStatus, set[ResearchRunStatus]] = {
    "pending": {"running", "cancelled"},
    "running": {"completed", "failed", "cancelled"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}


# ---------------------------------------------------------------------------
# Research Storage Service
# ---------------------------------------------------------------------------

class ResearchStorageService:
    """Manages project-local persistence of research plans, runs, sources, and evidence.

    Concurrency Contract:
    - Assumes sequential, single-writer access per project, aligning with the Script Builder's
      single-user, synchronous execution architecture.
    - Uses atomic filesystem replacement (via temporary file writes followed by os.replace)
      which guarantees all-or-nothing file updates and prevents partially written or torn reads.
    - Atomic replacement does NOT prevent concurrent read-modify-write lost updates: if multiple
      processes write to the same project concurrently, the last write will atomically overwrite
      prior writes. Multi-process concurrency locking is deferred.
    """

    def __init__(
        self,
        projects_root: Optional[Path] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self.projects_root = Path(projects_root or self._settings.PROJECTS_DIR).resolve()
        self.projects_root.mkdir(parents=True, exist_ok=True)
        self._project_service = ProjectService(projects_root=self.projects_root)
        self._active_runs: set[str] = set()

    # -----------------------------------------------------------------------
    # Path & Security Resolution
    # -----------------------------------------------------------------------

    def _resolve_project_dir(self, project_id: str) -> Path:
        """Validate and resolve project root directory safely."""
        if not project_id or not PROJECT_ID_REGEX.match(project_id):
            raise ResearchSecurityError(
                f"Invalid project ID '{project_id}'. IDs must only contain alphanumeric characters, underscores, or dashes."
            )

        project_dir = (self.projects_root / project_id).resolve()
        if not project_dir.is_relative_to(self.projects_root) or project_dir.parent != self.projects_root:
            raise ResearchSecurityError(
                f"Path traversal detected: project ID '{project_id}' attempts to escape project root."
            )

        if not project_dir.exists() or not project_dir.is_dir():
            raise ProjectNotFoundError(f"Project '{project_id}' does not exist.")

        return project_dir

    def _resolve_research_dir(self, project_id: str) -> Path:
        """Resolve and initialize project's research/ subdirectory structure."""
        project_dir = self._resolve_project_dir(project_id)
        research_dir = (project_dir / "research").resolve()

        if not research_dir.is_relative_to(project_dir):
            raise ResearchSecurityError(
                f"Path traversal detected in research directory for project '{project_id}'."
            )

        # Initialize canonical subdirectories
        (research_dir / "plans").mkdir(parents=True, exist_ok=True)
        (research_dir / "runs").mkdir(parents=True, exist_ok=True)
        (research_dir / "sources").mkdir(parents=True, exist_ok=True)
        (research_dir / "evidence").mkdir(parents=True, exist_ok=True)
        (research_dir / "content").mkdir(parents=True, exist_ok=True)

        return research_dir

    def _atomic_write_json(self, target_path: Path, data: Any) -> None:
        """Atomically persist a JSON serializable object using tempfile replacement."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path_str = tempfile.mkstemp(
            prefix=f".{target_path.name}_",
            suffix=".tmp",
            dir=str(target_path.parent),
        )
        try:
            with open(tmp_fd, "w", encoding="utf-8") as f:
                if isinstance(data, (dict, list)):
                    json.dump(data, f, indent=2, default=str)
                elif hasattr(data, "model_dump_json"):
                    f.write(data.model_dump_json(indent=2))
                else:
                    json.dump(data, f, indent=2, default=str)
            os.replace(tmp_path_str, str(target_path))
        except Exception:
            if os.path.exists(tmp_path_str):
                try:
                    os.remove(tmp_path_str)
                except OSError:
                    pass
            raise

    def _atomic_write_text(self, target_path: Path, text: str) -> None:
        """Atomically persist text content using tempfile replacement."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path_str = tempfile.mkstemp(
            prefix=f".{target_path.name}_",
            suffix=".tmp",
            dir=str(target_path.parent),
        )
        try:
            with open(tmp_fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp_path_str, str(target_path))
        except Exception:
            if os.path.exists(tmp_path_str):
                try:
                    os.remove(tmp_path_str)
                except OSError:
                    pass
            raise

    # -----------------------------------------------------------------------
    # Research Plan Operations
    # -----------------------------------------------------------------------

    def save_plan(self, plan: ResearchPlan) -> Path:
        """Persist a research plan within its project research/plans/ directory."""
        research_dir = self._resolve_research_dir(plan.project_id)
        if not ID_REGEX.match(plan.plan_id):
            raise ResearchSecurityError(f"Invalid plan ID '{plan.plan_id}'.")

        plan_file = research_dir / "plans" / f"{plan.plan_id}.json"
        self._atomic_write_json(plan_file, plan)
        return plan_file

    def get_plan(self, project_id: str, plan_id: str) -> ResearchPlan:
        """Retrieve and parse a research plan by ID."""
        if not ID_REGEX.match(plan_id):
            raise ResearchSecurityError(f"Invalid plan ID '{plan_id}'.")

        research_dir = self._resolve_research_dir(project_id)
        plan_file = research_dir / "plans" / f"{plan_id}.json"
        if not plan_file.exists() or not plan_file.is_file():
            raise ResearchPlanNotFoundError(f"Research plan '{plan_id}' not found in project '{project_id}'.")

        try:
            content = plan_file.read_text(encoding="utf-8")
            data = json.loads(content)
            plan = ResearchPlan.model_validate(data)
        except Exception as exc:
            raise ResearchValidationError(f"Corrupted research plan '{plan_id}': {exc}") from exc

        if plan.project_id != project_id:
            raise ResearchSecurityError(
                f"Project isolation violation: plan '{plan_id}' belongs to project '{plan.project_id}', not '{project_id}'."
            )

        return plan

    def list_plans(self, project_id: str) -> List[ResearchPlan]:
        """List all research plans saved within a project."""
        research_dir = self._resolve_research_dir(project_id)
        plans_dir = research_dir / "plans"
        results: List[ResearchPlan] = []
        for p in sorted(plans_dir.glob("*.json")):
            try:
                results.append(self.get_plan(project_id, p.stem))
            except (ResearchValidationError, ResearchSecurityError):
                continue
        return results

    # -----------------------------------------------------------------------
    # Research Run Operations & State Machine
    # -----------------------------------------------------------------------

    def create_run(
        self,
        project_id: str,
        plan_id: str,
        run_id: Optional[str] = None,
        budget_limits: Optional[ResearchBudgetConstraints] = None,
    ) -> ResearchRun:
        """Initialize a new research run in 'pending' status."""
        plan = self.get_plan(project_id, plan_id)
        clean_run_id = run_id.strip() if run_id else f"rrun_{uuid.uuid4().hex[:12]}"
        if not ID_REGEX.match(clean_run_id):
            raise ResearchSecurityError(f"Invalid run ID '{clean_run_id}'.")

        research_dir = self._resolve_research_dir(project_id)
        run_file = research_dir / "runs" / f"{clean_run_id}.json"
        if run_file.exists():
            raise ResearchStorageError(f"Research run '{clean_run_id}' already exists in project '{project_id}'.")

        limits = budget_limits or plan.budget_limits

        run = ResearchRun(
            run_id=clean_run_id,
            plan_id=plan.plan_id,
            project_id=project_id,
            status="pending",
            started_at=utc_now(),
            budget_limits=limits,
            artifacts={"plan": f"plans/{plan.plan_id}.json"},
        )

        self._atomic_write_json(run_file, run)
        return run

    def save_run(self, run: ResearchRun) -> Path:
        """Persist a research run record atomically."""
        research_dir = self._resolve_research_dir(run.project_id)
        if not ID_REGEX.match(run.run_id):
            raise ResearchSecurityError(f"Invalid run ID '{run.run_id}'.")

        run_file = research_dir / "runs" / f"{run.run_id}.json"
        self._atomic_write_json(run_file, run)
        return run_file

    def get_run(self, project_id: str, run_id: str) -> ResearchRun:
        """Retrieve and validate a research run, reconciling interrupted states if necessary."""
        if not ID_REGEX.match(run_id):
            raise ResearchSecurityError(f"Invalid run ID '{run_id}'.")

        research_dir = self._resolve_research_dir(project_id)
        run_file = research_dir / "runs" / f"{run_id}.json"
        if not run_file.exists() or not run_file.is_file():
            raise ResearchRunNotFoundError(f"Research run '{run_id}' not found in project '{project_id}'.")

        try:
            content = run_file.read_text(encoding="utf-8")
            data = json.loads(content)
            run = ResearchRun.model_validate(data)
        except Exception as exc:
            raise ResearchValidationError(f"Corrupted research run '{run_id}': {exc}") from exc

        if run.project_id != project_id:
            raise ResearchSecurityError(
                f"Project isolation violation: run '{run_id}' belongs to project '{run.project_id}', not '{project_id}'."
            )

        # Process Interruption Recovery: A run found persisted as 'running' on disk was aborted
        # if it is not currently an active in-flight run in this service instance.
        if run.status == "running" and run.run_id not in self._active_runs:
            reconciled = run.model_copy(
                update={
                    "status": "failed",
                    "completed_at": utc_now(),
                    "termination_reason": "Process interrupted while run was in progress.",
                }
            )
            self.save_run(reconciled)
            return reconciled

        return run

    def list_runs(self, project_id: str) -> List[ResearchRun]:
        """List all research runs for a project."""
        research_dir = self._resolve_research_dir(project_id)
        runs_dir = research_dir / "runs"
        results: List[ResearchRun] = []
        for p in sorted(runs_dir.glob("*.json")):
            try:
                results.append(self.get_run(project_id, p.stem))
            except (ResearchValidationError, ResearchSecurityError):
                continue
        return results

    def reconcile_interrupted_runs(self, project_id: str) -> List[ResearchRun]:
        """Explicitly reconcile any in-progress runs that were interrupted by a process restart."""
        research_dir = self._resolve_research_dir(project_id)
        runs_dir = research_dir / "runs"
        reconciled_runs: List[ResearchRun] = []

        for p in sorted(runs_dir.glob("*.json")):
            try:
                content = p.read_text(encoding="utf-8")
                data = json.loads(content)
                if data.get("status") == "running":
                    self._active_runs.discard(p.stem)
                    reconciled = self.get_run(project_id, p.stem)
                    reconciled_runs.append(reconciled)
            except Exception:
                continue

        return reconciled_runs

    def transition_run_status(
        self,
        project_id: str,
        run_id: str,
        to_status: ResearchRunStatus,
        termination_reason: Optional[str] = None,
    ) -> ResearchRun:
        """Enforce valid research run state transitions and persist."""
        run = self.get_run(project_id, run_id)
        current = run.status
        allowed = VALID_RUN_TRANSITIONS.get(current, set())

        if to_status not in allowed:
            raise ResearchStateTransitionError(
                f"Invalid transition from '{current}' to '{to_status}' for run '{run_id}'. "
                f"Allowed target states: {sorted(allowed) if allowed else 'none (terminal state)'}."
            )

        updates: Dict[str, Any] = {"status": to_status}
        if to_status == "running":
            self._active_runs.add(run_id)
            if run.started_at is None:
                updates["started_at"] = utc_now()
        elif to_status in ("completed", "failed", "cancelled"):
            self._active_runs.discard(run_id)
            updates["completed_at"] = utc_now()
            if termination_reason:
                updates["termination_reason"] = termination_reason

        updated_run = run.model_copy(update=updates)
        self.save_run(updated_run)
        return updated_run

    # -----------------------------------------------------------------------
    # Query Execution Logging
    # -----------------------------------------------------------------------

    def record_query_execution(
        self,
        project_id: str,
        run_id: str,
        query: QueryRecord,
    ) -> QueryRecord:
        """Record a query execution outcome into the research run record."""
        run = self.get_run(project_id, run_id)
        if query.run_id != run_id:
            raise ResearchSecurityError(
                f"Query record run_id '{query.run_id}' does not match target run '{run_id}'."
            )

        # Sanitize error message if present to protect against secret leakage
        sanitized_query = query
        if query.error_message:
            cleaned_msg = sanitize_secret(
                query.error_message,
                [self._settings.TAVILY_API_KEY, self._settings.FIRECRAWL_API_KEY],
            )
            sanitized_query = query.model_copy(update={"error_message": cleaned_msg})

        # Update or append in run query_records
        existing = [q for q in run.query_records if q.query_id != query.query_id]
        existing.append(sanitized_query)
        updated_run = run.model_copy(update={"query_records": existing})
        self.save_run(updated_run)

        return sanitized_query

    # -----------------------------------------------------------------------
    # Source Records & Content Artifacts
    # -----------------------------------------------------------------------

    def _load_sources_map(self, research_dir: Path) -> Dict[str, Any]:
        sources_file = research_dir / "sources" / "sources.json"
        if not sources_file.exists():
            return {}
        try:
            return json.loads(sources_file.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ResearchValidationError(f"Corrupted sources.json: {exc}") from exc

    def save_source(
        self,
        project_id: str,
        source: SourceRecord,
        content: Optional[str] = None,
    ) -> SourceRecord:
        """Persist source metadata in sources/sources.json and optional content in content/<id>.md."""
        if source.project_id != project_id:
            raise ResearchSecurityError(
                f"Project isolation violation: source '{source.source_id}' belongs to project '{source.project_id}', not '{project_id}'."
            )

        research_dir = self._resolve_research_dir(project_id)
        updated_source = source

        # Persist extracted content separately if provided
        if content is not None:
            content_file = research_dir / "content" / f"{source.source_id}.md"
            self._atomic_write_text(content_file, content)
            c_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            updated_source = source.model_copy(
                update={
                    "content_artifact_ref": f"content/{source.source_id}.md",
                    "content_hash": c_hash,
                    "char_count": len(content),
                    "retrieval_status": "extracted",
                }
            )

        sources_map = self._load_sources_map(research_dir)
        sources_map[updated_source.source_id] = json.loads(updated_source.model_dump_json())

        sources_file = research_dir / "sources" / "sources.json"
        self._atomic_write_json(sources_file, sources_map)

        return updated_source

    def get_source(self, project_id: str, source_id: str) -> SourceRecord:
        """Retrieve a specific source record by source ID."""
        if not ID_REGEX.match(source_id):
            raise ResearchSecurityError(f"Invalid source ID '{source_id}'.")

        research_dir = self._resolve_research_dir(project_id)
        sources_map = self._load_sources_map(research_dir)

        if source_id not in sources_map:
            raise ResearchSourceNotFoundError(f"Source '{source_id}' not found in project '{project_id}'.")

        try:
            return SourceRecord.model_validate(sources_map[source_id])
        except Exception as exc:
            raise ResearchValidationError(f"Corrupted source record '{source_id}': {exc}") from exc

    def list_sources(self, project_id: str) -> List[SourceRecord]:
        """List all sources saved for a project."""
        research_dir = self._resolve_research_dir(project_id)
        sources_map = self._load_sources_map(research_dir)
        results: List[SourceRecord] = []
        for s_id, s_data in sources_map.items():
            try:
                results.append(SourceRecord.model_validate(s_data))
            except Exception:
                continue
        return sorted(results, key=lambda s: s.retrieved_at)

    def get_source_content(self, project_id: str, source_id: str) -> Optional[str]:
        """Read persisted content markdown for a source if available."""
        source = self.get_source(project_id, source_id)
        if not source.content_artifact_ref:
            return None

        research_dir = self._resolve_research_dir(project_id)
        content_file = (research_dir / source.content_artifact_ref).resolve()
        if not content_file.is_relative_to(research_dir):
            raise ResearchSecurityError(f"Path traversal detected in content artifact reference for '{source_id}'.")

        if not content_file.exists() or not content_file.is_file():
            return None

        return content_file.read_text(encoding="utf-8")

    # -----------------------------------------------------------------------
    # Evidence Records
    # -----------------------------------------------------------------------

    def _load_evidence_map(self, research_dir: Path) -> Dict[str, Any]:
        evidence_file = research_dir / "evidence" / "evidence.json"
        if not evidence_file.exists():
            return {}
        try:
            return json.loads(evidence_file.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ResearchValidationError(f"Corrupted evidence.json: {exc}") from exc

    def save_evidence(self, project_id: str, evidence: EvidenceRecord) -> EvidenceRecord:
        """Persist an evidence excerpt with source reference integrity validation."""
        if evidence.project_id != project_id:
            raise ResearchSecurityError(
                f"Project isolation violation: evidence '{evidence.evidence_id}' belongs to project '{evidence.project_id}', not '{project_id}'."
            )

        # Enforce source reference integrity
        try:
            self.get_source(project_id, evidence.source_id)
        except ResearchSourceNotFoundError as exc:
            raise ResearchReferenceIntegrityError(
                f"Cannot save evidence '{evidence.evidence_id}': referenced source '{evidence.source_id}' does not exist in project '{project_id}'."
            ) from exc

        research_dir = self._resolve_research_dir(project_id)
        evidence_map = self._load_evidence_map(research_dir)
        evidence_map[evidence.evidence_id] = json.loads(evidence.model_dump_json())

        evidence_file = research_dir / "evidence" / "evidence.json"
        self._atomic_write_json(evidence_file, evidence_map)

        return evidence

    def get_evidence(self, project_id: str, evidence_id: str) -> EvidenceRecord:
        """Retrieve a specific evidence record."""
        if not ID_REGEX.match(evidence_id):
            raise ResearchSecurityError(f"Invalid evidence ID '{evidence_id}'.")

        research_dir = self._resolve_research_dir(project_id)
        evidence_map = self._load_evidence_map(research_dir)

        if evidence_id not in evidence_map:
            raise ResearchEvidenceNotFoundError(f"Evidence '{evidence_id}' not found in project '{project_id}'.")

        try:
            return EvidenceRecord.model_validate(evidence_map[evidence_id])
        except Exception as exc:
            raise ResearchValidationError(f"Corrupted evidence record '{evidence_id}': {exc}") from exc

    def list_evidence(
        self,
        project_id: str,
        source_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> List[EvidenceRecord]:
        """List all evidence excerpts for a project, optionally filtered by source or run."""
        research_dir = self._resolve_research_dir(project_id)
        evidence_map = self._load_evidence_map(research_dir)
        results: List[EvidenceRecord] = []
        for e_id, e_data in evidence_map.items():
            try:
                rec = EvidenceRecord.model_validate(e_data)
                if source_id and rec.source_id != source_id:
                    continue
                if run_id and rec.run_id != run_id:
                    continue
                results.append(rec)
            except Exception:
                continue
        return sorted(results, key=lambda e: e.created_at)

    # -----------------------------------------------------------------------
    # Evidence Brief Artifacts
    # -----------------------------------------------------------------------

    def save_evidence_brief(
        self,
        project_id: str,
        markdown_content: str,
        filename: str = "evidence_brief.md",
    ) -> Path:
        """Atomically persist a markdown evidence brief under the project research directory."""
        research_dir = self._resolve_research_dir(project_id)
        target_path = (research_dir / filename).resolve()
        if not target_path.is_relative_to(research_dir):
            raise ResearchSecurityError(f"Path traversal detected in brief filename '{filename}'.")

        self._atomic_write_text(target_path, markdown_content)
        return target_path

    def get_evidence_brief(
        self,
        project_id: str,
        filename: str = "evidence_brief.md",
    ) -> Optional[str]:
        """Read persisted markdown evidence brief if available."""
        research_dir = self._resolve_research_dir(project_id)
        target_path = (research_dir / filename).resolve()
        if not target_path.is_relative_to(research_dir):
            raise ResearchSecurityError(f"Path traversal detected in brief filename '{filename}'.")

        if not target_path.exists() or not target_path.is_file():
            return None

        return target_path.read_text(encoding="utf-8")

    def get_run_evidence_brief(
        self,
        project_id: str,
        run_id: str,
    ) -> Optional[str]:
        """Read run-scoped persisted markdown evidence brief for a specific research run.

        Looks up the run to determine its registered artifact path in `run.artifacts["evidence_brief"]`.
        If registered and the file exists on disk within the project research directory, returns its content.
        If the run has no registered artifact or the file is missing, returns None.
        """
        run = self.get_run(project_id, run_id)
        filename = run.artifacts.get("evidence_brief")
        if not filename:
            # Check default run-scoped location
            candidate_filename = f"runs/{run_id}/evidence_brief.md"
            research_dir = self._resolve_research_dir(project_id)
            candidate_path = (research_dir / candidate_filename).resolve()
            if candidate_path.is_relative_to(research_dir) and candidate_path.is_file():
                filename = candidate_filename
            else:
                return None

        return self.get_evidence_brief(project_id, filename=filename)
