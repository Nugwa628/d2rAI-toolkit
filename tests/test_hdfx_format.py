"""Unit tests of hdkit/fx (schema, pfile, graph, CLI) on synthetic `.particles` files built here; a corpus check
runs when D2R's extracted hd/vfx is present (HDFX_CORPUS or the shared cache), else it is skipped.
Run: python -m unittest tests.test_hdfx_format"""
import io
import json
import os
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
from hdkit.formats import hd_particles as hp  # noqa: E402
from hdkit.fx import graph, schema  # noqa: E402
from hdkit.fx import __main__ as cli  # noqa: E402
from hdkit.fx.pfile import PFile, PObj  # noqa: E402

CORPUS = Path((os.environ.get("HDFX_CORPUS") or "/nonexistent"))
MEPHISTO = "hd/vfx/particles/character/enemy/mephisto/vfx_mephisto_ambient.particles"
GOLDEN = Path((os.environ.get("HDFX_GOLDEN") or "/nonexistent"))
RED = [1.0, 0.25, 0.5]


def program(consts: list[bytes]) -> bytes:
    code = bytes([0x7D])                                   # eplg
    body = code + b"".join(consts)
    head = struct.pack("<9I", 4, len(code), 0, 32 * len(consts), 0, len(consts), 0, 0, 0)
    prog = head + body
    return prog + b"\0" * (-len(prog) % 4)


def lanes(v) -> bytes:
    return struct.pack("<8f", *[float(v[k % len(v)]) for k in range(8)])


def build(layer_names=("Root", "Smoke"), texture="data/hd/vfx/textures/a.texture") -> bytes:
    """An effect with one PC graph: Root (spawner) emits Signal, which spawns the other layers; each non-root layer has
    two fields (a float3 position, a float4 colour), a curve sampler, a colour constant, a script and a billboard."""
    pf = PFile()
    root = pf.add(PObj("CParticleEffect"))
    alist = pf.add(pf.new("CParticleAttributeList"))
    fade = pf.add(pf.new("CParticleAttributeDeclaration", name="Fade"))
    pf.obj(fade).set_vec(14, [1, 0, 0, 0])
    pf.obj(alist).set_arr(0, [fade])
    g = pf.add(pf.new("CLayerGraphCompileCache", platform="PC"))
    e0 = pf.add(pf.new("CLayerGraphCompileCache_EntrySlot"))
    e1 = pf.add(pf.new("CLayerGraphCompileCache_EntrySlot", name="Root", layer_slot=1))
    ev0 = pf.add(pf.new("CLayerGraphCompileCache_EventSlot", source=0xFFFFFFFF, targets=[0]))
    ev1 = pf.add(pf.new("CLayerGraphCompileCache_EventSlot", name="Signal",
                        targets=list(range(1, len(layer_names)))))
    slots = []
    for k, name in enumerate(layer_names):
        lay = PObj("CLayerCompileCache")
        lref = pf.add(lay)
        pf.set_str(lay, 20, name)
        f_pos = pf.add(pf.new("CLayerCompileCacheField", type=33, size=12))
        pf.obj(f_pos).set_u32(0, 0x80000000 | 7)
        f_col = pf.add(pf.new("CLayerCompileCacheField", type=34, size=16))
        pf.obj(f_col).set_u32(0, 0x80000000 | 8)
        lay.set_arr(2, [f_pos, f_col])
        if k == 0:
            data = pf.add(pf.new("CParticleNodeSamplerData_EventStream", data=[0.0]))
            smp = pf.add(pf.new("CLayerCompileCacheSampler", name="EventStream", data=data, type=4))
            lay.set_arr(7, [smp])
            scr = pf.add(pf.new("CCompilerBlobCache", backend="CPU/CPU_VM", program=[], ext_names=["0", "1"]))
            pf.obj(scr).set(2, program([]), 4)
            lay.set_arr(9, [scr])
        else:
            cur = pf.new("CParticleNodeSamplerData_Curve", times=[0.0, 1.0], values=[0, 0, 0, 1, 1, 1],
                         tangents=[0.0] * 12)
            cur.set_u32(10, 3)
            cur.set(0, struct.pack("<II", 0x676E65, pf.intern("Growth")), 8)
            cref = pf.add(cur)
            smp = pf.add(pf.new("CLayerCompileCacheSampler", name="__sampler_0", data=cref, type=3))
            lay.set_arr(7, [smp])
            col = lanes(RED)
            lay.set(0, col, 16)
            lay.set(1, col[:16], 16)
            spawn = pf.add(pf.new("CCompilerBlobCache", backend="CPU/CPU_VM", ext_names=["0", "1"],
                                  calls=["rand_float_float_RandCtx"]))
            pf.obj(spawn).set(2, program([col, lanes([2.0])]), 4)
            evo = pf.add(pf.new("CCompilerBlobCache", backend="CPU/CPU_VM", kind=4, ext_names=["0", "1"]))
            pf.obj(evo).set(2, program([lanes([3.0])]), 4)
            lay.set_arr(9, [spawn, evo])
            props = [pf.add(pf.new("CLayerCompileCacheRendererProperty", name="EnableRendering", value=hp.ON)),
                     pf.add(pf.new("CLayerCompileCacheRendererProperty", name="Diffuse.DiffuseMap", type=12,
                                   path=texture)),
                     pf.add(pf.new("CLayerCompileCacheRendererProperty", name="SoftParticles.SoftnessDistance",
                                   type=7, value=struct.pack("<4f", 1, 1, 1, 1)))]
            inp = [pf.add(pf.new("CLayerCompileCacheRendererParticleInput", field=0)),
                   pf.add(pf.new("CLayerCompileCacheRendererParticleInput", input=100, field=1, name="Color.Color",
                                 type=34))]
            ren = pf.add(pf.new("CLayerCompileCacheRenderer", kind=0, inputs=inp, props=props,
                                material="vfx/materials/Diablo_Uber.pkma"))
            lay.set_arr(8, [ren])
        slot = pf.new("CLayerGraphCompileCache_LayerSlot", layer=lref, inputs=[0 if k == 0 else 1])
        if k == 0:
            slot.set_arr(2, [1])
        slots.append(pf.add(slot))
    go = pf.obj(g)
    go.set_arr(2, [e0, e1])
    go.set_arr(3, slots)
    go.set_arr(4, [ev0, ev1])
    pf.obj(root).set_arr(1, [g])
    pf.obj(root).set_u32(3, alist)
    return pf.data()


