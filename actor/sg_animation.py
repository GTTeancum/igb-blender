"""Parse animations from IGB files.

Extracts animation data from igAnimationDatabase -> igAnimationList.

XML2 uses two transform source types:
1. igTransformSequence1_5: Standard Alchemy format with igVec3fList,
   igQuaternionfList, igLongList keyframes.
2. igEnbayaTransformSource/igEnbayaAnimationSource: Raven's proprietary
   compressed animation format. Decompressed using enbaya.py
   (ported from korenkonder/enbrip, MIT License).

Field layouts (verified from XML2 v6 game files):

igAnimation:
    slot 2: String (name)
    slot 3: Int (_priority)
    slot 4: ObjectRef (_bindingList -> igAnimationBindingList)
    slot 5: ObjectRef (_trackList -> igAnimationTrackList)
    slot 6: ObjectRef (_transitionDefList)
    slot 7: Long (_startTime, nanoseconds)
    slot 8: Long (_keyFrameTimeOffset, nanoseconds)
    slot 9: Long (_duration, nanoseconds)
    slot 10: ObjectRef (_bitMask)

igAnimationTrack:
    slot 2: String (name = bone name)
    slot 3: ObjectRef (_source -> igTransformSource subclass)
    slot 4: Vec4f (_constantQuaternion, XYZW)
    slot 5: Vec3f (_constantTranslation)

igAnimationBinding:
    slot 2: ObjectRef (_skeleton -> igSkeleton)
    slot 3: MemoryRef (_boneTrackIdxArray -> Int[boneCount])
    slot 4: Int (_bindCount)

igTransformSequence1_5:
    slot 2: ObjectRef (_xlateList -> igVec3fList)
    slot 3: ObjectRef (_quatList -> igQuaternionfList)
    slot 11: ObjectRef (_timeListLong -> igLongList)
    slot 15: UnsignedChar (_drivenChannels bitmask)
    slot 17: Long (_keyFrameTimeOffset)
    slot 18: Long (_animationDuration)

igEnbayaTransformSource:
    slot 2: Int (_trackId — index into shared Enbaya blob)
    slot 3: ObjectRef (_enbayaAnimSource -> igEnbayaAnimationSource)

igEnbayaAnimationSource:
    slot 2: MemoryRef (_enbayaAnimationStream — compressed data blob)
    slot 3: UnsignedCharArray (_interpolationMethod, 3 bytes)
    slot 4: UnsignedChar (_drivenChannels)
    slot 5: Enum (_playMode)
"""

import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict

from ..igb_format.igb_objects import IGBObject, IGBMemoryBlock

# Channel bitmask constants
CHANNEL_TRANSLATION = 0x01
CHANNEL_QUATERNION = 0x02
CHANNEL_EULER = 0x04
CHANNEL_SCALE = 0x08


@dataclass
class ParsedKeyframe:
    """A single keyframe for one bone."""
    time_ms: float              # time in milliseconds
    quaternion: Tuple[float, float, float, float]  # (w, x, y, z) Blender order
    translation: Tuple[float, float, float]        # (x, y, z)
    scale: Optional[Tuple[float, float, float]] = None


@dataclass
class ParsedAnimationTrack:
    """Animation track for one bone."""
    bone_name: str
    bone_index: int             # index in skeleton
    keyframes: List[ParsedKeyframe]
    is_constant: bool = False   # True if track uses constant quat/xlate only


@dataclass
class ParsedAnimation:
    """A complete animation clip."""
    name: str
    duration_ms: float          # in milliseconds
    tracks: List[ParsedAnimationTrack]
    priority: int = 0
    source_obj: Optional[IGBObject] = field(default=None, repr=False)
    # True when EVERY keyframe of every track is the identity quaternion with
    # zero translation — an engine-side "hold current pose" clip (e.g. XML2
    # lunge_loop: the lunge pose carries over from lunge_start; the file
    # itself contains no pose). Standalone, the honest playback is REST HOLD;
    # applying it through the bind-delta path scrambles the rig.
    identity_hold: bool = False


