# Example: a 3D monster built against its classic sprite, on vanilla files only

The method of [docs/monster-sprite-to-3d.md](../../docs/monster-sprite-to-3d.md), run on D2R's own Dried Corpse: its
classic frames are the reference, its vanilla HD unit (`mummy1`) is the donor, and a new body replaces the donor's
mesh. Nothing here contains game or mod art. Every input file the steps read comes from your own extraction, and every
output goes to a work folder outside the repository.

## What is in this folder

| File | What |
|-|-|
| `vanilla.json` | the monster config of the example (name, classic token, donor, height, spare bones) |
| `run_example.py` | runs the steps in order (`--dry` prints them, `--blender` adds the designed body) |
| `fungus/monster.json` | the monster config of the guide's worked example, a mushroom-capped biped on the same donor |
| `fungus/design.json`, `shape.json`, `paint.json`, `params.json` | that example's body design graph, displacement, paint and body parameters |
| `fungus/part.json`, `part_paint.json` | its rigid radial part (the cap): profile and paint |

All of these are numbers only.

## What you need

- Python with `hdkit/requirements.txt`, including scikit-image and numba, and `wgpu` with a Vulkan, Metal or D3D12
  GPU for the review sheets.
- D2R's own files, extracted by you:
  - `HDKIT_M3D_D2R`: the `data` folder holding `hd/character/enemy/mummy1/...` (its models, skeleton, clips and
    textures) and `hd/character/enemy/mummy1.json`;
  - `HDKIT_CLASSIC`: a folder holding `data/global/monsters/mm` and `data/global/excel`;
  - `HDKIT_PALETTE_ROOT`: a folder holding `data/global/palette/act1/pal.dat`.

  Or set the same places in `hdkit.toml` (`[mod] d2r_extract`, `[mod] classic`, `[monster3d] d2r_data`; see
  `hdkit/hdkit.example.toml`).
- For `--blender`: Blender 4.5 LTS (`HDKIT_M3D_BLENDER` or `blender` on PATH). It runs as a separate program only.

## Run it

```
python examples/monster3d/run_example.py --dry                   # print the commands
python examples/monster3d/run_example.py --work ~/m3d-example    # frames, base stage, procedural body, sheet
python examples/monster3d/run_example.py --work ~/m3d-example --blender   # and the designed body (a Cycles bake: tens of minutes)
```

The steps (each is one `python -m hdkit.monster3d ...` command you can run alone):

1. `s1_frames classic`: every classic mode x 8 facings as PNGs, and `frames.json` with each mode's box and COF timing.
2. `s0_base`: a stage made from the donor alone: its models, textures (at 2048) and clips under the new unit's name.
3. `s4_procbody build`: the skeleton edited to longer arms and wider hips, every clip retargeted, and a procedural tube
   body painted into a free rectangle of the atlas.
4. `s5_stretch`: how far the skinned edges stretch in every clip.
5. `g7_viewersheet`: classic | base | procedural body at the same time in each mode, in the 8 facings.
6. With `--blender`: `s4_hdbody design`, `hdbody_bl.py model`, `s4_hdbody shape`, `hdbody_bl.py bake`, `s4_hdbody
   weights`, `s4_hdbody build` and a second sheet. This uses the worked example's design, shape and paint, so the
   Dried Corpse gets that example's body.

The example monster has no rigid part (`body.params.hd_cap` is false), so `s4_hdcap` is not run. For a monster with
one, see the guide's command table.
