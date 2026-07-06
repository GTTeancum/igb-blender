bl_info = {
    "name": "IGB Format (Alchemy Engine)",
    "author": "Kaiko",
    "version": (0, 6, 1),
    "blender": (4, 4, 0),
    "location": "File > Import/Export, 3D Viewport > Sidebar > IGB",
    "description": "Import/Export Alchemy Engine IGB/IGZ files for X-Men Legends, XML2, MUA. By Kaiko.",
    "category": "Import-Export",
}

import os
import bpy
import bpy.utils.previews
from bpy.props import StringProperty, BoolProperty, EnumProperty, FloatProperty, IntProperty
from bpy_extras.io_utils import ImportHelper, ExportHelper

# Custom icon collection
_preview_collections = {}


def _get_icon_id():
    """Return the custom IGB icon ID, or 0 if not loaded."""
    pcoll = _preview_collections.get("igb_icons")
    if pcoll and "igb_icon" in pcoll:
        return pcoll["igb_icon"].icon_id
    return 0


def _game_preset_items(self, context):
    """Dynamic enum items for the game preset dropdown (import)."""
    from .game_profiles import get_profile_items
    return get_profile_items()


def _export_game_items(self, context):
    """Enum items for export game preset (no Auto-Detect)."""
    from .game_profiles import get_profile_items
    items = get_profile_items()
    # Filter out Auto-Detect for export
    return [(id, name, desc) for id, name, desc in items if id != 'auto']


def _export_texture_format_items(self, context):
    """Enum items for export texture format."""
    return [
        ('clut', "CLUT (Universal)",
         "256-color palette texture. Works in both XML2 and MUA (recommended)"),
        ('dxt5_xml2', "DXT5 (XML2 Only)",
         "DXT5 compressed for X-Men Legends 2 (standard RGB565)"),
        ('dxt5_mua', "DXT5 (MUA Only)",
         "DXT5 compressed for Marvel Ultimate Alliance (BGR565)"),
    ]


def _export_texture_resolution_items(self, context):
    """Shared texture-resolution cap (longest edge) — SAME control as the IGB
    Actor 'Export Skin'. Defaults to 512 (first item). See exporter.texture_size."""
    from .exporter.texture_size import texture_size_items
    return texture_size_items(self, context)


class ImportIGB(bpy.types.Operator, ImportHelper):
    """Import an Alchemy Engine IGB file"""
    bl_idname = "import_scene.igb"
    bl_label = "Import IGB"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".igb"

    filter_glob: StringProperty(
        default="*.igb;*.igz",
        options={'HIDDEN'},
    )

    game_preset: EnumProperty(
        name="Game Preset",
        description="Select the game this IGB file is from (affects format interpretation)",
        items=_game_preset_items,
    )

    import_normals: BoolProperty(
        name="Import Normals",
        description="Import vertex normals from the IGB file",
        default=True,
    )

    import_uvs: BoolProperty(
        name="Import UVs",
        description="Import texture coordinates",
        default=True,
    )

    import_vertex_colors: BoolProperty(
        name="Import Vertex Colors",
        description="Import vertex color data",
        default=True,
    )

    import_materials: BoolProperty(
        name="Import Materials",
        description="Import materials and textures",
        default=True,
    )

    import_collision: BoolProperty(
        name="Import Collision",
        description="Import collision hull as solid mesh in 'Colliders' collection",
        default=True,
    )

    import_lights: BoolProperty(
        name="Import Lights",
        description="Import scene lights from igLightSet nodes",
        default=True,
    )

    igz_texture_dir: StringProperty(
        name="Texture Directory",
        description="(IGZ only) Path to the folder containing materials/ and models/ subfolders",
        subtype='DIR_PATH',
        default="",
    )

    import_entity_models: BoolProperty(
        name="Import Entity Models",
        description="(IGZ only) Import placed props/objects from companion .mua entity files",
        default=True,
    )

    def invoke(self, context, event):
        # Always reset to auto-detect when opening the dialog so the
        # previous import's manual selection doesn't carry over.
        self.game_preset = "auto"
        return super().invoke(context, event)

    def execute(self, context):
        from .importer.import_igb import import_igb
        return import_igb(context, self.filepath, self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "game_preset")
        layout.separator()
        layout.prop(self, "import_normals")
        layout.prop(self, "import_uvs")
        layout.prop(self, "import_vertex_colors")
        layout.prop(self, "import_materials")
        layout.separator()
        layout.prop(self, "import_collision")
        layout.prop(self, "import_lights")

        # Show IGZ options when importing .igz
        if self.filepath.lower().endswith('.igz'):
            layout.prop(self, "import_entity_models")
            if self.import_materials:
                layout.separator()
                box = layout.box()
                box.label(text="IGZ Data Directory", icon='TEXTURE')
                box.prop(self, "igz_texture_dir")


