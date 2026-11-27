# hdfx MCP tools and JSON endpoints (`fx_*`)

Lane `mcp` of the hdfx work (interfaces: `docs/hdfx.md` section 5). The tools read, edit, author, draw and export D2R's HD
particle effects (`.particles`, PopcornFX). One implementation, three front doors:

| Door | Where | Used by |
|-|-|-|
| `hdkit.fx.api` | `hdkit/fx/api.py`: plain Python, JSON in, JSON out, no `mcp` import | tests, scripts, the other two |
| MCP tools `fx_*` | `hdkit/view/mcp_server.py` (stdio: `python -m hdkit.view.mcp_server`, or `mcp/hdkit_view_server.py`; mcp/README.md) | Claude and other agents |
| JSON endpoints | `hdkit/view/webserve.py` (`serve-web --fx`, 127.0.0.1 only) | the web editor |

Each tool is `api.<name without fx_>` (`fx_edit` -> `api.edit`) and `api.call(name, **args)` is the dispatcher that
turns an exception into the error shape below. The tools exist before every lane has landed: a part whose lane is not
merged answers `not_implemented` with `error.needs` naming the lane, and starts working when that lane's module exists
(`hdkit.fx.graph`/`pfile` = lane format, `sim` = lane vm, `render` = lane render, `context` = lane context, `lang` = lane
lang, `coverage` = lane survey). Until then the tools run on `hdkit.formats.hd_particles` and `hdkit.fx.cbvm`.

## Conventions

- **Paths.** `path` is any of: a logical game path (`hd/vfx/particles/...particles`, with or without a leading `data/`,
  any case, resolved over `roots`, first root wins); a file on disk; or an edit session folder (see Sessions). Answers
  carry both: `path` (logical, when known) and `file` (on disk). Files are always paths, never bytes; one PNG of at most
  `HDKIT_MCP_INLINE_KB` (256) KiB is also attached inline by `fx_render` over MCP.
- **Roots.** `roots`: `["name=folder", ...]`, first wins; default `HDKIT_VIEW_ROOTS` (os.pathsep separated). Example:
  `d2r=~/d2r-extract/data` (a folder that holds `hd/...`).
- **Output folder.** `out` of each tool; default `<root>/<tool>/<name>_<hash of the arguments>` with
  `<root>` = `$HDKIT_FX_OUT`, else `$HDKIT_VIEW_OUT`, else `./hdview-out`; edit sessions use
  `<root>/edit/<name>_<hash of the source path>` so the same effect always lands in the same working copy.
- **Answers.** `{"ok": true, ...}`. Errors: `{"ok": false, "error": {"code", "message", ...}}` with `code` one of
  `bad_args`, `not_found`, `parse_error`, `edit_failed` (`op_index` says which op; nothing was changed),
  `validation_failed`, `not_implemented` (`needs`: `"lane format"`), `refused` (GPU gate: see `fx_render`). Over MCP the
  same dict is the tool result (never a transport error), so an agent can read `code` and `needs`.
- **Script ids.** A script is a `CCompilerBlobCache` object, named by its object number **as the listing prints it
  (1-based, `; ... object 42`)**; `fx_inspect` and `fx_script_read` list them.
- **Renderer selectors** (`sel`): an index into `fx_inspect`'s `renderers` (int), `"*"`, a kind (`"mesh"`, `"billboard"`,
  `"ribbon"`, `"light"`), a texture/mesh path fragment, several of those joined by commas, or `"layer:<name>"` (needs lane
  format).
- **Sessions.** An edit works on a *working copy* per effect, so the game's file is never touched and every step can be
  undone. `fx_edit`, `fx_create` and `fx_script_write` take `out` (the session folder) and return a `session` block;
  `fx_session` undoes, redoes, lists and resets. Folder layout: `session.json` (source, logical path, cursor, history),
  `<name>.particles` (the current working copy), `history/v0000.particles`... (every version). Passing the session folder
  (or the working file inside it) as `path` to any tool works on the working copy.

```json
{"session": {"out": "hdview-out/edit/vfx_mephisto_ambient_3fa91c02", "work": ".../vfx_mephisto_ambient.particles",
             "source": "hd/vfx/particles/character/enemy/mephisto/vfx_mephisto_ambient.particles",
             "version": 2, "can_undo": true, "can_redo": false}}
```

