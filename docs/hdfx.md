# hdfx: D2R HD particle effects, read, simulate, draw, edit, author

`hdkit.fx` (with its viewer parts in `hdkit.view`) reads, simulates, draws and edits D2R's HD particle effects, the
baked PopcornFX `.particles` files, and authors new ones. Start with `knowledge/hd-effects.md` for the overview and the
commands; this page is the reference of every interface. The work was done as a coordinator plus parallel lanes
(docs/ai-workflow.md); the lane names below (calib, r3-decal2, r5-postpass, ...) say which piece came from which lane and
are kept as provenance. Numbers "checked in game" were measured against screenshots and RenderDoc captures of D2R with a
mod loaded; the captures are not in this repository. Companion pages: `docs/hdfx-shaders.md` (the game's shaders),
`docs/hdfx-vm-re.md` (the CBVM runtime read from D2R.exe), `docs/hdfx-lang.md` (the script language),
`docs/hdfx-mcp.md` (API and MCP tools), `docs/hdfx-web.md` (the web editor), `docs/hdfx-cull.md` (culling).

## Where code goes

Everything lives in **`hdkit/fx/`** (a package next to `hdkit/view`). No project paths, game slots or mod names in
`hdkit/`: they come from arguments, environment variables or `hdkit.toml` (`hdkit/site.py`).
`hdkit.formats.hd_particles` reads the object file; `hdkit.formats.hd_cbvm` is `hdkit.fx.cbvm` under its old name.

| Module | Lane | What |
|-|-|-|
| `fx/schema.py` | format | field-size schema of all 26 classes (port of reimagined-d2r-mod-studio `ParticleFile.cs`, MIT, credited) |
| `fx/pfile.py` | format | lossless object file: header, objects (class, ordered fields), strings; `PFile.read(bytes)`, `.data()` byte-exact; add/remove/renumber objects, merge string tables |
| `fx/graph.py` | format | effect graph (dataclasses below), `load(bytes or path, vfs=None) -> Effect`, `Effect.to_json()`, `apply_edits` |
| `fx/cbvm.py` | vm | disassembler/assembler (moved from `hd_cbvm.py`), opcode tables |
| `fx/vm.py` | vm | vectorised CBVM interpreter (numpy SoA), engine-call registry |
| `fx/sim.py` | vm | runtime: effect clock, layers, spawners, particle storage, events, child layers; `Sim` below |
| `fx/samplers.py` | samplers | curves (cubic Hermite), shapes, turbulence/vector fields, `rand`/`vrand`; ports of PopcornFX-Web (MIT, credited). Event-stream and text samplers are not here: `fx/sim.py` owns them |
| `fx/noise.py`, `fx/noise_data.py` | vm | the script builtins `noise`, `noise3`, `fast.noise`: bit-exact fp32 ports of D2R.exe's kernels (tables dumped by `python -m hdkit.fx.noise_dump`; vm and samplers call them) |
| `fx/shad.py`, `fx/shaders/` notes | shaders | `SHAD` container reader, permutation index, disassembly notes of the Uber shaders |
| `fx/render.py`, `view/fx.wgsl` | render | packers (billboard, ribbon, mesh, light, decal) and WGSL Uber material; draws into `view/gpu.py`'s pass |
| `view/shadow.py` | r3-shadows | the game's directional shadow slices: casters (units, world, effects with `Shadows` / `Lit.CastShadows`), receivers (bind group 1), ground gain over game shots |
| `fx/lang/` | lang | the readable script language (`.hfx` text) → CBVM via `fx/cbvm.py`'s assembler; decompiler for reading |
| `fx/context.py` | context | bones (overlays on units), missiles (paths, speed, hit/explode events), skills (cast timing), map scenes, game light/bloom/tonemap |
| `view/fxplace.py` | r3-overlays | where the game puts an overlay effect: its overlay JSON from the effect's path (`overlay_for`), world yaw `OVERLAY_WORLD_YAW` (0: not the unit's facing), cast-overlay stop time (`stop_time`, `advance`) |
| `fx/api.py` | mcp | the pure-Python API every MCP tool and the web editor call (no `mcp` import) |
| `view/mcp_server.py` (`fx_*` tools) | mcp | thin wrappers over `fx/api.py` |
| `view/web/fx*.{html,js}` | web | the editor page on the same API (served by `view/webserve.py`) |
| `fx/calib/` | calib | capture bench, general parts: RenderDoc layer and scripts, capture decoding, viewer-vs-game comparison, load test. The game runner (slots, launches, harness plans) is the host project's own |
| `fx/mipbias.py` | r3-mipbias | material mip bias (cb4[6].y) from `General.MipBias`, `General.DrawResolution` and the game settings (`Quality`; docs/hdfx-shaders.md 2.1) |
| `fx/coverage.py` | survey | per-effect needs (classes, opcodes, calls, renderer features) and drawn/partly/not/empty report (`empty`: needs nothing; objects the root does not reach are not counted) |
| `fx/gamekinds.py` | r3-misc | D2R's material factory rule (kinds 3/6 and `Distortion_Billboard`: no material, not drawn), switches the game ignores (`LegacyLit`, `MeshSampling`, `Sound`, `Distortion`), sound records and markers |
| `fx/firedby.py` | r3-bind3 | reverse binding index: what fires each `.particles` (definition, trigger, chain to the skill/state/monster/object/level/item/exe that loads it), unused effects with evidence |
| `fx/decal.py` | r3-decal | D2R's FX decal record (`pack`, `records`), surface blend reference (`coverage`, `shade`), float plate texture; `docs/hdfx-shaders.md` 13 |
| `fx/decal_pass.py` + `view/fx_decals.wgsl` | r3-decal2 | every decal of a frame in one draw pair (`fs_decals_mul`, `fs_decals_add`): records in game order (`Record.seq`) in a storage buffer, maps in texture arrays (bind group 1), the material walk with the stop at summed weight 1, lit once; receivers = the ground plane (`RECEIVERS`, `RECEIVER_TOL`); CPU reference `blend` |
| `fx/timeline_fx.py` | r3-decal2 | an effect spawned by a unit clip's timeline event (death pools): spawn time, bone, clip played once and held (`TimelineEffect`, `ClipPoses`); fxcli `--timeline dt` |
| `fx/aurastencil.py` | r3-aurastencil | `EnableAuraStencil`: the game's stencil test on Uber draws (hidden over units), the unit stencil marks (docs/hdfx-shaders.md 12.1) |
| `fx/sceneunits.py` | r3-aura2 | more units in the viewer's scene (scene-units JSON: unit, `at` sub-tiles or `pos`, yaw, mode/clip, timing), the clip of a unit mode from its state machine (`mode_clip`), units read from a capture's model draws (`from_capture`) |
| `fx/wallcast.py` | r4-firewall | Fire Wall's missiles as the game lays them (srvdofunc 24: two makers from the target, one fire per sub-tile they enter, one on the target; `layout`, `children`, `range_of` for a Range formula like the mod's `7+2*lvl`); `cast()` calls it |
| `fx/firemakers.py` | r4-firemakers | Open-ground Molten Boulder trail, Blaze movement trail and Immolation Arrow impact disc. The cast driver resolves server sub-missiles from the tables and reuses the Fire Wall driver for stationary fires. |

Tests: `tests/test_hdfx_<module>.py`, unittest, no game files needed (synthetic files or a fixture folder that is
skipped when absent). Game files: the `HDKIT_VIEW_ROOTS` roots (`name=folder`, os.pathsep separated) or `--root casc`.
An extracted copy of the current game's `hd/vfx` (build 3.4.95032: 1,991 `.particles`, meshes, textures) is what the
corpus tests read: point `HDFX_CORPUS` (and the other `HDFX_*` variables a test names) at your own extract, read only
(root `d2r=<that folder>`); without them those tests skip. An older ~93847 tree (2,206 files; 1,480 particles differ)
is for comparison only: edits for the game must start from the current build (or `--root casc`).

## 1. Effect graph (`fx/graph.py`)

Plain dataclasses, JSON-serialisable (`to_json()`/`from_json()`), object numbers kept so edits map back to bytes.

```
Effect(path, version, objects, layers: list[Layer], attributes: list[Attribute], samplers: list[Sampler],
       events: list[EventSlot], entry: list[EntrySlot], scripts: dict[int, ScriptRef], strings: list[str],
       platform="PC", platforms: list[str], graph_obj)          # .renderers: every renderer once, in slot order
Layer(obj, name, slot, slot_obj, spawn_script: int|None, evolve_scripts: list[int], fields: list[Field],
      renderers: list[RendererDef], events: list[Event], children: list[str],  # layers its events spawn, by name
      life: None, spawn: {by: [event names], event_streams: [..]},  # rates/counts live in bytecode: not static
      samplers: [names], attribs: [names], inputs/outputs: [event slot indexes], colors: [{index, width, value}],
      scripts: [obj])                             # an unnamed layer is "@<obj>"; one layer object may fill several slots
Field(obj, name, type, size, id, inputs)          # name = the scripts' external name: hex(id - 7) ("16"), "_<id>" for
                                                  # the 7 built-ins; inputs = ["<renderer obj>:<input>"] it feeds
Attribute(obj, name, type, default, min, max, description)   # effect attributes (inputs set by the game: e.g. Fade)
Sampler(obj, name, kind: "curve"|"shape"|"turbulence"|"eventstream"|"text", data: dict, data_obj, layer, label)
   curve data: {times:[], values:[[..]], tangents_in:[[..]], tangents_out:[[..]], dim, interp: "hermite"|"linear"}
RendererDef(obj, index, kind: "billboard"|"ribbon"|"mesh"|"light"|"decal"|..., material, enabled,
            props: dict[name -> {obj, type, type_id, value, string}], inputs: dict[renderer input -> field name], layer)
            # inputs without a name are "input<N>" (mesh: input0 position float3, input1 scale, input2 enabled bool,
            # input14 orientation)
Event(obj, name, payloads: [{name, type, size}])  # the layer's CLayerCompileCacheEvent objects
EventSlot(obj, index, name, source: layer slot (-1 = the effect), targets: [layer slots])
EntrySlot(obj, name, slot)
ScriptRef(obj, kind: "spawn"|"evolve"|"evolve3"|"kind1"|"kind2", layer, externals: [{name, type}], calls, raw_kind,
          consts)                                 # raw kind = evaluator id: 4 evolve (full-rate nodes), 3 and 1/2 add
                                                  # slower-rate nodes, 5 all nodes, per-particle time (newborns): section 2
```

The file holds one layer graph per platform (`PC`, `Switch`, `PS4`, `PS5`); `load(..., platform="PC")`. Event and entry
slot objects are shared by all four graphs (layer slots are per platform), so `copy_layer`/`remove_layer` always change
every platform graph. Object-level access for anything not in the graph: `fx/pfile.py` (`PFile`, `schema.FIELDS` says
which fields are object/string references).

Edits (shared by API, CLI and web): a list of ops, each a dict, applied by `graph.apply_edits(data, ops) -> bytes`:
`{"op":"prop","renderer":sel,"name":..,"value":..}`, `{"op":"texture",...,"path":..}`, `{"op":"enable",...,"on":bool}`,
`{"op":"curve","sampler":name,"times":..,"values":..,"tangents_in":..,"tangents_out":..}`, `{"op":"const","script":obj,
"index":i,"values":[..]}`, `{"op":"script","script":obj,"text":<cbvm listing or .hfx source>}`, `{"op":"color",...}`,
`{"op":"copy_layer","from":path,"layer":name,"as":name}` (compose), `{"op":"remove_layer","layer":name}`.
`sel` = renderer index, `"layer:<name>"`, `"obj:<n>"`, a kind, a path fragment, a list, or `"*"`. Details: `prop` value is a
number, list or bool (scalars fill all 4 lanes, as `hd_particles.set_property`); `texture` takes `name` (property) or
`old` (path) and `path`; `curve` `sampler` is an object number, `"<layer>/<name>"` or a unique name/label, and may change
the key count; `const` also takes `"kind": "i"` and changes identical copies of the script and the layer's constant
table; `color` takes `layer` (name or `"*"`), `index` (from `Layer.colors`) and `value`, or `shift` {hue, sat, val};
`script` takes CBVM listings only until `fx/lang` exists; `copy_layer` attaches the layer to the event that spawns it in
the source (child layers are not copied; missing effect attributes are added).

## 2. Simulation (`fx/vm.py`, `fx/sim.py`)