class ImportIGZ(bpy.types.Operator, ImportHelper):
    """Import an Alchemy Engine IGZ file"""
    bl_idname = "import_scene.igz"
    bl_label = "Import IGZ"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".igz"

    filter_glob: StringProperty(
        default="*.igz",
        options={'HIDDEN'},
    )

    game_preset: EnumProperty(
        name="Game Preset",
        description="Select the game this IGZ file is from (affects format interpretation)",
        items=_game_preset_items,
    )

    import_normals: BoolProperty(
        name="Import Normals",
        description="Import vertex normals from the IGZ file",
        default=True,
    )

    import_uvs: BoolProperty(
        name="Import UVs",
        description="Import texture coordinates",
        default=True,
    )

    import_vertex_colors: BoolProperty(
        name="Import Vertex Colors",
        description="Import vertex color data",
        default=True,
    )

    import_materials: BoolProperty(
        name="Import Materials",
        description="Import materials and textures",
        default=True,
    )

    import_collision: BoolProperty(
        name="Import Collision",
        description="Import collision hull as solid mesh in 'Colliders' collection",
        default=True,
    )

    import_lights: BoolProperty(
        name="Import Lights",
        description="Import scene lights from igLightSet nodes",
        default=True,
    )

    igz_texture_dir: StringProperty(
        name="Texture Directory",
        description="Path to the folder containing materials/ and models/ subfolders",
        subtype='DIR_PATH',
        default="",
    )

    import_entity_models: BoolProperty(
        name="Import Entity Models",
        description="Import placed props/objects from companion .mua entity files (models/ directory)",
        default=True,
    )

    def invoke(self, context, event):
        self.game_preset = "auto"
        return super().invoke(context, event)

    def execute(self, context):
        from .importer.import_igb import import_igb
        return import_igb(context, self.filepath, self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "game_preset")
        layout.separator()
        layout.prop(self, "import_normals")
        layout.prop(self, "import_uvs")
        layout.prop(self, "import_vertex_colors")
        layout.prop(self, "import_materials")
        layout.separator()
        layout.prop(self, "import_collision")
        layout.prop(self, "import_lights")
        layout.prop(self, "import_entity_models")
        layout.separator()
        box = layout.box()
        box.label(text="IGZ Data Directory", icon='TEXTURE')
        box.prop(self, "igz_texture_dir")


