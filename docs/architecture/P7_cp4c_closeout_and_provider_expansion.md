# Phase 7 — CP4C Closeout: Writing Policy Correction & Research Provider Expansion

**To:** Project Lead  
**From:** Sole Employee  
**Date:** November 26, 2026  
**Status:** Completed (Offline Implementation and Testing Only)

---

## 1. Executive Summary & Context

Following the completion of Live Regression `p7_live_validation_009` and its read-only artifact review, this phase closed out Checkpoint 4C (CP4C) by resolving two specific writing-policy defects, verifying offline regressions, and executing the planned research provider expansion across three public research providers: **Wikimedia**, **Internet Archive**, and **OpenAlex**.

All changes were implemented and tested strictly offline. No live provider requests or unauthorized server invocations were made.

---

## 2. Minimal Writing-Policy Correction

### A. Citation Policy for Illustrative Scenarios (`policy.py`)
- **Observed Defect:** In `p7_live_validation_009`, everyday illustrative scenarios (e.g., waking to an alarm clock, morning coffee routines) were decorated with citations to academic papers (`evi_02`) that discussed general habit cue loops but never studied or mentioned alarm clocks.
- **Remediation in `backend/app/services/scriptwriter/policy.py`:**
  - Updated `ANTI_FILLER_DIRECTIVES` Item 3 (`HONEST FACTUAL BOUNDARIES & ILLUSTRATIVE SCENARIO CITATION POLICY`):
    - Explicitly states that everyday examples, hypothetical scenarios, metaphors, and illustrative narration (e.g., waking to an alarm clock, morning coffee, toothbrush routines) are permitted for narrative engagement, but **MUST NOT** receive evidence citations unless the cited research excerpt explicitly documents that specific everyday scenario itself.
    - Evidence citations must substantiate the scientific, causal, or historical proposition being conveyed, not the decorative illustrative vehicle.
    - Factual principles and scientific mechanisms embedded within illustrative examples remain subject to strict grounding.
  - Aligned prompt builders (`build_script_generation_prompt` and `build_single_section_prompt`) with explicit negative constraints forbidding citation decoration on illustrative examples.

### B. Unsupported-Specificity Repair Policy (`repairer.py`)
- **Observed Defect:** In `p7_live_validation_009`, claim `clm_14` asserted unsupported anatomical structures ("prefrontal cortex" and "basal ganglia") absent from permitted evidence. The local model qualified the assertion with a hedge ("While not explicitly mentioned, the prefrontal cortex may help guide habit routines") rather than removing the unsupported anatomical specifics or replacing them with supported general concepts.
- **Remediation in `backend/app/services/scriptwriter/repairer.py`:**
  - Updated `SECTION_REPAIR_SYSTEM_PROMPT` Rule 3:
    - If a claim asserts a specific anatomical structure (e.g. prefrontal cortex, basal ganglia), biological mechanism, or specific entity absent from permitted evidence, the model **must NOT** preserve it by merely hedging ("may", "could", "possibly", "might", "while not explicitly mentioned").
    - Hedging unsupported specifics is strictly prohibited. The repair must replace the claim with a directly supported general explanation (e.g. "brain regions" or "neural pathways" supported by neuroplasticity evidence) using `rephrase`, or delete it using `remove`.
  - Added programmatic validation in `_validate_section_repair_response`:
    - Inspects repairs with action `qualify` or `rephrase`.
    - If hedging language (`may`, `might`, `could`, `possibly`, `perhaps`, `while not explicitly mentioned`) is used while preserving key terminology from the original proposition that is absent from all permitted evidence excerpts, the repair is rejected with diagnostic feedback:
      `"Claim '{claim_id}' preserved unsupported specific detail(s) {words} (absent from permitted evidence) using hedging. Hedging unsupported specifics is prohibited; remove the unsupported detail or replace it with a directly supported general explanation."`
    - Preserves surrounding narration untouched during replacement or removal.

---

## 3. Planned Research Provider Expansion (Offline Implementation)

The research layer was expanded by implementing three provider tool pairs in `backend/app/services/research/tools/`:

### A. Wikimedia Provider (`backend/app/services/research/tools/wikimedia.py`)
- **`WikimediaSearchTool` (`wikimedia_search`)**:
  - Endpoint: `https://en.wikipedia.org/w/rest.php/v1/search/page`
  - Normalized results: Extracts `pages` into `SearchResultItem(title, url, snippet)`.
  - Strips HTML searchmatch tags from excerpts.
  - Enforces session search budget, request timeout, and polite User-Agent headers.
  - Handles HTTP 429 rate limits (`ResearchProviderError`) and timeouts (`ResearchTimeoutError`).
- **`WikimediaExtractTool` (`wikimedia_extract`)**:
  - Endpoint: `https://en.wikipedia.org/api/rest_v1/page/summary/{title}`
  - Enforces SSRF safety validation (`validate_url_safety`).
  - Extracts clean article summary text into markdown format.
  - Enforces session extract call budgets and content character ceilings (`consume_content_budget`).

