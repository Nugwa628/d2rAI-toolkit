"""The effect graph of a baked D2R particle effect, and edits on it (docs/hdfx.md section 1).

A `.particles` file holds one layer graph per platform (`PC`, `Switch`, `PS4`, `PS5`; D2R on PC reads `PC`). A graph has
entry slots, event slots and layer slots; a layer slot names a layer (`CLayerCompileCache`) and the event slots that
spawn it (`inputs`) and that it emits (`outputs`); an event slot names the layer slot that emits it (`source`, -1 = the
effect itself) and the layer slots it spawns (`targets`). A layer has particle fields (streams), samplers (curves, shapes,
turbulence, event streams, text), events (with payloads), compiled scripts (CBVM, see hd_cbvm.py) and renderers, whose
properties are plain data and whose particle inputs (`CLayerCompileCacheRendererParticleInput`) name the field feeding
each renderer input. Stream names are the scripts' external names: field id N is external `f"{N - 7:X}"`.

    eff = load(path_or_bytes)                    # -> Effect (PC graph by default)
    eff.to_json(); Effect.from_json(d)
    apply_edits(data, ops) -> bytes   # prop texture enable curve const color script copy_layer remove_layer

Object numbers are kept (1-based), so every graph item maps back to its bytes.
"""
from __future__ import annotations

import colorsys
import json
import struct
from dataclasses import asdict, dataclass, field, fields as dc_fields, replace
from pathlib import Path

from . import schema
from .pfile import PFile, PObj

RENDERER_KINDS = {0: "billboard", 1: "ribbon", 2: "mesh", 3: "triangle", 4: "decal", 5: "light", 6: "sound"}
PROP_TYPES = {0: "feature", 1: "bool", 2: "int", 3: "int", 4: "int2", 5: "int3", 6: "int4", 7: "float", 8: "float2",
              9: "float3", 10: "float4", 12: "texture", 13: "atlas", 14: "resource", 15: "sound"}
SCRIPT_KINDS = {None: "spawn", 1: "kind1", 2: "kind2", 3: "evolve3", 4: "evolve", 5: "evolve"}
SAMPLER_KINDS = {"Curve": "curve", "Shape": "shape", "Turbulence": "turbulence", "EventStream": "eventstream",
                 "Text": "text"}
ON, OFF = b"\xff" * 16, b"\x00" * 16


# ---------------------------------------------------------------------- dataclasses
@dataclass
class Field:
    obj: int
    name: str                 # the scripts' external name (hex of id - 7), "_<id>" for the 7 built-in ids
    type: str | None
    size: int
    id: int = 0
    inputs: list[str] = field(default_factory=list)   # renderer inputs fed by this field ("<renderer>:<input>")


@dataclass
class Attribute:
    obj: int
    name: str
    type: str
    default: list
    min: list | None = None
    max: list | None = None
    description: str | None = None


@dataclass
class Sampler:
    obj: int                  # the layer's CLayerCompileCacheSampler
    name: str
    kind: str
    data: dict
    data_obj: int = 0         # the CParticleNodeSamplerData_* object
    layer: str = ""
    label: str | None = None  # the node's own name (curves: e.g. "AlphaAnimation")


@dataclass
class RendererDef:
    obj: int
    index: int
    kind: str
    material: str | None
    enabled: bool
    props: dict = field(default_factory=dict)    # name -> {obj, type, value, string}
    inputs: dict = field(default_factory=dict)   # renderer input -> field name
    layer: str = ""


@dataclass
class Event:
    obj: int
    name: str | None
    payloads: list = field(default_factory=list)  # {name, type, size}


@dataclass
class EventSlot:
    obj: int
    index: int
    name: str | None
    source: int               # layer slot that emits it (-1: the effect)
    targets: list[int] = field(default_factory=list)


@dataclass
class EntrySlot:
    obj: int
    name: str | None
    slot: int                 # u32 field 1 (looks like an event slot index; kept as is)


@dataclass
class ScriptRef:
    obj: int
    kind: str                 # spawn | evolve | evolve3 | kind1 | kind2
    layer: str
    externals: list = field(default_factory=list)  # {name, type}
    calls: list = field(default_factory=list)
    raw_kind: int | None = None
    consts: int = 0


@dataclass
class Layer:
    obj: int
    name: str
    slot: int = 0
    slot_obj: int = 0
    spawn_script: int | None = None
    evolve_scripts: list[int] = field(default_factory=list)
    fields: list[Field] = field(default_factory=list)
    renderers: list[RendererDef] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    children: list[str] = field(default_factory=list)
    life: float | None = None
    spawn: dict | None = None
    samplers: list[str] = field(default_factory=list)
    attribs: list[str] = field(default_factory=list)
    inputs: list[int] = field(default_factory=list)    # event slots spawning it
    outputs: list[int] = field(default_factory=list)   # event slots it emits
    colors: list = field(default_factory=list)         # colour constants {index, width, value}
    scripts: list[int] = field(default_factory=list)


