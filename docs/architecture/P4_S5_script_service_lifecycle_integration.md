# P4-S5: ScriptService Lifecycle Integration (Checkpoints 1 & 2)

## Executive Summary

Phase 4 Stage 5 (P4-S5) integrates the evidence-aware `ScriptOutliner` (P4-S3) and `ScriptDraftGenerator` (P4-S4) into the authoritative `ScriptService` lifecycle, preserving project isolation, backward compatibility, version allocation guarantees, persistence safety, and revision claim identity & evidence integrity.

---

## 1. Architectural Changes

### 1.1 ScriptService Orchestration (Checkpoint 1)
- Injected `ScriptOutliner` and `ScriptDraftGenerator` as first-class collaborators within `ScriptService`.
- Replaced legacy mock drafting with the two-stage pipeline:
  1. **Outline generation/selection**: Standalone `ScriptOutline` creation or validation.
  2. **Script draft generation**: Narrative draft expansion with run-scoped claim/evidence linkage and duration estimation.
- Persisted standalone outlines under `projects/<id>/script/outlines/<outline_id>.json`.
- Implemented `create_outline` and `get_outline` on `ScriptService` and delegated via `PipelineOrchestrator`.

### 1.2 Strict Research-Run Selection Policy (Checkpoint 1)
- **No research runs**: Calling `start_script(project_id, topic)` raises `ScriptValidationError`. It refuses to silently generate an unresearched script.
- **Explicit unresearched authorization**: Calling `start_script(..., allow_unresearched=True)` explicitly bypasses research grounding.
- **Latest completed run selection**: When completed research runs exist and `research_run_id` is omitted, the outliner selects the latest completed run by `started_at` descending.
- **Rejection of incomplete/failed runs**: If the newest run is incomplete or failed, `start_script` rejects with `ScriptValidationError` without silent fallback to older runs or unresearched mode.
- **Unresearched override**: Explicit `allow_unresearched=True` overrides available research runs.

### 1.3 Version Allocation & Failure Safety (Checkpoint 1)
- Centralized `_allocate_next_version(dirs, current_version=None)` scanning both `projects/<id>/script/versions/vNNN.json` and `projects/<id>/script/finalized/vNNN.json`.
- Next version is computed as:
  $$\text{version} = \max(\text{existing\_versions} \cup \text{finalized\_versions}) + 1$$
- Existing version snapshots are never overwritten; existence of target `vNNN.json` triggers a `ScriptStorageError`.
- Draft validation occurs completely in memory before writing to disk; failures preserve the active working draft and previous snapshots without corruption.

### 1.4 Revision Claim Identity & Evidence Integrity (Checkpoint 2)
- **Conservative Claim Identity Policy**:
  - `_is_same_factual_proposition` evaluates normalized proposition text, matching exact normalized strings and equivalent significant content tokens.
  - Number mismatches and negation differences immediately indicate material changes, returning `False`.
  - Claims demonstrating the same factual proposition retain their prior `claim_id` across revisions and sections.
  - Materially modified or new claims receive newly allocated, unique claim IDs (`clm_NN`) that avoid collisions with all current and historical version IDs.
  - Unchanged claims retain valid evidence links; new or materially modified claims **never** inherit prior evidence links.
- **Evidence Catalog Validation & Corrective Retry**:
  - For researched scripts, cited evidence IDs in `raw_revised.claims` are validated against `evidence_catalog.evidence_by_id`.
  - When invalid evidence IDs are detected, at most one bounded corrective retry is performed with an explicit allowed evidence ID list injected into the revision prompt.
  - If invalid evidence IDs persist after retry, `ScriptRevisionEvidenceValidationError` is raised.
- **Authoritative Source ID Derivation**:
  - Model-emitted `source_ids` are completely ignored.
  - `claim.source_ids` are derived authoritatively from validated `EvidenceRecord`s in the run's catalog:
    $$\text{source\_ids} = \text{unique}(\{\text{catalog.evidence\_by\_id}[eid].\text{source\_id} \mid eid \in \text{valid\_evidence\_ids}\})$$
- **Unresearched Revision Guardrails**:
  - Unresearched scripts enforce empty `evidence_ids` and `source_ids`.
  - Model-emitted evidence IDs in unresearched revisions are rejected with `ScriptRevisionEvidenceValidationError`.
- **Bidirectional Section-Claim Consistency**:
  - `claim.section_id` validated against active revised sections.
  - `section.claim_ids` populated strictly from claims referencing that section.

---

## 2. Verification & Test Suite

### 2.1 Test Suite Breakdown
- `backend/tests/test_script_revision_integrity.py`: 14 focused tests verifying claim identity preservation, material modification re-keying, non-inheritance of evidence, unique ID allocation, single corrective retry on invalid evidence, authoritative source derivation, bidirectional consistency, unresearched guardrails, and legacy compatibility.
- `backend/tests/test_script_service_lifecycle.py`: 17 focused lifecycle integration tests.
- `backend/tests/test_script_builder.py`: 16 tests covering generation, revision, acceptance, finalization, persistence, and CLI.
- `backend/tests/test_script_quality.py`: 8 tests verifying duration targets, metrics, and soft pacing.
- `backend/tests/test_script_verification.py`: 19 tests verifying fault injection, immutability, state transitions, and recovery.
- `backend/tests/test_api.py`: 5 tests covering REST endpoints, error mapping, and lifecycle calls.
- Total test suite: **243 tests passing** with 0 errors across the entire repository.
- Frontend: `npm run build` passing cleanly.

---

## 3. Scope Boundaries

- **Completed in Checkpoint 1 & 2**: Service integration, initial-draft persistence, research-run selection, outline lifecycle, version allocation, pre-persistence failure safety, revision claim identity preservation, evidence catalog validation with bounded corrective retry, authoritative source derivation, and bidirectional section linkage.
- **Phase 5/6 Boundary**: Substantive fact-checking, claim repair, and UI claim challenges remain strictly out of scope for Phase 4.
