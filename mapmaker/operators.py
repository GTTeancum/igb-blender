"""Map Maker Tool operators — entity CRUD, placement, DB, preview, build pipeline."""

import math
import os

import bpy
from bpy.props import (StringProperty, IntProperty, EnumProperty,
                       BoolProperty, CollectionProperty, FloatProperty)
from bpy.types import Operator

from .entity_defs import (
    ENTITY_CLASSNAMES, ENTITY_DEFAULTS, ENTITY_VISUALS,
    ENTITY_EMPTY_SIZE, ENTITY_PRESETS, ENTITY_PRESET_ITEMS,
    get_visual_for_classname,
)

# Name of the collection that holds entity instance Empties
ENTITY_COLLECTION_NAME = "[MapMaker] Entities"

# Offset (in game units) to place holo globe above mission briefing
HOLO_GLOBE_Z_OFFSET = 32.0


def _get_entity_collection():
    """Get or create the MapMaker entities collection."""
    if ENTITY_COLLECTION_NAME in bpy.data.collections:
        col = bpy.data.collections[ENTITY_COLLECTION_NAME]
    else:
        col = bpy.data.collections.new(ENTITY_COLLECTION_NAME)
        bpy.context.scene.collection.children.link(col)
    return col


def _find_entity_def_by_name(scene, name):
    """Find an entity definition by its entity_name."""
    for edef in scene.mm_entity_defs:
        if edef.entity_name == name:
            return edef
    return None