@dataclass
class Effect:
    path: str
    version: str
    objects: int
    layers: list[Layer] = field(default_factory=list)
    attributes: list[Attribute] = field(default_factory=list)
    samplers: list[Sampler] = field(default_factory=list)
    events: list[EventSlot] = field(default_factory=list)
    entry: list[EntrySlot] = field(default_factory=list)
    scripts: dict[int, ScriptRef] = field(default_factory=dict)
    strings: list[str] = field(default_factory=list)
    platform: str = "PC"
    platforms: list[str] = field(default_factory=list)
    graph_obj: int = 0

    @property
    def renderers(self) -> list[RendererDef]:
        """Every renderer once (a layer object can sit in several slots), in slot order."""
        seen, out = set(), []
        for lay in self.layers:
            for r in lay.renderers:
                if r.obj not in seen:
                    seen.add(r.obj)
                    out.append(r)
        return out if self.platforms else sorted(out, key=lambda r: r.index)

    def layer(self, name: str) -> Layer:
        for lay in self.layers:
            if lay.name == name:
                return lay
        raise KeyError(f"no layer {name!r} (layers: {[x.name for x in self.layers]})")

    def to_json(self) -> dict:
        d = asdict(self)
        d["scripts"] = {str(k): v for k, v in d["scripts"].items()}
        return d

    @classmethod
    def from_json(cls, d: dict | str) -> "Effect":
        if isinstance(d, str):
            d = json.loads(d)

        def mk(tp, v):
            return tp(**v)
        lay = []
        for x in d.get("layers", []):
            x = dict(x)
            x["fields"] = [mk(Field, f) for f in x.get("fields", [])]
            x["renderers"] = [mk(RendererDef, r) for r in x.get("renderers", [])]
            x["events"] = [mk(Event, e) for e in x.get("events", [])]
            lay.append(Layer(**x))
        kw = {f.name: d[f.name] for f in dc_fields(cls) if f.name in d}
        kw.update(layers=lay, attributes=[mk(Attribute, a) for a in d.get("attributes", [])],
                  samplers=[mk(Sampler, s) for s in d.get("samplers", [])],
                  events=[mk(EventSlot, e) for e in d.get("events", [])],
                  entry=[mk(EntrySlot, e) for e in d.get("entry", [])],
                  scripts={int(k): mk(ScriptRef, v) for k, v in d.get("scripts", {}).items()})
        return cls(**kw)


# ---------------------------------------------------------------------- decoding helpers
def _i32(v: int) -> int:
    return v - (1 << 32) if v >= 1 << 31 else v


def prop_value(t: int | None, raw: bytes | None):
    """A renderer property's value as JSON: bool for features/bools, numbers (scalar or list) for ints/floats."""
    raw = raw if raw is not None else OFF
    ints = struct.unpack("<4i", raw) if len(raw) == 16 else (0,) * 4
    fl = struct.unpack("<4f", raw) if len(raw) == 16 else (0.0,) * 4
    t = t or 0
    if t in (12, 13, 14, 15):
        return None
    if t in (0, 1):
        return any(ints) if t == 0 else ints[0] != 0
    if t in (2, 3):
        return ints[0]
    if t in (4, 5, 6):
        return list(ints[:t - 2])
    if t == 7:
        return _f(fl[0])
    if t in (8, 9, 10):
        return [_f(x) for x in fl[:t - 6]]
    return raw.hex()


def _f(x: float):
    return float(f"{x:.7g}")


def prop_bytes(t: int | None, value) -> bytes:
    """The 16-byte value of a property (as hd_particles.set_property: a scalar fills all four lanes)."""
    t = t or 0
    if t in (0, 1):
        return ON if value else OFF
    vals = list(value) if isinstance(value, (list, tuple)) else [value] * 4
    vals = (vals + [0] * 4)[:4] if isinstance(value, (list, tuple)) else vals
    if t in (2, 3, 4, 5, 6):
        return struct.pack("<4i", *[int(v) for v in vals])
    if t in (7, 8, 9, 10):
        return struct.pack("<4f", *[float(v) for v in vals])
    raise ValueError(f"property type {t} ({PROP_TYPES.get(t)}) has no numeric value; use the texture op for paths")


def field_name(fid: int) -> str:
    return f"{fid - schema.STREAM_BASE:X}" if fid >= schema.STREAM_BASE else f"_{fid}"


