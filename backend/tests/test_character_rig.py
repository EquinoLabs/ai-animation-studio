"""Tests for hierarchical character rig, pose blending, and composite caching."""

import math
from pathlib import Path
import pytest

from backend.app.services.characters.poses import (
    PartPose,
    Pose,
    PoseInterpolator,
    interpolate_angle_deg,
    smoothstep,
)
from backend.app.services.characters.rig import (
    CharacterPart,
    CharacterRig,
    RigValidationError,
)
from backend.app.services.renderer.frame_renderer import FrameRenderer
from backend.tests.synthetic_assets import generate_synthetic_assets


@pytest.fixture(scope="module")
def synthetic_assets(tmp_path_factory):
    tmp_dir = tmp_path_factory.mktemp("rig_assets")
    return generate_synthetic_assets(tmp_dir)


def test_smoothstep_curve_and_angle_interpolation():
    """Verify smoothstep mathematical properties and shortest-path angle wrapping."""
    assert smoothstep(0.0) == 0.0
    assert smoothstep(0.5) == 0.5
    assert smoothstep(1.0) == 1.0
    assert smoothstep(-0.5) == 0.0  # Clamped
    assert smoothstep(1.5) == 1.0  # Clamped

    # Shortest path angle wrapping
    # 10 deg to 350 deg should interpolate via -20 deg delta, crossing 0 deg
    mid = interpolate_angle_deg(10.0, 350.0, 0.5)
    assert math.isclose(mid % 360.0, 0.0, abs_tol=1e-5)

    # 350 deg to 10 deg midpoint
    mid2 = interpolate_angle_deg(350.0, 10.0, 0.5)
    assert math.isclose(mid2 % 360.0, 0.0, abs_tol=1e-5)


def test_rig_validation_and_cycle_detection(synthetic_assets, tmp_path):
    """Verify validation rules: missing parent, cycle, missing file, bad origin."""
    body_img = synthetic_assets["body"]
    head_img = synthetic_assets["head"]

    # 1. Valid rig passes
    parts = {
        "body": CharacterPart(id="body", image_path=body_img, attach=(540, 960), origin=(175, 50)),
        "head": CharacterPart(id="head", image_path=head_img, parent_id="body", attach=(0, -200), origin=(130, 230)),
    }
    rig = CharacterRig("test_rig", parts)
    assert rig.rig_id == "test_rig"

    # 2. Missing parent
    bad_parent = {
        "head": CharacterPart(id="head", image_path=head_img, parent_id="nonexistent"),
    }
    with pytest.raises(RigValidationError, match="unknown parent"):
        CharacterRig("bad_parent", bad_parent)

    # 3. Cycle detection (A -> B -> A)
    cycle = {
        "part_a": CharacterPart(id="part_a", image_path=body_img, parent_id="part_b"),
        "part_b": CharacterPart(id="part_b", image_path=head_img, parent_id="part_a"),
    }
    with pytest.raises(RigValidationError, match="Cycle detected"):
        CharacterRig("cycle_rig", cycle)

    # 4. Out-of-bounds origin
    bad_origin = {
        "body": CharacterPart(id="body", image_path=body_img, origin=(9999, 9999)),
    }
    with pytest.raises(RigValidationError, match="outside image bounds"):
        CharacterRig("bad_origin", bad_origin)


def test_forward_kinematics_transform_propagation(synthetic_assets):
    """Verify forward kinematics: parent translation and rotation correctly transform child canvas coordinates."""
    parts = {
        "torso": CharacterPart(
            id="torso",
            image_path=synthetic_assets["body"],
            attach=(500.0, 1000.0),
            origin=(175.0, 50.0),
            rotation_deg=90.0,  # Rotated 90 deg clockwise
            scale=1.0,
        ),
        "head": CharacterPart(
            id="head",
            image_path=synthetic_assets["head"],
            parent_id="torso",
            attach=(100.0, 0.0),  # 100px along parent unrotated X
            origin=(130.0, 230.0),
            rotation_deg=15.0,
            scale=1.0,
        ),
    }
    rig = CharacterRig("fk_rig", parts)
    world = rig.resolve_world_transforms()

    torso_world = world["torso"]
    assert torso_world.canvas_x == 500.0
    assert torso_world.canvas_y == 1000.0
    assert torso_world.accum_rotation_deg == 90.0

    head_world = world["head"]
    # With parent rotated 90 deg clockwise:
    # unrotated vector (100, 0) rotated by 90 deg becomes (0, 100)
    assert math.isclose(head_world.canvas_x, 500.0, abs_tol=1e-3)
    assert math.isclose(head_world.canvas_y, 1100.0, abs_tol=1e-3)
    assert head_world.accum_rotation_deg == 105.0  # 90 + 15


