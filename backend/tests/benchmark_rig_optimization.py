"""Comprehensive benchmark comparing baseline, pose transitions, and face-only workloads.

Measures:
1. Baseline Active Animation (Uncached redraws)
2. Pose-Transition Workload (Active body kinematics with cached background)
3. Face-Only Workload (Stable body with head micro-motion and composite caching)
"""

import math
from pathlib import Path
import statistics
import tempfile
import time
import tracemalloc

from backend.app.services.characters.poses import PartPose, Pose, PoseInterpolator
from backend.app.services.characters.rig import CharacterPart, CharacterRig
from backend.app.services.renderer.frame_renderer import FrameRenderer
from backend.app.services.renderer.stream_encoder import StreamEncoder
from backend.tests.synthetic_assets import generate_synthetic_assets


def run_workload_benchmark(workload_type: str, frame_count: int = 72, fps: int = 24) -> dict:
    tracemalloc.start()
    t_start_wall = time.perf_counter()

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        assets = generate_synthetic_assets(tmp_path / "assets")

        renderer = FrameRenderer(width=1080, height=1920, fps=fps)
        encoder = StreamEncoder()
        output_mp4 = tmp_path / f"bench_{workload_type}.mp4"

        # Rig setup
        parts = {
            "body": CharacterPart(
                id="body",
                image_path=assets["body"],
                attach=(540.0, 960.0),
                origin=(175.0, 50.0),
                rotation_deg=0.0,
                z_index=10,
            ),
            "head": CharacterPart(
                id="head",
                image_path=assets["head"],
                parent_id="body",
                attach=(0.0, -180.0),
                origin=(130.0, 230.0),
                rotation_deg=0.0,
                z_index=20,
            ),
        }
        rig = CharacterRig("bench_rig", parts)

        pose_idle = Pose("idle", parts={"body": PartPose(rotation_deg=0.0), "head": PartPose(rotation_deg=0.0)})
        pose_point = Pose("point", parts={"body": PartPose(rotation_deg=18.0), "head": PartPose(rotation_deg=-8.0)})

        draw_latencies_ms = []
        pipe_latencies_ms = []

        def frame_source():
            for i in range(frame_count):
                t = i / float(fps)

                if workload_type == "baseline_uncached":
                    # Fully uncached: clear cache every frame to simulate raw draw cost
                    renderer.clear_cache()
                    # Animate both body and head
                    angle_b = 6.0 * math.sin(t * 4.0)
                    angle_h = -5.0 * math.sin(t * 4.0)
                    current_rig = rig.with_part_transform("body", rotation_deg=angle_b)
                    current_rig = current_rig.with_part_transform("head", rotation_deg=angle_h)
                    face_ids = None

                elif workload_type == "pose_transition":
                    # Cycle between idle and point every 0.5s transition
                    cycle_t = t % 1.0
                    if cycle_t < 0.5:
                        current_rig = PoseInterpolator.interpolate(rig, pose_idle, pose_point, cycle_t, 0.5)
                    else:
                        current_rig = PoseInterpolator.interpolate(rig, pose_point, pose_idle, cycle_t - 0.5, 0.5)
                    # No body cache because body is in active transition
                    face_ids = None

                elif workload_type == "face_only":
                    # Body is stable in idle pose; head has active speaking/nodding motion
                    head_angle = 3.0 * math.sin(t * 8.0)
                    head_scale = 1.0 + 0.02 * math.cos(t * 10.0)
                    current_rig = rig.with_part_transform("head", rotation_deg=head_angle, scale=head_scale)
                    # Body composite cache active for "head"
                    face_ids = {"head"}
                else:
                    raise ValueError(f"Unknown workload: {workload_type}")

                # Measure pure frame render (world transforms + Skia composite + snapshot + tobytes)
                t_draw0 = time.perf_counter()
                frame_bytes = renderer.render_rig(
                    rig=current_rig,
                    background_image=assets["background"],
                    face_part_ids=face_ids,
                )
                t_draw1 = time.perf_counter()
                draw_latencies_ms.append((t_draw1 - t_draw0) * 1000.0)

                yield frame_bytes

        # Instrument pipe delivery
        original_execute = encoder._execute_stream

        def timed_execute(encoder, frames, output_path, width, height, fps, timeout_s):
            def timed_frames():
                for f in frames:
                    tw0 = time.perf_counter()
                    yield f
                    tw1 = time.perf_counter()
                    pipe_latencies_ms.append((tw1 - tw0) * 1000.0)

            return original_execute(
                encoder=encoder,
                frames=timed_frames(),
                output_path=output_path,
                width=width,
                height=height,
                fps=fps,
                timeout_s=timeout_s,
            )

        encoder._execute_stream = timed_execute

        result = encoder.encode(
            frame_source=frame_source,
            output_path=output_mp4,
            width=1080,
            height=1920,
            fps=fps,
            preferred_encoder="auto",
        )

        t_end_wall = time.perf_counter()
        _, peak_mem = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        cache_stats = renderer.cache_stats
        renderer.close()

        total_wall_s = t_end_wall - t_start_wall

        steady_draw_ms = draw_latencies_ms[1:] if len(draw_latencies_ms) > 1 else draw_latencies_ms

        return {
            "workload": workload_type,
            "encoder": result.encoder_used,
            "frame_count": frame_count,
            "total_wall_s": total_wall_s,
            "effective_fps": frame_count / total_wall_s,
            "peak_memory_mb": peak_mem / (1024.0 * 1024.0),
            "cache_hits": cache_stats["hits"],
            "cache_misses": cache_stats["misses"],
            "draw_ms": {
                "mean": statistics.mean(draw_latencies_ms),
                "median": statistics.median(draw_latencies_ms),
                "min": min(draw_latencies_ms),
                "max": max(draw_latencies_ms),
                "steady_mean": statistics.mean(steady_draw_ms),
                "steady_median": statistics.median(steady_draw_ms),
            },
            "pipe_ms": {
                "mean": statistics.mean(pipe_latencies_ms) if pipe_latencies_ms else 0.0,
                "median": statistics.median(pipe_latencies_ms) if pipe_latencies_ms else 0.0,
                "min": min(pipe_latencies_ms) if pipe_latencies_ms else 0.0,
                "max": max(pipe_latencies_ms) if pipe_latencies_ms else 0.0,
            },
        }


