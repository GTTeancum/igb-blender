"""Universal bone-name tables for rig conversion (ported from CATS).

Data ported from the CATS Blender Plugin (MIT license, tools/armature_bones.py)
and remapped from CATS' VRChat-standard targets to XML2 'Bip01' targets.

Pattern templates use literal backslash placeholders, expanded per side:
    \\Left -> Left / Right        \\left -> left / right
    \\L    -> L / R               \\l    -> l / r

All aliases here are matched against names AFTER rig_converter's
_normalize_bone_name() pipeline (capitalization, prefix/suffix strips,
separator collapsing), compared lowercase.

Sections:
    RENAME_SIDED / RENAME_CENTER  -> direct renames to XML2 bones
    SPINE_CHAIN_ALIASES           -> chain-assigned (Spine/Spine1/Spine2)
    MERGE_SIDED / MERGE_CENTER    -> weight-merge targets (CATS bone_reweight)
    EXACT_MERGE                   -> exact-name merges (CATS bone_list_weight)
    FACE keywords + classifier    -> face bones -> Bip01 Head
    Finger classifier             -> (side, finger, sort_hint) from any scheme
"""

import re

# ============================================================================
# Side template expansion
# ============================================================================

def expand_side(pattern, side):
    """Expand \\Left / \\L template placeholders for one side ('L' or 'R')."""
    if side == 'L':
        return (pattern.replace('\\Left', 'Left').replace('\\left', 'left')
                       .replace('\\L', 'L').replace('\\l', 'l'))
    return (pattern.replace('\\Left', 'Right').replace('\\left', 'right')
                   .replace('\\L', 'R').replace('\\l', 'r'))


# ============================================================================
# Direct rename tables (CATS bone_rename, remapped to XML2)
# ============================================================================
# Lists are PRIORITY-ORDERED: when several bones alias to the same XML2
# target, the bone matching the earliest list entry wins the rename; losers
# become weight-merge sources into the winner.

