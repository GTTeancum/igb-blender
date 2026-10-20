"""Asset-free integration check of the registered Export Skin operator.

Run: blender --background --factory-startup --python-exit-code 1
             --python tests/blender_skin_presets.py
No files are exported; the builder boundary is mocked to inspect preset routing.
"""
import importlib.util
from pathlib import Path
import sys
from unittest.mock import patch

import bpy


root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "igb_blender", root / "__init__.py", submodule_search_locations=[str(root)])
addon = importlib.util.module_from_spec(spec)
sys.modules["igb_blender"] = addon
spec.loader.exec_module(addon)
addon.register()

from igb_blender.actor.operators import _populate_import_results
from igb_blender.exporter import skin_export

arm = bpy.data.objects.new("TestArm", bpy.data.armatures.new("TestArm"))
bpy.context.collection.objects.link(arm)
arm["igb_skin_bone_translations"] = [0.0, 0.0, 0.0]
mesh = bpy.data.objects.new("TestSkin", bpy.data.meshes.new("TestSkin"))
bpy.context.collection.objects.link(mesh)
props = bpy.context.scene.igb_actor
_populate_import_results(bpy.context, props, arm, [("default", mesh)], [])
props.skins_index = 0

rna = bpy.ops.actor.export_skin.get_rna_type()
assert "WII" in rna.properties["platform"].enum_items.keys()
cases = [
    ("MUA", "WII", False, "V6", "wii_rgb5a3", False, False),
    ("XML2", "WII", False, "V6", "wii_rgb5a3", False, False),
    ("MUA", "GC", False, "V6", "cmpr", False, True),
    ("MUA", "PS2", False, "V6", "clut", False, True),
    ("MUA", "PC", False, "V4", "dxt5", True, True),
    ("XML2", "PC", False, "V6", "clut", False, True),
    ("MUA", "WII", True, "V6", "dxt5", True, True),
]
for game, platform, mannequin, version, texture, swap_rb, mips in cases:
    with patch.object(skin_export, "export_skin", return_value=True) as export:
        result = bpy.ops.actor.export_skin(
            "EXEC_DEFAULT", filepath=str(root / "unused.igb"),
            game_target=game, platform=platform, mannequin_mode=mannequin,
            texture_resolution="256", use_normal_maps=True,
            use_specular_maps=True, use_gloss_map=True)
        assert result == {"FINISHED"}, (game, platform, result)
        args = export.call_args.kwargs
        assert args["igb_format"] == version, args
        assert args["texture_mode"] == texture, args
        assert args["swap_rb"] == swap_rb, args
        assert args["use_mipmaps"] == mips, args
        assert args["max_texture_size"] == 256, args
        maps = (args["use_normal_maps"], args["use_specular_maps"],
                args["use_gloss_map"])
        assert maps == ((True,) * 3 if platform == "PC" and game == "MUA"
                        else (False,) * 3), args
        if platform == "WII" and not mannequin:
            assert args["actor_graph"] == "OFF", args
print("PASS: seven registered Export Skin preset combinations")
