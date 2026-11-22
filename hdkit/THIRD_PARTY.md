# Third-party code and tools in hdkit

This list covers `hdkit/` (the HD model viewer, its browser page, the CASC cache, the sign-off wrapper, the MCP server,
the map tools and the D2R format modules). Every entry is MIT-compatible. Add an entry whenever code from elsewhere is
ported or bundled. hdkit bundles no binary, no model weights and no game or mod art.

## Ported code (`view/`)

No third-party file is copied; these parts are written after the named sources, all MIT:

- Granny curve decoding (`view/curves.py`): ported from LSLib, Copyright (c) 2015 Norbyte, MIT,
  <https://github.com/Norbyte/lslib> @6f5f698 (`LSLib/Granny/Model/CurveData`).
- Pose and skinning rules (`view/gr2.py`): after Reimagined Level Editor, MIT, @3f76c75.
- COF layer order and blend rules (`view/classic.py`): after Reimagined D2R Mod Studio, MIT, @ea1a969 (`Cof.cs`).
- Cross-checks only, no code taken: aamt by Hellcommander (MIT, @a58bb5d, `Tools/DiabloII/GrannyAnim.py`),
  carbonenginejs tools-blender (MIT, @c97d43b, `carbon_gr2/curves.py`), rasen by rasenjs (MIT, @d2e115a).

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

## Outside tools, never bundled

Users download these themselves; hdkit only runs them as separate programs or reads their output.

| Tool | Used for | Licence | Where |
|-|-|-|-|
| realesrgan-ncnn-vulkan with the realesrgan-x4plus model | 4x upscale of map atlases and sprite frames | ncnn build MIT; Real-ESRGAN and its models BSD-3-Clause (checked upstream, 2026-11) | release page of github.com/xinntao/Real-ESRGAN-ncnn-vulkan |
| Blender (4.5 LTS) | headless bakes, decimation and renders for 3D models (docs/monster-sprite-to-3d.md) | GPL: run only as a separate process (`blender -b --python ...`), never imported or shipped | blender.org |
| TripoSG | image-to-3D shape, the researched and weaker route | MIT (code and weights) | github.com/VAST-AI-Research/TripoSG |

Researched and not usable: image-to-3D models whose licence excludes regions or commercial use cannot ship with a mod
and are not referenced by any code here. pymeshlab (GPL-3.0) is not used by hdkit.
