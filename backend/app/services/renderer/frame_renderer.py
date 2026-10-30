"""Skia-based frame renderer with validated caching and rig compositing per TechSpec Section 6."""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import skia

from backend.app.services.characters.rig import CharacterRig, WorldTransform


class RendererInputError(ValueError):
    """Raised when layer dimensions, paths, or origins are invalid."""


@dataclass(frozen=True)
class RenderLayer:
    """Represents an image layer to be composited onto the frame."""

    image_path: Path
    x: float
    y: float
    origin: Tuple[float, float] = (0.0, 0.0)
    rotation_deg: float = 0.0
    scale: float = 1.0
    opacity: float = 1.0
    z_index: int = 0


class FrameRenderer:
    """High-performance frame compositor using Skia raster surface with safe composite caching."""

    def __init__(
        self,
        width: int = 1080,
        height: int = 1920,
        fps: int = 24,
    ):
        if width <= 0 or height <= 0:
            raise RendererInputError(f"Invalid dimensions: {width}x{height}")
        if fps <= 0:
            raise RendererInputError(f"Invalid fps: {fps}")

        self.width = width
        self.height = height
        self.fps = fps
        self.expected_frame_bytes = width * height * 4

        self._image_info = skia.ImageInfo.Make(
            self.width,
            self.height,
            skia.ColorType.kRGBA_8888_ColorType,
            skia.AlphaType.kPremul_AlphaType,
        )
        self._surface = skia.Surface.MakeRaster(self._image_info)
        if not self._surface:
            raise RuntimeError("Failed to allocate Skia raster surface")

        # Caches
        self._image_cache: Dict[Path, skia.Image] = {}
        self._bg_cache_key: Optional[Tuple[Optional[Path], Optional[Tuple[int, int, int, int]]]] = None
        self._cached_bg_surface: Optional[skia.Surface] = None
        self._cached_bg_bytes: Optional[bytes] = None
        self._cached_bg_pixmap: Optional[skia.Pixmap] = None

        self._body_composite_key: Optional[Tuple] = None
        self._cached_body_bytes: Optional[bytes] = None
        self._cached_body_pixmap: Optional[skia.Pixmap] = None

        self._cache_hits = 0
        self._cache_misses = 0

    @property
    def cache_stats(self) -> Dict[str, int]:
        """Return total composite cache hits and misses."""
        return {"hits": self._cache_hits, "misses": self._cache_misses}

    def get_or_load_image(self, path: Path) -> skia.Image:
        """Load and cache image from disk with input validation."""
        resolved = path.resolve()
        if resolved in self._image_cache:
            return self._image_cache[resolved]

        if not resolved.exists() or not resolved.is_file():
            raise RendererInputError(f"Layer image file not found: {resolved}")

        try:
            image = skia.Image.open(str(resolved))
        except Exception as exc:
            raise RendererInputError(f"Failed to read image '{resolved}': {exc}") from exc

        if not image or image.width() <= 0 or image.height() <= 0:
            raise RendererInputError(f"Corrupt or empty image: {resolved}")

        self._image_cache[resolved] = image
        return image

    def _prepare_background(
        self,
        background_image: Optional[Path] = None,
        background_color: Optional[Tuple[int, int, int, int]] = (255, 255, 255, 255),
    ) -> skia.Pixmap:
        """Return a pre-rendered background pixmap, caching when inputs remain identical."""
        cache_key = (background_image.resolve() if background_image else None, background_color)
        if self._cached_bg_pixmap is not None and self._bg_cache_key == cache_key:
            return self._cached_bg_pixmap

        # Re-render background onto dedicated surface
        if self._cached_bg_surface is None:
            self._cached_bg_surface = skia.Surface.MakeRaster(self._image_info)

        bg_canvas = self._cached_bg_surface.getCanvas()
        if background_color is not None:
            r, g, b, a = background_color
            bg_canvas.clear(skia.Color(r, g, b, a))
        else:
            bg_canvas.clear(skia.ColorTRANSPARENT)

        if background_image is not None:
            bg_img = self.get_or_load_image(background_image)
            paint = skia.Paint()
            paint.setAntiAlias(True)
            bg_canvas.drawImage(bg_img, 0, 0, skia.SamplingOptions(), paint)

        pixmap = skia.Pixmap()
        self._cached_bg_surface.peekPixels(pixmap)
        self._cached_bg_bytes = bytes(pixmap)
        self._cached_bg_pixmap = skia.Pixmap(self._image_info, self._cached_bg_bytes, self.width * 4)
        self._bg_cache_key = cache_key
        return self._cached_bg_pixmap

    def render_frame(
        self,
        layers: List[RenderLayer],
        background_color: Optional[Tuple[int, int, int, int]] = (255, 255, 255, 255),
    ) -> bytes:
        """Composite layers in z-index order and return raw RGBA bytes."""
        # Separate full-frame background layer if present at (0, 0)
        bg_layer = None
        other_layers = []
        for l in layers:
            if l.x == 0 and l.y == 0 and l.rotation_deg == 0.0 and l.scale == 1.0 and l.z_index == 0:
                img = self.get_or_load_image(l.image_path)
                if img.width() == self.width and img.height() == self.height:
                    bg_layer = l
                    continue
            other_layers.append(l)

        if bg_layer is not None:
            bg_pixmap = self._prepare_background(background_image=bg_layer.image_path, background_color=background_color)
            self._surface.writePixels(bg_pixmap, 0, 0)
        else:
            canvas = self._surface.getCanvas()
            if background_color is not None:
                r, g, b, a = background_color
                canvas.clear(skia.Color(r, g, b, a))
            else:
                canvas.clear(skia.ColorTRANSPARENT)

        canvas = self._surface.getCanvas()
        sorted_layers = sorted(other_layers, key=lambda l: l.z_index)

        for layer in sorted_layers:
            img = self.get_or_load_image(layer.image_path)
            ox, oy = layer.origin
            if ox < 0 or ox > img.width() or oy < 0 or oy > img.height():
                raise RendererInputError(
                    f"Pivot origin ({ox}, {oy}) is outside image bounds ({img.width()}x{img.height()})"
                )

            canvas.save()
            try:
                canvas.translate(layer.x, layer.y)
                if layer.rotation_deg != 0.0:
                    canvas.rotate(layer.rotation_deg)
                if layer.scale != 1.0:
                    canvas.scale(layer.scale, layer.scale)

                paint = skia.Paint()
                paint.setAntiAlias(True)
                if layer.opacity < 1.0:
                    paint.setAlphaf(max(0.0, min(1.0, layer.opacity)))

                canvas.drawImage(img, -ox, -oy, skia.SamplingOptions(), paint)
            finally:
                canvas.restore()

        snapshot = self._surface.makeImageSnapshot()
        raw_bytes = snapshot.tobytes()
        if len(raw_bytes) != self.expected_frame_bytes:
            raise RuntimeError(
                f"Unexpected frame byte size: got {len(raw_bytes)}, expected {self.expected_frame_bytes}"
            )
        return raw_bytes

    def render_rig(
        self,
        rig: CharacterRig,
        background_image: Optional[Path] = None,
        background_color: Optional[Tuple[int, int, int, int]] = (255, 255, 255, 255),
        face_part_ids: Optional[Set[str]] = None,
    ) -> bytes:
        """Render a CharacterRig using body composite caching for face-only frames."""
        world_transforms = rig.resolve_world_transforms()
        sorted_transforms = sorted(world_transforms.values(), key=lambda t: t.z_index)

        face_ids = face_part_ids or set()
        body_transforms = [t for t in sorted_transforms if t.part_id not in face_ids]
        face_transforms = [t for t in sorted_transforms if t.part_id in face_ids]

        # Key for the composite beneath the face
        bg_key = (background_image.resolve() if background_image else None, background_color)
        body_state = tuple(
            (
                t.part_id,
                round(t.canvas_x, 2),
                round(t.canvas_y, 2),
                round(t.accum_rotation_deg, 2),
                round(t.accum_scale, 3),
                round(t.accum_opacity, 2),
                t.z_index,
                str(t.image_path.resolve()),
            )
            for t in body_transforms
        )
        composite_key = (bg_key, body_state)

        # Check body composite cache
        if face_ids and self._body_composite_key == composite_key and self._cached_body_pixmap is not None:
            # CACHE HIT: blit composite in ~4ms!
            self._surface.writePixels(self._cached_body_pixmap, 0, 0)
            self._cache_hits += 1
        else:
            # CACHE MISS: restore background and draw body
            self._cache_misses += 1
            bg_pixmap = self._prepare_background(background_image, background_color)
            self._surface.writePixels(bg_pixmap, 0, 0)

            canvas = self._surface.getCanvas()
            for t in body_transforms:
                self._draw_world_transform(canvas, t)

            # If face layers exist, save current composite to cache
            if face_ids:
                body_pix = skia.Pixmap()
                self._surface.peekPixels(body_pix)
                self._cached_body_bytes = bytes(body_pix)
                self._cached_body_pixmap = skia.Pixmap(self._image_info, self._cached_body_bytes, self.width * 4)
                self._body_composite_key = composite_key

        # Draw face overlays / parts on top
        canvas = self._surface.getCanvas()
        for t in face_transforms:
            self._draw_world_transform(canvas, t)

        snapshot = self._surface.makeImageSnapshot()
        return snapshot.tobytes()

    def _draw_world_transform(self, canvas: skia.Canvas, t: WorldTransform) -> None:
        """Helper to draw a single resolved world transform."""
        img = self.get_or_load_image(t.image_path)
        ox, oy = t.origin
        canvas.save()
        try:
            canvas.translate(t.canvas_x, t.canvas_y)
            if t.accum_rotation_deg != 0.0:
                canvas.rotate(t.accum_rotation_deg)
            if t.accum_scale != 1.0:
                canvas.scale(t.accum_scale, t.accum_scale)

            paint = skia.Paint()
            paint.setAntiAlias(True)
            if t.accum_opacity < 1.0:
                paint.setAlphaf(max(0.0, min(1.0, t.accum_opacity)))

            canvas.drawImage(img, -ox, -oy, skia.SamplingOptions(), paint)
        finally:
            canvas.restore()

    def clear_cache(self) -> None:
        """Clear cached image textures and composite pixmaps."""
        self._image_cache.clear()
        self._cached_bg_bytes = None
        self._cached_bg_pixmap = None
        self._cached_bg_surface = None
        self._bg_cache_key = None
        self._cached_body_bytes = None
        self._cached_body_pixmap = None
        self._body_composite_key = None
        self._cache_hits = 0
        self._cache_misses = 0

    def close(self) -> None:
        """Release Skia resources."""
        self.clear_cache()
        self._surface = None
