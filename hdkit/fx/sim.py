"""Runtime of D2R's HD particle effects: effect clock, layers, spawns, events, particle storage (docs/hdfx.md section 2).

    sim = Sim(effect, seed=0, attrs=None, ctx=None)    # effect: a path, the file's bytes, a routing.Routing, or an
    sim.step(dt); sim.seek(t); sim.state()             # object with `.path` (fx.graph.Effect)

How a baked PopcornFX 2.9 effect runs (read from D2R's files and the CBVM scripts):
- The effect is a graph of *layer slots* joined by *event slots* (`routing.py`). Event slot "start" (source -1) spawns
  one particle in each of its target layers when the effect starts (`Root`, the emitter).
- Every layer slot keeps its own particles: a structure of arrays, one numpy array (count, components) per stream the
  layer's scripts name (`0`, `1`, ... `self.invLife`, `self.lifeRatio`).
- New particles start from the layer's stream defaults (`routing._defaults`: e.g. `self.invLife` 0 for the "Inf" layers,
  +inf for relay layers that live one frame), run the layer's spawn script once (kind none); every frame, live
  particles get
  `self.lifeRatio += self.invLife * dt`, then D2R's page update (`Sim._page`, D2R.exe 0xae4450 / 0xaee170): particles
  with lifeRatio >= 1 are swapped to the page end (KillDeadParticles), the rest run the evolve script (kind 4), the
  dead run one "dying" evolve (kind 5, lifeRatio 1, dt = the life they had left) and particles the evolve killed
  (`self.kill(true)`) one more with dt 0; then both are removed. Newborns (born by a kick this frame) get the same
  page update with their per-particle dt, the life update giving lifeRatio 1 to a non-finite invLife (zero life).
- Scripts fire events (`generate`/`trigger` -> `particleEvent:initPayload` -> `appendPayload` -> `kick`): each kick
  spawns `count` particles per firing particle in every target layer of the event slot of that name (the one this
  layer slot kicks), with the payload elements (positions, colours ... at the sub-frame time of each child) that the
  child's spawn script reads with `extractPayloadElement*`. Spawns from spawn scripts (chains of events) are processed
  in the same frame; spawned particles evolve from the next frame on.
Unknown engine calls are stubs counted in `sim.stats["stub_calls"]`; curve/shape/vector-field samplers come from lane
samplers (`hdkit.fx.samplers`) when present, else a linear curve fallback (`stats["sampler_fallback"]`).
"""
from __future__ import annotations

import collections
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import cbvm as cb
from . import routing as rt
from . import vm

F32, I32 = np.float32, np.int32
PARENT = "\0parent"     # payload key: the ids of the particles that fired the event (ParentID of the children)
FRAC = "\0frac"         # payload key: each child's sub-frame time in (0, 1] (end of the frame = 1)
XPOS = "\0xpos"         # payload key: the event's first XFormsPositions element (builtin kind 1), float3 per child
XORI = "\0xori"         # payload key: the event's first XFormsOrientations element (builtin kind 2), quaternion
MAX_CHAIN = 16          # event chains processed in one frame
MAX_PARTICLES = 200000  # per layer (a runaway spawner stops here; counted in stats["capped"])


@dataclass
class SimContext:
    transform: np.ndarray = field(default_factory=lambda: np.eye(4, dtype=F32))   # effect -> world
    velocity: np.ndarray = field(default_factory=lambda: np.zeros(3, F32))
    target: np.ndarray | None = None
    bones: object = None                       # callable(name) -> 4x4
    ground_z: float = 0.0                      # ground height (world y, HD metres, y up)
    emitter_shape: object = None
    time_scale: float = 1.0
    camera: np.ndarray | None = None           # view.position (default: Env.camera)
    scene_time0: float = 0.0                   # the game's scene clock (SceneCtx +0x108) when the effect starts: the
                                               # time turbulence samplers animate with (samplers._vf_sample)
    collision: object = None                   # hdkit.fx.collide.World for scene.intersect* (None: floor slab at ground_z)
    running: bool = True                       # False once the game has stopped the effect (a missile's death: the
                                               # driver's stop_t); Sim.step then stops it (effect.isRunning false)


@dataclass
class ParticleBatch:
    count: int
    fields: dict                               # stream name -> (count, n) float32
    renderer_inputs: dict = field(default_factory=dict)   # renderer index -> {input name: (count, k) array}


@dataclass
class SimState:
    t: float
    layers: dict                               # layer name -> ParticleBatch
    lights: list = field(default_factory=list)
    decals: list = field(default_factory=list)
    events: list = field(default_factory=list)  # (t, event name, count)
    sounds: list = field(default_factory=list)  # sound renderers (kind 6): fx/gamekinds.sound_record (not heard in D2R)


