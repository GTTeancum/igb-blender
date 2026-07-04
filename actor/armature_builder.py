"""Build a Blender Armature from a ParsedSkeleton.

Uses inv_joint matrices (from skin file) for both position AND rotation of
deforming bones.  Non-deforming bones (e.g. Root, Bip01) use FK from the
afakeanim bind-pose animation.

ANIMATION COMPATIBILITY:
The animation_builder.py uses: pose_q = rest_q^{-1} @ conjugate(anim_q)
rest_q comes from the Blender armature's actual rest-local quaternion,
so the formula works regardless of how rest pose was constructed.
"""

import math
from typing import Dict, List, Optional, Tuple

from .sg_skeleton import ParsedSkeleton, ParsedBone


# Minimum bone length to prevent zero-length bones in Blender
_MIN_BONE_LENGTH = 0.5

# Maximum bone length to prevent oversized display (e.g., root→pelvis ~42 units)
_MAX_BONE_LENGTH = 12.0


def build_armature(context, skeleton, armature_name=None, collection=None,
                   skin_data=None, bind_pose=None, inv_joint_data=None):
    """Build a Blender Armature from a ParsedSkeleton.

    Uses inv_joint matrices (from skin file) for both position AND rotation
    of deforming bones — this is the authoritative source, same approach as
    the Noesis reference.  Non-deforming bones use FK from afakeanim.

    Args:
        context: Blender context.
        skeleton: ParsedSkeleton instance.
        armature_name: Name for the armature (defaults to skeleton.name).
        collection: Blender collection to link to (defaults to active collection).
        skin_data: Optional list of skin geometry data (fallback for positions).
        bind_pose: Dict mapping bone_name -> (quat_wxyz, trans_xyz) from
                   afakeanim. Used for computing bone orientations (FK chain).
        inv_joint_data: Dict mapping bone_name -> inv_joint_matrix (16-float
                        tuple, row-major) from the skin file's skeleton.
                        Used for exact bone world positions AND rotations.

    Returns:
        The armature Object (bpy.types.Object with type='ARMATURE').
    """
    import bpy
    from mathutils import Vector, Quaternion, Matrix

    if armature_name is None:
        armature_name = skeleton.name or "Skeleton"

    # Compute world-space positions AND rotations for each bone.
    # Both come from the SAME source to ensure consistency.
    world_rotations = {}

    if bind_pose:
        # FK positions + rotations as base (for all bones including non-deforming)
        fk_positions, world_rotations = _compute_oriented_positions(skeleton, bind_pose)
        world_positions = dict(fk_positions)
    else:
        world_positions = _compute_world_positions(skeleton, skin_data)

    # Override deforming bones with inv_joint data (BOTH position AND rotation).
    # This is the authoritative source — same approach as the Noesis reference.
    if inv_joint_data:
        for bone in skeleton.bones:
            inv_mat = inv_joint_data.get(bone.name)
            if inv_mat is not None:
                world_mat = _invert_4x4(inv_mat)
                if world_mat:
                    # Position from inv_joint (row-major: translation in last row)
                    world_positions[bone.index] = (world_mat[12], world_mat[13], world_mat[14])
                    # Rotation from inv_joint (row-major → Blender column-major: transpose)
                    blender_mat = Matrix((
                        (world_mat[0], world_mat[4], world_mat[8], world_mat[12]),
                        (world_mat[1], world_mat[5], world_mat[9], world_mat[13]),
                        (world_mat[2], world_mat[6], world_mat[10], world_mat[14]),
                        (world_mat[3], world_mat[7], world_mat[11], world_mat[15]),
                    ))
                    rot_q = blender_mat.to_3x3().to_quaternion()
                    rot_q.normalize()
                    world_rotations[bone.index] = rot_q

    # Fill bones left at the origin: non-skinned dummies (fx*, Motion, *Nub,
    # Footsteps) have no inv_joint, and the v8 bind pose (afakeanim) is
    # degenerate, so they default to (0,0,0). Place each from its parent's world
    # position + the parent's world rotation applied to the skeleton's
    # parent-relative bone translation (the authoritative rest offset).
    def _at_origin(p):
        return p is None or (abs(p[0]) < 1e-5 and abs(p[1]) < 1e-5
                             and abs(p[2]) < 1e-5)

    def _fill_pos(idx, seen=None):
        if seen is None:
            seen = set()
        cur = world_positions.get(idx)
        if not _at_origin(cur):
            return cur
        if idx in seen:
            return cur if cur is not None else (0.0, 0.0, 0.0)
        seen.add(idx)
        bone = skeleton.bones[idx]
        pidx = bone.parent_idx
        if pidx is not None and 0 <= pidx < len(skeleton.bones):
            ppos = _fill_pos(pidx, seen)
            prot = world_rotations.get(pidx)
            t = Vector(bone.translation)
            if prot is not None:
                t = prot @ t
            newpos = (ppos[0] + t[0], ppos[1] + t[1], ppos[2] + t[2])
            world_positions[idx] = newpos
            # Inherit the parent's orientation so the dummy isn't axis-aligned
            if idx not in world_rotations and prot is not None:
                world_rotations[idx] = prot
            return newpos
        # Root with no parent: keep its own translation (often the origin)
        return tuple(bone.translation)

    for bone in skeleton.bones:
        _fill_pos(bone.index)

    # Create armature data block and object
    armature = bpy.data.armatures.new(armature_name)
    armature.display_type = 'OCTAHEDRAL'
    arm_obj = bpy.data.objects.new(armature_name, armature)

    # Link to collection
    if collection is None:
        collection = context.collection
    collection.objects.link(arm_obj)

    # Make active and enter edit mode
    context.view_layer.objects.active = arm_obj
    arm_obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')

    edit_bones = armature.edit_bones
    bone_map = {}  # bone_index -> EditBone

    # Pass 1: Create bones with bind-pose orientation (rest = bind)
    for bone in skeleton.bones:
        eb_name = bone.name if bone.name else f"Bone_{bone.index:03d}"
        eb = edit_bones.new(eb_name)
        head = Vector(world_positions.get(bone.index, (0.0, 0.0, 0.0)))
        eb.head = head

        # Compute bone length from distance to first child, capped
        children = skeleton.get_children(bone.index)
        if children:
            first_child_idx = min(children)
            child_head = Vector(world_positions.get(first_child_idx, tuple(head)))
            bone_length = (child_head - head).length
            bone_length = max(min(bone_length, _MAX_BONE_LENGTH), _MIN_BONE_LENGTH)
        else:
            # Leaf bone: fraction of parent length
            if bone.parent_idx >= 0 and bone.parent_idx in bone_map:
                parent_eb = bone_map[bone.parent_idx]
                bone_length = max(parent_eb.length * 0.4, _MIN_BONE_LENGTH)
            else:
                bone_length = _MIN_BONE_LENGTH

        # Orient bone from world rotation (inv_joint or FK fallback)
        world_q = world_rotations.get(bone.index)
        if world_q is not None:
            rot_mat = world_q.to_matrix()  # 3x3
            y_axis = rot_mat @ Vector((0, 1, 0))
            z_axis = rot_mat @ Vector((0, 0, 1))
            eb.tail = head + y_axis * bone_length
            eb.align_roll(z_axis)
        else:
            # No rotation data: point tail up
            eb.tail = (head[0], head[1], head[2] + bone_length)

        eb.use_connect = False

        # Set parent
        if bone.parent_idx >= 0 and bone.parent_idx in bone_map:
            eb.parent = bone_map[bone.parent_idx]

        bone_map[bone.index] = eb

    # Pass 2: Connect chains where child head ≈ parent tail
    for bone in skeleton.bones:
        if bone.parent_idx < 0 or bone.parent_idx not in bone_map:
            continue
        eb = bone_map[bone.index]
        parent_eb = bone_map[bone.parent_idx]
        dist = (Vector(eb.head) - Vector(parent_eb.tail)).length
        if dist < 0.5:
            eb.use_connect = True

    bpy.ops.object.mode_set(mode='OBJECT')

    # Store bone metadata as custom properties on the armature
    arm_obj["igb_skeleton_name"] = skeleton.name
    arm_obj["igb_joint_count"] = skeleton.joint_count
    arm_obj["igb_bone_count"] = len(skeleton.bones)

    # Store per-bone metadata on pose bones
    for bone in skeleton.bones:
        pb_name = bone.name if bone.name else f"Bone_{bone.index:03d}"
        if pb_name in arm_obj.pose.bones:
            pb = arm_obj.pose.bones[pb_name]
            pb["igb_bone_index"] = bone.index
            pb["igb_parent_idx"] = bone.parent_idx
            pb["igb_bm_idx"] = skeleton.get_effective_bm_idx(bone.index)
            pb["igb_flags"] = bone.flags
            pb.rotation_mode = 'QUATERNION'

    _assign_bone_collections(arm_obj, skeleton)

    return arm_obj


