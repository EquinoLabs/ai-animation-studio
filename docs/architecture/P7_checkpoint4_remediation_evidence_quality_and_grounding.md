# Phase 7 Checkpoint 4B Remediation: Evidence Quality, Grounding Integrity, Coverage, and Duration

**Status:** Implemented & Verified  
**Date:** 2026-11-05  
**Domain:** Research Orchestration, Scriptwriter Policy & Generator, Fact Checker, Contracts  

---

## 1. Overview & Context

During the initial Phase 7 Checkpoint 4B live research test with external providers (Tavily search and Firecrawl extraction), the application successfully executed the end-to-end research-grounded Script Builder workflow. However, the subsequent verification audit uncovered critical defects in evidence quality, claim grounding, research queueing, and duration reporting:

1. **Evidence Quality Defects:** The extractor captured bot verification notices, cookie consent gates, navigation links, and article metadata/bylines, treating them as valid evidence records.
2. **Grounding Integrity Defects:** The fact checker marked claims as `"supported"` based solely on source title keywords or parametric knowledge, even when the underlying excerpt contained only navigation chrome or bot challenges.
3. **Extraction Coverage Skew:** Extractor calls were consumed in sequential order from the first query's search results, starving subsequent research queries of extraction budget.
4. **Duration Deficit Masking:** The generated script achieved only 186 words (~80s narration) against a requested 5-minute target without explicit reporting or categorization of the shortfall.

This document details the architectural rework implemented to remediate these defects across four core workstreams.

---

## 2. Architectural Remediation Workstreams

```
                                      [ Research Orchestration ]
                                                  │
                 ┌────────────────────────────────┴────────────────────────────────┐
                 ▼                                                                 ▼
      [ Stage 1: Round-Robin Queue ]                                    [ Stage 2: Substantive Extraction ]
      • Interleave query candidates                                     • Multi-factor paragraph scoring
      • Preserve canonical deduplication                                • Reject bot gates & cookie notices
      • Enforce max_extract_calls budget                                • Reject navigation chrome & bylines
                 │                                                      • Zero evidence records on invalid content
                 ▼                                                                 │
      [ Evidence Catalog & Storage ] ◄─────────────────────────────────────────────┘
                 │
                 ▼
      [ Script Generation & Policy ] ──────────────────────────────────────────────┐
      • Enforce anti-filler directives                                             │
      • Legitimate educational depth guidance                                      ▼
      • Pacing & duration target computation                           [ Claim-Level Fact Checking ]
      • Duration compliance classification                             • Explicit excerpt vs title framing
        - compliant                                                    • Ban title inference / parametric facts
        - under_target_evidence_deficit                                • Code-level citation validation
        - under_target_generator_shortfall                             • Authoritative downgrade of invalid claims
```

### 2.1 Workstream A: Substantive Evidence Extraction Quality (P0)

Implemented in [`backend/app/services/research/orchestrator.py`](../../backend/app/services/research/orchestrator.py):

- **Gatekeeper & Challenge Detection (`is_challenge_or_gatekeeper`):**
  Matches regex signatures for Cloudflare, Ray ID, cookie notices, JavaScript requirements, access denied, and 403 Forbidden pages. Fast-rejects entire pages under 500 characters dominated by challenge text.
- **Navigation Chrome Detection (`is_navigation_or_chrome`):**
  Calculates link density (ratio of markdown link characters to total characters) and detects navigation keywords (`home`, `about`, `terms`, `privacy`, `contact`). Rejects blocks with >35% link density or multiple links with low prose count.
- **Byline & Metadata Filtering (`is_byline_or_metadata`):**
  Identifies image tags, reading-time badges (`5 min read`), view counters (`149 views`), and AI generation notices (`AI generated...`) lacking substantive prose.
- **Paragraph Quality Scoring (`score_candidate_paragraph`):**
  - Evaluates candidate paragraphs across length, sentence boundaries, punctuation, and structural headings.
  - Rejects blocks with disallowed headings (`cookie`, `menu`, `privacy policy`, `advertisement`, `related articles`).
  - Rejects fragments under 4 words or short passages (<15 words) lacking terminal sentence punctuation.
  - Awards bonuses to well-formed prose (ideal range: 30–150 words) with proper sentence termination.
- **Zero-Record Invariant on Extraction Failure:**
  In `extract_verbatim_evidence()`, if no candidate paragraph achieves a score > 0, returns `(None, None)`. When `excerpt is None`, `ResearchOrchestrator.execute_plan()` updates the source record with `retrieval_status="unavailable"` and creates **zero** evidence records, preventing garbage citations in downstream pipelines.

