# Alchemy Batch Converter 0.1.0 — Windows x64

A separate desktop application built around igb-blender's import/export code.
You do not need to install this as a Blender add-on.

## Quick start

1. Extract the ZIP into a writable folder.
2. Install Blender 4.5 LTS and the .NET Framework 4.8.1 runtime if needed.
3. Open `AlchemyBatchConverter.exe`. Select `blender.exe` if it is installed
   somewhere other than the standard Blender 4.5 location.
4. Choose the input folder and a separate output folder, then **Scan folder**.
5. Review detected profiles, choose a target preset, select files, and convert.

Paths relative to the input folder are preserved: `input/actors/15701.igb`
becomes `output/actors/15701.igb`. Subfolder scanning is optional. Input/output
folders must not be nested. Original files and existing outputs are never
overwritten. Choose a new output folder to repeat a conversion.

## What is supported

- 15 source game/platform profiles from igb-blender, with auto-detection and a
  manual override. Auto-detection is heuristic; review the selected profile.
- Skinned character exports: MUA2 Wii RGB5A3, MUA PC DXT/CLUT, XML2 PC CLUT,
  PS2 CLUT, and GameCube CMPR.
- Simple static scene exports through the MUA PC/XML2 PC scene exporter.
- Texture-size limits, supported extra-map/mipmap settings, saved settings,
  progress, cancellation, and JSON reports.
- Animations, UI/textures, effects, unknown assets and unsupported input formats
  can be copied unchanged or skipped. Copy does not convert their format.

This first release is not a universal scene-graph converter. It scans loose IGB
files, not WAD/PKGB archives. Complex scenes containing actors/animations/effects
are gated; arbitrary game-specific metadata is not guaranteed to roundtrip.
Known Wii format-21 import and Xbox 360 native geometry limitations are gated.
The 15 import profiles are not a promise of 15 export targets. Target presets
select implemented exporter combinations, not arbitrary Alchemy versions.

## Runtime files and cancellation

Blender is run in the background, one process per conversion. A ten-minute
watchdog bounds each worker. Cancel terminates that worker and preserves already
completed files. No desktop input is sent. Work is staged below the output folder
in `.alchemy-staging` and published only after validation. Cancellation may leave
unpublished staging files for inspection.

The EXE extracts its embedded backend and writes logs, configuration, temporary
files and `last-report.json` under `.local/alchemy-batch` beside itself. The EXE
folder must be writable. Blender itself is not bundled. Do not run two copies
against the same output folder. Use **Export report** to save the batch receipt.

## Validation and limits

The four Wii test outputs match previously game-tested character models byte for
byte. Character exports for the other four targets passed parsing/geometry checks;
they have not been tested in their destination games by this app release. A
synthetic static scene, unchanged-copy handling, overwrite protection and worker
cancellation were also tested. No game assets or proprietary fixtures are included.

## Build from source

From a full checkout of this release tag:

`python tools/alchemy_batch/build.py`

Build prerequisites: Python 3.11+, Git, a stable .NET SDK containing Roslyn, and
the .NET Framework 4.8.1 developer targeting pack. The build uses installed tools
without downloads/package restore. It archives the pinned backend revision
`4672c8d79a4539333632d1ada70937672e1c21ae`; shallow checkouts must include that commit.
The output is `.local/AlchemyBatchConverter/AlchemyBatchConverter.exe`.

`python tools/alchemy_batch/test_worker.py` runs focused regression tests.
`python tools/alchemy_batch/verify.py <PC-actors-folder>` runs private integration
checks if you supply your own authorized MUA PC Luke Cage models 15701–15704.
`--verify-cancel <input> <output>` tests contained worker cancellation.
`--render <png>` renders actual WPF content offscreen without opening a window.

Backend project: https://github.com/KaikoClanworth1/igb-blender
App source/release fork: https://github.com/GTTeancum/igb-blender
Wii exporter contribution: https://github.com/KaikoClanworth1/igb-blender/pull/5
The embedded backend includes its tracked source and original notices.
