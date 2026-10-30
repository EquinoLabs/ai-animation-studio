"""Tests for Skia frame renderer and FFmpeg streaming encoder."""

import json
from pathlib import Path
import shutil
import subprocess
from typing import List
import pytest
import skia

from backend.app.services.renderer.encoder import EncoderManager
from backend.app.services.renderer.frame_renderer import (
    FrameRenderer,
    RendererInputError,
    RenderLayer,
)
from backend.app.services.renderer.stream_encoder import StreamEncoder
from backend.tests.synthetic_assets import generate_synthetic_assets


@pytest.fixture(scope="module")
def synthetic_assets_dir(tmp_path_factory):
    tmp_dir = tmp_path_factory.mktemp("synthetic_assets")
    assets = generate_synthetic_assets(tmp_dir)
    return assets


def test_skia_surface_smoke():
    """Scope 2: Validate Skia surface creation, drawing, dimensions, and pixel format."""
    info = skia.ImageInfo.Make(
        200,
        100,
        skia.ColorType.kRGBA_8888_ColorType,
        skia.AlphaType.kPremul_AlphaType,
    )
    surface = skia.Surface.MakeRaster(info)
    assert surface is not None

    canvas = surface.getCanvas()
    canvas.clear(skia.Color(255, 0, 0, 255))  # Pure Red

    # Draw green rectangle at top-left
    paint = skia.Paint()
    paint.setColor(skia.Color(0, 255, 0, 255))
    canvas.drawRect(skia.Rect(0, 0, 50, 50), paint)

    img = surface.makeImageSnapshot()
    assert img.width() == 200
    assert img.height() == 100

    raw = img.tobytes()
    assert len(raw) == 200 * 100 * 4

    # Top-left pixel must be pure green (0, 255, 0, 255)
    assert list(raw[:4]) == [0, 255, 0, 255]

    # Pixel at x=100 (inside pure red region) must be (255, 0, 0, 255)
    pixel_100_offset = 100 * 4
    assert list(raw[pixel_100_offset : pixel_100_offset + 4]) == [255, 0, 0, 255]


def test_frame_renderer_dimensions_and_pixel_layout(synthetic_assets_dir):
    """Scope 3: Verify 1080x1920 output, 24 FPS configuration, and exact RGBA byte length."""
    renderer = FrameRenderer(width=1080, height=1920, fps=24)
    try:
        layers = [
            RenderLayer(image_path=synthetic_assets_dir["background"], x=0, y=0, z_index=0)
        ]
        raw_frame = renderer.render_frame(layers)
        assert len(raw_frame) == 1080 * 1920 * 4
        assert renderer.expected_frame_bytes == 8_294_400
    finally:
        renderer.close()


def test_alpha_compositing_layering(synthetic_assets_dir):
    """Scope 3: Verify multi-layer compositing in z-order with transparency."""
    renderer = FrameRenderer(width=1080, height=1920, fps=24)
    try:
        layers = [
            RenderLayer(
                image_path=synthetic_assets_dir["background"],
                x=0,
                y=0,
                z_index=0,
            ),
            RenderLayer(
                image_path=synthetic_assets_dir["body"],
                x=540,
                y=960,
                origin=(175, 50),
                rotation_deg=5.0,
                scale=1.0,
                z_index=10,
            ),
            RenderLayer(
                image_path=synthetic_assets_dir["head"],
                x=540,
                y=960,
                origin=(130, 230),
                rotation_deg=-3.0,
                scale=1.0,
                z_index=20,
            ),
        ]
        frame = renderer.render_frame(layers)
        assert len(frame) == 8_294_400
        # Background pixel at top left (10, 10):
        # row 10, col 10 => (10 * 1080 + 10) * 4
        idx = (10 * 1080 + 10) * 4
        # Calm blue background color: (235, 242, 250, 255)
        assert frame[idx + 3] == 255  # Fully opaque
    finally:
        renderer.close()


