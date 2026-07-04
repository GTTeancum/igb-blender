"""Game-specific format profiles for IGB import.

Each supported game + platform combination has a GameProfile that describes
how to interpret the IGB file's geometry, textures, and coordinate system.
The IGB binary format is self-describing (class definitions are embedded in
each file), but the *interpretation* of vertex data slots, color byte order,
UV conventions, etc. varies between games.

Profiles are registered in a global dict and can be selected manually via
the Blender import dialog or auto-detected from the file's metadata.

Adding a new game:
    1. Parse ref .igb files, inspect meta-objects to discover classes present
    2. Determine vertex slot layout, color order, UV convention, etc.
    3. Create a GameProfile with the discovered parameters
    4. Call register_profile() to add it to the registry
"""

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple, List


# ---------------------------------------------------------------------------
# Configuration dataclasses
# ---------------------------------------------------------------------------

@dataclass
class GeometryConfig:
    """Configuration for geometry / vertex data extraction.

    Controls which igExternalIndexedEntry slots hold positions, normals,
    colors, and texture coordinates (for v1 geometry), and how vertex
    colors are decoded.
    """

    # Geometry attr version expected in the file:
    #   "v1"   = igGeometryAttr / igGeometryAttr1_5 + igVertexArray1_1
    #   "v2"   = igGeometryAttr2 + igVertexArray2
    #   "auto" = try v2 first, fall back to v1
    geometry_attr_version: str = "auto"

    # igExternalIndexedEntry slot mapping (v1 geometry only).
    # These are indices into the 20-slot (80 byte) external indexed entry.
    ext_slot_positions: int = 0
    ext_slot_normals: int = 1
    ext_slot_colors: int = 2
    ext_slot_texcoords: int = 11

    # Color byte order in vertex buffer.
    #   "abgr" = DirectX convention (A high byte, R low byte) — XML2 PC
    #   "rgba" = OpenGL convention (R high byte, A low byte)
    color_byte_order: str = "abgr"

    # Bytes per vertex for each component (v1 format).
    position_bpv: int = 12   # Vec3f
    normal_bpv: int = 12     # Vec3f
    color_bpv: int = 4       # uint32
    texcoord_bpv: int = 8    # Vec2f


@dataclass
class TextureConfig:
    """Configuration for texture / image handling."""

    # UV V-flip: True = apply v = 1.0 - v (DirectX convention).
    # Most Alchemy PC games use DirectX UVs and need flipping for Blender.
    uv_v_flip: bool = True

    # Expected primary pixel formats (for diagnostics, not filtering).
    # Uses IG_GFX_TEXTURE_FORMAT enum values from igGfx.h.
    expected_pixel_formats: Tuple[int, ...] = (15,)  # 15 = DXT3

    # PS2-specific: whether pixel data needs unswizzling.
    needs_unswizzle: bool = False

    # PS2-specific: CLUT (color lookup table) indexed textures.
    has_clut: bool = False

    # Swap R and B channels after decompression.
    # MUA PC (Alchemy 5.0) stores texture color data with R/B swapped
    # relative to XML2 PC (Alchemy 2.5).
    swap_rb: bool = False


@dataclass
class CoordinateConfig:
    """Configuration for coordinate system handling."""

    # Up axis convention: "Z" = Z-up (Alchemy default, same as Blender).
    up_axis: str = "Z"

    # Whether Alchemy matrices need transposing for Blender.
    # All known Alchemy versions use row-major matrices (translation in
    # last row); Blender uses column-major (translation in last column).
    transpose_matrices: bool = True


@dataclass
class GameProfile:
    """Complete format profile for a specific game + platform combination.

    Encodes all known format parameters so the extraction pipeline can
    adapt without hardcoded constants scattered across multiple files.
    """

    # Display info
    game_id: str = "xml2_pc"
    game_name: str = "X-Men Legends 2 (PC)"
    engine_version: str = "Alchemy 2.5/3.2"

    # Expected IGB version range (from header verFlags & 0xFFFF).
    min_version: int = 5
    max_version: int = 7

    # Expected endianness: "little", "big", or "any".
    expected_endian: str = "any"

    # Sub-configs
    geometry: GeometryConfig = field(default_factory=GeometryConfig)
    texture: TextureConfig = field(default_factory=TextureConfig)
    coordinate: CoordinateConfig = field(default_factory=CoordinateConfig)

    # Default backface culling when a material has NO explicit igCullFaceAttr.
    # PC games (XML2/MUA) default to single-sided; XML1's console renderer
    # draws world geometry double-sided (nuke1_3: winding is normal-consistent
    # yet in-game both faces show), so XML1 profiles set False.
    backface_cull_default: bool = True

    # Auto-detection hints: class names that *should* be present in the
    # file's meta-object registry.  Used by detect_profile() to score
    # candidate profiles.  More matches = higher confidence.
    signature_classes: Tuple[bytes, ...] = ()

    # Short description shown in Blender UI tooltip.
    notes: str = ""


