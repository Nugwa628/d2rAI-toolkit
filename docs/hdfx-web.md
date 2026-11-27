# hdfx web editor (`fx.html`)

Lane `web` of the hdfx work (interfaces: `docs/hdfx.md` section 5, endpoints: `docs/hdfx-mcp.md`). The effect editor page of the
existing viewer web app: dependency-free JS modules (no build, no CDN, nothing loaded from the network), served by the same
local-only server (127.0.0.1) as the other pages. It is a client of the tools' JSON endpoints and nothing else: whatever the MCP
tools can do, the page can do, and the edit sessions are the same folders (`fx_session`), so an agent and a person can open one.

## Run

```
python -m hdkit.view fx --roots d2r=~/d2r-extract/data --out ~/hdkit-work/fx
   -> http://127.0.0.1:8766/fx.html           (the real tools; --roots default HDKIT_VIEW_ROOTS, --out default HDKIT_FX_OUT)
python -m hdkit.view fx --mock               (three built-in effect graphs; no game files)
python -m hdkit.view.webserve --fx ...        (the same server with the viewer bundle optional; the page is /fx.html there too)
```
Page parameters (`fx.html?open=<path>&sel=renderer:0&tab=script&t=1.5&ctx=bone`) open an effect, select a node, show a tab, set the
time and the context kind: used by the screenshots and the checks.

## Panels

| Panel | What it does | Tools used |
|-|-|-|
| Effects | search by path (text/glob), renderer kind, texture/mesh used; click opens | `fx_list(info)` |
| Layers | tree: layers (spawn/evolve scripts, renderers, fields, child layers), samplers, attributes, all scripts; enabled switch shown struck out when off; Remove layer. Layer inspector: the layer's colour constants (picker + HDR multiplier + alpha, or number fields by width) and a hue/saturation/value shift of all of them | `fx_inspect` (graph when present), ops `remove_layer`, `color` (`layer`, `index`, `value` / `shift`) |
| Inspector | per node. Renderer: enable switch, properties grouped by prefix with a filter and typed widgets (bool switch, int/float field + slider, vec fields, colour picker with HDR multiplier, texture/mesh path with a path list, enum); properties with no stored value are shown as "default (not stored)" because the API edits stored values only; unknown types are shown raw | `fx_edit` ops `prop`, `texture`, `enable` |
| Tint | picks a colour and multiplies every script's float4 stream of that name (names offered from the scripts' externals) | op `color` |
| Curve editor | samplers are told apart by object number (names such as `__sampler_0` repeat in every layer); Hermite keys with in/out tangent handles (drag), multi-dimensional (per-dimension colours and toggles), double-click adds a key, Delete removes, auto tangents (no overshoot), interpolation hermite/linear/step, numeric table of the selected key, wheel zoom, shift+drag pan | op `curve` (needs the effect graph) |
| Script | CBVM listing or `.hfx` source (language switch), constants table (edit a value = `const` op), Validate (into a scratch session: nothing changes), Apply (op `script`), error lines are clickable | `fx_script_read`, `fx_script_write` (scratch), ops `script`, `const` |
| Validate | `fx_validate` of the working copy, `fx_coverage` | |
| History | the session's versions (click = goto) | `fx_session` |
| Preview | server-rendered PNG at time `t` and seed, camera (orbit/game, az/el/dist), size; Clip GIF; play/pause/step/scrub/speed | `fx_render` |
| Context | none, unit + bone, missile (from/to, speed, hit/explode events), skill cast (unit, frame), map scene (map, xy) | `fx_render(context)` kinds of `docs/hdfx-mcp.md` |
| Compose | load another effect, pick one of its layers, copy it in (optional new name) | op `copy_layer` (needs the graph) |
| Top bar | open by path or session folder, New (`fx_create`), Undo/Redo (Ctrl+Z, Ctrl+Shift+Z, Ctrl+Y), Export to a mod folder | `fx_session`, `fx_create`, `fx_export` |

## How edits work

Edits go to the effect's edit session, never to the game's file. The page names its own session folder for the first edit
(`<out_root>/edit/web-<page id>-<n>-<effect>`; `out_root` is in `GET /api/fx/tools`) so opening an effect starts from the source;
to resume a session, open its folder (shown in History). `EditQueue` (`fxlogic.js`) runs the calls one at a time: **one history
version per edit**; an edit with the same key as the previous one (a drag, typing in a number field, the tint picker) first undoes
that version and then edits, so a drag leaves one version, and queued same-key edits that did not start are replaced by the newest.
An edit the server refuses (all-or-nothing, `error.op_index`) leaves the session alone and its reason is shown. After the queue
drains the page re-reads `fx_inspect` of the session and repaints; the preview re-renders (switch "re-render on edit").

## What each lane gives the page

| Lane | Without it | With it |
|-|-|-|
| format (`fx/graph.py`) | one pseudo layer "(all renderers)" from `fx_inspect.renderers`, no samplers/attributes/fields, curve editor and layer copy/remove explain why they are off | real layer tree, curve editor, compose, remove layer |
| vm + render (`fx/sim.py`, `fx/render.py`) | the preview says which lane it needs (`not_implemented` is shown, not an error) | PNG/GIF frames; `Preview.show` in `fx.js` is the seam for a live WebGPU canvas later |
| context | context picker answers `not_implemented` | bone/missile/skill/scene previews |
| lang | `.hfx` view answers `not_implemented` | `.hfx` listing and editing; diagnostics with line numbers |

Checked against the real format lane (game effects: Mephisto's ambient smoke, an ice storm impact with 24 samplers): a graph
script's `obj` is the tools' script id (the number in the listing header; `scriptId` in `fxlogic.js`); graph renderer props carry
`string` where `fx_inspect` has `file` and the type `feature` for the switches (`toModel`/`widgetFor` handle both); sampler names
repeat across layers, so the `curve` op takes the sampler's object number; properties with no stored value cannot be edited
(`fx_edit` says "no stored value"), the page shows them as "default (not stored)". Still assumed: context specs are those of
`fx_render` in `docs/hdfx-mcp.md`; `.hfx` goes through the same `script` op with `language: "hfx"` once lane lang lands.

`webserve.py` change made for the page (also in `docs/hdfx-mcp.md`): `GET /api/fx/tools` carries `out_root`, and every JSON answer
turns NaN/Infinity into `null` (real turbulence samplers hold NaN words and `JSON.parse` refuses them).

## Files

`view/web/fx.html` (page and styles), `fx.js` (panels, API calls), `fxlogic.js` (pure logic: Hermite curves and key edits, op builders,
`EditQueue`, widget typing, model/tree, diagnostics; testable under node), `fxcurve.js` (the curve canvas), `view/fxeditor.py`
(`python -m hdkit.view fx`), `view/fxmock.py` (stand-in with the same contract, `engine.mock = true`, for development and checks).
Tests: `tests/test_hdfx_web.py` (assets, mock contract, endpoint safety, session choreography on the real tools over the
synthetic fixtures, node logic tests, headless Chrome on the mock and on the real tools), `fx_web_logic.test.mjs`,
`fx_web_browser.mjs` (mock: 42 checks), `fx_web_real.mjs` (real tools: any effect, 33 checks on a curve-rich game effect, 10 on the
synthetic fixtures; `node tests/fx_web_real.mjs <base url> <shots folder> <effect path>`), shared driver `fx_cdp.mjs`.
`HDFX_WEB_BROWSER=0` skips the Chrome checks, `HDFX_WEB_SHOTS=<folder>` keeps their screenshots, `CHROME=<binary>`.
