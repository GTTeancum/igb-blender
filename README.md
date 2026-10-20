> **Separate Windows app:** [Alchemy Batch Converter](tools/alchemy_batch/README.md)
> provides folder scanning and batch conversion using this add-on's backend.
> Download its `batch-converter-v0.1.0` release from the GTTeancum fork.

# IGB Blender Addon

**Version 0.6.0** — see [CHANGELOG.md](CHANGELOG.md) for what's new.

A Blender 4.4 addon for importing and exporting Alchemy Engine IGB/IGZ files used by **X-Men Legends 1 & 2** and **Marvel Ultimate Alliance**.

Includes a full map maker toolkit (with **Import Full Map** — load any game map with its entities, NPCs, motion paths and audio, then edit and rebuild it), a game-accurate **Simulate** + **Game Shading** preview, an actor/skeleton/animation pipeline, a rig converter for custom characters, a full **Conversation Editor** with in-game-style preview and voice playback, and a texture editor for creating and modifying game textures.

> Tip: set **Game Data Dir** once (Map Maker → Scene → Paths → **Set Game
> Folder…**, pointing at the folder that contains `maps/` and `actors/`) —
> voice playback, NPC loading, conversations and scene audio all depend on it.

Created by **Kaiko** with the aid of **Claude**.

---

## Installation

**Recommended — install the release ZIP:**

