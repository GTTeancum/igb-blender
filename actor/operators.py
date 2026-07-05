"""Blender operators for the IGB Actors panel."""

import os

import bpy
from bpy.types import Operator
from bpy.props import StringProperty, IntProperty, EnumProperty, BoolProperty, FloatProperty
from bpy_extras.io_utils import ImportHelper, ExportHelper


def _game_dir_from_actor_path(actor_filepath):
    """Infer the game root from an actor .igb path.

    Actors live in ``<game root>/actors/<file>.igb``; the game root holds the
    ``actors/`` and ``data/`` folders used to resolve herostat/skin files.
    Returns the inferred root, or "" if the path isn't under an actors/ folder.
    """
    parent = os.path.dirname(actor_filepath)
    if os.path.basename(parent).lower() == 'actors':
        root = os.path.dirname(parent)
        if os.path.isdir(root):
            return root
    return ""


def _build_anim_ref_armature(context, anim_filepath):
    """Build a throwaway reference armature for a separate anim file.

    Reuses import_actor EXACTLY as the actor would be built FROM the anim file
    (same skin discovery + inverse-joint source), so the reference's bone rest
    frames match what the animation was authored against — i.e. the upright
    "ground truth" pose. pick_animation uses it to rebase a separate-file anim
    into the actor's parent frame (sideways fix). Geometry/materials are skipped
    (armature only). The caller MUST remove it via _remove_anim_ref_armature.
    """
    from .actor_import import import_actor

    game_dir = _game_dir_from_actor_path(anim_filepath)
    skins = _discover_skin_files(anim_filepath, game_dir)
    res = import_actor(context, anim_filepath,
                       skin_filepaths=skins or [],
                       game_dir=game_dir or None,
                       options={'import_skins': False,
                                'import_animations': False,
                                'import_materials': False,
                                'game_preset': 'auto'})
    if not res or res[0] is None:
        raise ValueError("anim reference import failed")
    return res[0]


def _rest_local_mismatch(target_arm, ref_arm, tol_deg=3.0, frac=0.25,
                         hi_deg=10.0):
    """True when target_arm's bone rests don't match the anim skeleton's.

    Game-imported actors match the anim file's skeleton (same rig), but a
    Rig Converter output — or a cross-game import (an XML1-PS2 skin driven
    by XML2-PC anims) — keeps its own bone rest orientations under Bip01
    names. Applying game anims through those rests directly produces the
    "crooked lean". Detection compares parent-local rest rotations of bones
    whose PARENT name also matches (roots are excluded: a skin-built game
    actor legitimately differs from the anim skeleton at the root, e.g. a
    +90X "Unnamed bone" above Bip01 — handled by the ref-armature rebase).

    Fires on EITHER signal:
      - a broad difference (>frac of comparable bones off by >tol_deg) —
        catches a Rig Converter's uniform roll skew; OR
      - a concentrated high-impact difference (any bone that PARENTS others
        off by >hi_deg) — catches the XML1-PS2 Pelvis (~93deg vs XML2-PC),
        which is only 1-2 bones but tilts the whole body hanging off it.
    """
    import math as _math

    tgt_bones = target_arm.data.bones
    ref_bones = ref_arm.data.bones

    def _local_q(b):
        if b.parent:
            return (b.parent.matrix_local.inverted()
                    @ b.matrix_local).to_quaternion()
        return b.matrix_local.to_quaternion()

    compared = 0
    bad = 0
    hi_influential = False
    for rb in ref_bones:
        tb = tgt_bones.get(rb.name)
        if tb is None or rb.parent is None or tb.parent is None:
            continue
        if rb.parent.name != tb.parent.name:
            continue
        d = abs(_local_q(rb).dot(_local_q(tb)))
        ang = _math.degrees(2.0 * _math.acos(min(1.0, d)))
        compared += 1
        if ang > tol_deg:
            bad += 1
        # A large rest delta on a bone that parents a subtree propagates the
        # error to every descendant — a handful of such bones wreck the pose
        # even when the overall off-fraction stays low.
        if ang > hi_deg and len(tb.children) > 0:
            hi_influential = True

    if compared < 8:
        return False  # not enough shared structure to judge — stay native
    return hi_influential or (bad / compared) > frac


def _import_anim_via_retarget(context, ref_arm, target_arm, pa, bind_pose):
    """Import a game anim onto a FOREIGN rig (Rig Converter output).

    Applies the anim natively to the reference armature (whose rests match
    the anim file exactly), then world-delta retargets + bakes onto the
    target rig by matching bone names. Returns the baked Action or None.
    """
    import math as _math
    from .animation_builder import build_action
    from .vr_mocap import retarget_and_bake

    src_action = build_action(ref_arm, pa, bind_pose=bind_pose,
                              rebase_unanimated_parents=True)
    if src_action is None:
        return None

    if ref_arm.animation_data is None:
        ref_arm.animation_data_create()
    prev_action = ref_arm.animation_data.action
    ref_arm.animation_data.action = src_action
    try:
        f0, f1 = src_action.frame_range
        bone_map = {b.name: b.name for b in ref_arm.data.bones
                    if b.name in target_arm.data.bones}
        action, n_mapped, _n_frames = retarget_and_bake(
            ref_arm, target_arm,
            int(f0), int(_math.ceil(f1)),
            action_name=pa.name, root_motion=True, bone_map=bone_map,
            follow_translation=True, match_absolute=True)
    finally:
        ref_arm.animation_data.action = prev_action
        try:
            bpy.data.actions.remove(src_action)
        except Exception:
            pass

    if action is not None:
        action["igb_anim_name"] = pa.name
        action["igb_duration_ms"] = pa.duration_ms
        action["igb_track_count"] = len(pa.tracks)
    return action


def _remove_anim_ref_armature(arm):
    """Remove a temp anim-ref armature (object + data + its now-empty collection)."""
    try:
        colls = list(arm.users_collection)
        data = arm.data
        bpy.data.objects.remove(arm, do_unlink=True)
        if data is not None and data.users == 0:
            bpy.data.armatures.remove(data)
        # import_actor links the armature into a collection named after the
        # anim file; drop it once empty so the reference leaves no trace.
        scene_coll = bpy.context.scene.collection
        for c in colls:
            if c is not scene_coll and len(c.objects) == 0 \
                    and len(c.children) == 0:
                try:
                    bpy.data.collections.remove(c)
                except Exception:
                    pass
    except Exception:
        pass


def _discover_skin_files(anim_filepath, game_dir=""):
    """Discover skin files for an actor animation file.

    Returns list of (variant_name, filepath) tuples.
    """
    from .actor_import import resolve_skin_files

    skin_filepaths = []

    # Try herostat-based resolution first
    if game_dir:
        anim_name = os.path.splitext(os.path.basename(anim_filepath))[0]
        skin_filepaths = resolve_skin_files(anim_name, game_dir)

    if not skin_filepaths:
        # Scan same directory for matching skin files
        actors_dir = os.path.dirname(anim_filepath)
        anim_name = os.path.splitext(os.path.basename(anim_filepath))[0]
        parts = anim_name.split('_', 1)
        if parts[0].isdigit():
            char_id = parts[0]
            if os.path.isdir(actors_dir):
                for f in sorted(os.listdir(actors_dir)):
                    if f.lower().endswith('.igb') and f[:len(char_id)] == char_id:
                        stem = f[:-4]
                        if stem.isdigit() and len(stem) == 4:
                            variant = stem[len(char_id):]
                            path = os.path.join(actors_dir, f)
                            name = f"skin_{variant}" if variant != "01" else "default"
                            skin_filepaths.append((name, path))

    if not skin_filepaths:
        # Monolithic files (v4 from 3ds Max 5 / Alchemy 2.5) contain skeleton,
        # animation, AND mesh in a single IGB.  Use the anim file itself as skin.
        try:
            from ..igb_format.igb_reader import IGBReader
            reader = IGBReader(anim_filepath)
            reader.read()
            if reader.get_objects_by_type(b"igSkin"):
                skin_filepaths.append(("default", anim_filepath))
        except Exception:
            pass

    return skin_filepaths


class ACTOR_OT_import_actor(Operator, ImportHelper):
    """Import an actor with skeleton, skins, and animations"""
    bl_idname = "actor.import_actor"
    bl_label = "Import Actor"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".igb"

    filter_glob: StringProperty(
        default="*.igb",
        options={'HIDDEN'},
    )

    def execute(self, context):
        from .actor_import import import_actor, resolve_skin_files

        props = context.scene.igb_actor
        anim_filepath = self.filepath

        # Auto-detect game from the skin's own metadata (ignores a stale Game
        # dropdown — fixes blue/BGR MUA PC textures) and fill the game dir.
        if props.auto_detect_game:
            game_preset = 'auto'
            detected_dir = _game_dir_from_actor_path(anim_filepath)
            if detected_dir and not props.game_dir:
                props.game_dir = detected_dir
        else:
            game_preset = props.game_preset

        # Try to resolve skin files
        skin_filepaths = _discover_skin_files(anim_filepath, props.game_dir)

        options = {
            'import_skins': props.import_skins,
            'import_animations': props.import_animations,
            'import_materials': props.import_materials,
            'game_preset': game_preset,
        }

        # If skins found and import_skins enabled, show selection dialog
        if skin_filepaths and props.import_skins:
            _skin_import_cache['anim_filepath'] = anim_filepath
            _skin_import_cache['skins'] = skin_filepaths
            _skin_import_cache['options'] = options
            bpy.ops.actor.select_skins('INVOKE_DEFAULT')
            return {'FINISHED'}

        # No skins or skins disabled — import directly
        result = import_actor(
            context, anim_filepath,
            skin_filepaths=skin_filepaths if props.import_skins else [],
            game_dir=props.game_dir if props.game_dir else None,
            operator=self,
            options=options,
        )

        if result is None:
            return {'CANCELLED'}

        armature_obj, skin_objects, actions = result

        # Ensure all imported skin meshes have IGB material properties
        # so the IGB Materials panel can always edit render state.
        for _, mesh_obj in skin_objects:
            _ensure_igb_material_properties(mesh_obj)

        _populate_import_results(context, props, armature_obj,
                                 skin_objects, actions)
        return {'FINISHED'}

    def invoke(self, context, event):
        # Pre-fill with game_dir actors folder if set
        props = context.scene.igb_actor
        if props.game_dir:
            actors_dir = os.path.join(props.game_dir, "actors")
            if os.path.isdir(actors_dir):
                self.filepath = actors_dir + os.sep
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class ACTOR_OT_import_skin(Operator, ImportHelper):
    """Import an additional skin file for the current actor"""
    bl_idname = "actor.import_skin"
    bl_label = "Import Skin"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".igb"

    filter_glob: StringProperty(
        default="*.igb",
        options={'HIDDEN'},
    )

    variant_name: StringProperty(
        name="Variant Name",
        default="custom",
    )

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return props.active_armature and props.active_armature in bpy.data.objects

    def execute(self, context):
        from .actor_import import _import_skin_file
        from .sg_skeleton import extract_skeleton
        from ..igb_format.igb_reader import IGBReader
        from ..game_profiles import detect_profile, get_profile

        props = context.scene.igb_actor
        armature_obj = bpy.data.objects.get(props.active_armature)
        if armature_obj is None or armature_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "No active armature found")
            return {'CANCELLED'}

        # Re-extract skeleton from the animation file
        anim_file = props.anim_file
        if not anim_file or not os.path.exists(anim_file):
            self.report({'ERROR'}, "Animation file not found")
            return {'CANCELLED'}

        reader = IGBReader(anim_file)
        reader.read()
        skeleton = extract_skeleton(reader)
        if skeleton is None:
            self.report({'ERROR'}, "Failed to extract skeleton")
            return {'CANCELLED'}

        game_preset = props.game_preset
        if game_preset == 'auto':
            profile = detect_profile(reader)
        else:
            profile = get_profile(game_preset)

        # Find the actor's collection
        collection = None
        for coll in bpy.data.collections:
            if armature_obj.name in coll.objects:
                collection = coll
                break
        if collection is None:
            collection = context.collection

        options = {
            'import_normals': True,
            'import_uvs': True,
            'import_vertex_colors': True,
            'import_materials': props.import_materials,
        }

        mesh_objs = _import_skin_file(
            context, self.filepath, skeleton, armature_obj,
            collection, self.variant_name, profile, options
        )

        if mesh_objs:
            for mesh_obj in mesh_objs:
                # Ensure IGB material properties exist for the panel
                _ensure_igb_material_properties(mesh_obj)

                item = props.skins.add()
                item.name = self.variant_name
                item.object_name = mesh_obj.name
                item.filepath = self.filepath
                item.is_visible = True
            self.report({'INFO'}, f"Imported skin '{self.variant_name}' ({len(mesh_objs)} parts)")
            return {'FINISHED'}

        self.report({'WARNING'}, "No geometry found in skin file")
        return {'CANCELLED'}


class ACTOR_OT_toggle_skin(Operator):
    """Toggle visibility of a skin variant"""
    bl_idname = "actor.toggle_skin"
    bl_label = "Toggle Skin"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty(default=0)

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return len(props.skins) > 0

    def execute(self, context):
        props = context.scene.igb_actor
        if self.index >= len(props.skins):
            return {'CANCELLED'}

        item = props.skins[self.index]
        obj = bpy.data.objects.get(item.object_name)
        if obj:
            obj.hide_viewport = not obj.hide_viewport
            obj.hide_render = obj.hide_viewport
            item.is_visible = not obj.hide_viewport

        return {'FINISHED'}


class ACTOR_OT_solo_skin(Operator):
    """Show only the selected skin, hide all others"""
    bl_idname = "actor.solo_skin"
    bl_label = "Solo Skin"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty(default=0)

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return len(props.skins) > 0

    def execute(self, context):
        props = context.scene.igb_actor
        for i, item in enumerate(props.skins):
            obj = bpy.data.objects.get(item.object_name)
            if obj:
                visible = (i == self.index)
                obj.hide_viewport = not visible
                obj.hide_render = not visible
                item.is_visible = visible

        return {'FINISHED'}


class ACTOR_OT_set_animation(Operator):
    """Set the selected animation as active on the armature"""
    bl_idname = "actor.set_animation"
    bl_label = "Set Animation"

    index: IntProperty(default=0)

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (props.active_armature and
                props.active_armature in bpy.data.objects and
                len(props.animations) > 0)

    def execute(self, context):
        props = context.scene.igb_actor
        if self.index >= len(props.animations):
            return {'CANCELLED'}

        item = props.animations[self.index]
        action = bpy.data.actions.get(item.action_name)
        armature_obj = bpy.data.objects.get(props.active_armature)

        if action and armature_obj:
            from .animation_builder import set_active_action
            set_active_action(armature_obj, action)
            props.animations_index = self.index

            # Set frame range
            fps = context.scene.render.fps
            context.scene.frame_start = 0
            context.scene.frame_end = max(1, int(item.duration_ms / 1000.0 * fps))

        return {'FINISHED'}


class ACTOR_OT_rest_pose(Operator):
    """Clear animation and show the armature in its rest pose (T-pose)"""
    bl_idname = "actor.rest_pose"
    bl_label = "Rest Pose"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (props.active_armature and
                props.active_armature in bpy.data.objects)

    def execute(self, context):
        props = context.scene.igb_actor
        armature_obj = bpy.data.objects.get(props.active_armature)
        if armature_obj and armature_obj.animation_data:
            armature_obj.animation_data.action = None
        return {'FINISHED'}


class ACTOR_OT_play_animation(Operator):
    """Play/stop the current animation in the viewport"""
    bl_idname = "actor.play_animation"
    bl_label = "Play Animation"

    def execute(self, context):
        if context.screen.is_animation_playing:
            bpy.ops.screen.animation_cancel()
        else:
            bpy.ops.screen.animation_play()
        return {'FINISHED'}


class ACTOR_OT_ground_preview(Operator):
    """Ground the animation preview: mute the root bones' location curves so
    the character doesn't float/slide in the viewport. Preview-only — the
    game applies root motion itself, and export still reads the full curves
    (muted F-Curves evaluate normally for export sampling)"""
    bl_idname = "actor.ground_preview"
    bl_label = "Ground Preview"
    bl_options = {'REGISTER', 'UNDO'}

    # Root-level bones whose location the engine owns at runtime
    _ROOT_BONES = ("Bip01", "Motion")

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        arm = bpy.data.objects.get(props.active_armature)
        return (arm is not None and arm.animation_data
                and arm.animation_data.action)

    def execute(self, context):
        props = context.scene.igb_actor
        arm = bpy.data.objects.get(props.active_armature)
        action = arm.animation_data.action

        targets = []
        for fc in action.fcurves:
            if not fc.data_path.endswith(".location"):
                continue
            for rb in self._ROOT_BONES:
                if fc.data_path == f'pose.bones["{rb}"].location':
                    targets.append(fc)
                    break

        if not targets:
            self.report({'INFO'}, "No root location curves in this action")
            return {'CANCELLED'}

        # Toggle: if any are unmuted, mute all; else unmute all
        mute = any(not fc.mute for fc in targets)
        for fc in targets:
            fc.mute = mute
        # Force re-evaluation of the pose
        arm.update_tag()
        context.scene.frame_set(context.scene.frame_current)

        state = "grounded (root motion hidden)" if mute else "full root motion"
        self.report({'INFO'}, f"Preview: {state}")
        return {'FINISHED'}


class ACTOR_OT_import_animations(Operator, ImportHelper):
    """Import animations from an IGB file (select which ones)"""
    bl_idname = "actor.import_animations"
    bl_label = "Import Animations"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".igb"

    filter_glob: StringProperty(
        default="*.igb",
        options={'HIDDEN'},
    )

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (props.active_armature and
                props.active_armature in bpy.data.objects)

    def execute(self, context):
        """Read the IGB file, cache animations, then open the multi-select dialog."""
        from .sg_skeleton import extract_skeleton
        from .sg_animation import extract_animations
        from .actor_import import _extract_bind_pose
        from ..igb_format.igb_reader import IGBReader

        reader = IGBReader(self.filepath)
        reader.read()

        skeleton = extract_skeleton(reader)
        if skeleton is None:
            self.report({'ERROR'}, "No skeleton found in animation file")
            return {'CANCELLED'}

        parsed_anims = extract_animations(reader, skeleton)
        if not parsed_anims:
            self.report({'WARNING'}, "No animations found in file")
            return {'CANCELLED'}

        bind_pose = _extract_bind_pose(parsed_anims)

        # v8 (MUA next-gen) afakeanim binds are UNTRUSTWORTHY: usually
        # degenerate zero-quats (02_daredevil, 33/36), and when they ARE
        # valid they're authored against a different rig state — applying
        # 21_nickfury's menu anims through its "valid" bind tilts the whole
        # body back ~45° (#45), while the absolute + parent-rest-rebase path
        # renders the correct upright pose. Drop the bind entirely for v8;
        # v6 (XML2/XML1) binds are proven-correct and keep working.
        try:
            if reader.header.version >= 8 and bind_pose:
                bind_pose = None
        except Exception:
            pass

        # Populate the cache for the multi-select dialog
        _anim_import_cache['parsed_anims'] = parsed_anims
        _anim_import_cache['bind_pose'] = bind_pose
        _anim_import_cache['anim_filepath'] = self.filepath
        _anim_import_cache['anim_names'] = [
            (pa.name, pa.name, f"{pa.duration_ms / 1000.0:.2f}s, {len(pa.tracks)} tracks")
            for pa in parsed_anims
        ]

        # Open the multi-select pick dialog
        bpy.ops.actor.pick_animation('INVOKE_DEFAULT')
        return {'FINISHED'}


def _skin_export_texture_items(self, context):
    """Enum items for skin export texture format."""
    return [
        ('clut', "CLUT (Universal)",
         "256-color palette texture. Works in both XML2 and MUA (recommended)"),
        ('dxt5_xml2', "DXT5 (XML2 Only)",
         "DXT5 compressed for X-Men Legends 2 (standard RGB565)"),
        ('dxt5_mua', "DXT5 (MUA Only)",
         "DXT5 compressed for Marvel Ultimate Alliance (BGR565)"),
    ]


def _skin_export_resolution_items(self, context):
    """Shared texture-resolution cap (longest edge). See exporter.texture_size."""
    from ..exporter.texture_size import texture_size_items
    return texture_size_items(self, context)