class ExportIGB(bpy.types.Operator, ExportHelper):
    """Export the current scene as an Alchemy Engine IGB file (maps / environments)"""
    bl_idname = "export_scene.igb"
    bl_label = "Export Scene IGB"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".igb"

    filter_glob: StringProperty(
        default="*.igb",
        options={'HIDDEN'},
    )

    # Mirrors the IGB Actor export: pick the game, the exporter applies the
    # correct texture encoding + map support automatically.
    game_target: EnumProperty(
        name="Game",
        description="Which game this map/scene is for. The exporter picks "
                    "the correct texture encoding automatically",
        items=[
            ('XML2', "X-Men Legends 2",
             "DXT5 (XML2) or CLUT textures. XML2 has no normal maps"),
            ('MUA', "Marvel Ultimate Alliance",
             "DXT5 (MUA BGR) textures, with normal + specular + gloss maps "
             "exported automatically (reads each material's Normal Map / "
             "Specular / Gloss textures, same as the IGB Actor tab)"),
        ],
        default='XML2',
    )

    texture_format: EnumProperty(
        name="Texture Format",
        description="Texture encoding format for game compatibility",
        items=_export_texture_format_items,
    )

    texture_resolution: EnumProperty(
        name="Texture Resolution",
        description="Optional cap on texture size (longest edge). "
                    "Use lower values to shrink IGB file size",
        items=_export_texture_resolution_items,
    )

    normal_y_flip: BoolProperty(
        name="Flip Normal Green (Y)",
        description="Convert Blender's OpenGL (Y-up) normal green back to "
                    "MUA's DirectX (Y-down). Leave ON (default) — matches "
                    "native MUA normal maps and the addon's importer",
        default=True,
    )

    use_mipmaps: BoolProperty(
        name="Generate Mipmaps",
        description="Build the full mipmap chain for every texture (smoother "
                    "at distance). Turn off to ship only the base level for a "
                    "~33% smaller file — but distant surfaces may shimmer",
        default=True,
    )

    # Internal knobs export_igb reads; set from game_target in execute().
    # Per-map export toggles (MUA), default ON — untick to drop a map.
    use_normal_maps: BoolProperty(name="Normal Maps",
                                  description="Export normal maps (MUA)",
                                  default=True)
    use_specular_maps: BoolProperty(name="Specular Maps",
                                    description="Export specular maps (MUA)",
                                    default=True)
    use_gloss_map: BoolProperty(name="Gloss / Mask Maps",
                                description="Export gloss/mask maps (MUA)",
                                default=True)

    collision_source: EnumProperty(
        name="Collision Source",
        description="Source geometry for collision hull export",
        items=[
            ('COLLIDERS', "Colliders Collection",
             "Use objects from the 'Colliders' collection"),
            ('VISUAL', "Visual Mesh",
             "Auto-generate collision from the visible mesh geometry"),
            ('NONE', "None",
             "Do not export collision data"),
        ],
        default='COLLIDERS',
    )

    surface_type: IntProperty(
        name="Surface Type",
        description="Surface type for collision (0=default, 1=stone, 12=wood). "
                    "Only used for faces without a custom 'surface_type' attribute",
        default=0,
        min=0,
    )

    export_lights: BoolProperty(
        name="Export Lights",
        description="Export scene lights as igLightSet objects",
        default=True,
    )

    def execute(self, context):
        from .exporter.export_igb import export_igb
        # Apply the game's correct texture encoding + map support (mirrors
        # the IGB Actor export: the game choice drives everything).
        if self.game_target == 'MUA':
            self.texture_format = 'dxt5_mua'
            # export ticks carry the user's choice for MUA
        else:
            # XML2 keeps the texture-format choice, but never MUA encoding
            if self.texture_format == 'dxt5_mua':
                self.texture_format = 'dxt5_xml2'
            self.use_normal_maps = False
            self.use_specular_maps = False
            self.use_gloss_map = False
        return export_igb(context, self.filepath, self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "game_target")
        layout.prop(self, "texture_resolution")
        layout.prop(self, "use_mipmaps")
        # Show the format consequence (same pattern as the Actor export)
        fmt = layout.box().column(align=True)
        if self.game_target == 'MUA':
            fmt.label(text="MUA (PC): DXT5 (MUA) textures", icon='CHECKMARK')
            mp = layout.box().column(align=True)
            mp.label(text="Export Maps:", icon='TEXTURE')
            mp.prop(self, "use_normal_maps")
            mp.prop(self, "use_specular_maps")
            mp.prop(self, "use_gloss_map")
            if self.use_normal_maps:
                layout.prop(self, "normal_y_flip")
        else:
            fmt.label(text="XML2 (PC): DXT5 or CLUT textures", icon='SCENE')
            fmt.label(text="No normal/specular maps (XML2 has none).",
                      icon='INFO')
            fmt.label(text="For normal maps, pick Marvel Ultimate Alliance.",
                      icon='ERROR')
            layout.prop(self, "texture_format")
        layout.separator()
        layout.label(text="Collision", icon='MOD_PHYSICS')
        box = layout.box()
        box.prop(self, "collision_source")
        if self.collision_source != 'NONE':
            box.prop(self, "surface_type")
        layout.separator()
        layout.label(text="Lighting", icon='LIGHT')
        box = layout.box()
        box.prop(self, "export_lights")
        layout.separator()
        layout.label(text="Exports all scene meshes with materials & textures")


