# Phase 5: Claim-Level Fact-Checking & Flexible Repair (Checkpoints 1, 2, 3, & 4)

## Executive Summary

Phase 5 introduces substantive, claim-level factual verification, risk-based readiness evaluation, and flexible surgical repair into the AI Animation Studio scriptwriting pipeline.

In Checkpoint 1, the foundational domain data contracts and version-aligned, project-local atomic storage architecture were established.
In Checkpoint 2, the Claim Verification Engine (`ScriptFactChecker`) was implemented, providing batched, structured model evaluation with application-authoritative provenance enforcement, bounded corrective retries, deterministic batch splitting, and graceful failure isolation.
In Checkpoint 3, the Surgical Repair Engine (`ScriptRepairer`) was implemented, providing targeted, evidence-grounded repair of problematic claims while preserving unrelated script content, claim identity where justified, and bidirectional mappings without recursive repair loops.
In Checkpoint 4, the full Service Lifecycle Integration has been completed in `ScriptService`, integrating `ScriptDraftGenerator`, `ScriptFactChecker`, and `ScriptRepairer` into an end-to-end automated pipeline fulfilling all mandatory requirements: Independent Evidence Coverage (Requirement A), Application-Controlled Risk Classification (Requirement B), Programmatic Surgical Sentence Preservation (Requirement C), and Strict Readiness Gating on Finalization.

---

## 1. Domain Data Contracts (`backend/app/domain/fact_check.py`)

### 1.1 Assessment Status Taxonomy
Exactly the six approved non-binary statuses per roadmap Section 7.3 and 9.3:
- `supported`: The factual proposition is directly entailed by gathered evidence records.
- `partially_supported`: Evidence supports part of the assertion, but requires qualification or a narrower scope.
- `conflicting`: Gathered evidence records disagree with each other.
- `unsupported`: Gathered evidence does not contain facts addressing the claim.
- `not_checked`: Claim was not checked (e.g. unresearched script, model timeout, or unassessed claim).
- `contradicted`: Reliable gathered evidence directly refutes the assertion.

### 1.2 Risk-Based Scrutiny Levels
- `high`: Central theses, dates, numbers, scientific mechanisms, and named entities.
- `normal`: Background context and supporting details.
- `creative_flexibility`: Stylistic framing, analogies, and transitional flow.

### 1.3 `ClaimAssessment` Contract
Represents the evaluation of a discrete `ScriptClaim`:
- `claim_id: str`: Target claim ID (`clm_01`).
- `section_id: str`: Section where the claim appears.
- `proposition: str`: Exact factual proposition evaluated.
- `status: ClaimAssessmentStatus`: One of the six approved statuses.
- `risk_level: ClaimRiskLevel`: Scrutiny tier.
- `explanation: str`: Plain-language justification of assessment.
- `considered_evidence_ids: List[str]`: All evidence record IDs evaluated.
- `supporting_evidence_ids: List[str]`: Evidence record IDs supporting the assertion.
- `contradicting_evidence_ids: List[str]`: Evidence record IDs refuting the assertion.
- `contextual_evidence_ids: List[str]`: Evidence record IDs providing background.
- `unassessed_reason: Optional[str]`: Reason if status is `not_checked`.
- `recommended_action: RepairAction`: `keep`, `qualify`, `remove`, or `rephrase`.
- `assessed_at: datetime`: Timestamp of assessment.

### 1.4 `ScriptFactCheckReport` Contract
Complete fact-check report for a specific `Script` version:
- `report_id: str`: Stable identifier (`fcr_<uuid>`).
- `project_id: str`: Associated project identifier.
- `script_version: int`: Script version evaluated (e.g. `1`, `2`).
- `research_run_id: Optional[str]`: Grounding research run ID.
- `total_claims`, `supported_count`, `partially_supported_count`, `conflicting_count`, `unsupported_count`, `not_checked_count`, `contradicted_count`.
- `has_unresolved_high_risk: bool`: Flagged if any high-risk claim remains unsupported, contradicted, conflicting, or unassessed.
- `is_ready: bool`: Application-authoritative readiness determination.
- `readiness_reason: Optional[str]`: Description of readiness or blocking reasons.
- `assessments: List[ClaimAssessment]`: List of individual assessments.
- `schema_version: str`: `"1.0.0"`.

---

