"""Briefing Loader — assemble a full XML2 briefing scene in Blender, editable.

A briefing is authored across several sibling files (reference: briefing1_10):
  maps/briefing/<b>.igb   scene/environment geometry (+ holo-prop models)
  maps/briefing/<b>.chrb  XMLB: the cast (the "_mc" mocap characters)
  maps/briefing/<b>.xmlb  XMLB: staging — a spawner per character (monster_name),
                          the zonescript (-> the mocap file), voice + subtitle paths
  actors/zone_<b>.igb     the mocap: one shared 65-bone rig + one performance per
                          character (pose + face + ROOT POSITION)
  sounds/eng/.../<sf>_*.zss/.zsm   voice audio (ZSNDPC), keyed by soundfile="<sf>"

The cast are the dedicated 65-bone MOCAP MODELS (npcstat '<char>_MC' -> skin
200xx), NOT the reduced gameplay skins. The zone performance was authored for
that exact skeleton, so it applies DIRECTLY (animation_builder.build_action with
rebase_unanimated_parents) — no retargeting, no distortion.

This module parses that data model and orchestrates the load, composing existing
engines: importer.import_igb (map), mapmaker.game_database (character->mocap
skin), actor_import.import_actor (models), animation_builder (direct apply).
Audio (ZSNDPC decode) plugs in via zsnd_audio.

Everything lands in one 'Briefing: <b>' collection so the user can edit it.
"""

import os
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional

_log = logging.getLogger(__name__)


@dataclass
class BriefingSpawner:
    name: str = ""            # e.g. sp_cyclopsmocap
    character: str = ""       # e.g. cyclops_mc  (the .chrb cast id / npcstat _MC name)
    monster_name: str = ""    # e.g. cyclops     (-> herostat -> GAMEPLAY skin, fallback)
    monster_skin: str = ""    # optional explicit skin code override
    skin_code: str = ""       # resolved skin code (200xx mocap model preferred)
    skin_path: str = ""       # resolved actors/<skin>.igb
    characteranims: str = ""  # e.g. 200_wolverine — the mocap moveset (reference)
    is_mocap_model: bool = False  # True when skin_code resolved to a 200xx mocap model
    pos: Optional[tuple] = None      # world (x,y,z) from the .xmlb <inst>
    orient: Optional[tuple] = None   # euler (x,y,z) radians from <inst>
    zone_anim: str = ""       # e.g. zone3 — from scripts/briefing/<map>.py
    start_time: float = 0.0   # scripted start (seconds; cumulative waittimed)


@dataclass
class BriefingProp:
    name: str = ""
    model: str = ""           # e.g. briefing_holo_models/1_10_deadzone_spire
    classname: str = ""


@dataclass
class BriefingData:
    name: str = ""            # briefing1_10
    map_igb: str = ""         # maps/briefing/briefing1_10.igb
    game_dir: str = ""        # game data root
    act: str = ""
    cast: List[str] = field(default_factory=list)       # from .chrb
    spawners: List[BriefingSpawner] = field(default_factory=list)
    props: List[BriefingProp] = field(default_factory=list)
    zonescript: str = ""      # briefing/briefing1_10
    mocap_igb: str = ""       # actors/zone_briefing1_10.igb (resolved)
    soundfile: str = ""       # vbrief  (-> sounds/eng/.../vbrief_*.zss/.zsm)
    voice_precache: str = ""  # voice/briefing/briefing1_10
    subtitle: str = ""        # subtitles/Briefing_1_10_subtitle
    voice_time: Optional[float] = None  # scripted voice start (seconds)
    voice_ref: str = ""       # script sound() voice path (XML1: voice/briefings/<id>)
    game: str = "xml2"        # detected layout: 'xml1' | 'xml2' (see _briefing_profile)
    # scripted camera timeline: [(t_seconds, (x,y,z)|None, (roll,pitch,yaw)|None,
    # duration_seconds)] — one entry per cameraMove/cameraPan call, in order
    camera_events: List[tuple] = field(default_factory=list)


def _find_game_dir(map_igb_path):
    """Walk up from a map .igb to the game data root (contains actors/ and
    maps/). Works for XML2 maps/briefing/<b>.igb and XML1
    maps/mocap/mocapN/<b>.igb alike."""
    p = os.path.dirname(os.path.abspath(map_igb_path))
    for _ in range(8):
        if (os.path.isdir(os.path.join(p, "actors"))
                and os.path.isdir(os.path.join(p, "maps"))):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return None


