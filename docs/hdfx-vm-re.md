# hdfx: D2R.exe CBVM kernels and engine calls (lane vmre)

What D2R.exe's particle-script runtime does, read from the statics of the decrypted image
(a memory dump of build 93847, image timestamp 0x6ab3782c, with file offset = RVA; addresses below are RVAs,
VA = 0x140000000 + RVA), decompiled with Ghidra 12.1.4 headless (`tools/ghidra_scripts/`, knowledge/engine-re.md: find a function by its
registered name, follow rip-relative references, list its callers).
The interpreter is `hdkit/fx/vm.py`; every behaviour below has a test in `tests/test_hdfx_vm.py`
(class `D2RKernels`).

## How to find things

- **Engine calls are registered by name.** Each registration fills a descriptor on the stack (`[rbp-0x30]` name,
  `[rbp-0x28]` name length, `[rbp-0x20]`/`[rbp-0x18]` two code pointers: a shared adapter and the kernel) and calls
  `0xa38890`. `regscan.py <names.json>` finds the `lea` of every name string and the code pointers of the same block
  (when a block reuses a register set earlier, read the block by hand: `x64.py d <addr>`). Kernels take strided
  stream views (`{ptr, count, stride}`; stride 0 = a constant broadcast).
- **Opcode executors**: table `0x2a05e60`, index = opcode - 0x69 (the decoder table `0x2a05a30` has the same layout):
  `vct` 0x16d5530, `mop`/`add`..`div` 0x16deff0..0x16df4c0, `madd` 0x16e1130, `mfn1`/`mfn2` 0x1706970/0x1706a90
  (per type -> math kernels 0x16e3210 float, 0x16e4050, 0x16e4df0, 0x16e5bd0 float2..4), `mfn3` 0x17073a0 ->
  0x1709480 (per type: float 0x17073b0, int 0x17086f0), `sel` 0x17099b0 -> 0x170a7f0 -> 0x1709ad0 (4-byte lanes),
  `call` 0x170aca0.
- **Names of the mfn3 functions** sit just before the opcode-name table: `0x2a05ad8` lerp, `0x2a05ae0` clamp,
  `0x2a05ae8` within (then ld, st, ...). Builtin doc strings (`0x1d9f2f0..`) say the same: "Returns 1.0 if a is
  within the [b, c] range, returns 0.0 otherwise".
- **Coordinate frame**: `0xa956d0` returns the global frame, `0xa953a0(frame)` its signed axis triple (table
  `0x27da6c0`, 12 bytes per frame), `0xa95460(axis, frame)` one signed axis of the EAxis order Left, Right, Down, Up,
  Backward, Forward (doc strings at `0x1dc7d0d..`: 0/1 = -/+ entry 0, 2/3 = -/+ entry 1, 4/5 = +/- entry 2).
  D2R calls `0xeba840(0, ...)` (from `0xe223c1`), which sets frame 0 (`0xa95a90`); frame 0 is (1, 2, 3) and its
  handedness byte (`0x27da708`) is 1: **Right +x, Up +y, Backward +z, Forward -z.**

## Opcodes (all confirmed)

| op | D2R.exe | semantics |
|-|-|-|
| `mfn1.sincos`, `mfn1.fast_sincos` | 0x16e3210 case 7 (Cephes polynomials; quadrant mask `0x1cc7570` = -1) and case 0x26 (parabolic, phase 0 / +0.25 turn) | output **(sin, cos)**: component 0 has phase 0, component 1 the +1/4 turn |
| `mfn3.0` lerp | 0x17073b0 `param_1 == 0` | `(b - a) * c + a` |
| `mfn3.1` clamp | 0x17073b0 / 0x17086f0 | `min(max(a, b), c)`: max first, so lo > hi gives hi |
| `mfn3.2` within | 0x17073b0 (float: 1.0/0.0 from `0x1cba904`), 0x17086f0 (int: 1/0) | `b <= a <= c` (both ends inclusive) |
| `sel d, a, b, m` | 0x1709ad0 (`(~m & a) \| (m & b)` on 32-bit lanes; operands at instruction bytes +5 a, +9 b, +0xd m) | `m ? b : a` |
| `vct` | 0x16d5530 (component pointers of each source, copied lane by lane) | concatenation, **raw 32-bit copy**: a float source in an int vector keeps its bits (`vm.vct_part`). No game script mixes types; every corpus `vct` is float |

