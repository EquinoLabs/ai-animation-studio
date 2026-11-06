# Phase 7 — Checkpoint 3 Architecture: Research Pipeline Integration

## Executive Summary
Phase 7 Checkpoint 3 connects the existing research planning and execution engine directly to the real Script Builder workflow. A completely clean project (with no preseeded fixture data) can now progress seamlessly from a user-provided topic to real retrieved sources, persisted evidence records, an evidence brief (`evidence_brief.md`), a research-grounded script draft, and claim-level fact checking against verified evidence provenance.

## 1. Architectural Architecture & Component Reuse
Rather than duplicating existing subsystems, Checkpoint 3 unified and wired:
- **`ResearchPlanner`**: Configured LLM provider analyzes project topic and generates a query plan with search and extraction queries.
- **`ResearchOrchestrator` & `ToolRegistry`**: Real production adapters (`TavilySearchTool`, `FirecrawlExtractTool`) or injected test tools execute the plan under strict budget constraints (`max_search_calls`, `max_extract_calls`, wall-clock deadlines, byte limits, SSRF protection).
- **`ResearchStorageService`**: Persists `ResearchRunRecord` in `research/runs/<run_id>.json`, source records in `research/sources/`, raw extractions in `research/extractions/`, evidence records in `research/evidence/evidence.json`, and the aggregated `evidence_brief.md`.
- **`ScriptService` & `PipelineOrchestrator`**: Ingests the evidence brief into outline and draft generation prompts, citing real evidence identifiers (`evi_*`).
- **`FactCheckService`**: Extracts claims from draft scripts, cross-references them against active run evidence, computes risk and support levels, and saves `FactCheckReport`.
- **`ResearchStudioPanel`**: Allows users to initiate research on clean projects directly, track query progress, and inspect evidence, briefs, and sources.

```
[ User / UI ]
      |
      v
POST /api/v1/projects/{id}/research/start (or start_script with auto_research=True)
      |
      v
PipelineOrchestrator.start_research()
      |
      +---> ResearchPlanner.plan_research() [Configured LLM Provider]
      |
      +---> ResearchOrchestrator.execute_plan() [Tavily + Firecrawl via ToolRegistry]
      |         |
      |         +--> Executes queries safely with SSRF validation & budget tracking
      |         +--> Persists sources, extractions, evidence, runs
      |
      +---> EvidenceBriefBuilder.build_brief() -> writes research/evidence_brief.md
      |
      +---> ResearchOverviewResponse (summarizes queries, sources, evidence)
      |
      v
POST /api/v1/projects/{id}/script/start (or subsequent pipeline stages)
      |
      +---> Uses evidence_brief.md & active evidence records
      +---> Passes Permitted Evidence IDs to Script Generator
      +---> Validates citations against real evidence IDs
      +---> Runs claim-level Fact Checking against catalog
```

## 2. Initiation & Production Safety
### Proactive Credential Validation
In production mode (`use_mocks=False`), starting research without necessary API keys immediately raises `MissingCredentialsError`, returning HTTP 400 with actionable messages:
- `"Missing Tavily API key. Configure TAVILY_API_KEY in environment or .env."`
- `"Missing Firecrawl API key. Configure FIRECRAWL_API_KEY in environment or .env."`

Silent fallback to mock providers is strictly forbidden in production. Mocks are only used when explicitly requested (`use_mocks=True` in tests).

### Clean Project Orchestration Path
- `PipelineOrchestrator.start_research(...)`:
  1. Validates project existence.
  2. Runs LLM-based research planning.
  3. Executes research plan with budget limits.
  4. Generates and stores `evidence_brief.md`.
  5. Stores run metadata linking queries, evidence IDs, and source URLs.
  6. Returns structured `StartResearchResponse`.
- `PipelineOrchestrator.start_script(...)` with `auto_research=True`:
  - If a clean project has no completed research run, automatically triggers `start_research` before drafting the script, ensuring the script is grounded in real research.
  - If `allow_unresearched=True` is explicitly specified, research is bypassed.

## 3. Provenance & Claim Linkage
- Research runs generate deterministic evidence IDs (`evi_<hash>`).
- Script generation prompts supply the active run's evidence catalog.
- Script outlines and narration cite evidence IDs directly (`evidence_ids: ["evi_..."]`).
- Post-generation validator verifies that all cited evidence IDs exist in the active run.
- Fact checker reconciles each claim against the cited evidence records.

## 4. Failure & Partial Coverage Semantics
- **Zero Results / Planned Only:** Handled gracefully without crashes; run record status reflects partial or empty coverage.
- **Budget Exceeded:** Remaining planned queries are marked as `skipped` with `skip_reason="budget_exceeded"`.
- **Query Timeouts / Errors:** Individual queries record `status="failed"` and safe sanitized `error_message` without halting non-dependent execution.

## 5. Zero-Evidence Guard Enforcement
- **Usable Run Eligibility:** A research run is eligible for script grounding if and only if its terminal status is `completed` AND it contains at least one evidence record strictly scoped to its exact `run_id`.
- **Auto-Research Check (`PipelineOrchestrator.start_script`):**
  - Completed runs with 0 evidence are treated as unusable. If no usable run exists and `auto_research=True`, research is triggered automatically.
  - If triggered research completes with 0 evidence records, script generation is stopped immediately with a clear `PipelineError` without retrying.
  - The empty/failed run is preserved in storage with complete failure/empty-result metadata.
- **Outliner Selection Guard (`Outliner._select_research_run`):**
  - Both explicit and auto-selected newest runs must have at least one evidence record matching `ev.run_id == run.run_id`.
  - Empty runs are rejected with `ScriptOutlinerRunError`.
  - Evidence from earlier runs cannot leak or make an empty subsequent run appear usable.