class ACTOR_OT_export_skin(Operator, ExportHelper):
    """Export the active skin to an IGB file (from-scratch builder, no template)"""
    bl_idname = "actor.export_skin"
    bl_label = "Export Skin"
    bl_options = {'REGISTER'}

    filename_ext = ".igb"

    filter_glob: StringProperty(
        default="*.igb",
        options={'HIDDEN'},
    )

    texture_format: EnumProperty(
        name="Texture Format",
        description="Texture encoding format",
        items=_skin_export_texture_items,
    )

    texture_resolution: EnumProperty(
        name="Texture Resolution",
        description="Optional cap on texture size (longest edge). "
                    "Use lower values to shrink IGB file size",
        items=_skin_export_resolution_items,
    )

    # The ONLY format choice the user sees: pick the game, we apply its correct,
    # in-game-proven format + texture encoding automatically.
    game_target: EnumProperty(
        name="Game",
        description="Which game this skin is for. The exporter picks the "
                    "correct, in-game-proven format and texture encoding "
                    "automatically — no other choices needed",
        items=[
            ('MUA', "Marvel Ultimate Alliance",
             "3ds Max v4 skin + DXT5 (MUA BGR) textures, with normal + "
             "specular maps exported automatically. The format MUA loads"),
            ('XML2', "X-Men Legends 2",
             "Native v6 skin + CLUT textures. The format XML2 loads"),
        ],
        default='MUA',
    )

    platform: EnumProperty(
        name="Platform",
        description="Target platform. PC uses each game's native PC format; "
                    "PS2 / GameCube force the v6 console format these games "
                    "use (CLUT on PS2, GX-tiled CMPR on GameCube)",
        items=[
            ('PC', "PC", "Native PC format for the selected game"),
            ('PS2', "PS2 (XML1/XML2/MUA1)",
             "v6 skin + CLUT (PSMT8) textures — the format these games load on "
             "PlayStation 2. Use this even for MUA1: PS2 MUA1 is v6, not the "
             "PC v4 + DXT5"),
        ('GC', "GameCube (XML1/XML2)",
             "v6 skin + CMPR (GX-tiled DXT1) textures — the format X-Men "
             "Legends 1 & 2 load on GameCube. (MUA on Nintendo is Wii/v8, not "
             "covered by this preset)"),
        ],
        default='PC',
    )

    # --- internal format knobs (kept for the builder code; not shown in the UI;
    #     execute() sets the right combination from game_target) ---
    actor_graph: EnumProperty(
        name="Skin Format",
        options={'HIDDEN'},
        description="IGB layout + how much of the 3ds Max-style actor graph "
                    "to emit. The v4 Max-style formats mirror community "
                    "skins; 'v4 + Combiner' carries the engine-side "
                    "retargeting that grounds RESIZED characters "
                    "everywhere incl. the character select menu",
        items=[
            ('OFF', "XML2 Native v6 (Stable)",
             "Bare native-style v6 skin — proven in-game"),
            ('ANIM', "v6 + Bind-Pose Anim",
             "Native v6 + embedded bind-pose animation (loads in-game)"),
            ('FULL', "v6 + Full Combiner (crashes)",
             "Complete combiner graph in v6 — crashed XML2, kept for "
             "bisection only"),
            ('V4_ANIM', "3ds Max v4 (0103 mirror)",
             "v4 Max-era layout mirroring a known-compatible community "
             "skin: CLUT texture, tri-list geometry, bind-pose animation"),
            ('V4_FULL', "3ds Max v4 + Combiner (MUA ONLY)",
             "v4 layout + MUA-era combiner graph. CRASHES XML2 — the XML2 "
             "engine cannot deserialize combiner classes (no XML2 skin, "
             "vanilla or modded, carries them). MUA-era Max skins all do"),
            ('V8_NATIVE', "v8 (MUA Native)",
             "Alchemy 5.0 native format: igGeometryAttr2 separate-list "
             "geometry. The format MUA itself ships — carries normal/spec/"
             "gloss maps + mipmaps. MUA ONLY (XML2 cannot read v8)"),
        ],
        default='OFF',
    )

    use_normal_maps: BoolProperty(
        name="Normal Maps",
        description="Export each material's Normal map (MUA only). Also emits "
                    "the tangent-frame vertex data the bump shader needs. Untick "
                    "to ship the skin with no normal maps",
        default=True,
    )

    use_specular_maps: BoolProperty(
        name="Specular Maps",
        description="Export each material's Specular map (MUA only). Untick to "
                    "drop specular entirely — the quickest way to kill an "
                    "over-shiny look in advanced lighting",
        default=True,
    )

    use_gloss_map: BoolProperty(
        name="Gloss / Mask Maps",
        description="Export each material's Gloss/Mask map on texture unit 5 "
                    "(MUA). Used by the shader for specular masking / "
                    "glossiness. Untick to omit it",
        default=True,
    )

    use_mipmaps: BoolProperty(
        name="Generate Mipmaps",
        description="Build the full mipmap chain for every texture (smoother "
                    "at distance, matches native MUA skins). Turn off to ship "
                    "only the base level for a smaller file",
        default=True,
    )

    normal_map_size: EnumProperty(
        name="Normal Map Size",
        description="Resize + recompress the normal/specular maps to keep the "
                    "IGB small (the diffuse uses Texture Resolution above)",
        items=[
            ('0', "Keep Same Size", "Use each normal/specular map's source size"),
            ('512', "512 x 512", "Cap normal/specular maps at 512px"),
            ('256', "256 x 256", "Cap normal/specular maps at 256px"),
        ],
        default='0',
    )

    normal_y_flip: BoolProperty(
        name="Flip Normal Green (Y)",
        description="Convert Blender's OpenGL (Y-up) normal green back to MUA's "
                    "DirectX (Y-down). Leave this ON (default) — it matches "
                    "native MUA normal maps and the addon's importer, which "
                    "flips green for Blender's viewport. Turn OFF only if your "
                    "map is already DirectX in Blender and looks like dents",
        default=True,
    )

    mannequin_mode: BoolProperty(
        name="MUA Animated Mannequin",
        description="Export a v6 skinned actor for the MUA character-select "
                    "screen. The mannequin is NOT animated by the file — the "
                    "MUA menu plays an idle on any skinned actor it finds at "
                    "ui/models/mannequin/<id>.igb, keyed by the character slot "
                    "<id>. (Verified: the stock animated mannequins ship an "
                    "EMPTY animation list; nothing is embedded.) Forces v6 "
                    "Native + no combiner so it loads cleanly",
        default=False,
        options={'HIDDEN'},
    )

    def _is_v8(self):
        return self.actor_graph == 'V8_NATIVE'

    def _normal_maps_available(self):
        """Normal maps need the MUA DXT5 + (3ds Max v4 OR v8 native) path."""
        return ((self.actor_graph.startswith('V4') or self._is_v8())
                and self.texture_format == 'dxt5_mua')

    def draw(self, context):
        layout = self.layout
        if self.mannequin_mode:
            # Placement guidance is the only thing that matters for a mannequin.
            box = layout.box()
            box.label(text="MUA Animated Mannequin", icon='ARMATURE_DATA')
            box.label(text="Save as <id>.igb (id = a character slot)",
                      icon='FILE_TICK')
            box.label(text="into  ui/models/mannequin/")
            box.label(text="The MUA menu supplies the idle automatically.",
                      icon='INFO')
            return
        # Choices: the game, the platform, and an optional texture-size cap. The
        # exporter applies the correct format/textures/maps automatically.
        layout.prop(self, "game_target")
        layout.prop(self, "platform")
        layout.prop(self, "texture_resolution")
        # Show the format consequence so the choice can't silently drop normal
        # maps. PS2 = v6 + CLUT for all 3 games; PC = each game's native format.
        fmt = layout.box().column(align=True)
        if self.platform == 'PS2':
            fmt.label(text="PS2: v6 + CLUT (PSMT8) textures", icon='SCENE')
            fmt.label(text="XML1 / XML2 / MUA1 all load this on PS2.",
                      icon='INFO')
            fmt.label(text="No normal/specular maps on PS2.", icon='INFO')
        elif self.platform == 'GC':
            fmt.label(text="GameCube: v6 + CMPR (GX-tiled) textures",
                      icon='SCENE')
            fmt.label(text="X-Men Legends 1 & 2 load this on GameCube.",
                      icon='INFO')
            fmt.label(text="MUA on Nintendo is Wii/v8 (not this preset).",
                      icon='INFO')
        elif self.game_target == 'MUA':
            fmt.label(text="MUA (PC): v4 + DXT5 textures", icon='CHECKMARK')
            mp = layout.box().column(align=True)
            mp.label(text="Export Maps:", icon='TEXTURE')
            mp.prop(self, "use_normal_maps")
            mp.prop(self, "use_specular_maps")
            mp.prop(self, "use_gloss_map")
            if self.use_normal_maps:
                layout.prop(self, "normal_y_flip")
        else:
            fmt.label(text="XML2 (PC): v6 + CLUT textures", icon='SCENE')
            fmt.label(text="No normal/specular maps (XML2 has none).",
                      icon='INFO')
            fmt.label(text="For normal maps, pick Marvel Ultimate Alliance.",
                      icon='ERROR')

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        if not (len(props.skins) > 0 and
                props.skins_index < len(props.skins)):
            return False
        # Check that the skin mesh object exists
        item = props.skins[props.skins_index]
        return item.object_name in bpy.data.objects

    def execute(self, context):
        from ..exporter.skin_export import export_skin

        props = context.scene.igb_actor
        item = props.skins[props.skins_index]

        # Get the main mesh object
        mesh_obj = bpy.data.objects.get(item.object_name)
        if mesh_obj is None or mesh_obj.type != 'MESH':
            self.report({'ERROR'}, f"Skin mesh not found: {item.object_name}")
            return {'CANCELLED'}

        # Get the armature
        armature_obj = bpy.data.objects.get(props.active_armature)
        if armature_obj is None or armature_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "No active armature found")
            return {'CANCELLED'}

        # Check that skeleton data is stored on the armature
        if "igb_skin_bone_translations" not in armature_obj:
            self.report({'ERROR'},
                        "No skeleton data found on armature. "
                        "Re-import the actor with a skin file to store "
                        "skeleton data for from-scratch export.")
            return {'CANCELLED'}

        # Collect all mesh objects for this skin using collect_skin_segments
        # This only uses meshes registered in the skins list (no armature
        # children scan — export only what's explicitly in the panel).
        from .actor_import import collect_skin_segments
        segments = collect_skin_segments(props, props.skins_index)
        mesh_objs = segments if segments else [
            (mesh_obj, bool(mesh_obj.get("igb_is_outline", False)))
        ]

        self.report({'INFO'},
                    f"Exporting {len(mesh_objs)} mesh part(s) "
                    f"({sum(1 for _, o in mesh_objs if not o)} main, "
                    f"{sum(1 for _, o in mesh_objs if o)} outline)")

        # Apply the correct, in-game-proven format + textures for the game.
        if self.game_target == 'MUA':
            igb_format = 'V4'        # 3ds Max v4 — the MUA format that loads
            texture_mode = 'dxt5'
            swap_rb = True           # MUA BGR565
            # Respect the export ticks (default ON): user can drop any of the
            # normal / specular / gloss maps at export time.
            use_nm = self.use_normal_maps
            use_spec = self.use_specular_maps
            use_gloss = self.use_gloss_map
            # Resized / rig-converted characters need the MUA combiner to ground
            # in the character-select menu (no float); native-proportioned skins
            # use the simpler bind-pose form. Auto-detected — no user choice.
            _scale = armature_obj.get("igb_export_scale", 1.0)
            _scaled = (armature_obj.get("igb_converted_rig", False) or
                       (isinstance(_scale, (int, float)) and abs(_scale - 1.0) > 0.01))
            graph_mode = 'FULL' if _scaled else 'ANIM'
        else:  # XML2
            igb_format = 'V6'        # XML2 native
            graph_mode = 'OFF'
            texture_mode = 'clut'    # universal CLUT, proven in XML2
            swap_rb = False
            use_nm = False           # XML2 has no normal maps
            use_spec = False
            use_gloss = False        # XML2 uses a single diffuse texture

        # PS2 (XML1 / XML2 / MUA1) override: native PS2 actors for ALL THREE
        # games are v6 / igGeometryAttr1_5 (float32) / pfmt 65536 CLUT — verified
        # against 0101.igb in each. This forces that format regardless of which
        # game is selected, so a MUA1 skin targets PS2 (v6+CLUT) instead of the
        # PC-MUA v4+DXT5 path. No normal/gloss maps (PS2-classic has none).
        if self.platform == 'PS2':
            igb_format = 'V6'
            graph_mode = 'OFF'
            texture_mode = 'clut'
            swap_rb = False
            use_nm = False
            use_spec = False
            use_gloss = False
        elif self.platform == 'GC':
            # GameCube (XML1/XML2): v6 + CMPR (GX-tiled DXT1, pfmt 34). Same
            # float32 v6 geometry/rig as PS2/PC — only the texture encoder
            # differs. Verified native GC 0101 = v6 / igGeometryAttr1_5 / pfmt 34.
            igb_format = 'V6'
            graph_mode = 'OFF'
            texture_mode = 'cmpr'
            swap_rb = False
            use_nm = False
            use_spec = False
            use_gloss = False

        use_mips = True
        normal_map_size = 0

        try:
            max_texture_size = int(self.texture_resolution)
        except (TypeError, ValueError):
            max_texture_size = 0  # 'original'

        # Mannequin export is a bare v6 skinned actor; the MUA menu animates it
        # at runtime (stock mannequins ship an empty igAnimationList).
        if self.mannequin_mode:
            igb_format = 'V6'
            graph_mode = 'OFF'
            texture_mode = 'dxt5'
            swap_rb = True
            use_nm = False
            use_spec = False
            use_gloss = False
            self.report({'INFO'},
                        "Mannequin: save as <id>.igb in ui/models/mannequin/ "
                        "(id = character slot). The menu plays the idle.")

        success = export_skin(
            filepath=self.filepath,
            mesh_objs=mesh_objs,
            armature_obj=armature_obj,
            operator=self,
            swap_rb=swap_rb,
            texture_mode=texture_mode,
            max_texture_size=max_texture_size,
            actor_graph=graph_mode,
            igb_format=igb_format,
            use_normal_maps=use_nm,
            use_specular_maps=use_spec,
            normal_map_size=normal_map_size,
            use_gloss_map=use_gloss,
            use_mipmaps=use_mips,
            normal_y_flip=self.normal_y_flip,
        )

        return {'FINISHED'} if success else {'CANCELLED'}

    def invoke(self, context, event):
        # Pre-fill filename from skin source path or object name
        props = context.scene.igb_actor
        if props.skins_index < len(props.skins):
            item = props.skins[props.skins_index]
            mesh_obj = bpy.data.objects.get(item.object_name)
            source_path = ""
            if item.filepath and os.path.exists(item.filepath):
                source_path = item.filepath
            if source_path and os.path.exists(source_path):
                dirname = os.path.dirname(source_path)
                basename = os.path.splitext(os.path.basename(source_path))[0]
                if self.mannequin_mode:
                    # The internal igSkin/database name derives from the
                    # filename, and the engine keys the idle on <id>.igb — so
                    # offer a clean <id>.igb (no _export suffix) for the user
                    # to rename to the target character slot.
                    self.filepath = os.path.join(dirname, f"{basename}.igb")
                else:
                    self.filepath = os.path.join(dirname,
                                                 f"{basename}_export.igb")
        # Auto-pick the game from the imported source: MUA-sourced skins default
        # to MUA, XML2-sourced to XML2 (the user can still flip the dropdown).
        if not self.mannequin_mode:
            arm = bpy.data.objects.get(props.active_armature)
            if arm is not None:
                # The user-set target (Materials tab toggle) is authoritative;
                # fall back to the imported source profile only if unset.
                tgt = str(arm.get("igb_target_game", "") or "").upper()
                profile = (arm.get("igb_game_profile", "") or "")
                if tgt == 'MUA':
                    self.game_target = 'MUA'
                elif tgt == 'XML2':
                    self.game_target = 'XML2'
                elif arm.get("igb_source_is_mua_pc") or "Ultimate Alliance" in profile:
                    self.game_target = 'MUA'
                elif "X-Men Legends 2" in profile:
                    self.game_target = 'XML2'
                # PS2-sourced (v6 CLUT) skins default the platform to PS2.
                # Exclude MUA2 PS2 — that's the v8/VIF path this preset can't emit.
                if "(PS2)" in profile and "Alliance 2" not in profile:
                    self.platform = 'PS2'
                elif "(GameCube)" in profile:
                    self.platform = 'GC'
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


def _mannequin_meshes(context):
    """Resolve the mesh objects for a static mannequin export.

    Works with ANY model — not just a set-up actor skin. Priority:
      1. The current selection (the universal 'export this' idiom) — BUT if
         the whole selection belongs to the registered actor skin, export the
         FULL skin (a partial selection would silently drop the other parts of
         a multi-part character).
      2. The registered actor skin, if one is loaded (uses its segment list).
      3. The active object.
    Toon-outline shells (igb_is_outline) are filtered out of every path so a
    statue never carries an inverted black husk.

    Returns (mesh_objs, source_label).
    """
    def _gather(objs):
        return [o for o in objs
                if o is not None and getattr(o, 'type', None) == 'MESH'
                and not o.get('igb_is_outline', False)]

    # Resolve the registered actor skin's mesh parts up front (if any), so a
    # partial selection of a multi-part skin can expand to the whole thing.
    props = context.scene.igb_actor
    skin_meshes = []
    if len(props.skins) > 0 and props.skins_index < len(props.skins):
        item = props.skins[props.skins_index]
        skin_obj = bpy.data.objects.get(item.object_name)
        if skin_obj is not None:
            from .actor_import import collect_skin_segments
            segs = collect_skin_segments(props, props.skins_index)
            skin_meshes = [o for o, is_outline in (segs or [(skin_obj, False)])
                           if not is_outline and o is not None
                           and o.type == 'MESH']

    # 1. explicit selection
    sel = _gather(context.selected_objects)
    if sel:
        # If every selected mesh is part of the registered skin, the user is
        # inspecting the character — export the whole skin, not the subset.
        skin_set = set(skin_meshes)
        if skin_set and all(o in skin_set for o in sel):
            return skin_meshes, 'skin'
        return sel, 'selection'

    # 2. registered actor skin (nothing selected)
    if skin_meshes:
        return skin_meshes, 'skin'

    # 3. active object
    ao = context.active_object
    if (ao is not None and ao.type == 'MESH'
            and not ao.get('igb_is_outline', False)):
        return [ao], 'active'

    return [], None


def _mirror_counterpart(name):
    """Game-rig L/R counterpart: 'Bip01 L Hand' <-> 'Bip01 R Hand'.

    Blender's own flip tooling only understands .L/.R style suffixes, not
    the ' L ' / ' R ' infix every XML/MUA rig uses — so X-mirror posing has
    never worked on these rigs.
    """
    for a, b in ((" L ", " R "), (" R ", " L ")):
        if a in name:
            return name.replace(a, b, 1)
    for a, b in ((" L", " R"), (" R", " L")):
        if name.endswith(a):
            return name[: -len(a)] + b
    return None  # center bone


