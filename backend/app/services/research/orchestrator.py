"""Research execution orchestrator coordinating plans, tools, sessions, and storage (P2-S3)."""

from datetime import datetime
import logging
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import uuid

from backend.app.config import Settings, get_settings
from backend.app.domain.research import (
    EvidenceRecord,
    ID_REGEX,
    PlannedQuery,
    QueryRecord,
    ResearchPlan,
    ResearchRun,
    ResearchRunStatus,
    SourceRecord,
    utc_now,
)
from backend.app.services.project.service import ProjectNotFoundError, ProjectSecurityError
from backend.app.services.research.storage import (
    ResearchPlanNotFoundError,
    ResearchReferenceIntegrityError,
    ResearchSecurityError,
    ResearchStorageError,
    ResearchStorageService,
    ResearchValidationError,
)
from backend.app.services.research.tools.base import (
    ResearchBudgetExceededError,
    ResearchDeadlineExceededError,
    ResearchExecutionSession,
    ResearchProviderError,
    ResearchTimeoutError,
    ResearchToolError,
    ToolRegistry,
    ToolValidationError,
    UnsafeURLError,
    sanitize_secret,
    validate_url_safety,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ResearchOrchestratorError(Exception):
    """Base exception for research execution orchestration failures."""
    pass


# ---------------------------------------------------------------------------
# Conservative URL Deduplication Helper
# ---------------------------------------------------------------------------

TRACKING_QUERY_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "msclkid",
}


def normalize_url_for_dedupe(url: str) -> str:
    """Conservatively normalize URL strictly for deduplication identity comparison.

    Rules:
    - Lowercase scheme and hostname.
    - Strip standard default ports (:80 for http, :443 for https).
    - Strip trailing slash from path root only (e.g. 'https://example.com/' -> 'https://example.com').
    - Strip only recognized tracking query parameters (utm_*, fbclid, gclid, msclkid).
      Never strip content-identifying parameters (e.g. id, article, page, view).
    - Sort remaining query parameters deterministically.
    - Strip URL fragments (#...).
    - Retains original fetch URL for actual network calls.
    """
    if not url or not isinstance(url, str):
        return ""

    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    # Strip default ports
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]

    path = parsed.path
    if path == "/":
        path = ""

    # Filter recognized tracking parameters only
    query_tuples = parse_qsl(parsed.query, keep_blank_values=True)
    filtered_queries = [
        (k, v) for k, v in query_tuples if k.lower() not in TRACKING_QUERY_PARAMS
    ]
    # Sort deterministically
    filtered_queries.sort(key=lambda t: t[0])
    new_query = urlencode(filtered_queries)

    # Reassemble without fragment
    return urlunsplit((scheme, netloc, path, new_query, ""))


# ---------------------------------------------------------------------------
# Verbatim Evidence Extraction Helper
# ---------------------------------------------------------------------------

