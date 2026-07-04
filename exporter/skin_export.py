"""From-scratch skin export for IGB actor files.

Builds a complete skin IGB file without any template, using SkinBuilder
to construct all meta-objects, scene graph, skeleton, and geometry data
from scratch. This eliminates template dependency and all associated
patching issues.

Pipeline:
    1. Extract skeleton data from armature custom properties
    2. Extract BMS palette from armature custom properties
    3. Extract mesh data from Blender (positions, normals, UVs, blend data)
    4. Extract material/texture data
    5. Build complete IGB via SkinBuilder.build_skin()
    6. Write output IGB
"""

import json
import math
import os
import struct
from typing import Dict, List, Optional, Tuple


def _meshpart_to_v8_tris(mesh_part, with_tangents=False):
    """Expand an indexed MeshExport into v8 non-indexed triangle-list arrays.

    v8 native skins (Max-export form) store geometry as a flat triangle list —
    one entry per triangle vertex — with single-bone rigid skinning (1 weight +
    1 bone index per vertex).

    When `with_tangents` is set (normal-mapped material), a per-vertex tangent +
    binormal frame is computed from the UVs on the *indexed* mesh, then expanded
    alongside the other streams so the v8 builder can emit TANGENT/BINORMAL
    vertex data (the streams MUA's normal-map shader reads).
    """
    pos = mesh_part.positions
    nrm = mesh_part.normals or []
    uv = mesh_part.uvs or []
    bw = mesh_part.blend_weights or []
    bidx = mesh_part.blend_indices or []
    tangents = binormals = None
    if with_tangents and uv:
        from .skin_builder_v4 import _compute_tangent_frame
        tangents, binormals = _compute_tangent_frame(
            pos, nrm, uv, mesh_part.indices)
    # MUA skinning is per-PART: native uses 4-bone weighting (comp_size=4) for
    # skinned meshes but 1-bone (comp_size=1) for rigid props (e.g. guns bound
    # to one hand bone). Match that — emitting weight[0] (often 0.25 of a 4-bone
    # vertex) un-normalized both deforms wrong and is a format the engine's skin
    # path doesn't expect. Pick bones-per-vertex from the actual max influence.
    max_inf = 1
    for vi in mesh_part.indices:
        wv = bw[vi] if (vi < len(bw) and bw[vi]) else (1.0,)
        max_inf = max(max_inf, sum(1 for w in wv if w > 1e-5))
    WPV = 4 if max_inf > 1 else 1
    P, N, U, W, BI, T, B = [], [], [], [], [], [], []
    for vi in mesh_part.indices:
        P.append(pos[vi])
        N.append(nrm[vi] if vi < len(nrm) else (0.0, 0.0, 1.0))
        U.append(uv[vi] if vi < len(uv) else (0.0, 0.0))
        wv = list(bw[vi]) if vi < len(bw) and bw[vi] else [1.0]
        iv = list(bidx[vi]) if vi < len(bidx) and bidx[vi] else [0]
        # keep the WPV strongest influences, sorted by weight descending
        pairs = sorted(zip(wv, iv), key=lambda p: -p[0])[:WPV]
        ws = [float(p[0]) for p in pairs]
        ix = [int(p[1]) & 0xFF for p in pairs]
        while len(ws) < WPV:
            ws.append(0.0)
            ix.append(0)
        s = sum(ws)
        if s > 1e-6:
            ws = [w / s for w in ws]
        else:
            ws = [1.0] + [0.0] * (WPV - 1)
            ix = [ix[0]] + [0] * (WPV - 1)
        W.extend(ws)
        BI.extend(ix)
        if tangents is not None:
            T.append(tangents[vi] if vi < len(tangents) else (1.0, 0.0, 0.0))
            B.append(binormals[vi] if vi < len(binormals) else (0.0, 1.0, 0.0))
    out = {'positions': P, 'normals': N, 'uvs': U, 'weights': W,
           'blend_idx': BI, 'weights_per_vertex': WPV}
    if tangents is not None:
        out['tangents'] = T
        out['binormals'] = B
    return out