def _briefing_profile(map_igb_path):
    """Detect the game's briefing/mocap layout for a picked map .igb.

    The XML1 console cutscene ("mocap") and the XML2/MUA "briefing" are the
    SAME data model with different packaging:

      XML1 (console): maps/mocap/mocapN/<b>.igb, plain-text .xml + .chr,
          scripts/mocap/mocapN/<b>.py, EA_MISSION<n> tags, one shared
          performance file actors/mission_mocapN.igb (anims named 'mission<n>').
      XML2/MUA (PC):  maps/briefing/<b>.igb, compiled .xmlb + .chrb,
          scripts/briefing/<b>.py, EA_ZONE<n> tags, actors/zone_<b>.igb
          (anims named 'zone<n>').

    Detection is by the maps/mocap/mocapN path (which also yields N, needed to
    resolve mission_mocapN.igb) with a plain-text-sibling fallback. Returns a
    dict: game, mocap_n, staging_exts, cast_exts, script_subdir.
    """
    import re
    p = os.path.abspath(map_igb_path).replace("\\", "/").lower()
    base = os.path.splitext(map_igb_path)[0]
    m = re.search(r"/maps/mocap/mocap(\d+)/", p)
    xml1_siblings = (os.path.exists(base + ".xml")
                     and os.path.exists(base + ".chr"))
    if m or xml1_siblings:
        n = m.group(1) if m else None
        if n is None:                              # picked outside a mocapN dir
            parent = os.path.basename(os.path.dirname(map_igb_path)).lower()
            mm = re.match(r"mocap(\d+)$", parent)
            n = mm.group(1) if mm else None
        return {"game": "xml1", "mocap_n": n,
                "staging_exts": (".xml",), "cast_exts": (".chr",),
                "script_subdir": ("mocap", "mocap" + n) if n else ("mocap",)}
    return {"game": "xml2", "mocap_n": None,
            "staging_exts": (".xmlb", ".engb"), "cast_exts": (".chrb",),
            "script_subdir": ("briefing",)}


def _read_xml_any(path):
    """Read a staging/cast file as an ElementTree root, whether it is compiled
    XMLB (XML2/MUA .xmlb/.chrb/.engb) or plain-text XML (XML1 .xml/.chr).

    Both yield an ElementTree.Element supporting .iter()/.attrib, so callers
    are format-agnostic downstream.
    """
    from pathlib import Path
    with open(path, "rb") as f:
        head = f.read(16)
    # first non-whitespace byte '<' => plain-text XML. Strip a leading UTF-8
    # BOM too (bytes.lstrip() alone leaves it, which would misroute BOM'd text
    # to the compiled-XMLB reader and silently drop it).
    if head.lstrip(b"\xef\xbb\xbf \t\r\n\x0b\x0c")[:1] == b"<":
        import xml.etree.ElementTree as ET
        return ET.parse(str(path)).getroot()
    from ..mapmaker import xmlb
    return xmlb.read_xmlb(Path(path))


