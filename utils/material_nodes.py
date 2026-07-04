"""IGB Material Settings node group for the Blender shader editor.

Creates a custom node group that replaces the Principled BSDF as the
main shader. The Principled BSDF lives INSIDE the node group, driven
by both texture inputs (from Image Texture nodes) and Alchemy material
properties (diffuse, specular, blend state, etc.).

Pipeline:
    Image Texture → [IGB Material] → Material Output
                     (BSDF inside)

The shared node group definition is named ".IGB Material"
(dot-prefix hides it from Blender's Add menu). Each material gets a
ShaderNodeGroup instance referencing this shared definition.

Usage:
    # On import — create and populate
    from ..utils.material_nodes import add_igb_node_to_material, set_igb_node_values
    node = add_igb_node_to_material(bl_mat)
    set_igb_node_values(node, {'diffuse': (1,1,1,1), 'blend_enabled': 1, ...})

    # On export — read back
    from ..utils.material_nodes import find_igb_node, read_igb_node_values
    node = find_igb_node(bl_mat)
    if node:
        vals = read_igb_node_values(node)
"""

import bpy

# Internal name for the shared node group definition
_NODE_GROUP_NAME = ".IGB Material"

# ── Input socket definitions ──────────────────────────────────────────
# (name, socket_type, default_value, min_value, max_value)
# socket_type: 'Color', 'Float', 'Int', 'Vector'

_INPUT_DEFS = [
    # ── Texture Inputs (from Image Texture nodes) ──
    # Base Texture defaults to WHITE so the final color = Diffuse when there is
    # no texture bound (Base Color = Base Texture × Diffuse × Tint × VertexColor).
    ("Base Texture",     "Color",  (1.0, 1.0, 1.0, 1.0), None, None),
    ("Texture Alpha",    "Float",  1.0,   0.0, 1.0),
    ("Normal",           "Vector", (0.0, 0.0, 0.0), None, None),
    ("Specular Texture", "Float",  0.0,   0.0, 1.0),
    # Gloss/mask map (MUA v8 texture unit 5). Export-only tag socket: plug an
    # Image Texture in and the v8 exporter emits it on unit 5. Not wired into
    # the Blender preview (MUA's shader, not Blender's, consumes it).
    ("Gloss/Mask Texture", "Color", (1.0, 1.0, 1.0, 1.0), None, None),
    # Per-vertex color (igVec4ucList / igColorAttr); white = no tint.
    ("Vertex Color",     "Color",  (1.0, 1.0, 1.0, 1.0), None, None),
    # Display-only brightness multiplier (game-look modulate). XML2 maps bake
    # lighting into DARK vertex colors and the fixed-function pipe renders
    # them modulate-2X — set 2.0 there. NOT exported to IGB.
    ("Brightness",       "Float",  1.0,   0.0, 4.0),

    # ── Material Colors (igMaterialAttr) ──
    ("Diffuse",          "Color",  (1.0, 1.0, 1.0, 1.0), None, None),
    ("Diffuse Alpha",    "Float",  1.0,   0.0, 1.0),
    ("Ambient",          "Color",  (0.588, 0.588, 0.588, 1.0), None, None),
    ("Specular",         "Color",  (0.0, 0.0, 0.0, 1.0), None, None),
    ("Emission",         "Color",  (0.0, 0.0, 0.0, 1.0), None, None),
    ("Shininess",        "Float",  0.0,   0.0, 128.0),
    ("Flags",            "Int",    31,    0,   255),

    # ── Blend State ──
    ("Blend Enabled",    "Int",    0,     0,   1),
    ("Blend Source",     "Int",    4,     0,   10),
    ("Blend Dest",       "Int",    5,     0,   10),
    ("Blend Equation",   "Int",    0,     0,   4),

    # ── Alpha Test ──
    ("Alpha Test Enabled", "Int",  0,     0,   1),
    ("Alpha Function",   "Int",    6,     0,   7),
    ("Alpha Reference",  "Float",  0.5,   0.0, 1.0),

    # ── Color Tint ──
    ("Color Tint",       "Color",  (1.0, 1.0, 1.0, 1.0), None, None),
    ("Color Tint Alpha", "Float",  1.0,   0.0, 1.0),

    # ── Render State ──
    ("Lighting Enabled", "Int",    1,     0,   1),
    ("Cull Face Enabled","Int",    1,     0,   1),
    ("Cull Face Mode",   "Int",    0,     0,   2),
    ("UV Anim Enabled",  "Int",    0,     0,   1),
    ("UV Anim Unit ID",  "Int",    0,     0,   7),

    # ── PS2 / Advanced Blend ──
    ("Blend Constant",   "Int",    0,     0,   255),
    ("Blend Stage",      "Int",    0,     0,   7),
    ("Blend A",          "Int",    0,     0,   3),
    ("Blend B",          "Int",    0,     0,   3),
    ("Blend C",          "Int",    0,     0,   3),
    ("Blend D",          "Int",    0,     0,   3),
]

