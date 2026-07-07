"""Material and texture extraction from IGB scene graph.

Handles extraction of:
- igMaterialAttr: diffuse, ambient, specular, emission, shininess
- igTextureBindAttr -> igTextureAttr -> igImage chain
- DXT3 compressed pixel data from igImage memory blocks
- PS2 CLUT-indexed textures via igImage -> igClut chain
- Mipmap chains from igImageMipMapList
- igBlendStateAttr / igBlendFunctionAttr: alpha blending control
- igAlphaStateAttr / igAlphaFunctionAttr: alpha test control
- igColorAttr: per-node color tint
- igLightingStateAttr: per-subtree lighting toggle
- igTextureMatrixStateAttr: UV animation toggle

Field slot mappings (from Alchemy 5.0 SDK headers):

igMaterialAttr (inherits igVisualAttribute -> igAttr -> igObject):
    slot 2:  Short      - _priority (from igAttr)
    slot 4:  Float      - _shininess
    slot 5:  Vec4f      - _diffuse  (R, G, B, A)
    slot 6:  Vec4f      - _ambient  (R, G, B, A)
    slot 7:  Vec4f      - _specular (R, G, B, A)
    slot 8:  Vec4f      - _emission (R, G, B, A)
    slot 9:  UnsignedInt - _flags

igTextureBindAttr (inherits igVisualAttribute -> igAttr -> igObject):
    slot 2:  Short      - _priority (from igAttr)
    slot 4:  ObjectRef  - _texture (-> igTextureAttr)
    slot 5:  Int        - _unitID

igTextureAttr (inherits igVisualAttribute -> igAttr -> igObject):
    slot 5:  Enum       - _magFilter (IG_GFX_TEXTURE_FILTER)
    slot 6:  Enum       - _minFilter
    slot 7:  Enum       - _wrapS (IG_GFX_TEXTURE_WRAP)
    slot 8:  Enum       - _wrapT
    slot 12: ObjectRef  - _image (-> igImage)
    slot 15: Int        - _imageCount
    slot 16: ObjectRef  - _imageMipMaps (-> igImageMipMapList)

igImage (inherits igObject):
    slot 2:  UnsignedInt - _px (width)
    slot 3:  UnsignedInt - _py (height)
    slot 4:  UnsignedInt - _pz (num components, typically 4)
    slot 5:  UnsignedInt - _orderPreservation
    slot 6:  UnsignedInt - _order (IG_GFX_IMAGE_ORDER)
    slot 7:  UnsignedInt - _bitsRed
    slot 8:  UnsignedInt - _bitsGrn
    slot 9:  UnsignedInt - _bitsBlu
    slot 10: UnsignedInt - _bitsAlpha
    slot 11: Enum        - _pfmt (IG_GFX_TEXTURE_FORMAT)
    slot 12: Int         - _imageSize (total bytes)
    slot 13: MemoryRef   - _pImage (pixel data)
    slot 14: MemoryRef   - _pName (legacy, usually -1)
    slot 15: Bool        - _localImage
    slot 17: ObjectRef   - _clut (-> igClut, PS2 only)
    slot 19: Int         - _bytesPerRow
    slot 20: Bool        - _compressed
    slot 22: String      - _pNameString (source file path)

igClut (inherits igObject, PS2 only):
    slot 2:  Enum        - palette pixel format (7 = RGBA_8888_32)
    slot 3:  UnsignedInt - number of palette entries (16 or 256)
    slot 4:  Int         - bytes per entry (4 for RGBA32)
    slot 5:  MemoryRef   - palette data
    slot 6:  Int         - total palette data size in bytes
"""

import struct
import os

from ..igb_format.igb_objects import IGBObject, IGBMemoryBlock


# IG_GFX_TEXTURE_FORMAT values (from igGfx.h)
PFMT_L_8 = 0
PFMT_A_8 = 1
PFMT_LA_88_16 = 3
PFMT_RGB_888_24 = 5
PFMT_RGBA_8888_32 = 7
PFMT_RGBA_5551_16 = 8
PFMT_RGBA_4444_16 = 9
PFMT_RGB_565_16 = 10
PFMT_RGB_DXT1 = 13
PFMT_RGBA_DXT1 = 14
PFMT_RGBA_DXT3 = 15
PFMT_RGBA_DXT5 = 16

