"""Hierarchical character rig and transform propagation for 2D cutout animation per TechSpec Section 5."""

from dataclasses import dataclass, replace
import math
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import skia


class RigValidationError(ValueError):
    """Raised when character rig hierarchy, transforms, or asset paths are invalid."""


@dataclass(frozen=True)
class CharacterPart:
    """Represents a discrete cutout body part attached to a kinematic hierarchy."""

    id: str
    image_path: Path
    parent_id: Optional[str] = None
    origin: Tuple[float, float] = (0.0, 0.0)  # Pivot inside the part image
    attach: Tuple[float, float] = (0.0, 0.0)  # Offset from parent pivot (or canvas pos if root)
    rotation_deg: float = 0.0  # Local rotation relative to parent
    scale: float = 1.0  # Local uniform scale
    opacity: float = 1.0  # Local opacity (0.0 .. 1.0)
    z_index: int = 0  # Draw order (lower drawn first)


@dataclass(frozen=True)
class WorldTransform:
    """Resolved world/canvas transform after propagating through parent hierarchy."""

    part_id: str
    canvas_x: float
    canvas_y: float
    accum_rotation_deg: float
    accum_scale: float
    accum_opacity: float
    z_index: int
    origin: Tuple[float, float]
    image_path: Path


class CharacterRig:
    """Manages character parts hierarchy, validation, and forward kinematics."""

    def __init__(self, rig_id: str, parts: Dict[str, CharacterPart]):
        self.rig_id = rig_id
        self.parts = dict(parts)
        self._topological_order: List[str] = []
        self._validate_and_sort()

    def _validate_and_sort(self) -> None:
        """Validate hierarchy acyclicity, asset paths, origins, and compute topological order."""
        if not self.parts:
            raise RigValidationError(f"Rig '{self.rig_id}' must contain at least one part.")

        for pid, part in self.parts.items():
            if pid != part.id:
                raise RigValidationError(f"Part key '{pid}' does not match part.id '{part.id}'")

            # Numeric validation
            if not (math.isfinite(part.rotation_deg) and math.isfinite(part.scale) and math.isfinite(part.opacity)):
                raise RigValidationError(f"Part '{pid}' has non-finite transform values")

            if part.scale <= 0.0:
                raise RigValidationError(f"Part '{pid}' scale must be positive, got {part.scale}")

            if not (0.0 <= part.opacity <= 1.0):
                raise RigValidationError(f"Part '{pid}' opacity must be in [0.0, 1.0], got {part.opacity}")

            if not (math.isfinite(part.attach[0]) and math.isfinite(part.attach[1])):
                raise RigValidationError(f"Part '{pid}' has non-finite attach coordinates")

            # File and image validation
            resolved_path = part.image_path.resolve()
            if not resolved_path.exists() or not resolved_path.is_file():
                raise RigValidationError(f"Part '{pid}' image not found: {resolved_path}")

            try:
                img = skia.Image.open(str(resolved_path))
                if not img or img.width() <= 0 or img.height() <= 0:
                    raise RigValidationError(f"Part '{pid}' has corrupt image: {resolved_path}")
            except Exception as exc:
                raise RigValidationError(f"Part '{pid}' failed to load image: {exc}") from exc

            ox, oy = part.origin
            if ox < 0 or ox > img.width() or oy < 0 or oy > img.height():
                raise RigValidationError(
                    f"Part '{pid}' pivot origin ({ox}, {oy}) is outside image bounds ({img.width()}x{img.height()})"
                )

            # Parent reference check
            if part.parent_id is not None:
                if part.parent_id not in self.parts:
                    raise RigValidationError(f"Part '{pid}' references unknown parent '{part.parent_id}'")
                if part.parent_id == pid:
                    raise RigValidationError(f"Part '{pid}' cannot be its own parent")

        # Cycle detection and topological sort using Kahn's algorithm
        in_degree: Dict[str, int] = {pid: 0 for pid in self.parts}
        children: Dict[str, List[str]] = {pid: [] for pid in self.parts}

        for pid, part in self.parts.items():
            if part.parent_id is not None:
                children[part.parent_id].append(pid)
                in_degree[pid] += 1

        queue = [pid for pid, deg in in_degree.items() if deg == 0]
        sorted_order: List[str] = []

        while queue:
            curr = queue.pop(0)
            sorted_order.append(curr)
            for child in children[curr]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if len(sorted_order) != len(self.parts):
            raise RigValidationError(f"Cycle detected in character rig hierarchy '{self.rig_id}'")

        self._topological_order = sorted_order

    def resolve_world_transforms(self) -> Dict[str, WorldTransform]:
        """Propagate forward kinematics from root to leaves and return resolved world transforms."""
        world_transforms: Dict[str, WorldTransform] = {}

        for pid in self._topological_order:
            part = self.parts[pid]

            if part.parent_id is None:
                # Root part: attach defines canvas position directly
                world_transforms[pid] = WorldTransform(
                    part_id=pid,
                    canvas_x=part.attach[0],
                    canvas_y=part.attach[1],
                    accum_rotation_deg=part.rotation_deg,
                    accum_scale=part.scale,
                    accum_opacity=part.opacity,
                    z_index=part.z_index,
                    origin=part.origin,
                    image_path=part.image_path,
                )
            else:
                parent_world = world_transforms[part.parent_id]
                # Rotate local attach by parent's accumulated rotation
                rad = math.radians(parent_world.accum_rotation_deg)
                cos_a = math.cos(rad)
                sin_a = math.sin(rad)

                ax, ay = part.attach
                world_x = parent_world.canvas_x + parent_world.accum_scale * (ax * cos_a - ay * sin_a)
                world_y = parent_world.canvas_y + parent_world.accum_scale * (ax * sin_a + ay * cos_a)

                world_transforms[pid] = WorldTransform(
                    part_id=pid,
                    canvas_x=world_x,
                    canvas_y=world_y,
                    accum_rotation_deg=parent_world.accum_rotation_deg + part.rotation_deg,
                    accum_scale=parent_world.accum_scale * part.scale,
                    accum_opacity=parent_world.accum_opacity * part.opacity,
                    z_index=part.z_index,
                    origin=part.origin,
                    image_path=part.image_path,
                )

        return world_transforms

    def with_part_transform(
        self,
        part_id: str,
        rotation_deg: Optional[float] = None,
        attach: Optional[Tuple[float, float]] = None,
        scale: Optional[float] = None,
        opacity: Optional[float] = None,
    ) -> "CharacterRig":
        """Return a new CharacterRig with modified local transform for a specific part."""
        if part_id not in self.parts:
            raise RigValidationError(f"Part '{part_id}' not found in rig '{self.rig_id}'")

        orig = self.parts[part_id]
        new_part = replace(
            orig,
            rotation_deg=orig.rotation_deg if rotation_deg is None else rotation_deg,
            attach=orig.attach if attach is None else attach,
            scale=orig.scale if scale is None else scale,
            opacity=orig.opacity if opacity is None else opacity,
        )
        updated_parts = dict(self.parts)
        updated_parts[part_id] = new_part
        return CharacterRig(self.rig_id, updated_parts)
