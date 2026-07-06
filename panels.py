"""IGB Tools panel for the 3D Viewport sidebar (N-panel).

Provides convenient operators for setting up collision geometry:
- Create Colliders collection
- Add box collider primitives (wireframe display, auto-named)
- Generate Box Colliders from a source collection (bounding box per object)
- Generate Convex Hull Colliders from a source collection
- Generate Decimated Colliders from a source collection
- Merge Colliders (join + weld seam vertices)
- Status display showing collider count and triangle count

Also provides material conversion:
- Convert All to IGB: sets IGB custom properties on all scene materials

Quick-access batch tools for modifying IGB material properties:
- Lighting toggle, backface culling, UV animation, alpha test,
  blend state, and color tint — applied to all scene materials at once.
"""

import os
import bpy
from bpy.types import Operator, Panel
from . import _get_icon_id
from bpy.props import (BoolProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, EnumProperty, StringProperty)


# Maximum collision triangles recommended for the game engine.
# Game files range 9–17231, median ~3600. Going above this risks
# performance issues or engine limits.
_MAX_RECOMMENDED_TRIS = 15000


# ===========================================================================
# Operators
# ===========================================================================

class IGB_OT_create_colliders_collection(Operator):
    """Create the 'Colliders' collection if it doesn't exist"""
    bl_idname = "igb.create_colliders_collection"
    bl_label = "Create Colliders Collection"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if "Colliders" in bpy.data.collections:
            self.report({'INFO'}, "Colliders collection already exists")
            return {'FINISHED'}

        coll = bpy.data.collections.new("Colliders")
        context.scene.collection.children.link(coll)
        self.report({'INFO'}, "Created 'Colliders' collection")
        return {'FINISHED'}


class IGB_OT_add_box_collider(Operator):
    """Add a box collider mesh at the 3D cursor"""
    bl_idname = "igb.add_box_collider"
    bl_label = "Add Box Collider"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # Ensure Colliders collection exists
        if "Colliders" not in bpy.data.collections:
            coll = bpy.data.collections.new("Colliders")
            context.scene.collection.children.link(coll)
        else:
            coll = bpy.data.collections["Colliders"]

        # Ensure collection is linked to scene
        if coll.name not in context.scene.collection.children:
            context.scene.collection.children.link(coll)

        # Generate unique name
        base_name = "Collider_Box"
        index = 1
        while f"{base_name}_{index:03d}" in bpy.data.objects:
            index += 1
        name = f"{base_name}_{index:03d}"

        # Create cube mesh
        import bmesh
        mesh = bpy.data.meshes.new(name)
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=500.0)
        bm.to_mesh(mesh)
        bm.free()

        # Create object at 3D cursor
        obj = bpy.data.objects.new(name, mesh)
        obj.location = context.scene.cursor.location.copy()

        # Solid display with semi-transparent collision material
        obj.display_type = 'SOLID'
        col_mat = _get_collision_material()
        mesh.materials.append(col_mat)

        # Link ONLY to Colliders collection (not scene root)
        coll.objects.link(obj)

        # Select and make active
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        context.view_layer.objects.active = obj

        self.report({'INFO'}, f"Added '{name}' to Colliders collection")
        return {'FINISHED'}


# ===========================================================================
# Helper: get or create Colliders collection
# ===========================================================================

def _ensure_colliders_collection(context):
    """Get or create the 'Colliders' collection, linked to the scene."""
    if "Colliders" not in bpy.data.collections:
        coll = bpy.data.collections.new("Colliders")
        context.scene.collection.children.link(coll)
    else:
        coll = bpy.data.collections["Colliders"]
        if coll.name not in context.scene.collection.children:
            context.scene.collection.children.link(coll)
    return coll


def _get_source_mesh_objects(context):
    """Return visible mesh objects that are NOT in the Colliders collection."""
    colliders_coll = bpy.data.collections.get("Colliders")
    collider_objs = set(colliders_coll.objects) if colliders_coll else set()

    return [
        obj for obj in context.scene.objects
        if obj.type == 'MESH'
        and obj not in collider_objs
        and not obj.hide_viewport
    ]


# ===========================================================================
# Generate Box Colliders operator
# ===========================================================================

class IGB_OT_generate_box_colliders(Operator):
    """Generate box colliders from all visible mesh objects.\n"""  \
    """Creates one oriented bounding box (12 tris) per object in the\n"""  \
    """Colliders collection. This is the most game-accurate approach —\n"""  \
    """game maps use simplified collision at ~15% of visual complexity"""
    bl_idname = "igb.generate_box_colliders"
    bl_label = "Generate Box Colliders"
    bl_options = {'REGISTER', 'UNDO'}

    padding: FloatProperty(
        name="Padding",
        description="Expand each box by this amount in all directions "
                    "(game units). Prevents tiny gaps at edges",
        default=1.0,
        min=0.0,
        soft_max=50.0,
    )

    clear_existing: BoolProperty(
        name="Clear Existing",
        description="Remove all existing objects in Colliders before generating",
        default=True,
    )

    def execute(self, context):
        import bmesh

        source_objs = _get_source_mesh_objects(context)
        if not source_objs:
            self.report({'ERROR'}, "No visible mesh objects in scene to generate from")
            return {'CANCELLED'}

        coll = _ensure_colliders_collection(context)

        # Clear existing colliders if requested
        if self.clear_existing:
            for obj in list(coll.objects):
                bpy.data.objects.remove(obj, do_unlink=True)

        col_mat = _get_collision_material()
        created = 0
        total_tris = 0

        depsgraph = context.evaluated_depsgraph_get()

        for src_obj in source_objs:
            # Get evaluated mesh (applies modifiers)
            eval_obj = src_obj.evaluated_get(depsgraph)
            mesh = eval_obj.to_mesh()
            if mesh is None or len(mesh.vertices) == 0:
                eval_obj.to_mesh_clear()
                continue

            # Compute world-space AABB
            world_mat = src_obj.matrix_world
            ws_coords = [world_mat @ v.co for v in mesh.vertices]

            min_x = min(c.x for c in ws_coords) - self.padding
            min_y = min(c.y for c in ws_coords) - self.padding
            min_z = min(c.z for c in ws_coords) - self.padding
            max_x = max(c.x for c in ws_coords) + self.padding
            max_y = max(c.y for c in ws_coords) + self.padding
            max_z = max(c.z for c in ws_coords) + self.padding

            eval_obj.to_mesh_clear()

            # Create box mesh from the 8 AABB corners
            name = f"Collider_{src_obj.name}"
            box_mesh = bpy.data.meshes.new(name)
            bm = bmesh.new()

            # 8 corners of the AABB
            verts = [
                bm.verts.new((min_x, min_y, min_z)),  # 0
                bm.verts.new((max_x, min_y, min_z)),  # 1
                bm.verts.new((max_x, max_y, min_z)),  # 2
                bm.verts.new((min_x, max_y, min_z)),  # 3
                bm.verts.new((min_x, min_y, max_z)),  # 4
                bm.verts.new((max_x, min_y, max_z)),  # 5
                bm.verts.new((max_x, max_y, max_z)),  # 6
                bm.verts.new((min_x, max_y, max_z)),  # 7
            ]

            # 6 faces (outward normals)
            bm.faces.new((verts[0], verts[3], verts[2], verts[1]))  # bottom
            bm.faces.new((verts[4], verts[5], verts[6], verts[7]))  # top
            bm.faces.new((verts[0], verts[1], verts[5], verts[4]))  # front
            bm.faces.new((verts[2], verts[3], verts[7], verts[6]))  # back
            bm.faces.new((verts[0], verts[4], verts[7], verts[3]))  # left
            bm.faces.new((verts[1], verts[2], verts[6], verts[5]))  # right

            bm.to_mesh(box_mesh)
            bm.free()

            obj = bpy.data.objects.new(name, box_mesh)
            # Object is at origin — verts already in world space
            obj.display_type = 'SOLID'
            box_mesh.materials.append(col_mat)
            coll.objects.link(obj)

            created += 1
            total_tris += 12  # 6 faces × 2 tris each

        self.report({'INFO'},
                    f"Generated {created} box collider(s), {total_tris} tris total")
        return {'FINISHED'}


# ===========================================================================
# Generate Convex Hull Colliders operator
# ===========================================================================

class IGB_OT_generate_hull_colliders(Operator):
    """Generate convex hull colliders from all visible mesh objects.\n"""  \
    """Creates one convex hull per object — tighter fit than boxes\n"""  \
    """but more triangles. Good for organic or irregular shapes"""
    bl_idname = "igb.generate_hull_colliders"
    bl_label = "Generate Hull Colliders"
    bl_options = {'REGISTER', 'UNDO'}

    clear_existing: BoolProperty(
        name="Clear Existing",
        description="Remove all existing objects in Colliders before generating",
        default=True,
    )

    def execute(self, context):
        import bmesh

        source_objs = _get_source_mesh_objects(context)
        if not source_objs:
            self.report({'ERROR'}, "No visible mesh objects in scene to generate from")
            return {'CANCELLED'}

        coll = _ensure_colliders_collection(context)

        if self.clear_existing:
            for obj in list(coll.objects):
                bpy.data.objects.remove(obj, do_unlink=True)

        col_mat = _get_collision_material()
        created = 0
        total_tris = 0

        depsgraph = context.evaluated_depsgraph_get()

        for src_obj in source_objs:
            eval_obj = src_obj.evaluated_get(depsgraph)
            mesh = eval_obj.to_mesh()
            if mesh is None or len(mesh.vertices) == 0:
                eval_obj.to_mesh_clear()
                continue

            # Compute world-space vertex positions
            world_mat = src_obj.matrix_world
            ws_coords = [world_mat @ v.co for v in mesh.vertices]
            eval_obj.to_mesh_clear()

            if len(ws_coords) < 4:
                continue

            # Build convex hull via bmesh
            name = f"Collider_{src_obj.name}"
            hull_mesh = bpy.data.meshes.new(name)
            bm = bmesh.new()

            for co in ws_coords:
                bm.verts.new(co)

            try:
                result = bmesh.ops.convex_hull(bm, input=bm.verts[:])
                # Remove interior geometry
                interior = result.get("geom_interior", [])
                if interior:
                    bmesh.ops.delete(bm, geom=interior, context='VERTS')
            except Exception as e:
                print(f"[Collision] Convex hull failed for '{src_obj.name}': {e}")
                bm.free()
                continue

            bm.to_mesh(hull_mesh)
            tri_count = sum(len(f.vertices) - 2 for f in hull_mesh.polygons)
            bm.free()

            obj = bpy.data.objects.new(name, hull_mesh)
            obj.display_type = 'SOLID'
            hull_mesh.materials.append(col_mat)
            coll.objects.link(obj)

            created += 1
            total_tris += tri_count

        self.report({'INFO'},
                    f"Generated {created} hull collider(s), {total_tris} tris total")
        return {'FINISHED'}


# ===========================================================================
# Generate Decimated Colliders operator
# ===========================================================================