# ---------------------------------------------------------------------------
# Profile registry
# ---------------------------------------------------------------------------

GAME_PROFILES: Dict[str, GameProfile] = {}


def register_profile(profile: GameProfile) -> None:
    """Register a game profile in the global registry."""
    GAME_PROFILES[profile.game_id] = profile


def get_profile(game_id: str) -> Optional[GameProfile]:
    """Look up a profile by its game_id string."""
    return GAME_PROFILES.get(game_id)


def get_profile_items() -> List[Tuple[str, str, str]]:
    """Return (identifier, name, description) tuples for Blender EnumProperty.

    First item is always "Auto-Detect".
    """
    items = [("auto", "Auto-Detect", "Try to detect the game from file contents")]
    for gid, prof in GAME_PROFILES.items():
        items.append((gid, prof.game_name, prof.notes or prof.engine_version))
    return items


# ---------------------------------------------------------------------------
# Auto-detection
# ---------------------------------------------------------------------------

def detect_profile(reader) -> GameProfile:
    """Auto-detect the best matching GameProfile for a parsed IGB file.

    Strategy:
        1. Collect all class names in the file's meta-object registry.
        2. Score each registered profile by version match, endianness match,
           and how many of its signature_classes appear in the file.
        3. Return the highest-scoring profile, or the XML2 PC default.

    Args:
        reader: IGBReader instance (already parsed).

    Returns:
        The best-matching GameProfile.
    """
    header = reader.header
    class_names = set()
    for mo in reader.meta_objects:
        if hasattr(mo, 'name'):
            class_names.add(mo.name)

    best_score = -1
    best_profile = None

    for profile in GAME_PROFILES.values():
        score = 0

        # Version range check (hard requirement).
        if not (profile.min_version <= header.version <= profile.max_version):
            continue

        score += 1  # Base score for version match.

        # Endianness match (soft bonus).
        if profile.expected_endian != "any":
            expected_char = "<" if profile.expected_endian == "little" else ">"
            if header.endian == expected_char:
                score += 1

        # Signature class matches (strongest signal).
        for sig_class in profile.signature_classes:
            if sig_class in class_names:
                score += 3

        if score > best_score:
            best_score = score
            best_profile = profile

    # Fallback to XML2 PC if nothing matched.
    if best_profile is None:
        best_profile = GAME_PROFILES.get("xml2_pc")

    _path = ""
    try:
        _path = (getattr(reader, "filepath", "") or "").lower()
    except Exception:
        pass

    # XML1 PS2 and XML2 PC files are byte-format IDENTICAL (v6 LE, v1
    # geometry, CLUT) — the class-signature score is a coin toss between
    # them, and since backface_cull_default diverged the winner matters.
    # Resolve with the file's location when it names either game.
    if (best_profile is not None
            and best_profile.game_id in ("xml1_ps2", "xml2_pc")):
        if any(k in _path for k in ("legends ii", "legends 2", "xml2")):
            best_profile = GAME_PROFILES.get("xml2_pc", best_profile)
        elif any(k in _path for k in ("xml1", "legends 1")):
            best_profile = GAME_PROFILES.get("xml1_ps2", best_profile)
        elif best_profile.game_id == "xml1_ps2":
            # No path signal: prefer the PC profile — it's this addon's
            # primary target and the safer default (single-sided culling).
            best_profile = GAME_PROFILES.get("xml2_pc", best_profile)

    # Ambiguity fixes: v4/v6 files authored FOR MUA (community skins, our own
    # exports, animated mannequins) carry the same version + classes as XML2
    # files, so they score as xml2_pc — then re-import with R/B-swapped DXT
    # colors, an XML2 target-game tag, and XML2-style export defaults.
    if best_profile is not None and best_profile.game_id == "xml2_pc":
        mua = GAME_PROFILES.get("mua_pc")

        # 1. CONTENT rule (path-independent, unambiguous): normal/specular/
        #    gloss texture units (1/2/5) exist ONLY in MUA — XML2 has no
        #    normal-map pipeline at all. Any igTextureBindAttr bound to one
        #    of those units marks the file as MUA regardless of where it is.
        if mua is not None:
            s = 1 if reader.header.version <= 5 else 0
            for obj in reader.objects:
                try:
                    if (hasattr(obj, "is_type")
                            and obj.is_type(b"igTextureBindAttr")):
                        for slot, val, fi in obj._raw_fields:
                            if (fi.short_name == b"Int"
                                    and val in (1, 2, 5)):
                                return mua
                except Exception:
                    continue

        # 2. PATH rule for the remaining single-texture ambiguity (e.g. MUA's
        #    own v6 CLUT actors, mannequins): when the location names MUA and
        #    the textures aren't XML2-native DXT3, prefer the MUA profile so
        #    swap_rb + the target-game tag come out right.
        path_says_mua = any(k in _path for k in
                            ("mannequin", "ultimate alliance", "mua"))
        if path_says_mua and mua is not None:
            pfmts = set()
            for obj in reader.objects:
                try:
                    if (hasattr(obj, "is_type")
                            and obj.is_type(b"igImage")):
                        for slot, val, fi in obj._raw_fields:
                            if slot == 11 and fi.short_name == b"Enum":
                                pfmts.add(val)
                except Exception:
                    continue
            # DXT3 (15) is the one format ONLY XML2 uses; CLUT (65536) is
            # platform-neutral, 14/16 are MUA-style. If nothing says
            # "XML2 native", trust the path.
            if 15 not in pfmts:
                return mua

    return best_profile


