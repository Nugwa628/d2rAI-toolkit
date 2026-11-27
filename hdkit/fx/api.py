"""The pure-Python API of the hdfx tools: what every MCP tool (`view/mcp_server.py`) and the web editor (`view/webserve.py`)
call. JSON-able values in and out, files as paths, no `mcp` import. Contract: docs/hdfx.md section 5, schemas and examples:
docs/hdfx-mcp.md.

  fx_list fx_inspect fx_edit fx_session fx_validate fx_create fx_script_read fx_script_write fx_render fx_export fx_coverage
  fx_fired_by

`call(name, **args)` runs one tool under a lock and turns every failure into `{"ok": False, "error": {code, message, ...}}`.

Lanes that are not merged yet: this module works on `hdkit.formats.hd_particles` and `hdkit.formats.hd_cbvm` and reaches the other lanes
through `lane(name)`, which imports `hdkit.fx.<name>` when it exists. A part that needs a missing module raises
`FxError("not_implemented", needs="lane <x>")` (`need(name)`), so the tools exist now and light up on merge:
  graph, pfile (lane format): `graph.load(bytes, vfs=)`, `graph.apply_edits(data, ops)` (ops listed in `graph.OPS`, else the
                              local fallback below runs), `Effect.to_json()`
  sim (lane vm): `Sim(effect, seed, attrs, ctx)`, `.seek(t)`, `.step(dt)`, `.state()`
  render (lane render): `FxRenderer(gpu, vfs)`, `.prepare(effect, state, cam)`, `.render(draws, cam, size, base, light, post)`,
                        and `camera_from_spec(spec: dict, size, state) -> cam` (the `cam` argument of fx_render)
  context (lane context, landed): `attach / missile / skill / scene`, `effects_of_*`, `Tables`, `unit_path`, `drive(driver,
                          make_sim, fps, duration, seed)`; a map spot's `.light`/`.post` go to the renderer
  render also: optional `base_scene(gpu, vfs, unit=, scene=) -> base` (the opaque unit/map the effect is drawn over)
  lang (lane lang): `compile(src, effect=None) -> hd_cbvm.Script`, `decompile(Script) -> str`
  coverage (lane survey, landed): `survey_file(path, rel) -> dict`, `assess(dict) -> {status, missing, ...}`
"""
from __future__ import annotations

import fnmatch
import hashlib
import importlib
import inspect
import json
import os
import re
import struct
import sys
import threading
import typing
from pathlib import Path

from hdkit.formats import hd_particles as hp
from hdkit.fx import cbvm as cb
from hdkit.view import vfs as vfs_mod

LOCK = threading.RLock()
NEEDS = {"graph": "lane format", "pfile": "lane format", "sim": "lane vm", "render": "lane render",
         "context": "lane context", "lang": "lane lang", "coverage": "lane survey"}
TYPE_NAMES = {hp.T_BOOL: "bool", hp.T_INT: "int", hp.T_FLOAT: "float", hp.T_TEXTURE: "texture", hp.T_MESH: "mesh"}
CONTEXT_KINDS = ("bone", "unit", "missile", "skill", "scene")
OPS_ALL = ("prop", "texture", "enable", "color", "const", "script", "curve", "copy_layer", "remove_layer")
GPU_PROVIDER = None     # a callable returning the shared hdkit.view.gpu.Renderer (the MCP server sets it); else made here
_GPU = {}


class FxError(Exception):
    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra

    def as_dict(self) -> dict:
        return {"ok": False, "error": {"code": self.code, "message": self.message, **self.extra}}


# ---------------------------------------------------------------------------------------------------- lanes (seams)
def lane(name: str):
    """`hdkit.fx.<name>` or None when that lane has not landed (an import error inside the module still raises)."""
    try:
        return importlib.import_module("hdkit.fx." + name)
    except ModuleNotFoundError as e:
        if e.name == "hdkit.fx." + name:
            return None
        raise


def need(name: str, what: str = ""):
    m = lane(name)
    if m is None:
        raise FxError("not_implemented", "%s needs hdkit.fx.%s (%s has not landed)" % (what or name, name, NEEDS[name]),
                      needs=NEEDS[name])
    return m


def engine() -> dict:
    return {k: lane(k) is not None for k in ("graph", "sim", "render", "context", "lang", "coverage")}


# ---------------------------------------------------------------------------------------------------- paths, roots, outputs
def default_roots() -> list:
    return [r for r in os.environ.get("HDKIT_VIEW_ROOTS", "").split(os.pathsep) if r]


def make_fs(roots) -> vfs_mod.Layered:
    return vfs_mod.Layered(vfs_mod.parse_roots(roots or default_roots()))


def out_root() -> Path:
    return Path(os.environ.get("HDKIT_FX_OUT") or os.environ.get("HDKIT_VIEW_OUT") or (Path.cwd() / "hdview-out")).resolve()


def _hash(*a) -> str:
    return hashlib.sha256(json.dumps(a, sort_keys=True, default=str).encode()).hexdigest()[:8]


def out_dir(tool: str, name: str, *key) -> Path:
    return out_root() / tool / ("%s_%s" % (name, _hash(*key)))


def _stem(p: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-]", "_", Path(str(p).replace("\\", "/")).name.split(".")[0]) or "effect"


class Source:
    """An effect's bytes and where they came from."""

    def __init__(self, data, file, logical=None, root=None, session=None):
        self.data, self.file, self.logical, self.root, self.session = data, file, logical, root, session

    @property
    def name(self) -> str:
        return _stem(self.logical or str(self.file))

    @property
    def key(self) -> str:
        if self.session:                    # the edited bytes count: a render of version 2 is not the render of version 0
            return "%s@%s" % (self.session.source, hashlib.sha256(self.data).hexdigest()[:12])
        return self.logical or str(self.file)


def _logical_of(file: Path, fs) -> tuple[str | None, str | None]:
    for name, root in fs.roots:
        if hasattr(root, "find"):
            continue
        try:
            rel = file.resolve().relative_to(Path(root).resolve())
        except ValueError:
            continue
        return vfs_mod.normalise(rel.as_posix()), name
    return None, None


def open_source(path, roots=None, fs=None) -> Source:
    """A logical game path, a file, a session folder or the working file of a session."""
    if not path or not isinstance(path, (str, os.PathLike)):
        raise FxError("bad_args", "path: a logical game path, a file or a session folder")
    fs = fs or make_fs(roots)
    p = Path(path)
    if p.is_dir() and (p / "session.json").is_file():
        s = Session.open(p)
        return Source(s.read(), s.work, s.logical, None, s)
    if p.is_file():
        if (p.parent / "session.json").is_file():
            s = Session.open(p.parent)
            if p.resolve() == s.work.resolve():
                return Source(s.read(), s.work, s.logical, None, s)
        logical, root = _logical_of(p, fs)
        return Source(p.read_bytes(), p, logical, root)
    hit = fs.resolve(str(path))
    if hit is None:
        raise FxError("not_found", "%s: not a file or session, and in none of the roots %s" %
                      (path, [n for n, _ in fs.roots] or "(no roots: pass roots or set HDKIT_VIEW_ROOTS)"))
    return Source(hit.path.read_bytes(), hit.path, hit.logical, hit.root)


