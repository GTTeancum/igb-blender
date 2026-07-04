"""IGB Actors N-panel for the 3D Viewport sidebar.

One sidebar tab ("IGB Actors") with a persistent Active-Actor banner at the top
and collapsible sections below, in order:
- Quick Tools (the Import -> Setup -> Resize -> Export workflow)
- Import, Skins (+ Segments), Animations, Materials
- Information: plain-language docs for every part of the panel + a full
  reference of what each engine bone does
- Extras: the MUA Animated Mannequin export, VR Motion Capture, and the
  Animation Converter (each a sub-section under Extras)
"""

import bpy
from bpy.types import Panel, UIList
from .. import _get_icon_id


# ---------------------------------------------------------------------------
# Bone reference (Information tab) — what every engine bone does.
# Grouped (category, [(bone, description), ...]).
# ---------------------------------------------------------------------------
_BONE_REFERENCE = (
    ("Root & core", (
        ("Bone_000", "Absolute scene root above Bip01. Anchors the character's "
                     "world transform — without it the rig can float in menus."),
        ("Bip01", "The master root. The engine parents the whole character to "
                  "it and reads facing / position from here."),
        ("Bip01 Pelvis", "The hips — first real body bone. Head tracking and "
                         "optic beams need it placed right. Scale this in Pose "
                         "Mode to resize the whole body."),
        ("Bip01 Spine / Spine1 / Spine2", "The torso chain from hips to chest."),
        ("Bip01 Neck / Neck1", "The neck, between chest and head."),
        ("Bip01 Head", "The head. Drives face tracking and aim."),
        ("Motion", "Carries locomotion (walk / run) displacement. Animations "
                   "move this bone so the character actually travels."),
        ("Footsteps", "Marker at the feet used for footstep FX / IK."),
    )),
    ("Arms (L / R)", (
        ("Bip01 L/R Clavicle", "The collarbone / shoulder root."),
        ("Bip01 L/R UpperArm", "The upper arm (shoulder to elbow)."),
        ("Bip01 L/R Forearm", "The forearm (elbow to wrist)."),
        ("Bip01 L/R Hand", "The hand / wrist; parent of the fingers."),
    )),
    ("Hands — fingers (L / R)", (
        ("Bip01 L/R Finger0, Finger01", "Thumb: proximal then intermediate."),
        ("Bip01 L/R Finger1, Finger11", "Index finger segments."),
        ("Bip01 L/R Finger2..Finger4", "Middle / ring / pinky segments."),
    )),
    ("Legs (L / R)", (
        ("Bip01 L/R Thigh", "The upper leg (hip to knee)."),
        ("Bip01 L/R Calf", "The lower leg (knee to ankle)."),
        ("Bip01 L/R Foot", "The foot / ankle."),
        ("Bip01 L/R Toe0", "The toes."),
    )),
    ("FX & attach points", (
        ("fx01 / fx02", "Effect attach points (usually the hands) where powers "
                        "and particles spawn. MUA characters use these."),
        ("Gun1", "Weapon / prop attach point for detachable segments."),
        ("Ponytail1 / Ponytail2", "Hair / cape dynamic bones on some characters."),
        ("*Nub", "Leaf / tip markers at finger & toe ends. Cosmetic end caps — "
                 "safe if missing."),
    )),
)


def _wrap(text, width=44):
    """Word-wrap text into lines for fixed-width Blender labels."""
    lines, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    if cur:
        lines.append(cur)
    return lines


def _draw_note(layout, text, icon='INFO', alert=False):
    """Draw word-wrapped multi-line text with the icon on the first line."""
    col = layout.column(align=True)
    col.alert = alert
    for i, ln in enumerate(_wrap(text)):
        col.label(text=ln, icon=icon if i == 0 else 'NONE')


def _collapsible(layout, props, prop_name, label, icon='NONE'):
    """Draw a boxed, collapsible section header.

    Returns (box, is_open). Caller draws content into box when is_open.
    """
    box = layout.box()
    is_open = getattr(props, prop_name)
    hdr = box.row(align=True)
    hdr.prop(props, prop_name, text="",
             icon='TRIA_DOWN' if is_open else 'TRIA_RIGHT', emboss=False)
    hdr.label(text=label, icon=icon)
    return box, is_open


# ---------------------------------------------------------------------------
# UIList classes
# ---------------------------------------------------------------------------

class ACTOR_UL_skins(UIList):
    """UIList for the mesh parts that make up the current skin."""
    bl_idname = "ACTOR_UL_skins"

    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_property, index=0, flt_flag=0):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            vis_icon = 'HIDE_OFF' if item.is_visible else 'HIDE_ON'
            op = row.operator("actor.toggle_skin", text="", icon=vis_icon,
                              emboss=False)
            op.index = index
            obj = bpy.data.objects.get(item.object_name)
            if obj is not None and obj.get("igb_is_outline", False):
                part_icon = 'MOD_WIREFRAME'
            elif obj is not None and obj.get("igb_segment_name", ""):
                part_icon = 'OBJECT_DATA'
            else:
                part_icon = 'MESH_DATA'
            row.label(text="", icon=part_icon)
            row.prop(item, "name", text="", emboss=False)
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text=item.name)


class ACTOR_UL_animations(UIList):
    """UIList for actor animations."""
    bl_idname = "ACTOR_UL_animations"

    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_property, index=0, flt_flag=0):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            op = row.operator("actor.set_animation", text="", icon='PLAY',
                              emboss=False)
            op.index = index
            row.prop(item, "name", text="", emboss=False)
            if item.duration_ms > 0:
                secs = item.duration_ms / 1000.0
                row.label(text=f"{secs:.1f}s")
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text=item.name)


# ===========================================================================
# MAIN TAB — "IGB Actors"
# ===========================================================================