class Layer:
    """Particles of one layer slot."""

    def __init__(self, slot: rt.LayerSlot):
        self.slot = slot
        self.types: dict[str, int] = dict(slot.fields)    # every layer field + streams only a script names
        self.n = 0
        self.streams = {k: vm.zeros(t, 0) for k, t in self.types.items()}
        self.ids = np.zeros(0, np.int64)
        self.side: dict[str, np.ndarray] = {}    # "light"/"decal"/"model" -> per-particle handle
        self.side_on: dict[str, np.ndarray] = {}  # "light"/"decal" -> per-particle on (last Update call)

    def grow(self, k: int, next_id: int) -> slice:
        for name, t in self.types.items():
            self.streams[name] = np.concatenate([self.streams[name], vm.zeros(t, k)], axis=0)
        self.ids = np.concatenate([self.ids, np.arange(next_id, next_id + k)])
        for kind, h in self.side.items():
            self.side[kind] = np.concatenate([h, np.full(k, -1, I32)])
        for kind, h in self.side_on.items():
            self.side_on[kind] = np.concatenate([h, np.ones(k, bool)])
        s = slice(self.n, self.n + k)
        self.n += k
        return s

    def permute(self, lo: int, perm: np.ndarray) -> None:
        """Rows lo + i take rows lo + perm[i] (perm: a permutation of 0..len(perm)-1)."""
        idx = np.arange(self.n)
        idx[lo:lo + len(perm)] = lo + np.asarray(perm)
        for name in self.streams:
            self.streams[name] = self.streams[name][idx]
        self.ids = self.ids[idx]
        for d in (self.side, self.side_on):
            for kind in d:
                d[kind] = d[kind][idx]

    def keep(self, alive: np.ndarray) -> None:
        if alive.all():
            return
        for name in self.streams:
            self.streams[name] = self.streams[name][alive]
        self.ids = self.ids[alive]
        for kind in self.side:
            self.side[kind] = self.side[kind][alive]
        for kind in self.side_on:
            self.side_on[kind] = self.side_on[kind][alive]
        self.n = int(alive.sum())


class LayerEnv(vm.Env):
    """The runtime behind one script run over some rows of a layer."""

    def __init__(self, sim: "Sim", layer: Layer, script: cb.Script, rows: slice, payload: dict | None = None,
                 dt=None, birth: bool = False):
        n = rows.stop - rows.start
        super().__init__(script, n, stats=sim.stats, t=sim.t, dt=sim.dt_now if dt is None else dt,
                         attrs=sim.attr_values(layer.slot))
        self.rng, self.randctx = sim.rng, sim.randctx
        self.sim, self.layer, self.rows, self.payloads = sim, layer, rows, payload or {}
        self.streams = {k: v[rows] for k, v in layer.streams.items()}
        c = sim.ctx
        self.transform = np.asarray(c.transform, F32)
        self.prev_transform = sim.prev_transform if sim.prev_transform is not None else self.transform
        self.velocity = np.asarray(c.velocity, F32)
        self.ground_y = c.ground_z
        self.collision = getattr(c, "collision", None)
        # lane cast2: D2R.exe 0xbfdd60 -> 0xc001b0 lerps each particle's effect transform prev -> now at its sub-frame
        # time; in a spawn script that is the child's birth time (payload FRAC, set by kicks): vm._xf reads it
        fr = (payload or {}).get(FRAC) if birth else None
        self.birth_frac = None if fr is None or len(fr) != n else np.asarray(fr, F32).reshape(-1)
        # lane trailre: the spawn script of an event child gets the kick's spawner fields (ctx+0xb8, set only by
        # _SpawnMedium 0xae9250); xform* local -> world then uses the payload's XForms elements (D2R.exe 0xbfdd60 ->
        # 0xa76bb0 / 0xc019b0): vm._xf reads xform_payload = (positions, orientations or None)
        xp = (payload or {}).get(XPOS) if birth else None
        xq = (payload or {}).get(XORI) if birth else None
        self.xform_payload = None if xp is None or len(xp) != n else (
            np.asarray(xp, F32), None if xq is None or len(xq) != n else np.asarray(xq, F32))
        self.running = sim.running
        self.scene_time = float(sim.t) + float(getattr(c, "scene_time0", 0.0))
        if c.camera is not None:
            self.camera = np.asarray(c.camera, F32)

    def write_back(self) -> None:
        for k, v in self.streams.items():
            if k in self.layer.streams and not isinstance(v, vm.Ctx):
                dst = self.layer.streams[k]
                self.layer.streams[k][self.rows] = vm.fit(v, self.layer.types[k]) if v.shape[1] != dst.shape[1] \
                    else v.astype(dst.dtype, copy=False)

    def store(self, k, v):
        name, d = self.ext(k)
        if name is not None and d[3] == 0 and name not in self.layer.types and d[1] != cb.NONE:
            self.layer.types[name] = d[1] + 1          # a stream only this script names (rare): add it
            self.layer.streams[name] = vm.zeros(d[1] + 1, self.layer.n)
        super().store(k, v)

    def kick(self, event, counts, elements, fracs=None):
        self.sim.kick(self.layer.slot.index, event, counts, elements, self.layer.ids[self.rows], fracs)

    def payload(self, eid, k=1):
        """Element `eid` of the spawning event's payload: entry `eid` of the layer's incoming payload list
        (`LayerSlot.reads`), found by name in the payload of the event that spawned the particle; elements that event
        does not carry read as zeros (the engine's default). Files without payload objects: by appendPayload slot."""
        reads = self.layer.slot.reads
        if 0 <= eid < len(reads) and reads[eid] is not None:
            v = self.payloads.get(reads[eid])
            if v is None:
                self.stats["payload_absent"][reads[eid]] += 1
                return np.zeros((self.n, k), F32)
            return v
        return self.payloads.get(eid)

    def sampler(self, name):
        ref = self.layer.slot.samplers.get(name) if name else None
        return None if ref is None else self.sim.sampler_fn(ref)

    def alloc(self, kind):
        h = self.sim.new_handles(self.n)
        side = self.layer.side.setdefault(kind, np.full(self.layer.n, -1, I32))
        side[self.rows] = h
        return np.stack([h, np.zeros(self.n, I32)], axis=1)

    def update_side(self, kind, h, on, rest):
        on = np.broadcast_to(np.asarray(on, bool).reshape(-1, 1)[:, :1], (self.n, 1))[:, 0]
        side = self.layer.side_on.setdefault(kind, np.ones(self.layer.n, bool))
        side[self.rows] = on
        self.sim.side_updates.append((kind, self.layer.slot.index, self.rows, on))