def parse_file(data: bytes) -> hp.ParticleFile:
    try:
        return hp.ParticleFile(data)
    except (ValueError, struct.error, IndexError) as e:
        raise FxError("parse_error", "not a readable D2R particle file: %s" % e) from None


# ---------------------------------------------------------------------------------------------------- edit sessions
class Session:
    """A working copy of one effect with its history: <out>/session.json, <out>/<name>.particles, <out>/history/vNNNN."""

    def __init__(self, out: Path, meta: dict):
        self.out, self.meta = Path(out), meta

    # -- creation / opening
    @classmethod
    def create(cls, out, data: bytes, source: str, logical: str | None, name: str, label: str = "open") -> "Session":
        out = Path(out)
        (out / "history").mkdir(parents=True, exist_ok=True)
        s = cls(out, {"format": 1, "name": name, "source": source, "logical": logical, "cursor": 0, "versions": []})
        s._add(data, label, [])
        return s

    @classmethod
    def open(cls, out) -> "Session":
        f = Path(out) / "session.json"
        if not f.is_file():
            raise FxError("not_found", "%s is not an edit session folder (no session.json)" % out)
        return cls(Path(out), json.loads(f.read_text("utf-8")))

    # -- properties
    name = property(lambda s: s.meta["name"])
    source = property(lambda s: s.meta["source"])
    logical = property(lambda s: s.meta.get("logical"))
    cursor = property(lambda s: s.meta["cursor"])
    work = property(lambda s: s.out / ("%s.particles" % s.meta["name"]))

    def _hist(self, v: int) -> Path:
        return self.out / "history" / ("v%04d.particles" % v)

    def read(self) -> bytes:
        return self.work.read_bytes()

    def _save(self):
        tmp = self.out / "session.json.tmp"
        tmp.write_text(json.dumps(self.meta, indent=1), "utf-8")
        os.replace(tmp, self.out / "session.json")

    def _add(self, data: bytes, label: str, ops: list):
        vs = self.meta["versions"]
        for v in vs[self.cursor + 1:] if vs else []:      # a new edit drops the redo versions
            self._hist(v["version"]).unlink(missing_ok=True)
        del vs[(self.cursor + 1 if vs else 0):]
        n = len(vs)
        self._hist(n).write_bytes(data)
        vs.append({"version": n, "label": label, "ops": ops, "size": len(data)})
        self.meta["cursor"] = n
        self.work.write_bytes(data)
        self._save()

    def commit(self, data: bytes, label: str, ops: list | None = None) -> None:
        self._add(data, label, ops or [])

    def goto(self, v: int) -> None:
        if not 0 <= v < len(self.meta["versions"]):
            raise FxError("bad_args", "no version %s (0..%d)" % (v, len(self.meta["versions"]) - 1))
        self.meta["cursor"] = v
        self.work.write_bytes(self._hist(v).read_bytes())
        self._save()

    def info(self) -> dict:
        n = len(self.meta["versions"])
        return {"out": str(self.out), "work": str(self.work), "source": self.source, "logical": self.logical,
                "version": self.cursor, "versions": n, "can_undo": self.cursor > 0, "can_redo": self.cursor < n - 1}

    def history(self) -> list:
        return [{**v, "current": v["version"] == self.cursor} for v in self.meta["versions"]]


def session_for(path, out=None, roots=None) -> tuple[Session, Source]:
    """The edit session of an effect: the one `path` names, the one in `out`, or a new one (default folder by the
    source path, so the same effect always lands in the same working copy)."""
    src = open_source(path, roots)
    if src.session:
        return src.session, src
    o = Path(out) if out else out_root() / "edit" / ("%s_%s" % (src.name, _hash(src.key)))
    if (o / "session.json").is_file():
        s = Session.open(o)
        if s.source != src.key:
            raise FxError("bad_args", "%s holds the session of %s, not %s: pass another out" % (o, s.source, src.key))
        return s, src
    s = Session.create(o, src.data, src.key, src.logical, src.name)
    return s, src


# ---------------------------------------------------------------------------------------------------- inspect
def _prop_json(pf: hp.ParticleFile, p: hp.Prop) -> dict:
    d = {"type": TYPE_NAMES.get(p.type, "?")}
    if p.value is not None:
        if p.type == hp.T_FLOAT:
            d["value"] = struct.unpack_from("<f", p.value)[0]
        elif p.type == hp.T_INT:
            d["value"] = struct.unpack_from("<i", p.value)[0]
        elif p.type in (hp.T_BOOL, None):
            d["value"] = p.value == hp.ON
        else:
            d["value"] = p.value.hex()
    if p.string is not None:
        d["file"] = pf.prop_string(p)
    return d


def renderers_json(pf: hp.ParticleFile) -> list:
    out = []
    for k, r in enumerate(pf.renderers()):
        files = sorted({pf.prop_string(p) for p in r.props.values() if p.string is not None} - {None})
        out.append({"index": k, "object": r.obj, "kind": r.kind, "material": r.material, "enabled": r.enabled,
                    "props": {n: _prop_json(pf, p) for n, p in r.props.items()}, "files": files})
    return out


def scripts_json(pf: hp.ParticleFile) -> list:
    out = []
    for s in cb.scripts(pf):
        out.append({"script": s.obj + 1, "backend": s.backend, "kind": s.kind, "instructions": len(s.code),
                    "constants": len(s.consts), "externals": [n for n in s.ext_names if n],
                    "engine_calls": list(s.fn_names or [])})
    return out


def fx_inspect(path: str, roots: list[str] | None = None, graph: bool = True) -> dict:
    """What an effect is made of: renderers (kind, material, switches, files), scripts, object classes, textures, and the
    effect graph when lane format has landed."""
    src = open_source(path, roots)
    pf = parse_file(src.data)
    classes: dict = {}
    for i in range(len(pf.objects)):
        classes[pf.cls(i)] = classes.get(pf.cls(i), 0) + 1
    g = None
    gm = lane("graph") if graph else None
    if gm is not None:
        g = gm.load(src.data, vfs=make_fs(roots)).to_json()
    return {"ok": True, "path": src.logical, "file": str(src.file), "root": src.root, "size": len(src.data),
            "version": "%#010x" % struct.unpack_from("<I", src.data, 4)[0], "objects": len(pf.objects),
            "strings": len(pf.strings), "classes": classes, "renderers": renderers_json(pf), "scripts": scripts_json(pf),
            "textures": pf.textures(), "graph": g, "engine": engine(),
            "colour_streams": cb.colour_streams(src.data),      # the stream names a {"op": "color"} edit can tint
            "session": src.session.info() if src.session else None}


# ---------------------------------------------------------------------------------------------------- list / search
_PARSED: dict = {}


