# Milestone 1 Task 01B: Character Rig, Pose Blending & Render Optimization

Date: 2026-10-29  
Status: Implemented, Tested & Profiled  

## 1. Overview
Task M1-01B establishes a validated, hierarchical 2D character rig and deterministic smoothstep pose blending engine on top of the M1-01A Skia renderer. Guided by stage-level profiling, it introduces two layers of raster caching (background Pixmap restore and body composite caching) to accelerate frame rendering.

## 2. Phase 1 Profiling Findings
Stage-level profiling of the unoptimized 72-frame active animation baseline revealed:
- **Transform calculation:** 0.067 ms / frame (< 0.1% of budget).
- **Skia snapshot:** 1.124 ms / frame.
- **Memory extraction (`tobytes`):** 4.695 ms / frame.
- **Pipe delivery to FFmpeg:** 7.001 ms / frame.
- **Canvas drawing:** **376.179 ms / frame (97.5% of total time)**.
  - Deeper layer isolation revealed that drawing the 1080x1920 background layer via `canvas.drawImage` consumed **192.08 ms** on every frame.
  - Drawing body parts took 16.64 ms, and head took 6.68 ms.

## 3. Architecture & Implemented Components

### A. Hierarchical Rig (`backend/app/services/characters/rig.py`)
- `CharacterPart`: Represents a cutout part with image path, parent reference (`parent_id`), local pivot (`origin`), local offset (`attach`), rotation in degrees, scale, opacity, and `z_index`.
- `CharacterRig`: Validates acyclicity using Kahn's topological sort, verifies parent references, checks image accessibility and dimension bounds, and prevents out-of-bounds pivots.
- Forward Kinematics: Resolves accumulated world translations, rotations, scales, and opacities from root to leaves in topological order.

### B. Pose Definitions & Blending (`backend/app/services/characters/poses.py`)
- `Pose`: Named dictionary of `PartPose` transform targets.
- `smoothstep`: Standard Hermite interpolation ($S(t) = 3t^2 - 2t^3$) over a configurable transition duration (default 0.5s).
- `interpolate_angle_deg`: Shortest-path angular interpolation handling $360^\circ$ wrapping without unnatural spin.
- `PoseInterpolator.interpolate`: Pure, deterministic blending function producing an interpolated `CharacterRig` at any timestamp $t$.

### C. Safe Raster Caching (`backend/app/services/renderer/frame_renderer.py`)
- **Background Pixmap Restore:** Pre-renders background once and blits it via `_surface.writePixels(cached_pixmap, 0, 0)` in **4.15 ms** instead of a 192 ms `drawImage`.
- **Body Composite Cache:** When face/head parts are isolated (`face_part_ids`), parts below the head are pre-composited with the background into a cached Pixmap keyed on the exact body transforms state.
- **Lifetime Safety:** Pixmap byte buffers (`self._cached_bg_bytes`, `self._cached_body_bytes`) are anchored to the renderer instance to avoid pybind11 dangling pointer deallocations.
- **Strict Invalidation:** Any change in body rotation, position, opacity, or background immediately invalidates the composite cache.

## 4. Benchmark Results & Target Analysis

### Comparable 72-Frame Benchmark Workloads (1080x1920 @ 24 FPS, NVENC)

| Metric | 1. Baseline (Uncached Redraws) | 2. Pose-Transition (Background Cached, Dynamic Body) | 3. Face-Only (Body Composite Cached, Head Motion) |
| :--- | :--- | :--- | :--- |
| **Workload Description** | Full redraw every frame (changing body + head) | Idle $\leftrightarrow$ Point smoothstep transition every 0.5s | Static body, active head nodding/micro-motion |
| **Encoder Used** | `h264_nvenc` | `h264_nvenc` | `h264_nvenc` |
| **Cache Hits** | 0 / 72 | 0 / 72 (Body dynamic) | **71 / 72 (98.6% hit rate)** |
| **Draw Latency (Overall Mean)** | 639.90 ms | **134.52 ms** | **57.41 ms** |
| **Draw Latency (Steady Mean, fr 1-71)**| 620.15 ms | **120.73 ms** | **45.57 ms** |
| **Draw Latency (Median)** | 573.02 ms | **113.07 ms** | **39.73 ms** |
| **Draw Latency (Min)** | 297.67 ms | **49.36 ms** | **16.16 ms** |
| **Draw Latency (Max)** | 2042.43 ms | 1106.98 ms | 898.28 ms (cold frame 0) |
| **Total Wall Clock Duration** | 50.04 s | **15.06 s** | **8.59 s** |
| **Effective Pipeline Rate** | 1.4 FPS | **4.8 FPS** | **8.4 FPS** |
| **Target Check** | N/A | **Target $\le$ 150 ms: MET** | **Target $\le$ 30 ms: UNMET (45.57 ms steady mean)** |

### Bottleneck Analysis on Face-Only Target ($\le$ 30 ms)
- **Observed:** Steady-state face-only frames achieve **39.73 ms median** and **16.16 ms minimum**, but the steady mean sits at **45.57 ms** (missing the 30 ms target by 15.57 ms).
- **Primary Root Causes:**
  1. *Concurrent Pipe Bottleneck:* When piping 8.3 MB per frame to FFmpeg's stdin while FFmpeg encodes with NVENC, thread contention on Windows temporarily stretches frame handoffs. Measuring pure off-thread rendering without pipe writes achieves **16.6 ms / frame** (well under 30 ms).
  2. *Cold Start Spike:* Frame 0 has an 898 ms cold start due to NVENC process startup and initial texture compilation.
  3. *Unscaled Head Asset:* Head layer is currently downsampled dynamically during canvas rendering.
- **Remediation for M1:**
  1. *Identical Frame Skipping:* In real videos, unchanged frames (pauses, holds) bypass drawing completely, transmitting previous frame bytes (~0.5 ms).
  2. *Pre-scaled 1x Parts:* Pre-scaling parts to display resolution eliminates dynamic sampling.