```
sim = Sim(effect: Effect, seed=0, attrs: dict|None=None, ctx: SimContext|None=None)
sim.step(dt)                      # advance; fixed sub-steps inside if needed
sim.seek(t)                       # restart and run to t (deterministic for a seed)
sim.state() -> SimState
SimState(t, layers: dict[layer name -> ParticleBatch], lights: list, decals: list, events: list[(t, name, payload)])
ParticleBatch(count, fields: dict[name -> np.ndarray (count, n) float32], renderer_inputs: dict[renderer index -> dict])
SimContext(transform: 4x4 (effect → world), velocity, target, bones: callable(name)->4x4, ground_z, emitter_shape, time_scale, scene_time0 (the game's scene clock at the effect start: turbulence time),
           camera: xyz | None,     # camera: view.position for view.* calls (added by lane vm); None: vm.Env's default,
           running: bool = True,    # since lane r3-aura2 the game camera vm.game_eye() (884 units towards the camera at
           collision: World | None) # el 30 / az 45; was (0, 10, 10), which put camera-pushed auras 21 px left, 15 px up);
                                    # running False: Sim.step stops the effect (a driver's stop_t, lane r3-calibfix);
                                    # collision: hdkit.fx.collide.World of scene.intersect* (lane r3-intersect)
sim.stop()                        # the game stops the effect: effect.isRunning becomes false (added by lane vm)
sim.ctx                           # settable between steps (fx/context.py drive())
```
`effect` is a path, the file's bytes, an object with `.path` (and optionally `.data`), or a `routing.Routing`. The runtime
reads the layer/event-slot routing through `fx/graph.py` (`routing.load(data, path, platform="PC")`: graph layers, events,
samplers and renderers, plus each layer's field objects, event payload lists and the scripts). `SimState.events` holds
`(t, event name, count)`, `ParticleBatch.fields` every layer field (`self.lifeRatio`, `self.invLife`, `0`, `1` ... the scripts'
external names; `SelfID`/`ParentID` for the built-in fields 4/5) plus streams only a script names.

**`ParticleBatch.renderer_inputs`** (lane render reads it): `{renderer index (graph.RendererDef.index, PC graph):
{input name: (count, k) array}}`, one entry per renderer of the layer slot, the arrays are the layer's streams (float32;
`Enabled` bool; ints int32), rows = the batch's particles (no filtering: `Enabled` is an input like the others). Named
inputs keep their name (`Color.Color`, `Light.Color`, `LightAttenuation.Range`, `Decal.DecalSize`, `Atlas.TextureID` ...);
unnamed `input<N>` get `sim.INPUT_NAMES` (from the corpus and D2R.exe's names): 0 `Position`, 1 `Size` (billboard),
`Scale` (mesh, decal: float3), `Width` (ribbon), 2 `Enabled`, 3 `SortKey` (Transparent.SortMode 1), 4 `Axis` (billboarding
modes 2-5), 5 `Axis2` (mode 5, planar), 6 `Rotation` (modes 0/1/5), 7 `Size2` (General.EnableSize2D), 8 `Axis` (ribbon),
9 `SelfID`, 10 `ParentID` (ribbon, int2 (0, id): the parent's SelfID; a ribbon chains the particles of one ParentID by
SelfID), 11/12 `Position2`/`Position3` (triangle), 13 `MeshID`, 14 `Orientation` (quaternion xyzw). Constant inputs are
streams too (their field's constant-pool default), so every input with a field is present.
**`SimState.lights` / `.decals`**: one dict per light/decal renderer with particles: `layer`, `renderer`, `kind`, `count`,
`on` (bool per particle: `Enabled` and not switched off by `_pksi_vvUpdateLight/Decal(.., false)` or `Kill*Immediate`),
`position` (n,3), `color` (n,4: `Light.Color`), `power` (n: `Light.Power`), `range` (n: `LightAttenuation.Range`), `handle`
(the `_pksi_vvAllocate*` handle per particle or None); decals add `size` (`Decal.DecalSize`), `direction`, `orientation`,
`weight` and `textures` {property: path} (the renderer's texture properties).
**`SimState.sounds`** (lane r3-misc): one dict per sound renderer (kind 6) with particles, `fx/gamekinds.sound_record`:
`layer`, `renderer`, `count`, `on`, `position` (n,3), `volume` (n), `ids`, `fired` (on and born since the previous
`state()`: PopcornFX starts one voice per new particle), `sound` (`Sound.SoundData` or None). D2R plays none (no sound
backend; the renderer has no material); `FxRenderer.sounds` keeps the last prepare's, `FxRenderer.show_sounds` (CLI
`--sound-markers`) draws a magenta marker at each, and `metrics.json` frames / `fx_render` stats list them.

**Events and payloads** (`fx/routing.py` docstring): a parent's `appendPayload(p, k, e)` puts element k of its event's
payload list (CLayerCompileCacheEvent field 2); a child's `extractPayloadElement*(i)` reads entry i of its layer's incoming
list (CLayerCompileCache field 11), matched by element name; elements the spawning event does not carry read as zeros
(`stats["payload_absent"]`). Element semantics are the payload builtin kinds: 0 Generic, 1 XFormsPositions, 2
XFormsOrientations, 3 EmittedCount (counts the children of one burst). `xform*` uses the effect instance's transform, lerped
prev -> now at the particle's sub-frame birth time in spawn scripts (`LayerEnv.birth_frac` = the kick's `FRAC`; lane cast2,
D2R.exe 0xbfdd60/0xc001b0), **except** (lane trailre) local -> world in the spawn script of an event child whose event carries
an XFormsPositions element: then the local frame is that element (`sim.XPOS`) rotated by the first XFormsOrientations element
(`sim.XORI`) if any, no scale (`LayerEnv.xform_payload`, `vm.xform_payload`; docs/hdfx-vm-re.md). Event slots
whose event no script fires (`CLayerCompileCacheEvent` field 1 unset; `Routing.dead_events()`) never spawn: the
`__e_Spawn`/`__e_SpawnElse` gates are resolved per platform by the bake (on PC 1,616 `__e_Spawn` slots with targets are dead).

**Scripts per frame**: blob field 1 is the evaluator id (layer field 16 `ActiveEvaluatorMask` has its bit). The stores of
4 are a subset of 3's, 3's of 1/2's, and 1/2 = 5 in every layer: 4 runs the full-rate nodes, 3 and 1/2 add the nodes with
a lower update rate (mostly turbulence/curve sampling), 5 runs everything with per-particle time. `Sim(tier="full")`
(default, LOD 0) runs the largest of 1/2/3/4 every frame, `tier="base"` kind 4; particles a kick spawns during a frame run
5 for the rest of it (dt = (1 - their sub-frame time) x dt: "Evolve Newborns"). `Sim(platform=)` picks another graph.
**Calls checked against D2R.exe** (`vm.NOTES`): `generate(state, amount, divisor, a, b)` (0xc526b0/0xc529f0: amount /
divisor accumulates in state.x, floor of it fires, child k at f = min(1, (k + 1 - x0) / n) inside the window [a, b] of
the frame), `trigger(cond, t)` (0xc53580: one event, at sub-frame time clamp(t, 0, 1)), `xform*/dxformdt*(v, mask)`
(0xbfdd60: one implementation, the mask says it all: bits 0-2 translation/rotation/scale, 3-4 source and 5-6 destination
space, 0 world, 1 previous-frame world, 2/3 local; `vm.xform_matrix`), `idivmi d, a, k, magic, shift` = a / k.
Lane vmre read the rest (docs/hdfx-vm-re.md, addresses and evidence): opcode semantics (sincos = (sin, cos), mfn3
lerp/clamp/within, sel, vct lane copy), vrand(a, b), orientation axes and scene.orientation_* (D2R's frame 0: Right +x,
Up +y, Forward -z; `effect.axisForward` = -column 2), dxformdt (translation = SimContext.velocity), payload
interpolation modes and Hermite (`buildPayloadElementD`), scene.intersect (float4 normal + distance), the `_pksi_*`
hooks, and the evolver's tier choice. **Registers** (lanes r3-calibfix, r3-decal2, r3-misc, r3-overlays; one rule since the r3 integration): a register read
under another type than its last write reinterprets the 32-bit lane (`vm.reinterpret`; fx/lang `asint`/`asfloat`/`asbool`),
only `cvt` converts values; a bool is a lane mask, true = all ones; `and`/`or`/`xor` run on raw lanes (`vm.bitwise_lanes`).
The corpus' select idiom `and x.int, x.int, mask.int` (~18k bool and ~15k float reads as int) keeps x where the mask is set
(Frost Nova's SmokePuff stop fade `dt * 4 & !isRunning`). `vm.NOTES` status: plain = confirmed, `game:` = confirmed kernel reading game
state (stand-in used), `doubtful:` = none left (intersect/intersectExt: lane r3-intersect, `fx/collide.py`); `noise_float`, `fast.noise_float` and `noise_float3` are bit-exact ports of D2R.exe's kernels (`fx/noise.py`, `noise_data.py` dumped by `python -m hdkit.fx.noise_dump`).
**Collision** (lane r3-intersect, `fx/collide.py`): `scene.intersect*` query `Env.collision`, a `collide.World` (boxes,
spheres, floor slabs, triangle meshes with D2Physics categories; `world_from_preset(fs, preset, origin)` reads a room's
PhysicsBodyDefinitionComponents) set from the new `SimContext.collision` (default: the floor slab at `ground_z`; a
driver passes its `.collision`). Game semantics: ray origin -> origin + direction (1 unit for the scripts' unit
directions; the length only gates), hit = (normal, 0); `World(mode="authored")` uses the length and the real distance.
`python -m hdkit.view fx ... --collision game|authored|none [--frame-dt S]`. Coverage no longer counts `doubtful:` calls. **Confirmed in game by lane r3-intersect2**: colliding
particles rest ~1 unit above the floor (Cold Blood ice chunks middle value 1.04, barricade wall debris 0.80-1.19; authored would
be 0.01). Frame-rate dependent: debris faster than ~1 unit per
frame tunnels (30 fps); render with `--frame-dt` at the game's rate.
`python -m hdkit.fx langvm <dir> [--effects N]` (`fx/vmcheck.py`): every script fx/lang decompiles is compiled back and
both run in the interpreter on the same inputs (and N whole effects in Sim); stores, kills and kicks must match.

Engine calls: `vm.ENGINE[name] = fn(ctx, *args) -> array`, `ctx` a `vm.CallCtx` (`.env` the runtime: `t`, `dt`, `rng`,
`transform`, `sampler(name)`, `kick(...)`, `stats`; `.n` particles; `.bound` the external the call is bound to, e.g. the
sampler or event; `.dst_type`); unknown calls run a stub that returns zeros and counts the name in
`sim.stats["stub_calls"]` (the coverage report reads it). `vm.NOTES[name]` says how each call is modelled ("doubtful: ..."
for guesses). Sampler calls dispatch to `samplers.ENGINE_CALLS[name](ctx, sampler, *args)` with the sampler object built by
`samplers.make(kind, raw fields)` from the layer's sampler bound by the call. World units: the game's HD metres, y up (as
`view/gr2.py`). `python -m hdkit.fx sim <file> --t 2 --json` (counts and stream statistics per layer) and
`python -m hdkit.fx vmcov <dir> [--run]` (opcodes, implemented/doubtful calls, effects that run clean).

### Samplers API (`fx/samplers.py`)

All sampling is numpy-vectorised: inputs are scalars or `(N,)`/`(N,k)` arrays, outputs `(N,k)` float32 (`dtype=` to change).
Sampler objects are built from graph data with `samplers.make(kind, source)`, `kind` = `"curve"|"shape"|"turbulence"`,
`source` = the `Sampler.data` JSON dict (`X.from_json`) or the raw field bytes `{field id: bytes}` of the object
(`X.from_fields`; arrays without their count). Every class has `to_json()`; `Curve` also `to_fields()` (fields 17/18/19).

- `Curve(times, values, tangents=None, dim, mode="hermite"|"linear"|"step", wrap="clamp"|"repeat"|"mirror", min_limits,
  max_limits)`: `.sample(t)`, `.derive(t)`, `.integrate(a, b)`, `.key_sum(a, b)`, `.valid`; `Curve.from_slopes(...)` authors
  Hermite curves from per-time slopes. **Tangent layout of D2R curves** (verified on the corpus): field 19 holds `2*n*dim`
  floats, key-major `[key][in|out][component]`; the tangent used for a segment is the one of its start key (`out`) and end
  key (`in`), **already multiplied by that segment's duration** (the unused `in` of key 0 / `out` of the last key keep the raw
  slope). Curves without field 19 are linear; fewer than two keys or inconsistent sizes = invalid, samples 0. Curves in
  the corpus always span times 0..1. `DoubleCurve(c0, c1).sample(t, selector)`.
- `ShapeSampler(type, ...)` with `type` in box, sphere, ellipsoid, cylinder (Y axis), capsule, cone (apex `+height`), mesh
  (`set_mesh(positions, indices, normals)`, mode `fast` = triangle by index like the engine, `uniform` = by area), or a
  collection (`subs=[(shape, weight)]`). `.sample_position(rand, n, pcoords=None)`, `.sample_normal`, `.sample` (both for
  the same points), `.sample_pcoords(n, rand)` (3 uniforms per point; passing them back gives the same point).
  Shape field map (`from_fields`): 11 type (0 box 1 sphere 2 ellipsoid 3 cylinder 4 capsule 5 cone 6 mesh), 10 sample
  dimensionality (3 = volume, else surface), 12 box dims, 13 radius, 14 inner radius, 16 height, 17 hemisphere,
  18 non-uniform scale, 20 mesh scale, 27/28 inherit translate/rotate, 29 position, 30 euler degrees.
- `TurbulenceSampler(**params)` (`strength, wavelength, octaves, lacunarity, gain, interpolator, time_scale, time_base,
  time_random_variation, flow_factor, divergence_factor, initial_seed, fast_fake_flow, global_scale, gain_multiplier,
  lattice`; omitted = the defaults, the same in 1.13 and D2R 2.9): `.sample_curl(pos, time)`, `.sample_potential(pos, time)`,
  `.sample(pos, time)`; `time` scalar or per point. Octaves, gradient and curl are D2R.exe 2.9's (read by lane vmfix, same
  as PopcornFX-Web 1.13 term for term). `lattice="d2r"` (default) is D2R's lattice: odd entries negated, phase 0 = that
  lattice, value i = fast_sin(phase x speed_i + asin(value_i)); `"web113"` is PopcornFX-Web's (bit-exact with its JS,
  the golden tests). `TURB_FIELDS` maps file field ids 10, 11, 19..31 as D2R.exe registers them (24 TimeScale,
  25 TimeBase, 26 TimeRandomVariation, 31 GainMultiplier; docs/hdfx-vm-re.md). The engine calls
  `samplerVectorField:sample(pos)` and `sample(pos, t)` both sample at the **scene time** (t is ignored by D2R.exe):
  `SimContext.scene_time0` + the sim clock.