def _resolve_model_igb_path(model_path, game_data_dir):
    """Resolve a model path to an absolute IGB file path.

    Entity defs store model paths WITHOUT the 'models/' prefix (e.g.
    'puzzles/beacon_xtraction').  This function tries the path both with
    and without a 'models/' prefix so it works either way.
    """
    if not model_path or not game_data_dir:
        return None

    # Try with models/ prefix first
    candidates = [
        os.path.join(game_data_dir, "models", model_path.replace("/", os.sep) + ".igb"),
        os.path.join(game_data_dir, model_path.replace("/", os.sep) + ".igb"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def _ensure_zone_script(game_dir, settings):
    """Create a minimal zone script .py if one doesn't exist.

    The game requires a Python script at scripts/{zone_script}.py for each zone.
    This generates a minimal stub that initializes the zone.
    """
    zone_script = settings.zone_script
    if not zone_script:
        zone_script = f"{settings.map_path}/{settings.map_name}"

    script_path = os.path.join(game_dir, "scripts", zone_script + ".py")

    if os.path.exists(script_path):
        return  # Don't overwrite existing scripts

    script_dir = os.path.dirname(script_path)
    os.makedirs(script_dir, exist_ok=True)

    map_name = settings.map_name
    content = f'''# Auto-generated zone script for {map_name}
# Created by Map Maker Tools

def OnPostInit():
    """Called after the zone is initialized."""
    pass

def OnActComplete():
    """Called when the zone activation is complete."""
    pass
'''
    with open(script_path, 'w') as f:
        f.write(content)


def _ensure_conversation_script(game_dir, conv_path):
    """Create a helper .py script that starts a conversation.

    The game's monster_actscript field references a script path that,
    on activation, starts the conversation.

    Args:
        game_dir: Root game data directory
        conv_path: Conversation path (e.g. 'act1/sanctuary/my_conv')
    """
    # Script goes at scripts/{conv_path}.py
    script_path = os.path.join(game_dir, "scripts", conv_path + ".py")

    if os.path.exists(script_path):
        return  # Don't overwrite existing scripts

    script_dir = os.path.dirname(script_path)
    os.makedirs(script_dir, exist_ok=True)

    conv_name = conv_path.split("/")[-1]
    content = f'''# Auto-generated conversation script for {conv_name}
# Created by Map Maker Tools

def OnActivate():
    """Start the conversation when the NPC is interacted with."""
    game.startConversation('{conv_path}')
'''
    with open(script_path, 'w') as f:
        f.write(content)


def _export_all_conversations(scene, output_dir, game_dir):
    """Export and deploy all conversations during Build All.

    Compiles each conversation to XMLB, then copies to the game's
    conversations/ directory if game_dir is set.

    Returns number of conversations exported.
    """
    from .xml_gen import generate_conversation_text, _write_raven_text
    from .xmlb_compile import compile_xml_to_xmlb
    import shutil

    count = 0
    for conv in scene.mm_conversations:
        if len(conv.nodes) == 0:
            continue

        # Generate Raven text format
        conv_text = generate_conversation_text(conv)
        xml_path = os.path.join(output_dir, conv.conv_name + ".engb.xml")
        _write_raven_text(conv_text, xml_path)

        # Compile to XMLB
        engb_path = os.path.join(output_dir, conv.conv_name + ".engb")
        compile_xml_to_xmlb(xml_path, engb_path)

        # Deploy to game conversations/ directory
        if game_dir and os.path.isdir(game_dir):
            conv_path = conv.conv_path
            if conv_path:
                deploy_dir = os.path.join(game_dir, "conversations", conv_path)
            else:
                deploy_dir = os.path.join(game_dir, "conversations")
            os.makedirs(deploy_dir, exist_ok=True)
            dst = os.path.join(deploy_dir, conv.conv_name + ".engb")
            shutil.copy2(engb_path, dst)

            # Also generate the helper script for NPCs that reference this conversation
            full_conv_path = f"{conv_path}/{conv.conv_name}" if conv_path else conv.conv_name
            _ensure_conversation_script(game_dir, full_conv_path)

        count += 1

    return count


# ===========================================================================
# Entity Definition CRUD
# ===========================================================================

class MM_OT_add_entity_def(Operator):
    """Add a new entity definition"""
    bl_idname = "mm.add_entity_def"
    bl_label = "Add Entity Def"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        edef = scene.mm_entity_defs.add()
        edef.entity_name = f"entity_{len(scene.mm_entity_defs):03d}"
        scene.mm_entity_defs_index = len(scene.mm_entity_defs) - 1
        return {'FINISHED'}


class MM_OT_remove_entity_def(Operator):
    """Remove selected entity definition"""
    bl_idname = "mm.remove_entity_def"
    bl_label = "Remove Entity Def"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return len(scene.mm_entity_defs) > 0 and 0 <= scene.mm_entity_defs_index < len(scene.mm_entity_defs)

    def execute(self, context):
        scene = context.scene
        scene.mm_entity_defs.remove(scene.mm_entity_defs_index)
        scene.mm_entity_defs_index = min(scene.mm_entity_defs_index, len(scene.mm_entity_defs) - 1)
        return {'FINISHED'}


class MM_OT_add_entity_property(Operator):
    """Add a custom key-value property to the selected entity definition"""
    bl_idname = "mm.add_entity_property"
    bl_label = "Add Property"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return 0 <= scene.mm_entity_defs_index < len(scene.mm_entity_defs)

    def execute(self, context):
        scene = context.scene
        edef = scene.mm_entity_defs[scene.mm_entity_defs_index]
        prop = edef.properties.add()
        prop.key = "key"
        prop.value = "value"
        edef.properties_index = len(edef.properties) - 1
        return {'FINISHED'}


class MM_OT_remove_entity_property(Operator):
    """Remove a custom property from the selected entity definition"""
    bl_idname = "mm.remove_entity_property"
    bl_label = "Remove Property"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        if not (0 <= scene.mm_entity_defs_index < len(scene.mm_entity_defs)):
            return False
        edef = scene.mm_entity_defs[scene.mm_entity_defs_index]
        return len(edef.properties) > 0 and 0 <= edef.properties_index < len(edef.properties)

    def execute(self, context):
        scene = context.scene
        edef = scene.mm_entity_defs[scene.mm_entity_defs_index]
        edef.properties.remove(edef.properties_index)
        edef.properties_index = min(edef.properties_index, len(edef.properties) - 1)
        return {'FINISHED'}


class MM_OT_apply_defaults(Operator):
    """Apply default properties for the current classname"""
    bl_idname = "mm.apply_defaults"
    bl_label = "Apply Defaults"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return 0 <= scene.mm_entity_defs_index < len(scene.mm_entity_defs)

    def execute(self, context):
        scene = context.scene
        edef = scene.mm_entity_defs[scene.mm_entity_defs_index]
        defaults = ENTITY_DEFAULTS.get(edef.classname, {})

        # Add defaults as custom properties (nocollide is a dedicated field, not in defaults)
        existing_keys = {p.key for p in edef.properties}
        for key, val in defaults.items():
            if key not in existing_keys:
                prop = edef.properties.add()
                prop.key = key
                prop.value = val

        self.report({'INFO'}, f"Applied {edef.classname} defaults")
        return {'FINISHED'}


def _create_preset_entity(context, preset, suffix_hint=""):
    """Create an entity def + instance from a preset template.

    Returns (edef, empty) tuple, or (None, None) on failure.

    Args:
        context: Blender context
        preset: Tuple from ENTITY_PRESETS
        suffix_hint: Optional suffix for unique naming (e.g. '_trig')
    """
    scene = context.scene
    pid, label, desc, classname, dedicated, props = preset

    # Generate unique entity name
    base_name = pid + suffix_hint
    existing_names = {edef.entity_name for edef in scene.mm_entity_defs}
    entity_name = base_name
    suffix = 1
    while entity_name in existing_names:
        entity_name = f"{base_name}_{suffix:02d}"
        suffix += 1

    # Create entity def
    edef = scene.mm_entity_defs.add()
    edef.entity_name = entity_name
    edef.classname = classname
    edef.nocollide = dedicated.get('nocollide', False)
    if 'model' in dedicated:
        edef.model = dedicated['model']
    if 'character' in dedicated:
        edef.character = dedicated['character']
    if 'monster_name' in dedicated:
        edef.monster_name = dedicated['monster_name']
    scene.mm_entity_defs_index = len(scene.mm_entity_defs) - 1

    # Apply custom properties
    for key, val in props.items():
        prop = edef.properties.add()
        prop.key = key
        prop.value = val

    # Auto-add precache entries for effects, scripts, models referenced
    existing_precache = {e.filename for e in scene.mm_precache}
    for key in ('loopfx', 'deatheffect', 'acteffect', 'actscript', 'spawnscript',
                'actsoundloop', 'spawnsoundloop'):
        val = props.get(key, '')
        if not val or val in existing_precache:
            continue
        # Skip inline function calls like stashMenu()
        if '(' in val:
            continue
        entry = scene.mm_precache.add()
        entry.filename = val
        if key in ('loopfx', 'deatheffect', 'acteffect'):
            entry.entry_type = 'fx'
        elif key in ('actsoundloop', 'spawnsoundloop'):
            entry.entry_type = 'sound'
        else:
            entry.entry_type = 'script'
        existing_precache.add(val)

    if edef.model and edef.model not in existing_precache:
        entry = scene.mm_precache.add()
        entry.filename = edef.model
        entry.entry_type = 'model'
        existing_precache.add(edef.model)

    # Place instance at cursor
    col = _get_entity_collection()
    display_type, color = get_visual_for_classname(classname)

    empty = bpy.data.objects.new(entity_name, None)
    empty.empty_display_type = display_type
    empty.empty_display_size = ENTITY_EMPTY_SIZE
    empty.location = context.scene.cursor.location.copy()
    empty.color = color
    empty["mm_entity_type"] = entity_name
    empty["mm_classname"] = classname
    col.objects.link(empty)

    # Auto-create light preview for lightent
    if classname == 'lightent':
        _create_light_preview(empty, edef, col)

    return edef, empty


def _create_light_preview(parent_empty, edef, collection):
    """Create a Blender light object parented to a lightent entity Empty.

    Reads lightcolor and lightradius from the entity def properties to
    configure the light. The light object is marked as a preview so it
    is excluded from export.
    """
    # Parse light properties from entity def
    color = (1.0, 1.0, 1.0)
    radius = 200.0
    for prop in edef.properties:
        if prop.key == 'lightcolor':
            try:
                vals = [float(v) for v in prop.value.split()]
                if len(vals) >= 3:
                    color = tuple(vals[:3])
            except ValueError:
                pass
        elif prop.key == 'lightradius':
            try:
                radius = float(prop.value)
            except ValueError:
                pass

    # Create point light data
    light_data = bpy.data.lights.new(
        name=f"{parent_empty.name}_light", type='POINT')
    light_data.color = color
    light_data.energy = radius * 0.5  # Scale energy from game radius
    light_data.shadow_soft_size = radius * 0.1

    # Create light object
    light_obj = bpy.data.objects.new(
        name=f"{parent_empty.name}_light", object_data=light_data)
    light_obj.parent = parent_empty
    light_obj.location = (0, 0, 0)  # At parent origin
    light_obj["mm_preview"] = True  # Mark as preview (excluded from export)

    collection.objects.link(light_obj)


class MM_OT_place_preset(Operator):
    """Create an entity def + instance from a premade template"""
    bl_idname = "mm.place_preset"
    bl_label = "Place Preset"
    bl_description = "Create a pre-configured entity and place it at the 3D cursor"
    bl_options = {'REGISTER', 'UNDO'}

    preset_id: EnumProperty(
        name="Preset",
        items=ENTITY_PRESET_ITEMS,
    )

    def execute(self, context):
        scene = context.scene

        # Find the preset definition
        preset = None
        for p in ENTITY_PRESETS:
            if p[0] == self.preset_id:
                preset = p
                break
        if not preset:
            self.report({'ERROR'}, f"Unknown preset: {self.preset_id}")
            return {'CANCELLED'}

        pid = preset[0]

        # --- Special handling: Extraction Point auto-creates trigger zone ---
        if pid == 'extraction_point':
            # Create the beacon entity
            edef, empty = _create_preset_entity(context, preset)
            if not empty:
                return {'CANCELLED'}

            # Select beacon
            bpy.ops.object.select_all(action='DESELECT')
            empty.select_set(True)
            context.view_layer.objects.active = empty

            # Auto-load model preview
            if scene.mm_settings.show_previews and edef.model:
                _load_model_preview(context, empty, edef.model,
                                    scene.mm_settings.game_data_dir)

            # Also create the extraction trigger entity at same location
            trig_preset = None
            for p in ENTITY_PRESETS:
                if p[0] == 'extraction_trigger':
                    trig_preset = p
                    break
            if trig_preset:
                trig_edef, trig_empty = _create_preset_entity(context, trig_preset)
                if trig_empty:
                    # Set extents for the trigger zone
                    trig_empty["mm_extents"] = "-120 -120 0 120 120 72"

            self.report({'INFO'},
                        f"Placed Extraction Point + Trigger at cursor")
            return {'FINISHED'}

        # --- Special handling: Mission Briefing auto-creates holo globe ---
        if pid == 'mission_briefing':
            edef, empty = _create_preset_entity(context, preset)
            if not empty:
                return {'CANCELLED'}

            bpy.ops.object.select_all(action='DESELECT')
            empty.select_set(True)
            context.view_layer.objects.active = empty

            if scene.mm_settings.show_previews and edef.model:
                _load_model_preview(context, empty, edef.model,
                                    scene.mm_settings.game_data_dir)

            # Auto-place holographic globe above the mission briefing
            globe_preset = None
            for p in ENTITY_PRESETS:
                if p[0] == 'holo_globe':
                    globe_preset = p
                    break
            if globe_preset:
                globe_edef, globe_empty = _create_preset_entity(context, globe_preset)
                if globe_empty:
                    # Offset the globe Z position above the briefing
                    globe_empty.location.z += HOLO_GLOBE_Z_OFFSET
                    if scene.mm_settings.show_previews and globe_edef.model:
                        _load_model_preview(context, globe_empty, globe_edef.model,
                                            scene.mm_settings.game_data_dir)

            self.report({'INFO'},
                        f"Placed Mission Briefing + Holographic Globe at cursor")
            return {'FINISHED'}

        # --- Default path: create single entity ---
        edef, empty = _create_preset_entity(context, preset)
        if not empty:
            return {'CANCELLED'}

        # Select
        bpy.ops.object.select_all(action='DESELECT')
        empty.select_set(True)
        context.view_layer.objects.active = empty

        # Auto-load model preview if enabled
        if scene.mm_settings.show_previews and edef.model:
            _load_model_preview(context, empty, edef.model,
                                scene.mm_settings.game_data_dir)

        self.report({'INFO'}, f"Placed preset '{preset[1]}' as {edef.entity_name}")
        return {'FINISHED'}


# ===========================================================================
# Entity Instance Placement
# ===========================================================================

class MM_OT_place_entity(Operator):
    """Place an entity instance (Empty) at the 3D cursor"""
    bl_idname = "mm.place_entity"
    bl_label = "Place Entity"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return 0 <= scene.mm_entity_defs_index < len(scene.mm_entity_defs)

    def execute(self, context):
        scene = context.scene
        edef = scene.mm_entity_defs[scene.mm_entity_defs_index]
        col = _get_entity_collection()

        # Look up visual settings
        display_type, color = get_visual_for_classname(edef.classname)

        # Create Empty
        empty = bpy.data.objects.new(edef.entity_name, None)
        empty.empty_display_type = display_type
        empty.empty_display_size = ENTITY_EMPTY_SIZE
        empty.location = context.scene.cursor.location.copy()
        empty.color = color

        # Store entity metadata
        empty["mm_entity_type"] = edef.entity_name
        empty["mm_classname"] = edef.classname

        # Link to entity collection
        col.objects.link(empty)

        # Auto-create light preview for lightent
        if edef.classname == 'lightent':
            _create_light_preview(empty, edef, col)

        # Select the new empty
        bpy.ops.object.select_all(action='DESELECT')
        empty.select_set(True)
        context.view_layer.objects.active = empty

        # Auto-load preview if enabled (works for ALL entity types with models/characters)
        if scene.mm_settings.show_previews:
            _load_preview_for_entity(context, empty, edef, scene.mm_settings.game_data_dir)

        self.report({'INFO'}, f"Placed '{edef.entity_name}' ({edef.classname})")
        return {'FINISHED'}


def _load_actor_preview(context, parent_empty, character_name, game_data_dir):
    """Load actor IGB as preview mesh, parented to entity Empty.

    Args:
        context: Blender context
        parent_empty: The entity Empty object
        character_name: Character name from entity def
        game_data_dir: Root game data directory
    """
    if not game_data_dir:
        return

    game_data_dir = bpy.path.abspath(game_data_dir)

    # Look up character in DB to get skin code
    from .game_database import get_character_db, get_skin_actor_path

    db = get_character_db(game_data_dir)
    char_info = db.get(character_name)
    if not char_info or not char_info.skin:
        return

    actor_path = get_skin_actor_path(char_info.skin, game_data_dir)
    if not actor_path:
        return

    try:
        # Import the actor IGB
        from ..importer.import_igb import import_igb
        import mathutils

        # Remember what objects exist before import
        existing_objects = set(bpy.data.objects)

        import_igb(context, actor_path)

        # Find newly created objects
        new_objects = [obj for obj in bpy.data.objects if obj not in existing_objects]

        if not new_objects:
            return

        # Move all imported objects under the entity collection so they don't
        # clutter the main scene collection
        entity_col = _get_entity_collection()

        # Parent new objects to the entity Empty and tag as preview.
        # Reset each object's transform so it sits at the parent Empty's
        # location instead of staying at its original world-space position.
        for obj in new_objects:
            obj["mm_preview"] = True

            # Unlink from whatever collection the importer placed it in
            for col in list(obj.users_collection):
                col.objects.unlink(obj)
            entity_col.objects.link(obj)

            # Parent to the entity Empty with an identity parent-inverse so
            # the object's local transform is relative to the Empty.
            obj.parent = parent_empty
            obj.matrix_parent_inverse = mathutils.Matrix.Identity(4)

            # Zero out the imported world-space location/rotation so the
            # mesh sits right on the Empty.  Keep scale intact.
            obj.location = (0, 0, 0)
            obj.rotation_euler = (0, 0, 0)

    except Exception as e:
        print(f"[MapMaker] Warning: Failed to load actor preview for {character_name}: {e}")


def _load_model_preview(context, parent_empty, model_path, game_data_dir):
    """Load a model IGB as preview mesh, parented to entity Empty.

    Works for physent, gameent, actionent, doorent — anything with a model path.
    Model paths in entity defs do NOT include the 'models/' prefix.

    Args:
        context: Blender context
        parent_empty: The entity Empty object
        model_path: Relative model path (e.g. 'puzzles/beacon_xtraction')
        game_data_dir: Root game data directory
    """
    if not game_data_dir or not model_path:
        return

    game_data_dir = bpy.path.abspath(game_data_dir)

    # Resolve the IGB path (handles both prefixed and unprefixed paths)
    igb_path = _resolve_model_igb_path(model_path, game_data_dir)
    if not igb_path:
        return

    try:
        from ..importer.import_igb import import_igb
        import mathutils

        existing_objects = set(bpy.data.objects)
        import_igb(context, igb_path)
        new_objects = [obj for obj in bpy.data.objects if obj not in existing_objects]

        if not new_objects:
            return

        entity_col = _get_entity_collection()

        for obj in new_objects:
            obj["mm_preview"] = True

            # Unlink from whatever collection the importer placed it in
            for col in list(obj.users_collection):
                col.objects.unlink(obj)
            entity_col.objects.link(obj)

            # Parent to the entity Empty
            obj.parent = parent_empty
            obj.matrix_parent_inverse = mathutils.Matrix.Identity(4)
            obj.location = (0, 0, 0)
            obj.rotation_euler = (0, 0, 0)

    except Exception as e:
        print(f"[MapMaker] Warning: Failed to load model preview for {model_path}: {e}")


def _load_preview_for_entity(context, parent_empty, edef, game_data_dir):
    """Auto-load the appropriate preview for any entity def type.

    - monsterspawnerent: load actor IGB from character skin
    - physent/gameent/etc with model: load model IGB
    """
    if not game_data_dir:
        return

    if edef.classname == 'monsterspawnerent' and edef.character:
        _load_actor_preview(context, parent_empty, edef.character, game_data_dir)
    elif edef.model:
        _load_model_preview(context, parent_empty, edef.model, game_data_dir)


class MM_OT_remove_entity_instance(Operator):
    """Remove selected entity instance(s)"""
    bl_idname = "mm.remove_entity_instance"
    bl_label = "Remove Instance"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.selected_objects and any(
            "mm_entity_type" in obj for obj in context.selected_objects
        )

    def execute(self, context):
        removed = 0
        for obj in list(context.selected_objects):
            if "mm_entity_type" in obj:
                # Also remove preview children
                for child in list(obj.children):
                    if child.get("mm_preview"):
                        bpy.data.objects.remove(child, do_unlink=True)
                bpy.data.objects.remove(obj, do_unlink=True)
                removed += 1
        self.report({'INFO'}, f"Removed {removed} entity instance(s)")
        return {'FINISHED'}


class MM_OT_select_by_type(Operator):
    """Select all entity instances of the active entity definition type"""
    bl_idname = "mm.select_by_type"
    bl_label = "Select By Type"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return 0 <= scene.mm_entity_defs_index < len(scene.mm_entity_defs)

    def execute(self, context):
        scene = context.scene
        edef = scene.mm_entity_defs[scene.mm_entity_defs_index]
        target_name = edef.entity_name

        bpy.ops.object.select_all(action='DESELECT')
        count = 0
        if ENTITY_COLLECTION_NAME in bpy.data.collections:
            for obj in bpy.data.collections[ENTITY_COLLECTION_NAME].objects:
                if obj.get("mm_entity_type") == target_name:
                    obj.select_set(True)
                    count += 1

        self.report({'INFO'}, f"Selected {count} instance(s) of '{target_name}'")
        return {'FINISHED'}


# ===========================================================================
# Actor Preview Management
# ===========================================================================

class MM_OT_refresh_previews(Operator):
    """Refresh all actor preview meshes"""
    bl_idname = "mm.refresh_previews"
    bl_label = "Refresh Previews"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (ENTITY_COLLECTION_NAME in bpy.data.collections and
                bool(context.scene.mm_settings.game_data_dir))

    def execute(self, context):
        scene = context.scene
        game_data_dir = bpy.path.abspath(scene.mm_settings.game_data_dir)

        if not os.path.isdir(game_data_dir):
            self.report({'ERROR'}, "Game data directory not found")
            return {'CANCELLED'}

        # First strip all existing previews
        _strip_all_previews()

        # Then re-load for ALL entity types that have models or characters
        loaded = 0
        if ENTITY_COLLECTION_NAME in bpy.data.collections:
            for obj in list(bpy.data.collections[ENTITY_COLLECTION_NAME].objects):
                etype = obj.get("mm_entity_type", "")
                if not etype:
                    continue
                edef = _find_entity_def_by_name(scene, etype)
                if edef:
                    _load_preview_for_entity(context, obj, edef, game_data_dir)
                    loaded += 1

        self.report({'INFO'}, f"Refreshed {loaded} entity previews")
        return {'FINISHED'}


class MM_OT_strip_previews(Operator):
    """Remove all actor preview meshes"""
    bl_idname = "mm.strip_previews"
    bl_label = "Strip Previews"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        removed = _strip_all_previews()
        self.report({'INFO'}, f"Removed {removed} preview objects")
        return {'FINISHED'}


def _strip_all_previews():
    """Remove all objects tagged as mm_preview. Returns count removed."""
    removed = 0
    for obj in list(bpy.data.objects):
        if obj.get("mm_preview"):
            bpy.data.objects.remove(obj, do_unlink=True)
            removed += 1
    return removed


# ===========================================================================
# Precache CRUD
# ===========================================================================

class MM_OT_add_precache(Operator):
    """Add a precache entry"""
    bl_idname = "mm.add_precache"
    bl_label = "Add Precache"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        entry = scene.mm_precache.add()
        entry.filename = ""
        scene.mm_precache_index = len(scene.mm_precache) - 1
        return {'FINISHED'}


class MM_OT_remove_precache(Operator):
    """Remove selected precache entry"""
    bl_idname = "mm.remove_precache"
    bl_label = "Remove Precache"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return len(scene.mm_precache) > 0 and 0 <= scene.mm_precache_index < len(scene.mm_precache)

    def execute(self, context):
        scene = context.scene
        scene.mm_precache.remove(scene.mm_precache_index)
        scene.mm_precache_index = min(scene.mm_precache_index, len(scene.mm_precache) - 1)
        return {'FINISHED'}


class MM_OT_scan_precache(Operator):
    """Auto-scan entity defs to find required precache entries"""
    bl_idname = "mm.scan_precache"
    bl_label = "Scan Precache"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        existing = {e.filename for e in scene.mm_precache}
        added = 0

        for edef in scene.mm_entity_defs:
            # Check custom properties for script/conversation/sound references
            for prop in edef.properties:
                if prop.key in ('actscript', 'spawnscript', 'monster_actscript'):
                    # Scripts might be inline (game.startConversation(...)) or path references
                    val = prop.value
                    if val.startswith("game.startConversation("):
                        # Extract conversation path
                        path = val.split("'")[1] if "'" in val else ""
                        if path and path not in existing:
                            entry = scene.mm_precache.add()
                            entry.filename = path
                            entry.entry_type = 'conversation'
                            existing.add(path)
                            added += 1
                    elif '(' in val:
                        # Other inline function calls (e.g. stashMenu()) - skip
                        continue
                    elif '/' in val and val not in existing:
                        entry = scene.mm_precache.add()
                        entry.filename = val
                        entry.entry_type = 'script'
                        existing.add(val)
                        added += 1
                elif prop.key in ('loopfx', 'deatheffect', 'acteffect'):
                    if prop.value and prop.value not in existing:
                        entry = scene.mm_precache.add()
                        entry.filename = prop.value
                        entry.entry_type = 'fx'
                        existing.add(prop.value)
                        added += 1
                elif prop.key in ('actsound', 'actsoundloop', 'spawnsoundloop'):
                    if prop.value and prop.value not in existing:
                        entry = scene.mm_precache.add()
                        entry.filename = prop.value
                        entry.entry_type = 'sound'
                        existing.add(prop.value)
                        added += 1

            # Models from entity defs
            if edef.model and edef.model not in existing:
                entry = scene.mm_precache.add()
                entry.filename = edef.model
                entry.entry_type = 'model'
                existing.add(edef.model)
                added += 1

        self.report({'INFO'}, f"Scan added {added} precache entries")
        return {'FINISHED'}


# ===========================================================================
# Character CRUD
# ===========================================================================

class MM_OT_add_character(Operator):
    """Add a character entry"""
    bl_idname = "mm.add_character"
    bl_label = "Add Character"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        entry = scene.mm_characters.add()
        entry.char_name = ""
        scene.mm_characters_index = len(scene.mm_characters) - 1
        return {'FINISHED'}


class MM_OT_remove_character(Operator):
    """Remove selected character entry"""
    bl_idname = "mm.remove_character"
    bl_label = "Remove Character"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return len(scene.mm_characters) > 0 and 0 <= scene.mm_characters_index < len(scene.mm_characters)

    def execute(self, context):
        scene = context.scene
        scene.mm_characters.remove(scene.mm_characters_index)
        scene.mm_characters_index = min(scene.mm_characters_index, len(scene.mm_characters) - 1)
        return {'FINISHED'}


class MM_OT_scan_characters(Operator):
    """Auto-scan monsterspawnerent entities to build character list"""
    bl_idname = "mm.scan_characters"
    bl_label = "Scan Characters"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        existing = {c.char_name for c in scene.mm_characters}
        added = 0

        for edef in scene.mm_entity_defs:
            if edef.classname == 'monsterspawnerent' and edef.character:
                if edef.character not in existing:
                    entry = scene.mm_characters.add()
                    entry.char_name = edef.character
                    existing.add(edef.character)
                    added += 1

        self.report({'INFO'}, f"Scan added {added} characters")
        return {'FINISHED'}


def _rename_character_cascade(scene, old_name, new_name, game_dir=""):
    """Rename a character across all entity defs, precache, and conversations.

    Performs a cascading update:
    1. Update CHRB character list entry
    2. Update entity_defs where character == old_name
    3. Update entity_defs where monster_name == old_name
    4. Update precache skin/anim entries for the character
    5. Update conversation participant names

    Returns:
        dict with counts of updated items
    """
    counts = {'entity_defs': 0, 'precache': 0, 'conversations': 0}

    # 1. Update CHRB character list entry
    for ch in scene.mm_characters:
        if ch.char_name.lower() == old_name.lower():
            ch.char_name = new_name

    # 2. Update entity defs — character field
    for edef in scene.mm_entity_defs:
        if edef.character and edef.character.lower() == old_name.lower():
            edef.character = new_name
            counts['entity_defs'] += 1

    # 3. Update entity defs — monster_name field
    for edef in scene.mm_entity_defs:
        if edef.monster_name and edef.monster_name.lower() == old_name.lower():
            edef.monster_name = new_name

    # 4. Update precache entries — look up new character info to fix skin/anim refs
    new_info = _resolve_character_info(scene, new_name, game_dir)
    if new_info:
        old_lower = old_name.lower()
        for entry in scene.mm_precache:
            # Update skin references (e.g. "1234" -> new skin number)
            if entry.entry_type == 'skin':
                # Check if this skin belongs to the old character by looking up
                # old char info
                old_info = _resolve_character_info(scene, old_name, game_dir)
                if old_info and entry.filename == old_info.skin:
                    entry.filename = new_info.skin
                    counts['precache'] += 1
            # Update anim references
            elif entry.entry_type == 'anim':
                if old_info and old_info.characteranims:
                    if entry.filename == old_info.characteranims:
                        if new_info.characteranims:
                            entry.filename = new_info.characteranims
                            counts['precache'] += 1

    # 5. Update conversation participant names
    for conv in scene.mm_conversations:
        for node in conv.nodes:
            if (node.node_type == 'PARTICIPANT' and
                    node.participant_name.lower() == old_name.lower()):
                node.participant_name = new_name
                counts['conversations'] += 1

    return counts


class MM_OT_rename_character(Operator):
    """Rename a character — updates all entity defs, precache, and conversation references"""
    bl_idname = "mm.rename_character"
    bl_label = "Rename Character"
    bl_options = {'REGISTER', 'UNDO'}

    new_name: StringProperty(
        name="New Name",
        description="New character name (case-sensitive)",
    )

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return (0 <= scene.mm_characters_index < len(scene.mm_characters))

    def invoke(self, context, event):
        scene = context.scene
        ch = scene.mm_characters[scene.mm_characters_index]
        self.new_name = ch.char_name
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        ch = scene.mm_characters[scene.mm_characters_index]
        layout.label(text=f"Current: {ch.char_name}", icon='ARMATURE_DATA')
        layout.prop(self, "new_name")

    def execute(self, context):
        scene = context.scene
        ch = scene.mm_characters[scene.mm_characters_index]
        old_name = ch.char_name

        if not self.new_name or self.new_name == old_name:
            self.report({'WARNING'}, "No name change specified")
            return {'CANCELLED'}

        game_dir = ""
        if hasattr(scene, 'mm_settings'):
            game_dir = bpy.path.abspath(getattr(scene.mm_settings, 'game_data_dir', ''))

        counts = _rename_character_cascade(scene, old_name, self.new_name, game_dir)

        total = counts['entity_defs'] + counts['precache'] + counts['conversations']
        self.report({'INFO'},
                    f"Renamed '{old_name}' → '{self.new_name}': "
                    f"{counts['entity_defs']} entity defs, "
                    f"{counts['precache']} precache, "
                    f"{counts['conversations']} conversation nodes updated")
        return {'FINISHED'}


class MM_OT_replace_character(Operator):
    """Replace character with the selected character from the DB"""
    bl_idname = "mm.replace_character"
    bl_label = "Replace from DB"
    bl_description = ("Replace this character with the one currently selected "
                      "in the Character Database panel")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return (0 <= scene.mm_characters_index < len(scene.mm_characters) and
                0 <= scene.mm_char_db_index < len(scene.mm_char_db))

    def execute(self, context):
        scene = context.scene
        ch = scene.mm_characters[scene.mm_characters_index]
        old_name = ch.char_name

        db_entry = scene.mm_char_db[scene.mm_char_db_index]
        new_name = db_entry.name_id

        if new_name.lower() == old_name.lower():
            self.report({'WARNING'}, "Same character selected — no change needed")
            return {'CANCELLED'}

        game_dir = ""
        if hasattr(scene, 'mm_settings'):
            game_dir = bpy.path.abspath(getattr(scene.mm_settings, 'game_data_dir', ''))

        counts = _rename_character_cascade(scene, old_name, new_name, game_dir)

        total = counts['entity_defs'] + counts['precache'] + counts['conversations']
        self.report({'INFO'},
                    f"Replaced '{old_name}' → '{new_name}': "
                    f"{counts['entity_defs']} entity defs, "
                    f"{counts['precache']} precache, "
                    f"{counts['conversations']} conversation nodes updated")
        return {'FINISHED'}


# ===========================================================================
# Character Database
# ===========================================================================

class MM_OT_load_char_db(Operator):
    """Load character database from npcstat.engb + herostat.engb"""
    bl_idname = "mm.load_char_db"
    bl_label = "Load Character DB"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return bool(context.scene.mm_settings.game_data_dir)

    def execute(self, context):
        from .game_database import get_character_db

        scene = context.scene
        game_dir = bpy.path.abspath(scene.mm_settings.game_data_dir)

        if not os.path.isdir(game_dir):
            self.report({'ERROR'}, "Game data directory not found")
            return {'CANCELLED'}

        try:
            db = get_character_db(game_dir, force_reload=True)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to load character DB: {e}")
            return {'CANCELLED'}

        # Populate scene collection
        scene.mm_char_db.clear()
        for name, info in sorted(db.items(), key=lambda x: x[1].charactername or x[0]):
            entry = scene.mm_char_db.add()
            entry.name_id = info.name
            entry.display_name = info.charactername or info.name
            entry.team = info.team
            entry.skin = info.skin
            entry.source = info.source
            entry.characteranims = info.characteranims

        self.report({'INFO'}, f"Loaded {len(scene.mm_char_db)} characters from game data")
        return {'FINISHED'}


class MM_OT_pick_character(Operator):
    """Set selected entity def's character from character database"""
    bl_idname = "mm.pick_character"
    bl_label = "Pick Character"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return (0 <= scene.mm_char_db_index < len(scene.mm_char_db) and
                0 <= scene.mm_entity_defs_index < len(scene.mm_entity_defs))

    def execute(self, context):
        scene = context.scene
        char_entry = scene.mm_char_db[scene.mm_char_db_index]
        edef = scene.mm_entity_defs[scene.mm_entity_defs_index]

        # Set entity def's character field
        edef.character = char_entry.name_id

        # Auto-add to CHRB character list if not already there
        existing_chars = {c.char_name for c in scene.mm_characters}
        if char_entry.name_id not in existing_chars:
            entry = scene.mm_characters.add()
            entry.char_name = char_entry.name_id

        self.report({'INFO'}, f"Set character to '{char_entry.display_name}' ({char_entry.name_id})")
        return {'FINISHED'}


class MM_OT_quick_place_character(Operator):
    """One-click: create entity def + place instance for selected character"""
    bl_idname = "mm.quick_place_character"
    bl_label = "Quick Place Character"
    bl_description = "Create a monsterspawnerent entity def, fill in all fields, add to CHRB, and place at cursor"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return 0 <= scene.mm_char_db_index < len(scene.mm_char_db)

    def execute(self, context):
        from .entity_defs import (
            ENTITY_DEFAULTS, get_visual_for_classname, ENTITY_EMPTY_SIZE,
        )

        scene = context.scene
        char_entry = scene.mm_char_db[scene.mm_char_db_index]

        # Generate unique entity name from character (e.g. "sp_cyclops01")
        base_name = f"sp_{char_entry.name_id.lower()}"
        existing_names = {edef.entity_name for edef in scene.mm_entity_defs}
        entity_name = base_name
        suffix = 1
        while entity_name in existing_names:
            entity_name = f"{base_name}{suffix:02d}"
            suffix += 1

        # Create entity def
        edef = scene.mm_entity_defs.add()
        edef.entity_name = entity_name
        edef.classname = 'monsterspawnerent'
        edef.character = char_entry.name_id
        edef.monster_name = char_entry.display_name.lower()
        # Nocollide: True for spawners (most NPCs shouldn't block)
        edef.nocollide = True
        scene.mm_entity_defs_index = len(scene.mm_entity_defs) - 1

        # Apply monsterspawnerent defaults
        defaults = ENTITY_DEFAULTS.get('monsterspawnerent', {})
        for key, val in defaults.items():
            prop = edef.properties.add()
            prop.key = key
            prop.value = val

        # Conditionally add monster_actonuse — only for friendly/allied NPCs
        # (team != enemy/boss). This lets the player interact with the NPC.
        is_friendly = char_entry.team.lower() not in ('enemy', 'boss')
        if is_friendly:
            prop = edef.properties.add()
            prop.key = 'monster_actonuse'
            prop.value = 'true'

        # Auto-add to CHRB
        existing_chars = {c.char_name for c in scene.mm_characters}
        if char_entry.name_id not in existing_chars:
            entry = scene.mm_characters.add()
            entry.char_name = char_entry.name_id

        # Place instance at cursor
        col = _get_entity_collection()
        display_type, color = get_visual_for_classname('monsterspawnerent')

        empty = bpy.data.objects.new(entity_name, None)
        empty.empty_display_type = display_type
        empty.empty_display_size = ENTITY_EMPTY_SIZE
        empty.location = context.scene.cursor.location.copy()
        empty.color = color
        empty["mm_entity_type"] = entity_name
        empty["mm_classname"] = 'monsterspawnerent'
        col.objects.link(empty)

        # Select the new empty
        bpy.ops.object.select_all(action='DESELECT')
        empty.select_set(True)
        context.view_layer.objects.active = empty

        # Auto-load actor preview if enabled
        if scene.mm_settings.show_previews:
            _load_actor_preview(context, empty, char_entry.name_id,
                                scene.mm_settings.game_data_dir)

        self.report({'INFO'},
                     f"Placed '{char_entry.display_name}' as {entity_name} at cursor")
        return {'FINISHED'}


# ===========================================================================
# Model Database
# ===========================================================================

class MM_OT_scan_models(Operator):
    """Scan game models/ directory for available models"""
    bl_idname = "mm.scan_models"
    bl_label = "Scan Models"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return bool(context.scene.mm_settings.game_data_dir)

    def execute(self, context):
        from .game_database import get_model_db

        scene = context.scene
        game_dir = bpy.path.abspath(scene.mm_settings.game_data_dir)

        if not os.path.isdir(game_dir):
            self.report({'ERROR'}, "Game data directory not found")
            return {'CANCELLED'}

        try:
            models = get_model_db(game_dir, force_reload=True)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to scan models: {e}")
            return {'CANCELLED'}

        # Populate scene collection
        scene.mm_model_db.clear()
        for info in models:
            entry = scene.mm_model_db.add()
            entry.rel_path = info.rel_path
            entry.category = info.category
            entry.display_name = info.display_name

        self.report({'INFO'}, f"Found {len(scene.mm_model_db)} models")
        return {'FINISHED'}


class MM_OT_pick_model(Operator):
    """Set selected entity def's model from model database"""
    bl_idname = "mm.pick_model"
    bl_label = "Pick Model"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return (0 <= scene.mm_model_db_index < len(scene.mm_model_db) and
                0 <= scene.mm_entity_defs_index < len(scene.mm_entity_defs))

    def execute(self, context):
        scene = context.scene
        model_entry = scene.mm_model_db[scene.mm_model_db_index]
        edef = scene.mm_entity_defs[scene.mm_entity_defs_index]

        # Set entity def's model field (WITHOUT 'models/' prefix)
        edef.model = model_entry.rel_path

        self.report({'INFO'}, f"Set model to '{model_entry.rel_path}'")
        return {'FINISHED'}


class MM_OT_quick_place_model(Operator):
    """One-click: create physent entity def + place instance + load preview for selected model"""
    bl_idname = "mm.quick_place_model"
    bl_label = "Quick Place Model"
    bl_description = ("Create a physent entity def, import the model as a visual preview, "
                      "and place at the 3D cursor")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return (0 <= scene.mm_model_db_index < len(scene.mm_model_db) and
                bool(scene.mm_settings.game_data_dir))

    def execute(self, context):
        scene = context.scene
        model_entry = scene.mm_model_db[scene.mm_model_db_index]

        # Model path WITHOUT 'models/' prefix (PKGB generator adds it)
        model_path = model_entry.rel_path

        # Generate unique entity name
        base_name = model_entry.display_name
        existing_names = {edef.entity_name for edef in scene.mm_entity_defs}
        entity_name = base_name
        suffix = 1
        while entity_name in existing_names:
            entity_name = f"{base_name}_{suffix:02d}"
            suffix += 1

        # Create physent entity def
        edef = scene.mm_entity_defs.add()
        edef.entity_name = entity_name
        edef.classname = 'physent'
        edef.model = model_path
        edef.nocollide = True
        scene.mm_entity_defs_index = len(scene.mm_entity_defs) - 1

        # Apply physent defaults
        defaults = ENTITY_DEFAULTS.get('physent', {})
        for key, val in defaults.items():
            prop = edef.properties.add()
            prop.key = key
            prop.value = val

        # Place instance at cursor
        col = _get_entity_collection()
        display_type, color = get_visual_for_classname('physent')

        empty = bpy.data.objects.new(entity_name, None)
        empty.empty_display_type = display_type
        empty.empty_display_size = ENTITY_EMPTY_SIZE
        empty.location = context.scene.cursor.location.copy()
        empty.color = color
        empty["mm_entity_type"] = entity_name
        empty["mm_classname"] = 'physent'
        col.objects.link(empty)

        # Select the new empty
        bpy.ops.object.select_all(action='DESELECT')
        empty.select_set(True)
        context.view_layer.objects.active = empty

        # Auto-load model preview
        if scene.mm_settings.show_previews:
            _load_model_preview(context, empty, model_path,
                                scene.mm_settings.game_data_dir)

        self.report({'INFO'},
                     f"Placed '{model_entry.display_name}' as {entity_name} at cursor")
        return {'FINISHED'}


class MM_OT_import_model_to_asset_lib(Operator):
    """Import selected model from DB into current file and mark as Asset"""
    bl_idname = "mm.import_model_asset"
    bl_label = "Import as Asset"
    bl_description = ("Import the selected model IGB, mark it as a Blender Asset, "
                      "and tag it with MapMaker metadata for auto-detection")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return (0 <= scene.mm_model_db_index < len(scene.mm_model_db) and
                bool(scene.mm_settings.game_data_dir))

    def execute(self, context):
        scene = context.scene
        model_entry = scene.mm_model_db[scene.mm_model_db_index]
        game_dir = bpy.path.abspath(scene.mm_settings.game_data_dir)

        igb_path = os.path.join(game_dir, "models",
                                model_entry.rel_path.replace("/", os.sep) + ".igb")
        if not os.path.exists(igb_path):
            self.report({'ERROR'}, f"Model file not found: {igb_path}")
            return {'CANCELLED'}

        try:
            from ..importer.import_igb import import_igb

            existing_objects = set(bpy.data.objects)
            import_igb(context, igb_path)
            new_objects = [obj for obj in bpy.data.objects if obj not in existing_objects]

            if not new_objects:
                self.report({'WARNING'}, "No objects imported")
                return {'CANCELLED'}

            # Tag all imported objects with MapMaker metadata
            for obj in new_objects:
                obj["mm_model_path"] = model_entry.rel_path
                obj["mm_model_category"] = model_entry.category
                obj.asset_mark()
                obj.asset_data.tags.new(name="MapMaker")
                obj.asset_data.tags.new(name=model_entry.category)

            self.report({'INFO'},
                         f"Imported '{model_entry.display_name}' as Asset "
                         f"({len(new_objects)} objects)")
        except Exception as e:
            self.report({'ERROR'}, f"Import failed: {e}")
            return {'CANCELLED'}

        return {'FINISHED'}


class MM_OT_import_category_to_asset_lib(Operator):
    """Batch import all models from selected category as Assets"""
    bl_idname = "mm.import_category_assets"
    bl_label = "Import Category as Assets"
    bl_description = "Import all models from the current category filter as Blender Assets"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return (len(scene.mm_model_db) > 0 and
                bool(scene.mm_settings.game_data_dir) and
                bool(scene.mm_model_filter_category))

    def execute(self, context):
        scene = context.scene
        game_dir = bpy.path.abspath(scene.mm_settings.game_data_dir)
        category = scene.mm_model_filter_category
        imported = 0
        failed = 0

        from ..importer.import_igb import import_igb

        for entry in scene.mm_model_db:
            if entry.category != category:
                continue

            igb_path = os.path.join(game_dir, "models",
                                    entry.rel_path.replace("/", os.sep) + ".igb")
            if not os.path.exists(igb_path):
                failed += 1
                continue

            try:
                existing_objects = set(bpy.data.objects)
                import_igb(context, igb_path)
                new_objects = [obj for obj in bpy.data.objects
                               if obj not in existing_objects]

                for obj in new_objects:
                    obj["mm_model_path"] = entry.rel_path
                    obj["mm_model_category"] = entry.category
                    obj.asset_mark()
                    obj.asset_data.tags.new(name="MapMaker")
                    obj.asset_data.tags.new(name=entry.category)

                imported += 1
            except Exception:
                failed += 1

        self.report({'INFO'},
                     f"Imported {imported} models as Assets "
                     f"({failed} failed) from '{category}'")
        return {'FINISHED'}


class MM_OT_detect_placed_assets(Operator):
    """Detect MapMaker-tagged objects in scene and create entity defs for them"""
    bl_idname = "mm.detect_placed_assets"
    bl_label = "Detect Placed Assets"
    bl_description = ("Scan scene for MapMaker-tagged model objects and "
                      "auto-create physent entity defs + instances")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .entity_defs import (
            ENTITY_DEFAULTS, get_visual_for_classname, ENTITY_EMPTY_SIZE,
        )

        scene = context.scene
        col = _get_entity_collection()
        existing_names = {edef.entity_name for edef in scene.mm_entity_defs}

        created_defs = 0
        created_instances = 0

        # Group tagged objects by model path
        model_objects = {}
        for obj in bpy.data.objects:
            model_path = obj.get("mm_model_path", "")
            if model_path and not obj.get("mm_entity_type"):
                if model_path not in model_objects:
                    model_objects[model_path] = []
                model_objects[model_path].append(obj)

        for model_path, objects in model_objects.items():
            # Generate entity name from model path
            base_name = model_path.split("/")[-1]
            entity_name = base_name
            suffix = 1
            while entity_name in existing_names:
                entity_name = f"{base_name}_{suffix:02d}"
                suffix += 1

            # Create physent entity def
            edef = scene.mm_entity_defs.add()
            edef.entity_name = entity_name
            edef.classname = 'physent'
            edef.model = model_path
            edef.nocollide = True
            existing_names.add(entity_name)
            created_defs += 1

            # Apply physent defaults
            defaults = ENTITY_DEFAULTS.get('physent', {})
            for key, val in defaults.items():
                prop = edef.properties.add()
                prop.key = key
                prop.value = val

            # Create entity instance Empties at each object's location
            display_type, color = get_visual_for_classname('physent')
            for obj in objects:
                empty = bpy.data.objects.new(
                    f"{entity_name}", None)
                empty.empty_display_type = display_type
                empty.empty_display_size = ENTITY_EMPTY_SIZE
                empty.location = obj.location.copy()
                empty.rotation_euler = obj.rotation_euler.copy()
                empty.color = color
                empty["mm_entity_type"] = entity_name
                empty["mm_classname"] = 'physent'
                col.objects.link(empty)

                # Tag original object as processed
                obj["mm_entity_type"] = entity_name

                created_instances += 1

        self.report({'INFO'},
                     f"Detected {created_defs} model types, "
                     f"created {created_instances} entity instances")
        return {'FINISHED'}


# ===========================================================================
# Navigation Mesh
# ===========================================================================

class MM_OT_generate_navmesh(Operator):
    """Generate navigation grid from selected floor geometry"""
    bl_idname = "mm.generate_navmesh"
    bl_label = "Generate NavMesh"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.selected_objects and any(
            obj.type == 'MESH' for obj in context.selected_objects
        )

    def execute(self, context):
        from .nav_gen import generate_nav_cells
        import time

        scene = context.scene
        settings = scene.mm_settings
        cellsize = settings.nav_cellsize
        max_slope = settings.nav_max_slope
        multi_layer = settings.nav_multi_layer
        layer_sep = settings.nav_layer_separation
        mesh_objects = [obj for obj in context.selected_objects if obj.type == 'MESH']

        t0 = time.perf_counter()
        cells = generate_nav_cells(
            context, mesh_objects, cellsize,
            max_slope=max_slope,
            multi_layer=multi_layer,
            min_layer_sep=layer_sep,
        )
        elapsed = time.perf_counter() - t0

        if not cells:
            self.report({'WARNING'}, "No navigation cells generated")
            return {'CANCELLED'}

        # Store cells as a scene custom property (serialized as string)
        scene["mm_nav_cells"] = repr(cells)

        self.report({'INFO'},
                    f"Generated {len(cells)} nav cells in {elapsed:.2f}s "
                    f"(cellsize={cellsize}, slope={max_slope:.0f}°, "
                    f"layers={'ON' if multi_layer else 'OFF'})")
        return {'FINISHED'}


class MM_OT_visualize_navmesh(Operator):
    """Create a visual mesh showing the navigation grid"""
    bl_idname = "mm.visualize_navmesh"
    bl_label = "Visualize NavMesh"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return "mm_nav_cells" in context.scene

    def execute(self, context):
        import ast

        scene = context.scene
        cellsize = scene.mm_settings.nav_cellsize

        try:
            cells = ast.literal_eval(scene["mm_nav_cells"])
        except Exception:
            self.report({'ERROR'}, "Failed to parse stored nav cells")
            return {'CANCELLED'}

        if not cells:
            self.report({'WARNING'}, "No nav cells stored")
            return {'CANCELLED'}

        # Build a mesh with quads for each nav cell
        verts = []
        faces = []
        half = cellsize / 2.0

        for i, (gx, gy, wz) in enumerate(cells):
            # Convert grid coords to world coords (center of cell)
            cx = gx * cellsize
            cy = gy * cellsize

            base = len(verts)
            verts.append((cx - half, cy - half, wz))
            verts.append((cx + half, cy - half, wz))
            verts.append((cx + half, cy + half, wz))
            verts.append((cx - half, cy + half, wz))
            faces.append((base, base + 1, base + 2, base + 3))

        # Create mesh
        mesh = bpy.data.meshes.new("NavMesh_Preview")
        mesh.from_pydata(verts, [], faces)
        mesh.update()

        # Remove old preview if exists
        old = bpy.data.objects.get("NavMesh_Preview")
        if old:
            bpy.data.objects.remove(old, do_unlink=True)

        obj = bpy.data.objects.new("NavMesh_Preview", mesh)
        obj.display_type = 'WIRE'
        context.scene.collection.children[0].objects.link(obj) if context.scene.collection.children else context.scene.collection.objects.link(obj)

        self.report({'INFO'}, f"Visualized {len(cells)} nav cells")
        return {'FINISHED'}


class MM_OT_clear_navmesh(Operator):
    """Remove stored nav cells and the NavMesh_Preview object"""
    bl_idname = "mm.clear_navmesh"
    bl_label = "Clear NavMesh"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return "mm_nav_cells" in context.scene

    def execute(self, context):
        scene = context.scene

        # Remove stored nav cell data
        if "mm_nav_cells" in scene:
            del scene["mm_nav_cells"]

        # Remove visualization mesh if present
        old = bpy.data.objects.get("NavMesh_Preview")
        if old:
            bpy.data.objects.remove(old, do_unlink=True)

        self.report({'INFO'}, "Nav mesh data cleared")
        return {'FINISHED'}


# ===========================================================================
# Build & Compile
# ===========================================================================

class MM_OT_generate_xml(Operator):
    """Generate all XML files (ENGB, CHRB, NAVB, BOYB, PKGB)"""
    bl_idname = "mm.generate_xml"
    bl_label = "Generate XML"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return bool(context.scene.mm_settings.output_dir)

    def execute(self, context):
        from .xml_gen import generate_all_xml

        scene = context.scene
        output_dir = bpy.path.abspath(scene.mm_settings.output_dir)

        if not os.path.isdir(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        try:
            files = generate_all_xml(scene, output_dir)
            self.report({'INFO'}, f"Generated {len(files)} XML files in {output_dir}")
        except Exception as e:
            self.report({'ERROR'}, f"XML generation failed: {e}")
            return {'CANCELLED'}

        return {'FINISHED'}


class MM_OT_compile_xmlb(Operator):
    """Compile all generated XML files to XMLB binary"""
    bl_idname = "mm.compile_xmlb"
    bl_label = "Compile XMLB"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return bool(context.scene.mm_settings.output_dir)

    def execute(self, context):
        from .xmlb_compile import compile_all_xmlb

        scene = context.scene
        output_dir = bpy.path.abspath(scene.mm_settings.output_dir)

        try:
            compiled = compile_all_xmlb(output_dir)
            self.report({'INFO'}, f"Compiled {compiled} XMLB files")
        except Exception as e:
            self.report({'ERROR'}, f"XMLB compilation failed: {e}")
            return {'CANCELLED'}

        return {'FINISHED'}


class MM_OT_build_all(Operator):
    """Full build: Generate XML -> Compile XMLB -> Copy to game dir.
    XMLB compilation runs in a background thread to keep Blender responsive"""
    bl_idname = "mm.build_all"
    bl_label = "Build All"
    bl_options = {'REGISTER'}

    # Shared state for the async build
    _thread = None
    _result = None    # (success: bool, message: str)
    _timer = None
    _building = False

    @classmethod
    def poll(cls, context):
        return bool(context.scene.mm_settings.output_dir) and not cls._building

    def execute(self, context):
        import threading

        # Step 1: Generate XML (fast, runs on main thread — touches Blender data)
        result = bpy.ops.mm.generate_xml()
        if result != {'FINISHED'}:
            return result

        # Gather all paths on the main thread (Blender data access)
        scene = context.scene
        settings = scene.mm_settings
        output_dir = bpy.path.abspath(settings.output_dir)
        game_dir = bpy.path.abspath(settings.game_data_dir)
        map_name = settings.map_name
        map_path = settings.map_path
        zone_script = settings.zone_script

        # Gather conversation data on main thread
        # Apply conv_path fallback here (same logic as PKGB generator)
        conv_data = []
        for conv in scene.mm_conversations:
            cpath = conv.conv_path
            if not cpath:
                cpath = f"{map_path}/{map_name}"
            conv_data.append({
                'conv_path': cpath,
                'conv_name': conv.conv_name,
            })
        edef_scripts = []
        for edef in scene.mm_entity_defs:
            for prop in edef.properties:
                if prop.key == 'monster_actscript' and prop.value:
                    edef_scripts.append(prop.value)

        # Gather act number for mission naming
        act_number = settings.act

        # Generate objective zone script on main thread (needs bpy access)
        objective_script = ""
        mission_name = ""
        try:
            from .objective_gen import generate_zone_script_with_objectives
            objective_script = generate_zone_script_with_objectives(scene)
            if objective_script:
                mission_name = f"act{act_number}_{map_name}"
        except Exception as e:
            print(f"[MapMaker] Warning: objective generation failed: {e}")

        # Step 1.5: Export IGB if enabled (main thread — needs bpy access)
        if settings.build_export_igb:
            from ..exporter.export_igb import export_igb

            class _IGBOptions:
                """Fake operator namespace for export_igb options."""
                pass

            opts = _IGBOptions()
            opts.texture_format = settings.build_texture_format
            opts.collision_source = settings.build_collision_source
            opts.surface_type = 0
            opts.export_lights = settings.build_export_lights

            igb_path = os.path.join(output_dir, map_name + ".igb")
            igb_result = export_igb(context, igb_path, opts)
            if igb_result == {'CANCELLED'}:
                self.report({'ERROR'}, "IGB export failed")
                return {'CANCELLED'}
            self.report({'INFO'}, f"IGB exported: {map_name}.igb")

        # Step 2+3: Compile XMLB + copy files in background thread
        MM_OT_build_all._building = True
        MM_OT_build_all._result = None

        def _bg_build():
            try:
                msg = _build_all_background(
                    output_dir, game_dir, map_name, map_path,
                    zone_script, conv_data, edef_scripts,
                    objective_script, mission_name,
                )
                MM_OT_build_all._result = (True, msg)
            except Exception as e:
                MM_OT_build_all._result = (False, str(e))

        MM_OT_build_all._thread = threading.Thread(
            target=_bg_build, daemon=True)
        MM_OT_build_all._thread.start()

        # Register a timer to check for completion
        wm = context.window_manager
        MM_OT_build_all._timer = wm.event_timer_add(0.2, window=context.window)
        wm.modal_handler_add(self)

        self.report({'INFO'}, "Build started (compiling in background)...")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}

        if MM_OT_build_all._result is not None:
            # Build finished — clean up
            wm = context.window_manager
            wm.event_timer_remove(MM_OT_build_all._timer)
            MM_OT_build_all._timer = None
            MM_OT_build_all._thread = None
            MM_OT_build_all._building = False

            success, msg = MM_OT_build_all._result
            MM_OT_build_all._result = None

            if success:
                self.report({'INFO'}, msg)
                # Force redraw so panel updates
                for area in context.screen.areas:
                    area.tag_redraw()
                return {'FINISHED'}
            else:
                self.report({'ERROR'}, f"Build failed: {msg}")
                return {'CANCELLED'}

        return {'PASS_THROUGH'}

    def cancel(self, context):
        if MM_OT_build_all._timer is not None:
            context.window_manager.event_timer_remove(MM_OT_build_all._timer)
            MM_OT_build_all._timer = None
        MM_OT_build_all._building = False


def _build_all_background(output_dir, game_dir, map_name, map_path,
                          zone_script, conv_data, edef_scripts,
                          objective_script="", mission_name=""):
    """Run XMLB compilation + file deployment in a background thread.

    This function must NOT access bpy.data or bpy.context — only use
    the pre-gathered path strings and the compile_all_xmlb function.

    Returns:
        str: success message
    """
    from .xmlb_compile import compile_all_xmlb, compile_xml_to_xmlb
    import shutil

    # Compile all XMLB files
    compiled = compile_all_xmlb(output_dir)

    # Copy to game dir if set
    if game_dir and os.path.isdir(game_dir):
        map_subpath = f"maps/{map_path}/{map_name}"
        maps_target = os.path.join(game_dir, os.path.dirname(map_subpath))
        os.makedirs(maps_target, exist_ok=True)

        pkgb_subpath = f"packages/generated/maps/{map_path}/{map_name}"
        pkgb_target = os.path.join(game_dir, os.path.dirname(pkgb_subpath))
        os.makedirs(pkgb_target, exist_ok=True)

        count = 0
        for ext in ('.igb', '.engb', '.chrb', '.navb', '.boyb'):
            src = os.path.join(output_dir, map_name + ext)
            if os.path.exists(src):
                dst = os.path.join(maps_target, os.path.basename(src))
                shutil.copy2(src, dst)
                count += 1

        src = os.path.join(output_dir, map_name + '.pkgb')
        if os.path.exists(src):
            dst = os.path.join(pkgb_target, os.path.basename(src))
            shutil.copy2(src, dst)
            count += 1

        # Mission ENGB (objective definitions) → data/missions/{mission_name}.engb
        # MUST be at top-level data/missions/ (NOT nested) and registered
        # in missions.xmlb for the engine to load it.
        mission_src = os.path.join(output_dir, map_name + '_mission.engb')
        if os.path.exists(mission_src) and mission_name:
            mission_dir = os.path.join(game_dir, "data", "missions")
            os.makedirs(mission_dir, exist_ok=True)
            dst = os.path.join(mission_dir, mission_name + '.engb')
            shutil.copy2(mission_src, dst)
            count += 1

            # Register in missions.xmlb if not already present
            _register_mission_in_xmlb(mission_dir, mission_name)

            # Update new_game.py to use beginMission() so engine loads
            # mission data (objectives) before zone scripts run
            _write_new_game_script_bg(game_dir, mission_name, map_path,
                                      map_name)

        # Zone script — write objective-aware script or minimal stub
        _write_zone_script_bg(game_dir, map_path, map_name, zone_script,
                              objective_script)

        # Compile and deploy conversations
        conv_count = 0
        for cd in conv_data:
            conv_xml = os.path.join(output_dir, f"{cd['conv_name']}.engb.xml")
            if os.path.exists(conv_xml):
                conv_bin = os.path.join(output_dir, f"{cd['conv_name']}.engb")
                try:
                    compile_xml_to_xmlb(conv_xml, conv_bin)
                except Exception:
                    continue
                # Deploy to game conversations dir (conv_path already has
                # fallback applied — see main thread data gathering)
                conv_target = os.path.join(game_dir, "conversations",
                                           cd['conv_path'])
                os.makedirs(conv_target, exist_ok=True)
                dst = os.path.join(conv_target, f"{cd['conv_name']}.engb")
                shutil.copy2(conv_bin, dst)
                conv_count += 1

        count += conv_count

        # Auto-generate conversation helper scripts for linked NPCs
        for val in edef_scripts:
            if '(' in val:
                continue
            for cd in conv_data:
                full_conv_path = f"{cd['conv_path']}/{cd['conv_name']}"
                # Match entity script value against full path OR bare conv_name
                if val == full_conv_path or val == cd['conv_name']:
                    _write_conversation_script_bg(game_dir, val, full_conv_path)
                    break

        return f"Build complete. Compiled {compiled} files, copied {count} to game directory."
    else:
        return f"Build complete. Compiled {compiled} files. Set Game Data Directory to auto-deploy."


def _write_zone_script_bg(game_dir, map_path, map_name, zone_script="",
                          objective_script=""):
    """Write zone script to game directory (background-safe, no bpy access).

    If objective_script is provided, writes the full objective-aware script.
    Otherwise writes a minimal stub. Always overwrites existing script to
    keep objectives in sync with the build.
    """
    if not zone_script:
        zone_script = f"{map_path}/{map_name}"
    script_path = os.path.join(game_dir, "scripts", zone_script + ".py")
    os.makedirs(os.path.dirname(script_path), exist_ok=True)

    if objective_script:
        with open(script_path, 'w') as f:
            f.write(objective_script)
    elif not os.path.exists(script_path):
        # Only write stub if no script exists yet
        with open(script_path, 'w') as f:
            f.write(f"# Zone script for {map_name}\n")
            f.write("# Auto-generated by Map Maker\n")
            f.write("\ndef OnPostInit():\n")
            f.write("    pass\n")


def _write_new_game_script_bg(game_dir, mission_name, map_path, map_name):
    """Write new_game.py to start the mission (background-safe, no bpy).

    Uses beginMission() which loads the mission data AND transitions to
    the starting zone. The engine determines the starting zone from
    the objectives' zone attributes in the mission ENGB.
    """
    ng_path = os.path.join(game_dir, "scripts", "menus", "new_game.py")
    if os.path.exists(ng_path):
        # Back up existing if not already backed up
        bak = ng_path + ".bak"
        if not os.path.exists(bak):
            import shutil
            shutil.copy2(ng_path, bak)
    os.makedirs(os.path.dirname(ng_path), exist_ok=True)
    with open(ng_path, 'w') as f:
        f.write("# Auto-generated by Map Maker — starts mission with objectives\n")
        f.write(f"# Original backed up to new_game.py.bak\n")
        f.write(f"# Mission: {mission_name}  Zone: {map_path}/{map_name}\n")
        f.write(f'beginMission("{mission_name}" )\n')


def _write_conversation_script_bg(game_dir, script_name, full_conv_path):
    """Write a conversation helper script (background-safe, no bpy access).

    Args:
        game_dir: Game data root directory
        script_name: Value of monster_actscript (where game looks for script)
        full_conv_path: Full conversation path for game.startConversation()
    """
    script_path = os.path.join(game_dir, "scripts", script_name + ".py")
    os.makedirs(os.path.dirname(script_path), exist_ok=True)
    # Always overwrite — ensures correct callback and conversation path
    with open(script_path, 'w') as f:
        f.write(f"# Conversation script for {full_conv_path}\n")
        f.write("# Auto-generated by Map Maker\n")
        f.write(f"\ndef OnActivate():\n")
        f.write(f"    game.startConversation('{full_conv_path}')\n")


def _register_mission_in_xmlb(mission_dir, mission_name):
    """Add a mission to missions.xmlb if not already present.

    Background-safe (no bpy access). Reads the existing missions.xmlb,
    adds the new mission entry, and rewrites it.
    """
    from .xmlb_compile import compile_xml_to_xmlb
    import xml.etree.ElementTree as ET

    xmlb_path = os.path.join(mission_dir, "missions.xmlb")
    if not os.path.exists(xmlb_path):
        # No master file — write a minimal one
        text = (
            f"XMLB MISSIONS {{\n"
            f"   maxmissions = 50 ;\n"
            f"   maxquestsperact = 100 ;\n"
            f"   maxtotalquests = 500 ;\n"
            f"\n"
            f"   MISSION {{\n"
            f"   name = {mission_name} ;\n"
            f"   }}\n"
            f"\n"
            f"}}\n"
        )
        text_path = xmlb_path + ".xml"
        with open(text_path, 'w') as f:
            f.write(text)
        try:
            compile_xml_to_xmlb(text_path, xmlb_path)
        except Exception as e:
            print(f"[MapMaker] Warning: failed to compile missions.xmlb: {e}")
        return

    # Read existing missions.xmlb
    try:
        from .xmlb import read_xmlb
        from pathlib import Path
        root = read_xmlb(Path(xmlb_path))
    except Exception as e:
        print(f"[MapMaker] Warning: failed to read missions.xmlb: {e}")
        return

    # Check if our mission is already registered
    existing = set()
    for child in root:
        if child.tag == 'MISSION':
            name = child.get('name', '')
            existing.add(name.lower())

    if mission_name.lower() in existing:
        return  # Already registered

    # Rebuild missions.xmlb with our mission added
    lines = ["XMLB MISSIONS {"]
    # Preserve original attributes
    for k in ('maxmissions', 'maxquestsperact', 'maxtotalquests'):
        val = root.get(k, '')
        if val:
            lines.append(f"   {k} = {val} ;")
    lines.append("")

    # Re-emit existing missions
    for child in root:
        if child.tag == 'MISSION':
            name = child.get('name', '')
            lines.append(f"   MISSION {{")
            lines.append(f"   name = {name} ;")
            lines.append(f"   }}")
            lines.append("")

    # Add our new mission
    lines.append(f"   MISSION {{")
    lines.append(f"   name = {mission_name} ;")
    lines.append(f"   }}")
    lines.append("")
    lines.append("}")
    lines.append("")

    text_path = xmlb_path + ".xml"
    with open(text_path, 'w') as f:
        f.write("\n".join(lines))
    try:
        compile_xml_to_xmlb(text_path, xmlb_path)
    except Exception as e:
        print(f"[MapMaker] Warning: failed to recompile missions.xmlb: {e}")


def _parse_eng_file(filepath):
    """Parse .eng (plain XML) or .engb (XMLB binary) to an ET.Element tree.

    Auto-detects the format by checking the first 4 bytes for the XMLB
    magic number (0x11B1 little-endian).  Falls back to standard XML parse.

    Args:
        filepath: Path to the .eng or .engb file.

    Returns:
        ET.Element root of the parsed XML tree.
    """
    import xml.etree.ElementTree as ET
    from pathlib import Path

    with open(filepath, 'rb') as f:
        magic = f.read(4)

    if len(magic) >= 4 and magic[0] == 0xB1 and magic[1] == 0x11:
        # XMLB binary format (XML2, MUA1, MUA2)
        from .xmlb import read_xmlb
        return read_xmlb(Path(filepath))
    else:
        # Plain XML text format (XML1)
        return ET.parse(filepath).getroot()


def _get_models_collection():
    """Get or create the MapMaker models collection."""
    name = "[MapMaker] Models"
    if name in bpy.data.collections:
        col = bpy.data.collections[name]
    else:
        col = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(col)
    return col


# Script property keys that indicate an entity MUST remain as an entity
# (because the game needs the entity for script/trigger logic)
_SCRIPT_PROPERTY_KEYS = {
    'actscript', 'zonescript', 'spawnscript', 'activatescript',
    'deactivatescript', 'deathscript', 'touchscript', 'usescript',
    'triggerscript', 'destructscript', 'damagescript', 'script',
}


def _entity_has_scripts(scene, entity_name):
    """Check if an entity def has any script properties that require it
    to remain as a separate entity (not merged to map).

    An entity with actscript, zonescript, spawnscript, etc. is typically
    a trigger/door/elevator that references game logic and MUST stay
    in the ENGB as an entity or its animations/scripts won't work.

    Returns True if the entity has scripts or is a gameplay-critical type.
    """
    for edef in scene.mm_entity_defs:
        if edef.entity_name != entity_name:
            continue

        # Gameplay entity classnames that should always stay as entities
        # even without explicit scripts (spawners, zone links, player starts)
        gameplay_classes = {
            'monsterspawnerent', 'playerstartent', 'zonelinkent',
            'cameramagnetent', 'waypointent',
        }
        if edef.classname in gameplay_classes:
            return True

        # Check custom properties for any script keys
        for prop in edef.properties:
            if prop.key.lower() in _SCRIPT_PROPERTY_KEYS:
                if prop.value:  # has a non-empty script reference
                    return True
        return False

    return False


def _load_entity_models(context, scene, models_dir, operator=None,
                        merge_to_map=False):
    """Load IGB model files for entity instances that reference models.

    Iterates entity definitions to find model references, then loads
    the corresponding .igb files and places them at each entity
    instance's position and rotation.

    When merge_to_map=True, models for entities WITHOUT scripts are
    merged directly into the map geometry collection instead of being
    placed as separate entity-linked instances. Entities with scripts
    (actscript, zonescript, etc.) are always kept as separate entities.

    Args:
        context: Blender context
        scene: bpy.types.Scene
        models_dir: Path to the models root directory
        operator: Optional operator for reporting
        merge_to_map: If True, merge non-scripted models into map collection

    Returns:
        Tuple of (models_loaded, instances_placed, models_missing, merged_count)
    """
    from ..importer.import_igb import import_igb
    import mathutils

    # Build map: entity_name -> model_path (from entity defs)
    # Also build tile_map: entity_name -> tilemodelfolder
    model_map = {}
    tile_map = {}  # entity_name -> tile folder path (for tileent)
    for edef in scene.mm_entity_defs:
        if edef.model:
            model_map[edef.entity_name] = edef.model
        # Check custom properties for 'model' or 'tilemodelfolder'
        for prop in edef.properties:
            if prop.key == "model" and prop.value:
                model_map[edef.entity_name] = prop.value
            elif prop.key == "tilemodelfolder" and prop.value:
                tile_map[edef.entity_name] = prop.value

    if not model_map and not tile_map:
        return (0, 0, 0, 0)

    entities_col = bpy.data.collections.get(ENTITY_COLLECTION_NAME)
    if not entities_col:
        return (0, 0, 0, 0)

    models_col = _get_models_collection()

    # For merge mode, get or create a "[MapMaker] Merged" collection
    merged_col = None
    if merge_to_map:
        merged_name = "[MapMaker] Merged"
        if merged_name in bpy.data.collections:
            merged_col = bpy.data.collections[merged_name]
        else:
            merged_col = bpy.data.collections.new(merged_name)
            bpy.context.scene.collection.children.link(merged_col)

    # Cache: model_path -> collection_name (the Blender collection created by import_igb)
    model_cache = {}
    models_loaded = 0
    instances_placed = 0
    models_missing = 0
    merged_count = 0

    # --- Handle tile entities (tilemodelfolder) ---
    # Tile instances have names like "T_tutorial_DAA_W_A0" which map to
    # "tutorial_daa_w_a0.igb" in the tilemodelfolder directory.
    tile_instances = []  # [(entity_empty, igb_path, etype)]
    for obj in entities_col.objects:
        etype = obj.get("mm_entity_type", "")
        if etype not in tile_map:
            continue

        folder = tile_map[etype].rstrip("/").rstrip("\\")
        inst_name = obj.name

        # Map instance name to IGB file: strip "T_" prefix, lowercase,
        # and strip Blender's .001/.002 suffix from duplicate names
        tile_name = inst_name
        # Strip Blender numeric suffix (e.g. "T_tutorial_DAA_W_A0.003")
        if '.' in tile_name:
            base, suffix = tile_name.rsplit('.', 1)
            if suffix.isdigit():
                tile_name = base
        if tile_name.startswith("T_"):
            tile_name = tile_name[2:]
        tile_name = tile_name.lower()

        # Resolve to full IGB path
        igb_path = os.path.join(
            models_dir, folder.replace("/", os.sep), tile_name + ".igb")
        if not os.path.exists(igb_path):
            # Also try with models/ prefix
            igb_path = os.path.join(
                models_dir, "models", folder.replace("/", os.sep),
                tile_name + ".igb")

        tile_instances.append((obj, igb_path, etype))

    # Load and place tile instances
    for entity_empty, igb_path, etype in tile_instances:
        if not os.path.exists(igb_path):
            models_missing += 1
            if operator:
                operator.report({'WARNING'},
                    f"Tile model not found: {igb_path}")
            continue

        if igb_path not in model_cache:
            existing_collections = set(bpy.data.collections.keys())
            result = import_igb(context, igb_path)
            if result == {'CANCELLED'}:
                models_missing += 1
                continue
            new_collections = (
                set(bpy.data.collections.keys()) - existing_collections)
            if not new_collections:
                models_missing += 1
                continue
            imported_col_name = new_collections.pop()
            model_cache[igb_path] = imported_col_name
            models_loaded += 1

        imported_col_name = model_cache[igb_path]
        imported_col = bpy.data.collections.get(imported_col_name)
        if not imported_col:
            continue

        source_objects = [o for o in imported_col.objects if o.type == 'MESH']
        if not source_objects:
            continue

        # Decide target collection
        is_scripted = _entity_has_scripts(scene, etype)
        if merge_to_map and not is_scripted:
            target_col = merged_col
            is_merged = True
        else:
            target_col = entities_col
            is_merged = False

        location = entity_empty.location
        rotation_z = entity_empty.rotation_euler.z

        for src_obj in source_objects:
            new_obj = bpy.data.objects.new(
                f"{entity_empty.name}_{src_obj.name}", src_obj.data)

            if is_merged:
                # Merged: place at full world position (no parent)
                rot_mat = mathutils.Matrix.Rotation(rotation_z, 4, 'Z')
                new_obj.matrix_world = (
                    mathutils.Matrix.Translation(location)
                    @ rot_mat
                    @ src_obj.matrix_world
                )
            else:
                # Parented: entity empty carries position/rotation,
                # child only needs the IGB model's internal transform
                new_obj.parent = entity_empty
                new_obj.matrix_parent_inverse = mathutils.Matrix.Identity(4)
                new_obj.matrix_local = src_obj.matrix_world.copy()

            target_col.objects.link(new_obj)

        if is_merged:
            merged_count += 1
        else:
            instances_placed += 1

    # Clean up tile source collections
    for igb_path, col_name in list(model_cache.items()):
        col = bpy.data.collections.get(col_name)
        if col:
            for src_obj in list(col.objects):
                col.objects.unlink(src_obj)
            if len(col.objects) == 0:
                bpy.data.collections.remove(col)
    # Reset cache for regular models (tile cache already consumed)
    model_cache.clear()

    # --- Handle regular model entities ---
    # Group instances by model path, keeping reference to entity empties
    instances_by_model = {}  # model_path -> [(entity_empty, etype)]
    for obj in entities_col.objects:
        etype = obj.get("mm_entity_type", "")
        if etype not in model_map:
            continue

        model_path = model_map[etype]
        if model_path not in instances_by_model:
            instances_by_model[model_path] = []
        instances_by_model[model_path].append((obj, etype))

    for model_path, instance_list in instances_by_model.items():
        # Resolve model to IGB file
        igb_path = _resolve_model_igb_path(model_path, models_dir)
        if igb_path is None:
            models_missing += 1
            if operator:
                operator.report({'WARNING'}, f"Model not found: {model_path}")
            continue

        if model_path not in model_cache:
            # First time seeing this model — import it
            existing_collections = set(bpy.data.collections.keys())

            result = import_igb(context, igb_path)
            if result == {'CANCELLED'}:
                models_missing += 1
                if operator:
                    operator.report({'WARNING'}, f"Failed to import model: {model_path}")
                continue

            # Find the collection created by import_igb
            new_collections = set(bpy.data.collections.keys()) - existing_collections
            if not new_collections:
                models_missing += 1
                continue

            imported_col_name = new_collections.pop()
            model_cache[model_path] = imported_col_name
            models_loaded += 1

        # Now place instances
        imported_col_name = model_cache[model_path]
        imported_col = bpy.data.collections.get(imported_col_name)
        if not imported_col:
            continue

        source_objects = [o for o in imported_col.objects if o.type == 'MESH']
        if not source_objects:
            continue

        for entity_empty, etype in instance_list:
            # Decide target collection: merge to map or keep as entity model
            is_scripted = _entity_has_scripts(scene, etype)
            if merge_to_map and not is_scripted:
                target_col = merged_col
                is_merged = True
            else:
                target_col = entities_col
                is_merged = False

            location = entity_empty.location
            rotation_z = entity_empty.rotation_euler.z

            for src_obj in source_objects:
                # Create linked duplicate
                new_obj = bpy.data.objects.new(
                    f"{entity_empty.name}_{src_obj.name}", src_obj.data
                )

                if is_merged:
                    # Merged: place at full world position (no parent)
                    rot_mat = mathutils.Matrix.Rotation(rotation_z, 4, 'Z')
                    new_obj.matrix_world = (
                        mathutils.Matrix.Translation(location)
                        @ rot_mat
                        @ src_obj.matrix_world
                    )
                else:
                    # Parented: entity empty carries position/rotation,
                    # child only needs the IGB model's internal transform
                    new_obj.parent = entity_empty
                    new_obj.matrix_parent_inverse = mathutils.Matrix.Identity(4)
                    new_obj.matrix_local = src_obj.matrix_world.copy()

                target_col.objects.link(new_obj)

            if is_merged:
                merged_count += 1
            else:
                instances_placed += 1

        # Unlink the original imported collection's objects from the scene
        for src_obj in list(imported_col.objects):
            imported_col.objects.unlink(src_obj)

        # Remove the now-empty imported collection
        if len(imported_col.objects) == 0:
            bpy.data.collections.remove(imported_col)

    return (models_loaded, instances_placed, models_missing, merged_count)


def _finalize_placed_models():
    """Make single user on mesh data for placed models.

    After ENG/ENGB import, models are linked duplicates sharing mesh data.
    This makes each object's data unique so they can be edited independently.

    Models parented under entity empties keep their transforms (the parent
    carries position/rotation). Only un-parented merged models get their
    transforms baked into the mesh.
    """
    import mathutils

    identity = mathutils.Matrix.Identity(4)

    # Process models in both Entities and Merged collections
    for col_name in (ENTITY_COLLECTION_NAME, "[MapMaker] Merged"):
        col = bpy.data.collections.get(col_name)
        if not col:
            continue

        for obj in col.objects:
            if obj.type != 'MESH' or obj.data is None:
                continue

            # Make single user — give this object its own unique mesh copy
            if obj.data.users > 1:
                obj.data = obj.data.copy()

            # Only bake transforms for un-parented (merged) objects
            if obj.parent is not None:
                continue

            mat = obj.matrix_world
            if mat == identity:
                continue

            obj.data.transform(mat)
            obj.data.update()
            obj.matrix_world = identity.copy()


class MM_OT_import_eng(Operator):
    """Import entities from an ENG (XML1 plain XML) or ENGB (XMLB binary) file"""
    bl_idname = "mm.import_eng"
    bl_label = "Import ENG/ENGB"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.eng;*.engb", options={'HIDDEN'})

    models_dir: StringProperty(
        name="Models Folder",
        description="Path to the game's models/ folder for loading entity model IGB files",
        subtype='DIR_PATH',
        default="",
    )
    load_models: BoolProperty(
        name="Load Models",
        description="Import IGB model files referenced by entities and place them in the scene",
        default=True,
    )
    merge_models: BoolProperty(
        name="Merge Models to Map",
        description=(
            "Merge imported models directly into the map geometry instead of "
            "placing them as separate entity instances. Entities with scripts "
            "(actscript, zonescript, spawnscript) are always kept as entities"
        ),
        default=False,
    )
    import_conversations: BoolProperty(
        name="Import Conversations",
        description="Auto-import conversation files referenced in precache entries",
        default=True,
    )
    skip_spawners: BoolProperty(
        name="Skip Enemy Spawners",
        description="Skip importing monsterspawnerent entities and their instances",
        default=False,
    )
    skip_playerstart: BoolProperty(
        name="Skip Player Start",
        description="Skip importing playerstartent entities and their instances",
        default=False,
    )
    skip_lights: BoolProperty(
        name="Skip Lights",
        description="Skip importing lightent entities and their instances",
        default=False,
    )
    skip_cameras: BoolProperty(
        name="Skip Cameras",
        description="Skip importing cameramagnetent entities and their instances",
        default=False,
    )
    skip_waypoints: BoolProperty(
        name="Skip Waypoints",
        description="Skip importing waypointent entities and their instances",
        default=False,
    )

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        layout = self.layout

        # Model loading
        layout.prop(self, "load_models")
        col = layout.column()
        col.enabled = self.load_models
        col.prop(self, "models_dir")
        col.prop(self, "merge_models")

        layout.separator()
        layout.prop(self, "import_conversations")

        # Entity type filters
        layout.separator()
        box = layout.box()
        box.label(text="Skip Entity Types:")
        col = box.column(align=True)
        col.prop(self, "skip_spawners")
        col.prop(self, "skip_playerstart")
        col.prop(self, "skip_lights")
        col.prop(self, "skip_cameras")
        col.prop(self, "skip_waypoints")

    def execute(self, context):
        from .xml_gen import import_engb_xml

        if not os.path.exists(self.filepath):
            self.report({'ERROR'}, f"File not found: {self.filepath}")
            return {'CANCELLED'}

        # Auto-detect format and parse
        try:
            root = _parse_eng_file(self.filepath)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to parse file: {e}")
            return {'CANCELLED'}

        # Build skip-classnames set from user options
        skip_classes = set()
        if self.skip_spawners:
            skip_classes.add('monsterspawnerent')
        if self.skip_playerstart:
            skip_classes.add('playerstartent')
        if self.skip_lights:
            skip_classes.add('lightent')
        if self.skip_cameras:
            skip_classes.add('cameramagnetent')
        if self.skip_waypoints:
            skip_classes.add('waypointent')

        # Import entities into scene PropertyGroups
        try:
            stats = import_engb_xml(context.scene, root,
                                    skip_classes=skip_classes)
        except Exception as e:
            self.report({'ERROR'}, f"Entity import failed: {e}")
            return {'CANCELLED'}

        msg = (f"Imported {stats['entity_defs']} entity defs, "
               f"{stats['instances']} instances, "
               f"{stats['precache']} precache entries")
        if stats.get('skipped_defs', 0) > 0:
            msg += f" (skipped {stats['skipped_defs']} defs)"

        # Optionally load model IGB files
        if self.load_models and self.models_dir and os.path.isdir(self.models_dir):
            loaded, placed, missing, merged = _load_entity_models(
                context, context.scene, self.models_dir, self,
                merge_to_map=self.merge_models,
            )
            msg += f" | Models: {loaded} loaded, {placed} placed"
            if merged > 0:
                msg += f", {merged} merged to map"
            if missing > 0:
                msg += f", {missing} missing"

            # Post-import: make single user + apply transforms on all placed models
            if loaded > 0:
                _finalize_placed_models()

        elif self.load_models and self.models_dir and not os.path.isdir(self.models_dir):
            self.report({'WARNING'}, f"Models directory not found: {self.models_dir}")

        # Optionally auto-import conversations from precache entries
        if self.import_conversations:
            conv_count = _import_conversations_from_precache(
                context.scene, self.filepath, self)
            if conv_count > 0:
                msg += f" | {conv_count} conversations imported"

        self.report({'INFO'}, msg)
        return {'FINISHED'}


class MM_OT_import_full_map(Operator):
    """Import an existing game map (geometry + entities + settings) so it can be edited and rebuilt.

    Pick the map's .igb (e.g. maps/act1/stark/stark1.igb). The operator:
    - imports the map geometry (meshes, materials, lights, collision)
    - finds the sibling .engb/.eng and imports all entity defs + instances
    - fills in Map Maker settings (map name, map path, game data dir) from
      the file's location so Build All can write a working replacement map
    """
    bl_idname = "mm.import_full_map"
    bl_label = "Import Full Map"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.igb", options={'HIDDEN'})

    load_geometry: BoolProperty(
        name="Import Map Geometry",
        description="Import the map IGB (meshes, materials, lights, collision)",
        default=True,
    )
    load_models: BoolProperty(
        name="Load Entity Models",
        description=("Import IGB model files referenced by entities "
                     "(doors, props) from the game's models/ folder"),
        default=True,
    )
    merge_models: BoolProperty(
        name="Merge Models to Map",
        description=(
            "Merge imported models directly into the map geometry instead of "
            "placing them as separate entity instances. Entities with scripts "
            "are always kept as entities"
        ),
        default=False,
    )
    import_conversations: BoolProperty(
        name="Import Conversations",
        description="Auto-import conversation files referenced in precache entries",
        default=True,
    )
    load_motionpaths: BoolProperty(
        name="Import Motion Paths",
        description=("Scan the map's scripts for motion path references and "
                     "import them as animated empties + path curves"),
        default=True,
    )
    load_npc_actors: BoolProperty(
        name="Load NPC Actors",
        description=("Import each NPC spawner's rigged character model with "
                     "its idle animation, parented to the spawner empty "
                     "(slower — one actor import per unique character)"),
        default=True,
    )
    load_scene_audio: BoolProperty(
        name="Import Scene Audio",
        description=("Decode the map's ambience + music from its ZSNDPC "
                     "sound banks into Video Sequencer strips so the scene "
                     "plays with game audio"),
        default=True,
    )
    game_look: BoolProperty(
        name="Game Shading",
        description=("Set up game-accurate rendering: raw-sRGB view "
                     "transform, baked vertex lighting on map materials, "
                     "converted game light energies"),
        default=True,
    )

    def invoke(self, context, event):
        # Start the file browser in the game's maps/ folder when known
        settings = context.scene.mm_settings
        if not self.filepath and settings.game_data_dir:
            maps_dir = os.path.join(bpy.path.abspath(settings.game_data_dir), "maps")
            if os.path.isdir(maps_dir):
                self.filepath = maps_dir + os.sep
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "load_geometry")
        layout.prop(self, "load_models")
        col = layout.column()
        col.enabled = self.load_models
        col.prop(self, "merge_models")
        layout.prop(self, "import_conversations")
        layout.prop(self, "load_motionpaths")
        layout.prop(self, "load_npc_actors")
        layout.prop(self, "load_scene_audio")
        layout.prop(self, "game_look")

    @staticmethod
    def _derive_game_paths(map_dir):
        """Walk up from the map folder to the dir containing maps/.

        Returns (game_root, map_path) — e.g. for
        <root>/maps/act1/stark → (<root>, "act1/stark").
        Both empty strings if no maps/ ancestor exists.
        """
        parts = []
        d = os.path.normpath(map_dir)
        while True:
            parent, name = os.path.split(d)
            if not name:
                return "", ""
            if name.lower() == "maps":
                return parent, "/".join(reversed(parts))
            parts.append(name)
            d = parent

    def execute(self, context):
        # Cursor progress for the long stages (geometry ~20 s, NPC actors
        # ~35 s) so the UI doesn't look frozen.
        wm = context.window_manager
        try:
            wm.progress_begin(0, 10)
        except Exception:
            wm = None
        try:
            return self._execute_stages(context, wm)
        finally:
            if wm is not None:
                try:
                    wm.progress_end()
                except Exception:
                    pass

    @staticmethod
    def _progress(wm, value):
        if wm is not None:
            try:
                wm.progress_update(value)
            except Exception:
                pass

    def _execute_stages(self, context, wm=None):
        from .xml_gen import import_engb_xml

        map_igb = os.path.abspath(self.filepath)
        if not os.path.isfile(map_igb):
            self.report({'ERROR'}, f"File not found: {map_igb}")
            return {'CANCELLED'}

        map_dir = os.path.dirname(map_igb)
        base = os.path.splitext(os.path.basename(map_igb))[0]
        game_root, map_path = self._derive_game_paths(map_dir)

        # --- Map Maker settings from the file's location ---
        settings = context.scene.mm_settings
        settings.map_name = base
        if map_path:
            settings.map_path = map_path
        if game_root:
            settings.game_data_dir = game_root

        msgs = []

        # --- Map geometry ---
        self._progress(wm, 1)
        if self.load_geometry:
            from ..importer.import_igb import import_igb
            try:
                result = import_igb(context, map_igb, operator=None)
            except Exception as e:
                self.report({'ERROR'}, f"Map geometry import failed: {e}")
                return {'CANCELLED'}
            if result != {'FINISHED'}:
                self.report({'WARNING'}, "Map geometry import did not finish cleanly")
            else:
                coll = bpy.data.collections.get(base)
                n_objs = len(coll.objects) if coll else 0
                msgs.append(f"geometry: {n_objs} objects")

        # --- Entities from the sibling .engb / .eng ---
        self._progress(wm, 4)
        eng_path = None
        for ext in (".engb", ".eng"):
            cand = os.path.join(map_dir, base + ext)
            if os.path.isfile(cand):
                eng_path = cand
                break

        if eng_path is None:
            self.report(
                {'WARNING'},
                f"No {base}.engb next to the map IGB — imported geometry only")
            self.report({'INFO'}, " | ".join(msgs) if msgs else "Nothing imported")
            return {'FINISHED'}

        try:
            root = _parse_eng_file(eng_path)
            stats = import_engb_xml(context.scene, root, skip_classes=set())
        except Exception as e:
            self.report({'ERROR'}, f"Entity import failed ({eng_path}): {e}")
            return {'CANCELLED'}
        msgs.append(f"{stats['entity_defs']} entity defs")
        msgs.append(f"{stats['instances']} instances")
        if stats['precache']:
            msgs.append(f"{stats['precache']} precache")

        # import_engb_xml may overwrite identity fields from the world entity;
        # the file location is authoritative for an existing game map.
        settings.map_name = base
        if map_path:
            settings.map_path = map_path

        # --- Entity models (doors, props, …) ---
        self._progress(wm, 5)
        models_dir = os.path.join(game_root, "models") if game_root else ""
        if self.load_models and models_dir and os.path.isdir(models_dir):
            loaded, placed, missing, merged = _load_entity_models(
                context, context.scene, models_dir, self,
                merge_to_map=self.merge_models,
            )
            m = f"models: {loaded} loaded, {placed} placed"
            if merged > 0:
                m += f", {merged} merged"
            if missing > 0:
                m += f", {missing} missing"
            msgs.append(m)
            if loaded > 0:
                _finalize_placed_models()
        elif self.load_models and not models_dir:
            self.report({'WARNING'},
                        "Could not locate the game's models/ folder "
                        "(map is not under a maps/ directory)")

        # --- Conversations referenced by precache ---
        if self.import_conversations:
            conv_count = _import_conversations_from_precache(
                context.scene, eng_path, self)
            if conv_count > 0:
                msgs.append(f"{conv_count} conversations")

        # --- NPC actors (rigged characters + idle) at spawner empties ---
        self._progress(wm, 7)
        if self.load_npc_actors and game_root:
            try:
                n_npc, n_skipped = _load_npc_actors(
                    context, context.scene, game_root, self)
                if n_npc:
                    m = f"{n_npc} NPC actors"
                    if n_skipped:
                        m += f" ({n_skipped} unresolved)"
                    msgs.append(m)
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.report({'WARNING'}, f"NPC actor load failed: {e}")

        # --- Scene audio (ambience + music) from the map's sound banks ---
        self._progress(wm, 9)
        if self.load_scene_audio and game_root:
            try:
                from .voice_audio import add_map_audio_strips
                n_audio = add_map_audio_strips(
                    context, game_root, settings.soundfile, base)
                if n_audio:
                    msgs.append(f"{n_audio} audio strips")
            except Exception as e:
                self.report({'WARNING'}, f"Scene audio failed: {e}")

        # --- Game-accurate shading (view transform, baked lighting, lights) ---
        if self.game_look:
            try:
                from .game_shading import apply_game_shading
                gs = apply_game_shading(context)
                msgs.append(f"game shading ({gs['baked_mats']} baked mats, "
                            f"{gs['lights']} lights)")
            except Exception as e:
                self.report({'WARNING'}, f"Game shading failed: {e}")

        # --- Motion paths referenced by the map's scripts ---
        if self.load_motionpaths and game_root:
            from .motionpath_import import (
                find_map_motionpaths, parse_motionpath_igb,
                build_motionpath_objects,
            )
            mp_files = find_map_motionpaths(game_root, map_path, base)
            n_paths = 0
            if mp_files:
                mp_coll = bpy.data.collections.get("[MapMaker] Motion Paths")
                if mp_coll is None:
                    mp_coll = bpy.data.collections.new("[MapMaker] Motion Paths")
                    context.scene.collection.children.link(mp_coll)
                for mp_file in mp_files:
                    try:
                        tracks = parse_motionpath_igb(mp_file)
                        n_paths += len(build_motionpath_objects(
                            context, tracks, mp_coll, source_path=mp_file))
                    except Exception as e:
                        self.report({'WARNING'},
                                    f"Motion path failed ({mp_file}): {e}")
            if n_paths:
                msgs.append(f"{n_paths} motion paths")

        self.report({'INFO'}, f"Imported map '{base}': " + ", ".join(msgs))
        return {'FINISHED'}


def _pick_idle_animation(anims):
    """Pick the best standing-around ParsedAnimation from a moveset.

    Preference: exact 'idle' > any name containing 'idle' > calm poses
    ('stand', 'talk', 'wait', 'menu') > first anim that isn't the fake
    bind-pose ('afakeanim') or an action move.
    """
    if not anims:
        return None
    usable = [pa for pa in anims if pa.name.lower() != 'afakeanim']
    if not usable:
        return anims[0]
    by_name = {pa.name.lower(): pa for pa in usable}
    if 'idle' in by_name:
        return by_name['idle']
    for pa in usable:
        if 'idle' in pa.name.lower():
            return pa
    for keyword in ('stand', 'talk', 'wait', 'menu'):
        for pa in usable:
            if keyword in pa.name.lower():
                return pa
    action_words = ('atk', 'attack', 'jump', 'death', 'hit', 'run', 'walk',
                    'block', 'charge', 'throw', 'land', 'fall', 'stun',
                    'knock', 'pain', 'swim')
    for pa in usable:
        n = pa.name.lower()
        if not any(w in n for w in action_words):
            return pa
    return usable[0]


def _load_npc_actors(context, scene, game_root, operator=None):
    """Import rigged character models (+idle animation) at NPC spawner empties.

    For each monsterspawnerent entity def: character → npcstat/herostat
    (game_database) → skin IGB (actors/NNNN.igb) + moveset IGB
    (actors/<characteranims>.igb). The skin is imported once per character
    (briefing-loader pattern: hidden ref rig from the moveset skeleton +
    build_action with rebase_unanimated_parents), then cheaply duplicated
    for every additional spawner instance and parented to the spawner empty
    so moving the empty moves the NPC.

    Returns (placed_count, unresolved_count).
    """
    from .game_database import get_character_db, get_skin_actor_path
    from ..actor.actor_import import import_actor
    from ..actor.animation_builder import build_action
    from ..actor.sg_animation import extract_animations
    from ..actor.sg_skeleton import extract_skeleton
    from ..igb_format.igb_reader import IGBReader

    def report(level, msg):
        if operator is not None:
            operator.report({level}, msg)
        else:
            print(f"[{level}] {msg}")

    char_db = get_character_db(game_root) or {}
    # case-insensitive lookup (engb character fields vary in casing)
    char_lut = {k.lower(): v for k, v in char_db.items()}

    ents_coll = bpy.data.collections.get(ENTITY_COLLECTION_NAME)
    if ents_coll is None:
        return 0, 0

    npc_coll = bpy.data.collections.get("[MapMaker] NPC Actors")
    if npc_coll is None:
        npc_coll = bpy.data.collections.new("[MapMaker] NPC Actors")
        scene.collection.children.link(npc_coll)

    # spawner defs → their instance empties
    inst_by_type = {}
    for obj in ents_coll.objects:
        et = obj.get("mm_entity_type")
        if et:
            inst_by_type.setdefault(et, []).append(obj)

    ref_rigs = {}      # characteranims -> (ref_arm, {anim_name: ParsedAnimation})
    actor_cache = {}   # skin_path -> (armature, [mesh objs], action or None)
    placed = 0
    unresolved = 0

    for ent in scene.mm_entity_defs:
        if ent.classname != 'monsterspawnerent':
            continue
        instances = inst_by_type.get(ent.entity_name, [])
        if not instances:
            continue

        char_key = (ent.character or ent.monster_name or "").strip().lower()
        ci = char_lut.get(char_key)
        if ci is None and ent.monster_name:
            ci = char_lut.get(ent.monster_name.strip().lower())
        if ci is None or not ci.skin:
            unresolved += len(instances)
            continue
        skin_path = get_skin_actor_path(ci.skin, game_root)
        if not skin_path or not os.path.exists(skin_path):
            unresolved += len(instances)
            continue

        # --- moveset ref rig + idle anim (once per characteranims) ---
        ref_entry = None
        if ci.characteranims:
            ref_entry = ref_rigs.get(ci.characteranims)
            if ref_entry is None:
                anim_path = os.path.join(
                    game_root, "actors", f"{ci.characteranims}.igb")
                if os.path.exists(anim_path):
                    try:
                        r = import_actor(
                            context, anim_path, skin_filepaths=None,
                            game_dir=game_root,
                            options={"import_animations": False},
                            operator=None)
                        ref_arm = r[0] if r else None
                        anims = {}
                        if ref_arm is not None:
                            ar = IGBReader(anim_path)
                            ar.read()
                            sk = extract_skeleton(ar)
                            for pa in extract_animations(ar, sk):
                                anims[pa.name] = pa
                            for c in list(ref_arm.users_collection):
                                c.objects.unlink(ref_arm)
                            npc_coll.objects.link(ref_arm)
                            ref_arm.hide_set(True)
                            ref_arm.hide_render = True
                            ref_arm["mm_npc_ref_rig"] = ci.characteranims
                        ref_entry = (ref_arm, anims)
                    except Exception as e:
                        report('WARNING',
                               f"Moveset load failed ({ci.characteranims}): {e}")
                        ref_entry = (None, {})
                else:
                    ref_entry = (None, {})
                ref_rigs[ci.characteranims] = ref_entry

        # --- base actor (once per skin) ---
        base = actor_cache.get(skin_path)
        if base is None:
            try:
                r = import_actor(context, skin_path,
                                 skin_filepaths=[(ci.skin, skin_path)],
                                 game_dir=game_root,
                                 options={"import_animations": False},
                                 operator=None)
            except Exception as e:
                report('WARNING', f"Actor import failed ({ci.skin}): {e}")
                unresolved += len(instances)
                continue
            if not r:
                unresolved += len(instances)
                continue
            arm = r[0]
            meshes = [o for (_v, o) in (r[1] or [])]
            for obj in [arm] + meshes:
                for c in list(obj.users_collection):
                    c.objects.unlink(obj)
                npc_coll.objects.link(obj)

            action = None
            if ref_entry is not None:
                ref_arm, anims = ref_entry
                pa = _pick_idle_animation(list(anims.values()))
                if pa is not None and ref_arm is not None:
                    try:
                        action = build_action(
                            arm, pa, bind_pose=None, fps=None,
                            anim_armature=ref_arm,
                            rebase_unanimated_parents=True,
                            relative_translation=True,
                            root_motion_mode='vertical')
                    except Exception as e:
                        report('WARNING',
                               f"Idle apply failed ({ci.skin}): {e}")
            if action is not None:
                if arm.animation_data is None:
                    arm.animation_data_create()
                arm.animation_data.action = action
            base = (arm, meshes, action)
            actor_cache[skin_path] = base

        # --- one placed actor per instance empty ---
        base_arm, base_meshes, action = base
        first_use = base_arm.parent is None and not base_arm.get("mm_npc_actor")
        for i, empty in enumerate(instances):
            if i == 0 and first_use:
                arm, meshes = base_arm, base_meshes
            else:
                arm = base_arm.copy()
                npc_coll.objects.link(arm)
                if action is not None:
                    if arm.animation_data is None:
                        arm.animation_data_create()
                    arm.animation_data.action = action
                meshes = []
                for m in base_meshes:
                    nm = m.copy()
                    nm.parent = arm
                    for mod in nm.modifiers:
                        if mod.type == 'ARMATURE' and mod.object == base_arm:
                            mod.object = arm
                    npc_coll.objects.link(nm)
                    meshes.append(nm)
            arm["mm_npc_actor"] = ent.entity_name
            # Parent to the spawner empty: the empty (pos/orient from the
            # .engb inst) is the transform source of truth for export.
            arm.parent = empty
            arm.matrix_parent_inverse.identity()
            arm.location = (0.0, 0.0, 0.0)
            arm.rotation_euler = (0.0, 0.0, 0.0)
            placed += 1

    return placed, unresolved


class MM_OT_game_shading(Operator):
    """Make the scene render like in-game: raw-sRGB view transform, baked vertex lighting on map materials, correct game light energies"""
    bl_idname = "mm.game_shading"
    bl_label = "Game Shading"
    bl_options = {'REGISTER', 'UNDO'}

    baked_brightness: FloatProperty(
        name="Baked Brightness",
        description=("Multiplier for vertex-color baked map materials "
                     "(the game renders these modulate-2X)"),
        default=2.0, min=0.5, max=4.0,
    )
    light_power: FloatProperty(
        name="Light Power",
        description="Global multiplier on converted game light energies",
        default=1.0, min=0.05, max=10.0,
    )

    def execute(self, context):
        from .game_shading import apply_game_shading
        try:
            s = apply_game_shading(context,
                                   baked_brightness=self.baked_brightness,
                                   light_power=self.light_power,
                                   operator=self)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f"Game shading failed: {e}")
            return {'CANCELLED'}
        self.report({'INFO'},
                    f"Game shading: {s['baked_mats']} baked materials, "
                    f"{s['lights']} lights converted, view=Standard")
        return {'FINISHED'}


