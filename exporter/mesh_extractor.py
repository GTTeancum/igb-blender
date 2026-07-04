"""Extract mesh data from Blender objects for IGB export.

Converts a Blender mesh object into MeshExport data classes suitable for
the IGBBuilder. Handles triangulation, vertex splitting (for per-loop
normals/UVs), UV V-flip (Blender OpenGL → Alchemy DirectX), vertex
color extraction, per-material splitting, and triangle strip conversion.

This is the inverse of mesh_builder.py.
"""

import struct
import math


class MeshExport:
    """Exported mesh data ready for IGBBuilder consumption.

    Attributes:
        positions: list of (x, y, z) tuples per unique vertex
        normals: list of (nx, ny, nz) tuples per unique vertex
        uvs: list of (u, v) tuples per unique vertex (already V-flipped)
        colors: list of (r, g, b, a) tuples per unique vertex (0-255 int)
        indices: list of int (uint16 triangle indices, flat)
        bbox_min: (x, y, z) axis-aligned bounding box minimum
        bbox_max: (x, y, z) axis-aligned bounding box maximum
        name: mesh name string
        material_index: index into bl_object.material_slots (-1 if none)
        blend_weights: list of (w0, w1, w2, w3) float tuples per vertex (skinning)
        blend_indices: list of (i0, i1, i2, i3) int tuples per vertex (skinning)
    """

    def __init__(self):
        self.positions = []
        self.normals = []
        self.uvs = []
        self.colors = []
        self.indices = []
        self.bbox_min = (0.0, 0.0, 0.0)
        self.bbox_max = (0.0, 0.0, 0.0)
        self.name = ""
        self.material_index = -1
        self.blend_weights = []
        self.blend_indices = []


def _bake_world_matrix(bl_mesh, world_matrix):
    """Bake an object's world matrix into a (temporary) evaluated mesh.

    Applies the full transform so the exported geometry sits at its world
    position/orientation/scale — needed when exporting a mesh whose object
    carries a non-identity transform (e.g. an FBX prop imported with a
    +90°X object rotation, or a posed armature-child at a world offset).
    A negative-determinant matrix (mirrored/negative-scale object) reverses
    winding, so flip normals to keep faces + lighting correct.

    Safe on the temp mesh from Object.to_mesh(): it is discarded afterwards,
    so the source object's mesh data is untouched.
    """
    if world_matrix is None:
        return
    bl_mesh.transform(world_matrix)
    if world_matrix.determinant() < 0.0:
        bl_mesh.flip_normals()


def extract_mesh(bl_object, uv_v_flip=True, world_matrix=None):
    """Extract mesh data from a Blender object (all materials combined).

    Creates a MeshExport with unique vertices split wherever UVs or normals
    differ per-loop (the same approach as a typical game exporter).

    Args:
        bl_object: Blender mesh object (bpy.types.Object with type=='MESH')
        uv_v_flip: if True, apply v = 1.0 - v for DirectX convention (default True)
        world_matrix: optional 4x4 Matrix to bake into the geometry (world
            transform). None (default) keeps local coords — the map exporter's
            historical behavior.

    Returns:
        MeshExport instance with all data populated

    Raises:
        ValueError: if the object has no mesh data or no triangles
    """
    import bpy

    if bl_object.type != 'MESH':
        raise ValueError(f"Object '{bl_object.name}' is not a mesh (type={bl_object.type})")

    # Get evaluated mesh (applies modifiers)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    eval_obj = bl_object.evaluated_get(depsgraph)
    bl_mesh = eval_obj.to_mesh()

    if bl_mesh is None:
        raise ValueError(f"Could not get mesh data from '{bl_object.name}'")

    try:
        _bake_world_matrix(bl_mesh, world_matrix)
        return _extract_from_mesh(bl_mesh, bl_object.name, uv_v_flip)
    finally:
        eval_obj.to_mesh_clear()