# Map from property dict keys → socket names
# Used by set_igb_node_values / read_igb_node_values
_KEY_TO_SOCKET = {
    # Texture inputs
    'base_texture':       ('Base Texture', 'Texture Alpha'),
    # Material colors (RGBA tuples split into Color + Alpha)
    'diffuse':            ('Diffuse', 'Diffuse Alpha'),
    'ambient':            ('Ambient', None),
    'specular':           ('Specular', None),
    'emission':           ('Emission', None),
    'shininess':          ('Shininess', None),
    'flags':              ('Flags', None),
    # Blend
    'blend_enabled':      ('Blend Enabled', None),
    'blend_src':          ('Blend Source', None),
    'blend_dst':          ('Blend Dest', None),
    'blend_eq':           ('Blend Equation', None),
    # Alpha
    'alpha_test_enabled': ('Alpha Test Enabled', None),
    'alpha_func':         ('Alpha Function', None),
    'alpha_ref':          ('Alpha Reference', None),
    # Color tint
    'color':              ('Color Tint', 'Color Tint Alpha'),
    # Render state
    'lighting_enabled':   ('Lighting Enabled', None),
    'cull_face_enabled':  ('Cull Face Enabled', None),
    'cull_face_mode':     ('Cull Face Mode', None),
    'tex_matrix_enabled': ('UV Anim Enabled', None),
    'tex_matrix_unit_id': ('UV Anim Unit ID', None),
    # PS2 blend
    'blend_constant':     ('Blend Constant', None),
    'blend_stage':        ('Blend Stage', None),
    'blend_a':            ('Blend A', None),
    'blend_b':            ('Blend B', None),
    'blend_c':            ('Blend C', None),
    'blend_d':            ('Blend D', None),
}


# ── Public API ────────────────────────────────────────────────────────

def get_or_create_igb_node_group():
    """Get or create the shared ".IGB Material" node group.

    Contains a Principled BSDF internally. Outputs a Shader socket
    that connects directly to Material Output.

    Returns:
        bpy.types.ShaderNodeTree
    """
    ng = bpy.data.node_groups.get(_NODE_GROUP_NAME)

    if ng is not None:
        # Version check — must have Shader output (v3 architecture)
        existing_outputs = {item.name for item in ng.interface.items_tree
                           if item.item_type == 'SOCKET' and item.in_out == 'OUTPUT'}
        existing_inputs = {item.name for item in ng.interface.items_tree
                           if item.item_type == 'SOCKET' and item.in_out == 'INPUT'}
        # Recreate when the OUTPUT changed (pre-v3) OR the internal wiring
        # changed. 'Vertex Color' marks the v4 base-color pipeline
        # (Base Texture × Diffuse × Color Tint × Vertex Color); without it the
        # group is the old "Base Texture only" wiring and just adding the input
        # would leave it unwired — so rebuild from scratch.
        # 'Brightness' marks the v5 wiring (unlit/baked-lighting branch driven
        # by Lighting Enabled + brightness modulate) — older groups must be
        # rebuilt, adding the input alone would leave the branch unwired.
        if ('Shader' not in existing_outputs
                or 'Vertex Color' not in existing_inputs
                or 'Brightness' not in existing_inputs):
            bpy.data.node_groups.remove(ng)
            ng = None
        else:
            # Check for missing inputs (forward compat)
            for name, stype, default, mn, mx in _INPUT_DEFS:
                if name not in existing_inputs:
                    _add_input(ng, name, stype, default, mn, mx)
            return ng

    # Create fresh
    ng = bpy.data.node_groups.new(name=_NODE_GROUP_NAME, type='ShaderNodeTree')

    for name, stype, default, mn, mx in _INPUT_DEFS:
        _add_input(ng, name, stype, default, mn, mx)

    # Single Shader output → connects to Material Output
    ng.interface.new_socket(name="Shader", in_out='OUTPUT',
                            socket_type='NodeSocketShader')

    # Build the internal BSDF network
    _build_internal_nodes(ng)

    return ng


