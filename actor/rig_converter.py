"""Auto-rig converter: Unity Humanoid / Mixamo -> XML2 IGB skeleton.

Converts a Blender armature from Unity Humanoid or Mixamo bone naming to the
XML2 "Bip01" skeleton convention. After conversion, the armature has all the
custom properties needed by the from-scratch skin exporter (skin_export.py).

Usage:
    from igb_blender.actor.rig_converter import convert_rig
    result = convert_rig(armature_obj, profile='AUTO')
    # result = {'success': True, 'mapped': 30, 'added': 5, 'removed': 12}
"""

import json
from typing import Dict, List, Optional, Tuple

# ============================================================================
# XML2 Target Skeleton Definition (35 bones)
# ============================================================================

# Each entry: (name, index, parent_idx, bm_idx, flags)
# Non-deforming bones have bm_idx = -1
XML2_SKELETON = [
    ("",                       0,  -1,  -1, 0x40),  # unnamed root
    ("Bip01",                  1,   0,  -1, 0x02),
    ("Bip01 Pelvis",           2,   1,   0, 0x02),
    ("Bip01 Spine",            3,   2,   1, 0x02),
    ("Bip01 Spine1",           4,   3,   2, 0x02),
    ("Bip01 Spine2",           5,   4,   3, 0x02),
    ("Bip01 Neck",             6,   5,   4, 0x02),
    ("Bip01 Head",             7,   6,   5, 0x02),
    ("Bip01 Ponytail1",        8,   7,   6, 0x02),
    ("Bip01 Ponytail11",       9,   8,   7, 0x02),
    ("Bip01 L Clavicle",      10,   6,   8, 0x02),
    ("Bip01 L UpperArm",      11,  10,   9, 0x02),
    ("Bip01 L Forearm",        12,  11,  10, 0x02),
    ("Bip01 L Hand",           13,  12,  11, 0x02),
    ("Bip01 L Finger0",        14,  13,  12, 0x02),
    ("Bip01 L Finger01",       15,  14,  13, 0x02),
    ("Bip01 L Finger1",        16,  13,  14, 0x02),
    ("Bip01 L Finger11",       17,  16,  15, 0x02),
    ("Bip01 R Clavicle",      18,   6,  16, 0x02),
    ("Bip01 R UpperArm",      19,  18,  17, 0x02),
    ("Bip01 R Forearm",        20,  19,  18, 0x02),
    ("Bip01 R Hand",           21,  20,  19, 0x02),
    ("Bip01 R Finger0",        22,  21,  20, 0x02),
    ("Bip01 R Finger01",       23,  22,  21, 0x02),
    ("Bip01 R Finger1",        24,  21,  22, 0x02),
    ("Bip01 R Finger11",       25,  24,  23, 0x02),
    ("Bip01 L Thigh",          26,   3,  24, 0x02),
    ("Bip01 L Calf",           27,  26,  25, 0x02),
    ("Bip01 L Foot",           28,  27,  26, 0x02),
    ("Bip01 L Toe0",           29,  28,  27, 0x02),
    ("Bip01 R Thigh",          30,   3,  28, 0x02),
    ("Bip01 R Calf",           31,  30,  29, 0x02),
    ("Bip01 R Foot",           32,  31,  30, 0x02),
    ("Bip01 R Toe0",           33,  32,  31, 0x02),
    ("Motion",                 34,   0,  -1, 0x42),
]

# Set of all XML2 bone names (for quick lookup)
XML2_BONE_NAMES = {entry[0] for entry in XML2_SKELETON}

# Joint count = number of deforming bones (bm_idx >= 0)
XML2_JOINT_COUNT = sum(1 for _, _, _, bm, _ in XML2_SKELETON if bm >= 0)


# ============================================================================
# MUA Extended Skeleton (XML2 base + FX bones)
# ============================================================================
# FX bones are non-deforming effect attachment points used by MUA's engine.
# The engine looks up bones by name to attach particles, weapon visuals, and
# scripted effects. All parented to Bip01 by default — users can reparent
# in Blender for per-character customization.

# (name, parent_name, bm_idx, flags)
MUA_FX_BONES = [
    ("Gun1", "Bip01", -1, 0x02),
    ("fx01", "Bip01", -1, 0x02),
    ("fx02", "Bip01", -1, 0x02),
    ("fx03", "Bip01", -1, 0x02),
    ("fx04", "Bip01", -1, 0x02),
    ("fx05", "Bip01", -1, 0x02),
    ("fx06", "Bip01", -1, 0x02),
    ("fx07", "Bip01", -1, 0x02),
    ("fx08", "Bip01", -1, 0x02),
    ("fx09", "Bip01", -1, 0x02),
    ("fx10", "Bip01", -1, 0x02),
    ("fx11", "Bip01", -1, 0x02),
    ("fx12", "Bip01", -1, 0x02),
    ("fx13", "Bip01", -1, 0x02),
]

MUA_FX_BONE_NAMES = {name for name, _, _, _ in MUA_FX_BONES}

# Build MUA_SKELETON = XML2_SKELETON + FX bones (auto-indexed)
def _build_mua_skeleton():
    """Build MUA skeleton by appending FX bones to XML2 skeleton."""
    # Start with XML2 base
    skeleton = list(XML2_SKELETON)
    # Build name→index lookup from XML2
    name_to_idx = {entry[0]: entry[1] for entry in XML2_SKELETON}
    next_idx = len(XML2_SKELETON)
    for fx_name, parent_name, bm_idx, flags in MUA_FX_BONES:
        parent_idx = name_to_idx.get(parent_name, 0)
        skeleton.append((fx_name, next_idx, parent_idx, bm_idx, flags))
        name_to_idx[fx_name] = next_idx
        next_idx += 1
    return skeleton

MUA_SKELETON = _build_mua_skeleton()
MUA_BONE_NAMES = {entry[0] for entry in MUA_SKELETON}
MUA_JOINT_COUNT = sum(1 for _, _, _, bm, _ in MUA_SKELETON if bm >= 0)  # = 32 (same as XML2)


def _build_full_finger_skeleton():
    """MUA skeleton with the reduced 2-finger hand replaced by the full
    5-finger underscore-named hand (30 finger bones, Bip01_{side}_Finger{0-4}
    _{0-2}) — the names MUA's full-finger movesets (Storm/Ultron/Loki) drive.

    Built programmatically from MUA_SKELETON so indices + blend-matrix indices
    stay consistent. MUA-only; gated behind the full_fingers option.
    """
    reduced = {"Bip01 L Finger0", "Bip01 L Finger01", "Bip01 L Finger1",
               "Bip01 L Finger11", "Bip01 R Finger0", "Bip01 R Finger01",
               "Bip01 R Finger1", "Bip01 R Finger11"}
    idx_to_name = {e[1]: e[0] for e in MUA_SKELETON}
    # (name, parent_name, was_deform, flags), dropping the reduced fingers
    seq = []
    for name, idx, parent_idx, bm, flags in MUA_SKELETON:
        if name in reduced:
            continue
        parent_name = idx_to_name.get(parent_idx) if parent_idx >= 0 else None
        seq.append((name, parent_name, bm >= 0, flags))
    # insert the full finger bones right after each Hand
    expanded = []
    for entry in seq:
        expanded.append(entry)
        name = entry[0]
        if name in ("Bip01 L Hand", "Bip01 R Hand"):
            side = 'L' if ' L ' in name else 'R'
            for col in range(5):           # 0=thumb .. 4=little
                for s in range(3):         # 0=proximal,1=medial,2=distal
                    fn = f'Bip01_{side}_Finger{col}_{s}'
                    par = name if s == 0 else f'Bip01_{side}_Finger{col}_{s - 1}'
                    expanded.append((fn, par, True, 0x02))
    # re-index + assign blend-matrix indices to deforming bones in order
    name_to_idx = {e[0]: i for i, e in enumerate(expanded)}
    skel = []
    bm = 0
    for i, (name, parent_name, deform, flags) in enumerate(expanded):
        parent_idx = name_to_idx.get(parent_name, -1) if parent_name else -1
        bm_idx = -1
        if deform:
            bm_idx = bm
            bm += 1
        skel.append((name, i, parent_idx, bm_idx, flags))
    return skel


_MUA_FULL_FINGER_SKELETON = None


def get_skeleton_for_game(game: str, full_fingers: bool = False):
    """Return the skeleton definition for the specified target game.

    Args:
        game: 'XML2' or 'MUA'
        full_fingers: when True and game is MUA, return the full 5-finger
            variant (30 finger bones) instead of the reduced 2-finger hand.

    Returns:
        List of (name, index, parent_idx, bm_idx, flags) tuples.
    """
    global _MUA_FULL_FINGER_SKELETON
    if full_fingers and game == 'MUA':
        if _MUA_FULL_FINGER_SKELETON is None:
            _MUA_FULL_FINGER_SKELETON = _build_full_finger_skeleton()
        return _MUA_FULL_FINGER_SKELETON
    if game == 'MUA':
        return MUA_SKELETON
    return XML2_SKELETON


def get_bone_names_for_game(game: str, full_fingers: bool = False):
    """Return set of bone names for the specified target game."""
    if full_fingers and game == 'MUA':
        return {e[0] for e in get_skeleton_for_game(game, full_fingers=True)}
    if game == 'MUA':
        return MUA_BONE_NAMES
    return XML2_BONE_NAMES


# ============================================================================
# Universal Bone Name Normalization (CATS-style)
# ============================================================================
# Adapts the normalization approach from the CATS Blender Plugin to
# standardize bone names from ANY rig format before matching against aliases.

# Prefixes stripped during normalization (order matters — first match wins)
_STRIP_PREFIXES = [
    ('ValveBiped_', ''), ('Valvebiped_', ''),
    ('Bip1_', 'Bip_'), ('Bip01_', 'Bip_'), ('Bip001_', 'Bip_'),
    ('Bip02_', 'Bip_'), ('Bip01', ''),
    ('Character1_', ''), ('HLP_', ''), ('JD_', ''), ('JU_', ''),
    ('Armature|', ''), ('Bone_', ''), ('Cf_S_', ''), ('Cf_J_', ''),
    ('Joint_', ''), ('Def_C_', ''), ('Def_', ''), ('DEF_', ''),
    ('Chr_', ''), ('B_', ''), ('G_', ''), ('C_', ''),
]
_STRIP_SUFFIXES = [
    ('_Bone', ''), ('_Bn', ''), ('_Le', '_L'), ('_Ri', '_R'), ('_', ''),
]
_REPLACEMENTS = [
    (' ', '_'), ('-', '_'), ('.', '_'), (':', '_'),
    ('____', '_'), ('___', '_'), ('__', '_'),
    ('_Le_', '_L_'), ('_Ri_', '_R_'),
    ('LEFT', 'Left'), ('RIGHT', 'Right'),
]


def _normalize_bone_name(name):
    """Normalize a bone name to a standard form for alias matching.

    Adapted from the CATS Blender Plugin normalization pipeline.
    Capitalizes segments, strips common prefixes/suffixes, normalizes
    separators so bone names from any rig converge to matchable forms.
    """
    # Capitalize first letter of each underscore-delimited segment
    parts = name.split('_')
    name = '_'.join(s[:1].upper() + s[1:] for s in parts)

    # Apply character replacements
    for old, new in _REPLACEMENTS:
        name = name.replace(old, new)

    # Strip known prefixes (repeat to handle chained prefixes like
    # ValveBiped_Bip01_ → strip ValveBiped_ → strip Bip01_)
    for _ in range(2):
        for prefix, repl in _STRIP_PREFIXES:
            if name.startswith(prefix):
                name = repl + name[len(prefix):]
                break

    # Strip known suffixes (first match only)
    for suffix, repl in _STRIP_SUFFIXES:
        if name.endswith(suffix):
            name = name[:-len(suffix)] + repl
            break

    # Remove leading digit segment (e.g. "01_Spine" → "Spine")
    parts = name.split('_')
    if len(parts) > 1 and parts[0].isdigit():
        name = parts[1]

    # Remove trailing S0, _Jnt
    if name.endswith('S0'):
        name = name[:-2]
    if name.endswith('_Jnt'):
        name = name[:-4]

    # Strip mixamorig prefix (after : → _ replacement)
    if name.startswith('Mixamorig_'):
        name = name[len('Mixamorig_'):]

    return name


# ============================================================================
# Comprehensive Bone Alias → XML2 Mapping
# ============================================================================
# Aliases are in NORMALIZED form (post-normalization).
# Uses \L / \Left templates expanded into L/R and Left/Right variants.
# Derived from CATS Blender Plugin bone tables + Unity/VRChat/MMD conventions.

def _expand_sides(template_key, aliases, xml2_l, xml2_r):
    """Expand \\L / \\Left templates into Left/Right alias entries."""
    entries = []
    for side_data in [('Left', 'L', xml2_l), ('Right', 'R', xml2_r)]:
        full, short, xml2 = side_data
        expanded_aliases = []
        for a in aliases:
            expanded_aliases.append(
                a.replace('\\Left', full).replace('\\left', full.lower())
                 .replace('\\Lf', full[:1]).replace('\\L', short).replace('\\l', short.lower()))
        entries.append((expanded_aliases, xml2))
    return entries


# Template aliases for bones with left/right sides.
# Each entry: (CATS template key, [normalized aliases with \L/\Left], xml2_L, xml2_R)
_SIDED_ALIASES = [
    # Shoulder / Clavicle
    ('\\Left_Shoulder', [
        '\\Left_Shoulder', '\\LeftShoulder', 'Shoulder_\\L', '\\LShoulder',
        '\\L_Shoulder', 'Bip_\\L_Clavicle', '\\L_Clavicle', '\\Left_Clavicle',
        '\\LCollar', '\\LeftCollar', '\\L_Collar', 'Clavicle_\\L',
        'ShoulderArm_\\L', 'Bip_Collar_\\L', 'Shol_\\L',
        'J_Bip_\\L_Shoulder', 'Bip_Clavicle_\\L', 'Clav_\\L',
        '\\LClavicle', 'Bip_\\L_Arm', 'J_\\L_Collar', 'J_\\L_Shoulder',
        'Collarbone_\\L', 'J_Clavicle_\\L', '\\L_Clav',
    ], 'Bip01 L Clavicle', 'Bip01 R Clavicle'),

    # Upper arm
    ('\\Left_Arm', [
        '\\Left_Arm', '\\LeftArm', 'Arm_\\L', '\\LArm',
        'Bip_\\L_UpperArm', 'Upper_Arm_\\L', 'UpperArm_\\L',
        '\\Left_Upper_Arm', '\\L_UpperArm', '\\LeftUpArm',
        'Uparm_\\L', '\\L_Arm', 'Arm_Upper_\\L',
        'J_Bip_\\L_UpperArm', '\\LUpperArm', '\\LShldr',
        'Bip_UpperArm_\\L', '\\L_Shldr', 'Arm_Stretch_\\L',
        'J_Shoulder_\\L', '\\L_Uparm', 'Bip_\\L_Arm1',
    ], 'Bip01 L UpperArm', 'Bip01 R UpperArm'),

    # Forearm / Elbow
    ('\\Left_Elbow', [
        '\\Left_Elbow', '\\LeftElbow', 'Elbow_\\L', '\\L_Elbow',
        'Bip_\\L_ForeArm', 'Fore_Arm_\\L', 'ForeArm_\\L',
        '\\LForeArm', '\\L_ForeArm', '\\LeftLowArm', '\\Left_Forearm',
        '\\LeftForeArm', 'Lower_Arm_\\L', 'LowerArm_\\L',
        'Arm_Lower_\\L', 'J_Bip_\\L_LowerArm', 'Bip_Forearm_\\L',
        '\\LElbow', 'Forearm_Stretch_\\L', 'J_Elbow_\\L',
        '\\L_LowerArm', '\\L_Forearm', 'Bip_\\L_Arm2',
        'Bip_LowerArm_\\L', '\\L_Hiji',
    ], 'Bip01 L Forearm', 'Bip01 R Forearm'),

    # Hand / Wrist
    ('\\Left_Wrist', [
        '\\Left_Wrist', '\\LeftWrist', 'Wrist_\\L', '\\LHand',
        'Bip_\\L_Hand', 'Hand_\\L', '\\LeftHand', '\\Left_Hand',
        '\\L_Hand', 'Palm_\\L', 'J_Bip_\\L_Hand', 'Bip_Hand_\\L',
        'J_\\L_Wrist', '\\L_Wrist', '\\LWrist', 'J_Te_\\L', 'J_Wrist_\\L',
    ], 'Bip01 L Hand', 'Bip01 R Hand'),

    # Thigh / Upper leg
    ('\\Left_Leg', [
        '\\Left_Leg', '\\Left_Foot', '\\LeftLeg', 'Leg_\\L',
        'Bip_\\L_Thigh', 'Upper_Leg_\\L', '\\LThigh', 'Thigh_\\L',
        '\\L_Thigh', '\\LeftUpLeg', '\\Left_Thigh', '\\L_Hip',
        'UpperLeg_\\L', 'J_Bip_\\L_UpperLeg', 'Bip_Thigh_\\L',
        'Bip_Hip_\\L', 'Thigh_Stretch_\\L', 'J_Hip_\\L',
        '\\LUpLeg', '\\L_UpperLeg', 'Bip_\\L_Leg', '\\L_Leg',
        '\\LeftHip', '\\L_Momo',
    ], 'Bip01 L Thigh', 'Bip01 R Thigh'),

    # Calf / Knee / Lower leg
    ('\\Left_Knee', [
        '\\Left_Knee', '\\LeftKnee', 'Knee_\\L', '\\LLeg',
        '\\LShin', 'Shin_\\L', '\\L_Calf', 'Calf_\\L',
        '\\LeftLowLeg', '\\Left_Shin', 'Lower_Leg_\\L', 'LowerLeg_\\L',
        'Bip_\\L_Calf', 'J_Bip_\\L_LowerLeg', 'Bip_Leg_\\L',
        'Leg_Stretch_\\L', 'J_Knee_\\L', '\\LCalf', '\\L_LowerLeg',
        'Bip_Knee_\\L', '\\L_Sune', 'Bip_\\L_Leg1',
    ], 'Bip01 L Calf', 'Bip01 R Calf'),

    # Foot / Ankle
    ('\\Left_Ankle', [
        '\\Left_Ankle', '\\LeftAnkle', 'Ankle_\\L', '\\L_Ankle',
        'Bip_\\L_Foot', 'Foot_\\L', '\\LFoot', '\\L_Foot',
        '\\LeftFoot', 'J_Bip_\\L_Foot', 'Bip_Foot_\\L',
        '\\LAnkle', '\\Left_Heel', 'J_Ankle_\\L',
    ], 'Bip01 L Foot', 'Bip01 R Foot'),

    # Toe
    ('\\Left_Toe', [
        '\\Left_Toe', '\\Left_Toes', '\\LeftToe', 'LegTip_\\L',
        'Bip_\\L_Toe0', 'Toe_\\L', '\\LToe', '\\L_Toe',
        '\\LeftToeBase', 'Toes_\\L', 'J_Bip_\\L_ToeBase',
        'Bip_Toe_\\L', 'J_Ball_\\L', '\\LToeBase', '\\LToe0',
        '\\L_Toes', '\\L_Toe0', 'ToeTip_\\L',
    ], 'Bip01 L Toe0', 'Bip01 R Toe0'),

    # Thumb (proximal → Finger0, intermediate → Finger01)
    ('Thumb0_\\L', [
        'Thumb0_\\L', 'Thumb_0_\\L', '\\LThumb1', 'Thumb1_\\L',
        'Finger0_\\L', 'Bip_\\L_Finger0', 'J_Bip_\\L_Thumb1',
        'ThumbFinger1_\\L', 'Thumb_Proximal_\\L', 'Thumb_A_\\L',
        '\\LFingerThumb1',
    ], 'Bip01 L Finger0', 'Bip01 R Finger0'),
    ('Thumb1_\\L', [
        'Thumb1_\\L', 'Thumb_1_\\L', '\\LThumb2', 'Thumb2_\\L',
        'Finger01_\\L', 'Bip_\\L_Finger01', 'J_Bip_\\L_Thumb2',
        'ThumbFinger2_\\L', 'Thumb_Intermediate_\\L', 'Thumb_B_\\L',
        '\\LFingerThumb2',
    ], 'Bip01 L Finger01', 'Bip01 R Finger01'),

    # Middle finger (proximal → Finger1, intermediate → Finger11)
    ('MiddleFinger1_\\L', [
        'MiddleFinger1_\\L', 'Middle1_\\L', 'Middle_Proximal_\\L',
        'Bip_\\L_Finger1', 'J_Bip_\\L_Middle1', 'MiddleFinger_A_\\L',
        '\\LFingerMiddle1', 'Mid1_\\L',
    ], 'Bip01 L Finger1', 'Bip01 R Finger1'),
    ('MiddleFinger2_\\L', [
        'MiddleFinger2_\\L', 'Middle2_\\L', 'Middle_Intermediate_\\L',
        'Bip_\\L_Finger11', 'J_Bip_\\L_Middle2', 'MiddleFinger_B_\\L',
        '\\LFingerMiddle2', 'Mid2_\\L',
    ], 'Bip01 L Finger11', 'Bip01 R Finger11'),
]

# Center (non-sided) aliases → XML2
_CENTER_ALIASES = {
    # Hips / Pelvis
    'Bip01 Pelvis': [
        'Hips', 'LowerBody', 'Lower_Body', 'Pelvis', 'Bip_Pelvis',
        'Hip', 'Waist', 'Root', 'J_Bip_C_Hips', 'Root_X',
        'HipN', 'J_Kosi', 'Kosi', 'J_Hip',
    ],
    # Spine
    'Bip01 Spine': [
        'Spine', 'UpperBody', 'Upper_Body', 'Bip_Spine',
        'Spine_Lower', 'Abdomen', 'Spine0', 'SpineA', 'SpA',
        'J_Bip_C_Spine', 'BODY1', 'WaistN', 'Torso_1',
    ],
    # Chest (Spine1)
    'Bip01 Spine1': [
        'Chest', 'Bip_Spine1', 'Bip_Chest', 'Spine_Upper',
        'Spine1', 'SpineB', 'SpB', 'J_Bip_C_Chest', 'BODY2',
        'BustN', 'Bust', 'Ribs', 'Torso_2', 'ChestLower',
    ],
    # Upper Chest (Spine2)
    'Bip01 Spine2': [
        'Upper_Chest', 'UpperChest', 'Bip_Spine2',
        'Spine2', 'SpineC', 'SpC', 'J_Bip_C_UpperChest',
        'Chest_Def', 'SpineTop',
    ],
    # Neck
    'Bip01 Neck': [
        'Neck', 'Bip_Neck', 'Bip_Neck1', 'Head_Neck_Lower',
        'Head_Neck', 'J_Bip_C_Neck', 'NECK', 'NeckN', 'Kubi',
        'J_Kubi', 'NeckLower', 'Neck_X',
    ],
    # Head
    'Bip01 Head': [
        'Head', 'Bip_Head', 'Bip_Head1', 'J_Bip_C_Head',
        'HEAD', 'HeadN', 'Head_01', 'J_Head', 'Head_X', 'J_Kao',
    ],
}

# Build the flat lookup: normalized_alias (lowercase) → XML2 name
ALIAS_TO_XML2 = {}

# Add center aliases
for xml2_name, aliases in _CENTER_ALIASES.items():
    for alias in aliases:
        ALIAS_TO_XML2[alias.lower()] = xml2_name

# Add sided aliases (expand \L/\Left templates)
for _, aliases, xml2_l, xml2_r in _SIDED_ALIASES:
    for entry_list in _expand_sides(_, aliases, xml2_l, xml2_r):
        expanded_aliases, xml2_name = entry_list
        for alias in expanded_aliases:
            ALIAS_TO_XML2[alias.lower()] = xml2_name