def _parsed(file: Path) -> hp.ParticleFile | None:
    key = (str(file), file.stat().st_mtime_ns)
    if key not in _PARSED:
        try:
            _PARSED[key] = hp.ParticleFile(file.read_bytes())
        except (ValueError, struct.error, IndexError, OSError):
            _PARSED[key] = None
    return _PARSED[key]


def _match(pattern: str | None, logical: str) -> bool:
    if not pattern:
        return True
    pat = vfs_mod.normalise(pattern) if "/" in pattern else pattern.lower()
    return fnmatch.fnmatch(logical, "*%s*" % pat) if any(c in pat for c in "*?[") else pat in logical


def fx_list(pattern: str | None = None, roots: list[str] | None = None, texture: str | None = None,
            material: str | None = None, kind: str | None = None, limit: int = 200, info: bool = False) -> dict:
    """List `.particles` files of the roots (first root wins per path). pattern: substring or glob on the logical path;
    texture / material / kind: look into the files; info: renderer kinds and materials per hit."""
    fs = make_fs(roots)
    if not fs.roots:
        raise FxError("bad_args", "no roots: pass roots=[\"name=folder\"] or set HDKIT_VIEW_ROOTS")
    items, total = [], 0
    deep = bool(texture or material or kind or info)
    for h in fs.walk("", ".particles"):
        if not _match(pattern, h.logical):
            continue
        row = {"path": h.logical, "root": h.root, "file": str(h.path), "size": h.path.stat().st_size}
        if deep:
            pf = _parsed(h.path)
            if pf is None:
                continue
            rs = pf.renderers()
            kinds: dict = {}
            for r in rs:
                kinds[r.kind] = kinds.get(r.kind, 0) + 1
            mats = sorted({r.material for r in rs if r.material})
            files = [s.lower() for s in pf.strings]
            if (kind and kind not in kinds) or (material and not any(material.lower() in m.lower() for m in mats)) \
                    or (texture and not any(texture.lower() in s for s in files)):
                continue
            if info:
                row.update(kinds=kinds, materials=mats)
        total += 1
        if len(items) < limit:
            items.append(row)
    return {"ok": True, "count": total, "truncated": total > len(items), "items": items}


# ---------------------------------------------------------------------------------------------------- edit
def select(pf: hp.ParticleFile, sel) -> list:
    if isinstance(sel, str) and sel.startswith("layer:"):
        need("graph", "a layer selector")
        raise FxError("not_implemented", "layer selectors are resolved by graph.apply_edits", needs=NEEDS["graph"])
    if isinstance(sel, int) and not isinstance(sel, bool):
        sel = [sel]
    try:
        rs = hp.select(pf, "*" if sel is None else sel)
    except IndexError:
        raise FxError("bad_args", "renderer %s: the effect has %d renderers" % (sel, len(pf.renderers()))) from None
    if not rs:
        raise FxError("bad_args", "renderer selector %r matches no renderer" % (sel,))
    return rs


def _local_op(data: bytes, op: dict) -> tuple[bytes, int, str]:
    """One edit op on `data` without lane format: (new bytes, things changed, note)."""
    kind = op.get("op")
    if kind in ("curve", "copy_layer", "remove_layer"):
        need("graph", "op %s" % kind)
        raise FxError("not_implemented", "op %s is not in hdkit.fx.graph.OPS" % kind, needs=NEEDS["graph"])
    if kind not in OPS_ALL:
        raise FxError("bad_args", "unknown op %r (one of %s)" % (kind, ", ".join(OPS_ALL)))
    if kind == "color":
        rgba = op.get("rgba")
        if not op.get("stream") or not isinstance(rgba, list) or not 1 <= len(rgba) <= 4:
            raise FxError("bad_args", "color needs stream and rgba (1-4 numbers)")
        out, n = cb.tint(data, op["stream"], [float(x) for x in rgba])
        return out, n, "stores of %s tinted" % op["stream"]
    pf = parse_file(data)
    if kind in ("prop", "texture", "enable"):
        if kind == "texture" and "replace" in op:
            n = 0
            for old, new in op["replace"].items():
                c = pf.replace_string(old, new)
                if not c:
                    raise FxError("bad_args", "the file names no %s" % old)
                n += c
            return pf.data(), n, "strings replaced"
        rs, n, skipped = select(pf, op.get("renderer", "*")), 0, 0
        name = op.get("name")
        for r in rs:
            if kind == "enable":
                pf.set_enabled(r, bool(op["on"]))
            elif name not in r.props:
                continue
            elif kind == "prop":
                if r.props[name].value_at is None:      # takes the material's default: nothing stored to change
                    skipped += 1
                    continue
                pf.set_property(r, name, op["value"])
            else:
                pf.set_texture(r, name, op["path"])
            n += 1
        if not n:
            raise FxError("bad_args", "no selected renderer has a stored value for %r (%d take the material's default)"
                          % (name, skipped) if skipped else "no selected renderer has the property %r" % name)
        return pf.data(), n, "renderers changed" + (", %d skipped (no stored value)" % skipped if skipped else "")
    ss = {s.obj + 1: s for s in cb.scripts(pf)}
    s = ss.get(op.get("script"))
    if s is None:
        raise FxError("bad_args", "script %r: not a script object (see fx_script_read)" % op.get("script"))
    if kind == "const":
        vals, i = op.get("values"), op.get("index")
        if not isinstance(vals, list) or not 1 <= len(vals) <= 8 or not isinstance(i, int) or not 0 <= i < len(s.consts):
            raise FxError("bad_args", "const: index 0..%d and 1-8 values" % (len(s.consts) - 1))
        isint = op.get("kind") == "i"
        s.consts[i] = struct.pack("<8i" if isint else "<8f", *(((list(map(int if isint else float, vals))) * 8)[:8]))
        return cb.write_scripts(pf, [s]), 1, "constant %d of script %d" % (i, s.obj + 1)
    new = compile_script(op.get("text", ""), s.obj, op.get("language", "auto"), data)       # kind == "script"
    return cb.write_scripts(pf, [new]), 1, "script %d replaced (%d instructions)" % (s.obj + 1, len(new.code))


def apply_ops(data: bytes, ops: list, roots: list[str] | None = None) -> tuple[bytes, list]:
    """All ops in order, on a copy. Ops in `graph.OPS` (lane format) go through `graph.apply_edits`, the rest run here.
    A copy_layer's `from` (logical path, file or session) is resolved over `roots` first."""
    if not isinstance(ops, list) or not ops:
        raise FxError("bad_args", "ops: a non-empty list of {\"op\": ...} dicts")
    gm = lane("graph")
    graph_ops = set(getattr(gm, "OPS", ())) if gm is not None else set()
    applied = []
    for i, op in enumerate(ops):
        try:
            if not isinstance(op, dict):
                raise FxError("bad_args", "an op is a dict")
            if op.get("op") == "copy_layer" and op.get("from"):
                op = dict(op, **{"from": str(open_source(op["from"], roots).file)})
            if op.get("op") in graph_ops:
                data, n, note = gm.apply_edits(data, [op]), 1, "by hdkit.fx.graph"
            else:
                data, n, note = _local_op(data, op)
            applied.append({"op": op["op"], "changed": n, "note": note})
        except FxError as e:
            raise FxError(e.code if e.code != "bad_args" else "edit_failed", "op %d: %s" % (i, e.message), op_index=i,
                          **e.extra) from None
        except (ValueError, KeyError, IndexError, TypeError, struct.error) as e:
            raise FxError("edit_failed", "op %d (%s): %s" % (i, op.get("op") if isinstance(op, dict) else op, e),
                          op_index=i) from None
    parse_file(data)    # whatever the ops did, the result must still read
    return data, applied


