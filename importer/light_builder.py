"""Build Blender lights from parsed IGB light data.

Creates Blender light objects (SUN, POINT, SPOT) from igLightAttr data,
with color, energy, attenuation, and shadow settings.

Energy mapping follows the IGBConverter reference implementation:
    - Constant attenuation > 0: energy = (1 / constant) * 100
    - Linear attenuation > 0:   energy = (0.025 / linear) * 100
    - Quadratic attenuation > 0: energy = (0.0006 / quadratic) * 100
"""

import bpy
import math
from mathutils import Vector

from ..scene_graph.sg_lights import (
    ParsedLight,
    LIGHT_TYPE_DIRECTIONAL,
    LIGHT_TYPE_POINT,
    LIGHT_TYPE_SPOT,
)


def build_light(parsed_light, name="IGB_Light"):
    """Create a Blender light object from ParsedLight data.

    Maps Alchemy light types to Blender equivalents:
        DIRECTIONAL -> SUN
        POINT       -> POINT
        SPOT        -> SPOT

    Position is taken directly from the igLightAttr (world-space).
    Direction is converted to a rotation for SUN/SPOT lights.

    Args:
        parsed_light: ParsedLight data container
        name: name for the Blender light data-block and object

    Returns:
        bpy.types.Object (the light object) or None
    """
    bl_type = parsed_light.blender_type

    # Create light data-block
    light_data = bpy.data.lights.new(name=name, type=bl_type)

    # In Alchemy, brightness is encoded directly in the diffuse color RGB.
    # e.g. (0.78, 0.98, 1.0) = bright blue-white, (0.0, 0.20, 0.20) = dim teal
    # We extract the max channel as a brightness factor and normalize the color.
    diffuse = parsed_light.diffuse
    max_channel = max(diffuse[0], diffuse[1], diffuse[2], 0.001)

    # Normalized color (0-1 range for Blender color swatch)
    light_data.color = (
        diffuse[0] / max_channel,
        diffuse[1] / max_channel,
        diffuse[2] / max_channel,
    )

    # Energy: scale the max channel into Blender watts.
    # Alchemy color 1.0 ≈ full brightness. A multiplier of 10 gives
    # a reasonable Blender viewport result that the user can fine-tune.
    light_data.energy = max_channel * 10.0

    # Shadow casting
    light_data.use_shadow = parsed_light.cast_shadow

    # Spot-specific settings
    if bl_type == 'SPOT':
        # Outer cone angle: Alchemy stores half-angle, FBX doubles it
        outer_angle_deg = parsed_light.cutoff * 2.0
        # Clamp to Blender's valid range (1-180 degrees)
        outer_angle_deg = max(1.0, min(180.0, outer_angle_deg))
        light_data.spot_size = math.radians(outer_angle_deg)

        # Inner cone blend: how much of the outer cone fades out
        # IGBConverter: inner = (1 - falloff * 30) * outer
        # Blender spot_blend: 0 = sharp edge, 1 = fully soft
        inner_ratio = max(0.0, min(1.0, 1.0 - parsed_light.falloff * 30.0))
        light_data.spot_blend = 1.0 - inner_ratio

    # Create the Blender object
    light_obj = bpy.data.objects.new(name, light_data)

    # Set position from igLightAttr._position (world-space coordinates)
    pos = parsed_light.position
    light_obj.location = (pos[0], pos[1], pos[2])

    # Set rotation for directional/spot lights from direction vector
    if bl_type in ('SUN', 'SPOT'):
        direction = Vector(parsed_light.direction)
        if direction.length > 0.0001:
            # Blender lights emit along local -Z by default.
            # Convert direction vector to rotation that aligns -Z with it.
            light_obj.rotation_euler = direction.to_track_quat(
                '-Z', 'Y').to_euler()

    # Store Alchemy-specific properties that Blender doesn't natively support.
    # These are preserved as custom properties for lossless round-trip export.
    light_obj["ig_light_id"] = parsed_light.light_id
    light_obj["ig_ambient"] = list(parsed_light.ambient)
    light_obj["ig_specular"] = list(parsed_light.specular)
    light_obj["ig_attenuation"] = list(parsed_light.attenuation)
    light_obj["ig_shininess"] = parsed_light.shininess
    # raw intensity for the game-shading energy conversion (mapmaker/game_shading.py)
    light_obj["ig_diffuse_max"] = max_channel

    return light_obj


def set_world_ambient(parsed_light):
    """Set the Blender world ambient from a SceneAmbient light.

    Creates or updates the scene world to use the light's ambient color
    as the background environment color.

    Args:
        parsed_light: ParsedLight from a "SceneAmbient" igLightSet
    """
    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new("World")
        bpy.context.scene.world = world

    # Prefer ambient color, fall back to diffuse
    ambient = parsed_light.ambient
    if max(ambient[0], ambient[1], ambient[2]) > 0.001:
        color = ambient
    else:
        color = parsed_light.diffuse

    # Set up world node tree with background color
    world.use_nodes = True
    nodes = world.node_tree.nodes
    bg_node = nodes.get("Background")
    if bg_node is not None:
        bg_node.inputs['Color'].default_value = (
            color[0], color[1], color[2], 1.0)
        # Keep strength at 1.0 — the ambient color IS the desired brightness
        bg_node.inputs['Strength'].default_value = 1.0
