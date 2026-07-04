"""Scene graph node classes for traversing the Alchemy scene graph.

Provides high-level abstractions over the raw IGB objects for:
- igSceneInfo: scene root container
- igGroup/igNode: node hierarchy
- igTransform: transform nodes with 4x4 matrices
- igAttrSet: attribute containers
- igGeometry: mesh geometry
"""

import struct

from ..igb_format.igb_objects import IGBObject, IGBMemoryBlock


class SceneGraph:
    """Traverses the Alchemy scene graph from an IGB file.

    Starting from igSceneInfo, walks the node tree to extract
    transforms, geometry, materials, and hierarchy.

    Combined map files (__combined.igb) contain multiple igSceneInfo
    objects, each with its own scene graph root. We collect ALL roots
    and walk each one during traversal.
    """

    def __init__(self, reader):
        self.reader = reader
        self.scene_info = None
        self.root_node = None
        self.root_nodes = []  # All scene graph roots (for multi-root files)
        self.up_vector = (0, 0, 1)  # Default Z-up

    def build(self):
        """Build the scene graph from the parsed IGB data."""
        # Find ALL igSceneInfo objects (combined map files have many)
        scene_infos = self.reader.get_objects_by_type(b"igSceneInfo")
        if not scene_infos:
            # No scene info - try to find geometry directly (actor files etc.)
            return self._build_without_scene_info()

        self.scene_info = scene_infos[0]

        # Extract up vector from the first scene info
        for slot, val, fi in self.scene_info._raw_fields:
            if fi.short_name == b"Vec3f":
                self.up_vector = val
                break

        # Collect root nodes from ALL igSceneInfo objects
        for si in scene_infos:
            root = self._get_scene_root(si)
            if root is not None and root not in self.root_nodes:
                self.root_nodes.append(root)

        # Also check the info list for additional scene infos
        if not self.root_nodes:
            info_list = self.reader.get_info_list()
            if info_list is not None:
                items = self.reader.resolve_object_list(info_list)
                for item in items:
                    if isinstance(item, IGBObject) and item.is_type(b"igSceneInfo"):
                        root = self._get_scene_root(item)
                        if root is not None and root not in self.root_nodes:
                            self.root_nodes.append(root)

        # Set self.root_node to the first root for backward compat
        if self.root_nodes:
            self.root_node = self.root_nodes[0]

        return len(self.root_nodes) > 0

    def _get_scene_root(self, scene_info_obj):
        """Extract the scene graph root node from an igSceneInfo object."""
        for slot, val, fi in scene_info_obj._raw_fields:
            if fi.short_name == b"ObjectRef" and val != -1:
                ref = self.reader.resolve_ref(val)
                if isinstance(ref, IGBObject) and ref.is_type(b"igNode"):
                    return ref
        return None

    def _build_without_scene_info(self):
        """Build scene graph for files without igSceneInfo (actors, etc.).

        For actor files: finds igSkin._skinnedGraph as the root node.
        This ensures we traverse ALL geometry subtrees in the skinned graph,
        not just one branch.

        Falls back to parentless igNode search, then flat mode.
        """
        # Strategy 1: Find igSkin._skinnedGraph (actor files)
        # igSkin has a _skinnedGraph field (ObjectRef to igNode) that contains
        # the full geometry hierarchy including all LOD/material subtrees
        skins = self.reader.get_objects_by_type(b"igSkin")
        for skin in skins:
            for slot, val, fi in skin._raw_fields:
                if fi.short_name == b"ObjectRef" and val != -1:
                    ref = self.reader.resolve_ref(val)
                    if isinstance(ref, IGBObject) and ref.is_type(b"igNode"):
                        self.root_node = ref
                        return True

        # Strategy 2: Try info list for root nodes
        info_list = self.reader.get_info_list()
        if info_list is not None:
            items = self.reader.resolve_object_list(info_list)
            for item in items:
                if isinstance(item, IGBObject) and item.is_type(b"igNode"):
                    self.root_node = item
                    return True

        # Strategy 3: Look for any top-level node (one with no parents that is a node)
        for obj in self.reader.objects:
            if isinstance(obj, IGBObject) and obj.is_type(b"igNode"):
                parents = self.reader.back_refs.get(obj.index, set())
                # Filter to only object parents (not memory block parents)
                obj_parents = [
                    p for p in parents
                    if isinstance(self.reader.objects[p], IGBObject)
                    and not self.reader.objects[p].is_type(b"igObjectList")
                    and not self.reader.objects[p].is_type(b"igNodeList")
                ]
                if not obj_parents:
                    self.root_node = obj
                    return True

        # Last resort: we have no root node but there may be geometry
        # The walk() will handle this by iterating all geometry attrs
        self._flat_mode = True
        return True

    def walk(self, visitor, node=None, parent_transform=None):
        """Walk the scene graph calling visitor methods.

        Alchemy scene graphs are DAGs (directed acyclic graphs) where the same
        node can be referenced from multiple parents (instancing). For example,
        a lamp mesh might be placed 100+ times across a map, each with a
        different parent transform. We must visit each instance separately.

        Uses an ancestor set to prevent cycles while allowing instancing.

        Combined map files have multiple root nodes (one per igSceneInfo).
        When no explicit node is given, we walk ALL roots.

        Args:
            visitor: object with visit_* methods
            node: starting node (defaults to all roots)
            parent_transform: accumulated 4x4 matrix (tuple of 16 floats)
        """
        if node is not None:
            # Explicit node given - walk just that one
            self._visit_node(node, visitor, parent_transform, set())
            return

        if getattr(self, '_flat_mode', False):
            # Flat mode: visit all geometry attrs directly
            for obj in self.reader.objects:
                if isinstance(obj, IGBObject):
                    if obj.is_type(b"igGeometryAttr") or obj.is_type(b"igGeometryAttr2"):
                        if hasattr(visitor, 'visit_geometry_attr'):
                            visitor.visit_geometry_attr(obj, None, None)
            return

        # Walk ALL root nodes (handles multi-root combined map files)
        roots = self.root_nodes if self.root_nodes else ([self.root_node] if self.root_node else [])
        for root in roots:
            # Use ancestor set for cycle prevention (not a global visited set)
            # This allows the same node to be visited multiple times from
            # different parent paths (instancing), while preventing infinite loops
            self._visit_node(root, visitor, parent_transform, set())

    def _visit_node(self, obj, visitor, parent_transform, ancestors):
        """Recursively visit a scene graph node.

        Args:
            obj: the current IGBObject node
            visitor: visitor with visit_* methods
            parent_transform: accumulated transform from parent chain
            ancestors: set of obj.index values in the current recursion path
                      (used for cycle detection, NOT for preventing instancing)
        """
        if not isinstance(obj, IGBObject):
            return
        # Cycle detection: only skip if this node is an ancestor (would create loop)
        if obj.index in ancestors:
            return

        # Add to ancestor path for this branch of recursion
        ancestors.add(obj.index)

        local_transform = parent_transform

        # Track igSegment enter (for segment name/flags propagation)
        is_segment = obj.is_type(b"igSegment")
        if is_segment and hasattr(visitor, 'enter_segment'):
            visitor.enter_segment(
                self._get_node_name(obj),
                self._get_node_flags(obj),
            )

        # Handle transform nodes
        if obj.is_type(b"igTransform"):
            matrix = self._get_transform_matrix(obj)
            if matrix is not None:
                if parent_transform is not None:
                    # Alchemy uses row-major matrices (translation in last row)
                    # For row-major: accumulated = local * parent
                    local_transform = _multiply_matrices(matrix, parent_transform)
                else:
                    local_transform = matrix
                if hasattr(visitor, 'visit_transform'):
                    visitor.visit_transform(obj, local_transform)

        # Handle geometry nodes
        if obj.is_type(b"igGeometry"):
            if hasattr(visitor, 'visit_geometry'):
                visitor.visit_geometry(obj, local_transform)

        # Handle igBlendMatrixSelect nodes (BMS inherits igAttrSet -> igGroup -> igNode)
        # BMS appears as a NODE in the scene graph, not as an attribute.
        # Must check BEFORE the generic igAttrSet handling.
        if obj.is_type(b"igBlendMatrixSelect"):
            if hasattr(visitor, 'visit_blend_matrix_select'):
                visitor.visit_blend_matrix_select(obj, obj)

        # Handle attribute sets.
        # Alchemy semantics: attributes on an igAttrSet apply to that node and
        # its SUBTREE only (state is pushed on entry and popped on exit).
        # Visitors that track material/texture state implement push_state()/
        # pop_state() so sibling subtrees don't leak state into each other.
        # State attrs are dispatched BEFORE geometry attrs so a geometry attr
        # always sees its own node's material state regardless of list order.
        pushed_state = False
        if obj.is_type(b"igAttrSet"):
            attrs = self._get_attr_list(obj)
            if attrs and hasattr(visitor, 'push_state'):
                visitor.push_state()
                pushed_state = True
            for attr in attrs:
                if isinstance(attr, IGBObject):
                    if attr.is_type(b"igGeometryAttr") or attr.is_type(b"igGeometryAttr2"):
                        continue  # dispatched after state attrs below
                    elif attr.is_type(b"igMaterialAttr"):
                        if hasattr(visitor, 'visit_material_attr'):
                            visitor.visit_material_attr(attr, obj)
                    elif attr.is_type(b"igTextureBindAttr"):
                        if hasattr(visitor, 'visit_texture_bind_attr'):
                            visitor.visit_texture_bind_attr(attr, obj)
                    elif attr.is_type(b"igBlendStateAttr"):
                        if hasattr(visitor, 'visit_blend_state_attr'):
                            visitor.visit_blend_state_attr(attr, obj)
                    elif attr.is_type(b"igBlendFunctionAttr"):
                        if hasattr(visitor, 'visit_blend_function_attr'):
                            visitor.visit_blend_function_attr(attr, obj)
                    elif attr.is_type(b"igAlphaStateAttr"):
                        if hasattr(visitor, 'visit_alpha_state_attr'):
                            visitor.visit_alpha_state_attr(attr, obj)
                    elif attr.is_type(b"igAlphaFunctionAttr"):
                        if hasattr(visitor, 'visit_alpha_function_attr'):
                            visitor.visit_alpha_function_attr(attr, obj)
                    elif attr.is_type(b"igColorAttr"):
                        if hasattr(visitor, 'visit_color_attr'):
                            visitor.visit_color_attr(attr, obj)
                    elif attr.is_type(b"igLightingStateAttr"):
                        if hasattr(visitor, 'visit_lighting_state_attr'):
                            visitor.visit_lighting_state_attr(attr, obj)
                    elif attr.is_type(b"igTextureMatrixStateAttr"):
                        if hasattr(visitor, 'visit_tex_matrix_state_attr'):
                            visitor.visit_tex_matrix_state_attr(attr, obj)
                    elif attr.is_type(b"igCullFaceAttr"):
                        if hasattr(visitor, 'visit_cull_face_attr'):
                            visitor.visit_cull_face_attr(attr, obj)
                    elif attr.is_type(b"igBlendMatrixSelect"):
                        if hasattr(visitor, 'visit_blend_matrix_select'):
                            visitor.visit_blend_matrix_select(attr, obj)
            # Second pass: geometry attrs (now that this node's state attrs
            # have all been applied)
            for attr in attrs:
                if isinstance(attr, IGBObject):
                    if attr.is_type(b"igGeometryAttr") or attr.is_type(b"igGeometryAttr2"):
                        if hasattr(visitor, 'visit_geometry_attr'):
                            visitor.visit_geometry_attr(attr, local_transform, obj)

        # Handle light set nodes (contain igLightAttr objects)
        if obj.is_type(b"igLightSet"):
            if hasattr(visitor, 'visit_light_set'):
                visitor.visit_light_set(obj, local_transform)

        # Recurse into children
        children = self._get_children(obj)
        for child in children:
            self._visit_node(child, visitor, local_transform, ancestors)

        # Restore state on subtree exit (matches Alchemy's push/pop semantics)
        if pushed_state and hasattr(visitor, 'pop_state'):
            visitor.pop_state()

        # Track igSegment exit
        if is_segment and hasattr(visitor, 'exit_segment'):
            visitor.exit_segment()

        # Remove from ancestor path when backtracking
        # (allows this node to be visited again from a different parent)
        ancestors.discard(obj.index)

    def _get_node_name(self, obj):
        """Read name (slot 2 String) from any igNamedObject."""
        s = self.reader.slot_offset  # v4/v5 slots are +1 vs v6
        for slot, val, fi in obj._raw_fields:
            if slot == 2 + s and fi.short_name == b"String":
                if isinstance(val, bytes):
                    return val.decode('utf-8', errors='replace')
                return val if isinstance(val, str) else ''
        return ''

    def _get_node_flags(self, obj):
        """Read _nodeFlags (slot 5 Int) from any igNode. Default 0."""
        s = self.reader.slot_offset  # v4/v5 slots are +1 vs v6
        for slot, val, fi in obj._raw_fields:
            if slot == 5 + s and fi.short_name in (b"Int", b"UInt"):
                return int(val)
        return 0

    def _get_transform_matrix(self, transform_obj):
        """Extract 4x4 matrix from igTransform."""
        for slot, val, fi in transform_obj._raw_fields:
            if fi.short_name == b"Matrix44f":
                # val is a tuple of 16 floats (row-major)
                return val
        return None

    def _get_children(self, node):
        """Get child nodes from igGroup._childList or similar."""
        children = []
        for slot, val, fi in node._raw_fields:
            if fi.short_name == b"ObjectRef" and val != -1:
                ref = self.reader.resolve_ref(val)
                if isinstance(ref, IGBObject):
                    if ref.is_type(b"igObjectList") or ref.is_type(b"igNodeList"):
                        # This is a child list - resolve it
                        items = self.reader.resolve_object_list(ref)
                        children.extend(
                            item for item in items
                            if isinstance(item, IGBObject) and item.is_type(b"igNode")
                        )
                    elif ref.is_type(b"igNode"):
                        children.append(ref)
        return children

    def _get_attr_list(self, attrset_obj):
        """Get attributes from igAttrSet._attributes."""
        attrs = []
        for slot, val, fi in attrset_obj._raw_fields:
            if fi.short_name == b"ObjectRef" and val != -1:
                ref = self.reader.resolve_ref(val)
                if isinstance(ref, IGBObject):
                    if ref.is_type(b"igObjectList") or ref.is_type(b"igAttrList"):
                        items = self.reader.resolve_object_list(ref)
                        attrs.extend(
                            item for item in items
                            if isinstance(item, IGBObject)
                        )
                    elif ref.is_type(b"igAttr"):
                        attrs.append(ref)
        return attrs


def _multiply_matrices(a, b):
    """Multiply two 4x4 matrices (tuples of 16 floats, row-major).

    Returns new tuple of 16 floats.
    """
    result = [0.0] * 16
    for row in range(4):
        for col in range(4):
            s = 0.0
            for k in range(4):
                s += a[row * 4 + k] * b[k * 4 + col]
            result[row * 4 + col] = s
    return tuple(result)