- `RandCtx(seed)`: `.rand(lo, hi, n)`, `.vrand(lo, hi, n)`, `.uniform(n, k)` (deterministic per seed; not the engine's generator).
  `value_noise(p)`: `noise(p)` of the engine (1 column: 1D simplex, 3 columns: 3D simplex; the exact kernels of `fx/noise.py`).
- **Engine calls for the vm lane**: `samplers.register(vm.ENGINE)` installs `samplers.ENGINE_CALLS`, each `fn(ctx, sampler,
  *args)` (the sampler is the class operand; an object with a `.sampler` attribute is unwrapped): `samplerCurve{1..4}:sample_float`,
  `samplerCurve{1..4}:derive_float_pCtxI`, `samplerVectorField:sample_float3_float_SceneCtx` / `..._float3_SceneCtx` (time
  defaults to `ctx.time`), `samplerShape:samplePosition_RandCtx` / `samplePosition_int3` (pcoords arg), `sampleNormal_*`,
  `samplePCoords_RandCtx`, `rand_float_float_RandCtx`, `rand_float3_float3_RandCtx`, `rand_float4_float4_RandCtx`,
  `vrand_float_float_RandCtx`, `vrand_RandCtx`, `noise_float`, `noise_float3`, `fast.noise_float`. The ctx provides `rand` (a
  `RandCtx`; else a default stream), `count` (particles, for calls with scalar arguments) and `time`.

## 3. Rendering (`fx/render.py`, `fx/packers.py`, `fx/rspec.py`, `view/fx.wgsl`)

```
eff = rspec.load(bytes_or_path, platform="PC")         # RenderEffect: renderers (RSpec: index, obj, kind, material, enabled,
                                                       # layer, props {name: value}, inputs {name: Input(field, type, const)})
                                                       # via graph.load when importable, constant inputs from the layer table
fxr = FxRenderer(gpu: view.gpu.Renderer, vfs, post=Post())
draws = fxr.prepare(eff, state: SimState, cam, transform=None, only=None, hide=())   # -> [FxDraw], sorted; fxr.lights
img = fxr.render(draws, cam, size, base=gpu.Prepared|None, light=gpu.Light|None, post=Post(rig, gain, exposure=None),
                 origin=None, ss=2, bg=None, linear=False, floor=None)
```
Mip bias (lane r3-mipbias): `Post.quality` (`fx/mipbias.Quality(transparency, anti_aliasing, global_bias)`, a dict with the Settings.json keys, or None = the test copies' visual profile 4/3, env `HDFX_TRANSPARENCY_QUALITY` / `HDFX_ANTI_ALIASING`) picks the game settings the texture LOD bias follows; `Material.draw_resolution` holds the renderer's `General.DrawResolution`.
Exposure (lane polish; was `Post(rig, gain, hold_exposure)`): `Post.exposure` is the game's cb10[0].x (None: the rig's,
else `render.GAME_EXPOSURE` 0.073125, calib r3). The Uber output is in the game's pre-exposed target units; the viewer's
target is scene-linear, so it is scaled by `Post.target_scale()` = gain / the rig's exposure; PhysicalExposure effects by
PhysicalIntensity / pi x cb10[0].x. `bg` with a rig: the shot goes back through the rig's tone curve to scene light, the
render is added there and the sum tone-mapped (`render.hdr_over_bg`). `floor=y`: an invisible depth-only ground plane at
height y (soft particles and depth tests see it). The capture's camera and unit placement: `hdkit.fx.calib.gamecam`
(`GameView`, fx CLI `--game-view`).
`state.layers[layer].renderer_inputs[renderer index]` = {input name: (count, n) array}; the packers read the renderer input
names (`packers.INPUT_MAP`: `Color.Color`, `Blend.BaseUVScaleAndOffset`, ..., plus `Position`, `Scale`, `Orientation`,
`Size`, `Rotation`, `TextureID`, `Axis`, `Axis2`, `Width`, `TextureU`), taken from `ParticleBatch.renderer_inputs` (lane vm),
else mapped from the streams by `render.inputs_for`; `Enabled` 0 particles are not drawn; `SimState.lights` become point
lights in the game's form (lane calib3, read from the terrain shader's light loop and its light records): colour =
Light.Color x Power / (4 pi), falloff `max(1 - (D/Range)^e, 0)^2 / (1 + 0.0929 D^2)` with e = 8 x AttenuationSteepness,
diffuse albedo / pi x N.L (lane calib4 checked it on four more records: Charged Bolt's bolts, e 0.992 = 8 x 0.124, colour 1500 / (4 pi); light_cast_1, Power 9000, R 10, e 2.4; fire_cast_1 and ice_cast_2, e 4.0; each at its script's local position, e.g. 7 units above the caster's root) (`packers.FxLight.point()` passes them with a negative `atten` = -e, which `view/shaders.wgsl`
and `fx.wgsl` `point_falloff` read as this form). Over a game shot (`render(..., bg=, plate_light=PLATE_IRRADIANCE)`, fx
CLI `--plate-light R,G,B|off`) the lights relight the shot at the visible `--depth-unit` surface or at the ground
plane elsewhere using shot x (1 + E / plate irradiance). `plate_gain(..., n=...)` takes the corresponding normal.
The default irradiance was fitted on Fire Bolt's pool in Blood Moor at night. Lane calib4 refitted its scale using
Fire Bolt and Charged Bolt (the bench's ground light round the caster). A `.particles` file holds four platform graphs (PC, Switch,
PS4, PS5), each with its own renderer copies: only the PC graph is drawn.
Transparent draws composite over the opaque scene of `gpu.Renderer` through its `overlay` hook (`render_rect(..., overlay)`:
the depth is kept, resolved to r32float for soft particles and decals, then the effect pass draws into the same MSAA
target). Lights become `gpu.Light` point lights. Uber permutation = `Material.of(rspec)` (switches, ops, blend kind, soft
distance, textures by slot); `Material.keywords()` gives the game's keyword set (`UV_DISTORTION, ALPHA_REMAP,
GRADIENT_REMAP, FRESNEL, SOFT_PARTICLES, POPCORN_ADDITIVE/MULTIPLICATIVE/BLEND/BLEND_MASK`); WGSL takes them as uniform
switches. The maths in `view/fx.wgsl` is approximate (every guess marked GUESS) until `docs/hdfx-shaders.md` lands.
**Materials** (lane mats, `docs/hdfx-shaders.md` 8-13): `shad.material_family(material, kind)` picks D2R's class by
name (uber, smoke, liquid, dmesh, standard, stencil, default, light, decal; None = not drawn) and
`shad.material_keywords(get, kind, material, inputs)` its keywords; `Material` carries `family`, `lit` (WGSL lit mode),
`wrap`, `bend`, `entry` (fragment entry: `fs_fx`, `fs_lit`, `fs_liquid`, `fs_dmesh`, `fs_stencil`; decals: `fs_decal_mul` + `fs_decal_add`, `FxDraw.decal_rec` = `fx/decal.Record`, lane r3-decal; since lane r3-decal2 `FxRenderer.render` groups them into
`fs_decals_mul` + `fs_decals_add` (`fx/decal_pass.group`), so `fs_decal_*` only run when a caller draws a decal draw itself), `opaque`
(m 4 default meshes and standard meshes write depth), `liquid` (liquid/stencil constants), `tint` (u.tint colour x
alpha). `standard_material(mat, mesh)`: a Standard_Mesh draw takes its model's maps; a model whose material is a
`WaterMaterial` (`gr2.material_params` "water": Shallow/DeepAlbedo, alpha, Roughness) draws as water
(`render.water_material`, lane r3-calibfix: the cave mana shrine's white disc; since lane r4-water the game's water_p:
fragment entry `fs_water`, blend 3 (premultiplied over), `Material.water` = `render.water_uniforms(params)` (23 floats since r4-glow; the first 19 in
the decal box slot u[60:76] and `xv.yzw`; the game's cb4 order checked on capture r3-calibfix r4 eid 19928). The water's
thickness over the scene depth (view axis, as soft particles) picks shallow/deep colour and alpha (`WaterTransmissionDistanceMax`)
and the shore foam (`ShoreFoamDepth`, FoamMaskTexture); the normal combines the flow and detail normal maps; out = lerp(what is
behind, lit water, F + (1 - F) x alpha). The game samples the scene at the water's own pixel (no normal offset), so this
blend is its whole "refraction". **The flow** (lane r4-glow): `Material.water` has 23 floats, 19..22 = the flow vector
(`render.water_flow_vector`: the flow map default_WATER (255, 127, 0) through its sRGB view, turned by WaterFlowDirection
as radians, x WaterFlowIntensity), WaterFlowNormalSpeed, ShoreFoamAnimationSpeed. With `UseFlowTexture` and a
`WaterFlowTexture` map the first two are the rotated +X basis, and `fs_water` uses it to rotate the model's sRGB
flow texel (mesh UV, point clamp, mip 0). `render.water_time_uniforms(water, t, flow_map=False)`
fills u.ops / u.fres per draw from `FxDraw.time` (= `SimState.t`, the effect age; the game's clock is cb10[2].w): two
phases half a cycle apart + 0.5 simplex(xz / 10), the foam mask scrolled along (-1, 0) (cb10[55] of capture 4268, GUESS:
constant). A draw's own material entry now wins over its renderer's in `FxRenderer.prepare`). **ModelInstanceTracking**
(lane r4-glow, `fx/modeltrack.py`): `scene.Unit.tracked_fx` = (part, effect) of the object effects no mask/strip layer
takes; with `--unit` the fx CLI simulates them (`TrackedEffects`, `--object-fx on|off`) and a mesh renderer with
ModelInstanceTracking and no `General.Mesh` draws the part's posed world meshes (`gpu.Renderer.last_posed`, set by every
`Renderer.prepare`) once per particle, through `FxRenderer.tracked`. For a selected effect, `--unit` supplies the
posed meshes and `--timeline <mode> --unit-mode <mode>` uses the event's `unitPart` and start time. The fx CLI also loads
equipment with object effects from a wearer's `UnitAttachmentComponent` (`scene.load_unit(..., equipment=True)`).
The item mesh follows the wearer's posed `overrideBoneName` and the authored `attachmentTransform`. Effects declared
on the item root bind to its models. Effects declared on a model entity bind to that model. An equipment context in
`tracking_contexts` has `unit`, `item`, and `entity`. Coverage reports mesh-less tracking as missing without a usable
context and uses `unreferenced` when the fired-by index assigns that status to the exact path. An explicitly selected
entity effect can pose its own model without adding an opaque copy to the scene. `--object-fx off` suppresses
the unit's automatic object effects while previewing the selected effect. **Attribute switch** (lane r4-glow): fx CLI
`--attr-before NAME=V@SECONDS` (`fxcli.preroll`): static sims run SECONDS with that value, then take the `--attr` one;
effect times count from the switch (a used shrine: `--attr ObjectState=1 --attr-before ObjectState=0@3`).
A bench runner forwards the switch to the viewer the same way. For successful object operations, `object_switch_time`
estimates the reply frame from the 25 Hz harness clock, bounded by the recorded request and reply wall times when
available. `--launch-t` can supply an explicit timestamp. **Sibling VFX entities**: the fx CLI previews
the unit's other VFX entities alongside one of its bone-bound effects, including the Wraith's wrist glows. Use
`--unit-fx off` to isolate the selected effect. **Depth-only scene objects** (lane r4-water): `gpu.Prepared.occluders` are drawn
into the scene depth only (no colour, directional shadow or aura stencil; they do cast into the shadowed point's cube,
lane r5-playerlight), after the opaque draws; fx CLI `--depth-unit <object or
unit JSON>` (repeatable) adds one placed as the effect (its yaw and `--offset`), e.g. the cave shrine's basin over a game
shot, so its rim hides the pool's edge and the water reads its depth. Game placement note (one capture so far): a map object sits on its
sub-tile corner and the player on its sub-tile centre (capture r3-calibfix r4: player 2 x sub-tile + 4.733, shrine + 3.733),
so the effect offset for an object is `near.at - player - 0.5` (the shrine: `-3.5,-3.5`, not `-3,-3`). The vertex format has 16
attributes (`packers.FLOATS` 62: `so_x` = `AlphaRemap.DissolveUVScaleAndOffset`); `packers.particle_block(inputs, n,
family)` maps the liquid/default-mesh/stencil inputs onto the Uber attributes (`INPUT_MAP`, `FAMILY_DEFAULTS`, and `FAMILY_INPUT_MAP` per family: a liquid's `EdgeColor` goes to `so_db`, its distortion is the Mask* pair only; lane calib5);
`pack_billboards(..., bend=)` bends normals (NormalBend). The uniform block is 260 floats: the Uber 96, then
`render.mats_uniforms` (lit mode, scene key/ambient/env light, liquid constants, up to 16 point lights). `Material.aura_stencil` (EnableAuraStencil on an Uber-class renderer; lane r3-aurastencil): `FxOverlay.encode` draws those
draws against a stencil copy of the depth that the base scene's unit draws (`gpu.Draw.light_class` "unit", given as
`FxOverlay.units` by `FxRenderer.render`) mark, with the game's test (GreaterEqual ref 0 mask 0x30: hidden over units);
`FxRenderer._pipe(entry, blend, stencil=None)` takes the extra depth-stencil state. `render.layer_batch(state, r)`: a renderer's own layer batch (by its index in the batch's `renderer_inputs`; layers can share a name). `render.SUPPORTED_KINDS
/ _MATERIALS / _FEATURES` feed `coverage.SUPPORTED_RENDER_*`.
**Atlas extras** (lane r3-atlasalpha, docs/hdfx-shaders.md 4.1-4.2): `Material.atlas` carries `alpha_only`
(`AlphaMask.AtlasAlphaOnly`, billboards) and `flipbook` (`Atlas.Flipbook`); uniforms `inv.w` (u[43]) AtlasAlphaOnly,
`exq.w` (u[51]) Flipbook, `vp.zw` (u[94:96]) Atlas.SubDiv; vertex `misc.y` += the game's atlas cell `packers.atlas_cell(TexID)` + 2048
(`pack_billboards(..., alpha_only_subdiv=)`), the VS passes the cell origin in VOut `misc.w` / `dist.z`. With AtlasAlphaOnly
only the alpha mask samples the atlas cell; every other texture the cell-local uv. With Flipbook the material textures
ignore their UVScaleAndOffset.
**Colour variants** (lane r3-colorvariant; `fx/colorvariant.py`, `docs/hdfx-shaders.md` 14): renderers with
`ColorVariant` recolour each particle by its `ColorVariant.TransformIndex`, which the scripts copy from the effect attribute
`ColorVariantIndex` (the game: 37 + the unit's state colour shift, e.g. cold 145, poison 141; 0..7 none). The table is the
game's `hd/global/palette/randtransforms.json` + `transforms.json` read through the renderer's vfs (`--root casc`).
Vertex format (integration r3, shared with AtlasAlphaOnly): `misc.y` = (atlas cell + 2048) + 4096 x round(variant exponent x 256)
(`colorvariant.pack_misc_y` / `unpack_misc_y`; exponent 0 = none), `dist.z` = its packed tint; the VS passes both in the
flat VOut `cv` (location 15). Uniform `xv.x` (float 256, after the mats block; the uniform block is 260 floats) =
`Material.color_variant` (VariantContribution). Choose: CLI `--color-variant cold|145|shift:104|rand:3`,
`--attr ColorVariantIndex=145`, `fx_render(attrs={"ColorVariantIndex": "cold"})`, MCP/web `fx_color_variants`.
CLI: `python -m hdkit.view fx <effect> [--unit <json>] [--clip neutral] --t 1 [--frames N --dt 0.08] [--jpg]`.
**Animated meshes, ribbon UV flags** (lane r3-animuv): `fx/animmesh.py` skins a mesh renderer with
`Animation` on (clip = the `Animation.AnimationFile` stem inside `<its folder>/combined.animations`, model skeleton
re-parented on `Animation.Skeleton`, root translation kept; clip time = particle age (`self.lifeRatio / self.invLife`)
x `Animation.Speed`, `LoopCount` plays then the last frame holds, 0 = forever); `FxRenderer._prep_mesh(..., batch)`
calls `animmesh.skin_groups` / `pack_groups` (particles grouped by clip time, 1/120 s). `packers.pack_ribbon(...,
rotate=False)` and `packers.ribbon_uv(t, side, flip_u, flip_v, rotate)`: PopcornFX's billboarder UV flags as D2R packs
them (`TextureUVs` off: no flags); `TextureRepeat` is not read by D2R (TextureU unnormalised + wrap samplers).
**Decal emissive and the `Emissive` coverage name** (lanes r3-decal2 and r3-animuv, merged at the r3 integration):
`Emissive` is a decal-only feature in the corpus (20 effects). One implementation: `fx/emissive.py` holds the game's
record bytes (`encode` / `decode`, ps_terrain flag 128; intensity 100..1e6 log, focus exponent ~ 8 x Focus) and
`fx/decal.pack` / `Record.em_intensity` / `em_power` use it; drawn by `fs_decals_add` / `fs_decal_add` (record flag 128,
the map in the decal emissive texture array). r3-animuv's `fs_decal` hook, `FxDraw.emissive` and texture slot 5 for
`Decal.EmissiveMap` are gone.
Added by lane r3-aura2: `--unit-mode nu --unit-wclass stf` (the unit's clip for that mode from its state machine,
`sceneunits.mode_clip`; with `--unit-period` the game's loop length, e.g. the classic NU 8 frames at speed 128 = 0.64 s),
`--scene-units <json>` (more units drawn with the `--unit`, light class "unit": they mark the aura stencil too), and the
effects' `view.position` is the render camera's `vm.game_eye(el, az)`.
`--ground depth` is the default for bench renders (was a hard-coded `none`): soft particles fade against the
terrain as in game. Concentration's ring (pushed 1 unit towards the camera, SoftnessDistance 3) is drawn at 1/3 alpha in
game; without the ground the viewer drew it at full alpha, 3x too bright (capture a2 frame 9412: same inputs, same PS).
**AlphaRemap.Dissolve** (lane r3-shapes; D2R's uber mesh PS 1375 and billboard PS 3647, `cb4[6].x`): with the ALPHA_REMAP
permutation and `Dissolve` on, `f = sat(Dissolve.r(uvM x AlphaRemap.DissolveUVScaleAndOffset) - AlphaCursor.x)` and the grey
and the alpha each become `sat(x f)`; only without Dissolve is the alpha `AlphaMap(alpha + cursor.x, cursor.y)`. The viewer
did the AlphaMap lookup always, which turned e.g. the Pain Worm chest glow (cursor -1: f = 1) into a uniform hard disc
(alpha = AlphaMap(0, 0) everywhere). `Material.dissolve`, uniform `lq2.y` (the liquid path's flag, now set for the Uber path too).
**Effects on unit bones** (lane r3-shapes, `view/fxbones.py`): with `--unit`, `--bind bone` (default) plays one Sim per binding
the unit has for the effect (`context.effects_of_unit(...).vfx()`: entity, object and name-bound overlay triggers) on that binding's
bone of the drawn clip (`ClipPoses`: the same `gr2.pose_at` at `--unit-t0 + t x --unit-rate`, or `--time`; `--game-view` root
offsets included), moved every `--sim-dt` step through `drive_to` (transform and velocity). World = lift place x rot_y(yaw) x root
scale x bone world x entity transform, as the unit draw. `--bind origin` is the old placement (the unit's origin turned by its yaw).
metrics.json `driver.bones` lists each binding (label, bone, skipped bones). Ported at the r3 integration: `fx_render`
context `{"kind": "unit", "unit", "clip", "t0", "rate", "yaw"}` (`api.unit_bone_driver`: one BoneDriver with a layer per
binding) and the web editor's context "unit (its own bones)"; `fx --cast` already plays the cast's overlays on their
bindings' bones through `context.skill`.
**Units whose every model entity has an object effect** (Wraith torso/l_torso/r_torso, colbarbwhirl): `scene.load_unit` now draws
those models (the `UnitPartComponent` ones, else all; entities named `*shell*` stay effect-only) as the body when the unit has no plain model part; before the unit loaded
empty (no body, no skeleton). Units with plain parts still skip object-effect models (Fire Golem / Pain Worm shells).
Scene JSONs: `scene.vfx_entities(doc)` lists VfxDefinitionComponent entities with their transforms (`Unit.effects`).
Shader keywords and the blend mode come from `hdkit.fx.shad.uber_keywords(get, kind)` and `shad.blend_mode(get)`
(D2R's own rules) and the maths from `docs/hdfx-shaders.md` (order, constants, per-instance inputs).

**The unit under an effect** (lane body; `view/gr2.py`, `view/scene.py`, `view/gpu.py`, `view/export_web.py`): a model's
meshes, and each mesh's material groups, draw with their own textures, as the game draws them (one `models_p` draw per
group). `gr2.Mesh.groups` = [(tri_first, tri_count, maps, params)] (every group; `maps`/`params` stay the first group's),
`scene.Part.mesh_textures` per mesh per group, `Part.draw_groups(i)`, `gpu._material_draws(part, i, mesh)` (groups that
share textures and params stay one draw); the web bundle carries `meshes[i].textures` / `meshes[i].groups`. Before, every
mesh of a part used the part's first textures and only a mesh's first group was read: Mephisto's flesh (body_meshShape
group 0, `body_2_alb`) was drawn with `bones_ALB`, so his body looked pale in the side by side. Ground truth: RenderDoc r3
eids 24399 / 24407 / 24415 bind body_2 / bones / eyes_head_2 for index ranges 0 / 59778 / 122610 of body_meshShape.
16 of 55 stock lod0 character models checked have meshes with different albedos (goatman, bloodlord, brute2, snowyeti,
hellbovine, thornhulk, megademon, blunderbore, deathmauler, frogdemon, sorceress head, ...): their renders change too.
**Unit light** (lane unitlight; `fx/calib/unitlight.py`, `view/gpu.py` `Light.game` / `pack_game`, `view/shaders.wgsl`
`game_shade`): the game's `ps_models_p`, read from the capture (r3 eid 24399: SPIR-V through SPIRV-Cross
`--vulkan-semantics`, root CBVs from the extract's `bda_<push offset>.bin`). What a unit pixel gets, term by term:
- material: albedo (sRGB), ORM r occlusion, g roughness (min 0.04, alpha = r^2), b metal; F0 = mix(0.04, albedo, metal);
  KTINT recolour (identity for Mephisto); emissive = albedo x (1 - ORM a) x cb8[3] (0 here).
- directional lights (root CBV 14, view space, up to 3 + a loop): colour x intensity, diffuse and specular weights,
  shadow mode. r3: (1, .90, .70) x 9.66 from 47 deg up; **player_key_light (1, .94, .88) x 30**, the camera-side key at
  (0.60, 0.80, 0) world; (.90, .92, 1) x 4 from the left. Per light: N.L x colour x (dw x albedo x occlusion x (1 - metal)
  x (1 - F) / pi + sw x D F Vis (1 + ms)); GGX D, Schlick F, Hammon's Vis, multiscatter ms from the split-sum LUT.
  Occlusion darkens direct diffuse too (the old viewer model: ambient only). The 1 / pi is the game's.
- local lights (clustered: tile words bits 6..15 point, 16..23 spot, 24..31 shadowed point; records in a typed buffer,
  view space): falloff max(1 - (d / radius)^e, 0)^2 / (1 + 0.0929 d^2), same BRDF. At Mephisto: his own unit light
  (33, 33, 40), radius 21, e 5, 10.3 above his feet; the player's light (159 = 500 / pi grey, radius 34.7, e 5.5); three fires 20-40 units away (only their edges reach).
- ambient (root CBV 6): SH irradiance (9 rgb, world axes) x occlusion / pi, diffuse x albedo x (1 - metal) x
  (1 - Fenv (1 + ms)), specular Fenv x (occlusion x cube(R) + irradiance x ms); all x iblIntensity 0.4 (diffuse and
  specular weights 1; screen-space AO off). Night-sky SH: L0 (3.66, 3.80, 4.06), y (4.50, 4.70, 5.08).
- baked light volumes (3D textures, cb5 flags 343 / 347): off. Directional shadow: the whole direct sum x
  1 + 0.9 (shadow - 1) (light 1 twice); shadow maps: see **Shadows** below (without them `direct_scale` stands in).
- x exposure cb5[0] (0.073125), then **the unit value curve** (cb7 floats 72..79, on for this draw): HSV, V' = (V + 0.4
  poly(sat V)) (1 + 0), poly = (0, 0.36, 3.2, -2.56), the cubic through (0, 0), characterContrastPoints (0.25, 0.25),
  (0.75, 0.99) and (1, 1). So **characterBrightnessFactor 0.4 is the curve's strength, not a light factor** (the `game`
  rig's key = 30 x 0.4 was wrong), and the poly is P itself, not P - V (`tone.value_curve`'s hypothesis).
- post (RenderDoc eid 28385 "Postprocess", its pixel shader in full): display = CLUT(PQ(exposed x 0.008)), one 65^3
  RGBA16F "Universal CLUT" (tone curve + grading, built per frame by cs_lut_combine_c), sampled trilinearly at
  PQ x 65 - 0.5 texels; its output is already display-encoded (sRGB). Emulated from the dumped LUT it matches the pass's
  output to 0.0008 mean abs over the frame and the game shot's flesh within 1.5 / 255. Viewer: tone `clut`
  (`tone.clut`, rig `post.clut` = the LUT as .npy). The `game` / `lut` tone modes are 5-20 % too bright against it
  on the flesh (green and blue most).
- check against the capture (viewer at the capture's pose and pixels vs the flesh draw's own float target, eid 24399):
  with no shadow the viewer's exposed flesh is 1.20 / 1.24 / 1.30 x the game's (R, G, B); ambient alone is 0.10 x.
  The excess sits in clustered patches (his arm's shadow on the chest, crevices): implied direct visibility is ~1 on
  52 % of the flesh pixels and < 0.3 on 25 %. So the gap is the directional shadow map; the rig's `direct_scale` 0.65 (one
  number, fitted so the capture frame's flesh display luma matches: 47.7 game, 50.0 at 0.7, 60.6 at 1.0) stands in
  for it. With shadow maps on (**Shadows** below) the direct scale is 1 and the shadows are per pixel.
The viewer does the same in `game_shade` when `Light.game` is set (uniform floats 316..399, `gpu.GAME_AT`; the uniform
block grew from 1280 to 1600 bytes, so web bundles exported before must be exported again). Stand-ins, said plainly:
Karis's analytic fit for the split-sum LUT, the SH at the reflection direction / pi for the specular cube, no shadow
maps, no screen AO, no specular-AA widening. Local lights go through `point_falloff` (the capture's with atten = -e).
Make a rig: `python -m hdkit.fx.calib unitlight <draw dir> --game-view game_view.json [--clut clut.npy]
[--direct-scale S] --out rig.json` (lights to world
axes with the capture's view, local lights within reach of the unit, positions relative to its origin); use it with
`--rig rig.json` (Mephisto's rig from one capture used direct_scale 0.65). Rigs without `light.game`
(every preset, `vis:`) render as before.

**Shadows** (lane r3-shadows; `view/shadow.py`, read from the r3 capture: ps_models_p's shadow code and the frame's
`Shadows` passes). The game: one 4096 x 4096 depth array of two orthographic slices, both along the main directional light
(the moon in r3), reversed depth (1 = the near plane): slice 0 `Shadows / DirectionalLight` holds the static world (props,
walls, plants, placed objects; no units), half extents 168 x 109, near 1, far 263; slice 1
`Shadows / NegativeDirectionalLight` holds the units (dithered for fades), half extents 97 x 65, near 130.2, far 240.7; the
light's eye 189.4 units from the unit's feet. Receiver: world position + 0.05 x normal, filter by the slice's mode (1 one
tap, 2 four at +-0.5 texel, 3 eight Poisson taps at 2 texels, 4 / 5 PCSS with 8 / 16 taps: blocker search radius light size
x (d - near) / d, penumbra (d - d_blocker) / d_blocker x light size x near / d, light size 1.5 units; both slices use 4).
G = 1 + 0.9 (s1 - 1); directional light k with shadow index 0 gets G x s0, index 1 gets G, none 1; then the whole direct sum
(directional and local) x G again; ambient and image light are not shadowed. Casters among the effects: `Shadows` (Uber
billboards/ribbons/meshes through ps_popcorn_uber_shadows_p: the material's alpha, discarded below
`Shadows.ShadowThreshold`; Standard meshes as the engine's model instances). **`Lit.CastShadows` casts nothing** (lane
r3-shadows2, from the binary: the Default and Uber material set-ups read only `Shadows` and `Shadows.ShadowThreshold`,
the threshold 0.0 when absent; `Lit.CastShadows` / `LegacyLit.CastShadows` / `CastShadows` are registered by PopcornFX's
RenderHelpers and never read; `shadow.LIT_CASTS_SHADOWS` restores the old guess). Receivers: units, the ground and
Standard meshes; lit particles read no shadow map (their shaders have none). Depth clamp (the casters' depthClampEnable):
emulated in the caster shaders (clip z kept inside, frag_depth = the clamped map z), so casters before the near plane
still cast. **The ground's light set** (r3-shadows2, s2 terrain draw 13692 against the swarm unit's 13030): the terrain
gets only the main directional light (shadow index 0, the moon), not the units' player key light x 30 or the fill; the
same SH, exposure, local lights and shadow composition (ps_terrain's shadow code is ps_models_p's). `ground_terms` uses
`shadow.terrain_dirs(Light.game)`: `terrain_dirs` (indices) when given, else the shadow-index-0 lights.
**Local-light shadows** (r3-shadows2, from ps_models_p / Global Attenuation): spot lights: cone clamp((dot(axis, -L) - c) /
(1 - c))^2 (c = record word 11 low 16 bits / 65535), shadow index 0..3 into a 2D-array map (perspective, reversed depth,
static world casters only in r3's `Shadows / SpotLights`), receiver = position + 0.2 x normal, 4 compare taps at
(u - a | u + b, v + b | v - a), a, b = cb5[194], [195] = 3 / 1024, 1 / 1024. Shadowed point lights: one cube (index 0
only; -1 = none), compare value (distance - 0.1) / 2000 on the light -> receiver direction, 4 taps a, b = cb5[192], [193];
every shadowed point light in the s2 and r3 captures (the player's light, Mephisto's) has index -1, so the game drew no
cube. Viewer: a local light dict with `spot` = {"dir" (travel direction), "cone", "shadow"} or `cube` = 0 (kept by
`gpu.game_points`); `shadow.local_records(light)`, `spot_matrix`, `cube_face_matrices`; the receivers call
`shd_local(light pos, wpos, n, shadowed)` in their local-light loops (records matched by position; the stub returns 1, so
the browser page draws spots as points). Spot map 512 x 512 (compare Greater, depth clamp on) and the spot projection (reversed infinite perspective, near 1, z / w = 1 / distance, x / w and y / w = uv) are from the r1 capture's forge spot (`spot_matrix` reproduces its matrix); the cone word mapping c = cos(angle / 2) is a GUESS (no spot was on screen in any capture), and the cube size (512) is a GUESS. `shadow.spot_from_preset(comp, xf)` builds a spot record from a preset light: its axis is the transform's +Z (the game's forge spot; `mapview/hd.py preset_lights` uses +Z too since the r3 integration, it used -Z before).
Viewer: `gpu.Light.shadow` (dict; `{"on": true}` = the game's set-up above, keys of `shadow.GAME` and per-slice `slices`
override, `direct_scale` replaces Light.game's, default 1). `shadow.spec_for(light, centre) -> Spec` (slices, `lights`
= each directional light's shadow index from `Light.game.dirs[k].shadow_mode`, the global slice, matrices, uniforms); with
no `Light.game`: one slice along the key light holding every caster, read by the key light. `gpu.Renderer.shadow`
(`ShadowMaps`) renders the casters before the main pass of every `render_rect` (slice casters by draw light class: `world`
draws into slice 0, `unit` draws and effect casters into slice 1) and is bind group 1 of the scene and effect pipelines:
`shaders.wgsl` / `fx.wgsl` carry a no-shadow stub block (`// @shadow-stub-begin .. end`, what the browser page runs) that
`shadow.patch` swaps for `RECEIVER_WGSL`. `FxDraw.cast` = `shadow.caster_threshold(renderer)` (None: casts none). Over a game
shot (`FxRenderer.render(..., bg=)`) the ground gets `ShadowMaps.ground_gain` ((ambient + shadowed direct) / (ambient +
direct) on the plane y = floor, x the plate light gain). fx CLI: `--shadows rig|on|off`; `metrics.json` `shadow` holds the
spec and the casters per slice. Interface change (this lane): bind group 1 on `gpu.Renderer`'s and `FxRenderer`'s pipelines.
Interface change (r3-shadows2): bind group 1 grew bindings 3 (spot maps, depth 2D array), 4 (cube, depth cube), 5 (cube
compare sampler, less-equal); ShdU grew to 224 floats (`loc`, `loc2`, `lrec`, `sm`); the stub block gained `shd_local`;
`ground_terms` returns (dir, rgb, shadow index) triples.