# GameCube/Wii tiled formats
PFMT_WII_CMPR = 21        # GC/Wii S3TC compressed (DXT1 in 8x8 macro-blocks)

# PSP tiled CLUT formats
PFMT_PSP_TILED_8 = 42     # PSP 8-bit CLUT indexed (tiled layout)
PFMT_PSP_TILED_4 = 43     # PSP 4-bit CLUT indexed (tiled layout)

# Xbox 360 / DX10 formats
PFMT_DXN = 44             # DXN / BC5 / ATI2N (two-channel normal map)

# PS2-specific pixel formats (CLUT-indexed)
PFMT_PS2_PSMT8 = 65536    # 0x10000 - 8-bit indexed (256 colors)
PFMT_PS2_PSMT4 = 65537    # 0x10001 - 4-bit indexed (16 colors)

# IG_GFX_TEXTURE_FILTER values
FILTER_NEAREST = 0
FILTER_LINEAR = 1
FILTER_NEAREST_MIPMAP_NEAREST = 2
FILTER_NEAREST_MIPMAP_LINEAR = 3
FILTER_LINEAR_MIPMAP_NEAREST = 4
FILTER_LINEAR_MIPMAP_LINEAR = 5
FILTER_ANISOTROPIC = 6

# IG_GFX_TEXTURE_WRAP values
WRAP_CLAMP = 0
WRAP_REPEAT = 1

# IG_GFX_BLENDING_FUNCTION values (from igGfx.h)
BLEND_ZERO = 0
BLEND_ONE = 1
BLEND_SRC_COLOR = 2
BLEND_ONE_MINUS_SRC_COLOR = 3
BLEND_SRC_ALPHA = 4
BLEND_ONE_MINUS_SRC_ALPHA = 5
BLEND_DST_COLOR = 6
BLEND_ONE_MINUS_DST_COLOR = 7
BLEND_DST_ALPHA = 8
BLEND_ONE_MINUS_DST_ALPHA = 9
BLEND_SRC_ALPHA_SATURATE = 10

# IG_GFX_ALPHA_FUNCTION values (from igGfx.h)
ALPHA_NEVER = 0
ALPHA_LESS = 1
ALPHA_EQUAL = 2
ALPHA_LEQUAL = 3
ALPHA_GREATER = 4
ALPHA_NOTEQUAL = 5
ALPHA_GEQUAL = 6
ALPHA_ALWAYS = 7


class ParsedMaterial:
    """Container for extracted material properties."""

    __slots__ = (
        'shininess', 'diffuse', 'ambient', 'specular', 'emission',
        'flags', 'priority', 'source_obj',
    )

    def __init__(self):
        self.shininess = 0.0
        self.diffuse = (1.0, 1.0, 1.0, 1.0)    # RGBA
        self.ambient = (0.0, 0.0, 0.0, 1.0)     # RGBA
        self.specular = (0.0, 0.0, 0.0, 0.0)    # RGBA
        self.emission = (0.0, 0.0, 0.0, 0.0)    # RGBA
        self.flags = 0
        self.priority = 0      # inherited igAttr base Short (slot 2/v6+v8, 3/v4)
        self.source_obj = None

    @property
    def alpha(self):
        """Diffuse alpha channel."""
        return self.diffuse[3] if len(self.diffuse) > 3 else 1.0

    @property
    def is_transparent(self):
        """Whether this material uses alpha transparency."""
        return self.alpha < 0.999


class ParsedTexture:
    """Container for extracted texture data."""

    __slots__ = (
        'image', 'mag_filter', 'min_filter',
        'wrap_s', 'wrap_t', 'unit_id',
        'source_obj',
    )

    def __init__(self):
        self.image = None          # ParsedImage or None
        self.mag_filter = FILTER_LINEAR
        self.min_filter = FILTER_LINEAR_MIPMAP_LINEAR
        self.wrap_s = WRAP_REPEAT
        self.wrap_t = WRAP_REPEAT
        self.unit_id = 0
        self.source_obj = None


