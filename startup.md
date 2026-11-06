# Local Development Startup Guide: AI Animation Studio

This guide explains how to configure, start, use, and troubleshoot the local **AI Animation Studio** backend, **Script Builder** web studio, and command-line interface on Windows.

All commands in this guide assume you are running **PowerShell** from the repository root:
```powershell
cd C:\Lakshya\Coding\EquinoLabs\ai-animation-studio
```

---

## 1. Prerequisites and Environment

Confirm the required runtimes and configurations before launching the studio:

| Component | Supported Version / Location | Notes |
| :--- | :--- | :--- |
| **Operating System** | Windows 10/11 | Uses standard PowerShell |
| **Python** | 3.11+ (verified on 3.13.2) | Virtual environment located strictly at `backend\.venv` |
| **Node.js & npm** | Node v20+ / npm v10+ (verified on Node v24.22.0) | For the Vite + React frontend |
| **Ollama** | Local daemon running at `http://localhost:11434` | Required for local LLM script planning and drafting |
| **FFmpeg** | Any modern build in PATH | Required for downstream rendering milestones |

### Virtual Environment Setup
All backend execution and tests must run within `backend/.venv`.
To install dependencies or activate manually:
```powershell
# Activate backend virtual environment (PowerShell)
.\backend\.venv\Scripts\Activate.ps1

# Verify active interpreter
python -c "import sys; print(sys.executable)"
# Output must resolve to: ...\backend\.venv\Scripts\python.exe
```

### Environment Configuration (`.env`)
The application reads configuration from the repository-root `.env` file (copied from `.env.example`):
- **Ollama Settings:**
  - `ANIM_LLM_SCRIPT_PROVIDER=ollama` (or `mock` for offline unit testing)
  - `ANIM_OLLAMA_MODEL=qwen2.5:7b`
  - `ANIM_OLLAMA_HOST=http://localhost:11434`
  - `ANIM_OLLAMA_TIMEOUT_SECONDS=60.0`
- **Research Provider Settings:**
  - `ANIM_TAVILY_API_KEY`: Required for live web search queries.
  - `ANIM_FIRECRAWL_API_KEY`: Required for web page scraping/content extraction.
  - `ANIM_FIRECRAWL_API_URL=https://api.firecrawl.dev/v1/scrape`: Extraction endpoint.
  - `ANIM_RESEARCH_MAX_SEARCH_CALLS=3`: Call budget limit.
  - `ANIM_RESEARCH_MAX_EXTRACT_CALLS=3`: Extract budget limit.
  - `ANIM_RESEARCH_CALL_TIMEOUT_SECONDS=15.0`: Per-request timeout.
  - `ANIM_RESEARCH_DEADLINE_SECONDS=60.0`: Total research run deadline.
  - `ANIM_RESEARCH_MAX_PAGE_CHARS=15000`: Per-page character ceiling.
  - `ANIM_RESEARCH_MAX_TOTAL_CHARS=45000`: Aggregate character ceiling.

> [!IMPORTANT]
> **Credential Security:** Never commit `.env` to source control. Never print, log, copy, or expose API key values in shell output or reports.

---

## 2. Normal Application Startup

