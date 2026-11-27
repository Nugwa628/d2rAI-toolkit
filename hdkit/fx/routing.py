"""What the runtime needs from a `.particles` file, read through lane format's effect graph (`fx/graph.py`).

    r = load(data, path=None, platform="PC")      # -> Routing (one platform graph; PC is what D2R reads)

Layer slots, event slots, samplers, attributes and renderers come from `graph.load` (one `PFile` parse); the compiled
scripts from `cbvm.scripts`. What the graph does not keep is read here from the same `PFile`:

- **Streams** (`LayerSlot.fields`): every `CLayerCompileCacheField` of the layer (field 0 id | 0x80000000, 1 value type =
  the externals' descriptor type, 2 size, 3 flags, 4 constant-pool row, 5 storage slot). Id N >= 7 is the scripts'
  external `f"{N - 7:X}"`; ids 0 and 1 are `self.lifeRatio` / `self.invLife`; id 4 is the particle's own id (`SelfID`,
  the layers that kick events and the ribbons have it) and id 5 its parent's (`ParentID`, ribbons only); ids 2, 3, 6
  are engine state the runtime does not model. A field with flag 4 starts from row field 4 (absent: row 0) of the
  layer's constant pool (CLayerCompileCache field 0, 32 bytes a row); e.g. `self.invLife` 0 = immortal ("Inf" layers),
  +inf = one frame (relay layers), 10 = 0.1 s.
- **Events** (`CLayerCompileCacheEvent`, layer field 5): 0 name, 1 = 1 when a script of the layer fires it (28,926 of
  28,926 in the PC and PS5 graphs of the corpus; the others never fire: see `EventSlot.fired`), 2 the payload elements
  (`CLayerCompileCacheEventPayload`: 0 name, 1 type, 2 size, 3 flags, 4 semantic, 5 slot), 3 the payload's byte size.
  `particleEvent:appendPayload(payload, k, element)` puts element k of that list. The layer's *incoming* event (layer
  field 11, unnamed) lists the elements the layer's spawn script reads: `extractPayloadElement*(i)` reads element i of
  that list, matched by **name** to the element of the event that spawned the particle (absent: zeros).
- **Event gates**: `__e_Spawn` / `__e_SpawnElse` pairs belong to condition layers (one-frame relays) the bake resolved
  per platform: on PC 2,585 gates fire `__e_SpawnElse` (65 fire nothing), consoles fire `__e_Spawn` on some of the same
  gates. So an event slot whose event is not `fired` never spawns (1,616 `__e_Spawn` slots with targets on PC).

Scripts of a layer (CCompilerBlobCache field 1 "Identifier", the evaluator): none = spawn, 4/3/1/2 = evolve of the
full-rate nodes / plus medium-rate / plus the rest, 5 = everything with per-particle time (newborns); see `pick_evolve`.
Names of the cache classes' fields: D2R.exe's PopcornFX reflection strings (e.g. CLayerCompileCache 11 RootEvent,
16 ActiveEvaluatorMask, 20 LayerName; Event 0 EventName 1 EventFlags 2 EventPayload 3 EventPayloadSizeInBytes;
Payload 0 PayloadName 1 PayloadType 2 PayloadFootprintInBytes 3 PayloadFlags 4 PayloadKind 5 PayloadRangeSlot;
Field 0 FieldNameID 1 FieldType 2 FieldStorageSize 3 FieldFlags 4 FieldConstantSlot 5 FieldRangeSlot).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from . import cbvm as cb
from . import graph as G
from .pfile import PFile, PObj

hp = cb.hp
KIND = {None: "spawn", 4: "evolve", 5: "evolve newborns", 1: "evolve tier 1", 2: "evolve tier 2", 3: "evolve tier 3"}
BUILTIN = {0: "self.lifeRatio", 1: "self.invLife", 4: "SelfID", 5: "ParentID"}


@dataclass
class SamplerRef:
    name: str
    kind: str                     # Curve, EventStream, Shape, Turbulence, Text, ...
    obj: int                      # data object (1-based)
    data: dict = field(default_factory=dict)
    fields: dict = field(default_factory=dict)   # raw {field id: bytes} (arrays without their count): samplers.make


@dataclass
class LayerSlot:
    index: int
    layer: int                    # CLayerCompileCache object (1-based; slots may share one)
    name: str
    inputs: list[int]             # event slots that spawn it
    outputs: list[int]            # event slots it kicks
    spawn: cb.Script | None = None
    evolve: cb.Script | None = None
    others: dict = field(default_factory=dict)   # evaluator id -> script, every one but the spawn (1-5)
    newborn: cb.Script | None = None              # kind 5: evolve of the particles born this frame (per-particle dt)
    samplers: dict = field(default_factory=dict)  # name -> SamplerRef
    attrs: dict = field(default_factory=dict)     # "__a_Name" -> default floats
    renderers: list = field(default_factory=list)  # graph.RendererDef of the layer
    defaults: dict = field(default_factory=dict)  # stream name -> (descriptor type, 32 constant bytes): spawn values
    fields: dict = field(default_factory=dict)    # stream name -> VM type (cbvm.TYPES index) of every layer field
    reads: list = field(default_factory=list)     # payload element names its spawn script reads (extract index)
    graph: object = None                          # the graph.Layer


@dataclass
class EventSlot:
    index: int
    name: str | None
    source: int                   # layer slot index; -1 the effect's start
    targets: list[int]
    payload: list = field(default_factory=list)   # element names by appendPayload slot
    semantics: list = field(default_factory=list)  # element semantic (payload field 4: 1 position, 2 orientation,
    #                                                3 emitted count) by slot
    fired: bool = True            # a script of the source layer fires it (event field 1); False: never spawns


@dataclass
class Routing:
    path: str | None
    platform: str
    slots: list[LayerSlot]
    events: list[EventSlot]
    attributes: dict              # name -> default floats (effect attributes)
    scripts: dict                 # object number (1-based) -> cbvm.Script
    effect: object = None         # the graph.Effect

    def event_for(self, slot: int, name: str | None) -> EventSlot | None:
        for i in self.slots[slot].outputs if 0 <= slot < len(self.slots) else []:
            if 0 <= i < len(self.events) and self.events[i].name == name:
                return self.events[i]
        for e in self.events:
            if e.name == name and e.source == slot:
                return e
        return None

    def entry(self) -> list[int]:
        return sorted({t for e in self.events if e.source in (-1, 0xFFFFFFFF) for t in e.targets})

    def dead_events(self) -> list[EventSlot]:
        """Event slots with targets that never spawn (their event is fired by no script of the source layer)."""
        return [e for e in self.events if e.source >= 0 and e.targets and not e.fired]


def _sampler_data(pf: PFile, ref: int) -> tuple[dict, dict]:
    o = pf.obj(ref)
    raw = {fid: v for fid, v, _e in o.fields}
    if o.cls.endswith("_Curve"):
        times, vals = o.arr(17, "f"), o.arr(18, "f")
        dim = len(vals) // len(times) if times else 0
        return {"times": times, "values": [vals[i * dim:(i + 1) * dim] for i in range(len(times))],
                "tangents": o.arr(19, "f"), "dim": dim, "interp": o.u32(3, None)}, raw
    if o.cls.endswith("_EventStream"):
        return {"times": o.arr(11, "f"), "raw": o.arr(11)}, raw
    return {k: v.hex() for k, v in raw.items() if len(v) <= 64}, raw


def _streams(pf: PFile, lo, scripts) -> tuple[dict, dict]:
    """(stream name -> VM type, stream name -> (descriptor type, 32 bytes)) of a layer: its field objects, plus streams
    only a script names (a layer without field objects: hand-made files)."""
    pool = lo.get(0) or b""
    rows = [pool[i:i + 32] for i in range(0, len(pool), 32)]
    types: dict[str, int] = {"self.lifeRatio": cb.TYPES.index("float"), "self.invLife": cb.TYPES.index("float")}
    defaults: dict = {}
    for r in lo.arr(2):
        f = pf.obj(r)
        fid = f.u32(0) & 0x7FFFFFFF
        name = BUILTIN.get(fid) if fid < G.schema.STREAM_BASE else f"{fid - G.schema.STREAM_BASE:X}"
        t = f.u32(1, 0)
        if name is None or t == cb.NONE:
            continue
        types[name] = t + 1
        if f.u32(3, 0) & 4 and rows:
            k = f.u32(4, 0)
            defaults[name] = (t, rows[k] if k < len(rows) else b"\0" * 32)
    for sc in scripts:
        for n, d in zip(sc.ext_names, sc.ext_desc):
            if n is not None and d[3] == 0 and d[1] != cb.NONE:
                types.setdefault(n, d[1] + 1)
    return types, defaults


def _payload_names(pf: PFile, ref: int) -> tuple[list, list]:
    names, sem = [], []
    for p in pf.obj(ref).arr(2):
        po = pf.obj(p)
        names.append(pf.str_field(po, 0))
        sem.append(po.u32(4, 0))
    return names, sem


def pick_evolve(by_kind: dict, tier: str = "full"):
    """The evolve script a frame runs. Evaluator ids (blob field 1 "Identifier"; layer field 16 ActiveEvaluatorMask
    has bit k for each): 0 spawn, 4 evolve of the full-rate nodes only, 3 adds the medium-rate nodes, 1 / 2 add the
    rest (their stores: 4 subset of 3 subset of 1/2 = 5 in every layer of the corpus; the extra code is mostly
    turbulence and curve sampling), 5 everything with per-particle time (the "Evolve Newborns" pass). The engine picks
    a tier per frame from the LOD (node "update rate"); `tier="full"` (default: LOD 0, every node every frame) runs the
    largest, `"base"` kind 4."""
    order = (4, 5) if tier == "base" else (1, 2, 3, 4, 5)
    return next((by_kind[k] for k in order if k in by_kind), None)


def load(data: bytes, path: str | None = None, platform: int | str = "PC", tier: str = "full") -> Routing:
    """The routing of one platform's graph, by name ("PC", "PS5" ...) or index into CParticleEffect field 1; `tier`:
    which evolve runs every frame (`pick_evolve`)."""
    pf = PFile.read(bytes(data))
    if isinstance(platform, int):
        plats = G.platforms(pf) if pf.objects and pf.obj(1).cls == "CParticleEffect" else []
        platform = plats[min(platform, len(plats) - 1)][0] if plats else "PC"
    eff = G.load(b"", pf=pf, platform=platform)
    eff.path = path or ""
    scripts = {s.obj + 1: s for s in cb.scripts(hp.ParticleFile(bytes(data)))}
    attributes = {a.name: (list(a.default) + [0.0] * 4)[:4] for a in eff.attributes}
    slots: list[LayerSlot] = []
    out_events: dict[int, dict] = {}            # layer slot -> {event name: (names, semantics, fired)}
    for lay in eff.layers:
        lo = pf.obj(lay.obj) if lay.obj else None
        sl = LayerSlot(lay.slot, lay.obj, lay.name, list(lay.inputs), list(lay.outputs), graph=lay,
                       renderers=list(lay.renderers))
        for b in lay.scripts:
            s = scripts.get(b)
            if s is None:
                continue
            if s.kind is None and sl.spawn is None:
                sl.spawn = s
            elif s.kind is not None:
                sl.others.setdefault(s.kind, s)
        sl.evolve = pick_evolve(sl.others, tier)
        sl.newborn = sl.others.get(5)
        for smp in eff.samplers:
            if smp.name and lo is not None and smp.obj in lo.arr(7) and smp.data_obj:
                d, raw = _sampler_data(pf, smp.data_obj)
                kind = pf.obj(smp.data_obj).cls.replace("CParticleNodeSamplerData_", "")
                sl.samplers[smp.name] = SamplerRef(smp.name, kind, smp.data_obj, d, raw)
        if lo is not None:
            for r in lo.arr(3):
                ao = pf.obj(r)
                name = pf.str_field(ao, 0)
                if name:
                    b = ao.get(9) or b"\0" * 16
                    sl.attrs[name] = list(struct.unpack("<4f", b)) if len(b) == 16 else [0.0] * 4
            run = [x for x in [sl.spawn, sl.evolve, *sl.others.values()] if x is not None]
            sl.fields, sl.defaults = _streams(pf, lo, run)
            evs = {}
            for r in lo.arr(5):
                eo = pf.obj(r)
                names, sem = _payload_names(pf, r)
                evs[pf.str_field(eo, 0)] = (names, sem, bool(eo.u32(1, 0)))
            out_events[lay.slot] = evs
            if lo.has(11):
                sl.reads = _payload_names(pf, lo.u32(11))[0]
        else:
            sl.fields, sl.defaults = _streams(pf, PObj("CLayerCompileCache"), [])
        slots.append(sl)
    events = []
    for e in eff.events:
        names, sem, fired = out_events.get(e.source, {}).get(e.name, ([], [], None))
        if fired is None:                       # no event object (hand-made files): fired when a script names it
            sl = slots[e.source] if 0 <= e.source < len(slots) else None
            fired = sl is None or any(e.name in s.ext_names for s in [sl.spawn, sl.evolve, *sl.others.values()] if s)
        events.append(EventSlot(e.index, e.name, e.source, list(e.targets), names, sem, fired))
    return Routing(path, eff.platform or "", slots, events, attributes, scripts, eff)
