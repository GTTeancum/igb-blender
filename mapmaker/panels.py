"""Map Maker Tool panels — tabbed layout with 4 workflow views.

Panel structure (tab-based):
  [Scene]  Map identity, environment, build paths
  [Place]  Quick-add, entity defs, character/model browsers
  [Logic]  Objectives, conversations, precache
  [Build]  Navigation, compilation, deployment, collision, automap
"""

import bpy
from bpy.types import Panel, UIList
from .. import _get_icon_id


# ===========================================================================
# UIList renderers (unchanged, all existing UILists preserved)
# ===========================================================================

class MM_UL_entity_defs(UIList):
    """UIList for entity definitions with search/filter support."""
    bl_idname = "MM_UL_entity_defs"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            row.prop(item, "entity_name", text="", emboss=False)
            row.label(text=item.classname, icon='NONE')
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text=item.entity_name)

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        flt_flags = [0] * len(items)
        flt_neworder = list(range(len(items)))

        if self.filter_name:
            query = self.filter_name.lower()
            for i, item in enumerate(items):
                if (query in item.entity_name.lower()
                        or query in item.classname.lower()):
                    flt_flags[i] = self.bitflag_filter_item
        else:
            flt_flags = [self.bitflag_filter_item] * len(items)

        return flt_flags, flt_neworder


class MM_UL_entity_properties(UIList):
    """UIList for custom key-value properties on an entity def."""
    bl_idname = "MM_UL_entity_properties"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            split = layout.split(factor=0.4)
            split.prop(item, "key", text="", emboss=False)
            split.prop(item, "value", text="", emboss=False)
        elif self.layout_type == 'GRID':
            layout.label(text=f"{item.key}={item.value}")


class MM_UL_precache(UIList):
    """UIList for precache entries."""
    bl_idname = "MM_UL_precache"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            row.prop(item, "filename", text="", emboss=False)
            row.prop(item, "entry_type", text="")
        elif self.layout_type == 'GRID':
            layout.label(text=item.filename)


class MM_UL_characters(UIList):
    """UIList for character entries."""
    bl_idname = "MM_UL_characters"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            layout.prop(item, "char_name", text="", emboss=False, icon='ARMATURE_DATA')
        elif self.layout_type == 'GRID':
            layout.label(text=item.char_name)


class MM_UL_char_db(UIList):
    """UIList for character database entries with search filtering."""
    bl_idname = "MM_UL_char_db"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            if item.team == 'hero':
                team_icon = 'COMMUNITY'
            elif item.team in ('enemy', 'boss'):
                team_icon = 'GHOST_ENABLED'
            else:
                team_icon = 'OUTLINER_OB_EMPTY'
            row.label(text="", icon=team_icon)
            row.label(text=item.display_name)
            row.label(text=item.skin)
        elif self.layout_type == 'GRID':
            layout.label(text=item.display_name)

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        search = context.scene.mm_char_search.lower()

        flt_flags = [self.bitflag_filter_item] * len(items)
        if search:
            for i, item in enumerate(items):
                if (search not in item.display_name.lower() and
                        search not in item.name_id.lower() and
                        search not in item.team.lower()):
                    flt_flags[i] = 0

        flt_neworder = list(range(len(items)))
        return flt_flags, flt_neworder


class MM_UL_model_db(UIList):
    """UIList for model database entries with search/category filtering."""
    bl_idname = "MM_UL_model_db"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            row.label(text=item.display_name, icon='MESH_CUBE')
            row.label(text=item.category)
        elif self.layout_type == 'GRID':
            layout.label(text=item.display_name)

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        search = context.scene.mm_model_search.lower()
        category = context.scene.mm_model_filter_category

        flt_flags = [self.bitflag_filter_item] * len(items)
        for i, item in enumerate(items):
            if search and search not in item.display_name.lower():
                flt_flags[i] = 0
            elif category and item.category != category:
                flt_flags[i] = 0

        flt_neworder = list(range(len(items)))
        return flt_flags, flt_neworder


class MM_UL_conversations(UIList):
    """UIList for conversations."""
    bl_idname = "MM_UL_conversations"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            layout.prop(item, "conv_name", text="", emboss=False, icon='OUTLINER')
        elif self.layout_type == 'GRID':
            layout.label(text=item.conv_name)


class MM_UL_conv_nodes(UIList):
    """UIList for conversation nodes with tree indentation."""
    bl_idname = "MM_UL_conv_nodes"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            from .conversation import (
                get_flat_display_order, get_node_indicators, parse_speaker_text,
            )

            display = get_flat_display_order(data)
            depth = 0
            display_text = ""
            node_icon = 'BLANK1'
            indicators = None

            if index < len(display):
                node, depth = display[index]
                indicators = get_node_indicators(node)

                if node.node_type == 'START_CONDITION':
                    node_icon = 'PLAY'
                    if node.condition_script:
                        display_text = f"[Start] {node.condition_script}"
                    else:
                        display_text = "[Start Condition]"
                    if node.run_once:
                        display_text += " (once)"

                elif node.node_type == 'PARTICIPANT':
                    node_icon = 'USER'
                    display_text = f"[{node.participant_name}]"

                elif node.node_type == 'LINE':
                    node_icon = 'OUTLINER_OB_FONT'
                    speaker, dialogue = parse_speaker_text(node.text)
                    if speaker:
                        full = f"{speaker}: {dialogue}"
                    else:
                        full = dialogue if dialogue else "(empty line)"
                    display_text = full[:50] + "..." if len(full) > 50 else full

                elif node.node_type == 'RESPONSE':
                    if indicators['is_blank']:
                        node_icon = 'FORWARD'
                        display_text = "(auto-advance)"
                    else:
                        node_icon = 'TRIA_RIGHT'
                        _, cleaned = parse_speaker_text(node.response_text)
                        if not cleaned:
                            cleaned = "(empty response)"
                        display_text = (cleaned[:50] + "..."
                                        if len(cleaned) > 50 else cleaned)

            row = layout.row(align=True)
            for _ in range(depth):
                row.label(text="", icon='BLANK1')

            row.label(text=display_text, icon=node_icon)

            if indicators:
                if indicators['has_sound']:
                    row.label(text="", icon='SOUND')
                if indicators['has_end']:
                    row.label(text="", icon='PANEL_CLOSE')
                if indicators['has_tag_jump']:
                    row.label(text="", icon='LINKED')
                if indicators['has_tag_index']:
                    row.label(text="", icon='BOOKMARKS')
                if indicators['has_faction'] == 'xmen':
                    row.label(text="", icon='COMMUNITY')
                elif indicators['has_faction'] == 'brotherhood':
                    row.label(text="", icon='GHOST_ENABLED')
                if indicators['has_brotherhood_variant']:
                    row.label(text="", icon='FILE_TEXT')

        elif self.layout_type == 'GRID':
            layout.label(text="node")

    def filter_items(self, context, data, propname):
        """Override to show nodes in DFS tree order."""
        from .conversation import get_flat_display_order

        items = getattr(data, propname)
        display = get_flat_display_order(data)

        flt_flags = [0] * len(items)
        flt_neworder = list(range(len(items)))

        id_to_idx = {}
        for i, node in enumerate(items):
            id_to_idx[node.node_id] = i

        new_order = [0] * len(items)
        for display_idx, (node, depth) in enumerate(display):
            coll_idx = id_to_idx.get(node.node_id, -1)
            if coll_idx >= 0:
                flt_flags[coll_idx] = self.bitflag_filter_item
                new_order[coll_idx] = display_idx

        return flt_flags, new_order