def color_constants(pf: PFile, layer: int) -> list[dict]:
    """Colour-like constants of a layer's constant table (after the mod studio's `ParticleEdits.ColorConstants`): float3
    or float4 lane patterns (8 lanes over two 16-byte entries) with differing, non-negative channels."""
    o = pf.obj(layer)
    ent = o.elems(0)
    out = []
    for i in range(0, len(ent) - 1, 2):
        lanes = struct.unpack("<8f", ent[i] + ent[i + 1])
        bits = struct.unpack("<8I", ent[i] + ent[i + 1])
        for w in (3, 4):
            if any(bits[k] != bits[k % w] for k in range(8)):
                continue
            v = list(lanes[:w])
            if (any(not (0 <= x <= 64) for x in v) or all(x in (0.0, 1.0) for x in v) or max(v[:3]) - min(v[:3]) < 1e-6
                    or (w == 4 and v[2] == 0 and v[3] == 0)):
                break
            out.append({"index": i, "width": w, "value": [_f(x) for x in v]})
            break
    return out


# ---------------------------------------------------------------------- load
def _read(src) -> tuple[bytes, str]:
    if isinstance(src, (bytes, bytearray)):
        return bytes(src), ""
    return Path(src).read_bytes(), str(src)


def platforms(pf: PFile) -> list[tuple[str, int]]:
    root = pf.obj(1)
    return [(pf.str_field(pf.obj(g), 0) or f"graph{k}", g) for k, g in enumerate(root.arr(1))]


def load(src, vfs=None, platform: str = "PC", pf: PFile | None = None) -> Effect:
    """The effect graph of a file (path or bytes) for one platform. `vfs` is accepted for the shared signature (paths
    stay data-relative strings; nothing is resolved here)."""
    path = ""
    if pf is None:
        data, path = _read(src)
        pf = PFile.read(data)
    v = pf.version
    eff = Effect(path, f"{v & 0xFF}.{(v >> 8) & 0xFF}.{(v >> 16) & 0xFF} (build {pf.build})", len(pf.objects),
                 strings=list(pf.strings))
    root = pf.obj(1) if pf.objects else PObj("")
    if root.cls != "CParticleEffect":
        return _load_flat(pf, eff)
    plats = platforms(pf)
    eff.platforms = [p for p, _ in plats]
    if root.has(3):
        for r in pf.obj(root.u32(3)).arr(0):
            eff.attributes.append(_attribute(pf, r))
    graph = dict(plats).get(platform)
    if graph is None:
        if not plats:
            return eff
        platform, graph = plats[0]
    eff.platform, eff.graph_obj = platform, graph
    g = pf.obj(graph)
    for r in g.arr(2):
        o = pf.obj(r)
        eff.entry.append(EntrySlot(r, pf.str_field(o, 0), o.u32(1)))
    for k, r in enumerate(g.arr(4)):
        o = pf.obj(r)
        eff.events.append(EventSlot(r, k, pf.str_field(o, 0), _i32(o.u32(1)), o.arr(2)))
    cache: dict[int, Layer] = {}
    for k, r in enumerate(g.arr(3)):
        slot = pf.obj(r)
        ref = slot.u32(0)
        if ref in cache:   # one layer object in several slots (sub-spawners): share its contents
            eff.layers.append(replace(cache[ref], slot=k, slot_obj=r, inputs=slot.arr(1), outputs=slot.arr(2),
                                      children=[]))
        else:
            cache[ref] = _layer(pf, eff, ref, k, r, slot)
            eff.layers.append(cache[ref])
    names = {lay.slot: lay.name for lay in eff.layers}
    for lay in eff.layers:
        kids = []
        for e in lay.outputs:
            if e < len(eff.events):
                kids += [names[t] for t in eff.events[e].targets if t in names and names[t] not in kids]
        lay.children = kids
        trig = [eff.events[e].name for e in lay.inputs if e < len(eff.events)]
        lay.spawn = {"by": [t or "" for t in trig],
                     "event_streams": [s.data for s in eff.samplers if s.layer == lay.name and s.kind == "eventstream"]}
    index = {r.obj: k for k, r in enumerate(eff.renderers)}
    for lay in eff.layers:
        for r in lay.renderers:
            r.index = index[r.obj]
    return eff


def _load_flat(pf: PFile, eff: Effect) -> Effect:
    """A file without a CParticleEffect root (partial or hand-made test files): its layers in file order, and the
    renderers no layer lists in one pseudo layer "@renderers"; renderer indexes follow file order (as hd_particles)."""
    eff.platform, eff.platforms = "", []
    listed = set()
    for k, r in enumerate(pf.find("CLayerCompileCache")):
        lay = _layer(pf, eff, r, k, 0, PObj("CLayerGraphCompileCache_LayerSlot"))
        eff.layers.append(lay)
        listed.update(x.obj for x in lay.renderers)
    rest = [r for r in pf.find("CLayerCompileCacheRenderer") if r not in listed]
    if rest:
        lay = Layer(0, "@renderers", len(eff.layers))
        lay.renderers = [_renderer(pf, r, lay) for r in rest]
        eff.layers.append(lay)
    order = {r: k for k, r in enumerate(pf.find("CLayerCompileCacheRenderer"))}
    for lay in eff.layers:
        lay.spawn = {"by": [], "event_streams": []}
        for r in lay.renderers:
            r.index = order[r.obj]
    eff.layers.sort(key=lambda x: min([r.index for r in x.renderers], default=-1) if x.obj == 0 else -1)
    return eff