class IGB_OT_generate_decimated_colliders(Operator):
    """Generate simplified collision from all visible meshes.\n"""  \
    """Copies all visual geometry into one Collider object and applies\n"""  \
    """a Decimate modifier to reduce triangle count. Produces better\n"""  \
    """surface coverage than boxes, at the cost of more triangles.\n"""  \
    """Target: ~10-15% of visual mesh (matches game files)"""
    bl_idname = "igb.generate_decimated_colliders"
    bl_label = "Generate Decimated Collision"
    bl_options = {'REGISTER', 'UNDO'}

    ratio: FloatProperty(
        name="Ratio",
        description="Decimate ratio — fraction of triangles to keep. "
                    "Game files average ~15% of visual complexity. "
                    "Lower = fewer tris, faster collision",
        default=0.10,
        min=0.01,
        max=1.0,
        step=1,
        precision=2,
    )

    max_tris: IntProperty(
        name="Max Triangles",
        description="Hard cap on collision triangles. Game engine max is ~17000. "
                    "Decimate will be tightened if ratio alone exceeds this",
        default=15000,
        min=100,
        max=50000,
    )

    clear_existing: BoolProperty(
        name="Clear Existing",
        description="Remove all existing objects in Colliders before generating",
        default=True,
    )

    def execute(self, context):
        import bmesh

        source_objs = _get_source_mesh_objects(context)
        if not source_objs:
            self.report({'ERROR'}, "No visible mesh objects in scene to generate from")
            return {'CANCELLED'}

        coll = _ensure_colliders_collection(context)

        if self.clear_existing:
            for obj in list(coll.objects):
                bpy.data.objects.remove(obj, do_unlink=True)

        col_mat = _get_collision_material()

        # Merge all source meshes into one bmesh in world space
        bm = bmesh.new()
        depsgraph = context.evaluated_depsgraph_get()
        total_source_tris = 0

        for src_obj in source_objs:
            eval_obj = src_obj.evaluated_get(depsgraph)
            mesh = eval_obj.to_mesh()
            if mesh is None or len(mesh.vertices) == 0:
                eval_obj.to_mesh_clear()
                continue

            mesh.calc_loop_triangles()
            total_source_tris += len(mesh.loop_triangles)

            # Import into bmesh with world transform
            bm.from_mesh(mesh, face_normals=False)
            # Transform the newly added verts to world space
            # bmesh appends at the end, so we transform the last batch
            eval_obj.to_mesh_clear()

        if total_source_tris == 0:
            bm.free()
            self.report({'ERROR'}, "No triangles in source meshes")
            return {'CANCELLED'}

        # We need to apply world transforms per-object.
        # Rebuild with explicit transforms instead.
        bm.free()

        # Create a temporary mesh by joining copies
        bm = bmesh.new()
        depsgraph = context.evaluated_depsgraph_get()

        for src_obj in source_objs:
            eval_obj = src_obj.evaluated_get(depsgraph)
            mesh = eval_obj.to_mesh()
            if mesh is None or len(mesh.vertices) == 0:
                eval_obj.to_mesh_clear()
                continue

            world_mat = src_obj.matrix_world
            offset = len(bm.verts)
            for v in mesh.vertices:
                bm.verts.new(world_mat @ v.co)
            bm.verts.ensure_lookup_table()

            for poly in mesh.polygons:
                try:
                    face_verts = [bm.verts[offset + vi] for vi in poly.vertices]
                    bm.faces.new(face_verts)
                except (IndexError, ValueError):
                    pass  # skip degenerate

            eval_obj.to_mesh_clear()

        # Weld coincident verts before decimation
        bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=0.5)

        # Write to a real mesh so we can apply Decimate modifier
        col_mesh = bpy.data.meshes.new("Collider_Decimated")
        bm.to_mesh(col_mesh)
        bm.free()

        obj = bpy.data.objects.new("Collider_Decimated", col_mesh)
        coll.objects.link(obj)

        # Temporarily link to scene for modifier application
        if obj.name not in context.scene.collection.objects:
            context.scene.collection.objects.link(obj)

        # Triangulate first
        tri_mod = obj.modifiers.new("Triangulate", 'TRIANGULATE')

        context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier=tri_mod.name)

        pre_decimate_tris = sum(len(p.vertices) - 2 for p in col_mesh.polygons)

        # Calculate effective ratio: respect both user ratio and max_tris cap
        effective_ratio = self.ratio
        if pre_decimate_tris * effective_ratio > self.max_tris:
            effective_ratio = self.max_tris / pre_decimate_tris

        # Apply decimate modifier
        dec_mod = obj.modifiers.new("Decimate", 'DECIMATE')
        dec_mod.ratio = effective_ratio

        bpy.ops.object.modifier_apply(modifier=dec_mod.name)

        final_tris = sum(len(p.vertices) - 2 for p in col_mesh.polygons)

        # Unlink from scene root (keep only in Colliders)
        if obj.name in context.scene.collection.objects:
            context.scene.collection.objects.unlink(obj)

        # Apply collision material
        obj.display_type = 'SOLID'
        col_mesh.materials.append(col_mat)

        # Select and make active
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        context.view_layer.objects.active = obj

        self.report({'INFO'},
                    f"Generated decimated collision: {total_source_tris} → {final_tris} tris "
                    f"({final_tris / max(total_source_tris, 1) * 100:.1f}%)")
        return {'FINISHED'}


# ===========================================================================
# Merge Colliders operator
# ===========================================================================

class IGB_OT_merge_colliders(Operator):
    """Join all objects in the Colliders collection into one mesh and
weld coincident vertices (Merge by Distance).
This removes seam gaps between adjacent collision tiles so the
character cannot fall through cracks at object boundaries."""
    bl_idname = "igb.merge_colliders"
    bl_label = "Merge Colliders"
    bl_options = {'REGISTER', 'UNDO'}

    merge_distance: FloatProperty(
        name="Merge Distance",
        description="Vertices closer than this distance are merged",
        default=0.1,
        min=0.0,
        soft_max=10.0,
    )

    def execute(self, context):
        coll = bpy.data.collections.get("Colliders")
        if coll is None:
            self.report({'ERROR'}, "No 'Colliders' collection found")
            return {'CANCELLED'}

        mesh_objs = [o for o in coll.objects if o.type == 'MESH']
        if not mesh_objs:
            self.report({'ERROR'}, "Colliders collection has no mesh objects")
            return {'CANCELLED'}

        if len(mesh_objs) == 1:
            # Nothing to join — just run merge by distance on the single object
            obj = mesh_objs[0]
            bpy.ops.object.select_all(action='DESELECT')
            obj.select_set(True)
            context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.mesh.remove_doubles(threshold=self.merge_distance)
            bpy.ops.object.mode_set(mode='OBJECT')
            self.report({'INFO'}, f"Merged by distance on '{obj.name}'")
            return {'FINISHED'}

        # Deselect everything, then select all collider objects
        bpy.ops.object.select_all(action='DESELECT')
        for obj in mesh_objs:
            obj.select_set(True)
        context.view_layer.objects.active = mesh_objs[0]

        # Join into one object
        bpy.ops.object.join()
        merged = context.view_layer.objects.active

        # Rename the merged object
        merged.name = "Collision"

        # Move to Colliders collection (join may have moved it to scene root)
        if merged.name not in [o.name for o in coll.objects]:
            for scene_coll in context.scene.collection.children_recursive:
                if merged in list(scene_coll.objects):
                    scene_coll.objects.unlink(merged)
            coll.objects.link(merged)

        # Run Merge by Distance to weld seam vertices
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.remove_doubles(threshold=self.merge_distance)
        bpy.ops.object.mode_set(mode='OBJECT')

        verts = len(merged.data.vertices)
        tris = sum(len(p.vertices) - 2 for p in merged.data.polygons)
        self.report({'INFO'},
                    f"Merged {len(mesh_objs)} collider(s) → '{merged.name}': "
                    f"{verts} verts, {tris} tris")
        return {'FINISHED'}


# ===========================================================================
# Material conversion operator
# ===========================================================================

class IGB_OT_convert_materials(Operator):
    """Set IGB custom properties on all scene materials for export compatibility"""
    bl_idname = "igb.convert_materials"
    bl_label = "Convert All to IGB"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # Collect materials used by visible mesh objects (exclude Colliders)
        colliders_coll = bpy.data.collections.get("Colliders")
        collider_objs = set(colliders_coll.objects) if colliders_coll else set()

        scene_mats = set()
        for obj in context.scene.objects:
            if obj.type != 'MESH':
                continue
            if obj in collider_objs:
                continue
            for slot in obj.material_slots:
                if slot.material is not None:
                    scene_mats.add(slot.material)

        if not scene_mats:
            self.report({'WARNING'}, "No materials found on scene meshes")
            return {'FINISHED'}

        converted = 0
        skipped = 0

        for mat in scene_mats:
            # Skip the collision helper material
            if mat.name == "IGB_Collision":
                continue

            # Check if already has IGB properties
            has_igb = any(key.startswith("igb_") for key in mat.keys())
            if has_igb:
                skipped += 1
                continue

            _convert_material_to_igb(mat)
            converted += 1

        self.report({'INFO'},
                    f"Converted {converted} material(s), "
                    f"skipped {skipped} already-IGB material(s)")
        return {'FINISHED'}


def _convert_material_to_igb(mat):
    """Set default IGB custom properties on a Blender material.

    Analyzes the material's Blender settings to infer appropriate IGB
    render state. Game files omit blend/alpha attrs entirely for opaque
    materials, so we only set those properties when actually needed.
    Color and lighting are always set.
    """
    # --- Detect if material uses transparency ---
    uses_blend = False
    uses_alpha_tex = False

    # Check Blender blend mode
    if hasattr(mat, 'surface_render_method'):
        uses_blend = (mat.surface_render_method == 'BLENDED')
    elif hasattr(mat, 'blend_method'):
        uses_blend = (mat.blend_method != 'OPAQUE')

    # Check node tree for alpha connections and BSDF alpha value
    if mat.use_nodes and mat.node_tree:
        for node in mat.node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                alpha_input = node.inputs.get('Alpha')
                if alpha_input is not None:
                    if alpha_input.is_linked:
                        uses_alpha_tex = True
                        uses_blend = True
                    elif alpha_input.default_value < 0.999:
                        uses_blend = True
                break

    # --- Blend State & Function (only when transparent) ---
    if uses_blend:
        mat["igb_blend_enabled"] = 1
        mat["igb_blend_src"] = 4   # SRC_ALPHA
        mat["igb_blend_dst"] = 5   # ONE_MINUS_SRC_ALPHA
        mat["igb_blend_eq"] = 0
        mat["igb_blend_constant"] = 0
        mat["igb_blend_stage"] = 0
        mat["igb_blend_a"] = 0
        mat["igb_blend_b"] = 0
        mat["igb_blend_c"] = 0
        mat["igb_blend_d"] = 0
    # Opaque: don't set blend props at all (game files omit them)

    # --- Alpha Test (only for cutout/alpha-tested materials) ---
    if uses_alpha_tex:
        mat["igb_alpha_test_enabled"] = 1
        mat["igb_alpha_func"] = 6    # GEQUAL
        mat["igb_alpha_ref"] = 0.5
        # Cutout pattern: alpha test ON, blend OFF
        if not uses_blend:
            mat["igb_blend_enabled"] = 0
    # Opaque without alpha tex: don't set alpha props at all

    # --- Color Attr (white = no tint) ---
    mat["igb_color_r"] = 1.0
    mat["igb_color_g"] = 1.0
    mat["igb_color_b"] = 1.0
    mat["igb_color_a"] = 1.0

    # --- Lighting ---
    mat["igb_lighting_enabled"] = 1

    # --- Backface Culling ---
    if mat.use_backface_culling:
        mat["igb_cull_face_enabled"] = 1
        mat["igb_cull_face_mode"] = 0  # matches game files


# ===========================================================================
# Helper: collect all IGB-ready scene materials
# ===========================================================================

def _get_igb_scene_mats(context, selected_only=False, skins_only=False):
    """Return set of IGB-ready materials used by visible scene meshes.

    Excludes objects in the Colliders collection and the IGB_Collision
    helper material.  Only returns materials that have at least one
    ``igb_`` custom property (i.e. already converted or imported).

    Args:
        selected_only: If True, only consider selected objects.
        skins_only: If True, only consider meshes registered in the
            IGB Actors skins list (so actor quick tools can't clobber
            map materials in a mixed scene).
    """
    colliders_coll = bpy.data.collections.get("Colliders")
    collider_objs = set(colliders_coll.objects) if colliders_coll else set()

    if skins_only:
        # Use the actor Materials panel's robust resolver: registered skins,
        # else the armature's skinned meshes, else the active mesh. This is why
        # the actor quick tools work even when the skins LIST is empty (an
        # imported actor whose meshes were never added to props.skins).
        try:
            from .actor.panels import _actor_material_meshes
            source_objects = _actor_material_meshes(context)
        except Exception:
            actor_props = getattr(context.scene, "igb_actor", None)
            skin_names = ({item.object_name for item in actor_props.skins}
                          if actor_props is not None else set())
            source_objects = [obj for obj in context.scene.objects
                              if obj.name in skin_names]
    elif selected_only:
        source_objects = context.selected_objects
    else:
        source_objects = context.scene.objects

    mats = set()
    for obj in source_objects:
        if obj.type != 'MESH' or obj in collider_objs:
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None or mat.name == "IGB_Collision":
                continue
            if any(k.startswith("igb_") for k in mat.keys()):
                mats.add(mat)
    return mats