def fx_edit(path: str, ops: list[dict], out: str | None = None, roots: list[str] | None = None,
            validate: bool = True) -> dict:
    """Apply edit ops to the effect's working copy (made on first use); all-or-nothing; undo with fx_session."""
    s, src = session_for(path, out, roots)
    data, applied = apply_ops(s.read(), ops, roots)
    s.commit(data, "edit: " + ", ".join(a["op"] for a in applied), ops)
    return {"ok": True, "session": s.info(), "applied": applied,
            "validate": fx_validate(str(s.out), roots) if validate else None}


def fx_session(out: str, action: str = "status", version: int | None = None) -> dict:
    """The edit session in `out` (or of a working file or source path): status, history, undo, redo, reset, goto."""
    p = Path(out)
    s = Session.open(p) if (p / "session.json").is_file() else Session.open(p.parent if p.is_file() else p)
    if action == "undo":
        s.goto(s.cursor - 1) if s.cursor > 0 else _refuse("nothing to undo")
    elif action == "redo":
        s.goto(s.cursor + 1) if s.info()["can_redo"] else _refuse("nothing to redo")
    elif action == "reset":
        s.goto(0)
    elif action == "goto":
        s.goto(-1 if version is None else int(version))
    elif action not in ("status", "history"):
        raise FxError("bad_args", "action: status, history, undo, redo, reset, goto")
    return {"ok": True, "session": s.info(), "history": s.history() if action == "history" else None}


def _refuse(msg):
    raise FxError("bad_args", msg)


# ---------------------------------------------------------------------------------------------------- validate
def fx_validate(path: str, roots: list[str] | None = None, files: bool = True) -> dict:
    """Is the file sound: parse, byte-exact round trip, scripts, renderers, referenced files, graph."""
    src = open_source(path, roots)
    errors, warnings, checks = [], [], {}

    def err(check, msg, where=""):
        errors.append({"check": check, "message": msg, "where": where})
        checks[check] = False

    pf = None
    try:
        pf = hp.ParticleFile(src.data)
        checks["parse"] = True
    except (ValueError, struct.error, IndexError) as e:
        err("parse", str(e))
    if pf is not None:
        checks["roundtrip"] = pf.data() == src.data or err("roundtrip", "decode + encode differs from the file") or False
        try:
            ss = cb.scripts(pf)
            n, same = cb.roundtrip(src.data)
            checks["scripts"] = True
            if not same:
                err("scripts", "script listings do not assemble back to the same bytes")
            for s in ss:
                for i in s.code:
                    if i.op in (0x69, 0x6A, 0x6B) and not 0 <= i.imm[0] < len(s.ext_names):
                        err("scripts", "external %d is out of range" % i.imm[0], "script %d" % (s.obj + 1))
                        break
        except Exception as e:  # noqa: BLE001  (a decoder failure is the finding)
            err("scripts", "%s: %s" % (type(e).__name__, e))
        rs = pf.renderers()
        checks["renderers"] = True
        if rs and not any(r.enabled for r in rs):
            warnings.append({"check": "renderers", "message": "every renderer is switched off", "where": ""})
        for k, r in enumerate(rs):
            for n, p in r.props.items():
                if p.type in (hp.T_TEXTURE, hp.T_MESH) and p.string is None:
                    warnings.append({"check": "renderers", "message": "%s names no file (empty slot)" % n,
                                     "where": "renderer %d" % k})   # D2R's own files leave slots empty
        if files:
            fs = make_fs(roots)
            if fs.roots:
                checks["files"] = True
                for f in pf.textures():
                    if not fs.exists(f):
                        warnings.append({"check": "files", "message": "not found in the roots: " + f, "where": ""})
            else:
                checks["files"] = None
        gm = lane("graph")
        if gm is not None:
            try:
                eff = gm.load(src.data, vfs=make_fs(roots) if files else None)
                checks["graph"] = True
                for m in (eff.validate() if hasattr(eff, "validate") else []):
                    err("graph", str(m))
            except Exception as e:  # noqa: BLE001
                err("graph", "%s: %s" % (type(e).__name__, e))
        else:
            checks["graph"] = None
    return {"ok": not errors, "errors": errors, "warnings": warnings, "checks": checks}


# ---------------------------------------------------------------------------------------------------- create
def fx_create(template: str | None = None, layers: list[dict] | None = None, name: str | None = None,
              textures: dict | None = None, logical: str | None = None, out: str | None = None,
              roots: list[str] | None = None) -> dict:
    """A new effect as an edit session: copied from a template (with texture renames), plus layers copied in from other
    effects (template + layers composes them onto the template; layers alone use the first layer's file as the base)."""
    if not template and not layers:
        need("graph", "an empty effect")
        raise FxError("not_implemented", "an empty effect scaffold is not in hdkit.fx.graph yet", needs=NEEDS["graph"])
    src = open_source(template or layers[0]["from"], roots)
    data = src.data
    if textures:
        data, _ = apply_ops(data, [{"op": "texture", "replace": textures}])
    nm = _stem(name or src.name)
    key = ("create", nm, template, layers, textures)
    o = Path(out) if out else out_root() / "edit" / ("%s_%s" % (nm, _hash(*key)))
    if (o / "session.json").is_file():
        raise FxError("bad_args", "%s already holds a session: pass another out or name" % o)
    lg = _logical_ok(logical or "hd/vfx/particles/custom/%s/%s.particles" % (nm, nm))   # lower case, as the game spells it
    s = Session.create(o, data, "new:%s" % (template or "layers"), lg, nm, "create")
    names = None
    if layers:
        ops = [{"op": "copy_layer", "from": l["from"], "layer": l["layer"], "as": l.get("as", l["layer"])} for l in layers]
        d2, _ = apply_ops(data, ops, roots)
        s.commit(d2, "compose", ops)
        names = [l.get("as", l["layer"]) for l in layers]
    return {"ok": True, "session": s.info(), "logical": lg, "layers": names}


# ---------------------------------------------------------------------------------------------------- scripts
def detect_language(text: str) -> str:
    """`cbvm` when the text has a `.backend` directive (a listing of fx_script_read) or lane lang is absent, else `hfx`."""
    return "cbvm" if re.search(r"^\s*\.backend\b", text, re.M) or lane("lang") is None else "hfx"