def _attribute(pf: PFile, r: int) -> Attribute:
    a = pf.obj(r)
    t = a.u32(1)
    name, width, integer = {1: ("bool", 1, True), 25: ("int", 1, True), 32: ("float2", 2, False),
                            33: ("float3", 3, False), 34: ("float4", 4, False), 0: ("float", 1, False),
                            31: ("float", 1, False)}.get(t, (schema.type_name(t), 4, False))
    if integer:
        take = lambda fid: a.vec(fid, "i")[:width]  # noqa: E731
        return Attribute(r, pf.str_field(a, 0) or "?", name, take(21), None, take(50) if a.has(50) else None,
                         pf.loc_text(a, 3) or pf.loc_text(a, 2))
    take = lambda fid: [_f(x) for x in a.vec(fid)[:width]]  # noqa: E731
    return Attribute(r, pf.str_field(a, 0) or "?", name, take(14), take(32) if a.has(32) else None,
                     take(44) if a.has(44) else None, pf.loc_text(a, 3) or pf.loc_text(a, 2))


def _sampler_data(pf: PFile, d: PObj) -> dict:
    kind = d.cls.replace("CParticleNodeSamplerData_", "")
    if kind == "Curve":
        t, v, g = d.arr(17, "f"), d.arr(18, "f"), d.arr(19, "f")
        dim = d.u32(10, 1) or 1
        n = len(t)
        vals = [[_f(x) for x in v[k * dim:(k + 1) * dim]] for k in range(n)]
        tin = [[_f(x) for x in g[2 * k * dim:(2 * k + 1) * dim]] for k in range(n)] if g else []
        tout = [[_f(x) for x in g[(2 * k + 1) * dim:(2 * k + 2) * dim]] for k in range(n)] if g else []
        return {"times": [_f(x) for x in t], "values": vals, "tangents_in": tin, "tangents_out": tout, "dim": dim,
                "interp": "linear" if d.u32(11, 0) == 1 else "hermite"}
    if kind == "EventStream":
        return {"data": [_f(x) for x in d.arr(11, "f")]}
    if kind == "Shape":
        return {"mesh": pf.str_field(d, 19), "raw": _raw_fields(d, (0, 1, 6, 19))}
    return {"raw": _raw_fields(d, (0, 1, 6))}


def _raw_fields(o: PObj, skip=()) -> dict:
    out = {}
    for fid, v, e in o.fields:
        if fid in skip:
            continue
        if len(v) % 4 == 0 and v:
            out[str(fid)] = [_f(x) for x in struct.unpack(f"<{len(v) // 4}f", v)]
        else:
            out[str(fid)] = v.hex()
    return out


def _layer(pf: PFile, eff: Effect, ref: int, slot_index: int, slot_obj: int, slot: PObj) -> Layer:
    o = pf.obj(ref)
    lay = Layer(ref, pf.str_field(o, 20) or f"@{ref}", slot_index, slot_obj, inputs=slot.arr(1),
                outputs=slot.arr(2))
    flds = []
    for r in o.arr(2):
        f = pf.obj(r)
        fid = f.u32(0) & 0x7FFFFFFF
        flds.append(Field(r, field_name(fid), schema.type_name(f.u32(1)), f.u32(2), fid))
    lay.fields = flds
    for r in o.arr(3):
        lay.attribs.append(pf.str_field(pf.obj(r), 0) or "?")
    for r in o.arr(7):
        s = pf.obj(r)
        d = pf.obj(s.u32(1)) if s.has(1) else None
        kind = SAMPLER_KINDS.get(d.cls.replace("CParticleNodeSamplerData_", ""), "?") if d else "?"
        smp = Sampler(r, pf.str_field(s, 0) or "", kind, _sampler_data(pf, d) if d else {}, s.u32(1), lay.name,
                      pf.loc_text(d, 0) if d else None)
        eff.samplers.append(smp)
        lay.samplers.append(smp.name)
    evs = o.arr(5) + ([o.u32(11)] if o.has(11) else [])
    for r in evs:
        e = pf.obj(r)
        pays = [{"name": pf.str_field(pf.obj(p), 0), "type": schema.type_name(pf.obj(p).u32(1)),
                 "size": pf.obj(p).u32(2)} for p in e.arr(2)]
        lay.events.append(Event(r, pf.str_field(e, 0), pays))
    for r in o.arr(9):
        s = pf.obj(r)
        rk = s.u32(1, None)
        desc = s.arr(4)
        names = [pf.string(x) for x in s.arr(5)]
        k = len(desc) // len(names) if names else 0
        ext = [{"name": n, "type": schema.type_name(desc[i * k + 1]) if k > 1 and desc[i * k + 1] != schema.NONE
                else None} for i, n in enumerate(names) if n is not None]
        prog = s.arr(2)
        eff.scripts[r] = ScriptRef(r, SCRIPT_KINDS.get(rk, f"kind{rk}"), lay.name, ext,
                                   [pf.string(x) for x in s.arr(6)], rk, prog[5] if len(prog) > 5 else 0)
        lay.scripts.append(r)
        if rk is None and lay.spawn_script is None:
            lay.spawn_script = r
        elif rk is not None:
            lay.evolve_scripts.append(r)
    for r in o.arr(8):
        lay.renderers.append(_renderer(pf, r, lay))
    lay.colors = color_constants(pf, ref)
    return lay