# ===========================================================================
# Quick-access batch operators
# ===========================================================================

class IGB_OT_set_all_lighting(Operator):
    """Set lighting on/off for every IGB material.\n"""  \
    """When OFF the game ignores scene lights and renders the surface fully bright.\n"""  \
    """Useful for self-illuminated objects like lava, energy effects, or sky domes"""
    bl_idname = "igb.set_all_lighting"
    bl_label = "Set All Lighting"
    bl_options = {'REGISTER', 'UNDO'}

    enable: BoolProperty(
        name="Enable Lighting",
        description="Turn lighting on (1) or off (0) for all IGB materials",
        default=True,
    )
    selected_only: BoolProperty(default=False)

    def execute(self, context):
        mats = _get_igb_scene_mats(context, self.selected_only)
        if not mats:
            self.report({'WARNING'}, "No IGB materials found")
            return {'FINISHED'}
        for mat in mats:
            mat["igb_lighting_enabled"] = int(self.enable)
        state = "ON" if self.enable else "OFF"
        scope = "selected" if self.selected_only else "all"
        self.report({'INFO'}, f"Set lighting {state} on {len(mats)} {scope} material(s)")
        return {'FINISHED'}


class IGB_OT_set_all_culling(Operator):
    """Toggle backface culling for every IGB material.\n"""  \
    """ON = single-sided faces (game skips back faces for performance).\n"""  \
    """OFF = double-sided, needed for thin geometry like leaves or cloth"""
    bl_idname = "igb.set_all_culling"
    bl_label = "Set All Backface Culling"
    bl_options = {'REGISTER', 'UNDO'}

    enable: BoolProperty(
        name="Enable Culling",
        description="Turn backface culling on or off for all IGB materials",
        default=True,
    )
    selected_only: BoolProperty(default=False)

    def execute(self, context):
        mats = _get_igb_scene_mats(context, self.selected_only)
        if not mats:
            self.report({'WARNING'}, "No IGB materials found")
            return {'FINISHED'}
        for mat in mats:
            mat["igb_cull_face_enabled"] = int(self.enable)
            mat["igb_cull_face_mode"] = mat.get("igb_cull_face_mode", 0)
            mat.use_backface_culling = self.enable
        state = "ON" if self.enable else "OFF"
        scope = "selected" if self.selected_only else "all"
        self.report({'INFO'}, f"Set culling {state} on {len(mats)} {scope} material(s)")
        return {'FINISHED'}


class IGB_OT_set_all_uv_anim(Operator):
    """Set UV animation (igTextureMatrixStateAttr) on/off for all IGB materials.\n"""  \
    """Enables UV scrolling/rotation driven by the game engine.\n"""  \
    """Used for flowing water, energy beams, conveyor belts, etc."""
    bl_idname = "igb.set_all_uv_anim"
    bl_label = "Set All UV Animation"
    bl_options = {'REGISTER', 'UNDO'}

    enable: BoolProperty(
        name="Enable UV Animation",
        description="Turn UV animation on or off for all IGB materials",
        default=True,
    )
    unit_id: IntProperty(
        name="Texture Unit",
        description="Texture unit ID for the UV matrix (usually 0)",
        default=0, min=0, max=7,
    )
    selected_only: BoolProperty(default=False)

    def execute(self, context):
        mats = _get_igb_scene_mats(context, self.selected_only)
        if not mats:
            self.report({'WARNING'}, "No IGB materials found")
            return {'FINISHED'}
        for mat in mats:
            mat["igb_tex_matrix_enabled"] = int(self.enable)
            mat["igb_tex_matrix_unit_id"] = self.unit_id
        state = "ON" if self.enable else "OFF"
        scope = "selected" if self.selected_only else "all"
        self.report({'INFO'},
                    f"Set UV animation {state} (unit {self.unit_id}) "
                    f"on {len(mats)} {scope} material(s)")
        return {'FINISHED'}


class IGB_OT_set_all_alpha(Operator):
    """Turn alpha testing on/off for all IGB materials.\n"""  \
    """When ON, pixels below the alpha threshold are discarded (cutout effect).\n"""  \
    """Used for foliage, fences, grates — anything with sharp transparent edges"""
    bl_idname = "igb.set_all_alpha"
    bl_label = "Set All Alpha Test"
    bl_options = {'REGISTER', 'UNDO'}

    enable: BoolProperty(
        name="Enable Alpha Test",
        description="Turn alpha test on or off for all IGB materials",
        default=True,
    )
    selected_only: BoolProperty(default=False)

    def execute(self, context):
        mats = _get_igb_scene_mats(context, self.selected_only)
        if not mats:
            self.report({'WARNING'}, "No IGB materials found")
            return {'FINISHED'}
        for mat in mats:
            if self.enable:
                mat["igb_alpha_test_enabled"] = 1
                # Set defaults if not already present
                if mat.get("igb_alpha_func") is None:
                    mat["igb_alpha_func"] = 6    # GEQUAL
                if mat.get("igb_alpha_ref") is None:
                    mat["igb_alpha_ref"] = 0.5
            else:
                mat["igb_alpha_test_enabled"] = 0
        state = "ON" if self.enable else "OFF"
        scope = "selected" if self.selected_only else "all"
        self.report({'INFO'}, f"Set alpha test {state} on {len(mats)} {scope} material(s)")
        return {'FINISHED'}


class IGB_OT_set_all_blend(Operator):
    """Turn alpha blending on/off for all IGB materials.\n"""  \
    """When ON, surfaces can be semi-transparent (glass, water, smoke).\n"""  \
    """When OFF, surfaces are fully opaque (game omits blend attributes)"""
    bl_idname = "igb.set_all_blend"
    bl_label = "Set All Blend State"
    bl_options = {'REGISTER', 'UNDO'}

    enable: BoolProperty(
        name="Enable Blending",
        description="Turn alpha blending on or off for all IGB materials",
        default=True,
    )
    selected_only: BoolProperty(default=False)

    def execute(self, context):
        mats = _get_igb_scene_mats(context, self.selected_only)
        if not mats:
            self.report({'WARNING'}, "No IGB materials found")
            return {'FINISHED'}
        for mat in mats:
            if self.enable:
                mat["igb_blend_enabled"] = 1
                # Set standard alpha blend defaults if not present
                if mat.get("igb_blend_src") is None:
                    mat["igb_blend_src"] = 4   # SRC_ALPHA
                if mat.get("igb_blend_dst") is None:
                    mat["igb_blend_dst"] = 5   # ONE_MINUS_SRC_ALPHA
            else:
                mat["igb_blend_enabled"] = 0
        state = "ON" if self.enable else "OFF"
        scope = "selected" if self.selected_only else "all"
        self.report({'INFO'}, f"Set blending {state} on {len(mats)} {scope} material(s)")
        return {'FINISHED'}


class IGB_OT_set_all_color(Operator):
    """Set the igColorAttr tint on all IGB materials.\n"""  \
    """This color is multiplied with the texture in the game engine.\n"""  \
    """White (1,1,1,1) = no tint. Use darker values to dim surfaces, """  \
    """or tint to colorize geometry without changing the texture"""
    bl_idname = "igb.set_all_color"
    bl_label = "Set All Color Tint"
    bl_options = {'REGISTER', 'UNDO'}

    color: FloatVectorProperty(
        name="Color",
        description="RGBA color tint applied to all IGB materials",
        subtype='COLOR',
        size=4,
        min=0.0, max=1.0,
        default=(1.0, 1.0, 1.0, 1.0),
    )
    selected_only: BoolProperty(default=False)

    def execute(self, context):
        mats = _get_igb_scene_mats(context, self.selected_only)
        if not mats:
            self.report({'WARNING'}, "No IGB materials found")
            return {'FINISHED'}
        for mat in mats:
            mat["igb_color_r"] = self.color[0]
            mat["igb_color_g"] = self.color[1]
            mat["igb_color_b"] = self.color[2]
            mat["igb_color_a"] = self.color[3]
        scope = "selected" if self.selected_only else "all"
        self.report({'INFO'},
                    f"Set color ({self.color[0]:.2f}, {self.color[1]:.2f}, "
                    f"{self.color[2]:.2f}, {self.color[3]:.2f}) "
                    f"on {len(mats)} {scope} material(s)")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Material color batch operators
# ---------------------------------------------------------------------------


class IGB_OT_set_all_diffuse(Operator):
    """Set igMaterialAttr diffuse color on all IGB materials.\n"""  \
    """Diffuse is the base surface color used in fixed-function lighting.\n"""  \
    """Affects how the surface looks under direct light"""
    bl_idname = "igb.set_all_diffuse"
    bl_label = "Set All Diffuse"
    bl_options = {'REGISTER', 'UNDO'}

    color: FloatVectorProperty(
        name="Diffuse Color",
        description="RGBA diffuse color for all IGB materials",
        subtype='COLOR',
        size=4,
        min=0.0, max=1.0,
        default=(1.0, 1.0, 1.0, 1.0),
    )
    selected_only: BoolProperty(name="Selected Only", default=False)
    skins_only: BoolProperty(default=False, options={'HIDDEN'})
    show_dialog: BoolProperty(default=False, options={'HIDDEN'})

    def invoke(self, context, event):
        if self.show_dialog:
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def execute(self, context):
        mats = _get_igb_scene_mats(context, self.selected_only,
                                   self.skins_only)
        if not mats:
            self.report({'WARNING'}, "No IGB materials found")
            return {'FINISHED'}
        for mat in mats:
            mat["igb_diffuse"] = list(self.color)
        scope = ("skin" if self.skins_only
                 else "selected" if self.selected_only else "all")
        self.report({'INFO'},
                    f"Set diffuse on {len(mats)} {scope} material(s)")
        return {'FINISHED'}


class IGB_OT_set_all_ambient(Operator):
    """Set igMaterialAttr ambient color on all IGB materials.\n"""  \
    """Ambient controls how the surface responds to indirect/ambient light.\n"""  \
    """0.588 gray is the game standard; white = fully lit from all angles"""
    bl_idname = "igb.set_all_ambient"
    bl_label = "Set All Ambient"
    bl_options = {'REGISTER', 'UNDO'}

    color: FloatVectorProperty(
        name="Ambient Color",
        description="RGBA ambient color for all IGB materials",
        subtype='COLOR',
        size=4,
        min=0.0, max=1.0,
        default=(0.588, 0.588, 0.588, 1.0),
    )
    selected_only: BoolProperty(name="Selected Only", default=False)
    skins_only: BoolProperty(default=False, options={'HIDDEN'})
    show_dialog: BoolProperty(default=False, options={'HIDDEN'})

    def invoke(self, context, event):
        if self.show_dialog:
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def execute(self, context):
        mats = _get_igb_scene_mats(context, self.selected_only,
                                   self.skins_only)
        if not mats:
            self.report({'WARNING'}, "No IGB materials found")
            return {'FINISHED'}
        for mat in mats:
            mat["igb_ambient"] = list(self.color)
        scope = ("skin" if self.skins_only
                 else "selected" if self.selected_only else "all")
        self.report({'INFO'},
                    f"Set ambient on {len(mats)} {scope} material(s)")
        return {'FINISHED'}