def extract_verbatim_evidence(
    content: str, max_chars: int = 1000
) -> Tuple[str, str]:
    """Extract a non-empty, bounded verbatim passage and structural locator from markdown content."""
    lines = content.splitlines()
    current_heading: Optional[str] = None
    target_paragraph: List[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            if not target_paragraph:
                current_heading = stripped.lstrip("#").strip()
            else:
                break
        elif stripped:
            target_paragraph.append(stripped)
            if sum(len(p) for p in target_paragraph) >= 200:
                break

    if target_paragraph:
        passage = " ".join(target_paragraph)
    else:
        passage = content.strip()

    excerpt = passage[:max_chars].strip()
    locator = f"heading: '{current_heading}'" if current_heading else "section: main"
    return excerpt, locator


# ---------------------------------------------------------------------------
# Research Orchestrator Service
# ---------------------------------------------------------------------------

class ResearchOrchestrator:
    """Orchestrates bounded, sequential research execution from an accepted ResearchPlan."""

    DEFAULT_TOOL_MAP = {
        "web_search": "tavily_search",
        "web_extract": "firecrawl_extract",
    }

    def __init__(
        self,
        tool_registry: ToolRegistry,
        storage_service: Optional[ResearchStorageService] = None,
        settings: Optional[Settings] = None,
        tool_map: Optional[Dict[str, str]] = None,
    ) -> None:
        self.tool_registry = tool_registry
        self.storage_service = storage_service or ResearchStorageService(settings=settings)
        self.settings = settings or get_settings()
        self.tool_map = tool_map or self.DEFAULT_TOOL_MAP

    def _resolve_tool_name(self, category: str, approved_categories: List[str]) -> str:
        """Resolve and validate a tool name for a given tool category."""
        if category not in approved_categories:
            raise ResearchOrchestratorError(
                f"Tool category '{category}' is not approved for this plan (approved: {approved_categories})."
            )
        tool_name = self.tool_map.get(category)
        if not tool_name:
            raise ResearchOrchestratorError(f"No configured tool for category '{category}'.")
        # Verify tool is registered in ToolRegistry
        self.tool_registry.get(tool_name)
        return tool_name

    def execute_plan(
        self,
        project_id: str,
        plan_id: str,
        clock: Optional[Callable[[], float]] = None,
    ) -> ResearchRun:
        """Execute all approved queries and extractions for a ResearchPlan sequentially.

        Args:
            project_id: Associated project identifier.
            plan_id: Identifier of the persisted ResearchPlan.
            clock: Optional monotonic clock double for deterministic testing.

        Returns:
            The finalized ResearchRun entity.

        Raises:
            ResearchOrchestratorError: If project security, plan retrieval, or persistence fails.
        """
        # 1. Project directory resolution & isolation check
        try:
            self.storage_service._resolve_research_dir(project_id)
        except (ResearchSecurityError, ProjectSecurityError, ValueError) as sec_err:
            raise ResearchOrchestratorError(
                f"Project security error for '{project_id}': {sec_err}"
            ) from sec_err

        # 2. Load ResearchPlan
        try:
            plan = self.storage_service.get_plan(project_id, plan_id)
        except (ResearchPlanNotFoundError, ResearchValidationError) as p_err:
            raise ResearchOrchestratorError(
                f"Failed to load research plan '{plan_id}' in project '{project_id}': {p_err}"
            ) from p_err

        if plan.project_id != project_id:
            raise ResearchOrchestratorError(
                f"Plan project mismatch: plan '{plan_id}' belongs to '{plan.project_id}', not '{project_id}'."
            )

        # 3. Create ResearchRun (pending) and transition to running
        try:
            run = self.storage_service.create_run(project_id=project_id, plan_id=plan.plan_id)
            run = self.storage_service.transition_run_status(project_id=project_id, run_id=run.run_id, to_status="running")
        except Exception as st_err:
            raise ResearchOrchestratorError(f"Failed to initialize research run: {st_err}") from st_err

        # 4. Initialize Execution Session
        session = ResearchExecutionSession(
            max_search_calls=plan.budget_limits.max_search_calls,
            max_extract_calls=plan.budget_limits.max_extract_calls,
            call_timeout_seconds=plan.budget_limits.call_timeout_seconds,
            deadline_seconds=plan.budget_limits.deadline_seconds,
            max_page_chars=plan.budget_limits.max_page_chars,
            max_total_chars=plan.budget_limits.max_total_chars,
            clock=clock,
        )

        discovered_sources: List[SourceRecord] = []
        seen_canonical_urls: Set[str] = set()
        source_to_query_map: Dict[str, str] = {}  # source_id -> query_id
        source_to_target_question: Dict[str, Optional[str]] = {}  # source_id -> target_question

        search_budget_exhausted = False
        search_deadline_exhausted = False

        # -------------------------------------------------------------------
        # STAGE 1: Search Execution Loop
        # -------------------------------------------------------------------
        for query in plan.planned_queries:
            # Check deadline before query
            if session.remaining_seconds() <= 0:
                search_deadline_exhausted = True
                self._record_skipped_query(
                    project_id=project_id,
                    run=run,
                    query=query,
                    reason="Skipped: session deadline exceeded.",
                )
                continue

            # Check search budget before query
            if session.search_calls >= session.max_search_calls:
                search_budget_exhausted = True
                self._record_skipped_query(
                    project_id=project_id,
                    run=run,
                    query=query,
                    reason=f"Skipped: search budget limit reached ({session.max_search_calls}).",
                )
                continue

            # Resolve tool
            try:
                tool_name = self._resolve_tool_name(query.tool_category, plan.approved_tool_categories)
            except Exception as t_err:
                self._record_failed_query(
                    project_id=project_id,
                    run=run,
                    query=query,
                    tool_name="unknown",
                    error=str(t_err),
                )
                continue

            # Record running query
            start_time = utc_now()
            q_rec = QueryRecord(
                query_id=query.query_id,
                plan_id=plan.plan_id,
                run_id=run.run_id,
                query_text=query.query_text,
                intended_question=query.target_question,
                tool_name=tool_name,
                status="running",
                started_at=start_time,
            )
            try:
                self.storage_service.record_query_execution(project_id, run.run_id, q_rec)
            except Exception as err:
                self._handle_persistence_failure(project_id, run, f"Failed to record query '{query.query_id}': {err}")

            # Execute tool
            try:
                tool_result = self.tool_registry.execute(
                    name=tool_name,
                    raw_args={"query": query.query_text},
                    session=session,
                )
            except Exception as exc:
                clean_err = sanitize_secret(str(exc), [self.settings.TAVILY_API_KEY, self.settings.FIRECRAWL_API_KEY])
                self._record_failed_query(
                    project_id=project_id,
                    run=run,
                    query=query,
                    tool_name=tool_name,
                    error=clean_err,
                    started_at=start_time,
                )
                continue

            # Process search results
            data = tool_result.data or {}
            raw_results = data.get("results", [])
            returned_source_ids: List[str] = []

            for item in raw_results:
                if isinstance(item, dict):
                    raw_url = item.get("url", "")
                    raw_title = item.get("title")
                    raw_snippet = item.get("snippet")
                else:
                    raw_url = getattr(item, "url", "")
                    raw_title = getattr(item, "title", None)
                    raw_snippet = getattr(item, "snippet", None)

                if not raw_url:
                    continue

                canonical = normalize_url_for_dedupe(raw_url)
                if not canonical:
                    canonical = raw_url.strip()

                # Deduplicate: if already seen in this project/run, reference existing source
                if canonical in seen_canonical_urls:
                    existing = next((s for s in discovered_sources if s.canonical_url == canonical), None)
                    if existing and existing.source_id not in returned_source_ids:
                        returned_source_ids.append(existing.source_id)
                    continue

                seen_canonical_urls.add(canonical)
                src_id = f"src_{uuid.uuid4().hex[:12]}"
                source_rec = SourceRecord(
                    source_id=src_id,
                    project_id=project_id,
                    url=raw_url.strip(),
                    canonical_url=canonical,
                    title=raw_title.strip() if raw_title else None,
                    source_type="web_page",
                    retrieval_method="search_snippet",
                    retrieval_status="discovered",
                    discovery_snippet=raw_snippet.strip() if raw_snippet else None,
                    retrieved_at=utc_now(),
                )

                try:
                    saved_source = self.storage_service.save_source(project_id, source_rec)
                    discovered_sources.append(saved_source)
                    returned_source_ids.append(saved_source.source_id)
                    source_to_query_map[saved_source.source_id] = query.query_id
                    source_to_target_question[saved_source.source_id] = query.target_question
                except Exception as s_err:
                    self._handle_persistence_failure(project_id, run, f"Failed to save source: {s_err}")

            # Record succeeded query
            succeeded_q = QueryRecord(
                query_id=query.query_id,
                plan_id=plan.plan_id,
                run_id=run.run_id,
                query_text=query.query_text,
                intended_question=query.target_question,
                tool_name=tool_name,
                status="succeeded",
                started_at=start_time,
                completed_at=utc_now(),
                returned_source_ids=returned_source_ids,
                result_count=len(returned_source_ids),
            )
            try:
                self.storage_service.record_query_execution(project_id, run.run_id, succeeded_q)
            except Exception as err:
                self._handle_persistence_failure(project_id, run, f"Failed to record completed query: {err}")

        # -------------------------------------------------------------------
        # STAGE 2: Extraction & Evidence Generation Loop
        # -------------------------------------------------------------------
        extracted_sources_count = 0
        evidence_records_count = 0
        extract_deadline_exhausted = False
        extract_budget_exhausted = False

        # Candidate selection: iterate deterministically over discovered sources
        for source in discovered_sources:
            # Check deadline before extraction
            if session.remaining_seconds() <= 0:
                extract_deadline_exhausted = True
                break

            # Check extraction budget before extraction
            if session.extract_calls >= session.max_extract_calls:
                extract_budget_exhausted = True
                break

            # Pre-flight SSRF and safety check on the EXACT fetch URL
            try:
                validate_url_safety(source.url)
            except UnsafeURLError as unsafe_err:
                logger.warning("Extraction URL '%s' failed safety validation: %s", source.url, unsafe_err)
                unavail_source = source.model_copy(update={"retrieval_status": "unavailable"})
                try:
                    self.storage_service.save_source(project_id, unavail_source)
                except Exception as err:
                    self._handle_persistence_failure(project_id, run, f"Failed to update unavailable source: {err}")
                continue

            # Resolve extraction tool
            try:
                extract_tool_name = self._resolve_tool_name("web_extract", plan.approved_tool_categories)
            except Exception as e_err:
                logger.error("Failed to resolve extract tool: %s", e_err)
                break

            # Dispatch extraction tool
            try:
                extract_result = self.tool_registry.execute(
                    name=extract_tool_name,
                    raw_args={"url": source.url},
                    session=session,
                )
            except Exception as exc:
                logger.warning("Extraction failed for URL '%s': %s", source.url, exc)
                failed_source = source.model_copy(update={"retrieval_status": "failed"})
                try:
                    self.storage_service.save_source(project_id, failed_source)
                except Exception as err:
                    self._handle_persistence_failure(project_id, run, f"Failed to update failed source: {err}")
                continue

            extract_data = extract_result.data or {}
            content_text = extract_data.get("content", "")

            if not content_text or not content_text.strip():
                failed_source = source.model_copy(update={"retrieval_status": "failed"})
                try:
                    self.storage_service.save_source(project_id, failed_source)
                except Exception as err:
                    self._handle_persistence_failure(project_id, run, f"Failed to update empty source: {err}")
                continue

            # Save content markdown artifact atomically and update source
            try:
                updated_source = self.storage_service.save_source(
                    project_id=project_id,
                    source=source,
                    content=content_text,
                )
                extracted_sources_count += 1
            except Exception as err:
                self._handle_persistence_failure(project_id, run, f"Failed to persist extracted content: {err}")

            # Extract bounded verbatim excerpt and structural locator
            excerpt, locator = extract_verbatim_evidence(content_text, max_chars=1000)
            if not excerpt:
                continue

            # Retrieve associated inquiry question target (never a fabricated factual claim)
            inquiry_target = source_to_target_question.get(source.source_id) or "General Inquiry"
            associated_qid = source_to_query_map.get(source.source_id)

            evidence_rec = EvidenceRecord(
                evidence_id=f"evi_{uuid.uuid4().hex[:12]}",
                project_id=project_id,
                source_id=source.source_id,
                query_id=associated_qid,
                run_id=run.run_id,
                excerpt=excerpt,
                locator=locator,
                target_claim=inquiry_target,
                status="retrieved",
                created_at=utc_now(),
            )

            try:
                self.storage_service.save_evidence(project_id, evidence_rec)
                evidence_records_count += 1
            except Exception as err:
                self._handle_persistence_failure(project_id, run, f"Failed to persist evidence record: {err}")

        # -------------------------------------------------------------------
        # STAGE 3: Terminal State Classification & Final Persistence
        # -------------------------------------------------------------------
        # Reload latest run state to capture all query records
        latest_run = self.storage_service.get_run(project_id, run.run_id)

        # Update metrics
        latest_run.search_calls_used = session.search_calls
        latest_run.extract_calls_used = session.extract_calls
        latest_run.total_chars_extracted = session.total_chars_extracted
        latest_run.completed_at = utc_now()

        total_queries = len(plan.planned_queries)
        succeeded_queries = [q for q in latest_run.query_records if q.status == "succeeded"]
        failed_queries = [q for q in latest_run.query_records if q.status == "failed"]
        skipped_queries = [q for q in latest_run.query_records if q.status == "skipped"]

        # Determine terminal status and explicit termination reason
        terminal_status: ResearchRunStatus
        term_reason: str

        if len(failed_queries) == total_queries:
            terminal_status = "failed"
            term_reason = f"failed_all_queries_errored: 0 of {total_queries} queries succeeded"
        elif len(succeeded_queries) > 0 and len(discovered_sources) == 0:
            terminal_status = "failed"
            term_reason = "failed_no_sources_found: executed queries returned 0 results"
        elif evidence_records_count == 0:
            terminal_status = "failed"
            if search_deadline_exhausted or extract_deadline_exhausted:
                term_reason = "failed_deadline_exceeded_before_evidence: deadline expired before evidence could be extracted"
            elif search_budget_exhausted:
                term_reason = "failed_budget_exhausted_before_evidence: budget expired before evidence could be extracted"
            else:
                term_reason = "failed_no_evidence_extracted: sources discovered but no content could be safely extracted"
        elif search_budget_exhausted or (skipped_queries and any("budget" in (q.error_message or "").lower() for q in skipped_queries)):
            terminal_status = "completed"
            term_reason = f"partial_coverage_budget_exhausted: {len(succeeded_queries)} of {total_queries} queries executed; {len(skipped_queries)} skipped due to search budget limit"
        elif search_deadline_exhausted or extract_deadline_exhausted or (skipped_queries and any("deadline" in (q.error_message or "").lower() for q in skipped_queries)):
            terminal_status = "completed"
            term_reason = f"partial_coverage_deadline_exceeded: session deadline elapsed after {len(succeeded_queries)} queries and {extracted_sources_count} extractions"
        elif len(failed_queries) > 0:
            terminal_status = "completed"
            term_reason = f"partial_coverage_query_errors: {len(succeeded_queries)} of {total_queries} queries succeeded, {len(failed_queries)} failed; see query_records"
        elif len(succeeded_queries) == total_queries and evidence_records_count > 0:
            terminal_status = "completed"
            term_reason = "completed_full_coverage"
        else:
            terminal_status = "failed"
            term_reason = "failed_unresolved_research_state: research goals not achieved"

        # Apply final transition
        try:
            # First save updated metrics on the run
            self.storage_service.save_run(latest_run)
            final_run = self.storage_service.transition_run_status(
                project_id=project_id,
                run_id=run.run_id,
                to_status=terminal_status,
                termination_reason=term_reason,
            )

            # Automatically compile and save the evidence brief artifact
            from backend.app.services.research.evidence_brief import EvidenceBriefBuilder
            brief_builder = EvidenceBriefBuilder(
                storage_service=self.storage_service,
                settings=self.settings,
            )
            brief_builder.build_and_save_brief(project_id, final_run.run_id)

            # Reload updated run record with evidence_brief artifact reference
            return self.storage_service.get_run(project_id, final_run.run_id)
        except Exception as err:
            self._handle_persistence_failure(project_id, run, f"Failed to persist terminal run state: {err}")
            # _handle_persistence_failure raises ResearchOrchestratorError, so execution will not proceed

    def _record_skipped_query(
        self,
        project_id: str,
        run: ResearchRun,
        query: PlannedQuery,
        reason: str,
    ) -> None:
        """Record an unexecuted planned query as skipped."""
        q_rec = QueryRecord(
            query_id=query.query_id,
            plan_id=run.plan_id,
            run_id=run.run_id,
            query_text=query.query_text,
            intended_question=query.target_question,
            tool_name="skipped",
            status="skipped",
            started_at=utc_now(),
            completed_at=utc_now(),
            error_message=reason,
            result_count=0,
        )
        try:
            self.storage_service.record_query_execution(project_id, run.run_id, q_rec)
        except Exception as err:
            logger.error("Failed to record skipped query '%s': %s", query.query_id, err)

    def _record_failed_query(
        self,
        project_id: str,
        run: ResearchRun,
        query: PlannedQuery,
        tool_name: str,
        error: str,
        started_at: Optional[datetime] = None,
    ) -> None:
        """Record an attempted planned query as failed with sanitized error message."""
        q_rec = QueryRecord(
            query_id=query.query_id,
            plan_id=run.plan_id,
            run_id=run.run_id,
            query_text=query.query_text,
            intended_question=query.target_question,
            tool_name=tool_name,
            status="failed",
            started_at=started_at or utc_now(),
            completed_at=utc_now(),
            error_message=sanitize_secret(error, [self.settings.TAVILY_API_KEY, self.settings.FIRECRAWL_API_KEY]),
            result_count=0,
        )
        try:
            self.storage_service.record_query_execution(project_id, run.run_id, q_rec)
        except Exception as err:
            logger.error("Failed to record failed query '%s': %s", query.query_id, err)

    def _handle_persistence_failure(
        self,
        project_id: str,
        run: ResearchRun,
        error_message: str,
    ) -> None:
        """Handle persistence failure by attempting failure recording and raising ResearchOrchestratorError."""
        logger.error("Persistence failure during research execution: %s", error_message)
        try:
            self.storage_service.transition_run_status(
                project_id=project_id,
                run_id=run.run_id,
                to_status="failed",
                termination_reason=f"persistence_failure: {error_message}",
            )
        except Exception as abort_err:
            logger.error("Could not record failure status on run '%s': %s", run.run_id, abort_err)

        raise ResearchOrchestratorError(f"Research execution aborted due to persistence failure: {error_message}")
