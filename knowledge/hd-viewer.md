# The HD viewer: render D2R's HD art offline, check it, then sign it off in game

`hdkit.view` renders D2R's HD units, models, classic frames on cards, and whole maps (classic and HD on the same pixels)
the way the game's camera and light show them, without starting the game. It reads the same files the game uses (the
mod's built data first, then the game's CASC storage through a capped cache). Work viewer-first: make and judge a model or
map in the viewer, and run the game only for the final sign-off. Target: D2R 3.3 build 93847.

Code: `hdkit/view/` (CLI `python -m hdkit.view`, browser page in `hdkit/view/web/`, the CASC cache `casc.py`, the MCP
server `mcp_server.py`), the map viewer `hdkit/view/mapview/`, the sign-off `hdkit/signoff/`, the GPU gate
`hdkit/gpugate.py`, the art store `hdkit/store.py`. Config: `hdkit/site.py`, read from `hdkit.toml`
(copy `hdkit/hdkit.example.toml`). MCP setup: [mcp/README.md](../mcp/README.md).

## Setup

- Python 3.12 with numpy, Pillow and scipy; `wgpu` (BSD-2-Clause, bundles wgpu-native, MIT/Apache-2.0) for GPU renders;
  `mcp` (MIT) for the MCP server. Install into a venv of your own, not system-wide.
- A GPU with Vulkan, D3D12 or Metal. Renders were checked on one AMD card under Windows (D3D12) and Linux (Vulkan,
  Mesa RADV): all 6,686 per-cell calibration numbers were identical on both, PNGs differed only at a few edge pixels.
- `[game] dir` in `hdkit.toml`: a game folder whose CASC storage is read on demand (best a test copy, see
  [../testing/README.md](../testing/README.md)). CascLib must be built (`tools/build_casclib.ps1`, or a `libcasc.so` on
  Linux via `MODKIT_CASCLIB`).
- `[locks] globs`: the lock files that mean a game run is in progress. GPU jobs and CASC reads wait while one exists.

## Commands

```
python -m hdkit.view render <asset> [--facings 8] [--mode dt] [--scale 4] [--ss 4] [--pass lit|albedo|normals|...]
python -m hdkit.view sheet <asset> --modes nu,a1,dt        contact sheet, modes x facings
python -m hdkit.view turntable <asset> --frames 24 --anim gif,apng
python -m hdkit.view watch render <asset> ...              render again whenever an input file changes
python -m hdkit.view compare <render.png> <shot.png> --shot-feet x,y --twin <shot without the unit>
python -m hdkit.view ls units|models|clips --root ...      what the roots hold
python -m hdkit.view camera --height 900                   the game camera as numbers
python -m hdkit.view gate                                  may a GPU job run now
python -m hdkit.view serve-web <bundle>                    the browser page on 127.0.0.1
python -m hdkit.view map ls|info|classic|hd|sheet|cell|edit|serve ...   the map viewer
```

`<asset>` is a logical game path (a unit JSON or a `.model`), or `card:<frames folder>` for classic frames on a card.
`--scale 1` is the size of a 1600x900 game capture. Every command writes PNGs plus `metrics.json`: the provenance of every
file read (which root answered), the pose, the rig, image measures and texels per screen pixel.

- **Passes**: lit, unlit, albedo, normals, depth, wire, glow, ORM channels, height. Use albedo to judge art, lit to judge
  mood.
- **Big images**: the camera is orthographic, so any size is cut into tiles of at most the GPU's texture limit and
  stitched without seams; `--ss` supersamples per axis. Beyond about one texel per screen pixel, more pixels add no detail.
- **Mods first**: `--root` folders are searched in order, first wins; the default is the mod's built data, then CASC.

### Map viewer

`python -m hdkit.view map ...` draws a map (path below `data/global/tiles` without `.ds1`, e.g. `act1/cathedrl/cathy3`)
whole or as a cell range, classic and HD on the same pixels:

