"""VR / mocap retargeting: bake a Unity-named source armature's motion onto
the XML2 / MUA Bip01 armature, producing a Blender Action ready for IGB export.

The user's VR-in-Blender addon drives a *Unity-humanoid* armature (Hips,
LeftUpperArm, ...). This module retargets that performance onto the game's
Bip01 skeleton so it can be exported with :func:`vmc_bridge.export_action_as_igb`.

Pipeline
--------
1. **Map** source bones -> Bip01 targets using the addon's CATS-grade fuzzy
   matcher (:func:`rig_converter._lookup_bone`) so it works regardless of the
   source naming scheme (Mecanim / VRM / Mixamo / custom).
2. **Retarget + bake**: for every frame, read the source pose, compute each
   target bone's world-space rotation via the world-delta method
   ``T_world = (S_world @ S_rest^-1) @ T_rest`` and FK-propagate it down the
   Bip01 hierarchy analytically (no depsgraph round-trip per bone). Hip
   translation is optionally carried on the Pelvis, scaled to the character's
   proportions.
3. The resulting keyframed Action feeds the existing from-scratch IGB anim
   builder.

The retarget is rotation-based, so it is correct across differing rest poses
(Unity T-pose vs 3ds Max biped) — a naive Copy-Rotation would not be.
"""

import logging

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bone mapping (source -> Bip01)
# ---------------------------------------------------------------------------

def build_retarget_map(src_arm, tgt_arm, game='xml2'):
    """Map source-armature bones to Bip01 target bones.

    Uses the fuzzy CATS-ported matcher so Unity / VRM / Mixamo / custom names
    all resolve. When several source bones claim the same target, the one with
    the strongest (lowest-priority-number) alias wins.

    Args:
        src_arm: The source (VR / Unity) armature object.
        tgt_arm: The target Bip01 (XML2 / MUA) armature object.
        game: 'xml2' or 'mua' (selects FX-bone aliases for MUA).

    Returns:
        dict {target_bone_name: source_bone_name}. Only pairs where the target
        bone actually exists on ``tgt_arm`` are kept.
    """
    from .rig_converter import _lookup_bone_priority

    target_game = 'MUA' if str(game).lower().startswith('mua') else 'XML2'
    tgt_bone_names = {b.name for b in tgt_arm.data.bones}

    # target_name -> (source_name, priority) keep best priority
    best = {}
    for sb in src_arm.data.bones:
        hit = _lookup_bone_priority(sb.name, target_game=target_game)
        if not hit:
            continue
        tgt_name, prio = hit
        if tgt_name not in tgt_bone_names:
            continue
        cur = best.get(tgt_name)
        if cur is None or prio < cur[1]:
            best[tgt_name] = (sb.name, prio)

    return {t: v[0] for t, v in best.items()}


def _hierarchy_order(armature_obj):
    """Return armature bones in parents-before-children order."""
    ordered = []

    def walk(bone):
        ordered.append(bone)
        for child in bone.children:
            walk(child)

    for bone in armature_obj.data.bones:
        if bone.parent is None:
            walk(bone)
    return ordered


def _find_pelvis_target(bone_map, tgt_arm):
    """Identify the target Pelvis / root-of-motion bone in the map."""
    # Common Bip01 hip names produced by the rig converter.
    for cand in ("Bip01 Pelvis", "Bip01 Spine", "Pelvis"):
        if cand in bone_map:
            return cand
    # Fall back to the shallowest mapped bone (closest to the root).
    depth = {b.name: _depth(b) for b in tgt_arm.data.bones}
    mapped = [t for t in bone_map if t in depth]
    if not mapped:
        return None
    return min(mapped, key=lambda n: depth[n])


def _depth(bone):
    d = 0
    p = bone.parent
    while p is not None:
        d += 1
        p = p.parent
    return d


# ---------------------------------------------------------------------------
# Retarget + bake
# ---------------------------------------------------------------------------