class IGB_OT_set_all_specular(Operator):
    """Set igMaterialAttr specular color on all IGB materials.\n"""  \
    """Specular controls the highlight reflection color and intensity.\n"""  \
    """Black (0,0,0) = no specular highlights"""
    bl_idname = "igb.set_all_specular"
    bl_label = "Set All Specular"
    bl_options = {'REGISTER', 'UNDO'}

    color: FloatVectorProperty(
        name="Specular Color",
        description="RGBA specular color for all IGB materials",
        subtype='COLOR',
        size=4,
        min=0.0, max=1.0,
        default=(0.0, 0.0, 0.0, 1.0),
    )
    selected_only: BoolProperty(name="Selected Only", default=False)
    skins_only: BoolProperty(default=False, options={'HIDDEN'})
    show_dialog: BoolProperty(default=False, options={'HIDDEN'})

    def invoke(self, context, event):
        if self.show_dialog:
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def execute(self, context):
        mats = _get_igb_scene_mats(context, self.selected_only,
                                   self.skins_only)
        if not mats:
            self.report({'WARNING'}, "No IGB materials found")
            return {'FINISHED'}
        for mat in mats:
            mat["igb_specular"] = list(self.color)
        scope = ("skin" if self.skins_only
                 else "selected" if self.selected_only else "all")
        self.report({'INFO'},
                    f"Set specular on {len(mats)} {scope} material(s)")
        return {'FINISHED'}


class IGB_OT_set_all_emission(Operator):
    """Set igMaterialAttr emission color on all IGB materials.\n"""  \
    """Emission makes surfaces glow / self-illuminate.\n"""  \
    """Lava, energy effects, glowing eyes, light panels"""
    bl_idname = "igb.set_all_emission"
    bl_label = "Set All Emission"
    bl_options = {'REGISTER', 'UNDO'}

    color: FloatVectorProperty(
        name="Emission Color",
        description="RGBA emission color for all IGB materials",
        subtype='COLOR',
        size=4,
        min=0.0, max=1.0,
        default=(0.0, 0.0, 0.0, 1.0),
    )
    selected_only: BoolProperty(name="Selected Only", default=False)
    skins_only: BoolProperty(default=False, options={'HIDDEN'})
    show_dialog: BoolProperty(default=False, options={'HIDDEN'})

    def invoke(self, context, event):
        if self.show_dialog:
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def execute(self, context):
        mats = _get_igb_scene_mats(context, self.selected_only,
                                   self.skins_only)
        if not mats:
            self.report({'WARNING'}, "No IGB materials found")
            return {'FINISHED'}
        for mat in mats:
            mat["igb_emission"] = list(self.color)
        scope = ("skin" if self.skins_only
                 else "selected" if self.selected_only else "all")
        self.report({'INFO'},
                    f"Set emission on {len(mats)} {scope} material(s)")
        return {'FINISHED'}


def _apply_shininess(mat, value):
    """Set shininess on both the custom property AND the IGB Material node
    group's Shininess socket.

    The custom property (``igb_shininess``) is what EXPORT reads; the node
    group's Shininess socket is what the viewport SHADER reads. They are
    independent values, so updating only the property (the old behaviour)
    changed the panel number but left the shading tab untouched.
    """
    v = float(value)
    mat["igb_shininess"] = v
    try:
        from .utils.material_nodes import find_igb_node, set_igb_node_values
        node = find_igb_node(mat)
        if node is not None:
            set_igb_node_values(node, {'shininess': v})
    except Exception:
        pass


class IGB_OT_set_all_shininess(Operator):
    """Set igMaterialAttr shininess on all IGB materials.\n"""  \
    """Shininess (specular exponent) controls the size of specular highlights.\n"""  \
    """0 = no highlights, 128 = very tight/sharp highlights"""
    bl_idname = "igb.set_all_shininess"
    bl_label = "Set All Shininess"
    bl_options = {'REGISTER', 'UNDO'}

    shininess: FloatProperty(
        name="Shininess",
        description="Specular exponent (0-128) for all IGB materials",
        min=0.0, max=128.0,
        default=0.0,
    )
    selected_only: BoolProperty(name="Selected Only", default=False)
    skins_only: BoolProperty(default=False, options={'HIDDEN'})

    def execute(self, context):
        mats = _get_igb_scene_mats(context, self.selected_only,
                                   self.skins_only)
        if not mats:
            self.report({'WARNING'}, "No IGB materials found")
            return {'FINISHED'}
        for mat in mats:
            _apply_shininess(mat, self.shininess)
        scope = ("skin" if self.skins_only
                 else "selected" if self.selected_only else "all")
        self.report({'INFO'},
                    f"Set shininess={self.shininess:.1f} on {len(mats)} "
                    f"{scope} material(s)")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Shader type / blend mode quick swap
# ---------------------------------------------------------------------------

# Blender material blend mode presets for IGB materials.
# Each entry: (enum_id, label, description).
_BLEND_MODE_ITEMS = [
    ('OPAQUE', "Opaque", "Fully opaque — no transparency, fastest rendering"),
    ('CLIP', "Alpha Clip", "Hard cutout — pixels below threshold are discarded"),
    ('HASHED', "Alpha Hashed", "Dithered transparency — no sorting issues, slightly noisy"),
    ('BLEND', "Alpha Blend", "Smooth transparency — glass, water, smoke (needs sorting)"),
]


class IGB_OT_set_all_shader(Operator):
    """Swap the Blender shader type and blend mode for all IGB materials.\n"""  \
    """Shader: the node tree used (Principled BSDF or Emission-only).\n"""  \
    """Blend Mode: how Blender's EEVEE handles transparency for the material.\n"""  \
    """  OPAQUE — no transparency, fastest.\n"""  \
    """  ALPHA CLIP — hard cutout (foliage, fences).\n"""  \
    """  ALPHA HASHED — dithered (no sort issues).\n"""  \
    """  ALPHA BLEND — smooth semi-transparent (glass, water)"""
    bl_idname = "igb.set_all_shader"
    bl_label = "Set Shader / Blend Mode"
    bl_options = {'REGISTER', 'UNDO'}

    shader_type: EnumProperty(
        name="Shader",
        description="Which BSDF shader to use for all IGB materials",
        items=[
            ('PRINCIPLED', "Principled BSDF",
             "Full PBR shader — diffuse, specular, roughness, emission"),
            ('EMISSION', "Emission Only",
             "Flat unlit shader — texture color only, no lighting response"),
        ],
        default='PRINCIPLED',
    )
    blend_mode: EnumProperty(
        name="Blend Mode",
        description="How Blender handles transparency for these materials",
        items=_BLEND_MODE_ITEMS,
        default='OPAQUE',
    )
    selected_only: BoolProperty(default=False)

    def execute(self, context):
        mats = _get_igb_scene_mats(context, self.selected_only)
        if not mats:
            self.report({'WARNING'}, "No IGB materials found")
            return {'FINISHED'}

        for mat in mats:
            self._apply_shader(mat)
            self._apply_blend_mode(mat)

        scope = "selected" if self.selected_only else "all"
        self.report(
            {'INFO'},
            f"Set {self.shader_type} / {self.blend_mode} "
            f"on {len(mats)} {scope} material(s)")
        return {'FINISHED'}

    # ---- internal helpers ----

    def _apply_shader(self, mat):
        """Rebuild the shader node tree to match the chosen shader type."""
        if not mat.use_nodes or not mat.node_tree:
            return

        nodes = mat.node_tree.nodes
        links = mat.node_tree.links

        # Find existing key nodes
        output_node = None
        bsdf_node = None
        emission_node = None
        tex_node = None
        uv_node = None
        mix_node = None

        for n in nodes:
            if n.type == 'OUTPUT_MATERIAL':
                output_node = n
            elif n.type == 'BSDF_PRINCIPLED':
                bsdf_node = n
            elif n.type == 'EMISSION':
                emission_node = n
            elif n.type == 'TEX_IMAGE':
                tex_node = n
            elif n.type == 'UVMAP':
                uv_node = n
            elif n.type == 'MIX' and getattr(n, 'blend_type', None) == 'MULTIPLY':
                mix_node = n

        if output_node is None:
            return

        if self.shader_type == 'PRINCIPLED':
            # Remove emission node if present, add Principled BSDF if missing
            if emission_node is not None:
                nodes.remove(emission_node)
                emission_node = None

            if bsdf_node is None:
                bsdf_node = nodes.new('ShaderNodeBsdfPrincipled')
                bsdf_node.location = (0, 0)

            # Connect BSDF -> Output
            links.new(bsdf_node.outputs['BSDF'], output_node.inputs['Surface'])

            # Reconnect texture if available
            if tex_node is not None:
                if mix_node is not None:
                    # Color tint path: Texture -> Mix -> BSDF Base Color
                    links.new(tex_node.outputs['Color'], mix_node.inputs[6])
                    links.new(mix_node.outputs[2], bsdf_node.inputs['Base Color'])
                else:
                    links.new(tex_node.outputs['Color'],
                              bsdf_node.inputs['Base Color'])
                # Alpha
                links.new(tex_node.outputs['Alpha'], bsdf_node.inputs['Alpha'])

        elif self.shader_type == 'EMISSION':
            # Remove Principled BSDF if present, add Emission if missing
            if bsdf_node is not None:
                nodes.remove(bsdf_node)
                bsdf_node = None

            if emission_node is None:
                emission_node = nodes.new('ShaderNodeEmission')
                emission_node.location = (0, 0)
                emission_node.inputs['Strength'].default_value = 1.0

            # Connect Emission -> Output
            links.new(emission_node.outputs['Emission'],
                      output_node.inputs['Surface'])

            # Reconnect texture if available
            if tex_node is not None:
                if mix_node is not None:
                    links.new(tex_node.outputs['Color'], mix_node.inputs[6])
                    links.new(mix_node.outputs[2], emission_node.inputs['Color'])
                else:
                    links.new(tex_node.outputs['Color'],
                              emission_node.inputs['Color'])

    def _apply_blend_mode(self, mat):
        """Set the Blender material blend mode / render method."""
        mode = self.blend_mode

        if mode == 'OPAQUE':
            if hasattr(mat, 'surface_render_method'):
                mat.surface_render_method = 'DITHERED'
            elif hasattr(mat, 'blend_method'):
                mat.blend_method = 'OPAQUE'
            if hasattr(mat, 'shadow_method'):
                mat.shadow_method = 'OPAQUE'
            if hasattr(mat, 'alpha_threshold'):
                mat.alpha_threshold = 0.5

        elif mode == 'CLIP':
            if hasattr(mat, 'surface_render_method'):
                mat.surface_render_method = 'DITHERED'
            elif hasattr(mat, 'blend_method'):
                mat.blend_method = 'CLIP'
            if hasattr(mat, 'shadow_method'):
                mat.shadow_method = 'CLIP'
            if hasattr(mat, 'alpha_threshold'):
                mat.alpha_threshold = mat.get("igb_alpha_ref", 0.5)

        elif mode == 'HASHED':
            if hasattr(mat, 'surface_render_method'):
                mat.surface_render_method = 'DITHERED'
            elif hasattr(mat, 'blend_method'):
                mat.blend_method = 'HASHED'
            if hasattr(mat, 'shadow_method'):
                mat.shadow_method = 'HASHED'

        elif mode == 'BLEND':
            if hasattr(mat, 'surface_render_method'):
                mat.surface_render_method = 'BLENDED'
            elif hasattr(mat, 'blend_method'):
                mat.blend_method = 'BLEND'
            if hasattr(mat, 'shadow_method'):
                mat.shadow_method = 'CLIP'


# ===========================================================================
# IGB Tab — Parent container
# ===========================================================================

class IGB_PT_Main(Panel):
    """IGB Tools — Alchemy Engine Toolkit"""
    bl_label = "IGB Tools"
    bl_idname = "IGB_PT_Main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"

    def draw_header(self, context):
        icon_id = _get_icon_id()
        if icon_id:
            self.layout.label(icon_value=icon_id)

    def draw(self, context):
        pass  # Child sub-panels provide all content


# ===========================================================================
# Import / Export sub-panel
# ===========================================================================

