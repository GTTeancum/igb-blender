"""Motion path import — Alchemy motionpaths/*.igb → animated empties + curves.

A motion path IGB (MUA motionpaths/, also XML2) is a tiny scene:
    igSceneInfo → igTransform "mp_<name>" (slot 10 _transformInput)
        → igTransformSequence1_5:
            slot 2  ObjectRef → igVec3fList        translations (one per key)
            slot 3  ObjectRef → igQuaternionfList  rotations (XYZW, engine row-vector)
            slot 4  ObjectRef → igVec3fList        scales
            slot 11 ObjectRef → igLongList         key times in NANOSECONDS
                                                   (33333335 ≈ 1/30 s per key)
The game binds them via script calls like
    cameraFollowMotionPath("heli_carrier/opening_cam", "mp_opening_cam", ...)
where arg 1 is the path under motionpaths/ and arg 2 the igTransform name.

Import strategy: one Empty per mp_* node, keyframed at the sequence keys
(engine quaternions conjugated for Blender — same row→column conversion as
the actor animation pipeline), plus a poly Curve of the translation track
for an always-visible path line.
"""

import os
import re
import struct

import bpy
from mathutils import Matrix, Quaternion

from ..igb_format.igb_reader import IGBReader
from ..igb_format.igb_objects import IGBObject, IGBMemoryBlock

NS_PER_SECOND = 1.0e9


class MotionPathTrack:
    """One mp_* node: keyed transform track."""

    def __init__(self, name):
        self.name = name
        self.times = []       # seconds
        self.locations = []   # (x, y, z)
        self.rotations = []   # Quaternion (Blender convention)
        self.scales = []      # (x, y, z)
        self.base_matrix = None  # Blender-convention Matrix or None


def _mem_of_list(reader, list_obj):
    """Resolve an ig*List object's MemoryRef data bytes (or None)."""
    if not isinstance(list_obj, IGBObject):
        return None
    for slot, val, fi in list_obj._raw_fields:
        if fi.short_name == b"MemoryRef" and val != -1:
            mem = reader.resolve_ref(val)
            if isinstance(mem, IGBMemoryBlock) and mem.data:
                return bytes(mem.data)
    return None


def _list_count(list_obj):
    for slot, val, fi in list_obj._raw_fields:
        if fi.short_name == b"Int":
            return val
    return 0


def _obj_ref(reader, obj, slot_wanted):
    for slot, val, fi in obj._raw_fields:
        if slot == slot_wanted and fi.short_name == b"ObjectRef" and val != -1:
            return reader.resolve_ref(val)
    return None


def _alchemy_matrix_to_blender(m16):
    """Row-major Alchemy matrix (translation in last row) → Blender Matrix."""
    return Matrix((
        (m16[0], m16[4], m16[8], m16[12]),
        (m16[1], m16[5], m16[9], m16[13]),
        (m16[2], m16[6], m16[10], m16[14]),
        (m16[3], m16[7], m16[11], m16[15]),
    ))


def parse_motionpath_igb(filepath):
    """Parse a motionpath IGB into a list of MotionPathTrack.

    Returns [] when the file contains no igTransformSequence tracks.
    """
    reader = IGBReader(filepath)
    reader.read()

    tracks = []
    for obj in reader.objects:
        if not isinstance(obj, IGBObject) or not obj.is_type(b"igTransform"):
            continue

        # name (slot 2 String) + base matrix + _transformInput (slot 10)
        name = ""
        base_m16 = None
        seq = None
        for slot, val, fi in obj._raw_fields:
            if fi.short_name == b"String" and not name:
                name = (val.decode("utf-8", "replace")
                        if isinstance(val, bytes) else (val or ""))
            elif fi.short_name == b"Matrix44f":
                base_m16 = val
            elif fi.short_name == b"ObjectRef" and val != -1:
                ref = reader.resolve_ref(val)
                # igTransformSequence1_5 inherits igTransformSource, not a
                # plain igTransformSequence base — match by name prefix.
                if (isinstance(ref, IGBObject) and
                        ref.type_name.startswith(b"igTransformSequence")):
                    seq = ref
        if seq is None:
            continue

        track = MotionPathTrack(name or os.path.splitext(
            os.path.basename(filepath))[0])
        if base_m16 is not None:
            track.base_matrix = _alchemy_matrix_to_blender(base_m16)

        trans_list = _obj_ref(reader, seq, 2)
        rot_list = _obj_ref(reader, seq, 3)
        scale_list = _obj_ref(reader, seq, 4)
        time_list = _obj_ref(reader, seq, 11)

        n = 0
        if trans_list is not None:
            n = _list_count(trans_list)
        if n == 0 and rot_list is not None:
            n = _list_count(rot_list)
        if n == 0:
            continue

        tdata = _mem_of_list(reader, trans_list) if trans_list else None
        rdata = _mem_of_list(reader, rot_list) if rot_list else None
        sdata = _mem_of_list(reader, scale_list) if scale_list else None
        timedata = _mem_of_list(reader, time_list) if time_list else None

        for i in range(n):
            if timedata is not None and (i + 1) * 8 <= len(timedata):
                t_ns = struct.unpack_from("<q", timedata, i * 8)[0]
                track.times.append(t_ns / NS_PER_SECOND)
            else:
                track.times.append(i / 30.0)

            if tdata is not None and (i + 1) * 12 <= len(tdata):
                track.locations.append(
                    struct.unpack_from("<fff", tdata, i * 12))
            if rdata is not None and (i + 1) * 16 <= len(rdata):
                x, y, z, w = struct.unpack_from("<ffff", rdata, i * 16)
                # Engine igQuaternionf rotates row-vector style — conjugate
                # converts to Blender's column-vector convention (same rule
                # as the proven actor animation pipeline).
                track.rotations.append(Quaternion((w, -x, -y, -z)))
            if sdata is not None and (i + 1) * 12 <= len(sdata):
                track.scales.append(
                    struct.unpack_from("<fff", sdata, i * 12))

        tracks.append(track)

    return tracks