def add_igb_node_to_material(mat):
    """Add an IGB Material group node, replacing the Principled BSDF.

    Removes the existing Principled BSDF (if any), creates the IGB node,
    and wires it to the Material Output. Reconnects any existing texture
    nodes to the IGB node's texture inputs.

    Args:
        mat: bpy.types.Material

    Returns:
        bpy.types.ShaderNodeGroup instance
    """
    if not mat.use_nodes:
        mat.use_nodes = True

    existing = find_igb_node(mat)
    if existing is not None:
        return existing

    tree = mat.node_tree
    nodes = tree.nodes
    links = tree.links

    # Find existing Principled BSDF and its connections before removing
    bsdf = None
    tex_connections = {}  # input_name → (from_node, from_socket)
    for n in nodes:
        if n.type == 'BSDF_PRINCIPLED':
            bsdf = n
            break

    if bsdf is not None:
        # Save texture connections
        for link in list(links):
            if link.to_node == bsdf:
                if link.to_socket.name == 'Base Color':
                    tex_connections['base_color'] = (link.from_node, link.from_socket)
                elif link.to_socket.name == 'Alpha':
                    tex_connections['alpha'] = (link.from_node, link.from_socket)
                elif link.to_socket.name == 'Normal':
                    tex_connections['normal'] = (link.from_node, link.from_socket)
                elif link.to_socket.name in ('Specular IOR Level', 'Specular'):
                    tex_connections['specular_tex'] = (link.from_node, link.from_socket)

    # Create the IGB node group
    ng_def = get_or_create_igb_node_group()
    node = nodes.new('ShaderNodeGroup')
    node.node_tree = ng_def
    node.name = "IGB Material"
    node.label = "IGB Material"
    node.width = 260

    if bsdf is not None:
        node.location = bsdf.location
        # Remove the old BSDF
        nodes.remove(bsdf)
    else:
        node.location = (0, 300)

    # Find Material Output and wire Shader output to it
    mat_output = None
    for n in nodes:
        if n.type == 'OUTPUT_MATERIAL':
            mat_output = n
            break
    if mat_output is None:
        mat_output = nodes.new('ShaderNodeOutputMaterial')
        mat_output.location = (300, 300)

    links.new(node.outputs['Shader'], mat_output.inputs['Surface'])

    # Reconnect textures to IGB node inputs
    if 'base_color' in tex_connections:
        from_node, from_socket = tex_connections['base_color']
        links.new(from_socket, node.inputs['Base Texture'])
    if 'alpha' in tex_connections:
        from_node, from_socket = tex_connections['alpha']
        links.new(from_socket, node.inputs['Texture Alpha'])
    if 'normal' in tex_connections:
        from_node, from_socket = tex_connections['normal']
        links.new(from_socket, node.inputs['Normal'])
    if 'specular_tex' in tex_connections:
        from_node, from_socket = tex_connections['specular_tex']
        links.new(from_socket, node.inputs['Specular Texture'])

    return node


def find_igb_node(mat):
    """Find the IGB Material group node in a material.

    Args:
        mat: bpy.types.Material

    Returns:
        bpy.types.ShaderNodeGroup or None
    """
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return None

    for node in mat.node_tree.nodes:
        if (node.type == 'GROUP' and node.node_tree is not None
                and node.node_tree.name == _NODE_GROUP_NAME):
            return node
    return None