The repository provides a unified root entry point ([`main.py`](file:///C:/Lakshya/Coding/EquinoLabs/ai-animation-studio/main.py)) that manages the application lifecycle.

### Single-Command Studio Launch
To launch both the backend API and frontend studio with automatic browser launch:
```powershell
python main.py --studio
# or
python main.py --script-builder
```

### How the Root Launcher Operates
1. **Virtual Environment Guarantee:** The root `main.py` detects the active Python interpreter. If invoked via global Python, it automatically re-executes itself under `backend/.venv/Scripts/python.exe`.
2. **Port Awareness & Process Reuse:**
   - Checks if the backend (`127.0.0.1:8000`) or frontend (`127.0.0.1:5173`) is already active.
   - If either process is already running, it attaches to the existing instance without crashing or port-conflict errors.
   - If a service is not running, it spawns it:
     - Backend: `uvicorn backend.app.main:app --host 127.0.0.1 --port 8000`
     - Frontend: `npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173`
3. **Browser Integration:** Opens [`http://127.0.0.1:5173`](http://127.0.0.1:5173) in your default web browser.

### Stopping the Studio
To stop the studio:
- Press `Ctrl + C` in the launcher terminal.
- The launcher catches `KeyboardInterrupt` and cleanly stops all child process trees (both Uvicorn and Vite/Node).

### Alternative: Two-Terminal Manual Startup
If you prefer separate, dedicated log streams for backend and frontend during development:
- **Terminal 1 (Backend):**
  ```powershell
  backend\.venv\Scripts\python.exe -m backend.app.cli.main --serve --host 127.0.0.1 --port 8000
  ```
- **Terminal 2 (Frontend):**
  ```powershell
  npm --prefix frontend run dev
  ```
- Open your browser to [`http://127.0.0.1:5173`](http://127.0.0.1:5173). Verify that the header displays **`● API Online (127.0.0.1:8000)`** in green.

---

## 3. Command-Line Interface (CLI)

The CLI allows running pipeline stages, creating projects, and managing scripts directly from PowerShell using `python main.py [options]`.

### Supported Commands & Stage Implementation Status

| Command / Flag | Alias | Implementation Status | Behavior & Scope |
| :--- | :--- | :--- | :--- |
| `--studio` | `--script-builder` | **Fully Implemented** | Starts backend and frontend dev servers, opens browser. |
| `--list-projects` | — | **Fully Implemented** | Lists all local projects in `projects/` with last-updated timestamps (supports `--json`). |
| `--new-project` | — | **Fully Implemented** | Initializes a project directory and `metadata.json` (requires `--title`, optional `--project <id>`). |
| `--script` | `--script-writing` | **Fully Implemented** | Complete script lifecycle (research-grounded generation, revision, accept, reject, finalize, outline). |
| `--plan-scenes` | `--scene-builder` | **Prerequisites Implemented** *(Stage Engine Unimplemented)* | Validates prerequisite (`script/script.json` status must be `final`). Writes run manifest. Downstream scene planner is not yet implemented (Phase 8 milestone); raises `StageNotImplementedError` once prerequisites pass. |
| `--script-to-voice`| — | **Prerequisites Implemented** *(Stage Engine Unimplemented)* | Validates prerequisite (`script/script.json` status `final`). Downstream voice generator not yet implemented. |
| `--video` | — | **Prerequisites Implemented** *(Stage Engine Unimplemented)* | Validates prerequisites (`scene_plan.json` and audio in `script_to_audio/`). Downstream renderer not yet implemented. |
| `--full` | — | **Orchestration Implemented** *(Downstream Stages Pending)* | Executes pipeline in order: `script` → `plan-scenes` → `script-to-voice` → `video`. Stops and records failure at first unfulfilled prerequisite or unimplemented stage. Does not falsely claim success. |

### CLI Example Workflows (Windows PowerShell)

```powershell
# 1. List all existing projects
python main.py --list-projects

# 2. Create a new project
python main.py --new-project --title "Why Humans Form Habits" --project p7_habit_01

# 3. Generate an initial research-grounded script draft (using local Ollama)
python main.py --script-writing --project p7_habit_01 --topic "Why do humans form habits, and how does habit formation work?" --provider ollama

# 4. View active script draft in terminal
python main.py --script --show-script --project p7_habit_01

# 5. Revise the script conversationally
python main.py --script --instruction "Focus on basal ganglia and dopamine reward loop" --project p7_habit_01

# 6. View conversation and revision history
python main.py --script --history --project p7_habit_01

# 7. Accept the draft (marks draft ready for finalization)
python main.py --script --accept --project p7_habit_01

# 8. Finalize the draft (writes immutable script/finalized/vXXX.json and script/script.json)
python main.py --script --finalize --project p7_habit_01

# 9. Run Scene Planning (enforces prerequisite: checks script.json is finalized)
python main.py --scene-builder --project p7_habit_01

# 10. Run Full Pipeline
python main.py --full --project p7_habit_01
```

---

## 4. Development and Testing Rules

Follow these standards to ensure project stability:

1. **Use Project Runtimes:** Always execute backend commands and tests using `backend/.venv/Scripts/python.exe`. Do not use global system Python.
2. **Use Repository Entry Points:** Do not create temporary or substitute frontends/backends. Testing must operate through `main.py`, `backend.app.main:app`, or the official React Vite frontend.
3. **Mocks vs. Live Providers:**
   - In automated unit/regression tests, mocks are permitted only via explicit test injection (e.g. `use_mocks=True` or `MockSearchTool`).
   - Mock tools are **never** silently selected in production runtime. If real API keys are missing in production, the application raises an actionable `MissingCredentialsError` (HTTP 400).
4. **Shared Project Store:** Both the CLI and API operate against the common `projects/` root directory.
5. **No Architectural Violations:** Backend architecture enforces clean separation: routes → orchestrator → services → domain. Verify with:
   ```powershell
   backend\.venv\Scripts\python.exe -m pytest backend/tests/test_import_rules.py -v
   ```

---

## 5. Troubleshooting

### Port 8000 or 5173 Already in Use
If port `8000` or `5173` is occupied by an orphaned process:
```powershell
# Identify processes listening on ports
Get-NetTCPConnection -LocalPort 8000, 5173 -ErrorAction SilentlyContinue | Select-Object LocalPort, OwningProcess

# Terminate process by PID (replace <PID> with OwningProcess number)
Stop-Process -Id <PID> -Force
```

### Frontend Shows "API Offline (Disconnected)"
1. Verify the backend process is running:
   ```powershell
   Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/health"
   # Expected response: @{service=ai-animation-studio; status=ok}
   ```
2. If the backend is running but the UI shows disconnected:
   - Ensure you accessed `http://127.0.0.1:5173` directly.
   - Vite proxy rules in [`frontend/vite.config.ts`](file:///C:/Lakshya/Coding/EquinoLabs/ai-animation-studio/frontend/vite.config.ts) automatically forward `/api` requests to `http://127.0.0.1:8000`. Confirm loopback routing is not blocked by local VPN/firewall software.

### Ollama Not Responding / Missing Model
If script generation fails with an Ollama connection error:
1. Confirm the Ollama daemon is running:
   ```powershell
   curl http://127.0.0.1:11434/api/tags
   ```
2. Confirm the required model is installed:
   ```powershell
   ollama list
   # If qwen2.5:7b is missing:
   ollama pull qwen2.5:7b
   ```

### Missing Credentials Error (`MissingCredentialsError`)
If research initiation fails with HTTP 400:
- Live web search requires `ANIM_TAVILY_API_KEY` in `.env`.
- Web page extraction requires `ANIM_FIRECRAWL_API_KEY` in `.env`.
- For offline development without external credentials, supply `allow_unresearched=True` or run CLI commands with `--allow-unresearched`.

### Prerequisite Failure on Downstream Stages
If `--plan-scenes`, `--script-to-voice`, or `--video` fails with `PrerequisiteError`:
- Confirm that the project has a finalized script artifact (`script/script.json`) with `status="final"` and non-empty narration text.
- Script drafts (`status="draft"` or `"accepted"`) must be explicitly finalized via `python main.py --script --finalize --project <id>` or the UI Finalize button before downstream stages can be initiated.
