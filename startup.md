# Local Development Startup Guide: AI Animation Studio

This guide explains how to start, use, and stop the local **AI Animation Studio** backend and the **Script Builder** web user interface on Windows.

All commands in this guide assume you are running **PowerShell** from the repository root:
```
C:\Lakshya\Coding\EquinoLabs\ai-animation-studio
```

---

## 1. Prerequisites

Before starting, confirm that your system has the required runtimes installed:

| Requirement | Supported Version | Notes |
|---|---|---|
| **Operating System** | Windows 10/11 | Uses standard PowerShell |
| **Python** | 3.11+ (verified on 3.13.2) | Virtual environment located at `backend\.venv` |
| **Node.js & npm** | Node v20+ / npm v10+ (verified on Node v24.22.0) | For the Vite + React frontend |
| **FFmpeg** | Any modern build in PATH | Required for downstream rendering milestones |

---

## 2. Complete Startup Sequence (Step-by-Step)

The application requires **two separate PowerShell terminals** running concurrently:
- **Terminal 1:** Runs the local FastAPI backend on `http://127.0.0.1:8000`.
- **Terminal 2:** Runs the local Vite development server on `http://127.0.0.1:5173`.

---

### Step 1: Start the Backend Server (Terminal 1)

1. Open PowerShell and navigate to the repository root:
   ```powershell
   cd C:\Lakshya\Coding\EquinoLabs\ai-animation-studio
   ```

2. Activate the Python virtual environment:
   ```powershell
   .\backend\.venv\Scripts\activate
   ```
   > **Verification:** Your terminal prompt will display `(.venv)`. You can verify the active interpreter with:
   > ```powershell
   > python --version
   > where.exe python
   > ```
   > The first path returned by `where.exe python` must be `...\backend\.venv\Scripts\python.exe`.

3. Launch the FastAPI local backend server:
   ```powershell
   # Default mode (uses local Ollama model from root .env):
   python -m backend.app.cli.main --serve --host 127.0.0.1 --port 8000

   # Or explicitly override provider if desired (e.g. mock):
   python -m backend.app.cli.main --serve --host 127.0.0.1 --port 8000 --provider mock
   ```

4. **Expected Output:**
   ```text
   Starting local API server on http://127.0.0.1:8000 ...
     Active Script LLM Provider: OLLAMA
     Ollama Target: qwen2.5:7b at http://localhost:11434
   INFO:     Started server process [...]
   INFO:     Waiting for application startup.
   INFO:     Application startup complete.
   INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
   ```
   > **Important:** Leave Terminal 1 running while using the studio.

---

### Step 2: Start the Frontend UI (Terminal 2)

1. Open a **second** PowerShell window at the repository root:
   ```powershell
   cd C:\Lakshya\Coding\EquinoLabs\ai-animation-studio
   ```

2. Launch the Vite development server (run directly from the root):
   ```powershell
   npm --prefix frontend run dev
   ```

3. **Expected Output:**
   ```text
     VITE v6.4.5  ready in 300 ms

     ➜  Local:   http://127.0.0.1:5173/
     ➜  press h + enter to show help
   ```
   > **Important:** Leave Terminal 2 running alongside Terminal 1.

---

### Step 3: Open the Web UI & Verify Connectivity

1. Open your web browser and navigate to:
   ```
   http://127.0.0.1:5173
   ```