## fx_list: list and search effects

Walks the roots for `.particles`. `pattern` is a substring or `fnmatch` glob on the logical path (case-insensitive);
`texture`, `material` and `kind` look into the files (substring of a texture/mesh path, of a material path, a renderer
kind). `info` adds renderer kinds and materials per hit.

```json input
{"type": "object", "properties": {
  "pattern": {"type": ["string", "null"], "default": null, "description": "substring or glob on the logical path"},
  "roots": {"type": ["array", "null"], "items": {"type": "string"}},
  "texture": {"type": ["string", "null"], "default": null},
  "material": {"type": ["string", "null"], "default": null},
  "kind": {"type": ["string", "null"], "enum": ["billboard", "ribbon", "mesh", "light", null], "default": null},
  "limit": {"type": "integer", "default": 200},
  "info": {"type": "boolean", "default": false}}}
```
```json output
{"type": "object", "required": ["ok", "count", "items"], "properties": {
  "ok": {"type": "boolean"}, "count": {"type": "integer", "description": "all hits"},
  "truncated": {"type": "boolean"},
  "items": {"type": "array", "items": {"type": "object", "required": ["path", "root", "file", "size"], "properties": {
    "path": {"type": "string"}, "root": {"type": "string"}, "file": {"type": "string"}, "size": {"type": "integer"},
    "kinds": {"type": "object", "description": "info: renderer kind -> count"},
    "materials": {"type": "array", "items": {"type": "string"}}}}}}}
```
Example: `fx_list(pattern="mephisto*", kind="ribbon", info=true)` ->
`{"ok": true, "count": 1, "items": [{"path": "hd/vfx/particles/character/enemy/mephisto/vfx_mephisto_ambient.particles",
"root": "d2r", "file": ".../vfx_mephisto_ambient.particles", "size": 187452, "kinds": {"billboard": 9, "ribbon": 2},
"materials": ["vfx/materials/Diablo_Uber.pkma"]}]}`

## fx_inspect: what an effect is made of

Renderers with their switches and files, the scripts (what each reads and writes, the engine functions it calls), the
object classes, textures. With lane format landed it also returns the effect graph (`graph`: `Effect.to_json()` of
`docs/hdfx.md` section 1: layers, attributes, samplers, events); without it `graph` is `null` and `engine.graph` false.

```json input
{"type": "object", "required": ["path"], "properties": {
  "path": {"type": "string"}, "roots": {"type": ["array", "null"], "items": {"type": "string"}},
  "graph": {"type": "boolean", "default": true, "description": "include the effect graph when available"}}}
```
```json output
{"type": "object", "required": ["ok", "path", "file", "objects", "renderers", "scripts"], "properties": {
  "ok": {"type": "boolean"}, "path": {"type": ["string", "null"]}, "file": {"type": "string"},
  "root": {"type": ["string", "null"]}, "size": {"type": "integer"}, "version": {"type": "string"},
  "objects": {"type": "integer"}, "strings": {"type": "integer"},
  "classes": {"type": "object", "description": "class name -> object count"},
  "renderers": {"type": "array", "items": {"type": "object", "properties": {
    "index": {"type": "integer"}, "object": {"type": "integer", "description": "0-based object number"},
    "kind": {"type": "string"}, "material": {"type": "string"}, "enabled": {"type": "boolean"},
    "props": {"type": "object", "description": "name -> {type: bool|int|float|texture|mesh|?, value, file}"},
    "files": {"type": "array", "items": {"type": "string"}}}}},
  "scripts": {"type": "array", "items": {"type": "object", "properties": {
    "script": {"type": "integer", "description": "1-based object number"}, "backend": {"type": "string"},
    "kind": {"type": ["integer", "null"]}, "instructions": {"type": "integer"}, "constants": {"type": "integer"},
    "externals": {"type": "array", "items": {"type": "string"}},
    "engine_calls": {"type": "array", "items": {"type": "string"}}}}},
  "textures": {"type": "array", "items": {"type": "string"}},
  "colour_streams": {"type": "array", "items": {"type": "string"}, "description": "float4 colour streams the scripts store from a grey level: the `stream` names a `color` edit op can tint"},
  "graph": {"type": ["object", "null"]}, "engine": {"type": "object", "description": "which lanes are present"}}}
```
Example: `fx_inspect(path="hd/vfx/particles/character/enemy/mephisto/vfx_mephisto_ambient.particles")` ->
`{"ok": true, "objects": 411, "renderers": [{"index": 0, "kind": "billboard", "material": "vfx/materials/Diablo_Uber.pkma",
"enabled": true, "props": {"GradientRemap.GradientMap": {"type": "texture", "file": "data/hd/vfx/textures/gradient/..."}}}],
"scripts": [{"script": 14, "backend": "CPU/CPU_VM", "instructions": 83, "externals": ["Position", "Color", "Life"],
"engine_calls": ["rand", "curve_sample"]}], "graph": null, "engine": {"graph": false, "sim": false, "render": false}}`