class ParsedImage:
    """Container for extracted image/texture data."""

    __slots__ = (
        'width', 'height', 'num_components', 'pixel_format',
        'image_size', 'compressed', 'bytes_per_row',
        'bits_red', 'bits_green', 'bits_blue', 'bits_alpha',
        'pixel_data', 'name', 'source_obj',
        'clut_data', 'clut_num_entries', 'clut_bpp',
        'cache_key', 'orig_levels', 'orig_pfmt',
    )

    def __init__(self):
        self.width = 0
        self.height = 0
        self.num_components = 4
        self.pixel_format = PFMT_RGBA_8888_32
        self.image_size = 0
        self.compressed = False
        self.bytes_per_row = 0
        self.bits_red = 8
        self.bits_green = 8
        self.bits_blue = 8
        self.bits_alpha = 8
        self.pixel_data = None     # raw bytes
        self.name = ""             # source filename
        self.source_obj = None
        self.clut_data = None      # raw CLUT palette bytes (RGBA entries)
        self.clut_num_entries = 0   # 16 for 4bpp, 256 for 8bpp
        self.clut_bpp = 0           # 4 or 8 (index bit depth)
        self.cache_key = None       # unique key for caching (e.g. file path for IGZ)
        # Original encoded mip chain for byte-identical re-export (level 0 first)
        self.orig_levels = None     # list of (encoded_bytes, w, h) or None
        self.orig_pfmt = None       # original igImage pixel format

    @property
    def base_name(self):
        """Get just the filename without path."""
        if not self.name:
            return ""
        return os.path.basename(self.name.replace("\\", "/"))

    @property
    def is_dxt(self):
        """Whether pixel data is DXT compressed."""
        return self.pixel_format in (
            PFMT_RGB_DXT1, PFMT_RGBA_DXT1,
            PFMT_RGBA_DXT3, PFMT_RGBA_DXT5,
        )

    @property
    def is_indexed(self):
        """Whether pixel data is CLUT-indexed (PS2 paletted)."""
        return self.pixel_format in (PFMT_PS2_PSMT8, PFMT_PS2_PSMT4)


def extract_material(reader, material_obj, profile=None):
    """Extract material properties from an igMaterialAttr object.

    Args:
        reader: IGBReader instance
        material_obj: IGBObject of type igMaterialAttr
        profile: GameProfile instance (reserved for future game-specific handling)

    Returns:
        ParsedMaterial or None
    """
    if not isinstance(material_obj, IGBObject):
        return None

    mat = ParsedMaterial()
    mat.source_obj = material_obj
    # igAttr-derived: only the base Short (slot 2) shifts in v4; slots 4+ are fixed.

    for slot, val, fi in material_obj._raw_fields:
        if fi.short_name == b"Short":
            # Inherited igAttr base Short (_priority sort key). Its slot SHIFTS by
            # version (2 in v6/v8, 3 in v4) but it is igMaterialAttr's ONLY Short,
            # so match by type rather than a hardcoded slot. v6/v8 = 0, v4 = 1.
            mat.priority = val
        elif slot == 4 and fi.short_name == b"Float":
            mat.shininess = val
        elif slot == 5 and fi.short_name == b"Vec4f":
            mat.diffuse = val
        elif slot == 6 and fi.short_name == b"Vec4f":
            mat.ambient = val
        elif slot == 7 and fi.short_name == b"Vec4f":
            mat.specular = val
        elif slot == 8 and fi.short_name == b"Vec4f":
            mat.emission = val
        elif slot == 9 and fi.short_name == b"UnsignedInt":
            mat.flags = val

    return mat


