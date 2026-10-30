# Script Builder: Research and Writing Design

**Status:** Proposed design for review --- not yet approved for
implementation\
**Scope:** Improve the existing Script Builder only\
**Primary model:** Local Ollama model (`qwen2.5:7b` currently
configured)\
**Storage:** Local project files; no database\
**Orchestration:** Existing Python application/orchestrator and a
controlled tool registry; no LangGraph dependency for now

------------------------------------------------------------------------

## 1. Purpose

The Script Builder should turn a user-provided topic into an engaging,
evidence-grounded YouTube explainer script. The user delegates story
choices to the AI but can inspect the research, challenge facts in chat,
and request additions or removals.

The system should be transparent about where factual claims came from
without forcing the user to approve the research brief before script
generation.

This document describes the desired behavior. It does **not** assert
that all described features already exist in the current repository.

## 2. Product goals and constraints

### Goals

-   Produce original, engaging narration with a clear narrative arc.
-   Research factual topics using external APIs rather than relying
    solely on model memory.
-   Let Ollama choose which supported facts best serve the story.
-   Preserve links between sources, evidence claims, and script
    passages.
-   Run a mandatory but flexible fact-check before presenting a draft as
    ready for review.
-   Support conversational revision while preserving version history and
    research provenance.
-   Let the user inspect retrieved facts, AI-selected facts, and facts
    actually used in the script.

### Constraints

-   Local-first application. Research requires internet access and sends
    queries to configured providers.
-   Use the existing local Ollama setup for model-driven planning,
    writing, and checking; no additional AI provider is required in the
    initial design.
-   No database or hosted SaaS backend.
-   Keep project artifacts in the existing project-folder system, using
    portable relative paths.
-   Preserve the existing Script Builder UI and lifecycle unless a
    specific change is justified.
-   No animation, TTS, scene-planning, or video-pipeline implementation
    work is included in this scope.
-   Do not introduce LangGraph now. Keep orchestration explicit and
    modular so it can be reconsidered later if real complexity justifies
    it.

## 3. Core principles

1.  **Evidence informs factual claims; storytelling determines
    presentation.** Ollama may choose the most interesting facts and
    narrative order, but should not make weak evidence sound certain.
2.  **Retrieved does not mean verified.** Search results and source
    pages may be wrong, outdated, incomplete, or misinterpreted.
3.  **AI-selected does not mean objectively true.** Track selection
    separately from evidence strength.
4.  **The same model can write and check, but cannot guarantee its own
    accuracy.** Use source excerpts, claim-by-claim review, structured
    outputs, and explicit uncertainty.
5.  **Fact-checking is mandatory; automatic rejection of the entire
    draft is not.**
6.  **No invented citations.** Every source reference must resolve to a
    source record actually collected by the application.
7.  **Preserve user agency.** The AI manages the story by default; the
    user can challenge evidence and request changes in chat.
8.  **Bounded adaptive research.** Search more when important gaps
    remain, but obey time, call, content-size, and API-credit budgets.
9.  **Keep artifacts and runs recoverable.** Save outputs at stage
    boundaries so a failure does not require restarting the whole
    workflow.

## 4. High-level workflow

1.  Accept topic and user settings, including a target duration.
2.  Ask Ollama to produce a research plan: key questions, likely
    subtopics, claims requiring strong support, and useful source types.
3.  Search the web using approved tools.
4.  Select promising sources and retrieve readable content.
5.  Build a structured evidence brief, retaining source provenance and
    uncertainty.
6.  Assess coverage and gaps; conduct targeted follow-up research when
    useful and within budget.
7.  Ask Ollama to create a topic-appropriate story outline based on the
    evidence.
8.  Ask Ollama to write the full narration from the outline and
    evidence.
9.  Run a mandatory claim-level fact-check against the actual retrieved
    evidence.
10. Repair unsupported, contradicted, overconfident, or misleading
    claims where possible.
11. Run one automatic quality-improvement pass for story flow, pacing,
    repetition, clarity, and duration.
12. Recheck factual claims changed by the quality pass.
13. Present the draft with the research brief, source links, fact
    statuses, and unresolved issues available in the UI.
14. On later revisions, reuse existing research where appropriate;
    investigate new or challenged claims and update affected script
    passages and links.

These are logical stages, not necessarily one model call per stage. The
implementation may combine calls if testing shows no meaningful loss in
quality or traceability.

## 5. Research tool registry

### 5.1 Design

Implement a small Python registry of explicitly approved tools. Do not
implement a full MCP server as the first step. Keep tool definitions and
execution adapters modular so the same underlying functions could be
exposed through MCP later if needed.

The model does not automatically inspect Python docstrings. The
application must provide the model with tool names, concise
descriptions, and structured parameter schemas in the format supported
by the Ollama API.

Each tool definition should include:

-   Stable tool name and version.
-   Concise purpose and description of when to use it.
-   Parameter schema, required fields, limits, and defaults.
-   Output schema and expected failure modes.
-   Usage guidance and limitations.
-   Execution policy: timeout, rate/call budget, maximum result
    count/content size, and whether network access is used.
-   Provider and credential requirements, kept separate from
    model-visible metadata.

Docstrings should explain the tool for developers. Registry metadata
should be the authoritative description passed to the model. Runtime
validation and security policies must be enforced by Python, not merely
requested in a prompt.