def _assign_bone_collections(arm_obj, skeleton):
    """Organize bones into named collections by their game role.

    Roles come from the igSkeletonBoneInfo flags (0x40 = helper such as the
    root 'Unnamed bone' and fx01-fx04 attachment points; 0x42 = the 'Motion'
    root-motion extraction bone) plus name conventions (Nub end-caps,
    Footsteps). Deforming bones (bmIdx >= 0) go to Deform. Makes 40-60-bone
    game rigs scannable and lets users hide the helpers with one click.
    """
    try:
        colls = {}

        def coll(name):
            if name not in colls:
                c = arm_obj.data.collections.get(name)
                colls[name] = c or arm_obj.data.collections.new(name)
            return colls[name]

        palette = {"Deform": 'THEME01', "Helpers": 'THEME09',
                   "Motion": 'THEME03', "Attach (fx)": 'THEME04'}
        for bone in skeleton.bones:
            name = bone.name if bone.name else f"Bone_{bone.index:03d}"
            b = arm_obj.data.bones.get(name)
            if b is None:
                continue
            lname = name.lower()
            if bone.flags & 0x02 and lname == "motion" or lname == "motion":
                group = "Motion"
            elif lname.startswith("fx"):
                group = "Attach (fx)"
            elif (bone.flags & 0x40 or lname.endswith("nub")
                  or "footsteps" in lname or name.startswith("Unnamed")):
                group = "Helpers"
            elif skeleton.get_effective_bm_idx(bone.index) >= 0:
                group = "Deform"
            else:
                group = "Helpers"
            coll(group).assign(b)
            pb = arm_obj.pose.bones.get(name)
            if pb is not None:
                try:
                    pb.color.palette = palette.get(group, 'DEFAULT')
                except Exception:
                    pass
    except Exception:
        # cosmetic only — never fail an import over collections
        pass