def extract_texture_bind(reader, texbind_obj, profile=None):
    """Extract texture binding from an igTextureBindAttr object.

    Follows the chain: igTextureBindAttr -> igTextureAttr -> igImage
    to extract the full texture and image data.

    Args:
        reader: IGBReader instance
        texbind_obj: IGBObject of type igTextureBindAttr
        profile: GameProfile instance (reserved for future PS2 CLUT support)

    Returns:
        ParsedTexture or None
    """
    if not isinstance(texbind_obj, IGBObject):
        return None

    tex = ParsedTexture()
    tex.source_obj = texbind_obj

    # Extract texture attr reference and unit ID
    # igAttr-derived: only the base Short (slot 2) shifts in v4; slots 4+ are fixed.
    texture_attr_ref = None
    for slot, val, fi in texbind_obj._raw_fields:
        if slot == 4 and fi.short_name == b"ObjectRef" and val != -1:
            texture_attr_ref = val
        elif fi.short_name == b"Int":
            # _unitID — igTextureBindAttr has exactly one Int field, but its
            # slot number shifts across versions (v8 MUA next-gen puts it
            # somewhere other than 5). Hardcoding slot==5 left unit_id=None on
            # v8 skins, so every texture defaulted to unit 0 (diffuse) and the
            # last bind (the black gloss map) clobbered the real _d diffuse.
            tex.unit_id = val

    if texture_attr_ref is None:
        return None

    # Resolve igTextureAttr
    texture_attr = reader.resolve_ref(texture_attr_ref)
    if not isinstance(texture_attr, IGBObject):
        return None

    # Extract texture properties (igTextureAttr is igAttr-derived: slots 4+ are fixed)
    image_ref = None
    texdata_ref = None  # slot 19: _data (igUnsignedCharList, Wii pixel data)
    miplist_candidates = []  # ObjectRefs that may be the igImageMipMapList
    for slot, val, fi in texture_attr._raw_fields:
        if slot == 5 and fi.short_name == b"Enum":
            tex.mag_filter = val
        elif slot == 6 and fi.short_name == b"Enum":
            tex.min_filter = val
        elif slot == 7 and fi.short_name == b"Enum":
            tex.wrap_s = val
        elif slot == 8 and fi.short_name == b"Enum":
            tex.wrap_t = val
        elif slot == 12 and fi.short_name == b"ObjectRef" and val != -1:
            image_ref = val
        elif slot == 19 and fi.short_name == b"ObjectRef" and val != -1:
            texdata_ref = val
        elif fi.short_name == b"ObjectRef" and val != -1 and slot not in (12, 19):
            miplist_candidates.append(val)

    if image_ref is None:
        return tex

    # Extract image
    image_obj = reader.resolve_ref(image_ref)
    if isinstance(image_obj, IGBObject) and image_obj.is_type(b"igImage"):
        tex.image = extract_image(reader, image_obj)

        # Wii fallback: pixel data is NOT stored in igImage._pImage (slot 13).
        # Instead, it's on igTextureAttr._data (slot 19) as an igUnsignedCharList
        # containing all mip levels concatenated. We use the top-level image's
        # width/height/pfmt metadata with the pixel data from this list.
        if (tex.image is not None and tex.image.pixel_data is None
                and texdata_ref is not None):
            texdata_obj = reader.resolve_ref(texdata_ref)
            if isinstance(texdata_obj, IGBObject):
                # Resolve the igUnsignedCharList's MemoryRef to get raw bytes
                for slot, val, fi in texdata_obj._raw_fields:
                    if fi.short_name == b"MemoryRef" and val != -1:
                        mem_block = reader.resolve_ref(val)
                        if isinstance(mem_block, IGBMemoryBlock) and mem_block.data:
                            tex.image.pixel_data = bytes(mem_block.data)
                        break

        # Capture the ORIGINAL encoded mip chain so an unedited re-export can
        # reuse the exact DXT blocks instead of re-compressing (byte-identical
        # textures, no lossy decode->re-encode pass). Level 0 = the base image;
        # levels 1..N come from the igImageMipMapList. Best-effort: any failure
        # just leaves orig_levels None and the exporter re-compresses as before.
        if tex.image is not None and tex.image.pixel_data:
            try:
                levels = [(tex.image.pixel_data,
                           tex.image.width, tex.image.height)]
                mip_list = None
                for cand in miplist_candidates:
                    obj = reader.resolve_ref(cand)
                    if isinstance(obj, IGBObject) and obj.is_type(b"igImageMipMapList"):
                        mip_list = obj
                        break
                if mip_list is not None:
                    for mip_obj in reader.resolve_object_list(mip_list):
                        if (isinstance(mip_obj, IGBObject)
                                and mip_obj.is_type(b"igImage")):
                            mp = extract_image(reader, mip_obj)
                            if mp is not None and mp.pixel_data:
                                levels.append((mp.pixel_data, mp.width, mp.height))
                tex.image.orig_levels = levels
                tex.image.orig_pfmt = tex.image.pixel_format
            except Exception:
                pass

    return tex