## fx_edit: apply edit ops to the working copy

Ops are the shared list of `docs/hdfx.md` section 1; the details of each payload are below. All-or-nothing: if op *i*
fails, nothing changes and `error.op_index` = *i*. On success the new version is saved (undo with `fx_session`) and
`validate` (default on) runs `fx_validate` on it.

| op | payload | does |
|-|-|-|
| `prop` | `renderer: sel, name, value` | a float/int/bool renderer property (`Transparent.CameraSortOffset`) |
| `texture` | `renderer: sel, name, path` or `replace: {old: new}` | point a texture/mesh property at `path` (any length), or rename a path everywhere |
| `enable` | `renderer: sel, on` | the `EnableRendering` switch (layer simulates, draws or not) |
| `color` | `stream, rgba` | tint every script that stores the float4 stream `stream` (multiply by `rgba`; `hd_cbvm.tint`) |
| `const` | `script, index, values, kind: "f"\|"i"` | rewrite constant `index` (1-8 values, repeated to fill the lanes) of script `script` |
| `script` | `script, text, language: "auto"\|"cbvm"\|"hfx"` | replace a whole script from a CBVM listing or `.hfx` source |
| `curve` | `sampler, times, values, tangents_in?, tangents_out?` | set a sampler curve (needs lane format) |
| `copy_layer` | `from: path, layer, as` | compose: copy a layer with its scripts/renderers from another effect (needs lane format) |
| `remove_layer` | `layer` | drop a layer (needs lane format) |

```json input
{"type": "object", "required": ["path", "ops"], "properties": {
  "path": {"type": "string"},
  "ops": {"type": "array", "items": {"type": "object", "required": ["op"], "properties": {
    "op": {"enum": ["prop", "texture", "enable", "color", "const", "script", "curve", "copy_layer", "remove_layer"]}}}},
  "out": {"type": ["string", "null"], "default": null, "description": "session folder (default by the source path)"},
  "roots": {"type": ["array", "null"], "items": {"type": "string"}},
  "validate": {"type": "boolean", "default": true}}}
```
```json output
{"type": "object", "required": ["ok", "session", "applied"], "properties": {
  "ok": {"type": "boolean"}, "session": {"$ref": "#/session"},
  "applied": {"type": "array", "items": {"type": "object", "properties": {
    "op": {"type": "string"}, "changed": {"type": "integer", "description": "renderers/scripts/stores touched"},
    "note": {"type": "string"}}}},
  "validate": {"type": ["object", "null"], "description": "fx_validate's answer for the new working copy"}}}
```
Example: `fx_edit(path="hd/vfx/particles/.../vfx_mephisto_ambient.particles", ops=[{"op": "color", "stream": "Color",
"rgba": [0.4, 1, 0.5, 1]}, {"op": "enable", "renderer": "ribbon", "on": false}])` -> `{"ok": true, "session": {"version": 1,
...}, "applied": [{"op": "color", "changed": 3}, {"op": "enable", "changed": 2}], "validate": {"ok": true, ...}}`.

## fx_session: undo, redo, history, reset