class PFileTest(unittest.TestCase):
    def test_roundtrip_and_header(self):
        b = build()
        pf = PFile.read(b)
        self.assertEqual(pf.data(), b)
        self.assertEqual(struct.unpack_from("<I", b, 0)[0], 0xCA000B11)
        self.assertEqual(pf.check(), [])
        self.assertEqual(pf.obj(1).cls, "CParticleEffect")
        names = [pf.strings[c] for c in pf.classes]
        self.assertEqual(names[0], "CParticleEffect")    # class table in order of first appearance

    def test_errors(self):
        b = build()
        with self.assertRaises(ValueError):
            PFile.read(b[:20])
        with self.assertRaises(ValueError):
            PFile.read(b"\0" * 4 + b[4:])
        with self.assertRaises(ValueError):
            PFile.read(b + b"x")

    def test_unknown_class_kept_raw(self):
        pf = PFile.read(build())
        pf.add(PObj("CSomethingNew", raw=b"\x01\x00\x07\x00abcd"))
        b = pf.data()
        pf2 = PFile.read(b)
        self.assertEqual(pf2.objects[-1].raw, b"\x01\x00\x07\x00abcd")
        self.assertEqual(pf2.data(), b)

    def test_field_edits_keep_order_and_sizes(self):
        o = PObj("CLayerCompileCacheRendererProperty")
        o.set_u32(3, 5)
        o.set_u32(0, 1)
        self.assertEqual([f[0] for f in o.fields], [0, 3])
        with self.assertRaises(ValueError):
            o.set(2, b"\0" * 4)
        o.set(3, None)
        self.assertFalse(o.has(3))

    def test_remove_renumbers(self):
        pf = PFile.read(build(("Root", "A", "B")))
        lay_a = graph.load(b"", pf=pf).layer("A").obj
        ren_b = graph.load(b"", pf=pf).layer("B").renderers[0].obj
        mapping = pf.remove([lay_a])
        self.assertEqual(pf.check(), [])
        self.assertEqual(pf.obj(mapping[ren_b]).cls, "CLayerCompileCacheRenderer")
        self.assertNotIn(lay_a, mapping)

    def test_copy_from_merges_strings(self):
        a, b = PFile.read(build()), PFile.read(build(texture="data/hd/vfx/textures/other.texture"))
        lay = graph.load(b"", pf=b).layer("Smoke").obj
        n = len(a.objects)
        mp = a.copy_from(b, [lay])
        self.assertEqual(a.check(), [])
        self.assertEqual(min(mp.values()), n + 1)
        self.assertIn("data/hd/vfx/textures/other.texture", a.strings)
        # every copied string field names the same text as in the source
        for old, new in mp.items():
            so, no = b.obj(old), a.obj(new)
            for fid, _v, _e in so.fields:
                if schema.kind(so.cls, fid) == "str":
                    self.assertEqual(b.str_field(so, fid), a.str_field(no, fid))

    def test_closure_and_unreachable(self):
        pf = PFile.read(build())
        self.assertEqual(pf.unreachable(), [])
        pf.add(PObj("CParticleNodePinIn"))
        self.assertEqual(pf.unreachable(), [len(pf.objects)])