class MM_UL_objectives(UIList):
    """UIList for objectives."""
    bl_idname = "MM_UL_objectives"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            # Order number
            row.label(text=f"{index + 1}.")
            # Status icon
            if item.is_optional:
                row.label(text="", icon='LAYER_USED')
            else:
                row.label(text="", icon='LAYER_ACTIVE')
            row.prop(item, "display_text", text="", emboss=False)
            if item.auto_activate:
                row.label(text="", icon='PLAY')
        elif self.layout_type == 'GRID':
            layout.label(text=item.display_text)


class MM_UL_objective_steps(UIList):
    """UIList for objective steps."""
    bl_idname = "MM_UL_objective_steps"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            # Step type icon
            type_icons = {
                'GO_TO': 'TRACKER',
                'DESTROY': 'TRASH',
                'COLLECT': 'PACKAGE',
                'INTERACT': 'HAND',
                'DEFEAT_ALL': 'ARMATURE_DATA',
                'CUSTOM': 'SCRIPT',
            }
            row.label(text="", icon=type_icons.get(item.step_type, 'DOT'))
            # Description or auto-generated text
            if item.description:
                row.label(text=item.description)
            else:
                label = item.step_type.replace('_', ' ').title()
                if item.target_entity:
                    label += f" → {item.target_entity}"
                elif item.scene_object_name:
                    label += f" → {item.scene_object_name}"
                row.label(text=label)
        elif self.layout_type == 'GRID':
            layout.label(text=item.step_type)


# ===========================================================================
# Helper: Property schema renderer
# ===========================================================================

def _find_property(edef, key):
    """Find a custom property by key on an entity def."""
    for prop in edef.properties:
        if prop.key == key:
            return prop
    return None


def _draw_schema_properties(layout, edef):
    """Draw typed property widgets from entity schema, with raw fallback.

    Bool properties use game-correct toggle behavior:
    - default 'false': property present with 'true' = ON, property absent = OFF
    - default 'true': property absent = ON (uses game default), property 'false' = OFF
    """
    from .entity_schemas import get_schema, get_schema_keys

    schema = get_schema(edef.classname)
    schema_keys = get_schema_keys(edef.classname)

    if schema:
        for section in schema['sections']:
            box = layout.box()
            box.label(text=section['label'], icon=section.get('icon', 'PREFERENCES'))

            for prop_def in section['properties']:
                key = prop_def['key']
                prop = _find_property(edef, key)
                schema_default = prop_def.get('default', '')
                current_value = prop.value if prop else schema_default
                label = prop_def.get('label', key)

                if prop_def['type'] == 'bool':
                    is_true = current_value.lower() in ('true', '1', 'yes')
                    row = box.row(align=True)

                    if is_true:
                        # Currently ON → toggle OFF
                        if schema_default.lower() in ('true', '1', 'yes'):
                            # Default is true: set explicit 'false'
                            op = row.operator("mm.set_entity_property",
                                             text=label,
                                             icon='CHECKBOX_HLT',
                                             depress=True)
                            op.entity_name = edef.entity_name
                            op.property_key = key
                            op.property_value = 'false'
                        else:
                            # Default is false: REMOVE property (absent = off)
                            op = row.operator("mm.remove_entity_property_by_key",
                                             text=label,
                                             icon='CHECKBOX_HLT',
                                             depress=True)
                            op.entity_name = edef.entity_name
                            op.property_key = key
                    else:
                        # Currently OFF → toggle ON
                        if schema_default.lower() in ('true', '1', 'yes'):
                            # Default is true: REMOVE property (absent = on by default)
                            op = row.operator("mm.remove_entity_property_by_key",
                                             text=label,
                                             icon='CHECKBOX_DEHLT',
                                             depress=False)
                            op.entity_name = edef.entity_name
                            op.property_key = key
                        else:
                            # Default is false: set 'true'
                            op = row.operator("mm.set_entity_property",
                                             text=label,
                                             icon='CHECKBOX_DEHLT',
                                             depress=False)
                            op.entity_name = edef.entity_name
                            op.property_key = key
                            op.property_value = 'true'

                elif prop_def['type'] == 'enum':
                    row = box.row(align=True)
                    row.label(text=label)
                    if prop:
                        row.prop(prop, "value", text="")
                    else:
                        op = row.operator("mm.set_entity_property", text=current_value or "(none)")
                        op.entity_name = edef.entity_name
                        op.property_key = key
                        op.property_value = prop_def.get('default', '')

                else:
                    # string / int / float — use direct prop editor if property exists
                    is_script = (prop_def['type'] == 'string'
                                 and key.endswith('script'))
                    row = box.row(align=True)
                    row.label(text=label)
                    if prop:
                        row.prop(prop, "value", text="")
                    else:
                        op = row.operator("mm.set_entity_property", text=current_value or "(empty)")
                        op.entity_name = edef.entity_name
                        op.property_key = key
                        op.property_value = prop_def.get('default', '')
                    if is_script:
                        op = row.operator("mm.open_script_editor",
                                          text="", icon='TEXT')
                        op.script_key = key

    # Raw key-value editor for properties NOT covered by schema
    has_extra = False
    for prop in edef.properties:
        if prop.key not in schema_keys:
            has_extra = True
            break

    if has_extra or not schema:
        layout.separator()
        box = layout.box()
        box.label(text="Custom Properties", icon='PROPERTIES')
        _script_keys = {'actscript', 'spawnscript', 'monster_actscript'}
        for i, prop in enumerate(edef.properties):
            if schema and prop.key in schema_keys:
                continue
            row = box.row(align=True)
            row.prop(prop, "key", text="")
            row.prop(prop, "value", text="")
            if prop.key in _script_keys:
                op = row.operator("mm.open_script_editor",
                                  text="", icon='TEXT')
                op.script_key = prop.key
            op = row.operator("mm.remove_entity_property_by_index", text="", icon='X')
            op.index = i

    row = layout.row(align=True)
    row.operator("mm.add_entity_property", icon='ADD', text="Add Property")
    row.operator("mm.apply_defaults", icon='FILE_REFRESH', text="Defaults")


