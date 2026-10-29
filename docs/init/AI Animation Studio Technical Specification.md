# AI Animation Studio: Technical Specification

Oct 29, 2026 · @Equinox

This spec defines how `ai-animation-studio` turns a finalized script into a rendered MP4 through a resumable pipeline, with a cutout character drawn by Skia; measured results are marked as measured and everything else is a plan.

## 1. Scope and status

This spec covers the backend pipeline, the character format, the renderer and the contracts between them. Product goals and requirement IDs (FR-1 to FR-12) live in the PRD, and the frontend is described in PRD section 7.4.

| Area | Status | Evidence |
| --- | --- | --- |
| Skia renderer, raw frame pipe, frame skipping, NVENC with fallback | Measured in the spike | 20 s test: 428 of 480 frames skipped, 19.2 s total, 2.1 GB peak RAM |
| Lip sync (continuous openness, pause rule) | Tuned on one sample voice | User confirmed it looks right; recheck on the chosen TTS voice |
| Cutout character at 2x resolution | Built, reviewed | Skia and Pillow renders differ by 0.39/255 on average |
| Image-character speed (pre-scaled parts, layer cache) | In progress | 282 ms per drawn frame before the work; targets in section 11 |
| Face overlays (eyes, mouth, blink) | Planned next | Face positions measured on the 572x1024 source |
| TTS, script chat, scene planner, assets, API, frontend | Planned | PRD milestones M2 to M6 |

## 2. Pipeline

A run is eight stages in a straight line, and each stage writes its artifacts and a status to the run manifest, so a rerun skips finished stages. Only one GPU-heavy stage runs at a time.

| # | Stage | Input | Output | Compute |
| --- | --- | --- | --- | --- |
| 1 | Brief and outline | user chat | outline | LLM (Claude preferred) |
| 2 | Script and finalize | outline | `script.json`, locked and versioned | LLM (Claude preferred) |
| 3 | Scene plan | final script, `asset_manifest.json` | `scene_plan.json` | LLM (local Ollama or Claude) |
| 4 | Coverage report | scene plan, manifest | `coverage_report.json`, missing-asset backlog | CPU, no LLM |
| 5 | Voiceover | beat narration | one audio file per beat, durations, word timings | TTS (GPU or CPU, one model loaded) |
| 6 | Scene render | scene plan, beat audio, character packs | one silent or voiced MP4 per scene | CPU draws, NVENC encodes |
| 7 | Captions and mix | word timings, music | ASS captions, mixed audio | CPU, ffmpeg |
| 8 | Final join | scene MP4s, captions, audio | final MP4 | ffmpeg, NVENC |

Stage 3 refuses to start unless `script.json` has status `final`. Stage 6 caches each scene by a content hash of its plan, audio and character pack version, so changing one line re-renders one scene.

## 3. Repository layout and dependency rules

The repo is one Python backend, one React frontend, a character asset folder and a tools folder, with the layout below (PRD section 7.1 has the same tree).

```
ai-animation-studio/
  backend/app/
    main.py  config.py  domain/  llm/
    services/ scriptwriter/ planner/ assets/ tts/ characters/ renderer/ pipeline/
    api/  cli/
  backend/tests/
  frontend/
  assets/ characters/narrator/  backgrounds/  props/  music/  manifest.json
  tools/     cutout pipeline, ESRGAN helper
  docs/      PRD, TechSpec, character spec
  data/      run artifacts (git-ignored)
```

Dependency rules, enforced by an import-scan test:

- `services/*` import `domain`, `config` and `llm`, never `api`.
- `api` and `cli` call `services/pipeline` and nothing deeper.
- `renderer` and `characters` never import `llm` or `scriptwriter`, so a render needs no AI process running.
- Nothing imports the clipper repo. The only link is a finished MP4 uploaded by the user.
- Runtime data goes under `data/`, never into `assets/`, and `data/`, `*.mp4`, `*.wav`, `.venv/` and the 4x upscaled images are git-ignored.