class SchemaTest(unittest.TestCase):
    def test_tables(self):
        self.assertEqual(len(schema.SIZES), 26)
        for cls, fields in schema.FIELDS.items():
            self.assertIn(cls, schema.SIZES)
            for fid, (_n, k) in fields.items():
                self.assertIn(fid, schema.SIZES[cls], (cls, fid))
                if k in ("objs", "strs", "u32s", "f32s"):
                    self.assertEqual(schema.SIZES[cls][fid], -4, (cls, fid))
                if k == "loc":
                    self.assertEqual(schema.SIZES[cls][fid], -8, (cls, fid))
        for (cls, fid) in schema.TARGET:
            self.assertIn(schema.kind(cls, fid), schema.REF_KINDS)
        self.assertEqual(schema.type_name(31), "float")
        self.assertEqual(schema.type_name(34), "float4")
        self.assertEqual(schema.field_id("CLayerCompileCacheRenderer", "material"), 4)


class GraphTest(unittest.TestCase):
    def test_load(self):
        eff = graph.load(build(("Root", "Smoke", "Sparks")))
        self.assertEqual([lay.name for lay in eff.layers], ["Root", "Smoke", "Sparks"])
        self.assertEqual(eff.layers[0].children, ["Smoke", "Sparks"])
        self.assertEqual(eff.layers[1].spawn["by"], ["Signal"])
        self.assertEqual(eff.attributes[0].name, "Fade")
        self.assertEqual(eff.attributes[0].default, [1.0])
        smoke = eff.layer("Smoke")
        self.assertEqual([f.name for f in smoke.fields], ["0", "1"])
        self.assertEqual(smoke.fields[1].type, "float4")
        r = smoke.renderers[0]
        self.assertEqual((r.kind, r.enabled, r.index), ("billboard", True, 0))
        self.assertEqual(r.inputs, {"input0": "0", "Color.Color": "1"})
        self.assertEqual(r.props["Diffuse.DiffuseMap"]["string"], "data/hd/vfx/textures/a.texture")
        self.assertEqual(r.props["SoftParticles.SoftnessDistance"]["value"], 1.0)
        self.assertEqual(smoke.spawn_script, eff.scripts[smoke.scripts[0]].obj)
        self.assertEqual(eff.scripts[smoke.scripts[1]].kind, "evolve")
        self.assertEqual(eff.scripts[smoke.scripts[0]].calls, ["rand_float_float_RandCtx"])
        c = [s for s in eff.samplers if s.kind == "curve"][0]
        self.assertEqual(c.label, "Growth")
        self.assertEqual(c.data["values"], [[0, 0, 0], [1, 1, 1]])
        self.assertEqual(len(c.data["tangents_in"]), 2)
        self.assertEqual(smoke.colors, [{"index": 0, "width": 3, "value": RED}])
        self.assertEqual(len(eff.renderers), 2)

    def test_json_roundtrip(self):
        eff = graph.load(build())
        d = json.loads(json.dumps(eff.to_json()))
        self.assertEqual(graph.Effect.from_json(d).to_json(), eff.to_json())

    def edit(self, ops, names=("Root", "Smoke")):
        b = graph.apply_edits(build(names), ops)
        pf = PFile.read(b)
        self.assertEqual(pf.data(), b)
        self.assertEqual(pf.check(), [])
        return graph.load(b)

    def test_prop_enable_texture(self):
        eff = self.edit([{"op": "prop", "renderer": 0, "name": "SoftParticles.SoftnessDistance", "value": 2.5},
                         {"op": "enable", "renderer": "layer:Smoke", "on": False},
                         {"op": "texture", "renderer": "*", "name": "Diffuse.DiffuseMap",
                          "path": "data/hd/vfx/textures/b.texture"}])
        r = eff.renderers[0]
        self.assertEqual(r.props["SoftParticles.SoftnessDistance"]["value"], 2.5)
        self.assertFalse(r.enabled)
        self.assertEqual(r.props["Diffuse.DiffuseMap"]["string"], "data/hd/vfx/textures/b.texture")
        eff = self.edit([{"op": "texture", "old": "data/hd/vfx/textures/a.texture", "path": "x.texture"}])
        self.assertEqual(eff.renderers[0].props["Diffuse.DiffuseMap"]["string"], "x.texture")
        with self.assertRaises(ValueError):
            graph.apply_edits(build(), [{"op": "prop", "renderer": 0, "name": "Nope", "value": 1}])
        with self.assertRaises(ValueError):
            graph.apply_edits(build(), [{"op": "bogus"}])

    def test_same_as_hd_particles(self):
        """prop/enable write the bytes hd_particles.set_property/set_enabled write."""
        b = build()
        pf = hp.ParticleFile(b)
        r = pf.renderers()[0]
        pf.set_property(r, "SoftParticles.SoftnessDistance", 4.0)
        pf.set_enabled(r, False)
        ours = graph.apply_edits(b, [{"op": "prop", "renderer": 0, "name": "SoftParticles.SoftnessDistance",
                                      "value": 4.0}, {"op": "enable", "renderer": 0, "on": False}])
        self.assertEqual(ours, pf.data())

    def test_curve(self):
        eff = self.edit([{"op": "curve", "sampler": "Smoke/Growth", "times": [0, 0.5, 1],
                          "values": [[0, 0, 0], [2, 2, 2], [1, 1, 1]]}])
        c = [s for s in eff.samplers if s.kind == "curve"][0]
        self.assertEqual(c.data["times"], [0, 0.5, 1])
        self.assertEqual(c.data["values"][1], [2, 2, 2])
        self.assertEqual(len(c.data["tangents_out"]), 3)
        with self.assertRaises(ValueError):
            graph.apply_edits(build(), [{"op": "curve", "sampler": "Growth", "values": [[1, 2]]}])

    def test_const_and_color(self):
        eff0 = graph.load(build())
        spawn = eff0.layer("Smoke").spawn_script
        b = graph.apply_edits(build(), [{"op": "const", "script": spawn, "index": 1, "values": [7.0]}])
        pf = PFile.read(b)
        prog = pf.obj(spawn).get(2)
        at = 36 + struct.unpack_from("<I", prog, 4)[0]
        self.assertEqual(struct.unpack_from("<8f", prog, at + 32), (7.0,) * 8)
        eff = self.edit([{"op": "color", "layer": "Smoke", "index": 0, "value": [0.0, 1.0, 0.5]}])
        self.assertEqual(eff.layer("Smoke").colors[0]["value"], [0.0, 1.0, 0.5])
        pf = PFile.read(graph.apply_edits(build(), [{"op": "color", "layer": "*", "shift": {"hue": 180}}]))
        self.assertIn(lanes(graph.load(b"", pf=pf).layer("Smoke").colors[0]["value"]), pf.obj(spawn).get(2))

    def test_remove_layer(self):
        eff = self.edit([{"op": "remove_layer", "layer": "A"}], ("Root", "A", "B"))
        self.assertEqual([lay.name for lay in eff.layers], ["Root", "B"])
        self.assertEqual(eff.events[1].targets, [1])
        self.assertEqual(eff.layers[0].children, ["B"])
        self.assertNotIn("A", [graph.load(b"", pf=PFile.read(graph.apply_edits(
            build(("Root", "A", "B")), [{"op": "remove_layer", "layer": "A"}]))).layers[k].name for k in range(2)])

    def test_copy_layer(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "src.particles"
            src.write_bytes(build(("Root", "Glow"), texture="data/hd/vfx/textures/glow.texture"))
            eff = self.edit([{"op": "copy_layer", "from": str(src), "layer": "Glow", "as": "Glow2"}])
        self.assertEqual([lay.name for lay in eff.layers], ["Root", "Smoke", "Glow2"])
        self.assertEqual(eff.layers[0].children, ["Smoke", "Glow2"])
        g = eff.layer("Glow2")
        self.assertEqual(g.inputs, [1])
        self.assertEqual(g.renderers[0].props["Diffuse.DiffuseMap"]["string"], "data/hd/vfx/textures/glow.texture")
        self.assertEqual(len(eff.renderers), 2)


class CliTest(unittest.TestCase):
    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.particles"
            p.write_bytes(build())
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(cli.main(["roundtrip", d]), 0)
                cli.main(["info", str(p)])
                cli.main(["dump", str(p), "-o", str(Path(d) / "a.json")])
                cli.main(["survey", d, "-o", str(Path(d) / "s.json")])
            self.assertIn("1 byte-exact", out.getvalue())
            self.assertIn("renderer 0", out.getvalue())
            self.assertEqual(json.loads((Path(d) / "a.json").read_text())["layers"][1]["name"], "Smoke")
            s = json.loads((Path(d) / "s.json").read_text())
            self.assertEqual(s["errors"], 0)
            self.assertEqual(s["per_file"]["a.particles"]["renderers"], {"billboard|vfx/materials/Diablo_Uber.pkma": 1})
            self.assertEqual(s["per_file"]["a.particles"]["ops"].get("eplg"), 3)


@unittest.skipUnless((CORPUS / MEPHISTO).is_file(), "D2R hd/vfx corpus not present")
class CorpusTest(unittest.TestCase):
    """A sample of the corpus (every 25th file; `python -m hdkit.fx roundtrip <root>` does all of them)."""

    def test_sample_roundtrip(self):
        files = sorted(CORPUS.rglob("*.particles"))[::25]
        for f in files:
            b = f.read_bytes()
            pf = PFile.read(b)
            self.assertEqual(pf.data(), b, f)
            self.assertEqual(pf.check(), [], f)
            graph.load(b"", pf=pf)

    def test_mephisto(self):
        eff = graph.load(CORPUS / MEPHISTO)
        self.assertEqual(eff.platforms, ["PC", "Switch", "PS4", "PS5"])
        self.assertEqual([lay.name for lay in eff.layers if lay.renderers],
                         ["CylinderInf", "CylinderSkullsInf", "GroundDiscInf"])
        r = eff.renderers[0]
        self.assertEqual((r.kind, r.material), ("mesh", "vfx/materials/Diablo_Uber_Mesh.pkma"))
        self.assertEqual(r.props["SoftParticles.SoftnessDistance"]["value"], 1.5)
        self.assertEqual(r.inputs["Color.Color"], "1A")
        b = (CORPUS / MEPHISTO).read_bytes()
        out = graph.apply_edits(b, [{"op": "remove_layer", "layer": "CylinderSkullsInf"}])
        self.assertEqual(PFile.read(out).check(), [])
        self.assertEqual(len(graph.load(out).renderers), 2)

    def test_golden_dumps(self):
        """`python -m hdkit.fx dump` output equals the golden dumps (when they are present)."""
        golden = sorted(GOLDEN.glob("vfx_*.json"))
        if not golden:
            self.skipTest("no golden dumps")
        for g in golden:
            want = json.loads(g.read_text())
            src = Path(want["path"])
            if src.is_file():
                self.assertEqual(json.loads(json.dumps(graph.load(src).to_json())), want, g.name)


if __name__ == "__main__":
    unittest.main()