class IGB_PT_ImportExport(Panel):
    """Import and export Alchemy Engine files"""
    bl_label = "Import / Export"
    bl_idname = "IGB_PT_ImportExport"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "IGB_PT_Main"

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.scale_y = 1.3
        col.operator("import_scene.igb", text="Import IGB (.igb)", icon='IMPORT')
        col.scale_y = 1.0
        # The generic importer loads skins as STATIC geometry (no rig) —
        # point character modders at the right pipeline
        col.label(text="Characters/skins: use the IGB Actors tab",
                  icon='INFO')
        col.scale_y = 1.3
        col.operator("import_scene.igz", text="Import IGZ (.igz)", icon='IMPORT')
        col.operator("mm.import_eng", text="Import ENG/ENGB (.engb)", icon='IMPORT')
        col.operator("mm.import_full_map", text="Import Full Map (.igb)", icon='IMPORT')

        # Models folder path (used by ENGB import to load entity IGB files)
        settings = context.scene.mm_settings
        col.scale_y = 1.0
        col.prop(settings, "models_dir")

        col.scale_y = 1.3
        col.separator()
        col.operator("export_scene.igb", text="Export IGB (.igb)", icon='EXPORT')


# ===========================================================================
# IGB Log sub-panel
# ===========================================================================

class IGB_OT_log_copy(bpy.types.Operator):
    """Copy the full IGB log to the clipboard"""
    bl_idname = "igb.log_copy"
    bl_label = "Copy IGB Log"

    def execute(self, context):
        from .utils.igb_log import as_text
        context.window_manager.clipboard = as_text()
        self.report({'INFO'}, "IGB log copied to clipboard")
        return {'FINISHED'}


class IGB_OT_log_clear(bpy.types.Operator):
    """Clear the IGB log"""
    bl_idname = "igb.log_clear"
    bl_label = "Clear IGB Log"

    def execute(self, context):
        from .utils.igb_log import clear
        clear()
        return {'FINISHED'}


class IGB_PT_Log(Panel):
    """Recent import/export messages (copyable — no console digging)"""
    bl_label = "IGB Log"
    bl_idname = "IGB_PT_Log"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "IGB_PT_Main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        from .utils.igb_log import entries
        layout = self.layout
        row = layout.row(align=True)
        row.operator("igb.log_copy", icon='COPYDOWN', text="Copy All")
        row.operator("igb.log_clear", icon='TRASH', text="Clear")
        items = entries(last=14)
        if not items:
            layout.label(text="No messages yet", icon='INFO')
            return
        col = layout.column(align=True)
        icons = {'ERROR': 'CANCEL', 'WARNING': 'ERROR'}
        for t, lv, m in reversed(items):
            col.label(text=f"{t}  {m[:64]}",
                      icon=icons.get(lv, 'DOT'))


# ===========================================================================
# Collision sub-panel (collapsible)
# ===========================================================================

class IGB_PT_Collision(Panel):
    """Collision geometry tools"""
    bl_label = "Collision"
    bl_idname = "IGB_PT_Collision"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "IGB_PT_Main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout

        # --- Generate section ---
        box = layout.box()
        box.label(text="Generate Colliders", icon='MOD_BUILD')
        col = box.column(align=True)
        col.scale_y = 1.2
        col.operator("igb.generate_box_colliders", icon='MESH_CUBE',
                      text="Box Colliders (Fastest)")
        col.operator("igb.generate_hull_colliders", icon='MESH_ICOSPHERE',
                      text="Convex Hulls (Tighter)")
        col.operator("igb.generate_decimated_colliders", icon='MOD_DECIM',
                      text="Decimated Mesh (Best Fit)")

        # --- Manual tools section ---
        box = layout.box()
        box.label(text="Manual Tools", icon='TOOL_SETTINGS')
        col = box.column(align=True)
        col.scale_y = 1.2
        col.operator("igb.create_colliders_collection", icon='COLLECTION_NEW')
        col.operator("igb.add_box_collider", icon='MESH_CUBE',
                      text="Add Box at Cursor")
        col.operator("igb.merge_colliders", icon='AUTOMERGE_ON')

        # --- Status display ---
        layout.separator()
        coll = bpy.data.collections.get("Colliders")
        if coll is not None:
            mesh_objs = [o for o in coll.objects if o.type == 'MESH']
            num_objs = len(mesh_objs)
            total_tris = 0
            for obj in mesh_objs:
                if obj.data is not None:
                    total_tris += sum(
                        len(p.vertices) - 2 for p in obj.data.polygons
                    )

            if total_tris > _MAX_RECOMMENDED_TRIS:
                layout.label(
                    text=f"Colliders: {num_objs} obj, {total_tris:,} tris",
                    icon='ERROR')
                layout.label(
                    text=f"  WARNING: >{_MAX_RECOMMENDED_TRIS:,} may cause issues!",
                    icon='BLANK1')
                layout.label(
                    text=f"  Game files max ~17,000 tris",
                    icon='BLANK1')
            elif total_tris > 0:
                layout.label(
                    text=f"Colliders: {num_objs} obj, {total_tris:,} tris",
                    icon='CHECKMARK')
            else:
                layout.label(
                    text=f"Colliders: {num_objs} obj (empty)",
                    icon='INFO')
        else:
            layout.label(text="No Colliders collection", icon='ERROR')


# ===========================================================================
# Materials sub-panel (collapsible)
# ===========================================================================

class IGB_PT_Materials(Panel):
    """Material conversion tools"""
    bl_label = "Materials"
    bl_idname = "IGB_PT_Materials"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "IGB_PT_Main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout

        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator("igb.convert_materials", icon='NODE_MATERIAL')

        row = layout.row(align=True)
        row.scale_y = 1.2
        row.operator("igb.resize_textures", icon='IMAGE_DATA')

        # Status: count IGB vs non-IGB materials on scene meshes
        colliders_coll = bpy.data.collections.get("Colliders")
        collider_objs = (set(colliders_coll.objects)
                         if colliders_coll else set())
        scene_mats = set()
        for obj in context.scene.objects:
            if obj.type != 'MESH' or obj in collider_objs:
                continue
            for slot in obj.material_slots:
                if (slot.material is not None
                        and slot.material.name != "IGB_Collision"):
                    scene_mats.add(slot.material)

        if scene_mats:
            igb_count = sum(
                1 for m in scene_mats
                if any(k.startswith("igb_") for k in m.keys())
            )
            total = len(scene_mats)
            layout.separator()
            if igb_count == total:
                layout.label(text=f"All {total} material(s) IGB-ready",
                             icon='CHECKMARK')
            else:
                layout.label(
                    text=f"{igb_count}/{total} material(s) IGB-ready",
                    icon='INFO')
        else:
            layout.separator()
            layout.label(text="No materials on scene meshes", icon='ERROR')


def _prev_pow2(n):
    """Largest power of 2 <= n (min 1)."""
    p = 1
    while p * 2 <= n:
        p *= 2
    return max(1, p)


def _texture_size_items(self, context):
    """Shared texture-size enum (same as the export operators)."""
    from .exporter.texture_size import texture_size_items
    return texture_size_items(self, context)


class IGB_OT_resize_textures(Operator):
    """Downscale every scene texture so its longest edge is at most the chosen
    size, PRESERVING aspect ratio (snapped to power-of-2). Only shrinks —
    textures already within the cap are left alone. Mutates the images in place
    (a deliberate pre-bake), so save a copy first if you need the originals"""
    bl_idname = "igb.resize_textures"
    bl_label = "Downscale Textures"
    bl_description = ("Downscale all scene textures to a max longest-edge size "
                      "(aspect-preserving) to shrink IGB file size")
    bl_options = {'REGISTER', 'UNDO'}

    max_size: EnumProperty(
        name="Max Texture Size",
        description="Cap the longest edge of every scene texture to this size",
        items=_texture_size_items,
    )

    def execute(self, context):
        # Collect all images used by scene materials
        images = set()
        for obj in context.scene.objects:
            if obj.type != 'MESH':
                continue
            for slot in obj.material_slots:
                mat = slot.material
                if mat is None or not mat.use_nodes or mat.node_tree is None:
                    continue
                for node in mat.node_tree.nodes:
                    if node.type == 'TEX_IMAGE' and node.image is not None:
                        images.add(node.image)

        if not images:
            self.report({'WARNING'}, "No textures found on scene materials")
            return {'FINISHED'}

        if self.max_size == 'original':
            self.report({'INFO'}, "Max = Original: nothing to downscale")
            return {'FINISHED'}
        cap = int(self.max_size)

        resized = 0
        skipped = 0
        for img in images:
            w, h = img.size[0], img.size[1]
            if w == 0 or h == 0 or max(w, h) <= cap:
                skipped += 1
                continue
            # Longest edge -> cap, other edge scaled proportionally, POT-snapped
            scale = cap / float(max(w, h))
            nw = _prev_pow2(max(1, int(round(w * scale))))
            nh = _prev_pow2(max(1, int(round(h * scale))))
            img.scale(nw, nh)
            resized += 1

        self.report({'INFO'},
                    f"Downscaled {resized} texture(s) to ≤{cap}px longest edge"
                    f" ({skipped} already within cap)")
        return {'FINISHED'}


# ===========================================================================
# Quick Tools sub-panel (collapsible, with nested children)
# ===========================================================================

class IGB_PT_QuickTools(Panel):
    """Batch-set IGB material properties"""
    bl_label = "Quick Tools"
    bl_idname = "IGB_PT_QuickTools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "IGB_PT_Main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.prop(context.scene, "igb_qt_selected_only",
                    toggle=True, icon='RESTRICT_SELECT_OFF')
        if context.scene.igb_qt_selected_only:
            sel_count = sum(1 for o in context.selected_objects if o.type == 'MESH')
            layout.label(text=f"Affects materials on {sel_count} selected mesh(es)",
                         icon='INFO')
        else:
            layout.label(text="Affects ALL IGB materials in scene",
                         icon='INFO')


class IGB_PT_QT_Lighting(Panel):
    """Lighting toggle for all IGB materials"""
    bl_label = "Lighting"
    bl_idname = "IGB_PT_QT_Lighting"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "IGB_PT_QuickTools"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        sel = context.scene.igb_qt_selected_only
        layout.label(text="Controls whether scene lights affect surfaces.")
        layout.label(text="OFF = fully bright (lava, sky, energy).")
        row = layout.row(align=True)
        op = row.operator("igb.set_all_lighting", text="Lighting ON",
                          icon='OUTLINER_OB_LIGHT')
        op.enable = True
        op.selected_only = sel
        op = row.operator("igb.set_all_lighting", text="Lighting OFF",
                          icon='LIGHT')
        op.enable = False
        op.selected_only = sel


class IGB_PT_QT_Culling(Panel):
    """Backface culling toggle for all IGB materials"""
    bl_label = "Backface Culling"
    bl_idname = "IGB_PT_QT_Culling"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "IGB_PT_QuickTools"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        sel = context.scene.igb_qt_selected_only
        layout.label(text="ON = single-sided (skips back faces).")
        layout.label(text="OFF = double-sided (leaves, cloth, thin geo).")
        row = layout.row(align=True)
        op = row.operator("igb.set_all_culling", text="Culling ON",
                          icon='SNAP_FACE')
        op.enable = True
        op.selected_only = sel
        op = row.operator("igb.set_all_culling", text="Culling OFF",
                          icon='FACESEL')
        op.enable = False
        op.selected_only = sel


class IGB_PT_QT_UVAnim(Panel):
    """UV animation toggle for all IGB materials"""
    bl_label = "UV Animation"
    bl_idname = "IGB_PT_QT_UVAnim"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "IGB_PT_QuickTools"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        sel = context.scene.igb_qt_selected_only
        layout.label(text="Enables UV scrolling/rotation by the engine.")
        layout.label(text="Water, energy beams, conveyor belts, etc.")
        row = layout.row(align=True)
        op = row.operator("igb.set_all_uv_anim", text="UV Anim ON",
                          icon='PLAY')
        op.enable = True
        op.selected_only = sel
        op = row.operator("igb.set_all_uv_anim", text="UV Anim OFF",
                          icon='PAUSE')
        op.enable = False
        op.selected_only = sel