1. Go to the [Releases page](https://github.com/KaikoClanworth1/igb-blender/releases)
   and download the **`igb_blender.zip`** file listed under **Assets** for the
   latest release.
2. In Blender open **Edit > Preferences > Add-ons**, click the **▾** menu
   (top-right) and choose **Install from Disk…** (on older builds: the **Install…**
   button), then pick the downloaded `igb_blender.zip`. You can also just drag the
   zip into the Blender window.
3. Search for **"IGB Format"** in the add-on list and tick the checkbox to enable it.
4. The addon adds five sidebar tabs (press `N` in the 3D viewport): **IGB**,
   **IGB Actors**, **Map Maker**, **Menu Editor**, and **Texture Editor**.

> ⚠️ **Do NOT use the green "Code → Download ZIP" button** (or the "Source code (zip)"
> link on a release). That archive unpacks to a folder named `igb-blender-master`
> / `igb-blender-0.4.0`, and the hyphens make it an invalid Python module name, so
> Blender will install it but **fail to enable** it. Always use the
> **`igb_blender.zip`** asset from the Releases page.

**Manual install (advanced):** copy the inner `igb_blender` folder so it lands at
`<Blender>/scripts/addons/igb_blender/` (the folder must be named exactly
`igb_blender`), then enable it as in step 3.

### PySide6 (Optional)

The standalone Conversation Editor window requires PySide6. You can install it from the **IGB > IGB Extras** panel inside Blender, then restart.

---

## Supported Games

| Game | Platform | Import | Export |
|------|----------|--------|--------|
| X-Men Legends I  | PS2, Xbox | IGB | - |
| X-Men Legends II | PC | IGB | IGB |
| X-Men Legends II | PS2, PSP, Xbox | IGB | - |
| Marvel Ultimate Alliance | PC | IGB | IGB |
| Marvel Ultimate Alliance | PS2, PSP, Xbox | IGB | - |
| Marvel Ultimate Alliance 2 | PS2, PSP | IGB | - |
| Marvel Ultimate Alliance 2 | Wii | - | Skin IGB (tested in OpenMUA2) |
| Marvel Ultimate Alliance 2 | PC | IGZ (Broken) | - |

> **IGZ Note:** Mesh geometry mostly imports but textures are not working yet.

The addon auto-detects the game format on import. You can also pick a game preset manually in the import dialog.

---

## File Menu: Import / Export

These show up in **File > Import** and **File > Export**.

### Import IGB / IGZ

Opens an Alchemy Engine file and brings geometry, materials, textures, collision, and lights into Blender.

**Options in the import dialog:**
- **Game Preset** - Auto-detect or pick a specific game/platform.
- **Import Normals** - Bring in vertex normals.
- **Import UVs** - Bring in texture coordinates.
- **Import Vertex Colors** - Bring in per-vertex color data.
- **Import Materials** - Create Blender materials with the embedded textures.
- **Import Collision** - Import collision hull as mesh in a "Colliders" collection.
- **Import Lights** - Import scene lights.
- **Texture Directory** (IGZ only) - Point to the `materials/` folder for external textures.

### Export IGB

Exports your scene to an IGB file the game can load. Uses a template-based approach (reads a reference IGB and patches in your data).

**Options in the export dialog:**
- **Texture Format** - Choose the encoding:
  - *CLUT (Universal)* - 256-color palette. Works in both XML2 and MUA. Recommended.
  - *DXT5 (XML2 Only)* - DXT5 with standard color endpoints.
  - *DXT5 (MUA Only)* - DXT5 with swapped color endpoints for MUA.
- **Collision Source** - Where to get collision geometry:
  - *Colliders Collection* - Use objects from the "Colliders" collection.
  - *Visual Mesh* - Auto-generate from the visible geometry.
  - *None* - Skip collision.
- **Surface Type** - Physics surface (0 = default, 1 = stone, 12 = wood, etc.).
- **Export Lights** - Include scene lights in the output.

### Import / Export ZAM

Opens or saves `.zam` minimap files used by the in-game automap. Available in File > Import/Export.

---

## Sidebar Panels

Press `N` in the 3D Viewport to open the sidebar. The addon adds three tabs:

---

### IGB Tab

The main tools tab. Everything for working with IGB files outside of characters and map building.

#### Import / Export
Quick-access buttons for importing IGB, IGZ, and ENGB files, plus exporting IGB. Same functionality as the File menu entries but right in the sidebar. Also has a **Models Directory** path used when importing ENGB files (so it can load the referenced entity models).

#### Collision
Tools for creating and managing collision geometry that gets baked into the IGB file on export.

- **Generate Colliders** - Three auto-generation methods:
  - *Box Colliders* - One bounding box per mesh object. Fastest, good for blocky geometry.
  - *Convex Hulls* - One convex hull per object. Tighter fit for organic shapes.
  - *Decimated Mesh* - Merges all visible geometry and reduces triangle count. Best surface coverage.
- **Manual Tools**:
  - *Create Colliders Collection* - Makes the "Colliders" collection if it doesn't exist.
  - *Add Box at Cursor* - Drops a box collider at the 3D cursor.
  - *Merge Colliders* - Joins all collider objects into one mesh and welds seam vertices.
- **Status** - Shows collider count and triangle count. Warns if you exceed the game engine's ~15,000 triangle limit.

#### Materials
- **Convert All to IGB** - Stamps IGB custom properties (lighting, blend, alpha, color) onto every material in the scene. Does this by analyzing Blender's material settings (transparency, backface culling, etc.) and setting appropriate IGB values.
- Shows a status count of how many materials are IGB-ready vs still need converting.

#### Quick Tools
Batch operators that apply a setting to every IGB material at once. Useful for quickly tweaking all materials in a scene. Has a **Selected Only** toggle to limit changes to selected objects.

Sub-panels:
- **Lighting** - Toggle scene lighting on/off. Off = fully bright (for lava, sky, energy effects).
- **Backface Culling** - Toggle single-sided vs double-sided faces.
- **UV Animation** - Enable UV scrolling/rotation (for water, conveyor belts, energy beams).
- **Alpha Test** - Toggle cutout transparency (for foliage, fences, grates).
- **Blend State** - Toggle semi-transparency (for glass, water, smoke).
- **Color Tint** - Set the color multiplier on all materials. White = no tint.
- **Shader** - Batch set shader presets (e.g. additive glow, standard opaque).
- **Material Colors** - Batch set diffuse, ambient, specular, emission, and shininess.

#### IGB Extras
- **Merge Duplicate Materials** - Cleans up `.001` / `.002` material duplicates by merging materials that share the same texture and IGB properties.
- **PySide6 Install** - One-click install of PySide6 for the external editor windows.

#### Credits & References
Lists the open-source projects this addon references.

---

### IGB Actors Tab

Everything for working with game characters: skeletons, skins, animations, and rigs.

#### Import
- **Game Directory** - Set the path to your game install (so the addon can find actor IGB files in `actors/`).
- **Import Actor** - Pick a character and import their full skeleton, skin meshes, and animations.
- **Import Options** - Toggle whether to import skins, animations, and/or materials.
- **Skin Setup** - Appears when you select any armature. Detects whether the rig is already an XML2 Bip01 skeleton or a foreign rig (VRChat/Unity/Mixamo) and handles each case:
  - **Existing Bip01 rigs** - Computes and stores the skeleton metadata needed for export (inverse bind matrices, bone translations, bone info) without modifying the rig or mesh. Bones stay exactly where they are.
  - **Non-XML2 rigs** - Full conversion: renames bones to XML2 names, rebuilds the hierarchy, creates missing bones, computes skeleton data, and round-trips the mesh through export/reimport for a clean game-ready result.
- **Rig Profile** - Selects the bone naming convention of the source rig (VRChat, Mixamo, or Mixamo Alt).
- **Target Pose** - Choose the output pose for converted rigs:
  - **T-Pose (Recommended)** - Arms horizontal. Required for compatibility with the game's existing animations (idle, walk, attack, etc.). If the source model is in A-pose, the converter will automatically repose the mesh and shape keys to T-pose.
  - **A-Pose** - Arms angled ~45° downward. Use only if you are providing custom animations for the character. **Warning:** A-pose support is experimental. The game's built-in animations assume T-pose and will cause visible arm deformation on A-pose exports. A-pose may also produce unexpected results with some source rigs — if you see broken mesh or wrong arm positions, switch to T-pose.
- **Auto Scale / Target Height** - Scales the rig to match XML2 character proportions (~68 game units tall). The scale factor is stored as a property and applied at export time — the Blender model is not resized.

#### Skins
Manages the skin mesh variants attached to an actor.

- **Skin List** - Shows all skins with visibility toggles and skin codes (e.g. `0101`).
- **Solo Selected** - Hides all skins except the selected one.
- **Import / Export Skin** - Load or save individual skin IGB files.
- **Add / Remove** - Attach a Blender mesh as a new skin or remove one.

##### Wii skin export (MUA2)

In **IGB Actor > Skins > Export Skin**, select **Platform: Wii (MUA2)**.
The preset overrides the game-specific PC format and exports a complete v6
skinned graph with **tiled RGB5A3 diffuse textures** (Wii image format 21,
image order 103). It disables normal/specular/gloss maps, mipmaps and the PC
red/blue swap. This is separate from the GameCube CMPR preset.

Choose **Texture Resolution: 256 (Smallest)** to use the tested size. The existing
resolution control still allows larger textures; those need their own in-game
memory/appearance checks. Encoded textures must have positive dimensions divisible
by four. This addition does not implement Wii texture import or scene export.

Validation used four MUA1 PC Luke Cage costumes in **OpenMUA2**, including repeated
costume cycling and loading the third costume into a level. The registered Blender
export operator reproduced those tested models byte-for-byte. Other Wii games,
original hardware, and exhaustive animation coverage have not been verified.
No game assets are included.

Asset-free regression checks:

```text
python -m unittest discover -s tests -v
blender --background --factory-startup --python-exit-code 1 --python tests/blender_skin_presets.py
```

The Blender check exercises the registered export operator with a mocked builder
boundary; it verifies preset routing, not rendered game output.

#### Segments

Manages toggleable sub-parts on actor skins. Some characters have pieces that can be shown or hidden per skin — for example, Bishop has drawn guns (in hands) and holstered guns (on hips) controlled by segments.

- **Segment Groups** - Segments are grouped by base name. Each group shows its main mesh and outline mesh together, with vertex counts and blend weight diagnostics.
- **Add Segment** - Select one or more mesh objects, click **Add Segment**, and enter a name (e.g. `gun_left`, `cape`). The addon auto-detects outline meshes from the object name (anything containing `_outline`), parents meshes to the armature, adds an Armature modifier, and registers them in the skins list. Leave the name empty to add meshes as part of the body (no segment wrapping).
- **Rename Segment** (pencil icon) - Rename an existing segment group. Updates `igb_segment_name` on all meshes in the group (both main and outline variants). Setting the name to empty converts segment meshes into body meshes.
- **Select Segment** (cursor icon) - Selects all meshes belonging to a segment group in the viewport and makes the last one active. Works for both body and named segments.
- **Visibility Toggle** (eye icon) - Click to show/hide a segment. This sets the igSegment `_nodeFlags` field: `0` = visible, `2` = hidden. Also toggles Blender viewport/render visibility to match. The game reads these flags to decide what to render for each skin.
- **Remove Segment** (X icon) - Removes all meshes in a segment group from the skins list. The mesh objects remain in the scene but are no longer part of the export.
- **Refresh** - Recomputes blend weight diagnostics for all segment meshes.
- **Body Group** - Meshes without a segment name are grouped under "Body". These are always visible and cannot be toggled or removed.
- **Round-Trip** - Segment names, flags, and structure are fully preserved through import and export.

**How to create segments for a custom skin:**

1. Model your segment mesh in Blender (e.g. a weapon, cape, or helmet piece).
2. If the segment needs an outline, duplicate the mesh and add `_outline` to its name.
3. Select the segment meshes, then click **Add Segment** in the Segments panel.
4. Enter a segment name (e.g. `gun_left`). The addon pairs main and outline meshes automatically.
5. Use the **Visibility Toggle** to set whether the segment starts visible or hidden for this skin.
6. Export the skin — each segment gets its own `igSegment` node in the IGB file.

**How segments work in-game:**
Each skin file (e.g. `1801.igb`, `1802.igb`) can have different segment visibility. For example, one Bishop skin might show drawn guns with holstered guns hidden, while another shows only holstered guns. Toggle the segments in the panel before exporting to set which parts are visible for that skin. The game can also control segments at runtime through herostat `Multipart` blocks and powerstyle `ce_skinsegment` events.

#### Animations

> **Note:** Animation import and export are currently broken and under active development.

Manages animation clips on the actor.

- **Animation List** - Shows all loaded animations with play buttons and durations.
- **Add / Remove** - Import single animations or remove them.
- **Playback Controls** - Rest Pose / Play / Stop buttons.
- **Info Box** - Duration, track count, and frame count for the selected animation.
- **Import / Save Animations** - Batch import or export all animations.

#### Materials
Shows the IGB material properties for the selected skin's material. Lets you edit render state (blend, alpha test, lighting, culling) and material colors (diffuse, ambient, specular, emission, shininess) directly. Also has **Quick Tools** buttons for applying settings across all skins at once.

---

### Map Maker Tab

A full level editor for creating custom game maps. Handles all the game files you need: `.igb` (geometry), `.engb` (entities), `.chrb` (characters), `.navb` (navigation), `.boyb` (bounding), `.pkgb` (packages/conversations).

The Map Maker uses a **tabbed layout** with four workflow views: **Scene**, **Place**, **Logic**, and **Build**. Switch between them using the tab bar at the top of the panel.

#### Scene Tab

Configure your map's identity, environment, and build paths.

**Map Identity:**
- **Map Name / Map Path** - The internal name and path the game uses to load your map.
- **Act / Level** - Which act and level number this map belongs to.
- **Zone Script** - Lua script file for the zone.

**Environment:**
- **Sound File** - Background music/ambient sound.
- **Party Light** - Light color and radius for the player party.
- **Combat Locked / No Save** - Gameplay flags.
- **Big Conv Map** - Enable if this map has NPC conversations.

**Paths & Options:**
- **Output Directory** - Where compiled game files are written.
- **Game Data Directory** - Path to the game install for asset lookups.
- **Automap Path** - Override for minimap data path (auto-generated if blank).
- **Show Previews** - Toggle 3D previews for placed entities.

#### Place Tab

Place gameplay assets into your map: entities, NPCs, and models.

**Quick Add:**
One-click buttons for common gameplay entities (NPC spawn, zone link, trigger, pickup, etc.). Each button creates an entity definition with the correct classname and default properties, then places an instance at the 3D cursor.

**Entity Definitions:**
- **Entity Definitions List** - Searchable list of entity types defined for this map.
- **Entity Details** - Edit the selected definition: name, classname, character reference, model path, collision toggle, and custom key-value properties. Known classnames (e.g. `monsterspawnerent`, `zonelinkent`) get **typed property schemas** with proper widgets (checkboxes, dropdowns, number fields) instead of raw key-value strings.
- **Placement** - Place instances of the selected entity at the 3D cursor. Select/remove placed instances. Refresh 3D previews.

**Character Database:**
- **Load Character DB** - Reads `npcstat.engb` and `herostat.engb` from your game data directory.
- **Search** - Filter by name, ID, or team.
- **Character Details** - Name, ID, team, skin, anim set.
- **Quick Place at Cursor** - Instantly creates an entity definition and places it.
- **Pick for Active Entity Def** - Sets the character on your selected entity definition.
- **NPC/Hero Stat Editor** - Opens an external editor window for npcstat or herostat files.

**Model Browser:**
- **Scan Models** - Indexes all `.igb` files in the models directory.
- **Search / Category Filter** - Find models by name or category (props, environment, etc.).
- **Quick Place at Cursor** - Creates an entity with the model and places it.
- **Import as Asset** - Import the model geometry into Blender.
- **Asset Library** - Batch import an entire category for use as Blender assets.

**Selected Entity Inspector:**
Appears on any tab when an entity Empty is selected. Shows the entity name, classname, and all custom properties inline so you can tweak placed entities without switching tabs.

#### Logic Tab

Define objectives, build conversations, and manage precache lists.

**Objectives:**
Visual objective builder for creating in-game mission goals. Each objective has a name, display text, activation mode, and one or more steps.

- **Objective List** - Create, reorder, and remove objectives.
- **Objective Details** - Name, display text, auto-activate toggle, optional flag, trigger entity, and next objective chain.
- **Steps** - Each objective has typed steps:
  - *Go to Location* - Player must reach a position. Can reference a scene object or manual coordinates. Place visual markers at the 3D cursor.
  - *Destroy Target* - Destroy a specific entity.
  - *Collect Item* - Pick up N items of a type.
  - *Interact* - Use/interact with an entity.
  - *Defeat All Enemies* - Clear all spawned enemies in an area.
  - *Custom Script* - Run arbitrary Lua script code.
- Each step can have a completion script that fires when the step is done.

**Conversations:**

> **Beta:** The external Conversation Editor GUI is in beta.

Build NPC dialogue trees that become PKGB conversation files.

- **Conversation List** - Create, remove, and select conversations.
- **Settings** - Name, file path, HUD portrait reference.
- **Edit Conversation** - Opens the external PySide6 dialogue editor (requires PySide6).
- **Dialogue Tree** - Inline tree view of all nodes. Nodes are indented to show parent/child relationships, with icons indicating type (start condition, participant, line, response) and metadata (sound, end flag, tag jump, faction gating).
- **Add Nodes** - Add start conditions, line+response pairs, individual lines, or individual responses.
- **Dialogue Preview** - Game-style preview showing how the conversation looks in-game: NPC speech boxes, player response choices, faction variants, and follow-up lines.
- **Node Properties** - Edit the selected node's text, sounds, scripts, branching tags, faction gating, and other attributes.
- **Import / Export** - Save or load conversation XML files.
- **Link to NPC** - Connect a conversation to a placed NPC entity.

**Precache & Characters:**
- **Precache List** - Resources the game should preload. Auto-scan fills this from your placed entities.
- **Characters (CHRB)** - Character list for the map's `.chrb` file. Auto-scan detects characters from placed NPC entities.

#### Build Tab

Compile, validate, and deploy your map.

**Navigation Mesh:**
- **Cell Size** - Grid resolution for the nav mesh.
- **Advanced** - Max slope angle, multi-layer support, layer separation distance.
- **Generate Nav Mesh** - Computes walkable cells from scene geometry.
- **Visualize Nav Mesh** - Shows the generated nav grid as a wireframe overlay.

**Compile & Deploy:**
- **Generate XML** - Creates the XML source files for all map components.
- **Compile XMLB** - Converts XML to the game's binary XMLB format.
- **Build All** - Runs the full pipeline (XML generation + XMLB compilation + IGB export) in one click.

**Validation:**
Pre-build checks that catch common mistakes before compiling. Click **Run Validation** to check for:
- Missing required settings (map name, paths, output directory).
- Entity definitions with missing classnames or character references.
- Placed instances referencing deleted entity definitions.
- Objectives with empty steps or missing targets.
- Results are shown inline with error/warning icons.

**Collision Preview:**
- **Preview Colliders** - Visualize the Colliders collection.
- **Preview Visual Mesh Collision** - Show auto-generated collision from visible geometry.

**Automap:**
- **Import / Export ZAM** - Load or save `.zam` minimap files.
- **Generate Automap Mesh** - Create an automap from selected floor geometry.

---

### Menu Editor Tab

A full N-panel GUI for editing game menu ENGB files (main menu, character select, load screens, etc.). Appears as its own sidebar tab.

**File Operations:**
- **Game Directory** - Path to the game install for finding menu files.
- **Load Menu ENGB** - Open a menu file from `ui/menus/`.
- **Save / Save As** - Write changes back to the ENGB file.
- **Deploy** - Copy the compiled menu to the game directory for testing.
- **Patch IGB Transforms** - Sync item positions from Blender back into the menu's IGB file.

**Items:**
- **Item List** - Searchable list of all menu items with icons (text, model, selectable).
- **Add / Remove / Duplicate / Reorder** - Full list management.
- **Focus** - Zoom the 3D viewport to the selected item.
- **Debug Filter** - Toggle visibility of debug-only items.

**Item Properties:**
Edit the selected menu item's attributes: name, type, text, font, position, rotation, scale, colors (focus/unfocus), navigation links, commands, sounds, and visibility flags.

**Precache:**
Manage precache entries for textures and models used by the menu.

---

### Texture Editor Tab

A standalone PySide6 window for viewing, replacing, and creating game texture IGB files. Works with any texture in the game: loading screens, HUD elements, fonts, skins, models, and more.

The editor builds brand-new PS2 CLUT (PSMT8) IGB files from scratch, so the output works in both XML2 and MUA regardless of the source format.

**Opening the Editor:**
- Find the **Texture Editor** tab in the sidebar and click **Open Texture Editor**.
- Requires PySide6 (install from **IGB > IGB Extras** if not already installed).

**File Operations:**
- **Open IGB** (Ctrl+O) - Load textures from an existing IGB file. Supports all formats (DXT3, DXT5, CLUT, uncompressed).
- **Create from Image** (Ctrl+I) - Build a new IGB directly from PNG, TGA, BMP, or JPG images. Multi-select supported.
- **Save** (Ctrl+S) - Overwrite the original file.
- **Save As** (Ctrl+Shift+S) - Save to a new path.

**Editing:**
- Textures display as thumbnails with name and dimensions.
- **Replace Selected** - Swap a texture with a new image file. Auto-scales and auto-flips to match the game format.
- **Swap R/B** - Swap red and blue channels on all textures (fixes color issues on some platform textures).
- **Show Mipmaps** - Toggle visibility of mipmap levels in the thumbnail grid.

**Export Resolution:**
- Choose from **Original**, **2048**, **1024**, or **512** in the toolbar dropdown.
- When set to a specific size, all textures are scaled to that square resolution on save.

> **Note:** 2048 resolution is experimental. It works in-game but the engine forces a 4:3 aspect ratio on loading screen textures, which may stretch non-4:3 source images.

---

## Properties Panel: Material State

In the **Properties Editor > Material tab**, a new **IGB Material State** panel appears when a material has IGB properties. This shows the raw Alchemy render state: blend function, alpha test, color tint, lighting, culling, UV animation, and full material color breakdown (diffuse, ambient, specular, emission, shininess). This is the detailed view for fine-tuning individual materials.

---

## Credits

Built on research and code from these projects:

- **IGBConverter** by nikita488 (MIT)
- **IGBDataExtractor** by ChmtTnky
- **igBlender** by ak2yny (GPLv3)
- **raven-formats** by nikita488 (MIT)
- **Crash-NST-Level-Editor** by kishimisu
- **IGB parser gist** by mateon1
- **fmt_alchemy_igz** by Nefarious/ak2yny
