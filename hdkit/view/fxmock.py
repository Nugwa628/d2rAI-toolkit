"""A stand-in for the hdfx tools with the same JSON contract (docs/hdfx-mcp.md), for developing and testing the web editor
before every lane has landed, and for headless checks that must not need game files or a GPU.

  python -m hdkit.view.fxmock [--port 8766] [--out <folder>]      then open http://127.0.0.1:8766/fx.html

It serves hdkit/view/web/ and /api/fx/{tools,<tool>,file} on 127.0.0.1 like `serve-web --fx`, but the effects are three
built-in effect graphs (docs/hdfx.md section 1 JSON), edit sessions are folders of JSON (version history, undo/redo as in
`hdkit.fx.api.Session`), and `fx_render` draws a synthetic picture of the graph (not the game's look). The answers carry
`engine.mock = true` so the page shows a "mock API" badge. Nothing here reads game files.
"""
import argparse
import copy
import fnmatch
import hashlib
import http.server
import json
import re
import shutil
import sys
import threading
from pathlib import Path
from urllib.parse import unquote

import numpy as np

from . import webserve

LOGICAL = "hd/vfx/particles/mock/"
FX_TYPES = {".png": "image/png", ".gif": "image/gif", ".json": "application/json", ".particles": "application/octet-stream"}
FX_MAX_BODY = 8 << 20
CBVM_OPS = {"mov", "add", "sub", "mul", "div", "mad", "rand", "ret", "call", "load", "store", "max", "min", "lerp", "sin", "cos"}


class MockError(Exception):
    def __init__(self, code, message, **extra):
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra

    def as_dict(self):
        return {"ok": False, "error": {"code": self.code, "message": self.message, **self.extra}}


def _curve(times, values, dim=1, tin=None, tout=None, interp="hermite"):
    vals = [v if isinstance(v, list) else [v] for v in values]
    zero = [[0.0] * dim for _ in times]
    return {"times": list(times), "values": vals, "tangents_in": tin or [list(z) for z in zero],
            "tangents_out": tout or [list(z) for z in zero], "dim": dim, "interp": interp}


def _script(obj, text):
    return text.strip("\n") + "\n"


def _listing(obj, consts, code):
    head = ["; D2R CBVM script (mock): object %d" % obj, ".order 0 1 2", ".backend CPU/CPU_VM", ".kind 2"]
    head += [".const %d f %s" % (k, " ".join(repr(float(x)) for x in (c * 8)[:8])) for k, c in enumerate(consts)]
    return "\n".join(head + [""] + code) + "\n"


def _renderer(index, kind, material, props, enabled=True):
    return {"obj": 20 + index, "index": index, "kind": kind, "material": material, "enabled": enabled, "props": props,
            "inputs": {"Position": "Position", "Color": "Color", "Size": "Size"}}


