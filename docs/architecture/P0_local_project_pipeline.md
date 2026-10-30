# Task P0: Local Project & Pipeline Foundation Architecture

Date: 2026-10-29  
Status: Implemented & Tested  

## 1. Overview
Task P0 establishes the local filesystem project persistence engine, versioned metadata contract, pipeline orchestration layer, and command-line interface for `ai-animation-studio`. It aligns with the local-first product direction prior to any SaaS infrastructure consideration.

## 2. Local Project Directory Layout
All projects are stored as self-contained directories under `projects/`:

```text
projects/
└── proj_<id>/
    ├── metadata.json           # Machine-portable project metadata and stage states
    ├── script/                 # Script generation artifacts (script.json)
    ├── scene_planning/         # Scene planning manifests (scene_plan.json)
    ├── script_to_audio/        # TTS audio files (.wav) and timings
    ├── video/                  # Rendered scene MP4s and final video
    └── runs/                   # Individual execution run manifests (<run_id>.json)
```

### Safety & Storage Principles
- **No Database:** Projects are managed purely through filesystem hierarchies.
- **Portability:** `metadata.json` never stores machine-specific absolute paths; all artifact references are relative to the project root.
- **Atomic Writes:** All metadata updates write to a temporary file (`metadata_*.tmp`) before performing an atomic `os.replace` to prevent corruption on interruption.
- **Path Traversal Protection:** Project IDs are validated against `^[a-zA-Z0-9_-]+$`, and paths are verified to strictly reside directly within the configured `projects_root`.
- **Title Collisions:** Project IDs are stable and unique (e.g. `proj_<uuid4_hex>`); projects with identical human-readable titles receive distinct project directories.

## 3. Metadata Contract (`metadata.json`)

```json
{
  "project_id": "proj_a1b2c3d4e5f6",
  "title": "Why We Overthink",
  "created_at": "2026-10-29T07:40:00.000000Z",
  "updated_at": "2026-10-29T07:40:00.000000Z",
  "schema_version": "1.0.0",
  "stages": {
    "script": {
      "status": "not_started",
      "updated_at": null,
      "error": null,
      "artifacts": []
    },
    "plan-scenes": {
      "status": "not_started",
      "updated_at": null,
      "error": null,
      "artifacts": []
    },
    "script-to-voice": {
      "status": "not_started",
      "updated_at": null,
      "error": null,
      "artifacts": []
    },
    "video": {
      "status": "not_started",
      "updated_at": null,
      "error": null,
      "artifacts": []
    }
  }
}
```

Stage statuses: `not_started`, `in_progress`, `completed`, `failed`.

## 4. Pipeline Stages & Prerequisite Contracts
The orchestrator coordinates 4 distinct stages with strict prerequisite validation:

1. **`script` (Script Builder):**
   - *Prerequisites:* None (can execute on an empty or in-progress project).
   - *Output:* `script/script.json` (status: `"final"` upon completion).
2. **`plan-scenes` (Scene Planner):**
   - *Prerequisites:* `script/script.json` must exist, parse as `Script`, and possess status `"final"`.
   - *Output:* `scene_planning/scene_plan.json`.
3. **`script-to-voice` (TTS Voiceover):**
   - *Prerequisites:* `script/script.json` must exist, parse as `Script`, and possess status `"final"`.
   - *Output:* Narrated audio files (`*.wav`) and timings in `script_to_audio/`.
4. **`video` (Video Production):**
   - *Prerequisites:* `scene_planning/scene_plan.json` must exist and parse as `ScenePlan`, AND `script_to_audio/` must contain audio artifacts.
   - *Output:* Rendered MP4 in `video/`.
5. **`full` (End-to-End Pipeline):**
   - Executes `script` $\to$ `plan-scenes` $\to$ `script-to-voice` $\to$ `video` in dependency order.
   - If any stage fails, execution halts immediately, preventing dependent downstream stages from executing.

### Unimplemented Stage Reporting
- Real business logic for these 4 stages is deferred to subsequent tasks.
- In accordance with safety criteria, placeholder stage handlers explicitly raise `StageNotImplementedError` and record failure in the run manifest rather than reporting false success.
- Stage handlers can be mocked or injected for tests via `orchestrator.register_stage_handler(stage_name, handler)`.

## 5. CLI Interface

Syntax supported from repository root:
```powershell
# Create project
python -m backend.app.main --new-project --title "Why We Overthink"

# List projects
python -m backend.app.main --list-projects

# Run individual stage
python -m backend.app.main --script --project proj_<id>
python -m backend.app.main --plan-scenes --project proj_<id>
python -m backend.app.main --script-to-voice --project proj_<id>
python -m backend.app.main --video --project proj_<id>

# Run full pipeline
python -m backend.app.main --full --project proj_<id>

# Optional flags: --projects-dir <path>, --json
```

*(Note: When `$env:PYTHONPATH="backend"`, `python -m app.main ...` is equally supported).*