def parse_briefing(map_igb_path, game_dir_override=None):
    """Parse a briefing's sibling cast/staging/script into a BriefingData model.

    Handles BOTH the XML2/MUA "briefing" (compiled .chrb/.xmlb, maps/briefing,
    EA_ZONE, actors/zone_<b>.igb) and the XML1 console "mocap" cutscene
    (plain-text .chr/.xml, maps/mocap/mocapN, EA_MISSION, actors/mission_mocapN)
    — see _briefing_profile.

    Args:
        map_igb_path: path to the briefing map .igb.
        game_dir_override: optional explicit asset root (contains actors/ +
            maps/ + data/ + scripts/). When set, used instead of walking up
            from the map path — lets XML1 assets live anywhere.

    Returns:
        BriefingData, or None on failure.
    """
    base = os.path.splitext(map_igb_path)[0]
    name = os.path.basename(base)
    profile = _briefing_profile(map_igb_path)

    game_dir = None
    if game_dir_override and os.path.isdir(game_dir_override):
        game_dir = os.path.abspath(game_dir_override)
    if not game_dir:
        game_dir = _find_game_dir(map_igb_path)

    data = BriefingData(name=name, map_igb=map_igb_path,
                        game_dir=game_dir or "", game=profile["game"])

    # ---- cast (.chrb XML2 / .chr XML1) ----
    for ext in profile["cast_exts"] + (".chrb", ".chr"):
        cand = base + ext
        if os.path.exists(cand):
            try:
                root = _read_xml_any(cand)
                for ch in root.iter("character"):
                    nm = ch.attrib.get("name")
                    if nm:
                        data.cast.append(nm)
            except Exception as exc:
                _log.warning("cast parse failed: %s", exc)
            break

    # ---- staging: spawners, zonescript, audio, props ----
    # XML2/MUA store it compiled (.xmlb/.engb); XML1 stores plain-text .xml.
    staging = None
    for ext in profile["staging_exts"] + (".xmlb", ".engb", ".xml"):
        cand = base + ext
        if os.path.exists(cand):
            staging = cand
            break
    if staging:
        try:
            root = _read_xml_any(staging)
            for e in root.iter("entity"):
                a = e.attrib
                cn = a.get("classname", "")
                if a.get("name") == "world" or cn == "":
                    if "zonescript" in a:
                        data.zonescript = a["zonescript"]
                    if "soundfile" in a:
                        data.soundfile = a["soundfile"]
                    if "act" in a:
                        data.act = a["act"]
                if cn == "monsterspawnerent" and a.get("character"):
                    data.spawners.append(BriefingSpawner(
                        name=a.get("name", ""),
                        character=a.get("character", ""),
                        monster_name=a.get("monster_name", ""),
                        monster_skin=a.get("monster_skin", ""),
                    ))
                elif a.get("model", "").startswith("briefing_holo_models") or \
                        (cn == "physent" and a.get("model")):
                    data.props.append(BriefingProp(
                        name=a.get("name", ""), model=a.get("model", ""),
                        classname=cn))
            # precache: voice + subtitle
            for e in root.iter("precache"):
                a = e.attrib
                fn = a.get("filename", "")
                if a.get("type") == "sound" and "voice" in fn:
                    data.voice_precache = fn
                elif "subtitle" in fn:
                    data.subtitle = fn
            # <inst name="sp_X" pos="x y z" orient="rx ry rz"> — world placement
            inst_by_name = {}
            for inst in root.iter("inst"):
                nm = inst.attrib.get("name")
                if nm:
                    inst_by_name[nm] = inst.attrib
            for sp in data.spawners:
                a = inst_by_name.get(sp.name)
                if not a:
                    continue
                if a.get("pos"):
                    try:
                        sp.pos = tuple(float(x) for x in a["pos"].split())
                    except ValueError:
                        pass
                if a.get("orient"):
                    try:
                        sp.orient = tuple(float(x) for x in a["orient"].split())
                    except ValueError:
                        pass
        except Exception as exc:
            _log.warning("staging parse failed: %s", exc)

    # ---- anim -> character from the briefing script (authoritative) ----
    # XML2:  scripts/briefing/<name>.py   playanim("EA_ZONE3", "cyclops", ...)
    # XML1:  scripts/mocap/mocapN/<name>.py  playanim("EA_MISSION1","wolverine")
    if game_dir:
        import re
        script = os.path.join(game_dir, "scripts", *profile["script_subdir"],
                              name + ".py")
        if not os.path.exists(script):
            alt = os.path.join(game_dir, "scripts", "briefing", name + ".py")
            if os.path.exists(alt):
                script = alt
        if os.path.exists(script):
            try:
                text = open(script, encoding="utf-8", errors="replace").read()
                # Walk the script IN ORDER, accumulating waittimed() so each
                # playanim / voice sound gets its scripted START TIME — the
                # game staggers the cast (e.g. Wolverine t=0.5, ProfX t=1.9)
                # and starts the voice with the last one.
                zone_of = {}     # monster -> zoneNN
                time_of = {}     # monster -> start seconds
                t = 0.0
                for line in text.splitlines():
                    mw = re.search(r'waittimed\s*\(\s*([0-9.]+)', line)
                    if mw:
                        t += float(mw.group(1))
                        continue
                    mp = re.search(
                        r'playanim\s*\(\s*"EA_([A-Z]+)(\d+)"\s*,\s*"([^"]+)"',
                        line)
                    if mp:
                        mon = mp.group(3).lower()
                        if mon not in zone_of:
                            # the tag names the anim inside the performance igb:
                            # EA_ZONE3 -> 'zone3' (XML2), EA_MISSION7 ->
                            # 'mission7' (XML1).
                            zone_of[mon] = (mp.group(1) + mp.group(2)).lower()
                            time_of[mon] = t
                        continue
                    if (re.search(r'sound\s*\(\s*"PLAY_SOUND"', line)
                            and "voice" in line.lower()):
                        if data.voice_time is None:
                            data.voice_time = t
                        # voice_ref feeds the XML1 VAG resolver only; XML2/MUA
                        # voice is keyed by the world entity's soundfile
                        # (ZSNDPC), so don't capture it there.
                        if data.game == "xml1" and not data.voice_ref:
                            mv = re.search(
                                r'sound\s*\(\s*"PLAY_SOUND"\s*,\s*"([^"]+)"',
                                line)
                            if mv:
                                data.voice_ref = mv.group(1)
                        continue
                    # camera timeline: cameraMove(" x y z ", dur) /
                    # cameraPan(" roll pitch yaw ", dur, ...) — pan degrees:
                    # pitch = downward tilt, yaw = heading from +X (CCW)
                    mc = re.search(r'cameraMove\s*\(\s*"\s*([-0-9. ]+?)\s*"\s*,'
                                   r'\s*([0-9.]+)', line)
                    if mc:
                        try:
                            xyz = tuple(float(v) for v in mc.group(1).split())
                            data.camera_events.append(
                                (t, xyz, None, float(mc.group(2))))
                        except ValueError:
                            pass
                        continue
                    mpn = re.search(r'cameraPan\s*\(\s*"\s*([-0-9. ]+?)\s*"\s*,'
                                    r'\s*([0-9.]+)', line)
                    if mpn:
                        try:
                            rpy = tuple(float(v) for v in mpn.group(1).split())
                            data.camera_events.append(
                                (t, None, rpy, float(mpn.group(2))))
                        except ValueError:
                            pass
                for sp in data.spawners:
                    z = zone_of.get(sp.monster_name.lower())
                    if z:
                        sp.zone_anim = z
                        sp.start_time = time_of.get(sp.monster_name.lower(), 0.0)
            except Exception as exc:
                _log.warning("script mapping parse failed: %s", exc)

    # ---- resolve the mocap performance igb ----
    # XML2: zonescript "briefing/briefing1_10" -> actors/zone_briefing1_10.igb.
    # XML1: the mocapN folder keys it -> actors/mission_mocapN.igb (one shared
    #       performance file per cutscene, holding all cast anims 'mission<n>').
    if game_dir:
        cands = []
        if profile["game"] == "xml1" and profile["mocap_n"]:
            cands.append(os.path.join(
                game_dir, "actors", f"mission_mocap{profile['mocap_n']}.igb"))
        zs_leaf = os.path.basename(data.zonescript) if data.zonescript else name
        cands.append(os.path.join(game_dir, "actors", f"zone_{zs_leaf}.igb"))
        for cand in cands:
            if os.path.exists(cand):
                data.mocap_igb = cand
                break
        # XML1 detected via .xml/.chr siblings with no derivable N (relocated /
        # flattened assets under an asset-root override): the shared performance
        # is still actors/mission_mocap*.igb. Adopt the SOLE match rather than
        # silently loading a frozen cast; if several exist, N is genuinely
        # ambiguous so leave it (better a still cutscene than the wrong perf).
        if not data.mocap_igb and profile["game"] == "xml1":
            import glob
            hits = sorted(glob.glob(os.path.join(
                game_dir, "actors", "mission_mocap*.igb")))
            if len(hits) == 1:
                data.mocap_igb = hits[0]

    return data


