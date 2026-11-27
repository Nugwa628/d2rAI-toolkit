"""Regression tests for map object alignment, added-light calibration and model ORM alpha."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hdkit.view import camera  # noqa: E402


@unittest.skipUnless(os.environ.get("HDFX_GPU_TESTS") == "1", "GPU tests: set HDFX_GPU_TESTS=1 (gate first)")
class ModelOrmAlpha(unittest.TestCase):
    def test_preview_uses_orm_alpha_for_emissive_only(self):
        from hdkit.view import gpu, gr2, scene

        cam = camera.OrthoCamera(k=10.0)
        right, up, normal = cam.basis()
        positions = np.array([-right - up, right - up, right + up, -right + up]) * 3
        mesh = gr2.Mesh("quad", positions, np.tile(normal, (4, 1)), np.tile(np.append(right, 1), (4, 1)),
                        np.array([[0, 0], [1, 0], [1, 1], [0, 1.0]]), np.array([[0, 1, 2], [0, 2, 3]]), {}, [])
        renderer = gpu.Renderer(msaa=4)
        light = gpu.Light(floor_clip=0.0, key_dir=tuple(normal))

        def draw(alpha, emissive):
            rgba = np.full((4, 4, 4), (255, 128, 0, alpha), np.uint8)
            textures = {"ORMTexture": scene.Texture("orm", 0, [rgba], False)}
            mesh.params = {"emissive": emissive}
            unit = scene.Unit("quad", [scene.Part("quad", "quad.model", [mesh], None, textures)])
            lin = renderer.render(renderer.prepare(unit, cam=cam), cam, (64, 64), (32, 32), light=light,
                                  glow=False, linear=True)
            self.assertGreater(lin[32, 32, 3], 0.99)
            return lin[32, 32, :3]

        plain = draw(0, 0)
        np.testing.assert_allclose(plain, draw(255, 0), atol=0.001)
        np.testing.assert_allclose(plain, draw(255, 2), atol=0.001)
        self.assertTrue(np.all(draw(0, 2) > plain + 0.5))