def _compute_oriented_positions(skeleton, bind_pose):
    """Compute world positions and rotations using conjugated bind-pose quaternions.

    Alchemy stores quaternions in the conjugate of Blender's convention.
    Using conjugated quaternions to rotate child translations into parent
    space produces anatomically correct bone positions.

    Args:
        skeleton: ParsedSkeleton instance.
        bind_pose: Dict mapping bone_name -> (quat_wxyz, trans_xyz).

    Returns:
        Tuple of (world_positions, world_rotations) dicts:
        - world_positions: bone_index -> (x, y, z) tuple
        - world_rotations: bone_index -> Quaternion
    """
    from mathutils import Vector, Quaternion

    world_pos = {}
    world_rot = {}
    computed = set()

    def _compute(idx):
        if idx in computed:
            return
        computed.add(idx)

        bone = skeleton.bones[idx]
        bp = bind_pose.get(bone.name)

        # Conjugated: Alchemy stores rotations in the opposite convention
        if bp:
            local_q = Quaternion(bp[0]).conjugated()
        else:
            local_q = Quaternion((1, 0, 0, 0))

        t = Vector(bone.translation)

        if bone.parent_idx < 0 or bone.parent_idx >= len(skeleton.bones):
            world_pos[idx] = tuple(t)
            world_rot[idx] = local_q
        else:
            _compute(bone.parent_idx)
            pp = Vector(world_pos[bone.parent_idx])
            pq = world_rot[bone.parent_idx]
            rt = pq @ t
            world_pos[idx] = tuple(pp + rt)
            world_rot[idx] = pq @ local_q

    for bone in skeleton.bones:
        _compute(bone.index)

    return world_pos, world_rot