# Sided: XML2 name uses {S} placeholder for 'L'/'R'.
RENAME_SIDED = {
    'Bip01 {S} Clavicle': [
        '\\Left_Shoulder', '\\LeftShoulder', 'Shoulder_\\L', 'Shoulder_\\Left',
        '\\LShoulder', '\\LShoulderN', 'Shoulder\\L', '\\L_Shoulder',
        'Mixamorig_\\LeftShoulder', 'Arm_\\Left_Shoulder', 'Arm_\\Left_Shoulder_1',
        'ShoulderArm_\\L', 'Bip_\\L_Clavicle', 'Bip_Collar_\\L', 'B_\\L_Shoulder',
        '\\LCollar', '\\L_Clavicle', '\\L_Clavicle1', '\\Left_Clavicle',
        '\\LeftCollar', '\\Left_Collar', '\\L_Collar', '\\L_Clavicle_1',
        '\\L_CBONE', 'Shoulder+_\\L', 'Shol_\\L', '\\Lf_Clavicle', 'Clavicle_\\L',
        'Arm_\\Left_Sh_1', 'Shoulder(\\L)_0', '\\L_Kata', 'Cf_D_Shoulder_\\L',
        'Cf_D_Shoulder2_\\L', 'Clavicle\\LT_01', 'J_Bip_\\L_Shoulder',
        'J_\\L_Collar', 'J_\\L_Shoulder', 'Clavicle\\L', 'Bip_Clavicle_\\L',
        '\\L_Clavic', 'J_Sako_\\L', '\\L_ShoulderPad', 'Collarbone_\\L',
        'J_Clavicle_\\L', '\\L_Clav', 'Clav_\\L', '\\LClavicle', 'Bip_\\L_Arm',
    ],
    'Bip01 {S} UpperArm': [
        '\\Left_Arm', '\\LeftArm', 'Arm_\\L', 'cShrugger.\\L', '\\LArm', '\\LArmA',
        'ArmTC_\\L', 'Mixamorig_\\LeftArm', 'Arm_\\Left_Shoulder_2',
        'Bip_\\L_UpperArm', 'Bip_UpperArm_\\L', 'B_\\L_Arm1', 'Upper_Arm_\\L',
        'UpperArm_\\L', '\\Left_Upper_Arm', '\\LShldr', '\\L_UpperArm',
        '\\LeftUpArm', 'Uparm_\\L', '\\L_Uparm', '\\L_Arm', '\\L_Arm_01',
        'Arm_\\Left_Arm', '\\L_Upperarm_1', '\\L_ARM1', 'Arm\\L1', '\\LShoulderJ',
        '\\Lf_Shoulder', 'Arm_Upper_\\L', 'Arm_\\Left_Sh_2', '\\Larm1',
        'Shoulder(\\L)_1', '\\L_Ude', 'Shoulder\\LT_01', 'J_Bip_\\L_UpperArm',
        'J_\\L_UpArm', 'J_\\L_Elbow', 'Arm_1_\\L', 'Upperarm01_\\L', '\\L_Shldr',
        '\\LShldrBend', 'Arm_Stretch_\\L', 'J_Ude_A_\\L', 'J_Shoulder_\\L',
        '\\LUpperArm', 'Bip_\\L_Arm1',
    ],
    'Bip01 {S} Forearm': [
        '\\Left_Elbow', '\\LeftElbow', 'Elbow_\\L', '\\L_Elbow',
        'Mixamorig_\\LeftForeArm', 'Arm_\\Left_Elbow', 'Bip_\\L_ForeArm',
        'Bip_LowerArm_\\L', 'B_\\L_Arm2', 'Fore_Arm_\\L', 'ForeArm_\\L',
        '\\LForeArm', '\\L_ForeArm', '\\LeftLowArm', '\\Left_Forearm',
        '\\L_Foarm', 'Loarm_\\L', '\\L_Arm_02', '\\LeftForeArm', '\\L_Forearm_1',
        'Lower_Arm_\\L', '\\L_ARM2', 'Arm\\L2', '\\LArmJ', 'Elb_\\L',
        'Arm_\\Left_Elbow_1', 'LowerArm_\\L', '\\Lf_Elbow', 'Arm_Lower_\\L',
        '\\Left_Forarm', '\\Larm2', 'Hand(\\L)07', 'Lowarm_\\L', '\\L_Hiji',
        'Elbow\\LT_01', 'J_Bip_\\L_LowerArm', 'J_\\L_ForeArm', 'Arm_2_\\L',
        'Bip_Forearm_\\L', 'Lowerarm01_\\L', '\\LElbow', '\\LForearmBend',
        'Forearm_Stretch_\\L', 'J_Ude_B_\\L', 'J_Elbow_\\L', '\\L_LowerArm',
        'Bip_\\L_Arm2', '\\L_Forearm',
    ],
    'Bip01 {S} Hand': [
        '\\Left_Wrist', '\\LeftWrist', 'Wrist_\\L', 'Wrist2_\\L', 'HandAux2_\\L',
        'Mixamorig_\\LeftHand', 'Arm_\\Left_Wrist', 'Arm_\\Left_Wirst',
        'Bip_\\L_Hand', 'Bip_Hand_\\L', 'B_\\L_Hand', 'Hand_\\L', 'Hand\\L',
        '\\LHand', '\\LHandN', '\\LeftHand', '\\Left_Hand', '\\L_Hand',
        '\\L_Hand_1', '\\LFingerBaseN', '\\Lf_Wrist', 'Palm_\\L', 'Hand(\\L)00',
        '\\L_Te', 'Hand\\LT_01', 'J_Bip_\\L_Hand', 'J_\\L_Wrist', '\\L_Wrist',
        '\\LWrist', 'J_Te_\\L', 'J_Wrist_\\L',
    ],
    'Bip01 {S} Thigh': [
        '\\Left_Leg', '\\Left_Foot', '\\LeftLeg', 'Hip.\\L', 'Leg_\\L',
        'Leg_\\L_001', 'Leg\\L1', 'Mixamorig_\\LeftUpLeg', 'Leg_\\Left_Thigh',
        'Bip_\\L_Thigh', 'Bip_Hip_\\L', 'B_\\L_Leg1', 'Upper_Leg_\\L', '\\LThigh',
        'Thigh_\\L', '\\L_Thigh', '\\LeftUpLeg', '\\LeftHip', '\\Left_Thigh',
        'Upleg_\\L', '\\L_Hip', '\\L_Leg_01', '\\L_Femur', '\\L_Femur_1',
        '\\L_LEG1', '\\LLegJ', 'Tg_\\L', 'Leg_\\Left_Thigh_1', 'UpperLeg_\\L',
        '\\Lf_Leg', '\\L_UpLeg', 'Thigh00_\\L', '\\Lfoot1', 'Leg(\\L)04',
        '\\L_Momo', 'Leg_Thigh_\\L', '\\L_Leg', 'Hip\\LT_01', 'J_Bip_\\L_UpperLeg',
        'J_\\L_UpLeg', 'Leg\\L', 'Leg_1_\\L', 'Bip_Thigh_\\L', 'Groin_\\L',
        'Upperleg01_\\L', '\\LThighBend', 'Thigh_Stretch_\\L', 'J_Asi_A_\\L',
        'J_Hip_\\L', '\\LUpLeg', '\\L_UpperLeg', 'Bip_\\L_Leg',
    ],
    'Bip01 {S} Calf': [
        '\\Left_Knee', '\\LeftKnee', 'Knee_\\L_001', 'Knee_\\L',
        'Mixamorig_\\LeftLeg', 'Leg_\\Left_Knee', 'Bip_\\L_Calf', 'Bip_Knee_\\L',
        'B_\\L_Leg2', 'Lower_Leg_\\L', '\\LLeg', '\\LShin', 'Shin_\\L',
        '\\L_Calf', 'Calf_\\L', '\\LeftLowLeg', '\\Left_Shin', 'Loleg_\\L',
        '\\L_Leg_02', '\\L_KneeLower', 'Tibia_\\L', '\\L_Tibia', '\\L_Tibia_1',
        '\\L_LEG2', 'Leg\\L2', '\\LKneeJ', '\\LKnee', 'LowerLeg_\\L', '\\Lf_Knee',
        '\\L_Knee', 'Leg01_\\L', '\\Lfoot2', 'Leg(\\L)00', '\\L_Sune',
        'Leg_Calf_\\L', 'Knee\\LT_01', 'J_Bip_\\L_LowerLeg', 'J_\\L_Leg',
        'Knee\\L', 'Leg_2_\\L', 'Bip_Leg_\\L', 'Lowerleg01_\\L', 'Leg_Stretch_\\L',
        'J_Asi_B_\\L', 'J_Knee_\\L', '\\LCalf', '\\L_LowerLeg', 'Bip_\\L_Leg1',
    ],
    'Bip01 {S} Foot': [
        '\\Left_Ankle', '\\Left_Ankle_001', '\\LeftAnkle', 'Ankle_\\L',
        '\\L_Ankle', 'Mixamorig_\\LeftFoot', 'Leg_\\Left_Ankle', 'Eg_\\Left_Ankle',
        'Bip_\\L_Foot', 'Bip_Foot_\\L', 'B_\\L_Foot', '\\LFoot', 'Foot_\\L',
        'Foot\\L', '\\L_Foot', '\\LeftFoot', 'Leg_\\Left_Foot', '\\L_Foot_01',
        '\\L_Foot_1', '\\L_FOOT1', '\\LFootJ', '\\Lf_Ankle', '\\Left_Heel',
        'Leg(\\L)02', '\\L_Asi', 'Foot\\LT_01', 'J_Bip_\\L_Foot', 'J_\\L_Foot',
        '\\LAnkle', 'J_Asi_D_\\L', 'J_Ankle_\\L',
    ],
    'Bip01 {S} Toe0': [
        '\\Left_Toe', '\\Left_Toes', '\\LeftToe', '\\LeftToe1', 'LegTip_\\L',
        'LegTipEX_\\L', 'Mixamorig_\\LeftToeBase', 'Leg_\\Left_Toes',
        'Bip_\\L_Toe0', 'B_\\L_Toe', '\\LToe', 'Toe_\\L', 'Toe\\L', '\\L_Toe',
        '\\L_Toe1', '\\L_Toe2', '\\LeftToeBase', 'Toe1_1_\\L',
        'Leg_\\Left_Foot_Toes', 'ToeSaki_\\L', '\\L_Toes', '\\L_Toe0',
        '\\L_Toe_1', '\\L_FOOT2', '\\LToeN', '\\LToeA', 'Toes_\\L', 'ToeTip_\\L',
        '\\Lf_Toe', 'Tsumasaki_\\L', 'Leg_\\Left_Toe', 'Leg(\\L)03', 'Toe\\LT_01',
        'J_Bip_\\L_ToeBase', 'J_\\L_Toe', 'Bip_Toe_\\L', 'Toe_Boot_\\L',
        'Toes_01_\\L', 'J_Asi_E_\\L', 'J_Ball_\\L', '\\LToeBase', '\\LToe0',
    ],
}

# Center bones (no side). Priority-ordered like the sided lists.
RENAME_CENTER = {
    'Bip01 Pelvis': [
        # 'Waist' ranks above 'Root': Nintendo rigs (BotW/TotK/Splatoon) use
        # Waist as the weighted hip bone while Root is a ground-level origin.
        # MMD rigs with both also have LowerBody, which outranks them anyway.
        'Hips', 'Pelvis', 'LowerBody', 'Lower_Body', 'Mixamorig_Hips',
        'B_C_Pelvis', 'Bip_Pelvis', 'Hip', 'Waist', 'Root', 'Hips_Root',
        'Rot_Root', 'Sk', 'C_Waist_1', 'Pelwas_001', 'HipN', 'Unused_Root_Hips',
        'Waist01', 'Waist02', 'Hips_Root_1', 'Hips_Root_2', 'Pelvis_Def',
        'J_Kosi', 'Kosi', 'HipMaster_01', 'J_Bip_C_Hips', 'J_Hip', 'Pelvis_L',
        'Pelvis_R', 'Root_Pelvis_1', 'Root_X',
    ],
    'Bip01 Neck': [
        'Neck', 'Mixamorig_Neck', 'Head_Neck_Lower', 'Head_Neck_Lower_1',
        'Head_Neck_Lower_2', 'Head_Neck_Middle', 'Bip_Neck', 'Bip_Neck1',
        'B_C_Neck1', 'Head_Neck', 'J_Bip_C_Neck', 'C_Neck_1', 'NECK', 'NeckN',
        'Helmet_Lower', 'Neck_Dev', 'Neck00', 'Kubi', 'J_Kubi', 'NeckA_01',
        'J_Neck1', 'NeckLower', 'Neck_X',
    ],
    'Bip01 Head': [
        'Head', 'Mixamorig_Head', 'Head_Neck_Upper', 'Head_Neck_Upper_1',
        'Head_Neck_Upper_2', 'Bip_Head', 'Bip_Head1', 'B_C_Head', 'J_Bip_C_Head',
        'C_Head_1', 'HEAD', 'HeadN', 'Helmet_Upper', 'Head_01', 'Head_001',
        'J_Head', 'Head_X', 'J_Kao',
    ],
}

