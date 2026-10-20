import sys
sys.dont_write_bytecode = True
"""Create a public-domain synthetic scene for the background integration check."""
import importlib.util,sys
from pathlib import Path
from types import SimpleNamespace
import bpy
worker=Path(__file__).with_name('worker.py')
spec=importlib.util.spec_from_file_location('worker',worker);w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
addon,output,scratch=sys.argv[sys.argv.index('--')+1:]
bpy.context.preferences.filepaths.temporary_directory=scratch
w.load_addon(addon)
from igb_blender.exporter.export_igb import export_igb
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add()
options=SimpleNamespace(texture_format='clut',texture_resolution='256',collision_source='NONE',use_mipmaps=False,report=lambda *x:None)
assert export_igb(bpy.context,output,options)=={'FINISHED'}
print(w.inspect_asset(Path(output),Path(output).name))