```json input
{"type": "object", "required": ["out"], "properties": {
  "out": {"type": "string", "description": "the session folder (or the working file or source path)"},
  "action": {"enum": ["status", "history", "undo", "redo", "reset", "goto"], "default": "status"},
  "version": {"type": ["integer", "null"], "default": null, "description": "goto: the version"}}}
```
```json output
{"type": "object", "required": ["ok", "session"], "properties": {"ok": {"type": "boolean"}, "session": {"$ref": "#/session"},
  "history": {"type": "array", "items": {"type": "object", "properties": {"version": {"type": "integer"},
  "label": {"type": "string"}, "ops": {"type": "array"}, "size": {"type": "integer"}, "current": {"type": "boolean"}}}}}}
```
`undo`/`redo` move the cursor (the working file becomes that version); a new edit after an undo drops the redo versions.
`reset` goes back to version 0 (the source as opened), keeping the history.

## fx_validate: is this file sound

Checks (each in `checks`): `parse` (the object file reads, string table ends the file), `roundtrip` (decode and re-encode
is byte-identical), `scripts` (each script's listing assembles back to the same bytes; externals named; constants in
range), `renderers` (a texture/mesh property without a file, or every renderer switched off, is a warning: D2R's own files leave slots empty), `files` (every referenced texture/mesh
resolves in the roots, or is listed as `warnings`), `graph` (graph invariants when lane format is present).

```json input
{"type": "object", "required": ["path"], "properties": {"path": {"type": "string"},
  "roots": {"type": ["array", "null"], "items": {"type": "string"}},
  "files": {"type": "boolean", "default": true, "description": "check that referenced files resolve"}}}
```
```json output
{"type": "object", "required": ["ok", "errors", "warnings", "checks"], "properties": {
  "ok": {"type": "boolean", "description": "no errors"},
  "errors": {"type": "array", "items": {"type": "object", "properties": {"check": {"type": "string"},
    "message": {"type": "string"}, "where": {"type": "string"}}}},
  "warnings": {"type": "array", "items": {"type": "object"}},
  "checks": {"type": "object", "description": "check name -> true | false | null (skipped)"}}}
```

## fx_create: a new effect from a template or from layers

`template`: a path of an effect to start from (copied; `textures` `{old: new}` renames paths at once). `layers`: compose
from several effects: `[{"from": path, "layer": name, "as": name}, ...]` (needs lane format). With neither, an empty
effect scaffold (needs lane format). The result is a session whose version 0 is the new effect; `logical` is where
`fx_export` will put it (default `hd/vfx/particles/custom/<name>/<name>.particles`).

```json input
{"type": "object", "properties": {
  "template": {"type": ["string", "null"], "default": null},
  "layers": {"type": ["array", "null"], "items": {"type": "object", "required": ["from", "layer"], "properties": {
    "from": {"type": "string"}, "layer": {"type": "string"}, "as": {"type": "string"}}}},
  "name": {"type": ["string", "null"], "default": null, "description": "default: the template's name"},
  "textures": {"type": ["object", "null"], "additionalProperties": {"type": "string"}},
  "logical": {"type": ["string", "null"], "default": null},
  "out": {"type": ["string", "null"], "default": null},
  "roots": {"type": ["array", "null"], "items": {"type": "string"}}}}
```
```json output
{"type": "object", "required": ["ok", "session"], "properties": {"ok": {"type": "boolean"},
  "session": {"$ref": "#/session"}, "logical": {"type": "string"}, "layers": {"type": "array", "items": {"type": "string"}}}}
```

## fx_script_read and fx_script_write

Without `script`, `fx_script_read` lists the scripts (as `fx_inspect` does, plus the first lines). With `script` it
returns the text: `language` `cbvm` (the `hdkit.fx.cbvm` listing: header directives, constants, instructions with
notes) or `hfx` (decompiled readable source of lane lang; needs it). `fx_script_write` replaces one script from text
(`language` `auto` = `hfx` when the text has no `.backend` directive and lane lang is present, else `cbvm`), writes a new
version of the working copy and returns the new instruction count; the text is parsed and checked before anything is
saved (a parse error leaves the working copy as it was).

