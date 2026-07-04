"""Shared texture-resolution cap enum, used by BOTH the IGB Actor 'Export Skin'
and the map 'Export Scene IGB' operators so there is ONE consistent
"Max Texture Size" control everywhere.

Textures are ~80-90% of an exported IGB's size, so this cap is by far the
biggest lever on file size. It caps the LONGEST edge (downscale only, never
upscales; short edge scales proportionally; snaps to power-of-2). The default
is 512 — native characters mostly use 256-512px skins and a model is only
~100-200px on screen, so 512 is already 2-4x oversampled. The FIRST item is the
EnumProperty default, so '512' must stay first on both operators.
"""


def texture_size_items(self, context):
    return [
        ('512', "512  (Standard)",
         "Cap every texture (diffuse, normal, specular, gloss) to 512px on the "
         "longest edge — the project standard. ~4x smaller than 1024 and "
         "visually identical in-game; matches native assets"),
        ('256', "256  (Smallest)",
         "Cap every texture to 256px. ~16x smaller than 1024, but noticeably "
         "soft for most tastes — only for tight size budgets"),
        ('1024', "1024",
         "Cap every texture to 1024px on the longest edge"),
        ('2048', "2048  (High detail)",
         "Cap every texture to 2048px. Large files — use only for hero/close-up "
         "assets that genuinely need the detail"),
        ('original', "Original  (Largest)",
         "Keep each texture's full source size (rounded up to power-of-2). "
         "Biggest files; use only if you need maximum texture detail"),
    ]
