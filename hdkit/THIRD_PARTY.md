# Third-party code and tools in hdkit

This list covers `hdkit/` (the HD model viewer, its browser page, the CASC cache, the sign-off wrapper, the MCP server,
the map tools, the HD particle effects tools `fx/` and the D2R format modules). Every entry is MIT-compatible. Add an entry whenever code from elsewhere is
ported or bundled. hdkit bundles no binary, no model weights and no game or mod art.

## Ported code (`view/`)

No third-party file is copied; these parts are written after the named sources, all MIT:

- Granny curve decoding (`view/curves.py`): ported from LSLib, Copyright (c) 2015 Norbyte, MIT,
  <https://github.com/Norbyte/lslib> @6f5f698 (`LSLib/Granny/Model/CurveData`).
- Pose and skinning rules (`view/gr2.py`): after Reimagined Level Editor, MIT, @3f76c75.
- COF layer order and blend rules (`view/classic.py`): after Reimagined D2R Mod Studio, MIT, @ea1a969 (`Cof.cs`).
- Cross-checks only, no code taken: aamt by Hellcommander (MIT, @a58bb5d, `Tools/DiabloII/GrannyAnim.py`),
  carbonenginejs tools-blender (MIT, @c97d43b, `carbon_gr2/curves.py`), rasen by rasenjs (MIT, @d2e115a).

## Ported code (`view/assao.py`)

- Screen-space ambient occlusion: the game's own shaders (D2R 3.4.95032, RenderDoc captures decompiled with SPIRV-Cross,
  lane r5-postpass), which are Intel's ASSAO (Adaptive Screen Space Ambient Occlusion, Copyright (c) 2016 Intel
  Corporation, MIT, <https://github.com/GameTechDev/ASSAO>): the sample pattern, the PatternRotScaleMatrices formula and
  the pass structure follow it. No ASSAO source file was copied or downloaded; names follow ASSAO's.

## Ported code (`fx/schema.py`, `fx/pfile.py`, `fx/graph.py`)

- `.particles` object layout and the field-size schema of its 26 classes (`fx/schema.py`, `fx/pfile.py`): ported from
  Reimagined D2R Mod Studio, Copyright (c) 2026 Mod Studio contributors, MIT,
  <https://github.com/D2R-Reimagined/reimagined-d2r-mod-studio> @ade2a0c (`src/ModStudio.Core/ParticleFile.cs`, which
  credits Retera's PKBlaster, MIT). Its `ParticleEffectInfo.cs` and `ParticleEdits.cs` (same licence) are the model for the
  meaning of fields (curves 17/18/19, attributes, renderer property types) and for the curve and colour-constant edits in
  `fx/graph.py`; which fields are object/string references was then checked over D2R's whole `hd/vfx` corpus.

## Ported code (`fx/samplers.py`)

- Curves (cubic Hermite, segment-scaled tangents, limits, integral), shape sampling (box, sphere, ellipsoid, cylinder,
  capsule, cone, mesh), the bit-exact procedural turbulence (SFMT seeding, fp32 operation order, fast sin/asin, analytic and
  finite-difference curl, potential, fake flow) and `rand`/`vrand`: ported from AallynReed/PopcornFX-Web (MIT, Copyright (c)
  2026 Aallyn), <https://github.com/AallynReed/PopcornFX-Web> @d22b8fe64e595d432fad69facd016a98e3563a0e
  (`src/engine/curves.js`, `turbulence.js`, `sim.js`), vectorised with numpy. The tests' golden values come from running
  that project's own JS with node (`tests/data/hdfx_samplers_gen.mjs`); nothing of it is bundled.
- Field layouts of the D2R sampler objects: reimagined-d2r-mod-studio (MIT, `ParticleFile.cs`, `ParticleEffectInfo.cs`),
  plus own reading of the game's `.particles` and of the property tables in `D2R.exe` (analysis only).

## Ported code (`fx/`: schema subsets, CBVM, packers)

