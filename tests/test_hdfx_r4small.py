"""hdfx round 4, lane small: free CASC slot pick (8), runs-folder detection for tree copies (9), the bench clearing
round unkillable classes (10)."""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

BUILD_INFO = "Branch!STRING:0|Version!STRING:0\nus|1.6.%s\n"


class WraithEmissive(unittest.TestCase):
    """Item 7: the game's ps_models adds albedo x (1 - ORM alpha) x EmissiveIntensity (unitlight/shader/ps_models.glsl
    lines 326 and 697); the viewer used the MaskTexture's red, which on VariantMaterial units is the KTINT mask (Wraith:
    EmissiveIntensity 50 over KTINT r -> white bones)."""

    def test_shader_reads_orm_alpha(self):
        src = (TOOLS / "hdkit" / "view" / "shaders.wgsl").read_text()
        self.assertIn("let emask = 1.0 - orm.a;", src)
        self.assertNotIn("let emask = mask4.r;", src)

    def test_bone_binding_with_default_overlay(self):
        """--overlay defaults to `auto` since lane overlays; the bone binding must test the resolved overlay, or every
        --unit effect fell back to the origin (the Wraith's wing trails under the floor after the r3 merge)."""
        src = (TOOLS / "hdkit" / "view" / "fxcli.py").read_text()
        self.assertIn("drv is None and not overlay and not a.offset", src)
        from hdkit.view import fxcli
        self.assertEqual(fxcli.build_parser().parse_args(["x.particles"]).overlay, "auto")


def _adapter():
    try:
        import wgpu
        return wgpu.gpu.request_adapter_sync(power_preference="high-performance") is not None
    except Exception:  # noqa: BLE001
        return False


@unittest.skipUnless(os.environ.get("HDFX_GPU_TESTS") == "1" and _adapter(), "GPU tests: set HDFX_GPU_TESTS=1 (gate first)")
class WraithEmissiveGpu(unittest.TestCase):
    def quad(self, orm_a, emissive):
        import numpy as np
        from hdkit.view import camera, gpu, gr2, scene
        tex = lambda px, srgb: scene.Texture("t", 0, [np.full((4, 4, 4), px, np.uint8)], srgb)   # noqa: E731
        cam = camera.OrthoCamera(k=10.0)
        r, up, w = cam.basis()
        P = np.array([-r - up, r - up, r + up, -r + up], float) * 3.0 + np.array([0.0, 5.0, 0.0])
        m = gr2.Mesh("q", P, np.tile(w, (4, 1)), np.tile(np.append(r, 1.0), (4, 1)), np.array([[0, 0], [1, 0], [1, 1], [0, 1.0]]),
                     np.array([[0, 1, 2], [0, 2, 3]]), {}, [], params={"emissive": emissive} if emissive else {})
        part = scene.Part("p", "p.model", [m], None, {"AlbedoTexture": tex((60, 60, 60, 255), True),
                                                       "ORMTexture": tex((255, 128, 0, orm_a), False),
                                                       "MaskTexture": tex((255, 255, 255, 255), False)})   # KTINT r = 1
        R = gpu.Renderer(msaa=4)
        lin = R.render(R.prepare(scene.Unit("q", [part]), cam=cam), cam, (64, 64), (32, 32), light=gpu.Light(), glow=False,
                       linear=True)
        cov = lin[..., 3] > 0.99
        self.assertGreater(int(cov.sum()), 50)
        return float(np.percentile(lin[cov][:, :3], 50))

    def test_orm_alpha_masks_emissive(self):
        plain = self.quad(255, 0.0)
        self.assertAlmostEqual(self.quad(255, 50.0), plain, delta=0.01)   # ORM a = 1: no emissive despite KTINT r = 1
        self.assertGreater(self.quad(0, 2.0), plain + 0.05)                # ORM a = 0: albedo x 2 added


if __name__ == "__main__":
    unittest.main()