# Spine chain: ALL spine/chest aliases lumped together (CATS approach).
# Matching bones are sorted by hierarchy depth and assigned:
#   first -> Bip01 Spine, second -> Bip01 Spine1, last -> Bip01 Spine2,
#   any extra middles -> weight-merge into Bip01 Spine1.
SPINE_CHAIN_ALIASES = [
    'Spine', 'UpperBody', 'Upper_Body', 'Upper_Waist', 'UpperBody2',
    'Upper_Body_2', 'Upper_Waist_2', 'Waist_Upper_2', 'UpperBody3',
    'Upper_Body_3', 'Upper_Waist_3', 'Waist_Upper_3',
    'Mixamorig_Spine', 'Mixamorig_Spine0', 'Mixamorig_Spine1',
    'Mixamorig_Spine2', 'Mixamorig_Spine3', 'Mixamorig_Spine4',
    'Bip_Spine', 'Bip_Spine0', 'Bip_Spine1', 'Bip_Spine2', 'Bip_Spine3',
    'Bip_Spine4', 'Bip_Spine5', 'Bip_Spine00', 'Bip_Spine01', 'Bip_Spine02',
    'Bip_Spine03', 'Bip_Spine04', 'Bip_Spine05', 'Bip_Spine_0', 'Bip_Spine_1',
    'Bip_Spine_2', 'Bip_Spine_3', 'Bip_Spine_4', 'Bip_Spine_5', 'Bip_Chest',
    'B_C_Spine', 'B_C_Spine0', 'B_C_Spine1', 'B_C_Spine2', 'B_C_Spine3',
    'B_C_Spine4', 'B_C_Spine5', 'B_C_Chest',
    'Spine_Lower', 'Spine_Lower_1', 'Spine_Lower_2', 'Spine_Middle',
    'Spine_Upper', 'Spine_Upper_1', 'Spine_Upper_2',
    'J_SpineLower', 'J_SpineUpper', 'Abdomen',
    'Spine0', 'Spine1', 'Spine2', 'Spine3', 'Spine4', 'Spine5',
    'Spine_0', 'Spine_1', 'Spine_2', 'Spine_3', 'Spine_4', 'Spine_5',
    'Spine01', 'Spine02', 'Spine03', 'Spine04', 'Spine05',
    'Spine_01', 'Spine_02', 'Spine_03', 'Spine_04', 'Spine_05',
    'Spine_A', 'Spine_B', 'Spine_C', 'Spine_D', 'Spine_E',
    'SpineA', 'SpineB', 'SpineC', 'SpineD', 'SpineE',
    'Spina00', 'Spina01', 'Spina02',
    'J_Spine1', 'J_Spine2', 'J_Spine3', 'J_Spine4',
    'Spine_Jnt_01', 'Spine_Jnt_02', 'Spine_Jnt_03',
    'Chest1', 'Chest2', 'Chest3',
    'Chest_A', 'Chest_B', 'Chest_C', 'Chest_D', 'Chest_E',
    'C_Spine_A_1', 'C_Spine_B_1',
    'J_Bip_C_Spine', 'J_Bip_C_Chest', 'J_Bip_C_UpperChest',
    'Pelwas', 'Pelwas2', 'Ribs', 'BODY1', 'BODY2', 'WaistN', 'BustN',
    'Middle', 'Bust', 'SpA', 'SpB', 'SpC', 'Stomach_Def', 'Chest_Def',
    'TorsoA_01', 'TorsoB_01', 'TorsoC_01', 'Torso_1', 'Torso_2',
    'AbdomenUpper', 'ChestLower', 'Spine_01_X', 'Spine_02_X',
    'J_Sebo_A', 'J_Sebo_B', 'J_Sebo_C', 'Mune', 'SpineTop', 'UpperBodyx2',
    'Chest', 'Upper_Chest', 'UpperChest', 'Backbone1', 'Backbone2',
    'Backbone3', 'Backbone4', 'Backbone5', 'Backbone6', 'Backbone7',
]

# ============================================================================
# Weight-merge tables (CATS bone_reweight, remapped to XML2)
# ============================================================================

