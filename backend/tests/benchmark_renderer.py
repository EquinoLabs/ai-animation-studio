"""Repeatable benchmark measuring Skia drawing and FFmpeg stream delivery performance.

Executes a 72-frame (3.0s @ 24 FPS) dynamic animation with continuously varying
transforms (0 identical frames) to measure realistic non-cached rendering performance.
"""

import math
from pathlib import Path
import statistics
import tempfile
import time
import tracemalloc

from backend.app.services.renderer.frame_renderer import FrameRenderer, RenderLayer
from backend.app.services.renderer.stream_encoder import StreamEncoder
from backend.tests.synthetic_assets import generate_synthetic_assets


def run_benchmark(frame_count: int = 72, fps: int = 24, preferred_encoder: str = "auto") -> dict:
    """Run Skia rendering and FFmpeg streaming benchmark."""
    tracemalloc.start()
    start_wall_time = time.perf_counter()

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        assets = generate_synthetic_assets(tmp_path / "assets")

        renderer = FrameRenderer(width=1080, height=1920, fps=fps)
        encoder = StreamEncoder()
        output_mp4 = tmp_path / "benchmark_output.mp4"

        draw_times_ms = []
        pipe_write_times_ms = []

        def frame_generator():
            for i in range(frame_count):
                # Continuously varying transforms: 0 identical frames
                t = i / float(fps)
                body_angle = 6.0 * math.sin(t * 3.0)
                body_y = 960.0 + 15.0 * math.sin(t * 6.0)
                head_angle = -4.0 * math.sin(t * 3.0 + 0.5)
                head_scale = 1.0 + 0.03 * math.cos(t * 4.0)

                layers = [
                    RenderLayer(image_path=assets["background"], x=0, y=0, z_index=0),
                    RenderLayer(
                        image_path=assets["body"],
                        x=540,
                        y=body_y,
                        origin=(175, 50),
                        rotation_deg=body_angle,
                        scale=1.0,
                        z_index=10,
                    ),
                    RenderLayer(
                        image_path=assets["head"],
                        x=540,
                        y=body_y,
                        origin=(130, 230),
                        rotation_deg=head_angle,
                        scale=head_scale,
                        z_index=20,
                    ),
                ]

                t_draw_start = time.perf_counter()
                raw_bytes = renderer.render_frame(layers)
                t_draw_end = time.perf_counter()
                draw_times_ms.append((t_draw_end - t_draw_start) * 1000.0)

                yield raw_bytes

        # Custom wrapper to time pipe delivery during encode
        original_execute = encoder._execute_stream

        def timed_execute_stream(encoder, frames, output_path, width, height, fps, timeout_s):
            def timed_frames():
                for f_bytes in frames:
                    t_write_start = time.perf_counter()
                    yield f_bytes
                    t_write_end = time.perf_counter()
                    pipe_write_times_ms.append((t_write_end - t_write_start) * 1000.0)

            return original_execute(
                encoder=encoder,
                frames=timed_frames(),
                output_path=output_path,
                width=width,
                height=height,
                fps=fps,
                timeout_s=timeout_s,
            )

        encoder._execute_stream = timed_execute_stream

        result = encoder.encode(
            frame_source=frame_generator,
            output_path=output_mp4,
            width=1080,
            height=1920,
            fps=fps,
            preferred_encoder=preferred_encoder,
        )

        end_wall_time = time.perf_counter()
        current_mem, peak_mem = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        renderer.close()

        total_wall_s = end_wall_time - start_wall_time

        metrics = {
            "encoder_used": result.encoder_used,
            "frame_count": frame_count,
            "fps": fps,
            "resolution": "1080x1920",
            "video_duration_s": result.duration_s,
            "output_file_size_bytes": result.file_size_bytes,
            "output_file_size_kb": result.file_size_bytes / 1024.0,
            "total_wall_clock_s": total_wall_s,
            "effective_fps": frame_count / total_wall_s,
            "peak_python_memory_mb": peak_mem / (1024.0 * 1024.0),
            "draw_time_ms": {
                "mean": statistics.mean(draw_times_ms),
                "median": statistics.median(draw_times_ms),
                "min": min(draw_times_ms),
                "max": max(draw_times_ms),
            },
            "pipe_delivery_ms": {
                "mean": statistics.mean(pipe_write_times_ms) if pipe_write_times_ms else 0.0,
                "median": statistics.median(pipe_write_times_ms) if pipe_write_times_ms else 0.0,
                "min": min(pipe_write_times_ms) if pipe_write_times_ms else 0.0,
                "max": max(pipe_write_times_ms) if pipe_write_times_ms else 0.0,
            },
        }

        return metrics


if __name__ == "__main__":
    print("Running Skia & FFmpeg rendering benchmark (72 frames, 1080x1920 @ 24fps)...")
    res = run_benchmark()
    print("\n--- Benchmark Results ---")
    print(f"Encoder: {res['encoder_used']}")
    print(f"Frames: {res['frame_count']} ({res['video_duration_s']}s @ {res['fps']} FPS)")
    print(f"Resolution: {res['resolution']}")
    print(f"Output size: {res['output_file_size_kb']:.2f} KB ({res['output_file_size_bytes']} bytes)")
    print(f"Total wall clock: {res['total_wall_clock_s']:.3f} s (Effective: {res['effective_fps']:.1f} FPS)")
    print(f"Peak memory: {res['peak_python_memory_mb']:.2f} MB")
    print(f"Skia draw time per frame (ms): Mean={res['draw_time_ms']['mean']:.2f}, Median={res['draw_time_ms']['median']:.2f}, Min={res['draw_time_ms']['min']:.2f}, Max={res['draw_time_ms']['max']:.2f}")
    print(f"Pipe write time per frame (ms): Mean={res['pipe_delivery_ms']['mean']:.2f}, Median={res['pipe_delivery_ms']['median']:.2f}, Min={res['pipe_delivery_ms']['min']:.2f}, Max={res['pipe_delivery_ms']['max']:.2f}")