def compile_script(text: str, obj0: int, language: str = "auto", data: bytes | None = None) -> cb.Script:
    """A CBVM listing or `.hfx` source -> a hd_cbvm.Script for object `obj0` (0-based)."""
    if language not in ("auto", "cbvm", "hfx"):
        raise FxError("bad_args", "language: auto, cbvm or hfx")
    if not isinstance(text, str) or not text.strip():
        raise FxError("bad_args", "the script text is empty")
    language = detect_language(text) if language == "auto" else language
    if language == "hfx":
        lm = need("lang", "the .hfx language")
        eff = lane("graph").load(data) if data and lane("graph") is not None else None
        base = None
        if data:                         # the script being replaced keeps its externals, kind and order
            try:
                base = next((x for x in cb.scripts(hp.ParticleFile(data)) if x.obj == obj0), None)
            except Exception:  # noqa: BLE001  (no base: the source alone decides)
                base = None
        try:
            s = lm.compile(text, effect=eff, base=base)
        except ValueError as e:          # lang.LangError: a syntax or type error, with its line
            raise FxError("parse_error", "the .hfx source does not compile: %s" % e) from None
        s.obj = obj0
        return s
    try:
        s = cb.script_parse(text, obj0)
        cb.decode(cb.encode(s.code))
    except (ValueError, KeyError, IndexError, struct.error) as e:
        raise FxError("parse_error", "the CBVM listing does not assemble: %s" % e) from None
    if not s.backend or not s.order:
        raise FxError("parse_error", "the CBVM listing lacks its .backend / .order directives (start from fx_script_read)")
    return s


def fx_script_read(path: str, script: int | None = None, language: str = "cbvm",
                   roots: list[str] | None = None) -> dict:
    """The scripts of an effect, or one script as a CBVM listing (`cbvm`) or readable `.hfx` source (`hfx`, lane lang)."""
    src = open_source(path, roots)
    pf = parse_file(src.data)
    if script is None:
        return {"ok": True, "scripts": scripts_json(pf)}
    if language not in ("cbvm", "hfx"):
        raise FxError("bad_args", "language: cbvm or hfx")
    ss = {s.obj + 1: s for s in cb.scripts(pf)}
    s = ss.get(script)
    if s is None:
        raise FxError("not_found", "script %r: objects that are scripts: %s" % (script, sorted(ss)[:40]))
    text = cb.script_text(s) if language == "cbvm" else need("lang", "the .hfx decompiler").decompile(s)
    return {"ok": True, "script": script, "language": language, "text": text, "instructions": len(s.code),
            "externals": [n for n in s.ext_names if n]}


def fx_script_write(path: str, script: int, source: str, language: str = "auto", out: str | None = None,
                    roots: list[str] | None = None) -> dict:
    """Replace one script of the working copy by a CBVM listing or `.hfx` source; nothing is saved when it fails to parse."""
    s, _src = session_for(path, out, roots)
    r = fx_edit(str(s.out), [{"op": "script", "script": script, "text": source, "language": language}], roots=roots)
    new = {x.obj + 1: x for x in cb.scripts(parse_file(s.read()))}[script]
    return {"ok": True, "session": r["session"], "script": script, "instructions": len(new.code),
            "language": detect_language(source) if language == "auto" else language, "validate": r["validate"]}


# ---------------------------------------------------------------------------------------------------- render
def gate(wait_s: float = 0.0) -> dict | None:
    """None when a GPU job may run; else the polite refusal (hdkit.gpugate)."""
    from hdkit import gpugate
    rd = gpugate.check() if wait_s <= 0 else gpugate.wait_free(every=min(15.0, wait_s), most=wait_s,
                                                                 log=lambda *a: print(*a, file=sys.stderr))
    if rd.free:
        return None
    return {"ok": False, "refused": True, "why": rd.why, "gate": rd.as_dict(),
            "advice": "GPU jobs wait while a game-slot run holds its lock or the GPU/CPU is busy; ask again later "
                      "(or pass wait_s)"}


def gpu_renderer():
    if GPU_PROVIDER is not None:
        return GPU_PROVIDER()
    if "r" not in _GPU:
        from hdkit.view import gpu
        _GPU["r"] = gpu.Renderer(msaa=4, aniso=16)
    return _GPU["r"]


def parse_size(size: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d{2,5})\s*[xX]\s*(\d{2,5})\s*", str(size))
    if not m or not (16 <= int(m[1]) <= 4096 and 16 <= int(m[2]) <= 4096):
        raise FxError("bad_args", "size: WxH, each 16..4096 (got %r)" % (size,))
    return int(m[1]), int(m[2])


def parse_colour(bg: str):
    if bg in (None, "", "transparent"):
        return None
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", bg)
    if not m:
        raise FxError("bad_args", "bg: transparent or #rrggbb")
    return tuple(int(m[1][i:i + 2], 16) for i in (0, 2, 4))


def _times(t: float, clip: dict | None) -> tuple[list, float]:
    if not clip:
        return [float(t)], 25.0
    t0, t1, fps = float(clip.get("t0", 0.0)), float(clip.get("t1", 3.0)), float(clip.get("fps", 25.0))
    if not (0 <= t0 < t1 and 0 < fps <= 120 and (t1 - t0) * fps <= 1500):
        raise FxError("bad_args", "clip: 0 <= t0 < t1, fps 1..120, at most 1500 frames")
    n = max(1, int(round((t1 - t0) * fps)))
    return [t0 + k / fps for k in range(n)], fps


class _SimProxy:
    """What `context.drive` plays: a Sim that also remembers its effect (so the states can be drawn)."""

    def __init__(self, sim, effect):
        self.sim, self.effect = sim, effect

    ctx = property(lambda s: s.sim.ctx, lambda s, v: setattr(s.sim, "ctx", v))

    def step(self, dt):
        self.sim.step(dt)

    def state(self):
        return self.effect, self.sim.state()


def _unit(fs, spec: str):
    from hdkit.view import scene
    lg = need("context", "a unit context").unit_path(fs, spec)
    if lg is None:
        raise FxError("not_found", "unit %s: no unit JSON in the roots" % spec)
    return scene.load_unit(fs, lg)


def unit_bone_driver(fs, spec: str, effect_path: str, clip: str | None = None, t0: float = 0.0, rate: float = 0.0,
                     yaw: float = 0.0, skill: str | None = None):
    """(driver, unit, bindings) of the effect on every bone `spec` binds it to (lane r3-shapes' `view/fxbones.py`, ported
    from the fx CLI at the r3 integration): one BoneDriver holding a layer per binding, all on the unit's `clip` at clip
    time t0 + t x rate (rate 0: the pose the unit is drawn in). Raises FxError when the unit does not bind the effect."""
    from hdkit.view import fxbones, scene
    cm = need("context", "a unit context")
    lg = cm.unit_path(fs, spec)
    if lg is None:
        raise FxError("not_found", "unit %s: no unit JSON in the roots" % spec)
    unit = scene.load_unit(fs, lg)
    clip = clip or ("neutral" if "neutral" in getattr(unit, "clips", {}) else None)
    drv = fxbones.bone_drivers(fs, lg, unit, effect_path, unit.clips.get(clip) if clip else None, float(t0),
                               float(rate), float(yaw), None, {"loop": True}, None, skill)
    if not drv:
        raise FxError("bad_args", "unit %s does not bind %s to a bone (use kind bone with an explicit bone)"
                      % (spec, effect_path))
    first = drv[0][1]
    layers = [lay for _nm, d in drv for lay in d.layers]
    one = fxbones.BoneDriver(layers, first.poses, float(getattr(unit, "root_scale", 1.0) or 1.0), None, float(yaw),
                             name="unit")
    one.skipped = [b for _nm, d in drv for b in d.skipped]
    return one, unit, [{"label": nm, "bone": d.layers[0].bone, "skipped": d.skipped} for nm, d in drv]