def _find_game_installs():
    """Scan Steam/GOG/common install paths for XML1/XML2/MUA game roots.

    A game root = a directory containing maps/ + actors/ (and usually
    XMen2.exe or Game.exe). Returns list of (label, path).
    """
    candidates = []

    # Steam libraries from the registry
    try:
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE,
                           r"SOFTWARE\WOW6432Node\Valve\Steam")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    steam, _ = winreg.QueryValueEx(k, "SteamPath" if hive ==
                                                   winreg.HKEY_CURRENT_USER
                                                   else "InstallPath")
                    candidates.append(os.path.join(steam, "steamapps",
                                                   "common"))
                    vdf = os.path.join(steam, "steamapps",
                                       "libraryfolders.vdf")
                    if os.path.isfile(vdf):
                        import re
                        text = open(vdf, "r", errors="replace").read()
                        for m in re.finditer(r'"path"\s+"([^"]+)"', text):
                            candidates.append(os.path.join(
                                m.group(1).replace("\\\\", "\\"),
                                "steamapps", "common"))
            except OSError:
                continue
    except Exception:
        pass

    # Common fixed locations
    for drive in "CDEFG":
        for sub in (r"Program Files (x86)\Activision",
                    r"Program Files\Activision",
                    r"GOG Games", r"Games"):
            candidates.append(f"{drive}:\\{sub}")

    found = []
    seen = set()
    for root in candidates:
        if not root or not os.path.isdir(root):
            continue
        try:
            children = os.listdir(root)
        except OSError:
            continue
        for child in children:
            p = os.path.join(root, child)
            key = os.path.normcase(p)
            if key in seen or not os.path.isdir(p):
                continue
            seen.add(key)
            if (os.path.isdir(os.path.join(p, "maps"))
                    and os.path.isdir(os.path.join(p, "actors"))):
                found.append((child, p))
    return found


