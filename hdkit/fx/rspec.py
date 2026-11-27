"""What the renderer needs of an effect: its renderers with typed properties and their particle inputs.

`load(data_or_path) -> RenderEffect`. Until lane format's `fx/graph.py` lands this reads the `.particles` objects itself
with a small subset of the field-size schema of reimagined-d2r-mod-studio's `ParticleFile.cs` (MIT, D2R-Reimagined;
credited in hdkit/THIRD_PARTY.md).
`from_graph(effect)` takes a graph.Effect (docs/hdfx.md section 1) when it exists.

Read from the file (found on Mephisto's ambient effect, layer CylinderInf):
  - CLayerCompileCache: field 20 the layer name (string), 2 its fields, 8 its renderers, 0 the layer's constant table:
    32-byte groups (8 lanes), group k holding the value of every field whose descriptor names slot k;
  - CLayerCompileCacheField: 1 type (31 float, 33 float3, 34 float4, 36 orientation, 25 int, 26 int2, 28 int4, 1 bool),
    3 flags (5: set at spawn, 1: evolved; 57: the position stream), 4 the constant slot (absent: not constant);
  - CLayerCompileCacheRendererParticleInput: 2 the input name (string; absent for the built-in inputs, whose field 0 is
    a semantic id: absent position, 1 scale, 2 enabled, 14 orientation), 1 the index of the layer field that feeds it, 3 its type.
A renderer input fed by a constant field gets that value (`const`); the others name the field (`field`), whose values
the simulation writes (lane vm's SimState).
"""
import struct
from dataclasses import dataclass, field
from pathlib import Path

T_BOOL, T_INT, T_FLOAT, T_TEXTURE, T_MESH = 1, 3, 7, 12, 14
KIND = {0: "billboard", 1: "ribbon", 2: "mesh", 3: "triangle", 4: "decal", 5: "light", 6: "sound"}  # as graph
FIELD_TYPE = {31: ("float", 1), 33: ("float3", 3), 34: ("float4", 4), 36: ("orientation", 4), 25: ("int", 1),
              26: ("int2", 2), 28: ("int4", 4), 1: ("bool", 1), 5: ("bool", 1), 32: ("float2", 2), 27: ("int3", 3)}
SEMANTIC = {-1: "Position", 1: "Scale", 2: "Enabled", 14: "Orientation"}     # built-in inputs (field 0; absent: -1)
_S = {"CLayerCompileCache": "0:a16 1:a16 2:a4 3:a4 4:a4 5:a4 6:a4 7:a4 8:a4 9:a4 10:a4 11:4 12:4 13:1 14:1 15:1 16:4 17:4 "
                            "18:4 19:16 20:4 21:4 22:4 23:4 24:4 25:4 26:4 27:4 28:4 29:4 30:4 31:4",
      "CLayerCompileCacheField": "0:4 1:4 2:4 3:4 4:4 5:4",
      "CLayerCompileCacheRenderer": "0:4 1:a4 2:a4 3:4 4:4",
      "CLayerCompileCacheRendererProperty": "0:4 1:4 2:16 3:4",
      "CLayerCompileCacheRendererParticleInput": "0:4 1:4 2:4 3:4"}
SCHEMA = {k: {int(a): (-int(b[1:]) if b[0] == "a" else int(b)) for a, b in (f.split(":") for f in v.split())}
          for k, v in _S.items()}


@dataclass
class Input:
    name: str
    field: int | None = None        # index in the layer's field list
    type: str = "float"
    const: tuple | None = None      # value when the field is constant


@dataclass
class RSpec:
    index: int                      # renderer index in the file (hd_particles order)
    obj: int                        # object number (0-based)
    kind: str
    material: str
    enabled: bool
    layer: str
    props: dict = field(default_factory=dict)      # name -> bool | int | float | tuple | str (texture/mesh path) | None
    inputs: dict = field(default_factory=dict)     # input name -> Input

    def on(self, feature: str) -> bool:
        return bool(self.props.get(feature))

    def get(self, name, default=None):
        v = self.props.get(name)
        return default if v is None else v

    def textures(self) -> dict:
        return {k: v for k, v in self.props.items() if isinstance(v, str) and v.endswith(".texture")}


@dataclass
class RenderEffect:
    path: str
    renderers: list
    notes: list = field(default_factory=list)

    def layers(self) -> list:
        out = []
        for r in self.renderers:
            if r.layer not in out:
                out.append(r.layer)
        return out


