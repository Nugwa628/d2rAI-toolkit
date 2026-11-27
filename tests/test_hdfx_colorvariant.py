"""hdkit.fx.colorvariant (lane r3-colorvariant): the game's colour variants. Index rules (37 + state colour shift, 8 +
randtransforms entry), the 158-entry table (randtransforms then transforms), the shader maths, the vertex packing, the
renderer hooks (Material contribution, uniform, per-particle block) and, with a GPU (HDFX_GPU_TESTS=1), one draw.
No game files needed; the real palette is read when a casc cache copy is present."""
import json
import os
import sys
import unittest
from pathlib import Path

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hdkit.fx import colorvariant as cv, packers as pk, rspec, sim  # noqa: E402
from hdkit.fx.render import Material  # noqa: E402

PALETTE = Path((os.environ.get("HDFX_PALETTE") or "/nonexistent"))


def _t(name, adj=(0, 0, 0, 1), tint=(0, 0, 0, 0)):
    return {"type": "ColorTransform", "name": name, "colorAdjustment": dict(zip("xyzw", adj)),
            "colorTint": dict(zip("xyzw", tint))}


def _docs():
    rand = {"transforms": [_t("R%02d" % i, tint=(i / 30, 0.5, 0.25, 1.0)) for i in range(30)]}
    shift = {"transforms": [_t("S%03d" % i) for i in range(128)]}
    shift["transforms"][107] = _t("ColorTransform_ColdFrozen", (0, -1, 0.03, 0.3), (0, 0.03137255, 1, 1))
    shift["transforms"][103] = _t("ColorTransform_Poison", (0, -0.5, -0.25, 0.3), (0.0745, 0.9294, 0, 1))
    return rand, shift


class MemVfs:
    def __init__(self, files):
        self.files = {k.lower(): v for k, v in files.items()}

    def exists(self, p):
        return p.lower() in self.files

    def read(self, p):
        return self.files[p.lower()]


class Index(unittest.TestCase):
    def test_states_and_shifts(self):
        self.assertEqual(cv.index_for("cold"), 145)            # states.txt colorshift 108 + 37
        self.assertEqual(cv.index_for("poison"), 141)
        self.assertEqual(cv.index_for("red"), 137)
        self.assertEqual(cv.index_for(colorshift=73), 110)
        self.assertEqual(cv.index_for("shift:128"), 165)       # the engine's largest
        self.assertEqual(cv.index_for(colorshift=130), 0)      # a unit's custom entry: no particle tint
        self.assertEqual(cv.index_for("rand:3"), 11)
        self.assertEqual(cv.index_for("none"), 0)
        self.assertEqual(cv.index_for(150), 150)
        self.assertEqual(cv.index_for("150"), 150)
        for bad in (166, -1, "rand:30", "blurple"):
            with self.assertRaises(ValueError):
                cv.index_for(bad)


class TableTest(unittest.TestCase):
    def setUp(self):
        self.t = cv.Table(*_docs())

    def test_layout(self):
        self.assertEqual(len(self.t.entries), 158)
        self.assertIsNone(self.t.entry(7))                     # 0..7: no variant
        self.assertIsNone(self.t.entry(-3))
        self.assertIsNone(self.t.entry(166))
        e = self.t.entry(8)
        self.assertEqual((e.source, e.slot, e.name), ("rand", 0, "R00"))
        e = self.t.entry(37)
        self.assertEqual((e.source, e.slot), ("rand", 29))
        e = self.t.entry(38)                                   # colour shift 1 -> transforms[0]
        self.assertEqual((e.source, e.slot), ("shift", 0))
        e = self.t.entry(cv.index_for("cold"))                 # transforms[colorshift - 1], as the model path
        self.assertEqual(e.name, "ColorTransform_ColdFrozen")
        self.assertAlmostEqual(e.gamma, 0.3)
        self.assertEqual(self.t.entry(141).name, "ColorTransform_Poison")
        self.assertEqual(self.t.entry(145.7).index, 145)       # float -> uint truncation (the VS)
        self.assertEqual(self.t.entry(165).slot, 127)

    def test_load_from_vfs_and_missing(self):
        rand, shift = _docs()
        t = cv.Table.load(MemVfs({cv.RAND_PATH: json.dumps(rand).encode(), cv.SHIFT_PATH: json.dumps(shift).encode()}))
        self.assertTrue(t.loaded)
        self.assertEqual(t.entry(145).name, "ColorTransform_ColdFrozen")
        t = cv.Table.load(MemVfs({}))
        self.assertFalse(t.loaded)
        self.assertTrue(all(e.identity() for e in t.entries))

    def test_params_and_choices(self):
        g, tint = self.t.params([0, 145, 145, 8, np.nan])
        np.testing.assert_allclose(g, [0, 0.3, 0.3, 1.0, 0], atol=1e-6)
        np.testing.assert_allclose(tint[1], [0, 0.03137255, 1, 1], atol=1e-6)
        np.testing.assert_allclose(tint[0], [1, 1, 1, 0])
        ch = self.t.choices()
        self.assertEqual((ch[0]["index"], len(ch)), (0, 159))
        self.assertFalse(next(c for c in ch if c["index"] == 145)["identity"])