class ACTOR_PT_Main(Panel):
    """IGB Actors main panel — always shows the active actor + target."""
    bl_label = "IGB Actors"
    bl_idname = "ACTOR_PT_Main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"

    def draw_header(self, context):
        icon_id = _get_icon_id()
        if icon_id:
            self.layout.label(icon_value=icon_id)

    def draw(self, context):
        layout = self.layout
        props = context.scene.igb_actor

        arm = (bpy.data.objects.get(props.active_armature)
               if props.active_armature else None)

        box = layout.box()
        if arm is not None:
            row = box.row(align=True)
            row.label(text=arm.name, icon='ARMATURE_DATA')
            target = arm.get("igb_target_game", "XML2")
            row.label(text=target, icon='SCENE')
            bone_count = arm.get(
                "igb_bone_count",
                len(arm.data.bones) if arm.type == 'ARMATURE' and arm.data
                else 0)
            mesh_children = sum(1 for c in arm.children if c.type == 'MESH')
            box.label(text=f"{bone_count} bones · {mesh_children} mesh(es)",
                      icon='BONE_DATA')
        else:
            box.label(text="No actor loaded", icon='INFO')
            box.label(text="Quick Tools ▸ 1. Import to begin.")


class ACTOR_PT_QuickTools(Panel):
    """One-stop workflow buttons so the user never has to leave this tab."""
    bl_label = "Quick Tools"
    bl_idname = "ACTOR_PT_QuickTools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "ACTOR_PT_Main"

    def draw(self, context):
        layout = self.layout
        props = context.scene.igb_actor

        # ---- 1. Import ----
        box, open = _collapsible(layout, props, "show_qt_import",
                                 "1. Import", 'IMPORT')
        if open:
            col = box.column(align=True)
            col.operator("actor.import_actor", text="Import Actor (.igb)",
                         icon='ARMATURE_DATA')
            col.operator("import_scene.igb", text="Import Scene/Model (.igb)",
                         icon='MESH_DATA')
            col.label(text="Skins need Import Actor, not Import Scene",
                      icon='INFO')

        # ---- 2. Setup ----
        box, open = _collapsible(layout, props, "show_qt_setup",
                                 "2. Setup", 'TOOL_SETTINGS')
        if open:
            col = box.column(align=True)
            col.operator("actor.setup_skin", text="Setup / Convert Rig",
                         icon='TOOL_SETTINGS')
            col.operator("actor.add_mesh_as_skin", icon='ADD')
            col.operator("actor.add_segment", icon='ADD')
            col.operator("igb.convert_materials",
                         text="Convert Materials to IGB", icon='MATERIAL')

        # ---- 3. Resize & Fixes ----
        # The armature-dependent operators grey out via their own poll();
        # scale_anim_set / fix_menu_anims are pure file tools.
        box, open = _collapsible(layout, props, "show_qt_resize",
                                 "3. Resize & Fixes", 'CON_SIZELIKE')
        if open:
            col = box.column(align=True)
            col.operator("actor.apply_pose_resize", icon='CON_SIZELIKE')
            col.operator("actor.scale_anim_set", icon='ANIM')
            col.operator("actor.fix_menu_anims", icon='SEQ_PREVIEW')
            col.operator("actor.fix_bip01", icon='FILE_REFRESH')

        # ---- 4. Export ----
        # One button. The dialog asks only for the game (XML2 / MUA) and an
        # optional texture-size cap, then applies the correct format, textures
        # and normal/specular maps for that game automatically.
        box, open = _collapsible(layout, props, "show_qt_export",
                                 "4. Export", 'EXPORT')
        if open:
            box.operator("actor.export_skin", text="Export Skin (.igb)",
                         icon='EXPORT')
            box.operator("actor.export_mannequin",
                         text="Export Mannequin (Static)",
                         icon='OUTLINER_OB_ARMATURE')
            box.label(text="Static = current pose baked, no armature",
                      icon='INFO')
            box.label(text="Animated Mannequin lives in the Extras tab",
                      icon='INFO')


class ACTOR_PT_Import(Panel):
    """Import + skin setup."""
    bl_label = "Import"
    bl_idname = "ACTOR_PT_Import"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "ACTOR_PT_Main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = context.scene.igb_actor

        layout.prop(props, "game_dir")
        layout.operator("actor.import_actor", icon='ARMATURE_DATA')

        # Import options (collapsible)
        box, open = _collapsible(layout, props, "show_import_options",
                                 "Import Options", 'PREFERENCES')
        if open:
            col = box.column(align=True)
            col.prop(props, "import_skins")
            col.prop(props, "import_animations")
            col.prop(props, "import_materials")
            col.separator()
            col.prop(props, "auto_detect_game")
            sub = col.column(align=True)
            sub.enabled = not props.auto_detect_game
            sub.prop(props, "game_preset")

        # Skin Setup (collapsible) — shown when an armature is selected.
        obj = context.active_object
        if obj and obj.type == 'ARMATURE':
            box, open = _collapsible(layout, props, "show_skin_setup",
                                     "Skin Setup", 'ARMATURE_DATA')
            if open:
                self._draw_skin_setup(box, props, obj)

    def _draw_skin_setup(self, box, props, obj):
        has_igb_data = "igb_skin_bone_info_list" in obj
        if has_igb_data:
            from .rig_converter import validate_bip01_rig
            issues = validate_bip01_rig(obj)

            if not issues:
                box.label(text="Skeleton data valid", icon='CHECKMARK')
            else:
                errors = sum(1 for lvl, _ in issues if lvl == 'ERROR')
                warns = sum(1 for lvl, _ in issues if lvl == 'WARNING')
                # Issues in their own collapsible sub-section.
                ebox, eopen = _collapsible(
                    box, props, "show_skin_errors",
                    (f"{errors} error(s), {warns} warning(s)" if errors
                     else f"{warns} warning(s)"),
                    'ERROR' if errors else 'INFO')
                if eopen:
                    for lvl, msg in issues[:8]:
                        ebox.label(text=msg,
                                   icon='ERROR' if lvl == 'ERROR' else 'INFO')
                    if len(issues) > 8:
                        ebox.label(text=f"...and {len(issues) - 8} more")
                    ebox.label(text="See the Information tab for what each "
                                    "bone does.", icon='QUESTION')

            bone_count = obj.get("igb_bone_count", 0)
            mesh_children = sum(1 for c in obj.children if c.type == 'MESH')
            target_game = obj.get("igb_target_game", "XML2")
            fx_count = sum(1 for b in obj.data.bones
                           if b.name in ('Gun1',) or
                           (b.name.startswith('fx') and b.name[2:].isdigit()))
            info_text = f"{bone_count} bones, {mesh_children} mesh(es)"
            if target_game == 'MUA':
                info_text += f", {fx_count} FX"
            box.label(text=info_text, icon='BONE_DATA')
            box.operator("actor.setup_skin", text="Re-setup Skin",
                         icon='TOOL_SETTINGS')
        else:
            from .rig_converter import is_bip01_rig
            if is_bip01_rig(obj):
                box.label(text="Bip01 rig — will configure for export")
            else:
                box.label(text="Non-XML2 rig — will convert and setup")
            box.operator("actor.setup_skin", icon='TOOL_SETTINGS')