def set_igb_node_values(group_node, props):
    """Set input values on an IGB Material group node.

    Args:
        group_node: ShaderNodeGroup instance
        props: dict with keys from _KEY_TO_SOCKET

    Unrecognized keys are silently ignored.
    """
    if group_node is None:
        return

    for key, value in props.items():
        mapping = _KEY_TO_SOCKET.get(key)
        if mapping is None:
            continue

        color_socket_name, alpha_socket_name = mapping

        if alpha_socket_name is not None:
            # RGBA value split across Color + Float sockets
            if isinstance(value, (list, tuple)) and len(value) >= 3:
                _set_input(group_node, color_socket_name, value[:4])
                if len(value) >= 4:
                    _set_input(group_node, alpha_socket_name, float(value[3]))
            else:
                _set_input(group_node, color_socket_name, value)
        else:
            _set_input(group_node, color_socket_name, value)


def read_igb_node_values(group_node):
    """Read all values from an IGB Material group node.

    Returns:
        dict with keys matching the export format
    """
    if group_node is None:
        return {}

    result = {}

    for key, (color_name, alpha_name) in _KEY_TO_SOCKET.items():
        if key == 'base_texture':
            continue  # Skip texture inputs — not material properties

        if alpha_name is not None:
            color_val = _get_input(group_node, color_name)
            alpha_val = _get_input(group_node, alpha_name)
            if color_val is not None:
                r, g, b = color_val[0], color_val[1], color_val[2]
                a = alpha_val if alpha_val is not None else 1.0
                result[key] = (r, g, b, a)
        else:
            val = _get_input(group_node, color_name)
            if val is not None:
                result[key] = val

    return result


def migrate_custom_props_to_node(mat):
    """Migrate legacy igb_* custom properties to the node group.

    Creates the node group if needed, reads existing custom properties,
    and populates the node inputs.

    Args:
        mat: bpy.types.Material

    Returns:
        bpy.types.ShaderNodeGroup or None
    """
    node = add_igb_node_to_material(mat)

    props = {}

    # Material colors
    if "igb_diffuse" in mat:
        props['diffuse'] = tuple(mat["igb_diffuse"])
    if "igb_ambient" in mat:
        props['ambient'] = tuple(mat["igb_ambient"])
    if "igb_specular" in mat:
        props['specular'] = tuple(mat["igb_specular"])
    if "igb_emission" in mat:
        props['emission'] = tuple(mat["igb_emission"])
    if "igb_shininess" in mat:
        props['shininess'] = float(mat["igb_shininess"])
    if "igb_flags" in mat:
        props['flags'] = int(mat["igb_flags"])

    # Blend state
    if "igb_blend_enabled" in mat:
        props['blend_enabled'] = int(mat["igb_blend_enabled"])
    if "igb_blend_src" in mat:
        props['blend_src'] = int(mat["igb_blend_src"])
    if "igb_blend_dst" in mat:
        props['blend_dst'] = int(mat["igb_blend_dst"])
    if "igb_blend_eq" in mat:
        props['blend_eq'] = int(mat["igb_blend_eq"])
    if "igb_blend_constant" in mat:
        props['blend_constant'] = int(mat["igb_blend_constant"])
    if "igb_blend_stage" in mat:
        props['blend_stage'] = int(mat["igb_blend_stage"])
    for suffix in ('a', 'b', 'c', 'd'):
        k = f"igb_blend_{suffix}"
        if k in mat:
            props[f'blend_{suffix}'] = int(mat[k])

    # Alpha test
    if "igb_alpha_test_enabled" in mat:
        props['alpha_test_enabled'] = int(mat["igb_alpha_test_enabled"])
    if "igb_alpha_func" in mat:
        props['alpha_func'] = int(mat["igb_alpha_func"])
    if "igb_alpha_ref" in mat:
        props['alpha_ref'] = float(mat["igb_alpha_ref"])

    # Color tint
    if "igb_color_r" in mat:
        r = float(mat.get("igb_color_r", 1.0))
        g = float(mat.get("igb_color_g", 1.0))
        b = float(mat.get("igb_color_b", 1.0))
        a = float(mat.get("igb_color_a", 1.0))
        props['color'] = (r, g, b, a)

    # Render state
    if "igb_lighting_enabled" in mat:
        props['lighting_enabled'] = int(mat["igb_lighting_enabled"])
    if "igb_cull_face_enabled" in mat:
        props['cull_face_enabled'] = int(mat["igb_cull_face_enabled"])
    if "igb_cull_face_mode" in mat:
        props['cull_face_mode'] = int(mat["igb_cull_face_mode"])
    if "igb_tex_matrix_enabled" in mat:
        props['tex_matrix_enabled'] = int(mat["igb_tex_matrix_enabled"])
    if "igb_tex_matrix_unit_id" in mat:
        props['tex_matrix_unit_id'] = int(mat["igb_tex_matrix_unit_id"])

    if props:
        set_igb_node_values(node, props)

    return node


