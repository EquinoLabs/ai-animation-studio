# P1: Script Builder Backend Architecture & Technical Design

## 1. Overview & Objectives

The Script Builder backend powers the initial stage of the AI Animation Studio video generation pipeline. It enables turning a topic or idea into a structured, narrated explainer video script through an iterative drafting and revision workflow before explicit finalization.

### Core Principles
- **Local-First & Offline-Ready:** Uses local file persistence and deterministic mock providers for testing with zero network access required.
- **Provider-Agnostic LLM Interface:** Integrates an abstract LLM interface (`LLMProvider`) supporting structured JSON generation (`generate_json`), with a production-grade local Ollama adapter (`OllamaLLMProvider`) and an in-memory test double (`MockLLMProvider`).
- **Strict Domain Contracts:** Enforces structural integrity, non-empty narration, unique section IDs, and explicit lifecycle states (`draft` and `final`).
- **Immutable Finalization & Revisions:** Finalized script artifacts are immutable. Subsequent revisions create new draft versions without overwriting previously finalized artifacts, ensuring downstream stages (scene planning, TTS) always have a stable reference.
- **Clean Dependency Boundaries:** API and CLI layers access pipeline/project services only, remaining completely decoupled from internal scriptwriter implementations.

---

## 2. Script Data Contract

The data contracts are defined in `backend/app/domain/script.py` and re-exported through `backend.app.domain.contracts`:

### `ScriptSection`
Represents a distinct structural narrative section suitable for later beat planning.

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Unique stable section identifier (e.g. `sec_01_hook`). |
| `type` | `str` | Section role: `hook`, `setup`, `middle`, `climax`, or `takeaway`. |
| `heading` | `str` | Descriptive headline or topic anchor. |
| `narration` | `str` | Spoken narrative voice text. |
| `notes` | `Optional[str]` | Optional visual directions and scene suggestions. |
| `name` | `Optional[str]` | Backwards-compatible alias for `type`/`heading`. |

### `Script`
Validated script draft or finalized artifact.

| Field | Type | Description |
|---|---|---|
| `script_id` | `str` | Unique script identifier (e.g. `scr_...`). |
| `project_id` | `str` | ID of the parent project. |
| `version` | `int` | Monotonically increasing revision number ($\ge 1$). |
| `status` | `Literal["draft", "final"]` | Lifecycle state. |
| `title` | `str` | Non-empty title ($\le 200$ chars). |
| `topic` | `str` | Source prompt or subject topic ($\le 1000$ chars). |
| `target_duration_minutes` | `float` | Estimated target video duration ($0.1 - 60.0$ min). |
| `sections` | `List[ScriptSection]` | Ordered list of narrative sections. |
| `created_at` | `datetime` | Timezone-aware UTC timestamp. |
| `updated_at` | `datetime` | Timezone-aware UTC timestamp of last update. |

### `ConversationEvent`
Chronological event representing user interactions and system drafting events.

| Field | Type | Description |
|---|---|---|
| `event_id` | `str` | Unique event identifier (`evt_...`). |
| `timestamp` | `datetime` | UTC timestamp of occurrence. |
| `event_type` | `ConversationEventType` | One of `topic_submission`, `draft_generated`, `revision_instruction`, `revision_result`, `draft_accepted`, `draft_rejected`, `script_finalized`. |
| `content` | `Optional[str]` | Text prompt, instruction, title, or reason. |
| `script_version` | `Optional[int]` | Related script version number. |
| `metadata` | `Dict[str, Any]` | Extensible metadata dictionary. |

---

## 3. Persistence Layout

Scripts and conversation history reside within each project's isolated directory tree under `projects/proj_<id>/script/`:

```text
projects/
└── proj_<id>/
    ├── metadata.json
    ├── script/
    │   ├── current.json           # Working active draft or current finalized script
    │   ├── conversation.json      # Complete chronological array of ConversationEvents
    │   ├── workflow_state.json    # Persistent acceptance/rejection status for active draft
    │   ├── script.json            # Mirrored copy of latest finalized script (for pipeline stages)
    │   ├── versions/
    │   │   ├── v001.json          # Immutable historical draft revision 1
    │   │   ├── v002.json          # Immutable historical draft revision 2
    │   │   └── ...
    │   └── finalized/
    │       ├── v001.json          # Immutable finalized script artifact v1
    │       └── ...
    ├── scene_planning/
    ├── script_to_audio/
    ├── video/
    └── runs/
```

### Persistence & Crash-Recovery Guarantees
- **Atomic File Writes:** All JSON updates (`current.json`, `conversation.json`, `workflow_state.json`, and version files) use atomic writes via temporary files and `os.replace`. An interrupted write never leaves a corrupt or truncated file.
- **Two-Phase Finalization Commit Marker:** Multi-file finalization (`finalized/v*.json`, `script.json`, `current.json`, `conversation.json`, `metadata.json`) is coordinated by a transaction intent record (`script/.finalizing.json`). If execution crashes at any transaction boundary, reloading the service automatically and idempotently reconciles all remaining steps, prevents duplicate event emission, and clears the marker.
- **Finalized Artifact Immutability:** On-disk finalized files (`finalized/v*.json`) serve as ground truth and are never overwritten, even during recovery from divergent marker payloads.
- **Canonical Role Normalization:** Section roles (`type`) accept case-insensitive inputs (e.g., `HOOK`, `TakeAway`) and are validated and normalized to canonical lowercase form (`hook`, `takeaway`) at the Pydantic model boundary.
- **Path Traversal Protection:** Project resolution enforces path containment within `projects_root`. Malicious project IDs (e.g. `../../etc`) are rejected immediately with `ProjectSecurityError`.

