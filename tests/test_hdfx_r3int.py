"""Integration of hdfx round 3 (lane r3-integrate): features that share slots work together.

- AlphaMask.AtlasAlphaOnly (lane r3-atlasalpha) and ColorVariant (lane r3-colorvariant) share the vertex float misc.y
  (packers.MISC_Y_*, colorvariant.pack_misc_y): both on at once must give the atlas cell and the variant exponent back.
- With a GPU (HDFX_GPU_TESTS=1, pass hdkit.gpugate first): one billboard with both features on draws the cell-local base
  ramp (AtlasAlphaOnly) recoloured by the variant (pow(c, e) x tint).
- A unit's own ColorVariantIndex (lane r3-unitmat) never overrides an explicit one (lane r3-colorvariant).
- Lane r3-shapes' bone binding in the API: fx_render context {"kind": "unit"} (api.unit_bone_driver), with the game's
  CASC data (skipped without it).
"""
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hdkit.fx import colorvariant as cv, packers as pk, rspec, sim  # noqa: E402
from hdkit.fx import render as fr  # noqa: E402
from hdkit.view import camera  # noqa: E402

AM = "data/hd/vfx/textures/test/am_atlas.texture"
BASE = "data/hd/vfx/textures/test/base_ramp.texture"
GAMMA, TINT = 2.0, (0.5, 1.0, 1.0, 1.0)          # index 145 (cold) in the test table: c^2 x (0.5, 1, 1)


def _t(name, adj=(0, 0, 0, 1), tint=(0, 0, 0, 0)):
    return {"type": "ColorTransform", "name": name, "colorAdjustment": dict(zip("xyzw", adj)),
            "colorTint": dict(zip("xyzw", tint))}


def _table():
    rand = {"transforms": [_t("R%02d" % i) for i in range(30)]}
    shift = {"transforms": [_t("S%03d" % i) for i in range(128)]}
    shift["transforms"][107] = _t("Cold", (0, 0, 0, GAMMA), TINT)
    return cv.Table(rand, shift)


def _props(alpha_only=True, variant=True):
    p = {"Transparent": True, "Transparent.Type": 0, "Blend": True, "Blend.TextureBase": BASE,
         "Blend.TextureBlend": "data/hd/vfx/textures/default/White.texture", "AlphaMask": True, "AlphaMask.Mask": AM,
         "Atlas": True, "Atlas.SubDiv": (2, 2), "Atlas.Blending": 0}
    if alpha_only:
        p["AlphaMask.AtlasAlphaOnly"] = True
    if variant:
        p.update({"ColorVariant": True, "ColorVariant.VariantContribution": 1.0})
    return p


class SharedMiscY(unittest.TestCase):
    def test_cell_and_exponent_round_trip(self):
        r, u, w = camera.OrthoCamera().basis()
        inputs = {cv.INPUT: np.array([[145.0], [145.0], [0.0]])}
        blk = pk.particle_block(inputs, 3)
        self.assertEqual(cv.fill_block(blk, inputs, 3, _table(), True), 2)
        v = pk.pack_billboards(0, [[0, 0, 0], [1, 0, 0], [2, 0, 0]], [[1, 1]] * 3, [0, 0, 0], (r, u, w), blk,
                               tid=[1.0, -1.0, 6.0], rects=pk.atlas_rects((2, 2)), alpha_only_subdiv=(2, 2))
        g, cell = cv.unpack_misc_y(v[::6, pk.OFF["misc"][0] + 1])
        np.testing.assert_allclose(g, [GAMMA, GAMMA, 0.0])
        np.testing.assert_array_equal(cell, [1.0, -1.0, 2.0])     # negative cells (the VS's sign rule) survive

    def test_exponent_quantisation(self):
        g, cell = cv.unpack_misc_y(cv.pack_misc_y([0.3, 1.0, 15.99, 40.0]) + pk.MISC_Y_CELL_BIAS)
        np.testing.assert_allclose(g, [0.3, 1.0, 15.99, 4095 / 256], atol=1 / 512)
        np.testing.assert_array_equal(cell, 0.0)

    def test_uniforms_both(self):
        m = fr.Material.of(rspec.RSpec(0, 0, "billboard", "Diablo_Uber", True, "L", _props(), {}))
        self.assertTrue(m.atlas.get("alpha_only"))
        self.assertEqual(m.color_variant, 1.0)