class ACTOR_PT_Skins(Panel):
    """Skin management section."""
    bl_label = "Skins"
    bl_idname = "ACTOR_PT_Skins"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "ACTOR_PT_Main"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.scene.igb_actor.active_armature != ""

    def draw(self, context):
        layout = self.layout
        props = context.scene.igb_actor

        # Every row below is one MESH PART of the same skin (body, outline,
        # segments...) — they all export together into a single .igb.
        n = len(props.skins)
        if n > 1:
            layout.label(text=f"{n} mesh parts — export as ONE skin",
                         icon='INFO')

        row = layout.row()
        row.template_list(
            "ACTOR_UL_skins", "",
            props, "skins",
            props, "skins_index",
            rows=3,
        )

        # Solo / Show All for visibility wrangling
        if props.skins_index < len(props.skins):
            row = layout.row(align=True)
            op = row.operator("actor.solo_skin", text="Solo Selected",
                              icon='RESTRICT_VIEW_OFF')
            op.index = props.skins_index
            row.operator("actor.show_all_skins", text="Show All",
                         icon='HIDE_OFF')

        # Add/Remove (Import/Export Skin live in Quick Tools).
        row = layout.row(align=True)
        row.operator("actor.add_mesh_as_skin", icon='ADD')
        row.operator("actor.remove_skin", icon='REMOVE')

        # Weight mirroring — Blender's own X-mirror can't flip the mid-name
        # game bone sides ('Bip01 L UpperArm'), so we provide our own.
        box = layout.box()
        box.label(text="Mirror Weights (game bone names)", icon='MOD_MIRROR')
        row = box.row(align=True)
        opl = row.operator("actor.mirror_weights", text="Left → Right")
        opl.direction = 'L2R'
        opr = row.operator("actor.mirror_weights", text="Right → Left")
        opr.direction = 'R2L'
        box.label(text="Select the skin MESH first. Paint one side,",
                  icon='INFO')
        box.label(text="then mirror onto the other.")


class ACTOR_PT_Segments(Panel):
    """Segment management and diagnostics for the selected skin."""
    bl_label = "Segments"
    bl_idname = "ACTOR_PT_Segments"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "ACTOR_PT_Skins"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        props = context.scene.igb_actor
        return (props.active_armature != "" and
                len(props.skins) > 0 and
                props.skins_index < len(props.skins))

    def draw(self, context):
        layout = self.layout
        props = context.scene.igb_actor

        from ..actor.actor_import import collect_skin_segments
        segments = collect_skin_segments(props, props.skins_index)

        if not segments:
            layout.label(text="No segments found.", icon='INFO')
            return

        arm = bpy.data.objects.get(props.active_armature)

        # Group meshes by base segment name (strip _outline suffix to pair).
        groups = {}
        for seg_mesh, is_outline in segments:
            seg_name = seg_mesh.get("igb_segment_name", "")
            seg_flags = seg_mesh.get("igb_segment_flags", 0)
            base = seg_name
            if base.endswith("_outline"):
                base = base[:-8]
            if base not in groups:
                groups[base] = {'main': [], 'outline': [], 'flags': seg_flags}
            (groups[base]['outline'] if is_outline
             else groups[base]['main']).append(seg_mesh)

        sorted_names = sorted(groups.keys(), key=lambda n: (n != "", n))

        for base_name in sorted_names:
            grp = groups[base_name]
            is_body = (base_name == "")
            display_name = "Body" if is_body else base_name
            seg_flags = grp['flags']
            is_hidden = bool(seg_flags & 2)

            box = layout.box()

            # Header row: collapse toggle + name + action buttons.
            # "__body__" sentinel mirrors ACTOR_OT_toggle_segment_collapse so
            # the unnamed Body group has a stable, non-empty key.
            collapsed = bool(arm.get(
                "igb_segcollapse:" + (base_name or "__body__"), False)
            ) if arm else False
            header = box.row(align=True)
            op_c = header.operator("actor.toggle_segment_collapse", text="",
                                   icon='TRIA_RIGHT' if collapsed
                                   else 'TRIA_DOWN', emboss=False)
            op_c.segment_name = base_name
            header.label(text=display_name,
                         icon='MESH_DATA' if is_body else 'OBJECT_DATA')

            op_sel = header.operator("actor.select_segment",
                                     text="", icon='RESTRICT_SELECT_OFF')
            op_sel.segment_name = base_name

            if not is_body:
                op_ren = header.operator("actor.rename_segment",
                                         text="", icon='GREASEPENCIL')
                op_ren.old_name = base_name
                vis_icon = 'HIDE_ON' if is_hidden else 'HIDE_OFF'
                op = header.operator("actor.toggle_segment", text="",
                                     icon=vis_icon, depress=not is_hidden)
                op.segment_name = base_name
                op_rem = header.operator("actor.remove_segment",
                                         text="", icon='X')
                op_rem.segment_name = base_name

            if collapsed:
                continue

            # Sub-meshes
            col = box.column(align=True)
            for mesh_obj in grp['main'] + grp['outline']:
                mesh_outline = bool(mesh_obj.get("igb_is_outline", False))
                vert_count = len(mesh_obj.data.vertices) if mesh_obj.data else 0
                vgroup_count = len(mesh_obj.vertex_groups)
                weighted = mesh_obj.get("igb_weighted_vert_count", -1)
                mesh_icon = 'MOD_WIREFRAME' if mesh_outline else 'MESH_DATA'
                mesh_type = "Outline" if mesh_outline else "Main"

                col.label(text=f"{mesh_type}: {mesh_obj.name}", icon=mesh_icon)
                sub = col.row(align=True)
                sub.label(text=f"  {vert_count}v, {vgroup_count} groups")
                if weighted >= 0 and vert_count > 0:
                    ratio = weighted / vert_count
                    w_icon = 'CHECKMARK' if ratio > 0.99 else 'ERROR'
                    sub.label(text=f"{weighted}/{vert_count}", icon=w_icon)

        layout.separator()
        row = layout.row(align=True)
        row.operator("actor.add_segment", icon='ADD')
        row.operator("actor.refresh_segments", text="Refresh",
                     icon='FILE_REFRESH')