class ACTOR_OT_mirror_pose(Operator):
    """Mirror the current pose left<->right (game 'Bip01 L/R' bone naming)"""
    bl_idname = "actor.mirror_pose"
    bl_label = "Mirror Pose L↔R"
    bl_options = {'REGISTER', 'UNDO'}

    selected_only: BoolProperty(
        name="Selected Bones Only",
        description="Mirror only the selected bones (and their counterparts)",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return (context.active_object is not None
                and context.active_object.type == 'ARMATURE'
                and context.mode == 'POSE')

    def execute(self, context):
        from mathutils import Matrix
        arm = context.active_object
        M = Matrix.Diagonal((-1.0, 1.0, 1.0, 1.0))

        bones = list(arm.pose.bones)
        if self.selected_only:
            sel = {pb.name for pb in bones if pb.bone.select}
            for n in list(sel):
                cp = _mirror_counterpart(n)
                if cp:
                    sel.add(cp)
            bones = [pb for pb in bones if pb.name in sel]

        # Snapshot armature-space matrices FIRST (assignments change children)
        snap = {pb.name: pb.matrix.copy() for pb in arm.pose.bones}

        # Target matrix per bone: mirrored counterpart's pose (or own, for
        # center bones). W' = M @ W @ M reflects across the X=0 plane while
        # keeping a right-handed frame.
        targets = {}
        for pb in bones:
            src_name = _mirror_counterpart(pb.name) or pb.name
            src = snap.get(src_name)
            if src is None:
                continue
            targets[pb.name] = M @ src @ M

        # Apply parents-first so pose_bone.matrix conversion sees updated
        # parents.
        def depth(pb):
            d, p = 0, pb.parent
            while p is not None:
                d += 1
                p = p.parent
            return d

        n_applied = 0
        for pb in sorted(bones, key=depth):
            t = targets.get(pb.name)
            if t is None:
                continue
            pb.matrix = t
            context.view_layer.update()
            n_applied += 1

        self.report({'INFO'}, f"Mirrored {n_applied} bones")
        return {'FINISHED'}


class ACTOR_OT_export_mannequin(Operator, ExportHelper):
    """Export the selected model as a STATIC mannequin IGB.

    Works with ANY model — a rigged actor OR a plain mesh with no armature.
    Bakes the current transform/pose into the geometry and exports it with no
    armature/skeleton — a statue, keeping the same materials incl. normal/
    specular/gloss maps (MUA). Save as ui/models/mannequin/<id>.igb."""
    bl_idname = "actor.export_mannequin"
    bl_label = "Export Mannequin (Static)"
    bl_options = {'REGISTER'}

    filename_ext = ".igb"

    filter_glob: StringProperty(
        default="*.igb",
        options={'HIDDEN'},
    )

    game_target: EnumProperty(
        name="Game",
        description="Which game this mannequin is for",
        items=[
            ('MUA', "Marvel Ultimate Alliance",
             "DXT5 (MUA BGR) textures with normal/spec/gloss maps"),
            ('XML2', "X-Men Legends 2",
             "DXT5 (XML2) textures, no normal maps"),
        ],
        default='MUA',
    )

    normal_y_flip: BoolProperty(
        name="Flip Normal Green (Y)",
        description="Convert Blender's OpenGL normal green back to MUA's "
                    "DirectX convention (leave ON, matches native maps)",
        default=True,
    )

    texture_resolution: EnumProperty(
        name="Max Texture Size",
        description="Cap every texture (diffuse/normal/spec/gloss) to this "
                    "longest-edge size. Menu mannequins are small on screen, so "
                    "512 keeps files small with no visible loss",
        items=_skin_export_resolution_items,
    )

    # Per-map export toggles (MUA), default ON — untick to drop a map. E.g.
    # untick Specular to kill an over-shiny mannequin.
    use_normal_maps: BoolProperty(name="Normal Maps",
                                  description="Export normal maps (MUA)",
                                  default=True)
    use_specular_maps: BoolProperty(name="Specular Maps",
                                    description="Export specular maps (MUA) — "
                                    "untick to drop specular / reduce shine",
                                    default=True)
    use_gloss_map: BoolProperty(name="Gloss / Mask Maps",
                                description="Export gloss/mask maps (MUA)",
                                default=True)

    # Attributes export_igb reads — a mannequin is pure visual geometry.
    collision_source = 'NONE'
    surface_type = 0
    export_lights = False
    texture_format = 'dxt5_mua'

    @classmethod
    def poll(cls, context):
        # Enable exactly when execute() would find meshes — same resolver, so
        # the button is never enabled for a selection execute() would reject
        # (e.g. outline-only), and never disabled when there IS something.
        return bool(_mannequin_meshes(context)[0])

    def draw(self, context):
        layout = self.layout
        meshes, source = _mannequin_meshes(context)
        box = layout.box()
        box.label(text="Static Mannequin", icon='OUTLINER_OB_MESH')
        if meshes:
            box.label(text=f"Exporting {len(meshes)} mesh(es) "
                           f"(from {source}).", icon='CHECKMARK')
        else:
            box.label(text="Select a mesh to export.", icon='ERROR')
        box.label(text="Current transform/pose bakes in.", icon='POSE_HLT')
        box.label(text="No armature is exported (statue).", icon='INFO')
        layout.prop(self, "game_target")
        layout.prop(self, "texture_resolution")
        if self.game_target == 'MUA':
            mp = layout.box().column(align=True)
            mp.label(text="Export Maps:", icon='TEXTURE')
            mp.prop(self, "use_normal_maps")
            mp.prop(self, "use_specular_maps")
            mp.prop(self, "use_gloss_map")
            if self.use_normal_maps:
                layout.prop(self, "normal_y_flip")
        box = layout.box()
        box.label(text="Save as <id>.igb (id = a character slot)",
                  icon='FILE_TICK')
        box.label(text="into  ui/models/mannequin/")

    def execute(self, context):
        from ..exporter.export_igb import export_igb

        mesh_objs, source = _mannequin_meshes(context)
        if not mesh_objs:
            self.report({'ERROR'},
                        "Select a mesh (or import an actor skin) to export")
            return {'CANCELLED'}

        # Game consequence (mirrors Export IGB): the map path reads these. On
        # MUA the export ticks (use_normal/specular/gloss) carry the user's
        # choice; XML2 has no role maps.
        if self.game_target == 'MUA':
            self.texture_format = 'dxt5_mua'
        else:
            self.texture_format = 'dxt5_xml2'
            self.use_normal_maps = False
            self.use_specular_maps = False
            self.use_gloss_map = False

        self.report({'INFO'},
                    f"Baking transform/pose into {len(mesh_objs)} mesh(es) "
                    f"from {source} (static mannequin, no armature)")
        # bake_transforms=True bakes each object's world matrix into the
        # geometry so a plain FBX prop (with a +90X object rotation, scale,
        # or offset) exports correctly placed — not just origin-identity rigs.
        result = export_igb(context, self.filepath, self, objects=mesh_objs,
                            bake_transforms=True)
        if result == {'FINISHED'}:
            self.report({'INFO'},
                        "Mannequin: save as <id>.igb in ui/models/mannequin/ "
                        "(id = character slot)")
        return result

    def invoke(self, context, event):
        # Prefill the filename to match what will ACTUALLY be exported: the
        # skin's <id>.igb only when the resolved source is the skin; otherwise
        # the exported object's own name (so a plain-model export isn't nudged
        # toward overwriting an unrelated registered skin file).
        meshes, source = _mannequin_meshes(context)
        prefilled = False
        if source == 'skin':
            props = context.scene.igb_actor
            if props.skins_index < len(props.skins):
                item = props.skins[props.skins_index]
                if item.filepath and os.path.exists(item.filepath):
                    dirname = os.path.dirname(item.filepath)
                    basename = os.path.splitext(
                        os.path.basename(item.filepath))[0]
                    # The engine keys mannequins on <id>.igb — offer a clean name
                    self.filepath = os.path.join(dirname, f"{basename}.igb")
                    prefilled = True
        if not prefilled:
            name = meshes[0].name if meshes else "mannequin"
            self.filepath = f"{name}.igb"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class ACTOR_OT_export_animations(Operator, ExportHelper):
    """Export animations to a NEW IGB file (uses original as read-only template)"""
    bl_idname = "actor.export_animations"
    bl_label = "Export Animations"
    bl_options = {'REGISTER'}

    filename_ext = ".igb"

    filter_glob: StringProperty(
        default="*.igb",
        options={'HIDDEN'},
    )

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (props.active_armature and
                props.active_armature in bpy.data.objects and
                len(props.animations) > 0)

    def execute(self, context):
        from .animation_export import export_animations
        return export_animations(context, self.filepath, operator=self)

    def invoke(self, context, event):
        # Default to a NEW file alongside the original (never overwrite template)
        props = context.scene.igb_actor
        armature_obj = bpy.data.objects.get(props.active_armature)
        if armature_obj:
            template = armature_obj.get("igb_anim_file", "")
            if template and os.path.exists(template):
                dirname = os.path.dirname(template)
                stem = os.path.splitext(os.path.basename(template))[0]
                self.filepath = os.path.join(dirname, f"{stem}_export.igb")
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


# Enum item caches — Blender requires the strings returned by enum-items
# callbacks to stay referenced, or the UI shows garbage / crashes.
_replace_target_items = []
_replace_action_items = []


def _replace_anim_target_items(self, context):
    return _replace_target_items or [("NONE", "(no template loaded)", "")]


def _replace_anim_action_items(self, context):
    return _replace_action_items or [("NONE", "(no actions in file)", "")]


class ACTOR_OT_replace_animation(Operator, ExportHelper):
    """Replace or rename ONE animation inside a moveset .igb (e.g. swap the
    idle in 01_cyclops.igb for any Blender action, or repurpose an unused
    slot under a new name)"""
    bl_idname = "actor.replace_animation"
    bl_label = "Replace Animation…"
    bl_options = {'REGISTER'}

    filename_ext = ".igb"
    filter_glob: StringProperty(default="*.igb", options={'HIDDEN'})

    target_anim: EnumProperty(
        name="Target",
        description="Animation inside the .igb to replace",
        items=_replace_anim_target_items,
    )
    source_action: EnumProperty(
        name="Action",
        description="Blender action whose motion replaces the target",
        items=_replace_anim_action_items,
    )
    rename_only: BoolProperty(
        name="Rename Only",
        description="Keep the target's original motion, just change its name "
                    "(the game binds animations by name)",
        default=False,
    )
    new_name: StringProperty(
        name="New Name",
        description="Optional: rename the target animation in the output "
                    "(e.g. turn an unused slot into a new animation name)",
        default="",
    )

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        arm = bpy.data.objects.get(props.active_armature)
        return arm is not None and arm.get("igb_anim_file", "")

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "target_anim")
        layout.prop(self, "rename_only")
        row = layout.row()
        row.enabled = not self.rename_only
        row.prop(self, "source_action")
        layout.prop(self, "new_name")

    def invoke(self, context, event):
        from .animation_export import list_template_animations
        props = context.scene.igb_actor
        armature_obj = bpy.data.objects.get(props.active_armature)
        template = armature_obj.get("igb_anim_file", "") if armature_obj else ""
        if not template or not os.path.exists(template):
            self.report({'ERROR'}, "Armature has no animation .igb on record "
                                   "(re-import the actor with animations)")
            return {'CANCELLED'}

        _replace_target_items.clear()
        try:
            for name, dur in list_template_animations(template):
                _replace_target_items.append(
                    (name, name, f"{dur:.2f}s"))
        except Exception as exc:
            self.report({'ERROR'}, f"Could not read template: {exc}")
            return {'CANCELLED'}
        if not _replace_target_items:
            self.report({'ERROR'}, "No animations found in the template .igb")
            return {'CANCELLED'}

        _replace_action_items.clear()
        for act in bpy.data.actions:
            _replace_action_items.append((act.name, act.name, ""))
        if not _replace_action_items:
            self.report({'ERROR'}, "No actions in this .blend to export")
            return {'CANCELLED'}

        # Preselect: active object's current action, and its matching slot
        obj = context.active_object
        cur = (obj.animation_data.action.name
               if obj and obj.animation_data and obj.animation_data.action
               else "")
        if cur:
            self.source_action = cur
            act = bpy.data.actions.get(cur)
            slot = act.get("igb_anim_name", "") if act else ""
            if slot and any(slot == it[0] for it in _replace_target_items):
                self.target_anim = slot

        dirname = os.path.dirname(template)
        stem = os.path.splitext(os.path.basename(template))[0]
        self.filepath = os.path.join(dirname, f"{stem}_replaced.igb")
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from .animation_export import export_animations

        if self.target_anim in ("", "NONE"):
            self.report({'ERROR'}, "No target animation selected")
            return {'CANCELLED'}

        actions = []
        name_overrides = None
        if not self.rename_only:
            action = bpy.data.actions.get(self.source_action)
            if action is None:
                self.report({'ERROR'}, "Source action not found")
                return {'CANCELLED'}
            if action.get("igb_identity_hold"):
                self.report({'ERROR'},
                            f"'{action.name}' is an engine hold clip with no "
                            f"motion of its own — pick another action")
                return {'CANCELLED'}
            actions = [action]
            name_overrides = {action.name: self.target_anim}

        rename_map = None
        new_name = self.new_name.strip()
        if new_name and new_name != self.target_anim:
            rename_map = {self.target_anim: new_name}

        if not actions and not rename_map:
            self.report({'ERROR'}, "Nothing to do: rename-only with no new name")
            return {'CANCELLED'}

        return export_animations(
            context, self.filepath, operator=self,
            actions_override=actions,
            name_overrides=name_overrides,
            rename_map=rename_map,
        )


def _obj_alive(obj):
    """True if obj is a still-valid Blender object.

    After bpy.ops.object.join()/delete() the joined-away / removed objects are
    purged from bpy.data, and any Python reference to them raises
    "StructRNA of type Object has been removed" on attribute access. This lets
    callers filter such dangling references out of a captured list.
    """
    if obj is None:
        return False
    try:
        obj.name  # touches the underlying StructRNA; raises if removed
        return True
    except (ReferenceError, RuntimeError):
        return False


def _norm_tex_key(name):
    """Normalize a texture image name into a round-trip-stable match key.

    Strips Blender duplicate suffixes (.001) and file extensions in any
    order, lowercased — 'Body.png' before export and 'Body.png.001' on the
    reimported image both reduce to 'body'.
    """
    _EXTS = {'png', 'jpg', 'jpeg', 'tga', 'dds', 'bmp',
             'tif', 'tiff', 'exr', 'psd', 'webp'}
    n = (name or '').lower()
    while True:
        base, dot, ext = n.rpartition('.')
        if dot and base and (ext.isdigit() or ext in _EXTS):
            n = base
            continue
        return n


# Group-socket + node-layout constants shared with ACTOR_OT_add_material_map.
_ROLE_SOCKETS = {'normal': 'Normal',
                 'specular': 'Specular Texture',
                 'gloss': 'Gloss/Mask Texture'}
_ROLE_Y_OFFSETS = {'normal': 250, 'specular': 420, 'gloss': 590}


def _wire_role_maps(mat, entry):
    """Wire captured normal/spec/gloss images into a material's IGB group.

    Same wiring as ACTOR_OT_add_material_map (Image Texture feeding the
    group socket, via a Normal Map node for 'normal'), so the Materials
    panel and the exporter's _find_role_image both see the maps.
    Returns the number of maps wired.
    """
    grp = _igb_group_node(mat)
    if grp is None:
        return 0
    nt = mat.node_tree
    count = 0
    for role, img in entry.get('images', {}).items():
        if img is None or _role_map_node(mat, role) is not None:
            continue
        sock = grp.inputs.get(_ROLE_SOCKETS[role])
        if sock is None:
            continue
        tex = nt.nodes.new('ShaderNodeTexImage')
        tex.label = role.capitalize() + " Map"
        tex.image = img
        y = grp.location.y - _ROLE_Y_OFFSETS[role]
        tex.location = (grp.location.x - 650, y)
        if role == 'normal':
            nm = nt.nodes.new('ShaderNodeNormalMap')
            nm.location = (grp.location.x - 320, y)
            nt.links.new(tex.outputs['Color'], nm.inputs['Color'])
            nt.links.new(nm.outputs['Normal'], sock)
        else:
            nt.links.new(tex.outputs['Color'], sock)
        count += 1
    for attr, val in entry.get('flags', {}).items():
        try:
            setattr(mat, attr, val)
        except Exception:
            pass
    return count


def _round_trip_skin(context, armature_obj, props, operator=None):
    """Full round-trip: export to game-scale IGB, delete original, reimport fresh.

    Exports the rig + child meshes at full game scale (with igb_export_scale
    and axis rotation applied), then deletes the original armature and all
    child meshes, and reimports the exported IGB as a completely fresh actor.

    This gives a clean IGB-compatible model usable for XML2 animation testing.
    """
    import tempfile

    def _report(level, msg):
        if operator:
            operator.report(level, msg)

    # Collect all skinned meshes (children + Armature-modifier users —
    # FBX imports often parent meshes outside the armature)
    from .rig_converter import _get_skinned_meshes
    mesh_children = _get_skinned_meshes(armature_obj)
    if not mesh_children:
        _report({'INFO'}, "No skinned meshes to round-trip")
        return

    # Remember the user's mesh layout so it can be restored after reimport
    # (IGB import splits one object per material — users who combined their
    # meshes expect them to STAY combined)
    source_mesh_count = len(mesh_children)
    source_mesh_name = mesh_children[0].name if mesh_children else ""

    # Source weighting ratio: read the value convert_rig captured BEFORE any
    # weight op (from the settled, freshly-imported meshes). Recomputing it
    # here would under-report — these post-convert meshes carry weight writes
    # made within this same operator, which Blender does not flush until the
    # operator returns. The round-trip is weight-preserving, so this ratio is
    # the true exported weighting; we stamp it on the reimported parts.
    _src_ratio = armature_obj.get("igb_src_weight_ratio", 1.0)

    # Check that skeleton data exists
    if "igb_skin_bone_translations" not in armature_obj:
        _report({'WARNING'},
                "No skeleton data on armature, skipping round-trip")
        return

    try:
        from ..exporter.skin_export import export_skin
        from .actor_import import import_actor

        # 1. Export to temp IGB at FULL game scale.
        tmp_path = os.path.join(tempfile.gettempdir(),
                                f"{armature_obj.name}_roundtrip.igb")

        mesh_objs = [(c, False) for c in mesh_children]
        success = export_skin(
            filepath=tmp_path,
            mesh_objs=mesh_objs,
            armature_obj=armature_obj,
            operator=operator,
            swap_rb=False,
            texture_mode='clut',
        )

        if not success:
            _report({'WARNING'},
                    "Round-trip export failed, keeping original meshes")
            return

        # 2. Find old collection
        old_collection = None
        for coll in bpy.data.collections:
            if armature_obj.name in coll.objects:
                old_collection = coll
                break

        # Capture the conversion stamps BEFORE the original armature is
        # deleted — the reimport builds a fresh object and would otherwise
        # default to XML2, hiding the MUA-only normal/spec/gloss map UI and
        # export options the user converted FOR.
        carry_props = {}
        for key in ("igb_target_game", "igb_full_fingers"):
            if key in armature_obj:
                carry_props[key] = armature_obj[key]

        # Also capture normal/spec/gloss role maps per material. The
        # round-trip IGB only carries the diffuse, so without this any maps
        # the user (or the FBX importer) wired up are silently lost. Keyed
        # by diffuse texture name — the one identity that survives the trip
        # (the exporter writes it into the IGB, the importer names the
        # rebuilt image/material from it). The bpy Images themselves outlive
        # the mesh deletion (data-blocks aren't purged mid-session).
        from ..exporter.skin_export import (_find_role_image,
                                            _get_texture_name)
        carry_maps = {}
        for mc in mesh_children:
            for slot_i, mat in enumerate(mc.data.materials or []):
                if mat is None or not mat.use_nodes or mat.node_tree is None:
                    continue
                tex_key = _norm_tex_key(_get_texture_name(mc,
                                                          mat_slot=slot_i))
                if not tex_key:
                    continue
                entry = carry_maps.setdefault(tex_key,
                                              {'images': {}, 'flags': {}})
                for role in ('normal', 'specular', 'gloss'):
                    img = _find_role_image(mat, role)
                    if img is not None and role not in entry['images']:
                        entry['images'][role] = img
                for attr in ('igb_use_normal_map', 'igb_use_specular_map',
                             'igb_use_gloss_map'):
                    entry['flags'][attr] = bool(getattr(mat, attr, True))
        # Drop keys that carried nothing
        carry_maps = {k: v for k, v in carry_maps.items() if v['images']}

        # 3. Delete original armature and ALL skinned meshes (children AND
        # modifier-linked meshes that aren't parented to the armature)
        to_remove = {c.name for c in armature_obj.children}
        to_remove.update(m.name for m in mesh_children)
        for obj_name in to_remove:
            obj = bpy.data.objects.get(obj_name)
            if obj is not None:
                bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.objects.remove(armature_obj, do_unlink=True)
        armature_obj = None

        # Remove old empty collection
        if old_collection and len(old_collection.objects) == 0:
            bpy.data.collections.remove(old_collection)

        # 4. Reimport the temp IGB as a completely fresh actor.
        result = import_actor(
            context, tmp_path,
            skin_filepaths=[("default", tmp_path)],
            operator=operator,
            options={
                'import_skins': True,
                'import_animations': False,
                'import_materials': True,
                'game_preset': 'xml2_pc',
            },
        )

        if result is None:
            _report({'ERROR'}, "Round-trip reimport failed")
            return

        new_armature, skin_objects, actions = result

        # 5. Set game-scale properties + restore the conversion stamps (the
        # target game drives the Materials panel's MUA map slots + export
        # defaults; without it a converted-for-MUA rig reads as XML2)
        new_armature["igb_export_scale"] = 1.0
        new_armature["igb_converted_rig"] = False
        for key, val in carry_props.items():
            new_armature[key] = val
        if str(carry_props.get("igb_target_game", "")).upper() == 'MUA':
            # display-name convention (matches actor_import's stamping)
            new_armature["igb_game_profile"] = "Marvel: Ultimate Alliance (PC)"

        # 5b. Fix non-deforming bone orientations
        _fix_nondeforming_bone_orientations(new_armature)

        # 5c. Add default IGB material properties
        for _, mesh_obj in skin_objects:
            _ensure_igb_material_properties(mesh_obj)

        # 5c2. Reattach the captured normal/spec/gloss maps to the
        # reimported materials (matched by diffuse texture name; wiring
        # mirrors ACTOR_OT_add_material_map so the Materials panel and the
        # exporter both see them). Blue tangent-space normals are fine to
        # carry as-is — export converts them to MUA DXT5nm automatically.
        if carry_maps:
            reattached = 0
            seen_mats = set()
            for _v, mesh_obj in skin_objects:
                if not _obj_alive(mesh_obj) or mesh_obj.type != 'MESH':
                    continue
                for slot_i, mat in enumerate(
                        mesh_obj.data.materials or []):
                    if (mat is None or mat.name in seen_mats
                            or not mat.use_nodes
                            or mat.node_tree is None):
                        continue
                    seen_mats.add(mat.name)
                    tex_key = _norm_tex_key(
                        _get_texture_name(mesh_obj, mat_slot=slot_i))
                    entry = carry_maps.get(tex_key)
                    if entry:
                        reattached += _wire_role_maps(mat, entry)
            if reattached:
                _report({'INFO'},
                        f"Carried {reattached} normal/spec/gloss map(s) "
                        f"through the round-trip")

        # 5d. Restore the user's mesh layout: IGB import splits the model
        # into one object per material — if the user had a single combined
        # mesh going in, join the reimported pieces back together.
        if source_mesh_count == 1 and len(skin_objects) > 1:
            try:
                # Classify EVERYTHING up front. join() purges the joined-away
                # objects from bpy.data, so any skin_objects entry touched
                # AFTER the join raises "StructRNA ... has been removed" — that
                # is the bug that broke multi-part models (e.g. 15-part skins
                # with no outline meshes). Capture outlines + first variant now,
                # while every reference is still valid.
                main_meshes = [mo for _, mo in skin_objects
                               if mo is not None and mo.type == 'MESH'
                               and not mo.get('igb_is_outline', False)]
                outlines = [(v, mo) for v, mo in skin_objects
                            if mo is not None
                            and mo.get('igb_is_outline', False)]
                first_variant = skin_objects[0][0]
                if len(main_meshes) > 1:
                    if context.mode != 'OBJECT':
                        bpy.ops.object.mode_set(mode='OBJECT')
                    bpy.ops.object.select_all(action='DESELECT')
                    for mo in main_meshes:
                        mo.select_set(True)
                    context.view_layer.objects.active = main_meshes[0]
                    bpy.ops.object.join()
                    joined = main_meshes[0]  # active survives the join
                    if source_mesh_name:
                        joined.name = source_mesh_name
                    # Rebuild skin_objects from pre-join captures only (no
                    # access to the now-removed joined-away objects).
                    # (weight diagnostic stamped for all parts at 5e below)
                    skin_objects = [(first_variant, joined)] + outlines
                    _report({'INFO'},
                            f"Re-joined {len(main_meshes)} imported parts "
                            f"into '{joined.name}' (source was one mesh)")
            except Exception as join_err:
                _report({'WARNING'},
                        f"Could not re-join imported meshes: {join_err}")
                import traceback
                traceback.print_exc()
                # join() may have removed objects before raising; drop any
                # dangling references so the steps below don't crash on them.
                skin_objects = [(v, mo) for v, mo in skin_objects
                                if _obj_alive(mo)]

        # 5e. Stamp the weight diagnostic on EVERY reimported part from the
        # settled source ratio. The round-trip is lossless, so each part's
        # weighted fraction equals the source's; a live recount here would
        # under-report (just-built/just-joined deform data isn't finalized
        # until this operator returns). Covers both the joined-single-mesh
        # case and multi-part models (which are never joined).
        for _v, mo in skin_objects:
            if _obj_alive(mo) and mo.type == 'MESH':
                mo["igb_weighted_vert_count"] = int(round(
                    len(mo.data.vertices) * _src_ratio))

        # 6. Update panel state
        _populate_import_results(context, props, new_armature,
                                 skin_objects, actions)

        _report({'INFO'},
                f"Round-trip complete: game-scale actor with "
                f"{len(skin_objects)} mesh(es)")

        # 7. Clean up temp file
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    except Exception as e:
        _report({'WARNING'}, f"Round-trip failed: {e}")
        import traceback
        traceback.print_exc()


