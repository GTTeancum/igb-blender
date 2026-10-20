"""From-scratch IGB builder for skin (actor) files.

Builds a complete skin IGB file without any template, following the same
pattern as igb_builder.py (map export). The meta-object registry matches
exactly what vanilla 0601.igb uses — 57 meta-objects with indices
specific to skin files (dumped from the actual game file).

Uses Pattern B (Cable 11501 / 3ds Max) scene graph structure where each
body/segment unit has its own igBlendMatrixSelect. This is confirmed
working with custom skins and segment toggling in-game.

Multi-texture support: Each mesh object can have its own material and
texture. The builder groups meshes by texture_name, building a separate
igTextureBindAttr + igMaterialAttr per unique texture. Shared render
state (igColorAttr, igTextureStateAttr) is still shared across all units.

Skin file structure:
    igInfoList → [igAnimationDatabase]
    igAnimationDatabase → igSkeletonList → igSkeleton
    igSkin._skinnedGraph → igGroup "igActor01"
        → igGroup "{name}" → BMS → AttrSet(tex1) → Geometry (body)
        → igGroup "{name}_outline" → BMS → AttrSet → Geometry (body outline)
        → igSegment "seg" → igGroup → BMS → AttrSet(tex2) → Geometry (segments)
        → igSegment "seg_outline" → igGroup → BMS → AttrSet → Geometry

Usage:
    builder = SkinBuilder()
    writer = builder.build_skin(submeshes, skeleton_data, bms_palette)
    writer.write("output.igb")
"""

import json
import struct
from ..igb_format.igb_writer import (
    IGBWriter, MetaFieldDef, MetaObjectDef, MetaObjectFieldDef,
    EntryDef, ObjectDef, ObjectFieldDef, MemoryBlockDef,
)
from .mesh_extractor import triangles_to_strip


# ============================================================================
# Skin file type registry — 47 meta-fields (same as map files)
# ============================================================================

META_FIELDS = [
    ("igObjectRefMetaField", 1, 0),         # 0
    ("igIntMetaField", 1, 0),               # 1
    ("igBoolMetaField", 1, 0),              # 2
    ("igCharMetaField", 1, 0),              # 3
    ("igStringMetaField", 1, 0),            # 4
    ("igRawRefMetaField", 1, 0),            # 5
    ("igEnumMetaField", 1, 0),              # 6
    ("igMemoryRefMetaField", 1, 0),         # 7
    ("igUnsignedCharMetaField", 1, 0),      # 8
    ("igUnsignedIntMetaField", 1, 0),       # 9
    ("igUnsignedShortMetaField", 1, 0),     # 10
    ("igStructMetaField", 1, 0),            # 11
    ("igLongMetaField", 1, 0),              # 12
    ("igUnsignedLongMetaField", 1, 0),      # 13
    ("igIntArrayMetaField", 1, 0),          # 14
    ("igCharArrayMetaField", 1, 0),         # 15
    ("igFloatMetaField", 1, 0),             # 16
    ("igShortMetaField", 1, 0),             # 17
    ("igVirtualCFuncMetaField", 1, 0),      # 18
    ("igUnsignedShortArrayMetaField", 1, 0),# 19
    ("igUnsignedLongArrayMetaField", 1, 0), # 20
    ("igUnsignedIntArrayMetaField", 1, 0),  # 21
    ("igUnsignedCharArrayMetaField", 1, 0), # 22
    ("igStringArrayMetaField", 1, 0),       # 23
    ("igShortArrayMetaField", 1, 0),        # 24
    ("igRawRefArrayMetaField", 1, 0),       # 25
    ("igObjectRefArrayMetaField", 1, 0),    # 26
    ("igMemoryRefArrayMetaField", 1, 0),    # 27
    ("igLongArrayMetaField", 1, 0),         # 28
    ("igFloatArrayMetaField", 1, 0),        # 29
    ("igEnumArrayMetaField", 1, 0),         # 30
    ("igDoubleMetaField", 1, 0),            # 31
    ("igDoubleArrayMetaField", 1, 0),       # 32
    ("igDependencyMetaField", 1, 0),        # 33
    ("igBoolArrayMetaField", 1, 0),         # 34
    ("igMemoryDescriptorMetaField", 1, 0),  # 35
    ("igVec3fMetaField", 1, 0),             # 36
    ("igVec2fMetaField", 1, 0),             # 37
    ("igVec4fMetaField", 1, 0),             # 38
    ("igVec4ucMetaField", 1, 0),            # 39
    ("igMatrix44fMetaField", 1, 0),         # 40
    ("igTrackedElementMetaField", 1, 0),    # 41
    ("igVertexBlendShaderCapsInfoMetaField", 1, 0),  # 42
    ("igVec4fArrayMetaField", 1, 0),        # 43
    ("igItemDataBaseField", 1, 0),          # 44
    ("igVec3ucMetaField", 1, 0),            # 45
    ("igInterfaceDeclarationField", 1, 0),  # 46
]

# Field type indices
_ObjRef = 0
_Int = 1
_Bool = 2
_String = 4
_Enum = 6
_MemRef = 7
_UChar = 8
_UInt = 9
_Struct = 11
_Float = 16
_Short = 17
_Vec3f = 36
_Vec4f = 38
_Matrix44f = 40
_Long = 12


# ============================================================================
# Skin file meta-object registry — 57 types (MO[0]..MO[56])
# Matches EXACTLY what vanilla 0601.igb uses (dumped from game file).
# DIFFERENT from map file indices! Do NOT mix with igb_builder.py MO_* constants.
# ============================================================================