## 2. Readiness Evaluation Rules (Section 2.2)

`recompute_summary()` implements application-authoritative readiness determination:
1. **Contradictions Block Readiness:** Any contradicted claim blocks ready status (`is_ready = False`).
2. **High-Risk Unsupported Blocks Readiness:** Any high-risk claim that is unsupported blocks ready status.
3. **High-Risk Conflicting Blocks Readiness:** Conflicting evidence on a high-risk claim blocks ready status.
4. **High-Risk Not Checked Blocks Readiness:** Any unassessed high-risk claim blocks ready status.
5. **Normal-Risk Unsupported Allowed:** Background details that are unsupported are flagged in counts but do **not** automatically block readiness.

---

## 3. Storage Architecture & Report Persistence

Reports are persisted under:
`projects/<project_id>/script/fact_checks/v{version:03d}.json`

### 3.1 Persistence Guarantees
- **Atomic Writes:** `_atomic_write_json` writes to a temporary file (`.vNNN.json.tmp_{uuid}`) and commits via `os.replace`.
- **Project Isolation:** Validated through `ProjectService.get_project(project_id)`.
- **Overwrite Protection:** Attempting to overwrite an existing `vNNN.json` fact-check report raises `ScriptStorageError`. Historical reports are completely immutable.
- **Lookup by Version:** `get_fact_check_report(project_id, version)` retrieves the exact report for a version.
- **Listing:** `list_fact_check_reports(project_id)` returns all reports sorted by script version ascending.

---

## 4. Backward Compatibility

In `backend/app/domain/script.py`:
- `ScriptClaim.link_status: ClaimLinkStatus` remains strictly unchanged (`"linked"` | `"unlinked"`).
- Added optional `assessment_status: Optional[str] = Field(default=None)` to `ScriptClaim`.
- Existing serialized scripts continue to load cleanly without error or schema migration.

---

## 5. Verification & Testing

- `backend/tests/test_fact_check_contracts_and_storage.py`: 18 comprehensive unit and integration tests verifying all statuses, fields, validators, summary recomputations, atomic writes, overwrite protection, and legacy compatibility.
- Total repository test suite: **261 tests passing** (exit code `0`).
- Frontend production build: `npm run build` passing cleanly.

---

## 6. Checkpoint 2: Claim Verification Engine (`ScriptFactChecker`)

Implemented in `backend/app/services/scriptwriter/fact_checker.py`.

### 6.1 Architecture & Core Components
- **Public API:** `ScriptFactChecker.check_script(script, catalog=None, project_id=None, batch_size=None, supply_all_catalog_evidence=False, fail_fast=False) -> ScriptFactCheckReport`.
- **Structured Payloads:** `ClaimAssessmentPayload` and `BatchFactCheckResponsePayload` define the structured output contract across the LLM boundary.
- **Typed Exceptions:**
  - `ScriptFactCheckError(Exception)`: Root base exception.
  - `ScriptFactCheckSecurityError(ScriptFactCheckError)`: Project isolation or run mismatch violations.
  - `ScriptFactCheckUnavailableError(ScriptFactCheckError)`: LLM connection failure or timeout.
  - `ScriptFactCheckMalformedError(ScriptFactCheckError)`: Schema or JSON syntax failure after retry.

### 6.2 Deterministic Batching & Context Budgeting
- Claims are chunked into batches of at most `batch_size` (default `5`, clamped `>= 1`).
- `_recursively_budget_chunk()` deterministically splits any chunk in half if the combined prompt characters exceed `max_batch_chars` (default `12,000`), preventing context budget overflow without falling back to unbounded single-claim calls.