class IGB_PT_QT_Alpha(Panel):
    """Alpha test toggle for all IGB materials"""
    bl_label = "Alpha Test"
    bl_idname = "IGB_PT_QT_Alpha"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "IGB_PT_QuickTools"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        sel = context.scene.igb_qt_selected_only
        layout.label(text="Discards pixels below alpha threshold (cutout).")
        layout.label(text="Foliage, fences, grates — sharp alpha edges.")
        row = layout.row(align=True)
        op = row.operator("igb.set_all_alpha", text="Alpha ON",
                          icon='CHECKBOX_HLT')
        op.enable = True
        op.selected_only = sel
        op = row.operator("igb.set_all_alpha", text="Alpha OFF",
                          icon='CHECKBOX_DEHLT')
        op.enable = False
        op.selected_only = sel


class IGB_PT_QT_Blend(Panel):
    """Blend state toggle for all IGB materials"""
    bl_label = "Blend State"
    bl_idname = "IGB_PT_QT_Blend"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "IGB_PT_QuickTools"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        sel = context.scene.igb_qt_selected_only
        layout.label(text="ON = semi-transparent (glass, water, smoke).")
        layout.label(text="OFF = fully opaque (no blend attributes).")
        row = layout.row(align=True)
        op = row.operator("igb.set_all_blend", text="Blend ON",
                          icon='SHADING_RENDERED')
        op.enable = True
        op.selected_only = sel
        op = row.operator("igb.set_all_blend", text="Blend OFF",
                          icon='SHADING_SOLID')
        op.enable = False
        op.selected_only = sel


class IGB_PT_QT_Color(Panel):
    """Color tint for all IGB materials"""
    bl_label = "Color Tint"
    bl_idname = "IGB_PT_QT_Color"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "IGB_PT_QuickTools"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        sel = context.scene.igb_qt_selected_only
        layout.label(text="Multiplied with texture in-game.")
        layout.label(text="White = no tint. Darker = dims surfaces.")
        row = layout.row(align=True)
        op = row.operator("igb.set_all_color", text="Set Color Tint",
                          icon='COLORSET_01_VEC')
        op.selected_only = sel


class IGB_PT_QT_Shader(Panel):
    """Shader type and blend mode for all IGB materials"""
    bl_label = "Shader / Blend Mode"
    bl_idname = "IGB_PT_QT_Shader"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "IGB_PT_QuickTools"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        sel = context.scene.igb_qt_selected_only

        layout.label(text="Swap the shader node & Blender blend mode")
        layout.label(text="for all IGB materials at once.")

        # Shader type row
        box = layout.box()
        box.label(text="Shader Type:", icon='NODE_MATERIAL')
        col = box.column(align=True)

        row = col.row(align=True)
        op = row.operator("igb.set_all_shader", text="Principled BSDF",
                          icon='SHADING_RENDERED')
        op.shader_type = 'PRINCIPLED'
        op.blend_mode = context.scene.get("igb_qt_blend_mode", 'OPAQUE') or 'OPAQUE'
        op.selected_only = sel

        op = row.operator("igb.set_all_shader", text="Emission Only",
                          icon='LIGHT_SUN')
        op.shader_type = 'EMISSION'
        op.blend_mode = context.scene.get("igb_qt_blend_mode", 'OPAQUE') or 'OPAQUE'
        op.selected_only = sel

        # Blend mode buttons
        box = layout.box()
        box.label(text="Blend Mode:", icon='MATERIAL')
        col = box.column(align=True)

        row = col.row(align=True)
        op = row.operator("igb.set_all_shader", text="Opaque",
                          icon='SHADING_SOLID')
        op.shader_type = context.scene.get("igb_qt_shader_type", 'PRINCIPLED') or 'PRINCIPLED'
        op.blend_mode = 'OPAQUE'
        op.selected_only = sel

        op = row.operator("igb.set_all_shader", text="Alpha Clip",
                          icon='SNAP_FACE')
        op.shader_type = context.scene.get("igb_qt_shader_type", 'PRINCIPLED') or 'PRINCIPLED'
        op.blend_mode = 'CLIP'
        op.selected_only = sel

        row = col.row(align=True)
        op = row.operator("igb.set_all_shader", text="Alpha Hashed",
                          icon='TEXTURE')
        op.shader_type = context.scene.get("igb_qt_shader_type", 'PRINCIPLED') or 'PRINCIPLED'
        op.blend_mode = 'HASHED'
        op.selected_only = sel

        op = row.operator("igb.set_all_shader", text="Alpha Blend",
                          icon='SHADING_RENDERED')
        op.shader_type = context.scene.get("igb_qt_shader_type", 'PRINCIPLED') or 'PRINCIPLED'
        op.blend_mode = 'BLEND'
        op.selected_only = sel

        # Combined presets
        layout.separator()
        box = layout.box()
        box.label(text="Quick Presets:", icon='PRESET')
        col = box.column(align=True)

        row = col.row(align=True)
        op = row.operator("igb.set_all_shader", text="PBR Opaque",
                          icon='OBJECT_DATA')
        op.shader_type = 'PRINCIPLED'
        op.blend_mode = 'OPAQUE'
        op.selected_only = sel

        op = row.operator("igb.set_all_shader", text="PBR Transparent",
                          icon='GHOST_ENABLED')
        op.shader_type = 'PRINCIPLED'
        op.blend_mode = 'BLEND'
        op.selected_only = sel

        row = col.row(align=True)
        op = row.operator("igb.set_all_shader", text="Unlit Opaque",
                          icon='LIGHT_SUN')
        op.shader_type = 'EMISSION'
        op.blend_mode = 'OPAQUE'
        op.selected_only = sel

        op = row.operator("igb.set_all_shader", text="Unlit Cutout",
                          icon='IMAGE_ALPHA')
        op.shader_type = 'EMISSION'
        op.blend_mode = 'CLIP'
        op.selected_only = sel


class IGB_PT_QT_MaterialColors(Panel):
    """Material color properties (diffuse, ambient, specular, emission, shininess)"""
    bl_label = "Material Colors"
    bl_idname = "IGB_PT_QT_MaterialColors"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "IGB_PT_QuickTools"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        sel = context.scene.igb_qt_selected_only
        layout.label(text="Set igMaterialAttr colors on all IGB materials.")
        layout.label(text="These affect in-game fixed-function lighting.")

        # Diffuse
        box = layout.box()
        box.label(text="Diffuse", icon='MATERIAL')
        box.label(text="Base surface color under direct light.")
        op = box.operator("igb.set_all_diffuse", text="Set Diffuse",
                          icon='COLORSET_03_VEC')
        op.selected_only = sel

        # Ambient
        box = layout.box()
        box.label(text="Ambient", icon='WORLD')
        box.label(text="Indirect light response. 0.588 = game default.")
        row = box.row(align=True)
        op = row.operator("igb.set_all_ambient", text="Game Default",
                          icon='COLORSET_12_VEC')
        op.color = (0.588, 0.588, 0.588, 1.0)
        op.selected_only = sel
        op = row.operator("igb.set_all_ambient", text="Set Ambient",
                          icon='COLORSET_01_VEC')
        op.selected_only = sel

        # Specular
        box = layout.box()
        box.label(text="Specular", icon='LIGHT_POINT')
        box.label(text="Highlight reflection color. Black = no highlights.")
        row = box.row(align=True)
        op = row.operator("igb.set_all_specular", text="No Specular",
                          icon='COLORSET_13_VEC')
        op.color = (0.0, 0.0, 0.0, 1.0)
        op.selected_only = sel
        op = row.operator("igb.set_all_specular", text="Set Specular",
                          icon='COLORSET_01_VEC')
        op.selected_only = sel

        # Emission
        box = layout.box()
        box.label(text="Emission", icon='LIGHT_SUN')
        box.label(text="Self-illumination. Lava, energy, glowing effects.")
        row = box.row(align=True)
        op = row.operator("igb.set_all_emission", text="No Emission",
                          icon='COLORSET_13_VEC')
        op.color = (0.0, 0.0, 0.0, 1.0)
        op.selected_only = sel
        op = row.operator("igb.set_all_emission", text="Set Emission",
                          icon='COLORSET_01_VEC')
        op.selected_only = sel

        # Shininess
        box = layout.box()
        box.label(text="Shininess", icon='SHADING_RENDERED')
        box.label(text="Specular exponent: higher = tighter = less shiny.")
        row = box.row(align=True)
        op = row.operator("igb.set_all_shininess", text="Soft (native)",
                          icon='MATPLANE')
        op.shininess = 1.28
        op.selected_only = sel
        op = row.operator("igb.set_all_shininess", text="Less Shiny",
                          icon='SHADING_RENDERED')
        op.shininess = 32.0
        op.selected_only = sel
        op = row.operator("igb.set_all_shininess", text="Set Value",
                          icon='COLORSET_01_VEC')
        op.selected_only = sel


# ===========================================================================
# Merge Duplicate Materials operator
# ===========================================================================

class IGB_OT_merge_duplicate_materials(Operator):
    """Merge duplicate IGB materials that share the same texture.\n"""  \
    """Materials imported from IGB files often have duplicates like\n"""  \
    """'Mat.001', 'Mat.002' that use the same texture image and similar\n"""  \
    """properties. This operator detects and merges them, replacing all\n"""  \
    """references on mesh objects with the single canonical material"""
    bl_idname = "igb.merge_duplicate_materials"
    bl_label = "Merge Duplicate Materials"
    bl_options = {'REGISTER', 'UNDO'}

    selected_only: BoolProperty(default=False)

    def execute(self, context):
        # Collect relevant materials
        colliders_coll = bpy.data.collections.get("Colliders")
        collider_objs = set(colliders_coll.objects) if colliders_coll else set()

        if self.selected_only:
            source_objects = [o for o in context.selected_objects
                              if o.type == 'MESH' and o not in collider_objs]
        else:
            source_objects = [o for o in context.scene.objects
                              if o.type == 'MESH' and o not in collider_objs]

        if not source_objects:
            self.report({'WARNING'}, "No mesh objects found")
            return {'FINISHED'}

        # Build a fingerprint for each material:
        # (texture_image_name, base_color_approx, blend_enabled, alpha_test_enabled)
        # Materials with the same fingerprint are duplicates.
        def _mat_fingerprint(mat):
            """Build a hashable fingerprint for duplicate detection."""
            if mat is None:
                return None

            tex_name = ""
            base_color = (1.0, 1.0, 1.0)

            if mat.use_nodes and mat.node_tree:
                for node in mat.node_tree.nodes:
                    if node.type == 'TEX_IMAGE' and node.image:
                        # Use the underlying image data name, not the node name
                        tex_name = node.image.name
                        break
                    elif node.type == 'BSDF_PRINCIPLED':
                        bc = node.inputs['Base Color'].default_value
                        base_color = (round(bc[0], 2), round(bc[1], 2),
                                      round(bc[2], 2))

            # Include IGB custom properties in the fingerprint
            blend_en = mat.get("igb_blend_enabled", -1)
            alpha_en = mat.get("igb_alpha_test_enabled", -1)
            blend_src = mat.get("igb_blend_src", -1)
            blend_dst = mat.get("igb_blend_dst", -1)
            cull = mat.get("igb_cull_face_enabled", -1)

            return (tex_name, base_color, blend_en, alpha_en,
                    blend_src, blend_dst, cull)

        # Group materials by fingerprint
        fingerprint_map = {}  # fingerprint -> [mat, mat, ...]
        all_mats = set()
        for obj in source_objects:
            for slot in obj.material_slots:
                if slot.material and slot.material.name != "IGB_Collision":
                    all_mats.add(slot.material)

        for mat in all_mats:
            fp = _mat_fingerprint(mat)
            if fp is None:
                continue
            fingerprint_map.setdefault(fp, []).append(mat)

        # For each group with >1 material, keep the one with the shortest
        # name (the "original") and remap the rest
        remap = {}  # duplicate_mat -> canonical_mat
        merge_count = 0

        for fp, mats in fingerprint_map.items():
            if len(mats) <= 1:
                continue
            # Sort by name length, then alphabetically — shortest = canonical
            mats.sort(key=lambda m: (len(m.name), m.name))
            canonical = mats[0]
            for dup in mats[1:]:
                remap[dup] = canonical
                merge_count += 1

        if not remap:
            self.report({'INFO'}, "No duplicate materials found")
            return {'FINISHED'}

        # Apply remap: replace material slots on all objects
        replaced = 0
        for obj in source_objects:
            for i, slot in enumerate(obj.material_slots):
                if slot.material in remap:
                    slot.material = remap[slot.material]
                    replaced += 1

        # Clean up orphan materials (they'll have 0 users after remap)
        removed_names = []
        for dup_mat, canon_mat in remap.items():
            if dup_mat.users == 0:
                removed_names.append(dup_mat.name)
                bpy.data.materials.remove(dup_mat)

        scope = "selected" if self.selected_only else "all"
        self.report(
            {'INFO'},
            f"Merged {merge_count} duplicate material(s) on {scope} objects. "
            f"{replaced} slot(s) remapped, {len(removed_names)} orphan(s) removed.")
        return {'FINISHED'}