class ACTOR_OT_setup_skin(Operator):
    """Auto-setup an armature for IGB skin export.

    Detects whether the rig is already an XML2 Bip01 skeleton or needs
    conversion. For Bip01 rigs: computes skeleton data and configures
    properties. For non-Bip01 rigs: runs full conversion pipeline.
    Either way, performs a round-trip export/reimport for a clean
    IGB-compatible model ready for in-game use.
    """
    bl_idname = "actor.setup_skin"
    bl_label = "Setup Skin"
    bl_options = {'REGISTER', 'UNDO'}

    # --- Conversion options (only used for non-Bip01 rigs) ---
    rig_profile: EnumProperty(
        name="Source Rig",
        items=[
            ('AUTO', "Auto-Detect",
             "Auto-detect any rig (Unity, Mixamo, Source Engine, MMD, etc.)"),
        ],
        default='AUTO',
    )

    target_game: EnumProperty(
        name="Target Game",
        description="Target game determines skeleton (XML2=35 bones, MUA=35+14 FX bones)",
        items=[
            ('XML2', "XML2", "X-Men Legends II (35 bones, no FX)"),
            ('MUA', "MUA", "Marvel Ultimate Alliance (35 + 14 FX bones)"),
        ],
        default='MUA',
    )

    target_pose: EnumProperty(
        name="Target Pose",
        items=[
            ('T_POSE', "T-Pose (Recommended)",
             "Repose arms to T-pose for XML2 animation compatibility"),
            ('A_POSE', "A-Pose",
             "Repose arms to ~45 degrees down (for custom animations)"),
            ('KEEP_POSE', "Keep Current Pose",
             "Leave the model in its exact imported pose — no arm reposing "
             "and no native-skeleton fit. Use for models already posed how "
             "you want, or for custom animations authored to that pose"),
        ],
        default='T_POSE',
    )

    skeleton_mode: EnumProperty(
        name="Skeleton",
        description="How the skeleton relates to the game's shared biped",
        items=[
            ('KEEP', "Keep Model Proportions (Default)",
             "Keep the model's own limb lengths (pelvis still anchored at "
             "native height). Preserves the model's proportions; deep "
             "crouch/jump animations may show slight foot deviation"),
            ('NATIVE', "Native Skeleton",
             "Warp the mesh onto the game's exact shared biped (every "
             "official skin uses this identical skeleton). All animations "
             "behave exactly like official characters: no floating, no "
             "sinking, standard size. Stylized proportions get adjusted"),
        ],
        default='KEEP',
    )

    align_pose: BoolProperty(
        name="Force T-Pose (Align to Native Bind)",
        description="Rotate limb chains (arms, legs, hands, thumb, fingers) "
                    "to the game's exact bind directions. Fixes off-angle "
                    "thumbs/fingers from curled or spread source poses, but "
                    "deforms the mesh — leave off unless fingers look wrong",
        default=False,
    )

    character_size: FloatProperty(
        name="Character Size",
        description="Body size relative to a standard character (1.0). "
                    "Roots stay native so feet remain planted; sizes far "
                    "from 1.0 show slight crouch deviation in animations "
                    "(herostat scale_factor is the engine-exact method)",
        default=1.0,
        min=0.5,
        max=1.5,
    )

    full_fingers: BoolProperty(
        name="Full Fingers (MUA)",
        description="Build MUA's full 5-finger hand (Bip01_{L|R}_Finger0-4) "
                    "instead of the reduced thumb+1 hand, mapping each source "
                    "finger to its own bone. MUA only. NOTE: fingers only "
                    "ANIMATE if the character uses a full-finger moveset "
                    "(Storm/Ultron/Loki); on other movesets they stay at the "
                    "(correct) rest pose. XML2 ignores this",
        default=False,
    )

    # --- Shared options ---
    auto_scale: BoolProperty(
        name="Auto Scale",
        description="Anchor the pelvis at the native game height (~41.82 units) so the game's animations keep feet on the ground. To resize a character in-game, use herostat scale_factor instead",
        default=True,
    )

    target_height: FloatProperty(
        name="Target Height",
        description="Fallback total height, used ONLY when no pelvis bone "
                    "is found. With a pelvis, scale is locked to the native "
                    "pelvis height for animation compatibility",
        default=68.0,
        min=10.0,
        max=500.0,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'ARMATURE'

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        armature_obj = context.active_object
        from .rig_converter import is_bip01_rig
        has_bip01 = is_bip01_rig(armature_obj) if armature_obj else False

        if has_bip01:
            layout.label(text="Bip01 rig detected", icon='CHECKMARK')
        else:
            layout.label(text="Non-XML2 rig detected", icon='INFO')
        layout.label(text="Will convert, clean up, and round-trip")
        layout.separator()
        layout.prop(self, "target_game")
        if self.target_game == 'MUA':
            layout.label(text="  +14 FX bones (Gun1, fx01-fx13)", icon='BONE_DATA')
            layout.prop(self, "full_fingers")
            if self.full_fingers:
                layout.label(text="  Needs a full-finger moveset to animate",
                             icon='INFO')
        layout.prop(self, "target_pose")
        layout.prop(self, "skeleton_mode")
        layout.prop(self, "align_pose")
        layout.prop(self, "character_size")

        layout.separator()
        layout.prop(self, "auto_scale")
        if self.auto_scale:
            layout.prop(self, "target_height")

    def execute(self, context):
        armature_obj = context.active_object
        return self._convert_and_setup(context, armature_obj)

    def _convert_and_setup(self, context, armature_obj):
        """Full conversion path — handles both Bip01 and non-XML2 rigs."""
        from .rig_converter import convert_rig

        result = convert_rig(armature_obj, profile=self.rig_profile,
                             auto_scale=self.auto_scale,
                             target_height=self.target_height,
                             target_pose=self.target_pose,
                             target_game=self.target_game,
                             skeleton_mode=self.skeleton_mode,
                             align_pose=self.align_pose,
                             character_size=self.character_size,
                             full_fingers=self.full_fingers)

        if not result['success']:
            self.report({'ERROR'}, result['error'])
            return {'CANCELLED'}

        pose_info = ""
        src = result.get('source_pose', 'UNKNOWN')
        if src != 'UNKNOWN':
            pose_info = f" (detected {src} → {self.target_pose})"
        game_info = f" [{self.target_game}]"
        self.report(
            {'INFO'},
            f"Converted rig{game_info}: {result['mapped']} mapped, "
            f"{result['added']} added, {result['removed']} removed"
            f"{pose_info}")

        props = context.scene.igb_actor
        props.active_armature = armature_obj.name

        _populate_skins_from_children(props, armature_obj)

        for child in armature_obj.children:
            if child.type == 'MESH':
                _ensure_igb_material_properties(child)

        # Full round-trip for ALL pose modes (incl. Keep Current Pose) so the
        # model is fully processed into a clean, game-ready skin. The round-
        # trip faithfully preserves whatever pose the converted skeleton is
        # in — it reconstructs bone positions from the inv_joint bind, which
        # encodes the kept pose exactly (verified: an arms-down source stays
        # arms-down through the round-trip).
        _round_trip_skin(context, armature_obj, props, operator=self)
        return {'FINISHED'}

class ACTOR_OT_add_mesh_as_skin(Operator):
    """Add the selected mesh as a skin in the IGB Actors panel"""
    bl_idname = "actor.add_mesh_as_skin"
    bl_label = "Add Mesh as Skin"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        # Need an active armature set AND a mesh selected
        props = context.scene.igb_actor
        if not props.active_armature:
            return False
        arm_obj = bpy.data.objects.get(props.active_armature)
        if arm_obj is None:
            return False
        # Check for mesh selection
        for obj in context.selected_objects:
            if obj.type == 'MESH':
                return True
        return False

    def execute(self, context):
        props = context.scene.igb_actor
        armature_obj = bpy.data.objects.get(props.active_armature)
        if armature_obj is None:
            self.report({'ERROR'}, "No active armature")
            return {'CANCELLED'}

        added = 0
        existing_names = {item.object_name for item in props.skins}

        for obj in context.selected_objects:
            if obj.type != 'MESH':
                continue
            if obj.name in existing_names:
                continue

            # Parent to armature if not already
            if obj.parent != armature_obj:
                obj.parent = armature_obj
                obj.parent_type = 'OBJECT'

            # Add Armature modifier if not present
            has_armature_mod = any(
                m.type == 'ARMATURE' and m.object == armature_obj
                for m in obj.modifiers
            )
            if not has_armature_mod:
                mod = obj.modifiers.new(name="Armature", type='ARMATURE')
                mod.object = armature_obj

            item = props.skins.add()
            item.name = obj.name
            item.object_name = obj.name
            item.is_visible = not obj.hide_viewport
            added += 1

        if added > 0:
            self.report({'INFO'}, f"Added {added} mesh(es) to skins list")
            return {'FINISHED'}
        else:
            self.report({'WARNING'}, "No new meshes to add")
            return {'CANCELLED'}


class ACTOR_OT_remove_skin(Operator):
    """Remove the selected skin from the skins list"""
    bl_idname = "actor.remove_skin"
    bl_label = "Remove Skin"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (len(props.skins) > 0 and
                props.skins_index < len(props.skins))

    def execute(self, context):
        props = context.scene.igb_actor
        idx = props.skins_index
        if idx >= len(props.skins):
            return {'CANCELLED'}

        props.skins.remove(idx)
        # Adjust index
        if props.skins_index >= len(props.skins) and len(props.skins) > 0:
            props.skins_index = len(props.skins) - 1

        return {'FINISHED'}


def _populate_skins_from_children(props, armature_obj):
    """Populate the skins list with all mesh children of the armature."""
    existing_names = {item.object_name for item in props.skins}

    for child in armature_obj.children:
        if child.type != 'MESH':
            continue
        if child.name in existing_names:
            continue

        # Add Armature modifier if not present
        has_armature_mod = any(
            m.type == 'ARMATURE' and m.object == armature_obj
            for m in child.modifiers
        )
        if not has_armature_mod:
            mod = child.modifiers.new(name="Armature", type='ARMATURE')
            mod.object = armature_obj

        item = props.skins.add()
        item.name = child.name
        item.object_name = child.name
        item.is_visible = not child.hide_viewport


# ---------------------------------------------------------------------------
# Animation management operators (Part 2: single import, Part 3: remove)
# ---------------------------------------------------------------------------

# Module-level cache for the two-step animation import dialog
_anim_import_cache = {
    'parsed_anims': [],  # list of ParsedAnimation
    'bind_pose': None,
    'anim_names': [],    # [(identifier, display_name, description), ...]
}


def _anim_name_items(self, context):
    """Dynamic EnumProperty items from cached parsed animations."""
    items = _anim_import_cache.get('anim_names', [])
    if not items:
        return [('NONE', "No animations found", "")]
    return items


class ACTOR_OT_pick_animation(Operator):
    """Select which animations to import"""
    bl_idname = "actor.pick_animation"
    bl_label = "Select Animations to Import"
    bl_options = {'REGISTER', 'UNDO'}

    source_game: EnumProperty(
        name="Source Game",
        description=(
            "Game the animation file comes from. "
            "MUA and XML2 share bone names, so cross-game import works automatically. "
            "Extra MUA bones (Nub endpoints, fx/Gun bones) are skipped on XML2 rigs"
        ),
        items=[
            ('AUTO', "Auto", "Same game as current rig (no bone remapping)"),
            ('XML2', "XML2", "X-Men Legends II animations"),
            ('MUA', "MUA", "Marvel Ultimate Alliance animations"),
        ],
        default='AUTO',
    )

    # Up to 30 animation slots with toggles (enough for most actors)
    anim_00: BoolProperty(name="Anim 0", default=True)
    anim_01: BoolProperty(name="Anim 1", default=True)
    anim_02: BoolProperty(name="Anim 2", default=True)
    anim_03: BoolProperty(name="Anim 3", default=True)
    anim_04: BoolProperty(name="Anim 4", default=True)
    anim_05: BoolProperty(name="Anim 5", default=True)
    anim_06: BoolProperty(name="Anim 6", default=True)
    anim_07: BoolProperty(name="Anim 7", default=True)
    anim_08: BoolProperty(name="Anim 8", default=True)
    anim_09: BoolProperty(name="Anim 9", default=True)
    anim_10: BoolProperty(name="Anim 10", default=True)
    anim_11: BoolProperty(name="Anim 11", default=True)
    anim_12: BoolProperty(name="Anim 12", default=True)
    anim_13: BoolProperty(name="Anim 13", default=True)
    anim_14: BoolProperty(name="Anim 14", default=True)
    anim_15: BoolProperty(name="Anim 15", default=True)
    anim_16: BoolProperty(name="Anim 16", default=True)
    anim_17: BoolProperty(name="Anim 17", default=True)
    anim_18: BoolProperty(name="Anim 18", default=True)
    anim_19: BoolProperty(name="Anim 19", default=True)
    anim_20: BoolProperty(name="Anim 20", default=True)
    anim_21: BoolProperty(name="Anim 21", default=True)
    anim_22: BoolProperty(name="Anim 22", default=True)
    anim_23: BoolProperty(name="Anim 23", default=True)
    anim_24: BoolProperty(name="Anim 24", default=True)
    anim_25: BoolProperty(name="Anim 25", default=True)
    anim_26: BoolProperty(name="Anim 26", default=True)
    anim_27: BoolProperty(name="Anim 27", default=True)
    anim_28: BoolProperty(name="Anim 28", default=True)
    anim_29: BoolProperty(name="Anim 29", default=True)

    @classmethod
    def poll(cls, context):
        return len(_anim_import_cache.get('parsed_anims', [])) > 0

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=350)

    def draw(self, context):
        layout = self.layout

        # Source game selector at the top
        layout.prop(self, "source_game")
        layout.separator()

        anims = _anim_import_cache.get('parsed_anims', [])
        if not anims:
            layout.label(text="No animations found.", icon='INFO')
            return
        layout.label(text=f"Found {len(anims)} animation(s):", icon='ACTION')
        col = layout.column(align=True)
        for i, pa in enumerate(anims):
            if i >= 30:
                col.label(text=f"... and {len(anims) - 30} more (not shown)")
                break
            prop_name = f"anim_{i:02d}"
            secs = pa.duration_ms / 1000.0
            label = f"{pa.name}  ({secs:.1f}s, {len(pa.tracks)} tracks)"
            col.prop(self, prop_name, text=label)

    def execute(self, context):
        from .animation_builder import build_action

        props = context.scene.igb_actor
        armature_obj = bpy.data.objects.get(props.active_armature)
        if armature_obj is None or armature_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "No active armature found")
            return {'CANCELLED'}

        parsed_anims = _anim_import_cache.get('parsed_anims', [])
        bind_pose = _anim_import_cache.get('bind_pose')

        # Determine bone remapping based on source game selection.
        # MUA and XML2 use identical bone names for shared bones, so
        # cross-game import works automatically — extra MUA bones
        # (HeadNub, Finger0Nub, Toe0Nub, fx01, Gun1, etc.) are
        # silently skipped when not found in the target armature.
        # The bone_remap dict is reserved for future use if any
        # name mismatches are discovered.
        bone_remap = None  # No remapping needed — names are identical

        # For cross-character animation: extract the TARGET armature's
        # bind_pose from its original animation file.  This ensures bone
        # translations are computed relative to the target character's
        # skeleton, not the source's.
        target_bind_pose = None
        target_anim_file = armature_obj.get("igb_anim_file", "")
        if target_anim_file and os.path.exists(target_anim_file):
            try:
                from ..igb_format.igb_reader import IGBReader
                from .sg_skeleton import extract_skeleton
                from .sg_animation import extract_animations
                from .actor_import import _extract_bind_pose

                target_reader = IGBReader(target_anim_file)
                target_reader.read()
                # v8 afakeanim binds are unreliable (see the browse operator's
                # source-bind gate) — don't derive target translations from one.
                if target_reader.header.version < 8:
                    target_skel = extract_skeleton(target_reader)
                    if target_skel:
                        target_anims = extract_animations(target_reader,
                                                          target_skel)
                        target_bind_pose = _extract_bind_pose(target_anims)
            except Exception:
                pass  # Fall back to source bind_pose

        # Collect selected animations based on checkboxes
        selected = []
        for i, pa in enumerate(parsed_anims):
            if i >= 30:
                break
            prop_name = f"anim_{i:02d}"
            if getattr(self, prop_name, True):
                selected.append(pa)

        if not selected:
            self.report({'WARNING'}, "No animations selected")
            return {'CANCELLED'}

        # Build a throwaway armature from the SEPARATE anim file's own skeleton
        # so build_action can rebase its rotations into the actor's parent frame
        # (fixes sideways playback when the actor's root chain differs, e.g. a
        # skin-built actor has a +90X "Unnamed bone" root above Bip01). Safe:
        # on any failure we fall back to anim_armature=None (current behavior).
        anim_armature = None
        anim_fp = _anim_import_cache.get('anim_filepath')
        if anim_fp and os.path.exists(anim_fp):
            try:
                anim_armature = _build_anim_ref_armature(context, anim_fp)
            except Exception:
                anim_armature = None

        # Foreign-rig detection: a Rig Converter output has Bip01 bone NAMES
        # but its own rest orientations/rolls — applying game anims through
        # those rests directly gives a crooked lean. Route through the ref
        # armature (native math there) + world-delta retarget instead.
        retarget_mode = (
            anim_armature is not None
            and _rest_local_mismatch(armature_obj, anim_armature)
        )
        if retarget_mode:
            self.report({'INFO'},
                        "Custom rig detected — retargeting animations "
                        "through the game skeleton")

        fps = context.scene.render.fps
        imported_count = 0
        try:
            for pa in selected:
                # Identity-hold clips (e.g. lunge_loop) contain no pose of
                # their own — in-game they hold whatever the ENTRY anim left.
                # When the moveset has the matching '<stem>_start', preview
                # the hold with that anim's FINAL pose instead of rest.
                pa_to_build = pa
                hold_from = None
                if (getattr(pa, 'identity_hold', False)
                        and pa.name.lower().endswith('_loop')):
                    stem = pa.name[:-len('_loop')]
                    start_pa = next(
                        (p for p in parsed_anims
                         if p.name.lower() == (stem + '_start').lower()
                         and not getattr(p, 'identity_hold', False)), None)
                    if start_pa is not None and start_pa.tracks:
                        from .sg_animation import (ParsedAnimation,
                                                   ParsedAnimationTrack,
                                                   ParsedKeyframe)
                        held_tracks = []
                        for t in start_pa.tracks:
                            if not t.keyframes:
                                continue
                            last = t.keyframes[-1]
                            held_tracks.append(ParsedAnimationTrack(
                                bone_name=t.bone_name,
                                bone_index=t.bone_index,
                                keyframes=[ParsedKeyframe(
                                    time_ms=0.0,
                                    quaternion=last.quaternion,
                                    translation=last.translation)],
                                is_constant=True))
                        if held_tracks:
                            pa_to_build = ParsedAnimation(
                                name=pa.name,
                                duration_ms=pa.duration_ms,
                                tracks=held_tracks,
                                priority=pa.priority)
                            hold_from = start_pa.name

                # rebase_unanimated_parents fixes MUA v8 menu movesets whose
                # unanimated root rest differs from the skin's (#45 nickfury
                # tilt-back). Internally gated to fire only when the anim's
                # bind quat is absent/degenerate, so XML2/v6 files (valid
                # binds, already correct) are untouched.
                if retarget_mode:
                    action = _import_anim_via_retarget(
                        context, anim_armature, armature_obj, pa_to_build,
                        bind_pose)
                else:
                    action = build_action(armature_obj, pa_to_build,
                                          bind_pose=bind_pose,
                                          bone_remap=bone_remap,
                                          target_bind_pose=target_bind_pose,
                                          anim_armature=anim_armature,
                                          rebase_unanimated_parents=True)
                if action is None:
                    continue
                if hold_from:
                    action["igb_identity_hold"] = True
                    action["igb_hold_from"] = hold_from

                item = props.animations.add()
                item.name = action.name
                item.action_name = action.name
                item.duration_ms = action.get("igb_duration_ms", 0.0)
                item.track_count = action.get("igb_track_count", 0)
                item.frame_count = max(1, int(item.duration_ms / 1000.0 * fps))
                imported_count += 1
        finally:
            if anim_armature is not None:
                _remove_anim_ref_armature(anim_armature)

        if imported_count > 0:
            props.animations_index = len(props.animations) - 1

        self.report({'INFO'}, f"Imported {imported_count} animation(s)")

        # Clear cache
        _anim_import_cache['parsed_anims'] = []
        _anim_import_cache['bind_pose'] = None
        _anim_import_cache['anim_names'] = []
        _anim_import_cache['anim_filepath'] = None

        return {'FINISHED'}