### 2.2 Workstream B: Claim Grounding Integrity (P0)

Implemented in [`backend/app/services/scriptwriter/fact_checker.py`](../../backend/app/services/scriptwriter/fact_checker.py):

- **Prompt Boundary Hardening (`FACT_CHECK_SYSTEM_PROMPT`):**
  - Strictly instructs the LLM that a claim can only be marked `"supported"` if the proposition is directly stated or strictly entailed by the verbatim `Excerpt`.
  - Forbids inferring factual support from source titles (`source_title` is provided only as contextual metadata).
  - Explicitly prohibits using parametric knowledge to substantiate ungrounded claims.
- **Batch Prompt Formatting (`_build_batch_prompt`):**
  Formats evidence blocks with clear separation:
  `ID: {eid} | Excerpt: "{excerpt}" | (Context: Source title is '{source_title}')`
- **Code-Level Verification (`_validate_batch_response` & `_build_claim_assessments`):**
  - Requires non-empty `supporting_evidence_ids` for any assessment marked `"supported"`.
  - Detects and rejects citations referencing cookie notices, bot challenges, or navigation chrome.
  - Authoritatively downgrades claims supported by invalid excerpts to `status="unsupported"`, clears `supporting_evidence_ids=[]`, and sets `recommended_action="Remove or revise claim..."`.
  - Catches title inference where claim keywords appear in the title but have zero overlap with the excerpt.

### 2.3 Workstream C: Round-Robin Extraction Coverage (P1)

Implemented in [`backend/app/services/research/orchestrator.py`](../../backend/app/services/research/orchestrator.py):

- **Query-Balanced Queue Construction:**
  During Stage 1 search, discovered sources are partitioned by `query_id`: `query_sources[query.query_id]`.
- **Deterministic Round-Robin Interleaving:**
  Before Stage 2 extraction, candidates are pulled round-robin across all planned queries (e.g., Query 1 candidate 0, Query 2 candidate 0, Query 3 candidate 0, Query 1 candidate 1, ...).
- **Deduplication & Budget Compliance:**
  Maintains `seen_queue_sources` set to ensure no canonical URL is queued or extracted twice. Hard-stops extraction when `max_extract_calls` is reached, ensuring diverse multi-query research coverage without exceeding API limits.

### 2.4 Workstream D: Script Duration Compliance & Deficit Reporting (P1)

Implemented in [`backend/app/domain/contracts.py`](../../backend/app/domain/contracts.py), [`backend/app/domain/script.py`](../../backend/app/domain/script.py), [`backend/app/services/scriptwriter/policy.py`](../../backend/app/services/scriptwriter/policy.py), and [`backend/app/services/scriptwriter/generator.py`](../../backend/app/services/scriptwriter/generator.py):

- **Duration Compliance Model (`DurationComplianceReport`):**
  ```python
  DurationComplianceStatus = Literal[
      "compliant",
      "under_target_evidence_deficit",
      "under_target_generator_shortfall",
      "under_target_undetermined",
  ]

  class DurationComplianceReport(BaseModel):
      target_duration_minutes: float
      target_words: int
      actual_words: int
      min_target_words: int
      max_target_words: int
      deficit_words: int
      compliance_status: DurationComplianceStatus
      assessment_note: str
  ```
- **Auditing & Classification Logic (`populate_script_duration_metrics`):**
  - Scripts meeting >= 70% of target word count (`actual_words >= min_target_words`) are classified as `"compliant"`.
  - When under target, inspects available evidence records, distinct sources, query coverage, and total excerpt words:
    * **No Usable Evidence:** 0 records or 0 excerpt words -> `"under_target_evidence_deficit"`.
    * **Repetitive or Narrow Evidence:** >= 2 records but single source, single query, < 60 words, or low vocabulary diversity -> `"under_target_evidence_deficit"`.
    * **Adequate Research with Generator Brevity:** Substantive records across >= 2 sources and >= 2 queries with >= 100 excerpt words -> `"under_target_generator_shortfall"`.
    * **Borderline or Missing Evidence Metadata:** Ambiguous counts or missing catalog metadata -> `"under_target_undetermined"`, reporting uncertainty honestly rather than manufacturing an unfounded cause.
- **Frontend Integration:**
  - TypeScript contracts (`frontend/src/types.ts`) expose `DurationComplianceStatus` and `DurationComplianceReport` on `Script`.
  - Script Workspace UI (`frontend/src/components/ScriptViewer.tsx`) renders a dedicated duration status badge, displaying actual vs target words, shortfall count, and explanatory notes, with distinct color-coded indicators (green for compliant, amber for evidence deficit, red for generator shortfall, purple for undetermined).