| Command | What |
|-|-|
| `classic <map>` | the `.ds1` drawn with its `.dt1` tiles and the act palette. Layers: floor, shadow, wall, lower, roof, special, objects, labels, grid, coords, walk |
| `hd <map>` | the preset drawn by the renderer at the game camera. Layers: terrain, decals, walls, props, prefabs, lights, fx, units, objects, grid. `--rig vis` lights it with the biome's own vis file and the preset's lights |
| `sheet <map>` | classic, HD and a 50 % blend side by side |
| `cell <map> x y` | every floor / wall / shadow dword on the cell, the tile it resolves to (file, variants, sub-tile flags), its objects and preset entities |
| `edit init/tile/obj-*/ent-*/show` | edits on a working copy under the work folder; never game files or extracts |
| `serve [<map>]` | a local page (127.0.0.1 only): pan, zoom, classic / HD / overlay, click a cell. `/rounds` shows prepared pictures side by side, as a flicker or a wipe |

Classic draw order (after ds1edit and OpenDiablo2): cell (x, y)'s floor diamond top corner at ((x - y) 80, (x + y) 40);
pass 1 lower walls, floors, shadows; pass 2 walls; pass 3 roofs. The `.ds1` writer round-trips all 2,311 `.ds1` files of
D2R byte for byte.

Not drawn: tile-mask path edges, grass and clutter, particles other than fire (fire is an emissive card with its
light), the wall fade in front of the player, shadows, spot-light cones, monsters in HD. Classic: animated tiles show
frame 0, light is the palette at full brightness, units are markers.

## Game camera and light

| Item | Value | How found |
|-|-|-|
| Projection | orthographic (perspective only through a debug toggle or launch flag) | disassembly of the 93847 camera code |
| Orientation | elevation exactly 30 degrees, azimuth -135 degrees, for map pieces and units alike | code constant; silhouette fits peak at 29-30 |
| View size | vertical extent 70.71 / (4/3) / zoom world units (53.03 at zoom 1, zoom 1-2.25), independent of resolution | code and pixel counts |
| Screen scale | screen height / 53.03 px per world unit: 16.97 at 900 lines, 20.4 at 1080p. 1 sub-tile = 2 units, 1 tile = 10 | pixel count on shots |
| Near / far | 10 / 10000 | code |
| Exposure | 0.18 / (2^EV x 10/65): 0.0731 at the usual EV 4 | shader disassembly |
| Tone | HDR x 0.008, then PQ (ST.2084), then a 65³ grading LUT (two blended) | shader disassembly |
| Light per time of day | each biome names a vis file per time slot (`data/hd/env/vis/*.json`, 369 files): key light, player / monster / item lights, LUT, image-based light, fog | D2R's files |
| Unit light | `characterBrightnessFactor` 0.4 in 349 of 369 vis files; the unit key light has power 30 at every time of day | D2R's files |
| Units and point lights | units get no point light | observed |
| Shaders | DXBC SM5 in CASC `data/hd/shaders/*.bin` (162 files); unit material `VariantMaterial.ogsfx`, 18 uniforms | file survey |

## Calibration: what the viewer can and cannot judge

The viewer's unit rig was tuned against game captures of one self-lit monster at night (frames held out from the tuning,
each with a twin shot without the unit for an exact mask):

| Measure | Viewer against game | Target | Verdict |
|-|-|-|-|
| Corpse silhouette IoU | 0.73-0.75 | 0.70 | good enough |
| Live brightness, p50 ratio | 0.98 on one set, 1.43 on another (the second had the unit light off) | 0.8-1.25 | depends on the light set-up |
| Live silhouette IoU (fair / best ±10 px) | 0.33 / 0.62 | 0.65 | not good enough |
| Colour of a glowing part (RGB distance) | 72-99 | 40 | not good enough: the game's glow layer is not modelled |