class ACTOR_OT_remove_animation(Operator):
    """Remove the selected animation from the list"""
    bl_idname = "actor.remove_animation"
    bl_label = "Remove Animation"
    bl_options = {'REGISTER', 'UNDO'}

    delete_action: BoolProperty(
        name="Delete Action",
        description="Also delete the Blender Action data",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (len(props.animations) > 0 and
                props.animations_index < len(props.animations))

    def execute(self, context):
        props = context.scene.igb_actor
        idx = props.animations_index
        if idx >= len(props.animations):
            return {'CANCELLED'}

        item = props.animations[idx]
        action = bpy.data.actions.get(item.action_name)

        # Clear from armature if it was the active action
        armature_obj = bpy.data.objects.get(props.active_armature)
        if (armature_obj and armature_obj.animation_data and
                armature_obj.animation_data.action == action):
            armature_obj.animation_data.action = None

        # Delete the Blender Action
        if self.delete_action and action is not None:
            bpy.data.actions.remove(action)

        # Remove from list
        props.animations.remove(idx)
        if props.animations_index >= len(props.animations) and len(props.animations) > 0:
            props.animations_index = len(props.animations) - 1

        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Skin selection dialog (Part 5)
# ---------------------------------------------------------------------------

# Module-level cache for the two-step skin selection
_skin_import_cache = {
    'anim_filepath': '',
    'skins': [],      # [(name, path), ...]
    'options': {},
}


class ACTOR_OT_select_skins(Operator):
    """Select which skins to import with the actor"""
    bl_idname = "actor.select_skins"
    bl_label = "Select Skins to Import"
    bl_options = {'REGISTER', 'UNDO'}

    # Up to 10 skin slots with toggles
    skin_00: BoolProperty(name="Skin 0", default=True)
    skin_01: BoolProperty(name="Skin 1", default=True)
    skin_02: BoolProperty(name="Skin 2", default=True)
    skin_03: BoolProperty(name="Skin 3", default=True)
    skin_04: BoolProperty(name="Skin 4", default=True)
    skin_05: BoolProperty(name="Skin 5", default=True)
    skin_06: BoolProperty(name="Skin 6", default=True)
    skin_07: BoolProperty(name="Skin 7", default=True)
    skin_08: BoolProperty(name="Skin 8", default=True)
    skin_09: BoolProperty(name="Skin 9", default=True)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        layout = self.layout
        skins = _skin_import_cache.get('skins', [])
        if not skins:
            layout.label(text="No skins discovered.", icon='INFO')
            return
        layout.label(text=f"Found {len(skins)} skin(s):", icon='MESH_DATA')
        col = layout.column(align=True)
        for i, (name, path) in enumerate(skins):
            if i >= 10:
                break
            prop_name = f"skin_{i:02d}"
            basename = os.path.basename(path)
            col.prop(self, prop_name, text=f"{name} ({basename})")

    def execute(self, context):
        from .actor_import import import_actor

        skins = _skin_import_cache.get('skins', [])
        anim_filepath = _skin_import_cache.get('anim_filepath', '')
        options = _skin_import_cache.get('options', {})

        if not anim_filepath:
            self.report({'ERROR'}, "No animation file cached")
            return {'CANCELLED'}

        # Filter skins based on checkboxes
        selected_skins = []
        for i, (name, path) in enumerate(skins):
            if i >= 10:
                break
            prop_name = f"skin_{i:02d}"
            if getattr(self, prop_name, True):
                selected_skins.append((name, path))

        # Clear the import_skins option if no skins selected
        # but always pass the list (even empty) to import_actor
        props = context.scene.igb_actor
        game_dir = props.game_dir if props.game_dir else None

        result = import_actor(
            context, anim_filepath,
            skin_filepaths=selected_skins,
            game_dir=game_dir,
            operator=self,
            options=options,
        )

        # Clear cache
        _skin_import_cache['anim_filepath'] = ''
        _skin_import_cache['skins'] = []
        _skin_import_cache['options'] = {}

        if result is None:
            return {'CANCELLED'}

        armature_obj, skin_objects, actions = result

        # Ensure all imported skin meshes have IGB material properties
        for _, mesh_obj in skin_objects:
            _ensure_igb_material_properties(mesh_obj)

        # Update panel state (same as original import_actor execute)
        _populate_import_results(context, props, armature_obj,
                                 skin_objects, actions)

        return {'FINISHED'}


def _populate_import_results(context, props, armature_obj, skin_objects, actions):
    """Populate the panel lists after a successful import.

    Saves the previous armature's state before switching, then populates
    the new armature's skins and animations from the import result.
    """
    from . import properties as actor_props

    # Save the outgoing armature's state before we overwrite the lists
    old_armature = props.active_armature
    if old_armature and old_armature != armature_obj.name:
        actor_props._save_armature_state(props, old_armature)

    # Set the new armature (bypass update callback since we're populating directly)
    props["active_armature"] = armature_obj.name
    actor_props._prev_armature = armature_obj.name
    props.anim_file = armature_obj.get("igb_anim_file", "")

    # Populate skins list
    props.skins.clear()
    for variant_name, mesh_obj in skin_objects:
        item = props.skins.add()
        is_outline = mesh_obj.get("igb_is_outline", False)
        if is_outline:
            item.name = f"{variant_name} (outline)"
        else:
            item.name = variant_name
        item.object_name = mesh_obj.name
        item.is_visible = not mesh_obj.hide_viewport

    # Populate animations list
    props.animations.clear()
    fps = context.scene.render.fps
    for action in actions:
        item = props.animations.add()
        item.name = action.name
        item.action_name = action.name
        item.duration_ms = action.get("igb_duration_ms", 0.0)
        item.track_count = action.get("igb_track_count", 0)
        item.frame_count = max(1, int(item.duration_ms / 1000.0 * fps))

    # Save the newly populated state so switching away and back restores it
    actor_props._save_armature_state(props, armature_obj.name)


def _fix_nondeforming_bone_orientations(armature_obj):
    """Fix Root/Bip01/Motion bone orientations after round-trip reimport.

    In native XML2 imports, these non-deforming bones get their orientation
    from the afakeanim bind_pose (identity quaternion → bone Y-axis along
    world Y, Z-axis along world Z).  The round-trip has no bind_pose, so
    build_armature defaults to Z-up tails, which changes rest_q and causes
    a 90° animation rotation.

    Fix: enter edit mode and set these bones to identity orientation
    (tail along Y-forward, roll aligned to Z-up).
    """
    from mathutils import Vector

    bpy.context.view_layer.objects.active = armature_obj
    armature_obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')

    nondeforming = {"Bone_000", "", "Bip01", "Motion"}
    for eb in armature_obj.data.edit_bones:
        if eb.name in nondeforming:
            bone_len = max(eb.length, 0.5)
            # Identity orientation: tail along Y (forward in game space)
            eb.tail = eb.head + Vector((0, bone_len, 0))
            eb.align_roll(Vector((0, 0, 1)))

    bpy.ops.object.mode_set(mode='OBJECT')


def _ensure_igb_material_properties(mesh_obj):
    """Ensure a mesh's materials have IGB custom properties for the panel.

    If a material already has igb_diffuse (set by build_material from IGB
    data), it's left as-is.  Otherwise, sensible defaults are added so the
    IGB Materials panel can edit blend, alpha, lighting, and cull settings.
    """
    if not mesh_obj or mesh_obj.type != 'MESH':
        return

    for mat in mesh_obj.data.materials:
        if mat is None:
            continue

        # Derive diffuse from Principled BSDF if no igb_diffuse already set
        diffuse = [0.8, 0.8, 0.8, 1.0]
        if "igb_diffuse" not in mat and mat.use_nodes and mat.node_tree:
            for node in mat.node_tree.nodes:
                if node.type == 'BSDF_PRINCIPLED':
                    bc = node.inputs.get('Base Color')
                    if bc:
                        c = bc.default_value
                        diffuse = [c[0], c[1], c[2], c[3]]
                    break

        # Set each property only if not already present.
        # build_material() sets these from IGB data during normal imports;
        # this fills in defaults for non-IGB materials (VRChat, Unity) or
        # IGB files with missing state attributes.
        def _set(key, val):
            if key not in mat:
                mat[key] = val

        # Core material properties
        _set("igb_diffuse", diffuse)
        _set("igb_ambient", diffuse[:])
        _set("igb_specular", [0.0, 0.0, 0.0, 1.0])
        _set("igb_emission", [0.0, 0.0, 0.0, 1.0])
        _set("igb_shininess", 0.0)
        _set("igb_flags", 31)

        # Render state defaults (standard opaque character material)
        _set("igb_blend_enabled", True)
        _set("igb_blend_src", 4)   # SRC_ALPHA
        _set("igb_blend_dst", 5)   # ONE_MINUS_SRC_ALPHA
        _set("igb_alpha_test_enabled", True)
        _set("igb_alpha_func", 6)  # GREATER
        _set("igb_alpha_ref", 0.5)
        _set("igb_lighting_enabled", True)
        _set("igb_cull_face_enabled", True)
        _set("igb_cull_face_mode", 0)

        # Color attribute (white tint = no tint)
        _set("igb_color_r", 1.0)
        _set("igb_color_g", 1.0)
        _set("igb_color_b", 1.0)
        _set("igb_color_a", 1.0)


class ACTOR_OT_refresh_segments(Operator):
    """Recompute segment diagnostics (blend weight counts) for the active skin"""
    bl_idname = "actor.refresh_segments"
    bl_label = "Refresh Segment Info"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (props.active_armature and
                len(props.skins) > 0 and
                props.skins_index < len(props.skins))

    def execute(self, context):
        from .actor_import import _count_weighted_vertices, collect_skin_segments

        props = context.scene.igb_actor
        segments = collect_skin_segments(props, props.skins_index)

        if not segments:
            self.report({'WARNING'}, "No segments found for active skin")
            return {'CANCELLED'}

        count = 0
        for seg_mesh, _ in segments:
            seg_mesh["igb_weighted_vert_count"] = _count_weighted_vertices(seg_mesh)
            count += 1

        self.report({'INFO'}, f"Updated diagnostics for {count} segment(s)")
        return {'FINISHED'}


class ACTOR_OT_add_segment(Operator):
    """Add the selected mesh as a segment to the active skin"""
    bl_idname = "actor.add_segment"
    bl_label = "Add Segment"
    bl_options = {'REGISTER', 'UNDO'}

    segment_name: StringProperty(
        name="Segment Name",
        description="Name for this segment (e.g. 'gun_left', 'cape'). "
                    "Leave empty to add as part of the body (no segment wrapping)",
        default="",
    )

    segment_visible: BoolProperty(
        name="Visible",
        description="If checked, segment starts visible. "
                    "If unchecked, segment starts hidden (flag bit 1 set)",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        if not props.active_armature:
            return False
        if not (len(props.skins) > 0 and props.skins_index < len(props.skins)):
            return False
        # Need a mesh selected
        for obj in context.selected_objects:
            if obj.type == 'MESH':
                return True
        return False

    def invoke(self, context, event):
        # Show dialog so user can set segment name and visibility
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        props = context.scene.igb_actor
        armature_obj = bpy.data.objects.get(props.active_armature)
        if armature_obj is None:
            self.report({'ERROR'}, "No active armature")
            return {'CANCELLED'}

        seg_flags = 0 if self.segment_visible else 2

        existing_names = {item.object_name for item in props.skins}
        added = 0

        for obj in context.selected_objects:
            if obj.type != 'MESH':
                continue
            if obj.name in existing_names:
                continue

            # Parent to armature if not already
            if obj.parent != armature_obj:
                obj.parent = armature_obj
                obj.parent_type = 'OBJECT'

            # Add Armature modifier if not present
            has_armature_mod = any(
                m.type == 'ARMATURE' and m.object == armature_obj
                for m in obj.modifiers
            )
            if not has_armature_mod:
                mod = obj.modifiers.new(name="Armature", type='ARMATURE')
                mod.object = armature_obj

            # Auto-detect outline from name
            is_outline = "_outline" in obj.name.lower()
            obj["igb_is_outline"] = is_outline

            # Set segment properties
            if self.segment_name:
                if is_outline:
                    obj["igb_segment_name"] = self.segment_name + "_outline"
                else:
                    obj["igb_segment_name"] = self.segment_name
            else:
                obj["igb_segment_name"] = ""

            obj["igb_segment_flags"] = seg_flags

            # Apply Blender visibility to match segment flags
            if seg_flags & 2:
                obj.hide_viewport = True
                obj.hide_render = True
            else:
                obj.hide_viewport = False
                obj.hide_render = False

            item = props.skins.add()
            item.name = f"{obj.name} (outline)" if is_outline else obj.name
            item.object_name = obj.name
            item.is_visible = not obj.hide_viewport
            added += 1

        if added > 0:
            vis_text = "visible" if self.segment_visible else "hidden"
            seg_text = f"'{self.segment_name}'" if self.segment_name else "body"
            self.report({'INFO'},
                        f"Added {added} mesh(es) as {seg_text} segment ({vis_text})")
            return {'FINISHED'}
        else:
            self.report({'WARNING'}, "No new meshes to add as segments")
            return {'CANCELLED'}


class ACTOR_OT_remove_segment(Operator):
    """Remove all meshes in a segment group from the skins list"""
    bl_idname = "actor.remove_segment"
    bl_label = "Remove Segment"
    bl_options = {'REGISTER', 'UNDO'}

    segment_name: StringProperty(
        name="Segment Name",
        description="Base segment name (e.g. 'gun_left'). "
                    "Removes both main and outline meshes for this segment",
        default="",
    )

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (len(props.skins) > 0 and
                props.skins_index < len(props.skins))

    def execute(self, context):
        props = context.scene.igb_actor

        if not self.segment_name:
            self.report({'WARNING'}, "No segment specified")
            return {'CANCELLED'}

        # Collect indices of skins list entries that belong to this segment
        # A mesh belongs to segment "gun_left" if its igb_segment_name is
        # "gun_left" or "gun_left_outline"
        matching_names = {self.segment_name, self.segment_name + "_outline"}
        indices_to_remove = []

        for i, item in enumerate(props.skins):
            mesh_obj = bpy.data.objects.get(item.object_name)
            if mesh_obj is None:
                continue
            seg = mesh_obj.get("igb_segment_name", "")
            if seg in matching_names:
                indices_to_remove.append(i)

        if not indices_to_remove:
            self.report({'WARNING'},
                        f"No meshes found for segment '{self.segment_name}'")
            return {'CANCELLED'}

        # Remove in reverse order so indices stay valid
        for i in reversed(indices_to_remove):
            props.skins.remove(i)

        # Adjust skins_index if needed
        if props.skins_index >= len(props.skins) and len(props.skins) > 0:
            props.skins_index = len(props.skins) - 1

        self.report({'INFO'},
                    f"Removed {len(indices_to_remove)} mesh(es) "
                    f"from segment '{self.segment_name}'")
        return {'FINISHED'}


class ACTOR_OT_rename_segment(Operator):
    """Rename a segment group (updates all meshes in the group)"""
    bl_idname = "actor.rename_segment"
    bl_label = "Rename Segment"
    bl_options = {'REGISTER', 'UNDO'}

    old_name: StringProperty(
        name="Current Name",
        description="Current base segment name",
        default="",
    )

    new_name: StringProperty(
        name="New Name",
        description="New segment name (e.g. 'gun_left', 'cape'). "
                    "Leave empty to convert segment meshes to body meshes",
        default="",
    )

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (props.active_armature and
                len(props.skins) > 0 and
                props.skins_index < len(props.skins))

    def invoke(self, context, event):
        self.new_name = self.old_name
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "new_name")

    def execute(self, context):
        from .actor_import import collect_skin_segments

        props = context.scene.igb_actor
        segments = collect_skin_segments(props, props.skins_index)

        if not self.old_name:
            self.report({'WARNING'}, "Cannot rename the body group")
            return {'CANCELLED'}

        new_name = self.new_name.strip()
        if new_name == self.old_name:
            return {'CANCELLED'}

        # Find all meshes matching the old base segment name
        count = 0
        for seg_mesh, _is_outline in segments:
            mesh_seg = seg_mesh.get("igb_segment_name", "")
            base_seg = mesh_seg
            is_outline_seg = base_seg.endswith("_outline")
            if is_outline_seg:
                base_seg = base_seg[:-8]
            if base_seg != self.old_name:
                continue

            # Set new segment name
            if new_name:
                if is_outline_seg:
                    seg_mesh["igb_segment_name"] = new_name + "_outline"
                else:
                    seg_mesh["igb_segment_name"] = new_name
            else:
                # Converting to body — clear segment name
                seg_mesh["igb_segment_name"] = ""
            count += 1

        if count == 0:
            self.report({'WARNING'},
                        f"No meshes found for segment '{self.old_name}'")
            return {'CANCELLED'}

        target = f"'{new_name}'" if new_name else "body"
        self.report({'INFO'},
                    f"Renamed segment '{self.old_name}' → {target} "
                    f"({count} mesh(es))")
        return {'FINISHED'}


class ACTOR_OT_select_segment(Operator):
    """Select all meshes belonging to a segment group"""
    bl_idname = "actor.select_segment"
    bl_label = "Select Segment Meshes"
    bl_options = {'REGISTER', 'UNDO'}

    segment_name: StringProperty(
        name="Segment Name",
        description="Base segment name to select (empty = body)",
        default="",
    )

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (props.active_armature and
                len(props.skins) > 0 and
                props.skins_index < len(props.skins))

    def execute(self, context):
        from .actor_import import collect_skin_segments

        props = context.scene.igb_actor
        segments = collect_skin_segments(props, props.skins_index)

        # Deselect all first
        bpy.ops.object.select_all(action='DESELECT')

        count = 0
        last_obj = None
        for seg_mesh, _is_outline in segments:
            mesh_seg = seg_mesh.get("igb_segment_name", "")
            base_seg = mesh_seg
            if base_seg.endswith("_outline"):
                base_seg = base_seg[:-8]
            if base_seg == self.segment_name:
                seg_mesh.select_set(True)
                last_obj = seg_mesh
                count += 1

        if last_obj:
            context.view_layer.objects.active = last_obj

        name = f"'{self.segment_name}'" if self.segment_name else "body"
        self.report({'INFO'}, f"Selected {count} mesh(es) in {name}")
        return {'FINISHED'}


class ACTOR_OT_toggle_segment(Operator):
    """Toggle segment visibility (visible/hidden) for export"""
    bl_idname = "actor.toggle_segment"
    bl_label = "Toggle Segment"
    bl_options = {'REGISTER', 'UNDO'}

    segment_name: StringProperty(
        name="Segment Name",
        default="",
    )

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (props.active_armature and
                len(props.skins) > 0 and
                props.skins_index < len(props.skins))

    def execute(self, context):
        from .actor_import import collect_skin_segments

        props = context.scene.igb_actor
        segments = collect_skin_segments(props, props.skins_index)

        if not segments or not self.segment_name:
            self.report({'WARNING'}, "No segment specified")
            return {'CANCELLED'}

        # Find all meshes matching this base segment name (main + outline pair)
        # Main segments: igb_segment_name = "gun_left"
        # Outline segments: igb_segment_name = "gun_left_outline"
        matched = []
        for seg_mesh, _is_outline in segments:
            mesh_seg = seg_mesh.get("igb_segment_name", "")
            # Strip _outline suffix for comparison
            base_seg = mesh_seg
            if base_seg.endswith("_outline"):
                base_seg = base_seg[:-8]
            if base_seg == self.segment_name:
                matched.append(seg_mesh)

        if not matched:
            self.report({'WARNING'},
                        f"No meshes found for segment '{self.segment_name}'")
            return {'CANCELLED'}

        # Toggle: if currently hidden (flags & 2), make visible, else hide
        current_flags = matched[0].get("igb_segment_flags", 0)
        new_hidden = not bool(current_flags & 2)
        new_flags = 2 if new_hidden else 0

        for mesh_obj in matched:
            mesh_obj["igb_segment_flags"] = new_flags
            mesh_obj.hide_viewport = new_hidden
            mesh_obj.hide_render = new_hidden

        state = "Hidden" if new_hidden else "Visible"
        self.report({'INFO'},
                    f"Segment '{self.segment_name}': {state} "
                    f"({len(matched)} mesh(es))")
        return {'FINISHED'}


class ACTOR_OT_setup_vmc4b(Operator):
    """Configure VMC4B bone bindings for XML2/MUA skeleton"""
    bl_idname = "actor.setup_vmc4b"
    bl_label = "Setup VMC4B for XML2"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (props.active_armature and
                props.active_armature in bpy.data.objects and
                hasattr(context.scene, 'vmc4b_target_armature'))

    def execute(self, context):
        from .vmc_bridge import setup_vmc4b_bindings

        props = context.scene.igb_actor
        armature_obj = bpy.data.objects.get(props.active_armature)
        if armature_obj is None or armature_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "No active armature found")
            return {'CANCELLED'}

        bound, skipped = setup_vmc4b_bindings(armature_obj)
        if bound > 0:
            self.report({'INFO'},
                        f"VMC4B configured: {bound} bones bound"
                        f"{f', {len(skipped)} skipped' if skipped else ''}. "
                        f"Press Connect in VMC4B panel to start.")
        else:
            self.report({'WARNING'},
                        "No VMC4B bone bindings could be set. "
                        "Is the VMC4B addon installed?")

        return {'FINISHED'}