def _compute_inv_joint_positions(skeleton, inv_joint_data):
    """Compute world positions from inverse joint matrices.

    The inv_joint matrix for a bone is the INVERSE of its world-space bind
    transform. Inverting it gives the world position (translation component
    at indices [12], [13], [14] in row-major layout).

    Non-deforming bones (root, Bip01) without inv_joint matrices get their
    positions from parent + accumulated translation.

    Args:
        skeleton: ParsedSkeleton instance.
        inv_joint_data: Dict mapping bone_name -> inv_joint_matrix tuple.

    Returns:
        Dict mapping bone_index -> (x, y, z) world position.
    """
    positions = {}

    # First pass: extract positions from inv_joint matrices
    for bone in skeleton.bones:
        inv_mat = inv_joint_data.get(bone.name)
        if inv_mat is not None:
            world_mat = _invert_4x4(inv_mat)
            if world_mat:
                positions[bone.index] = (world_mat[12], world_mat[13], world_mat[14])

    # Second pass: fill non-deforming bones via parent chain + translation
    def _fill(bone_idx):
        if bone_idx in positions:
            return positions[bone_idx]
        bone = skeleton.bones[bone_idx]
        if bone.parent_idx >= 0 and bone.parent_idx < len(skeleton.bones):
            parent_pos = _fill(bone.parent_idx)
            t = bone.translation
            positions[bone_idx] = (
                parent_pos[0] + t[0],
                parent_pos[1] + t[1],
                parent_pos[2] + t[2],
            )
        else:
            positions[bone_idx] = bone.translation
        return positions[bone_idx]

    for bone in skeleton.bones:
        _fill(bone.index)

    return positions


def _compute_tail_position(bone, skeleton, world_positions, head):
    """Compute tail position for a bone based on child positions or parent direction."""
    children = skeleton.get_children(bone.index)
    if children:
        child_positions = [world_positions.get(c, head) for c in children]
        avg_x = sum(p[0] for p in child_positions) / len(child_positions)
        avg_y = sum(p[1] for p in child_positions) / len(child_positions)
        avg_z = sum(p[2] for p in child_positions) / len(child_positions)
        tail = (avg_x, avg_y, avg_z)

        dx = tail[0] - head[0]
        dy = tail[1] - head[1]
        dz = tail[2] - head[2]
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        if length < _MIN_BONE_LENGTH:
            tail = (head[0], head[1] + _MIN_BONE_LENGTH, head[2])
    else:
        if bone.parent_idx >= 0:
            parent_head = world_positions.get(bone.parent_idx, (0, 0, 0))
            dx = head[0] - parent_head[0]
            dy = head[1] - parent_head[1]
            dz = head[2] - parent_head[2]
            length = math.sqrt(dx * dx + dy * dy + dz * dz)
            if length > _MIN_BONE_LENGTH:
                scale = 0.5
                tail = (head[0] + dx * scale, head[1] + dy * scale, head[2] + dz * scale)
            else:
                tail = (head[0], head[1] + _MIN_BONE_LENGTH, head[2])
        else:
            tail = (head[0], head[1] + _MIN_BONE_LENGTH, head[2])

    return tail


def _compute_world_positions(skeleton, skin_data=None):
    """Compute world-space head positions for each bone.

    Primary strategy: Accumulate parent-relative translations through the
    bone hierarchy. This is the standard Alchemy approach — bone translations
    are offsets in parent space, accumulated without rotation.

    Fallback: Inverse joint matrices (for MUA/Alchemy 5.0 files that store
    them), or skin vertex centroids.
    """
    positions = {}

    # --- Strategy 1: Inverse joint matrices (if present) ---
    has_inv_joints = any(b.inv_joint_matrix is not None for b in skeleton.bones)

    if has_inv_joints:
        for bone in skeleton.bones:
            if bone.inv_joint_matrix is not None:
                world_mat = _invert_4x4(bone.inv_joint_matrix)
                if world_mat:
                    positions[bone.index] = (world_mat[12], world_mat[13], world_mat[14])
                else:
                    positions[bone.index] = (0.0, 0.0, 0.0)
            else:
                positions[bone.index] = (0.0, 0.0, 0.0)
        return positions

    # --- Strategy 2: Accumulated translations (primary for XML2) ---
    positions = _accumulate_translations(skeleton)
    return positions


