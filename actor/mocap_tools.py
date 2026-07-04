"""Mocap toolset for XML2/MUA cutscene-style motion capture.

The game's real motion capture lives in the actor "zone" files (e.g.
actors/zone_briefing1_10.igb): long (~80s) Enbaya-compressed performances on a
high-detail 65-bone skeleton (full body + face + fingers + toes), with NO skin
of their own — the visible characters come from the separate skin files.

This module gives three capabilities, each built on existing, verified engines:

  1. IMPORT  — bring a mocap file in as a visible, tagged "mocap rig" with every
     performance as a Blender Action.               (wraps actor_import.import_actor)
  2. RETARGET — drive a playable character's rig from a mocap performance.
                                                     (wraps vr_mocap.retarget_and_bake)
  3. EXPORT  — write a performance back to the v6 zone mocap format.
                                                     (wraps vmc_bridge.export_action_as_igb)

The 65-bone mocap skeleton uses underscore bone names (Bip01_Spine,
Bip01_L_UpperArm, Bip01_L_Finger0_0). The fuzzy bone matcher used by the
retarget maps those onto a character's spaced Bip01 names automatically; face
and finger tracks only transfer when the target rig actually has those bones.
"""

import os
import logging

_log = logging.getLogger(__name__)


def import_mocap(context, filepath, operator=None):
    """Import a mocap performance file as a visible, tagged mocap rig.

    Args:
        context: Blender context.
        filepath: Path to the mocap .igb (e.g. actors/zone_briefing1_10.igb).
        operator: Optional operator for reporting.

    Returns:
        (armature_obj, actions) or None on failure.
    """
    from .actor_import import import_actor

    res = import_actor(
        context, filepath, skin_filepaths=None,
        options={"import_skins": False, "import_materials": False,
                 "import_animations": True},
        operator=operator,
    )
    if not res:
        return None
    arm, _skins, actions = res

    # Tag it as a mocap source rig so the retarget UI can find it, and so it is
    # not mistaken for a playable actor.
    arm["igb_mocap_rig"] = True
    arm["igb_mocap_file"] = filepath

    # Make the skeleton visible without a skin: octahedral bones drawn in front.
    try:
        arm.show_in_front = True
        arm.data.display_type = 'OCTAHEDRAL'
    except Exception:
        pass

    _log.info("Imported mocap rig '%s': %d bones, %d performances",
              arm.name, len(arm.data.bones), len(actions))
    return arm, actions


def list_performances(arm):
    """Return the list of Actions that belong to a mocap rig (best effort).

    Blender Actions aren't owned by an object, so we return all actions whose
    channels target bones present on this armature.
    """
    import bpy
    bone_names = {b.name for b in arm.data.bones}
    out = []
    for act in bpy.data.actions:
        for fc in act.fcurves:
            dp = fc.data_path
            if dp.startswith('pose.bones["'):
                bn = dp.split('"')[1]
                if bn in bone_names:
                    out.append(act)
                    break
    return out


def retarget_performance(src_arm, tgt_arm, performance_action,
                         frame_start, frame_end, game='xml2',
                         root_motion=True, step=1):
    """Retarget a mocap performance onto a playable character rig.

    Args:
        src_arm: The mocap rig (source), 65-bone.
        tgt_arm: The target character armature (Bip01).
        performance_action: The Action on src_arm to retarget.
        frame_start, frame_end: Inclusive frame range to bake.
        game: 'xml2' or 'mua'.
        root_motion: Carry hip translation (scaled) onto the Pelvis.
        step: Frame step.

    Returns:
        (baked_action, n_mapped, n_frames) or (None, 0, 0).
    """
    from .vr_mocap import retarget_and_bake

    # The bake samples the source's evaluated pose each frame, so the chosen
    # performance must be the source's active Action.
    if src_arm.animation_data is None:
        src_arm.animation_data_create()
    src_arm.animation_data.action = performance_action

    name = "MoCap_" + (performance_action.get("igb_anim_name",
                                               performance_action.name))
    return retarget_and_bake(
        src_arm, tgt_arm, frame_start, frame_end, game=game,
        action_name=name, root_motion=root_motion, step=step)


def export_performance(arm, action, output_path, reference_path=None,
                       game='xml2', anim_name=None, fps=30.0):
    """Export an Action as a v6/v8 zone-style mocap IGB.

    Args:
        arm: Armature whose skeleton + the Action define the performance.
        action: The Action to export.
        output_path: Output .igb path.
        reference_path: A mocap/anim IGB providing the schema. Defaults to the
                        rig's stored igb_mocap_file / igb_anim_file.
        game: 'xml2' (v6) or 'mua'.
        anim_name: Name for the animation in the file.
        fps: Sampling rate.

    Returns:
        output_path on success, else None.
    """
    from .vmc_bridge import export_action_as_igb

    if reference_path is None:
        reference_path = (arm.get("igb_mocap_file")
                          or arm.get("igb_anim_file") or "")
    if not reference_path or not os.path.exists(reference_path):
        _log.error("No reference mocap/anim IGB for schema extraction")
        return None

    return export_action_as_igb(
        arm, action, output_path, game=game, reference_path=reference_path,
        anim_name=anim_name or action.get("igb_anim_name", action.name),
        fps=fps)