class ACTOR_OT_cleanup_vmc(Operator):
    """Remove VMC4B orientation correction from armature"""
    bl_idname = "actor.cleanup_vmc"
    bl_label = "Remove VMC Setup"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        if not props.active_armature:
            return False
        armature_obj = bpy.data.objects.get(props.active_armature)
        if not armature_obj:
            return False
        return "vmc_use_standard_orientations" in armature_obj

    def execute(self, context):
        from .vmc_bridge import cleanup_vmc_setup

        props = context.scene.igb_actor
        armature_obj = bpy.data.objects.get(props.active_armature)
        if armature_obj is None:
            self.report({'ERROR'}, "No active armature found")
            return {'CANCELLED'}

        cleanup_vmc_setup(armature_obj)
        self.report({'INFO'}, "VMC orientation correction removed")
        return {'FINISHED'}


class ACTOR_OT_vr_retarget(Operator):
    """Retarget a Unity / VR source armature's recorded motion onto the loaded
    actor's Bip01 skeleton, baking a new Action ready to export as an IGB"""
    bl_idname = "actor.vr_retarget"
    bl_label = "Retarget VR Motion"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        if (not props.active_armature or
                props.active_armature not in bpy.data.objects):
            return False
        src = props.vr_source_armature
        return (bool(src) and src in bpy.data.objects and
                src != props.active_armature)

    def execute(self, context):
        from .vr_mocap import retarget_and_bake

        props = context.scene.igb_actor
        tgt = bpy.data.objects.get(props.active_armature)
        src = bpy.data.objects.get(props.vr_source_armature)
        if tgt is None or tgt.type != 'ARMATURE':
            self.report({'ERROR'}, "Active actor is not an armature")
            return {'CANCELLED'}
        if src is None or src.type != 'ARMATURE':
            self.report({'ERROR'}, "Source is not an armature")
            return {'CANCELLED'}

        # Game from the actor's stored import profile (FX-bone aliases).
        profile = (tgt.get("igb_game_profile", "") or "").lower()
        game = 'mua' if 'mua' in profile else 'xml2'

        # Frame range: source Action range, or the manual range.
        src_action = (src.animation_data.action
                      if src.animation_data else None)
        if props.vr_use_action_range and src_action:
            fr = src_action.frame_range
            f0, f1 = int(fr[0]), int(fr[1])
        else:
            f0, f1 = int(props.vr_frame_start), int(props.vr_frame_end)
        if f1 < f0:
            f0, f1 = f1, f0

        base = src_action.name if src_action else src.name
        action_name = "VR_" + base

        try:
            action, n_mapped, n_frames = retarget_and_bake(
                src, tgt, f0, f1, game=game, action_name=action_name,
                root_motion=props.vr_root_motion)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f"Retarget failed: {exc}")
            return {'CANCELLED'}

        if action is None:
            self.report({'ERROR'},
                        "Retarget failed — fewer than 3 source bones matched "
                        "the Bip01 skeleton. Check bone names in the console.")
            return {'CANCELLED'}

        self.report({'INFO'},
                    f"Baked '{action.name}': {n_mapped} bones over "
                    f"{n_frames} frames. Now export it as a new IGB below.")
        return {'FINISHED'}


class ACTOR_OT_import_mocap(Operator, ImportHelper):
    """Import a mocap performance file (e.g. actors/zone_briefing1_10.igb) as a
    visible mocap rig with every performance available as an Action"""
    bl_idname = "actor.import_mocap"
    bl_label = "Import Mocap Performance"
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = ".igb"
    filter_glob: StringProperty(default="*.igb", options={'HIDDEN'})

    def execute(self, context):
        from .mocap_tools import import_mocap
        try:
            res = import_mocap(context, self.filepath, operator=self)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f"Mocap import failed: {exc}")
            return {'CANCELLED'}
        if not res:
            self.report({'ERROR'}, "No mocap rig / animations found in file")
            return {'CANCELLED'}
        arm, actions = res
        context.scene.igb_actor.mocap_source_armature = arm.name
        self.report({'INFO'},
                    f"Imported mocap rig '{arm.name}': {len(arm.data.bones)} "
                    f"bones, {len(actions)} performances. Set as retarget source "
                    f"— pick one from the Action editor, then Retarget.")
        return {'FINISHED'}


class ACTOR_OT_retarget_mocap(Operator):
    """Retarget the mocap rig's active performance onto the loaded actor's rig"""
    bl_idname = "actor.retarget_mocap"
    bl_label = "Retarget Performance onto Actor"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        src = props.mocap_source_armature
        return (props.active_armature and
                props.active_armature in bpy.data.objects and
                src and src in bpy.data.objects and
                src != props.active_armature)

    def execute(self, context):
        from .mocap_tools import retarget_performance
        props = context.scene.igb_actor
        tgt = bpy.data.objects.get(props.active_armature)
        src = bpy.data.objects.get(props.mocap_source_armature)
        if src is None or src.type != 'ARMATURE':
            self.report({'ERROR'}, "Mocap rig not found")
            return {'CANCELLED'}
        if tgt is None or tgt.type != 'ARMATURE':
            self.report({'ERROR'}, "Target actor is not an armature")
            return {'CANCELLED'}
        perf = (src.animation_data.action
                if src.animation_data else None)
        if perf is None:
            self.report({'ERROR'},
                        "Pick a performance first — set an Action on the mocap "
                        "rig (Action editor / dope sheet)")
            return {'CANCELLED'}
        profile = (tgt.get("igb_game_profile", "") or "").lower()
        game = 'mua' if 'mua' in profile else 'xml2'
        if props.mocap_full_range:
            fr = perf.frame_range
            f0, f1 = int(fr[0]), int(fr[1])
        else:
            f0, f1 = int(props.mocap_frame_start), int(props.mocap_frame_end)
        if f1 < f0:
            f0, f1 = f1, f0
        try:
            action, nmap, nfr = retarget_performance(
                src, tgt, perf, f0, f1, game=game,
                root_motion=props.mocap_root_motion)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f"Retarget failed: {exc}")
            return {'CANCELLED'}
        if action is None:
            self.report({'ERROR'},
                        "Retarget failed — too few bones matched the character")
            return {'CANCELLED'}
        if tgt.animation_data is None:
            tgt.animation_data_create()
        tgt.animation_data.action = action
        self.report({'INFO'},
                    f"Retargeted '{perf.name}': {nmap} bones over {nfr} frames "
                    f"onto {tgt.name}")
        return {'FINISHED'}


class ACTOR_OT_export_mocap(Operator, ExportHelper):
    """Export the active armature's active Action to a v6 zone-style mocap IGB"""
    bl_idname = "actor.export_mocap"
    bl_label = "Export Mocap (zone IGB)"
    bl_options = {'REGISTER'}
    filename_ext = ".igb"
    filter_glob: StringProperty(default="*.igb", options={'HIDDEN'})
    anim_name: StringProperty(name="Performance Name", default="zone1")

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        arm = (bpy.data.objects.get(props.active_armature)
               if props.active_armature else None)
        return (arm is not None and arm.animation_data is not None and
                arm.animation_data.action is not None)

    def execute(self, context):
        from .mocap_tools import export_performance
        props = context.scene.igb_actor
        arm = bpy.data.objects.get(props.active_armature)
        act = arm.animation_data.action
        profile = (arm.get("igb_game_profile", "") or "").lower()
        game = 'mua' if 'mua' in profile else 'xml2'
        try:
            r = export_performance(arm, act, self.filepath, game=game,
                                   anim_name=self.anim_name)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f"Export failed: {exc}")
            return {'CANCELLED'}
        if not r:
            self.report({'ERROR'},
                        "Export failed — no reference mocap IGB found. Import a "
                        "mocap file first (its schema is reused).")
            return {'CANCELLED'}
        self.report({'INFO'},
                    f"Exported '{self.anim_name}' to "
                    f"{os.path.basename(self.filepath)} "
                    f"({os.path.getsize(self.filepath)} bytes)")
        return {'FINISHED'}

    def invoke(self, context, event):
        props = context.scene.igb_actor
        arm = (bpy.data.objects.get(props.active_armature)
               if props.active_armature else None)
        if arm and arm.animation_data and arm.animation_data.action:
            a = arm.animation_data.action
            self.anim_name = a.get("igb_anim_name", a.name)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class ACTOR_OT_load_briefing(Operator, ImportHelper):
    """Load a full briefing scene — map + cast (skins) + staging positions +
    mocap performances + voice audio — into one editable collection"""
    bl_idname = "actor.load_briefing"
    bl_label = "Load Briefing"
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = ".igb"
    filter_glob: StringProperty(default="*.igb", options={'HIDDEN'})

    apply_performances: BoolProperty(
        name="Apply Performances", default=True,
        description="Direct-apply each character's zone performance onto its "
                    "65-bone mocap model (no retarget)")
    root_motion: BoolProperty(
        name="Keep Root Motion", default=False,
        description="Keep the performance's root translation. Off keeps the "
                    "cast on their staging marks (recommended)")
    load_audio: BoolProperty(
        name="Load Voice Audio", default=True,
        description="Decode the ZSNDPC voice bank and add it to the sequencer")
    load_camera: BoolProperty(
        name="Import Script Camera", default=True,
        description="Build an animated camera from the briefing script's "
                    "cameraMove/cameraPan timeline and set it as the scene "
                    "camera (scene ready to play/render)")
    asset_root: StringProperty(
        name="Asset Root", default="", subtype='DIR_PATH',
        description="Optional: force the game data root (the folder holding "
                    "actors/, maps/, scripts/, data/) to grab actor/mocap "
                    "files from. Leave blank to auto-detect from the picked "
                    "map. Set this to point at another game's assets, e.g. the "
                    "XML1 'Decompiled Assets - PS2' folder")

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "apply_performances")
        sub = col.column()
        sub.enabled = self.apply_performances
        sub.prop(self, "root_motion")
        col.prop(self, "load_audio")
        col.prop(self, "load_camera")
        col.separator()
        col.prop(self, "asset_root")
        col.label(text="Blank = auto-detect from the map", icon='INFO')

    def execute(self, context):
        from .briefing_loader import load_briefing
        opts = {"apply_performances": self.apply_performances,
                "root_motion": self.root_motion,
                "audio": self.load_audio, "camera": self.load_camera,
                "game_dir": (self.asset_root.strip() or None),
                "operator": self}
        try:
            s = load_briefing(context, self.filepath, options=opts)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f"Briefing load failed: {exc}")
            return {'CANCELLED'}
        if not s:
            self.report({'ERROR'}, "Briefing load failed — see console")
            return {'CANCELLED'}
        nanim = sum(1 for _n, st in s["characters"] if "direct-applied" in st)
        nchar = sum(1 for _n, st in s["characters"]
                    if "direct-applied" in st or "placed" in st)
        end = s.get("frame_end", 0)
        self.report({'INFO'},
                    f"Loaded '{s['name']}': map={s['map']} chars={nchar}/"
                    f"{len(s['characters'])} animated={nanim}"
                    + (f", timeline 1..{end} — press Play/scrub to see the "
                       f"briefing" if end else ""))
        return {'FINISHED'}


def _side_counterpart_group(name):
    """'Bip01 L X' <-> 'Bip01 R X' / 'Bip01_L_X' <-> 'Bip01_R_X', else None."""
    for a, b in ((' L ', ' R '), ('_L_', '_R_')):
        if a in name:
            return name.replace(a, b, 1)
        if b in name:
            return name.replace(b, a, 1)
    return None


class ACTOR_OT_mirror_weights(Operator):
    """Mirror vertex weights from one side of the body to the other.

    Blender's built-in X-mirror can't flip game bone names (the L/R sits
    MID-name: 'Bip01 L UpperArm'), so weight-paint symmetry never finds the
    opposite group. This operator pairs vertices across the rig's own
    symmetry plane (auto-detected from the L/R bone heads) and copies each
    source-half vertex's weights onto its mirror partner with the group
    names side-swapped. Paint one side, mirror to the other"""
    bl_idname = "actor.mirror_weights"
    bl_label = "Mirror Weights"
    bl_options = {'REGISTER', 'UNDO'}

    direction: EnumProperty(
        name="Direction",
        items=[('L2R', "Left → Right", "Left half is the source"),
               ('R2L', "Right → Left", "Right half is the source")],
        default='L2R')
    tolerance: FloatProperty(
        name="Match Tolerance", default=0.02, min=0.0001, max=1.0,
        description="Max distance (as a fraction of the mesh size) between a "
                    "vertex and its mirrored position to count as a pair")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'MESH'
                and obj.parent is not None and obj.parent.type == 'ARMATURE')

    def execute(self, context):
        from mathutils import Vector, kdtree

        mesh_obj = context.active_object
        arm = mesh_obj.parent

        # --- symmetry plane from paired bone heads (world space) ---
        pairs = []
        for b in arm.data.bones:
            cp = _side_counterpart_group(b.name)
            if cp and cp in arm.data.bones and b.name < cp:
                hl = arm.matrix_world @ b.head_local
                hr = arm.matrix_world @ arm.data.bones[cp].head_local
                pairs.append((b.name, hl, hr))
        if not pairs:
            self.report({'ERROR'}, "No L/R bone pairs found on the armature")
            return {'CANCELLED'}
        # dominant flip axis + plane offset
        axis_votes = [0.0, 0.0, 0.0]
        offsets = []
        for _n, hl, hr in pairs:
            d = hl - hr
            for i in range(3):
                axis_votes[i] += abs(d[i])
        axis = axis_votes.index(max(axis_votes))
        for _n, hl, hr in pairs:
            offsets.append((hl[axis] + hr[axis]) * 0.5)
        plane = sum(offsets) / len(offsets)
        # which side of the plane is "L"?
        l_sign = 1.0 if (sum(hl[axis] for _n, hl, _hr in pairs)
                         / len(pairs)) >= plane else -1.0
        src_sign = l_sign if self.direction == 'L2R' else -l_sign

        # --- vertex world positions + KD tree over the SOURCE half only ---
        # (target verts PULL from their mirrored source position: game meshes
        # stack duplicate verts on UV/material seams, and a push-based write
        # to "the nearest target vert" reaches only one duplicate per spot)
        mw = mesh_obj.matrix_world
        verts = mesh_obj.data.vertices
        n = len(verts)
        wpos = [mw @ v.co for v in verts]

        import mathutils
        bb = [mw @ Vector(c) for c in mesh_obj.bound_box]
        size = max((max(p[i] for p in bb) - min(p[i] for p in bb))
                   for i in range(3))
        tol = max(1e-6, self.tolerance * size)
        eps = tol * 0.5

        src_idx = [i for i, p in enumerate(wpos)
                   if (p[axis] - plane) * src_sign > eps]
        if not src_idx:
            self.report({'ERROR'}, "No vertices found on the source half")
            return {'CANCELLED'}
        kd = kdtree.KDTree(len(src_idx))
        for i in src_idx:
            kd.insert(wpos[i], i)
        kd.balance()

        # --- group name maps ---
        groups = mesh_obj.vertex_groups
        gidx = {g.name: g.index for g in groups}

        def target_group_for(src_name):
            cp = _side_counterpart_group(src_name)
            return cp if cp else src_name   # center bones map to themselves

        # snapshot source weights (vertex -> [(gi, w)]) BEFORE writes
        vert_groups = {}
        for v in verts:
            vert_groups[v.index] = [(g.group, g.weight) for g in v.groups
                                    if g.weight > 1e-5]
        gname = {g.index: g.name for g in groups}

        # --- mirror: each TARGET-half vertex PULLS from its mirrored source ---
        written = 0
        no_partner = 0
        for j, p in enumerate(wpos):
            s = p[axis] - plane
            if s * src_sign >= -eps:     # not on the target half
                continue
            mp = p.copy()
            mp[axis] = plane - s         # reflect into the source half
            _co, i, dist = kd.find(mp)
            if i is None or dist > tol:
                no_partner += 1
                continue
            # clear this vert's existing weights, then copy side-swapped
            for g in groups:
                try:
                    g.remove([j])
                except RuntimeError:
                    pass
            for gi, w in vert_groups[i]:
                tname = target_group_for(gname[gi])
                tg = groups.get(tname)
                if tg is None:
                    tg = groups.new(name=tname)
                    gname[tg.index] = tname
                tg.add([j], w, 'REPLACE')
            written += 1

        axis_name = "XYZ"[axis]
        self.report({'INFO'},
                    f"Mirrored {written} verts "
                    f"({'L→R' if self.direction == 'L2R' else 'R→L'}, "
                    f"{axis_name}-plane at {plane:.2f}"
                    + (f", {no_partner} without a mirror partner"
                       if no_partner else "") + ")")
        return {'FINISHED'}