class Sim:
    def __init__(self, effect, seed: int = 0, attrs: dict | None = None, ctx: SimContext | None = None,
                 max_dt: float = 1 / 30, platform: str | None = None, tier: str = "full"):
        self.routing = _routing(effect, platform, tier)
        self.seed, self.attrs_in, self.ctx, self.max_dt = seed, dict(attrs or {}), ctx or SimContext(), max_dt
        self.stats = vm.new_stats()
        self.stats.update({"sampler_fallback": collections.Counter(), "scripts_run": collections.Counter(),
                           "capped": collections.Counter(), "spawned": collections.Counter(),
                           "errors": collections.Counter(), "payload_absent": collections.Counter()})
        self.reset()

    # ------------------------------------------------------------------ clock
    def reset(self) -> None:
        self.rng = np.random.default_rng(self.seed)
        S = _samplers_module()
        self.randctx = S.RandCtx(self.seed) if S is not None and hasattr(S, "RandCtx") else None
        self.t, self.dt_now, self.started, self.running = 0.0, 0.0, False, True
        self.layers = [Layer(s) for s in self.routing.slots]
        self.next_id, self.next_handle = 0, 1
        self.queue: list = []
        self.events: list = []
        self.side_updates: list = []
        self._smp_cache: dict = {}
        self.prev_transform = None          # the effect's transform at the end of the previous frame
        self.sound_seen: dict = {}          # (layer, renderer) -> last particle id state() reported (gamekinds.sound_record)

    def stop(self) -> None:
        """The game stops the effect (effect.isRunning becomes false)."""
        self.running = False

    def step(self, dt: float) -> None:
        if self.running and not getattr(self.ctx, "running", True):
            self.stop()                     # the driver says the game stopped the effect (context.Driver.stop_t)
        dt = float(dt) * self.ctx.time_scale
        if not self.started:
            self._start()
        while dt > 1e-9:
            h = min(dt, self.max_dt)
            self._frame(h)
            dt -= h

    def seek(self, t: float) -> None:
        self.reset()
        self.step(t)

    def state(self) -> SimState:
        names = collections.Counter()
        out, lights, decals = {}, [], []
        for L in self.layers:
            key = L.slot.name if not names[L.slot.name] else f"{L.slot.name}#{L.slot.index}"
            names[L.slot.name] += 1
            fields = {k: np.asarray(v, F32) for k, v in L.streams.items() if not isinstance(v, vm.Ctx)}
            ri = {r.index: renderer_inputs(r, L) for r in L.slot.renderers}
            out[key] = ParticleBatch(L.n, fields, ri)
            for r in L.slot.renderers:
                if r.kind in ("light", "decal") and L.n:
                    (lights if r.kind == "light" else decals).append(side_record(r, L, key, ri[r.index]))
        return SimState(self.t, out, lights, decals, list(self.events), self._sounds(out))

    def _sounds(self, out: dict) -> list:
        """Sound renderers' records (fx/gamekinds.py): where PopcornFX would start a voice; D2R plays none."""
        from . import gamekinds
        recs, names = [], collections.Counter()
        for L in self.layers:
            key = L.slot.name if not names[L.slot.name] else f"{L.slot.name}#{L.slot.index}"
            names[L.slot.name] += 1
            for r in L.slot.renderers:
                if r.kind == "sound" and L.n:
                    seen = self.sound_seen.get((key, r.index), -1)
                    recs.append(gamekinds.sound_record(r, L, key, out[key].renderer_inputs[r.index], seen))
                    self.sound_seen[(key, r.index)] = int(L.ids.max())
        return recs

    # ------------------------------------------------------------------ internals
    def attr_values(self, slot: rt.LayerSlot) -> dict:
        a = {k: v for k, v in self.routing.attributes.items()}
        for k, v in slot.attrs.items():
            a.setdefault(k[4:] if k.startswith("__a_") else k, v)
        a.update(self.attrs_in)
        return a

    def new_handles(self, n: int) -> np.ndarray:
        h = np.arange(self.next_handle, self.next_handle + n, dtype=I32)
        self.next_handle += n
        return h

    def _start(self) -> None:
        self.started = True
        self.dt_now = 0.0
        for k in self.routing.entry():
            self.queue.append((k, 1, {}))
        self._drain()

    def _frame(self, dt: float) -> None:
        if self.prev_transform is None:
            self.prev_transform = np.asarray(self.ctx.transform, F32)
        self.t += dt
        self.dt_now = dt
        self.side_updates = []
        for L in self.layers:
            if L.n == 0:
                continue
            if dt != 0:      # D2R.exe 0xc07250 -> 0xc06f50: lifeRatio += (dt x scale) x invLife, skipped when dt is 0
                L.streams["self.lifeRatio"] = L.streams["self.lifeRatio"] + L.streams["self.invLife"] * F32(dt)
            self._page(L, 0, L.n, L.slot.evolve, None)
        self._drain()
        self.prev_transform = np.asarray(self.ctx.transform, F32)

    def _run(self, L: Layer, script: cb.Script, rows: slice, payload: dict | None = None, dt=None,
             birth: bool = False) -> LayerEnv:
        env = LayerEnv(self, L, script, rows, payload, dt, birth)
        self.stats["scripts_run"][f"{L.slot.name}:{rt.KIND.get(script.kind, script.kind)}"] += 1
        try:
            vm.run(script, env.n, env)
        except Exception as e:  # noqa: BLE001  (a script the interpreter cannot run yet: counted, not fatal)
            self.stats["errors"][f"{type(e).__name__}: {e}"[:160]] += 1
        env.write_back()
        return env

    def kick(self, slot: int, event: str | None, counts: np.ndarray, elements: dict, parents=None,
             fracs=None) -> None:
        """Particles of layer slot `slot` fire `event`: counts[i] children per firing particle i in every target of
        the event slot. `elements`: appendPayload slot -> (v0, v1[, build tag[, t0, t1]]); a float element is
        interpolated at each child's sub-frame time f (`fracs`, from vm.child_fracs; default (k + 1) / count) as the
        tag's low byte says (D2R.exe 0xc58ca0): 0 none (v0), 1/2 linear v0 -> v1, 3 Hermite with tangents
        (v1 - v0) + t0, (v1 - v0) + t1 (`hermite`); an element whose payload
        semantic (field 4, or tag >> 8) is 3 (`EmittedCount`) counts the children: v0 + 0, 1, 2 ... (doubtful: the
        count may run over frames). Payload keys are the element names (routing.EventSlot.payload)."""
        counts = np.asarray(counts).reshape(-1).astype(np.int64).clip(0, None)
        total = int(counts.sum())
        if not total:
            return
        es = self.routing.event_for(slot, event)
        self.events.append((round(self.t, 6), event, total))
        if es is None or not es.targets:
            return
        idx = np.repeat(np.arange(len(counts)), counts)
        first = np.repeat(np.cumsum(counts) - counts, counts)
        if fracs is not None and len(fracs) == total:      # the emission's sub-frame times (generate/trigger)
            frac = np.asarray(fracs, F32).reshape(-1, 1)
        else:
            frac = ((np.arange(total) - first + 1) / counts[idx]).astype(F32).reshape(-1, 1)
        payload = {}
        xkinds = set()   # builtin kinds 1/2 already looked up (D2R.exe 0xa76bb0 takes the first element of a kind)
        def spread(x):
            x = np.asarray(x)
            return x[idx] if x.shape[0] == len(counts) else np.repeat(x[:1], total, axis=0)

        for eid, el in elements.items():
            v0, v1, tag, t0, t1 = (*el, 0, None, None)[:5]
            if isinstance(v0, vm.Ctx) or isinstance(v1, vm.Ctx):
                continue
            a, b = spread(v0), spread(v1)
            sem = es.semantics[eid] if 0 <= eid < len(es.semantics) else int(tag) >> 8
            mode = int(tag) & 0xFF
            if sem == 3:
                v = (a + (np.arange(total) - first).reshape(-1, 1).astype(a.dtype)).astype(a.dtype)
            elif a.dtype.kind != "f":
                v = b
            elif mode == 0:
                v = a
            elif mode == 3 and t0 is not None and t1 is not None and not isinstance(t0, vm.Ctx):
                v = hermite(a, b, spread(t0), spread(t1), frac)
            else:
                v = (a + (b - a) * frac).astype(a.dtype)
            name = es.payload[eid] if 0 <= eid < len(es.payload) and es.payload[eid] is not None else eid
            payload[name] = v
            if sem in (1, 2) and sem not in xkinds:
                # the xform lookup takes the first element of the kind and needs a float3 (positions) or an
                # orientation (quaternion) type, else the children keep the effect's transform
                xkinds.add(sem)
                w = 3 if sem == 1 else 4
                if v.dtype.kind == "f" and v.ndim == 2 and v.shape[1] >= w:
                    payload[XPOS if sem == 1 else XORI] = v[:, :w]
        payload[FRAC] = frac
        if parents is not None:
            payload[PARENT] = np.asarray(parents)[idx] if len(parents) == len(counts) else np.repeat(
                np.asarray(parents)[:1], total)
        for t in es.targets:
            self.queue.append((t, total, payload))

    def _drain(self) -> None:
        for _ in range(MAX_CHAIN):
            if not self.queue:
                return
            q, self.queue = self.queue, []
            for slot, count, payload in q:
                if not 0 <= slot < len(self.layers):
                    continue
                L = self.layers[slot]
                count = min(count, MAX_PARTICLES - L.n)
                if count <= 0:
                    self.stats["capped"][L.slot.name] += 1
                    continue
                rows = L.grow(count, self.next_id)
                for name, (t, b) in L.slot.defaults.items():
                    if name in L.streams:
                        L.streams[name][rows] = vm.fit(vm.const_value(b, t + 1), t + 1)
                if "SelfID" in L.streams:
                    L.streams["SelfID"][rows] = _id2(L.ids[rows])
                if "ParentID" in L.streams and PARENT in payload:
                    L.streams["ParentID"][rows] = _id2(payload[PARENT][:count])
                self.next_id += count
                self.stats["spawned"][L.slot.name] += count
                pl = {k: v[:count] for k, v in payload.items()}
                killed = np.zeros(count, bool)
                if L.slot.spawn is not None:
                    killed |= self._run(L, L.slot.spawn, rows, pl, birth=True).killed
                if L.slot.newborn is not None and self.dt_now > 0 and FRAC in pl:
                    self._newborn(L, rows, pl, killed)
                elif killed.any():
                    alive = np.ones(L.n, bool)
                    alive[rows.start:rows.stop] &= ~killed
                    L.keep(alive)
        self.stats["capped"]["event chain"] += 1

    def _newborn(self, L: Layer, rows: slice, pl: dict, pre_killed: np.ndarray) -> None:
        """"Evolve Newborns" (D2R.exe 0xaee170): particles a kick spawned during this frame evolve for the rest of it
        (the kind-5 script, per-particle dt = (1 - frac) x dt, frac = the child's sub-frame time used for its
        payload). Life update 0xaef3d0: lifeRatio += dt_i x invLife, or 1 where invLife is not finite (zero life, the
        EventMultiplier relays): such a particle is dead before its evolve and runs only the dying evolve (dt 0), so
        it emits once even when born at the frame end. Then the page update (`_page`); spawn-script kills
        (`pre_killed`) are removed with the rest."""
        dts = ((1 - pl[FRAC]) * F32(self.dt_now)).astype(F32).reshape(-1, 1)
        L.streams["self.lifeRatio"][rows] = newborn_life(L.streams["self.lifeRatio"][rows],
                                                         L.streams["self.invLife"][rows], dts)
        self._page(L, rows.start, rows.stop, L.slot.newborn, dts, pl, pre_killed)

    def _page(self, L: Layer, lo: int, hi: int, script, dt, payload: dict | None = None,
              pre_killed: np.ndarray | None = None) -> None:
        """D2R's update of one page of particles (rows lo..hi) after the life update, as 0xae4450 (frame) and
        0xaee170 (newborns) run it:
        1. KillDeadParticles (0xb814c0): rows with lifeRatio bits > 0x3f7fffff (lifeRatio >= 1, +inf, +NaN) are
           swapped to the page end (`kill_order`);
        2. the survivors run `script` with `dt` (None: the frame dt);
        3. the dead run the dying evolve (0xaae470 -> 0xaae8a0 -> 0xaad980: the per-particle-time script, kind 5):
           lifeRatio set to 1, dt = the life they had left this frame (`dying_dt`);
        4. if the survivors' evolve killed some (`self.kill`): _ProcessKillSwitches (0xaacea0) sets their lifeRatio
           to 1, KillDeadParticles again, and they run the dying evolve with dt 0;
        5. the dead of 3 and 4 are removed (the page is cut at the survivors, 0xaaedd0)."""
        n = hi - lo
        if n <= 0:
            return
        lr, inv = "self.lifeRatio", "self.invLife"
        per = None if dt is None else np.broadcast_to(np.asarray(dt, F32).reshape(-1, 1), (n, 1)).copy()
        pay = dict(payload or {})
        pre = None if pre_killed is None else np.asarray(pre_killed, bool).copy()

        def reorder(perm, m_all):
            nonlocal per, pre
            L.permute(lo, perm)
            if per is not None:
                per[:m_all] = per[:m_all][perm]
            for k, v in pay.items():
                if isinstance(v, np.ndarray) and len(v) == n:
                    v = v.copy()
                    v[:m_all] = v[:m_all][perm]
                    pay[k] = v
            if pre is not None:
                pre[:m_all] = pre[:m_all][perm]

        def sub(a, b):
            return {k: (v[a:b] if isinstance(v, np.ndarray) and len(v) == n else v) for k, v in pay.items()}

        full = per if per is not None else np.full((n, 1), F32(self.dt_now), F32)
        dying = L.slot.newborn or script
        perm, m = kill_order(dead_mask(L.streams[lr][lo:hi]))
        reorder(perm, n)
        killed = np.zeros(m, bool)
        if m and script is not None:
            env = self._run(L, script, slice(lo, lo + m), sub(0, m), dt=None if per is None else per[:m])
            killed = env.killed[:m].copy()
        if m < n:
            rows = slice(lo + m, hi)
            dd = dying_dt(L.streams[lr][rows], L.streams[inv][rows], full[m:], full[m:])
            L.streams[lr][rows] = 1
            if dying is not None:
                self._run(L, dying, rows, sub(m, n), dt=dd)
        if killed.any():
            L.streams[lr][lo:lo + m][killed] = 1
            perm, m2 = kill_order(killed)
            reorder(perm, m)
            if dying is not None:
                self._run(L, dying, slice(lo + m2, lo + m), sub(m2, m), dt=np.zeros((m - m2, 1), F32))
            m = m2
        alive = np.ones(L.n, bool)
        alive[lo + m:hi] = False
        if pre is not None:
            alive[lo:lo + m] &= ~pre[:m]
        L.keep(alive)

    # ------------------------------------------------------------------ samplers
    def sampler_fn(self, ref: rt.SamplerRef):
        fn = self._smp_cache.get(ref.obj)
        if fn is None:
            fn = self._smp_cache[ref.obj] = _make_sampler(ref, self.stats)
        return fn


