# Architecture & Implementation Document: P2 Local Script Builder Chat UI

## 1. Overview & Objective

Task P2 introduces the local web user interface for the Script Builder stage of the AI Animation Studio (`EquinoLabs/ai-animation-studio`).

The web UI provides a clean, local-first studio workspace allowing the creator to:
1. Select, view, and create local animation projects.
2. Initialize an explainer script draft from a user topic with target duration and title settings using the configured local LLM provider (`MockLLMProvider` or `OllamaLLMProvider`).
3. View and inspect structured script sections (`hook`, `setup`, `middle`, `climax`, `takeaway`) with voiceover narration and visual anime cues.
4. Interact conversationally with the revision assistant to iteratively refine the draft.
5. Explicitly accept or reject drafts with actionable feedback.
6. Explicitly finalize accepted drafts into immutable downstream artifacts via a confirmation safety modal.
7. Browse historical draft revisions and finalized artifacts.

---

## 2. System Architecture & Communication Boundaries

```
┌────────────────────────────────────────────────────────┐
│                   Vite / React Web UI                  │
│               (http://127.0.0.1:5173)                  │
│                                                        │
│  [ProjectBar]  [ScriptViewer]  [ChatPanel]  [Modals]   │
└──────────────────────────┬─────────────────────────────┘
                           │ Reverse Proxy: /api/*
                           ▼
┌────────────────────────────────────────────────────────┐
│                  FastAPI Backend Server                │
│               (http://127.0.0.1:8000)                  │
│                                                        │
│  [API Router: /api/v1/*] (CORS enabled for localhost)  │
└────────────┬─────────────────────────────┬─────────────┘
             │                             │
             ▼                             ▼
┌───────────────────────────┐ ┌──────────────────────────┐
│   ProjectService          │ │   PipelineOrchestrator   │
│   (backend/app/services/  │ │   (backend/app/services/ │
│    project)               │ │    pipeline)             │
└───────────────────────────┘ └────────────┬─────────────┘
                                           │
                                           ▼
                              ┌──────────────────────────┐
                              │   ScriptService          │
                              │   (backend/app/services/ │
                              │    scriptwriter)         │
                              └──────────────────────────┘
```

### Architectural Isolation & Boundary Invariants
In accordance with TechSpec Section 3 and `backend/tests/test_import_rules.py`:
- `backend/app/api/routes.py` imports only `backend.app.services.pipeline.orchestrator` and `backend.app.services.project.service`.
- Neither `backend/app/api` nor `backend/app/cli` ever imports `backend.app.services.scriptwriter` directly.
- All script operations (start, revise, accept, reject, finalize, history, versions, state) are mediated by `PipelineOrchestrator`.
- The frontend operates strictly through REST calls against `127.0.0.1:8000` (proxied via Vite on `127.0.0.1:5173`). No external cloud databases, SaaS dependencies, or telemetry are used.

---

## 3. REST API Contract (`/api/v1`)

| Method | Path | Description | Key Status Codes |
|---|---|---|---|
| `GET` | `/health` | Server health check | 200 |
| `GET` | `/projects` | List all local projects | 200 |
| `POST` | `/projects` | Create a new local project directory & metadata | 201, 422 |
| `GET` | `/projects/{id}` | Get metadata for a specific project | 200, 404 |
| `GET` | `/projects/{id}/script` | Get active draft & workflow state | 200, 404 |
| `POST` | `/projects/{id}/script/start` | Generate initial draft from topic | 201, 404, 422, 503 |
| `POST` | `/projects/{id}/script/revise` | Generate revised draft from user instruction | 200, 404, 422, 503 |
| `POST` | `/projects/{id}/script/accept` | Accept active draft version | 200, 404, 400 |
| `POST` | `/projects/{id}/script/reject` | Reject active draft version with optional reason | 200, 404 |
| `POST` | `/projects/{id}/script/finalize` | Commit accepted draft into immutable artifact (`script/finalized/vXXX.json`) | 200, 400, 404 |
| `GET` | `/projects/{id}/script/history` | Chronological conversation events | 200, 404 |
| `GET` | `/projects/{id}/script/versions` | List all draft versions | 200, 404 |
| `GET` | `/projects/{id}/script/finalized`| List all immutable finalized versions | 200, 404 |

---

## 4. Frontend Component Breakdown

1. **`App.tsx`**: Top-level coordinator managing active project selection, data fetching, global errors, and modal toggles.
2. **`ProjectBar.tsx`**: Displays active project selector, new project modal form, and local server status.
3. **`ScriptViewer.tsx`**:
   - Initial state: Form to start script from topic, target duration, and title.
   - Active state: Header with version badge, status pill (`Draft`, `Accepted`, `Rejected`, `Finalized`), word count, and action buttons (`Accept Draft`, `Reject Draft`, `Finalize Script`, `History / Versions`).
   - Section cards: Displays each script section with role tags (`hook`, `setup`, `middle`, `climax`, `takeaway`), narration block, and visual notes.
4. **`ChatPanel.tsx`**:
   - Scrollable event stream displaying user instructions, system generations, rejections with reasons, and finalization notices.
   - Suggestion chips for rapid prompt creation.
   - Instruction textarea and submission handling.
5. **`FinalizeModal.tsx`**: Confirmation modal warning that finalization creates an immutable artifact and commits the script stage as complete for downstream pipeline stages.
6. **`RejectModal.tsx`**: Modal capturing optional rejection reasons to direct upcoming revisions.
7. **`VersionsModal.tsx`**: Inspector modal allowing tabbed switching between historical drafts and finalized artifacts.

---

## 5. Verification & Testing

- **Backend Unit & Integration Tests (`backend/tests/test_api.py`):**
  - Verified health check, project CRUD, initial empty state, full script start $\rightarrow$ unaccepted finalization blocked $\rightarrow$ revise $\rightarrow$ reject $\rightarrow$ accept $\rightarrow$ finalize lifecycle, and input validation failures (400, 404, 422).
- **Import Rule AST Check (`backend/tests/test_import_rules.py`):**
  - Confirmed 0 architectural violations across `api`, `cli`, and `services`.
- **Full Backend Suite:** 85/85 tests passed.
- **Frontend Build:** `npm --prefix frontend run build` succeeded with zero TypeScript or bundling errors.
