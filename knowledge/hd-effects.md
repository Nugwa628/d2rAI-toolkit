# HD particle effects (`.particles`): what D2R does, and the hdfx tools

D2R's HD effects (missiles, cast overlays, auras, states, monster and object effects, decals, lights) are PopcornFX
effects baked into `data/hd/vfx/particles/**/*.particles`. `hdkit.fx` reads, simulates, draws, edits and authors them,
offline, without the game. This page is the overview: the engine facts a mod needs and the commands. The reference of
every interface is [docs/hdfx.md](../docs/hdfx.md), with [hdfx-shaders.md](../docs/hdfx-shaders.md) (the game's
particle shaders), [hdfx-vm-re.md](../docs/hdfx-vm-re.md) (the script runtime read from D2R.exe),
[hdfx-lang.md](../docs/hdfx-lang.md) (a readable script language), [hdfx-mcp.md](../docs/hdfx-mcp.md) (the `fx_*` API and
MCP tools), [hdfx-web.md](../docs/hdfx-web.md) (the web editor) and [hdfx-cull.md](../docs/hdfx-cull.md) (culling). The
viewer it draws in is [hd-viewer.md](hd-viewer.md).

## The file

- A `.particles` file is a PopcornFX object file (magic `0xCA000B11`): a class table, objects (26 classes, each a list of
  fields), a string table. `hdkit.fx.pfile` reads and writes it byte for byte; `hdkit.fx.graph` turns it into an effect
  graph (layers, renderers, attributes, samplers, events) and applies edits.
- One file holds **four platform graphs** (PC, Switch, PS4, PS5), each with its own copies of the renderers. Only the PC
  graph is drawn on PC: edit that one (or all four).
- The scripts (spawner, particle spawn, particle evolve) are **compiled bytecode** for PopcornFX's CPU virtual machine
  (`CPU/CPU_VM`, "CBVM"), not source. `hdkit.fx.cbvm` disassembles and reassembles them byte for byte (every one of the
  41,337 scripts of the 93847 files round-trips); `hdkit.fx.lang` decompiles them to a readable language and compiles it
  back; `hdkit.fx.vm` runs them (vectorised over particles) with the engine calls D2R registers.
- Edit the **current game build's** file. A cache from another build has different bytes for the same effect (Mephisto's
  smoke: 104,698 bytes in an older cache, 83,268 in 3.4); an edit of it loads but is not what the game shows.

## What the game does (checked against captures)

- **Not drawn:** renderers of kind 3 (triangle), 4 (decal kind without the light/decal material) and 6 (sound), and
  `Distortion_Billboard` materials, get no material in D2R's factory and draw nothing. `Distortion`, `Sound` and
  `LegacyLit*` switches are never read; `MeshSampling` only logs "no longer supported". (`hdkit/fx/gamekinds.py`, read in
  the 3.4 build 95032 image.)
- **Missile speed:** missiles.txt `Vel` 1 = 3/64 sub-tile per game frame at 25 frames per second (2.34375 world units per
  second), measured from server positions in game.
- **Cast overlays** start with the cast mode, not at the missile's release; the missiles leave at the weapon class's
  action frame (staff: 0.123 s later). Give the viewer the caster's real weapon class.
- **Colour variants:** a monster's effects get `ColorVariantIndex` = 2 + monstats `TransLvl` (the entry of its
  `<unit>_variant.json`), and a colour state (states.txt `colorshift` c, 1..128) gives 37 + c; the scripts switch their
  colours on it. Without it a tinted unit's effects draw in the cold colours.
- **Collision:** colliding particles rest about 1 unit above the floor (game ray semantics: a unit-length ray, the
  length only gates), not on it; fast debris tunnels at 30 fps.
- **Soft particles** fade against the terrain's depth: render with a depth-only ground or rings draw up to 3x too bright.
- **Screen-space AO:** the game multiplies the opaque scene by Intel's ASSAO between the opaque and transparent passes;
  the viewer ports it (`hdkit/view/assao.py`).
- **Lighting:** units are lit by the game's `ps_models_p` terms (one scene light from the level's vis data, IBL from
  the act's cube as spherical harmonics, local lights with the game's falloff, directional shadow slices, a player
  light with a cube shadow inside caves). Details and the measured numbers: docs/hdfx.md section 3.

## Commands

```
python -m hdkit.fx info|dump|roundtrip <file.particles>        objects, fields, byte-exact round trip
python -m hdkit.fx dis <file.particles>                         CBVM listing of every script
python -m hdkit.fx sim <file.particles> --t 1.5                 run the scripts, print the particle counts and fields
python -m hdkit.fx coverage <root> --out <dir>                  which effects the viewer draws, partly draws, or not
python -m hdkit.fx.lang decompile|compile|check ...             the readable script language (docs/hdfx-lang.md)
python -m hdkit.view fx <effect> [--unit <unit json>] --t 1     draw an effect alone or on a unit, at the game camera
python -m hdkit.view fx <effect> --cast "<skill>" --tables <excel> --toward dx,dy   a whole cast: overlay, missiles, hits
python -m hdkit.view fx [--port 8766] [--mock]                  the web editor on 127.0.0.1 (docs/hdfx-web.md)
```

Agents use the MCP tools `fx_list`, `fx_inspect`, `fx_edit`, `fx_validate`, `fx_create`, `fx_script_read`,
`fx_script_write`, `fx_render`, `fx_export`, `fx_session`, `fx_coverage`, `fx_fired_by` and `fx_color_variants` of the
hdkit MCP server (mcp/README.md). Every edit writes a working copy in a session folder (undo, redo, history), never a
game file; `fx_export` writes it to `<mod>/data/<logical path>`, validates it and refuses a game root as the target.
The tables a cast needs (missiles.txt, skills.txt, overlay.txt) come from a folder you name (`--tables` or
`HDFX_TABLES`); nothing mod-specific is in hdkit.

GPU renders pass the GPU gate (`python -m hdkit.gpugate`) and run under a memory cap (`python -m hdkit.memwatch --gb 9
-- ...`). Game files are read through the viewer's roots (`--roots name=folder`, `HDKIT_VIEW_ROOTS`, or `casc` with
`[game] dir` in `hdkit.toml`) and never written.

## Checking an edit in game

The calibration bench (`hdkit.fx.calib`, docs/hdfx.md section 7) compares viewer renders with game screenshots and
RenderDoc captures, and its load test lays an edited file into a test copy's mod data for one run and puts in-game and
viewer frames side by side. The game half (a test copy of the game and a harness plugin that spawns, casts and takes
screenshots) is your project's: see `testing/` and `hdkit/signoff/HARNESS.md`.

## Open

Known gaps against the game, none of which changes an interface:
- an operated shrine's flash is dimmer and ends about 0.3 s early, and its whole-region added light undershoots;
- Frost Nova's peak ground light is 1.1-1.2x the game's;
- the cave rules (`inside`) are a name rule proven only on act 1's caves;
- AO runs on the CPU only (0.7 s per 1600x900 frame) and is not applied to big tiled renders;
- Molten Boulder, Blaze and Immolation Arrow layouts are not checked in game;
- object effects on equipment do not yet follow the target bone's orientation or the item's own animation.

Third-party code and the licences: `hdkit/THIRD_PARTY.md`.