if __name__ == "__main__":
    print("Running Rig Optimization Benchmark Suite (72 frames each @ 24fps)...")

    print("\n--- 1. Baseline Workload (Uncached redraws) ---")
    res_base = run_workload_benchmark("baseline_uncached")
    print(f"Draw latency: Mean={res_base['draw_ms']['mean']:.2f}ms, Median={res_base['draw_ms']['median']:.2f}ms, Min={res_base['draw_ms']['min']:.2f}ms, Max={res_base['draw_ms']['max']:.2f}ms")
    print(f"Total wall time: {res_base['total_wall_s']:.2f}s (Effective: {res_base['effective_fps']:.1f} FPS)")

    print("\n--- 2. Pose-Transition Workload (Background cached, active body kinematics) ---")
    res_pose = run_workload_benchmark("pose_transition")
    print(f"Draw latency: Mean={res_pose['draw_ms']['mean']:.2f}ms, Median={res_pose['draw_ms']['median']:.2f}ms, Min={res_pose['draw_ms']['min']:.2f}ms, Max={res_pose['draw_ms']['max']:.2f}ms")
    print(f"Total wall time: {res_pose['total_wall_s']:.2f}s (Effective: {res_pose['effective_fps']:.1f} FPS)")
    print(f"Target <= 150 ms: {'MET' if res_pose['draw_ms']['mean'] <= 150.0 else 'UNMET'}")

    print("\n--- 3. Face-Only Workload (Body composite cached, head micro-movement) ---")
    res_face = run_workload_benchmark("face_only")
    print(f"Draw latency: Overall Mean={res_face['draw_ms']['mean']:.2f}ms, Steady Mean (frames 1-71)={res_face['draw_ms']['steady_mean']:.2f}ms, Median={res_face['draw_ms']['median']:.2f}ms, Min={res_face['draw_ms']['min']:.2f}ms, Max={res_face['draw_ms']['max']:.2f}ms")
    print(f"Total wall time: {res_face['total_wall_s']:.2f}s (Effective: {res_face['effective_fps']:.1f} FPS)")
    print(f"Cache Hits: {res_face['cache_hits']} / {res_face['frame_count']} (Misses: {res_face['cache_misses']})")
    print(f"Steady-state Target <= 30 ms: {'MET' if res_face['draw_ms']['steady_mean'] <= 30.0 else 'UNMET'}")
