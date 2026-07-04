"""High-level IGB scene builder for XML2 PC export.

Constructs a complete in-memory IGB structure from mesh/material/texture data.
Defines the XML2 type registry (47 meta-fields, 50 meta-objects) and builds
the scene graph with all required objects and memory blocks.

Supports multi-material meshes: each submesh gets its own igAttrSet branch
with material, texture, and geometry, all under a shared root igGroup.

The output is an IGBWriter instance ready to be serialized to disk.
"""

import struct
from ..igb_format.igb_writer import (
    IGBWriter, MetaFieldDef, MetaObjectDef, MetaObjectFieldDef,
    EntryDef, ObjectDef, ObjectFieldDef, MemoryBlockDef,
)
from .mesh_extractor import triangles_to_strip


# ============================================================================
# XML2 PC Type Registry — exactly matches what every XML2 IGB file uses
# ============================================================================

# 47 meta-field types (index -> (name, major, minor))
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

# Field type indices (shorthand for readability)
_ObjRef = 0     # igObjectRefMetaField
_Int = 1        # igIntMetaField
_Bool = 2       # igBoolMetaField
_String = 4     # igStringMetaField
_Enum = 6       # igEnumMetaField
_MemRef = 7     # igMemoryRefMetaField
_UChar = 8      # igUnsignedCharMetaField
_UInt = 9       # igUnsignedIntMetaField
_Struct = 11    # igStructMetaField
_Long = 12      # igLongMetaField
_Float = 16     # igFloatMetaField
_Short = 17     # igShortMetaField
_Vec3f = 36     # igVec3fMetaField
_Vec4f = 38     # igVec4fMetaField