2. Check the connectivity indicator in the top-right header:
   - **`● API Online (127.0.0.1:8000)`** in green: The frontend is successfully communicating with the local backend.
   - If it displays **`● API Offline (Disconnected)`** in red, ensure Terminal 1 is still running and see the [Troubleshooting](#5-troubleshooting) section below.

---

## 3. Script Builder Workflow Walkthrough

Once both servers are running:

1. **Create or Select a Project:**
   - Click the **`+ New Project`** button in the top bar.
   - Enter a title (e.g. `Currency Invention Explainer`) and click **Create Project**.
   - The project is initialized under `projects/<project_id>/` with an isolated artifact store.

2. **Generate Initial Script Draft:**
   - In the script editor form, enter a topic (e.g. `How did humans invent currency, and why was it needed?`).
   - Choose a target duration (e.g. `5.0 min`).
   - Click **Generate Script Draft**.
   - The editor displays the generated multi-scene structure (`hook`, `setup`, `middle`, `climax`, `takeaway`) with voiceover narration and visual anime cues. The draft starts in `v1` with status **`Draft (Unreviewed)`**.

3. **Revise the Script Conversationally:**
   - Use the **Revision Assistant** on the right panel.
   - Enter an instruction (e.g. `Emphasize the double coincidence of wants and show cowrie shell trade`) or click a suggestion chip.
   - Click **Send Revision**.
   - A new draft (`v2`) is generated with the requested adjustments, and the revision event is added to the conversation timeline.

4. **Accept or Reject a Draft:**
   - **Reject Draft:** Click **`✕ Reject Draft`** to provide optional feedback. This sets status to **`Rejected (Needs Revision)`** and prevents accidental finalization.
   - **Accept Draft:** Click **`✓ Accept Draft`**. The status changes to **`Accepted (Ready to Finalize)`**, which unlocks the Finalize button.

5. **Finalize the Script:**
   - Click **`🔒 Finalize Script`**.
   - A confirmation dialog explains that this writes an immutable artifact to `script/finalized/vXXX.json` (and mirrors it at `script/script.json`) to establish it as the official downstream truth for Scene Planning and Voiceover.
   - Click **Confirm & Finalize Version**. The status updates to **`Finalized`**.

6. **Inspect History & Versions:**
   - Click **`📜 History / Versions`** to inspect previous drafts (`v1`, `v2`, etc.) and finalized artifacts side-by-side without mutating them.
   - Refreshing the browser (`F5`) confirms that all project data, script drafts, and chat history persist on disk.

---

## 4. Stopping the Servers

When you are finished using the studio:

1. In **Terminal 1** (Backend): Press `Ctrl + C`.
2. In **Terminal 2** (Frontend): Press `Ctrl + C` (and type `Y` if prompted to terminate the batch job).

---

## 5. Troubleshooting

### Port 8000 or 5173 is Already in Use
If either port is occupied by a background or orphan process:
```powershell
# Identify processes listening on port 8000 or 5173
Get-NetTCPConnection -LocalPort 8000, 5173 -ErrorAction SilentlyContinue | Select-Object LocalPort, OwningProcess

# Terminate process by PID if necessary (replace <PID> with the OwningProcess number)
Stop-Process -Id <PID> -Force
```

### Python Module Invocation Error (`No module named backend`)
If running `python -m backend.app.cli.main ...` fails:
1. Confirm you are in the repository root (`C:\Lakshya\Coding\EquinoLabs\ai-animation-studio`).
2. Confirm the virtual environment is activated (`.\backend\.venv\Scripts\activate`).
3. Confirm `where.exe python` points to `backend\.venv\Scripts\python.exe`.

### Frontend Shows "API Offline (Disconnected)"
1. Verify that Terminal 1 is still running without errors.
2. Test the backend health endpoint directly in PowerShell:
   ```powershell
   Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/health"
   ```
   Expected response: `@{service=ai-animation-studio; status=ok}`.
3. If the backend is running but the UI is disconnected, ensure no proxy or firewall is blocking loopback traffic on `127.0.0.1`.

### LLM Provider Settings: Real Ollama vs. Mock Provider

- **Real Ollama Model (Default via root `.env`):** Real LLM generation using your local Ollama daemon and installed models (e.g. `qwen2.5:7b`).
  ```powershell
  python -m backend.app.cli.main --serve
  ```
  Ensure Ollama is running (`curl http://127.0.0.1:11434/api/tags`) and model `qwen2.5:7b` is pulled before generating scripts.
- **Mock Provider (Optional override):** Fast, deterministic offline generation for unit testing or offline debugging.
  ```powershell
  python -m backend.app.cli.main --serve --provider mock
  ```