def build_driver(context: dict, effect, fs, duration: float, effect_path: str | None = None):
    """The context lane's driver for fx_render's `context` dict: (driver, unit or None, SceneContext or None)."""
    cm = need("context", "context %s" % context.get("kind"))
    k, unit, sc = context.get("kind"), None, None
    try:
        if k == "unit":                 # every bone the unit binds the effect to (fxbones, as the fx CLI's --bind bone)
            drv, unit, _b = unit_bone_driver(fs, context["unit"], effect_path or "", context.get("clip"),
                                             float(context.get("t0", 0.0)), float(context.get("rate", 0.0)),
                                             float(context.get("yaw", 0.0)), context.get("skill"))
            drv.duration = duration
        elif k == "bone":
            unit = _unit(fs, context["unit"])
            drv = cm.attach(effect, unit, context["bone"], yaw=float(context.get("yaw", 0.0)), duration=duration)
        elif k == "missile":
            path = context.get("path") or [context.get("start", [0, 0, 0]), context.get("end", [0, 0, 20])]
            row = None
            if context.get("name"):
                row = cm.effects_of_missile(fs, context["name"], cm.Tables.from_dir(context.get("tables") or "."))
            drv = cm.missile(effect, path=path, speed=context.get("speed"), row=row, events=context.get("events") or {},
                             arc=float(context.get("arc", 0.0)))
        elif k == "skill":
            unit = _unit(fs, context["unit"])
            if context.get("name"):
                fx = cm.effects_of_skill(fs, context["name"], cm.Tables.from_dir(context.get("tables") or "."))
                mode = context.get("mode") or fx.mode
            else:       # the effect itself plays as the skill's overlay on the unit, or as the missile it throws
                fx, mode = [(context.get("as", "overlay"), effect)], context.get("mode", "sc")
            drv = cm.skill(fx, unit, float(context.get("cast_frame", 0)), mode, target=context.get("target"),
                           bone=context.get("bone"))
        elif k == "scene":
            sc = cm.scene(context["map"], context["xy"], fs=fs)
            drv = sc.place(effect, context.get("offset", (0.0, 0.0, 0.0)), float(context.get("yaw", 0.0)), duration=duration)
        else:
            raise FxError("bad_args", "context.kind: bone, unit, missile, skill or scene")
    except KeyError as e:
        raise FxError("bad_args", "context %s needs %s" % (k, e)) from None
    except (ValueError, FileNotFoundError) as e:
        raise FxError("bad_args", "context %s: %s" % (k, e)) from None
    return drv, unit, sc


def play(effect, gm, sm, cm, fs, context, seed, attrs, fps: float, duration: float, effect_path: str | None = None):
    """Frames of the effect as `(t, [(effect, SimState), ...])`: alone (seek/step the Sim) or driven by a context (bone,
    missile, skill, scene: `context.drive` plays the driver and the children it spawns). Also returns (unit, scene)."""
    cache = {id(effect): effect}

    def make_sim(eff, ctx, sd):
        if isinstance(eff, (str, os.PathLike)):
            if str(eff) not in cache:
                raw = fs.read(str(eff))
                cache[str(eff)] = gm.load(raw, vfs=fs)
                cache[str(eff)].data = raw          # the Sim runs the file's scripts (lane vm)
            eff = cache[str(eff)]
        return _SimProxy(sm.Sim(eff, seed=sd, attrs=attrs, ctx=ctx), eff)

    if not context:
        def alone():
            px = make_sim(effect, None, seed)
            px.sim.seek(0.0)
            for k in range(int(round(duration * fps)) + 1):
                if k:
                    px.step(1.0 / fps)
                yield k / fps, [("effect", px.state())]
        return alone(), None, None
    drv, unit, sc = build_driver(context, effect, fs, duration, effect_path)
    return need("context", "context").drive(drv, make_sim, fps, duration, seed), unit, sc


def write_frames(frames: list, out: Path, name: str, fmt: str, fps: float, bg) -> list:
    """RGBA uint8 frames -> files: png (one still, or numbered PNGs in a folder for a clip), gif, apng, mp4."""
    import numpy as np
    from PIL import Image
    from hdkit.view import sheets
    frames = [np.asarray(f) for f in frames]
    out.mkdir(parents=True, exist_ok=True)
    if fmt == "png":
        if len(frames) == 1:
            f = frames[0]
            Image.fromarray(sheets.over(f, bg) if bg is not None and f.shape[-1] == 4 else f).save(out / ("%s.png" % name))
            return [str(out / ("%s.png" % name))]
        d = out / name
        d.mkdir(exist_ok=True)
        paths = []
        for k, f in enumerate(frames):
            p = d / ("%s_%04d.png" % (name, k))
            Image.fromarray(sheets.over(f, bg) if bg is not None and f.shape[-1] == 4 else f).save(p)
            paths.append(str(p))
        return paths
    ext = {"gif": ".gif", "apng": ".apng", "mp4": ".mp4"}.get(fmt)
    if ext is None:
        raise FxError("bad_args", "format: png, gif, apng or mp4")
    p = out / (name + ext)
    try:
        sheets.write_anim(frames, p, fps, bg if bg is not None else sheets.BG)
    except RuntimeError as e:
        raise FxError("bad_args", str(e)) from None
    return [str(p)]


