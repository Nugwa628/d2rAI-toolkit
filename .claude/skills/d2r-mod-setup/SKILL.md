---
name: d2r-mod-setup
description: Set up d2r-modkit and start, build, install or run a Diablo II Resurrected mod project. Use when someone wants to begin a new D2R mod or overhaul, when tools fail with missing CascLib / game files, or when they ask how to build, install, launch or ship their mod.
---

# Set up the toolkit and a mod project

## One-time setup (Windows)
1. Prerequisites: D2R installed (default `C:\Program Files (x86)\Diablo II Resurrected`; else set `MODKIT_GAME`),
   Python 3.12+, git, CMake and Visual Studio C++ build tools (for CascLib and plugins). D2RLoader
   (https://github.com/D2RLoader) is needed only for plugins or extended saves.
2. `git submodule update --init` then `powershell -ExecutionPolicy Bypass -File tools\build_casclib.ps1`
   (reads the game's CASC storage).
3. `python -m modkit fetch` copies the tables, strings, UI layouts and data guide into `d2r-data/<build>/`
   (git-ignored: Blizzard's files are never committed). Other files are fetched on first use.

## A new mod
`python -m modkit new <dir> --name <ModName>` creates:
- `mod.json`: name, savepath (its own save folder), `extendedSaves` (D2RLoader extended save format: needed for
  more than 30 skills per class or wide stat/item IDs), `plugins` (built plugin DLL paths).
- `patches/`: JSON change lists (knowledge/modkit-ops.md), applied in name order on pristine game files.
- `assets/`: PNGs etc. that patches use. `files/data/`: files shipped as they are.
Keep the mod project in its own git repo: patches are text, review them like code.

## The loop
1. Write or change a patch (use the d2r-skill / d2r-items / d2r-monsters / d2r-ui / d2r-cube skills).
2. `python -m modkit build <dir>`: rebuilds `out/data` from scratch and validates (`out/validation.md`). Fix all
   errors; read warnings.
3. Close the game, then `python -m modkit install <dir>`: copies into `<game>/mods/<Name>/` with modinfo.json
   and D2RLoader metadata.
4. Run: with plugins or extended saves, start `D2RLoader.exe` and pick the mod; otherwise `D2R.exe -mod <Name> -txt`
   also works. Fully restart the game after every install. The first start after installing or updating D2RLoader
   or D2R builds D2RLoader's table cache and loads D2R's own sounds: start the game a second time. Set the default
   mod in D2RLoader's options (gear, Default Mod); see knowledge/d2rloader.md.
5. Keep a TESTING.md in the project: every installed change gets a row (what, how, expected). The user tests in
   batches; move rows to "Confirmed" when they report back. When something breaks, use the d2r-debug skill.

## Test characters
`python -m modkit save-info <save.d2s> [--extended] [--project <dir>]` shows stats and class skill points;
`save-set <save> level=30 strength=100 --skill 12=1` edits them (backs up the save folder first, fixes the
checksum and D2RLoader's .d2rl hashes). Never hand-write items into saves; use D2R's debug Item Spawner.

## Shipping
Players need the `mods/<Name>` folder (and D2RLoader if you use plugins/extended saves). Ship only your mod's
files, never the game's; `out/data` already holds only what the mod changes. Requirement checks name the game
version (`3.3`), never a build number: Steam and Battle.net ship the same program under different builds. Tell
players to start twice the first time, to set Default Mod, and to remove other gameplay plugins.

Porting an existing classic mod instead of starting fresh: the `d2r-port` skill.