def retarget_and_bake(src_arm, tgt_arm, frame_start, frame_end,
                      game='xml2', action_name="VR_Take", root_motion=True,
                      step=1, hip_scale=None, bone_map=None,
                      follow_translation=False):
    """Retarget ``src_arm``'s animation onto ``tgt_arm`` and bake a new Action.

    Args:
        src_arm: Source (VR / Unity) armature, animated over the frame range.
        tgt_arm: Target Bip01 armature to receive the baked motion.
        frame_start, frame_end: Inclusive scene-frame range to sample.
        game: 'xml2' or 'mua'.
        action_name: Name for the baked Action.
        root_motion: If True, carry (scaled) hip translation on the Pelvis.
        step: Frame step (1 = every frame).
        hip_scale: Override the auto hip-height scale (float) or None to auto.
        bone_map: Optional explicit {target_bone: source_bone} map. Default
            builds the fuzzy CATS map (VR rigs). Pass an identity map when
            both armatures already share Bip01 names (game-anim → converted
            rig import).
        follow_translation: If True, carry each source bone's per-frame
            translation deviation from its OWN rest FK onto the target
            (scaled). Game anims animate joint translations subtly (feet
            drift ~5 units without this); VR mocap should leave it off so
            target bone lengths stay rigid.

    Returns:
        (action, n_mapped, n_frames) on success, or (None, 0, 0) on failure.
    """
    import bpy
    from mathutils import Matrix, Vector

    if bone_map is None:
        bone_map = build_retarget_map(src_arm, tgt_arm, game=game)
    if len(bone_map) < 3:
        _log.error("Retarget map too small (%d bones) — source/target names "
                   "don't correspond", len(bone_map))
        return None, 0, 0

    src_pose = src_arm.pose.bones
    tgt_pose = tgt_arm.pose.bones
    src_data = src_arm.data.bones
    tgt_data = tgt_arm.data.bones

    src_mw = src_arm.matrix_world.copy()
    tgt_mw = tgt_arm.matrix_world.copy()
    src_mw3 = src_mw.to_3x3()
    tgt_mw3 = tgt_mw.to_3x3()
    tgt_mw3_inv = tgt_mw3.inverted()

    # Rest data (constant across frames).
    src_rest_w3 = {}   # source rest world rotation (3x3)
    for tgt_name, src_name in bone_map.items():
        sb = src_data.get(src_name)
        if sb:
            src_rest_w3[src_name] = (src_mw3 @ sb.matrix_local.to_3x3())

    tgt_rest_w3 = {b.name: (tgt_mw3 @ b.matrix_local.to_3x3())
                   for b in tgt_data}
    # rest_offset[name] = parent.matrix_local^-1 @ bone.matrix_local (armature
    # space bone-relative-to-parent at rest); for root parent = Identity.
    rest_offset = {}
    for b in tgt_data:
        if b.parent:
            rest_offset[b.name] = b.parent.matrix_local.inverted() @ b.matrix_local
        else:
            rest_offset[b.name] = b.matrix_local.copy()

    # Source rest offsets (for follow_translation).
    src_rest_offset = {}
    if follow_translation:
        for tgt_name, src_name in bone_map.items():
            sb = src_data.get(src_name)
            if sb is None:
                continue
            if sb.parent:
                src_rest_offset[src_name] = (
                    sb.parent.matrix_local.inverted() @ sb.matrix_local)
            else:
                src_rest_offset[src_name] = sb.matrix_local.copy()

    tgt_order = _hierarchy_order(tgt_arm)

    # --- Root-motion setup ---
    pelvis_name = _find_pelvis_target(bone_map, tgt_arm) if root_motion else None
    src_hips_name = bone_map.get(pelvis_name) if pelvis_name else None
    src_hips_rest_w = None
    scale = 1.0
    if root_motion and src_hips_name:
        shb = src_data.get(src_hips_name)
        pvb = tgt_data.get(pelvis_name)
        if shb and pvb:
            src_hips_rest_w = (src_mw @ shb.matrix_local).translation.copy()
            tgt_pelvis_rest_w = (tgt_mw @ pvb.matrix_local).translation
            if hip_scale is not None:
                scale = float(hip_scale)
            else:
                sh = abs(src_hips_rest_w.z)
                scale = (abs(tgt_pelvis_rest_w.z) / sh) if sh > 1e-4 else 1.0
        else:
            pelvis_name = None

    # Ensure target pose bones are quaternion (exporter samples quaternions).
    for pb in tgt_pose:
        pb.rotation_mode = 'QUATERNION'

    # --- Prepare a fresh Action on the target ---
    if tgt_arm.animation_data is None:
        tgt_arm.animation_data_create()
    action = bpy.data.actions.new(action_name)
    action["igb_anim_name"] = action_name
    tgt_arm.animation_data.action = action

    scene = bpy.context.scene
    saved_frame = scene.frame_current

    frames = list(range(int(frame_start), int(frame_end) + 1, max(1, int(step))))
    if not frames:
        frames = [int(frame_start)]

    for f in frames:
        scene.frame_set(f)

        # Precompute source world-delta rotations for this frame.
        delta_w3 = {}
        trans_delta_w = {}  # follow_translation: world head deviation vs rest FK
        for tgt_name, src_name in bone_map.items():
            spb = src_pose.get(src_name)
            rest = src_rest_w3.get(src_name)
            if spb is None or rest is None:
                continue
            s_pose_w3 = (src_mw3 @ spb.matrix.to_3x3())
            delta_w3[tgt_name] = s_pose_w3 @ rest.inverted()

            if follow_translation and spb.parent is not None:
                sro = src_rest_offset.get(src_name)
                if sro is not None:
                    expected = (spb.parent.matrix @ sro).translation
                    d_arm = spb.matrix.translation - expected
                    if d_arm.length_squared > 1e-8:
                        trans_delta_w[tgt_name] = src_mw3 @ d_arm

        # Hip translation (world), scaled to armature space.
        disp_arm = None
        if pelvis_name and src_hips_name and src_hips_rest_w is not None:
            shp = src_pose.get(src_hips_name)
            if shp is not None:
                src_hips_pose_w = (src_mw @ shp.matrix).translation
                disp_w = (src_hips_pose_w - src_hips_rest_w) * scale
                disp_arm = tgt_mw3_inv @ disp_w

        # FK-propagate down the target hierarchy (analytic).
        computed = {}  # bone_name -> armature-space 4x4 pose matrix
        for bone in tgt_order:
            name = bone.name
            ro = rest_offset[name]
            parent_pose = (computed[bone.parent.name]
                           if bone.parent else Matrix.Identity(4))

            if name in delta_w3:
                # Desired armature-space rotation for this bone.
                desired_w3 = delta_w3[name] @ tgt_rest_w3[name]
                desired_arm3 = tgt_mw3_inv @ desired_w3
                head = (parent_pose @ ro).translation
                if name == pelvis_name and disp_arm is not None:
                    head = head + disp_arm
                if name in trans_delta_w:
                    head = head + tgt_mw3_inv @ (trans_delta_w[name] * scale)
                pose_arm = Matrix.Translation(head) @ desired_arm3.to_4x4()
            else:
                # Unmapped bone: rigidly follow parent at its rest offset.
                pose_arm = parent_pose @ ro

            computed[name] = pose_arm

            if name in bone_map:
                basis = ro.inverted() @ parent_pose.inverted() @ pose_arm
                pb = tgt_pose.get(name)
                if pb is not None:
                    pb.matrix_basis = basis
                    pb.keyframe_insert("rotation_quaternion", frame=f,
                                       group=name)
                    if ((name == pelvis_name and root_motion)
                            or name in trans_delta_w):
                        pb.keyframe_insert("location", frame=f, group=name)

    scene.frame_set(saved_frame)

    # Tag frame range for the exporter / UI.
    action.frame_start = frames[0]
    action.frame_end = frames[-1]

    return action, len(bone_map), len(frames)