# ---- Unity Humanoid camelCase names (not covered by CATS templates) ----
_UNITY_DIRECT = {
    # Spine
    'upperchest': 'Bip01 Spine2',
    # Arms
    'leftshoulder': 'Bip01 L Clavicle', 'rightshoulder': 'Bip01 R Clavicle',
    'leftupperarm': 'Bip01 L UpperArm', 'rightupperarm': 'Bip01 R UpperArm',
    'leftlowerarm': 'Bip01 L Forearm', 'rightlowerarm': 'Bip01 R Forearm',
    'leftforearm': 'Bip01 L Forearm', 'rightforearm': 'Bip01 R Forearm',
    'lefthand': 'Bip01 L Hand', 'righthand': 'Bip01 R Hand',
    # Legs
    'leftupperleg': 'Bip01 L Thigh', 'rightupperleg': 'Bip01 R Thigh',
    'leftlowerleg': 'Bip01 L Calf', 'rightlowerleg': 'Bip01 R Calf',
    'leftfoot': 'Bip01 L Foot', 'rightfoot': 'Bip01 R Foot',
    'lefttoes': 'Bip01 L Toe0', 'righttoes': 'Bip01 R Toe0',
    'lefttoebase': 'Bip01 L Toe0', 'righttoebase': 'Bip01 R Toe0',
    'lefttoe': 'Bip01 L Toe0', 'righttoe': 'Bip01 R Toe0',
    # Fingers (thumb → Finger0/01, middle → Finger1/11)
    'leftthumbproximal': 'Bip01 L Finger0',
    'rightthumbproximal': 'Bip01 R Finger0',
    'leftthumbintermediate': 'Bip01 L Finger01',
    'rightthumbintermediate': 'Bip01 R Finger01',
    'leftmiddleproximal': 'Bip01 L Finger1',
    'rightmiddleproximal': 'Bip01 R Finger1',
    'leftmiddleintermediate': 'Bip01 L Finger11',
    'rightmiddleintermediate': 'Bip01 R Finger11',
}
for _k, _v in _UNITY_DIRECT.items():
    ALIAS_TO_XML2.setdefault(_k, _v)

# Also add underscore variants (left_upper_arm, etc.)
for _k, _v in list(_UNITY_DIRECT.items()):
    # Insert underscores before uppercase letters
    import re
    _under = re.sub(r'([a-z])([A-Z])', r'\1_\2', _k).lower()
    ALIAS_TO_XML2.setdefault(_under, _v)

# ---- Descriptive naming convention (e.g., "arm left shoulder 1") ----
# After normalization, spaces → underscores, first letter capitalized per segment.
# "arm left shoulder 1" → "arm_left_shoulder_1" (lowered).
_DESCRIPTIVE_DIRECT = {
    # Center bones
    'root pelvis': 'Bip01 Pelvis',
    'root_pelvis': 'Bip01 Pelvis',
    'spine lower': 'Bip01 Spine',
    'spine_lower': 'Bip01 Spine',
    'spine middle': 'Bip01 Spine1',
    'spine_middle': 'Bip01 Spine1',
    'spine upper': 'Bip01 Spine2',
    'spine_upper': 'Bip01 Spine2',
    'head neck lower': 'Bip01 Neck',
    'head_neck_lower': 'Bip01 Neck',
    'head neck upper': 'Bip01 Head',
    'head_neck_upper': 'Bip01 Head',
    # Arms (sided)
    'arm left shoulder 1': 'Bip01 L Clavicle',
    'arm_left_shoulder_1': 'Bip01 L Clavicle',
    'arm right shoulder 1': 'Bip01 R Clavicle',
    'arm_right_shoulder_1': 'Bip01 R Clavicle',
    'arm left shoulder 2': 'Bip01 L UpperArm',
    'arm_left_shoulder_2': 'Bip01 L UpperArm',
    'arm right shoulder 2': 'Bip01 R UpperArm',
    'arm_right_shoulder_2': 'Bip01 R UpperArm',
    'arm left elbow': 'Bip01 L Forearm',
    'arm_left_elbow': 'Bip01 L Forearm',
    'arm right elbow': 'Bip01 R Forearm',
    'arm_right_elbow': 'Bip01 R Forearm',
    'arm left wrist': 'Bip01 L Hand',
    'arm_left_wrist': 'Bip01 L Hand',
    'arm right wrist': 'Bip01 R Hand',
    'arm_right_wrist': 'Bip01 R Hand',
    # Legs (sided)
    'leg left thigh': 'Bip01 L Thigh',
    'leg_left_thigh': 'Bip01 L Thigh',
    'leg right thigh': 'Bip01 R Thigh',
    'leg_right_thigh': 'Bip01 R Thigh',
    'leg left knee': 'Bip01 L Calf',
    'leg_left_knee': 'Bip01 L Calf',
    'leg right knee': 'Bip01 R Calf',
    'leg_right_knee': 'Bip01 R Calf',
    'leg left ankle': 'Bip01 L Foot',
    'leg_left_ankle': 'Bip01 L Foot',
    'leg right ankle': 'Bip01 R Foot',
    'leg_right_ankle': 'Bip01 R Foot',
    'leg left toes': 'Bip01 L Toe0',
    'leg_left_toes': 'Bip01 L Toe0',
    'leg right toes': 'Bip01 R Toe0',
    'leg_right_toes': 'Bip01 R Toe0',
    # Thumb (finger 1): proximal → Finger0, intermediate → Finger01
    'arm left finger 1a': 'Bip01 L Finger0',
    'arm_left_finger_1a': 'Bip01 L Finger0',
    'arm right finger 1a': 'Bip01 R Finger0',
    'arm_right_finger_1a': 'Bip01 R Finger0',
    'arm left finger 1b': 'Bip01 L Finger01',
    'arm_left_finger_1b': 'Bip01 L Finger01',
    'arm right finger 1b': 'Bip01 R Finger01',
    'arm_right_finger_1b': 'Bip01 R Finger01',
    # Middle finger (finger 3): proximal → Finger1, intermediate → Finger11
    'arm left finger 3a': 'Bip01 L Finger1',
    'arm_left_finger_3a': 'Bip01 L Finger1',
    'arm right finger 3a': 'Bip01 R Finger1',
    'arm_right_finger_3a': 'Bip01 R Finger1',
    'arm left finger 3b': 'Bip01 L Finger11',
    'arm_left_finger_3b': 'Bip01 L Finger11',
    'arm right finger 3b': 'Bip01 R Finger11',
    'arm_right_finger_3b': 'Bip01 R Finger11',
}
for _k, _v in _DESCRIPTIVE_DIRECT.items():
    ALIAS_TO_XML2.setdefault(_k.lower(), _v)

# Descriptive naming: merge targets (weights fold into nearest XML2 bone)
_DESCRIPTIVE_MERGE = {}
for _side in ('left', 'right'):
    _s = 'L' if _side == 'left' else 'R'
    # Thumb distal → Finger01
    for _var in (f'arm {_side} finger 1c', f'arm_{_side}_finger_1c'):
        _DESCRIPTIVE_MERGE[_var] = f'Bip01 {_s} Finger01'
    # Middle finger metacarpal + distal → Finger1 / Finger11
    for _var in (f'arm {_side} finger 3', f'arm_{_side}_finger_3'):
        _DESCRIPTIVE_MERGE[_var] = f'Bip01 {_s} Finger1'
    for _var in (f'arm {_side} finger 3c', f'arm_{_side}_finger_3c'):
        _DESCRIPTIVE_MERGE[_var] = f'Bip01 {_s} Finger11'
    # Index (finger 2), ring (finger 4), pinky (finger 5) → merge to Finger1/Finger11
    for _fnum in ('2', '4', '5'):
        for _var in (f'arm {_side} finger {_fnum}', f'arm_{_side}_finger_{_fnum}',
                     f'arm {_side} finger {_fnum}a', f'arm_{_side}_finger_{_fnum}a'):
            _DESCRIPTIVE_MERGE[_var] = f'Bip01 {_s} Finger1'
        for _var in (f'arm {_side} finger {_fnum}b', f'arm_{_side}_finger_{_fnum}b',
                     f'arm {_side} finger {_fnum}c', f'arm_{_side}_finger_{_fnum}c'):
            _DESCRIPTIVE_MERGE[_var] = f'Bip01 {_s} Finger11'
    # Thigh adjustment bones → merge to Thigh
    for _var in (f'leg {_side} thigh adj. 1', f'leg_{_side}_thigh_adj._1',
                 f'leg {_side} thigh adj 1', f'leg_{_side}_thigh_adj_1'):
        _DESCRIPTIVE_MERGE[_var] = f'Bip01 {_s} Thigh'
    # Elbow adjustment → merge to Forearm
    for _var in (f'unused arm {_side} elbow adj. 2', f'unused_arm_{_side}_elbow_adj._2',
                 f'unused arm {_side} elbow adj 2', f'unused_arm_{_side}_elbow_adj_2'):
        _DESCRIPTIVE_MERGE[_var] = f'Bip01 {_s} Forearm'

# Facial bones → merge to Head (base names + left/right variants)
for _face_base in ['head eyeball', 'head cheek', 'head eyebrow', 'head eyelid',
                   'head lip', 'head nose', 'head jaw', 'head lip upper',
                   'head lip lower', 'head lip corner']:
    _DESCRIPTIVE_MERGE[_face_base] = 'Bip01 Head'
    for _side_suffix in (' left', ' right', '_left', '_right'):
        _DESCRIPTIVE_MERGE[_face_base + _side_suffix] = 'Bip01 Head'

# (Descriptive merge targets registered later, after MERGE_WEIGHT_TARGETS is defined)

# ---- Legacy compat: keep UNITY_TO_XML2 as an alias for the new mapping ----
UNITY_TO_XML2 = ALIAS_TO_XML2

# ============================================================================
# FX Bone Aliases (MUA effect attachment bones)
# ============================================================================
# Aliases for common modder naming variations → standard MUA FX bone names.
# Stored in separate dict (not ALIAS_TO_XML2) since FX bones only exist in MUA.
ALIAS_TO_MUA_FX = {}
_FX_ALIAS_TABLE = {
    'Gun1':  ['gun1', 'gun_1', 'gun01', 'weapon_r', 'weapon_right'],
    'fx01':  ['fx1', 'fx_01', 'fx_1', 'effect01', 'effect1'],
    'fx02':  ['fx2', 'fx_02', 'fx_2', 'effect02', 'effect2'],
    'fx03':  ['fx3', 'fx_03', 'fx_3', 'effect03', 'effect3'],
    'fx04':  ['fx4', 'fx_04', 'fx_4', 'effect04', 'effect4'],
    'fx05':  ['fx5', 'fx_05', 'fx_5', 'effect05', 'effect5'],
    'fx06':  ['fx6', 'fx_06', 'fx_6', 'effect06', 'effect6'],
    'fx07':  ['fx7', 'fx_07', 'fx_7', 'effect07', 'effect7'],
    'fx08':  ['fx8', 'fx_08', 'fx_8', 'effect08', 'effect8'],
    'fx09':  ['fx9', 'fx_09', 'fx_9', 'effect09', 'effect9'],
    'fx10':  ['fx_10', 'effect10'],
    'fx11':  ['fx_11', 'effect11'],
    'fx12':  ['fx_12', 'effect12'],
    'fx13':  ['fx_13', 'effect13'],
}
for _fx_std, _fx_aliases in _FX_ALIAS_TABLE.items():
    # Map the standard name itself
    ALIAS_TO_MUA_FX[_fx_std.lower()] = _fx_std
    for _alias in _fx_aliases:
        ALIAS_TO_MUA_FX[_alias.lower()] = _fx_std


# ============================================================================
# Merge Weight Targets (bones that merge into XML2 targets)
# ============================================================================
MERGE_WEIGHT_TARGETS = {}

_MERGE_SIDED = [
    # Thumb distal → Finger01
    ('Thumb2_\\L', [
        'Thumb2_\\L', 'Thumb_2_\\L', '\\LThumb3', 'Thumb3_\\L',
        'Thumb_Distal_\\L', 'ThumbFinger3_\\L', 'Thumb_C_\\L',
        'J_Bip_\\L_Thumb3', 'Bip_\\L_Finger02',
    ], 'Bip01 L Finger01', 'Bip01 R Finger01'),

    # Index finger (proximal/intermediate/distal) → Finger1 / Finger11
    ('IndexFinger1_\\L', [
        'IndexFinger1_\\L', 'Index1_\\L', 'Fore1_\\L',
        'Index_Proximal_\\L', 'J_Bip_\\L_Index1',
        'Bip_\\L_Finger1', '\\LFingerIndex1',
    ], 'Bip01 L Finger1', 'Bip01 R Finger1'),
    ('IndexFinger2_\\L', [
        'IndexFinger2_\\L', 'IndexFinger3_\\L',
        'Index2_\\L', 'Index3_\\L', 'Fore2_\\L', 'Fore3_\\L',
        'Index_Intermediate_\\L', 'Index_Distal_\\L',
        'J_Bip_\\L_Index2', 'J_Bip_\\L_Index3',
    ], 'Bip01 L Finger11', 'Bip01 R Finger11'),

    # Ring finger → Finger1 / Finger11
    ('RingFinger1_\\L', [
        'RingFinger1_\\L', 'Third1_\\L', 'Ring1_\\L',
        'Ring_Proximal_\\L', 'J_Bip_\\L_Ring1',
    ], 'Bip01 L Finger1', 'Bip01 R Finger1'),
    ('RingFinger2_\\L', [
        'RingFinger2_\\L', 'RingFinger3_\\L',
        'Third2_\\L', 'Third3_\\L', 'Ring2_\\L', 'Ring3_\\L',
        'Ring_Intermediate_\\L', 'Ring_Distal_\\L',
    ], 'Bip01 L Finger11', 'Bip01 R Finger11'),

    # Little finger → Finger1 / Finger11
    ('LittleFinger1_\\L', [
        'LittleFinger1_\\L', 'Little1_\\L', 'Pinky1_\\L',
        'Little_Proximal_\\L', 'J_Bip_\\L_Little1',
    ], 'Bip01 L Finger1', 'Bip01 R Finger1'),
    ('LittleFinger2_\\L', [
        'LittleFinger2_\\L', 'LittleFinger3_\\L',
        'Little2_\\L', 'Little3_\\L', 'Pinky2_\\L', 'Pinky3_\\L',
        'Little_Intermediate_\\L', 'Little_Distal_\\L',
    ], 'Bip01 L Finger11', 'Bip01 R Finger11'),

    # Middle finger distal → Finger11
    ('MiddleFinger3_\\L', [
        'MiddleFinger3_\\L', 'Middle3_\\L', 'Mid3_\\L',
        'Middle_Distal_\\L', 'J_Bip_\\L_Middle3',
    ], 'Bip01 L Finger11', 'Bip01 R Finger11'),
]

# Expand sided merge targets
for _, aliases, xml2_l, xml2_r in _MERGE_SIDED:
    for entry_list in _expand_sides(_, aliases, xml2_l, xml2_r):
        expanded_aliases, xml2_name = entry_list
        for alias in expanded_aliases:
            MERGE_WEIGHT_TARGETS[alias.lower()] = xml2_name

# Non-sided merge targets
for alias in ['LeftEye', 'Left_Eye', 'RightEye', 'Right_Eye',
              'Eye_L', 'Eye_R', 'Jaw', 'jaw']:
    MERGE_WEIGHT_TARGETS[alias.lower()] = 'Bip01 Head'

# Unity Humanoid camelCase merge targets
_UNITY_MERGE_DIRECT = {
    'leftthumbdistal': 'Bip01 L Finger01',
    'rightthumbdistal': 'Bip01 R Finger01',
    'leftindexproximal': 'Bip01 L Finger1',
    'rightindexproximal': 'Bip01 R Finger1',
    'leftindexintermediate': 'Bip01 L Finger11',
    'rightindexintermediate': 'Bip01 R Finger11',
    'leftindexdistal': 'Bip01 L Finger11',
    'rightindexdistal': 'Bip01 R Finger11',
    'leftmiddledistal': 'Bip01 L Finger11',
    'rightmiddledistal': 'Bip01 R Finger11',
    'leftringproximal': 'Bip01 L Finger1',
    'rightringproximal': 'Bip01 R Finger1',
    'leftringintermediate': 'Bip01 L Finger11',
    'rightringintermediate': 'Bip01 R Finger11',
    'leftringdistal': 'Bip01 L Finger11',
    'rightringdistal': 'Bip01 R Finger11',
    'leftlittleproximal': 'Bip01 L Finger1',
    'rightlittleproximal': 'Bip01 R Finger1',
    'leftlittleintermediate': 'Bip01 L Finger11',
    'rightlittleintermediate': 'Bip01 R Finger11',
    'leftlittledistal': 'Bip01 L Finger11',
    'rightlittledistal': 'Bip01 R Finger11',
}
for _k, _v in _UNITY_MERGE_DIRECT.items():
    MERGE_WEIGHT_TARGETS.setdefault(_k, _v)

# Register descriptive naming merge targets (defined earlier, registered here)
for _k, _v in _DESCRIPTIVE_MERGE.items():
    MERGE_WEIGHT_TARGETS.setdefault(_k.lower(), _v)

# ---- CATS-ported tables (rig_bone_tables.py) ----
# Full bone dictionary from the CATS Blender Plugin, remapped to XML2 targets.
# CATS_RENAME_LOOKUP carries per-alias priority: when several bones alias to
# the same XML2 target, the bone matching the highest-priority (lowest value)
# alias wins the rename; the losers become weight-merge sources.
from . import rig_bone_tables as _bone_tables

CATS_RENAME_LOOKUP = _bone_tables.build_rename_lookup()
SPINE_ALIAS_SET = _bone_tables.build_spine_alias_set()

for _k, _v in _bone_tables.build_merge_lookup().items():
    MERGE_WEIGHT_TARGETS.setdefault(_k, _v)

# Make CATS aliases visible to legacy _lookup_bone callers (profile detection)
for _k, (_v, _p) in CATS_RENAME_LOOKUP.items():
    ALIAS_TO_XML2.setdefault(_k, _v)

# Bones the weight-merge ancestor fallback must never target (non-deforming)
_NON_DEFORM_TARGETS = {"", "Bone_000", "Bip01", "Motion"} | MUA_FX_BONE_NAMES

# Mixamo prefix (kept for legacy detection)
MIXAMO_PREFIX = "mixamorig:"

# ============================================================================
# Native XML2 Bone Rotations (extracted from 0601.igb)
# ============================================================================
# Quaternions (w, x, y, z) in ALCHEMY convention (conjugate of Blender).
# These represent each deforming bone's world-space orientation in the native
# XML2 Biped skeleton.  Game animations are authored against these, so
# converted rigs MUST use them in inv_joint_matrices — but use the CUSTOM
# model's bone positions so the skeleton matches the mesh proportions.
#
# CRITICAL: These are Alchemy convention.  Must conjugate before passing
# to Blender's Quaternion.to_matrix() to get the correct rotation.
NATIVE_BONE_ROTATIONS = {
    "Bip01 Pelvis":     ( 0.500001,  0.499999,  0.500000,  0.499999),
    "Bip01 Spine":      ( 0.494167,  0.494167,  0.505766,  0.505766),
    "Bip01 Spine1":     ( 0.483543,  0.483543,  0.515932,  0.515932),
    "Bip01 Spine2":     ( 0.483543,  0.483543,  0.515932,  0.515932),
    "Bip01 Neck":       ( 0.546961,  0.546962,  0.448144,  0.448144),
    "Bip01 Head":       ( 0.499999,  0.500000,  0.500000,  0.500001),
    "Bip01 Ponytail1":  ( 0.435534,  0.435537, -0.557052, -0.557055),
    "Bip01 Ponytail11": ( 0.505759,  0.505761, -0.494172, -0.494174),
    "Bip01 L Clavicle": ( 0.681713,  0.039113,  0.008246, -0.730527),
    "Bip01 L UpperArm": ( 0.688073, -0.006872,  0.006476, -0.725580),
    "Bip01 L Forearm":  ( 0.704559, -0.007019,  0.006316, -0.709582),
    "Bip01 L Hand":     ( 0.503358,  0.493035,  0.506019, -0.497485),
    "Bip01 L Finger0":  ( 0.870648,  0.015735, -0.020128, -0.491242),
    "Bip01 L Finger01": ( 0.675922,  0.008735, -0.024009, -0.736531),
    "Bip01 L Finger1":  ( 0.493886,  0.502507,  0.497015, -0.506498),
    "Bip01 L Finger11": ( 0.493886,  0.502507,  0.497015, -0.506498),
    "Bip01 R Clavicle": ( 0.039113,  0.681714, -0.730526,  0.008246),
    "Bip01 R UpperArm": ( 0.006872, -0.688074,  0.725579, -0.006476),
    "Bip01 R Forearm":  ( 0.007019, -0.704561,  0.709581, -0.006316),
    "Bip01 R Hand":     ( 0.493036,  0.503359, -0.497484,  0.506018),
    "Bip01 R Finger0":  ( 0.015735,  0.870649, -0.491241, -0.020128),
    "Bip01 R Finger01": ( 0.008735,  0.675923, -0.736529, -0.024009),
    "Bip01 R Finger1":  ( 0.502508,  0.493887, -0.506497,  0.497014),
    "Bip01 R Finger11": ( 0.502508,  0.493887, -0.506497,  0.497014),
    "Bip01 L Thigh":    ( 0.527514, -0.477845, -0.594117,  0.374726),
    "Bip01 L Calf":     ( 0.494748, -0.427121, -0.631568,  0.417031),
    "Bip01 L Foot":     ( 0.574484, -0.412272, -0.574484,  0.412272),
    "Bip01 L Toe0":     ( 0.697742, -0.697742, -0.114702, -0.114702),
    "Bip01 R Thigh":    ( 0.477846, -0.527513, -0.374727,  0.594116),
    "Bip01 R Calf":     ( 0.427122, -0.494747, -0.417032,  0.631567),
    "Bip01 R Foot":     ( 0.412273, -0.574483, -0.412273,  0.574483),
    "Bip01 R Toe0":     ( 0.697742, -0.697742,  0.114700,  0.114700),
}