# Unnamed renderer inputs (CLayerCompileCacheRendererParticleInput without a name: field 0 the input number), named
# from the corpus (types, and which renderers have them: billboard inputs 4/5/6 follow General.BillboardingMode,
# 3 follows Transparent.SortMode 1, 7 follows General.EnableSize2D; ribbons 9/10 are the built-in fields 4/5) and the
# names next to them in D2R.exe ("SelfID", "Transparent.SortKey"). Names as the render lane reads them.
INPUT_NAMES = {0: "Position", 1: "Size", 2: "Enabled", 3: "SortKey", 4: "Axis", 5: "Axis2", 6: "Rotation",
               7: "Size2", 8: "Axis", 9: "SelfID", 10: "ParentID", 11: "Position2", 12: "Position3", 13: "MeshID",
               14: "Orientation"}
INPUT_NAMES_BY_KIND = {"mesh": {1: "Scale"}, "decal": {1: "Scale"}, "ribbon": {1: "Width"}}


def input_name(kind: str, name: str) -> str:
    """A renderer input's name as SimState gives it: named inputs keep their name, `input<N>` get INPUT_NAMES."""
    if name.startswith("input") and name[5:].isdigit():
        k = int(name[5:])
        return INPUT_NAMES_BY_KIND.get(kind, {}).get(k) or INPUT_NAMES.get(k, name)
    return name