# ---------------------------------------------------------------------------
# Built-in profiles
# ---------------------------------------------------------------------------

register_profile(GameProfile(
    game_id="xml2_pc",
    game_name="X-Men Legends 2 (PC)",
    engine_version="Alchemy 2.5/3.2",
    min_version=4,
    max_version=7,
    expected_endian="little",
    geometry=GeometryConfig(
        geometry_attr_version="v1",
        ext_slot_positions=0,
        ext_slot_normals=1,
        ext_slot_colors=2,
        ext_slot_texcoords=11,
        color_byte_order="abgr",
    ),
    texture=TextureConfig(
        uv_v_flip=True,
        expected_pixel_formats=(15,),  # DXT3
    ),
    coordinate=CoordinateConfig(
        up_axis="Z",
        transpose_matrices=True,
    ),
    signature_classes=(
        b"igGeometryAttr1_5",
        b"igVertexArray1_1",
    ),
    notes="IGB v6, LE, igGeometryAttr1_5, DXT3 textures",
))

register_profile(GameProfile(
    game_id="mua2_ps2",
    game_name="Marvel: Ultimate Alliance 2 (PS2)",
    engine_version="Alchemy 5.0",
    min_version=8,
    max_version=8,
    expected_endian="little",
    geometry=GeometryConfig(
        geometry_attr_version="v2",
        # v2 geometry uses VIF packet data, not ext indexed slots.
        # These slot values are unused for v2 but kept as defaults.
        color_byte_order="rgba",  # PS2 OpenGL convention
    ),
    texture=TextureConfig(
        uv_v_flip=False,           # PS2 OpenGL convention: no V-flip
        expected_pixel_formats=(65536, 65537, 7),  # PS2 paletted + uncompressed
        needs_unswizzle=True,
        has_clut=True,
    ),
    coordinate=CoordinateConfig(
        up_axis="Z",
        transpose_matrices=True,
    ),
    signature_classes=(
        b"igGeometryAttr2",
        b"igVertexArray2",
        b"igPsx2VertexStream",
        b"igClut",
    ),
    notes="IGB v8, LE, igGeometryAttr2, PS2 VIF vertex data, CLUT textures",
))

register_profile(GameProfile(
    game_id="xml1_ps2",
    game_name="X-Men Legends 1 (PS2)",
    engine_version="Alchemy 2.5",
    backface_cull_default=False,
    min_version=5,
    max_version=7,
    expected_endian="little",
    geometry=GeometryConfig(
        geometry_attr_version="v1",
        ext_slot_positions=0,
        ext_slot_normals=1,
        ext_slot_colors=2,
        ext_slot_texcoords=11,
        color_byte_order="abgr",
    ),
    texture=TextureConfig(
        uv_v_flip=True,              # Same DirectX UV convention as XML2 PC
        expected_pixel_formats=(65536, 65537, 8, 0),  # PS2 paletted + uncompressed
        needs_unswizzle=False,        # Data already linear in IGB
        has_clut=True,
    ),
    coordinate=CoordinateConfig(
        up_axis="Z",
        transpose_matrices=True,
    ),
    signature_classes=(
        b"igGeometryAttr1_5",
        b"igVertexArray1_1",
        b"igClut",
    ),
    notes="IGB v6, LE, igGeometryAttr1_5, CLUT paletted textures",
))