# ===========================================================================
# Root Panel with Tab Bar
# ===========================================================================

class MM_PT_Root(Panel):
    """Map Maker — root panel with tab navigation"""
    bl_label = "Map Maker"
    bl_idname = "MM_PT_Root"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_options = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        icon_id = _get_icon_id()
        if icon_id:
            self.layout.label(icon_value=icon_id)

    def draw(self, context):
        layout = self.layout
        settings = context.scene.mm_settings
        layout.prop(settings, "ui_tab", expand=True)


# ===========================================================================
# Tab: SCENE — Map Settings
# ===========================================================================

class MM_PT_Scene_Identity(Panel):
    """Map identity and world entity settings"""
    bl_label = "Map Identity"
    bl_idname = "MM_PT_Scene_Identity"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "MM_PT_Root"

    @classmethod
    def poll(cls, context):
        return context.scene.mm_settings.ui_tab == 'SCENE'

    def draw(self, context):
        layout = self.layout
        settings = context.scene.mm_settings

        # One-click load of an existing game map (geometry + entities +
        # settings) so it can be edited and rebuilt
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator("mm.import_full_map", icon='IMPORT',
                     text="Import Full Map (.igb)")
        mp_row = layout.row(align=True)
        mp_row.operator("mm.import_motionpath", icon='CURVE_PATH',
                        text="Import Motion Path(s)")
        mp_row.operator("mm.export_motionpath", icon='EXPORT', text="Export")

        # Game preview: play the scene as it behaves in-game
        sim_row = layout.row(align=True)
        sim_row.scale_y = 1.2
        if context.scene.get("mm_simulating"):
            sim_row.operator("mm.simulate_stop", icon='PAUSE',
                             text="Stop Simulation")
        else:
            sim_row.operator("mm.simulate_play", icon='PLAY',
                             text="Simulate Map")
        layout.operator("mm.game_shading", icon='SHADING_RENDERED',
                        text="Game Shading")
        layout.separator()

        col = layout.column(align=True)
        col.prop(settings, "map_name")
        col.prop(settings, "map_path")
        col.separator()
        row = col.row(align=True)
        row.prop(settings, "act")
        row.prop(settings, "level")
        col.prop(settings, "zone_script")


class MM_PT_Scene_Environment(Panel):
    """Environment settings — light, sound, flags"""
    bl_label = "Environment"
    bl_idname = "MM_PT_Scene_Environment"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "MM_PT_Root"

    @classmethod
    def poll(cls, context):
        return context.scene.mm_settings.ui_tab == 'SCENE'

    def draw(self, context):
        layout = self.layout
        settings = context.scene.mm_settings

        col = layout.column(align=True)
        col.prop(settings, "soundfile")
        col.prop(settings, "partylight")
        col.prop(settings, "partylightradius")

        # NextGen overrides (MUA)
        box = layout.box()
        box.label(text="NextGen Overrides (MUA)", icon='SHADING_RENDERED')
        col_ng = box.column(align=True)
        col_ng.prop(settings, "next_gen_partylight")
        col_ng.prop(settings, "next_gen_partylightradius")

        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(settings, "combatlocked")
        row.prop(settings, "nosave")
        col.prop(settings, "bigconvmap")


class MM_PT_Scene_Lights(Panel):
    """Scene lights — IGB static lights for the map"""
    bl_label = "Lights"
    bl_idname = "MM_PT_Scene_Lights"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "MM_PT_Root"

    @classmethod
    def poll(cls, context):
        return context.scene.mm_settings.ui_tab == 'SCENE'

    def draw(self, context):
        layout = self.layout
        settings = context.scene.mm_settings

        # --- Collect scene lights (excluding mm_preview lights) ---
        lights = []
        for obj in context.scene.objects:
            if obj.type == 'LIGHT' and not obj.get("mm_preview"):
                lights.append(obj)
        lights.sort(key=lambda o: o.name)

        # --- Add / Remove / Select buttons ---
        row = layout.row(align=True)
        row.operator("mm.add_light", text="", icon='ADD')
        row.operator("mm.remove_light", text="", icon='REMOVE')
        row.separator()
        row.operator("mm.select_light", text="", icon='RESTRICT_SELECT_OFF')
        row.separator()
        row.label(text=f"{len(lights)} light(s)")

        # --- Light list (manual draw, not UIList) ---
        if lights:
            box = layout.box()
            col = box.column(align=True)
            idx = settings.active_light_index

            for i, obj in enumerate(lights):
                light = obj.data
                # Type icon
                type_icons = {
                    'SUN': 'LIGHT_SUN',
                    'POINT': 'LIGHT_POINT',
                    'SPOT': 'LIGHT_SPOT',
                    'AREA': 'LIGHT_AREA',
                }
                icon = type_icons.get(light.type, 'LIGHT')

                row = col.row(align=True)
                # Highlight active row
                if i == idx:
                    row.alert = True
                op = row.operator("mm.select_light_by_index", text="",
                                  icon=icon, emboss=(i == idx))
                op.index = i
                row.label(text=obj.name)
                # Visibility toggle
                row.prop(obj, "hide_viewport", text="", icon='HIDE_OFF',
                         emboss=False)

        # --- Details for selected light ---
        if lights and 0 <= settings.active_light_index < len(lights):
            obj = lights[settings.active_light_index]
            light = obj.data

            layout.separator()
            box = layout.box()
            box.label(text="Properties", icon='PROPERTIES')

            col = box.column(align=True)
            col.prop(light, "type", text="Type")
            col.prop(light, "color", text="")
            col.prop(light, "energy", text="Energy")
            col.prop(light, "use_shadow", text="Shadows")

            # Spot-specific
            if light.type == 'SPOT':
                col.separator()
                col.label(text="Spot Settings:", icon='LIGHT_SPOT')
                col.prop(light, "spot_size", text="Cone Angle")
                col.prop(light, "spot_blend", text="Blend")

            # Position
            col = box.column(align=True)
            col.label(text="Position:")
            col.prop(obj, "location", text="")

        # --- Preset buttons ---
        layout.separator()
        row = layout.row(align=True)
        row.label(text="Presets:", icon='LIGHT')
        row = layout.row(align=True)
        for preset_key in ('WARM', 'COOL', 'RED', 'GREEN'):
            op = row.operator("mm.add_light_preset", text=preset_key.capitalize())
            op.preset = preset_key


