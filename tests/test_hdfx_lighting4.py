"""Lane r4-lighting4: models get no shadowed point lights (ps_models_p cb7[80] bit 1), the water's image light, the
scene spec's ibl= input."""
import math
import unittest

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

from hdkit.fx import lighting as Lg
from hdkit.fx import render as R
from hdkit.view import gpu as G


def _setup(**kw):
    sh = np.zeros((9, 3))
    sh[0] = (1.0, 1.0, 1.0)            # constant
    sh[1] = (0.5, 0.25, 0.0)           # + y: brighter (and warmer) upwards
    return Lg.GameLighting("test", 1.0, {"iblIntensity": 2.0, "unitLightPower": 500.0}, {}, sh, **kw)


class ShadowedPoints(unittest.TestCase):
    def test_player_and_unit_light_are_shadowed_points(self):
        L = _setup(player=(0.0, 0.0, 0.0))
        self.assertTrue(L.player_light()["shadowed"])
        self.assertTrue(L.unit_light(7, (212, 212, 255))["shadowed"])
        pts = L.light(unit=(7, (212, 212, 255))).points
        self.assertEqual(sorted(p["name"] for p in pts), ["player_light", "unit_light"])
        self.assertTrue(all(p["shadowed"] for p in pts))

    def test_extra_lights_stay_plain(self):
        L = _setup(player=None, extra=[{"name": "torch", "pos": [1, 2, 3], "color": [1, 1, 1], "radius": 5, "exp": 2}])
        self.assertFalse(L.light().points[0]["shadowed"])

    def test_model_points_drop_shadowed(self):
        pts = [{"name": "a", "shadowed": True}, {"name": "b"}, {"name": "c", "shadowed": False}]
        self.assertEqual([p["name"] for p in G.model_points(pts, {"points": []})], ["b", "c"])
        self.assertEqual(len(G.model_points(pts, {"shadowed_points": True})), 3)

    def test_capture_names_mark_shadowed(self):
        g = {"points": [{"name": "shadowed_point194", "pos": [0, 10.5, 0], "radius": 21, "color": [1, 1, 1], "exp": 5},
                        {"name": "point166", "pos": [0, 7, 0], "radius": 25, "color": [1, 1, 1], "exp": 1.6}]}
        self.assertEqual([p["shadowed"] for p in G.game_points(g)], [True, False])

    def test_irradiance_keeps_shadowed_points_on_the_ground(self):
        L = _setup(player=(0.0, 0.0, 0.0), player_on_plate=True)
        P = np.array([[1.0, 0.0, 0.0]])
        with_p = L.irradiance(P)
        L.player = None
        self.assertTrue(np.all(with_p > L.irradiance(P)))


class WaterImageLight(unittest.TestCase):
    def test_sh_up_and_reflection(self):
        L = _setup(player=None)
        light = L.light()
        v = np.array([0.6124, 0.5, 0.6124])
        amb, env = R.water_image_light(light, v)
        sh = np.asarray(L.sh)
        k = 2.0 / math.pi
        from hdkit.view import ibl as I
        np.testing.assert_allclose(amb, k * np.maximum(I.irradiance(sh, (0, 1, 0)), 0), atol=1e-6)
        np.testing.assert_allclose(env, k * np.maximum(I.irradiance(sh, (-v[0], v[1], -v[2]) / np.linalg.norm(v)), 0),
                                   atol=1e-6)

    def test_no_game_light_no_override(self):
        self.assertIsNone(R.water_image_light(G.Light(), (0, 1, 0)))


class SpecIbl(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(Lg.parse_spec("scene:act1_caves;ibl=1.0")["ibl"], 1.0)
        self.assertNotIn("ibl", Lg.parse_spec("scene:act1_caves"))


if __name__ == "__main__":
    unittest.main()
