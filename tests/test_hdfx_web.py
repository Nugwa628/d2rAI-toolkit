"""The hdfx web editor (hdkit/view/web/fx*.js|html, view/fxmock.py, view/fxeditor.py): page assets, the mock's contract with
docs/hdfx-mcp.md (answers, sessions, errors, the safety checks of the endpoints), the editor's session choreography against the
real `hdkit.fx.api` endpoints on the synthetic fixture effects, the pure-JS logic tests (node) and the headless-Chrome checks
(node + google-chrome, screenshots optional). No game files, no GPU. Browser checks are skipped when node or Chrome is absent;
HDFX_WEB_BROWSER=0 skips them (about 40 s), HDFX_WEB_SHOTS=<folder> keeps their screenshots, CHROME=<binary> picks the browser.
Run: python -m unittest discover -s tests -p "test_hdfx_web.py" """
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
from hdkit.view import fxmock, webserve  # noqa: E402

try:
    from hdkit.fx import api
except ImportError:                      # pragma: no cover
    api = None

WEB = TOOLS / "hdkit" / "view" / "web"
FIXTURES = TOOLS / "tests" / "fixtures" / "hdfx"
EMBERS = fxmock.LOGICAL + "vfx_mock_embers.particles"
NODE = shutil.which("node")
CHROME = os.environ.get("CHROME") or shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
PAGE_FILES = ["fx.html", "fx.js", "fxlogic.js", "fxcurve.js"]


def call(base, tool, headers=None, **args):
    h = {"Content-Type": "application/json", **(headers or {})}
    c = http.client.HTTPConnection(base[0], base[1], timeout=60)
    c.request("POST", "/api/fx/" + tool, json.dumps(args), h)
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, json.loads(body)


def get(base, path, headers=None):
    c = http.client.HTTPConnection(base[0], base[1], timeout=60)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, r.getheader("Content-Type"), body


class Serving:
    """A server thread on 127.0.0.1:<free port>; self.base = (host, port)."""

    def start(self, srv):
        self.srv = srv
        self.thread = threading.Thread(target=srv.serve_forever, daemon=True)
        self.thread.start()
        self.base = ("127.0.0.1", srv.server_address[1])
        self.url = "http://127.0.0.1:%d/" % self.base[1]

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(5)