MERGE_SIDED = {
    'Bip01 {S} Clavicle': [
        'ShoulderP_\\L', 'Shoulder1_\\L', 'Shoulder2_\\L', 'Shoulder3_\\L',
        'Shoulder4_\\L', 'ShoulderSleeve_\\L', 'SleeveShoulderIK_\\L',
        '\\Left_Shoulder_Weight', 'ShoulderS_\\L', 'ShoulderW_\\L',
        'B_\\L_ArmorParts', 'Bip_\\L_Shoulder', 'Kata_\\L', 'Bip_\\L_Clavicle_Rig',
        'Shoulder02_\\L', 'Shoulder1_WayA_\\L', 'Shoulder\\LT_Roll_01',
        'Bip_Armpit_\\L', 'Bip_UpperArmBase_\\L', 'ShoulderHalf_\\L',
        'ShoulderAux_\\L', 'ShoulderC_\\L',
    ],
    'Bip01 {S} UpperArm': [
        'Arm01_\\L', 'Arm02_\\L', 'Arm03_\\L', 'Arm04_\\L', 'Arm05_\\L',
        'ArmTwist_\\L', 'ArmTwist0_\\L', 'ArmTwist1_\\L', 'ArmTwist2_\\L',
        'ArmTwist3_\\L', 'ArmTwist4_\\L', 'ArmTwist5_\\L', 'ArmTwist+0_\\L',
        'ArmTwist+1_\\L', 'ArmTwist+2_\\L', 'ArmTwist+3_\\L', 'ArmTwist+4_\\L',
        'ArmTwist+5_\\L', '\\Left_Arm_Twist', '\\Left_Arm_Torsion',
        '\\Left_Arm_Torsion_1', '\\Left_Arm_Tight', '\\Left_Arm_Tight_1',
        '\\Left_Arm_Tight_2', '\\Left_Arm_Tight_3', '\\Left_Upper_Arm_Twist',
        '\\Left_Upper_Arm_Twist_B', 'ElbowAux_\\L', 'ElbowAux+_\\L',
        '+ElbowAux_\\L', 'ArmSleeve_\\L', 'Arm_Sleeve_\\L', 'ArmTwist_Sleeve_\\L',
        'ShoulderTwist_\\L', 'ArmW_\\L', 'ArmW2_\\L', 'SleeveArm_\\L',
        'SleeveElbowAux_\\L', 'ArmRotation_\\L', 'ArmTwistReturn_\\L',
        'DEF_Upper_Arm_02_\\L', 'DEF_Upper_Arm_Twist_25_\\L',
        'DEF_Upper_Arm_Twist_50_\\L', 'DEF_Upper_Arm_Twist_75_\\L',
        'Arm_\\Left_Bicep', '\\LArmB', '\\L_Sub_Shoulder', 'B_\\L_Elbow',
        'B_\\L_ArmHelper', 'B_\\L_UpperArm_Hojo01', 'B_\\L_Hiji01',
        'Bip_\\L_Trapezius', 'Bip_\\L_Bicep', 'Arm_\\Left_Elbow_Ctr',
        'Arm_\\Left_Shoulder_Ctr_1', '\\L_Sho_Ast', '\\L_Arm_Ast',
        'Uppertwist1_\\L', 'ArmS_\\L', 'Arm\\L1Sub', '\\LSholJb',
        '\\LUpArmTwistjb', '\\LUpArmTwist', 'Bip_\\LUpArmTwist',
        '\\LeftArmRoll', '\\LeftArmBend', '\\L_Uptwist_A',
        '\\L_Uptwist_B', 'Shoulder5_\\L', 'Shoulder6_\\L', 'Shoulder7_\\L',
        'ElbowUpper_\\L', 'Arm_\\Left_Sh_Tw', 'Bip_\\L_UpperArm_Rig',
        'Bip_\\L_UpperArm_Twist', 'Arm_WayA_\\L', '\\L_Arm_EX', '\\L_Elbow_EX',
        'Arm1D_\\L', 'Arm2D_\\L', 'Bip_UpperArmTwistTop_\\L',
        'Bip_ArmpitRingBase_\\L', 'Bip_UpperArmTwistBottom_\\L',
        'Bip_ForearmHelper_\\L', 'Bip_ElbowHelper_\\L', 'Hlp_UpperArm_\\L',
        'Upperarm02_\\L', '\\LArmJiggle', '\\LShldrTwist', 'J_Sec_\\L_UpperArm',
        'UpArmLow_\\L', 'UpArmUp_\\L', 'N_Hkata_\\L', 'ArmAux_\\L',
        'Arm_\\Left_Shoulder_Adj_1', 'Arm_\\Left_Shoulder_Adj_2',
        'Arm_\\Left_Shoulder_Adj_3', 'Arm_\\Left_Shoulder_Adj_4',
        'Arm_\\Left_Shoulder_2_Adj_1', 'Arm_\\Left_Shoulder_2_Adj_2',
        'Arm_\\Left_Shoulder_2_Adj_3', 'Arm_\\Left_Shoulder_2_Adj_4',
    ],
    'Bip01 {S} Forearm': [
        'Elbow1_\\L', 'Elbow2_\\L', 'Elbow3_\\L', 'Elbow+1_\\L', 'Elbow+2_\\L',
        'Elbow01_\\L', 'Elbow02_\\L', 'Elbow03_\\L', 'Elbow04_\\L', 'Elbow05_\\L',
        'HandTwist_\\L', 'HandTwist1_\\L', 'HandTwist2_\\L', 'HandTwist3_\\L',
        'HandTwist4_\\L', 'HandTwist5_\\L', 'HandTwist+1_\\L', 'HandTwist+2_\\L',
        'HandTwist+3_\\L', 'HandTwist+4_\\L', 'HandTwist+5_\\L',
        '\\Left_Hand_1', '\\Left_Hand_2', '\\Left_Hand_3', '\\Left_Hand_Twist',
        '\\Left_Hand_Twist_1', '\\Left_Hand_Twist_2', 'ElbowSleeve_\\L',
        'WristAux_\\L', 'ElbowTwist_\\L', 'ElbowTwist2_\\L', 'ElbowW_\\L',
        'ElbowW2_\\L', 'SleeveElbow_\\L', 'Elbow_Sleeve_\\L', 'SleeveMouth_\\L',
        'ElbowRotation_\\L', 'HandTwistRotation1_\\L', 'HandTwistRotation2_\\L',
        'DEF_Upper_Arm_Elbow_\\L', 'DEF_Forearm_Twist_25_\\L',
        'DEF_Forearm_Twist_50_\\L', 'DEF_Forearm_Twist_75_\\L', '+Elbow_\\L',
        'Elbowa_\\L', 'Arm_\\Left_Wrist_Adj', 'Arm_\\Left_Forearm',
        '\\Left_Forearm_Twist', '\\LHandEX', '\\L_Sub_Elbow', 'B_\\L_ArmRoll',
        'Bip_\\L_ForeTwist', 'Bip_\\L_ForeTwist1', 'Bip_\\L_ForeTwist2',
        'Bip_\\L_ForeTwist3', 'Bip_\\L_ForeTwist4', 'Bip_\\L_ForeTwist5',
        'Bip_\\L_ForeTwist6', 'Bip_\\L_ForeTwist7', 'Bip_\\L_ForeTwist8',
        'Bip_\\L_ForeTwist9', 'Bip_\\L_Elbow', 'Bip_\\L_Ulna',
        'Bip_\\L_Wrist', 'Arm_\\Left_Wrist_Ctr', '\\L_Elb_Ast', '\\L_Wrist_Ast',
        'Foretwist_\\L', 'Foretwist1_\\L', 'ElbowS_\\L', 'Arm\\L2Sub',
        '\\LTekubiJb', '\\LArmTwistjb', '\\LElbowJb', '\\LForeArmSub',
        'TW_Elb_\\L', '\\LeftElbowRoll', '\\LeftForeArmRoll', '\\LeftForeArmRoll1',
        '\\LeftForeArmRoll2', 'ElbowMiddle_\\L', 'ElbowLower1_\\L',
        'ElbowLower2_\\L', 'Bip_\\L_Forearm_Rig', 'Bip_\\L_Forearm_Twist',
        '\\L_Tekubi', 'Forearm02_\\L', 'Wrist_\\L_001', 'Cf_D_Hand_\\L',
        'Elbo_\\L', 'Forearm01_\\L', 'Elboback_\\L', '\\L_Wrist_EX',
        'HandHelperBone_\\L', 'BK_\\L_Elbow_00', 'BK_\\L_Elbow_01',
        'BK_\\L_Elbow_02', 'BK_\\L_Elbow_03', 'BK_\\L_Elbow_04',
        'Wrist\\LT_Roll_01', 'ForArm\\LT_Roll_01', 'Arm_\\Left_Forearm_Adj_1',
        'H_Elbow\\L', 'Bip_ForearmTwistTop_\\L', 'Bip_ForearmTwistMiddle_\\L',
        'Bip_ForearmTwistBottom_\\L', 'Bip_ElbowFront_\\L', 'Bip_ElbowBackTop_\\L',
        'Bip_ElbowBack_\\L', 'Bip_WristTwistBase_\\L', 'Bip_WristTwistOut_\\L',
        'Bip_WristTwistN_\\L', 'Bip_WristTwistIn_\\L', 'Bip_WristTwistS_\\L',
        'Hlp_Wrist_\\L', 'Hlp_LowerArm_\\L', 'Lowerarm02_\\L', '\\LElbowJiggle',
        '\\LForearmJiggle1', '\\LForearmJiggle2', '\\LCuffsMain',
        '\\LForearmTwist', 'J_Sec_\\L_LowerArm', 'ForearmLow_\\L',
        'ForearmUp_\\L', 'N_Hhiji_\\L', 'N_Hte_\\L', 'Arm_\\Left_Wrist_Tw_E',
        'Arm_\\Left_Elbow_Adj_1', 'Arm_\\Left_Elbow_Adj_2',
        'Arm_\\Left_Elbow_Adj_3', 'Arm_\\Left_Elbow_Adj_4',
    ],
    'Bip01 {S} Hand': [
        'WristSleeve_\\L', 'WristW_\\L', 'WristS_\\L', 'IndexFinger0_\\L',
        'MiddleFinger0_\\L', 'RingFinger0_\\L', 'LittleFinger0_\\L', 'DEF_Hand_\\L',
        'DEF_Halm_01_\\L', 'DEF_Halm_02_\\L', 'DEF_Halm_03_\\L', 'DEF_Halm_04_\\L',
        'Arm_\\Left_Hand', '\\LIndexN', '\\LMiddleN', '\\LRingN', '\\LPinkyN',
        '\\LHandSub', '\\LeftHandIndex0', '\\LeftHandMiddle0', '\\LeftHandRing0',
        '\\LeftHandPinky0', '\\Lf_Metacarpal', '\\Left_Hand_001', '\\Left_Hand_005',
        '\\L_Finger_Ctr', 'Bip_\\L_Carpal1', 'Bip_\\L_Carpal2', 'Bip_\\L_Carpal3',
        'Bip_\\L_Carpal4', 'Offset_Hand_\\L', '\\L_FingerBase', 'BK_\\L_Hand_00',
        'BK_\\L_Hand_01', 'BK_\\L_Hand_02', 'BK_\\L_Hand_03', 'BK_\\L_Hand_04',
        'Arm_\\Left_Finger_5_Base', '\\Left_Finger_Index_Metacarpal',
        '\\Left_Finger_Ring_Metacarpal', 'Metacarpal1_\\L', 'Metacarpal2_\\L',
        'Metacarpal3_\\L', 'Metacarpal4_\\L', '\\LCarpal1', '\\LCarpal2',
        '\\LCarpal3', '\\LCarpal4', 'Arm_\\Left_Fist', 'J_Pinkybase_\\L',
        'J_Ringbase_\\L',
    ],
    'Bip01 {S} Thigh': [
        'LegD_\\L', 'LegD_001_\\L', '+LegD_\\L', '\\Left_Foot_D',
        '\\Left_Foot_Complement', '\\Left_Foot_Supplement', 'LegcntEven_\\L',
        '\\LLegTwist1', '\\LLegTwist2', '\\LLegTwist3', '\\Left_Leg_Twist',
        'LegW_\\L', 'LegW2_\\L', 'LowerKnee_\\L', 'UpperKnee_\\L', 'LegX1_\\L',
        'LegX2_\\L', 'LegX3_\\L', '\\Left_Knee_EX', '\\Left_Foot_EX', 'KneeEX_\\L',
        'LegEX_\\L', 'Leg+_\\L', 'Leg++_\\L', 'Leg+++_\\L', 'Leg++++_\\L',
        'Knee++_\\L', 'Peaches_\\L', 'DEF_Thigh_Sub_\\L', 'DEF_Thigh_01_\\L',
        'DEF_Thigh_02_\\L', 'DEF_Thigh_Twist_25_\\L', 'DEF_Thigh_Twist_50_\\L',
        'DEF_Thigh_Twist_75_\\L', 'Leg_\\Left_Thigh_Adj_1', 'Leg_\\Left_Thigh_Adj_2',
        'Leg_\\Left_Thigh_Adj_3', 'B_\\L_LegHelper', 'B_\\L_Knee',
        'Leg_\\Left_Thigh_Ctr', 'Leg_\\Left_Knee_Ctr', 'B_\\L_Hiza01',
        'B_\\L_Pelvis_Hojo01', 'Bip_\\L_ThighTwist', 'Bip_\\L_ThighTwist1',
        '\\L_KneeUpper', '\\L_Tro_Ast', 'Momotwist_\\L', 'Momotwist2_\\L',
        'Momoniku_\\L', '+_\\Left_Foot_D', 'LegDS_\\L', '\\Left_Leg_2',
        '\\Left leg 2', '\\LComaneciJb', '\\LeftHipsRoll', '\\LeftUpLegRoll',
        '\\LeftKneeUp', 'Bip_\\L_Thigh_Rig', 'Leg(\\L)_0', 'Thigh01_\\L',
        'Thigh02_\\L', 'Thigh03_\\L', '\\L_Leg_EX', '\\L_Knee_EX',
        'Knee\\LT_Roll_01', 'Hip\\LT_Roll_01', 'Leg_\\Left_Hip_Adj',
        'Leg_\\Left_Knee_Adj', 'Leg_\\Left_Thigh_Adj', 'Bip_ThighTwistTop_\\L',
        'Bip_ThighTwistBottom_\\L', 'Bip_KneeIn_\\L', 'Bip_KneeOut_\\L',
        'Hlp_Hip_\\L', 'Upperleg02_\\L', '\\LThighTwist', 'Bip_\\LThighTwist',
        'J_Sec_\\L_UpperLeg',
        '\\Left_Thigh_Twist', 'ThighLow_\\L', 'ThighUp_\\L', 'LegAux_\\L',
        'Leg_\\Left_Knee_Adj_1', 'Leg_\\Left_Knee_Adj_2', 'Leg_\\Left_Knee_Adj_3',
        'Leg_\\Left_Knee_Adj_4',
    ],
    'Bip01 {S} Calf': [
        'KneeD_\\L', 'KneeD_001_\\L', '\\Left_Knee_D', 'KneecntEven_\\L',
        '\\LTibiaTwist1', '\\LTibiaTwist2', '\\LTibiaTwist3', 'KneeW1_\\L',
        'KneeW2_\\L', 'Knee+_\\L', 'Knee+++_\\L', 'Knee++++_\\L', 'Ankle++_\\L',
        'KneeArmor2_\\L', 'KneeX1_\\L', 'KneeX2_\\L', 'KneeX3_\\L',
        'Leg_\\Left_Acc', '\\Left_Knee_Twist', '\\Left_Ankle_EX', 'AnkleEX_\\L',
        'KneeAux_\\L', 'DEF_Knee_\\L', 'DEF_Knee_02_\\L', 'DEF_Shin_01_\\L',
        'DEF_Shin_02_\\L', 'DEF_Shin_Twist_25_\\L', 'DEF_Shin_Twist_50_\\L',
        'DEF_Shin_Twist_75_\\L', 'Leg_\\Left_Ankle_Adj', '\\L_Knee_Ast',
        '\\L_HorseLink', 'KneeD2_\\L', '\\LeftKneeRoll', '\\LeftKneeLow',
        'Bip_\\L_Calf_Rig', 'Leg(\\L)01', 'Cf_D_KneeF_\\L', 'Leg02_\\L',
        'Leg03_\\L', 'KneeB_\\L', '\\L_Ankle_EX', 'BK_\\L_Knee_00',
        'BK_\\L_Knee_01', 'BK_\\L_Knee_02', 'BK_\\L_Knee_03', 'BK_\\L_Knee_04',
        'Foot\\LT_Roll_01', 'Bip_LegTwistTop_\\L', 'Bip_LegTwistBottom_\\L',
        'Bip_KneeTwistTopOut_\\L', 'Bip_KneeTwistBottomOut_\\L',
        'Bip_KneeTwistBottomIn_\\L', 'Bip_Ankle_\\L', 'Bip_AnkleHelper_\\L',
        'Hlp_Knee_\\L', 'Hlp_Foot_\\L', 'KneeUpper_\\L', 'KneeLower_\\L',
        'Lowerleg02_\\L', '\\LKneeJiggle', 'J_Sec_\\L_LowerLeg',
        '\\Left_Ankle_Twist', 'CalfLow_\\L', 'CalfUp_\\L', 'J_Asi_C_\\L',
        'Knee1_\\L', 'Knee2_\\L', 'Knee3_\\L', 'Knee4_\\L', 'Knee5_\\L',
        'Knee6_\\L', 'Knee7_\\L',
    ],
    'Bip01 {S} Foot': [
        'AnkleD_\\L', '\\Left_Ankle_D', 'AnkleEven_\\L', 'AnkleW1_\\L',
        'AnkleW2_\\L', 'ToeTipMovable_\\L', 'AnkleArmor_\\L', 'LowerUseless_\\L',
        'Ankle+_\\L', 'Ankle+++_\\L', 'Ankle++++_\\L', 'DEF_Foot_\\L', 'LegA_L',
        'Foot_Controller_\\L', 'AnkleD_001_\\L', 'Bip_\\L_Foot_Rig',
        'BK_\\L_Ankle_00', 'BK_\\L_Ankle_01', 'BK_\\L_Ankle_02', 'BK_\\L_Ankle_03',
        'BK_\\L_Ankle_04', 'Foot_\\LT_01_IK', '\\LMetatarsals',
        'Leg_\\Left_Ankle_Heel',
    ],
    'Bip01 {S} Toe0': [
        'ClawTipEX_\\L', 'ClawTipEX2_\\L', 'ClawTipThumbEX_\\L',
        'ClawTipThumbEX2_\\L', '\\Left_Toe_EX', '\\Left_Foot_Tip_EX',
        'LegTip2_\\L', 'DEF_Toe_\\L', 'Bip_\\L_Toe01', 'Bip_\\L_Toe1',
        'Bip_\\L_Toe11', 'Bip_\\L_Toe2', 'Bip_\\L_Toe21', 'ToeTip2_\\L',
    ],
    # XML2 has no eye bones -> merge into Head
    'Bip01 Head|eye': [
        '\\Left_Eye', 'Mixamorig_\\LeftEye', 'Head_Eyeball_\\Left',
        'Head_Eyeball_\\Left_1', 'FEye\\L', 'Eye\\L', '\\L_Eye', 'Eye_\\L',
        'Bip_Eyeball_\\L', 'G_Eye_\\L', 'Eyes\\L', '\\Lf_Eye', 'Eyes(\\L)',
        'Eye\\LT_01', 'J_Adj_\\L_FaceEye', 'Bip_Eye_\\L', '\\LEye', 'J_F_Eye_\\L',
        'EyeW_\\L', 'EyeLight_\\L', 'EyeReturn_\\L', '\\Left_Eye_Return',
        'Pupil_\\L', '\\Left_Pupil', '\\Left_Eye_Glint', 'Highlight_\\L',
        'F_EyeTip_\\L', 'DEF_Eye_\\L', 'EyeLight+_\\L', 'EyeRotationErase_\\L',
        'EyeFlip_\\L', '\\LeftEyeHighlight',
    ],
    # XML2 has no breast bones -> merge into chest (Spine2)
    'Bip01 Spine2|breast': [
        'Breast_\\L', 'J_Sec_\\L_Bust1', 'J_Sec_\\L_Bust2', '\\LPectoral',
        'Spine_\\Left_Breast_2', 'Breast_Def_\\L', 'DEF_Bust_01_\\L',
        'DEF_Bust_02_\\L', '\\LBreast', 'Bust1_\\L', 'Bust2_\\L',
    ],
}