def _renderer(pf: PFile, r: int, lay: Layer) -> RendererDef:
    o = pf.obj(r)
    k = o.u32(0)
    mat = pf.str_field(o, 4)
    kind = RENDERER_KINDS.get(k, f"kind{k}")
    if k == 5 and mat and "decal" in mat.lower():
        kind = "decal"
    props = {}
    for q in o.arr(2):
        p = pf.obj(q)
        t = p.u32(1)
        props[pf.str_field(p, 0) or f"#{q}"] = {"obj": q, "type": PROP_TYPES.get(t, f"type{t}"), "type_id": t,
                                                 "value": prop_value(t, p.get(2)),
                                                 "string": pf.str_field(p, 3) if p.has(3) else None}
    en = props.get("EnableRendering")
    rd = RendererDef(r, 0, kind, mat, bool(en and en["value"]), props, {}, lay.name)
    for q in o.arr(1):
        p = pf.obj(q)
        name = pf.str_field(p, 2) if p.has(2) else f"input{p.u32(0)}"
        fi = p.u32(1)
        if fi < len(lay.fields):
            rd.inputs[name] = lay.fields[fi].name
            lay.fields[fi].inputs.append(f"{r}:{name}")
        else:
            rd.inputs[name] = None
    return rd


# ---------------------------------------------------------------------- edits
def _select(eff: Effect, sel) -> list[RendererDef]:
    rs = eff.renderers
    if sel is None or sel == "*":
        return rs
    if isinstance(sel, bool):
        raise ValueError("bad renderer selector")
    if isinstance(sel, int):
        return [rs[sel]]
    if isinstance(sel, list):
        return [r for s in sel for r in _select(eff, s)]
    if isinstance(sel, str):
        if sel.startswith("layer:"):
            return eff.layer(sel[6:]).renderers
        if sel.startswith("obj:"):
            return [r for r in rs if r.obj == int(sel[4:])]
        if sel in RENDERER_KINDS.values():
            return [r for r in rs if r.kind == sel]
        if sel.isdigit():
            return [rs[int(sel)]]
        return [r for r in rs if any(sel in (p["string"] or "") for p in r.props.values())]
    raise ValueError(f"bad renderer selector {sel!r}")


def _sampler(eff: Effect, sel) -> Sampler:
    if isinstance(sel, int):
        hits = [s for s in eff.samplers if sel in (s.obj, s.data_obj)]
    elif "/" in str(sel):
        lay, name = str(sel).split("/", 1)
        hits = [s for s in eff.samplers if s.layer == lay and (s.name == name or s.label == name)]
    else:
        hits = [s for s in eff.samplers if s.name == sel or s.label == sel]
        if len({h.data_obj for h in hits}) == 1:
            hits = hits[:1]
    if len(hits) != 1:
        raise ValueError(f"sampler {sel!r}: {len(hits)} matches (use '<layer>/<name>' or an object number)")
    return hits[0]


def _flat(rows) -> list[float]:
    return [float(x) for row in rows for x in (row if isinstance(row, (list, tuple)) else [row])]


def _op_prop(pf, eff, op, name, value):
    n = 0
    for r in _select(eff, op.get("renderer", "*")):
        p = r.props.get(name)
        if p is None:
            if op.get("renderer", "*") == "*":
                continue
            raise ValueError(f"renderer {r.index} has no property {name!r}")
        pf.obj(p["obj"]).set(2, prop_bytes(p["type_id"], value))
        n += 1
    if not n:
        raise ValueError(f"no selected renderer has a property {name!r}")
    return n