def _stream_of(L: "Layer", field: str | None):
    """The stream a graph field name feeds (graph names the built-in fields "_<id>")."""
    if field is None:
        return None
    if field.startswith("_") and field[1:].isdigit():
        field = rt.BUILTIN.get(int(field[1:]), field)
    return L.streams.get(field)


def renderer_inputs(r, L: "Layer") -> dict:
    """{input name: (count, k) array} of one renderer over a layer's particles (docs/hdfx.md section 2): float inputs
    as float32, `Enabled` as bool, ints (ids, handles, DrawResolution) as int32. Inputs whose field the runtime does
    not keep (engine state) are left out."""
    out = {}
    for name, fld in r.inputs.items():
        v = _stream_of(L, fld)
        if v is None or isinstance(v, vm.Ctx):
            continue
        out[input_name(r.kind, name)] = np.asarray(v)
    return out


def side_record(r, L: "Layer", layer: str, ri: dict) -> dict:
    """A light or decal renderer's particles for the renderer: positions, colour, power, range (decals: size,
    direction, orientation, textures), `on` = Enabled and not released by the scripts' _pksi_vvUpdate* hook (its bool: lifeRatio >= 1)."""
    n = L.n
    on = np.ones(n, bool)
    if "Enabled" in ri:
        on &= np.asarray(ri["Enabled"]).reshape(n, -1)[:, 0] != 0
    side = L.side_on.get(r.kind)
    if side is not None and len(side) == n:
        on &= side

    def get(*names, k=1, default=0.0):
        for nm in names:
            if nm in ri:
                a = np.asarray(ri[nm], F32).reshape(n, -1)
                return a[:, :k] if a.shape[1] >= k else np.repeat(a[:, :1], k, axis=1)
        p = next((r.props[nm]["value"] for nm in names if nm in r.props and r.props[nm]["value"] is not None), None)
        v = np.asarray(p if p is not None else default, F32).reshape(1, -1)
        return np.repeat(v[:, :k] if v.shape[1] >= k else np.repeat(v[:, :1], k, axis=1), n, axis=0)
    rec = {"layer": layer, "renderer": r.index, "kind": r.kind, "count": n, "on": on,
           "position": get("Position", k=3), "color": get("Light.Color", "Diffuse.Color", k=4, default=1.0),
           "power": get("Light.Power", default=1.0)[:, 0], "range": get("LightAttenuation.Range", default=1.0)[:, 0],
           "handle": L.side.get(r.kind)}
    if r.kind == "decal":
        rec.update(size=get("Decal.DecalSize", "Scale", k=3, default=1.0), direction=get("Decal.Direction", k=3),
                   orientation=get("Orientation", k=4), weight=get("Decal.DecalWeight", default=1.0)[:, 0],
                   textures={k: p["string"] for k, p in r.props.items() if p.get("string")})
    return rec