- **Legitimate Educational Depth Directives:**
  Added Section 2 to `ANTI_FILLER_DIRECTIVES` in `policy.py`:
  Instructs the generator to expand runtime legitimately without empty filler by breaking down mechanisms, contrasting concepts with analogies (`"Think of it like..."`), and explaining causal steps in depth.

---

## 3. Test Coverage & Verification

All remediation behaviors and adversarial checks are validated with 24 comprehensive unit tests in [`backend/tests/test_phase7_checkpoint4_remediation.py`](../../backend/tests/test_phase7_checkpoint4_remediation.py):

| Test Case | Workstream | Verifies |
|---|---|---|
| `test_extract_verbatim_evidence_rejects_cookie_gate` | Evidence Quality | Cookie notice returns `(None, None)` |
| `test_extract_verbatim_evidence_rejects_bot_challenge_and_access_denied` | Evidence Quality | Cloudflare / 403 pages return `(None, None)` |
| `test_extract_verbatim_evidence_rejects_navigation_chrome` | Evidence Quality | Link-dense navigation chrome returns `(None, None)` |
| `test_extract_verbatim_evidence_rejects_byline_metadata` | Evidence Quality | Reading times and author metadata return `(None, None)` |
| `test_extract_verbatim_evidence_accepts_valid_long_form_prose` | Evidence Quality | Substantive article body extracted with heading locator |
| `test_extract_verbatim_evidence_accepts_concise_substantive_definitions` | Evidence Quality | Valid short scientific definitions extracted |
| `test_orchestrator_creates_zero_evidence_for_rejected_sources` | Evidence Quality | Source marked `unavailable`, 0 evidence records created |
| `test_fact_checker_rejects_cookie_or_chrome_evidence` | Grounding | Claims referencing gatekeeper text downgraded to unsupported |
| `test_fact_checker_rejects_title_inference_without_excerpt_support` | Grounding | Claims inferring facts from title alone downgraded to unsupported |
| `test_fact_checker_safely_handles_malformed_llm_assessments` | Grounding | Safe fallback to unsupported on invalid evidence IDs |
| `test_round_robin_extraction_queue_across_queries` | Scheduler | Interleaved candidate extraction across queries 1, 2, 3 |
| `test_round_robin_deduplication_and_budget_enforcement` | Scheduler | Duplicate URLs deduplicated and budget limit enforced |
| `test_duration_compliance_compliant_script` | Duration | Script within acceptable duration range marked compliant |
| `test_duration_compliance_evidence_deficit` | Duration | Short script with < 2 evidence records marked evidence deficit |
| `test_duration_compliance_does_not_alter_factual_readiness` | Duration | Duration deficit does not block factual readiness |
| `test_adversarial_legitimate_prose_does_not_support_unrelated_aristotle_claim` | Grounding (Adversarial) | Modern sociology prose does not support ancient philosopher claim |
| `test_adversarial_topically_related_incidental_overlap_does_not_support_claim` | Grounding (Adversarial) | Shared words ("human", "brain") do not support unverified mechanism |
| `test_adversarial_compound_claim_where_only_one_proposition_supported` | Grounding (Adversarial) | Compound claim marked `partially_supported`, blocking readiness |
| `test_adversarial_valid_paraphrase_genuinely_supports_claim` | Grounding (Adversarial) | Semantic paraphrase passes validation without false rejection |
| `test_adversarial_excerpt_supports_narrower_statement_not_broader_universal_claim` | Grounding (Adversarial) | Narrow study average cannot support universal claim (`partially_supported`) |
| `test_duration_classification_no_usable_evidence` | Duration (Multi-Factor) | 0 usable evidence classified as evidence deficit |
| `test_duration_classification_repetitive_or_narrow_evidence` | Duration (Multi-Factor) | Single-source / single-query repetitive evidence classified as evidence deficit |
| `test_duration_classification_adequate_coverage_with_generator_shortfall` | Duration (Multi-Factor) | Multi-source, multi-query adequate evidence classified as generator shortfall |
| `test_duration_classification_insufficient_information_reports_undetermined` | Duration (Multi-Factor) | Missing evidence metadata classified honestly as undetermined |

---

## 4. Operational Safety and Baseline Preservation

- **No External Network Calls:** All remediation implementation and verification were conducted locally using unit mocks and deterministic data fixtures.
- **Baseline Preservation:** Project `p7_live_validation_001` was preserved entirely untouched as the historical baseline for audit comparisons.