# XML2 PS2 and MUA1 PS2 are byte-structurally identical to XML2 PC v6
# (float32 v1 geometry, slots 0/1/11, DirectX UVs) — only the texture is CLUT
# (pfmt 65536 PSMT8 + igClut 256xRGBA8888) instead of DXT3. Verified: 0101.igb
# across XML1/XML2/MUA1 PS2 all = v6 / igGeometryAttr1_5 / pfmt 65536 / igClut.
# (MUA2 PS2 is the odd one out — v8 + igPsx2VertexStream VIF; see mua2_ps2.)
register_profile(GameProfile(
    game_id="xml2_ps2",
    game_name="X-Men Legends 2 (PS2)",
    engine_version="Alchemy 2.5/3.2",
    min_version=5,
    max_version=7,
    expected_endian="little",
    geometry=GeometryConfig(
        geometry_attr_version="v1",
        ext_slot_positions=0,
        ext_slot_normals=1,
        ext_slot_colors=2,
        ext_slot_texcoords=11,
        color_byte_order="abgr",
    ),
    texture=TextureConfig(
        uv_v_flip=True,              # Same DirectX UV convention as XML2 PC
        expected_pixel_formats=(65536, 65537, 8, 0),
        needs_unswizzle=False,        # Data already linear in IGB
        has_clut=True,
    ),
    coordinate=CoordinateConfig(up_axis="Z", transpose_matrices=True),
    signature_classes=(
        b"igGeometryAttr1_5",
        b"igVertexArray1_1",
        b"igClut",
    ),
    notes="IGB v6, LE, igGeometryAttr1_5, CLUT paletted textures",
))

register_profile(GameProfile(
    game_id="mua1_ps2",
    game_name="Marvel: Ultimate Alliance (PS2)",
    engine_version="Alchemy 3.2",
    min_version=5,
    max_version=7,
    expected_endian="little",
    geometry=GeometryConfig(
        geometry_attr_version="v1",
        ext_slot_positions=0,
        ext_slot_normals=1,
        ext_slot_colors=2,
        ext_slot_texcoords=11,
        color_byte_order="abgr",
    ),
    texture=TextureConfig(
        uv_v_flip=True,
        expected_pixel_formats=(65536, 65537, 8, 0),
        needs_unswizzle=False,
        has_clut=True,
    ),
    coordinate=CoordinateConfig(up_axis="Z", transpose_matrices=True),
    signature_classes=(
        b"igGeometryAttr1_5",
        b"igVertexArray1_1",
        b"igClut",
    ),
    notes="IGB v6, LE, igGeometryAttr1_5, CLUT paletted textures (MUA1 PS2)",
))

# XML1 / XML2 GameCube: v6 + v1 float32 geometry (identical to PC/PS2-classic),
# LITTLE-ENDIAN container (the GC PowerPC engine byte-swaps at load), CMPR
# textures (pfmt 34 = GX-tiled DXT1, no CLUT). Verified: 0101.igb GC = v6 /
# igGeometryAttr1_5 / pfmt 34 / 0.5 byte-per-px. (Wii MUA = v8 + CMPR; separate.)
register_profile(GameProfile(
    game_id="xml1_gc",
    game_name="X-Men Legends 1 (GameCube)",
    engine_version="Alchemy 2.5",
    backface_cull_default=False,
    min_version=5,
    max_version=7,
    expected_endian="little",
    geometry=GeometryConfig(
        geometry_attr_version="v1",
        ext_slot_positions=0,
        ext_slot_normals=1,
        ext_slot_colors=2,
        ext_slot_texcoords=11,
        color_byte_order="abgr",
    ),
    texture=TextureConfig(
        uv_v_flip=True,
        expected_pixel_formats=(34, 21),  # GC/Wii CMPR
    ),
    coordinate=CoordinateConfig(up_axis="Z", transpose_matrices=True),
    signature_classes=(
        b"igGeometryAttr1_5",
        b"igVertexArray1_1",
    ),
    notes="IGB v6, LE, igGeometryAttr1_5, CMPR (GX-tiled) textures (GameCube)",
))