class Maths(unittest.TestCase):
    def test_shader_formula(self):
        c = np.array([[0.5, 0.25, 0.1]])
        tint = np.array([[0.2, 0.4, 1.0, 0.5]])
        got = cv.apply(c, 0.3, tint, 0.8)
        v = c ** 0.3
        v = v + 0.5 * (v * tint[:, :3] - v)
        np.testing.assert_allclose(got, c + 0.8 * (v - c))
        np.testing.assert_allclose(cv.apply(c, 0.3, tint, 0.0), c)             # VariantContribution 0
        np.testing.assert_allclose(cv.apply(c, 0.0, tint, 1.0), c)             # no entry
        np.testing.assert_allclose(cv.apply(c, 1.0, [[0, 0, 0, 0]], 1.0), c)   # identity entry

    def test_pack_folds_the_amount(self):
        tint = np.array([[0.2, 0.4, 1.0, 0.5], [0, 0.03137255, 1, 1], [1, 1, 1, 0]])
        eff = cv.unpack_tint(cv.pack_tint(tint))
        np.testing.assert_allclose(eff, 1 + tint[:, 3:] * (tint[:, :3] - 1), atol=0.5 / 255 + 1e-6)
        c = np.array([[0.5, 0.25, 0.1]] * 3)
        g = np.array([0.3, 0.3, 1.0])
        packed = np.power(c, g[:, None]) * eff                     # what fx.wgsl does with contribution 1
        np.testing.assert_allclose(packed, cv.apply(c, g, tint, 1.0), atol=3e-3)
        self.assertTrue(np.all(cv.pack_tint(tint) < 2 ** 24))       # exact in f32


def _cpu_renderer(fr, vfs):
    """An FxRenderer without a GPU (prepare is CPU only)."""
    f = fr.FxRenderer.__new__(fr.FxRenderer)
    f.gpu, f.vfs, f.post, f.notes, f.lights = None, vfs, fr.Post(), [], []
    return f


class Renderer(unittest.TestCase):
    def _r(self, on=True, contrib=0.75, kind="billboard", mat="vfx/materials/Diablo_Uber.pkma"):
        props = {"Transparent": True, "Transparent.Type": 1, "Color": True}
        if on:
            props.update({"ColorVariant": True, "ColorVariant.VariantContribution": contrib})
        return rspec.RSpec(0, 0, kind, mat, True, "L", props, {})

    def test_material_contribution(self):
        m = Material.of(self._r())
        self.assertTrue(m.on("COLOR_VARIANT"))
        self.assertAlmostEqual(m.color_variant, 0.75)
        self.assertEqual(Material.of(self._r(on=False)).color_variant, 0.0)
        dm = Material.of(self._r(kind="mesh", mat="vfx/materials/Diablo_Default_Mesh.pkma"))
        self.assertTrue(dm.on("COLOR_VARIANT"))
        self.assertAlmostEqual(dm.color_variant, 0.75)

    def test_fill_block(self):
        t = cv.Table(*_docs())
        inputs = {cv.INPUT: np.array([[145.0], [0.0], [141.0]])}
        blk = pk.particle_block(inputs, 3)
        self.assertEqual(cv.fill_block(blk, inputs, 3, t, True), 2)
        m0, d0 = pk.OFF["misc"][0] - 10, pk.OFF["dist"][0] - 10
        np.testing.assert_allclose(cv.unpack_misc_y(blk[:, m0 + 1])[0], [0.3, 0, 0.3], atol=1 / 512)
        self.assertEqual(blk[1, d0 + 2], 0)
        np.testing.assert_allclose(cv.unpack_tint(blk[0, d0 + 2]), [[0, 8 / 255, 1]], atol=1e-6)
        np.testing.assert_allclose(blk[:, m0 + 3], 1.0)                 # MonsterFade untouched
        blk2 = pk.particle_block(inputs, 3)
        self.assertEqual(cv.fill_block(blk2, inputs, 3, t, False), 0)   # permutation off: nothing written
        np.testing.assert_array_equal(blk2, pk.particle_block(inputs, 3))
        one = pk.particle_block({cv.INPUT: 145.0}, 4)                    # a constant input
        self.assertEqual(cv.fill_block(one, {cv.INPUT: 145.0}, 4, t, True), 4)

    def test_prepare_and_uniform(self):
        from hdkit.fx import render as fr
        from hdkit.view import camera
        rand, shift = _docs()
        vfs = MemVfs({cv.RAND_PATH: json.dumps(rand).encode(), cv.SHIFT_PATH: json.dumps(shift).encode()})
        fxr = _cpu_renderer(fr, vfs)
        e = rspec.RenderEffect("t", [self._r(contrib=1.0)])
        st = sim.SimState(0.0, {"L": sim.ParticleBatch(1, {}, {0: {"Position": np.array([[0, 2.0, 0]]),
                                                                   "Size": np.array([[1.0, 1.0]]),
                                                                   "Color.Color": np.array([[1, 1, 1, 1]]),
                                                                   cv.INPUT: np.array([[145.0]])}})})
        cam = camera.OrthoCamera(k=10.0)
        d = fxr.prepare(e, st, cam)[0]
        np.testing.assert_allclose(cv.unpack_misc_y(d.verts[:, pk.OFF["misc"][0] + 1])[0], 0.3, atol=1 / 512)
        self.assertGreater(d.verts[0, pk.OFF["dist"][0] + 2], 0)
        u = fr.FxOverlay.uniforms(type("P", (), {"post": fr.Post(), "light": None})(), d, np.eye(4), (64, 64),
                               (0.0, 1.0), cam)
        self.assertEqual(u[256], 1.0)                                    # xv.x = VariantContribution
        fxr2 = _cpu_renderer(fr, MemVfs({}))                           # no palette: untinted, with a note
        d2 = fxr2.prepare(e, st, cam)[0]
        self.assertEqual(cv.unpack_misc_y(d2.verts[0, pk.OFF["misc"][0] + 1])[0], 1.0)   # identity entry: exponent 1, tint 1
        self.assertTrue(any("table not found" in n for n in fxr2.notes))

    def test_coverage_counts_it(self):
        from hdkit.fx import coverage
        self.assertIn("ColorVariant", coverage.SUPPORTED_RENDER_FEATURES)

    def test_api_attr_names(self):
        from hdkit.fx import api
        self.assertEqual(api.color_variant_attrs({"ColorVariantIndex": "cold"})["ColorVariantIndex"], [145, 0, 0, 0])
        self.assertEqual(api.color_variant_attrs({"ColorVariantIndex": [141]})["ColorVariantIndex"], [141, 0, 0, 0])
        self.assertIsNone(api.color_variant_attrs(None))
        with self.assertRaises(api.FxError):
            api.color_variant_attrs({"ColorVariantIndex": "nope"})