class ACTOR_OT_convert_normal_mua(Operator):
    """Convert this material's normal map to the MUA channel layout
    (X→alpha, Y→green — what the game's bump shader reads) as a new image
    named <name>_MUA. NOTE: the exporter already does this automatically on
    every MUA export, so a standard blue normal map works as-is — this
    operator is for previewing / saving the converted map"""
    bl_idname = "actor.convert_normal_mua"
    bl_label = "Convert Normal Map for MUA (preview)"
    bl_options = {'REGISTER', 'UNDO'}

    material_name: StringProperty()

    def execute(self, context):
        from ..exporter.skin_export import convert_normal_rgba_to_mua
        mat = bpy.data.materials.get(self.material_name)
        if mat is None:
            self.report({'ERROR'}, "Material not found")
            return {'CANCELLED'}
        node = _role_map_node(mat, 'normal')
        img = node.image if node is not None else None
        if img is None or img.size[0] == 0:
            self.report({'ERROR'}, "No normal map image on this material")
            return {'CANCELLED'}
        w, h = img.size
        px = [0.0] * (w * h * 4)
        img.pixels.foreach_get(px)
        src = bytearray(int(max(0.0, min(1.0, v)) * 255.0 + 0.5) for v in px)
        out = convert_normal_rgba_to_mua(src, normal_y_flip=True)
        name = f"{img.name.rsplit('.', 1)[0]}_MUA"
        new = bpy.data.images.get(name)
        if new is None or tuple(new.size) != (w, h):
            new = bpy.data.images.new(name, w, h, alpha=True)
        new.pixels.foreach_set([v / 255.0 for v in out])
        new.pack()
        self.report({'INFO'},
                    f"Created '{name}' (MUA layout: X→alpha, Y→green). "
                    f"Export already converts automatically — this is a "
                    f"preview copy.")
        return {'FINISHED'}


def _target_holder(context):
    """The object that stores the actor's target game: the active armature if
    there is one, else the active mesh (so a bare model can be tagged too)."""
    props = context.scene.igb_actor
    arm = (bpy.data.objects.get(props.active_armature)
           if props.active_armature else None)
    if arm is not None:
        return arm
    ao = context.active_object
    return ao if (ao is not None and ao.type in ('ARMATURE', 'MESH')) else None


def actor_target_game(context):
    """Resolve the loaded actor's target game ('XML2' / 'MUA'), default XML2."""
    holder = _target_holder(context)
    return str(holder.get("igb_target_game", "XML2")) if holder else "XML2"


class ACTOR_OT_set_target_game(Operator):
    """Switch the loaded actor's target game (XML2 / MUA) at will. Controls
    which material features show (MUA has normal/spec/gloss maps) and the
    format the exporter uses. Re-export to apply the new format in-game."""
    bl_idname = "actor.set_target_game"
    bl_label = "Set Target Game"
    bl_options = {'REGISTER', 'UNDO'}

    target: EnumProperty(
        name="Target",
        items=[('XML2', "XML2", "X-Men Legends 2 (v6 + CLUT, no normal maps)"),
               ('MUA', "MUA", "Marvel Ultimate Alliance (v4 + DXT5, "
                              "normal/specular/gloss maps)")],
        default='MUA',
    )

    @classmethod
    def poll(cls, context):
        return _target_holder(context) is not None

    def execute(self, context):
        holder = _target_holder(context)
        if holder is None:
            self.report({'ERROR'}, "No actor / mesh to set a target on")
            return {'CANCELLED'}
        holder["igb_target_game"] = self.target
        # Keep the display profile in sync so the Export dialog pre-selects the
        # same game (its invoke reads igb_target_game first now, but this keeps
        # the summary + any profile readers consistent).
        holder["igb_game_profile"] = ("Marvel: Ultimate Alliance (PC)"
                                      if self.target == 'MUA'
                                      else "X-Men Legends 2 (PC)")
        self.report({'INFO'},
                    f"Target set to {self.target} — re-export to apply the "
                    f"{'MUA (v4+DXT5)' if self.target == 'MUA' else 'XML2 (v6+CLUT)'} "
                    f"format in-game")
        return {'FINISHED'}


class ACTOR_OT_convert_skin_materials(Operator):
    """Convert ALL of this actor's skin materials to IGB materials for the
    current target — sets the render state AND adds the .IGB Material node
    (so you can add/edit Normal / Specular / Gloss maps on MUA)."""
    bl_idname = "actor.convert_skin_materials"
    bl_label = "Convert All to IGB"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        from .panels import _actor_material_meshes
        return bool(_actor_material_meshes(context))

    def execute(self, context):
        from ..panels import _convert_material_to_igb
        from ..utils.material_nodes import add_igb_node_to_material
        from .panels import _actor_material_meshes

        meshes = _actor_material_meshes(context)
        if not meshes:
            self.report({'ERROR'}, "No skin materials to convert")
            return {'CANCELLED'}

        seen = set()
        converted = 0
        for mo in meshes:
            for m in mo.data.materials:
                if m is None or m.name in seen or m.name == "IGB_Collision":
                    continue
                seen.add(m.name)
                if not m.use_nodes:
                    m.use_nodes = True
                # Render-state custom props (blend/alpha/color/lighting/cull).
                _convert_material_to_igb(m)
                # The .IGB Material node group (Base/Normal/Specular/Gloss
                # sockets) — what makes the map slots available to edit + export.
                try:
                    add_igb_node_to_material(m)
                except Exception:
                    pass
                converted += 1

        tgt = actor_target_game(context)
        self.report({'INFO'},
                    f"Converted {converted} material(s) to IGB ({tgt}). "
                    f"{'Normal/spec/gloss maps are now editable.' if tgt == 'MUA' else ''}")
        return {'FINISHED'}


def _connect_armature_bones(arm, frame_step=1, report=None):
    """Reorient bones tail->child (connected look) WITHOUT changing the mesh.

    The mesh deform of bone b is `deform_b = pose_b . rest_b^-1`. We capture it
    (per frame if animated), reorient the bones (which changes rest_b), then
    re-pose as `deform_b . new_rest_b` so the deform — and therefore the mesh —
    is preserved EXACTLY, while each bone sits at its reoriented (connected)
    frame. Proven mesh-invariant (max vertex delta ~3e-5). Returns (n_connected,
    n_total_children).
    """
    from mathutils import Matrix

    def horder():
        out = []
        def walk(b):
            out.append(b.name)
            for c in b.children:
                walk(c)
        for b in arm.data.bones:
            if b.parent is None:
                walk(b)
        return out
    order = horder()

    act = arm.animation_data.action if arm.animation_data else None
    # Frames to preserve: the action's keyed range (or just the current frame).
    # Cap the sample count so a ~2000-frame performance doesn't hang for a
    # minute (which reads as a freeze / "broke it"). ~800 samples is plenty for
    # a display re-bake; auto-widen the step for very long performances.
    MAX_SAMPLES = 800
    if act is not None:
        fr = act.frame_range
        span = int(fr[1]) - int(fr[0])
        step = max(1, int(frame_step))
        if span // step > MAX_SAMPLES:
            step = max(step, span // MAX_SAMPLES + 1)
        frames = list(range(int(fr[0]), int(fr[1]) + 1, step))
        if not frames:
            frames = [int(fr[0])]
    else:
        frames = [bpy.context.scene.frame_current]

    scene = bpy.context.scene
    saved = scene.frame_current

    # 1. capture deform_b per frame (armature space)
    deform = {f: {} for f in frames}
    for f in frames:
        scene.frame_set(f)
        bpy.context.view_layer.update()
        for pb in arm.pose.bones:
            b = arm.data.bones[pb.name]
            deform[f][pb.name] = pb.matrix @ b.matrix_local.inverted()

    # which bones carried a location track (keep re-keying those; root motion)
    loc_bones = set()
    if act is not None:
        for fc in act.fcurves:
            if fc.data_path.endswith(".location"):
                try:
                    loc_bones.add(fc.data_path.split('"')[1])
                except IndexError:
                    pass

    # 2. reorient: tail -> nearest child head (connect the chain)
    prev_mode = arm.mode
    bpy.ops.object.mode_set(mode='EDIT')
    ebs = arm.data.edit_bones
    child_of = {}
    for eb in ebs:
        if eb.parent:
            child_of.setdefault(eb.parent.name, []).append(eb.name)
    for eb in ebs:
        kids = child_of.get(eb.name)
        if not kids:
            continue
        ch = min(kids, key=lambda k: (ebs[k].head - eb.head).length)
        chead = ebs[ch].head.copy()
        if (chead - eb.head).length > 1e-4:
            eb.tail = chead
    # NOTE: do NOT set use_connect — a connected bone locks its head to the
    # parent tail and IGNORES its location channel, which the re-pose needs
    # (reorienting shifts each bone's rest-relative offset). The tail-at-child-
    # head already gives the visually-connected look.
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.view_layer.update()

    # 3. re-pose: for each frame the desired armature-space pose of bone b is
    #    target_b = deform_b . new_rest_b (preserves the deform => mesh). Set it
    #    with pose_bone.matrix (hierarchy order) and capture with Blender's own
    #    keyframe_insert — reliable; a manual fcurve .co bake evaluates wrong.
    if act is not None:
        # Compute each bone's matrix_basis ANALYTICALLY (fast; verified to match
        # Blender's own pose_bone.matrix setter). target_b = deform_b.new_rest_b.
        rest_rel = {}
        for b in arm.data.bones:
            rest_rel[b.name] = (b.parent.matrix_local.inverted() @ b.matrix_local
                                if b.parent else b.matrix_local.copy())
        keys = {name: [] for name in order}   # name -> [(f, quat, loc)]
        for f in frames:
            computed = {}
            for name in order:
                b = arm.data.bones[name]
                target = deform[f][name] @ b.matrix_local
                computed[name] = target
                base = (computed[b.parent.name] @ rest_rel[name]
                        if b.parent is not None else rest_rel[name])
                basis = base.inverted() @ target
                keys[name].append((f, basis.to_quaternion(),
                                   basis.to_translation()))

        # Rebuild the action with keyframe_insert (reliable — a manual fcurve
        # .co bake evaluates wrong). Set each bone's basis then insert it with
        # NO depsgraph update in the loop, so the accumulating action can't
        # re-apply partial keys over the basis we're capturing.
        for pb in arm.pose.bones:
            pb.rotation_mode = 'QUATERNION'
        meta = {k: act[k] for k in act.keys()}
        oldname = act.name
        arm.animation_data.action = None
        bpy.data.actions.remove(act)
        newact = bpy.data.actions.new(oldname)
        for k, v in meta.items():
            newact[k] = v
        arm.animation_data.action = newact
        nfr = len(frames)
        for fi in range(nfr):
            for name in order:
                f, q, l = keys[name][fi]
                pb = arm.pose.bones[name]
                pb.rotation_quaternion = q
                pb.location = l
                pb.keyframe_insert("rotation_quaternion", frame=f, group=name)
                pb.keyframe_insert("location", frame=f, group=name)
    else:
        # static rig: at rest the deform is identity, so reorienting alone left
        # the mesh unchanged — nothing to re-pose.
        pass

    scene.frame_set(saved)
    bpy.context.view_layer.update()

    n_conn = sum(1 for b in arm.data.bones
                 if b.parent and (b.head_local - b.parent.tail_local).length < 0.5)
    n_tot = sum(1 for b in arm.data.bones if b.parent)
    arm["igb_bones_connected"] = True
    return n_conn, n_tot


class ACTOR_OT_connect_bones(Operator):
    """Reorient the active rig's bones so they connect head-to-tail (the clean
    'connected' look) without changing the mesh or the animation. If the rig has
    an active performance it is re-baked to stay pixel-identical. Note: this
    reorients the skeleton for VIEWING/EDITING — re-import the model for a clean
    export rig"""
    bl_idname = "actor.connect_bones"
    bl_label = "Connect Bones (display)"
    bl_options = {'REGISTER', 'UNDO'}

    frame_step: IntProperty(
        name="Frame Step", default=1, min=1, max=10,
        description="Sample every Nth frame when re-baking an animation "
                    "(higher = faster, less precise)")

    @classmethod
    def poll(cls, context):
        ao = context.active_object
        if ao is not None and (ao.type == 'ARMATURE'
                               or (ao.parent and ao.parent.type == 'ARMATURE')):
            return True
        props = context.scene.igb_actor
        return bool(props.active_armature
                    and props.active_armature in bpy.data.objects)

    def execute(self, context):
        props = context.scene.igb_actor
        # Prefer the armature the user actually SELECTED (viewport), then a
        # selected child's parent, then the addon's active_armature. Using the
        # stale active_armature first would connect the wrong character in a
        # multi-actor scene (e.g. a briefing).
        arm = None
        ao = context.active_object
        if ao is not None:
            if ao.type == 'ARMATURE':
                arm = ao
            elif ao.parent is not None and ao.parent.type == 'ARMATURE':
                arm = ao.parent
        if arm is None:
            for o in context.selected_objects:
                if o.type == 'ARMATURE':
                    arm = o
                    break
                if o.parent is not None and o.parent.type == 'ARMATURE':
                    arm = o.parent
                    break
        if arm is None and props.active_armature:
            arm = bpy.data.objects.get(props.active_armature)
        if arm is None or arm.type != 'ARMATURE':
            self.report({'ERROR'}, "Select the mocap model (its armature or "
                        "mesh) in the viewport first")
            return {'CANCELLED'}
        # make it active for edit-mode ops
        context.view_layer.objects.active = arm
        try:
            nc, nt = _connect_armature_bones(arm, frame_step=self.frame_step,
                                             report=self)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f"Connect bones failed: {exc}")
            return {'CANCELLED'}
        self.report({'INFO'},
                    f"Connected {nc}/{nt} bones on {arm.name} "
                    f"(mesh + animation preserved). Branch bones stay split.")
        return {'FINISHED'}


class ACTOR_OT_load_mocap_performances(Operator, ImportHelper):
    """Direct-apply every performance from a zone mocap file onto the active
    65-bone mocap model — no retarget. Each performance becomes an Action;
    pick one from the Action editor. Use on a mocap model (skin 200xx) whose
    skeleton matches the zone file"""
    bl_idname = "actor.load_mocap_performances"
    bl_label = "Load Performances onto Model"
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = ".igb"
    filter_glob: StringProperty(default="*.igb", options={'HIDDEN'})

    strip_root_motion: BoolProperty(
        name="Strip Root Motion", default=True,
        description="Drop the performance's root translation so the model "
                    "stays in place (pose only)")

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        arm = (bpy.data.objects.get(props.active_armature)
               if props.active_armature else None)
        return arm is not None and arm.type == 'ARMATURE'

    def execute(self, context):
        from ..igb_format.igb_reader import IGBReader
        from .sg_skeleton import extract_skeleton
        from .sg_animation import extract_animations
        from .animation_builder import build_action

        props = context.scene.igb_actor
        tgt = bpy.data.objects.get(props.active_armature)
        if tgt is None or tgt.type != 'ARMATURE':
            self.report({'ERROR'}, "Load / select a mocap model first "
                        "(its armature must be the active actor)")
            return {'CANCELLED'}

        # Parse the zone performances.
        try:
            r = IGBReader(self.filepath)
            r.read()
            skel = extract_skeleton(r)
            anims = extract_animations(r, skel) if skel else []
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f"Could not read performances: {exc}")
            return {'CANCELLED'}
        if not anims:
            self.report({'ERROR'}, "No performances found in file")
            return {'CANCELLED'}

        # Ref armature from the zone file (rebases the unanimated root).
        try:
            ref = _build_anim_ref_armature(context, self.filepath)
        except Exception:
            ref = None

        n = 0
        first = None
        try:
            for pa in anims:
                act = build_action(tgt, pa, bind_pose=None,
                                   anim_armature=ref,
                                   rebase_unanimated_parents=ref is not None,
                                   relative_translation=True,
                                   root_motion_mode=('vertical'
                                                     if self.strip_root_motion
                                                     else 'full'))
                if act is None:
                    continue
                if first is None:
                    first = act
                n += 1
        finally:
            if ref is not None:
                _remove_anim_ref_armature(ref)

        if first is not None:
            if tgt.animation_data is None:
                tgt.animation_data_create()
            tgt.animation_data.action = first
            # extend the timeline to the performance + jump to a posed frame,
            # else the default 1..250 range hides most of the ~2000f perf.
            try:
                end = int(first.frame_range[1])
                if end > 1:
                    context.scene.frame_start = 1
                    context.scene.frame_end = end
                    context.scene.frame_set(max(1, min(end, int(end * 0.35))))
            except Exception:
                pass
        self.report({'INFO'},
                    f"Applied {n} performance(s) onto {tgt.name} (direct). "
                    f"Pick one in the Action editor; timeline set to the "
                    f"performance length.")
        return {'FINISHED'}


class ACTOR_OT_export_anim_from_scratch(Operator, ExportHelper):
    """Export current animation as a new IGB file (from scratch, no template needed)"""
    bl_idname = "actor.export_anim_scratch"
    bl_label = "Export Animation (New IGB)"
    bl_options = {'REGISTER'}

    filename_ext = ".igb"

    filter_glob: StringProperty(
        default="*.igb",
        options={'HIDDEN'},
    )

    anim_name: StringProperty(
        name="Animation Name",
        description="Name for the animation in the IGB file",
        default="idle",
    )

    game_preset: EnumProperty(
        name="Game",
        items=[
            ('xml2', "XML2", "X-Men Legends II"),
            ('mua', "MUA", "Marvel Ultimate Alliance"),
        ],
        default='xml2',
    )

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        if not props.active_armature:
            return False
        armature_obj = bpy.data.objects.get(props.active_armature)
        if armature_obj is None:
            return False
        return (armature_obj.animation_data is not None and
                armature_obj.animation_data.action is not None)

    def execute(self, context):
        from .vmc_bridge import export_action_as_igb

        props = context.scene.igb_actor
        armature_obj = bpy.data.objects.get(props.active_armature)
        if armature_obj is None:
            self.report({'ERROR'}, "No active armature found")
            return {'CANCELLED'}

        action = armature_obj.animation_data.action
        if action is None:
            self.report({'ERROR'}, "No active animation on armature")
            return {'CANCELLED'}

        fps = context.scene.render.fps

        # Find a reference IGB for schema
        ref_path = armature_obj.get("igb_anim_file", "")
        if not ref_path or not os.path.exists(ref_path):
            self.report({'ERROR'},
                        "No reference IGB file found. Import an actor first "
                        "to provide the IGB schema.")
            return {'CANCELLED'}

        try:
            result = export_action_as_igb(
                armature_obj, action, self.filepath,
                game=self.game_preset,
                reference_path=ref_path,
                anim_name=self.anim_name,
                fps=fps,
            )
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f"Export failed: {exc}")
            return {'CANCELLED'}

        if result:
            size = os.path.getsize(self.filepath)
            self.report({'INFO'},
                        f"Exported '{self.anim_name}' to "
                        f"{os.path.basename(self.filepath)} ({size} bytes)")
            return {'FINISHED'}
        else:
            self.report({'ERROR'}, "Export failed — check console for details")
            return {'CANCELLED'}

    def invoke(self, context, event):
        props = context.scene.igb_actor
        armature_obj = bpy.data.objects.get(props.active_armature)
        if armature_obj and armature_obj.animation_data:
            action = armature_obj.animation_data.action
            if action:
                # Use the action's IGB name if available
                igb_name = action.get("igb_anim_name", "")
                if igb_name:
                    self.anim_name = igb_name
                else:
                    self.anim_name = action.name

                # Default output path: alongside the original anim file
                ref_path = armature_obj.get("igb_anim_file", "")
                if ref_path and os.path.exists(ref_path):
                    dirname = os.path.dirname(ref_path)
                    safe_name = self.anim_name.replace(" ", "_")
                    self.filepath = os.path.join(dirname,
                                                 f"{safe_name}_custom.igb")

        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class ACTOR_OT_convert_animation(Operator, ExportHelper):
    """Convert animation from any rig to XML2/MUA IGB (non-destructive)"""
    bl_idname = "actor.convert_animation"
    bl_label = "Convert Animation to IGB"
    bl_options = {'REGISTER'}

    filename_ext = ".igb"

    filter_glob: StringProperty(
        default="*.igb",
        options={'HIDDEN'},
    )

    anim_name: StringProperty(
        name="Animation Name",
        description="Name for the animation in the IGB file",
        default="idle",
    )

    game_preset: EnumProperty(
        name="Game",
        items=[
            ('xml2', "XML2", "X-Men Legends II"),
            ('mua', "MUA", "Marvel Ultimate Alliance"),
        ],
        default='xml2',
    )

    reference_igb: StringProperty(
        name="Reference IGB",
        description="Path to any game animation IGB (for format schema)",
        subtype='FILE_PATH',
    )

    scale_mode: EnumProperty(
        name="Scale",
        items=[
            ('AUTO', "Auto-detect", "Auto-detect from skeleton proportions"),
            ('CUSTOM', "Custom", "Use a custom scale factor"),
        ],
        default='AUTO',
    )

    custom_scale: FloatProperty(
        name="Scale Factor",
        description="Custom scale multiplier (source * factor = game units)",
        default=1.0,
        min=0.001,
        max=10000.0,
    )

    sample_rate: IntProperty(
        name="Sample Rate",
        description="Samples per second for animation baking",
        default=30,
        min=1,
        max=120,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'ARMATURE'
                and obj.animation_data is not None
                and obj.animation_data.action is not None)

    def execute(self, context):
        from .anim_converter import convert_animation

        obj = context.active_object
        action = obj.animation_data.action

        # Resolve reference IGB
        ref_path = self.reference_igb
        if not ref_path or not os.path.exists(ref_path):
            ref_path = obj.get("igb_anim_file", "")
        if not ref_path or not os.path.exists(ref_path):
            # Try to find any IGB in the game actors folder
            self.report({'ERROR'},
                        "Reference IGB required. Provide a path to any game "
                        "animation .igb file for format schema extraction.")
            return {'CANCELLED'}

        scale = None if self.scale_mode == 'AUTO' else self.custom_scale

        try:
            result = convert_animation(
                context, obj, action, self.filepath,
                game=self.game_preset,
                reference_path=ref_path,
                anim_name=self.anim_name,
                scale_factor=scale,
                sample_rate=self.sample_rate,
            )
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f"Conversion failed: {exc}")
            return {'CANCELLED'}

        size = os.path.getsize(self.filepath)
        self.report({'INFO'},
                    f"Converted '{self.anim_name}' to "
                    f"{os.path.basename(self.filepath)} ({size} bytes)")
        return {'FINISHED'}

    def invoke(self, context, event):
        obj = context.active_object
        if obj and obj.animation_data and obj.animation_data.action:
            action = obj.animation_data.action
            self.anim_name = action.get("igb_anim_name", action.name)

            # Default output path
            ref_path = obj.get("igb_anim_file", "")
            if ref_path and os.path.exists(ref_path):
                self.reference_igb = ref_path
                dirname = os.path.dirname(ref_path)
                safe_name = self.anim_name.replace(" ", "_")
                self.filepath = os.path.join(dirname, f"{safe_name}_conv.igb")

        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class ACTOR_OT_scale_anim_set(Operator, ImportHelper):
    """Scale a character's animation IGB for a resized character.

    Animations drive the root bones with ABSOLUTE heights authored for
    the standard skeleton — a resized skin alone always floats or sinks.
    This rewrites the character's anim set (e.g. 15_xxx.igb) at your
    scale: every translation (skeleton, keyframes, root motion) is
    multiplied; rotations are untouched. Deploy the output alongside
    your resized skin and use the same factor for both.
    """
    bl_idname = "actor.scale_anim_set"
    bl_label = "Scale Animation Set (.igb)"
    bl_options = {'REGISTER'}

    filename_ext = ".igb"
    filter_glob: StringProperty(default="*.igb", options={'HIDDEN'})

    scale_factor: FloatProperty(
        name="Scale Factor",
        description="Uniform character scale (match your skin's resize, "
                    "e.g. 0.65 for a 65% character)",
        default=0.65,
        min=0.05,
        max=5.0,
    )

    game: EnumProperty(
        name="Game",
        items=[('xml2', "XML2", ""), ('mua', "MUA", "")],
        default='xml2',
    )

    def execute(self, context):
        from .anim_scale import scale_anim_file

        src = self.filepath
        base, ext = os.path.splitext(src)
        out = f"{base}_scaled{ext}"
        try:
            ok, message, skipped = scale_anim_file(
                src, out, self.scale_factor, game=self.game)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f"Scaling failed: {exc}")
            return {'CANCELLED'}

        if not ok:
            self.report({'ERROR'}, message)
            return {'CANCELLED'}
        self.report({'INFO'}, message)
        if skipped:
            print("[Scale Animation Set] unscaled blobs:", skipped)
        return {'FINISHED'}


