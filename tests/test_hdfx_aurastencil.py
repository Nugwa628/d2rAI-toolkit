"""Lane r3-aurastencil: EnableAuraStencil (fx/aurastencil.py). No game files needed; the GPU part skips when the gate
is busy."""
import os
import sys
import unittest

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hdkit.fx import aurastencil as au, coverage as cv, render as fr, rspec, sim  # noqa: E402
from hdkit.view import camera  # noqa: E402

M = "vfx/materials/%s.pkma"


def spec(kind, material, props):
    return rspec.RSpec(0, 0, kind, material, True, "L", props, {})


def uber(aura, kind="billboard", material="Diablo_Uber"):
    p = {"Transparent": True, "Transparent.Type": 2, "Transparency": True, "Transparency.Type": 2}
    if aura:
        p["EnableAuraStencil"] = True
    return spec(kind, M % material, p)


class Rules(unittest.TestCase):
    def test_game_state(self):
        # 0x14102e500: 0x06003001 -> test on, compare mask 0x30, write mask 0, GREATER_OR_EQUAL, ops keep, back NEVER
        s = au.stencil_state()
        self.assertEqual(s["stencil_read_mask"], 0x30)
        self.assertEqual(s["stencil_write_mask"], 0)
        self.assertEqual(s["stencil_front"]["compare"], "greater-equal")
        self.assertEqual(s["stencil_back"]["compare"], "never")
        self.assertTrue(all(s["stencil_front"][k] == "keep" for k in ("fail_op", "depth_fail_op", "pass_op")))

    def test_test_per_pixel(self):
        self.assertTrue(au.passes(0x00))                 # nothing marked: drawn
        self.assertTrue(au.passes(0x0F))                 # bits outside the mask do not count
        self.assertEqual(au.passes(au.UNIT_REF), au.AURA_REF & 0x30 >= au.UNIT_REF & 0x30)

    def test_material_flag_only_on_uber_class(self):
        self.assertTrue(fr.Material.of(uber(True)).aura_stencil)
        self.assertTrue(fr.Material.of(uber(True, "mesh", "Diablo_Uber_Mesh")).aura_stencil)
        self.assertTrue(fr.Material.of(uber(True, material="Diablo_Smoke")).aura_stencil)
        self.assertFalse(fr.Material.of(uber(False)).aura_stencil)
        self.assertFalse(fr.Material.of(uber(True, material="Diablo_Liquid")).aura_stencil)

    def test_runs_keep_order(self):
        class D:
            def __init__(self, a):
                self.material = fr.Material(aura_stencil=a)
        ds = [D(False), D(True), D(True), D(False), D(True)]
        r = au.runs(ds)
        self.assertEqual([(a, len(g)) for a, g in r], [(False, 1), (True, 2), (False, 1), (True, 1)])
        self.assertEqual([d for _a, g in r for d in g], ds)

    def test_coverage_counts_it(self):
        self.assertIn("EnableAuraStencil", cv.SUPPORTED_RENDER_FEATURES)


def _gpu_free() -> bool:
    try:
        from hdkit import gpugate
        return bool(gpugate.check().free)
    except Exception:  # noqa: BLE001
        return False


@unittest.skipUnless(_gpu_free(), "GPU gate busy or no adapter")
class Gpu(unittest.TestCase):
    """A unit quad stands in front of the camera; a big billboard is drawn in front of it. With EnableAuraStencil the
    billboard is cut out where the unit is (when the game's test hides aura draws over units), elsewhere unchanged."""

    @classmethod
    def setUpClass(cls):
        from hdkit.view import gpu

        class NoVfs:
            def exists(self, p):
                return False
        cls.G = gpu
        cls.g = gpu.Renderer(msaa=4)
        cls.fxr = fr.FxRenderer(cls.g, NoVfs())

    def unit(self):
        g, G = self.g, self.G
        P = np.array([[-0.5, 0.5, 0.0], [0.5, 0.5, 0.0], [0.5, 2.5, 0.0], [-0.5, 2.5, 0.0]])
        _r, _u, w = camera.OrthoCamera(k=10.0).basis()
        N = np.tile(w, (4, 1))
        T = np.tile([1.0, 0, 0, 1.0], (4, 1))
        uv = np.array([[0, 1], [1, 1], [1, 0], [0, 0]], float)
        tris = np.array([[0, 1, 2], [0, 2, 3]])
        vbuf, n = g._vbuf(P, N, T, uv, tris)
        ub = g._ubuf()
        bind = g._bind(ub, g.default("alb"), g.default("nrm"), g.default("orm"), g.default("mask"), g.default("strip"))
        return G.Prepared([G.Draw(vbuf, n, ub, bind, [], 0.0, P[tris], uv[tris], "quad", "unit")])

    def draw(self, aura):
        e = rspec.RenderEffect("t", [uber(aura)])
        st = sim.SimState(0.0, {"L": sim.ParticleBatch(1, {}, {0: {"Position": np.array([[0, 1.5, 0.0]]) + 2.0 * np.array(
            camera.OrthoCamera(k=10.0).basis()[2]), "Size": np.array([[4.0, 4.0]])}})})
        cam = camera.OrthoCamera(k=10.0)
        draws = self.fxr.prepare(e, st, cam)
        unit = self.unit()
        bare = self.fxr.render(draws, cam, (128, 128), None, None, fr.Post(bare_alpha=True), (64, 100), 1, None,
                               linear=True)
        lin = self.fxr.render(draws, cam, (128, 128), unit, None, fr.Post(bare_alpha=True), (64, 100), 1, None,
                              linear=True)
        none = self.fxr.render([], cam, (128, 128), unit, None, fr.Post(bare_alpha=True), (64, 100), 1, None,
                               linear=True)
        return draws, bare, lin, none

    def test_aura_draw_hidden_over_units(self):
        d0, bare0, lin0, none = self.draw(False)
        d1, bare1, lin1, _ = self.draw(True)
        self.assertFalse(d0[0].material.aura_stencil)
        self.assertTrue(d1[0].material.aura_stencil)
        unit = np.abs(none[..., :3]).sum(-1) > 1e-3        # pixels the unit covers
        self.assertGreater(int(unit.sum()), 50)
        np.testing.assert_allclose(bare0, bare1, atol=1e-3)     # no unit: the stencil changes nothing
        diff = np.abs(lin1 - lin0).sum(-1)
        hidden = not au.passes(au.UNIT_REF)
        if hidden:
            self.assertGreater(float(diff[unit].mean()), 0.01)  # over the unit: changed
        else:
            self.assertLess(float(diff[unit].max()), 1e-3)
        ring = ~unit
        ring[:, :] &= np.abs(bare0[..., 3]) > 0.05
        self.assertLess(float(diff[ring & ~np.roll(unit, 2, 0) & ~np.roll(unit, -2, 0)
                                   & ~np.roll(unit, 2, 1) & ~np.roll(unit, -2, 1)].max()), 2e-2)


if __name__ == "__main__":
    unittest.main()
