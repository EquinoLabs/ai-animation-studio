"""Fine-grained profiling script measuring individual stages of the render pipeline.

Instruments:
1. Transform calculation
2. Skia canvas clearing & drawing operations
3. Skia snapshot creation (surface.makeImageSnapshot)
4. Memory extraction (snapshot.tobytes)
5. Pipe delivery to FFmpeg
6. Total wall-clock time
"""

from pathlib import Path
import statistics
import tempfile
import time
import skia

from backend.app.services.renderer.frame_renderer import FrameRenderer, RenderLayer
from backend.app.services.renderer.stream_encoder import StreamEncoder
from backend.tests.synthetic_assets import generate_synthetic_assets


def profile_pipeline(frame_count: int = 72, fps: int = 24) -> dict:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        assets = generate_synthetic_assets(tmp_path / "assets")

        renderer = FrameRenderer(width=1080, height=1920, fps=fps)
        encoder = StreamEncoder()
        output_mp4 = tmp_path / "profile_output.mp4"

        transform_times_ms = []
        canvas_draw_times_ms = []
        snapshot_times_ms = []
        tobytes_times_ms = []
        pipe_write_times_ms = []

        # Pre-warm asset cache
        renderer.get_or_load_image(assets["background"])
        renderer.get_or_load_image(assets["body"])
        renderer.get_or_load_image(assets["head"])

        def frame_generator():
            for i in range(frame_count):
                # 1. Transform calculation
                t0 = time.perf_counter()
                t = i / float(fps)
                body_angle = 6.0 * (i % 12 - 6) / 6.0
                body_y = 960.0 + 10.0 * (i % 6)
                head_angle = -4.0 * (i % 10 - 5) / 5.0
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
                        scale=1.0,
                        z_index=20,
                    ),
                ]
                t1 = time.perf_counter()
                transform_times_ms.append((t1 - t0) * 1000.0)

                # 2. Skia Canvas drawing
                t_draw_start = time.perf_counter()
                canvas = renderer._surface.getCanvas()
                canvas.clear(skia.Color(255, 255, 255, 255))
                for layer in sorted(layers, key=lambda l: l.z_index):
                    img = renderer.get_or_load_image(layer.image_path)
                    ox, oy = layer.origin
                    canvas.save()
                    canvas.translate(layer.x, layer.y)
                    if layer.rotation_deg != 0.0:
                        canvas.rotate(layer.rotation_deg)
                    if layer.scale != 1.0:
                        canvas.scale(layer.scale, layer.scale)
                    paint = skia.Paint()
                    paint.setAntiAlias(True)
                    canvas.drawImage(img, -ox, -oy, skia.SamplingOptions(), paint)
                    canvas.restore()
                t_draw_end = time.perf_counter()
                canvas_draw_times_ms.append((t_draw_end - t_draw_start) * 1000.0)

                # 3. Snapshot
                t_snap_start = time.perf_counter()
                snapshot = renderer._surface.makeImageSnapshot()
                t_snap_end = time.perf_counter()
                snapshot_times_ms.append((t_snap_end - t_snap_start) * 1000.0)

                # 4. tobytes
                t_bytes_start = time.perf_counter()
                raw_bytes = snapshot.tobytes()
                t_bytes_end = time.perf_counter()
                tobytes_times_ms.append((t_bytes_end - t_bytes_start) * 1000.0)

                yield raw_bytes

        original_execute = encoder._execute_stream

        def timed_execute(encoder, frames, output_path, width, height, fps, timeout_s):
            def timed_frames():
                for f in frames:
                    t_w0 = time.perf_counter()
                    yield f
                    t_w1 = time.perf_counter()
                    pipe_write_times_ms.append((t_w1 - t_w0) * 1000.0)

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

        t_total_start = time.perf_counter()
        result = encoder.encode(
            frame_source=frame_generator,
            output_path=output_mp4,
            width=1080,
            height=1920,
            fps=fps,
            preferred_encoder="auto",
        )
        t_total_end = time.perf_counter()
        renderer.close()

        def stats(lst):
            return {
                "mean": statistics.mean(lst) if lst else 0.0,
                "median": statistics.median(lst) if lst else 0.0,
                "min": min(lst) if lst else 0.0,
                "max": max(lst) if lst else 0.0,
            }

        return {
            "encoder": result.encoder_used,
            "total_wall_s": t_total_end - t_total_start,
            "transforms": stats(transform_times_ms),
            "canvas_draw": stats(canvas_draw_times_ms),
            "snapshot": stats(snapshot_times_ms),
            "tobytes": stats(tobytes_times_ms),
            "pipe_write": stats(pipe_write_times_ms),
        }


if __name__ == "__main__":
    print("Profiling 72 frames across stages...")
    res = profile_pipeline(72, 24)
    print(f"\nEncoder Used: {res['encoder']}")
    print(f"Total Wall Clock: {res['total_wall_s']:.3f} s")
    print(f"1. Transform calc (ms): mean={res['transforms']['mean']:.3f}, max={res['transforms']['max']:.3f}")
    print(f"2. Canvas draw (ms):    mean={res['canvas_draw']['mean']:.3f}, median={res['canvas_draw']['median']:.3f}, max={res['canvas_draw']['max']:.3f}")
    print(f"3. Snapshot (ms):       mean={res['snapshot']['mean']:.3f}, median={res['snapshot']['median']:.3f}, max={res['snapshot']['max']:.3f}")
    print(f"4. tobytes (ms):        mean={res['tobytes']['mean']:.3f}, median={res['tobytes']['median']:.3f}, max={res['tobytes']['max']:.3f}")
    print(f"5. Pipe write (ms):     mean={res['pipe_write']['mean']:.3f}, median={res['pipe_write']['median']:.3f}, max={res['pipe_write']['max']:.3f}")