def _op_texture(pf, eff, op):
    if "replace" in op:   # {old path: new path} for every user of the string (hd_particles.replace_string)
        n = 0
        for old, new in op["replace"].items():
            c = pf.replace_string(old, new)
            if not c:
                raise ValueError(f"the file names no {old}")
            n += c
        return n
    path = op["path"]
    if "old" in op and "name" not in op:
        n = 0
        for r in _select(eff, op.get("renderer", "*")):
            for p in r.props.values():
                if p["string"] == op["old"]:
                    pf.set_str(pf.obj(p["obj"]), 3, path)
                    n += 1
        if not n:
            raise ValueError(f"no selected renderer names {op['old']!r}")
        return n
    n = 0
    for r in _select(eff, op.get("renderer", "*")):
        p = r.props.get(op["name"])
        if p is None or p["string"] is None and p["type_id"] not in (12, 13, 14, 15):
            if op.get("renderer", "*") == "*":
                continue
            raise ValueError(f"renderer {r.index}: {op['name']!r} is not a file property")
        pf.set_str(pf.obj(p["obj"]), 3, path)
        n += 1
    if not n:
        raise ValueError(f"no selected renderer has a file property {op['name']!r}")
    return n


def _op_curve(pf, eff, op):
    s = _sampler(eff, op["sampler"])
    if s.kind != "curve":
        raise ValueError(f"sampler {op['sampler']!r} is a {s.kind}, not a curve")
    d = pf.obj(s.data_obj)
    dim = s.data["dim"]
    times = op.get("times", s.data["times"])
    vals = op.get("values", s.data["values"])
    n = len(times)
    fv = _flat(vals)
    if len(fv) != n * dim:
        raise ValueError(f"curve: {len(fv)} values for {n} keys of {dim} components")
    if any(b < a for a, b in zip(times, times[1:])):
        raise ValueError("curve: key times must not decrease")
    d.set_arr(17, [float(t) for t in times], "f")
    d.set_arr(18, fv, "f")
    has_tan = bool(s.data["tangents_in"]) or "tangents_in" in op or "tangents_out" in op
    if has_tan:
        tin = op.get("tangents_in", s.data["tangents_in"] if len(s.data["tangents_in"]) == n else [[0.0] * dim] * n)
        tout = op.get("tangents_out", s.data["tangents_out"] if len(s.data["tangents_out"]) == n else [[0.0] * dim] * n)
        ti, to = _flat(tin), _flat(tout)
        if len(ti) != n * dim or len(to) != n * dim:
            raise ValueError("curve: tangents need one vector per key")
        d.set_arr(19, [x for k in range(n) for x in ti[k * dim:(k + 1) * dim] + to[k * dim:(k + 1) * dim]], "f")
    return 1


def _script_consts(prog: list[int]) -> tuple[int, int]:
    """(byte offset of the constants in the program, constant count)."""
    return 36 + prog[1], prog[5]


def _op_const(pf, eff, op):
    """Script constant `index` of `script` (object number) gets `values` (repeated over 8 lanes; "kind": "i" for ints).
    The same constant of identical copies of the script (kinds 4/5) and of the layer's constant table change too."""
    ref = int(op["script"])
    o = pf.obj(ref)
    if o.cls != "CCompilerBlobCache":
        raise ValueError(f"object {ref} is {o.cls}, not a script")
    prog = bytearray(o.get(2))
    words = o.arr(2)
    at, cnt = _script_consts(words)
    i = int(op["index"])
    if not 0 <= i < cnt:
        raise ValueError(f"script {ref} has {cnt} constants")
    vals = list(op["values"])
    new = struct.pack("<8i" if op.get("kind") == "i" else "<8f", *((vals * 8)[:8]))
    old = bytes(prog[at + 32 * i:at + 32 * i + 32])
    if old == new:
        return 0
    lay = next((lay for lay in eff.layers if ref in lay.scripts), None)
    copies = [ref] + [s for s in (lay.scripts if lay else []) if s != ref and pf.obj(s).get(2) == bytes(prog)]
    for s in copies:
        so = pf.obj(s)
        b = bytearray(so.get(2))
        b[at + 32 * i:at + 32 * i + 32] = new
        so.set(2, bytes(b), 4)
    if lay is not None:
        _replace_table(pf.obj(lay.obj), old, new)
    return len(copies)


def _replace_table(lo: PObj, old: bytes, new: bytes) -> None:
    ent = lo.elems(0)
    changed = False
    for k in range(0, len(ent) - 1, 2):
        if ent[k] + ent[k + 1] == old:
            ent[k], ent[k + 1] = new[:16], new[16:]
            changed = True
    if changed:
        lo.set(0, b"".join(ent), 16)
        lo.set(1, b"".join(new[:16] if e == old[:16] else e for e in lo.elems(1)), 16)