class UnitVariantDefaults(unittest.TestCase):
    def test_explicit_index_wins(self):
        from hdkit.view import unitvariant as uv
        choice = SimpleNamespace(effect_index=141)
        self.assertEqual(uv.effect_attrs(choice, None)["ColorVariantIndex"], [141, 0, 0, 0])
        self.assertEqual(uv.effect_attrs(choice, {"ColorVariantIndex": [145, 0, 0, 0]})["ColorVariantIndex"], [145, 0, 0, 0])
        from hdkit.view import fxcli
        a = fxcli.parse_attrs(None, "cold")
        self.assertEqual(uv.effect_attrs(choice, a)["ColorVariantIndex"][0], 145)


NATIVE = (os.environ.get("HDKIT_CASC_GAME") or "/nonexistent")


@unittest.skipUnless(Path(NATIVE).is_dir(), "CASC data (HDKIT_CASC_GAME) absent")
class UnitContext(unittest.TestCase):
    def setUp(self):
        from unittest import mock
        env = {k: v for k, v in os.environ.items() }
        env.setdefault("HDKIT_CASC_GAME", NATIVE)
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        from hdkit.fx import api
        self.api = api
        self.fs = api.make_fs(["casc"])

    def test_painworm_glow_on_its_two_bones(self):
        d, unit, binds = self.api.unit_bone_driver(
            self.fs, "hd/character/enemy/painworm1.json",
            "data/hd/vfx/particles/character/enemy/painworm1/vfx_painworm1_chest_glow.particles")
        self.assertEqual(sorted(b["bone"] for b in binds), ["chest", "pelvis"])
        f = d.frame(0.5)
        self.assertEqual(len(f.contexts), 2)
        ys = sorted(float(c.transform[1, 3]) for c in f.contexts)
        self.assertGreater(ys[0], 0.2)                       # on the body, not at the unit's origin

    def test_build_driver_and_refusal(self):
        eff = "data/hd/vfx/particles/character/enemy/wraith1/vfx_wraith1_neutral.particles"
        d, unit, sc = self.api.build_driver({"kind": "unit", "unit": "hd/character/enemy/wraith1.json"}, None, self.fs,
                                            1.0, eff)
        self.assertEqual(d.duration, 1.0)
        self.assertIsNotNone(unit)
        with self.assertRaises(self.api.FxError):
            self.api.unit_bone_driver(self.fs, "hd/character/enemy/wraith1.json",
                                      "data/hd/vfx/particles/character/enemy/painworm1/vfx_painworm1_chest_glow.particles")
        self.assertIn("unit", self.api.CONTEXT_KINDS)