Python packages install only into `backend/.venv`, and `requirements.txt` and `requirements-dev.txt` stay in sync with it.

## 4. Data contracts

Five JSON files carry data between stages, and each is validated on load with Pydantic models in `domain/`. A change to a contract goes in its own small commit.

| File | Holds | Rules |
| --- | --- | --- |
| `script.json` | title, structure sections (hook, setup, middle, climax, takeaway), narration per section, `status` (`draft` or `final`), `version` | Immutable once `final`; edits create a new version |
| `asset_manifest.json` | assets with `id`, `type` (background, prop, character, pose, expression, action), `tags`, `description`, file path | Paths must exist; ids unique |
| `scene_plan.json` | ordered scenes, each with ordered beats | Every referenced id must exist in the manifest; invalid plans are retried with the error text |
| `coverage_report.json` | per scene: reuse, substitute or missing; missing-asset specs | Written by code, not by the LLM |
| `animation_run_manifest.json` | stage statuses, artifact paths, durations, hashes | Written after every stage so reruns can skip |

One beat in `scene_plan.json`:

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

Pose and expression names must exist in the character's `poses.json` and character file (section 5). The first character pack has four poses (`idle`, `point`, `shrug`, `explain`) and the expressions `neutral`, `happy`, `surprised` and `serious`; the example's `curious` needs to be added before it is valid.

## 5. Character format and rig

A character is a tree of PNG parts joined at pivots, a pose file of joint rotations, and (next) face overlays; the narrator pack is 8 parts cut from the user's anime design in a red flannel outfit.

Conventions:

- The character is about 965 design units tall (the 572x1024 source pixels); design units are scaled to the frame at render time (about 1.55x for 1080x1920).
- The origin of every part is its joint. +y is down. Angles are degrees, positive clockwise on screen. `l` and `r` are screen-left and screen-right.
- Rotation 0 is the rest pose, with arms and legs hanging down. Front view only.
- `attach` is the child's pivot minus the parent's pivot, in design units, and `z` is draw order (higher in front).

| Part | Parent | `attach` | `z` |
| --- | --- | --- | --- |
| `torso` | root | \[0, 0\] | 20 |
| `head` | torso | \[1, -365\] | 40 |
| `upper_arm_l`, `upper_arm_r` | torso | \[-119, -310\], \[119, -310\] | 30 |
| `forearm_l`, `forearm_r` | its upper arm | \[-13, 148\], \[13, 148\] | 31 |
| `leg_l`, `leg_r` | torso | \[-55, 0\], \[55, 0\] | 10 |

An image part in the 2x pack (PNG pixels are twice the design size, so `scale` is 0.5 and `origin` stays in PNG pixels):

```json
{ "type": "image", "file": "parts/head.png", "origin": [210, 430], "scale": 0.5 }
```

**Poses** (`poses.json`) list joint rotations relative to rest, plus a root offset. The pack has `idle`, `point`, `shrug`, `explain` and `tilt`. Transitions blend between two poses over 0.5 s with smoothstep, and the scene planner picks poses by name.

**Face overlays** (planned, M1) are drawn on the head because the face is baked into the art. Positions were measured on the source image and are head-local design units (source pixel minus the head pivot at 288, 245):

| Element | Center | Size | Variants |
| --- | --- | --- | --- |
| Left eye | (-30.5, -95) | about 28 x 15 | open, closed, happy, wide |
| Right eye | (24.5, -95) | about 30 x 15 | open, closed, happy, wide |
| Mouth | (-1, -50.5) | about 31 wide | closed, small, mid, wide, o, smile |

Overlays are filled with the measured skin colors (`#EDC0A6` around the eyes, `#EBBCA2` around the mouth) before the variant is drawn. Eyebrows stay as drawn in the art in v1, and a blink scheduler closes the eyes at random intervals.

**Art pipeline** (`tools/`):

