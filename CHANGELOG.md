# Changelog

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