def extract_mesh_per_material(bl_object, uv_v_flip=True, world_matrix=None):
    """Extract per-material submeshes from a Blender object.

    Splits the mesh by material slot. Each returned MeshExport contains only
    the triangles assigned to that material, with independent vertex indices
    starting from 0.

    Args:
        bl_object: Blender mesh object (bpy.types.Object with type=='MESH')
        uv_v_flip: if True, apply v = 1.0 - v for DirectX convention
        world_matrix: optional 4x4 Matrix to bake into the geometry (world
            transform). None (default) keeps local coords.

    Returns:
        list of MeshExport, one per material slot that has geometry.
        Each MeshExport.material_index tracks which slot it came from.
        If the object has no material slots, returns a single MeshExport
        with material_index=-1.

    Raises:
        ValueError: if the object has no mesh data or no triangles
    """
    import bpy

    if bl_object.type != 'MESH':
        raise ValueError(f"Object '{bl_object.name}' is not a mesh (type={bl_object.type})")

    depsgraph = bpy.context.evaluated_depsgraph_get()
    eval_obj = bl_object.evaluated_get(depsgraph)
    bl_mesh = eval_obj.to_mesh()

    if bl_mesh is None:
        raise ValueError(f"Could not get mesh data from '{bl_object.name}'")

    try:
        _bake_world_matrix(bl_mesh, world_matrix)
        bl_mesh.calc_loop_triangles()
        loop_tris = bl_mesh.loop_triangles

        if len(loop_tris) == 0:
            raise ValueError(f"Mesh '{bl_object.name}' has no triangles")

        # If no material slots, extract everything as one submesh
        num_slots = len(bl_object.material_slots)
        if num_slots == 0:
            result = _extract_from_mesh(bl_mesh, bl_object.name, uv_v_flip)
            result.material_index = -1
            return [result]

        # Group triangles by material_index
        tris_by_mat = {}
        for tri in loop_tris:
            mat_idx = tri.material_index
            if mat_idx not in tris_by_mat:
                tris_by_mat[mat_idx] = []
            tris_by_mat[mat_idx].append(tri)

        # Extract each material group as a separate submesh
        submeshes = []
        for mat_idx in sorted(tris_by_mat.keys()):
            tris = tris_by_mat[mat_idx]
            mat_name = ""
            if mat_idx < num_slots and bl_object.material_slots[mat_idx].material:
                mat_name = bl_object.material_slots[mat_idx].material.name

            submesh = _extract_from_triangles(
                bl_mesh, tris,
                f"{bl_object.name}_{mat_name}" if mat_name else f"{bl_object.name}_mat{mat_idx}",
                uv_v_flip
            )
            submesh.material_index = mat_idx
            submeshes.append(submesh)

        return submeshes

    finally:
        eval_obj.to_mesh_clear()


def extract_mesh_data(bl_mesh, name="Mesh", uv_v_flip=True):
    """Extract mesh data from a Blender Mesh data-block directly.

    For use when you already have a bpy.types.Mesh (e.g., in tests).

    Args:
        bl_mesh: Blender mesh data (bpy.types.Mesh)
        name: name for the export
        uv_v_flip: if True, apply v = 1.0 - v

    Returns:
        MeshExport instance
    """
    return _extract_from_mesh(bl_mesh, name, uv_v_flip)


