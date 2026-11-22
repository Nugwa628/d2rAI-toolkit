# HD maps: classic map art in HD, upscaling, light and the minimap

How D2R 3.3 (build 93847) draws a map in HD, and a method to show a mod's own classic maps (its `.ds1` and `.dt1`
tiles) in HD the way they look in classic, sharp enough for 4K. Found while converting a large overhaul mod with its own
tile sets and 17 maps of its own; every engine fact below was seen in a game run or read from D2R's files, as marked.
The tools are in `hdkit/` (see [hd-viewer.md](hd-viewer.md) for the viewer and its map page).

## How D2R HD draws a map

| Fact | Detail | How we know |
|-|-|-|
| The HD scene is a preset | `data/hd/env/preset/<ds1 path without .ds1>.json`: model entities, prefabs, lights, terrain, a biome | D2R's files |
| A map without a preset | loads with a default biome and floors painted from tile data, but **no walls and no buildings** | game run |
| An empty preset | one that holds only `biomeFilename` (D2R ships such files, e.g. small Kurast rooms) gives the right ground and light | D2R's files, game run |
| HD ground | comes from the biome (terrain layers with albedo, normal, ORM, grass, clutter) and `perTileBiomeOverrides`, **never** from `.dt1` art. At most 2 override biomes in view | D2R's files |
| Tiles at run time | a `.ds1` names no `.dt1` file at run time. A key (orientation, style, sequence) resolves through lvlprest `Dt1Mask` over the level type's `lvltypes` `File 1..32`, first file that has the key wins. The `.ds1` header's file list is editor metadata | file survey, matches every cell of every map tried |
| Level type `File n` | a value longer than 41 characters stops D2R before the main menu | game run |
| A mod copy of a D2R map | D2R builds the preset from the `.ds1` in its own CASC whenever CASC has that path, not from the mod's copy (D2RLoader 1.3.1). A mod that edits a D2R map must move it to a new path | game run |
| HD camera | orthographic, exactly 30 degrees above the ground, azimuth -135 degrees (the 2:1 diamond); 10 HD units per tile, 2 per sub-tile; cell (cx, cy) is at (10 cx, 0, 10 cy), no offset | disassembly of the camera code and game shots (0.5 px rms) |
| Scale | at 900 screen lines 16.97 px per world unit; at 3840x2160 one classic pixel covers 3.6 screen pixels | pixel count on game shots |
| Entity budget | about 4,423 entities and 4.65 MB per scene before trouble; model cache 8,250 | observed, see [engine-limits.md](engine-limits.md) |