class MM_PT_Scene_Paths(Panel):
    """Build paths and options"""
    bl_label = "Paths & Options"
    bl_idname = "MM_PT_Scene_Paths"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "MM_PT_Root"

    @classmethod
    def poll(cls, context):
        return context.scene.mm_settings.ui_tab == 'SCENE'

    def draw(self, context):
        layout = self.layout
        settings = context.scene.mm_settings

        col = layout.column(align=True)
        col.prop(settings, "output_dir")
        col.prop(settings, "game_data_dir")
        if not settings.game_data_dir:
            col.label(text="Needed for audio, NPCs, conversations",
                      icon='ERROR')
        col.operator("mm.pick_game_dir", icon='FILE_FOLDER')
        col.separator()
        col.prop(settings, "automap_path")
        col.label(text="Leave blank for auto: automaps/{map_path}/{map_name}", icon='INFO')
        col.separator()
        col.prop(settings, "show_previews", toggle=True)


# ===========================================================================
# Tab: PLACE — Entity Placement
# ===========================================================================

class MM_PT_Place_QuickAdd(Panel):
    """One-click gameplay asset placement"""
    bl_label = "Quick Add"
    bl_idname = "MM_PT_Place_QuickAdd"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "MM_PT_Root"

    @classmethod
    def poll(cls, context):
        return context.scene.mm_settings.ui_tab == 'PLACE'

    # Map category labels to filter enum values
    _CAT_FILTER_MAP = {
        "Hub & Terminals": 'HUB',
        "Triggers & Zones": 'TRIGGERS',
        "Enemies & NPCs": 'ENEMIES',
        "Environment & FX": 'ENV',
        "Doors & Movers": 'DOORS',
        "Pickups & Items": 'PICKUPS',
        "Camera": 'CAMERA',
        "MUA Only": 'MUA',
    }

    def draw(self, context):
        layout = self.layout
        settings = context.scene.mm_settings

        # Filter tabs
        layout.prop(settings, "quick_add_filter", expand=True)

        from .entity_defs import ENTITY_PRESETS, PRESET_CATEGORIES

        # Build lookup: preset_id -> preset tuple
        preset_map = {p[0]: p for p in ENTITY_PRESETS}
        active_filter = settings.quick_add_filter

        for cat_label, cat_icon, cat_game, preset_ids in PRESET_CATEGORIES:
            # Skip categories that don't match the filter
            if active_filter != 'ALL':
                cat_key = self._CAT_FILTER_MAP.get(cat_label, '')
                if cat_key != active_filter:
                    continue

            valid = [pid for pid in preset_ids if pid in preset_map]
            if not valid:
                continue

            box = layout.box()
            header = box.row()
            header.label(text=cat_label, icon=cat_icon)
            if cat_game == 'MUA':
                header.label(text="MUA", icon='COLORSET_13_VEC')

            col = box.column(align=True)
            for i in range(0, len(valid), 2):
                row = col.row(align=True)
                p = preset_map[valid[i]]
                op = row.operator("mm.place_preset", text=p[1])
                op.preset_id = valid[i]
                if i + 1 < len(valid):
                    p2 = preset_map[valid[i + 1]]
                    op2 = row.operator("mm.place_preset", text=p2[1])
                    op2.preset_id = valid[i + 1]


class MM_PT_Place_Entities(Panel):
    """Entity definitions and placement"""
    bl_label = "Entity Definitions"
    bl_idname = "MM_PT_Place_Entities"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "MM_PT_Root"

    @classmethod
    def poll(cls, context):
        return context.scene.mm_settings.ui_tab == 'PLACE'

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Entity Definitions list
        row = layout.row()
        row.template_list(
            "MM_UL_entity_defs", "",
            scene, "mm_entity_defs",
            scene, "mm_entity_defs_index",
            rows=4,
        )
        col = row.column(align=True)
        col.operator("mm.add_entity_def", icon='ADD', text="")
        col.operator("mm.remove_entity_def", icon='REMOVE', text="")

        # Selected entity details
        if 0 <= scene.mm_entity_defs_index < len(scene.mm_entity_defs):
            edef = scene.mm_entity_defs[scene.mm_entity_defs_index]

            box = layout.box()
            col = box.column(align=True)
            col.prop(edef, "entity_name")
            col.prop(edef, "classname")

            if edef.classname == 'monsterspawnerent':
                col.prop(edef, "character")
                col.prop(edef, "monster_name")

            if edef.classname in ('physent', 'doorent', 'gameent', 'actionent') or edef.model:
                col.prop(edef, "model")

            col.prop(edef, "nocollide")

            # Schema-based properties
            col.separator()
            _draw_schema_properties(box, edef)

        # Placement controls
        layout.separator()
        col = layout.column(align=True)
        col.scale_y = 1.3
        col.operator("mm.place_entity", icon='EMPTY_SINGLE_ARROW')

        row = layout.row(align=True)
        row.operator("mm.select_by_type", icon='RESTRICT_SELECT_OFF')
        row.operator("mm.remove_entity_instance", icon='X')

        # Instance count
        count = 0
        if "[MapMaker] Entities" in bpy.data.collections:
            count = sum(1 for o in bpy.data.collections["[MapMaker] Entities"].objects
                        if not o.get("mm_preview"))
        layout.label(text=f"Placed instances: {count}", icon='OBJECT_DATA')

        # Actor preview controls
        row = layout.row(align=True)
        row.operator("mm.refresh_previews", icon='FILE_REFRESH', text="Refresh Previews")
        row.operator("mm.strip_previews", icon='TRASH', text="Strip All")


class MM_PT_Place_CharacterDB(Panel):
    """Character Database — browse NPCs and heroes"""
    bl_label = "Character Database"
    bl_idname = "MM_PT_Place_CharacterDB"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "MM_PT_Root"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.scene.mm_settings.ui_tab == 'PLACE'

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        row = layout.row(align=True)
        row.operator("mm.load_char_db", icon='FILE_REFRESH', text="Load Character DB")
        row.label(text=f"({len(scene.mm_char_db)} chars)")

        if len(scene.mm_char_db) > 0:
            layout.prop(scene, "mm_char_search", icon='VIEWZOOM')

            row = layout.row()
            row.template_list(
                "MM_UL_char_db", "",
                scene, "mm_char_db",
                scene, "mm_char_db_index",
                rows=6,
            )

            if 0 <= scene.mm_char_db_index < len(scene.mm_char_db):
                entry = scene.mm_char_db[scene.mm_char_db_index]
                box = layout.box()
                col = box.column(align=True)
                col.label(text=f"Name: {entry.display_name}", icon='ARMATURE_DATA')
                col.label(text=f"ID: {entry.name_id}")
                col.label(text=f"Team: {entry.team}")
                col.label(text=f"Skin: {entry.skin}")
                col.label(text=f"Source: {entry.source}")
                if entry.characteranims:
                    col.label(text=f"Anims: {entry.characteranims}")

                col.separator()
                col.scale_y = 1.3
                col.operator("mm.quick_place_character", icon='EMPTY_SINGLE_ARROW',
                             text="Quick Place at Cursor")
                col.scale_y = 1.0
                col.operator("mm.pick_character", icon='EYEDROPPER',
                             text="Pick for Active Entity Def")

        # NPC Stat Editor
        layout.separator()
        box = layout.box()
        box.label(text="NPC Stat Editor", icon='PREFERENCES')
        row = box.row(align=True)
        op = row.operator("mm.open_npcstat_editor", text="NPC Stats",
                          icon='WINDOW')
        op.stat_file = 'npcstat'
        op = row.operator("mm.open_npcstat_editor", text="Hero Stats",
                          icon='COMMUNITY')
        op.stat_file = 'herostat'


