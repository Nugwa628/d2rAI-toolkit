# Classic monster sprite to D2R 3D model

This guide shows how to turn a classic (legacy) Diablo II monster sprite into a 3D monster model for Diablo II:
Resurrected's HD mode. The method keeps the classic sprite as the reference and builds the model by hand: a skeleton,
a designed and sculpted mesh, a painted and baked texture, and clips fitted to the sprite's own frames. You iterate in
an offline viewer and use the game only for the final check.

The worked example is the Glowing Fungus, a small mushroom-capped biped from a classic mod. It took 18 rounds. Rounds
1 to 13 tried AI image-to-3D reconstruction and sprite projection, and those rounds are the reason this guide
recommends hand building. The numbers quoted below are the Fungus's.

> This repository stores no Blizzard or mod art. Everything that reads game files reads your own local copy. Large
> outputs (meshes, bakes, textures) belong in a local art store (`hdkit/store.py`), not in git.

**Tools in this repository.** The stage scripts of this method are `python -m hdkit.monster3d <script> --monster
<monster.json> ...` ([section 20](#20-commands) lists every command, [section 21](#21-the-monster-config) the
monster config). The viewer, its browser page, the CASC cache, the MCP server and the sign-off are in `hdkit/`
(`python -m hdkit.view`, `python -m hdkit.signoff`, [mcp/](../mcp/README.md)); the D2R format modules (GR2 models and
clips, textures, the model index, skeletons, DCC, COF, classic frames, particles) are in `hdkit/formats/`.
[examples/monster3d/](../examples/monster3d/README.md) runs the route on vanilla files only, and holds the worked
example's monster config and design, shape and paint inputs (numbers only). The engine facts are in
[knowledge/hd-graphics.md](../knowledge/hd-graphics.md) and [knowledge/hd-monsters.md](../knowledge/hd-monsters.md).

## Contents

1. When to make a 3D model
2. What you need
3. The pipeline at a glance
4. Step 1: the reference
5. Step 2: the design brief
6. Step 3: donor skeleton and proportions
7. Step 4: shape
8. Step 5: UVs, paint and bake
9. Step 6: weights and LODs
10. Step 7: material and glow
11. Step 8: clips fitted to the sprite
12. Step 9: death and corpse
13. Step 10: write the D2R files
14. The viewer-first loop
15. In-game sign-off
16. Engine rules
17. What did not work
18. Checklist
19. Cost
20. Commands
21. The monster config

---

## 1. When to make a 3D model

An upscaled sprite card (the classic frames on a camera-facing quad) is often the better choice for small monsters.
It keeps the classic look exactly and costs minutes. A 3D model pays off where real turning, lighting and shadows
matter: big or slow monsters, bosses, or monsters players look at closely. Decide per monster.

## 2. What you need

- Your own extraction of the game's files: the classic COF/DCC/palettes, and the HD donor unit (skeleton,
  `combined.animations`, state machine, unit JSON), the `vis` light files, and one glowing monster's object effect if
  the monster has self-lit parts.
- Python 3 with numpy, scipy, scikit-image and Pillow; `wgpu` for the viewer.
- Blender 4.5 (LTS), run headless as a separate process. Blender is GPL; use it as a tool, never ship it.
- A test install of the game for the final check, with a dev plugin that can spawn, face, cast and kill a monster.
- No AI model weights are needed for this route.

## 3. The pipeline at a glance