class IGB_MT_import_actor(bpy.types.Menu):
    """File > Import > IGB Actor — characters (skeleton + skins + anims)."""
    bl_idname = "IGB_MT_import_actor"
    bl_label = "IGB Actor"

    def draw(self, context):
        self.layout.operator("actor.import_actor",
                             text="Actor / Skin (.igb)",
                             icon_value=_get_icon_id())


class IGB_MT_import_map(bpy.types.Menu):
    """File > Import > IGB Map — scenes / maps / minimaps."""
    bl_idname = "IGB_MT_import_map"
    bl_label = "IGB Map / Scene"

    def draw(self, context):
        icon = _get_icon_id()
        l = self.layout
        l.operator(ImportIGB.bl_idname, text="Scene IGB (.igb)", icon_value=icon)
        l.operator(ImportIGZ.bl_idname, text="Scene IGZ (.igz)", icon_value=icon)
        l.operator(ImportZAM.bl_idname, text="ZAM Minimap (.zam)")


class IGB_MT_export_actor(bpy.types.Menu):
    """File > Export > IGB Actor — skins + animations."""
    bl_idname = "IGB_MT_export_actor"
    bl_label = "IGB Actor"

    def draw(self, context):
        icon = _get_icon_id()
        l = self.layout
        l.operator("actor.export_skin", text="Skin (.igb)", icon_value=icon)
        l.operator("actor.export_animations", text="Animations (.igb)",
                   icon_value=icon)


class IGB_MT_export_map(bpy.types.Menu):
    """File > Export > IGB Map — scenes / minimaps."""
    bl_idname = "IGB_MT_export_map"
    bl_label = "IGB Map / Scene"

    def draw(self, context):
        icon = _get_icon_id()
        l = self.layout
        l.operator(ExportIGB.bl_idname, text="Scene IGB (.igb)", icon_value=icon)
        l.operator(ExportZAM.bl_idname, text="ZAM Minimap (.zam)")


def menu_func_import(self, context):
    icon = _get_icon_id()
    self.layout.menu("IGB_MT_import_actor", icon_value=icon)
    self.layout.menu("IGB_MT_import_map", icon_value=icon)


def menu_func_export(self, context):
    icon = _get_icon_id()
    self.layout.menu("IGB_MT_export_actor", icon_value=icon)
    self.layout.menu("IGB_MT_export_map", icon_value=icon)


class ImportZAM(bpy.types.Operator, ImportHelper):
    """Import a ZAM minimap file"""
    bl_idname = "import_mesh.zam"
    bl_label = "Import ZAM"
    bl_options = {'PRESET', 'UNDO'}

    filename_ext = ".zam"

    filter_glob: StringProperty(
        default="*.zam",
        options={'HIDDEN'},
    )

    scale: FloatProperty(
        name="Scale",
        description="Scale factor for coordinates",
        default=0.01, min=0.001, max=10.0,
    )

    def execute(self, context):
        import os
        from .mapmaker.zam_io import parse_zam, create_mesh_from_zam

        zam_data = parse_zam(self.filepath)
        name = os.path.splitext(os.path.basename(self.filepath))[0]
        obj, tri_count = create_mesh_from_zam(name, zam_data, self.scale)
        context.collection.objects.link(obj)

        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        context.view_layer.objects.active = obj

        self.report({'INFO'},
                    f"Imported {tri_count} triangles from "
                    f"{len(zam_data['polygons'])} polygons ({name})")
        return {'FINISHED'}


class ExportZAM(bpy.types.Operator, ExportHelper):
    """Export to ZAM minimap format"""
    bl_idname = "export_mesh.zam"
    bl_label = "Export ZAM"
    bl_options = {'PRESET'}

    filename_ext = ".zam"

    filter_glob: StringProperty(
        default="*.zam",
        options={'HIDDEN'},
    )

    scale: FloatProperty(
        name="Scale",
        description="Scale factor for coordinates",
        default=100.0, min=0.1, max=1000.0,
    )

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'MESH'

    def execute(self, context):
        from .mapmaker.zam_io import write_zam

        obj = context.active_object
        poly_count = write_zam(self.filepath, obj, self.scale)
        self.report({'INFO'},
                    f"Exported {poly_count} polygons to {self.filepath}")
        return {'FINISHED'}


_FILE_MENUS = (IGB_MT_import_actor, IGB_MT_import_map,
               IGB_MT_export_actor, IGB_MT_export_map)