def extract_texture_cube(reader, cube_obj):
    """Parse an igTextureCubeAttr (environment / reflection cube, MUA unit 3).

    igTextureCubeAttr inherits igTextureAttr, so slot 12 = a base igImage
    ("sphereImage") and slot 19 = an ObjectRefArray of the 6 cube-face igImage
    indices. Native env cubes = 6 x 32x32 L8 grayscale faces (blade_env.png.cube_0..5).

    Returns a dict {'faces': [ParsedImage x6], 'base': ParsedImage|None} or None.
    """
    if not isinstance(cube_obj, IGBObject):
        return None
    faces = []
    base = None
    for slot, val, fi in cube_obj._raw_fields:
        if slot == 12 and fi.short_name == b"ObjectRef" and val not in (-1, None):
            img = reader.resolve_ref(val)
            if isinstance(img, IGBObject) and img.is_type(b"igImage"):
                base = extract_image(reader, img)
        elif fi.short_name == b"ObjectRefArray" and isinstance(val, (bytes, bytearray)):
            raw = bytes(val)
            for i in range(0, len(raw) - 3, 4):
                ix = struct.unpack_from("<i", raw, i)[0]
                if 0 <= ix < len(reader.objects):
                    img = reader.objects[ix]
                    if isinstance(img, IGBObject) and img.is_type(b"igImage"):
                        pi = extract_image(reader, img)
                        if pi is not None:
                            faces.append(pi)
    if not faces and base is None:
        return None
    return {'faces': faces, 'base': base}


def extract_image(reader, image_obj):
    """Extract image data from an igImage object.

    Args:
        reader: IGBReader instance
        image_obj: IGBObject of type igImage

    Returns:
        ParsedImage or None
    """
    if not isinstance(image_obj, IGBObject):
        return None

    img = ParsedImage()
    img.source_obj = image_obj
    s = reader.slot_offset  # v4/v5 slots are +1 vs v6

    pixel_data_ref = None
    clut_ref = None

    for slot, val, fi in image_obj._raw_fields:
        if slot == 2 + s and fi.short_name == b"UnsignedInt":
            img.width = val
        elif slot == 3 + s and fi.short_name == b"UnsignedInt":
            img.height = val
        elif slot == 4 + s and fi.short_name == b"UnsignedInt":
            img.num_components = val
        elif slot == 7 + s and fi.short_name == b"UnsignedInt":
            img.bits_red = val
        elif slot == 8 + s and fi.short_name == b"UnsignedInt":
            img.bits_green = val
        elif slot == 9 + s and fi.short_name == b"UnsignedInt":
            img.bits_blue = val
        elif slot == 10 + s and fi.short_name == b"UnsignedInt":
            img.bits_alpha = val
        elif slot == 11 + s and fi.short_name == b"Enum":
            img.pixel_format = val
        elif slot == 12 + s and fi.short_name == b"Int":
            img.image_size = val
        elif slot == 13 + s and fi.short_name == b"MemoryRef" and val != -1:
            pixel_data_ref = val
        elif slot == 17 + s and fi.short_name == b"ObjectRef" and val != -1:
            clut_ref = val
        elif slot == 19 + s and fi.short_name == b"Int":
            img.bytes_per_row = val
        elif slot == 20 + s and fi.short_name == b"Bool":
            img.compressed = bool(val)
        elif slot == 22 + s and fi.short_name == b"String":
            img.name = val if isinstance(val, str) else val.decode("utf-8", errors="replace")

    # Read pixel data
    if pixel_data_ref is not None:
        mem_block = reader.resolve_ref(pixel_data_ref)
        if isinstance(mem_block, IGBMemoryBlock) and mem_block.data is not None:
            img.pixel_data = bytes(mem_block.data)

    # Read CLUT palette data (PS2 indexed textures)
    if clut_ref is not None:
        clut_obj = reader.resolve_ref(clut_ref)
        if isinstance(clut_obj, IGBObject) and clut_obj.is_type(b"igClut"):
            _extract_clut(reader, clut_obj, img)

    return img


def _extract_clut(reader, clut_obj, img):
    """Extract CLUT (Color Lookup Table) palette data from an igClut object.

    igClut field layout:
        slot 2: Enum       - palette pixel format (7 = RGBA_8888_32)
        slot 3: UnsignedInt - number of palette entries (16 or 256)
        slot 4: Int        - bytes per entry (4 for RGBA32)
        slot 5: MemoryRef  - palette data
        slot 6: Int        - total palette data size in bytes

    Args:
        reader: IGBReader instance
        clut_obj: IGBObject of type igClut
        img: ParsedImage to store the CLUT data on
    """
    palette_ref = None
    num_entries = 0
    s = reader.slot_offset  # v4/v5 slots are +1 vs v6

    for slot, val, fi in clut_obj._raw_fields:
        if slot == 3 + s and fi.short_name == b"UnsignedInt":
            num_entries = val
        elif slot == 5 + s and fi.short_name == b"MemoryRef" and val != -1:
            palette_ref = val

    if palette_ref is not None and num_entries > 0:
        mem_block = reader.resolve_ref(palette_ref)
        if isinstance(mem_block, IGBMemoryBlock) and mem_block.data is not None:
            img.clut_data = bytes(mem_block.data)
            img.clut_num_entries = num_entries
            # Determine index bit depth from entry count
            if num_entries <= 16:
                img.clut_bpp = 4
            else:
                img.clut_bpp = 8


