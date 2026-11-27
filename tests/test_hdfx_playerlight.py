"""Lane r5-playerlight: the player's light on the ground in inside levels (cube shadow, the light radius on the ambient)
and the player's position from plan_sbs --at. No game files needed; the GPU test runs with HDFX_GPU_TESTS=1."""
import math
import unittest

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

from hdkit.fx import lighting as Lg


def _setup(biome="act1_caves", **kw):
    sh = np.zeros((9, 3))
    sh[0] = (1.0, 1.0, 1.0)
    vis = {"iblIntensity": 2.0, "exposureValue": 4.0, "playerUnitLightPower": 2125.0}
    return Lg.GameLighting(biome, 1.0, vis, {}, sh, **kw)


class InsideRule(unittest.TestCase):
    def test_cube_only_inside(self):
        self.assertEqual(_setup(player=(7, 0, 1)).player_light().get("cube"), 0)
        self.assertNotIn("cube", _setup("act1_outdoors", player=(0, 0, 0)).player_light())
        self.assertNotIn("cube", _setup(player=(0, 0, 0), inside=False).player_light())
        pts = _setup(player=(7, 0, 1)).light().points
        self.assertEqual(pts[0].get("cube"), 0)            # reaches gpu.Light: the cube pass renders it

    def test_spec_inside(self):
        self.assertTrue(Lg.parse_spec("scene:act1_outdoors;inside=1")["inside"])
        self.assertFalse(Lg.parse_spec("scene:act1_caves;inside=0")["inside"])
        self.assertNotIn("inside", Lg.parse_spec("scene:act1_caves"))

    def test_light_radius_matches_capture(self):
        """The player's light alone (2125 power, 34.66 radius, exponent 5.5, 8 up, EV 4) against frame 4268's Global
        Attenuation surface left of the player: 1 inside ~12 units, 0.44 at 15.3, 0.145 at 20."""
        L = _setup(player=(0, 0, 0))
        g = Lg.light_radius(L.scene_points(), np.array([[h, 0.0, 0.0] for h in (5.0, 15.3, 20.0)]), L.exposure)
        self.assertAlmostEqual(g[0], 1.0, places=3)
        self.assertAlmostEqual(g[1], 0.44, delta=0.08)
        self.assertAlmostEqual(g[2], 0.145, delta=0.03)

    def test_irradiance_cube_and_radius(self):
        L = _setup(player=(0, 0, 0))
        P = np.array([[3.0, 0.0, 0.0], [20.0, 0.0, 0.0]])
        vis = np.ones((2, 4))
        lit = L.irradiance(P, visibility=vis)
        vis[0, 3] = 0.0                                     # the cube shadows the first point
        dark = L.irradiance(P, visibility=vis)
        amb = 2.0 * np.maximum(__import__("hdkit.view.ibl", fromlist=["x"]).irradiance(L.sh, (0, 1, 0)), 0.0)
        np.testing.assert_allclose(dark[0], amb, rtol=1e-6)  # ambient only (light radius 1 there)
        self.assertGreater(lit[0, 0], 3 * dark[0, 0])
        self.assertLess(lit[1, 0], 0.5 * amb[0] + 1.0)      # far out: ambient x 0.15 plus a weak player light
        out = _setup(player=(0, 0, 0), inside=False).irradiance(P[1:], visibility=np.ones((1, 4)))
        self.assertGreater(out[0, 0], amb[0])               # outdoors the ambient stays whole

    def test_fx_lights_raise_the_ambient(self):
        """Inside, the effect's own lights open the light radius: far from the player the gain is more than 1 + E/E0."""
        L = _setup(player=(0, 0, 0))
        P = np.array([[[22.0, 0.0, 0.0]]])
        fx = [{"pos": [22.0, 2.0, 0.0], "radius": 5.0, "color": [62.0 / math.pi] * 3, "atten": -4.0, "fx": True}]
        gin = L.plate_gain(P, fx)
        gout = _setup(player=(0, 0, 0), inside=False).plate_gain(P, fx)
        E0 = _setup(player=(0, 0, 0), inside=False).irradiance(P) / math.pi
        self.assertGreater(gin[0, 0, 0], gout[0, 0, 0])
        self.assertGreater(gout[0, 0, 0], 1.0)
        self.assertGreater(E0[0, 0, 0], 0.0)

    def test_shadow_terms_take_four_channels(self):
        G, s = Lg.shadow_terms(np.full((2, 4), 0.5), (2,))
        np.testing.assert_allclose(G, 0.5)
        np.testing.assert_allclose(Lg.cube_term(np.full((2, 4), 0.25), (2,)), 0.25)
        np.testing.assert_allclose(Lg.cube_term(np.full((2, 3), 0.25), (2,)), 1.0)


