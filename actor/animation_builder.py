"""Build Blender Actions from ParsedAnimation data.

Creates animation Actions with quaternion rotation and location keyframes
for each bone track.

GENERAL ROTATION FORMULA:
Alchemy animation quaternions are ABSOLUTE local rotations in parent-bone
space. Alchemy uses the CONJUGATE quaternion convention vs Blender:

    desired_local_rot = conjugate(alchemy_anim_q)

Blender's pose.rotation_quaternion is a DELTA from the bone's rest pose:

    final_local_rot = rest_local_rot @ pose_q

Therefore the general formula is:

    pose_q = rest_q^{-1} @ conjugate(anim_q)

Where rest_q is the bone's actual rest-local quaternion read from the
armature data (NOT assumed to be conjugate(bind_q)).

This works regardless of how the armature was built (quaternion-oriented
bones, child-pointing bones, etc.). When rest_q == conjugate(bind_q), it
degrades to the old formula: pose_q = bind_q @ anim_q^{-1}.

For translations: Blender's pose.location is in the bone's rest-local
coordinate system, so we use the rest rotation inverse:

    pose_loc = rest_rot_inv @ (anim_trans - bind_trans)
"""

from typing import Dict, List, Optional, Tuple


def _compute_rest_local_data(armature_obj):
    """Precompute rest-local rotation data for each bone.

    Returns:
        Dict mapping bone_name -> (rest_rot_inv, rest_q):
        - rest_rot_inv: inverted 3x3 rotation matrix (for location conversion)
        - rest_q: rest-local Quaternion (for rotation conversion)
    """
    result = {}
    for bone in armature_obj.data.bones:
        if bone.parent:
            local_rest_mat = bone.parent.matrix_local.inverted() @ bone.matrix_local
        else:
            local_rest_mat = bone.matrix_local.copy()

        rest_rot_inv = local_rest_mat.to_3x3().inverted()
        rest_q = local_rest_mat.to_quaternion()
        rest_trans = local_rest_mat.to_translation()
        result[bone.name] = (rest_rot_inv, rest_q, rest_trans)

    return result


def _world_parent_rot(armature_obj):
    """Map bone_name -> world-rest rotation Quaternion of its PARENT bone.

    For a parentless (root) bone the parent frame is world identity. Used to
    rebase a separate-file animation's rotations into the actor's parent frame
    (fixes sideways playback when the actor's root chain differs from the
    anim's, e.g. a skin-built actor has a +90deg-X "Unnamed bone" root above
    Bip01 that the anim file does not).
    """
    from mathutils import Quaternion
    out = {}
    for bone in armature_obj.data.bones:
        out[bone.name] = (bone.parent.matrix_local.to_quaternion()
                          if bone.parent else Quaternion((1.0, 0.0, 0.0, 0.0)))
    return out


