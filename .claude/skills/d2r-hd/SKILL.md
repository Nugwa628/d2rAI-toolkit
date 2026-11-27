---
name: d2r-hd
description: Carry a mod's classic (legacy) art into D2R's HD graphics so HD looks like classic, upscaled - classic maps and their tiles in HD, the minimap (automap rows), monsters with only classic sprites (animated sprite card or a 3D model rebuilt from the sprite), skill and missile effects as flipbooks, outside 3D models; and render, compare and sign off HD art with the offline hdkit viewer and its MCP server. Use when HD shows the wrong art, nothing, a stand-in, or looks blurry at 4K, or when a new HD model or map must be checked before a game run.
---

# Classic art in HD

Rule: **classic is the reference.** HD should show the mod's own art at HD quality, not D2R's stock look. Judge every step
with a `classic | HD before | HD after` picture at the same spot, and say honestly what is fixed, not fixed and worse.

Read first: knowledge/hd-graphics.md (files and indexes), then the topic page: knowledge/hd-maps.md,
knowledge/hd-monsters.md, knowledge/hd-viewer.md, knowledge/hd-effects.md.

## Set up once
1. A venv with numpy, Pillow, scipy, `wgpu` (renders) and `mcp` (the MCP server). Nothing system-wide.
2. Copy `hdkit/hdkit.example.toml` to `hdkit.toml`. Set `[game] dir` (a test copy of the game; CASC is read from it on
   demand into a capped cache), `[locks] globs` (the testing tools' slot locks), `[mod] classic` (your mod's classic
   files) and `[mod] data` (your built mod's data), `[paths] art_store` (outside the repo).
3. Check: `python -m hdkit.view camera`, `python -m hdkit.view gate` (exit 0 = a GPU job may run).
4. Outside tools only when needed: `realesrgan-ncnn-vulkan` with `realesrgan-x4plus` (upscales), Blender (LODs, pinned
   renders, as a separate process). Download from upstream; never commit them or their outputs.
5. For agents: register the MCP server as in mcp/README.md. Every render tool returns a picture: look at it.

## Viewer first, game last
1. Make the change in the mod project and build it (`python -m modkit build <project>`).
2. Render it offline: `python -m hdkit.view render <unit json|.model> --facings 8 --pass lit` (and `--pass albedo` to
   judge art), `sheet --modes nu,wl,a1,gh,dt`, or for maps `python -m hdkit.view map sheet <map> --cells x0,y0,x1,y1`.
3. Compare with classic in the same picture. Use the viewer for shape, pose, corpses and relative changes; do not trust
   it for absolute colour of glowing parts or absolute live brightness (knowledge/hd-viewer.md, "Calibration").
4. Iterate until the sheet is right. Every GPU job waits at the gate; never override it.
5. Then one in-game sign-off: `python -m hdkit.signoff all <token>` (a test copy of the game, foreground, slot lock,
   restore after), or the project's own game check. Add a row to TESTING.md.

## Classic maps in HD (knowledge/hd-maps.md)
1. Find the map's tiles and HD preset: `python -m hdkit.view map info <map>`, `map cell <map> x y`.
2. Build every layer from the map's own `.dt1` tiles: floors as chunk models, walls as sprite cut-outs, lower walls and
   wall faces under the floor line painted flat, black ground where the map has no floor, D2R's base pieces and biome
   overrides off. Pick rarity variants per cell.
3. Light per map from levels.txt (ambient, `f^2.2`), the player's pool and the light monsters / lit objects as point
   lights; tune gains against classic shots (two or three rounds). A cave biome with a key light of 0 shows black walls.
4. 4x for 4K: Real-ESRGAN x4plus on the stitched atlases, floors in a 3x3 lattice of themselves. Store the atlases in the
   art store, keep only the manifest.
5. Pack the chosen maps for a test: `python -m hdkit.maps overlay BUILD_DATA OUT MAP ...`.
6. Preset rooms inside random levels still show D2R's rooms around them: say so in the report.

## The minimap (knowledge/hd-maps.md, "The minimap")
1. `python -m hdkit.maps automap-rows OUT_AUTOMAP_TXT --plan PLAN` adds rows for the mod's wall keys per level type
   (stock keys skipped; the tool proves stock maps unchanged).
2. `python -m hdkit.maps automap-art build MOD_DATA --plan PLAN` writes bright legacy cel copies in free cels (never past cel 1498).
3. `python -m hdkit.maps automap-void build MOD_DATA --plan PLAN` moves walls in the void to a key without a row; review the picks on a world
   render first. **Leave this step out of release builds:** in the port it comes from it crashed legacy graphics after
   several areas in one session (`BC_ASSERT n < m_size`), cause not pinned down (hdkit/maps/README.md, "Warning").
4. Check each map's before / after render (`python -m hdkit.maps automap-render`), legacy and HD.

## A monster with only classic art (knowledge/hd-monsters.md)
1. Confirm why HD is wrong: monstats `Id` -> `data/hd/character/monsters.json` -> unit JSON. No row = invisible in HD.
2. Default route: the **animated sprite card**: classic frames upscaled (x4plus with alpha through the same scaler, 2x
   for 1080p), one clip per mode at the COF frame time, colour gain about 1.35-1.5. For facing, the plugin hook in
   knowledge/plugin-techniques.md section 7 (one clip per facing under the weapon-class keys).
3. 3D route where real turning, light and shadow matter (big or slow monsters, bosses): build it by hand against the
   sprite, following docs/monster-sprite-to-3d.md (design brief, donor skeleton at the body's lengths, design graph
   through Blender's skin modifier, parametric rigid parts, paint and bake, clips fitted over all 8 facings, death and
   corpse). Follow the engine rules E1-E14 in knowledge/hd-graphics.md. Pick monsters without mirrored facings first.
   Do not reconstruct the shape with AI image-to-3D or project the sprite as texture: both were tried and looked worse.
   The stage scripts are `python -m hdkit.monster3d <script> --monster <monster.json> ...` (the guide's command table;
   examples/monster3d/ has a filled-in monster config and an art-free run).
4. Render it in the viewer at the 8 facings and every mode next to classic before any game run.

## Skill and missile effects (knowledge/hd-graphics.md, "Effects from classic frames")
1. Follow the skill's whole missile chain (skills.txt -> missiles.txt explosion and sub-missiles): rows without art may
   carry a light; rows on vanilla art still show D2R's stock effect.
2. Each picture becomes a particle flipbook: additive, unlit billboard, size, anchor, timing and light from the tables and
   the DCC. Multi-hue art as three channel layers.
3. Lanczos 2x for soft glows, x4plus for hard-edged art. Piles of copies need their own gain.

## D2R's own HD effects (knowledge/hd-effects.md)
D2R draws skill, missile, aura, state and object effects from baked PopcornFX files (`data/hd/vfx/particles/**`).
`hdkit.fx` reads, simulates, draws, edits and authors them offline:
1. Inspect: `python -m hdkit.fx info|dis|sim <file.particles>`; what the viewer covers: `python -m hdkit.fx coverage`.
2. Draw at the game camera: `python -m hdkit.view fx <effect> [--unit <unit json>] --t 1`, a whole cast with
   `--cast "<skill>" --tables <excel> --toward dx,dy`. GPU jobs wait at the gate.
3. Edit in the web editor (`python -m hdkit.view fx --port 8766`, docs/hdfx-web.md) or through the `fx_*` MCP tools
   (docs/hdfx-mcp.md); scripts in the readable language (docs/hdfx-lang.md). Check open accuracy gaps on the
   knowledge page before trusting absolute brightness or timing, then sign off in game.

## Outside 3D models
glTF/GLB or OBJ (CC0 or your own) -> normalise (Y up, height in cells) -> LODs -> `.model` x N + albedo / normal / ORM
`.texture` + a `model_lod_desc.bin` entry (`hdkit/formats/`). Keep a licence record per asset (licence, author, source,
sha256). No share-alike or non-commercial art without the user's say.

## Never
- Commit game files, extracted or upscaled art, renders of game assets, or AI weights.
- Start a GPU job while a game run holds its lock, or a game on the user's own install or desktop.
- Ship AI model weights with region or non-commercial limits.
- Hand-tune one map or monster without writing the numbers down: put new engine facts in knowledge/.