# ============================================================================
# Native XML2 Inverse Joint Matrices (extracted from 0601.igb) — reference
# ============================================================================
# Kept for reference/debugging. These are the exact values from 0601.igb.
# The converter computes custom inv_joint_matrices from native rotations +
# custom bone positions instead of using these directly.
NATIVE_INV_JOINT_MATRICES = {
    "Bip01 Pelvis": [0.00000000, 1.00000000, -0.00000339, 0.00000000, -0.00000139, 0.00000339, 1.00000000, 0.00000000, 1.00000000, -0.00000000, 0.00000139, 0.00000000, -41.81953049, -0.68984902, -0.00005566, 1.00000000],
    "Bip01 Spine": [-0.02319670, 0.99973089, 0.00000077, 0.00000000, -0.00000009, -0.00000078, 0.99999994, 0.00000000, 0.99973089, 0.02319670, 0.00000011, 0.00000000, -42.10088348, -1.82178116, -0.00000367, 1.00000000],
    "Bip01 Spine1": [-0.06474347, 0.99790192, 0.00000077, 0.00000000, -0.00000017, -0.00000078, 1.00000000, 0.00000000, 0.99790192, 0.06474347, 0.00000022, 0.00000000, -45.47655869, -3.71112752, -0.00000891, 1.00000000],
    "Bip01 Spine2": [-0.06474347, 0.99790192, 0.00000077, 0.00000000, -0.00000017, -0.00000078, 1.00000000, 0.00000000, 0.99790192, 0.06474347, 0.00000022, 0.00000000, -51.36871719, -3.70284390, -0.00000889, 1.00000000],
    "Bip01 Neck": [0.19666788, 0.98047018, 0.00000072, 0.00000000, 0.00000035, -0.00000080, 0.99999994, 0.00000000, 0.98047018, -0.19666788, -0.00000050, 0.00000000, -60.61355972, 12.47044086, 0.00003597, 1.00000000],
    "Bip01 Head": [-0.00000139, 1.00000000, 0.00000077, 0.00000000, -0.00000004, -0.00000077, 1.00000000, 0.00000000, 1.00000000, 0.00000139, 0.00000004, 0.00000000, -64.28015137, -0.17490363, 0.00000089, 1.00000000],
    "Bip01 Ponytail1": [-0.24123503, -0.97046673, -0.00000469, 0.00000000, -0.00000044, -0.00000473, 1.00000000, 0.00000000, -0.97046673, 0.24123503, 0.00000071, 0.00000000, 63.14309311, -18.90842247, -0.00005206, 1.00000000],
    "Bip01 Ponytail11": [0.02317266, -0.99973148, -0.00000477, 0.00000000, 0.00000009, -0.00000477, 1.00000000, 0.00000000, -0.99973148, -0.02317266, -0.00000002, 0.00000000, 56.71431351, -4.09063768, -0.00001097, 1.00000000],
    "Bip01 L Clavicle": [-0.06747600, -0.99537432, -0.06838905, 0.00000000, 0.99666429, -0.07039971, 0.04128037, 0.00000000, -0.04590398, -0.06537550, 0.99680436, 0.00000000, 2.44098711, 4.26298046, -59.22934723, 1.00000000],
    "Bip01 L UpperArm": [-0.05301731, -0.99859303, 0.00106044, 0.00000000, 0.99841511, -0.05302788, -0.01885467, 0.00000000, 0.01888438, 0.00005913, 0.99982166, 0.00000000, -7.28284168, 0.28444195, -59.03387833, 1.00000000],
    "Bip01 L Forearm": [-0.00709340, -0.99997425, 0.00106044, 0.00000000, 0.99979705, -0.00711213, -0.01885467, 0.00000000, 0.01886173, 0.00092648, 0.99982160, 0.00000000, -17.51569366, -0.52064860, -59.03387451, 1.00000000],
    "Bip01 L Hand": [-0.00709340, -0.00185669, -0.99997306, 0.00000000, 0.99979705, 0.01884900, -0.00712714, 0.00000000, 0.01886173, -0.99982053, 0.00172261, 0.00000000, -26.03530884, 59.03343964, -0.56765550, 1.00000000],
    "Bip01 L Finger0": [0.51655138, -0.85603207, 0.01958970, 0.00000000, 0.85476512, 0.51686633, 0.04717601, 0.00000000, -0.05050867, -0.00762430, 0.99869418, 0.00000000, -20.10997581, -11.68594742, -59.72957230, 1.00000000],
    "Bip01 L Finger01": [-0.08610736, -0.99609321, 0.01958970, 0.00000000, 0.99525416, -0.08510716, 0.04717601, 0.00000000, -0.04532391, 0.02355844, 0.99869412, 0.00000000, -24.77327156, 3.57737637, -59.72956467, 1.00000000],
    "Bip01 L Finger1": [-0.00712774, -0.00079757, -0.99997425, 0.00000000, 0.99981070, -0.01810670, -0.00711213, 0.00000000, -0.01810056, -0.99983561, 0.00092648, 0.00000000, -27.42947769, 60.08722687, -0.18117857, 1.00000000],
    "Bip01 L Finger11": [-0.00712774, -0.00079757, -0.99997425, 0.00000000, 0.99981070, -0.01810670, -0.00711213, 0.00000000, -0.01810056, -0.99983561, 0.00092648, 0.00000000, -29.26063347, 60.08722687, -0.18117855, 1.00000000],
    "Bip01 R Clavicle": [-0.06747202, -0.99537456, 0.06838889, 0.00000000, -0.99666458, 0.07039573, 0.04128073, 0.00000000, -0.04590407, -0.06537549, -0.99680430, 0.00000000, 2.44098473, 4.26298046, 59.22934341, 1.00000000],
    "Bip01 R UpperArm": [-0.05301315, -0.99859321, -0.00106036, 0.00000000, -0.99841529, 0.05302371, -0.01885459, 0.00000000, 0.01888429, 0.00005914, -0.99982160, 0.00000000, -7.28284407, 0.28444102, 59.03387451, 1.00000000],
    "Bip01 R Forearm": [-0.00708922, -0.99997431, -0.00106036, 0.00000000, -0.99979711, 0.00710796, -0.01885459, 0.00000000, 0.01886164, 0.00092648, -0.99982160, 0.00000000, -17.51569748, -0.52064961, 59.03387451, 1.00000000],
    "Bip01 R Hand": [-0.00708922, -0.00185661, 0.99997312, 0.00000000, -0.99979711, -0.01884892, -0.00712297, 0.00000000, 0.01886164, -0.99982053, -0.00172261, 0.00000000, -26.03531075, 59.03344345, 0.56765658, 1.00000000],
    "Bip01 R Finger0": [0.51655495, -0.85602993, -0.01958990, 0.00000000, -0.85476297, -0.51686996, 0.04717602, 0.00000000, -0.05050875, -0.00762435, -0.99869412, 0.00000000, -20.10997772, -11.68595028, 59.72956848, 1.00000000],
    "Bip01 R Finger01": [-0.08610324, -0.99609357, -0.01958990, 0.00000000, -0.99525458, 0.08510298, 0.04717602, 0.00000000, -0.04532399, 0.02355844, -0.99869412, 0.00000000, -24.77327538, 3.57737494, 59.72956848, 1.00000000],
    "Bip01 R Finger1": [-0.00712357, -0.00079764, 0.99997431, 0.00000000, -0.99981076, 0.01810678, -0.00710796, 0.00000000, -0.01810064, -0.99983561, -0.00092648, 0.00000000, -27.42948151, 60.08723068, 0.18117964, 1.00000000],
    "Bip01 R Finger11": [-0.00712357, -0.00079764, 0.99997431, 0.00000000, -0.99981076, 0.01810678, -0.00710796, 0.00000000, -0.01810064, -0.99983561, -0.00092648, 0.00000000, -29.26063538, 60.08722687, 0.18117964, 1.00000000],
    "Bip01 L Thigh": [0.01321209, 0.96313661, 0.26868817, 0.00000000, 0.17244528, 0.26249057, -0.94940054, 0.00000000, -0.98493052, 0.05887756, -0.16262026, 0.00000000, 40.60599899, -4.00070333, 9.77672863, 1.00000000],
    "Bip01 L Calf": [-0.14558448, 0.95216161, 0.26868808, 0.00000000, 0.12686184, 0.28730601, -0.94940054, 0.00000000, -0.98117846, -0.10413171, -0.16262028, 0.00000000, 22.17516518, -0.35356724, 9.77672958, 1.00000000],
    "Bip01 L Foot": [0.00000002, 0.94737417, 0.32012862, 0.00000000, 0.00000005, 0.32012862, -0.94737411, 0.00000000, -1.00000000, 0.00000003, -0.00000004, 0.00000000, 5.25708008, -1.20160520, 8.99331665, 1.00000000],
    "Bip01 L Toe0": [0.94737417, -0.00000002, 0.32012862, 0.00000000, 0.32012862, -0.00000005, -0.94737411, 0.00000000, 0.00000003, 1.00000000, -0.00000004, 0.00000000, -6.83172464, -0.06356285, 8.99331665, 1.00000000],
    "Bip01 R Thigh": [0.01321281, 0.96313775, -0.26868421, 0.00000000, -0.17244513, -0.26248658, -0.94940174, 0.00000000, -0.98493057, 0.05887754, 0.16262017, 0.00000000, 40.60599899, -4.00070381, -9.77673244, 1.00000000],
    "Bip01 R Calf": [-0.14558396, 0.95216280, -0.26868418, 0.00000000, -0.12686238, -0.28730205, -0.94940174, 0.00000000, -0.98117846, -0.10413173, 0.16262020, 0.00000000, 22.17516518, -0.35356766, -9.77673244, 1.00000000],
    "Bip01 R Foot": [0.00000002, 0.94737542, -0.32012478, 0.00000000, 0.00000004, -0.32012478, -0.94737536, 0.00000000, -1.00000000, 0.00000000, -0.00000005, 0.00000000, 5.25708008, -1.20160663, -8.99331856, 1.00000000],
    "Bip01 R Toe0": [0.94737542, -0.00000002, -0.32012478, 0.00000000, -0.32012478, -0.00000004, -0.94737536, 0.00000000, 0.00000000, 1.00000000, -0.00000005, 0.00000000, -6.83172512, -0.06356285, -8.99331951, 1.00000000],
}

# ============================================================================
# Native XML2 Bone Translations (extracted from 0601.igb)
# ============================================================================
# Each entry is [x, y, z] indexed by bone index (0-34).  These are the exact
# parent-local offsets the game FK engine uses to reconstruct bone positions.
NATIVE_TRANSLATIONS = {
    0: [0.00000000, 0.00000000, 0.00000000],
    1: [0.68984902, 0.00000000, 41.81953049],
    2: [0.00000000, -0.00000000, 0.00000000],
    3: [0.31228638, 0.15484005, -0.00000162],
    4: [3.49065018, -0.00468764, -0.00000001],
    5: [5.89215851, -0.00828373, -0.00000002],
    6: [10.40359020, -0.00188103, -0.00000001],
    7: [2.44559216, 0.00000006, 0.00000000],
    8: [1.55949831, -3.29247999, -0.00000813],
    9: [9.51408577, -0.00931360, -0.00000003],
    10: [-2.27314448, 1.13265991, 0.31228313],
    11: [5.88065577, 0.00000033, -0.00000215],
    12: [10.23828030, 0.00000001, -0.00000022],
    13: [8.51961517, -0.00000005, -0.00000050],
    14: [1.09982407, 0.98525184, -2.20674801],
    15: [2.02486897, -0.00000018, -0.00000271],
    16: [3.59602070, 0.00110330, -0.33947095],
    17: [1.83115566, 0.00000141, -0.00000002],
    18: [-2.27314448, 1.13266170, -0.31227681],
    19: [5.88065577, 0.00000023, 0.00000099],
    20: [10.23827934, -0.00000002, -0.00000131],
    21: [8.51961327, 0.00000002, -0.00000147],
    22: [1.09982443, 0.98525357, 2.20674801],
    23: [2.02486849, 0.00000024, -0.00000041],
    24: [3.59602070, 0.00110484, 0.33947095],
    25: [1.83115411, 0.00000253, 0.00000000],
    26: [-0.30860963, -0.16205171, 3.32986927],
    27: [18.79184341, 0.00000009, -0.00000031],
    28: [18.40011024, 0.00000015, 0.00000021],
    29: [5.19351721, 5.63011932, -0.00000014],
    30: [-0.30860934, -0.16203320, -3.32987022],
    31: [18.79184151, 0.00000005, -0.00000035],
    32: [18.40011024, 0.00000021, 0.00000033],
    33: [5.19351721, 5.63011837, 0.00000071],
    34: [0.68984902, 0.00000000, 0.14953232],
}


# ============================================================================
# Bip Prefix Normalization
# ============================================================================

def _rename_bip_prefix(armature_obj, old_prefix, new_prefix):
    """Rename all bones from one Bip prefix to another (e.g. Bip001 → Bip01).

    Also updates vertex group names on child meshes so skinning stays intact.
    """
    import bpy

    # Build rename map: only rename bones that start with old_prefix
    rename_map = {}
    for bone in armature_obj.data.bones:
        if bone.name.startswith(old_prefix):
            new_name = new_prefix + bone.name[len(old_prefix):]
            rename_map[bone.name] = new_name

    if not rename_map:
        return

    # Rename bones in edit mode
    bpy.context.view_layer.objects.active = armature_obj
    armature_obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    for old_name, new_name in rename_map.items():
        eb = armature_obj.data.edit_bones.get(old_name)
        if eb:
            eb.name = new_name
    bpy.ops.object.mode_set(mode='OBJECT')

    # Rename vertex groups on all skinned meshes to match
    for child in _get_skinned_meshes(armature_obj):
        for vg in child.vertex_groups:
            if vg.name in rename_map:
                vg.name = rename_map[vg.name]


# ============================================================================
# Profile Detection
# ============================================================================

def _detect_bip_prefix(armature_obj):
    """Detect which Bip prefix the rig uses (Bip01, Bip001, etc.).

    Returns:
        The prefix string (e.g. "Bip01", "Bip001") or None if not a Biped rig.
    """
    bone_names = [b.name for b in armature_obj.data.bones]
    # Count bones by prefix — check common 3ds Max Biped variants
    for prefix in ("Bip01", "Bip001", "Bip002"):
        count = sum(1 for n in bone_names if n.startswith(prefix))
        if count >= 3:
            return prefix
    return None


def is_bip01_rig(armature_obj):
    """Check if an armature is a Bip01-style rig (already XML2 naming or variant).

    Detects rigs that use Bip01, Bip001, or similar 3ds Max Biped naming
    and should go through the setup path (not the convert path).

    Returns:
        True if the rig has Biped-style bones.
    """
    return _detect_bip_prefix(armature_obj) is not None


def detect_rig_profile(armature_obj):
    """Detect the source rig naming convention.

    Uses universal normalization to detect ANY supported rig type:
    - Bip01/Bip001 (already XML2/3dsMax naming → 'bip01')
    - Mixamo, Unity Humanoid, VRChat, Source Engine, MMD, etc. → 'universal'

    Args:
        armature_obj: Blender armature object.

    Returns:
        'bip01', 'universal', or None (unknown).
    """
    # Check for Bip01/Bip001 rig (already Biped naming)
    if is_bip01_rig(armature_obj):
        return 'bip01'

    # Universal detection: try normalizing each bone and looking up.
    # Counts direct aliases, finger-classified bones, and spine-chain bones.
    matched = 0
    for bone in armature_obj.data.bones:
        norm_low = _normalize_bone_name(bone.name).lower()
        if (_lookup_bone(bone.name) is not None
                or _bone_tables.classify_finger(norm_low) is not None
                or norm_low in SPINE_ALIAS_SET
                or bone.name.lower() in SPINE_ALIAS_SET):
            matched += 1
            if matched >= 3:
                return 'universal'

    return None


# ============================================================================
# Bone Rename Map Builder
# ============================================================================

_DUP_SUFFIX_RE = None  # compiled lazily (module import order)


def _strip_dup_suffix(name_lower):
    """Strip a Blender duplicate suffix ('.001' / normalized '_001').

    FBX files can contain a second skeleton with duplicate bone names
    (e.g. TotK armor rigs: 'Waist.001', 'Leg_1_L.001') — these must still
    alias so their weights merge into the real bone's target.
    """
    global _DUP_SUFFIX_RE
    if _DUP_SUFFIX_RE is None:
        import re
        _DUP_SUFFIX_RE = re.compile(r'[._]\d{3}$')
    return _DUP_SUFFIX_RE.sub('', name_lower)


def _lookup_bone_priority(name, target_game='XML2'):
    """Match a bone name to a target skeleton bone, with alias priority.

    Tries: raw lowercase match → normalized match, against the CATS-ported
    priority table first, then the legacy alias table (default priority 500).
    Duplicate-suffixed names ('Waist.001') match with a +1000 penalty so the
    unsuffixed original always wins the rename claim.
    When target_game='MUA', FX bone aliases match first.

    Returns:
        (target_name, priority) or None. Lower priority = stronger claim.
    """
    low = name.lower()
    normalized = _normalize_bone_name(name)
    norm_low = normalized.lower()

    keys = [(low, 0), (norm_low, 0)]
    for base, _pen in list(keys):
        stripped = _strip_dup_suffix(base)
        if stripped != base:
            keys.append((stripped, 1000))

    for key, penalty in keys:
        if target_game == 'MUA':
            fx = ALIAS_TO_MUA_FX.get(key)
            if fx:
                return fx, 0 + penalty
        hit = CATS_RENAME_LOOKUP.get(key)
        if hit:
            return hit[0], hit[1] + penalty
        legacy = ALIAS_TO_XML2.get(key)
        if legacy:
            return legacy, 500 + penalty

    return None


def _lookup_bone(name, target_game='XML2'):
    """Legacy wrapper: match a bone name to a target skeleton bone name."""
    hit = _lookup_bone_priority(name, target_game=target_game)
    return hit[0] if hit else None


def _bone_depth(bone):
    """Number of ancestors above a bone (hierarchy depth)."""
    depth = 0
    parent = bone.parent
    while parent is not None:
        depth += 1
        parent = parent.parent
    return depth


_CONFLICT_RULES = _bone_tables.build_conflict_rules()


def _conflict_demotions(armature_obj):
    """Resolve context-dependent alias conflicts (CATS-style).

    Some names mean different things depending on what else exists in the
    rig — e.g. Nintendo's Knee_L is a kneecap helper when Leg_1_L/Leg_2_L
    are both present, but IS the calf on rigs without Leg_2.

    Returns:
        Dict of bone_name -> merge_target for bones demoted from rename
        eligibility to weight-merge sources.
    """
    # Map both raw and normalized lowercase names to actual bone names
    name_lookup = {}
    for bone in armature_obj.data.bones:
        name_lookup.setdefault(bone.name.lower(), bone.name)
        name_lookup.setdefault(_normalize_bone_name(bone.name).lower(),
                               bone.name)

    demotions = {}
    for required_set, demoted, merge_target in _CONFLICT_RULES:
        if all(req in name_lookup for req in required_set):
            # Demote the named bone AND any duplicate-suffixed variant
            # ('Knee_L' + 'Knee_L.001' from a second armor skeleton)
            for key, bone_name in name_lookup.items():
                if key == demoted or _strip_dup_suffix(key) == demoted:
                    demotions[bone_name] = merge_target
    return demotions


def _assign_spine_chain(armature_obj):
    """Assign spine-class bones to the XML2 spine chain by hierarchy depth.

    All bones matching the (huge, CATS-derived) spine/chest alias set are
    sorted root-to-tip and distributed across Bip01 Spine / Spine1 / Spine2.
    Rigs with more than three spine bones keep the first, second, and LAST
    (the one that connects to the neck) and merge the middles into Spine1.

    Returns:
        (rename, extras): dicts of bone_name -> xml2 target. 'extras' are
        weight-merge sources, not renames.
    """
    matches = []
    dup_bones = []  # '.001'-suffixed duplicates (second skeleton in file)
    seen_stripped = set()
    for bone in armature_obj.data.bones:
        low = bone.name.lower()
        norm_low = _normalize_bone_name(bone.name).lower()
        stripped_low = _strip_dup_suffix(low)
        stripped_norm = _strip_dup_suffix(norm_low)
        if low in SPINE_ALIAS_SET or norm_low in SPINE_ALIAS_SET:
            matches.append(bone)
            seen_stripped.add(stripped_norm)
        elif (stripped_low in SPINE_ALIAS_SET
              or stripped_norm in SPINE_ALIAS_SET):
            dup_bones.append(bone)

    matches.sort(key=lambda b: (_bone_depth(b), b.name))

    rename = {}
    extras = {}
    n = len(matches)
    if n == 0:
        return rename, extras

    if n <= 3:
        targets = ['Bip01 Spine', 'Bip01 Spine1', 'Bip01 Spine2'][:n]
        for bone, target in zip(matches, targets):
            rename[bone.name] = target
    else:
        rename[matches[0].name] = 'Bip01 Spine'
        rename[matches[1].name] = 'Bip01 Spine1'
        rename[matches[-1].name] = 'Bip01 Spine2'
        for bone in matches[2:-1]:
            extras[bone.name] = 'Bip01 Spine1'

    # Duplicate-suffixed spine bones merge into their primary's target
    # (or Spine1 when the primary wasn't found)
    primary_target = {}
    for bone in matches:
        key = _strip_dup_suffix(_normalize_bone_name(bone.name).lower())
        primary_target.setdefault(
            key, rename.get(bone.name) or extras.get(bone.name))
    for bone in dup_bones:
        key = _strip_dup_suffix(_normalize_bone_name(bone.name).lower())
        extras[bone.name] = primary_target.get(key) or 'Bip01 Spine1'

    return rename, extras


def _dist3(a, b):
    """Euclidean distance between two indexable 3-vectors."""
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    dz = a[2] - b[2]
    return (dx * dx + dy * dy + dz * dz) ** 0.5


def _reanchor_letter_chains(armature_obj, chains, dup_members,
                            chain_letters, side):
    """Correct letter-scheme finger labels using geometry.

    The thumb's base joint is anatomically closest to the wrist — measure
    each letter chain's base-bone distance to the hand bone and relabel the
    chains so the closest is the thumb, with the remaining letters keeping
    their order walking away from it (index, middle, ring, little).

    Mutates chains / dup_members / chain_letters in place. No-op when:
    - fewer than 2 letter chains on this side
    - a word-named thumb chain exists (unambiguous, trust it)
    - the hand bone or bone positions can't be found
    - the closest chain isn't decisively closer (< 80% of second place)
    """
    fingers_order = ['thumb', 'index', 'middle', 'ring', 'little']

    letter_fingers = {f: chain_letters[(side, f)] for f in fingers_order
                      if (side, f) in chain_letters and (side, f) in chains}
    if len(letter_fingers) < 2:
        return

    # A word-named thumb chain (Thumb_L, Oya...) is unambiguous — letters
    # then never include the thumb, and the existing labels stand.
    if ('thumb' in chains_keys_for_side(chains, side)
            and 'thumb' not in letter_fingers):
        return

    # Find the hand bone (pre-rename name) on this side
    hand_bone = None
    for bone in armature_obj.data.bones:
        hit = _lookup_bone_priority(bone.name)
        if hit is not None and hit[0] == f'Bip01 {side} Hand':
            hand_bone = bone
            break
    if hand_bone is None or getattr(hand_bone, 'head_local', None) is None:
        return

    # Distance from each letter chain's base bone to the hand
    dists = {}
    for finger in letter_fingers:
        entries = chains.get((side, finger))
        if not entries:
            continue
        base = min(entries, key=lambda e: _bone_depth(e[0]))[0]
        head = getattr(base, 'head_local', None)
        if head is None:
            return
        dists[finger] = _dist3(head, hand_bone.head_local)
    if len(dists) < 2:
        return

    ranked = sorted(dists, key=dists.get)
    closest, second = ranked[0], ranked[1]
    if dists[closest] > 0.8 * dists[second]:
        return  # not decisively closer — trust the naming convention

    if closest == 'thumb':
        return  # naming already correct

    # Relabel: sort letter chains by their letter; the geometric thumb's
    # letter anchors which end of the sequence is the thumb.
    by_letter = sorted(letter_fingers.items(), key=lambda kv: kv[1])
    ordered = [f for f, _l in by_letter]  # finger labels in letter order
    thumb_pos = ordered.index(closest)
    if thumb_pos == 0:
        new_order = ordered  # ascending: first letter = thumb
    elif thumb_pos == len(ordered) - 1:
        new_order = list(reversed(ordered))  # descending
    else:
        return  # thumb in the middle of the letter sequence — bail

    relabel = {}  # old finger label -> new finger label
    for new_finger, old_finger in zip(fingers_order, new_order):
        if old_finger != new_finger:
            relabel[old_finger] = new_finger
    if not relabel:
        return

    # Apply relabel to all three dicts (two-phase to avoid key collisions)
    def remap(d):
        moved = {}
        for old_f, new_f in relabel.items():
            if (side, old_f) in d:
                moved[(side, new_f)] = d.pop((side, old_f))
        d.update(moved)

    remap(chains)
    remap(dup_members)
    remap(chain_letters)
    print(f"[IGB Rig Converter] {side} letter fingers re-anchored "
          f"geometrically: {relabel}")


def chains_keys_for_side(chains, side):
    """Finger labels present in chains for one side."""
    return {f for (s, f) in chains if s == side}


# Full-finger (MUA) target columns: thumb..little -> Finger0..Finger4.
# Used when full_fingers is on; bone names use the underscore scheme
# Bip01_{side}_Finger{col}_{seg} that MUA's full-finger movesets (Storm/Ultron/
# Loki) drive.
_FULL_FINGER_COL = {'thumb': 0, 'index': 1, 'middle': 2, 'ring': 3, 'little': 4}