class PresetSpot(unittest.TestCase):
    def test_spot_points_along_plus_z(self):
        """mapview/hd.py preset_lights places a spot's stand-in point along the transform's +Z, as shadow.spot_from_preset
        (the game's forge spot); it used -Z before the r3 integration."""
        from unittest import mock
        from hdkit.view.mapview import hd
        ent = {"name": "s", "components": [
            {"type": "TransformDefinitionComponent", "position": {"x": 1.0, "y": 2.0, "z": 3.0},
             "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}, "scale": {"x": 1.0, "y": 1.0, "z": 1.0}},
            {"type": "SpotLightDefinitionComponent", "power": 1.0, "distance": 10.0, "angle": 90.0}]}
        with mock.patch.object(hd.preset, "_read_json", return_value={"entities": [ent]}):
            (L,) = hd.preset_lights(None, "x.json")
        np.testing.assert_allclose(L["pos"], (1.0, 2.0, 8.0), atol=1e-6)


def _adapter():
    try:
        import wgpu
        return wgpu.gpu.request_adapter_sync(power_preference="high-performance") is not None
    except Exception:
        return False


@unittest.skipUnless(os.environ.get("HDFX_GPU_TESTS") == "1" and _adapter(), "GPU tests: set HDFX_GPU_TESTS=1 (gate first)")
class Gpu(unittest.TestCase):
    """test_hdfx_atlasalpha's 2 x 2 alpha atlas (cell 1 white) and red base ramp, TexID 1, now with a colour variant."""

    def _render(self, props, index=145.0):
        from hdkit.view import gpu

        class NoVfs:
            def exists(self, p):
                return False
        R = gpu.Renderer(msaa=4)
        fxr = fr.FxRenderer(R, NoVfs())
        fxr.cv_table = _table()
        n = 64
        am = np.zeros((n, n, 4), np.uint8)
        am[: n // 2, n // 2:] = 255
        ramp = np.zeros((n, n, 4), np.uint8)
        ramp[..., 0] = np.linspace(0, 255, n).astype(np.uint8)[None, :]
        ramp[..., 3] = 255
        fxr._texinfo[(False, AM)] = SimpleNamespace(mips=[am], srgb=False)
        fxr._texinfo[(False, BASE)] = SimpleNamespace(mips=[ramp], srgb=False)
        fxr._texinfo[(True, BASE)] = fxr._texinfo[(False, BASE)]
        e = rspec.RenderEffect("t", [rspec.RSpec(0, 0, "billboard", "Diablo_Uber", True, "L", props, {})])
        st = sim.SimState(0.0, {"L": sim.ParticleBatch(1, {}, {0: {
            "Position": np.array([[0, 2.0, 0]]), "Size": np.array([[2.0, 2.0]]), "TextureID": np.array([[1.0]]),
            "Color.Color": np.array([[1, 1, 1, 1.0]]), "Blend.BaseContribution": np.array([[1.0]]),
            cv.INPUT: np.array([[index]])}})})
        cam = camera.OrthoCamera(k=20.0)
        draws = fxr.prepare(e, st, cam)
        self.assertEqual(len(draws), 1)
        lin = fxr.render(draws, cam, (128, 128), None, None, fr.Post(bare_alpha=True), (64, 96), 1, None, linear=True)
        _r, u, _w = cam.basis()
        cy = int(round(96 - 2.0 * 20.0 * u[1]))
        row = lin[cy, :, 0]
        return row, np.nonzero(row > 1e-4)[0]

    def test_alpha_only_and_variant_together(self):
        row_a, lit_a = self._render(_props(True, False))          # AtlasAlphaOnly alone
        row_b, lit_b = self._render(_props(True, True))           # both
        self.assertGreater(len(lit_a), 10)
        self.assertLess(row_a[lit_a[0] + 4], 0.05)                 # the cell-local ramp: dark left ...
        self.assertGreater(row_a[lit_a[-1] - 1], 0.7)              # ... bright right
        # both on: the same cell-local ramp, its colour recoloured. Additive output = rgb x alpha, and the alpha is the grey
        # g itself (AlphaMask op 0: g x mask 1), so row_a = g^2 and with the variant row_b = 0.5 g^GAMMA x g.
        k = lit_a[len(lit_a) // 2:-2]
        g = np.sqrt(row_a[k])
        np.testing.assert_allclose(row_b[k], 0.5 * g ** GAMMA * g, rtol=0.03, atol=2e-3)
        row_n, _ = self._render(_props(True, True), index=0.0)    # index 0..7: no variant
        np.testing.assert_allclose(row_n[k], row_a[k], rtol=1e-3, atol=1e-4)


if __name__ == "__main__":
    unittest.main()