def _build_skin_bone_mapping(skeleton, armature_obj, bms_indices):
    """Build bone name → blend matrix index mappings for skin export.

    Shared setup for extract_skin_mesh and extract_skin_mesh_per_material.

    Args:
        skeleton: ParsedSkeleton-like object with .bones list.
        armature_obj: Blender armature object (for renamed bone fallback).
        bms_indices: BMS palette list (local_idx -> global_bm_idx), or None.

    Returns:
        (bone_name_to_bm, global_to_local) tuple where:
        - bone_name_to_bm: dict mapping bone name -> global bm_idx
        - global_to_local: dict mapping global_bm_idx -> local blend idx, or None
    """
    # Build bone_name -> global_bm_idx mapping from skeleton.
    # CRITICAL: Only use bones with EXPLICIT bm_idx values (bm_idx >= 0).
    # Bones with bm_idx=-1 (like unnamed root, Bip01) are NOT deforming
    # bones — their effective_bm_idx fallback (= bone_index) can collide
    # with real bm_idx values from other bones.
    bone_name_to_bm = {}
    for bone in skeleton.bones:
        if bone.bm_idx >= 0:
            bone_name_to_bm[bone.name] = bone.bm_idx
        else:
            eff_bm = skeleton.get_effective_bm_idx(bone.index)
            has_conflict = any(
                b.bm_idx == eff_bm for b in skeleton.bones if b.bm_idx >= 0
            )
            bms_out_of_range = (bms_indices is not None and
                                eff_bm not in range(len(bms_indices)))
            if not has_conflict and not bms_out_of_range:
                bone_name_to_bm[bone.name] = eff_bm

    # Also map CURRENT armature bone names to bm_idx.  This handles the
    # case where bones were renamed in Blender (e.g. by the rig converter
    # or manual edits).  Blender auto-renames vertex groups when bone names
    # change, so vertex groups follow the armature names — but the stored
    # skeleton data (igb_skin_bone_info_list) still has the original names.
    if armature_obj is not None and armature_obj.type == 'ARMATURE':
        for pb in armature_obj.pose.bones:
            if pb.name in bone_name_to_bm:
                continue
            bm_idx = pb.get("igb_skin_bm_idx")
            if bm_idx is not None and bm_idx >= 0:
                bone_name_to_bm[pb.name] = bm_idx

    # Build reverse BMS palette: global_bm_idx -> local_blend_idx
    if bms_indices is not None:
        global_to_local = {}
        for local_idx, global_bm_idx in enumerate(bms_indices):
            global_to_local[global_bm_idx] = local_idx
    else:
        global_to_local = None

    return bone_name_to_bm, global_to_local


def extract_skin_mesh(bl_object, armature_obj, skeleton, bms_indices=None,
                       uv_v_flip=True):
    """Extract mesh data with blend weights/indices for skin export.

    Forces armature to REST pose, then evaluates the mesh via depsgraph
    to get bind-pose positions with vertex group weights intact.
    The game applies skinning on load, so we need undeformed rest-pose
    positions — if we exported posed positions, double-deformation occurs.

    Args:
        bl_object: Blender mesh object with vertex groups.
        armature_obj: Blender armature object (for REST pose enforcement).
        skeleton: ParsedSkeleton from the skin file (has correct bm_idx values).
        bms_indices: Optional BMS palette list (local_idx -> global_bm_idx).
                     If provided, we reverse-map global_bm_idx -> local_idx
                     for the exported blend indices.
        uv_v_flip: if True, apply v = 1.0 - v for DirectX convention.

    Returns:
        MeshExport with blend_weights and blend_indices populated.
    """
    import bpy

    if bl_object.type != 'MESH':
        raise ValueError(f"Object '{bl_object.name}' is not a mesh")

    bone_name_to_bm, global_to_local = _build_skin_bone_mapping(
        skeleton, armature_obj, bms_indices
    )

    # Force armature to REST pose before evaluating mesh.
    # This ensures we get bind-pose positions, not animation-deformed positions.
    old_pose_position = None
    if armature_obj is not None and armature_obj.type == 'ARMATURE':
        old_pose_position = armature_obj.data.pose_position
        armature_obj.data.pose_position = 'REST'
        # Force full depsgraph update to propagate the pose change
        depsgraph = bpy.context.evaluated_depsgraph_get()
        depsgraph.update()

    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        eval_obj = bl_object.evaluated_get(depsgraph)
        bl_mesh = eval_obj.to_mesh()

        if bl_mesh is None:
            raise ValueError(f"Could not get mesh data from '{bl_object.name}'")

        try:
            # Extract base mesh data (positions, normals, UVs, indices)
            result = _extract_from_mesh(bl_mesh, bl_object.name, uv_v_flip)

            # Extract blend weights/indices per unique vertex
            _extract_blend_data(
                bl_mesh, bl_object, result,
                bone_name_to_bm, global_to_local, uv_v_flip
            )

            # Sanity check: blend data count must match position count
            if result.blend_weights and len(result.blend_weights) != len(result.positions):
                print(f"WARNING: blend_weights count ({len(result.blend_weights)}) "
                      f"!= positions count ({len(result.positions)})")

            return result
        finally:
            eval_obj.to_mesh_clear()
    finally:
        # Restore original pose mode
        if old_pose_position is not None:
            armature_obj.data.pose_position = old_pose_position
            bpy.context.view_layer.update()


