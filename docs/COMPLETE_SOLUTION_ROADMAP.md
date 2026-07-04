# "True Complete Solution" Roadmap

Gap assessment 2026-07-04, after the map-import overhaul, Import Full Map, Simulate,
Game Shading, full Conversation Editor, and the animation-correctness fixes.
Organized by what each kind of user still hits a wall on.

---

## 1. Character modders — the missing last mile

The pipeline covers import → edit → rig-convert → export beautifully, but ends at
"you have an .igb file". The community's real workflow continues:

| Gap | What it is | Value / Effort |
|---|---|---|
| **New Character Wizard** | One flow that takes a finished skin and produces EVERYTHING a playable character needs: herostat/npcstat entry, packages, talents stub, hud head, icons, mannequin, copied into the game with backups. The pieces mostly exist (npcstat editor, team menu, hud_extract, texture IGB builder, mannequin export) — they're just not chained. | HIGH / Medium |
| **HUD head generator** | Render the current skin to a `hud_head_XXXX.igb` automatically (camera preset + texture IGB builder). Classic community pain point. | HIGH / Low |
| **"Install to game" for actors** | Skin/mannequin export straight into the game dirs with automatic backup of originals + a restore button (Map Maker already deploys; actors don't). | HIGH / Low |
| **v8 native export crash (#49/#53)** | MUA v8 exports crash on gameplay load (menu OK). The v4 path is the workaround; finishing the v8 actor/combiner graph makes "export for MUA" just work. | HIGH / Hard |
| **Batch operations** | Batch skin re-export / texture downscale / format convert over a folder (the XML1→XML2 port needs this constantly). | MED / Low |
| **Env-map export (Stage 2)** | Import + lossless storage shipped; export blocked on igb_writer ObjectRefArray serialization (plan documented in memory). Metallic sheen on MUA skins. | MED / Medium |
| **Morph targets / shape keys** | XML2 facial morphs (igMorphInstance) currently dropped. Import-first. | MED / Hard |
| **Foot-IK planting** | Kill the ~1.5u mocap foot-skate (user opted in earlier; not built). | LOW-MED / Hard |

## 2. Map makers

| Gap | What it is | Value / Effort |
|---|---|---|
| **Finish Map Editor Redesign (MM2)** | Already in progress — UI overhaul, gameplay templates, objectives, property schemas. Finishing it is the single biggest map-side item. | HIGH / In progress |
| **Fog import/export (igFogAttr)** | Marked High in SUPPORTED.md and still missing; maps preview wrong without it and exported maps can't set fog. | HIGH / Low |
| **Round-trip fidelity for imported maps** | Import Full Map → edit → Build All re-exports geometry but drops un-modeled render-state attrs (open task #35: render-state capture). Needed before "edit existing maps" is truly lossless. | HIGH / Medium |
| **Motion path editing + export** | We import motionpaths as animated empties/curves; no way to edit-and-write `motionpaths/*.igb` back (format is fully decoded — igTransformSequence1_5, ns times — so the writer is straightforward). | MED / Low |
| **Trigger volumes (igVolume), igLod, igBillboard** | Volumes matter for gameplay authoring; LOD/billboard for visual completeness of big maps. | MED / Medium |
| **Zone-script authoring depth** | Script editor exists; a library of BehavEd command templates (spawn waves, doors, objective beats — like stark's rand_npcs) would complete "make a real level". | MED / Medium |

## 3. Cutscene / machinima

| Gap | What it is | Value / Effort |
|---|---|---|
| **Briefing Composer (export)** | Briefing Loader imports cutscenes; the reverse — author staging + camera + performances in Blender and WRITE a playable briefing (xmlb staging + zone perf igb via IGBAnimationBuilder + script) — would close the loop. Most pieces exist. | HIGH / Medium |
| **XML1 voice extraction** | XML1 console briefings play silent (voice lives in the packed console sound archive, not the decompiles). RE the archive once, everything gets voice. | MED / Medium |
| **"Preview with entry pose"** | Identity-hold clips (lunge_loop) preview as rest; optional chaining after the entry anim's last frame. | LOW / Low |

## 4. Platform coverage

| Gap | What it is |
|---|---|
| **MUA2 PC IGZ textures** | README admits "Broken". Geometry 643/643 imports; finishing textures makes all four PC games first-class. |
| **Console export matrix** | PS2 + GameCube actor export done; Xbox / PSP / Wii / MUA2-PS2 scoped but unbuilt. |
| **v4 retail validation** | v4 paths were tuned against a modded install; validate against clean donors (0103.igb / Cable). |

## 5. Polish that makes it FEEL complete (cross-cutting)

1. **Docs refresh** — README says v0.4 and predates: Import Full Map, NPC actors,
   Simulate, Game Shading, motion paths, conversation Preview/audio export, size
   report, XML1 fixes. Add a 1-page quickstart per persona (skins / maps / cutscenes).
2. **Progress + cancel on long operators** — Import Full Map with NPCs ≈ 60 s of
   frozen UI. Blender `wm.progress_*` + chunked runs.
3. **Consistent error surfacing** — one "IGB Log" with copyable errors instead of
   console prints; failed imports should say WHY in the UI.
4. **Regression suite in tools/** — the harnesses built this session (registration
   test, 7-case profile detection, 62-anim audit, map import counts, export
   byte-parity vs native) collected into one `tools/run_regression.py` gate for
   every release.
5. **Release hygiene** — v0.5.1 shipped, but DXT1-opaque + env-import are deployed
   locally and UNCOMMITTED; README version stale; add CHANGELOG.md and a version
   bump step to build_igb_release.py.
6. **Texture decode disk cache** — re-importing big maps re-decodes every DXT/CLUT;
   caching by content hash would make second imports near-instant.
7. **First-run experience** — auto-detect game installs (registry/common paths) and
   offer to fill Game Data Dir; the audio/NPC/conversation features silently no-op
   without it today.