So use the viewer for **shape, pose, corpses and relative changes** (before against after on the same rig). Keep
**absolute colour of glowing parts and absolute live brightness** for the in-game sign-off. On maps, the viewer is within
1.5-2x of the game's brightness on a calibrated D2R map. The frame noise of the game itself is large: one run's middle value
spread from 0.65 to 1.20 for the same model, so pool at least 3 runs.

What will never match: PopcornFX particles (read, not simulated), post-processing (bloom, sharpening), fog volumes,
dynamic shadows, wall fade, screen-space effects, variant colour transforms.

## The in-game sign-off

`python -m hdkit.signoff all <token>` (or `prep`, `run`, `views`) runs one foreground game run on a test copy of the
game, spawns the unit, casts its modes, kills it, and grabs frames per facing key; then it renders the same (key, mode,
time) in the viewer and writes one sheet: game | viewer | outlines, with the numbers. The game part uses a test framework
with slot locks and a plugin test harness (spawn, place, cast, kill, and a `facing` verb that reports a unit's path
direction and facing key); the testing tools in [../testing/](../testing/README.md) and
[../docs/testing.md](../docs/testing.md) are that framework's shape. On Linux the game runs on a private display, never
on the user's desktop. Settings come from `[signoff]` in `hdkit.toml`.

## The GPU gate

A machine that runs game tests beside offline renders needs one rule: **no render while a test run holds its lock, and none
while the GPU or CPU is busy.** `python -m hdkit.gpugate` (or `hdkit.view gate`) prints one reading; exit 0 = free.

| Platform | GPU reading | CPU reading |
|-|-|-|
| Windows | the "GPU Engine" performance counters (vendor-neutral; per-process instances of one engine summed, busiest engine wins) | processor time |
| Linux | amdgpu's `/sys/class/drm/card*/device/gpu_busy_percent`, middle value of 5 samples of the busiest card | one-minute load average as a share of the logical CPUs |
| No GPU counter | locks and CPU only | |

`nvidia-smi` exists only on NVIDIA machines, so a gate built on it never says "free" on others. Every GPU job of hdkit
(renders, upscales) waits at the gate and never overrides it.

## The art store

Large outputs (upscaled atlases, model stages, big renders) never go into git. `hdkit.store` keeps them in a
content-addressed folder (`[paths] art_store`): `objects/sha256/<2 hex>/<sha256>`, written through `tmp/`, re-hashed, then
moved in. A small manifest (logical name, sha256, size) stays next to the work and can be committed; `fetch` copies the
files back. Keep the store outside the repo.

## Outside tools (get them yourself, never commit them)

| Tool | Use | Licence | Where |
|-|-|-|-|
| `realesrgan-ncnn-vulkan` with the `realesrgan-x4plus` model | 4x upscale of map atlases and sprite frames | MIT (ncnn build), Real-ESRGAN and its models BSD-3-Clause | the release page of github.com/xinntao/Real-ESRGAN (portable builds for Windows, Linux, macOS) |
| Blender (pin one version; the 3D monster guide uses 4.5 LTS) | the design graph's skin modifier and subdivision, Cycles bakes, LODs, pinned renders, all as a separate headless process | GPL: run it as an outside program, never import it into toolkit code | blender.org |
| TripoSG (optional, researched only) | image-to-3D shape candidates; looked worse than a hand-built model, not needed by the 3D route | MIT code and weights | github.com/VAST-AI-Research/TripoSG |
| imageio-ffmpeg | MP4 turntables (optional; GIF and APNG need nothing) | BSD-2 wrapper; the ffmpeg binary is GPL/LGPL, a local tool only | pip |

Check the upstream licence again when you download: model licences change. Weights with region or non-commercial limits
must not be used for anything that ships.

## See also

[hd-maps.md](hd-maps.md), [hd-monsters.md](hd-monsters.md), [hd-graphics.md](hd-graphics.md),
`hdkit/THIRD_PARTY.md` (ported code and its sources).
