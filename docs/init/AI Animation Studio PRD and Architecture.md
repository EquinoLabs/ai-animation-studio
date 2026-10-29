# AI Animation Studio: PRD and Architecture

Oct 29, 2026

## 1. Overview

A standalone product in its own repository, `ai-animation-studio` (split from `ai-video-clipper` in October 2026). The user describes a topic in a chat UI, the AI co-writes a structured script, the user approves it, and the system produces a finished explainer video: an original 2D anime-style cutout character with gestures, expressions and lip sync, AI voiceover, scenes with backgrounds and props, captions, and music. All content is original, so there is no third-party footage and no reused-content risk. Reference style: calm, motivating, simple-explanation channels (similar to Psych2Go). Characters and art must be original, not copies of any existing channel's designs.

Target: 5 to 10 minute videos (ideal), 30 minutes maximum. Personal project for now, not shipped.

## 2. Goals and non-goals

**Goals**

- Chat-driven script creation with a hard Finalize gate: nothing downstream runs until the script is approved.
- Script structure rules enforced: hook, setup, middle, climax, takeaway. Plain, non-exaggerated tone.
- Scene planner that reports which assets are reusable from the asset pack and which are missing.
- A rigged 2D cutout character (PNG body parts, poses, expressions, face overlays, lip sync) drawn with Skia and piped as raw frames to ffmpeg, with one GPU-heavy stage at a time on the 6 GB card.
- Per-scene rendering so one scene can be re-rendered without redoing the video.
- Independence: own repo, dependencies, tests and roadmap, with the clipper reachable only by passing finished video files.

**Non-goals for v1**

- AI-generated video or images, consistent AI characters, realistic faces or real people.
- Multi-user accounts, billing, public deployment.
- Walk cycles and complex object interaction (preset actions only).

## 3. User flow

1. **Brief:** user chats about topic, audience, length, tone.
2. **Outline:** AI proposes an outline (hook, sections, climax). User edits and approves.
3. **Script:** AI writes section by section. User edits in chat or directly. **Finalize Script** locks it (versioned, immutable after lock).
4. **Scene plan:** AI splits the script into scenes and beats and picks layout, characters, poses, expressions, actions, backgrounds, props from the asset manifest.
5. **Coverage report:** per scene: reuse, substitute, or missing, plus a ranked list of missing assets and which scenes need them. User fixes gaps by adding assets, accepting substitutes, or asking for a scene rewrite.
6. **Voiceover:** TTS per beat, durations measured.
7. **Render:** per-scene animation, joined with ffmpeg, captions from TTS word timings, background music, final encode.
8. **Review:** play the video, change a line or pose, re-render only that scene.

## 4. Functional requirements

| ID | Requirement |
| --- | --- |
| FR-1 | Script chat: multi-turn session, stored history, structured system prompt with story-structure rules. |
| FR-2 | Long scripts are generated outline-first, then per section, with a running summary passed forward to keep consistency. |
| FR-3 | Finalize gate: scene planning refuses to start on a non-finalized script. |
| FR-4 | Asset manifest (JSON): id, type (background, prop, character, pose, expression, action), tags, description, file path. Validated on load. |
| FR-5 | Scene plan JSON: beats with narration, layout, characters, pose, expression, action, background, props, camera. Every referenced id must exist in the manifest (validated in code, retry on failure). |
| FR-6 | Coverage report: reuse / substitute / missing per scene, with missing-asset specs saved to a backlog file. |
| FR-7 | TTS adapter interface (local Kokoro or Piper first, paid voice swappable). Synthesis per beat, with pauses joined by ffmpeg. |
| FR-8 | Character system: cutout PNG parts with a joint origin and optional scale, pose = joint rotations blended with smoothstep, expression = swappable face overlays (eyes, mouth), blink scheduler, continuous lip sync from the loudness of the voice track only. |
| FR-9 | Renderer: scene JSON plus audio in, scene MP4 out. Skia draws frames, raw RGBA goes to ffmpeg over a pipe (no image files), identical frames are skipped, the background is cached, NVENC encodes with a libx264 fallback. Scenes cached by content hash. |
| FR-10 | Captions generated from TTS word timings and written as ASS subtitles by a small writer in this repo (copied from the clipper only if it saves time). |
| FR-11 | Resumable runs: every stage writes artifacts and a run manifest, rerun skips completed stages. |
| FR-12 | LLM provider chosen per step (local Ollama for cheap structured work, Claude for script quality), with fallback. |

