"""Deterministic generator for synthetic test assets using Skia.

Creates background, body, and head PNG layers with transparency for testing
the cutout renderer without external dependencies.
"""

from pathlib import Path
import skia


def create_synthetic_png(
    output_path: Path,
    width: int,
    height: int,
    draw_fn,
) -> Path:
    """Helper to draw and save a PNG file using Skia."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    info = skia.ImageInfo.Make(
        width,
        height,
        skia.ColorType.kRGBA_8888_ColorType,
        skia.AlphaType.kPremul_AlphaType,
    )
    surface = skia.Surface.MakeRaster(info)
    canvas = surface.getCanvas()
    canvas.clear(skia.ColorTRANSPARENT)
    draw_fn(canvas, width, height)

    image = surface.makeImageSnapshot()
    data = image.encodeToData(skia.EncodedImageFormat.kPNG, 100)
    output_path.write_bytes(data.bytes())
    return output_path


def generate_synthetic_assets(directory: Path) -> dict[str, Path]:
    """Generate deterministic synthetic test assets in the specified folder."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    # 1. Background (1080 x 1920)
    def draw_bg(canvas, w, h):
        paint = skia.Paint()
        # Light calm blue background
        paint.setColor(skia.Color(235, 242, 250, 255))
        canvas.drawRect(skia.Rect(0, 0, w, h), paint)

        # Floor line
        floor_paint = skia.Paint()
        floor_paint.setColor(skia.Color(210, 225, 240, 255))
        canvas.drawRect(skia.Rect(0, h * 0.75, w, h), floor_paint)

    bg_path = create_synthetic_png(directory / "synth_bg.png", 1080, 1920, draw_bg)

    # 2. Body Layer (350 x 550, with pivot at center-top: 175, 50)
    def draw_body(canvas, w, h):
        paint = skia.Paint()
        paint.setAntiAlias(True)
        paint.setColor(skia.Color(180, 60, 60, 255))  # Red flannel tone
        rect = skia.Rect(25, 50, w - 25, h - 25)
        canvas.drawRoundRect(rect, 40, 40, paint)

    body_path = create_synthetic_png(directory / "synth_body.png", 350, 550, draw_body)

    # 3. Head Layer (260 x 260, circular face with eyes, pivot at chin center: 130, 230)
    def draw_head(canvas, w, h):
        paint = skia.Paint()
        paint.setAntiAlias(True)
        paint.setColor(skia.Color(245, 215, 195, 255))  # Skin tone
        canvas.drawCircle(w / 2, h / 2, 100, paint)

        # Eyes
        eye_paint = skia.Paint()
        eye_paint.setColor(skia.Color(40, 40, 40, 255))
        canvas.drawCircle(w / 2 - 35, h / 2 - 15, 10, eye_paint)
        canvas.drawCircle(w / 2 + 35, h / 2 - 15, 10, eye_paint)

        # Smile
        smile_paint = skia.Paint()
        smile_paint.setAntiAlias(True)
        smile_paint.setStyle(skia.Paint.kStroke_Style)
        smile_paint.setStrokeWidth(4)
        smile_paint.setColor(skia.Color(160, 80, 80, 255))
        canvas.drawArc(skia.Rect(w / 2 - 30, h / 2 + 10, w / 2 + 30, h / 2 + 45), 0, 180, False, smile_paint)

    head_path = create_synthetic_png(directory / "synth_head.png", 260, 260, draw_head)

    return {
        "background": bg_path,
        "body": body_path,
        "head": head_path,
    }