class ACTOR_PT_Animations(Panel):
    """Animation management section."""
    bl_label = "Animations"
    bl_idname = "ACTOR_PT_Animations"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "ACTOR_PT_Main"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.scene.igb_actor.active_armature != ""

    def draw(self, context):
        layout = self.layout
        props = context.scene.igb_actor

        row = layout.row()
        row.template_list(
            "ACTOR_UL_animations", "",
            props, "animations",
            props, "animations_index",
            rows=5,
        )
        col = row.column(align=True)
        col.operator("actor.import_animations", icon='IMPORT', text="")
        col.operator("actor.remove_animation", icon='REMOVE', text="")

        row = layout.row(align=True)
        row.operator("actor.rest_pose", text="Rest Pose", icon='ARMATURE_DATA')
        is_playing = context.screen.is_animation_playing
        play_icon = 'PAUSE' if is_playing else 'PLAY'
        play_text = "Stop" if is_playing else "Play"
        row.operator("actor.play_animation", text=play_text, icon=play_icon)

        if props.animations_index < len(props.animations):
            anim = props.animations[props.animations_index]
            box = layout.box()
            col = box.column(align=True)
            col.label(text=f"Duration: {anim.duration_ms / 1000.0:.2f}s")
            col.label(text=f"Tracks: {anim.track_count}")
            col.label(text=f"Frames: {anim.frame_count}")

        layout.separator()
        row = layout.row(align=True)
        row.operator("actor.import_animations", text="Import Animations",
                     icon='IMPORT')
        row.operator("actor.export_animations", text="Export Animations",
                     icon='EXPORT')


def _actor_material_meshes(context):
    """Meshes whose materials the actor Materials panel edits.

    Prefer the registered skins list, but fall back to the active armature's
    skinned mesh children, then the active mesh — so the Materials tab works
    for ANY imported actor even if its meshes were never added to the skins
    list (the "1 mesh(es)" summary counts armature children, not props.skins).
    Returns a de-duplicated list of mesh objects.
    """
    props = context.scene.igb_actor
    out, seen = [], set()

    def _add(mo):
        if (mo is not None and getattr(mo, 'type', None) == 'MESH'
                and mo.name not in seen):
            out.append(mo)
            seen.add(mo.name)

    # 1. registered skins
    for skin in props.skins:
        _add(bpy.data.objects.get(skin.object_name))
    if out:
        return out

    # 2. the active armature's skinned meshes
    arm = (bpy.data.objects.get(props.active_armature)
           if props.active_armature else None)
    if arm is not None and arm.type == 'ARMATURE':
        try:
            from .rig_converter import _get_skinned_meshes
            for mo in _get_skinned_meshes(arm):
                _add(mo)
        except Exception:
            for c in arm.children_recursive:
                _add(c)
    if out:
        return out

    # 3. the active mesh
    _add(context.active_object)
    return out


