# HD monsters from classic sprites: sprite cards, facing, 3D models

How to show a monster that has only classic art (its own COF/DCC) in D2R 3.3 HD (build 93847), so HD looks like classic,
upscaled. Two routes were built and run in game on a mod with its own monster sprites: an animated **sprite card** and a
**3D model** built by hand against the sprite. Facts are from game runs (Blood Moor, 1600x900, several runs per finding) and file
surveys, as marked. Formats and the HD monster chain are in [hd-graphics.md](hd-graphics.md#monsters).

## Which route

| | Sprite card | 3D model from the sprite |
|-|-|-|
| Looks like classic | yes: the classic frames, upscaled, sharp | a designed model close to classic in shape, colour and motion; an HD look rather than the exact pixels |
| Turns with the monster | with a plugin hook (below) | yes, natively |
| Lighting | lit like any model: darker than classic, colour gain needed | lit by the engine; albedo set for the game light |
| Cost per monster | a batch job once the build step exists | about a day in the viewer for a known body type, more for a new one |
| Best for | most monsters, small ones especially | big or slow monsters and bosses, where real turning shows |

For small monsters the card is often the better choice: it keeps the classic look exactly. A hand-built 3D model pays
off where real turning, light and shadow matter. Models reconstructed by AI from the sprite looked worse than the card.

## Why HD shows the wrong art

| Step | File | Field |
|-|-|-|
| 1 | `data/global/excel/monstats.txt` | `Id`, `Code` (the classic token) |
| 2 | `data/hd/character/monsters.json` | `Id` -> HD unit name |
| 3 | `data/hd/character/enemy/<unit>.json` (UnitDefinition) | state machine, skeleton, model, `TransformDefinitionComponent.scale` |
| 4 | `<folder>/<name>_lodN.model`, found through `data/hd/model_lod_desc.bin` | material `VariantMaterial`: albedo, normal, ORM textures |
| 5 | the state machine JSON | states by `modeId`, `animationBindings` by weapon class (monsters: `hth`) -> clips in `combined.animations` |

A monster with no `monsters.json` row is **invisible in HD** and still fights: HD never falls back to the classic sprite
(game run). A mod that maps its own monsters to stock D2R models (a stand-in) shows those models instead of its art.

## Route 1: the animated sprite card

### Build

- One camera-facing card per classic frame, all on one mesh and one texture atlas. Each card hangs half on a direction
  bone and half on a frame bone; "off" bones sit 4,000 units under the ground. One Granny clip per mode at the classic
  frame time (COF speed: `fps = 25 x speed / 256`), own skeleton and state machine, crossfade 0.
- `doNotInheritRotation: true` keeps the card facing the camera: the model keeps the world's axes whatever the facing, and
  the card is built upright along the screen's right axis for the fixed HD camera.
- Five modes (NU, WL, A1, GH, DT) animate from data alone (game run). A mode that is a skill sequence (`sq`) needs no state
  (a state for it asserts `skillIndex != INVALID_SKILL`).
- Bones: 90 bones for 5 modes (8 direction + 81 frame + root) ran fine; 107 for 7 modes; the engine's limit was not probed.

### Upscaling the frames

Per frame: bleed the opaque colour into the transparent pixels first (no dark halo), pad 8 px, upscale the colour 4x,
then the alpha with Lanczos plus a smooth step around 0.5 (± 0.15). Passing the alpha mask through the same upscaler
rounds the classic stair-step outline.

| Method | Flicker over a 15-frame clip (mean change, 0-255; the animation itself is 4.4) | By eye |
|-|-|-|
| Lanczos 4x | 4.39 | soft |
| hq4x | 4.27 | clean edges, still pixel art |
| Real-ESRGAN x4plus | 4.59 | HD and still the mod's art: the pick |
| Real-ESRGAN animevideov3 | 4.65 | steadiest model, closest to classic |
| Real-ESRGAN x4plus-anime | 5.00 | restyles, shimmers frame to frame |

2x is the sensible ceiling at 1080p (about 2 screen pixels per classic pixel); 4x pays only at 4K. Bytes for one monster
with 784 frames (BC3, trimmed, no mips): 5.8 MB at 1x, 21.5 MB at 2x, about 86 MB at 4x; classic DCC is 1.1 MB.

### Brightness

`VariantMaterial` is lit: the card shows at about 65 % of the sprite's value next to the player, darker elsewhere. No
unlit mode or emissive slot works on it (`EmissiveIntensity` 4 and 20: no visible effect). A colour gain on the albedo
of about 1.35-1.5 is the practical fix; matching exactly needs about 1.7, which clips highlights.

### Facing: data cannot do it, a plugin hook can

From data alone the card shows one direction whatever way the monster faces. Every data route failed in game:

| Try | Result |
|-|-|
| `doNotInheritRotation: false` | the engine turns the whole model continuously by the facing (not in 8 steps; model forward is +Z), so the flat card turns edge-on |
| 8 one-sided cards turned back by their direction's yaw | culling only removes cards more than 90 degrees away: 3-4 show at once. Leaning them narrows the window but smears the art |
| The trap units' aim layer (`AimLayersComponent`) | draws every direction at once (aimTarget 0, 1, 2, 4) |
| Anything else in the unit data | nothing in 275 units and 56 state machines depends on facing |

What works is a plugin that picks the clip: the card binds one clip per facing under the first 8 of the weapon-class keys
of each state's `animationBindings` (in the engine's own order: hth, bow, 1hs, 1ht, stf, 2hs, 2ht, xbw), and a hook hands
the engine the facing key instead of the weapon class. Hooks, assert fix and addresses:
[plugin-techniques.md, "Sprite-card facing"](plugin-techniques.md#7-sprite-card-facing). Result over 32 mode x facing
cells: 27 right, 3 unclear, 2 wrong, at the same level as the judge scores classic against itself; 0 asserts, no fps cost.

## Route 2: a 3D model built by hand against the sprite

The worked method, step by step, is [docs/monster-sprite-to-3d.md](../docs/monster-sprite-to-3d.md). In short: the
classic sprite stays the reference, and the model is **built by hand**, not reconstructed by an AI model:

| Step | What |
|-|-|
| Reference | every mode x 8 facings from the COF/DCC with the act palette; mode boxes; COF timing; a fitting camera (orthographic, one azimuth per facing) |
| Design brief | proportions, parts, surface, colour bands and motion per mode, written as an artist would model them, not as pixel targets |
| Skeleton | a D2R donor of the same body type with every mode and spare bones for rigid parts and a corpse shell; edited to the body's own lengths, clips retargeted |
| Shape | a design graph of centre-line nodes run through Blender's skin modifier and subdivision (an outside process); rigid parts (a cap, a shell, a weapon) as a parametric grid; sculpted high mesh for the bake |
| Paint and bake | vertex colours painted in classic's layout, baked high to low with Cycles: normal, AO, albedo, roughness, glow |
| Weights and LODs | weights from the design graph (no weight crosses air), a stretch check over every clip, decimated LODs |
| Material and glow | DXT5nm normals, ORM alpha 0 on matte parts, a self-lit layer copied from a stock glowing monster |
| Clips | one pose per classic frame, fitted over all 8 facings at once (below) |
| Death and corpse | death retimed to classic's DT frames, a corpse pose fitted to the DD frames, corpse texels of its own |
| Check | the viewer first, then one in-game sign-off ([hd-viewer.md](hd-viewer.md)) |

Measured on a small capped biped: body / part IoU against classic 0.77-0.80 / 0.87-0.89 per mode; 46,133 triangles at
LOD0 (stock human-size units are about 36-52k); about 10 h of viewer work for the first monster, about a day for a second
of the same body type. The engine rules the model must follow are in one place:
[hd-graphics.md, "Engine rules for 3D monsters"](hd-graphics.md#engine-rules-for-3d-monsters).

**The camera.** The HD camera equals classic's in direction: orthographic, the same azimuths. Render like the game at 30
degrees elevation (the game's constant); the camera that best reproduces classic sprites in a silhouette fit came out at
about 35 degrees for one monster, so fit it per monster rather than assume it.

### Fitting clips to classic

One pose per classic frame, on the donor skeleton, scored over the 8 directions together (silhouette IoU, the IoU and
tilt of a part with its own colour, an arm term, an outline-distance term so thin limbs count, a ground penalty): fitting
all directions at once fixes the depth one view cannot. A pose template of about two dozen parameters (turns of named
bones about world axes at the bind joint, plus a body shift into `cog`), random search with shrinking steps and a prior
toward the previous frame, a rescue grid when the score drops, then smoothing over time. 5-10 CPU minutes per mode, no
GPU. Gate a coloured part's mask before it scores, or limbs of the same colour pull the fit. Check the donor's bind pose
(T or A) against the template, or arm turns go about the wrong axis.

### Researched and weaker: AI shape and sprite projection

Tried first, over many rounds, and kept only as a record:

- **AI image-to-3D** (TripoSG, MIT code and weights; a region-restricted multi-view model as a comparison only, not
  shippable): silhouette overlap up to 0.84 over 8 facings on a simple monster, 0.61-0.64 on a humanoid with a weapon,
  but a crumpled surface and a generic lumpy body. The best open models also needed NVIDIA-only extensions.
- **Projecting the 8 sprite views as texture**: the views disagree by about 50 % at the same point, about 40 % of texels
  are never seen, and the sprite's baked light gets lit a second time: a patchwork. Blending per 3D point halves the
  seams but never looks HD.
- **More detail from the sprite** (shape from shading, AI upscale of the atlas, a bigger bake): the sprite holds no more
  information (about 60 x 100 px against a 2048 atlas).
- Sprites with mirrored facings (the weapon changes hand) cannot match any single 3D object: pick monsters without
  mirrors first.

### Not in the toolkit yet

The stage scripts of this route (reference decode, design graph and Blender scripts, bake, clip fit, death and corpse,
export) are not in `hdkit/` yet; they follow in a later change. The viewer, the sign-off and the formats they need are.

## See also

[hd-viewer.md](hd-viewer.md) (render and check a model offline first), [hd-graphics.md](hd-graphics.md) (monster files,
new model paths), [plugin-techniques.md](plugin-techniques.md#7-sprite-card-facing) (the facing hook),
[engine-limits.md](engine-limits.md).