def fx_render(path: str, t: float = 1.0, clip: dict | None = None, cam: dict | None = None, context: dict | None = None,
              size: str = "640x480", format: str = "png", seed: int = 0, attrs: dict | None = None,
              bg: str = "#101014", out: str | None = None, roots: list[str] | None = None, wait_s: float = 0.0) -> dict:
    """Draw an effect: a still at `t`, or a clip (`{"t0", "t1", "fps"}`) as numbered PNGs, GIF, APNG or MP4; alone or in a
    context (a unit's bone, a missile, a skill cast, a map scene). GPU: passes hdkit.gpugate first."""
    w, h = parse_size(size)
    bgc = parse_colour(bg)
    times, fps = _times(t, clip)
    if format not in ("png", "gif", "apng", "mp4"):
        raise FxError("bad_args", "format: png, gif, apng or mp4")
    if context is not None and (not isinstance(context, dict) or context.get("kind") not in CONTEXT_KINDS):
        raise FxError("bad_args", "context.kind: %s" % ", ".join(CONTEXT_KINDS))
    src = open_source(path, roots)
    attrs = color_variant_attrs(attrs)
    gm, sm, rm = need("graph", "rendering"), need("sim", "rendering"), need("render", "rendering")
    cm = need("context", "context %s" % context["kind"]) if context else lane("context")
    refusal = gate(wait_s)
    if refusal:
        return refusal
    fs = make_fs(roots)
    effect = gm.load(src.data, vfs=fs)
    effect.data = src.data                      # the Sim runs the file's scripts (lane vm)
    stream, unit, sc = play(effect, gm, sm, cm, fs, context, seed, attrs, fps, times[-1] + 1e-6,
                            src.logical or str(src.file))
    fxr = rm.FxRenderer(gpu_renderer(), fs)
    kw = {}
    if sc is not None:                                  # a map spot's game light, bloom and tonemap
        kw = {"light": sc.light.light, "post": sc.light.post.as_kwargs()}
    bkw = {}
    if context and context.get("kind") == "unit":       # the body in the pose its bones carry the effect in (rate 0)
        bkw = {"clip": context.get("clip"), "t": float(context.get("t0", 0.0)), "yaw": float(context.get("yaw", 0.0))}
    base = rm.base_scene(gpu_renderer(), fs, unit=unit, scene=sc, **bkw) if hasattr(rm, "base_scene") and (unit or sc) \
        else None
    if base is not None:
        kw["base"] = base
    want, frames, camera, stats = list(times), [], None, {}
    try:
        for tt, layers in stream:
            if not want or tt < want[0] - 1e-6:        # the first simulation frame at or after each wanted time
                continue
            want.pop(0)
            camera = camera or rm.camera_from_spec(cam or {}, (w, h), layers[0][1][1])    # one camera for the whole clip
            draws, snd = [], []
            for _label, (eff, st) in layers:
                draws += fxr.prepare(eff, st, camera)
                snd += getattr(fxr, "sounds", None) or []
            frames.append(fxr.render(draws, camera, (w, h), **kw))
            if snd:                                     # sound renderers: where PopcornFX would play (D2R: silent)
                from . import gamekinds
                stats.setdefault("sounds", []).append({"t": round(tt, 4), "sounds": gamekinds.sounds_summary(snd)})
            if not want:
                break
    except KeyError as e:
        if not context:
            raise
        raise FxError("bad_args", "context %s: %s" % (context["kind"], e)) from None
    if not frames:
        raise FxError("error", "the simulation produced no frame at t=%s" % times[0])
    o = Path(out) if out else out_dir("fx_render", src.name, src.key, t, clip, cam, context, size, format, seed, attrs)
    files = write_frames(frames, o, src.name, format, fps, bgc)
    return {"ok": True, "files": files, "format": format, "frames": len(frames), "size": [w, h], "t": times,
            "camera": cam or {"preset": "default"}, "context": context, "stats": stats}


# ---------------------------------------------------------------------------------------------------- export
def _under(p: Path, base: Path) -> bool:
    try:
        p.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _write(dest: Path, data: bytes) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    return {"file": str(dest), "size": len(data), "sha256": _sha(data)}


def _logical_ok(lg: str) -> str:
    lg = vfs_mod.normalise(lg)
    if not lg or ".." in lg.split("/") or ":" in lg or lg.startswith("/"):
        raise FxError("bad_args", "logical path %r is not a plain relative game path" % lg)
    return lg


def fx_export(path: str, mod_dir: str, logical: str | None = None, check: bool = True, extras: list[dict] | None = None,
              roots: list[str] | None = None) -> dict:
    """Write the effect to <mod_dir>/data/<logical> (mod_dir named `data` is used as is), plus `extras` ({src, logical})
    files; refused when mod_dir is one of the game roots; `check` reads it back and validates."""
    src = open_source(path, roots)
    lg = _logical_ok(logical or src.logical or "")
    if not (logical or src.logical):
        raise FxError("bad_args", "no logical path known for %s: pass logical" % path)
    md = Path(mod_dir)
    if not md.is_dir():
        raise FxError("not_found", "mod_dir %s is not a folder" % mod_dir)
    fs = make_fs(roots)
    for name, root in fs.roots:
        if not hasattr(root, "find") and (_under(md, Path(root)) or _under(Path(root), md)):
            raise FxError("bad_args", "mod_dir %s overlaps the game root %s: game files are never written" % (md, name))
    data_dir = md if md.name.lower() == "data" else md / "data"
    written = []
    for lgx, blob in [(lg, src.data)] + [(_logical_ok(e["logical"]), Path(e["src"]).read_bytes()) for e in extras or []]:
        dest = data_dir / lgx
        if not _under(dest, data_dir):
            raise FxError("bad_args", "%s leaves the mod folder" % lgx)
        written.append({"logical": lgx, **_write(dest, blob)})
    res = {"ok": True, "written": written, "missing": [], "check": None}
    if check:
        v = fx_validate(written[0]["file"], roots, files=False)
        pf = hp.ParticleFile(Path(written[0]["file"]).read_bytes())
        have = [f for f in pf.textures() if (data_dir / vfs_mod.normalise(f)).is_file() or fs.exists(f)]
        res["missing"] = [f for f in pf.textures() if f not in have]
        res["check"] = v
        res["ok"] = v["ok"]
    return res


# ---------------------------------------------------------------------------------------------------- coverage
def effect_needs(data: bytes) -> dict:
    pf = parse_file(data)
    classes: dict = {}
    for i in range(len(pf.objects)):
        classes[pf.cls(i)] = classes.get(pf.cls(i), 0) + 1
    rs = pf.renderers()
    kinds: dict = {}
    for r in rs:
        kinds[r.kind] = kinds.get(r.kind, 0) + 1
    ops: dict = {}
    ext, calls = set(), set()
    for s in cb.scripts(pf):
        ext.update(n for n in s.ext_names if n)
        calls.update(s.fn_names or [])
        for i in s.code:
            ops[i.name] = ops.get(i.name, 0) + 1
    return {"classes": classes, "kinds": kinds, "materials": sorted({r.material for r in rs if r.material}),
            "opcodes": ops, "externals": sorted(ext), "engine_calls": sorted(calls)}


def fx_coverage(pattern: str | None = None, roots: list[str] | None = None, limit: int = 500,
                summary: bool = False) -> dict:
    """What each effect needs (classes, renderers, materials, opcodes, externals, engine calls, samplers) and, from the
    survey lane (`hdkit.fx.coverage`), the verdict drawn / partly / not with what is `missing`."""
    fs = make_fs(roots)
    if not fs.roots:
        raise FxError("bad_args", "no roots: pass roots=[\"name=folder\"] or set HDKIT_VIEW_ROOTS")
    cm, fb = lane("coverage"), fired_index()
    effects, status, common = [], {}, {}
    for h in fs.walk("", ".particles"):
        if not _match(pattern, h.logical):
            continue
        if len(effects) >= limit:
            break
        try:
            row = {"path": h.logical, **effect_needs(h.path.read_bytes())}
            row["status"], row["missing"] = "unknown", []
            if cm is not None:
                e = cm.assess(cm.survey_file(h.path, h.logical), fb)
                row.update(status=e["status"], missing=e["missing"], samplers=e["samplers"], features=e["render"]["features"])
        except (FxError, ValueError) as e:
            row = {"path": h.logical, "status": "unknown", "missing": [], "error": str(getattr(e, "message", e))}
        if fb is not None:
            row["fired_by"] = fb.summary(h.logical)
        status[row["status"]] = status.get(row["status"], 0) + 1
        for key in ("opcodes", "engine_calls", "materials", "externals"):
            for n in row.get(key, []):
                common["%s:%s" % (key, n)] = common.get("%s:%s" % (key, n), 0) + 1
        effects.append(row)
    res = {"ok": True, "count": len(effects), "status": status, "engine": engine(),
           "common": dict(sorted(common.items(), key=lambda kv: -kv[1])[:60])}
    if not summary:
        res["effects"] = effects
    return res