class MM_OT_autodetect_game_dir(Operator):
    """Scan Steam/GOG/common install folders for XML1/XML2/MUA and fill Game Data Dir"""
    bl_idname = "mm.autodetect_game_dir"
    bl_label = "Auto-Detect Game Folder"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.mm_settings
        found = _find_game_installs()
        if not found:
            self.report({'WARNING'},
                        "No game install found (looked in Steam libraries, "
                        "Activision/GOG folders). Set Game Data Dir manually.")
            return {'CANCELLED'}
        # Prefer MUA > XML2 > first (MUA unlocks the most features)
        def rank(item):
            n = item[0].lower()
            if "ultimate alliance" in n or "mua" in n:
                return 0
            if "legends ii" in n or "legends 2" in n or "xml2" in n:
                return 1
            return 2
        found.sort(key=rank)
        settings.game_data_dir = found[0][1]
        extra = f" (+{len(found)-1} more found)" if len(found) > 1 else ""
        self.report({'INFO'},
                    f"Game Data Dir → {found[0][1]}{extra}")
        return {'FINISHED'}


class MM_OT_export_motionpath(Operator):
    """Export the selected animated motion-path empty back to a motionpaths .igb (patches its original file as template)"""
    bl_idname = "mm.export_motionpath"
    bl_label = "Export Motion Path"
    bl_options = {'REGISTER'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.igb", options={'HIDDEN'})
    sample_fps: FloatProperty(
        name="Keys / Second",
        description="Resample rate written to the file (native paths use 30)",
        default=30.0, min=1.0, max=120.0,
    )

    @classmethod
    def poll(cls, context):
        o = context.active_object
        return (o is not None and o.get("igb_motionpath") is not None
                and o.animation_data is not None
                and o.animation_data.action is not None)

    def invoke(self, context, event):
        o = context.active_object
        src = o.get("igb_motionpath_src", "") if o else ""
        if src:
            self.filepath = src  # default: overwrite-in-place style path
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from .motionpath_export import export_motionpath, sample_empty_track
        empty = context.active_object
        template = empty.get("igb_motionpath_src", "")
        if not template or not os.path.exists(template):
            self.report({'ERROR'},
                        "No template: this empty wasn't imported from a "
                        "motionpath .igb (igb_motionpath_src missing)")
            return {'CANCELLED'}
        try:
            times, locs, quats, scales = sample_empty_track(
                empty, fps=self.sample_fps)
            n = export_motionpath(template, self.filepath, times, locs,
                                  quats, scales)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f"Motion path export failed: {e}")
            return {'CANCELLED'}
        self.report({'INFO'},
                    f"Wrote {n} keys ({times[-1]:.2f}s) → "
                    f"{os.path.basename(self.filepath)}")
        return {'FINISHED'}


