"""ZSNDPC (.zss / .zsm) audio decoder + briefing-voice loader.

X-Men Legends 2 PC voice/sfx banks: magic "ZSNDPC  " + a sub-sound directory,
audio = contiguous mono IMA/DVI-ADPCM (4-bit, low-nibble-first, no per-block
preamble), 22050 Hz / 16-bit. Format reverse-engineered + verified (session
7df40c71). Decodes to a temp WAV added to Blender's Video Sequence Editor so
the briefing plays in sync.
"""

import os
import struct
import array
import wave
import logging

_log = logging.getLogger(__name__)

INDEX_TABLE = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8]
STEP_TABLE = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230,
    253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
    1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327,
    3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
    11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794,
    32767]


def decode_ima_mono(raw):
    """Contiguous IMA ADPCM (low-nibble first, predictor/index start at 0)."""
    out = array.array('h')
    pred = 0
    index = 0
    for byte in raw:
        for nib in (byte & 0xf, (byte >> 4) & 0xf):
            step = STEP_TABLE[index]
            diff = step >> 3
            if nib & 1:
                diff += step >> 2
            if nib & 2:
                diff += step >> 1
            if nib & 4:
                diff += step
            if nib & 8:
                diff = -diff
            pred += diff
            if pred > 32767:
                pred = 32767
            elif pred < -32768:
                pred = -32768
            index += INDEX_TABLE[nib]
            if index < 0:
                index = 0
            elif index > 88:
                index = 88
            out.append(pred)
    return out


def parse_zsnd(data):
    """Return (sample_rate, [(name, data_offset, data_size), ...]) or None."""
    if data[:8] != b'ZSNDPC  ':
        return None
    hdr_size = struct.unpack_from('<I', data, 0xc)[0]
    sr = 22050
    for o in range(0x10, min(hdr_size, len(data)), 4):
        v = struct.unpack_from('<I', data, o)[0]
        if v in (11025, 22050, 32000, 44100, 48000, 16000):
            sr = v
            break
    entries = []
    i = 0
    while True:
        j = data.find(b'.wav', i, hdr_size)
        if j < 0:
            break
        s = j
        while s > 0 and 32 <= data[s - 1] < 127:
            s -= 1
        name = data[s:j + 4].decode('ascii', 'replace')
        if s >= 8:
            size = struct.unpack_from('<I', data, s - 8)[0]
            entries.append((name, size))
        i = j + 4
    sounds = []
    off = hdr_size
    for name, size in entries:
        sounds.append((name, off, size))
        off += size
    return sr, sounds


def decode_subsound_to_wav(zsnd_path, out_wav, subsound_name=None):
    """Decode one sub-sound (by name substring, or the first) to a WAV file.

    Returns (out_wav, duration_seconds) or None.
    """
    try:
        with open(zsnd_path, 'rb') as f:
            data = f.read()
    except OSError:
        return None
    parsed = parse_zsnd(data)
    if not parsed:
        return None
    sr, sounds = parsed
    if not sounds:
        return None
    pick = None
    if subsound_name:
        key = subsound_name.lower()
        pick = next((s for s in sounds if key in s[0].lower()), None)
    if pick is None:
        pick = sounds[0]
    name, off, size = pick
    pcm = decode_ima_mono(data[off:off + size])
    if not len(pcm):
        return None
    w = wave.open(out_wav, 'wb')
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    w.writeframes(pcm.tobytes())
    w.close()
    return out_wav, len(pcm) / float(sr)


def _voice_bank_path(game_dir, soundfile):
    """sounds/eng/<c1>/<c2>/<soundfile>_v.zss (voice bank), trying fallbacks."""
    if not soundfile:
        return None
    base = os.path.join(game_dir, "sounds", "eng",
                        soundfile[0].lower(),
                        soundfile[1].lower() if len(soundfile) > 1 else "")
    for suffix, ext in (("_v", ".zss"), ("_v", ".zsm"),
                        ("_d", ".zsm"), ("", ".zss")):
        p = os.path.join(base, f"{soundfile}{suffix}{ext}")
        if os.path.exists(p):
            return p
    return None


def _subsound_for_briefing(name):
    """briefing1_10 -> '1_Briefing_10' (the sub-sound name convention)."""
    n = name.lower()
    if n.startswith("briefing"):
        rest = n[len("briefing"):]        # e.g. '1_10'
        parts = rest.split("_")
        if len(parts) == 2:
            return f"{parts[0]}_Briefing_{parts[1]}"
    return name


def add_briefing_voice_strip(context, data):
    """Decode the briefing voice and add it to the VSE at frame 1.

    Returns True if a strip was added.
    """
    import bpy
    bank = _voice_bank_path(data.game_dir, data.soundfile)
    if not bank:
        _log.info("No voice bank for soundfile '%s'", data.soundfile)
        return False

    cache = bpy.app.tempdir or os.path.dirname(bank)
    out_wav = os.path.join(cache, f"{data.name}_voice.wav")
    res = decode_subsound_to_wav(bank, out_wav,
                                 subsound_name=_subsound_for_briefing(data.name))
    if not res:
        return False
    wav_path, dur = res

    scene = context.scene
    if scene.sequence_editor is None:
        scene.sequence_editor_create()
    se = scene.sequence_editor
    # place the voice at its SCRIPTED start time (the game plays it after the
    # staggered playanim calls, e.g. 1.9s in), not at frame 1
    fps0 = scene.render.fps / max(scene.render.fps_base, 1e-6)
    vt = getattr(data, "voice_time", None) or 0.0
    start = 1 + int(round(vt * fps0))
    try:
        strip = se.sequences.new_sound(
            name=f"{data.name}_voice", filepath=wav_path,
            channel=1, frame_start=start)
    except Exception as exc:
        _log.warning("VSE strip add failed: %s", exc)
        return False
    # extend the scene playback to cover the voice
    fps = scene.render.fps / max(scene.render.fps_base, 1e-6)
    scene.frame_end = max(scene.frame_end, start + int(dur * fps) + 1)
    # AV-sync: this scene is heavy (map + 6 animated characters), so viewport
    # playback can't always hold the frame rate — without frame-dropping the
    # animation falls behind the audio and drifts out of sync near the end.
    scene.sync_mode = 'AUDIO_SYNC'
    _log.info("Added briefing voice strip (%.1fs)", dur)
    return True