def resolve_skins(data):
    """Resolve each spawner to its dedicated MOCAP MODEL (200xx), not the
    gameplay skin.

    A briefing spawner carries two identities:
      character   = 'wolverine_mc'  -> npcstat 'Wolverine_MC' -> skin 20025
                    (the 65-bone mocap model the zone performance was authored
                    for; full face + fingers). THIS is what the game loads.
      monster_name= 'Wolverine'     -> gameplay skin 0303 (35-bone, no face) —
                    used only as a fallback if the _mc entry is missing.

    The zone performances play DIRECTLY on the mocap model (identical skeleton),
    so no retarget is needed. Populates skin_code/skin_path/characteranims and
    sets is_mocap_model when the 200xx model was found.
    """
    if not data.game_dir:
        return
    from ..mapmaker.game_database import (get_character_db,
                                          get_skin_actor_path)
    try:
        db = get_character_db(data.game_dir)
    except Exception as exc:
        _log.warning("character db load failed: %s", exc)
        db = {}

    # index by lowercase internal name for tolerant lookup
    by_name = {}
    for k, ci in db.items():
        by_name[str(getattr(ci, "name", k)).lower()] = ci
        by_name[str(k).lower()] = ci

    for sp in data.spawners:
        # explicit skin override on the spawner always wins
        if sp.monster_skin:
            sp.skin_code = sp.monster_skin
            ci = None
        else:
            # PREFER the _mc mocap character (-> 200xx model). Fall back to the
            # gameplay character only if the mocap entry is absent.
            ci = by_name.get(sp.character.lower()) if sp.character else None
            if ci is None and sp.monster_name:
                ci = by_name.get(sp.monster_name.lower())
            if ci is not None and getattr(ci, "skin", ""):
                sp.skin_code = ci.skin
        if ci is not None:
            sp.characteranims = getattr(ci, "characteranims", "")
        if sp.skin_code:
            path = get_skin_actor_path(sp.skin_code, data.game_dir)
            if path:
                sp.skin_path = path
            # a briefing spawner's skin is a dedicated mocap model: XML2/MUA
            # 200xx (65-bone), XML1 63xx (all XML1 mocap-cutscene cast).
            sp.is_mocap_model = (data.game == "xml1"
                                 or str(sp.skin_code).startswith("200"))


def default_zone_map(cast, anim_names):
    """Pair the mocap performances to the cast when no explicit mapping exists.

    Heuristic (until the exact game mapping is wired): performances sorted by
    their trailing number (zone3, zone6, ... zone16) pair with the cast in
    .chrb order. Returns {cast_name: anim_name}.
    """
    def _num(n):
        d = "".join(c for c in n if c.isdigit())
        return int(d) if d else 0
    ordered = sorted(anim_names, key=_num)
    return {c: a for c, a in zip(cast, ordered)}