```json input
{"type": "object", "required": ["path"], "properties": {"path": {"type": "string"},
  "script": {"type": ["integer", "null"], "default": null, "description": "1-based object number"},
  "language": {"enum": ["cbvm", "hfx"], "default": "cbvm"},
  "roots": {"type": ["array", "null"], "items": {"type": "string"}}}}
```
```json output
{"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"},
  "scripts": {"type": "array", "description": "when `script` is null: as fx_inspect's scripts"},
  "script": {"type": "integer"}, "language": {"type": "string"}, "text": {"type": "string"},
  "instructions": {"type": "integer"}, "externals": {"type": "array", "items": {"type": "string"}}}}
```
Write:
```json input
{"type": "object", "required": ["path", "script", "source"], "properties": {"path": {"type": "string"},
  "script": {"type": "integer"}, "source": {"type": "string", "description": "the listing or .hfx text"},
  "language": {"enum": ["auto", "cbvm", "hfx"], "default": "auto"},
  "out": {"type": ["string", "null"], "default": null}, "roots": {"type": ["array", "null"], "items": {"type": "string"}}}}
```
```json output
{"type": "object", "required": ["ok", "session", "script"], "properties": {"ok": {"type": "boolean"},
  "session": {"$ref": "#/session"}, "script": {"type": "integer"}, "language": {"type": "string"},
  "instructions": {"type": "integer"}, "validate": {"type": "object"}}}
```
Example: read script 14, change the constant in line `.const 3 f 0.5 ...` to `0.8`, `fx_script_write(path, 14, text)`.

## fx_render: still, clip, in context

Draws the working copy (or any effect) with the simulator and renderer: `sim.Sim` steps the effect, `render.FxRenderer`
draws it into the viewer's pass. GPU: the call passes `hdkit.gpugate` first and answers `{"ok": false, "refused": true,
"why": ..., "gate": {...}}` while a game-slot run holds its lock or the GPU/CPU is busy (`wait_s` waits that long). A still
is drawn at the first simulation frame (steps of 1/25 s, or 1/`fps` of a clip) at or after `t`. Needs lanes format, vm and
render (`not_implemented` until they land); the context lane has landed.

- `t`: seconds for a still. `clip`: `{"t0": 0, "t1": 3, "fps": 25}` for an animation; `format` `png` (a still, or the frames
  as numbered PNGs in a folder when `clip` is set), `gif`, `apng` or `mp4` (mp4 needs imageio-ffmpeg in the Python).
- `cam`: `{"preset": "orbit"|"game", "az": deg, "el": deg, "dist": m, "target": [x, y, z], "ortho": bool}`; default orbit
  around the effect's bounds, or the game camera when a `context` puts the effect on a unit or a map.
- `context`: `null` (effect alone at the origin) or one of (`fx/context.py` drivers play the effect and the children they
  spawn, such as an explosion at a missile's impact; `unit` = a unit JSON name or logical path):
  `{"kind": "unit", "unit": name, "clip": name|null, "t0": s, "rate": 0, "yaw": deg, "skill": name|null}` (r3 integration:
  lane r3-shapes' bone binding, `api.unit_bone_driver`: one Sim per binding the unit has for the effect, each on its
  binding's bone of the unit's clip at clip time t0 + t x rate, default neutral and rate 0 = the pose the body is drawn
  in; `bad_args` when the unit does not bind the effect),
  `{"kind": "bone", "unit": name, "bone": name, "yaw": deg}` (the effect follows the bone; rest pose for now),
  `{"kind": "missile", "path": [[x,y,z],...] | "start", "end", "speed": units/s | "name": missiles.txt name + "tables":
  <excel folder>, "events": {"hit": path, "explode": path}, "arc": h}`,
  `{"kind": "skill", "unit": name, "cast_frame": n, "mode": "sc", "as": "overlay"|"missile", "target": [x,y,z], "bone": name}`
  (the effect plays as that part of the cast) or the same with `"name"` + `"tables"` (the skill's own effects from
  skills.txt), `{"kind": "scene", "map": name, "xy": [cell x, cell y], "offset": [x,y,z], "yaw": deg}` (the effect stands on
  the map spot with the preset's game light, bloom and tonemap). Units are game metres, y up.
- `attrs`: effect attributes (inputs the game sets: `{"MonsterFade": 1.0}`); `seed`: simulation seed (renders are
  deterministic for a seed).

```json input
{"type": "object", "required": ["path"], "properties": {"path": {"type": "string"},
  "t": {"type": "number", "default": 1.0}, "clip": {"type": ["object", "null"], "properties": {
    "t0": {"type": "number"}, "t1": {"type": "number"}, "fps": {"type": "number", "default": 25}}},
  "cam": {"type": ["object", "null"]}, "context": {"type": ["object", "null"]},
  "size": {"type": "string", "default": "640x480"},
  "format": {"enum": ["png", "gif", "apng", "mp4"], "default": "png"},
  "seed": {"type": "integer", "default": 0}, "attrs": {"type": ["object", "null"]},
  "bg": {"type": "string", "default": "#101014"}, "out": {"type": ["string", "null"], "default": null},
  "roots": {"type": ["array", "null"], "items": {"type": "string"}}, "wait_s": {"type": "number", "default": 0}}}