---

## 4. Lifecycle & State Machine

```
              ┌────────────────────────┐
              │ start_script(topic)    │
              └───────────┬────────────┘
                          │ (version=1, status=draft)
                          ▼
            ┌─────────────────────────────┐
            │       Active Draft          │◄───────────────────────┐
            └──────┬───────────────┬──────┘                        │
                   │               │                               │
  accept_draft()   │               │ reject_draft(reason)          │ revise_script(instruction)
                   ▼               ▼                               │ (version=N+1, status=draft)
            ┌──────────────┐┌──────────────┐                       │
            │   Accepted   ││   Rejected   │───────────────────────┘
            └──────┬───────┘└──────────────┘
                   │
  finalize_script()│ (strict validation, saves finalized/v{:03d}.json,
                   │  mirrors script.json, marks stage completed)
                   ▼
            ┌──────────────┐
            │  Finalized   │───────────────────────────────────────┐
            │(status=final)│                                       │
            └──────────────┘                                       │
                   ▲                                               │ revise_script(instruction)
                   │                                               ▼
                   │                                 ┌─────────────────────────────┐
                   └─────────────────────────────────┤       New Draft v(N+1)      │
                                                     └─────────────────────────────┘
```

### State Transition Rules
1. **Initial Draft:** `start_script` creates v1 with `status="draft"`. Acceptance state is unaccepted.
2. **Revision:** `revise_script` creates version $N+1$ with `status="draft"`. Acceptance state is reset to unaccepted. The previous version remains preserved in `versions/v{:03d}.json`.
3. **Acceptance:** `accept_draft` marks the active draft as accepted. Status remains `draft`—acceptance alone does **not** finalize the script.
4. **Rejection:** `reject_draft` marks the draft as rejected with an optional reason. The draft remains viewable in `current.json`, but **cannot** be finalized.
5. **Finalization:** `finalize_script` requires that the active draft has been explicitly accepted and is not rejected. It validates narrative content, writes `finalized/v{:03d}.json`, mirrors the file to `script/script.json`, updates `current.json`, and marks the project metadata stage `script` as `completed`.
6. **Revising a Finalized Script:** Calling `revise_script` on a project with a finalized script creates a new draft ($N+1$). The previous finalized file in `finalized/` is immutable and untouched. Downstream stages continue reading `script/script.json` until a new version is explicitly finalized.

---

## 5. LLM Provider Architecture & Configuration

The LLM abstraction resides in `backend/app/llm/`:
- `base.py`: Defines `LLMProvider` abstract base class with `generate_json`, `generate_script`, and `revise_script`.
- `mock.py`: `MockLLMProvider` provides deterministic, offline responses for testing with zero network access.
- `ollama.py`: `OllamaLLMProvider` provides a production-grade local adapter connecting to an Ollama server.

### Configuration Settings
Configurable via environment variables (prefixed with `ANIM_`) or `.env`:

| Setting | Env Variable | Default | Purpose |
|---|---|---|---|
| `LLM_SCRIPT_PROVIDER` | `ANIM_LLM_SCRIPT_PROVIDER` | `"mock"` | Provider selector (`mock` or `ollama`) |
| `OLLAMA_HOST` | `ANIM_OLLAMA_HOST` | `"http://localhost:11434"` | Endpoint URL for Ollama |
| `OLLAMA_MODEL` | `ANIM_OLLAMA_MODEL` | `"qwen2.5:7b"` | Ollama model name |
| `OLLAMA_TIMEOUT_SECONDS` | `ANIM_OLLAMA_TIMEOUT_SECONDS` | `60.0` | HTTP request timeout in seconds |

### Manual Testing with Ollama
1. Install and run Ollama:
   ```bash
   ollama serve
   ```
2. Pull the recommended model:
   ```bash
   ollama pull qwen2.5:7b
   ```
3. Set the environment variable or create `.env`:
   ```bash
   ANIM_LLM_SCRIPT_PROVIDER=ollama
   ANIM_OLLAMA_MODEL=qwen2.5:7b
   ```
4. Run CLI commands to generate scripts with the local LLM.

---

## 6. CLI Usage & Pipeline Integration

The CLI exposes conversational and lifecycle commands under `--script --project <id>`:

### CLI Commands
```bash
# 1. Start a script draft from a topic
python -m backend.app.cli.main --script --project proj_123456 --topic "Why the sky is blue" --target-duration 5.0

# 2. Revise the active draft with a conversational instruction
python -m backend.app.cli.main --script --project proj_123456 --instruction "Make the hook punchier and emphasize Rayleigh scattering"

# 3. View the current script draft
python -m backend.app.cli.main --script --project proj_123456 --show-script

# 4. View conversation and revision history
python -m backend.app.cli.main --script --project proj_123456 --history

# 5. Reject a draft with an optional reason
python -m backend.app.cli.main --script --project proj_123456 --reject --reason "The middle section is too technical"

# 6. Accept the draft
python -m backend.app.cli.main --script --project proj_123456 --accept

# 7. Finalize the accepted draft
python -m backend.app.cli.main --script --project proj_123456 --finalize
```

---

## 7. Verification & Test Suite

The test suite runs 100% deterministically and offline:

```bash
# Run all backend tests
backend\.venv\Scripts\python -m pytest backend/tests -v

# Run with test coverage
backend\.venv\Scripts\python -m pytest backend/tests --cov=backend.app -v

# Run architectural boundary AST check
backend\.venv\Scripts\python -m pytest backend/tests/test_import_rules.py -v
```

### Test Summary
- Total tests: **61 passed**
- Overall test coverage: **82%**
- Architectural boundary checks: **0 violations**