def extract_skin_mesh_per_material(bl_object, armature_obj, skeleton,
                                    bms_indices=None, uv_v_flip=True):
    """Extract per-material skinned submeshes from a Blender mesh object.

    Like extract_skin_mesh but splits the mesh by material slot. Each
    returned MeshExport contains only the triangles assigned to that
    material, with blend weights/indices correctly mapped.

    Args:
        Same as extract_skin_mesh.

    Returns:
        list of MeshExport, one per material slot that has geometry.
        Each has .material_index set to the slot index.
        If the mesh has 0 or 1 material slots, returns a single-element list.
    """
    import bpy

    if bl_object.type != 'MESH':
        raise ValueError(f"Object '{bl_object.name}' is not a mesh")

    bone_name_to_bm, global_to_local = _build_skin_bone_mapping(
        skeleton, armature_obj, bms_indices
    )

    # Force armature to REST pose
    old_pose_position = None
    if armature_obj is not None and armature_obj.type == 'ARMATURE':
        old_pose_position = armature_obj.data.pose_position
        armature_obj.data.pose_position = 'REST'
        depsgraph = bpy.context.evaluated_depsgraph_get()
        depsgraph.update()

    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        eval_obj = bl_object.evaluated_get(depsgraph)
        bl_mesh = eval_obj.to_mesh()

        if bl_mesh is None:
            raise ValueError(f"Could not get mesh data from '{bl_object.name}'")

        try:
            bl_mesh.calc_loop_triangles()
            all_tris = bl_mesh.loop_triangles

            if len(all_tris) == 0:
                raise ValueError(f"Mesh '{bl_object.name}' has no triangles")

            num_slots = len(bl_object.material_slots)
            if num_slots <= 1:
                # Single material — extract as one piece
                result = _extract_from_mesh(bl_mesh, bl_object.name, uv_v_flip)
                result.material_index = 0 if num_slots == 1 else -1
                _extract_blend_data(
                    bl_mesh, bl_object, result,
                    bone_name_to_bm, global_to_local, uv_v_flip,
                    loop_tris=all_tris
                )
                return [result]

            # Group triangles by material_index
            tris_by_mat = {}
            for tri in all_tris:
                mat_idx = tri.material_index
                if mat_idx not in tris_by_mat:
                    tris_by_mat[mat_idx] = []
                tris_by_mat[mat_idx].append(tri)

            # Extract each material group as a skinned submesh
            results = []
            for mat_idx in sorted(tris_by_mat.keys()):
                tris = tris_by_mat[mat_idx]
                mat_name = ""
                if (mat_idx < num_slots and
                        bl_object.material_slots[mat_idx].material):
                    mat_name = bl_object.material_slots[mat_idx].material.name

                name = (f"{bl_object.name}_{mat_name}" if mat_name
                        else f"{bl_object.name}_mat{mat_idx}")

                submesh = _extract_from_triangles(
                    bl_mesh, tris, name, uv_v_flip
                )
                submesh.material_index = mat_idx

                _extract_blend_data(
                    bl_mesh, bl_object, submesh,
                    bone_name_to_bm, global_to_local, uv_v_flip,
                    loop_tris=tris
                )

                results.append(submesh)

            return results
        finally:
            eval_obj.to_mesh_clear()
    finally:
        if old_pose_position is not None:
            armature_obj.data.pose_position = old_pose_position
            bpy.context.view_layer.update()


