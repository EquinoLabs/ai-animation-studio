# Phase 6: Research Studio UI & Conversational Fact Challenges — Architecture Record

## Executive Summary

Phase 6 exposes the research and claim verification capabilities built in Phases 4 and 5 through structured inspection APIs, research overview aggregation, evidence provenance tracking, and conversational fact challenges.

In **Checkpoint 1**, the backend inspection APIs and conversational challenge workflow have been implemented and rigorously verified with exhaustive test coverage:
1. **Research Overview API** (`GET /api/v1/projects/{project_id}/research`): aggregates research run status, query execution outcomes, sanitized source citations, selected evidence records, query lifecycle states, and live 4-way evidence categorization (retrieved, AI-selected, active script used, and unused).
2. **Evidence Brief API** (`GET /api/v1/projects/{project_id}/research/brief`): returns the serialized Markdown evidence brief for an active or specified research run.
3. **Fact-Check Report Retrieval API** (`GET /api/v1/projects/{project_id}/script/fact-check`): retrieves the structured fact-check report for the current script or an immutable historical version (`?version=N`).
4. **Conversational Fact Challenge Workflow** (`POST /api/v1/projects/{project_id}/script/challenge`): provides conversational challenge actions (`investigate`, `qualify`, `rephrase`, `remove`) on individual claims, executing targeted re-investigation via `ResearchOrchestrator` when research capabilities are available, and reporting limitations honestly without false research claims when external tools are unavailable.

All changes adhere strictly to project isolation, path safety, offline determinism, and version immutability guarantees.

---

## 1. API Endpoints and Data Contracts

### 1.1 Research Overview (`GET /api/v1/projects/{project_id}/research`)
- **Handler**: `get_project_research_overview` in `backend/app/api/routes.py`
- **Orchestration**: `PipelineOrchestrator.get_research_overview(project_id)`
- **Response Model**: `ResearchOverviewResponse`
  - `has_research: bool`: Indicates whether research runs exist for the project.
  - `active_run_id: Optional[str]`: Run ID matching current script's `research_run_id` or latest run.
  - `status: Optional[str]`: Research run status (`completed`, `budget_exhausted`, `failed`).
  - `termination_reason: Optional[str]`: Structured termination explanation.
  - `budget_summary: Dict[str, Any]`: Search, scrape, tokens, and duration expenditures.
  - `queries: List[QueryOutcomeView]`: Per-query lifecycle state (`planned`, `completed`, `skipped`, `failed`), question, result count, error message, and skip reason.
  - `sources: List[SafeSourceView]`: Sanitized sources with validated `http://` / `https://` URLs, titles, publishers, retrieval status, and character counts.
  - `selected_evidence: List[SafeEvidenceView]`: Evidence records with excerpt, locator, target claim, status, `is_selected: bool`, `selection_rationale: Optional[str]`, `is_used_in_current_script: bool`, and `used_by_claims: List[str]`.
  - `retrieved_evidence_ids: List[str]`: All evidence records persisted for the run.
  - `selected_evidence_ids: List[str]`: Evidence selected by narrative outline or candidate status.
  - `used_evidence_ids: List[str]`: Evidence IDs cited by active script claims.
  - `unused_evidence_ids: List[str]`: Available evidence IDs not cited by active script claims.
  - `conflicts: List[str]`: Synthesized conflicts identified during research.
  - `unresolved_questions: List[str]`: Questions remaining unanswered.

### 1.2 Query Lifecycle Taxonomy (`QueryOutcomeView`)
Exposes all 4 required query lifecycle states:
- **`planned`**: Query was planned in `ResearchPlan` but has not yet been executed in a run. `result_count=0`, `error_message=None`, `skip_reason=None`.
- **`completed`**: Query was dispatched and returned source results (`QueryRecord.status == "succeeded"`).
- **`skipped`**: Query was skipped by `ResearchExecutionSession` due to budget or deadline ceilings. Exposes `skip_reason` from the underlying execution record (e.g., `"Search call budget exhausted (max 3 calls reached)."`).
- **`failed`**: Query attempt raised an error during execution. Exposes `error_message` from the provider response.

### 1.3 Evidence Taxonomy & Selection Semantics
Strictly separates evidence lifecycle and usage concepts:
1. **Retrieved**: All evidence excerpts verified and persisted in storage (`retrieved_evidence_ids`).
2. **Candidate**: Evidence available for consideration (`EvidenceRecord.status == "candidate"`). Being a candidate does *not* automatically mean the item is selected.
3. **Selected**: Evidence explicitly selected by the narrative outliner (`outline.sections[].evidence_ids`) or another recorded selection operation (`is_selected: bool`, `selected_evidence_ids`). Not inferred from candidate status or mere existence.
4. **Used**: Evidence explicitly cited by active script claims asserted in section narration (`used_evidence_ids`, `is_used_in_current_script: bool`). Not inferred from selection.
5. **Unused**: Retrieved evidence records not currently linked to active script claims (`unused_evidence_ids`). An item may be selected by the outliner yet unused in final narration.
6. **Selection Rationale**: Optional rationale recorded on `EvidenceRecord.selection_rationale`. When not recorded on the underlying artifact, exposed honestly as `null` (`None`) without fabrication.

---

## 2. Fact Challenge Mechanics & Provenance Guarantees

