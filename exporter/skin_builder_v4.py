"""From-scratch v4 (Alchemy 2.5 / 3ds Max 5 era) skin IGB builder.

Mirrors the structure of 0103.igb — a community 3ds Max skin the XML2 engine
demonstrably loads and plays all animations with — instead of the native v6
layout. The motivation: only Max-style skins carry the animation-combiner
actor graph that lets the engine retarget animation root heights onto RESIZED
skeletons (the "menu float" fix). Those combiner types crashed XML2 when
hand-translated to v6, but in v4 we have engine-proven layouts verbatim
(extracted by tools/gen_v4_meta.py into skin_builder_v4_meta.py).

v4 vs v6 structural differences handled here:
  - all object slots +1 (v4 igObject base has one extra slot)
  - igAnimationTrack has NO rest-translation Vec3f; igAnimationBinding has
    no trailing ObjectRefs (real class evolution, not just renumbering)
  - vertex streams: igVertexArray1_1 carries a 76-byte/19-slot table of
    DIRECTORY ENTRY indices (slot0=positions 12bpv, slot1=normals 12bpv,
    slot11=UVs 8bpv) + inline blend weights (16bpv) / indices (4bpv) refs
  - geometry is a plain TRIANGLE LIST (Enum 3) — no strips, no PrimLengthArray
  - outlines are black igColorAttr sets (no igOverrideAttrSet)
  - scene graph embeds the bone hierarchy as igModelViewMatrixBoneSelect tree
  - dir entries: igObjectDirEntry = 2 fields, igMemoryDirEntry = 5 fields
    (no memory pool handle); no index buffer; info index at END of file
"""

import struct

from .skin_builder_v4_meta import (
    V4_META_FIELDS, V4_META_OBJECTS, N_BASE_METAS, N_ANIM_METAS,
    V4_ALIGNMENT,
)

try:
    from ..igb_format.igb_writer import (
        IGBWriter, MetaFieldDef, MetaObjectDef, MetaObjectFieldDef,
        EntryDef, ObjectDef, ObjectFieldDef, MemoryBlockDef,
    )
except ImportError:  # standalone testing outside the addon package
    from igb_pkg.igb_format.igb_writer import (
        IGBWriter, MetaFieldDef, MetaObjectDef, MetaObjectFieldDef,
        EntryDef, ObjectDef, ObjectFieldDef, MemoryBlockDef,
    )

# Meta-object indices by name (table order is fixed by the generator)
_MO = {t[0]: i for i, t in enumerate(V4_META_OBJECTS)}

# Short name of each meta field (e.g. 'igVec4fMetaField' -> 'Vec4f')
_FIELD_SHORT = {n: n[2:-9] for n, _ma, _mi in V4_META_FIELDS}

MO_OBJECT = _MO['igObject']
MO_NAMED = _MO['igNamedObject']
MO_OBJ_ENTRY = _MO['igObjectDirEntry']
MO_MEM_ENTRY = _MO['igMemoryDirEntry']
MO_ATTR = _MO['igAttr']
MO_TEXTURE_ATTR = _MO['igTextureAttr']
MO_MIPMAP_LIST = _MO['igImageMipMapList']
MO_CLUT = _MO['igClut']
MO_TEX_BIND = _MO['igTextureBindAttr']
MO_IMAGE = _MO['igImage']
MO_INFO_LIST = _MO['igInfoList']
MO_TRANS_DEF_LIST = _MO['igAnimationTransitionDefinitionList']
MO_TRACK_LIST = _MO['igAnimationTrackList']
MO_BINDING_LIST = _MO['igAnimationBindingList']
MO_ANIMATION = _MO['igAnimation']
MO_SKIN = _MO['igSkin']
MO_AABOX = _MO['igAABox']
MO_ATTR_LIST = _MO['igAttrList']
MO_NODE_LIST = _MO['igNodeList']
MO_GROUP = _MO['igGroup']
MO_ATTR_SET = _MO['igAttrSet']
MO_GEOMETRY = _MO['igGeometry']
MO_BONE_INFO = _MO['igSkeletonBoneInfo']
MO_BONE_INFO_LIST = _MO['igSkeletonBoneInfoList']
MO_SKELETON = _MO['igSkeleton']
MO_TRACK = _MO['igAnimationTrack']
MO_BINDING = _MO['igAnimationBinding']
MO_GEOM_ATTR_1_5 = _MO['igGeometryAttr1_5']
MO_INDEX_ARRAY = _MO['igIndexArray']
MO_VERTEX_ARRAY_1_1 = _MO['igVertexArray1_1']
MO_TEX_STATE = _MO['igTextureStateAttr']
MO_MATERIAL = _MO['igMaterialAttr']
MO_COLOR = _MO['igColorAttr']
MO_LIGHTING_STATE = _MO['igLightingStateAttr']
MO_MVMBS = _MO['igModelViewMatrixBoneSelect']
MO_COMBINER_LIST = _MO['igAnimationCombinerList']
MO_APPEARANCE_LIST = _MO['igAppearanceList']
MO_SKIN_LIST = _MO['igSkinList']
MO_ANIMATION_LIST = _MO['igAnimationList']
MO_SKELETON_LIST = _MO['igSkeletonList']
MO_ANIM_DB = _MO['igAnimationDatabase']
MO_BMS = _MO['igBlendMatrixSelect']
MO_INT_LIST = _MO['igIntList']
MO_VBS_ATTR = _MO['igVertexBlendStateAttr']
MO_CULL_FACE = _MO['igCullFaceAttr']
MO_SEGMENT = _MO['igSegment']
# v6-translated render-state attrs (emitted only when user enables them)
MO_ALPHA_FUNC = _MO['igAlphaFunctionAttr']
MO_ALPHA_STATE = _MO['igAlphaStateAttr']
MO_BLEND_STATE = _MO['igBlendStateAttr']
MO_BLEND_FUNC = _MO['igBlendFunctionAttr']
# Cable graft (combiner graph)
MO_ANIM_SYSTEM = _MO['igAnimationSystem']
MO_COMBINER = _MO['igAnimationCombiner']
MO_CBONE_INFO = _MO['igAnimationCombinerBoneInfo']
MO_CBONE_INFO_LIST = _MO['igAnimationCombinerBoneInfoList']
MO_CBONE_INFO_LIST_LIST = _MO['igAnimationCombinerBoneInfoListList']
MO_ANIM_STATE = _MO['igAnimationState']
MO_ANIM_STATE_LIST = _MO['igAnimationStateList']
MO_ANIM_MODIFIER_LIST = _MO['igAnimationModifierList']
MO_ACTOR_INFO = _MO['igActorInfo']
MO_ACTOR_LIST = _MO['igActorList']
MO_ACTOR = _MO['igActor']
MO_APPEARANCE = _MO['igAppearance']
MO_STRING_OBJ_LIST = _MO['igStringObjList']
MO_MVMBS_LIST = _MO['igModelViewMatrixBoneSelectList']

# Memory block "type tag" mirror of 0103.igb (the tags look recycled /
# arbitrary — igClut on weight streams! — but they are what the engine
# has been loading for 20 years, so copy them exactly)
TAG_LIST_DATA = MO_TEXTURE_ATTR        # all igDataList payloads
TAG_INT_LIST = MO_MEM_ENTRY            # igIntList payloads + binding map
TAG_VTX_STREAM = MO_NAMED              # position/normal/uv streams
TAG_VA_FORMAT = MO_ATTR                # 76-byte slot table
TAG_WEIGHTS = MO_CLUT                  # blend weights (align VertexArrayData)
TAG_BLEND_IDX = MO_MIPMAP_LIST         # blend indices
TAG_INDEX_DATA = MO_MIPMAP_LIST        # index array payload
TAG_CLUT_DATA = MO_MIPMAP_LIST         # palette colors
TAG_IMAGE_DATA = MO_MIPMAP_LIST        # pixels (align ImageData)
TAG_SKEL_TRANS = MO_ANIMATION          # skeleton bone translations
TAG_SKEL_MATS = MO_INFO_LIST           # skeleton inverse joint matrices

ALIGN_NONE = -1
ALIGN_IMAGE = 0        # "ImageData" in V4_ALIGNMENT
ALIGN_VERTEX = 1       # "VertexArrayData" in V4_ALIGNMENT

_IDENTITY44 = (1.0, 0.0, 0.0, 0.0,
               0.0, 1.0, 0.0, 0.0,
               0.0, 0.0, 1.0, 0.0,
               0.0, 0.0, 0.0, 1.0)

