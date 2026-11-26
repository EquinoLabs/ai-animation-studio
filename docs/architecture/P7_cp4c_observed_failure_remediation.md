# Phase 7 — CP4C Observed-Failure Remediation Report

**To:** Project Lead  
**From:** Sole Employee  
**Status:** Offline Remediation Complete — Ready for Review

---

## 1. Executive Summary & Objective

Following the live regression run `p7_live_validation_007`, the deterministic grounding and fact-checking layer demonstrated notable improvements in preventing unevidenced claims from falsely qualifying as `supported` (correctly identifying unsupported assertions regarding dopamine, basal ganglia, and prefrontal cortex, and returning `is_ready: false`).

However, the live run exposed three critical operational defects:
1. **ScriptRepairer Deserialization & Validation Failure:** When Qwen2.5:7b omitted required fields like `target_span` or `explanation`, Pydantic deserialization raised validation errors, and the repairer was unable to resolve the claims, leaving all 7 target claims unresolved across versions.
2. **Off-Topic Research Extraction:** Tavily/Firecrawl returned an irrelevant source (`src_9411f6a8f84f`, Prepp.in exam prep for child development) due to query keyword overlap with "environmental factors" and "development", leaving a topical evidence gap.
3. **Overreaching Script Generation:** The script generator introduced specific neurobiological mechanisms (dopamine, basal ganglia, prefrontal cortex) not supported by the evidence brief.

Under the Project Lead's directive, strict offline remediation has been executed, verified, and audited across 499 backend unit/integration tests and 18 frontend tests.

---

## 2. Root Cause Analysis & Implemented Solutions

### Defect 1: ScriptRepairer Reliability & Contract Handling
- **Root Cause:**
  - `ClaimRepairPayload` had mandatory `target_span: str = Field(...)` and `explanation: str = Field(...)`. When the LLM omitted `explanation` or `target_span`, Pydantic failed deserialization immediately with an unhandled schema error rather than passing parsed data to domain validation.
  - Furthermore, whole-section replacement attempts were not cleanly separated from field-omission recovery.
- **Remediation:**
  - In `backend/app/services/scriptwriter/repairer.py`:
    - Updated `ClaimRepairPayload`: `explanation: Optional[str]` now defaults to `"Surgical factual repair applied per fact-check assessment."`, with an empty-string validator fallback.
    - Updated `ClaimRepairPayload.target_span: Optional[str] = Field(default=None)`.
    - Maintained strict validation in `_validate_section_repair_response`: if `target_span` is missing or empty, validation rejects the payload with `"Claim '{r.claim_id}' is missing mandatory target_span."` without silent acceptance.
    - Added explicit formatting requirements and JSON examples to `_build_section_repair_prompt` and `_execute_section_corrective_retry` informing the model that `target_span` is strictly mandatory and must match original narration sentences verbatim.
    - Ensured unresolved claims and original section narration are safely retained if corrective retry also fails, preventing silent corruption or dropping of claims.
  - **Tests Added/Updated:**
    - `backend/tests/test_script_repairer.py`: `test_missing_optional_fields_repaired_successfully_regression` verifies safe deserialization when fields are omitted, corrective retry execution when `target_span` is missing, and safe recheck on successful retry.
    - Verified `test_surgical_repair_rejects_missing_target_span_and_whole_section_replacement` in `test_phase5_checkpoint4_lifecycle.py` passes.

### Defect 2: Topical Anchor Gating in Evidence Selection
- **Root Cause:**
  - In `backend/app/domain/research.py`, evidence paragraph scoring scored candidate paragraphs based on general word overlap with queries.
  - A query like `"environmental factors influencing development of habits"` matched a child-development syllabus because the paragraph heavily discussed "environmental", "factors", and "development", despite containing zero discussion of habits.
- **Remediation:**
  - Added `extract_topic_tokens_and_stems` and `check_topical_relevance` to `backend/app/domain/research.py`.
  - Implemented a **Primary Topic Anchor Gate**:
    - Candidate paragraphs must contain at least one primary topic anchor stem (e.g., `habit`, `habits`, `habitual` when researching habit formation), OR demonstrate deep technical multi-term convergence (`len(matched_specific_roots) >= 3`) across distinct query concepts (e.g. striatum, conditioning, automaticity).
    - Off-topic passages (e.g. child development, crop cultivation) lacking both topic anchor roots and query convergence receive an immediate `-100.0` score penalty, preventing selection into the evidence catalog.
  - Wired `topic`, `target_question`, and `query_text` into `extract_verbatim_evidence` and `score_candidate_paragraph` across `backend/app/services/research/orchestrator.py`.

### Defect 3: Evidence-Constrained Script Generation
- **Root Cause:**
  - The section generator prompt urged depth and scientific rigor, which encouraged Qwen2.5:7b to invoke its pretrained knowledge of neuroanatomy (dopamine, basal ganglia, prefrontal cortex) even when absent from retrieved excerpts.
- **Remediation:**
  - In `backend/app/services/scriptwriter/policy.py`:
    - Injected strict negative constraints into both full draft and section generation prompts:
      - `CRITICAL CONSTRAINT: DO NOT INVENT SPECIFIC SCIENTIFIC MECHANISMS, NEUROTRANSMITTERS, BRAIN STRUCTURES, OR CLINICAL STUDIES NOT DIRECTLY MENTIONED IN THE EVIDENCE PASSAGES.`
      - `PRETRAINED KNOWLEDGE IS NOT EVIDENCE. IF THE RETRIEVED EXCERPTS DO NOT EXPLICITLY NAME A SPECIFIC NEUROTRANSMITTER (e.g. dopamine) OR ANATOMICAL REGION (e.g. basal ganglia), YOU MUST NEVER INTRODUCE OR CITE THEM.`
  - In `backend/app/services/scriptwriter/fact_checker.py`:
    - Enhanced attribution matching by sorting known entities by descending word and character length, preventing sub-entity shadowing (e.g. `"ann graybiel"` correctly matched rather than prematurely matching `"graybiel"`).

---

## 3. Verification & Regression Suite Outcomes

1. **Full Backend Test Suite:**
   - Command: `backend/.venv/Scripts/python -m pytest backend/tests`
   - Outcome: **499 passed**, 0 failed (46.25s).
2. **Key Targeted Regression Tests:**
   - `backend/tests/test_script_repairer.py`: 21 passed.
   - `backend/tests/test_phase5_checkpoint4_lifecycle.py`: 29 passed.
   - `backend/tests/test_phase7_cp4c_remediation_isolation.py`: 37 passed.
   - `backend/tests/test_phase7_cp4c_grounding_semantic_overclaim.py`: 35 passed.
   - `backend/tests/test_phase6_checkpoint1_api_and_challenge.py`: 16 passed.
   - `backend/tests/test_phase7_checkpoint4_remediation.py`: 24 passed.
3. **Frontend Test Suite:**
   - Command: `npm --prefix frontend test -- --run`
   - Outcome: **18 passed**, 0 failed (8 skipped due to offline dev server).

---

## 4. Current State & Next Steps

All changes are strictly offline and adhere to the project directives:
- No live search or extraction calls were made.
- No servers or background daemons are running.
- `.env` and external configurations were untouched.
- Ready for Project Lead review and authorization before initiating any subsequent live regression run (`p7_live_validation_008`).