```
```json output
{"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"},
  "files": {"type": "array", "items": {"type": "string"}, "description": "PNG/GIF/MP4 paths; first = the still or the clip"},
  "format": {"type": "string"}, "frames": {"type": "integer"}, "size": {"type": "array", "items": {"type": "integer"}},
  "t": {"type": "array", "items": {"type": "number"}, "description": "times drawn"},
  "camera": {"type": "object"}, "context": {"type": ["object", "null"]},
  "stats": {"type": "object", "description": "particles per layer, stub_calls from the simulator"},
  "refused": {"type": "boolean"}, "why": {"type": "string"}}}
```
Example: `fx_render(path=<session out>, clip={"t0": 0, "t1": 4, "fps": 25}, format="gif", size="480x360",
context={"kind": "bone", "unit": "hd/character/enemy/mephisto.json", "bone": "root"})`.

## fx_export: write into a mod folder

Writes the working copy (or any effect) to `<mod_dir>/data/<logical>` (`mod_dir` is the folder that holds `data/`, e.g.
`.../mymod.mpq`; a `mod_dir` that is itself named `data` is used as is), `logical` = the session's `logical` or
the source's logical path or the argument. `extras`: `[{"src": file, "logical": path}]` copies more files (authored
textures) to their logical paths. A `mod_dir` that is one of the game `roots` is refused (game files are never written).
With `check` (default) the written file is read back and validated, and every texture/mesh it names must resolve in
`mod_dir` or the roots (`missing`).

```json input
{"type": "object", "required": ["path", "mod_dir"], "properties": {"path": {"type": "string"}, "mod_dir": {"type": "string"},
  "logical": {"type": ["string", "null"], "default": null}, "check": {"type": "boolean", "default": true},
  "extras": {"type": ["array", "null"], "items": {"type": "object", "required": ["src", "logical"]}},
  "roots": {"type": ["array", "null"], "items": {"type": "string"}}}}
```
```json output
{"type": "object", "required": ["ok", "written"], "properties": {"ok": {"type": "boolean"},
  "written": {"type": "array", "items": {"type": "object", "properties": {"logical": {"type": "string"},
    "file": {"type": "string"}, "size": {"type": "integer"}, "sha256": {"type": "string"}}}},
  "missing": {"type": "array", "items": {"type": "string"}}, "check": {"type": ["object", "null"]}}}
```

## fx_coverage: what each effect needs, what is drawn

Per effect: object classes, renderer kinds and materials, CBVM opcodes and externals used, engine functions called, and
the survey lane's verdict (`fx/coverage.py`: `drawn`, `partly` or `not`, and what is `missing`, as `group:name` strings
such as `kind:ribbon`, `op:call`, `sampler:curve`) against what the viewer supports so far; without that module `status`
is `"unknown"` and the needs are still listed. `summary` reduces the list to totals and the commonest
missing things.

```json input
{"type": "object", "properties": {"pattern": {"type": ["string", "null"], "default": null},
  "roots": {"type": ["array", "null"], "items": {"type": "string"}}, "limit": {"type": "integer", "default": 500},
  "summary": {"type": "boolean", "default": false}}}
```
```json output
{"type": "object", "required": ["ok", "count"], "properties": {"ok": {"type": "boolean"}, "count": {"type": "integer"},
  "status": {"type": "object", "description": "status -> count"},
  "effects": {"type": "array", "items": {"type": "object", "properties": {"path": {"type": "string"},
    "status": {"enum": ["drawn", "partly", "not", "unknown"]}, "classes": {"type": "object"},
    "kinds": {"type": "object"}, "materials": {"type": "array"}, "opcodes": {"type": "object"},
    "externals": {"type": "array"}, "engine_calls": {"type": "array"}, "samplers": {"type": "object"},
    "features": {"type": "object"}, "missing": {"type": "array", "items": {"type": "string"}}}}},
  "common": {"type": "object", "description": "summary: opcode/engine call/material -> number of effects"}}}