VTX_FMT_SLOTS = 19          # v4 slot-table size (v6 uses 20)
FMT_SKINNED = 0x443         # pos+normals+weights
FMT_SKINNED_UV = 0x10443    # pos+normals+uv+weights
# Normal-mapped format flag = 12649539, verified byte-for-byte against native
# 3ds Max MUA modder skins (0104bladecybernetic.igb, all 5 vertex arrays).
# NOTE: 12649539 == 0xC10443, NOT 0xC0E443 — earlier notes/code used the wrong
# hex (0xC0E443 == 12641347, a different value), so the engine read the
# normal-mapped vertex layout from the wrong slots and rendered the model BLACK.
# Declares the layout: slot 0 pos (12B), slot 1 normal x3 (36B), slot 11 uv (8B),
# slot 17 tangent (12B), slot 18 binormal (12B).
FMT_NORMAL_MAPPED = 0xC10443     # == 12649539
# extra ext-indexed-entry slots used only by the normal-mapped format
VTX_SLOT_TANGENT = 17
VTX_SLOT_BINORMAL = 18


def _compute_tangent_frame(positions, normals, uvs, indices):
    """Per-vertex orthonormal tangent + binormal from UVs (Lengyel's method).

    Returns (tangents, binormals) as lists of (x,y,z). Used to emit the
    tangent-frame vertex streams MUA's normal-map shader path reads. Requires
    UVs; callers must guard. Degenerate-UV triangles contribute nothing.
    """
    n = len(positions)
    tan = [[0.0, 0.0, 0.0] for _ in range(n)]
    bin_ = [[0.0, 0.0, 0.0] for _ in range(n)]
    for t in range(0, len(indices) - 2, 3):
        i0, i1, i2 = indices[t], indices[t + 1], indices[t + 2]
        if i0 >= n or i1 >= n or i2 >= n:
            continue
        p0, p1, p2 = positions[i0], positions[i1], positions[i2]
        w0, w1, w2 = uvs[i0], uvs[i1], uvs[i2]
        e1 = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
        e2 = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
        du1, dv1 = w1[0] - w0[0], w1[1] - w0[1]
        du2, dv2 = w2[0] - w0[0], w2[1] - w0[1]
        denom = du1 * dv2 - du2 * dv1
        f = 1.0 / denom if abs(denom) > 1e-12 else 0.0
        tx = (dv2 * e1[0] - dv1 * e2[0]) * f
        ty = (dv2 * e1[1] - dv1 * e2[1]) * f
        tz = (dv2 * e1[2] - dv1 * e2[2]) * f
        bx = (du1 * e2[0] - du2 * e1[0]) * f
        by = (du1 * e2[1] - du2 * e1[1]) * f
        bz = (du1 * e2[2] - du2 * e1[2]) * f
        for i in (i0, i1, i2):
            tan[i][0] += tx; tan[i][1] += ty; tan[i][2] += tz
            bin_[i][0] += bx; bin_[i][1] += by; bin_[i][2] += bz

    tangents, binormals = [], []
    for i in range(n):
        nx, ny, nz = normals[i] if i < len(normals) else (0.0, 0.0, 1.0)
        tx, ty, tz = tan[i]
        # Gram-Schmidt: T' = normalize(T - N*(N.T))
        d = nx * tx + ny * ty + nz * tz
        tx, ty, tz = tx - nx * d, ty - ny * d, tz - nz * d
        ln = (tx * tx + ty * ty + tz * tz) ** 0.5
        if ln < 1e-8:
            # Degenerate: pick any axis perpendicular to N
            if abs(nx) < 0.9:
                tx, ty, tz = 1.0 - nx * nx, -nx * ny, -nx * nz
            else:
                tx, ty, tz = -ny * nx, 1.0 - ny * ny, -ny * nz
            ln = (tx * tx + ty * ty + tz * tz) ** 0.5 or 1.0
        tx, ty, tz = tx / ln, ty / ln, tz / ln
        # Binormal = N x T, sign-corrected against the accumulated bitangent
        bxc = ny * tz - nz * ty
        byc = nz * tx - nx * tz
        bzc = nx * ty - ny * tx
        bax, bay, baz = bin_[i]
        if bxc * bax + byc * bay + bzc * baz < 0.0:
            bxc, byc, bzc = -bxc, -byc, -bzc
        tangents.append((tx, ty, tz))
        binormals.append((bxc, byc, bzc))
    return tangents, binormals


def _quat_from_matrix3(m):
    """Quaternion (x, y, z, w) from row-major 3x3 rotation rows."""
    t = m[0][0] + m[1][1] + m[2][2]
    if t > 0.0:
        s = (t + 1.0) ** 0.5 * 2.0
        return ((m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s,
                (m[1][0] - m[0][1]) / s, 0.25 * s)
    if m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = (1.0 + m[0][0] - m[1][1] - m[2][2]) ** 0.5 * 2.0
        return (0.25 * s, (m[0][1] + m[1][0]) / s,
                (m[0][2] + m[2][0]) / s, (m[2][1] - m[1][2]) / s)
    if m[1][1] > m[2][2]:
        s = (1.0 + m[1][1] - m[0][0] - m[2][2]) ** 0.5 * 2.0
        return ((m[0][1] + m[1][0]) / s, 0.25 * s,
                (m[1][2] + m[2][1]) / s, (m[0][2] - m[2][0]) / s)
    s = (1.0 + m[2][2] - m[0][0] - m[1][1]) ** 0.5 * 2.0
    return ((m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s,
            0.25 * s, (m[1][0] - m[0][1]) / s)


def _bone_world_rotation(bone):
    """World-space bind rotation rows from the row-major inverse joint matrix."""
    inv = bone.get('inv_joint_matrix')
    if not inv:
        # non-deforming bones (nubs) carry no matrix — identity
        return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    # inverse bind is row-major; rotation part transposed = forward rotation
    return [[inv[0], inv[4], inv[8]],
            [inv[1], inv[5], inv[9]],
            [inv[2], inv[6], inv[10]]]


def _mat3_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)]


def _mat3_transpose(a):
    return [[a[j][i] for j in range(3)] for i in range(3)]


def _local_bind_transforms(skeleton_data):
    """Per-bone (quat_xyzw, trans) parent-local bind transforms."""
    bones = skeleton_data['bones']
    world = [_bone_world_rotation(b) for b in bones]
    out = []
    for i, b in enumerate(bones):
        p = b.get('parent_idx', b.get('parent_index', -1))
        if p < 0:
            local = world[i]
        else:
            local = _mat3_mul(_mat3_transpose(world[p]), world[i])
        t = b.get('translation', b.get('trans', (0.0, 0.0, 0.0)))
        out.append((_quat_from_matrix3(local), tuple(t)))
    return out


def _world_bind_matrix(bone):
    """Column-style world bind Matrix44f (flat 16 floats) from inv_joint."""
    inv = bone.get('inv_joint_matrix')
    if not inv:
        return _IDENTITY44
    r = _bone_world_rotation(bone)
    # world translation = -(t_inv . R_inv_rows): solve from inverse bind
    ti = (inv[12], inv[13], inv[14])
    t = tuple(-(ti[0] * inv[0 + c] + ti[1] * inv[4 + c] + ti[2] * inv[8 + c])
              for c in range(3))
    # emitted row-major with translation in last row (Alchemy convention)
    return (r[0][0], r[1][0], r[2][0], 0.0,
            r[0][1], r[1][1], r[2][1], 0.0,
            r[0][2], r[1][2], r[2][2], 0.0,
            t[0], t[1], t[2], 1.0)


def _default_material():
    # mirrors 0103's MAIN (textured) material
    return {
        'diffuse': (1.0, 1.0, 1.0, 1.0),
        'ambient': (0.588, 0.588, 0.588, 1.0),
        'specular': (0.0, 0.0, 0.0, 0.0),
        'emission': (0.0, 0.0, 0.0, 0.0),
        'shininess': 0.0,   # native default (specular is black here anyway)
        'flags': 0,
    }


def _default_outline_material():
    return {
        'diffuse': (0.0, 0.0, 0.0, 1.0),
        'ambient': (0.0, 0.0, 0.0, 1.0),
        'specular': (0.0, 0.0, 0.0, 0.0),
        'emission': (0.0, 0.0, 0.0, 0.0),
        'shininess': 0.0,
        'flags': 0,
    }


