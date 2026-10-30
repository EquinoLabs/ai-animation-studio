# Milestone 1 Task 01A: Skia Renderer Compatibility & Performance Spike

Date: 2026-10-29  
Status: Completed & Measured  

## 1. Objective
Validate that `skia-python` operates reliably in the project's Windows 10/11 + Python 3.13.2 64-bit environment, establish a minimal multi-layer frame renderer with alpha compositing at 1080x1920 @ 24 FPS, connect it to FFmpeg via direct raw RGBA stdin streaming, and benchmark performance with active animation (0 identical frames).

## 2. Environment & Compatibility Findings
- **Python Version:** 3.13.2 (Windows 64-bit).
- **Package:** `skia-python==144.1` with `numpy==2.5.4` installs cleanly from pre-built wheels (`skia_python-144.1-cp313-cp313-win_amd64.whl`).
- **Surface Pixel Format:**
  - Standard `skia.Surface(w, h)` defaults to `kBGRA_8888_ColorType` on Windows.
  - To produce explicit RGBA bytes matching FFmpeg `-pix_fmt rgba`, surfaces must be created via:
    `skia.Surface.MakeRaster(skia.ImageInfo.Make(w, h, skia.ColorType.kRGBA_8888_ColorType, skia.AlphaType.kPremul_AlphaType))`.
- **Canvas DrawImage API:**
  - In `skia-python` 144.1, `canvas.drawImage` requires sampling options: `canvas.drawImage(image, left, top, skia.SamplingOptions(), paint)`.
- **FFmpeg Probe Dimension Requirement:**
  - `h264_nvenc` enforces a minimum hardware encoding resolution (testing at 64x64 triggers `InitializeEncoder failed: Frame Dimension less than the minimum supported value`).
  - The encoder probe was adjusted to 256x256, which passes immediately and activates hardware acceleration.

## 3. Architecture & Implementation
- `backend/app/services/renderer/frame_renderer.py`:
  - `FrameRenderer`: Allocates a single reusable 1080x1920 raster Skia surface.
  - `RenderLayer`: Value object specifying image path, position `(x, y)`, pivot `(ox, oy)`, rotation in degrees, scale, opacity, and `z_index`.
  - In-memory texture caching avoids reading PNG files from disk repeatedly during animation loops.
  - Input validation: verifies file existence, readable image header, positive dimensions, and ensures pivot points reside within image boundaries.
  - Clean resource management via `close()`.
- `backend/app/services/renderer/stream_encoder.py`:
  - `StreamEncoder`: Pipes raw RGBA bytes frame-by-frame to FFmpeg stdin (`pipe:0`) using argument arrays and bounded subprocess timeouts.
  - Eliminates disk frame I/O entirely.
  - Automatically unlinks any incomplete or zero-byte output file if encoding aborts.
  - Integrated with `EncoderManager` to support preferred `h264_nvenc` encoding with automatic fallback to `libx264`.

## 4. Benchmark Measurements
- **Workload:** 72 frames (3.0 seconds @ 24 FPS) rendered at 1080x1920.
- **Animation Pattern:** Continuous sinusoidal body sway/translation and head bob/rotation (0 identical frames; no caching shortcuts).
- **Environment:** Intel Core i5 13th Gen, 16 GB RAM, NVIDIA RTX 4050 Laptop GPU (6 GB VRAM), Windows 11.

| Metric | Measured Result (NVENC) | Measured Result (libx264 fallback) | Notes |
| :--- | :--- | :--- | :--- |
| **Encoder Used** | `h264_nvenc` (CQ 19, p4) | `libx264` (CRF 19, fast) | Auto-selected by `EncoderManager` |
| **Frame Dimensions** | 1080 x 1920 (RGBA) | 1080 x 1920 (RGBA) | 8,294,400 bytes per frame |
| **Frames Rendered** | 72 frames | 72 frames | Full 3.0s duration |
| **Output File Size** | 154.80 KB (158,516 B) | 78.85 KB (80,739 B) | Clean, playable H.264 MP4 |
| **Total Wall Clock Duration** | **28.76 seconds** | **30.57 seconds** | End-to-end (draw + encode) |
| **Effective Pipeline Rate** | **2.50 FPS** | **2.35 FPS** | Without frame skipping or caching |
| **Skia Draw Time (Mean)** | **365.69 ms / frame** | **376.98 ms / frame** | Drawing 3 layers (bg, body, head) |
| **Skia Draw Time (Median)** | **361.80 ms / frame** | **367.85 ms / frame** | Consistent latency |
| **Skia Draw Time (Min / Max)**| **201.34 ms / 534.60 ms** | **268.79 ms / 596.93 ms**| Max includes initial texture upload |
| **Pipe Delivery Time (Mean)** | **12.53 ms / frame** | **4.70 ms / frame** | Time to write 8.3 MB into stdin |
| **Pipe Delivery Time (Median)**| **3.36 ms / frame** | **3.98 ms / frame** | Highly efficient I/O |
| **Peak Python Memory** | **16.00 MB** | **16.00 MB** | Flat memory; no memory leaks |

### Observations vs Future Milestones:
- The measured ~365 ms per-frame drawing time reflects non-cached full-frame redraws at 1080x1920 with high-quality transforms.
- This closely mirrors the initial spike baseline recorded in PRD Section 13 (282 ms before speed optimizations).
- The M1 speed target (30 ms for face-only frames, 150 ms for pose transitions) will be unlocked in subsequent tasks via:
  1. Identical frame skipping (~89% of frames in typical narration).
  2. Pre-scaled character parts (eliminating runtime Lanczos/cubic scaling of 2x assets).
  3. Layer composite caching for body layers beneath facial overlays.