```

## fx_fired_by: what fires an effect (lane r3-bind3)

The reverse binding index of `fx/firedby.py` (built once: `python -m hdkit.fx.firedby build <data folder with hd/ and
global/excel/> --corpus <root> --exe <exe> --out <dir>`; the tool reads `index` or env `HDFX_FIRED_BY`). `path`: a logical
path, a file below a root or a bare file name. `status`: `bound` (named by a definition the game loads), `exe` (a path string
in the executable), `orphan` (named only by definitions nothing loads), `manifest` (only in `dependencies` preload lists),
`unreferenced`. `refs[]`: `src` (definition), `trigger` (as `context.Binding.trigger`, plus `manifest`, `other`), `key`,
`entity`, `comp`, `mode` (timeline), `bone`. `chains`: per definition, paths `[definition, parent..., "root text"]`, the
root text being e.g. `missile frostnova <- skill Frost Nova (cltmissilea)`, `level Act 1 - Town 1`, `monster mephisto`.
`evidence`: why an unused one is unused. `fx_coverage` rows carry `fired_by` (the one-line summary) when an index is set.

```json input
{"type": "object", "properties": {"path": {"type": "string"}, "index": {"type": ["string", "null"], "default": null}}}
```
```json output
{"type": "object", "required": ["ok", "path", "status"], "properties": {"ok": {"type": "boolean"},
  "path": {"type": "string"}, "status": {"enum": ["bound", "exe", "orphan", "manifest", "unreferenced"]},
  "summary": {"type": "string"}, "exe": {"type": "boolean"},
  "refs": {"type": "array", "items": {"type": "object", "properties": {"src": {"type": "string"},
    "trigger": {"type": "string"}, "key": {"type": "string"}, "entity": {"type": "string"}, "comp": {"type": "string"},
    "mode": {"type": "string"}, "bone": {"type": "string"}}}},
  "chains": {"type": "object", "description": "definition -> [[definition, parent, ..., root text]]"},
  "evidence": {"type": "array", "items": {"type": "string"}}}}
```

## fx_color_variants: the game's colour variants (lane r3-colorvariant)

Renderers with `ColorVariant` on recolour their particles by the effect attribute `ColorVariantIndex`, which the game sets
to 37 + the unit's state colour shift (cold/freeze 145, poison 141, red 137, revive 110; `fx/colorvariant.py`,
`docs/hdfx-shaders.md` 14). Lists every index with its palette entry (exponent and tint, from the roots'
`hd/global/palette/randtransforms.json` + `transforms.json`: add a casc root; `table_found` false = none found, variants
draw untinted), the state names, and for `path` the renderers that use them. Choose one in `fx_render` with
`attrs={"ColorVariantIndex": 145}` or a name (`"cold"`, `"shift:104"`, `"rand:3"`, `"none"`).

```json input
{"type": "object", "properties": {"path": {"type": ["string", "null"], "default": null},
  "roots": {"type": ["array", "null"], "items": {"type": "string"}}}}
```
```json output
{"type": "object", "required": ["ok", "choices"], "properties": {"ok": {"type": "boolean"}, "attr": {"type": "string"},
  "table_found": {"type": "boolean"},
  "choices": {"type": "array", "items": {"type": "object", "properties": {"index": {"type": "integer"},
    "name": {"type": "string"}, "source": {"enum": ["", "rand", "shift"]}, "slot": {"type": "integer"},
    "gamma": {"type": "number"}, "tint": {"type": "array"}, "identity": {"type": "boolean"}}}},
  "states": {"type": "object", "description": "state name -> ColorVariantIndex"},
  "renderers": {"type": "array", "items": {"type": "object", "properties": {"index": {"type": "integer"},
    "kind": {"type": "string"}, "layer": {"type": "string"}, "contribution": {"type": "number"}}}}}}