- Object field schema of the particle classes (`fx/routing.py`, until lane format's schema replaces it): from Reimagined
  D2R Mod Studio, MIT, `src/ModStudio.Core/ParticleFile.cs` (which credits Retera's PKBlaster, MIT).
- CBVM opcode layouts and operator names (`fx/cbvm.py`, `fx/vm.py`): read from D2R.exe (decoder table 0x2a05a30,
  debug name table 0x2a05ae8) with Ghidra; no code taken. Engine-call meanings modelled after PopcornFX's documented
  script builtins; PopcornFX-Web (MIT, AallynReed) read for reference only, no code taken.

- Billboard corner maths of every billboarding mode, atlas frame clamping, ribbon side vectors and the alpha sort rule
  (`fx/packers.py`, used by `fx/render.py` and `view/fx.wgsl`): ported (vectorised, rewritten for the orthographic camera)
  from PopcornFX-Web by AallynReed, Copyright (c) 2026 Aallyn, MIT, <https://github.com/AallynReed/PopcornFX-Web>
  (`src/render/renderer.js`, `src/render/packer.js`), which reads them from the PopcornFX runtime.
- The `.particles` field-size schema subset in `fx/rspec.py`: after Reimagined D2R Mod Studio, MIT
  (`ParticleFile.cs`, which credits Retera's PKBlaster, MIT), as ported by the hdfx research probe `survey.py`.
- Enum orders (billboarding modes, ribbon modes, Transparent.Type, Opaque.Type, Atlas.Blending): read from PopcornFX's
  `Editor.pkri` in wiselencave/war3-pfx-boilerplate (repository MIT; the bundled PopcornFX library is PopcornFX's own
  content, read only, nothing copied).

## Ported code (`fx/context.py` cast: seed, Charged Bolt path)

- `D2Seed` (SEED_RollRandomNumber / SEED_InitLowSeed), `chargedbolt_path` (PATH_ComputePathChargedBolt, its 32-entry turn
  table and gatDirectionToOffset), the bolt count and life cap of `cast()` (SKILLS_SrvDo017_ChargedBolt_BoltSentry,
  SKILLS_MissileInit_ChargedBolt): written after D2MOO, Copyright (c) 2020-2025 The Phrozen Keep community, MIT,
  <https://github.com/ThePhrozenKeep/D2MOO> master of 2026-11-25 (`source/D2Game/src/SKILLS/SkillSor.cpp`,
  `source/D2Common/src/Path/PathMisc.cpp`). The 8-way main direction is rounded from the angle (the game's own lookup
  table, sub_6FDAB6A0, is not ported).
- `fx/wallcast.py` (lane r4-firewall): Fire Wall's missile layout written after D2MOO (same licence and copyright),
  master ea666b5f of 2026-10-31: `SKILLS_SrvDo024_FireWall` (`source/D2Game/src/SKILLS/SkillSor.cpp`),
  `MISSMODE_SrvDo06_MoltenBoulder_FireWallMaker` (`MISSILES/MissMode.cpp`), the range in
  `MISSILES_CreateMissileFromParams` (`MISSILES/Missiles.cpp`).
- `fx/firemakers.py` (lane r4-firemakers): open-ground fire layouts written after the same D2MOO revision and licence,
  using `SKILLS_SrvDo023_Blaze_EnergyShield_SpiderLay` and `SKILLS_CreateBlazeMissile` (`SkillSor.cpp`),
  `MISSMODE_SrvHit48_MoltenBoulderEmerge`, `MISSMODE_SrvDo06_MoltenBoulder_FireWallMaker`,
  `MISSMODE_SrvHit09_ImmolationArrow`, and `MISSMODE_CreateImmolationArrowHitSubmissiles` (`MissMode.cpp`).
  The `dmXY` skill formula follows `D2Common_11033` (`source/D2Common/src/D2Skills.cpp`).

## Ported code (`view/fx.wgsl` water)

- The 2D simplex noise of `fs_water` (as the game's `ps_water_p` inlines it): Ashima Arts' webgl-noise by Ian McEwan
  and Stefan Gustavson, Copyright (c) 2011 Ashima Arts and Stefan Gustavson, MIT,
  <https://github.com/ashima/webgl-noise>; written from the game shader's disassembly, which follows it.

## Ported code (`formats/`)

- BitKnit2 decompression in `formats/d2r_model.py`: a pure-Python port of the algorithm as documented in eiz/pybg3 and
  libbg3 (MIT), cross-checked against the file CRCs. The GR2 container reader and writer are our own, from reading the
  game's files.
- Everything else in `formats/` and `maps/` (texture, model index, DCC, COF, particles, DT1/DS1 readers and writers,
  automap rows) is our own, written from reading the game's files.

## Libraries used at run time, not bundled

- CascLib by Ladislav Zezula (MIT), <https://github.com/ladislav-zezula/CascLib>: reads the game's CASC storage
  for `view/casc.py`, through modkit's wrapper (`modkit/casc.py`, the `third_party/CascLib` submodule built locally).
  hdkit ships no CascLib binary.
- imageio-ffmpeg (BSD-2-Clause wrapper) for MP4 output in `view/sheets.py`. The ffmpeg binary it fetches is a GPL
  build: a local tool in the user's own venv, never shipped with hdkit.
- numpy (BSD-3-Clause), Pillow (MIT-CMU), scipy (BSD-3-Clause), wgpu-py (BSD-2-Clause): ordinary pip dependencies
  (`hdkit/requirements.txt`).
- The MCP Python SDK `mcp` (MIT): only for the MCP server (`mcp/requirements.txt`).

## Analysis tools, never shipped

- The game shaders were read with Microsoft's `D3DDisassemble` (d3dcompiler_47.dll, a Windows system DLL) called
  through ctypes; no binary is bundled. The colour path in `view/tone.py` (`lut_chain`, `value_curve`, `bloom`) is
  written from that disassembly. Its constants are the game's. The ACES-style matrices (sRGB/AP1/BT.2020, gamut
  expansion) are the standard published values that Unreal Engine 4's tone path also uses; no UE4 code was taken.
- capstone 5.0.7 (BSD-3-Clause, <https://github.com/capstone-engine/capstone>) was used in a local venv to search the
  game executable for where the constants are filled. It is not an hdkit dependency.

- The PopcornFX particle shaders (`fx/shad.py` reads their `SHAD` containers; notes in `docs/hdfx-shaders.md`) were
  disassembled with vkd3d-shader 2.2 (`vkd3d-compiler`, LGPL-2.1, <https://dl.winehq.org/vkd3d/source/>) and
  SPIRV-Cross (Apache-2.0, <https://github.com/KhronosGroup/SPIRV-Cross>), built and run locally as outside programs;
  no binary is bundled and no code was taken. The `SHAD` layout is our own reading of the files.
- RenderDoc 1.47 by Baldur Karlsson (MIT, <https://renderdoc.org>, official Linux release tarball, sha256
  200361c2761da1ca77a2acfe0c37e8db5839ba7cb9f204e8394d66dad7e6336a) for the calibration captures of `fx/calib/`:
  its Vulkan capture layer loaded into a test game through VK_LAYER_PATH (env only, nothing registered), and its
  embedded Python (`qrenderdoc --python`) runs `fx/calib/rd_trigger.py` / `rd_extract.py`. Unpacked by the user
  (`HDKIT_RENDERDOC` names it); no RenderDoc file is copied into hdkit.

## Script language (`fx/lang/`)

- The `.hfx` syntax follows PopcornFX's own particle script language (names `self.*`, `effect.*`, `select`, `.xyz1`
  swizzles), as read in PopcornFX-Web (MIT, <https://github.com/AallynReed/PopcornFX-Web>, `src/engine/script.js`).
  No code was taken. The CBVM tables (mop sub-ops, swizzle encoding, mfn3) were read from D2R.exe; `fns_data.py` is
  generated from the game's own `.particles` files (names and signatures of the engine calls).

## Script noise (`fx/noise.py`, `fx/noise_data.py`)

- `noise_float3`, `noise_float` and `fast.noise_float` are ports of D2R.exe's SSE kernels (0xc623e0, 0xc616a0, 0xb3d5b0),
  written from the disassembly; their tables (permutation, simplex corner offsets) are dumped from the decrypted image by
  `python -m hdkit.fx.noise_dump`. The maths is Ken Perlin's improved gradient set and Stefan Gustavson's simplex noise
  (public domain, "Simplex noise demystified"); no code was taken. The `fast.noise` table is drawn with the SFMT
  generator already ported in `fx/samplers.py`.
- Verification only: unicorn 2.1.4 (GPL-2.0, <https://github.com/unicorn-engine/unicorn>) ran the kernels' machine code in a
  local venv to produce the pinned test values; it is not an hdkit dependency and nothing of it is shipped.

## Outside tools, never bundled

Users download these themselves; hdkit only runs them as separate programs or reads their output.

| Tool | Used for | Licence | Where |
|-|-|-|-|
| realesrgan-ncnn-vulkan with the realesrgan-x4plus model | 4x upscale of map atlases and sprite frames | ncnn build MIT; Real-ESRGAN and its models BSD-3-Clause (checked upstream, 2026-11) | release page of github.com/xinntao/Real-ESRGAN-ncnn-vulkan |
| Blender (4.5 LTS) | headless bakes, decimation and renders for 3D models (docs/monster-sprite-to-3d.md) | GPL: run only as a separate process (`blender -b --python ...`), never imported or shipped | blender.org |
| TripoSG | image-to-3D shape, the researched and weaker route | MIT (code and weights) | github.com/VAST-AI-Research/TripoSG |

Researched and not usable: image-to-3D models whose licence excludes regions or commercial use cannot ship with a mod
and are not referenced by any code here. pymeshlab (GPL-3.0) is not used by hdkit.
