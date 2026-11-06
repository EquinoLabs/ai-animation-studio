# Phase 7 — Checkpoint 2: Research Provider Factory and Production Runtime Wiring

## 1. Architectural Overview

Phase 7 Checkpoint 2 connects the live external research adapters (`TavilySearchTool` and `FirecrawlExtractTool`) to the production runtime of the AI Animation Studio. Prior to this checkpoint, the adapters existed in isolation and were only exercised through isolated tool tests, while the production `PipelineOrchestrator` and `ScriptService` ran with `research_orchestrator=None`.

This checkpoint introduces `ResearchToolFactory`, establishes strict credentials-validation and anti-silent-fallback rules, configures the Firecrawl endpoint, and wires the production dependency tree across `FastAPI` routes, `PipelineOrchestrator`, `ScriptService`, and `ResearchPlanner`.

```
                        FastAPI Routes (get_orchestrator)
                                      │
                                      ▼
             PipelineOrchestrator.create_production(settings)
                                      │
               ┌──────────────────────┴──────────────────────┐
               ▼                                             ▼
     ResearchToolFactory                       PipelineOrchestrator
               │                                      │
   ┌───────────┴───────────┐                          │
   ▼                       ▼                          │
ToolRegistry         ResearchOrchestrator ────────────┼───────────┐
   │                       ▲                          │           ▼
   ├── TavilySearchTool    │                          │     ScriptService
   └── FirecrawlExtractTool ─┘                         │           │
                                                      │           ├── outliner
                                                      │           ├── generator
                                                      │           ├── fact_checker
                                                      │           ├── repairer
                                                      │           └── challenge_claim("investigate")
                                                      ▼
                                                Stage Handlers
```

---

## 2. Core Components

### 2.1 ResearchToolFactory (`backend/app/services/research/tools/factory.py`)
Responsible for constructing tool registries, orchestrators, and planners with appropriate settings and dependency injection.

- **Methods:**
  - `validate_credentials(settings)`: Validates that `ANIM_TAVILY_API_KEY` and `ANIM_FIRECRAWL_API_KEY` are present and non-empty. Raises actionable `MissingCredentialsError` if either is missing.
  - `create_tool_registry(settings, client, use_mocks, custom_tools, validate_credentials)`:
    - In production (`use_mocks=False`): Registers real `TavilySearchTool` and `FirecrawlExtractTool`.
    - **Anti-Silent-Fallback Rule:** Never silently falls back to `MockSearchTool` or `MockExtractTool` when credentials are absent. If credentials are missing, attempts to invoke the tools raise an explicit `MissingCredentialsError` pointing to the exact environment variables required.
    - If `use_mocks=True`: Registers `MockSearchTool` and `MockExtractTool` for explicit test injection.
    - If `custom_tools` is provided: Registers the supplied tools directly.
  - `create_orchestrator(settings, storage_service, tool_registry, client, use_mocks, tool_map)`: Constructs a `ResearchOrchestrator` wired with the tool registry and `ResearchStorageService`.
  - `create_planner(settings, storage_service, llm_provider, max_retries)`: Constructs a `ResearchPlanner` wired with the configured LLM provider (resolving `settings.LLM_PLAN_PROVIDER` to `ollama` or `mock`).

### 2.2 Configurable Firecrawl API URL
- Added `FIRECRAWL_API_URL` to `Settings` in `backend/app/config.py`:
  - Environment alias: `ANIM_FIRECRAWL_API_URL`, fallback: `FIRECRAWL_API_URL`.
  - Default: `https://api.firecrawl.dev/v1/scrape`.
- Updated `FirecrawlExtractTool`:
  - Constructor accepts optional `api_url` parameter overriding settings.
  - Validates API URL scheme (strictly `http`/`https`), hostname, and rejects embedded credentials.
  - Target URL scraping continues to enforce strict SSRF validation via `validate_url_safety`.

### 2.3 Production Runtime Wiring
- **`backend/app/api/routes.py`**:
  `get_orchestrator` FastAPI dependency calls `PipelineOrchestrator.create_production(project_service=project_service, settings=settings)`.
- **`backend/app/services/pipeline/orchestrator.py`**:
  `PipelineOrchestrator.create_production` creates `ResearchStorageService` and `ResearchToolFactory.create_orchestrator(settings=cfg, storage_service=storage)`.
  When `PipelineOrchestrator` initializes `ScriptService`, it supplies the single shared `ResearchOrchestrator` instance.
- **Investigate Workflow Enabled**:
  `ScriptService.challenge_claim(..., action="investigate")` now has access to the configured `ResearchOrchestrator` to perform targeted re-investigation runs, persisting newly retrieved evidence and rechecking claims against updated evidence catalogs.

---

## 3. Environment Template (`.env.example`)

Updated `.env.example` to document research provider credentials and endpoint settings:
```ini
# Research tool settings (Phase 1 & Phase 7)
# Real external research providers require valid API credentials.
# If credentials are not configured, attempting live web search or page extraction
# will raise an explicit, actionable MissingCredentialsError. Mock tools are never
# silently selected as a fallback in production runtime.
#
# Tavily Search API key (required for live web search queries):
# ANIM_TAVILY_API_KEY=tvly-...
#
# Firecrawl API key (required for live web page scraping/extraction):
# ANIM_FIRECRAWL_API_KEY=fc-...
#
# Firecrawl API URL (defaults to Firecrawl cloud; configure for self-hosted instances):
ANIM_FIRECRAWL_API_URL=https://api.firecrawl.dev/v1/scrape
```

---

## 4. Verification & Testing

- **New Test Suite (`backend/tests/test_phase7_checkpoint2_runtime_wiring.py`)**:
  - `test_factory_creates_real_tools_by_default`: Verified production registry contains real adapters.
  - `test_factory_no_silent_mock_fallback_on_missing_credentials`: Verified missing credentials raise `MissingCredentialsError` and do not instantiate mocks.
  - `test_factory_validate_credentials_flag`: Verified proactive credential validation.
  - `test_firecrawl_configurable_api_endpoint`: Verified configurable endpoint from settings.
  - `test_firecrawl_api_endpoint_constructor_override`: Verified constructor override.
  - `test_firecrawl_api_endpoint_validation_rejects_unsafe_urls`: Verified rejection of unsafe API schemes and credentials.
  - `test_pipeline_orchestrator_create_production_wires_research`: Verified shared orchestrator across pipeline and script service.
  - `test_routes_get_orchestrator_dependency_wires_production_runtime`: Verified FastAPI dependency wires research orchestrator.
  - `test_factory_create_planner_uses_configured_llm_provider`: Verified Ollama/Mock planner provider selection.
  - `test_explicit_mock_injection_preserved`: Verified `use_mocks=True` is honored for test doubles.
  - `test_custom_tools_injection_preserved`: Verified injection of custom tool instances.
  - `test_challenge_investigate_action_uses_wired_production_orchestrator`: Verified conversational investigate challenge triggers targeted research via the wired orchestrator.
- **All 12 Checkpoint 2 tests passed.**
- **All 46 Phase 1-5 regression tests passed.**
- **All 16 Phase 6 Checkpoint 1 challenge and API tests passed.**
- **All 26 frontend unit and integration tests passed.**
- **Frontend production build succeeded.**
- **No architectural import rule violations.**