def _gpu_free():
    import os
    if os.environ.get("HDFX_GPU_TESTS") != "1":
        return False
    try:
        from hdkit import gpugate
        return gpugate.check().free
    except Exception:  # noqa: BLE001
        return False


@unittest.skipUnless(_gpu_free(), "GPU tests: set HDFX_GPU_TESTS=1 (and a free GPU gate)")
class TestGpu(unittest.TestCase):
    def test_ground_cube(self):
        """A box between the player's light and the ground: ground_vis channel 3 is dark behind the box, lit beside it
        and in front of it; without `cube` the channel stays 1."""
        from hdkit.view import camera, gpu as G
        R = G.Renderer(msaa=4)
        cam = camera.OrthoCamera(30, 45, 8.0)
        v = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], float) * [1.0, 2.0, 1.0] \
            + [0.0, 2.0, 0.0]
        faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
        pos, nrm, tris = [], [], []
        for q in faces:
            a, b, c, d = (v[i] for i in q)
            n = np.cross(b - a, d - a)
            o = len(pos)
            pos += [a, b, c, d]
            nrm += [n / np.linalg.norm(n)] * 4
            tris += [[o, o + 1, o + 2], [o, o + 2, o + 3]]
        pos, nrm, tris = np.array(pos), np.array(nrm), np.array(tris)
        vb, n = R._vbuf(pos, nrm, np.tile([1.0, 0, 0, 1], (len(pos), 1)), np.zeros((len(pos), 2)), tris)
        ub = R._ubuf()
        bind = R._bind(ub, R.texture([np.full((4, 4, 4), (200, 60, 60, 255), np.uint8)], srgb=True), R.default("nrm"),
                       R.default("orm"), R.default("mask"), R.default("strip"))
        box = G.Draw(vb, n, ub, bind, [], 0.0, pos[tris], np.zeros((len(tris), 3, 2)), "box", "box", "box")
        size, origin = (320, 200), (160, 100)
        out = {}
        for cube, prep in ((True, G.Prepared([box])), (False, G.Prepared([box])), ("occ", G.Prepared([], occluders=[box]))):
            pl = {"name": "player_light", "pos": (7.0, 8.0, 0.0), "radius": 34.66, "color": [53.8] * 3,
                  "atten": -5.5, "fx": True, "shadowed": True, **({"cube": 0} if cube else {})}   # "occ": a depth unit
            light = G.Light(points=(pl,), shadow={"on": True})
            enc = R.device.create_command_encoder()
            R.shadow.encode(enc, prep, light)
            R.device.queue.submit([enc.finish()])
            vis = R.shadow.ground_vis(light, cam, origin, size, 0.0)
            self.assertEqual(vis.shape, (200, 320, 4))
            px = {k: cam.to_pixels(np.array([P]), origin)[0] for k, P in
                  (("behind", (-3.0, 0.0, 0.0)), ("side", (0.0, 0.0, 6.0)), ("front", (4.0, 0.0, 0.0)))}
            out[cube] = {k: float(vis[int(y), int(x), 3]) for k, (x, y) in px.items()}
            if cube == "occ":
                from hdkit.fx.render import ground_points

                # The same pixels see a different cube direction when their receiver is above the box.
                P = ground_points(cam, origin, size, 5.0)
                surface = R.shadow.ground_vis(light, cam, origin, size, surface=(P, (0.0, 1.0, 0.0)))
                elevated = R.shadow.ground_vis(light, cam, origin, size, 5.0)
                np.testing.assert_allclose(surface, elevated, atol=1e-5)
                x, y = px["behind"].astype(int)
                self.assertGreater(surface[y, x, 3], 0.9)
        self.assertLess(out[True]["behind"], 0.2)
        self.assertGreater(out[True]["side"], 0.9)
        self.assertGreater(out[True]["front"], 0.9)
        self.assertEqual(set(out[False].values()), {1.0})
        self.assertLess(out["occ"]["behind"], 0.2)          # --depth-unit occluders cast into the cube too


if __name__ == "__main__":
    unittest.main()