def _op_color(pf, eff, op):
    """A colour constant of a layer (`index` from Layer.colors) set to `value`, or every colour of the selected layers
    (`layer`: name or "*") shifted by `shift` {hue (degrees), sat, val (multipliers)}; or, with `stream` and `rgba`,
    a colour stream of the scripts tinted (hd_cbvm.tint). Rewrites the constant in the
    layer's tables and in every compiled script of the layer that carries it (as the mod studio's SetColorConstant)."""
    if "stream" in op:    # tint a colour stream in the scripts (hd_cbvm.tint), as fx/api.py's local op
        cb = _hd_cbvm()
        out, n = cb.tint(pf.data(), op["stream"], [float(x) for x in op.get("rgba", op.get("value"))])
        _replace_all(pf, PFile.read(out))
        return n
    lays = eff.layers if op.get("layer", "*") == "*" else [eff.layer(op["layer"])]
    n = 0
    for lay in lays:
        for c in lay.colors:
            if "index" in op and c["index"] != op["index"]:
                continue
            if "value" in op:
                v = list(op["value"])
            elif "shift" in op:
                sh = op["shift"]
                h, s, val = colorsys.rgb_to_hsv(*c["value"][:3]) if max(c["value"][:3]) <= 1 else _hsv(c["value"][:3])
                h = (h + sh.get("hue", 0) / 360.0) % 1.0
                rgb = colorsys.hsv_to_rgb(h, min(1.0, s * sh.get("sat", 1)), val * sh.get("val", 1))
                v = list(rgb) + c["value"][3:]
            else:
                raise ValueError("color op needs value or shift")
            if len(v) != c["width"] or any(x < 0 for x in v):
                raise ValueError(f"colour {c['index']} of {lay.name} takes {c['width']} non-negative numbers")
            n += _set_color(pf, lay, c, v)
    if not n:
        raise ValueError("no colour constant changed")
    return n


def _hsv(rgb):
    mx = max(rgb)
    h, s, _v = colorsys.rgb_to_hsv(*[x / mx for x in rgb]) if mx > 0 else (0, 0, 0)
    return h, s, mx


def _set_color(pf, lay: Layer, c: dict, v) -> int:
    lo = pf.obj(lay.obj)
    ent = lo.elems(0)
    i, w = c["index"], c["width"]
    old = ent[i] + ent[i + 1]
    new = struct.pack("<8f", *[float(v[k % w]) for k in range(8)])
    if old == new:
        return 0
    hits = 0
    for s in lay.scripts:
        so = pf.obj(s)
        b = so.get(2)
        if old in b:
            hits += b.count(old)
            so.set(2, b.replace(old, new), 4)
    if not hits:
        raise ValueError(f"the scripts of {lay.name} do not carry colour {i}; left unchanged")
    _replace_table(lo, old, new)
    return hits


def _hd_cbvm():
    from hdkit.fx import cbvm
    return cbvm


def _replace_all(pf: PFile, new: PFile) -> None:
    pf.objects, pf.strings, pf.classes = new.objects, new.strings, new.classes


def _op_script(pf, eff, op):
    """A script replaced by a CBVM listing (hd_cbvm text form). `.hfx` sources need fx/lang (not here yet).
    fx/api.py has its own script path (language detection, parse errors), so `script` is not in OPS."""
    cb = _hd_cbvm()
    from hdkit.formats import hd_particles as hp
    text = op["text"]
    if ".backend" not in text and ".order" not in text:
        raise NotImplementedError("script op: only CBVM listings (hd_cbvm dis) for now; .hfx needs fx/lang")
    ref = int(op["script"])
    s = cb.script_parse(text, ref - 1)
    _replace_all(pf, PFile.read(cb.write_scripts(hp.ParticleFile(pf.data()), [s])))
    return 1


# ---- structure: copy_layer / remove_layer
def _graphs(pf: PFile, platform: str | None) -> list[int]:
    return [g for p, g in platforms(pf) if platform in (None, "all", p)]


def _remove_slot(pf: PFile, graph: int, k: int, done: set) -> None:
    """Layer slot k leaves graph `graph`; event slots' sources/targets are renumbered (event slot objects are shared by
    the platform graphs, so each is renumbered once: `done`)."""
    g = pf.obj(graph)
    slots = g.arr(3)
    g.set_arr(3, slots[:k] + slots[k + 1:])
    for e in g.arr(4):
        if e in done:
            continue
        done.add(e)
        eo = pf.obj(e)
        if eo.has(2):
            eo.set_arr(2, [t - (t > k) for t in eo.arr(2) if t != k])
        if eo.has(1):
            src = _i32(eo.u32(1))
            if src == k:
                eo.set_arr(2, [])
            elif src > k:
                eo.set_u32(1, src - 1)
        elif k == 0:
            eo.set_arr(2, [])


def _slot_of(pf: PFile, graph: int, name: str) -> int | None:
    for k, r in enumerate(pf.obj(graph).arr(3)):
        if pf.str_field(pf.obj(pf.obj(r).u32(0)), 20) == name or f"@{pf.obj(r).u32(0)}" == name:
            return k
    return None


