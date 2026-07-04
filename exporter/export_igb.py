"""Standalone IGB file export for Blender.

Exports all mesh objects in the scene as an Alchemy Engine IGB file.
No template file needed — builds the IGB from scratch using IGBBuilder.

Pipeline:
    1. Collect all mesh objects in the scene (excluding "Colliders" collection)
    2. For each object, extract per-material submeshes (mesh_extractor)
    3. For each submesh:
       - Get Blender material properties
       - Extract texture image → RGBA → DXT5 compress with mipmaps
    4. Based on collision_source setting:
       - "Colliders Collection": build collision from 'Colliders' collection objects
       - "Visual Mesh": build collision from the same visible mesh objects
       - "None": skip collision entirely
    5. IGBBuilder.build(all_submesh_data, collision_data) → IGBWriter
    6. Write to disk

Supports multi-object scenes and multi-material meshes: every material
slot on every object gets its own igAttrSet branch in the scene graph
with independent geometry, material, and texture.

Collision: Objects in a "Colliders" collection are always excluded from
visual geometry. Collision hull source is controlled by the collision_source
setting (Colliders Collection, Visual Mesh, or None).
"""

import bpy
import os
import time


def export_igb(context, filepath, operator=None, objects=None,
               bake_transforms=False):
    """Export all scene meshes as a standalone IGB file.

    Args:
        context: Blender context
        filepath: output .igb file path
        operator: the export operator (for options and error reporting)
        objects: optional explicit list of mesh objects to export INSTEAD of
            scanning the scene (used by Export Mannequin — meshes are read
            through the evaluated depsgraph, so armature poses bake in)
        bake_transforms: when True, bake each object's world matrix into its
            geometry (so a mesh with a non-identity object transform — e.g. an
            FBX prop — exports correctly oriented/placed). Default False keeps
            the map exporter's historical local-space behavior byte-identical.

    Returns:
        {'FINISHED'} or {'CANCELLED'}
    """
    t_start = time.time()

    # Read collision options from operator
    collision_source = 'COLLIDERS'  # default for backward compat
    surface_type = 0
    swap_rb = False
    texture_mode = 'dxt5'  # 'dxt5' or 'clut'
    max_texture_size = 0  # 0 = original size; otherwise 256/512/1024
    if operator is not None:
        if hasattr(operator, 'collision_source'):
            collision_source = operator.collision_source
        elif hasattr(operator, 'export_collision'):
            # Legacy backward compat: old BoolProperty -> new enum
            collision_source = 'COLLIDERS' if operator.export_collision else 'NONE'
        if hasattr(operator, 'surface_type'):
            surface_type = operator.surface_type
        # Texture format: new texture_format property or legacy game_preset
        if hasattr(operator, 'texture_format'):
            fmt = operator.texture_format
            if fmt == 'clut':
                texture_mode = 'clut'
                swap_rb = False
            elif fmt == 'dxt5_mua':
                texture_mode = 'dxt5'
                swap_rb = True
            else:  # dxt5_xml2
                texture_mode = 'dxt5'
                swap_rb = False
        elif hasattr(operator, 'game_preset'):
            swap_rb = 'mua' in operator.game_preset
        # Texture resolution cap (operator passes string enum: 'original', '1024', ...)
        if hasattr(operator, 'texture_resolution'):
            try:
                max_texture_size = int(operator.texture_resolution)
            except (TypeError, ValueError):
                max_texture_size = 0  # 'original' or anything non-numeric
        if max_texture_size > 0:
            _report(operator, 'INFO',
                    f"Texture resolution cap: {max_texture_size}px on longest edge")

    # Normal/spec/gloss role maps (MUA only — reads the same per-material
    # wiring as the Actor tab: the .IGB Material group or a raw BSDF). Each is
    # an independent EXPORT toggle so the user can drop any of them.
    use_normal_maps = bool(getattr(operator, 'use_normal_maps', False))
    use_specular_maps = bool(getattr(operator, 'use_specular_maps',
                                     use_normal_maps))
    use_gloss_map = bool(getattr(operator, 'use_gloss_map', False))
    normal_y_flip = bool(getattr(operator, 'normal_y_flip', True))
    if texture_mode == 'clut' or not swap_rb:
        # Role maps are an MUA DXT5 feature; XML2/CLUT paths have none.
        use_normal_maps = False
        use_specular_maps = False
        use_gloss_map = False

    # Build set of objects to EXCLUDE from visual export:
    # 1. "Colliders" collection (collision-only geometry)
    # 2. Any collection prefixed with "[MapMaker]" (editor-only objects)
    excluded_objects = set()

    colliders_coll = bpy.data.collections.get("Colliders")
    if colliders_coll is not None:
        for obj in colliders_coll.objects:
            if obj.type == 'MESH':
                excluded_objects.add(obj)
        collider_count = sum(1 for o in colliders_coll.objects if o.type == 'MESH')
        if collider_count:
            _report(operator, 'INFO',
                    f"Found {collider_count} collider object(s) "
                    f"in 'Colliders' collection")

    # Exclude all [MapMaker] collections (entities, automap, etc.)
    mapmaker_excluded = 0
    for coll in bpy.data.collections:
        if coll.name.startswith("[MapMaker]"):
            for obj in coll.objects:
                excluded_objects.add(obj)
                mapmaker_excluded += 1
    if mapmaker_excluded:
        _report(operator, 'INFO',
                f"Excluding {mapmaker_excluded} [MapMaker] object(s) from export")

    # Separate collider objects for collision hull building
    collider_objects = set()
    if colliders_coll is not None:
        for obj in colliders_coll.objects:
            if obj.type == 'MESH':
                collider_objects.add(obj)

    # Collect all VISIBLE mesh objects EXCLUDING colliders, [MapMaker], and
    # hidden objects — or use the explicit list when one was provided.
    if objects is not None:
        mesh_objects = [obj for obj in objects if obj.type == 'MESH']
    else:
        mesh_objects = [
            obj for obj in context.scene.objects
            if obj.type == 'MESH'
            and obj not in excluded_objects
            and not obj.hide_viewport
        ]

    if not mesh_objects:
        _report(operator, 'ERROR',
                "No visible mesh objects in the scene. Add a mesh to export.")
        return {'CANCELLED'}

    # Log which objects will be exported so the user can verify
    obj_names = [obj.name for obj in mesh_objects]
    _report(operator, 'INFO',
            f"Exporting {len(mesh_objects)} visual mesh object(s) to IGB: {obj_names}")

    # Step 1: Extract per-material submeshes from ALL objects
    from .mesh_extractor import extract_mesh_per_material
    from ..utils.dxt_compress import compress_with_mipmaps

    # Cache compressed textures by Blender image name to avoid
    # re-compressing the same texture for multiple objects/materials
    texture_cache = {}  # image_name -> (texture_levels, texture_name)

    builder_submeshes = []
    total_objects = 0
    total_submeshes = 0

    for obj in mesh_objects:
        _report(operator, 'INFO', f"  Object '{obj.name}':")

        try:
            wm = obj.matrix_world if bake_transforms else None
            submeshes = extract_mesh_per_material(obj, uv_v_flip=True,
                                                  world_matrix=wm)
        except ValueError as e:
            _report(operator, 'WARNING',
                    f"    Skipping '{obj.name}': {e}")
            continue
        except Exception as e:
            _report(operator, 'WARNING',
                    f"    Skipping '{obj.name}': unexpected error: {e}")
            continue

        total_objects += 1

        for sub_mesh in submeshes:
            total_tris = len(sub_mesh.indices) // 3
            _report(operator, 'INFO',
                    f"    Submesh '{sub_mesh.name}': {len(sub_mesh.positions)} verts, "
                    f"{total_tris} tris, mat_idx={sub_mesh.material_index}")

            # Get Blender material for this submesh
            bl_mat = None
            if (sub_mesh.material_index >= 0 and
                    sub_mesh.material_index < len(obj.material_slots)):
                bl_mat = obj.material_slots[sub_mesh.material_index].material

            # Extract material properties
            if bl_mat is not None:
                material_props = _extract_material_props(bl_mat)
            else:
                material_props = _default_material()

            # Extract and compress texture(s) (with caching).
            # Role maps (normal/spec/gloss) read the SAME per-material wiring
            # as the Actor tab — skin_export._find_role_image handles both
            # the .IGB Material group and a raw Principled BSDF — and honor
            # the per-material igb_use_* toggles (default on). MUA units:
            # 0=diffuse, 1=normal, 2=specular, 5=gloss/mask.
            role_stages = []
            if bl_mat is not None and (use_normal_maps or use_specular_maps
                                       or use_gloss_map):
                from .skin_export import _find_role_image

                def _map_on(attr):
                    return bool(getattr(bl_mat, attr, True))

                role_defs = []
                if use_normal_maps and _map_on('igb_use_normal_map'):
                    role_defs.append(('normal', 1))
                if use_specular_maps and _map_on('igb_use_specular_map'):
                    role_defs.append(('specular', 2))
                if use_gloss_map and _map_on('igb_use_gloss_map'):
                    role_defs.append(('gloss', 5))
                for role, unit_id in role_defs:
                    role_img = _find_role_image(bl_mat, role)
                    if role_img is None:
                        continue
                    tex_levels, tex_name = _get_texture_for_image(
                        role_img, texture_cache, operator,
                        swap_rb=swap_rb, role=role,
                        normal_y_flip=normal_y_flip,
                        max_texture_size=max_texture_size,
                    )
                    if tex_levels is None:
                        # e.g. a non-normal image on the Normal input was
                        # skipped (warned above) — don't emit an empty stage.
                        continue
                    role_stages.append((tex_levels, tex_name, unit_id))
                    _report(operator, 'INFO',
                            f"      Texture stage {unit_id} ({role}): "
                            f"{tex_name}")

            if texture_mode == 'clut':
                # CLUT mode: only supports single diffuse texture
                clut_data, texture_name = _get_texture_clut_for_material(
                    bl_mat, texture_cache, operator,
                    max_texture_size=max_texture_size,
                )
                builder_submeshes.append({
                    'mesh': sub_mesh,
                    'material': material_props,
                    'material_state': material_props.get('material_state', {}),
                    'clut_data': clut_data,
                    'texture_levels': None,
                    'texture_name': texture_name,
                })
            elif role_stages:
                # Multi-texture path: diffuse on unit 0 + the role maps.
                # normal_mapped makes the builder emit the tangent-frame
                # vertex streams MUA's bump shader needs.
                texture_levels, texture_name = _get_texture_for_material(
                    bl_mat, texture_cache, operator, swap_rb=swap_rb,
                    max_texture_size=max_texture_size,
                )
                builder_submeshes.append({
                    'mesh': sub_mesh,
                    'material': material_props,
                    'material_state': material_props.get('material_state', {}),
                    'texture_stages': ([(texture_levels, texture_name, 0)]
                                       + role_stages),
                    'normal_mapped': any(u == 1 for _, _, u in role_stages),
                })
            else:
                # Single-texture path (most common)
                texture_levels, texture_name = _get_texture_for_material(
                    bl_mat, texture_cache, operator, swap_rb=swap_rb,
                    max_texture_size=max_texture_size,
                )
                builder_submeshes.append({
                    'mesh': sub_mesh,
                    'material': material_props,
                    'material_state': material_props.get('material_state', {}),
                    'texture_levels': texture_levels,
                    'texture_name': texture_name,
                })
            total_submeshes += 1

    if not builder_submeshes:
        _report(operator, 'ERROR',
                "No valid mesh data found in scene objects.")
        return {'CANCELLED'}

    # Honor the Mipmaps toggle: drop every texture to its base level (~33%
    # smaller). Same trim the skin exporter does.
    use_mipmaps = bool(getattr(operator, 'use_mipmaps', True))
    if not use_mipmaps:
        for sub in builder_submeshes:
            lv = sub.get('texture_levels')
            if lv:
                sub['texture_levels'] = lv[:1]
            stages = sub.get('texture_stages')
            if stages:
                sub['texture_stages'] = [
                    ((levels[:1] if levels else levels), name, unit)
                    for (levels, name, unit) in stages]

    _report(operator, 'INFO',
            f"  Total: {total_objects} objects, {total_submeshes} submeshes")

    # Step 2: Build collision data based on collision_source setting
    collision_data = None
    collision_objects = None

    if collision_source == 'COLLIDERS' and collider_objects:
        collision_objects = list(collider_objects)
        _report(operator, 'INFO',
                f"Building collision hull from Colliders collection "
                f"({len(collision_objects)} objects, surface_type={surface_type})...")
    elif collision_source == 'VISUAL':
        collision_objects = list(mesh_objects)
        _report(operator, 'INFO',
                f"Building collision hull from visual mesh "
                f"({len(collision_objects)} objects, surface_type={surface_type})...")

    if collision_objects:
        from .collide_hull import build_collision_data

        try:
            collision_data = build_collision_data(
                collision_objects, surface_type=surface_type)
            if collision_data is not None:
                num_tris = collision_data['num_triangles']
                num_nodes = collision_data['num_tree_nodes_minus_1'] + 1
                _report(operator, 'INFO',
                        f"  Collision: {num_tris} triangles, {num_nodes} BVH nodes")
                if num_tris > 17000:
                    _report(operator, 'WARNING',
                            f"  Collision has {num_tris} tris — game files max ~17000! "
                            f"Consider using Generate Box/Hull Colliders in the IGB panel")
            else:
                _report(operator, 'WARNING',
                        "  No collision triangles extracted")
        except Exception as e:
            import traceback
            traceback.print_exc()
            _report(operator, 'WARNING',
                    f"  Collision hull build failed: {e}")
            collision_data = None

    # Step 2.5: Collect scene lights
    export_lights = True
    if operator is not None and hasattr(operator, 'export_lights'):
        export_lights = operator.export_lights

    light_data_list = []
    if export_lights:
        light_data_list = _collect_scene_lights(context)

        # Add SceneAmbient from world background color if available
        world = context.scene.world
        if world and world.use_nodes:
            for node in world.node_tree.nodes:
                if node.type == 'BACKGROUND':
                    c = node.inputs['Color'].default_value
                    if c[0] > 0.001 or c[1] > 0.001 or c[2] > 0.001:
                        light_data_list.insert(0, {
                            'name': 'SceneAmbient',
                            'type': 0,  # DIRECTIONAL
                            'position': (0.0, 0.0, 0.0),
                            'direction': (0.0, 0.0, -1.0),
                            'diffuse': (c[0], c[1], c[2], 1.0),
                            'ambient': (c[0], c[1], c[2], 1.0),
                            'specular': (0.0, 0.0, 0.0, 1.0),
                            'attenuation': (1.0, 0.0, 0.0),
                            'falloff': 0.0,
                            'cutoff': 180.0,
                        })
                    break

        if light_data_list:
            _report(operator, 'INFO',
                    f"Exporting {len(light_data_list)} light(s)")

    # Step 3: Build IGB via IGBBuilder
    from .igb_builder import IGBBuilder

    try:
        builder = IGBBuilder()
        writer = builder.build(
            builder_submeshes,
            collision_data=collision_data,
            lights=light_data_list if light_data_list else None,
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        _report(operator, 'ERROR', f"IGB build failed: {e}")
        return {'CANCELLED'}

    # Step 4: Write to disk
    try:
        writer.write(filepath)
    except Exception as e:
        import traceback
        traceback.print_exc()
        _report(operator, 'ERROR', f"IGB write failed: {e}")
        return {'CANCELLED'}

    t_elapsed = time.time() - t_start
    file_size = os.path.getsize(filepath)
    collision_msg = ""
    if collision_data is not None:
        collision_msg = f", collision={collision_data['num_triangles']} tris"
    light_msg = ""
    if light_data_list:
        light_msg = f", {len(light_data_list)} lights"
    _report(operator, 'INFO',
            f"Exported {total_objects} object(s) ({total_submeshes} submeshes"
            f"{collision_msg}{light_msg}) "
            f"to {os.path.basename(filepath)} "
            f"({file_size:,} bytes, {t_elapsed:.2f}s)")

    return {'FINISHED'}


# ===========================================================================
# Light collection
# ===========================================================================

def _collect_scene_lights(context):
    """Collect all visible light objects from the scene.

    Converts Blender light properties to Alchemy igLightAttr format:
        SUN  -> DIRECTIONAL (type 0)
        POINT -> POINT (type 1)
        SPOT -> SPOT (type 2)

    Energy is converted back to attenuation coefficients using the
    inverse of the IGBConverter import formulas.

    Returns:
        list of dicts suitable for IGBBuilder.build(lights=...)
    """
    import math

    lights = []
    for obj in context.scene.objects:
        if obj.type != 'LIGHT' or obj.hide_viewport:
            continue

        light = obj.data

        # Map Blender type -> Alchemy type
        type_map = {'SUN': 0, 'POINT': 1, 'SPOT': 2, 'AREA': 1}
        light_type = type_map.get(light.type, 1)

        # Position from object location
        pos = obj.location
        position = (pos.x, pos.y, pos.z)

        # Direction from object's -Z axis (Blender lights emit along local -Z)
        from mathutils import Vector
        direction = (obj.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized()
        direction = (direction.x, direction.y, direction.z)

        # In Alchemy, brightness is baked into the diffuse color RGB.
        # Blender separates color (0-1 normalized) from energy (watts).
        # We recombine them: diffuse = color * (energy / 10.0)
        # The /10 matches the *10 multiplier on import.
        c = light.color
        energy = max(light.energy, 0.001)
        brightness = energy / 10.0
        # Clamp to 0-1 range (Alchemy uses 0-1 color values)
        diffuse = (
            min(1.0, c[0] * brightness),
            min(1.0, c[1] * brightness),
            min(1.0, c[2] * brightness),
            1.0,
        )
        specular = diffuse
        ambient = (0.0, 0.0, 0.0, 1.0)

        # Attenuation — use the same values as the original game files
        if light_type == 0:
            # Directional: constant attenuation, no distance falloff
            attenuation = (1.0, 0.0, 0.0)
            cutoff = 180.0
        elif light_type == 1:
            # Point: quadratic attenuation (matches game files)
            attenuation = (0.0, 0.0, 0.0001)
            cutoff = -0.5
        else:
            # Spot: quadratic attenuation
            attenuation = (0.0, 0.0, 0.0001)
            cutoff = math.degrees(light.spot_size) / 2.0

        # Spot falloff
        falloff = 0.0
        if light_type == 2:
            # spot_blend: 0 = sharp edge, 1 = fully soft
            # inner_ratio = 1.0 - spot_blend
            # From import: inner_ratio = max(0, 1 - falloff*30)
            # So: falloff = (1 - inner_ratio) / 30 = spot_blend / 30
            falloff = light.spot_blend / 30.0

        # Override defaults with Alchemy-specific custom properties if present
        # (stored on import for lossless round-trip)
        if "ig_ambient" in obj:
            ambient = tuple(obj["ig_ambient"])
        if "ig_specular" in obj:
            specular = tuple(obj["ig_specular"])
        if "ig_attenuation" in obj:
            attenuation = tuple(obj["ig_attenuation"])

        shininess = obj.get("ig_shininess", 0.0)
        light_id = obj.get("ig_light_id", 0)
        cast_shadow = light.use_shadow

        light_name = obj.name
        lights.append({
            'name': light_name,
            'type': light_type,
            'position': position,
            'direction': direction,
            'diffuse': diffuse,
            'ambient': ambient,
            'specular': specular,
            'attenuation': attenuation,
            'falloff': falloff,
            'cutoff': cutoff,
            'shininess': shininess,
            'light_id': light_id,
            'cast_shadow': cast_shadow,
        })

    return lights


# ===========================================================================
# Texture extraction with caching
# ===========================================================================

def _get_texture_for_material(bl_mat, texture_cache, operator, swap_rb=False,
                              max_texture_size=0):
    """Get compressed texture levels for a material, using cache.

    Args:
        max_texture_size: 0 = keep original size; otherwise cap longest edge.

    Returns:
        (texture_levels, texture_name)
    """
    from ..utils.dxt_compress import compress_with_mipmaps

    texture_levels = None
    texture_name = ''

    if bl_mat is not None:
        bl_image = _find_texture_image(bl_mat)
        if bl_image is not None:
            texture_name = bl_image.name

            # Cache key includes the cap so different caps don't collide
            cache_key = f"{texture_name}@{max_texture_size}"
            if cache_key in texture_cache:
                cached_levels, cached_name = texture_cache[cache_key]
                _report(operator, 'INFO',
                        f"      Texture: {texture_name} (cached)")
                return cached_levels, cached_name

            _report(operator, 'INFO',
                    f"      Texture: {bl_image.name} "
                    f"({bl_image.size[0]}x{bl_image.size[1]})")

            # Extract RGBA pixels
            rgba_data, img_w, img_h = _extract_image_pixels(bl_image)
            if rgba_data is not None:
                # Optional max-size cap (downscale, no upscale)
                img_w, img_h, rgba_data = _cap_resolution(
                    img_w, img_h, rgba_data, max_texture_size)
                # Ensure power-of-2 dimensions
                img_w, img_h, rgba_data = _ensure_power_of_2(
                    img_w, img_h, rgba_data)

                # DXT5 compress with mipmaps
                try:
                    texture_levels = compress_with_mipmaps(
                        rgba_data, img_w, img_h, swap_rb=swap_rb)
                    _report(operator, 'INFO',
                            f"      Compressed: {img_w}x{img_h}, "
                            f"{len(texture_levels)} mip levels")

                    # Cache for reuse
                    texture_cache[cache_key] = (texture_levels, texture_name)
                except Exception as e:
                    _report(operator, 'WARNING',
                            f"      Texture compression failed: {e}")

    # If no texture found, create a 4x4 white placeholder
    if texture_levels is None:
        texture_levels = _create_placeholder_texture(swap_rb=swap_rb)
        texture_name = texture_name or 'placeholder'
        _report(operator, 'INFO', "      Using 4x4 white placeholder texture")

    return texture_levels, texture_name


def _get_texture_for_image(bl_image, texture_cache, operator, swap_rb=False,
                           role=None, normal_y_flip=True, max_texture_size=0):
    """Get compressed texture levels for a specific Blender image, using cache.

    Like _get_texture_for_material but takes a bl_image directly (for
    multi-texture role maps).

    Args:
        bl_image: Blender image to compress
        texture_cache: dict for caching compressed textures
        operator: Blender operator for reporting
        swap_rb: swap R/B channels (MUA PC)
        role: 'normal' repacks into MUA's DXT5nm layout (X→alpha, Y→green,
            auto-detecting blue tangent-space vs already-DXT5nm sources —
            same conversion the skin exporter uses); other roles pass through
        normal_y_flip: flip normal green OpenGL→DirectX during the repack
        max_texture_size: 0 = keep original size; otherwise cap longest edge.

    Returns:
        (texture_levels, texture_name)
    """
    from ..utils.dxt_compress import compress_with_mipmaps

    texture_levels = None
    texture_name = bl_image.name if bl_image is not None else ''

    # Cache key MUST include the role: the diffuse path (_get_texture_for_material)
    # keys on "name@size" too, so a specular/gloss image that shares a Blender
    # image name with the diffuse would otherwise fetch the diffuse's bytes.
    cache_key = f"{texture_name}@{max_texture_size}@{role or 'diffuse'}"
    if role == 'normal' and normal_y_flip:
        cache_key = cache_key + '_yflip'

    if bl_image is not None:
        # Check cache first
        if cache_key in texture_cache:
            cached_levels, cached_name = texture_cache[cache_key]
            _report(operator, 'INFO',
                    f"      Texture: {texture_name} (cached)")
            return cached_levels, cached_name

        # Extract RGBA pixels
        rgba_data, img_w, img_h = _extract_image_pixels(bl_image)
        if rgba_data is not None:
            # Optional max-size cap (downscale, no upscale)
            img_w, img_h, rgba_data = _cap_resolution(
                img_w, img_h, rgba_data, max_texture_size)
            # Ensure power-of-2 dimensions
            img_w, img_h, rgba_data = _ensure_power_of_2(
                img_w, img_h, rgba_data)

            # Normal maps: repack into MUA's DXT5nm channel layout (the
            # naive green-flip alone leaves X in RED where the Cg bump
            # shader never looks — it reads X from ALPHA, Y from GREEN).
            # Guard against a colour/grayscale map mis-wired onto the Normal
            # input (would become green-black); warn + skip instead.
            if role == 'normal':
                from .skin_export import (convert_normal_rgba_to_mua,
                                          looks_like_normal_map)
                if not looks_like_normal_map(rgba_data):
                    _report(operator, 'WARNING',
                            f"      '{texture_name}' on the Normal input isn't a "
                            f"normal map (colour/grayscale) — skipping it; wire a "
                            f"specular/gloss map to its own input instead")
                    return None, texture_name
                rgba_data = bytes(convert_normal_rgba_to_mua(
                    bytearray(rgba_data), normal_y_flip=normal_y_flip))

            # DXT5 compress with mipmaps
            try:
                texture_levels = compress_with_mipmaps(
                    rgba_data, img_w, img_h, swap_rb=swap_rb)
                _report(operator, 'INFO',
                        f"      Compressed: {img_w}x{img_h}, "
                        f"{len(texture_levels)} mip levels"
                        f"{' (MUA DXT5nm)' if role == 'normal' else ''}")
                texture_cache[cache_key] = (texture_levels, texture_name)
            except Exception as e:
                _report(operator, 'WARNING',
                        f"      Texture compression failed: {e}")

    # If no texture found, create a placeholder
    if texture_levels is None:
        texture_levels = _create_placeholder_texture(swap_rb=swap_rb)
        texture_name = texture_name or 'placeholder'

    return texture_levels, texture_name


def _get_texture_clut_for_material(bl_mat, texture_cache, operator,
                                   max_texture_size=0):
    """Get CLUT-quantized texture data for a material, using cache.

    Args:
        max_texture_size: 0 = keep original size; otherwise cap longest edge.

    Returns:
        (clut_data, texture_name) where clut_data is
        (palette_data, index_data, width, height) or None
    """
    from ..utils.clut_compress import quantize_rgba_to_clut

    clut_data = None
    texture_name = ''

    if bl_mat is not None:
        bl_image = _find_texture_image(bl_mat)
        if bl_image is not None:
            texture_name = bl_image.name

            # Check cache first
            cache_key = f"{texture_name}@{max_texture_size}_clut"
            if cache_key in texture_cache:
                cached_data, cached_name = texture_cache[cache_key]
                _report(operator, 'INFO',
                        f"      Texture: {texture_name} (cached CLUT)")
                return cached_data, cached_name

            _report(operator, 'INFO',
                    f"      Texture: {bl_image.name} "
                    f"({bl_image.size[0]}x{bl_image.size[1]}) → CLUT")

            rgba_data, img_w, img_h = _extract_image_pixels(bl_image)
            if rgba_data is not None:
                img_w, img_h, rgba_data = _cap_resolution(
                    img_w, img_h, rgba_data, max_texture_size)
                img_w, img_h, rgba_data = _ensure_power_of_2(
                    img_w, img_h, rgba_data)
                try:
                    palette_data, index_data = quantize_rgba_to_clut(
                        rgba_data, img_w, img_h)
                    clut_data = (palette_data, index_data, img_w, img_h)
                    _report(operator, 'INFO',
                            f"      Quantized: {img_w}x{img_h}, 256 colors")
                    texture_cache[cache_key] = (clut_data, texture_name)
                except Exception as e:
                    _report(operator, 'WARNING',
                            f"      CLUT quantization failed: {e}")

    # If no texture found, create a 4x4 white placeholder CLUT
    if clut_data is None:
        palette_data = bytearray(1024)
        # First entry: white
        palette_data[0] = 255
        palette_data[1] = 255
        palette_data[2] = 255
        palette_data[3] = 255
        index_data = bytes(16)  # 4x4 pixels, all index 0
        clut_data = (bytes(palette_data), index_data, 4, 4)
        texture_name = texture_name or 'placeholder'
        _report(operator, 'INFO', "      Using 4x4 white placeholder CLUT")

    return clut_data, texture_name


# ===========================================================================
# Material/Texture extraction helpers
# ===========================================================================

def _default_material():
    """Return default material properties.

    Ambient uses 0.588 gray to match the standard value found in game files.
    In OpenGL fixed-function: final = ambient_light * ambient_mat + diffuse_light * diffuse_mat * NdotL.
    Full-white ambient (1.0) would make surfaces bright from all angles regardless of lighting.
    """
    return {
        'diffuse': (1.0, 1.0, 1.0, 1.0),
        'ambient': (0.588, 0.588, 0.588, 1.0),
        'specular': (0.0, 0.0, 0.0, 0.0),
        'emission': (0.0, 0.0, 0.0, 0.0),
        'shininess': 0.0,
        'material_state': {},
    }


def _extract_material_props(bl_mat):
    """Extract material properties from a Blender material.

    First checks for igb_* custom properties (set during import for
    round-trip fidelity). Falls back to reading Principled BSDF inputs.

    Mapping (fallback):
        Base Color → diffuse
        Roughness → shininess (inverted: shininess = (1-roughness) * 128)
        Specular → specular
        Emission Color → emission
    """
    props = _default_material()

    # --- Try IGB node group first (shader editor) ---
    from ..utils.material_nodes import find_igb_node, read_igb_node_values
    igb_node = find_igb_node(bl_mat)
    if igb_node is not None:
        nv = read_igb_node_values(igb_node)
        if 'diffuse' in nv:
            props['diffuse'] = nv['diffuse']
        if 'ambient' in nv:
            props['ambient'] = nv['ambient']
        if 'specular' in nv:
            props['specular'] = nv['specular']
        if 'emission' in nv:
            props['emission'] = nv['emission']
        if 'shininess' in nv:
            props['shininess'] = nv['shininess']
        props['material_state'] = _extract_material_state(bl_mat)
        return props

    # --- Try igb_* custom properties (legacy round-trip) ---
    igb_diffuse = bl_mat.get("igb_diffuse")
    if igb_diffuse is not None:
        props['diffuse'] = tuple(igb_diffuse)
        props['ambient'] = tuple(bl_mat.get("igb_ambient", (0.588, 0.588, 0.588, 1.0)))
        props['specular'] = tuple(bl_mat.get("igb_specular", (0.0, 0.0, 0.0, 0.0)))
        props['emission'] = tuple(bl_mat.get("igb_emission", (0.0, 0.0, 0.0, 0.0)))
        props['shininess'] = bl_mat.get("igb_shininess", 0.0)
    elif bl_mat.use_nodes:
        # --- Fallback: read from Principled BSDF ---
        for node in bl_mat.node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                base_color = node.inputs.get('Base Color')
                if base_color is not None:
                    c = base_color.default_value
                    props['diffuse'] = (c[0], c[1], c[2], c[3])

                roughness = node.inputs.get('Roughness')
                if roughness is not None:
                    r = roughness.default_value
                    props['shininess'] = (1.0 - r) * 128.0

                spec_input = (node.inputs.get('Specular IOR Level') or
                              node.inputs.get('Specular'))
                if spec_input is not None:
                    s = spec_input.default_value
                    if isinstance(s, float):
                        props['specular'] = (s, s, s, 1.0)

                emission = node.inputs.get('Emission Color')
                if emission is not None:
                    e = emission.default_value
                    props['emission'] = (e[0], e[1], e[2], 1.0)

                break

        # Ambient: use game-standard 0.588 gray, NOT the diffuse color.
        # In fixed-function: final = ambient_light * ambient_mat + diffuse_light * diffuse_mat * NdotL.
        # Full-white ambient makes everything fully lit from all angles.
        props['ambient'] = (0.588, 0.588, 0.588, 1.0)
    else:
        c = bl_mat.diffuse_color
        props['diffuse'] = (c[0], c[1], c[2], c[3])

    # --- Extract IGB material state custom properties ---
    props['material_state'] = _extract_material_state(bl_mat)

    return props


def _extract_material_state(bl_mat):
    """Extract IGB material state from a Blender material.

    Priority order:
    1. IGB Material Settings node group (shader editor)
    2. igb_* custom properties (legacy)
    3. Infer from Blender material settings

    Returns:
        dict with material state keys (only present keys are included)
    """
    # --- Try IGB node group first ---
    from ..utils.material_nodes import find_igb_node, read_igb_node_values
    igb_node = find_igb_node(bl_mat)
    if igb_node is not None:
        nv = read_igb_node_values(igb_node)
        state = {}
        if nv.get('blend_enabled'):
            state['blend_enabled'] = True
            state['blend_src'] = nv.get('blend_src', 4)
            state['blend_dst'] = nv.get('blend_dst', 5)
            state['blend_eq'] = nv.get('blend_eq', 0)
            state['blend_constant'] = nv.get('blend_constant', 0)
            state['blend_stage'] = nv.get('blend_stage', 0)
            state['blend_a'] = nv.get('blend_a', 0)
            state['blend_b'] = nv.get('blend_b', 0)
            state['blend_c'] = nv.get('blend_c', 0)
            state['blend_d'] = nv.get('blend_d', 0)
        if nv.get('alpha_test_enabled'):
            state['alpha_test_enabled'] = True
            state['alpha_func'] = nv.get('alpha_func', 6)
            state['alpha_ref'] = nv.get('alpha_ref', 0.5)
        color = nv.get('color')
        if color is not None and color != (1.0, 1.0, 1.0, 1.0):
            state['color_r'] = color[0]
            state['color_g'] = color[1]
            state['color_b'] = color[2]
            state['color_a'] = color[3]
        lighting = nv.get('lighting_enabled')
        if lighting is not None:
            state['lighting_enabled'] = bool(lighting)
        if nv.get('tex_matrix_enabled'):
            state['tex_matrix_enabled'] = True
            state['tex_matrix_unit_id'] = nv.get('tex_matrix_unit_id', 0)
        cull = nv.get('cull_face_enabled')
        if cull is not None:
            state['cull_face_enabled'] = bool(cull)
            state['cull_face_mode'] = nv.get('cull_face_mode', 0)
        return state

    state = {}

    # Blend state — only include if enabled (game files omit for opaque)
    blend_enabled = bl_mat.get("igb_blend_enabled")
    if blend_enabled is not None:
        state['blend_enabled'] = bool(blend_enabled)
    else:
        # Infer from Blender blend mode
        is_blended = False
        if hasattr(bl_mat, 'surface_render_method'):
            is_blended = (bl_mat.surface_render_method == 'BLENDED')
        elif hasattr(bl_mat, 'blend_method'):
            is_blended = (bl_mat.blend_method != 'OPAQUE')
        if is_blended:
            state['blend_enabled'] = True

    # Blend function — only include when blend is actually enabled
    if state.get('blend_enabled'):
        if bl_mat.get("igb_blend_src") is not None:
            state['blend_src'] = bl_mat.get("igb_blend_src", 4)
            state['blend_dst'] = bl_mat.get("igb_blend_dst", 5)
            state['blend_eq'] = bl_mat.get("igb_blend_eq", 0)
            state['blend_constant'] = bl_mat.get("igb_blend_constant", 0)
            state['blend_stage'] = bl_mat.get("igb_blend_stage", 0)
            state['blend_a'] = bl_mat.get("igb_blend_a", 0)
            state['blend_b'] = bl_mat.get("igb_blend_b", 0)
            state['blend_c'] = bl_mat.get("igb_blend_c", 0)
            state['blend_d'] = bl_mat.get("igb_blend_d", 0)
        else:
            # Blending inferred but no custom props — standard alpha blend
            state['blend_src'] = 4   # SRC_ALPHA
            state['blend_dst'] = 5   # ONE_MINUS_SRC_ALPHA

    # Alpha test state — only include if enabled (game files omit for non-cutout)
    alpha_enabled = bl_mat.get("igb_alpha_test_enabled")
    if alpha_enabled is not None and bool(alpha_enabled):
        state['alpha_test_enabled'] = True

    # Alpha function — only include when alpha test is enabled
    if state.get('alpha_test_enabled'):
        if bl_mat.get("igb_alpha_func") is not None:
            state['alpha_func'] = bl_mat.get("igb_alpha_func", 6)
            state['alpha_ref'] = bl_mat.get("igb_alpha_ref", 0.5)
        else:
            state['alpha_func'] = 6    # GEQUAL (default)
            state['alpha_ref'] = 0.5

    # Color attr
    if bl_mat.get("igb_color_r") is not None:
        state['color_r'] = bl_mat.get("igb_color_r", 1.0)
        state['color_g'] = bl_mat.get("igb_color_g", 1.0)
        state['color_b'] = bl_mat.get("igb_color_b", 1.0)
        state['color_a'] = bl_mat.get("igb_color_a", 1.0)

    # Lighting state
    lighting = bl_mat.get("igb_lighting_enabled")
    if lighting is not None:
        state['lighting_enabled'] = bool(lighting)

    # Texture matrix state
    tex_matrix = bl_mat.get("igb_tex_matrix_enabled")
    if tex_matrix is not None:
        state['tex_matrix_enabled'] = bool(tex_matrix)
        state['tex_matrix_unit_id'] = bl_mat.get("igb_tex_matrix_unit_id", 0)

    # Backface culling
    cull_enabled = bl_mat.get("igb_cull_face_enabled")
    if cull_enabled is not None:
        state['cull_face_enabled'] = bool(cull_enabled)
        state['cull_face_mode'] = bl_mat.get("igb_cull_face_mode", 0)
    elif bl_mat.use_backface_culling:
        # Infer from Blender's backface culling setting.
        # Game files: enable=1, mode=0 is the standard for culled geometry.
        state['cull_face_enabled'] = True
        state['cull_face_mode'] = 0

    return state


def _find_texture_image(bl_mat):
    """Find the DIFFUSE (base colour) image for a material.

    Robust to the .IGB Material node group: checks the group's 'Base Texture'
    input and a raw BSDF 'Base Color' first, so it never grabs the normal/
    specular/gloss map by falling through to "any texture node" (the bug where
    the normal map became the main texture after Convert-to-IGB).
    """
    if bl_mat is None or not bl_mat.use_nodes or bl_mat.node_tree is None:
        return None
    from .skin_export import _find_diffuse_image
    return _find_diffuse_image(bl_mat)


def _extract_image_pixels(bl_image):
    """Extract RGBA pixel data from a Blender image.

    Blender stores pixels bottom-to-top (OpenGL convention).
    IGB/DXT expects top-to-bottom, so we Y-flip here.

    Returns:
        (rgba_bytes, width, height) or (None, 0, 0) on failure
    """
    width = bl_image.size[0]
    height = bl_image.size[1]

    if width == 0 or height == 0:
        return None, 0, 0

    pixels = list(bl_image.pixels)
    num_pixels = width * height

    rgba = bytearray(num_pixels * 4)
    for i in range(num_pixels):
        src_y = i // width
        src_x = i % width
        # Y-flip: Blender bottom-up → DXT top-down
        dst_y = height - 1 - src_y
        dst_idx = (dst_y * width + src_x) * 4
        src_idx = i * 4

        rgba[dst_idx] = _float_to_byte(pixels[src_idx])
        rgba[dst_idx + 1] = _float_to_byte(pixels[src_idx + 1])
        rgba[dst_idx + 2] = _float_to_byte(pixels[src_idx + 2])
        rgba[dst_idx + 3] = _float_to_byte(pixels[src_idx + 3])

    return bytes(rgba), width, height


def _ensure_power_of_2(width, height, rgba_data):
    """Ensure texture dimensions are powers of 2.

    DXT compression requires POT dimensions. If the image is not POT,
    it is scaled up to the next power of 2 using nearest-neighbor sampling.

    Returns:
        (new_width, new_height, new_rgba_data)
    """
    new_w = _next_power_of_2(width)
    new_h = _next_power_of_2(height)

    if new_w == width and new_h == height:
        return width, height, rgba_data

    new_data = bytearray(new_w * new_h * 4)
    for y in range(new_h):
        src_y = min(y * height // new_h, height - 1)
        for x in range(new_w):
            src_x = min(x * width // new_w, width - 1)
            src_off = (src_y * width + src_x) * 4
            dst_off = (y * new_w + x) * 4
            new_data[dst_off:dst_off + 4] = rgba_data[src_off:src_off + 4]

    return new_w, new_h, bytes(new_data)


def _cap_resolution(width, height, rgba_data, max_size):
    """Downscale to at most max_size on the longest edge, preserving aspect.

    Result dimensions are clamped to powers of 2 (DXT requirement). When
    max_size is 0, returns the input unchanged.
    """
    if max_size <= 0 or (width <= max_size and height <= max_size):
        return width, height, rgba_data

    if width >= height:
        new_w = max_size
        new_h = max(1, (height * max_size) // width)
    else:
        new_h = max_size
        new_w = max(1, (width * max_size) // height)

    # Snap to power-of-2 (round down so we don't bounce back over the cap)
    new_w = _prev_power_of_2(new_w)
    new_h = _prev_power_of_2(new_h)

    new_data = bytearray(new_w * new_h * 4)
    for y in range(new_h):
        src_y = min(y * height // new_h, height - 1)
        for x in range(new_w):
            src_x = min(x * width // new_w, width - 1)
            src_off = (src_y * width + src_x) * 4
            dst_off = (y * new_w + x) * 4
            new_data[dst_off:dst_off + 4] = rgba_data[src_off:src_off + 4]

    return new_w, new_h, bytes(new_data)


def _prev_power_of_2(n):
    """Return the largest power of 2 <= n (n >= 1)."""
    if n <= 1:
        return 1
    p = 1
    while p * 2 <= n:
        p <<= 1
    return p


def _create_placeholder_texture(swap_rb=False):
    """Create a minimal 4x4 white DXT5 texture with no mipmaps.

    Used when a material has no texture image. Returns a list with
    one (compressed_data, width, height) tuple.
    """
    from ..utils.dxt_compress import compress_rgba_to_dxt5

    # 4x4 white RGBA (R/B swap doesn't matter for all-white)
    white_rgba = bytes([255, 255, 255, 255] * 16)
    compressed = compress_rgba_to_dxt5(white_rgba, 4, 4, swap_rb=swap_rb)
    return [(compressed, 4, 4)]


# ===========================================================================
# Utility helpers
# ===========================================================================

def _next_power_of_2(n):
    """Return the next power of 2 >= n."""
    if n <= 0:
        return 1
    p = 1
    while p < n:
        p *= 2
    return p


def _float_to_byte(f):
    """Convert 0.0-1.0 float to 0-255 byte, clamped."""
    return max(0, min(255, int(f * 255.0 + 0.5)))


def _report(operator, level, message):
    """Report a message through the operator or print to console."""
    if operator is not None and hasattr(operator, 'report'):
        operator.report({level}, message)
    else:
        print(f"[{level}] {message}")