def extract_animations(reader, skeleton=None) -> List[ParsedAnimation]:
    """Extract all animations from an IGB file.

    Args:
        reader: IGBReader instance (already parsed).
        skeleton: Optional ParsedSkeleton for bone name mapping.

    Returns:
        List of ParsedAnimation.
    """
    # Direct type scan — this reliably finds ALL igAnimation objects in the
    # file regardless of how the igAnimationDatabase/igAnimationList stores
    # its references (some files have list formats that resolve_object_list
    # cannot handle correctly).
    anim_objs = reader.get_objects_by_type(b"igAnimation")

    # Cache for decompressed Enbaya blobs (shared across animations in the same file).
    # Key: igEnbayaAnimationSource object index -> decompressed per-track keyframes.
    enbaya_cache = {}

    results = []
    for anim_obj in anim_objs:
        if not isinstance(anim_obj, IGBObject):
            continue
        parsed = _parse_animation(reader, anim_obj, skeleton, enbaya_cache)
        if parsed:
            results.append(parsed)

    return results


def extract_animation_names(reader) -> List[Tuple[str, float]]:
    """Quick extraction of animation names and durations without full parsing.

    Returns:
        List of (name, duration_ms) tuples.
    """
    anim_objs = reader.get_objects_by_type(b"igAnimation")

    s = reader.slot_offset  # v4/v5 slots are +1 vs v6
    results = []
    for obj in anim_objs:
        if not isinstance(obj, IGBObject):
            continue
        name = ""
        duration_ns = 0
        for slot, val, fi in obj._raw_fields:
            if fi.short_name == b"String":
                name = val if isinstance(val, str) else ""
            elif fi.short_name == b"Long" and slot == 9 + s:
                duration_ns = val
        results.append((name, duration_ns / 1_000_000.0))  # ns -> ms

    return results


def _find_anim_list(reader, anim_db_obj):
    """Find the igAnimationList from an igAnimationDatabase."""
    s = reader.slot_offset  # v4/v5 slots are +1 vs v6
    for slot, val, fi in anim_db_obj._raw_fields:
        if fi.short_name == b"ObjectRef" and val != -1 and slot == 6 + s:
            ref = reader.resolve_ref(val)
            if isinstance(ref, IGBObject) and ref.is_type(b"igObjectList"):
                return ref
    return None


def _parse_animation(reader, anim_obj, skeleton=None, enbaya_cache=None) -> Optional[ParsedAnimation]:
    """Parse a single igAnimation into a ParsedAnimation."""
    endian = reader.header.endian
    name = ""
    priority = 0
    duration_ns = 0
    track_list_ref = None
    binding_list_ref = None

    if enbaya_cache is None:
        enbaya_cache = {}

    s = reader.slot_offset  # v4/v5 slots are +1 vs v6
    for slot, val, fi in anim_obj._raw_fields:
        if fi.short_name == b"String":
            name = val if isinstance(val, str) else ""
        elif fi.short_name == b"Int" and slot == 3 + s:
            priority = val
        elif fi.short_name == b"Long" and slot == 9 + s:
            duration_ns = val
        elif fi.short_name == b"ObjectRef" and val != -1:
            ref = reader.resolve_ref(val)
            if isinstance(ref, IGBObject):
                if ref.is_type(b"igAnimationTrackList") or (slot == 5 + s and ref.is_type(b"igObjectList")):
                    track_list_ref = val
                elif ref.is_type(b"igAnimationBindingList") or (slot == 4 + s and ref.is_type(b"igObjectList")):
                    binding_list_ref = val

    # Parse binding to get bone->track mapping
    bone_track_map = None
    if binding_list_ref is not None:
        bone_track_map = _parse_binding(reader, binding_list_ref, endian)

    # Parse tracks
    tracks = []
    if track_list_ref is not None:
        tl = reader.resolve_ref(track_list_ref)
        if isinstance(tl, IGBObject):
            track_objs = reader.resolve_object_list(tl)
            for ti, track_obj in enumerate(track_objs):
                if not isinstance(track_obj, IGBObject):
                    continue
                track = _parse_track(reader, track_obj, ti, endian, enbaya_cache)
                if track:
                    tracks.append(track)

    duration_ms = duration_ns / 1_000_000.0

    # Detect identity-hold clips (see ParsedAnimation.identity_hold).
    identity_hold = bool(tracks)
    for t in tracks:
        for kf in t.keyframes:
            q = kf.quaternion  # WXYZ
            x = kf.translation
            if (abs(abs(q[0]) - 1.0) > 0.02 or abs(q[1]) > 0.02
                    or abs(q[2]) > 0.02 or abs(q[3]) > 0.02
                    or abs(x[0]) > 0.1 or abs(x[1]) > 0.1 or abs(x[2]) > 0.1):
                identity_hold = False
                break
        if not identity_hold:
            break

    return ParsedAnimation(
        name=name,
        duration_ms=duration_ms,
        tracks=tracks,
        priority=priority,
        source_obj=anim_obj,
        identity_hold=identity_hold,
    )