# ---------------------------------------------------------------------------------------------------- fired by (lane r3-bind3)
_FIRED: dict = {}


def fired_index(index: str | None = None):
    """The reverse binding index (`hdkit.fx.firedby`, built by `python -m hdkit.fx.firedby build`): `index` or the
    environment's HDFX_FIRED_BY; None when neither names a file."""
    path = index or os.environ.get("HDFX_FIRED_BY")
    if not path or not Path(path).is_file():
        return None
    key = (str(path), Path(path).stat().st_mtime)
    if key not in _FIRED:
        from . import firedby
        _FIRED.clear()
        _FIRED[key] = firedby.FiredBy.load(path)
    return _FIRED[key]


def fx_fired_by(path: str, index: str | None = None) -> dict:
    """What fires an effect (a logical path, a file below a root, or a bare file name): status bound / exe / orphan /
    manifest / unreferenced, every definition that names it (trigger, entity, timeline mode, bone), the chain from each
    definition up to what the game loads (a skill, state, monster, object, level, item, global table or exe string) and,
    for unused effects, the evidence. index: the firedby.json (default: env HDFX_FIRED_BY)."""
    fb = fired_index(index)
    if fb is None:
        raise FxError("not_found", "no fired-by index: pass index=<firedby.json> or set HDFX_FIRED_BY "
                      "(build: python -m hdkit.fx.firedby build <data> --corpus <root> --out <dir>)")
    q = str(path).replace("\\", "/")
    m = re.search(r"(hd/vfx/.+\.particles)$", q, re.I)
    e = fb.of(m.group(1) if m else q)
    if e is None:
        raise FxError("not_found", "%s: not in the index (or the bare name is ambiguous)" % path)
    return {"ok": True, **e, "summary": fb.summary(e["path"])}


# ---------------------------------------------------------------------------------------------------- colour variants
def color_variant_attrs(attrs: dict | None) -> dict | None:
    """attrs with a ColorVariantIndex given as a name ("cold", "shift:104", "rand:3", "none") turned into its index."""
    from . import colorvariant as cv
    if not attrs or cv.ATTR not in attrs:
        return attrs
    v = attrs[cv.ATTR]
    v = v[0] if isinstance(v, (list, tuple)) and v else v
    try:
        return {**attrs, cv.ATTR: [cv.index_for(v), 0, 0, 0]}
    except ValueError as e:
        raise FxError("bad_args", str(e)) from None


def fx_color_variants(path: str | None = None, roots: list[str] | None = None) -> dict:
    """The game's colour variants (fx/colorvariant.py): every ColorVariantIndex with its table entry (from the roots'
    hd/global/palette/*.json; add a casc root), the states that pick one, and, for `path`, which renderers use them
    (their VariantContribution). Pass the choice to fx_render as attrs={"ColorVariantIndex": index or name}."""
    from . import colorvariant as cv
    fs = make_fs(roots)
    table = cv.Table.load(fs)
    res = {"ok": True, "attr": cv.ATTR, "table_found": table.loaded, "choices": table.choices(),
           "states": {k: cv.index_for(k) for k in cv.STATE_SHIFTS}, "renderers": []}
    if path:
        src = open_source(path, roots)
        rm = need("render", "colour variants")
        eff = rm.rspec.load(src.data)
        for r in eff.renderers:
            if r.enabled and r.props.get("ColorVariant"):
                res["renderers"].append({"index": r.index, "kind": r.kind, "layer": r.layer,
                                         "contribution": cv.contribution(r.props.get, True)})
    return res


# ---------------------------------------------------------------------------------------------------- dispatch
TOOLS = {f.__name__: f for f in (fx_list, fx_inspect, fx_edit, fx_session, fx_validate, fx_create, fx_script_read,
                                 fx_script_write, fx_render, fx_export, fx_coverage, fx_fired_by,
                                 fx_color_variants)}


def _jsonschema(t) -> dict:
    origin, args = typing.get_origin(t), typing.get_args(t)
    if origin in (typing.Union, getattr(__import__("types"), "UnionType", None)):
        subs = [_jsonschema(a) for a in args if a is not type(None)]
        return {"anyOf": subs + [{"type": "null"}]} if len(subs) > 1 else {**subs[0], "nullable": True}
    if origin is list:
        return {"type": "array", "items": _jsonschema(args[0]) if args else {}}
    if origin is dict or t is dict:
        return {"type": "object"}
    return {"type": {str: "string", int: "integer", float: "number", bool: "boolean"}.get(t, "string")}


def schema_of(fn) -> dict:
    """A JSON schema of a tool's inputs, from its signature (what GET /api/fx/tools serves)."""
    sig, props, req = inspect.signature(fn, eval_str=True), {}, []
    for n, p in sig.parameters.items():
        props[n] = _jsonschema(p.annotation) if p.annotation is not inspect.Parameter.empty else {}
        if p.default is inspect.Parameter.empty:
            req.append(n)
        else:
            props[n]["default"] = p.default
    return {"type": "object", "properties": props, "required": req}


def describe() -> dict:
    return {n: {"description": inspect.getdoc(f) or "", "input": schema_of(f)} for n, f in TOOLS.items()}


def call(name: str, /, **args) -> dict:
    """Run a tool; never raises: failures are {"ok": False, "error": {code, message, ...}}."""
    fn = TOOLS.get(name)
    if fn is None:
        return FxError("bad_args", "no tool %r (%s)" % (name, ", ".join(TOOLS))).as_dict()
    try:
        with LOCK:
            return fn(**args)
    except FxError as e:
        return e.as_dict()
    except TypeError as e:
        if re.search(r"unexpected keyword|required positional|missing \d+ required", str(e)):      # a wrong call, not a bug
            return FxError("bad_args", "%s: %s" % (name, e)).as_dict()
        return FxError("error", "TypeError: %s" % e).as_dict()
    except NotImplementedError as e:
        m = re.search(r"needs ([\w ]+)", str(e))
        return FxError("not_implemented", str(e), needs=m.group(1).strip() if m else None).as_dict()
    except FileNotFoundError as e:
        return FxError("not_found", str(e)).as_dict()
    except Exception as e:  # noqa: BLE001  (a tool never takes the server down)
        return FxError("error", "%s: %s" % (type(e).__name__, e)).as_dict()