```

## Seams: what the other lanes provide (and what this API assumes)

`hdkit.fx.api.lane(name)` imports `hdkit.fx.<name>` when it exists. Until then the tool says `not_implemented` +
`needs`; nothing else changes. What the api calls, so a lane can check its module against it:

| Module (lane) | Used by | Assumed interface |
|-|-|-|
| `graph` (format) | inspect, edit, validate, render | `load(bytes, vfs=) -> Effect` with `to_json()` (and optional `validate() -> [str]`); `OPS`: the op names `apply_edits(data, ops) -> bytes` handles (every op listed there is routed to it, the others run on `hd_particles`/`hd_cbvm` here; `copy_layer`, `remove_layer`, `curve`, `layer:<name>` selectors and an empty-effect scaffold are not available without it) |
| `sim` (vm) | render | `Sim(effect, seed=, attrs=, ctx=)`, `.seek(t)`, `.step(dt)`, `.state()`, optional `.stats` (returned in `stats`) |
| `render` (render) | render | `FxRenderer(gpu, vfs)`, `.prepare(effect, state, cam)`, `.render(draws, cam, (w, h), base=, light=, post=) -> RGBA uint8 array`; `camera_from_spec(spec: dict, size, state) -> cam` turns fx_render's `cam` dict (empty = a default) into the renderer's camera; called once per render, on the first frame's state, so it should give a stable camera (effect-level bounds, not that frame's particles) |
| `context` (context, landed) | render | `attach`, `missile`, `skill`, `scene`, `effects_of_missile/skill`, `Tables.from_dir`, `unit_path`, `drive(driver, make_sim, fps, duration, seed)` as in `docs/hdfx.md` section 6; a scene's `.light.light` and `.light.post.as_kwargs()` go to `FxRenderer.render(light=, post=)`; `make_sim(effect, ctx, seed)` builds the `Sim` (the effect may be a `.particles` path of a binding: the api loads it with `graph.load`). Optional `render.base_scene(gpu, vfs, unit=, scene=) -> base` draws the opaque unit/map under the effect |
| `lang` (lang) | edit `script` op, script_read/write | `compile(src, effect=None) -> hd_cbvm.Script` (the api sets `.obj`), `decompile(Script) -> str` |
| `coverage` (survey, landed) | coverage | `survey_file(path, rel)` and `assess(entry)` (status, `missing`); the supported sets in that module decide the verdict |

The GPU device is shared with the `view_*` tools in the MCP server (`api.GPU_PROVIDER`); the web door makes its own.
Script ids are 1-based object numbers; `hd_cbvm.Script.obj` is 0-based (the api converts).

## JSON endpoints for the web editor

`python -m hdkit.view serve-web [<bundle>] --fx [--fx-out <folder>] [--fx-roots name=folder ...]` adds, on the same
127.0.0.1-only server (without `--fx` they answer 403; `<bundle>` is optional with `--fx`):

| Request | Answer |
|-|-|
| `GET /api/fx/tools` | `{"tools": {name: {"description", "input": <json schema from the signature>}}, "engine": {...}, "out_root": "<the output folder>"}` (`out_root` lets the web editor name its own session folders: `<out_root>/edit/web-<id>-<n>-<name>`) |
| `POST /api/fx/<tool>` | body = the tool's arguments as one JSON object (`Content-Type: application/json`, at most 8 MB); answer = the tool's result, HTTP 200 also for `ok: false` (HTTP 400 only for a malformed body or unknown tool) |
| `GET /api/fx/file?path=<file>` | a file below the fx output folder (render PNG/GIF/MP4, `.particles`, listings); no other file is served |

JSON answers never carry NaN or Infinity (they become `null`: real turbulence samplers hold NaN words). Safety: the server binds 127.0.0.1; a request is refused (403) when its `Host` is not `127.0.0.1`/`localhost` (DNS
rebinding) or its `Origin` header names another origin; `roots` and `mod_dir` are the caller's arguments, so run the
server only on a machine you trust. Tools run one at a time (one lock, like the MCP server). The GPU tools use the same
gate and answer `refused` the same way.

## Environment

`HDKIT_VIEW_ROOTS` default roots; `HDKIT_FX_OUT` / `HDKIT_VIEW_OUT` output folder; `HDKIT_MCP_INLINE_KB` inline PNG limit.
Tests: `tests/test_hdfx_api.py` (synthetic files; the stdio end-to-end check runs when the `mcp` package is present).