def fixtures() -> dict:
    tex = lambda f: {"type": "texture", "file": "data/hd/vfx/textures/" + f}
    embers = {
        "path": LOGICAL + "vfx_mock_embers.particles", "version": "0x00010001", "objects": 64, "strings": 18,
        "layers": [
            {"obj": 3, "name": "Embers", "spawn_script": 11, "evolve_scripts": [12], "fields": [
                {"obj": 4, "name": "Position", "type": "float3", "size": 12}, {"obj": 5, "name": "Color", "type": "float4", "size": 16},
                {"obj": 6, "name": "Size", "type": "float", "size": 4}, {"obj": 7, "name": "Life", "type": "float", "size": 4}],
             "renderers": [_renderer(0, "billboard", "vfx/materials/Diablo_Uber.pkma", {
                 "EnableRendering": {"type": "bool", "value": True}, "Opacity": {"type": "float", "value": 0.85},
                 "SortOffset": {"type": "int", "value": 2}, "Color": {"type": "color", "value": [1.0, 0.55, 0.15, 1.0]},
                 "Offset": {"type": "vec3", "value": [0.0, 0.2, 0.0]}, "SoftDistance": {"type": "float", "value": 0.35},
                 "DiffuseMap": tex("fire/ember_a.texture"), "GradientMap": tex("gradient/fire_grad.texture"), "Packed": {"type": "?", "value": "0a0b0c0d"}})],
             "events": [], "children": ["Sparks"], "life": [1.2, 2.4], "spawn": {"rate": 40, "duration": 4.0},
             "colors": [{"index": 2, "width": 3, "value": [1.0, 1.25, 1.0]}, {"index": 4, "width": 4, "value": [1.0, 0.55, 0.15, 1.0]}]},
            {"obj": 8, "name": "Glow", "spawn_script": None, "evolve_scripts": [13], "fields": [], "renderers": [
                _renderer(1, "billboard", "vfx/materials/Diablo_Uber.pkma", {
                    "EnableRendering": {"type": "bool", "value": True}, "Opacity": {"type": "float", "value": 0.5},
                    "Color": {"type": "color", "value": [1.0, 0.3, 0.05, 0.6]}, "DiffuseMap": tex("glow/soft_round.texture")})],
             "events": [], "children": [], "life": 4.0, "spawn": {"count": 1, "duration": 4.0},
             "colors": [{"index": 2, "width": 4, "value": [1.0, 0.3, 0.05, 0.6]}]},
            {"obj": 9, "name": "Sparks", "spawn_script": None, "evolve_scripts": [], "fields": [], "renderers": [
                _renderer(2, "ribbon", "vfx/materials/Diablo_Uber.pkma", {
                    "EnableRendering": {"type": "bool", "value": True}, "Width": {"type": "float", "value": 0.04},
                    "Color": {"type": "color", "value": [1.0, 0.9, 0.5, 1.0]}, "DiffuseMap": tex("trail/streak.texture")})],
             "events": [], "children": [], "life": [0.4, 0.9], "spawn": {"rate": 12, "duration": 4.0}, "colors": []}],
        "attributes": [{"obj": 40, "name": "MonsterFade", "type": "float", "default": 1.0, "min": 0.0, "max": 1.0}],
        "samplers": [
            {"obj": 50, "name": "SizeOverLife", "layer": "Embers", "kind": "curve", "data": _curve([0, 0.2, 0.7, 1], [0.1, 1.0, 0.8, 0.0],
                                                                                tin=[[0], [1.5], [-0.3], [-1.2]], tout=[[2.5], [0.4], [-0.5], [-1.2]])},
            {"obj": 51, "name": "ColorOverLife", "layer": "Embers", "kind": "curve", "data": _curve(
                [0, 0.35, 1], [[1.0, 0.9, 0.5], [1.0, 0.45, 0.1], [0.3, 0.05, 0.02]], dim=3,
                tin=[[0, 0, 0], [-0.4, -1.1, -0.5], [-0.8, -0.2, -0.05]], tout=[[0, -0.8, -0.6], [-0.4, -1.1, -0.5], [0, 0, 0]])},
            {"obj": 52, "name": "AlphaOverLife", "layer": "Glow", "kind": "curve", "data": _curve([0, 0.1, 0.8, 1], [0, 1, 0.9, 0], interp="linear")},
            {"obj": 53, "name": "Wind", "layer": "Embers", "kind": "turbulence", "data": {"frequency": 0.7, "octaves": 3, "strength": 0.4}},
            {"obj": 54, "name": "SizeOverLife", "layer": "Glow", "kind": "curve", "data": _curve([0, 1], [0.5, 1.5], interp="linear")}],     # names repeat across layers
        "events": [], "entry": [3, 8],
        "scripts": {            # keyed by script id = the graph's object number = the number in the listing header ("object N")
            "11": {"kind": "spawn", "layer": "Embers", "externals": ["Position", "Life", "Color"], "calls": ["rand", "curve_sample"]},
            "12": {"kind": "evolve", "layer": "Embers", "externals": ["Position", "Size", "Color"], "calls": ["curve_sample", "turbulence"]},
            "13": {"kind": "evolve", "layer": "Glow", "externals": ["Size", "Color"], "calls": []}},
        "_listings": {
            "11": _listing(11, [[1.0]], ["rand r0", "mul r0, r0, c0", "ret"]),
            "12": _listing(12, [[1.0, 0.55, 0.15, 1.0], [0.8], [2.0]], ["mov r0, ext:Position", "mul r1, ext:Color, c0", "mad r2, r1, c1, c2", "store ext:Color, r2", "ret"]),
            "13": _listing(13, [[0.3], [1.0, 0.3, 0.05, 0.6]], ["load r0, ext:Size", "mul r0, r0, c0", "store ext:Size, r0", "ret"])},
    }
    bolt = {
        "path": LOGICAL + "vfx_mock_bolt.particles", "version": "0x00010001", "objects": 30, "strings": 8,
        "layers": [{"obj": 3, "name": "Bolt", "spawn_script": None, "evolve_scripts": [], "fields": [], "renderers": [
            _renderer(0, "ribbon", "vfx/materials/Diablo_Uber.pkma", {"EnableRendering": {"type": "bool", "value": True}, "Width": {"type": "float", "value": 0.12},
                                                                       "Color": {"type": "color", "value": [0.5, 0.7, 1.0, 1.0]}, "DiffuseMap": tex("trail/lightning.texture")}),
            _renderer(1, "light", "", {"EnableRendering": {"type": "bool", "value": True}, "Intensity": {"type": "float", "value": 3.0},
                                       "Color": {"type": "color", "value": [0.4, 0.6, 1.0, 1.0]}})],
             "events": [], "children": [], "life": 0.6, "spawn": {"count": 1, "duration": 0.6}}],
        "attributes": [], "samplers": [{"obj": 50, "name": "Flicker", "kind": "curve", "data": _curve([0, 0.1, 0.2, 0.6], [1, 0.3, 1, 0], interp="step")}],
        "events": [], "entry": [3], "scripts": {}, "_listings": {}}
    smoke = {
        "path": LOGICAL + "vfx_mock_smoke.particles", "version": "0x00010001", "objects": 40, "strings": 10,
        "layers": [{"obj": 3, "name": "Plume", "spawn_script": None, "evolve_scripts": [], "fields": [], "renderers": [
            _renderer(0, "billboard", "vfx/materials/Diablo_Uber.pkma", {"EnableRendering": {"type": "bool", "value": True}, "Opacity": {"type": "float", "value": 0.4},
                                                                          "Color": {"type": "color", "value": [0.5, 0.5, 0.55, 0.7]}, "DiffuseMap": tex("smoke/puff.texture")}),
            _renderer(1, "mesh", "vfx/materials/Diablo_Uber.pkma", {"EnableRendering": {"type": "bool", "value": False}, "Mesh": {"type": "mesh", "file": "data/hd/vfx/meshes/cone.gr2"}})],
             "events": [], "children": [], "life": [2.0, 3.5], "spawn": {"rate": 8, "duration": 6.0}}],
        "attributes": [], "samplers": [{"obj": 50, "name": "Rise", "kind": "curve", "data": _curve([0, 1], [[0.0, 0.0], [0.0, 1.6]], dim=2)}],
        "events": [], "entry": [3], "scripts": {}, "_listings": {}}
    return {g["path"]: g for g in (embers, bolt, smoke)}