## Engine calls

Status words in `vm.NOTES`: plain = confirmed against D2R.exe; `game:` = the kernel is read, but its value comes from
game state the viewer does not have (a stand-in is used); `doubtful:` = not confirmed.

| call | registered kernel | finding | change in vm.py |
|-|-|-|-|
| `vrand_float_float_RandCtx` | 0xbf03c0 | direction uniform on the sphere (y = 1 - 2u, xz = sqrt(r^2 - y^2) (cos, sin) of a random angle, 0xc457a0); length `lo + (hi - lo) * u^((1 + lo/hi)/3)` (pow = 0xc4e850) with a, b clamped >= 0 and sorted; hi = 0 -> 0 | `vrand_radius`; was length uniform in [a, b] |
| `orientation_axisSide/Up/Forward_orientation` | 0xbecfd0 / 0xbed020 / 0xbecf80 -> table 0x27df638 (0xbe9110 / 0xbe9510 / 0xbe98f0 = matrix columns x / y / z of q, sign from the frame) | frame 0: q * (+x), q * (+y), q * (+z) | note only (model was right) |
| `scene.orientation_u_norm_float3` | 0xbf4dd0 -> 0xbf3330(mode 0) | shortest arc from Up (+y) to d; fallback axis Right (+x) when the cross product is tiny (squared length <= 1e-5); d = 0 -> identity; q = (axis sqrt((1-c)/2), sqrt((1+c)/2)), c clamped | `q_arc` (antiparallel case explicit) |
| `scene.orientation_f_norm_float3` | 0xbf3ab0 -> 0xbf3330(mode 1) | shortest arc from **Forward = -z** (axis id `2*mode+3` = 5) to d; fallback Down (-y) | **was +z** |
| `scene.orientation_fu_norm_float3_float3` | 0xbf3c90 (args f, u) | F = normalize(f) (else Backward +z), S = normalize(cross(u, F)) (else cross(Up, F), else Right), U = normalize(cross(F, S)); matrix rows (S, U, F) -> quaternion (0xa8da70): +z goes to f, +y towards u | note only (model was right) |
| `effect.axisSide/Up/Forward_pCtxI` | 0xbf6090 / 0xbf5330 / 0xbf53f0 -> 0xbf6c00(column, sign) | the effect orientation (interpolated prev -> current at the particle's time) times Right / Up / **Forward = -column 2** | **axisForward was +column 2** (1,690 uses, 184 files) |
| `dxformdt_*_masked_float3_int_pCtxS` | 0xbfd9a7 block -> 0xc02200 -> 0xbfc150/0xbfc440/0xbfc870/0xbfcbe0/0xbfd1c0/0xbfd3c0, applied by 0xc01ef0 (the xform apply: mode 1 translate, 6 rotate+scale, 7 all) | rows 0-2 = (current matrix - previous) * (1/dt); row 3 (translation) = the effect instance's stored velocity (instance data +0x58), not a finite difference; a translation-only mask returns that velocity | translation part = `SimContext.velocity` for world-space destinations |
| `buildPayloadElement*` | 0xbe4d50 adapter -> 0xc53d80.. -> 0xc58ca0 | (desc, v0, v1, eid): eid = semantic << 8 \| interpolation; interpolation 0 = none (every child gets v0), 1/2 = linear v0 -> v1 at the child's sub-frame time, 3 = Hermite | `sim.Sim.kick` honours the mode (mode 0 used to lerp) |
| `buildPayloadElementD_int3_float3_float3_float3_float3_int_pCtxS` | 0xbf2d40 adapter (args out, desc, a1..a4, eid, ctx) -> 0xc544d0 -> 0xc58ca0 | arguments **(v0, v1, t0, t1)**; Hermite `v0 + h01 d + h10 (d + t0) + h11 (d + t1)`, d = v1 - v0 | **was read as (v0, d0, v1, d1) and lerped**; `sim.hermite` |
| `scene.intersect_float3_float3_float_bool_int_SceneCtx` | 0xbf3210 -> 0xbf18d0 -> 0x7e80c0 | D2R's `PopcornPhysics::RayTracePacket`: ray origin -> origin + direction (length unused), sweep r 0.01, query min(bsf(mask), 6); hit = (normal, **0**), miss (1, 0, 0, +inf), zeros without a scene or with a false constant `enabled`. See "scene.intersect: what the game does" | `game:` (lane r3-intersect, `fx/collide.py`); the world is the viewer's scene (`Env.collision`) |
| `scene.intersectExt_float3_float3_float_bool_int_SceneCtx` | 0xbf3260 -> 0xbf2230 -> 0x7e80c0, then vtable+0x20 0xa39970 | same trace; int4 raw bits: hit (n.x, n.y, distance with n.z's sign bit, -1), miss (1.0f, 0, +inf, 0) | `game:` (was `doubtful`, zeros) |
| `noise_float3` | 0xbec8c0 -> 0xc623e0 | 3D simplex gradient noise (Gustavson style), one float: skew by 1/3 (floor via the 2^23 trick), unskew by 1/6, corner order from `movmskps` of (x<y, y<z, z<x) -> offset tables 0x1ddac00 (int32 [8][2][4]) and 0x1ddad00 (float [8][3][4]); hash `P[P[P[k]+j]+i]` over the classic permutation 0x1dda9f0 (corner 3: all +1); gradient = Perlin's 12 by `h = hash & 15` (u = h<8 ? x : y, v = h<4 ? y : (h = 12/14 ? x : z), signs from bits 0/1); kernel `min(0, d - 0.6)^4`; sum `((c2+c0)+(c3+c1)) * 32` | **confirmed and ported bit-exact** (`fx/noise.py`; checked against the machine code in an x86 emulator over 18,000 points incl. NaN/inf/huge) |
| `noise_float` | 0xbec680 -> 0xc616a0 | 1D simplex noise: `f = x - floor(x)`, `(1 - f^2)^4 * f * g0 + (1 - (f-1)^2)^4 * (f-1) * g1`, `g = ((perm[i] & 7) + 1)`, negated when bit 3 is set. **Two paths**: 4-wide for aligned dense arrays (scale 0x3eca456f = 32/81, kernel as `(t^2)^2 * f`) and scalar for the tail, unaligned and constant inputs (scale 0.395, `((f*t)*t)*(t*t)`): they differ by up to 1.5e-4 relative | **confirmed and ported bit-exact**; `noise_float(x)` goes wide in groups of four and scalar for the last `n % 4` and for a one-row input |
| `fast.noise_float` | 0xbead00 -> 0xb3d5b0 (the object at 0x34ca0d0, built at start-up by 0xa5130b -> 0xb3c4d0 -> 0xb3ef70) | 1D value noise: `T[perm[i]]` and `T[perm[i+1]]` blended by the quintic `f^3 (6f^2 - 15f + 10)` (`perm` = uint32 table 0x1dbdfe0, period 256); `T` = 256 floats drawn at start-up from the engine's SFMT19937 (**seed 0xcafe4269**, set at 0xa5130b, filled by 0xc06830 with range (-1, 1), duplicated to 512, 513th = first) | **confirmed and ported bit-exact** (the table was reproduced by running the RNG's own init/regen/fill code; the 4-wide and scalar paths are the same arithmetic) |
| `_pksi_vvAllocate/Update/KillLight*`, `..Decal*`, `..AnimatedModelInstance` | registered by D2Render (0xe14060..0xe17230, strings `0x1df5..`): lights 0x1002980 -> 0x1001400, 0x1002c00 -> 0x1001cf0 -> 0x10032f0, 0x1002bb0 -> 0x1001880; decals 0x1002960/0x1002bd0/0x1002b90 | Allocate -> handle int2 (id, slot < 1024; -1 when full); Update(handle, `IsDead`): false keeps the light, true frees it at once; Kill*Immediate(handle) = 1 when the handle is no longer in the game's active list | `game:` (model matched; notes) |
| `_pksi_vvGetVfxAttenuation_SI_float3` | 0xe15360 -> 0xd98f40 | calls the game's callback (0xe26d0, installed at 0x7ef6b) per point: ray down to the floor, tile cell -> byte of a 48x48 visibility/light map around the player (0x2a346a4) / 255; no callback -> 1.0 | `game:` (1.0) |
| `_pksi_vvWindDirection_SI_float3` | 0xe179f0 -> 0x1000d80 -> 0xe5ff20 | the game's wind: direction/strength globals (0x27f9fe0.., 0x27fa5d0..), gusts from sines of time and x + z, noise, times clamp(y / 30, 0.1, 1) | `game:` (0) |

Already confirmed by lane vm2 and unchanged: `generate` (0xc526b0/0xc529f0), `trigger` (0xc53580), `xform*`
(0xbfdd60), `idivmi`.

## Evaluator tiers (script kinds)

`CParticleKernelCPU_Evolver_Nodegraph::Run` (0xb818d0, profiler tag `0x27ddbf0`) holds five evaluators: one at
+0x18 and four at +0x20..+0x38. A page whose evaluation context has newborn ranges (+0xcc, +0xdc, +0xec non-zero,
the spawn/"Evolve Newborns" pass, 0xaad980) runs the +0x18 evaluator. Otherwise it reads the context's update level
L = ctx+0x124 (1..4) and runs **the first present evaluator among slots L..4**. With lane vm's corpus finding
(stores of 4 subset of 3 subset of 1/2, 1/2 = 5) the consistent reading is: kind 5 = +0x18 (newborns, per-particle
time), kinds 1..4 = +0x20..+0x38, and update level L runs kind L or the next smaller one present. `Sim(tier="full")`
(kind 1/2, L = 1) and `tier="base"` (kind 4) are the two ends. Not read: the code that writes ctx+0x124 for regular
pages and the code that fills the slots, so the kind -> slot mapping is inferred, not read.

## What statics cannot settle (for lane calib2)

1. **Update level per frame.** In a running game, break on the `[rdx+0x124]` read in 0xb818d0 for Mephisto's ambient
   effect (`hd/vfx/particles/character/enemy/mephisto/vfx_mephisto_ambient.particles`) near and far from the camera;
   log `[rdx+0x124]` and which of `[rcx+0x18..0x38]` is non-null and taken. Settles the kind -> slot mapping and
   whether distance changes the tier.
2. ~~noise_float3 values~~: ported (`fx/noise.py`), see the table.
3. ~~intersectExt int4~~: read from statics (lane r3-intersect, section below).
4. **Wind / attenuation values** are game state (area wind, visibility map); a capture gives one sample, not a model.

## Life, death and the dying evolve (lane vmfix)

Read from D2R.exe statics; ported in `fx/sim.py` (`Sim._page`, `_newborn`, `newborn_life`, `dead_mask`, `kill_order`,
`dying_dt`), tested in `tests/test_hdfx_sim.py`. Both the frame update of a page (`0xae4450`) and Evolve Newborns
(`CParticleTask_EvolveNewbornsPage`, `0xaee170`) run, per page:

1. **Life update.** Frame: `lifeRatio += (dt x scale_i) x invLife` (`CParticleKernelCPU_Internal_UpdateLifeRatios`,
   `0xc07250 -> 0xc06f50`), skipped when dt is 0. Newborns (`0xaef3d0` / `0xaef250`): `lifeRatio = scale x dt_i x invLife
   + lifeRatio` where `(invLife & 0x7f800000) != 0x7f800000`, **else 1.0** (a zero-life particle, invLife +-inf or NaN,
   is dead whatever its dt_i, also dt_i 0 for a child born at the frame end).
2. **KillDeadParticles** (`0xb814c0`): rows whose lifeRatio bits as int32 are `> 0x3f7fffff` (>= 1, +inf, +NaN) are
   swapped to the page end, scanning from the last row (a dead row swaps with the last live one: survivors are not kept
   in order). Returns the survivor count.
3. **Evolve the survivors** (`0xaad980` / `0xaac070` -> `0xb818d0`) with their dt.
4. **Dying evolve** (`0xaae470 -> 0xaae8a0 -> 0xaad980`, the per-particle-time evaluator = kind 5, also in the frame
   path): for each dead row `x = 1 / (scale x invLife)`; `lived = max(0, (1 - lifeRatio) x x + d_i)` when x is finite and
   not 0, else 0; `end = max(0, start_i - lived)`; lifeRatio is set to **1.0**; the evolve runs over [start_i, end]
   (dt = life left at the step start; 0 for zero life). This is where `trigger(self.lifeRatio >= 1)` (the OnDeath
   events) fires. Then the rows are cut off (`0xaaedd0`).
5. **Kill switches.** If the survivors' evolve called `self.kill` (context flag bit 7 of +0x12c), `_ProcessKillSwitches`
   (`0xaacea0`) sets those rows' lifeRatio to 1.0, KillDeadParticles runs again and they get the dying evolve with
   `d = 0` (dt 0), then are cut off: killed particles fire OnDeath too.

Consequences in the sim: a zero-life EventMultiplier relay emits exactly once (its generate runs in the dying evolve);
spawners whose amount is `rate x dt` emit only for the life they had left in their last frame; OnDeath layers spawn for
killed particles (Corpse Explosion's meat/bone chunks). Not modelled: pages (D2R compacts per page, so survivor order
matches the game only within one page) and substeps (`0xaef530`, newborns split in `N` steps when the layer asks).

Update level (open point 1 above, partly read): `0xae4450` writes `ctx+0x124` per page: 0 for newborn pages; else a
3-entry table picked by a LOD value (`[layer+0x80] + [..+0x58]` clamped to [0, 1]) gives shifts `s_k`, and the level is
`3 - k` for the first k (from 2 down) with `(frame_a + frame_b) & ((1 << s_k) - 1) == 0`, else 4 (not ported).

## Turbulence sampler (lane vmfix)

`CParticleNodeSamplerData_Turbulence` registers its properties at `0xb0ba80` in field order after the base fields:
10 GlobalScale (+0x17c), 11 Strength, 12 DataSource, 13 VectorFieldResource, 14..17 Wrap*, 18 Filtering, 19 Wavelength,
20 Octaves, 21 Lacunarity, 22 Gain, 23 Interpolator, 24 TimeScale (+0x1ac), 25 TimeBase (+0x1b0), 26
TimeRandomVariation (+0x1b4), 27 FlowFactor, 28 DivergenceFactor, 29 InitialSeed, 30 FastFakeFlow, 31 GainMultiplier
(+0x1c8); defaults as 1.13. Octaves `0xb0e480`, curl `0xba7710` (vtable `0x1db68b8` +0x20; coordinate-frame remap via
`0xa97780` is the identity, D2R sets frame 0), cubic per-octave gradient `0xba48e0 -> 0xb3cdb0`: all the same terms as
PopcornFX-Web 1.13. Differences, ported: (1) the lattice (`0xb3c530`): after the SFMT draws the odd entries are negated
(mask at `0x1cc1ac0`) and base = fast_asin of those (`0xc44600`), where 1.13 adds pi to odd bases and uses the raw
draws at phase 0; (2) `sample(pos, t)` (`0xba7320 -> vtable +0x10 = 0xba76f0`) drops t and samples at
`(float)*(double*)(SceneCtx + 0x108)`, as `sample(pos)` (`0xba6f70`); phase = time x TimeScale + TimeBase.
Fire Bolt's sparks (GlobalScale 2, Strength 1, Wavelength 0.2, TimeScale 1) move ~0.03..0.1 m on average from the
turbulence in either version: their ~2.7 m sideways drift is the spawn velocity, not the sampler.

## xform local -> world of event-spawned children (review of lane cast's SPAWN_POS, lane vmfix)

`xform*_masked` (`0xbfdd60`) fetches its matrices per particle: an index array from the evaluation context (`ctx[0xe]`,
one int per particle: the `EffectID` stream every script names) goes through `0xa91150` (a paged table, 512 per page,
at `+0x230`) to that effect instance's current (`+0x50`) and previous (`+0x48`) transforms. `0xc001b0` and its siblings
lerp them at the particle's sub-frame time (the newborn time ranges at ctx `+0xc0`/`+0xd0` x 1/dt). Children of an
event keep their effect's EffectID, so their local frame is **the effect instance's transform**. No path reads the
event's position payload. `sim.SPAWN_POS` (local origin = the event's position element, commit c9ef3a1) is not
D2R's rule. Lightning's arcs round the body must come from where the overlay effect instance is placed (its attach point
or height), not from the sim. Lane cast2 ported the prev -> now transform lerp at a newborn's sub-frame time for spawn scripts
(`vm._xf` with `LayerEnv.birth_frac`) and removed `sim.SPAWN_POS`.
Corpus regression (every 20th effect = 100, effect at (5, 1, -3),
1 s): the rule moves particles in 3 of them (Ice Break rocks/snow/decal up to 3.4 m, tentacle-head ripple 1.7 m) and
changes spawn counts in Warlock cleave (1345 vs 1351).

