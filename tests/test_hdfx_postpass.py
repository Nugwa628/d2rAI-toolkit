"""Lane hdfx-r5-postpass: the game's screen-space AO (view.assao, ASSAO as D2R runs it) and its viewer hook."""
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

from hdkit.view import assao, gpu  # noqa: E402
from hdkit.fx import lighting  # noqa: E402

EXT = (94.28090667724609, 53.03300476074219)      # the game's ortho view at 1600 x 900 (NDCToViewMul)
CAP = Path(os.environ.get("HDFX_POSTPASS_CAPS") or "/nonexistent")   # RenderDoc pass dumps of one game frame (rd4268*/...)


def ground(h=96, w=128, z0=884.0):
    """The flat y = 0 ground of the game camera (30 degrees): depth grows up the screen, normal (0, cos, sin)."""
    y = np.arange(h)[:, None] * np.ones((1, w))
    z = z0 - (y - h / 2) * (EXT[1] / 900) / np.tan(np.radians(30))
    n = np.zeros((h, w, 3))
    n[..., 1], n[..., 2] = np.cos(np.radians(30)), np.sin(np.radians(30))
    return z, n


class TestAssao(unittest.TestCase):
    def test_rotation_matrices_are_the_captured_ones(self):
        # frame 4268, generate passes 0 and 3 (cb words 36..55)
        np.testing.assert_allclose(assao._rot(0)[:2].ravel(), [.867, 0, 0, -.867, .8379, -.2722, -.2722, -.8379],
                                   atol=1e-4)
        np.testing.assert_allclose(assao._rot(3)[4], [.6495, .894, .894, -.6495], atol=1e-4)

    def test_flat_ground_is_unoccluded(self):
        z, n = ground(192, 256)                    # (the image border clamps taps: a wall there; keep away from it)
        ao = assao.ssao(z, n, (256 * EXT[0] / 1600, 192 * EXT[1] / 900))
        self.assertGreater(ao[32:-32, 32:-32].min(), 0.995)

    def test_box_on_the_ground_darkens_its_foot(self):
        z, n = ground(256, 320)
        z[80:140, 100:220] -= 2.0                  # a 2-unit block nearer the camera, facing it
        n[80:140, 100:220] = (0.0, 0.0, 1.0)
        ao = assao.ssao(z, n, (320 * EXT[0] / 1600, 256 * EXT[1] / 900))
        self.assertLess(ao[142:150, 120:200].mean(), 0.97)      # the ground just in front of the block's base
        self.assertGreater(ao[200:216, 40:80].mean(), 0.995)    # open ground far from it

    def test_box_crop_equals_full(self):
        z, n = ground(384, 640)
        z[150:220, 280:380] -= 2.0
        n[150:220, 280:380] = (0.0, 0.0, 1.0)
        ext = (640 * EXT[0] / 1600, 384 * EXT[1] / 900)
        full = assao.ssao(z, n, ext)
        part = assao.ssao(z, n, ext, box=(300, 200, 360, 230))   # cropped by margin(): 1 outside
        self.assertTrue(np.all(part[:, :100] == 1.0))
        np.testing.assert_allclose(part[200:230, 300:360], full[200:230, 300:360], atol=1e-9)

    def test_spec_and_switch(self):
        self.assertIs(lighting.parse_spec("scene:act1_outdoors;ao=0")["ao"], False)
        self.assertNotIn("ao", lighting.parse_spec("scene:act1_outdoors"))
        prep = gpu.Prepared([1])
        for L in (gpu.Light(), gpu.Light(game={"dirs": [], "ao": False})):
            self.assertIsNone(gpu.Renderer.ssao(object(), prep, None, (8, 8), (4, 4), L))

    @unittest.skipUnless((CAP / "rd4268/19283/out_13403.bin").is_file(), "no frame 4268 extract")
    def test_port_matches_the_game_frame(self):
        W, H = 1600, 900
        z = np.fromfile(CAP / "rd4268/19283/out_13403.bin", np.uint16)[:W * H].reshape(H, W) / 65535.0 * 510 + 550
        q = np.fromfile(CAP / "rd4268c/19688/in_13351.bin", np.uint8)[:W * H * 4].reshape(H, W, 4) / 255.0
        f = q[..., :2] * 2 - 1
        nz = 1 - np.abs(f).sum(-1)
        t = np.clip(-nz, 0, 1)
        n = np.stack([f[..., 0] - np.where(f[..., 0] >= 0, t, -t), f[..., 1] - np.where(f[..., 1] >= 0, t, -t), nz], -1)
        n /= np.linalg.norm(n, axis=-1, keepdims=True)
        game = np.fromfile(CAP / "rd4268c/19688/out_13407.bin", np.uint8)[:W * H].reshape(H, W) / 255.0
        ao = assao.ssao(z, n, EXT)
        self.assertLess(abs(ao.mean() - game.mean()), 0.002)
        self.assertLess(np.abs(ao - game).mean(), 0.012)


if __name__ == "__main__":
    unittest.main()