def _parse_binding(reader, binding_list_ref, endian) -> Optional[Dict[int, int]]:
    """Parse igAnimationBinding to get bone_index -> track_index mapping.

    Returns:
        Dict mapping bone_index -> track_index, or None.
    """
    bl = reader.resolve_ref(binding_list_ref)
    if not isinstance(bl, IGBObject):
        return None

    bindings = reader.resolve_object_list(bl)
    for binding in bindings:
        if not isinstance(binding, IGBObject):
            continue

        bind_count = 0
        track_idx_ref = None

        s = reader.slot_offset  # v4/v5 slots are +1 vs v6
        for slot, val, fi in binding._raw_fields:
            if fi.short_name == b"MemoryRef" and val != -1:
                track_idx_ref = val
            elif fi.short_name == b"Int" and slot == 4 + s:
                bind_count = val

        if track_idx_ref is not None:
            block = reader.resolve_ref(track_idx_ref)
            if isinstance(block, IGBMemoryBlock) and block.data:
                n = block.mem_size // 4
                values = struct.unpack_from(endian + "i" * n, block.data, 0)
                mapping = {}
                for bone_idx, track_idx in enumerate(values):
                    if track_idx >= 0:
                        mapping[bone_idx] = track_idx
                return mapping

    return None


def _parse_track(reader, track_obj, track_index, endian, enbaya_cache=None) -> Optional[ParsedAnimationTrack]:
    """Parse a single igAnimationTrack."""
    bone_name = ""
    source_ref = None
    const_quat = (0.0, 0.0, 0.0, 1.0)  # XYZW identity
    const_xlate = (0.0, 0.0, 0.0)

    if enbaya_cache is None:
        enbaya_cache = {}

    s = reader.slot_offset  # v4/v5 slots are +1 vs v6
    for slot, val, fi in track_obj._raw_fields:
        if fi.short_name == b"String":
            bone_name = val if isinstance(val, str) else ""
        elif fi.short_name == b"ObjectRef" and val != -1 and slot == 3 + s:
            source_ref = val
        elif fi.short_name in (b"Vec4f", b"Quaternionf"):
            const_quat = val  # (x, y, z, w) Alchemy order
        elif fi.short_name == b"Vec3f":
            const_xlate = val

    # Try to parse the source
    keyframes = []
    is_constant = True

    if source_ref is not None:
        src = reader.resolve_ref(source_ref)
        if isinstance(src, IGBObject):
            if src.is_type(b"igTransformSequence1_5") or src.is_type(b"igTransformSequence"):
                keyframes = _parse_transform_sequence(reader, src, endian)
                is_constant = len(keyframes) == 0
            elif src.is_type(b"igEnbayaTransformSource"):
                keyframes = _parse_enbaya_source(reader, src, endian, enbaya_cache)
                is_constant = len(keyframes) == 0

    # If no keyframes from source, use constant values
    if not keyframes:
        # Convert Alchemy XYZW quaternion to Blender WXYZ
        qw = const_quat[3] if len(const_quat) == 4 else 1.0
        qx = const_quat[0] if len(const_quat) >= 1 else 0.0
        qy = const_quat[1] if len(const_quat) >= 2 else 0.0
        qz = const_quat[2] if len(const_quat) >= 3 else 0.0

        keyframes = [ParsedKeyframe(
            time_ms=0.0,
            quaternion=(qw, qx, qy, qz),
            translation=const_xlate,
        )]
        is_constant = True

    return ParsedAnimationTrack(
        bone_name=bone_name,
        bone_index=track_index,
        keyframes=keyframes,
        is_constant=is_constant,
    )