def _id2(ids) -> np.ndarray:
    """A particle id as the engine's int2 id (`SelfID`/`ParentID`): (0, id); ids are unique in one Sim."""
    ids = np.asarray(ids, np.int64)
    return np.stack([np.zeros(len(ids), I32), ids.astype(I32)], axis=1)


def _samplers_module():
    try:
        from . import samplers  # lane samplers (fx/samplers.py)
        return samplers
    except ImportError:
        return None


class SamplerFn:
    """A sampler of a layer: `obj` is lane samplers' object (Curve, ShapeSampler, TurbulenceSampler) when it can be
    built; calling it is the fallback the vm uses otherwise (event-stream times, a linear curve)."""

    def __init__(self, ref: rt.SamplerRef, stats: dict):
        self.ref, self.stats, self.obj = ref, stats, None
        S = _samplers_module()
        kind = {"Curve": "curve", "Shape": "shape", "Turbulence": "turbulence"}.get(ref.kind)
        if S is not None and kind:
            try:
                self.obj = S.make(kind, ref.fields)
            except Exception as e:  # noqa: BLE001
                stats["errors"][f"samplers.make({kind}): {e}"[:160]] += 1

    def __call__(self, kind, ctx, *vals):
        d = self.ref.data
        if kind == "eventstream":
            return np.asarray(d.get("times", []), F32)
        if kind in ("curve", "curve_derive") and d.get("times"):
            self.stats["sampler_fallback"][self.ref.kind] += 1
            x = vm.as_base(vals[0], "f")[:, 0] if vals else np.zeros(ctx.n, F32)
            times = np.asarray(d["times"], F32)
            values = np.asarray(d["values"], F32).reshape(len(times), -1)
            if kind == "curve_derive":
                dv = np.gradient(values, times, axis=0) if len(times) > 1 else np.zeros_like(values)
                return np.stack([np.interp(x, times, dv[:, j]) for j in range(values.shape[1])], axis=1).astype(F32)
            return np.stack([np.interp(x, times, values[:, j]) for j in range(values.shape[1])], axis=1).astype(F32)
        return None