| Step | Input | Output | Kind |
|-|-|-|-|
| 1 reference | classic COF/DCC, palette | frames of every mode x 8 facings, mode boxes, COF timing, the fitting camera | automatic |
| 2 design brief | the frames | proportions, surface, colour bands, motion per mode | by hand, about 1 h |
| 3 skeleton | a donor HD skeleton | the skeleton at the body's own lengths, clips retargeted | semi-automatic |
| 4 shape | design graph | low (game) and high (bake) meshes, sculpt fields | script + Blender |
| 5 paint and bake | high mesh with vertex colours | normal, AO, albedo, roughness, glow maps in the atlas | script + Blender (Cycles) |
| 6 weights and LODs | design graph, low mesh | per-LOD skinned meshes | automatic |
| 7 material and glow | maps | ORM, DXT5nm normal, glow layer from a stock object effect | automatic + gain tuning |
| 8 clips | classic frames, skeleton | neutral, walk, attacks, get-hit fitted to all 8 facings | automatic, 5-10 CPU min per mode |
| 9 death and corpse | classic DT and DD frames | death clip, corpse pose, corpse texels | semi-automatic |
| 10 export | everything | models, textures, skeleton, animations, state machine, unit JSON, index entries | automatic |
| viewer loop | the build | sheets, close-ups, turntables, metrics | automatic |
| sign-off | the build | one game run, a game-vs-viewer sheet | automatic, one run |

## 4. Step 1: the reference

**Frames.** Decode the monster's COF and DCC files for every mode (NU neutral, WL walk, A1/A2 attacks, GH get-hit, DT
death, DD corpse, plus any skills) and all 8 facings, with the act's palette, compositing the COF layers in the game's
order. Store each mode's box with the unit's ground point as origin.

**Check for mirrored facings.** Some classic monsters draw three facings as exact mirror images of others (IoU 1.000
after a flip). A one-handed monster cannot match both sides in 3D. Prefer a first monster without mirrors.

**Timing comes from the COF, not from the donor.** fps = 25 x speed / 256; length = frames / fps. The Fungus: walk 19
frames at 25 fps (0.76 s), attacks 15 frames at 19.53 fps (0.768 s). The attack's hit frame then lands at classic's
time on its own.

**Two cameras.** Both are orthographic.
- *Fitting camera*: the camera that reproduces the classic sprites. Elevation 35 degrees fitted best for the Fungus,
  with one azimuth per facing and a scale k (px per model unit) taken from the width of a rigid part in the NU
  frames.
- *Game camera*: D2R's HD camera is 30 degrees elevation, azimuth -135, with a view height of 53.03 world units at
  zoom 1 (16.97 px per unit at 900 lines). Use it to render like the game.

## 5. Step 2: the design brief

Write a short brief from classic's frames before modelling. Describe the creature as an artist would model it, not as
per-pixel targets.

- **Proportions** in donor units, with the ground at y = 0. Fungus: cap about 4.8 wide, its underside at y 7.1;
  shoulders 6.2, hips 4.2, knees 2.2, ankles 0.4.
- **Parts.** Fungus: a dark fibrous stalk that is also the neck; long thin arms to the knee with knobbly elbows; big
  dark claw hands with 3 hooked fingers and a thumb; thin legs with a woody knee knob; splayed club feet with 3 toes.
- **Surface and colour.** Fungus: near-black matte flesh, long fibres, knots and cracks; sparse, one-sided pale-blue
  patches (never rings) at the knees, shins, forearms and feet; mid blue-grey arms.
- **Motion per mode.** Fungus: bent knees and a hunch; a leaning, lurching walk; arms swing forward in A1; the trunk
  recoils and one arm flings out in GH; the death crumples sideways with the cap tipping over.

The decision that unlocked the Fungus was: the body does not need to match the sprite pixel for pixel; build a body
that is close enough. Matching the silhouette is the fit's job (step 8), not the mesh's.

## 6. Step 3: donor skeleton and proportions