def _build_finger_maps(armature_obj, full_fingers=False):
    """Classify finger bones and map them to the target finger rig.

    Default (reduced) target = XML2/MUA's 2-chain hand: Finger0/Finger01 (thumb)
    + Finger1/Finger11 (one combined finger). When full_fingers is True, targets
    MUA's full 5-finger hand (Bip01_{side}_Finger{0-4}_{0-2}, underscore scheme)
    — each source finger keeps its own column, no promotion/merge.

    XML2 has two chains per hand: Finger0/Finger01 (thumb) and
    Finger1/Finger11 (fingers). Mapping rules:
      - thumb proximal/intermediate  -> rename to Finger0 / Finger01
      - "main" finger prox/interm    -> rename to Finger1 / Finger11
        (middle preferred; falls back to index, ring, then little when the
        model lacks a middle finger — this keeps a real bone with correct
        position instead of a guessed dummy)
      - every other finger bone      -> weight-merge: proximal -> Finger1,
        deeper segments -> Finger11, distals -> the chain's deepest target,
        metacarpals -> Hand
    Segment identity comes from hierarchy depth within each chain, NOT from
    the digits in the name — numbering schemes (MMD 0-based, VRoid 1-based,
    Max two-digit) disagree, but depth order never lies.

    Returns:
        (rename, merge): dicts of bone_name -> xml2 target.
    """
    from collections import defaultdict

    chains = defaultdict(list)  # (side, finger) -> [(bone, sort_hint)]
    dup_members = defaultdict(list)  # (side, finger) -> [(bone, stripped_key)]
    chain_letters = {}  # (side, finger) -> scheme letter ('a'-'e') or absent
    for bone in armature_obj.data.bones:
        # Blender's FBX importer appends zero-length '<name>_end' leaves to
        # every chain tip. They are NOT finger segments — letting them join a
        # chain inflates its depth and steals segment slots (the real
        # 'Finger11' loses Finger1_1 to 'Finger11_end'), which scrambles the
        # full-finger skeleton and, through the stored bone table, the whole
        # round-tripped rig. Skip them; the ancestor fallback merges them.
        if bone.name.lower().endswith('_end'):
            continue
        norm_low = _normalize_bone_name(bone.name).lower()
        hit = _bone_tables.classify_finger(norm_low)
        if hit is None:
            # Also try a lightly-cleaned raw name (normalization can strip
            # leading segments that the classifier wants to see)
            raw = bone.name.lower().replace(' ', '_').replace('.', '_')
            raw = raw.replace('-', '_').replace(':', '_')
            hit = _bone_tables.classify_finger(raw)
        if hit is None:
            continue
        side, finger, sort_hint, letter = hit
        # Duplicate-suffixed bones ('Finger_A_1_L.001' from a second
        # skeleton) must not join the chain — they'd corrupt segment
        # ordering. Route them to merge into the primary's target below.
        stripped = _strip_dup_suffix(norm_low)
        if stripped != norm_low:
            dup_members[(side, finger)].append((bone, stripped))
        else:
            chains[(side, finger)].append((bone, sort_hint))
            if letter is not None:
                chain_letters.setdefault((side, finger), letter)

    # Letter schemes are ambiguous across games (Pokemon Masters: A=thumb,
    # VRoid fused: A=little). Re-anchor letter chains geometrically: the
    # chain whose base bone is closest to the hand is the thumb.
    for side in ('L', 'R'):
        _reanchor_letter_chains(armature_obj, chains, dup_members,
                                chain_letters, side)

    rename = {}
    merge = {}

    for side in ('L', 'R'):
        # Promotion: pick the chain that becomes XML2's Finger1/Finger11
        main = next((f for f in ('middle', 'index', 'ring', 'little')
                     if (side, f) in chains), None)

        for finger in ('thumb', 'middle', 'index', 'ring', 'little'):
            entries = chains.get((side, finger))
            if not entries:
                continue

            # Order chain root-to-tip: hierarchy depth first, name segment
            # number as tiebreaker (hint -1 = no segment in name)
            entries.sort(key=lambda e: (_bone_depth(e[0]),
                                        e[1] if e[1] >= 0 else 99,
                                        e[0].name))

            # Assign anatomical segments from chain order:
            # 0=metacarpal, 1=proximal, 2=intermediate, 3=distal, 4+=tip
            n = len(entries)
            if n <= 3:
                segs = list(range(1, n + 1))
            elif entries[0][1] == 0:
                # Explicit metacarpal marker on the first bone (MMD Finger0_)
                segs = [0, 1, 2, 3] + [4] * (n - 4)
            else:
                segs = [1, 2, 3] + [4] * (n - 3)

            hand = f'Bip01 {side} Hand'

            # --- Full-finger (MUA) path: each finger keeps its own column ---
            if full_fingers:
                col = _FULL_FINGER_COL.get(finger)
                if col is None:
                    continue
                for (bone, _hint), seg in zip(entries, segs):
                    if seg == 0:
                        merge[bone.name] = hand          # metacarpal -> Hand
                    else:
                        s = min(seg - 1, 2)              # 1/2/3 -> 0/1/2
                        tname = f'Bip01_{side}_Finger{col}_{s}'
                        if seg <= 3:
                            rename[bone.name] = tname
                        else:
                            merge[bone.name] = tname     # extra tips -> distal
                # duplicate-suffixed members handled by the shared block below
                for dup_bone, stripped in dup_members.get((side, finger), ()):
                    target = None
                    for primary, _h in entries:
                        if _normalize_bone_name(primary.name).lower() == stripped:
                            target = (rename.get(primary.name)
                                      or merge.get(primary.name))
                            break
                    merge[dup_bone.name] = target or f'Bip01_{side}_Finger{col}_0'
                continue

            kind = ('thumb' if finger == 'thumb'
                    else 'main' if finger == main
                    else 'other')

            f0 = f'Bip01 {side} Finger0'
            f01 = f'Bip01 {side} Finger01'
            f1 = f'Bip01 {side} Finger1'
            f11 = f'Bip01 {side} Finger11'

            for (bone, _hint), seg in zip(entries, segs):
                if seg == 0:
                    merge[bone.name] = hand
                elif kind == 'thumb':
                    if seg == 1:
                        rename[bone.name] = f0
                    elif seg == 2:
                        rename[bone.name] = f01
                    else:
                        merge[bone.name] = f01
                elif kind == 'main':
                    if seg == 1:
                        rename[bone.name] = f1
                    elif seg == 2:
                        rename[bone.name] = f11
                    else:
                        merge[bone.name] = f11
                else:
                    if seg == 1:
                        merge[bone.name] = f1
                    else:
                        merge[bone.name] = f11

            # Duplicate-suffixed chain members merge into their primary's
            # target (the duplicate skeleton's weights follow the original)
            for dup_bone, stripped in dup_members.get((side, finger), ()):
                target = None
                for primary, _h in entries:
                    pkey = _normalize_bone_name(primary.name).lower()
                    if pkey == stripped:
                        target = (rename.get(primary.name)
                                  or merge.get(primary.name))
                        break
                if target is None:
                    # No matching primary — default to the chain's base
                    target = f0 if kind == 'thumb' else f1
                merge[dup_bone.name] = target

    # Duplicate members whose entire primary chain is absent
    for (side, finger), dups in dup_members.items():
        if full_fingers:
            col = _FULL_FINGER_COL.get(finger, 0)
            base = f'Bip01_{side}_Finger{col}_0'
        else:
            base = (f'Bip01 {side} Finger0' if finger == 'thumb'
                    else f'Bip01 {side} Finger1')
        for dup_bone, _stripped in dups:
            if dup_bone.name not in merge and dup_bone.name not in rename:
                merge[dup_bone.name] = base

    return rename, merge


def build_rename_map(armature_obj, profile=None, target_game='XML2',
                     full_fingers=False):
    """Build a mapping from current bone names to target skeleton bone names.

    Three passes:
      1. Spine chain assignment (depth-ordered, handles any spine count)
      2. Finger chain assignment (scheme-independent, with promotion)
      3. General alias matching with per-alias priority — when several bones
         alias to the same target (e.g. both 'Root' and 'Pelvis' present),
         the canonical name wins and the others become merge sources.

    Args:
        armature_obj: Blender armature object.
        profile: Ignored (kept for API compatibility).
        target_game: 'XML2' or 'MUA' — determines which bones are valid targets.

    Returns:
        Dict mapping old_name -> new_name for bones that can be mapped.
    """
    rename_map = {}
    valid_names = get_bone_names_for_game(target_game, full_fingers=full_fingers)

    spine_rename, spine_extras = _assign_spine_chain(armature_obj)
    finger_rename, finger_merge = _build_finger_maps(
        armature_obj, full_fingers=full_fingers)
    demotions = _conflict_demotions(armature_obj)

    # Spine/finger-classified and conflict-demoted bones are settled —
    # exclude from the general alias pass
    handled = (set(spine_rename) | set(spine_extras)
               | set(finger_rename) | set(finger_merge)
               | set(demotions))

    for chain_map in (spine_rename, finger_rename):
        for old_name, target in chain_map.items():
            if target in valid_names:
                rename_map[old_name] = target

    claimed = set(rename_map.values())

    # General pass: best-priority candidate per target wins
    candidates = {}  # target -> (priority, bone_name)
    for bone in armature_obj.data.bones:
        if bone.name in handled:
            continue
        hit = _lookup_bone_priority(bone.name, target_game=target_game)
        if hit is None:
            continue
        target, priority = hit
        if target not in valid_names or target in claimed:
            continue
        best = candidates.get(target)
        if best is None or priority < best[0]:
            candidates[target] = (priority, bone.name)

    for target, (_priority, bone_name) in candidates.items():
        rename_map[bone_name] = target

    return rename_map


def build_merge_map(armature_obj, profile=None, rename_map=None,
                    target_game='XML2', full_fingers=False):
    """Build a mapping for bones whose weights should merge into a target.

    Resolution order per bone:
      1. Spine-chain extras and non-promoted finger bones
      2. Explicit merge tables (CATS bone_reweight + legacy entries)
      3. Face bone detection -> Bip01 Head
      4. Keyword heuristics (breast/hair/weapon/tail)
      5. Nearest-mapped-ancestor fallback — NO weighted bone is ever simply
         deleted; weights always flow to the closest surviving relative.

    Args:
        armature_obj: Blender armature object.
        profile: Ignored (kept for API compatibility).
        rename_map: Already-built rename map (to exclude already-mapped bones).
        target_game: 'XML2' or 'MUA'.

    Returns:
        Dict mapping old_bone_name -> xml2_target_name for weight merging.
    """
    if rename_map is None:
        rename_map = build_rename_map(armature_obj, target_game=target_game,
                                      full_fingers=full_fingers)

    merge_map = {}

    # 1. Chain-derived merges (spine extras + non-promoted fingers +
    # conflict-demoted helper bones like Nintendo's Knee_L)
    spine_rename, spine_extras = _assign_spine_chain(armature_obj)
    finger_rename, finger_merge = _build_finger_maps(
        armature_obj, full_fingers=full_fingers)
    demotions = _conflict_demotions(armature_obj)
    for source, target in (list(spine_extras.items())
                           + list(finger_merge.items())
                           + list(demotions.items())):
        if source not in rename_map:
            merge_map[source] = target

    for bone in armature_obj.data.bones:
        name = bone.name
        if name in rename_map or name in merge_map:
            continue

        low = name.lower()
        norm_low = _normalize_bone_name(name).lower()

        # 2. Explicit merge tables (also matched with '.001' suffix stripped)
        target = (MERGE_WEIGHT_TARGETS.get(low)
                  or MERGE_WEIGHT_TARGETS.get(norm_low)
                  or MERGE_WEIGHT_TARGETS.get(_strip_dup_suffix(low))
                  or MERGE_WEIGHT_TARGETS.get(_strip_dup_suffix(norm_low)))
        if target:
            merge_map[name] = target
            continue

        # 2b. Alias losers: this bone aliases a target another bone claimed
        # (e.g. 'Root' lost Pelvis to 'Waist', or 'Leg_1_L.001' from a
        # duplicate armor skeleton) — merge its weights into that target.
        hit = _lookup_bone_priority(name, target_game=target_game)
        if hit is not None and hit[0] not in _NON_DEFORM_TARGETS:
            merge_map[name] = hit[0]
            continue

        # 3. Face bones -> Head (cheeks, brows, lids, mouth, jaw, ears...)
        if (_bone_tables.is_face_bone(norm_low)
                or _bone_tables.is_face_bone(low)):
            merge_map[name] = "Bip01 Head"
            continue

        # 4. Keyword heuristics (checked before the ancestor fallback so
        # oddly-parented accessory bones still land sensibly)
        if 'breast' in low or 'bust' in low or 'pectoral' in low:
            merge_map[name] = "Bip01 Spine2"
            continue
        if ('hair' in low or 'ahoge' in low or 'bangs' in low
                or 'ponytail' in low or 'twintail' in low or 'kami' in low):
            merge_map[name] = "Bip01 Head"
            continue
        if 'weapon' in low:
            right = ('right' in low or low.endswith('_r')
                     or '_r_' in low or low.endswith('.r'))
            merge_map[name] = f"Bip01 {'R' if right else 'L'} Hand"
            continue
        # 'humerus' is the upper-arm bone — some rigs (e.g. MSW models)
        # parent these shoulder helpers under the HEAD, which would
        # otherwise send their weights to Bip01 Head via the ancestor
        # fallback. Route by anatomy instead.
        if 'humerus' in low or 'deltoid' in low:
            right = ('right' in low or low.endswith('_r')
                     or low.endswith(' r') or '_r_' in low
                     or low.endswith('.r'))
            merge_map[name] = f"Bip01 {'R' if right else 'L'} UpperArm"
            continue
        if 'tentacle' in low or 'cape' in low or 'tail' in low:
            merge_map[name] = "Bip01 Pelvis"
            continue

    # 5. Nearest-mapped-ancestor fallback for everything still unresolved.
    # Walk up the hierarchy to the closest bone that survives conversion
    # (renamed or merged) and route weights there. Skips non-deforming
    # targets (Bip01 root, Motion, MUA FX bones) — weights merged into
    # those would be silently dropped at export.
    resolved = dict(rename_map)
    resolved.update(merge_map)

    for bone in armature_obj.data.bones:
        name = bone.name
        if name in resolved:
            continue

        target = None
        parent = bone.parent
        while parent is not None:
            candidate = resolved.get(parent.name)
            if candidate and candidate not in _NON_DEFORM_TARGETS:
                target = candidate
                break
            parent = parent.parent

        merge_map[name] = target or "Bip01 Pelvis"
        resolved[name] = merge_map[name]

    return merge_map


# ============================================================================
# Native-Skeleton Fit (Max-modder style)
# ============================================================================
# Every native XML2 character uses the bit-identical skeleton (verified by
# parsing 20+ game skins: same Bip01 41.82, same thigh/calf/spine lengths).
# 3ds Max modder skins work flawlessly with all animations because they're
# rigged onto this exact shared biped. This mode reproduces that: the MESH
# is warped (weight-blended, translation-only — smooth) so its joints land
# exactly on the native skeleton, then the bones are moved to native
# positions. The exported skeleton is then identical to official skins.

def _native_game_positions(target_game='XML2'):
    """Native joint world positions (game space) for every skeleton bone.

    Deforming bones come from NATIVE_INV_JOINT_MATRICES (row-major inverse
    bind: joint position p satisfies [p,1] @ M = 0 -> p_i = -(t . R_row_i)).
    Non-deforming roots use NATIVE_TRANSLATIONS.
    """
    positions = {}
    for name, m in NATIVE_INV_JOINT_MATRICES.items():
        r0 = (m[0], m[1], m[2])
        r1 = (m[4], m[5], m[6])
        r2 = (m[8], m[9], m[10])
        t = (m[12], m[13], m[14])
        positions[name] = tuple(
            -(t[0] * row[0] + t[1] * row[1] + t[2] * row[2])
            for row in (r0, r1, r2)
        )

    bip01 = tuple(NATIVE_TRANSLATIONS[1])
    positions['Bip01'] = bip01
    positions['Motion'] = tuple(NATIVE_TRANSLATIONS[34])
    positions['Bone_000'] = (0.0, 0.0, 0.0)
    if target_game == 'MUA':
        for fx_name in MUA_FX_BONE_NAMES:
            positions[fx_name] = bip01
    return positions


def _fit_skeleton_to_native(armature_obj, target_game='XML2', operator=None):
    """Warp the mesh and skeleton onto the exact native XML2 biped.

    Per-vertex offsets are the weight-blended bone position deltas
    (translation-only, so shape keys shift rigidly and stay intact).
    Must run AFTER rename/merge/repose and AFTER the export scale is
    stored, but BEFORE inv_bind/translation computation.
    """
    import bpy
    import math
    from mathutils import Quaternion, Vector

    # Total export scale (matches the exporter's composition)
    custom_scale = armature_obj.get("igb_export_scale", 1.0)
    if isinstance(custom_scale, (list, tuple)):
        custom_scale = 1.0
    obj_scale = armature_obj.matrix_world.to_scale()
    total_scale = custom_scale * ((abs(obj_scale.x) + abs(obj_scale.y)
                                   + abs(obj_scale.z)) / 3.0)
    if total_scale < 1e-9:
        return

    # game space = Rz90 @ arm_world_rot @ armature space * total_scale
    # -> armature space = (Rz90 @ arm_rot)^-1 @ game / total_scale
    arm_rot = armature_obj.matrix_world.to_quaternion().to_matrix()
    rz90 = Quaternion((0, 0, 1), math.radians(90)).to_matrix()
    game_to_arm = (rz90 @ arm_rot).inverted()

    targets = {}
    for name, p_game in _native_game_positions(target_game).items():
        targets[name] = (game_to_arm @ Vector(p_game)) / total_scale

    # Bone deltas in armature space
    deltas = {}
    for name, target in targets.items():
        bone = armature_obj.data.bones.get(name)
        if bone is not None:
            deltas[name] = target - bone.head_local

    if not deltas:
        return

    # Bones the native table doesn't know (the full-finger hand's 30
    # Bip01_{S}_Finger{0-4}_{0-2} chains) inherit the delta of their nearest
    # fitted ancestor — the whole chain rides rigidly with its Hand. Without
    # this the fit moved the Hand but left the fingers (and their weighted
    # mesh) behind, tearing the wrist — which is why full-finger conversions
    # used to skip the fit entirely (and then round-tripped as a scrambled
    # NON-native skeleton: arms reconstructed at leg height).
    inherited = {}
    for bone in armature_obj.data.bones:
        if bone.name in deltas:
            continue
        parent = bone.parent
        while parent is not None:
            d = deltas.get(parent.name)
            if d is not None:
                inherited[bone.name] = d
                break
            parent = parent.parent
    deltas.update(inherited)

    # ---- Warp mesh vertices (and shape keys) ----
    for mesh_obj in _get_skinned_meshes(armature_obj):
        mesh = mesh_obj.data
        try:
            rel3 = (mesh_obj.matrix_world.inverted()
                    @ armature_obj.matrix_world).to_3x3()
        except ValueError:
            continue

        # vertex group index -> delta in MESH space (zero-delta bones must
        # still appear so they count toward the blend normalization)
        vg_delta = {}
        any_nonzero = False
        for vg in mesh_obj.vertex_groups:
            d = deltas.get(vg.name)
            if d is not None:
                md = rel3 @ d
                vg_delta[vg.index] = md
                if md.length > 1e-7:
                    any_nonzero = True
        if not any_nonzero:
            continue

        n_verts = len(mesh.vertices)
        offsets = [None] * n_verts
        for vert in mesh.vertices:
            acc = Vector((0.0, 0.0, 0.0))
            total_w = 0.0
            for g in vert.groups:
                if g.weight <= 0.0:
                    continue
                d = vg_delta.get(g.group)
                if d is not None:
                    acc += d * g.weight
                    total_w += g.weight
            if total_w > 1e-6:
                offsets[vert.index] = acc / total_w

        for vi, off in enumerate(offsets):
            if off is not None:
                mesh.vertices[vi].co += off

        # Shape keys: translation-only warp -> shift every key rigidly
        if mesh.shape_keys and mesh.shape_keys.key_blocks:
            for kb in mesh.shape_keys.key_blocks:
                for vi, off in enumerate(offsets):
                    if off is not None:
                        kb.data[vi].co += off

        mesh.update()

    # ---- Move bones to native positions ----
    bpy.context.view_layer.objects.active = armature_obj
    armature_obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = armature_obj.data.edit_bones
    for name, target in targets.items():
        eb = edit_bones.get(name)
        if eb is None:
            continue
        direction = eb.tail - eb.head
        eb.head = target
        eb.tail = target + direction
    # inherited bones (full-finger chains) shift rigidly by their ancestor's
    # delta, keeping their pose relative to the moved Hand
    for name, d in inherited.items():
        eb = edit_bones.get(name)
        if eb is None:
            continue
        eb.head = eb.head + d
        eb.tail = eb.tail + d
    bpy.ops.object.mode_set(mode='OBJECT')

    if operator is not None:
        operator.report({'INFO'},
                        "Fitted mesh to the native XML2 skeleton "
                        "(Max-style, exact animation compatibility)")
    print("[IGB Rig Converter] Native-skeleton fit applied "
          f"({len(deltas)} bones aligned)")


# ============================================================================
# Missing-Finger Placement
# ============================================================================
# When a model has no usable finger bones, the converter creates dummies.
# Placing them at the hand's TAIL (the old behavior) scatters them wherever
# the importer happened to point the hand bone. Instead, anchor them to the
# hand using the native XML2 skeleton's proportions: offsets below are the
# native finger positions relative to the Hand head, expressed as fractions
# of the native hand length (hand head -> Finger1 knuckle = 3.596 units),
# in a (along-hand, character-front, up) frame.

_FINGER_PLACEMENT_FRACTIONS = {
    # name suffix: (along, front, up) — thumb spreads forward, fingers
    # continue past the knuckles
    'Finger0':  (0.31, 0.61, 0.27),
    'Finger01': (0.70, 1.00, 0.27),
    'Finger1':  (1.00, 0.09, 0.00),
    'Finger11': (1.51, 0.09, 0.00),
}


def _place_missing_finger(eb, bone_name, edit_bones):
    """Place a newly-created finger bone relative to its side's Hand bone.

    Args:
        eb: The new edit bone.
        bone_name: Full XML2 name, e.g. 'Bip01 L Finger0'.
        edit_bones: armature.data.edit_bones.

    Returns:
        True if placed (head set), False if the hand bone is unusable.
    """
    from mathutils import Vector

    parts = bone_name.split(' ')  # ['Bip01', 'L', 'Finger0']
    if len(parts) != 3:
        return False
    side, suffix = parts[1], parts[2]
    fractions = _FINGER_PLACEMENT_FRACTIONS.get(suffix)
    if fractions is None:
        return False

    hand = edit_bones.get(f'Bip01 {side} Hand')
    if hand is None:
        return False
    along = hand.tail - hand.head
    hand_len = along.length
    if hand_len < 1e-5:
        return False
    along = along / hand_len

    # Character-front approximation: imported humanoids face -Y in Blender.
    # Only used for dummy bones (the model had no real finger to convert),
    # so a wrong facing is cosmetic, not a skinning error.
    front = Vector((0.0, -1.0, 0.0))
    up = Vector((0.0, 0.0, 1.0))

    a, f, u = fractions
    eb.head = (hand.head + along * (a * hand_len)
               + front * (f * hand_len) + up * (u * hand_len))
    eb.tail = eb.head + along * max(0.25 * hand_len, 0.001)
    return True


# ============================================================================
# Transform Application & Auto-Scale
# ============================================================================

# The game's animations drive the Pelvis/Bip01 with ABSOLUTE translations
# authored for the native skeleton (pelvis at ~41.82 game units). A model
# whose scaled pelvis sits anywhere else shifts by the difference whenever
# an animation plays: floating in the character select, sinking into the
# ground on jumps. Matching the PELVIS height (not the total height) to
# native is what keeps feet on the ground.
_NATIVE_PELVIS_Z = NATIVE_TRANSLATIONS[1][2]  # 41.8195 game units


