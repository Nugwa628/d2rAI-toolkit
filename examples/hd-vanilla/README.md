# Example: the HD viewer and map viewer on vanilla files

A short run of `hdkit` on the base game only: one vanilla monster in the viewer, and one vanilla map in the map
viewer. Nothing here is game art: the script reads your own copy of the game and writes its pictures to your work
folder, outside the repository.

## What you need

- A venv with `hdkit/requirements.txt` (numpy, Pillow, scipy, wgpu). It needs a GPU with Vulkan, Metal or D3D12.
- The base game files, either way:
  - **CASC** (the default): `[game] dir` in your config names a D2R folder (best a test copy, see
    [testing/README.md](../../testing/README.md)), and CascLib is built (`tools/build_casclib.ps1`, or CMake on Linux).
    Files are read on demand into a capped cache under the work folder.
  - **An extracted folder**: pass `--root base=<folder that holds data/hd/...>`.
- D2R's tables and tiles for the map steps: `[mod] d2r_extract`, or modkit's cache (`python -m modkit fetch`).

## Run it

```bash
cp examples/hd-vanilla/hdkit.toml ~/hdkit-example.toml      # then edit it
export HDKIT_CONFIG=~/hdkit-example.toml                     # Windows: set HDKIT_CONFIG=...
python examples/hd-vanilla/run_example.py --dry              # print the commands
python examples/hd-vanilla/run_example.py                    # run them
```

The steps (each is one `python -m hdkit.view ...` command you can run alone):

| Step | Command | Output |
|-|-|-|
| GPU gate | `gate` | JSON: free or busy, and why |
| Camera | `camera` | the game camera's numbers |
| Clips | `ls clips hd/character/enemy/mummy1.json` | the unit's clips, modes and parts |
| One frame | `render ... --mode wl --facing 2` | PNG + metrics.json |
| Sheet | `sheet ... --modes nu,wl,a1 --facings 8` | modes x 8 facings in one PNG |
| Turntable | `turntable ... --frames 16` | sheet, GIF/APNG |
| Map facts | `map info act1/cathedrl/cathy3 --source d2r` | size, act, level types, tile files |
| Classic map | `map classic act1/cathedrl/cathy3 --source d2r` | the map drawn from its .ds1 and .dt1 |
| Classic and HD | `map sheet act1/cathedrl/cathy3 --source d2r` | classic and HD side by side (needs the HD preset from CASC) |

Then try the browser page (`python -m hdkit.view export-web ...` and `serve-web`), the map page
(`python -m hdkit.view map serve`) and the same commands through the MCP server ([mcp/README.md](../../mcp/README.md)).

Pick any other vanilla unit with `--monster` (`python -m hdkit.view ls units` lists them) and any vanilla map with
`--map` (`python -m hdkit.view map ls --source d2r`).

## Where to go next

- [knowledge/hd-viewer.md](../../knowledge/hd-viewer.md): what the viewer can and cannot judge.
- [knowledge/hd-maps.md](../../knowledge/hd-maps.md): classic maps in HD, upscaling, light, the minimap.
- [docs/monster-sprite-to-3d.md](../../docs/monster-sprite-to-3d.md): a classic monster sprite as a 3D model.
- The `d2r-hd` skill (`.claude/skills/d2r-hd/SKILL.md`).
