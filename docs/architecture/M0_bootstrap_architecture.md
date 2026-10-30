# Milestone 0 (M0): Repository Bootstrap & Foundation Architecture

Date: 2026-10-29  
Status: Implemented & Verified  

## 1. Overview
This document records the foundational repository layout, architectural boundaries, dependency rules, and verification standards established for `ai-animation-studio`.

## 2. Package Boundaries & Roles
Per TechSpec Section 3, the codebase is partitioned into distinct, decoupled subsystems:

- `backend/app/domain/`: Pure data contracts (`Script`, `AssetManifest`, `ScenePlan`, `CoverageReport`, `AnimationRunManifest`) implemented with Pydantic. Validates data passing between pipeline stages with zero dependency on business logic.
- `backend/app/config.py`: Centralized configuration via `pydantic-settings`, reading environment variables with the `ANIM_` prefix.
- `backend/app/llm/`: Isolated LLM interface boundary (`LLMProvider`, `generate_json`).
- `backend/app/services/`: Core application services:
  - `scriptwriter/`: Multi-turn conversational script engine and outline management.
  - `planner/`: Scene planning and asset coverage engine.
  - `assets/`: Asset loading and manifest validation.
  - `tts/`: Audio synthesis interface and adapters.
  - `characters/`: Character loading, rig hierarchy, and pose management.
  - `renderer/`: Skia frame renderer and FFmpeg stream piping.
  - `pipeline/`: Resumable multi-stage orchestrator.
- `backend/app/api/`: FastAPI routes and request handling. Only interacts with `services/pipeline`.
- `backend/app/cli/`: Command-line interface (`run_animation.py`). Only interacts with `services/pipeline`.
- `frontend/`: React 19 + TypeScript + Vite web client.
- `assets/`: 2x character packs, backgrounds, props, music, and `manifest.json`.
- `tools/`: Image extraction, upscaling, background removal, and polygon cutting scripts.
- `data/`: Ephemeral runtime artifacts and manifests (`data/<run_id>/`).

## 3. Strict Dependency Boundaries
An automated AST-based scanner (`backend/tests/test_import_rules.py`) enforces the following structural rules on every test run:
1. **Services Independence from Presentation:** `services/*` must never import from `api` or `cli`.
2. **Layer Entry Control:** `api` and `cli` may only import `services.pipeline` and cannot bypass it to call deeper service internals.
3. **AI/Renderer Decoupling:** `renderer` and `characters` must never import `llm`, `scriptwriter`, or `planner`. Video rendering and character posing can execute in complete isolation from AI models.
4. **Zero Clipper Coupling:** No backend module may import or depend on `ai-video-clipper` or `clipper`. Integration is purely file-based.

## 4. Video Encoder Selection & Fallback Design
`backend/app/services/renderer/encoder.py` implements deterministic hardware acceleration with software fallback:
1. **Listing Check:** Confirms that FFmpeg lists `h264_nvenc` and `libx264`.
2. **Probe Encode:** Executes a minimal 0.1-second probe encode to test if the NVIDIA GPU driver and hardware encoder are actually operational (avoiding false positives where the binary lists NVENC but no supported GPU exists).
3. **Execution Fallback:** In `auto` mode, NVENC is preferred. If NVENC fails during a render, the system automatically falls back to `libx264`.
4. **Subprocess Isolation:** All subprocess executions use argument arrays (no shell string execution), bounded timeouts, and captured stderr diagnostics.
5. **Hardware Independence in Tests:** Unit tests isolate subprocess execution via dependency injection (`MockSubprocessRunner`), verifying all selection and fallback branches without requiring GPU hardware.

## 5. Scope & Future Work
M0 establishes the skeleton, guardrails, and test harness. Subsequent milestones will implement:
- **M1:** Character v1 with 2x pre-scaled parts, face overlays, and continuous lip sync.
- **M2:** Voiceover TTS integration and word-level ASS caption generation.
- **M3:** Scene planning and manifest coverage validation.
- **M4:** Script chat UI and hard Finalize gate.
- **M5:** Multi-character support, backgrounds, and full 5-minute video assembly.
- **M6:** Web review UI and per-scene selective re-rendering.