## Event children's frame: payload XForms (lane trailre; settles the conflict below)

The section above missed a branch of `xform*_masked` (`0xbfdd60`). The kick (`CParticleTask_KickEvent`, payload generation
`0xae97d0`) gives children their parent's EffectID (`[ctx+0x120]` per firing particle) and spawns them through `_SpawnMedium`
(`0xae9250`), the only place that sets the evaluation context's spawner fields (`ctx+0xb8`: EffectIDs, ParentIDs, the event's
payload description at +0x38, data at +0x40, stride +0x4c, row +0x50, count +0x54; `0xae0e90` clears +0xb8 for every other run,
so evolve and newborn-evolve scripts never see it). In `0xbfdd60`, when +0xb8 is set and the mask goes from a local space
(`(mask >> 3) & 2`) to a world one (`(mask >> 5) & 3` < 2), `0xa76bb0` looks up the payload description for the first element
of builtin kind 1 (`XFormsPositions`; the kinds' names at `0x1dbb1a0..`: Generic 0, XFormsPositions 1, XFormsOrientations 2,
EmittedCount 3) and of kind 2; positions need a float3 type, orientations the orientation type. With positions found, the mask
keeps bit 0 if positions, bit 1 if orientations, never bit 2 (scale), and `0xc019b0` applies per child: 1 `v + p`, 2
`q v q*`, 3 `q v q* + p` (quaternion x, y, z, w; `vm.qrot`). Without a positions element: the EffectID path. The previous-frame
spaces (1) read the same payload. The orientation variant (`0xbfea80`, not used by the corpus) does `q_payload * q`.
`dxformdt*` and `effect.axis*` never read the payload (`0xa76bb0` has no other callers). Ported: `sim.XPOS`/`XORI` (set by
`Sim.kick` from the first kind-1/2 elements), `LayerEnv.xform_payload` (spawn scripts only), `vm.xform_payload`; tests
`tests/test_hdfx_payloadxform.py`. This is close to lane cast's SPAWN_POS but adds the payload rotation, drops the effect's,
and applies only in spawn scripts. Corpus regression (every 20th effect): 10 of 100
change: blood-pool decals land where their drops are (0.7..1 m), Ice Break pieces (up to 6 m), the tentacle-head ripple (1.7 m),
shrine fires of a moving effect (their parent's payload carries a -90 deg yaw), Fire meteor sparks while moving, Warlock cleave
counts (1351 -> 1347). Charged Bolt's cast arcs now start in the ground ring (0.07 s, middle radius 6.7) and rise to ~7 units
by 0.25 s, matching calib2 g7 (side-by-side IoU 0.029 -> 0.113).

## Lightning cast overlay under the D2R rule: the open conflict (lane cast2; settled above)

`light_cast_1.json` (OverlayDefinition) has one `entity_root` with a VfxDefinitionComponent: no
EntityAttachmentDefinitionComponent (no bone), no transform; overlay.txt `light_cast_1` has no Height/offsets; the sorceress's
UnitRootComponent only has `rotationOverride` (45 deg about y) and `doNotUseHDHeight: false`. So the effect instance sits at the
unit root (her feet). Inside `LightningCastOverlay.particles` every drawn layer is placed from that origin:
- `MainBranch` (ribbon, spawned by `__e_TrailOut` of `@391`): spawn `A` = payload `Offset` (the `@391` vrand), `B` =
  `xform_l2w(0, 17)`; evolve `C = B + Turbulence(B + lerp(A, 0, frac(2 lifeRatio)))` (turbulence: Wavelength 1, TimeBase 1,
  TimeRandomVariation 1, other fields default, Strength 0.1) -> within ~+-1 unit of the origin. It never reads `Position`.
- `@430` (cloud billboards, `__e_PlasmaOut`): `xform_l2w(0, 17)` -> the origin. `Lobe`, `SparkLobes`, the light: local (0, 7, 0).
- `@391` (not drawn) carries the plasma points: ring radius 3 x 2.25 x curves, rising to 7 x curve units.
In game (calib2 g7, shots at 0.07..0.48 s after the launch, K = 16.97 px/unit, her head bone at 5.81 units) the arcs are
streaks from the ground ring (0.07 s, +-3 units round her) rising to ~8 units (0.17 s): the `@391` plasma paths, which
`MainBranch` trails. Under "EffectID = the effect's transform, payload position unread" the sim draws them within +-1 unit of
her feet. Either the event child's xform frame is not the effect's in this case (lane cast's SPAWN_POS reproduced the
game here, but moved other effects, section above) or the turbulence sample differs by an order of magnitude. Needs RE of
how a `__e_TrailOut` child's EffectID / matrices are set (0xa91150 table entries for event children) or a RenderDoc
capture during the first 0.3 s of a Charged Bolt cast (g7's capture at 1.45 s holds only the bolts).

Fire Bolt sparks (`vfx_firebolt_trail.particles` layers `Sparks`, spawn 346/428, evolve 347): spawn position = local ring
r 0.3 (`xform_l2w`, mask 19); velocity = `xform_l2w_d((0, 0, rand(1, 30)), 18)` = local **+z** x 1..30 u/s; evolve
`v = v e^(-2 dt) + T (1 - e^(-2 dt))` (T = the turbulence, GlobalScale 2, Strength 1, Wavelength 0.2, TimeScale 1) and the exact
position integral. No gravity or acceleration term. In the missile driver (`context.missile`: local +z = flight) sideways
drift is ~0 (local x +-0.3 at 0.8 s); the sparks go forward slower than the head (trail behind it). If D2R's missile
instance faces -z (axisForward = -column 2), the same velocity throws them backwards at up to 30 u/s. Downward drift can only
come from T. Settling it needs the missile instance matrix from a capture (g4's Fire Bolt frame d2r_frame6013 is not decoded).

## scene.intersect: what the game does (lane r3-intersect)

Read in the decompiled image as above. Ported in `hdkit/fx/collide.py`,
tests `tests/test_hdfx_collide.py`.

- **Kernel** 0xbf18d0 (intersect) / 0xbf2230 (intersectExt): if the scene interface (`SceneCtx+8 -> +0x60`, 0xae0e80)
  exists and the `enabled` bool stream is not a constant false (and vtable slot 3, 0xa39900, says no), per 128 rows:
  0xbf5070 builds the PopcornFX packet: origins (float4 copies), `(dir.xyz, length)` when `|dir|^2 > 1e-12`
  (const 0x1dc9ca0) else 0, hit distances **initialised to the lengths**; then vtable slot 1 (RayTracePacket) with the
  filter = the int argument. Afterwards a row is a hit when `distance < length`: intersect keeps the normal written by
  the trace and sets `.w = distance`, else writes (1.0, 0, 0.0, +inf) (0x1da9260.., 0x34c6d50); intersectExt keeps
  (n.x, n.y), `.z = (n.z & sign) | (distance & 0x7fffffff)`, `.w` = the contact entry's id (0xa39970 default: -1),
  else (1.0f, 0, +inf, 0). Without a scene or with `enabled` constant false: all zeros.
- **D2R's scene** (vtable 0x1d672b8: slot 0 0x7e27c0, slot 1 **0x7e80c0** RayTracePacket, string
  `PopcornPhysics::RayTracePacket`, D2Physics.cpp) per ray: `p0 = s * origin`, `p1 = p0 + s * dir.xyz` with
  `s = [0x280008c] = 1.0` (**the length in .w is not used**), max fraction 1.0, sweep radius `radii[i]` or **0.01**
  (0x3c23d70a; PopcornFX passes none), filter = query table entry `min(bsf(mask), 6)` of D2Physics (+0xe0 + 8 i);
  checks the ray (0x98acf0), casts it through the dmWorld (0x98e8f0, world id = D2Physics+0, up to 4 broadphase trees)
  and on a hit writes the hit position (if asked), the normal, and **distance 0** (`mov dword [rax+rdi*4], 0`).
  So scripts that pass a unit direction get a 1-unit ray; every hit reads as distance 0 (the physics template puts the
  particle back at its previous position and bounces its velocity; snap-to-ground scripts snap to the ray origin).
- **Filter** (0x7e81b1 `bsf eax, [mask]`; `cmovb` 6): index 0 effectenvironment (floor, effectfloor, wall,
  walltransparent), 1 player, 2 monster, 3 damage (player, monster), 4 effectfloor (floor, effectfloor), 5 wall (wall,
  walltransparent), 6 all. Categories (0x7e8790): default 1, floor 2, wall 4, player 8, monster 0x10, missile 0x20,
  item 0x40, object 0x80, simworld 0x100, destructible 0x200, walltransparent 0x400, effectfloor 0x800, all 0xffff.
  The corpus passes mask 0 (almost all) or 16 (effectfloor). For mask 0 `bsf` leaves eax unchanged: it holds the low half
  of the stale qword at [rsp+0x60] (no profiler) - the previous call's ray count (`movsxd`, stored at 0x7e81cb) for
  the 2nd+ block of 128 rows, else older stack data; values >= 6 give "all". `collide.MASK0_INDEX = 6`.
- **What is in the world** (LevelTranslationLayer 0x6b3150): per room a "simworld" body and, unless the room's preset
  already has a body with the floor filter, a "default_floor" box (filter floor, top at the ground, 10 units deep)
  over the room; the static PhysicsBodyDefinitionComponents of the scene's entities and prefabs (corpus counts: 1,159
  boxes, 30 `.physics` files, 13 spheres; filters destructible 678, walltransparent 381, wall 105, floor 25, monster 12,
  missile 1); kinematic spheres of a few critters. Players' bodies are made in code (not read).
