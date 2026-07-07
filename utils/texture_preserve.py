"""Preserve original IGB texture blocks across import -> export.

When a native skin is imported and re-exported WITHOUT the user editing a
texture, we want the exported DXT blocks to be byte-identical to the original
so there is NO lossy decode -> re-encode round trip (the second DXT pass is
what subtly shifts a skin's colour in-game).

How it works:
  - On import, the scene-graph parser gathers each texture's ORIGINAL encoded
    mip levels (level 0 = base igImage, levels 1..N = the mipmap list) and the
    original pixel format, and hands them here via remember_original().
  - remember_original() fingerprints the DECODED Blender pixels (8-bit, so it is
    stable against float round-trip drift) and stashes the encoded levels in a
    content-keyed disk cache, recording the fingerprint on the image datablock.
  - On export, recall_original() re-fingerprints the current pixels; if they
    still match, the texture is unedited and we return the ORIGINAL encoded
    levels verbatim (byte-identical). Any edit changes the fingerprint and we
    fall back to a normal re-compress.

The cache lives in the OS temp dir (like the decode cache). If it is cleared,
recall simply misses and the exporter re-compresses — no correctness risk.
"""
import os
import struct
import tempfile

_MAGIC = b"IOT1"
_FP_PROP = "igb_orig_fp"


def _cache_dir():
    d = os.path.join(tempfile.gettempdir(), "igb_orig_tex_cache")
    os.makedirs(d, exist_ok=True)
    return d


def _fingerprint(bl_image):
    """Stable content fingerprint of an image's 8-bit pixels + dims.

    Quantises to uint8 first so a lossless float round-trip (or a sub-LSB tweak
    below 8-bit precision) does not read as an edit. Returns a hex string, or
    None if the image has no pixels.
    """
    try:
        import hashlib
        w, h = bl_image.size[0], bl_image.size[1]
        if w == 0 or h == 0:
            return None
        n = w * h * 4
        hs = hashlib.blake2b(digest_size=16)
        try:
            import numpy as np
            arr = np.empty(n, dtype=np.float32)
            bl_image.pixels.foreach_get(arr)
            u8 = np.clip(arr * 255.0 + 0.5, 0.0, 255.0).astype(np.uint8)
            hs.update(u8.tobytes())
        except Exception:
            px = bl_image.pixels[:]
            hs.update(bytes(min(255, max(0, int(v * 255.0 + 0.5)))
                            for v in px))
        hs.update(f"|{w}x{h}".encode())
        return hs.hexdigest()
    except Exception:
        return None


def _serialize(pfmt, levels):
    out = bytearray()
    out += _MAGIC
    out += struct.pack("<ii", int(pfmt), len(levels))
    for data, w, h in levels:
        data = bytes(data)
        out += struct.pack("<iii", int(w), int(h), len(data))
        out += data
    return bytes(out)


def _deserialize(raw):
    if len(raw) < 12 or raw[:4] != _MAGIC:
        return None
    pfmt, nlev = struct.unpack_from("<ii", raw, 4)
    off = 12
    levels = []
    for _ in range(nlev):
        if off + 12 > len(raw):
            return None
        w, h, dl = struct.unpack_from("<iii", raw, off)
        off += 12
        data = raw[off:off + dl]
        off += dl
        if len(data) != dl:
            return None
        levels.append((data, w, h))
    return pfmt, levels


def remember_original(bl_image, orig_levels, orig_pfmt):
    """Stash the original encoded mip levels for an imported texture.

    orig_levels: list of (encoded_bytes, width, height), level 0 first.
    orig_pfmt:   the igImage pixel format (14 = DXT1, 16 = DXT5, ...).
    No-op (silently) if anything is missing.
    """
    if bl_image is None or not orig_levels or orig_pfmt is None:
        return
    try:
        fp = _fingerprint(bl_image)
        if fp is None:
            return
        path = os.path.join(_cache_dir(), fp + ".bin")
        if not os.path.isfile(path):
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(_serialize(orig_pfmt, orig_levels))
            os.replace(tmp, path)
        bl_image[_FP_PROP] = fp
    except Exception:
        pass


def recall_original(bl_image):
    """Return (levels, pfmt) if the image is unedited native, else None.

    levels: list of (encoded_bytes, width, height). Reusing these emits the
    exact original DXT blocks — no re-compression.
    """
    if bl_image is None:
        return None
    try:
        fp = bl_image.get(_FP_PROP)
        if not fp:
            return None
        if _fingerprint(bl_image) != fp:
            return None  # user edited the texture -> let the exporter re-encode
        path = os.path.join(_cache_dir(), fp + ".bin")
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as f:
            raw = f.read()
        parsed = _deserialize(raw)
        if parsed is None:
            return None
        pfmt, levels = parsed
        if not levels:
            return None
        return levels, pfmt
    except Exception:
        return None