# ── Internal helpers ──────────────────────────────────────────────────

def _add_input(ng, name, stype, default, mn, mx):
    """Add an input socket to a node group interface."""
    socket_type_map = {
        'Color':  'NodeSocketColor',
        'Float':  'NodeSocketFloat',
        'Int':    'NodeSocketInt',
        'Vector': 'NodeSocketVector',
    }
    bl_type = socket_type_map[stype]
    sock = ng.interface.new_socket(name=name, in_out='INPUT',
                                   socket_type=bl_type)

    if default is not None:
        sock.default_value = default
    if mn is not None:
        sock.min_value = mn
    if mx is not None:
        sock.max_value = mx


def _set_input(group_node, socket_name, value):
    """Set a group node input's default_value by socket name."""
    inp = group_node.inputs.get(socket_name)
    if inp is None:
        return

    if isinstance(value, (list, tuple)):
        n = len(inp.default_value) if hasattr(inp.default_value, '__len__') else 1
        if n >= 3:
            vals = list(value)
            while len(vals) < n:
                vals.append(1.0)
            inp.default_value = vals[:n]
        else:
            inp.default_value = value[0] if value else 0
    else:
        inp.default_value = value


def _get_input(group_node, socket_name):
    """Get a group node input's default_value by socket name."""
    inp = group_node.inputs.get(socket_name)
    if inp is None:
        return None

    val = inp.default_value
    if hasattr(val, '__len__'):
        return tuple(val)
    return val