# ------------------------------------------------------------------------------------------------------ the tools
class Mock:
    def __init__(self, out: Path):
        self.out = Path(out).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.fx = fixtures()
        self.lock = threading.Lock()
        self.tools = {n: getattr(self, n) for n in ("fx_list", "fx_inspect", "fx_edit", "fx_session", "fx_validate", "fx_create",
                                                   "fx_script_read", "fx_script_write", "fx_render", "fx_export", "fx_coverage",
                                                   "fx_color_variants")}

    # -- sources and sessions
    def _name(self, path):
        return re.sub(r"[^A-Za-z0-9_.\-]", "_", Path(path).name.split(".")[0]) or "effect"

    def _session_dir(self, path):
        p = Path(path)
        if (p / "session.json").is_file():
            return p
        if p.suffix == ".particles" and (p.parent / "session.json").is_file():
            return p.parent
        return None

    def _open(self, path):
        sd = self._session_dir(path)
        if sd:
            meta = json.loads((sd / "session.json").read_text())
            g = json.loads((sd / ("%s.particles" % meta["name"])).read_text())
            return g, sd, meta
        key = str(path).replace("\\", "/")
        key = key[key.index("hd/"):] if "hd/" in key else key
        if key not in self.fx:
            raise MockError("not_found", "no effect %r (mock effects: %s)" % (path, ", ".join(sorted(self.fx))))
        return copy.deepcopy(self.fx[key]), None, None

    def _save(self, sd, meta, g):
        (sd / ("%s.particles" % meta["name"])).write_text(json.dumps(g))
        (sd / "session.json").write_text(json.dumps(meta, indent=1))

    def _info(self, sd, meta):
        n = len(meta["versions"])
        return {"out": str(sd), "work": str(sd / ("%s.particles" % meta["name"])), "source": meta["source"], "logical": meta["logical"], "version": meta["cursor"],
                "versions": n, "can_undo": meta["cursor"] > 0, "can_redo": meta["cursor"] < n - 1}

    def _session_for(self, path, out):
        g, sd, meta = self._open(path)
        if sd:
            return g, sd, meta
        sd = Path(out).resolve() if out else self.out / "edit" / ("%s_%s" % (self._name(path), hashlib.sha256(g["path"].encode()).hexdigest()[:8]))
        if (sd / "session.json").is_file():
            meta = json.loads((sd / "session.json").read_text())
            return json.loads((sd / ("%s.particles" % meta["name"])).read_text()), sd, meta
        (sd / "history").mkdir(parents=True, exist_ok=True)
        meta = {"name": self._name(path), "source": g["path"], "logical": g["path"], "cursor": 0, "versions": [{"version": 0, "label": "open", "ops": [], "size": 0}]}
        (sd / "history" / "v0000.particles").write_text(json.dumps(g))
        self._save(sd, meta, g)
        return g, sd, meta

    def _commit(self, sd, meta, g, label, ops):
        vs = meta["versions"]
        for v in vs[meta["cursor"] + 1:]:
            (sd / "history" / ("v%04d.particles" % v["version"])).unlink(missing_ok=True)
        del vs[meta["cursor"] + 1:]
        n = len(vs)
        (sd / "history" / ("v%04d.particles" % n)).write_text(json.dumps(g))
        vs.append({"version": n, "label": label, "ops": ops, "size": len(json.dumps(g))})
        meta["cursor"] = n
        self._save(sd, meta, g)

    # -- tools
    def fx_list(self, pattern=None, roots=None, texture=None, material=None, kind=None, limit=200, info=False):
        items = []
        for p, g in sorted(self.fx.items()):
            rs = [r for l in g["layers"] for r in l["renderers"]]
            if pattern and not (fnmatch.fnmatch(p.lower(), pattern.lower()) or pattern.lower().strip("*") in p.lower()):
                continue
            if kind and not any(r["kind"] == kind for r in rs):
                continue
            if texture and not any(texture.lower() in (v.get("file") or "").lower() for r in rs for v in r["props"].values()):
                continue
            it = {"path": p, "root": "mock", "file": "mock:" + p, "size": len(json.dumps(g))}
            if info:
                it["kinds"] = {}
                for r in rs:
                    it["kinds"][r["kind"]] = it["kinds"].get(r["kind"], 0) + 1
                it["materials"] = sorted({r["material"] for r in rs if r["material"]})
            items.append(it)
        return {"ok": True, "count": len(items), "truncated": len(items) > limit, "items": items[:limit]}

    def fx_inspect(self, path, roots=None, graph=True):
        g, sd, meta = self._open(path)
        rend = [{"index": r["index"], "object": r["obj"], "kind": r["kind"], "material": r["material"], "enabled": r["enabled"],
                 "props": {k: {kk: vv for kk, vv in v.items()} for k, v in r["props"].items()},
                 "files": sorted({v["file"] for v in r["props"].values() if v.get("file")})} for l in g["layers"] for r in l["renderers"]]
        scripts = []
        for sid, s in g["scripts"].items():
            text = g["_listings"].get(sid, "")
            scripts.append({"script": int(sid), "backend": "CPU/CPU_VM", "kind": 2, "instructions": len([x for x in text.splitlines() if x and x[0] not in ".;"]),
                            "constants": text.count("\n.const "), "externals": s["externals"], "engine_calls": s["calls"]})
        gr = {k: v for k, v in g.items() if not k.startswith("_")} if graph else None
        return {"ok": True, "path": g["path"], "file": "mock:" + g["path"], "root": "mock", "size": len(json.dumps(g)), "version": g["version"], "objects": g["objects"],
                "strings": g["strings"], "classes": {"CParticleLayer": len(g["layers"]), "CParticleRenderer": len(rend)}, "renderers": rend, "scripts": scripts,
                "textures": sorted({f for r in rend for f in r["files"]}), "graph": gr, "engine": self.engine(),
                "session": self._info(sd, meta) if sd else None}

    def engine(self):
        return {"graph": True, "sim": False, "render": False, "context": False, "lang": False, "coverage": False, "mock": True}

    def _sel(self, g, sel):
        rs = [r for l in g["layers"] for r in l["renderers"]]
        if sel == "*":
            return rs
        if isinstance(sel, int) or (isinstance(sel, str) and sel.isdigit()):
            return [r for r in rs if r["index"] == int(sel)] or self._bad("no renderer %s" % sel)
        if isinstance(sel, str) and sel.startswith("layer:"):
            return [r for l in g["layers"] if l["name"] == sel[6:] for r in l["renderers"]] or self._bad("no layer %s" % sel[6:])
        return [r for r in rs if r["kind"] == sel] or self._bad("no renderer matches %r" % sel)

    @staticmethod
    def _bad(msg):
        raise MockError("bad_args", msg)

    def _apply(self, g, op, i):
        def fail(msg):
            raise MockError("edit_failed", "op %d (%s): %s" % (i, op.get("op"), msg), op_index=i)
        k = op.get("op")
        try:
            if k == "prop":
                for r in self._sel(g, op["renderer"]):
                    p = r["props"].get(op["name"]) or fail("no property %r" % op["name"])
                    if p["type"] in ("texture", "mesh", "?"):
                        fail("%r is a %s property: use the texture op" % (op["name"], p["type"]))
                    p["value"] = op["value"]
            elif k == "texture":
                for r in self._sel(g, op["renderer"]):
                    p = r["props"].get(op["name"]) or fail("no property %r" % op["name"])
                    p["file"] = op["path"]
            elif k == "enable":
                for r in self._sel(g, op["renderer"]):
                    r["enabled"] = bool(op["on"])
                    if "EnableRendering" in r["props"]:
                        r["props"]["EnableRendering"]["value"] = bool(op["on"])
            elif k == "color" and "stream" in op:
                t = g.setdefault("tint", [1, 1, 1, 1])
                g["tint"] = [a * b for a, b in zip(t, op["rgba"])]
            elif k == "color":
                lay = next((l for l in g["layers"] if l["name"] == op.get("layer")), None) or fail("no layer %r" % op.get("layer"))
                hit = [c for c in lay["colors"] if c["index"] == op.get("index")] if "index" in op else lay["colors"]
                hit or fail("no colour constant %r in layer %s" % (op.get("index"), lay["name"]))
                for c in hit:
                    if "value" in op:
                        len(op["value"]) == c["width"] or fail("colour %d takes %d numbers" % (c["index"], c["width"]))
                        c["value"] = list(op["value"])
                    elif "shift" in op:
                        import colorsys
                        sh = op["shift"]
                        h, sa, v = colorsys.rgb_to_hsv(*[min(x, 1.0) for x in c["value"][:3]])
                        rgb = colorsys.hsv_to_rgb((h + sh.get("hue", 0) / 360.0) % 1.0, min(1.0, sa * sh.get("sat", 1)), v * sh.get("val", 1))
                        c["value"] = list(rgb) + c["value"][3:]
                    else:
                        fail("color needs value or shift")
            elif k == "curve":
                hits = [x for x in g["samplers"] if str(x["obj"]) == str(op["sampler"])] or [x for x in g["samplers"] if x["name"] == op["sampler"]]
                if len(hits) != 1:
                    fail("sampler %r: %s" % (op["sampler"], "no such sampler" if not hits else "the name is not unique: use the object number"))
                s = hits[0]
                s["kind"] == "curve" or fail("sampler %r is not a curve" % op["sampler"])
                n = len(op["times"])
                if any(b <= a for a, b in zip(op["times"], op["times"][1:])) or len(op["values"]) != n:
                    fail("times must increase and match values")
                s["data"].update({"times": op["times"], "values": op["values"], "tangents_in": op.get("tangents_in") or s["data"]["tangents_in"],
                                  "tangents_out": op.get("tangents_out") or s["data"]["tangents_out"]})
            elif k == "const":
                sid = str(op["script"])
                text = g["_listings"].get(sid) or fail("no script %s" % op["script"])
                vals = (list(map(float, op["values"])) * 8)[:8]
                fmt = " ".join(repr(float(v)) if op.get("kind", "f") == "f" else str(int(v)) for v in vals)
                new, n = re.subn(r"(?m)^\.const %d \w .*$" % int(op["index"]), ".const %d %s %s" % (int(op["index"]), op.get("kind", "f"), fmt), text)
                n or fail("no constant %s" % op["index"])
                g["_listings"][sid] = new
            elif k == "script":
                sid = str(op["script"])
                sid in g["scripts"] or fail("no script %s" % op["script"])
                self._check_listing(op["text"])
                g["_listings"][sid] = op["text"]
            elif k == "copy_layer":
                other, _, _ = self._open(op["from"])
                src = next((l for l in other["layers"] if l["name"] == op["layer"]), None) or fail("no layer %r in %s" % (op["layer"], op["from"]))
                lay = copy.deepcopy(src)
                lay["name"] = op.get("as") or src["name"]
                if any(l["name"] == lay["name"] for l in g["layers"]):
                    fail("layer %r exists: give an 'as' name" % lay["name"])
                nxt = max([r["index"] for l in g["layers"] for r in l["renderers"]] + [-1]) + 1
                for r in lay["renderers"]:
                    r["index"], r["obj"] = nxt, 100 + nxt
                    nxt += 1
                lay["obj"] = max(l["obj"] for l in g["layers"]) + 10
                g["layers"].append(lay)
                g["objects"] += 8
            elif k == "remove_layer":
                n = len(g["layers"])
                g["layers"] = [l for l in g["layers"] if l["name"] != op["layer"]]
                len(g["layers"]) < n or fail("no layer %r" % op["layer"])
            else:
                fail("unknown op")
        except KeyError as e:
            fail("missing field %s" % e)

    def _check_listing(self, text):
        for n, raw in enumerate(text.splitlines(), 1):
            line = raw.split(";")[0].strip()
            if not line or line.startswith("."):
                if line.startswith(".const"):
                    if not re.match(r"^\.const \d+ [fi] (-?[\d.e+-]+\s*)+$", line):
                        raise MockError("parse_error", "line %d: bad constant %r" % (n, line), line=n)
                continue
            if line.split()[0] not in CBVM_OPS:
                raise MockError("parse_error", "line %d: unknown instruction %r" % (n, line.split()[0]), line=n)

    def fx_edit(self, path, ops, out=None, roots=None, validate=True):
        g, sd, meta = self._session_for(path, out)
        g2 = copy.deepcopy(g)
        for i, op in enumerate(ops):
            self._apply(g2, op, i)
        self._commit(sd, meta, g2, "edit: " + ", ".join(o["op"] for o in ops), ops)
        return {"ok": True, "session": self._info(sd, meta), "applied": [{"op": o["op"], "changed": 1} for o in ops],
                "validate": self.fx_validate(str(sd)) if validate else None}

    def fx_session(self, out, action="status", version=None):
        sd = self._session_dir(out) or self._bad("%s is not an edit session folder" % out)
        meta = json.loads((sd / "session.json").read_text())
        c, n = meta["cursor"], len(meta["versions"])
        tgt = {"undo": c - 1, "redo": c + 1, "reset": 0, "goto": -1 if version is None else int(version)}.get(action)
        if action in ("undo", "redo", "reset", "goto"):
            if not 0 <= tgt < n:
                raise MockError("bad_args", "nothing to %s" % action if action in ("undo", "redo") else "no version %s" % version)
            meta["cursor"] = tgt
            self._save(sd, meta, json.loads((sd / "history" / ("v%04d.particles" % tgt)).read_text()))
        elif action not in ("status", "history"):
            raise MockError("bad_args", "action: status, history, undo, redo, reset, goto")
        return {"ok": True, "session": self._info(sd, meta),
                "history": [{**v, "current": v["version"] == meta["cursor"]} for v in meta["versions"]] if action == "history" else None}

    def fx_validate(self, path, roots=None, files=True):
        g, _, _ = self._open(path)
        errors, warnings = [], []
        rs = [r for l in g["layers"] for r in l["renderers"]]
        if not any(r["enabled"] for r in rs):
            errors.append({"check": "renderers", "message": "no renderer is enabled", "where": ""})
        for r in rs:
            for n, p in r["props"].items():
                if p["type"] in ("texture", "mesh") and not p.get("file"):
                    errors.append({"check": "renderers", "message": "property %s names no file" % n, "where": "renderer %d" % r["index"]})
                elif p.get("file") and files and "/mock_missing/" in p["file"]:
                    warnings.append({"check": "files", "message": "%s does not resolve" % p["file"], "where": "renderer %d" % r["index"]})
        return {"ok": not errors, "errors": errors, "warnings": warnings, "checks": {"parse": True, "roundtrip": True, "scripts": True, "renderers": not errors, "files": True, "graph": True}}

    def fx_create(self, template=None, layers=None, name=None, textures=None, logical=None, out=None, roots=None):
        if template:
            g, _, _ = self._open(template)
        else:
            g = {"path": LOGICAL + "vfx_mock_new.particles", "version": "0x00010001", "objects": 8, "strings": 2, "layers": [], "attributes": [], "samplers": [],
                 "events": [], "entry": [], "scripts": {}, "_listings": {}}
        for spec in layers or []:
            self._apply(g, {"op": "copy_layer", "from": spec["from"], "layer": spec["layer"], "as": spec.get("as")}, 0)
        name = name or self._name(g["path"])
        g["path"] = logical or LOGICAL + name + ".particles"
        self.fx[g["path"]] = copy.deepcopy(g)
        _, sd, meta = self._session_for(g["path"], out)
        return {"ok": True, "session": self._info(sd, meta), "logical": g["path"], "layers": [l["name"] for l in g["layers"]]}

    def fx_script_read(self, path, script=None, language="cbvm", roots=None):
        g, _, _ = self._open(path)
        if script is None:
            return {"ok": True, "scripts": self.fx_inspect(path)["scripts"]}
        text = g["_listings"].get(str(script))
        if text is None:
            raise MockError("not_found", "script %r: scripts are %s" % (script, sorted(int(k) for k in g["scripts"])))
        if language == "hfx":
            text = "; .hfx view of the CBVM listing (mock: the decompiler is lane lang)\n" + "\n".join("// " + x for x in text.splitlines())
        return {"ok": True, "script": script, "language": language, "text": text, "instructions": text.count("\n"), "externals": g["scripts"][str(script)]["externals"]}

    def fx_script_write(self, path, script, source, language="auto", out=None, roots=None):
        r = self.fx_edit(path, [{"op": "script", "script": script, "text": source, "language": language}], out=out)
        return {"ok": True, "session": r["session"], "script": script, "language": language, "instructions": source.count("\n"), "validate": r["validate"]}

    def fx_render(self, path, t=1.0, clip=None, cam=None, context=None, size="640x480", format="png", seed=0, attrs=None, bg="#101014", out=None, roots=None, wait_s=0.0):
        from PIL import Image
        g, _, _ = self._open(path)
        w, h = (int(x) for x in size.lower().split("x"))
        times = [t] if not clip else [clip["t0"] + k / clip.get("fps", 25) for k in range(int((clip["t1"] - clip["t0"]) * clip.get("fps", 25)) + 1)]
        frames = [Image.fromarray(self._draw(g, tt, seed, w, h, context)) for tt in times]
        key = hashlib.sha256(json.dumps([g, t, clip, cam, context, size, format, seed], sort_keys=True, default=str).encode()).hexdigest()[:8]
        o = Path(out) if out else self.out / "fx_render" / ("%s_%s" % (self._name(g["path"]), key))
        o.mkdir(parents=True, exist_ok=True)
        f = o / ("%s.%s" % (self._name(g["path"]), "gif" if format == "gif" else "png"))
        if format == "gif":
            frames[0].save(f, save_all=True, append_images=frames[1:], duration=int(1000 / (clip or {}).get("fps", 25)), loop=0)
        else:
            frames[0].save(f)
        return {"ok": True, "files": [str(f)], "format": format, "frames": len(frames), "size": [w, h], "t": times, "camera": cam or {"preset": "orbit"}, "context": context,
                "stats": {"mock": True}}

    def _draw(self, g, t, seed, w, h, context):
        def curve(name):
            s = next((x for x in g["samplers"] if x["name"] == name and x["kind"] == "curve"), None)
            if not s:
                return None
            d = s["data"]
            tt, vv = d["times"], np.array(d["values"], float)
            return lambda u: np.array([np.interp(u, tt, vv[:, k]) for k in range(vv.shape[1])])
        size_c, col_c, alpha_c = curve("SizeOverLife"), curve("ColorOverLife"), curve("AlphaOverLife")
        img = np.zeros((h, w, 3), np.float32) + np.linspace(0.05, 0.09, h)[:, None, None]
        tint = np.array(g.get("tint", [1, 1, 1, 1]), np.float32)
        for l in g["layers"]:
            life = max(l["life"]) if isinstance(l["life"], list) else float(l["life"])
            for r in l["renderers"]:
                if not r["enabled"] or r["kind"] == "mesh":
                    continue
                col = np.array((r["props"].get("Color", {"value": [1, 1, 1, 1]})["value"] + [1, 1, 1, 1])[:4], np.float32)
                op = float(r["props"].get("Opacity", {"value": 1.0})["value"])
                n = 1 if r["kind"] == "light" else 48
                rng = np.random.default_rng(seed * 1000 + r["index"] * 17 + 3)
                phase, px, vx = rng.random(n), rng.random(n), rng.random(n)
                for k in range(n):
                    age = ((t / max(life, 0.1)) + phase[k]) % 1.0
                    sz = 0.1 * float(size_c(age)[0]) if size_c and r["kind"] == "billboard" else 0.05
                    c = (col_c(age)[:3] if col_c and r["kind"] == "billboard" else col[:3]) * col[:3] * tint[:3]
                    a = float(alpha_c(age)[0]) if alpha_c else 1.0 - age
                    cx = w * (0.3 + 0.4 * px[k]) + 12 * np.sin(t * 2 + k)
                    cy = h * (0.85 - 0.6 * age * (0.5 + vx[k])) if r["kind"] != "light" else h * 0.5
                    rad = max(2.0, sz * min(w, h) * (3 if r["kind"] == "light" else 1))
                    x0, x1, y0, y1 = int(max(cx - 3 * rad, 0)), int(min(cx + 3 * rad + 1, w)), int(max(cy - 3 * rad, 0)), int(min(cy + 3 * rad + 1, h))
                    if x1 <= x0 or y1 <= y0:
                        continue
                    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
                    g2 = np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * rad * rad)))
                    img[y0:y1, x0:x1] += g2[:, :, None] * c[None, None, :] * (a * op * 0.5)
        img = np.clip(img, 0, 1) ** (1 / 1.2)
        img[:6, :6] = (1.0, 0.7, 0.2)                                     # a corner mark: this is the mock's picture, not the game's
        return (img * 255).astype(np.uint8)

    def fx_export(self, path, mod_dir, logical=None, check=True, extras=None, roots=None):
        g, _, meta = self._open(path)
        lg = logical or (meta or {}).get("logical") or g["path"]
        if ".." in lg.split("/"):
            raise MockError("bad_args", "bad logical path")
        dest = Path(mod_dir) / "data" / lg
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(g).encode()
        dest.write_bytes(data)
        return {"ok": True, "written": [{"logical": lg, "file": str(dest), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}], "missing": [], "check": {"ok": True}}

    def fx_color_variants(self, path=None, roots=None):
        from hdkit.fx import colorvariant as cv
        t = cv.Table()
        return {"ok": True, "attr": cv.ATTR, "table_found": False, "choices": t.choices(),
                "states": {k: cv.index_for(k) for k in cv.STATE_SHIFTS}, "renderers": []}

    def fx_coverage(self, pattern=None, roots=None, limit=500, summary=False):
        effs = [{"path": p, "status": "unknown", "kinds": {}, "reasons": ["mock"]} for p in sorted(self.fx) if not pattern or pattern.strip("*") in p]
        return {"ok": True, "count": len(effs), "status": {"unknown": len(effs)}, "effects": effs[:limit]}

    def call(self, _tool, /, **args):
        name = _tool
        fn = self.tools.get(name)
        if fn is None:
            return MockError("bad_args", "no tool %r" % name).as_dict()
        try:
            with self.lock:
                return fn(**args)
        except MockError as e:
            return e.as_dict()
        except TypeError as e:
            return MockError("bad_args", "%s: %s" % (name, e)).as_dict()
        except Exception as e:  # noqa: BLE001
            return MockError("error", "%s: %s" % (type(e).__name__, e)).as_dict()