def _apply_transforms_and_scale(armature_obj, target_height=68.0,
                                auto_scale=True, pelvis_bone='Bip01 Pelvis'):
    """Compute and store an export scale factor for XML2 proportions.

    Instead of modifying Blender data (which can break parent-child
    relationships and Armature modifiers), this stores a scale factor
    as a custom property on the armature. The skin exporter applies
    it at export time so the IGB file is at the correct scale.

    Scaling anchors the PELVIS at EXACTLY the native game height (~41.82
    units). This is not negotiable for animation compatibility: decoding
    the game's own animations shows they drive Bip01 with ABSOLUTE Z values
    (idle: 38.0-39.3, jump_land: 24.3-36.4) authored for the native
    skeleton — any other pelvis height makes the body float or sink
    whenever those animations play. To make a character bigger/smaller
    in-game, use the herostat 'scale_factor' attribute (the engine scales
    animation translations along with it, preserving ground contact).

    target_height is only used by the no-pelvis fallback (total height).

    Args:
        armature_obj: Blender armature object.
        target_height: Fallback total height when no pelvis bone exists.
        auto_scale: If True, compute and store scale factor.
        pelvis_bone: Name of the pelvis bone (call AFTER rename).
    """
    if not auto_scale or target_height <= 0:
        return

    from mathutils import Vector

    scale_factor = None

    # --- Primary: anchor pelvis to exact native game height ---
    pelvis = armature_obj.data.bones.get(pelvis_bone)
    if pelvis is not None:
        pelvis_world_z = (armature_obj.matrix_world
                          @ pelvis.head_local).z
        if pelvis_world_z > 1e-5:
            scale_factor = _NATIVE_PELVIS_Z / pelvis_world_z

    # --- Fallback: total visual height from mesh world bounding boxes ---
    if scale_factor is None:
        min_z = float('inf')
        max_z = float('-inf')
        for mesh_obj in _get_skinned_meshes(armature_obj):
            mw = mesh_obj.matrix_world
            for corner in mesh_obj.bound_box:
                world_z = (mw @ Vector(corner)).z
                min_z = min(min_z, world_z)
                max_z = max(max_z, world_z)

        if min_z < max_z:
            scale_factor = target_height / (max_z - min_z)
        else:
            # Last resort: bone Z range x object scale
            obj_scale_z = (abs(armature_obj.scale.z)
                           if armature_obj.scale.z != 0 else 1.0)
            for bone in armature_obj.data.bones:
                for vec in (bone.head_local, bone.tail_local):
                    min_z = min(min_z, vec.z)
                    max_z = max(max_z, vec.z)
            if min_z >= max_z:
                return
            scale_factor = target_height / ((max_z - min_z) * obj_scale_z)

    # Only store if significantly different (>10 % off)
    if abs(scale_factor - 1.0) <= 0.1:
        return

    # Store on the armature — the export pipeline will apply this
    armature_obj["igb_export_scale"] = scale_factor


# ============================================================================
# Pose Detection & Reposing
# ============================================================================

# Arm-chain bones that need reposing when converting between A-pose and T-pose.
# These are the bones whose rest orientation changes between the two poses.
ARM_CHAIN_BONES = {
    "Bip01 L Clavicle", "Bip01 L UpperArm", "Bip01 L Forearm", "Bip01 L Hand",
    "Bip01 L Finger0", "Bip01 L Finger01", "Bip01 L Finger1", "Bip01 L Finger11",
    "Bip01 R Clavicle", "Bip01 R UpperArm", "Bip01 R Forearm", "Bip01 R Hand",
    "Bip01 R Finger0", "Bip01 R Finger01", "Bip01 R Finger1", "Bip01 R Finger11",
}


def _detect_source_pose(armature_obj, rename_map=None):
    """Detect whether the source rig is in T-pose or A-pose.

    Measures the elevation angle of UpperArm bones relative to the horizontal
    plane.  Uses PRE-rename bone names (the original Unity/Mixamo names) via
    the rename_map to find the UpperArm bones before they've been renamed.

    Args:
        armature_obj: Blender armature object (before rename).
        rename_map: Dict mapping old_name -> xml2_name.  If None, assumes
                    bones already have XML2 names.

    Returns:
        'T_POSE' if arms are nearly horizontal (< 15° from XY plane),
        'A_POSE' if arms are angled down (25-65° below horizontal),
        'UNKNOWN' if detection fails or angle is ambiguous.
    """
    import math

    # Build reverse map: xml2_name -> current_bone_name
    if rename_map:
        reverse = {v: k for k, v in rename_map.items()}
    else:
        reverse = {}

    angles = []
    for side_upper, side_fore in [
        ("Bip01 L UpperArm", "Bip01 L Forearm"),
        ("Bip01 R UpperArm", "Bip01 R Forearm"),
    ]:
        # Resolve to current bone names
        upper_name = reverse.get(side_upper, side_upper)
        fore_name = reverse.get(side_fore, side_fore)

        upper_bone = armature_obj.data.bones.get(upper_name)
        fore_bone = armature_obj.data.bones.get(fore_name)

        if upper_bone is None:
            continue

        # Arm direction: upperarm head -> forearm head (or upperarm tail)
        if fore_bone is not None:
            arm_dir = fore_bone.head_local - upper_bone.head_local
        else:
            arm_dir = upper_bone.tail_local - upper_bone.head_local

        if arm_dir.length < 0.0001:
            continue

        # Measure in WORLD space — Max/FBX armatures carry a 90°X object
        # rotation, so a 44°-drooped arm reads ~0° in local space and the
        # A-pose goes undetected (no repose -> the native fit shears the
        # still-drooped arm into a zigzag).
        arm_dir = armature_obj.matrix_world.to_quaternion() @ arm_dir

        # Elevation angle from horizontal (XY plane in Blender Z-up)
        horizontal_len = math.sqrt(arm_dir.x ** 2 + arm_dir.y ** 2)
        elevation = math.degrees(math.atan2(abs(arm_dir.z), horizontal_len))
        angles.append(elevation)

    if not angles:
        return 'UNKNOWN'

    avg_angle = sum(angles) / len(angles)

    if avg_angle < 15.0:
        return 'T_POSE'
    elif 25.0 <= avg_angle <= 65.0:
        return 'A_POSE'
    else:
        return 'UNKNOWN'


def _repose_meshes(armature_obj, source_pose, target_pose):
    """Repose mesh vertices and shape keys from source pose to target pose.

    When source_pose != target_pose, this rotates the arm-chain bones in pose
    mode to match the target, then manually computes deformed vertex positions
    (because VRChat models have 100-200+ shape keys which prevent using
    bpy.ops.object.modifier_apply()).

    The algorithm:
    1. Measure the actual arm angle from the rest pose
    2. Compute the delta rotation needed to reach the target angle
    3. Set pose bone rotations for the arm chain
    4. For each child mesh:
       a. Compute deformed base mesh positions via weighted bone transforms
       b. Transform shape key offsets by per-vertex weighted rotation
       c. Write new positions back
    5. Apply pose as new rest pose on the armature

    Args:
        armature_obj: Blender armature (already renamed to XML2 bones).
        source_pose: 'T_POSE' or 'A_POSE'.
        target_pose: 'T_POSE' or 'A_POSE'.
    """
    import bpy
    import math
    from mathutils import Matrix, Quaternion, Vector

    if source_pose == target_pose:
        return

    # --- 1. Measure actual arm angle and compute delta ---
    # T-pose = 0° elevation, A-pose = ~45° elevation below horizontal.
    # We measure the actual angle rather than assuming exact 45°.
    arm_bones_info = []  # (bone_name, measured_angle, side)
    for side, upper_name, fore_name in [
        ('L', "Bip01 L UpperArm", "Bip01 L Forearm"),
        ('R', "Bip01 R UpperArm", "Bip01 R Forearm"),
    ]:
        upper_bone = armature_obj.data.bones.get(upper_name)
        fore_bone = armature_obj.data.bones.get(fore_name)
        if upper_bone is None:
            continue

        if fore_bone is not None:
            arm_dir = fore_bone.head_local - upper_bone.head_local
        else:
            arm_dir = upper_bone.tail_local - upper_bone.head_local

        if arm_dir.length < 0.0001:
            continue

        # WORLD space (rotated FBX armature objects — see _detect_source_pose)
        arm_dir = armature_obj.matrix_world.to_quaternion() @ arm_dir

        horizontal_len = math.sqrt(arm_dir.x ** 2 + arm_dir.y ** 2)
        elevation = math.atan2(arm_dir.z, horizontal_len)  # radians, negative = downward
        arm_bones_info.append((upper_name, elevation, side))

    if not arm_bones_info:
        return

    # Target elevation: T-pose = 0°, A-pose = -45° (arms angled down)
    if target_pose == 'T_POSE':
        target_elevation = 0.0
    else:
        target_elevation = math.radians(-45.0)

    # --- 2. Disconnect all bones so pose rotation works freely ---
    # Connected bones (use_connect=True) have their head locked to the
    # parent's tail, which prevents bpy.ops.pose.armature_apply() from
    # correctly applying the reposed positions as the new rest pose.
    bpy.context.view_layer.objects.active = armature_obj
    armature_obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    for eb in armature_obj.data.edit_bones:
        eb.use_connect = False
    bpy.ops.object.mode_set(mode='OBJECT')

    # --- 3. Enter pose mode and set rotations ---
    bpy.ops.object.mode_set(mode='POSE')

    # Clear all pose transforms first
    for pb in armature_obj.pose.bones:
        pb.rotation_mode = 'QUATERNION'
        pb.rotation_quaternion = Quaternion((1, 0, 0, 0))
        pb.location = Vector((0, 0, 0))
        pb.scale = Vector((1, 1, 1))

    # Apply delta rotation to each arm's UpperArm bone.
    # The rotation is around the bone's local "forward" axis (the axis
    # pointing outward from shoulder, which is roughly ±Y in Blender space
    # for a character facing -Y).
    for upper_name, current_elevation, side in arm_bones_info:
        delta_angle = target_elevation - current_elevation

        pb = armature_obj.pose.bones.get(upper_name)
        if pb is None:
            continue

        # The arm extends outward along bone Y. We want to rotate the arm
        # up/down, which is a rotation around the arm's outward axis.
        # In the bone's local space, Y is along the bone, so we rotate
        # around the bone-local Y axis (roll).
        # Actually, for arms extending to the side, the up/down rotation
        # is around the forward axis (which is bone-local X or Z depending
        # on the bone's roll).
        #
        # Simpler approach: compute the rotation axis in armature space.
        # The arm points outward (roughly ±Y). We want to pitch it up/down.
        # Rotation axis = arm direction cross up vector = forward/back axis.
        bone = armature_obj.data.bones[upper_name]
        # WORLD space throughout: the up vector must be world-up, and the
        # axis conversion must include the armature OBJECT rotation (Max/FBX
        # rigs are rotated 90°X at the object level).
        obj_rot = armature_obj.matrix_world.to_quaternion().to_matrix()
        arm_dir = obj_rot @ (bone.tail_local - bone.head_local)
        arm_dir.normalize()

        # Cross with world up to get the pitch axis
        up = Vector((0, 0, 1))
        rot_axis = arm_dir.cross(up)
        if rot_axis.length < 0.0001:
            continue
        rot_axis.normalize()

        # Convert the world axis into bone-local space (object rot included)
        bone_mat = obj_rot @ bone.matrix_local.to_3x3()
        try:
            local_axis = bone_mat.inverted() @ rot_axis
        except ValueError:
            continue
        local_axis.normalize()

        # Apply the delta as a quaternion in bone-local space
        delta_q = Quaternion(local_axis, delta_angle)
        pb.rotation_quaternion = delta_q

    # --- 4. Bake the pose into the meshes, then apply as rest pose ---
    _apply_current_pose_to_meshes(armature_obj)
    bpy.ops.pose.armature_apply()
    bpy.ops.object.mode_set(mode='OBJECT')


def _apply_current_pose_to_meshes(armature_obj):
    """Bake the armature's CURRENT pose into all skinned meshes' vertices.

    Computes per-vertex weighted bone transforms manually (VRChat models
    carry 100-200 shape keys, which rules out modifier_apply) and writes
    the deformed positions back, rotating shape key offsets to match.
    Leaves the pose itself untouched — callers follow with armature_apply.
    """
    import bpy
    from mathutils import Matrix, Vector

    # Force dependency graph update so pose matrices are current
    bpy.context.view_layer.update()

    for child in _get_skinned_meshes(armature_obj):
        mesh = child.data
        n_verts = len(mesh.vertices)

        # Find the armature modifier
        arm_mod = None
        for mod in child.modifiers:
            if mod.type == 'ARMATURE' and mod.object == armature_obj:
                arm_mod = mod
                break
        if arm_mod is None:
            continue

        # Pose matrices live in ARMATURE space but vert.co is MESH-local.
        # FBX rigs often parent the mesh with an inverse that cancels the
        # armature's 90°X object rotation (mesh world = identity, armature
        # world = rotated) — applying armature-space rotations directly to
        # mesh coords then rotates about the wrong axis and smears the limbs
        # into blades. Conjugate by the relative transform; identity when the
        # frames already match (every previously-working rig).
        try:
            to_arm = armature_obj.matrix_world.inverted() @ child.matrix_world
            to_mesh = to_arm.inverted()
        except ValueError:
            to_arm = Matrix.Identity(4)
            to_mesh = Matrix.Identity(4)
        frames_differ = any(abs(to_arm[r][c] - (1.0 if r == c else 0.0)) > 1e-6
                            for r in range(4) for c in range(4))

        # Precompute per-bone transform: pose_bone.matrix @ rest_bone.matrix_local.inverted()
        bone_transforms = {}
        for pb in armature_obj.pose.bones:
            bone = pb.bone
            try:
                rest_inv = bone.matrix_local.inverted()
            except ValueError:
                rest_inv = Matrix.Identity(4)
            bt = pb.matrix @ rest_inv
            if frames_differ:
                bt = to_mesh @ bt @ to_arm
            bone_transforms[pb.name] = bt

        # Precompute per-vertex weighted rotation (for shape key offsets)
        # and deformed positions (for base mesh)
        new_positions = [None] * n_verts
        per_vertex_matrices = [None] * n_verts

        for vi, vert in enumerate(mesh.vertices):
            # Gather bone weights for this vertex
            weighted_mat = Matrix.Identity(4) * 0  # zero matrix
            total_weight = 0.0

            for g in vert.groups:
                vg = child.vertex_groups[g.group]
                bt = bone_transforms.get(vg.name)
                if bt is None:
                    continue
                w = g.weight
                if w < 0.0001:
                    continue
                for r in range(4):
                    for c in range(4):
                        weighted_mat[r][c] += bt[r][c] * w
                total_weight += w

            if total_weight < 0.0001:
                # No valid bone weights — keep original position
                new_positions[vi] = vert.co.copy()
                per_vertex_matrices[vi] = Matrix.Identity(4)
                continue

            # Normalize
            for r in range(4):
                for c in range(4):
                    weighted_mat[r][c] /= total_weight

            per_vertex_matrices[vi] = weighted_mat
            new_positions[vi] = weighted_mat @ vert.co

        # Write deformed positions to base mesh
        for vi, pos in enumerate(new_positions):
            if pos is not None:
                mesh.vertices[vi].co = pos

        # Transform shape key offsets
        if mesh.shape_keys and mesh.shape_keys.key_blocks:
            basis = mesh.shape_keys.key_blocks[0]
            basis_coords = [Vector(v.co) for v in basis.data]

            # Update basis to match new deformed positions
            for vi, pos in enumerate(new_positions):
                if pos is not None:
                    basis.data[vi].co = pos

            # For each non-basis shape key, rotate the offset vector
            for sk in mesh.shape_keys.key_blocks[1:]:
                for vi in range(n_verts):
                    original_offset = Vector(sk.data[vi].co) - basis_coords[vi]
                    if original_offset.length < 0.00001:
                        # Zero offset — just update to new basis
                        sk.data[vi].co = new_positions[vi]
                        continue

                    # Rotate the offset by the per-vertex weighted rotation
                    mat = per_vertex_matrices[vi]
                    rot_mat = mat.to_3x3()
                    rotated_offset = rot_mat @ original_offset
                    sk.data[vi].co = new_positions[vi] + rotated_offset

        mesh.update()


# ============================================================================
# Universal T-Pose (pose-match the native bind)
# ============================================================================
# Each bone's "direction" is defined by the chain: bone head -> child head.
# Rotating the LIMB chains to the native bind directions puts arms, legs,
# hands, thumb, and fingers into the exact pose the game's animations were
# authored against. Subsumes the old arm-only A->T repose and fixes
# finger/thumb misalignment from models with curled or spread bind poses.
#
# Torso chains (Pelvis->Spine->...->Head) are deliberately EXCLUDED: the
# native spine offsets are tiny diagonal vectors (Pelvis->Spine is 0.31
# units with a forward lean), so aligning a normal upright rig to them
# tilts the whole body and bakes the distortion into the mesh.

_NATIVE_CHAIN_CHILD = {}
for _s in ('L', 'R'):
    # Clavicles also excluded — shoulder roots vary too much across rigs;
    # the arm's T-pose level comes from the UpperArm chain.
    _NATIVE_CHAIN_CHILD.update({
        f'Bip01 {_s} UpperArm': f'Bip01 {_s} Forearm',
        f'Bip01 {_s} Forearm': f'Bip01 {_s} Hand',
        f'Bip01 {_s} Hand': f'Bip01 {_s} Finger1',
        f'Bip01 {_s} Finger0': f'Bip01 {_s} Finger01',
        f'Bip01 {_s} Finger1': f'Bip01 {_s} Finger11',
        f'Bip01 {_s} Thigh': f'Bip01 {_s} Calf',
        f'Bip01 {_s} Calf': f'Bip01 {_s} Foot',
        f'Bip01 {_s} Foot': f'Bip01 {_s} Toe0',
    })


def _pose_match_native_bind(armature_obj, target_game='XML2'):
    """Rotate every bone chain to the native bind direction (universal T-pose).

    Walks the hierarchy parent-first; for each mapped bone, rotates its pose
    (pivot at the bone head) so the direction to its chain child matches the
    native bind direction. Leaf bones (Head, Toe0, finger tips) follow their
    parents automatically. The pose is then baked into the meshes (weighted,
    shape-key aware) and applied as the new rest pose.

    Models already in clean T-pose see near-zero rotations (no-op).
    """
    import bpy
    import math
    from mathutils import Matrix, Quaternion, Vector

    # Native joint positions in armature space (directions only — overall
    # scale is irrelevant, but reuse the standard conversion)
    arm_rot = armature_obj.matrix_world.to_quaternion().to_matrix()
    rz90 = Quaternion((0, 0, 1), math.radians(90)).to_matrix()
    game_to_arm = (rz90 @ arm_rot).inverted()

    native_pos = {}
    for name, p_game in _native_game_positions(target_game).items():
        native_pos[name] = game_to_arm @ Vector(p_game)

    target_dirs = {}
    chain_child = dict(_NATIVE_CHAIN_CHILD)
    for bone_name, child_name in _NATIVE_CHAIN_CHILD.items():
        a = native_pos.get(bone_name)
        b = native_pos.get(child_name)
        if a is None or b is None:
            continue
        d = b - a
        if d.length > 1e-6:
            target_dirs[bone_name] = d.normalized()

    # FULL-FINGER rigs (Bip01_{S}_Finger{0-4}_{0-2}) have no native positions,
    # so without these aliases the Hand's chain child ('Bip01 {S} Finger1') is
    # missing and the hand NEVER rotates — the whole hand+fingers keep the
    # source's drooping curl while the arm straightens (blade-hands), and the
    # droop bakes against the native-oriented bind. Align the Hand via the
    # middle-finger proximal, and straighten each finger chain along the
    # native thumb/finger directions.
    bones = armature_obj.data.bones
    for s in ('L', 'R'):
        if f'Bip01_{s}_Finger2_0' not in bones:
            continue
        hand = f'Bip01 {s} Hand'
        if f'Bip01 {s} Finger1' not in bones:
            chain_child[hand] = f'Bip01_{s}_Finger2_0'
        t_thumb = None
        t_fing = None
        a, b = native_pos.get(f'Bip01 {s} Finger0'), \
            native_pos.get(f'Bip01 {s} Finger01')
        if a is not None and b is not None and (b - a).length > 1e-6:
            t_thumb = (b - a).normalized()
        a, b = native_pos.get(f'Bip01 {s} Finger1'), \
            native_pos.get(f'Bip01 {s} Finger11')
        if a is not None and b is not None and (b - a).length > 1e-6:
            t_fing = (b - a).normalized()
        for col in range(5):
            tdir = t_thumb if col == 0 else t_fing
            if tdir is None:
                continue
            for seg in (0, 1):
                bn = f'Bip01_{s}_Finger{col}_{seg}'
                cn = f'Bip01_{s}_Finger{col}_{seg + 1}'
                if bn in bones and cn in bones:
                    target_dirs[bn] = tdir
                    chain_child[bn] = cn

    if not target_dirs:
        return

    # Disconnect bones so pose rotations apply freely as a new rest pose
    bpy.context.view_layer.objects.active = armature_obj
    armature_obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    for eb in armature_obj.data.edit_bones:
        eb.use_connect = False
    bpy.ops.object.mode_set(mode='POSE')

    # Clear any existing pose
    for pb in armature_obj.pose.bones:
        pb.rotation_mode = 'QUATERNION'
        pb.rotation_quaternion = Quaternion((1, 0, 0, 0))
        pb.location = Vector((0, 0, 0))
        pb.scale = Vector((1, 1, 1))
    bpy.context.view_layer.update()

    def pose_depth(pb):
        d = 0
        p = pb.parent
        while p is not None:
            d += 1
            p = p.parent
        return d

    rotated = 0
    for pb in sorted(armature_obj.pose.bones, key=pose_depth):
        tdir = target_dirs.get(pb.name)
        if tdir is None:
            continue
        child_name = chain_child.get(pb.name)
        child_pb = (armature_obj.pose.bones.get(child_name)
                    if child_name else None)
        if child_pb is None:
            continue

        head = pb.matrix.translation.copy()
        current = child_pb.matrix.translation - head
        if current.length < 1e-6:
            continue

        delta = current.normalized().rotation_difference(tdir)
        if delta.angle < 0.001:
            continue

        # Rotate the pose bone about its own head (world/armature space)
        pivot = (Matrix.Translation(head)
                 @ delta.to_matrix().to_4x4()
                 @ Matrix.Translation(-head))
        pb.matrix = pivot @ pb.matrix
        rotated += 1
        # Children read updated parent matrices on the next iteration
        bpy.context.view_layer.update()

    if rotated == 0:
        bpy.ops.object.mode_set(mode='OBJECT')
        return

    # Bake into meshes and set as the new rest pose
    _apply_current_pose_to_meshes(armature_obj)
    bpy.ops.pose.armature_apply()
    bpy.ops.object.mode_set(mode='OBJECT')
    print(f"[IGB Rig Converter] Universal T-pose: aligned {rotated} "
          f"chains to the native bind")