## 5. Data contracts (the interfaces between parts)

These JSON schemas are the contracts that let separate people or agents work in parallel. Changing one needs agreement from both sides.

- `script.json`: title, structure sections, narration text per section, status (draft or final), version.
- `asset_manifest.json`: list of assets as in FR-4.
- `scene_plan.json`: ordered scenes, each with ordered beats (see FR-5).
- `coverage_report.json`: per scene status, missing-asset specs.
- `animation_run_manifest.json`: stage statuses, artifact paths, durations, hashes.

Example beat:

```json
{
  "id": "s03_b02",
  "narration": "Most people never notice this habit.",
  "scene": {
    "layout": "two_shot",
    "background": "bg_office_01",
    "characters": [
      {"id": "narrator", "pose": "point", "expression": "curious"},
      {"id": "bystander_1", "pose": "idle", "expression": "neutral"}
    ],
    "props": ["prop_laptop_01"],
    "action": "none",
    "camera": "zoom_in"
  }
}
```

## 6. What the clipper repo offered (reference for the new repo)

Findings from reading the clipper repo (`backend/`, a modular monolith with FastAPI, a worker and a React frontend under `frontend/src/features/*`) before the split. Animation is now its own repository, so this section is a copy-or-rewrite checklist, not a dependency list. The exact file list, line counts and import map come from the clipper team's read-only assessment (pending).

**Directly reusable by the animation feature**

- `core/` (config, logging, security) and the standard error envelope in `api/v1/schemas/common.py`.
- `services/media/` (ffmpeg runner, probing, extraction, ASS/SRT subtitle generation). Subtitles take transcript segments with word timings, so TTS timings can be converted into that format.
- `services/storage/` (output manager, staging).
- `services/llm/` factory and Ollama and OpenAI adapters, with caveats below.
- `main.py` app factory, request-ID middleware, CORS.

**Clipper-specific, animation must not depend on these**

- `services/candidates/`, `services/pipeline/` (orchestrator, long and short pipelines), `services/framing/`, `services/rendering/` (long and short clip renderers), `api/v1/routes/review.py`, `domain/candidates`, `domain/manifest`, clipper CLI files.

**Things to know before sharing code**

1. `LLMProvider` has one abstract method, `discover_and_evaluate`, shaped for clip discovery, and `llm/base.py` imports from `api/v1/schemas/candidates`. Do not add abstract methods to it (it would break existing adapters and tests). Add a separate generic structured-output interface instead.
2. `core/config.py` is one flat `Settings` class. Do not add animation fields to it. Give animation its own settings class.
3. The review router serves anything under `outputs/`, relative to the working directory. Animation runs must not write there, or the clipper's run listing may pick them up.
4. `db/models.py` and `workers/worker.py` are currently documentation and a stub, so the pipeline is file-based and run through CLI and orchestrator. Animation should follow the same file-based, manifest-driven style for now.
5. `requirements.txt` has no TTS, Playwright or image libraries yet. Per `RULES.md`, install only into `backend/.venv` and keep requirements files in sync.

**Copy or rewrite for the new repo**

- Copy as a starting point: the Ollama and Claude provider calls, a generic structured-JSON helper (`common/llm/structured.py` in the clipper), the settings pattern, the error envelope, the ffmpeg runner with argument arrays and timeouts, ASS subtitle generation.
- Rewrite fresh: anything touching candidates, the clipper pipeline, framing, review routes or the clipper's output folders.
- Leave behind: heavy transcription and model dependencies, unless a later animation feature needs them (narration timings come from TTS).

## 7. Proposed architecture (standalone repository)

### 7.1 Repository layout