class ACTOR_PT_Materials(Panel):
    """Target-aware material editor: pick a material, edit what the game supports."""
    bl_label = "Materials"
    bl_idname = "ACTOR_PT_Materials"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "ACTOR_PT_Main"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        if context.scene.igb_actor.active_armature != "":
            return True
        ao = context.active_object
        return ao is not None and ao.type == 'MESH'

    def draw(self, context):
        layout = self.layout
        props = context.scene.igb_actor

        # Resolve the actor's skin mesh(es) + their materials (robust: works
        # from the registered skins list OR the armature's meshes OR the active
        # mesh, so an imported actor always shows its materials to edit).
        meshes = _actor_material_meshes(context)
        if not meshes:
            layout.label(text="Import an actor (or select a skinned mesh)",
                         icon='INFO')
            layout.label(text="to edit its materials / maps.")
            return

        materials, seen = [], set()
        for mo in meshes:
            for m in mo.data.materials:
                if m is not None and m.name not in seen:
                    materials.append(m)
                    seen.add(m.name)

        # --- Target game: switch at will (drives map features + export format) ---
        from .operators import actor_target_game, _igb_group_node
        target = actor_target_game(context)
        is_mua = (target == 'MUA')

        tbox = layout.box()
        trow = tbox.row(align=True)
        trow.label(text="Target Game:", icon='SCENE')
        op = trow.operator("actor.set_target_game", text="XML2",
                           depress=(target == 'XML2'))
        op.target = 'XML2'
        op = trow.operator("actor.set_target_game", text="MUA",
                           depress=(target == 'MUA'))
        op.target = 'MUA'
        mname = (meshes[0].name if len(meshes) == 1
                 else f"{len(meshes)} meshes")
        tbox.label(text=mname, icon='MESH_DATA')

        # --- Convert all skin materials to IGB (for this target) ---
        igb_count = sum(1 for m in materials
                        if _igb_group_node(m) is not None
                        or any(k.startswith("igb_") for k in m.keys()))
        crow = layout.row(align=True)
        crow.scale_y = 1.2
        crow.operator("actor.convert_skin_materials",
                      text=f"Convert All to IGB ({target})",
                      icon='NODE_MATERIAL')
        if materials:
            layout.label(
                text=f"{igb_count}/{len(materials)} material(s) IGB-ready"
                     + ("  — all set" if igb_count == len(materials) else ""),
                icon='CHECKMARK' if igb_count == len(materials) else 'INFO')

        if not materials:
            layout.label(text="Skin mesh has no materials yet.", icon='INFO')
            return

        # --- Material list: pick one to edit ---
        active_name = props.active_material_name
        if not active_name or active_name not in seen:
            active_name = materials[0].name
        if len(materials) > 1:
            lst = layout.box().column(align=True)
            for m in materials:
                row = lst.row(align=True)
                op = row.operator("actor.select_material", text=m.name,
                                  icon='MATERIAL',
                                  depress=(m.name == active_name))
                op.material_name = m.name
        mat = bpy.data.materials.get(active_name) or materials[0]

        self._draw_material(layout, props, mat, is_mua)

        # --- Quick Tools for actor materials (skin meshes only) ---
        layout.separator()
        self._draw_quick_tools(layout)

    # -- per-material editor -------------------------------------------------

    def _draw_material(self, layout, props, mat, is_mua):
        from .operators import _role_map_node, _igb_group_node
        from ..panels import _draw_material_colors

        box = layout.box()
        box.label(text=mat.name, icon='MATERIAL')

        has_igb = (_igb_group_node(mat) is not None or
                   any(k.startswith("igb_") for k in mat.keys()))
        if not has_igb:
            box.label(text="Not an IGB material yet.", icon='INFO')
            box.operator("actor.convert_skin_materials",
                         text="Convert All to IGB", icon='MATERIAL')
            return

        # Material colors (collapsible).
        hdr = box.row(align=True)
        hdr.prop(props, "show_material_colors",
                 icon='TRIA_DOWN' if props.show_material_colors
                 else 'TRIA_RIGHT', emboss=False)
        hdr.label(text="Material Colors", icon='COLOR')
        if props.show_material_colors:
            _draw_material_colors(box, mat)

        # Render state (collapsible): blend + alpha test + lighting + culling.
        rbox, ropen = _collapsible(box, props, "show_material_render",
                                   "Render State", 'SHADING_RENDERED')
        if ropen:
            rbox.prop(mat, "igb_blend_mode", text="Blend")
            if mat.get("igb_alpha_test_enabled") is not None:
                rbox.prop(mat, '["igb_alpha_test_enabled"]', text="Alpha Test")
            if mat.get("igb_lighting_enabled") is not None:
                rbox.prop(mat, '["igb_lighting_enabled"]', text="Lighting")
            if mat.get("igb_cull_face_enabled") is not None:
                rbox.prop(mat, '["igb_cull_face_enabled"]',
                          text="Backface Culling")

        # Texture maps (collapsible). Base Color for BOTH games (the one texture
        # XML2 uses); normal / specular / gloss are MUA-only, below it.
        mbox, mopen = _collapsible(box, props, "show_material_maps",
                                   "Texture Maps", 'TEXTURE')
        if mopen:
            if _igb_group_node(mat) is None:
                mbox.label(text="Convert to IGB material to edit maps.",
                           icon='INFO')
            else:
                self._map_row(mbox, mat, 'diffuse', "Base Color", None,
                              _role_map_node)
                if is_mua:
                    self._map_row(mbox, mat, 'normal', "Normal",
                                  "igb_use_normal_map", _role_map_node)
                    self._map_row(mbox, mat, 'specular', "Specular",
                                  "igb_use_specular_map", _role_map_node)
                    self._map_row(mbox, mat, 'gloss', "Gloss/Mask",
                                  "igb_use_gloss_map", _role_map_node)
                    mbox.label(text="Normal/spec/gloss export on MUA "
                                    "(units 0/1/2/5).", icon='INFO')
                    mbox.label(text="Blue normal maps auto-convert to MUA "
                                    "on export.", icon='CHECKMARK')
                    if _role_map_node(mat, 'normal') is not None and \
                            getattr(_role_map_node(mat, 'normal'), 'image',
                                    None) is not None:
                        opc = mbox.operator("actor.convert_normal_mua",
                                            icon='NODE_TEXTURE')
                        opc.material_name = mat.name
                else:
                    mbox.label(text="XML2 uses only the Base Color "
                                    "(no normal maps).", icon='INFO')

    @staticmethod
    def _map_row(layout, mat, role, label, use_prop, role_node_fn):
        row = layout.row(align=True)
        # use_prop=None -> no on/off toggle (e.g. Base Color, always exported).
        if use_prop:
            row.prop(mat, use_prop, text="")
        sub = row.row(align=True)
        sub.enabled = getattr(mat, use_prop, True) if use_prop else True
        sub.label(text=label)
        node = role_node_fn(mat, role)
        if node is not None:
            sub.template_ID(node, "image", new="image.new", open="image.open")
            opx = sub.operator("actor.clear_material_map", text="", icon='X')
            opx.material_name = mat.name
            opx.role = role
        else:
            op = sub.operator("actor.add_material_map", text="Add", icon='ADD')
            op.material_name = mat.name
            op.role = role

    # -- shared quick tools --------------------------------------------------

    @staticmethod
    def _draw_quick_tools(layout):
        box = layout.box()
        box.label(text="Quick Tools (All Skins)", icon='TOOL_SETTINGS')
        box.label(text="Applies to skin-list meshes only.")

        row = box.row(align=True)
        op = row.operator("igb.set_all_emission", text="No Emission",
                          icon='COLORSET_13_VEC')
        op.color = (0.0, 0.0, 0.0, 1.0)
        op.skins_only = True
        op = row.operator("igb.set_all_emission", text="Set Emission...",
                          icon='LIGHT_SUN')
        op.skins_only = True
        op.show_dialog = True

        row = box.row(align=True)
        op = row.operator("igb.set_all_specular", text="No Specular",
                          icon='COLORSET_13_VEC')
        op.color = (0.0, 0.0, 0.0, 1.0)
        op.skins_only = True
        op = row.operator("igb.set_all_specular", text="Set Specular...",
                          icon='LIGHT_POINT')
        op.skins_only = True
        op.show_dialog = True

        row = box.row(align=True)
        op = row.operator("igb.set_all_diffuse", text="Set Diffuse...",
                          icon='COLORSET_03_VEC')
        op.skins_only = True
        op.show_dialog = True

        # Shininess = specular exponent: higher = tighter highlight = LESS
        # overall shine. "Less Shiny" tightens it; "Soft" is the native look.
        row = box.row(align=True)
        op = row.operator("igb.set_all_shininess", text="Soft (native)",
                          icon='MATPLANE')
        op.shininess = 1.28
        op.skins_only = True
        op = row.operator("igb.set_all_shininess", text="Less Shiny",
                          icon='SHADING_RENDERED')
        op.shininess = 32.0
        op.skins_only = True