class MM_OT_simulate_play(Operator):
    """Play the scene like in-game: looping staggered NPC idles, motion paths, spawn conditions, audio-synced playback"""
    bl_idname = "mm.simulate_play"
    bl_label = "Simulate Map"
    bl_options = {'REGISTER', 'UNDO'}

    apply_conditions: BoolProperty(
        name="Apply Spawn Conditions",
        description=("Hide NPCs the game would not spawn at map load "
                     "(script-spawned without instantspawn, hidden, "
                     "startinactive)"),
        default=True,
    )
    use_rendered: BoolProperty(
        name="Rendered Viewport",
        description=("Switch the viewport to Rendered shading (game lights); "
                     "off = Material Preview (faster)"),
        default=False,
    )

    def execute(self, context):
        from .simulate import start_simulation
        try:
            s = start_simulation(context,
                                 apply_conditions=self.apply_conditions,
                                 use_rendered=self.use_rendered)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f"Simulation failed: {e}")
            return {'CANCELLED'}
        self.report({'INFO'},
                    f"Simulating: {s['looped']} NPC idles looping, "
                    f"{s['hidden']} hidden by spawn conditions, "
                    f"{s['paths']} motion paths, {s['audio']} audio strips")
        return {'FINISHED'}


class MM_OT_simulate_stop(Operator):
    """Stop the simulation and restore the scene (actions, visibility, paths)"""
    bl_idname = "mm.simulate_stop"
    bl_label = "Stop Simulation"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .simulate import stop_simulation
        try:
            s = stop_simulation(context)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f"Stop failed: {e}")
            return {'CANCELLED'}
        self.report({'INFO'},
                    f"Simulation stopped: {s['restored']} actions restored, "
                    f"{s['unhidden']} NPCs unhidden")
        return {'FINISHED'}