```
ai-animation-studio/
  backend/
    app/
      main.py                app factory, CORS, request-ID middleware
      config.py              settings (env prefix ANIM_)
      domain/                script, scene plan, asset manifest, run manifest models
      llm/                   provider factory (Ollama, Claude), generate_json(prompt, schema)
      services/
        scriptwriter/        chat session, outline, section writing, finalize gate
        planner/             scene planning, asset coverage report
        assets/              manifest loading, validation, tag search
        tts/                 TTS interface + adapters, per-beat synthesis, durations
        characters/          loader, cutout parts, poses, face overlays, lip sync
        renderer/            Skia scene renderer, raw-pipe encoder, scene cache
        pipeline/            orchestrator, resumable run manifest
      api/                   routes, schemas
      cli/                   run_animation.py
    tests/
    requirements.txt, requirements-dev.txt
  frontend/                  React + Vite + TypeScript
  assets/
    characters/narrator/     parts/*.png, character JSON, poses
    backgrounds/, props/, music/, manifest.json
  tools/                     cutout pipeline (background removal, cut, JSON build), ESRGAN helper
  docs/                      PRD, TechSpec, character spec
  data/                      run artifacts (git-ignored)
```

### 7.2 Origin of the code

The renderer, character loader, lip sync and cutout pack come from the spike folders (`Figure-Build`, `Ai-Animator`) and move into the new repo in M0. The clipper repo stays as it is and is kept only as a reference; this project moves nothing out of it.

### 7.3 Dependency rules

- `services/*` may import `domain`, `config` and `llm`; they never import `api`.
- `api` and `cli` call `services/pipeline` and nothing deeper.
- `renderer` and `characters` never import `llm` or `scriptwriter`, so rendering works with no AI running.
- No import of the clipper repo, now or later. Integration is file-based: export an MP4 and upload it to the clipper.
- Enforce the rules with a small import-scan test (or import-linter) so a violation fails the test run.

### 7.4 Frontend

One React + Vite + TypeScript app in `frontend/`, with an API client generated from the backend's OpenAPI schema. Screens: script chat, scene plan and coverage view, asset manager, render review. It starts with the script chat (M4); the earlier milestones need no UI.

### 7.5 Working lanes

| Lane | Owns (only edits here) | Talks to others through |
| --- | --- | --- |
| Renderer and characters | `services/renderer`, `services/characters`, `assets/characters`, `tools/` | the character JSON and poses contracts (section 5) |
| AI and script | `services/scriptwriter`, `services/planner`, `llm/`, `domain/` | scene plan and asset manifest JSON |
| Voice and captions | `services/tts` | per-beat audio files with word timings |
| API and frontend | `api/`, `frontend/` | the OpenAPI schema |

Working rules: one branch per lane, a root `CLAUDE.md` that tells each coding agent which folders it may edit, contract changes in their own small commit, and stub implementations behind each interface (a fake TTS that emits silence of the right length, a fake LLM that returns a canned scene plan) so lanes can build without waiting on each other.

## 8. Non-functional requirements

- Hardware: 6 GB VRAM laptop GPU, 16 GB RAM. GPU stages run one at a time and unload models between stages (for example Ollama `keep_alive: 0`).
- Rendering draws on the CPU with Skia, per scene, at 1080x1920 and 24 fps. Frames go to ffmpeg as raw RGBA over a pipe; `h264_nvenc` encodes when available, with a startup check and a `libx264` fallback.
- Deterministic and resumable: same inputs give same scene hash, finished scenes are skipped.
- All LLM output is validated against schemas, with bounded retries and a local fallback model.
- Subprocess calls use argument arrays and timeouts. Character image paths are checked so a character file cannot read files outside its own folder.

## 9. Milestones

| # | Milestone | Done when |
| --- | --- | --- |
| M0 | Repo bootstrap: new `ai-animation-studio` repo, spike code (loader, Skia renderer, lip sync, cutout pack) moved in, requirements, tests, import-rule test, root `CLAUDE.md` | Fresh clone installs, tests pass, the cutout test video renders |
| M1 | Character v1: 2x cutout pack, speed work (pre-scaled parts, layer cache), face overlays (eyes, mouth, blink), continuous lip sync on the cutout | 20 s test video is sharp at 100%; face-only frames take 30 ms or less and pose transitions 150 ms or less |
| M2 | Voice and captions: chosen TTS voice, per-beat synthesis, word timings to ASS captions, lip sync checked on the real voice | A hand-written 30 s script becomes a watchable MP4 from the CLI |
| M3 | Scene plan and coverage report from a short script, validated against the manifest | Invalid asset ids are rejected and retried; coverage JSON generated |
| M4 | Script chatbot with outline-first flow and Finalize gate (API plus UI) | Planner refuses a non-final script |
| M5 | Second and third characters, props, 20 backgrounds, camera moves, per-scene re-render | A 5-minute video end to end |
| M6 | Review UI, music mixing, polish, 10-minute run | Re-render one scene from the UI |

