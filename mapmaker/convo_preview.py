"""In-game conversation preview + audio export for the Conversation Editor.

Simulates how a conversation flows in-game: dialogue box with HUD portrait,
speaker name, typewriter text, live-decoded voice playback, numbered player
choices, %BLANK% auto-advance, tag jumps, faction variants (X-Men /
Brotherhood), and end-of-conversation handling.

Also provides audio export:
- every voice line as .wav files
- a single "radio play" mixdown WAV of a play-through path

The flow-engine functions are pure (no Qt) so they can be tested headless.
"""

import os
import shutil
import wave
import logging

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Flow engine (pure logic — testable without Qt)
# ---------------------------------------------------------------------------

def line_speaker_text(node, brotherhood=False):
    """Return (speaker, text) for a LINE node honoring the faction variant."""
    from .conversation import parse_speaker_text
    raw = node.text
    if brotherhood and node.text_b:
        raw = node.text_b
    return parse_speaker_text(raw)


def line_sound(node, brotherhood=False):
    if brotherhood and node.sound_to_play_b:
        return node.sound_to_play_b
    return node.sound_to_play


def first_line(conv):
    """The first LINE the game would show (skips condition/participant nodes)."""
    from .conversation import get_children

    def _scan(parent_id):
        for child in get_children(conv, parent_id):
            if child.node_type == 'LINE':
                return child
            if child.node_type in ('START_CONDITION', 'PARTICIPANT'):
                found = _scan(child.node_id)
                if found is not None:
                    return found
        return None

    return _scan(-1)


def get_choices(conv, line_node, brotherhood=False):
    """RESPONSE children of a line, filtered by faction."""
    from .conversation import get_children
    out = []
    for child in get_children(conv, line_node.node_id):
        if child.node_type != 'RESPONSE':
            continue
        if brotherhood and child.only_if_xman:
            continue
        if not brotherhood and child.only_if_brotherhood:
            continue
        out.append(child)
    return out


def line_children(conv, node):
    from .conversation import get_children
    return [c for c in get_children(conv, node.node_id)
            if c.node_type == 'LINE']


def next_sibling_line(conv, node):
    """Next LINE sibling after `node` under the same parent (monologue flow)."""
    from .conversation import get_children
    sibs = [c for c in get_children(conv, node.parent_id)
            if c.node_type == 'LINE']
    for i, s in enumerate(sibs):
        if s.node_id == node.node_id:
            if i + 1 < len(sibs):
                return sibs[i + 1]
            return None
    return None


def find_tag_target(conv, tag):
    """Node whose tag_index matches a response's tag_jump."""
    if not tag:
        return None
    t = str(tag).strip().lower()
    for node in conv.nodes:
        if str(node.tag_index).strip().lower() == t and node.node_type == 'LINE':
            return node
    for node in conv.nodes:
        if str(node.tag_index).strip().lower() == t:
            return node
    return None


def advance_from_line(conv, node, brotherhood=False):
    """What happens after a LINE finishes.

    Returns ('choices', [resp,...]) | ('line', next_line) | ('end', None).
    """
    choices = get_choices(conv, node, brotherhood)
    if choices:
        return 'choices', choices
    kids = line_children(conv, node)
    if kids:
        return 'line', kids[0]
    sib = next_sibling_line(conv, node)
    if sib is not None:
        return 'line', sib
    return 'end', None


def resolve_response(conv, resp):
    """Where a chosen RESPONSE leads.

    Returns ('end', None) | ('line', node) — tag jumps resolved, follow-up
    lines preferred, otherwise the conversation ends.
    """
    if resp.conversation_end:
        return 'end', None
    if resp.tag_jump:
        target = find_tag_target(conv, resp.tag_jump)
        if target is not None:
            return 'line', target
    kids = line_children(conv, resp)
    if kids:
        return 'line', kids[0]
    # No follow-up: fall back to the line after the parent line, else end
    from .conversation import find_node_by_id
    parent = find_node_by_id(conv, resp.parent_id)
    if parent is not None:
        sib = next_sibling_line(conv, parent)
        if sib is not None:
            return 'line', sib
    return 'end', None