class MM_OT_import_motionpath(Operator):
    """Import motion path IGB file(s) as animated empties + path curves"""
    bl_idname = "mm.import_motionpath"
    bl_label = "Import Motion Path"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH')
    directory: StringProperty(subtype='DIR_PATH')
    files: CollectionProperty(type=bpy.types.OperatorFileListElement,
                              options={'HIDDEN'})
    filter_glob: StringProperty(default="*.igb", options={'HIDDEN'})

    def invoke(self, context, event):
        settings = context.scene.mm_settings
        if not self.filepath and settings.game_data_dir:
            mp_dir = os.path.join(
                bpy.path.abspath(settings.game_data_dir), "motionpaths")
            if os.path.isdir(mp_dir):
                self.filepath = mp_dir + os.sep
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from .motionpath_import import (
            parse_motionpath_igb, build_motionpath_objects)

        # Multi-select support: files + directory, else single filepath
        paths = []
        if self.files and self.directory:
            paths = [os.path.join(self.directory, f.name)
                     for f in self.files if f.name]
        if not paths and self.filepath:
            paths = [self.filepath]
        paths = [p for p in paths if os.path.isfile(p)]
        if not paths:
            self.report({'ERROR'}, "No motion path file selected")
            return {'CANCELLED'}

        coll = bpy.data.collections.get("[MapMaker] Motion Paths")
        if coll is None:
            coll = bpy.data.collections.new("[MapMaker] Motion Paths")
            context.scene.collection.children.link(coll)

        n_paths = 0
        for p in paths:
            try:
                tracks = parse_motionpath_igb(p)
            except Exception as e:
                self.report({'WARNING'}, f"Failed to parse {p}: {e}")
                continue
            if not tracks:
                self.report({'WARNING'},
                            f"No motion path tracks in {os.path.basename(p)}")
                continue
            n_paths += len(build_motionpath_objects(context, tracks, coll,
                                                    source_path=p))

        if n_paths == 0:
            self.report({'ERROR'}, "No motion paths imported")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Imported {n_paths} motion path(s)")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Conversation auto-import helpers (used by MM_OT_import_eng)
# ---------------------------------------------------------------------------

def _resolve_conversation_path(filename, game_dir, engb_dir):
    """Try to find a conversation file on disk.

    Searches for both .engb (XML2 binary) and .eng (XML1 plain text)
    extensions in multiple candidate locations.

    Args:
        filename: precache filename (e.g. 'act1/sanctuary/1_sanctuary1_010')
        game_dir: game data root directory
        engb_dir: directory of the importing ENGB/ENG file

    Returns:
        Absolute path to the conversation file, or None.
    """
    basename = filename.replace('\\', '/').split('/')[-1]
    full_rel = filename.replace('\\', '/')

    candidates = []

    # 1. game_dir/conversations/{full_path}.engb / .eng
    if game_dir:
        conv_base = os.path.join(game_dir, "conversations")
        for ext in ('.engb', '.eng'):
            candidates.append(
                os.path.join(conv_base, full_rel.replace('/', os.sep) + ext))

    # 2. Same directory as the ENGB being imported
    if engb_dir:
        for ext in ('.engb', '.eng'):
            candidates.append(os.path.join(engb_dir, basename + ext))

    # 3. game_dir/conversations/{basename}.engb / .eng
    if game_dir:
        conv_base = os.path.join(game_dir, "conversations")
        for ext in ('.engb', '.eng'):
            candidates.append(os.path.join(conv_base, basename + ext))

    # 4. Walk upward from engb_dir looking for conversations/ subfolder
    if engb_dir:
        search_dir = engb_dir
        for _ in range(5):
            conv_dir = os.path.join(search_dir, "conversations")
            if os.path.isdir(conv_dir):
                for ext in ('.engb', '.eng'):
                    candidates.append(os.path.join(
                        conv_dir, full_rel.replace('/', os.sep) + ext))
                    candidates.append(os.path.join(conv_dir, basename + ext))
                break
            parent = os.path.dirname(search_dir)
            if parent == search_dir:
                break
            search_dir = parent

    for path in candidates:
        if os.path.exists(path):
            return path

    return None


def _import_conversations_from_precache(scene, engb_filepath, operator):
    """Import conversation files referenced in precache entries.

    After an ENGB import populates scene.mm_precache, this function finds
    all conversation entries, locates the corresponding .engb/.eng files,
    and imports each one into scene.mm_conversations.

    Args:
        scene: bpy.types.Scene
        engb_filepath: path to the ENGB file being imported
        operator: Blender operator for reporting (or None)

    Returns:
        Number of conversations imported.
    """
    from .conversation import xml_to_nodes

    game_dir = ''
    if hasattr(scene, 'mm_settings'):
        game_dir = getattr(scene.mm_settings, 'game_data_dir', '')

    engb_dir = os.path.dirname(os.path.abspath(engb_filepath))

    # Build set of already-imported conversation names
    existing_names = {c.conv_name for c in scene.mm_conversations}

    count = 0
    for entry in scene.mm_precache:
        if entry.entry_type != 'conversation':
            continue
        if not entry.filename:
            continue

        conv_path = _resolve_conversation_path(
            entry.filename, game_dir, engb_dir)

        if conv_path is None:
            if operator:
                operator.report({'WARNING'},
                    f"Conversation not found: {entry.filename}")
            continue

        # Extract name from filename
        conv_name = os.path.splitext(os.path.basename(conv_path))[0]
        if conv_name in existing_names:
            continue

        try:
            root = _parse_eng_file(conv_path)
            conv = scene.mm_conversations.add()
            conv.conv_name = conv_name

            # Extract path prefix from precache filename
            parts = entry.filename.replace('\\', '/').rsplit('/', 1)
            if len(parts) > 1:
                conv.conv_path = parts[0]

            xml_to_nodes(conv, root)
            existing_names.add(conv_name)
            count += 1
        except Exception as e:
            if operator:
                operator.report({'WARNING'},
                    f"Failed to import conversation '{conv_name}': {e}")

    return count


# ===========================================================================
# Conversation operators
# ===========================================================================

class MM_OT_add_conversation(Operator):
    """Add a new conversation"""
    bl_idname = "mm.add_conversation"
    bl_label = "Add Conversation"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        conv = scene.mm_conversations.add()
        conv.conv_name = f"conversation_{len(scene.mm_conversations):03d}"
        scene.mm_conversations_index = len(scene.mm_conversations) - 1
        return {'FINISHED'}


class MM_OT_remove_conversation(Operator):
    """Remove the selected conversation"""
    bl_idname = "mm.remove_conversation"
    bl_label = "Remove Conversation"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return (len(scene.mm_conversations) > 0 and
                0 <= scene.mm_conversations_index < len(scene.mm_conversations))

    def execute(self, context):
        scene = context.scene
        scene.mm_conversations.remove(scene.mm_conversations_index)
        scene.mm_conversations_index = min(
            scene.mm_conversations_index,
            len(scene.mm_conversations) - 1
        )
        return {'FINISHED'}


class MM_OT_add_start_condition(Operator):
    """Add a startCondition skeleton (startCondition + participant + line + response)"""
    bl_idname = "mm.add_start_condition"
    bl_label = "Add Start Condition"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return 0 <= scene.mm_conversations_index < len(scene.mm_conversations)

    def execute(self, context):
        from .conversation import allocate_node_id, get_next_sort_order

        scene = context.scene
        conv = scene.mm_conversations[scene.mm_conversations_index]

        # Create startCondition
        sc = conv.nodes.add()
        sc.node_id = allocate_node_id(conv)
        sc.parent_id = -1
        sc.node_type = 'START_CONDITION'
        sc.sort_order = get_next_sort_order(conv, -1)
        sc_id = sc.node_id

        # Create participant under startCondition
        part = conv.nodes.add()
        part.node_id = allocate_node_id(conv)
        part.parent_id = sc_id
        part.node_type = 'PARTICIPANT'
        part.sort_order = 0
        part.participant_name = "default"
        part_id = part.node_id

        # Create line under participant
        line = conv.nodes.add()
        line.node_id = allocate_node_id(conv)
        line.parent_id = part_id
        line.node_type = 'LINE'
        line.sort_order = 0
        line.text = "%CharName%: Hello."
        line_id = line.node_id

        # Create response under line
        resp = conv.nodes.add()
        resp.node_id = allocate_node_id(conv)
        resp.parent_id = line_id
        resp.node_type = 'RESPONSE'
        resp.sort_order = 0
        resp.response_text = "Player choice"
        resp.conversation_end = True

        self.report({'INFO'}, "Added start condition skeleton")
        return {'FINISHED'}


class MM_OT_add_conv_node(Operator):
    """Add a conversation node as child of the selected node"""
    bl_idname = "mm.add_conv_node"
    bl_label = "Add Node"
    bl_options = {'REGISTER', 'UNDO'}

    node_type: EnumProperty(
        name="Type",
        items=[
            ('LINE', "Line", "NPC dialogue line"),
            ('RESPONSE', "Response", "Player response choice"),
        ],
        default='LINE',
    )

    @classmethod
    def poll(cls, context):
        scene = context.scene
        if not (0 <= scene.mm_conversations_index < len(scene.mm_conversations)):
            return False
        conv = scene.mm_conversations[scene.mm_conversations_index]
        return len(conv.nodes) > 0 and 0 <= conv.nodes_index < len(conv.nodes)

    def execute(self, context):
        from .conversation import (
            allocate_node_id, get_next_sort_order, get_flat_display_order,
        )

        scene = context.scene
        conv = scene.mm_conversations[scene.mm_conversations_index]

        # Get the display-ordered node at the current index
        display = get_flat_display_order(conv)
        if not (0 <= conv.nodes_index < len(display)):
            self.report({'WARNING'}, "No valid node selected")
            return {'CANCELLED'}

        selected_node, _ = display[conv.nodes_index]
        parent_id = selected_node.node_id

        node = conv.nodes.add()
        node.node_id = allocate_node_id(conv)
        node.parent_id = parent_id
        node.node_type = self.node_type
        node.sort_order = get_next_sort_order(conv, parent_id)

        if self.node_type == 'LINE':
            node.text = "%CharName%: "
        elif self.node_type == 'RESPONSE':
            node.response_text = "Player choice"

        self.report({'INFO'}, f"Added {self.node_type} node")
        return {'FINISHED'}


class MM_OT_remove_conv_node(Operator):
    """Remove the selected conversation node and its descendants"""
    bl_idname = "mm.remove_conv_node"
    bl_label = "Remove Node"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        if not (0 <= scene.mm_conversations_index < len(scene.mm_conversations)):
            return False
        conv = scene.mm_conversations[scene.mm_conversations_index]
        return len(conv.nodes) > 0 and 0 <= conv.nodes_index < len(conv.nodes)

    def execute(self, context):
        from .conversation import remove_node_and_descendants, get_flat_display_order

        scene = context.scene
        conv = scene.mm_conversations[scene.mm_conversations_index]

        display = get_flat_display_order(conv)
        if not (0 <= conv.nodes_index < len(display)):
            return {'CANCELLED'}

        selected_node, _ = display[conv.nodes_index]
        remove_node_and_descendants(conv, selected_node.node_id)
        conv.nodes_index = min(conv.nodes_index, max(0, len(get_flat_display_order(conv)) - 1))

        self.report({'INFO'}, "Removed node and descendants")
        return {'FINISHED'}


class MM_OT_add_dialogue_exchange(Operator):
    """Add a line + response pair under the selected node"""
    bl_idname = "mm.add_dialogue_exchange"
    bl_label = "Add Line + Response"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        if not (0 <= scene.mm_conversations_index < len(scene.mm_conversations)):
            return False
        conv = scene.mm_conversations[scene.mm_conversations_index]
        return len(conv.nodes) > 0 and 0 <= conv.nodes_index < len(conv.nodes)

    def execute(self, context):
        from .conversation import allocate_node_id, get_next_sort_order, get_flat_display_order

        scene = context.scene
        conv = scene.mm_conversations[scene.mm_conversations_index]

        display = get_flat_display_order(conv)
        if not (0 <= conv.nodes_index < len(display)):
            return {'CANCELLED'}

        selected_node, _ = display[conv.nodes_index]
        parent_id = selected_node.node_id

        # Line
        line = conv.nodes.add()
        line.node_id = allocate_node_id(conv)
        line.parent_id = parent_id
        line.node_type = 'LINE'
        line.sort_order = get_next_sort_order(conv, parent_id)
        line.text = "%CharName%: "
        line_id = line.node_id

        # Response under line
        resp = conv.nodes.add()
        resp.node_id = allocate_node_id(conv)
        resp.parent_id = line_id
        resp.node_type = 'RESPONSE'
        resp.sort_order = 0
        resp.response_text = "Player choice"

        self.report({'INFO'}, "Added line + response pair")
        return {'FINISHED'}


class MM_OT_export_conversation(Operator):
    """Export selected conversation as XMLB (compiled conversation file)"""
    bl_idname = "mm.export_conversation"
    bl_label = "Export Conversation"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        if not (0 <= scene.mm_conversations_index < len(scene.mm_conversations)):
            return False
        conv = scene.mm_conversations[scene.mm_conversations_index]
        return len(conv.nodes) > 0 and bool(scene.mm_settings.output_dir)

    def execute(self, context):
        from .xml_gen import generate_conversation_text, _write_raven_text
        from .xmlb_compile import compile_xml_to_xmlb

        scene = context.scene
        conv = scene.mm_conversations[scene.mm_conversations_index]
        output_dir = bpy.path.abspath(scene.mm_settings.output_dir)

        if not os.path.isdir(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        # Generate Raven text format
        conv_text = generate_conversation_text(conv)
        xml_path = os.path.join(output_dir, conv.conv_name + ".engb.xml")
        _write_raven_text(conv_text, xml_path)

        # Compile to XMLB
        engb_path = os.path.join(output_dir, conv.conv_name + ".engb")
        compile_xml_to_xmlb(xml_path, engb_path)

        # Auto-add to precache if not already present
        full_conv_path = conv.conv_path
        if full_conv_path:
            full_conv_path = f"{full_conv_path}/{conv.conv_name}"
        else:
            full_conv_path = conv.conv_name
        existing = {e.filename for e in scene.mm_precache}
        if full_conv_path not in existing:
            entry = scene.mm_precache.add()
            entry.filename = full_conv_path
            entry.entry_type = 'conversation'

        self.report({'INFO'}, f"Exported conversation to {engb_path}")
        return {'FINISHED'}


class MM_OT_import_conversation(Operator):
    """Import a conversation from an ENGB file"""
    bl_idname = "mm.import_conversation"
    bl_label = "Import Conversation"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.engb", options={'HIDDEN'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from .xmlb_compile import decompile_xmlb
        from .conversation import xml_to_nodes

        if not os.path.exists(self.filepath):
            self.report({'ERROR'}, f"File not found: {self.filepath}")
            return {'CANCELLED'}

        try:
            root = decompile_xmlb(self.filepath)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to decompile: {e}")
            return {'CANCELLED'}

        scene = context.scene
        conv = scene.mm_conversations.add()
        conv.conv_name = os.path.splitext(os.path.basename(self.filepath))[0]
        scene.mm_conversations_index = len(scene.mm_conversations) - 1

        try:
            xml_to_nodes(conv, root)
            self.report({'INFO'}, f"Imported conversation '{conv.conv_name}' ({len(conv.nodes)} nodes)")
        except Exception as e:
            self.report({'ERROR'}, f"Import failed: {e}")
            return {'CANCELLED'}

        return {'FINISHED'}


def _resolve_character_info(scene, character_name, game_dir=""):
    """Look up a character's info from the scene char DB or game database.

    Checks the already-loaded scene.mm_char_db first (fast). Falls back to
    loading from npcstat/herostat via game_database module if game_dir is set.

    Args:
        scene: bpy.types.Scene
        character_name: character name string (e.g. "Beast", "wolverine")
        game_dir: path to game data directory (optional)

    Returns:
        CharacterInfo or None
    """
    # Check scene char_db first (already loaded by MM_OT_load_char_db)
    for entry in scene.mm_char_db:
        if entry.name_id == character_name:
            # Build a lightweight CharacterInfo-like object
            from .game_database import CharacterInfo
            return CharacterInfo(
                name=entry.name_id,
                charactername=entry.display_name,
                team=entry.team,
                skin=entry.skin,
                characteranims=entry.characteranims,
                source=entry.source,
            )

    # Fall back to loading from game database
    if game_dir and os.path.isdir(game_dir):
        try:
            from .game_database import get_character_db
            char_db = get_character_db(game_dir)
            return char_db.get(character_name)
        except Exception:
            pass

    return None


class MM_OT_link_conversation_to_npc(Operator):
    """Link a conversation to the active entity def via monster_actscript"""
    bl_idname = "mm.link_conversation_to_npc"
    bl_label = "Link to NPC"
    bl_description = ("Set monster_actscript on the active entity def so this "
                      "conversation triggers when the player interacts with the NPC")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return (0 <= scene.mm_conversations_index < len(scene.mm_conversations) and
                0 <= scene.mm_entity_defs_index < len(scene.mm_entity_defs))

    def execute(self, context):
        scene = context.scene
        conv = scene.mm_conversations[scene.mm_conversations_index]
        edef = scene.mm_entity_defs[scene.mm_entity_defs_index]

        # Build the conversation path
        conv_path = conv.conv_path
        if conv_path:
            full_path = f"{conv_path}/{conv.conv_name}"
        else:
            full_path = conv.conv_name

        # Set monster_actscript to the conversation helper script path
        # (NOT inline game.startConversation() — the game needs a proper script file)
        script_value = full_path

        # Check if monster_actscript already exists in custom properties
        found = False
        for prop in edef.properties:
            if prop.key == 'monster_actscript':
                prop.value = script_value
                found = True
                break
        if not found:
            prop = edef.properties.add()
            prop.key = 'monster_actscript'
            prop.value = script_value

        # Also ensure monster_actonuse is set
        has_actonuse = any(p.key == 'monster_actonuse' for p in edef.properties)
        if not has_actonuse:
            prop = edef.properties.add()
            prop.key = 'monster_actonuse'
            prop.value = 'true'

        # Auto-add conversation to precache
        existing_precache = {e.filename for e in scene.mm_precache}
        if full_path not in existing_precache:
            entry = scene.mm_precache.add()
            entry.filename = full_path
            entry.entry_type = 'conversation'

        # Auto-add conversation script to precache
        if script_value not in existing_precache:
            entry = scene.mm_precache.add()
            entry.filename = script_value
            entry.entry_type = 'script'

        # Auto-generate conversation script .py if game_data_dir is set
        settings = scene.mm_settings
        game_dir = bpy.path.abspath(settings.game_data_dir) if settings.game_data_dir else ""
        if game_dir and os.path.isdir(game_dir):
            _ensure_conversation_script(game_dir, full_path)

        # --- Auto-resolve HUD head from NPC's character skin number ---
        # The HUD head model is "hud_head_{skin}" where skin comes from the
        # character database (npcstat/herostat). e.g. wolverine skin=0301
        # → hud_head_0301.  Only auto-set if not already manually set.
        char_info = None
        if edef.character:
            char_info = _resolve_character_info(scene, edef.character, game_dir)

        if char_info and char_info.skin and not conv.hud_head:
            conv.hud_head = f"hud_head_{char_info.skin}"
            self.report({'INFO'},
                        f"Auto-set HUD head: hud_head_{char_info.skin}")

        # --- Auto-replace %CharName% placeholder in dialogue lines ---
        # Real game files use %Beast%:, %ProfX%:, %PLAYER%: etc.
        # Use the charactername from the database (e.g. "Beast", "Professor X")
        if char_info and char_info.charactername:
            char_placeholder = f"%{char_info.charactername}%"
            replaced = 0
            for node in conv.nodes:
                if node.node_type == 'LINE' and "%CharName%" in node.text:
                    node.text = node.text.replace("%CharName%", char_placeholder)
                    replaced += 1
            if replaced:
                self.report({'INFO'},
                            f"Replaced %CharName% → {char_placeholder} "
                            f"in {replaced} line(s)")

        self.report({'INFO'},
                     f"Linked conversation '{conv.conv_name}' to entity '{edef.entity_name}'")
        return {'FINISHED'}


# ===========================================================================
# Dialog Maker — quick conversation creation wizard
# ===========================================================================

class MM_OT_quick_dialog(Operator):
    """Create a complete NPC conversation from a simple dialog script.

    Enter dialog as lines of 'Speaker: Text' (one per line).
    Blank lines separate exchanges. Lines starting with '>' are player responses.
    """
    bl_idname = "mm.quick_dialog"
    bl_label = "Create Dialog"
    bl_description = ("Create a conversation from simple text. Use 'NPC: text' for "
                      "NPC lines, '> text' for player choices. Blank lines separate exchanges")
    bl_options = {'REGISTER', 'UNDO'}

    conv_name: StringProperty(
        name="Name",
        description="Conversation filename",
        default="npc_talk",
    )
    conv_path: StringProperty(
        name="Path",
        description="Conversation path prefix (e.g. 'act1/sanctuary')",
        default="",
    )
    dialog_text: StringProperty(
        name="Dialog",
        description=(
            "Dialog script. Format:\n"
            "  %NPC%: Hello, hero.\n"
            "  > What's going on?\n"
            "  %NPC%: The world is in danger.\n"
            "  > I'll help!\n"
            "  > Maybe later."
        ),
        default="",
    )
    npc_name: StringProperty(
        name="NPC Name",
        description="NPC speaker tag (e.g. 'Beast', 'ProfX'). Used as %NPC% placeholder",
        default="NPC",
    )

    def invoke(self, context, event):
        # Auto-fill path from map settings
        settings = context.scene.mm_settings
        if not self.conv_path and settings.map_path:
            self.conv_path = settings.map_path

        # Auto-generate a unique name
        existing = {c.conv_name for c in context.scene.mm_conversations}
        base = "npc_talk"
        name = base
        idx = 1
        while name in existing:
            name = f"{base}_{idx:02d}"
            idx += 1
        self.conv_name = name

        return context.window_manager.invoke_props_dialog(self, width=400)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "conv_name")
        layout.prop(self, "conv_path")
        layout.prop(self, "npc_name")
        layout.separator()
        layout.label(text="Dialog Script (NPC: text / > player choice):")
        layout.prop(self, "dialog_text", text="")
        layout.separator()
        box = layout.box()
        box.label(text="Format Guide:", icon='INFO')
        col = box.column(align=True)
        col.scale_y = 0.7
        col.label(text="%NPC%: Hello there.")
        col.label(text="> What's happening?")
        col.label(text="%NPC%: We need your help!")
        col.label(text="> I'll help!  |  > Maybe later.")
        col.label(text="(Use | to separate multiple choices)")

    def execute(self, context):
        from .conversation import allocate_node_id, get_next_sort_order

        scene = context.scene
        npc_tag = f"%{self.npc_name}%"

        # Parse dialog text
        lines = self.dialog_text.strip().split("\\n") if "\\n" in self.dialog_text else [self.dialog_text]
        if not lines or not lines[0].strip():
            # If dialog_text is empty, create a minimal template
            lines = [
                f"{npc_tag}: Hello.",
                "> (continue)",
            ]

        # Create conversation
        conv = scene.mm_conversations.add()
        conv.conv_name = self.conv_name
        conv.conv_path = self.conv_path
        scene.mm_conversations_index = len(scene.mm_conversations) - 1

        # Create start condition
        start = conv.nodes.add()
        start.node_id = allocate_node_id(conv)
        start.parent_id = -1
        start.node_type = 'START_CONDITION'
        start.sort_order = 0
        start_id = start.node_id

        # Create default participant
        part = conv.nodes.add()
        part.node_id = allocate_node_id(conv)
        part.parent_id = start_id
        part.node_type = 'PARTICIPANT'
        part.participant_name = "default"
        part.sort_order = 0
        part_id = part.node_id

        # Parse dialog lines
        current_parent = part_id
        line_count = 0
        response_count = 0

        for raw_line in lines:
            raw_line = raw_line.strip()
            if not raw_line:
                continue

            # Handle |-separated choices on one line
            if raw_line.startswith(">") and "|" in raw_line:
                choices = [c.strip().lstrip(">").strip() for c in raw_line.split("|")]
                for choice_text in choices:
                    if choice_text:
                        resp = conv.nodes.add()
                        resp.node_id = allocate_node_id(conv)
                        resp.parent_id = current_parent
                        resp.node_type = 'RESPONSE'
                        resp.sort_order = get_next_sort_order(conv, current_parent)
                        resp.response_text = choice_text
                        response_count += 1
                        # After choices, next NPC line should be under the last choice
                        current_parent = resp.node_id
                continue

            if raw_line.startswith(">"):
                # Player response
                resp_text = raw_line[1:].strip()
                resp = conv.nodes.add()
                resp.node_id = allocate_node_id(conv)
                resp.parent_id = current_parent
                resp.node_type = 'RESPONSE'
                resp.sort_order = get_next_sort_order(conv, current_parent)
                resp.response_text = resp_text if resp_text else ""
                response_count += 1
                # Next NPC line goes under this response
                current_parent = resp.node_id
            else:
                # NPC line (may have "Speaker: text" format)
                line_node = conv.nodes.add()
                line_node.node_id = allocate_node_id(conv)
                line_node.parent_id = current_parent
                line_node.node_type = 'LINE'
                line_node.sort_order = get_next_sort_order(conv, current_parent)
                line_node.text = raw_line
                line_count += 1
                # Next response/line goes under this line
                current_parent = line_node.node_id

        # If we only have NPC lines with no responses, add auto-advance
        if response_count == 0 and line_count > 0:
            resp = conv.nodes.add()
            resp.node_id = allocate_node_id(conv)
            resp.parent_id = current_parent
            resp.node_type = 'RESPONSE'
            resp.sort_order = 0
            resp.response_text = ""  # blank = auto-advance

        self.report({'INFO'},
                    f"Created dialog '{self.conv_name}' "
                    f"({line_count} lines, {response_count} responses)")
        return {'FINISHED'}