def _extract_blend_data(bl_mesh, bl_object, mesh_export,
                         bone_name_to_bm, global_to_local, uv_v_flip,
                         loop_tris=None):
    """Extract blend weights and indices for each unique vertex in mesh_export.

    Rebuilds the vertex dedup map to identify which Blender vertex index
    corresponds to each unique exported vertex, then reads vertex group
    weights and maps them to IGB blend indices.

    Args:
        loop_tris: Optional subset of loop triangles to process.
                   Must match the triangles used for the mesh_export extraction.
                   If None, uses all triangles from bl_mesh.
    """
    if loop_tris is None:
        bl_mesh.calc_loop_triangles()
        loop_tris = bl_mesh.loop_triangles
    vertices = bl_mesh.vertices
    loops = bl_mesh.loops

    uv_layer = None
    if bl_mesh.uv_layers.active is not None:
        uv_layer = bl_mesh.uv_layers.active.data

    color_layer = None
    if bl_mesh.color_attributes.active is not None:
        ca = bl_mesh.color_attributes.active
        if ca.domain == 'CORNER':
            color_layer = ca.data

    # Rebuild vertex dedup map to find original vertex index per unique vertex
    vertex_map = {}        # key -> unique_idx
    unique_vert_idx = []   # unique_idx -> Blender vertex index

    for tri in loop_tris:
        for loop_idx in tri.loops:
            loop = loops[loop_idx]
            vert_idx = loop.vertex_index

            if uv_layer is not None:
                uv_data = uv_layer[loop_idx].uv
                u, v = uv_data[0], uv_data[1]
                if uv_v_flip:
                    v = 1.0 - v
                uv = (_round_uv(u), _round_uv(v))
            else:
                uv = (0.0, 0.0)

            if color_layer is not None:
                c = color_layer[loop_idx].color
                color = (
                    _clamp_byte(c[0]),
                    _clamp_byte(c[1]),
                    _clamp_byte(c[2]),
                    _clamp_byte(c[3]),
                )
            else:
                color = (255, 255, 255, 255)

            key = (vert_idx, uv, color)
            if key not in vertex_map:
                unique_idx = len(unique_vert_idx)
                vertex_map[key] = unique_idx
                unique_vert_idx.append(vert_idx)

    # Build vertex group name -> group index map
    vgroup_names = {vg.index: vg.name for vg in bl_object.vertex_groups}

    # Track unmapped bone names and BMS mapping issues (for diagnostics)
    unmapped_bones = set()
    bms_unmapped_count = 0
    zero_weight_verts = 0

    # For each unique vertex, read blend weights from vertex groups
    num_unique = len(unique_vert_idx)
    blend_weights = []
    blend_indices = []

    for ui in range(num_unique):
        bl_vert_idx = unique_vert_idx[ui]
        vert = vertices[bl_vert_idx]

        # Collect all (weight, bm_idx) pairs from vertex groups
        influences = []
        for g in vert.groups:
            group_name = vgroup_names.get(g.group)
            if group_name is None:
                continue
            bm_idx = bone_name_to_bm.get(group_name)
            if bm_idx is None:
                # This bone name isn't in the skeleton's bm_idx mapping
                # (could be a non-deforming bone like root/Bip01, or
                # a bone outside BMS range)
                if group_name and g.weight > 0.001:
                    unmapped_bones.add(group_name)
                continue
            w = g.weight
            if w <= 0.0:
                continue
            # Verify this bm_idx can be reverse-mapped through BMS
            if global_to_local is not None and bm_idx not in global_to_local:
                bms_unmapped_count += 1
                continue  # Skip — this bm_idx isn't in the BMS palette
            influences.append((w, bm_idx))

        # Sort by weight descending, keep top 4
        influences.sort(key=lambda x: -x[0])
        influences = influences[:4]

        # Normalize weights to sum to 1.0
        total_w = sum(w for w, _ in influences)
        if total_w > 0:
            influences = [(w / total_w, bi) for w, bi in influences]
        else:
            zero_weight_verts += 1

        # Pad to exactly 4 influences — fill unused slots with index 0
        # (vanilla body meshes pad with 0: e.g. (5, 0, 0, 0) for 1-bone vertex)
        while len(influences) < 4:
            influences.append((0.0, 0))

        # Map global bm_idx -> local blend index (through reversed BMS)
        weights_out = []
        indices_out = []
        for w, global_bm in influences:
            if global_to_local is not None:
                local_idx = global_to_local.get(global_bm, 0)
            else:
                local_idx = global_bm
            weights_out.append(w)
            indices_out.append(local_idx)

        blend_weights.append(tuple(weights_out))
        blend_indices.append(tuple(indices_out))

    mesh_export.blend_weights = blend_weights
    mesh_export.blend_indices = blend_indices

    # Count actually-weighted vertices (at least one non-zero weight)
    weighted_verts = sum(1 for w in blend_weights if any(x > 0 for x in w))

    # Diagnostics
    if unmapped_bones:
        print(f"SKIN EXPORT [{bl_object.name}]: {len(unmapped_bones)} bone(s) not in "
              f"blend index map: {sorted(unmapped_bones)}")
    if bms_unmapped_count > 0:
        print(f"SKIN EXPORT [{bl_object.name}]: {bms_unmapped_count} influence(s) "
              f"skipped (bm_idx not in BMS palette)")
    if zero_weight_verts > 0:
        print(f"SKIN EXPORT [{bl_object.name}]: {zero_weight_verts}/{num_unique} "
              f"vertex(es) with zero total weight")
    if weighted_verts == 0 and num_unique > 0 and len(vgroup_names) > 0:
        print(f"WARNING [{bl_object.name}]: ALL {num_unique} vertices have zero "
              f"blend weights despite having {len(vgroup_names)} vertex groups! "
              f"Vertex group names may not match skeleton bone names. "
              f"Groups: {sorted(vgroup_names.values())[:5]}... "
              f"Skeleton: {sorted(bone_name_to_bm.keys())[:5]}...")
    print(f"SKIN EXPORT [{bl_object.name}]: {num_unique} unique verts, "
          f"{weighted_verts}/{num_unique} weighted, "
          f"{len(bone_name_to_bm)} mapped bones, "
          f"BMS size={len(global_to_local) if global_to_local else 'N/A'}")