register_profile(GameProfile(
    game_id="xml2_gc",
    game_name="X-Men Legends 2 (GameCube)",
    engine_version="Alchemy 2.5/3.2",
    min_version=5,
    max_version=7,
    expected_endian="little",
    geometry=GeometryConfig(
        geometry_attr_version="v1",
        ext_slot_positions=0,
        ext_slot_normals=1,
        ext_slot_colors=2,
        ext_slot_texcoords=11,
        color_byte_order="abgr",
    ),
    texture=TextureConfig(
        uv_v_flip=True,
        expected_pixel_formats=(34, 21),  # GC/Wii CMPR
    ),
    coordinate=CoordinateConfig(up_axis="Z", transpose_matrices=True),
    signature_classes=(
        b"igGeometryAttr1_5",
        b"igVertexArray1_1",
    ),
    notes="IGB v6, LE, igGeometryAttr1_5, CMPR (GX-tiled) textures (GameCube)",
))

# ---- Group A: v1 geometry platforms (same pipeline as XML2 PC) ----

register_profile(GameProfile(
    game_id="xml1_xbox",
    game_name="X-Men Legends 1 (Xbox)",
    engine_version="Alchemy 2.5",
    backface_cull_default=False,
    min_version=5,
    max_version=7,
    expected_endian="little",
    geometry=GeometryConfig(
        geometry_attr_version="v1",
        ext_slot_positions=0,
        ext_slot_normals=1,
        ext_slot_colors=2,
        ext_slot_texcoords=11,
        color_byte_order="abgr",
    ),
    texture=TextureConfig(
        uv_v_flip=True,
        expected_pixel_formats=(15,),  # DXT3
    ),
    coordinate=CoordinateConfig(
        up_axis="Z",
        transpose_matrices=True,
    ),
    signature_classes=(
        b"igGeometryAttr1_5",
        b"igVertexArray1_1",
    ),
    notes="IGB v6, LE, igGeometryAttr1_5, DXT3 textures (Xbox)",
))

register_profile(GameProfile(
    game_id="xml2_xbox",
    game_name="X-Men Legends 2 (Xbox)",
    engine_version="Alchemy 2.5/3.2",
    min_version=5,
    max_version=7,
    expected_endian="little",
    geometry=GeometryConfig(
        geometry_attr_version="v1",
        ext_slot_positions=0,
        ext_slot_normals=1,
        ext_slot_colors=2,
        ext_slot_texcoords=11,
        color_byte_order="abgr",
    ),
    texture=TextureConfig(
        uv_v_flip=True,
        expected_pixel_formats=(15,),  # DXT3
    ),
    coordinate=CoordinateConfig(
        up_axis="Z",
        transpose_matrices=True,
    ),
    signature_classes=(
        b"igGeometryAttr1_5",
        b"igVertexArray1_1",
    ),
    notes="IGB v6, LE, igGeometryAttr1_5, DXT3 textures (Xbox)",
))

register_profile(GameProfile(
    game_id="xbox_mua_proto",
    game_name="MUA Prototype (Xbox)",
    engine_version="Alchemy 3.2",
    min_version=5,
    max_version=7,
    expected_endian="little",
    geometry=GeometryConfig(
        geometry_attr_version="v1",
        ext_slot_positions=0,
        ext_slot_normals=1,
        ext_slot_colors=2,
        ext_slot_texcoords=11,
        color_byte_order="abgr",
    ),
    texture=TextureConfig(
        uv_v_flip=True,
        expected_pixel_formats=(13, 14, 16, 5, 7),  # DXT1, RGBA_DXT1, DXT5, RGB888, RGBA8888
    ),
    coordinate=CoordinateConfig(
        up_axis="Z",
        transpose_matrices=True,
    ),
    signature_classes=(
        b"igGeometryAttr1_5",
        b"igVertexArray1_1",
        b"igNamedObjectInfo",
    ),
    notes="IGB v6, LE, igGeometryAttr1_5, DXT1/DXT5 textures (Xbox MUA Prototype)",
))

register_profile(GameProfile(
    game_id="mua_wii",
    game_name="Marvel: Ultimate Alliance (Wii)",
    engine_version="Alchemy 5.0",
    min_version=8,
    max_version=8,
    expected_endian="little",
    geometry=GeometryConfig(
        geometry_attr_version="v1",  # Wii uses v1 geometry despite v8 format!
        ext_slot_positions=0,
        ext_slot_normals=1,
        ext_slot_colors=2,
        ext_slot_texcoords=11,
        color_byte_order="abgr",
    ),
    texture=TextureConfig(
        uv_v_flip=True,
        expected_pixel_formats=(21,),  # Wii CMPR (GC tiled RGBA 5553)
    ),
    coordinate=CoordinateConfig(
        up_axis="Z",
        transpose_matrices=True,
    ),
    signature_classes=(
        b"igGeometryAttr1_5",
        b"igVertexArray1_1",
    ),
    notes="IGB v8, LE, igGeometryAttr1_5, Wii CMPR textures",
))

