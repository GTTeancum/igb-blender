# Changelog

## Unreleased
- **Root-cause fix for crooked animations on custom / cross-game rigs.**
  A rig whose Bip01 root is rotated off the game skeleton (a Rig Converter
  output, or an XML1-PS2 skin driven by XML2/MUA anims) played every game
  animation tilted. Import Animations now **snaps the rig's unweighted root
  bones (Bone_000/Bip01/Motion) to the game skeleton** — they carry no mesh
  weights, so not a single vertex moves — turning the rig into a proper game
  skeleton. Both XML2 and MUA then play **natively**, upright and centered,
  with no per-game retargeting. Verified on a real cross-game Cyclops rig:
  mesh moves 0.000, body deviation from the game pose 0.000 (XML2), and both
  games render on-model. Falls back to retargeting only if a *skinned* bone
  is also off (can't snap it without deforming the mesh).
- Native animation import now uses the **absolute** pose formula (the same
  one MUA uses) instead of the bind-branch, so menu idles no longer import
  as a T-pose.
- **Game animations now import correctly onto converted / cross-game rigs.**
  A rig with Bip01 bone names but different rest orientations — a Rig
  Converter output, or an XML1-PS2 skin driven by XML2-PC animations —
  played game anims with a crooked full-body lean. Import Animations now
  detects the rest mismatch and retargets through the game skeleton
  (world-delta + analytic FK, rotation-exact vs the game pose, custom
  proportions preserved). Game-imported actors are unaffected (native path).
- The mismatch detector now also fires on a **concentrated** high-impact
  difference — a single root bone like the Pelvis being ~90° off (XML1-PS2
  vs XML2-PC) tilts the whole body even though only 1-2 bones differ. It
  previously required a broad (>25% of bones) difference and missed this.
- Retarget now **absolute pose-matches** for game-anim import (the target
  reaches the game bone's world orientation) instead of delta-retargeting
  (rig rest + motion). Delta retargeting preserved a rig's wrong root rest
  as the baseline, so a rig whose Bip01 is turned ~90° (e.g. Cyclops97
  imports) kept its arms splayed out. Absolute-match reproduces the game
  pose exactly (0.0-unit joint match). VR mocap still uses delta.
- **Fixed menu idles importing as a T-pose on retargeted rigs.** The
  retarget built its source pose with the bind-branch, which maps
  "animation == bind pose" (exactly what menu idles do — they park bones at
  the bind) to the reference's *rest* pose, i.e. a T-pose, which then got
  copied onto the rig. It now builds the source with the absolute formula
  (the same one MUA uses), so menu_idle resolves to its real stance.
- Retargeted anims no longer **float off the floor / drift to a corner**
  (both games). The reference armature imports at a different scale, so
  baking its hip world-displacement mis-scaled the character up and off
  origin. The retarget now keeps the rig grounded at its rest hip height;
  per-bone translation (correct hip width) is preserved.
- Retargeted anims no longer sit **off-center / to one side**. The retarget
  rotated the rig's root chain to the game orientation, but on a rig whose
  Bip01 is turned ~90° that swings the whole body sideways off origin. The
  pelvis is now anchored at its rest position and the pose is built down
  from there (the body below the pelvis matches the game skeleton), so the
  character stays planted. Verified 0.0-unit pelvis drift, 0.000 body
  deviation from the game pose.
- **Animation export FIXED** — exported moveset .igb files were corrupt
  (every Enbaya blob's memory layout shifted; ~180° rotation errors on
  re-import). Three root causes fixed: shared memory-directory entries are
  now cloned instead of mutated when a blob resizes; the translation
  reverse used the inverted rest matrix; the rotation reverse was missing
  the bind-pose branch (XML2). Round-trip now verified at 0.01–0.02° average
  error on XML2 and MUA — quantization-exact.
- **Replace Animation in File…** (IGB Actors → Animations): swap ANY
  animation inside a moveset .igb (e.g. 01_cyclops.igb) for any Blender
  action — cross-slot (put a menu_action on the idle slot), and/or rename
  the slot (the game binds animations by name, so renaming an unused slot
  effectively adds a new animation). Untouched animations stay
  byte-identical.
- **Ground Preview** button (anchor icon next to Play): mutes root-bone
  location curves so MUA actors no longer float/slide in the viewport.
  Preview-only — export still reads the full curves, and the game applies
  root motion itself.
- ig*List count AND capacity fields both patched on keyframe-list resize;
  8 Hz low-rate blobs keep their real sample rate + signature on export.
- Regression suite: new animation-export round-trip check (18 checks).
- "Auto-Detect Game Folder" replaced with **Set Game Folder…** — a validated
  folder picker (these games are 2005/2006-era and long delisted; scanning
  Steam/GOG paths never finds them). Picking a subfolder like maps/ walks up
  to the game root automatically; confirms maps/actors/sounds are present.

## 0.6.0 — 2026-07-04

The "maps play like the game" release.

### Map import — correctness overhaul
- **Fixed wrong textures / white materials on MUA maps**: scene-graph attribute
  state is now subtree-scoped (Alchemy push/pop semantics). On stark1 the old
  state leak gave 180/468 objects the wrong textures and 411/468 the wrong
  blend/alpha state (the "white stairs").
- **Backface culling now matches each game**: XML2/MUA PC default single-sided,
  XML1 (console renderer) double-sided. Winding verified normal-consistent.
- Collision hulls no longer tint renders green (hidden from renders, still
  visible in the viewport).
- Zone-shadow decals (`zone_shadow_*`) lifted off the floor (no z-fighting) and
  excluded from shadow casting.

### Map Maker
- **Import Full Map**: pick any game map .igb — imports geometry, all entities
  (empties + defs), entity prop models, conversations, motion paths, scene
  audio, and auto-fills map name/path/game dir. Progress cursor during import.
- **Simulate Map**: NPC idles loop with per-NPC stagger via NLA, spawn
  conditions applied (script-spawned NPCs start hidden), motion paths loop,
  audio-synced playback; Stop restores everything.
- **Game Shading**: raw-sRGB view transform, baked vertex-lighting path for
  XML2 world geometry (modulate-2X), physically-converted game light energies
  (4π/q — was 10 W, now e.g. 125,000 W). Renders now look like the game.
- **Load NPC Actors**: rigged characters with idle animations placed at
  spawner empties (parented — move the empty, move the NPC).
- **Motion paths**: import (`motionpaths/*.igb` → animated empty + curve,
  auto-discovered from map scripts) AND export (template-patched back to a
  valid .igb).
- **Scene audio**: map ambience + level music decoded from the ZSNDPC banks
  into Video Sequencer strips. Bank layout mapped: `_a`=ambience, `_c`=music,
  `_m.zsm`=map SFX.
- **Auto-Detect Game Folder** button (Steam/GOG/common paths).

### Conversation Editor (full rebuild)
- **Preview mode**: game-style dialogue box with portrait, typewriter text,
  live voice playback, numbered choices (1-9), %BLANK% auto-advance, tag-jump
  hubs, Heroes/Brotherhood toggle, auto-advance timed to real voice length.
- Edit mode: + Follow-up, Duplicate, reorder ↑/↓, numbered choices in tree.
- **Audio export**: every voice line as WAVs, or a single play-through
  mixdown WAV. ENGB export button.
- Voice lines decode LIVE from the game's sound banks (~0.07 s per line,
  cached) — set Game Data Dir once and every 🔊▶ just works.

### Animations
- **Fixed #45**: MUA menu movesets (21_nickfury) played tilted-back — v8
  afakeanim binds are untrustworthy and are now ignored; unanimated-root
  rebase applied instead (gated so XML2/XML1 are untouched).
- **Fixed frozen/garbage low-rate animations** (XML1 power_3 and family):
  the Enbaya sample-rate gate rejected legit 8 Hz clips.
- **Identity-hold clips** (XML2 lunge_loop): recognized as engine-side "hold
  pose" clips; import as a clean rest hold — or, when `<stem>_start` exists
  in the moveset, hold that animation's final pose.
- 62-animation regression matrix across XML1/XML2/MUA is green.

### Actors / Armature
- **MUA v4/v6 skin detection fixed for good**: any texture bind on unit
  1/2/5 (normal/spec/gloss) now marks a file as MUA — validated against 120
  XML2 files (901 binds, all unit 0). Community skins and re-imported
  exports get correct colors, normal maps, and MUA export defaults.
- **Bone collections**: imported rigs organize bones into Deform / Helpers /
  Motion / Attach (fx) with colors, driven by the decoded bone flags.
- **Mirror Pose L↔R** (IGB Actors → Posing): exact pose mirroring for the
  games' "Bip01 L/R" naming that Blender's flip tooling can't parse.
- **Selection sync**: clicking a skin's object or its collection in the
  outliner points the IGB Actors tab at that skin automatically (great for
  multi-skin .blends).
- Armature format audit: bone flags round-trip; appearances/transition
  lists verified empty in all 105 sampled actor files (non-features).

### Exporter
- Per-export **size report** (textures/geometry/structure split + one hint).
  Output verified at native size parity (0101: pools byte-identical).
- 512 confirmed as the texture-size standard everywhere; labels updated.
- Profile auto-detect fixes: our v6 MUA mannequins no longer re-import as
  XML2 (R/B swap), XML1-vs-XML2 coin-toss resolved by path.

### Infrastructure
- **IGB Log panel** (IGB tab): recent import/export messages, copyable.
- **Texture disk cache**: decoded DXT/CLUT textures cached by content hash —
  re-imports are near-instant.
- **Regression suite**: `tools/run_regression.py` (registration, detection,
  map import, animation spot checks, export parity, motionpath round-trip).

## 0.5.1 and earlier
See release notes on GitHub.