### 2.1 Action: `investigate`
1. **When Research Capabilities are Configured**:
   - `ScriptService` invokes `ResearchOrchestrator.execute_plan` with a targeted `ResearchPlan` addressing the challenged claim.
   - A new, distinct `ResearchRun` (`new_run_id`) is persisted in storage with fresh sources and evidence excerpts (`e.run_id == new_run_id`).
   - Prior research runs (`prior_run_id`) and their evidence records remain in storage intact.
   - An updated evidence brief is compiled and saved for the new run.
   - A combined evidence catalog is constructed, allowing the revision to cite new evidence while maintaining previous valid evidence.
   - Script version advances to `vNNN` with `research_run_id = new_run_id`.
   - `ScriptFactChecker` evaluates the revised script against the new run catalog. The resulting claim assessment references the newly retrieved evidence in `considered_evidence_ids` / `supporting_evidence_ids`.
   - Provenance is recorded in `conversation.json` with metadata containing `action="investigate"`, `prior_run_id`, `new_run_id`, `new_evidence_ids`, `investigation_performed=True`, and zero raw paths or sensitive provider details.
2. **When Research Capabilities are Unavailable (or Unresearched Script)**:
   - Sets `investigation_performed = False`.
   - Returns explicit `investigation_note` (e.g., `"Fresh external investigation is currently unavailable (no active research tools configured). Claim was re-evaluated against existing evidence catalog for run '...'. Fresh research was NOT performed."`).
   - `outcome` is reported as `"rechecked_existing_evidence_only"` or `"unresearched_script_recheck"`.
   - Never falsely claims fresh research occurred.

### 2.2 Action: `remove`
When a user challenges a claim with `action="remove"`:
1. **Surgical Sentence Removal**: The exact sentence containing the target claim is located and removed from `target_section.narration`. Unrelated sentences in the section and all other sections are preserved verbatim.
2. **Section Claim Unlinking**: The target claim ID is removed from `target_section.claim_ids`.
3. **Current Script Claim Status**: In `current_script.claims`, the target claim remains present with `link_status="unlinked"`, `evidence_ids=[]`, `source_ids=[]`, and `assessment_status="not_checked"`. Historical script versions (`v001`, `v002`, etc.) remain immutable.
4. **Evidence Usage Update**: In subsequent research overview requests (`/research`), evidence records previously cited exclusively by the removed claim transition to `unused_evidence_ids`.
5. **Re-Checking**: `ScriptFactChecker.check_script` is executed on the new script. Active claims are determined by section narration membership (`sec.claim_ids`). The unlinked claim is omitted from verification.
6. **Persistence**: The new version (`vNNN`) and its report are committed via `_commit_script_version`, and a `fact_challenge` conversation event is recorded.
7. **Re-Challenge Guard**: Any subsequent attempt to challenge an already unlinked claim is rejected with HTTP `422 Unprocessable Content`.

### 2.3 Actions: `qualify`, `rephrase`
1. Formulates targeted revision directives specifying the target claim, its section, prior assessment status, existing evidence IDs, and user instructions.
2. Dispatches through `ScriptService.revise_script`, invoking `ScriptDraftGenerator`, reconciling claim IDs, running `ScriptFactChecker`, and persisting the new version atomically.

---

## 3. Checkpoint 2: Research Studio UI Panel Architecture

### 3.1 Overview & Integration
The Research Studio UI is integrated seamlessly into the main workspace via top-level tab navigation (`[ 📝 Script Workspace ]` and `[ 🔬 Research Studio ]`), preserving full responsiveness and layout continuity.

### 3.2 Component Breakdown
- **`ResearchStudioPanel.tsx`**:
  - **Run Status & Disclaimer Header**: Displays active run ID, status badge, budget metrics (search calls, extract calls, characters extracted), and the explicit grounding notice: *"Evidence excerpts and fact-checking evaluations reflect local AI-assisted grounding against retrieved source documents. They do not certify objective external truth, and rechecking existing catalogs does not constitute fresh external research."*
  - **Evidence Catalog Tab**:
    - Lifecycle stage filters: `All`, `Candidates`, `Selected`, `Actively Used`, `Unused in Script`.
    - Keyword search across IDs, excerpts, locators, and claims.
    - Card badges strictly separate:
      - `Candidate for Consideration` (status indicator)
      - `✨ AI-Selected by Outline` vs `⚪ Not Selected by Outline` (outliner choice)
      - `📜 Actively Used in Narration` vs `💤 Unused in Current Narration` (claim linkage)
    - Truthful display of `selection_rationale` (renders recorded rationale verbatim; shows *"No selection rationale recorded"* when null, never hallucinating reasons).
  - **Query History Tab**:
    - Exposes queries in all 4 lifecycle states: `completed` (with result counts), `planned`, `skipped` (with explicit `skip_reason`), and `failed` (with sanitized `error_message`).
  - **Sources Tab**:
    - Displays extracted sources with title, domain/publisher, character count, and retrieval status.
    - Strictly enforces safe outbound links (`http://` or `https://` only) opened with `rel="noopener noreferrer"`.
  - **Assessments, Conflicts & Unresolved Questions Tab**:
    - Synthesized evidence conflicts and contradictions.
    - Unresolved research questions.
    - Fact-check report summary and per-claim assessment details when reports are present.
- **`ResearchBriefModal.tsx`**:
  - Dedicated modal for full-text inspection of the markdown evidence brief (`/research/brief`).
  - Includes clipboard copy functionality and handles absent briefs (404) gracefully.

### 3.3 Robustness & Safety Guarantees
1. **Empty States**: Projects without research data render a dedicated empty state explaining the research cycle and guiding the user to generate a draft.
2. **Missing Reports**: Handles absent fact-check reports without throwing errors or displaying misleading data.
3. **Boundary Protection**: Does not implement claim badges, claim drawers, or conversational fact-challenge controls in the script view (reserved for Checkpoint 3).