MERGE_CENTER = {
    'Bip01 Pelvis': [
        'LowerBody1', 'Lowerbody2', 'Pelvis_Adj', 'Left_Hip', 'Right_Hip',
        'Feet', 'Waist02_001', 'Bip_SpineBase', 'Bip_HipFront_L',
        'Bip_HipFront_R', 'Root_Pelvis_2', 'Root_Waist', 'Root_Waist_L',
        'Root_Waist_R', 'Hiphalf_L', 'Hiphalf_R', 'PelvisDown_Sld',
        'WaistCancel', 'AbdomenLower',
    ],
    'Bip01 Spine': [
        'UpperBodyx', 'Spine_Lower_Adj', 'Spine_Middle_Adj', 'Bip_Spine0a',
        'Back_Low',
    ],
    'Bip01 Spine1': [
        'Bip_Spine1a', 'Bip_CollarHelper_L', 'Bip_CollarHelper_R', 'Back_Mid',
    ],
    'Bip01 Spine2': [
        'J_Adj_C_UpperChest', 'Spine_Upper_Adj', 'ChestUpper',
    ],
    'Bip01 Neck': [
        'Neck1', 'Neck2', 'Neckx', 'NeckW', 'NeckW2', 'Neck01', 'Neck02',
        'Neck03', 'J_Neck2', 'NeckUpper',
    ],
    'Bip01 Head': [
        'Neckx2', 'J_Adj_L_FaceEyeSet', 'J_Adj_R_FaceEyeSet', 'M_Head_Copy',
        'LeftEye', 'RightEye', 'Left_Eye', 'Right_Eye', 'OldLeftEye',
        'OldRightEye', 'Eyes', 'Jaw',
    ],
}