# 50 meta-object definitions
# Each: (name, major, minor, parent_index, slot_count, [(type_idx, slot, size), ...])
META_OBJECTS = [
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
    # [9] igDataList
    ("igDataList", 1, 0, 0, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [10] igFloatList
    ("igFloatList", 1, 0, 9, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [11] igInfo
    ("igInfo", 1, 0, 1, 5, [(_String, 2, 4), (_Bool, 4, 1)]),
    # [12] igCollideHull
    ("igCollideHull", 1, 0, 11, 9,
     [(_String, 2, 4), (_Bool, 4, 1), (_ObjRef, 5, 4), (_ObjRef, 6, 4), (_Int, 7, 4), (_Int, 8, 4)]),
    # [13] igVertexArray
    ("igVertexArray", 1, 0, 0, 6,
     [(_MemRef, 2, 4), (_UInt, 3, 4), (_UInt, 4, 4), (_UInt, 5, 4)]),
    # [14] igVertexArray1_1
    ("igVertexArray1_1", 1, 0, 13, 12,
     [(_MemRef, 2, 4), (_UInt, 3, 4), (_UInt, 4, 4), (_UInt, 5, 4),
      (_Struct, 6, 4), (_MemRef, 7, 4), (_MemRef, 8, 4), (_MemRef, 10, 4)]),
    # [15] igPropertyValue
    ("igPropertyValue", 1, 0, 0, 2, []),
    # [16] igStringValue
    ("igStringValue", 1, 0, 15, 3, [(_String, 2, 4)]),
    # [17] igProperty
    ("igProperty", 1, 0, 0, 4, [(_ObjRef, 2, 4), (_ObjRef, 3, 4)]),
    # [18] igObjectList
    ("igObjectList", 1, 0, 9, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [19] igGraphPathList
    ("igGraphPathList", 1, 0, 18, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [20] igImage
    ("igImage", 1, 0, 0, 23,
     [(_UInt, 2, 4), (_UInt, 3, 4), (_UInt, 4, 4), (_UInt, 5, 4),
      (_UInt, 6, 4), (_UInt, 7, 4), (_UInt, 8, 4), (_UInt, 9, 4), (_UInt, 10, 4),
      (_Enum, 11, 4), (_Int, 12, 4), (_MemRef, 13, 4), (_MemRef, 14, 4),
      (_Bool, 15, 1), (_UInt, 16, 4), (_ObjRef, 17, 4), (_UInt, 18, 4),
      (_Int, 19, 4), (_Bool, 20, 1), (_UInt, 21, 4), (_String, 22, 4)]),
    # [21] igNode
    ("igNode", 1, 0, 1, 7,
     [(_String, 2, 4), (_ObjRef, 3, 4), (_Int, 5, 4)]),
    # [22] igGroup
    ("igGroup", 1, 0, 21, 8,
     [(_String, 2, 4), (_ObjRef, 3, 4), (_Int, 5, 4), (_ObjRef, 7, 4)]),
    # [23] igAttrSet
    ("igAttrSet", 1, 0, 22, 10,
     [(_String, 2, 4), (_ObjRef, 3, 4), (_Int, 5, 4), (_ObjRef, 7, 4),
      (_ObjRef, 8, 4), (_Bool, 9, 1)]),
    # [24] igUserInfo
    ("igUserInfo", 1, 0, 22, 9,
     [(_String, 2, 4), (_ObjRef, 3, 4), (_Int, 5, 4), (_ObjRef, 7, 4), (_ObjRef, 8, 4)]),
    # [25] igHashedUserInfo
    ("igHashedUserInfo", 1, 0, 24, 10,
     [(_String, 2, 4), (_ObjRef, 3, 4), (_Int, 5, 4), (_ObjRef, 7, 4), (_ObjRef, 8, 4)]),
    # [26] igNodeList
    ("igNodeList", 1, 0, 18, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [27] igPropertyList
    ("igPropertyList", 1, 0, 18, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [28] igGeometry
    ("igGeometry", 1, 0, 23, 11,
     [(_String, 2, 4), (_ObjRef, 3, 4), (_Int, 5, 4), (_ObjRef, 7, 4),
      (_ObjRef, 8, 4), (_Bool, 9, 1)]),
    # [29] igAttr
    ("igAttr", 1, 0, 0, 4, [(_Short, 2, 2)]),
    # [30] igVisualAttribute
    ("igVisualAttribute", 1, 0, 29, 4, [(_Short, 2, 2)]),
    # [31] igTextureBindAttr
    ("igTextureBindAttr", 1, 0, 30, 6,
     [(_Short, 2, 2), (_ObjRef, 4, 4), (_Int, 5, 4)]),
    # [32] igTextureList
    ("igTextureList", 1, 0, 18, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [33] igPropertyKey
    ("igPropertyKey", 1, 0, 0, 2, []),
    # [34] igStringKey
    ("igStringKey", 1, 0, 33, 3, [(_String, 2, 4)]),
    # [35] igSceneInfo
    ("igSceneInfo", 1, 0, 11, 12,
     [(_String, 2, 4), (_Bool, 4, 1), (_ObjRef, 5, 4), (_ObjRef, 6, 4),
      (_ObjRef, 7, 4), (_Long, 8, 8), (_Long, 9, 8), (_Vec3f, 10, 12), (_ObjRef, 11, 4)]),
    # [36] igInfoList
    ("igInfoList", 1, 0, 18, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [37] igAttrList
    ("igAttrList", 1, 0, 18, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [38] igGeometryAttr
    ("igGeometryAttr", 1, 0, 30, 13,
     [(_Short, 2, 2), (_ObjRef, 4, 4), (_ObjRef, 5, 4), (_Enum, 6, 4),
      (_UInt, 7, 4), (_UInt, 8, 4), (_ObjRef, 9, 4), (_Int, 10, 4),
      (_ObjRef, 11, 4), (_ObjRef, 12, 4)]),
    # [39] igGeometryAttr1_5
    ("igGeometryAttr1_5", 1, 0, 38, 14,
     [(_Short, 2, 2), (_ObjRef, 4, 4), (_ObjRef, 5, 4), (_Enum, 6, 4),
      (_UInt, 7, 4), (_UInt, 8, 4), (_ObjRef, 9, 4), (_Int, 10, 4),
      (_ObjRef, 11, 4), (_ObjRef, 12, 4), (_ObjRef, 13, 4)]),
    # [40] igIndexArray
    ("igIndexArray", 1, 0, 0, 7,
     [(_MemRef, 2, 4), (_UInt, 3, 4), (_Enum, 4, 4), (_UInt, 5, 4)]),
    # [41] igColorAttr
    ("igColorAttr", 1, 0, 30, 6, [(_Short, 2, 2), (_Vec4f, 4, 16)]),
    # [42] igTextureAttr
    ("igTextureAttr", 1, 0, 30, 19,
     [(_Short, 2, 2), (_UInt, 4, 4), (_Enum, 5, 4), (_Enum, 6, 4),
      (_Enum, 7, 4), (_Enum, 8, 4), (_Enum, 10, 4), (_Enum, 11, 4),
      (_ObjRef, 12, 4), (_Bool, 13, 1), (_ObjRef, 14, 4), (_Int, 15, 4),
      (_ObjRef, 16, 4)]),
    # [43] igTextureStateAttr
    ("igTextureStateAttr", 1, 0, 30, 6,
     [(_Short, 2, 2), (_Bool, 4, 1), (_Int, 5, 4)]),
    # [44] igMaterialAttr
    ("igMaterialAttr", 1, 0, 30, 10,
     [(_Short, 2, 2), (_Float, 4, 4), (_Vec4f, 5, 16), (_Vec4f, 6, 16),
      (_Vec4f, 7, 16), (_Vec4f, 8, 16), (_UInt, 9, 4)]),
    # [45] igImageMipMapList
    ("igImageMipMapList", 1, 0, 18, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [46] igVolume
    ("igVolume", 1, 0, 0, 2, []),
    # [47] igAABox
    ("igAABox", 1, 0, 46, 4, [(_Vec3f, 2, 12), (_Vec3f, 3, 12)]),
    # [48] igPrimLengthArray
    ("igPrimLengthArray", 1, 0, 0, 5,
     [(_MemRef, 2, 4), (_UInt, 3, 4), (_UInt, 4, 4)]),
    # [49] igPrimLengthArray1_1
    ("igPrimLengthArray1_1", 1, 0, 48, 5,
     [(_MemRef, 2, 4), (_UInt, 3, 4), (_UInt, 4, 4)]),
    # [50] igLightAttr (inherits igVisualAttribute[30] -> igAttr -> igObject)
    # Fields: lightType, position, ambient, diffuse, specular, direction,
    #         falloff, cutoff, attenuation, shininess, cachedPos, cachedDir
    ("igLightAttr", 1, 0, 30, 20,
     [(_Short, 2, 2), (_Enum, 4, 4), (_Vec3f, 6, 12),
      (_Vec4f, 7, 16), (_Vec4f, 8, 16), (_Vec4f, 9, 16),
      (_Vec3f, 10, 12), (_Float, 11, 4), (_Float, 12, 4),
      (_Vec3f, 13, 12), (_Float, 14, 4), (_Vec3f, 15, 12), (_Vec3f, 16, 12)]),
    # [51] igLightList (inherits igObjectList[18] -> igDataList -> igObject)
    ("igLightList", 1, 0, 18, 5, [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [52] igLightSet (inherits igNode[21] -> igNamedObject -> igObject)
    # Slot 7 is the _lights ObjectRef (same slot as igGroup._childList)
    ("igLightSet", 1, 0, 21, 8,
     [(_String, 2, 4), (_ObjRef, 3, 4), (_Int, 5, 4), (_ObjRef, 7, 4)]),
    # [53] igLightStateAttr (inherits igVisualAttribute[30] -> igAttr -> igObject)
    # XML2 v2.5: slot 4=_light(ObjRef), slot 5=_enabled(Bool)
    ("igLightStateAttr", 1, 0, 30, 7,
     [(_Short, 2, 2), (_ObjRef, 4, 4), (_Bool, 5, 1)]),
    # [54] igLightStateAttrList (inherits igObjectList[18])
    ("igLightStateAttrList", 1, 0, 18, 5,
     [(_Int, 2, 4), (_Int, 3, 4), (_MemRef, 4, 4)]),
    # [55] igLightStateSet (inherits igGroup[22] -> igNode -> igNamedObject -> igObject)
    # Has slot 8 = _lightEnables (ObjectRef -> igLightStateAttrList)
    ("igLightStateSet", 1, 0, 22, 9,
     [(_String, 2, 4), (_ObjRef, 3, 4), (_Int, 5, 4), (_ObjRef, 7, 4),
      (_ObjRef, 8, 4)]),
    # [56] igBlendStateAttr (inherits igVisualAttribute[30] -> igAttr -> igObject)
    ("igBlendStateAttr", 1, 0, 30, 5,
     [(_Short, 2, 2), (_Bool, 4, 1)]),
    # [57] igBlendFunctionAttr (inherits igVisualAttribute[30])
    # 11 fields across 15 slots (slot 10 missing). Slots 8-14 are PS2-specific.
    ("igBlendFunctionAttr", 1, 0, 30, 15,
     [(_Short, 2, 2), (_Enum, 4, 4), (_Enum, 5, 4), (_Enum, 6, 4),
      (_ObjRef, 7, 4), (_UChar, 8, 1), (_Short, 9, 2),
      (_Enum, 11, 4), (_Enum, 12, 4), (_Enum, 13, 4), (_Enum, 14, 4)]),
    # [58] igAlphaStateAttr (inherits igVisualAttribute[30])
    ("igAlphaStateAttr", 1, 0, 30, 5,
     [(_Short, 2, 2), (_Bool, 4, 1)]),
    # [59] igAlphaFunctionAttr (inherits igVisualAttribute[30])
    ("igAlphaFunctionAttr", 1, 0, 30, 6,
     [(_Short, 2, 2), (_Enum, 4, 4), (_Float, 5, 4)]),
    # [60] igLightingStateAttr (inherits igVisualAttribute[30])
    ("igLightingStateAttr", 1, 0, 30, 5,
     [(_Short, 2, 2), (_Bool, 4, 1)]),
    # [61] igTextureMatrixStateAttr (inherits igVisualAttribute[30])
    ("igTextureMatrixStateAttr", 1, 0, 30, 6,
     [(_Short, 2, 2), (_Bool, 4, 1), (_Int, 5, 4)]),
    # [62] igCullFaceAttr (inherits igVisualAttribute[30])
    # slot 4: Bool (_enable: 1=culling on, 0=culling off)
    # slot 5: Enum (_cullFace: 0=FRONT, 1=BACK, 2=FRONT_AND_BACK)
    ("igCullFaceAttr", 1, 0, 30, 6,
     [(_Short, 2, 2), (_Bool, 4, 1), (_Enum, 5, 4)]),

    # [63] igClut (PS2 CLUT palette - universal texture format)
    # Parent: igObject (0). 7 own fields (slots 2-8), only 5 persistent (2-6).
    # Slots 7 (_clutDirty) and 8 (_isAlphaScaled) are runtime-only.
    ("igClut", 1, 0, 0, 9, [
        (_Enum, 2, 4),   # _fmt: palette pixel format (7 = RGBA_8888_32)
        (_UInt, 3, 4),   # _numEntries: palette entry count (256)
        (_Int, 4, 4),    # _stride: bytes per entry (4)
        (_MemRef, 5, 4), # _pData: palette data memory ref
        (_Int, 6, 4),    # _clutSize: total palette data size (1024)
    ]),
]

# XML2 PC alignment buffer (exact bytes from reference files)
ALIGNMENT_BUFFER = bytes.fromhex(
    "3d000000"  # size=61
    "01000000"  # count=1
    "03000000"  # mo_idx=3 (igObjectDirEntry - used for alignment type references)
    "10000000"  # alignment=16
    "0a000000"  # 10
    "0b000000"  # 11
    "56657274657841727261794461746100"  # "VertexArrayData\0"
    "496d61676544617461 00"              # "ImageData\0"
    "5665727465784461746100"             # "VertexData\0"
).replace(b" ", b"")

# XML2 memory pool names
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

# Meta-object indices (for convenience)
MO_OBJECT = 0
MO_OBJECT_DIR_ENTRY = 3
MO_MEMORY_DIR_ENTRY = 4
MO_EXTERNAL_INDEXED_ENTRY = 7
MO_EXTERNAL_INFO_ENTRY = 8
MO_DATA_LIST = 9
MO_FLOAT_LIST = 10
MO_VERTEX_ARRAY_1_1 = 14
MO_NODE = 21  # igNode — used as mem_type for collision triangle/tree data blocks
MO_STRING_VALUE = 16
MO_OBJECT_LIST = 18
MO_IMAGE = 20
MO_GROUP = 22
MO_ATTR_SET = 23
MO_NODE_LIST = 26
MO_GEOMETRY = 28
MO_TEXTURE_BIND_ATTR = 31
MO_TEXTURE_LIST = 32
MO_SCENE_INFO = 35
MO_INFO_LIST = 36
MO_ATTR_LIST = 37
MO_GEOMETRY_ATTR_1_5 = 39
MO_INDEX_ARRAY = 40
MO_COLOR_ATTR = 41
MO_TEXTURE_ATTR = 42
MO_TEXTURE_STATE_ATTR = 43
MO_MATERIAL_ATTR = 44
MO_MIPMAP_LIST = 45
MO_AABOX = 47
MO_PRIM_LENGTH_1_1 = 49
MO_COLLIDE_HULL = 12
MO_LIGHT_ATTR = 50
MO_LIGHT_LIST = 51
MO_LIGHT_SET = 52
MO_LIGHT_STATE_ATTR = 53
MO_LIGHT_STATE_ATTR_LIST = 54
MO_LIGHT_STATE_SET = 55
MO_BLEND_STATE_ATTR = 56
MO_BLEND_FUNCTION_ATTR = 57
MO_ALPHA_STATE_ATTR = 58
MO_ALPHA_FUNCTION_ATTR = 59
MO_LIGHTING_STATE_ATTR = 60
MO_TEX_MATRIX_STATE_ATTR = 61
MO_CULL_FACE_ATTR = 62
MO_CLUT = 63


class IGBBuilder:
    """Builds an IGB file for XML2 PC from high-level mesh/material/texture data.

    Supports single and multi-material meshes. Each submesh gets its own
    igAttrSet branch with material, texture, and geometry.

    Usage (multi-material):
        builder = IGBBuilder()
        writer = builder.build([
            {'mesh': submesh0, 'material': mat0, 'texture_levels': tex0, 'texture_name': 'diffuse0'},
            {'mesh': submesh1, 'material': mat1, 'texture_levels': tex1, 'texture_name': 'diffuse1'},
        ])
        writer.write("output.igb")
    """

    def __init__(self):
        self._obj_list = []   # unified list: ('obj'|'mem', type_idx, data)
        self._ref_infos = []  # per-item metadata for entries/index_map

    def build(self, submeshes, collision_data=None, lights=None):
        """Build a complete IGB structure for one or more submeshes.

        Args:
            submeshes: list of dicts, each with keys:
                'mesh': MeshExport instance
                    .positions: list of (x,y,z)
                    .normals: list of (nx,ny,nz)
                    .uvs: list of (u,v)
                    .indices: list of int (triangle list indices)
                    .bbox_min: (x,y,z)
                    .bbox_max: (x,y,z)
                    .name: string
                'material': dict with keys:
                    'diffuse': (r,g,b,a) 0.0-1.0
                    'ambient': (r,g,b,a)
                    'specular': (r,g,b,a)
                    'emission': (r,g,b,a)
                    'shininess': float
                'texture_levels': list of (compressed_dxt5_bytes, width, height)
                    or None for no texture (4x4 white placeholder created)
                'texture_name': str (texture name for igImage)
            collision_data: optional dict with collision hull data:
                'triangle_floats': bytes — packed triangle float data
                'num_triangles': int
                'tree_floats': bytes — packed BVH tree float data
                'num_tree_nodes_minus_1': int
            lights: optional list of dicts, each with keys:
                'name': str (light set name, e.g. "light01")
                'type': int (0=DIRECTIONAL, 1=POINT, 2=SPOT)
                'position': (x, y, z)
                'direction': (x, y, z)
                'diffuse': (r, g, b, a)
                'ambient': (r, g, b, a)
                'specular': (r, g, b, a)
                'attenuation': (constant, linear, quadratic)
                'falloff': float (spot falloff rate)
                'cutoff': float (spot cutoff angle)

        Returns:
            IGBWriter ready to write
        """
        # Reset state
        self._obj_list = []
        self._ref_infos = []
        # Per-build texture-chain dedup: a map with many objects sharing one
        # tileset used to emit a FULL igImage+mipmap copy per submesh. Cache the
        # built (attr, bind) by (unit, payload) so a shared texture is stored
        # once — the same dedup the v6/v8 skin builders already do.
        self._tex_chain_cache = {}

        writer = self._init_writer()

        # Node flags from game file analysis (101 files):
        #   igGeometry:     0 (map static), 0x10 (collidable), 0x14 (actor)
        #   igAttrSet:      0 or 2 (STATIC) — ~52% use flag 2
        #   igGroup:        0 or 2 (STATIC) — ~61% use flag 2
        #   igTransform:    2 (STATIC) — 100%
        #   igLightSet:     1 (ACTIVE) — 100%
        #   igLightStateSet: 3 (ACTIVE+STATIC) — 100%
        # Bit 1 (0x02) = STATIC: tells engine this node won't change at runtime,
        # enabling lighting and rendering optimizations. Used on most scene nodes.
        geom_flags = 0
        container_flags = 2  # STATIC — matches majority of game files

        # Build per-submesh branches (bottom-up)
        attrset_indices = []       # one igAttrSet per submesh
        texture_attr_indices = []  # all igTextureAttr refs for igTextureList
        all_bbox_mins = []
        all_bbox_maxs = []

        for sub in submeshes:
            mesh = sub['mesh']
            material = sub.get('material', _default_material())
            texture_levels = sub.get('texture_levels', None)
            tex_name = sub.get('texture_name', '')

            all_bbox_mins.append(mesh.bbox_min)
            all_bbox_maxs.append(mesh.bbox_max)

            # --- Texture chain(s) (DXT5 or CLUT) ---
            # Support multiple texture stages (diffuse, normal, specular)
            texture_stages = sub.get('texture_stages', None)
            texture_bind_indices = []

            if texture_stages and len(texture_stages) > 0:
                # Multi-texture path: each stage is (levels, name, unit_id)
                for stage_levels, stage_name, stage_unit_id in texture_stages:
                    t_attr_idx, t_bind_idx = self._build_texture_chain(
                        stage_levels, stage_name, unit_id=stage_unit_id
                    )
                    texture_attr_indices.append(t_attr_idx)
                    texture_bind_indices.append(t_bind_idx)
            else:
                # Single-texture path (backward compat)
                clut_data = sub.get('clut_data', None)
                if clut_data is not None:
                    palette_data, index_data, cw, ch = clut_data
                    texture_attr_idx, texture_bind_idx = self._build_clut_texture_chain(
                        palette_data, index_data, cw, ch, tex_name
                    )
                else:
                    texture_attr_idx, texture_bind_idx = self._build_texture_chain(
                        texture_levels, tex_name
                    )
                texture_attr_indices.append(texture_attr_idx)
                texture_bind_indices.append(texture_bind_idx)

            # --- TextureStateAttr ---
            tex_state_idx = self._add_obj(MO_TEXTURE_STATE_ATTR, [
                (2, 0, 'Short', 2),
                (4, 1, 'Bool', 1),
                (5, 0, 'Int', 4),
            ])

            # --- MaterialAttr ---
            material_idx = self._build_material(material)

            # --- ColorAttr (use actual color from material state if present) ---
            mat_state = sub.get('material_state', {})
            color_rgba = (
                mat_state.get('color_r', 1.0),
                mat_state.get('color_g', 1.0),
                mat_state.get('color_b', 1.0),
                mat_state.get('color_a', 1.0),
            )
            color_attr_idx = self._add_obj(MO_COLOR_ATTR, [
                (2, 0, 'Short', 2),
                (4, color_rgba, 'Vec4f', 16),
            ])

            # --- Vertex data ---
            vertex_array_idx = self._build_vertex_array(
                mesh, normal_mapped=bool(sub.get('normal_mapped', False)))

            # --- Index data (with strip conversion) ---
            strip_indices = triangles_to_strip(mesh.indices)
            num_strip_indices = len(strip_indices)

            idx_data = self._pack_indices(strip_indices)
            idx_mb_idx = self._add_mem(MO_EXTERNAL_INFO_ENTRY, idx_data)

            index_array_idx = self._add_obj(MO_INDEX_ARRAY, [
                (2, idx_mb_idx, 'MemoryRef', 4),
                (3, num_strip_indices, 'UnsignedInt', 4),
                (4, 0, 'Enum', 4),   # index type (0 = uint16)
                (5, 0, 'UnsignedInt', 4),
            ])

            # --- PrimLengthArray1_1 ---
            prim_data = struct.pack("<I", num_strip_indices)
            prim_mb_idx = self._add_mem(MO_DATA_LIST, prim_data)

            prim_array_idx = self._add_obj(MO_PRIM_LENGTH_1_1, [
                (2, prim_mb_idx, 'MemoryRef', 4),
                (3, 1, 'UnsignedInt', 4),   # count = 1 strip
                (4, 32, 'UnsignedInt', 4),   # unk
            ])

            # --- GeometryAttr1_5 ---
            geom_attr_idx = self._add_obj(MO_GEOMETRY_ATTR_1_5, [
                (2, 0, 'Short', 2),
                (4, vertex_array_idx, 'ObjectRef', 4),
                (5, index_array_idx, 'ObjectRef', 4),
                (6, 4, 'Enum', 4),          # prim_type = 4 (TriangleStrip)
                (7, 1, 'UnsignedInt', 4),    # num strips
                (8, 0, 'UnsignedInt', 4),
                (9, -1, 'ObjectRef', 4),
                (10, 0, 'Int', 4),
                (11, -1, 'ObjectRef', 4),
                (12, -1, 'ObjectRef', 4),
                (13, prim_array_idx, 'ObjectRef', 4),
            ])

            # --- AttrList for AttrSet ---
            # Base attrs: material + texture bind(s) + texstate + color
            attr_refs = [material_idx] + texture_bind_indices + [tex_state_idx, color_attr_idx]

            # Conditionally add state attrs from material_state.
            # Game files omit blend/alpha attrs entirely for opaque materials.
            # Only emit when actually needed:
            #  - Blend enabled → BlendStateAttr(1) + BlendFunctionAttr
            #  - Alpha test enabled → AlphaStateAttr(1) + AlphaFunctionAttr
            #  - Alpha test + blend disabled → cutout pattern:
            #      AlphaStateAttr(1) + AlphaFunctionAttr + BlendStateAttr(0)
            blend_on = bool(mat_state.get('blend_enabled', False))
            alpha_on = bool(mat_state.get('alpha_test_enabled', False))

            if blend_on or (alpha_on and 'blend_enabled' in mat_state):
                # Emit BlendStateAttr — enabled for blend, explicit off for cutout
                blend_state_idx = self._add_obj(MO_BLEND_STATE_ATTR, [
                    (2, 0, 'Short', 2),
                    (4, int(blend_on), 'Bool', 1),
                ])
                attr_refs.append(blend_state_idx)

            if blend_on and mat_state.get('blend_src') is not None:
                # Only emit BlendFunctionAttr when blending is actually on
                blend_func_idx = self._add_obj(MO_BLEND_FUNCTION_ATTR, [
                    (2, 0, 'Short', 2),
                    (4, mat_state.get('blend_src', 4), 'Enum', 4),
                    (5, mat_state.get('blend_dst', 5), 'Enum', 4),
                    (6, mat_state.get('blend_eq', 0), 'Enum', 4),
                    (7, -1, 'ObjectRef', 4),
                    (8, mat_state.get('blend_constant', 0), 'UnsignedChar', 1),
                    (9, mat_state.get('blend_stage', 0), 'Short', 2),
                    (11, mat_state.get('blend_a', 0), 'Enum', 4),
                    (12, mat_state.get('blend_b', 0), 'Enum', 4),
                    (13, mat_state.get('blend_c', 0), 'Enum', 4),
                    (14, mat_state.get('blend_d', 0), 'Enum', 4),
                ])
                attr_refs.append(blend_func_idx)

            if alpha_on:
                # Only emit AlphaStateAttr when alpha test is enabled
                alpha_state_idx = self._add_obj(MO_ALPHA_STATE_ATTR, [
                    (2, 0, 'Short', 2),
                    (4, 1, 'Bool', 1),
                ])
                attr_refs.append(alpha_state_idx)

            if alpha_on and mat_state.get('alpha_func') is not None:
                # Only emit AlphaFunctionAttr when alpha test is on
                alpha_func_idx = self._add_obj(MO_ALPHA_FUNCTION_ATTR, [
                    (2, 0, 'Short', 2),
                    (4, mat_state.get('alpha_func', 6), 'Enum', 4),
                    (5, mat_state.get('alpha_ref', 0.5), 'Float', 4),
                ])
                attr_refs.append(alpha_func_idx)

            if mat_state.get('lighting_enabled') is not None:
                lighting_state_idx = self._add_obj(MO_LIGHTING_STATE_ATTR, [
                    (2, 0, 'Short', 2),
                    (4, int(mat_state['lighting_enabled']), 'Bool', 1),
                ])
                attr_refs.append(lighting_state_idx)

            if mat_state.get('tex_matrix_enabled') is not None:
                tex_matrix_idx = self._add_obj(MO_TEX_MATRIX_STATE_ATTR, [
                    (2, 0, 'Short', 2),
                    (4, int(mat_state['tex_matrix_enabled']), 'Bool', 1),
                    (5, mat_state.get('tex_matrix_unit_id', 0), 'Int', 4),
                ])
                attr_refs.append(tex_matrix_idx)

            # Backface culling: emit igCullFaceAttr when explicitly set
            if mat_state.get('cull_face_enabled') is not None:
                cull_idx = self._add_obj(MO_CULL_FACE_ATTR, [
                    (2, 0, 'Short', 2),
                    (4, int(mat_state['cull_face_enabled']), 'Bool', 1),
                    (5, mat_state.get('cull_face_mode', 0), 'Enum', 4),
                ])
                attr_refs.append(cull_idx)

            attr_data = struct.pack("<" + "i" * len(attr_refs), *attr_refs)
            attr_data_mb = self._add_mem(MO_OBJECT, attr_data)
            attr_list_idx = self._add_obj(MO_ATTR_LIST, [
                (2, len(attr_refs), 'Int', 4),
                (3, len(attr_refs), 'Int', 4),
                (4, attr_data_mb, 'MemoryRef', 4),
            ])

            # --- AttrList for Geometry (just geom attr) ---
            geom_attr_refs = [geom_attr_idx]
            geom_attr_data = struct.pack("<" + "i" * len(geom_attr_refs), *geom_attr_refs)
            geom_attr_mb = self._add_mem(MO_OBJECT, geom_attr_data)
            geom_attr_list_idx = self._add_obj(MO_ATTR_LIST, [
                (2, len(geom_attr_refs), 'Int', 4),
                (3, len(geom_attr_refs), 'Int', 4),
                (4, geom_attr_mb, 'MemoryRef', 4),
            ])

            # --- AABox for this submesh's geometry ---
            aabox_idx = self._add_obj(MO_AABOX, [
                (2, mesh.bbox_min, 'Vec3f', 12),
                (3, mesh.bbox_max, 'Vec3f', 12),
            ])

            # --- Geometry node (leaf) ---
            geom_node_list_idx = self._add_obj(MO_NODE_LIST, [
                (2, 0, 'Int', 4),
                (3, 0, 'Int', 4),
                (4, -1, 'MemoryRef', 4),
            ])

            geometry_idx = self._add_obj(MO_GEOMETRY, [
                (2, '', 'String', 4),
                (3, aabox_idx, 'ObjectRef', 4),
                (5, geom_flags, 'Int', 4),
                (7, geom_node_list_idx, 'ObjectRef', 4),
                (8, geom_attr_list_idx, 'ObjectRef', 4),
                (9, 1, 'Bool', 1),
            ])

            # --- AttrSet node containing this geometry ---
            geom_ref_data = struct.pack("<i", geometry_idx)
            geom_ref_mb = self._add_mem(MO_OBJECT, geom_ref_data)
            attrset_children_idx = self._add_obj(MO_NODE_LIST, [
                (2, 1, 'Int', 4),
                (3, 1, 'Int', 4),
                (4, geom_ref_mb, 'MemoryRef', 4),
            ])

            attrset_idx = self._add_obj(MO_ATTR_SET, [
                (2, '', 'String', 4),
                (3, -1, 'ObjectRef', 4),  # no aabox on AttrSet
                (5, container_flags, 'Int', 4),
                (7, attrset_children_idx, 'ObjectRef', 4),
                (8, attr_list_idx, 'ObjectRef', 4),
                (9, 0, 'Bool', 1),
            ])

            attrset_indices.append(attrset_idx)

        # --- Compute union bounding box ---
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

        # --- Root AABox ---
        root_aabox_idx = self._add_obj(MO_AABOX, [
            (2, union_min, 'Vec3f', 12),
            (3, union_max, 'Vec3f', 12),
        ])

        # --- Build light objects if present ---
        light_set_indices = []       # igLightSet nodes (children of root)
        light_state_attr_indices = []  # igLightStateAttr for parallel list
        if lights:
            for light_data in lights:
                ls_idx, lsa_idx = self._build_light_chain(light_data)
                light_set_indices.append(ls_idx)
                light_state_attr_indices.append(lsa_idx)

        # --- Root NodeList (contains igLightSets + all AttrSets) ---
        # In XML2, lights come before geometry in the children list
        all_root_children = light_set_indices + attrset_indices
        n_children = len(all_root_children)
        children_data = struct.pack("<" + "i" * n_children, *all_root_children)
        children_mb = self._add_mem(MO_OBJECT, children_data)
        root_children_idx = self._add_obj(MO_NODE_LIST, [
            (2, n_children, 'Int', 4),
            (3, n_children, 'Int', 4),
            (4, children_mb, 'MemoryRef', 4),
        ])

        # --- Root node: igLightStateSet (with lights) or igGroup (without) ---
        if light_set_indices:
            # Build igLightStateAttrList (parallel list of enable/disable states)
            n_lsa = len(light_state_attr_indices)
            lsa_data = struct.pack("<" + "i" * n_lsa, *light_state_attr_indices)
            lsa_data_mb = self._add_mem(MO_OBJECT, lsa_data)
            light_state_list_idx = self._add_obj(MO_LIGHT_STATE_ATTR_LIST, [
                (2, n_lsa, 'Int', 4),
                (3, n_lsa, 'Int', 4),
                (4, lsa_data_mb, 'MemoryRef', 4),
            ])

            # igLightStateSet._flags is always 3 in XML2 game files
            root_group_idx = self._add_obj(MO_LIGHT_STATE_SET, [
                (2, '', 'String', 4),
                (3, root_aabox_idx, 'ObjectRef', 4),
                (5, 3, 'Int', 4),
                (7, root_children_idx, 'ObjectRef', 4),
                (8, light_state_list_idx, 'ObjectRef', 4),
            ])
        else:
            root_group_idx = self._add_obj(MO_GROUP, [
                (2, '', 'String', 4),
                (3, root_aabox_idx, 'ObjectRef', 4),
                (5, container_flags, 'Int', 4),
                (7, root_children_idx, 'ObjectRef', 4),
            ])

        # --- igTextureList (all texture attrs) ---
        n_tex = len(texture_attr_indices)
        tex_refs_data = struct.pack("<" + "i" * n_tex, *texture_attr_indices)
        tex_refs_mb = self._add_mem(MO_OBJECT, tex_refs_data)
        texture_list_idx = self._add_obj(MO_TEXTURE_LIST, [
            (2, n_tex, 'Int', 4),
            (3, n_tex, 'Int', 4),
            (4, tex_refs_mb, 'MemoryRef', 4),
        ])

        # --- igGraphPathList (empty) ---
        graph_path_idx = self._add_obj(19, [  # igGraphPathList
            (2, 0, 'Int', 4),
            (3, 0, 'Int', 4),
            (4, -1, 'MemoryRef', 4),
        ])

        # --- igNodeList for SceneInfo (empty) ---
        scene_node_list_idx = self._add_obj(MO_NODE_LIST, [
            (2, 0, 'Int', 4),
            (3, 0, 'Int', 4),
            (4, -1, 'MemoryRef', 4),
        ])

        # --- igSceneInfo ---
        scene_info_idx = self._add_obj(MO_SCENE_INFO, [
            (2, 'Scene Graph', 'String', 4),
            (4, 1, 'Bool', 1),
            (5, root_group_idx, 'ObjectRef', 4),
            (6, texture_list_idx, 'ObjectRef', 4),
            (7, graph_path_idx, 'ObjectRef', 4),
            (8, 0, 'Long', 8),
            (9, 0, 'Long', 8),
            (10, (0.0, 0.0, 1.0), 'Vec3f', 12),
            (11, scene_node_list_idx, 'ObjectRef', 4),
        ])

        # --- igCollideHull (optional) ---
        collide_hull_idx = None
        if collision_data is not None:
            collide_hull_idx = self._build_collide_hull(collision_data)

        # --- igInfoList (root) ---
        if collide_hull_idx is not None:
            info_refs = struct.pack("<ii", scene_info_idx, collide_hull_idx)
            info_count = 2
        else:
            info_refs = struct.pack("<i", scene_info_idx)
            info_count = 1

        info_refs_mb = self._add_mem(MO_OBJECT, info_refs)
        info_list_idx = self._add_obj(MO_INFO_LIST, [
            (2, info_count, 'Int', 4),
            (3, info_count, 'Int', 4),
            (4, info_refs_mb, 'MemoryRef', 4),
        ])

        # === Convert to writer structures ===
        self._finalize_writer(writer, info_list_idx)
        return writer

    # =========================================================================
    # Private helpers — object/memory allocation
    # =========================================================================

    def _add_obj(self, meta_obj_idx, fields):
        """Add an object, return its index in the unified list."""
        idx = len(self._obj_list)
        self._obj_list.append(('obj', meta_obj_idx, fields))
        self._ref_infos.append({
            'is_object': True,
            'type_index': meta_obj_idx,
            'type_name': META_OBJECTS[meta_obj_idx][0].encode(),
            'mem_pool_handle': -1,
        })
        return idx

    def _add_mem(self, type_idx, data, align_type=-1, pool=-1):
        """Add a memory block, return its index in the unified list."""
        idx = len(self._obj_list)
        self._obj_list.append(('mem', type_idx, data))
        self._ref_infos.append({
            'is_object': False,
            'type_index': type_idx,
            'type_name': META_OBJECTS[type_idx][0].encode(),
            'mem_size': len(data),
            'ref_counted': 1,
            'align_type_idx': align_type,
            'mem_pool_handle': pool,
        })
        return idx

    # =========================================================================
    # Private helpers — building sub-components
    # =========================================================================

    def _build_texture_chain(self, texture_levels, tex_name, unit_id=0):
        """Build images + mipmap list + texture attr + texture bind.

        Returns:
            (texture_attr_idx, texture_bind_idx)
        """
        if texture_levels is None:
            texture_levels = []

        # Dedup identical textures across submeshes: key on the unit + the base
        # level's dims and compressed bytes (identity is stable — export_igb's
        # texture_cache hands back the SAME levels object for a shared image).
        cache = getattr(self, '_tex_chain_cache', None)
        ckey = None
        if cache is not None and texture_levels:
            _cd, _tw, _th = texture_levels[0]
            ckey = (unit_id, _tw, _th, len(texture_levels), bytes(_cd))
            hit = cache.get(ckey)
            if hit is not None:
                return hit

        # Images (base + mipmaps)
        base_img_idx = None
        mip_img_indices = []

        for level_idx, (comp_data, tw, th) in enumerate(texture_levels):
            pixel_mb = self._add_mem(MO_EXTERNAL_INFO_ENTRY, comp_data, align_type=1)

            img_size = len(comp_data)
            stride = max(1, (tw + 3) // 4) * 16

            img_idx = self._add_obj(MO_IMAGE, [
                (2, tw, 'UnsignedInt', 4),       # width
                (3, th, 'UnsignedInt', 4),       # height
                (4, 4, 'UnsignedInt', 4),        # bpp (4 = compressed)
                (5, 1, 'UnsignedInt', 4),        # depth
                (6, 100, 'UnsignedInt', 4),      # unk
                (7, 2, 'UnsignedInt', 4),        # unk
                (8, 2, 'UnsignedInt', 4),        # unk
                (9, 2, 'UnsignedInt', 4),        # unk
                (10, 2, 'UnsignedInt', 4),       # unk
                (11, 16, 'Enum', 4),             # pfmt=16 (DXT5)
                (12, img_size, 'Int', 4),         # imageSize
                (13, pixel_mb, 'MemoryRef', 4),   # pixelData
                (14, -1, 'MemoryRef', 4),         # unk
                (15, 1, 'Bool', 1),               # compressed=True
                (16, 0, 'UnsignedInt', 4),        # unk
                (17, -1, 'ObjectRef', 4),          # unk
                (18, 0, 'UnsignedInt', 4),        # unk
                (19, stride, 'Int', 4),            # stride
                (20, 1, 'Bool', 1),               # compressed flag
                (21, 0, 'UnsignedInt', 4),        # unk
                (22, tex_name, 'String', 4),      # name
            ])

            if level_idx == 0:
                base_img_idx = img_idx
            else:
                mip_img_indices.append(img_idx)

        # MipMap list
        if mip_img_indices:
            mip_list_data = struct.pack("<" + "i" * len(mip_img_indices), *mip_img_indices)
            mip_data_mb = self._add_mem(MO_OBJECT, mip_list_data)
            mipmap_list_idx = self._add_obj(MO_MIPMAP_LIST, [
                (2, len(mip_img_indices), 'Int', 4),
                (3, len(mip_img_indices), 'Int', 4),
                (4, mip_data_mb, 'MemoryRef', 4),
            ])
        else:
            mipmap_list_idx = -1

        # igTextureAttr
        num_levels = len(texture_levels)
        texture_attr_idx = self._add_obj(MO_TEXTURE_ATTR, [
            (2, 0, 'Short', 2),
            (4, 0, 'UnsignedInt', 4),
            (5, 1, 'Enum', 4),             # magFilter = 1 (linear)
            (6, 3, 'Enum', 4),             # minFilter = 3 (linear mipmap linear)
            (7, 1, 'Enum', 4),             # wrapS = 1 (repeat)
            (8, 1, 'Enum', 4),             # wrapT = 1 (repeat)
            (10, 0, 'Enum', 4),
            (11, 0, 'Enum', 4),
            (12, base_img_idx if base_img_idx is not None else -1, 'ObjectRef', 4),
            (13, 0, 'Bool', 1),
            (14, -1, 'ObjectRef', 4),
            (15, num_levels, 'Int', 4),
            (16, mipmap_list_idx, 'ObjectRef', 4),
        ])

        # igTextureBindAttr
        texture_bind_idx = self._add_obj(MO_TEXTURE_BIND_ATTR, [
            (2, 0, 'Short', 2),
            (4, texture_attr_idx, 'ObjectRef', 4),
            (5, unit_id, 'Int', 4),  # unit_id: 0=diffuse, 1=normal, 2=specular
        ])

        result = (texture_attr_idx, texture_bind_idx)
        if ckey is not None:
            cache[ckey] = result
        return result

    def _build_clut_texture_chain(self, palette_data, index_data, width, height, tex_name):
        """Build CLUT image + igClut + texture attr + bind.

        PS2 PSMT8 palette-based texture. No DXT compression — palette is raw
        RGBA bytes so no RGB565/BGR565 ambiguity. Works in both XML2 and MUA.

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
            (4, 1, 'UnsignedInt', 4),       # components=1 (indexed)
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
        """Build an igMaterialAttr object. Returns its index."""
        diffuse = material.get('diffuse', (1.0, 1.0, 1.0, 1.0))
        ambient = material.get('ambient', (1.0, 1.0, 1.0, 1.0))
        specular = material.get('specular', (0.0, 0.0, 0.0, 0.0))
        emission = material.get('emission', (0.0, 0.0, 0.0, 0.0))
        shininess = material.get('shininess', 0.0)

        return self._add_obj(MO_MATERIAL_ATTR, [
            (2, 0, 'Short', 2),
            (4, shininess, 'Float', 4),
            (5, diffuse, 'Vec4f', 16),
            (6, ambient, 'Vec4f', 16),
            (7, specular, 'Vec4f', 16),
            (8, emission, 'Vec4f', 16),
            (9, 0, 'UnsignedInt', 4),
        ])

    def _build_vertex_array(self, mesh, normal_mapped=False):
        """Build igVertexArray1_1 with ext_indexed + position/normal/UV blocks.

        When normal_mapped, also emits per-vertex tangent-frame streams
        (slots 17/18) so MUA's bump shader can light the unit-1 normal map —
        the same streams the v4 skin path emits for normal-mapped skins.

        Returns the vertex_array object index.
        """
        num_verts = len(mesh.positions)

        # A tangent frame is derived from UVs — fall back to plain format
        # if a normal-mapped mesh somehow lacks them.
        normal_mapped = bool(normal_mapped and mesh.uvs)

        # igExternalIndexedEntry (80 bytes, 20 uint32 slots)
        ext_indexed = self._build_ext_indexed_data()
        ext_indexed_mb_idx = self._add_mem(MO_EXTERNAL_INDEXED_ENTRY, ext_indexed)

        # Vertex data memory blocks (NO vertex colors — confuses stride)
        pos_data = self._pack_positions(mesh.positions)
        pos_mb_idx = self._add_mem(MO_OBJECT_DIR_ENTRY, pos_data)

        norm_data = self._pack_normals(mesh.normals)
        norm_mb_idx = self._add_mem(MO_OBJECT_DIR_ENTRY, norm_data)

        uv_data = self._pack_uvs(mesh.uvs)
        uv_mb_idx = self._add_mem(MO_OBJECT_DIR_ENTRY, uv_data)

        tan_mb_idx = bin_mb_idx = -1
        if normal_mapped:
            from .skin_builder_v4 import _compute_tangent_frame
            tangents, binormals = _compute_tangent_frame(
                mesh.positions, mesh.normals, mesh.uvs, mesh.indices)
            tan_data = bytearray(num_verts * 12)
            bin_data = bytearray(num_verts * 12)
            for i in range(num_verts):
                struct.pack_into('<fff', tan_data, i * 12, *tangents[i])
                struct.pack_into('<fff', bin_data, i * 12, *binormals[i])
            tan_mb_idx = self._add_mem(MO_OBJECT_DIR_ENTRY, bytes(tan_data))
            bin_mb_idx = self._add_mem(MO_OBJECT_DIR_ENTRY, bytes(bin_data))

        # Patch ext_indexed with actual memory block indices
        self._patch_ext_indexed(ext_indexed_mb_idx, pos_mb_idx, norm_mb_idx,
                                uv_mb_idx, tan_mb=tan_mb_idx,
                                bin_mb=bin_mb_idx)

        # vertex format: 0x00010003 = pos+normal+texcoord (matches reference).
        # Normal-mapped adds the tangent/binormal stream bits (0xC00000) —
        # the same bits FMT_NORMAL_MAPPED (0xC10443) adds over FMT_SKINNED_UV
        # (0x10443) in the v4 skin path, minus the skinning bits.
        vertex_format = 0x00C10003 if normal_mapped else 0x00010003

        return self._add_obj(MO_VERTEX_ARRAY_1_1, [
            (2, ext_indexed_mb_idx, 'MemoryRef', 4),
            (3, num_verts, 'UnsignedInt', 4),
            (4, 0, 'UnsignedInt', 4),
            (5, 0, 'UnsignedInt', 4),
            (6, vertex_format, 'Struct', 4),
            (7, -1, 'MemoryRef', 4),   # blend weights
            (8, -1, 'MemoryRef', 4),   # blend indices
            (10, -1, 'MemoryRef', 4),  # unk
        ])

    def _build_light_chain(self, light_data):
        """Build igLightAttr -> igLightList -> igLightSet + igLightStateAttr.

        Each light produces two objects that get referenced from the root:
        - An igLightSet (child of igLightStateSet via NodeList)
        - An igLightStateAttr (in the igLightStateAttrList)

        Args:
            light_data: dict with keys:
                'name', 'type', 'position', 'direction', 'diffuse',
                'ambient', 'specular', 'attenuation', 'falloff', 'cutoff',
                'shininess', 'light_id', 'cast_shadow'

        Returns:
            (light_set_idx, light_state_attr_idx)
        """
        name = light_data.get('name', '')
        light_type = light_data.get('type', 1)  # default POINT
        position = light_data.get('position', (0.0, 0.0, 0.0))
        direction = light_data.get('direction', (0.0, 0.0, -1.0))
        diffuse = light_data.get('diffuse', (1.0, 1.0, 1.0, 1.0))
        ambient = light_data.get('ambient', (0.0, 0.0, 0.0, 1.0))
        specular = light_data.get('specular', (0.0, 0.0, 0.0, 1.0))
        attenuation = light_data.get('attenuation', (1.0, 0.0, 0.0))
        falloff = light_data.get('falloff', 0.0)
        cutoff = light_data.get('cutoff', -0.5)
        shininess = light_data.get('shininess', 0.0)
        light_id = light_data.get('light_id', 0)
        cast_shadow = light_data.get('cast_shadow', False)

        # --- igLightAttr ---
        light_attr_fields = [
            (2, 0, 'Short', 2),                     # _cachedAttrIndex
            (4, light_type, 'Enum', 4),              # _lightType
            (5, light_id, 'Int', 4),                 # _lightId
            (6, position, 'Vec3f', 12),              # _position
            (7, ambient, 'Vec4f', 16),               # _ambient
            (8, diffuse, 'Vec4f', 16),               # _diffuse
            (9, specular, 'Vec4f', 16),              # _specular
            (10, direction, 'Vec3f', 12),             # _direction
            (11, falloff, 'Float', 4),                # _falloff
            (12, cutoff, 'Float', 4),                 # _cutoff
            (13, attenuation, 'Vec3f', 12),           # _attenuation
            (14, shininess, 'Float', 4),              # _shininess
            (15, (0.0, 0.0, 0.0), 'Vec3f', 12),      # _cachedPosition (runtime)
            (16, (0.0, 0.0, -1.0), 'Vec3f', 12),     # _cachedDirection (runtime)
            (17, 1 if cast_shadow else 0, 'Bool', 1), # _castShadowState
        ]
        light_attr_idx = self._add_obj(MO_LIGHT_ATTR, light_attr_fields)

        # --- igLightList (ObjectList with 1 entry) ---
        light_ref_data = struct.pack("<i", light_attr_idx)
        light_ref_mb = self._add_mem(MO_OBJECT, light_ref_data)
        light_list_idx = self._add_obj(MO_LIGHT_LIST, [
            (2, 1, 'Int', 4),
            (3, 1, 'Int', 4),
            (4, light_ref_mb, 'MemoryRef', 4),
        ])

        # --- igLightSet (node in scene graph) ---
        light_set_idx = self._add_obj(MO_LIGHT_SET, [
            (2, name, 'String', 4),
            (3, -1, 'ObjectRef', 4),    # no bounding box
            (5, 1, 'Int', 4),           # flags
            (7, light_list_idx, 'ObjectRef', 4),  # _lights
        ])

        # --- igLightStateAttr (parallel enable/disable entry) ---
        light_state_attr_idx = self._add_obj(MO_LIGHT_STATE_ATTR, [
            (2, 0, 'Short', 2),                        # _cachedAttrIndex
            (4, light_attr_idx, 'ObjectRef', 4),        # _light -> igLightAttr
            (5, 1, 'Bool', 1),                          # _enabled = True
        ])

        return light_set_idx, light_state_attr_idx

    def _build_collide_hull(self, collision_data):
        """Build an igCollideHull object with triangle and BVH tree data.

        Creates two igFloatList objects (triangle data + BVH tree) and
        the igCollideHull that references them.

        Args:
            collision_data: dict with keys:
                'triangle_floats': bytes
                'num_triangles': int
                'tree_floats': bytes
                'num_tree_nodes_minus_1': int

        Returns:
            Object index of the igCollideHull
        """
        tri_floats = collision_data['triangle_floats']
        num_tris = collision_data['num_triangles']
        tree_floats = collision_data['tree_floats']
        num_tree_m1 = collision_data['num_tree_nodes_minus_1']

        # Number of floats in each list
        num_tri_floats = num_tris * 12
        num_tree_nodes = num_tree_m1 + 1
        num_tree_floats = num_tree_nodes * 8

        # igFloatList for triangle data (slot 5)
        # Memory block type_index is per-file (references local meta_object table).
        # Game files typically use whichever index happens to be at position 16 in
        # their meta_object table, but the actual type name is irrelevant — the
        # engine identifies collision data through the igCollideHull object refs.
        tri_data_mb = self._add_mem(MO_DATA_LIST, tri_floats)
        tri_float_list_idx = self._add_obj(MO_FLOAT_LIST, [
            (2, num_tri_floats, 'Int', 4),   # count (number of floats)
            (3, num_tri_floats, 'Int', 4),   # capacity
            (4, tri_data_mb, 'MemoryRef', 4),
        ])

        # igFloatList for BVH tree data (slot 6)
        tree_data_mb = self._add_mem(MO_DATA_LIST, tree_floats)
        tree_float_list_idx = self._add_obj(MO_FLOAT_LIST, [
            (2, num_tree_floats, 'Int', 4),  # count (number of floats)
            (3, num_tree_floats, 'Int', 4),  # capacity
            (4, tree_data_mb, 'MemoryRef', 4),
        ])

        # igCollideHull
        # Inherits from igInfo (slots 2=name, 4=bool)
        # Own fields: slot 5=triangles(ObjRef), slot 6=tree(ObjRef),
        #             slot 7=numTriangles(Int), slot 8=numTreeNodes-1(Int)
        collide_hull_idx = self._add_obj(MO_COLLIDE_HULL, [
            (2, '', 'String', 4),                        # name (igInfo)
            (4, 1, 'Bool', 1),                           # enabled (igInfo)
            (5, tri_float_list_idx, 'ObjectRef', 4),     # triangle data
            (6, tree_float_list_idx, 'ObjectRef', 4),    # BVH tree
            (7, num_tris, 'Int', 4),                     # numTriangles
            (8, num_tree_m1, 'Int', 4),                  # numTreeNodes - 1
        ])

        return collide_hull_idx

    # =========================================================================
    # Writer initialization and finalization
    # =========================================================================

    def _init_writer(self):
        """Create and configure a new IGBWriter with the XML2 type registry."""
        writer = IGBWriter()
        writer.version = 6
        writer.endian = "<"
        writer.has_info = True
        writer.has_external = True
        writer.shared_entries = True
        writer.has_memory_pool_names = True

        # Meta-field registry
        writer.meta_fields = []
        for name, major, minor in META_FIELDS:
            writer.meta_fields.append(MetaFieldDef(name, major, minor))

        # Meta-object registry
        writer.meta_objects = []
        for name, major, minor, parent_idx, slot_count, fields in META_OBJECTS:
            field_defs = [MetaObjectFieldDef(ti, slot, size) for ti, slot, size in fields]
            writer.meta_objects.append(MetaObjectDef(
                name, major, minor, field_defs, parent_idx, slot_count
            ))

        # Constant data
        writer.alignment_data = ALIGNMENT_BUFFER
        writer.external_dirs = [b"system"]
        writer.external_dirs_unk = 1
        writer.memory_pool_names = list(MEMORY_POOL_NAMES)

        return writer

    def _finalize_writer(self, writer, info_list_idx):
        """Convert internal obj_list/ref_infos into writer entries/objects."""
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

        # Build ObjectDef and MemoryBlockDef lists
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
        """Pack positions as Vec3f array (12 bytes per vertex)."""
        data = bytearray(len(positions) * 12)
        for i, (x, y, z) in enumerate(positions):
            struct.pack_into("<fff", data, i * 12, x, y, z)
        return bytes(data)

    def _pack_normals(self, normals):
        """Pack normals as Vec3f array (12 bytes per vertex)."""
        data = bytearray(len(normals) * 12)
        for i, (nx, ny, nz) in enumerate(normals):
            struct.pack_into("<fff", data, i * 12, nx, ny, nz)
        return bytes(data)

    def _pack_uvs(self, uvs):
        """Pack UVs as Vec2f array (8 bytes per vertex)."""
        data = bytearray(len(uvs) * 8)
        for i, (u, v) in enumerate(uvs):
            struct.pack_into("<ff", data, i * 8, u, v)
        return bytes(data)

    def _pack_indices(self, indices):
        """Pack indices as uint16 array."""
        data = bytearray(len(indices) * 2)
        for i, idx in enumerate(indices):
            struct.pack_into("<H", data, i * 2, idx)
        return bytes(data)

    def _build_ext_indexed_data(self):
        """Build the 80-byte igExternalIndexedEntry (20 x uint32, all 0xFFFFFFFF)."""
        return struct.pack("<" + "I" * 20, *([0xFFFFFFFF] * 20))

    def _patch_ext_indexed(self, ext_mb_idx, pos_mb, norm_mb, uv_mb,
                           tan_mb=-1, bin_mb=-1):
        """Patch the igExternalIndexedEntry memory block with actual indices.

        Slot 0: positions, Slot 1: normals, Slot 11: UVs.
        No vertex colors — matching XML2 PC reference files.

        Normal-mapped geometry adds the tangent frame: native binds
        slot 17 -> D3DDECLUSAGE_BINORMAL and slot 18 -> D3DDECLUSAGE_TANGENT
        (contents swapped vs our local tangent/binormal names — see the
        route-swap note in skin_builder_v4._build_vertex_array).
        """
        _, type_idx, data = self._obj_list[ext_mb_idx]
        slots = list(struct.unpack("<" + "I" * 20, data))
        slots[0] = pos_mb
        slots[1] = norm_mb
        slots[11] = uv_mb
        if bin_mb >= 0:
            slots[17] = bin_mb   # slot 17 carries BINORMAL (+V)
        if tan_mb >= 0:
            slots[18] = tan_mb   # slot 18 carries TANGENT  (+U)
        new_data = struct.pack("<" + "I" * 20, *slots)
        self._obj_list[ext_mb_idx] = ('mem', type_idx, new_data)


def _default_material():
    """Return default material properties."""
    return {
        'diffuse': (1.0, 1.0, 1.0, 1.0),
        'ambient': (1.0, 1.0, 1.0, 1.0),
        'specular': (0.0, 0.0, 0.0, 0.0),
        'emission': (0.0, 0.0, 0.0, 0.0),
        'shininess': 0.0,
    }