def _objects(b: bytes):
    _m, _ver, _build, _nobj, ncls, so = struct.unpack_from("<6I", b, 0)
    n = struct.unpack_from("<I", b, so)[0]
    p, strs = so + 4, []
    for _ in range(n):
        strs.append(b[p + 1:p + 1 + b[p]].decode("latin-1"))
        p += 1 + b[p]
    p, objs = 28 + 8 * ncls, []
    while p < so:
        ln, cl = struct.unpack_from("<I", b, p)[0], struct.unpack_from("<I", b, p + 5)[0]
        objs.append((strs[cl], b[p + 9:p + 4 + ln]))
        p += 4 + ln
    return strs, objs


def _decode(cls, d):
    sch = SCHEMA[cls]
    nf, q, out = struct.unpack_from("<H", d, 0)[0], 2, {}
    for _ in range(nf):
        fid = struct.unpack_from("<H", d, q)[0]
        q += 2
        sz = sch[fid]
        if sz < 0:
            c = struct.unpack_from("<I", d, q)[0]
            out[fid] = d[q + 4:q + 4 - sz * c]
            q += 4 - sz * c
        else:
            out[fid] = d[q:q + sz]
            q += sz
    return out


def _u32(v):
    return struct.unpack("<I", v)[0]


def _u32s(v):
    return list(struct.unpack("<%dI" % (len(v) // 4), v))


def _prop_value(ptype, raw, string, strs):
    if ptype == T_TEXTURE or ptype == T_MESH:
        return strs[string] if string is not None and string < len(strs) else None
    if ptype is None:                                   # a feature switch: 0xFF * 16 on
        return bool(raw) and raw[:1] == b"\xff"
    if raw is None:
        return 0.0 if ptype == T_FLOAT else (False if ptype == T_BOOL else 0)
    if ptype == T_FLOAT:
        f = struct.unpack("<4f", raw)
        return f[0] if f[1:] == (1.0, 1.0, 1.0) or f[1:] == (0.0, 0.0, 0.0) else f
    if ptype == T_BOOL:
        return struct.unpack("<i", raw[:4])[0] != 0
    i = struct.unpack("<4i", raw)
    return i[0] if i[1:] == (0, 0, 0) else i


def _const(table: bytes, slot: int, ftype: str, ncomp: int):
    off = slot * 32
    if table is None or off + 4 * ncomp > len(table):
        return None
    fmt = "<%d%s" % (ncomp, "i" if ftype.startswith(("int", "bool")) else "f")
    return struct.unpack_from(fmt, table, off)


def load(src, platform: str = "PC", use_graph: bool = True) -> RenderEffect:
    """RenderEffect of a `.particles` file (bytes or path): the renderers of one platform's layer graph (a file holds
    PC, Switch, PS4 and PS5 graphs, each with its own copies of the renderers). Lane format's graph.load is used when it
    is importable (`use_graph`), with the constant inputs added from this module's reader."""
    path = ""
    if isinstance(src, (str, Path)):
        path = str(src)
        src = Path(src).read_bytes()
    own = _load(src, platform)
    own.path = path
    if use_graph:
        try:
            from . import graph
        except ImportError:
            return own
        g = from_graph(graph.load(src, platform=platform))
        g.path = path
        consts = {r.obj + 1: r for r in own.renderers}        # graph object numbers start at 1
        for r in g.renderers:
            o = consts.get(r.obj)
            if o is None:
                continue
            for name, inp in r.inputs.items():
                if name in o.inputs:
                    inp.const, inp.type = o.inputs[name].const, o.inputs[name].type
        g.notes.append("renderers from fx.graph (%s graph)" % platform)
        return g
    return own


def _platform_caches(strs, objs, platform):
    """Object indices (0-based) of the CLayerCompileCache objects the `platform` layer graph reaches (None: no graph)."""
    graphs = [i for i, (c, _d) in enumerate(objs) if c == "CLayerGraphCompileCache"]
    if not graphs:
        return None
    pick = None
    for gi in graphs:
        o = _decode_any(objs[gi])
        if 0 in o and _u32(o[0]) < len(strs) and strs[_u32(o[0])] == platform:
            pick = o
            break
    if pick is None:
        pick = _decode_any(objs[graphs[0]])
    out = set()
    for si in _u32s(pick.get(3, b"")):
        so = _decode_any(objs[si - 1])
        if 0 in so:
            out.add(_u32(so[0]) - 1)
    return out


def _decode_any(obj):
    c, d = obj
    if c in SCHEMA:
        return _decode(c, d)
    sch = {"CLayerGraphCompileCache": {0: 4, 2: -4, 3: -4, 4: -4, 6: 16},
           "CLayerGraphCompileCache_LayerSlot": {0: 4, 1: -4, 2: -4}}.get(c)
    if sch is None:
        return {}
    SCHEMA[c] = sch
    return _decode(c, d)


def _load(src: bytes, platform: str) -> RenderEffect:
    strs, objs = _objects(src)
    caches = _platform_caches(strs, objs, platform)
    layer_of, fields_of, table_of = {}, {}, {}
    for i, (c, d) in enumerate(objs):
        if c != "CLayerCompileCache" or (caches is not None and i not in caches):
            continue
        o = _decode(c, d)
        name = strs[_u32(o[20])] if 20 in o and _u32(o[20]) < len(strs) else "layer%d" % i
        fl = [x - 1 for x in _u32s(o.get(2, b""))]
        for r in _u32s(o.get(8, b"")):
            layer_of[r - 1] = name
            fields_of[r - 1] = fl
            table_of[r - 1] = o.get(0)
    eff = RenderEffect("", [])
    k = 0
    for i, (c, d) in enumerate(objs):
        if c != "CLayerCompileCacheRenderer" or i not in layer_of:
            continue
        o = _decode(c, d)
        kind = KIND.get(_u32(o[0]) if 0 in o else 0, "kind%d" % (_u32(o[0]) if 0 in o else 0))
        mat = strs[_u32(o[4])] if 4 in o else ""
        props = {}
        for pi in _u32s(o.get(2, b"")):
            pc, pd = objs[pi - 1]
            if pc != "CLayerCompileCacheRendererProperty":
                continue
            p = _decode(pc, pd)
            name = strs[_u32(p[0])]
            props[name] = _prop_value(_u32(p[1]) if 1 in p else None, p.get(2), _u32(p[3]) if 3 in p else None, strs)
        if kind == "light" and ("Decal" in props or "decal" in mat.lower()):
            kind = "decal"
        fl, table = fields_of.get(i, []), table_of.get(i)
        inputs = {}
        for ii in _u32s(o.get(1, b"")):
            ic, idd = objs[ii - 1]
            if ic != "CLayerCompileCacheRendererParticleInput":
                continue
            x = _decode(ic, idd)
            name = strs[_u32(x[2])] if 2 in x else SEMANTIC.get(_u32(x[0]) if 0 in x else -1, "semantic%d" %
                                                                 (_u32(x[0]) if 0 in x else -1))
            fidx = _u32(x[1]) if 1 in x else None
            inp = Input(name, fidx)
            if fidx is not None and fidx < len(fl):
                fo = _decode("CLayerCompileCacheField", objs[fl[fidx]][1])
                ftype, ncomp = FIELD_TYPE.get(_u32(fo[1]) if 1 in fo else 31, ("float", 1))
                inp.type = ftype
                if 4 in fo and _u32(fo[4]) != 0xFFFFFFFF and (_u32(fo[3]) if 3 in fo else 0) & 4:
                    inp.const = _const(table, _u32(fo[4]), ftype, ncomp)
                elif (_u32(fo[3]) if 3 in fo else 0) & 4:
                    inp.const = (0,) * ncomp if ftype.startswith(("int", "bool")) else (0.0,) * ncomp
            inputs[name] = inp
        enabled = bool(props.get("EnableRendering", True))
        eff.renderers.append(RSpec(k, i, kind, mat, enabled, layer_of.get(i, "?"), props, inputs))
        k += 1
    return eff


GRAPH_INPUTS = {"input0": "Position", "input1": "Scale", "input2": "Enabled", "input14": "Orientation"}


def from_graph(effect) -> RenderEffect:
    """RenderEffect of a lane-format graph.Effect (RendererDef: obj, index, kind, material, enabled, props {name: {obj,
    type, type_id, value, string}}, inputs {renderer input: field name}, layer). Constant inputs are left to the
    simulation (lane vm fills them from the layer's constant table); `Input.field` is the field's name there."""
    out = RenderEffect(getattr(effect, "path", ""), [])
    rds = getattr(effect, "renderers", None) or [rd for lay in effect.layers for rd in lay.renderers]
    for k, rd in enumerate(rds):
        props = {}
        for name, p in (rd.props or {}).items():
            v = (p.get("string") or p.get("value")) if isinstance(p, dict) else p
            props[name] = tuple(v) if isinstance(v, list) else v
        inputs = {}
        for name, fname in (rd.inputs or {}).items():
            nm = GRAPH_INPUTS.get(name, name)
            inputs[nm] = Input(nm, fname)
        out.renderers.append(RSpec(getattr(rd, "index", k) or k, rd.obj, rd.kind, rd.material, bool(rd.enabled),
                                   getattr(rd, "layer", "") or "?", props, inputs))
    return out


def summary(eff: RenderEffect) -> list:
    """One line per renderer: index, kind, layer, material, switched-on features, constant inputs."""
    lines = []
    for r in eff.renderers:
        feats = [k for k, v in r.props.items() if v is True and "." not in k]
        consts = {k: v.const for k, v in r.inputs.items() if v.const is not None}
        lines.append("%d %s %s %s on=%s const=%s" % (r.index, r.kind, r.layer, r.material.split("/")[-1], feats, consts))
    return lines