class ACTOR_OT_fix_menu_anims(Operator, ImportHelper):
    """Fix character-select float for resized characters.

    The select screen applies the Pelvis animation track literally
    (explicit zeros override the skin's offset), while gameplay treats
    them as no-data — so a resized character grounds in-game but floats
    in the menu. This bakes the skin's Pelvis offset into the MENU
    animations (menu_idle/menu_action/menu_goodbye) of the character's
    anim file. Menu-only animations: zero gameplay impact.

    Patches the file IN PLACE (a .bak backup is written first). Select
    the resized armature beforehand so the offset is read automatically.
    """
    bl_idname = "actor.fix_menu_anims"
    bl_label = "Fix Menu Float (Anim File)"
    bl_options = {'REGISTER'}

    filename_ext = ".igb"
    filter_glob: StringProperty(default="*.igb", options={'HIDDEN'})

    pelvis_offset: FloatProperty(
        name="Pelvis Offset (game units)",
        description="The skin's Pelvis Z offset. 0 = read automatically "
                    "from the active armature's stored skeleton data",
        default=0.0,
        min=-60.0,
        max=0.0,
    )

    game: EnumProperty(
        name="Game",
        items=[('xml2', "XML2", ""), ('mua', "MUA", "")],
        default='xml2',
    )

    def execute(self, context):
        import json as _json
        from .anim_scale import fix_menu_pelvis
        from .rig_converter import _total_export_scale

        offset = self.pelvis_offset
        if abs(offset) < 1e-6:
            # Auto-read from the active armature's stored skeleton data
            obj = context.active_object
            if (obj is None or obj.type != 'ARMATURE'
                    or "igb_skin_bone_translations" not in obj):
                self.report({'ERROR'},
                            "Set Pelvis Offset manually, or select the "
                            "resized armature first for auto-detect")
                return {'CANCELLED'}
            try:
                translations = _json.loads(
                    obj["igb_skin_bone_translations"])
                offset = translations[2][2] * _total_export_scale(obj)
            except Exception as exc:
                self.report({'ERROR'}, f"Could not read offset: {exc}")
                return {'CANCELLED'}
            if abs(offset) < 1e-4:
                self.report({'WARNING'},
                            "Armature's Pelvis offset is 0 — character "
                            "is standard size, nothing to fix")
                return {'CANCELLED'}

        try:
            ok, message = fix_menu_pelvis(self.filepath, offset,
                                          game=self.game)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f"Patch failed: {exc}")
            return {'CANCELLED'}

        self.report({'INFO'} if ok else {'ERROR'}, message)
        return {'FINISHED'} if ok else {'CANCELLED'}


class ACTOR_OT_apply_pose_resize(Operator):
    """Apply your Pose Mode scaling as the new shape and rebuild the
    export data so everything stays consistent.

    The proven resize recipe, one click: in Pose Mode, scale
    'Bip01 Pelvis' to resize the body (Bip01 keeps its size), then run
    this. The pose is baked into the meshes (shape keys included),
    applied as the new rest pose, and ALL export data (bone translations
    + inverse binds) is rebuilt to match — so the arms don't break.
    """
    bl_idname = "actor.apply_pose_resize"
    bl_label = "Apply Pose Scale (Resize)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'ARMATURE'
                and "igb_skin_bone_translations" in obj)

    def execute(self, context):
        from .rig_converter import apply_pose_and_rebuild

        success, message = apply_pose_and_rebuild(context.active_object)
        if success:
            self.report({'INFO'}, message)
            return {'FINISHED'}
        self.report({'ERROR'}, message)
        return {'CANCELLED'}


class ACTOR_OT_fix_bip01(Operator):
    """Re-anchor Bip01/Motion to native game values against the CURRENT
    armature scale.

    Workflow: scale the armature object in Object Mode to resize your
    character, then click this — the body keeps your size while the root
    bones export at exact native heights, so feet stay planted and
    animations behave. Safe to run after every scale tweak (idempotent).
    """
    bl_idname = "actor.fix_bip01"
    bl_label = "Rebuild Export Data"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'ARMATURE'
                and "igb_skin_bone_translations" in obj)

    def execute(self, context):
        from .rig_converter import fix_root_translations

        success, message = fix_root_translations(context.active_object)
        if success:
            self.report({'INFO'}, message)
            return {'FINISHED'}
        self.report({'ERROR'}, message)
        return {'CANCELLED'}


class ACTOR_OT_show_all_skins(Operator):
    """Show every mesh in the skins list (undo a Solo)"""
    bl_idname = "actor.show_all_skins"
    bl_label = "Show All Skins"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return len(context.scene.igb_actor.skins) > 0

    def execute(self, context):
        props = context.scene.igb_actor
        shown = 0
        for item in props.skins:
            obj = bpy.data.objects.get(item.object_name)
            if obj is not None:
                obj.hide_viewport = False
                obj.hide_render = False
                shown += 1
            item.is_visible = True
        self.report({'INFO'}, f"Showing {shown} skin mesh(es)")
        return {'FINISHED'}


def _igb_group_node(mat):
    """Return the ".IGB Material" group node of a material, or None."""
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return None
    return next((n for n in mat.node_tree.nodes
                 if n.type == 'GROUP' and n.node_tree is not None
                 and n.node_tree.name == '.IGB Material'), None)


def _role_map_node(mat, role):
    """Return the Image Texture node feeding the group's Normal/Specular input.

    Mirrors how the exporter (_find_role_image) reads it, so the per-material
    picker and the export agree.
    """
    grp = _igb_group_node(mat)
    if grp is None:
        return None
    if role in ('diffuse', 'base'):
        inp = grp.inputs.get('Base Texture')
        if (inp and inp.is_linked
                and inp.links[0].from_node.type == 'TEX_IMAGE'):
            return inp.links[0].from_node
        return None
    if role == 'normal':
        inp = grp.inputs.get('Normal')
        if inp and inp.is_linked:
            src = inp.links[0].from_node
            if src.type == 'NORMAL_MAP':
                col = src.inputs.get('Color')
                if (col and col.is_linked
                        and col.links[0].from_node.type == 'TEX_IMAGE'):
                    return col.links[0].from_node
            elif src.type == 'TEX_IMAGE':
                return src
    elif role == 'gloss':
        inp = grp.inputs.get('Gloss/Mask Texture')
        if (inp and inp.is_linked
                and inp.links[0].from_node.type == 'TEX_IMAGE'):
            return inp.links[0].from_node
    else:  # specular
        inp = grp.inputs.get('Specular Texture')
        if (inp and inp.is_linked
                and inp.links[0].from_node.type == 'TEX_IMAGE'):
            return inp.links[0].from_node
    return None


class ACTOR_OT_add_material_map(Operator):
    """Add a Normal/Specular map slot to this material, then pick its image.

    Exported when 'Use Normal Maps' is on (3ds Max v4 + DXT5 MUA)."""
    bl_idname = "actor.add_material_map"
    bl_label = "Add Map"
    bl_options = {'REGISTER', 'UNDO'}

    material_name: StringProperty()
    role: EnumProperty(items=[('diffuse', "Base Color", ""),
                              ('normal', "Normal", ""),
                              ('specular', "Specular", ""),
                              ('gloss', "Gloss/Mask", "")])

    # Which group-node input each role feeds, and the vertical offset used
    # to lay the new Image Texture node out tidily in the shader editor.
    _ROLE_SOCKET = {'diffuse': 'Base Texture',
                    'normal': 'Normal',
                    'specular': 'Specular Texture',
                    'gloss': 'Gloss/Mask Texture'}
    _ROLE_OFFSET = {'diffuse': 80, 'normal': 250, 'specular': 420,
                    'gloss': 590}

    def execute(self, context):
        mat = bpy.data.materials.get(self.material_name)
        grp = _igb_group_node(mat)
        if grp is None:
            self.report({'ERROR'},
                        "Material has no IGB Material node — use 'Convert "
                        "Materials to IGB' first")
            return {'CANCELLED'}
        if _role_map_node(mat, self.role) is not None:
            return {'FINISHED'}
        # Validate the target socket BEFORE creating any node, so a stale node
        # group (missing the socket) can't leave an orphaned, unwired image.
        target_sock = grp.inputs.get(self._ROLE_SOCKET[self.role])
        if target_sock is None:
            self.report({'ERROR'},
                        f"'{self._ROLE_SOCKET[self.role]}' input is missing on "
                        f"the .IGB Material node — re-run 'Convert Materials to "
                        f"IGB' to update it")
            return {'CANCELLED'}
        nt = mat.node_tree
        tex = nt.nodes.new('ShaderNodeTexImage')
        tex.label = ("Base Color" if self.role == 'diffuse'
                     else self.role.capitalize() + " Map")
        try:
            tex.image = None
        except Exception:
            pass
        y = grp.location.y - self._ROLE_OFFSET.get(self.role, 250)
        tex.location = (grp.location.x - 650, y)
        if self.role == 'normal':
            nm = nt.nodes.new('ShaderNodeNormalMap')
            nm.location = (grp.location.x - 320, y)
            nt.links.new(tex.outputs['Color'], nm.inputs['Color'])
            nt.links.new(nm.outputs['Normal'], target_sock)
        else:
            nt.links.new(tex.outputs['Color'], target_sock)
        self.report({'INFO'},
                    f"Added {self.role} map slot to {mat.name} — pick an image")
        return {'FINISHED'}


class ACTOR_OT_clear_material_map(Operator):
    """Remove this material's Base Color / Normal / Specular / Gloss map node."""
    bl_idname = "actor.clear_material_map"
    bl_label = "Remove Map"
    bl_options = {'REGISTER', 'UNDO'}

    material_name: StringProperty()
    role: EnumProperty(items=[('diffuse', "Base Color", ""),
                              ('normal', "Normal", ""),
                              ('specular', "Specular", ""),
                              ('gloss', "Gloss/Mask", "")])

    def execute(self, context):
        mat = bpy.data.materials.get(self.material_name)
        tex = _role_map_node(mat, self.role)
        if tex is None:
            return {'FINISHED'}
        nt = mat.node_tree
        # For normals, also remove the intervening Normal Map node.
        to_remove = [tex]
        for out in tex.outputs:
            for link in out.links:
                if link.to_node.type == 'NORMAL_MAP':
                    to_remove.append(link.to_node)
        for n in to_remove:
            try:
                nt.nodes.remove(n)
            except Exception:
                pass
        self.report({'INFO'}, f"Removed {self.role} map from {mat.name}")
        return {'FINISHED'}


class ACTOR_OT_select_material(Operator):
    """Select which material on the skin to edit in the Materials panel."""
    bl_idname = "actor.select_material"
    bl_label = "Select Material"
    bl_options = {'INTERNAL'}

    material_name: StringProperty()

    def execute(self, context):
        context.scene.igb_actor.active_material_name = self.material_name
        return {'FINISHED'}


class ACTOR_OT_toggle_segment_collapse(Operator):
    """Collapse / expand a segment's details in the Segments panel."""
    bl_idname = "actor.toggle_segment_collapse"
    bl_label = "Toggle Segment"
    bl_options = {'INTERNAL'}

    segment_name: StringProperty()

    def execute(self, context):
        props = context.scene.igb_actor
        arm = bpy.data.objects.get(props.active_armature)
        if arm is None:
            return {'CANCELLED'}
        # "__body__" sentinel for the unnamed Body group — keep this in sync
        # with the read site in panels.py (_seg_collapse_key).
        key = "igb_segcollapse:" + (self.segment_name or "__body__")
        arm[key] = not arm.get(key, False)
        return {'FINISHED'}


class ACTOR_OT_import_game_lights(Operator, ImportHelper):
    """Create Blender lights from a game scene IGB so your skin previews under
    real game lighting. Actor IGBs have no lights — point this at a map
    (maps/act1/.../*.igb) for in-level lighting, or maps/menu/main_back.igb for
    the menu rig."""
    bl_idname = "actor.import_game_lights"
    bl_label = "Import Game Lighting"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".igb"
    filter_glob: StringProperty(default="*.igb", options={'HIDDEN'})
    point_energy: FloatProperty(name="Point/Spot Energy", default=400.0,
                                min=0.0, soft_max=5000.0)
    sun_strength: FloatProperty(name="Sun Strength", default=3.0,
                                min=0.0, soft_max=50.0)
    set_world_ambient: BoolProperty(name="Set World Ambient", default=True)

    def invoke(self, context, event):
        props = context.scene.igb_actor
        if props.game_dir and not self.filepath:
            cand = os.path.join(props.game_dir, "maps", "menu", "main_back.igb")
            if os.path.exists(cand):
                self.filepath = cand
        return super().invoke(context, event)

    def execute(self, context):
        from ..importer.light_importer import import_lights_to_blender
        try:
            created, total, amb = import_lights_to_blender(
                self.filepath, point_energy=self.point_energy,
                sun_strength=self.sun_strength)
        except Exception as e:
            self.report({'ERROR'}, f"Light import failed: {e}")
            return {'CANCELLED'}
        if created == 0:
            self.report({'WARNING'},
                        "No lights in that IGB — actor files have none. Try a "
                        "map .igb or maps/menu/main_back.igb")
            return {'CANCELLED'}
        if self.set_world_ambient and amb is not None:
            world = context.scene.world or bpy.data.worlds.new("IGB World")
            context.scene.world = world
            world.use_nodes = True
            bg = world.node_tree.nodes.get('Background')
            if bg is not None:
                bg.inputs[0].default_value = (amb[0], amb[1], amb[2], 1.0)
                bg.inputs[1].default_value = 1.0
        self.report({'INFO'},
                    f"Imported {created} game light(s) into the 'IGB Game "
                    f"Lights' collection")
        return {'FINISHED'}


class ACTOR_OT_character_preview_lights(Operator):
    """Build a 3-point key/fill/rim SUN rig tuned to show off a skin's
    normal / specular / gloss maps. Replaces the 'IGB Game Lights' collection.
    Switch the viewport to Material Preview or Rendered shading to see it."""
    bl_idname = "actor.character_preview_lights"
    bl_label = "Character Preview Lighting"
    bl_options = {'REGISTER', 'UNDO'}

    energy_scale: FloatProperty(name="Brightness", default=1.0, min=0.0,
                                soft_max=4.0,
                                description="Scale all three lamps together")
    set_world_ambient: BoolProperty(name="Set World Ambient", default=True)

    def execute(self, context):
        from ..importer.light_importer import build_character_preview_rig
        try:
            n = build_character_preview_rig(
                set_world_ambient=self.set_world_ambient,
                energy_scale=self.energy_scale)
        except Exception as e:
            self.report({'ERROR'}, f"Preview rig failed: {e}")
            return {'CANCELLED'}
        self.report({'INFO'},
                    f"Built {n}-point character preview rig in 'IGB Game Lights'")
        return {'FINISHED'}


_classes = (
    ACTOR_OT_import_game_lights,
    ACTOR_OT_character_preview_lights,
    ACTOR_OT_add_material_map,
    ACTOR_OT_clear_material_map,
    ACTOR_OT_select_material,
    ACTOR_OT_toggle_segment_collapse,
    ACTOR_OT_fix_menu_anims,
    ACTOR_OT_scale_anim_set,
    ACTOR_OT_apply_pose_resize,
    ACTOR_OT_fix_bip01,
    ACTOR_OT_import_actor,
    ACTOR_OT_import_skin,
    ACTOR_OT_import_animations,
    ACTOR_OT_pick_animation,
    ACTOR_OT_remove_animation,
    ACTOR_OT_select_skins,
    ACTOR_OT_toggle_skin,
    ACTOR_OT_solo_skin,
    ACTOR_OT_show_all_skins,
    ACTOR_OT_set_animation,
    ACTOR_OT_rest_pose,
    ACTOR_OT_play_animation,
    ACTOR_OT_set_target_game,
    ACTOR_OT_convert_skin_materials,
    ACTOR_OT_export_skin,
    ACTOR_OT_export_mannequin,
    ACTOR_OT_export_animations,
    ACTOR_OT_replace_animation,
    ACTOR_OT_ground_preview,
    ACTOR_OT_setup_skin,
    ACTOR_OT_add_mesh_as_skin,
    ACTOR_OT_remove_skin,
    ACTOR_OT_refresh_segments,
    ACTOR_OT_add_segment,
    ACTOR_OT_remove_segment,
    ACTOR_OT_rename_segment,
    ACTOR_OT_select_segment,
    ACTOR_OT_toggle_segment,
    ACTOR_OT_setup_vmc4b,
    ACTOR_OT_cleanup_vmc,
    ACTOR_OT_vr_retarget,
    ACTOR_OT_import_mocap,
    ACTOR_OT_retarget_mocap,
    ACTOR_OT_export_mocap,
    ACTOR_OT_load_mocap_performances,
    ACTOR_OT_convert_normal_mua,
    ACTOR_OT_mirror_weights,
    ACTOR_OT_connect_bones,
    ACTOR_OT_mirror_pose,
    ACTOR_OT_load_briefing,
    ACTOR_OT_export_anim_from_scratch,
    ACTOR_OT_convert_animation,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