### 5.2 Initial provider candidates

  -----------------------------------------------------------------------
  Provider                Intended role           Notes
  ----------------------- ----------------------- -----------------------
  Tavily                  General web search and  Candidate primary
                          source discovery        search provider;
                                                  enforce configured
                                                  credit/call budgets.

  Firecrawl               Extract readable        Avoid crawling
                          content from selected   arbitrary sites
                          webpages; search        broadly; extract
                          capabilities may be     selected pages as
                          evaluated separately    needed.

  OpenAlex                Scholarly literature    Use for topics where
                          and research metadata   academic literature
                                                  adds value; monitor
                                                  plan and usage limits.

  Wikimedia APIs          Quick orientation and   Not a sole authority
                          reference leads         for significant claims;
                                                  preserve source URLs
                                                  and attribution
                                                  metadata where
                                                  relevant.

  Internet Archive        Historical materials    Optional specialist,
                          and archived records    used when relevant. S3
                                                  credentials are not a
                                                  general web-search key
                                                  and should not be
                                                  exposed to Ollama.
  -----------------------------------------------------------------------

Do not integrate all providers at once merely because they are
available. Begin with the smallest useful set, measure result quality
and cost, then add specialist providers behind the same interface.

### 5.3 Tool calling, execution safety, and budget controls

-   **Allowlisted dispatch and validation:** Ollama proposes tool calls; application code strictly validates tool names and parameter payloads against Pydantic schemas in Python before execution.
-   **Initial provider scope:** Begin exclusively with **Tavily** for web search and **Firecrawl** for structured page extraction. Defer specialist providers (OpenAlex, Wikimedia, Internet Archive) until the core workflow is proven and benchmarked.
-   **Ceilings vs. Adequacy:** Initial research budgets (e.g. 3 search calls and 3 extraction calls per topic) are strict development *ceilings* to prevent runaway loops and credit drain; they are NOT proof that research is complete or adequate.
-   **Separate timeout controls:** Per-call network timeouts (e.g. 15.0 seconds per HTTP request) and an overall research stage deadline (e.g. 60.0 seconds total) must be separate, independently enforced controls.
-   **Content and buffer limits:** Extracted webpage text must enforce both a per-page limit (e.g. max 15,000 characters) and a total session limit (e.g. max 45,000 characters) to prevent memory bloating and context window overflow.
-   **Credential protection:** API keys (`TAVILY_API_KEY`, `FIRECRAWL_API_KEY`) must reside exclusively in application configuration (`Settings`). Credentials must NEVER enter model-visible prompts, returned tool output, UI elements, or log files.
-   **Untrusted data handling:** Search snippets and extracted page contents are untrusted data, not model instructions. Web content must be sanitized; prompt injections or system directives embedded in external pages must be ignored and stripped.
-   **Explicit error visibility:** Provider outages, HTTP errors, and budget exhaustions must surface as explicit typed domain exceptions (`ResearchProviderError`, `ResearchBudgetExceededError`). The system must never silently swallow errors or convert missing research into successful output.
-   **Offline testing isolation:** All unit and regression tests must use offline mocks (`MockSearchTool`, `MockExtractTool`) and must NEVER consume real provider credits or require live internet access.
-   **No Ollama tool-calling needed in Phase 1:** Phase 1 focuses exclusively on the Python tool registry, parameter validation, rate/budget enforcement, and provider client adapters. Testing the tool registry contract does not require Ollama tool-calling integration; tool execution is tested directly in Python.
-   **HTTP client conventions:** Use existing project conventions and installed runtimes (such as `httpx` or standard library `urllib.request`) rather than introducing unneeded third-party dependencies.

### 5.4 Adaptive tool selection

Ollama may recommend which approved tool would help answer a research
question. Python remains responsible for deciding whether that tool is
permitted, whether the budget allows it, and how to execute it.