def export_skin(filepath, mesh_objs, armature_obj, operator=None, swap_rb=False,
                texture_mode='dxt5', max_texture_size=0, actor_graph='OFF',
                igb_format='V6', use_normal_maps=False, normal_map_size=0,
                use_gloss_map=False, use_mipmaps=True, normal_y_flip=True,
                use_specular_maps=False):
    """Export Blender skin mesh(es) to an IGB file using from-scratch builder.

    Args:
        filepath: Output .igb file path.
        mesh_objs: List of (mesh_obj, is_outline) tuples.
        armature_obj: Blender armature object (with igb_* custom properties).
        operator: Blender operator for reporting.
        swap_rb: If True, swap R/B channels in DXT for MUA PC BGR565 encoding.
        texture_mode: 'dxt5' for DXT5 compressed, 'clut' for 256-color palette (universal).
        max_texture_size: 0 = keep original size; otherwise cap longest edge to this value.
        actor_graph: 'OFF' / 'ANIM' / 'FULL' — how much of the Max-style
            actor/animation graph to emit.
        igb_format: 'V6' (XML2-native layout) or 'V4' (3ds Max 5-era layout,
            mirrors community skins; required for the FULL combiner graph
            that fixes resized-skeleton grounding).

    Returns:
        True on success, False on failure.
    """
    from .skin_builder import SkinBuilder
    from .mesh_extractor import extract_skin_mesh, extract_mesh

    # Fresh per-export texture-compression cache: a texture shared across several
    # submeshes is then compressed once, not once per submesh (the ~2x speedup).
    _reset_texture_compress_cache()

    # Normal maps are a MUA-only feature (DXT diffuse/normal/specular +
    # tangent-frame vertex streams). Both the 3ds Max v4 path and the native v8
    # path carry them; XML2's CLUT/v6 paths cannot — so gate to V4/V8.
    if ((use_normal_maps or use_specular_maps or use_gloss_map)
            and igb_format not in ('V4', 'V8')):
        _report(operator, 'WARNING',
                "Normal/specular/gloss maps require the v4 (3ds Max) or v8 "
                "(MUA native) skin format — ignoring them for this export")
        use_normal_maps = False
        use_specular_maps = False
        use_gloss_map = False

    # Gloss/mask (texture unit 5). Native MUA ships it on v8, but MUA's engine
    # reads texture units by ID regardless of skin-file version, so we also emit
    # it on the v4 path as a 4th unit-5 bind (in-game-verified by the user).
    if use_gloss_map and igb_format not in ('V4', 'V8'):
        _report(operator, 'WARNING',
                "Gloss/mask maps require the v4 (3ds Max) or v8 (MUA native) "
                "skin format — ignoring 'Use Gloss Map' for this export")
        use_gloss_map = False

    if igb_format == 'V4':
        if use_normal_maps or use_specular_maps or use_gloss_map:
            # diffuse/normal/specular/gloss all ship as DXT when any map is on
            texture_mode = 'dxt5'
        elif texture_mode != 'clut':
            _report(operator, 'INFO',
                    "v4 export uses CLUT textures — switching texture mode")
            texture_mode = 'clut'

    if igb_format == 'V8':
        # Native MUA v8 characters use DXT textures (igImage pfmt 14/16) with
        # mipmaps, never CLUT — force DXT so texture_levels get extracted.
        texture_mode = 'dxt5'
        # BUILD STAMP — if you do NOT see this exact line in Blender's export
        # log, Blender is running OLD cached addon code: disable+re-enable the
        # IGB addon (or restart Blender) and re-export.
        # print() (not operator.report) so this lands in the SYSTEM CONSOLE next
        # to the "SKIN EXPORT [...]" lines — the channel you actually watch.
        print(">>> IGB v8 native export — BUILD 2026-06-29-K "
              "(simple 0201 char form + skin-graph fixes: no actor graph) <<<")
        _report(operator, 'INFO',
                "IGB v8 native export — BUILD 2026-06-29-K")

    # Normalize mesh_objs
    if not isinstance(mesh_objs, list):
        mesh_objs = [(mesh_objs, False)]

    # ---- 1. Extract skeleton data from armature custom properties ----
    skeleton_data = _extract_skeleton_from_armature(armature_obj)
    if skeleton_data is None:
        _report(operator, 'ERROR',
                "No skeleton data found on armature. "
                "Re-import the actor with a skin file to store skeleton data.")
        return False

    # ---- 2. Apply export scale to skeleton data ----
    # Combine igb_export_scale with armature's actual object scale
    custom_scale = armature_obj.get("igb_export_scale", 1.0)
    if isinstance(custom_scale, (list, tuple)):
        custom_scale = 1.0
    from mathutils import Vector
    obj_scale = armature_obj.matrix_world.to_scale()
    obj_scale_uniform = (obj_scale.x + obj_scale.y + obj_scale.z) / 3.0
    scale_factor = custom_scale * obj_scale_uniform
    _apply_scale_to_skeleton(skeleton_data, scale_factor)

    # NOTE: No axis rotation is applied to skeleton data here.
    # The rig converter already bakes the game-space rotation (Rz90 @ arm_world_rot)
    # into translations and inv_joint matrices via _get_game_rotation().
    # _transform_mesh_for_export applies the same rotation to mesh vertices.
    # Both are already consistent — no additional rotation needed.

    # ---- 3. Build BMS palette ----
    bms_palette = _extract_bms_palette(armature_obj, skeleton_data)

    _report(operator, 'INFO',
            f"Skeleton: {len(skeleton_data['bones'])} bones, "
            f"{skeleton_data['joint_count']} joints, "
            f"BMS palette: {len(bms_palette)} entries")

    # ---- 3. Build submesh data for the builder ----
    submeshes = []
    total_verts = 0
    total_tris = 0

    for mesh_obj, is_outline in mesh_objs:
        if mesh_obj is None or mesh_obj.type != 'MESH':
            continue

        # Extract mesh data
        # Both main and outline meshes need blend data for skinned animation.
        # Outlines are skinned too (they deform with the character).
        skel_adapter = _SkeletonAdapter(skeleton_data)

        # ALL geometry under igBlendMatrixSelect MUST be skinned.
        # If a mesh has no vertex groups (common for user-added segments),
        # create a temporary vertex group so it goes through the skinned
        # extraction path with correct coordinate-space handling.
        # extract_skin_mesh() forces REST pose and evaluates via depsgraph,
        # giving bind-pose positions; extract_mesh() gives object-local
        # positions which may be in a different space — causing the mesh
        # to be invisible or misplaced in-game.
        import bpy
        temp_vg_name = None
        has_vertex_groups = bool(mesh_obj.vertex_groups)

        if not has_vertex_groups:
            # Find the first bone name that exists in both the skeleton and
            # the armature.  Prefer "Bip01" as the default anchor bone.
            first_bone_name = None
            if armature_obj and armature_obj.type == 'ARMATURE':
                # Try Bip01 first (root bone in XML2 skeletons)
                if "Bip01" in armature_obj.data.bones:
                    first_bone_name = "Bip01"
                else:
                    # Fall back to any bone that has a valid bm_idx
                    for bone_info in skeleton_data.get('bones', []):
                        bname = bone_info.get('name', '')
                        if bone_info.get('bm_idx', -1) >= 0 and bname:
                            if bname in armature_obj.data.bones:
                                first_bone_name = bname
                                break
                    # Last resort: any armature bone
                    if first_bone_name is None and armature_obj.data.bones:
                        first_bone_name = armature_obj.data.bones[0].name

            if first_bone_name:
                _report(operator, 'WARNING',
                        f"Mesh '{mesh_obj.name}' has no vertex groups. "
                        f"Creating temporary weight to bone '{first_bone_name}' "
                        f"so it exports as skinned geometry. For proper "
                        f"deformation, weight-paint to the correct bones.")
                temp_vg_name = first_bone_name
                vg = mesh_obj.vertex_groups.new(name=first_bone_name)
                # Assign ALL vertices with weight 1.0
                vert_count = len(mesh_obj.data.vertices)
                vg.add(list(range(vert_count)), 1.0, 'REPLACE')
            else:
                _report(operator, 'WARNING',
                        f"Mesh '{mesh_obj.name}' has no vertex groups and "
                        f"no matching bone found. Falling back to unskinned "
                        f"extraction — segment may not be visible in-game.")

        # Extract mesh data via the skinned path (forces REST pose, correct
        # coordinate space).  Only fall back to unskinned if we truly have
        # no vertex groups at all.
        #
        # Multi-material meshes: If a mesh has >1 material slots and is not
        # an outline, split it into per-material submeshes so each gets its
        # own texture chain in the IGB file.
        has_vertex_groups = bool(mesh_obj.vertex_groups)
        num_mat_slots = len(mesh_obj.data.materials) if mesh_obj.data.materials else 0

        is_segment = bool(mesh_obj.get('igb_segment_name', ''))
        if (num_mat_slots > 1 and not is_outline and not is_segment
                and has_vertex_groups):
            # Multi-material skinned mesh → split by material slot
            # NOTE: Segments are NOT split — they must stay as a single unit
            # under one igSegment node.  Splitting would create duplicate
            # igSegment nodes with the same name, breaking segment toggling.
            from .mesh_extractor import extract_skin_mesh_per_material
            mesh_parts = extract_skin_mesh_per_material(
                mesh_obj, armature_obj, skel_adapter,
                bms_indices=bms_palette, uv_v_flip=True
            )
            _report(operator, 'INFO',
                    f"Mesh '{mesh_obj.name}': split into {len(mesh_parts)} "
                    f"material groups")
        else:
            # Single material (or outline, or unskinned)
            if has_vertex_groups:
                mesh_export = extract_skin_mesh(
                    mesh_obj, armature_obj, skel_adapter,
                    bms_indices=bms_palette, uv_v_flip=True
                )
            else:
                mesh_export = extract_mesh(mesh_obj, uv_v_flip=True)
            mesh_export.material_index = 0
            mesh_parts = [mesh_export]

        # Clean up temporary vertex group
        if temp_vg_name is not None:
            vg_to_remove = mesh_obj.vertex_groups.get(temp_vg_name)
            if vg_to_remove is not None:
                mesh_obj.vertex_groups.remove(vg_to_remove)

        # Process each mesh part (1 for single-material, N for multi-material)
        for mesh_part in mesh_parts:
            mat_slot = max(0, getattr(mesh_part, 'material_index', 0))

            # IGB skin files store vertices in armature/bind-pose space.
            # igTransform nodes (e.g., on Bishop's drawn guns) are scene graph
            # metadata — they do NOT offset vertex positions.

            # Apply armature world rotation + export scale to vertex data
            _transform_mesh_for_export(mesh_part, armature_obj,
                                       mesh_obj=mesh_obj)

            num_verts = len(mesh_part.positions)
            num_tris = len(mesh_part.indices) // 3
            has_blend = bool(mesh_part.blend_weights)
            # Check if blend data is actually meaningful (not all zeros)
            weighted_verts = 0
            if has_blend:
                weighted_verts = sum(
                    1 for w in mesh_part.blend_weights
                    if any(x > 0 for x in w))

            # Safety net: if extraction produced no blend data, auto-assign
            # to bone 0.
            if num_verts > 0 and not has_blend:
                mesh_part.blend_weights = [(1.0, 0.0, 0.0, 0.0)] * num_verts
                mesh_part.blend_indices = [(0, 0, 0, 0)] * num_verts
                has_blend = True
                weighted_verts = num_verts

            _report(operator, 'INFO',
                    f"{'Outline' if is_outline else 'Main'} "
                    f"'{mesh_part.name}': "
                    f"{num_verts} verts, {num_tris} tris, "
                    f"weighted={weighted_verts}/{num_verts}")

            if has_blend and weighted_verts == 0 and num_verts > 0:
                _report(operator, 'WARNING',
                        f"Mesh '{mesh_part.name}' has vertex groups but ALL "
                        f"blend weights are zero! The mesh will not deform.")

            # Extract material/texture using the correct material slot
            material = _extract_material_props(mesh_obj, mat_slot=mat_slot)
            tex_name = _get_texture_name(mesh_obj, mat_slot=mat_slot)
            sub_dict = {
                'mesh': mesh_part,
                'material': material,
                'texture_name': tex_name,
                'is_outline': is_outline,
                'segment_name': mesh_obj.get('igb_segment_name', ''),
                'segment_flags': mesh_obj.get('igb_segment_flags', 0),
            }

            if texture_mode == 'clut':
                clut_result = _get_texture_clut(
                    mesh_obj, mat_slot=mat_slot,
                    max_texture_size=max_texture_size)
                sub_dict['clut_data'] = clut_result
                sub_dict['texture_levels'] = None
            elif texture_mode == 'cmpr':
                # GameCube/Wii CMPR (GX-tiled DXT1). v6 geometry, pfmt 34.
                sub_dict['cmpr_levels'] = _get_texture_cmpr(
                    mesh_obj, mat_slot=mat_slot,
                    max_texture_size=max_texture_size)
                sub_dict['clut_data'] = None
                sub_dict['texture_levels'] = None
            elif igb_format == 'V8':
                # Native MUA character diffuse is DXT1 (half the size of DXT5);
                # use it when the image is opaque, else fall back to DXT5.
                levels, pfmt = _get_texture(
                    mesh_obj, swap_rb=swap_rb, mat_slot=mat_slot,
                    max_texture_size=max_texture_size, dxt1=True,
                    return_pfmt=True)
                sub_dict['texture_levels'] = levels
                sub_dict['diffuse_pfmt'] = pfmt
                sub_dict['clut_data'] = None
            else:
                # v4 MUA: diffuse = DXT1 when opaque. Native uses DXT1 (pfmt 14)
                # for diffuse + specular and DXT5 only for the normal map, so this
                # halves their size and matches native's pfmt.
                levels, pfmt = _get_texture(
                    mesh_obj, swap_rb=swap_rb, mat_slot=mat_slot,
                    max_texture_size=max_texture_size, dxt1=True, return_pfmt=True)
                sub_dict['texture_levels'] = levels
                sub_dict['diffuse_pfmt'] = pfmt
                sub_dict['clut_data'] = None

            # Per-material "use this map" toggles let the user keep a map in
            # the .blend but leave it out of a given export. Default True.
            _slot_mat = None
            if (mesh_obj.data.materials and
                    mat_slot < len(mesh_obj.data.materials)):
                _slot_mat = mesh_obj.data.materials[mat_slot]

            def _map_on(attr):
                return _slot_mat is None or getattr(_slot_mat, attr, True)

            # Normal-map path (MUA/v4/v8): extract normal + specular maps,
            # DXT-encoded, each gated by its own EXPORT toggle (the per-material
            # igb_use_* flags AND the operator's global tick). Normal and
            # specular are independent, so you can ship one without the other.
            if (use_normal_maps or use_specular_maps) and not is_outline:
                # normal/spec/gloss inherit the global cap, and can only go
                # SMALLER via normal_map_size — never ESCAPE it. So no map
                # slips past the user's "Max Texture Size".
                if normal_map_size and max_texture_size:
                    nm_cap = min(normal_map_size, max_texture_size)
                else:
                    nm_cap = normal_map_size or max_texture_size
                if use_normal_maps and _map_on('igb_use_normal_map'):
                    sub_dict['normal_levels'] = _get_texture_role(
                        mesh_obj, 'normal', swap_rb=swap_rb, mat_slot=mat_slot,
                        max_texture_size=nm_cap, normal_y_flip=normal_y_flip,
                        operator=operator)
                if use_specular_maps and _map_on('igb_use_specular_map'):
                    _spec = _get_texture_role(
                        mesh_obj, 'specular', swap_rb=swap_rb, mat_slot=mat_slot,
                        max_texture_size=nm_cap, dxt1=True, return_pfmt=True,
                        operator=operator)
                    if _spec:
                        sub_dict['specular_levels'], sub_dict['specular_pfmt'] = _spec
                if sub_dict.get('normal_levels'):
                    _report(operator, 'INFO',
                            f"  + normal map for '{mesh_part.name}'"
                            + ('' if not sub_dict.get('specular_levels')
                               else ' (+ specular)'))

            # Gloss/mask (texture unit 5) — v8 only, its own toggle.
            if use_gloss_map and not is_outline and _map_on('igb_use_gloss_map'):
                # normal/spec/gloss inherit the global cap, and can only go
                # SMALLER via normal_map_size — never ESCAPE it. So no map
                # slips past the user's "Max Texture Size".
                if normal_map_size and max_texture_size:
                    nm_cap = min(normal_map_size, max_texture_size)
                else:
                    nm_cap = normal_map_size or max_texture_size
                _gl = _get_texture_role(
                    mesh_obj, 'gloss', swap_rb=swap_rb, mat_slot=mat_slot,
                    max_texture_size=nm_cap, dxt1=True, return_pfmt=True,
                    operator=operator)
                if _gl:
                    sub_dict['gloss_levels'], sub_dict['gloss_pfmt'] = _gl
                if sub_dict.get('gloss_levels'):
                    _report(operator, 'INFO',
                            f"  + gloss/mask map for '{mesh_part.name}'")

            # Honor the Mipmaps toggle by trimming every map to its base level.
            if not use_mipmaps:
                for _k in ('texture_levels', 'normal_levels',
                           'specular_levels', 'gloss_levels', 'cmpr_levels'):
                    lv = sub_dict.get(_k)
                    if lv:
                        sub_dict[_k] = lv[:1]

            submeshes.append(sub_dict)

            total_verts += num_verts
            total_tris += num_tris

    if not submeshes:
        _report(operator, 'ERROR', "No valid mesh objects to export")
        return False

    # ---- 4. Build IGB via SkinBuilder ----
    # Use output filename stem as the "public" name (vanilla convention:
    # 0601.igb uses "0601" for igSkin name, geometry names, etc.)
    export_name = os.path.splitext(os.path.basename(filepath))[0]

    # The igSkeleton must be NAMED. Game actors sometimes ship an unnamed
    # skeleton (loads fine via herostat), but every working MUA mannequin has
    # a "<id>_skel" name — and the character-select viewer appears to need it
    # to set up the skinned actor. Synthesize one from the file id when the
    # imported skeleton carried no name, matching the vanilla convention.
    if skeleton_data and not skeleton_data.get('name'):
        skeleton_data['name'] = f"{export_name}_skel"

    if igb_format == 'V8':
        from .skin_builder_v8 import SkinBuilderV8
        v8_subs = []
        for sub in submeshes:
            if sub.get('is_outline'):
                continue
            # Tangent-frame streams are only needed (and only valid) when a
            # normal map is actually present for this submesh.
            with_tan = bool(sub.get('normal_levels'))
            v8_subs.append({
                'mesh': _meshpart_to_v8_tris(sub['mesh'], with_tangents=with_tan),
                'material': sub.get('material'),
                'texture_levels': sub.get('texture_levels'),
                'diffuse_pfmt': sub.get('diffuse_pfmt', 16),
                'normal_levels': sub.get('normal_levels'),
                'specular_levels': sub.get('specular_levels'),
                'gloss_levels': sub.get('gloss_levels'),
                'texture_name': sub.get('texture_name'),
                'segment_name': sub.get('segment_name', ''),
            })
        builder = SkinBuilderV8()
        # Use the GENUINE-CHARACTER (0201) simple form — igInfoList=[animDB],
        # no actor graph. The engine builds the actor/combiner at runtime from
        # the herostat. This avoids our actor-graph value bugs (wrong combiner
        # quats / identity palette / missing 2nd anim state) entirely. Combined
        # now with the skin-graph fixes (body not segmented) — the untested
        # combination. (full 2103 actor-graph form available via actor_graph=True)
        writer = builder.build_skin(v8_subs, skeleton_data, bms_palette,
                                    export_name=export_name, actor_graph=False)
    elif igb_format == 'V4':
        from .skin_builder_v4 import SkinBuilderV4
        builder = SkinBuilderV4()
        writer = builder.build_skin(
            submeshes, skeleton_data, bms_palette,
            export_name=export_name,
            actor_graph=('FULL' if actor_graph == 'FULL' else 'ANIM'),
            use_normal_maps=use_normal_maps)
    else:
        builder = SkinBuilder()
        writer = builder.build_skin(submeshes, skeleton_data, bms_palette,
                                    export_name=export_name,
                                    actor_graph=actor_graph)

    # ---- 5. Write to disk ----
    writer.write(filepath)

    _report(operator, 'INFO',
            f"Exported skin to {os.path.basename(filepath)} "
            f"({total_verts} verts, {total_tris} tris, "
            f"{len(submeshes)} parts)")

    # ---- 6. Size breakdown report ----
    # Where the bytes went (textures vs geometry vs structure) plus the
    # option that would shrink the file most. Profiling note: our from-
    # scratch v6/v4 output is already at native size parity (0101 CLUT:
    # 1,111,061 vs native 1,109,917 — pools byte-identical), so remaining
    # size wins come from the texture OPTIONS, not the writer.
    _report_size_breakdown(filepath, operator,
                           texture_mode=texture_mode,
                           max_texture_size=max_texture_size,
                           use_mipmaps=use_mipmaps)
    return True


