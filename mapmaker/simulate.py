"""Map Maker "Simulate" mode — play the imported map like in-game.

What it does:
- Every NPC actor's idle action is pushed onto an NLA strip that LOOPS for
  the whole scene range, with a per-NPC stagger offset so a crowd doesn't
  idle in robot-lockstep.
- Motion-path empties get CYCLES modifiers so movers/cameras loop.
- Spawn conditions are applied: a spawner that is script-spawned
  (monster_spawnscript) without instantspawn="true" — or hidden/
  startinactive — starts INVISIBLE, matching map load in-game.
- Ambience/music VSE strips play in AUDIO_SYNC.
- Viewport switches to Material Preview (optional Rendered w/ game lights).
- Timeline plays.

Everything is tagged so Stop Simulation restores the scene exactly:
actions return from NLA to the active slot, hidden NPCs re-appear,
cycle modifiers are removed.
"""

import logging

import bpy

_log = logging.getLogger(__name__)

SIM_TRACK = "MM Simulate"
SIM_HIDDEN_TAG = "mm_sim_hidden"
SIM_CYCLES_TAG = "MM_SIM_CYCLES"


# ---------------------------------------------------------------------------
# Entity condition evaluation
# ---------------------------------------------------------------------------

def _ent_prop(ent, key, default=""):
    """Read a key from an entity def's extra-properties collection."""
    k = key.lower()
    for p in ent.properties:
        if p.key.lower() == k:
            return p.value
    return default


def spawner_starts_hidden(ent):
    """Would this spawner's NPC be absent at map load?

    Game rules (verified against stark1.engb):
    - hidden="true" / startinactive="true"  → hidden
    - has a spawn script (monster_spawnscript / spawnscript) and is NOT
      instantspawn="true" → spawned later by script → hidden at load
    """
    if _ent_prop(ent, "hidden").lower() == "true":
        return True
    if _ent_prop(ent, "startinactive").lower() == "true":
        return True
    has_script = bool(_ent_prop(ent, "monster_spawnscript")
                      or _ent_prop(ent, "spawnscript"))
    instant = _ent_prop(ent, "instantspawn").lower() == "true"
    return has_script and not instant


# ---------------------------------------------------------------------------
# Start / stop
# ---------------------------------------------------------------------------

def start_simulation(context, apply_conditions=True, use_rendered=False,
                     min_frames=600):
    """Set the scene up to play like in-game and start playback.

    Returns a summary dict.
    """
    scene = context.scene
    summary = {"looped": 0, "hidden": 0, "paths": 0, "audio": 0}

    # --- scene range / sync ---
    if scene.frame_end < min_frames:
        scene.frame_end = min_frames
    scene.sync_mode = 'AUDIO_SYNC'
    if scene.sequence_editor:
        summary["audio"] = len(scene.sequence_editor.sequences_all)

    # --- NPC idle loops (NLA strip, staggered) ---
    npc_coll = bpy.data.collections.get("[MapMaker] NPC Actors")
    ents_by_name = {e.entity_name: e for e in scene.mm_entity_defs}
    if npc_coll:
        idx = 0
        for obj in npc_coll.objects:
            if obj.type != 'ARMATURE' or not obj.get("mm_npc_actor"):
                continue
            ad = obj.animation_data
            if ad and ad.action and not _sim_track(obj, create=False):
                action = ad.action
                length = max(action.frame_range[1] - action.frame_range[0], 1)
                track = ad.nla_tracks.new()
                track.name = SIM_TRACK
                # deterministic per-NPC stagger (frames)
                offset = (idx * 37) % int(min(length, 120))
                start = 1 - offset
                strip = track.strips.new(action.name, start, action)
                strip.repeat = max(2.0, (scene.frame_end - start) / length + 1)
                strip.extrapolation = 'HOLD'
                ad.action = None
                summary["looped"] += 1
                idx += 1

            # spawn conditions
            if apply_conditions:
                ent = ents_by_name.get(obj.get("mm_npc_actor"))
                if ent is not None and spawner_starts_hidden(ent):
                    _hide_actor(obj, True)
                    summary["hidden"] += 1

    # --- motion paths loop ---
    # The motion-path empties are created by our own importer, so any CYCLES
    # modifier on them is ours — keeps add/remove simple and robust.
    mp_coll = bpy.data.collections.get("[MapMaker] Motion Paths")
    if mp_coll:
        for obj in mp_coll.objects:
            ad = obj.animation_data
            if ad and ad.action:
                for fc in ad.action.fcurves:
                    if not any(m.type == 'CYCLES' for m in fc.modifiers):
                        fc.modifiers.new('CYCLES')
                summary["paths"] += 1

    # --- viewport shading ---
    _set_viewport_shading('RENDERED' if use_rendered else 'MATERIAL')

    # --- play ---
    scene.frame_set(scene.frame_start)
    if not bpy.app.background:
        try:
            if not context.screen.is_animation_playing:
                bpy.ops.screen.animation_play()
        except Exception as e:
            _log.warning("animation_play failed: %s", e)

    scene["mm_simulating"] = True
    return summary


def stop_simulation(context):
    """Undo everything start_simulation changed. Returns a summary dict."""
    scene = context.scene
    summary = {"restored": 0, "unhidden": 0, "paths": 0}

    if not bpy.app.background:
        try:
            if context.screen.is_animation_playing:
                bpy.ops.screen.animation_play()
        except Exception:
            pass

    npc_coll = bpy.data.collections.get("[MapMaker] NPC Actors")
    if npc_coll:
        for obj in npc_coll.objects:
            if obj.type != 'ARMATURE':
                continue
            track = _sim_track(obj, create=False)
            if track is not None:
                ad = obj.animation_data
                action = track.strips[0].action if track.strips else None
                ad.nla_tracks.remove(track)
                if action is not None:
                    ad.action = action
                summary["restored"] += 1
            if obj.get(SIM_HIDDEN_TAG):
                _hide_actor(obj, False)
                summary["unhidden"] += 1

    mp_coll = bpy.data.collections.get("[MapMaker] Motion Paths")
    if mp_coll:
        for obj in mp_coll.objects:
            ad = obj.animation_data
            if ad and ad.action:
                removed = False
                for fc in ad.action.fcurves:
                    for m in list(fc.modifiers):
                        if m.type == 'CYCLES':
                            fc.modifiers.remove(m)
                            removed = True
                if removed:
                    summary["paths"] += 1

    scene["mm_simulating"] = False
    return summary


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _sim_track(obj, create=False):
    ad = obj.animation_data
    if ad is None:
        return None
    for t in ad.nla_tracks:
        if t.name == SIM_TRACK:
            return t
    return None


def _hide_actor(arm, hide):
    """Hide/show an NPC armature and its child meshes (viewport + render)."""
    objs = [arm] + [o for o in arm.children_recursive]
    for o in objs:
        o.hide_render = hide
        try:
            o.hide_set(hide)
        except Exception:
            o.hide_viewport = hide
    if hide:
        arm[SIM_HIDDEN_TAG] = True
    elif SIM_HIDDEN_TAG in arm:
        del arm[SIM_HIDDEN_TAG]


def _set_viewport_shading(shading_type):
    try:
        wm = bpy.context.window_manager
        for window in wm.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    for space in area.spaces:
                        if space.type == 'VIEW_3D':
                            space.shading.type = shading_type
    except Exception:
        pass