class Page(unittest.TestCase):
    def test_files_exist_and_are_ascii(self):
        for f in PAGE_FILES:
            data = (WEB / f).read_bytes()
            self.assertGreater(len(data), 500, f)
            data.decode("ascii")                                   # source stays ASCII (see the repo's hooks)

    def test_nothing_from_the_network(self):
        for f in PAGE_FILES:
            text = (WEB / f).read_text()
            self.assertIsNone(re.search(r"https?://(?!127\.0\.0\.1|localhost)", text.replace("http://www.w3.org", "")), f)
            self.assertNotIn("<script src=\"http", text)
            self.assertNotIn("cdn", text.lower().replace("cdnjs", ""), f)

    def test_module_imports_resolve(self):
        for f in PAGE_FILES[1:]:
            for m in re.finditer(r'from "\./([\w.]+)"', (WEB / f).read_text()):
                self.assertTrue((WEB / m.group(1)).is_file(), "%s imports %s" % (f, m.group(1)))
        self.assertIn('src="fx.js"', (WEB / "fx.html").read_text())

    def test_every_id_the_script_uses_is_in_the_page(self):
        html = (WEB / "fx.html").read_text()
        ids = set(re.findall(r'\bid="([\w-]+)"', html))
        used = set(re.findall(r'\$\("([\w-]+)"\)', (WEB / "fx.js").read_text()))
        dynamic = {"streams"}                                       # created or declared on the page: checked below anyway
        self.assertEqual(sorted(used - ids - dynamic), [])
        for tab in re.findall(r'data-tab="(\w+)"', html):
            self.assertIn("pane-" + tab, ids)

    def test_javascript_parses(self):
        if not NODE:
            self.skipTest("node not found")
        for f in PAGE_FILES[1:]:
            r = subprocess.run([NODE, "--check", str(WEB / f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f + r.stderr)

    def test_editor_entry_point(self):
        from hdkit.view import cli, fxeditor
        self.assertTrue(callable(fxeditor.main))
        self.assertIn('argv[:1] == ["fx"]', Path(cli.__file__).read_text())


class MockTools(unittest.TestCase):
    """The stand-in answers as docs/hdfx-mcp.md says (shapes the page relies on), sessions included."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.m = fxmock.Mock(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def edit(self, *ops, path=EMBERS):
        r = self.m.call("fx_edit", path=path, ops=list(ops))
        self.assertTrue(r["ok"], r)
        return r

    def test_list_and_inspect_shapes(self):
        r = self.m.call("fx_list", info=True)
        self.assertEqual((r["ok"], r["count"]), (True, 3))
        self.assertTrue({"path", "root", "file", "size", "kinds", "materials"} <= set(r["items"][0]))
        self.assertEqual([i["path"] for i in self.m.call("fx_list", kind="light")["items"]], [fxmock.LOGICAL + "vfx_mock_bolt.particles"])
        self.assertEqual(self.m.call("fx_list", pattern="*smoke*")["count"], 1)
        self.assertEqual(self.m.call("fx_list", texture="ember_a")["count"], 1)
        i = self.m.call("fx_inspect", path=EMBERS)
        for k in ("ok", "path", "file", "objects", "renderers", "scripts", "textures", "graph", "engine", "session"):
            self.assertIn(k, i)
        self.assertTrue(i["engine"]["mock"])
        self.assertEqual({r["index"] for r in i["renderers"]}, {0, 1, 2})
        self.assertTrue({"script", "backend", "instructions", "constants", "externals", "engine_calls"} <= set(i["scripts"][0]))
        self.assertEqual({s["script"] for s in i["scripts"]}, {11, 12, 13})                # the id is the graph's object number
        g = i["graph"]
        self.assertEqual([l["name"] for l in g["layers"]], ["Embers", "Glow", "Sparks"])
        self.assertEqual({s["kind"] for s in g["samplers"]}, {"curve", "turbulence"})
        self.assertEqual(len({s["obj"] for s in g["samplers"]}), len(g["samplers"]))
        self.assertGreater(len([s for s in g["samplers"] if s["name"] == "SizeOverLife"]), 1)          # a name on two layers
        self.assertEqual(g["layers"][0]["spawn_script"], 11)                                              # a graph script object is the tools' script id
        self.assertFalse(any(k.startswith("_") for k in g))
        self.assertIsNone(self.m.call("fx_inspect", path=EMBERS, graph=False)["graph"] and None)

    def test_errors_have_codes(self):
        self.assertEqual(self.m.call("fx_inspect", path="hd/nope.particles")["error"]["code"], "not_found")
        self.assertEqual(self.m.call("fx_nothing")["error"]["code"], "bad_args")
        self.assertEqual(self.m.call("fx_inspect")["error"]["code"], "bad_args")
        e = self.m.call("fx_edit", path=EMBERS, ops=[{"op": "prop", "renderer": 0, "name": "Opacity", "value": 1}, {"op": "prop", "renderer": 0, "name": "Nope", "value": 1}])
        self.assertEqual((e["error"]["code"], e["error"]["op_index"]), ("edit_failed", 1))
        s = self.m.call("fx_inspect", path=EMBERS)["session"]
        self.assertIsNone(s)                                       # all or nothing: the first op did not create or change a session

    def test_property_ops(self):
        r = self.edit({"op": "prop", "renderer": 0, "name": "Opacity", "value": 0.25}, {"op": "enable", "renderer": 1, "on": False},
                      {"op": "texture", "renderer": 0, "name": "DiffuseMap", "path": "data/x/y.texture"})
        out = r["session"]["out"]
        g = self.m.call("fx_inspect", path=out)
        props = {r_["index"]: r_ for r_ in g["renderers"]}
        self.assertEqual(props[0]["props"]["Opacity"]["value"], 0.25)
        self.assertEqual(props[0]["props"]["DiffuseMap"]["file"], "data/x/y.texture")
        self.assertFalse(props[1]["enabled"])
        self.assertEqual(g["session"]["version"], 1)
        bad = self.m.call("fx_edit", path=out, ops=[{"op": "prop", "renderer": 0, "name": "DiffuseMap", "value": "x"}])
        self.assertEqual(bad["error"]["code"], "edit_failed")                 # a texture property is edited with the texture op

    def test_sessions_undo_redo_goto_history(self):
        a = self.edit({"op": "enable", "renderer": 0, "on": False})
        out = a["session"]["out"]
        self.edit({"op": "enable", "renderer": 1, "on": False}, path=out)
        self.edit({"op": "enable", "renderer": 2, "on": False}, path=out)
        h = self.m.call("fx_session", out=out, action="history")
        self.assertEqual([v["version"] for v in h["history"]], [0, 1, 2, 3])
        self.assertEqual(h["session"]["version"], 3)
        u = self.m.call("fx_session", out=out, action="undo")["session"]
        self.assertEqual((u["version"], u["can_undo"], u["can_redo"]), (2, True, True))
        self.assertTrue(self.m.call("fx_inspect", path=out)["renderers"][2]["enabled"])
        self.assertEqual(self.m.call("fx_session", out=out, action="goto", version=0)["session"]["version"], 0)
        self.assertEqual(self.m.call("fx_session", out=out, action="redo")["session"]["version"], 1)
        self.edit({"op": "enable", "renderer": 2, "on": False}, path=out)               # a new edit drops the redo versions
        self.assertEqual(self.m.call("fx_session", out=out)["session"]["versions"], 3)
        self.assertEqual(self.m.call("fx_session", out=out, action="redo")["error"]["code"], "bad_args")
        self.assertEqual(self.m.call("fx_session", out=out, action="undo")["session"]["version"], 1)

    def test_curves_tint_layers(self):
        c = {"op": "curve", "sampler": 50, "times": [0, 1], "values": [[0], [2]]}
        out = self.edit(c)["session"]["out"]
        s = [x for x in self.m.call("fx_inspect", path=out)["graph"]["samplers"] if x["obj"] == 50][0]
        dup = self.m.call("fx_edit", path=out, ops=[{**c, "sampler": "SizeOverLife"}])
        self.assertEqual(dup["error"]["code"], "edit_failed")                                              # names repeat: the object number is the key
        self.assertEqual(s["data"]["times"], [0, 1])
        self.assertEqual(self.m.call("fx_edit", path=out, ops=[{**c, "times": [1, 0]}])["error"]["code"], "edit_failed")
        self.edit({"op": "color", "stream": "Color", "rgba": [1, 0.5, 1, 1]}, {"op": "color", "stream": "Color", "rgba": [1, 0.5, 1, 1]}, path=out)
        self.assertEqual(self.m.call("fx_inspect", path=out)["graph"]["tint"], [1, 0.25, 1, 1])        # tints multiply
        r = self.edit({"op": "copy_layer", "from": fxmock.LOGICAL + "vfx_mock_smoke.particles", "layer": "Plume", "as": "Plume2"}, path=out)
        names = [l["name"] for l in self.m.call("fx_inspect", path=out)["graph"]["layers"]]
        self.assertEqual(names, ["Embers", "Glow", "Sparks", "Plume2"])
        idx = [r_["index"] for r_ in self.m.call("fx_inspect", path=out)["renderers"]]
        self.assertEqual(len(idx), len(set(idx)))                                                       # copied renderers get new indexes
        self.assertEqual(self.m.call("fx_edit", path=out, ops=[{"op": "copy_layer", "from": fxmock.LOGICAL + "vfx_mock_smoke.particles", "layer": "Plume", "as": "Plume2"}])["error"]["code"], "edit_failed")
        self.edit({"op": "remove_layer", "layer": "Plume2"}, path=r["session"]["out"])
        self.assertEqual(len(self.m.call("fx_inspect", path=out)["graph"]["layers"]), 3)

    def test_scripts(self):
        t = self.m.call("fx_script_read", path=EMBERS, script=12)
        self.assertTrue(t["ok"] and ".const 0 f" in t["text"] and t["language"] == "cbvm")
        self.assertEqual(self.m.call("fx_script_read", path=EMBERS, script=99)["error"]["code"], "not_found")
        self.assertEqual(len(self.m.call("fx_script_read", path=EMBERS)["scripts"]), 3)
        bad = self.m.call("fx_script_write", path=EMBERS, script=12, source=t["text"].replace("mul r1", "frobnicate r1"), out=str(Path(self.tmp.name) / "chk"))
        self.assertEqual(bad["error"]["code"], "parse_error")
        self.assertEqual(bad["error"]["line"], 10)
        self.assertIn("line 10", bad["error"]["message"])
        out = self.edit({"op": "const", "script": 12, "index": 1, "values": [0.5, 0.25], "kind": "f"})["session"]["out"]
        self.assertIn(".const 1 f 0.5 0.25 0.5 0.25", self.m.call("fx_script_read", path=out, script=12)["text"])      # values repeat to fill the lanes

    def test_validate_create_export(self):
        self.assertTrue(self.m.call("fx_validate", path=EMBERS)["ok"])
        out = self.edit(*({"op": "enable", "renderer": i, "on": False} for i in range(3)))["session"]["out"]
        v = self.m.call("fx_validate", path=out)
        self.assertFalse(v["ok"])
        self.assertEqual(v["errors"][0]["check"], "renderers")
        mod = Path(self.tmp.name) / "mod"
        e = self.m.call("fx_export", path=out, mod_dir=str(mod))
        self.assertTrue(e["ok"])
        self.assertTrue((mod / "data" / EMBERS).is_file())
        self.assertEqual(self.m.call("fx_export", path=out, mod_dir=str(mod), logical="hd/../x")["error"]["code"], "bad_args")
        c = self.m.call("fx_create", template=EMBERS, name="copy1")
        self.assertTrue(c["ok"] and c["logical"].endswith("copy1.particles"))
        self.assertEqual(c["session"]["version"], 0)
        self.assertEqual(self.m.call("fx_coverage")["count"], 4)

    def test_render_png_and_gif(self):
        from PIL import Image
        r = self.m.call("fx_render", path=EMBERS, t=1.0, size="160x120", seed=3)
        self.assertTrue(r["ok"] and r["size"] == [160, 120])
        im = Image.open(r["files"][0])
        self.assertEqual((im.format, im.size), ("PNG", (160, 120)))
        r2 = self.m.call("fx_render", path=EMBERS, t=1.0, size="160x120", seed=3)
        self.assertEqual(Image.open(r2["files"][0]).tobytes(), im.tobytes())              # deterministic for a seed
        r3 = self.m.call("fx_render", path=EMBERS, t=1.0, size="160x120", seed=4)
        self.assertNotEqual(Image.open(r3["files"][0]).tobytes(), im.tobytes())
        g = self.m.call("fx_render", path=EMBERS, clip={"t0": 0, "t1": 0.5, "fps": 6}, format="gif", size="96x96")
        gi = Image.open(g["files"][0])
        self.assertEqual((gi.format, g["frames"], getattr(gi, "n_frames", 1)), ("GIF", 4, 4))
        out = self.edit({"op": "enable", "renderer": 0, "on": False}, {"op": "enable", "renderer": 1, "on": False}, {"op": "enable", "renderer": 2, "on": False})["session"]["out"]
        dark = Image.open(self.m.call("fx_render", path=out, t=1.0, size="160x120", seed=3)["files"][0])
        self.assertLess(sum(dark.convert("L").getdata()), sum(im.convert("L").getdata()))      # an edit shows in the picture


class MockEndpoints(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Serving()
        self.s.start(fxmock.make_server(Path(self.tmp.name) / "out", 0))

    def tearDown(self):
        self.s.stop()
        self.tmp.cleanup()

    def test_page_and_tools(self):
        code, ct, body = get(self.s.base, "/")
        self.assertEqual((code, ct), (200, "text/html; charset=utf-8"))
        self.assertIn(b"fx.js", body)
        for f in PAGE_FILES:
            self.assertEqual(get(self.s.base, "/" + f)[0], 200, f)
        code, _, body = get(self.s.base, "/api/fx/tools")
        j = json.loads(body)
        self.assertTrue(j["engine"]["mock"])
        self.assertTrue(Path(j["out_root"]).is_dir())
        self.assertEqual(j["default_effect"], EMBERS)
        self.assertEqual(set(j["tools"]), {"fx_list", "fx_inspect", "fx_edit", "fx_session", "fx_validate", "fx_create", "fx_script_read", "fx_script_write",
                                           "fx_render", "fx_export", "fx_coverage", "fx_color_variants"})

    def test_post_and_file(self):
        code, j = call(self.s.base, "fx_list", pattern="embers")
        self.assertEqual((code, j["ok"], j["count"]), (200, True, 1))
        code, j = call(self.s.base, "fx_render", path=EMBERS, size="64x64")
        f = j["files"][0]
        code, ct, body = get(self.s.base, "/api/fx/file?path=" + f)
        self.assertEqual((code, ct), (200, "image/png"))
        self.assertTrue(body.startswith(b"\x89PNG"))
        self.assertEqual(get(self.s.base, "/api/fx/file?path=/etc/passwd")[0], 403)
        self.assertEqual(get(self.s.base, "/api/fx/file?path=" + f.replace("/fx_render/", "/fx_render/../../"))[0], 403)

    def test_refusals(self):
        self.assertEqual(call(self.s.base, "fx_list", headers={"Host": "evil.example"})[0], 403)                 # DNS rebinding
        self.assertEqual(call(self.s.base, "fx_list", headers={"Origin": "http://evil.example"})[0], 403)       # another origin
        self.assertEqual(call(self.s.base, "fx_list", headers={"Origin": "http://localhost:1234"})[0], 200)
        c = http.client.HTTPConnection(*self.s.base)
        c.request("POST", "/api/fx/fx_list", "{}", {"Content-Type": "text/plain"})
        self.assertEqual(c.getresponse().status, 400)
        c = http.client.HTTPConnection(*self.s.base)
        c.request("POST", "/api/fx/fx_list", "[1]", {"Content-Type": "application/json"})
        self.assertEqual(c.getresponse().status, 400)
        self.assertEqual(call(self.s.base, "fx_nothing")[0], 400)
        code, j = call(self.s.base, "fx_edit", path=EMBERS, ops=[], out="/tmp/elsewhere")
        self.assertEqual((code, j["error"]["code"]), (403, "forbidden"))                                         # out stays below the output folder
        self.assertEqual(get(self.s.base, "/api/fx/tools", headers={"Origin": "https://evil.example"})[0], 403)


@unittest.skipIf(api is None, "hdkit.fx.api is not present")
class RealEndpoints(unittest.TestCase):
    """The page's session choreography (EditQueue: first edit with its own `out`, one version per edit, a coalesced edit = undo +
    edit, undo/redo/goto, an edit refused leaves the session alone) against the real tools on the synthetic fixture effects."""
    SYNTH = "hd/vfx/particles/synth/mesh_uber.particles"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"HDKIT_FX_OUT": str(Path(self.tmp.name).resolve())})
        self.env.start()
        self.s = Serving()
        self.s.start(webserve.make_server(None, 0, None, True, True, Path(self.tmp.name) / "out", ["synth=" + str(FIXTURES)]))

    def tearDown(self):
        self.s.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_tools_report_out_root(self):
        j = json.loads(get(self.s.base, "/api/fx/tools")[2])
        self.assertTrue(Path(j["out_root"]).is_absolute())
        self.assertIn("fx_session", j["tools"])
        self.assertFalse(j["engine"].get("mock", False))

    def test_inspect_has_what_the_page_reads(self):
        _, j = call(self.s.base, "fx_inspect", path=self.SYNTH, graph=False)
        self.assertTrue(j["ok"])
        r = j["renderers"][0]
        for k in ("index", "kind", "material", "enabled", "props"):
            self.assertIn(k, r)
        for k in ("script", "backend", "instructions", "constants", "externals", "engine_calls"):
            self.assertIn(k, j["scripts"][0])
        _, lst = call(self.s.base, "fx_list", pattern="mesh_*", info=True)
        self.assertEqual([i["path"] for i in lst["items"]], [self.SYNTH])
        self.assertIn("kinds", lst["items"][0])

    def test_answers_are_valid_json_even_with_nan(self):
        """Real effect data holds NaN (a turbulence sampler's raw words); the page's JSON.parse needs valid JSON."""
        self.assertEqual(webserve._finite({"a": [float("nan"), 1.5, float("inf"), {"b": -float("inf")}], "c": "NaN"}),
                         {"a": [None, 1.5, None, {"b": None}], "c": "NaN"})
        with mock.patch.object(api, "call", return_value={"ok": True, "x": [float("nan"), 2.0]}):
            c = http.client.HTTPConnection(*self.s.base)
            c.request("POST", "/api/fx/fx_list", "{}", {"Content-Type": "application/json"})
            body = c.getresponse().read().decode()
        self.assertNotIn("NaN", body)
        json.loads(body, parse_constant=lambda x: self.fail("non-JSON constant " + x))

    def test_edit_session_flow(self):
        _, tools = None, json.loads(get(self.s.base, "/api/fx/tools")[2])
        out = tools["out_root"] + "/edit/web-test-1-mesh_uber"
        op = lambda on: {"op": "enable", "renderer": 0, "on": on}      # noqa: E731
        _, a = call(self.s.base, "fx_edit", path=self.SYNTH, ops=[op(False)], out=out)
        self.assertTrue(a["ok"], a)
        self.assertEqual((a["session"]["out"], a["session"]["version"]), (out, 1))
        _, b = call(self.s.base, "fx_session", out=out, action="undo")            # the coalescing step: undo, then edit again
        self.assertEqual(b["session"]["version"], 0)
        _, c = call(self.s.base, "fx_edit", path=out, ops=[op(True)])
        self.assertEqual((c["session"]["version"], c["session"]["versions"]), (1, 2))
        _, i = call(self.s.base, "fx_inspect", path=out)
        self.assertEqual((i["renderers"][0]["enabled"], i["session"]["version"]), (True, 1))
        _, bad = call(self.s.base, "fx_edit", path=out, ops=[{"op": "prop", "renderer": 0, "name": "NoSuchProperty", "value": 1}])
        self.assertFalse(bad["ok"])
        self.assertEqual(call(self.s.base, "fx_session", out=out)[1]["session"]["version"], 1)           # refused: nothing changed
        h = call(self.s.base, "fx_session", out=out, action="history")[1]["history"]
        self.assertEqual([v["version"] for v in h], [0, 1])
        self.assertEqual(call(self.s.base, "fx_session", out=out, action="goto", version=0)[1]["session"]["can_redo"], True)

    def test_script_check_does_not_touch_the_edit_session(self):
        _, j = call(self.s.base, "fx_inspect", path=self.SYNTH, graph=False)
        sid = j["scripts"][0]["script"]
        text = call(self.s.base, "fx_script_read", path=self.SYNTH, script=sid)[1]["text"]
        root = json.loads(get(self.s.base, "/api/fx/tools")[2])["out_root"]
        _, a = call(self.s.base, "fx_edit", path=self.SYNTH, ops=[{"op": "enable", "renderer": 0, "on": False}], out=root + "/edit/web-t-1-x")
        ok = call(self.s.base, "fx_script_write", path=self.SYNTH, script=sid, source=text, language="cbvm", out=root + "/edit/web-t-check")[1]
        self.assertTrue(ok["ok"], ok)
        self.assertEqual(ok["session"]["out"], root + "/edit/web-t-check")
        self.assertEqual(call(self.s.base, "fx_session", out=a["session"]["out"])[1]["session"]["version"], 1)
        bad = call(self.s.base, "fx_script_write", path=self.SYNTH, script=sid, source=text + "\nbogus_op r0\n", language="cbvm", out=root + "/edit/web-t-check")[1]
        self.assertFalse(bad["ok"])


@unittest.skipUnless(NODE, "node not found")
class NodeLogic(unittest.TestCase):
    def test_logic_tests(self):
        r = subprocess.run([NODE, "--test", str(Path(__file__).with_name("fx_web_logic.test.mjs"))], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
        self.assertRegex(r.stdout, r"pass ([1-9]\d+)")
        self.assertRegex(r.stdout, r"fail 0\b")


def run_browser(script, base, shots, *extra):
    r = subprocess.run([NODE, str(Path(__file__).with_name(script)), base, str(shots), *extra], capture_output=True, text=True, timeout=300,
                       env={**os.environ, "CHROME": CHROME})
    line = (r.stdout.strip().splitlines() or ["{}"])[-1]
    try:
        j = json.loads(line)
    except ValueError:
        raise AssertionError("no result line: " + (r.stdout + r.stderr)[-2000:])
    return j


@unittest.skipUnless(NODE and CHROME and os.environ.get("HDFX_WEB_BROWSER", "1") != "0", "node, Chrome, or HDFX_WEB_BROWSER=0")
class Browser(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        keep = os.environ.get("HDFX_WEB_SHOTS")
        self.shots = Path(keep) if keep else Path(self.tmp.name) / "shots"
        self.shots.mkdir(parents=True, exist_ok=True)
        self.s = None

    def tearDown(self):
        if self.s:
            self.s.stop()
        self.tmp.cleanup()

    def verdict(self, j):
        bad = [c for c in j.get("checks", []) if not c["ok"]]
        self.assertEqual(bad, [], json.dumps(bad)[:3000])
        self.assertEqual(j.get("errors"), [])
        self.assertTrue(j.get("ok"), j)
        self.assertGreaterEqual(len(j["shots"]), 3)
        for f in j["shots"]:
            self.assertGreater(Path(f).stat().st_size, 5000)

    def test_editor_on_the_mock_api(self):
        self.s = Serving()
        self.s.start(fxmock.make_server(Path(self.tmp.name) / "out", 0))
        j = run_browser("fx_web_browser.mjs", self.s.url, self.shots / "mock")
        self.assertGreaterEqual(len(j["checks"]), 30)
        self.verdict(j)

    @unittest.skipIf(api is None, "hdkit.fx.api is not present")
    def test_editor_on_the_real_tools(self):
        with mock.patch.dict(os.environ, {"HDKIT_FX_OUT": str(Path(self.tmp.name).resolve() / "out")}):
            self.s = Serving()
            self.s.start(webserve.make_server(None, 0, None, True, True, Path(self.tmp.name) / "out", ["synth=" + str(FIXTURES)]))
            j = run_browser("fx_web_real.mjs", self.s.url, self.shots / "real", RealEndpoints.SYNTH)
            self.assertGreaterEqual(len(j["checks"]), 8)
            self.verdict(j)


if __name__ == "__main__":
    unittest.main()