def _op_remove_layer(pf, eff, op):
    """Every slot of a layer (by name) leaves every platform graph (they share their event slots, so structure edits
    are made on all of them); objects only it used are removed and references renumbered. Layers spawned only by its
    events stay in the file but are no longer spawned."""
    name = op["layer"]
    before = set(pf.unreachable())
    n, done = 0, set()
    while True:
        hits = [(g, _slot_of(pf, g, name)) for g in _graphs(pf, None)]
        hits = [(g, k) for g, k in hits if k is not None]
        if not hits:
            break
        for g, k in hits:
            _remove_slot(pf, g, k, done)
        done.clear()
        n += 1
    if not n:
        raise ValueError(f"no layer {name!r}")
    gone = [r for r in pf.unreachable() if r not in before]
    pf.remove(gone)
    return n


def _op_copy_layer(pf, eff, op):
    """Copy a layer (and everything it references) from another effect; it gets a new layer slot in every platform
    graph, spawned by the event that spawns it in the source (matched by event name and emitting layer name, else the
    target's first event of the same name, else the root's first output event). Child layers are not copied. Like
    remove_layer it changes every platform graph (they share their event slots)."""
    src_data, _ = _read(op["from"])
    spf = PFile.read(src_data)
    seff = load(b"", pf=spf, platform=op.get("source_platform", "PC"))
    sl = seff.layer(op["layer"])
    trig = []
    for e in sl.inputs:
        if e < len(seff.events):
            ev = seff.events[e]
            src_layer = next((x.name for x in seff.layers if x.slot == ev.source), None)
            trig.append((ev.name, src_layer))
    mp = pf.copy_from(spf, [sl.obj])
    new_layer = mp[sl.obj]
    if op.get("as"):
        pf.set_str(pf.obj(new_layer), 20, op["as"])
    # effect attributes the layer reads (__a_<Name>) that this effect lacks
    root = pf.obj(1)
    if root.has(3):
        alist = pf.obj(root.u32(3))
        have = {pf.str_field(pf.obj(r), 0) for r in alist.arr(0)}
        sroot = spf.obj(1)
        sdecl = {spf.str_field(spf.obj(r), 0): r for r in spf.obj(sroot.u32(3)).arr(0)} if sroot.has(3) else {}
        for a in sl.attribs:
            nm = a[4:] if a.startswith("__a_") else a
            if nm not in have and nm in sdecl:
                m = pf.copy_from(spf, [sdecl[nm]])
                alist.set_arr(0, alist.arr(0) + [m[sdecl[nm]]])
                have.add(nm)
    n, done = 0, set()
    for g in _graphs(pf, None):
        teff = load(b"", pf=pf, platform=pf.str_field(pf.obj(g), 0))
        go = pf.obj(g)
        k = len(go.arr(3))
        ins = []
        for name, src_layer in trig:
            cand = [e for e in teff.events if e.name == name and any(
                x.slot == e.source and x.name == src_layer for x in teff.layers)]
            cand = cand or [e for e in teff.events if e.name == name]
            if cand:
                ins.append(cand[0].index)
        if not ins:
            root_slot = next((x for x in teff.layers if x.name == "Root"), teff.layers[0] if teff.layers else None)
            if root_slot and root_slot.outputs:
                ins.append(root_slot.outputs[0])
            elif teff.events:
                ins.append(0)
        slot = pf.new("CLayerGraphCompileCache_LayerSlot", layer=new_layer, inputs=ins)
        go.set_arr(3, go.arr(3) + [pf.add(slot)])
        for e in ins:
            ref = go.arr(4)[e]
            if ref not in done:   # event slots are shared by the platform graphs
                done.add(ref)
                eo = pf.obj(ref)
                eo.set_arr(2, eo.arr(2) + [k])
        n += 1
    return n


OPS = {"prop": lambda pf, eff, op: _op_prop(pf, eff, op, op["name"], op["value"]),
       "enable": lambda pf, eff, op: _op_prop(pf, eff, op, "EnableRendering", bool(op.get("on", True))),
       "texture": _op_texture, "curve": _op_curve, "const": _op_const, "color": _op_color,
       "copy_layer": _op_copy_layer, "remove_layer": _op_remove_layer}
_ALL_OPS = {**OPS, "script": _op_script}


def apply_edits(data: bytes, ops: list[dict], platform: str = "PC") -> bytes:
    """Apply edit ops in order (docs/hdfx.md section 1); returns the new file. Renderer selectors (`renderer`): an
    index into `Effect.renderers`, "layer:<name>", "obj:<n>", a kind, a path fragment, a list of those, or "*".
    Renderer/curve/colour edits act on the chosen platform's graph (PC: what D2R reads; layers shared between
    platforms change for all of them); copy_layer/remove_layer always act on every platform graph."""
    pf = PFile.read(bytes(data))
    for op in ops:
        fn = _ALL_OPS.get(op.get("op"))
        if fn is None:
            raise ValueError(f"unknown op {op.get('op')!r} (known: {', '.join(_ALL_OPS)})")
        eff = load(b"", pf=pf, platform=op.get("platform", platform) if op["op"] not in (
            "copy_layer", "remove_layer") else platform)
        fn(pf, eff, op)
    return pf.data()
