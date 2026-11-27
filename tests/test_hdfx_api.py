"""hdkit.fx.api (the hdfx tools), their MCP wrappers, the web JSON endpoints and the doc, on a synthetic particle file (a mesh
and a billboard renderer plus one CBVM script that writes a grey colour); no game files, no GPU. The stdio end-to-end check of
the MCP server runs when the `mcp` package is present. Lanes that have not landed are checked as `not_implemented`; the
render orchestration runs against fake lane modules.
Run: python -m unittest discover -s tests -p "test_hdfx_api.py" """
import asyncio
import http.client
import json
import os
import re
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np
from PIL import Image

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hdkit.fx import cbvm as hc  # noqa: E402
from hdkit.formats import hd_particles as hp  # noqa: E402
import test_hdfx_synth as thp  # noqa: E402  (the synthetic renderer objects)
from hdkit.fx import api  # noqa: E402

try:
    import mcp  # noqa: F401
    HAVE_MCP = True
except ImportError:
    HAVE_MCP = False

STRINGS = thp.STRINGS + [hc.BLOB, "CPU/CPU_VM", "Color", "Age"]           # blob strings: 9 class, 10 backend, 11 Color, 12 Age
T = {n: hc.TYPES.index(n) for n in ("float", "float4")}
LOGICAL = "hd/vfx/particles/test/fx_test.particles"


def reg(cls: str, idx: int, typ: str) -> int:
    return T[typ] << 24 | hc.CLASS.index(cls) << 21 | idx