def extract_all_materials(reader):
    """Extract all materials from the file.

    Returns:
        dict mapping igMaterialAttr object index to ParsedMaterial
    """
    result = {}
    for obj in reader.objects:
        if isinstance(obj, IGBObject) and obj.is_type(b"igMaterialAttr"):
            mat = extract_material(reader, obj)
            if mat is not None:
                result[obj.index] = mat
    return result


def extract_all_textures(reader):
    """Extract all texture bindings from the file.

    Returns:
        dict mapping igTextureBindAttr object index to ParsedTexture
    """
    result = {}
    for obj in reader.objects:
        if isinstance(obj, IGBObject) and obj.is_type(b"igTextureBindAttr"):
            tex = extract_texture_bind(reader, obj)
            if tex is not None:
                result[obj.index] = tex
    return result


# ===================================================================
# State attribute extraction (blend, alpha, color, lighting, texmatrix)
# ===================================================================

def extract_blend_state(reader, obj, profile=None):
    """Extract igBlendStateAttr fields.

    igBlendStateAttr (inherits igVisualAttribute):
        slot 2: Short - _priority
        slot 4: Bool  - _enabled

    Returns:
        dict with 'enabled' key, or None
    """
    if not isinstance(obj, IGBObject):
        return None
    # igAttr-derived: slots 4+ are fixed across versions.
    for slot, val, fi in obj._raw_fields:
        if slot == 4 and fi.short_name == b"Bool":
            return {'enabled': bool(val)}
    return {'enabled': False}


def extract_blend_function(reader, obj, profile=None):
    """Extract igBlendFunctionAttr fields.

    igBlendFunctionAttr (inherits igVisualAttribute):
        slot 2:  Short       - _priority
        slot 4:  Enum        - _src (IG_GFX_BLENDING_FUNCTION)
        slot 5:  Enum        - _dst (IG_GFX_BLENDING_FUNCTION)
        slot 6:  Enum        - _eq  (deprecated)
        slot 7:  ObjectRef   - _blendEquationExt (usually -1)
        slot 8:  UnsignedChar - _blendConstant (PS2)
        slot 9:  Short       - _blendStage (PS2)
        slot 11: Enum        - _blendA (PS2)
        slot 12: Enum        - _blendB (PS2)
        slot 13: Enum        - _blendC (PS2)
        slot 14: Enum        - _blendD (PS2)

    Returns:
        dict with 'src', 'dst' keys (and PS2 fields), or None
    """
    if not isinstance(obj, IGBObject):
        return None
    # igAttr-derived: slots 4+ are fixed across versions.
    result = {
        'src': BLEND_SRC_ALPHA,
        'dst': BLEND_ONE_MINUS_SRC_ALPHA,
        'eq': 0, 'blend_constant': 0, 'blend_stage': 0,
        'blend_a': 0, 'blend_b': 0, 'blend_c': 0, 'blend_d': 0,
    }
    for slot, val, fi in obj._raw_fields:
        if slot == 4 and fi.short_name == b"Enum":
            result['src'] = val
        elif slot == 5 and fi.short_name == b"Enum":
            result['dst'] = val
        elif slot == 6 and fi.short_name == b"Enum":
            result['eq'] = val
        elif slot == 8 and fi.short_name == b"UnsignedChar":
            result['blend_constant'] = val
        elif slot == 9 and fi.short_name == b"Short":
            result['blend_stage'] = val
        elif slot == 11 and fi.short_name == b"Enum":
            result['blend_a'] = val
        elif slot == 12 and fi.short_name == b"Enum":
            result['blend_b'] = val
        elif slot == 13 and fi.short_name == b"Enum":
            result['blend_c'] = val
        elif slot == 14 and fi.short_name == b"Enum":
            result['blend_d'] = val
    return result