def _align_thumbs_to_native(armature_obj, target_game='XML2'):
    """Rotate each thumb chain (mesh included) into the native thumb FRAME.

    The exported skeleton always uses native bind orientations, so game
    animations curl the thumb about the NATIVE flexion axis. Direction
    alignment alone leaves the thumb free to spin about its own axis
    (roll) — a mis-rolled thumb curls in the wrong plane in-game. Build an
    anatomical frame from (thumb direction, palm reference) in both the
    model and the native biped and rotate model -> native about the thumb
    root. Near no-op when the thumb already agrees (e.g. a clean Max-style
    rig or a manually fixed model).
    """
    import bpy
    import math
    from mathutils import Matrix, Quaternion, Vector

    arm_rot = armature_obj.matrix_world.to_quaternion().to_matrix()
    rz90 = Quaternion((0, 0, 1), math.radians(90)).to_matrix()
    game_to_arm_m = (rz90 @ arm_rot).inverted()

    native_pos = {name: game_to_arm_m @ Vector(p)
                  for name, p in _native_game_positions(target_game).items()}

    def frame_from(thumb_dir, palm_ref):
        t = thumb_dir.normalized()
        n = palm_ref.cross(t)
        if n.length < 1e-6:
            return None
        n.normalize()
        b = t.cross(n)
        m = Matrix((t, n, b))   # rows
        m.transpose()           # columns = frame axes
        return m

    bpy.context.view_layer.objects.active = armature_obj
    armature_obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    for eb in armature_obj.data.edit_bones:
        eb.use_connect = False
    bpy.ops.object.mode_set(mode='POSE')
    for pb in armature_obj.pose.bones:
        pb.rotation_mode = 'QUATERNION'
        pb.rotation_quaternion = Quaternion((1, 0, 0, 0))
        pb.location = Vector((0, 0, 0))
        pb.scale = Vector((1, 1, 1))
    bpy.context.view_layer.update()

    rotated = 0
    for s in ('L', 'R'):
        names = {k: f'Bip01 {s} {k}'
                 for k in ('Hand', 'Finger0', 'Finger01', 'Finger1')}
        pbs = {k: armature_obj.pose.bones.get(v) for k, v in names.items()}
        if (pbs['Hand'] is None or pbs['Finger0'] is None
                or pbs['Finger01'] is None):
            continue
        if any(native_pos.get(names[k]) is None
               for k in ('Hand', 'Finger0', 'Finger01', 'Finger1')):
            continue

        # ---- model anatomical frame (current pose, armature space) ----
        t_m = (pbs['Finger01'].matrix.translation
               - pbs['Finger0'].matrix.translation)
        if pbs['Finger1'] is not None:
            palm_m = (pbs['Finger1'].matrix.translation
                      - pbs['Hand'].matrix.translation)
        else:
            # fallback: hand bone direction (Blender bone Y = head->tail)
            palm_m = pbs['Hand'].y_axis.copy()
        if t_m.length < 1e-6 or palm_m.length < 1e-6:
            continue

        # ---- native anatomical frame (same construction) ----
        t_n = native_pos[names['Finger01']] - native_pos[names['Finger0']]
        palm_n = native_pos[names['Finger1']] - native_pos[names['Hand']]

        F_m = frame_from(t_m, palm_m.normalized())
        F_n = frame_from(t_n, palm_n.normalized())
        if F_m is None or F_n is None:
            continue

        R = F_n @ F_m.inverted()
        if R.to_quaternion().angle < 0.002:
            continue
        head = pbs['Finger0'].matrix.translation.copy()
        pivot = (Matrix.Translation(head) @ R.to_4x4()
                 @ Matrix.Translation(-head))
        pbs['Finger0'].matrix = pivot @ pbs['Finger0'].matrix
        bpy.context.view_layer.update()
        rotated += 1

        # ---- refine the distal segment toward the native Finger01 axis ----
        # Native bone +X (row 0 of the world rotation) points at the child;
        # the inverse bind stores the world rotation transposed.
        m01 = NATIVE_INV_JOINT_MATRICES.get(names['Finger01'])
        if m01 is not None:
            tgt = (game_to_arm_m
                   @ Vector((m01[0], m01[4], m01[8]))).normalized()
            child = (pbs['Finger01'].children[0]
                     if pbs['Finger01'].children else None)
            if child is not None:
                cur = (child.matrix.translation
                       - pbs['Finger01'].matrix.translation)
            else:
                cur = pbs['Finger01'].y_axis.copy()
            if cur.length > 1e-6:
                delta = cur.normalized().rotation_difference(tgt)
                if delta.angle > 0.002:
                    a = pbs['Finger01'].matrix.translation.copy()
                    pivot = (Matrix.Translation(a)
                             @ delta.to_matrix().to_4x4()
                             @ Matrix.Translation(-a))
                    pbs['Finger01'].matrix = pivot @ pbs['Finger01'].matrix
                    bpy.context.view_layer.update()

    if rotated == 0:
        bpy.ops.object.mode_set(mode='OBJECT')
        return

    _apply_current_pose_to_meshes(armature_obj)
    bpy.ops.pose.armature_apply()
    bpy.ops.object.mode_set(mode='OBJECT')
    print(f"[IGB Rig Converter] Thumb alignment: rotated {rotated} "
          f"thumb chain(s) into the native frame")


# ============================================================================
# Main Conversion Function
# ============================================================================

def convert_rig(armature_obj, profile='AUTO', auto_scale=True, target_height=68.0,
                target_pose='T_POSE', target_game='XML2',
                skeleton_mode='NATIVE', align_pose=False,
                character_size=1.0, full_fingers=False):
    """Convert an armature from Unity/Mixamo naming to XML2/MUA Bip01 convention.

    This is the main entry point. It:
    0. Applies object-level rotation/scale and optionally auto-scales
    1. Detects or uses the specified rig profile
    2. Renames bones and vertex groups
    3. Merges extra vertex weights into nearest mapped bones
    4. Removes unmapped bones
    5. Creates missing XML2 bones (+ FX bones for MUA)
    6. Detects source pose and reposes if needed
    6b. skeleton_mode='NATIVE': warps the mesh onto the exact native biped
        (Max-modder style — every animation behaves identically to official
        skins). 'KEEP' preserves the model's own proportions instead.
    7. Computes inverse joint matrices and bone translations
    8. Stores all required custom properties for IGB export

    Args:
        armature_obj: Blender armature object.
        profile: 'AUTO', 'UNITY', or 'MIXAMO'.
        auto_scale: If True, auto-scale rig to match XML2 character proportions.
        target_height: Target character height in game units (default ~80).
        target_pose: 'T_POSE' or 'A_POSE'. Target arm pose for export.
                     T_POSE recommended for XML2 animation compatibility.
        target_game: 'XML2' or 'MUA'. MUA adds 14 non-deforming FX bones.

    Returns:
        dict with keys: success (bool), mapped (int), added (int),
        removed (int), source_pose (str), error (str or None).
    """
    import bpy
    from mathutils import Matrix, Vector

    if armature_obj is None or armature_obj.type != 'ARMATURE':
        return {'success': False, 'error': "No armature selected", 'mapped': 0, 'added': 0, 'removed': 0}

    # Check if already converted
    if armature_obj.get("igb_converted_rig", False):
        return {'success': False,
                'error': "Rig is already converted to XML2. Use a fresh import.",
                'mapped': 0, 'added': 0, 'removed': 0}

    # (Auto-scale is computed AFTER the bone rename — pelvis-anchored
    # scaling needs 'Bip01 Pelvis' to exist. See step 7c below.)

    # ---- 0b. Rename Bip001/Bip002 → Bip01 if needed ----
    bip_prefix = _detect_bip_prefix(armature_obj)
    if bip_prefix and bip_prefix != "Bip01":
        _rename_bip_prefix(armature_obj, bip_prefix, "Bip01")

    # ---- 1. Detect profile ----
    if profile == 'AUTO':
        detected = detect_rig_profile(armature_obj)
        if detected is None:
            bone_names = [b.name for b in armature_obj.data.bones[:10]]
            return {'success': False,
                    'error': f"Could not detect rig type. Bones: {', '.join(bone_names)}... "
                             "Try running CATS 'Fix Model' first.",
                    'mapped': 0, 'added': 0, 'removed': 0}
        profile = detected
    else:
        profile = profile.lower()

    # Full fingers is a MUA-only feature (targets the 5-finger underscore hand
    # that MUA's Storm/Ultron/Loki movesets drive). Stored on the armature so
    # the skeleton-export-data builders below pick the matching variant.
    full_fingers = bool(full_fingers and target_game == 'MUA')
    armature_obj["igb_full_fingers"] = full_fingers

    # ---- 2. Build rename and merge maps (universal normalization) ----
    rename_map = build_rename_map(armature_obj, target_game=target_game,
                                  full_fingers=full_fingers)
    merge_map = build_merge_map(armature_obj, rename_map=rename_map,
                                target_game=target_game,
                                full_fingers=full_fingers)

    # ---- Console diagnostics (check the System Console for this) ----
    skinned_meshes = _get_skinned_meshes(armature_obj)
    print(f"[IGB Rig Converter] ===== {armature_obj.name} -> {target_game} =====")
    print(f"[IGB Rig Converter] Skinned meshes found: "
          f"{[m.name for m in skinned_meshes]}")
    direct_children = {c.name for c in armature_obj.children_recursive
                       if c.type == 'MESH'}
    modifier_only = [m.name for m in skinned_meshes
                     if m.name not in direct_children]
    if modifier_only:
        print(f"[IGB Rig Converter] NOTE: {len(modifier_only)} mesh(es) found "
              f"via Armature modifier (not parented to armature): "
              f"{modifier_only}")
    print(f"[IGB Rig Converter] Renames ({len(rename_map)}):")
    for old, new in sorted(rename_map.items(), key=lambda kv: kv[1]):
        print(f"    {old}  ->  {new}")
    print(f"[IGB Rig Converter] Weight merges ({len(merge_map)}):")
    by_target = {}
    for src, tgt in merge_map.items():
        by_target.setdefault(tgt, []).append(src)
    for tgt in sorted(by_target):
        print(f"    -> {tgt}:  {', '.join(sorted(by_target[tgt]))}")

    if not rename_map:
        bone_names = [b.name for b in armature_obj.data.bones[:10]]
        return {'success': False,
                'error': f"No bones matched any known convention. "
                         f"Bones: {', '.join(bone_names)}...",
                'mapped': 0, 'added': 0, 'removed': 0}

    # ---- 2b. Detect source pose (before rename, using rename_map) ----
    source_pose = _detect_source_pose(armature_obj, rename_map)

    # ---- 2c. Capture the source weighting NOW, before any weight op ----
    # These meshes were skinned by the (completed) import operator, so their
    # weight data is settled and reads accurately. Every weight op from here
    # on (merge / repose / round-trip) runs inside the current operator,
    # where Blender's per-vertex weight cache does not flush until the
    # operator returns — so any later in-operator recount under-reports.
    # The pipeline is weight-preserving, so this ratio is the true exported
    # weighting; the round-trip stamps it onto the final meshes.
    from .actor_import import _count_weighted_vertices
    _src_vt = sum(len(m.data.vertices) for m in skinned_meshes)
    _src_w = sum(_count_weighted_vertices(m) for m in skinned_meshes)
    armature_obj["igb_src_weight_ratio"] = _src_w / max(_src_vt, 1)

    # ---- 3. Merge vertex weights for extra bones ----
    # Build reverse rename map: xml2_name -> current_bone_name
    # so merge targets resolve to the CURRENT vertex group name.
    # e.g., "Bip01 L Finger1" -> "MiddleFinger1_L" (current VG name)
    reverse_rename = {v: k for k, v in rename_map.items()}
    _merge_vertex_weights(armature_obj, merge_map, reverse_rename)

    # ---- 3b. Weight verts the SOURCE left unskinned ----
    # Game rips often ship rigid accessory geometry with NO weights (the
    # X-Men '97 Cyclops leaves 11% of verts — visor etc. — unweighted). In
    # XML2/MUA those verts would stay frozen at bind pose while the body
    # moves. Copy each unweighted vert's weights from its nearest WEIGHTED
    # vertex so accessories follow the body part they sit on.
    _fix_unweighted_verts(armature_obj)

    # ---- 4. Rename vertex groups on all child meshes BEFORE bone rename ----
    # (vertex groups reference bones by name)
    _rename_vertex_groups(armature_obj, rename_map)

    # Also remove vertex groups for bones being deleted (not in rename or merge)
    target_skeleton = get_skeleton_for_game(target_game,
                                            full_fingers=full_fingers)
    all_target_names = {entry[0] for entry in target_skeleton if entry[0]}
    bones_to_remove = set()
    for bone in armature_obj.data.bones:
        if bone.name not in rename_map:
            bones_to_remove.add(bone.name)

    _remove_vertex_groups(armature_obj, bones_to_remove)

    # ---- 5. Enter edit mode: rename, remove, create bones ----
    # Ensure armature is active and selected
    bpy.context.view_layer.objects.active = armature_obj
    armature_obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')

    edit_bones = armature_obj.data.edit_bones
    mapped_count = 0
    removed_count = 0

    # CRITICAL: Disconnect ALL bones before any reparenting.
    # When use_connect=True and we change a bone's parent, Blender SNAPS
    # the bone's head to the new parent's tail — destroying the correct
    # position.  Native XML2 bones are never connected, so this is also
    # structurally correct.
    for eb in edit_bones:
        eb.use_connect = False

    # Rename mapped bones
    for old_name, new_name in rename_map.items():
        eb = edit_bones.get(old_name)
        if eb:
            eb.name = new_name
            mapped_count += 1

    # Remove unmapped bones (those not renamed to a target skeleton name)
    current_names = {eb.name for eb in edit_bones}
    bones_to_delete = []
    for eb in edit_bones:
        if eb.name not in all_target_names and eb.name not in {"", "Bone_000"}:
            bones_to_delete.append(eb.name)

    for name in bones_to_delete:
        eb = edit_bones.get(name)
        if eb:
            edit_bones.remove(eb)
            removed_count += 1

    # ---- 6. Create missing target skeleton bones ----
    added_count = 0
    current_names = {eb.name for eb in edit_bones}

    # Build parent name lookup from target skeleton
    skel_by_name = {}
    for name, idx, parent_idx, bm_idx, flags in target_skeleton:
        parent_name = target_skeleton[parent_idx][0] if parent_idx >= 0 else None
        skel_by_name[name] = (idx, parent_idx, parent_name, bm_idx, flags)

    # Helper: compute a small bone length based on existing bones
    bone_lengths = [eb.length for eb in edit_bones if eb.length > 0.001]
    avg_bone_len = sum(bone_lengths) / len(bone_lengths) if bone_lengths else 0.05

    # Process in index order so parents are created before children
    for name, idx, parent_idx, bm_idx, flags in target_skeleton:
        if not name:
            display_name = "Bone_000"
        else:
            display_name = name

        if display_name in current_names:
            continue  # Already exists (was renamed from source bone)

        # Create the bone
        eb = edit_bones.new(display_name)
        small_len = avg_bone_len * 0.3  # dummy bones are small

        # Find parent bone
        if parent_idx >= 0:
            parent_bone_name = target_skeleton[parent_idx][0]
            if not parent_bone_name:
                parent_bone_name = "Bone_000"
            parent_eb = edit_bones.get(parent_bone_name)
            if parent_eb:
                eb.parent = parent_eb

                # Smart placement: place dummy at parent's TAIL (end)
                # so it chains naturally after existing bones
                eb.head = parent_eb.tail.copy()

                # Special cases for better visual placement
                finger_placed = False
                if name == "Bip01 Spine2":
                    # UpperChest: place between Chest (Spine1) and Neck
                    neck_eb = edit_bones.get("Bip01 Neck")
                    if neck_eb:
                        # Midpoint between parent tail and neck head
                        eb.head = (parent_eb.tail + neck_eb.head) * 0.5
                    # else stays at parent tail
                elif name.startswith("Bip01 Ponytail"):
                    # Ponytails: place behind head, extending upward
                    head_eb = edit_bones.get("Bip01 Head")
                    if head_eb:
                        eb.head = head_eb.tail.copy()
                        if name == "Bip01 Ponytail11":
                            # Second ponytail extends further
                            pony1 = edit_bones.get("Bip01 Ponytail1")
                            if pony1:
                                eb.head = pony1.tail.copy()
                elif " Finger" in name:
                    # Fingers: anchor to the hand bone using native XML2
                    # proportions (hand tail can point anywhere on imports)
                    finger_placed = _place_missing_finger(eb, name, edit_bones)
                elif name == "" or name == "Bip01":
                    # Root/Bip01: at origin
                    eb.head = Vector((0, 0, 0))
                elif name == "Motion":
                    # Motion: at origin
                    eb.head = Vector((0, 0, 0))

                if not finger_placed:
                    # Compute tail: extend in same direction as parent
                    parent_dir = (parent_eb.tail - parent_eb.head)
                    if parent_dir.length > 0.001:
                        eb.tail = eb.head + parent_dir.normalized() * small_len
                    else:
                        eb.tail = eb.head + Vector((0, small_len, 0))
            else:
                eb.head = Vector((0, 0, 0))
                eb.tail = Vector((0, small_len, 0))
        else:
            # Root bone (no parent)
            eb.head = Vector((0, 0, 0))
            eb.tail = Vector((0, small_len, 0))

        eb.use_connect = False
        added_count += 1
        current_names.add(display_name)

    # Fix parent relationships for ALL target skeleton bones (some renamed
    # ones may need re-parenting to match the target hierarchy)
    for name, idx, parent_idx, bm_idx, flags in target_skeleton:
        display_name = name if name else "Bone_000"
        eb = edit_bones.get(display_name)
        if not eb:
            continue

        if parent_idx >= 0:
            parent_name = target_skeleton[parent_idx][0]
            parent_display = parent_name if parent_name else "Bone_000"
            parent_eb = edit_bones.get(parent_display)
            if parent_eb and eb.parent != parent_eb:
                eb.parent = parent_eb

    # ---- 7. Position non-deforming structural bones ----
    # Bip01 must be co-located with Pelvis (native XML2 convention).
    # This ensures correct FK pivot for animations and proper head/optic
    # beam placement.  Motion bone anchors at ground level for walk cycles.
    pelvis_eb = edit_bones.get("Bip01 Pelvis")
    bip01_eb = edit_bones.get("Bip01")
    if pelvis_eb and bip01_eb:
        bip01_eb.head = pelvis_eb.head.copy()
        bip01_eb.tail = bip01_eb.head + Vector((0, 0, avg_bone_len * 0.3))

    motion_eb = edit_bones.get("Motion")
    if motion_eb and pelvis_eb:
        motion_eb.head = Vector((pelvis_eb.head.x, pelvis_eb.head.y, 0.0))
        motion_eb.tail = motion_eb.head + Vector((0, avg_bone_len * 0.3, 0))

    bpy.ops.object.mode_set(mode='OBJECT')

    # ---- 7c. Auto-scale (pelvis-anchored, now that Bip01 Pelvis exists) ----
    # Must happen before step 8 — _force_native_root_translations reads
    # igb_export_scale when forcing Bip01/Motion to native heights.
    _apply_transforms_and_scale(armature_obj, target_height, auto_scale)

    # ---- 7c2. Character size ----
    # Scales the body (mesh + deforming bones) while Bip01/Motion stay at
    # native values (the root forcing divides by the composed scale, which
    # now includes this factor — so anims still drive the roots natively).
    # The Pelvis offset automatically becomes -41.82*(1-s), keeping feet
    # planted at bind. Deep-crouch animations deviate slightly for s far
    # from 1.0 (anim heights are offset, not multiplied — engine-side
    # herostat scale_factor is still the exact method).
    if character_size and abs(character_size - 1.0) > 0.001:
        current = armature_obj.get("igb_export_scale", 1.0)
        if isinstance(current, (list, tuple)):
            current = 1.0
        armature_obj["igb_export_scale"] = current * character_size
        print(f"[IGB Rig Converter] Character size {character_size:.2f} "
              f"(body scaled, roots stay native)")

    # ---- 7b. Pose alignment ----
    # KEEP_POSE: leave the model exactly as imported — no reposing at all.
    # T-pose target with align_pose: pose-match the LIMB chains (arms, legs,
    # hands, fingers, thumb) to the native bind directions. Subsumes the old
    # arm-only A->T repose and fixes thumb/finger misalignment from models
    # with curled or spread bind poses. Near no-op for clean T-poses.
    # A-pose target (or align_pose off): legacy arm-elevation repose only.
    if target_pose == 'KEEP_POSE':
        # Don't repose — but DO disconnect bones, like every other pose
        # path. Connected bones lock their head to the parent's tail, so
        # the later bone-display tail update (and translation computation)
        # would drag connected children and corrupt the skeleton. Pure
        # connection toggle: bone positions are unchanged.
        import bpy
        bpy.context.view_layer.objects.active = armature_obj
        armature_obj.select_set(True)
        bpy.ops.object.mode_set(mode='EDIT')
        for eb in armature_obj.data.edit_bones:
            eb.use_connect = False
        bpy.ops.object.mode_set(mode='OBJECT')
        print("[IGB Rig Converter] Keep Current Pose: no reposing, no "
              "native-biped fit — exporting the model's imported pose as-is")
    elif target_pose == 'T_POSE' and (align_pose
                                      or (source_pose != 'UNKNOWN'
                                          and source_pose != 'T_POSE')):
        # A-posed source, T-pose target: use the universal chain-direction
        # pose-match — per-bone rotations toward the native bind, object-
        # rotation aware, proven across rig families. The cruder single-axis
        # _repose_meshes swept rotated-object FBX arms out of plane (zigzag
        # blades on the X-Men '97 Cyclops).
        _pose_match_native_bind(armature_obj, target_game=target_game)
    elif source_pose != 'UNKNOWN' and source_pose != target_pose:
        _repose_meshes(armature_obj, source_pose, target_pose)

    # ---- 7c. Thumb frame alignment (always) ----
    # Thumbs are the one chain whose ROLL matters as much as direction:
    # native anims curl the thumb about the native flexion axis, so the
    # mesh must sit in the native thumb frame. Direction+roll matched via
    # the palm plane; near no-op for already-correct rigs.
    if target_pose == 'T_POSE':
        _align_thumbs_to_native(armature_obj, target_game=target_game)

    # ---- 7d. Native-skeleton fit (Max-modder style) ----
    # Warp mesh + bones onto the exact native biped so the exported
    # skeleton matches official skins bit-for-bit. Runs after repose
    # (mesh is final) and after auto-scale (needs igb_export_scale).
    #
    # The native biped is a T-POSE skeleton, so this fit only makes sense
    # for T_POSE targets. For A_POSE it would warp the arms straight back to
    # horizontal — silently undoing the A-pose (the long-standing "A-pose
    # setup does nothing" bug). A_POSE therefore always keeps the model's
    # own skeleton/proportions (KEEP behaviour), regardless of skeleton_mode.
    # Full-finger rigs USED to skip the fit (no native full-finger positions →
    # the Hand moved but the fingers stayed → wrist tear). That left the whole
    # skeleton NON-native, and the export/reimport round-trip reconstructs
    # non-native skeletons wrongly (arms at leg height, no T-pose). The fit
    # now carries unfitted bones rigidly with their nearest fitted ancestor
    # (fingers ride the Hand), so it is safe — and necessary — for both modes.
    if skeleton_mode == 'NATIVE' and target_pose == 'T_POSE':
        _fit_skeleton_to_native(armature_obj, target_game=target_game)
    elif skeleton_mode == 'NATIVE' and target_pose == 'A_POSE':
        print("[IGB Rig Converter] A-pose target: skipping native-biped fit "
              "(it is T-pose only) — keeping the model's own A-pose skeleton")

    # ---- 8. Compute inverse joint matrices from rest pose ----
    # T-pose target: use hardcoded NATIVE_BONE_ROTATIONS (animation compat)
    # A-pose target: compute rotations from actual bone orientations
    # Pass the ACTUAL target table explicitly — igb_target_game isn't stamped
    # on the armature yet, and the default resolver would fall back to the
    # reduced XML2 table (the full-finger bm-shift bug).
    use_native = (target_pose == 'T_POSE')
    export_skeleton = get_skeleton_for_game(target_game,
                                            full_fingers=full_fingers)
    inv_matrices = _compute_inv_joint_matrices(armature_obj, use_native,
                                               skeleton=export_skeleton)

    # ---- 9. Compute bone translations ----
    translations = _compute_bone_translations(armature_obj, use_native,
                                              skeleton=export_skeleton)

    # ---- 9b. Force non-deforming root bones to native translations ----
    # Bip01 Z must be exactly ~41.82 game units for correct menu placement,
    # head tracking, and animation compatibility. MUST divide by the same
    # composed scale the exporter multiplies with (custom x object scale).
    _force_native_root_translations(translations,
                                    _total_export_scale(armature_obj))

    # ---- 10. Store all custom properties ----
    _store_skeleton_properties(armature_obj, inv_matrices, translations,
                               target_game=target_game)

    # Store target game for export pipeline
    armature_obj["igb_target_game"] = target_game

    # Determine if axis rotation is needed during export.
    # If bones were identity-renamed (Bip01 Pelvis → Bip01 Pelvis), the rig
    # was already in XML2 orientation and does NOT need the +90° Z rotation.
    # If bones were actually renamed (Hips → Bip01 Pelvis), the rig came from
    # a non-XML2 convention and needs the rotation.
    actually_renamed = sum(1 for old, new in rename_map.items() if old != new)
    armature_obj["igb_converted_rig"] = actually_renamed > 0

    # ---- 11. Update bone display to show XML2 orientations ----
    # Set bone tails based on native XML2 rotations so the skeleton
    # visually reflects the game's bone layout in the Blender viewport.
    # This is purely cosmetic — all export data is already stored above.
    _update_bone_display(armature_obj)

    return {
        'success': True,
        'mapped': mapped_count,
        'added': added_count,
        'removed': removed_count,
        'source_pose': source_pose,
        'error': None,
    }


