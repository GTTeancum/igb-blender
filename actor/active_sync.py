"""Auto-sync the IGB Actors panel to the user's selection.

With several skins imported in one .blend (e.g. 0101 + 0102 + a converted
custom character), the panel's Active Armature had to be switched by hand.
Now clicking any object of a skin — or its collection in the outliner —
points the IGB Actors tab at that skin automatically.

Mechanism: msgbus subscriptions on the view layer's active object and
active collection, re-armed on file load (msgbus does not persist).
"""

import bpy
from bpy.app.handlers import persistent

_OWNER = object()


def _actor_armature_of(obj):
    """The imported-actor armature an object belongs to, or None."""
    seen = 0
    while obj is not None and seen < 32:
        if obj.type == 'ARMATURE' and (
                "igb_skeleton_name" in obj or "igb_anim_file" in obj):
            return obj
        obj = obj.parent
        seen += 1
    return None


def _armature_in_collection(coll):
    """First imported-actor armature inside a collection (recursive)."""
    if coll is None:
        return None
    try:
        for obj in coll.all_objects:
            if obj.type == 'ARMATURE' and (
                    "igb_skeleton_name" in obj or "igb_anim_file" in obj):
                return obj
    except Exception:
        pass
    return None


def _set_active(arm):
    if arm is None:
        return
    try:
        props = bpy.context.scene.igb_actor
        if props.active_armature != arm.name:
            props.active_armature = arm.name
    except Exception:
        pass


def _on_active_object(*_args):
    try:
        obj = bpy.context.view_layer.objects.active
    except Exception:
        return
    _set_active(_actor_armature_of(obj))


def _on_active_collection(*_args):
    try:
        lc = bpy.context.view_layer.active_layer_collection
        coll = lc.collection if lc else None
    except Exception:
        return
    _set_active(_armature_in_collection(coll))


def _subscribe():
    bpy.msgbus.clear_by_owner(_OWNER)
    try:
        bpy.msgbus.subscribe_rna(
            key=(bpy.types.LayerObjects, "active"),
            owner=_OWNER, args=(), notify=_on_active_object)
    except Exception:
        pass
    try:
        bpy.msgbus.subscribe_rna(
            key=(bpy.types.ViewLayer, "active_layer_collection"),
            owner=_OWNER, args=(), notify=_on_active_collection)
    except Exception:
        pass


@persistent
def _on_load_post(_dummy):
    _subscribe()


def register():
    _subscribe()
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister():
    try:
        bpy.msgbus.clear_by_owner(_OWNER)
    except Exception:
        pass
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