1. Upscale the source 4x with Real-ESRGAN (`realesrgan-x4plus-anime`), then Lanczos down to 2x (1144 x 2048).
2. Remove the background by flood fill from seed points, erode the edge, then soften it with a Gaussian.
3. Cut parts with polygons, remove the right hand (it is hooked in a pocket) and mirror the left forearm for the right one.
4. Build the character JSON from the pivots. Every tool takes a `SCALE` factor so the same recipe runs at 1x or 2x.

**Loader checks** (`characters/loader`): schema, parents without cycles, image paths that stay inside the character folder, readable PNGs, `origin` inside the image, `scale` numeric and above 0, poses that only name existing joints.

**Known limits**: hands are baked into the forearms, the cut is flat so shoulder and elbow seams show at large rotations, there are no knees or ankles, and the view is front only.

## 6. Renderer

The renderer draws each scene with Skia on the CPU at 1080x1920 and 24 fps and pipes raw frames straight into ffmpeg, which removed the disk and PNG cost that made the first spike slow.

Per scene, the frame loop does this:

1. Build the timeline from the scene plan: pose changes, expression changes, camera moves, and the beat audio with its durations.
2. For each frame, compute a state: pose blend, face variants (eyes, mouth), camera, character positions.
3. Hash the state. If it equals the previous frame's hash, resend the previous frame's bytes without drawing.
4. Otherwise draw: the cached background, the ground shadow, then the characters in `z` order with each part transformed by its joint chain.
5. Write the RGBA bytes to ffmpeg's stdin. ffmpeg encodes with `h264_nvenc` (CQ 19), or `libx264` if NVENC is missing, and muxes the beat audio.

Details that matter:

- Premultiplied alpha is used for part images, and sampling is cubic (Mitchell) today. The speed work replaces it with parts pre-scaled once to their final pixel size and a cheaper filter, and the change must keep the mean pixel difference small (check against the current output).
- A layer cache will hold the composite of everything below the face overlays, keyed by the full pose state (every joint angle rounded to 0.01 degree, hand or part variants). It is used only when nothing under the face is moving, never during a pose transition.
- NVENC availability is tested at startup with a one-second encode; the result picks the encoder for the whole run.
- Scene output is cached by a hash of the scene plan, beat audio, character pack version and renderer version.
- ffmpeg is called with argument arrays and timeouts, and its stderr is captured to the run log.

Measured (20 s cutout test, 1080x1920): 480 frames, 52 drawn, 428 skipped (89.2%), 282 ms per drawn frame, 19.18 s wall clock, 2,106 MB peak RAM, 1.11 MB file.

## 7. Lip sync

The mouth opening is a continuous value computed per video frame from the loudness of the voice track alone, normalized per file so it works for any voice and any recording level.

The controller (`LipsyncController`) works in this order:

1. Measure loudness per video frame from the voice audio only, never the mixed track with music.
2. Normalize per file: clip to the 10th and 95th percentile of that file's loudness and rescale to 0..1.
3. Smooth the raw value with a fast attack and a slower release. Smoothing happens once, in the controller, and not also in the audio envelope.
4. Apply the pause rule: when loudness falls below the pause threshold, the mouth closes at the fast rate instead of following the release curve, so a short gap between words closes the mouth.
5. Quantize to 12 openness levels and map them to mouth variants or an opening height.
6. Shift the result earlier by `lead_frames`, so the mouth leads the sound slightly.
7. Hold an opening for `min_hold_frames` to stop flicker. The hold applies only when opening, never when closing.
8. After a silence timeout, switch to the expression's `mouth_rest`.

Why it is built this way (measured in the spike):

- Fixed thresholds on raw loudness left the mouth stuck as a circle, because the median loudness (0.59) sat above the top threshold (0.5). Per-file percentile normalization fixed it.
- Smoothing the signal twice, plus a hold on closing, made the mouth close slowly after a word. One smoothing step, a pause rule and a hold on opening only fixed it, and the user confirmed it looks right on the sample voice.