def build_action(armature_obj, parsed_animation, bind_pose=None, fps=None,
                  bone_remap=None, target_bind_pose=None, anim_armature=None,
                  rebase_unanimated_parents=False, relative_translation=False,
                  root_motion_mode='full'):
    """Create a Blender Action from a ParsedAnimation.

    Args:
        armature_obj: The Blender armature object.
        parsed_animation: ParsedAnimation instance.
        bind_pose: Dict mapping bone_name -> (quat_wxyz, trans_xyz) from
                   afakeanim frame 0 of the SOURCE animation file. Used
                   as fallback for translation delta conversion.
        fps: Frames per second for time conversion. If None, uses the
             Blender scene's render FPS (respects fps_base).
        bone_remap: Optional dict mapping source bone names to target bone
                    names. Used for cross-game animation import (e.g.,
                    MUA -> XML2). If a source bone name is found in this
                    dict, the mapped name is used for armature lookup.
        target_bind_pose: Dict mapping bone_name -> (quat_wxyz, trans_xyz)
                          from the TARGET armature's original animation file.
                          When importing cross-character animations, this
                          keeps bone positions relative to the target
                          character's skeleton. Falls back to bind_pose if
                          not provided.
        anim_armature: Optional temporary armature built from the SEPARATE
                       anim file's OWN skeleton. When provided, each bone's
                       rotation is rebased from the anim skeleton's parent
                       frame into the actor's parent frame (parent-rest delta),
                       fixing sideways playback when the root chains differ.
                       When None (e.g. an actor's own embedded anims) behavior
                       is unchanged.
        relative_translation: When True, each track's translation is applied as
                       a delta from its OWN FRAME-0 position (instead of an
                       absolute position). This is the mocap/briefing case:
                       body bones carry ZERO translation (rotation-only, delta 0
                       either way) but FACE/finger bones carry their full
                       ABSOLUTE parent-local position — applying that absolutely
                       displaces them by several units and tears the face apart.
                       Frame-0 baseline makes both correct. Ignored for a bone
                       that already has an explicit bind_trans. EXCEPTION: the
                       REBASED root bone (see rebase_unanimated_parents) uses
                       ABSOLUTE semantics with the bone's REST as the baseline —
                       its track carries the character's true root position
                       (e.g. Prof X seated at Z~19 on a model whose rest is
                       standing Z~38), which a frame-0 baseline would discard.
        root_motion_mode: 'full' (default) keeps the rebased root's whole
                       translation (the character travels as authored);
                       'vertical' zeroes the HORIZONTAL travel in armature space
                       but keeps the VERTICAL position — the character stays on
                       its staging mark yet still sits/crouches at the height
                       the performance dictates. Only affects the rebased root.
        rebase_unanimated_parents: When True (requires anim_armature), the
                       parent-rest delta ALSO fires for a bone whose parent is
                       PRESENT in the anim skeleton but carries no track (i.e.
                       an unanimated root such as Bone_000). This is the mocap /
                       briefing case: the model file and the zone performance
                       file store the SAME-named skeleton with DIFFERENT rest
                       orientations, so the unanimated root must be rebased from
                       the model's rest onto the performance's authoring rest or
                       the whole body plays lying-down. Default False keeps the
                       existing behavior (delta only for structurally-absent
                       parents), so a character playing its OWN moveset — whose
                       roots are either animated or identically-oriented — is a
                       no-op and never regresses.

    Returns:
        The created bpy.types.Action, or None on failure.
    """
    import bpy
    from mathutils import Quaternion, Vector

    # Use scene FPS if not explicitly provided
    if fps is None:
        fps = bpy.context.scene.render.fps / bpy.context.scene.render.fps_base

    action_name = parsed_animation.name or "Action"
    action = bpy.data.actions.new(name=action_name)

    # Identity-hold clips (all-identity quats, zero translations — e.g. XML2
    # lunge_loop) carry NO pose of their own: in-engine they hold whatever
    # pose is already playing. Pushing them through the retarget math
    # scrambles the rig (pose_q = rest⁻¹·bind·rest per bone). The honest
    # standalone playback is a REST-POSE hold: key pose identity (which IS
    # the rest pose in Blender) on every tracked bone for the clip duration.
    if getattr(parsed_animation, 'identity_hold', False):
        if fps is None:
            fps = (bpy.context.scene.render.fps
                   / bpy.context.scene.render.fps_base)
        end_frame = max(parsed_animation.duration_ms / 1000.0 * fps, 1.0)
        n_tracks = 0
        for track in parsed_animation.tracks:
            bone_name = track.bone_name
            if bone_remap and bone_name in bone_remap:
                bone_name = bone_remap[bone_name]
            if not bone_name or armature_obj.pose.bones.get(bone_name) is None:
                continue
            dp = f'pose.bones["{bone_name}"].rotation_quaternion'
            for idx, val in enumerate((1.0, 0.0, 0.0, 0.0)):
                fc = action.fcurves.new(data_path=dp, index=idx)
                fc.keyframe_points.add(2)
                fc.keyframe_points[0].co = (0.0, val)
                fc.keyframe_points[1].co = (end_frame, val)
                fc.update()
            n_tracks += 1
        action["igb_identity_hold"] = True
        action["igb_duration_ms"] = parsed_animation.duration_ms
        action["igb_track_count"] = n_tracks
        action["igb_anim_name"] = parsed_animation.name
        return action

    # Ensure animation data exists
    if armature_obj.animation_data is None:
        armature_obj.animation_data_create()

    # Precompute bind-pose translation maps.
    # For cross-character animation, prefer target_bind_pose so bone
    # positions are relative to the TARGET character's skeleton.
    bind_trans_map = {}
    bind_quat_map = {}
    if bind_pose:
        for bone_name, (quat_wxyz, trans_xyz) in bind_pose.items():
            bind_trans_map[bone_name] = Vector(trans_xyz)
            bind_quat_map[bone_name] = quat_wxyz

    target_trans_map = {}
    if target_bind_pose:
        for bone_name, (quat_wxyz, trans_xyz) in target_bind_pose.items():
            target_trans_map[bone_name] = Vector(trans_xyz)

    # Precompute rest-local data (quaternion + rotation inverse)
    rest_data = _compute_rest_local_data(armature_obj)

    # Parent-rest delta: when applying a SEPARATE anim file, rebase each bone's
    # rotation from the anim skeleton's parent frame onto the actor's parent
    # frame. delta = wpr_actor[bone]^-1 @ wpr_anim[bone]; identity (no-op) when
    # the parent frames already match (same skeleton / own anims).
    wpr_actor = _world_parent_rot(armature_obj) if anim_armature else None
    wpr_anim = _world_parent_rot(anim_armature) if anim_armature else None
    anim_bone_names = (set(b.name for b in anim_armature.data.bones)
                       if anim_armature else None)
    # Target-space set of bones that carry a track (used to detect a bone whose
    # PARENT is unanimated — the root that needs rebasing for mocap files).
    animated_bones = set()
    if rebase_unanimated_parents:
        for _t in parsed_animation.tracks:
            if _t.bone_name:
                _tn = (bone_remap[_t.bone_name]
                       if (bone_remap and _t.bone_name in bone_remap)
                       else _t.bone_name)
                animated_bones.add(_tn)

    track_count = 0
    seen_bones = set()

    for track in parsed_animation.tracks:
        if not track.bone_name:
            continue

        # Skip duplicate tracks for the same bone (some anims have them)
        if track.bone_name in seen_bones:
            continue
        seen_bones.add(track.bone_name)

        # Apply bone name remapping if provided (cross-game import)
        bone_name = track.bone_name
        if bone_remap and bone_name in bone_remap:
            bone_name = bone_remap[bone_name]

        # Find the pose bone
        pb = armature_obj.pose.bones.get(bone_name)
        if pb is None:
            import logging
            logging.getLogger("igb_anim").debug(
                "Animation '%s': bone '%s' not found in armature, skipping track",
                parsed_animation.name, bone_name)
            continue

        if not track.keyframes:
            continue

        # Get rest-local data for this bone (use remapped name for armature lookup)
        rest_info = rest_data.get(bone_name)
        rest_rot_inv = rest_info[0] if rest_info else None
        rest_q = rest_info[1] if rest_info else None
        rest_trans = rest_info[2] if rest_info else None

        # Get bind-pose translation for this bone.
        # For cross-character animation, prefer target_bind_pose so bone
        # translations are computed relative to the TARGET character's
        # skeleton, not the source character's.
        bind_trans = None
        if target_trans_map:
            bind_trans = target_trans_map.get(bone_name)
            if bind_trans is None:
                bind_trans = target_trans_map.get(track.bone_name)
        if bind_trans is None:
            bind_trans = bind_trans_map.get(track.bone_name)
            if bind_trans is None and bone_name != track.bone_name:
                bind_trans = bind_trans_map.get(bone_name)

        # The anim's own bind-frame quaternion (keyed by SOURCE bone name) —
        # lets the rotation be applied as a delta from the anim's bind onto the
        # actor's rest, so a separate anim file doesn't flip/rotate the actor.
        anim_bind_q = bind_quat_map.get(track.bone_name)
        if anim_bind_q is None and bone_name != track.bone_name:
            anim_bind_q = bind_quat_map.get(bone_name)

        # Parent-rest delta for this bone. Applied ONLY when this bone's parent
        # in the ACTOR is absent from the anim skeleton — a genuine structural
        # extra-root (e.g. a skin-built actor's +90X "Unnamed bone" above
        # Bip01, which the anim file doesn't have). Skeletons that share the
        # same structure leave EVERY bone untouched, so anims that already play
        # correctly are never disturbed.
        parent_delta = None
        if wpr_actor is not None and anim_bone_names is not None:
            ab = armature_obj.data.bones.get(bone_name)
            actor_parent = ab.parent.name if (ab and ab.parent) else None
            # Fire the parent-rest rebase when the parent is a structural
            # extra-root (absent from the anim skeleton) OR — for mocap/briefing
            # files — present but UNANIMATED (so it keeps the model's rest,
            # which differs from the performance's authoring rest).
            parent_absent = (actor_parent is not None
                             and actor_parent not in anim_bone_names)
            # The bind-delta path (valid anim bind quat) ALREADY retargets the
            # rotation onto the actor's rest — adding the parent delta on top
            # would double-compensate (the historical XML2-idle regression).
            # Only rebase an unanimated parent when the bind is absent or
            # degenerate (< 0.5 magnitude, same guard as the keyframe writer),
            # e.g. MUA v8 menu movesets like 21_nickfury whose "Unnamed bone"
            # root rest differs 90° from the 2101 skin's (#45 tilt-back).
            bind_active = False
            if anim_bind_q is not None:
                from mathutils import Quaternion as _Q
                try:
                    bind_active = _Q(anim_bind_q).magnitude >= 0.5
                except Exception:
                    bind_active = False
            parent_unanimated = (rebase_unanimated_parents
                                 and not bind_active
                                 and actor_parent is not None
                                 and actor_parent not in animated_bones)
            if parent_absent or parent_unanimated:
                wa = wpr_actor.get(bone_name)
                wn = wpr_anim.get(track.bone_name) or wpr_anim.get(bone_name)
                if wa is not None and wn is not None:
                    d = wa.inverted() @ wn
                    d.normalize()
                    if abs(d.angle) > 1e-3:   # skip float noise
                        parent_delta = d

        # Insert rotation keyframes (delta-from-anim-bind retarget when the
        # anim's bind quat is known; else pose_q = rest_q^{-1} @ conjugate(anim_q))
        _insert_quaternion_keyframes(action, track, fps, rest_q,
                                     bone_name_override=bone_name,
                                     bind_q=anim_bind_q,
                                     parent_delta=parent_delta)

        # Insert location keyframes using bind-pose translation delta. For mocap
        # (relative_translation) with no explicit bind, use the track's own
        # frame-0 position as the baseline so face/finger bones (absolute
        # translation) apply only their movement, not their full offset.
        # The REBASED root bone instead uses ABSOLUTE semantics: baseline = the
        # bone's REST translation, so the bone lands exactly where the anim
        # says (seated Prof X drops from standing rest 38 to his chair at ~19).
        loc_bind = bind_trans
        root_wpr = None
        baseline_in_actor_frame = False
        if parent_delta is not None and relative_translation:
            if rest_trans is not None:
                # absolute: (parent_delta @ anim) − rest, both in ACTOR frame
                loc_bind = rest_trans
                baseline_in_actor_frame = True
            if root_motion_mode == 'vertical' and wpr_actor is not None:
                root_wpr = wpr_actor.get(bone_name)
        elif loc_bind is None and relative_translation and track.keyframes:
            loc_bind = Vector(track.keyframes[0].translation)
        _insert_location_keyframes(action, track, fps, rest_rot_inv, loc_bind,
                                   bone_name_override=bone_name,
                                   parent_delta=parent_delta,
                                   lock_horizontal_wpr=root_wpr,
                                   baseline_in_actor_frame=baseline_in_actor_frame)

        track_count += 1

    # Store metadata
    action["igb_duration_ms"] = parsed_animation.duration_ms
    action["igb_track_count"] = track_count
    action["igb_anim_name"] = parsed_animation.name

    return action