def setup_bip01_rig(armature_obj, auto_scale=True, target_height=68.0,
                    target_game='XML2'):
    """Setup an existing Bip01 rig for IGB skin export.

    Unlike convert_rig(), this does NOT rename or reparent bones — it assumes
    the armature already uses XML2 bone naming (Bip01, Bip01 Pelvis, etc.).
    It creates any missing XML2 bones (+ FX bones for MUA), fixes the parent
    hierarchy, computes inverse joint matrices and bone translations, and
    stores all required custom properties for the skin exporter.

    Use this for:
    - Native XML2 rigs from other importers or manual creation
    - Rigs that already have Bip01 naming but lack IGB export properties
    - Re-setting up a rig whose properties were lost or need refreshing

    Args:
        armature_obj: Blender armature object with XML2 bone names.
        auto_scale: If True, auto-scale rig to match XML2 character proportions.
        target_height: Target character height in game units (default ~68).
        target_game: 'XML2' or 'MUA'. MUA adds 14 non-deforming FX bones.

    Returns:
        dict with keys: success (bool), added (int), error (str or None).
    """
    import bpy
    from mathutils import Vector

    if armature_obj is None or armature_obj.type != 'ARMATURE':
        return {'success': False, 'added': 0,
                'error': "No armature selected"}

    # Detect Biped prefix variant (Bip01, Bip001, etc.)
    bip_prefix = _detect_bip_prefix(armature_obj)
    if bip_prefix is None:
        return {'success': False, 'added': 0,
                'error': "Armature has no Bip01/Bip001 bones. "
                         "Use Convert Rig for non-XML2 rigs."}

    # ---- 0. Rename Bip001 → Bip01 if needed ----
    if bip_prefix != "Bip01":
        _rename_bip_prefix(armature_obj, bip_prefix, "Bip01")

    # ---- 0b. Apply object transforms and auto-scale ----
    _apply_transforms_and_scale(armature_obj, target_height, auto_scale)

    # ---- 1. Enter edit mode: create missing bones, fix hierarchy ----
    bpy.context.view_layer.objects.active = armature_obj
    armature_obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')

    edit_bones = armature_obj.data.edit_bones
    added_count = 0
    newly_created = set()  # Track which bones we create (vs already existed)

    # Disconnect all bones (XML2 bones are never connected)
    for eb in edit_bones:
        eb.use_connect = False

    # Compute a reference bone length for dummies
    bone_lengths = [eb.length for eb in edit_bones if eb.length > 0.001]
    avg_bone_len = (sum(bone_lengths) / len(bone_lengths)
                    if bone_lengths else 0.05)

    # Create missing target skeleton bones (XML2 or MUA; full-finger variant
    # when the conversion flagged it on the armature)
    skeleton = get_skeleton_for_game(
        target_game, full_fingers=armature_obj.get('igb_full_fingers', False))
    current_names = {eb.name for eb in edit_bones}
    for name, idx, parent_idx, bm_idx, flags in skeleton:
        display_name = name if name else "Bone_000"
        if display_name in current_names:
            continue

        eb = edit_bones.new(display_name)
        small_len = avg_bone_len * 0.3

        if parent_idx >= 0:
            parent_bone_name = skeleton[parent_idx][0]
            if not parent_bone_name:
                parent_bone_name = "Bone_000"
            parent_eb = edit_bones.get(parent_bone_name)
            if parent_eb:
                eb.parent = parent_eb
                eb.head = parent_eb.tail.copy()

                # Smart placement for common bones
                finger_placed = False
                if name == "Bip01 Spine2":
                    neck_eb = edit_bones.get("Bip01 Neck")
                    if neck_eb:
                        eb.head = (parent_eb.tail + neck_eb.head) * 0.5
                elif name.startswith("Bip01 Ponytail"):
                    head_eb = edit_bones.get("Bip01 Head")
                    if head_eb:
                        eb.head = head_eb.tail.copy()
                        if name == "Bip01 Ponytail11":
                            pony1 = edit_bones.get("Bip01 Ponytail1")
                            if pony1:
                                eb.head = pony1.tail.copy()
                elif " Finger" in name:
                    finger_placed = _place_missing_finger(eb, name, edit_bones)

                if not finger_placed:
                    parent_dir = parent_eb.tail - parent_eb.head
                    if parent_dir.length > 0.001:
                        eb.tail = eb.head + parent_dir.normalized() * small_len
                    else:
                        eb.tail = eb.head + Vector((0, small_len, 0))
            else:
                eb.head = Vector((0, 0, 0))
                eb.tail = Vector((0, small_len, 0))
        else:
            eb.head = Vector((0, 0, 0))
            eb.tail = Vector((0, small_len, 0))

        eb.use_connect = False
        added_count += 1
        newly_created.add(display_name)
        current_names.add(display_name)

    # Fix parent hierarchy for ALL target skeleton bones
    for name, idx, parent_idx, bm_idx, flags in skeleton:
        display_name = name if name else "Bone_000"
        eb = edit_bones.get(display_name)
        if not eb:
            continue
        if parent_idx >= 0:
            parent_name = skeleton[parent_idx][0]
            parent_display = parent_name if parent_name else "Bone_000"
            parent_eb = edit_bones.get(parent_display)
            if parent_eb and eb.parent != parent_eb:
                eb.parent = parent_eb

    # Position structural bones — ONLY if they were newly created.
    # Existing bones (Bip01, Motion, Bone_000) are already in the correct
    # position and must NOT be moved.  Bip01 controls the model's facing
    # direction, and Motion / Bone_000 must stay at the origin.
    pelvis_eb = edit_bones.get("Bip01 Pelvis")

    if "Bip01" in newly_created:
        bip01_eb = edit_bones.get("Bip01")
        if pelvis_eb and bip01_eb:
            bip01_eb.head = pelvis_eb.head.copy()
            bip01_eb.tail = (bip01_eb.head
                             + Vector((0, 0, avg_bone_len * 0.3)))

    if "Motion" in newly_created:
        motion_eb = edit_bones.get("Motion")
        if motion_eb:
            motion_eb.head = Vector((0, 0, 0))
            motion_eb.tail = Vector((0, avg_bone_len * 0.3, 0))

    if "Bone_000" in newly_created:
        bone000_eb = edit_bones.get("Bone_000")
        if bone000_eb:
            bone000_eb.head = Vector((0, 0, 0))
            bone000_eb.tail = Vector((0, avg_bone_len * 0.3, 0))

    bpy.ops.object.mode_set(mode='OBJECT')

    # ---- 2. Compute and store skeleton data ----
    # Always use native T-pose rotations (NATIVE_BONE_ROTATIONS) since
    # existing Bip01 rigs are expected to be in T-pose orientation.
    use_native = True
    inv_matrices = _compute_inv_joint_matrices(armature_obj, use_native)
    translations = _compute_bone_translations(armature_obj, use_native)

    _force_native_root_translations(translations,
                                    _total_export_scale(armature_obj))

    _store_skeleton_properties(armature_obj, inv_matrices, translations,
                               target_game=target_game)

    # Store target game for export pipeline
    armature_obj["igb_target_game"] = target_game

    # CRITICAL: Override igb_converted_rig to False for existing Bip01 rigs.
    # _store_skeleton_properties() sets it to True (which tells the exporter
    # to apply +90° Z rotation to mesh vertices for Blender→game conversion).
    # But an existing Bip01 rig is already in the correct XML2 orientation —
    # the mesh does NOT need any rotation applied during export.
    armature_obj["igb_converted_rig"] = False

    # NOTE: Do NOT call _update_bone_display() here.  The bones are
    # already oriented correctly in the existing Bip01 rig — rewriting
    # tails would rotate them to match hardcoded XML2 orientations and
    # break the visual pose that's already correct.

    return {
        'success': True,
        'added': added_count,
        'error': None,
    }


def validate_bip01_rig(armature_obj):
    """Validate a Bip01 rig's skeleton data for export readiness.

    Read-only check — does not modify the armature. Returns a list of
    (level, message) tuples describing any issues found. An empty list
    means the rig is fully valid and ready for export.

    Levels: 'ERROR' (will break export), 'WARNING' (may cause issues).

    Args:
        armature_obj: Blender armature object to validate.

    Returns:
        list of (level, message) tuples.
    """
    issues = []

    if armature_obj is None or armature_obj.type != 'ARMATURE':
        return [('ERROR', "Not an armature object")]

    bones = armature_obj.data.bones

    # 1. Missing bones
    missing = []
    for name, idx, parent_idx, bm_idx, flags in XML2_SKELETON:
        display_name = name if name else "Bone_000"
        if display_name not in bones:
            missing.append(display_name)
    if missing:
        if len(missing) <= 3:
            issues.append(('ERROR', f"Missing bones: {', '.join(missing)}"))
        else:
            issues.append(('ERROR',
                           f"Missing {len(missing)} bones: "
                           f"{', '.join(missing[:3])}..."))

    # 2. Wrong parent hierarchy
    hierarchy_fixes = 0
    for name, idx, parent_idx, bm_idx, flags in XML2_SKELETON:
        display_name = name if name else "Bone_000"
        bone = bones.get(display_name)
        if bone is None:
            continue
        if parent_idx >= 0:
            expected_parent = XML2_SKELETON[parent_idx][0]
            if not expected_parent:
                expected_parent = "Bone_000"
            actual_parent = bone.parent.name if bone.parent else None
            if actual_parent != expected_parent:
                hierarchy_fixes += 1
    if hierarchy_fixes:
        issues.append(('WARNING',
                       f"{hierarchy_fixes} bone(s) have wrong parent"))

    # 3. Connected bones (XML2 requires all disconnected)
    connected = sum(1 for b in bones if b.use_connect)
    if connected:
        issues.append(('WARNING',
                       f"{connected} bone(s) are connected (should be disconnected)"))

    # 4. Missing armature-level properties
    required_props = [
        'igb_skin_bone_info_list',
        'igb_skin_inv_joint_matrices',
        'igb_skin_bone_translations',
        'igb_bms_palette',
    ]
    missing_props = [p for p in required_props if p not in armature_obj]
    if missing_props:
        issues.append(('ERROR',
                       f"Missing skeleton data: {', '.join(missing_props)}"))

    # 5. Stale bone counts
    bone_count = armature_obj.get("igb_bone_count", 0)
    joint_count = armature_obj.get("igb_skin_joint_count", 0)
    if bone_count != len(XML2_SKELETON):
        issues.append(('WARNING',
                       f"Bone count {bone_count} != expected {len(XML2_SKELETON)}"))
    if joint_count != XML2_JOINT_COUNT:
        issues.append(('WARNING',
                       f"Joint count {joint_count} != expected {XML2_JOINT_COUNT}"))

    # 6. Missing per-bone metadata
    missing_meta = 0
    for name, idx, parent_idx, bm_idx, flags in XML2_SKELETON:
        display_name = name if name else "Bone_000"
        if display_name in armature_obj.pose.bones:
            pb = armature_obj.pose.bones[display_name]
            if "igb_bone_index" not in pb:
                missing_meta += 1
    if missing_meta:
        issues.append(('WARNING',
                       f"{missing_meta} bone(s) missing IGB metadata"))

    # 7. Mesh children without Armature modifier
    for child in armature_obj.children:
        if child.type != 'MESH':
            continue
        has_mod = any(
            m.type == 'ARMATURE' and m.object == armature_obj
            for m in child.modifiers
        )
        if not has_mod:
            issues.append(('WARNING',
                           f"Mesh '{child.name}' has no Armature modifier"))

    return issues


# ============================================================================
# Vertex Group Operations
# ============================================================================

def _get_skinned_meshes(armature_obj):
    """Find ALL meshes skinned to an armature, not just direct children.

    FBX/glTF imports often parent meshes to an empty or scene root while
    skinning them via an Armature modifier. Walking armature_obj.children
    alone silently misses those meshes — weights then never rename or merge.

    Returns:
        List of mesh objects (children first, then modifier-linked).
    """
    import bpy

    meshes = []
    seen = set()

    for child in armature_obj.children_recursive:
        if child.type == 'MESH' and child.name not in seen:
            meshes.append(child)
            seen.add(child.name)

    for obj in bpy.data.objects:
        if obj.type != 'MESH' or obj.name in seen:
            continue
        for mod in obj.modifiers:
            if mod.type == 'ARMATURE' and mod.object == armature_obj:
                meshes.append(obj)
                seen.add(obj.name)
                break

    return meshes


def _rename_vertex_groups(armature_obj, rename_map):
    """Rename vertex groups on all skinned meshes according to rename_map."""
    for child in _get_skinned_meshes(armature_obj):
        for vg in child.vertex_groups:
            if vg.name in rename_map:
                vg.name = rename_map[vg.name]


def _remove_vertex_groups(armature_obj, bone_names_to_remove):
    """Remove vertex groups for deleted bones on all skinned meshes."""
    for child in _get_skinned_meshes(armature_obj):
        to_remove = [vg for vg in child.vertex_groups if vg.name in bone_names_to_remove]
        for vg in to_remove:
            child.vertex_groups.remove(vg)


def _merge_vertex_weights(armature_obj, merge_map, reverse_rename=None):
    """Merge vertex weights from extra bones into their target bones.

    For each vertex, adds the weight from the source bone's vertex group
    to the target bone's vertex group, then removes the source group.

    CRITICAL: merge_map targets use XML2 names (e.g., "Bip01 L Finger1"),
    but vertex groups still use the original names (e.g., "MiddleFinger1_L").
    reverse_rename resolves XML2 names back to current VG names.

    Args:
        armature_obj: Blender armature object.
        merge_map: Dict mapping source_bone_name -> target_xml2_name.
        reverse_rename: Dict mapping xml2_name -> current_bone_name (optional).
    """
    import bpy

    if not merge_map:
        return
    if reverse_rename is None:
        reverse_rename = {}

    for child in _get_skinned_meshes(armature_obj):
        mesh = child.data

        # Resolve all source groups -> target group NAMES up front, creating
        # missing target groups. Names (not references/indices) are tracked
        # because vertex_groups.new() can reallocate the collection.
        src_index_to_target = {}   # src vg index -> target vg name
        source_group_names = []
        for src_name, xml2_target in merge_map.items():
            src_vg = child.vertex_groups.get(src_name)
            if src_vg is None:
                continue

            # Resolve XML2 target name to the CURRENT vertex group name.
            # e.g., "Bip01 L Finger1" -> "MiddleFinger1_L" (pre-rename name)
            current_target_name = reverse_rename.get(xml2_target, xml2_target)

            target_vg = child.vertex_groups.get(current_target_name)
            if target_vg is None:
                target_vg = child.vertex_groups.get(xml2_target)
            if target_vg is None:
                # Create with the CURRENT name so rename step works later
                target_vg = child.vertex_groups.new(name=current_target_name)

            src_index_to_target[child.vertex_groups[src_name].index] = target_vg.name
            source_group_names.append(src_name)

        if not src_index_to_target:
            continue

        # Single pass over all vertices: accumulate merged weight per
        # (target, vertex). O(verts x groups) instead of O(sources x verts).
        pending = {}  # target vg name -> {vert_index: weight}
        for vert in mesh.vertices:
            for g in vert.groups:
                target_name = src_index_to_target.get(g.group)
                if target_name is not None and g.weight > 0.0:
                    bucket = pending.setdefault(target_name, {})
                    bucket[vert.index] = bucket.get(vert.index, 0.0) + g.weight

        for target_name, vert_weights in pending.items():
            target_vg = child.vertex_groups.get(target_name)
            if target_vg is None:
                continue
            for vert_index, weight in vert_weights.items():
                try:
                    target_vg.add([vert_index], weight, 'ADD')
                except Exception:
                    pass

        # Remove source vertex groups (re-lookup by name — removal shifts
        # collection indices)
        for src_name in source_group_names:
            src_vg = child.vertex_groups.get(src_name)
            if src_vg is not None:
                child.vertex_groups.remove(src_vg)


def _fix_unweighted_verts(armature_obj, weight_eps=0.01):
    """Weight source-unskinned verts like their nearest WEIGHTED vertex.

    Game rips often leave rigid accessory geometry (visors, belts, armour
    plates) with no skin weights — their engine bound the piece some other
    way. In XML2/MUA an unweighted vert stays frozen at bind pose while the
    body animates. For each such vert, copy the vertex-group weights of the
    spatially nearest weighted vertex (KD-tree), so the accessory follows
    whatever body part it sits on. No-op on fully weighted meshes.

    Returns the number of vertices fixed (for reporting).
    """
    from mathutils import kdtree

    fixed = 0
    for child in _get_skinned_meshes(armature_obj):
        mesh = child.data
        weighted_idx = []
        unweighted_idx = []
        for v in mesh.vertices:
            if any(g.weight > weight_eps for g in v.groups):
                weighted_idx.append(v.index)
            else:
                unweighted_idx.append(v.index)
        if not unweighted_idx or not weighted_idx:
            continue

        kd = kdtree.KDTree(len(weighted_idx))
        for i, vi in enumerate(weighted_idx):
            kd.insert(mesh.vertices[vi].co, vi)
        kd.balance()

        # source vert -> [(group_index, weight), ...] snapshot BEFORE writes
        donor_groups = {}
        for vi in weighted_idx:
            donor_groups[vi] = [(g.group, g.weight)
                                for g in mesh.vertices[vi].groups
                                if g.weight > weight_eps]

        groups = child.vertex_groups
        for vi in unweighted_idx:
            _co, donor_vi, _dist = kd.find(mesh.vertices[vi].co)
            if donor_vi is None:
                continue
            for gidx, w in donor_groups.get(donor_vi, ()):
                if 0 <= gidx < len(groups):
                    try:
                        groups[gidx].add([vi], w, 'REPLACE')
                    except Exception:
                        pass
            fixed += 1
    return fixed


# ============================================================================
# Visual Display
# ============================================================================

def _update_bone_display(armature_obj):
    """Update bone tails to reflect native XML2 orientations in the viewport.

    After conversion, the bone HEAD positions are correct (model proportions),
    but the tails still point in the original VRChat/Unity directions.  This
    function sets each bone's tail to point along the native XML2 bone axis
    so the skeleton visually matches the game layout.

    Bone lengths are computed from the distance to the nearest child bone
    (matching the XML2 hierarchy) so bones look natural and proportional.

    This is purely cosmetic — all export data (inv_bind, translations) is
    already stored as custom properties and is not affected by tail changes.
    """
    import bpy
    import math
    from mathutils import Quaternion, Vector

    rz90_inv = Quaternion((0, 0, 1), math.radians(-90))

    bpy.context.view_layer.objects.active = armature_obj
    armature_obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')

    edit_bones = armature_obj.data.edit_bones

    # Build XML2 parent->children map for natural bone length computation
    xml2_children = {}  # parent_name -> [child_name, ...]
    for name, idx, parent_idx, bm_idx, flags in XML2_SKELETON:
        if parent_idx >= 0:
            parent_name = XML2_SKELETON[parent_idx][0]
            parent_display = parent_name if parent_name else "Bone_000"
            display_name = name if name else "Bone_000"
            xml2_children.setdefault(parent_display, []).append(display_name)

    # Compute a fallback bone length from the armature's Z extent
    min_z = float('inf')
    max_z = float('-inf')
    for eb in edit_bones:
        min_z = min(min_z, eb.head.z)
        max_z = max(max_z, eb.head.z)
    armature_height = max(max_z - min_z, 0.1)
    default_bone_len = armature_height * 0.03  # ~3% of height

    for eb in edit_bones:
        # Compute bone length from distance to nearest child
        children = xml2_children.get(eb.name, [])
        bone_len = 0.0
        if children:
            min_child_dist = float('inf')
            for child_name in children:
                child_eb = edit_bones.get(child_name)
                if child_eb:
                    dist = (child_eb.head - eb.head).length
                    if dist > 0.001:
                        min_child_dist = min(min_child_dist, dist)
            if min_child_dist < float('inf'):
                bone_len = min_child_dist * 0.8  # 80% of distance to child

        if bone_len < 0.001:
            bone_len = default_bone_len

        native_q = NATIVE_BONE_ROTATIONS.get(eb.name)
        if native_q:
            # Convert native rotation: Alchemy -> Blender, game space -> armature
            q_blender = Quaternion(native_q).conjugated()
            q_armature = rz90_inv @ q_blender
            rot_mat = q_armature.to_matrix()

            # Bone Y axis = tail direction in Blender convention
            bone_dir = rot_mat @ Vector((0, 1, 0))
            eb.tail = eb.head + bone_dir * bone_len
        else:
            # Non-deforming bones (Bone_000, Bip01, Motion): point upward
            eb.tail = eb.head + Vector((0, 0, bone_len))

    bpy.ops.object.mode_set(mode='OBJECT')


# ============================================================================
# Matrix / Translation Computation
# ============================================================================

def _get_game_rotation(armature_obj, converted=True):
    """Get the combined rotation that converts bone positions from Blender
    armature-local space to XML2 game space.

    Includes:
    1. Armature world rotation (handles FBX -90° X for Y-up → Z-up)
    2. For CONVERTED rigs only: +90° Z rotation converting Blender axis
       convention (X=right, -Y=forward) to XML2 (X=forward, Y=left).
       Round-tripped/native rigs are already in game convention.

    Returns:
        4x4 rotation Matrix.
    """
    import math
    from mathutils import Quaternion

    world_q = armature_obj.matrix_world.to_quaternion()
    if converted:
        rz90 = Quaternion((0, 0, 1), math.radians(90))
        game_q = rz90 @ world_q
    else:
        game_q = world_q
    return game_q.to_matrix().to_4x4()


def _compute_bone_rotation_alchemy(bone, game_rot):
    """Derive a bone's bind-pose rotation in Alchemy convention from its rest matrix.

    Used for A-pose export where we can't use the hardcoded T-pose
    NATIVE_BONE_ROTATIONS.  Instead, we compute the rotation from the bone's
    actual rest-pose orientation in game space.

    Args:
        bone: Blender bone (from armature.data.bones).
        game_rot: 4x4 rotation matrix from Blender-local to game space.

    Returns:
        Quaternion in Blender convention (already conjugated from Alchemy).
    """
    from mathutils import Quaternion

    # bone.matrix_local is the bone's rest transform in armature space.
    # Convert to game space and extract the rotation.
    game_mat = game_rot @ bone.matrix_local
    q_blender = game_mat.to_quaternion()
    return q_blender


def _skeleton_for_armature(armature_obj):
    """The bone table this armature was converted WITH.

    The per-bone export functions below MUST iterate the same table the
    armature was built from — iterating the reduced XML2_SKELETON on a
    full-finger MUA rig skips the 30 finger bones and shifts every bm-index
    pairing after the L-finger block (R UpperArm receives R Thigh's bind,
    the hand lands on the floor, tail bones fall off the table entirely).
    Resolved from the properties convert_rig stamps on the armature."""
    game = str(armature_obj.get("igb_target_game", "XML2")).upper()
    if game not in ("XML2", "MUA"):
        game = "XML2"
    full = bool(armature_obj.get("igb_full_fingers", False))
    return get_skeleton_for_game(game, full_fingers=full)