- **Choose a donor** of the same body type with every mode the monster needs and spare bones: one for each rigid part
  (the Fungus cap rides the donor's unused `jaw`) and one for a corpse shell (`loin_cloth`). The Fungus used a stock
  shambling biped.
- **Edit the skeleton to the body's own lengths** and retarget every clip's position tracks to the same lengths
  (Fungus: forearm and wrist x1.3, clavicle x1.25, hips x1.35 apart). Write the same lengths into every LOD's skeleton
  and the unit's `.skeleton`.
- **Do not squeeze a mesh into the donor's T-pose.** Binding a mesh with hanging arms to a T-pose skeleton, or scaling
  it into the donor's proportions, is what made the reconstructed Fungus's arms into short blobs and its legs into one
  column.
- **Check the donor's bind pose against your pose template.** A template that assumes an A-pose turns a T-pose arm
  about its own axis: the Fungus's arms "could never swing forward" for 15 rounds because of this.

## 7. Step 4: shape

**Body: a design graph through Blender's skin modifier.**
1. Lay out centre-line nodes (centre, radius, bone weights, chain, kind) along the edited skeleton.
2. In Blender (headless), run the graph through the skin modifier and subdivision. Subdivision level 2 is the game
   mesh (Fungus body about 23.6k vertices at LOD0); level 4 is the high mesh for baking.
3. Displace the high mesh with numpy fields along each limb: twisted ridges, lumps, knobs, fibres, cracks and fine
   noise.

**Rigid parts (a cap, a shell, a weapon): a parametric grid.** The Fungus cap is one closed grid, one ray per angle
from the cap's centre. Its profile runs over a flat top to about 0.85 R, then over a shoulder into a rolled bead that
curls under, then along the underside to where the stalk meets it, rising into a shallow gill chamber. Sculpt the
high version (150 gills with short ones between, creases, lumps, warts, pores; 2.9M triangles). Keep the part's frame
(centre, up axis, outline) from the fitted pose so every clip still moves it correctly, and give the stalk a flared
apex that runs into the cap so the two read as one creature.

**Budget.** Compare against stock monsters of the same screen size: stock human-size units are about 36-52k triangles
at LOD0. The Fungus ships at 46,133 (body 33,695, cap 8,960, corpse shell 3,478).

## 8. Step 5: UVs, paint and bake

- **UVs:** smart-project the low mesh and pack its islands only into atlas texels that nothing else uses. A round
  part can take disc UVs (radius = arc length along the profile, so the only seam is the rim's equator). Pack
  densely: the Fungus body went from 1,047 to 1,771 texels per UV unit when re-packed, about 1.7x sharper.
- **Paint** the high mesh's vertex colours in classic's layout: albedo, roughness, and a glow mask. Fungus cap: a
  near-black navy crown with navy clouds and blue specks, green flecks at its edge, a saturated blue band, pale-cyan
  blotches toward the rim, dark blue-grey gills.
- **Bake with Cycles, high to low:** tangent-space normal, AO, albedo, roughness, glow. Generate every LOD of a
  parametric part from the same parameters, so one bake serves all LODs.
- **Protect texels.** When the atlas is shared (an approved part, a corpse strip), splice at the compressed-block
  level: any 4x4 block that touches a protected texel keeps its old bytes at every mip.
- **Fill unused texels** with the nearest used colour, so coarse mips get no dark halos.

Why not project the sprite? See section 17: the 8 views disagree, a third of the surface is never seen, and the
sprite's baked light gets lit a second time.

## 9. Step 6: weights and LODs

- **Weights from the design graph:** each node carries its bone weights; interpolate them to the mesh, smooth them
  over the welded mesh (30 iterations for the Fungus), keep the top 4 bones, floor 0.02. Each limb is its own piece, so
  no weight can cross air.
- **Rigid parts** have weight 1 on their own bone.
- **LODs:** Blender's decimate (Fungus ratios 1, 0.5, 0.26, 0.15, 0.09) or, for parametric parts, a lower grid
  resolution.
- **Stretch check:** pose the skinned mesh at 0.05 s steps through every clip and sum the excess edge length beyond
  1.5x the bind length. It exposes smeared limbs that a silhouette score can reward.

## 10. Step 7: material and glow

**Material.**
- Normal maps are DXT5nm (rule E3).
- For matte parts set ORM alpha to 0: alpha is a specular level (rule E5).
- Keep dark parts dark in the albedo (rule E6).
- Fungus flesh: roughness 0.95, metal 0, low specular.

**Glow (self-lit parts).** The monster material has no working emissive. D2R's own glowing monsters (Fire Golem,
Finger Mage, Wraith, Will o' Wisp) draw their skinned model a second time, additively, through an object effect: a
PopcornFX mesh layer with `ModelInstanceTracking`, whose shape comes from an alpha mask in the model's own UV layout.
To reuse it:
1. Copy the Fire Golem's object effect from your extraction.
2. Set its blend textures to the stock white texture, and set its alpha mask to your glow mask (BC4).
3. Give it a one-colour gradient strip for its gradient remap.
4. Switch off the flame wobble and the dissolve.
5. Make the scripts' UV scale, offset and scroll identity. Without this the mask tiles and slides.
6. Add one entity per glowing part to the unit JSON.

Tune the strength on a mask at mip 4: keep its mean small, and let only the top few percent of texels glow. The first
Fungus values (1.0 cap / 0.45 body) were white-hot; 0.22 / 0.12 matched at night. Under the night light a blue glow
gains green, so set the glow colour bluer than the target.

## 11. Step 8: clips fitted to the sprite

Fit one skeleton pose per classic frame, scored over all 8 facings at once (one view cannot fix depth; eight can).

- **Pose template:** about two dozen parameters, each a turn of named bones about a world axis at the bind joint, plus
  a body shift. Each parameter has a range and a search step. The lift goes into the `cog` bone, because the engine
  ignores root translation.
- **Score per facing:** silhouette IoU, plus a part IoU and a tilt term for any part with its own colour (the cap),
  plus an arm term (your arm pixels against what remains of classic's silhouette once your torso, legs and part are
  taken out), a chamfer (outline distance) term so thin limbs count, and a ground penalty.
- **Gate the classic part mask:** a colour test alone mixes the cap rim with pale limbs. Use the part's hull only when
  its area and rim coverage are plausible.
- **Search:** random steps that shrink; frame 0 takes the best of several starts; each later frame starts from the one
  before; a temporal prior; a rescue grid when the score drops; then [1 2 1]/4 smoothing (cyclic for loops).
- **Fit on a base stage with the donor's trunk.** Trunk parameters are deltas on the stage's own clip, so chaining fits
  across stages compounds them.
- **Write the clips** with the animation file's own curve types (float knots and controls, 60 samples a second,
  Catmull-Rom between the fitted frames), set each clip's duration to the mode length, and keep the other clips
  unchanged.

Fungus, final, body / cap IoU: NU 0.80 / 0.89, walk 0.77 / 0.88, A1 0.78 / 0.87. One mode costs 5-10 CPU minutes.

## 12. Step 9: death and corpse

- **Own death and dead clips** go into a copy of the donor's animation file, written with the curve types it already
  has. The state machine is copied with every clip path moved.
- **Retime the fall to classic's DT frames** with a fall-progress curve: the screen drop of a tracked part from its
  first DT frame to its last, 0 to 1, middle value over the 8 directions. Get the rms over DT below about 0.08. Copy the
  shape of the curve, not only its end: the Fungus slumps first, holds, then falls.
- **Hand over to the corpse inside DT.** Hand over at about 0.85 of the shortest DT, with a knot just before it and a
  near-zero death-to-dead fade. The default crossfade blends local bone transforms and can make the monster vanish for
  a frame.
- **The corpse has no glow** (rule E2), so its colour lives in its albedo. Give it its own texels: a strip of the atlas
  (squeeze all u by 7/8 and use the freed strip), recoloured from classic's DD frames.
- **Corpse pose:**
  1. Fit one 3D pose to classic's DD frames over all 8 facings.
  2. Score only what lies above the floor, and bound how far a part may sink.
  3. Fit classic's DD origin shift: classic draws its corpse frames 12-15 px below the ground point.
  4. A simple gravity settle within joint limits then gives a limp, believable rest pose.
- **Dim the dead part.** A part that glows alive should read as dull and dark when dead, and its glow masks must be
  zero where the corpse samples.
- **Fungus corpse, final:** the body lies limp on its side, and the cap stays attached and stands on its rim,
  slightly bent where it touches the ground.

## 13. Step 10: write the D2R files

- **Models:** each LOD's `.model` (GR2) with skinned vertices. Indices are 16-bit unsigned up to 65,535 vertices per
  mesh (32-bit above that).
- **Textures:** BC3 albedo and ORM (2048 with mips), BC3 DXT5nm normal (linear), BC4 glow masks, RGBA8 gradient strips.
- **Skeleton:** write the same edited lengths into the model skeletons and the unit's `.skeleton`.
- **Animations and state machine:** `combined.animations` with the fitted clips, plus the unit's own state machine.
- **Unit JSON:** the model, materials, the glow entities and their dependencies.
- **Index files:**
  - `hd/character/monsters.json` maps the monster's id to the new unit.
  - `hd/model_lod_desc.bin` needs an entry for every new model path, with its bounds. A model path that is missing
    there loads but never draws.

## 14. The viewer-first loop

**Why a viewer.** A game run takes minutes. It needs a free test install, and its numbers move with the time of day,
the weather and lightning. A viewer that reads the same files and renders them with the game's camera and light lets
you judge a change in seconds.

**What the viewer does:**
This is `hdkit.view` ([knowledge/hd-viewer.md](../knowledge/hd-viewer.md)).

- It resolves logical game paths over layered roots: your build first, then a cache of the base game files.
- It reads the model, skeleton, curve, texture, unit JSON, state machine and classic COF/DCC formats.
- It renders on the GPU (WebGPU) with the game's orthographic camera and light rig: the act's `vis` file, exposure,
  the tone and LUT path, the unit light factor and bloom.
- It renders the same scene in a browser page.
- It writes sheets, turntables, high-resolution tiles, debug passes (albedo, normals, ORM, glow, wire) and
  comparisons.

**Calibrate once, then trust it only where it holds.** Calibrate against existing game frames with held-out sets:

| Fungus result | Verdict |
|-|-|
| Corpse IoU 0.73-0.75 | judge in the viewer |
| Shape, pose, relative before/after changes | judge in the viewer |
| Live ring colour 72-99 RGB away; absolute live brightness 0.98 on one set and 1.43 on another; live silhouette on held-out frames 0.33 | game only |

The viewer has no particle runtime, so additive glow layers are the largest viewer-versus-game difference.

**Every round produces:**
- a review sheet: classic | previous | new, 8 facings x every mode, at the same time in each mode;
- close-ups of the changed part, and a turntable;
- metrics: body and part IoU and tilt, body colour by luminance band against classic, skin stretch per clip, triangle
  counts, and a byte check that approved parts did not change.

**Look first, measure second.** On the Fungus a higher IoU twice meant a worse model, because stretched or smeared
limbs fill more of classic's outline. The designed body scored 0.01-0.02 lower than the procedural one and looked far
better.

## 15. In-game sign-off

Make one run at the end, on a test install that nothing else uses:
1. Lay the build over the install and restore it byte for byte afterwards.
2. Hold the install's lock from the overlay to the restore, with no gap.
3. Run on a private display so the game never takes the desktop's focus.

Capture:
- every mode at 4 or 8 facing keys;
- a burst of frames through the death, polling the unit's mode;
- the corpse;
- a group shot with the player at play distance;
- the asserts, crash reports and fps.

`python -m hdkit.signoff` does this run and the sheet. Its game half goes through a backend
([hdkit/signoff/backend.py](../hdkit/signoff/backend.py): the built-in Linux and Windows ones, or your project's own)
and a seat hook that keeps a machine-wide budget of running games (`seats = "testing"` uses the toolkit's own seats in
`testing/`). The game needs a test plugin with the harness verbs `spawn`, `place`, `facing`, `mcast`, `kill` and
`move`: [hdkit/signoff/HARNESS.md](../hdkit/signoff/HARNESS.md) documents each one.

Then render each captured (facing, mode, time) in the viewer and make a game | viewer | outline sheet. Write down
every difference (glow and bloom first) and decide each one: fix it, or accept it.

Fungus at 4K, final run: 0 asserts, 63-67 fps, and every mode works. The live cap glows brighter cyan in game than in
the viewer; that difference was accepted.

## 16. Engine rules

- **E1** The root bone's translation is ignored. Move the body with `cog`.
- **E2** Object-effect glow ends when the unit becomes a corpse. No fade, kill or render switch keeps it. A
  model-tracking layer in a `VfxDefinitionComponent` crashes at load. Put a corpse's light in its albedo.
- **E3** Normal maps are DXT5nm: BC3 read as linear, R 255, G = y (up the texture), B 0, A = x, with 128 = 0. Plain
  RGB normals are read as tangents, and the light then follows the UV charts.
- **E4** The floor hides everything below y = 0.
- **E5** ORM alpha is a specular level. Roughness 255 with metal 0 is not matte while alpha is 255.
- **E6** A black dielectric still reflects about 4 % of the environment. A near-black part needs metal, or a matte,
  low-spec material with the albedo painted at the right level.
- **E7** 65,535 vertices per mesh with 16-bit indices, which are unsigned.
- **E8** Detail smaller than about 15 texels at LOD0 density is lost at game size (the Fungus cap is minified about
  13x). Sculpt it and bake it into the normal map, or leave it out.
- **E9** The death-to-dead crossfade blends local bone transforms.
- **E10** A new model path needs a `model_lod_desc.bin` entry.
- **E11** Night light pushes a blue glow toward green.
- **E12** Lightning-lit frames spoil measurements. Drop frames whose mean luminance is above 1.3x the run's middle value.
- **E13** Object-pool asserts at exit and a launch-time crash report are engine noise.
- **E14** The material's `EmissiveIntensity` has no visible effect on these units.

## 17. What did not work (and why)

| Tried | Result |
|-|-|
| AI image-to-3D from the sprite (TripoSG; Hunyuan3D-2mv for comparison) | Silhouette IoU 0.84 over 8 facings, but a crumpled surface, a generic lumpy body under the cap, and a verdict of "terrible" from the review. The best open models also needed NVIDIA-only extensions, and some carry licences that cannot ship. |
| Projecting the 8 sprite views into the atlas | The views disagree by about 50 % at the same point. About 40 % of texels are never seen. The sprite's light is baked in and gets lit again. The result reads as a patchwork. Unifying the views halved the seams but never looked HD. |
| More detail from the sprite (shape from shading, procedural noise, AI upscale of the atlas, a 4096 re-bake) | The sprite holds no more information (61 x 102 px against a 2048 atlas), and fine specks die at game size. |
| Capsule skinning on the donor's T-pose | The mesh is squeezed, weights bleed across air, segment halves belong to the child bone, and the joints are guesses. Every clip stretched the limbs. |
| Holding the trunk exact so the part's motion stays fixed | No arm pose matched all 8 facings. Free the trunk and add part terms instead. |
| Material emissive, KTINT masks, bone-attached static VFX | No visible effect. |
| Moving the root bone | Ignored by the engine. |

Lessons about the process:
- Test every claim with something that cannot be mistaken. A solid magenta texture on a layer shows whether the
  layer draws at all. An all-black albedo shows where the darkness floor really is.
- Three of the Fungus's confident early claims were wrong and had to be withdrawn: a "glow layer that draws on
  corpses", a "vertex limit of 32,767" and an "ambient floor".

## 18. Checklist

- [ ] Route chosen (card or 3D); no mirrored facings, or a plan for them.
- [ ] Frames of every mode x 8 facings; mode boxes; COF timing; fitting and game cameras.
- [ ] Design brief.
- [ ] Donor with spare bones; skeleton at own lengths; clips retargeted; template axes checked against the bind pose.
- [ ] Design graph -> low and high meshes; parametric rigid parts on their fitted frames; sculpt.
- [ ] UVs in free texels; paint; Cycles bake; protected texels kept per block; gutter filled.
- [ ] Graph weights, smoothed; stretch check; LODs; triangle budget.
- [ ] DXT5nm normals; ORM alpha 0 on matte parts; glow layer with identity UVs and a small gain.
- [ ] NU, WL, A1, A2, GH fitted over 8 facings on a donor-trunk base.
- [ ] Death retimed; hand-over inside DT; corpse pose; corpse texels; dead glow zero; dead part dimmed.
- [ ] Viewer sheet, close-ups, turntable, metrics; approved parts byte-checked.
- [ ] One in-game sign-off run; game vs viewer notes; decisions recorded.
- [ ] Index entries (`monsters.json`, `model_lod_desc.bin`); art from the store into the build; release smoke run.

## 19. Cost

Measured on the Fungus, with one agent working at a time on a single desktop PC with one consumer GPU:

| Work | Time |
|-|-|
| The hand-built route from scratch (procedural body to designed body to designed cap, viewer only) | about 10 h wall time |
| The corpse rebuild and the in-game sign-off (7 short game runs at 4K) | about 3 h |
| Fitting one mode | 5-10 CPU minutes |
| One Blender design iteration (model, shape, preview) | minutes |
| A full Cycles bake at 2048 | tens of minutes |

For a second monster of the same body type the tools exist. Expect about one day in the viewer and one sign-off
session. A new body type adds a donor choice, a pose template and a corpse plan.

## 20. Commands

Every script runs as `python -m hdkit.monster3d <script> --monster <monster.json> [args]` from the repository root
(`python -m hdkit.monster3d` lists them; the usage of each is at the top of its file). The Blender steps run inside
Blender: `blender -b --factory-startup -noaudio --python hdkit/monster3d/blender/<script>.py -- <step> <work dir>`.
Run memory-heavy steps under a limit (`prlimit --as=10000000000` on Linux). A stage is a folder with `overlay/` (the
unit's files laid out like a mod's `data/`), `skinned_lod<k>.npz` and `build.json`; each step reads one stage and
writes a new one, so every step can be compared and undone.

| Step | Command | Notes |
|-|-|-|
| 1 reference | `s1_frames classic` | every mode x 8 facings and `frames.json` (boxes, COF timing); `s1_frames up MODE:FRAME` upscales 4x with Real-ESRGAN (optional) |
| base stage | `s0_base <stage>` | the donor's own models, textures (at 2048) and clips under the new unit's name |
| 3 skeleton, procedural body | `s4_procbody build <src> <dst> [--params body.json]` | skeleton edit, clip retarget, tube body; `shell` and `heaptint` make the corpse shell and its colours |
| 4 shape | `s4_hdbody design <src> W [--params] [--design]`, Blender `hdbody_bl.py -- model W`, `s4_hdbody shape W [--shape] [--paint]` | the design graph, skin modifier and subdivision, displacement and paint |
| 4 rigid radial part | `s4_hdcap design <src> W [--cap part.json] [--paint part_paint.json]`, Blender `hdcap_bl.py -- preview W` / `-- bake W` | a cap, shell or shield on its own bone, on the frame of the fitted part; `s4_hdcap shell` and `corpse` rebuild the corpse shell |
| 5 bake | Blender `hdbody_bl.py -- bake W [--size 2048] [--ao-samples 64]` | UVs packed into free texels, Cycles bakes, LOD meshes |
| 6 weights, build | `s4_hdbody weights W [--smooth 30] [--min-w 0.02]`, `s4_hdbody build <src> <dst> W [--params]` | models, textures spliced outside protected texels, clips, skeleton |
| 6 checks | `s5_stretch <overlay>`, `s4_smoothnormals <stage>`, `s4_lods <dir> <out>`, `s3_gutter <overlay> <skinned_lod0.npz>` | stretch per clip; normal smoothing; Blender decimate LODs; gutter fill |
| 7 material, glow | `s3_bodymat labels|apply|measure`, `s5_glow <overlay> <albedo> <relit> <part.png>` | body material by labels; the self-lit layer from the Fire Golem's object effect in your extraction |
| 8 clips | `s5_clipfit fit --mode <m> <stage> <frames> <out.json>`, `s5_clipfit write <stage> <clip.json>...`, `s5_limbfit fit|write|measure` | whole-pose fit over 8 facings; limb refit on a held trunk |
| 9 death and corpse | `s5_death <overlay> [--shell ...]`, `s5_corpsefit <skinned_lod0.npz> <frames> <camera.json> <pose.json>`, `s4_hdcorpse fit|settle|tex` | own death and dead clips; corpse pose fit, settle and corpse texels |
| 10 the mesh route | `s5_build <mesh_lods.npz> <albedo.png> <overlay>` | builds a stage from any mesh set (skinned by `hdkit.sprite3d.rig_fit`); not needed for the hand-built route |
| viewer loop | `g7_viewersheet <frames> <out.png> --stage <label>=<overlay> ...` | classic against one or more stages in every mode x 8 facings; then `python -m hdkit.view` for close-ups and turntables |
| art store | `python -m hdkit.monster3d.store put|fetch|where|check` | large stage files out of the work folder into the content-addressed store |
| sign-off | `python -m hdkit.signoff all <token>` | one game run through the backend (section 15) |

The camera file the fits take (`camera.json`: `{"views": {"<facing>": {"az": degrees}}}`) is optional: without one
(`clipfit.camera` unset, or `-` on the command line) the 8 facings are 45 degrees apart clockwise from "down", turned by
`clipfit.az0`. Fitted cameras of classic sprites stay within a few degrees of that ring.

The older routes (AI image-to-3D reconstruction and sprite projection, section 17) are not in the toolkit. TripoSG
(MIT) could feed `s5_build` with a generated mesh; its weights are yours to download and never go in git.

## 21. The monster config

What differs per monster lives in one JSON file. Every key is optional except `name` and `donor`; the defaults are in
`hdkit/monster3d/common.py` (`MONSTER_DEFAULTS`). [examples/monster3d/fungus/monster.json](../examples/monster3d/fungus/monster.json)
is the worked example's.

| Key | Meaning |
|-|-|
| `name` | file-safe name of the unit's files |
| `token` | the classic monster token (`data/global/monsters/<token>/`) of the reference frames; `act`: its palette |
| `unit_prefix`, `unit` | the unit JSON is `hd/character/enemy/<unit>.json` (default `m3d_<name>`) |
| `folder`, `vfx_particles`, `vfx_textures` | where the unit's files go (defaults under `.../m3d/<name>`) |
| `donor`, `profile` | the vanilla unit whose skeleton, state machine and clips are reused; the rig profile of `hdkit.sprite3d.rig_fit` |
| `K` | the upright mesh (height 1) in game units (mesh route) |
| `rigid_part`, `part_bone` | `[c, m]`: everything above the plane y = c + m z is one rigid part, bound to `part_bone` alone |
| `shell_bone` | the spare bone that carries the corpse shell |
| `keep` | donor material maps left as they are |
| `weapon_attach`, `weapon_bone`, `weapon_length` | an optional rigid weapon (`s5_build --weapon`) |
| `clipfit` | `template` (from `hdkit/monster3d/clipfit_templates.json`), `part_mask` (the part's colour test in classic's frames), `part_weight`, `edge_weight`, `shell_drop`, `loop`, `camera` or `az0`, `el` |
| `death` | `length`, `detach`, `land`, `dt_frames`, `dt_fps`, `dd_dy`, `keys`, `pivot_z`, `head_bone`, `cog_bone` |
| `corpse` | `dd_part_box` (the part's box in each DD facing), `timing` |
| `glow` | `source`: the object effect copied from your extraction (default the Fire Golem's) |
| `body` | `params` (overrides of the body parameters), `rect` (a free atlas rectangle), `corpse_paint` |

Machine settings (work folder, D2R extract, Blender) come from `hdkit.toml` `[monster3d]` or the `HDKIT_M3D_*`
environment variables (`hdkit/hdkit.example.toml`).

**Limits of the scripts today.** They assume a biped donor with a T-pose bind and the bone names of D2R's human
monsters (`mummy1` style), a corpse-shell bone, and (for `s4_hdcap` and the part terms of the fits) one rigid part on
its own bone. A monster without a rigid part runs through `s0_base`, `s4_procbody` and `s4_hdbody` (the example does);
other body types need their own template in `clipfit_templates.json` and checks of the bone lists in `s5_limbfit.py`
and `s5_death.py`.