def blob_object() -> bytes:
    """Age -> * c0 -> float4 grey -> Color: the shape of a grey-level colour script (see test_hd_cbvm)."""
    code = hc.encode([hc.Ins(0x69, [reg("s", 0, "float")], [1]),
                      hc.Ins(0x73, [reg("s", 0, "float"), reg("s", 0, "float"), reg("c", 0, "float")]),
                      hc.Ins(0x6E, [reg("s", 1, "float4"), reg("s", 0, "float")], [0, 0, 0]),
                      hc.Ins(0x6A, [reg("s", 1, "float4")], [0]), hc.Ins(0x7D)])
    consts = [struct.pack("<8f", *[0.5] * 8)]
    prog = thp.u32(3, len(code), 0, 32, 0, 1, 0, 0, 0) + code + consts[0]
    prog += b"\0" * (-len(prog) % 4)
    arr = lambda w: thp.u32(len(w) // 4) + w  # noqa: E731
    f = [(0, thp.u32(10)), (1, thp.u32(4)), (2, arr(prog)), (4, arr(thp.u32(*range(10)))), (5, arr(thp.u32(11, 12)))]
    return thp.obj(9, f)


def particle_file() -> bytes:
    objs = [thp.prop(2, hp.T_BOOL, hp.ON), thp.prop(3, hp.T_TEXTURE, string=4),
            thp.prop(6, hp.T_FLOAT, struct.pack("<4f", 0, 0, 0, 0)), thp.prop(7, hp.T_MESH, string=8),
            thp.renderer(2, [1, 2, 3, 4]), thp.prop(2, hp.T_BOOL, hp.ON), thp.prop(3, hp.T_TEXTURE, string=4),
            thp.renderer(None, [6, 7]), blob_object()]
    body = b"".join(objs)
    classes = thp.u32(0, 6, 1, 2, 9, 1)
    stro = 0x1C + len(classes) + len(body)
    head = thp.u32(hp.MAGIC, 0x01050902, 0x1F40, len(objs), 3, stro, 0)
    tail = thp.u32(len(STRINGS)) + b"".join(bytes([len(s)]) + s.encode() for s in STRINGS)
    return head + classes + body + tail


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.d = Path(self.td.name)
        self.addCleanup(self.td.cleanup)
        self.root = self.d / "game"
        self.file = self.root / LOGICAL
        self.file.parent.mkdir(parents=True)
        self.file.write_bytes(particle_file())
        self.roots = ["g=%s" % self.root]
        env = mock.patch.dict(os.environ, {"HDKIT_FX_OUT": str(self.d / "out")})
        env.start()
        self.addCleanup(env.stop)
        self.out = self.d / "out"

    def ok(self, tool, **args):
        r = api.call(tool, **args)
        self.assertTrue(r.get("ok"), r)
        return r

    def err(self, tool, code, **args):
        r = api.call(tool, **args)
        self.assertFalse(r["ok"], r)
        self.assertEqual(r["error"]["code"], code, r)
        return r["error"]


class Reading(Base):
    def test_list_and_search(self):
        (self.root / "hd/vfx/particles/test/other.particles").write_bytes(b"junk")
        r = self.ok("fx_list", roots=self.roots, pattern="fx_test")
        self.assertEqual([i["path"] for i in r["items"]], [LOGICAL])
        self.assertEqual(r["items"][0]["root"], "g")
        self.assertEqual(self.ok("fx_list", roots=self.roots)["count"], 2)
        self.assertEqual(self.ok("fx_list", roots=self.roots, pattern="*test/fx_*")["count"], 1)
        r = self.ok("fx_list", roots=self.roots, kind="mesh", info=True)
        self.assertEqual(r["items"][0]["kinds"], {"mesh": 1, "billboard": 1})
        self.assertEqual(r["items"][0]["materials"], ["vfx/materials/Diablo_Uber.pkma"])
        self.assertEqual(self.ok("fx_list", roots=self.roots, kind="ribbon")["count"], 0)
        self.assertEqual(self.ok("fx_list", roots=self.roots, texture="flame_gradient")["count"], 1)
        self.assertEqual(self.ok("fx_list", roots=self.roots, material="nope")["count"], 0)
        self.assertEqual(self.ok("fx_list", roots=self.roots, limit=0)["truncated"], True)
        self.err("fx_list", "bad_args", roots=[])

    def test_inspect_by_logical_path_file_and_data_prefix(self):
        r = self.ok("fx_inspect", path=LOGICAL, roots=self.roots)
        self.assertEqual((r["path"], r["root"], r["objects"]), (LOGICAL, "g", 9))
        self.assertEqual([x["kind"] for x in r["renderers"]], ["mesh", "billboard"])
        self.assertEqual(r["renderers"][0]["props"]["General.Mesh"], {"type": "mesh", "file": thp.MESH})
        self.assertEqual(r["renderers"][0]["props"]["EnableRendering"], {"type": "bool", "value": True})
        self.assertEqual(r["classes"]["CCompilerBlobCache"], 1)
        self.assertEqual(r["scripts"][0]["script"], 9)
        self.assertEqual(r["scripts"][0]["externals"], ["Color", "Age"])
        if api.lane("graph") is None:
            self.assertIsNone(r["graph"])
        else:   # lane format: a file without a layer graph shows its renderers in one pseudo layer
            self.assertEqual([x["name"] for x in r["graph"]["layers"]], ["@renderers"])
        by_file = self.ok("fx_inspect", path=str(self.file))
        self.assertEqual(by_file["objects"], 9)
        self.assertEqual(self.ok("fx_inspect", path="Data/" + LOGICAL.upper(), roots=self.roots)["objects"], 9)

    def test_errors(self):
        self.err("fx_inspect", "not_found", path="hd/vfx/nope.particles", roots=self.roots)
        junk = self.d / "junk.particles"
        junk.write_bytes(b"PKFX" + b"\0" * 40)
        self.err("fx_inspect", "parse_error", path=str(junk))
        self.err("fx_inspect", "bad_args", path="")
        self.err("nope", "bad_args")
        self.err("fx_inspect", "bad_args", pth="x")          # a wrong argument name is bad_args, not a crash

    def test_validate(self):
        r = self.ok("fx_validate", path=LOGICAL, roots=self.roots)
        self.assertEqual(r["checks"]["parse"], True)
        self.assertEqual(r["checks"]["roundtrip"], True)
        self.assertEqual(r["checks"]["scripts"], True)
        self.assertEqual(r["checks"]["graph"], None if not api.lane("graph") else True)
        self.assertEqual(len(r["warnings"]), 2)              # the gradient texture and the mesh are not in the roots
        self.assertEqual(self.ok("fx_validate", path=LOGICAL, roots=self.roots, files=False)["warnings"], [])
        bad = self.d / "bad.particles"
        bad.write_bytes(particle_file() + b"x")
        r = api.call("fx_validate", path=str(bad))
        self.assertEqual(r["error"]["code"], "parse_error") if "error" in r else self.assertFalse(r["ok"])

    def test_coverage(self):
        r = self.ok("fx_coverage", roots=self.roots)
        e = r["effects"][0]
        self.assertEqual(e["kinds"], {"mesh": 1, "billboard": 1})
        self.assertEqual(e["externals"], ["Age", "Color"])
        self.assertEqual(e["opcodes"]["st"], 1)
        self.assertIn(e["status"], ("not", "partly", "drawn") if api.lane("coverage") else ("unknown",))
        s = self.ok("fx_coverage", roots=self.roots, summary=True)
        self.assertNotIn("effects", s)
        self.assertEqual(s["common"]["opcodes:st"], 1)


class Editing(Base):
    def edit(self, ops, **kw):
        return self.ok("fx_edit", path=LOGICAL, roots=self.roots, ops=ops, **kw)

    def props(self, out):
        return {k: v for k, v in self.ok("fx_inspect", path=out)["renderers"][0]["props"].items()}

    def test_ops(self):
        r = self.edit([{"op": "enable", "renderer": "mesh", "on": False},
                       {"op": "prop", "renderer": 0, "name": "Transparent.CameraSortOffset", "value": 2.5},
                       {"op": "texture", "renderer": "*", "name": "GradientRemap.GradientMap",
                        "path": "data/hd/vfx/textures/mine/my_gradient.texture"},
                       {"op": "color", "stream": "Color", "rgba": [1, 0.5, 0.25, 1]}])
        self.assertEqual([a["op"] for a in r["applied"]], ["enable", "prop", "texture", "color"])
        self.assertEqual(r["session"]["version"], 1)
        self.assertTrue(r["validate"]["ok"])
        out = r["session"]["out"]
        ins = self.ok("fx_inspect", path=out)
        self.assertFalse(ins["renderers"][0]["enabled"])
        self.assertTrue(ins["renderers"][1]["enabled"])
        self.assertAlmostEqual(ins["renderers"][0]["props"]["Transparent.CameraSortOffset"]["value"], 2.5)
        self.assertEqual({x["props"]["GradientRemap.GradientMap"]["file"] for x in ins["renderers"]},
                         {"data/hd/vfx/textures/mine/my_gradient.texture"})
        self.assertEqual(ins["scripts"][0]["instructions"], 6)          # the tint added one `mul`
        self.assertEqual(self.file.read_bytes(), particle_file())       # the game file is never written
        self.assertEqual(self.ok("fx_edit", path=r["session"]["work"], ops=[{"op": "enable", "renderer": 1, "on": False}]
                                 )["session"]["version"], 2)            # the working file as path continues the session

    def test_const_script_and_replace(self):
        r = self.edit([{"op": "const", "script": 9, "index": 0, "values": [0.25]},
                       {"op": "texture", "replace": {thp.GRAD: "data/hd/vfx/textures/mine/g2.texture"}}])
        out = r["session"]["out"]
        text = self.ok("fx_script_read", path=out, script=9)["text"]
        self.assertIn(".const 0 f 0.25 0.25", text)
        self.assertIn("g2.texture", json.dumps(self.ok("fx_inspect", path=out)["renderers"]))
        i = self.edit([{"op": "const", "script": 9, "index": 0, "values": [7], "kind": "i"}])
        self.assertIn(".const 0 i 7 7", self.ok("fx_script_read", path=i["session"]["out"], script=9)["text"])

    def test_all_or_nothing_and_errors(self):
        self.edit([{"op": "enable", "renderer": "billboard", "on": False}])
        for ops, code, idx in [([{"op": "enable", "renderer": 0, "on": False}, {"op": "prop", "renderer": 0, "name": "Nope",
                                                                               "value": 1}], "edit_failed", 1),
                               ([{"op": "enable", "renderer": 7, "on": True}], "edit_failed", 0),
                               ([{"op": "color", "stream": "Nope", "rgba": [1, 1, 1]}], "edit_failed", 0),
                               ([{"op": "const", "script": 9, "index": 5, "values": [1]}], "edit_failed", 0),
                               ([{"op": "const", "script": 3, "index": 0, "values": [1]}], "edit_failed", 0),
                               ([{"op": "bogus"}], "edit_failed", 0), ([], "bad_args", None)]:
            e = self.err("fx_edit", code, path=LOGICAL, roots=self.roots, ops=ops)
            self.assertEqual(e.get("op_index"), idx, e)
        out = str(next((self.out / "edit").iterdir()))
        st = self.ok("fx_session", out=out)["session"]
        self.assertEqual(st["version"], 1)                               # none of the failures saved a version
        for op in ("curve", "copy_layer", "remove_layer"):
            if api.lane("graph") is None:
                e = self.err("fx_edit", "not_implemented", path=LOGICAL, roots=self.roots, ops=[{"op": op, "layer": "a"}])
                self.assertEqual(e["needs"], "lane format")

    def test_undo_redo_history(self):
        self.edit([{"op": "enable", "renderer": 0, "on": False}])
        r = self.edit([{"op": "enable", "renderer": 1, "on": False}])
        out = r["session"]["out"]
        self.assertEqual((r["session"]["version"], r["session"]["can_undo"], r["session"]["can_redo"]), (2, True, False))
        s = self.ok("fx_session", out=out, action="undo")["session"]
        self.assertEqual((s["version"], s["can_redo"]), (1, True))
        ins = self.ok("fx_inspect", path=out)
        self.assertEqual([x["enabled"] for x in ins["renderers"]], [False, True])
        self.assertEqual(self.ok("fx_session", out=out, action="redo")["session"]["version"], 2)
        self.ok("fx_session", out=out, action="undo")
        r = self.edit([{"op": "enable", "renderer": 0, "on": True}])     # a new edit drops the redo version
        self.assertEqual((r["session"]["version"], r["session"]["versions"], r["session"]["can_redo"]), (2, 3, False))
        h = self.ok("fx_session", out=out, action="history")["history"]
        self.assertEqual([x["version"] for x in h], [0, 1, 2])
        self.assertEqual(h[2]["current"], True)
        self.assertEqual(self.ok("fx_session", out=out, action="reset")["session"]["version"], 0)
        self.assertEqual((self.out / "edit").iterdir().__next__().joinpath("fx_test.particles").read_bytes(),
                         particle_file())
        self.err("fx_session", "bad_args", out=out, action="undo")
        self.err("fx_session", "bad_args", out=out, action="goto", version=9)
        self.err("fx_session", "bad_args", out=out, action="dance")
        self.err("fx_session", "not_found", out=str(self.d))

    def test_session_is_per_source_and_out(self):
        a = self.edit([{"op": "enable", "renderer": 0, "on": False}])["session"]
        b = self.edit([{"op": "enable", "renderer": 1, "on": False}])["session"]
        self.assertEqual((a["out"], b["version"]), (b["out"], 2))        # same source -> the same working copy
        mine = str(self.d / "mine")
        c = self.ok("fx_edit", path=LOGICAL, roots=self.roots, out=mine, ops=[{"op": "enable", "renderer": 0, "on": False}])
        self.assertEqual((c["session"]["out"], c["session"]["version"]), (mine, 1))
        other = self.d / "other.particles"
        other.write_bytes(particle_file())
        e = self.err("fx_edit", "bad_args", path=str(other), out=mine, ops=[{"op": "enable", "renderer": 0, "on": False}])
        self.assertIn("holds the session", e["message"])


class Scripts(Base):
    def test_list_read_write(self):
        out = self.ok("fx_edit", path=LOGICAL, roots=self.roots, ops=[{"op": "enable", "renderer": 0, "on": True}])["session"]["out"]
        self.assertEqual(self.ok("fx_script_read", path=out)["scripts"][0]["script"], 9)
        r = self.ok("fx_script_read", path=out, script=9)
        self.assertEqual((r["language"], r["instructions"], r["externals"]), ("cbvm", 5, ["Color", "Age"]))
        w = self.ok("fx_script_write", path=out, script=9, source=r["text"])     # an unchanged listing: identical bytes
        self.assertEqual(w["session"]["version"], 2)
        self.assertEqual(w["language"], "cbvm")
        v1, v2 = (Path(out) / "history" / ("v%04d.particles" % v) for v in (1, 2))
        self.assertEqual(v1.read_bytes(), v2.read_bytes())
        new = r["text"].replace(".const 0 f 0.5 0.5 0.5 0.5 0.5 0.5 0.5 0.5", ".const 0 f 0.75")
        self.assertNotEqual(new, r["text"])
        self.ok("fx_script_write", path=out, script=9, source=new)
        self.assertIn(".const 0 f 0.75", self.ok("fx_script_read", path=out, script=9)["text"])

    def test_bad_text_changes_nothing(self):
        out = self.ok("fx_edit", path=LOGICAL, roots=self.roots, ops=[{"op": "enable", "renderer": 0, "on": True}])["session"]["out"]
        before = self.ok("fx_session", out=out)["session"]["version"]
        for text, code in [("garbage line\n", "parse_error"), ("", "edit_failed"), ("   ", "edit_failed"),
                           ("mul s0.float, s0.float, c0.float\n", "parse_error")]:     # no .backend / .order directives
            e = self.err("fx_script_write", code, path=out, script=9, source=text)
            self.assertEqual(e["op_index"], 0)
        self.assertEqual(self.ok("fx_session", out=out)["session"]["version"], before)
        self.err("fx_script_read", "not_found", path=out, script=3)
        self.err("fx_script_read", "bad_args", path=out, script=9, language="java")

    @unittest.skipIf(api.lane("lang"), "lane lang is present")
    def test_hfx_needs_lane_lang(self):
        self.ok("fx_edit", path=LOGICAL, roots=self.roots, ops=[{"op": "enable", "renderer": 0, "on": True}])
        out = str(next((self.out / "edit").iterdir()))
        self.assertEqual(self.err("fx_script_read", "not_implemented", path=out, script=9, language="hfx")["needs"], "lane lang")
        e = self.err("fx_script_write", "not_implemented", path=out, script=9, source="effect x {}", language="hfx")
        self.assertEqual(e["needs"], "lane lang")
        self.assertEqual(api.detect_language("effect x {}"), "cbvm")      # without the lang lane the listing is the default


class Creating(Base):
    def test_template_and_layers(self):
        r = self.ok("fx_create", template=LOGICAL, roots=self.roots, name="My Fx!",
                    textures={thp.GRAD: "data/hd/vfx/textures/mine/g.texture"})
        self.assertEqual(r["logical"], "hd/vfx/particles/custom/my_fx_/my_fx_.particles")
        self.assertEqual(r["session"]["source"], "new:%s" % LOGICAL)
        ins = self.ok("fx_inspect", path=r["session"]["out"])
        self.assertIn("g.texture", json.dumps(ins["renderers"]))
        self.err("fx_create", "bad_args", template=LOGICAL, roots=self.roots, name="My Fx!", out=r["session"]["out"])
        if api.lane("graph") is not None:   # template + layers composes; the logical `from` resolves over the roots
            self.err("fx_create", "edit_failed", template=LOGICAL, layers=[{"from": LOGICAL, "layer": "nope"}],
                     roots=self.roots, name="compose")
        if api.lane("graph") is None:
            e = self.err("fx_create", "not_implemented", layers=[{"from": LOGICAL, "layer": "a", "as": "b"}], roots=self.roots)
            self.assertEqual(e["needs"], "lane format")
            self.err("fx_create", "not_implemented")
        # an exported new effect lands at its logical path
        mod = self.d / "mod"
        mod.mkdir()
        w = self.ok("fx_export", path=r["session"]["out"], mod_dir=str(mod), roots=self.roots)
        self.assertTrue((mod / "data" / r["logical"]).is_file())
        self.assertEqual(w["written"][0]["logical"], r["logical"])


class Exporting(Base):
    def test_export(self):
        out = self.ok("fx_edit", path=LOGICAL, roots=self.roots, ops=[{"op": "enable", "renderer": 0, "on": False}]
                      )["session"]["out"]
        mod = self.d / "mymod"
        tex = self.d / "t.texture"
        tex.write_bytes(b"tex")
        mod.mkdir()
        r = self.ok("fx_export", path=out, mod_dir=str(mod), roots=self.roots,
                    extras=[{"src": str(tex), "logical": "data/hd/vfx/textures/mine/my_gradient.texture"}])
        f = mod / "data" / LOGICAL
        self.assertEqual(json.loads(json.dumps(r["written"][0]))["size"], f.stat().st_size)
        self.assertEqual(hp.ParticleFile(f.read_bytes()).renderers()[0].enabled, False)
        self.assertEqual((mod / "data/hd/vfx/textures/mine/my_gradient.texture").read_bytes(), b"tex")
        self.assertTrue(r["check"]["ok"])
        self.assertEqual(sorted(r["missing"]), sorted([thp.GRAD, thp.MESH]))     # not in the mod, not in the roots
        data_dir = self.d / "data"                                             # a mod_dir named data is used as is
        data_dir.mkdir()
        self.ok("fx_export", path=out, mod_dir=str(data_dir))
        self.assertTrue((data_dir / LOGICAL).is_file())
        # refusals
        self.err("fx_export", "bad_args", path=out, mod_dir=str(self.root), roots=self.roots)             # a game root
        self.err("fx_export", "bad_args", path=out, mod_dir=str(self.root / "hd"), roots=self.roots)      # inside one
        self.err("fx_export", "not_found", path=out, mod_dir=str(self.d / "nowhere"))
        self.err("fx_export", "bad_args", path=out, mod_dir=str(mod), logical="../escape.particles")
        self.err("fx_export", "bad_args", path=out, mod_dir=str(mod), logical="hd/../../x")
        loose = self.d / "loose.particles"
        loose.write_bytes(particle_file())
        self.err("fx_export", "bad_args", path=str(loose), mod_dir=str(mod))                              # no logical path
        self.ok("fx_export", path=str(loose), mod_dir=str(mod), logical="hd/vfx/particles/x/loose.particles")


class FakeSkeleton:
    names = ["root", "Hand"]
    idx = {"root": 0, "Hand": 1}
    parent = [-1, 0]

    def worlds(self, loc):
        return np.stack(loc)

    def rest_locals(self):
        m = np.eye(4)
        m[1, 3] = 1.0
        return [np.eye(4), m]


class FakeUnit:
    class P:
        skeleton = FakeSkeleton()
    parts = [P()]
    root_scale = 1.0


class FakeLanes(Base):
    """fx_render's orchestration against stand-ins for lanes graph, vm and render; the context lane is the real one."""

    def setUp(self):
        super().setUp()
        self.calls = []
        calls = self.calls

        class Effect:
            pass

        class Sim:
            stats = {"stub_calls": {"x": 1}}
            ctx = None

            def __init__(self, effect, seed=0, attrs=None, ctx=None):
                calls.append(("sim", seed, attrs, ctx))
                self.t, self.ctx = 0.0, ctx

            def seek(self, t):
                calls.append(("seek", t))
                self.t = t

            def step(self, dt):
                calls.append(("step", round(dt, 4)))
                if self.ctx is not None:
                    calls.append(("ctx", self.ctx.transform[:3, 3].round(3).tolist()))
                self.t += dt

            def state(self):
                return {"t": round(self.t, 6)}

        class Fxr:
            def __init__(self, gpu, vfs):
                calls.append(("fxr", gpu))

            def prepare(self, effect, state, cam):
                return [state["t"]]

            def render(self, draws, cam, size, **kw):
                calls.append(("render", draws, size, sorted(kw)))
                img = np.zeros((size[1], size[0], 4), np.uint8)
                img[..., 0] = int(min(draws[0], 1.0) * 255)
                img[..., 3] = 255
                return img

        mods = {"graph": mock.Mock(load=lambda data, vfs=None: Effect(), OPS=()),
                "sim": mock.Mock(Sim=Sim),
                "render": mock.Mock(FxRenderer=Fxr, camera_from_spec=lambda spec, size, st: calls.append(("cam", spec)) or "cam",
                                    spec=["FxRenderer", "camera_from_spec"])}
        real = api.lane
        for target, new in ((api, {"lane": lambda n: mods[n] if n in mods else real(n), "gate": lambda w=0.0: None,
                                   "gpu_renderer": lambda: "gpu", "_unit": lambda fs, spec: FakeUnit()}),):
            for k, v in new.items():
                p = mock.patch.object(target, k, v)
                p.start()
                self.addCleanup(p.stop)

    def test_still_and_clip(self):
        r = self.ok("fx_render", path=LOGICAL, roots=self.roots, t=0.4, size="64x48", bg="transparent")
        self.assertEqual((r["frames"], r["size"], r["t"]), (1, [64, 48], [0.4]))
        self.assertTrue(r["files"][0].endswith(".png"))
        with Image.open(r["files"][0]) as im:
            self.assertEqual((im.size, im.mode, im.getpixel((3, 3))), ((64, 48), "RGBA", (102, 0, 0, 255)))
        self.assertEqual([c for c in self.calls if c[0] == "seek"], [("seek", 0.0)])
        self.assertEqual(len([c for c in self.calls if c[0] == "step"]), 10)         # 0.4 s at 25 fps
        self.assertEqual([c for c in self.calls if c[0] == "cam"], [("cam", {})])    # one camera
        r = self.ok("fx_render", path=LOGICAL, roots=self.roots, clip={"t0": 0, "t1": 0.4, "fps": 10}, size="32x32", seed=7,
                    attrs={"MonsterFade": 1})
        self.assertEqual((r["frames"], len(r["files"])), (4, 4))
        self.assertEqual([c for c in self.calls if c[0] == "sim"][-1], ("sim", 7, {"MonsterFade": 1}, None))
        self.assertEqual([c[1] for c in self.calls if c[0] == "step"][-3:], [0.1, 0.1, 0.1])
        self.assertEqual([c for c in self.calls if c[0] == "cam"][-1], ("cam", {}))
        r = self.ok("fx_render", path=LOGICAL, roots=self.roots, clip={"t0": 0, "t1": 0.4, "fps": 10}, size="32x32",
                    format="gif")
        with Image.open(r["files"][0]) as gif:
            self.assertEqual(gif.n_frames, 4)

    def test_missile_context_is_driven_by_the_context_lane(self):
        r = self.ok("fx_render", path=LOGICAL, roots=self.roots, size="32x32", t=0.2,
                    context={"kind": "missile", "path": [[0, 0, 0], [0, 0, 20]], "speed": 10})
        zs = [c[1][2] for c in self.calls if c[0] == "ctx"]
        self.assertEqual(len(zs), 6)                                            # the start frame and 5 steps of 1/25 s
        self.assertTrue(all(b > a for a, b in zip(zs, zs[1:])), zs)            # the effect flies along +z
        self.assertAlmostEqual(zs[-1], 2.0, places=1)                            # 10 units/s for 0.2 s
        self.assertEqual(r["context"]["kind"], "missile")

    def test_bone_context_follows_the_bone(self):
        self.ok("fx_render", path=LOGICAL, roots=self.roots, size="32x32", t=0.08,
                context={"kind": "bone", "unit": "hd/character/x.json", "bone": "Hand"})
        self.assertEqual([c[1] for c in self.calls if c[0] == "ctx"][0], [0.0, 1.0, 0.0])     # the rest pose of Hand
        self.err("fx_render", "bad_args", path=LOGICAL, roots=self.roots, size="32x32",
                 context={"kind": "bone", "unit": "u", "bone": "Elbow"})            # a bone the skeleton lacks
        self.err("fx_render", "bad_args", path=LOGICAL, roots=self.roots, size="32x32", context={"kind": "bone", "unit": "u"})

    def test_argument_errors(self):
        self.err("fx_render", "bad_args", path=LOGICAL, roots=self.roots, context={"kind": "weather"})
        self.err("fx_render", "bad_args", path=LOGICAL, roots=self.roots, context="bone")
        self.err("fx_render", "bad_args", path=LOGICAL, roots=self.roots, size="9x9")
        self.err("fx_render", "bad_args", path=LOGICAL, roots=self.roots, format="avi")
        self.err("fx_render", "bad_args", path=LOGICAL, roots=self.roots, clip={"t0": 3, "t1": 1})
        self.err("fx_render", "bad_args", path=LOGICAL, roots=self.roots, bg="red")

    def test_gpu_gate_refuses(self):
        refusal = {"ok": False, "refused": True, "why": "lock held: game.lock.2"}
        with mock.patch.object(api, "gate", lambda w=0.0: refusal):
            r = api.call("fx_render", path=LOGICAL, roots=self.roots)
        self.assertEqual((r["refused"], r["why"]), (True, "lock held: game.lock.2"))
        self.assertNotIn(("fxr", "gpu"), self.calls)                     # no GPU device was asked for


class Seams(Base):
    @unittest.skipIf(api.lane("sim") or api.lane("render"), "lanes vm and render are present")
    def test_render_needs_lanes(self):
        e = self.err("fx_render", "not_implemented", path=LOGICAL, roots=self.roots)
        self.assertIn("needs", e)
        self.assertTrue(e["needs"].startswith("lane "))

    def test_call_wraps_failures(self):
        with mock.patch.dict(api.TOOLS, {"fx_boom": mock.Mock(side_effect=NotImplementedError("x needs lane vm")),
                                         "fx_crash": mock.Mock(side_effect=RuntimeError("bad")),
                                         "fx_gone": mock.Mock(side_effect=FileNotFoundError("nope"))}):
            self.assertEqual(api.call("fx_boom")["error"]["needs"], "lane vm")
            self.assertEqual(api.call("fx_crash")["error"]["code"], "error")
            self.assertEqual(api.call("fx_gone")["error"]["code"], "not_found")

    def test_schemas_and_doc_agree(self):
        d = api.describe()
        self.assertEqual(sorted(d), sorted(api.TOOLS))
        self.assertEqual(d["fx_edit"]["input"]["required"], ["path", "ops"])
        self.assertEqual(d["fx_edit"]["input"]["properties"]["ops"], {"type": "array", "items": {"type": "object"}})
        self.assertEqual(d["fx_render"]["input"]["properties"]["t"]["default"], 1.0)
        json.dumps(d)
        doc = (TOOLS / "docs" / "hdfx-mcp.md").read_text("utf-8")
        blocks = [set(json.loads(b)["properties"]) for b in re.findall(r"```json input\n(.*?)\n```", doc, re.S)]
        for name, fn in api.TOOLS.items():
            self.assertIn(name, doc)
            import inspect
            self.assertIn(set(inspect.signature(fn).parameters), blocks, "%s: the doc's input schema differs" % name)
        outs = [json.loads(b) for b in re.findall(r"```json output\n(.*?)\n```", doc, re.S)]
        self.assertEqual(len(outs), len(blocks))                          # every input schema has its output schema
        self.assertTrue(all("properties" in o for o in outs))


class WebEndpoints(Base):
    def setUp(self):
        super().setUp()
        from hdkit.view import webserve
        self.web = webserve
        self.srv = webserve.make_server(None, 0, fx=True, fx_out=str(self.out), fx_roots=self.roots)
        t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        t.start()
        self.addCleanup(lambda: (self.srv.shutdown(), self.srv.server_close(), t.join(5)))
        self.port = self.srv.server_address[1]

    def req(self, method, path, body=None, headers=None, raw=False):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Content-Type": "application/json", **(headers or {})}
        c.request(method, path, None if body is None else json.dumps(body), h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, (data if raw else json.loads(data))

    def test_tools_and_post(self):
        st, d = self.req("GET", "/api/fx/tools")
        self.assertEqual((st, sorted(d["tools"])), (200, sorted(api.TOOLS)))
        self.assertIn("graph", d["engine"])
        st, d = self.req("POST", "/api/fx/fx_list", {"pattern": "fx_test"})          # default roots from --fx-roots
        self.assertEqual((st, d["count"]), (200, 1))
        st, d = self.req("POST", "/api/fx/fx_edit", {"path": LOGICAL, "ops": [{"op": "enable", "renderer": 0, "on": False}]})
        self.assertEqual((st, d["ok"], d["session"]["version"]), (200, True, 1))
        st, d = self.req("POST", "/api/fx/fx_session", {"out": d["session"]["out"], "action": "undo"})
        self.assertEqual((st, d["session"]["version"]), (200, 0))
        st, d = self.req("POST", "/api/fx/fx_inspect", {"path": "hd/nope"})        # a tool failure is HTTP 200, ok false
        self.assertEqual((st, d["error"]["code"]), (200, "not_found"))
        st, d = self.req("POST", "/api/fx/fx_inspect", {"nope": 1})
        self.assertEqual((st, d["error"]["code"]), (200, "bad_args"))

    def test_bad_requests_and_guards(self):
        self.assertEqual(self.req("POST", "/api/fx/nope", {})[0], 400)
        self.assertEqual(self.req("POST", "/api/fx/fx_list", [1])[0], 400)
        self.assertEqual(self.req("POST", "/api/fx/fx_list", {}, {"Content-Type": "text/plain"})[0], 400)
        self.assertEqual(self.req("POST", "/api/fx/fx_list", {}, {"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.req("POST", "/api/fx/fx_list", {}, {"Host": "evil.example"})[0], 403)
        self.assertEqual(self.req("POST", "/api/fx/fx_list", {}, {"Origin": "http://localhost:8765"})[0], 200)
        st, d = self.req("POST", "/api/fx/fx_edit", {"path": LOGICAL, "out": str(self.d / "elsewhere"),
                                                     "ops": [{"op": "enable", "renderer": 0, "on": False}]})
        self.assertEqual(st, 403)                                                    # out must be below the fx folder
        self.assertFalse((self.d / "elsewhere").exists())
        self.assertEqual(self.req("GET", "/api/fx/nope")[0], 404)

    def test_file_endpoint_serves_only_the_output_folder(self):
        st, d = self.req("POST", "/api/fx/fx_edit", {"path": LOGICAL, "ops": [{"op": "enable", "renderer": 0, "on": False}]})
        work = d["session"]["work"]
        st, body = self.req("GET", "/api/fx/file?path=%s" % work, raw=True)
        self.assertEqual((st, body[:4]), (200, struct.pack("<I", hp.MAGIC)))
        self.assertEqual(self.req("GET", "/api/fx/file?path=%s" % self.file)[0], 403)
        self.assertEqual(self.req("GET", "/api/fx/file?path=%s/../game/%s" % (self.out, LOGICAL))[0], 403)
        self.assertEqual(self.req("GET", "/api/fx/file?path=%s/none.png" % self.out)[0], 404)

    def test_off_without_fx_and_no_bundle_leak(self):
        srv = self.web.make_server(None, 0)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
            c.request("GET", "/api/fx/tools")
            self.assertEqual(c.getresponse().status, 403)
            c.close()
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
            c.request("GET", "/bundle/scene.json")
            self.assertEqual(c.getresponse().status, 404)
            c.close()
        finally:
            srv.shutdown()
            srv.server_close()
            t.join(5)


@unittest.skipUnless(HAVE_MCP, "needs the mcp package (official Python SDK) in this python")
class McpWrappers(Base):
    def setUp(self):
        super().setUp()
        from hdkit.view import mcp_server
        self.m = mcp_server
        self.srv = mcp_server.build()

    def call(self, name, args):
        r = asyncio.run(self.srv.call_tool(name, args))
        blocks = r[0] if isinstance(r, tuple) else r
        return json.loads(next(b.text for b in blocks if b.type == "text")), [b for b in blocks if b.type == "image"]

    def test_tools_listed_and_match_the_api(self):
        tools = {t.name: t for t in asyncio.run(self.srv.list_tools())}
        self.assertEqual(sorted(tools), sorted(self.m.TOOL_NAMES))
        for name, fn in api.TOOLS.items():
            self.assertEqual(set(tools[name].inputSchema["properties"]), set(api.schema_of(fn)["properties"]), name)

    def test_tools_run(self):
        r, _ = self.call("fx_list", {"roots": self.roots, "pattern": "fx_test"})
        self.assertEqual(r["count"], 1)
        r, _ = self.call("fx_edit", {"path": LOGICAL, "roots": self.roots, "ops": [{"op": "enable", "renderer": 0, "on": False}]})
        self.assertEqual(r["session"]["version"], 1)
        r, _ = self.call("fx_edit", {"path": LOGICAL, "roots": self.roots, "ops": [{"op": "zap"}]})
        self.assertEqual((r["ok"], r["error"]["code"]), (False, "edit_failed"))
        r, imgs = self.call("fx_render", {"path": LOGICAL, "roots": self.roots})
        if r["ok"]:                     # renderer merged and the gate free: a picture comes back (at most one inline)
            self.assertLessEqual(len(imgs), 1)
        else:                           # gate busy, no GPU, or a lane missing
            self.assertEqual(imgs, [])
            self.assertTrue(r.get("refused") or r["error"]["code"] in ("not_implemented", "render_failed"), r)

    def test_render_shares_the_gpu_device(self):
        self.assertIs(api.GPU_PROVIDER, self.m._gpu_renderer)


@unittest.skipUnless(HAVE_MCP, "needs the mcp package (official Python SDK) in this python")
class McpStdio(Base):
    def test_end_to_end(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        env = {**os.environ, "HDKIT_VIEW_ROOTS": self.roots[0], "HDKIT_FX_OUT": str(self.out), "PYTHONPATH": str(TOOLS)}
        params = StdioServerParameters(command=sys.executable, args=["-m", "hdkit.view.mcp_server"], cwd=str(TOOLS), env=env)

        async def run():
            async with stdio_client(params) as (rd, wr):
                async with ClientSession(rd, wr) as s:
                    await s.initialize()
                    names = {t.name for t in (await s.list_tools()).tools}
                    jr = lambda res: json.loads(res.content[0].text)  # noqa: E731
                    lst = jr(await s.call_tool("fx_list", {"pattern": "fx_test"}))      # roots from HDKIT_VIEW_ROOTS
                    ed = jr(await s.call_tool("fx_edit", {"path": LOGICAL, "ops": [
                        {"op": "enable", "renderer": "mesh", "on": False}, {"op": "color", "stream": "Color", "rgba": [1, 0, 0]}]}))
                    out = ed["session"]["out"]
                    sc = jr(await s.call_tool("fx_script_read", {"path": out, "script": 9}))
                    un = jr(await s.call_tool("fx_session", {"out": out, "action": "undo"}))
                    mod = self.d / "mod"
                    mod.mkdir()
                    ex = jr(await s.call_tool("fx_export", {"path": out, "mod_dir": str(mod)}))
                    cov = jr(await s.call_tool("fx_coverage", {"summary": True}))
                    return names, lst, ed, sc, un, ex, cov
        names, lst, ed, sc, un, ex, cov = asyncio.run(run())
        self.assertTrue({"fx_list", "fx_edit", "fx_render", "fx_export", "view_render"} <= names)
        self.assertEqual(lst["count"], 1)
        self.assertEqual((ed["ok"], ed["session"]["version"], [a["op"] for a in ed["applied"]]), (True, 1, ["enable", "color"]))
        self.assertIn("mul", sc["text"])
        self.assertEqual(un["session"]["version"], 0)
        self.assertTrue(ex["ok"])
        self.assertTrue((self.d / "mod" / "data" / LOGICAL).is_file())
        self.assertEqual(cov["count"], 1)


if __name__ == "__main__":
    unittest.main()
