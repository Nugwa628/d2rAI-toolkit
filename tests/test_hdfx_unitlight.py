"""hdkit.fx.calib.unitlight + the viewer's game unit light (gpu.Light.game, shaders.wgsl game_shade). No game files."""
import os
import json
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from hdkit.fx.calib import unitlight  # noqa: E402
from hdkit.view import gpu, rig  # noqa: E402

CAPTURE = Path(os.environ.get("HDFX_UNITLIGHT_DRAWS") or "/nonexistent")


def fake_draw(d: Path):
    """bda files of a draw with one directional light, SH 1 = 3, value curve on, one point light in the grid."""
    z = lambda: np.zeros(1024, np.float32)                                   # noqa: E731
    frame, ibl, mat, view, dl = z(), z(), z(), z(), z()
    frame[0], frame[196] = 0.073125, 0.9
    ibl[0:4] = (0.4, 0.0, 1.0, 1.0)
    ibl[4:7] = 3.0
    mat[72:76] = (0.0, 0.36, 3.2, -2.56)
    mat[76:78] = (0.0, 0.4)
    mat.view(np.uint32)[79] = 1
    view[36:48] = np.eye(3, 4, dtype=np.float32).ravel()                    # view axes = world axes
    view[48:51] = (100.0, 0.0, 0.0)                                         # eye
    dl.view(np.uint32)[0] = 1
    dl[4:7] = (1.0, 0.5, 0.25)
    dl[8:12] = (1.0, 1.0, 30.0, 0.0)
    dl[12:15] = (0.0, -1.0, 0.0)                                            # travels down: light from above
    for off, a in ((40, frame), (48, ibl), (56, mat), (72, view), (112, dl)):
        a.tofile(d / ("bda_%d.bin" % off))
    np.array([1 << 6], np.uint32).tofile(d / "p_u0_1.bin")                  # one tile, one point light
    np.array([0, 0], np.uint32).tofile(d / "p_u1_2.bin")
    T = np.zeros((3, 4), np.float32)
    T[0, :3] = (-95.0, 8.0, 0.0)                                            # 5 units from the unit (at x = 0), 8 up
    T[1] = (10.0, 10.0, 10.0, 20.0)
    T[2, 0] = 5.0
    T[2, 1] = np.array([0xFFFFFFFF], np.uint32).view(np.float32)[0]
    T.tofile(d / "p_t0_3.bin")


class TestUnitLight(unittest.TestCase):
    def test_read_and_world(self):
        with tempfile.TemporaryDirectory() as t:
            fake_draw(Path(t))
            raw = unitlight.read_draw(t)
            self.assertAlmostEqual(raw["exposure"], 0.073125, 6)
            self.assertTrue(raw["value"]["on"])
            g = unitlight.to_world(raw, [0.0, 0.0, 0.0])
            self.assertEqual(g["dirs"][0]["dir"], [0.0, 1.0, 0.0])            # towards the light = - travel
            self.assertEqual(g["dirs"][0]["color"], [30.0, 15.0, 7.5])         # colour x intensity
            self.assertEqual(len(g["points"]), 1)
            np.testing.assert_allclose(g["points"][0]["pos"], [5.0, 8.0, 0.0])  # view -> world + eye - unit
            self.assertEqual((g["points"][0]["diffuse"], g["points"][0]["specular"]), (1.0, 1.0))
            r = unitlight.rig_from_draw(t, unit_pos=[0.0, 0.0, 0.0])
            p = Path(t) / "rig.json"
            r.save(p)
            r2 = rig.load(p)                                                    # the rig JSON round trip keeps the block
            self.assertEqual(r2.light.game["dirs"], r.light.game["dirs"])
            self.assertEqual(r2.name, "capture")
            self.assertAlmostEqual(r2.exposure, 0.073125, 6)

    def test_pack_layout(self):
        g = {"dirs": [{"dir": [0, 2, 0], "color": [1, 2, 3], "diffuse": 1, "specular": 0.5}], "exposure": 0.07,
             "sh": [[float(i)] * 3 for i in range(9)], "ibl": {"intensity": 0.4}, "value": {"poly": [0, 1, 2, 3],
                                                                                           "strength": 0.4, "on": True}}
        u = gpu.pack_game(g)
        self.assertEqual(len(u), 84)
        np.testing.assert_allclose(u[0:4], [1, 1, 0.07, 1], rtol=1e-6)
        np.testing.assert_allclose(u[4:8], [0, 1, 2, 3])
        np.testing.assert_allclose(u[8:11], [0.4, 0, 0.4], rtol=1e-6)
        np.testing.assert_allclose(u[16:52].reshape(9, 4)[:, 0], range(9))
        np.testing.assert_allclose(u[52:60], [0, 1, 0, 1, 1, 2, 3, 0.5])
        self.assertEqual(gpu.GAME_AT + 84, gpu.KT_AT)            # the unit variant block follows (lane r3-unitmat)
        pts = gpu.game_points({"points": [{"pos": [1, 2, 3], "radius": 21.0, "color": [1, 1, 1], "exp": 5.0}]})
        self.assertEqual(pts[0]["atten"], -5.0)                              # D2R's own falloff (point_falloff, w < 0)

    def test_off_by_default(self):
        """Rigs without the block leave the shader's game path off (the earlier outputs stay as they were)."""
        self.assertFalse(np.any(gpu.pack_game({})))
        self.assertEqual(gpu.Light().game, {})
        self.assertNotIn("game", gpu.Light().as_dict())
        self.assertEqual(rig.game().light.game, {})
        src = (TOOLS / "hdkit" / "view" / "shaders.wgsl").read_text("utf-8")
        self.assertIn("if (u.g0.x > 0.5)", src)

    def test_clut_tone(self):
        """tone 'clut': an identity LUT in PQ space gives back PQ(v x 0.008); the rig encodes through it."""
        from hdkit.view import tone
        n = 65
        g = (np.arange(n) + 0.5) / n                                         # texel centres -> PQ code
        L = np.stack(np.meshgrid(g, g, g, indexing="ij"), -1)[..., ::-1]      # [b, g, r] -> (r, g, b)
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "id.npy"
            np.save(p, L.astype(np.float32))
            v = np.array([[0.01, 0.1, 1.0], [0.5, 0.05, 0.002]])
            np.testing.assert_allclose(tone.clut(v, p), tone.pq_enc(v * tone.SCALE), atol=2e-3)
            r = rig.Rig("c", gpu.Light(), 0.5, "clut", post={"clut": str(p)})
            np.testing.assert_allclose(r.encode(v), tone.pq_enc(v * 0.5 * tone.SCALE), atol=2e-3)

    @unittest.skipUnless((CAPTURE / "bda_112.bin").exists(), "calib r3 extract absent")
    def test_capture_r3(self):
        raw = unitlight.read_draw(CAPTURE)
        self.assertEqual(len(raw["dir_lights"]), 3)
        self.assertAlmostEqual(raw["dir_lights"][1]["intensity"], 30.0, 4)        # player_key_light power 30, not x 0.4
        self.assertAlmostEqual(raw["value"]["strength"], 0.4, 6)                 # characterBrightnessFactor
        np.testing.assert_allclose(raw["value"]["poly"], [0, 0.36, 3.2, -2.56], atol=1e-5)
        p = np.polynomial.polynomial.polyval([0.25, 0.75, 1.0], raw["value"]["poly"])
        np.testing.assert_allclose(p, [0.25, 0.99, 1.0], atol=1e-5)               # the cubic through the contrast points


if __name__ == "__main__":
    unittest.main()