# Exact-name merges (CATS bone_list_weight, finger entries omitted — the
# finger classifier handles all DEF_* finger forms via word matching).
EXACT_MERGE = {
    'DEF_Zipper': 'Bip01 Spine1',
    'B_F_Mune01': 'Bip01 Spine2',
}

# ============================================================================
# Conflict rules (CATS bone_list_conflicting_names concept)
# ============================================================================
# Some alias names mean different things depending on what ELSE is in the rig.
# Each rule: (required_aliases, demoted_alias, merge_target) — when ALL
# required aliases are present, the demoted bone loses rename eligibility and
# its weights merge into merge_target instead.
#
# e.g. Nintendo rigs (BotW/TotK): Leg_1_L=thigh, Leg_2_L=calf, and Knee_L is
# just a kneecap helper — but on rigs WITHOUT Leg_2, Knee_L IS the calf.

CONFLICT_RULES = [
    # Nintendo leg chain: Knee is a helper when Leg_1+Leg_2 are both present
    (['Leg_1_\\L', 'Leg_2_\\L'], 'Knee_\\L', 'Bip01 {S} Calf'),
    # Same idea with Calf naming
    (['Thigh_\\L', 'Calf_\\L'], 'Knee_\\L', 'Bip01 {S} Calf'),
    # 'Lower_Arm'/'Lower_Leg' rigs where Elbow/Knee are helper bones (CATS)
    (['Lower_Arm_\\L'], 'Elbow_\\L', 'Bip01 {S} Forearm'),
    (['Lower_Leg_\\L'], 'Knee_\\L', 'Bip01 {S} Calf'),
    # UpLeg/Leg rigs: 'Leg' is the calf, so a bare 'Knee' is a helper
    (['\\LeftUpLeg', '\\LeftLeg'], 'Knee_\\L', 'Bip01 {S} Calf'),
]