def _make_sampler(ref: rt.SamplerRef, stats: dict):
    return SamplerFn(ref, stats)


def newborn_life(lr: np.ndarray, inv: np.ndarray, dt: np.ndarray) -> np.ndarray:
    """Newborn life update (D2R.exe 0xaef3d0 / 0xaef250): lifeRatio + dt x invLife where invLife is finite, else 1
    (the bits test (invLife & 0x7f800000) != 0x7f800000: +-inf and NaN give 1)."""
    inv = np.asarray(inv, F32)
    fin = (np.ascontiguousarray(inv).view(I32) & 0x7F800000) != 0x7F800000
    with np.errstate(all="ignore"):
        v = (np.asarray(dt, F32) * inv + np.asarray(lr, F32)).astype(F32)
    return np.where(fin, v, F32(1)).astype(F32)


def dead_mask(lr: np.ndarray) -> np.ndarray:
    """KillDeadParticles' test (D2R.exe 0xb814c0): the lifeRatio bits as int32 > 0x3f7fffff (>= 1, +inf, +NaN)."""
    return np.ascontiguousarray(np.asarray(lr, F32).reshape(len(lr), -1)[:, 0]).view(I32) > 0x3F7FFFFF


def kill_order(dead: np.ndarray) -> tuple[np.ndarray, int]:
    """KillDeadParticles' compaction (D2R.exe 0xb814c0): from the last row down, a dead row swaps with the last live
    one. Returns (perm, survivors): new row i is old row perm[i]; rows >= survivors are the dead."""
    dead = np.asarray(dead, bool)
    perm = np.arange(len(dead))
    m = len(dead)
    for i in np.flatnonzero(dead)[::-1]:
        m -= 1
        if i != m:
            perm[i], perm[m] = perm[m], perm[i]
    return perm, m