**Shadows with scene lighting and decals** (r3 integration; lighting2 merged and the shadow maps fed into `visibility` by
lane r4-lighting3): bind group 1 of an effect pipeline is the shadow maps (`gpu.shadow.bind`), except the two decal-pass
draws (`fs_decals_mul` / `fs_decals_add`), whose own pipeline layout puts the decal records and texture arrays there
(their module is the un-patched `fx.wgsl` + `fx_decals.wgsl`, so its shadow stub returns 1). Over a game shot with a
`scene:` rig, `FxRenderer.plate_shadow_terms(..., surface=(P, n))` encodes the maps before the pass (caster only, then
caster + the overlay's effect casters) and returns `ShadowMaps.ground_vis` of both (vis, vis_c). The optional
`surface=(P, n)` takes the same per-pixel world positions and normals as the plate relight, including visible depth
units. With no surface, visibility is sampled on the ground plane. The plate gain is
`GameLighting.plate_gain(P, fx_points, visibility=vis_c, visibility_fx=vis, n=n)`, made once and used by the
decals' plate and the composite alike (no `ground_gain` on top); the decals' plate irradiance is `L.irradiance` at the
origin under vis_c of the origin pixel. Other rigs: `plate_gain(..., PLATE_IRRADIANCE)` x `ShadowMaps.ground_gain`. Units
take their directional shadows in the shader (`shd_light` / `shd_global` per light's shadow index). fx CLI
`--shadows rig|on|off` applies on top of `scene:` rigs.

**Scene lighting** (lane hdfx-r3-lighting; `fx/lighting.py`, `view/ibl.py`, table `fx/ibl_sh.json`): one setup for every
scene from the game's data, no per-scene factor. `L = lighting.for_biome(fs, biome, tod, player=(x, y, z), extra=[...])`
-> `GameLighting`; `L.rig(mask, clut, unit=(Light, (r, g, b)))` -> a `view.rig.Rig` with `.lighting`; fx CLI / fxcast
`--rig "scene:<biome>;tod=1.54;mask=19;unit=7,212,212,255;player=-10,0,12;clut=<lut.npy>[;plate_player=0][;light=x,y,z,r,g,b,R,e]"`
(the plate relight then uses `L.plate_gain`, not `PLATE_IRRADIANCE`, which stays the fallback for other rigs). From the data:
the biome's six vis files blended at `tod` (0..6 = dawn1, dawn2, day, dusk1, dusk2, night; numbers linear, orientations
slerp: the captured key light gives one weight, 0.53-0.55 past dawn2 on every calib bench, so the benches are morning,
not night); directional lights by lightMask (terrain bit 1; models their own mask); SH ambient = the irradiance projection
of the biome's `iblSettings.cubemapFilename` top mip (BC6H cube, D3D face axes = world; equals the captures' CBV 6 to
0.004 over 27 numbers on act 1 outdoors and caves) x iblIntensity; exposure from EV; the player's light
playerUnitLightPower / 4 pi, radius 26 x playerUnitLightDistanceScaler, exponent playerUnitLightAttenuation, 8.0 above
the feet; a unit's light rgb / 255 x unitLightPower / 4 pi, radius 2 Light x unitLightDistanceScaler, height radius x
unitLightDistanceOffsetRatio (Uber Mephisto reads Light 7); the value curve from characterContrastPoints /
characterBrightnessFactor. Units: `Light.game` (dirs, SH, ibl, exposure, value; `points` empty); the local lights go to
`Light.points` in FxLight form (colour / pi, atten -e, `"fx": True`), which effects' lit materials read and `gpu` passes
to `game_shade` x pi (also fixed for every effect light on a game-lit unit: they had lost a second 1 / pi).
**Shadowed point lights** (lane r4-lighting4): the player's light and a unit's own light are the game's shadowed points
(`"shadowed": True` on their `Light.points` / `Light.game.points` dicts; capture rigs: kind `shadowed_point`). ps_models_p
runs that loop only when cb7[80] bit 1 is set, and no captured model draw has it (5 or 9 everywhere), so
`gpu.model_points(points, game)` drops them for game-shaded models; the ground (`L.irradiance`, terrain draws) and
effects keep them. `Light.game["shadowed_points"]` True (a capture with bit 1, `unitlight.read_draw` "model_flags") keeps
them. **Inside levels** (lane r5-playerlight; spec `;inside=0|1`, default on when the biome name holds `cave`): every
captured terrain draw has cb7[80] = 19 (bit 1: the player's light always lights the ground, x the shadow G); in the cave
captures its light record has cube index 0 (outdoors -1), so the player light carries `"cube": 0`, the cube pass renders
it and `ShadowMaps.ground_vis` returns a 4th channel, that cube's visibility (`lighting.cube_term`; `shadow_terms` takes
(..., 3) or (..., 4)); and the terrain's ambient is x the Global Attenuation surface (cb6[1] = 1 in caves, 0 outdoors),
`lighting.light_radius(points, P, exposure)` = smoothstep(clamp(exposure x sum max(rgb) x falloff)) over the scene's and
(in `plate_gain`'s E1) the effect's lights. `irradiance(..., fx=())` takes those effect lights in game units. A bench runner
puts the player at -2 x the effect offset (x, z) in a `scene:` rig without `player=`. Spec `;event=<name>` selects a fully active visual event from `hd/env/weather/vis_event_map.json`
while retaining the biome's IBL cube. Use `scene:act1_caves;event=DenOfEvil` for a Den of Evil capture.
The event's vis sets IBL strength 1.0 and a red key light of power 4. The cave's time-of-day vis sets IBL strength 2.0
in the Pit captures. Spec `;ibl=<x>` overrides the active vis IBL strength.
Use `--rig game` for the generic game rig, which is also the fallback for benches without rig metadata.
**Screen-space AO** (lane r5-postpass, `view/assao.py`): the game multiplies the opaque scene by Intel's ASSAO (quality 2)
between the opaque pass and the transparents (markers "AO": the last "Prepare Depths" draw writes Main Color 0 with blend
Zero/SrcColor and the "Ambient Occlusion" target); this is the ×0.925 on Mephisto's upper flesh. `assao.ssao(z, n, ext,
C=None, box=None)` ports the game's shaders (deinterleave, smart mip of the stored depth, 12 tap pairs, wide smart blur,
edge-aware apply; constants `assao.GAME` from the captured cb). `gpu.Renderer.ssao(prep, cam, size, origin, light,
overlay)` runs it on lit renders of a game-shaded light (`Light.game`; `Light.game["ao"]` False turns it off, a dict
overrides `GAME`) from the scene's `depth` and `normals` passes at `size` (the game's full resolution for a game frame;
occluders drawn as the scene there), and `render_rect(..., ao=, occ_main=)` multiplies it onto the colour target after
the opaque and occluder draws, before blended model parts, glow layers and the effect overlay. Spec `;ao=0` turns it off.
Absolute view depth: the ground under the image centre is `GAME["z_centre"]` (884), as in the game when the image is a
game frame.
Water (`fs_water`) under a scene rig: ambient = iblIntensity x max(SH(up), 0) / pi and env = the same at R = reflect(-v, up)
(`render.water_image_light`, ps_water_p's image light with the SH standing in for the cube).
For a capture comparison, composite the viewer's water layer once over the game's pre-water input in the capture
target's exposure and measure the pixels drawn by both.
Ground of a game shot: `L.irradiance(P, n, mask, visibility=None)` = sum dirs x N.L (x visibility) + iblIntensity x SH(n)
+ local lights (game falloff), the terrain shader's sum before albedo / pi; `L.plate_gain(P, fx_points, visibility=None, n=(0, 1, 0))`
= 1 + E_fx / E(P) without shadow terms. **Interface for lane shadows**: `visibility` (0..1 per ground point, the directional shadow) in both;
`L.dirs(mask)[i]["shadow"]` (shadowCaster), `L.vis["keyLightShadowPitchDeg"]`, `L.vis["characterShadowDarkness"]`;
scene rigs keep `Light.game["direct_scale"]` 1 (no fitted stand-in). Not in the setup: shadow maps, the preset's map lights
and object lights (pass them as `extra`), the post LUT (the captured `clut_r3.npy` is the game's for gradingLutFile
default.dds, every act 1 vis file; `clut=` else tone `lut`). Rebuild the SH table (GPU) with
`python -m hdkit.view.ibl --root casc --out hdkit/fx/ibl_sh.json`. Checked on Fire Bolt, Charged Bolt, Frost Nova, Mephisto
and the cave mana shrine with one `tod=1.54`; open: no bloom
under tone `clut` (effect sprites 20-25 % dim in added light), shadows.

**Scene lighting, round 2** (lane hdfx-r3-lighting2; `fx/lighting.py`, `fx/scene_inputs.py`, `view/clut.py`,
`view/tone.py` game_bloom, `view/shadow.py` ground_vis). Interface changes:
- Shadows in the scene setup: `GameLighting.light()` sets `Light.shadow = {"on": True}` (spec `shadows=0` turns it off);
  the shadow index of each directional light follows the game (`lighting.shadow_index`: shadowCaster 0, lightMask bit 64
  (player_key / monster_fill) 1, others none = 4294967295, as the s2 capture's light records). `irradiance(P, n, mask,
  visibility)` now composes the game's terms: `visibility` = an (..., 3) array (G, slice 0, slice 1; what
  `ShadowMaps.ground_vis(light, cam, origin, size, y)` returns) or a dict {"G", "s": [s0, s1]}: a light with index
  i != the global slice gets G x s_i, the global slice's light G, none 1, then the whole direct sum (directional and local
  lights) x G; a plain array keeps the old meaning (a factor on the directional lights). `plate_gain(P, fx_points,
  visibility=None, visibility_fx=None)` = E1 / E0: E0 the ground's light with the caster's shadow (the shot holds it),
  E1 the same with the effect's casters too plus G x E_fx (effect lights are local lights, so G darkens them).
  `player_on_plate` defaults to None: the player's light counts on the plate exactly when the shadow terms are given.
  fx/render.py's scene plate uses `FxRenderer.plate_shadow_terms` (the last render's maps, and a caster-only re-render
  of the units slice when effect casters exist) and no longer multiplies `ground_gain` on top (other rigs keep it).
- Bloom: `tone.game_bloom(v exposed, cutoff, intensity)` is the game's chain (r4 capture draws 21234-21467: cutoff
  c / (1 + luma) above bloomCutoff, six 9-tap downsamples, tent upsamples added up the chain, final tent x 0.2 x
  bloomIntensity, every target stored as R11G11B10 (`tone.r11g11b10`, truncating)); it reproduces the capture's added
  light to 0.4 % (corr 0.99996). Scene rigs set `post["bloom"] = {"model": "game", cutoff, intensity}` (spec `bloom=0`
  off); `Rig.bloom(c)` / `Rig.game_bloom()`; tone `clut` now draws it in `encode_image`, and `hdr_over_bg` adds only the
  effect's part over a game shot (bloom(comp) - bloom(shot)).
- Post LUT from data: `view/clut.py` builds the Universal CLUT (cs_lut_combine_c FINAL_LUT_COMBINE=1 in numpy, constants
  `clut.PARAMS` from the r4 capture's Create CLUT dispatch) from any grading LUT .dds; `clut.register(fs, lut)` -> key
  `game:<lut>` that `tone.clut` reads (in-process). Scene rigs default to it (the gradingLutFile of the vis file the
  time of day is nearer to); `clut=<.npy>` still takes a file, `clut=lut` the old tone curve. default.dds build vs the
  capture's own CLUT: mean 0.00044.
- Scene inputs from a preset: `scene_inputs.for_preset(fs, preset, at=(x, z), radius, tod, player, fx_lights, objects)`
  -> GameLighting (biome from the preset; its and its prefabs' Point/SpotLights in the game's form, power / (4 pi) x
  colour, checked against the r3 capture's point133; placed effects' light renderers simulated at t; HD objects' effect
  lights; `at` becomes the origin); spec `scene:preset=<key>;at=x,z[;radius=40][;fxlights=0]`. Default time of day
  `scene_inputs.NEW_GAME_TOD` 1.54 (fresh games, every calib bench).
- `lighting.monster_light(monstats2 row, unique)`: unique / boss monsters get the "light" umod (+4 radius; Uber
  Mephisto's Light 3 -> 7 in capture r3).
- `mapview/hd.py preset_lights` puts spots along +Z (the game's axis), as `shadow.spot_from_preset` does.

## 4. Script language (`fx/lang/`)

Text files `.hfx`; `compile(src, effect=None, base=None, bindings=None) -> hd_cbvm.Script` (`base`: the script being
replaced, its externals/kind kept; pass it when replacing a script) (externals and engine calls resolved by name), `decompile(
Script) -> str` (readable, re-compilable for the opcodes it covers). Spec in `docs/hdfx-lang.md`.

## 5. API and MCP tools (`fx/api.py`, `view/mcp_server.py`)

Every function takes and returns JSON-able values; files are paths. Tools (names fixed; schemas in `docs/hdfx-mcp.md`):
`fx_list(pattern, roots)`, `fx_inspect(path)`, `fx_edit(path, ops, out)`, `fx_validate(path)`, `fx_create(template|layers,
out)`, `fx_script_read(path, script)`, `fx_script_write(path, script, source, out)`, `fx_render(path, t|clip, cam, context,
size, format png|gif|mp4)`, `fx_export(path, mod_dir)`, `fx_coverage(pattern)`. GPU ones pass `hdkit.gpugate` first, as the
existing `view_*` tools do. The web editor calls the same functions over `view/webserve.py` JSON endpoints. The editor page, its panels and what it needs from each lane: `docs/hdfx-web.md`.
Added by lane mcp: `fx_session(out, action)` (undo/redo/history of the edit session's working copy: every edit tool writes
a working copy, never the game file). Script ids are 1-based object numbers (as the listing prints them); errors are
`{"ok": false, "error": {code, message, needs}}`, `needs` = the lane whose module is missing. Op payloads (incl. the `color` op:
`stream`, `rgba`), the context dicts of `fx_render`, what the api expects of each lane's module ("Seams") and the web endpoints
(`POST /api/fx/<tool>`): `docs/hdfx-mcp.md`.

## 6. Context (`fx/context.py`)

Two halves; the module docstring lists every convention and assumption. Tables (missiles.txt, skills.txt, overlay.txt) come
from a folder the caller names (`Tables.from_dir`, CLI `--tables` or `HDFX_TABLES`): nothing mod-specific is in hdkit.

**Unit colour variant** (lane r3-unitmat; `view/unitvariant.py`, `scene.Unit.variant` / `.variant_table`, `gpu.Draw.variant`,
`shaders.wgsl` `kt_apply`): a unit JSON's `VariantDefinitionComponent` names its `<unit>_variant.json` (260 tables, 8
`VariantColorTransform` entries each, 249 in the order cold, poison, level0 .. level4, misc; each entry is 3
`ColorTransform`s, one per KTINT mask channel R, G, B). The game picks **entry 2 + monstats `TransLvl`** for a monster:
the model's `ps_models_p` gets its 3 transforms in cb7 floats 0..71 (24 each, the JSON's field order) and a mask floor
in cb7[82], and every effect the unit starts gets `ColorVariantIndex` = that entry's position (0..7; the effect scripts
switch their colours on it). Proof: RenderDoc s2 frame 3505, `swarm1` (TransLvl 0): its `models_p` draw (eid 13030)
binds `level0`'s numbers, and the `vfx_swarm1_core` "Swarm" billboard VS outputs the colour the script picks for index
2. A colour state (states.txt colorshift c, 1..128) uses `hd/global/palette/transforms.json` entry c - 1 on all three
channels with floor 1, and effect index 37 + c. The recolour: HSV of the linear albedo, hue + shift and six 60-degree
band shifts, saturation and value scales, back to RGB, clamp, ^ gamma, tint lerp; mixed by max(KTINT rgb, floor)
(normalised over 1), the rest of the weight keeps the albedo (`unitvariant.apply` is the numpy reference, ported line by
line from the shader). Before this lane the viewer drew every unit's albedo untinted and started its effects with
index 0 (`cold`): swarm1's bug cloud drew as grey x 1.42 x 4 (white) instead of (0.0016, 0.131, 0.020) sRGB.
`scene.load_unit` picks the default (TransLvl 0, `level0`); `unitvariant.choose(table, variant=, trans_lvl=, shift=, vfs=)`
picks another; `unitvariant.effect_attrs(choice, attrs)` fills `ColorVariantIndex` unless the caller set it. Fx CLI:
`--monster <monstats Id>` (with `--tables`), `--unit-variant <position or name>`, `--color-shift <c>`; the report has
`unit_variant` and `attrs`. **Interface change:** the uniform block grew from 1600 to 1904 bytes (`gpu.KT_AT` = 400: 72
transform floats, then on, floor); web bundles carry `uniform_floats` from `gpu.UBUF_BYTES` and leave it 0 (off).
**Open:** colossal1's table has another order (cold, level0 .. level4, misc, poison): the game's pick for it (by position
or by name) is not checked; monsters with TransLvl over 5 clamp to the last entry (unchecked); champion / unique random
colours (randtransforms) are not modelled.

**What the game binds where** (read-only data; `fs` = `view.vfs.Layered`):
`effects_of_unit(fs, unit, tables=None, mode_map=None)`, `effects_of_missile(fs, name, tables)`, `effects_of_overlay(fs, name,
tables, monster=None, height_index=None)`, `effects_of_skill(fs, name, tables)`, `effects_of_state(fs, state, tables)` and
`effects_of_monster(fs, monstats_id, tables)` return dataclasses with `.as_dict()` and (all but the monster) `.vfx(skill=None)`.
A `Binding` is one entity or event: `file` (.particles), `bone` (`EntityAttachmentDefinitionComponent.targetbone`, a timeline's
`boneName`, or a SpawnObjectEffect `unitPart`), `xf` (the 4x4 of its `TransformDefinitionComponent`, or of a timeline event's
translation/rotation offset: position, orientation, scale as the game applies them, y up), `inherit_only_position`,
`source_skill` (`UnitSourceSkillFilterComponent`: the entity draws only for that skill; **lane r4-firewall**: a filter with an
empty `sourceSkill` sets `skill_default`, and `context.skill_pick` leaves such an entity out when another entity of the
definition names the skill, as the game does: only `hd/missiles/ground_fire_big.json` has one), `release_point`, and, **added by lane
context2**, `trigger`, `mode`, `attach`, `time`:

| `trigger` | where it comes from | draws when |
|-|-|-|
| `entity` | a unit JSON entity with a `VfxDefinitionComponent` (bone or root) | the unit lives (`vfx()`) |
| `name` | `hd/overlays/monster/<key>.json` named like the unit (key = the argument, the unit JSON's `name`, its file stem, raw and `norm_key`'d; none for player units). **Inferred**, nothing in the data names it (research lane bind); in-game calibration showed `ubermephisto`, a monsters.json alias of unit `mephisto`, gets Mephisto's smoke too, so the rule follows the unit | the unit lives (`vfx()`); Mephisto's `vfx_mephisto_ambient` is `entity_root` and `entity_vfx` of that overlay, identity at the unit root |
| `preload` | overlays of `DefinitionPreloadComponent.overlays` that are not already `name` ones: loaded, not necessarily played | never by default; `vfx(preload=True)` |
| `timeline` | `SpawnVFX` / `SpawnObjectEffect` of `<unit folder>/animation/combined.timelines` (by convention, no JSON names it): `mode` = the unit mode token (`a1`, `dt` ...) of the timeline's name via the unit's state machine (`mode_map_of`, `State_Attack1` modeId 4 -> `attack1` -> `a1`; else the lower-cased name), `time` = event timestamp in the mode's clip (s), `attach` = `attachToEntity` (None for SpawnObjectEffect), extras `duration`, `unit_part`, `orientation_bone`, `attach_to_dead_body`, `timeline` | only for that mode: `vfx(mode="dt")`, `UnitEffects.timeline(mode)`, the drivers below |
| `overlay`, `missile`, `state` | a definition reached by name (`effects_of_overlay`), by missile row, or by a states.txt column (extra `column`) | as before |
| `object` | `ObjectEffectDefinitionComponent.filename` of a unit / item / object JSON (kind `objecteffect`, `bone` = its attachment, `extra["model"]`; 208 particles, e.g. `ancientbarb1.json` entity `entity_torso` -> `vfx_ancientbarb1_objfx`). **Added by lane context3** (was `entity`) | with the unit's model (`vfx()`) |
| `attack_success` | `attackSuccessVfxOverrides` (`rules[].vfxFile` + `defaultVfxFile`) of a unit's `DamageFXComponent`: kind `hitfx`, extras `table`, `rule`, `hit_classes`, `victims`, `blood_types`, `default` | on a successful hit; never by `vfx()` (still also in `UnitEffects.hit`) |
| `screenspace` | `screenspaceVfx` of a component of `hd/env/vis/<env>.json` (20 particles): `screenspace_effects(fs, env)` | while that vis is active, over the screen |
| `global` | named files of global tables: `global_tables_effects(fs)`; extras `table` (file), `key` (key path), `table_kind` (`footprint`, `weather`, `ui`, `attack`, `attack_success`, `vfx_only`), `lang`, `variant` | by the game's own rule for the table (footprint id, weather, hit class, reticle, gold pickup); not tied to a unit |

**Added by lane context3** (survey bind2: 290 of the 766 particles no binding reached): `object_effects(fs, unit)` (a unit name or any
definition path such as `hd/items/weapon/axe/axe.json`; `effects_of_unit` now tags those entities `object`), `screenspace_effects(fs, env)`
(vis name or path), `attack_success_effects(fs, unit=None)` (`unit=None`: the global `attackSuccessVfx{Blood,NoBlood}`),
`global_tables_effects(fs)` (table `GLOBAL_TABLES`: `footprintdecals.json` `parameters[].vfxFile`; `weather_effects.json` rain, snow,
splash1-4, bubble1-4; `attackfxglobals.json` every `attack{Attempt,Success,Fail}Vfx{Blood,NoBlood}` table, a language dict for the fail
ones; `controllersettings.json` `config.freeTargetVFXFilename`; `gameplay_translation_settings.json` `goldPickupVFXFilename`; the
`hd/objects/vfx_only` units' `VfxDefinitionComponent` and `VfxVariantComponent` (`VFX_ONLY_UNITS` names those whose effects sit in a
variant, since a CASC root cannot be listed)). Timelines also read `SpawnVFXConnected` (a beam: `bone` = `sourceBoneName`,
`extra["target_bone"]` = `targetBoneName`, 12 particles). **`dependencies.particles[]` / `dependencies.objecteffects[]` of any JSON are
preload manifests, never bindings: nothing reads them.** The rest of the 766 (472) have no reference in any file; 16 sit as strings in
the D2RLoader exe (hard-coded by the game, not bound here). On the CASC data the new mechanisms reach 276 of the 766.

**Reverse index, added by lane r3-bind3** (`fx/firedby.py`): `build(read, names, tables, exe, corpus, raw)` reads every
definition once (relaxed JSON too) and gives per effect `status` (`bound`, `exe` = a path string in the executable,
`orphan` = named only by definitions nothing loads, `manifest` = only in `dependencies` lists, `unreferenced`), `refs`
(`src`, `trigger` as above plus `manifest`/`other`, `key`, `entity`, `comp`, `mode`, `bone`), `chains` (per definition:
`[definition, parent..., root]`; parents = any `.json` path a definition names, `.timelines` -> its unit JSON or the JSON
whose skeleton sits in `<folder>/skeleton/`; roots = missiles.txt rows via `missiles.json` (+ the skills / missiles
using them), overlay.txt rows (+ skills, states, ProgOverlay, the monster name rule), monstats ids via `monsters.json`,
`objects.json` keys (+ objects.txt `Class`, snake-cased), item codes of `items/uniques/sets.json`, lvlprest `File1..6`
(-> `hd/env/preset/<ds1>.json`), lvlwarp names (-> `hd/roomtiles/`), player classes, `hd/global/`, `hd/objects/vfx_only/`,
exe strings (full path, or a folder tail such as `gold/gold_large`)) and, for unused ones, `evidence` (authoring folder,
bound copy with the same name, bound variant whose name is this one minus tokens, bound size/number sibling, raw hits).
`FiredBy.load(json).of(path|bare name)`, `.summary(path)`; API/MCP `fx_fired_by(path, index)` (env `HDFX_FIRED_BY`);
`fx_coverage` rows get `fired_by` when an index is set; `python -m hdkit.fx.coverage ... --fired firedby.json` adds the
"What fires each effect" section. CLI: `python -m hdkit.fx.firedby build <data folder> --corpus <root> --exe <exe>
[--raw rawhits.jsonl] --out <dir>`, `show <index> <effect>`. Baked `.particles` never name another `.particles` file (no
file-to-file edge; child layers live inside the file).

`Binding.applies(skill=None, mode=None, preload=False)`: timeline bindings need their `mode`, preload ones `preload`, the skill
filter works as before; `UnitEffects.vfx(skill, mode, preload)` and `.timeline(mode)`. `resolve_particle(fs, file)` finds a
`.particles` file case-insensitively (`ambientFire` in the JSON, `ambientfire` on disk). Missiles add the
row's `Vel/MaxVel/Accel/Range` (motion read from the game's code by lane calib4, D2MOO: a row's missile moves in game frames,
raw velocity Vel x 256 x 0.75 / 4096 sub-tiles per frame, `Accel` added to the raw velocity every 5th frame, clamped to
[0, MaxVel x 256]: `MissileDriver(stepped=True)` for row-built missiles, `ACCEL_EVERY`, `ACCEL_VEL_PER_FRAME` 1/960,
`VEL_MAX_PER_VEL`); `MissileDriver.attrs_at(t, index)` gives the game's run-time missile attributes
MissileFrames/CurrentFrame (counting UP from 0 to Range: the client's attribute, lane calib5; the server's own counter counts down)/SlowFactor/Index (`driver.index`, set per
missile by `cast()`), fx CLI `--nova N`, `--attr NAME=V`; `cast()` makes a nova skill (srvdofunc 22) as the game's 64
missiles at `RING_DX/RING_DY`, velocity calc1 + Vel. Frost Nova's Core band (PlaneAligned, Size x |Axis2| radially)
equals the game's capture (3.86 x 8.97 units at the same missile frame); its ring radius too since lane calib5 (the ~1.9x
gap was the counter direction: timed game shots without a capture put the ring on the stepped missile track, ~24 units
at frame 13, gone by ~0.55 s); `Vel`: **measured in game by lane calib2**, 1 Vel = 3/64 sub-tile per frame = 2.34375 units/s
(`context.VEL_UNITS_PER_S`; the community "32 px = 1 sub-tile" rule, 1.5625, is 1.5x too slow); `Range` = lifetime in frames,
checked), light, and the missiles the row makes (`ExplosionMissile`, `CltSubMissile*`, `CltHitSubMissile*`).
Overlays resolve through `overlay.txt` `Character` (`all` = `common`) and carry `height`: monstats2 `OverlayHeight` (1..4) of
`monster` (or `height_index`) picks `Height1..4` of the row (`{"index", "px", "verified": False}`; **unverified**, column pairing
by name only, 0 for everything shipped; `effects_of_unit(..., tables).overlay_height` does it for the name-bound overlay).
States (`effects_of_state`) list `overlay1..4` (while active), `castoverlay`, `pgsvoverlay`, `removerlay` as overlays; monsters
(`effects_of_monster`) their unit and `Skill1..8` (+ `Sk<n>mode`, `Sk<n>lvl`) as `effects_of_skill`. Skills list every missile / overlay column
(`cltmissile*`, `srvmissile*`, `castoverlay`, `cltoverlaya/b`, `srvoverlay`, `tgtoverlay`, `prgoverlay`, `sumoverlay`) and
`anim` -> unit mode. CLI: `python -m hdkit.fx context unit|missile|skill|overlay|state|monster <name> --root d2r=<data> [--tables <excel
folder>] [--json]` (`unit` prints `<trigger mode @time attached|free>` per binding), `context scene <map> <cell x> <cell y>`,
`context env <vis name|path>` (screenspace effects) and `context global` (the global tables).

**Overlays as the game draws them (lane r3-overlays).** Interface changes:
- `python -m hdkit.view fx`: `--overlay auto` is the **default** (was: none). An effect under
  `hd/vfx/particles/overlays/<folder>/<key>/` is placed by `hd/overlays/<folder>/<key>.json`'s entity transform
  (`view/fxplace.overlay_for`; `--overlay none` = the old origin placement). Such an effect keeps world yaw
  `fxplace.OVERLAY_WORLD_YAW` (0) whatever `--yaw` / `--facing` the unit has (`--effect-yaw` still wins): bench o1 cast the
  player east then west, `setstate 136` drew the same winged foot both times; a CPU silhouette fit over yaws -180..170 peaks
  at 0.
- `--stop auto|none|<s>` (default auto): a **cast** overlay (named in a skills.txt castoverlay / cltoverlaya / cltoverlayb
  column; needs `--tables` or `HDFX_TABLES`) gets `Sim.stop()` (effect.isRunning false) at one overlay.txt pass,
  `context.overlay_seconds(row)` = Frames x 16 / AnimRate / 25 s (1.14d `GFX_AddOverlayToUnit` 0x470390: frame delay =
  frames << 8, rate = AnimRate << 4; `GFX_UpdateOverlayAnimations` 0x470c30 drops a play-once overlay at the end of the
  pass). When `*Frames` is blank, `context.overlay_seconds(row, fs)` reads the frame count from the DCC named by
  `Filename` under `global/overlays`. State overlays are never stopped. For `fx --cast`, `context.cast(..., fs=fs)`
  uses the same rule for `AttachDriver.stop_s` (`cast_info["overlays"][i]["stop_s"]`). `fxcast.step_to` stops the sim
  there. `OverlayEffects.row` keeps `frames`.
- **view.position** (r3-overlays and r3-aura2, unified at the r3 integration): `fxcli.make_sim(data, seed, transform,
  attrs=None, eye=None)`; the fx and cast viewers pass the render camera's eye `camera.OrthoCamera.eye(target=(0,0,0),
  dist=camera.GAME_EYE_DIST)` = target + `vm.game_eye(el, az, dist)` (884 units towards the camera, `vm.GAME_EYE_DISTANCE`,
  from the calib captures' eye - unit) as `SimContext.camera`, kept by `drive_to` / `step_to`. A sim without one reads
  the VM default, which is `vm.game_eye()` (the game's el 30 / az 45) since r3-aura2; before, (0, 10, 10): shrine overlays
  that put their mesh 3 units towards the camera landed 25 px left / 19 px up of the game's. `--view-eye default` = the
  VM default (same as `camera` at the game angles).
- VM, one rule for register types (r3-misc, r3-overlays, r3-decal2 and r3-calibfix each fixed it; merged): a register
  read under another type than its last write is a bit reinterpretation (`vm.reinterpret`, alias `vm.as_lanes`; a bool is
  0 / all ones), only `cvt` converts values; `and` / `or` / `xor` work on raw 32-bit lanes (`vm.bitwise_lanes`) and the
  result is stored by its destination type's bits. Idioms: `and dt, mask` (druid fire cast scorch timers), `rate & (Rain
  Amount > 0.35)`, the death pools' `asfloat(asint(dt) & asint(cond))`, Frost Nova's `dt * 4 & !isRunning`.
- Classic overlay height (1.14d `GetOverlayHeight` 0x6223a0): players use overlay.txt `Height2`, monsters
  `Height<OverlayHeight>` (index OverlayHeight - 1), other units `Height1`; HD overlays use their JSON transform instead
  (shrine_stamina: y 14).
- Findings (no interface change): **Lava Pit** (`druid_fire_cast_1`): the viewer's too wide / bright ground glow was the
  MaindecalSmokeSahder scorch never fading (the `and dt` bug above) and the effect never stopped; with both fixed the
  side by side (r3-overlays `sbs/lavapit_e/sidebyside_fixed.jpg`) shows the game's flames and after-stop ember scorch. The
  game's wider orange ground light during the flames is the general light (skill / missile light, lane r3-lighting), not
  the effect. **Acid missile** (`acidblast1` -> `frog_poison_missile.json`): 1.14d `MISSILE_CltDoFn01_BasicMissile`
  0x4d30c0 spawns no trail missiles; the long green streak is the HD effect's own `Trail_outer` ribbon. Rendered with the
  mod root bound and the game camera it is long and wide as in game. `TextureRepeat` is not read by D2R (lane animuv).

**Drivers** (what the Sim and the renderer take): every driver has `layers` (one per effect entity: `effect`, `xf`, `bone`),
`frame(t, prev) -> CtxFrame(t, age, contexts, events, layers)` and `frames(fps)`; `contexts[i]` is a `SimContext(transform,
velocity, target, bones, ground_z, emitter_shape, time_scale, running)` with the transform = driver pose x the layer's `xf`
and `running` = `Driver.running_at(t)`: False from `Driver.stop_t` on (None = never; a `MissileDriver` stops at the
missile's death, start + t_end: the game stops a missile's effect then, its binding's hardKillOnDestroy being false; lane
r3-calibfix)
(`SimContext` is imported from `fx/sim.py` when it exists, else the same fields are defined in `context.py`; events are NOT a
`SimContext` field: they are in `CtxFrame.events` as `(t, name, payload)` and the player feeds them to the Sim's event input).
- `attach(effect, unit, bone, player=, steps=, poses=, place=, yaw=)`: follows a bone of `view.player` poses (or the rest pose);
  `attach_unit(effects_of_unit(...), unit, ...)` does every entity on its own bone.
- `missile(effect, start=, end=|path=, speed=|row=, events={"hit":..,"explode":..}, arc=)`: flight with acceleration and range;
  events `launch`, `hit` or `expire`, `explode`, `spawn`; `driver.children` = `[(t, StaticDriver)]` at the impact point.
- `skill(effects, unit, cast_frame, mode, player=, target=, bone=, unit_fx=)`: the unit plays `mode`; the `cast` event and the children
  (overlays on the unit root, missiles leaving `bone`) start at the trigger time (`cast_time`: classic frame -> seconds via the player).
  `unit_fx` (an `effects_of_unit`) adds the unit's timeline effects of `mode` as children (`timeline` events).
- `timeline_driver(unit_fx, unit, mode, player=, poses=, hold=)` / `timeline_children(...)`: the unit plays `mode` and every
  timeline binding of it starts at its `time` (the clip's seconds scaled to the player's mode length, `event_time`): `attach` true
  (or a SpawnObjectEffect, or `attachToDeadBody`) -> an `AttachDriver` on the bone through the clip (`pose_t0=0`: poses at driver
  time), else a `StaticDriver` where the bone was at that moment. Bone `root` and a `unitPart` the skeleton lacks mean the unit
  frame; any other missing bone is listed in `driver.skipped`. `StaticPoses` (no clip) is the bind pose `inverse(iwt)`.
- `scene(map, xy, ...)` -> `SceneContext`: `.place(effect)` (a `StaticDriver`), `.light` (`LightPreset`), `.lights` (preset point
  lights near the spot), `.frame` + `.preset` (the mapview base scene), `.render_kwargs()`.
- **Added by lane cast**: `cast(skill_fx, unit=None, toward=(dx, dy), level=1, seed=0, anim=None, player=, poses=, place=,
  yaw=None)` -> `SkillDriver` of a whole cast (driver time 0 = the mode starts; `.cast_info` lists what it made): the caster
  overlays (`CAST_OVERLAY_COLS`: castoverlay, cltoverlaya/b) on the unit root and the missiles, both at the action frame of
  `anim` (`animdata_record(animdata.d2 bytes, "SOSCBOW")` -> `{frames, speed, action}`: action x 256 / speed / 25 s) from the
  caster's sub-tile (the HD missile's own transform gives the height), with the count and path of `SKILL_MISSILE_FUNCS` by
  srvdofunc (17 Charged Bolt: `skill_calc(calc1)` bolts, life capped at 77 frames, each on `chargedbolt_path`, the game's
  seeded random walk, `D2Seed`; 24 Fire Wall: `fx/wallcast.py`, each fire its own effect copy and seed, layout checked
  against the server track of a game cast). The fire-maker paths follow the missile's server do/hit functions. For Blaze,
  `toward` is the caster's post-cast running destination, with `RunVelocity` from the Sorceress row of `charstats.txt`
  (default 9) and trail drops limited to the `blaze` aura duration. For Immolation Arrow, `toward` is the assumed
  unobstructed impact target, capped by missile range. For Molten Boulder it is the heading. These previews use open
  ground and do not model obstacles, unit hits or town boundaries. Other skills default to one straight missile.
  `skill_calc(expr, row, level)` evaluates skills.txt calcs (`lnXY`, `dmXY`, `parN`, `lvl`, min/max). Overlays start with the cast mode (`overlay_at="start"`, default since lane r5-casttiming: on 27 ms shots of Fire Bolt, Charged Bolt and Frost Nova the cast light rises with the caster's first cast pose and the missiles leave at the action frame, 0.123 s later for the bench sorceress' staff `SOSCSTF`, frame 5 at speed 416; the light then runs its own curves at 1x and is not cut at the classic overlay's end); `overlay_at="lead"` starts them `CAST_OVERLAY_LEAD_S` (0.08 s) before the action frame (lane calib5's reading with a bow's 0.28 s action frame), `"cast"` at it (fx CLI `--cast-overlay-at`). Give fx `--wclass` the caster's real weapon class: the action frame comes from its COF. A bench runner can put the launch at the cast pose seen on the shots + the action time (0.123 s for the staff), one clock for every spell. Ribbons are packed one strip per `ParentID` (`render.CHAIN`). `SkillEffects.row` = the skills.txt row. Viewer: `python -m hdkit.view fx <name>
  --cast "<skill>" --tables <excel> --toward dx,dy [--unit <caster JSON> --wclass bow --cast-body --cast-level N
  --cast-t0 launch|mode]` (`view/fxcast.py`: every effect of every child gets its own Sim, one frame).
- `drive(driver, make_sim, fps)` plays a driver and its children against `make_sim(effect, ctx, seed) -> sim` (`sim.ctx`,
  `sim.step(dt)`, `sim.state()`), yielding `(t, [(label, SimState)])`.

**Light / bloom / tonemap for the render lane**: `light_preset(vis, light_mask, tone, bloom_gain)` and `scene(...).light` give a
`LightPreset(light: gpu.Light, post: PostPreset, vis, unknown, assumptions)`; `post.as_kwargs()` = `{"bloom", "tonemap",
"exposure"}` for `FxRenderer.render(..., post=)`. The values are what `view/rig.py`, `view/tone.py` and `mapview/hd.py` already
know, per vis file: exposure `0.18 / (2^EV x 10/65)` (EV 4 -> 0.0731); tonemap `"lut"` (`tone.lut_chain`, peak 250 nits, max-RGB
curve off) or `"game"` (`tone.grey`); bloom `{cutoff: bloomCutoff, intensity: bloomIntensity, levels: 5, gain}` for `tone.bloom`
(on the exposed HDR image before the tone map, so the render lane composites effects into that image first); ambient `0.004 x
iblIntensity`; preset point lights x `VIS_LIGHT_GAIN` (0.01); key and fill directional lights chosen by `lightMask` (1 environment,
19 monsters, 68 player). Unknown, listed in `unknown`: the grading LUT (`default.dds`, identity), the max-RGB curve, the bloom
level weights (`gain` stands in), the light directions in our axes (assumed equal to the engine's).

## 7. Calibration (`fx/calib/`)

Ground truth from the game (lane calib). The game side (a test copy of the game, a harness plugin that spawns the unit,
casts and screenshots; `testing/` and `hdkit/signoff/HARNESS.md` describe that machinery) belongs to the project that
runs it; `hdkit.fx.calib` holds the general parts, and every path, slot and mod name comes in as an argument.

- **Game shots** (what a bench gives the tools): the unit pinned in a fixed HD camera, empty-view twins, full-window
  shots every few frames. `bench.json`: per shot `t` (s since the spawn), the twin of the camera, the unit's and the
  character's sub-tiles (`unit_rel`: the unit's feet pixel is `view.camera.unit_pixel(*unit_rel, height)`), `clock`
  [[t, harness frame]]. Effects on demand through harness verbs: set a state on the player or a monster (state overlays,
  colour shift / ColorVariant: cold 11, poison 2), set a skill and cast it with a click (the client draws the cast
  overlay only for its own input; a harness `cast` shows the pose only), make a monster cast (its attack and missile).
- **RenderDoc** (`HDKIT_RENDERDOC`): the capture layer in the game's environment only (`rdoc.game_env`), captures
  `caps/*.rdc`. `python -m hdkit.fx.calib extract CAP --dir D [--events ..|--auto]` writes `actions.json` (all draws;
  `--list-stencil` adds each draw's stencil and depth state) and `draws/<eid>/` (draw.json, vertex/index/push/constant
  bytes, textures, target after the draw), `decode D` turns them into `draw.npz` (arrays named in `draws.py`).
- **Placement from a bench**: a map object's effect sits at `near.at - player - 0.5` on each axis; a killed unit's death
  effect at the client spot, turned by the unit's facing (harness `facing <id>`: mode, dir 0-63) and offset by its
  death-timeline binding. For added light, subtract the game's effect-free floor (the p50 `added_sum` of fresh shots after
  the effect has ended, or of a bounded window such as the settled corpse before a pool appears), and mask out bodies
  with a full-frame grayscale image when measuring a pool. Missiles the harness creates directly play no client effect.
- **Compare** (`python -m hdkit.fx.calib compare --render R.png --shot S.png --twin T.png --feet X,Y [--render-feet X,Y]`
  or `compare.compare(...)`): effect footprint = |shot - twin|, render footprint = alpha (or render vs base render), best
  shift, IoU, added light per mask, luma, brightest colour; a panel JPG.
- **Load test** ("save -> in-game load test"; general part `hdkit/fx/calib/loadtest.py`): the edited file is laid at its
  logical path into the test copy's mod data (`mods/<mod>/<mod>.mpq/data/<logical>`, which the game reads over its CASC)
  by a pre-launch hook that runs after the run's snapshot of the game folder, so the run's restore removes it again; the
  bench runs twice (nothing laid = reference, overlay laid), the viewer renders the same file at the shots' effect ages
  (`--root edited=<folder holding <logical>> --root casc`, first root wins), and `build_pair` puts in-game edited, viewer
  edited and in-game unedited (and viewer unedited) side by side with tint numbers. **Roots matter**: edit the game's own
  file (`roots=["casc"]`); a cache of another game build (Mephisto's smoke: 104,698 bytes against the game's 83,268)
  loads but is not what the game has. The GPU gate blocks every viewer render while any game holds its lock, so a runner
  renders after its game runs and waits for the gate.

## Heavy jobs

Renders and long sims run under `python -m hdkit.memwatch --gb 9 -- ...` (a resident-memory cap; `prlimit --as` does not
work for the GPU tools because the Vulkan driver reserves several GB of address space) and pass `python -m hdkit.gpugate`
first (never override it). Licences of everything ported or read: `hdkit/THIRD_PARTY.md`.