class MM_OT_quick_dialog_from_text(Operator):
    """Create a conversation from multiline text in the text editor"""
    bl_idname = "mm.quick_dialog_from_text"
    bl_label = "Dialog from Text Block"
    bl_description = ("Create a conversation from a Blender text block. "
                      "Format: 'Speaker: text' for NPC, '> text' for choices")
    bl_options = {'REGISTER', 'UNDO'}

    text_name: StringProperty(
        name="Text Block",
        description="Name of the Blender text block containing the dialog script",
    )
    conv_name: StringProperty(
        name="Conversation Name",
        default="npc_talk",
    )
    conv_path: StringProperty(
        name="Path",
        default="",
    )

    def invoke(self, context, event):
        settings = context.scene.mm_settings
        if not self.conv_path and settings.map_path:
            self.conv_path = settings.map_path

        # Auto-generate unique name
        existing = {c.conv_name for c in context.scene.mm_conversations}
        base = "npc_talk"
        name = base
        idx = 1
        while name in existing:
            name = f"{base}_{idx:02d}"
            idx += 1
        self.conv_name = name

        return context.window_manager.invoke_props_dialog(self, width=350)

    def draw(self, context):
        layout = self.layout
        layout.prop_search(self, "text_name", bpy.data, "texts")
        layout.prop(self, "conv_name")
        layout.prop(self, "conv_path")

    def execute(self, context):
        text_block = bpy.data.texts.get(self.text_name)
        if not text_block:
            self.report({'ERROR'}, f"Text block '{self.text_name}' not found")
            return {'CANCELLED'}

        # Read text and delegate to quick_dialog logic
        from .conversation import allocate_node_id, get_next_sort_order

        scene = context.scene
        raw_text = text_block.as_string()
        lines = raw_text.split("\n")

        # Create conversation
        conv = scene.mm_conversations.add()
        conv.conv_name = self.conv_name
        conv.conv_path = self.conv_path
        scene.mm_conversations_index = len(scene.mm_conversations) - 1

        # Create start condition
        start = conv.nodes.add()
        start.node_id = allocate_node_id(conv)
        start.parent_id = -1
        start.node_type = 'START_CONDITION'
        start.sort_order = 0
        start_id = start.node_id

        # Create default participant
        part = conv.nodes.add()
        part.node_id = allocate_node_id(conv)
        part.parent_id = start_id
        part.node_type = 'PARTICIPANT'
        part.participant_name = "default"
        part.sort_order = 0
        part_id = part.node_id

        # Parse lines
        current_parent = part_id
        line_count = 0
        response_count = 0

        for raw_line in lines:
            raw_line = raw_line.strip()
            if not raw_line or raw_line.startswith("#"):
                continue

            # Handle |-separated choices
            if raw_line.startswith(">") and "|" in raw_line:
                choices = [c.strip().lstrip(">").strip() for c in raw_line.split("|")]
                for choice_text in choices:
                    if choice_text:
                        resp = conv.nodes.add()
                        resp.node_id = allocate_node_id(conv)
                        resp.parent_id = current_parent
                        resp.node_type = 'RESPONSE'
                        resp.sort_order = get_next_sort_order(conv, current_parent)
                        resp.response_text = choice_text
                        response_count += 1
                        current_parent = resp.node_id
                continue

            if raw_line.startswith(">"):
                resp_text = raw_line[1:].strip()
                resp = conv.nodes.add()
                resp.node_id = allocate_node_id(conv)
                resp.parent_id = current_parent
                resp.node_type = 'RESPONSE'
                resp.sort_order = get_next_sort_order(conv, current_parent)
                resp.response_text = resp_text if resp_text else ""
                response_count += 1
                current_parent = resp.node_id
            else:
                line_node = conv.nodes.add()
                line_node.node_id = allocate_node_id(conv)
                line_node.parent_id = current_parent
                line_node.node_type = 'LINE'
                line_node.sort_order = get_next_sort_order(conv, current_parent)
                line_node.text = raw_line
                line_count += 1
                current_parent = line_node.node_id

        if response_count == 0 and line_count > 0:
            resp = conv.nodes.add()
            resp.node_id = allocate_node_id(conv)
            resp.parent_id = current_parent
            resp.node_type = 'RESPONSE'
            resp.sort_order = 0
            resp.response_text = ""

        self.report({'INFO'},
                    f"Created dialog '{self.conv_name}' from text block "
                    f"({line_count} lines, {response_count} responses)")
        return {'FINISHED'}


# ===========================================================================
# Conversation Editor (PySide6 external window)
# ===========================================================================

def _get_deps_dir():
    """Get the deps directory path (the bundled deps/ next to this file)."""
    from pathlib import Path
    return str(Path(__file__).parent / "deps")


def _ensure_pyside6_path():
    """Add the bundled deps directory to sys.path for PySide6 imports."""
    import sys
    deps_dir = _get_deps_dir()
    if deps_dir not in sys.path:
        sys.path.insert(0, deps_dir)


def _has_pyside6():
    """Check if PySide6 is actually importable (not just a stale directory)."""
    _ensure_pyside6_path()
    try:
        from PySide6.QtWidgets import QApplication  # noqa
        return True
    except ImportError:
        return False


class MM_OT_open_convo_editor(Operator):
    """Open the external conversation editor window (PySide6)"""
    bl_idname = "mm.open_convo_editor"
    bl_label = "Edit Conversation"
    bl_description = ("Open the conversation in an external editor window with "
                      "game-style dialogue preview and HUD character portraits")

    _app = None
    _window = None
    _timer = None

    @classmethod
    def poll(cls, context):
        scene = context.scene
        if cls._window is not None:
            return False  # Already open
        if not (0 <= scene.mm_conversations_index < len(scene.mm_conversations)):
            return False
        return _has_pyside6()

    def execute(self, context):
        _ensure_pyside6_path()
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            self.report({'ERROR'},
                        "PySide6 not installed. Use 'Install PySide6' in IGB Extras panel")
            return {'CANCELLED'}

        from .convo_editor import ConversationEditorWindow
        from .hud_extract import get_hud_cache_dir

        scene = context.scene
        conv_index = scene.mm_conversations_index

        # Create QApplication if needed
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        MM_OT_open_convo_editor._app = app

        # Create editor window
        cache_dir = str(get_hud_cache_dir())
        window = ConversationEditorWindow(scene, conv_index, cache_dir)
        window.show()
        MM_OT_open_convo_editor._window = window

        # Register modal timer to pump Qt events
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)

        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'TIMER':
            app = MM_OT_open_convo_editor._app
            if app is not None:
                app.processEvents()

            # Check if window was closed
            window = MM_OT_open_convo_editor._window
            if window is None or not window.isVisible():
                self._cleanup(context)
                return {'FINISHED'}

        return {'PASS_THROUGH'}

    def cancel(self, context):
        self._cleanup(context)

    def _cleanup(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        window = MM_OT_open_convo_editor._window
        if window is not None:
            window.close()
        MM_OT_open_convo_editor._window = None


class MM_OT_open_script_editor(Operator):
    """Open the external script editor with command palette (PySide6)"""
    bl_idname = "mm.open_script_editor"
    bl_label = "Edit Script"
    bl_description = ("Open a script editor with game command palette for "
                      "building entity activation/spawn scripts")

    script_key: StringProperty(
        name="Script Key",
        description="Entity property key (actscript, spawnscript, monster_actscript)",
        default='actscript',
    )

    _app = None
    _window = None
    _timer = None

    @classmethod
    def poll(cls, context):
        if cls._window is not None:
            return False  # Already open
        scene = context.scene
        if not (0 <= scene.mm_entity_defs_index < len(scene.mm_entity_defs)):
            return False
        return _has_pyside6()

    def execute(self, context):
        _ensure_pyside6_path()
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            self.report({'ERROR'},
                        "PySide6 not installed. Use 'Install PySide6' in IGB Extras panel")
            return {'CANCELLED'}

        from .script_editor import ScriptEditorWindow

        scene = context.scene
        settings = scene.mm_settings
        edef = scene.mm_entity_defs[scene.mm_entity_defs_index]

        # Resolve game data dir — use blend file directory as fallback
        if settings.game_data_dir:
            game_data_dir = bpy.path.abspath(settings.game_data_dir)
        else:
            # Fall back to blend file dir, or temp dir
            blend_dir = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else ''
            if blend_dir:
                game_data_dir = blend_dir
            else:
                import tempfile
                game_data_dir = os.path.join(tempfile.gettempdir(), 'mapmaker_scripts')
            self.report({'INFO'},
                        f"No Game Data Dir set — saving scripts to: {game_data_dir}")

        # Find the script path from entity properties
        script_path = ''
        for prop in edef.properties:
            if prop.key == self.script_key:
                script_path = prop.value
                break

        # Check for inline Lua (contains parentheses) — not a file path
        if script_path and '(' in script_path:
            self.report({'WARNING'},
                        f"'{script_path}' is an inline Lua call, not a file path. "
                        "Clear it first to create a file-based script.")
            return {'CANCELLED'}

        # Auto-generate script path if empty
        if not script_path:
            map_path = settings.map_path or 'custom'
            safe_name = edef.entity_name.replace(' ', '_').lower()
            script_path = f"{map_path}/{safe_name}"

            # Set the property on the entity def
            found = False
            for prop in edef.properties:
                if prop.key == self.script_key:
                    prop.value = script_path
                    found = True
                    break
            if not found:
                new_prop = edef.properties.add()
                new_prop.key = self.script_key
                new_prop.value = script_path

        # Detect game type from game directory
        game = self._detect_game(game_data_dir)

        # Create QApplication if needed
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        MM_OT_open_script_editor._app = app

        # Create editor window
        window = ScriptEditorWindow(
            entity_name=edef.entity_name,
            script_key=self.script_key,
            script_path=script_path,
            game_data_dir=game_data_dir,
            game=game,
        )

        # Connect saved signal to update entity property
        def _on_saved(path):
            for prop in edef.properties:
                if prop.key == self.script_key:
                    prop.value = path
                    break

        window.saved.connect(_on_saved)
        window.show()
        MM_OT_open_script_editor._window = window

        # Register modal timer to pump Qt events
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)

        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'TIMER':
            app = MM_OT_open_script_editor._app
            if app is not None:
                app.processEvents()

            # Check if window was closed
            window = MM_OT_open_script_editor._window
            if window is None or not window.isVisible():
                self._cleanup(context)
                return {'FINISHED'}

        return {'PASS_THROUGH'}

    def cancel(self, context):
        self._cleanup(context)

    def _cleanup(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        window = MM_OT_open_script_editor._window
        if window is not None:
            window.close()
        MM_OT_open_script_editor._window = None

    @staticmethod
    def _detect_game(game_data_dir):
        """Detect XML2 vs MUA from game directory contents."""
        parent = os.path.dirname(game_data_dir.rstrip('/\\'))
        if os.path.exists(os.path.join(parent, 'Game.exe')):
            return 'mua'
        if os.path.exists(os.path.join(parent, 'XMen2.exe')):
            return 'xml2'
        # Fallback: check for MUA-specific paths
        if os.path.exists(os.path.join(game_data_dir, 'ui', 'models')):
            return 'mua'
        return 'xml2'


class MM_OT_extract_hud_heads(Operator):
    """Extract HUD head portraits from game IGB files to PNG cache"""
    bl_idname = "mm.extract_hud_heads"
    bl_label = "Extract HUD Heads"
    bl_description = ("Extract character portrait textures from hud_head_*.igb files "
                      "to PNG for use in the conversation editor")

    @classmethod
    def poll(cls, context):
        settings = context.scene.mm_settings
        game_dir = bpy.path.abspath(settings.game_data_dir) if settings.game_data_dir else ""
        if not game_dir or not os.path.isdir(game_dir):
            return False
        hud_dir = os.path.join(game_dir, "hud")
        return os.path.isdir(hud_dir)

    def execute(self, context):
        from .hud_extract import extract_all_hud_heads, get_hud_cache_dir

        settings = context.scene.mm_settings
        game_dir = bpy.path.abspath(settings.game_data_dir)
        hud_dir = os.path.join(game_dir, "hud")
        cache_dir = str(get_hud_cache_dir())

        results, extracted, skipped = extract_all_hud_heads(hud_dir, cache_dir)
        total = len(results)

        self.report({'INFO'},
                    f"HUD heads: {extracted} extracted, {skipped} cached, {total} total")
        return {'FINISHED'}


class MM_OT_install_pyside6(Operator):
    """Install PySide6 into the addon's deps folder for the conversation editor"""
    bl_idname = "mm.install_pyside6"
    bl_label = "Install PySide6"
    bl_description = ("Install PySide6-Essentials GUI framework into the addon's deps "
                      "folder. Restart Blender after installation")

    @classmethod
    def poll(cls, context):
        return not _has_pyside6()

    def execute(self, context):
        import subprocess
        import sys

        deps_dir = _get_deps_dir()
        self.report({'INFO'}, "Installing PySide6-Essentials... this may take a minute")

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install",
                 "PySide6-Essentials", "--target", deps_dir, "--no-cache-dir"],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode == 0:
                self.report({'INFO'},
                            "PySide6 installed successfully. Please restart Blender.")
            else:
                self.report({'ERROR'},
                            f"PySide6 install failed: {result.stderr[:200]}")
        except subprocess.TimeoutExpired:
            self.report({'ERROR'}, "PySide6 install timed out after 5 minutes")
        except Exception as e:
            self.report({'ERROR'}, f"PySide6 install error: {e}")

        return {'FINISHED'}