@unittest.skipUnless((PALETTE / "transforms.json").exists(), "game palette (casc cache) absent")
class GamePalette(unittest.TestCase):
    def test_states_hit_named_entries(self):
        t = cv.Table(json.loads((PALETTE / "randtransforms.json").read_text()),
                     json.loads((PALETTE / "transforms.json").read_text()))
        self.assertEqual(t.entry(cv.index_for("cold")).name, "ColorTransform_ColdFrozen")
        self.assertEqual(t.entry(cv.index_for("poison")).name, "ColorTransform_Poison")
        self.assertEqual(t.entry(cv.index_for("sigillethargy")).name, "ColorTransform_Seal_Lethargy")
        self.assertEqual(t.entry(cv.index_for("sigilrancor")).name, "ColorTransform_Seal_Rancor")
        self.assertEqual(t.entry(cv.index_for("sigildeath")).name, "ColorTransform_Seal_Death")


def _adapter():
    try:
        import wgpu
        return wgpu.gpu.request_adapter_sync(power_preference="high-performance") is not None
    except Exception:  # noqa: BLE001
        return False


@unittest.skipUnless(os.environ.get("HDFX_GPU_TESTS") == "1" and _adapter(), "GPU tests: set HDFX_GPU_TESTS=1 (gate first)")
class Gpu(unittest.TestCase):
    def test_cold_variant_turns_blue(self):
        from hdkit.fx import render as fr
        from hdkit.view import camera, gpu
        rand, shift = _docs()
        vfs = MemVfs({cv.RAND_PATH: json.dumps(rand).encode(), cv.SHIFT_PATH: json.dumps(shift).encode()})
        R = gpu.Renderer(msaa=4)
        fxr = fr.FxRenderer(R, vfs)
        r = rspec.RSpec(0, 0, "billboard", "vfx/materials/Diablo_Uber.pkma", True, "L",
                        {"Transparent": True, "Transparent.Type": 0, "ColorVariant": True,
                         "ColorVariant.VariantContribution": 1.0}, {})
        cam = camera.OrthoCamera(k=10.0)
        out = []
        for idx in (0.0, 145.0):
            st = sim.SimState(0.0, {"L": sim.ParticleBatch(1, {}, {0: {
                "Position": np.array([[0, 2.0, 0]]), "Size": np.array([[2.0, 2.0]]),
                "Color.Color": np.array([[1, 0.6, 0.3, 1]]), cv.INPUT: np.array([[idx]])}})})
            draws = fxr.prepare(fr.rspec.RenderEffect("t", [r]), st, cam)
            lin = fxr.render(draws, cam, (128, 128), None, None, fr.Post(bare_alpha=True), (64, 96), 1, None, linear=True)
            out.append(lin[int(96 - 2 * 10 * cam.basis()[1][1]), 64][:3])
        self.assertGreater(out[0][0], out[0][2])          # untinted: orange
        self.assertGreater(out[1][2], 3 * out[1][0])      # ColdFrozen: blue
