"""PS2 VAG (.vag) audio decoder + briefing-voice loader.

X-Men Legends 1 (and other PS2 titles) store voice/sfx as Sony VAG: an 0x30
header ("VAGp" magic, big-endian fields) followed by 16-byte PS-ADPCM blocks
(1 shift/filter byte, 1 flag byte, 14 data bytes = 28 samples). Mono, the
sample rate from the header (22050 Hz for XML1 briefings). Decoded to a temp
WAV and dropped into Blender's Video Sequence Editor so an XML1 briefing plays
in sync — the XML1 counterpart of zsnd_audio (which handles XML2's ZSNDPC).

NOTE: the loose XML1 console *decompiles* ship only a `badaudio` placeholder —
the per-line voice streams live in packed sound archives that were not unpacked.
So on a stock decompile this decoder validates but finds no briefing voice; it
plays the moment real `voice/...vag` files are dropped alongside the assets.
"""

import os
import struct
import array
import wave
import logging

_log = logging.getLogger(__name__)

# Sony PS-ADPCM filter coefficients (fixed-point, >> 6). Index = predictor nr.
_VAG_COEFFS = [(0, 0), (60, 0), (115, -52), (98, -55), (122, -60)]


def decode_vag(data):
    """Decode Sony VAG (PS-ADPCM) bytes -> (sample_rate, int16 array 'h').

    Accepts the whole file's bytes. Returns None if not a VAG.
    """
    if len(data) < 0x30 or data[:4] != b'VAGp':
        return None
    sample_rate = struct.unpack_from('>I', data, 0x10)[0] or 22050
    data_size = struct.unpack_from('>I', data, 0x0C)[0]
    start = 0x30
    avail = len(data) - start
    if data_size <= 0 or data_size > avail:
        data_size = avail
    n_blocks = data_size // 16

    out = array.array('h')
    hist1 = 0
    hist2 = 0
    for b in range(n_blocks):
        base = start + b * 16
        b0 = data[base]
        shift = b0 & 0x0F
        predict = b0 >> 4
        flag = data[base + 1]
        if flag == 7:                      # end-of-stream marker: stop
            break
        if predict > 4:                    # guard invalid predictor
            predict = 0
        if shift > 12:                     # PS1 HW quirk: oversized shift -> 9
            shift = 9
        f0, f1 = _VAG_COEFFS[predict]
        for i in range(2, 16):
            byte = data[base + i]
            for nibble in (byte & 0x0F, byte >> 4):   # low nibble first
                s = (nibble << 12) & 0xFFFF
                if s & 0x8000:                        # sign-extend 16-bit
                    s -= 0x10000
                s >>= shift
                sample = s + ((hist1 * f0 + hist2 * f1) >> 6)
                if sample > 32767:
                    sample = 32767
                elif sample < -32768:
                    sample = -32768
                out.append(sample)
                hist2 = hist1
                hist1 = sample
    return sample_rate, out


def vag_to_wav(vag_path, out_wav):
    """Decode a .vag file to a mono 16-bit WAV. Returns (out_wav, seconds)."""
    try:
        with open(vag_path, 'rb') as f:
            data = f.read()
    except OSError:
        return None
    res = decode_vag(data)
    if not res:
        return None
    sr, pcm = res
    if not len(pcm):
        return None
    w = wave.open(out_wav, 'wb')
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    w.writeframes(pcm.tobytes())
    w.close()
    return out_wav, len(pcm) / float(sr)


def resolve_voice_vag(game_dir, voice_ref):
    """Resolve a script voice reference (e.g. 'voice/briefings/1_1_6_5') to an
    on-disk .vag under the game dir, trying the common sound roots. Returns a
    path or None."""
    if not game_dir or not voice_ref:
        return None
    ref = voice_ref.replace("\\", "/").strip().lstrip("/")
    stems = [ref]
    # some scripts omit the leading 'voice/' or the 'sounds/' root
    if not ref.startswith("voice/"):
        stems.append("voice/" + ref)
    roots = [game_dir, os.path.join(game_dir, "sounds"),
             os.path.join(game_dir, "sounds", "eng")]
    for root in roots:
        for stem in stems:
            for ext in (".vag", ".VAG"):
                p = os.path.join(root, *stem.split("/")) + ext
                if os.path.exists(p):
                    return p
    return None


def add_briefing_voice_strip(context, data):
    """Decode the XML1 briefing voice (.vag) and add it to the VSE at its
    scripted start time. Returns True if a strip was added.

    Mirrors zsnd_audio.add_briefing_voice_strip (the XML2 ZSNDPC path).
    """
    import bpy
    voice_ref = getattr(data, "voice_ref", "") or ""
    vag = resolve_voice_vag(data.game_dir, voice_ref)
    if not vag:
        _log.info("No .vag voice for '%s' (XML1 decompiles ship no voice streams)",
                  voice_ref or data.name)
        return False

    cache = bpy.app.tempdir or os.path.dirname(vag)
    out_wav = os.path.join(cache, f"{data.name}_voice.wav")
    res = vag_to_wav(vag, out_wav)
    if not res:
        return False
    wav_path, dur = res

    scene = context.scene
    if scene.sequence_editor is None:
        scene.sequence_editor_create()
    se = scene.sequence_editor
    fps = scene.render.fps / max(scene.render.fps_base, 1e-6)
    vt = getattr(data, "voice_time", None) or 0.0
    start = 1 + int(round(vt * fps))
    try:
        se.sequences.new_sound(name=f"{data.name}_voice", filepath=wav_path,
                               channel=1, frame_start=start)
    except Exception as exc:
        _log.warning("VSE strip add failed: %s", exc)
        return False
    scene.frame_end = max(scene.frame_end, start + int(dur * fps) + 1)
    scene.sync_mode = 'AUDIO_SYNC'
    _log.info("Added XML1 briefing voice strip (%.1fs)", dur)
    return True