### 6.3 Application-Authoritative Provenance Enforcement
- The LLM is an assessment assistant; the application assigns all authoritative metadata (`claim_id`, `section_id`, `proposition`, `risk_level`, `considered_evidence_ids`, `assessed_at`).
- For each claim, only evidence IDs explicitly linked to that claim (and verified in the run's `ValidatedEvidenceCatalog`) are supplied in the prompt under `PERMITTED EVIDENCE`.
- Validation checks:
  1. Exactly 1-to-1 claim mapping (no duplicate claim IDs, no missing claim IDs, no fabricated claim IDs).
  2. Evidence provenance: every returned evidence ID in `supporting_evidence_ids`, `contradicting_evidence_ids`, and `contextual_evidence_ids` must belong to the catalog AND must have been supplied for that specific claim.
  3. Status values must belong to the approved 6-status taxonomy.
  4. Non-empty explanation strings.

### 6.4 Failure Isolation & Bounded Corrective Retry
- If schema validation or provenance verification fails, exactly one bounded corrective retry is executed with detailed feedback (`--- CORRECTION REQUIRED ---`).
- If the retry fails or the LLM encounters a connection/timeout error:
  - The failure is isolated strictly to that specific batch.
  - Claims in that batch are marked `not_checked` with an explicit reason (`provider_unavailable`, `provider_timeout`, `malformed_response`, or `assessment_failed`).
  - Successfully assessed claims from preceding and subsequent batches are preserved in full.
  - Provider failure is **never** converted into `unsupported` or `contradicted`.

### 6.5 Creative & Rhetorical Text Handling
- Claims flagged with `risk_level = "creative_flexibility"` are prompted with explicit guidance: metaphors, analogies, and rhetorical flourishes are not treated as contradicted unless they make an explicit false factual assertion. If acceptable stylistic expressions, they are assessed as `supported` with `recommended_action = "keep"`.

### 6.6 Verification
- Unit test suite: `backend/tests/test_script_fact_checker.py` (27 tests covering all 6 statuses, creative text, unresearched mode, duplicate/missing/fabricated IDs, batch splitting, partial failure isolation, timeouts, and provider compatibility).
- Full regression suite: **290 tests passing**, 1 warning.

---

## 7. Checkpoint 3: Surgical Repair Engine (`ScriptRepairer`)

Implemented in `backend/app/services/scriptwriter/repairer.py`.

### 7.1 Architecture & Core Components
- **Public API:** `ScriptRepairer.repair_script(script, report, catalog=None, all_known_claim_ids=None, fail_fast=False) -> ScriptRepairResult`.
- **Result Object:** `ScriptRepairResult` contains `repaired_script: Script`, `fact_check_report: ScriptFactCheckReport`, `repair_actions: List[ClaimRepairActionRecord]`, `attempted_claims_count: int`, `resolved_claims_count: int`, `unresolved_claims_count: int`, `is_fully_resolved: bool`.
- **Structured Payloads:** `ClaimRepairPayload` and `SectionRepairPayload` define the structured output contract across the LLM boundary for section-level surgical editing.
- **Typed Exceptions:**
  - `ScriptRepairError(Exception)`: Root base exception.
  - `ScriptRepairSecurityError(ScriptRepairError)`: Project isolation or run mismatch violations.
  - `ScriptRepairUnavailableError(ScriptRepairError)`: LLM connection failure or timeout.
  - `ScriptRepairMalformedError(ScriptRepairError)`: Schema or JSON syntax failure after retry.

### 7.2 Approved Repair Policy Execution
| Assessment | Behavior in ScriptRepairer |
|---|---|
| `supported` | Untouched. Zero repair calls made. |
| `partially_supported` | Attempt qualification or narrowing against considered evidence. |
| `conflicting` | Preserve uncertainty; not targeted for automated repair. |
| `unsupported` | Attempt correction using permitted alternative evidence, or remove assertion. |
| `contradicted` | Attempt correction using permitted alternative evidence; otherwise remove assertion. |
| `not_checked` | Preserved and flagged; zero repair calls made. |

### 7.3 Minimality, Content Preservation & Claim Identity
- **Section-Level Surgical Scoping:** Only sections containing target claims are processed. All other sections have their narration, headings, and metadata preserved verbatim.
- **Claim Identity Preservation:** Repaired propositions are compared against the original proposition using `_is_same_factual_proposition()`. If the proposition meaning is unchanged (harmless qualification/wording), the claim ID is preserved. If the factual meaning changes, a new unique claim ID (`clm_NN`) is allocated.
- **Bidirectional Consistency:** Claim-to-section (`claim.section_id`) and section-to-claim (`section.claim_ids`) mappings are authoritatively rebuilt. Removed claims are eliminated from `section.claim_ids` and `script.claims`.
- **Original Risk Preservation:** The original `risk_level` is carried forward to the repaired claim.

### 7.4 Single-Pass Rechecking Without Recursive Loops
- Changed/replaced claims are rechecked using the existing `ScriptFactChecker` on a lightweight mini-script containing only the affected sections and changed claims.
- Assessments for untouched claims are preserved from the original report.
- A failed recheck marks the claim `not_checked`, never false success.
- Exactly one repair pass followed by one recheck pass is performed. No recursive repair loops.

### 7.5 Verification
- Unit test suite: `backend/tests/test_script_repairer.py` (19 tests covering qualification, correction, removal, preservation of supported/conflicting/not_checked, new claim IDs, evidence provenance, malformed retries, provider failures, rechecking, and bidirectional mappings).
- Phase 5 combined test suite: **66 passed in 0.79s**.
- Total repository regression suite: **309 tests passing**, 1 warning.

---

## 8. Checkpoint 4: Service Lifecycle Integration

Integrated into `ScriptService` (`backend/app/services/scriptwriter/service.py`) alongside `ScriptFactChecker` and `ScriptRepairer`.

### 8.1 Mandatory Requirement A: Independent Evidence Coverage
- **Candidate Discovery:** Implemented `retrieve_candidate_evidence_for_claim(claim, catalog, max_candidates=5)` in `ScriptFactChecker`.
- **Criteria & Contradiction Discovery:**
  - Matches proposition content tokens against catalog evidence excerpts, topics, and source titles.
  - Number & date matching: identical figures boost support relevance (+5), while mismatched figures on matching topics boost potential contradiction discovery (+7).
  - Antonym heuristics: detects pairs such as `(increased, decreased)`, `(above, below)`, `(hotter, colder)`, `(positive, negative)` to retrieve refuting evidence (+8).
  - Negation mismatch heuristics: retrieves opposing assertions (+6).
- **Bounded & Deterministic:** Returns up to `max_candidates` (default 5) sorted by score descending and evidence ID ascending without external search infrastructure dependencies.
- **Provenance Safe:** Discovered evidence records are added to `claim_supplied_eids`, allowing the fact checker to discover and cite unlinked supporting or contradictory findings while still enforcing provenance constraints.

### 8.2 Mandatory Requirement B: Application-Controlled Risk Classification
- Implemented `classify_claim_risk(claim_text, section_type, is_central_thesis)` in `backend/app/services/scriptwriter/classifier.py`.
- **Classification Rules:**
  - `high`: Central thesis assertions, statistics, percentages, numerical values, dates/years/months, scientific/causal mechanisms (`causes`, `temperature`, `pascals`, `gravity`, `dna`, `mitochondria`, etc.), and named entities (multi-word capitalized proper nouns).
  - `creative_flexibility`: Pure stylistic metaphors (`imagine`, `like a dance`, `cosmic tapestry`), analogies, and rhetorical questions.
  - `normal`: Standard explanatory narrative assertions.
  - **Mixed Assertions:** Metaphorical framing that also embeds concrete numbers, dates, or scientific assertions is strictly prioritized as `high`.
- **Lifecycle Integration:**
  - Initial generation: `ScriptDraftGenerator` applies `classify_claim_risk` to assign explicit risk levels to all initial claims.
  - Revision reconciliation: `ScriptService._reconcile_revision_claims` preserves the existing `risk_level` on matched claims (high risk is never silently downgraded to normal). New claims are classified deterministically.

### 8.3 Mandatory Requirement C: Surgical Sentence Preservation & Exact-Span Enforcement
- **Programmatic Replacement-Target:**
  - `ClaimRepairPayload` includes mandatory `target_span` (exact verbatim sentence or phrase from original narration) and `replacement_span` (mandatory for `qualify` and `rephrase`; optional/empty for `remove`).
  - `ScriptRepairer._validate_section_repair_response` rejects payloads where `target_span` is missing, empty, not present in section narration, or occurs multiple times (ambiguous span).
  - `ScriptRepairer._apply_section_repairs` calculates pairwise non-overlapping spans across target claims within a section. Overlapping spans are rejected without modifying narration for those claims.
  - Non-overlapping repairs are applied in descending order of start offset (right-to-left), ensuring earlier offsets remain strictly stable during multi-claim repairs (e.g. simultaneous remove and qualify).
  - Whitespace and punctuation artifacts (duplicate spaces, orphaned punctuation) are cleaned programmatically.
  - `revised_narration` fallback is deleted entirely. If span extraction or matching fails, the section's narration is preserved 100% untouched and the target claims remain unresolved.

### 8.4 Automated End-to-End Service Lifecycle & Strict Persistence Ordering
- **Strict 7-Step Atomic Commit Sequence (`_commit_script_version`):**
  1. Validate candidate script (`Script.model_validate`, `script.validate_content`).
  2. Validate corresponding fact-check report (`ScriptFactCheckReport.model_validate`, project and version match).
  3. Persist immutable script snapshot (`versions/vNNN.json`).
  4. Persist matching immutable fact-check report (`fact_checks/vNNN.json`).
  5. Update active `current.json` pointer.
  6. Update workflow state (`workflow_state.json`, resetting accepted/rejected version).
  7. Emit conversation events (`conversation.json`).
  - **Invariant:** The active `current.json` pointer never references a script version whose corresponding fact-check report has not been persisted successfully. If snapshot or report writing fails, `current.json` remains pointing to the previous valid version.
- **Fail-Closed Finalization & Report Retrieval:**
  - `finalize_script` enforces that a valid fact-check report matching the current draft version is mandatory.
  - If missing, mismatched (version/project), or malformed, finalization raises `ScriptFinalizationError`.
  - Legacy exception is strictly bounded: applies only if the script has 0 claims AND no fact-check reports exist anywhere in the project.
  - `get_current_fact_check_report` returns `None` only when no report file exists; if a report file exists on disk and is malformed, it fails closed by raising `ScriptStorageError`.
- **Unresearched High-Risk Claim Readiness Policy:**
  - High-risk factual claims in unresearched scripts (e.g. statistics, numerical measurements, causal claims) block readiness (`is_ready = False`) and finalization with an explicit explanation: `"unresearched high-risk claim(s) require research grounding"`.
  - Normal-risk and creative-flexibility claims in unresearched mode remain non-blocking flagged warnings.

### 8.5 Verification & Test Results
- Phase 5 Checkpoint 4 lifecycle & corrective test suite: `backend/tests/test_phase5_checkpoint4_lifecycle.py` (**29 tests passing**).
- Script repairer test suite: `backend/tests/test_script_repairer.py` (**20 tests passing**).
- Script fact-checker test suite: `backend/tests/test_script_fact_checker.py` (**27 tests passing**).
- Fact-check domain & storage contracts: `backend/tests/test_fact_check_contracts_and_storage.py` (**18 tests passing**).
- Import boundary architectural rules: `backend/tests/test_import_rules.py` (**2 tests passing, 0 violations**).
- Full backend regression suite: **339 passed, 1 warning in 39.83s**.
- Frontend production build: **Passed (zero errors)**.

### 8.6 Research-Run Provenance & Exact-Span Failure Hardening (Final Corrections)
- **Provenance Validation in Version Commit (`_commit_script_version`):**
  - Enforces `report.research_run_id == script.research_run_id` as Step 2 of the commit sequence, before any disk snapshots or pointer updates.
  - Rejection with `ScriptValidationError` occurs if:
    - `script.research_run_id` is set and `report.research_run_id` is missing/None or different.
    - `report.research_run_id` is set and `script.research_run_id` is missing/None or different.
  - Acceptance occurs only when both are identical strings or both are `None` (authorized unresearched drafts).
- **Independent Provenance Enforcement in Finalization (`finalize_script`):**
  - Independently re-verifies `fc_report.research_run_id == current.research_run_id`.
  - Rejection with `ScriptFinalizationError` occurs if:
    - Report research-run ID does not match the script's research-run ID.
    - Script is a researched script (`current.research_run_id is not None`) but the fact-check report has no `research_run_id`.
- **Narrowed Legacy Finalization Bypass:**
  - Strictly bounded to: `len(current.claims) == 0 and not any(dirs["fact_checks_dir"].glob("v*.json")) and current.research_run_id is None`.
  - Researched scripts without a report are rejected even if they have 0 claims.
- **Two-Stage Exact-Span Failure Verification:**
  - Added dedicated regression test `test_two_stage_exact_span_failure_end_to_end_regression` in `backend/tests/test_script_repairer.py` verifying that when both initial repair and single retry emit invalid spans, execution completes gracefully: original section narration preserved verbatim, no whole-section fallback, claims remain unresolved, previous fact-check status retained, and readiness gates closed.