## 10. Risks and open questions

- **Asset volume** is the long-term bottleneck. Mitigate with the coverage report, icon packs with permissive licenses, and scripts steered toward what the library supports.
- **Visual monotony** over 5 to 10 minutes. Needs scene variety, camera moves, text callouts, cutaway diagrams.
- **Script quality** from a 7B local model. Use Claude for scripting and review factual claims before finalizing, especially for psychology or health topics.
- **Render time**: about 14,400 frames for 10 minutes at 24 fps. Skipping identical frames helped (89% skipped in the 20 s cutout test), but a drawn image frame cost 282 ms before the speed work; M1 targets 30 ms for face-only frames.
- **Art resolution and seams**: the 572x1024 source was soft at video size (fixed with a 2x upscale), and the paper-doll cut shows flat seams at shoulders and elbows in rotated poses. Keep arm rotations moderate or redraw joint caps later.
- **Character art in git**: PNG parts, 4x upscales and test videos bloat a repo. Keep only the 2x parts in git and ignore the rest.
- **Voice licensing** if the videos are monetized. Check each TTS model's license.
- **Copy drift**: code copied from the clipper (LLM client, ffmpeg runner) will not get later clipper fixes. Acceptable while the copy is small; extract a package only if a shared module grows.
- Open: which TTS voice to use, and whether lip sync needs retuning on it.
- Open: where the asset pack lives long term (in the repo or separate storage).

## 11. Complexity summary

| Area | Difficulty |
| --- | --- |
| Script chatbot and Finalize gate | Easy to medium |
| Long-script generation (outline-first) | Medium |
| Scene planner and coverage report | Medium |
| TTS per beat with timing | Easy to medium |
| Character system | Medium (prototype done, speed and faces left) |
| Asset library growth | Medium to hard (ongoing) |
| Renderer and per-scene cache | Medium |
| Captions, music, final mix | Easy (mostly reuse) |
| Review and re-render UI | Medium |

## 12. Repository decision

Animation is a separate repository from the clipper. Observed: the working animation code (renderer, loader, lip sync, cutout pack) lives in local spike folders and not in the clipper repo, so there is almost nothing to extract; the two products need different heavy dependencies (Skia, Real-ESRGAN and NVENC here, transcription and clip ranking there). Assumed until the clipper team's report confirms it: the shared code is small enough to copy. The only link between the products is a finished video file exported from animation and uploaded to the clipper.

## 13. Spike results and decisions

| Topic | Result | Decision |
| --- | --- | --- |
| Renderer | Skia and Playwright drew the same figure | Skia, which is lighter on resources |
| Frame output | Writing PNG files took about 95% of the time in the first spike (about 0.34 s per frame) | Raw RGBA frames piped to ffmpeg, cached background, skip identical frames |
| Encoder | NVENC and libx264 both produced video | NVENC (CQ 19) with a libx264 fallback, checked at startup |
| Lip sync | Mouth stayed a circle, then closed too slowly | Per-file loudness normalization (10th to 95th percentile), 12 openness levels, attack and release smoothing, pause rule, lead frames; read the voice track only. User confirmed it looks better |
| Character | Vector starter replaced by the user's anime design, red flannel outfit only | 8 cutout PNG parts with joint origins; flat paper-doll seams accepted for v1 |
| Image sharpness | 572x1024 source was soft at about 1.55x | Real-ESRGAN anime model 4x, Lanczos to 2x (1144x2048); edge gradient up 22% on the head and 17% on the plaid; user checked the crops; image shapes carry `scale: 0.5` so design units stay unchanged |
| Image-character speed | 282 ms per drawn frame at 1080x1920; 20 s test: 428 of 480 frames skipped, 19.2 s total, 2.1 GB peak RAM | In progress: pre-scaled parts and a layer cache, targets 30 ms (face-only) and 150 ms (transitions) |
