# AI Animation Studio

Personal AI studio that turns a chat-written script into a narrated 2D animated explainer video: scene planning, TTS voiceover, captions, and a rigged cutout character rendered with Skia.

---

## Supported Tools & Runtime Environment
- **Operating System:** Windows 10/11 (or Linux/macOS)
- **Python:** Python 3.11+ (verified on Python 3.13.2)
- **Node.js:** Node.js v20+ (verified on v24.22.0) with npm
- **FFmpeg:** FFmpeg with `h264_nvenc` and `libx264` support

---

## Backend Setup & Verification

### 1. Environment Setup (Windows)
```powershell
# Create isolated virtual environment
py -m venv backend/.venv

# Activate environment and install dependencies
.\backend\.venv\Scripts\pip.exe install -r .\backend\requirements-dev.txt
```

### 2. Run Automated Test Suite
```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -v
```

This verifies:
- Data contract validation and settings loading.
- Strict architectural boundary enforcement via AST import analysis (`test_import_rules.py`).
- Hardware encoder selection (`h264_nvenc`) and software fallback (`libx264`) without GPU hardware dependencies (`test_encoder.py`).

### 3. Launching Local Studio & API
```powershell
# Start local backend API server (binds to 127.0.0.1:8000)
.\backend\.venv\Scripts\python.exe -m backend.app.cli.main --serve --port 8000

# In a separate terminal, start frontend dev server (binds to 127.0.0.1:5173)
cd frontend
npm run dev
```
Open `http://localhost:5173` in your browser.

---

## Frontend Setup & Build

### 1. Installation
```powershell
cd frontend
npm install
```

### 2. Development & Production Build
```powershell
# Run dev server
npm run dev

# Run TypeScript check and production build
npm run build
```

---

## Current Status & Roadmap

### What Currently Works (Milestone 0)
- Complete backend package architecture and entry points.
- Pydantic domain data contracts for all 5 pipeline stages.
- AST-based import-rule boundary test preventing forbidden coupling (e.g. services importing API, renderer importing LLM, or clipper dependencies).
- Video encoder abstraction with NVENC probe testing and libx264 fallback.
- Frontend React + TypeScript + Vite project scaffold.
- Complete documentation and repository directives (`CLAUDE.md`, `GEMINI.md`, `docs/architecture/M0_bootstrap_architecture.md`).

### Intentionally Not Implemented Yet
- Character rendering, face overlays, and lip sync (scheduled for **M1**).
- Voiceover TTS adapter and ASS caption generation (scheduled for **M2**).
- Scene planning and manifest coverage engine (scheduled for **M3**).
- Scriptwriting conversational chat engine (scheduled for **M4**).
- Production UI screens and multi-character workflows (scheduled for **M5**–**M6**).