def _accumulate_translations(skeleton):
    """Compute bone world positions by accumulating parent-relative translations.

    Each bone's world position = parent_world_position + bone_local_translation.
    No rotation is applied — this produces positions in the same coordinate
    space as the skin mesh vertices.
    """
    positions = {}
    processed = set()

    def _accum(bone_idx):
        if bone_idx in processed:
            return positions.get(bone_idx, (0.0, 0.0, 0.0))

        bone = skeleton.bones[bone_idx]
        if bone.parent_idx < 0 or bone.parent_idx >= len(skeleton.bones):
            positions[bone_idx] = bone.translation
        else:
            parent_pos = _accum(bone.parent_idx)
            tx, ty, tz = bone.translation
            positions[bone_idx] = (
                parent_pos[0] + tx,
                parent_pos[1] + ty,
                parent_pos[2] + tz,
            )

        processed.add(bone_idx)
        return positions[bone_idx]

    for bone in skeleton.bones:
        _accum(bone.index)

    return positions


def _compute_positions_from_skin(skeleton, skin_data):
    """Compute bone world positions from weighted skin vertex centroids."""
    num_bones = len(skeleton.bones)

    wx_sum = [0.0] * num_bones
    wy_sum = [0.0] * num_bones
    wz_sum = [0.0] * num_bones
    w_total = [0.0] * num_bones

    for positions_list, blend_weights, blend_indices, bms_indices in skin_data:
        if not positions_list or not blend_weights or not blend_indices:
            continue

        num_verts = min(len(positions_list), len(blend_weights), len(blend_indices))

        for vi in range(num_verts):
            vx, vy, vz = positions_list[vi]
            weights = blend_weights[vi]
            indices = blend_indices[vi]

            for w, bi in zip(weights, indices):
                if w <= 0.0:
                    continue

                if bms_indices is not None and bi < len(bms_indices):
                    global_bm_idx = bms_indices[bi]
                else:
                    global_bm_idx = bi

                bone_idx = _bm_to_bone_idx(skeleton, global_bm_idx)
                if bone_idx is not None and 0 <= bone_idx < num_bones:
                    wx_sum[bone_idx] += vx * w
                    wy_sum[bone_idx] += vy * w
                    wz_sum[bone_idx] += vz * w
                    w_total[bone_idx] += w

    positions = {}
    for i in range(num_bones):
        if w_total[i] > 0.001:
            positions[i] = (
                wx_sum[i] / w_total[i],
                wy_sum[i] / w_total[i],
                wz_sum[i] / w_total[i],
            )

    for bone in skeleton.bones:
        if bone.index not in positions:
            if bone.parent_idx >= 0 and bone.parent_idx in positions:
                positions[bone.index] = positions[bone.parent_idx]
            else:
                positions[bone.index] = (0.0, 0.0, 0.0)

    return positions if len(positions) > 0 else {}


def _bm_to_bone_idx(skeleton, bm_idx):
    """Map a blend matrix index to a bone index."""
    for bone in skeleton.bones:
        effective = skeleton.get_effective_bm_idx(bone.index)
        if effective == bm_idx:
            return bone.index
    if 0 <= bm_idx < len(skeleton.bones):
        return bm_idx
    return None