def _build_internal_nodes(ng):
    """Build the internal Principled BSDF network.

    The node group contains:
        Group Input → Principled BSDF → Group Output (Shader)

    Texture inputs (Base Texture, Normal, etc.) feed the BSDF directly.
    Material property inputs (Shininess, Specular Color, etc.) are
    converted to BSDF-compatible values via math nodes.

    Internal wiring:
        Base Texture    → BSDF Base Color
        Texture Alpha   → BSDF Alpha
        Normal          → BSDF Normal
        Specular Texture → (mixed with Specular color) → BSDF Specular
        Shininess       → 1-(shin/128) → BSDF Roughness
        Emission        → BSDF Emission Color + Strength
        Diffuse         → (stored only, not wired — Base Texture overrides)
    """
    gi = ng.nodes.new('NodeGroupInput')
    gi.location = (-600, 0)
    go = ng.nodes.new('NodeGroupOutput')
    go.location = (600, 0)

    links = ng.links

    # ── Principled BSDF (the core shader) ──
    bsdf = ng.nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.location = (200, 0)
    # (BSDF → output goes through the lit/unlit Mix Shader wired below)

    # ── Base Color = Base Texture × Diffuse × Color Tint × Vertex Color ──
    # Faithful fixed-function combine. Base Texture / Color Tint / Vertex Color
    # all default to WHITE, so:
    #   • textured mesh      → texture × diffuse(usually white) = texture
    #   • untextured colored → white × diffuse × tint × vcol = the real color
    # This is why color-driven geometry (UI backgrounds, igColorAttr/material
    # diffuse, vertex colors) now renders correctly instead of flat gray.
    # VectorMath MULTIPLY is used (component-wise RGB) for version stability;
    # alpha is combined separately below.
    cm1 = ng.nodes.new('ShaderNodeVectorMath')
    cm1.operation = 'MULTIPLY'; cm1.location = (-350, 250); cm1.label = "Tex x Diffuse"
    links.new(gi.outputs['Base Texture'], cm1.inputs[0])
    links.new(gi.outputs['Diffuse'], cm1.inputs[1])

    cm2 = ng.nodes.new('ShaderNodeVectorMath')
    cm2.operation = 'MULTIPLY'; cm2.location = (-200, 250); cm2.label = "x Color Tint"
    links.new(cm1.outputs['Vector'], cm2.inputs[0])
    links.new(gi.outputs['Color Tint'], cm2.inputs[1])

    cm3 = ng.nodes.new('ShaderNodeVectorMath')
    cm3.operation = 'MULTIPLY'; cm3.location = (-50, 250); cm3.label = "x Vertex Color"
    cm3.name = "BaseColorMix"
    links.new(cm2.outputs['Vector'], cm3.inputs[0])
    links.new(gi.outputs['Vertex Color'], cm3.inputs[1])

    # ── × Brightness (game-look modulate, e.g. XML2 modulate-2X) ──
    cmb = ng.nodes.new('ShaderNodeVectorMath')
    cmb.operation = 'SCALE'; cmb.location = (80, 250); cmb.label = "x Brightness"
    links.new(cm3.outputs['Vector'], cmb.inputs[0])
    links.new(gi.outputs['Brightness'], cmb.inputs['Scale'])
    links.new(cmb.outputs['Vector'], bsdf.inputs['Base Color'])

    # ── Alpha = Texture Alpha × Diffuse Alpha × Color Tint Alpha ──
    am1 = ng.nodes.new('ShaderNodeMath')
    am1.operation = 'MULTIPLY'; am1.location = (-200, 100)
    links.new(gi.outputs['Texture Alpha'], am1.inputs[0])
    links.new(gi.outputs['Diffuse Alpha'], am1.inputs[1])

    am2 = ng.nodes.new('ShaderNodeMath')
    am2.operation = 'MULTIPLY'; am2.location = (-50, 100)
    links.new(am1.outputs[0], am2.inputs[0])
    links.new(gi.outputs['Color Tint Alpha'], am2.inputs[1])
    links.new(am2.outputs[0], bsdf.inputs['Alpha'])

    # ── Normal → Normal ──
    links.new(gi.outputs['Normal'], bsdf.inputs['Normal'])

    # ── Specular Color → Specular float (max channel) ──
    # First: extract max channel from Specular Color input
    sep_spec = ng.nodes.new('ShaderNodeSeparateColor')
    sep_spec.location = (-300, -200)
    sep_spec.label = "Separate Specular"
    links.new(gi.outputs['Specular'], sep_spec.inputs['Color'])

    max_rg = ng.nodes.new('ShaderNodeMath')
    max_rg.operation = 'MAXIMUM'
    max_rg.location = (-100, -200)
    links.new(sep_spec.outputs['Red'], max_rg.inputs[0])
    links.new(sep_spec.outputs['Green'], max_rg.inputs[1])

    max_rgb = ng.nodes.new('ShaderNodeMath')
    max_rgb.operation = 'MAXIMUM'
    max_rgb.location = (50, -200)
    links.new(max_rg.outputs[0], max_rgb.inputs[0])
    links.new(sep_spec.outputs['Blue'], max_rgb.inputs[1])

    # If Specular Texture is connected, use it; otherwise use color max
    # Use Math Max to combine (spec texture adds on top of color spec)
    spec_combine = ng.nodes.new('ShaderNodeMath')
    spec_combine.operation = 'MAXIMUM'
    spec_combine.location = (50, -300)
    spec_combine.label = "Spec Max"
    links.new(max_rgb.outputs[0], spec_combine.inputs[0])
    links.new(gi.outputs['Specular Texture'], spec_combine.inputs[1])

    bsdf_spec = bsdf.inputs.get('Specular IOR Level') or bsdf.inputs.get('Specular')
    if bsdf_spec:
        links.new(spec_combine.outputs[0], bsdf_spec)

    # ── Shininess → Roughness: 1.0 - (shininess / 128.0) ──
    div_node = ng.nodes.new('ShaderNodeMath')
    div_node.operation = 'DIVIDE'
    div_node.location = (-300, -400)
    div_node.label = "Shininess / 128"
    links.new(gi.outputs['Shininess'], div_node.inputs[0])
    div_node.inputs[1].default_value = 128.0

    sub_node = ng.nodes.new('ShaderNodeMath')
    sub_node.operation = 'SUBTRACT'
    sub_node.location = (-100, -400)
    sub_node.label = "1.0 - normalized"
    sub_node.inputs[0].default_value = 1.0
    links.new(div_node.outputs[0], sub_node.inputs[1])

    clamp_node = ng.nodes.new('ShaderNodeClamp')
    clamp_node.location = (50, -400)
    links.new(sub_node.outputs[0], clamp_node.inputs['Value'])
    clamp_node.inputs['Min'].default_value = 0.0
    clamp_node.inputs['Max'].default_value = 1.0

    links.new(clamp_node.outputs[0], bsdf.inputs['Roughness'])

    # ── Emission → Emission Color + Strength ──
    links.new(gi.outputs['Emission'], bsdf.inputs['Emission Color'])

    # Emission Strength: 1.0 if any emission channel > 0.001
    sep_em = ng.nodes.new('ShaderNodeSeparateColor')
    sep_em.location = (-300, -600)
    sep_em.label = "Separate Emission"
    links.new(gi.outputs['Emission'], sep_em.inputs['Color'])

    max_em_rg = ng.nodes.new('ShaderNodeMath')
    max_em_rg.operation = 'MAXIMUM'
    max_em_rg.location = (-100, -600)
    links.new(sep_em.outputs['Red'], max_em_rg.inputs[0])
    links.new(sep_em.outputs['Green'], max_em_rg.inputs[1])

    max_em_rgb = ng.nodes.new('ShaderNodeMath')
    max_em_rgb.operation = 'MAXIMUM'
    max_em_rgb.location = (50, -600)
    links.new(max_em_rg.outputs[0], max_em_rgb.inputs[0])
    links.new(sep_em.outputs['Blue'], max_em_rgb.inputs[1])

    em_gt = ng.nodes.new('ShaderNodeMath')
    em_gt.operation = 'GREATER_THAN'
    em_gt.location = (50, -700)
    links.new(max_em_rgb.outputs[0], em_gt.inputs[0])
    em_gt.inputs[1].default_value = 0.001

    links.new(em_gt.outputs[0], bsdf.inputs['Emission Strength'])

    # ── Unlit branch (game fixed-function: lighting disabled) ─────────────
    # When the game renders with lighting OFF, the final pixel is exactly
    # texture × diffuse × tint × vertex color (× brightness) — no BSDF, no
    # scene lights. XML2 map geometry ships with lighting baked into DARK
    # vertex colors and relies on this path.
    unlit_em = ng.nodes.new('ShaderNodeEmission')
    unlit_em.location = (200, 500)
    unlit_em.label = "Unlit Color"
    links.new(cmb.outputs['Vector'], unlit_em.inputs['Color'])
    unlit_em.inputs['Strength'].default_value = 1.0

    unlit_transp = ng.nodes.new('ShaderNodeBsdfTransparent')
    unlit_transp.location = (200, 380)

    # honor alpha in the unlit path: alpha=0 → transparent, 1 → emission
    unlit_alpha_mix = ng.nodes.new('ShaderNodeMixShader')
    unlit_alpha_mix.location = (380, 450)
    unlit_alpha_mix.label = "Unlit Alpha"
    links.new(am2.outputs[0], unlit_alpha_mix.inputs['Fac'])
    links.new(unlit_transp.outputs['BSDF'], unlit_alpha_mix.inputs[1])
    links.new(unlit_em.outputs['Emission'], unlit_alpha_mix.inputs[2])

    # Lighting Enabled < 0.5 → unlit branch
    lit_lt = ng.nodes.new('ShaderNodeMath')
    lit_lt.operation = 'LESS_THAN'
    lit_lt.location = (380, 300)
    lit_lt.label = "Lighting Off?"
    links.new(gi.outputs['Lighting Enabled'], lit_lt.inputs[0])
    lit_lt.inputs[1].default_value = 0.5

    final_mix = ng.nodes.new('ShaderNodeMixShader')
    final_mix.location = (560, 100)
    final_mix.label = "Lit / Unlit"
    links.new(lit_lt.outputs[0], final_mix.inputs['Fac'])
    links.new(bsdf.outputs['BSDF'], final_mix.inputs[1])
    links.new(unlit_alpha_mix.outputs['Shader'], final_mix.inputs[2])

    go.location = (760, 100)
    links.new(final_mix.outputs['Shader'], go.inputs['Shader'])