# ---- Group B: v2 geometry platforms ----

register_profile(GameProfile(
    game_id="mua_pc",
    game_name="Marvel: Ultimate Alliance (PC)",
    engine_version="Alchemy 5.0",
    min_version=8,
    max_version=8,
    expected_endian="little",
    geometry=GeometryConfig(
        geometry_attr_version="v2",
        color_byte_order="abgr",
    ),
    texture=TextureConfig(
        uv_v_flip=True,
        expected_pixel_formats=(14, 16, 5),  # RGBA_DXT1, DXT5, RGB888
        swap_rb=True,  # MUA PC uses BGR565 color endpoints (R/B swapped vs XML2)
    ),
    coordinate=CoordinateConfig(
        up_axis="Z",
        transpose_matrices=True,
    ),
    signature_classes=(
        b"igGeometryAttr2",
        b"igVertexArray2",
        b"igVertexData",
        b"igVertexStream",
        b"igShaderParametersAttr",
        b"igGlobalColorStateAttr",
    ),
    notes="IGB v8, LE, igGeometryAttr2, igVertexData, DXT1/DXT5 textures",
))

register_profile(GameProfile(
    game_id="mua_xbox360",
    game_name="Marvel: Ultimate Alliance (Xbox 360)",
    engine_version="Alchemy 5.0",
    min_version=8,
    max_version=8,
    expected_endian="little",
    geometry=GeometryConfig(
        geometry_attr_version="v2",
        color_byte_order="abgr",
    ),
    texture=TextureConfig(
        uv_v_flip=True,
        expected_pixel_formats=(16, 44, 7, 5),  # DXT5, DXN, RGBA8888, RGB888
    ),
    coordinate=CoordinateConfig(
        up_axis="Z",
        transpose_matrices=True,
    ),
    signature_classes=(
        b"igGeometryAttr2",
        b"igVertexArray2",
        b"igVertexData",
        b"igVertexStream",
        b"igShaderParametersAttr",
        b"igGlobalColorStateAttr",
    ),
    notes="IGB v8, LE, igGeometryAttr2, igVertexData, DXT5/DXN textures (Xbox 360)",
))

register_profile(GameProfile(
    game_id="xml2_psp",
    game_name="X-Men Legends 2 (PSP)",
    engine_version="Alchemy 5.0",
    min_version=8,
    max_version=8,
    expected_endian="little",
    geometry=GeometryConfig(
        geometry_attr_version="v2",
        color_byte_order="rgba",
    ),
    texture=TextureConfig(
        uv_v_flip=False,  # PSP uses OpenGL convention
        expected_pixel_formats=(42, 43),  # PSP tiled 8-bit/4-bit CLUT
        has_clut=True,
    ),
    coordinate=CoordinateConfig(
        up_axis="Z",
        transpose_matrices=True,
    ),
    signature_classes=(
        b"igGeometryAttr2",
        b"igVertexArray2",
        b"igVertexData",
        b"igVertexStream",
        b"igClut",
        b"igVertexBlendMatrixListAttr",
    ),
    notes="IGB v8, LE, igGeometryAttr2, igVertexData, PSP CLUT textures",
))

register_profile(GameProfile(
    game_id="mua_psp",
    game_name="Marvel: Ultimate Alliance (PSP)",
    engine_version="Alchemy 5.0",
    min_version=8,
    max_version=8,
    expected_endian="little",
    geometry=GeometryConfig(
        geometry_attr_version="v2",
        color_byte_order="rgba",
    ),
    texture=TextureConfig(
        uv_v_flip=False,  # PSP uses OpenGL convention
        expected_pixel_formats=(42, 43),  # PSP tiled 8-bit/4-bit CLUT
        has_clut=True,
    ),
    coordinate=CoordinateConfig(
        up_axis="Z",
        transpose_matrices=True,
    ),
    signature_classes=(
        b"igGeometryAttr2",
        b"igVertexArray2",
        b"igVertexData",
        b"igVertexStream",
        b"igClut",
    ),
    notes="IGB v8, LE, igGeometryAttr2, igVertexData, PSP CLUT textures",
))
