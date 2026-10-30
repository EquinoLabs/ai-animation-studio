# Script Builder: Research & Writing Architecture Gap Analysis

**To:** Lead Architect / Project Higher-up  
**From:** Implementation Engineer (Employee AI)  
**Date:** October 29, 2026  
**Status:** Audit & Analysis Complete — Implementation Awaiting Authorization  
**Subject:** Comparative Gap Analysis between `docs/architecture/Script_Builder_Research_and_Writing_Design.md`, the `script-writer` Skill, and the Current Repository Implementation  

---

## 1. Executive Summary

This audit evaluates the proposed **Script Builder: Research and Writing Design** against the active codebase of `EquinoLabs/ai-animation-studio` and inspects the external **Script Writer skill** located at `C:\Users\laksh\.agents\skills\script-writer`.

### Key Conclusions
1. **Strong Foundational Subsystems in Place:**
   The repository already possesses a verified, local-first script lifecycle (`start` -> `revise` -> `accept` -> `reject` -> `finalize`). It features crash-resilient atomic persistence with two-phase commit reconciliation (`.finalizing.json`), immutable versioned artifacts, soft duration pacing estimation (WPM-based), and full test coverage (94 passed backend unit and regression tests).
2. **Current Script Builder is Exclusively Parametric:**
   No external search or content extraction capability exists in the repository today. Scripts are generated purely from the local LLM’s internal weights based on a single topic prompt. There is no tool registry, no provider adapters (Tavily, Firecrawl, etc.), no query generation, and no web retrieval.
3. **Evidence Tracking & Fact-Checking Are Unbuilt:**
   Data structures for source records (`src_...`), claim records (`clm_...`), evidence briefs, fact assessments (`supported`, `conflicting`, `contradicted`, `unsupported`), and claim-to-passage traceability do not yet exist.
4. **Script Writer Skill Inspection Findings:**
   The `script-writer` skill contains high-value YouTube storytelling reference material (hook techniques, pacing at ~140–150 WPM, curiosity gaps, transition phrases, visual staging cues). However, its code relies on an empty Node.js wrapper (`index.js`) and stores state in a global user file (`~/.claude/script_writer.json`). The storytelling rules should be adapted into an internal Python **Writing Skill / Prompt Policy layer** in Phase 4, while its global database and Node.js wrappers are strictly excluded.
5. **Architectural Alignment:**
   The proposed design explicitly rejects LangGraph, rejects hosted databases, and avoids a heavy MCP server in favor of a small, internal Python tool registry and file-based JSON persistence. This decision aligns cleanly with the repository's modular architecture.
6. **Implementation Recommendation:**
   Proceed in **6 compact, testable phases**—starting with **Phase 1: Research Tool Registry & Search Provider Foundation** (Tavily search + Firecrawl extraction adapters, execution limits, and secret protection), without breaking existing downstream pipeline contracts or UI workflows.

---

## 2. Verified Current-State Architecture