# ===========================================================================
# Triangle strip conversion
# ===========================================================================

def triangles_to_strip(tri_indices):
    """Convert a triangle list to a triangle strip with degenerate separators.

    InsightViewer/XML2 requires prim_type=4 (TriangleStrip). This function
    converts flat triangle list indices [a,b,c, d,e,f, ...] into a single
    triangle strip with degenerate triangles separating each real triangle.

    For each triangle (a,b,c), we emit it as a 3-vertex mini-strip.
    Between consecutive triangles, we insert degenerate connectors:
    repeat the last vertex of the previous triangle and the first vertex
    of the next triangle, plus an extra vertex if needed to fix winding.

    Strip format:
        tri 0: a, b, c
        connector: c, d          (degenerate: c,c,d and c,d,d)
        tri 1: d, e, f           (even position → winding ok)
        connector: f, g
        tri 2: g, h, i
        ...

    Each triangle at strip position P:
      - Even P → winding (v0, v1, v2) = normal
      - Odd P  → winding (v0, v2, v1) = reversed

    After the connector (2 degenerate triangles), the next real triangle
    starts at an even position, so winding is always correct.

    Args:
        tri_indices: flat list of triangle list indices [a,b,c, d,e,f, ...]

    Returns:
        list of int — strip indices with degenerate separators
    """
    num_tris = len(tri_indices) // 3
    if num_tris == 0:
        return []

    if num_tris == 1:
        return list(tri_indices[:3])

    strip = []
    for t in range(num_tris):
        a = tri_indices[t * 3]
        b = tri_indices[t * 3 + 1]
        c = tri_indices[t * 3 + 2]

        if t == 0:
            # First triangle — just emit it
            strip.extend([a, b, c])
        else:
            # Insert degenerate connector: repeat last vertex, then first of new tri
            strip.append(strip[-1])  # repeat last (creates degenerate)
            strip.append(a)          # first of new tri (creates degenerate)

            # After 2 degenerate indices, we've advanced the strip position by 2.
            # The current strip position (0-based triangle index in the strip)
            # determines winding. We need even position for correct winding.
            # Current strip length before adding the triangle = len(strip)
            # Strip position = len(strip) - 2 (the triangle starts at this index)
            strip_pos = len(strip) - 2  # position of the triangle's first vertex
            if strip_pos % 2 != 0:
                # Odd position — add one more degenerate to fix winding
                strip.append(a)

            strip.extend([a, b, c])

    return strip