def build_conflict_rules():
    """Expand CONFLICT_RULES into per-side lowercase tuples.

    Returns:
        List of (required_set, demoted_name, merge_target) with all names
        lowercase and side-expanded.
    """
    rules = []
    for required, demoted, target_tpl in CONFLICT_RULES:
        for side in ('L', 'R'):
            req = {expand_side(r, side).lower() for r in required}
            dem = expand_side(demoted, side).lower()
            target = target_tpl.replace('{S}', side)
            rules.append((req, dem, target))
    return rules

# ============================================================================
# Face bone detection -> merge into Bip01 Head
# ============================================================================

# Matched as whole '_'-separated segments of the normalized lowercase name
# (safe for short words: 'ear' won't hit 'Forearm', 'lip' won't hit 'Flip').
FACE_SEGMENT_KEYWORDS = {
    'eye', 'eyes', 'ear', 'lip', 'lips', 'jaw', 'chin', 'brow', 'lid',
    'iris', 'face', 'fang', 'nose', 'kuti', 'mayu',
}

# Matched as substrings anywhere in the normalized lowercase name
# (long/distinctive enough to be safe).
FACE_SUBSTRING_KEYWORDS = (
    'cheek', 'eyelid', 'eyelash', 'eyebrow', 'eyeball', 'pupil', 'tongue',
    'teeth', 'tooth', 'mouth', 'forehead', 'temple_face', 'nostril',
    'beard', 'mustache', 'moustache', 'whisker', 'smile', 'dimple',
    'blush', 'facial', 'viseme', 'eyetracking',
)


def is_face_bone(normalized_lower):
    """Return True if a normalized lowercase bone name looks like a face bone.

    e.g. 'cheek_u_l', 'eye_l_001', 'mouthcorner_r', 'jaw_open'.
    """
    for kw in FACE_SUBSTRING_KEYWORDS:
        if kw in normalized_lower:
            return True
    for seg in normalized_lower.split('_'):
        if seg in FACE_SEGMENT_KEYWORDS:
            return True
    return False


# ============================================================================
# Finger classification
# ============================================================================
# Returns (side, finger, sort_hint) where side in {'L','R'},
# finger in {'thumb','index','middle','ring','little'}, sort_hint orders
# segments within one finger chain (hierarchy depth is the primary order;
# this is the tiebreaker). Returns None for non-finger names.
#
# Segment numbers from names are NOT trusted directly — schemes disagree
# (MMD Thumb0/1/2 vs VRoid Thumb1/2/3 vs Max Finger0/01/02). The converter
# sorts each chain by hierarchy depth and assigns proximal/mid/distal from
# the chain order, which is scheme-independent.

_FINGER_WORDS = {
    'thumb': 'thumb', 'thmb': 'thumb', 'oya': 'thumb',
    'index': 'index', 'indexfinger': 'index', 'fore': 'index', 'inde': 'index',
    'hito': 'index',
    'middle': 'middle', 'middlefinger': 'middle', 'mid': 'middle',
    'midd': 'middle', 'naka': 'middle',
    'ring': 'ring', 'ringfinger': 'ring', 'third': 'ring', 'thirdfinger': 'ring',
    'kusu': 'ring',
    'little': 'little', 'littlefinger': 'little', 'litt': 'little',
    'pinky': 'little', 'pinkie': 'little', 'pinkey': 'little', 'ko': 'little',
}
_FINGER_COMPACT = {
    't': 'thumb', 'i': 'index', 'm': 'middle', 'ri': 'ring', 'li': 'little',
    'if': 'index', 'mf': 'middle', 'rf': 'ring', 'sf': 'little',
}
# Letter schemes — there are TWO conflicting conventions in the wild:
#   UNDERSCORED 'Finger_A_1_L' / 'L_Finger_A1':  A=thumb .. E=little
#   FUSED 'FingerA1_L' / 'LFingerA1' (VRoid):    A=little .. D=index (no thumb)
# CATS confirms both: '\L_Finger_A1' -> Thumb0 but '\LFingerA1' -> Little1.
_FINGER_LETTERS_THUMB_FIRST = {'a': 'thumb', 'b': 'index', 'c': 'middle',
                               'd': 'ring', 'e': 'little'}
_FINGER_LETTERS_REVERSED = {'a': 'little', 'b': 'ring', 'c': 'middle',
                            'd': 'index', 'e': 'thumb'}
# Numeric scheme (3ds Max style): Finger0..4 = thumb..little.
# '5' appears in 1-based rigs (FINGER51 = little) — mapped as little.
_FINGER_NUMBERS = {'0': 'thumb', '1': 'index', '2': 'middle', '3': 'ring',
                   '4': 'little', '5': 'little'}
_SEGMENT_WORDS = {'metacarpal': 0, 'proximal': 1, 'intermediate': 2,
                  'medial': 2, 'distal': 3, 'tip': 4, 'end': 4, 'nail': 4}
_SEGMENT_LETTERS = {'a': 1, 'b': 2, 'c': 3}

_WORD_ALT = '|'.join(sorted(_FINGER_WORDS, key=len, reverse=True))
_COMPACT_ALT = '|'.join(sorted(_FINGER_COMPACT, key=len, reverse=True))
_SEGWORD_ALT = '|'.join(sorted(_SEGMENT_WORDS, key=len, reverse=True))
_SIDE_ALT = 'left|right|l|r'
_SEG_ALT = rf'\d{{1,2}}|{_SEGWORD_ALT}|[a-c]'