The model should usually choose a research need (for example, "search
web" or "look up scholarly literature"), while Python selects the
configured provider. This avoids hard-coupling prompts to a specific
vendor and allows provider replacement.

## 6. Adaptive research policy

### 6.1 Initial research plan and data contracts (P2-S1 Foundation)

The research data foundation is established via typed Pydantic contracts in `backend/app/domain/research.py`:

-   **`ResearchPlan` (`rplan_<id>`):** Defines intended research work before execution (plan ID, project ID, topic, research objective, research questions, planned queries, approved tool categories, budget constraints, created timestamp, schema version).
-   **`PlannedQuery` (`qry_<id>`):** Specific intended query (query ID, query text, purpose, target research question, tool category).
-   **`QueryRecord`:** Execution outcome of a query (query ID, plan ID, run ID, query text, tool name, status: planned/running/succeeded/failed/skipped, timestamps, sanitized error message, returned source IDs, result count).
-   **`SourceRecord` (`src_<id>`):** Provenance metadata for a discovered or extracted source (source ID, project ID, URL, canonical URL, title, publisher, source type, retrieval method, retrieval status, retrieved_at, publication date, content artifact ref, SHA-256 content hash, char count).
-   **`EvidenceRecord` (`evi_<id>`):** Concrete excerpt extracted from a source (evidence ID, project ID, source ID, query ID, run ID, excerpt, locator, target claim/question, status: retrieved/candidate/discarded, created_at).
-   **`ResearchRun` (`rrun_<id>`):** Execution lifecycle state (run ID, plan ID, project ID, status: pending/running/completed/failed/cancelled, started_at, completed_at, search/extract calls used, chars extracted, budget limits, termination reason, artifact refs, query records, schema version).
-   **`ResearchBudgetConstraints`:** Configured ceilings for search calls, extract calls, per-call timeouts, overall deadline, max page chars, and max total chars.

The model's prior knowledge may help generate questions, but it is not itself evidence that a claim is true.

### 6.1.1 Project-Local Artifact Storage Layout

All research artifacts reside strictly within the corresponding project directory (`projects/proj_<id>/research/`), managed by `ResearchStorageService` (`backend/app/services/research/storage.py`):

```text
projects/proj_<id>/
  research/
    plans/
      rplan_<id>.json           # Immutable plan specification
    runs/
      rrun_<id>.json            # Run state, budget usage, termination reason, and query records
    sources/
      sources.json              # Aggregated source metadata and retrieval status
    evidence/
      evidence.json             # Aggregated evidence excerpts and claim linkages
    content/
      src_<id>.md               # Extracted markdown page content (bounded to max_page_chars)
```

Key persistence properties:
- **Atomic Writes:** All JSON and content markdown files are written via temporary file replacement (`.tmp_` + `os.replace`) to prevent corrupt states.
- **Project Isolation & Path Safety:** Enforces strict regex validation on all IDs, disallows path traversal (`..`), verifies canonical paths reside within the project root, and prevents writing artifacts belonging to one project into another.
- **Interruption Recovery:** Any run persisted as `running` on disk is reconciled to `failed` upon retrieval if not actively tracked in the current process, preventing orphaned runs from falsely appearing active or completed.
- **Source Reference Integrity:** Evidence records cannot be persisted unless the referenced `source_id` already exists within the project's source records.
- **Secret Sanitization:** All error messages in query records and run termination reasons pass through `sanitize_secret()` before disk persistence.

### 6.1.2 Research Planner Service (P2-S2 Implementation)

The first model-driven planning capability is implemented via `ResearchPlanner` (`backend/app/services/research/planner.py`):

1. **Planner Input Contract (`ResearchPlannerInput`):**
   - Encapsulates `project_id`, `topic`, `target_duration_minutes`, `existing_script_summary`, `user_guidance`, and `budget_limits` (`ResearchBudgetConstraints`).
   - Validates project and topic identifiers to prevent path traversal or malformed input before model invocation.

2. **Model Invocation & Structured Output:**
   - Prompts the LLM (Ollama `qwen2.5:7b` or deterministic offline test double) to formulate:
     * A substantive `research_objective`.
     * 2 to 5 foundational `research_questions`.
     * 2 to `max_search_calls` targeted search queries (`PlannedQuery`) with concrete search terms and explicit factual purposes.
     * Approved tool categories (`["web_search", "web_extract"]`).
   - Parsed into `GeneratedPlanPayload` via Pydantic.

3. **Bounded Retries & Error Handling:**
   - Handles malformed JSON or schema validation failures through bounded retries (default: max 2 retries, 3 total attempts).
   - On retry, appends a targeted error correction directive with the specific validation error summary.
   - Raises typed domain exceptions:
     * `ResearchPlannerUnavailableError`: When Ollama / LLM endpoint is unreachable (fails fast; does not retry indefinitely).
     * `ResearchPlannerMalformedError`: When output remains malformed after all retry attempts are exhausted.
     * `ResearchPlannerSecurityError`: If project isolation is breached or an invalid project ID is provided.
     * `ResearchPlannerError`: Base exception for unexpected failures.
   - **No Silent Fallbacks:** The planner never silently substitutes mock plans or fabricated outputs when the LLM fails.

4. **Query Capping & Atomic Persistence:**
   - Planned queries are capped strictly to the configured `max_search_calls` ceiling.
   - Generates stable canonical IDs (`rplan_<id>`, `qry_<id>`).
   - Persists the validated `ResearchPlan` atomically to `projects/<project_id>/research/plans/rplan_<id>.json` via `ResearchStorageService`.

5. **Current Scope Limitations:**
   - The planner solely outputs and saves the `ResearchPlan`. It does NOT execute search or extraction tools, fetch web pages, or generate scripts. Query execution and search orchestration are deferred to P2-S3.

### 6.2 Search, read, evaluate, repeat

### 6.2.1 Research Execution Orchestration (P2-S3 Implementation)

The bounded execution layer is implemented in `ResearchOrchestrator` (`backend/app/services/research/orchestrator.py`):

1. **Sequential Two-Stage Execution:**
   - **Stage 1 (Web Search):** Iterates over `PlannedQuery` items from the validated `ResearchPlan`. Enforces session deadlines and `max_search_calls`. Dispatches `"tavily_search"` via `ToolRegistry`. Persists query outcome in `QueryRecord` and discovered sources in `sources/sources.json`.
   - **Stage 2 (Content Extraction & Evidence):** Conservatively deduplicates discovered sources by canonical URL while preserving content-identifying query parameters. Runs pre-flight SSRF safety checks on the exact fetch URL. Dispatches `"firecrawl_extract"` within `max_extract_calls` and character budget limits. Saves content to `content/src_<id>.md` and persists verbatim `EvidenceRecord` items to `evidence/evidence.json`.

2. **Evidence Provenance & Discovery Separation:**
   - Search snippets are stored strictly as `SourceRecord.discovery_snippet` metadata and are **never** promoted to `EvidenceRecord` entries.
   - `EvidenceRecord` items require successfully persisted content markdown, an un-truncated verbatim passage ($\le 1,000$ chars), and a structural locator (`heading: ...` or `section: ...`).
   - `EvidenceRecord.target_claim` records the plan's inquiry question target (`PlannedQuery.target_question`), representing the target inquiry without asserting it as a verified factual claim.

3. **Deterministic Outcome Classification:**
   The orchestrator classifies runs strictly within the five canonical `ResearchRunStatus` values:
   - `"completed"` (`completed_full_coverage`): All queries succeeded, $\ge 1$ source extracted, verified evidence gathered.
   - `"completed"` (`partial_coverage_budget_exhausted: ...`): Evidence gathered, but remaining queries skipped due to `max_search_calls`.
   - `"completed"` (`partial_coverage_deadline_exceeded: ...`): Evidence gathered, but remaining work skipped due to session deadline.
   - `"completed"` (`partial_coverage_query_errors: ...`): Evidence gathered, but 1 or more queries failed.
   - `"failed"` (`failed_no_sources_found`): Queries succeeded but returned zero sources.
   - `"failed"` (`failed_all_queries_errored`): Every query errored or timed out.
   - `"failed"` (`failed_no_evidence_extracted` / `failed_deadline_exceeded_before_evidence`): Sources discovered but zero content could be safely extracted or deadline expired before extraction.
   - `"failed"` (`Process interrupted while run was in progress.`): In-flight run reconciled on restart.

4. **Persistence Safety & Single-Writer Isolation:**
   - Writes are atomic (`.tmp` + `os.replace`). If any persistence call fails, the orchestrator aborts with `ResearchOrchestratorError` and never reports a false success. Prior artifacts remain uncorrupted.

1.  Run a small initial batch of targeted searches.
2.  Inspect search results and select relevant sources.
3.  Extract source content when possible; do not rely on snippets alone
    for major claims.
4.  Record source metadata and relevant excerpts.
5.  Build or update the evidence brief.
6.  Identify missing coverage, weak support, duplicate reporting, and
    disagreements.
7.  Run targeted follow-up searches only when they could materially
    improve the evidence.
8.  Stop when evidence is adequate for the story, remaining gaps are
    minor or explicitly disclosed, or a configured budget is reached.

Do not use a fixed number of searches or sources as the only stopping
rule. Budget limits are ceilings, not proof of research adequacy.

### 6.3 Evidence standard

-   Prioritize sources according to the claim being assessed. Primary
    records and official data can be strong for specific facts;
    scholarly work can be strong for research; reputable journalism can
    be useful for current reporting and explanation.
-   Seek independent corroboration for important claims when practical.
-   Two websites repeating the same original report do not necessarily
    count as independent corroboration.
-   A single strong primary source may be sufficient for a narrowly
    scoped claim.
-   Distinguish source quality, source relevance, source recency, and
    whether the source actually supports the claim.
-   When sources conflict, retain the disagreement and seek context
    rather than forcing a false consensus.
-   A lack of evidence in retrieved material does not automatically
    prove a claim false.

### 6.4 Adaptive stopping and budget

The system should stop research when: - Key questions have reasonable
evidence coverage; - Important claims are supported sufficiently for
their risk and role in the story; - Further searching has diminishing
expected value; or - The configured time, call, token/content, or
API-credit budget is reached.

If a budget is reached before evidence is adequate, disclose the
remaining gaps. Do not mark research as complete merely because the
budget ran out.

Budgets should be configurable and observable. Exact defaults should be
chosen after measuring real usage and quality; do not invent a universal
ideal number of calls or sources.

## 7. Evidence brief and fact tracking

### 7.1 Distinct states

The UI and saved data must distinguish at least:

-   **Retrieved:** Relevant material found and retained.
-   **AI-selected:** Ollama chose a fact or claim as useful to the
    story.
-   **Used in script:** The factual claim actually appears in the
    current script version.
-   **Needs checking / unresolved:** Evidence is insufficient,
    conflicting, or not yet assessed.
-   **Contradicted:** Reliable evidence collected so far conflicts with
    the claim.
-   **Not used:** Retrieved or selected information that is not present
    in the current script.

These statuses describe workflow state, not absolute truth. A selected
fact is not automatically verified. A source that exists is not
automatically supporting evidence.

### 7.2 Source records

Each source record should retain, where available:

-   Stable source ID.
-   URL and canonical URL if safely determined.
-   Title, publisher/domain, author, publication date, and retrieval
    timestamp where available.
-   Provider/tool used to find or extract it.
-   Relevant excerpt(s) and extraction status.
-   Source type and licensing/attribution metadata where relevant.
-   Errors, redirects, or other retrieval limitations.

Do not assume all metadata is available. Missing fields should remain
missing rather than be fabricated.

### 7.3 Claim/evidence records

Each factual claim should have a stable ID and retain:

-   Claim text and normalized summary.
-   Supporting, contradicting, and contextual source IDs.
-   Relevant excerpts or evidence spans.
-   Assessment: supported, partially supported, conflicting, unsupported
    by gathered evidence, not checked adequately, or contradicted.
-   Confidence/strength explanation in plain language; avoid false
    precision.
-   Importance/risk to the story.
-   Selection status and reason for inclusion or exclusion.
-   Script section/passage references for each script version in which
    it appears.
-   Revision history for assessment and use status.

The model should produce structured assessments, but the application
must validate source IDs and schema fields. A valid schema does not
guarantee a correct assessment.

### 7.4 Evidence brief synthesis (`EvidenceBriefBuilder`)

Implemented in `backend/app/services/research/evidence_brief.py`, the `EvidenceBriefBuilder` compiles persisted research records into a human-readable, schema-grounded Markdown document (`evidence_brief.md`) saved directly within `projects/<project_id>/research/`.

#### Invariants and Synthesis Contract
1. **Zero Fabrication & Strict Provenance:**
   - Consumes only persisted artifacts loaded through `ResearchStorageService` (`ResearchPlan`, `ResearchRun`, `QueryRecord`, `SourceRecord`, `EvidenceRecord`).
   - Does not invent facts, sources, relationships, or excerpts.
   - Preserves exact source URLs, titles, structural locators, and query targets.
2. **Deterministic Markdown Structure:**
   - **Header & Run Metadata:** Topic, Project/Run IDs, generation timestamp, run terminal status, termination reason, and honest coverage assessment.
   - **Objectives & Execution Overview:** High-level objective, execution window, resource consumption (queries executed vs skipped/failed, sources inspected vs extracted, total byte volume), and persisted artifact listing.
   - **Question-by-Question Breakdown:** Iterates every planned question with its execution status, verbatim evidence excerpts (`> ...`), source attribution, locator tags, and target claims.
   - **Consistency & Cross-Source Analysis:** Identifies corroborated facts and honestly documents any contradictions or explicitly notes where retrieved material is insufficient to declare consensus.
   - **Unresolved Questions & Gaps:** Explicitly itemizes skipped queries (with budget or deadline reasons), failed queries (with sanitized error details), and questions with zero extracted evidence.
   - **Epistemic Limitations:** Explicitly instructs downstream scriptwriters that "Retrieved Does Not Equal Verified", respects budget boundaries, and strictly forbids hallucinations for unretrieved questions.
3. **Integration and Standalone Regenerability:**
   - Invoked automatically at the conclusion of `ResearchOrchestrator.execute_plan()`, recording the relative path in `run.artifacts["evidence_brief"] = "evidence_brief.md"`.
   - Supports standalone offline regeneration at any time via `EvidenceBriefBuilder(storage).build_and_save_brief(project_id, run_id)` without requiring in-memory orchestrator state.
   - Employs atomic replacement (`.tmp` + `os.replace`) and strict path validation through `ResearchStorageService.save_evidence_brief()`.

### 7.5 UI behavior

Add a Research & Facts view or panel to the existing Script Builder. It
should allow the user to inspect:

-   Retrieved sources and relevant excerpts.
-   Facts selected by AI and why they were selected.
-   Facts used in the current script and the corresponding passages.
-   Facts not used.
-   Evidence strength, conflicts, and unresolved questions.
-   Clickable source links and retrieval dates where available.

The user should not be required to approve or reject the research brief
before script generation. This view is for transparency and optional
cross-checking.

## 8. Writing skill and prompt policy layer

### 8.1 Separation of responsibilities

To maintain clean architecture, maintainability, and predictable model behavior, the system strictly separates four distinct policy layers:

1.  **Writing Skill (Storytelling & Craft):**
    Governs narrative arc, hook design, pacing, retention techniques, tone calibration, transitions, sentence rhythm, and visual animation cues. Focuses on *how* to tell the story compellingly without inventing facts.
2.  **Research Policy (Evidence Retrieval & Synthesis):**
    Governs discovering, retrieving, filtering, and organizing evidence into a structured brief. Focuses on *what* facts exist and their provenance, without making creative storytelling decisions.
3.  **Fact-Check Policy (Verification & Repair):**
    Governs extracting factual claims from drafts, assessing them against source records, and repairing unsupported or contradicted claims. Focuses on *truth and evidence alignment*, without dictating narrative style.
4.  **Project Settings (Creator Intent):**
    Stores user-specified parameters per project: audience knowledge level, tone (educational, casual, authoritative), target duration, optional channel introduction, and optional CTA. Kept in project metadata (`metadata.json`).

### 8.2 Consistent application across the generation lifecycle

The Writing Skill is implemented as an internal, modular Prompt Policy layer in Python. It supplies consistent guidelines and few-shot formatting rules to Ollama across all four generation stages:

1.  **Outline Generation:**
    - Transforms the topic, evidence brief, and project settings into an intentional narrative roadmap.
    - Establishes section roles (`hook`, `setup`, `middle`/development, `climax`/turning point, `takeaway`), runtime targets per section, core factual milestones, and visual staging concepts.
    - Adapts structure to topic: chronological progression for history, mechanism-breakdown for science, problem-solution for technology.
2.  **Full Script Writing:**
    - Expands the outline into spoken-word narration, strictly grounded in the evidence brief.
    - Explicitly tags factual assertions with claim IDs (`[clm_...]`) while distinguishing facts from analogies, metaphors, or hypothetical illustrations.
    - Embeds retention techniques (curiosity gaps, pattern interrupts, smooth transition phrases).
3.  **Conversational Revisions:**
    - Applies user instructions while preserving well-written, unaffected sections and good factual material.
    - Enforces writing standards on new passages so revisions do not degrade into dry bullet summaries or generic filler.
    - Flags newly introduced factual claims for evidence assessment before marking them as verified.
4.  **Quality-Improvement Pass:**
    - Evaluates flow, pacing, sentence variety, and repetition in a single bounded pass.
    - Flags and rechecks any factual claims altered or introduced during stylistic smoothing.

### 8.3 Best practices adapted from Script Writer skill

Inspection of the `script-writer` skill reference materials (`references/script_formats.md`) provides tested YouTube storytelling techniques that are directly incorporated into our writing policy layer:

-   **Hook Architecture (First 5–10 seconds):**
    The hook must immediately stop the scroll. Prohibit generic greetings or topic restatements. Choose an intentional hook formula:
    - *Question/Paradox:* Establish a compelling dilemma or counter-intuitive reality.
    - *Bold Statement / Value Promise:* Make an intriguing promise of what the viewer will understand.
    - *Pattern Interrupt:* Break typical expectations immediately.
-   **Concise Introduction (10–30 seconds):**
    Quickly bridge the hook to the setup. Establish why the question matters to the viewer and preview the core journey without rambling or burying the lede.
-   **Narrative Transitions:**
    Avoid abrupt thematic jumps. Use active transition phrases ("Now that we've seen X, here is where the problem emerges...", "This worked until a fundamental flaw was discovered...").
-   **Pacing and Word Count Calibration:**
    Calibrate pacing to standard explainer voiceover rates (120–150 WPM, nominal 140 WPM). Soft target guidelines:
    - Short explainer (3–5 minutes): ~450–750 words.
    - Medium explainer (7–12 minutes): ~1,050–1,800 words.
-   **Visual Staging Cues:**
    Every narrative section must pair narration with actionable, concrete 2D visual cues formatted as `[Visual cue: ...]` or in the section's `notes` field (e.g. `[Cutaway: diagram of cuneiform clay token]`, `[Character pose: explain, expression: curious]`).
-   **Anti-Patterns Strictly Forbidden:**
    - *Burying the lede:* Delayed explanations or wandering context.
    - *Monotone delivery:* Lack of energy shifts, rhetorical variation, or sentence length contrast.
    - *Generic filler:* Meaningless transitional padding like "Step by step, the pieces connect."
    - *Forced CTAs:* Never inject a rigid, generic "Like and subscribe" into scripts unless explicitly configured in project settings.

### 8.4 Exclusions from the Script Writer skill

To preserve our local-first, modular architecture and avoid technical debt, the following elements of the external `script-writer` skill are **explicitly excluded**:

-   **No global database (`~/.claude/script_writer.json`):** User preferences and past scripts must NEVER be saved to a global home directory file. All settings and script versions live strictly inside isolated project directories (`projects/<project_id>/`).
-   **No Node.js / npm dependencies:** The skill's `package.json` and `index.js` (an empty stub wrapper) are ignored. All logic is implemented natively in Python within `backend/app/`.
-   **No interactive pre-generation questionnaire:** The skill's multi-turn interview workflow is replaced by direct project settings and conversational revisions in the Script Builder UI.
-   **No Claude-specific API dependencies:** Writing prompts are designed and evaluated for our local Ollama model (`qwen2.5:7b`).

### 8.5 Duration is a soft target

The requested duration is a guide, not a hard cap. The story may be
shorter or longer if that improves completeness and pacing. Do not pad
or rush solely to hit a number.

-   Estimate duration from narration word count and a configurable
    speaking-rate assumption.
-   Display estimated runtime and the assumptions used.
-   Treat the estimate as approximate, not a guarantee.
-   If substantially below target, assess whether meaningful explanation
    or context is missing before expanding.
-   If above target, improve concision without removing essential
    reasoning or context.
-   Avoid a hard model-token limit being mistaken for a desired video
    duration. Detect truncated/incomplete model output and handle it
    explicitly.

## 9. Fact-checking and repair policy

### 9.1 Mandatory, flexible check

Fact-checking must run before the draft is presented as ready for
review. It is not an automatic rejection gate for the entire script.

Ollama should extract factual claims from the draft and compare them
with actual source content and evidence records. It should distinguish
factual claims from analogies, opinions, hypothetical examples, and
creative transitions unless those imply real-world facts.

### 9.2 Risk-based scrutiny

-   **High scrutiny:** central claims, dates, numbers, scientific
    mechanisms, named people, and statements that could substantially
    mislead the audience.
-   **Normal scrutiny:** supporting details and context; a credible
    source may be enough for a minor claim, with corroboration sought
    when practical.
-   **Creative flexibility:** metaphors, transitions, and illustrative
    framing, unless misleadingly presented as real events.

### 9.3 Assessment language

Distinguish: - Supported by the gathered evidence. - Partially supported
/ needs qualification. - Conflicting evidence. - Unsupported by the
material gathered. - Not checked adequately. - Contradicted by reliable
evidence.

Do not collapse these into a binary "true/false" label. A source can be
wrong, outdated, or misread; the system should explain limitations.

### 9.4 Repair behavior

-   Keep well-supported claims.
-   Qualify plausible claims when the evidence supports a narrower
    statement.
-   Research further when an important uncertainty could materially
    change the story and budget permits.
-   Correct or remove claims contradicted by strong evidence.
-   Omit or clearly qualify unverified details that are not essential.
-   Do not block an entire draft over a minor unresolved issue. Show
    unresolved issues in the research panel.
-   For a central, potentially false claim, investigate further or avoid
    stating it confidently.
-   Recheck factual claims affected by repairs and by the later quality
    pass.

The goal is to improve factual reliability while preserving engaging
storytelling. The system must not deliberately target a percentage of
incorrect facts. Quality should be measured through evaluation, not
assumed.

### 9.5 Limitations

The same Qwen model is used for writing and checking in the initial
design, so it may repeat or miss its own errors. Evidence-grounded
claim-by-claim review reduces risk but cannot guarantee truth. The UI
and documentation must not claim that an AI check certifies a script as
factually correct.

## 10. Quality-improvement pass

After fact-check and repair, run one automatic quality pass covering:

-   Narrative flow and clarity.
-   Repetition and generic filler.
-   Pacing and estimated runtime.
-   Whether the hook and ending work.
-   Whether key concepts are explained adequately.
-   Whether selected facts serve the story.
-   Whether visual cues are useful and specific.

The pass should preserve the evidence trail. Recheck any factual claims
it changes or introduces. Do not create an unbounded self-rewriting
loop.

## 11. Conversational revisions and fact challenges

Users can ask to add, remove, qualify, or investigate a fact in the
chat.

For a fact challenge, the system should: 1. Identify the claim and its
source/evidence links. 2. Research the claim again when useful. 3.
Update the evidence assessment and explain what changed. 4. Revise
affected script passages and dependent transitions. 5. Recheck the
changed claims. 6. Preserve prior script versions and the
research/change history.

If a revision introduces a new factual claim, flag it as requiring
evidence until assessed. It must not inherit "researched" status just
because it appears near an existing fact.

Removing a fact from the current script should mark it as no longer
used, not erase its source or historical record.

## 12. Local project artifacts and persistence

Use the existing project-folder and file-based architecture. No database
is proposed.

Potential artifact organization (adapt to existing naming conventions
after code inspection):

``` text
projects/
  <project-name>__<uuid>/
    metadata.json
    script/
      script.json
      versions/
      finalized/
      conversations.json
    research/
      runs/
      queries.json
      sources.json
      evidence.json
      evidence_brief.md
      claim_assessments/
    runs/
      <run-id>/
        run_manifest.json
        stage_outputs/
```

This is a proposed organization, not a mandate to duplicate existing
files. The Employee should first map existing artifacts and recommend
the smallest compatible extension.

Requirements: - Stable project-relative paths; protect against path
traversal. - Atomic writes where appropriate. - Record schema versions
and timestamps. - Keep stage/run status and failure information. - Save
stage outputs so interrupted runs can resume or retry safely. - Preserve
immutable finalized script versions and existing finalization recovery
guarantees. - Record links between source IDs, claim IDs, script
versions, and passages. - Avoid storing secrets in project artifacts. -
Do not silently overwrite finalized artifacts or corrupt earlier
evidence.

## 13. Errors, retries, and recovery

### 13.1 Failure handling and observability

-   **Bounded retries:** Use bounded retries (1–2 retries) with exponential backoff exclusively for transient network errors or rate limits.
-   **Model validation:** Validate all structured outputs against explicit Pydantic schemas. Never accept unparseable or partial JSON.
-   **No silent mock substitution:** When Ollama or an external provider fails, raise explicit domain exceptions (`LLMUnavailableError`, `ResearchProviderError`). Never silently switch to mock responses in production.
-   **State preservation:** Save diagnostic error state to `stage_outputs/` and project logs without ever recording API keys or credentials.
-   **Idempotency:** Stage retries must be idempotent and reuse existing valid artifacts where possible to prevent redundant API consumption.

### 13.2 Policy on unresearched mode

-   **No silent unresearched fallback (Strict Rule):** The default system behavior must NEVER silently present an unresearched script as research-backed. If external API keys are missing, the provider endpoint is down, or the research budget exhausts before minimum viable coverage is achieved, the operation must halt with an explicit, observable error.
-   **Explicit unresearched mode (Future Option):** If unresearched script generation is supported, it must be an intentional, deliberate creator choice:
    - Must require an explicit CLI flag (e.g. `--allow-unresearched`) or an explicit UI toggle ("Generate without external web research").
    - Must explicitly record `"unresearched": true` in project `metadata.json` and in the draft artifact.
    - The UI must display an unmissable warning banner: *"Draft generated from model memory without web research or factual verification."*
    - The research panel must display an empty state clearly explaining that research was bypassed by user request.
-   **Implementation boundary:** This unresearched mode is a future option and is NOT in scope for Phase 1.

## 14. Evaluation and acceptance criteria

Acceptance should include unit/integration tests and real Ollama smoke
tests. Mock tests alone are insufficient to establish writing quality.

### Research/tooling

-   Tool definitions exposed to Ollama include valid schemas and useful
    descriptions.
-   Invalid tool names/arguments are rejected by the application.
-   Tool budgets, timeouts, output-size limits, and provider errors are
    tested.
-   API secrets do not appear in model-visible tool metadata, UI, or
    logs.
-   Retrieved webpage instructions cannot authorize tools or override
    application policy.
-   Source metadata and excerpts persist and can be reopened after
    restart.
-   Research stopping/budget behavior is observable and tested.

### Evidence tracking

-   Retrieved, selected, used, unused, and unresolved states remain
    distinct.
-   Every displayed source reference resolves to a stored source record.
-   Script factual claims link to evidence and current script passages.
-   Unsupported new claims are not silently treated as researched.
-   Conflicting and insufficient evidence are represented distinctly.
-   Revision and version history preserve provenance.

### Writing and fact-checking

-   A real Ollama-generated script is topic-specific, not the
    deterministic mock template.
-   The outline and script use the evidence brief.
-   Target duration is treated as soft guidance and runtime is estimated
    transparently.
-   Fact-checking identifies claims lacking evidence and claims
    contradicted by provided evidence in test fixtures.
-   Repair does not remove creative narration unnecessarily.
-   The quality pass does not create an unbounded loop.
-   Claims changed by repair/quality improvement are rechecked.
-   No claim is marked "verified" solely because Ollama returned a
    confident assessment.

### UI and lifecycle

-   User can inspect sources, evidence assessments, selected facts, and
    used-in-script mappings.
-   User can challenge a fact through chat and see the result reflected
    in evidence and script revisions.
-   Existing project creation, script
    draft/revision/acceptance/finalization, and version recovery
    continue to work.
-   Existing tests and frontend build remain green.

### Evaluation method

Create a small, repeatable test set of representative topics and
manually review: - Factual claim support and source quality. - Narrative
coherence and specificity. - Estimated versus observed narration
duration. - Number and severity of unresolved issues. - API calls, time,
and credits consumed.

Do not claim a factual accuracy percentage without defining the
denominator, evaluation method, and reviewer process. Use evaluation
results to improve prompts and policies.

## 15. Phased implementation roadmap

These phases are structured to keep each increment small, strictly testable offline, and decoupled from existing downstream pipelines:

1.  **Phase 0 — Architecture Audit & Gap Analysis (Complete):**
    Map existing Script Builder, configuration, provider adapters, persistence, and tests against this design. Inspect the `script-writer` skill in read-only mode and incorporate its best practices into the writing policy layer. No application code changes.
2.  **Phase 1 — Research Tool Registry & Provider Foundation (Complete):**
    Build the Python tool registry, schemas, allowlisted dispatch, and client adapters for Tavily search and Firecrawl extraction. Enforce call budgets, timeouts, byte limits, and secret protection. Verify 100% offline with mock adapters. No Ollama tool calling or UI changes in this phase.
3.  **Phase 2 — Research Domain Models & Project Persistence (Complete):**
    Model `SourceRecord`, `ClaimRecord`, and `EvidenceBrief`. Create `ResearchStorageService` managing `projects/<project_id>/research/` (saving `sources.json`, `queries.json`, `evidence.json`, and `evidence_brief.md` atomically with schema version validation and corrupted-index resilience).
4.  **Phase 3 — Adaptive Research Loop & Evidence Brief Builder (Complete):**
    Ollama generates structured `ResearchPlan`; Python `ResearchOrchestrator` executes search and extraction loop within budget ceilings, enforces URL safety and strict content-first provenance, and `EvidenceBriefBuilder` deterministically compiles `evidence_brief.md` for downstream scriptwriting.
5.  **Phase 4 — Writing Policy Integration & Evidence-Grounded Script Generation:**
    Integrate the Writing Skill / Prompt Policy layer (hook formulas, pacing, transitions, visual cues). Ollama creates an outline grounded in the evidence brief, followed by full 5-section narration mapped to claim IDs.
6.  **Phase 5 — Claim-Level Fact-Checking, Repair & Quality Pass:**
    Extract factual claims, verify against evidence excerpts, execute flexible claim repair, and run one bounded quality pass with rechecking of modified claims.
7.  **Phase 6 — Research Studio UI & Conversational Fact Challenges:**
    Add Research & Facts panel to the web studio with source links and claim badges. Wire conversational fact challenges through the ChatPanel to trigger targeted re-investigation and draft updates.

Each phase must be small, verified by unit/integration tests, and authorized before proceeding.

## 16. Explicit non-goals for this design

-   No LangGraph dependency now.
-   No full MCP server now; use an MCP-compatible internal tool registry
    first.
-   No automatic installation or execution of arbitrary tools discovered
    online.
-   No database.
-   No second AI provider/model for fact-checking in the initial
    version.
-   No mandatory user approval of the research brief.
-   No promise that AI fact-checking guarantees truth.
-   No animation engine, TTS, scene planning, or video rendering
    changes.
-   No broad refactor of existing architecture without an explicit
    justification and approval.

------------------------------------------------------------------------

## Review questions before implementation

1.  Does the proposed artifact layout fit the current project structure,
    or should existing files be extended instead?
2.  Which provider should be integrated first: Tavily search plus
    Firecrawl extraction, with specialist providers added later?
3.  What default budgets should apply to search calls, extraction calls,
    total runtime, and response size? These should be chosen after a
    usage test.
4.  Which existing project/script lifecycle contracts must remain
    unchanged?
5.  What small set of topics should be used for real Ollama acceptance
    testing?

**Approval rule:** This document is a design proposal. The Employee
should first perform an architecture audit and report gaps and an
implementation plan without changing code. Implementation begins only
after review and explicit authorization.