def _parse_transform_sequence(reader, seq_obj, endian) -> List[ParsedKeyframe]:
    """Parse igTransformSequence1_5 keyframes.

    Slot layout:
        slot 2: ObjectRef (_xlateList -> igVec3fList)
        slot 3: ObjectRef (_quatList -> igQuaternionfList)
        slot 11: ObjectRef (_timeListLong -> igLongList)
        slot 15: UnsignedChar (_drivenChannels bitmask)
    """
    xlate_list_ref = None
    quat_list_ref = None
    time_list_ref = None
    driven_channels = 0x03  # default: translation + quaternion

    s = reader.slot_offset  # v4/v5 slots are +1 vs v6
    for slot, val, fi in seq_obj._raw_fields:
        if fi.short_name == b"ObjectRef" and val != -1:
            if slot == 2 + s:
                xlate_list_ref = val
            elif slot == 3 + s:
                quat_list_ref = val
            elif slot == 11 + s:
                time_list_ref = val
        elif fi.short_name == b"UnsignedChar" and slot == 15 + s:
            driven_channels = val

    # Parse time list
    times_ns = _parse_long_list(reader, time_list_ref, endian)

    # Parse quaternion list
    quats = _parse_quatf_list(reader, quat_list_ref, endian)

    # Parse translation list
    xlates = _parse_vec3f_list(reader, xlate_list_ref, endian)

    # Build keyframes
    num_keys = max(len(times_ns), len(quats), len(xlates))
    if num_keys == 0:
        return []

    keyframes = []
    for i in range(num_keys):
        time_ms = times_ns[i] / 1_000_000.0 if i < len(times_ns) else 0.0

        # Quaternion: Alchemy XYZW -> Blender WXYZ
        if i < len(quats):
            q = quats[i]
            quat = (q[3], q[0], q[1], q[2])  # (w, x, y, z)
        else:
            quat = (1.0, 0.0, 0.0, 0.0)

        xlate = xlates[i] if i < len(xlates) else (0.0, 0.0, 0.0)

        keyframes.append(ParsedKeyframe(
            time_ms=time_ms,
            quaternion=quat,
            translation=xlate,
        ))

    return keyframes


def _parse_enbaya_source(reader, src_obj, endian, enbaya_cache=None) -> List[ParsedKeyframe]:
    """Parse igEnbayaTransformSource → decompress the shared Enbaya blob.

    igEnbayaTransformSource has:
        slot 2: Int (_trackId — which track in the shared blob)
        slot 3: ObjectRef (_enbayaAnimationSource -> igEnbayaAnimationSource)

    igEnbayaAnimationSource has:
        slot 2: MemoryRef (_enbayaAnimationStream — compressed data blob)

    All bone tracks in one animation share the same igEnbayaAnimationSource.
    We cache the decompressed result keyed by the source object index.
    """
    from .enbaya import decompress_enbaya_to_tracks

    if enbaya_cache is None:
        enbaya_cache = {}

    # Extract track_id and source reference
    track_id = -1
    eas_ref = None

    s = reader.slot_offset  # v4/v5 slots are +1 vs v6
    for slot, val, fi in src_obj._raw_fields:
        if fi.short_name == b"Int" and slot == 2 + s:
            track_id = val
        elif fi.short_name == b"ObjectRef" and val != -1:
            eas_ref = val

    if track_id < 0 or eas_ref is None:
        return []

    # Resolve the igEnbayaAnimationSource
    eas_obj = reader.resolve_ref(eas_ref)
    if not isinstance(eas_obj, IGBObject):
        return []

    # Check cache
    cache_key = eas_obj.index
    if cache_key not in enbaya_cache:
        # Extract the compressed data blob
        blob_data = None
        for slot, val, fi in eas_obj._raw_fields:
            if fi.short_name == b"MemoryRef" and val != -1:
                block = reader.resolve_ref(val)
                if isinstance(block, IGBMemoryBlock) and block.data and block.mem_size > 0:
                    blob_data = bytes(block.data[:block.mem_size])
                    break

        if blob_data is None or len(blob_data) < 80:
            enbaya_cache[cache_key] = None
        else:
            # Validate the enbaya header before attempting decompression.
            # Many XML2 files have encrypted/obfuscated enbaya blobs where
            # the header contains garbage. Feeding these to the decoder can
            # cause hangs (infinite loops) because the garbage track_count
            # or sample_rate leads to enormous allocations or endless loops.
            from .enbaya import EnbayaStream as _ES
            _hdr = _ES(blob_data, endian=endian)
            # sample_rate lower bound is 1, NOT 10 — real game anims ship at
            # low rates (XML1 01_cyclops power_3 = 8 Hz; ~122 such anims were
            # silently dropped to their constant-quat fallback, rendering as
            # frozen garbage poses). The other bounds still reject the
            # encrypted/obfuscated blobs this guard exists for.
            _header_ok = (
                _hdr.track_count >= 1 and _hdr.track_count <= 500 and
                _hdr.sample_rate >= 1 and _hdr.sample_rate <= 240 and
                _hdr.duration >= 0.01 and _hdr.duration <= 300 and
                _hdr.quantization_error >= 1e-6 and
                _hdr.quantization_error <= 0.5
            )
            if not _header_ok:
                # Encrypted/invalid blob — skip decompression entirely
                enbaya_cache[cache_key] = None
            else:
                try:
                    # Decompress to per-track keyframes
                    # Each track is a list of (time_ms, quat_wxyz, trans_xyz)
                    tracks = decompress_enbaya_to_tracks(
                        blob_data, endian=endian, fps=30.0
                    )
                    enbaya_cache[cache_key] = tracks
                except Exception:
                    # If decompression fails, cache None → fall back to constant
                    enbaya_cache[cache_key] = None

    cached = enbaya_cache.get(cache_key)
    if cached is None or track_id >= len(cached):
        return []

    # Convert our track data to ParsedKeyframe list
    track_data = cached[track_id]
    keyframes = []
    for time_ms, quat_wxyz, trans_xyz in track_data:
        keyframes.append(ParsedKeyframe(
            time_ms=time_ms,
            quaternion=quat_wxyz,   # Already WXYZ (Blender order)
            translation=trans_xyz,
        ))

    return keyframes