# ===========================================================================
# Core extraction logic
# ===========================================================================

def _extract_from_mesh(bl_mesh, name, uv_v_flip):
    """Core extraction logic from a Blender Mesh data-block (all triangles).

    Strategy:
    1. Triangulate using calc_loop_triangles()
    2. Build unique vertices by (position_index, uv, color) — normals are NOT
       part of the dedup key because per-loop corner normals differ at shared
       vertices between faces, which would prevent adjacent triangles from
       sharing vertex indices and produce a disconnected "triangle soup".
    3. Average normals across all loops that map to the same unique vertex
    4. Build triangle index list referencing unique vertices
    5. Compute bounding box

    Args:
        bl_mesh: bpy.types.Mesh (already triangulated or will be triangulated)
        name: name string
        uv_v_flip: V-flip flag

    Returns:
        MeshExport
    """
    bl_mesh.calc_loop_triangles()
    loop_tris = bl_mesh.loop_triangles

    if len(loop_tris) == 0:
        raise ValueError(f"Mesh '{name}' has no triangles")

    return _extract_from_triangles(bl_mesh, loop_tris, name, uv_v_flip)


def _extract_from_triangles(bl_mesh, loop_tris, name, uv_v_flip):
    """Extract mesh data from a specific set of loop triangles.

    Used by both _extract_from_mesh (all tris) and extract_mesh_per_material
    (filtered tris per material).

    Args:
        bl_mesh: bpy.types.Mesh
        loop_tris: iterable of loop triangles to process
        name: name string
        uv_v_flip: V-flip flag

    Returns:
        MeshExport
    """
    # Prepare data sources
    vertices = bl_mesh.vertices
    loops = bl_mesh.loops

    # Get UV layer (use active, or first available)
    uv_layer = None
    if bl_mesh.uv_layers.active is not None:
        uv_layer = bl_mesh.uv_layers.active.data

    # Get vertex color layer
    color_layer = None
    if bl_mesh.color_attributes.active is not None:
        ca = bl_mesh.color_attributes.active
        # Only use CORNER-domain color attributes
        if ca.domain == 'CORNER':
            color_layer = ca.data

    # Access corner (per-loop) normals.
    # Blender 4.1+ removed calc_normals_split() and loop.normal.
    # Corner normals are now always available via mesh.corner_normals.
    corner_normals = None
    if hasattr(bl_mesh, 'corner_normals') and len(bl_mesh.corner_normals) > 0:
        corner_normals = bl_mesh.corner_normals
    elif hasattr(bl_mesh, 'has_custom_normals') and bl_mesh.has_custom_normals:
        # Blender < 4.1 fallback
        if hasattr(bl_mesh, 'calc_normals_split'):
            bl_mesh.calc_normals_split()

    # Build unique vertex list
    # Key: (vert_index, uv_tuple, color_tuple) -> unique_index
    # Normals are NOT in the key — they are averaged per unique vertex to
    # preserve proper edge sharing between adjacent triangles.
    vertex_map = {}
    unique_positions = []
    unique_uvs = []
    unique_colors = []
    # Accumulate normals for averaging: unique_idx -> [nx_sum, ny_sum, nz_sum, count]
    normal_accum = []
    indices = []

    for tri in loop_tris:
        for loop_idx in tri.loops:
            loop = loops[loop_idx]
            vert_idx = loop.vertex_index
            vert = vertices[vert_idx]

            # Position
            pos = (vert.co.x, vert.co.y, vert.co.z)

            # Normal (per-loop corner normals preferred, else per-vertex)
            if corner_normals is not None:
                n = corner_normals[loop_idx].vector
                nx, ny, nz = n[0], n[1], n[2]
            elif hasattr(loop, 'normal'):
                # Blender < 4.1: loop.normal available after calc_normals_split()
                n = loop.normal
                nx, ny, nz = n.x, n.y, n.z
            else:
                n = vert.normal
                nx, ny, nz = n.x, n.y, n.z

            # UV
            if uv_layer is not None:
                uv_data = uv_layer[loop_idx].uv
                u, v = uv_data[0], uv_data[1]
                if uv_v_flip:
                    v = 1.0 - v
                uv = (_round_uv(u), _round_uv(v))
            else:
                uv = (0.0, 0.0)

            # Vertex color
            if color_layer is not None:
                c = color_layer[loop_idx].color
                # Convert 0.0-1.0 float to 0-255 int
                color = (
                    _clamp_byte(c[0]),
                    _clamp_byte(c[1]),
                    _clamp_byte(c[2]),
                    _clamp_byte(c[3]),
                )
            else:
                color = (255, 255, 255, 255)

            # Build key for deduplication — no normals!
            key = (vert_idx, uv, color)

            if key not in vertex_map:
                unique_idx = len(unique_positions)
                vertex_map[key] = unique_idx
                unique_positions.append(pos)
                unique_uvs.append(uv)
                unique_colors.append(color)
                normal_accum.append([nx, ny, nz, 1])
            else:
                unique_idx = vertex_map[key]
                # Accumulate normal for averaging
                acc = normal_accum[unique_idx]
                acc[0] += nx
                acc[1] += ny
                acc[2] += nz
                acc[3] += 1

            indices.append(unique_idx)

    # Compute averaged normals and normalize them
    unique_normals = []
    for acc in normal_accum:
        nx, ny, nz = acc[0] / acc[3], acc[1] / acc[3], acc[2] / acc[3]
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length > 1e-8:
            nx /= length
            ny /= length
            nz /= length
        else:
            nx, ny, nz = 0.0, 0.0, 1.0  # fallback up vector
        unique_normals.append((_round_normal(nx), _round_normal(ny), _round_normal(nz)))

    # Check uint16 index limit
    if len(unique_positions) > 65535:
        raise ValueError(
            f"Mesh '{name}' has {len(unique_positions)} unique vertices, "
            f"which exceeds the uint16 index limit (65535). "
            f"Please reduce the mesh complexity."
        )

    # Compute bounding box
    if unique_positions:
        xs = [p[0] for p in unique_positions]
        ys = [p[1] for p in unique_positions]
        zs = [p[2] for p in unique_positions]
        bbox_min = (min(xs), min(ys), min(zs))
        bbox_max = (max(xs), max(ys), max(zs))
    else:
        bbox_min = (0.0, 0.0, 0.0)
        bbox_max = (0.0, 0.0, 0.0)

    # Build result
    result = MeshExport()
    result.positions = unique_positions
    result.normals = unique_normals
    result.uvs = unique_uvs
    result.colors = unique_colors
    result.indices = indices
    result.bbox_min = bbox_min
    result.bbox_max = bbox_max
    result.name = name

    return result


# ===========================================================================
# Helpers
# ===========================================================================

def _round_normal(v):
    """Round normal component to avoid floating point noise in dedup keys."""
    return round(v, 5)


def _round_uv(v):
    """Round UV component to avoid floating point noise in dedup keys."""
    return round(v, 6)


def _clamp_byte(f):
    """Convert 0.0-1.0 float to 0-255 int, clamped."""
    return max(0, min(255, int(f * 255.0 + 0.5)))