class MM_OT_open_menu_editor(Operator):
    """Open the external menu editor window (PySide6)"""
    bl_idname = "mm.open_menu_editor"
    bl_label = "Open Menu Editor"
    bl_description = ("Open the menu editor window to edit game menu ENGB files "
                      "with visual preview and IGB texture extraction")

    _app = None
    _window = None
    _timer = None

    @classmethod
    def poll(cls, context):
        if cls._window is not None:
            return False  # Already open
        return _has_pyside6()

    def execute(self, context):
        _ensure_pyside6_path()
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            self.report({'ERROR'},
                        "PySide6 not installed. Use 'Install PySide6' in IGB Extras panel")
            return {'CANCELLED'}

        from .menu_editor import MenuEditorWindow

        # Get game directory for IGB path resolution
        settings = context.scene.mm_settings
        game_dir = ''
        if settings.game_data_dir:
            game_dir = bpy.path.abspath(settings.game_data_dir)

        # Create QApplication if needed
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        MM_OT_open_menu_editor._app = app

        # Create editor window
        window = MenuEditorWindow(game_dir=game_dir)
        window.show()
        MM_OT_open_menu_editor._window = window

        # Register modal timer to pump Qt events
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)

        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'TIMER':
            app = MM_OT_open_menu_editor._app
            if app is not None:
                app.processEvents()

            # Check if window was closed
            window = MM_OT_open_menu_editor._window
            if window is None or not window.isVisible():
                self._cleanup(context)
                return {'FINISHED'}

        return {'PASS_THROUGH'}

    def cancel(self, context):
        self._cleanup(context)

    def _cleanup(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        window = MM_OT_open_menu_editor._window
        if window is not None:
            window.close()
        MM_OT_open_menu_editor._window = None


class MM_OT_open_npcstat_editor(Operator):
    """Open the external NPC Stat Editor window (PySide6)"""
    bl_idname = "mm.open_npcstat_editor"
    bl_label = "Open NPC Stat Editor"
    bl_description = ("Open the NPC stat editor to browse, edit, add, and remove "
                      "NPCs from npcstat.engb or herostat.engb with a visual interface")

    stat_file: EnumProperty(
        name="Stat File",
        description="Which stat file to edit",
        items=[
            ('npcstat', "npcstat.engb", "NPC stats (enemies, NPCs, allies)"),
            ('herostat', "herostat.engb", "Hero stats (playable characters)"),
        ],
        default='npcstat',
    )

    _app = None
    _window = None
    _timer = None

    @classmethod
    def poll(cls, context):
        if cls._window is not None:
            return False  # Already open
        if not _has_pyside6():
            return False
        # Need game_data_dir to find stat files
        settings = context.scene.mm_settings
        return bool(settings.game_data_dir)

    def execute(self, context):
        _ensure_pyside6_path()
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            self.report({'ERROR'},
                        "PySide6 not installed. Use 'Install PySide6' in IGB Extras panel")
            return {'CANCELLED'}

        from .npcstat_editor import NPCStatEditorWindow

        settings = context.scene.mm_settings
        game_dir = bpy.path.abspath(settings.game_data_dir)
        filename = f"{self.stat_file}.engb"

        # Find the stat file in data/ subfolder or root
        stat_path = os.path.join(game_dir, "data", filename)
        if not os.path.isfile(stat_path):
            stat_path = os.path.join(game_dir, filename)
        if not os.path.isfile(stat_path):
            self.report({'ERROR'},
                        f"{filename} not found. Checked:\n"
                        f"  {os.path.join(game_dir, 'data', filename)}\n"
                        f"  {os.path.join(game_dir, filename)}")
            return {'CANCELLED'}

        # Create QApplication if needed
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        MM_OT_open_npcstat_editor._app = app

        # Create editor window
        try:
            window = NPCStatEditorWindow(stat_path)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to open NPC Stat Editor: {e}")
            return {'CANCELLED'}

        window.show()
        MM_OT_open_npcstat_editor._window = window

        # Register modal timer to pump Qt events
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)

        self.report({'INFO'}, f"Stat Editor opened: {stat_path}")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'TIMER':
            app = MM_OT_open_npcstat_editor._app
            if app is not None:
                app.processEvents()

            # Check if window was closed
            window = MM_OT_open_npcstat_editor._window
            if window is None or not window.isVisible():
                self._cleanup(context)
                return {'FINISHED'}

        return {'PASS_THROUGH'}

    def cancel(self, context):
        self._cleanup(context)

    def _cleanup(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        window = MM_OT_open_npcstat_editor._window
        if window is not None:
            window.close()
        MM_OT_open_npcstat_editor._window = None


# ===========================================================================
# Collision visualization
# ===========================================================================

class MM_OT_visualize_colliders(Operator):
    """Preview the generated collision mesh (like navmesh visualization)"""
    bl_idname = "mm.visualize_colliders"
    bl_label = "Visualize Colliders"
    bl_description = ("Generate collision hull from the current Colliders collection "
                      "(or visual mesh) and show it as a wireframe preview object. "
                      "This shows EXACTLY what the game will use for collision.")
    bl_options = {'REGISTER', 'UNDO'}

    source: EnumProperty(
        name="Source",
        items=[
            ('COLLIDERS', "Colliders Collection",
             "Build from objects in the 'Colliders' collection"),
            ('VISUAL', "Visual Mesh",
             "Build from all visible mesh objects (excludes Colliders & [MapMaker])"),
        ],
        default='COLLIDERS',
    )

    def execute(self, context):
        from ..exporter.collide_hull import extract_collision_triangles

        # Determine source objects
        if self.source == 'COLLIDERS':
            coll = bpy.data.collections.get("Colliders")
            if coll is None or not coll.objects:
                self.report({'WARNING'},
                            "No 'Colliders' collection found. "
                            "Create one with collision geometry or switch to Visual Mesh.")
                return {'CANCELLED'}
            source_objects = [o for o in coll.objects if o.type == 'MESH']
        else:
            # Gather visual mesh objects (same logic as export_igb.py)
            excluded = set()
            colliders_coll = bpy.data.collections.get("Colliders")
            if colliders_coll:
                for o in colliders_coll.objects:
                    excluded.add(o)
            for c in bpy.data.collections:
                if c.name.startswith("[MapMaker]"):
                    for o in c.objects:
                        excluded.add(o)
            source_objects = [
                o for o in context.scene.objects
                if o.type == 'MESH'
                and o not in excluded
                and not o.hide_viewport
            ]

        if not source_objects:
            self.report({'WARNING'}, "No mesh objects found for collision preview")
            return {'CANCELLED'}

        # Extract collision triangles (with bmesh cleaning + degenerate filtering)
        triangles = extract_collision_triangles(source_objects)

        if not triangles:
            self.report({'WARNING'}, "No valid collision triangles extracted")
            return {'CANCELLED'}

        # Build Blender preview mesh from collision triangles
        verts = []
        faces = []
        for tri in triangles:
            v0, v1, v2 = tri['verts']
            base = len(verts)
            verts.extend([v0, v1, v2])
            faces.append((base, base + 1, base + 2))

        mesh_name = "Collision_Preview"
        mesh = bpy.data.meshes.new(mesh_name)
        mesh.from_pydata(verts, [], faces)
        mesh.update()

        # Remove old preview if exists
        old = bpy.data.objects.get(mesh_name)
        if old:
            bpy.data.objects.remove(old, do_unlink=True)

        obj = bpy.data.objects.new(mesh_name, mesh)
        obj.display_type = 'WIRE'

        # Semi-transparent green material for collision preview
        mat_name = "Collision_Preview_Mat"
        mat = bpy.data.materials.get(mat_name)
        if mat is None:
            mat = bpy.data.materials.new(mat_name)
            mat.diffuse_color = (0.0, 0.8, 0.2, 0.3)
            mat.use_nodes = False
        mesh.materials.append(mat)

        # Link to scene
        if context.scene.collection.children:
            context.scene.collection.children[0].objects.link(obj)
        else:
            context.scene.collection.objects.link(obj)

        self.report({'INFO'},
                    f"Collision preview: {len(triangles)} triangles "
                    f"from {len(source_objects)} object(s)")
        return {'FINISHED'}


# ===========================================================================
# ZAM Automap operators
# ===========================================================================

class MM_OT_import_zam(Operator):
    """Import a .zam minimap file as a Blender mesh with vertex colors"""
    bl_idname = "mm.import_zam"
    bl_label = "Import ZAM Minimap"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.zam", options={'HIDDEN'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from .zam_io import parse_zam, create_mesh_from_zam

        zam_data = parse_zam(self.filepath)
        name = os.path.splitext(os.path.basename(self.filepath))[0]
        obj, tri_count = create_mesh_from_zam(name, zam_data, scale=0.01)

        # Put in an Automap collection
        coll_name = "[MapMaker] Automap"
        if coll_name not in bpy.data.collections:
            coll = bpy.data.collections.new(coll_name)
            context.scene.collection.children.link(coll)
        coll = bpy.data.collections[coll_name]
        coll.objects.link(obj)

        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        context.view_layer.objects.active = obj

        self.report({'INFO'},
                    f"Imported {tri_count} triangles from "
                    f"{len(zam_data['polygons'])} polygons ({name}.zam)")
        return {'FINISHED'}


class MM_OT_export_zam(Operator):
    """Export a mesh to .zam minimap format (indexed triangles with spatial grid)"""
    bl_idname = "mm.export_zam"
    bl_label = "Export ZAM Minimap"
    bl_options = {'REGISTER'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.zam", options={'HIDDEN'})

    game_format: EnumProperty(
        name="Game Format",
        description="Target game format version",
        items=[
            ('XML2', "X-Men Legends II (v9)", "8 bytes/vertex with full RGBA"),
            ('MUA', "Marvel Ultimate Alliance (v10)", "6 bytes/vertex, alpha only"),
        ],
        default='XML2',
    )

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'MESH'

    def invoke(self, context, event):
        # Default filename from map name
        settings = context.scene.mm_settings
        if settings.map_name:
            self.filepath = settings.map_name + ".zam"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from .zam_io import write_zam

        obj = context.active_object
        version = 10 if self.game_format == 'MUA' else 9
        count = write_zam(self.filepath, obj, scale=100.0, version=version)
        ver_str = "v9/XML2" if version == 9 else "v10/MUA"
        self.report({'INFO'},
                    f"Exported {count} polygons ({ver_str}) to {os.path.basename(self.filepath)}")
        return {'FINISHED'}


class MM_OT_make_automap(Operator):
    """Generate an automap mesh from selected floor geometry.

    Projects selected mesh faces onto the XY plane as triangles with white
    vertex colors. The result can be exported as a .zam file."""
    bl_idname = "mm.make_automap"
    bl_label = "Generate Automap Mesh"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'MESH'

    def execute(self, context):
        import bmesh
        from mathutils import Vector

        src_obj = context.active_object
        src_mesh = src_obj.data

        # Create a flattened copy — project all vertices to Z=0
        bm = bmesh.new()
        bm.from_mesh(src_mesh)

        # Apply object transform
        bm.transform(src_obj.matrix_world)

        # Flatten to Z=0
        for v in bm.verts:
            v.co.z = 0.0

        # Remove duplicate verts that collapse when flattened
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.01)

        # Triangulate for ZAM triangle strip format
        bmesh.ops.triangulate(bm, faces=bm.faces)

        # Add white vertex colors
        color_layer = bm.loops.layers.color.new("Color")
        for face in bm.faces:
            for loop in face.loops:
                loop[color_layer] = (1.0, 1.0, 1.0, 0.8)  # White, slightly transparent

        # Create new mesh
        name = f"{src_obj.name}_automap"
        mesh = bpy.data.meshes.new(name)
        bm.to_mesh(mesh)
        bm.free()

        obj = bpy.data.objects.new(name, mesh)

        # Put in Automap collection
        coll_name = "[MapMaker] Automap"
        if coll_name not in bpy.data.collections:
            coll = bpy.data.collections.new(coll_name)
            context.scene.collection.children.link(coll)
        coll = bpy.data.collections[coll_name]
        coll.objects.link(obj)

        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        context.view_layer.objects.active = obj

        tri_count = len(mesh.polygons)
        self.report({'INFO'},
                    f"Generated automap: {tri_count} triangles")
        return {'FINISHED'}


# ===========================================================================
# Registration
# ===========================================================================

# ===========================================================================
# Schema-Aware Property Editing
# ===========================================================================

class MM_OT_set_entity_property(Operator):
    """Set or create a key-value property on an entity definition"""
    bl_idname = "mm.set_entity_property"
    bl_label = "Set Property"
    bl_options = {'REGISTER', 'UNDO'}

    entity_name: StringProperty()
    property_key: StringProperty()
    property_value: StringProperty()

    def execute(self, context):
        scene = context.scene
        edef = _find_entity_def_by_name(scene, self.entity_name)
        if not edef:
            # Fall back to active entity def
            if 0 <= scene.mm_entity_defs_index < len(scene.mm_entity_defs):
                edef = scene.mm_entity_defs[scene.mm_entity_defs_index]
            else:
                self.report({'WARNING'}, "No entity def found")
                return {'CANCELLED'}

        # Find existing property or create new one
        for prop in edef.properties:
            if prop.key == self.property_key:
                prop.value = self.property_value
                return {'FINISHED'}

        # Create new property
        prop = edef.properties.add()
        prop.key = self.property_key
        prop.value = self.property_value
        return {'FINISHED'}


class MM_OT_remove_entity_property_by_index(Operator):
    """Remove a custom property by index"""
    bl_idname = "mm.remove_entity_property_by_index"
    bl_label = "Remove Property"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty()

    def execute(self, context):
        scene = context.scene
        if not (0 <= scene.mm_entity_defs_index < len(scene.mm_entity_defs)):
            return {'CANCELLED'}
        edef = scene.mm_entity_defs[scene.mm_entity_defs_index]
        if 0 <= self.index < len(edef.properties):
            edef.properties.remove(self.index)
        return {'FINISHED'}


class MM_OT_remove_entity_property_by_key(Operator):
    """Remove a named property from an entity definition"""
    bl_idname = "mm.remove_entity_property_by_key"
    bl_label = "Remove Property by Key"
    bl_options = {'REGISTER', 'UNDO'}

    entity_name: StringProperty()
    property_key: StringProperty()

    def execute(self, context):
        scene = context.scene
        edef = _find_entity_def_by_name(scene, self.entity_name)
        if not edef:
            if 0 <= scene.mm_entity_defs_index < len(scene.mm_entity_defs):
                edef = scene.mm_entity_defs[scene.mm_entity_defs_index]
            else:
                return {'CANCELLED'}

        for i, prop in enumerate(edef.properties):
            if prop.key == self.property_key:
                edef.properties.remove(i)
                return {'FINISHED'}
        return {'FINISHED'}  # Key not found = already removed


# ===========================================================================
# Validation
# ===========================================================================

class MM_OT_run_validation(Operator):
    """Run pre-build validation checks"""
    bl_idname = "mm.run_validation"
    bl_label = "Run Validation"

    def execute(self, context):
        import json
        from .entity_validate import validate_all

        scene = context.scene
        results = validate_all(scene)

        # Cache results on the scene for the panel to display
        scene["mm_validation_results"] = json.dumps(results)

        errors = sum(1 for level, _ in results if level == 'ERROR')
        warnings = sum(1 for level, _ in results if level == 'WARNING')
        infos = sum(1 for level, _ in results if level == 'INFO')

        if errors:
            self.report({'ERROR'}, f"Validation: {errors} errors, {warnings} warnings")
        elif warnings:
            self.report({'WARNING'}, f"Validation: {warnings} warnings, {infos} info")
        else:
            self.report({'INFO'}, f"Validation passed ({infos} info)")

        return {'FINISHED'}


# ===========================================================================
# Lights panel operators
# ===========================================================================

# Collection name for scene lights (NOT [MapMaker] prefixed — lights must
# be in a regular collection so they are included in IGB export)
LIGHTS_COLLECTION_NAME = "Lights"


def _get_scene_lights(context):
    """Get all non-preview LIGHT objects in the scene, sorted by name."""
    lights = []
    for obj in context.scene.objects:
        if obj.type == 'LIGHT' and not obj.get("mm_preview"):
            lights.append(obj)
    lights.sort(key=lambda o: o.name)
    return lights


def _get_or_create_lights_collection():
    """Get or create the Lights collection."""
    col = bpy.data.collections.get(LIGHTS_COLLECTION_NAME)
    if col is None:
        col = bpy.data.collections.new(LIGHTS_COLLECTION_NAME)
        bpy.context.scene.collection.children.link(col)
    return col


class MM_OT_add_light(Operator):
    """Add a new light to the scene"""
    bl_idname = "mm.add_light"
    bl_label = "Add Light"
    bl_options = {'REGISTER', 'UNDO'}

    light_type: EnumProperty(
        name="Type",
        items=[
            ('POINT', "Point", "Omnidirectional light", 'LIGHT_POINT', 0),
            ('SUN', "Sun", "Directional light (infinite distance)", 'LIGHT_SUN', 1),
            ('SPOT', "Spot", "Cone-shaped directional light", 'LIGHT_SPOT', 2),
        ],
        default='POINT',
    )

    def execute(self, context):
        col = _get_or_create_lights_collection()
        light_data = bpy.data.lights.new(name="Light", type=self.light_type)
        light_data.color = (1.0, 1.0, 1.0)
        light_data.energy = 10.0
        light_data.use_shadow = True

        light_obj = bpy.data.objects.new("Light", light_data)
        light_obj.location = context.scene.cursor.location
        col.objects.link(light_obj)

        # Select it
        bpy.ops.object.select_all(action='DESELECT')
        light_obj.select_set(True)
        context.view_layer.objects.active = light_obj

        # Update active index
        lights = _get_scene_lights(context)
        for i, obj in enumerate(lights):
            if obj == light_obj:
                context.scene.mm_settings.active_light_index = i
                break

        self.report({'INFO'}, f"Added {self.light_type} light")
        return {'FINISHED'}


class MM_OT_remove_light(Operator):
    """Remove the selected light from the scene"""
    bl_idname = "mm.remove_light"
    bl_label = "Remove Light"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        lights = _get_scene_lights(context)
        idx = context.scene.mm_settings.active_light_index
        return 0 <= idx < len(lights)

    def execute(self, context):
        lights = _get_scene_lights(context)
        idx = context.scene.mm_settings.active_light_index
        if 0 <= idx < len(lights):
            obj = lights[idx]
            name = obj.name
            light_data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if light_data and light_data.users == 0:
                bpy.data.lights.remove(light_data)
            # Clamp index
            new_count = len(_get_scene_lights(context))
            if idx >= new_count:
                context.scene.mm_settings.active_light_index = max(0, new_count - 1)
            self.report({'INFO'}, f"Removed light '{name}'")
        return {'FINISHED'}


class MM_OT_select_light(Operator):
    """Select the active light in the viewport"""
    bl_idname = "mm.select_light"
    bl_label = "Select Light"

    @classmethod
    def poll(cls, context):
        lights = _get_scene_lights(context)
        idx = context.scene.mm_settings.active_light_index
        return 0 <= idx < len(lights)

    def execute(self, context):
        lights = _get_scene_lights(context)
        idx = context.scene.mm_settings.active_light_index
        if 0 <= idx < len(lights):
            bpy.ops.object.select_all(action='DESELECT')
            obj = lights[idx]
            obj.select_set(True)
            context.view_layer.objects.active = obj
        return {'FINISHED'}


class MM_OT_select_light_by_index(Operator):
    """Select a light by its index in the lights list"""
    bl_idname = "mm.select_light_by_index"
    bl_label = "Select Light"

    index: IntProperty()

    def execute(self, context):
        context.scene.mm_settings.active_light_index = self.index
        lights = _get_scene_lights(context)
        if 0 <= self.index < len(lights):
            bpy.ops.object.select_all(action='DESELECT')
            obj = lights[self.index]
            obj.select_set(True)
            context.view_layer.objects.active = obj
        return {'FINISHED'}


class MM_OT_add_light_preset(Operator):
    """Add a light with preset color and energy"""
    bl_idname = "mm.add_light_preset"
    bl_label = "Add Light Preset"
    bl_options = {'REGISTER', 'UNDO'}

    preset: EnumProperty(
        name="Preset",
        items=[
            ('WARM', "Warm", "Warm orange/yellow light"),
            ('COOL', "Cool", "Cool blue tech light"),
            ('RED', "Red", "Red danger/lava light"),
            ('GREEN', "Green", "Green toxic/magic light"),
        ],
    )

    def execute(self, context):
        from .entity_defs import LIGHT_PRESETS

        preset_data = LIGHT_PRESETS.get(self.preset.lower())
        if preset_data is None:
            self.report({'WARNING'}, f"Unknown preset: {self.preset}")
            return {'CANCELLED'}

        col = _get_or_create_lights_collection()
        name = f"Light_{self.preset.capitalize()}"
        light_data = bpy.data.lights.new(name=name, type='POINT')
        light_data.color = preset_data['color']
        light_data.energy = preset_data['energy']
        light_data.use_shadow = True

        light_obj = bpy.data.objects.new(name, light_data)
        light_obj.location = context.scene.cursor.location
        col.objects.link(light_obj)

        # Select it
        bpy.ops.object.select_all(action='DESELECT')
        light_obj.select_set(True)
        context.view_layer.objects.active = light_obj

        # Update index
        lights = _get_scene_lights(context)
        for i, obj in enumerate(lights):
            if obj == light_obj:
                context.scene.mm_settings.active_light_index = i
                break

        self.report({'INFO'}, f"Added {self.preset} preset light")
        return {'FINISHED'}


_classes = (
    # Entity def CRUD
    MM_OT_add_entity_def,
    MM_OT_remove_entity_def,
    MM_OT_add_entity_property,
    MM_OT_remove_entity_property,
    MM_OT_apply_defaults,
    MM_OT_place_preset,
    # Entity instance placement
    MM_OT_place_entity,
    MM_OT_remove_entity_instance,
    MM_OT_select_by_type,
    # Actor preview
    MM_OT_refresh_previews,
    MM_OT_strip_previews,
    # Precache
    MM_OT_add_precache,
    MM_OT_remove_precache,
    MM_OT_scan_precache,
    # Characters
    MM_OT_add_character,
    MM_OT_remove_character,
    MM_OT_scan_characters,
    MM_OT_rename_character,
    MM_OT_replace_character,
    # Character DB
    MM_OT_load_char_db,
    MM_OT_pick_character,
    MM_OT_quick_place_character,
    # Model DB
    MM_OT_scan_models,
    MM_OT_pick_model,
    MM_OT_quick_place_model,
    MM_OT_import_model_to_asset_lib,
    MM_OT_import_category_to_asset_lib,
    MM_OT_detect_placed_assets,
    # NavMesh
    MM_OT_generate_navmesh,
    MM_OT_visualize_navmesh,
    MM_OT_clear_navmesh,
    # Build
    MM_OT_generate_xml,
    MM_OT_compile_xmlb,
    MM_OT_build_all,
    # Import
    MM_OT_import_eng,
    MM_OT_import_full_map,
    MM_OT_import_motionpath,
    MM_OT_export_motionpath,
    # Simulate / Game look
    MM_OT_simulate_play,
    MM_OT_simulate_stop,
    MM_OT_game_shading,
    MM_OT_autodetect_game_dir,
    # Conversations
    MM_OT_add_conversation,
    MM_OT_remove_conversation,
    MM_OT_add_start_condition,
    MM_OT_add_conv_node,
    MM_OT_remove_conv_node,
    MM_OT_add_dialogue_exchange,
    MM_OT_export_conversation,
    MM_OT_import_conversation,
    MM_OT_link_conversation_to_npc,
    # Dialog Maker
    MM_OT_quick_dialog,
    MM_OT_quick_dialog_from_text,
    # Conversation Editor
    MM_OT_open_convo_editor,
    MM_OT_extract_hud_heads,
    MM_OT_install_pyside6,
    # Script Editor
    MM_OT_open_script_editor,
    # Menu Editor
    MM_OT_open_menu_editor,
    # NPC Stat Editor
    MM_OT_open_npcstat_editor,
    # Collision
    MM_OT_visualize_colliders,
    # ZAM Automap
    MM_OT_import_zam,
    MM_OT_export_zam,
    MM_OT_make_automap,
    # Schema-aware property editing
    MM_OT_set_entity_property,
    MM_OT_remove_entity_property_by_index,
    MM_OT_remove_entity_property_by_key,
    # Validation
    MM_OT_run_validation,
    # Lights panel
    MM_OT_add_light,
    MM_OT_remove_light,
    MM_OT_select_light,
    MM_OT_select_light_by_index,
    MM_OT_add_light_preset,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