def build_all_actions(armature_obj, animations, bind_pose=None, fps=None,
                      bone_remap=None, target_bind_pose=None):
    """Build Actions for all animations and return the list.

    Args:
        armature_obj: The Blender armature object.
        animations: List of ParsedAnimation.
        bind_pose: Dict mapping bone_name -> (quat_wxyz, trans_xyz).
        fps: Frames per second. If None, uses scene render FPS.
        bone_remap: Optional dict mapping source bone names to target names.
        target_bind_pose: Dict from TARGET armature's original anim file.

    Returns:
        List of created bpy.types.Action objects.
    """
    actions = []
    for anim in animations:
        action = build_action(armature_obj, anim, bind_pose=bind_pose, fps=fps,
                              bone_remap=bone_remap,
                              target_bind_pose=target_bind_pose)
        if action:
            actions.append(action)
    return actions


def set_active_action(armature_obj, action):
    """Set the active action on an armature.

    Args:
        armature_obj: The Blender armature object.
        action: The bpy.types.Action to activate.
    """
    if armature_obj.animation_data is None:
        armature_obj.animation_data_create()
    armature_obj.animation_data.action = action


def _insert_quaternion_keyframes(action, track, fps, rest_q=None,
                                  bone_name_override=None, bind_q=None,
                                  parent_delta=None):
    """Insert quaternion rotation keyframes for a track.

    Uses the general formula to convert Alchemy absolute local quaternions
    to Blender pose deltas:

        pose_q = rest_q^{-1} @ conjugate(anim_q)

    This works regardless of bone orientation. The final local rotation is
    always conjugate(anim_q), matching the Alchemy animation.

    Args:
        action: Blender Action to add keyframes to.
        track: Animation track with keyframes.
        fps: Frames per second for time conversion.
        rest_q: Bone's rest-local Quaternion from the armature.
        bone_name_override: If provided, use this as the bone name in the
                           data path instead of track.bone_name.
    """
    from mathutils import Quaternion

    bone_name = bone_name_override or track.bone_name
    data_path = f'pose.bones["{bone_name}"].rotation_quaternion'

    # Create fcurves for W, X, Y, Z components
    fcurves = []
    for idx in range(4):
        fc = action.fcurves.new(data_path=data_path, index=idx)
        fc.keyframe_points.add(len(track.keyframes))
        fcurves.append(fc)

    # Precompute rest_q inverse
    rest_q_inv = rest_q.inverted() if rest_q is not None else None
    # The anim's OWN bind-frame quaternion for this bone (from the source
    # animation file). When present, the animation is applied as a DELTA from
    # its own bind, mapped onto the actor's rest:
    #     final_local = conjugate(anim_q) @ bind_q @ rest_q
    #     pose_q      = rest_q^{-1} @ final_local
    # This makes applying the bind pose a no-op (returns to rest) even when the
    # actor's rest differs from the anim's bind — the case for a SEPARATE anim
    # file on a native actor, which otherwise flips/rotates the whole body.
    # For an actor's OWN anim (rest_q == conjugate(bind_q)) it reduces exactly
    # to the old formula pose_q = rest_q^{-1} @ conjugate(anim_q), so no change.
    bq = Quaternion(bind_q) if bind_q is not None else None
    # Guard: some files (e.g. v8 next-gen) store a degenerate afakeanim with
    # near-zero bind quaternions. A zero quat would corrupt the delta math, so
    # discard it and fall back to the absolute formula.
    if bq is not None and bq.magnitude < 0.5:
        bq = None

    prev_q = None
    for ki, kf in enumerate(track.keyframes):
        frame = kf.time_ms / (1000.0 / fps) if fps > 0 else 0

        # Alchemy quaternion (w, x, y, z) — already in Blender WXYZ order
        aq = Quaternion(kf.quaternion)

        if rest_q_inv is not None and bq is not None:
            # delta-from-anim-bind, retargeted onto the actor's rest
            final_local = aq.conjugated() @ bq @ rest_q
            if parent_delta is not None:
                final_local = parent_delta @ final_local
            q = rest_q_inv @ final_local
        elif rest_q_inv is not None:
            # General formula: pose_q = rest_q^{-1} @ conjugate(anim_q).
            # parent_delta rebases a separate-file anim into the actor's
            # parent frame (final_local = parent_delta @ conjugate(anim_q)).
            final_local = aq.conjugated()
            if parent_delta is not None:
                final_local = parent_delta @ final_local
            q = rest_q_inv @ final_local
        else:
            q = parent_delta @ aq if parent_delta is not None else aq

        # Ensure shortest-path interpolation: q and -q are the same rotation,
        # but if the sign flips between consecutive keyframes, Blender's
        # per-component linear interpolation passes through near-zero (identity),
        # causing visible jitter. Fix: negate q when dot(prev, q) < 0.
        if prev_q is not None and prev_q.dot(q) < 0:
            q = Quaternion((-q.w, -q.x, -q.y, -q.z))
        prev_q = q

        for idx in range(4):
            point = fcurves[idx].keyframe_points[ki]
            point.co = (frame, q[idx])
            point.interpolation = 'LINEAR'

    # Update fcurves
    for fc in fcurves:
        fc.update()


