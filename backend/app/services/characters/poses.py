"""Pose definitions and smoothstep interpolation for character rigs per TechSpec Section 5."""

from dataclasses import dataclass, field
import math
from typing import Dict, Optional, Tuple
from backend.app.services.characters.rig import CharacterRig, RigValidationError


def smoothstep(t: float) -> float:
    """Standard Hermite smoothstep curve clamped between 0.0 and 1.0."""
    t_clamped = max(0.0, min(1.0, t))
    return t_clamped * t_clamped * (3.0 - 2.0 * t_clamped)


def interpolate_angle_deg(start_deg: float, end_deg: float, weight: float) -> float:
    """Shortest-path angular interpolation in degrees."""
    delta = (end_deg - start_deg + 180.0) % 360.0 - 180.0
    return start_deg + delta * weight


@dataclass(frozen=True)
class PartPose:
    """Target transform values for an individual character part in a pose."""

    rotation_deg: Optional[float] = None
    attach: Optional[Tuple[float, float]] = None
    scale: Optional[float] = None
    opacity: Optional[float] = None


@dataclass(frozen=True)
class Pose:
    """Named collection of part transforms."""

    name: str
    parts: Dict[str, PartPose] = field(default_factory=dict)


class PoseInterpolator:
    """Performs deterministic pose transitions with smoothstep easing."""

    @staticmethod
    def interpolate(
        rig: CharacterRig,
        pose_a: Pose,
        pose_b: Pose,
        elapsed_s: float,
        transition_duration_s: float = 0.5,
    ) -> CharacterRig:
        """Blend between pose_a and pose_b across transition_duration_s."""
        if transition_duration_s <= 0.0:
            weight = 1.0
        else:
            norm_t = elapsed_s / transition_duration_s
            weight = smoothstep(norm_t)

        current_rig = rig
        # Blend each part defined in the rig
        for pid, part in rig.parts.items():
            pose_a_part = pose_a.parts.get(pid)
            pose_b_part = pose_b.parts.get(pid)

            # 1. Rotation
            rot_a = pose_a_part.rotation_deg if (pose_a_part and pose_a_part.rotation_deg is not None) else part.rotation_deg
            rot_b = pose_b_part.rotation_deg if (pose_b_part and pose_b_part.rotation_deg is not None) else part.rotation_deg
            new_rot = interpolate_angle_deg(rot_a, rot_b, weight)

            # 2. Attach position
            att_a = pose_a_part.attach if (pose_a_part and pose_a_part.attach is not None) else part.attach
            att_b = pose_b_part.attach if (pose_b_part and pose_b_part.attach is not None) else part.attach
            new_att = (
                att_a[0] + (att_b[0] - att_a[0]) * weight,
                att_a[1] + (att_b[1] - att_a[1]) * weight,
            )

            # 3. Scale
            scale_a = pose_a_part.scale if (pose_a_part and pose_a_part.scale is not None) else part.scale
            scale_b = pose_b_part.scale if (pose_b_part and pose_b_part.scale is not None) else part.scale
            new_scale = scale_a + (scale_b - scale_a) * weight

            # 4. Opacity
            op_a = pose_a_part.opacity if (pose_a_part and pose_a_part.opacity is not None) else part.opacity
            op_b = pose_b_part.opacity if (pose_b_part and pose_b_part.opacity is not None) else part.opacity
            new_opacity = op_a + (op_b - op_a) * weight

            current_rig = current_rig.with_part_transform(
                part_id=pid,
                rotation_deg=new_rot,
                attach=new_att,
                scale=new_scale,
                opacity=new_opacity,
            )

        return current_rig
