# AI Animation Studio — Instructions and Engineering Directives

## 1. Project Purpose & Architectural Boundaries
`ai-animation-studio` is a standalone application that generates 2D animated explainer videos using an original rigged cutout character, backgrounds, props, TTS voiceover, and captions.

### Strict Boundary Rules
- **No Clipper Coupling:** Never import, reference, or depend on the old `ai-video-clipper` codebase. Interaction with external tools is strictly file-based (passing exported MP4s).
- **No Unauthorized Research Folder Access:** Never access local research spikes or scratch directories (`Figure-Build`, `Ai-Animator`, etc.) unless explicitly instructed by the Lead Architect.
- **Architectural Layering:**
  - `backend/app/services/*` may import `domain`, `config`, and `llm`. Never import `api` or `cli`.
  - `backend/app/services/renderer` and `backend/app/services/characters` must remain completely independent of AI/LLM modules (`llm/`, `scriptwriter/`, `planner/`). Rendering runs without AI.
  - `backend/app/api` and `backend/app/cli` invoke `services/pipeline` only, and nothing deeper.
  - Runtime artifacts must be written to `data/<run_id>/` and never into `assets/`.

## 2. Document Precedence & Conflict Resolution
- Requirements are defined in:
  1. `docs/init/AI Animation Studio Technical Specification.md` (Technical implementation, performance targets, and architectural structure)
  2. `docs/init/AI Animation Studio PRD and Architecture.md` (Product requirements and functional specs)
- If the TechSpec and PRD conflict, or if an ambiguity arises:
  - Document the conflict explicitly.
  - Do **not** silently invent a decision or rewrite specs to fit code.
  - Escalate to the Lead Architect for clarification.

## 3. Scope of Edits & Work Lanes
Edits must be strictly confined to the lane assigned for the specific task:
- **Renderer & Characters Lane:** `backend/app/services/renderer/`, `backend/app/services/characters/`, `assets/characters/`, `tools/`
- **AI & Script Lane:** `backend/app/services/scriptwriter/`, `backend/app/services/planner/`, `backend/app/llm/`, `backend/app/domain/`
- **Voice & Captions Lane:** `backend/app/services/tts/`
- **API & Pipeline Lane:** `backend/app/services/pipeline/`, `backend/app/api/`, `backend/app/cli/`
- **Frontend Lane:** `frontend/`

Files and directories off-limits unless explicitly authorized:
- `docs/init/*` (historical baseline specifications)
- External or parent directories outside this repository

## 4. Verification & Testing Requirements
- Every change must be verified with automated unit/integration tests before completion.
- Always execute tests in the isolated environment (`backend/.venv\Scripts\python.exe -m pytest backend/tests`).
- **Reporting Integrity:**
  - Clearly distinguish between **measured results** (benchmarks, timing, memory) and **estimates / targets**.
  - Always report exact commands executed and true outcomes (Passed / Failed / Not run / Blocked).
  - Never claim a test passed unless it was executed in the current session.
  - Detail any deviations, residual risks, or unresolved items in execution summaries.