The full camera, light and tone numbers are in [hd-viewer.md](hd-viewer.md#game-camera-and-light).

## The method: classic tiles entirely in HD

For a mod with its own tile sets, D2R's stock biomes and wall models never look like the mod's classic map: grass where
classic has a brick road, a stock wall where classic has the mod's own. The method that matched classic best:

1. **Art.** Every floor, wall, roof and prop of the map comes from the map's own `.dt1` tiles. D2R's base pieces and
   biome overrides are left out.
   - Floors are baked into chunk models (a map of 12,000 cells: about 60 chunks).
   - Walls are camera-facing cut-outs of the wall sprite, sized to the tile's real pixel count.
   - Rarity variants: pick one per cell by a hash weighted by rarity, as classic does, for floors as well as walls.
     Floors that always took the first variant showed as one grey slab.
2. **Lower walls** (orientations 16-19: cliff, kerb and ledge faces classic draws under a floor's edge, before the
   floors) are painted flat on the floor plane where the game camera shows them. Without this, cliff fringes and railings
   are missing.
3. **Wall pictures under their floor line** (the front faces of raised blocks, terraces, plinths) lie flat over the
   floors in front, because classic draws walls after every floor. Cutting them at the floor line loses those faces.
   Both 2 and 3 are flat, not 3D: a monster standing on them covers them like a floor.
4. **Ground.** A per-map biome with every terrain layer black and no grass or clutter, so nothing of D2R's ground shows
   where the map has no floor tile (on one cave map, 9,475 of 14,641 cells). The black ground reads 3 to 6 of 255,
   not 0.
5. **Light.** A light file per map, from the classic tables (below). A biome whose key light has power 0 (D2R's caves)
   leaves every sprite black: this is the first thing to check when a converted map shows nothing.

Measured over 12 converted maps (whole-map audit, mean): score 91.8 -> 92.9, edge F1 0.921 -> 0.954 against the
earlier sprite-wall build, with the lower walls and wall skirts added.

### Upscaling to 4x

At 4K the 1x atlases are blurry and stair-stepped. What works:

- Upscale the **stitched atlas**, tile by tile, not single tiles in isolation: no ringing at tile borders.
- Floor diamonds go into the upscaler inside a 3x3 lattice of themselves (no seam ring) and get an exact diamond alpha
  afterwards. Spread alpha 2 texels at seams.
- The upscaler: Real-ESRGAN x4plus through `realesrgan-ncnn-vulkan` (no PyTorch, about 50 MB, runs on AMD and NVIDIA
  through Vulkan). Halve the result for a 2x build.
- Two variants were built and compared at the 4K camera on 12 maps:

| Variant | What it does | Colour distance to classic (dE, 24 crops) | Sharpness against a plain Lanczos upscale (1.0) |
|-|-|-|-|
| 1x (shipped before) | the tiles as they are | 5.57 | 0.76 |
| 1x with lower walls and skirts | parity fixes only | 4.86 | 0.79 |
| 4x raw Real-ESRGAN | the upscaler's output as it is | about 5 (pilot crops 5.1) | 1.7 |
| 4x back-projected | the 4x picture corrected until each 4x4 block averages to the classic pixel again | 3.73 | 1.89 |

**The method kept is raw Real-ESRGAN 4x with the per-map light**, not the back-projected build. Back-projection wins on
the colour numbers, but it was judged by eye on side-by-side 4K pictures, and the raw output was preferred there; it is
also one step less in the build. Keep back-projection as the option when colour fidelity to classic matters more than
the look. What to watch with raw output: the fine detail is the upscaler's guess. Stone becomes clean cracked flagstones,
noisy dithering goes smooth, and faces, writing and small symbols on the art (signs, runes) can change. Check those.

Cost seen: 62 atlases (1,330 tiles) for 12 maps in about 40 minutes of GPU; the build output grows from 105 MB raw /
18.7 MB gzipped at 1x to 909 MB / 134 MB at 4x (BC3 albedo with mips is most of it; BC1 with 1-bit alpha would about halve
it, not tried).

### Per-map light from the classic tables

Classic light has three parts, all in the tables; none is placed by hand:

| Part | Classic source | HD form |
|-|-|-|
| Level ambient | levels.txt `Intensity`, `Red`, `Green`, `Blue` (classic multiplies the art by `Intensity/255 x C/255`) | key light: HD lights in linear values, so the factor is `f^2.2`; colour `lin / max(lin)`, power `250 x max(lin)`. Image-based light 0, neutral LUT, fog 0 |
| The player's light | the classic light radius | the vis file's `playerUnitLightPower` 3500, `playerUnitLightDistanceScaler` 4.5, `playerUnitLightAttenuation` 5.5 (2.5 / 3.0 gave a pool too small on every map) |
| Light monsters and lit objects | monstats2 `Light`, `light-r/g/b` (invisible still monsters placed as lamps); objects.txt light | D2R point lights: power 2500, radius `Light x 2.0`, attenuation 2.0, height 6 |

The rule alone does not predict HD brightness: per-map gains of 0.06 to 4 were needed (measured as HD/classic
brightness ratios at the player, at mid range and far away; each gain `r^-1.6`, two or three rounds per map). Of 17 maps,
5 matched, 8 matched in part, 4 could not be judged (see the next point). No fitted rule (for example gain from
Intensity) was found in 17 data points.

**Preset rooms inside random levels** are not reached by this method: HD still shows D2R's stock rooms around the one
preset room, so the map's own art and light are never seen there.

### Packing maps as an overlay

`python -m hdkit.maps overlay BUILD_DATA OUT MAP ...` packs the HD side of chosen maps from a build as a mod data overlay:
each preset, the models (with their LODs), textures, lights and particles it references, and the build's
`model_lod_desc.bin`, byte for byte, with `OUT/overlay.json` (sha256 per file and the references left to the base mod,
such as biomes). Use it to test maps in a test game without rebuilding the whole mod.

## The minimap (automap)

The minimap draws a tile only when `automap.txt` has a row for its (level type, tile code, style, sequence). A mod that
adds tile sets to a stock level type finds D2R's rows, which were written for the stock tiles: most of the new walls get
no row, and the minimap shows almost nothing, in classic as in HD.

The generic tool (`python -m hdkit.maps automap-rows|automap-art|automap-void|automap-render ...`, modules `hdkit/maps/automap_rows.py`, `automap_art.py`,
`automap_void.py`, `automap_render.py`) adds, per level type, one row for every wall key a map of that type draws and no
row covers:

| Step | Rule |
|-|-|
| Maps | every `.ds1` the mod ships, plus every map of a level whose type lists a tile set D2R's row of that type does not. Maze rooms (level 0 in lvlprest) get their type from their name |
| Cells | per level type and wall code, the cell its own rows use most (ties: the one named first). A missing code takes a related one (wtll <-> wtlr, co -> wbr, doors -> walls). A type missing one of the 7 main codes takes `1 Catacombs`' whole set |
| Skipped | keys a stock tile set of the type holds or that one of D2R's own maps of the type draws (stock levels keep their minimap exactly; the tool proves 0 changes over every D2R map of the changed types); tree and invisible-wall tile sets; floors, roofs, shadows and lower walls (stock draws none of them either) |
| Atlas limit | D2R's automap atlas ends at cel 1498: a cel past it crashes the HD game |
| Name trap | `Act 5 - Ice Caves` is called `5 Ice` in automap.txt; without the mapping, those maps find no rows |

**Brighter legacy cels.** Classic minimap lines are thinner than HD's, so a level type with new rows can read dim in
legacy. The measure is ink: brightness times opacity per screen area, legacy cel against its HD cel. Types under 0.8 of
the HD ink get bright copies in free cels: every pixel moved to the act palette colour nearest to gain x its colour
(gain `0.8 / ratio`, at most 2.4), palette indices 0 and 225-254 (act-dependent) never picked. The HD side of a copy is
the template cel's own HD picture. At most 8 copies per type; types of one act share them. Stock cels and rows are
untouched.

**Walls in the void.** A wall tile standing far from any walkable floor (a kerb running 40 cells into the black) shows as
a long line once its key has a row, because the automap draws every wall of a revealed room. A row belongs to a tile key,
not to a place, so those places get a key of their own: a copy of the same tile (same pixels, same walk flags) under a
free sequence number of the same style, appended to the tile set. The copy has no row. The world looks the same in both
modes; the minimap leaves them out. A wall counts as unreachable when no walkable floor lies within REACH cells (default
small; a map whose thick front walls stand 4-5 cells from their floor needs 6) and it is not next to a roof. Review the
picks against a world render before shipping: the first pass on one map removed whole room sides.

## Open points

- The whole method was checked in game on one map at 4K; the others were checked in the viewer only.
- The viewer's light is within 1.5-2x of the game's on a calibrated D2R map (it draws walls the game fades and lacks the
  player's pool), so judge art on unlit (albedo) renders and mood on lit ones.
- Animated floors, special tiles (orientations 10/11) and a rule for floor tiles used as walls are not handled.

## See also

[hd-viewer.md](hd-viewer.md) (map viewer and rounds page), [hd-graphics.md](hd-graphics.md), [map-generation.md](map-generation.md)
(level and room generation), [tables.md](tables.md) (lvltypes, lvlprest, automap.txt).
