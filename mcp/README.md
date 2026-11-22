# MCP server: hdkit.view for agents

`hdkit.view` (the offline D2R HD viewer, [knowledge/hd-viewer.md](../knowledge/hd-viewer.md)) also runs as a local
[MCP](https://modelcontextprotocol.io) server, so an agent can render a model, a sheet or a map, look at the picture,
change something and render again, in one warm process (the GPU device and parsed files stay loaded).

The server is one file over the library, `hdkit/view/mcp_server.py`. It holds no rendering or measuring code of its own:
every tool builds the CLI's argument list and calls the CLI's function. `mcp/hdkit_view_server.py` is only the entry
point an MCP client starts.

## Set up

1. A venv with hdkit's packages and the MCP SDK (official Python SDK, MIT):

   ```bash
   python -m venv .venv-hd
   .venv-hd/bin/python -m pip install -r mcp/requirements.txt      # Windows: .venv-hd\Scripts\python
   ```

2. A config: copy `hdkit/hdkit.example.toml` to `hdkit.toml` and set at least `[game] dir` (a D2R folder whose CASC
   storage is read, read only; best a test copy), `[paths] work` (where renders go) and `[locks] globs` if you run test
   games (GPU jobs then wait while a game runs). CascLib must be built (`tools/build_casclib.ps1` on Windows, CMake on
   Linux, [docs/linux.md](../docs/linux.md)); set `[game] casclib` or `MODKIT_CASCLIB` if it is not in `build/CascLib`.

3. Tell your MCP client how to start it. For Claude Code, add this to the project's `.mcp.json` (paths are yours;
   `mcp.example.json` has the same block):

   ```json
   {
     "mcpServers": {
       "hdkit-view": {
         "command": "/path/to/.venv-hd/bin/python",
         "args": ["/path/to/d2rAI-toolkit/mcp/hdkit_view_server.py"],
         "env": {
           "HDKIT_CONFIG": "/path/to/hdkit.toml",
           "HDKIT_VIEW_ROOTS": "mod=/path/to/your-mod/out/data:casc"
         }
       }
     }
   }
   ```

   `HDKIT_VIEW_ROOTS` lists the default roots, first wins, separated by `:` on Linux and `;` on Windows: your built mod's
   data folder, then `casc` (the base game through the capped cache). A tool's own `roots` argument replaces them.

4. Check it: `python mcp/hdkit_view_server.py` must start and wait on stdin (Ctrl+C stops it). In the client, call
   `view_gate` and `view_camera`; neither needs the GPU.

## Tools

| Group | Tools | Returns |
|-|-|-|
| Look (read only) | `view_assets` (units, models, a unit's clips and parts), `view_resolve` (which root answers a game path), `view_camera` (the game camera's numbers), `view_gate` (may a GPU job run now), `view_rig_load` | data |
| Render (GPU, behind the gate) | `view_render` (one frame or a pass: lit, albedo, normals, ORM, glow, wire ...), `view_sheet` (modes x facings), `view_turntable`, `view_export_web` (a browser page of one unit) | PNG paths, one small PNG inline, metrics |
| Judge | `view_compare` (a render against a game capture: silhouette IoU, luma, colour), `view_signoff_sheet` (game, viewer and outlines side by side after an in-game sign-off) | data, PNG path |
| Maps | `map_list`, `map_info`, `map_cell`, `map_render` (classic, HD or both side by side, a whole map or a cell range) | data, PNG |
| Map working copy | `map_edit_init`, `map_edit` (ops `tile`, `obj-add` / `obj-move` / `obj-remove`, `ent-add` / `ent-move` / `ent-remove`, `show`, on a copy in the work folder) | data |
| Rigs | `view_rig_save` (save a light rig with changes) | path |

## Rules built in

- **stdio only.** No port, no listener, nothing reachable from the network.
- **Never writes game files.** Renders go to `<work>/view` (or `HDKIT_VIEW_OUT`); map edits go to a working copy under
  `<work>/mapview/work`, never into the game folder, the CASC cache, the extracts or the art store.
- **No game control.** Starting, driving and stopping a game stays with the testing tools (`testing/`) and
  `python -m hdkit.signoff`, under their locks.
- **The GPU gate.** Every GPU tool calls `hdkit.gpugate` first and answers `{"refused": true, "why": ...}` while a
  configured lock is held or the GPU or CPU is busy (`wait_s` waits up to that long). Never work around a refusal.
- **Large outputs** (16 MB and up, `HDKIT_MCP_STORE_MB`) move into the art store by content hash when one is configured.
- **Pictures by path.** Only one PNG of at most 256 KiB (`HDKIT_MCP_INLINE_KB`) is sent inline per call.

## Map editing for classic maps

This server covers the HD side and the join between classic and HD (render both, compare, a working copy of the .ds1
and its HD preset). A full classic map editor with its own MCP (painting cells, objects, NPC paths) is a separate tool;
the two meet at files.