def _report_size_breakdown(filepath, operator, texture_mode='dxt5',
                           max_texture_size=0, use_mipmaps=True):
    """Report texture/geometry/structure byte split + biggest shrink lever."""
    try:
        from ..igb_format.igb_reader import IGBReader
        from ..igb_format.igb_objects import IGBObject, IGBMemoryBlock

        total = os.path.getsize(filepath)
        reader = IGBReader(filepath)
        reader.read()

        def mem_targets(obj):
            out = []
            for _slot, val, fi in obj._raw_fields:
                if fi.short_name == b"MemoryRef" and val != -1:
                    ref = reader.resolve_ref(val)
                    if isinstance(ref, IGBMemoryBlock) and ref.data:
                        out.append(ref)
            return out

        tex_blocks = set()
        geo_blocks = set()
        max_img_edge = 0
        for obj in reader.objects:
            if not isinstance(obj, IGBObject):
                continue
            tn = obj.type_name
            if tn in (b"igImage", b"igClut"):
                for m in mem_targets(obj):
                    tex_blocks.add(m.index)
                if tn == b"igImage":
                    for slot, val, fi in obj._raw_fields:
                        if (slot in (2, 3)
                                and fi.short_name == b"UnsignedInt" and val):
                            max_img_edge = max(max_img_edge, int(val))
            elif (tn.startswith(b"igVertexArray")
                  or tn.startswith(b"igIndexArray")
                  or tn.startswith(b"igPrimLengthArray")):
                for m in mem_targets(obj):
                    geo_blocks.add(m.index)
                    # positions/normals/uvs hang off the 80-byte external
                    # indexed entry rather than the vertex array itself
                    if len(m.data) in (76, 80):
                        import struct as _s
                        for i in range(len(m.data) // 4):
                            idx = _s.unpack_from("<I", m.data, i * 4)[0]
                            if idx != 0xFFFFFFFF and idx < len(reader.objects):
                                r2 = reader.objects[idx]
                                if isinstance(r2, IGBMemoryBlock) and r2.data:
                                    geo_blocks.add(r2.index)

        tex = sum(len(reader.objects[i].data) for i in tex_blocks)
        geo = sum(len(reader.objects[i].data) for i in geo_blocks - tex_blocks)
        other = total - tex - geo

        def pct(n):
            return f"{100.0 * n / max(total, 1):.0f}%"

        _report(operator, 'INFO',
                f"Size {total / 1024:.0f} KB — textures {tex / 1024:.0f} KB "
                f"({pct(tex)}), geometry {geo / 1024:.0f} KB ({pct(geo)}), "
                f"structure {other / 1024:.0f} KB ({pct(other)})")

        # one actionable hint, biggest lever first
        if tex > total * 0.4:
            if max_texture_size == 0 and max_img_edge > 512:
                _report(operator, 'INFO',
                        f"Hint: textures up to {max_img_edge}px — 'Max "
                        f"Texture Size' 512 would cut texture bytes "
                        f"~{(1 - (512.0 / max_img_edge) ** 2) * 100:.0f}%")
            elif texture_mode == 'clut':
                _report(operator, 'INFO',
                        "Hint: DXT texture mode halves opaque textures "
                        "(DXT1) vs CLUT — measured ~16% smaller file on "
                        "0101 Cyclops")
            elif use_mipmaps:
                _report(operator, 'INFO',
                        "Hint: disabling mipmaps saves ~33% of texture "
                        "bytes (in-game distant shimmer tradeoff)")
    except Exception as e:
        # Reporting must never fail an export
        print(f"[IGB] size report skipped: {e}")


# ============================================================================
# Export-time transforms (rotation + scale)
# ============================================================================

def _transform_mesh_for_export(mesh_export, armature_obj, mesh_obj=None):
    """Transform vertex data from Blender armature-local space to game space.

    Applies:
    0. Mesh-to-armature relative transform — extracted positions are in the
       MESH object's local space, but all downstream math (rotation, export
       scale, skeleton translations) lives in the ARMATURE's space. When the
       mesh object's world transform differs from the armature's (common in
       FBX rips where meshes aren't armature children and carry their own
       scale), skipping this step exports wildly wrong sizes/orientations.
       Identity when both transforms match, so existing flows are unchanged.
    1. Armature world rotation — converts Y-up FBX imports to Z-up game space.
       For native XML2 actors (identity rotation) this is a no-op.
    2. Blender-to-XML2 axis conversion — for converted rigs (igb_converted_rig),
       applies +90° Z rotation to convert Blender convention (X=right, -Y=forward)
       to XML2 game convention (X=forward, Y=left).  Native IGB imports are already
       in game convention and don't need this.
    3. Export scale factor — stored as 'igb_export_scale' custom property
       on the armature by the rig converter.

    Modifies mesh_export in place (positions, normals, bounding box).
    """
    from mathutils import Vector, Quaternion, Matrix
    import math

    # ---- 0. Mesh-local -> armature-local relative transform ----
    rel_matrix = None
    if mesh_obj is not None:
        try:
            rel = armature_obj.matrix_world.inverted() @ mesh_obj.matrix_world
        except ValueError:
            rel = None  # non-invertible armature matrix — skip
        if rel is not None:
            is_identity = all(
                abs(rel[r][c] - (1.0 if r == c else 0.0)) < 1e-5
                for r in range(4) for c in range(4)
            )
            if not is_identity:
                rel_matrix = rel

    if rel_matrix is not None:
        rel_normal = rel_matrix.to_3x3().inverted_safe().transposed()
        for i, (x, y, z) in enumerate(mesh_export.positions):
            v = rel_matrix @ Vector((x, y, z))
            mesh_export.positions[i] = (v.x, v.y, v.z)
        if mesh_export.normals:
            for i, (nx, ny, nz) in enumerate(mesh_export.normals):
                n = rel_normal @ Vector((nx, ny, nz))
                n.normalize()
                mesh_export.normals[i] = (n.x, n.y, n.z)

    # Get armature world rotation
    arm_rot_q = armature_obj.matrix_world.to_quaternion()
    identity_q = Quaternion()
    has_rotation = arm_rot_q.rotation_difference(identity_q).angle > 0.001

    # For converted rigs with native skeleton data, add a +90° Z rotation
    # to convert from Blender axis convention to XML2 game convention.
    # Blender: X=right, -Y=forward, Z=up  →  XML2: X=forward, Y=left, Z=up
    # +90° Z rotation: (x, y, z) → (-y, x, z)
    converted_rig = armature_obj.get("igb_converted_rig", False)
    if converted_rig:
        # Build combined rotation: Rz(+90°) @ world_rotation
        rz90 = Quaternion((0, 0, 1), math.radians(90))
        combined_q = rz90 @ arm_rot_q
        arm_rot_q = combined_q
        has_rotation = True

    # Get export scale factor: combine igb_export_scale custom property
    # with the armature's actual object scale (uniform axis average).
    # This way, if the user scales the armature in Object Mode, the
    # exported mesh reflects that scale.
    custom_scale = armature_obj.get("igb_export_scale", 1.0)
    if isinstance(custom_scale, (list, tuple)):
        custom_scale = 1.0
    obj_scale = armature_obj.matrix_world.to_scale()
    obj_scale_uniform = (obj_scale.x + obj_scale.y + obj_scale.z) / 3.0
    scale_factor = custom_scale * obj_scale_uniform
    has_scale = abs(scale_factor - 1.0) > 0.001

    if not has_rotation and not has_scale and rel_matrix is None:
        return  # Nothing to do

    arm_rot_mat = arm_rot_q.to_matrix()  # 3x3

    # Transform vertex positions: rotate then scale
    for i, (x, y, z) in enumerate(mesh_export.positions):
        v = Vector((x, y, z))
        if has_rotation:
            v = arm_rot_mat @ v
        if has_scale:
            v = v * scale_factor
        mesh_export.positions[i] = (v.x, v.y, v.z)

    # Transform normals: rotate only (normals don't scale)
    if has_rotation and mesh_export.normals:
        for i, (nx, ny, nz) in enumerate(mesh_export.normals):
            n = arm_rot_mat @ Vector((nx, ny, nz))
            mesh_export.normals[i] = (n.x, n.y, n.z)

    # Recompute bounding box
    if mesh_export.positions:
        xs = [p[0] for p in mesh_export.positions]
        ys = [p[1] for p in mesh_export.positions]
        zs = [p[2] for p in mesh_export.positions]
        mesh_export.bbox_min = (min(xs), min(ys), min(zs))
        mesh_export.bbox_max = (max(xs), max(ys), max(zs))


def _apply_scale_to_skeleton(skeleton_data, scale_factor):
    """Apply export scale factor to skeleton translations and inv_bind matrices.

    For translations: multiply by scale_factor.
    For inv_bind matrices: scale the translation row (last row in row-major).
    The inverse of a scaled bind matrix [R | S*p] is [R^T | S*(-R^T*p)],
    so the translation in the inverse is multiplied by S.
    """
    if abs(scale_factor - 1.0) <= 0.001:
        return

    for bone in skeleton_data['bones']:
        # Scale translations
        tx, ty, tz = bone['translation']
        bone['translation'] = (tx * scale_factor, ty * scale_factor,
                               tz * scale_factor)

        # Scale inv_joint_matrix translation row
        ijm = bone.get('inv_joint_matrix')
        if ijm is not None:
            m = list(ijm)
            # Row-major: last row = [tx, ty, tz, 1.0] at indices 12, 13, 14
            m[12] *= scale_factor
            m[13] *= scale_factor
            m[14] *= scale_factor
            bone['inv_joint_matrix'] = m


def _apply_rotation_to_skeleton(skeleton_data, armature_obj):
    """Apply axis rotation to skeleton data to match mesh vertex rotation.

    _transform_mesh_for_export() applies Rz(+90°) @ arm_world_rot to vertices
    for converted rigs. The skeleton translations and inv_joint matrices must
    receive the same rotation so the exported skeleton matches the rotated mesh.

    For native IGB imports (identity rotation, igb_converted_rig=False), this
    is a no-op.

    Translations are parent-local offsets used in accumulated-translation mode
    (no FK rotation). To produce correct rotated world positions when summing
    translations, each translation vector must be rotated.

    Inv_joint matrices map world → bone space. After rotating world by R:
        new_IJM = old_IJM @ R^{-1}  (column-major / Blender convention)
    """
    from mathutils import Quaternion, Matrix, Vector
    import math

    # Compute the same rotation as _transform_mesh_for_export
    arm_rot_q = armature_obj.matrix_world.to_quaternion()

    converted_rig = armature_obj.get("igb_converted_rig", False)
    if converted_rig:
        rz90 = Quaternion((0, 0, 1), math.radians(90))
        combined_q = rz90 @ arm_rot_q
    else:
        combined_q = arm_rot_q

    # Check if rotation is significant
    identity_q = Quaternion()
    if combined_q.rotation_difference(identity_q).angle < 0.001:
        return  # No rotation needed (native XML2 import case)

    rot_mat_3x3 = combined_q.to_matrix()  # 3x3
    rot_mat_4x4 = rot_mat_3x3.to_4x4()
    rot_inv_4x4 = rot_mat_4x4.inverted()

    for bone in skeleton_data['bones']:
        # Rotate translations (parent-local offsets)
        tx, ty, tz = bone['translation']
        v = rot_mat_3x3 @ Vector((tx, ty, tz))
        bone['translation'] = (v.x, v.y, v.z)

        # Rotate inv_joint matrices
        ijm = bone.get('inv_joint_matrix')
        if ijm is not None:
            # Convert row-major IGB → Blender column-major (transpose)
            ijm_cm = Matrix((
                (ijm[0], ijm[4], ijm[8],  ijm[12]),
                (ijm[1], ijm[5], ijm[9],  ijm[13]),
                (ijm[2], ijm[6], ijm[10], ijm[14]),
                (ijm[3], ijm[7], ijm[11], ijm[15]),
            ))

            # new_IJM = old_IJM @ R^{-1}
            new_ijm_cm = ijm_cm @ rot_inv_4x4

            # Convert back to row-major (transpose)
            bone['inv_joint_matrix'] = [
                new_ijm_cm[0][0], new_ijm_cm[1][0], new_ijm_cm[2][0], new_ijm_cm[3][0],
                new_ijm_cm[0][1], new_ijm_cm[1][1], new_ijm_cm[2][1], new_ijm_cm[3][1],
                new_ijm_cm[0][2], new_ijm_cm[1][2], new_ijm_cm[2][2], new_ijm_cm[3][2],
                new_ijm_cm[0][3], new_ijm_cm[1][3], new_ijm_cm[2][3], new_ijm_cm[3][3],
            ]


# ============================================================================
# Skeleton data extraction from armature custom properties
# ============================================================================

def _extract_skeleton_from_armature(armature_obj):
    """Extract skeleton data from armature custom properties.

    These properties are stored during import by _store_skin_skeleton_data()
    in actor_import.py.

    CRITICAL: Uses the stored bone info list (igb_skin_bone_info_list) which
    preserves the EXACT vanilla bone ordering, parent indices, bm_idx values,
    and flags. This avoids bone reordering issues from Blender's internal
    bone iteration order.

    Returns:
        dict with skeleton_data format, or None if data not found.
    """
    # Check for required properties
    if "igb_skin_bone_translations" not in armature_obj:
        return None

    name = armature_obj.get("igb_skin_skeleton_name", "")
    joint_count = armature_obj.get("igb_skin_joint_count", 0)

    # Parse per-bone data from JSON
    translations = json.loads(armature_obj["igb_skin_bone_translations"])
    inv_matrices_raw = json.loads(armature_obj["igb_skin_inv_joint_matrices"])

    # Use stored bone info list if available (preserves exact vanilla ordering)
    stored_bone_info = armature_obj.get("igb_skin_bone_info_list")
    if stored_bone_info:
        bone_info_list = json.loads(stored_bone_info)
        bones = []
        for bi in bone_info_list:
            bone_idx = bi['index']

            # Get translation from stored data
            if bone_idx < len(translations):
                trans = tuple(translations[bone_idx])
            else:
                trans = (0.0, 0.0, 0.0)

            # Get inv_joint_matrix from stored data
            inv_matrix = None
            if bone_idx < len(inv_matrices_raw) and inv_matrices_raw[bone_idx] is not None:
                inv_matrix = inv_matrices_raw[bone_idx]

            bones.append({
                'name': bi['name'],         # ORIGINAL vanilla name
                'index': bone_idx,
                'parent_idx': bi['parent_idx'],  # ORIGINAL parent index
                'bm_idx': bi['bm_idx'],     # ORIGINAL bm_idx (including -1!)
                'flags': bi['flags'],       # ORIGINAL flags
                'translation': trans,
                'inv_joint_matrix': inv_matrix,
            })

        if not bones:
            return None

        return {
            'name': name,
            'joint_count': joint_count,
            'bones': bones,
        }

    # Fallback: reconstruct from pose bone properties (legacy, less accurate)
    bones = []
    for pb in armature_obj.pose.bones:
        bone_idx = pb.get("igb_bone_index", -1)
        if bone_idx < 0:
            continue

        parent_idx = pb.get("igb_parent_idx", -1)
        bm_idx = pb.get("igb_skin_bm_idx", pb.get("igb_bm_idx", bone_idx))
        flags = pb.get("igb_flags", 0)

        if bone_idx < len(translations):
            trans = tuple(translations[bone_idx])
        else:
            trans = (0.0, 0.0, 0.0)

        inv_matrix = None
        if bone_idx < len(inv_matrices_raw) and inv_matrices_raw[bone_idx] is not None:
            inv_matrix = inv_matrices_raw[bone_idx]

        bones.append({
            'name': pb.name,
            'index': bone_idx,
            'parent_idx': parent_idx,
            'bm_idx': bm_idx,
            'flags': flags,
            'translation': trans,
            'inv_joint_matrix': inv_matrix,
        })

    bones.sort(key=lambda b: b['index'])

    if not bones:
        return None

    return {
        'name': name,
        'joint_count': joint_count,
        'bones': bones,
    }


def _extract_bms_palette(armature_obj, skeleton_data):
    """Extract or build the BMS palette.

    If stored during import, use it. Otherwise, build an identity palette
    for all deforming bones (those with bm_idx >= 0).
    """
    # Try stored palette first
    stored = armature_obj.get("igb_bms_palette")
    if stored:
        return json.loads(stored)

    # Build identity palette: [0, 1, 2, ..., joint_count-1]
    joint_count = skeleton_data.get('joint_count', 0)
    if joint_count > 0:
        return list(range(joint_count))

    # Fallback: collect all unique bm_idx values
    bm_indices = set()
    for bone in skeleton_data['bones']:
        bm = bone['bm_idx']
        if bm >= 0:
            bm_indices.add(bm)

    return sorted(bm_indices)


# ============================================================================
# Skeleton adapter for extract_skin_mesh compatibility
# ============================================================================

class _SkeletonAdapter:
    """Lightweight adapter that makes skeleton_data dict look like ParsedSkeleton.

    extract_skin_mesh() expects a ParsedSkeleton-like object with:
    - bones: list with .name, .index, .bm_idx attributes
    - build_bm_to_bone_map(): returns dict mapping bm_idx -> bone_name
    - get_effective_bm_idx(bone_idx): returns bm_idx
    """

    class _BoneAdapter:
        def __init__(self, bone_dict):
            self.name = bone_dict['name']
            self.index = bone_dict['index']
            self.bm_idx = bone_dict['bm_idx']
            self.parent_idx = bone_dict['parent_idx']
            self.flags = bone_dict['flags']
            self.translation = bone_dict['translation']
            self.inv_joint_matrix = bone_dict.get('inv_joint_matrix')

    def __init__(self, skeleton_data):
        self.name = skeleton_data.get('name', '')
        self.joint_count = skeleton_data.get('joint_count', 0)
        self.bones = [self._BoneAdapter(b) for b in skeleton_data['bones']]

    def get_effective_bm_idx(self, bone_idx):
        if bone_idx < len(self.bones):
            bm = self.bones[bone_idx].bm_idx
            return bm if bm >= 0 else bone_idx
        return bone_idx

    def build_bm_to_bone_map(self):
        mapping = {}
        for bone in self.bones:
            bm = self.get_effective_bm_idx(bone.index)
            mapping[bm] = bone.name
        return mapping


# ============================================================================
# Material / texture extraction
# ============================================================================

def _extract_material_props(mesh_obj, mat_slot=0):
    """Extract material properties from a Blender mesh object.

    Reads both core material properties (diffuse, specular, etc.) and
    IGB render state custom properties (blend, alpha, color, lighting,
    cull face) set by the IGB Materials panel.

    Args:
        mesh_obj: Blender mesh object.
        mat_slot: Material slot index to read from (default 0).

    Returns:
        dict with material properties for the skin builder.
    """
    import bpy

    defaults = {
        'diffuse': (0.8, 0.8, 0.8, 1.0),
        'ambient': (0.8, 0.8, 0.8, 1.0),
        'specular': (0.0, 0.0, 0.0, 1.0),
        'emission': (0.0, 0.0, 0.0, 1.0),
        'shininess': 0.0,
        'flags': 31,
        'priority': 0,
    }

    if not mesh_obj.data.materials or mat_slot >= len(mesh_obj.data.materials):
        return dict(defaults)

    mat = mesh_obj.data.materials[mat_slot]
    if mat is None:
        return dict(defaults)

    # Try to read from custom properties stored during import
    diffuse = mat.get("igb_diffuse")
    if diffuse:
        result = {
            'diffuse': tuple(diffuse),
            'ambient': tuple(mat.get("igb_ambient", (0.8, 0.8, 0.8, 1.0))),
            'specular': tuple(mat.get("igb_specular", (0.0, 0.0, 0.0, 1.0))),
            'emission': tuple(mat.get("igb_emission", (0.0, 0.0, 0.0, 1.0))),
            'shininess': mat.get("igb_shininess", 0.0),
            'flags': mat.get("igb_flags", 31),
            'priority': int(mat.get("igb_priority", 0)),
        }
    elif mat.use_nodes and mat.node_tree:
        # Fallback: extract from Principled BSDF
        result = dict(defaults)
        for node in mat.node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                bc = node.inputs.get('Base Color')
                if bc:
                    c = bc.default_value
                    result['diffuse'] = (c[0], c[1], c[2], c[3])
                    result['ambient'] = (c[0], c[1], c[2], c[3])
                break
    else:
        result = dict(defaults)

    # Read IGB render state custom properties (set by IGB Materials panel)
    # These override the skin builder's default render state attrs.
    _read_igb_render_state(mat, result)

    return result


def _read_igb_render_state(mat, result):
    """Read IGB render state custom properties from a Blender material.

    Populates result dict with 'color_attr', 'blend_state', 'blend_func',
    'alpha_state', 'alpha_func', 'lighting', 'cull_face' sub-dicts
    if the corresponding igb_* properties are found on the material.
    """
    # Color attribute (tint)
    if "igb_color_r" in mat:
        result['color_attr'] = (
            mat.get("igb_color_r", 1.0),
            mat.get("igb_color_g", 1.0),
            mat.get("igb_color_b", 1.0),
            mat.get("igb_color_a", 1.0),
        )

    # Blend state. The friendly "Blend Mode" enum (igb_blend_mode) maps to the
    # exact igBlendFunctionAttr src/dst pairs; it wins over any raw blend props.
    # ONE=1, SRC_ALPHA=4, ONE_MINUS_SRC_ALPHA=5.
    _BLEND_MODES = {
        'OPAQUE':        (False, 4, 5),
        'ALPHA':         (True,  4, 5),   # SRC_ALPHA, ONE_MINUS_SRC_ALPHA
        'ADDITIVE':      (True,  4, 1),   # SRC_ALPHA, ONE
        'INV_ALPHA_ADD': (True,  5, 1),   # ONE_MINUS_SRC_ALPHA, ONE
    }
    # The dropdown is AUTHORITATIVE for every mode, including OPAQUE. OPAQUE
    # MUST force blend off — otherwise it falls through to the raw
    # igb_blend_enabled prop, which is commonly True on imported MUA materials
    # (the game ships igBlendStateAttr enabled even on solid skins), so the
    # model would export see-through despite the user picking Opaque.
    mode = getattr(mat, "igb_blend_mode", "OPAQUE")
    if mode in _BLEND_MODES:
        en, src, dst = _BLEND_MODES[mode]
        result['blend_enabled'] = en
        result['blend_src'] = src
        result['blend_dst'] = dst
    elif "igb_blend_enabled" in mat:
        result['blend_enabled'] = bool(mat["igb_blend_enabled"])
        result['blend_src'] = mat.get("igb_blend_src", 4)
        result['blend_dst'] = mat.get("igb_blend_dst", 5)

    # Alpha test
    if "igb_alpha_test_enabled" in mat:
        result['alpha_test_enabled'] = bool(mat["igb_alpha_test_enabled"])
        result['alpha_func'] = mat.get("igb_alpha_func", 6)
        result['alpha_ref'] = mat.get("igb_alpha_ref", 0.5)

    # Lighting
    if "igb_lighting_enabled" in mat:
        result['lighting_enabled'] = bool(mat["igb_lighting_enabled"])

    # Cull face
    if "igb_cull_face_enabled" in mat:
        result['cull_face_enabled'] = bool(mat["igb_cull_face_enabled"])
        result['cull_face_mode'] = mat.get("igb_cull_face_mode", 0)


# Per-export cache of compressed texture levels, keyed by (image, params).
# A skin's body atlas is typically shared across several submeshes (body, cape,
# weapon segments...), and each submesh used to re-compress it from scratch —
# ~47% of a skin export was redundant DXT of the same 1024² images. Memoizing by
# image identity makes each unique texture compress exactly once per export.
# Reset at the top of export_skin so edits to a texture recompress next time.
_TEX_COMPRESS_CACHE = {}


def _reset_texture_compress_cache():
    _TEX_COMPRESS_CACHE.clear()


def _get_texture(mesh_obj, swap_rb=False, mat_slot=0, max_texture_size=0,
                 dxt1=False, return_pfmt=False):
    """Extract texture data from a Blender mesh object's material.

    Uses the same approach as the map file exporter:
    1. Find Image Texture via BSDF Base Color link (or fallback any TEX_IMAGE)
    2. Extract RGBA pixels with Y-flip (Blender OpenGL → DXT/IGB convention)
    3. Ensure power-of-2 dimensions
    4. DXT5 compress with mipmaps (or DXT1 when dxt1=True AND fully opaque —
       native MUA character diffuse is DXT1, half the size)

    Args:
        mesh_obj: Blender mesh object.
        swap_rb: If True, swap R/B channels for MUA PC BGR565 encoding.
        mat_slot: Material slot index to read from (default 0).
        max_texture_size: 0 = keep original size; otherwise cap longest edge.
        dxt1: prefer DXT1 (pfmt 14) when the image is fully opaque.
        return_pfmt: when True, return (levels, pfmt) instead of just levels.

    Returns list of (compressed_bytes, width, height) tuples (or with pfmt),
    or None.
    """
    import bpy
    from ..utils.dxt_compress import (
        compress_with_mipmaps, compress_with_mipmaps_dxt1, rgba_is_opaque)

    if not mesh_obj.data.materials or mat_slot >= len(mesh_obj.data.materials):
        return None

    mat = mesh_obj.data.materials[mat_slot]
    if mat is None or not mat.use_nodes or not mat.node_tree:
        return None

    # Diffuse image via the group's Base Texture / BSDF Base Color — never the
    # normal/spec/gloss map (fixes the normal-map-as-main-texture bug).
    bl_image = _find_diffuse_image(mat)

    if bl_image is None:
        return None

    w, h = bl_image.size[0], bl_image.size[1]
    if w == 0 or h == 0:
        return None

    ck = ('diffuse', bl_image.name, w, h, bool(swap_rb),
          int(max_texture_size), bool(dxt1))
    cached = _TEX_COMPRESS_CACHE.get(ck)
    if cached is not None:
        levels, pfmt = cached
        return (levels, pfmt) if return_pfmt else levels

    rgba, w, h = _extract_image_rgba(bl_image, w, h, max_texture_size=max_texture_size)

    if dxt1 and rgba_is_opaque(rgba):
        levels = compress_with_mipmaps_dxt1(bytes(rgba), w, h, swap_rb=swap_rb)
        pfmt = 14  # RGBA_DXT1 — matches native MUA character diffuse
    else:
        levels = compress_with_mipmaps(bytes(rgba), w, h, swap_rb=swap_rb)
        pfmt = 16  # RGBA_DXT5
    _TEX_COMPRESS_CACHE[ck] = (levels, pfmt)
    if return_pfmt:
        return levels, pfmt
    return levels


def _find_role_image(mat, role):
    """Find the Image feeding a Normal Map / Specular slot in a BSDF tree.

    role 'normal': BSDF 'Normal' input <- Normal Map node <- Image Texture
    (or a direct Image Texture). role 'specular': image into 'Specular IOR
    Level' (or legacy 'Specular'). Returns a bpy Image or None.
    """
    # A material with 'Use Nodes' off (e.g. FBX/OBJ-imported, or hand-created)
    # has node_tree == None. Guard here so every caller — incl. the map-export
    # role path that calls this directly — is safe on any material.
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return None
    nodes = mat.node_tree.nodes
    # The shader may be a raw Principled BSDF OR the ".IGB Material" node group
    # (Principled lives inside it). Earlier this only matched BSDF_PRINCIPLED, so
    # normal/specular maps wired into the group were never found and never
    # exported even with "Use Normal Maps" on.
    shader = next((n for n in nodes
                   if n.type == 'BSDF_PRINCIPLED'
                   or (n.type == 'GROUP' and n.node_tree is not None
                       and n.node_tree.name == '.IGB Material')), None)
    if shader is None:
        return None
    if role == 'normal':
        inp = shader.inputs.get('Normal')
        if inp and inp.is_linked:
            src = inp.links[0].from_node
            if src.type == 'NORMAL_MAP':
                col = src.inputs.get('Color')
                if col and col.is_linked:
                    img_node = col.links[0].from_node
                    if img_node.type == 'TEX_IMAGE' and img_node.image:
                        return img_node.image
            elif src.type == 'TEX_IMAGE' and src.image:
                return src.image
        return None
    if role == 'specular':
        # Group input is "Specular Texture"; raw BSDF is "Specular IOR Level"
        for name in ('Specular Texture', 'Specular IOR Level', 'Specular'):
            inp = shader.inputs.get(name)
            if inp and inp.is_linked:
                src = inp.links[0].from_node
                if src.type == 'TEX_IMAGE' and src.image:
                    return src.image
        return None
    if role == 'gloss':
        # Gloss/mask (texture unit 5). The .IGB Material group exposes a
        # "Gloss/Mask Texture" input; fall back to a raw BSDF link or a
        # node named to hint gloss/mask if the group input is absent.
        for name in ('Gloss/Mask Texture', 'Gloss Texture', 'Gloss',
                     'Sheen Tint'):
            inp = shader.inputs.get(name)
            if inp and inp.is_linked:
                src = inp.links[0].from_node
                if src.type == 'TEX_IMAGE' and src.image:
                    return src.image
        return None
    return None


def _find_diffuse_image(mat):
    """Find the DIFFUSE (base colour) image, robust to the .IGB Material group.

    Checks the group's 'Base Texture' input and a raw BSDF 'Base Color' FIRST,
    so it never grabs the normal/specular/gloss map by falling through to "any
    texture node" — the bug where the NORMAL map became the main texture after
    Convert-to-IGB replaced the BSDF with the group (which has no top-level
    BSDF_PRINCIPLED, so the old finder found none and took the first TEX_IMAGE,
    often the normal). Only if no base socket is wired does it fall back to a
    TEX_IMAGE that is NOT a role (normal/spec/gloss) map.
    """
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return None
    nodes = mat.node_tree.nodes
    shader = next((n for n in nodes
                   if n.type == 'BSDF_PRINCIPLED'
                   or (n.type == 'GROUP' and n.node_tree is not None
                       and n.node_tree.name == '.IGB Material')), None)
    if shader is not None:
        for sock in ('Base Texture', 'Base Color'):
            inp = shader.inputs.get(sock)
            if inp and inp.is_linked:
                src = inp.links[0].from_node
                if src.type == 'TEX_IMAGE' and src.image:
                    return src.image
    # Fallback: a TEX_IMAGE that isn't a role (normal/spec/gloss) map.
    role_names = set()
    for r in ('normal', 'specular', 'gloss'):
        ri = _find_role_image(mat, r)
        if ri is not None:
            role_names.add(ri.name)
    for node in nodes:
        if (node.type == 'TEX_IMAGE' and node.image
                and node.image.name not in role_names):
            return node.image
    return None


def convert_normal_rgba_to_mua(src, normal_y_flip=True):
    """Repack an RGBA8 normal map into MUA's DXT5nm channel layout.

    Native MUA normal maps (decoded from 0104bladecybernetic: R=B~0,
    G~126=normal.Y, A 0..255=normal.X): the Cg bump shader reads X from ALPHA
    and Y from GREEN and reconstructs Z, so a plain RGB (blue) normal would
    have its opaque alpha read as X -> wrong bumps. Repack: X -> alpha,
    Y -> green (flipped to DirectX when normal_y_flip), R/B = 0.

    The source's X can live in RED (a standard blue tangent-space normal:
    R=X, G=Y, B=Z~255) OR already in ALPHA (a map imported from a native MUA
    DXT5nm file, which has R=B=0) — detected by sampling the R/B means, so
    both convert correctly and the operation is idempotent.

    Returns a new bytearray (RGBA8, same size)."""
    try:
        import numpy as np
    except ImportError:
        np = None

    if np is not None:
        a = np.frombuffer(bytes(src), dtype=np.uint8).reshape(-1, 4)
        step = max(1, len(a) // 4096)
        r_mean = float(a[::step, 0].mean())
        b_mean = float(a[::step, 2].mean())
        x_in_alpha = (r_mean < 12.0 and b_mean < 12.0)
        out = np.zeros_like(a)
        nx = a[:, 3] if x_in_alpha else a[:, 0]        # X from alpha or red
        ny = a[:, 1]                                   # Y from green
        out[:, 1] = (255 - ny) if normal_y_flip else ny  # Y in green
        out[:, 3] = nx                                 # X in alpha (DXT5nm)
        return bytearray(out.tobytes())

    # pure-python fallback (no numpy)
    n = len(src) // 4
    step = max(1, n // 4096)
    idxs = range(0, n, step)
    cnt = len(idxs) or 1
    r_mean = sum(src[i * 4] for i in idxs) / cnt
    b_mean = sum(src[i * 4 + 2] for i in idxs) / cnt
    x_in_alpha = (r_mean < 12.0 and b_mean < 12.0)
    out = bytearray(len(src))
    for px in range(0, len(src), 4):
        nx = src[px + 3] if x_in_alpha else src[px]   # X from alpha or red
        ny = src[px + 1]                               # Y from green
        out[px + 1] = (255 - ny) if normal_y_flip else ny  # Y in green
        out[px + 3] = nx                               # X in alpha (DXT5nm)
    return out


def looks_like_normal_map(rgba):
    """Heuristic: does this RGBA8 buffer look like a tangent-space normal map?

    A blue tangent normal has BLUE as the dominant channel (flat = 128,128,255).
    A native MUA DXT5nm normal has R=B~0 with a varying alpha (X in alpha). A
    grayscale colour map (specular / gloss) has R==G==B and is NEVER a normal.

    Used to stop a mis-wired specular/gloss (sitting on the material's Normal
    input) from being silently repacked into a green-black DXT5nm map — the
    "specular is green-black" bug. Real normals (blue OR DXT5nm) pass; grayscale
    colour maps are rejected.
    """
    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None:
        a = np.frombuffer(bytes(rgba), dtype=np.uint8).reshape(-1, 4)
        step = max(1, len(a) // 4096)
        s = a[::step, :3].astype(np.float32)
        r, g, b = float(s[:, 0].mean()), float(s[:, 1].mean()), float(s[:, 2].mean())
    else:
        n = len(rgba) // 4
        step = max(1, n // 4096)
        idx = range(0, n, step)
        c = len(idx) or 1
        r = sum(rgba[i * 4] for i in idx) / c
        g = sum(rgba[i * 4 + 1] for i in idx) / c
        b = sum(rgba[i * 4 + 2] for i in idx) / c
    # Already MUA DXT5nm (R and B near zero, X packed in alpha).
    if r < 12.0 and b < 12.0:
        return True
    # Standard blue tangent-space normal: blue clearly dominant and high.
    if b > 90.0 and b >= r + 6.0 and b >= g + 6.0:
        return True
    # Grayscale / colour map (spec, gloss, diffuse) — not a normal.
    return False


def _get_texture_role(mesh_obj, role, swap_rb=False, mat_slot=0,
                      max_texture_size=0, normal_y_flip=True,
                      dxt1=False, return_pfmt=False, operator=None):
    """Extract a normal/specular/gloss map as DXT levels, or None if absent.

    Normal maps are repacked to DXT5nm (X->alpha, Y->green, Z reconstructed).
    MUA's Cg bump shader expects DirectX (green = Y-down); the importer flips
    native green to Blender's OpenGL (Y-up) for a correct viewport, so the
    DEFAULT here flips it back to DirectX (normal_y_flip=True) — verified to
    match native byte-for-byte. Turn OFF only for a map that is already DirectX
    in Blender (no importer round-trip). Specular passes through unchanged.
    """
    from ..utils.dxt_compress import (
        compress_with_mipmaps, compress_with_mipmaps_dxt1, rgba_is_opaque)

    if not mesh_obj.data.materials or mat_slot >= len(mesh_obj.data.materials):
        return None
    mat = mesh_obj.data.materials[mat_slot]
    if mat is None or not mat.use_nodes or not mat.node_tree:
        return None
    bl_image = _find_role_image(mat, role)
    if bl_image is None:
        return None
    w, h = bl_image.size[0], bl_image.size[1]
    if w == 0 or h == 0:
        return None

    ck = ('role', bl_image.name, w, h, role, bool(swap_rb),
          int(max_texture_size), bool(dxt1), bool(normal_y_flip))
    cached = _TEX_COMPRESS_CACHE.get(ck)
    if cached is not None:
        levels, pfmt = cached
        if levels is None:      # cached "skip" (e.g. non-normal on Normal input)
            return None
        return (levels, pfmt) if return_pfmt else levels

    rgba, w, h = _extract_image_rgba(bl_image, w, h,
                                     max_texture_size=max_texture_size)
    if role == 'normal':
        # A grayscale/colour map mis-wired onto the material's Normal input would
        # be repacked to DXT5nm (R=0,B=0,green-only) — the "specular is
        # green-black" bug. Refuse to convert an image that isn't a normal map,
        # and tell the user why, instead of silently producing garbage.
        if not looks_like_normal_map(rgba):
            _report(operator, 'WARNING',
                    f"Material '{mat.name}': the image '{bl_image.name}' on the "
                    f"Normal input isn't a normal map (looks like a colour/"
                    f"grayscale map) — skipping normal export. If this is your "
                    f"specular/gloss map, wire it to the Specular / Gloss input "
                    f"instead of Normal.")
            _TEX_COMPRESS_CACHE[ck] = (None, None)
            return None
        rgba = convert_normal_rgba_to_mua(rgba, normal_y_flip=normal_y_flip)
        # Normal map MUST stay DXT5 (alpha carries X).
        levels = compress_with_mipmaps(bytes(rgba), w, h, swap_rb=False)
        pfmt = 16
    # Specular / gloss (plain colour maps): DXT1 when opaque — native uses DXT1
    # (pfmt 14) for these; fall back to DXT5 only if the map carries alpha.
    elif dxt1 and rgba_is_opaque(rgba):
        levels = compress_with_mipmaps_dxt1(bytes(rgba), w, h, swap_rb=swap_rb)
        pfmt = 14
    else:
        levels = compress_with_mipmaps(bytes(rgba), w, h, swap_rb=swap_rb)
        pfmt = 16
    _TEX_COMPRESS_CACHE[ck] = (levels, pfmt)
    return (levels, pfmt) if return_pfmt else levels


def _get_texture_clut(mesh_obj, mat_slot=0, max_texture_size=0):
    """Extract texture as CLUT palette + indices for universal PS2-style format.

    Same image extraction as _get_texture, but quantizes to 256 colors
    instead of DXT-compressing.

    Args:
        mesh_obj: Blender mesh object.
        mat_slot: Material slot index to read from (default 0).

    Returns (palette_data, index_data, w, h) or None.
    """
    import bpy
    from ..utils.clut_compress import quantize_rgba_to_clut

    if not mesh_obj.data.materials or mat_slot >= len(mesh_obj.data.materials):
        return None

    mat = mesh_obj.data.materials[mat_slot]
    if mat is None or not mat.use_nodes or not mat.node_tree:
        return None

    # Diffuse image (group Base Texture / BSDF Base Color; never a role map).
    bl_image = _find_diffuse_image(mat)
    if bl_image is None:
        return None

    w, h = bl_image.size[0], bl_image.size[1]
    if w == 0 or h == 0:
        return None

    rgba, w, h = _extract_image_rgba(bl_image, w, h, max_texture_size=max_texture_size)

    palette_data, index_data = quantize_rgba_to_clut(bytes(rgba), w, h)
    return (palette_data, index_data, w, h)


def _get_texture_cmpr(mesh_obj, mat_slot=0, max_texture_size=0):
    """Extract the diffuse texture as a GameCube/Wii CMPR byte stream.

    Same RGBA extraction as _get_texture_clut, then GX-tiled DXT1 (CMPR, pfmt
    34). Returns [(cmpr_bytes, w, h)] (single base level) or None.
    """
    import bpy
    from ..utils.wii_cmpr_compress import compress_rgba_to_cmpr

    if not mesh_obj.data.materials or mat_slot >= len(mesh_obj.data.materials):
        return None
    mat = mesh_obj.data.materials[mat_slot]
    if mat is None or not mat.use_nodes or not mat.node_tree:
        return None

    # Diffuse image (group Base Texture / BSDF Base Color; never a role map).
    bl_image = _find_diffuse_image(mat)
    if bl_image is None:
        return None

    w, h = bl_image.size[0], bl_image.size[1]
    if w == 0 or h == 0:
        return None
    rgba, w, h = _extract_image_rgba(bl_image, w, h, max_texture_size=max_texture_size)
    return [(compress_rgba_to_cmpr(bytes(rgba), w, h), w, h)]


def _extract_image_rgba(bl_image, w, h, max_texture_size=0):
    """Extract RGBA uint8 pixels from a Blender image with Y-flip and POT resize.

    Uses numpy (bundled with Blender) for fast vectorized processing.
    A 256x256 texture processes in <1ms vs ~200ms with pure Python loops.

    Args:
        bl_image: Blender Image object.
        w: Image width.
        h: Image height.
        max_texture_size: 0 = keep original size; otherwise cap longest edge.

    Returns:
        (rgba_bytes, final_w, final_h) tuple.
    """
    # Ensure pixel data is loaded. After undo, Blender may have unloaded
    # the pixel buffer from memory. For file-backed images, reload() will
    # re-read from disk. For packed images, the data is already in memory.
    if bl_image.filepath and not bl_image.packed_file:
        try:
            bl_image.reload()
        except RuntimeError:
            pass  # File might not exist; pixels.foreach_get will give zeros

    try:
        import numpy as np
        return _extract_image_rgba_numpy(bl_image, w, h, np,
                                         max_texture_size=max_texture_size)
    except ImportError:
        return _extract_image_rgba_python(bl_image, w, h,
                                          max_texture_size=max_texture_size)


def _extract_image_rgba_numpy(bl_image, w, h, np, max_texture_size=0):
    """Numpy-vectorized pixel extraction with Y-flip and POT resize."""
    # bl_image.pixels is a flat float array [r,g,b,a, r,g,b,a, ...]
    # Grab directly into numpy — avoids creating a Python list
    pixels = np.zeros(w * h * 4, dtype=np.float32)
    bl_image.pixels.foreach_get(pixels)

    # Reshape to (h, w, 4), flip Y axis, convert float [0,1] → uint8 [0,255]
    pixels = pixels.reshape(h, w, 4)
    pixels = pixels[::-1]  # Y-flip (Blender bottom-up → IGB top-down)
    rgba = np.clip(pixels * 255.0 + 0.5, 0, 255).astype(np.uint8)

    # Choose target dimensions: cap to max_texture_size if requested, else POT-up
    target_w, target_h = _target_dims(w, h, max_texture_size)
    if target_w != w or target_h != h:
        # Nearest-neighbor resize via index mapping
        src_y = np.minimum(np.arange(target_h) * h // target_h, h - 1)
        src_x = np.minimum(np.arange(target_w) * w // target_w, w - 1)
        rgba = rgba[np.ix_(src_y, src_x)]
        w, h = target_w, target_h

    return bytes(rgba.tobytes()), w, h


def _extract_image_rgba_python(bl_image, w, h, max_texture_size=0):
    """Pure Python fallback for pixel extraction."""
    pixels = list(bl_image.pixels)
    num_pixels = w * h
    rgba = bytearray(num_pixels * 4)
    for i in range(num_pixels):
        src_y = i // w
        src_x = i % w
        dst_y = h - 1 - src_y
        dst_idx = (dst_y * w + src_x) * 4
        src_idx = i * 4
        rgba[dst_idx + 0] = max(0, min(255, int(pixels[src_idx + 0] * 255 + 0.5)))
        rgba[dst_idx + 1] = max(0, min(255, int(pixels[src_idx + 1] * 255 + 0.5)))
        rgba[dst_idx + 2] = max(0, min(255, int(pixels[src_idx + 2] * 255 + 0.5)))
        rgba[dst_idx + 3] = max(0, min(255, int(pixels[src_idx + 3] * 255 + 0.5)))

    # Choose target dimensions: cap to max_texture_size if requested, else POT-up
    target_w, target_h = _target_dims(w, h, max_texture_size)
    if target_w != w or target_h != h:
        new_rgba = bytearray(target_w * target_h * 4)
        for y in range(target_h):
            src_y = min(y * h // target_h, h - 1)
            for x in range(target_w):
                src_x = min(x * w // target_w, w - 1)
                src_off = (src_y * w + src_x) * 4
                dst_off = (y * target_w + x) * 4
                new_rgba[dst_off:dst_off + 4] = rgba[src_off:src_off + 4]
        rgba = new_rgba
        w, h = target_w, target_h

    return bytes(rgba), w, h


def _target_dims(w, h, max_texture_size):
    """Compute final (width, height) for export.

    Both dimensions snap to power-of-2 (DXT requirement). When max_texture_size
    is set, the longer edge is capped to it (rounding down to POT), and the
    shorter edge scales proportionally.
    """
    pot_w = _next_power_of_2(w)
    pot_h = _next_power_of_2(h)
    if max_texture_size <= 0:
        return pot_w, pot_h
    if pot_w <= max_texture_size and pot_h <= max_texture_size:
        return pot_w, pot_h
    # Snap cap to power-of-2 (round down so we never exceed it)
    cap = _prev_power_of_2(max_texture_size)
    if pot_w >= pot_h:
        new_w = cap
        new_h = max(1, _prev_power_of_2((pot_h * cap) // pot_w))
    else:
        new_h = cap
        new_w = max(1, _prev_power_of_2((pot_w * cap) // pot_h))
    return new_w, new_h


def _next_power_of_2(n):
    """Return the smallest power of 2 >= n."""
    if n <= 0:
        return 1
    p = 1
    while p < n:
        p <<= 1
    return p


def _prev_power_of_2(n):
    """Return the largest power of 2 <= n (n >= 1)."""
    if n <= 1:
        return 1
    p = 1
    while p * 2 <= n:
        p <<= 1
    return p


def _get_texture_name(mesh_obj, mat_slot=0):
    """Get the texture image name from a mesh object's material.

    Args:
        mesh_obj: Blender mesh object.
        mat_slot: Material slot index to read from (default 0).
    """
    import bpy

    if not mesh_obj.data.materials or mat_slot >= len(mesh_obj.data.materials):
        return ''

    mat = mesh_obj.data.materials[mat_slot]
    if mat is None or not mat.use_nodes or not mat.node_tree:
        return ''

    for node in mat.node_tree.nodes:
        if node.type == 'TEX_IMAGE' and node.image:
            return node.image.name

    return ''


# ============================================================================
# Helpers
# ============================================================================

def _report(operator, level, msg):
    """Report a message through the operator if available."""
    try:
        from ..utils.igb_log import log as _igb_log
        _igb_log(level, msg)
    except Exception:
        pass
    if operator:
        operator.report({level}, msg)