class MM_PT_Place_ModelBrowser(Panel):
    """Model Browser — browse game model assets"""
    bl_label = "Model Browser"
    bl_idname = "MM_PT_Place_ModelBrowser"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "MM_PT_Root"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.scene.mm_settings.ui_tab == 'PLACE'

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        row = layout.row(align=True)
        row.operator("mm.scan_models", icon='FILE_REFRESH', text="Scan Models")
        row.label(text=f"({len(scene.mm_model_db)} models)")

        if len(scene.mm_model_db) > 0:
            col = layout.column(align=True)
            col.prop(scene, "mm_model_search", icon='VIEWZOOM')
            col.prop(scene, "mm_model_filter_category", icon='FILE_FOLDER', text="Category")

            row = layout.row()
            row.template_list(
                "MM_UL_model_db", "",
                scene, "mm_model_db",
                scene, "mm_model_db_index",
                rows=6,
            )

            if 0 <= scene.mm_model_db_index < len(scene.mm_model_db):
                entry = scene.mm_model_db[scene.mm_model_db_index]
                box = layout.box()
                col = box.column(align=True)
                col.label(text=f"Name: {entry.display_name}", icon='MESH_CUBE')
                col.label(text=f"Path: {entry.rel_path}")
                col.label(text=f"Category: {entry.category}")

                col.separator()
                col.scale_y = 1.3
                col.operator("mm.quick_place_model", icon='EMPTY_SINGLE_ARROW',
                             text="Quick Place at Cursor")
                col.scale_y = 1.0
                col.operator("mm.pick_model", icon='EYEDROPPER',
                             text="Pick for Active Entity Def")
                col.operator("mm.import_model_asset", icon='ASSET_MANAGER',
                             text="Import as Asset")

            layout.separator()
            box = layout.box()
            box.label(text="Asset Library", icon='ASSET_MANAGER')
            col = box.column(align=True)
            col.operator("mm.import_category_assets", icon='IMPORT',
                         text="Import Category as Assets")
            col.operator("mm.detect_placed_assets", icon='VIEWZOOM',
                         text="Detect Placed Assets")


# ===========================================================================
# Tab: LOGIC — Objectives, Conversations, Precache
# ===========================================================================

class MM_PT_Logic_Objectives(Panel):
    """Objectives — visual objective builder"""
    bl_label = "Objectives"
    bl_idname = "MM_PT_Logic_Objectives"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "MM_PT_Root"

    @classmethod
    def poll(cls, context):
        return context.scene.mm_settings.ui_tab == 'LOGIC'

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Objective list
        row = layout.row()
        row.template_list(
            "MM_UL_objectives", "",
            scene, "mm_objectives",
            scene, "mm_objectives_index",
            rows=4,
        )
        col = row.column(align=True)
        col.operator("mm.add_objective", icon='ADD', text="")
        col.operator("mm.remove_objective", icon='REMOVE', text="")
        col.separator()
        op = col.operator("mm.move_objective", icon='TRIA_UP', text="")
        op.direction = 'UP'
        op = col.operator("mm.move_objective", icon='TRIA_DOWN', text="")
        op.direction = 'DOWN'

        # Selected objective details
        if not (0 <= scene.mm_objectives_index < len(scene.mm_objectives)):
            return

        objective = scene.mm_objectives[scene.mm_objectives_index]

        box = layout.box()
        col = box.column(align=True)
        col.prop(objective, "obj_name")
        col.prop(objective, "display_text")
        row = col.row(align=True)
        row.prop(objective, "auto_activate", toggle=True)
        row.prop(objective, "is_optional", toggle=True)

        if not objective.auto_activate:
            col.prop(objective, "trigger_entity")

        col.prop(objective, "next_objective")

        # Steps
        layout.separator()
        box = layout.box()
        box.label(text="Steps", icon='LINENUMBERS_ON')

        row = box.row()
        row.template_list(
            "MM_UL_objective_steps", "",
            objective, "steps",
            objective, "steps_index",
            rows=3,
        )
        col = row.column(align=True)
        col.operator("mm.add_objective_step", icon='ADD', text="")
        col.operator("mm.remove_objective_step", icon='REMOVE', text="")

        # Selected step details
        if 0 <= objective.steps_index < len(objective.steps):
            step = objective.steps[objective.steps_index]

            sbox = box.box()
            col = sbox.column(align=True)
            col.prop(step, "step_type")
            col.prop(step, "description")

            if step.step_type == 'GO_TO':
                col.separator()
                col.prop(step, "use_scene_object")
                if step.use_scene_object:
                    row = col.row(align=True)
                    row.prop(step, "scene_object_name", text="Object")
                    row.operator("mm.select_step_target", text="", icon='RESTRICT_SELECT_OFF')
                else:
                    col.prop(step, "target_location")
                col.prop(step, "radius")
                col.operator("mm.place_objective_marker", icon='SPHERE',
                             text="Place Marker at Cursor")

            elif step.step_type in ('DESTROY', 'INTERACT', 'DEFEAT_ALL'):
                col.prop(step, "target_entity")

            elif step.step_type == 'COLLECT':
                col.prop(step, "target_entity")
                col.prop(step, "count")

            elif step.step_type == 'CUSTOM':
                col.prop(step, "custom_check_script")

            col.separator()
            col.prop(step, "completion_script")