The starter character file listed `attack_ms` 30, `release_ms` 80 and `min_hold_frames` 2. The tuned values live in the spike's controller and move into the character file's `lipsync` block in M0. Re-check them on the chosen TTS voice, and keep the debug CSV and the pause-check plot as a regression aid; `test_continuous_lipsync.py` covers the controller.

## 8. Voice, LLM and audio

**TTS.** A `TTSAdapter` interface takes text and returns a WAV file, its duration and word timings. The first adapter is a local model (Kokoro or Piper, to be chosen in M2), and a paid voice can be swapped in behind the same interface. Each beat is synthesized separately, pauses are added between beats, and the files are joined by ffmpeg. A fake adapter that returns silence of the right length lets the renderer and lip sync be built and tested before a voice is chosen.

**LLM.** `llm/` holds a provider factory for Ollama (`qwen2.5:7b` today) and Claude, and one function, `generate_json(prompt, schema, provider)`, that returns a validated object. Each pipeline step names its provider in settings.

| Step | Default provider | Why |
| --- | --- | --- |
| Outline and script | Claude | Script quality from a 7B local model is a known risk |
| Scene plan | Ollama, Claude on repeated failure | Structured output with schema checks is cheap |
| Fact review of the script | Claude | Psychology and health claims need care |

All LLM output is validated against the Pydantic schema. On a failure the call is retried a bounded number of times with the validation error in the prompt, then falls back to the other provider, then stops with a clear error. For long scripts the LLM writes an outline first, then one section at a time with a running summary passed forward.

**GPU use.** Ollama is called with `keep_alive: 0` so the model unloads after each step, and TTS loads its model only for the voiceover stage. This keeps the 6 GB card to one model at a time.

**Audio.** Beat audio is joined and mixed with background music at a lower level by ffmpeg. Lip sync reads the voice track before mixing (section 7). Captions come from the word timings and are written as ASS subtitles; each TTS model's license is checked before any video is published.

## 9. API, CLI and configuration

The CLI runs the whole pipeline first, and the API wraps the same orchestrator later (M4), so the API adds no logic of its own. Names below are proposals.

**CLI**

```
python -m app.cli.run_animation --script data/my_video/script.json --from-stage scene_plan --resume
```

`--resume` skips stages that the run manifest marks complete, and `--from-stage` forces a rerun from that stage on.

**API** (FastAPI, JSON, one standard error envelope with `code` and `message`):

| Method and path | Purpose |
| --- | --- |
| `POST /api/v1/scripts` | Start a script chat session |
| `POST /api/v1/scripts/{id}/messages` | Send a message, get the AI reply and the updated draft |
| `POST /api/v1/scripts/{id}/finalize` | Lock the script (status `final`, new version on later edits) |
| `POST /api/v1/runs` | Start a run from a final script |
| `GET /api/v1/runs/{id}` | Run manifest with stage statuses |
| `GET /api/v1/runs/{id}/coverage` | Coverage report and missing-asset backlog |
| `POST /api/v1/runs/{id}/scenes/{scene_id}/rerender` | Re-render one scene and rejoin |
| `GET /api/v1/assets` | Asset manifest, with filters by type and tag |