def dying_dt(lr, inv, start, d, scale=1.0) -> np.ndarray:
    """dt of the dying evolve (D2R.exe 0xaae8a0): x = 1 / (scale x invLife); lived = max(0, (1 - lifeRatio) x x + d)
    when x is finite and not 0, else 0; end = max(0, start - lived); dt = start - end. lifeRatio is after this
    frame's update, so `lived` is the life left at the start of the step; a zero-life particle (invLife inf) gets 0."""
    lr, inv = np.asarray(lr, F32), np.asarray(inv, F32)
    start, d = np.asarray(start, F32), np.asarray(d, F32)
    with np.errstate(all="ignore"):
        x = (F32(1) / (F32(scale) * inv)).astype(F32)
        ok = np.isfinite(x) & (x != 0)
        lived = np.where(ok, np.maximum((F32(1) - lr) * x + d, F32(0)), F32(0)).astype(F32)
    end = np.maximum(start - lived, F32(0)).astype(F32)
    return (start - end).astype(F32)


def hermite(v0, v1, t0, t1, f):
    """Payload Hermite of D2R.exe 0xc58ca0 (buildPayloadElementD, interpolation 3): d = v1 - v0,
    v0 + h01 d + h10 (d + t0) + h11 (d + t1) with h10 = f^3 - 2f^2 + f, h11 = f^3 - f^2, h01 = 3f^2 - 2f^3."""
    f = np.asarray(f, F32)
    f2 = f * f
    h11 = f * f2 - f2
    h10 = (f - f2) + h11
    h01 = f2 - 2 * h11
    d = v1 - v0
    return ((d + t0) * h10 + (d + t1) * h11 + d * h01 + v0).astype(F32)


def _routing(effect, platform: str | None = None, tier: str = "full") -> rt.Routing:
    if isinstance(effect, rt.Routing):
        return effect
    if isinstance(effect, (bytes, bytearray, memoryview)):
        return rt.load(bytes(effect), None, platform or "PC", tier)
    path = getattr(effect, "path", effect)
    data = getattr(effect, "data", None)
    if data is None:
        if not path or not Path(path).is_file():
            raise ValueError("Sim needs the effect's bytes: pass a path, the bytes, or a graph.Effect with .data set")
        data = Path(path).read_bytes()
    return rt.load(bytes(data), str(path), platform or getattr(effect, "platform", None) or "PC", tier)


def summary(sim: Sim, stats: bool = True) -> dict:
    """JSON-able: particle counts and per-stream statistics per layer, plus the counters."""
    st = sim.state()
    layers = {}
    for name, b in st.layers.items():
        f = {}
        for k, v in b.fields.items():
            if b.count:
                f[k] = {"min": np.round(v.min(axis=0), 4).tolist(), "mean": np.round(v.mean(axis=0), 4).tolist(),
                        "max": np.round(v.max(axis=0), 4).tolist()}
        layers[name] = {"count": b.count, "fields": f}
    out = {"path": sim.routing.path, "t": round(st.t, 6), "seed": sim.seed, "layers": layers,
           "events": collections.Counter(e[1] or "-" for e in st.events)}
    if stats:
        out["stats"] = {k: dict(v) for k, v in sim.stats.items() if v}
    return out
