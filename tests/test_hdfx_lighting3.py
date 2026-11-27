"""Lane hdfx-r4-lighting3: the shadow maps feed the scene lighting's `visibility` once, before the pass, so the decals'
plate and the composite over a game shot share one gain (no game files; GPU tests opt-in with HDFX_GPU_TESTS=1)."""
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_hdfx_lighting2 import scene  # noqa: E402


class TestPlateIrradiance(unittest.TestCase):
    def test_shadowed_origin_is_darker(self):
        """The decals' plate irradiance at the origin takes the shot's own shadow terms (G, s0, s1)."""
        Lg = scene(player=None)
        lit = Lg.irradiance(np.zeros((1, 3)))
        shad = Lg.irradiance(np.zeros((1, 3)), visibility=np.array([[0.2, 0.2, 0.2]]))
        self.assertTrue(np.all(shad < lit))
        self.assertTrue(np.all(shad > 0.0))                    # the ambient stays


def _adapter():
    try:
        import wgpu
        return wgpu.gpu.request_adapter_sync(power_preference="high-performance") is not None
    except Exception:  # noqa: BLE001
        return False


@unittest.skipUnless(os.environ.get("HDFX_GPU_TESTS") == "1" and _adapter(), "GPU tests: set HDFX_GPU_TESTS=1 (gate first)")
class Gpu(unittest.TestCase):
    def test_one_gain_for_decals_and_composite(self):
        """A decal over a game shot with a scene rig: the shadow terms are made before the pass, and the plate the decals
        relight and the final composite get the same gain."""
        from hdkit.fx import render as fr, rspec, sim
        from hdkit.view import camera, gpu

        class NoVfs:
            def exists(self, p):
                return False
        R = gpu.Renderer(msaa=4)
        fxr = fr.FxRenderer(R, NoVfs())
        Lg = scene(player=None)
        rig = Lg.rig(clut="lut")
        props = {"Decal": True, "Decal.DiffuseMap": "x.texture", "Decal.ApplyToAlbedo": True}
        e = rspec.RenderEffect("t", [rspec.RSpec(0, 0, "decal", "vfx/materials/Diablo_Decal.pkma", True, "L", props, {})])
        st = sim.SimState(0.0, {"L": sim.ParticleBatch(1, {}, {0: {"Position": np.array([[0, 0.0, 0]]),
                                                                   "Decal.DecalSize": np.array([[4.0, 2.0, 4.0]])}})})
        cam = camera.OrthoCamera(k=10.0)
        draws = fxr.prepare(e, st, cam)
        bg = np.full((96, 128, 3), 0.2, np.float32)
        seen, order = {}, []
        o_terms, o_plate, o_over, o_render = fxr.plate_shadow_terms, fr.plate_scene, fr.hdr_over_bg, R.render

        def terms(*a, **k):
            order.append("terms")
            return o_terms(*a, **k)

        def plate(bg_, rig_, gain=None):
            seen["plate"] = gain
            return o_plate(bg_, rig_, gain)

        def over(lin, bg_, rig_, gain=None):
            seen["over"] = gain
            return o_over(lin, bg_, rig_, gain)

        def render(*a, **k):
            order.append("render")
            return o_render(*a, **k)
        fxr.plate_shadow_terms, fr.plate_scene, fr.hdr_over_bg, R.render = terms, plate, over, render
        try:
            img = fxr.render(draws, cam, (128, 96), None, None, rig, (64, 72), 1, bg, floor=0.0, plate_light=Lg)
        finally:
            fr.plate_scene, fr.hdr_over_bg = o_plate, o_over
        self.assertEqual(img.shape, (96, 128, 4))
        self.assertEqual(order[:2], ["terms", "render"])
        self.assertIn("plate", seen)
        self.assertIs(seen["plate"], seen["over"])


if __name__ == "__main__":
    unittest.main()