class MM_PT_Logic_Conversations(Panel):
    """Conversations — dialogue tree editor"""
    bl_label = "Conversations"
    bl_idname = "MM_PT_Logic_Conversations"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "MM_PT_Root"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.scene.mm_settings.ui_tab == 'LOGIC'

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Conversation list
        row = layout.row()
        row.template_list(
            "MM_UL_conversations", "",
            scene, "mm_conversations",
            scene, "mm_conversations_index",
            rows=3,
        )
        col = row.column(align=True)
        col.operator("mm.add_conversation", icon='ADD', text="")
        col.operator("mm.remove_conversation", icon='REMOVE', text="")

        # Dialog Maker — quick conversation creation
        row = layout.row(align=True)
        row.operator("mm.quick_dialog", icon='GREASEPENCIL', text="Quick Dialog")
        row.operator("mm.quick_dialog_from_text", icon='TEXT', text="From Text Block")

        if not (0 <= scene.mm_conversations_index < len(scene.mm_conversations)):
            return

        conv = scene.mm_conversations[scene.mm_conversations_index]

        # Conversation settings
        box = layout.box()
        col = box.column(align=True)
        col.prop(conv, "conv_name")
        col.prop(conv, "conv_path")
        col.separator()
        col.prop(conv, "hud_head", icon='USER')
        col.label(text="e.g. hud_head_1101 (skin number)", icon='INFO')

        # Edit Conversation button
        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator("mm.open_convo_editor", text="Edit Conversation", icon='WINDOW')
        row.operator("mm.extract_hud_heads", text="", icon='IMAGE_DATA')

        # Node tree
        layout.separator()
        box = layout.box()
        box.label(text="Dialogue Tree", icon='OUTLINER')

        row = box.row()
        row.template_list(
            "MM_UL_conv_nodes", "",
            conv, "nodes",
            conv, "nodes_index",
            rows=10,
        )

        # Add node buttons
        col = box.column(align=True)
        col.operator("mm.add_start_condition", icon='PLAY', text="Add Start Condition")
        row = col.row(align=True)
        row.operator("mm.add_dialogue_exchange", icon='PLUS', text="Add Line+Response")
        op_line = row.operator("mm.add_conv_node", icon='OUTLINER_OB_FONT', text="Line")
        op_line.node_type = 'LINE'
        op_resp = row.operator("mm.add_conv_node", icon='TRIA_RIGHT', text="Response")
        op_resp.node_type = 'RESPONSE'
        col.operator("mm.remove_conv_node", icon='REMOVE', text="Remove Node")

        # Import/Export/Link
        layout.separator()
        row = layout.row(align=True)
        row.operator("mm.export_conversation", icon='EXPORT', text="Export")
        row.operator("mm.import_conversation", icon='IMPORT', text="Import")
        layout.operator("mm.link_conversation_to_npc", icon='LINKED',
                         text="Link to Active Entity Def")


class MM_PT_Logic_Conversations_Preview(Panel):
    """Game-style dialogue preview"""
    bl_label = "Dialogue Preview"
    bl_idname = "MM_PT_Logic_Conversations_Preview"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "MM_PT_Logic_Conversations"

    @classmethod
    def poll(cls, context):
        scene = context.scene
        if scene.mm_settings.ui_tab != 'LOGIC':
            return False
        if not (0 <= scene.mm_conversations_index < len(scene.mm_conversations)):
            return False
        conv = scene.mm_conversations[scene.mm_conversations_index]
        return len(conv.nodes) > 0

    def draw(self, context):
        from .conversation import (
            get_flat_display_order, get_conversation_context,
        )

        layout = self.layout
        scene = context.scene
        conv = scene.mm_conversations[scene.mm_conversations_index]

        display = get_flat_display_order(conv)
        if not (0 <= conv.nodes_index < len(display)):
            layout.label(text="Select a dialogue node", icon='INFO')
            return

        node, depth = display[conv.nodes_index]
        ctx = get_conversation_context(conv, node)

        if ctx['mode'] in ('start_condition', 'participant'):
            box = layout.box()
            if ctx['mode'] == 'start_condition':
                box.label(text="Start Condition", icon='PLAY')
                if node.condition_script:
                    box.label(text=f"Script: {node.condition_script}")
                else:
                    box.label(text="(no condition script)")
                if node.run_once:
                    box.label(text="Runs once", icon='CHECKBOX_HLT')
            else:
                box.label(text=f"Participant: {node.participant_name}",
                          icon='USER')
            return

        if ctx['mode'] == 'line':
            self._draw_npc_speech(layout, conv, ctx)
        elif ctx['mode'] == 'response':
            self._draw_response_view(layout, conv, ctx)

    def _draw_npc_speech(self, layout, conv, ctx):
        speech_box = layout.box()
        header = speech_box.row()
        header.scale_y = 1.4
        speaker = ctx['speaker'] or "NPC"
        if conv.hud_head:
            header.label(text="", icon='USER')
        header.label(text=speaker)
        if ctx['has_sound']:
            header.label(text="", icon='SOUND')

        speech_box.separator()
        dialogue = ctx['dialogue']
        if dialogue:
            for line in _wrap_text(dialogue, 55):
                speech_box.label(text=f"  {line}")
        else:
            speech_box.label(text="  (no dialogue text)")

        if ctx['dialogue_b']:
            speech_box.separator()
            var_box = speech_box.box()
            var_box.label(text="Brotherhood variant:", icon='GHOST_ENABLED')
            for line in _wrap_text(ctx['dialogue_b'], 50):
                var_box.label(text=f"  {line}")

        if ctx['responses']:
            layout.separator()
            self._draw_responses(layout, ctx['responses'])

    def _draw_response_view(self, layout, conv, ctx):
        if ctx['parent_speaker'] or ctx['parent_dialogue']:
            npc_box = layout.box()
            header = npc_box.row()
            header.scale_y = 1.2
            speaker = ctx['parent_speaker'] or "NPC"
            header.label(text=speaker, icon='USER')

            if ctx['parent_dialogue']:
                for line in _wrap_text(ctx['parent_dialogue'], 55):
                    npc_box.label(text=f"  {line}")

        if ctx['responses']:
            layout.separator()
            self._draw_responses(layout, ctx['responses'])

        if ctx['follow_up_speaker'] or ctx['follow_up_dialogue']:
            layout.separator()
            follow_box = layout.box()
            header = follow_box.row()
            header.scale_y = 1.2
            fu_speaker = ctx['follow_up_speaker'] or "NPC"
            header.label(text=f"{fu_speaker} responds:", icon='USER')
            if ctx['has_sound']:
                header.label(text="", icon='SOUND')

            if ctx['follow_up_dialogue']:
                for line in _wrap_text(ctx['follow_up_dialogue'], 55):
                    follow_box.label(text=f"  {line}")

    def _draw_responses(self, layout, responses):
        resp_box = layout.box()
        resp_box.label(text="Player Choices:", icon='TRIA_RIGHT')
        resp_box.separator()

        for i, resp in enumerate(responses):
            row = resp_box.row(align=True)

            if resp.get('is_selected'):
                row.alert = True
                icon = 'RADIOBUT_ON'
            else:
                icon = 'RADIOBUT_OFF'

            if resp.get('is_blank'):
                row.label(text=f"  {i + 1}. (auto-advance)", icon='FORWARD')
            else:
                row.label(text=f"  {i + 1}. {resp['text']}", icon=icon)

            faction = resp.get('faction')
            if faction == 'xmen':
                row.label(text="X", icon='COMMUNITY')
            elif faction == 'brotherhood':
                row.label(text="B", icon='GHOST_ENABLED')
            if resp.get('is_end'):
                row.label(text="", icon='PANEL_CLOSE')
            if resp.get('tag_jump'):
                row.label(text="", icon='LINKED')