def load_briefing(context, map_igb_path, options=None):
    """Assemble a full briefing scene into a 'Briefing: <b>' collection.

    Stages: parse -> map import -> per-character MOCAP MODEL import (200xx) ->
    DIRECT-apply the matching zone performance (no retarget) -> place at the
    staging marks -> audio.

    Args:
        context: Blender context.
        map_igb_path: maps/briefing/<b>.igb.
        options: dict —
                 apply_performances (bool, default True): apply the zone perf,
                 root_motion (bool, default False): keep the performance's root
                     translation (else the cast stays on the <inst> marks),
                 zone_map (dict override): {character: zoneNN},
                 audio (bool): load the voice strip (ZSNDPC for XML2, VAG XML1),
                 game_dir (str): explicit asset root override (XML1 assets can
                     live anywhere; else the root is found from the map path),
                 operator (for reporting).

    Returns:
        dict summary, or None on failure.
    """
    import bpy
    options = options or {}
    op = options.get("operator")

    def _report(kind, msg):
        _log.info(msg)
        if op:
            op.report({kind}, msg)

    data = parse_briefing(map_igb_path,
                          game_dir_override=options.get("game_dir"))
    if data is None:
        _report('ERROR', "Could not parse briefing")
        return None
    resolve_skins(data)

    # ---- collection ----
    coll = bpy.data.collections.new(f"Briefing: {data.name}")
    context.scene.collection.children.link(coll)

    summary = {"name": data.name, "map": False, "characters": [], "mocap": False,
               "audio": False, "props": len(data.props)}

    # ---- 1. map / environment ----
    try:
        from ..importer.import_igb import import_igb
        import_igb(context, map_igb_path, operator=op)
        # move the imported map collection under our briefing collection
        mapcoll = bpy.data.collections.get(os.path.splitext(
            os.path.basename(map_igb_path))[0])
        if mapcoll and mapcoll.name not in coll.children:
            try:
                context.scene.collection.children.unlink(mapcoll)
                coll.children.link(mapcoll)
            except Exception:
                pass
        summary["map"] = True
    except Exception as exc:
        import traceback
        traceback.print_exc()
        _report('WARNING', f"Map import failed: {exc}")

    # ---- 1b. detect the walkable floor under each staging mark NOW, while the
    # scene holds ONLY the map — once characters are placed they become ray
    # targets themselves and corrupt the estimate (a ray onto a neighbour).
    ground_of = {}
    try:
        context.view_layer.update()
        for sp in data.spawners:
            if sp.pos:
                # XML1 marks sit ~20u below the deck in enclosed interiors, so
                # use the nearest-to-mark floor detector + feet-snap. XML2/MUA
                # keeps its render-verified behavior (wide Z scale, >50 gate).
                if data.game == "xml1":
                    g = _detect_floor(context, sp.pos[0], sp.pos[1], sp.pos[2])
                else:
                    g = _ground_z(context, sp.pos[0], sp.pos[1], sp.pos[2])
                if g is not None:
                    ground_of[sp.name] = g
    except Exception as exc:
        _log.warning("floor detection failed: %s", exc)

    # ---- 2. mocap performances + their authoring skeleton ----
    # The zone file (actors/zone_<b>.igb) holds ONE 65-bone skeleton + one
    # Enbaya performance per character. We import it as a hidden reference rig
    # (used to rebase the unanimated root) AND parse the raw ParsedAnimations
    # for the DIRECT apply onto each character's own mocap model.
    mocap_arm = None
    zone_anims = {}          # name -> ParsedAnimation
    if data.mocap_igb and os.path.exists(data.mocap_igb):
        try:
            from . import mocap_tools
            res = mocap_tools.import_mocap(context, data.mocap_igb, operator=op)
            if res:
                mocap_arm, _perf_actions = res
                summary["mocap"] = True
                for c in list(mocap_arm.users_collection):
                    c.objects.unlink(mocap_arm)
                coll.objects.link(mocap_arm)
                # It's a bare reference rig — hide it; the visible cast are the
                # mocap models. Keep it so the user can inspect the raw perf.
                try:
                    mocap_arm.hide_set(True)
                    mocap_arm["igb_briefing_ref_rig"] = True
                except Exception:
                    pass
        except Exception as exc:
            import traceback
            traceback.print_exc()
            _report('WARNING', f"Mocap load failed: {exc}")
        # Parse the raw performances for the direct (retarget-free) apply.
        try:
            from ..igb_format.igb_reader import IGBReader
            from .sg_skeleton import extract_skeleton
            from .sg_animation import extract_animations
            zr = IGBReader(data.mocap_igb)
            zr.read()
            zsk = extract_skeleton(zr)
            for pa in extract_animations(zr, zsk):
                zone_anims[pa.name] = pa
        except Exception as exc:
            _log.warning("zone performance parse failed: %s", exc)

    zone_map = (options.get("zone_map")
                or default_zone_map(data.cast, list(zone_anims.keys())))

    # ---- 3. per-character mocap models + DIRECT-applied performance ----
    # Each spawner already resolved to its 65-bone mocap model (200xx). The zone
    # performance was authored for that exact skeleton, so it applies directly
    # (build_action with rebase_unanimated_parents) — NO retarget, no distortion.
    apply_perf = options.get("apply_performances", True) and mocap_arm is not None
    root_motion = options.get("root_motion", False)
    max_frame = 0
    for sp in data.spawners:
        if not sp.skin_path or not os.path.exists(sp.skin_path):
            summary["characters"].append((sp.monster_name, "no skin"))
            continue
        try:
            from .actor_import import import_actor
            r = import_actor(context, sp.skin_path,
                             skin_filepaths=[(sp.skin_code, sp.skin_path)],
                             game_dir=data.game_dir or None,
                             options={"import_animations": False},
                             operator=op)
            if not r:
                summary["characters"].append((sp.monster_name, "import fail"))
                continue
            char_arm = r[0]
            char_arm["igb_briefing_character"] = sp.character
            char_arm["igb_mocap_model"] = sp.is_mocap_model
            # move character objects into the collection
            for obj in [char_arm] + [o for (_v, o) in (r[1] or [])]:
                for c in list(obj.users_collection):
                    c.objects.unlink(obj)
                coll.objects.link(obj)

            # place at the staging position/orientation from the <inst>.
            if sp.pos:
                if data.game == "xml1":
                    # XML1 mark Z is unreliable (sits ~20u below the deck) — place
                    # at the raw mark and let the feet-snap below settle Z.
                    char_arm.location = sp.pos
                else:
                    # XML2/MUA: trust the authored mark; ground-snap only when it
                    # hangs implausibly high above the detected floor (Z=0 marks).
                    gx, gy, gz = sp.pos
                    ground = ground_of.get(sp.name)
                    if ground is not None and (gz - ground) > 50.0:
                        gz = ground
                    char_arm.location = (gx, gy, gz)
            if sp.orient:
                char_arm.rotation_mode = 'XYZ'
                char_arm.rotation_euler = sp.orient

            status = "placed" if sp.pos else "static"
            act = None
            if apply_perf:
                perf = (sp.zone_anim or zone_map.get(sp.monster_name)
                        or zone_map.get(sp.character))
                pa = zone_anims.get(perf) if perf else None
                if pa is not None:
                    from .animation_builder import build_action
                    # root_motion off = 'vertical': the char stays on its mark
                    # horizontally but keeps the performance's HEIGHT (seated
                    # Prof X sits in his chair instead of floating at rest
                    # height above it).
                    act = build_action(
                        char_arm, pa, bind_pose=None, fps=None,
                        anim_armature=mocap_arm,
                        rebase_unanimated_parents=True,
                        relative_translation=True,
                        root_motion_mode='full' if root_motion else 'vertical')
                    if act is not None:
                        # stagger per the briefing script (Wolverine t=0.5s,
                        # ProfX t=1.9s, ...) so the cast and voice line up.
                        if sp.start_time > 0:
                            fps = (context.scene.render.fps
                                   / max(context.scene.render.fps_base, 1e-6))
                            _shift_action_frames(
                                act, int(round(sp.start_time * fps)))
                        if char_arm.animation_data is None:
                            char_arm.animation_data_create()
                        char_arm.animation_data.action = act
                        try:
                            max_frame = max(max_frame, int(act.frame_range[1]))
                        except Exception:
                            pass
                        mtag = "mocap" if sp.is_mocap_model else "gameplay"
                        status = f"{perf} direct-applied [{mtag} {sp.skin_code}]"

            # feet-snap: settle the character vertically so its planted foot
            # rests on the detected deck. The staging mark Z is unreliable —
            # XML1 marks sit ~20u below the floor, leaving the cast dangling in
            # the void below the map ("floating"). Uses the animated stance when
            # a performance is applied, else the rest pose.
            if (options.get("snap_to_floor", True) and sp.pos
                    and data.game == "xml1"):
                floor = ground_of.get(sp.name)
                if floor is not None:
                    if act is not None:
                        fr = act.frame_range
                        frames = [fr[0] + (fr[1] - fr[0]) * t
                                  for t in (0.1, 0.3, 0.5, 0.7, 0.9)]
                    else:
                        frames = [context.scene.frame_current]
                    try:
                        d = _snap_feet_to_floor(context, char_arm, floor, frames)
                        if abs(d) > 0.01:
                            status += f" snap{d:+.0f}z"
                    except Exception as exc:
                        _log.warning("feet-snap failed: %s", exc)
            summary["characters"].append((sp.monster_name, status))
        except Exception as exc:
            import traceback
            traceback.print_exc()
            summary["characters"].append((sp.monster_name, f"error: {exc}"))

    # ---- 3b. timeline: cover the whole briefing so the performances are
    # visible. The default scene range (1..250) hides almost all of the
    # ~2000-frame performances, AND the cast stands nearly still for the first
    # ~120 frames before gesturing — so playing the default range looks frozen
    # ("no animation"). Extend the range and jump to a clearly-posed frame.
    if apply_perf and max_frame > 1:
        try:
            context.scene.frame_start = 1
            context.scene.frame_end = max_frame
            context.scene.frame_set(max(1, min(max_frame, int(max_frame * 0.35))))
        except Exception:
            pass
    summary["frame_end"] = max_frame

    # ---- 4. audio (ZSNDPC voice) ----
    if options.get("audio", True):
        try:
            summary["audio"] = _load_briefing_audio(context, data)
        except Exception as exc:
            _log.warning("audio load failed: %s", exc)

    # ---- 5. scripted camera (scene ready to play/render) ----
    if options.get("camera", True):
        try:
            cam = _build_briefing_camera(context, data, coll)
            summary["camera"] = cam is not None
        except Exception as exc:
            _log.warning("camera build failed: %s", exc)
            summary["camera"] = False

    return summary