# Each: (name, major, minor, parent_index, slot_count, [(type_idx, slot, size), ...])
SKIN_META_OBJECTS = [
    # [0] igObject
    ("igObject", 1, 0, -1, 2, []),
    # [1] igNamedObject
    ("igNamedObject", 1, 0, 0, 3, [(_String, 2, 4)]),
    # [2] igDirEntry
    ("igDirEntry", 1, 0, 1, 7, [(_String, 2, 4)]),
    # [3] igObjectDirEntry
    ("igObjectDirEntry", 1, 0, 2, 13, [(_String, 2, 4), (_Int, 11, 4), (_Int, 12, 4)]),
    # [4] igMemoryDirEntry
    ("igMemoryDirEntry", 1, 0, 2, 14,
     [(_String, 2, 4), (_Int, 7, 4), (_Int, 10, 4), (_Bool, 11, 1), (_Int, 12, 4), (_Int, 13, 4)]),
    # [5] igExternalDirEntry
    ("igExternalDirEntry", 1, 0, 2, 11,
     [(_String, 2, 4), (_String, 7, 4), (_String, 8, 4), (_Int, 9, 4)]),
    # [6] igExternalImageEntry
    ("igExternalImageEntry", 1, 0, 5, 11,
     [(_String, 2, 4), (_String, 7, 4), (_String, 8, 4), (_Int, 9, 4)]),
    # [7] igExternalIndexedEntry
    ("igExternalIndexedEntry", 1, 0, 2, 13,
     [(_String, 2, 4), (_Int, 7, 4), (_Int, 8, 4), (_Int, 10, 4), (_Int, 12, 4)]),
    # [8] igExternalInfoEntry
    ("igExternalInfoEntry", 1, 0, 2, 10,
     [(_String, 2, 4), (_String, 7, 4), (_Int, 8, 4), (_String, 9, 4)]),
    # [9] igInfo
    ("igInfo", 1, 0, 1, 5, [(_String, 2, 4), (_Bool, 4, 1)]),
    # [10] igAnimationDatabase
    ("igAnimationDatabase", 1, 0, 9, 10,
     [(_String, 2, 4), (_Bool, 4, 1), (_ObjRef, 5, 4), (_ObjRef, 6, 4),
      (_ObjRef, 7, 4), (_ObjRef, 8, 4), (_ObjRef, 9, 4)]),
    # [11] igAttr
    ("igAttr", 1, 0, 0, 4, [(_Short, 2, 2)]),
    # [12] igVisualAttribute
    ("igVisualAttribute", 1, 0, 11, 4, [(_Short, 2, 2)]),
    # [13] igGeometryAttr
    ("igGeometryAttr", 1, 0, 12, 13,
     [(_Short, 2, 2), (_ObjRef, 4, 4), (_ObjRef, 5, 4), (_Enum, 6, 4),
      (_UInt, 7, 4), (_UInt, 8, 4), (_ObjRef, 9, 4), (_Int, 10, 4),
      (_ObjRef, 11, 4), (_ObjRef, 12, 4)]),
    # [14] igGeometryAttr1_5
    ("igGeometryAttr1_5", 1, 0, 13, 14,
     [(_Short, 2, 2), (_ObjRef, 4, 4), (_ObjRef, 5, 4), (_Enum, 6, 4),
      (_UInt, 7, 4), (_UInt, 8, 4), (_ObjRef, 9, 4), (_Int, 10, 4),
      (_ObjRef, 11, 4), (_ObjRef, 12, 4), (_ObjRef, 13, 4)]),
    # [15] igDataList
    ("igDataList", 1, 0, 0, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [16] igObjectList
    ("igObjectList", 1, 0, 15, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [17] igSkeletonList
    ("igSkeletonList", 1, 0, 16, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [18] igPrimLengthArray
    ("igPrimLengthArray", 1, 0, 0, 5,
     [(_MemRef, 2, 4), (_UInt, 3, 4), (_UInt, 4, 4)]),
    # [19] igPrimLengthArray1_1
    ("igPrimLengthArray1_1", 1, 0, 18, 5,
     [(_MemRef, 2, 4), (_UInt, 3, 4), (_UInt, 4, 4)]),
    # [20] igNode
    ("igNode", 1, 0, 1, 7,
     [(_String, 2, 4), (_ObjRef, 3, 4), (_Int, 5, 4)]),
    # [21] igGroup
    ("igGroup", 1, 0, 20, 8,
     [(_String, 2, 4), (_ObjRef, 3, 4), (_Int, 5, 4), (_ObjRef, 7, 4)]),
    # [22] igAttrSet
    ("igAttrSet", 1, 0, 21, 10,
     [(_String, 2, 4), (_ObjRef, 3, 4), (_Int, 5, 4), (_ObjRef, 7, 4),
      (_ObjRef, 8, 4), (_Bool, 9, 1)]),
    # [23] igOverrideAttrSet
    ("igOverrideAttrSet", 1, 0, 22, 10,
     [(_String, 2, 4), (_ObjRef, 3, 4), (_Int, 5, 4), (_ObjRef, 7, 4),
      (_ObjRef, 8, 4), (_Bool, 9, 1)]),
    # [24] igVertexArray
    ("igVertexArray", 1, 0, 0, 6,
     [(_MemRef, 2, 4), (_UInt, 3, 4), (_UInt, 4, 4), (_UInt, 5, 4)]),
    # [25] igVertexArray1_1
    ("igVertexArray1_1", 1, 0, 24, 12,
     [(_MemRef, 2, 4), (_UInt, 3, 4), (_UInt, 4, 4), (_UInt, 5, 4),
      (_Struct, 6, 4), (_MemRef, 7, 4), (_MemRef, 8, 4), (_MemRef, 10, 4)]),
    # [26] igImage
    ("igImage", 1, 0, 0, 23,
     [(_UInt, 2, 4), (_UInt, 3, 4), (_UInt, 4, 4), (_UInt, 5, 4),
      (_UInt, 6, 4), (_UInt, 7, 4), (_UInt, 8, 4), (_UInt, 9, 4), (_UInt, 10, 4),
      (_Enum, 11, 4), (_Int, 12, 4), (_MemRef, 13, 4), (_MemRef, 14, 4),
      (_Bool, 15, 1), (_UInt, 16, 4), (_ObjRef, 17, 4), (_UInt, 18, 4),
      (_Int, 19, 4), (_Bool, 20, 1), (_UInt, 21, 4), (_String, 22, 4)]),
    # [27] igAlphaFunctionAttr
    ("igAlphaFunctionAttr", 1, 0, 12, 6,
     [(_Short, 2, 2), (_Enum, 4, 4), (_Float, 5, 4)]),
    # [28] igAnimationList
    ("igAnimationList", 1, 0, 16, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [29] igSkinList
    ("igSkinList", 1, 0, 16, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [30] igAppearanceList
    ("igAppearanceList", 1, 0, 16, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [31] igAnimationCombinerList
    ("igAnimationCombinerList", 1, 0, 16, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [32] igVolume
    ("igVolume", 1, 0, 0, 2, []),
    # [33] igAABox
    ("igAABox", 1, 0, 32, 4, [(_Vec3f, 2, 12), (_Vec3f, 3, 12)]),
    # [34] igSkin
    ("igSkin", 1, 0, 1, 5,
     [(_String, 2, 4), (_ObjRef, 3, 4), (_ObjRef, 4, 4)]),
    # [35] igSkeletonBoneInfo
    ("igSkeletonBoneInfo", 1, 0, 1, 6,
     [(_String, 2, 4), (_Int, 3, 4), (_Int, 4, 4), (_Int, 5, 4)]),
    # [36] igAnimationHierarchy
    ("igAnimationHierarchy", 1, 0, 1, 4,
     [(_String, 2, 4), (_MemRef, 3, 4)]),
    # [37] igSkeleton
    ("igSkeleton", 1, 0, 36, 7,
     [(_String, 2, 4), (_MemRef, 3, 4), (_ObjRef, 4, 4), (_MemRef, 5, 4), (_Int, 6, 4)]),
    # [38] igSkeletonBoneInfoList
    ("igSkeletonBoneInfoList", 1, 0, 16, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [39] igNodeList
    ("igNodeList", 1, 0, 16, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [40] igAttrList
    ("igAttrList", 1, 0, 16, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [41] igTextureBindAttr
    ("igTextureBindAttr", 1, 0, 12, 6,
     [(_Short, 2, 2), (_ObjRef, 4, 4), (_Int, 5, 4)]),
    # [42] igImageMipMapList
    ("igImageMipMapList", 1, 0, 16, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [43] igLightingStateAttr
    ("igLightingStateAttr", 1, 0, 12, 5, [(_Short, 2, 2), (_Bool, 4, 1)]),
    # [44] igTextureAttr
    ("igTextureAttr", 1, 0, 12, 19,
     [(_Short, 2, 2), (_UInt, 4, 4), (_Enum, 5, 4), (_Enum, 6, 4),
      (_Enum, 7, 4), (_Enum, 8, 4), (_Enum, 10, 4), (_Enum, 11, 4),
      (_ObjRef, 12, 4), (_Bool, 13, 1), (_ObjRef, 14, 4), (_Int, 15, 4),
      (_ObjRef, 16, 4)]),
    # [45] igCullFaceAttr
    ("igCullFaceAttr", 1, 0, 12, 6,
     [(_Short, 2, 2), (_Bool, 4, 1), (_Enum, 5, 4)]),
    # [46] igSegment
    ("igSegment", 1, 0, 21, 8,
     [(_String, 2, 4), (_ObjRef, 3, 4), (_Int, 5, 4), (_ObjRef, 7, 4)]),
    # [47] igIndexArray
    ("igIndexArray", 1, 0, 0, 7,
     [(_MemRef, 2, 4), (_UInt, 3, 4), (_Enum, 4, 4), (_UInt, 5, 4)]),
    # [48] igColorAttr
    ("igColorAttr", 1, 0, 12, 6, [(_Short, 2, 2), (_Vec4f, 4, 16)]),
    # [49] igGeometry
    ("igGeometry", 1, 0, 22, 11,
     [(_String, 2, 4), (_ObjRef, 3, 4), (_Int, 5, 4), (_ObjRef, 7, 4),
      (_ObjRef, 8, 4), (_Bool, 9, 1)]),
    # [50] igAlphaStateAttr
    ("igAlphaStateAttr", 1, 0, 12, 5, [(_Short, 2, 2), (_Bool, 4, 1)]),
    # [51] igTextureStateAttr
    ("igTextureStateAttr", 1, 0, 12, 6,
     [(_Short, 2, 2), (_Bool, 4, 1), (_Int, 5, 4)]),
    # [52] igMaterialAttr
    ("igMaterialAttr", 1, 0, 12, 10,
     [(_Short, 2, 2), (_Float, 4, 4), (_Vec4f, 5, 16), (_Vec4f, 6, 16),
      (_Vec4f, 7, 16), (_Vec4f, 8, 16), (_UInt, 9, 4)]),
    # [53] igVertexBlendStateAttr
    ("igVertexBlendStateAttr", 1, 0, 12, 5, [(_Short, 2, 2), (_Bool, 4, 1)]),
    # [54] igInfoList
    ("igInfoList", 1, 0, 16, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [55] igIntList
    ("igIntList", 1, 0, 15, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [56] igBlendMatrixSelect
    ("igBlendMatrixSelect", 1, 0, 22, 13,
     [(_String, 2, 4), (_ObjRef, 3, 4), (_Int, 5, 4), (_ObjRef, 7, 4),
      (_ObjRef, 8, 4), (_Bool, 9, 1), (_ObjRef, 10, 4),
      (_Matrix44f, 11, 64), (_Matrix44f, 12, 64)]),
    # [57] igClut (PS2 CLUT palette - universal texture format)
    # slot_count=9: 2 inherited from igObject + 7 own (5 persistent + 2 non-persistent)
    # Only 5 persistent fields defined (slots 2-6); slots 7-8 are runtime-only
    ("igClut", 1, 0, 0, 9, [
        (_Enum, 2, 4),   # _fmt: palette pixel format (7 = RGBA_8888_32)
        (_UInt, 3, 4),   # _numEntries: palette entry count (256)
        (_Int, 4, 4),    # _stride: bytes per entry (4)
        (_MemRef, 5, 4), # _pData: palette data memory ref
        (_Int, 6, 4),    # _clutSize: total palette data size (1024)
    ]),
    # [58] igBlendStateAttr (inherits igVisualAttribute[12])
    ("igBlendStateAttr", 1, 0, 12, 5,
     [(_Short, 2, 2), (_Bool, 4, 1)]),
    # [59] igBlendFunctionAttr (inherits igVisualAttribute[12])
    ("igBlendFunctionAttr", 1, 0, 12, 15,
     [(_Short, 2, 2), (_Enum, 4, 4), (_Enum, 5, 4), (_Enum, 6, 4),
      (_ObjRef, 7, 4), (_UChar, 8, 1), (_Short, 9, 2),
      (_Enum, 11, 4), (_Enum, 12, 4), (_Enum, 13, 4), (_Enum, 14, 4)]),

    # ========================================================================
    # Actor graph types (60+) — the animation COMBINER subsystem that gives
    # the engine per-skin retargeting. Native v6 skins lack these entirely;
    # 3ds Max-exported skins always carry them, which is why Max skins
    # ground at any skeleton size. Definitions translated from a v4 Max
    # export (210xxcableMUA2.igb): identical own-field lists, slots -1,
    # slot_count -1 (the v4 igObject base had one extra slot).
    # ========================================================================
    # ---- [60..65] Bind-pose animation types (v6-precedented: identical ----
    # ---- layouts appear in native v6 anim files AND in compatible v4  ----
    # ---- community skins like 0103.igb). Written in ANIM and FULL.    ----
    # [60] igAnimation (matches native v6 anim files)
    ("igAnimation", 1, 0, 1, 11,
     [(_String, 2, 4), (_Int, 3, 4), (_ObjRef, 4, 4), (_ObjRef, 5, 4),
      (_ObjRef, 6, 4), (_Long, 7, 8), (_Long, 8, 8), (_Long, 9, 8),
      (_ObjRef, 10, 4)]),
    # [61] igAnimationBinding (native v6 layout)
    ("igAnimationBinding", 1, 0, 0, 7,
     [(_ObjRef, 2, 4), (_MemRef, 3, 4), (_Int, 4, 4), (_ObjRef, 5, 4),
      (_ObjRef, 6, 4)]),
    # [62] igAnimationBindingList
    ("igAnimationBindingList", 1, 0, 16, 5,
     [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [63] igAnimationTrack (native v6 layout: rest quat + rest trans)
    ("igAnimationTrack", 1, 0, 1, 6,
     [(_String, 2, 4), (_ObjRef, 3, 4), (_Vec4f, 4, 16), (_Vec3f, 5, 12)]),
    # [64] igAnimationTrackList
    ("igAnimationTrackList", 1, 0, 16, 5,
     [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [65] igAnimationTransitionDefinitionList
    ("igAnimationTransitionDefinitionList", 1, 0, 16, 5,
     [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),

    # ---- [66..79] Combiner/actor types (UNPRECEDENTED in v6 — crashed ----
    # ---- XML2 when included; written ONLY in FULL mode)               ----
    # [66] igAnimationSystem
    ("igAnimationSystem", 1, 0, 1, 4,
     [(_String, 2, 4), (_ObjRef, 3, 4)]),
    # [67] igAnimationCombiner (parent igAnimationSystem)
    ("igAnimationCombiner", 1, 0, 66, 14,
     [(_String, 2, 4), (_ObjRef, 3, 4), (_ObjRef, 4, 4), (_ObjRef, 5, 4),
      (_ObjRef, 6, 4), (_MemRef, 7, 4), (_MemRef, 8, 4), (_Long, 9, 8),
      (_Bool, 10, 1), (_MemRef, 12, 4), (_MemRef, 13, 4)]),
    # [68] igAnimationCombinerBoneInfo
    ("igAnimationCombinerBoneInfo", 1, 0, 0, 8,
     [(_ObjRef, 2, 4), (_ObjRef, 3, 4), (_Vec4f, 4, 16), (_Vec3f, 5, 12),
      (_Int, 6, 4), (_Bool, 7, 1)]),
    # [69] igAnimationCombinerBoneInfoList
    ("igAnimationCombinerBoneInfoList", 1, 0, 16, 5,
     [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [70] igAnimationCombinerBoneInfoListList
    ("igAnimationCombinerBoneInfoListList", 1, 0, 16, 5,
     [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [71] igAnimationState
    ("igAnimationState", 1, 0, 0, 20,
     [(_ObjRef, 2, 4), (_Enum, 3, 4), (_Enum, 4, 4), (_Enum, 5, 4),
      (_ObjRef, 6, 4), (_Bool, 7, 1), (_Float, 8, 4), (_Long, 9, 8),
      (_Long, 10, 8), (_Float, 11, 4), (_Long, 12, 8), (_Long, 13, 8),
      (_Float, 14, 4), (_Float, 15, 4), (_Long, 16, 8), (_Long, 17, 8)]),
    # [72] igAnimationStateList
    ("igAnimationStateList", 1, 0, 16, 5,
     [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [73] igAnimationModifierList
    ("igAnimationModifierList", 1, 0, 16, 5,
     [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [74] igActorInfo (parent igInfo)
    ("igActorInfo", 1, 0, 9, 10,
     [(_String, 2, 4), (_Bool, 4, 1), (_ObjRef, 5, 4), (_ObjRef, 6, 4),
      (_ObjRef, 7, 4), (_ObjRef, 8, 4), (_ObjRef, 9, 4)]),
    # [75] igActorList
    ("igActorList", 1, 0, 16, 5,
     [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [76] igActor (parent igGroup)
    ("igActor", 1, 0, 21, 18,
     [(_String, 2, 4), (_ObjRef, 3, 4), (_Int, 5, 4), (_ObjRef, 6, 4),
      (_ObjRef, 7, 4), (_MemRef, 8, 4), (_MemRef, 9, 4), (_ObjRef, 10, 4),
      (_ObjRef, 11, 4), (_ObjRef, 12, 4), (_Matrix44f, 13, 64)]),
    # [77] igAppearance (parent igNamedObject)
    ("igAppearance", 1, 0, 1, 8,
     [(_String, 2, 4), (_ObjRef, 3, 4), (_ObjRef, 4, 4), (_ObjRef, 5, 4),
      (_ObjRef, 6, 4), (_ObjRef, 7, 4)]),
    # [78] igStringObjList
    ("igStringObjList", 1, 0, 16, 5,
     [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [79] igModelViewMatrixBoneSelectList
    ("igModelViewMatrixBoneSelectList", 1, 0, 16, 5,
     [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
]

# Skin-specific meta-object indices — matches vanilla 0601.igb except igClut added
MO_OBJECT = 0
MO_NAMED_OBJECT = 1
MO_OBJECT_DIR_ENTRY = 3
MO_MEMORY_DIR_ENTRY = 4
MO_EXTERNAL_INDEXED_ENTRY = 7
MO_EXTERNAL_INFO_ENTRY = 8
MO_INFO = 9
MO_ANIMATION_DATABASE = 10
MO_GEOMETRY_ATTR_1_5 = 14
MO_DATA_LIST = 15
MO_OBJECT_LIST = 16
MO_SKELETON_LIST = 17
MO_PRIM_LENGTH_1_1 = 19
MO_NODE = 20
MO_GROUP = 21
MO_ATTR_SET = 22
MO_OVERRIDE_ATTR_SET = 23
MO_VERTEX_ARRAY_1_1 = 25
MO_IMAGE = 26
MO_ALPHA_FUNCTION_ATTR = 27
MO_ANIMATION_LIST = 28
MO_SKIN_LIST = 29
MO_APPEARANCE_LIST = 30
MO_ANIMATION_COMBINER_LIST = 31
MO_AABOX = 33
MO_SKIN = 34
MO_SKELETON_BONE_INFO = 35
MO_ANIMATION_HIERARCHY = 36
MO_SKELETON = 37
MO_SKELETON_BONE_INFO_LIST = 38
MO_NODE_LIST = 39
# Metas [60+] are conditional. The engine validates meta DEFINITIONS at load
# time even when no object uses them, so each mode writes only what it needs:
#   OFF  -> [:60]  stable types only (proven in-game)
#   ANIM -> [:66]  + bind-pose animation types (v6-precedented; the exact set
#                  a known-compatible community skin, 0103.igb, carries)
#   FULL -> [:80]  + combiner/actor types (crashed XML2; v4-only so far)
N_STABLE_METAS = 60
N_ANIM_METAS = 66
MO_ANIMATION = 60
MO_ANIMATION_BINDING = 61
MO_ANIMATION_BINDING_LIST = 62
MO_ANIMATION_TRACK = 63
MO_ANIMATION_TRACK_LIST = 64
MO_ANIMATION_TRANSITION_DEF_LIST = 65
MO_ANIMATION_SYSTEM = 66
MO_ANIMATION_COMBINER = 67
MO_COMBINER_BONE_INFO = 68
MO_COMBINER_BONE_INFO_LIST = 69
MO_COMBINER_BONE_INFO_LIST_LIST = 70
MO_ANIMATION_STATE = 71
MO_ANIMATION_STATE_LIST = 72
MO_ANIMATION_MODIFIER_LIST = 73
MO_ACTOR_INFO = 74
MO_ACTOR_LIST = 75
MO_ACTOR = 76
MO_APPEARANCE = 77
MO_STRING_OBJ_LIST = 78
MO_MVMBS_LIST = 79
MO_ATTR_LIST = 40
MO_TEXTURE_BIND_ATTR = 41
MO_MIPMAP_LIST = 42
MO_LIGHTING_STATE_ATTR = 43
MO_TEXTURE_ATTR = 44
MO_CULL_FACE_ATTR = 45
MO_SEGMENT = 46
MO_INDEX_ARRAY = 47
MO_COLOR_ATTR = 48
MO_GEOMETRY = 49
MO_ALPHA_STATE_ATTR = 50
MO_TEXTURE_STATE_ATTR = 51
MO_MATERIAL_ATTR = 52
MO_VERTEX_BLEND_STATE_ATTR = 53
MO_INFO_LIST = 54
MO_INT_LIST = 55
MO_BLEND_MATRIX_SELECT = 56
MO_CLUT = 57
MO_BLEND_STATE_ATTR = 58
MO_BLEND_FUNCTION_ATTR = 59

# XML2 PC alignment buffer (same as map files)
ALIGNMENT_BUFFER = bytes.fromhex(
    "3d000000"
    "01000000"
    "03000000"
    "10000000"
    "0a000000"
    "0b000000"
    "56657274657841727261794461746100"
    "496d61676544617461 00"
    "5665727465784461746100"
).replace(b" ", b"")

MEMORY_POOL_NAMES = [
    b"Bootstrap", b"Default", b"Current", b"NonTracked", b"System",
    b"Static", b"MetaData", b"String", b"Fast", b"List", b"Temporary",
    b"Vertex", b"RenderList", b"Texture", b"Application", b"World",
    b"Actor", b"Level", b"Frame", b"Physics", b"AI", b"DriverData",
    b"Clut", b"DMA", b"Audio", b"Video", b"Handles", b"Image",
    b"ImageObject", b"Attribute", b"Node", b"User0", b"User1",
    b"User2", b"User3", b"User4", b"User5", b"User6", b"User7",
    b"User8", b"User9", b"User10", b"User11", b"User12", b"User13",
    b"User14", b"User15",
]

# Identity 4x4 matrix (row-major, 16 floats)
_IDENTITY_MATRIX = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


def _quat_from_matrix3(R):
    """Quaternion (x, y, z, w) from a 3x3 rotation matrix (nested rows)."""
    import math
    tr = R[0][0] + R[1][1] + R[2][2]
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        w = 0.25 * s
        x = (R[2][1] - R[1][2]) / s
        y = (R[0][2] - R[2][0]) / s
        z = (R[1][0] - R[0][1]) / s
    elif R[0][0] > R[1][1] and R[0][0] > R[2][2]:
        s = math.sqrt(1.0 + R[0][0] - R[1][1] - R[2][2]) * 2.0
        w = (R[2][1] - R[1][2]) / s
        x = 0.25 * s
        y = (R[0][1] + R[1][0]) / s
        z = (R[0][2] + R[2][0]) / s
    elif R[1][1] > R[2][2]:
        s = math.sqrt(1.0 + R[1][1] - R[0][0] - R[2][2]) * 2.0
        w = (R[0][2] - R[2][0]) / s
        x = (R[0][1] + R[1][0]) / s
        y = 0.25 * s
        z = (R[1][2] + R[2][1]) / s
    else:
        s = math.sqrt(1.0 + R[2][2] - R[0][0] - R[1][1]) * 2.0
        w = (R[1][0] - R[0][1]) / s
        x = (R[0][2] + R[2][0]) / s
        y = (R[1][2] + R[2][1]) / s
        z = 0.25 * s
    return (x, y, z, w)


def _local_bind_transforms(skeleton_data):
    """Per-bone PARENT-LOCAL bind (quat_xyzw, trans) for the combiner.

    World rotations come from the inverse joint matrices (row-major,
    row-vector convention: R_world = R_inv transposed). Local rotation
    composes as R_local = R_world(bone) @ R_inv(parent). Bones without an
    inverse bind (roots, FX bones) use identity. Translations are the
    skeleton's parent-local bind translations as-is.
    """
    bones = skeleton_data['bones']
    inv3 = []      # original inv rotation 3x3 (rows)
    world3 = []    # world rotation 3x3 = inv transposed
    for bone in bones:
        m = bone.get('inv_joint_matrix')
        if m:
            inv = [[m[4 * r + c] for c in range(3)] for r in range(3)]
            wld = [[m[4 * c + r] for c in range(3)] for r in range(3)]
        else:
            inv = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
            wld = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        inv3.append(inv)
        world3.append(wld)

    out = []
    for i, bone in enumerate(bones):
        parent = bone.get('parent_idx', -1)
        Rb = world3[i]
        if 0 <= parent < len(bones):
            Rp_inv = inv3[parent]
            local = [[sum(Rb[r][k] * Rp_inv[k][c] for k in range(3))
                      for c in range(3)] for r in range(3)]
        else:
            local = Rb
        trans = tuple(bone.get('translation', (0.0, 0.0, 0.0)))
        out.append((_quat_from_matrix3(local), trans))
    return out


def _world_bind_matrix(bone):
    """Row-major 4x4 world bind matrix (inverse of the inv joint matrix)."""
    m = bone.get('inv_joint_matrix')
    if not m:
        return (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
    # world rotation = inv 3x3 transposed; world position p_i = -(t . R_row_i)
    R = [[m[4 * c + r] for c in range(3)] for r in range(3)]  # world rows
    t = (m[12], m[13], m[14])
    inv_rows = [[m[0], m[1], m[2]], [m[4], m[5], m[6]], [m[8], m[9], m[10]]]
    p = [-(t[0] * row[0] + t[1] * row[1] + t[2] * row[2]) for row in inv_rows]
    return (R[0][0], R[0][1], R[0][2], 0.0,
            R[1][0], R[1][1], R[1][2], 0.0,
            R[2][0], R[2][1], R[2][2], 0.0,
            p[0], p[1], p[2], 1.0)


_IDENTITY44 = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
               0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


class SkinBuilder:
    """Builds a skin IGB file from mesh/skeleton/material data.

    Follows the same _obj_list/_ref_infos/_add_obj/_add_mem pattern
    as IGBBuilder but with the skin-specific meta-object registry.
    """

    def __init__(self):
        self._obj_list = []
        self._ref_infos = []

    def build_skin(self, submeshes, skeleton_data, bms_palette, export_name='',
                   actor_graph='OFF'):
        """Build a complete skin IGB structure.

        Scene graph uses Pattern B (per-unit BMS, Cable 11501 / 3ds Max style):
            igSkin._skinnedGraph -> igGroup "igActor01"
              +-- igGroup "{name}" -> BMS -> AttrSet -> igGeometry (body)
              +-- igGroup "{name}_outline" -> BMS -> AttrSet -> igGeometry (body outline)
              +-- igSegment "seg" -> igGroup -> BMS -> AttrSet -> igGeometry (segment)
              +-- igSegment "seg_outline" -> igGroup -> BMS -> AttrSet -> igGeometry

        Each unit has its own igBlendMatrixSelect, all sharing the same palette.

        Args:
            submeshes: list of dicts, each with keys:
                'mesh': MeshExport instance
                'material': dict with material properties
                'texture_levels': list of (compressed_bytes, w, h) or None
                'texture_name': str
                'is_outline': bool
            skeleton_data: dict with keys:
                'name': skeleton name string
                'joint_count': int
                'bones': list of dicts
            bms_palette: list of int (BMS indices)
            export_name: str, the output filename stem (e.g. "0101" for 0101.igb).
                Used for igSkin name, geometry names, and igAnimationDatabase name
                to match vanilla convention where these names equal the file stem.

        Returns:
            IGBWriter ready to write
        """
        self._obj_list = []
        self._ref_infos = []
        self._actor_graph_mode = actor_graph

        writer = self._init_writer()
        skel_name = skeleton_data.get('name', '')

        # Use export_name for "public" names (igSkin, igGeometry, igAnimationDatabase)
        # Vanilla convention: these names match the file stem (e.g. "0601" for 0601.igb)
        # The skeleton name stays as the original for internal reference
        skin_name = export_name if export_name else skel_name

        # ---- 1. Build skeleton objects ----
        skeleton_idx = self._build_skeleton(skeleton_data)

        # ---- 2. Build BMS palette (igIntList) ----
        bms_int_list_idx = self._build_int_list(bms_palette)

        # ---- 3. Build per-texture chains ----
        # Each unique texture gets its own igTextureBindAttr and igMaterialAttr.
        # This allows different body parts / segments to use different textures
        # (e.g. Cyclops body + winter accessories, Professor X + wheelchair).
        #
        # Submeshes with NO texture data (empty name + no levels/clut) are
        # skipped here — they'll fall back to the first available texture in
        # step 5c.  This preserves the old behavior where segments without
        # their own texture shared the body's texture chain.
        tex_chain_map = {}  # tex_key -> {'tex_bind_idx': int, 'material_idx': int}
        outline_material = _default_outline_material()

        for sub in submeshes:
            if sub.get('is_outline', False):
                if sub.get('material'):
                    outline_material = sub['material']
                continue

            tex_key = sub.get('texture_name', '') or ''
            if tex_key in tex_chain_map:
                continue  # Already built chain for this texture

            # Skip submeshes with NO texture data — they'll use fallback
            has_tex_data = bool(sub.get('clut_data') or sub.get('texture_levels')
                                or sub.get('cmpr_levels') or sub.get('rgb5a3_levels'))
            if not tex_key and not has_tex_data:
                continue

            # Build texture chain for this unique texture
            if sub.get('clut_data'):
                palette_data, index_data, cw, ch = sub['clut_data']
                _, tex_bind_idx = self._build_clut_texture_chain(
                    palette_data, index_data, cw, ch, tex_key
                )
            elif sub.get('rgb5a3_levels'):
                _, tex_bind_idx = self._build_rgb5a3_texture_chain(
                    sub.get('rgb5a3_levels'), tex_key
                )
            elif sub.get('cmpr_levels'):
                _, tex_bind_idx = self._build_cmpr_texture_chain(
                    sub.get('cmpr_levels'), tex_key
                )
            else:
                _, tex_bind_idx = self._build_texture_chain(
                    sub.get('texture_levels'), tex_key
                )

            # Build material for this texture group
            mat_props = sub.get('material', _default_material())
            material_idx = self._build_material(mat_props)

            tex_chain_map[tex_key] = {
                'tex_bind_idx': tex_bind_idx,
                'material_idx': material_idx,
            }

        # If NO submesh had valid texture data, build a default empty chain
        # so the file still has valid structure.
        if not tex_chain_map:
            _, tex_bind_idx = self._build_texture_chain(None, '')
            material_idx = self._build_material(_default_material())
            tex_chain_map[''] = {
                'tex_bind_idx': tex_bind_idx,
                'material_idx': material_idx,
            }

        # ---- 4. Build per-submesh geometry objects ----
        main_geom_entries = []    # [(geom_idx, sub_dict), ...]
        outline_geom_entries = []  # [(geom_idx, sub_dict), ...]
        all_bbox_mins = []
        all_bbox_maxs = []

        for sub in submeshes:
            mesh = sub['mesh']
            is_outline = sub.get('is_outline', False)

            all_bbox_mins.append(mesh.bbox_min)
            all_bbox_maxs.append(mesh.bbox_max)

            # Vertex data with blend weights/indices
            # CRITICAL: Outline meshes use format 0x441 (pos + blend only, NO
            # normals/UVs). Even if Blender provides UVs (all zeros), outlines
            # must NOT include them — vanilla uses format 0x441 for outlines.
            has_blend = bool(mesh.blend_weights and mesh.blend_indices)
            effective_has_uvs = bool(mesh.uvs) and not is_outline
            vertex_array_idx = self._build_vertex_array(
                mesh, skinned=has_blend, has_uvs=effective_has_uvs
            )

            # Index data (strip conversion)
            strip_indices = triangles_to_strip(mesh.indices)
            num_strip = len(strip_indices)

            idx_data = self._pack_indices(strip_indices)
            idx_mb = self._add_mem(MO_EXTERNAL_INFO_ENTRY, idx_data)

            index_array_idx = self._add_obj(MO_INDEX_ARRAY, [
                (2, idx_mb, 'MemoryRef', 4),
                (3, num_strip, 'UnsignedInt', 4),
                (4, 0, 'Enum', 4),
                (5, 0, 'UnsignedInt', 4),
            ])

            # PrimLengthArray1_1
            prim_data = struct.pack("<I", num_strip)
            prim_mb = self._add_mem(MO_INFO, prim_data)
            prim_array_idx = self._add_obj(MO_PRIM_LENGTH_1_1, [
                (2, prim_mb, 'MemoryRef', 4),
                (3, 1, 'UnsignedInt', 4),
                (4, 32, 'UnsignedInt', 4),
            ])

            # GeometryAttr1_5
            geom_attr_idx = self._add_obj(MO_GEOMETRY_ATTR_1_5, [
                (2, 0, 'Short', 2),
                (4, vertex_array_idx, 'ObjectRef', 4),
                (5, index_array_idx, 'ObjectRef', 4),
                (6, 4, 'Enum', 4),          # prim_type = 4 (TriangleStrip)
                (7, 1, 'UnsignedInt', 4),
                (8, 0, 'UnsignedInt', 4),
                (9, -1, 'ObjectRef', 4),
                (10, 0, 'Int', 4),
                (11, -1, 'ObjectRef', 4),
                (12, -1, 'ObjectRef', 4),
                (13, prim_array_idx, 'ObjectRef', 4),
            ])

            # Geometry attr list
            geom_data = struct.pack("<i", geom_attr_idx)
            geom_mb = self._add_mem(MO_OBJECT, geom_data)
            geom_attr_list_idx = self._add_obj(MO_ATTR_LIST, [
                (2, 1, 'Int', 4),
                (3, 1, 'Int', 4),
                (4, geom_mb, 'MemoryRef', 4),
            ])

            # Geometry leaf node (no children)
            geom_node_list = self._add_obj(MO_NODE_LIST, [
                (2, 0, 'Int', 4),
                (3, 0, 'Int', 4),
                (4, -1, 'MemoryRef', 4),
            ])

            # Geometry name: use segment name when available (vanilla: "gun_left", "1801")
            # Outline segment names already include "_outline" (e.g., "gun_left_outline")
            seg_name = sub.get('segment_name', '')
            if is_outline:
                if seg_name:
                    # seg_name already ends in _outline (e.g., "gun_left_outline")
                    geom_name = seg_name
                else:
                    geom_name = (skin_name + '_outline') if skin_name else ''
            else:
                geom_name = seg_name if seg_name else skin_name

            geometry_idx = self._add_obj(MO_GEOMETRY, [
                (2, geom_name, 'String', 4),
                (3, -1, 'ObjectRef', 4),   # _parentTransform = null
                (5, 0x4, 'Int', 4),        # flags=4 (matches vanilla)
                (7, geom_node_list, 'ObjectRef', 4),
                (8, geom_attr_list_idx, 'ObjectRef', 4),
                (9, 1, 'Bool', 1),
            ])

            if is_outline:
                outline_geom_entries.append((geometry_idx, sub))
            else:
                main_geom_entries.append((geometry_idx, sub))

        # Compute union bounding box across all submeshes
        union_min = (
            min(b[0] for b in all_bbox_mins),
            min(b[1] for b in all_bbox_mins),
            min(b[2] for b in all_bbox_mins),
        )
        union_max = (
            max(b[0] for b in all_bbox_maxs),
            max(b[1] for b in all_bbox_maxs),
            max(b[2] for b in all_bbox_maxs),
        )

        # ---- 5. Assemble scene graph (Pattern B: per-unit BMS) ----
        # Each body/segment unit gets its own BMS → AttrSet → Geometry chain.
        # All units are children of a root igGroup "igActor01".
        # This matches the Cable 11501 / 3ds Max pattern which is confirmed
        # working with custom skins and segments in-game.
        root_child_refs = []  # children of root igGroup

        # ---- 5a. Build shared render state attrs for main units ----
        # Color and TextureState are shared across all main units.
        # Material and TextureBindAttr are per-texture (looked up per unit).
        main_color_idx = None
        main_tex_state_idx = None
        if main_geom_entries:
            main_tex_state_idx = self._add_obj(MO_TEXTURE_STATE_ATTR, [
                (2, 0, 'Short', 2),
                (4, 1, 'Bool', 1),
                (5, 0, 'Int', 4),
            ])
            # Use first main submesh's material color_attr for shared color
            first_main_sub = main_geom_entries[0][1]
            first_mat = first_main_sub.get('material', {})
            color_val = first_mat.get('color_attr', (1.0, 1.0, 1.0, 1.0))
            main_color_idx = self._add_obj(MO_COLOR_ATTR, [
                (2, 0, 'Short', 2),
                (4, color_val, 'Vec4f', 16),
            ])

        # ---- 5b. Build shared render state attrs for outline units ----
        outline_attrs = None
        if outline_geom_entries:
            outline_tex_state_idx = self._add_obj(MO_TEXTURE_STATE_ATTR, [
                (2, 0, 'Short', 2),
                (4, 0, 'Bool', 1),     # disabled (texture OFF for outlines)
                (5, 0, 'Int', 4),
            ])
            outline_material_idx = self._build_material(outline_material)
            outline_color_idx = self._add_obj(MO_COLOR_ATTR, [
                (2, 0, 'Short', 2),
                (4, (0.0, 0.0, 0.0, 1.0), 'Vec4f', 16),
            ])
            cull_idx = self._add_obj(MO_CULL_FACE_ATTR, [
                (2, 0, 'Short', 2),
                (4, 1, 'Bool', 1),
                (5, 0, 'Enum', 4),    # FRONT face
            ])
            lighting_idx = self._add_obj(MO_LIGHTING_STATE_ATTR, [
                (2, 0, 'Short', 2),
                (4, 0, 'Bool', 1),    # disabled (lighting OFF for outlines)
            ])
            alpha_func_idx = self._add_obj(MO_ALPHA_FUNCTION_ATTR, [
                (2, 0, 'Short', 2),
                (4, 6, 'Enum', 4),     # GL_GEQUAL
                (5, 0.99, 'Float', 4), # ref=0.99
            ])
            alpha_state_idx = self._add_obj(MO_ALPHA_STATE_ATTR, [
                (2, 0, 'Short', 2),
                (4, 1, 'Bool', 1),
            ])
            outline_attrs = [outline_color_idx, outline_material_idx,
                             outline_tex_state_idx, cull_idx, lighting_idx,
                             alpha_func_idx, alpha_state_idx]

        # ---- 5c. Build per-unit chains for main geometry ----
        # Each main unit: igGroup/igSegment → igGroup → BMS → AttrSet → Geometry
        # Each unit gets per-texture material + texture bind from tex_chain_map.
        for gi, sub in main_geom_entries:
            seg_name = sub.get('segment_name', '')
            seg_flags = sub.get('segment_flags', 0)

            unit_name = seg_name if seg_name else skin_name

            # Look up per-texture attrs for this submesh.
            # If this submesh has no texture, fall back to the first available
            # texture chain (body texture).  This matches vanilla behavior where
            # segments without their own texture share the body's texture.
            tex_key = sub.get('texture_name', '') or ''
            if tex_key not in tex_chain_map:
                tex_key = next(iter(tex_chain_map))  # fallback to first
            chain = tex_chain_map.get(tex_key, {})
            tex_bind_idx = chain.get('tex_bind_idx', -1)
            material_idx = chain.get('material_idx', -1)

            # Compose per-unit attr list: color + material + texbind + texstate
            # + render state attrs (blend, alpha, lighting, cull face)
            unit_attrs = [main_color_idx, material_idx,
                          tex_bind_idx, main_tex_state_idx]

            # Build render state attrs from material properties
            mat_props = sub.get('material', {})
            unit_attrs += self._build_render_state_attrs(mat_props)

            attrset_idx = self._build_unit_attrset(gi, unit_attrs, unit_name)
            bms_idx = self._build_unit_bms(attrset_idx, bms_int_list_idx)

            if seg_name:
                # Segment: igSegment → igGroup → BMS → AttrSet → Geometry
                inner_grp = self._build_group_node(seg_name, [bms_idx])
                seg_node = self._build_segment_node(seg_name, seg_flags, inner_grp)
                root_child_refs.append(seg_node)
            else:
                # Body: igGroup → BMS → AttrSet → Geometry
                body_grp = self._build_group_node(skin_name, [bms_idx])
                root_child_refs.append(body_grp)

        # ---- 5d. Build per-unit chains for outline geometry ----
        # Each outline unit: igGroup/igSegment → igGroup → BMS → AttrSet → Geometry
        for gi, sub in outline_geom_entries:
            seg_name = sub.get('segment_name', '')
            seg_flags = sub.get('segment_flags', 0)

            if seg_name:
                unit_name = seg_name  # already includes _outline
            else:
                unit_name = (skin_name + '_outline') if skin_name else ''

            attrset_idx = self._build_unit_attrset(gi, outline_attrs, unit_name)
            bms_idx = self._build_unit_bms(attrset_idx, bms_int_list_idx)

            if seg_name:
                # Segment outline: igSegment → igGroup → BMS → AttrSet → Geometry
                inner_grp = self._build_group_node(seg_name, [bms_idx])
                seg_node = self._build_segment_node(seg_name, seg_flags, inner_grp)
                root_child_refs.append(seg_node)
            else:
                # Body outline: igGroup → BMS → AttrSet → Geometry
                outline_grp = self._build_group_node(unit_name, [bms_idx])
                root_child_refs.append(outline_grp)

        # ---- 6. Build root igGroup "igActor01" ----
        root_group_idx = self._build_group_node("igActor01", root_child_refs)

        # ---- 7. Build igSkin ----
        root_aabox = self._add_obj(MO_AABOX, [
            (2, union_min, 'Vec3f', 12),
            (3, union_max, 'Vec3f', 12),
        ])

        skin_idx = self._add_obj(MO_SKIN, [
            (2, skin_name, 'String', 4),
            (3, root_group_idx, 'ObjectRef', 4),  # _skinnedGraph -> igGroup root
            (4, root_aabox, 'ObjectRef', 4),      # _aabb
        ])

        # ---- 7.5 Bind-pose animation (per-bone parent-local transforms) ----
        # Part of the Max-style actor graph (engine-side anim retargeting).
        # actor_graph: 'OFF' = bare native-style skin (proven in-game),
        # 'ANIM' = bind-pose animation only (v6-precedented types — crash
        # bisect step), 'FULL' = complete combiner/actor graph (crashed
        # XML2 on first test; under investigation).
        bindpose_anim_idx = None
        bind_locals = None
        if actor_graph in ('ANIM', 'FULL'):
            bind_locals = _local_bind_transforms(skeleton_data)
            bindpose_anim_idx = self._build_bindpose_animation(
                skeleton_idx, skeleton_data, bind_locals
            )

        # ---- 8. Build igAnimationDatabase (after igSkin so SkinList can reference it) ----
        anim_db_idx = self._build_animation_database(
            skeleton_idx, skeleton_data, skin_idx, skin_name=skin_name,
            bindpose_anim_idx=bindpose_anim_idx
        )

        # ---- 8.5 Actor graph (engine-side animation retargeting) ----
        actor_info_idx = None
        if actor_graph == 'FULL':
            actor_info_idx = self._build_actor_graph(
                skeleton_idx, skeleton_data, skin_idx, anim_db_idx,
                bindpose_anim_idx, bms_palette, bind_locals
            )

        # ---- 9. Build igInfoList ----
        if actor_info_idx is not None:
            info_refs = struct.pack("<ii", actor_info_idx, anim_db_idx)
            n_infos = 2
        else:
            info_refs = struct.pack("<i", anim_db_idx)
            n_infos = 1
        info_mb = self._add_mem(MO_OBJECT, info_refs)
        info_list_idx = self._add_obj(MO_INFO_LIST, [
            (2, n_infos, 'Int', 4),
            (3, n_infos, 'Int', 4),
            (4, info_mb, 'MemoryRef', 4),
        ])

        # ---- 10. Finalize ----
        self._finalize_writer(writer, info_list_idx)
        return writer

    # =========================================================================
    # Skeleton building
    # =========================================================================

    def _build_skeleton(self, skeleton_data):
        """Build igSkeleton with all bone info objects."""
        bones = skeleton_data['bones']
        name = skeleton_data.get('name', '')
        joint_count = skeleton_data.get('joint_count', 0)

        # Build igSkeletonBoneInfo for each bone
        bone_info_indices = []
        for bone in bones:
            bi_idx = self._add_obj(MO_SKELETON_BONE_INFO, [
                (2, bone['name'], 'String', 4),
                (3, bone['parent_idx'], 'Int', 4),
                (4, bone['bm_idx'], 'Int', 4),
                (5, bone['flags'], 'Int', 4),
            ])
            bone_info_indices.append(bi_idx)

        # igSkeletonBoneInfoList
        n_bones = len(bone_info_indices)
        bil_data = struct.pack("<" + "i" * n_bones, *bone_info_indices)
        bil_mb = self._add_mem(MO_OBJECT, bil_data)
        bone_info_list_idx = self._add_obj(MO_SKELETON_BONE_INFO_LIST, [
            (2, n_bones, 'Int', 4),
            (3, n_bones, 'Int', 4),
            (4, bil_mb, 'MemoryRef', 4),
        ])

        # Bone translations memory (Vec3f per bone)
        trans_data = bytearray(n_bones * 12)
        for i, bone in enumerate(bones):
            tx, ty, tz = bone['translation']
            struct.pack_into("<fff", trans_data, i * 12, tx, ty, tz)
        trans_mb = self._add_mem(MO_ANIMATION_HIERARCHY, bytes(trans_data))

        # Inverse joint matrices memory (Matrix44f per joint, indexed by bm_idx)
        if joint_count > 0:
            inv_data = bytearray(joint_count * 64)
            # Build mapping: bm_idx → inv_joint_matrix
            for bone in bones:
                bm = bone['bm_idx']
                ijm = bone.get('inv_joint_matrix')
                if ijm is not None and 0 <= bm < joint_count:
                    for j, val in enumerate(ijm):
                        struct.pack_into("<f", inv_data, bm * 64 + j * 4, val)
            inv_mb = self._add_mem(MO_ATTR_LIST, bytes(inv_data))
        else:
            inv_mb = -1

        # igSkeleton
        skeleton_idx = self._add_obj(MO_SKELETON, [
            (2, name, 'String', 4),
            (3, trans_mb, 'MemoryRef', 4),                    # _boneTranslationArray
            (4, bone_info_list_idx, 'ObjectRef', 4),          # _boneInfoList
            (5, inv_mb, 'MemoryRef', 4),                      # _invJointArray
            (6, joint_count, 'Int', 4),                       # _jointCount
        ])

        return skeleton_idx

    def _build_animation_database(self, skeleton_idx, skeleton_data,
                                  skin_idx=None, skin_name='',
                                  bindpose_anim_idx=None):
        """Build igAnimationDatabase referencing the skeleton and skin."""
        # igSkeletonList (1 skeleton)
        skel_ref_data = struct.pack("<i", skeleton_idx)
        skel_ref_mb = self._add_mem(MO_OBJECT, skel_ref_data)
        skel_list_idx = self._add_obj(MO_SKELETON_LIST, [
            (2, 1, 'Int', 4),
            (3, 1, 'Int', 4),
            (4, skel_ref_mb, 'MemoryRef', 4),
        ])

        # igSkinList (contains igSkin ref if provided)
        if skin_idx is not None and skin_idx >= 0:
            skin_ref_data = struct.pack("<i", skin_idx)
            skin_ref_mb = self._add_mem(MO_OBJECT, skin_ref_data)
            skin_list_idx = self._add_obj(MO_SKIN_LIST, [
                (2, 1, 'Int', 4),
                (3, 1, 'Int', 4),
                (4, skin_ref_mb, 'MemoryRef', 4),
            ])
        else:
            skin_list_idx = self._add_obj(MO_SKIN_LIST, [
                (2, 0, 'Int', 4),
                (3, 0, 'Int', 4),
                (4, -1, 'MemoryRef', 4),
            ])

        # igAnimationList — carries the bind-pose animation when the actor
        # graph is emitted (Max-exporter convention); empty otherwise
        if bindpose_anim_idx is not None and bindpose_anim_idx >= 0:
            anim_ref_data = struct.pack("<i", bindpose_anim_idx)
            anim_ref_mb = self._add_mem(MO_OBJECT, anim_ref_data)
            anim_list_idx = self._add_obj(MO_ANIMATION_LIST, [
                (2, 1, 'Int', 4),
                (3, 1, 'Int', 4),
                (4, anim_ref_mb, 'MemoryRef', 4),
            ])
        else:
            anim_list_idx = self._add_obj(MO_ANIMATION_LIST, [
                (2, 0, 'Int', 4),
                (3, 0, 'Int', 4),
                (4, -1, 'MemoryRef', 4),
            ])

        # Empty igAppearanceList (vanilla has this, NOT null)
        appear_list_idx = self._add_obj(MO_APPEARANCE_LIST, [
            (2, 0, 'Int', 4),
            (3, 0, 'Int', 4),
            (4, -1, 'MemoryRef', 4),
        ])

        # Empty igAnimationCombinerList (vanilla has this, NOT null)
        anim_combiner_list_idx = self._add_obj(MO_ANIMATION_COMBINER_LIST, [
            (2, 0, 'Int', 4),
            (3, 0, 'Int', 4),
            (4, -1, 'MemoryRef', 4),
        ])

        # igAnimationDatabase — ALL slots must have actual list objects (not null)
        # Name should match the file stem (vanilla convention)
        adb_name = skin_name if skin_name else skeleton_data.get('name', '')
        adb_idx = self._add_obj(MO_ANIMATION_DATABASE, [
            (2, adb_name, 'String', 4),
            (4, 1, 'Bool', 1),
            (5, skel_list_idx, 'ObjectRef', 4),            # _skeletonList
            (6, anim_list_idx, 'ObjectRef', 4),             # _animationList (empty list)
            (7, skin_list_idx, 'ObjectRef', 4),             # _skinList
            (8, appear_list_idx, 'ObjectRef', 4),           # _appearanceList (empty list)
            (9, anim_combiner_list_idx, 'ObjectRef', 4),   # _animCombinerList (empty list)
        ])

        return adb_idx

    def _build_int_list(self, values):
        """Build igIntList from a list of ints."""
        n = len(values)
        data = struct.pack("<" + "i" * n, *values)
        data_mb = self._add_mem(MO_NAMED_OBJECT, data)
        return self._add_obj(MO_INT_LIST, [
            (2, n, 'Int', 4),
            (3, n, 'Int', 4),
            (4, data_mb, 'MemoryRef', 4),
        ])

    # =========================================================================
    # Mesh building (reused from igb_builder pattern)
    # =========================================================================

    def _build_vertex_array(self, mesh, skinned=True, has_uvs=True):
        """Build igVertexArray1_1 with optional blend data.

        Args:
            mesh: MeshExport with positions, normals, uvs, blend data
            skinned: whether to include blend weights/indices
            has_uvs: whether UVs are present
        """
        num_verts = len(mesh.positions)

        # Vanilla outline meshes (format 0x441) have ONLY positions in ext_indexed.
        # No normals, no UVs — just slot 0 (positions).
        # Main meshes (format 0x10443) have slots {0:pos, 1:norm, 11:uv}.
        is_outline_format = skinned and not has_uvs  # 0x441

        # igExternalIndexedEntry (20 x uint32)
        ext_indexed = self._build_ext_indexed_data()
        ext_mb = self._add_mem(MO_EXTERNAL_INDEXED_ENTRY, ext_indexed)

        pos_data = self._pack_positions(mesh.positions)
        pos_mb = self._add_mem(MO_OBJECT_DIR_ENTRY, pos_data)

        # Only include normals for non-outline formats
        if not is_outline_format and mesh.normals:
            norm_data = self._pack_normals(mesh.normals)
            norm_mb = self._add_mem(MO_OBJECT_DIR_ENTRY, norm_data)
        else:
            norm_mb = -1

        if has_uvs and mesh.uvs:
            uv_data = self._pack_uvs(mesh.uvs)
            uv_mb = self._add_mem(MO_OBJECT_DIR_ENTRY, uv_data)
        else:
            uv_mb = -1

        self._patch_ext_indexed(ext_mb, pos_mb, norm_mb, uv_mb)

        # Blend data
        bw_mb = -1
        bi_mb = -1
        if skinned and mesh.blend_weights and mesh.blend_indices:
            bw_data = self._pack_blend_weights(mesh.blend_weights)
            bw_mb = self._add_mem(MO_OBJECT_LIST, bw_data, align_type=0)

            bi_data = self._pack_blend_indices(mesh.blend_indices)
            bi_mb = self._add_mem(MO_EXTERNAL_INFO_ENTRY, bi_data)

        # Vertex format flags (from vanilla skin files):
        # 0x10443 = pos + norm + UV + blend (main skinned mesh)
        # 0x00441 = pos + blend (outline skinned mesh, no norm/UV)
        # 0x10003 = pos + norm + UV (unskinned)
        # 0x00001 = pos only
        has_blend = (bw_mb >= 0 and bi_mb >= 0)
        has_norms = bool(mesh.normals)
        actual_has_uvs = (uv_mb >= 0)

        if has_blend and actual_has_uvs:
            vertex_format = 0x10443      # skinned + UV (vanilla main mesh)
        elif has_blend and not actual_has_uvs:
            vertex_format = 0x00441      # skinned, no UV (vanilla outline)
        elif actual_has_uvs:
            vertex_format = 0x10003      # unskinned + UV (map-style)
        else:
            vertex_format = 0x00001      # pos only

        return self._add_obj(MO_VERTEX_ARRAY_1_1, [
            (2, ext_mb, 'MemoryRef', 4),
            (3, num_verts, 'UnsignedInt', 4),
            (4, 0, 'UnsignedInt', 4),
            (5, 0, 'UnsignedInt', 4),
            (6, vertex_format, 'Struct', 4),
            (7, bw_mb, 'MemoryRef', 4),
            (8, bi_mb, 'MemoryRef', 4),
            (10, -1, 'MemoryRef', 4),
        ])

    def _build_texture_chain(self, texture_levels, tex_name):
        """Build image + mipmap + texture attr + bind. Returns (tex_attr, tex_bind)."""
        if texture_levels is None:
            texture_levels = []

        base_img_idx = None
        mip_img_indices = []

        for level_idx, (comp_data, tw, th) in enumerate(texture_levels):
            pixel_mb = self._add_mem(MO_EXTERNAL_INFO_ENTRY, comp_data, align_type=1)
            img_size = len(comp_data)
            stride = max(1, (tw + 3) // 4) * 16

            img_idx = self._add_obj(MO_IMAGE, [
                (2, tw, 'UnsignedInt', 4),
                (3, th, 'UnsignedInt', 4),
                (4, 4, 'UnsignedInt', 4),
                (5, 1, 'UnsignedInt', 4),
                (6, 100, 'UnsignedInt', 4),
                (7, 2, 'UnsignedInt', 4),
                (8, 2, 'UnsignedInt', 4),
                (9, 2, 'UnsignedInt', 4),
                (10, 2, 'UnsignedInt', 4),
                (11, 16, 'Enum', 4),             # pfmt=16 (DXT5)
                (12, img_size, 'Int', 4),
                (13, pixel_mb, 'MemoryRef', 4),
                (14, -1, 'MemoryRef', 4),
                (15, 1, 'Bool', 1),
                (16, 0, 'UnsignedInt', 4),
                (17, -1, 'ObjectRef', 4),
                (18, 0, 'UnsignedInt', 4),
                (19, stride, 'Int', 4),
                (20, 1, 'Bool', 1),
                (21, 0, 'UnsignedInt', 4),
                (22, tex_name, 'String', 4),
            ])

            if level_idx == 0:
                base_img_idx = img_idx
            else:
                mip_img_indices.append(img_idx)

        if mip_img_indices:
            mip_data = struct.pack("<" + "i" * len(mip_img_indices), *mip_img_indices)
            mip_mb = self._add_mem(MO_OBJECT, mip_data)
            mipmap_list_idx = self._add_obj(MO_MIPMAP_LIST, [
                (2, len(mip_img_indices), 'Int', 4),
                (3, len(mip_img_indices), 'Int', 4),
                (4, mip_mb, 'MemoryRef', 4),
            ])
        else:
            mipmap_list_idx = -1

        num_levels = len(texture_levels)
        texture_attr_idx = self._add_obj(MO_TEXTURE_ATTR, [
            (2, 0, 'Short', 2),
            (4, 0, 'UnsignedInt', 4),
            (5, 1, 'Enum', 4),
            (6, 3, 'Enum', 4),
            (7, 1, 'Enum', 4),
            (8, 1, 'Enum', 4),
            (10, 0, 'Enum', 4),
            (11, 0, 'Enum', 4),
            (12, base_img_idx if base_img_idx is not None else -1, 'ObjectRef', 4),
            (13, 0, 'Bool', 1),
            (14, -1, 'ObjectRef', 4),
            (15, num_levels, 'Int', 4),
            (16, mipmap_list_idx, 'ObjectRef', 4),
        ])

        texture_bind_idx = self._add_obj(MO_TEXTURE_BIND_ATTR, [
            (2, 0, 'Short', 2),
            (4, texture_attr_idx, 'ObjectRef', 4),
            (5, 0, 'Int', 4),
        ])

        return texture_attr_idx, texture_bind_idx

    def _build_cmpr_texture_chain(self, cmpr_levels, tex_name):
        """Build a GameCube/Wii CMPR (igImage pfmt 34) texture chain.

        Mirrors _build_texture_chain but emits the GX-tiled CMPR image layout
        verified against native GC actors: components=4, _order=101, bits=1,
        pfmt=34, stride=width*2, compressed=1, NO igClut. `cmpr_levels` is a
        list of (cmpr_bytes, w, h) — currently a single base level.
        """
        if cmpr_levels is None:
            cmpr_levels = []

        base_img_idx = None
        mip_img_indices = []

        for level_idx, (comp_data, tw, th) in enumerate(cmpr_levels):
            pixel_mb = self._add_mem(MO_EXTERNAL_INFO_ENTRY, comp_data, align_type=1)
            img_size = len(comp_data)
            # Native GC stride = width*2 = (blocks-per-row) * 8 bytes per block.
            stride = max(1, (tw + 3) // 4) * 8

            img_idx = self._add_obj(MO_IMAGE, [
                (2, tw, 'UnsignedInt', 4),
                (3, th, 'UnsignedInt', 4),
                (4, 4, 'UnsignedInt', 4),
                (5, 1, 'UnsignedInt', 4),
                (6, 101, 'UnsignedInt', 4),       # _order = RGBA (native GC value)
                (7, 1, 'UnsignedInt', 4),
                (8, 1, 'UnsignedInt', 4),
                (9, 1, 'UnsignedInt', 4),
                (10, 1, 'UnsignedInt', 4),
                (11, 34, 'Enum', 4),              # pfmt = 34 (GameCube CMPR)
                (12, img_size, 'Int', 4),
                (13, pixel_mb, 'MemoryRef', 4),
                (14, -1, 'MemoryRef', 4),
                (15, 1, 'Bool', 1),
                (16, 0, 'UnsignedInt', 4),
                (17, -1, 'ObjectRef', 4),          # no CLUT
                (18, 0, 'UnsignedInt', 4),
                (19, stride, 'Int', 4),
                (20, 1, 'Bool', 1),                # compressed
                (21, 0, 'UnsignedInt', 4),
                (22, tex_name, 'String', 4),
            ])

            if level_idx == 0:
                base_img_idx = img_idx
            else:
                mip_img_indices.append(img_idx)

        if mip_img_indices:
            mip_data = struct.pack("<" + "i" * len(mip_img_indices), *mip_img_indices)
            mip_mb = self._add_mem(MO_OBJECT, mip_data)
            mipmap_list_idx = self._add_obj(MO_MIPMAP_LIST, [
                (2, len(mip_img_indices), 'Int', 4),
                (3, len(mip_img_indices), 'Int', 4),
                (4, mip_mb, 'MemoryRef', 4),
            ])
        else:
            mipmap_list_idx = -1

        num_levels = len(cmpr_levels)
        texture_attr_idx = self._add_obj(MO_TEXTURE_ATTR, [
            (2, 0, 'Short', 2),
            (4, 0, 'UnsignedInt', 4),
            (5, 1, 'Enum', 4),
            (6, 3, 'Enum', 4),
            (7, 1, 'Enum', 4),
            (8, 1, 'Enum', 4),
            (10, 0, 'Enum', 4),
            (11, 0, 'Enum', 4),
            (12, base_img_idx if base_img_idx is not None else -1, 'ObjectRef', 4),
            (13, 0, 'Bool', 1),
            (14, -1, 'ObjectRef', 4),
            (15, num_levels, 'Int', 4),
            (16, mipmap_list_idx, 'ObjectRef', 4),
        ])

        texture_bind_idx = self._add_obj(MO_TEXTURE_BIND_ATTR, [
            (2, 0, 'Short', 2),
            (4, texture_attr_idx, 'ObjectRef', 4),
            (5, 0, 'Int', 4),
        ])

        return texture_attr_idx, texture_bind_idx

    def _build_rgb5a3_texture_chain(self, rgb5a3_levels, tex_name):
        """Build Wii RGB5A3 images (pfmt 21, order 103, tiled 4x4)."""
        if rgb5a3_levels is None:
            rgb5a3_levels = []

        base_img_idx = None
        mip_img_indices = []

        for level_idx, (comp_data, tw, th) in enumerate(rgb5a3_levels):
            pixel_mb = self._add_mem(MO_EXTERNAL_INFO_ENTRY, comp_data, align_type=1)
            img_size = len(comp_data)
            # RGB5A3 stores two bytes per pixel.
            stride = tw * 2

            img_idx = self._add_obj(MO_IMAGE, [
                (2, tw, 'UnsignedInt', 4),
                (3, th, 'UnsignedInt', 4),
                (4, 4, 'UnsignedInt', 4),
                (5, 0, 'UnsignedInt', 4),
                (6, 103, 'UnsignedInt', 4),       # GameCube tiled image order
                (7, 5, 'UnsignedInt', 4),
                (8, 5, 'UnsignedInt', 4),
                (9, 5, 'UnsignedInt', 4),
                (10, 1, 'UnsignedInt', 4),
                (11, 21, 'Enum', 4),              # Wii RGB5A3
                (12, img_size, 'Int', 4),
                (13, pixel_mb, 'MemoryRef', 4),
                (14, -1, 'MemoryRef', 4),
                (15, 1, 'Bool', 1),
                (16, 0, 'UnsignedInt', 4),
                (17, -1, 'ObjectRef', 4),          # no CLUT
                (18, 0, 'UnsignedInt', 4),
                (19, stride, 'Int', 4),
                (20, 1, 'Bool', 1),                # Native Alchemy tiled-image flag
                (21, 0, 'UnsignedInt', 4),
                (22, tex_name, 'String', 4),
            ])

            if level_idx == 0:
                base_img_idx = img_idx
            else:
                mip_img_indices.append(img_idx)

        if mip_img_indices:
            mip_data = struct.pack("<" + "i" * len(mip_img_indices), *mip_img_indices)
            mip_mb = self._add_mem(MO_OBJECT, mip_data)
            mipmap_list_idx = self._add_obj(MO_MIPMAP_LIST, [
                (2, len(mip_img_indices), 'Int', 4),
                (3, len(mip_img_indices), 'Int', 4),
                (4, mip_mb, 'MemoryRef', 4),
            ])
        else:
            mipmap_list_idx = -1

        num_levels = len(rgb5a3_levels)
        texture_attr_idx = self._add_obj(MO_TEXTURE_ATTR, [
            (2, 0, 'Short', 2),
            (4, 0, 'UnsignedInt', 4),
            (5, 1, 'Enum', 4),
            (6, 3, 'Enum', 4),
            (7, 1, 'Enum', 4),
            (8, 1, 'Enum', 4),
            (10, 0, 'Enum', 4),
            (11, 0, 'Enum', 4),
            (12, base_img_idx if base_img_idx is not None else -1, 'ObjectRef', 4),
            (13, 0, 'Bool', 1),
            (14, -1, 'ObjectRef', 4),
            (15, num_levels, 'Int', 4),
            (16, mipmap_list_idx, 'ObjectRef', 4),
        ])

        texture_bind_idx = self._add_obj(MO_TEXTURE_BIND_ATTR, [
            (2, 0, 'Short', 2),
            (4, texture_attr_idx, 'ObjectRef', 4),
            (5, 0, 'Int', 4),
        ])

        return texture_attr_idx, texture_bind_idx

    def _build_clut_texture_chain(self, palette_data, index_data, width, height, tex_name):
        """Build CLUT image + igClut + texture attr + bind.

        PS2 PSMT8 palette-based texture. No DXT compression — palette is raw
        RGBA bytes so no RGB565/BGR565 ambiguity. Works in both XML2 and MUA.

        Args:
            palette_data: 1024 bytes (256 RGBA palette entries)
            index_data: width*height bytes (palette indices)
            width: image width
            height: image height
            tex_name: texture name string

        Returns:
            (tex_attr_idx, tex_bind_idx)
        """
        # Build igClut object with palette data
        palette_mb = self._add_mem(MO_EXTERNAL_INFO_ENTRY, palette_data, align_type=1)
        clut_idx = self._add_obj(MO_CLUT, [
            (2, 7, 'Enum', 4),              # _fmt = RGBA_8888_32
            (3, 256, 'UnsignedInt', 4),      # _numEntries
            (4, 4, 'Int', 4),                # _stride (bytes per entry)
            (5, palette_mb, 'MemoryRef', 4), # _pData
            (6, 1024, 'Int', 4),             # _clutSize (256 * 4)
        ])

        # Build igImage with PSMT8 format
        pixel_mb = self._add_mem(MO_EXTERNAL_INFO_ENTRY, index_data, align_type=1)
        img_size = len(index_data)  # width * height
        stride = width  # 1 byte per pixel for indexed

        base_img_idx = self._add_obj(MO_IMAGE, [
            (2, width, 'UnsignedInt', 4),
            (3, height, 'UnsignedInt', 4),
            (4, 1, 'UnsignedInt', 4),       # components=1 (indexed, 1 byte per pixel)
            (5, 1, 'UnsignedInt', 4),        # orderPreservation
            (6, 100, 'UnsignedInt', 4),      # _order = DEFAULT
            (7, 0, 'UnsignedInt', 4),        # bitsRed=0 (indexed)
            (8, 0, 'UnsignedInt', 4),        # bitsGreen=0
            (9, 0, 'UnsignedInt', 4),        # bitsBlue=0
            (10, 0, 'UnsignedInt', 4),       # bitsAlpha=0
            (11, 65536, 'Enum', 4),          # pfmt=65536 (PSMT8)
            (12, img_size, 'Int', 4),        # imageSize
            (13, pixel_mb, 'MemoryRef', 4),  # pixel data (indices)
            (14, -1, 'MemoryRef', 4),        # unused
            (15, 1, 'Bool', 1),              # localImage=true
            (16, 0, 'UnsignedInt', 4),
            (17, clut_idx, 'ObjectRef', 4),  # -> igClut
            (18, 8, 'UnsignedInt', 4),       # bitsPerPixel=8
            (19, stride, 'Int', 4),          # bytesPerRow
            (20, 0, 'Bool', 1),              # compressed=false
            (21, 0, 'UnsignedInt', 4),
            (22, tex_name, 'String', 4),
        ])

        # CLUT textures don't have mipmaps (single level)
        texture_attr_idx = self._add_obj(MO_TEXTURE_ATTR, [
            (2, 0, 'Short', 2),
            (4, 0, 'UnsignedInt', 4),
            (5, 1, 'Enum', 4),
            (6, 3, 'Enum', 4),
            (7, 1, 'Enum', 4),
            (8, 1, 'Enum', 4),
            (10, 0, 'Enum', 4),
            (11, 0, 'Enum', 4),
            (12, base_img_idx, 'ObjectRef', 4),
            (13, 0, 'Bool', 1),
            (14, -1, 'ObjectRef', 4),
            (15, 1, 'Int', 4),               # imageCount=1 (no mipmaps)
            (16, -1, 'ObjectRef', 4),         # no mipmap list
        ])

        texture_bind_idx = self._add_obj(MO_TEXTURE_BIND_ATTR, [
            (2, 0, 'Short', 2),
            (4, texture_attr_idx, 'ObjectRef', 4),
            (5, 0, 'Int', 4),
        ])

        return texture_attr_idx, texture_bind_idx

    def _build_material(self, material):
        """Build igMaterialAttr."""
        diffuse = material.get('diffuse', (0.8, 0.8, 0.8, 1.0))
        ambient = material.get('ambient', (0.8, 0.8, 0.8, 1.0))
        specular = material.get('specular', (0.0, 0.0, 0.0, 1.0))
        emission = material.get('emission', (0.0, 0.0, 0.0, 1.0))
        shininess = material.get('shininess', 0.0)
        flags = material.get('flags', 31)
        priority = int(material.get('priority', 0))

        return self._add_obj(MO_MATERIAL_ATTR, [
            (2, priority, 'Short', 2),
            (4, shininess, 'Float', 4),
            (5, diffuse, 'Vec4f', 16),
            (6, ambient, 'Vec4f', 16),
            (7, specular, 'Vec4f', 16),
            (8, emission, 'Vec4f', 16),
            (9, flags, 'UnsignedInt', 4),
        ])

    # =========================================================================
    # Scene graph node builders (Pattern B: per-unit BMS)
    # =========================================================================

    def _build_unit_bms(self, child_idx, bms_int_list_idx):
        """Build an igBlendMatrixSelect node with a single child and shared palette.

        Each unit (body, body_outline, segment, segment_outline) gets its own BMS
        in the Pattern B (Cable 11501 / 3ds Max) scene graph structure.
        """
        child_data = struct.pack("<i", child_idx)
        child_mb = self._add_mem(MO_OBJECT, child_data)
        children_list = self._add_obj(MO_NODE_LIST, [
            (2, 1, 'Int', 4),
            (3, 1, 'Int', 4),
            (4, child_mb, 'MemoryRef', 4),
        ])

        vb_state_idx = self._add_obj(MO_VERTEX_BLEND_STATE_ATTR, [
            (2, 0, 'Short', 2),
            (4, 1, 'Bool', 1),
        ])
        attr_data = struct.pack("<i", vb_state_idx)
        attr_mb = self._add_mem(MO_OBJECT, attr_data)
        attr_list_idx = self._add_obj(MO_ATTR_LIST, [
            (2, 1, 'Int', 4),
            (3, 1, 'Int', 4),
            (4, attr_mb, 'MemoryRef', 4),
        ])

        return self._add_obj(MO_BLEND_MATRIX_SELECT, [
            (2, '', 'String', 4),
            (3, -1, 'ObjectRef', 4),
            (5, 0, 'Int', 4),
            (7, children_list, 'ObjectRef', 4),
            (8, attr_list_idx, 'ObjectRef', 4),
            (9, 0, 'Bool', 1),
            (10, bms_int_list_idx, 'ObjectRef', 4),
            (11, _IDENTITY_MATRIX, 'Matrix44f', 64),
            (12, _IDENTITY_MATRIX, 'Matrix44f', 64),
        ])

    def _build_render_state_attrs(self, mat_props):
        """Build render state attr objects from material properties dict.

        Conditionally emits blend, alpha, lighting, and cull face attrs
        based on what properties are present in the material dict.
        Mirrors the pattern used by the map exporter (igb_builder.py).

        Args:
            mat_props: Dict with keys like 'blend_enabled', 'alpha_test_enabled',
                       'lighting_enabled', 'cull_face_enabled', etc.

        Returns:
            List of meta-object indices to append to the unit's attr list.
        """
        attrs = []

        # Every vanilla game skin — and every working MUA mannequin — carries
        # ALL SIX of these render-state attrs in its attr set (blend, blend
        # function, alpha state, alpha function, lighting, cull face), in this
        # exact order. Without them the MUA character-select viewer renders the
        # skin INVISIBLY (no cull/lighting/blend state to draw with); in-game
        # herostat actors happen to be more forgiving, which hid the gap.
        # The actor importer doesn't capture these states, so mat_props is
        # usually empty on re-export — emit the vanilla opaque-character
        # defaults, overridden by any material-stored values from import.
        blend_on = bool(mat_props.get('blend_enabled', True))
        alpha_on = bool(mat_props.get('alpha_test_enabled', True))
        lighting_on = bool(mat_props.get('lighting_enabled', True))
        cull_on = bool(mat_props.get('cull_face_enabled', True))

        # Blend state
        attrs.append(self._add_obj(MO_BLEND_STATE_ATTR, [
            (2, 0, 'Short', 2),
            (4, int(blend_on), 'Bool', 1),
        ]))

        # Blend function (SRC_ALPHA / ONE_MINUS_SRC_ALPHA)
        attrs.append(self._add_obj(MO_BLEND_FUNCTION_ATTR, [
            (2, 0, 'Short', 2),
            (4, mat_props.get('blend_src', 4), 'Enum', 4),
            (5, mat_props.get('blend_dst', 5), 'Enum', 4),
            (6, 0, 'Enum', 4),
            (7, -1, 'ObjectRef', 4),
            (8, 0, 'UnsignedChar', 1),
            (9, 0, 'Short', 2),
            (11, 0, 'Enum', 4),
            (12, 0, 'Enum', 4),
            (13, 0, 'Enum', 4),
            (14, 0, 'Enum', 4),
        ]))

        # Alpha state
        attrs.append(self._add_obj(MO_ALPHA_STATE_ATTR, [
            (2, 0, 'Short', 2),
            (4, int(alpha_on), 'Bool', 1),
        ]))

        # Alpha function (GEQUAL, ref 0.5)
        attrs.append(self._add_obj(MO_ALPHA_FUNCTION_ATTR, [
            (2, 0, 'Short', 2),
            (4, mat_props.get('alpha_func', 6), 'Enum', 4),
            (5, mat_props.get('alpha_ref', 0.5), 'Float', 4),
        ]))

        # Lighting state
        attrs.append(self._add_obj(MO_LIGHTING_STATE_ATTR, [
            (2, 0, 'Short', 2),
            (4, int(lighting_on), 'Bool', 1),
        ]))

        # Cull face
        attrs.append(self._add_obj(MO_CULL_FACE_ATTR, [
            (2, 0, 'Short', 2),
            (4, int(cull_on), 'Bool', 1),
            (5, mat_props.get('cull_face_mode', 0), 'Enum', 4),
        ]))

        return attrs

    def _build_unit_attrset(self, geom_idx, attr_indices, name=''):
        """Build an igAttrSet with geometry child and rendering attributes.

        Each unit gets its own AttrSet containing the appropriate rendering
        state attrs (main or outline) and the geometry node as its child.
        """
        child_data = struct.pack("<i", geom_idx)
        child_mb = self._add_mem(MO_OBJECT, child_data)
        children_list = self._add_obj(MO_NODE_LIST, [
            (2, 1, 'Int', 4),
            (3, 1, 'Int', 4),
            (4, child_mb, 'MemoryRef', 4),
        ])

        attr_data = struct.pack("<" + "i" * len(attr_indices), *attr_indices)
        attr_mb = self._add_mem(MO_OBJECT, attr_data)
        attr_list = self._add_obj(MO_ATTR_LIST, [
            (2, len(attr_indices), 'Int', 4),
            (3, len(attr_indices), 'Int', 4),
            (4, attr_mb, 'MemoryRef', 4),
        ])

        return self._add_obj(MO_ATTR_SET, [
            (2, name, 'String', 4),
            (3, -1, 'ObjectRef', 4),
            (5, 0, 'Int', 4),
            (7, children_list, 'ObjectRef', 4),
            (8, attr_list, 'ObjectRef', 4),
            (9, 0, 'Bool', 1),
        ])

    def _build_group_node(self, name, child_indices):
        """Build an igGroup node with named children.

        Used for root igGroup "igActor01", body wrapper groups,
        and inner groups within segments.
        """
        n = len(child_indices)
        child_data = struct.pack("<" + "i" * n, *child_indices)
        child_mb = self._add_mem(MO_OBJECT, child_data)
        children_list = self._add_obj(MO_NODE_LIST, [
            (2, n, 'Int', 4),
            (3, n, 'Int', 4),
            (4, child_mb, 'MemoryRef', 4),
        ])

        return self._add_obj(MO_GROUP, [
            (2, name, 'String', 4),
            (3, -1, 'ObjectRef', 4),
            (5, 0, 'Int', 4),
            (7, children_list, 'ObjectRef', 4),
        ])

    def _build_segment_node(self, name, flags, child_idx):
        """Build an igSegment node wrapping a single child (inner igGroup).

        Used for segment and segment outline entries in the scene graph.
        """
        child_data = struct.pack("<i", child_idx)
        child_mb = self._add_mem(MO_OBJECT, child_data)
        children_list = self._add_obj(MO_NODE_LIST, [
            (2, 1, 'Int', 4),
            (3, 1, 'Int', 4),
            (4, child_mb, 'MemoryRef', 4),
        ])

        return self._add_obj(MO_SEGMENT, [
            (2, name, 'String', 4),
            (3, -1, 'ObjectRef', 4),
            (5, flags, 'Int', 4),
            (7, children_list, 'ObjectRef', 4),
        ])

    # =========================================================================
    # Core allocation methods (same pattern as IGBBuilder)
    # =========================================================================

    def _empty_list(self, mo_idx):
        """Add an empty igObjectList-derived object."""
        return self._add_obj(mo_idx, [
            (2, 0, 'Int', 4),
            (3, 0, 'Int', 4),
            (4, -1, 'MemoryRef', 4),
        ])

    def _ref_list(self, mo_idx, refs):
        """Add an igObjectList-derived object containing object refs."""
        data = struct.pack("<" + "i" * len(refs), *refs)
        mb = self._add_mem(MO_OBJECT, data)
        return self._add_obj(mo_idx, [
            (2, len(refs), 'Int', 4),
            (3, len(refs), 'Int', 4),
            (4, mb, 'MemoryRef', 4),
        ])

    def _build_bindpose_animation(self, skeleton_idx, skeleton_data, locals_):
        """Build the constant bind-pose igAnimation (Max-exporter style).

        One track per bone carrying the parent-local bind rotation and
        translation; no transform sources (constant), duration 0.
        """
        bones = skeleton_data['bones']
        n = len(bones)

        idmap = struct.pack("<" + "i" * n, *range(n))
        map_mb = self._add_mem(MO_OBJECT, idmap)
        binding_idx = self._add_obj(MO_ANIMATION_BINDING, [
            (2, skeleton_idx, 'ObjectRef', 4),
            (3, map_mb, 'MemoryRef', 4),
            (4, n, 'Int', 4),
            (5, -1, 'ObjectRef', 4),
            (6, -1, 'ObjectRef', 4),
        ])
        binding_list_idx = self._ref_list(MO_ANIMATION_BINDING_LIST,
                                          [binding_idx])

        track_idxs = []
        for bone, (quat, trans) in zip(bones, locals_):
            track_idxs.append(self._add_obj(MO_ANIMATION_TRACK, [
                (2, bone['name'], 'String', 4),
                (3, -1, 'ObjectRef', 4),
                (4, quat, 'Vec4f', 16),
                (5, trans, 'Vec3f', 12),
            ]))
        track_list_idx = self._ref_list(MO_ANIMATION_TRACK_LIST, track_idxs)
        trans_def_idx = self._empty_list(MO_ANIMATION_TRANSITION_DEF_LIST)

        return self._add_obj(MO_ANIMATION, [
            (2, 'igActor01_Animation01', 'String', 4),
            (3, 0, 'Int', 4),
            (4, binding_list_idx, 'ObjectRef', 4),
            (5, track_list_idx, 'ObjectRef', 4),
            (6, trans_def_idx, 'ObjectRef', 4),
            (7, 0, 'Long', 8),
            (8, 0, 'Long', 8),
            (9, 0, 'Long', 8),
            (10, -1, 'ObjectRef', 4),
        ])

    def _build_actor_graph(self, skeleton_idx, skeleton_data, skin_idx,
                           anim_db_idx, bindpose_anim_idx, bms_palette,
                           locals_):
        """Build the actor/combiner graph that gives the ENGINE per-skin
        animation retargeting (the reason 3ds Max-exported skins ground at
        any skeleton size while bare native-style skins float).

        Mirrors a v4 Max export (210xxcableMUA2.igb): igActorInfo ->
        igActorList[igActor] / combinerList[igAnimationCombiner] /
        appearanceList[igAppearance]; combiner carries per-bone parent-local
        bind transforms + world bind matrices.
        """
        bones = skeleton_data['bones']
        n = len(bones)

        # --- igAnimationState referencing the bind-pose animation ---
        state_idx = self._add_obj(MO_ANIMATION_STATE, [
            (2, bindpose_anim_idx, 'ObjectRef', 4),
            (3, 0, 'Enum', 4),
            (4, 4, 'Enum', 4),
            (5, 0, 'Enum', 4),
            (6, -1, 'ObjectRef', 4),
            (7, 0, 'Bool', 1),
            (8, 0.0, 'Float', 4),
            (9, 0, 'Long', 8),
            (10, 0, 'Long', 8),
            (11, 1.0, 'Float', 4),
            (12, 0, 'Long', 8),
            (13, 0, 'Long', 8),
            (14, 0.0, 'Float', 4),
            (15, 0.0, 'Float', 4),
            (16, 0, 'Long', 8),
            (17, 0, 'Long', 8),
        ])
        state_list_idx = self._ref_list(MO_ANIMATION_STATE_LIST, [state_idx])

        # --- per-bone combiner infos, each wrapped in a 1-entry list ---
        bi_list_idxs = []
        for quat, trans in locals_:
            bi_idx = self._add_obj(MO_COMBINER_BONE_INFO, [
                (2, state_idx, 'ObjectRef', 4),
                (3, -1, 'ObjectRef', 4),
                (4, quat, 'Vec4f', 16),
                (5, trans, 'Vec3f', 12),
                (6, 0, 'Int', 4),
                (7, 0, 'Bool', 1),
            ])
            bi_list_idxs.append(
                self._ref_list(MO_COMBINER_BONE_INFO_LIST, [bi_idx]))
        bill_idx = self._ref_list(MO_COMBINER_BONE_INFO_LIST_LIST,
                                  bi_list_idxs)

        int_list_idx = self._build_int_list([0] * n)

        # --- combiner buffers ---
        quat_buf = b"".join(struct.pack("<4f", *q) for q, _t in locals_)
        quat_mb = self._add_mem(MO_OBJECT, quat_buf)
        ident_bones = struct.pack("<16f", *_IDENTITY44) * n
        identA_mb = self._add_mem(MO_OBJECT, ident_bones)
        world_buf = b"".join(struct.pack("<16f", *_world_bind_matrix(b))
                             for b in bones)
        world_mb = self._add_mem(MO_OBJECT, world_buf)
        n_palette = max(len(bms_palette), 1)
        ident_palette = struct.pack("<16f", *_IDENTITY44) * n_palette
        palette_mb = self._add_mem(MO_OBJECT, ident_palette)

        combiner_idx = self._add_obj(MO_ANIMATION_COMBINER, [
            (2, 'combiner_igActor01', 'String', 4),
            (3, skeleton_idx, 'ObjectRef', 4),
            (4, bill_idx, 'ObjectRef', 4),
            (5, int_list_idx, 'ObjectRef', 4),
            (6, state_list_idx, 'ObjectRef', 4),
            (7, quat_mb, 'MemoryRef', 4),
            (8, identA_mb, 'MemoryRef', 4),
            (9, 0, 'Long', 8),
            (10, 1, 'Bool', 1),
            (12, world_mb, 'MemoryRef', 4),
            (13, palette_mb, 'MemoryRef', 4),
        ])

        modifier_list_idx = self._empty_list(MO_ANIMATION_MODIFIER_LIST)

        # --- appearance (skin reference + empty sub-lists, Max-style) ---
        appearance_idx = self._add_obj(MO_APPEARANCE, [
            (2, 'appearance_igActor01', 'String', 4),
            (3, skin_idx if skin_idx is not None else -1, 'ObjectRef', 4),
            (4, self._empty_list(MO_SKIN_LIST), 'ObjectRef', 4),
            (5, self._empty_list(MO_MVMBS_LIST), 'ObjectRef', 4),
            (6, self._empty_list(MO_STRING_OBJ_LIST), 'ObjectRef', 4),
            (7, self._empty_list(MO_NODE_LIST), 'ObjectRef', 4),
        ])

        # --- igActor scene node (empty children, identity transform) ---
        actor_ident_bones = self._add_mem(
            MO_OBJECT, struct.pack("<16f", *_IDENTITY44) * n)
        actor_ident_palette = self._add_mem(
            MO_OBJECT, struct.pack("<16f", *_IDENTITY44) * n_palette)
        actor_idx = self._add_obj(MO_ACTOR, [
            (2, 'igActor01', 'String', 4),
            (3, -1, 'ObjectRef', 4),
            (5, 0, 'Int', 4),
            (6, self._empty_list(MO_NODE_LIST), 'ObjectRef', 4),
            (7, combiner_idx, 'ObjectRef', 4),
            (8, actor_ident_bones, 'MemoryRef', 4),
            (9, actor_ident_palette, 'MemoryRef', 4),
            (10, appearance_idx, 'ObjectRef', 4),
            (11, -1, 'ObjectRef', 4),
            (12, modifier_list_idx, 'ObjectRef', 4),
            (13, _IDENTITY44, 'Matrix44f', 64),
        ])

        actor_list_idx = self._ref_list(MO_ACTOR_LIST, [actor_idx])
        combiner_list_idx = self._ref_list(MO_ANIMATION_COMBINER_LIST,
                                           [combiner_idx])
        appearance_list_idx = self._ref_list(MO_APPEARANCE_LIST,
                                             [appearance_idx])

        return self._add_obj(MO_ACTOR_INFO, [
            (2, 'igActor01', 'String', 4),
            (4, 1, 'Bool', 1),
            (5, -1, 'ObjectRef', 4),
            (6, actor_list_idx, 'ObjectRef', 4),
            (7, anim_db_idx, 'ObjectRef', 4),
            (8, combiner_list_idx, 'ObjectRef', 4),
            (9, appearance_list_idx, 'ObjectRef', 4),
        ])

    def _add_obj(self, meta_obj_idx, fields):
        """Add an object, return its index."""
        idx = len(self._obj_list)
        self._obj_list.append(('obj', meta_obj_idx, fields))
        self._ref_infos.append({
            'is_object': True,
            'type_index': meta_obj_idx,
            'type_name': SKIN_META_OBJECTS[meta_obj_idx][0].encode(),
            'mem_pool_handle': -1,
        })
        return idx

    def _add_mem(self, type_idx, data, align_type=-1, pool=-1):
        """Add a memory block, return its index."""
        idx = len(self._obj_list)
        self._obj_list.append(('mem', type_idx, data))
        self._ref_infos.append({
            'is_object': False,
            'type_index': type_idx,
            'type_name': SKIN_META_OBJECTS[type_idx][0].encode(),
            'mem_size': len(data),
            'ref_counted': 1,
            'align_type_idx': align_type,
            'mem_pool_handle': pool,
        })
        return idx

    # =========================================================================
    # Writer initialization and finalization
    # =========================================================================

    def _init_writer(self):
        """Create and configure a new IGBWriter with skin type registry."""
        writer = IGBWriter()
        writer.version = 6
        writer.endian = "<"
        writer.has_info = True
        writer.has_external = True
        writer.shared_entries = True
        writer.has_memory_pool_names = True

        writer.meta_fields = []
        for name, major, minor in META_FIELDS:
            writer.meta_fields.append(MetaFieldDef(name, major, minor))

        # CRITICAL: the engine validates meta defs at load even when unused.
        # OFF  -> exact original 60-type table (proven in-game)
        # ANIM -> +6 bind-pose animation types (v6-precedented, mirrors the
        #         known-compatible 0103.igb community skin)
        # FULL -> +14 combiner/actor types (crashed XML2 — experimental)
        mode = getattr(self, '_actor_graph_mode', 'OFF')
        if mode == 'FULL':
            metas = SKIN_META_OBJECTS
        elif mode == 'ANIM':
            metas = SKIN_META_OBJECTS[:N_ANIM_METAS]
        else:
            metas = SKIN_META_OBJECTS[:N_STABLE_METAS]
        writer.meta_objects = []
        for name, major, minor, parent_idx, slot_count, fields in metas:
            field_defs = [MetaObjectFieldDef(ti, slot, size) for ti, slot, size in fields]
            writer.meta_objects.append(MetaObjectDef(
                name, major, minor, field_defs, parent_idx, slot_count
            ))

        writer.alignment_data = ALIGNMENT_BUFFER
        writer.external_dirs = [b"system", b"system"]  # vanilla has 2 entries
        writer.external_dirs_unk = 1
        writer.memory_pool_names = list(MEMORY_POOL_NAMES)

        return writer

    def _finalize_writer(self, writer, info_list_idx):
        """Convert internal obj_list/ref_infos into writer structures.

        Uses 1:1 entry mapping (same as the working map builder).
        Each object/memory block gets its own unique entry.
        """
        entries = []
        index_map = []

        for i, (kind, type_idx, data) in enumerate(self._obj_list):
            if kind == 'obj':
                entries.append(EntryDef(MO_OBJECT_DIR_ENTRY, [0, type_idx, -1]))
            else:
                ri = self._ref_infos[i]
                entries.append(EntryDef(MO_MEMORY_DIR_ENTRY, [
                    0, ri['mem_size'], ri['type_index'],
                    ri['ref_counted'], ri['align_type_idx'], ri['mem_pool_handle']
                ]))
            index_map.append(len(entries) - 1)

        writer.entries = entries
        writer.index_map = index_map
        writer.info_list_index = info_list_idx
        writer.ref_info = self._ref_infos

        writer.objects = []
        for i, (kind, type_idx, data) in enumerate(self._obj_list):
            if kind == 'obj':
                raw_fields = []
                for slot, val, sname, size in data:
                    fd = ObjectFieldDef(
                        slot,
                        sname.encode() if isinstance(sname, str) else sname,
                        size,
                    )
                    raw_fields.append((slot, val, fd))
                writer.objects.append(ObjectDef(type_idx, raw_fields))
            else:
                writer.objects.append(MemoryBlockDef(data))

    # =========================================================================
    # Data packing helpers
    # =========================================================================

    def _pack_positions(self, positions):
        data = bytearray(len(positions) * 12)
        for i, (x, y, z) in enumerate(positions):
            struct.pack_into("<fff", data, i * 12, x, y, z)
        return bytes(data)

    def _pack_normals(self, normals):
        data = bytearray(len(normals) * 12)
        for i, (nx, ny, nz) in enumerate(normals):
            struct.pack_into("<fff", data, i * 12, nx, ny, nz)
        return bytes(data)

    def _pack_uvs(self, uvs):
        data = bytearray(len(uvs) * 8)
        for i, (u, v) in enumerate(uvs):
            struct.pack_into("<ff", data, i * 8, u, v)
        return bytes(data)

    def _pack_indices(self, indices):
        data = bytearray(len(indices) * 2)
        for i, idx in enumerate(indices):
            struct.pack_into("<H", data, i * 2, idx)
        return bytes(data)

    def _pack_blend_weights(self, weights):
        """Pack 4 x float32 per vertex (16 bpv)."""
        data = bytearray(len(weights) * 16)
        for i, (w0, w1, w2, w3) in enumerate(weights):
            struct.pack_into("<ffff", data, i * 16, w0, w1, w2, w3)
        return bytes(data)

    def _pack_blend_indices(self, indices):
        """Pack 4 x uint8 per vertex (4 bpv)."""
        data = bytearray(len(indices) * 4)
        for i, (i0, i1, i2, i3) in enumerate(indices):
            struct.pack_into("BBBB", data, i * 4, i0, i1, i2, i3)
        return bytes(data)

    def _build_ext_indexed_data(self):
        return struct.pack("<" + "I" * 20, *([0xFFFFFFFF] * 20))

    def _patch_ext_indexed(self, ext_mb_idx, pos_mb, norm_mb, uv_mb):
        _, type_idx, data = self._obj_list[ext_mb_idx]
        slots = list(struct.unpack("<" + "I" * 20, data))
        slots[0] = pos_mb
        if norm_mb >= 0:
            slots[1] = norm_mb
        if uv_mb >= 0:
            slots[11] = uv_mb
        new_data = struct.pack("<" + "I" * 20, *slots)
        self._obj_list[ext_mb_idx] = ('mem', type_idx, new_data)


def _default_material():
    return {
        'diffuse': (0.8, 0.8, 0.8, 1.0),
        'ambient': (0.8, 0.8, 0.8, 1.0),
        'specular': (0.0, 0.0, 0.0, 1.0),
        'emission': (0.0, 0.0, 0.0, 1.0),
        'shininess': 0.0,
        'flags': 0,
    }


def _default_outline_material():
    """Default material for outline meshes — all black, matching vanilla."""
    return {
        'diffuse': (0.0, 0.0, 0.0, 1.0),
        'ambient': (0.0, 0.0, 0.0, 1.0),
        'specular': (0.0, 0.0, 0.0, 1.0),
        'emission': (0.0, 0.0, 0.0, 0.0),
        'shininess': 0.0,
        'flags': 0,
    }