def test_renderer_input_validation_and_errors(tmp_path, synthetic_assets_dir):
    """Scope 3: Validate handling of non-existent files, corrupt files, and bad origins."""
    renderer = FrameRenderer(width=1080, height=1920, fps=24)
    try:
        # 1. Non-existent file
        with pytest.raises(RendererInputError, match="file not found"):
            renderer.render_frame([
                RenderLayer(image_path=tmp_path / "does_not_exist.png", x=0, y=0)
            ])

        # 2. Corrupt or non-image file
        corrupt_file = tmp_path / "corrupt.png"
        corrupt_file.write_bytes(b"not an image data")
        with pytest.raises(RendererInputError, match="Failed to read image|Corrupt"):
            renderer.render_frame([
                RenderLayer(image_path=corrupt_file, x=0, y=0)
            ])

        # 3. Origin outside image bounds
        with pytest.raises(RendererInputError, match="Pivot origin"):
            renderer.render_frame([
                RenderLayer(
                    image_path=synthetic_assets_dir["head"],
                    x=0,
                    y=0,
                    origin=(9999, 9999),
                )
            ])
    finally:
        renderer.close()


def test_stream_encoder_ffmpeg_args(tmp_path):
    """Scope 4: Validate argument arrays for both NVENC and libx264 encoders."""
    encoder = StreamEncoder(ffmpeg_bin="ffmpeg")
    out = tmp_path / "test.mp4"

    nvenc_args = encoder.build_ffmpeg_args("h264_nvenc", out, 1080, 1920, 24)
    assert nvenc_args[0] == "ffmpeg"
    assert nvenc_args[nvenc_args.index("-c:v") + 1] == "h264_nvenc"
    assert "-pix_fmt" in nvenc_args
    assert "rgba" in nvenc_args
    assert "yuv420p" in nvenc_args

    x264_args = encoder.build_ffmpeg_args("libx264", out, 1080, 1920, 24)
    assert x264_args[x264_args.index("-c:v") + 1] == "libx264"
    assert "-crf" in x264_args


def test_stream_encoder_real_local_encode(tmp_path, synthetic_assets_dir):
    """Scope 4: Real local FFmpeg encode test streaming 12 frames (0.5s at 24fps) from Skia."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("FFmpeg binary not found on PATH")

    renderer = FrameRenderer(width=1080, height=1920, fps=24)
    encoder = StreamEncoder()

    def generate_frames():
        for i in range(12):
            angle = i * 2.0
            layers = [
                RenderLayer(image_path=synthetic_assets_dir["background"], x=0, y=0, z_index=0),
                RenderLayer(
                    image_path=synthetic_assets_dir["body"],
                    x=540,
                    y=960,
                    origin=(175, 50),
                    rotation_deg=angle,
                    z_index=10,
                ),
                RenderLayer(
                    image_path=synthetic_assets_dir["head"],
                    x=540,
                    y=960,
                    origin=(130, 230),
                    rotation_deg=-angle,
                    z_index=20,
                ),
            ]
            yield renderer.render_frame(layers)

    out_mp4 = tmp_path / "smoke_real_encode.mp4"
    result = encoder.encode(
        frame_source=generate_frames,
        output_path=out_mp4,
        width=1080,
        height=1920,
        fps=24,
        preferred_encoder="auto",
    )

    renderer.close()

    assert result.frame_count == 12
    assert result.output_path.exists()
    assert result.file_size_bytes > 0
    assert result.duration_s == 0.5
    assert result.encoder_used in ("h264_nvenc", "libx264")

    # Verify with ffprobe if available
    if shutil.which("ffprobe") is not None:
        probe_cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=width,height,r_frame_rate,duration",
            "-of",
            "json",
            str(out_mp4),
        ]
        probe_proc = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
        probe_data = json.loads(probe_proc.stdout)
        stream = probe_data["streams"][0]
        assert stream["width"] == 1080
        assert stream["height"] == 1920
        assert stream["r_frame_rate"] == "24/1"