### B. Internet Archive Provider (`backend/app/services/research/tools/internet_archive.py`)
- **`InternetArchiveSearchTool` (`internet_archive_search`)**:
  - Endpoint: `https://archive.org/advancedsearch.php`
  - Query construction: Supports keyword queries with optional `mediatype` filtering (e.g., `texts`).
  - Normalized results: Extracts `identifier`, `title`, `description`, `creator`, and `year` into `SearchResultItem`.
- **`InternetArchiveExtractTool` (`internet_archive_extract`)**:
  - Endpoint: `https://archive.org/metadata/{identifier}`
  - Enforces SSRF safety validation.
  - Extracts descriptive metadata, creator, date, media type, and subjects into structured markdown.
  - Graceful degradation: Handles inaccessible, restricted, or empty metadata records cleanly without crashing.

### C. OpenAlex Provider (`backend/app/services/research/tools/openalex.py`)
- **Abstract Reconstructor (`reconstruct_abstract`)**:
  - Algorithmically reconstructs plain text from OpenAlex's `abstract_inverted_index` map (`{word: [positions]}`).
  - Sorts positions in ascending index order and joins tokens cleanly.
  - Gracefully handles `None`, empty, or malformed dictionaries.
- **`OpenAlexSearchTool` (`openalex_search`)**:
  - Endpoint: `https://api.openalex.org/works`
  - Normalized results: Extracts title, DOI/landing URL, authors, publication year, venue, and reconstructed abstract into `SearchResultItem`.
  - Graceful fallback: If abstract is not indexed, compiles rich scholarly metadata snippet (authors, venue, year).
- **`OpenAlexExtractTool` (`openalex_extract`)**:
  - Endpoint: `https://api.openalex.org/works/{id_or_doi}`
  - Resolves work IDs (`W...`) and DOIs.
  - Formats full academic paper overview including authors, year, journal/venue, DOI, citations, key concepts, and reconstructed abstract.

### D. Registry & Factory Wiring
- **`ResearchToolFactory.create_tool_registry()` (`factory.py`)**:
  - Registers all 8 production research tools (`tavily_search`, `firecrawl_extract`, `wikimedia_search`, `wikimedia_extract`, `internet_archive_search`, `internet_archive_extract`, `openalex_search`, `openalex_extract`).
- **`ResearchOrchestrator.DEFAULT_TOOL_MAP` (`orchestrator.py`)**:
  - Mapped categories:
    - `"web_search": "tavily_search"`
    - `"web_extract": "firecrawl_extract"`
    - `"wiki_search": "wikimedia_search"`
    - `"wiki_extract": "wikimedia_extract"`
    - `"archive_search": "internet_archive_search"`
    - `"archive_extract": "internet_archive_extract"`
    - `"academic_search": "openalex_search"`
    - `"academic_extract": "openalex_extract"`
  - Completely preserves backwards compatibility with existing Tavily/Firecrawl workflows while allowing plans to leverage Wikipedia, Internet Archive, and scholarly literature.

---

## 4. Test Verification & Results

### Step 1 & 2: Writing Policy & Repair Regression Tests
- Added `test_citation_policy_forbids_citing_everyday_illustrative_scenarios` and `test_factual_principles_in_illustrative_examples_still_require_grounding` to `backend/tests/test_writing_policy.py`.
- Added `test_repair_rejects_hedging_of_unsupported_specifics` and `test_repair_accepts_replacing_or_removing_unsupported_specifics` to `backend/tests/test_script_repairer.py`.
- **Result:** All 39 tests passed in 0.51s.

### Step 3: Provider Unit and Mocked Integration Tests
- Created `backend/tests/test_expanded_research_providers.py` with 15 test cases covering:
  - Wikimedia search, extraction, rate limiting, timeouts, and SSRF rejection.
  - Internet Archive search, metadata extraction, and graceful degradation on inaccessible items.
  - OpenAlex inverted-index abstract reconstruction, search with fallback, and work extraction.
  - Tool factory registration and registry lookup across all 8 tools.
  - Budget (search/extract calls) and character ceiling enforcement.
- **Result:** All 15 tests passed in 0.51s.

### Full Test Suite Run
- **Backend Tests:** 522 passed, 0 failed, 3 warnings in 62.09s.
- **Frontend Tests:** 18 passed, 8 skipped (offline Vite dev server), 0 failed.

---

## 5. Proposed Controlled Live Validation Plan

Per Project Lead operating standards, zero live requests were dispatched during this implementation round. Below is the proposed validation plan for authorization:

1. **Validation Identifier:** `p7_live_validation_010`.
2. **Target Topic:** `"The Science of Habit Formation"`.
3. **Validation Scope:**
   - Verify that everyday illustrative examples (alarm clocks, coffee routines) are generated without false academic citation decorations.
   - Verify that if the script generator asserts specific anatomical structures (e.g. prefrontal cortex) not present in retrieved evidence, the surgical repairer removes or replaces them with supported general concepts rather than hedging.
   - Verify that Wikimedia, Internet Archive, and OpenAlex query dispatch functions under live execution without regression.
4. **Lifecycle Discipline:** One execution = one fresh app startup = one execution = complete shutdown.
