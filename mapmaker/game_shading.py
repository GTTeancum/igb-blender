"""Game-accurate shading setup — make Blender render like XML2/MUA.

Three things make imported maps look "standard Blender" instead of in-game:

1. Color management: Blender's default AgX/Filmic tone-mapping washes the
   image out. The games output raw sRGB → view_transform 'Standard'.
2. Baked lighting: XML2 map geometry ships its lighting BAKED into dark
   vertex colors (measured genosha1: avg 0.33) and renders fixed-function
   with lighting OFF at modulate-2X. In Blender that means: unlit
   emission = tex × vcol × 2 (the IGB Material group's Lighting Enabled=0 +
   Brightness=2 path), NOT a Principled BSDF lit by scene lamps.
3. Light energy: igLightAttr quadratic attenuation converts EXACTLY to
   Blender watts (game: 1/(q·d²), Blender point: E/(4π·d²) → E = 4π/q —
   ≈125,000 W for the common q=0.0001, not the old 10 W). MUA maps use
   attenuation-free fill lights → approximated with a reference-distance
   energy, tunable via light_power.
"""

import math
import logging

import bpy

_log = logging.getLogger(__name__)

_SKIP_COLLECTIONS = ("Colliders",)
_IGB_GROUP_PREFIX = ".IGB Material"

FOUR_PI = 4.0 * math.pi
# reference distance (game units) for attenuation-free / linear lights
NO_ATTEN_REF_DIST = 200.0


def _is_igb_group_node(node):
    return (node.type == 'GROUP' and node.node_tree is not None
            and node.node_tree.name.startswith(_IGB_GROUP_PREFIX))


def _iter_igb_nodes(mat):
    if not mat or not mat.use_nodes or mat.node_tree is None:
        return
    for n in mat.node_tree.nodes:
        if _is_igb_group_node(n):
            yield n


def apply_game_shading(context, baked_brightness=2.0, light_power=1.0,
                       operator=None):
    """Apply the full game-look setup to the scene. Returns a stats dict."""
    scene = context.scene
    stats = {"baked_mats": 0, "lights": 0, "world": False}

    # --- 1. color management: raw sRGB like the game ---
    try:
        scene.view_settings.view_transform = 'Standard'
        scene.view_settings.look = 'None'
        scene.view_settings.exposure = 0.0
        scene.view_settings.gamma = 1.0
    except Exception as e:
        _log.warning("view transform: %s", e)

    # --- 2. baked-lit materials (vertex-colored map geometry) ---
    skip_objs = set()
    for cname in _SKIP_COLLECTIONS:
        coll = bpy.data.collections.get(cname)
        if coll:
            skip_objs.update(o.name for o in coll.objects)
    npc = bpy.data.collections.get("[MapMaker] NPC Actors")
    if npc:
        skip_objs.update(o.name for o in npc.objects)

    done_mats = set()
    for obj in scene.objects:
        if obj.type != 'MESH' or obj.name in skip_objs:
            continue
        me = obj.data
        if not getattr(me, 'color_attributes', None):
            continue
        if len(me.color_attributes) == 0:
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None or mat.name in done_mats:
                continue
            if mat.name == "IGB_Collision":
                continue
            changed = False
            for node in _iter_igb_nodes(mat):
                if 'Lighting Enabled' in node.inputs:
                    node.inputs['Lighting Enabled'].default_value = 0
                    changed = True
                if 'Brightness' in node.inputs:
                    node.inputs['Brightness'].default_value = baked_brightness
            if changed:
                done_mats.add(mat.name)
                stats["baked_mats"] += 1

    # --- 3. game light energies ---
    for obj in scene.objects:
        if obj.type != 'LIGHT' or "ig_attenuation" not in obj:
            continue
        data = obj.data
        att = list(obj["ig_attenuation"])
        c, l, q = (att + [0.0, 0.0, 0.0])[:3]
        # recover the light's intensity (max diffuse channel). New imports
        # store ig_diffuse_max; older ones encoded it as energy = max*10.
        mx = obj.get("ig_diffuse_max")
        if mx is None:
            mx = max(data.energy / 10.0, 0.05) if data.energy <= 40 else 1.0
        if data.type == 'SUN':
            energy = mx * 2.0
        elif q > 1e-9:
            # exact inverse-square match: 1/(q d²) == E/(4π d²)
            energy = mx * FOUR_PI / q * light_power
        elif l > 1e-9:
            # match game 1/(l·d) at the reference distance
            energy = mx * FOUR_PI * NO_ATTEN_REF_DIST / l * light_power
        else:
            # attenuation-free game fill light — approximate: full
            # brightness at the reference distance
            energy = mx * FOUR_PI * NO_ATTEN_REF_DIST ** 2 * light_power
        data.energy = energy
        if data.type in ('POINT', 'SPOT'):
            data.shadow_soft_size = 10.0
        # The games' fixed-function lights cast NO shadow maps (characters
        # use drop shadows) — and 20-30 shadowed point lights also overflow
        # EEVEE's shadow pool. Shadows off = game-accurate AND fast.
        data.use_shadow = (data.type == 'SUN')
        stats["lights"] += 1

    # --- 4. world ambient floor ---
    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("World")
        scene.world = world
        world.use_nodes = True
        bg = world.node_tree.nodes.get("Background")
        if bg:
            bg.inputs['Color'].default_value = (0.03, 0.03, 0.035, 1.0)
            bg.inputs['Strength'].default_value = 1.0
        stats["world"] = True

    return stats