Runs execute in a background worker process and the state lives in files under `data/<run_id>/` (the clipper's file-based style); a database is added only when something needs queries across runs.

**Configuration** is one settings class read from environment variables with the prefix `ANIM_` and a `.env` file:

| Variable | Meaning | Default |
| --- | --- | --- |
| `ANIM_DATA_DIR` | Where run artifacts go | `data` |
| `ANIM_ASSETS_DIR` | Asset pack root | `assets` |
| `ANIM_FPS`, `ANIM_WIDTH`, `ANIM_HEIGHT` | Output format | 24, 1080, 1920 |
| `ANIM_ENCODER` | `auto`, `nvenc` or `x264` | `auto` |
| `ANIM_LLM_SCRIPT_PROVIDER`, `ANIM_LLM_PLAN_PROVIDER` | Provider per step | `claude`, `ollama` |
| `ANIM_OLLAMA_MODEL` | Local model | `qwen2.5:7b` |
| `ANIM_TTS_ADAPTER` | TTS adapter name | chosen in M2 |

## 10. Testing and quality gates

Tests run with `pytest` inside `backend/.venv`; the character and renderer tests need no LLM, no TTS and no GPU, and fake adapters stand in for the others.

| Test | Checks | Status |
| --- | --- | --- |
| Loader | Schema, parent cycles, image path jail, readable PNGs, `origin` bounds, `scale` above 0, pose joints | Exists in the spike (`test_loader.py`) |
| Skia renderer | Part transforms, image drawing, comparison with the Pillow reference (pass when mean difference stays under 1/255; measured 0.39 to 0.60) | Exists (`test_skia_character.py`, `test_cutout_renderer.py`) |
| Lip sync | Normalization, smoothing, pause rule, hold on opening only, rest mouth after silence | Exists (`test_continuous_lipsync.py`) |
| Cache equivalence | The same frame with the layer cache on and off is identical (max pixel difference 0) for 3 poses | Added with the speed work |
| Import rules | The dependency rules in section 3 | M0 |
| Contracts | Each JSON file validates; a plan with an unknown asset id is rejected and retried | M2 to M3 |
| Finalize gate | Scene planning refuses a script that is not `final` | M4 |
| Encoder fallback | With NVENC disabled, the run uses `libx264` and still produces a playable file | M0 |
| Render smoke | A 3 s scene with fake TTS renders end to end and ffprobe reports the expected size, fps and duration | M1 |

Quality gates before a milestone counts as done: all tests pass; the milestone's "done when" in the PRD is met by hand; and for visual work, the person who owns the art looks at 100% crops of the head and shirt, because gradient and pixel-difference numbers do not catch every artifact.

## 11. Performance and hardware budget

A 10-minute video is 14,400 frames at 24 fps, and the cost per drawn frame decides the render time: from about 7 minutes at the speed target to about 68 minutes at the measured cost, if every frame had to be drawn.

| Cost per drawn frame | Source | 10-minute video if every frame is drawn |
| --- | --- | --- |
| 282 ms | Measured, image character before the speed work | 67.7 min |
| 150 ms | Target for frames during a pose transition | 36.0 min |
| 30 ms | Target for face-only frames | 7.2 min |

In practice most frames repeat the previous one and are skipped (89.2% in the 20 s test, which had no lip sync, so real videos will skip fewer). The speed work is done when face-only frames meet 30 ms and transitions meet 150 ms, measured at 1080x1920 on the target machine.

Other budgets:

- **Hardware**: Intel i5 13th gen, 16 GB RAM, RTX 4050 with 6 GB VRAM. GPU stages run one at a time, and the model is unloaded between stages.
- **Memory**: 2.1 GB peak RAM measured in the cutout test. The target is to stay flat across a 10-minute render (the vector soak test was flat), which a long image-character render must confirm.
- **Disk**: raw frames never touch disk; scene MP4s and audio are the only large files per run.
- **Upscale step** (one-time per character): Real-ESRGAN 4x on the source took 6.8 s on the RTX 4050.

## 12. Open decisions

Six decisions are still open, and each one has a milestone that forces it.

| Decision | Options | Decide by |
| --- | --- | --- |
| Voice | Kokoro, Piper or a paid voice; check the model license if videos are monetized | M2 |
| Lip sync on the real voice | Keep the tuned values or retune; confirm by watching a 30 s clip | M2 |
| Hands and gestures | Keep hands baked into the forearms, or redraw hands as swappable variants for gestures such as pointing | After M1, before M5 |
| Shoulder and elbow seams | Accept the flat cut, limit arm rotation, or redraw joint caps | M1 review |
| Asset pack storage | In the repo (only 2x parts, no 4x files) or separate storage | M5 |
| Clipper code to copy | Final list from the clipper team's read-only assessment (file counts, imports, dependency size) | M0 |

The PRD (section 10) keeps the risks; this table keeps only decisions that change the code.