def build_motionpath_objects(context, tracks, collection,
                             name_prefix="", source_path=""):
    """Create an animated Empty + a visible poly curve per track.

    Returns the list of created empties. Extends the scene frame range to
    fit the longest path. When source_path is given it is stored on each
    empty (igb_motionpath_src) so Export Motion Path can use the original
    file as its patch template for a faithful round-trip.
    """
    scene = context.scene
    fps = scene.render.fps or 30
    created = []

    for track in tracks:
        base = track.base_matrix or Matrix.Identity(4)
        obj_name = f"{name_prefix}{track.name}" if name_prefix else track.name

        # --- path curve (translation trail, in base-matrix space) ---
        if len(track.locations) >= 2:
            curve = bpy.data.curves.new(obj_name + "_path", type='CURVE')
            curve.dimensions = '3D'
            spline = curve.splines.new('POLY')
            spline.points.add(len(track.locations) - 1)
            for i, loc in enumerate(track.locations):
                spline.points[i].co = (loc[0], loc[1], loc[2], 1.0)
            curve_obj = bpy.data.objects.new(obj_name + "_path", curve)
            curve_obj.matrix_world = base
            curve_obj.show_in_front = True
            collection.objects.link(curve_obj)

        # --- animated empty ---
        empty = bpy.data.objects.new(obj_name, None)
        empty.empty_display_type = 'CONE'
        empty.empty_display_size = 20.0
        empty.rotation_mode = 'QUATERNION'
        empty["igb_motionpath"] = track.name
        if source_path:
            empty["igb_motionpath_src"] = source_path
        if track.base_matrix is not None:
            empty["igb_motionpath_base"] = [v for row in track.base_matrix
                                            for v in row]
        collection.objects.link(empty)

        n = len(track.times)
        for i in range(n):
            frame = 1 + track.times[i] * fps
            if i < len(track.locations):
                loc = track.locations[i]
                m = base @ Matrix.Translation(loc)
                empty.location = m.translation
                empty.keyframe_insert("location", frame=frame)
            if i < len(track.rotations):
                rot = track.rotations[i]
                if track.base_matrix is not None:
                    rot = (base.to_quaternion() @ rot)
                empty.rotation_quaternion = rot
                empty.keyframe_insert("rotation_quaternion", frame=frame)
            if i < len(track.scales):
                empty.scale = track.scales[i]
                empty.keyframe_insert("scale", frame=frame)

        if n and track.times[-1] > 0:
            end = int(1 + track.times[-1] * fps) + 1
            if scene.frame_end < end:
                scene.frame_end = end

        created.append(empty)

    return created


# Matches followMotionPath-style script calls:
#   cameraFollowMotionPath("heli_carrier/opening_cam", "mp_opening_cam", ...)
#   entityFollowMotionPath / followMotionPath variants
_SCRIPT_MP_RE = re.compile(
    r'MotionPath\s*\(\s*"([^"]+)"', re.IGNORECASE)


def find_map_motionpaths(game_root, map_path, map_name):
    """Scan the map's script folder for motionpath references.

    Game scripts live at scripts/<map_path>/<map_name>/*.py and reference
    paths relative to motionpaths/ (without extension). Returns a sorted
    list of absolute .igb paths that exist on disk.
    """
    found = set()
    script_dir = os.path.join(game_root, "scripts",
                              *map_path.split("/"), map_name)
    mp_root = os.path.join(game_root, "motionpaths")
    if not os.path.isdir(script_dir) or not os.path.isdir(mp_root):
        return []

    for fn in os.listdir(script_dir):
        if not fn.lower().endswith(".py"):
            continue
        try:
            with open(os.path.join(script_dir, fn), "r",
                      encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        for rel in _SCRIPT_MP_RE.findall(text):
            cand = os.path.join(mp_root, *rel.split("/")) + ".igb"
            if os.path.isfile(cand):
                found.add(os.path.normpath(cand))

    return sorted(found)