class MM_PT_Logic_Conversations_NodeProps(Panel):
    """Selected node properties editor"""
    bl_label = "Node Properties"
    bl_idname = "MM_PT_Logic_Conversations_NodeProps"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "MM_PT_Logic_Conversations"

    @classmethod
    def poll(cls, context):
        scene = context.scene
        if scene.mm_settings.ui_tab != 'LOGIC':
            return False
        if not (0 <= scene.mm_conversations_index < len(scene.mm_conversations)):
            return False
        conv = scene.mm_conversations[scene.mm_conversations_index]
        from .conversation import get_flat_display_order
        display = get_flat_display_order(conv)
        return 0 <= conv.nodes_index < len(display)

    def draw(self, context):
        from .conversation import get_flat_display_order

        layout = self.layout
        scene = context.scene
        conv = scene.mm_conversations[scene.mm_conversations_index]

        display = get_flat_display_order(conv)
        node, depth = display[conv.nodes_index]

        box = layout.box()
        box.label(text=f"Selected: {node.node_type}", icon='PREFERENCES')

        col = box.column(align=True)

        if node.node_type == 'START_CONDITION':
            col.prop(node, "condition_script")
            col.prop(node, "run_once")

        elif node.node_type == 'PARTICIPANT':
            col.prop(node, "participant_name")

        elif node.node_type == 'LINE':
            col.prop(node, "text")
            col.prop(node, "sound_to_play")
            col.separator()
            col.label(text="Brotherhood Variants:")
            col.prop(node, "text_b")
            col.prop(node, "sound_to_play_b")
            col.separator()
            col.prop(node, "tag_index")

        elif node.node_type == 'RESPONSE':
            col.prop(node, "response_text")
            col.prop(node, "chosen_script")
            col.prop(node, "script_file")
            col.prop(node, "conversation_end")
            col.separator()
            col.label(text="Branching:")
            col.prop(node, "tag_jump")
            col.prop(node, "tag_index")
            col.separator()
            col.label(text="Faction Gating:")
            row = col.row(align=True)
            row.prop(node, "only_if_xman", toggle=True)
            row.prop(node, "only_if_brotherhood", toggle=True)


class MM_PT_Logic_Precache(Panel):
    """Precache entries and character list"""
    bl_label = "Precache & Characters"
    bl_idname = "MM_PT_Logic_Precache"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "MM_PT_Root"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.scene.mm_settings.ui_tab == 'LOGIC'

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Precache
        box = layout.box()
        box.label(text="Precache", icon='FILE_CACHE')
        row = box.row()
        row.template_list(
            "MM_UL_precache", "",
            scene, "mm_precache",
            scene, "mm_precache_index",
            rows=4,
        )
        col = row.column(align=True)
        col.operator("mm.add_precache", icon='ADD', text="")
        col.operator("mm.remove_precache", icon='REMOVE', text="")

        box.operator("mm.scan_precache", icon='VIEWZOOM', text="Auto-Scan Precache")
        box.label(text=f"{len(scene.mm_precache)} entries", icon='INFO')

        # Characters (CHRB)
        layout.separator()
        box = layout.box()
        box.label(text="Characters (CHRB)", icon='ARMATURE_DATA')
        row = box.row()
        row.template_list(
            "MM_UL_characters", "",
            scene, "mm_characters",
            scene, "mm_characters_index",
            rows=3,
        )
        col = row.column(align=True)
        col.operator("mm.add_character", icon='ADD', text="")
        col.operator("mm.remove_character", icon='REMOVE', text="")

        box.operator("mm.scan_characters", icon='VIEWZOOM', text="Auto-Scan Characters")

        if 0 <= scene.mm_characters_index < len(scene.mm_characters):
            ch = scene.mm_characters[scene.mm_characters_index]
            sbox = box.box()
            sbox.label(text=f"Selected: {ch.char_name}", icon='ARMATURE_DATA')
            row = sbox.row(align=True)
            row.operator("mm.rename_character", icon='SORTALPHA', text="Rename")
            row.operator("mm.replace_character", icon='FILE_REFRESH', text="Replace from DB")

        box.label(text=f"{len(scene.mm_characters)} characters", icon='ARMATURE_DATA')


# ===========================================================================
# Tab: BUILD — Navigation, Compile, Deploy
# ===========================================================================

class MM_PT_Build_Navigation(Panel):
    """Navigation Mesh generation"""
    bl_label = "Navigation Mesh"
    bl_idname = "MM_PT_Build_Navigation"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "MM_PT_Root"

    @classmethod
    def poll(cls, context):
        return context.scene.mm_settings.ui_tab == 'BUILD'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        settings = scene.mm_settings

        layout.prop(settings, "nav_cellsize")

        box = layout.box()
        box.label(text="Advanced", icon='PREFERENCES')
        col = box.column(align=True)
        col.prop(settings, "nav_max_slope", text="Max Slope")
        col.prop(settings, "nav_multi_layer")
        if settings.nav_multi_layer:
            col.prop(settings, "nav_layer_separation", text="Layer Gap")

        layout.separator()
        col = layout.column(align=True)
        col.scale_y = 1.2
        col.operator("mm.generate_navmesh", icon='MESH_GRID')
        row = col.row(align=True)
        row.operator("mm.visualize_navmesh", icon='SHADING_WIRE')
        row.operator("mm.clear_navmesh", text="", icon='TRASH')

        if "mm_nav_cells" in scene:
            import ast
            try:
                cells = ast.literal_eval(scene["mm_nav_cells"])
                layout.label(text=f"Stored: {len(cells)} nav cells", icon='INFO')
            except Exception:
                layout.label(text="Nav cell data invalid", icon='ERROR')
        else:
            layout.label(text="No nav cells generated yet", icon='INFO')


class MM_PT_Build_IGB(Panel):
    """IGB map file export settings"""
    bl_label = "IGB Export"
    bl_idname = "MM_PT_Build_IGB"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "MM_PT_Root"

    @classmethod
    def poll(cls, context):
        return context.scene.mm_settings.ui_tab == 'BUILD'

    def draw_header(self, context):
        self.layout.prop(context.scene.mm_settings, "build_export_igb", text="")

    def draw(self, context):
        layout = self.layout
        settings = context.scene.mm_settings
        layout.active = settings.build_export_igb

        layout.prop(settings, "build_texture_format")
        layout.prop(settings, "build_collision_source")
        layout.prop(settings, "build_export_lights")


