"""The plate samples the visible depth-unit geometry, with the ground as fallback."""
import os
import unittest

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

from hdkit import gpugate
from hdkit.fx import render
from hdkit.view import camera, gpu


@unittest.skipUnless(os.environ.get("HDFX_GPU_TESTS") == "1" and gpugate.check().free, "requires a free GPU")
class TestPlateSurface(unittest.TestCase):
    def test_tilted_depth_unit(self):
        R = gpu.Renderer(msaa=4)
        fxr = render.FxRenderer(R, None)
        cam = camera.OrthoCamera(k=12)
        size, origin = (80, 80), (40.5, 40.5)
        # y = 1.5 - 0.75x. Shift the quad so that its centre projects onto the origin pixel.
        w = cam.basis()[2]
        centre = 3 * w
        pos = centre + np.array([[-1, 0.75, -1], [1, -0.75, -1], [1, -0.75, 1], [-1, 0.75, 1]])
        n = np.array([0.6, 0.8, 0.0])
        tris = np.array([[0, 2, 1], [0, 3, 2]])
        uv = np.zeros((4, 2))
        vb, count = R._vbuf(pos, np.tile(n, (4, 1)), np.zeros((4, 4)), uv, tris)
        ub = R._ubuf()
        bind = R._bind(ub, R.default("alb"), R.default("nrm"), R.default("orm"),
                       R.default("mask"), R.default("strip"))
        prep = gpu.Prepared([], occluders=[gpu.Draw(vb, count, ub, bind, [], 0, pos[tris], uv[tris])])

        P, N = fxr.plate_surface(prep, cam, origin, size, 0.0)
        np.testing.assert_allclose(P[40, 40], centre, atol=0.01)
        np.testing.assert_allclose(N[40, 40], n, atol=0.002)
        self.assertAlmostEqual(P[0, 0, 1], 0.0)
        np.testing.assert_array_equal(N[0, 0], [0, 1, 0])
        blocked, _ = fxr.plate_surface(prep, cam, origin, size, 3.0)
        self.assertAlmostEqual(blocked[40, 40, 1], 3.0)

        point = {"pos": centre + [1, -0.25, 0], "radius": 5, "color": [1, 1, 1]}
        np.testing.assert_array_equal(render.plate_gain(P[40, 40], [point], 1), [1, 1, 1])
        self.assertTrue(np.all(render.plate_gain(P[40, 40], [point], 1, n=N[40, 40]) > 1))
