"""IGB Actor module — skeleton, skinning, and animation import/export.

Provides a separate "IGB Actors" N-panel for importing characters with
their skeletons, skins, and animations from Alchemy Engine IGB files.
"""


def register():
    from . import properties
    from . import operators
    from . import panels
    from . import active_sync
    properties.register()
    operators.register()
    panels.register()
    active_sync.register()


def unregister():
    from . import active_sync
    from . import panels
    from . import operators
    from . import properties
    active_sync.unregister()
    panels.unregister()
    operators.unregister()
    properties.unregister()