class SkinBuilderV4:
    """Builds Max-style v4 skin IGBs (0103.igb structure)."""

    def build_skin(self, submeshes, skeleton_data, bms_palette,
                   export_name='', actor_graph='ANIM', use_normal_maps=False):
        """Build a complete v4 skin.

        Args:
            submeshes: same dicts as SkinBuilder.build_skin (mesh, material,
                texture_name, clut_data/texture_levels, is_outline,
                optional is_segment)
            skeleton_data: same dict as SkinBuilder.build_skin
            bms_palette: list of blend-matrix bone indices
            export_name: file stem, used for igSkin/geometry names
            actor_graph: 'ANIM' (0103 mirror — bind-pose anim only) or
                'FULL' (adds Cable's combiner/actor graph)

        Returns:
            IGBWriter ready to write
        """
        self._obj_list = []      # ('obj', mo_idx, fields) | ('mem', tag, data, align)
        self._mode = actor_graph if actor_graph in ('ANIM', 'FULL') else 'ANIM'
        self._use_normal_maps = bool(use_normal_maps)
        # Use the DXT multi-bind (diffuse/normal/spec/gloss) path whenever
        # normal maps were requested OR any submesh carries a specular/gloss
        # (or normal) map. This lets specular ship WITHOUT a normal — the vertex
        # tangent frame (below, keyed on _use_normal_maps) is only added when a
        # normal is actually present, since specular/gloss need no tangents.
        self._use_dxt_maps = bool(use_normal_maps) or any(
            (not s.get('is_outline'))
            and (s.get('normal_levels') or s.get('specular_levels')
                 or s.get('gloss_levels'))
            for s in submeshes)

        skin_name = export_name or skeleton_data.get('name', 'skin')
        bones = skeleton_data['bones']

        # ---- skeleton ----
        skeleton_idx = self._build_skeleton(skeleton_data, bms_palette)

        # ---- texture chains ----
        # Default path: CLUT (the proven-universal v4 texture). Normal-map path
        # (MUA only): DXT5 diffuse(unit0) + normal(unit1) + specular(unit2),
        # built per-submesh below from the texture_levels/normal_levels/
        # specular_levels the exporter prepared. DXT chains are cached by
        # (name, unit) so shared textures aren't duplicated.
        tex_chain_map = {}
        self._dxt_cache = {}
        outline_material = _default_outline_material()
        for sub in submeshes:
            if sub.get('is_outline'):
                if sub.get('material'):
                    outline_material = sub['material']
                continue
            if self._use_dxt_maps:
                continue  # DXT binds built per-submesh in the unit loop
            key = sub.get('texture_name', '') or ''
            if key in tex_chain_map or not sub.get('clut_data'):
                continue
            palette_data, index_data, cw, ch = sub['clut_data']
            tex_chain_map[key] = self._build_clut_chain(
                palette_data, index_data, cw, ch, key)
        default_chain = next(iter(tex_chain_map.values()), None)

        def _dxt_binds(sub, base_name):
            """[diffuse, normal, specular, gloss] bind idxs for a sub.

            Units mirror native MUA: 0=diffuse, 1=normal, 2=specular, and
            5=gloss/mask. Native v4 modder skins stop at unit 2; the unit-5
            gloss bind is the v8 convention applied to v4 (MUA reads units by
            ID regardless of file version). gloss_levels is only present when
            the material has a gloss image and its 'Use Gloss/Mask Map' toggle
            is on, so non-gloss materials emit no extra bind.
            """
            # Per-unit pfmt: diffuse/specular/gloss = DXT1 (14) when opaque (set by
            # the exporter), normal = ALWAYS DXT5 (16, alpha carries X). Matches
            # native (diffuse+spec DXT1, normal DXT5).
            specs = [(sub.get('texture_levels'), base_name, 0, sub.get('diffuse_pfmt', 16)),
                     (sub.get('normal_levels'), base_name + '_n', 1, 16),
                     (sub.get('specular_levels'), base_name + '_s', 2, sub.get('specular_pfmt', 16)),
                     (sub.get('gloss_levels'), base_name + '_g', 5, sub.get('gloss_pfmt', 16))]
            binds = []
            for levels, nm, unit, pfmt in specs:
                if not levels:
                    continue
                ck = (nm, unit)
                if ck not in self._dxt_cache:
                    self._dxt_cache[ck] = self._build_dxt_chain(levels, nm, unit, pfmt=pfmt)
                if self._dxt_cache[ck] is not None:
                    binds.append((self._dxt_cache[ck], unit))
            return binds

        # ---- shared attrs ----
        cull_idx = self._add_obj(MO_CULL_FACE, [
            (3, 1, 'Short', 2), (4, 1, 'Bool', 1), (5, 0, 'Enum', 4)])
        # Lighting-OFF state is attached ONLY to toon-outline sets; a normal
        # textured mesh never references it (native actors carry no such attr,
        # so the engine lights them by default). Create it lazily via
        # _get_light_state() so a skin with no outline emits no orphan attr —
        # an orphaned lighting-off object was a red herring in export diffs.
        self._light_state_idx = None
        # 0103 carries two igTextureStateAttr: texturing ON for textured
        # sets, OFF for outline sets
        texstate_on_idx = self._add_obj(MO_TEX_STATE, [
            (3, 1, 'Short', 2), (4, 1, 'Bool', 1), (5, 0, 'Int', 4)])
        texstate_off_idx = self._add_obj(MO_TEX_STATE, [
            (3, 1, 'Short', 2), (4, 0, 'Bool', 1), (5, 0, 'Int', 4)])
        # Per-unit texturing-ON states (stage = texture unit). Seed unit 0 with
        # the state above; _texstate_for_unit() adds 1/2/5 on demand.
        self._texstate_cache = {0: texstate_on_idx}
        vbs_idx = self._add_obj(MO_VBS_ATTR, [
            (3, 1, 'Short', 2), (4, 1, 'Bool', 1)])

        # ---- BMS palette int list ----
        palette_idx = self._build_int_list(bms_palette)

        # ---- per-submesh units ----
        unit_idxs = []        # children of the igActor01 group, in order
        bbox_mins, bbox_maxs = [], []
        for sub in submeshes:
            mesh = sub['mesh']
            bbox_mins.append(mesh.bbox_min)
            bbox_maxs.append(mesh.bbox_max)
            is_outline = bool(sub.get('is_outline'))
            segment_name = sub.get('segment_name', '') or ''
            mat = sub.get('material') or (
                outline_material if is_outline else _default_material())
            if is_outline:
                chain = None
            elif self._use_dxt_maps:
                base = (sub.get('texture_name', '') or segment_name or skin_name)
                binds = _dxt_binds(sub, base)
                chain = binds or None
            else:
                chain = tex_chain_map.get(sub.get('texture_name', '') or '',
                                          default_chain)
            if segment_name:
                unit_name = (f'{segment_name}_outline' if is_outline
                             else segment_name)
            else:
                unit_name = (f'{skin_name}_outline' if is_outline
                             else skin_name)
            unit = self._build_unit(mesh, mat, chain, is_outline,
                                    palette_idx, cull_idx,
                                    texstate_on_idx, texstate_off_idx,
                                    vbs_idx, unit_name)
            if segment_name:
                seg_nl = self._ref_list(MO_NODE_LIST, [unit])
                unit = self._add_obj(MO_SEGMENT, [
                    (3, unit_name, 'String', 4),
                    (4, -1, 'ObjectRef', 4),
                    (6, 0, 'Int', 4),
                    (7, seg_nl, 'ObjectRef', 4)])
            unit_idxs.append(unit)

        # ---- MVMBS bone hierarchy tree ----
        unit_idxs.append(self._build_mvmbs_tree(bones))

        # ---- actor root group ----
        actor_nl = self._ref_list(MO_NODE_LIST, unit_idxs)
        actor_group_idx = self._add_obj(MO_GROUP, [
            (3, 'igActor01', 'String', 4),
            (4, -1, 'ObjectRef', 4),
            (6, 0, 'Int', 4),
            (7, actor_nl, 'ObjectRef', 4)])

        # ---- bounding box + skin ----
        gmin = (min(b[0] for b in bbox_mins), min(b[1] for b in bbox_mins),
                min(b[2] for b in bbox_mins)) if bbox_mins else (0, 0, 0)
        gmax = (max(b[0] for b in bbox_maxs), max(b[1] for b in bbox_maxs),
                max(b[2] for b in bbox_maxs)) if bbox_maxs else (0, 0, 0)
        aabox_idx = self._add_obj(MO_AABOX, [
            (3, gmin, 'Vec3f', 12), (4, gmax, 'Vec3f', 12)])
        skin_idx = self._add_obj(MO_SKIN, [
            (3, skin_name, 'String', 4),
            (4, actor_group_idx, 'ObjectRef', 4),
            (5, aabox_idx, 'ObjectRef', 4)])

        # ---- bind-pose animation (always present, 0103-style) ----
        locals_ = _local_bind_transforms(skeleton_data)
        anim_idx = self._build_bindpose_animation(skeleton_idx, bones, locals_)

        # ---- combiner graph (FULL only — Cable layouts) ----
        combiner_idx = None
        actor_info_idx = None
        if self._mode == 'FULL':
            combiner_idx, actor_info_idx = self._build_actor_graph(
                skeleton_idx, skeleton_data, skin_idx, anim_idx,
                bms_palette, locals_)

        # ---- animation database ----
        adb_idx = self._build_animation_database(
            skeleton_idx, skin_idx, anim_idx, combiner_idx)

        if actor_info_idx is not None:
            # wire the database into the actor info (slot 8 _animationDatabase;
            # slot 7 is the actorList — donor layout)
            kind, mo_idx, fields = self._obj_list[actor_info_idx]
            fields = [(8, adb_idx, 'ObjectRef', 4) if f[0] == 8 else f
                      for f in fields]
            self._obj_list[actor_info_idx] = (kind, mo_idx, fields)

        # ---- info list ----
        info_refs = [adb_idx] if actor_info_idx is None else [actor_info_idx,
                                                              adb_idx]
        info_data = struct.pack('<' + 'i' * len(info_refs), *info_refs)
        info_mb = self._add_mem(TAG_LIST_DATA, info_data)
        info_list_idx = self._add_obj(MO_INFO_LIST, [
            (3, len(info_refs), 'Int', 4),
            (4, len(info_refs), 'Int', 4),
            (5, info_mb, 'MemoryRef', 4)])

        return self._finalize(info_list_idx)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _add_obj(self, mo_idx, fields):
        self._obj_list.append(('obj', mo_idx, fields))
        return len(self._obj_list) - 1

    def _add_mem(self, tag_mo_idx, data, align=ALIGN_NONE):
        self._obj_list.append(('mem', tag_mo_idx, data, align))
        return len(self._obj_list) - 1

    def _ref_list(self, mo_idx, refs):
        if refs:
            data = struct.pack('<' + 'i' * len(refs), *refs)
            mb = self._add_mem(TAG_LIST_DATA, data)
        else:
            mb = -1
        return self._add_obj(mo_idx, [
            (3, len(refs), 'Int', 4),
            (4, len(refs), 'Int', 4),
            (5, mb, 'MemoryRef', 4)])

    def _empty_list(self, mo_idx):
        return self._ref_list(mo_idx, [])

    def _build_int_list(self, values):
        data = struct.pack('<' + 'i' * len(values), *values)
        mb = self._add_mem(TAG_INT_LIST, data)
        return self._add_obj(MO_INT_LIST, [
            (3, len(values), 'Int', 4),
            (4, len(values), 'Int', 4),
            (5, mb, 'MemoryRef', 4)])

    # ---- textures ----

    def _build_clut_chain(self, palette_data, index_data, w, h, name):
        """igClut + igImage + mipmap list + igTextureAttr + igTextureBindAttr."""
        pal_mb = self._add_mem(TAG_CLUT_DATA, bytes(palette_data))
        clut_idx = self._add_obj(MO_CLUT, [
            (3, 7, 'Enum', 4),               # fmt: RGBA8888
            (4, 256, 'UnsignedInt', 4),      # entries
            (5, 4, 'Int', 4),                # bytes/entry
            (6, pal_mb, 'MemoryRef', 4),
            (7, len(palette_data), 'Int', 4)])
        pix_mb = self._add_mem(TAG_IMAGE_DATA, bytes(index_data),
                               align=ALIGN_IMAGE)
        mip_idx = self._empty_list(MO_MIPMAP_LIST)
        image_idx = self._add_obj(MO_IMAGE, [
            (3, w, 'UnsignedInt', 4),
            (4, h, 'UnsignedInt', 4),
            (5, 1, 'UnsignedInt', 4),
            (6, 1, 'UnsignedInt', 4),
            (7, 100, 'UnsignedInt', 4),
            (8, 0, 'UnsignedInt', 4),
            (9, 0, 'UnsignedInt', 4),
            (10, 0, 'UnsignedInt', 4),
            (11, 0, 'UnsignedInt', 4),
            (12, 65536, 'Enum', 4),          # pfmt PSMT8 (CLUT universal)
            (13, len(index_data), 'Int', 4),
            (14, pix_mb, 'MemoryRef', 4),
            (15, -1, 'MemoryRef', 4),
            (16, 1, 'Bool', 1),
            (17, 0, 'UnsignedInt', 4),
            (18, clut_idx, 'ObjectRef', 4),
            (19, 8, 'UnsignedInt', 4),       # bits per pixel
            (20, w, 'Int', 4),               # bytesPerRow (1 byte/px indexed)
            (21, 0, 'Bool', 1),
            (22, 0, 'UnsignedInt', 4),
            (23, name or '', 'String', 4)])
        tex_attr_idx = self._add_obj(MO_TEXTURE_ATTR, [
            (3, 1, 'Short', 2),
            (4, 0, 'UnsignedInt', 4),
            (5, 1, 'Enum', 4), (6, 1, 'Enum', 4),
            (7, 1, 'Enum', 4), (8, 1, 'Enum', 4),
            (10, 0, 'Enum', 4), (11, 0, 'Enum', 4),
            (12, image_idx, 'ObjectRef', 4),
            (13, 0, 'Bool', 1),
            (14, -1, 'ObjectRef', 4),
            (15, 1, 'Int', 4),
            (16, mip_idx, 'ObjectRef', 4)])
        bind_idx = self._add_obj(MO_TEX_BIND, [
            (3, 1, 'Short', 2),
            (4, tex_attr_idx, 'ObjectRef', 4),
            (5, 0, 'Int', 4)])
        return bind_idx

    def _build_dxt_chain(self, levels, name, unit_id, pfmt=16):
        """DXT igImage + full mip chain + igTextureAttr + igTextureBindAttr.

        Used by the normal-map path (units 0=diffuse, 1=normal, 2=specular,
        5=gloss). `levels` = [(bytes, w, h), ...] base-first (from
        compress_with_mipmaps). pfmt 16 = DXT5 (16-byte blocks), 14 = RGBA_DXT1
        (8-byte blocks). Returns the bind idx, or None if no level data.

        Emitting the FULL mip pyramid (not just the base) + a trilinear
        min/mag filter is REQUIRED: an un-mipmapped normal map aliases into
        harsh, crawling "hard shadows" under minification. Native MUA actors
        ship an 11-level chain with min=mag=LINEAR_MIPMAP_LINEAR (5) on every
        texture, including the DXT5 normal map.
        """
        if not levels:
            return None
        block = 8 if pfmt == 14 else 16     # DXT1 = 8-byte blocks, DXT5 = 16

        def _mk_image(comp_data, tw, th):
            stride = max(1, (tw + 3) // 4) * block
            pix_mb = self._add_mem(TAG_IMAGE_DATA, bytes(comp_data),
                                   align=ALIGN_IMAGE)
            return self._add_obj(MO_IMAGE, [
                (3, tw, 'UnsignedInt', 4),
                (4, th, 'UnsignedInt', 4),
                (5, 4, 'UnsignedInt', 4),
                (6, 1, 'UnsignedInt', 4),
                (7, 100, 'UnsignedInt', 4),
                (8, 2, 'UnsignedInt', 4),
                (9, 2, 'UnsignedInt', 4),
                (10, 2, 'UnsignedInt', 4),
                (11, 2, 'UnsignedInt', 4),
                (12, int(pfmt), 'Enum', 4),
                (13, len(comp_data), 'Int', 4),
                (14, pix_mb, 'MemoryRef', 4),
                (15, -1, 'MemoryRef', 4),
                (16, 1, 'Bool', 1),
                (17, 0, 'UnsignedInt', 4),
                (18, -1, 'ObjectRef', 4),        # no clut for DXT
                (19, 0, 'UnsignedInt', 4),
                (20, stride, 'Int', 4),          # bytesPerRow (block stride)
                (21, 1, 'Bool', 1),              # compressed
                (22, 0, 'UnsignedInt', 4),
                (23, name or '', 'String', 4)])

        base_idx = _mk_image(*levels[0])
        # The mip list holds the SMALLER levels (base is referenced separately
        # by the texture attr at slot 12); imageCount = total level count.
        mip_indices = [_mk_image(*lv) for lv in levels[1:]]
        has_mips = bool(mip_indices)
        mip_idx = (self._ref_list(MO_MIPMAP_LIST, mip_indices)
                   if has_mips else self._empty_list(MO_MIPMAP_LIST))
        # Trilinear (5) only when a chain is present; else plain LINEAR (1).
        filt = 5 if has_mips else 1
        tex_attr_idx = self._add_obj(MO_TEXTURE_ATTR, [
            (3, 1, 'Short', 2),
            (4, 0, 'UnsignedInt', 4),
            (5, filt, 'Enum', 4), (6, filt, 'Enum', 4),
            (7, 1, 'Enum', 4), (8, 1, 'Enum', 4),
            (10, 0, 'Enum', 4), (11, 0, 'Enum', 4),
            (12, base_idx, 'ObjectRef', 4),
            (13, 0, 'Bool', 1),
            (14, -1, 'ObjectRef', 4),
            (15, len(levels), 'Int', 4),
            (16, mip_idx, 'ObjectRef', 4)])
        return self._add_obj(MO_TEX_BIND, [
            (3, 1, 'Short', 2),
            (4, tex_attr_idx, 'ObjectRef', 4),
            (5, int(unit_id), 'Int', 4)])

    def _build_render_state_attrs(self, mat_props):
        """Optional blend/alpha attrs from IGB Materials panel settings.

        Mirrors SkinBuilder._build_render_state_attrs with v4 slot numbers.
        Types are emitted into the meta table always, but objects only when
        the user enabled the corresponding state.
        """
        attrs = []
        blend_on = bool(mat_props.get('blend_enabled', False))
        alpha_on = bool(mat_props.get('alpha_test_enabled', False))

        if blend_on or (alpha_on and 'blend_enabled' in mat_props):
            attrs.append(self._add_obj(MO_BLEND_STATE, [
                (3, 1, 'Short', 2),
                (5, int(blend_on), 'Bool', 1)]))

        if blend_on and mat_props.get('blend_src') is not None:
            attrs.append(self._add_obj(MO_BLEND_FUNC, [
                (3, 1, 'Short', 2),
                (5, mat_props.get('blend_src', 4), 'Enum', 4),
                (6, mat_props.get('blend_dst', 5), 'Enum', 4),
                (7, 0, 'Enum', 4),
                (8, -1, 'ObjectRef', 4),
                (9, 0, 'UnsignedChar', 1),
                (10, 0, 'Short', 2),
                (12, 0, 'Enum', 4),
                (13, 0, 'Enum', 4),
                (14, 0, 'Enum', 4),
                (15, 0, 'Enum', 4)]))

        if alpha_on:
            attrs.append(self._add_obj(MO_ALPHA_STATE, [
                (3, 1, 'Short', 2),
                (5, 1, 'Bool', 1)]))

        if alpha_on and mat_props.get('alpha_func') is not None:
            attrs.append(self._add_obj(MO_ALPHA_FUNC, [
                (3, 1, 'Short', 2),
                (5, mat_props.get('alpha_func', 6), 'Enum', 4),
                (6, mat_props.get('alpha_ref', 0.5), 'Float', 4)]))

        return attrs

    def _build_material(self, mat):
        # Shininess is the specular EXPONENT and is preserved EXACTLY as the
        # material carries it (matching the v6/v8/general builders). Native
        # skins ship 0.0 — (R·V)^0 = 1, i.e. the specular map applied at full
        # strength everywhere — so 0.0 is a real, intended value, not "empty".
        # The user controls it via the IGB Materials panel / shader; we no
        # longer floor near-zero values to 1.28 (that silently changed the
        # imported look and broke round-trip fidelity).
        _sh = float(mat.get('shininess', 0.0) or 0.0)
        return self._add_obj(MO_MATERIAL, [
            # base Short lives at slot 3 on v4 (vs 2 on v6/v8); native v4 = 1.
            (3, int(mat.get('priority', 1)), 'Short', 2),
            (4, _sh, 'Float', 4),
            (5, tuple(mat.get('diffuse', (1.0, 1.0, 1.0, 1.0))), 'Vec4f', 16),
            (6, tuple(mat.get('ambient', (0.588, 0.588, 0.588, 1.0))), 'Vec4f', 16),
            (7, tuple(mat.get('specular', (0.0, 0.0, 0.0, 0.0))), 'Vec4f', 16),
            (8, tuple(mat.get('emission', (0.0, 0.0, 0.0, 0.0))), 'Vec4f', 16),
            (9, int(mat.get('flags', 0)), 'UnsignedInt', 4)])

    # ---- geometry ----

    def _build_vertex_array(self, mesh, has_uvs, normal_mapped=False):
        n = len(mesh.positions)

        # Normal mapping needs UVs to derive a tangent frame; fall back to the
        # plain format if a normal-mapped mesh somehow lacks UVs.
        normal_mapped = bool(normal_mapped and has_uvs and mesh.uvs)

        pos_data = bytearray(n * 12)
        for i, (x, y, z) in enumerate(mesh.positions):
            struct.pack_into('<fff', pos_data, i * 12, x, y, z)
        pos_mb = self._add_mem(TAG_VTX_STREAM, bytes(pos_data))

        tangents = binormals = None
        if normal_mapped:
            tangents, binormals = _compute_tangent_frame(
                mesh.positions, mesh.normals, mesh.uvs, mesh.indices)

        # slot 1 = DENSE 12B/vert vertex normal in ALL formats. The engine reads
        # slot1 at a 12-byte stride EVEN in the normal-mapped format, so the old
        # [N,N,N]-packed-at-36B layout made vertices 3k+1 / 3k+2 read the PREVIOUS
        # vertex's normal -> 2/3 of verts got the wrong lighting normal. Native
        # 0104's slot1 read at 12B stride is just the dense normal (its
        # over-allocated back two-thirds are unread garbage we don't reproduce).
        # The real per-vertex tangent frame lives in slots 17/18 (see below).
        norm_data = bytearray(n * 12)
        for i in range(n):
            nx, ny, nz = mesh.normals[i] if i < len(mesh.normals) else (0.0, 0.0, 1.0)
            struct.pack_into('<fff', norm_data, i * 12, nx, ny, nz)
        norm_mb = self._add_mem(TAG_VTX_STREAM, bytes(norm_data))

        uv_mb = -1
        if has_uvs and mesh.uvs:
            uv_data = bytearray(n * 8)
            for i, (u, v) in enumerate(mesh.uvs):
                struct.pack_into('<ff', uv_data, i * 8, u, v)
            uv_mb = self._add_mem(TAG_VTX_STREAM, bytes(uv_data))

        tan_mb = bin_mb = -1
        if normal_mapped:
            tan_data = bytearray(n * 12)
            bin_data = bytearray(n * 12)
            for i in range(n):
                struct.pack_into('<fff', tan_data, i * 12, *tangents[i])
                struct.pack_into('<fff', bin_data, i * 12, *binormals[i])
            tan_mb = self._add_mem(TAG_VTX_STREAM, bytes(tan_data))
            bin_mb = self._add_mem(TAG_VTX_STREAM, bytes(bin_data))

        slots = [0xFFFFFFFF] * VTX_FMT_SLOTS
        slots[0] = pos_mb
        slots[1] = norm_mb
        if uv_mb >= 0:
            slots[11] = uv_mb
        if normal_mapped:
            # Native binds slot 17 -> D3DDECLUSAGE_BINORMAL (the +V / dPdv vector)
            # and slot 18 -> D3DDECLUSAGE_TANGENT (the +U / dPdu vector) — the
            # REVERSE of our local tan/bin names. Routing tangent->17, binormal->18
            # transposes the engine's object->tangent basis, so the normal map is
            # lit from the wrong axes on every surface. So: binormal -> slot 17,
            # tangent -> slot 18. (Slot constants name the fixed slot index; only
            # the content routed into each is swapped.)
            slots[VTX_SLOT_TANGENT] = bin_mb     # slot 17 carries BINORMAL (+V)
            slots[VTX_SLOT_BINORMAL] = tan_mb    # slot 18 carries TANGENT  (+U)
        fmt_mb = self._add_mem(TAG_VA_FORMAT,
                               struct.pack('<' + 'I' * VTX_FMT_SLOTS, *slots))

        weights = mesh.blend_weights or [(1.0, 0.0, 0.0, 0.0)] * n
        w_data = bytearray(n * 16)
        for i, (w0, w1, w2, w3) in enumerate(weights):
            struct.pack_into('<ffff', w_data, i * 16, w0, w1, w2, w3)
        w_mb = self._add_mem(TAG_WEIGHTS, bytes(w_data), align=ALIGN_VERTEX)

        bidx = mesh.blend_indices or [(0, 0, 0, 0)] * n
        b_data = bytearray(n * 4)
        for i, (i0, i1, i2, i3) in enumerate(bidx):
            struct.pack_into('BBBB', b_data, i * 4, i0, i1, i2, i3)
        b_mb = self._add_mem(TAG_BLEND_IDX, bytes(b_data))

        if normal_mapped:
            fmt = FMT_NORMAL_MAPPED
        else:
            fmt = FMT_SKINNED_UV if uv_mb >= 0 else FMT_SKINNED
        return self._add_obj(MO_VERTEX_ARRAY_1_1, [
            (3, fmt_mb, 'MemoryRef', 4),
            (4, n, 'UnsignedInt', 4),
            (5, 0, 'UnsignedInt', 4),
            (6, 0, 'UnsignedInt', 4),
            (7, fmt, 'Struct', 4),
            (8, w_mb, 'MemoryRef', 4),
            (9, b_mb, 'MemoryRef', 4),
            (11, -1, 'MemoryRef', 4)])

    def _texstate_for_unit(self, unit):
        """igTextureStateAttr (texturing ON) for a texture stage. slot 5 =
        the texture unit, matching native modder skins; cached per unit."""
        cache = getattr(self, '_texstate_cache', None)
        if cache is None:
            cache = self._texstate_cache = {}
        if unit not in cache:
            cache[unit] = self._add_obj(MO_TEX_STATE, [
                (3, 1, 'Short', 2), (4, 1, 'Bool', 1), (5, int(unit), 'Int', 4)])
        return cache[unit]

    def _get_light_state(self):
        """Lazily create the shared lighting-OFF state (outline sets only)."""
        if self._light_state_idx is None:
            self._light_state_idx = self._add_obj(MO_LIGHTING_STATE, [
                (3, 1, 'Short', 2), (4, 0, 'Bool', 1)])
        return self._light_state_idx

    def _build_unit(self, mesh, material, tex_bind_idx, is_outline,
                    palette_idx, cull_idx, texstate_on_idx,
                    texstate_off_idx, vbs_idx, unit_name):
        """group -> BMS -> AttrSet -> Geometry chain for one submesh.

        tex_bind_idx may be a single bind idx (CLUT path, unit 0) or a list of
        (bind_idx, unit) tuples (normal-map path: diffuse(0)/normal(1)/
        specular(2)/gloss(5)).
        """
        # Normalize to a list of (bind_idx, unit) tuples.
        if tex_bind_idx is None:
            tex_binds = []
        elif (isinstance(tex_bind_idx, (list, tuple)) and tex_bind_idx
              and isinstance(tex_bind_idx[0], (list, tuple))):
            tex_binds = [(b, u) for (b, u) in tex_bind_idx if b is not None]
        elif isinstance(tex_bind_idx, (list, tuple)):
            # Legacy: bare list of bind idxs -> assume sequential unit numbers.
            tex_binds = [(b, i) for i, b in enumerate(tex_bind_idx)
                         if b is not None]
        else:
            tex_binds = [(tex_bind_idx, 0)]   # single CLUT diffuse bind
        # Normal mapping active when this textured unit carries a 2nd (normal)
        # bind — emit the tangent-frame vertex format to match.
        normal_mapped = (getattr(self, '_use_normal_maps', False)
                         and not is_outline and len(tex_binds) >= 2)

        has_uvs = bool(mesh.uvs) and not is_outline
        va_idx = self._build_vertex_array(mesh, has_uvs,
                                          normal_mapped=normal_mapped)

        idx_data = bytearray(len(mesh.indices) * 2)
        for i, v in enumerate(mesh.indices):
            struct.pack_into('<H', idx_data, i * 2, v)
        idx_mb = self._add_mem(TAG_INDEX_DATA, bytes(idx_data))
        ia_idx = self._add_obj(MO_INDEX_ARRAY, [
            (3, idx_mb, 'MemoryRef', 4),
            (4, len(mesh.indices), 'UnsignedInt', 4),
            (5, 0, 'Enum', 4),
            (6, 0, 'UnsignedInt', 4)])

        geom_attr_idx = self._add_obj(MO_GEOM_ATTR_1_5, [
            (3, 1, 'Short', 2),
            (4, va_idx, 'ObjectRef', 4),
            (5, ia_idx, 'ObjectRef', 4),
            (6, 3, 'Enum', 4),                       # TRIANGLE LIST
            (7, len(mesh.indices) // 3, 'UnsignedInt', 4),
            (8, 0, 'UnsignedInt', 4),
            (9, -1, 'ObjectRef', 4),
            (10, 0, 'Int', 4),
            (11, -1, 'ObjectRef', 4),
            (12, -1, 'ObjectRef', 4),
            (13, -1, 'ObjectRef', 4)])

        geom_al = self._ref_list(MO_ATTR_LIST, [geom_attr_idx])
        geom_nl = self._empty_list(MO_NODE_LIST)
        geom_idx = self._add_obj(MO_GEOMETRY, [
            (3, unit_name, 'String', 4),
            (4, -1, 'ObjectRef', 4),
            (6, 16, 'Int', 4),
            (7, geom_nl, 'ObjectRef', 4),
            (8, geom_al, 'ObjectRef', 4),
            (9, 1, 'Bool', 1)])

        # 0103: main sets carry WHITE vertex color + texturing ON;
        # outline sets carry BLACK color + texturing OFF. The IGB Materials
        # panel can override color tint, lighting, cull face, blend, alpha.
        textured = not is_outline and len(tex_binds) > 0
        if textured:
            color_val = tuple(material.get('color_attr', (1.0, 1.0, 1.0, 1.0)))
        else:
            color_val = (0.0, 0.0, 0.0, 1.0)
        color_idx = self._add_obj(MO_COLOR, [
            (3, 1, 'Short', 2),
            (4, color_val, 'Vec4f', 16)])
        mat_idx = self._build_material(material)

        # Cull-face. Native MUA modder skins emit NO cull-face attr on the main
        # textured mesh — the engine's default back-face cull is correct. We used
        # to force the shared attr with mode 0 (= cull FRONT), which removes the
        # VISIBLE faces and lights the inside-out back faces -> "dark shadows in
        # the wrong locations" on normal-mapped skins. Only emit a cull attr on
        # the main mesh when the material explicitly asks (default mode BACK=1).
        # Outlines keep the shared front-cull (param cull_idx) for the hull.
        mat_cull_idx = None
        if 'cull_face_enabled' in material:
            mat_cull_idx = self._add_obj(MO_CULL_FACE, [
                (3, 1, 'Short', 2),
                (4, int(material['cull_face_enabled']), 'Bool', 1),
                (5, material.get('cull_face_mode', 1), 'Enum', 4)])
        extra = self._build_render_state_attrs(material)
        if textured and 'lighting_enabled' in material:
            extra.append(self._add_obj(MO_LIGHTING_STATE, [
                (3, 1, 'Short', 2),
                (4, int(material['lighting_enabled']), 'Bool', 1)]))

        if textured:
            # Each texture bind is followed by its texturing-ON state. CRITICAL:
            # the state's stage (slot 5) MUST match the bind's texture unit —
            # native 3ds Max modder skins emit one state per unit (stage 0/1/2/
            # 5...). A single shared stage-0 state left the normal/specular/gloss
            # units (1/2/5) with no enabling state, rendering the model BLACK
            # in-game. Order stays diffuse, normal, specular, gloss.
            tex_pairs = []
            for bind_idx, unit in tex_binds:
                tex_pairs += [bind_idx, self._texstate_for_unit(unit)]
            head = [mat_cull_idx] if mat_cull_idx is not None else []
            attrs = head + [color_idx, mat_idx] + extra + tex_pairs
        else:
            attrs = ([cull_idx, self._get_light_state(), color_idx, mat_idx] + extra
                     + [texstate_off_idx])
        set_al = self._ref_list(MO_ATTR_LIST, attrs)
        set_nl = self._ref_list(MO_NODE_LIST, [geom_idx])
        attrset_idx = self._add_obj(MO_ATTR_SET, [
            (3, '', 'String', 4),
            (4, -1, 'ObjectRef', 4),
            (6, 0, 'Int', 4),
            (7, set_nl, 'ObjectRef', 4),
            (8, set_al, 'ObjectRef', 4),
            (9, 0, 'Bool', 1)])

        bms_al = self._ref_list(MO_ATTR_LIST, [vbs_idx])
        bms_nl = self._ref_list(MO_NODE_LIST, [attrset_idx])
        bms_idx = self._add_obj(MO_BMS, [
            (3, '', 'String', 4),
            (4, -1, 'ObjectRef', 4),
            (6, 0, 'Int', 4),
            (7, bms_nl, 'ObjectRef', 4),
            (8, bms_al, 'ObjectRef', 4),
            (9, 0, 'Bool', 1),
            (10, palette_idx, 'ObjectRef', 4),
            (11, _IDENTITY44, 'Matrix44f', 64),
            (12, _IDENTITY44, 'Matrix44f', 64)])

        group_nl = self._ref_list(MO_NODE_LIST, [bms_idx])
        return self._add_obj(MO_GROUP, [
            (3, '', 'String', 4),
            (4, -1, 'ObjectRef', 4),
            (6, 0, 'Int', 4),
            (7, group_nl, 'ObjectRef', 4)])

    # ---- skeleton ----

    def _build_skeleton(self, skeleton_data, bms_palette):
        bones = skeleton_data['bones']
        n = len(bones)

        bi_idxs = []
        for b in bones:
            parent = b.get('parent_idx', b.get('parent_index', -1))
            bi_idxs.append(self._add_obj(MO_BONE_INFO, [
                (3, b['name'], 'String', 4),
                (4, parent, 'Int', 4),
                (5, b.get('bm_idx', b.get('bm_index', -1)), 'Int', 4),
                (6, b.get('flags', 0), 'Int', 4)]))
        bil_idx = self._ref_list(MO_BONE_INFO_LIST, bi_idxs)

        trans_data = bytearray(n * 12)
        for i, b in enumerate(bones):
            t = b.get('translation', b.get('trans', (0.0, 0.0, 0.0)))
            struct.pack_into('<fff', trans_data, i * 12, *t)
        trans_mb = self._add_mem(TAG_SKEL_TRANS, bytes(trans_data))

        bm_count = len(bms_palette)
        inv_data = bytearray(bm_count * 64)
        # blend matrix i corresponds to the bone whose bm index == i
        by_bm = {}
        for b in bones:
            bm = b.get('bm_idx', b.get('bm_index', -1))
            if bm >= 0:
                by_bm[bm] = b
        for bm in range(bm_count):
            b = by_bm.get(bm)
            ijm = b.get('inv_joint_matrix') if b else None
            mat = tuple(ijm) if ijm else _IDENTITY44
            struct.pack_into('<16f', inv_data, bm * 64, *mat)
        inv_mb = self._add_mem(TAG_SKEL_MATS, bytes(inv_data))

        return self._add_obj(MO_SKELETON, [
            (3, 'igActor01Skeleton', 'String', 4),
            (4, trans_mb, 'MemoryRef', 4),
            (5, bil_idx, 'ObjectRef', 4),
            (6, inv_mb, 'MemoryRef', 4),
            (7, bm_count, 'Int', 4)])

    def _build_mvmbs_tree(self, bones):
        """Bone hierarchy as nested igModelViewMatrixBoneSelect nodes."""
        children = {}
        root = 0
        for i, b in enumerate(bones):
            p = b.get('parent_idx', b.get('parent_index', -1))
            if p < 0:
                root = i
            else:
                children.setdefault(p, []).append(i)

        def emit(i):
            kid_idxs = [emit(c) for c in children.get(i, [])]
            nl = self._ref_list(MO_NODE_LIST, kid_idxs)
            return self._add_obj(MO_MVMBS, [
                (3, bones[i]['name'], 'String', 4),
                (4, -1, 'ObjectRef', 4),
                (6, 0, 'Int', 4),
                (7, nl, 'ObjectRef', 4),
                (8, i, 'Int', 4)])

        return emit(root)

    # ---- animation ----

    def _build_bindpose_animation(self, skeleton_idx, bones, locals_):
        n = len(bones)
        idmap = struct.pack('<' + 'i' * n, *range(n))
        map_mb = self._add_mem(TAG_INT_LIST, idmap)
        binding_idx = self._add_obj(MO_BINDING, [
            (3, skeleton_idx, 'ObjectRef', 4),
            (4, map_mb, 'MemoryRef', 4),
            (5, n, 'Int', 4)])
        binding_list_idx = self._ref_list(MO_BINDING_LIST, [binding_idx])

        track_idxs = []
        for bone, (quat, _trans) in zip(bones, locals_):
            track_idxs.append(self._add_obj(MO_TRACK, [
                (3, bone['name'], 'String', 4),
                (4, -1, 'ObjectRef', 4),
                (5, quat, 'Vec4f', 16)]))
        track_list_idx = self._ref_list(MO_TRACK_LIST, track_idxs)
        trans_def_idx = self._empty_list(MO_TRANS_DEF_LIST)

        return self._add_obj(MO_ANIMATION, [
            (3, 'igActor01_Animation01', 'String', 4),
            (4, 0, 'Int', 4),
            (5, binding_list_idx, 'ObjectRef', 4),
            (6, track_list_idx, 'ObjectRef', 4),
            (7, trans_def_idx, 'ObjectRef', 4),
            (8, 0, 'Long', 8),
            (9, 0, 'Long', 8),
            (10, 0, 'Long', 8),
            (11, -1, 'ObjectRef', 4)])

    def _build_animation_database(self, skeleton_idx, skin_idx, anim_idx,
                                  combiner_idx=None):
        skel_list = self._ref_list(MO_SKELETON_LIST, [skeleton_idx])
        anim_list = self._ref_list(MO_ANIMATION_LIST, [anim_idx])
        skin_list = self._ref_list(MO_SKIN_LIST, [skin_idx])
        appear_list = self._empty_list(MO_APPEARANCE_LIST)
        comb_list = (self._ref_list(MO_COMBINER_LIST, [combiner_idx])
                     if combiner_idx is not None
                     else self._empty_list(MO_COMBINER_LIST))
        return self._add_obj(MO_ANIM_DB, [
            (3, 'igActor01_Animation01DB', 'String', 4),
            (5, 1, 'Bool', 1),
            (6, skel_list, 'ObjectRef', 4),
            (7, anim_list, 'ObjectRef', 4),
            (8, skin_list, 'ObjectRef', 4),
            (9, appear_list, 'ObjectRef', 4),
            (10, comb_list, 'ObjectRef', 4)])

    # ---- combiner / actor graph (Cable layouts, FULL mode) ----

    def _build_actor_graph(self, skeleton_idx, skeleton_data, skin_idx,
                           anim_idx, bms_palette, locals_):
        bones = skeleton_data['bones']
        n = len(bones)

        # Field layouts below mirror a WORKING MUA combiner skin verbatim
        # (X-Men Evolution 1230x-JEAN, F:\Downloads\Skins\MUA ONLY). The
        # previous layout (derived from the v6 graph +1) had the combiner /
        # actor / actorInfo / appearance fields misaligned, which is why
        # V4_FULL crashed. Buffer sizes: quat = n*16, world-bind = n*64,
        # palette = joints*64; the donor uses a distinct block per slot.
        state_idx = self._add_obj(MO_ANIM_STATE, [
            (3, anim_idx, 'ObjectRef', 4),
            (4, 0, 'Enum', 4),
            (5, 4, 'Enum', 4),
            (6, 0, 'Enum', 4),
            (7, -1, 'ObjectRef', 4),
            (8, 1, 'Bool', 1),                 # donor: 1
            (9, 0.0, 'Float', 4),
            (10, 0, 'Long', 8),
            (11, 0, 'Long', 8),
            (12, 1.0, 'Float', 4),
            (13, 0, 'Long', 8),
            (14, 0, 'Long', 8),
            (15, 0.0, 'Float', 4),
            (16, 0.0, 'Float', 4),
            (17, 0, 'Long', 8),
            (18, 0, 'Long', 8)])
        state_list_idx = self._ref_list(MO_ANIM_STATE_LIST, [state_idx])

        # one igAnimationCombinerBoneInfo per bone, each in a 1-entry list
        bi_list_idxs = []
        for quat, trans in locals_:
            bi_idx = self._add_obj(MO_CBONE_INFO, [
                (3, state_idx, 'ObjectRef', 4),
                (4, -1, 'ObjectRef', 4),
                (5, quat, 'Vec4f', 16),
                (6, trans, 'Vec3f', 12),
                (7, 0, 'Int', 4),
                (8, 0, 'Bool', 1)])
            bi_list_idxs.append(self._ref_list(MO_CBONE_INFO_LIST, [bi_idx]))
        bill_idx = self._ref_list(MO_CBONE_INFO_LIST_LIST, bi_list_idxs)

        # combiner int list: one entry per bone (all 0 -> all use state 0)
        comb_int_idx = self._build_int_list([0] * n)
        modifier_list_idx = self._empty_list(MO_ANIM_MODIFIER_LIST)

        # buffers — distinct blocks per slot, matching the donor
        quat_bytes = b''.join(struct.pack('<4f', *q) for q, _t in locals_)
        world_bytes = b''.join(struct.pack('<16f', *_world_bind_matrix(b))
                               for b in bones)
        n_pal = max(len(bms_palette), 1)
        pal_bytes = struct.pack('<16f', *_IDENTITY44) * n_pal
        quat_mb = self._add_mem(TAG_INT_LIST, quat_bytes)
        comb_world9 = self._add_mem(TAG_INT_LIST, world_bytes)
        comb_world13 = self._add_mem(TAG_INT_LIST, world_bytes)
        comb_pal14 = self._add_mem(TAG_INT_LIST, pal_bytes)
        actor_world9 = self._add_mem(TAG_INT_LIST, world_bytes)
        actor_pal10 = self._add_mem(TAG_INT_LIST, pal_bytes)

        # igAnimationCombiner (donor: 4=skeleton, 5=boneInfoListList,
        # 6=intList, 7=stateList, 8=quat, 9/13=world-bind, 14=palette)
        combiner_idx = self._add_obj(MO_COMBINER, [
            (3, 'combiner_igActor01', 'String', 4),
            (4, skeleton_idx, 'ObjectRef', 4),
            (5, bill_idx, 'ObjectRef', 4),
            (6, comb_int_idx, 'ObjectRef', 4),
            (7, state_list_idx, 'ObjectRef', 4),
            (8, quat_mb, 'MemoryRef', 4),
            (9, comb_world9, 'MemoryRef', 4),
            (10, 0, 'Long', 8),
            (11, 0, 'Bool', 1),               # donor: 0
            (13, comb_world13, 'MemoryRef', 4),
            (14, comb_pal14, 'MemoryRef', 4)])
        combiner_list_idx = self._ref_list(MO_COMBINER_LIST, [combiner_idx])

        # igAppearance (donor: 4=skin, 5=skinList, 6=MVMBSList,
        # 7=stringObjList, 8=nodeList — all empty lists except the skin ref)
        appearance_idx = self._add_obj(MO_APPEARANCE, [
            (3, 'appearance_igActor01', 'String', 4),
            (4, skin_idx, 'ObjectRef', 4),
            (5, self._empty_list(MO_SKIN_LIST), 'ObjectRef', 4),
            (6, self._empty_list(MO_MVMBS_LIST), 'ObjectRef', 4),
            (7, self._empty_list(MO_STRING_OBJ_LIST), 'ObjectRef', 4),
            (8, self._empty_list(MO_NODE_LIST), 'ObjectRef', 4)])
        appearance_list_idx = self._ref_list(MO_APPEARANCE_LIST,
                                             [appearance_idx])

        # igActor (donor: the hub — 8=combiner, 9=world-bind, 10=palette,
        # 11=appearance, 13=modifierList, 14=identity)
        actor_nl = self._empty_list(MO_NODE_LIST)
        actor_idx = self._add_obj(MO_ACTOR, [
            (3, 'igActor01', 'String', 4),
            (4, -1, 'ObjectRef', 4),
            (6, 0, 'Int', 4),
            (7, actor_nl, 'ObjectRef', 4),
            (8, combiner_idx, 'ObjectRef', 4),
            (9, actor_world9, 'MemoryRef', 4),
            (10, actor_pal10, 'MemoryRef', 4),
            (11, appearance_idx, 'ObjectRef', 4),
            (12, -1, 'ObjectRef', 4),
            (13, modifier_list_idx, 'ObjectRef', 4),
            (14, _IDENTITY44, 'Matrix44f', 64)])
        actor_list_idx = self._ref_list(MO_ACTOR_LIST, [actor_idx])

        # igActorInfo (donor: 6=-1, 7=actorList, 8=animDB, 9=combinerList,
        # 10=appearanceList). animDB ref is wired in build_skin at slot 8.
        actor_info_idx = self._add_obj(MO_ACTOR_INFO, [
            (3, 'igActor01', 'String', 4),
            (5, 1, 'Bool', 1),
            (6, -1, 'ObjectRef', 4),
            (7, actor_list_idx, 'ObjectRef', 4),
            (8, -1, 'ObjectRef', 4),          # animationDatabase (wired later)
            (9, combiner_list_idx, 'ObjectRef', 4),
            (10, appearance_list_idx, 'ObjectRef', 4)])

        return combiner_idx, actor_info_idx

    # ------------------------------------------------------------------
    # Writer assembly
    # ------------------------------------------------------------------

    def _finalize(self, info_list_idx):
        writer = IGBWriter()
        writer.version = 4
        writer.endian = '<'
        writer.has_info = True
        writer.has_external = False
        writer.shared_entries = False
        writer.has_memory_pool_names = False

        writer.meta_fields = [MetaFieldDef(n, ma, mi)
                              for n, ma, mi in V4_META_FIELDS]

        metas = (V4_META_OBJECTS if self._mode == 'FULL'
                 else V4_META_OBJECTS[:N_ANIM_METAS])
        name_to_idx = {t[0]: i for i, t in enumerate(metas)}
        writer.meta_objects = []
        for name, major, minor, parent_name, slot_count, fields in metas:
            fdefs = []
            for short, slot, size in fields:
                # type_index = position of the meta FIELD with this short name
                ti = next(i for i, (fn, _a, _b) in enumerate(V4_META_FIELDS)
                          if _FIELD_SHORT[fn] == short)
                fd = MetaObjectFieldDef(ti, slot, size)
                fd.short_name = short.encode('ascii')
                fdefs.append(fd)
            parent_idx = name_to_idx.get(parent_name, -1) if parent_name else -1
            writer.meta_objects.append(MetaObjectDef(
                name, major, minor, fdefs, parent_idx, slot_count))

        writer.alignment_data = V4_ALIGNMENT
        writer.external_dirs = []
        writer.memory_pool_names = []

        entries = []
        index_map = []
        writer.objects = []
        writer.ref_info = []

        for i, item in enumerate(self._obj_list):
            if item[0] == 'obj':
                _kind, mo_idx, fields = item
                entries.append(EntryDef(MO_OBJ_ENTRY, [0, mo_idx]))
                raw_fields = []
                for slot, val, short, size in fields:
                    raw_fields.append(
                        (slot, val, ObjectFieldDef(slot, short, size)))
                writer.objects.append(ObjectDef(mo_idx, raw_fields))
                writer.ref_info.append({
                    'is_object': True,
                    'type_index': mo_idx,
                    'type_name': metas[mo_idx][0].encode('ascii'),
                    'mem_size': 0, 'ref_counted': 1,
                    'align_type_idx': -1, 'mem_pool_handle': -1,
                })
            else:
                _kind, tag, data, align = item
                entries.append(EntryDef(MO_MEM_ENTRY,
                                        [0, len(data), tag, 1, align]))
                writer.objects.append(MemoryBlockDef(data))
                writer.ref_info.append({
                    'is_object': False,
                    'type_index': tag,
                    'type_name': metas[tag][0].encode('ascii'),
                    'mem_size': len(data), 'ref_counted': 1,
                    'align_type_idx': align, 'mem_pool_handle': -1,
                })
            index_map.append(i)

        writer.entries = entries
        writer.index_map = index_map
        writer.info_list_index = info_list_idx
        return writer
