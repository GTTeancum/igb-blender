"""Motion path EXPORT — write an edited path back to a motionpaths/*.igb.

Template-based (the proven from_reader byte-surgical approach used by
anim_scale): take the ORIGINAL motionpath file the empty was imported from
(igb_motionpath_src) as the template and replace the igTransformSequence1_5
keyframe lists — translations (igVec3fList), rotations (igQuaternionfList),
times (igLongList, NANOSECONDS) — plus their counts and the sequence
duration. Everything else (scene info, transform node, metas) stays
byte-identical, so the file loads exactly like a native one.

Conventions (mirror of motionpath_import):
- Engine quats are row-vector XYZW; Blender quats were imported as the
  CONJUGATE → exporting conjugates again: engine = (-x, -y, -z, w).
- The igTransform's base matrix was applied on import; keyframes here are
  re-expressed relative to it before writing.
"""

import os
import struct
import logging

import bpy
from mathutils import Matrix

from ..igb_format.igb_reader import IGBReader
from ..igb_format.igb_objects import IGBObject
from ..igb_format.igb_writer import from_reader
# proven byte-surgical helpers (same ones anim scaling ships on)
from ..actor.anim_scale import _replace_memblock

_log = logging.getLogger(__name__)

NS_PER_SECOND = 1_000_000_000


def _get_field(obj, slot_wanted, short_name=None):
    for slot, val, fi in obj._raw_fields:
        if slot == slot_wanted and (short_name is None
                                    or fi.short_name == short_name):
            return val
    return None


def _patch_entry_fields(writer, reader, obj, patches):
    """Patch Int/Long field values in an object's raw serialized bytes.

    patches: {slot: (new_value, 'Int'|'Long')}. Fields serialize
    sequentially, 4-byte aligned, in _raw_fields order (the same walk
    anim_scale.py uses for its in-place Vec3f patch).
    """
    odef = writer.objects[obj.index]
    if odef is None or getattr(odef, "raw_bytes", None) is None:
        raise ValueError(f"object #{obj.index} has no raw bytes")
    raw = bytearray(odef.raw_bytes)
    endian = reader.header.endian
    pos = 0
    remaining = dict(patches)
    for slot, _val, fi in obj._raw_fields:
        size = fi.size
        if slot in remaining:
            new_val, kind = remaining.pop(slot)
            fmt = "q" if kind == "Long" else "i"
            struct.pack_into(endian + fmt, raw, pos, int(new_val))
        pos += (size + 3) & ~3
        if not remaining:
            break
    if remaining:
        raise ValueError(f"slots not found for patch: {list(remaining)}")
    odef.raw_bytes = bytes(raw)


def sample_empty_track(empty, fps=30.0):
    """Sample an animated empty's action into (times_s, locs, quats, scales).

    Samples at the path's native rate (default 30 keys/second — the rate
    every native motionpath uses) across the action frame range, in the
    empty's LOCAL frame relative to the stored base matrix.
    """
    ad = empty.animation_data
    if ad is None or ad.action is None:
        raise ValueError("empty has no animation")
    action = ad.action
    f0, f1 = action.frame_range
    scene = bpy.context.scene
    scene_fps = scene.render.fps / max(scene.render.fps_base, 1e-6)
    dur_s = max((f1 - f0) / scene_fps, 1.0 / fps)
    n = max(int(round(dur_s * fps)) + 1, 2)

    base = Matrix.Identity(4)
    if "igb_motionpath_base" in empty:
        v = list(empty["igb_motionpath_base"])
        if len(v) == 16:
            base = Matrix((v[0:4], v[4:8], v[8:12], v[12:16]))
    base_inv = base.inverted()
    base_q_inv = base.to_quaternion().inverted()

    depsgraph = bpy.context.evaluated_depsgraph_get()
    cur = scene.frame_current
    times, locs, quats, scales = [], [], [], []
    try:
        for i in range(n):
            t = i / fps
            scene.frame_set(int(round(f0 + t * scene_fps)))
            ev = empty.evaluated_get(depsgraph)
            mw = ev.matrix_world.copy()
            local = base_inv @ mw
            times.append(t)
            locs.append(tuple(local.translation))
            q = base_q_inv @ mw.to_quaternion()
            quats.append((q.w, q.x, q.y, q.z))
            s = local.to_scale()
            scales.append((s.x, s.y, s.z))
    finally:
        scene.frame_set(cur)
    return times, locs, quats, scales


def export_motionpath(template_path, output_path, times_s, locs, quats_wxyz,
                      scales=None):
    """Write a motionpath IGB by patching the template's keyframe lists."""
    n = len(times_s)
    if n < 2:
        raise ValueError("need at least 2 keys")
    if scales is None:
        scales = [(1.0, 1.0, 1.0)] * n

    reader = IGBReader(template_path)
    reader.read()
    writer = from_reader(reader)
    endian = reader.header.endian
    s = reader.slot_offset

    seqs = [o for o in reader.objects
            if isinstance(o, IGBObject)
            and o.type_name.startswith(b"igTransformSequence")]
    if not seqs:
        raise ValueError("template has no igTransformSequence")
    seq = seqs[0]

    # list refs: slot2 xlate, slot3 quat, slot4 scale, slot11 times
    def list_and_block(slot):
        lref = _get_field(seq, slot + s, b"ObjectRef")
        if lref is None or lref < 0:
            return None, None
        lobj = reader.resolve_ref(lref)
        mref = None
        for s2, v2, f2 in lobj._raw_fields:
            if f2.short_name == b"MemoryRef" and v2 != -1:
                mref = v2
                break
        return lobj, mref

    xl_list, xl_mem = list_and_block(2)
    q_list, q_mem = list_and_block(3)
    sc_list, sc_mem = list_and_block(4)
    t_list, t_mem = list_and_block(11)

    # engine quats: conjugate back, XYZW order
    q_data = b"".join(
        struct.pack(endian + "4f", -q[1], -q[2], -q[3], q[0])
        for q in quats_wxyz)
    xl_data = b"".join(struct.pack(endian + "3f", *v) for v in locs)
    sc_data = b"".join(struct.pack(endian + "3f", *v) for v in scales)
    t_data = b"".join(struct.pack(endian + "q", int(round(t * NS_PER_SECOND)))
                      for t in times_s)

    for lobj, mem, data in ((xl_list, xl_mem, xl_data),
                            (q_list, q_mem, q_data),
                            (sc_list, sc_mem, sc_data),
                            (t_list, t_mem, t_data)):
        if lobj is None or mem is None:
            continue
        _replace_memblock(writer, reader, mem, data)
        # ig*List: slot 2 = count, slot 3 = capacity
        _patch_entry_fields(writer, reader, lobj,
                            {2 + s: (n, "Int"), 3 + s: (n, "Int")})

    # sequence duration (slot 18 Long, ns)
    dur_ns = int(round(times_s[-1] * NS_PER_SECOND))
    try:
        _patch_entry_fields(writer, reader, seq, {18 + s: (dur_ns, "Long")})
    except ValueError:
        pass  # some variants lack the field — harmless

    # igSceneInfo duration (slot 9 Long) when present
    for o in reader.objects:
        if isinstance(o, IGBObject) and o.is_type(b"igSceneInfo"):
            if _get_field(o, 9 + s, b"Long") is not None:
                try:
                    _patch_entry_fields(writer, reader, o,
                                        {9 + s: (dur_ns, "Long")})
                except ValueError:
                    pass
            break

    writer.write(output_path)
    return n
