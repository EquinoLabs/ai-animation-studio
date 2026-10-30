"""Evidence Brief Builder compiling persisted research artifacts into a structured evidence brief (Phase 3)."""

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from backend.app.config import Settings, get_settings
from backend.app.domain.research import (
    EvidenceRecord,
    PlannedQuery,
    QueryRecord,
    ResearchPlan,
    ResearchRun,
    SourceRecord,
    utc_now,
)
from backend.app.services.project.service import ProjectNotFoundError, ProjectSecurityError
from backend.app.services.research.storage import (
    ResearchPlanNotFoundError,
    ResearchRunNotFoundError,
    ResearchSecurityError,
    ResearchStorageError,
    ResearchStorageService,
    ResearchValidationError,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class EvidenceBriefError(Exception):
    """Base exception for evidence brief generation failures."""
    pass


# ---------------------------------------------------------------------------
# Evidence Brief Builder
# ---------------------------------------------------------------------------

class EvidenceBriefBuilder:
    """Compiles persisted research run records, sources, and evidence into evidence_brief.md."""

    def __init__(
        self,
        storage_service: Optional[ResearchStorageService] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self.storage_service = storage_service or ResearchStorageService(settings=settings)
        self.settings = settings or get_settings()

    def build_brief_markdown(self, project_id: str, run_id: str) -> str:
        """Compile a structured, factual markdown brief strictly from persisted data.

        Args:
            project_id: Associated project identifier.
            run_id: Research run identifier to compile the brief for.

        Returns:
            The formatted markdown text.

        Raises:
            EvidenceBriefError: If project, plan, or run data is missing or corrupted.
        """
        try:
            self.storage_service._resolve_research_dir(project_id)
        except (ResearchSecurityError, ProjectSecurityError, ValueError) as sec_err:
            raise EvidenceBriefError(f"Security error for project '{project_id}': {sec_err}") from sec_err

        try:
            run = self.storage_service.get_run(project_id, run_id)
        except (ResearchRunNotFoundError, ResearchValidationError) as r_err:
            raise EvidenceBriefError(f"Failed to load run '{run_id}' in project '{project_id}': {r_err}") from r_err

        try:
            plan = self.storage_service.get_plan(project_id, run.plan_id)
        except (ResearchPlanNotFoundError, ResearchValidationError) as p_err:
            raise EvidenceBriefError(f"Failed to load plan '{run.plan_id}' for run '{run_id}': {p_err}") from p_err

        # Load all sources and index by source_id
        sources_list = self.storage_service.list_sources(project_id)
        sources_by_id: Dict[str, SourceRecord] = {s.source_id: s for s in sources_list}

        # Load all evidence records for this specific run
        evidence_list = self.storage_service.list_evidence(project_id, run_id=run.run_id)

        # Index query records
        queries_by_id: Dict[str, QueryRecord] = {q.query_id: q for q in run.query_records}

        # Format date strings
        gen_time_str = utc_now().strftime("%Y-%m-%d %H:%M:%S UTC")
        run_started_str = run.started_at.strftime("%Y-%m-%d %H:%M:%S UTC") if run.started_at else "N/A"
        run_completed_str = run.completed_at.strftime("%Y-%m-%d %H:%M:%S UTC") if run.completed_at else "In Progress"

        # Coverage assessment description
        coverage_desc = self._format_coverage_assessment(run, plan)

        # Build Section 1: Header & Metadata
        md_lines: List[str] = [
            f"# Evidence Brief: {plan.topic}",
            "",
            f"**Project ID:** `{project_id}`  ",
            f"**Plan ID:** `{plan.plan_id}`  ",
            f"**Run ID:** `{run.run_id}`  ",
            f"**Generated At:** `{gen_time_str}`  ",
            f"**Execution Status:** `{run.status}`  ",
            f"**Termination Reason:** `{run.termination_reason or 'None'}`  ",
            f"**Coverage Assessment:** {coverage_desc}",
            "",
            "---",
            "",
            "## 1. Research Objectives & Execution Overview",
            f"- **Topic:** {plan.topic}",
            f"- **Research Objective:** {plan.research_objective}",
            f"- **Execution Window:** {run_started_str} — {run_completed_str}",
            f"- **Resource Consumption:** {run.search_calls_used} search calls, {run.extract_calls_used} extraction calls, {run.total_chars_extracted} characters extracted.",
            f"- **Artifacts Retained:** {len(sources_list)} sources discovered, {len(evidence_list)} evidence excerpts verified.",
            "",
            "---",
            "",
            "## 2. Research Questions & Retrieved Evidence",
            "",
        ]

        # Build Section 2: Question-by-Question Evidence Grouping
        unresolved_questions: List[Tuple[str, str]] = []

        for q_idx, question in enumerate(plan.research_questions, start=1):
            md_lines.append(f"### Question {q_idx}: \"{question}\"")

            # Identify queries addressing this research question
            matching_planned_queries = [
                pq for pq in plan.planned_queries if (pq.target_question == question or pq.purpose == question)
            ]
            if not matching_planned_queries:
                # If target_question was not exact, check query text or fallback
                matching_planned_queries = plan.planned_queries

            matching_q_ids = {pq.query_id for pq in matching_planned_queries}

            # Associated queries execution outcomes
            md_lines.append("- **Query Execution Outcomes:**")
            for pq in matching_planned_queries:
                q_rec = queries_by_id.get(pq.query_id)
                q_status = q_rec.status if q_rec else "unexecuted"
                md_lines.append(f"  * Query `{pq.query_id}`: \"{pq.query_text}\" — **Status:** `{q_status}`")
                if q_rec and q_rec.error_message:
                    md_lines.append(f"    - *Note / Error:* {q_rec.error_message}")

            # Collect evidence matching this question or its queries
            q_evidence = [
                e for e in evidence_list
                if (e.target_claim == question or (e.query_id and e.query_id in matching_q_ids))
            ]

            if q_evidence:
                md_lines.append("")
                md_lines.append("- **Retrieved Evidence Excerpts:**")
                for e_item in q_evidence:
                    src = sources_by_id.get(e_item.source_id)
                    src_title = src.title if src and src.title else "Untitled Source"
                    src_url = src.url if src and src.url else "URL Unavailable"
                    src_id = src.source_id if src else e_item.source_id

                    md_lines.append(f"  > \"{e_item.excerpt}\"")
                    md_lines.append(f"  > ")
                    md_lines.append(f"  > — *Source:* [{src_title}]({src_url}) (ID: `{src_id}`)")
                    md_lines.append(f"  > — *Locator:* `{e_item.locator or 'section: main'}`")
                    md_lines.append(f"  > — *Inquiry Target:* `{e_item.target_claim or question}`")
                    md_lines.append("")
            else:
                md_lines.append("")
                md_lines.append("- **Retrieved Evidence:** *No verified evidence was extracted for this question.*")
                md_lines.append("")
                unresolved_questions.append((question, "No evidence extracted or all extractions failed"))

        md_lines.append("---")
        md_lines.append("")

        # Build Section 3: Evidence Consistency & Conflict Analysis
        md_lines.append("## 3. Evidence Consistency & Cross-Source Analysis")
        if len(evidence_list) >= 2 and len(sources_list) >= 2:
            md_lines.append(
                "Multiple sources and excerpts were gathered. While specific passages provide factual details on their respective subtopics, "
                "available retrieved data is currently insufficient to determine cross-source contradictions or historical consensus. "
                "Findings reflect verified retrieved passages only, and should be treated as complementary evidence."
            )
        elif len(evidence_list) == 1:
            md_lines.append(
                "Single evidence excerpt retrieved. Cross-source comparison is not possible with currently available records."
            )
        else:
            md_lines.append(
                "Zero evidence excerpts retrieved. Cross-source conflict analysis is not applicable."
            )
        md_lines.append("")
        md_lines.append("---")
        md_lines.append("")

        # Build Section 4: Unresolved Questions & Research Gaps
        md_lines.append("## 4. Unresolved Questions & Research Gaps")
        skipped_queries = [q for q in run.query_records if q.status == "skipped"]
        failed_queries = [q for q in run.query_records if q.status == "failed"]

        if skipped_queries:
            md_lines.append("- **Skipped Queries (Budget / Deadline Ceilings):**")
            for sq in skipped_queries:
                md_lines.append(f"  * `{sq.query_id}`: \"{sq.query_text}\" — Reason: {sq.error_message or 'Budget ceiling reached'}")

        if failed_queries:
            md_lines.append("- **Failed Queries (Provider / Network Errors):**")
            for fq in failed_queries:
                md_lines.append(f"  * `{fq.query_id}`: \"{fq.query_text}\" — Error: {fq.error_message or 'Provider failure'}")

        if unresolved_questions:
            md_lines.append("- **Research Questions with Gaps:**")
            for uq, reason in unresolved_questions:
                md_lines.append(f"  * \"{uq}\" — {reason}")

        if not skipped_queries and not failed_queries and not unresolved_questions:
            md_lines.append("All planned research questions have at least one verified evidence excerpt. No gaps identified within the configured budget scope.")

        md_lines.append("")
        md_lines.append("---")
        md_lines.append("")

        # Build Section 5: Methodological Limitations & Epistemic Boundaries
        md_lines.append("## 5. Methodological Limitations & Epistemic Boundaries")
        md_lines.append("1. **Retrieved Does Not Equal Verified:** Passages in this brief represent verbatim excerpts from retrieved web documents. Inclusion in this brief does not constitute independent verification of objective historical or scientific truth.")
        md_lines.append(f"2. **Budget Ceilings:** Research was executed strictly within development ceilings ({plan.budget_limits.max_search_calls} searches, {plan.budget_limits.max_extract_calls} extractions, {plan.budget_limits.max_page_chars} chars/page). Unvisited sources may contain differing perspectives or further context.")
        md_lines.append("3. **Downstream Scriptwriting Directives:** Script generation (Phase 4) must cite these specific source IDs and locators. Script narration must NOT invent factual claims beyond the scope of these excerpts.")
        md_lines.append("")

        return "\n".join(md_lines)

    def build_and_save_brief(
        self,
        project_id: str,
        run_id: str,
        filename: str = "evidence_brief.md",
    ) -> Tuple[Path, str]:
        """Compile the brief markdown and atomically persist it to project research storage.

        Args:
            project_id: Associated project identifier.
            run_id: Research run identifier.
            filename: Target brief filename (defaults to 'evidence_brief.md').

        Returns:
            Tuple of (persisted_brief_path, markdown_content).

        Raises:
            EvidenceBriefError: If building or persistence fails.
        """
        markdown_content = self.build_brief_markdown(project_id, run_id)

        try:
            brief_path = self.storage_service.save_evidence_brief(
                project_id=project_id,
                markdown_content=markdown_content,
                filename=filename,
            )
        except Exception as st_err:
            raise EvidenceBriefError(f"Failed to persist evidence brief in project '{project_id}': {st_err}") from st_err

        # Update ResearchRun artifact reference
        try:
            run = self.storage_service.get_run(project_id, run_id)
            run.artifacts["evidence_brief"] = filename
            self.storage_service.save_run(run)
        except Exception as r_err:
            raise EvidenceBriefError(f"Failed to update artifact reference in run '{run_id}': {r_err}") from r_err

        return brief_path, markdown_content

    def _format_coverage_assessment(self, run: ResearchRun, plan: ResearchPlan) -> str:
        """Produce an honest plain-language coverage assessment."""
        succeeded_q = [q for q in run.query_records if q.status == "succeeded"]
        skipped_q = [q for q in run.query_records if q.status == "skipped"]
        failed_q = [q for q in run.query_records if q.status == "failed"]
        total_q = len(plan.planned_queries)

        if run.status == "failed":
            return f"Failed execution ({run.termination_reason or 'Unresolved error'}). No reliable evidence gathered."
        elif run.termination_reason == "completed_full_coverage":
            return f"Full coverage: all {total_q} planned queries succeeded with verified evidence."
        elif skipped_q:
            return f"Partial coverage: {len(succeeded_q)} of {total_q} queries executed ({len(skipped_q)} skipped due to budget/deadline limits)."
        elif failed_q:
            return f"Partial coverage: {len(succeeded_q)} of {total_q} queries succeeded ({len(failed_q)} failed)."
        return f"Completed with status '{run.status}' (reason: {run.termination_reason})."
