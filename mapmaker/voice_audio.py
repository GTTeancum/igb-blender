"""Live voice/ambience decode + playback for conversations and maps.

Conversation lines store a game sound reference like
    soundtoplay="voice/fury/1_stark1_010_013_nf"
The audio lives in the map's ZSNDPC voice bank
    sounds/eng/<c1>/<c2>/<world.soundfile>_v.zss
as a sub-sound named "1_stark1_010_013_nf.wav". Decoding one line takes
~0.07 s, so lines are decoded ON DEMAND (with a temp-dir cache) instead of
at import time. Reuses the proven ZSNDPC decoder in actor/zsnd_audio.py.

Windows playback uses winsound (async, no extra dependencies) — fine for
this project's Windows-only toolchain; other platforms just get the WAV
path back.
"""

import os
import struct
import tempfile
import logging

from ..actor.zsnd_audio import decode_subsound_to_wav, _voice_bank_path

_log = logging.getLogger(__name__)

_CACHE_DIR = None


def _cache_dir():
    global _CACHE_DIR
    if _CACHE_DIR is None:
        d = os.path.join(tempfile.gettempdir(), "igb_voice_cache")
        os.makedirs(d, exist_ok=True)
        _CACHE_DIR = d
    return _CACHE_DIR


def _bank_has_subsound(bank_path, subsound_lower):
    """Header-only check whether a ZSNDPC bank contains a sub-sound name.

    Reads just the directory header (not the multi-MB audio payload), so
    scanning every bank in sounds/eng stays fast.
    """
    try:
        with open(bank_path, "rb") as f:
            head = f.read(16)
            if head[:8] != b"ZSNDPC  ":
                return False
            hdr_size = struct.unpack_from("<I", head, 0xC)[0]
            if hdr_size <= 16 or hdr_size > 32 * 1024 * 1024:
                return False
            f.seek(0)
            hdr = f.read(hdr_size)
        return subsound_lower.encode("ascii", "ignore") in hdr.lower()
    except (OSError, struct.error):
        return False


def find_voice_bank(game_dir, subsound, soundfile=None):
    """Locate the .zss/.zsm bank containing a sub-sound.

    Tries the map's own voice bank first (from the world entity's
    `soundfile`), then falls back to a header-only scan of every bank
    under sounds/eng.
    """
    sub = subsound.lower()

    if soundfile:
        bank = _voice_bank_path(game_dir, soundfile)
        if bank and _bank_has_subsound(bank, sub):
            return bank

    eng = os.path.join(game_dir, "sounds", "eng")
    if not os.path.isdir(eng):
        return None
    for dirpath, _dirnames, filenames in os.walk(eng):
        for fn in filenames:
            if not fn.lower().endswith((".zss", ".zsm")):
                continue
            p = os.path.join(dirpath, fn)
            if _bank_has_subsound(p, sub):
                return p
    return None


def resolve_voice_wav(game_dir, sound_to_play, soundfile=None):
    """Decode a conversation sound reference to a cached WAV.

    Args:
        game_dir: game root (contains sounds/)
        sound_to_play: e.g. "voice/fury/1_stark1_010_013_nf"
        soundfile: the map's world soundfile (bank hint, e.g. "town1")

    Returns (wav_path, duration_seconds) or None.
    """
    if not game_dir or not sound_to_play:
        return None
    base = os.path.basename(sound_to_play.replace("\\", "/")).strip()
    if not base:
        return None
    subsound = base if base.lower().endswith(".wav") else base + ".wav"

    cache_key = subsound.lower().replace(".wav", "")
    out_wav = os.path.join(_cache_dir(), f"{cache_key}.wav")
    if os.path.isfile(out_wav) and os.path.getsize(out_wav) > 44:
        return out_wav, _wav_duration(out_wav)

    bank = find_voice_bank(game_dir, subsound, soundfile=soundfile)
    if bank is None:
        _log.info("No bank found for sub-sound %s", subsound)
        return None
    res = decode_subsound_to_wav(bank, out_wav, subsound_name=subsound)
    if not res:
        return None
    return res


def _wav_duration(path):
    try:
        import wave
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate() or 22050)
    except Exception:
        return 0.0


def play_wav(path):
    """Play a WAV asynchronously (Windows winsound). Returns True on start."""
    try:
        import winsound
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        return True
    except Exception as e:
        _log.warning("Playback failed: %s", e)
        return False


def stop_playback():
    try:
        import winsound
        winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception:
        pass


def add_map_audio_strips(context, game_dir, soundfile, map_name):
    """Decode the map's ambience + music into VSE strips (scene audio).

    Ambience comes from the voice bank ("<soundfile>_a" or "amb" sub-sound),
    music from the sibling <soundfile>_m.zsm. Returns the number of strips
    added.
    """
    import bpy

    if not soundfile:
        return 0
    bank = _voice_bank_path(game_dir, soundfile)
    added = 0

    scene = context.scene
    if scene.sequence_editor is None:
        scene.sequence_editor_create()
    se = scene.sequence_editor

    def _add(wav_path, label, channel):
        nonlocal added
        try:
            se.sequences.new_sound(name=label, filepath=wav_path,
                                   channel=channel, frame_start=1)
            added += 1
        except Exception as e:
            _log.warning("VSE strip failed (%s): %s", label, e)

    # Both live in the map's voice bank: "<soundfile>_a" (or "amb") is the
    # ambience loop and "<soundfile>_c" is the level music track.
    # (The sibling <soundfile>_m.zsm is map SFX — footsteps/alerts — not music.)
    # decode_subsound_to_wav falls back to the FIRST sub-sound when the name
    # is missing, so pre-check the header to avoid adding wrong audio.
    if bank:
        for sub in (f"{soundfile}_a", "amb"):
            if not _bank_has_subsound(bank, f"{sub}.wav".lower()):
                continue
            out = os.path.join(_cache_dir(), f"{map_name}_{sub}.wav")
            res = decode_subsound_to_wav(bank, out, subsound_name=sub)
            if res:
                _add(res[0], f"{map_name}_ambience", 1)
                break

        music_sub = f"{soundfile}_c"
        if _bank_has_subsound(bank, f"{music_sub}.wav".lower()):
            out = os.path.join(_cache_dir(), f"{map_name}_music.wav")
            res = decode_subsound_to_wav(bank, out, subsound_name=music_sub)
            if res:
                _add(res[0], f"{map_name}_music", 2)

    return added