def is_blank_response(resp):
    return resp.response_text.strip().upper() == '%BLANK%'


def golden_path(conv, brotherhood=False, max_steps=500):
    """The play-through taking the FIRST choice at every branch.

    Returns the ordered list of LINE nodes visited (for the audio mixdown).
    Tag-jump loops are cut by a visited set.
    """
    path = []
    seen = set()
    node = first_line(conv)
    steps = 0
    while node is not None and steps < max_steps:
        steps += 1
        if node.node_id in seen:
            break
        seen.add(node.node_id)
        path.append(node)
        kind, nxt = advance_from_line(conv, node, brotherhood)
        if kind == 'choices':
            resp = nxt[0]
            kind2, node = resolve_response(conv, resp)
            if kind2 == 'end':
                break
        elif kind == 'line':
            node = nxt
        else:
            break
    return path


def all_lines_in_order(conv):
    """Every LINE node in display (tree) order — for exporting all voice."""
    from .conversation import get_flat_display_order
    return [n for n, _d in get_flat_display_order(conv)
            if n.node_type == 'LINE']


# ---------------------------------------------------------------------------
# Audio export
# ---------------------------------------------------------------------------

def _resolve_wav(game_dir, soundfile, sound_ref):
    from .voice_audio import resolve_voice_wav
    if not sound_ref:
        return None
    res = resolve_voice_wav(game_dir, sound_ref, soundfile=soundfile)
    return res[0] if res else None


def export_all_voice_wavs(conv, game_dir, soundfile, out_dir):
    """Decode every voice line (both factions) to <out_dir>/<line-id>.wav.

    Returns (exported, missing) counts.
    """
    os.makedirs(out_dir, exist_ok=True)
    exported = 0
    missing = 0
    seen = set()
    for node in all_lines_in_order(conv):
        for ref in (node.sound_to_play, node.sound_to_play_b):
            if not ref or ref in seen:
                continue
            seen.add(ref)
            wav = _resolve_wav(game_dir, soundfile, ref)
            if wav is None:
                missing += 1
                continue
            name = os.path.basename(ref.replace("\\", "/"))
            if not name.lower().endswith(".wav"):
                name += ".wav"
            shutil.copyfile(wav, os.path.join(out_dir, name))
            exported += 1
    return exported, missing


def export_conversation_mixdown(conv, game_dir, soundfile, out_path,
                                brotherhood=False, gap_seconds=0.6):
    """Concatenate the golden-path voice lines into one WAV "radio play".

    All ZSNDPC voice is 22050 Hz mono 16-bit, so concatenation is a
    straight frame append with silence gaps. Returns
    (line_count, duration_seconds) or None when no line has audio.
    """
    lines = golden_path(conv, brotherhood=brotherhood)
    wavs = []
    for node in lines:
        ref = line_sound(node, brotherhood)
        wav = _resolve_wav(game_dir, soundfile, ref)
        if wav:
            wavs.append(wav)
    if not wavs:
        return None

    out = wave.open(out_path, 'wb')
    sr = 22050
    first = wave.open(wavs[0], 'rb')
    sr = first.getframerate()
    out.setnchannels(first.getnchannels())
    out.setsampwidth(first.getsampwidth())
    out.setframerate(sr)
    first.close()

    gap = b"\x00" * int(gap_seconds * sr) * 2  # 16-bit mono silence
    total_frames = 0
    for i, w in enumerate(wavs):
        src = wave.open(w, 'rb')
        frames = src.readframes(src.getnframes())
        src.close()
        if i > 0:
            out.writeframes(gap)
            total_frames += len(gap) // 2
        out.writeframes(frames)
        total_frames += len(frames) // 2
    out.close()
    return len(wavs), total_frames / float(sr)