# ===========================================================================
# IGB Extras sub-panel (collapsible)
# ===========================================================================

class IGB_PT_Extras(Panel):
    """Extra utilities for IGB materials and meshes"""
    bl_label = "IGB Extras"
    bl_idname = "IGB_PT_Extras"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "IGB_PT_Main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        sel = context.scene.igb_qt_selected_only

        # --- Merge Duplicate Materials ---
        box = layout.box()
        box.label(text="Material Cleanup", icon='MATERIAL')
        box.label(text="Merge materials with identical textures")
        box.label(text="and IGB properties (removes .001 etc.)")
        col = box.column(align=True)
        col.scale_y = 1.2
        op = col.operator("igb.merge_duplicate_materials",
                          text="Merge Duplicate Materials",
                          icon='AUTOMERGE_ON')
        op.selected_only = sel

        # Show duplicate count preview
        colliders_coll = bpy.data.collections.get("Colliders")
        collider_objs = set(colliders_coll.objects) if colliders_coll else set()
        all_mats = set()
        for obj in context.scene.objects:
            if obj.type == 'MESH' and obj not in collider_objs:
                for slot in obj.material_slots:
                    if slot.material and slot.material.name != "IGB_Collision":
                        all_mats.add(slot.material)

        if all_mats:
            # Quick duplicate count: materials whose base name (without .NNN suffix)
            # matches another material
            import re
            base_names = {}
            for m in all_mats:
                base = re.sub(r'\.\d{3,}$', '', m.name)
                base_names.setdefault(base, []).append(m.name)
            dupes = sum(len(v) - 1 for v in base_names.values() if len(v) > 1)
            if dupes > 0:
                layout.label(
                    text=f"{len(all_mats)} materials, ~{dupes} potential duplicate(s)",
                    icon='INFO')
            else:
                layout.label(text=f"{len(all_mats)} materials (no obvious duplicates)",
                             icon='CHECKMARK')

        # --- PySide6 Install ---
        layout.separator()
        box = layout.box()
        box.label(text="Conversation Editor", icon='WINDOW')
        try:
            from .mapmaker.operators import _has_pyside6
            has_pyside6 = _has_pyside6()
        except Exception:
            has_pyside6 = False

        if has_pyside6:
            box.label(text="PySide6 installed", icon='CHECKMARK')
        else:
            box.label(text="PySide6 required for external editor")
            box.operator("mm.install_pyside6", text="Install PySide6", icon='IMPORT')
            box.label(text="Restart Blender after install", icon='INFO')


# ===========================================================================
# Credits sub-panel (collapsible)
# ===========================================================================

class IGB_OT_update_addon(Operator):
    """Download the latest addon version from GitHub and install it"""
    bl_idname = "igb.update_addon"
    bl_label = "Update Addon"

    _thread = None
    _timer = None
    _result = None  # (success: bool, message: str)

    def execute(self, context):
        # Launch download in background thread
        import threading
        IGB_OT_update_addon._result = None
        addon_dir = os.path.dirname(os.path.abspath(__file__))
        IGB_OT_update_addon._thread = threading.Thread(
            target=self._download_and_install, args=(addon_dir,))
        IGB_OT_update_addon._thread.start()

        # Start modal timer to check completion
        wm = context.window_manager
        IGB_OT_update_addon._timer = wm.event_timer_add(0.5, window=context.window)
        wm.modal_handler_add(self)
        self.report({'INFO'}, "Downloading update from GitHub...")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}

        if IGB_OT_update_addon._thread is not None and IGB_OT_update_addon._thread.is_alive():
            return {'PASS_THROUGH'}

        # Thread finished
        wm = context.window_manager
        if IGB_OT_update_addon._timer is not None:
            wm.event_timer_remove(IGB_OT_update_addon._timer)
            IGB_OT_update_addon._timer = None

        result = IGB_OT_update_addon._result
        if result is None:
            self.report({'ERROR'}, "Update failed: no result from download thread")
            return {'CANCELLED'}

        success, message = result
        self.report({'INFO'} if success else {'ERROR'}, message)
        return {'FINISHED'} if success else {'CANCELLED'}

    @staticmethod
    def _download_and_install(addon_dir):
        import urllib.request
        import zipfile
        import tempfile
        import shutil

        zip_url = "https://github.com/KaikoClanworth1/igb-blender/archive/refs/heads/master.zip"

        try:
            # Download zip to temp file
            tmp_zip = os.path.join(tempfile.gettempdir(), "igb_blender_update.zip")
            urllib.request.urlretrieve(zip_url, tmp_zip)

            # Extract to temp dir
            tmp_extract = os.path.join(tempfile.gettempdir(), "igb_blender_extract")
            if os.path.exists(tmp_extract):
                shutil.rmtree(tmp_extract)

            with zipfile.ZipFile(tmp_zip, 'r') as zf:
                zf.extractall(tmp_extract)

            # Find the root folder inside the zip (e.g., igb-blender-master/)
            extracted_items = os.listdir(tmp_extract)
            if len(extracted_items) == 1 and os.path.isdir(
                    os.path.join(tmp_extract, extracted_items[0])):
                src_root = os.path.join(tmp_extract, extracted_items[0])
            else:
                src_root = tmp_extract

            # Copy all files from extracted zip over the addon directory
            for dirpath, dirnames, filenames in os.walk(src_root):
                # Skip .git directories
                dirnames[:] = [d for d in dirnames
                               if d != '.git' and d != '__pycache__']

                rel_dir = os.path.relpath(dirpath, src_root)
                dst_dir = os.path.join(addon_dir, rel_dir) if rel_dir != '.' else addon_dir

                os.makedirs(dst_dir, exist_ok=True)

                for fname in filenames:
                    src_file = os.path.join(dirpath, fname)
                    dst_file = os.path.join(dst_dir, fname)
                    shutil.copy2(src_file, dst_file)

            # Clear all __pycache__ directories
            for dirpath, dirnames, _ in os.walk(addon_dir):
                for d in dirnames:
                    if d == '__pycache__':
                        shutil.rmtree(os.path.join(dirpath, d),
                                      ignore_errors=True)

            # Cleanup temp files
            try:
                os.remove(tmp_zip)
                shutil.rmtree(tmp_extract, ignore_errors=True)
            except OSError:
                pass

            IGB_OT_update_addon._result = (
                True,
                "Update complete! Please restart Blender to apply changes."
            )

        except Exception as e:
            IGB_OT_update_addon._result = (
                False, f"Update failed: {e}"
            )


class IGB_PT_Credits(Panel):
    """Credits and references"""
    bl_label = "Credits & References"
    bl_idname = "IGB_PT_Credits"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "IGB_PT_Main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout

        from . import bl_info
        version = bl_info.get("version", (0, 0, 0))
        layout.label(text=f"IGB Format Addon v{version[0]}.{version[1]}.{version[2]} by Kaiko")
        layout.separator()

        layout.operator("igb.update_addon", icon='URL')
        layout.separator()

        layout.label(text="Reference projects:")
        col = layout.column(align=True)
        col.scale_y = 0.8
        col.label(text="IGBConverter (nikita488) - MIT")
        col.label(text="IGBDataExtractor (ChmtTnky)")
        col.label(text="igBlender (ak2yny) - GPLv3")
        col.label(text="raven-formats (nikita488) - MIT")
        col.label(text="Crash-NST-Level-Editor (kishimisu)")
        col.label(text="IGB parser gist (mateon1)")
        col.label(text="fmt_alchemy_igz (Nefarious/ak2yny)")


# ===========================================================================
# IGB Material State Panel (Material Properties)
# ===========================================================================

# Human-readable names for blend function enums
_BLEND_FUNC_NAMES = {
    0: "ZERO", 1: "ONE", 2: "SRC_COLOR", 3: "1-SRC_COLOR",
    4: "SRC_ALPHA", 5: "1-SRC_ALPHA", 6: "DST_COLOR", 7: "1-DST_COLOR",
    8: "DST_ALPHA", 9: "1-DST_ALPHA", 10: "SRC_ALPHA_SAT",
}

# Human-readable names for alpha function enums
_ALPHA_FUNC_NAMES = {
    0: "NEVER", 1: "LESS", 2: "EQUAL", 3: "LEQUAL",
    4: "GREATER", 5: "NOTEQUAL", 6: "GEQUAL", 7: "ALWAYS",
}

# Common blend mode display names
_BLEND_MODE_NAMES = {
    (4, 5): "Standard Alpha",
    (4, 1): "Additive",
    (1, 0): "Replace (ONE, ZERO)",
}


def _draw_material_colors(layout, mat):
    """Draw the igMaterialAttr color properties section.

    Shared between the Properties > Material panel (IGB_PT_MaterialState)
    and the IGB Actors panel material sub-section.
    """
    box = layout.box()
    row = box.row()
    row.label(text="Material Colors", icon='MATERIAL')

    if mat.get("igb_diffuse") is not None:
        # Diffuse
        sub = box.box()
        sub.label(text="Diffuse (base surface color)")
        row = sub.row(align=True)
        d = mat.get("igb_diffuse", [1, 1, 1, 1])
        row.label(text=f"({d[0]:.2f}, {d[1]:.2f}, {d[2]:.2f}, {d[3]:.2f})")
        col = sub.column(align=True)
        for i, ch in enumerate(("R", "G", "B", "A")):
            col.prop(mat, f'["igb_diffuse"]', index=i, text=f"Diffuse {ch}")

        # Ambient
        sub = box.box()
        sub.label(text="Ambient (indirect light response)")
        row = sub.row(align=True)
        a = mat.get("igb_ambient", [0, 0, 0, 1])
        row.label(text=f"({a[0]:.2f}, {a[1]:.2f}, {a[2]:.2f}, {a[3]:.2f})")
        col = sub.column(align=True)
        for i, ch in enumerate(("R", "G", "B", "A")):
            col.prop(mat, f'["igb_ambient"]', index=i, text=f"Ambient {ch}")

        # Specular
        sub = box.box()
        sub.label(text="Specular (highlight reflection)")
        row = sub.row(align=True)
        s = mat.get("igb_specular", [0, 0, 0, 0])
        row.label(text=f"({s[0]:.2f}, {s[1]:.2f}, {s[2]:.2f}, {s[3]:.2f})")
        col = sub.column(align=True)
        for i, ch in enumerate(("R", "G", "B", "A")):
            col.prop(mat, f'["igb_specular"]', index=i, text=f"Specular {ch}")

        # Emission
        sub = box.box()
        sub.label(text="Emission (self-illumination)")
        row = sub.row(align=True)
        e = mat.get("igb_emission", [0, 0, 0, 0])
        row.label(text=f"({e[0]:.2f}, {e[1]:.2f}, {e[2]:.2f}, {e[3]:.2f})")
        col = sub.column(align=True)
        for i, ch in enumerate(("R", "G", "B", "A")):
            col.prop(mat, f'["igb_emission"]', index=i, text=f"Emission {ch}")

        # Shininess (specular EXPONENT). Higher = tighter/smaller highlight =
        # LESS overall shine. Very low (near 0) spreads full specular over the
        # whole surface = "very shiny". Native MUA = 1.28; raise it to tame a
        # too-shiny model. To remove specular entirely, untick the Specular map
        # or set Specular colour to black.
        sub = box.box()
        sub.label(text="Shininess (higher = tighter = less shiny)")
        sub.prop(mat, '["igb_shininess"]', text="Shininess", slider=True)
        prow = sub.row(align=True)
        prow.label(text="Presets:")
        for val, lbl in ((1.28, "Soft"), (8.0, "Medium"), (32.0, "Tight")):
            op = prow.operator("igb.set_shininess", text=lbl)
            op.value = val
            op.material_name = mat.name
        sub.label(text="0 exports as 1.28 (native) — never full-surface shine.",
                  icon='INFO')

        # Flags
        if mat.get("igb_flags") is not None:
            sub = box.box()
            sub.label(text="Material Flags (bitmask)")
            sub.prop(mat, '["igb_flags"]', text="Flags")

        # Priority (inherited igAttr base Short sort key; v6/v8=0, v4=1)
        if mat.get("igb_priority") is not None:
            sub = box.box()
            sub.label(text="Priority (render sort key)")
            sub.prop(mat, '["igb_priority"]', text="Priority")
    else:
        box.label(text="No igMaterialAttr data on this material.")
        box.label(text="Import an IGB file or use Quick Tools to set.")