def _parse_long_list(reader, ref, endian):
    """Parse igLongList (ObjectRef) into a list of int64 values."""
    if ref is None or ref == -1:
        return []
    obj = reader.resolve_ref(ref)
    if not isinstance(obj, IGBObject):
        return []

    # igLongList is an igObjectList variant with a data block of int64 values
    data_ref = None
    count = 0
    for slot, val, fi in obj._raw_fields:
        if fi.short_name == b"Int" and count == 0:
            count = val
        elif fi.short_name == b"MemoryRef" and val != -1:
            data_ref = val

    if data_ref is None or count == 0:
        return []

    block = reader.resolve_ref(data_ref)
    if not isinstance(block, IGBMemoryBlock) or not block.data:
        return []

    result = []
    for i in range(min(count, block.mem_size // 8)):
        val = struct.unpack_from(endian + "q", block.data, i * 8)[0]
        result.append(val)
    return result


def _parse_quatf_list(reader, ref, endian):
    """Parse igQuaternionfList into list of (x,y,z,w) tuples."""
    if ref is None or ref == -1:
        return []
    obj = reader.resolve_ref(ref)
    if not isinstance(obj, IGBObject):
        return []

    data_ref = None
    count = 0
    for slot, val, fi in obj._raw_fields:
        if fi.short_name == b"Int" and count == 0:
            count = val
        elif fi.short_name == b"MemoryRef" and val != -1:
            data_ref = val

    if data_ref is None or count == 0:
        return []

    block = reader.resolve_ref(data_ref)
    if not isinstance(block, IGBMemoryBlock) or not block.data:
        return []

    result = []
    for i in range(min(count, block.mem_size // 16)):
        x, y, z, w = struct.unpack_from(endian + "ffff", block.data, i * 16)
        result.append((x, y, z, w))
    return result


def _parse_vec3f_list(reader, ref, endian):
    """Parse igVec3fList into list of (x,y,z) tuples."""
    if ref is None or ref == -1:
        return []
    obj = reader.resolve_ref(ref)
    if not isinstance(obj, IGBObject):
        return []

    data_ref = None
    count = 0
    for slot, val, fi in obj._raw_fields:
        if fi.short_name == b"Int" and count == 0:
            count = val
        elif fi.short_name == b"MemoryRef" and val != -1:
            data_ref = val

    if data_ref is None or count == 0:
        return []

    block = reader.resolve_ref(data_ref)
    if not isinstance(block, IGBMemoryBlock) or not block.data:
        return []

    result = []
    for i in range(min(count, block.mem_size // 12)):
        x, y, z = struct.unpack_from(endian + "fff", block.data, i * 12)
        result.append((x, y, z))
    return result