def _build_native_bind_matrix(name, bone, game_rot, use_native_rotations=True):
    """Build a bind matrix using native XML2 orientation + custom bone position.

    For deforming bones: native rotation + rig's game-space bone position.
    For non-deforming / unmapped: identity rotation at game-space position.

    When use_native_rotations=False (A-pose export), computes the bone's
    actual orientation from its rest matrix instead of using hardcoded
    T-pose quaternions.

    CRITICAL — Quaternion convention:
    NATIVE_BONE_ROTATIONS are in Alchemy convention (conjugate of Blender).
    Must conjugate before use in Blender's Quaternion.to_matrix().

    Args:
        name: XML2 bone name.
        bone: Blender bone.
        game_rot: 4x4 rotation matrix from Blender-local to game space.
        use_native_rotations: If True, use T-pose NATIVE_BONE_ROTATIONS.
                              If False, compute from actual bone orientation.

    Returns:
        4x4 Matrix: bone's bind transform in game space (unscaled).
    """
    from mathutils import Matrix, Quaternion

    bone_pos_game = game_rot @ bone.head_local

    if use_native_rotations:
        native_q = NATIVE_BONE_ROTATIONS.get(name)
        if native_q:
            # Conjugate: Alchemy convention -> Blender convention
            q_blender = Quaternion(native_q).conjugated()
            rot_4x4 = q_blender.to_matrix().to_4x4()
            return Matrix.Translation(bone_pos_game) @ rot_4x4
        else:
            return Matrix.Translation(bone_pos_game)
    else:
        # A-pose: compute rotation from actual bone orientation
        if name in NATIVE_BONE_ROTATIONS:  # only deforming bones
            q_blender = _compute_bone_rotation_alchemy(bone, game_rot)
            rot_4x4 = q_blender.to_matrix().to_4x4()
            return Matrix.Translation(bone_pos_game) @ rot_4x4
        else:
            return Matrix.Translation(bone_pos_game)


def _compute_inv_joint_matrices(armature_obj, use_native_rotations=True,
                                converted=True, skeleton=None):
    """Compute inverse joint matrices from rotations + custom positions.

    For each deforming bone (bm_idx >= 0), builds a bind matrix from:
        - Rotation (native T-pose or computed from actual bone orientation)
        - game_rot @ bone.head_local (rig's own position in game space)

    When use_native_rotations=True (T-pose target), uses NATIVE_BONE_ROTATIONS.
    When use_native_rotations=False (A-pose target), computes from bone rest pose.

    Uses custom bone positions so the skeleton matches the mesh proportions.
    The exporter scales both mesh and skeleton by igb_export_scale.

    Stored in ROW-MAJOR order (Alchemy convention).

    Args:
        armature_obj: Blender armature object.
        use_native_rotations: If True, use T-pose rotations. If False, compute
                              from actual bone orientations (A-pose).

    Returns:
        List of 35 entries (one per XML2 bone), each either a 16-float list
        or None for non-deforming bones.
    """
    if skeleton is None:
        skeleton = _skeleton_for_armature(armature_obj)
    n_bones = len(skeleton)
    result = [None] * n_bones

    game_rot = _get_game_rotation(armature_obj, converted=converted)

    for name, idx, parent_idx, bm_idx, flags in skeleton:
        if bm_idx < 0:
            continue

        display_name = name if name else "Bone_000"
        bone = armature_obj.data.bones.get(display_name)
        if bone is None:
            continue

        bind_game = _build_native_bind_matrix(name, bone, game_rot,
                                               use_native_rotations)

        try:
            inv_bind = bind_game.inverted()
        except ValueError:
            continue

        # Convert from Blender column-major to Alchemy row-major (transpose)
        row_major = []
        for row in range(4):
            for col in range(4):
                row_major.append(inv_bind[col][row])

        result[idx] = row_major

    return result


def _compute_bone_translations(armature_obj, use_native_rotations=True,
                               converted=True, skeleton=None):
    """Compute bone translations (parent-local offsets) from custom positions.

    Alchemy FK reconstructs bone world positions as:
        child_world = parent_world_pos + parent_world_rot @ child_translation

    translation = inv(parent_bind) @ child_game_pos  (4D point, take xyz)

    For root bones (parent_idx < 0): translation = game-space world position.

    Args:
        armature_obj: Blender armature object.
        use_native_rotations: If True, use T-pose rotations for parent bind.
                              If False, compute from actual bone orientations.

    Returns:
        List of 35 [x, y, z] lists, indexed by bone index.
    """
    if skeleton is None:
        skeleton = _skeleton_for_armature(armature_obj)
    n_bones = len(skeleton)
    result = [[0.0, 0.0, 0.0]] * n_bones

    game_rot = _get_game_rotation(armature_obj, converted=converted)

    xml2_by_idx = {}
    for entry_name, entry_idx, entry_parent, entry_bm, entry_flags in skeleton:
        xml2_by_idx[entry_idx] = (entry_name, entry_parent)

    for name, idx, parent_idx, bm_idx, flags in skeleton:
        display_name = name if name else "Bone_000"
        bone = armature_obj.data.bones.get(display_name)
        if bone is None:
            result[idx] = [0.0, 0.0, 0.0]
            continue

        bone_pos_game = game_rot @ bone.head_local

        if parent_idx < 0:
            result[idx] = [bone_pos_game.x, bone_pos_game.y, bone_pos_game.z]
        else:
            parent_entry = xml2_by_idx.get(parent_idx)
            if parent_entry is None:
                result[idx] = [0.0, 0.0, 0.0]
                continue

            parent_name, _ = parent_entry
            parent_display = parent_name if parent_name else "Bone_000"
            parent_bone = armature_obj.data.bones.get(parent_display)

            if parent_bone is None:
                result[idx] = [0.0, 0.0, 0.0]
                continue

            parent_bind = _build_native_bind_matrix(
                parent_name, parent_bone, game_rot, use_native_rotations)
            try:
                local_pos = parent_bind.inverted() @ bone_pos_game
                result[idx] = [local_pos.x, local_pos.y, local_pos.z]
            except ValueError:
                parent_pos_game = game_rot @ parent_bone.head_local
                delta = bone_pos_game - parent_pos_game
                result[idx] = [delta.x, delta.y, delta.z]

    return result


def _total_export_scale(armature_obj):
    """The FULL scale the exporter applies: igb_export_scale x object scale.

    _apply_scale_to_skeleton in skin_export multiplies stored translations by
    custom_scale * armature_object_scale_uniform. Anything that pre-divides
    values for that multiplication MUST divide by the same composition —
    dividing by igb_export_scale alone left Bip01/Motion off by exactly the
    armature's object scale on DAE/FBX imports (e.g. 0.0254 inch->meter),
    which corrupted the engine's root-motion handling.
    """
    custom_scale = armature_obj.get("igb_export_scale", 1.0)
    if isinstance(custom_scale, (list, tuple)):
        custom_scale = 1.0
    obj_scale = armature_obj.matrix_world.to_scale()
    uniform = (abs(obj_scale.x) + abs(obj_scale.y) + abs(obj_scale.z)) / 3.0
    if uniform < 1e-9:
        uniform = 1.0
    return custom_scale * uniform


# Root-compensation anchor: the Bip01 height the offset is exact at.
# Decoded from real anim tracks across characters: menu_idle drives Bip01
# to ~41.5-41.9 (all characters — it's the close-camera pose where float
# is most visible), Iron Man's idle ~40.9-41.6, Wolverine's idle 38-39.3.
# The native bind (41.82) is the best single anchor: menus ground exactly,
# standing idles land within ~1-2 units, and s=1 is a strict no-op.
_ANIM_IDLE_BIP01_Z = 41.8195


def _uniform_root_translations(translations):
    """Normalize root translations for a non-standard-size character.

    The empirical facts (from the user's three-way in-game test + decoding
    the game's animations):
      - The runtime uses the SKIN skeleton's translations for FK (a scaled
        skin gives a scaled body) ...
      - ... EXCEPT animations with Bip01 translation keys REPLACE the bind
        value with ABSOLUTE native heights (idle drives Z to ~38). A short
        rig with Bip01 bind at its own pelvis height gets hoisted to 38
        and floats.
      - The Pelvis track in anims carries no data, so the Pelvis bind
        offset SURVIVES animation — it's the one knob that works always.

    Layout chosen (exact at idle, the pose you see 95% of the time):
      pelvis_bind  P  (feet on floor at bind)
      s          = P / native(41.82)
      Pelvis.z   = -IDLE * (1 - s)     -> anim-keyed pelvis = K - IDLE(1-s)
                                          == K*s exactly when K == IDLE
      Bip01.z    = P - Pelvis.z        -> bind/walk/run pelvis stays at P
      Motion     = (x, y, 0)           -> vanilla non-standard-size pattern

    Deep crouch transients (jump_land K~24) deviate by (IDLE-K)*(1-s) —
    brief and small for moderate scales. For s=1 everything is native.

    Args:
        translations: list of 35 [x,y,z] — modified in place.
    """
    pelvis_fk = [translations[1][i] + translations[2][i] for i in range(3)]
    native_z = NATIVE_TRANSLATIONS[1][2]  # 41.8195

    s = pelvis_fk[2] / native_z if native_z > 1e-9 else 1.0
    pelvis_offset_z = -_ANIM_IDLE_BIP01_Z * (1.0 - s)

    translations[0] = [0.0, 0.0, 0.0]
    translations[2] = [0.0, 0.0, pelvis_offset_z]
    translations[1] = [pelvis_fk[0], pelvis_fk[1],
                       pelvis_fk[2] - pelvis_offset_z]
    translations[34] = [pelvis_fk[0], pelvis_fk[1], 0.0]


def _force_native_root_translations(translations, export_scale):
    """Force non-deforming root bone translations to native game-unit values.

    The game engine expects Bip01 at a very specific Z height (~41.82 game
    units / "inches").  This is required for correct character select menu
    positioning, head-tracking, optic beams, and animation compatibility.

    Pelvis (idx 2) is recomputed so that its FK-reconstructed position still
    matches the model's actual pelvis height (preserving inv_bind consistency).

    Translations are stored UNSCALED here — the exporter multiplies by
    export_scale later.  So we store native_game_units / export_scale.

    Args:
        translations: list of 35 [x,y,z] — modified in place.
        export_scale: float (e.g. 66.79).
    """
    if export_scale < 0.001:
        return

    # Current Pelvis game-space position via FK (unscaled).
    # FK chain: Root(0,0,0) -> Bip01 at T[1] -> Pelvis at T[1]+T[2]
    # (Bip01 has no native rotation, so identity applies.)
    pelvis_unscaled = [translations[1][i] + translations[2][i] for i in range(3)]

    # Force Bip01 (idx 1) and Motion (idx 34) to exact native values.
    native_t1 = NATIVE_TRANSLATIONS[1]    # [0.6898, 0, 41.8195] game units
    native_t34 = NATIVE_TRANSLATIONS[34]  # [0.6898, 0, 0.1495]  game units

    translations[0] = [0.0, 0.0, 0.0]
    translations[1] = [v / export_scale for v in native_t1]
    translations[34] = [v / export_scale for v in native_t34]

    # Recompute Pelvis (idx 2) as offset from forced Bip01 so that the
    # FK chain still reconstructs the actual pelvis position.
    translations[2] = [pelvis_unscaled[i] - translations[1][i] for i in range(3)]


def fix_root_translations(armature_obj):
    """Recompute the export skeleton from the armature's CURRENT state and
    re-anchor Bip01/Motion to native game values.

    Handles every way the user may have resized the rig after conversion:
    - Object Mode scaling (object scale changes, bones unchanged)
    - Edit Mode scaling (bone positions change, object scale unchanged)
    - any combination / repeated tweaks (idempotent)

    The full skeleton data (translations + inverse bind matrices) is
    rebuilt from the live bone positions, then the roots are forced to
    export at exact native heights with the Pelvis offset absorbing the
    difference — body keeps the user's size, feet stay planted.

    Returns:
        (success, message)
    """
    if armature_obj is None or armature_obj.type != 'ARMATURE':
        return False, "No armature selected"

    if "igb_skin_bone_translations" not in armature_obj:
        return False, ("No skeleton data on armature — run Setup Skin "
                       "or import an actor first")

    if armature_obj.data.bones.get("Bip01 Pelvis") is None:
        return False, "Armature has no 'Bip01 Pelvis' bone"

    total = _total_export_scale(armature_obj)
    if total < 0.001:
        return False, f"Export scale too small ({total:.5f})"

    target_game = armature_obj.get("igb_target_game", "XML2")
    prev_converted = armature_obj.get("igb_converted_rig", False)

    # FULL REBUILD from the live bones — the Blender skeleton is the truth.
    # Whatever the user did (object scale, edit-mode scale, pose-scale of
    # the Pelvis + apply rest, manual bone surgery), the export data is
    # regenerated to match it exactly: translations AND inverse binds stay
    # consistent with each other — that consistency is what keeps the arms
    # from breaking. Roots are then normalized to Raven's vanilla pattern
    # for non-standard-size characters (Bip01 at the pelvis, Motion Z=0).
    inv_matrices = _compute_inv_joint_matrices(armature_obj, True,
                                               converted=prev_converted)
    translations = _compute_bone_translations(armature_obj, True,
                                              converted=prev_converted)
    _uniform_root_translations(translations)
    # _store_skeleton_properties unconditionally sets igb_converted_rig;
    # preserve the original value
    _store_skeleton_properties(armature_obj, inv_matrices, translations,
                               target_game=target_game)
    armature_obj["igb_converted_rig"] = prev_converted

    # ---- Restore Bip01 to its ORIGINAL scale (only Bip01) ----
    # The user scales the armature object to resize the character; Bip01
    # shrinks with it. Restore the bone's WORLD size to what it was at
    # setup time (stored reference), keeping its current head position and
    # direction. Also clear any pose-scale on it.
    import bpy

    bip_pb = armature_obj.pose.bones.get("Bip01")
    if bip_pb is not None and tuple(bip_pb.scale) != (1.0, 1.0, 1.0):
        old_scale = tuple(bip_pb.scale)
        bip_pb.scale = (1.0, 1.0, 1.0)
        pelvis_pb = armature_obj.pose.bones.get("Bip01 Pelvis")
        if pelvis_pb is not None:
            inherit = getattr(pelvis_pb.bone, 'inherit_scale', 'FULL')
            if inherit != 'NONE':
                pelvis_pb.scale = (pelvis_pb.scale[0] * old_scale[0],
                                   pelvis_pb.scale[1] * old_scale[1],
                                   pelvis_pb.scale[2] * old_scale[2])

    obj_scale = armature_obj.matrix_world.to_scale()
    obj_uniform = (abs(obj_scale.x) + abs(obj_scale.y)
                   + abs(obj_scale.z)) / 3.0 or 1.0
    bip_bone = armature_obj.data.bones.get("Bip01")
    if bip_bone is not None:
        # Reference world length from setup; fall back to "original object
        # scale was 1.0" (true for round-tripped rigs) when not stored
        ref_world_len = armature_obj.get("igb_bip01_world_length")
        if not isinstance(ref_world_len, (int, float)) or ref_world_len <= 0:
            ref_world_len = bip_bone.length  # length at obj scale 1.0
        target_edit_len = ref_world_len / obj_uniform
        if abs(target_edit_len - bip_bone.length) > 1e-6:
            try:
                bpy.context.view_layer.objects.active = armature_obj
                armature_obj.select_set(True)
                bpy.ops.object.mode_set(mode='EDIT')
                eb = armature_obj.data.edit_bones.get("Bip01")
                if eb is not None and (eb.tail - eb.head).length > 1e-9:
                    direction = (eb.tail - eb.head).normalized()
                    eb.tail = eb.head + direction * target_edit_len
                bpy.ops.object.mode_set(mode='OBJECT')
            except Exception:
                pass  # cosmetic only — never fail the data fix over it

    bip_z = translations[1][2] * total
    pelvis_z = (translations[1][2] + translations[2][2]) * total
    return True, (f"Export data rebuilt from bones: Bip01 at Z={bip_z:.2f}, "
                  f"pelvis at Z={pelvis_z:.2f} (native Bip01 = 41.82)")


def _ground_rig(armature_obj):
    """Translate bones AND meshes down so the lowest mesh point sits at
    world Z=0 (feet on the floor).

    Pose-scaling the Pelvis shrinks the body toward the pelvis head, which
    leaves the feet hanging above the ground — vanilla small characters
    (e.g. 6601.igb) have their pelvis LOWERED so feet touch zero.

    Returns:
        The world-space drop distance applied (0.0 if already grounded).
    """
    import bpy
    from mathutils import Vector

    min_z = float('inf')
    meshes = _get_skinned_meshes(armature_obj)
    for mesh_obj in meshes:
        mw = mesh_obj.matrix_world
        for corner in mesh_obj.bound_box:
            z = (mw @ Vector(corner)).z
            min_z = min(min_z, z)
    if min_z == float('inf') or abs(min_z) < 1e-4:
        return 0.0

    drop_world = Vector((0.0, 0.0, -min_z))

    # Shift all bones (armature-local direction of world -Z)
    try:
        d_arm = armature_obj.matrix_world.to_3x3().inverted() @ drop_world
    except ValueError:
        return 0.0
    bpy.context.view_layer.objects.active = armature_obj
    armature_obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    for eb in armature_obj.data.edit_bones:
        eb.head += d_arm
        eb.tail += d_arm
    bpy.ops.object.mode_set(mode='OBJECT')

    # Shift mesh data (each mesh's local direction), shape keys included
    for mesh_obj in meshes:
        try:
            d_mesh = mesh_obj.matrix_world.to_3x3().inverted() @ drop_world
        except ValueError:
            continue
        mesh = mesh_obj.data
        for vert in mesh.vertices:
            vert.co += d_mesh
        if mesh.shape_keys and mesh.shape_keys.key_blocks:
            for kb in mesh.shape_keys.key_blocks:
                for point in kb.data:
                    point.co += d_mesh
        mesh.update()

    return -min_z


def apply_pose_and_rebuild(armature_obj):
    """One-click version of the proven manual resize recipe.

    The user scales bones in POSE mode (e.g. just 'Bip01 Pelvis' to shrink
    the whole body while Bip01 keeps its size). This then:
    1. Bakes the current pose into every skinned mesh (weighted,
       shape-key aware) — so the mesh actually matches the new skeleton
    2. Applies the pose as the new rest pose
    3. Rebuilds ALL export data (translations + inverse binds) from the
       result — keeping them consistent, which is what stops the arms
       from breaking

    Returns:
        (success, message)
    """
    import bpy

    if armature_obj is None or armature_obj.type != 'ARMATURE':
        return False, "No armature selected"
    if "igb_skin_bone_translations" not in armature_obj:
        return False, ("No skeleton data on armature — run Setup Skin "
                       "or import an actor first")

    # Anything posed at all?
    posed = False
    for pb in armature_obj.pose.bones:
        if (tuple(pb.scale) != (1.0, 1.0, 1.0)
                or tuple(pb.location) != (0.0, 0.0, 0.0)
                or tuple(pb.rotation_quaternion) != (1.0, 0.0, 0.0, 0.0)
                or tuple(pb.rotation_euler) != (0.0, 0.0, 0.0)):
            posed = True
            break
    if not posed:
        return False, ("No pose changes found — scale bones in Pose Mode "
                       "first (e.g. scale 'Bip01 Pelvis' to resize the "
                       "body while Bip01 keeps its size)")

    # 1. Bake the pose into the meshes (manual weighted deform — works
    # with the 100+ shape keys that block modifier_apply)
    _apply_current_pose_to_meshes(armature_obj)

    # 2. Pose becomes the new rest pose
    bpy.context.view_layer.objects.active = armature_obj
    armature_obj.select_set(True)
    bpy.ops.object.mode_set(mode='POSE')
    bpy.ops.pose.armature_apply()
    bpy.ops.object.mode_set(mode='OBJECT')

    # 3. Ground the rig — pelvis-pivot scaling leaves the feet hanging;
    # vanilla small characters have the whole skeleton lowered so feet
    # touch Z=0
    dropped = _ground_rig(armature_obj)

    # 4. Rebuild the export data from the new rest skeleton
    success, message = fix_root_translations(armature_obj)
    if not success:
        return False, f"Pose applied, but data rebuild failed: {message}"
    if dropped > 1e-4:
        message += f" (grounded: dropped {dropped:.3f})"
    return True, f"Pose applied and export data rebuilt. {message}"


# ============================================================================
# Custom Property Storage
# ============================================================================

def _store_skeleton_properties(armature_obj, inv_matrices, translations,
                               target_game='XML2'):
    """Store all IGB skeleton custom properties on the armature.

    This makes the armature compatible with the from-scratch skin exporter.
    Uses the target game's skeleton definition (XML2=35 bones, MUA=49 bones).
    """
    import bpy

    skeleton = get_skeleton_for_game(
        target_game, full_fingers=armature_obj.get('igb_full_fingers', False))
    joint_count = sum(1 for _, _, _, bm, _ in skeleton if bm >= 0)

    # Skeleton-level properties
    armature_obj["igb_skin_skeleton_name"] = ""
    armature_obj["igb_skin_joint_count"] = joint_count
    armature_obj["igb_bone_count"] = len(skeleton)

    # Remember Bip01's WORLD-space display length so "Fix Bip01" can
    # restore the bone to its original size after the user rescales the
    # armature (the bone size is cosmetic, but it's the user's visual
    # reference for "Bip01 stayed native")
    bip01_bone = armature_obj.data.bones.get("Bip01")
    if bip01_bone is not None:
        obj_scale = armature_obj.matrix_world.to_scale()
        uniform = (abs(obj_scale.x) + abs(obj_scale.y)
                   + abs(obj_scale.z)) / 3.0 or 1.0
        armature_obj["igb_bip01_world_length"] = bip01_bone.length * uniform

    # Bone translations (indexed by bone index)
    # translations is a list from _compute_bone_translations (35 entries for XML2).
    # For MUA, extend with zeros for FX bone indices (35-48).
    n_skel = len(skeleton)
    extended_trans = list(translations)  # copy
    while len(extended_trans) < n_skel:
        extended_trans.append([0.0, 0.0, 0.0])
    armature_obj["igb_skin_bone_translations"] = json.dumps(extended_trans)

    # Inverse joint matrices (indexed by bone index)
    # FX bones have bm_idx=-1 so they don't get inv_joint entries.
    # Extend with None for FX bone indices.
    extended_inv = list(inv_matrices)  # copy
    while len(extended_inv) < n_skel:
        extended_inv.append(None)
    armature_obj["igb_skin_inv_joint_matrices"] = json.dumps(extended_inv)

    # Complete bone info list (the authoritative source for export)
    bone_info_list = []
    for name, idx, parent_idx, bm_idx, flags in skeleton:
        bone_info_list.append({
            'name': name,
            'index': idx,
            'parent_idx': parent_idx,
            'bm_idx': bm_idx,
            'flags': flags,
        })
    armature_obj["igb_skin_bone_info_list"] = json.dumps(bone_info_list)

    # BMS palette: identity [0, 1, 2, ..., 31] for 32 deforming bones
    bms_palette = list(range(joint_count))
    armature_obj["igb_bms_palette"] = json.dumps(bms_palette)

    # Flag: rig was converted from non-XML2 convention.  The exporter uses
    # this to apply the 90° Z axis rotation to mesh vertices (converting
    # Blender convention to XML2 game convention).  Skeleton data is computed
    # with the same rotation baked in, so it just needs the normal export scale.
    armature_obj["igb_converted_rig"] = True

    # Per-bone metadata on pose bones
    for name, idx, parent_idx, bm_idx, flags in skeleton:
        display_name = name if name else "Bone_000"
        if display_name in armature_obj.pose.bones:
            pb = armature_obj.pose.bones[display_name]
            pb["igb_bone_index"] = idx
            pb["igb_parent_idx"] = parent_idx
            pb["igb_skin_bm_idx"] = bm_idx if bm_idx >= 0 else idx
            pb["igb_bm_idx"] = bm_idx if bm_idx >= 0 else idx
            pb["igb_flags"] = flags
            # Tag FX bones for easy identification
            if name in MUA_FX_BONE_NAMES:
                pb["igb_fx_bone"] = True