def register():
    # Load custom icon
    pcoll = bpy.utils.previews.new()
    icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
    if os.path.exists(icon_path):
        pcoll.load("igb_icon", icon_path, 'IMAGE')
    _preview_collections["igb_icons"] = pcoll

    bpy.utils.register_class(ImportIGB)
    bpy.utils.register_class(ImportIGZ)
    bpy.utils.register_class(ExportIGB)
    bpy.utils.register_class(ImportZAM)
    bpy.utils.register_class(ExportZAM)
    # Submodules register their own operators (actor.export_skin,
    # actor.import_actor) which the File menu entries below reference.
    from . import panels
    panels.register()
    from . import mapmaker
    mapmaker.register()
    from . import actor
    actor.register()
    from . import teammenu
    teammenu.register()
    # The "IGB Menu Editor" tab registers LAST so its sidebar tab appears at the
    # end, giving the order: IGB Tools, IGB Map Maker, IGB Actors, IGB Team Menu,
    # IGB Menu Editor (N-panel tabs follow registration order).
    mapmaker.menu_panels.register()

    # Per-material MUA blend mode -> igBlendStateAttr + igBlendFunctionAttr on
    # the MUA (v4) export. The four values mirror what real MUA models use.
    bpy.types.Material.igb_blend_mode = bpy.props.EnumProperty(
        name="Blend Mode",
        description="How this material blends in-game. Exported as "
                    "igBlendStateAttr + igBlendFunctionAttr (MUA only)",
        items=[
            ('OPAQUE', "Opaque",
             "No blending (default) — solid material"),
            ('ALPHA', "Alpha Blend",
             "Standard transparency: SRC_ALPHA, ONE_MINUS_SRC_ALPHA"),
            ('ADDITIVE', "Additive (glow)",
             "Glow / energy effects: SRC_ALPHA, ONE"),
            ('INV_ALPHA_ADD', "Inverse-Alpha Additive",
             "Found on some MUA models: ONE_MINUS_SRC_ALPHA, ONE"),
        ],
        default='OPAQUE',
    )

    # Per-material "use this map" toggles (MUA only). The export skips a map
    # when its toggle is off, even if an image is assigned — lets the user keep
    # a map in the .blend but leave it out of a given export.
    bpy.types.Material.igb_use_normal_map = bpy.props.BoolProperty(
        name="Use Normal Map",
        description="Export this material's normal map (MUA v4 + DXT5)",
        default=True)
    bpy.types.Material.igb_use_specular_map = bpy.props.BoolProperty(
        name="Use Specular Map",
        description="Export this material's specular map (MUA v4 + DXT5)",
        default=True)
    bpy.types.Material.igb_use_gloss_map = bpy.props.BoolProperty(
        name="Use Gloss/Mask Map",
        description="Export this material's gloss/mask map (MUA next-gen / "
                    "v8 texture unit 5)",
        default=True)

    # File menu: grouped IGB Actor / IGB Map submenus under Import + Export.
    for _mcls in _FILE_MENUS:
        bpy.utils.register_class(_mcls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for _mcls in reversed(_FILE_MENUS):
        try:
            bpy.utils.unregister_class(_mcls)
        except RuntimeError:
            pass

    if hasattr(bpy.types.Material, "igb_blend_mode"):
        del bpy.types.Material.igb_blend_mode
    for _p in ("igb_use_normal_map", "igb_use_specular_map",
               "igb_use_gloss_map"):
        if hasattr(bpy.types.Material, _p):
            delattr(bpy.types.Material, _p)

    from . import mapmaker
    mapmaker.menu_panels.unregister()   # registered last -> unregister first
    from . import teammenu
    teammenu.unregister()
    from . import actor
    actor.unregister()
    mapmaker.unregister()
    from . import panels
    panels.unregister()

    bpy.utils.unregister_class(ExportZAM)
    bpy.utils.unregister_class(ImportZAM)
    bpy.utils.unregister_class(ExportIGB)
    bpy.utils.unregister_class(ImportIGZ)
    bpy.utils.unregister_class(ImportIGB)

    # Remove custom icon
    for pcoll in _preview_collections.values():
        bpy.utils.previews.remove(pcoll)
    _preview_collections.clear()


if __name__ == "__main__":
    register()