def _invert_4x4(m):
    """Invert a 4x4 matrix (tuple of 16 floats, row-major)."""
    a = list(m)
    inv = [0.0] * 16
    for i in range(4):
        inv[i * 4 + i] = 1.0

    for col in range(4):
        max_val = abs(a[col * 4 + col])
        max_row = col
        for row in range(col + 1, 4):
            val = abs(a[row * 4 + col])
            if val > max_val:
                max_val = val
                max_row = row

        if max_val < 1e-12:
            return None

        if max_row != col:
            for k in range(4):
                a[col * 4 + k], a[max_row * 4 + k] = a[max_row * 4 + k], a[col * 4 + k]
                inv[col * 4 + k], inv[max_row * 4 + k] = inv[max_row * 4 + k], inv[col * 4 + k]

        pivot = a[col * 4 + col]
        for k in range(4):
            a[col * 4 + k] /= pivot
            inv[col * 4 + k] /= pivot

        for row in range(4):
            if row == col:
                continue
            factor = a[row * 4 + col]
            for k in range(4):
                a[row * 4 + k] -= factor * a[col * 4 + k]
                inv[row * 4 + k] -= factor * inv[col * 4 + k]

    return tuple(inv)


# --- Utility functions (used by animation export and tests) ---

def _quat_to_mat3(w, x, y, z):
    """Quaternion to 3x3 rotation matrix.

    Uses the Alchemy row-major convention (transpose of standard math formula).
    Used for animation world-space computations (not for armature building).

    Returns flat list of 9 floats: [r00,r01,r02, r10,r11,r12, r20,r21,r22].
    """
    xx = x*x; yy = y*y; zz = z*z
    xy = x*y; xz = x*z; yz = y*z
    wx = w*x; wy = w*y; wz = w*z
    return [
        1-2*(yy+zz), 2*(xy+wz),   2*(xz-wy),
        2*(xy-wz),   1-2*(xx+zz), 2*(yz+wx),
        2*(xz+wy),   2*(yz-wx),   1-2*(xx+yy),
    ]


def _mat3_mul_vec3(m, v):
    """Multiply 3x3 matrix by 3-vector."""
    return (
        m[0]*v[0] + m[1]*v[1] + m[2]*v[2],
        m[3]*v[0] + m[4]*v[1] + m[5]*v[2],
        m[6]*v[0] + m[7]*v[1] + m[8]*v[2],
    )


def _mat3_mul_mat3(a, b):
    """Multiply two 3x3 matrices."""
    r = [0.0]*9
    for i in range(3):
        for j in range(3):
            for k in range(3):
                r[i*3+j] += a[i*3+k] * b[k*3+j]
    return r


def _compute_world_data_from_bind_pose(skeleton, bind_pose):
    """Compute world-space position + rotation for each bone from bind pose.

    NOTE: This is used for animation world-space computations (e.g., verifying
    animation correctness), NOT for armature building. The armature uses
    accumulated translations without rotation.

    Args:
        skeleton: ParsedSkeleton instance.
        bind_pose: Dict mapping bone_name -> (quat_wxyz, trans_xyz).

    Returns:
        Dict mapping bone_index -> (world_pos_xyz, world_rot_3x3_flat)
    """
    world_data = {}

    def _get_world(bone_idx):
        if bone_idx in world_data:
            return world_data[bone_idx]

        bone = skeleton.bones[bone_idx]
        bp_data = bind_pose.get(bone.name)

        if bp_data:
            quat_wxyz = bp_data[0]
        else:
            quat_wxyz = (1.0, 0.0, 0.0, 0.0)

        w, x, y, z = quat_wxyz
        local_rot = _quat_to_mat3(w, x, y, z)

        if bone.parent_idx >= 0 and bone.parent_idx < len(skeleton.bones):
            parent_pos, parent_rot = _get_world(bone.parent_idx)

            t = bone.translation
            rotated_t = _mat3_mul_vec3(parent_rot, t)

            world_pos = (
                parent_pos[0] + rotated_t[0],
                parent_pos[1] + rotated_t[1],
                parent_pos[2] + rotated_t[2],
            )
            world_rot = _mat3_mul_mat3(parent_rot, local_rot)
        else:
            world_pos = bone.translation
            world_rot = local_rot

        world_data[bone_idx] = (world_pos, world_rot)
        return world_data[bone_idx]

    for bone in skeleton.bones:
        _get_world(bone.index)

    return world_data