class MM_PT_Build_Compile(Panel):
    """Compile and deploy game files"""
    bl_label = "Compile & Deploy"
    bl_idname = "MM_PT_Build_Compile"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "MM_PT_Root"

    @classmethod
    def poll(cls, context):
        return context.scene.mm_settings.ui_tab == 'BUILD'

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        from .operators import MM_OT_build_all

        col = layout.column(align=True)
        col.scale_y = 1.2
        col.operator("mm.generate_xml", icon='FILE_TEXT', text="Generate XML")
        col.operator("mm.compile_xmlb", icon='MODIFIER', text="Compile XMLB")
        col.separator()
        col.scale_y = 1.4
        if MM_OT_build_all._building:
            col.enabled = False
            col.operator("mm.build_all", icon='SORTTIME',
                         text="Building... (async)")
        else:
            col.operator("mm.build_all", icon='PLAY', text="Build All")

        if not scene.mm_settings.output_dir:
            layout.label(text="Set Output Directory in Scene tab", icon='ERROR')


class MM_PT_Build_Validation(Panel):
    """Pre-build validation results"""
    bl_label = "Validation"
    bl_idname = "MM_PT_Build_Validation"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "MM_PT_Root"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.scene.mm_settings.ui_tab == 'BUILD'

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.operator("mm.run_validation", icon='CHECKMARK', text="Run Validation")

        # Show cached validation results if available
        results_str = scene.get("mm_validation_results", "")
        if results_str:
            import json
            try:
                results = json.loads(results_str)
            except Exception:
                results = []

            if not results:
                layout.label(text="All checks passed", icon='CHECKMARK')
            else:
                for level, message in results:
                    if level == 'ERROR':
                        icon = 'ERROR'
                    elif level == 'WARNING':
                        icon = 'INFO'
                    else:
                        icon = 'DOT'
                    layout.label(text=message, icon=icon)


class MM_PT_Build_Collision(Panel):
    """Collision preview"""
    bl_label = "Collision Preview"
    bl_idname = "MM_PT_Build_Collision"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "MM_PT_Root"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.scene.mm_settings.ui_tab == 'BUILD'

    def draw(self, context):
        layout = self.layout

        col = layout.column(align=True)
        op = col.operator("mm.visualize_colliders", icon='SHADING_WIRE',
                          text="Preview Colliders")
        op.source = 'COLLIDERS'
        op = col.operator("mm.visualize_colliders", icon='MESH_DATA',
                          text="Preview Visual Mesh Collision")
        op.source = 'VISUAL'

        coll = bpy.data.collections.get("Colliders")
        if coll:
            mesh_count = sum(1 for o in coll.objects if o.type == 'MESH')
            col.label(text=f"Colliders collection: {mesh_count} mesh(es)", icon='INFO')
        else:
            col.label(text="No 'Colliders' collection found", icon='INFO')


class MM_PT_Build_Automap(Panel):
    """Automap import/export"""
    bl_label = "Automap"
    bl_idname = "MM_PT_Build_Automap"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "MM_PT_Root"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.scene.mm_settings.ui_tab == 'BUILD'

    def draw(self, context):
        layout = self.layout

        col = layout.column(align=True)
        col.operator("mm.import_zam", icon='IMPORT', text="Import ZAM")
        col.operator("mm.export_zam", icon='EXPORT', text="Export ZAM")

        layout.separator()
        box = layout.box()
        box.label(text="Make Automap", icon='IMAGE_DATA')
        col = box.column(align=True)
        col.label(text="Create from floor geometry:")
        col.operator("mm.make_automap", icon='MESH_GRID',
                     text="Generate Automap Mesh")
        col.label(text="Uses selected mesh as floor reference", icon='INFO')


# ===========================================================================
# Selection Inspector — appears on ANY tab when entity Empty is selected
# ===========================================================================

class MM_PT_SelectionInspector(Panel):
    """Inline inspector for the selected entity instance"""
    bl_label = "Selected Entity"
    bl_idname = "MM_PT_SelectionInspector"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IGB Tools"
    bl_parent_id = "MM_PT_Root"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and "mm_entity_type" in obj and not obj.get("mm_preview")

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        etype = obj["mm_entity_type"]
        classname = obj.get("mm_classname", "")

        # Header
        row = layout.row()
        row.label(text=f"{obj.name}", icon='EMPTY_SINGLE_ARROW')
        row.label(text=classname)

        # Find entity def
        edef = None
        for ed in context.scene.mm_entity_defs:
            if ed.entity_name == etype:
                edef = ed
                break

        if not edef:
            layout.label(text=f"Entity def '{etype}' not found", icon='ERROR')
            return

        # Show key fields
        box = layout.box()
        col = box.column(align=True)
        col.prop(edef, "classname")

        if edef.classname == 'monsterspawnerent':
            col.prop(edef, "character")
            col.prop(edef, "monster_name")

        if edef.model:
            col.prop(edef, "model")

        col.prop(edef, "nocollide")

        # Instance-specific: extents
        if "mm_extents" in obj:
            col.separator()
            col.label(text=f"Extents: {obj['mm_extents']}", icon='MESH_CUBE')

        # Schema properties (compact view)
        col.separator()
        _draw_schema_properties(box, edef)


# ===========================================================================
# Helper: text wrapping
# ===========================================================================

def _wrap_text(text, width=60):
    """Word-wrap text for Blender labels."""
    if not text:
        return [""]

    words = text.split()
    lines = []
    current_line = ""

    for word in words:
        if current_line and len(current_line) + 1 + len(word) > width:
            lines.append(current_line)
            current_line = word
        else:
            current_line = f"{current_line} {word}" if current_line else word

    if current_line:
        lines.append(current_line)

    return lines if lines else [""]


# ===========================================================================
# Registration
# ===========================================================================

_classes = (
    # UILists
    MM_UL_entity_defs,
    MM_UL_entity_properties,
    MM_UL_precache,
    MM_UL_characters,
    MM_UL_char_db,
    MM_UL_model_db,
    MM_UL_conversations,
    MM_UL_conv_nodes,
    MM_UL_objectives,
    MM_UL_objective_steps,
    # Root panel
    MM_PT_Root,
    # Tab: Scene
    MM_PT_Scene_Identity,
    MM_PT_Scene_Environment,
    MM_PT_Scene_Lights,
    MM_PT_Scene_Paths,
    # Tab: Place
    MM_PT_Place_QuickAdd,
    MM_PT_Place_Entities,
    MM_PT_Place_CharacterDB,
    MM_PT_Place_ModelBrowser,
    # Tab: Logic
    MM_PT_Logic_Objectives,
    MM_PT_Logic_Conversations,
    MM_PT_Logic_Conversations_Preview,
    MM_PT_Logic_Conversations_NodeProps,
    MM_PT_Logic_Precache,
    # Tab: Build
    MM_PT_Build_Navigation,
    MM_PT_Build_IGB,
    MM_PT_Build_Compile,
    MM_PT_Build_Validation,
    MM_PT_Build_Collision,
    MM_PT_Build_Automap,
    # Always-visible
    MM_PT_SelectionInspector,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