class ACTOR_PT_AnimConverter(Panel):
    """Convert animations from any rig to XML2/MUA IGB."""
    bl_label = "Animation Converter"
    bl_idname = "ACTOR_PT_AnimConverter"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "ACTOR_PT_Extras"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'ARMATURE'

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        props = context.scene.igb_actor
        if props.active_armature and obj.name != props.active_armature:
            layout.label(text=f"Converting non-IGB rig: {obj.name}", icon='INFO')

        from .rig_converter import build_rename_map
        bone_mapping = build_rename_map(obj)

        box = layout.box()
        box.label(text="Rig Analysis", icon='BONE_DATA')
        box.label(text=f"Source bones: {len(obj.data.bones)}")
        box.label(text=f"Mapped to XML2: {len(bone_mapping)}")
        if bone_mapping:
            mapped = set(bone_mapping.values())
            row = box.row()
            row.label(text="Spine",
                      icon='CHECKMARK' if 'Bip01 Spine' in mapped else 'X')
            row.label(text="Arms",
                      icon='CHECKMARK' if ('Bip01 L UpperArm' in mapped and
                                           'Bip01 R UpperArm' in mapped)
                      else 'X')
            row.label(text="Legs",
                      icon='CHECKMARK' if ('Bip01 L Thigh' in mapped and
                                           'Bip01 R Thigh' in mapped)
                      else 'X')

        if obj.animation_data and obj.animation_data.action:
            action = obj.animation_data.action
            box = layout.box()
            box.label(text=f"Action: {action.name}", icon='ACTION')
            fr = action.frame_range
            fps = context.scene.render.fps
            dur = (fr[1] - fr[0]) / fps if fps > 0 else 0
            box.label(text=f"Duration: {dur:.2f}s ({int(fr[1] - fr[0])} frames)")
        else:
            layout.label(text="No active animation", icon='INFO')

        layout.separator()
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator("actor.convert_animation", text="Convert to IGB",
                     icon='EXPORT')
        row.enabled = (len(bone_mapping) >= 3 and
                       obj.animation_data is not None and
                       obj.animation_data.action is not None)


# ===========================================================================
# INFORMATION  (collapsible section at the bottom of the IGB Actors tab)
# ===========================================================================

class ACTOR_PT_Information(Panel):
    """Plain-language docs for the whole panel + a full bone reference."""
    bl_label = "Information"
    bl_idname = "ACTOR_PT_Information"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "ACTOR_PT_Main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        self.layout.label(icon='INFO')

    def draw(self, context):
        layout = self.layout
        props = context.scene.igb_actor

        layout.label(text="How the IGB Actors tab works.", icon='HELP')

        box, open = _collapsible(layout, props, "show_doc_workflow",
                                 "Workflow", 'SEQUENCE')
        if open:
            _draw_note(box, "1. Import — 'Import Actor' loads a character "
                            "(skeleton + skins + animations). 'Import Scene' is "
                            "for maps/props, not skins.", 'IMPORT')
            box.separator()
            _draw_note(box, "2. Setup — 'Setup / Convert Rig' prepares any "
                            "rig (even a VRChat/Unity one) for export: renames "
                            "bones to Bip01, fixes parents, builds weights.",
                       'TOOL_SETTINGS')
            box.separator()
            _draw_note(box, "3. Resize & Fixes — scale 'Bip01 Pelvis' in "
                            "Pose Mode then 'Apply Pose Scale'. Resized "
                            "characters also need 'Scale Animation Set' and "
                            "'Fix Menu Float' so they stand on the ground.",
                       'CON_SIZELIKE')
            box.separator()
            _draw_note(box, "4. Export — 'Export Skin' asks only for the "
                            "game (MUA / XML2) and a texture-size cap, then "
                            "applies the right format and maps automatically.",
                       'EXPORT')

        box, open = _collapsible(layout, props, "show_doc_skins",
                                 "Skins & Segments", 'MESH_DATA')
        if open:
            _draw_note(box, "A skin can be several mesh parts (body, outline, "
                            "guns). They all export together into ONE .igb — "
                            "the Skins list shows each part.", 'MESH_DATA')
            box.separator()
            _draw_note(box, "Segments are detachable named parts (like a gun) "
                            "that the engine can hide or swap. Body is the main "
                            "mesh. Use the eye icon to hide a segment, the "
                            "pencil to rename it.", 'OBJECT_DATA')
            box.separator()
            _draw_note(box, "The green check / red number after each part is "
                            "the weighted-vertex count: every vertex should be "
                            "weighted to a bone or it won't deform.", 'CHECKMARK')

        box, open = _collapsible(layout, props, "show_doc_materials",
                                 "Materials & Maps", 'MATERIAL')
        if open:
            _draw_note(box, "Pick a material from the list to edit it. Only the "
                            "features the chosen game supports are shown.",
                       'MATERIAL')
            box.separator()
            _draw_note(box, "Blend sets transparency: Opaque, Alpha (glass), "
                            "Additive (glow), or Inverse-Alpha Additive.",
                       'NODE_MATERIAL')
            box.separator()
            _draw_note(box, "MUA only: Normal, Specular and Gloss/Mask maps "
                            "(texture units 0/1/2/5). Each has a checkbox — "
                            "untick it to leave that map out of the export "
                            "without deleting it. All three export on the MUA "
                            "skin; on import the Gloss/Mask map round-trips "
                            "through the material's Sheen Tint slot.", 'TEXTURE')
            box.separator()
            _draw_note(box, "XML2 skins use a single diffuse texture — no "
                            "normal/specular/gloss maps, so those are hidden.",
                       'INFO')
            box.separator()
            _draw_note(box, "Texture size is the #1 driver of file size. The "
                            "export's Texture Resolution defaults to 512, which "
                            "matches native characters and is much smaller than "
                            "1024.", 'IMAGE_DATA')

        box, open = _collapsible(layout, props, "show_doc_extras",
                                 "Extras", 'PLUS')
        if open:
            _draw_note(box, "Animated Mannequin — export a v6 skin to "
                            "ui/models/mannequin/<id>.igb; the MUA "
                            "character-select screen idles it automatically.",
                       'ARMATURE_DATA')
            box.separator()
            _draw_note(box, "VR Motion Capture — drive the rig live with "
                            "the VMC4B addon and export the captured action as "
                            "a new animation IGB.", 'CON_TRANSLIKE')

        box, open = _collapsible(layout, props, "show_doc_bones",
                                 "Bone Reference", 'BONE_DATA')
        if open:
            _draw_note(box, "What each engine bone does. The Setup step "
                            "creates / renames the body bones for you.", 'BONE_DATA')
            for cat, bones in _BONE_REFERENCE:
                cbox = box.box()
                cbox.label(text=cat, icon='GROUP_BONE')
                for name, desc in bones:
                    col = cbox.column(align=True)
                    col.label(text=name, icon='BONE_DATA')
                    for ln in _wrap("    " + desc, width=46):
                        col.label(text=ln)


# ===========================================================================
# EXTRAS  (collapsible section at the bottom of the IGB Actors tab) — holds the
# mannequin export, VR mocap, and the animation converter as sub-sections.
# ===========================================================================