def _ground_z(context, x, y, z_hint=0.0):
    """Estimate the walkable ground Z at (x, y) by raycasting the loaded map.

    Multi-samples a small footprint (center + 4 offsets) and takes the MEDIAN
    hit: the briefing decks are GRATED (a ray through a hole hits the hull
    several units below) AND props like Prof X's wheelchair sit right on the
    marks (a ray onto the prop hits several units above) — the median rejects
    both outliers. MUST be called while the scene contains only the MAP:
    placed characters are ray targets too and corrupt the estimate.
    Returns None if nothing is hit."""
    from mathutils import Vector
    dg = context.evaluated_depsgraph_get()
    down = Vector((0.0, 0.0, -1.0))
    hits = []
    # center + a WIDE ring (r=8): props on the mark (Prof X's wheelchair base
    # plate spans the whole close footprint) must be outvoted by deck hits.
    for dx, dy in ((0, 0), (8, 0), (-8, 0), (0, 8), (0, -8),
                   (5.7, 5.7), (-5.7, 5.7), (5.7, -5.7), (-5.7, -5.7)):
        for oz in (z_hint + 4.0, 10000.0):
            try:
                hit, loc, _n, _i, _o, _m = context.scene.ray_cast(
                    dg, Vector((x + dx, y + dy, oz)), down)
            except Exception:
                hit = False
            if hit:
                hits.append(loc.z)
                break
    if not hits:
        return None
    hits.sort()
    return hits[len(hits) // 2]


def _detect_floor(context, x, y, z_hint):
    """Find the walkable-floor Z under (x, y): the map surface (upward-facing)
    NEAREST the staging mark.

    The mark Z is unreliable — the engine drops spawns to the floor, and some
    briefings (esp. XML1 console cutscenes) author marks ~20u BELOW the deck.
    Enclosed interiors also have a ceiling directly overhead, so we cannot take
    the topmost hit. We gather EVERY upward-facing crossing in the vertical
    column and pick the one closest to the mark.

    Prefers the COLLISION mesh (what the game stands actors on) but falls back
    to the visual deck when the collision hull disagrees — coarse closed hulls
    report consoles/roofs rather than the deck. Returns Z or None.
    """
    import bpy
    from mathutils import Vector
    dg = context.evaluated_depsgraph_get()
    down = Vector((0.0, 0.0, -1.0))
    coll_objs = set(o for o in bpy.data.objects
                    if o.type == 'MESH' and o.name.lower().endswith('collision'))

    def column(only=None, exclude=None):
        """Upward-facing hit Zs down the column at (x,y), footprint-sampled."""
        zs = []
        for dx, dy in ((0, 0), (6, 0), (-6, 0), (0, 6), (0, -6)):
            z = 1.0e5
            for _ in range(48):
                hit, loc, nrm, _i, ob, _m = context.scene.ray_cast(
                    dg, Vector((x + dx, y + dy, z)), down)
                if not hit or loc.z <= -1.0e5:
                    break
                if (nrm.z > 0.5
                        and (only is None or ob in only)
                        and (exclude is None or ob not in exclude)):
                    zs.append(loc.z)
                z = loc.z - 0.5
        return zs

    def nearest(zs):
        # a real floor is within a sane band of the mark (rejects the ceiling)
        cands = [z for z in zs if abs(z - z_hint) <= 120.0]
        return min(cands, key=lambda z: abs(z - z_hint)) if cands else None

    visual = nearest(column(exclude=coll_objs))
    coll = nearest(column(only=coll_objs)) if coll_objs else None
    # collision preferred, but only when it AGREES with the visual deck; a
    # coarse hull that reports a surface far from the visual floor is not it.
    if coll is not None and (visual is None or abs(coll - visual) <= 15.0):
        return coll
    return visual


def _snap_feet_to_floor(context, arm, floor_z, frames, tol=2.0):
    """Shift an armature vertically so the character's lowest point (the planted
    foot) rests on floor_z.

    Uses the MINIMUM foot height across the sampled frames (the stance level) so
    a mid-stride foot lift never hoists the whole body, and the character never
    sinks below the floor. No-op when already within `tol` of the floor, so a
    correctly-authored mark is left untouched. Returns the applied delta.
    """
    scene = context.scene
    cur = scene.frame_current
    lowest = None
    try:
        for f in frames:
            scene.frame_set(int(round(f)))
            context.view_layer.update()
            mn = min((arm.matrix_world @ pb.head).z for pb in arm.pose.bones)
            lowest = mn if lowest is None else min(lowest, mn)
    finally:
        scene.frame_set(cur)
    if lowest is None:
        return 0.0
    delta = floor_z - lowest
    if abs(delta) < tol:
        return 0.0
    arm.location.z += delta
    return delta


def _build_briefing_camera(context, data, coll):
    """Create an animated camera from the briefing script's cameraMove/Pan
    timeline and make it the scene camera.

    Pan convention (verified against briefing1_10's authored shots):
    "roll pitch yaw" in degrees — pitch = downward tilt, yaw = heading measured
    from +X counter-clockwise. Blender camera at euler (90°,0,-90°+yaw) looks
    along that heading; pitch subtracts from the X euler."""
    import bpy
    import math as _m

    if not data.camera_events:
        return None
    scene = context.scene
    fps = scene.render.fps / max(scene.render.fps_base, 1e-6)

    cam_data = bpy.data.cameras.new(f"{data.name}_camera")
    cam_data.angle = _m.radians(60.0)     # ~game FOV
    cam_data.clip_end = 10000.0
    cam = bpy.data.objects.new(f"{data.name}_camera", cam_data)
    coll.objects.link(cam)
    cam.rotation_mode = 'XYZ'

    def _euler(rpy, prev_yaw):
        _roll, pitch, yaw = rpy
        # unwrap yaw vs the previous keyed yaw so slow pans don't spin 360°
        if prev_yaw is not None:
            while yaw - prev_yaw > 180.0:
                yaw -= 360.0
            while yaw - prev_yaw < -180.0:
                yaw += 360.0
        return (_m.radians(90.0 - pitch), 0.0, _m.radians(yaw - 90.0)), yaw

    cur_pos = None
    cur_rot = None
    prev_yaw = None
    for (t, xyz, rpy, dur) in data.camera_events:
        f0 = 1 + int(round(t * fps))
        f1 = 1 + int(round((t + max(dur, 0.0)) * fps))
        if xyz is not None:
            if cur_pos is not None and f1 > f0:
                cam.location = cur_pos
                cam.keyframe_insert("location", frame=f0)
            cam.location = xyz
            cam.keyframe_insert("location", frame=max(f1, f0 + (0 if cur_pos
                                                                is None else 1)))
            cur_pos = xyz
        if rpy is not None:
            eul, yaw = _euler(rpy, prev_yaw)
            if cur_rot is not None and f1 > f0:
                cam.rotation_euler = cur_rot
                cam.keyframe_insert("rotation_euler", frame=f0)
            cam.rotation_euler = eul
            cam.keyframe_insert("rotation_euler", frame=max(f1, f0 + (0 if
                                                            cur_rot is None
                                                            else 1)))
            cur_rot = eul
            prev_yaw = yaw

    scene.camera = cam
    # 4:3 like the game; keep it modest so playback stays responsive
    scene.render.resolution_x = 960
    scene.render.resolution_y = 720

    # The game moves the camera LINEARLY between authored positions and CUTS on
    # instant (dur~0) shots. Blender's default Bezier easing overshoots between
    # closely-spaced cut keyframes — sending the camera flying off between shots
    # (looks like "the camera doesn't work"). Force LINEAR so every move is a
    # straight interpolation and the instant cuts stay clean.
    if cam.animation_data and cam.animation_data.action:
        for fc in cam.animation_data.action.fcurves:
            for kp in fc.keyframe_points:
                kp.interpolation = 'LINEAR'
            fc.update()

    # Look through the briefing camera in every 3D viewport so the cinematic
    # framing is visible immediately — otherwise the default view shows the
    # scene from the side and it looks like the camera did nothing. Guarded:
    # there is no screen in background/headless mode.
    try:
        for area in getattr(context.screen, "areas", None) or []:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D' and space.region_3d is not None:
                        space.region_3d.view_perspective = 'CAMERA'
    except Exception:
        pass
    return cam


def _shift_action_frames(action, offset):
    """Shift every keyframe in an Action forward by `offset` frames (fast,
    via foreach_get/set). Used to reproduce the briefing script's staggered
    performance starts."""
    if offset <= 0:
        return
    for fc in action.fcurves:
        n = len(fc.keyframe_points)
        if not n:
            continue
        co = [0.0] * (2 * n)
        fc.keyframe_points.foreach_get("co", co)
        for i in range(0, 2 * n, 2):
            co[i] += offset
        fc.keyframe_points.foreach_set("co", co)
        fc.update()


def _load_briefing_audio(context, data):
    """Decode the briefing's voice and add it as a VSE sound strip.

    Dispatches by game: XML2/MUA voice is a ZSNDPC bank (.zss, keyed by the
    world entity's soundfile); XML1 voice is a Sony VAG referenced by the
    script's sound() call. Each decoder no-ops (returns False) when its files
    aren't present — e.g. stock XML1 decompiles ship no voice streams.
    Returns True if a strip was added.
    """
    if not data.game_dir:
        return False
    # XML1: VAG voice referenced by the script (voice/briefings/<id>). Gate
    # strictly to XML1 so an XML2/MUA briefing is never routed to the PS2 VAG
    # decoder (which would probe bogus paths and could suppress its ZSNDPC voice).
    if data.game == "xml1":
        try:
            from . import vag_audio
            if vag_audio.add_briefing_voice_strip(context, data):
                return True
        except Exception as exc:
            _log.warning("VAG voice load failed: %s", exc)
    # XML2/MUA: ZSNDPC bank keyed by the world entity's soundfile.
    if data.soundfile:
        try:
            from . import zsnd_audio
        except Exception:
            return False
        return zsnd_audio.add_briefing_voice_strip(context, data)
    return False