The active repository implementation was systematically audited across backend services, domain models, LLM adapters, persistence structures, REST routes, and the frontend web UI.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Vite + React Web Studio                         │
│   [ProjectBar]  [ScriptViewer (Pacing/Durations)]  [ChatPanel]  [Modals]│
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ HTTP /api/v1/*
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                          FastAPI Application                           │
│     Routes: /projects, /script/start, /revise, /accept, /finalize      │
└───────────────────┬────────────────────────────────┬───────────────────┘
                    │                                │
                    ▼                                ▼
┌──────────────────────────────────────┐  ┌──────────────────────────────┐
│            ProjectService            │  │     PipelineOrchestrator     │
│   - projects/<id>/metadata.json      │  │   - Enforces Stage Order:    │
│   - Atomic stage updates             │  │     script -> scene_planning │
│   - Directory traversal protection   │  │     -> script_to_audio -> vid│
└──────────────────────────────────────┘  └──────────────┬───────────────┘
                                                         │
                                                         ▼
                                          ┌──────────────────────────────┐
                                          │        ScriptService         │
                                          │   - versions/vXXX.json       │
                                          │   - finalized/vXXX.json      │
                                          │   - current.json             │
                                          │   - .finalizing.json marker  │
                                          │   - conversation.json        │
                                          └──────────────┬───────────────┘
                                                         │
                                                         ▼
                                          ┌──────────────────────────────┐
                                          │         LLMProvider          │
                                          │   - SCRIPT_GENERATION_PROMPT │
                                          │   - Bounded Expansion Pass   │
                                          │   - SCRIPT_REVISION_PROMPT   │
                                          │   - OllamaLLMProvider        │
                                          │     (qwen2.5:7b, format=json)│
                                          │   - MockLLMProvider (Offline)│
                                          └──────────────────────────────┘
```

### Verified Implementation Characteristics

1. **Model Invocation ([`backend/app/llm/ollama.py`](file:///C:/Lakshya/Coding/EquinoLabs/ai-animation-studio/backend/app/llm/ollama.py)):**
   - Connects to Ollama daemon (`http://localhost:11434/api/generate`) using `format: "json"`, `keep_alive: 0`, `temperature: 0.7`, `repeat_penalty: 1.15`, and `num_predict: 4096`.
   - Validates output strictly into Pydantic domain models (`Script`).
   - Detects token limit truncation (`done_reason == "length"`).
   - Does **not** use Ollama tool calling or agentic loops.
2. **Prompt Pacing & Narrative Depth ([`backend/app/llm/base.py`](file:///C:/Lakshya/Coding/EquinoLabs/ai-animation-studio/backend/app/llm/base.py)):**
   - Implements soft duration targets (120–150 WPM range) across a 5-section narrative arc (`hook` ~12%, `setup` ~20%, `middle` ~36%, `climax` ~20%, `takeaway` ~12%).
   - Bounded single-pass expansion triggered if draft is substantially under-length (`actual_words < 0.60 * min_words`).
3. **Persistence & Recovery ([`backend/app/services/scriptwriter/service.py`](file:///C:/Lakshya/Coding/EquinoLabs/ai-animation-studio/backend/app/services/scriptwriter/service.py)):**
   - Uses atomic file writes (`.tmp` replace).
   - Two-phase commit crash recovery marker (`.finalizing.json`) ensures idempotent finalization even across process interruption.
   - Finalized drafts in `script/finalized/vXXX.json` are strictly immutable; downstream contracts consume `script/script.json`.
4. **Project Storage Layout:**
   - Format: `projects/proj_<12hex>/`.
   - Subdirectories: `runs/`, `scene_planning/`, `script/`, `script_to_audio/`, `video/`.
   - Metadata: `metadata.json` tracks project schema v1.0.0 and stage statuses (`script`, `plan-scenes`, `script-to-voice`, `video`).

---

## 3. Script Writer Skill Compatibility & Inspection Findings

The external skill directory at `C:\Users\laksh\.agents\skills\script-writer` was inspected in read-only mode without executing code or modifying files:

### 3.1 Structure & Component Analysis

| File | Type | Inspection Findings | Compatibility Assessment |
|---|---|---|---|
| `package.json` | npm manifest | Defines package `@ai-labs-claude-skills/script-writer` version 1.0.0 with `main: index.js`. | **Excluded.** npm packaging is unnecessary in our Python/Vite project. |
| `index.js` | ES module | Contains a 9-line dummy export (`export default async function script_writer(input) { ... // TODO: implement actual logic }`). | **Excluded.** Contains no executable scriptwriting logic. |
| `scripts/script_db.py` | Python CLI script | Manages preferences, past scripts, and templates using a hardcoded global file: `Path.home() / ".claude" / "script_writer.json"`. | **Strictly Excluded.** Directly violates our local-first, project-isolated architecture. Must not be executed or integrated. |
| `SKILL.md` | Agent markdown prompt | Defines an interactive conversational workflow for Claude: checks `script_db.py`, conducts an onboarding questionnaire (tone, audience, style), and generates a script. | **Partially Adapted.** The interactive questionnaire is excluded; the writing rules and style classifications are adapted. |
| `references/script_formats.md` | Reference guide | Comprehensive guide on YouTube video structures, hook formulas, intro pacing, retention/engagement techniques, transition phrases, visual cues, and common pitfalls. | **High Value — Adapted.** Informs the Writing Skill / Prompt Policy layer. |

### 3.2 What to Adapt vs. What to Exclude

- **Adapt into Writing Skill / Prompt Policy Layer (Phase 4):**
  1. *Hook Formulas (First 5–10s):* Question/paradox, bold statement, conflict/problem, pattern interrupt.
  2. *Intro Architecture (10–30s):* Fast bridge from hook to setup; establish stakes and preview points without rambling.
  3. *Transition Phrases:* Explicit connectors ("Now that we've covered X, let's look at why it broke...", "The decisive turning point occurred when...").
  4. *Engagement & Retention:* Curiosity gaps, pattern interrupts, and energy shifts.
  5. *Pacing Calibration:* 140–150 WPM target ranges aligned with our existing duration estimation math.
  6. *Visual Staging Cues:* Actionable 2D animation cues formatted as `[Visual cue: ...]` in narration or notes.
  7. *Anti-Pattern Prohibitions:* Forbid burying the lede, monotone delivery, generic filler, and forced generic CTAs.
- **Explicitly Exclude:**
  1. *No global database:* Never write to `~/.claude/script_writer.json`. All settings belong in `projects/<project_id>/metadata.json`.
  2. *No Node.js wrappers:* Ignore `package.json` and `index.js`.
  3. *No pre-generation interview questionnaire:* Our UI provides direct settings and conversational chat revisions.
  4. *No Claude-specific API assumptions:* All prompts are tailored for local Ollama (`qwen2.5:7b`).

---

## 4. Requirement-by-Requirement Status Table

The requirements defined in `docs/architecture/Script_Builder_Research_and_Writing_Design.md` are evaluated below:

| # | Architecture Requirement | Status | Current Code / Test Citations |
|---|---|---|---|
| **1** | **Approved Research Tool Registry & Provider Adapters**<br>Internal Python tool registry; adapters for search and extraction (Tavily, Firecrawl). | **Not Implemented** | No tool registry exists in `backend/app/`. No search or extraction client code found anywhere in repository. |
| **2** | **Tool Schemas, Argument Validation, Limits & Secret Protection**<br>Pydantic tool schemas, allowlisted dispatch, execution limits (timeout, rate, max size), credentials kept separate from LLM. | **Not Implemented** | No tool schemas exist. `backend/app/config.py` has no external search API keys (`ANIM_TAVILY_API_KEY`, etc.). |
| **3** | **Adaptive Research Planning, Search, Source Extraction & Stopping**<br>Ollama generates research plan; iterative search-read-evaluate loop; budget-based and sufficiency stopping rules. | **Not Implemented** | `ScriptService.start_script` jumps directly from topic string to `generate_script`. No query synthesis or web extraction. |
| **4** | **Source Records, Evidence Excerpts, Stable Fact IDs & Provenance**<br>Persistent source records (`src_...`), claim records (`clm_...`), excerpts, workflow states (`retrieved`, `selected`, `used`, `contradicted`). | **Not Implemented** | `backend/app/domain/script.py` models only `ScriptSection`, `Script`, and `ConversationEvent`. No claim/source models exist. |
| **5** | **Writing Skill & Evidence-Grounded Outline/Script Writing**<br>Writing skill policy layer; evidence-informed outline first, then full narration grounded in evidence brief without copy-pasting or filler. | **Partially Implemented** | Prompt enforces narrative arc (`hook`, `setup`, `middle`, `climax`, `takeaway`), but generation is purely parametric from model memory without outline pre-pass, evidence brief, or formal writing policy layer. |
| **6** | **Mandatory Claim-Level Fact-Checking & Flexible Repair**<br>Extract claims from draft, verify against evidence records, qualify/repair unsupported/contradicted claims before review. | **Not Implemented** | No fact-checking or claim-extraction routines exist. Draft is accepted directly into working copy. |
| **7** | **One Bounded Quality Pass & Rechecking Changed Claims**<br>Single-pass quality check for flow/filler/pacing; rechecking any factual claims modified by the pass. | **Partially Implemented** | Quality Pass 1 implemented bounded under-length expansion in `base.py` (`expand_script`), but no story/repetition/fact rechecking pass exists. |
| **8** | **Soft Duration Targeting & Runtime Estimation**<br>Target duration is soft guideline; speaking rate configurable (140 WPM); runtime estimated from narration word count. | **Implemented** | `backend/app/domain/script.py` (`count_words`, `estimate_duration_seconds`), `backend/app/config.py` (`WORDS_PER_MINUTE`), `frontend/src/components/ScriptViewer.tsx`. Verified by 8 tests in `backend/tests/test_script_quality.py`. |
| **9** | **Research/Facts UI, Source Links & Traceability**<br>Research & Facts panel; inspection of sources, selected/used facts, claim badges, clickable citations. | **Not Implemented** | Frontend has `ScriptViewer` and `ChatPanel`, but zero research, source, or claim visualization components. |
| **10** | **Conversational Fact Challenges & Evidence-Aware Revisions**<br>User can challenge facts in chat; system re-investigates, updates evidence assessments, and revises affected script passages. | **Partially Implemented** | `ScriptService.revise_script` and `ChatPanel` support conversational revision, but revisions are general text adjustments blind to evidence records or claim IDs. |
| **11** | **File-Based Persistence, Stage Recovery & Immutable Script Versions**<br>Atomic writes, portable project-relative storage, crash recovery, immutable finalized versions. | **Implemented** | `backend/app/services/scriptwriter/service.py` (`_atomic_write_json`, `.finalizing.json` recovery, `finalized/vXXX.json`). Verified by 23 tests in `test_script_verification.py`. *(Note: `research/` directory is not yet added).* |
| **12** | **Error Handling, Budgets, Observability & Real Ollama Evaluation**<br>Bounded retries, no silent mock fallback, diagnostic logging without secrets, real model verification. | **Partially Implemented** | `OllamaLLMProvider` has bounded retries, explicit error hierarchy, and zero silent fallback. However, research budgets, tool execution metrics, and provider call logs do not exist. |

---

## 5. Architectural Alignment & Writing Policy Layer

### 5.1 Architecture Refinement: Four Distinct Policy Layers
To prevent conflating storytelling with verification or research mechanics, the architecture cleanly separates four responsibilities:

1. **Writing Skill (Storytelling & Craft):** Narrative arc, hook formulas, pacing, transitions, curiosity gaps, visual cues. Implemented as a modular Prompt Policy layer in Python (`backend/app/services/scriptwriter/prompts/`).
2. **Research Policy (Evidence Gathering):** Adaptive search, extraction, gap detection, evidence brief synthesis.
3. **Fact-Check Policy (Verification & Truth):** Claim extraction, evidence cross-checking, repair, and claim status management.
4. **Project Settings (Creator Intent):** Audience level, tone, target duration, channel intro, and CTA stored in project `metadata.json`.

### 5.2 Project Storage Reconciliation
The proposed design noted `projects/<project-name>__<uuid>/` and `conversations.json`.
- **Inspected Reality:** The codebase consistently uses `projects/proj_<12hex>/` and `conversation.json` (singular).
- **Resolution:** Retain existing directory format (`proj_<12hex>`) and `conversation.json` filename to guarantee zero regression on existing project data. Add `projects/<project_id>/research/` as a clean sibling to `script/`.

---

## 6. Recommended Phased Implementation Plan

```
┌────────────────────────────────────────────────────────────────────────┐
│ Phase 1: Research Tool Registry & Provider Foundation                  │
│   - Python tool registry, parameter schemas, allowlisted dispatch      │
│   - Tavily search + Firecrawl extract adapters (HTTP client)           │
│   - Separate timeouts (call vs deadline), byte limits, call ceilings   │
│   - Zero secret leakage, 100% offline mocks (no UI/Ollama tool call)   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Phase 2: Research Domain Models & Project Persistence                  │
│   - Domain models: SourceRecord, ClaimRecord, EvidenceBrief            │
│   - Atomic persistence in projects/<project_id>/research/              │
│   - queries.json, sources.json, evidence.json, evidence_brief.md       │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Phase 3: Adaptive Research Loop & Evidence Brief Builder               │
│   - Ollama research planning prompt (questions, keywords)              │
│   - Search -> Extract -> Evaluate loop with stopping budgets           │
│   - Structured evidence brief generation                               │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Phase 4: Writing Policy Layer & Evidence-Grounded Script Writing       │
│   - Modular Writing Skill / Prompt Policy (hooks, pacing, visual cues) │
│   - Two-step generation: Evidence-informed Outline -> Full Script      │
│   - Links script passages to initial evidence claim IDs                │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Phase 5: Claim-Level Fact-Checking & Flexible Repair                   │
│   - Claim extraction & verification against evidence excerpts          │
│   - Plain-language assessments (supported, conflicting, etc.)         │
│   - Bounded claim repair + quality recheck of modified claims          │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Phase 6: Research Studio UI & Conversational Fact Challenges           │
│   - Research & Facts tab in frontend UI with source links              │
│   - ChatPanel fact challenge workflow (re-query & patch)               │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 7. Refined Phase 1 Safety, Budget & Implementation Scope

### 7.1 Specific Safety & Budget Requirements
- **Ceilings vs. Adequacy:** 3 search calls and 3 extraction calls are initial development *ceilings* to evaluate; they do not prove research adequacy.
- **Separate Timeouts:** Per-call timeout (default 15.0s) and overall research deadline (default 60.0s) must be independently enforced controls.
- **Content Limits:** Extracted webpage text must enforce a per-page limit (max 15,000 characters) and total session limit (max 45,000 characters).
- **Validation:** Tool names and arguments must be allowlisted and validated in Python via Pydantic models before execution.
- **Secret Protection:** API keys (`TAVILY_API_KEY`, `FIRECRAWL_API_KEY`) must reside exclusively in application configuration (`Settings`). Credentials must NEVER enter model prompts, tool output, UI, or logs.
- **Untrusted Web Data:** Web content must be sanitized; prompt injections or system directives embedded in external pages must be ignored.
- **Explicit Observable Errors:** Outages and budget exhaustions must raise typed domain exceptions (`ResearchProviderError`, `ResearchBudgetExceededError`).
- **Offline Test Isolation:** Unit tests must use offline mocks (`MockSearchTool`, `MockExtractTool`) and must not consume provider credits or access the internet.
- **No Ollama Tool-Calling in Phase 1:** Phase 1 tests the Python registry contract directly in Python unit tests. No Ollama integration is needed in Phase 1.
- **HTTP Client Convention:** Use existing installed `httpx` or standard library `urllib.request`. Do not add new network dependencies.

### 7.2 Explicit Policy on Unresearched Mode
- **No Silent Fallback (Strict Rule):** The default system behavior must NEVER silently present an unresearched script as research-backed. If credentials are missing, endpoints are down, or budgets exhaust before research succeeds, the system raises an explicit error and halts.
- **Explicit Unresearched Mode (Future Option):** If unresearched drafting is supported in the future, it must be an intentional, user-initiated choice (CLI flag `--allow-unresearched` or UI toggle), explicitly recorded in `metadata.json` (`unresearched: true`), and accompanied by a prominent warning banner in the UI. **Not implemented in Phase 1.**

---

## 8. Decisions Requiring Higher-Up Approval

1. **Default Research Budget Ceilings:** Confirm initial development limits of max 3 searches, max 3 extractions, 15s per-call timeout, 60s total deadline, 15,000 chars per page, 45,000 chars total session.
2. **Initial External Provider Scope:** Confirm starting exclusively with **Tavily** (search) and **Firecrawl** (extract), deferring specialist providers.
3. **No Silent Fallback Policy:** Confirm that missing credentials or provider outages halt with an explicit error, rather than silently falling back to unresearched generation.
4. **Project Storage Path:** Confirm retaining existing project ID naming (`projects/proj_<12hex>/`) and adding `research/` as a sibling to `script/`.
5. **Phase 1 Implementation Authorization:** Confirm authorization to begin Phase 1 under the refined scope.

---

## 9. Proposed First Implementation Task (Phase 1 Detailed Scope)

**Task Title:** Phase 1 — Research Tool Registry & Search Provider Foundation  
**Scope:** Backend only. No prompt changes. No UI changes. No live scriptwriting modifications.

### Scope:
1. Create `backend/app/services/research/tools/base.py`:
   - `ToolParameterSchema`, `ToolDefinition`, `ToolResult`.
   - `ToolRegistry` with allowlisted registration and dispatch.
2. Implement adapters in `backend/app/services/research/tools/`:
   - `TavilySearchTool`: query, max results (default 5), search depth.
   - `FirecrawlExtractTool`: extract clean markdown/text from target URL.
   - Use existing `httpx` or standard library `urllib.request`.
3. Add configuration in `backend/app/config.py`:
   - `ANIM_TAVILY_API_KEY: Optional[str] = None`
   - `ANIM_FIRECRAWL_API_KEY: Optional[str] = None`
   - `ANIM_RESEARCH_MAX_SEARCH_CALLS: int = 3`
   - `ANIM_RESEARCH_MAX_EXTRACT_CALLS: int = 3`
   - `ANIM_RESEARCH_CALL_TIMEOUT_SECONDS: float = 15.0`
   - `ANIM_RESEARCH_DEADLINE_SECONDS: float = 60.0`
   - `ANIM_RESEARCH_MAX_PAGE_CHARS: int = 15000`
   - `ANIM_RESEARCH_MAX_TOTAL_CHARS: int = 45000`
4. Implement offline mock adapters (`MockSearchTool`, `MockExtractTool`) for deterministic testing.
5. Strict execution safety:
   - Validate arguments with Pydantic before dispatch.
   - Enforce call count, timeout, and character limits.
   - Sanitize all returned logs so API keys are never leaked.
6. Comprehensive test suite in `backend/tests/test_research_tools.py`.

### Acceptance Criteria:
- `pytest backend/tests` passes 100% offline without requiring internet access or paid API keys.
- Mock search and extract tools return valid structured records.
- Passing invalid arguments or unregistered tool names raises explicit validation errors.
- Exceeding call budget, byte limit, or timeout raises typed domain errors.
- Zero architectural import violations (`test_import_rules.py` remains green).
- Zero changes to existing script builder APIs, CLI, or UI.

---

**Report complete.** Awaiting higher-up review and explicit authorization before proceeding with Phase 1.