# ------------------------------------------------------------------------------------------------------ the server
def make_server(out, port=0, quiet=True):
    mock = Mock(out)

    class MockHandler(webserve.Handler):
        bundle = None
        quiet = True

        def _json(self, code, obj):
            self._send(code, json.dumps(webserve._finite(obj), default=str).encode("utf-8"), "application/json")

        def _fx_guard(self) -> bool:
            """As the real endpoints: Host must be 127.0.0.1 or localhost (DNS rebinding) and an Origin, if any, the same."""
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
            origin = self.headers.get("Origin")
            if host not in ("127.0.0.1", "localhost") or not (origin is None or re.fullmatch(r"https?://(127\.0\.0\.1|localhost)(:\d+)?", origin)):
                self._json(403, {"ok": False, "error": {"code": "forbidden", "message": "not a request from this machine's page"}})
                return False
            return True

        def do_GET(self):
            return self._fx_get() if self.path.startswith("/api/fx/") else super().do_GET()

        do_HEAD = do_GET

        def do_POST(self):
            return self._fx_post() if self.path.startswith("/api/fx/") else super().do_POST()

        def resolve(self):
            p = super().resolve()
            return webserve.WEB / "fx.html" if self.path.split("?")[0] == "/" else p

        def _fx_get(self):
            if not self._fx_guard():
                return
            url, _, query = self.path.partition("?")
            if url == "/api/fx/tools":
                return self._json(200, {"tools": {n: {"description": "mock", "input": {}} for n in mock.tools}, "engine": mock.engine(), "out_root": str(mock.out),
                                        "default_effect": LOGICAL + "vfx_mock_embers.particles"})
            if url == "/api/fx/file":
                q = dict(kv.partition("=")[::2] for kv in query.split("&") if kv)
                f = Path(unquote(q.get("path", "")))
                try:
                    f.resolve().relative_to(mock.out)
                except ValueError:
                    return self._json(403, {"ok": False, "error": {"code": "forbidden", "message": "only files below %s" % mock.out}})
                if not f.is_file():
                    return self._json(404, {"ok": False, "error": {"code": "not_found", "message": str(f)}})
                return self._send(200, f.read_bytes(), FX_TYPES.get(f.suffix.lower(), "application/octet-stream"))
            self._json(404, {"ok": False, "error": {"code": "not_found", "message": url}})

        def _fx_post(self):
            if not self._fx_guard():
                return
            name = unquote(self.path.split("?", 1)[0])[len("/api/fx/"):]
            n = int(self.headers.get("Content-Length") or 0)
            if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json" or n > FX_MAX_BODY:
                return self._json(400, {"ok": False, "error": {"code": "bad_args", "message": "POST application/json, at most 8 MB"}})
            try:
                args = json.loads(self.rfile.read(n) or b"{}")
                assert isinstance(args, dict)
            except (ValueError, AssertionError):
                return self._json(400, {"ok": False, "error": {"code": "bad_args", "message": "the body is one JSON object"}})
            if name not in mock.tools:
                return self._json(400, {"ok": False, "error": {"code": "bad_args", "message": "no tool %r" % name}})
            for key in ("out",):
                if isinstance(args.get(key), str):
                    try:
                        Path(args[key]).resolve().relative_to(mock.out)
                    except ValueError:
                        return self._json(403, {"ok": False, "error": {"code": "forbidden", "message": "out must be below %s" % mock.out}})
            self._json(200, mock.call(name, **args))

    MockHandler.quiet = quiet
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), MockHandler)
    srv.daemon_threads = True
    srv.mock = mock
    return srv


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hdkit.view.fxmock", description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--out", default=None, help="folder for sessions and renders (default: a fresh temporary folder)")
    a = ap.parse_args(argv)
    import tempfile
    out = a.out or tempfile.mkdtemp(prefix="hdfx-mock-")
    srv = make_server(out, a.port, quiet=True)
    print("hdfx mock API + editor on http://127.0.0.1:%d/ (sessions in %s; Ctrl+C stops it)" % (srv.server_address[1], out), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
