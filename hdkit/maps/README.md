# hdkit.maps: map tools for any mod

Tools that work on a mod's classic maps (`.ds1` + `.dt1`) and their HD builds. Places come from `hdkit.toml`
(`hdkit/site.py`): `[mod] classic` (the mod's classic files, holding `data/global/tiles` and `data/global/excel`) and
`[mod] d2r_extract` (D2R's own files; default modkit's `d2r-data/<build>`). D2R's act palettes must be in that extract
(`data/global/palette/actN/pal.dat`) or readable through the configured game folder; the tools stop rather than guess.

```
python -m hdkit.maps overlay BUILD_DATA OUT MAP [MAP ...]
python -m hdkit.maps automap-rows OUT/data/global/excel/automap.txt --plan OUT/automap_plan.json
python -m hdkit.maps automap-art build OUT/data --plan OUT/automap_plan.json
python -m hdkit.maps automap-void find MAP
python -m hdkit.maps automap-void build OUT/data --plan OUT/automap_plan.json [--reach-map MAP=N]
python -m hdkit.maps automap-render MAP PREFIX --automap OUT/data/global/excel/automap.txt --art OUT/data
```

| Tool | What it does |
|-|-|
| `overlay` | Copies each map's HD preset and every file it references (models with their LODs, textures, lights, particles) plus `model_lod_desc.bin` out of a build folder, byte for byte, with an `overlay.json` (sha256 per file, references left to the base mod). Lay it over an installed mod for a test run, or pack it. |
| `automap-rows` | The minimap draws a tile only when `automap.txt` has a row for its (level type, code, style, sequence). Adds one row per wall key the mod's maps draw and no row covers, using each level type's own most-used cel. Stock keys, trees and invisible walls are skipped. Ends with a stock check: every D2R map of the changed types must draw its minimap exactly as before (exit code 1 otherwise). |
| `automap-art` | Classic automap lines are thinner than HD's, so legacy reads dimmer. For each dim type it writes brighter copies of the template cels (act palette, gain from the ink ratio, capped at 2.4) into free cels of `maximap.dc6` / `maximaps.dc6`, and the template's HD picture into the same cels of the HD sheets. Stock cels are untouched. |
| `automap-void` | **Crashed legacy graphics in the port it comes from; leave it out of release builds (see the warning below).** Walls standing far from any walkable floor would show as lines into the void once they have a row. Moves them to a copy of their tile under a free sequence with no row: the world looks the same, the minimap leaves them out. Check the picks with `find` and a world render first; maps with thick walls need a wider `--reach-map`. |
| `automap-render` | The minimap of a map as the game builds it, classic and HD side by side, with per-code counts and the keys that have no row. |

Order in a build: tile and scene writers, then `automap-rows`, `automap-art`, `automap-void` (it rewrites the `.ds1`
and tile sets already copied into the build's data folder). `--pins` keeps the bright cels of an earlier release so a
rebuild does not move them.

## Warning: `automap-void` crashed legacy graphics

In the overhaul port these tools come from, the release candidate with `automap-void` crashed the game in **legacy
graphics** after several of the mod's areas in one session: `BC_ASSERT: n < m_size` (n 29423, m_size 2048), then an
access violation in D2RCore. A bisection in game pinned it on this step's output: 8 of 8 full tours of the mod's areas
crashed with it, 0 with only the tile files it writes put back to the previous release's. `automap-rows`,
`automap-art` and the HD map art were ruled out. The exact cause is not known. Suspects: the tile copies themselves
(one map got 66) and a stock D2R tile set the step copies into the mod to append to (there, `act1/monastry/facade.dt1`).

What the port did: it shipped without the step (rows and bright cels only) and has not put it back. The cost was a few
wall lines running into the black on the minimap of 7 maps. Do the same. If you try the step anyway, tour every map it
edits in legacy graphics, in one long session, before you release.

## Checking in game

There is no in-game check here (the one it was ported from was tied to one project's harness and map). Use the
toolkit's testing tools (`testing/README.md`): lay the build (or an `overlay` folder) into a test slot with a pre-launch
step, start the game on that slot, go to the level, open the automap, and take one screenshot in HD and one in legacy
graphics. Compare them with `automap-render`'s pair; the cells it lists under `no_row` are the ones still missing.