def extract_alpha_state(reader, obj, profile=None):
    """Extract igAlphaStateAttr fields.

    igAlphaStateAttr (inherits igVisualAttribute):
        slot 2: Short - _priority
        slot 4: Bool  - _enabled

    Returns:
        dict with 'enabled' key, or None
    """
    if not isinstance(obj, IGBObject):
        return None
    # igAttr-derived: slots 4+ are fixed across versions.
    for slot, val, fi in obj._raw_fields:
        if slot == 4 and fi.short_name == b"Bool":
            return {'enabled': bool(val)}
    return {'enabled': False}


def extract_alpha_function(reader, obj, profile=None):
    """Extract igAlphaFunctionAttr fields.

    igAlphaFunctionAttr (inherits igVisualAttribute):
        slot 2: Short - _priority
        slot 4: Enum  - _func (IG_GFX_ALPHA_FUNCTION)
        slot 5: Float - _refValue

    Returns:
        dict with 'func', 'ref' keys, or None
    """
    if not isinstance(obj, IGBObject):
        return None
    # igAttr-derived: slots 4+ are fixed across versions.
    result = {'func': ALPHA_GEQUAL, 'ref': 0.5}
    for slot, val, fi in obj._raw_fields:
        if slot == 4 and fi.short_name == b"Enum":
            result['func'] = val
        elif slot == 5 and fi.short_name == b"Float":
            result['ref'] = val
    return result


def extract_color_attr(reader, obj, profile=None):
    """Extract igColorAttr fields.

    igColorAttr (inherits igVisualAttribute):
        slot 2: Short - _priority
        slot 4: Vec4f - _color (R, G, B, A)

    Returns:
        tuple (r, g, b, a) or None
    """
    if not isinstance(obj, IGBObject):
        return None
    # igAttr-derived: slots 4+ are fixed across versions.
    for slot, val, fi in obj._raw_fields:
        if slot == 4 and fi.short_name == b"Vec4f":
            return val  # already a tuple (r, g, b, a)
    return (1.0, 1.0, 1.0, 1.0)


def extract_lighting_state(reader, obj, profile=None):
    """Extract igLightingStateAttr fields.

    igLightingStateAttr (inherits igVisualAttribute):
        slot 2: Short - _priority
        slot 4: Bool  - _enabled

    Returns:
        dict with 'enabled' key, or None
    """
    if not isinstance(obj, IGBObject):
        return None
    # igAttr-derived: slots 4+ are fixed across versions.
    for slot, val, fi in obj._raw_fields:
        if slot == 4 and fi.short_name == b"Bool":
            return {'enabled': bool(val)}
    return {'enabled': True}  # default: lighting on


def extract_tex_matrix_state(reader, obj, profile=None):
    """Extract igTextureMatrixStateAttr fields.

    igTextureMatrixStateAttr (inherits igVisualAttribute):
        slot 2: Short - _priority
        slot 4: Bool  - _enabled
        slot 5: Int   - _unitID

    Returns:
        dict with 'enabled', 'unit_id' keys, or None
    """
    if not isinstance(obj, IGBObject):
        return None
    # igAttr-derived: slots 4+ are fixed across versions.
    result = {'enabled': False, 'unit_id': 0}
    for slot, val, fi in obj._raw_fields:
        if slot == 4 and fi.short_name == b"Bool":
            result['enabled'] = bool(val)
        elif slot == 5 and fi.short_name == b"Int":
            result['unit_id'] = val
    return result


def extract_cull_face(reader, obj, profile=None):
    """Extract igCullFaceAttr fields.

    igCullFaceAttr (inherits igVisualAttribute):
        slot 2: Short - _priority
        slot 4: Bool  - _enable (1=culling on, 0=off)
        slot 5: Enum  - _cullFace (0=FRONT, 1=BACK, 2=FRONT_AND_BACK)

    Returns:
        dict with 'enabled', 'mode' keys, or None
    """
    if not isinstance(obj, IGBObject):
        return None
    # igAttr-derived: slots 4+ are fixed across versions.
    result = {'enabled': True, 'mode': 0}
    for slot, val, fi in obj._raw_fields:
        if slot == 4 and fi.short_name == b"Bool":
            result['enabled'] = bool(val)
        elif slot == 5 and fi.short_name == b"Enum":
            result['mode'] = val
    return result
