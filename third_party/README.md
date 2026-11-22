# Third-party code, libraries and tools

Every licence the toolkit depends on, in one place. The toolkit itself is MIT ([LICENSE](../LICENSE)). It bundles no
binary, no AI model weights and no game or mod art. Add a row whenever code from elsewhere is ported, bundled or run.
Licences were checked upstream on 2026-11-22.

## Submodules in this folder

| Folder | Project | Licence | Used for |
|-|-|-|-|
| `CascLib/` | CascLib by Ladislav Zezula, <https://github.com/ladislav-zezula/CascLib> | MIT | reading the game's CASC storage (`modkit/casc.py`, `hdkit/view/casc.py`); built locally, never shipped as a binary |
| `PluginSDK/` | D2RLoader PluginSDK, <https://github.com/D2RLoader/PluginSDK> | MIT | the plugin template (`plugin-template/`) |

## Code written after other projects (no file copied)

Details per file are in [hdkit/THIRD_PARTY.md](../hdkit/THIRD_PARTY.md).

| Where | After | Licence |
|-|-|-|
| `hdkit/view/curves.py` | LSLib (Norbyte), Granny curve decoding | MIT |
| `hdkit/view/gr2.py` | Reimagined Level Editor, pose and skinning rules | MIT |
| `hdkit/view/classic.py` | Reimagined D2R Mod Studio, COF layer order | MIT |
| `hdkit/formats/d2r_model.py` | pybg3 / libbg3, BitKnit2 decompression as documented | MIT |

Everything else (modkit, hdkit including `hdkit/monster3d/` and `hdkit/sprite3d/`, testing, release, the downloader) is
our own code.

## Python libraries used at run time (pip, never bundled)

| Library | Licence | Needed by |
|-|-|-|
| numpy | BSD-3-Clause | hdkit |
| scipy | BSD-3-Clause | hdkit |
| Pillow | MIT-CMU (HPND) | hdkit |
| wgpu-py | BSD-2-Clause | `hdkit.view` GPU renders |
| scikit-image | BSD-3-Clause | `hdkit.monster3d` clip and corpse fits (convex hulls) |
| numba | BSD-2-Clause | `hdkit.sprite3d.render_soft` (the no-GPU renderer the fits use) |
| mcp (Model Context Protocol SDK) | MIT | the MCP server only (`mcp/`) |
| imageio-ffmpeg | BSD-2-Clause | MP4 sheets only; the ffmpeg binary it fetches is a GPL build: local only, never shipped |

The standard library covers modkit, testing, release and the downloader.

## Outside programs (you download them; the toolkit only runs them as separate processes)

| Program | Licence | Used for | Notes |
|-|-|-|-|
| Blender 4.5 LTS, <https://www.blender.org> | GPL-2.0-or-later | `hdkit.monster3d` modelling, Cycles bakes and LODs (`blender -b --python hdkit/monster3d/blender/...`) | GPL: never imported into the toolkit's Python, never bundled. The `bpy` scripts in `hdkit/monster3d/blender/` are our own (MIT) and run inside your Blender. |
| realesrgan-ncnn-vulkan, <https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan> | MIT (the ncnn build) | optional 4x upscale of classic frames and map atlases | its models (realesrgan-x4plus) are BSD-3-Clause; download them yourself |
| TripoSG, <https://github.com/VAST-AI-Research/TripoSG> | MIT (code and weights) | not used by any shipped code; the image-to-3D route that was researched and found weaker than hand building | weights never go in git; see docs/monster-sprite-to-3d.md if you want to try it |
| Ghidra, <https://ghidra-sre.org> | Apache-2.0 | reverse engineering (`tools/ghidra_scripts/`, `tools/ghidra_*.ps1`); run by you, never bundled | |

## Left out on purpose

- **Hunyuan3D-2mv** (Tencent Hunyuan Community License): the licence excludes the European Union, the United Kingdom and
  South Korea. The toolkit has no code for it.
- **pymeshlab** (GPL-3.0): used by an older reconstruction route that is not in the toolkit.
- **AI model weights** of any kind: never in git. Where a route can use one, the docs say where to download it.