def test_pose_interpolation_smoothstep(synthetic_assets):
    """Verify pose blending across a 0.5s transition at t=0.0, t=0.25, and t=0.5."""
    parts = {
        "body": CharacterPart(id="body", image_path=synthetic_assets["body"], attach=(540.0, 960.0), rotation_deg=0.0),
        "head": CharacterPart(id="head", image_path=synthetic_assets["head"], parent_id="body", rotation_deg=0.0),
    }
    rig = CharacterRig("anim_rig", parts)

    pose_idle = Pose("idle", parts={"body": PartPose(rotation_deg=0.0), "head": PartPose(rotation_deg=0.0)})
    pose_point = Pose("point", parts={"body": PartPose(rotation_deg=20.0), "head": PartPose(rotation_deg=-10.0)})

    # t = 0.0 -> exact idle
    rig_0 = PoseInterpolator.interpolate(rig, pose_idle, pose_point, elapsed_s=0.0, transition_duration_s=0.5)
    assert rig_0.parts["body"].rotation_deg == 0.0
    assert rig_0.parts["head"].rotation_deg == 0.0

    # t = 0.25 -> midpoint (weight = smoothstep(0.5) = 0.5)
    rig_mid = PoseInterpolator.interpolate(rig, pose_idle, pose_point, elapsed_s=0.25, transition_duration_s=0.5)
    assert math.isclose(rig_mid.parts["body"].rotation_deg, 10.0, abs_tol=1e-3)
    assert math.isclose(rig_mid.parts["head"].rotation_deg, -5.0, abs_tol=1e-3)

    # t = 0.5 -> exact point
    rig_end = PoseInterpolator.interpolate(rig, pose_idle, pose_point, elapsed_s=0.5, transition_duration_s=0.5)
    assert math.isclose(rig_end.parts["body"].rotation_deg, 20.0, abs_tol=1e-3)
    assert math.isclose(rig_end.parts["head"].rotation_deg, -10.0, abs_tol=1e-3)


def test_composite_caching_and_invalidation(synthetic_assets):
    """Verify cache hits for face-only frames and cache invalidation when body transforms change."""
    renderer = FrameRenderer(width=1080, height=1920, fps=24)
    parts = {
        "body": CharacterPart(id="body", image_path=synthetic_assets["body"], attach=(540.0, 960.0), origin=(175, 50)),
        "head": CharacterPart(id="head", image_path=synthetic_assets["head"], parent_id="body", attach=(0, -200), origin=(130, 230)),
    }
    rig = CharacterRig("cache_rig", parts)

    try:
        # Frame 1: Initial render -> Cache miss
        renderer.render_rig(rig, background_image=synthetic_assets["background"], face_part_ids={"head"})
        stats1 = renderer.cache_stats
        assert stats1["misses"] == 1
        assert stats1["hits"] == 0

        # Frame 2: Only head rotates (face-only update, body unchanged) -> Cache HIT!
        rig_face_move = rig.with_part_transform("head", rotation_deg=5.0)
        renderer.render_rig(rig_face_move, background_image=synthetic_assets["background"], face_part_ids={"head"})
        stats2 = renderer.cache_stats
        assert stats2["hits"] == 1
        assert stats2["misses"] == 1

        # Frame 3: Body moves (pose transition) -> Cache INVALIDATION (miss)
        rig_body_move = rig.with_part_transform("body", rotation_deg=10.0)
        renderer.render_rig(rig_body_move, background_image=synthetic_assets["background"], face_part_ids={"head"})
        stats3 = renderer.cache_stats
        assert stats3["misses"] == 2
        assert stats3["hits"] == 1

        # Frame 4: Second frame with new body state and moving head -> Cache HIT on new body state!
        rig_body_static_head_move = rig_body_move.with_part_transform("head", rotation_deg=-8.0)
        renderer.render_rig(rig_body_static_head_move, background_image=synthetic_assets["background"], face_part_ids={"head"})
        stats4 = renderer.cache_stats
        assert stats4["hits"] == 2
        assert stats4["misses"] == 2
    finally:
        renderer.close()