class ACTOR_PT_Extras(Panel):
    """Container for the less-common tools."""
    bl_label = "Extras"
    bl_idname = "ACTOR_PT_Extras"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "ACTOR_PT_Main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        self.layout.label(icon='PLUS')

    def draw(self, context):
        layout = self.layout
        layout.label(text="Mannequin export, VR mocap, animation converter.",
                     icon='INFO')
        # Preview lighting. Primary = a synthesized 3-point key/fill/rim rig that
        # shows off normal/spec/gloss relief (the game's own front-end lighting
        # does this poorly). Secondary = import real lights from a scene IGB.
        box = layout.box()
        box.label(text="Preview Lighting", icon='LIGHT')
        box.operator("actor.character_preview_lights",
                     text="Character Preview (3-point)",
                     icon='OUTLINER_OB_LIGHT')
        box.label(text="Best for checking normal/specular maps.", icon='INFO')
        sub = box.box()
        sub.label(text="Or import real scene lights:", icon='IMPORT')
        sub.operator("actor.import_game_lights",
                     text="Import Scene Lights from IGB")
        sub.label(text="XML2 backdrop: maps/menu/main_back.igb.")
        box.label(text="Use Material Preview / Rendered shading to see it.")


class ACTOR_PT_Mannequin(Panel):
    """MUA Animated Mannequin export."""
    bl_label = "MUA Animated Mannequin"
    bl_idname = "ACTOR_PT_Mannequin"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "ACTOR_PT_Extras"
    bl_options = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        self.layout.label(icon='ARMATURE_DATA')

    def draw(self, context):
        layout = self.layout
        # A mannequin is just a v6 skinned actor at ui/models/mannequin/<id>.igb;
        # the MUA character-select screen plays an idle on it at runtime.
        layout.label(text="Idles in the MUA character-select screen", icon='INFO')
        op = layout.operator("actor.export_skin",
                             text="Export Animated Mannequin (MUA)",
                             icon='ARMATURE_DATA')
        op.mannequin_mode = True
        layout.label(text="Save as <id>.igb in ui/models/mannequin/")


class ACTOR_PT_VMC(Panel):
    """VMC4B VR Motion Capture integration."""
    bl_label = "VR Motion Capture"
    bl_idname = "ACTOR_PT_VMC"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "ACTOR_PT_Extras"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = context.scene.igb_actor

        if not props.active_armature:
            layout.label(text="Load an actor first.", icon='INFO')
            return

        armature_obj = bpy.data.objects.get(props.active_armature)

        # ---- Retarget from a VR / Unity source armature ----
        # Works with any addon that drives a Unity-named armature (not just
        # VMC4B): pick the source, record your take onto it, then retarget.
        box = layout.box()
        box.label(text="Retarget VR / Mocap Take", icon='ARMATURE_DATA')
        box.prop_search(props, "vr_source_armature", bpy.data, "objects",
                        text="Source Rig")
        src = bpy.data.objects.get(props.vr_source_armature)
        if src and src.type != 'ARMATURE':
            box.label(text="Pick an armature.", icon='ERROR')
        elif src:
            src_action = (src.animation_data.action
                          if src.animation_data else None)
            if src_action:
                fr = src_action.frame_range
                box.label(text=f"Take: {src_action.name} "
                               f"({int(fr[1] - fr[0])} frames)", icon='ACTION')
            else:
                box.label(text="Source has no recorded Action yet.",
                          icon='INFO')
            box.prop(props, "vr_use_action_range")
            if not props.vr_use_action_range:
                row = box.row(align=True)
                row.prop(props, "vr_frame_start")
                row.prop(props, "vr_frame_end")
            box.prop(props, "vr_root_motion")
            box.operator("actor.vr_retarget", text="Retarget onto Actor",
                         icon='CON_ARMATURE')
            box.label(text="Then Export as New IGB below.", icon='EXPORT')
        else:
            box.label(text="Pick the armature your VR addon drives.",
                      icon='INFO')

        # ---- VMC4B live-drive setup (only if that addon is installed) ----
        has_vmc4b = hasattr(context.scene, 'vmc4b_target_armature')
        if has_vmc4b:
            layout.separator()
            is_configured = (armature_obj and
                             "vmc_use_standard_orientations" in armature_obj)
            box = layout.box()
            box.label(text="VMC4B Live Setup", icon='LINKED')
            if is_configured:
                box.label(text="Orientation correction active",
                          icon='CHECKMARK')
                vmc_target = context.scene.vmc4b_target_armature
                icon = ('CHECKMARK' if armature_obj and
                        vmc_target == armature_obj.name else 'INFO')
                box.label(text=f"VMC4B target: {vmc_target}", icon=icon)
                row = box.row(align=True)
                row.operator("actor.setup_vmc4b", text="Reconfigure",
                             icon='FILE_REFRESH')
                row.operator("actor.cleanup_vmc", text="Remove", icon='TRASH')
            else:
                box.label(text="Binds VMC4B bones and enables", icon='INFO')
                box.label(text="orientation correction for XML2 rigs.")
                box.operator("actor.setup_vmc4b", text="Setup VMC4B",
                             icon='LINKED')

        # ---- Export ----
        layout.separator()
        box = layout.box()
        box.label(text="Export Custom Animation", icon='EXPORT')
        if armature_obj and armature_obj.animation_data:
            action = armature_obj.animation_data.action
            if action:
                box.label(text=f"Active: {action.name}", icon='ACTION')
                frange = action.frame_range
                fps = context.scene.render.fps
                dur = (frange[1] - frange[0]) / fps if fps > 0 else 0
                box.label(text=f"Duration: {dur:.2f}s "
                          f"({int(frange[1] - frange[0])} frames)")
            else:
                box.label(text="No active animation", icon='INFO')
        else:
            box.label(text="No animation data", icon='INFO')
        box.operator("actor.export_anim_scratch", text="Export as New IGB",
                     icon='FILE_NEW')


# ===========================================================================
# IGB Mocap / Briefings — its own N-panel tab.
# The game plays a briefing on dedicated 65-bone MOCAP MODELS (npcstat <char>_MC
# -> skin 200xx) with the zone performances applied DIRECTLY (no retarget). This
# tab exposes that: load a whole briefing, or a single mocap model + performance,
# plus a retarget path for putting a performance on a custom character, and
# export back to the zone format.
# ===========================================================================