# All patterns run against the NORMALIZED lowercase name ('_' separators only).
# Optional decorations: leading 'j'/'h'/'b'/'def' prefixes, 'bip'/'hand'/'f'
# joiners, trailing '_001'-style numeric suffix (stripped before matching).
_FINGER_PATTERNS = [
    # --- BG3/Larian: arm_left_finger_1a (1-based finger1=thumb..5=little,
    #     a/b/c segments). Requires the 'arm_' prefix so it can't shadow the
    #     0-based Max scheme below.
    re.compile(
        rf'^arm_(?P<side>{_SIDE_ALT})_finger_?(?P<fnum1>[1-5])(?P<seg>[a-c])?$'),
    # --- side prefix, word finger: l_thumb_02 / lefthandindex1 / j_bip_l_thumb1
    re.compile(
        rf'^(?:[jhbf]_|def_)?(?:bip_)?(?P<side>{_SIDE_ALT})_?(?:hand_?|f_?)?'
        rf'(?P<word>{_WORD_ALT})_?(?P<seg>{_SEG_ALT})?$'),
    # --- word finger, side suffix: thumb_2_l / indexfinger1_r / j_oya_a_l
    re.compile(
        rf'^(?:[jhbf]_|def_)?(?:bip_)?(?:hand_?|f_?)?(?P<word>{_WORD_ALT})_?'
        rf'(?P<seg>{_SEG_ALT})?_?(?P<side>{_SIDE_ALT})$'),
    # --- UNDERSCORED letter scheme (A=thumb), side suffix: finger_a_2_l
    re.compile(
        rf'^finger_(?P<uletter>[a-e])_?(?P<seg>\d{{1,2}})?_?(?P<side>{_SIDE_ALT})$'),
    # --- UNDERSCORED letter scheme, side prefix: l_finger_a1 / left_finger_c_2
    re.compile(
        rf'^(?P<side>{_SIDE_ALT})_?finger_(?P<uletter>[a-e])_?(?P<seg>\d{{1,2}})?$'),
    # --- FUSED letter scheme (A=little, VRoid), side suffix: fingera1_l
    re.compile(
        rf'^finger(?P<fletter>[a-e])_?(?P<seg>\d{{1,2}})?_?(?P<side>{_SIDE_ALT})$'),
    # --- FUSED letter scheme, side prefix: lfingera1 / l_fingerd2
    re.compile(
        rf'^(?P<side>{_SIDE_ALT})_?finger(?P<fletter>[a-e])_?(?P<seg>\d{{1,2}})?$'),
    # --- numeric scheme (Max style), side prefix: l_finger0 / leftfinger21
    re.compile(
        rf'^(?:bip_?)?(?P<side>{_SIDE_ALT})_?finger_?(?P<fnum>\d)(?P<seg>\d)?$'),
    # --- numeric scheme, side suffix: finger0_l / finger21_r
    re.compile(
        rf'^(?:bip_?)?finger_?(?P<fnum>\d)(?P<seg>\d)?_?(?P<side>{_SIDE_ALT})$'),
    # --- MMD 1-based two-token scheme, side suffix: finger1_2_l (finger1=thumb)
    re.compile(
        rf'^finger(?P<fnum1>[1-5])_(?P<seg1>[1-9])_?(?P<side>{_SIDE_ALT})$'),
    # --- compact forms with mandatory segment: t_1_l / if2_r / mf3_l / sf1_l
    re.compile(
        rf'^(?P<compact>{_COMPACT_ALT})_?(?P<seg>\d{{1,2}}|[a-c])_?(?P<side>{_SIDE_ALT})$'),
    re.compile(
        rf'^(?P<side>{_SIDE_ALT})_?(?P<compact>{_COMPACT_ALT})_?(?P<seg>\d{{1,2}}|[a-c])$'),
    # --- sideless word forms (side recovered from a stripped LT/RT suffix,
    #     e.g. 'ThumbA_LT_01' -> 'thumba'): only used when forced_side is set
    re.compile(
        rf'^(?:[jhbf]_|def_)?(?:bip_)?(?:hand_?|f_?)?(?P<word>{_WORD_ALT})_?'
        rf'(?P<seg>{_SEG_ALT})?$'),
]

_TRAILING_COPY_RE = re.compile(r'_\d{3}$')   # strip Blender '.001' (normalized to _001)
_TRAILING_T01_RE = re.compile(r'(lt|rt)_\d{2}$')  # VRoid-style 'thumba_lt_01'


def classify_finger(normalized_lower):
    """Classify a normalized lowercase bone name as a finger bone.

    Returns:
        (side, finger, sort_hint, letter) or None.
        side: 'L' or 'R'; finger: thumb/index/middle/ring/little;
        sort_hint: int used as a within-chain ordering tiebreaker;
        letter: the scheme letter ('a'-'e') when the name uses a letter
        scheme, else None. Letter schemes are AMBIGUOUS across games
        (Pokemon Masters: A=thumb; VRoid fused: A=little) — the converter
        re-anchors letter chains geometrically using this.
    """
    name = _TRAILING_COPY_RE.sub('', normalized_lower)

    # VRoid/CATS 'ThumbA_LT_01' style: thumba_lt_01 → side from LT/RT
    m = _TRAILING_T01_RE.search(name)
    forced_side = None
    if m:
        forced_side = 'L' if m.group(1) == 'lt' else 'R'
        name = name[:m.start()].rstrip('_')

    for pat in _FINGER_PATTERNS:
        m = pat.match(name)
        if not m:
            continue
        gd = m.groupdict()

        # Side
        side_tok = gd.get('side')
        if side_tok:
            side = 'L' if side_tok in ('l', 'left') else 'R'
        elif forced_side:
            side = forced_side
        else:
            continue

        # Finger identity
        letter = None
        if gd.get('word'):
            finger = _FINGER_WORDS[gd['word']]
        elif gd.get('compact'):
            finger = _FINGER_COMPACT[gd['compact']]
        elif gd.get('uletter'):
            finger = _FINGER_LETTERS_THUMB_FIRST[gd['uletter']]
            letter = gd['uletter']
        elif gd.get('fletter'):
            finger = _FINGER_LETTERS_REVERSED[gd['fletter']]
            letter = gd['fletter']
        elif gd.get('fnum') is not None:
            finger = _FINGER_NUMBERS.get(gd['fnum'])
            if finger is None:
                return None
        elif gd.get('fnum1') is not None:
            # MMD 1-based: finger1=thumb .. finger5=little
            mmd_map = {'1': 'thumb', '2': 'index', '3': 'middle',
                       '4': 'ring', '5': 'little'}
            finger = mmd_map.get(gd['fnum1'])
            if finger is None:
                return None
        else:
            continue

        # Sort hint from segment token (ordering tiebreaker only).
        # -1 = no segment in the name; 0 = EXPLICIT metacarpal marker.
        seg_tok = gd.get('seg') or gd.get('seg1')
        if seg_tok is None:
            hint = -1
        elif seg_tok in _SEGMENT_WORDS:
            hint = _SEGMENT_WORDS[seg_tok]
        elif seg_tok in _SEGMENT_LETTERS:
            hint = _SEGMENT_LETTERS[seg_tok]
        else:
            try:
                hint = int(seg_tok)
            except ValueError:
                hint = 0
        return (side, finger, hint, letter)

    return None


# ============================================================================
# Table expansion → flat lookup dicts
# ============================================================================

def build_rename_lookup():
    """Build {alias_lower: (xml2_target, priority)} from the rename tables.

    Lower priority value = stronger claim when several bones alias to the
    same target (list order = CATS priority order).
    """
    lookup = {}

    def add(alias, target, prio):
        key = alias.lower()
        existing = lookup.get(key)
        if existing is None or prio < existing[1]:
            lookup[key] = (target, prio)

    for target_tpl, patterns in RENAME_SIDED.items():
        for side in ('L', 'R'):
            target = target_tpl.replace('{S}', side)
            for prio, pat in enumerate(patterns):
                add(expand_side(pat, side), target, prio)

    for target, patterns in RENAME_CENTER.items():
        for prio, pat in enumerate(patterns):
            add(pat, target, prio)

    return lookup


def build_merge_lookup():
    """Build {alias_lower: xml2_target} from the merge tables."""
    lookup = {}

    for target_tpl, patterns in MERGE_SIDED.items():
        # Strip disambiguation suffix ('Bip01 Head|eye' -> 'Bip01 Head')
        target_tpl = target_tpl.split('|')[0]
        for side in ('L', 'R'):
            target = target_tpl.replace('{S}', side)
            for pat in patterns:
                lookup.setdefault(expand_side(pat, side).lower(), target)

    for target, patterns in MERGE_CENTER.items():
        for pat in patterns:
            lookup.setdefault(pat.lower(), target)

    for name, target in EXACT_MERGE.items():
        lookup.setdefault(name.lower(), target)

    return lookup


def build_spine_alias_set():
    """Build the lowercase set of spine-chain alias names."""
    return {a.lower() for a in SPINE_CHAIN_ALIASES}