def _insert_location_keyframes(action, track, fps, rest_rot_inv=None,
                                bind_trans=None, bone_name_override=None,
                                parent_delta=None, lock_horizontal_wpr=None,
                                baseline_in_actor_frame=False):
    """Insert location keyframes for a track.

    Converts Alchemy translations to Blender pose.location deltas:
        delta_trans = alchemy_translation - bind_translation
        (delta_trans is rebased by parent_delta when the bone's parent frame
         was rebased for the rotation — else root motion leaks into other axes)
        pose_loc = rest_rot_inv @ delta_trans

    lock_horizontal_wpr: when set (a Quaternion — the bone's parent rest
    rotation in armature space), the delta's X/Y components are zeroed in
    ARMATURE space: the character keeps the performance's VERTICAL position
    (sit/crouch) but stays on its staging mark horizontally.

    The bind_translation is subtracted first to compute the delta from the
    bind pose (which is the rest position). This delta is then transformed
    from Alchemy parent-local space to Blender bone rest-local space.

    For the bind pose, delta_trans = (0,0,0), so pose_loc = (0,0,0).
    For animations with root motion, delta_trans is the additional offset.

    Args:
        bone_name_override: If provided, use this as the bone name in the
                           data path instead of track.bone_name.
    """
    from mathutils import Vector

    def _delta_for(at):
        """Translation delta in the ACTOR's parent frame for one keyframe."""
        if baseline_in_actor_frame:
            # absolute semantics: rebase the anim position into the actor's
            # parent frame FIRST, then subtract the actor-frame baseline (rest)
            pos = parent_delta @ at if parent_delta is not None else at
            d = pos - bind_trans if bind_trans is not None else pos
        else:
            # delta semantics: motion vector in the ANIM frame, then rebase
            d = at - bind_trans if bind_trans is not None else at
            if parent_delta is not None:
                d = parent_delta @ d
        # 'vertical' root motion: zero horizontal travel in ARMATURE space
        # (staging placement is object-level Z-rotation only, so armature Z ==
        # world up). Keeps sit/crouch height, pins the char to its mark.
        if lock_horizontal_wpr is not None:
            d_arm = lock_horizontal_wpr @ d
            d_arm.x = 0.0
            d_arm.y = 0.0
            d = lock_horizontal_wpr.inverted() @ d_arm
        return d

    # Compute delta translations and check if any are meaningful
    has_meaningful_delta = False
    for kf in track.keyframes:
        if _delta_for(Vector(kf.translation)).length > 1e-6:
            has_meaningful_delta = True
            break

    if not has_meaningful_delta and track.is_constant:
        return

    bone_name = bone_name_override or track.bone_name
    data_path = f'pose.bones["{bone_name}"].location'

    fcurves = []
    for idx in range(3):
        fc = action.fcurves.new(data_path=data_path, index=idx)
        fc.keyframe_points.add(len(track.keyframes))
        fcurves.append(fc)

    for ki, kf in enumerate(track.keyframes):
        frame = kf.time_ms / (1000.0 / fps) if fps > 0 else 0

        # Delta in the actor's parent frame (rebase/baseline/lock handled once
        # in _delta_for — see above)
        delta = _delta_for(Vector(kf.translation))

        # Convert delta to bone rest-local space
        if rest_rot_inv is not None:
            t = rest_rot_inv @ delta
        else:
            t = delta

        for idx in range(3):
            point = fcurves[idx].keyframe_points[ki]
            point.co = (frame, t[idx])
            point.interpolation = 'LINEAR'

    for fc in fcurves:
        fc.update()