class MOCAP_PT_Main(Panel):
    """Root panel for the IGB Mocap / Briefings tab."""
    bl_label = "IGB Mocap / Briefings"
    bl_idname = "MOCAP_PT_Main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Mocap"

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.label(text="Cutscene mocap & briefings", icon='SEQUENCE')
        col.label(text="Loads the real 65-bone mocap models and", icon='INFO')
        col.label(text="plays zone performances directly (no retarget).")


class MOCAP_PT_Briefings(Panel):
    """One-click load of a full briefing cutscene."""
    bl_label = "Briefings"
    bl_idname = "MOCAP_PT_Briefings"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Mocap"
    bl_parent_id = "MOCAP_PT_Main"

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Load a whole briefing cutscene", icon='PLAY')
        box.label(text="map + mocap models + performances", icon='INFO')
        box.label(text="+ staging marks + voice audio")
        box.operator("actor.load_briefing", text="Load Briefing…",
                     icon='IMPORT')
        box.label(text="XML2/MUA: maps/briefing/<b>.igb", icon='FILE_FOLDER')
        box.label(text="XML1: maps/mocap/mocapN/<b>.igb")
        box.label(text="Set 'Asset Root' to force the data folder")


class MOCAP_PT_Models(Panel):
    """Load a single mocap model and direct-apply zone performances."""
    bl_label = "Mocap Models"
    bl_idname = "MOCAP_PT_Models"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Mocap"
    bl_parent_id = "MOCAP_PT_Main"

    def draw(self, context):
        layout = self.layout
        props = context.scene.igb_actor

        box = layout.box()
        box.label(text="1. Load a mocap model", icon='OUTLINER_OB_ARMATURE')
        box.label(text="65-bone models: actors/200xx.igb", icon='INFO')
        box.operator("actor.import_actor", text="Load Mocap Model…",
                     icon='IMPORT')

        box = layout.box()
        box.label(text="2. Apply performances (direct)", icon='ACTION')
        tgt = (bpy.data.objects.get(props.active_armature)
               if props.active_armature else None)
        box.label(text=f"Model: {tgt.name if tgt else '(load a model)'}",
                  icon='OUTLINER_OB_ARMATURE')
        box.operator("actor.load_mocap_performances",
                     text="Load Performances…", icon='IMPORT')
        box.label(text="Pick actors/zone_*.igb — no retarget",
                  icon='FILE_FOLDER')

        box = layout.box()
        box.label(text="3. Rig display", icon='BONE_DATA')
        box.operator("actor.connect_bones", text="Connect Bones",
                     icon='CONSTRAINT_BONE')
        box.label(text="Connects the chain; mesh + anim kept.", icon='INFO')

        box = layout.box()
        box.label(text="4. Posing", icon='ARMATURE_DATA')
        box.operator("actor.mirror_pose", text="Mirror Pose L↔R",
                     icon='MOD_MIRROR')
        box.label(text="Works with game 'Bip01 L/R' names", icon='INFO')


class MOCAP_PT_Retarget(Panel):
    """Retarget a performance onto a DIFFERENT / custom character (for skins
    that aren't a matching mocap model). Uses the world-delta retarget."""
    bl_label = "Retarget onto Custom Character"
    bl_idname = "MOCAP_PT_Retarget"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Mocap"
    bl_parent_id = "MOCAP_PT_Main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        props = context.scene.igb_actor
        layout.label(text="For custom skins with a different rig.", icon='INFO')
        layout.label(text="Matching mocap models don't need this.")

        box = layout.box()
        box.operator("actor.import_mocap", text="Import Mocap Rig (source)",
                     icon='ARMATURE_DATA')
        box.prop_search(props, "mocap_source_armature", bpy.data, "objects",
                        text="Mocap Rig")
        src = bpy.data.objects.get(props.mocap_source_armature)
        if src and src.type == 'ARMATURE':
            perf = src.animation_data.action if src.animation_data else None
            if perf:
                fr = perf.frame_range
                box.label(text=f"Performance: {perf.name} "
                               f"({int(fr[1] - fr[0])} frames)", icon='ACTION')
            else:
                box.label(text="Pick a performance (Action) on the rig",
                          icon='ERROR')
        tgt = (bpy.data.objects.get(props.active_armature)
               if props.active_armature else None)
        box.label(text=f"Target: {tgt.name if tgt else '(load an actor)'}",
                  icon='OUTLINER_OB_ARMATURE')
        box.prop(props, "mocap_root_motion")
        box.prop(props, "mocap_full_range")
        if not props.mocap_full_range:
            row = box.row(align=True)
            row.prop(props, "mocap_frame_start")
            row.prop(props, "mocap_frame_end")
        box.operator("actor.retarget_mocap", text="Retarget onto Actor",
                     icon='CON_ARMATURE')


class MOCAP_PT_Export(Panel):
    """Export the active model's Action back to the zone mocap format."""
    bl_label = "Export Performance"
    bl_idname = "MOCAP_PT_Export"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Mocap"
    bl_parent_id = "MOCAP_PT_Main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Active model's Action → zone mocap IGB", icon='INFO')
        box.operator("actor.export_mocap", text="Export Mocap (zone IGB)",
                     icon='FILE_NEW')


# ---------------------------------------------------------------------------
# Registration. One "IGB Actors" tab; sibling sub-panels appear in registration
# order, so Information then Extras land at the bottom. A parent must register
# before its children (Extras before its Mannequin/VMC/AnimConverter).
# ---------------------------------------------------------------------------

_classes = (
    ACTOR_UL_skins,
    ACTOR_UL_animations,
    ACTOR_PT_Main,
    ACTOR_PT_QuickTools,
    ACTOR_PT_Import,
    ACTOR_PT_Skins,
    ACTOR_PT_Segments,
    ACTOR_PT_Animations,
    ACTOR_PT_Materials,
    # Bottom of the tab: docs, then the Extras container + its sub-sections.
    ACTOR_PT_Information,
    ACTOR_PT_Extras,
    ACTOR_PT_Mannequin,
    ACTOR_PT_VMC,
    ACTOR_PT_AnimConverter,
    # Dedicated "IGB Mocap" tab (parent registers before its children).
    MOCAP_PT_Main,
    MOCAP_PT_Briefings,
    MOCAP_PT_Models,
    MOCAP_PT_Retarget,
    MOCAP_PT_Export,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