class IGB_OT_set_shininess(Operator):
    """Set the igMaterialAttr shininess (specular power) to a preset value"""
    bl_idname = "igb.set_shininess"
    bl_label = "Set Shininess"
    bl_options = {'REGISTER', 'UNDO'}

    value: FloatProperty(name="Shininess", default=0.0, min=0.0, max=128.0)
    material_name: StringProperty(default="")

    def execute(self, context):
        mat = bpy.data.materials.get(self.material_name)
        if mat is None:
            mat = context.material
        if mat is None:
            self.report({'ERROR'}, "No material")
            return {'CANCELLED'}
        _apply_shininess(mat, self.value)
        return {'FINISHED'}


class IGB_OT_add_material_node(Operator):
    """Add IGB Material Settings node group to the active material"""
    bl_idname = "igb.add_material_node"
    bl_label = "Add IGB Settings Node"
    bl_description = "Add an IGB Material Settings node group to the shader editor"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.material is not None

    def execute(self, context):
        mat = context.material
        from .utils.material_nodes import find_igb_node, migrate_custom_props_to_node, add_igb_node_to_material

        existing = find_igb_node(mat)
        if existing is not None:
            self.report({'INFO'}, "IGB Settings node already exists")
            return {'FINISHED'}

        # If material has igb_* custom props, migrate them to the node
        has_igb = any(key.startswith("igb_") for key in mat.keys())
        if has_igb:
            migrate_custom_props_to_node(mat)
            self.report({'INFO'}, "Migrated IGB properties to shader node")
        else:
            add_igb_node_to_material(mat)
            self.report({'INFO'}, "Added IGB Settings node with defaults")

        return {'FINISHED'}


class IGB_PT_MaterialState(Panel):
    """IGB Material State — shows Alchemy render attributes on the material."""
    bl_label = "IGB Material State"
    bl_idname = "IGB_PT_MaterialState"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "material"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.material is not None

    def draw(self, context):
        layout = self.layout
        mat = context.material

        # --- IGB Node Group button ---
        from .utils.material_nodes import find_igb_node
        igb_node = find_igb_node(mat)
        if igb_node is not None:
            layout.label(text="IGB Settings node active", icon='CHECKMARK')
            layout.label(text="Edit values in the Shader Editor")
        else:
            layout.operator("igb.add_material_node", icon='NODE')

        layout.separator()

        # Check if this material has any IGB properties
        has_igb = any(key.startswith("igb_") for key in mat.keys())
        if not has_igb and igb_node is None:
            layout.label(text="No IGB properties on this material.")
            layout.label(text="Import an IGB file to see state here.")
            return

        # --- Material Colors (igMaterialAttr) ---
        _draw_material_colors(layout, mat)

        # --- Blend State ---
        box = layout.box()
        row = box.row()
        row.label(text="Blend State", icon='MOD_OPACITY')
        blend_en = mat.get("igb_blend_enabled")
        if blend_en is not None:
            row.label(text="ON" if blend_en else "OFF")
            box.prop(mat, '["igb_blend_enabled"]', text="Enabled")
            if blend_en:
                src = mat.get("igb_blend_src", 4)
                dst = mat.get("igb_blend_dst", 5)
                mode_name = _BLEND_MODE_NAMES.get(
                    (src, dst),
                    f"{_BLEND_FUNC_NAMES.get(src, str(src))} / "
                    f"{_BLEND_FUNC_NAMES.get(dst, str(dst))}")
                box.label(text=f"Mode: {mode_name}")
                row = box.row()
                row.prop(mat, '["igb_blend_src"]', text="Source")
                row.prop(mat, '["igb_blend_dst"]', text="Dest")
        else:
            row.label(text="Opaque (no blend)")

        # --- Alpha Test ---
        box = layout.box()
        row = box.row()
        row.label(text="Alpha Test", icon='IMAGE_ALPHA')
        alpha_en = mat.get("igb_alpha_test_enabled")
        if alpha_en is not None:
            row.label(text="ON" if alpha_en else "OFF")
            box.prop(mat, '["igb_alpha_test_enabled"]', text="Enabled")
            if alpha_en:
                func_val = mat.get("igb_alpha_func", 6)
                func_name = _ALPHA_FUNC_NAMES.get(func_val, str(func_val))
                ref_val = mat.get("igb_alpha_ref", 0.5)
                box.label(text=f"Function: {func_name}, Ref: {ref_val:.3f}")
                box.prop(mat, '["igb_alpha_func"]', text="Function")
                box.prop(mat, '["igb_alpha_ref"]', text="Reference")
        else:
            row.label(text="Off (no alpha test)")

        # --- Color Tint ---
        box = layout.box()
        row = box.row()
        row.label(text="Color Tint", icon='COLOR')
        if mat.get("igb_color_r") is not None:
            r = mat.get("igb_color_r", 1.0)
            g = mat.get("igb_color_g", 1.0)
            b = mat.get("igb_color_b", 1.0)
            a = mat.get("igb_color_a", 1.0)
            is_white = (abs(r - 1) < 0.001 and abs(g - 1) < 0.001
                        and abs(b - 1) < 0.001 and abs(a - 1) < 0.001)
            row.label(text="White (default)" if is_white else f"({r:.2f}, {g:.2f}, {b:.2f}, {a:.2f})")
            col = box.column(align=True)
            col.prop(mat, '["igb_color_r"]', text="R")
            col.prop(mat, '["igb_color_g"]', text="G")
            col.prop(mat, '["igb_color_b"]', text="B")
            col.prop(mat, '["igb_color_a"]', text="A")
        else:
            row.label(text="Not set")

        # --- Lighting ---
        box = layout.box()
        row = box.row()
        row.label(text="Lighting", icon='LIGHT')
        lighting = mat.get("igb_lighting_enabled")
        if lighting is not None:
            row.label(text="ON" if lighting else "OFF")
            box.prop(mat, '["igb_lighting_enabled"]', text="Enabled")
        else:
            row.label(text="Not set (default)")

        # --- UV Animation ---
        box = layout.box()
        row = box.row()
        row.label(text="UV Animation", icon='UV')
        tex_mat = mat.get("igb_tex_matrix_enabled")
        if tex_mat is not None:
            row.label(text="ON" if tex_mat else "OFF")
            box.prop(mat, '["igb_tex_matrix_enabled"]', text="Enabled")
            if tex_mat:
                box.prop(mat, '["igb_tex_matrix_unit_id"]', text="Unit ID")
        else:
            row.label(text="Not set")

        # --- Backface Culling ---
        box = layout.box()
        row = box.row()
        row.label(text="Backface Culling", icon='NORMALS_FACE')
        cull_en = mat.get("igb_cull_face_enabled")
        if cull_en is not None:
            row.label(text="ON" if cull_en else "OFF")
            box.prop(mat, '["igb_cull_face_enabled"]', text="Enabled")
            if cull_en:
                mode = mat.get("igb_cull_face_mode", 0)
                mode_names = {0: "FRONT", 1: "BACK", 2: "FRONT_AND_BACK"}
                box.label(text=f"Mode: {mode_names.get(mode, str(mode))}")
                box.prop(mat, '["igb_cull_face_mode"]', text="Cull Face")
        else:
            row.label(text="Not set (double-sided)")


# ===========================================================================
# Collision material helper
# ===========================================================================

def _get_collision_material():
    """Get or create the shared semi-transparent collision material."""
    mat_name = "IGB_Collision"
    if mat_name in bpy.data.materials:
        return bpy.data.materials[mat_name]

    mat = bpy.data.materials.new(name=mat_name)
    mat.use_nodes = True

    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs['Base Color'].default_value = (0.0, 0.8, 0.4, 1.0)
        bsdf.inputs['Alpha'].default_value = 0.3
        bsdf.inputs['Roughness'].default_value = 0.8

    # Blender 4.2+ (EEVEE Next) replaced blend_method/shadow_method
    if hasattr(mat, 'surface_render_method'):
        mat.surface_render_method = 'BLENDED'
    elif hasattr(mat, 'blend_method'):
        mat.blend_method = 'BLEND'
    if hasattr(mat, 'shadow_method'):
        mat.shadow_method = 'NONE'
    mat.use_backface_culling = False
    mat.diffuse_color = (0.0, 0.8, 0.4, 0.3)

    return mat


# ===========================================================================
# Registration
# ===========================================================================

classes = (
    # Operators
    IGB_OT_create_colliders_collection,
    IGB_OT_add_box_collider,
    IGB_OT_generate_box_colliders,
    IGB_OT_generate_hull_colliders,
    IGB_OT_generate_decimated_colliders,
    IGB_OT_merge_colliders,
    IGB_OT_convert_materials,
    IGB_OT_resize_textures,
    IGB_OT_set_all_lighting,
    IGB_OT_set_all_culling,
    IGB_OT_set_all_uv_anim,
    IGB_OT_set_all_alpha,
    IGB_OT_set_all_blend,
    IGB_OT_set_all_color,
    IGB_OT_set_all_diffuse,
    IGB_OT_set_all_ambient,
    IGB_OT_set_all_specular,
    IGB_OT_set_all_emission,
    IGB_OT_set_all_shininess,
    IGB_OT_set_all_shader,
    IGB_OT_merge_duplicate_materials,
    IGB_OT_set_shininess,
    IGB_OT_add_material_node,
    IGB_OT_update_addon,
    # IGB Tab panels (parent before children)
    IGB_PT_Main,
    IGB_PT_ImportExport,
    IGB_OT_log_copy,
    IGB_OT_log_clear,
    IGB_PT_Log,
    IGB_PT_Collision,
    IGB_PT_Materials,
    IGB_PT_QuickTools,
    IGB_PT_QT_Lighting,
    IGB_PT_QT_Culling,
    IGB_PT_QT_UVAnim,
    IGB_PT_QT_Alpha,
    IGB_PT_QT_Blend,
    IGB_PT_QT_Color,
    IGB_PT_QT_MaterialColors,
    IGB_PT_QT_Shader,
    IGB_PT_Extras,
    IGB_PT_Credits,
    # Properties panel (unchanged)
    IGB_PT_MaterialState,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.igb_qt_selected_only = BoolProperty(
        name="Selected Only",
        description="Quick Tools only affect materials on selected objects",
        default=False,
    )


def unregister():
    del bpy.types.Scene.igb_qt_selected_only

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
